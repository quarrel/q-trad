import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from qtrad import __main__ as cli
from qtrad.adapters.parquet.foundation import ParquetFoundationArtifactStore
from qtrad.adapters.parquet.observations import ParquetObservationStore
from qtrad.application.foundation import build_asof_panel, build_frozen_targets
from qtrad.application.walk_forward import build_expanding_folds, build_zero_return_forecasts
from qtrad.domain.events import JsonValue
from qtrad.domain.foundation import AvailabilityBasis, PanelDataset
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.research import (
    ObservationDataset,
    ObservationRow,
    build_availability_delay_report,
    build_revision_delay_report,
)
from qtrad.runtime.foundation_bundle import (
    load_foundation_bundle,
    load_foundation_config,
    persist_foundation_bundle,
    verify_foundation_bundle,
    verify_foundation_configuration_evidence,
    verify_observation_build_evidence,
)
from qtrad.runtime.settings import Settings
from tests.test_r1_walk_forward import _config


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _observations() -> ObservationDataset:
    config = _config()
    source_start = config.required_observation_start
    source_end = config.required_observation_end
    rows: list[ObservationRow] = []
    interval_end = source_start + timedelta(minutes=1)
    position = 1
    while interval_end <= source_end:
        available_at = interval_end + timedelta(seconds=5)
        rows.append(
            ObservationRow(
                event_id=uuid4(),
                stream_id="market-bar:fx:aud-usd:MID",
                stream_version=position,
                event_type="MarketBarClosed",
                event_time=interval_end,
                received_at=available_at,
                persisted_at=available_at,
                global_position=position,
                instrument_id="fx:aud-usd",
                basis=PriceBasis.MID,
                interval_start=interval_end - timedelta(minutes=1),
                interval_end=interval_end,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100") + Decimal(position) / Decimal("100"),
                sample_count=1,
                revision=1,
                provenance=BarProvenance.QUOTE_DERIVED,
                quality=DataQuality.HEALTHY,
                source_provider="ig",
                source_environment="demo",
                source_external_id="AUDUSD",
            )
        )
        interval_end += timedelta(minutes=1)
        position += 1
    return ObservationDataset.create(
        rows,
        configuration={
            "universe_name": "capture-v4",
            "universe_configuration_hash": "b" * 64,
            "ordered_instruments": ["fx:aud-usd"],
            "interval_start": source_start.isoformat(),
            "interval_end": source_end.isoformat(),
        },
        source_dataset_ids=("a" * 64,),
        selection_policies={
            "provenance": "QUOTE_DERIVED",
            "availability_basis": "persisted_at",
            "canonical_lineage": "GLOBAL_POSITION_EXACT",
        },
    )


def _evidence(
    observations: ObservationDataset, calibration_range: tuple[datetime, datetime]
) -> dict[str, JsonValue]:
    availability = build_availability_delay_report(
        observations.rows,
        calibration_start=calibration_range[0],
        calibration_end=calibration_range[1],
        configured_percentile=0.95,
        safety_margin=timedelta(0),
        grid_resolution=timedelta(minutes=1),
    )
    revisions = build_revision_delay_report(
        observations.rows,
        calibration_start=calibration_range[0],
        calibration_end=calibration_range[1],
    )
    event_counts: dict[str, JsonValue] = {"MarketBarClosed": len(observations.rows)}
    evidence: dict[str, JsonValue] = {
        "availability_delay_report": availability.as_json(),
        "revision_delay_report": revisions.as_json(),
        "data_gaps": [],
        "source_active_intervals": {
            "fx:aud-usd": [
                [
                    observations.configuration["interval_start"],
                    observations.configuration["interval_end"],
                ]
            ]
        },
        "lineage_summary": {
            "row_count": len(observations.rows),
            "event_type_counts": event_counts,
            "minimum_global_position": 1,
            "maximum_global_position": len(observations.rows),
        },
        "observation_bounds": {
            "interval_start": observations.configuration["interval_start"],
            "interval_end": observations.configuration["interval_end"],
        },
    }
    return evidence


async def _bundle(tmp_path: Path):
    clock = FixedClock(datetime(2026, 7, 2, tzinfo=UTC))
    observations = _observations()
    base_configuration = _config()
    calibration_range = base_configuration.feature_lag_calibration_range
    evidence = _evidence(observations, calibration_range)
    observation_manifest = await ParquetObservationStore(tmp_path, clock).write_observations(
        observations,
        application_version="test",
        image_identity="test@sha256:" + "1" * 64,
        source_snapshot={
            "kind": "verified-capture-snapshot",
            "import_sha256": "a" * 64,
            "universe_name": "capture-v4",
            "universe_hash": "b" * 64,
        },
        build_evidence=evidence,
    )
    configuration = replace(
        base_configuration,
        observation_dataset_id=observations.dataset_id,
        availability_basis=AvailabilityBasis.PERSISTED_AT,
        feature_lag_calibration_range=calibration_range,
        feature_lag_safety_margin=timedelta(0),
    )
    panel = build_asof_panel(
        observations,
        configuration,
        source_active_intervals={
            "fx:aud-usd": (
                (
                    datetime.fromisoformat(str(observations.configuration["interval_start"])),
                    datetime.fromisoformat(str(observations.configuration["interval_end"])),
                ),
            )
        },
    )
    targets = build_frozen_targets(
        observations, configuration, horizons=configuration.target_horizons
    )
    folds = build_expanding_folds(targets, configuration)
    forecasts = build_zero_return_forecasts(panel, targets, folds, configuration)
    path = tmp_path / "foundation.json"
    bundle = await persist_foundation_bundle(
        root=tmp_path,
        clock=clock,
        output_path=path,
        observation_manifest=observation_manifest,
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        availability_evidence=evidence,
        application_version="test",
        image_identity="test@sha256:" + "1" * 64,
    )
    return bundle, path, clock, configuration


@pytest.mark.asyncio
async def test_bundle_is_thin_and_children_verify_without_model_code(tmp_path: Path) -> None:
    bundle, path, clock, configuration = await _bundle(tmp_path)
    configuration_path = tmp_path / "configuration.json"
    configuration_path.write_text(json.dumps(configuration.as_json()), encoding="utf-8")

    loaded = load_foundation_bundle(path)
    verified = await verify_foundation_bundle(root=tmp_path, bundle_path=path, clock=clock)
    loaded_configuration = load_foundation_config(configuration_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert loaded == bundle
    assert verified.bundle == bundle
    assert loaded_configuration == configuration
    assert set(payload["children"]) == {
        "configuration",
        "observations",
        "availability",
        "panel",
        "targets",
        "folds",
        "forecasts",
    }
    assert all("rows" not in child for child in payload["children"].values())
    manifests_before = {item.name for item in (tmp_path / "manifests").glob("*.json")}
    with pytest.raises(ValueError, match="new regular file"):
        await persist_foundation_bundle(
            root=tmp_path,
            clock=clock,
            output_path=path,
            observation_manifest=await ParquetObservationStore(tmp_path, clock).read_manifest(
                bundle.observations.manifest_id
            ),
            configuration=configuration,
            observations=verified.observations,
            panel=verified.panel,
            targets=verified.targets,
            folds=verified.folds,
            forecasts=verified.forecasts,
            availability_evidence=_evidence(
                verified.observations, configuration.feature_lag_calibration_range
            ),
            application_version="test",
            image_identity="test@sha256:" + "1" * 64,
        )
    assert {item.name for item in (tmp_path / "manifests").glob("*.json")} == manifests_before


@pytest.mark.asyncio
async def test_tampering_with_a_child_invalidates_the_bundle(tmp_path: Path) -> None:
    bundle, path, clock, _ = await _bundle(tmp_path)
    manifest = await ParquetFoundationArtifactStore(tmp_path, clock).read_manifest(
        bundle.targets.manifest_id
    )
    child_path = tmp_path / manifest.file
    child_path.write_bytes(child_path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="file hash"):
        await verify_foundation_bundle(root=tmp_path, bundle_path=path, clock=clock)


@pytest.mark.asyncio
async def test_verifier_replays_children_instead_of_only_authenticating_them(
    tmp_path: Path,
) -> None:
    bundle, path, clock, configuration = await _bundle(tmp_path)
    verified = await verify_foundation_bundle(root=tmp_path, bundle_path=path, clock=clock)
    changed_row = replace(verified.panel.rows[0], close=Decimal("999"))
    changed_panel = PanelDataset.create(
        (changed_row, *verified.panel.rows[1:]),
        observation_dataset_id=verified.observations.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
    )
    changed_forecasts = build_zero_return_forecasts(
        changed_panel,
        verified.targets,
        verified.folds,
        configuration,
    )
    observation_manifest = await ParquetObservationStore(tmp_path, clock).read_manifest(
        bundle.observations.manifest_id
    )

    with pytest.raises(ValueError, match="deterministic causal replay"):
        await persist_foundation_bundle(
            root=tmp_path,
            clock=clock,
            output_path=tmp_path / "wrong-foundation.json",
            observation_manifest=observation_manifest,
            configuration=configuration,
            observations=verified.observations,
            panel=changed_panel,
            targets=verified.targets,
            folds=verified.folds,
            forecasts=changed_forecasts,
            availability_evidence=_evidence(
                verified.observations, configuration.feature_lag_calibration_range
            ),
            application_version="test",
            image_identity="test@sha256:" + "1" * 64,
        )


@pytest.mark.asyncio
async def test_measured_feature_policy_is_bound_to_authenticated_delay_evidence(
    tmp_path: Path,
) -> None:
    bundle, _, clock, configuration = await _bundle(tmp_path)
    manifest = await ParquetObservationStore(tmp_path, clock).read_manifest(
        bundle.observations.manifest_id
    )
    observations = await ParquetObservationStore(tmp_path, clock).read_observations(
        bundle.observations.manifest_id
    )
    evidence = verify_observation_build_evidence(manifest, observations)
    inconsistent = replace(
        configuration,
        feature_lag_policy="MEASURED",
        selected_feature_lag=timedelta(minutes=2),
    )

    with pytest.raises(ValueError, match="measured feature lag"):
        verify_foundation_configuration_evidence(inconsistent, observations, evidence)

    unsupported_maturity = replace(
        configuration,
        target_revision_policy="MEASURED",
        target_revision_policy_reason=None,
    )
    with pytest.raises(ValueError, match="lacks correction evidence"):
        verify_foundation_configuration_evidence(unsupported_maturity, observations, evidence)


@pytest.mark.asyncio
async def test_late_initial_bar_makes_calibration_immature_at_decision_start(
    tmp_path: Path,
) -> None:
    bundle, _, clock, configuration = await _bundle(tmp_path)
    manifest = await ParquetObservationStore(tmp_path, clock).read_manifest(
        bundle.observations.manifest_id
    )
    observations = await ParquetObservationStore(tmp_path, clock).read_observations(
        bundle.observations.manifest_id
    )
    evidence = verify_observation_build_evidence(manifest, observations)
    calibration_row = next(
        row
        for row in observations.rows
        if evidence.availability_report.calibration_start
        <= row.interval_end
        < evidence.availability_report.calibration_end
    )
    late_at = configuration.range_start + timedelta(minutes=30)
    late_row = replace(calibration_row, received_at=late_at, persisted_at=late_at)
    late_observations = ObservationDataset.create(
        tuple(
            late_row if row.event_id == calibration_row.event_id else row
            for row in observations.rows
        ),
        configuration=observations.configuration,
        source_dataset_ids=observations.source_dataset_ids,
        selection_policies=observations.selection_policies,
    )
    late_report = build_availability_delay_report(
        late_observations.rows,
        calibration_start=evidence.availability_report.calibration_start,
        calibration_end=evidence.availability_report.calibration_end,
        configured_percentile=evidence.availability_report.configured_percentile,
        safety_margin=evidence.availability_report.safety_margin,
        grid_resolution=configuration.grid_resolution,
    )

    with pytest.raises(ValueError, match="availability calibration was not mature"):
        verify_foundation_configuration_evidence(
            configuration,
            late_observations,
            replace(evidence, availability_report=late_report),
        )


@pytest.mark.asyncio
async def test_late_correction_makes_calibration_immature_at_decision_start(
    tmp_path: Path,
) -> None:
    bundle, _, clock, configuration = await _bundle(tmp_path)
    manifest = await ParquetObservationStore(tmp_path, clock).read_manifest(
        bundle.observations.manifest_id
    )
    observations = await ParquetObservationStore(tmp_path, clock).read_observations(
        bundle.observations.manifest_id
    )
    evidence = verify_observation_build_evidence(manifest, observations)
    initial = next(
        row
        for row in observations.rows
        if evidence.revision_report.calibration_start
        <= row.interval_end
        < evidence.revision_report.calibration_end
    )
    late_at = configuration.range_start + timedelta(minutes=30)
    correction = replace(
        initial,
        event_id=uuid4(),
        stream_version=max(row.stream_version for row in observations.rows) + 1,
        event_type="MarketBarCorrected",
        received_at=late_at,
        persisted_at=late_at,
        global_position=max(row.global_position for row in observations.rows) + 1,
        revision=2,
    )
    corrected_observations = ObservationDataset.create(
        (*observations.rows, correction),
        configuration=observations.configuration,
        source_dataset_ids=observations.source_dataset_ids,
        selection_policies=observations.selection_policies,
    )
    late_report = build_revision_delay_report(
        corrected_observations.rows,
        calibration_start=evidence.revision_report.calibration_start,
        calibration_end=evidence.revision_report.calibration_end,
    )

    with pytest.raises(ValueError, match="revision calibration was not mature"):
        verify_foundation_configuration_evidence(
            configuration,
            corrected_observations,
            replace(evidence, revision_report=late_report),
        )


@pytest.mark.asyncio
async def test_observation_verifier_recomputes_delay_evidence(tmp_path: Path) -> None:
    observations = _observations()
    configuration = _config()
    evidence = _evidence(observations, configuration.feature_lag_calibration_range)
    availability = dict(cast(dict[str, JsonValue], evidence["availability_delay_report"]))
    availability["selected_lag_seconds"] = 120.0
    evidence["availability_delay_report"] = availability
    clock = FixedClock(datetime(2026, 7, 2, tzinfo=UTC))
    manifest = await ParquetObservationStore(tmp_path, clock).write_observations(
        observations,
        source_snapshot={
            "kind": "verified-capture-snapshot",
            "import_sha256": "a" * 64,
            "universe_name": "capture-v4",
            "universe_hash": "b" * 64,
        },
        build_evidence=evidence,
    )

    with pytest.raises(ValueError, match="does not match observation rows"):
        verify_observation_build_evidence(manifest, observations)


@pytest.mark.asyncio
async def test_observation_verifier_rejects_rows_outside_the_declared_universe(
    tmp_path: Path,
) -> None:
    observations = _observations()
    first = observations.rows[0]
    outside = replace(
        first,
        stream_id="market-bar:fx:eur-usd:MID",
        instrument_id="fx:eur-usd",
    )
    invalid = ObservationDataset.create(
        (outside, *observations.rows[1:]),
        configuration=observations.configuration,
        source_dataset_ids=observations.source_dataset_ids,
        selection_policies=observations.selection_policies,
    )
    configuration = _config()
    evidence = _evidence(invalid, configuration.feature_lag_calibration_range)
    clock = FixedClock(datetime(2026, 7, 2, tzinfo=UTC))
    manifest = await ParquetObservationStore(tmp_path, clock).write_observations(
        invalid,
        source_snapshot={
            "kind": "verified-capture-snapshot",
            "import_sha256": "a" * 64,
            "universe_name": "capture-v4",
            "universe_hash": "b" * 64,
        },
        build_evidence=evidence,
    )

    with pytest.raises(ValueError, match="outside the declared instrument universe"):
        verify_observation_build_evidence(manifest, invalid)


def test_foundation_config_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = _config().as_json()
    payload["misspelled_feature_lag"] = 60
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown or missing"):
        load_foundation_config(path)


@pytest.mark.asyncio
async def test_foundation_verification_is_wired_to_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle, path, clock, _ = await _bundle(tmp_path)

    parsed = cli.build_parser().parse_args(
        ["research", "foundation", "verify", "--bundle", str(path)]
    )
    assert parsed.research_command == "foundation"
    await cli._verify_foundation_bundle(
        Settings(research_root=tmp_path),
        clock,
        path,
    )
    output = json.loads(capsys.readouterr().out)
    assert output["bundle_id"] == bundle.bundle_id
    assert output["children"]["forecasts"]["rows"] == bundle.forecasts.row_count
