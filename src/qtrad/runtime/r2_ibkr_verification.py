"""Build and independently verify the source-specific IBKR R2.H bundle."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import R2OofBundle
from qtrad.domain.r2_evaluation import R2_EVALUATION_CONTRACT, R2_SELECTION_CONTRACT
from qtrad.domain.r2_ibkr_bundles import R2IbkrHistoricalSoftwareVerificationBundle
from qtrad.domain.r2_ibkr_historical import (
    IBKR_HISTORICAL_PROFILE,
    IBKR_HISTORICAL_TARGETS,
)
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.runtime.r2_bundles import (
    atomic_create,
    canonical_bytes,
    reference_for_json,
    verify_r2_reference,
)
from qtrad.runtime.r2_verification import (
    OOF_DESCRIPTOR_CONTRACT,
    _build_ibkr_synthetic_oof_from_fixture,
    _copy_file,
    _copy_tree,
    _load_selection,
    _oof_child_payload,
    _selection_reference,
    runtime_identities,
    selection_freeze,
    verify_oof_bundle,
)


def build_ibkr_software_bundle(
    *,
    representative_oof_bundle_path: Path,
    representative_selection_path: Path,
    output: Path,
) -> Path:
    """Build the create-only v2 envelope after verifying the IBKR representative run."""

    representative_oof = verify_oof_bundle(representative_oof_bundle_path)
    descriptor = _oof_child_payload(
        representative_oof_bundle_path, representative_oof, OOF_DESCRIPTOR_CONTRACT
    )
    _require_ibkr_representative(descriptor, representative_oof.source_class)
    selection = _load_selection(representative_selection_path)
    _verify_selection(
        selection,
        oof=representative_oof,
        expected_source=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        expected_evaluation_report_id=_oof_child_payload(
            representative_oof_bundle_path, representative_oof, R2_EVALUATION_CONTRACT
        ).get("report_id"),
        expected_application_identity=descriptor.get("application_identity"),
    )

    output.mkdir(parents=True, exist_ok=False)
    _copy_tree(representative_oof_bundle_path.parent, output / "representative" / "oof")
    representative_selection_payload = _copy_file(
        representative_selection_path,
        output / "representative" / "selection.json",
    )

    synthetic_oof_path = _build_ibkr_synthetic_oof_from_fixture(output / "synthetic" / "oof")

    synthetic_selection_path = output / "synthetic" / "selection.json"
    selection_freeze(
        oof_bundle_path=synthetic_oof_path,
        frozen_by="software-verification",
        output=synthetic_selection_path,
    )
    synthetic_oof = verify_oof_bundle(synthetic_oof_path)
    synthetic_selection_payload = _load_selection(synthetic_selection_path)
    synthetic_descriptor = _oof_child_payload(
        synthetic_oof_path, synthetic_oof, OOF_DESCRIPTOR_CONTRACT
    )
    _verify_selection(
        synthetic_selection_payload,
        oof=synthetic_oof,
        expected_source=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        expected_evaluation_report_id=_oof_child_payload(
            synthetic_oof_path, synthetic_oof, R2_EVALUATION_CONTRACT
        ).get("report_id"),
        expected_application_identity=synthetic_descriptor.get("application_identity"),
    )
    identities = runtime_identities()
    software = R2IbkrHistoricalSoftwareVerificationBundle.create(
        market_data_source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        representative_profile=IBKR_HISTORICAL_PROFILE,
        synthetic_oof_bundle=reference_for_json(
            path="synthetic/oof/manifest.json",
            contract=synthetic_oof.CONTRACT,
            semantic_id=synthetic_oof.bundle_id,
            content=synthetic_oof.as_json(),
        ),
        representative_oof_bundle=reference_for_json(
            path="representative/oof/manifest.json",
            contract=representative_oof.CONTRACT,
            semantic_id=representative_oof.bundle_id,
            content=representative_oof.as_json(),
        ),
        synthetic_selection=_selection_reference(
            "synthetic/selection.json", synthetic_selection_payload
        ),
        representative_selection=_selection_reference(
            "representative/selection.json", representative_selection_payload
        ),
        application_identity=identities["application_identity"],
        python_identity=identities["python_identity"],
        numpy_identity=identities["numpy_identity"],
        sklearn_identity=identities["sklearn_identity"],
        representative_integration_ready="READY",
        evidence_disposition="IMPLEMENTATION_EVIDENCE_ONLY",
        research_disposition="RESEARCH_EVIDENCE_PENDING",
    )
    return write_ibkr_software_bundle(output, software)


def write_ibkr_software_bundle(
    output: Path, bundle: R2IbkrHistoricalSoftwareVerificationBundle
) -> Path:
    """Persist the v2 envelope and require every declared child to already exist."""

    for reference in (
        bundle.synthetic_oof_bundle,
        bundle.representative_oof_bundle,
        bundle.synthetic_selection,
        bundle.representative_selection,
    ):
        verify_r2_reference(output, reference)
    manifest = output / "manifest.json"
    atomic_create(manifest, canonical_bytes(bundle.as_json()))
    return manifest


def verify_ibkr_software_bundle(path: Path) -> R2IbkrHistoricalSoftwareVerificationBundle:
    """Verify the v2 envelope, both OOF replays, selections and exact top-level closure."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("IBKR software bundle must be a regular non-symlink manifest")
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("IBKR software bundle manifest must be an object")
    if path.read_bytes() != canonical_bytes(cast(Mapping[str, object], payload)):
        raise ValueError("IBKR software bundle manifest is not canonical")
    bundle = R2IbkrHistoricalSoftwareVerificationBundle.from_json(payload)
    root = path.parent
    identities = runtime_identities()
    expected_identity_keys = (
        "application_identity",
        "python_identity",
        "numpy_identity",
        "sklearn_identity",
    )
    for key in expected_identity_keys:
        expected = identities[key]
        if getattr(bundle, key) != expected:
            raise ValueError(f"IBKR software bundle {key} differs from the running environment")

    for reference in (
        bundle.synthetic_oof_bundle,
        bundle.representative_oof_bundle,
        bundle.synthetic_selection,
        bundle.representative_selection,
    ):
        verify_r2_reference(root, reference)

    synthetic_path = root / bundle.synthetic_oof_bundle.path
    representative_path = root / bundle.representative_oof_bundle.path
    synthetic = verify_oof_bundle(synthetic_path)
    representative = verify_oof_bundle(representative_path)
    if synthetic.source_class is not bundle.market_data_source_class:
        raise ValueError("IBKR software synthetic child has a mixed source")
    if representative.source_class is not bundle.market_data_source_class:
        raise ValueError("IBKR software representative child has a mixed source")
    if synthetic.evidence_class is not EvidenceClass.IMPLEMENTATION:
        raise ValueError("IBKR software synthetic child is not implementation-only")
    if representative.evidence_class is not EvidenceClass.IMPLEMENTATION:
        raise ValueError("IBKR software representative child is not implementation-only")

    synthetic_descriptor = _oof_child_payload(synthetic_path, synthetic, OOF_DESCRIPTOR_CONTRACT)
    representative_descriptor = _oof_child_payload(
        representative_path, representative, OOF_DESCRIPTOR_CONTRACT
    )
    if synthetic_descriptor.get("run_kind") != "SYNTHETIC":
        raise ValueError("IBKR software synthetic child is not a synthetic run")
    if synthetic_descriptor.get("representative_profile") != bundle.representative_profile:
        raise ValueError("IBKR software synthetic profile differs from the envelope")
    if representative_descriptor.get("representative_profile") != bundle.representative_profile:
        raise ValueError("IBKR software representative profile differs from the envelope")
    if synthetic_descriptor.get("feature_sets") != ["L0", "L1", "P0", "P1"]:
        raise ValueError("IBKR software synthetic child has the wrong feature closure")
    if representative_descriptor.get("feature_sets") != ["L0", "L1", "P0", "P1"]:
        raise ValueError("IBKR software representative child has the wrong feature closure")
    if synthetic_descriptor.get("target_instruments") != list(IBKR_HISTORICAL_TARGETS):
        raise ValueError("IBKR software synthetic child has the wrong target universe")
    if representative_descriptor.get("target_instruments") != list(IBKR_HISTORICAL_TARGETS):
        raise ValueError("IBKR software representative child has the wrong target universe")
    if synthetic.foundation_bundle_id == representative.foundation_bundle_id:
        raise ValueError("IBKR software children must bind independent foundation bundles")
    if synthetic.experiment_configuration_id == representative.experiment_configuration_id:
        raise ValueError("IBKR software children must bind independent experiment configurations")
    for name, descriptor in (
        ("synthetic", synthetic_descriptor),
        ("representative", representative_descriptor),
    ):
        for key in (
            "application_identity",
            "python_identity",
            "numpy_identity",
            "sklearn_identity",
        ):
            if descriptor.get(key) != getattr(bundle, key):
                raise ValueError(f"IBKR software {name} descriptor identity differs from envelope")
    synthetic_selection = _load_selection(root / bundle.synthetic_selection.path)
    representative_selection = _load_selection(root / bundle.representative_selection.path)
    _verify_selection(
        synthetic_selection,
        oof=synthetic,
        expected_source=bundle.market_data_source_class,
        expected_evaluation_report_id=_oof_child_payload(
            synthetic_path, synthetic, R2_EVALUATION_CONTRACT
        ).get("report_id"),
        expected_application_identity=synthetic_descriptor.get("application_identity"),
    )
    _verify_selection(
        representative_selection,
        oof=representative,
        expected_source=bundle.market_data_source_class,
        expected_evaluation_report_id=_oof_child_payload(
            representative_path, representative, R2_EVALUATION_CONTRACT
        ).get("report_id"),
        expected_application_identity=representative_descriptor.get("application_identity"),
    )
    if bundle.synthetic_selection.semantic_id != synthetic_selection.get("manifest_id"):
        raise ValueError("IBKR synthetic selection identity differs from its reference")
    if bundle.representative_selection.semantic_id != representative_selection.get("manifest_id"):
        raise ValueError("IBKR representative selection identity differs from its reference")

    allowed_prefixes = {"synthetic/oof/", "representative/oof/"}
    allowed_exact = {
        "manifest.json",
        bundle.synthetic_selection.path,
        bundle.representative_selection.path,
    }
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"IBKR software bundle contains a symlink: {candidate}")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        if relative in allowed_exact or any(
            relative.startswith(prefix) for prefix in allowed_prefixes
        ):
            continue
        raise ValueError(f"IBKR software bundle contains an orphan or additional child: {relative}")
    return bundle


def _verify_selection(
    payload: Mapping[str, object],
    *,
    oof: R2OofBundle,
    expected_source: MarketDataSourceClass,
    expected_evaluation_report_id: object,
    expected_application_identity: object,
) -> None:
    if payload.get("contract") != R2_SELECTION_CONTRACT:
        raise ValueError("IBKR software selection is not a typed SelectionManifest")
    if payload.get("oof_bundle_id") != oof.bundle_id:
        raise ValueError("IBKR software selection does not bind its OOF bundle")
    if payload.get("foundation_bundle_id") != oof.foundation_bundle_id:
        raise ValueError("IBKR software selection does not bind its foundation")
    if payload.get("experiment_configuration_id") != oof.experiment_configuration_id:
        raise ValueError("IBKR software selection does not bind its experiment")
    if payload.get("source_class") != expected_source.value:
        raise ValueError("IBKR software selection source class differs from its OOF bundle")
    if payload.get("evidence_class") != EvidenceClass.IMPLEMENTATION.value:
        raise ValueError("IBKR software selection is not implementation-only")
    if payload.get("evaluation_report_id") != expected_evaluation_report_id:
        raise ValueError("IBKR software selection does not bind its evaluation report")
    if payload.get("application_image_identity") != expected_application_identity:
        raise ValueError("IBKR software selection identity differs from its OOF descriptor")
    if payload.get("holdout_state_verification") != "PENDING_R2_H_INTEGRATION":
        raise ValueError("IBKR software selection must leave holdout verification pending")


def _require_ibkr_representative(
    descriptor: Mapping[str, object], source: MarketDataSourceClass
) -> None:
    if source is not MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
        raise ValueError("IBKR software representative child has a mixed source")
    if descriptor.get("run_kind") != "REPRESENTATIVE":
        raise ValueError("IBKR software representative child is not a representative run")
    if descriptor.get("representative_profile") != IBKR_HISTORICAL_PROFILE:
        raise ValueError("IBKR software representative child has the wrong profile")
    if descriptor.get("feature_sets") != ["L0", "L1", "P0", "P1"]:
        raise ValueError("IBKR software representative child has the wrong feature closure")
    if descriptor.get("target_instruments") != list(IBKR_HISTORICAL_TARGETS):
        raise ValueError("IBKR software representative child has the wrong target universe")


__all__ = [
    "build_ibkr_software_bundle",
    "verify_ibkr_software_bundle",
    "write_ibkr_software_bundle",
]
