"""Immutable JSON persistence for R2.F1 evaluation and selection evidence."""

import json
import os
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from qtrad.application.r2_baselines import LocalRidgeOofResult
from qtrad.application.r2_evaluation import (
    EvaluationModel,
    verify_r2_evaluation,
    verify_selection_manifest,
)
from qtrad.application.r2_readiness import R1FoundationBindings
from qtrad.domain.events import JsonValue
from qtrad.domain.r2_evaluation import (
    ConfigurationRecord,
    EvaluationReport,
    LocalComparatorManifest,
    SelectionManifest,
)
from qtrad.domain.r2_features import R2FeatureDataset
from qtrad.domain.r2_readiness import R2ExperimentConfig

R2_EVALUATION_BUNDLE_CONTRACT = "qtrad-r2-evaluation-bundle-v1"


def write_r2_evaluation_bundle(
    output: Path,
    local_manifest: LocalComparatorManifest,
    report: EvaluationReport,
) -> Path:
    """Persist the report and every independently authenticated model child."""

    output.mkdir(parents=True, exist_ok=True)
    child_bytes: dict[str, tuple[str, bytes]] = {
        "evaluation": ("evaluation.json", _canonical_bytes(report.as_json())),
        "local_comparator": (
            "local-comparator.json",
            _canonical_bytes(local_manifest.as_json()),
        ),
    }
    for model in report.evaluated_models:
        name = f"evaluated_model::{model.model_family.value}"
        child_bytes[name] = (
            f"evaluated-model-{model.model_family.value.lower()}.json",
            _canonical_bytes(model.as_json()),
        )
    children: dict[str, JsonValue] = {}
    for name, (filename, content) in sorted(child_bytes.items()):
        _immutable_write(output / filename, content)
        children[name] = {"path": filename, "sha256": sha256(content).hexdigest()}
    bundle: dict[str, JsonValue] = {
        "contract": R2_EVALUATION_BUNDLE_CONTRACT,
        "schema_version": 1,
        "evaluation_report_id": report.report_id,
        "local_comparator_manifest_id": local_manifest.manifest_id,
        "evaluated_model_manifest_ids": [item.manifest_id for item in report.evaluated_models],
        "children": children,
    }
    bundle_path = output / "manifest.json"
    _immutable_write(bundle_path, _canonical_bytes(bundle))
    return bundle_path


def verify_persisted_r2_evaluation(
    bundle_path: Path,
    report: EvaluationReport,
    local_manifest: LocalComparatorManifest,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    local_result: LocalRidgeOofResult,
    models: tuple[EvaluationModel, ...],
    configurations: tuple[ConfigurationRecord, ...],
    *,
    local_feature_set_id: str,
    local_feature_datasets: Sequence[R2FeatureDataset],
) -> None:
    """Verify bytes, every independent child and a complete evaluation replay."""

    payload = _object(json.loads(bundle_path.read_bytes()))
    if set(payload) != {
        "contract",
        "schema_version",
        "evaluation_report_id",
        "local_comparator_manifest_id",
        "evaluated_model_manifest_ids",
        "children",
    }:
        raise ValueError("R2 evaluation bundle has unexpected fields")
    if payload["contract"] != R2_EVALUATION_BUNDLE_CONTRACT or payload["schema_version"] != 1:
        raise ValueError("R2 evaluation bundle contract is unsupported")
    if (
        payload["evaluation_report_id"] != report.report_id
        or payload["local_comparator_manifest_id"] != local_manifest.manifest_id
        or payload["evaluated_model_manifest_ids"]
        != [item.manifest_id for item in report.evaluated_models]
    ):
        raise ValueError("R2 evaluation bundle child identities differ")
    children = _object(payload["children"])
    expected: dict[str, tuple[str, bytes]] = {
        "evaluation": ("evaluation.json", _canonical_bytes(report.as_json())),
        "local_comparator": (
            "local-comparator.json",
            _canonical_bytes(local_manifest.as_json()),
        ),
    }
    for model in report.evaluated_models:
        expected[f"evaluated_model::{model.model_family.value}"] = (
            f"evaluated-model-{model.model_family.value.lower()}.json",
            _canonical_bytes(model.as_json()),
        )
    if set(children) != set(expected):
        raise ValueError("R2 evaluation bundle child set is incomplete")
    for name, (expected_path, expected_bytes) in expected.items():
        reference = _object(children[name])
        if set(reference) != {"path", "sha256"} or reference["path"] != expected_path:
            raise ValueError(f"R2 evaluation {name} reference is invalid")
        child_path = _safe_child(bundle_path.parent, expected_path)
        child_content = child_path.read_bytes()
        if (
            child_content != expected_bytes
            or reference["sha256"] != sha256(child_content).hexdigest()
        ):
            raise ValueError(f"R2 evaluation {name} child failed authentication")
    verify_r2_evaluation(
        report,
        local_manifest,
        verified,
        experiment,
        local_result,
        models,
        configurations,
        local_feature_set_id=local_feature_set_id,
        local_feature_datasets=local_feature_datasets,
    )


def write_r2_selection_manifest(path: Path, manifest: SelectionManifest) -> None:
    _immutable_write(path, _canonical_bytes(manifest.as_json()))


def verify_persisted_r2_selection(
    path: Path,
    manifest: SelectionManifest,
    report: EvaluationReport,
    local_manifest: LocalComparatorManifest,
    experiment: R2ExperimentConfig,
) -> None:
    if path.read_bytes() != _canonical_bytes(manifest.as_json()):
        raise ValueError("persisted R2 selection bytes differ from the supplied manifest")
    verify_selection_manifest(manifest, report, local_manifest, experiment)


def _canonical_bytes(value: dict[str, JsonValue]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _immutable_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(
                f"immutable R2 artefact already exists with different content: {path}"
            )
        return
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


def _safe_child(parent: Path, name: str) -> Path:
    child = parent / name
    if child.parent.resolve() != parent.resolve():
        raise ValueError("R2 evidence child path escapes its bundle")
    return child


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected a JSON object")
    return cast(dict[str, object], value)
