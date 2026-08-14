"""Provider-history v2 verification and pruned reads."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

import polars as pl

from qtrad.application.provider_history import (
    ProviderHistorySelection,
    ProviderHistorySourceEvidence,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.provider_history import (
    PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT,
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalDataset,
    ProviderHistoricalObservation,
    ProviderHistoricalPartition,
    ProviderHistoricalPartitionReference,
    row_sort_key,
    sha256_json,
    utc_text,
)
from qtrad.runtime.ibkr_results import (
    IbkrHistoricalResultStream,
    _read_ibkr_historical_result_header,
)
from qtrad.runtime.provider_history import (
    _MANIFEST_NAME,
    _OBSERVATION_FIELDS,
    _SOURCE_DIRECTORY,
    _SOURCE_VERIFICATION_RECEIPT_CONTRACT,
    _dataset_from_manifest,
    _int,
    _mapping,
    _ObservationSummaryBuilder,
    _parquet_footer_row_count,
    _parse_json,
    _read_bounded,
    _require_digest,
    _require_exact_keys,
    _require_exact_tree,
    _require_file,
    _safe_child,
    _sha256_json,
    _source_file_digests,
    _source_files,
    _source_reference,
    _string,
    _write_create_only,
    canonical_json_bytes,
    provider_history_source_verification_receipt,
    read_provider_history_source_verification_receipt,
    sha256_bytes,
)

PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT = "qtrad-provider-historical-observations-v2"
PROVIDER_HISTORY_V2_SCHEMA_VERSION = 2
PROVIDER_HISTORY_V2_VERIFICATION_CONTRACT = "qtrad-provider-history-verification-v2"
PROVIDER_HISTORY_V2_VERIFIER_CONTRACT = "qtrad-provider-history-semantic-verifier-v2"
PROVIDER_HISTORY_V2_VERIFIER_VERSION = 1
PROVIDER_HISTORY_V2_COMPLETED_CHECKS = (
    "manifest-and-closure-bytes",
    "accepted-v1-receipt-binding",
    "availability-policy-binding",
    "physical-part-semantic-replay",
    "request-evidence-summary",
    "observation-interval-summary",
)
_MIGRATION_CONTRACT = "qtrad-provider-history-v1-to-v2-repack-v1"
_MIGRATION_RECEIPT_PATH = "migration-source/provider-history-verification-receipt.json"
_TARGET_PART_BYTES = 32 * 1024 * 1024
_MAX_PART_ROWS = 50_000
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024
_V2_MANIFEST_FIELDS = {
    "contract",
    "schema_version",
    "selector_contract",
    "dataset",
    "availability_policy",
    "source_result",
    "source_plan_row_bound",
    "migration_source",
    "parts",
    "physical_manifest_sha256",
}
_V2_PART_FIELDS = {
    "instrument_id",
    "minimum_interval_start",
    "maximum_interval_end",
    "row_count",
    "ordered_row_sha256",
    "bytes_sha256",
    "path",
    "part_ordinal",
}
_MIGRATION_FIELDS = {
    "contract",
    "source_provider_history_contract",
    "source_provider_history_manifest_sha256",
    "source_verification_receipt_path",
    "source_verification_receipt_sha256",
}
_V2_RECEIPT_FIELDS = {
    "contract",
    "schema_version",
    "provider_history_contract",
    "provider_history_schema_version",
    "provider_history_manifest_sha256",
    "provider_history_dataset_sha256",
    "stage6_plan_sha256",
    "stage6_runtime_sha256",
    "stage6_aggregate_sha256",
    "stage6_result_manifest_sha256",
    "availability_policy",
    "migration_source",
    "request_evidence",
    "observation_summary",
    "verifier_contract",
    "verifier_version",
    "verifier_identity",
    "completed_checks",
    "receipt_sha256",
}


@dataclass(frozen=True, slots=True)
class ProviderHistoryV2PartReference:
    instrument_id: str
    minimum_interval_start: datetime
    maximum_interval_end: datetime
    row_count: int
    ordered_row_sha256: str
    bytes_sha256: str
    path: str
    part_ordinal: int

    @classmethod
    def from_json_value(
        cls, value: object, *, previous: ProviderHistoryV2PartReference | None = None
    ) -> ProviderHistoryV2PartReference:
        item = _mapping(value, "provider-history v2 part")
        _require_exact_keys(item, _V2_PART_FIELDS, "provider-history v2 part")
        instrument_id = _string(item["instrument_id"], "provider-history v2 instrument")
        minimum = _utc(item["minimum_interval_start"], "provider-history v2 minimum")
        maximum = _utc(item["maximum_interval_end"], "provider-history v2 maximum")
        row_count = _int(item["row_count"], "provider-history v2 part rows")
        ordinal = _int(item["part_ordinal"], "provider-history v2 part ordinal")
        ordered_row_sha256 = _string(
            item["ordered_row_sha256"], "provider-history v2 ordered-row identity"
        )
        bytes_sha256 = _string(item["bytes_sha256"], "provider-history v2 byte identity")
        _require_digest(ordered_row_sha256, "provider-history v2 ordered-row identity")
        _require_digest(bytes_sha256, "provider-history v2 byte identity")
        path = _string(item["path"], "provider-history v2 part path")
        if (
            not instrument_id
            or instrument_id != instrument_id.lower()
            or ":" not in instrument_id
            or maximum <= minimum
            or row_count <= 0
            or row_count > _MAX_PART_ROWS
            or ordinal <= 0
        ):
            raise ValueError("provider-history v2 part metadata is invalid")
        expected_path = _v2_part_path(
            instrument_id,
            year=minimum.year,
            month=minimum.month,
            ordinal=ordinal,
        )
        if path != expected_path:
            raise ValueError("provider-history v2 part path is not canonical")
        result = cls(
            instrument_id=instrument_id,
            minimum_interval_start=minimum,
            maximum_interval_end=maximum,
            row_count=row_count,
            ordered_row_sha256=ordered_row_sha256,
            bytes_sha256=bytes_sha256,
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
                raise ValueError("provider-history v2 parts are not canonical")
            if key[:3] == previous_key[:3] and ordinal != previous.part_ordinal + 1:
                raise ValueError("provider-history v2 split ordinals are not contiguous")
            if key[:3] != previous_key[:3] and ordinal != 1:
                raise ValueError("provider-history v2 split ordinals must restart at one")
        elif ordinal != 1:
            raise ValueError("provider-history v2 first split ordinal must be one")
        return result

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


@dataclass(frozen=True, slots=True)
class _V2Manifest:
    path: Path
    bytes: bytes
    document: dict[str, object]
    dataset: ProviderHistoricalDataset
    source_reference: dict[str, object]
    migration_source: dict[str, object]
    parts: tuple[ProviderHistoryV2PartReference, ...]


@dataclass(frozen=True, slots=True)
class _V2PartInput:
    reference: ProviderHistoryV2PartReference
    path: Path
    global_offset: int


class VerifiedProviderHistoryV2Rows:
    """Re-iterable v2 rows with authenticated pruning and parent positions."""

    def __init__(
        self,
        *,
        manifest: _V2Manifest,
        selected_parts: tuple[_V2PartInput, ...],
        selection: ProviderHistorySelection | None,
    ) -> None:
        self._manifest = manifest
        self._selected_parts = selected_parts
        self.selection = selection
        self.instruments = tuple(sorted({part.reference.instrument_id for part in selected_parts}))

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
            if part.reference.instrument_id != instrument_id:
                continue
            yield from self._iter_part_with_positions(part)

    def _iter_part_with_positions(
        self, part: _V2PartInput
    ) -> Iterator[tuple[ProviderHistoricalObservation, int]]:
        rows = _read_v2_part(part.path, part.reference)
        for index, row in enumerate(rows, part.global_offset + 1):
            if self.selection is not None and (
                row.instrument_id not in self.selection.requested_instrument_ids
                or row.interval_start < self.selection.interval_start
                or row.interval_end > self.selection.interval_end
            ):
                continue
            yield row, index


def provider_history_v2_verifier_sha256() -> str:
    return sha256_json(
        {
            "contract": PROVIDER_HISTORY_V2_VERIFIER_CONTRACT,
            "version": PROVIDER_HISTORY_V2_VERIFIER_VERSION,
            "completed_checks": list(PROVIDER_HISTORY_V2_COMPLETED_CHECKS),
        }
    )


def verify_provider_history_v2(
    path: Path,
    *,
    receipt_output: Path | None = None,
) -> ProviderHistorySourceEvidence:
    """Independently decode v2 physical parts and reconstruct semantic identity."""

    receipt_path: Path | None = None
    if receipt_output is not None:
        from qtrad.runtime.provider_history import (
            preflight_provider_history_verification_receipt,
        )

        receipt_path = preflight_provider_history_verification_receipt(
            receipt_output,
            immutable_roots=(path.resolve().parent,),
        )

    manifest = verify_provider_history_v2_file_only(path)
    rows = _selected_provider_history_v2_rows(manifest, manifest.dataset)
    source_stream, _ = _read_provider_history_v2_source_header(manifest)
    embedded_receipt = _read_embedded_source_receipt(manifest)
    compact = {
        "contract": _SOURCE_VERIFICATION_RECEIPT_CONTRACT,
        "provider_verifier_sha256": embedded_receipt["verifier_identity"],
        "dataset_sha256": embedded_receipt["provider_history_dataset_sha256"],
        "request_evidence": embedded_receipt["request_evidence"],
        "observation_summary": embedded_receipt["observation_summary"],
    }
    restored = read_provider_history_source_verification_receipt(
        manifest.path,
        cast(dict[str, JsonValue], compact),
        _verified_dataset=manifest.dataset,
        _verified_observations=rows,
        _verified_source_artifact=source_stream,
    )
    summary = _ObservationSummaryBuilder()
    partitions: list[ProviderHistoricalPartitionReference] = []
    current_key: tuple[str, object] | None = None
    current_rows: list[ProviderHistoricalObservation] = []
    previous_row_key: tuple[str, datetime, str] | None = None

    def finish_partition() -> None:
        if not current_rows:
            return
        partition = ProviderHistoricalPartition.create(rows=tuple(current_rows))
        partitions.append(ProviderHistoricalPartitionReference.from_partition(partition))
        summary.add(partition.rows)

    for row in rows:
        key = row_sort_key(row)
        if previous_row_key is not None and key <= previous_row_key:
            raise ValueError("provider-history v2 observations are not canonical")
        previous_row_key = key
        partition_key = (row.instrument_id, row.interval_start.date())
        if current_key is not None and partition_key != current_key:
            finish_partition()
            current_rows = []
        current_key = partition_key
        current_rows.append(row)
    finish_partition()

    replayed = ProviderHistoricalDataset.create(
        partitions=tuple(partitions),
        contract_selection_sha256=manifest.dataset.contract_selection_sha256,
        plan_sha256=manifest.dataset.plan_sha256,
        runtime_sha256=manifest.dataset.runtime_sha256,
        aggregate_sha256=manifest.dataset.aggregate_sha256,
        availability_policy=manifest.dataset.availability_policy,
    )
    if replayed.dataset_sha256 != manifest.dataset.dataset_sha256:
        raise ValueError("provider-history v2 semantic dataset identity changed")
    observation_summary = summary.finish()
    if observation_summary != restored.observation_summary:
        raise ValueError("provider-history v2 observation summary changed")
    evidence = ProviderHistorySourceEvidence(
        dataset=manifest.dataset,
        observations=rows,
        source_artifact=restored.source_artifact,
        request_evidence=restored.request_evidence,
        observation_summary=observation_summary,
    )
    if receipt_path is not None:
        encoded = canonical_json_bytes(
            provider_history_v2_verification_receipt(manifest.path, evidence)
        )
        if len(encoded) > _MAX_RECEIPT_BYTES:
            raise ValueError("provider-history v2 receipt exceeds its byte bound")
        _write_create_only(receipt_path, encoded)
    return evidence


def authenticate_provider_history_v2(
    path: Path,
    *,
    receipt: Path,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> ProviderHistorySourceEvidence:
    """Cheaply authenticate v2 and optionally restore one pruned row selection."""

    manifest = _read_provider_history_v2_manifest(path)
    receipt_path = _require_file(receipt, "provider-history v2 verification receipt")
    receipt_bytes = _read_bounded(receipt_path, "provider-history v2 verification receipt")
    if len(receipt_bytes) > _MAX_RECEIPT_BYTES:
        raise ValueError("provider-history v2 receipt exceeds its byte bound")
    receipt_document = _mapping(
        _parse_json(receipt_bytes, "provider-history v2 verification receipt"),
        "provider-history v2 verification receipt",
    )
    if canonical_json_bytes(cast(dict[str, JsonValue], receipt_document)) != receipt_bytes:
        raise ValueError("provider-history v2 verification receipt is not canonical")
    _require_exact_keys(
        receipt_document, _V2_RECEIPT_FIELDS, "provider-history v2 verification receipt"
    )
    unsigned = dict(receipt_document)
    receipt_sha256 = _string(unsigned.pop("receipt_sha256"), "provider-history v2 receipt identity")
    _require_digest(receipt_sha256, "provider-history v2 receipt identity")
    if receipt_sha256 != _sha256_json(cast(dict[str, JsonValue], unsigned)):
        raise ValueError("provider-history v2 receipt identity changed")
    expected = _v2_receipt_bindings(manifest)
    for field, value in expected.items():
        if receipt_document[field] != value:
            raise ValueError(f"provider-history v2 receipt {field} changed")
    if (
        receipt_document["verifier_contract"] != PROVIDER_HISTORY_V2_VERIFIER_CONTRACT
        or receipt_document["verifier_version"] != PROVIDER_HISTORY_V2_VERIFIER_VERSION
        or receipt_document["verifier_identity"] != provider_history_v2_verifier_sha256()
        or receipt_document["completed_checks"] != list(PROVIDER_HISTORY_V2_COMPLETED_CHECKS)
    ):
        raise ValueError("provider-history v2 receipt verifier changed")

    compact = {
        "contract": _SOURCE_VERIFICATION_RECEIPT_CONTRACT,
        "provider_verifier_sha256": receipt_document["verifier_identity"],
        "dataset_sha256": receipt_document["provider_history_dataset_sha256"],
        "request_evidence": receipt_document["request_evidence"],
        "observation_summary": receipt_document["observation_summary"],
    }
    rows = _selected_provider_history_v2_rows(
        manifest,
        manifest.dataset,
        instrument_ids=instrument_ids,
        interval_start=interval_start,
        interval_end=interval_end,
    )
    if rows.selection is not None:
        for part in rows._selected_parts:
            _verified_v2_part_bytes(part.path, part.reference)
    source_stream, _ = _read_provider_history_v2_source_header(manifest)
    return read_provider_history_source_verification_receipt(
        manifest.path,
        cast(dict[str, JsonValue], compact),
        _verified_dataset=manifest.dataset,
        _verified_observations=rows,
        _verified_source_artifact=source_stream,
        _selection=rows.selection,
    )


def provider_history_v2_verification_receipt(
    path: Path,
    evidence: ProviderHistorySourceEvidence,
) -> dict[str, JsonValue]:
    manifest = verify_provider_history_v2_file_only(path)
    compact = provider_history_source_verification_receipt(evidence)
    identity: dict[str, JsonValue] = {
        **_v2_receipt_bindings(manifest),
        "request_evidence": compact["request_evidence"],
        "observation_summary": compact["observation_summary"],
        "verifier_contract": PROVIDER_HISTORY_V2_VERIFIER_CONTRACT,
        "verifier_version": PROVIDER_HISTORY_V2_VERIFIER_VERSION,
        "verifier_identity": provider_history_v2_verifier_sha256(),
        "completed_checks": list(PROVIDER_HISTORY_V2_COMPLETED_CHECKS),
    }
    return {**identity, "receipt_sha256": _sha256_json(identity)}


def _read_provider_history_v2_manifest(path: Path) -> _V2Manifest:
    """Authenticate canonical v2 metadata without reading closure children."""

    manifest_path = _require_file(path, "provider-history v2 manifest")
    manifest_bytes = _read_bounded(manifest_path, "provider-history v2 manifest")
    document = _mapping(
        _parse_json(manifest_bytes, "provider-history v2 manifest"),
        "provider-history v2 manifest",
    )
    if canonical_json_bytes(cast(dict[str, JsonValue], document)) != manifest_bytes:
        raise ValueError("provider-history v2 manifest is not canonical")
    _require_exact_keys(document, _V2_MANIFEST_FIELDS, "provider-history v2 manifest")
    if (
        document["contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT
        or document["schema_version"] != PROVIDER_HISTORY_V2_SCHEMA_VERSION
        or document["selector_contract"] != PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT
    ):
        raise ValueError("provider-history v2 manifest contract is unsupported")
    identity = dict(document)
    physical_manifest_sha256 = _string(
        identity.pop("physical_manifest_sha256"),
        "provider-history v2 physical manifest identity",
    )
    _require_digest(physical_manifest_sha256, "provider-history v2 physical manifest identity")
    if physical_manifest_sha256 != _sha256_json(cast(dict[str, JsonValue], identity)):
        raise ValueError("provider-history v2 physical manifest identity changed")
    policy = ProviderHistoricalAvailabilityPolicy.from_json_value(document["availability_policy"])
    dataset = _dataset_from_manifest(
        _mapping(document["dataset"], "provider-history v2 dataset"), policy
    )
    if document["availability_policy"] != dataset.availability_policy.as_json_value():
        raise ValueError("provider-history v2 availability policy changed")
    source_reference = _source_reference(document["source_result"])
    migration = _migration_source(document["migration_source"])
    raw_parts = document["parts"]
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError("provider-history v2 parts are missing")
    parts: list[ProviderHistoryV2PartReference] = []
    previous: ProviderHistoryV2PartReference | None = None
    total_rows = 0
    for raw_part in raw_parts:
        part = ProviderHistoryV2PartReference.from_json_value(raw_part, previous=previous)
        previous = part
        total_rows += part.row_count
        parts.append(part)
    if total_rows != dataset.row_count:
        raise ValueError("provider-history v2 physical row count changed")
    return _V2Manifest(
        path=manifest_path,
        bytes=manifest_bytes,
        document=document,
        dataset=dataset,
        source_reference=source_reference,
        migration_source=migration,
        parts=tuple(parts),
    )


def _read_provider_history_v2_source_header(
    manifest: _V2Manifest,
) -> tuple[IbkrHistoricalResultStream, bytes]:
    source_manifest = _safe_child(
        manifest.path.parent,
        _string(manifest.source_reference["path"], "provider-history v2 source manifest path"),
        "provider-history v2 source manifest",
    )
    source_manifest_bytes = _read_bounded(source_manifest, "provider-history v2 source manifest")
    if sha256_bytes(source_manifest_bytes) != manifest.source_reference["bytes_sha256"]:
        raise ValueError("provider-history v2 source manifest bytes changed")
    source_stream = _read_ibkr_historical_result_header(source_manifest)
    if (
        source_stream.plan.contract_selection_sha256 != manifest.dataset.contract_selection_sha256
        or source_stream.plan.plan_sha256 != manifest.dataset.plan_sha256
        or source_stream.plan.runtime_sha256 != manifest.dataset.runtime_sha256
        or source_stream.aggregate.aggregate_sha256 != manifest.dataset.aggregate_sha256
    ):
        raise ValueError("provider-history v2 source identities changed")
    return source_stream, source_manifest_bytes


def replay_provider_history_v2_stage6(path: Path) -> None:
    """Semantically replay the embedded Stage 6 source for confirmatory promotion."""

    manifest = _read_provider_history_v2_manifest(path)
    source_stream, _ = _read_provider_history_v2_source_header(manifest)
    for _ in source_stream.iter_request_results():
        pass


def verify_provider_history_v2_file_only(path: Path) -> _V2Manifest:
    """Audit every byte in the complete v2 closure without semantic row replay."""

    manifest = _read_provider_history_v2_manifest(path)
    source_stream, source_manifest_bytes = _read_provider_history_v2_source_header(manifest)
    source_root = source_stream.source_root
    source_files = _source_files(source_root)
    source_digests = _source_file_digests(source_stream, source_manifest_bytes)
    if source_files != set(source_digests):
        raise ValueError("provider-history v2 source closure changed")
    for relative, expected_sha256 in source_digests.items():
        child = _safe_child(source_root, relative, "provider-history v2 source child")
        if (
            sha256_bytes(_read_bounded(child, "provider-history v2 source child"))
            != expected_sha256
        ):
            raise ValueError("provider-history v2 source child bytes changed")

    embedded_receipt = _safe_child(
        manifest.path.parent,
        _string(
            manifest.migration_source["source_verification_receipt_path"],
            "provider-history v2 migration receipt path",
        ),
        "provider-history v2 migration receipt",
    )
    if (
        sha256_bytes(_read_bounded(embedded_receipt, "provider-history v2 migration receipt"))
        != manifest.migration_source["source_verification_receipt_sha256"]
    ):
        raise ValueError("provider-history v2 migration receipt bytes changed")

    expected_tree = {_MANIFEST_NAME, _MIGRATION_RECEIPT_PATH}
    for part in manifest.parts:
        child = _safe_child(manifest.path.parent, part.path, "provider-history v2 physical part")
        _verified_v2_part_bytes(child, part)
        frame = pl.read_parquet_schema(child)
        if tuple(frame) != _OBSERVATION_FIELDS:
            raise ValueError("provider-history v2 physical part schema changed")
        if _parquet_footer_row_count(child) != part.row_count:
            raise ValueError("provider-history v2 physical part row count changed")
        expected_tree.add(part.path)
    expected_tree.update(f"{_SOURCE_DIRECTORY}/{item}" for item in source_files)
    _require_exact_tree(manifest.path.parent, expected_tree)
    return manifest


def provider_history_v2_rows(
    path: Path,
    dataset: ProviderHistoricalDataset,
    *,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> VerifiedProviderHistoryV2Rows:
    manifest = verify_provider_history_v2_file_only(path)
    return _selected_provider_history_v2_rows(
        manifest,
        dataset,
        instrument_ids=instrument_ids,
        interval_start=interval_start,
        interval_end=interval_end,
    )


def _selected_provider_history_v2_rows(
    manifest: _V2Manifest,
    dataset: ProviderHistoricalDataset,
    *,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> VerifiedProviderHistoryV2Rows:
    if manifest.dataset.dataset_sha256 != dataset.dataset_sha256:
        raise ValueError("provider-history v2 dataset identity changed")
    all_inputs: list[_V2PartInput] = []
    offset = 0
    for part in manifest.parts:
        all_inputs.append(
            _V2PartInput(
                reference=part,
                path=_safe_child(
                    manifest.path.parent,
                    part.path,
                    "provider-history v2 physical part",
                ),
                global_offset=offset,
            )
        )
        offset += part.row_count
    selection: ProviderHistorySelection | None = None
    selected_inputs = tuple(all_inputs)
    supplied = (instrument_ids is not None, interval_start is not None, interval_end is not None)
    if any(supplied):
        if not all(supplied):
            raise ValueError("provider-history v2 pruning requires instruments and both bounds")
        assert instrument_ids is not None
        assert interval_start is not None
        assert interval_end is not None
        requested = tuple(sorted(set(instrument_ids)))
        selected_inputs = tuple(
            item
            for item in all_inputs
            if item.reference.instrument_id in requested
            and item.reference.maximum_interval_end > interval_start
            and item.reference.minimum_interval_start < interval_end
        )
        selection = ProviderHistorySelection.create(
            parent_manifest_sha256=sha256_bytes(manifest.bytes),
            parent_dataset_sha256=dataset.dataset_sha256,
            requested_instrument_ids=requested,
            interval_start=interval_start,
            interval_end=interval_end,
            selected_part_references=tuple(
                item.reference.as_json_value() for item in selected_inputs
            ),
            row_count_upper_bound=sum(item.reference.row_count for item in selected_inputs),
        )
    return VerifiedProviderHistoryV2Rows(
        manifest=manifest,
        selected_parts=selected_inputs,
        selection=selection,
    )


def _v2_part_path(instrument_id: str, year: int, month: int, ordinal: int) -> str:
    digest = sha256(instrument_id.encode()).hexdigest()
    return (
        f"observations/instrument-{digest}/month-{year:04d}-{month:02d}/part-{ordinal:04d}.parquet"
    )


def _verified_v2_part_bytes(path: Path, reference: ProviderHistoryV2PartReference) -> bytes:
    payload = _read_bounded(path, "provider-history v2 physical part")
    if len(payload) > _TARGET_PART_BYTES:
        raise ValueError("provider-history v2 physical part exceeds its byte bound")
    if sha256_bytes(payload) != reference.bytes_sha256:
        raise ValueError("provider-history v2 physical part bytes changed")
    return payload


def _read_v2_part(
    path: Path,
    reference: ProviderHistoryV2PartReference,
) -> tuple[ProviderHistoricalObservation, ...]:
    payload = _verified_v2_part_bytes(path, reference)
    frame = pl.read_parquet(io.BytesIO(payload))
    if tuple(frame.schema) != _OBSERVATION_FIELDS or frame.height != reference.row_count:
        raise ValueError("provider-history v2 selected part shape changed")
    observed: list[ProviderHistoricalObservation] = []
    for raw in frame.to_dicts():
        row = dict(raw)
        schedule = row["schedule_evidence"]
        if not isinstance(schedule, str):
            raise ValueError("provider-history v2 schedule evidence is not canonical JSON")
        row["schedule_evidence"] = json.loads(schedule)
        observed.append(ProviderHistoricalObservation.from_json_value(row))
    rows = tuple(observed)
    if (
        not rows
        or any(row.instrument_id != reference.instrument_id for row in rows)
        or rows[0].interval_start != reference.minimum_interval_start
        or rows[-1].interval_end != reference.maximum_interval_end
        or sha256_json({"observation_sha256": [row.observation_sha256 for row in rows]})
        != reference.ordered_row_sha256
    ):
        raise ValueError("provider-history v2 selected part semantics changed")
    previous: tuple[str, datetime, str] | None = None
    for row in rows:
        key = row_sort_key(row)
        if previous is not None and key <= previous:
            raise ValueError("provider-history v2 selected part order changed")
        previous = key
    return rows


def _read_embedded_source_receipt(manifest: _V2Manifest) -> dict[str, object]:
    path = _safe_child(
        manifest.path.parent,
        _string(
            manifest.migration_source["source_verification_receipt_path"],
            "provider-history v2 migration receipt path",
        ),
        "provider-history v2 migration receipt",
    )
    payload = _read_bounded(path, "provider-history v2 migration receipt")
    document = _mapping(
        _parse_json(payload, "provider-history v2 migration receipt"),
        "provider-history v2 migration receipt",
    )
    if canonical_json_bytes(cast(dict[str, JsonValue], document)) != payload:
        raise ValueError("provider-history v2 migration receipt is not canonical")
    return document


def _migration_source(value: object) -> dict[str, object]:
    migration = _mapping(value, "provider-history v2 migration source")
    _require_exact_keys(migration, _MIGRATION_FIELDS, "provider-history v2 migration source")
    if (
        migration["contract"] != _MIGRATION_CONTRACT
        or migration["source_provider_history_contract"]
        != "qtrad-provider-historical-observations-v1"
        or migration["source_verification_receipt_path"] != _MIGRATION_RECEIPT_PATH
    ):
        raise ValueError("provider-history v2 migration source is unsupported")
    for field in (
        "source_provider_history_manifest_sha256",
        "source_verification_receipt_sha256",
    ):
        _require_digest(
            _string(migration[field], f"provider-history v2 migration {field}"),
            f"provider-history v2 migration {field}",
        )
    return migration


def _v2_receipt_bindings(manifest: _V2Manifest) -> dict[str, JsonValue]:
    return {
        "contract": PROVIDER_HISTORY_V2_VERIFICATION_CONTRACT,
        "schema_version": PROVIDER_HISTORY_V2_SCHEMA_VERSION,
        "provider_history_contract": PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT,
        "provider_history_schema_version": PROVIDER_HISTORY_V2_SCHEMA_VERSION,
        "provider_history_manifest_sha256": sha256_bytes(manifest.bytes),
        "provider_history_dataset_sha256": manifest.dataset.dataset_sha256,
        "stage6_plan_sha256": manifest.dataset.plan_sha256,
        "stage6_runtime_sha256": manifest.dataset.runtime_sha256,
        "stage6_aggregate_sha256": manifest.dataset.aggregate_sha256,
        "stage6_result_manifest_sha256": cast(JsonValue, manifest.source_reference["bytes_sha256"]),
        "availability_policy": manifest.dataset.availability_policy.as_json_value(),
        "migration_source": cast(JsonValue, manifest.migration_source),
    }


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo != UTC:
        raise ValueError(f"{field} must be UTC")
    return result
