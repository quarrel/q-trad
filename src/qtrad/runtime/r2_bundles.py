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
from qtrad.domain.r2_evaluation import R2_SELECTION_CONTRACT
from qtrad.domain.r2_models import R2_PREPROCESSING_SELECTION_CONTRACT

_MAX_BYTES = 64 * 1024 * 1024
R2_EVALUATION_REGISTER_CONTRACT = "qtrad-r2-evaluation-register-v2"


def canonical_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _reject_symlink_ancestors(path: Path) -> None:
    """Reject every existing ancestor before creating a temporary file."""
    current = path.parent
    ancestors: list[Path] = []
    while current != current.parent:
        ancestors.append(current)
        current = current.parent
    for ancestor in ancestors:
        if ancestor.is_symlink():
            raise ValueError(f"R2 output path traverses a symlink: {ancestor}")


def atomic_create(path: Path, content: bytes) -> None:
    """Write one regular file atomically and fail on every existing path."""
    if len(content) > _MAX_BYTES:
        raise ValueError("R2 output exceeds the 64 MiB child limit")
    _reject_symlink_ancestors(path)
    if path.is_symlink() or path.exists():
        raise FileExistsError(f"create-only R2 output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(path)
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
        encoded = canonical_bytes(children[ref.path])
        if len(encoded) > _MAX_BYTES:
            raise ValueError(f"OOF child exceeds the 64 MiB limit: {ref.path}")
        atomic_create(output / ref.path, encoded)
        _verify_reference(output, ref)
    path = output / "manifest.json"
    atomic_create(path, canonical_bytes(bundle.as_json()))
    return path


def _verify_replay_inputs(
    root: Path, descriptor: Mapping[str, object], allowed_paths: set[str]
) -> None:
    raw = descriptor.get("replay_inputs")
    if not isinstance(raw, dict) or raw.get("root") != ".":
        raise ValueError("representative replay inputs are missing or malformed")
    children = raw.get("children")
    expected = {"foundation", "experiment", "L0", "L1", "P0", "P1"}
    if not isinstance(children, dict) or set(children) != expected:
        raise ValueError("representative replay inputs have incomplete children")
    root_resolved = root.resolve()
    for name, value in children.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise ValueError("representative replay input child is malformed")
        raw_path = value.get("path")
        raw_root = value.get("root")
        expected_digest = value.get("sha256")
        files = value.get("files")
        if not all(isinstance(item, str) for item in (raw_path, raw_root, expected_digest)):
            raise ValueError("representative replay input identity is incomplete")
        if not isinstance(files, list) or not files:
            raise ValueError("representative replay input file closure is missing")
        initial_path = Path(cast(str, raw_path))
        replay_root = Path(cast(str, raw_root))
        if (
            initial_path.is_absolute()
            or replay_root.is_absolute()
            or ".." in initial_path.parts
            or ".." in replay_root.parts
        ):
            raise ValueError("representative replay input path is unsafe")
        candidate_root = (root / replay_root).resolve()
        if not candidate_root.is_relative_to(root_resolved):
            raise ValueError("representative replay input escapes the bundle root")
        if candidate_root.is_symlink() or not candidate_root.is_dir():
            raise ValueError(f"representative replay input root is unavailable: {name}")
        declared: dict[str, str] = {}
        for raw_file in files:
            if not isinstance(raw_file, dict) or set(raw_file) != {"path", "sha256"}:
                raise ValueError("representative replay input file reference is malformed")
            file_path = raw_file.get("path")
            file_digest = raw_file.get("sha256")
            if not isinstance(file_path, str) or not isinstance(file_digest, str):
                raise ValueError("representative replay input file identity is incomplete")
            relative_file = Path(file_path)
            if relative_file.is_absolute() or ".." in relative_file.parts:
                raise ValueError("representative replay input file path is unsafe")
            candidate = (root / relative_file).resolve()
            if (
                not candidate.is_relative_to(root_resolved)
                or not candidate.is_relative_to(candidate_root)
                or candidate.is_symlink()
                or not candidate.is_file()
            ):
                raise ValueError(f"representative replay input file is unavailable: {name}")
            normalized = relative_file.as_posix()
            if normalized in declared:
                raise ValueError("representative replay input file closure contains duplicates")
            if sha256(candidate.read_bytes()).hexdigest() != file_digest:
                raise ValueError(f"representative replay input file changed: {name}")
            declared[normalized] = file_digest
            allowed_paths.add(normalized)
        normalized_initial = initial_path.as_posix()
        if normalized_initial not in declared:
            raise ValueError(f"representative replay input path is not in its closure: {name}")
        if declared[normalized_initial] != expected_digest:
            raise ValueError(
                f"representative replay input identity differs from its closure: {name}"
            )


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
        "holdout_target_source",
        "bundle_id",
    }:
        raise ValueError("R2 OOF bundle has unknown or missing fields")
    if (
        payload["contract"] != R2OofBundle.CONTRACT
        or payload["schema_version"] != R2OofBundle.SCHEMA_VERSION
    ):
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
        holdout_target_source=(
            None
            if payload["holdout_target_source"] is None
            else ArtifactReference.from_json(payload["holdout_target_source"])
        ),
    )
    all_refs = _all_oof_references(bundle)
    canonical = any(
        reference.contract.startswith("qtrad-r2-")
        and reference.contract != "qtrad-r2-holdout-target-source-v1"
        for reference in all_refs
    )
    register_refs = [
        reference
        for reference in bundle.evaluation_children
        if reference.contract == R2_EVALUATION_REGISTER_CONTRACT
    ]
    descriptor_refs = [
        reference
        for reference in bundle.evaluation_children
        if reference.contract == "qtrad-r2-oof-run-descriptor-v1"
    ]
    if canonical and (len(register_refs) != 1 or len(descriptor_refs) != 1):
        raise ValueError("canonical R2 OOF bundle must have exactly one register and descriptor")
    allowed_paths = {"manifest.json"} | {ref.path for ref in all_refs}
    for ref in all_refs:
        _verify_reference(path.parent, ref)
        child = _load_object(path.parent / ref.path)
        _verify_lineage_payload(child, bundle)
        if child.get("contract") == "qtrad-r2-oof-run-descriptor-v1":
            descriptor_id = child.get("descriptor_id")
            if not isinstance(descriptor_id, str):
                raise ValueError("R2 OOF descriptor must expose a descriptor ID")
            descriptor_payload = {
                key: value for key, value in child.items() if key != "descriptor_id"
            }
            if sha256(canonical_bytes(descriptor_payload)).hexdigest() != descriptor_id:
                raise ValueError("R2 OOF descriptor ID does not authenticate its content")
            if (
                child.get("foundation_bundle_id") != bundle.foundation_bundle_id
                or child.get("experiment_configuration_id") != bundle.experiment_configuration_id
                or child.get("source_class") != bundle.source_class.value
                or child.get("evidence_class") != bundle.evidence_class.value
                or child.get("holdout_excluded") is not True
            ):
                raise ValueError("R2 OOF descriptor lineage differs from its bundle")
            if "replay_inputs" in child:
                _verify_replay_inputs(path.parent, child, allowed_paths)
        contract = child.get("contract")
        if (
            ref.path.startswith("preprocessing/")
            and isinstance(contract, str)
            and contract.startswith("qtrad-r2-preprocessing-selection")
            and (
                contract != R2_PREPROCESSING_SELECTION_CONTRACT or child.get("schema_version") != 2
            )
        ):
            raise ValueError("R2 preprocessing-selection child is not v2")
        if child.get("contract") == R2ForecastManifest.CONTRACT:
            forecast_manifest = R2ForecastManifest.from_json(child)
            _verify_reference(path.parent, forecast_manifest.forecast_child)
            allowed_paths.add(forecast_manifest.forecast_child.path)
        if child.get("contract") == R2_EVALUATION_REGISTER_CONTRACT:
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
        *((bundle.holdout_target_source,) if bundle.holdout_target_source is not None else ()),
    )


def _allow_bound_selection(root: Path, bundle: R2OofBundle) -> None:
    selection_path = root / "selection.json"
    if not selection_path.exists():
        return
    selection = _load_object(selection_path)
    if selection.get("contract") != R2_SELECTION_CONTRACT:
        raise ValueError("R2 bundle contains an unexpected selection child")
    if (
        selection.get("oof_bundle_id") != bundle.bundle_id
        or selection.get("foundation_bundle_id") != bundle.foundation_bundle_id
        or selection.get("experiment_configuration_id") != bundle.experiment_configuration_id
        or selection.get("source_class") != bundle.source_class.value
        or selection.get("evidence_class") != bundle.evidence_class.value
        or selection.get("holdout_state_verification") != "PENDING_R2_H_INTEGRATION"
    ):
        raise ValueError("R2 selection child does not bind its OOF bundle")
    manifest_id = selection.get("manifest_id")
    if not isinstance(manifest_id, str):
        raise ValueError("R2 selection child has no manifest ID")
    _verify_reference(
        root,
        reference_for_json(
            path="selection.json",
            contract=str(selection["contract"]),
            semantic_id=manifest_id,
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
                "ablation_id",
                "source_id",
            )
            if key in payload
        ),
        None,
    )
    if identity != reference.semantic_id:
        raise ValueError(f"R2 bundle child semantic identity mismatch: {reference.path}")


def verify_r2_reference(root: Path, reference: ArtifactReference) -> None:
    """Verify one R2 child using its complete path, byte, contract and identity boundary."""

    _verify_reference(root, reference)


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
            and contract != "qtrad-r2-holdout-target-source-v1"
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
    """Authenticate every evaluation child and reconcile it with the OOF envelope."""
    if (
        payload.get("contract") != R2_EVALUATION_REGISTER_CONTRACT
        or payload.get("schema_version") != 2
    ):
        raise ValueError("R2 evaluation register contract is unsupported")
    required = {
        "local_comparator",
        "evaluation",
        "evaluated_models",
        "forecast_manifests",
        "coverage",
        "pooled_ablation",
        "selection_evaluation_report_id",
        "selection_decisions",
        "selection_selected_configuration_ids",
        "selection_holdout_comparator_configuration_ids",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(
            "R2 evaluation register is missing required references: " + ", ".join(sorted(missing))
        )
    if payload.get("source_class") != bundle.source_class.value:
        raise ValueError("R2 evaluation register source class differs from its OOF bundle")
    if payload.get("evidence_class") != bundle.evidence_class.value:
        raise ValueError("R2 evaluation register evidence class differs from its OOF bundle")
    declared = {
        (reference.contract, reference.semantic_id, reference.path): reference
        for reference in _all_oof_references(bundle)
    }

    def bind(value: object) -> ArtifactReference:
        reference = ArtifactReference.from_json(value)
        key = (reference.contract, reference.semantic_id, reference.path)
        if key not in declared:
            raise ValueError("R2 evaluation register references an undeclared child")
        _verify_reference(root, reference)
        return reference

    def bind_one(key: str, expected_path: str) -> ArtifactReference:
        reference = bind(payload[key])
        if reference.path != expected_path:
            raise ValueError(f"R2 evaluation register {key} path is inconsistent")
        return reference

    bind_one("local_comparator", "evaluation/local-comparator.json")
    evaluation_reference = bind_one("evaluation", "evaluation/report.json")
    bind_one("pooled_ablation", "evaluation/pooled-ablation.json")
    evaluation_payload = _load_object(root / evaluation_reference.path)
    if payload.get("selection_evaluation_report_id") != evaluation_payload.get("report_id"):
        raise ValueError(
            "R2 evaluation register selection report ID differs from evaluation report"
        )
    if not isinstance(payload["selection_decisions"], list):
        raise ValueError("R2 evaluation register selection decisions must be an array")
    for key in (
        "selection_selected_configuration_ids",
        "selection_holdout_comparator_configuration_ids",
    ):
        values = payload[key]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"R2 evaluation register {key} must be a string array")

    def bind_array(key: str, expected: tuple[ArtifactReference, ...]) -> None:
        values = payload[key]
        if not isinstance(values, list):
            raise ValueError(f"R2 evaluation register {key} references must be an array")
        actual = tuple(bind(value) for value in values)
        actual_keys = tuple(
            (item.contract, item.semantic_id, item.path, item.sha256) for item in actual
        )
        expected_keys = tuple(
            (item.contract, item.semantic_id, item.path, item.sha256) for item in expected
        )
        if len(actual_keys) != len(set(actual_keys)) or set(actual_keys) != set(expected_keys):
            raise ValueError(f"R2 evaluation register {key} is not an exact child reconciliation")

    bind_array("forecast_manifests", bundle.forecast_manifests)
    bind_array("coverage", bundle.coverage_children)
    evaluated = tuple(
        reference
        for reference in _all_oof_references(bundle)
        if reference.path.startswith("evaluation/models/")
    )
    bind_array("evaluated_models", evaluated)


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
