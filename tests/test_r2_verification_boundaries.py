"""Regression tests for authenticated R2 verification boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import qtrad.runtime.r2_verification as verification
from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore, R2FeatureManifest
from qtrad.domain.folds import Fold, membership_hash
from qtrad.domain.r2_ibkr_historical import IBKRHistoricalAdapterIdentity
from qtrad.ports.clock import Clock
from qtrad.runtime.r2_verification import (
    _image_identity_manifest as _production_image_identity_manifest,
)
from qtrad.runtime.r2_verification import (
    _materialise_synthetic_feature_manifests,
    _synthetic_pipeline_inputs,
    _validate_representative_capture_v4,
    _validate_representative_fold_layout,
    require_ibkr_adapter_runtime_identity,
    runtime_identities,
    verify_oof_bundle,
)


def test_image_digest_environment_is_not_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QTRAD_IMAGE_DIGEST", "sha256:" + "f" * 64)
    identities = runtime_identities()
    assert "image:sha256:" + "0" * 64 in identities["application_identity"]


def test_persisted_ibkr_adapter_identity_matches_current_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {
        "application_identity": "qtrad-test-application",
        "image_identity": "sha256:" + "1" * 64,
    }
    adapter = IBKRHistoricalAdapterIdentity.create(
        foundation_bundle_id="a" * 64,
        application_identity=runtime["application_identity"],
        image_identity=runtime["image_identity"],
    )
    monkeypatch.setattr(verification, "runtime_identities", lambda: runtime)
    require_ibkr_adapter_runtime_identity(adapter)

    for field in ("application_identity", "image_identity"):
        drifted = dict(runtime)
        drifted[field] = "runtime-drift"
        monkeypatch.setattr(
            verification, "runtime_identities", lambda identities=drifted: identities
        )
        with pytest.raises(ValueError, match=field):
            require_ibkr_adapter_runtime_identity(adapter)


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
