"""Create-only persistence and marker-first reveal for R2.G2 holdouts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import ArtifactReference
from qtrad.domain.r2_holdout import (
    R2_HOLDOUT_BUNDLE_CONTRACT,
    R2_HOLDOUT_CONSUMED_CONTRACT,
    R2_HOLDOUT_COVERAGE_CONTRACT,
    R2_HOLDOUT_EVALUATION_CONTRACT,
    R2_HOLDOUT_FEATURES_CONTRACT,
    R2_HOLDOUT_FORECAST_CONTRACT,
    R2_HOLDOUT_FORECAST_SEAL_CONTRACT,
    R2_HOLDOUT_OPENED_CONTRACT,
    R2_HOLDOUT_SELECTION_CONTRACT,
    EvidenceClass,
    HoldoutConclusion,
    HoldoutMarkerState,
    HoldoutScope,
    R2HoldoutBundle,
    R2HoldoutConsumedMarker,
    R2HoldoutEvaluation,
    R2HoldoutFeatureDataset,
    R2HoldoutForecastSeal,
    R2HoldoutOpenedMarker,
    R2HoldoutQuestionResult,
    R2HoldoutSelectionManifest,
)
from qtrad.runtime.r2_bundles import atomic_create, canonical_bytes

_MAX_BYTES = 64 * 1024 * 1024
_FAILURE_EVALUATION_ID = sha256(b"qtrad-r2-holdout-reveal-failed").hexdigest()


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"holdout child must be a regular non-symlink file: {path}")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_BYTES:
        raise ValueError(f"holdout child exceeds the {_MAX_BYTES} byte limit: {path}")
    value = json.loads(encoded)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"holdout child must be a JSON object: {path}")
    return cast(dict[str, object], value)


def _semantic_id(payload: Mapping[str, object], identity_key: str) -> str:
    semantic = {key: value for key, value in payload.items() if key != identity_key}
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _safe_child(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or "\\" in relative
        or any(part in ("", ".", "..") for part in candidate.parts)
    ):
        raise ValueError(f"holdout child path is unsafe: {relative}")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"holdout child path escapes its root: {relative}")
    return resolved


def _verify_child(
    root: Path,
    relative: str,
    *,
    contract: str,
    identity_key: str,
    expected_fields: set[str],
    expected_id: str | None = None,
) -> dict[str, object]:
    path = _safe_child(root, relative)
    payload = _load_object(path)
    if set(payload) != expected_fields:
        raise ValueError(f"{contract} child has unknown or missing fields")
    if payload.get("contract") != contract or payload.get("schema_version") != 1:
        raise ValueError(f"{contract} child contract is unsupported")
    identity = payload.get(identity_key)
    if not isinstance(identity, str) or identity != _semantic_id(payload, identity_key):
        raise ValueError(f"{contract} child identity does not authenticate its content")
    if expected_id is not None and identity != expected_id:
        raise ValueError(f"{contract} child identity differs from its declared reference")
    return payload


def _as_json(value: object) -> Mapping[str, object]:
    serializer = getattr(value, "as_json", None)
    if not callable(serializer):
        raise TypeError("holdout child must expose as_json")
    return cast(Mapping[str, object], cast(Callable[[], object], serializer)())


def _object_dict(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a JSON object")
    return cast(dict[str, object], value)


def _object_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _float_value(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def _optional_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _float_value(value, field)


def _int_value(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    atomic_create(path, canonical_bytes(payload))


_SELECTION_FIELDS = {
    "contract",
    "schema_version",
    "experiment_configuration_id",
    "foundation_bundle_id",
    "oof_bundle_id",
    "evaluation_report_id",
    "prior_selection_manifest_id",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "evaluated_configuration_ids",
    "selected_configuration_ids",
    "control_configuration_ids",
    "holdout_configuration_ids",
    "comparator_families",
    "metric_policy",
    "threshold_policy",
    "final_fitting_policy",
    "questions",
    "holdout_range",
    "experiment_count",
    "runtime_identities",
    "frozen_metadata",
    "frozen_at",
    "frozen_by",
    "state",
    "holdout_outcomes_accessed",
    "manifest_id",
}
_FEATURE_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "experiment_configuration_id",
    "foundation_bundle_id",
    "observation_dataset_id",
    "panel_dataset_id",
    "feature_schema_id",
    "feature_set_id",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_range",
    "expected_opportunity_ids",
    "unavailable_opportunity_ids",
    "rows",
    "outcome_blind_projection",
    "holdout_outcomes_accessed",
    "dataset_id",
}
_FINAL_FIT_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "configuration_id",
    "model_family",
    "feature_dataset_id",
    "feature_schema_id",
    "training_cutoff",
    "training_target_ids",
    "purged_target_ids",
    "inner_fit_target_ids",
    "inner_validation_target_ids",
    "preprocessing",
    "alpha_candidate_scores",
    "selected_alpha",
    "sample_weights",
    "coefficients",
    "intercept",
    "disposition",
    "failure_reason",
    "diagnostics",
    "runtime_identities",
    "evidence_class",
    "holdout_scope",
    "fit_id",
}
_FORECAST_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "feature_dataset_id",
    "configuration_id",
    "final_fit_id",
    "rows",
    "expected_opportunity_ids",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_outcomes_accessed",
    "dataset_id",
}
_COVERAGE_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "feature_dataset_id",
    "configuration_id",
    "expected_opportunity_ids",
    "rows",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_outcomes_accessed",
    "coverage_id",
}
_SEAL_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "feature_dataset_id",
    "final_fit_ids",
    "forecast_dataset_ids",
    "coverage_ids",
    "metric_policy",
    "comparison_support",
    "forecast_buckets",
    "state_buckets",
    "configuration_pairs",
    "coverage_rules",
    "questions",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "runtime_identities",
    "prepared_at",
    "prepared_by",
    "state",
    "holdout_outcomes_accessed",
    "seal_id",
}
_OPENED_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "opened_at",
    "opened_by",
    "acknowledgement",
    "expected_selection_manifest_id",
    "expected_seal_id",
    "state",
    "marker_id",
}
_CONSUMED_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "opened_marker_id",
    "consumed_at",
    "consumed_by",
    "evaluation_id",
    "outcome_accessed",
    "state",
    "marker_id",
}
_EVALUATION_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "opened_marker_id",
    "consumed_marker_id",
    "questions",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_outcomes_accessed",
    "evaluation_id",
}


def write_holdout_selection(output: Path, manifest: R2HoldoutSelectionManifest) -> Path:
    """Persist PR A's selection freeze as one create-only file."""
    if manifest.holdout_scope is HoldoutScope.CONFIRMATORY:
        raise ValueError("G2 selection freeze is restricted to disposable fixtures")
    _write_json(output, manifest.as_json())
    return output


def verify_holdout_selection(path: Path) -> R2HoldoutSelectionManifest:
    payload = _verify_child(
        path.parent,
        path.name,
        contract=R2_HOLDOUT_SELECTION_CONTRACT,
        identity_key="manifest_id",
        expected_fields=_SELECTION_FIELDS,
    )
    return R2HoldoutSelectionManifest.from_json(payload)


def _verify_prepare_children(root: Path, seal: R2HoldoutForecastSeal) -> None:
    feature = _verify_child(
        root,
        "features.json",
        contract=R2_HOLDOUT_FEATURES_CONTRACT,
        identity_key="dataset_id",
        expected_fields=_FEATURE_FIELDS,
        expected_id=seal.feature_dataset_id,
    )
    if feature["selection_manifest_id"] != seal.selection_manifest_id:
        raise ValueError("holdout features are bound to a different selection")
    expected_fits: set[str] = set()
    expected_forecasts: set[str] = set()
    expected_coverage: set[str] = set()
    for fit_id in seal.final_fit_ids:
        payload = _verify_child(
            root,
            f"fits/{fit_id}.json",
            contract="qtrad-r2-final-fit-v1",
            identity_key="fit_id",
            expected_fields=_FINAL_FIT_FIELDS,
            expected_id=fit_id,
        )
        expected_fits.add(str(payload["fit_id"]))
        if payload["selection_manifest_id"] != seal.selection_manifest_id:
            raise ValueError("final fit is bound to a different selection")
    for dataset_id in seal.forecast_dataset_ids:
        payload = _verify_child(
            root,
            f"forecasts/{dataset_id}.json",
            contract=R2_HOLDOUT_FORECAST_CONTRACT,
            identity_key="dataset_id",
            expected_fields=_FORECAST_FIELDS,
            expected_id=dataset_id,
        )
        expected_forecasts.add(str(payload["dataset_id"]))
        if (
            payload["selection_manifest_id"] != seal.selection_manifest_id
            or payload["feature_dataset_id"] != seal.feature_dataset_id
        ):
            raise ValueError("holdout forecast lineage differs from its seal")
    for coverage_id in seal.coverage_ids:
        payload = _verify_child(
            root,
            f"coverage/{coverage_id}.json",
            contract=R2_HOLDOUT_COVERAGE_CONTRACT,
            identity_key="coverage_id",
            expected_fields=_COVERAGE_FIELDS,
            expected_id=coverage_id,
        )
        expected_coverage.add(str(payload["coverage_id"]))
        if (
            payload["selection_manifest_id"] != seal.selection_manifest_id
            or payload["feature_dataset_id"] != seal.feature_dataset_id
        ):
            raise ValueError("holdout coverage lineage differs from its seal")
    if expected_fits != set(seal.final_fit_ids):
        raise ValueError("holdout final-fit children do not reconcile to the seal")
    if expected_forecasts != set(seal.forecast_dataset_ids):
        raise ValueError("holdout forecast children do not reconcile to the seal")
    if expected_coverage != set(seal.coverage_ids):
        raise ValueError("holdout coverage children do not reconcile to the seal")


def write_holdout_preparation(
    output: Path,
    *,
    selection: R2HoldoutSelectionManifest,
    feature_dataset: object,
    final_fits: Mapping[str, object],
    forecasts: Mapping[str, object],
    coverage: Mapping[str, object],
    seal: R2HoldoutForecastSeal,
) -> Path:
    """Persist all PR B children and the seal without overwriting any path."""
    if seal.holdout_scope is HoldoutScope.CONFIRMATORY:
        raise ValueError("G2 preparation is restricted to disposable fixtures")
    if selection.manifest_id != seal.selection_manifest_id:
        raise ValueError("selection and seal lineage differs")
    if not isinstance(feature_dataset, R2HoldoutFeatureDataset):
        raise TypeError("feature_dataset must be an R2HoldoutFeatureDataset")
    if set(final_fits) != set(seal.final_fit_ids):
        raise ValueError("final-fit arguments do not exactly match the seal")
    if set(forecasts) != set(seal.forecast_dataset_ids):
        raise ValueError("forecast arguments do not exactly match the seal")
    if set(coverage) != set(seal.coverage_ids):
        raise ValueError("coverage arguments do not exactly match the seal")
    selection_path = output / "selection.json"
    if selection_path.exists() or selection_path.is_symlink():
        existing = verify_holdout_selection(selection_path)
        if existing.manifest_id != selection.manifest_id:
            raise ValueError("existing selection child differs from preparation selection")
    else:
        _write_json(selection_path, selection.as_json())
    _write_json(output / "features.json", feature_dataset.as_json())
    for fit_id in seal.final_fit_ids:
        child = final_fits[fit_id]
        payload = _as_json(child)
        _write_json(output / "fits" / f"{fit_id}.json", payload)
    for dataset_id in seal.forecast_dataset_ids:
        child = forecasts[dataset_id]
        payload = _as_json(child)
        _write_json(
            output / "forecasts" / f"{dataset_id}.json",
            payload,
        )
    for coverage_id in seal.coverage_ids:
        child = coverage[coverage_id]
        payload = _as_json(child)
        _write_json(
            output / "coverage" / f"{coverage_id}.json",
            payload,
        )
    _write_json(output / "manifest.json", seal.as_json())
    return output / "manifest.json"


def verify_holdout_preparation(path: Path) -> R2HoldoutForecastSeal:
    seal_payload = _verify_child(
        path,
        "manifest.json",
        contract=R2_HOLDOUT_FORECAST_SEAL_CONTRACT,
        identity_key="seal_id",
        expected_fields=_SEAL_FIELDS,
    )
    seal = R2HoldoutForecastSeal.from_json(seal_payload)
    _verify_prepare_children(path, seal)
    allowed = {"manifest.json", "selection.json", "features.json"}
    allowed.update(f"fits/{fit_id}.json" for fit_id in seal.final_fit_ids)
    allowed.update(f"forecasts/{dataset_id}.json" for dataset_id in seal.forecast_dataset_ids)
    allowed.update(f"coverage/{coverage_id}.json" for coverage_id in seal.coverage_ids)
    for lifecycle_name in ("opened.json", "consumed.json", "evaluation.json"):
        if (path / lifecycle_name).is_file():
            allowed.add(lifecycle_name)
    _reject_orphans(path, allowed)
    return seal


def _reject_orphans(root: Path, allowed: set[str]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("holdout bundle root must be a regular directory")
    actual: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"holdout bundle contains a symlink: {candidate}")
        if candidate.is_file():
            actual.add(candidate.relative_to(root).as_posix())
    if actual != allowed:
        extra = sorted(actual - allowed)
        missing = sorted(allowed - actual)
        raise ValueError(f"holdout bundle file closure differs; extra={extra}, missing={missing}")


def _opened_from_payload(payload: Mapping[str, object]) -> R2HoldoutOpenedMarker:
    if set(payload) != _OPENED_FIELDS:
        raise ValueError("opened marker has unknown or missing fields")
    if payload.get("contract") != R2_HOLDOUT_OPENED_CONTRACT or payload.get("schema_version") != 1:
        raise ValueError("opened marker contract is unsupported")
    return R2HoldoutOpenedMarker(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        seal_id=str(payload["seal_id"]),
        opened_at=datetime.fromisoformat(str(payload["opened_at"])),
        opened_by=str(payload["opened_by"]),
        acknowledgement=str(payload["acknowledgement"]),
        expected_selection_manifest_id=str(payload["expected_selection_manifest_id"]),
        expected_seal_id=str(payload["expected_seal_id"]),
        state=HoldoutMarkerState(str(payload["state"])),
        marker_id=str(payload["marker_id"]),
    )


def _consumed_from_payload(payload: Mapping[str, object]) -> R2HoldoutConsumedMarker:
    if set(payload) != _CONSUMED_FIELDS:
        raise ValueError("consumed marker has unknown or missing fields")
    if (
        payload.get("contract") != R2_HOLDOUT_CONSUMED_CONTRACT
        or payload.get("schema_version") != 1
    ):
        raise ValueError("consumed marker contract is unsupported")
    return R2HoldoutConsumedMarker(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        seal_id=str(payload["seal_id"]),
        opened_marker_id=str(payload["opened_marker_id"]),
        consumed_at=datetime.fromisoformat(str(payload["consumed_at"])),
        consumed_by=str(payload["consumed_by"]),
        evaluation_id=str(payload["evaluation_id"]),
        outcome_accessed=bool(payload["outcome_accessed"]),
        state=HoldoutMarkerState(str(payload["state"])),
        marker_id=str(payload["marker_id"]),
    )


def _write_opened_marker(
    root: Path,
    *,
    selection_manifest_id: str,
    seal_id: str,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    opened_by: str,
    opened_at: object,
    acknowledgement: str,
) -> R2HoldoutOpenedMarker:
    if root.joinpath("opened.json").exists() or root.joinpath("opened.json").is_symlink():
        raise FileExistsError("holdout has already been opened")
    if not isinstance(opened_at, datetime):
        raise TypeError("opened_at must be a datetime")
    marker = R2HoldoutOpenedMarker.create(
        selection_manifest_id=selection_manifest_id,
        seal_id=seal_id,
        opened_at=opened_at,
        opened_by=opened_by,
        acknowledgement=acknowledgement,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
    )
    _write_json(root / "opened.json", marker.as_json())
    return marker


def reveal_holdout(
    root: Path,
    *,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    acknowledgement: str,
    opened_by: str,
    consumed_by: str,
    opened_at: object,
    consumed_at: object,
    outcome_loader: Callable[[], Mapping[str, float]],
    evaluator: Callable[[Mapping[str, float], R2HoldoutOpenedMarker], R2HoldoutEvaluation],
) -> tuple[R2HoldoutEvaluation | None, R2HoldoutConsumedMarker]:
    """Atomically record OPENED, then load/evaluate, and always record CONSUMED.

    The callback is intentionally the first code allowed to receive realised
    outcomes.  Any callback exception is re-raised after the consumed marker is
    durably created.
    """
    seal = verify_holdout_preparation(root)
    if (
        seal.selection_manifest_id != expected_selection_manifest_id
        or seal.seal_id != expected_seal_id
    ):
        raise ValueError("reveal IDs do not match the exact prepared seal")
    selection_path = root / "selection.json"
    if selection_path.exists():
        selection = verify_holdout_selection(selection_path)
        if selection.manifest_id != expected_selection_manifest_id:
            raise ValueError("selection child differs from expected reveal selection")
    else:
        raise FileNotFoundError("prepared holdout root must contain selection.json")
    if root.joinpath("consumed.json").exists() or root.joinpath("consumed.json").is_symlink():
        raise FileExistsError("holdout has already been consumed")
    marker = _write_opened_marker(
        root,
        selection_manifest_id=selection.manifest_id,
        seal_id=seal.seal_id,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
        opened_by=opened_by,
        opened_at=opened_at,
        acknowledgement=acknowledgement,
    )
    evaluation: R2HoldoutEvaluation | None = None
    evaluation_id = _FAILURE_EVALUATION_ID
    error: BaseException | None = None
    try:
        outcomes = outcome_loader()
        evaluation = evaluator(outcomes, marker)
        if (
            evaluation.selection_manifest_id != selection.manifest_id
            or evaluation.seal_id != seal.seal_id
        ):
            raise ValueError("holdout evaluation lineage differs from the opened seal")
        _write_json(root / "evaluation.json", evaluation.as_json())
        evaluation_id = evaluation.evaluation_id
    except BaseException as exc:
        error = exc
        evaluation_id = _FAILURE_EVALUATION_ID
    finally:
        if not isinstance(consumed_at, datetime):
            raise TypeError("consumed_at must be a datetime")
        consumed = R2HoldoutConsumedMarker.create(
            selection_manifest_id=selection.manifest_id,
            seal_id=seal.seal_id,
            opened_marker_id=marker.marker_id,
            consumed_at=consumed_at,
            consumed_by=consumed_by,
            evaluation_id=evaluation_id,
        )
        _write_json(root / "consumed.json", consumed.as_json())
    if error is not None:
        raise error
    if evaluation is None:
        raise RuntimeError("holdout evaluation unexpectedly missing")
    return evaluation, consumed


def recover_holdout_consumption(
    root: Path,
    *,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    consumed_by: str,
    consumed_at: object,
    evaluation_id: str = _FAILURE_EVALUATION_ID,
) -> R2HoldoutConsumedMarker:
    """Recover only the missing consumed marker; never reloads or refits."""
    seal = verify_holdout_preparation(root)
    if (
        seal.selection_manifest_id != expected_selection_manifest_id
        or seal.seal_id != expected_seal_id
    ):
        raise ValueError("recovery requires the exact original seal IDs")
    opened_payload = _verify_child(
        root,
        "opened.json",
        contract=R2_HOLDOUT_OPENED_CONTRACT,
        identity_key="marker_id",
        expected_fields=_OPENED_FIELDS,
    )
    opened = _opened_from_payload(opened_payload)
    if (
        opened.selection_manifest_id != expected_selection_manifest_id
        or opened.seal_id != expected_seal_id
    ):
        raise ValueError("recovery opened marker differs from the exact original seal")
    if root.joinpath("consumed.json").exists() or root.joinpath("consumed.json").is_symlink():
        raise FileExistsError("holdout has already been consumed")
    if not isinstance(consumed_at, datetime):
        raise TypeError("consumed_at must be a datetime")
    consumed = R2HoldoutConsumedMarker.create(
        selection_manifest_id=expected_selection_manifest_id,
        seal_id=expected_seal_id,
        opened_marker_id=opened.marker_id,
        consumed_at=consumed_at,
        consumed_by=consumed_by,
        evaluation_id=evaluation_id,
    )
    _write_json(root / "consumed.json", consumed.as_json())
    return consumed


def verify_holdout_markers(root: Path) -> tuple[R2HoldoutOpenedMarker, R2HoldoutConsumedMarker]:
    opened_payload = _verify_child(
        root,
        "opened.json",
        contract=R2_HOLDOUT_OPENED_CONTRACT,
        identity_key="marker_id",
        expected_fields=_OPENED_FIELDS,
    )
    consumed_payload = _verify_child(
        root,
        "consumed.json",
        contract=R2_HOLDOUT_CONSUMED_CONTRACT,
        identity_key="marker_id",
        expected_fields=_CONSUMED_FIELDS,
    )
    opened = _opened_from_payload(opened_payload)
    consumed = _consumed_from_payload(consumed_payload)
    if consumed.opened_marker_id != opened.marker_id:
        raise ValueError("consumed marker does not bind the opened marker")
    if (consumed.selection_manifest_id, consumed.seal_id) != (
        opened.selection_manifest_id,
        opened.seal_id,
    ):
        raise ValueError("holdout marker lineage is inconsistent")
    return opened, consumed


def verify_holdout_evaluation(root: Path) -> R2HoldoutEvaluation:
    payload = _verify_child(
        root,
        "evaluation.json",
        contract=R2_HOLDOUT_EVALUATION_CONTRACT,
        identity_key="evaluation_id",
        expected_fields=_EVALUATION_FIELDS,
    )
    seal = verify_holdout_preparation(root)
    opened, consumed = verify_holdout_markers(root)
    question_values = _object_list(payload["questions"], "holdout evaluation questions")
    results_list: list[R2HoldoutQuestionResult] = []
    for raw_item in question_values:
        item = _object_dict(raw_item, "holdout evaluation question result")
        results_list.append(
            R2HoldoutQuestionResult(
                question_id=str(item["question_id"]),
                metric=str(item["metric"]),
                candidate_value=_optional_float(item["candidate_value"], "candidate metric value"),
                comparator_value=_optional_float(
                    item["comparator_value"], "comparator metric value"
                ),
                delta=_optional_float(item["delta"], "metric delta"),
                support_count=_int_value(item["support_count"], "question support"),
                coverage=_float_value(item["coverage"], "question coverage"),
                conclusion=HoldoutConclusion(str(item["conclusion"])),
                reason=str(item["reason"]),
            )
        )
    results = tuple(results_list)
    evaluation = R2HoldoutEvaluation(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        seal_id=str(payload["seal_id"]),
        opened_marker_id=str(payload["opened_marker_id"]),
        consumed_marker_id=str(payload["consumed_marker_id"]),
        questions=results,
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        holdout_outcomes_accessed=bool(payload["holdout_outcomes_accessed"]),
        evaluation_id=str(payload["evaluation_id"]),
    )
    if (
        evaluation.selection_manifest_id != seal.selection_manifest_id
        or evaluation.seal_id != seal.seal_id
        or evaluation.opened_marker_id != opened.marker_id
        or evaluation.consumed_marker_id not in {consumed.marker_id, "0" * 64}
        or tuple(item.question_id for item in results)
        != tuple(item.question_id for item in seal.questions)
    ):
        raise ValueError("holdout evaluation does not cover the exact frozen seal")
    return evaluation


def write_holdout_bundle(
    output: Path,
    bundle: R2HoldoutBundle,
    children: Mapping[str, Mapping[str, object]],
) -> Path:
    refs = (
        bundle.selection,
        bundle.forecast_seal,
        bundle.opened_marker,
        bundle.consumed_marker,
        bundle.evaluation,
        *bundle.replay_evidence,
    )
    if set(children) != {ref.path for ref in refs}:
        raise ValueError("holdout bundle children must exactly match declared references")
    for ref in refs:
        if ref.path == "manifest.json":
            raise ValueError("holdout bundle manifest path is reserved")
        payload = children[ref.path]
        if sha256(canonical_bytes(payload)).hexdigest() != ref.sha256:
            raise ValueError(f"holdout child digest differs from its reference: {ref.path}")
        _write_json(output / ref.path, payload)
    _write_json(output / "manifest.json", bundle.as_json())
    return output / "manifest.json"


def verify_holdout_bundle(path: Path) -> R2HoldoutBundle:
    payload = _load_object(path / "manifest.json")
    expected = {
        "contract",
        "schema_version",
        "selection",
        "forecast_seal",
        "opened_marker",
        "consumed_marker",
        "evaluation",
        "replay_evidence",
        "source_class",
        "evidence_class",
        "holdout_scope",
        "bundle_id",
    }
    if set(payload) != expected or payload.get("contract") != R2_HOLDOUT_BUNDLE_CONTRACT:
        raise ValueError("holdout bundle manifest has unknown or unsupported fields")
    refs: list[ArtifactReference] = []
    for key in ("selection", "forecast_seal", "opened_marker", "consumed_marker", "evaluation"):
        refs.append(ArtifactReference.from_json(payload[key]))
    replay = payload["replay_evidence"]
    if not isinstance(replay, list):
        raise ValueError("holdout replay evidence must be an array")
    refs.extend(ArtifactReference.from_json(item) for item in replay)
    for ref in refs:
        child = _safe_child(path, ref.path)
        if not child.is_file() or child.is_symlink():
            raise ValueError(f"holdout bundle child is unavailable: {ref.path}")
        if sha256(child.read_bytes()).hexdigest() != ref.sha256:
            raise ValueError(f"holdout bundle child changed: {ref.path}")
        if _load_object(child).get("contract") != ref.contract:
            raise ValueError(f"holdout bundle child contract differs: {ref.path}")
    _reject_orphans(path, {"manifest.json", *(ref.path for ref in refs)})
    semantic = {key: value for key, value in payload.items() if key != "bundle_id"}
    expected_id = sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if payload["bundle_id"] != expected_id:
        raise ValueError("holdout bundle ID does not authenticate its content")
    return R2HoldoutBundle(
        selection=refs[0],
        forecast_seal=refs[1],
        opened_marker=refs[2],
        consumed_marker=refs[3],
        evaluation=refs[4],
        replay_evidence=tuple(refs[5:]),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        bundle_id=str(payload["bundle_id"]),
    )


def load_prior_selection_manifest(path: Path) -> object:
    """Load and independently authenticate the existing v2 selection contract."""
    from qtrad.domain.r2_evaluation import SelectionManifest
    from qtrad.domain.r2_readiness import ModelFamily
    from qtrad.runtime.r2_verification import _selection_decisions_from_payload

    payload = _load_object(path)
    expected = {
        "contract",
        "schema_version",
        "experiment_configuration_id",
        "evidence_class",
        "evaluation_report_id",
        "local_comparator_manifest_id",
        "evaluated_configuration_ids",
        "predeclared_comparators",
        "primary_metric",
        "secondary_metrics",
        "acceptance_thresholds",
        "decisions",
        "selected_configuration_ids",
        "holdout_comparator_configuration_ids",
        "final_fitting_procedure",
        "holdout_range",
        "holdout_state_verification",
        "application_image_identity",
        "frozen_at",
        "frozen_by",
        "manifest_id",
    }
    optional = {"source_class", "foundation_bundle_id", "oof_bundle_id"}
    if set(payload) - expected - optional or not expected <= set(payload):
        raise ValueError("prior R2 selection has unknown or missing fields")
    if payload["contract"] != "qtrad-r2-selection-v2" or payload["schema_version"] != 1:
        raise ValueError("prior R2 selection is not qtrad-r2-selection-v2")
    raw_thresholds = _object_dict(payload["acceptance_thresholds"], "prior R2 selection thresholds")
    raw_decisions = _object_list(payload["decisions"], "prior R2 selection decisions")
    source = payload.get("source_class")
    foundation = payload.get("foundation_bundle_id")
    oof = payload.get("oof_bundle_id")
    return SelectionManifest(
        experiment_configuration_id=str(payload["experiment_configuration_id"]),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        evaluation_report_id=str(payload["evaluation_report_id"]),
        local_comparator_manifest_id=str(payload["local_comparator_manifest_id"]),
        evaluated_configuration_ids=tuple(
            str(item) for item in cast(list[object], payload["evaluated_configuration_ids"])
        ),
        predeclared_comparators=tuple(
            ModelFamily(str(item))
            for item in cast(list[object], payload["predeclared_comparators"])
        ),
        primary_metric=str(payload["primary_metric"]),
        secondary_metrics=tuple(
            str(item) for item in cast(list[object], payload["secondary_metrics"])
        ),
        acceptance_thresholds=tuple(
            sorted(
                (str(key), _float_value(value, "selection threshold"))
                for key, value in raw_thresholds.items()
            )
        ),
        decisions=_selection_decisions_from_payload(raw_decisions),
        selected_configuration_ids=tuple(
            str(item) for item in cast(list[object], payload["selected_configuration_ids"])
        ),
        holdout_comparator_configuration_ids=tuple(
            str(item)
            for item in cast(list[object], payload["holdout_comparator_configuration_ids"])
        ),
        final_fitting_procedure=str(payload["final_fitting_procedure"]),
        holdout_range=(
            datetime.fromisoformat(str(cast(list[object], payload["holdout_range"])[0])),
            datetime.fromisoformat(str(cast(list[object], payload["holdout_range"])[1])),
        ),
        holdout_state_verification=str(payload["holdout_state_verification"]),
        application_image_identity=str(payload["application_image_identity"]),
        frozen_at=datetime.fromisoformat(str(payload["frozen_at"])),
        frozen_by=str(payload["frozen_by"]),
        manifest_id=str(payload["manifest_id"]),
        market_data_source_class=(None if source is None else MarketDataSourceClass(str(source))),
        foundation_bundle_id=None if foundation is None else str(foundation),
        oof_bundle_id=None if oof is None else str(oof),
    )


def load_holdout_policy(path: Path):
    from qtrad.domain.r2_holdout import R2FinalFittingPolicy

    return R2FinalFittingPolicy.from_json(_load_object(path))


def load_holdout_questions(path: Path):
    from qtrad.domain.r2_holdout import R2HoldoutQuestion

    payload = _load_object(path)
    raw = payload.get("questions")
    if set(payload) != {"questions"}:
        raise ValueError("holdout question register must contain only a questions array")
    return tuple(
        R2HoldoutQuestion.from_json(_object_dict(item, "holdout question"))
        for item in _object_list(raw, "holdout questions")
    )


def _forecast_dataset_from_payload(payload: Mapping[str, object]):
    from qtrad.domain.r2_holdout import R2HoldoutForecastDataset, R2HoldoutForecastRow
    from qtrad.domain.r2_readiness import ModelFamily

    rows = _object_list(payload["rows"], "holdout forecast rows")
    expected = _object_list(payload["expected_opportunity_ids"], "forecast opportunities")
    parsed_rows = []
    for raw_item in rows:
        item = _object_dict(raw_item, "holdout forecast row")
        parsed_rows.append(
            R2HoldoutForecastRow(
                configuration_id=str(item["configuration_id"]),
                target_id=str(item["target_id"]),
                feature_row_id=str(item["feature_row_id"]),
                forecast=_float_value(item["forecast"], "forecast"),
                model_family=ModelFamily(str(item["model_family"])),
                row_id=str(item["row_id"]),
            )
        )
    return R2HoldoutForecastDataset(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        feature_dataset_id=str(payload["feature_dataset_id"]),
        configuration_id=str(payload["configuration_id"]),
        final_fit_id=str(payload["final_fit_id"]),
        rows=tuple(parsed_rows),
        expected_opportunity_ids=tuple(str(item) for item in expected),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        holdout_outcomes_accessed=bool(payload["holdout_outcomes_accessed"]),
        dataset_id=str(payload["dataset_id"]),
    )


def _coverage_dataset_from_payload(payload: Mapping[str, object]):
    from qtrad.domain.r2_holdout import (
        HoldoutOpportunityDisposition,
        R2HoldoutCoverageDataset,
        R2HoldoutCoverageRow,
    )

    rows = _object_list(payload["rows"], "holdout coverage rows")
    expected = _object_list(payload["expected_opportunity_ids"], "coverage opportunities")
    parsed_rows = []
    for raw_item in rows:
        item = _object_dict(raw_item, "holdout coverage row")
        forecast_row_id = item["forecast_row_id"]
        parsed_rows.append(
            R2HoldoutCoverageRow(
                configuration_id=str(item["configuration_id"]),
                opportunity_id=str(item["opportunity_id"]),
                disposition=HoldoutOpportunityDisposition(str(item["disposition"])),
                forecast_row_id=None if forecast_row_id is None else str(forecast_row_id),
                reason=str(item["reason"]),
            )
        )
    return R2HoldoutCoverageDataset(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        feature_dataset_id=str(payload["feature_dataset_id"]),
        configuration_id=str(payload["configuration_id"]),
        expected_opportunity_ids=tuple(str(item) for item in expected),
        rows=tuple(parsed_rows),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        holdout_outcomes_accessed=bool(payload["holdout_outcomes_accessed"]),
        coverage_id=str(payload["coverage_id"]),
    )


def reveal_holdout_from_files(
    root: Path,
    *,
    outcomes_path: Path,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    acknowledgement: str,
    opened_by: str,
    consumed_by: str,
    opened_at: datetime,
    consumed_at: datetime,
):
    """Reveal persisted disposable children; outcome bytes are read after OPENED."""
    from qtrad.application.r2_holdout import evaluate_holdout

    selection = verify_holdout_selection(root / "selection.json")
    seal = verify_holdout_preparation(root)
    forecast_datasets = tuple(
        _forecast_dataset_from_payload(_load_object(root / "forecasts" / f"{dataset_id}.json"))
        for dataset_id in seal.forecast_dataset_ids
    )
    coverage_datasets = tuple(
        _coverage_dataset_from_payload(_load_object(root / "coverage" / f"{coverage_id}.json"))
        for coverage_id in seal.coverage_ids
    )

    def load_outcomes() -> Mapping[str, float]:
        payload = _load_object(outcomes_path)
        if set(payload) != {"outcomes"}:
            raise ValueError("holdout outcomes file must contain only an outcomes object")
        raw = _object_dict(payload["outcomes"], "holdout outcomes")
        return {str(key): _float_value(value, "holdout outcome") for key, value in raw.items()}

    def evaluate(
        outcomes: Mapping[str, float], opened: R2HoldoutOpenedMarker
    ) -> R2HoldoutEvaluation:
        return evaluate_holdout(
            selection=selection,
            seal=seal,
            opened_marker=opened,
            forecast_datasets=forecast_datasets,
            coverage_datasets=coverage_datasets,
            outcomes=outcomes,
        )

    return reveal_holdout(
        root,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
        acknowledgement=acknowledgement,
        opened_by=opened_by,
        consumed_by=consumed_by,
        opened_at=opened_at,
        consumed_at=consumed_at,
        outcome_loader=load_outcomes,
        evaluator=evaluate,
    )
