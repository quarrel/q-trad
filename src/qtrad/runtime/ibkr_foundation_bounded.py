"""Bounded provider-history foundation construction.

The general R1 builders intentionally retain immutable row tuples.  A full
provider-history acquisition is much larger, so Stage 8 uses this adapter to
replay the same row semantics partition-by-instrument while publishing the
same canonical child identities.  It never changes the small-fixture
application builders.
"""

from __future__ import annotations

import gc
import hashlib
import heapq
import json
import shutil
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from types import SimpleNamespace
from typing import Any

from qtrad.application.foundation import (
    _log_ratio,
    _return_disposition,
    build_asof_panel,
    build_frozen_targets,
    observation_availability_time,
)
from qtrad.application.ibkr_foundation import (
    IBKRFoundationBuild,
    _adapt_observation,
    _provider_evidence,
    evaluate_ibkr_foundation_readiness,
)
from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.application.walk_forward import build_expanding_folds
from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import (
    PANEL_DATASET_CONTRACT,
    TARGET_DATASET_CONTRACT,
    ExcursionDisposition,
    FoundationConfig,
    InstrumentRole,
    ReturnDisposition,
    TargetRow,
)
from qtrad.domain.ibkr_foundation import IBKR_CONFIRMATORY_INSTRUMENTS
from qtrad.domain.provider_history import ProviderHistoricalObservation
from qtrad.domain.research import ObservationRow


@dataclass(frozen=True, slots=True)
class _Part:
    index: int
    relative_path: str
    file_sha256: str
    row_count: int
    rows_sha256: str


class _StreamingRowsHash:
    """Hash a canonical object whose rows array is supplied incrementally."""

    def __init__(self, metadata: Mapping[str, object]) -> None:
        runtime = _runtime()
        template = runtime._json_bytes({**metadata, "rows": []})
        marker = b"[]"
        marker_index = template.find(marker)
        if marker_index < 0:
            raise AssertionError("canonical rows placeholder was not found")
        self._hasher = __import__("hashlib").sha256()
        self._hasher.update(template[:marker_index] + b"[")
        self._suffix = template[marker_index + len(marker) :]
        self._count = 0

    def add(self, payload: str) -> None:
        if self._count:
            self._hasher.update(b",")
        self._hasher.update(payload.encode("utf-8"))
        self._count += 1

    def finish(self) -> str:
        self._hasher.update(b"]")
        self._hasher.update(self._suffix)
        return self._hasher.hexdigest()


class _DeferredChildWriter:
    """Write bounded Parquet parts before the final dataset identity is known."""

    def __init__(
        self,
        *,
        child_root: Path,
        bundle_root: Path,
        child_name: str,
        kind: str,
        lineage: Mapping[str, object],
    ) -> None:
        self.child_root = child_root
        self.bundle_root = bundle_root
        self.child_name = child_name
        self.kind = kind
        self.lineage = dict(lineage)
        self.parts: list[_Part] = []
        self._payloads: list[str] = []
        self._finalized = False

    def add(self, payload: str) -> None:
        if self._finalized:
            raise RuntimeError("child writer is already finalized")
        self._payloads.append(payload)
        if len(self._payloads) >= _runtime()._MAX_CHILD_ROWS:
            self._flush()

    def close(self) -> None:
        if self._payloads or not self.parts:
            self._flush()

    def _flush(self) -> None:
        runtime = _runtime()
        index = len(self.parts)
        payloads = tuple(self._payloads)
        data = runtime._parquet_bytes(payloads)
        if not data or len(data) > runtime._MAX_CHILD_FILE_BYTES:
            raise ValueError("IBKR foundation Parquet child exceeds its byte bound")
        file_sha256 = hashlib.sha256(data).hexdigest()
        relative = (
            f"{self.child_name}/parquet/{self.kind}/"
            f"part-{index:06d}-{file_sha256[:24]}.parquet"
        )
        path = self.bundle_root / PurePosixPath(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        runtime._write_create_only(path, data)
        self.parts.append(
            _Part(
                index=index,
                relative_path=relative,
                file_sha256=file_sha256,
                row_count=len(payloads),
                rows_sha256=runtime._sha(list(payloads)),
            )
        )
        self._payloads.clear()

    def finalize(self, dataset_id: str) -> tuple[dict[str, object], ...]:
        if self._finalized:
            raise RuntimeError("child writer is already finalized")
        self.close()
        runtime = _runtime()
        references: list[dict[str, object]] = []
        for part in self.parts:
            identity = {
                "contract": runtime._FOUNDATION_CHILD_CONTRACT,
                "schema_version": runtime._FOUNDATION_CHILD_SCHEMA_VERSION,
                "kind": self.kind,
                "dataset_id": dataset_id,
                "part_index": part.index,
                "row_count": part.row_count,
                "file": part.relative_path,
                "file_sha256": part.file_sha256,
                "rows_sha256": part.rows_sha256,
                "lineage": self.lineage,
            }
            manifest_sha256 = runtime._sha(identity)
            manifest = {**identity, "manifest_sha256": manifest_sha256}
            relative_manifest = (
                f"{self.child_name}/manifests/{self.kind}/"
                f"part-{part.index:06d}-{manifest_sha256[:24]}.json"
            )
            encoded = runtime._json_bytes(manifest) + b"\\n"
            if len(encoded) > runtime._MAX_CHILD_MANIFEST_BYTES:
                raise ValueError("IBKR foundation child manifest exceeds the 4 MiB limit")
            runtime._write_create_only(
                self.bundle_root / PurePosixPath(relative_manifest), encoded
            )
            references.append(
                {
                    "kind": self.kind,
                    "dataset_id": dataset_id,
                    "manifest_id": manifest_sha256[:24],
                    "manifest_path": relative_manifest,
                    "manifest_sha256": manifest_sha256,
                    "row_count": part.row_count,
                    "file": part.relative_path,
                    "file_sha256": part.file_sha256,
                }
            )
        self._finalized = True
        return tuple(references)


def _runtime() -> Any:
    # Keep runtime imports lazy: runtime.ibkr_foundation imports this module.
    from qtrad.runtime import ibkr_foundation

    return ibkr_foundation


def _canonical_row(value: Mapping[str, object]) -> str:
    return _runtime()._canonical_row(value)


def _observation_metadata(
    configuration: Mapping[str, object],
    *,
    source_dataset_id: str,
    selection_policies: Mapping[str, object],
) -> dict[str, object]:
    return {
        "contract": ObservationRow.CONTRACT,
        "schema_version": ObservationRow.SCHEMA_VERSION,
        "configuration": configuration,
        "source_dataset_ids": [source_dataset_id],
        "selection_policies": selection_policies,
    }


def _foundation_child_lineage(
    source_evidence: ProviderHistorySourceEvidence,
    provider_manifest_sha256: str,
) -> dict[str, object]:
    return {
        "provider_manifest_sha256": provider_manifest_sha256,
        "provider_dataset_sha256": source_evidence.dataset.dataset_sha256,
        "plan_sha256": source_evidence.source_artifact.plan.plan_sha256,
        "aggregate_sha256": source_evidence.source_artifact.aggregate.aggregate_sha256,
    }


def _sorted_groups(
    rows: Sequence[ProviderHistoricalObservation],
) -> dict[str, tuple[tuple[ProviderHistoricalObservation, int], ...]]:
    grouped: dict[str, list[tuple[ProviderHistoricalObservation, int]]] = defaultdict(list)
    previous_key: tuple[str, datetime, datetime] | None = None
    for position, row in enumerate(rows, 1):
        key = (row.instrument_id, row.interval_start, row.interval_end)
        if previous_key is not None and key < previous_key:
            ordered = sorted(
                rows,
                key=lambda item: (
                    item.instrument_id,
                    item.interval_start,
                    item.interval_end,
                ),
            )
            grouped = defaultdict(list)
            for position, item in enumerate(ordered, 1):
                grouped[item.instrument_id].append((item, position))
            break
        grouped[row.instrument_id].append((row, position))
        previous_key = key
    return {instrument: tuple(values) for instrument, values in grouped.items()}


def _adapted_rows(
    grouped: Mapping[str, Sequence[tuple[ProviderHistoricalObservation, int]]],
    instrument: str,
    source_dataset_id: str,
) -> tuple[ObservationRow, ...]:
    return tuple(
        _adapt_observation(row, source_dataset_id, position)
        for row, position in grouped.get(instrument, ())
    )


def _local_observation_configuration(
    global_configuration: Mapping[str, object],
    instrument: str,
) -> dict[str, object]:
    result = dict(global_configuration)
    result["ordered_instruments"] = [instrument]
    return result


def _local_foundation_configuration(
    configuration: FoundationConfig,
    *,
    instrument: str,
    role: InstrumentRole,
    observation_dataset_id: str,
) -> FoundationConfig:
    return replace(
        configuration,
        ordered_instruments=(instrument,),
        instrument_roles={instrument: role},
        observation_dataset_id=observation_dataset_id,
    )


def _dataset_id(
    metadata: Mapping[str, object],
    rows: Iterable[Mapping[str, object]],
) -> str:
    hasher = _StreamingRowsHash(metadata)
    for row in rows:
        hasher.add(_canonical_row(row))
    return hasher.finish()


class _SummaryRows:
    def __init__(self, rows: Sequence[object], total_count: int) -> None:
        self._rows = tuple(rows)
        self._total_count = total_count

    def __iter__(self) -> Iterator[object]:
        return iter(self._rows)

    def __len__(self) -> int:
        return self._total_count


class _TargetSummary:
    __slots__ = (
        "decision_time",
        "horizon",
        "instrument_id",
        "return_disposition",
        "target_available_at",
        "target_basis",
        "target_end_time",
        "target_freeze_at",
        "target_id",
        "target_start_time",
    )

    def __init__(self, row: object) -> None:
        self.instrument_id = row.instrument_id
        self.decision_time = row.decision_time
        self.horizon = row.horizon
        self.target_basis = row.target_basis
        self.target_id = row.target_id
        self.target_start_time = row.target_start_time
        self.target_end_time = row.target_end_time
        self.target_freeze_at = row.target_freeze_at
        self.target_available_at = row.target_available_at
        self.return_disposition = row.return_disposition


def _fast_target_rows(
    rows: Sequence[ObservationRow],
    config: FoundationConfig,
    *,
    horizons: Sequence[timedelta],
) -> tuple[TargetRow, ...] | None:
    """Build singleton historical targets with rolling path extrema.

    A source with revisions must use the general builder.  The Stage 7
    provider source has one immutable MID observation per minute, allowing
    exact endpoint lookups and linear-time rolling path calculations.
    """
    target_instruments = tuple(
        instrument_id
        for instrument_id in config.ordered_instruments
        if InstrumentRole(config.instrument_roles[instrument_id]) is InstrumentRole.TARGET
    )
    if len(target_instruments) != 1:
        return None
    if not horizons:
        return ()

    target_instrument = target_instruments[0]
    rows_by_time: dict[datetime, ObservationRow] = {}
    for row in rows:
        if row.instrument_id != target_instrument or row.basis is not config.target_basis:
            continue
        if row.interval_end in rows_by_time:
            return None
        rows_by_time[row.interval_end] = row

    decision_times: list[datetime] = []
    current = config.range_start
    while current < config.range_end:
        decision_times.append(current)
        current += config.grid_resolution
    if not decision_times:
        return ()
    resolution = config.grid_resolution.total_seconds()
    if resolution <= 0 or not resolution.is_integer():
        return None
    resolution_steps = int(resolution)

    horizon_steps: list[int] = []
    for horizon in horizons:
        seconds = horizon.total_seconds()
        if seconds <= 0 or not seconds.is_integer() or int(seconds) % resolution_steps:
            return None
        horizon_steps.append(int(seconds) // resolution_steps)

    maximum_end = decision_times[-1] + max(horizons)
    total_steps = int((maximum_end - config.range_start).total_seconds()) // resolution_steps
    values = [
        rows_by_time.get(config.range_start + index * config.grid_resolution)
        for index in range(total_steps + 1)
    ]
    missing_prefix = [0]
    bad_high_prefix = [0]
    bad_low_prefix = [0]
    for row in values:
        missing_prefix.append(missing_prefix[-1] + (row is None))
        bad_high_prefix.append(
            bad_high_prefix[-1]
            + (row is not None and (row.high <= 0 or not row.high.is_finite()))
        )
        bad_low_prefix.append(
            bad_low_prefix[-1]
            + (row is not None and (row.low <= 0 or not row.low.is_finite()))
        )

    metric_sets: list[
        tuple[
            list[object | None],
            list[object | None],
            list[datetime | None],
            list[bool],
            list[bool],
        ]
    ] = []
    for steps in horizon_steps:
        max_high: deque[tuple[int, object]] = deque()
        min_low: deque[tuple[int, object]] = deque()
        max_available: deque[tuple[int, datetime]] = deque()
        highs: list[object | None] = [None] * len(decision_times)
        lows: list[object | None] = [None] * len(decision_times)
        availabilities: list[datetime | None] = [None] * len(decision_times)
        missing: list[bool] = [False] * len(decision_times)
        bad: list[bool] = [False] * len(decision_times)
        for right in range(1, len(values)):
            row = values[right]
            if row is not None:
                while max_high and max_high[-1][1] <= row.high:
                    max_high.pop()
                max_high.append((right, row.high))
                while min_low and min_low[-1][1] >= row.low:
                    min_low.pop()
                min_low.append((right, row.low))
                available_at = observation_availability_time(row, config.availability_basis)
                while max_available and max_available[-1][1] <= available_at:
                    max_available.pop()
                max_available.append((right, available_at))
            left = right - steps + 1
            while max_high and max_high[0][0] < left:
                max_high.popleft()
            while min_low and min_low[0][0] < left:
                min_low.popleft()
            while max_available and max_available[0][0] < left:
                max_available.popleft()
            decision_index = right - steps
            if 0 <= decision_index < len(decision_times):
                highs[decision_index] = max_high[-1][1] if max_high else None
                lows[decision_index] = min_low[-1][1] if min_low else None
                availabilities[decision_index] = (
                    max_available[-1][1] if max_available else None
                )
                missing[decision_index] = (
                    missing_prefix[right + 1] - missing_prefix[decision_index + 1] != 0
                )
                bad[decision_index] = bool(
                    bad_high_prefix[right + 1] - bad_high_prefix[decision_index + 1]
                    or bad_low_prefix[right + 1] - bad_low_prefix[decision_index + 1]
                )
        metric_sets.append((highs, lows, availabilities, missing, bad))

    result: list[TargetRow] = []
    for decision_index, decision_time in enumerate(decision_times):
        start_row = values[decision_index]
        for horizon_index, (horizon, steps) in enumerate(
            zip(horizons, horizon_steps, strict=True)
        ):
            target_end = decision_time + horizon
            freeze_at = target_end + config.target_revision_delay

            start_state = (
                "MISSING"
                if start_row is None
                else (
                    "OBSERVED"
                    if observation_availability_time(start_row, config.availability_basis)
                    <= freeze_at
                    else "UNAVAILABLE"
                )
            )
            end_row = values[decision_index + steps]
            end_state = (
                "MISSING"
                if end_row is None
                else (
                    "OBSERVED"
                    if observation_availability_time(end_row, config.availability_basis)
                    <= freeze_at
                    else "UNAVAILABLE"
                )
            )

            return_disposition = _return_disposition(
                start_row,
                start_state,
                end_row,
                end_state,
            )
            label_start = start_row.close if start_row else None
            label_end = end_row.close if end_row else None
            log_return = (
                _log_ratio(label_start, label_end)
                if return_disposition is ReturnDisposition.VALID
                else None
            )
            highs, lows, max_available, path_missing, path_bad = metric_sets[horizon_index]
            if return_disposition in {
                ReturnDisposition.MISSING_START,
                ReturnDisposition.UNAVAILABLE_BY_FREEZE,
            }:
                upper = lower = None
                excursion_disposition = ExcursionDisposition.MISSING_START
            elif return_disposition is ReturnDisposition.MISSING_END:
                upper = lower = None
                excursion_disposition = ExcursionDisposition.MISSING_END
            elif return_disposition is ReturnDisposition.AMBIGUOUS_SOURCE:
                upper = lower = None
                excursion_disposition = ExcursionDisposition.AMBIGUOUS_SOURCE
            elif (
                start_row is None
                or end_row is None
                or start_row.close <= 0
                or not start_row.close.is_finite()
                or path_missing[decision_index]
                or path_bad[decision_index]
                or (
                    max_available[decision_index] is not None
                    and max_available[decision_index] > freeze_at
                )
                or highs[decision_index] is None
                or lows[decision_index] is None
            ):
                upper = lower = None
                excursion_disposition = ExcursionDisposition.INCOMPLETE_PATH
            else:
                try:
                    upper = _log_ratio(highs[decision_index], start_row.close)
                    lower = _log_ratio(lows[decision_index], start_row.close)
                    excursion_disposition = ExcursionDisposition.VALID
                except (ValueError, OverflowError):
                    upper = lower = None
                    excursion_disposition = ExcursionDisposition.INCOMPLETE_PATH

            result.append(
                TargetRow(
                    instrument_id=target_instrument,
                    decision_time=decision_time,
                    horizon=horizon,
                    target_basis=config.target_basis,
                    target_revision_policy=config.target_revision_policy,
                    target_start_time=decision_time,
                    target_end_time=target_end,
                    target_freeze_at=freeze_at,
                    target_available_at=freeze_at,
                    label_start_close=label_start,
                    label_end_close=label_end,
                    log_return=log_return,
                    return_disposition=return_disposition,
                    start_event_id=start_row.event_id if start_row else None,
                    end_event_id=end_row.event_id if end_row else None,
                    upper_log_excursion=upper,
                    lower_log_excursion=lower,
                    excursion_disposition=excursion_disposition,
                )
            )
    return tuple(result)


def _panel_temp_key(payload: str) -> tuple[str, str, str]:
    row = json.loads(payload)
    return (str(row["decision_time"]), str(row["instrument_id"]), str(row["basis"]))


def _merge_panel_files(
    files: Sequence[Path],
    *,
    target_writer: _DeferredChildWriter,
    target_hasher: _StreamingRowsHash,
) -> None:
    handles = [path.open("r", encoding="utf-8") for path in files]
    heap: list[tuple[tuple[str, str, str], int, str]] = []
    try:
        for index, handle in enumerate(handles):
            line = handle.readline()
            if line:
                payload = line.rstrip("\n")
                heapq.heappush(heap, (_panel_temp_key(payload), index, payload))
        while heap:
            _, index, payload = heapq.heappop(heap)
            target_hasher.add(payload)
            target_writer.add(payload)
            line = handles[index].readline()
            if line:
                next_payload = line.rstrip("\n")
                heapq.heappush(heap, (_panel_temp_key(next_payload), index, next_payload))
    finally:
        for handle in handles:
            handle.close()


def _make_summary_target_dataset(
    rows: Sequence[object],
    *,
    target_dataset_id: str,
    observation_dataset_id: str,
    foundation_configuration_id: str,
    total_count: int,
) -> Any:
    return SimpleNamespace(
        rows=_SummaryRows(rows, total_count),
        dataset_id=target_dataset_id,
        observation_dataset_id=observation_dataset_id,
        foundation_configuration_id=foundation_configuration_id,
    )


def _empty_fold_dataset(
    *,
    target_dataset_id: str,
    foundation_configuration_id: str,
) -> FoldDataset:
    return FoldDataset.create(
        (),
        target_dataset_id=target_dataset_id,
        foundation_configuration_id=foundation_configuration_id,
    )


def build_bounded_provider_foundation(
    *,
    source_evidence: ProviderHistorySourceEvidence,
    configuration: FoundationConfig,
    child_root: Path,
    bundle_root: Path,
    child_name: str,
    provider_manifest_sha256: str,
) -> tuple[IBKRFoundationBuild, dict[str, tuple[dict[str, object], ...]]]:
    """Build and publish a large provider-history foundation with bounded memory."""

    provider_rows = source_evidence.observations
    if not provider_rows:
        raise ValueError("provider-history source has no observations")

    grouped = _sorted_groups(provider_rows)
    observed_instruments = set(grouped)
    candidate_names = {str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS}
    ordered_instruments = tuple(
        sorted(observed_instruments | set(configuration.ordered_instruments) | candidate_names)
    )
    roles = {
        instrument: (
            InstrumentRole.TARGET if instrument in candidate_names else InstrumentRole.CONTEXT
        )
        for instrument in ordered_instruments
    }

    source_start = min(row.interval_start for row in provider_rows)
    source_end = max(row.interval_end for row in provider_rows)
    observation_configuration = {
        "contract": "qtrad-ibkr-historical-observation-adapter-v1",
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "provider": "ibkr",
        "environment": "paper",
        "ordered_instruments": list(ordered_instruments),
        "interval_start": configuration.required_observation_start.isoformat(),
        "interval_end": configuration.required_observation_end.isoformat(),
        "observed_interval_start": source_start.isoformat(),
        "observed_interval_end": source_end.isoformat(),
        "grid_resolution_seconds": int(configuration.grid_resolution.total_seconds()),
        "availability_basis": configuration.availability_basis.value,
        "source_dataset_id": source_evidence.dataset.dataset_sha256,
    }
    selection_policies = {
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "availability_policy": source_evidence.dataset.availability_policy.as_json_value(),
        "correction_policy": "FROZEN_FIRST_SUCCESSFUL_RESPONSE_NO_REFETCH_MERGE",
    }
    global_observation_metadata = _observation_metadata(
        observation_configuration,
        source_dataset_id=source_evidence.dataset.dataset_sha256,
        selection_policies=selection_policies,
    )
    adapted_configuration = replace(
        configuration,
        ordered_instruments=ordered_instruments,
        instrument_roles=roles,
        observation_dataset_id="0" * 64,
    )
    active_intervals, provider_gaps = _provider_evidence(source_evidence)
    lineage = _foundation_child_lineage(source_evidence, provider_manifest_sha256)
    if not child_root.exists():
        child_root.mkdir(parents=True, exist_ok=False)

    observation_writer = _DeferredChildWriter(
        child_root=child_root,
        bundle_root=bundle_root,
        child_name=child_name,
        kind="observations",
        lineage=lineage,
    )
    observation_hasher = _StreamingRowsHash(global_observation_metadata)
    for instrument in ordered_instruments:
        for row in _adapted_rows(grouped, instrument, source_evidence.dataset.dataset_sha256):
            payload = _canonical_row(row.as_json())
            observation_hasher.add(payload)
            observation_writer.add(payload)
    observation_dataset_id = observation_hasher.finish()
    observation_refs = observation_writer.finalize(observation_dataset_id)
    adapted_configuration = replace(
        adapted_configuration,
        observation_dataset_id=observation_dataset_id,
    )

    panel_metadata = {
        "contract": PANEL_DATASET_CONTRACT,
        "schema_version": 1,
        "observation_dataset_id": observation_dataset_id,
        "foundation_configuration_id": adapted_configuration.configuration_id,
    }
    target_metadata = {
        "contract": TARGET_DATASET_CONTRACT,
        "schema_version": 1,
        "observation_dataset_id": observation_dataset_id,
        "foundation_configuration_id": adapted_configuration.configuration_id,
    }
    panel_hasher = _StreamingRowsHash(panel_metadata)
    target_hasher = _StreamingRowsHash(target_metadata)
    panel_writer = _DeferredChildWriter(
        child_root=child_root,
        bundle_root=bundle_root,
        child_name=child_name,
        kind="panel",
        lineage=lineage,
    )
    target_writer = _DeferredChildWriter(
        child_root=child_root,
        bundle_root=bundle_root,
        child_name=child_name,
        kind="targets",
        lineage=lineage,
    )

    panel_tmp = child_root / ".panel-temp"
    panel_tmp.mkdir()
    panel_files: list[Path] = []
    primary_summaries: list[_TargetSummary] = []
    target_row_count = 0
    try:
        for instrument_index, instrument in enumerate(ordered_instruments):
            role = roles[instrument]
            rows = _adapted_rows(grouped, instrument, source_evidence.dataset.dataset_sha256)
            local_observation_configuration = _local_observation_configuration(
                observation_configuration, instrument
            )
            local_observation_id = _dataset_id(
                _observation_metadata(
                    local_observation_configuration,
                    source_dataset_id=source_evidence.dataset.dataset_sha256,
                    selection_policies=selection_policies,
                ),
                (row.as_json() for row in rows),
            )
            local_configuration = _local_foundation_configuration(
                adapted_configuration,
                instrument=instrument,
                role=role,
                observation_dataset_id=local_observation_id,
            )
            local_dataset = SimpleNamespace(
                rows=rows,
                configuration=local_observation_configuration,
                dataset_id=local_observation_id,
            )
            panel = build_asof_panel(
                local_dataset,
                local_configuration,
                source_active_intervals=active_intervals,
            )
            panel_file = panel_tmp / f"{instrument_index:05d}.jsonl"
            with panel_file.open("w", encoding="utf-8") as stream:
                for row in panel.rows:
                    stream.write(_canonical_row(row.as_json()))
                    stream.write("\n")
            panel_files.append(panel_file)
            if role is InstrumentRole.TARGET:
                fast_rows = _fast_target_rows(
                    rows,
                    local_configuration,
                    horizons=adapted_configuration.target_horizons,
                )
                if fast_rows is None:
                    targets = build_frozen_targets(
                        local_dataset,
                        local_configuration,
                        horizons=adapted_configuration.target_horizons,
                    )
                    target_rows = targets.rows
                else:
                    target_rows = fast_rows
                target_row_count += len(target_rows)
                primary_horizon = adapted_configuration.primary_vertical_horizon
                for row in target_rows:
                    payload = _canonical_row(row.as_json())
                    target_hasher.add(payload)
                    target_writer.add(payload)
                    if row.horizon == primary_horizon:
                        primary_summaries.append(_TargetSummary(row))
            del panel
            if role is InstrumentRole.TARGET and fast_rows is None:
                del targets
            del local_dataset, rows
            gc.collect()

        _merge_panel_files(
            panel_files,
            target_writer=panel_writer,
            target_hasher=panel_hasher,
        )
        panel_dataset_id = panel_hasher.finish()
        target_dataset_id = target_hasher.finish()
        panel_refs = panel_writer.finalize(panel_dataset_id)
        target_refs = target_writer.finalize(target_dataset_id)

        summary_rows = tuple(primary_summaries)
        summary_dataset = _make_summary_target_dataset(
            summary_rows,
            target_dataset_id=target_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
            total_count=target_row_count,
        )
        try:
            folds = build_expanding_folds(
                summary_dataset,
                adapted_configuration,
            )
        except ValueError as error:
            if str(error) != "no scientifically valid expanding folds are available":
                raise
            folds = _empty_fold_dataset(
                target_dataset_id=target_dataset_id,
                foundation_configuration_id=adapted_configuration.configuration_id,
            )
        fold_writer = _DeferredChildWriter(
            child_root=child_root,
            bundle_root=bundle_root,
            child_name=child_name,
            kind="folds",
            lineage=lineage,
        )
        for fold in folds.folds:
            payload = _canonical_row(fold.as_json())
            fold_writer.add(payload)
        fold_refs = fold_writer.finalize(folds.dataset_id)
    finally:
        shutil.rmtree(panel_tmp, ignore_errors=True)

    observations_ref = SimpleNamespace(
        rows=(),
        dataset_id=observation_dataset_id,
        configuration=observation_configuration,
        source_dataset_ids=(source_evidence.dataset.dataset_sha256,),
        selection_policies=selection_policies,
    )
    panel_ref = SimpleNamespace(
        rows=(),
        dataset_id=panel_dataset_id,
        observation_dataset_id=observation_dataset_id,
        foundation_configuration_id=adapted_configuration.configuration_id,
    )
    target_ref = SimpleNamespace(
        rows=(),
        dataset_id=target_dataset_id,
        observation_dataset_id=observation_dataset_id,
        foundation_configuration_id=adapted_configuration.configuration_id,
    )
    build = IBKRFoundationBuild(
        configuration=adapted_configuration,
        observations=observations_ref,
        panel=panel_ref,
        targets=target_ref,
        folds=folds,
        provider_history=source_evidence.dataset,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        readiness=evaluate_ibkr_foundation_readiness(
            source_evidence,
            summary_dataset,
            source_start=source_start,
            source_end=source_end,
            active_intervals=active_intervals,
            provider_gaps=provider_gaps,
            primary_horizon=adapted_configuration.primary_vertical_horizon,
            fold_count=len(folds.folds),
        ),
    )
    return build, {
        "observations": tuple(observation_refs),
        "panel": tuple(panel_refs),
        "targets": tuple(target_refs),
        "folds": tuple(fold_refs),
    }


def verify_bounded_provider_foundation(
    *,
    source_evidence: ProviderHistorySourceEvidence,
    configuration: FoundationConfig,
    bundle_path: Path,
    document: Mapping[str, object],
    payload: Mapping[str, object],
) -> IBKRFoundationBuild:
    runtime = _runtime()
    child_payload = payload.get("children")
    if not isinstance(child_payload, Mapping):
        raise ValueError("foundation children payload is invalid")
    provider_value = document.get("provider_history_manifest")
    if not isinstance(provider_value, str):
        raise ValueError("foundation provider manifest path is invalid")
    provider_path = bundle_path.parent / provider_value
    root_name: str | None = None
    for raw_parts in child_payload.values():
        if isinstance(raw_parts, Sequence) and raw_parts:
            first = raw_parts[0]
            if isinstance(first, Mapping) and isinstance(first.get("manifest_path"), str):
                root_name = PurePosixPath(str(first["manifest_path"])).parts[0]
                break
    if root_name is None:
        raise ValueError("foundation child root is missing")
    actual_child_root = bundle_path.parent / root_name
    temp_parent = Path(
        mkdtemp(
            prefix=f".{actual_child_root.name}.verify-",
            dir=str(bundle_path.parent),
        )
    )
    temp_child = temp_parent / actual_child_root.name
    try:
        replay, generated = build_bounded_provider_foundation(
            source_evidence=source_evidence,
            configuration=configuration,
            child_root=temp_child,
            bundle_root=temp_parent,
            child_name=actual_child_root.name,
            provider_manifest_sha256=hashlib.sha256(provider_path.read_bytes()).hexdigest(),
        )
        expected_document = runtime._manifest_payload(
            replay,
            source_evidence,
            generated,
            provider_path,
            bundle_path.parent,
        )
        expected_payload = expected_document["payload"]
        if runtime._json_bytes(expected_payload) != runtime._json_bytes(payload):
            raise ValueError("bounded foundation payload identity does not match replay")
        _compare_generated_children(
            actual_child_root=actual_child_root,
            generated_root=temp_child,
            expected=generated,
            actual_payload=child_payload,
        )
        return replay
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)


def _compare_generated_children(
    *,
    actual_child_root: Path,
    generated_root: Path,
    expected: Mapping[str, Sequence[Mapping[str, object]]],
    actual_payload: Mapping[str, object],
) -> None:
    expected_paths: set[str] = set()
    actual_paths: set[str] = set()
    for kind, generated_parts in expected.items():
        actual_parts = actual_payload.get(kind)
        if not isinstance(actual_parts, Sequence):
            raise ValueError(f"child references for {kind} are invalid")
        if tuple(actual_parts) != tuple(generated_parts):
            raise ValueError(f"child references for {kind} diverge from bounded replay")
        for part in generated_parts:
            for key in ("manifest_path", "file"):
                relative = part.get(key)
                if not isinstance(relative, str):
                    raise ValueError(f"child {kind} reference path is invalid")
                expected_paths.add(relative)
                actual_path = actual_child_root.parent / relative
                generated_path = generated_root.parent / relative
                if not actual_path.is_file() or not generated_path.is_file():
                    raise ValueError(f"child {kind} part is missing")
                if actual_path.read_bytes() != generated_path.read_bytes():
                    raise ValueError(f"child {kind} part bytes diverge from replay")
    for path in actual_child_root.rglob("*"):
        if path.is_file():
            actual_paths.add(path.relative_to(actual_child_root.parent).as_posix())
    if actual_paths != expected_paths:
        raise ValueError("foundation child root contains an unexpected or missing file")
