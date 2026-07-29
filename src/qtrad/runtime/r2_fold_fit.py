"""Strict persistence and authenticated numerical replay for R2.D fold fits."""

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from math import isclose
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from qtrad.application.r2_baselines import build_local_ridge_fold
from qtrad.application.r2_readiness import R1FoundationBindings
from qtrad.domain.r2_baselines import (
    R2_FOLD_FIT_CONTRACT,
    FoldFitDiagnostics,
    R2FoldFit,
)
from qtrad.domain.r2_features import R2FeatureDataset
from qtrad.domain.r2_models import FitDisposition, R2PreprocessingSelection
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily, R2ExperimentConfig
from qtrad.domain.time import require_utc
from qtrad.runtime.r2_preprocessing_selection import decode_preprocessing_fit

_FIT_KEYS = {
    "contract",
    "schema_version",
    "r2_feature_dataset_id",
    "target_dataset_id",
    "fold_dataset_id",
    "experiment_configuration_id",
    "preprocessing_selection_id",
    "model_family",
    "horizon_seconds",
    "outer_fold_id",
    "outer_fold_membership_hash",
    "target_instrument_id",
    "feature_set_id",
    "feature_schema_id",
    "preprocessing_schema_id",
    "evidence_class",
    "application_image_identity",
    "sklearn_library_identity",
    "training_cutoff",
    "selected_alpha",
    "preprocessing",
    "coefficient_feature_names",
    "intercept",
    "coefficients",
    "fit_row_count",
    "excluded_row_count",
    "outer_validation_opportunity_count",
    "fit_warnings",
    "disposition",
    "failure",
    "diagnostics",
    "artifact_id",
}
_DIAGNOSTIC_KEYS = {
    "iteration_count",
    "training_target_mean",
    "training_target_standard_deviation",
    "training_prediction_mse",
    "coefficient_l2_norm",
    "maximum_absolute_coefficient",
    "prediction_replay_maximum_absolute_error",
}


def serialize_r2_fold_fit(fit: R2FoldFit) -> bytes:
    payload = json.dumps(fit.as_json(), sort_keys=True, separators=(",", ":"))
    return (payload + "\n").encode("utf-8")


def decode_r2_fold_fit(payload: bytes | str | Mapping[str, object]) -> R2FoldFit:
    raw: object
    if isinstance(payload, bytes):
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("fold-fit payload is not valid UTF-8 JSON") from error
    elif isinstance(payload, str):
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("fold-fit payload is not valid JSON") from error
    elif isinstance(payload, Mapping):
        raw = dict(payload)
    else:
        raise TypeError("fold-fit payload must be bytes, text or a mapping")
    obj = _exact_object(raw, _FIT_KEYS, "fold fit")
    if _text(obj["contract"], "contract") != R2_FOLD_FIT_CONTRACT:
        raise ValueError("unsupported fold-fit contract")
    if _integer(obj["schema_version"], "schema_version") != 1:
        raise ValueError("unsupported fold-fit schema version")
    return R2FoldFit(
        r2_feature_dataset_id=_text(obj["r2_feature_dataset_id"], "r2_feature_dataset_id"),
        target_dataset_id=_text(obj["target_dataset_id"], "target_dataset_id"),
        fold_dataset_id=_text(obj["fold_dataset_id"], "fold_dataset_id"),
        experiment_configuration_id=_text(
            obj["experiment_configuration_id"], "experiment_configuration_id"
        ),
        preprocessing_selection_id=_text(
            obj["preprocessing_selection_id"], "preprocessing_selection_id"
        ),
        model_family=ModelFamily(_text(obj["model_family"], "model_family")),
        horizon=timedelta(seconds=_number(obj["horizon_seconds"], "horizon_seconds")),
        outer_fold_id=_text(obj["outer_fold_id"], "outer_fold_id"),
        outer_fold_membership_hash=_text(
            obj["outer_fold_membership_hash"], "outer_fold_membership_hash"
        ),
        target_instrument_id=_text(obj["target_instrument_id"], "target_instrument_id"),
        feature_set_id=_text(obj["feature_set_id"], "feature_set_id"),
        feature_schema_id=_text(obj["feature_schema_id"], "feature_schema_id"),
        preprocessing_schema_id=_text(obj["preprocessing_schema_id"], "preprocessing_schema_id"),
        evidence_class=EvidenceClass(_text(obj["evidence_class"], "evidence_class")),
        application_image_identity=_text(
            obj["application_image_identity"], "application_image_identity"
        ),
        sklearn_library_identity=_text(obj["sklearn_library_identity"], "sklearn_library_identity"),
        training_cutoff=_timestamp(obj["training_cutoff"], "training_cutoff"),
        selected_alpha=_optional_number(obj["selected_alpha"], "selected_alpha"),
        preprocessing=decode_preprocessing_fit(obj["preprocessing"]),
        coefficient_feature_names=_texts(
            obj["coefficient_feature_names"], "coefficient_feature_names"
        ),
        intercept=_optional_number(obj["intercept"], "intercept"),
        coefficients=_numbers(obj["coefficients"], "coefficients"),
        fit_row_count=_integer(obj["fit_row_count"], "fit_row_count"),
        excluded_row_count=_integer(obj["excluded_row_count"], "excluded_row_count"),
        outer_validation_opportunity_count=_integer(
            obj["outer_validation_opportunity_count"], "outer_validation_opportunity_count"
        ),
        fit_warnings=_texts(obj["fit_warnings"], "fit_warnings"),
        disposition=FitDisposition(_text(obj["disposition"], "disposition")),
        failure=_optional_text(obj["failure"], "failure"),
        diagnostics=_diagnostics(obj["diagnostics"]),
        artifact_id=_text(obj["artifact_id"], "artifact_id"),
    )


def verify_r2_fold_fit(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    selection: R2PreprocessingSelection,
    persisted_payload: bytes | str | Mapping[str, object],
) -> R2FoldFit:
    """Rebuild the final fit and compare all structural and numerical evidence."""

    persisted = decode_r2_fold_fit(persisted_payload)
    rebuilt = build_local_ridge_fold(verified, feature_dataset, experiment, selection).fit
    if _structural_json(persisted) != _structural_json(rebuilt):
        raise ValueError("persisted fold fit does not match the authenticated rebuild")
    if not _optional_close(
        persisted.intercept,
        rebuilt.intercept,
        relative_tolerance=experiment.numeric_replay_relative_tolerance,
        absolute_tolerance=experiment.numeric_replay_absolute_tolerance,
    ) or not _vectors_close(
        persisted.coefficients,
        rebuilt.coefficients,
        relative_tolerance=experiment.numeric_replay_relative_tolerance,
        absolute_tolerance=experiment.numeric_replay_absolute_tolerance,
    ):
        raise ValueError("persisted fold fit differs numerically from the authenticated rebuild")
    for actual, expected in ((persisted.preprocessing, rebuilt.preprocessing),):
        if actual is None or expected is None:
            if actual is not expected:
                raise ValueError("persisted fold fit preprocessing availability differs")
            continue
        for actual_values, expected_values in (
            (actual.medians, expected.medians),
            (actual.means, expected.means),
            (actual.scales, expected.scales),
            (actual.sample_weights, expected.sample_weights),
        ):
            if not _optional_vectors_close(
                actual_values,
                expected_values,
                relative_tolerance=experiment.numeric_replay_relative_tolerance,
                absolute_tolerance=experiment.numeric_replay_absolute_tolerance,
            ):
                raise ValueError("persisted fold fit preprocessing differs numerically")
    if not _diagnostics_close(persisted.diagnostics, rebuilt.diagnostics, experiment):
        raise ValueError("persisted fold-fit diagnostics differ from the authenticated rebuild")
    return persisted


def write_r2_fold_fit(path: Path, fit: R2FoldFit) -> None:
    """Publish immutably, accepting an existing path only when semantically identical."""

    encoded = serialize_r2_fold_fit(fit)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            if decode_r2_fold_fit(path.read_bytes()) != fit:
                raise FileExistsError(
                    "existing fold-fit artifact has conflicting content"
                ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _structural_json(fit: R2FoldFit) -> dict[str, object]:
    payload = cast(dict[str, object], cast(object, fit.as_json()))
    payload["artifact_id"] = ""
    payload["intercept"] = None if fit.intercept is None else 0.0
    payload["coefficients"] = [0.0 for _ in fit.coefficients]
    preprocessing = payload["preprocessing"]
    if isinstance(preprocessing, dict):
        mutable_preprocessing = cast(dict[str, object], preprocessing)
        for field in ("medians", "means", "scales", "sample_weights"):
            mutable_preprocessing[field] = [
                None if value is None else 0.0
                for value in cast(list[object], mutable_preprocessing[field])
            ]
    diagnostics = payload["diagnostics"]
    if isinstance(diagnostics, dict):
        mutable_diagnostics = cast(dict[str, object], diagnostics)
        for field in _DIAGNOSTIC_KEYS - {"iteration_count"}:
            mutable_diagnostics[field] = 0.0
    return payload


def _diagnostics(value: object) -> FoldFitDiagnostics | None:
    if value is None:
        return None
    obj = _exact_object(value, _DIAGNOSTIC_KEYS, "diagnostics")
    return FoldFitDiagnostics(
        iteration_count=_optional_integer(obj["iteration_count"], "iteration_count"),
        training_target_mean=_number(obj["training_target_mean"], "training_target_mean"),
        training_target_standard_deviation=_number(
            obj["training_target_standard_deviation"], "training_target_standard_deviation"
        ),
        training_prediction_mse=_number(obj["training_prediction_mse"], "training_prediction_mse"),
        coefficient_l2_norm=_number(obj["coefficient_l2_norm"], "coefficient_l2_norm"),
        maximum_absolute_coefficient=_number(
            obj["maximum_absolute_coefficient"], "maximum_absolute_coefficient"
        ),
        prediction_replay_maximum_absolute_error=_number(
            obj["prediction_replay_maximum_absolute_error"],
            "prediction_replay_maximum_absolute_error",
        ),
    )


def _diagnostics_close(
    actual: FoldFitDiagnostics | None,
    expected: FoldFitDiagnostics | None,
    experiment: R2ExperimentConfig,
) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return actual.iteration_count == expected.iteration_count and _vectors_close(
        (
            actual.training_target_mean,
            actual.training_target_standard_deviation,
            actual.training_prediction_mse,
            actual.coefficient_l2_norm,
            actual.maximum_absolute_coefficient,
            actual.prediction_replay_maximum_absolute_error,
        ),
        (
            expected.training_target_mean,
            expected.training_target_standard_deviation,
            expected.training_prediction_mse,
            expected.coefficient_l2_norm,
            expected.maximum_absolute_coefficient,
            expected.prediction_replay_maximum_absolute_error,
        ),
        relative_tolerance=experiment.numeric_replay_relative_tolerance,
        absolute_tolerance=experiment.numeric_replay_absolute_tolerance,
    )


def _vectors_close(
    actual: Sequence[float],
    expected: Sequence[float],
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    return len(actual) == len(expected) and all(
        isclose(left, right, rel_tol=relative_tolerance, abs_tol=absolute_tolerance)
        for left, right in zip(actual, expected, strict=True)
    )


def _optional_vectors_close(
    actual: Sequence[float | None],
    expected: Sequence[float | None],
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    return len(actual) == len(expected) and all(
        _optional_close(
            left,
            right,
            relative_tolerance=relative_tolerance,
            absolute_tolerance=absolute_tolerance,
        )
        for left, right in zip(actual, expected, strict=True)
    )


def _optional_close(
    actual: float | None,
    expected: float | None,
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return isclose(actual, expected, rel_tol=relative_tolerance, abs_tol=absolute_tolerance)


def _exact_object(value: object, keys: set[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field} must be a JSON object")
    if set(value) != keys:
        raise ValueError(f"{field} has unknown or missing fields")
    return cast(dict[str, object], value)


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be non-empty text")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    return float(value)


def _optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _number(value, field)


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _optional_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


def _texts(value: object, field: str) -> tuple[str, ...]:
    return tuple(_text(item, field) for item in _list(value, field))


def _numbers(value: object, field: str) -> tuple[float, ...]:
    return tuple(_number(item, field) for item in _list(value, field))


def _timestamp(value: object, field: str) -> datetime:
    text = _text(value, field)
    try:
        result = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    require_utc(result, field)
    return result
