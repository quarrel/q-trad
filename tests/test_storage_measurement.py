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
    RawPayloadRepresentationCount,
    RelationStorage,
)
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.runtime.storage_measurement import (
    build_storage_comparison_artifact,
    build_storage_contrast_artifact,
    build_storage_snapshot,
    compare_storage_snapshots,
    load_storage_evidence_artifact,
    load_storage_snapshot,
    write_storage_evidence_artifact,
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
        raw_payload_representation_column_present=False,
        raw_payload_representation_counts=(),
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


def _snapshot(
    measurement: PostgresStorageMeasurement | None = None,
    *,
    application_image: str = "syd.ocir.io/example/qtrad@sha256:" + "b" * 64,
):
    return build_storage_snapshot(
        measurement or _measurement(),
        capture_source_id="oci-sydney-capture-1",
        universe_name="capture-v1",
        configuration_hash="a" * 64,
        application_version="0.1.0",
        application_image=application_image,
    )


def _coded_measurement(
    *,
    observed_at: datetime = NOW,
    legacy_rows: int,
    changed_field_rows: int,
) -> PostgresStorageMeasurement:
    raw_rows = legacy_rows + changed_field_rows
    counts = []
    if legacy_rows:
        counts.append(
            RawPayloadRepresentationCount(
                RawPayloadRepresentation.LEGACY_UNCLASSIFIED,
                legacy_rows,
            )
        )
    if changed_field_rows:
        counts.append(
            RawPayloadRepresentationCount(
                RawPayloadRepresentation.CHANGED_FIELDS,
                changed_field_rows,
            )
        )
    return replace(
        _measurement(),
        observed_at=observed_at,
        raw_message_count=raw_rows,
        canonical_event_count=raw_rows,
        raw_payload_representation_column_present=True,
        raw_payload_representation_counts=tuple(counts),
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


def test_storage_comparison_artifact_is_hash_verified_and_non_overwriting(tmp_path: Path) -> None:
    before = _snapshot()
    after = _snapshot(
        replace(
            _measurement(),
            observed_at=NOW + timedelta(minutes=1),
            raw_message_count=101,
            canonical_event_count=101,
        )
    )
    artifact = build_storage_comparison_artifact(before, after)
    path = tmp_path / "comparison.json"

    write_storage_evidence_artifact(path, artifact)

    assert load_storage_evidence_artifact(path) == artifact
    assert artifact.artifact_kind == "STORAGE_COMPARISON"
    assert artifact.payload["application_image"] == before.application_image
    with pytest.raises(FileExistsError):
        write_storage_evidence_artifact(path, artifact)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["payload"]["raw_messages_delta"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash does not match"):
        load_storage_evidence_artifact(path)

    contradictory = artifact.model_dump(mode="json")
    contradictory["payload"]["raw_representation_evidence"]["single_new_representation"] = (
        "CHANGED_FIELDS"
    )
    identity = {key: value for key, value in contradictory.items() if key != "artifact_sha256"}
    contradictory["artifact_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.write_text(json.dumps(contradictory), encoding="utf-8")
    with pytest.raises(ValueError, match="pre-marker representation evidence is contradictory"):
        load_storage_evidence_artifact(path)


def test_storage_comparison_proves_changed_field_rows_and_exposes_rollback_rows() -> None:
    before = _snapshot(_coded_measurement(legacy_rows=100, changed_field_rows=0))
    changed_only = _snapshot(
        _coded_measurement(
            observed_at=NOW + timedelta(minutes=1),
            legacy_rows=100,
            changed_field_rows=2,
        )
    )

    changed_evidence = compare_storage_snapshots(before, changed_only)[
        "raw_representation_evidence"
    ]

    assert changed_evidence == {
        "usable": True,
        "status": "CODED",
        "new_rows_by_representation": {"CHANGED_FIELDS": 2},
        "single_new_representation": "CHANGED_FIELDS",
        "all_new_rows_changed_fields": True,
        "legacy_unclassified_rows_delta": 0,
    }

    mixed = _snapshot(
        _coded_measurement(
            observed_at=NOW + timedelta(minutes=2),
            legacy_rows=101,
            changed_field_rows=2,
        )
    )
    mixed_evidence = compare_storage_snapshots(before, mixed)["raw_representation_evidence"]
    assert mixed_evidence == {
        "usable": True,
        "status": "CODED",
        "new_rows_by_representation": {"LEGACY_UNCLASSIFIED": 1, "CHANGED_FIELDS": 2},
        "single_new_representation": None,
        "all_new_rows_changed_fields": False,
        "legacy_unclassified_rows_delta": 1,
    }


def test_storage_snapshot_rejects_inconsistent_raw_representation_counts() -> None:
    inconsistent = replace(
        _coded_measurement(legacy_rows=100, changed_field_rows=0),
        raw_message_count=101,
    )

    with pytest.raises(ValueError, match="counts do not match raw messages"):
        _snapshot(inconsistent)


def test_storage_comparison_rejects_representation_regression_or_schema_transition() -> None:
    before = _snapshot(_coded_measurement(legacy_rows=100, changed_field_rows=0))
    regressed = _snapshot(
        _coded_measurement(
            observed_at=NOW + timedelta(minutes=1),
            legacy_rows=99,
            changed_field_rows=3,
        )
    )
    with pytest.raises(ValueError, match="representation count regressed"):
        compare_storage_snapshots(before, regressed)

    pre_marker = _snapshot(
        replace(
            _measurement(),
            observed_at=NOW + timedelta(minutes=1),
            raw_message_count=102,
        )
    )
    with pytest.raises(ValueError, match="representation schema changed"):
        compare_storage_snapshots(before, pre_marker)


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
    assert comparison["measurement_gate"] == {
        "minimum_elapsed_seconds": 21_600,
        "elapsed_satisfied": False,
        "minimum_raw_messages": 100_000,
        "raw_volume_satisfied": False,
        "representative_thresholds_satisfied": False,
        "index_scan_evidence_usable": False,
        "raw_representation_evidence_usable": True,
        "operator_active_market_review_required": True,
    }
    assert comparison["observed_rate_extrapolation"] == {
        "basis": "mechanical_continuation_of_observed_interval",
        "representative_thresholds_satisfied": False,
        "rates_per_second": {
            "raw_messages": "0.033333",
            "canonical_events": "0.050000",
            "raw_relation_bytes": "25.000",
            "canonical_relation_bytes": "25.000",
            "combined_capture_relation_bytes": "50.000",
        },
        "combined_capture_relation_bytes": {
            "one_day": "4320000.000",
            "thirty_days": "129600000.000",
            "three_hundred_sixty_five_days": "1576800000.000",
        },
    }
    assert comparison["bytes_per_raw_message"] == {
        "database": "2000.000",
        "raw_relation": "750.000",
        "canonical_relation": "750.000",
        "raw_and_canonical_relations": "1500.000",
    }
    assert comparison["canonical_events_per_raw_message"] == "1.500"
    assert comparison["raw_representation_evidence"] == {
        "usable": True,
        "status": "PRE_MARKER_SCHEMA",
        "new_rows_by_representation": {"PRE_MARKER_SCHEMA": 2},
        "single_new_representation": "PRE_MARKER_SCHEMA",
        "all_new_rows_changed_fields": None,
        "legacy_unclassified_rows_delta": None,
    }
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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("universe_name", "capture-v2", "different capture universes"),
        ("configuration_hash", "c" * 64, "different capture configurations"),
        ("application_version", "0.2.0", "different application versions"),
        (
            "application_image",
            "syd.ocir.io/example/qtrad@sha256:" + "d" * 64,
            "different application images",
        ),
    ],
)
def test_storage_comparison_rejects_release_identity_drift(
    field: str,
    value: str,
    message: str,
) -> None:
    before = _snapshot()
    after = _snapshot(replace(_measurement(), observed_at=NOW + timedelta(hours=7)))
    changed = after.model_copy(update={field: value})
    identity = {
        key: item
        for key, item in changed.model_dump(mode="json").items()
        if key != "snapshot_sha256"
    }
    changed = changed.model_copy(
        update={
            "snapshot_sha256": hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        }
    )

    with pytest.raises(ValueError, match=message):
        compare_storage_snapshots(before, changed)


def test_storage_comparison_reports_representative_measurement_gate() -> None:
    before = _measurement()
    after = replace(
        before,
        observed_at=NOW + timedelta(hours=7),
        raw_message_count=100_100,
        canonical_event_count=100_100,
    )

    comparison = compare_storage_snapshots(_snapshot(before), _snapshot(after))

    assert comparison["measurement_gate"] == {
        "minimum_elapsed_seconds": 21_600,
        "elapsed_satisfied": True,
        "minimum_raw_messages": 100_000,
        "raw_volume_satisfied": True,
        "representative_thresholds_satisfied": True,
        "index_scan_evidence_usable": True,
        "raw_representation_evidence_usable": True,
        "operator_active_market_review_required": True,
    }
    extrapolation = comparison["observed_rate_extrapolation"]
    assert isinstance(extrapolation, dict)
    assert extrapolation["representative_thresholds_satisfied"] is True


def test_storage_contrast_is_release_bound_and_keeps_operator_review_explicit(
    tmp_path: Path,
) -> None:
    baseline_before_measurement = _measurement()
    baseline_after_measurement = replace(
        baseline_before_measurement,
        observed_at=NOW + timedelta(hours=7),
        database_bytes=200_010_000,
        raw_message_count=100_100,
        canonical_event_count=100_100,
        relations=(
            RelationStorage("canonical", "events", 100_100, 40_001_000, 20_000_800, 70_002_000),
            RelationStorage("raw", "market_messages", 100_100, 50_000_500, 20_000_400, 80_001_000),
        ),
    )
    baseline = build_storage_comparison_artifact(
        _snapshot(baseline_before_measurement),
        _snapshot(baseline_after_measurement),
    )

    candidate_before_measurement = replace(
        _coded_measurement(
            observed_at=NOW + timedelta(hours=8),
            legacy_rows=100_100,
            changed_field_rows=0,
        ),
        database_bytes=200_010_000,
        relations=baseline_after_measurement.relations,
    )
    candidate_after_measurement = replace(
        _coded_measurement(
            observed_at=NOW + timedelta(hours=15),
            legacy_rows=100_100,
            changed_field_rows=100_000,
        ),
        database_bytes=350_010_000,
        relations=(
            RelationStorage("canonical", "events", 200_100, 80_001_000, 40_000_800, 140_002_000),
            RelationStorage("raw", "market_messages", 200_100, 68_000_500, 26_000_400, 110_001_000),
        ),
    )
    candidate_image = "syd.ocir.io/example/qtrad@sha256:" + "c" * 64
    candidate = build_storage_comparison_artifact(
        _snapshot(candidate_before_measurement, application_image=candidate_image),
        _snapshot(candidate_after_measurement, application_image=candidate_image),
    )

    contrast = build_storage_contrast_artifact(baseline, candidate)
    contrast_path = tmp_path / "contrast.json"
    write_storage_evidence_artifact(contrast_path, contrast)

    assert contrast.artifact_kind == "STORAGE_CONTRAST"
    assert load_storage_evidence_artifact(contrast_path) == contrast
    assert contrast.payload["automated_thresholds_satisfied"] is True
    assert contrast.payload["operator_active_market_reviews_required"] is True
    assert contrast.payload["storage_decision_accepted"] is False
    assert contrast.payload["baseline_new_representation"] == "PRE_MARKER_SCHEMA"
    assert contrast.payload["candidate_new_representation"] == "CHANGED_FIELDS"
    bytes_per_raw = contrast.payload["bytes_per_raw_message"]
    assert isinstance(bytes_per_raw, dict)
    assert bytes_per_raw["raw_relation"] == {
        "baseline": "800.000",
        "candidate": "300.000",
        "candidate_change": "-500.000",
        "candidate_reduction_percent": "62.500",
    }
    assert bytes_per_raw["raw_and_canonical_relations"] == {
        "baseline": "1500.000",
        "candidate": "1000.000",
        "candidate_change": "-500.000",
        "candidate_reduction_percent": "33.333",
    }

    same_image_candidate = build_storage_comparison_artifact(
        _snapshot(candidate_before_measurement),
        _snapshot(candidate_after_measurement),
    )
    with pytest.raises(ValueError, match="distinct immutable application images"):
        build_storage_contrast_artifact(baseline, same_image_candidate)

    short_candidate = build_storage_comparison_artifact(
        _snapshot(candidate_before_measurement, application_image=candidate_image),
        _snapshot(
            replace(
                candidate_after_measurement,
                observed_at=NOW + timedelta(hours=9),
            ),
            application_image=candidate_image,
        ),
    )
    with pytest.raises(ValueError, match="candidate storage comparison did not pass"):
        build_storage_contrast_artifact(baseline, short_candidate)

    rollback_after = replace(
        candidate_after_measurement,
        raw_payload_representation_counts=(
            RawPayloadRepresentationCount(
                RawPayloadRepresentation.LEGACY_UNCLASSIFIED,
                100_101,
            ),
            RawPayloadRepresentationCount(
                RawPayloadRepresentation.CHANGED_FIELDS,
                99_999,
            ),
        ),
    )
    rollback_candidate = build_storage_comparison_artifact(
        _snapshot(candidate_before_measurement, application_image=candidate_image),
        _snapshot(rollback_after, application_image=candidate_image),
    )
    with pytest.raises(ValueError, match="non-changed-field raw rows"):
        build_storage_contrast_artifact(baseline, rollback_candidate)


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
    payload.pop("raw_payload_representation_column_present")
    payload.pop("raw_payload_representations")
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


def test_version_two_storage_snapshot_remains_hash_verified_and_readable(tmp_path: Path) -> None:
    current_path = tmp_path / "current.json"
    write_storage_snapshot(current_path, _snapshot())
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    payload.pop("raw_payload_representation_column_present")
    payload.pop("raw_payload_representations")
    identity = {key: value for key, value in payload.items() if key != "snapshot_sha256"}
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    legacy_path = tmp_path / "version-two.json"
    legacy_path.write_text(json.dumps(payload), encoding="utf-8")

    legacy = load_storage_snapshot(legacy_path)

    assert legacy.schema_version == 2
    assert legacy.raw_payload_representation_column_present is None
    assert legacy.raw_payload_representations == ()


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
