import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qtrad.adapters.postgres.storage_measurement import (
    IndexStorage,
    PayloadSample,
    PostgresStorageMeasurement,
    RelationStorage,
)
from qtrad.runtime.storage_measurement import (
    build_storage_snapshot,
    compare_storage_snapshots,
    load_storage_snapshot,
    write_storage_snapshot,
)

NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _measurement() -> PostgresStorageMeasurement:
    return PostgresStorageMeasurement(
        observed_at=NOW,
        database_name="qtrad_capture",
        database_bytes=10_000,
        raw_message_count=100,
        canonical_event_count=100,
        relations=(
            RelationStorage("canonical", "events", 100, 1_000, 800, 2_000),
            RelationStorage("raw", "market_messages", 100, 500, 400, 1_000),
        ),
        indexes=(
            IndexStorage("canonical", "events", "events_pkey", 400, 100),
            IndexStorage("raw", "market_messages", "market_messages_pkey", 200, 100),
        ),
        raw_payload_sample=PayloadSample(100, 139, 7),
        canonical_payload_sample=PayloadSample(100, 420, 12),
    )


def _snapshot(measurement: PostgresStorageMeasurement | None = None):
    return build_storage_snapshot(
        measurement or _measurement(),
        capture_source_id="oci-sydney-capture-1",
        universe_name="capture-v1",
        configuration_hash="a" * 64,
        application_version="0.1.0",
        application_image="syd.ocir.io/example/qtrad@sha256:" + "b" * 64,
    )


def test_storage_snapshot_is_hash_verified_and_non_overwriting(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = tmp_path / "storage.json"

    write_storage_snapshot(path, snapshot)
    loaded = load_storage_snapshot(path)

    assert loaded == snapshot
    assert loaded.snapshot_sha256
    with pytest.raises(FileExistsError):
        write_storage_snapshot(path, snapshot)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["database_bytes"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash does not match"):
        load_storage_snapshot(path)


def test_storage_comparison_reports_physical_bytes_per_raw_message() -> None:
    before_measurement = _measurement()
    after_measurement = replace(
        before_measurement,
        observed_at=NOW + timedelta(minutes=1),
        database_bytes=14_000,
        raw_message_count=102,
        canonical_event_count=103,
        relations=(
            RelationStorage("canonical", "events", 103, 1_800, 1_200, 3_500),
            RelationStorage("raw", "market_messages", 102, 1_000, 700, 2_500),
        ),
        raw_payload_sample=PayloadSample(102, 61, 3),
    )

    comparison = compare_storage_snapshots(_snapshot(), _snapshot(after_measurement))

    assert comparison["elapsed_seconds"] == "60"
    assert comparison["raw_messages_delta"] == 2
    assert comparison["canonical_events_delta"] == 3
    assert comparison["database_bytes_delta"] == 4_000
    assert comparison["bytes_per_raw_message"] == {
        "database": "2000.000",
        "raw_relation": "750.000",
        "canonical_relation": "750.000",
        "raw_and_canonical_relations": "1500.000",
    }


def test_storage_comparison_fails_closed_on_identity_or_counter_drift() -> None:
    before = _snapshot()
    different_source = build_storage_snapshot(
        _measurement(),
        capture_source_id="another-source",
        universe_name="capture-v1",
        configuration_hash="a" * 64,
        application_version="0.1.0",
        application_image="syd.ocir.io/example/qtrad@sha256:" + "b" * 64,
    )
    with pytest.raises(ValueError, match="different capture sources"):
        compare_storage_snapshots(before, different_source)

    no_updates = _snapshot(replace(_measurement(), observed_at=NOW + timedelta(minutes=1)))
    with pytest.raises(ValueError, match="requires new raw messages"):
        compare_storage_snapshots(before, no_updates)


def test_storage_snapshot_allows_schema_scoped_index_names_and_bounds_input(
    tmp_path: Path,
) -> None:
    measurement = replace(
        _measurement(),
        indexes=(
            IndexStorage("canonical", "events", "capture_idx", 400, 100),
            IndexStorage("raw", "market_messages", "capture_idx", 200, 100),
        ),
    )
    assert _snapshot(measurement).indexes[0].index_name == "capture_idx"

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="maximum encoded size"):
        load_storage_snapshot(oversized)
