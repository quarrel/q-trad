"""Strict serialization and independent replay for R2.C preprocessing selection."""

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from math import isclose, isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from qtrad.application.r2_preprocessing import build_r2_preprocessing_selection
from qtrad.application.r2_readiness import R1FoundationBindings
from qtrad.domain.r2_features import R2FeatureDataset
from qtrad.domain.r2_models import (
    R2_PREPROCESSING_SCHEMA_CONTRACT,
    R2_PREPROCESSING_SELECTION_CONTRACT,
    AlphaCandidateScore,
    AlphaSelection,
    FitDisposition,
    PreprocessingFeatureDefinition,
    PreprocessingFeatureKind,
    PreprocessingFit,
    R2PreprocessingSchema,
    R2PreprocessingSelection,
)
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily, R2ExperimentConfig
from qtrad.domain.time import require_utc

_TOP_LEVEL_KEYS = {
    "contract",
    "schema_version",
    "r2_feature_dataset_id",
    "target_dataset_id",
    "fold_dataset_id",
    "experiment_configuration_id",
    "model_family",
    "horizon_seconds",
    "outer_fold_id",
    "outer_fold_membership_hash",
    "target_instruments",
    "inner_validation_start",
    "inner_validation_end",
    "purge_boundary",
    "feature_schema_id",
    "feature_set_id",
    "preprocessing_schema_id",
    "preprocessing_schema",
    "evidence_class",
    "application_image_identity",
    "sklearn_library_identity",
    "preprocessing_policy",
    "inner_validation_policy",
    "alpha_grid",
    "ridge_solver",
    "ridge_tolerance",
    "ridge_max_iterations",
    "loss_policy",
    "pooled_weighting_policy",
    "holdout_excluded",
    "selection",
    "artifact_id",
}
_PREPROCESSING_SCHEMA_KEYS = {
    "contract",
    "schema_version",
    "features",
    "preprocessing_schema_id",
}
_PREPROCESSING_FEATURE_KEYS = {"name", "kind"}
_PREPROCESSING_KEYS = {
    "feature_names",
    "indicator_feature_names",
    "medians",
    "means",
    "scales",
    "active_feature_names",
    "unscaled_feature_names",
    "dropped_all_null_feature_names",
    "dropped_zero_variance_feature_names",
    "training_target_ids",
    "sample_weights",
}
_CANDIDATE_KEYS = {
    "alpha",
    "disposition",
    "loss",
    "failure",
    "inner_fit_target_ids",
    "inner_validation_target_ids",
}
_SELECTION_KEYS = {
    "disposition",
    "outer_training_target_ids",
    "inner_fit_target_ids",
    "inner_validation_target_ids",
    "purged_target_ids",
    "inner_preprocessing",
    "candidate_scores",
    "selected_alpha",
    "outer_preprocessing",
}


def serialize_r2_preprocessing_selection(selection: R2PreprocessingSelection) -> bytes:
    """Return the one canonical UTF-8 representation."""
    payload = json.dumps(selection.as_json(), sort_keys=True, separators=(",", ":"))
    return (payload + "\n").encode("utf-8")


def decode_r2_preprocessing_selection(
    payload: bytes | str | Mapping[str, object],
) -> R2PreprocessingSelection:
    """Strictly decode and reconstruct the domain object, independently checking its digest."""
    raw: object
    if isinstance(payload, bytes):
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("preprocessing-selection payload is not valid UTF-8 JSON") from error
    elif isinstance(payload, str):
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("preprocessing-selection payload is not valid JSON") from error
    elif isinstance(payload, Mapping):
        raw = dict(payload)
    else:
        raise TypeError("preprocessing-selection payload must be bytes, text or a mapping")
    obj = _exact_object(raw, _TOP_LEVEL_KEYS, "preprocessing selection")
    if _text(obj["contract"], "contract") != R2_PREPROCESSING_SELECTION_CONTRACT:
        raise ValueError("unsupported preprocessing-selection contract")
    if _integer(obj["schema_version"], "schema_version") != 1:
        raise ValueError("unsupported preprocessing-selection schema version")
    selection = _selection(obj["selection"])
    preprocessing_schema = _preprocessing_schema(obj["preprocessing_schema"])
    return R2PreprocessingSelection(
        r2_feature_dataset_id=_text(obj["r2_feature_dataset_id"], "r2_feature_dataset_id"),
        target_dataset_id=_text(obj["target_dataset_id"], "target_dataset_id"),
        fold_dataset_id=_text(obj["fold_dataset_id"], "fold_dataset_id"),
        experiment_configuration_id=_text(
            obj["experiment_configuration_id"], "experiment_configuration_id"
        ),
        model_family=ModelFamily(_text(obj["model_family"], "model_family")),
        horizon=timedelta(seconds=_number(obj["horizon_seconds"], "horizon_seconds")),
        outer_fold_id=_text(obj["outer_fold_id"], "outer_fold_id"),
        outer_fold_membership_hash=_text(
            obj["outer_fold_membership_hash"], "outer_fold_membership_hash"
        ),
        target_instruments=_texts(obj["target_instruments"], "target_instruments"),
        inner_validation_start=_timestamp(obj["inner_validation_start"], "inner_validation_start"),
        inner_validation_end=_timestamp(obj["inner_validation_end"], "inner_validation_end"),
        purge_boundary=_timestamp(obj["purge_boundary"], "purge_boundary"),
        feature_schema_id=_text(obj["feature_schema_id"], "feature_schema_id"),
        feature_set_id=_text(obj["feature_set_id"], "feature_set_id"),
        preprocessing_schema_id=_text(obj["preprocessing_schema_id"], "preprocessing_schema_id"),
        preprocessing_schema=preprocessing_schema,
        evidence_class=EvidenceClass(_text(obj["evidence_class"], "evidence_class")),
        application_image_identity=_text(
            obj["application_image_identity"], "application_image_identity"
        ),
        sklearn_library_identity=_text(obj["sklearn_library_identity"], "sklearn_library_identity"),
        preprocessing_policy=_text(obj["preprocessing_policy"], "preprocessing_policy"),
        inner_validation_policy=_text(obj["inner_validation_policy"], "inner_validation_policy"),
        alpha_grid=_numbers(obj["alpha_grid"], "alpha_grid"),
        ridge_solver=_text(obj["ridge_solver"], "ridge_solver"),
        ridge_tolerance=_number(obj["ridge_tolerance"], "ridge_tolerance"),
        ridge_max_iterations=_integer(obj["ridge_max_iterations"], "ridge_max_iterations"),
        loss_policy=_text(obj["loss_policy"], "loss_policy"),
        pooled_weighting_policy=_text(obj["pooled_weighting_policy"], "pooled_weighting_policy"),
        holdout_excluded=_boolean(obj["holdout_excluded"], "holdout_excluded"),
        selection=selection,
        artifact_id=_text(obj["artifact_id"], "artifact_id"),
    )


def verify_r2_preprocessing_selection(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    *,
    model_family: ModelFamily,
    horizon: timedelta,
    outer_fold_id: str,
    target_instruments: Sequence[str] | None,
    application_image_identity: str,
    sklearn_library_identity: str,
    persisted_payload: bytes | str | Mapping[str, object],
) -> R2PreprocessingSelection:
    """Decode persisted evidence and compare it with a fresh authenticated rebuild."""
    persisted = decode_r2_preprocessing_selection(persisted_payload)
    rebuilt = build_r2_preprocessing_selection(
        verified,
        feature_dataset,
        experiment,
        model_family=model_family,
        horizon=horizon,
        outer_fold_id=outer_fold_id,
        target_instruments=target_instruments,
        application_image_identity=application_image_identity,
        sklearn_library_identity=sklearn_library_identity,
    )
    _verify_replay_match(
        persisted,
        rebuilt,
        relative_tolerance=experiment.numeric_replay_relative_tolerance,
        absolute_tolerance=experiment.numeric_replay_absolute_tolerance,
    )
    return persisted


def _verify_replay_match(
    persisted: R2PreprocessingSelection,
    rebuilt: R2PreprocessingSelection,
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> None:
    if _structural_replay_json(persisted) != _structural_replay_json(rebuilt):
        raise ValueError(
            "persisted preprocessing selection does not match the authenticated rebuild"
        )
    for actual, expected in (
        (persisted.selection.inner_preprocessing, rebuilt.selection.inner_preprocessing),
        (persisted.selection.outer_preprocessing, rebuilt.selection.outer_preprocessing),
    ):
        if actual is None or expected is None:
            continue
        for actual_values, expected_values in (
            (actual.medians, expected.medians),
            (actual.means, expected.means),
            (actual.scales, expected.scales),
            (actual.sample_weights, expected.sample_weights),
        ):
            if not _numeric_vectors_close(
                actual_values,
                expected_values,
                relative_tolerance=relative_tolerance,
                absolute_tolerance=absolute_tolerance,
            ):
                raise ValueError(
                    "persisted preprocessing selection does not match the authenticated rebuild"
                )
    for actual, expected in zip(
        persisted.selection.candidate_scores,
        rebuilt.selection.candidate_scores,
        strict=True,
    ):
        if not _optional_number_close(
            actual.loss,
            expected.loss,
            relative_tolerance=relative_tolerance,
            absolute_tolerance=absolute_tolerance,
        ):
            raise ValueError(
                "persisted preprocessing selection does not match the authenticated rebuild"
            )


def _structural_replay_json(selection: R2PreprocessingSelection) -> dict[str, object]:
    payload = cast(dict[str, object], cast(object, selection.as_json()))
    payload.pop("artifact_id")
    nested = cast(dict[str, object], payload["selection"])
    for field in ("inner_preprocessing", "outer_preprocessing"):
        value = nested[field]
        if value is None:
            continue
        fit = cast(dict[str, object], value)
        for vector_field in ("medians", "means", "scales", "sample_weights"):
            vector = cast(list[object], fit[vector_field])
            fit[vector_field] = [None if item is None else 0.0 for item in vector]
    for value in cast(list[object], nested["candidate_scores"]):
        candidate = cast(dict[str, object], value)
        if candidate["loss"] is not None:
            candidate["loss"] = 0.0
    return payload


def _numeric_vectors_close(
    actual: Sequence[float | None],
    expected: Sequence[float | None],
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    return len(actual) == len(expected) and all(
        _optional_number_close(
            actual_value,
            expected_value,
            relative_tolerance=relative_tolerance,
            absolute_tolerance=absolute_tolerance,
        )
        for actual_value, expected_value in zip(actual, expected, strict=True)
    )


def _optional_number_close(
    actual: float | None,
    expected: float | None,
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return isclose(
        actual,
        expected,
        rel_tol=relative_tolerance,
        abs_tol=absolute_tolerance,
    )


def write_r2_preprocessing_selection(path: Path, selection: R2PreprocessingSelection) -> None:
    """Publish immutably; accept an existing path only when it verifies as identical."""
    encoded = serialize_r2_preprocessing_selection(selection)
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
            existing = decode_r2_preprocessing_selection(path.read_bytes())
            if existing != selection:
                raise FileExistsError(
                    "existing preprocessing-selection artifact has conflicting semantic content"
                ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _preprocessing_schema(value: object) -> R2PreprocessingSchema:
    obj = _exact_object(value, _PREPROCESSING_SCHEMA_KEYS, "preprocessing_schema")
    if _text(obj["contract"], "preprocessing_schema.contract") != R2_PREPROCESSING_SCHEMA_CONTRACT:
        raise ValueError("unsupported preprocessing-schema contract")
    if _integer(obj["schema_version"], "preprocessing_schema.schema_version") != 1:
        raise ValueError("unsupported preprocessing-schema version")
    features = tuple(
        _preprocessing_feature(item)
        for item in _list(obj["features"], "preprocessing_schema.features")
    )
    return R2PreprocessingSchema(
        features,
        _text(obj["preprocessing_schema_id"], "preprocessing_schema.preprocessing_schema_id"),
    )


def _preprocessing_feature(value: object) -> PreprocessingFeatureDefinition:
    obj = _exact_object(value, _PREPROCESSING_FEATURE_KEYS, "preprocessing feature")
    return PreprocessingFeatureDefinition(
        _text(obj["name"], "preprocessing feature name"),
        PreprocessingFeatureKind(_text(obj["kind"], "preprocessing feature kind")),
    )


def _selection(value: object) -> AlphaSelection:
    obj = _exact_object(value, _SELECTION_KEYS, "selection")
    inner = _optional_preprocessing(obj["inner_preprocessing"], "inner_preprocessing")
    outer = _optional_preprocessing(obj["outer_preprocessing"], "outer_preprocessing")
    raw_scores = _list(obj["candidate_scores"], "candidate_scores")
    return AlphaSelection(
        FitDisposition(_text(obj["disposition"], "selection.disposition")),
        _texts(obj["outer_training_target_ids"], "outer_training_target_ids"),
        _texts(obj["inner_fit_target_ids"], "inner_fit_target_ids"),
        _texts(obj["inner_validation_target_ids"], "inner_validation_target_ids"),
        _texts(obj["purged_target_ids"], "purged_target_ids"),
        inner,
        tuple(_candidate(item) for item in raw_scores),
        _optional_number(obj["selected_alpha"], "selected_alpha"),
        outer,
    )


def _candidate(value: object) -> AlphaCandidateScore:
    obj = _exact_object(value, _CANDIDATE_KEYS, "candidate score")
    return AlphaCandidateScore(
        _number(obj["alpha"], "candidate.alpha"),
        FitDisposition(_text(obj["disposition"], "candidate.disposition")),
        _optional_number(obj["loss"], "candidate.loss"),
        _optional_text(obj["failure"], "candidate.failure"),
        _texts(obj["inner_fit_target_ids"], "candidate.inner_fit_target_ids"),
        _texts(obj["inner_validation_target_ids"], "candidate.inner_validation_target_ids"),
    )


def _optional_preprocessing(value: object, field: str) -> PreprocessingFit | None:
    if value is None:
        return None
    obj = _exact_object(value, _PREPROCESSING_KEYS, field)
    return PreprocessingFit(
        _texts(obj["feature_names"], f"{field}.feature_names"),
        _texts(obj["indicator_feature_names"], f"{field}.indicator_feature_names"),
        _optional_numbers(obj["medians"], f"{field}.medians"),
        _optional_numbers(obj["means"], f"{field}.means"),
        _optional_numbers(obj["scales"], f"{field}.scales"),
        _texts(obj["active_feature_names"], f"{field}.active_feature_names"),
        _texts(obj["unscaled_feature_names"], f"{field}.unscaled_feature_names"),
        _texts(
            obj["dropped_all_null_feature_names"],
            f"{field}.dropped_all_null_feature_names",
        ),
        _texts(
            obj["dropped_zero_variance_feature_names"],
            f"{field}.dropped_zero_variance_feature_names",
        ),
        _texts(obj["training_target_ids"], f"{field}.training_target_ids"),
        _numbers(obj["sample_weights"], f"{field}.sample_weights"),
    )


def _exact_object(value: object, keys: set[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field} must be a JSON object")
    if set(value) != keys:
        raise ValueError(f"{field} has unknown or missing fields")
    return cast(dict[str, object], value)


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a JSON array")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _texts(value: object, field: str) -> tuple[str, ...]:
    return tuple(_text(item, field) for item in _list(value, field))


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _optional_number(value: object, field: str) -> float | None:
    return None if value is None else _number(value, field)


def _numbers(value: object, field: str) -> tuple[float, ...]:
    return tuple(_number(item, field) for item in _list(value, field))


def _optional_numbers(value: object, field: str) -> tuple[float | None, ...]:
    return tuple(_optional_number(item, field) for item in _list(value, field))


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _timestamp(value: object, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    require_utc(parsed, field)
    return parsed
