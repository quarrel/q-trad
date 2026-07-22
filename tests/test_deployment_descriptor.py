from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from qtrad.runtime.deployment import load_capture_deployment_descriptor

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_capture_v4_deployment_descriptor_matches_its_universe() -> None:
    descriptor = load_capture_deployment_descriptor(
        REPOSITORY_ROOT / "config/capture-v4-deployment.toml",
        repository_root=REPOSITORY_ROOT,
    )

    assert descriptor.name == "capture-v4"
    assert descriptor.universe_instrument_count == 23
    assert descriptor.universe_configuration_hash == (
        "eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078"
    )


def test_deployment_descriptor_rejects_a_mismatched_universe_hash(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    source_universe = REPOSITORY_ROOT / "config/capture-v4.toml"
    (config / source_universe.name).write_bytes(source_universe.read_bytes())
    source_descriptor = REPOSITORY_ROOT / "config/capture-v4-deployment.toml"
    document = tomllib.loads(source_descriptor.read_text())
    rendered = source_descriptor.read_text().replace(
        str(document["universe_configuration_hash"]), "a" * 64
    )
    descriptor_path = config / source_descriptor.name
    descriptor_path.write_text(rendered)

    with pytest.raises(ValueError, match="hash does not match"):
        load_capture_deployment_descriptor(descriptor_path, repository_root=tmp_path)


@pytest.mark.parametrize(
    "universe_file",
    ["../capture-v4.toml", "/etc/qtrad/universe/active.toml", "nested/capture-v4.toml"],
)
def test_deployment_descriptor_rejects_an_unsafe_universe_path(
    tmp_path: Path, universe_file: str
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    descriptor = (REPOSITORY_ROOT / "config/capture-v4-deployment.toml").read_text()
    descriptor_path = config / "candidate-deployment.toml"
    descriptor_path.write_text(
        descriptor.replace(
            'universe_file = "config/capture-v4.toml"', f'universe_file = "{universe_file}"'
        )
    )

    with pytest.raises(ValueError, match="universe file"):
        load_capture_deployment_descriptor(descriptor_path, repository_root=tmp_path)
