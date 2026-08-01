"""Create-only persistence and independent verification for R2 bundles."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from qtrad.domain.r2_bundles import (
    ArtifactReference,
    R2ForecastManifest,
    R2OofBundle,
    R2SoftwareVerificationBundle,
)

_MAX_BYTES = 64 * 1024 * 1024


def canonical_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic_create(path: Path, content: bytes) -> None:
    """Write one regular file atomically and fail on every existing path."""
    if path.is_symlink() or path.exists():
        raise FileExistsError(f"create-only R2 output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.link(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def reference_for_json(
    *, path: str, contract: str, semantic_id: str, content: Mapping[str, object]
) -> ArtifactReference:
    encoded = canonical_bytes(content)
    return ArtifactReference(contract, semantic_id, path, sha256(encoded).hexdigest())


def write_r2_forecast_manifest(
    output: Path, manifest: R2ForecastManifest, forecast_json: Mapping[str, object]
) -> Path:
    """Persist a forecast child then its thin source/evidence manifest."""
    if manifest.forecast_child.path == "manifest.json":
        raise ValueError("forecast child path is reserved for the manifest")
    child = output / manifest.forecast_child.path
    atomic_create(child, canonical_bytes(forecast_json))
    _verify_reference(output, manifest.forecast_child)
    path = output / "manifest.json"
    atomic_create(path, canonical_bytes(manifest.as_json()))
    return path


def load_forecast_manifest(path: Path) -> R2ForecastManifest:
    payload = _load_object(path)
    return R2ForecastManifest.from_json(payload)


def write_r2_oof_bundle(
    output: Path,
    bundle: R2OofBundle,
    children: Mapping[str, Mapping[str, object]],
) -> Path:
    """Persist named children and the OOF manifest without embedding child data."""
    refs = _all_oof_references(bundle)
    if set(children) != {ref.path for ref in refs}:
        raise ValueError("OOF child payloads must exactly match declared references")
    for ref in refs:
        atomic_create(output / ref.path, canonical_bytes(children[ref.path]))
        _verify_reference(output, ref)
    path = output / "manifest.json"
    atomic_create(path, canonical_bytes(bundle.as_json()))
    return path


def verify_r2_oof_bundle(path: Path) -> R2OofBundle:
    payload = _load_object(path)
    if set(payload) != {
        "contract",
        "schema_version",
        "foundation_bundle_id",
        "experiment_configuration_id",
        "source_class",
        "evidence_class",
        "feature_children",
        "preprocessing_children",
        "fit_children",
        "forecast_manifests",
        "coverage_children",
        "evaluation_children",
        "bundle_id",
    }:
        raise ValueError("R2 OOF bundle has unknown or missing fields")
    if payload["contract"] != R2OofBundle.CONTRACT or payload["schema_version"] != 1:
        raise ValueError("R2 OOF bundle contract is unsupported")
    refs = {
        key: _references(payload[key])
        for key in (
            "feature_children",
            "preprocessing_children",
            "fit_children",
            "forecast_manifests",
            "coverage_children",
            "evaluation_children",
        )
    }
    bundle = R2OofBundle(
        foundation_bundle_id=_text(payload["foundation_bundle_id"]),
        experiment_configuration_id=_text(payload["experiment_configuration_id"]),
        source_class=_source(payload["source_class"]),
        evidence_class=_evidence(payload["evidence_class"]),
        **refs,
        bundle_id=_text(payload["bundle_id"]),
    )
    allowed_paths = {"manifest.json"} | {ref.path for ref in _all_oof_references(bundle)}
    for ref in _all_oof_references(bundle):
        _verify_reference(path.parent, ref)
        child = _load_object(path.parent / ref.path)
        _verify_lineage_payload(child, bundle)
        if child.get("contract") == R2ForecastManifest.CONTRACT:
            forecast_manifest = R2ForecastManifest.from_json(child)
            _verify_reference(path.parent, forecast_manifest.forecast_child)
            allowed_paths.add(forecast_manifest.forecast_child.path)
        if child.get("contract") == "qtrad-r2-evaluation-register-v1":
            report_id = child.get("report_id")
            if not isinstance(report_id, str):
                raise ValueError("R2 evaluation register must expose a report ID")
            report_payload = {key: value for key, value in child.items() if key != "report_id"}
            if sha256(canonical_bytes(report_payload)).hexdigest() != report_id:
                raise ValueError(
                    "R2 evaluation register report ID does not authenticate its content"
                )
            _verify_evaluation_register(child, bundle, path.parent)
    _allow_bound_selection(path.parent, bundle)
    _reject_orphan_files(path.parent, allowed_paths)
    return bundle


def write_r2_software_bundle(output: Path, bundle: R2SoftwareVerificationBundle) -> Path:
    for ref in (
        bundle.synthetic_oof_bundle,
        bundle.representative_oof_bundle,
        bundle.synthetic_selection,
        bundle.representative_selection,
    ):
        _verify_reference(output, ref)
    path = output / "manifest.json"
    atomic_create(path, canonical_bytes(bundle.as_json()))
    return path


def verify_r2_software_bundle(path: Path) -> R2SoftwareVerificationBundle:
    payload = _load_object(path)
    expected = {
        "contract",
        "schema_version",
        "synthetic_oof_bundle",
        "representative_oof_bundle",
        "synthetic_selection",
        "representative_selection",
        "application_identity",
        "python_identity",
        "numpy_identity",
        "sklearn_identity",
        "representative_integration_ready",
        "evidence_disposition",
        "research_disposition",
        "bundle_id",
    }
    if set(payload) != expected:
        raise ValueError("R2 software bundle has unknown or missing fields")
    if (
        payload["contract"] != R2SoftwareVerificationBundle.CONTRACT
        or payload["schema_version"] != 1
    ):
        raise ValueError("R2 software bundle contract is unsupported")
    bundle = R2SoftwareVerificationBundle(
        synthetic_oof_bundle=ArtifactReference.from_json(payload["synthetic_oof_bundle"]),
        representative_oof_bundle=ArtifactReference.from_json(payload["representative_oof_bundle"]),
        synthetic_selection=ArtifactReference.from_json(payload["synthetic_selection"]),
        representative_selection=ArtifactReference.from_json(payload["representative_selection"]),
        application_identity=_text(payload["application_identity"]),
        python_identity=_text(payload["python_identity"]),
        numpy_identity=_text(payload["numpy_identity"]),
        sklearn_identity=_text(payload["sklearn_identity"]),
        representative_integration_ready=_text(payload["representative_integration_ready"]),
        evidence_disposition=_text(payload["evidence_disposition"]),
        research_disposition=_text(payload["research_disposition"]),
        bundle_id=_text(payload["bundle_id"]),
    )
    for ref in (
        bundle.synthetic_oof_bundle,
        bundle.representative_oof_bundle,
        bundle.synthetic_selection,
        bundle.representative_selection,
    ):
        _verify_reference(path.parent, ref)
    _reject_software_orphans(
        path.parent,
        {
            "manifest.json",
            bundle.synthetic_selection.path,
            bundle.representative_selection.path,
        },
    )
    return bundle


def _all_oof_references(bundle: R2OofBundle) -> tuple[ArtifactReference, ...]:
    return (
        *bundle.feature_children,
        *bundle.preprocessing_children,
        *bundle.fit_children,
        *bundle.forecast_manifests,
        *bundle.coverage_children,
        *bundle.evaluation_children,
    )


def _allow_bound_selection(root: Path, bundle: R2OofBundle) -> None:
    selection_path = root / "selection.json"
    if not selection_path.exists():
        return
    selection = _load_object(selection_path)
    if selection.get("contract") != "qtrad-r2-selection-mechanics-v1":
        raise ValueError("R2 bundle contains an unexpected selection child")
    if (
        selection.get("oof_bundle_id") != bundle.bundle_id
        or selection.get("foundation_bundle_id") != bundle.foundation_bundle_id
        or selection.get("experiment_configuration_id") != bundle.experiment_configuration_id
        or selection.get("source_class") != bundle.source_class.value
        or selection.get("evidence_class") != bundle.evidence_class.value
        or selection.get("holdout_excluded") is not True
    ):
        raise ValueError("R2 selection child does not bind its OOF bundle")
    # The selection is an explicitly bound sibling, not an OOF child.
    _verify_reference(
        root,
        reference_for_json(
            path="selection.json",
            contract=str(selection["contract"]),
            semantic_id=str(selection["selection_id"]),
            content=selection,
        ),
    )


def _reject_orphan_files(root: Path, allowed_paths: set[str]) -> None:
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"R2 bundle contains a symlink: {candidate.relative_to(root)}")
        if candidate.is_file():
            relative = candidate.relative_to(root).as_posix()
            if relative == "selection.json":
                continue
            if relative not in allowed_paths:
                raise ValueError(f"R2 bundle contains an orphaned child: {relative}")


def _reject_software_orphans(root: Path, allowed_paths: set[str]) -> None:
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(
                f"R2 software bundle contains a symlink: {candidate.relative_to(root)}"
            )
        if candidate.is_file():
            relative = candidate.relative_to(root).as_posix()
            if relative in allowed_paths:
                continue
            if relative.startswith("synthetic/oof/") or relative.startswith("representative/oof/"):
                continue
            raise ValueError(f"R2 software bundle contains an orphaned child: {relative}")


def _verify_reference(root: Path, reference: ArtifactReference) -> None:
    root = root.resolve()
    candidate = root / reference.path
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("R2 child path escapes its bundle") from exc
    current = root
    for part in reference.path.split("/"):
        current /= part
        if current.is_symlink():
            raise ValueError("R2 bundle child path traverses a symlink")
    if not candidate.is_file():
        raise ValueError(f"R2 bundle child is missing or not a regular file: {reference.path}")
    content = candidate.read_bytes()
    if len(content) > _MAX_BYTES:
        raise ValueError("R2 bundle child exceeds the size limit")
    if sha256(content).hexdigest() != reference.sha256:
        raise ValueError(f"R2 bundle child digest mismatch: {reference.path}")
    payload = _load_object(candidate)
    if payload.get("contract") != reference.contract:
        raise ValueError(f"R2 bundle child contract mismatch: {reference.path}")
    identity = next(
        (
            payload[key]
            for key in (
                "manifest_id",
                "bundle_id",
                "dataset_id",
                "artifact_id",
                "selection_id",
                "fit_id",
                "coverage_id",
                "summary_id",
                "report_id",
                "descriptor_id",
                "scenario_id",
            )
            if key in payload
        ),
        None,
    )
    if identity != reference.semantic_id:
        raise ValueError(f"R2 bundle child semantic identity mismatch: {reference.path}")


def _verify_lineage_payload(payload: dict[str, object], bundle: R2OofBundle) -> None:
    """Reject source/evidence claims that disagree with the authenticated OOF envelope."""
    candidates = [payload]
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        candidates.append(cast(dict[str, object], manifest))
    for candidate in candidates:
        source = candidate.get("market_data_source_class", candidate.get("source_class"))
        evidence = candidate.get("evidence_class")
        contract = candidate.get("contract")
        # Canonical R2 children must carry both lineage dimensions explicitly.  The
        # generic test-child contracts intentionally remain usable as opaque children.
        if (
            isinstance(contract, str)
            and contract.startswith("qtrad-r2-")
            and (source is None or evidence is None)
        ):
            raise ValueError("R2 child must declare source and evidence class")
        if source is not None and source != bundle.source_class.value:
            raise ValueError("R2 child source class differs from its OOF bundle")
        if evidence is not None and evidence != bundle.evidence_class.value:
            raise ValueError("R2 child evidence class differs from its OOF bundle")


def _verify_evaluation_register(
    payload: dict[str, object], bundle: R2OofBundle, root: Path
) -> None:
    """Authenticate register references against the OOF child set."""
    reference_keys = (
        "local_comparator",
        "evaluation",
        "evaluated_models",
        "forecast_manifests",
        "coverage",
    )
    if not any(key in payload for key in reference_keys):
        return
    declared = {
        (reference.contract, reference.semantic_id, reference.path): reference
        for reference in _all_oof_references(bundle)
    }

    def bind(value: object) -> None:
        reference = ArtifactReference.from_json(value)
        key = (reference.contract, reference.semantic_id, reference.path)
        if key not in declared:
            raise ValueError("R2 evaluation register references an undeclared child")
        _verify_reference(root, reference)

    for key in ("local_comparator", "evaluation"):
        if key not in payload:
            raise ValueError(f"R2 evaluation register is missing {key} reference")
        bind(payload[key])
    for key in ("evaluated_models", "forecast_manifests", "coverage"):
        values = payload.get(key)
        if not isinstance(values, list):
            raise ValueError(f"R2 evaluation register {key} references must be an array")
        for value in values:
            bind(value)


def _references(value: object) -> tuple[ArtifactReference, ...]:
    if not isinstance(value, list):
        raise TypeError("R2 bundle child list must be an array")
    return tuple(ArtifactReference.from_json(item) for item in value)


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("R2 bundle manifest must be a regular non-symlink file")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_BYTES:
        raise ValueError("R2 bundle manifest exceeds the size limit")
    value = json.loads(encoded)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("R2 bundle manifest must be a JSON object")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def _source(value: object):
    from qtrad.domain.market_data import MarketDataSourceClass

    return MarketDataSourceClass(_text(value))


def _evidence(value: object):
    from qtrad.domain.r2_readiness import EvidenceClass

    return EvidenceClass(_text(value))
