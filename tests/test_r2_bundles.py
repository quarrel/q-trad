"""Round-trip and mutation tests for R2 replay bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import (
    ArtifactReference,
    R2OofBundle,
)
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.runtime.r2_bundles import (
    canonical_bytes,
    verify_r2_oof_bundle,
    verify_r2_software_bundle,
    write_r2_oof_bundle,
)
from qtrad.runtime.r2_verification import (
    build_software_bundle,
    selection_freeze,
    verify_software_bundle,
)


def _child(path: str, seed: str) -> tuple[ArtifactReference, dict[str, object]]:
    identity = hashlib.sha256(seed.encode()).hexdigest()
    contract = f"qtrad-test-child-{seed}-v1"
    payload: dict[str, object] = {
        "contract": contract,
        "schema_version": 1,
        "artifact_id": identity,
        "value": seed,
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return ArtifactReference(contract, identity, path, digest), payload


def _bundle_and_children() -> tuple[R2OofBundle, dict[str, dict[str, object]]]:
    refs: list[ArtifactReference] = []
    children: dict[str, dict[str, object]] = {}
    for _index, category in enumerate(
        ("feature", "preprocessing", "fit", "forecast", "coverage", "evaluation")
    ):
        path = f"{category}/child.json"
        reference, payload = _child(path, category)
        refs.append(reference)
        children[path] = payload
    bundle = R2OofBundle.create(
        foundation_bundle_id=hashlib.sha256(b"foundation").hexdigest(),
        experiment_configuration_id=hashlib.sha256(b"experiment").hexdigest(),
        source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        feature_children=(refs[0],),
        preprocessing_children=(refs[1],),
        fit_children=(refs[2],),
        forecast_manifests=(refs[3],),
        coverage_children=(refs[4],),
        evaluation_children=(refs[5],),
    )
    return bundle, children


def test_oof_bundle_round_trip_is_independently_authenticated(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)

    verified = verify_r2_oof_bundle(manifest_path)

    assert verified == bundle
    assert json.loads(manifest_path.read_bytes())["source_class"] == "IG_NATIVE_CAPTURE"


def test_oof_bundle_rejects_child_mutation_and_republication(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    child_path = tmp_path / "feature" / "child.json"
    child_path.write_bytes(child_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="digest mismatch"):
        verify_r2_oof_bundle(manifest_path)
    with pytest.raises(FileExistsError):
        write_r2_oof_bundle(tmp_path, bundle, children)


def test_oof_bundle_rejects_orphaned_children(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    (tmp_path / "orphan.json").write_bytes(canonical_bytes({"contract": "orphan-v1"}))

    with pytest.raises(ValueError, match="orphaned child"):
        verify_r2_oof_bundle(manifest_path)


def test_reordered_reference_arrays_replay_to_the_same_identity(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    payload = json.loads(manifest_path.read_bytes())
    payload["evaluation_children"] = list(reversed(payload["evaluation_children"]))
    manifest_path.write_bytes(canonical_bytes(payload))

    assert verify_r2_oof_bundle(manifest_path) == bundle


def test_bundle_rejects_unsafe_paths_and_duplicate_cross_category_children() -> None:
    with pytest.raises(ValueError, match="unsafe path"):
        ArtifactReference("child-v1", "a" * 64, "../child.json", "b" * 64)

    reference, _ = _child("child.json", "same")
    with pytest.raises(ValueError, match="duplicate identities"):
        R2OofBundle.create(
            foundation_bundle_id="a" * 64,
            experiment_configuration_id="b" * 64,
            source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
            evidence_class=EvidenceClass.IMPLEMENTATION,
            feature_children=(reference,),
            preprocessing_children=(reference,),
            fit_children=(),
            forecast_manifests=(),
            coverage_children=(),
            evaluation_children=(),
        )


def test_software_bundle_replays_synthetic_and_representative_children(tmp_path: Path) -> None:
    representative_root = tmp_path / "representative-input"
    representative_root.mkdir()
    bundle, children = _bundle_and_children()
    representative_manifest = write_r2_oof_bundle(representative_root, bundle, children)
    selection_path = representative_root / "selection.json"
    selection_freeze(
        oof_bundle_path=representative_manifest,
        frozen_by="test-operator",
        output=selection_path,
    )

    software_manifest = build_software_bundle(
        representative_oof_bundle_path=representative_manifest,
        representative_selection_path=selection_path,
        output=tmp_path / "software",
    )

    software = verify_r2_software_bundle(software_manifest)
    verify_software_bundle(software_manifest)
    assert software.representative_integration_ready == "READY"
    assert software.evidence_disposition == "IMPLEMENTATION_EVIDENCE_ONLY"
    assert software.research_disposition == "RESEARCH_EVIDENCE_PENDING"
