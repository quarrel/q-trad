import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qtrad import __main__ as cli
from qtrad.adapters.parquet.foundation import ParquetFoundationArtifactStore
from qtrad.adapters.parquet.observations import ParquetObservationStore
from qtrad.application.walk_forward import build_expanding_folds, build_zero_return_forecasts
from qtrad.domain.events import JsonValue
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.foundation_bundle import (
    load_foundation_bundle,
    load_foundation_config,
    persist_foundation_bundle,
    verify_foundation_bundle,
)
from qtrad.runtime.settings import Settings
from tests.test_r1_walk_forward import _config, _panel, _targets


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _evidence() -> dict[str, JsonValue]:
    return {
        "availability_delay_report": {"selected_lag_seconds": 60.0},
        "revision_delay_report": {"eligible_correction_count": 0},
        "data_gaps": [],
        "source_active_intervals": {
            "fx:aud-usd": [["2026-07-01T11:00:00+00:00", "2026-07-01T15:00:00+00:00"]]
        },
        "lineage_summary": {"row_count": 0},
        "observation_bounds": {
            "interval_start": "2026-07-01T11:00:00+00:00",
            "interval_end": "2026-07-01T15:00:00+00:00",
        },
    }


async def _bundle(tmp_path: Path):
    clock = FixedClock(datetime(2026, 7, 2, tzinfo=UTC))
    observations = ObservationDataset.create(
        (),
        configuration={
            "fixture": "r1.e",
            "interval_start": "2026-07-01T11:00:00+00:00",
            "interval_end": "2026-07-01T15:00:00+00:00",
        },
    )
    observation_manifest = await ParquetObservationStore(tmp_path, clock).write_observations(
        observations,
        application_version="test",
        image_identity="test@sha256:" + "1" * 64,
        source_snapshot={
            "kind": "verified-capture-snapshot",
            "import_sha256": "a" * 64,
        },
        build_evidence=_evidence(),
    )
    configuration = replace(_config(), observation_dataset_id=observations.dataset_id)
    targets = _targets(configuration)
    panel = _panel(configuration, targets)
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
        availability_evidence=_evidence(),
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
            availability_evidence=_evidence(),
            application_version="test",
            image_identity="test@sha256:" + "1" * 64,
        )


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
