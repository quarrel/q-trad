from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from qtrad.__main__ import build_parser
from qtrad.application import provider_history as provider_history_application
from qtrad.application.ibkr_results import build_ibkr_historical_result_artifact
from qtrad.application.provider_history import (
    _observation_values,
    build_provider_history_dataset,
)
from qtrad.domain.provider_history import (
    ProviderHistoricalDataset,
    ProviderHistoricalObservation,
    parse_declared_delay,
)
from qtrad.runtime import provider_history as provider_history_runtime
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
    parquet_path = next((manifest.parent / "observations").rglob("*.parquet"))
    parquet_path.write_bytes(parquet_path.read_bytes() + b"mutation")
    with pytest.raises(ValueError, match=r"Parquet.*bytes"):
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


def test_provider_history_drops_successful_chunks_for_ineligible_instrument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    monkeypatch.setattr(
        provider_history_application,
        "_provider_history_eligible_instruments",
        lambda _artifact: frozenset({"different-instrument"}),
    )

    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )

    assert dataset.rows == ()


def test_provider_history_publishes_26_week_daily_partitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    source_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    base_dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    base_row = base_dataset.rows[0]
    rows: list[ProviderHistoricalObservation] = []
    bounds: dict[tuple[str, date], int] = {}
    for day_offset in range(26 * 7):
        start = base_row.interval_start + timedelta(days=day_offset)
        end = start + timedelta(minutes=1)
        values = _observation_values(base_row)
        values.update(
            {
                "interval_start": start,
                "interval_end": end,
                "available_at": end + timedelta(minutes=5),
            }
        )
        rows.append(ProviderHistoricalObservation.create(**values))
        bounds[(base_row.instrument_id, start.date())] = 1
    scaled_dataset = ProviderHistoricalDataset.create(
        rows=tuple(rows),
        contract_selection_sha256=base_dataset.contract_selection_sha256,
        plan_sha256=base_dataset.plan_sha256,
        runtime_sha256=base_dataset.runtime_sha256,
        aggregate_sha256=base_dataset.aggregate_sha256,
        availability_policy=base_dataset.availability_policy,
    )
    monkeypatch.setattr(
        provider_history_runtime,
        "provider_history_partition_row_bounds",
        lambda _artifact: bounds,
    )
    monkeypatch.setattr(
        provider_history_runtime,
        "build_provider_history_dataset",
        lambda _artifact, availability_delay: scaled_dataset,
    )

    manifest = publish_provider_history(
        tmp_path / "scaled-provider",
        source_manifest=source_manifest,
        source_artifact=artifact,
        dataset=scaled_dataset,
    )
    verified = verify_provider_history(manifest)

    partitions = list((manifest.parent / "observations").rglob("*.parquet"))
    assert len(partitions) == 26 * 7
    assert verified == scaled_dataset
