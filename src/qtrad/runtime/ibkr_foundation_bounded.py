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
import inspect
import json
import resource
import shutil
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from types import SimpleNamespace
from typing import Any, cast

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
from qtrad.application.provider_history import (
    ProviderHistoryObservationRows,
    ProviderHistorySourceEvidence,
)
from qtrad.application.walk_forward import build_expanding_folds
from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import (
    PANEL_DATASET_CONTRACT,
    TARGET_DATASET_CONTRACT,
    ExcursionDisposition,
    FoundationConfig,
    InstrumentRole,
    PanelDataset,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.ibkr_foundation import IBKR_CONFIRMATORY_INSTRUMENTS
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.provider_history import ProviderHistoricalObservation
from qtrad.domain.research import ObservationDataset, ObservationRow

_CHECKPOINT_CONTRACT = "qtrad-stage8-foundation-checkpoint-v1"
_ProgressCallback = Callable[[Mapping[str, object]], None]


@dataclass(frozen=True, slots=True)
class _Part:
    index: int
    relative_path: str
    file_sha256: str
    row_count: int
    rows_sha256: str


@dataclass(frozen=True, slots=True)
class _CheckpointFile:
    path: Path
    row_count: int
    bytes_sha256: str


@dataclass(frozen=True, slots=True)
class _ObservationCheckpoint:
    rows: _CheckpointFile
    source_start: datetime | None
    source_end: datetime | None


@dataclass(frozen=True, slots=True)
class _DerivedCheckpoint:
    panel: _CheckpointFile
    targets: _CheckpointFile
    summaries: _CheckpointFile


class _Progress:
    def __init__(self, callback: _ProgressCallback | None) -> None:
        self._callback = callback
        self._started = time.monotonic()

    def emit(self, phase: str, event: str, **fields: object) -> None:
        if self._callback is None:
            return
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self._callback(
            {
                "contract": "qtrad-stage8-progress-v1",
                "phase": phase,
                "event": event,
                "elapsed_seconds": round(time.monotonic() - self._started, 3),
                "maximum_rss_kib": int(usage.ru_maxrss),
                **fields,
            }
        )


class _Stage8Checkpoint:
    """Disposable, identity-bound canonical per-instrument staging."""

    def __init__(
        self,
        root: Path | None,
        *,
        provider_manifest_sha256: str,
        provider_dataset_sha256: str,
        configuration_id: str,
    ) -> None:
        self.root = root
        if root is None:
            return
        identity = {
            "contract": _CHECKPOINT_CONTRACT,
            "provider_manifest_sha256": provider_manifest_sha256,
            "provider_dataset_sha256": provider_dataset_sha256,
            "configuration_id": configuration_id,
            "implementation_sha256": _implementation_sha256(),
        }
        encoded = _runtime()._json_bytes(identity) + b"\n"
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise ValueError("Stage 8 checkpoint root must be a real directory")
            identity_path = root / "identity.json"
            if not identity_path.is_file() or identity_path.is_symlink():
                raise ValueError("Stage 8 checkpoint identity is missing")
            if identity_path.read_bytes() != encoded:
                raise ValueError("Stage 8 checkpoint identity does not match this build")
        else:
            root.mkdir(parents=True, exist_ok=False)
            _runtime()._write_create_only(root / "identity.json", encoded)

    def observation(self, instrument: str) -> _ObservationCheckpoint | None:
        directory = self._instrument_directory("observations", instrument)
        if directory is None or not directory.exists():
            return None
        metadata = self._metadata(directory, instrument, "observations")
        rows = self._file(directory, metadata, "rows")
        start_value = metadata["source_start"]
        end_value = metadata["source_end"]
        source_start = datetime.fromisoformat(str(start_value)) if start_value else None
        source_end = datetime.fromisoformat(str(end_value)) if end_value else None
        if rows.row_count > 0 and (source_start is None or source_end is None):
            raise ValueError("Stage 8 observation checkpoint bounds are missing")
        if rows.row_count == 0 and (source_start is not None or source_end is not None):
            raise ValueError("Stage 8 empty observation checkpoint has bounds")
        if source_start is not None and source_end is not None and source_end <= source_start:
            raise ValueError("Stage 8 observation checkpoint bounds are invalid")
        return _ObservationCheckpoint(rows=rows, source_start=source_start, source_end=source_end)

    def store_observation(
        self,
        instrument: str,
        source: Path,
        *,
        source_start: datetime | None,
        source_end: datetime | None,
    ) -> _ObservationCheckpoint:
        if self.root is None:
            details = _checkpoint_file(source)
            return _ObservationCheckpoint(details, source_start, source_end)
        final = self._instrument_directory("observations", instrument)
        assert final is not None
        if final.exists():
            loaded = self.observation(instrument)
            if loaded is None:
                raise ValueError("Stage 8 observation checkpoint is incomplete")
            return loaded
        temporary = Path(mkdtemp(prefix=".observation-", dir=str(final.parent)))
        try:
            rows_path = temporary / "rows.jsonl"
            shutil.copyfile(source, rows_path)
            details = _checkpoint_file(rows_path)
            metadata = {
                "contract": _CHECKPOINT_CONTRACT,
                "kind": "observations",
                "instrument_id": instrument,
                "source_start": source_start.isoformat() if source_start else None,
                "source_end": source_end.isoformat() if source_end else None,
                "rows": _checkpoint_file_payload(details),
            }
            _runtime()._write_create_only(
                temporary / "complete.json", _runtime()._json_bytes(metadata) + b"\n"
            )
            temporary.rename(final)
            return _ObservationCheckpoint(
                rows=replace(details, path=final / "rows.jsonl"),
                source_start=source_start,
                source_end=source_end,
            )
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def derived(
        self,
        instrument: str,
        *,
        foundation_configuration_id: str,
    ) -> _DerivedCheckpoint | None:
        directory = self._instrument_directory(f"derived-{foundation_configuration_id}", instrument)
        if directory is None or not directory.exists():
            return None
        metadata = self._metadata(directory, instrument, "derived")
        if metadata["foundation_configuration_id"] != foundation_configuration_id:
            raise ValueError("Stage 8 derived checkpoint configuration changed")
        return _DerivedCheckpoint(
            panel=self._file(directory, metadata, "panel"),
            targets=self._file(directory, metadata, "targets"),
            summaries=self._file(directory, metadata, "summaries"),
        )

    def store_derived(
        self,
        instrument: str,
        *,
        foundation_configuration_id: str,
        panel: Path,
        targets: Path,
        summaries: Path,
    ) -> _DerivedCheckpoint:
        if self.root is None:
            return _DerivedCheckpoint(
                panel=_checkpoint_file(panel),
                targets=_checkpoint_file(targets),
                summaries=_checkpoint_file(summaries),
            )
        final = self._instrument_directory(f"derived-{foundation_configuration_id}", instrument)
        assert final is not None
        if final.exists():
            loaded = self.derived(
                instrument, foundation_configuration_id=foundation_configuration_id
            )
            if loaded is None:
                raise ValueError("Stage 8 derived checkpoint is incomplete")
            return loaded
        temporary = Path(mkdtemp(prefix=".derived-", dir=str(final.parent)))
        try:
            copied: dict[str, _CheckpointFile] = {}
            for name, source in (
                ("panel", panel),
                ("targets", targets),
                ("summaries", summaries),
            ):
                target = temporary / f"{name}.jsonl"
                shutil.copyfile(source, target)
                copied[name] = _checkpoint_file(target)
            metadata = {
                "contract": _CHECKPOINT_CONTRACT,
                "kind": "derived",
                "instrument_id": instrument,
                "foundation_configuration_id": foundation_configuration_id,
                **{name: _checkpoint_file_payload(details) for name, details in copied.items()},
            }
            _runtime()._write_create_only(
                temporary / "complete.json", _runtime()._json_bytes(metadata) + b"\n"
            )
            temporary.rename(final)
            return _DerivedCheckpoint(
                panel=replace(copied["panel"], path=final / "panel.jsonl"),
                targets=replace(copied["targets"], path=final / "targets.jsonl"),
                summaries=replace(copied["summaries"], path=final / "summaries.jsonl"),
            )
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _instrument_directory(self, kind: str, instrument: str) -> Path | None:
        if self.root is None:
            return None
        parent = self.root / kind
        parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(instrument.encode("utf-8")).hexdigest()[:24]
        return parent / f"instrument-{digest}"

    @staticmethod
    def _metadata(directory: Path, instrument: str, kind: str) -> dict[str, object]:
        path = directory / "complete.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Stage 8 {kind} checkpoint is incomplete")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Stage 8 {kind} checkpoint metadata is invalid")
        if (
            value.get("contract") != _CHECKPOINT_CONTRACT
            or value.get("kind") != kind
            or value.get("instrument_id") != instrument
        ):
            raise ValueError(f"Stage 8 {kind} checkpoint identity is invalid")
        return value

    @staticmethod
    def _file(
        directory: Path,
        metadata: Mapping[str, object],
        name: str,
    ) -> _CheckpointFile:
        value = metadata.get(name)
        if not isinstance(value, Mapping):
            raise ValueError(f"Stage 8 checkpoint {name} metadata is invalid")
        path = directory / f"{name}.jsonl"
        details = _checkpoint_file(path)
        if _checkpoint_file_payload(details) != dict(value):
            raise ValueError(f"Stage 8 checkpoint {name} content changed")
        return details


def _implementation_sha256() -> str:
    hasher = hashlib.sha256()
    hasher.update(Path(__file__).read_bytes())
    for function in (
        _adapt_observation,
        _provider_evidence,
        build_asof_panel,
        build_frozen_targets,
        build_expanding_folds,
    ):
        hasher.update(inspect.getsource(function).encode("utf-8"))
    return hasher.hexdigest()


def _checkpoint_file(path: Path) -> _CheckpointFile:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Stage 8 checkpoint file is missing")
    hasher = hashlib.sha256()
    row_count = 0
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n") or line == b"\n":
                raise ValueError("Stage 8 checkpoint JSONL is not canonical")
            hasher.update(line)
            row_count += 1
    return _CheckpointFile(path=path, row_count=row_count, bytes_sha256=hasher.hexdigest())


def _checkpoint_file_payload(details: _CheckpointFile) -> dict[str, object]:
    return {
        "row_count": details.row_count,
        "bytes_sha256": details.bytes_sha256,
    }


def _checkpoint_payloads(details: _CheckpointFile) -> Iterator[str]:
    with details.path.open("r", encoding="utf-8") as stream:
        for line in stream:
            payload = line.rstrip("\n")
            if payload:
                yield payload


def _dataset_id_from_checkpoint(
    metadata: Mapping[str, object],
    details: _CheckpointFile,
) -> str:
    hasher = _StreamingRowsHash(metadata)
    for payload in _checkpoint_payloads(details):
        hasher.add(payload)
    return hasher.finish()


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
            f"{self.child_name}/parquet/{self.kind}/part-{index:06d}-{file_sha256[:24]}.parquet"
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
            runtime._write_create_only(self.bundle_root / PurePosixPath(relative_manifest), encoded)
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
    rows: Iterable[ProviderHistoricalObservation],
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


def _provider_instruments(
    rows: ProviderHistoryObservationRows,
) -> tuple[str, ...]:
    instruments = getattr(rows, "instruments", None)
    if isinstance(instruments, tuple) and all(
        isinstance(instrument, str) for instrument in instruments
    ):
        return instruments
    return tuple(sorted({row.instrument_id for row in rows}))


def _adapted_instrument_rows(
    rows: ProviderHistoryObservationRows,
    grouped: Mapping[str, Sequence[tuple[ProviderHistoricalObservation, int]]] | None,
    instrument: str,
    source_dataset_id: str,
) -> tuple[ObservationRow, ...]:
    positioned = cast(
        Callable[[str], Iterator[tuple[ProviderHistoricalObservation, int]]] | None,
        getattr(rows, "iter_instrument_with_positions", None),
    )
    if positioned is not None:
        return tuple(
            _adapt_observation(row, source_dataset_id, position)
            for row, position in positioned(instrument)
        )
    if grouped is None:
        raise AssertionError("provider-history rows require a grouped fallback")
    return _adapted_rows(grouped, instrument, source_dataset_id)


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
    def __init__(self, path: Path, total_count: int) -> None:
        self._path = path
        self._total_count = total_count

    def __iter__(self) -> Iterator[object]:
        with self._path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield _TargetSummary.from_json_value(json.loads(line))

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

    def __init__(self, row: TargetRow) -> None:
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

    def as_json_value(self) -> dict[str, object]:
        return {
            "decision_time": self.decision_time.isoformat(),
            "horizon_seconds": self.horizon.total_seconds(),
            "instrument_id": self.instrument_id,
            "return_disposition": self.return_disposition.value,
            "target_available_at": self.target_available_at.isoformat(),
            "target_basis": self.target_basis.value,
            "target_end_time": self.target_end_time.isoformat(),
            "target_freeze_at": self.target_freeze_at.isoformat(),
            "target_id": self.target_id,
            "target_start_time": self.target_start_time.isoformat(),
        }

    @classmethod
    def from_json_value(cls, value: Mapping[str, object]) -> _TargetSummary:
        summary = cls.__new__(cls)
        summary.instrument_id = str(value["instrument_id"])
        summary.decision_time = datetime.fromisoformat(str(value["decision_time"]))
        summary.horizon = timedelta(
            seconds=float(cast(str | int | float, value["horizon_seconds"]))
        )
        summary.target_basis = PriceBasis(str(value["target_basis"]))
        summary.target_id = str(value["target_id"])
        summary.target_start_time = datetime.fromisoformat(str(value["target_start_time"]))
        summary.target_end_time = datetime.fromisoformat(str(value["target_end_time"]))
        summary.target_freeze_at = datetime.fromisoformat(str(value["target_freeze_at"]))
        summary.target_available_at = datetime.fromisoformat(str(value["target_available_at"]))
        summary.return_disposition = ReturnDisposition(str(value["return_disposition"]))
        return summary


def _fast_target_rows(
    rows: Sequence[ObservationRow],
    config: FoundationConfig,
    *,
    horizons: Sequence[timedelta],
) -> Iterator[TargetRow] | None:
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
        return iter(())

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
        return iter(())
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
            bad_high_prefix[-1] + (row is not None and (row.high <= 0 or not row.high.is_finite()))
        )
        bad_low_prefix.append(
            bad_low_prefix[-1] + (row is not None and (row.low <= 0 or not row.low.is_finite()))
        )

    metric_sets: list[
        tuple[
            list[Decimal | None],
            list[Decimal | None],
            list[datetime | None],
            list[bool],
            list[bool],
        ]
    ] = []
    for steps in horizon_steps:
        max_high: deque[tuple[int, Decimal]] = deque()
        min_low: deque[tuple[int, Decimal]] = deque()
        max_available: deque[tuple[int, datetime]] = deque()
        highs: list[Decimal | None] = [None] * len(decision_times)
        lows: list[Decimal | None] = [None] * len(decision_times)
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
                availabilities[decision_index] = max_available[-1][1] if max_available else None
                missing[decision_index] = (
                    missing_prefix[right + 1] - missing_prefix[decision_index + 1] != 0
                )
                bad[decision_index] = bool(
                    bad_high_prefix[right + 1] - bad_high_prefix[decision_index + 1]
                    or bad_low_prefix[right + 1] - bad_low_prefix[decision_index + 1]
                )
        metric_sets.append((highs, lows, availabilities, missing, bad))

    def _iter_rows() -> Iterator[TargetRow]:
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
                if return_disposition is ReturnDisposition.VALID:
                    assert label_start is not None and label_end is not None
                    log_return = _log_ratio(label_start, label_end)
                else:
                    log_return = None
                highs, lows, max_available, path_missing, path_bad = metric_sets[horizon_index]
                path_high = highs[decision_index]
                path_low = lows[decision_index]
                latest_available = max_available[decision_index]
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
                    or (latest_available is not None and latest_available > freeze_at)
                    or path_high is None
                    or path_low is None
                ):
                    upper = lower = None
                    excursion_disposition = ExcursionDisposition.INCOMPLETE_PATH
                else:
                    try:
                        upper = _log_ratio(path_high, start_row.close)
                        lower = _log_ratio(path_low, start_row.close)
                        excursion_disposition = ExcursionDisposition.VALID
                    except (ValueError, OverflowError):
                        upper = lower = None
                        excursion_disposition = ExcursionDisposition.INCOMPLETE_PATH

                yield TargetRow(
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

    return _iter_rows()


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
    path: Path,
    *,
    target_dataset_id: str,
    observation_dataset_id: str,
    foundation_configuration_id: str,
    total_count: int,
) -> Any:
    return SimpleNamespace(
        rows=_SummaryRows(path, total_count),
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
    checkpoint_root: Path | None = None,
    progress_callback: _ProgressCallback | None = None,
) -> tuple[IBKRFoundationBuild, dict[str, tuple[dict[str, object], ...]]]:
    """Build and publish a large provider-history foundation with bounded memory."""

    progress = _Progress(progress_callback)
    provider_rows = source_evidence.observations
    if not provider_rows:
        raise ValueError("provider-history source has no observations")

    observed_instruments = set(_provider_instruments(provider_rows))
    positioned = getattr(provider_rows, "iter_instrument_with_positions", None)
    grouped = None if callable(positioned) else _sorted_groups(provider_rows)
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
    progress.emit(
        "source",
        "started",
        instrument_count=len(ordered_instruments),
        provider_row_count=source_evidence.dataset.row_count,
    )

    checkpoint = _Stage8Checkpoint(
        checkpoint_root,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset_sha256=source_evidence.dataset.dataset_sha256,
        configuration_id=configuration.configuration_id,
    )
    active_intervals, provider_gaps, source_start, source_end = _provider_evidence(
        source_evidence,
        include_bounds=True,
    )
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
    progress.emit(
        "source",
        "completed",
        active_interval_count=len(active_intervals),
        gap_count=len(provider_gaps),
    )
    lineage = _foundation_child_lineage(source_evidence, provider_manifest_sha256)
    if not child_root.exists():
        child_root.mkdir(parents=True, exist_ok=False)
    panel_tmp = child_root / ".panel-temp"
    panel_tmp.mkdir()

    observation_writer = _DeferredChildWriter(
        child_root=child_root,
        bundle_root=bundle_root,
        child_name=child_name,
        kind="observations",
        lineage=lineage,
    )
    observation_hasher = _StreamingRowsHash(global_observation_metadata)
    for instrument_index, instrument in enumerate(ordered_instruments, 1):
        cached_observation = checkpoint.observation(instrument)
        if cached_observation is not None:
            payloads = _checkpoint_payloads(cached_observation.rows)
            row_count = cached_observation.rows.row_count
            event = "reused"
        elif checkpoint_root is None:
            row_count = 0
            for row_index, row in enumerate(
                _adapted_instrument_rows(
                    provider_rows,
                    grouped,
                    instrument,
                    source_evidence.dataset.dataset_sha256,
                ),
                1,
            ):
                payload = _canonical_row(row.as_json())
                observation_hasher.add(payload)
                observation_writer.add(payload)
                row_count = row_index
            payloads = ()
            event = "completed"
        else:
            observation_path = panel_tmp / f"{instrument_index:05d}.observations.jsonl"
            instrument_start: datetime | None = None
            instrument_end: datetime | None = None
            with observation_path.open("w", encoding="utf-8") as stream:
                for row in _adapted_instrument_rows(
                    provider_rows,
                    grouped,
                    instrument,
                    source_evidence.dataset.dataset_sha256,
                ):
                    instrument_start = (
                        row.interval_start
                        if instrument_start is None
                        else min(instrument_start, row.interval_start)
                    )
                    instrument_end = (
                        row.interval_end
                        if instrument_end is None
                        else max(instrument_end, row.interval_end)
                    )
                    stream.write(_canonical_row(row.as_json()))
                    stream.write("\n")
            cached_observation = checkpoint.store_observation(
                instrument,
                observation_path,
                source_start=instrument_start,
                source_end=instrument_end,
            )
            payloads = _checkpoint_payloads(cached_observation.rows)
            row_count = cached_observation.rows.row_count
            event = "completed"
        for payload in payloads:
            observation_hasher.add(payload)
            observation_writer.add(payload)
        progress.emit(
            "observations",
            event,
            instrument_id=instrument,
            instrument_index=instrument_index,
            instrument_count=len(ordered_instruments),
            row_count=row_count,
        )
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

    panel_files: list[Path] = []
    summary_path = panel_tmp / ".primary-target-summaries.jsonl"
    summary_stream = summary_path.open("w", encoding="utf-8")
    target_row_count = 0
    try:
        for instrument_index, instrument in enumerate(ordered_instruments):
            role = roles[instrument]
            panel_file = panel_tmp / f"{instrument_index:05d}.jsonl"
            cached_derived = checkpoint.derived(
                instrument,
                foundation_configuration_id=adapted_configuration.configuration_id,
            )
            if cached_derived is not None:
                shutil.copyfile(cached_derived.panel.path, panel_file)
                panel_files.append(panel_file)
                for payload in _checkpoint_payloads(cached_derived.targets):
                    target_hasher.add(payload)
                    target_writer.add(payload)
                target_row_count += cached_derived.targets.row_count
                for payload in _checkpoint_payloads(cached_derived.summaries):
                    summary_stream.write(payload)
                    summary_stream.write("\n")
                progress.emit(
                    "derived",
                    "reused",
                    instrument_id=instrument,
                    instrument_index=instrument_index + 1,
                    instrument_count=len(ordered_instruments),
                    panel_row_count=cached_derived.panel.row_count,
                    target_row_count=cached_derived.targets.row_count,
                )
                continue

            rows = _adapted_instrument_rows(
                provider_rows,
                grouped,
                instrument,
                source_evidence.dataset.dataset_sha256,
            )
            local_observation_configuration = _local_observation_configuration(
                observation_configuration, instrument
            )
            local_observation_metadata = _observation_metadata(
                local_observation_configuration,
                source_dataset_id=source_evidence.dataset.dataset_sha256,
                selection_policies=selection_policies,
            )
            cached_observation = checkpoint.observation(instrument)
            local_observation_id = (
                _dataset_id_from_checkpoint(local_observation_metadata, cached_observation.rows)
                if cached_observation is not None
                else _dataset_id(local_observation_metadata, (row.as_json() for row in rows))
            )
            local_configuration = _local_foundation_configuration(
                adapted_configuration,
                instrument=instrument,
                role=role,
                observation_dataset_id=local_observation_id,
            )
            local_dataset = cast(
                ObservationDataset,
                SimpleNamespace(
                    rows=rows,
                    configuration=local_observation_configuration,
                    dataset_id=local_observation_id,
                ),
            )
            panel = build_asof_panel(
                local_dataset,
                local_configuration,
                source_active_intervals=active_intervals,
            )
            with panel_file.open("w", encoding="utf-8") as stream:
                panel_row_count = 0
                for row in panel.rows:
                    stream.write(_canonical_row(row.as_json()))
                    stream.write("\n")
                    panel_row_count += 1
            panel_files.append(panel_file)
            target_file = panel_tmp / f"{instrument_index:05d}.targets.jsonl"
            instrument_summary_file = panel_tmp / f"{instrument_index:05d}.summaries.jsonl"
            instrument_target_row_count = 0
            with (
                target_file.open("w", encoding="utf-8") as target_stream,
                instrument_summary_file.open("w", encoding="utf-8") as instrument_summary_stream,
            ):
                if role is InstrumentRole.TARGET:
                    fast_rows = _fast_target_rows(
                        rows,
                        local_configuration,
                        horizons=adapted_configuration.target_horizons,
                    )
                    general_targets: TargetDataset | None = None
                    if fast_rows is None:
                        general_targets = build_frozen_targets(
                            local_dataset,
                            local_configuration,
                            horizons=adapted_configuration.target_horizons,
                        )
                        target_rows = general_targets.rows
                    else:
                        target_rows = fast_rows
                    primary_horizon = adapted_configuration.primary_vertical_horizon
                    for row in target_rows:
                        target_row_count += 1
                        instrument_target_row_count += 1
                        payload = _canonical_row(row.as_json())
                        target_hasher.add(payload)
                        target_writer.add(payload)
                        target_stream.write(payload)
                        target_stream.write("\n")
                        if row.horizon == primary_horizon:
                            summary = _TargetSummary(row)
                            summary_payload = _canonical_row(summary.as_json_value())
                            summary_stream.write(summary_payload)
                            summary_stream.write("\n")
                            instrument_summary_stream.write(summary_payload)
                            instrument_summary_stream.write("\n")
                    del target_rows, fast_rows
                    general_targets = None
            derived = (
                checkpoint.store_derived(
                    instrument,
                    foundation_configuration_id=adapted_configuration.configuration_id,
                    panel=panel_file,
                    targets=target_file,
                    summaries=instrument_summary_file,
                )
                if checkpoint_root is not None
                else None
            )
            del panel
            del local_dataset, rows
            gc.collect()
            progress.emit(
                "derived",
                "completed",
                instrument_id=instrument,
                instrument_index=instrument_index + 1,
                instrument_count=len(ordered_instruments),
                panel_row_count=(
                    derived.panel.row_count if derived is not None else panel_row_count
                ),
                target_row_count=(
                    derived.targets.row_count
                    if derived is not None
                    else instrument_target_row_count
                ),
            )

        progress.emit("panel_merge", "started", instrument_count=len(panel_files))
        _merge_panel_files(
            panel_files,
            target_writer=panel_writer,
            target_hasher=panel_hasher,
        )
        panel_dataset_id = panel_hasher.finish()
        target_dataset_id = target_hasher.finish()
        panel_refs = panel_writer.finalize(panel_dataset_id)
        target_refs = target_writer.finalize(target_dataset_id)
        progress.emit("panel_merge", "completed", panel_dataset_id=panel_dataset_id)

        summary_stream.close()
        summary_dataset = _make_summary_target_dataset(
            summary_path,
            target_dataset_id=target_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
            total_count=target_row_count,
        )
        progress.emit("folds", "started")
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
        progress.emit("folds", "completed", fold_count=len(folds.folds))
        readiness = evaluate_ibkr_foundation_readiness(
            source_evidence,
            summary_dataset,
            source_start=source_start,
            source_end=source_end,
            active_intervals=active_intervals,
            provider_gaps=provider_gaps,
            primary_horizon=adapted_configuration.primary_vertical_horizon,
            fold_count=len(folds.folds),
        )
    finally:
        if not summary_stream.closed:
            summary_stream.close()
        shutil.rmtree(panel_tmp, ignore_errors=True)

    observations_ref = cast(
        ObservationDataset,
        SimpleNamespace(
            rows=(),
            dataset_id=observation_dataset_id,
            configuration=observation_configuration,
            source_dataset_ids=(source_evidence.dataset.dataset_sha256,),
            selection_policies=selection_policies,
        ),
    )
    panel_ref = cast(
        PanelDataset,
        SimpleNamespace(
            rows=(),
            dataset_id=panel_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
        ),
    )
    target_ref = cast(
        TargetDataset,
        SimpleNamespace(
            rows=(),
            dataset_id=target_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
        ),
    )
    build = IBKRFoundationBuild(
        configuration=adapted_configuration,
        observations=observations_ref,
        panel=panel_ref,
        targets=target_ref,
        folds=folds,
        target_index=None,
        causal_metadata=None,
        provider_history=source_evidence.dataset,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        readiness=readiness,
    )
    progress.emit(
        "foundation",
        "completed",
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        target_dataset_id=target_dataset_id,
        fold_dataset_id=folds.dataset_id,
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
    typed_child_payload = cast(Mapping[str, object], child_payload)
    provider_value = document.get("provider_history_manifest")
    if not isinstance(provider_value, str):
        raise ValueError("foundation provider manifest path is invalid")
    provider_path = bundle_path.parent / provider_value
    root_name: str | None = None
    for raw_parts in typed_child_payload.values():
        if isinstance(raw_parts, Sequence) and raw_parts:
            first = raw_parts[0]
            if isinstance(first, Mapping):
                manifest_path = first.get("manifest_path")
                if isinstance(manifest_path, str):
                    root_name = PurePosixPath(manifest_path).parts[0]
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
            actual_payload=typed_child_payload,
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
