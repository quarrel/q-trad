import json
from dataclasses import replace
from pathlib import Path

import pytest

from qtrad import __main__ as cli
from qtrad.application.foundation_bundle import build_foundation_bundle
from qtrad.application.walk_forward import build_expanding_folds, build_zero_return_forecasts
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.foundation_bundle import (
    load_foundation_bundle,
    load_foundation_config,
    write_foundation_bundle,
)
from tests.test_r1_walk_forward import _config, _panel, _targets


def _bundle():
    observations = ObservationDataset.create((), configuration={"fixture": "r1.e"})
    configuration = replace(_config(), observation_dataset_id=observations.dataset_id)
    targets = _targets(configuration)
    panel = _panel(configuration, targets)
    folds = build_expanding_folds(targets, configuration)
    forecasts = build_zero_return_forecasts(panel, targets, folds, configuration)
    return build_foundation_bundle(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
    )


def test_bundle_verifies_children_and_round_trips_without_model_code(tmp_path: Path) -> None:
    bundle = _bundle()
    path = tmp_path / "foundation.json"
    configuration_path = tmp_path / "configuration.json"
    configuration_path.write_text(
        json.dumps(bundle.configuration.as_json()), encoding="utf-8"
    )

    write_foundation_bundle(path, bundle)
    loaded = load_foundation_bundle(path)
    loaded_configuration = load_foundation_config(configuration_path)

    assert loaded == bundle
    assert loaded.bundle_id == bundle.bundle_id
    assert loaded_configuration == bundle.configuration
    with pytest.raises(ValueError, match="new regular file"):
        write_foundation_bundle(path, bundle)


def test_tampering_with_a_child_invalidates_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle()
    path = tmp_path / "foundation.json"
    tampered_path = tmp_path / "tampered.json"
    write_foundation_bundle(path, bundle)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["targets"]["dataset_id"] = "0" * 64
    tampered_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_foundation_bundle(tampered_path)


def test_foundation_verification_is_wired_to_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _bundle()
    path = tmp_path / "foundation.json"
    write_foundation_bundle(path, bundle)

    parsed = cli.build_parser().parse_args(
        ["research", "foundation", "verify", "--bundle", str(path)]
    )
    assert parsed.research_command == "foundation"
    cli._verify_foundation_bundle(path)
    output = json.loads(capsys.readouterr().out)
    assert output["bundle_id"] == bundle.bundle_id
    assert output["row_counts"]["forecasts"] == len(bundle.forecasts.rows)
