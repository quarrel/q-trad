import hashlib
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
        statistics_reset_at=NOW - timedelta(days=1),
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
        raw_payload_sample=PayloadSample(100, 139, 128, 7),
        canonical_payload_sample=PayloadSample(100, 420, 372, 12),
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
        indexes=(
            IndexStorage("canonical", "events", "events_pkey", 700, 103),
            IndexStorage("raw", "market_messages", "market_messages_pkey", 350, 102),
        ),
        raw_payload_sample=PayloadSample(102, 61, 52, 3),
    )

    comparison = compare_storage_snapshots(_snapshot(), _snapshot(after_measurement))

    assert comparison["elapsed_seconds"] == "60"
    assert comparison["raw_messages_delta"] == 2
    assert comparison["canonical_events_delta"] == 3
    assert comparison["database_bytes_delta"] == 4_000
    assert comparison["statistics_reset_changed"] is False
    assert comparison["bytes_per_raw_message"] == {
        "database": "2000.000",
        "raw_relation": "750.000",
        "canonical_relation": "750.000",
        "raw_and_canonical_relations": "1500.000",
    }
    assert comparison["canonical_events_per_raw_message"] == "1.500"
    assert comparison["capture_growth_attribution"] == {
        "component_order": ["heap", "indexes", "auxiliary", "total"],
        "combined": {
            "rows_delta": 5,
            "bytes_delta": {
                "heap": 1_300,
                "indexes": 700,
                "auxiliary": 1_000,
                "total": 3_000,
            },
            "bytes_per_raw_message": {
                "heap": "650.000",
                "indexes": "350.000",
                "auxiliary": "500.000",
                "total": "1500.000",
            },
            "bytes_per_new_relation_row": {
                "heap": "260.000",
                "indexes": "140.000",
                "auxiliary": "200.000",
                "total": "600.000",
            },
        },
        "raw": {
            "rows_delta": 2,
            "bytes_delta": {
                "heap": 500,
                "indexes": 300,
                "auxiliary": 700,
                "total": 1_500,
            },
            "bytes_per_raw_message": {
                "heap": "250.000",
                "indexes": "150.000",
                "auxiliary": "350.000",
                "total": "750.000",
            },
            "bytes_per_new_relation_row": {
                "heap": "250.000",
                "indexes": "150.000",
                "auxiliary": "350.000",
                "total": "750.000",
            },
        },
        "canonical": {
            "rows_delta": 3,
            "bytes_delta": {
                "heap": 800,
                "indexes": 400,
                "auxiliary": 300,
                "total": 1_500,
            },
            "bytes_per_raw_message": {
                "heap": "400.000",
                "indexes": "200.000",
                "auxiliary": "150.000",
                "total": "750.000",
            },
            "bytes_per_new_relation_row": {
                "heap": "266.667",
                "indexes": "133.333",
                "auxiliary": "100.000",
                "total": "500.000",
            },
        },
    }
    assert comparison["index_deltas"] == [
        {
            "index": "canonical.events_pkey",
            "relation": "canonical.events",
            "index_bytes": 300,
            "scans_since_statistics_reset": 3,
            "bytes_per_raw_message": "150.000",
        },
        {
            "index": "raw.market_messages_pkey",
            "relation": "raw.market_messages",
            "index_bytes": 150,
            "scans_since_statistics_reset": 2,
            "bytes_per_raw_message": "75.000",
        },
    ]


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


def test_legacy_storage_snapshot_hash_omits_version_two_payload_evidence(tmp_path: Path) -> None:
    current_path = tmp_path / "current.json"
    write_storage_snapshot(current_path, _snapshot())
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload.pop("statistics_reset_at")
    payload["raw_payload_sample"].pop("average_json_text_bytes")
    payload["canonical_payload_sample"].pop("average_json_text_bytes")
    identity = {key: value for key, value in payload.items() if key != "snapshot_sha256"}
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps(payload), encoding="utf-8")

    legacy = load_storage_snapshot(legacy_path)

    assert legacy.schema_version == 1
    assert legacy.raw_payload_sample.average_json_text_bytes is None


def test_storage_comparison_invalidates_scan_deltas_after_statistics_reset() -> None:
    before = _measurement()
    after = replace(
        before,
        observed_at=NOW + timedelta(minutes=1),
        statistics_reset_at=NOW + timedelta(seconds=30),
        raw_message_count=101,
        canonical_event_count=101,
    )

    comparison = compare_storage_snapshots(_snapshot(before), _snapshot(after))

    assert comparison["statistics_reset_changed"] is True
    index_deltas = comparison["index_deltas"]
    assert isinstance(index_deltas, list)
    for row in index_deltas:
        assert isinstance(row, dict)
        assert row["scans_since_statistics_reset"] is None


def test_storage_comparison_handles_no_new_canonical_rows() -> None:
    before = _measurement()
    after = replace(
        before,
        observed_at=NOW + timedelta(minutes=1),
        raw_message_count=101,
    )

    comparison = compare_storage_snapshots(_snapshot(before), _snapshot(after))

    assert comparison["canonical_events_per_raw_message"] == "0.000"
    attribution = comparison["capture_growth_attribution"]
    assert isinstance(attribution, dict)
    canonical = attribution["canonical"]
    assert isinstance(canonical, dict)
    assert canonical["rows_delta"] == 0
    assert canonical["bytes_per_new_relation_row"] is None
