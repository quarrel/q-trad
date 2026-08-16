"""Direct receipt-backed Stage 7 provider-history contract.

The v3 closure owns only its manifest and Stage 7 Parquet parts. Stage 6 is
authenticated as an immediate parent and consumed exactly once by build/deep
verification; ordinary authentication never reopens Stage 6 bytes.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import polars as pl

from qtrad.application.provider_history import (
    ProviderHistoryObservationRows,
    ProviderHistoryObservationSummary,
    ProviderHistoryRequestEvidence,
    ProviderHistorySelection,
    ProviderHistorySourceEvidence,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrHistoricalRequestKind,
    IbkrPlannedContract,
)
from qtrad.domain.ibkr_results import (
    IbkrHistoricalEvidenceDisposition,
    IbkrHistoricalRequestResult,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.provider_history import (
    PROVIDER_HISTORICAL_OBSERVATIONS_V3_CONTRACT,
    PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT,
    PROVIDER_HISTORY_BAR_BASIS,
    PROVIDER_HISTORY_CORRECTION_POLICY,
    PROVIDER_HISTORY_DECLARED_DELAY,
    PROVIDER_HISTORY_ENVIRONMENT,
    PROVIDER_HISTORY_POLICY,
    PROVIDER_HISTORY_PROVIDER,
    PROVIDER_HISTORY_SOURCE_CLASS,
    PROVIDER_HISTORY_V3_SCHEMA_VERSION,
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalDatasetV3,
    ProviderHistoricalObservation,
    ProviderHistoricalPartition,
    ProviderHistoricalPartitionReference,
    row_sort_key,
    sha256_json,
    utc_text,
)
from qtrad.runtime.ibkr_results import (
    IbkrHistoricalResultStream,
    authenticate_ibkr_historical_result,
)

MANIFEST_NAME = "manifest.json"
OBSERVATION_FIELDS = (
    "contract",
    "schema_version",
    "source_class",
    "provider",
    "environment",
    "instrument_id",
    "contract_selection_identity",
    "plan_sha256",
    "interval_start",
    "interval_end",
    "basis",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "wap",
    "count",
    "callback_sequence",
    "request_sha256",
    "result_sha256",
    "attempt_id",
    "attempt_started_at",
    "attempt_completed_at",
    "acquisition_started_at",
    "acquisition_completed_at",
    "available_at",
    "availability_selector",
    "availability_policy",
    "availability_delay",
    "correction_policy",
    "schedule_evidence",
    "gap_disposition",
    "observation_sha256",
)


# Sole retained compatibility: the H4 attempt3 Stage 7 v3 packet required by
# the active R2 run has this obsolete physical field. Remove this branch when
# that packet is replaced or no longer used.
_RETAINED_OBSERVATION_FIELDS = (
    "contract",
    "schema_version",
    "source_class",
    "provider",
    "environment",
    "instrument_id",
    "contract_selection_identity",
    "plan_sha256",
    "interval_start",
    "interval_end",
    "basis",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "wap",
    "count",
    "callback_sequence",
    "request_sha256",
    "result_sha256",
    "aggregate_sha256",
    "attempt_id",
    "attempt_started_at",
    "attempt_completed_at",
    "acquisition_started_at",
    "acquisition_completed_at",
    "available_at",
    "availability_selector",
    "availability_policy",
    "availability_delay",
    "correction_policy",
    "schedule_evidence",
    "gap_disposition",
    "observation_sha256",
)
PART_FIELDS = {
    "instrument_id",
    "minimum_interval_start",
    "maximum_interval_end",
    "row_count",
    "ordered_row_sha256",
    "bytes_sha256",
    "path",
    "part_ordinal",
}
MANIFEST_FIELDS = {
    "contract",
    "schema_version",
    "selector_contract",
    "dataset",
    "availability_policy",
    "stage6",
    "parts",
    "closure_id",
    "physical_manifest_sha256",
}
STAGE6_FIELDS = {
    "result_id",
    "closure_id",
    "verification_id",
    "manifest_sha256",
    "contract_selection_sha256",
    "plan_sha256",
    "runtime_sha256",
    "requests",
    "eligible_contracts",
    "coverage_summary",
    "entitlement_summary",
    "request_evidence",
}
RECEIPT_FIELDS = {
    "contract",
    "schema_version",
    "provider_history_contract",
    "provider_history_schema_version",
    "provider_history_manifest_sha256",
    "provider_history_dataset_sha256",
    "closure_id",
    "stage6_result_id",
    "stage6_closure_id",
    "stage6_verification_id",
    "availability_policy",
    "source_summary_sha256",
    "verifier_contract",
    "verifier_version",
    "verifier_identity",
    "completed_checks",
    "verification_id",
}
V3_VERIFICATION_CONTRACT = "qtrad-provider-history-verification-v3"
V3_VERIFIER_CONTRACT = "qtrad-provider-history-semantic-verifier-v3"
V3_VERIFIER_VERSION = 1
V3_COMPLETED_CHECKS = (
    "manifest-and-closure-bytes",
    "authenticated-stage6-parent",
    "availability-policy-binding",
    "stage7-part-semantic-replay",
    "stage6-request-summary",
)
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024 * 1024
MAX_PART_ROWS = 50_000
DEFAULT_AVAILABILITY_POLICY = ProviderHistoricalAvailabilityPolicy(
    selector=PROVIDER_HISTORY_DECLARED_DELAY,
    policy=PROVIDER_HISTORY_POLICY,
    delay=timedelta(minutes=5),
)


@dataclass(frozen=True, slots=True)
class ProviderHistoryV3PartReference:
    instrument_id: str
    minimum_interval_start: datetime
    maximum_interval_end: datetime
    row_count: int
    ordered_row_sha256: str
    bytes_sha256: str
    path: str
    part_ordinal: int

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "instrument_id": self.instrument_id,
            "minimum_interval_start": utc_text(self.minimum_interval_start),
            "maximum_interval_end": utc_text(self.maximum_interval_end),
            "row_count": self.row_count,
            "ordered_row_sha256": self.ordered_row_sha256,
            "bytes_sha256": self.bytes_sha256,
            "path": self.path,
            "part_ordinal": self.part_ordinal,
        }

    @classmethod
    def from_json_value(
        cls, value: object, *, previous: ProviderHistoryV3PartReference | None = None
    ) -> ProviderHistoryV3PartReference:
        item = _mapping(value, "provider-history v3 part")
        _require_exact_keys(item, PART_FIELDS, "provider-history v3 part")
        instrument_id = _string(item["instrument_id"], "provider-history v3 instrument")
        minimum = _utc(item["minimum_interval_start"], "provider-history v3 minimum")
        maximum = _utc(item["maximum_interval_end"], "provider-history v3 maximum")
        row_count = _integer(item["row_count"], "provider-history v3 row count")
        ordinal = _integer(item["part_ordinal"], "provider-history v3 ordinal")
        ordered_hash = _string(item["ordered_row_sha256"], "provider-history v3 ordered rows")
        bytes_hash = _string(item["bytes_sha256"], "provider-history v3 bytes")
        path = _string(item["path"], "provider-history v3 part path")
        _require_digest(ordered_hash, "provider-history v3 ordered rows")
        _require_digest(bytes_hash, "provider-history v3 bytes")
        if (
            not instrument_id
            or ":" not in instrument_id
            or maximum <= minimum
            or row_count <= 0
            or row_count > MAX_PART_ROWS
            or ordinal <= 0
        ):
            raise ValueError("provider-history v3 part metadata is invalid")
        expected = _part_path(instrument_id, minimum.year, minimum.month, ordinal)
        if path != expected:
            raise ValueError("provider-history v3 part path is not canonical")
        result = cls(
            instrument_id=instrument_id,
            minimum_interval_start=minimum,
            maximum_interval_end=maximum,
            row_count=row_count,
            ordered_row_sha256=ordered_hash,
            bytes_sha256=bytes_hash,
            path=path,
            part_ordinal=ordinal,
        )
        if previous is not None:
            previous_key = (
                previous.instrument_id,
                previous.minimum_interval_start.year,
                previous.minimum_interval_start.month,
                previous.part_ordinal,
            )
            key = (
                result.instrument_id,
                result.minimum_interval_start.year,
                result.minimum_interval_start.month,
                result.part_ordinal,
            )
            if key <= previous_key:
                raise ValueError("provider-history v3 parts are not canonical")
            if key[:3] == previous_key[:3] and ordinal != previous.part_ordinal + 1:
                raise ValueError("provider-history v3 split ordinals are not contiguous")
            if key[:3] != previous_key[:3] and ordinal != 1:
                raise ValueError("provider-history v3 split ordinals must restart at one")
        elif ordinal != 1:
            raise ValueError("provider-history v3 first split ordinal must be one")
        return result


@dataclass(frozen=True, slots=True)
class ProviderHistoryV3Manifest:
    path: Path
    bytes: bytes
    document: dict[str, object]
    dataset: ProviderHistoricalDatasetV3
    stage6: dict[str, object]
    parts: tuple[ProviderHistoryV3PartReference, ...]


@dataclass(frozen=True, slots=True)
class _PartInput:
    reference: ProviderHistoryV3PartReference
    path: Path
    global_offset: int


@dataclass(frozen=True, slots=True)
class _PlanSummary:
    contract_selection_sha256: str
    runtime_sha256: str
    plan_sha256: str
    eligible_contracts: tuple[IbkrPlannedContract, ...]
    requests: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _Stage6SourceResultSummary:
    result_id: str
    closure_id: str
    coverage_summary: dict[str, JsonValue]
    entitlement_summary: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class _SourceSummary:
    plan: _PlanSummary
    source_result: _Stage6SourceResultSummary
    verification_id: str
    request_results: tuple[object, ...] = ()


class ProviderHistoryV3Rows:
    """Re-iterable selected Stage 7 rows; parts are authenticated on consumption."""

    def __init__(
        self,
        *,
        manifest: ProviderHistoryV3Manifest,
        selected_parts: tuple[_PartInput, ...],
        selection: ProviderHistorySelection | None,
    ) -> None:
        self._manifest = manifest
        self._selected_parts = selected_parts
        self.selection = selection
        self._part_cache: dict[Path, tuple[ProviderHistoricalObservation, ...]] = {}
        self.instruments = tuple(sorted({p.reference.instrument_id for p in selected_parts}))

    def __len__(self) -> int:
        if self.selection is not None:
            return self.selection.row_count_upper_bound
        return self._manifest.dataset.row_count

    def __iter__(self) -> Iterator[ProviderHistoricalObservation]:
        for part in self._selected_parts:
            for row, _ in self._iter_part_with_positions(part):
                yield row

    def iter_instrument(self, instrument_id: str) -> Iterator[ProviderHistoricalObservation]:
        return (row for row, _ in self.iter_instrument_with_positions(instrument_id))

    def iter_instrument_with_positions(
        self, instrument_id: str
    ) -> Iterator[tuple[ProviderHistoricalObservation, int]]:
        for part in self._selected_parts:
            if part.reference.instrument_id == instrument_id:
                yield from self._iter_part_with_positions(part)

    def _iter_part_with_positions(
        self, part: _PartInput
    ) -> Iterator[tuple[ProviderHistoricalObservation, int]]:
        rows = self._part_cache.get(part.path)
        if rows is None:
            rows = _read_part(part.path, part.reference)
            self._part_cache[part.path] = rows
        for index, row in enumerate(rows, part.global_offset + 1):
            if self.selection is not None and (
                row.instrument_id not in self.selection.requested_instrument_ids
                or row.interval_start < self.selection.interval_start
                or row.interval_end > self.selection.interval_end
            ):
                continue
            yield row, index


def provider_history_v3_verifier_sha256() -> str:
    return sha256_json(
        {
            "contract": V3_VERIFIER_CONTRACT,
            "version": V3_VERIFIER_VERSION,
            "completed_checks": list(V3_COMPLETED_CHECKS),
        }
    )


def build_provider_history(
    stage6_manifest: Path,
    *,
    stage6_receipt: Path,
    output: Path,
    availability_policy: ProviderHistoricalAvailabilityPolicy = DEFAULT_AVAILABILITY_POLICY,
) -> Path:
    """Build and structurally publish one direct Stage 7 closure."""
    stream = authenticate_ibkr_historical_result(stage6_manifest, receipt=stage6_receipt)
    rows, evidence, summary = _derive_rows(stream, availability_policy)
    manifest, part_payloads = _build_manifest(
        stage6_manifest,
        stage6_receipt,
        stream,
        rows,
        evidence,
        summary,
        availability_policy,
    )
    _publish(output, manifest, part_payloads)
    return output / MANIFEST_NAME


def verify_provider_history(
    path: Path,
    *,
    stage6_manifest: Path,
    stage6_receipt: Path,
    receipt_output: Path,
    availability_policy: ProviderHistoricalAvailabilityPolicy = DEFAULT_AVAILABILITY_POLICY,
) -> ProviderHistorySourceEvidence:
    """Deeply verify Stage 7 against its explicit Stage 6 parent and receipt."""
    manifest = _read_manifest(path)
    _require_exact_tree(path.parent, {MANIFEST_NAME, *(part.path for part in manifest.parts)})
    receipt_path = _preflight_receipt(receipt_output, path)
    stream = authenticate_ibkr_historical_result(stage6_manifest, receipt=stage6_receipt)
    policy = manifest.dataset.availability_policy
    if availability_policy != policy:
        raise ValueError("provider-history availability policy changed")
    rows, evidence, summary = _derive_rows(stream, policy)
    expected, expected_payloads = _build_manifest(
        stage6_manifest,
        stage6_receipt,
        stream,
        rows,
        evidence,
        summary,
        policy,
    )
    if expected.document != manifest.document:
        raise ValueError("provider-history v3 manifest differs from independent reconstruction")
    for reference in manifest.parts:
        actual = _read_bounded(
            _safe_child(path.parent, reference.path, "provider-history part"),
            "provider-history part",
        )
        if actual != expected_payloads[reference.path]:
            raise ValueError(
                "provider-history v3 part bytes differ from independent reconstruction"
            )
    observation_summary = _observation_summary_from_document(summary["observation_summary"])
    receipt = _receipt_document(manifest)
    result = _source_evidence(
        manifest,
        rows,
        evidence,
        observation_summary,
        stage7_verification_id=_string(receipt["verification_id"], "Stage 7 verification"),
    )
    _write_create_only(receipt_path, canonical_json_bytes(receipt))
    return result


def verify_provider_history_file_only(path: Path) -> ProviderHistoryV3Manifest:
    """Authenticate manifest/tree metadata without reading Stage 7 part bodies."""
    manifest = _read_manifest(path)
    expected = {MANIFEST_NAME, *(part.path for part in manifest.parts)}
    _require_exact_tree(path.parent, expected)
    return manifest


def authenticate_provider_history_v3(
    path: Path,
    *,
    receipt: Path,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> ProviderHistorySourceEvidence:
    """Authenticate Stage 7 metadata/receipt; Stage 6 and unselected parts stay unread."""
    manifest = verify_provider_history_file_only(path)
    receipt_document = _read_receipt(receipt)
    _validate_receipt(manifest, receipt_document)
    rows = _selected_rows(
        manifest,
        instrument_ids=instrument_ids,
        interval_start=interval_start,
        interval_end=interval_end,
    )
    evidence = _request_evidence_from_stage6(manifest.stage6)
    summary = _observation_summary_from_document(manifest.stage6.get("observation_summary"))
    return _source_evidence(
        manifest,
        rows,
        evidence,
        summary,
        stage7_verification_id=_string(receipt_document["verification_id"], "Stage 7 verification"),
    )


def provider_history_v3_rows(
    path: Path,
    dataset: ProviderHistoricalDatasetV3,
    *,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> ProviderHistoryV3Rows:
    manifest = verify_provider_history_file_only(path)
    if manifest.dataset.dataset_sha256 != dataset.dataset_sha256:
        raise ValueError("provider-history v3 dataset identity changed")
    return _selected_rows(
        manifest,
        instrument_ids=instrument_ids,
        interval_start=interval_start,
        interval_end=interval_end,
    )


def _derive_rows(
    stream: IbkrHistoricalResultStream,
    policy: ProviderHistoricalAvailabilityPolicy,
) -> tuple[
    tuple[ProviderHistoricalObservation, ...],
    tuple[ProviderHistoryRequestEvidence, ...],
    dict[str, object],
]:
    requests = {request.request_sha256: request for request in stream.plan.requests}
    all_rows: list[ProviderHistoricalObservation] = []
    request_evidence: list[ProviderHistoryRequestEvidence] = []
    for result in stream.iter_request_results():
        request = requests.get(result.request_sha256)
        if request is None:
            raise ValueError("Stage 6 request-result child is absent from its plan")
        request_evidence.append(ProviderHistoryRequestEvidence.from_result(result))
        if (
            request.kind is not IbkrHistoricalRequestKind.MIDPOINT_BARS
            or result.evidence_disposition is not IbkrHistoricalEvidenceDisposition.SUCCEEDED
        ):
            continue
        selected_attempt = next(
            (
                attempt
                for attempt in result.attempts
                if attempt.attempt_id == result.selected_attempt_id
            ),
            None,
        )
        if selected_attempt is None:
            raise ValueError("Stage 6 selected attempt is absent from request result")
        for raw in result.accepted_rows:
            all_rows.append(
                _observation_from_row(stream, result, request, selected_attempt, raw, policy)
            )
    ordered = tuple(sorted(all_rows, key=row_sort_key))
    if not ordered:
        raise ValueError("provider-history Stage 7 requires at least one accepted observation")
    partition_refs: list[ProviderHistoricalPartitionReference] = []
    grouped: dict[tuple[str, date], list[ProviderHistoricalObservation]] = {}
    for row in ordered:
        grouped.setdefault((row.instrument_id, row.interval_start.date()), []).append(row)
    for key in sorted(grouped):
        partition = ProviderHistoricalPartition.create(rows=tuple(grouped[key]))
        partition_refs.append(ProviderHistoricalPartitionReference.from_partition(partition))
    intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    for row in ordered:
        values = intervals.setdefault(row.request_sha256, [])
        end = row.interval_start + timedelta(minutes=1)
        if values and row.interval_start <= values[-1][1]:
            values[-1] = (values[-1][0], max(values[-1][1], end))
        else:
            values.append((row.interval_start, end))
    observation_summary = {
        "accepted_intervals_by_request": [
            {
                "request_sha256": request_sha256,
                "intervals": [[utc_text(start), utc_text(end)] for start, end in values],
            }
            for request_sha256, values in sorted(intervals.items())
        ],
        "source_start": utc_text(min(row.interval_start for row in ordered)),
        "source_end": utc_text(max(row.interval_end for row in ordered)),
    }
    summary = {
        "request_evidence": [_request_evidence_json(item) for item in request_evidence],
        "observation_summary": observation_summary,
    }
    return (
        ordered,
        tuple(request_evidence),
        {
            "partitions": tuple(partition_refs),
            "observation_summary": observation_summary,
            "request_evidence": summary["request_evidence"],
        },
    )


def _observation_from_row(
    stream: IbkrHistoricalResultStream,
    result: IbkrHistoricalRequestResult,
    request: object,
    attempt: object,
    raw: Mapping[str, object],
    policy: ProviderHistoricalAvailabilityPolicy,
) -> ProviderHistoricalObservation:
    instrument = str(cast(Any, request).instrument_id)
    start = _utc(raw["bar_start"], "accepted bar start")
    end = _utc(raw["bar_end"], "accepted bar end")
    selected = cast(Any, attempt)
    values = {
        name: _decimal_text(raw[name], f"accepted bar {name}")
        for name in ("open", "high", "low", "close")
    }
    return ProviderHistoricalObservation.create(
        source_class=PROVIDER_HISTORY_SOURCE_CLASS,
        provider=PROVIDER_HISTORY_PROVIDER,
        environment=PROVIDER_HISTORY_ENVIRONMENT,
        instrument_id=instrument,
        contract_selection_identity=stream.plan.contract_selection_sha256,
        plan_sha256=stream.plan.plan_sha256,
        interval_start=start,
        interval_end=end,
        basis=PROVIDER_HISTORY_BAR_BASIS,
        open=values["open"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
        volume=_optional_text(raw.get("volume")),
        wap=_optional_text(raw.get("wap")),
        count=_optional_int(raw.get("count")),
        callback_sequence=_optional_int(raw.get("callback_sequence")),
        request_sha256=result.request_sha256,
        result_sha256=result.result_sha256,
        attempt_id=selected.attempt_id,
        attempt_started_at=selected.started_at,
        attempt_completed_at=selected.terminal_at or result.acquisition_completed_at,
        acquisition_started_at=result.acquisition_started_at,
        acquisition_completed_at=result.acquisition_completed_at,
        available_at=end + policy.delay,
        availability_selector=policy.selector,
        availability_policy=policy.policy,
        availability_delay=policy.delay_text,
        correction_policy=PROVIDER_HISTORY_CORRECTION_POLICY,
        schedule_evidence={},
        gap_disposition=result.evidence_disposition.value,
    )


def _build_manifest(
    stage6_manifest: Path,
    stage6_receipt: Path,
    stream: IbkrHistoricalResultStream,
    rows: tuple[ProviderHistoricalObservation, ...],
    evidence: tuple[ProviderHistoryRequestEvidence, ...],
    summary: dict[str, object],
    policy: ProviderHistoricalAvailabilityPolicy,
) -> tuple[ProviderHistoryV3Manifest, dict[str, bytes]]:
    stage6_manifest_bytes = stage6_manifest.read_bytes()
    stage6 = _stage6_document(stage6_manifest_bytes, stage6_receipt, stream, evidence, summary)
    parts, payloads = _part_payloads(rows)
    dataset = ProviderHistoricalDatasetV3.create(
        partitions=cast(tuple[Any, ...], summary["partitions"]),
        contract_selection_sha256=stream.plan.contract_selection_sha256,
        stage6_result_id=stream.aggregate.result_id,
        availability_policy=policy,
        source_start=min(row.interval_start for row in rows),
        source_end=max(row.interval_end for row in rows),
        stage6_plan_sha256=stream.plan.plan_sha256,
        stage6_runtime_sha256=stream.plan.runtime_sha256,
        stage6_closure_id=stream.aggregate.closure_id,
        stage6_verification_id=_stage6_verification_id(stage6_receipt),
        stage6_manifest_sha256=sha256_bytes(stage6_manifest_bytes),
    )
    identity = {
        "contract": PROVIDER_HISTORICAL_OBSERVATIONS_V3_CONTRACT,
        "schema_version": PROVIDER_HISTORY_V3_SCHEMA_VERSION,
        "selector_contract": PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT,
        "dataset": dataset.as_json_value(),
        "availability_policy": policy.as_json_value(),
        "stage6": stage6,
        "parts": [part.as_json_value() for part in parts],
    }
    closure_id = sha256_json(
        {
            "manifest_identity": sha256_json(identity),
            "part_bytes": [part.bytes_sha256 for part in parts],
        }
    )
    with_closure = {**identity, "closure_id": closure_id}
    physical = sha256_json(with_closure)
    document = {**with_closure, "physical_manifest_sha256": physical}
    encoded = canonical_json_bytes(cast(Mapping[str, JsonValue], document))
    manifest = ProviderHistoryV3Manifest(
        path=stage6_manifest.parent / "manifest.json",
        bytes=encoded,
        document=cast(dict[str, object], document),
        dataset=dataset,
        stage6=stage6,
        parts=tuple(parts),
    )
    return manifest, payloads


def _stage6_document(
    manifest_bytes: bytes,
    stage6_receipt: Path,
    stream: IbkrHistoricalResultStream,
    evidence: tuple[ProviderHistoryRequestEvidence, ...],
    summary: dict[str, object],
) -> dict[str, object]:
    requests = [
        {
            "request_sha256": request.request_sha256,
            "instrument_id": str(request.instrument_id),
            "kind": request.kind.value,
            "interval_start": utc_text(request.interval_start),
            "interval_end": utc_text(request.interval_end),
        }
        for request in sorted(stream.plan.requests, key=lambda item: item.request_sha256)
    ]
    return {
        "result_id": stream.aggregate.result_id,
        "closure_id": stream.aggregate.closure_id,
        "verification_id": _stage6_verification_id(stage6_receipt),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "contract_selection_sha256": stream.plan.contract_selection_sha256,
        "plan_sha256": stream.plan.plan_sha256,
        "runtime_sha256": stream.plan.runtime_sha256,
        "requests": requests,
        "eligible_contracts": [
            contract.as_json_value()
            for contract in sorted(
                stream.plan.eligible_contracts, key=lambda item: str(item.instrument_id)
            )
        ],
        "coverage_summary": stream.aggregate.coverage_summary,
        "entitlement_summary": stream.aggregate.entitlement_summary,
        "request_evidence": [_request_evidence_json(item) for item in evidence],
        "observation_summary": summary["observation_summary"],
    }


def _part_payloads(
    rows: tuple[ProviderHistoricalObservation, ...],
) -> tuple[tuple[ProviderHistoryV3PartReference, ...], dict[str, bytes]]:
    grouped: dict[tuple[str, int, int], list[ProviderHistoricalObservation]] = {}
    for row in rows:
        key = (row.instrument_id, row.interval_start.year, row.interval_start.month)
        grouped.setdefault(key, []).append(row)
    references: list[ProviderHistoryV3PartReference] = []
    payloads: dict[str, bytes] = {}
    for instrument_id, year, month in sorted(grouped):
        monthly = tuple(sorted(grouped[(instrument_id, year, month)], key=row_sort_key))
        for offset in range(0, len(monthly), MAX_PART_ROWS):
            ordinal = offset // MAX_PART_ROWS + 1
            chunk = monthly[offset : offset + MAX_PART_ROWS]
            payload = _encode_rows(chunk)
            path = _part_path(instrument_id, year, month, ordinal)
            reference = ProviderHistoryV3PartReference(
                instrument_id=instrument_id,
                minimum_interval_start=chunk[0].interval_start,
                maximum_interval_end=chunk[-1].interval_end,
                row_count=len(chunk),
                ordered_row_sha256=sha256_json(
                    {"observation_sha256": [row.observation_sha256 for row in chunk]}
                ),
                bytes_sha256=sha256_bytes(payload),
                path=path,
                part_ordinal=ordinal,
            )
            references.append(reference)
            payloads[path] = payload
    return tuple(references), payloads


def _encode_rows(rows: Sequence[ProviderHistoricalObservation]) -> bytes:
    values: list[dict[str, object]] = []
    for row in rows:
        item = cast(dict[str, object], row.as_json_value().copy())
        item["schedule_evidence"] = json.dumps(
            item["schedule_evidence"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        values.append(item)
    frame = pl.DataFrame(values).select(OBSERVATION_FIELDS)
    output = io.BytesIO()
    frame.write_parquet(output, compression="zstd")
    return output.getvalue()


def _translate_retained_observation_identity(row: dict[str, object]) -> tuple[str, str]:
    """Translate the retained ancestry-bearing identity into current semantics.

    The retained H4 packet included aggregate_sha256 in each observation
    identity. Validate that obsolete identity before deriving the current
    identity after removing the ancestry field.
    """
    aggregate = row["aggregate_sha256"]
    if not isinstance(aggregate, str):
        raise ValueError("provider-history v3 retained aggregate_sha256 is not a lowercase SHA-256")
    _require_digest(aggregate, "provider-history v3 retained aggregate_sha256")
    legacy_observation_sha256 = row["observation_sha256"]
    if not isinstance(legacy_observation_sha256, str):
        raise ValueError(
            "provider-history v3 retained observation_sha256 is not a lowercase SHA-256"
        )
    _require_digest(
        legacy_observation_sha256,
        "provider-history v3 retained observation_sha256",
    )
    legacy_payload = dict(row)
    del legacy_payload["observation_sha256"]
    if sha256_json(legacy_payload) != legacy_observation_sha256:
        raise ValueError("provider-history v3 retained legacy observation identity changed")
    current_payload = dict(legacy_payload)
    del current_payload["aggregate_sha256"]
    return legacy_observation_sha256, sha256_json(current_payload)


def _read_part(
    path: Path, reference: ProviderHistoryV3PartReference
) -> tuple[ProviderHistoricalObservation, ...]:
    """Read one current or explicitly retained Stage 7 v3 physical part.

    The retained H4 attempt3 packet is the sole compatibility exception; its
    obsolete aggregate_sha256 field is translated from its ancestry-bearing
    identity before current domain decoding. Remove this exception when that
    packet is replaced or no longer used.
    """
    payload = _read_bounded(path, "provider-history v3 part")
    if sha256_bytes(payload) != reference.bytes_sha256:
        raise ValueError("provider-history v3 selected part bytes changed")
    frame = pl.read_parquet(io.BytesIO(payload))
    fields = tuple(frame.columns)
    retained_schema = fields == _RETAINED_OBSERVATION_FIELDS
    if fields != OBSERVATION_FIELDS and not retained_schema:
        raise ValueError("provider-history v3 selected part shape changed")
    if frame.height != reference.row_count:
        raise ValueError("provider-history v3 selected part shape changed")
    observed: list[ProviderHistoricalObservation] = []
    ordered_observation_sha256: list[str] = []
    for raw in frame.to_dicts():
        row = dict(raw)
        if retained_schema:
            schedule = row["schedule_evidence"]
            if not isinstance(schedule, str):
                raise ValueError("provider-history v3 schedule evidence is not canonical JSON")
            row["schedule_evidence"] = json.loads(schedule)
            original_observation_sha256, current_observation_sha256 = (
                _translate_retained_observation_identity(row)
            )
            del row["aggregate_sha256"]
            row["observation_sha256"] = current_observation_sha256
        else:
            original_observation_sha256 = str(row["observation_sha256"])
            schedule = row["schedule_evidence"]
            if not isinstance(schedule, str):
                raise ValueError("provider-history v3 schedule evidence is not canonical JSON")
            row["schedule_evidence"] = json.loads(schedule)
        ordered_observation_sha256.append(original_observation_sha256)
        observed.append(ProviderHistoricalObservation.from_json_value(row))
    rows = tuple(observed)
    if any(row.instrument_id != reference.instrument_id for row in rows):
        raise ValueError("provider-history v3 selected part instrument changed")
    if any(
        (row.interval_start.year, row.interval_start.month)
        != (reference.minimum_interval_start.year, reference.minimum_interval_start.month)
        for row in rows
    ):
        raise ValueError("provider-history v3 selected part month changed")
    if (
        not rows
        or rows[0].interval_start != reference.minimum_interval_start
        or rows[-1].interval_end != reference.maximum_interval_end
        or sha256_json({"observation_sha256": ordered_observation_sha256})
        != reference.ordered_row_sha256
    ):
        raise ValueError("provider-history v3 selected part semantics changed")
    previous: tuple[str, datetime, str] | None = None
    for row in rows:
        current = row_sort_key(row)
        if previous is not None and current <= previous:
            raise ValueError("provider-history v3 part order changed")
        previous = current
    return rows


def _read_manifest(path: Path) -> ProviderHistoryV3Manifest:
    manifest_path = path.resolve()
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FileNotFoundError(
            f"provider-history v3 manifest is not a regular file: {manifest_path}"
        )
    payload = _read_bounded(manifest_path, "provider-history v3 manifest")
    document = _mapping(
        _parse_json(payload, "provider-history v3 manifest"), "provider-history v3 manifest"
    )
    if payload != canonical_json_bytes(cast(Mapping[str, JsonValue], document)):
        raise ValueError("provider-history v3 manifest is not canonical")
    _require_exact_keys(document, MANIFEST_FIELDS, "provider-history v3 manifest")
    if (
        document["contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_V3_CONTRACT
        or document["schema_version"] != PROVIDER_HISTORY_V3_SCHEMA_VERSION
        or document["selector_contract"] != PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT
    ):
        raise ValueError("provider-history v3 manifest contract is unsupported")
    physical = _string(
        document["physical_manifest_sha256"], "provider-history physical manifest identity"
    )
    unsigned = dict(document)
    unsigned.pop("physical_manifest_sha256")
    if physical != sha256_json(unsigned):
        raise ValueError("provider-history v3 physical manifest identity changed")
    stage6 = _mapping(document["stage6"], "Stage 6 parent summary")
    _require_exact_keys(stage6, STAGE6_FIELDS | {"observation_summary"}, "Stage 6 parent summary")
    for field in (
        "result_id",
        "closure_id",
        "verification_id",
        "manifest_sha256",
        "contract_selection_sha256",
        "plan_sha256",
        "runtime_sha256",
    ):
        _require_digest(_string(stage6[field], f"Stage 6 {field}"), f"Stage 6 {field}")
    policy = ProviderHistoricalAvailabilityPolicy.from_json_value(document["availability_policy"])
    if document["availability_policy"] != policy.as_json_value():
        raise ValueError("provider-history v3 availability policy changed")
    dataset = ProviderHistoricalDatasetV3.from_json_value(
        document["dataset"],
        availability_policy=policy,
        stage6_plan_sha256=_string(stage6["plan_sha256"], "Stage 6 plan"),
        stage6_runtime_sha256=_string(stage6["runtime_sha256"], "Stage 6 runtime"),
        stage6_closure_id=_string(stage6["closure_id"], "Stage 6 closure"),
        stage6_verification_id=_string(stage6["verification_id"], "Stage 6 verification"),
        stage6_manifest_sha256=_string(stage6["manifest_sha256"], "Stage 6 manifest"),
    )
    if dataset.stage6_result_id != stage6["result_id"]:
        raise ValueError("provider-history v3 Stage 6 result binding changed")
    raw_parts = document["parts"]
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError("provider-history v3 parts are missing")
    parts: list[ProviderHistoryV3PartReference] = []
    previous: ProviderHistoryV3PartReference | None = None
    total_rows = 0
    for raw_part in raw_parts:
        part = ProviderHistoryV3PartReference.from_json_value(raw_part, previous=previous)
        parts.append(part)
        previous = part
        total_rows += part.row_count
    if total_rows != dataset.row_count:
        raise ValueError("provider-history v3 physical row count changed")
    expected_closure = sha256_json(
        {
            "manifest_identity": sha256_json(
                {
                    key: value
                    for key, value in document.items()
                    if key not in {"closure_id", "physical_manifest_sha256"}
                }
            ),
            "part_bytes": [part.bytes_sha256 for part in parts],
        }
    )
    if document["closure_id"] != expected_closure:
        raise ValueError("provider-history v3 closure identity changed")
    return ProviderHistoryV3Manifest(
        path=manifest_path,
        bytes=payload,
        document=document,
        dataset=dataset,
        stage6=stage6,
        parts=tuple(parts),
    )


def _selected_rows(
    manifest: ProviderHistoryV3Manifest,
    *,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> ProviderHistoryV3Rows:
    all_inputs: list[_PartInput] = []
    offset = 0
    for part in manifest.parts:
        all_inputs.append(
            _PartInput(
                reference=part,
                path=_safe_child(manifest.path.parent, part.path, "provider-history v3 part"),
                global_offset=offset,
            )
        )
        offset += part.row_count
    selected = tuple(all_inputs)
    selection: ProviderHistorySelection | None = None
    supplied = (instrument_ids is not None, interval_start is not None, interval_end is not None)
    if any(supplied):
        if not all(supplied):
            raise ValueError("provider-history v3 pruning requires instruments and both bounds")
        assert (
            instrument_ids is not None and interval_start is not None and interval_end is not None
        )
        requested = tuple(sorted(set(instrument_ids)))
        selected = tuple(
            item
            for item in all_inputs
            if item.reference.instrument_id in requested
            and item.reference.maximum_interval_end > interval_start
            and item.reference.minimum_interval_start < interval_end
        )
        selection = ProviderHistorySelection.create(
            parent_manifest_sha256=sha256_bytes(manifest.bytes),
            parent_dataset_sha256=manifest.dataset.dataset_sha256,
            requested_instrument_ids=requested,
            interval_start=interval_start,
            interval_end=interval_end,
            selected_part_references=tuple(item.reference.as_json_value() for item in selected),
            row_count_upper_bound=sum(item.reference.row_count for item in selected),
        )
    return ProviderHistoryV3Rows(manifest=manifest, selected_parts=selected, selection=selection)


def _source_evidence(
    manifest: ProviderHistoryV3Manifest,
    rows: ProviderHistoryObservationRows,
    request_evidence: tuple[ProviderHistoryRequestEvidence, ...],
    observation_summary: ProviderHistoryObservationSummary | None,
    *,
    stage7_verification_id: str,
) -> ProviderHistorySourceEvidence:
    stage6 = manifest.stage6
    raw_requests = stage6["requests"]
    if not isinstance(raw_requests, list):
        raise TypeError("Stage 6 requests must be a list")
    requests = tuple(
        SimpleNamespace(
            request_sha256=_string(item["request_sha256"], "Stage 6 request identity"),
            instrument_id=_string(item["instrument_id"], "Stage 6 request instrument"),
            kind=IbkrHistoricalRequestKind(_string(item["kind"], "Stage 6 request kind")),
            interval_start=_utc(item["interval_start"], "Stage 6 request start"),
            interval_end=_utc(item["interval_end"], "Stage 6 request end"),
        )
        for item in (_mapping(item, "Stage 6 request") for item in raw_requests)
    )
    plan = _PlanSummary(
        contract_selection_sha256=_string(stage6["contract_selection_sha256"], "Stage 6 selection"),
        runtime_sha256=_string(stage6["runtime_sha256"], "Stage 6 runtime"),
        plan_sha256=_string(stage6["plan_sha256"], "Stage 6 plan"),
        eligible_contracts=_planned_contracts_from_stage6(stage6["eligible_contracts"]),
        requests=requests,
    )
    source_result = _Stage6SourceResultSummary(
        result_id=_string(stage6["result_id"], "Stage 6 result"),
        closure_id=_string(stage6["closure_id"], "Stage 6 closure"),
        coverage_summary=cast(
            dict[str, JsonValue], _mapping(stage6["coverage_summary"], "coverage summary")
        ),
        entitlement_summary=cast(
            dict[str, JsonValue], _mapping(stage6["entitlement_summary"], "entitlement summary")
        ),
    )
    source = _SourceSummary(
        plan=plan,
        source_result=source_result,
        verification_id=_string(stage6["verification_id"], "Stage 6 verification"),
    )
    return ProviderHistorySourceEvidence(
        dataset=manifest.dataset,
        observations=rows,
        source_artifact=cast(Any, source),
        stage7_verification_id=stage7_verification_id,
        request_evidence=request_evidence,
        observation_summary=observation_summary,
        selection=getattr(rows, "selection", None),
    )


def _planned_contracts_from_stage6(value: object) -> tuple[IbkrPlannedContract, ...]:
    raw_contracts = value
    if not isinstance(raw_contracts, list) or not raw_contracts:
        raise TypeError("Stage 6 eligible contracts must be a non-empty list")
    result: list[IbkrPlannedContract] = []
    previous_instrument: str | None = None
    fingerprint_fields = {
        "con_id",
        "symbol",
        "security_type",
        "currency",
        "exchange",
        "primary_exchange",
        "local_symbol",
        "trading_class",
        "multiplier",
        "underlying_con_id",
        "contract_month",
    }
    for raw_contract in raw_contracts:
        contract = _mapping(raw_contract, "Stage 6 eligible contract")
        _require_exact_keys(contract, {"instrument_id", "fingerprint"}, "Stage 6 eligible contract")
        instrument_text = _string(contract["instrument_id"], "Stage 6 eligible contract instrument")
        if previous_instrument is not None and instrument_text <= previous_instrument:
            raise ValueError("Stage 6 eligible contracts are not canonical")
        previous_instrument = instrument_text
        fingerprint = _mapping(contract["fingerprint"], "Stage 6 contract fingerprint")
        _require_exact_keys(fingerprint, fingerprint_fields, "Stage 6 contract fingerprint")
        result.append(
            IbkrPlannedContract(
                instrument_id=InstrumentId(instrument_text),
                fingerprint=IbkrContractFingerprint(
                    con_id=_integer(fingerprint["con_id"], "contract con_id"),
                    symbol=_string(fingerprint["symbol"], "contract symbol"),
                    security_type=_string(fingerprint["security_type"], "contract security_type"),
                    currency=_string(fingerprint["currency"], "contract currency"),
                    exchange=_string(fingerprint["exchange"], "contract exchange"),
                    primary_exchange=_optional_string_value(
                        fingerprint["primary_exchange"], "contract primary_exchange"
                    ),
                    local_symbol=_string(fingerprint["local_symbol"], "contract local_symbol"),
                    trading_class=_optional_string_value(
                        fingerprint["trading_class"], "contract trading_class"
                    ),
                    multiplier=_optional_string_value(
                        fingerprint["multiplier"], "contract multiplier"
                    ),
                    underlying_con_id=_optional_integer_value(
                        fingerprint["underlying_con_id"], "contract underlying_con_id"
                    ),
                    contract_month=_optional_string_value(
                        fingerprint["contract_month"], "contract contract_month"
                    ),
                ),
            )
        )
    return tuple(result)


def _request_evidence_from_stage6(
    stage6: Mapping[str, object],
) -> tuple[ProviderHistoryRequestEvidence, ...]:
    raw = stage6["request_evidence"]
    if not isinstance(raw, list):
        raise TypeError("Stage 6 request evidence must be a list")
    result: list[ProviderHistoryRequestEvidence] = []
    for item in raw:
        value = _mapping(item, "Stage 6 request evidence")
        sessions_raw = value["sessions"]
        if not isinstance(sessions_raw, list):
            raise TypeError("Stage 6 request sessions must be a list")
        disposition = IbkrHistoricalEvidenceDisposition(
            _string(value["evidence_disposition"], "Stage 6 evidence disposition")
        )
        result.append(
            ProviderHistoryRequestEvidence(
                request_sha256=_string(value["request_sha256"], "request identity"),
                result_sha256=_string(value["result_sha256"], "result identity"),
                evidence_disposition=disposition,
                accepted_row_count=_integer(value["accepted_row_count"], "accepted row count"),
                sessions=tuple(
                    cast(dict[str, JsonValue], _mapping(session, "session"))
                    for session in sessions_raw
                ),
            )
        )
    return tuple(result)


def _observation_summary_from_document(value: object) -> ProviderHistoryObservationSummary | None:
    if value is None:
        return None
    summary = _mapping(value, "observation summary")
    raw = summary["accepted_intervals_by_request"]
    if not isinstance(raw, list):
        raise TypeError("observation summary intervals must be a list")
    entries: list[tuple[str, tuple[tuple[datetime, datetime], ...]]] = []
    for item in raw:
        entry = _mapping(item, "observation summary entry")
        raw_intervals = entry["intervals"]
        if not isinstance(raw_intervals, list):
            raise TypeError("observation summary intervals must be a list")
        entries.append(
            (
                _string(entry["request_sha256"], "observation summary request"),
                tuple(
                    (_utc(interval[0], "interval start"), _utc(interval[1], "interval end"))
                    for interval in raw_intervals
                    if isinstance(interval, list) and len(interval) == 2
                ),
            )
        )
    return ProviderHistoryObservationSummary(
        accepted_intervals_by_request=tuple(entries),
        source_start=_utc(summary["source_start"], "source start"),
        source_end=_utc(summary["source_end"], "source end"),
    )


def _receipt_document(manifest: ProviderHistoryV3Manifest) -> dict[str, JsonValue]:
    source_summary = {
        "stage6_result_id": manifest.dataset.stage6_result_id,
        "stage6_verification_id": manifest.dataset.stage6_verification_id,
        "eligible_contracts": manifest.stage6["eligible_contracts"],
        "availability_policy": manifest.dataset.availability_policy.as_json_value(),
        "observation_summary": manifest.stage6["observation_summary"],
    }
    identity: dict[str, JsonValue] = {
        "contract": V3_VERIFICATION_CONTRACT,
        "schema_version": PROVIDER_HISTORY_V3_SCHEMA_VERSION,
        "provider_history_contract": PROVIDER_HISTORICAL_OBSERVATIONS_V3_CONTRACT,
        "provider_history_schema_version": PROVIDER_HISTORY_V3_SCHEMA_VERSION,
        "provider_history_manifest_sha256": sha256_bytes(manifest.bytes),
        "provider_history_dataset_sha256": manifest.dataset.dataset_sha256,
        "closure_id": _string(manifest.document["closure_id"], "Stage 7 closure"),
        "stage6_result_id": manifest.dataset.stage6_result_id,
        "stage6_closure_id": manifest.dataset.stage6_closure_id,
        "stage6_verification_id": manifest.dataset.stage6_verification_id,
        "availability_policy": manifest.dataset.availability_policy.as_json_value(),
        "source_summary_sha256": sha256_json(source_summary),
        "verifier_contract": V3_VERIFIER_CONTRACT,
        "verifier_version": V3_VERIFIER_VERSION,
        "verifier_identity": provider_history_v3_verifier_sha256(),
        "completed_checks": list(V3_COMPLETED_CHECKS),
    }
    identity["verification_id"] = sha256_json(identity)
    return identity


def _read_receipt(path: Path) -> dict[str, object]:
    payload = _read_bounded(path, "provider-history v3 receipt")
    document = _mapping(
        _parse_json(payload, "provider-history v3 receipt"), "provider-history v3 receipt"
    )
    if payload != canonical_json_bytes(cast(Mapping[str, JsonValue], document)):
        raise ValueError("provider-history v3 receipt is not canonical")
    _require_exact_keys(document, RECEIPT_FIELDS, "provider-history v3 receipt")
    verification_id = _string(document["verification_id"], "provider-history verification identity")
    unsigned = dict(document)
    unsigned.pop("verification_id")
    if verification_id != sha256_json(unsigned):
        raise ValueError("provider-history v3 receipt identity changed")
    return document


def _validate_receipt(manifest: ProviderHistoryV3Manifest, receipt: Mapping[str, object]) -> None:
    expected = _receipt_document(manifest)
    for field in (
        "contract",
        "schema_version",
        "provider_history_contract",
        "provider_history_schema_version",
        "provider_history_manifest_sha256",
        "provider_history_dataset_sha256",
        "closure_id",
        "stage6_result_id",
        "stage6_closure_id",
        "stage6_verification_id",
        "availability_policy",
        "source_summary_sha256",
        "verifier_contract",
        "verifier_version",
        "verifier_identity",
        "completed_checks",
    ):
        if receipt[field] != expected[field]:
            raise ValueError(f"provider-history v3 receipt {field} changed")
    if receipt["verification_id"] != sha256_json(
        {key: value for key, value in receipt.items() if key != "verification_id"}
    ):
        raise ValueError("provider-history v3 receipt identity changed")


def _publish(
    output: Path, manifest: ProviderHistoryV3Manifest, payloads: Mapping[str, bytes]
) -> None:
    destination = _validate_output_path(output)
    if destination.exists():
        raise FileExistsError(f"provider-history v3 output already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"provider-history v3 output parent does not exist: {destination.parent}"
        )
    destination.mkdir()
    for path, payload in payloads.items():
        _write_create_only(_safe_child(destination, path, "provider-history part"), payload)
    _write_create_only(destination / MANIFEST_NAME, manifest.bytes)


def _preflight_receipt(path: Path, manifest: Path) -> Path:
    result = _validate_output_path(path)
    if result.exists():
        raise FileExistsError(f"provider-history receipt already exists: {result}")
    if result.is_relative_to(manifest.resolve().parent):
        raise ValueError("provider-history receipt cannot be written inside the Stage 7 closure")
    if not result.parent.is_dir():
        raise FileNotFoundError(f"provider-history receipt parent does not exist: {result.parent}")
    return result


def _require_exact_tree(root: Path, expected: set[str]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("provider-history v3 closure root is not a regular directory")
    rooted = root.absolute()
    if rooted.resolve() != rooted:
        raise ValueError("provider-history v3 closure root escapes its path")
    allowed_directories = {""}
    for relative in expected:
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or relative_path.as_posix() != relative
            or any(part in {"", ".", ".."} for part in relative_path.parts)
        ):
            raise ValueError("provider-history v3 closure path is not canonical")
        allowed_directories.update(
            parent.as_posix() for parent in relative_path.parents if parent != Path(".")
        )
        candidate = (root / relative_path).absolute()
        if candidate.resolve(strict=False) != candidate:
            raise ValueError("provider-history v3 closure path escapes its root")
    actual_files: set[str] = set()
    actual_directories: set[str] = {""}
    for item in root.rglob("*"):
        if item.is_symlink():
            raise ValueError("provider-history v3 closure tree contains a symlink")
        if item.is_dir():
            actual_directories.add(item.relative_to(root).as_posix())
            continue
        if not item.is_file():
            raise ValueError("provider-history v3 closure tree contains unsupported entry")
        actual_files.add(item.relative_to(root).as_posix())
    if actual_files != expected or actual_directories != allowed_directories:
        raise ValueError("provider-history v3 closure tree changed")


def _validate_output_path(output: Path) -> Path:
    if any(part == ".." for part in output.parts):
        raise ValueError("provider-history v3 output path is not canonical")
    candidate = output.absolute()
    if candidate.is_symlink() or candidate.resolve(strict=False) != candidate:
        raise ValueError("provider-history v3 output path escapes its root")
    return candidate


def _safe_child(root: Path, relative: str, field: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate.parent != root.resolve() and not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"{field} path escapes its closure")
    if any(part == ".." for part in Path(relative).parts):
        raise ValueError(f"{field} path is not canonical")
    if candidate.is_symlink():
        raise ValueError(f"{field} cannot be a symlink")
    return candidate


def _write_create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(payload)


def _read_bounded(path: Path, field: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"{field} is not a regular file: {path}")
    payload = path.read_bytes()
    if not payload or len(payload) > MAX_FILE_BYTES:
        raise ValueError(f"{field} exceeds its byte bound")
    return payload


def _parse_json(payload: bytes, field: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field} is not valid JSON") from error


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return dict(cast(Mapping[str, object], value))


def _require_exact_keys(value: Mapping[str, object], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} fields are not exact")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def _optional_string_value(value: object, field: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise TypeError(f"{field} must be a non-empty string when present")
    return value if value is None else cast(str, value)


def _optional_integer_value(value: object, field: str) -> int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise TypeError(f"{field} must be an integer when present")
    return value if value is None else cast(int, value)


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _require_digest(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} is not an ISO timestamp") from error
    if result.tzinfo != UTC:
        raise ValueError(f"{field} must be UTC")
    return result


def _decimal_text(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("provider-history optional count must be an integer")
    return value


def _request_evidence_json(item: ProviderHistoryRequestEvidence) -> dict[str, JsonValue]:
    return {
        "request_sha256": item.request_sha256,
        "result_sha256": item.result_sha256,
        "evidence_disposition": item.evidence_disposition.value,
        "accepted_row_count": item.accepted_row_count,
        "sessions": [dict(session) for session in item.sessions],
    }


def _stage6_verification_id(path: Path) -> str:
    payload = _read_bounded(path, "Stage 6 verification receipt")
    document = _mapping(
        _parse_json(payload, "Stage 6 verification receipt"),
        "Stage 6 verification receipt",
    )
    return _string(document["verification_id"], "Stage 6 verification identity")


def _part_path(instrument_id: str, year: int, month: int, ordinal: int) -> str:
    instrument_digest = sha256(instrument_id.encode("utf-8")).hexdigest()
    return (
        f"observations/instrument-{instrument_digest}/month-{year:04d}-{month:02d}/"
        f"part-{ordinal:04d}.parquet"
    )
