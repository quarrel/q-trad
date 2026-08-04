from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from qtrad.__main__ import build_parser
from qtrad.application.ibkr_results import build_ibkr_historical_result_artifact
from qtrad.application.provider_history import build_provider_history_dataset
from qtrad.domain.provider_history import parse_declared_delay
from qtrad.runtime.ibkr_results import write_ibkr_historical_result
from qtrad.runtime.provider_history import publish_provider_history, verify_provider_history
from tests.test_ibkr_historical_results import _build_fixture


def _published_provider_history(tmp_path: Path):
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    provider_manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=artifact,
        dataset=dataset,
    )
    return artifact, dataset, provider_manifest


def test_provider_history_builds_declared_availability_and_distinct_lineage() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)

    five_minutes = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    six_minutes = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=6),
    )

    assert len(five_minutes.rows) == 1
    row = five_minutes.rows[0]
    assert row.available_at == row.interval_end + timedelta(minutes=5)
    assert row.availability_delay == "PT5M"
    assert "received_at" not in row.as_json_value()
    assert "persisted_at" not in row.as_json_value()
    assert five_minutes.dataset_sha256 != six_minutes.dataset_sha256
    assert five_minutes.rows[0].observation_sha256 != six_minutes.rows[0].observation_sha256
    assert parse_declared_delay("PT5M") == timedelta(minutes=5)


def test_provider_history_closure_replays_from_embedded_result(tmp_path: Path) -> None:
    _, dataset, manifest = _published_provider_history(tmp_path)

    verified = verify_provider_history(manifest)

    assert verified == dataset
    assert verified.rows[0].schedule_evidence["schedule_state"] == "INACTIVE"


def test_provider_history_rejects_parquet_mutation_and_missing_child(tmp_path: Path) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
    parquet_path = manifest.parent / "observations" / "observations.parquet"
    parquet_path.write_bytes(parquet_path.read_bytes() + b"mutation")
    with pytest.raises(ValueError, match="Parquet bytes"):
        verify_provider_history(manifest)

    second_root = tmp_path / "second"
    second_root.mkdir()
    _, _, second_manifest = _published_provider_history(second_root)
    child = second_manifest.parent / "source-result" / "plan.json"
    child.unlink()
    with pytest.raises((FileNotFoundError, ValueError), match=r"plan|closure|child"):
        verify_provider_history(second_manifest)


def test_provider_history_cli_round_trip_arguments() -> None:
    parser = build_parser()

    build_args = parser.parse_args(
        [
            "research",
            "observations",
            "build-provider-history",
            "--historical-result",
            "/tmp/result/manifest.json",
            "--availability-delay",
            "PT5M",
            "--output",
            "/tmp/provider",
        ]
    )
    verify_args = parser.parse_args(
        [
            "research",
            "observations",
            "verify-provider-history",
            "--manifest",
            "/tmp/provider/manifest.json",
        ]
    )

    assert build_args.observations_command == "build-provider-history"
    assert build_args.availability_delay == timedelta(minutes=5)
    assert verify_args.observations_command == "verify-provider-history"
