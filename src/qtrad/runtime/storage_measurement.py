"""Hash-verified storage snapshots and deterministic growth comparison."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from qtrad.adapters.postgres.storage_measurement import PostgresStorageMeasurement
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.events import JsonValue

_MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
_MAX_RELATIONS = 500
_MAX_INDEXES = 2_000
_MIN_MEASUREMENT_SECONDS = 6 * 60 * 60
_MIN_RAW_MESSAGES = 100_000


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RelationStorageEvidence(_StrictModel):
    schema_name: str = Field(min_length=1, max_length=63)
    relation_name: str = Field(min_length=1, max_length=63)
    estimated_rows: int = Field(ge=0)
    heap_bytes: int = Field(ge=0)
    index_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class IndexStorageEvidence(_StrictModel):
    schema_name: str = Field(min_length=1, max_length=63)
    relation_name: str = Field(min_length=1, max_length=63)
    index_name: str = Field(min_length=1, max_length=63)
    index_bytes: int = Field(ge=0)
    scans_since_statistics_reset: int = Field(ge=0)


class PayloadSampleEvidence(_StrictModel):
    sample_rows: int = Field(ge=0, le=10_000)
    average_payload_bytes: int = Field(ge=0)
    average_json_text_bytes: int | None = Field(default=None, ge=0)
    average_payload_fields: int = Field(ge=0)


class RawPayloadRepresentationEvidence(_StrictModel):
    representation_code: int = Field(ge=0, le=3)
    representation_name: Literal["LEGACY_UNCLASSIFIED", "MERGED_STATE", "CHANGED_FIELDS", "FIXTURE"]
    row_count: int = Field(gt=0)


class StorageSnapshot(_StrictModel):
    schema_version: Literal[1, 2, 3]
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    statistics_reset_at: datetime | None = None
    capture_source_id: str = Field(min_length=1, max_length=200)
    universe_name: str = Field(min_length=1, max_length=64)
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_version: str = Field(min_length=1, max_length=64)
    application_image: str = Field(min_length=1, max_length=500)
    database_name: str = Field(min_length=1, max_length=63)
    database_bytes: int = Field(ge=0)
    raw_message_count: int = Field(ge=0)
    canonical_event_count: int = Field(ge=0)
    raw_payload_representation_column_present: bool | None = None
    raw_payload_representations: tuple[RawPayloadRepresentationEvidence, ...] = ()
    relations: tuple[RelationStorageEvidence, ...]
    indexes: tuple[IndexStorageEvidence, ...]
    raw_payload_sample: PayloadSampleEvidence
    canonical_payload_sample: PayloadSampleEvidence


def build_storage_snapshot(
    measurement: PostgresStorageMeasurement,
    *,
    capture_source_id: str,
    universe_name: str,
    configuration_hash: str,
    application_version: str,
    application_image: str,
) -> StorageSnapshot:
    snapshot = StorageSnapshot(
        schema_version=3,
        snapshot_sha256="0" * 64,
        observed_at=measurement.observed_at,
        statistics_reset_at=measurement.statistics_reset_at,
        capture_source_id=capture_source_id,
        universe_name=universe_name,
        configuration_hash=configuration_hash,
        application_version=application_version,
        application_image=application_image,
        database_name=measurement.database_name,
        database_bytes=measurement.database_bytes,
        raw_message_count=measurement.raw_message_count,
        canonical_event_count=measurement.canonical_event_count,
        raw_payload_representation_column_present=(
            measurement.raw_payload_representation_column_present
        ),
        raw_payload_representations=tuple(
            RawPayloadRepresentationEvidence(
                representation_code=int(count.representation),
                representation_name=count.representation.name,
                row_count=count.row_count,
            )
            for count in measurement.raw_payload_representation_counts
        ),
        relations=tuple(
            RelationStorageEvidence(
                schema_name=relation.schema_name,
                relation_name=relation.relation_name,
                estimated_rows=relation.estimated_rows,
                heap_bytes=relation.heap_bytes,
                index_bytes=relation.index_bytes,
                total_bytes=relation.total_bytes,
            )
            for relation in measurement.relations
        ),
        indexes=tuple(
            IndexStorageEvidence(
                schema_name=index.schema_name,
                relation_name=index.relation_name,
                index_name=index.index_name,
                index_bytes=index.index_bytes,
                scans_since_statistics_reset=index.scans_since_statistics_reset,
            )
            for index in measurement.indexes
        ),
        raw_payload_sample=PayloadSampleEvidence(
            sample_rows=measurement.raw_payload_sample.sample_rows,
            average_payload_bytes=measurement.raw_payload_sample.average_payload_bytes,
            average_json_text_bytes=measurement.raw_payload_sample.average_json_text_bytes,
            average_payload_fields=measurement.raw_payload_sample.average_payload_fields,
        ),
        canonical_payload_sample=PayloadSampleEvidence(
            sample_rows=measurement.canonical_payload_sample.sample_rows,
            average_payload_bytes=measurement.canonical_payload_sample.average_payload_bytes,
            average_json_text_bytes=measurement.canonical_payload_sample.average_json_text_bytes,
            average_payload_fields=measurement.canonical_payload_sample.average_payload_fields,
        ),
    )
    snapshot = snapshot.model_copy(
        update={"snapshot_sha256": _sha256_json(_snapshot_identity(snapshot))}
    )
    _validate_snapshot(snapshot)
    return snapshot


def write_storage_snapshot(path: Path, snapshot: StorageSnapshot) -> None:
    if not path.parent.is_dir():
        raise FileNotFoundError(f"storage snapshot output directory does not exist: {path.parent}")
    encoded = json.dumps(_snapshot_row(snapshot), indent=2, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("storage snapshot exceeds the maximum encoded size")
    with path.open("x", encoding="utf-8") as output:
        output.write(encoded)


def load_storage_snapshot(path: Path) -> StorageSnapshot:
    encoded = path.read_bytes()
    if len(encoded) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("storage snapshot exceeds the maximum encoded size")
    snapshot = StorageSnapshot.model_validate_json(encoded)
    _validate_snapshot(snapshot)
    return snapshot


def compare_storage_snapshots(
    before: StorageSnapshot,
    after: StorageSnapshot,
) -> dict[str, JsonValue]:
    _validate_snapshot(before)
    _validate_snapshot(after)
    if before.capture_source_id != after.capture_source_id:
        raise ValueError("storage snapshots have different capture sources")
    if before.database_name != after.database_name:
        raise ValueError("storage snapshots have different databases")
    if before.universe_name != after.universe_name:
        raise ValueError("storage snapshots have different capture universes")
    if before.configuration_hash != after.configuration_hash:
        raise ValueError("storage snapshots have different capture configurations")
    if before.application_version != after.application_version:
        raise ValueError("storage snapshots have different application versions")
    if before.application_image != after.application_image:
        raise ValueError("storage snapshots have different application images")
    if after.observed_at <= before.observed_at:
        raise ValueError("storage snapshot comparison is not chronological")
    raw_delta = after.raw_message_count - before.raw_message_count
    canonical_delta = after.canonical_event_count - before.canonical_event_count
    if raw_delta <= 0:
        raise ValueError("storage snapshot comparison requires new raw messages")
    if canonical_delta < 0:
        raise ValueError("canonical event count regressed between storage snapshots")

    before_relations = _relation_map(before)
    after_relations = _relation_map(after)
    relation_names = sorted(set(before_relations) | set(after_relations))
    relation_deltas: list[JsonValue] = []
    for name in relation_names:
        previous = before_relations.get(name)
        current = after_relations.get(name)
        relation_deltas.append(
            {
                "relation": name,
                "heap_bytes": _value(current, "heap_bytes") - _value(previous, "heap_bytes"),
                "index_bytes": _value(current, "index_bytes") - _value(previous, "index_bytes"),
                "total_bytes": _value(current, "total_bytes") - _value(previous, "total_bytes"),
            }
        )

    before_indexes = _index_map(before)
    after_indexes = _index_map(after)
    statistics_reset_changed = before.statistics_reset_at != after.statistics_reset_at
    index_names = sorted(set(before_indexes) | set(after_indexes))
    index_deltas: list[JsonValue] = []
    for name in index_names:
        previous = before_indexes.get(name)
        current = after_indexes.get(name)
        index_bytes_delta = _index_value(current, "index_bytes") - _index_value(
            previous, "index_bytes"
        )
        index_deltas.append(
            {
                "index": name,
                "relation": _index_relation(current or previous),
                "index_bytes": index_bytes_delta,
                "scans_since_statistics_reset": (
                    None
                    if statistics_reset_changed
                    else _index_value(current, "scans_since_statistics_reset")
                    - _index_value(previous, "scans_since_statistics_reset")
                ),
                "bytes_per_raw_message": _ratio(index_bytes_delta, raw_delta),
            }
        )

    raw_relation_delta = _total_delta(
        before_relations,
        after_relations,
        "raw.market_messages",
    )
    canonical_relation_delta = _total_delta(
        before_relations,
        after_relations,
        "canonical.events",
    )
    raw_components = _relation_component_deltas(
        before_relations,
        after_relations,
        "raw.market_messages",
    )
    canonical_components = _relation_component_deltas(
        before_relations,
        after_relations,
        "canonical.events",
    )
    combined_components = (
        raw_components[0] + canonical_components[0],
        raw_components[1] + canonical_components[1],
        raw_components[2] + canonical_components[2],
        raw_components[3] + canonical_components[3],
    )
    elapsed = after.observed_at - before.observed_at
    elapsed_seconds = Decimal(elapsed.days * 86_400 + elapsed.seconds) + (
        Decimal(elapsed.microseconds) / Decimal(1_000_000)
    )
    elapsed_satisfied = elapsed_seconds >= _MIN_MEASUREMENT_SECONDS
    raw_volume_satisfied = raw_delta >= _MIN_RAW_MESSAGES
    representative_thresholds_satisfied = elapsed_satisfied and raw_volume_satisfied
    representation_evidence = _representation_comparison(before, after, raw_delta=raw_delta)
    return {
        "schema_version": 2,
        "capture_source_id": before.capture_source_id,
        "database_name": before.database_name,
        "before_snapshot_sha256": before.snapshot_sha256,
        "after_snapshot_sha256": after.snapshot_sha256,
        "before_observed_at": _utc_text(before.observed_at),
        "after_observed_at": _utc_text(after.observed_at),
        "elapsed_seconds": str(elapsed_seconds),
        "before_configuration_hash": before.configuration_hash,
        "after_configuration_hash": after.configuration_hash,
        "raw_messages_delta": raw_delta,
        "canonical_events_delta": canonical_delta,
        "database_bytes_delta": after.database_bytes - before.database_bytes,
        "statistics_reset_changed": statistics_reset_changed,
        "measurement_gate": {
            "minimum_elapsed_seconds": _MIN_MEASUREMENT_SECONDS,
            "elapsed_satisfied": elapsed_satisfied,
            "minimum_raw_messages": _MIN_RAW_MESSAGES,
            "raw_volume_satisfied": raw_volume_satisfied,
            "representative_thresholds_satisfied": representative_thresholds_satisfied,
            "index_scan_evidence_usable": (
                representative_thresholds_satisfied and not statistics_reset_changed
            ),
            "raw_representation_evidence_usable": representation_evidence["usable"],
            "operator_active_market_review_required": True,
        },
        "raw_representation_evidence": representation_evidence,
        "observed_rate_extrapolation": {
            "basis": "mechanical_continuation_of_observed_interval",
            "representative_thresholds_satisfied": representative_thresholds_satisfied,
            "rates_per_second": {
                "raw_messages": _rate(raw_delta, elapsed_seconds, precision=6),
                "canonical_events": _rate(canonical_delta, elapsed_seconds, precision=6),
                "raw_relation_bytes": _rate(raw_relation_delta, elapsed_seconds, precision=3),
                "canonical_relation_bytes": _rate(
                    canonical_relation_delta, elapsed_seconds, precision=3
                ),
                "combined_capture_relation_bytes": _rate(
                    raw_relation_delta + canonical_relation_delta,
                    elapsed_seconds,
                    precision=3,
                ),
            },
            "combined_capture_relation_bytes": {
                "one_day": _rate_projection(
                    raw_relation_delta + canonical_relation_delta,
                    elapsed_seconds,
                    seconds=86_400,
                ),
                "thirty_days": _rate_projection(
                    raw_relation_delta + canonical_relation_delta,
                    elapsed_seconds,
                    seconds=30 * 86_400,
                ),
                "three_hundred_sixty_five_days": _rate_projection(
                    raw_relation_delta + canonical_relation_delta,
                    elapsed_seconds,
                    seconds=365 * 86_400,
                ),
            },
        },
        "raw_relation_bytes_delta": raw_relation_delta,
        "canonical_relation_bytes_delta": canonical_relation_delta,
        "canonical_events_per_raw_message": _ratio(canonical_delta, raw_delta),
        "bytes_per_raw_message": {
            "database": _ratio(after.database_bytes - before.database_bytes, raw_delta),
            "raw_relation": _ratio(raw_relation_delta, raw_delta),
            "canonical_relation": _ratio(canonical_relation_delta, raw_delta),
            "raw_and_canonical_relations": _ratio(
                raw_relation_delta + canonical_relation_delta,
                raw_delta,
            ),
        },
        "capture_growth_attribution": {
            "component_order": ["heap", "indexes", "auxiliary", "total"],
            "combined": _component_summary(
                combined_components,
                raw_message_delta=raw_delta,
                relation_row_delta=raw_delta + canonical_delta,
            ),
            "raw": _component_summary(
                raw_components,
                raw_message_delta=raw_delta,
                relation_row_delta=raw_delta,
            ),
            "canonical": _component_summary(
                canonical_components,
                raw_message_delta=raw_delta,
                relation_row_delta=canonical_delta,
            ),
        },
        "relation_deltas": relation_deltas,
        "index_deltas": index_deltas,
        "before_payload_samples": _sample_summary(before),
        "after_payload_samples": _sample_summary(after),
    }


def _validate_snapshot(snapshot: StorageSnapshot) -> None:
    if snapshot.observed_at.tzinfo is None or snapshot.observed_at.utcoffset() != UTC.utcoffset(
        snapshot.observed_at
    ):
        raise ValueError("storage snapshot observed time must use UTC")
    if snapshot.statistics_reset_at is not None:
        _utc_text(snapshot.statistics_reset_at)
    if snapshot.schema_version == 1 and snapshot.statistics_reset_at is not None:
        raise ValueError("version-one storage snapshot has statistics-reset evidence")
    if len(snapshot.relations) > _MAX_RELATIONS:
        raise ValueError("storage snapshot contains too many relations")
    if len(snapshot.indexes) > _MAX_INDEXES:
        raise ValueError("storage snapshot contains too many indexes")
    samples = (snapshot.raw_payload_sample, snapshot.canonical_payload_sample)
    if snapshot.schema_version == 1 and any(
        sample.average_json_text_bytes is not None for sample in samples
    ):
        raise ValueError("version-one storage snapshot has JSON-text evidence")
    if snapshot.schema_version >= 2 and any(
        sample.average_json_text_bytes is None for sample in samples
    ):
        raise ValueError("current storage snapshot requires JSON-text evidence")
    if snapshot.schema_version < 3:
        if snapshot.raw_payload_representation_column_present is not None:
            raise ValueError("legacy storage snapshot has raw representation schema evidence")
        if snapshot.raw_payload_representations:
            raise ValueError("legacy storage snapshot has raw representation counts")
    else:
        column_present = snapshot.raw_payload_representation_column_present
        if column_present is None:
            raise ValueError(
                "version-three storage snapshot requires representation schema evidence"
            )
        if not column_present and snapshot.raw_payload_representations:
            raise ValueError("pre-marker storage snapshot cannot contain representation counts")
        representation_codes = [
            representation.representation_code
            for representation in snapshot.raw_payload_representations
        ]
        if len(set(representation_codes)) != len(representation_codes):
            raise ValueError("storage snapshot raw representation codes must be unique")
        if representation_codes != sorted(representation_codes):
            raise ValueError("storage snapshot raw representation codes must be ordered")
        for representation in snapshot.raw_payload_representations:
            expected_name = RawPayloadRepresentation(representation.representation_code).name
            if representation.representation_name != expected_name:
                raise ValueError("storage snapshot raw representation code and name disagree")
        if column_present and (
            sum(representation.row_count for representation in snapshot.raw_payload_representations)
            != snapshot.raw_message_count
        ):
            raise ValueError("storage snapshot raw representation counts do not match raw messages")
    relation_names = [
        f"{relation.schema_name}.{relation.relation_name}" for relation in snapshot.relations
    ]
    if len(set(relation_names)) != len(relation_names):
        raise ValueError("storage snapshot relation names must be unique")
    if not {"raw.market_messages", "canonical.events"}.issubset(relation_names):
        raise ValueError("storage snapshot is missing capture relations")
    if any(
        relation.total_bytes < relation.heap_bytes + relation.index_bytes
        for relation in snapshot.relations
    ):
        raise ValueError("storage snapshot relation total is smaller than heap and indexes")
    index_names = [f"{index.schema_name}.{index.index_name}" for index in snapshot.indexes]
    if len(set(index_names)) != len(index_names):
        raise ValueError("storage snapshot index names must be unique")
    calculated = _sha256_json(_snapshot_identity(snapshot))
    if calculated != snapshot.snapshot_sha256:
        raise ValueError("storage snapshot hash does not match its canonical content")


def _snapshot_row(snapshot: StorageSnapshot) -> dict[str, JsonValue]:
    return {
        **_snapshot_identity(snapshot),
        "snapshot_sha256": snapshot.snapshot_sha256,
    }


def _snapshot_identity(snapshot: StorageSnapshot) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "schema_version": snapshot.schema_version,
        "observed_at": _utc_text(snapshot.observed_at),
        "capture_source_id": snapshot.capture_source_id,
        "universe_name": snapshot.universe_name,
        "configuration_hash": snapshot.configuration_hash,
        "application_version": snapshot.application_version,
        "application_image": snapshot.application_image,
        "database_name": snapshot.database_name,
        "database_bytes": snapshot.database_bytes,
        "raw_message_count": snapshot.raw_message_count,
        "canonical_event_count": snapshot.canonical_event_count,
        "relations": [relation.model_dump(mode="json") for relation in snapshot.relations],
        "indexes": [index.model_dump(mode="json") for index in snapshot.indexes],
        "raw_payload_sample": _payload_sample_identity(
            snapshot.raw_payload_sample, schema_version=snapshot.schema_version
        ),
        "canonical_payload_sample": _payload_sample_identity(
            snapshot.canonical_payload_sample, schema_version=snapshot.schema_version
        ),
    }
    if snapshot.schema_version >= 2:
        value["statistics_reset_at"] = (
            _utc_text(snapshot.statistics_reset_at)
            if snapshot.statistics_reset_at is not None
            else None
        )
    if snapshot.schema_version >= 3:
        value["raw_payload_representation_column_present"] = (
            snapshot.raw_payload_representation_column_present
        )
        value["raw_payload_representations"] = [
            representation.model_dump(mode="json")
            for representation in snapshot.raw_payload_representations
        ]
    return value


def _relation_map(snapshot: StorageSnapshot) -> dict[str, RelationStorageEvidence]:
    return {
        f"{relation.schema_name}.{relation.relation_name}": relation
        for relation in snapshot.relations
    }


def _index_map(snapshot: StorageSnapshot) -> dict[str, IndexStorageEvidence]:
    return {f"{index.schema_name}.{index.index_name}": index for index in snapshot.indexes}


def _index_value(index: IndexStorageEvidence | None, field: str) -> int:
    if index is None:
        return 0
    if field == "index_bytes":
        return index.index_bytes
    if field == "scans_since_statistics_reset":
        return index.scans_since_statistics_reset
    raise ValueError(f"unknown index storage field: {field}")


def _index_relation(index: IndexStorageEvidence | None) -> str:
    if index is None:
        raise ValueError("storage index delta has no relation identity")
    return f"{index.schema_name}.{index.relation_name}"


def _value(relation: RelationStorageEvidence | None, field: str) -> int:
    if relation is None:
        return 0
    if field == "heap_bytes":
        return relation.heap_bytes
    if field == "index_bytes":
        return relation.index_bytes
    if field == "total_bytes":
        return relation.total_bytes
    raise ValueError(f"unknown relation storage field: {field}")


def _total_delta(
    before: dict[str, RelationStorageEvidence],
    after: dict[str, RelationStorageEvidence],
    relation_name: str,
) -> int:
    if relation_name not in before or relation_name not in after:
        raise ValueError(f"storage snapshot comparison is missing {relation_name}")
    return after[relation_name].total_bytes - before[relation_name].total_bytes


def _relation_component_deltas(
    before: dict[str, RelationStorageEvidence],
    after: dict[str, RelationStorageEvidence],
    relation_name: str,
) -> tuple[int, int, int, int]:
    if relation_name not in before or relation_name not in after:
        raise ValueError(f"storage snapshot comparison is missing {relation_name}")
    previous = before[relation_name]
    current = after[relation_name]
    heap_delta = current.heap_bytes - previous.heap_bytes
    index_delta = current.index_bytes - previous.index_bytes
    total_delta = current.total_bytes - previous.total_bytes
    auxiliary_delta = total_delta - heap_delta - index_delta
    return heap_delta, index_delta, auxiliary_delta, total_delta


def _component_summary(
    components: tuple[int, int, int, int],
    *,
    raw_message_delta: int,
    relation_row_delta: int,
) -> dict[str, JsonValue]:
    names = ("heap", "indexes", "auxiliary", "total")
    return {
        "rows_delta": relation_row_delta,
        "bytes_delta": dict(zip(names, components, strict=True)),
        "bytes_per_raw_message": {
            name: _ratio(value, raw_message_delta)
            for name, value in zip(names, components, strict=True)
        },
        "bytes_per_new_relation_row": (
            None
            if relation_row_delta == 0
            else {
                name: _ratio(value, relation_row_delta)
                for name, value in zip(names, components, strict=True)
            }
        ),
    }


def _sample_summary(snapshot: StorageSnapshot) -> dict[str, JsonValue]:
    return {
        "raw": snapshot.raw_payload_sample.model_dump(mode="json"),
        "canonical": snapshot.canonical_payload_sample.model_dump(mode="json"),
    }


def _payload_sample_identity(
    sample: PayloadSampleEvidence,
    *,
    schema_version: int,
) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "sample_rows": sample.sample_rows,
        "average_payload_bytes": sample.average_payload_bytes,
        "average_payload_fields": sample.average_payload_fields,
    }
    if schema_version >= 2:
        if sample.average_json_text_bytes is None:
            raise ValueError("version-two storage sample requires JSON-text evidence")
        value["average_json_text_bytes"] = sample.average_json_text_bytes
    return value


def _representation_comparison(
    before: StorageSnapshot,
    after: StorageSnapshot,
    *,
    raw_delta: int,
) -> dict[str, JsonValue]:
    if before.schema_version < 3 or after.schema_version < 3:
        result: dict[str, JsonValue] = {
            "usable": False,
            "status": "UNAVAILABLE_IN_LEGACY_SNAPSHOT",
            "new_rows_by_representation": None,
            "single_new_representation": None,
            "all_new_rows_changed_fields": None,
            "legacy_unclassified_rows_delta": None,
        }
        return result
    before_column = before.raw_payload_representation_column_present
    after_column = after.raw_payload_representation_column_present
    if before_column != after_column:
        raise ValueError("raw payload representation schema changed between storage snapshots")
    if not before_column:
        pre_marker_result: dict[str, JsonValue] = {
            "usable": True,
            "status": "PRE_MARKER_SCHEMA",
            "new_rows_by_representation": {"PRE_MARKER_SCHEMA": raw_delta},
            "single_new_representation": "PRE_MARKER_SCHEMA",
            "all_new_rows_changed_fields": None,
            "legacy_unclassified_rows_delta": None,
        }
        return pre_marker_result

    before_counts = _representation_map(before)
    after_counts = _representation_map(after)
    deltas: dict[str, int] = {}
    for representation in RawPayloadRepresentation:
        delta = after_counts.get(representation, 0) - before_counts.get(representation, 0)
        if delta < 0:
            raise ValueError("raw payload representation count regressed between storage snapshots")
        if delta:
            deltas[representation.name] = delta
    if sum(deltas.values()) != raw_delta:
        raise ValueError("raw payload representation deltas do not match new raw messages")
    single = next(iter(deltas)) if len(deltas) == 1 else None
    changed_fields_delta = int(deltas.get(RawPayloadRepresentation.CHANGED_FIELDS.name, 0))
    new_rows_by_representation: dict[str, JsonValue] = {
        name: count for name, count in deltas.items()
    }
    coded_result: dict[str, JsonValue] = {
        "usable": True,
        "status": "CODED",
        "new_rows_by_representation": new_rows_by_representation,
        "single_new_representation": single,
        "all_new_rows_changed_fields": changed_fields_delta == raw_delta,
        "legacy_unclassified_rows_delta": int(
            deltas.get(RawPayloadRepresentation.LEGACY_UNCLASSIFIED.name, 0)
        ),
    }
    return coded_result


def _representation_map(
    snapshot: StorageSnapshot,
) -> dict[RawPayloadRepresentation, int]:
    return {
        RawPayloadRepresentation(representation.representation_code): representation.row_count
        for representation in snapshot.raw_payload_representations
    }


def _ratio(numerator: int, denominator: int) -> str:
    return format(Decimal(numerator) / Decimal(denominator), ".3f")


def _rate(numerator: int, elapsed_seconds: Decimal, *, precision: int) -> str:
    return format(Decimal(numerator) / elapsed_seconds, f".{precision}f")


def _rate_projection(numerator: int, elapsed_seconds: Decimal, *, seconds: int) -> str:
    return format(Decimal(numerator) * Decimal(seconds) / elapsed_seconds, ".3f")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("storage snapshot time must use UTC")
    return value.isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
