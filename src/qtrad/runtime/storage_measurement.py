"""Hash-verified storage snapshots and deterministic growth comparison."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from qtrad.adapters.postgres.storage_measurement import PostgresStorageMeasurement
from qtrad.domain.events import JsonValue

_MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
_MAX_RELATIONS = 500
_MAX_INDEXES = 2_000


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


class StorageSnapshot(_StrictModel):
    schema_version: Literal[1, 2]
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
        schema_version=2,
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
    elapsed = after.observed_at - before.observed_at
    elapsed_seconds = Decimal(elapsed.days * 86_400 + elapsed.seconds) + (
        Decimal(elapsed.microseconds) / Decimal(1_000_000)
    )
    return {
        "schema_version": 1,
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
        "raw_relation_bytes_delta": raw_relation_delta,
        "canonical_relation_bytes_delta": canonical_relation_delta,
        "bytes_per_raw_message": {
            "database": _ratio(after.database_bytes - before.database_bytes, raw_delta),
            "raw_relation": _ratio(raw_relation_delta, raw_delta),
            "canonical_relation": _ratio(canonical_relation_delta, raw_delta),
            "raw_and_canonical_relations": _ratio(
                raw_relation_delta + canonical_relation_delta,
                raw_delta,
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
    if snapshot.schema_version == 2 and any(
        sample.average_json_text_bytes is None for sample in samples
    ):
        raise ValueError("version-two storage snapshot requires JSON-text evidence")
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
    if snapshot.schema_version == 2:
        value["statistics_reset_at"] = (
            _utc_text(snapshot.statistics_reset_at)
            if snapshot.statistics_reset_at is not None
            else None
        )
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
    if schema_version == 2:
        if sample.average_json_text_bytes is None:
            raise ValueError("version-two storage sample requires JSON-text evidence")
        value["average_json_text_bytes"] = sample.average_json_text_bytes
    return value


def _ratio(numerator: int, denominator: int) -> str:
    return format(Decimal(numerator) / Decimal(denominator), ".3f")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("storage snapshot time must use UTC")
    return value.isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
