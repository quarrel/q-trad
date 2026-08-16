"""Create-only persistence and independent verification for R2 bundles."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from qtrad.domain.r2_baselines import (
    R2_COEFFICIENT_STABILITY_CONTRACT,
    R2_FOLD_FIT_CONTRACT,
    R2_FORECAST_COVERAGE_CONTRACT,
)
from qtrad.domain.r2_bundles import (
    R2_HOLDOUT_SOURCE_BINDING_CONTRACT,
    ArtifactReference,
    R2ForecastManifest,
    R2OofBundle,
)
from qtrad.domain.r2_evaluation import (
    R2_EVALUATION_CONTRACT,
    R2_LOCAL_COMPARATOR_CONTRACT,
    R2_SELECTION_CONTRACT,
)
from qtrad.domain.r2_features import R2_FEATURE_DATASET_CONTRACT
from qtrad.domain.r2_holdout import (
    R2_CONFIRMATORY_OPENED_CONTRACT,
    R2_FINAL_FIT_CONTRACT,
    R2_HOLDOUT_BUNDLE_CONTRACT,
    R2_HOLDOUT_CONSUMED_CONTRACT,
    R2_HOLDOUT_COVERAGE_CONTRACT,
    R2_HOLDOUT_EVALUATION_CONTRACT,
    R2_HOLDOUT_FEATURES_CONTRACT,
    R2_HOLDOUT_FORECAST_CONTRACT,
    R2_HOLDOUT_FORECAST_SEAL_CONTRACT,
    R2_HOLDOUT_OPENED_CONTRACT,
    R2_HOLDOUT_OPPORTUNITY_REGISTRY_CONTRACT,
    R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT,
    R2_HOLDOUT_SELECTION_CONTRACT,
    R2_HOLDOUT_TARGET_PROJECTION_CONTRACT,
    R2_PRE_HOLDOUT_TARGET_PROJECTION_CONTRACT,
    R2HoldoutTargetSource,
)
from qtrad.domain.r2_models import R2_PREPROCESSING_SELECTION_CONTRACT

_MAX_BYTES = 64 * 1024 * 1024
R2_EVALUATION_REGISTER_CONTRACT = "qtrad-r2-evaluation-register-v2"

_IDENTITY_FIELDS = frozenset(
    {
        "oof_id",
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
        "binding_id",
        "seal_id",
        "marker_id",
        "outcome_evidence_id",
        "projection_id",
        "registry_id",
    }
)

_IDENTITY_FIELD_BY_CONTRACT: dict[str, str] = {
    R2OofBundle.CONTRACT: "oof_id",
    R2ForecastManifest.CONTRACT: "manifest_id",
    R2_SELECTION_CONTRACT: "manifest_id",
    R2_EVALUATION_CONTRACT: "report_id",
    R2_LOCAL_COMPARATOR_CONTRACT: "manifest_id",
    R2_FEATURE_DATASET_CONTRACT: "dataset_id",
    "qtrad-research-forecasts-v1": "dataset_id",
    R2_PREPROCESSING_SELECTION_CONTRACT: "artifact_id",
    R2_FOLD_FIT_CONTRACT: "artifact_id",
    R2_FORECAST_COVERAGE_CONTRACT: "dataset_id",
    R2_COEFFICIENT_STABILITY_CONTRACT: "summary_id",
    R2_EVALUATION_REGISTER_CONTRACT: "report_id",
    "qtrad-r2-oof-run-descriptor-v1": "descriptor_id",
    "qtrad-r2-pooled-ablation-v1": "ablation_id",
    "qtrad-r2-pooled-ablation-v2": "ablation_id",
    R2HoldoutTargetSource.CONTRACT: "source_id",
    R2_HOLDOUT_SOURCE_BINDING_CONTRACT: "binding_id",
    R2_HOLDOUT_SELECTION_CONTRACT: "manifest_id",
    R2_HOLDOUT_FEATURES_CONTRACT: "dataset_id",
    R2_FINAL_FIT_CONTRACT: "fit_id",
    R2_HOLDOUT_FORECAST_CONTRACT: "dataset_id",
    R2_HOLDOUT_COVERAGE_CONTRACT: "coverage_id",
    R2_HOLDOUT_FORECAST_SEAL_CONTRACT: "seal_id",
    R2_HOLDOUT_OPENED_CONTRACT: "marker_id",
    R2_HOLDOUT_CONSUMED_CONTRACT: "marker_id",
    R2_CONFIRMATORY_OPENED_CONTRACT: "marker_id",
    R2_HOLDOUT_EVALUATION_CONTRACT: "evaluation_id",
    R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT: "outcome_evidence_id",
    R2_HOLDOUT_BUNDLE_CONTRACT: "bundle_id",
    R2_HOLDOUT_TARGET_PROJECTION_CONTRACT: "projection_id",
    R2_PRE_HOLDOUT_TARGET_PROJECTION_CONTRACT: "projection_id",
    R2_HOLDOUT_OPPORTUNITY_REGISTRY_CONTRACT: "registry_id",
}


def _identity_field_for_contract(contract: str) -> str:
    field = _IDENTITY_FIELD_BY_CONTRACT.get(contract)
    if field is None and contract.startswith("qtrad-test-child-"):
        field = "artifact_id"
    if field is None:
        raise ValueError(f"R2 child contract has no canonical identity field: {contract}")
    return field


def _canonical_payload_identity(contract: str, payload: Mapping[str, object]) -> str:
    if payload.get("contract") != contract:
        raise ValueError(f"R2 child payload contract mismatch: expected {contract}")
    if contract == R2_EVALUATION_CONTRACT:
        candidates = tuple(field for field in ("manifest_id", "report_id") if field in payload)
        if len(candidates) != 1:
            raise ValueError(
                f"R2 child contract {contract} must expose exactly one evaluation identity"
            )
        field = candidates[0]
    else:
        field = _identity_field_for_contract(contract)
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"R2 child contract {contract} requires canonical {field}")
    unexpected = _IDENTITY_FIELDS.intersection(payload) - {field}
    if contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT:
        unexpected -= {"source_id"}
    if unexpected:
        fields = ", ".join(sorted(unexpected))
        raise ValueError(
            f"R2 child contract {contract} has non-canonical identity field(s): {fields}"
        )
    return value


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
    *,
    prepublished_paths: set[str] | frozenset[str] = frozenset(),
) -> Path:
    """Persist named children and the OOF manifest without embedding child data."""
    from qtrad.runtime.r2_partitioned_rows import (
        PARTITIONED_ROWS_STORAGE,
        partitioned_manifest_part_paths,
    )

    refs = _all_oof_references(bundle)
    declared_paths = {ref.path for ref in refs}
    if set(children) != declared_paths:
        raise ValueError("OOF child payloads must exactly match declared references")
    if not set(prepublished_paths) <= declared_paths:
        raise ValueError("OOF prepublished paths must be declared references")

    partition_paths: list[str] = []
    for ref in refs:
        if ref.path in prepublished_paths:
            continue
        payload = children[ref.path]
        if payload.get("storage") == PARTITIONED_ROWS_STORAGE:
            partition_paths.extend(
                partitioned_manifest_part_paths(output, ref.path, payload)
            )

    created: list[Path] = []
    manifest_path = output / "manifest.json"
    try:
        for ref in refs:
            child_path = output / ref.path
            if ref.path in prepublished_paths:
                _verify_reference(output, ref)
                continue
            encoded = canonical_bytes(children[ref.path])
            if len(encoded) > _MAX_BYTES:
                raise ValueError(f"OOF child exceeds the 64 MiB limit: {ref.path}")
            atomic_create(child_path, encoded)
            created.append(child_path)
            _verify_reference(output, ref)
        atomic_create(manifest_path, canonical_bytes(bundle.as_json()))
        created.append(manifest_path)
        return manifest_path
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        for relative in partition_paths:
            (output / relative).unlink(missing_ok=True)
        directories = sorted(
            {
                (output / relative).parent
                for relative in partition_paths
            },
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for directory in directories:
            with suppress(OSError):
                directory.rmdir()
        raise


def _verify_r2_oof_bundle_with_source(path: Path) -> tuple[R2OofBundle, object | None]:
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
        "oof_id",
        "closure_id",
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
        oof_id=_text(payload["oof_id"]),
        closure_id=_text(payload["closure_id"]),
        holdout_target_source=(
            None
            if payload["holdout_target_source"] is None
            else ArtifactReference.from_json(payload["holdout_target_source"])
        ),
    )
    all_refs = _all_oof_references(bundle)
    canonical = any(
        reference.contract.startswith("qtrad-r2-")
        and reference.contract
        not in {
            "qtrad-r2-holdout-target-source-v1",
            R2_HOLDOUT_SOURCE_BINDING_CONTRACT,
        }
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
    from qtrad.runtime.r2_holdout_source import (
        bounded_manifest_part_paths,
        bounded_manifest_payload,
        bounded_source_closure_id,
        load_r2_holdout_target_source,
    )
    from qtrad.runtime.r2_partitioned_rows import (
        PARTITIONED_ROWS_STORAGE,
        partitioned_manifest_part_paths,
    )
    allowed_paths = {"manifest.json"} | {ref.path for ref in all_refs}
    binding_payload: dict[str, object] | None = None
    feature_bindings: list[tuple[ArtifactReference, dict[str, object]]] = []
    source_authority: object | None = None
    for ref in all_refs:
        _verify_reference(path.parent, ref)
        child = _load_object(path.parent / ref.path)
        if child.get("storage") == PARTITIONED_ROWS_STORAGE:
            allowed_paths.update(
                partitioned_manifest_part_paths(path.parent, ref.path, child)
            )
        if (
            ref.contract == R2_FEATURE_DATASET_CONTRACT
            and child.get("storage") == "qtrad-r2-feature-manifest-binding-v1"
        ):
            feature_bindings.append((ref, child))
        if ref.contract == R2HoldoutTargetSource.CONTRACT:
            source_path = path.parent / ref.path
            source = load_r2_holdout_target_source(source_path)
            if source.source_id != ref.semantic_id:
                raise ValueError("OOF holdout target source identity differs from its reference")
            source_parent = source_path.parent.relative_to(path.parent).as_posix()
            for part_path in bounded_manifest_part_paths(source_path):
                allowed_paths.add(
                    f"{source_parent}/{part_path}" if source_parent != "." else part_path
                )
        elif ref.contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT:
            binding_payload = child
        _verify_lineage_payload(child, bundle)
        if child.get("contract") == "qtrad-r2-oof-run-descriptor-v1":
            descriptor_id = child.get("descriptor_id")
            if not isinstance(descriptor_id, str):
                raise ValueError("R2 OOF descriptor must expose a descriptor ID")
            descriptor_payload = {
                key: value
                for key, value in child.items()
                if key
                not in {
                    "descriptor_id",
                    "runtime_inputs",
                    "application_identity",
                    "image_identity",
                    "python_identity",
                    "numpy_identity",
                    "sklearn_identity",
                }
            }
            if sha256(canonical_bytes(descriptor_payload)).hexdigest() != descriptor_id:
                raise ValueError("R2 OOF descriptor ID does not authenticate its semantic content")
            for provenance_field in (
                "application_identity",
                "image_identity",
                "python_identity",
                "numpy_identity",
                "sklearn_identity",
            ):
                if not isinstance(child.get(provenance_field), str) or not child[provenance_field]:
                    raise ValueError(f"R2 OOF descriptor provenance is missing {provenance_field}")
            if (
                child.get("foundation_bundle_id") != bundle.foundation_bundle_id
                or child.get("experiment_configuration_id") != bundle.experiment_configuration_id
                or child.get("source_class") != bundle.source_class.value
                or child.get("evidence_class") != bundle.evidence_class.value
                or child.get("holdout_excluded") is not True
            ):
                raise ValueError("R2 OOF descriptor lineage differs from its bundle")
            run_kind = child.get("run_kind")
            if run_kind in {"REPRESENTATIVE", "CONFIRMATORY"}:
                authority = child.get("foundation_authority")
                if not isinstance(authority, dict):
                    raise ValueError("canonical OOF descriptor has no foundation authority")
                if set(authority) != {
                    "foundation_id",
                    "closure_id",
                    "verification_id",
                    "promotion_id",
                    "source_class",
                    "evidence_class",
                }:
                    raise ValueError("OOF foundation authority fields are incomplete")
                for field in ("foundation_id", "closure_id", "verification_id"):
                    value = authority.get(field)
                    if (
                        not isinstance(value, str)
                        or len(value) != 64
                        or any(character not in "0123456789abcdef" for character in value)
                    ):
                        raise ValueError(f"OOF foundation authority has an invalid {field}")
                if authority.get("foundation_id") != bundle.foundation_bundle_id:
                    raise ValueError("OOF foundation authority differs from its bundle")
                if authority.get("source_class") != bundle.source_class.value:
                    raise ValueError("OOF foundation authority source differs from its bundle")
                if authority.get("evidence_class") != bundle.evidence_class.value:
                    raise ValueError("OOF foundation authority evidence differs from its bundle")
                promotion_id = authority.get("promotion_id")
                if promotion_id is not None and (
                    not isinstance(promotion_id, str)
                    or len(promotion_id) != 64
                    or any(character not in "0123456789abcdef" for character in promotion_id)
                ):
                    raise ValueError("OOF foundation authority has an invalid promotion ID")
                if (
                    run_kind == "CONFIRMATORY"
                    and bundle.source_class.value == "IBKR_HISTORICAL_RESEARCH"
                    and not isinstance(promotion_id, str)
                ):
                    raise ValueError("confirmatory IBKR OOF descriptor has no promotion authority")
                if run_kind == "REPRESENTATIVE" and promotion_id is not None:
                    raise ValueError(
                        "representative OOF descriptor cannot bind promotion authority"
                    )
                if (
                    bundle.source_class.value != "IBKR_HISTORICAL_RESEARCH"
                    and promotion_id is not None
                ):
                    raise ValueError("native OOF descriptor cannot bind promotion authority")
                runtime = child.get("runtime_inputs")
                expected_runtime_keys = {
                    "foundation",
                    "foundation_receipt",
                    "foundation_promotion",
                    "experiment",
                    "research_root",
                    "feature_manifests",
                }
                if (
                    bundle.holdout_target_source is not None
                    and bundle.holdout_target_source.contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT
                ):
                    expected_runtime_keys.add("holdout_target_source")
                if not isinstance(runtime, dict) or set(runtime) != expected_runtime_keys:
                    raise ValueError("canonical OOF descriptor runtime locators are incomplete")
                feature_manifests = runtime.get("feature_manifests")
                if not isinstance(feature_manifests, dict) or set(feature_manifests) != {
                    "L0",
                    "L1",
                    "P0",
                    "P1",
                }:
                    raise ValueError("canonical OOF descriptor feature locators are incomplete")
            elif "foundation_authority" in child or "runtime_inputs" in child:
                raise ValueError("synthetic OOF descriptor cannot bind runtime authority")
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
            nested = forecast_manifest.forecast_child
            _verify_reference(path.parent, nested)
            allowed_paths.add(nested.path)
            nested_payload = _load_object(path.parent / nested.path)
            if nested_payload.get("storage") == PARTITIONED_ROWS_STORAGE:
                allowed_paths.update(
                    partitioned_manifest_part_paths(path.parent, nested.path, nested_payload)
                )
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
    descriptor: dict[str, object] | None = None
    if len(descriptor_refs) == 1:
        descriptor = _load_object(path.parent / descriptor_refs[0].path)
        if descriptor.get("run_kind") == "REPRESENTATIVE" and bundle.holdout_target_source is None:
            raise ValueError("representative OOF bundle must bind an authenticated holdout source")
    if feature_bindings:
        if descriptor is None:
            raise ValueError("compact feature bindings require an OOF runtime descriptor")
        for reference, child in feature_bindings:
            _verify_feature_manifest_binding(path.parent, reference, child, descriptor)
    if bundle.holdout_target_source is not None and bundle.holdout_target_source.contract == (
        R2_HOLDOUT_SOURCE_BINDING_CONTRACT
    ):
        if binding_payload is None:
            raise ValueError("OOF holdout source binding child is missing")
        required_binding = {
            "contract",
            "schema_version",
            "source_id",
            "source_closure_id",
            "binding_id",
        }
        if (
            set(binding_payload) != required_binding
            or binding_payload.get("schema_version") != 1
            or not isinstance(binding_payload.get("source_id"), str)
            or not isinstance(binding_payload.get("source_closure_id"), str)
            or not isinstance(binding_payload.get("binding_id"), str)
        ):
            raise ValueError("OOF holdout source binding is malformed")
        binding_semantic = {
            key: binding_payload[key]
            for key in ("contract", "schema_version", "source_id", "source_closure_id")
        }
        binding_id = sha256(canonical_bytes(binding_semantic)).hexdigest()
        if (
            binding_payload["binding_id"] != binding_id
            or bundle.holdout_target_source.semantic_id != binding_id
        ):
            raise ValueError("OOF holdout source binding identity differs from its reference")
        if not isinstance(descriptor, dict):
            raise ValueError("OOF holdout source binding has no descriptor")
        raw_runtime = descriptor.get("runtime_inputs")
        if not isinstance(raw_runtime, dict):
            raise ValueError("OOF holdout source binding has no runtime locators")
        raw_source_path = raw_runtime.get("holdout_target_source")
        if not isinstance(raw_source_path, str):
            raise ValueError("OOF holdout source binding has no persisted source locator")
        source_path = Path(raw_source_path)
        if not source_path.is_absolute() or source_path.is_symlink() or not source_path.is_file():
            raise ValueError("OOF persisted source locator is unavailable")
        manifest = bounded_manifest_payload(source_path)
        if manifest is None or manifest.get("source_id") != binding_payload["source_id"]:
            raise ValueError("OOF persisted source manifest differs from its binding")
        closure_id = binding_payload["source_closure_id"]
        if (
            manifest.get("closure_id") != closure_id
            or bounded_source_closure_id(manifest) != closure_id
        ):
            raise ValueError("OOF persisted source closure differs from its binding")
        bounded_manifest_part_paths(source_path, payload=manifest)

    _allow_bound_selection(path.parent, bundle)
    _reject_orphan_files(path.parent, allowed_paths)
    return bundle, source_authority


def verify_r2_oof_bundle(path: Path) -> R2OofBundle:
    """Verify an OOF bundle and discard any consumed external source authority."""
    bundle, _source_authority = _verify_r2_oof_bundle_with_source(path)
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
        selection.get("oof_id") != bundle.oof_id
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
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_dir():
            if not any(
                path == relative or path.startswith(relative + "/") for path in allowed_paths
            ):
                raise ValueError(f"R2 bundle contains an orphaned directory: {relative}")
        elif candidate.is_file():
            if relative == "selection.json":
                continue
            if relative not in allowed_paths:
                raise ValueError(f"R2 bundle contains an orphaned child: {relative}")


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
    try:
        identity = _canonical_payload_identity(reference.contract, payload)
    except ValueError as exc:
        raise ValueError(f"R2 bundle child identity is invalid: {reference.path}: {exc}") from exc
    if identity != reference.semantic_id:
        raise ValueError(f"R2 bundle child semantic identity mismatch: {reference.path}")
    if payload.get("storage") == "qtrad-r2-partitioned-json-rows-v1":
        from qtrad.runtime.r2_partitioned_rows import partitioned_manifest_part_paths

        partitioned_manifest_part_paths(root, reference.path, payload)

def _verify_feature_manifest_binding(
    _root: Path,
    reference: ArtifactReference,
    payload: Mapping[str, object],
    descriptor: Mapping[str, object],
) -> None:
    if payload.get("storage") != "qtrad-r2-feature-manifest-binding-v1":
        raise ValueError(f"R2 feature binding has unsupported storage: {reference.path}")
    feature_set_name = payload.get("feature_set_name")
    if not isinstance(feature_set_name, str) or not feature_set_name:
        raise ValueError("R2 feature binding has no feature-set name")
    raw_runtime = descriptor.get("runtime_inputs")
    if not isinstance(raw_runtime, Mapping):
        raise ValueError("R2 feature binding has no runtime inputs")
    raw_manifests = raw_runtime.get("feature_manifests")
    if not isinstance(raw_manifests, Mapping):
        raise ValueError("R2 feature binding has no feature manifest locators")
    raw_path = raw_manifests.get(feature_set_name)
    if not isinstance(raw_path, str):
        raise ValueError(f"R2 feature binding has no locator for {feature_set_name}")
    manifest_path = Path(raw_path)
    if (
        not manifest_path.is_absolute()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        raise ValueError(f"R2 feature binding locator is unavailable: {feature_set_name}")
    from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore

    manifest = ParquetR2FeatureStore(manifest_path.parent, cast(Any, None)).read_manifest(
        manifest_path
    )
    declared_manifest = payload.get("manifest")
    if not isinstance(declared_manifest, Mapping):
        raise ValueError(f"R2 feature binding has no manifest payload: {reference.path}")
    manifest_payload = manifest.as_json()
    expected_manifest_fields = {
        "contract",
        "schema_version",
        "manifest_id",
        "manifest_sha256",
        "semantic_dataset_id",
        "feature_set_name",
        "feature_set_id",
    }
    if set(declared_manifest) != expected_manifest_fields:
        raise ValueError(f"R2 feature binding manifest fields are not compact: {reference.path}")
    for field in expected_manifest_fields:
        if declared_manifest[field] != manifest_payload[field]:
            raise ValueError(
                "R2 feature binding manifest differs from its runtime locator: "
                f"{reference.path} ({field})"
            )
    if payload.get("dataset_id") != manifest.semantic_dataset_id:
        raise ValueError(f"R2 feature binding dataset identity differs: {reference.path}")
    for field in (
        "feature_set_name",
        "feature_set_id",
        "raw_feature_schema_id",
        "observation_dataset_id",
        "panel_dataset_id",
        "target_dataset_id",
        "fold_dataset_id",
        "experiment_configuration_id",
        "evidence_class",
        "market_data_source_class",
        "holdout_excluded",
        "row_count",
    ):
        if payload.get(field) != manifest_payload.get(field):
            raise ValueError(f"R2 feature binding field differs from its manifest: {field}")


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
            and contract
            not in {
                "qtrad-r2-holdout-target-source-v1",
                R2_HOLDOUT_SOURCE_BINDING_CONTRACT,
                "qtrad-r2-feature-parquet-v2",
            }
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
