"""LAB-S statistical Ridge experiments over authenticated LAB-0 rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import traceback
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.lab_0.baseline import (
    _fit_preprocessing,
    _transform,
    _weights,
)
from experiments.r2_historical_lab.lab_0.features import MARKET_GROUPS
from experiments.r2_historical_lab.lab_0.harness import (
    TERMINAL_BLOCK,
    append_attempt,
    authenticate_manifest,
    configuration_id,
    evaluate_against_zero,
    freeze_finalists,
    load_parts,
)

WORKSTREAM = "LAB-S"
DEVELOPMENT_BLOCK_NAMES = ("DEV_1", "DEV_2", "DEV_3")
FEATURE_NAMES = (
    "return_60s",
    "return_60s_available",
    "return_300s",
    "return_300s_available",
    "return_contrast_60s_300s",
    "realised_std_60s",
    "mean_absolute_return_60s",
    "mean_log_range_60s",
    "return_sign_balance_60s",
    "available_interval_count_60s",
    "window_coverage_60s",
    "realised_std_300s",
    "mean_absolute_return_300s",
    "mean_log_range_300s",
    "return_sign_balance_300s",
    "available_interval_count_300s",
    "window_coverage_300s",
    "utc_minute_sin",
    "utc_minute_cos",
    "utc_day_sin",
    "utc_day_cos",
    "source_active",
    "target_feature_missing_fraction",
    "cross_market_available_count",
    "quality_healthy",
    "gap_known_by_cutoff",
)
INDICATOR_NAMES = {
    "return_60s_available",
    "return_300s_available",
    "source_active",
    "quality_healthy",
    "gap_known_by_cutoff",
}
KEYS = ("instrument_id", "decision_time", "horizon_minutes")
RESULT_FIELDS = (
    "support",
    "forecast_coverage",
    "zero_return_instrument_balanced_mse",
    "model_instrument_balanced_mse",
    "direct_delta_mse_versus_zero",
    "skill_versus_zero",
    "positive_chronological_block_count",
    "chronological_block_count",
    "positive_instrument_count",
    "instrument_count",
    "calibration_slope",
    "spearman_correlation",
    "best_instrument_contribution",
    "best_period_contribution",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _read_configuration(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("LAB-S configuration must be a JSON object")
    expected = {
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "horizon_minutes": 15,
        "feature_set": "P0",
        "ridge_alpha": 1.0,
        "rolling_history_days": 84,
        "decay_half_life_days": 42,
        "calibration_inner_days": 14,
        "calibration_stability_max_ratio": 3.0,
        "finalist_limit": 4,
    }
    if {key: value[key] for key in expected} != expected:
        raise ValueError("LAB-S configuration crosses its fixed experimental design")
    if tuple(value["feature_names"]) != FEATURE_NAMES:
        raise ValueError("LAB-S configuration does not bind the original P0 feature order")
    ratios = tuple(float(item) for item in value["hierarchical_instrument_penalty_ratios"])
    if ratios != (0.25, 1.0, 4.0):
        raise ValueError("LAB-S hierarchical penalty ladder changed")
    return value


def _block_specs(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["name"]): item for item in manifest["fold_blocks"]}


def _load_rows(
    manifest_path: Path,
    manifest_sha256: str,
    instruments: Sequence[str],
    blocks: Sequence[str],
    *,
    finalist_freeze: Path | None = None,
    finalist_freeze_sha256: str | None = None,
    configuration_identifier: str | None = None,
) -> pl.DataFrame:
    load_kwargs: dict[str, Any] = {}
    if TERMINAL_BLOCK in blocks:
        load_kwargs = {
            "finalist_freeze": finalist_freeze,
            "expected_finalist_freeze_sha256": finalist_freeze_sha256,
            "configuration_id": configuration_identifier,
        }
    features = load_parts(
        manifest_path,
        manifest_sha256,
        kind="feature",
        instruments=instruments,
        blocks=blocks,
        **load_kwargs,
    ).select("instrument_id", "decision_time", *FEATURE_NAMES)
    targets = load_parts(
        manifest_path,
        manifest_sha256,
        kind="target",
        instruments=instruments,
        horizons=(15,),
        blocks=blocks,
        **load_kwargs,
    ).select(
        "instrument_id",
        "decision_time",
        "horizon_minutes",
        "target_return",
        "target_valid",
        "target_available_at",
        "block",
    )
    return (
        targets.join(features, on=["instrument_id", "decision_time"], how="inner")
        .filter(pl.col("target_valid"))
        .collect()
        .sort("decision_time", "instrument_id")
    )


def _market_group(instrument: str) -> str:
    return MARKET_GROUPS[instrument.split(":", maxsplit=1)[0]]


def _select_training(
    training: pl.DataFrame,
    fit_time: datetime,
    configuration: dict[str, Any],
) -> pl.DataFrame:
    policy = configuration["recency"]
    if policy == "EXPANDING":
        selected = training
    elif policy == "ROLLING":
        selected = training.filter(
            pl.col("decision_time")
            >= fit_time - timedelta(days=int(configuration["rolling_history_days"]))
        )
    elif policy == "EXPONENTIAL_DECAY":
        selected = training
    else:
        raise ValueError(f"unsupported recency policy: {policy}")
    if selected.is_empty():
        raise ValueError(f"recency policy {policy} left no training rows")
    return selected


def _sample_weights(
    training: pl.DataFrame,
    fit_time: datetime,
    configuration: dict[str, Any],
    *,
    pooled: bool,
) -> np.ndarray:
    weights = _weights(training, pooled=pooled)
    if configuration["recency"] == "EXPONENTIAL_DECAY":
        half_life = float(configuration["decay_half_life_days"])
        ages = np.asarray(
            [
                max(0.0, (fit_time - value).total_seconds() / 86_400)
                for value in training["decision_time"].to_list()
            ],
            dtype=float,
        )
        weights *= np.exp2(-ages / half_life)
    return weights


def _target_scales(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    policy: str,
) -> tuple[np.ndarray, np.ndarray]:
    if policy == "RAW_RETURN":
        return np.ones(training.height), np.ones(validation.height)
    if policy != "CAUSAL_VOL_STANDARDISED":
        raise ValueError(f"unsupported target scale policy: {policy}")
    train_vol = training["realised_std_300s"].to_numpy().astype(float) * math.sqrt(15.0)
    valid_train = np.isfinite(train_vol) & (train_vol > 0)
    if not valid_train.any():
        raise ValueError("causal volatility target scale has no positive training values")
    median = float(np.median(train_vol[valid_train]))
    floor = float(np.quantile(train_vol[valid_train], 0.1))

    def clean(frame: pl.DataFrame) -> np.ndarray:
        values = frame["realised_std_300s"].to_numpy().astype(float) * math.sqrt(15.0)
        values = np.where(np.isfinite(values) & (values > 0), values, median)
        return np.maximum(values, floor)

    return clean(training), clean(validation)


def _identity_matrix(frame: pl.DataFrame, order: Sequence[str]) -> np.ndarray:
    positions = {value: index for index, value in enumerate(order)}
    matrix = np.zeros((frame.height, len(order)), dtype=float)
    for row_index, instrument in enumerate(frame["instrument_id"]):
        matrix[row_index, positions[str(instrument)]] = 1.0
    return matrix


def _ridge_prediction(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    configuration: dict[str, Any],
    fit_time: datetime,
    *,
    pooled: bool,
) -> tuple[np.ndarray, dict[str, object]]:
    selected = _select_training(training, fit_time, configuration)
    weights = _sample_weights(selected, fit_time, configuration, pooled=pooled)
    train_matrix = selected.select(FEATURE_NAMES).to_numpy().astype(float)
    validation_matrix = validation.select(FEATURE_NAMES).to_numpy().astype(float)
    x_train, state = _fit_preprocessing(
        train_matrix,
        FEATURE_NAMES,
        INDICATOR_NAMES,
        weights,
    )
    x_validation = _transform(validation_matrix, state)
    train_scales, validation_scales = _target_scales(
        selected,
        validation,
        str(configuration["target_scale"]),
    )
    target = selected["target_return"].to_numpy() / train_scales
    model = Ridge(
        alpha=float(configuration["ridge_alpha"]),
        solver="lsqr",
        tol=1e-8,
        max_iter=10_000,
        fit_intercept=not pooled,
    )
    if pooled:
        instruments = sorted(str(value) for value in selected["instrument_id"].unique())
        missing = set(str(value) for value in validation["instrument_id"].unique()) - set(
            instruments
        )
        if missing:
            raise ValueError(f"validation instruments absent from training: {sorted(missing)}")
        x_train = np.column_stack((x_train, _identity_matrix(selected, instruments)))
        x_validation = np.column_stack((x_validation, _identity_matrix(validation, instruments)))
    model.fit(
        x_train,
        target,
        sample_weight=np.asarray(state["weights"], dtype=float),
    )
    return model.predict(x_validation) * validation_scales, {
        "fit_rows": selected.height,
        "active_feature_count": len(cast(list[str], state["active_feature_names"])),
    }


def _local_prediction(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    configuration: dict[str, Any],
    fit_time: datetime,
) -> tuple[np.ndarray, dict[str, object]]:
    prediction = np.empty(validation.height, dtype=float)
    fit_rows = 0
    active_counts: list[int] = []
    instruments = sorted(str(value) for value in validation["instrument_id"].unique())
    validation_values = np.asarray(validation["instrument_id"].to_list(), dtype=object)
    for instrument in instruments:
        train_part = training.filter(pl.col("instrument_id") == instrument)
        positions = np.flatnonzero(validation_values == instrument)
        part = validation[positions.tolist()]
        values, metadata = _ridge_prediction(
            train_part,
            part,
            configuration,
            fit_time,
            pooled=False,
        )
        prediction[positions] = values
        fit_rows += cast(int, metadata["fit_rows"])
        active_counts.append(cast(int, metadata["active_feature_count"]))
    return prediction, {
        "fit_rows": fit_rows,
        "active_feature_count": min(active_counts),
    }


def _group_prediction(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    configuration: dict[str, Any],
    fit_time: datetime,
) -> tuple[np.ndarray, dict[str, object]]:
    prediction = np.empty(validation.height, dtype=float)
    fit_rows = 0
    active_counts: list[int] = []
    validation_groups = np.asarray(
        [_market_group(str(value)) for value in validation["instrument_id"]],
        dtype=object,
    )
    grouped_training = training.with_columns(
        pl.Series(
            "_market_group",
            [_market_group(str(value)) for value in training["instrument_id"]],
        )
    )
    for group in sorted(set(validation_groups.tolist())):
        positions = np.flatnonzero(validation_groups == group)
        part = validation[positions.tolist()]
        values, metadata = _ridge_prediction(
            grouped_training.filter(pl.col("_market_group") == group).drop("_market_group"),
            part,
            configuration,
            fit_time,
            pooled=True,
        )
        prediction[positions] = values
        fit_rows += cast(int, metadata["fit_rows"])
        active_counts.append(cast(int, metadata["active_feature_count"]))
    return prediction, {
        "fit_rows": fit_rows,
        "active_feature_count": min(active_counts),
    }


def _hierarchical_ridge_prediction(
    base_train: np.ndarray,
    base_validation: np.ndarray,
    targets: np.ndarray,
    weights: np.ndarray,
    train_group_codes: np.ndarray,
    validation_group_codes: np.ndarray,
    train_instrument_codes: np.ndarray,
    validation_instrument_codes: np.ndarray,
    *,
    group_count: int,
    instrument_count: int,
    alpha: float,
    instrument_penalty_ratio: float,
) -> tuple[np.ndarray, int]:
    """Solve global + group + instrument Ridge without a retained-scale dense design."""
    if instrument_penalty_ratio <= 0.0:
        raise ValueError("hierarchical instrument penalty ratio must be positive")
    feature_count = base_train.shape[1]
    column_count = feature_count * (1 + group_count + instrument_count)
    normal = np.zeros((column_count, column_count), dtype=float)
    right_hand_side = np.zeros(column_count, dtype=float)
    global_slice = slice(0, feature_count)
    weighted_train = weights[:, None] * base_train
    normal[global_slice, global_slice] = base_train.T @ weighted_train
    right_hand_side[global_slice] = base_train.T @ (weights * targets)

    group_offset = feature_count
    instrument_offset = feature_count * (1 + group_count)
    for group_code in range(group_count):
        selected = train_group_codes == group_code
        group_train = base_train[selected]
        group_weights = weights[selected]
        group_gram = group_train.T @ (group_weights[:, None] * group_train)
        group_slice = slice(
            group_offset + group_code * feature_count,
            group_offset + (group_code + 1) * feature_count,
        )
        normal[group_slice, group_slice] = group_gram
        normal[global_slice, group_slice] = group_gram
        normal[group_slice, global_slice] = group_gram
        right_hand_side[group_slice] = group_train.T @ (group_weights * targets[selected])

    ratio_scale = math.sqrt(instrument_penalty_ratio)
    for instrument_code in range(instrument_count):
        selected = train_instrument_codes == instrument_code
        instrument_train = base_train[selected]
        instrument_weights = weights[selected]
        instrument_gram = instrument_train.T @ (instrument_weights[:, None] * instrument_train)
        instrument_slice = slice(
            instrument_offset + instrument_code * feature_count,
            instrument_offset + (instrument_code + 1) * feature_count,
        )
        group_code = int(train_group_codes[selected][0])
        group_slice = slice(
            group_offset + group_code * feature_count,
            group_offset + (group_code + 1) * feature_count,
        )
        scaled_cross = instrument_gram / ratio_scale
        normal[instrument_slice, instrument_slice] = instrument_gram / instrument_penalty_ratio
        normal[global_slice, instrument_slice] = scaled_cross
        normal[instrument_slice, global_slice] = scaled_cross
        normal[group_slice, instrument_slice] = scaled_cross
        normal[instrument_slice, group_slice] = scaled_cross
        right_hand_side[instrument_slice] = (
            instrument_train.T @ (instrument_weights * targets[selected])
        ) / ratio_scale

    normal.flat[:: column_count + 1] += alpha
    coefficients = np.linalg.solve(normal, right_hand_side)
    prediction = base_validation @ coefficients[global_slice]
    for group_code in range(group_count):
        selected = validation_group_codes == group_code
        group_slice = slice(
            group_offset + group_code * feature_count,
            group_offset + (group_code + 1) * feature_count,
        )
        prediction[selected] += base_validation[selected] @ coefficients[group_slice]
    for instrument_code in range(instrument_count):
        selected = validation_instrument_codes == instrument_code
        instrument_slice = slice(
            instrument_offset + instrument_code * feature_count,
            instrument_offset + (instrument_code + 1) * feature_count,
        )
        prediction[selected] += (
            base_validation[selected] @ coefficients[instrument_slice]
        ) / ratio_scale
    return prediction, column_count


def _hierarchical_prediction(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    configuration: dict[str, Any],
    fit_time: datetime,
) -> tuple[np.ndarray, dict[str, object]]:
    selected = _select_training(training, fit_time, configuration)
    weights = _sample_weights(selected, fit_time, configuration, pooled=True)
    train_matrix = selected.select(FEATURE_NAMES).to_numpy().astype(float)
    validation_matrix = validation.select(FEATURE_NAMES).to_numpy().astype(float)
    x_train, state = _fit_preprocessing(
        train_matrix,
        FEATURE_NAMES,
        INDICATOR_NAMES,
        weights,
    )
    x_validation = _transform(validation_matrix, state)
    train_scales, validation_scales = _target_scales(
        selected,
        validation,
        str(configuration["target_scale"]),
    )
    base_train = np.column_stack((np.ones(selected.height), x_train))
    base_validation = np.column_stack((np.ones(validation.height), x_validation))
    groups = sorted({_market_group(str(value)) for value in selected["instrument_id"]})
    instruments = sorted(str(value) for value in selected["instrument_id"].unique())
    validation_instruments = {str(value) for value in validation["instrument_id"].unique()}
    missing = validation_instruments - set(instruments)
    if missing:
        raise ValueError(
            f"hierarchical validation instruments absent from training: {sorted(missing)}"
        )
    group_codes = {value: index for index, value in enumerate(groups)}
    instrument_codes = {value: index for index, value in enumerate(instruments)}
    train_group_codes = np.asarray(
        [group_codes[_market_group(str(value))] for value in selected["instrument_id"]],
        dtype=np.intp,
    )
    validation_group_codes = np.asarray(
        [group_codes[_market_group(str(value))] for value in validation["instrument_id"]],
        dtype=np.intp,
    )
    train_instrument_codes = np.asarray(
        [instrument_codes[str(value)] for value in selected["instrument_id"]],
        dtype=np.intp,
    )
    validation_instrument_codes = np.asarray(
        [instrument_codes[str(value)] for value in validation["instrument_id"]],
        dtype=np.intp,
    )
    prediction, column_count = _hierarchical_ridge_prediction(
        base_train,
        base_validation,
        selected["target_return"].to_numpy() / train_scales,
        np.asarray(state["weights"], dtype=float),
        train_group_codes,
        validation_group_codes,
        train_instrument_codes,
        validation_instrument_codes,
        group_count=len(groups),
        instrument_count=len(instruments),
        alpha=float(configuration["ridge_alpha"]),
        instrument_penalty_ratio=float(configuration["hierarchical_instrument_penalty_ratio"]),
    )
    return prediction * validation_scales, {
        "fit_rows": selected.height,
        "active_feature_count": len(cast(list[str], state["active_feature_names"])),
        "hierarchical_design_columns": column_count,
    }


def _raw_prediction(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    configuration: dict[str, Any],
    fit_time: datetime,
) -> tuple[np.ndarray, dict[str, object]]:
    pooling = configuration["pooling"]
    if pooling == "LOCAL_RIDGE":
        return _local_prediction(training, validation, configuration, fit_time)
    if pooling == "GROUP_POOLED_RIDGE":
        return _group_prediction(training, validation, configuration, fit_time)
    if pooling == "HIERARCHICAL_RIDGE":
        return _hierarchical_prediction(training, validation, configuration, fit_time)
    if pooling == "FULLY_POOLED_RIDGE":
        return _ridge_prediction(training, validation, configuration, fit_time, pooled=True)
    raise ValueError(f"unsupported pooling policy: {pooling}")


def _fit_calibration(
    prediction: np.ndarray,
    target: np.ndarray,
    instruments: Sequence[str],
    policy: str,
) -> tuple[float, float]:
    counts: dict[str, int] = {}
    for instrument in instruments:
        counts[instrument] = counts.get(instrument, 0) + 1
    weights = np.asarray(
        [len(instruments) / len(counts) / counts[value] for value in instruments],
        dtype=float,
    )
    if policy == "AFFINE":
        mean_prediction = float(np.average(prediction, weights=weights))
        mean_target = float(np.average(target, weights=weights))
        centred = prediction - mean_prediction
        denominator = float(np.sum(weights * centred**2))
        if denominator <= 0:
            raise ValueError("affine calibration forecast variance is zero")
        slope = float(np.sum(weights * centred * (target - mean_target)) / denominator)
        return slope, mean_target - slope * mean_prediction
    if policy == "NON_NEGATIVE_SLOPE":
        denominator = float(np.sum(weights * prediction**2))
        if denominator <= 0:
            raise ValueError("slope-only calibration forecast magnitude is zero")
        slope = max(0.0, float(np.sum(weights * prediction * target) / denominator))
        return slope, 0.0
    raise ValueError(f"unsupported calibration policy: {policy}")


def _outer_prediction(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    configuration: dict[str, Any],
    fit_time: datetime,
) -> tuple[np.ndarray, dict[str, object]]:
    policy = configuration["calibration"]
    if policy == "RAW":
        prediction, metadata = _raw_prediction(training, validation, configuration, fit_time)
        return prediction, {
            **metadata,
            "calibration_slope": None,
            "calibration_intercept": None,
        }

    inner_start = fit_time - timedelta(days=int(configuration["calibration_inner_days"]))
    inner_training = training.filter(
        (pl.col("decision_time") < inner_start) & (pl.col("target_available_at") < inner_start)
    )
    inner_validation = training.filter(
        (pl.col("decision_time") >= inner_start)
        & (pl.col("decision_time") < fit_time)
        & (pl.col("target_available_at") < fit_time)
    )
    if inner_training.is_empty() or inner_validation.is_empty():
        raise ValueError("inner calibration split lacks training or validation rows")
    inner_prediction, _ = _raw_prediction(
        inner_training,
        inner_validation,
        configuration,
        inner_start,
    )
    slope, intercept = _fit_calibration(
        inner_prediction,
        inner_validation["target_return"].to_numpy(),
        [str(value) for value in inner_validation["instrument_id"]],
        str(policy),
    )
    outer_prediction, metadata = _raw_prediction(
        training,
        validation,
        configuration,
        fit_time,
    )
    return slope * outer_prediction + intercept, {
        **metadata,
        "calibration_slope": slope,
        "calibration_intercept": intercept,
        "inner_calibration_rows": inner_validation.height,
    }


def _select_validation(
    rows: pl.DataFrame,
    block_name: str,
    block_specs: Mapping[str, Mapping[str, Any]],
) -> pl.DataFrame:
    validation = rows.filter(pl.col("block") == block_name)
    if block_name in DEVELOPMENT_BLOCK_NAMES:
        terminal_start = datetime.fromisoformat(str(block_specs[TERMINAL_BLOCK]["start"]))
        validation = validation.filter(pl.col("target_available_at") < terminal_start)
    return validation


def _evaluate_configuration(
    rows: pl.DataFrame,
    configuration: dict[str, Any],
    block_specs: dict[str, dict[str, Any]],
    block_names: Sequence[str],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    prediction_parts: list[pl.DataFrame] = []
    target_parts: list[pl.DataFrame] = []
    fold_metadata: list[dict[str, object]] = []
    for block_name in block_names:
        start = datetime.fromisoformat(str(block_specs[block_name]["start"]))
        validation = _select_validation(rows, block_name, block_specs)
        training = rows.filter(
            (pl.col("decision_time") < start) & (pl.col("target_available_at") < start)
        )
        if validation.is_empty() or training.is_empty():
            raise ValueError(f"{block_name} lacks training or validation rows")
        prediction, metadata = _outer_prediction(
            training,
            validation,
            configuration,
            start,
        )
        prediction_parts.append(
            validation.select(*KEYS).with_columns(pl.Series("expected_return", prediction))
        )
        target_parts.append(validation.select(*KEYS, "target_return", "target_valid", "block"))
        fold_metadata.append(
            {
                "block": block_name,
                "available_training_rows": training.height,
                "validation_rows": validation.height,
                **metadata,
            }
        )
    result = evaluate_against_zero(
        pl.concat(prediction_parts),
        pl.concat(target_parts),
        model_name=f"LAB_S_{configuration_id(configuration)[:12]}",
    )
    return result, fold_metadata


def _base_configuration(design: dict[str, Any], universe_name: str) -> dict[str, Any]:
    return {
        "universe": universe_name,
        "horizon_minutes": 15,
        "feature_set": "P0",
        "feature_semantic_sha256": design["feature_semantic_sha256"],
        "pooling": "FULLY_POOLED_RIDGE",
        "hierarchical_instrument_penalty_ratio": None,
        "target_scale": "RAW_RETURN",
        "calibration": "RAW",
        "recency": "EXPANDING",
        "ridge_alpha": float(design["ridge_alpha"]),
        "rolling_history_days": int(design["rolling_history_days"]),
        "decay_half_life_days": int(design["decay_half_life_days"]),
        "calibration_inner_days": int(design["calibration_inner_days"]),
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
    }


def _one_factor_configurations(
    design: dict[str, Any],
    universe_name: str,
) -> list[tuple[str, dict[str, Any]]]:
    baseline = _base_configuration(design, universe_name)
    values: list[tuple[str, dict[str, Any]]] = []
    for pooling in ("LOCAL_RIDGE", "GROUP_POOLED_RIDGE", "FULLY_POOLED_RIDGE"):
        values.append(("DEGREE_OF_POOLING", {**baseline, "pooling": pooling}))
    for ratio in design["hierarchical_instrument_penalty_ratios"]:
        values.append(
            (
                "DEGREE_OF_POOLING",
                {
                    **baseline,
                    "pooling": "HIERARCHICAL_RIDGE",
                    "hierarchical_instrument_penalty_ratio": float(ratio),
                },
            )
        )
    for target_scale in ("RAW_RETURN", "CAUSAL_VOL_STANDARDISED"):
        values.append(("TARGET_SCALE", {**baseline, "target_scale": target_scale}))
    for calibration in ("RAW", "AFFINE", "NON_NEGATIVE_SLOPE"):
        values.append(("CALIBRATION", {**baseline, "calibration": calibration}))
    for recency in ("EXPANDING", "ROLLING", "EXPONENTIAL_DECAY"):
        values.append(("TRAINING_RECENCY", {**baseline, "recency": recency}))
    return values


def _result_row(
    factor: str,
    configuration: dict[str, Any],
    result: dict[str, object],
    fold_metadata: Sequence[dict[str, object]],
    phase: str,
) -> dict[str, object]:
    slopes = [
        float(cast(float, item["calibration_slope"]))
        for item in fold_metadata
        if item.get("calibration_slope") is not None
    ]
    row: dict[str, object] = {
        "phase": phase,
        "factor": factor,
        "configuration_id": configuration_id(configuration),
        "configuration_json": json.dumps(configuration, sort_keys=True),
        "universe": configuration["universe"],
        "pooling": configuration["pooling"],
        "hierarchical_instrument_penalty_ratio": configuration[
            "hierarchical_instrument_penalty_ratio"
        ],
        "target_scale": configuration["target_scale"],
        "calibration": configuration["calibration"],
        "recency": configuration["recency"],
        "evaluated_blocks_json": json.dumps(result["evaluated_blocks"]),
        "terminal_block_accessed": result["terminal_block_accessed"],
        "fitted_calibration_slopes_json": json.dumps(slopes),
        "fold_metadata_json": json.dumps(fold_metadata, sort_keys=True),
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
    }
    for field in RESULT_FIELDS:
        row[field] = result[field]
    return row


def _calibration_is_eligible(
    row: Mapping[str, object],
    design: Mapping[str, Any],
) -> bool:
    if row["calibration"] == "RAW":
        return True
    slopes = cast(list[float], json.loads(str(row["fitted_calibration_slopes_json"])))
    if len(slopes) != 3 or min(slopes) <= 0:
        return False
    return max(slopes) / min(slopes) <= float(design["calibration_stability_max_ratio"])


def _nominate(
    one_factor_rows: Sequence[dict[str, object]],
    design: dict[str, Any],
    universe: str,
    factor: str,
) -> dict[str, object]:
    candidates = [
        row
        for row in one_factor_rows
        if row["universe"] == universe
        and row["factor"] == factor
        and (factor != "CALIBRATION" or _calibration_is_eligible(row, design))
    ]
    if not candidates:
        raise ValueError(f"no eligible {factor} candidate for {universe}")
    return min(candidates, key=lambda row: cast(float, row["model_instrument_balanced_mse"]))


def _combined_configuration(
    design: dict[str, Any],
    universe: str,
    nominations: dict[str, dict[str, object]],
) -> dict[str, Any]:
    configuration = _base_configuration(design, universe)
    pooling = nominations["DEGREE_OF_POOLING"]
    configuration.update(
        {
            "pooling": pooling["pooling"],
            "hierarchical_instrument_penalty_ratio": pooling[
                "hierarchical_instrument_penalty_ratio"
            ],
            "target_scale": nominations["TARGET_SCALE"]["target_scale"],
            "calibration": nominations["CALIBRATION"]["calibration"],
            "recency": nominations["TRAINING_RECENCY"]["recency"],
        }
    )
    return configuration


def _attempt(
    rows: pl.DataFrame,
    configuration: dict[str, Any],
    block_specs: dict[str, dict[str, Any]],
    blocks: Sequence[str],
    register: Path,
    manifest_sha256: str,
) -> tuple[dict[str, object], list[dict[str, object]]] | None:
    try:
        result, fold_metadata = _evaluate_configuration(
            rows,
            configuration,
            block_specs,
            blocks,
        )
    except Exception as exc:
        append_attempt(
            register,
            workstream=WORKSTREAM,
            configuration=configuration,
            result={
                "status": "FAILED",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
            manifest_sha256=manifest_sha256,
        )
        return None
    append_attempt(
        register,
        workstream=WORKSTREAM,
        configuration=configuration,
        result=result,
        manifest_sha256=manifest_sha256,
    )
    return result, fold_metadata


def run_smoke(
    design: dict[str, Any],
    manifest_path: Path,
    output_root: Path,
) -> None:
    manifest_sha256 = str(design["manifest_sha256"])
    manifest = authenticate_manifest(manifest_path, manifest_sha256)
    instruments = cast(list[str], design["universes"]["CORE_6"])[:2]
    rows = _load_rows(
        manifest_path,
        manifest_sha256,
        instruments,
        ("TRAINING_ONLY", "DEV_1"),
    )
    start = datetime.fromisoformat(str(_block_specs(manifest)["DEV_1"]["start"]))
    training = rows.filter(
        (pl.col("decision_time") >= start - timedelta(days=7))
        & (pl.col("decision_time") < start)
        & (pl.col("target_available_at") < start)
    )
    first_times = (
        rows.filter(pl.col("block") == "DEV_1")["decision_time"].unique().sort().head(60).to_list()
    )
    validation = rows.filter(
        (pl.col("block") == "DEV_1") & pl.col("decision_time").is_in(first_times)
    )
    configuration = _base_configuration(design, "SMOKE_CORE_2")
    prediction, metadata = _outer_prediction(training, validation, configuration, start)
    result = evaluate_against_zero(
        validation.select(*KEYS).with_columns(pl.Series("expected_return", prediction)),
        validation.select(*KEYS, "target_return", "target_valid", "block"),
        model_name="LAB_S_SMOKE",
    )
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "smoke.json").write_bytes(
        _canonical_json(
            {
                "status": "SUCCEEDED",
                "manifest_sha256": manifest_sha256,
                "configuration": configuration,
                "metadata": metadata,
                "result": result,
                "evidence_label": LABEL,
                "source_class": SOURCE_CLASS,
            }
        )
    )


def run_scale_projection(
    design: dict[str, Any],
    manifest_path: Path,
    output_root: Path,
) -> None:
    manifest_sha256 = str(design["manifest_sha256"])
    manifest = authenticate_manifest(manifest_path, manifest_sha256)
    instruments = cast(list[str], design["universes"]["ALL_20"])
    rows = _load_rows(
        manifest_path,
        manifest_sha256,
        instruments,
        ("TRAINING_ONLY", "DEV_1"),
    )
    start = datetime.fromisoformat(str(_block_specs(manifest)["DEV_1"]["start"]))
    training = rows.filter(
        (pl.col("decision_time") < start) & (pl.col("target_available_at") < start)
    )
    validation = rows.filter(pl.col("block") == "DEV_1")
    configuration = {
        **_base_configuration(design, "ALL_20"),
        "pooling": "HIERARCHICAL_RIDGE",
        "hierarchical_instrument_penalty_ratio": float(
            design["hierarchical_instrument_penalty_ratios"][0]
        ),
    }
    started = datetime.now().astimezone()
    prediction, metadata = _outer_prediction(
        training,
        validation,
        configuration,
        start,
    )
    elapsed_seconds = (datetime.now().astimezone() - started).total_seconds()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "scale-projection.json").write_bytes(
        _canonical_json(
            {
                "status": "SUCCEEDED",
                "purpose": "IMPLEMENTATION_EVIDENCE_ONLY",
                "manifest_sha256": manifest_sha256,
                "configuration": configuration,
                "metadata": metadata,
                "training_rows": training.height,
                "validation_rows": validation.height,
                "prediction_count": prediction.size,
                "predictions_all_finite": bool(np.isfinite(prediction).all()),
                "elapsed_seconds": elapsed_seconds,
                "conservative_full_attempt_seconds": 300.0 + 60.0 * elapsed_seconds,
                "projection_basis": (
                    "five minutes non-hierarchical allowance plus sixty times one retained-shaped "
                    "ALL_20 hierarchical fold for both universes, ratios, combined calibration "
                    "possibility, and twofold margin"
                ),
                "evidence_label": LABEL,
                "source_class": SOURCE_CLASS,
            }
        )
    )


def run_development(
    design: dict[str, Any],
    manifest_path: Path,
    output_root: Path,
) -> None:
    manifest_sha256 = str(design["manifest_sha256"])
    manifest = authenticate_manifest(manifest_path, manifest_sha256)
    specs = _block_specs(manifest)
    register = output_root / "run-register.jsonl"
    freeze_path = output_root / "finalist-freeze.json"
    if register.exists() or freeze_path.exists():
        raise FileExistsError("LAB-S development outputs already exist")
    output_root.mkdir(parents=True, exist_ok=True)

    one_factor_rows: list[dict[str, object]] = []
    successful: dict[str, tuple[dict[str, object], list[dict[str, object]]]] = {}
    rows_by_universe: dict[str, pl.DataFrame] = {}
    candidates_by_universe: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for universe, instruments in cast(dict[str, list[str]], design["universes"]).items():
        rows = _load_rows(
            manifest_path,
            manifest_sha256,
            instruments,
            ("TRAINING_ONLY", *DEVELOPMENT_BLOCK_NAMES),
        )
        rows_by_universe[universe] = rows
        candidates = _one_factor_configurations(design, universe)
        candidates_by_universe[universe] = candidates
        unique = {configuration_id(item): item for _, item in candidates}
        for identifier, configuration in unique.items():
            attempted = _attempt(
                rows,
                configuration,
                specs,
                DEVELOPMENT_BLOCK_NAMES,
                register,
                manifest_sha256,
            )
            if attempted is not None:
                successful[identifier] = attempted
        for factor, configuration in candidates:
            identifier = configuration_id(configuration)
            if identifier not in successful:
                continue
            result, metadata = successful[identifier]
            one_factor_rows.append(
                _result_row(
                    factor,
                    configuration,
                    result,
                    metadata,
                    "PRE_HOLDOUT_ONE_FACTOR",
                )
            )
    if not one_factor_rows:
        raise RuntimeError("every one-factor configuration failed")
    pl.DataFrame(one_factor_rows, infer_schema_length=None).write_parquet(
        output_root / "one-factor-results.parquet"
    )

    finalist_configurations: list[dict[str, Any]] = []
    nomination_record: dict[str, dict[str, object]] = {}
    for universe in cast(dict[str, list[str]], design["universes"]):
        nominations = {
            factor: _nominate(one_factor_rows, design, universe, factor)
            for factor in (
                "DEGREE_OF_POOLING",
                "TARGET_SCALE",
                "CALIBRATION",
                "TRAINING_RECENCY",
            )
        }
        nomination_record[universe] = {
            factor: row["configuration_id"] for factor, row in nominations.items()
        }
        finalist_configurations.extend(
            [
                _base_configuration(design, universe),
                _combined_configuration(design, universe, nominations),
            ]
        )
    unique_finalists = {configuration_id(item): item for item in finalist_configurations}
    if len(unique_finalists) > int(design["finalist_limit"]):
        raise ValueError("combined finalist limit exceeded")

    combined_rows: list[dict[str, object]] = []
    for identifier, configuration in unique_finalists.items():
        if identifier not in successful:
            attempted = _attempt(
                rows_by_universe[str(configuration["universe"])],
                configuration,
                specs,
                DEVELOPMENT_BLOCK_NAMES,
                register,
                manifest_sha256,
            )
            if attempted is not None:
                successful[identifier] = attempted
        if identifier not in successful:
            continue
        result, metadata = successful[identifier]
        combined_rows.append(
            _result_row(
                "COMBINED_FINALIST",
                configuration,
                result,
                metadata,
                "PRE_HOLDOUT_FINALIST",
            )
        )
    if len(combined_rows) != len(unique_finalists):
        raise RuntimeError("a nominated combined finalist failed")
    pl.DataFrame(combined_rows, infer_schema_length=None).write_parquet(
        output_root / "combined-finalists.parquet"
    )
    finalist_ids = [str(row["configuration_id"]) for row in combined_rows]
    freeze_finalists(
        register,
        freeze_path,
        workstream=WORKSTREAM,
        finalist_configuration_ids=finalist_ids,
        manifest_sha256=manifest_sha256,
    )
    state = {
        "status": "DEVELOPMENT_COMPLETE",
        "programme_base_sha": design["programme_base_sha"],
        "lab0_dependency_sha": design["lab0_dependency_sha"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "finalist_freeze_sha256": _sha256(freeze_path),
        "finalist_configurations": {
            configuration_id(item): item for item in unique_finalists.values()
        },
        "nominations": nomination_record,
        "one_factor_configuration_count": len(
            {
                configuration_id(item)
                for candidates in candidates_by_universe.values()
                for _, item in candidates
            }
        ),
        "combined_finalist_count": len(combined_rows),
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
    }
    (output_root / "development-state.json").write_bytes(_canonical_json(state))


def _format_metric(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def _write_result(
    output_root: Path,
    state: dict[str, Any],
    combined_rows: Sequence[dict[str, object]],
    register_entries: Sequence[dict[str, Any]],
) -> None:
    development = [row for row in combined_rows if row["phase"] == "PRE_HOLDOUT_FINALIST"]
    terminal = [row for row in combined_rows if row["phase"] == "FORMER_HOLDOUT_FINALIST"]
    lines = [
        "# LAB-S result",
        "",
        f"**{LABEL}** — model development and hypothesis generation only.",
        "",
        f"Source class: {SOURCE_CLASS}.",
        "",
        "The former holdout was scientifically consumed before this laboratory. "
        "Its rows were accessed only after the finalist freeze and are an explicitly "
        "post-hoc external development block, not confirmation.",
        "",
        "## Configuration counts",
        "",
        f"- Unique one-factor configurations: {state['one_factor_configuration_count']}",
        f"- Combined finalists frozen: {state['combined_finalist_count']}",
        f"- Registered attempts: {len(register_entries)}",
        "- Failed attempts: "
        f"{sum(item['attempt_status'] == 'FAILED' for item in register_entries)}",
        "",
        "## Combined finalist results",
        "",
        "| phase | universe | pooling | target | calibration | recency | "
        "delta MSE | skill | +blocks | +instruments | slope | Spearman |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in [*development, *terminal]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["phase"]),
                    str(row["universe"]),
                    str(row["pooling"]),
                    str(row["target_scale"]),
                    str(row["calibration"]),
                    str(row["recency"]),
                    _format_metric(row["direct_delta_mse_versus_zero"]),
                    _format_metric(row["skill_versus_zero"]),
                    str(row["positive_chronological_block_count"]),
                    str(row["positive_instrument_count"]),
                    _format_metric(row["calibration_slope"]),
                    _format_metric(row["spearman_correlation"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Selection record",
            "",
            "Nominations were made only from DEV_1-DEV_3. Calibrated variants were "
            "eligible only when all three fitted inner-validation slopes were positive "
            "and their maximum/minimum ratio was at most 3.",
            "",
            json.dumps(state["nominations"], indent=2, sort_keys=True),
            "",
            "No native-IBKR execution, second-holdout, confirmation, promotion, or "
            "decision-grade conclusion is claimed.",
            "",
        ]
    )
    (output_root / "result.md").write_text("\n".join(lines), encoding="utf-8")


def run_terminal(
    design: dict[str, Any],
    manifest_path: Path,
    output_root: Path,
) -> None:
    state_path = output_root / "development-state.json"
    freeze_path = output_root / "finalist-freeze.json"
    state = json.loads(state_path.read_bytes())
    manifest_sha256 = str(design["manifest_sha256"])
    if (
        state["status"] != "DEVELOPMENT_COMPLETE"
        or state["manifest_sha256"] != manifest_sha256
        or state["finalist_freeze_sha256"] != _sha256(freeze_path)
    ):
        raise ValueError("LAB-S development state or finalist freeze changed")
    manifest = authenticate_manifest(manifest_path, manifest_sha256)
    specs = _block_specs(manifest)
    configurations = cast(dict[str, dict[str, Any]], state["finalist_configurations"])
    first_identifier = next(iter(configurations))
    freeze_sha256 = str(state["finalist_freeze_sha256"])
    combined_path = output_root / "combined-finalists.parquet"
    combined_rows = pl.read_parquet(combined_path).to_dicts()
    register = output_root / "run-register.jsonl"

    preterminal_by_universe: dict[str, pl.DataFrame] = {}
    terminal_by_universe: dict[str, pl.DataFrame] = {}
    for universe, instruments in cast(dict[str, list[str]], design["universes"]).items():
        preterminal_by_universe[universe] = _load_rows(
            manifest_path,
            manifest_sha256,
            instruments,
            ("TRAINING_ONLY", *DEVELOPMENT_BLOCK_NAMES),
        )
        terminal_by_universe[universe] = _load_rows(
            manifest_path,
            manifest_sha256,
            instruments,
            (TERMINAL_BLOCK,),
            finalist_freeze=freeze_path,
            finalist_freeze_sha256=freeze_sha256,
            configuration_identifier=first_identifier,
        )

    terminal_rows: list[dict[str, object]] = []
    for configuration in configurations.values():
        universe = str(configuration["universe"])
        rows = pl.concat([preterminal_by_universe[universe], terminal_by_universe[universe]]).sort(
            "decision_time", "instrument_id"
        )
        attempted = _attempt(
            rows,
            configuration,
            specs,
            (TERMINAL_BLOCK,),
            register,
            manifest_sha256,
        )
        if attempted is None:
            continue
        result, metadata = attempted
        terminal_rows.append(
            _result_row(
                "COMBINED_FINALIST",
                configuration,
                result,
                metadata,
                "FORMER_HOLDOUT_FINALIST",
            )
        )
    if len(terminal_rows) != len(configurations):
        raise RuntimeError("a frozen finalist failed terminal evaluation")
    all_rows = [*combined_rows, *terminal_rows]
    pl.DataFrame(all_rows, infer_schema_length=None).write_parquet(combined_path)
    register_entries = [
        json.loads(line)
        for line in register.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    state["status"] = "COMPLETE"
    state["terminal_evaluated_at"] = datetime.now().astimezone().isoformat()
    state_path.write_bytes(_canonical_json(state))
    _write_result(output_root, state, all_rows, register_entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("smoke", "scale-projection", "development", "terminal"),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    design = _read_configuration(arguments.config)
    if arguments.command == "smoke":
        run_smoke(design, arguments.manifest, arguments.output)
    elif arguments.command == "scale-projection":
        run_scale_projection(design, arguments.manifest, arguments.output)
    elif arguments.command == "development":
        run_development(design, arguments.manifest, arguments.output)
    else:
        run_terminal(design, arguments.manifest, arguments.output)


if __name__ == "__main__":
    main()
