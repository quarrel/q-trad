"""Regression tests for authenticated R2 verification boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

import qtrad.runtime.r2_verification as verification
from qtrad.adapters.parquet.r2 import R2FeatureManifest
from qtrad.domain.folds import Fold, membership_hash
from qtrad.runtime.r2_verification import (
    _image_identity_manifest as _production_image_identity_manifest,
)
from qtrad.runtime.r2_verification import (
    _stage_replay_inputs,
    _synthetic_pipeline_inputs,
    _validate_representative_capture_v4,
    _validate_representative_fold_layout,
    runtime_identities,
    verify_oof_bundle,
)


def test_image_digest_environment_is_not_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QTRAD_IMAGE_DIGEST", "sha256:" + "f" * 64)
    identities = runtime_identities()
    assert "image:sha256:" + "0" * 64 in identities["application_identity"]


def test_image_identity_manifest_digest_is_authenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = next(
        part.removeprefix("git:")
        for part in runtime_identities()["application_identity"].split("+")
        if part.startswith("git:")
    )
    unsigned = {
        "contract": "qtrad-runtime-image-identity-v1",
        "schema_version": 1,
        "application_commit": commit,
        "image_digest": "sha256:" + "0" * 64,
    }
    payload = {**unsigned, "manifest_sha256": "0" * 64}
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    monkeypatch.setattr(
        verification,
        "_image_identity_manifest",
        lambda: _production_image_identity_manifest(path),  # type: ignore[assignment]
    )
    with pytest.raises(RuntimeError, match="manifest digest"):
        runtime_identities()


def test_replay_input_paths_are_relative_and_portable(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "foundation").mkdir()
    foundation = research / "foundation" / "bundle.json"
    foundation.write_text("{}")
    (research / "unrelated.txt").write_text("must not be staged")
    features: dict[str, Path] = {}
    for name in ("L0", "L1", "P0", "P1"):
        directory = research / name
        directory.mkdir()
        features[name] = directory / "manifest.json"
        features[name].write_text("{}")
    experiment = tmp_path / "experiment.json"
    experiment.write_text("{}")
    payload = _stage_replay_inputs(
        output=tmp_path / "bundle",
        research_root=research,
        paths={"foundation": foundation, "experiment": experiment, **features},
    )
    assert payload["root"] == "."
    children = payload["children"]
    assert isinstance(children, dict)
    for child in children.values():
        if not isinstance(child, dict):
            raise AssertionError("staged child is not an object")
        child_payload = cast(dict[str, object], child)
        child_path = child_payload["path"]
        child_root = child_payload["root"]
        assert isinstance(child_path, str)
        assert isinstance(child_root, str)
        assert not Path(child_path).is_absolute()
        assert ".." not in Path(child_path).parts
        assert not Path(child_root).is_absolute()
    assert not (tmp_path / "bundle" / "replay-inputs" / "research" / "unrelated.txt").exists()


def test_representative_fold_layout_preserves_dependency_embargo() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=1000)
    holdout = (start + timedelta(minutes=800), end)
    folds = tuple(
        Fold(
            fold_id=f"fold-{index}",
            training_start=start,
            training_cutoff=start + timedelta(minutes=500 + index * 100),
            validation_start=start + timedelta(minutes=505 + index * 100),
            validation_end=start + timedelta(minutes=605 + index * 100),
            embargo_end=start + timedelta(minutes=510 + index * 100),
            training_target_ids=(),
            validation_target_ids=(),
            holdout_excluded=True,
            membership_hash=membership_hash((), ()),
        )
        for index in range(3)
    )
    _validate_representative_fold_layout(folds, start, end, holdout)


def test_synthetic_manifests_use_strict_v2_parquet_contract() -> None:
    _, _, _, manifests = _synthetic_pipeline_inputs()
    for payload in manifests.values():
        assert payload["contract"] == R2FeatureManifest.CONTRACT
        assert payload["schema_version"] == R2FeatureManifest.SCHEMA_VERSION


def test_representative_admission_rejects_non_capture_v4_inputs() -> None:
    verified, experiment, _, _ = _synthetic_pipeline_inputs()
    with pytest.raises(ValueError, match="IG_NATIVE_CAPTURE"):
        _validate_representative_capture_v4(verified, experiment)


def test_oof_verification_dispatches_representative_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replayed = False

    def replay(_: Path) -> None:
        nonlocal replayed
        replayed = True

    monkeypatch.setattr(verification, "verify_r2_oof_bundle", lambda _: object())
    monkeypatch.setattr(
        verification,
        "_oof_child_payload",
        lambda *_: {"run_kind": "REPRESENTATIVE"},
    )
    monkeypatch.setattr(verification, "_replay_representative_oof", replay)
    assert verify_oof_bundle(Path("manifest.json")) is not None
    assert replayed


def test_replay_staging_rejects_ancestor_symlinks(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    foundation = research / "foundation.json"
    foundation.write_text("{}")
    paths: dict[str, Path] = {"foundation": foundation}
    for name in ("L0", "L1", "P0", "P1"):
        feature = research / f"{name}.json"
        feature.write_text("{}")
        paths[name] = feature
    experiment = tmp_path / "experiment.json"
    experiment.write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _stage_replay_inputs(
            output=link / "bundle",
            research_root=research,
            paths={"experiment": experiment, **paths},
        )
