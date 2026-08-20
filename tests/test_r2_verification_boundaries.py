"""Regression tests for authenticated R2 verification boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import qtrad.runtime.r2_verification as verification
from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore, R2FeatureManifest
from qtrad.domain.folds import Fold, membership_hash
from qtrad.ports.clock import Clock
from qtrad.runtime.r2_verification import (
    _descriptor_payload,
    _materialise_synthetic_feature_manifests,
    _synthetic_pipeline_inputs,
    _validate_representative_capture_v4,
    _validate_representative_fold_layout,
    execution_provenance,
    numerical_environment,
    runtime_identities,
    verify_oof_bundle,
)
from qtrad.runtime.r2_verification import (
    _image_identity_manifest as _production_image_identity_manifest,
)


def test_runtime_provenance_is_split_and_inspectable() -> None:
    execution = execution_provenance()
    numerical = numerical_environment()
    assert set(execution) == {"git_commit", "image_digest", "application_identity"}
    assert set(numerical) == {"python_version", "numpy_version", "sklearn_version"}
    assert len(execution["git_commit"]) == 40
    assert execution["image_digest"].startswith("sha256:")
    assert numerical["python_version"]
    assert runtime_identities()["application_identity"] == execution["application_identity"]


def test_image_digest_environment_is_not_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QTRAD_IMAGE_DIGEST", "sha256:" + "f" * 64)
    identities = runtime_identities()
    assert "image:sha256:" + "0" * 64 in identities["application_identity"]


def test_descriptor_identity_excludes_execution_provenance_but_binds_science() -> None:
    _, experiment, _ = _synthetic_pipeline_inputs()
    identities = {
        "application_identity": "qtrad-test+git:" + "1" * 40,
        "image_identity": "sha256:" + "2" * 64,
        "python_identity": "3.13.0",
        "numpy_identity": "2.0.0",
        "sklearn_identity": "1.6.0",
    }
    descriptor = _descriptor_payload(
        foundation_bundle_id="a" * 64,
        experiment=experiment,
        feature_names=("L0", "L1"),
        run_kind="SYNTHETIC",
        identities=identities,
    )
    changed_provenance = dict(identities)
    changed_provenance.update(
        {
            "application_identity": "qtrad-test+git:" + "f" * 40,
            "image_identity": "sha256:" + "3" * 64,
            "python_identity": "3.14.0",
            "numpy_identity": "2.1.0",
            "sklearn_identity": "1.7.0",
        }
    )
    drifted = _descriptor_payload(
        foundation_bundle_id="a" * 64,
        experiment=experiment,
        feature_names=("L0", "L1"),
        run_kind="SYNTHETIC",
        identities=changed_provenance,
    )
    assert descriptor["descriptor_id"] == drifted["descriptor_id"]
    assert descriptor["application_identity"] != drifted["application_identity"]
    assert descriptor["numpy_identity"] != drifted["numpy_identity"]

    model_changed = _descriptor_payload(
        foundation_bundle_id="a" * 64,
        experiment=replace(experiment, model_selection_policy="DIFFERENT_MODEL_SELECTION_V2"),
        feature_names=("L0", "L1"),
        run_kind="SYNTHETIC",
        identities=identities,
    )
    feature_changed = _descriptor_payload(
        foundation_bundle_id="a" * 64,
        experiment=experiment,
        feature_names=("L0", "P1"),
        run_kind="SYNTHETIC",
        identities=identities,
    )
    assert descriptor["descriptor_id"] != model_changed["descriptor_id"]
    assert descriptor["descriptor_id"] != feature_changed["descriptor_id"]


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


def test_dynamic_checkout_accepts_stale_legacy_image_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsigned = {
        "contract": "qtrad-image-identity-v1",
        "schema_version": 1,
        "application_commit": "f" * 40,
        "image_digest": "sha256:" + "0" * 64,
    }
    payload = {
        **unsigned,
        "manifest_sha256": sha256(verification.canonical_bytes(unsigned)).hexdigest(),
    }
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    monkeypatch.setattr(
        verification,
        "_image_identity_manifest",
        lambda: _production_image_identity_manifest(path),  # type: ignore[assignment]
    )

    identities = execution_provenance()

    assert identities["git_commit"] != unsigned["application_commit"]
    assert identities["image_digest"] == unsigned["image_digest"]


def test_deployment_image_contract_still_binds_application_commit(
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
        "application_commit": "f" * 40 if commit != "f" * 40 else "e" * 40,
        "image_digest": "sha256:" + "0" * 64,
    }
    payload = {
        **unsigned,
        "manifest_sha256": sha256(verification.canonical_bytes(unsigned)).hexdigest(),
    }
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    monkeypatch.setattr(
        verification,
        "_image_identity_manifest",
        lambda: _production_image_identity_manifest(path),  # type: ignore[assignment]
    )

    with pytest.raises(RuntimeError, match="manifest commit differs"):
        execution_provenance()


def test_image_identity_manifest_rejects_unsupported_contract(tmp_path: Path) -> None:
    unsigned = {
        "contract": "qtrad-unsupported-image-identity-v1",
        "schema_version": 1,
        "application_commit": "f" * 40,
        "image_digest": "sha256:" + "0" * 64,
    }
    payload = {
        **unsigned,
        "manifest_sha256": sha256(verification.canonical_bytes(unsigned)).hexdigest(),
    }
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(RuntimeError, match="contract is unsupported"):
        _production_image_identity_manifest(path)


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
            validation_end=start + timedelta(minutes=600 + index * 100),
            embargo_end=start + timedelta(minutes=505 + index * 100),
            training_target_ids=(),
            validation_target_ids=(),
            holdout_excluded=True,
            membership_hash=membership_hash((), ()),
        )
        for index in range(3)
    )
    _validate_representative_fold_layout(folds, start, end, holdout, embargo=timedelta(minutes=5))
    leaking = (*folds[:-1], replace(folds[-1], validation_end=holdout[0] + timedelta(minutes=5)))
    with pytest.raises(ValueError, match="50/30/20"):
        _validate_representative_fold_layout(
            leaking, start, end, holdout, embargo=timedelta(minutes=5)
        )


def test_synthetic_manifests_are_real_independently_verified_parquet(
    tmp_path: Path,
) -> None:
    _, experiment, datasets = _synthetic_pipeline_inputs()
    paths = _materialise_synthetic_feature_manifests(tmp_path, experiment, datasets)
    store = ParquetR2FeatureStore(
        tmp_path,
        cast(Clock, SimpleNamespace(now=lambda: datetime(2026, 1, 1, tzinfo=UTC))),
    )
    for name, path in paths.items():
        manifest = store.verify(path)
        payload = json.loads((tmp_path / path).read_text())
        assert payload["contract"] == R2FeatureManifest.CONTRACT
        assert payload["schema_version"] == R2FeatureManifest.SCHEMA_VERSION
        assert manifest.manifest_sha256 != "0" * 64
        assert store.load(path) == datasets[name]


def test_representative_admission_rejects_non_capture_v4_inputs() -> None:
    verified, experiment, _ = _synthetic_pipeline_inputs()
    with pytest.raises(ValueError, match="IG_NATIVE_CAPTURE"):
        _validate_representative_capture_v4(verified, experiment)


def test_oof_verification_dispatches_representative_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replayed = False

    async def replay(_: Path, **__: object) -> None:
        nonlocal replayed
        replayed = True

    bundle = SimpleNamespace(holdout_target_source=None)
    monkeypatch.setattr(
        verification,
        "_verify_r2_oof_bundle_with_source",
        lambda _: (bundle, None),
    )
    monkeypatch.setattr(
        verification,
        "_oof_child_payload",
        lambda *_: {"run_kind": "REPRESENTATIVE"},
    )
    monkeypatch.setattr(verification, "_replay_authority_oof_async", replay)
    assert verify_oof_bundle(Path("manifest.json")) is not None
    assert replayed
