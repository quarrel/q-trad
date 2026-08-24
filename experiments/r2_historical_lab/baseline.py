"""Real six-target, fifteen-minute baseline reconstruction over LAB-derived rows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS

EXPECTED_BASELINE = {
    "support": 239_535,
    "ZERO_RETURN": 0.0000028404586671320294,
    "POOLED_LOCAL_RIDGE": 0.000002841663414474555,
    "LOCAL_RIDGE": 0.0000028481068080631273,
}
EXPECTED_REPORT_SHA256 = "195015509cc9dd43b94815ad659dacd04470c849a63c5e51ab4a535c4dbb1a02"
EXPECTED_HOLDOUT_START = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
METRIC_ABS_TOLERANCE = 1e-14
PREPROCESSING_ABS_TOLERANCE = 1e-12
COEFFICIENT_ABS_TOLERANCE = 1e-8
INTERCEPT_ABS_TOLERANCE = 1e-9
ORIGINAL_TARGETS = (
    "commodity:spot-gold",
    "commodity:us-crude",
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _parts(root: Path, kind: str, instruments: Sequence[str]) -> list[Path]:
    selected = set(instruments)
    paths = []
    for path in (root / kind).glob("instrument=*/year=*/month=*/part.parquet"):
        instrument = path.parts[-4].split("=", maxsplit=1)[1].replace("--", ":")
        if instrument in selected:
            paths.append(path)
    if not paths:
        raise ValueError(f"no {kind} parts found for baseline reconstruction")
    return sorted(paths)


def _load_baseline_rows(root: Path) -> pl.DataFrame:
    feature_paths = _parts(root, "features", ORIGINAL_TARGETS)
    target_paths = _parts(root / "targets/horizon=15m", "", ORIGINAL_TARGETS)
    context_paths = _parts(root, "context", ORIGINAL_TARGETS)
    features = pl.read_parquet(feature_paths)
    targets = pl.read_parquet(target_paths).filter(pl.col("target_valid"))
    context = pl.read_parquet(context_paths)

    totals = context.group_by("decision_time").agg(
        pl.col("current_available").sum().alias("_total_available")
    )
    own = context.select(
        "instrument_id",
        "decision_time",
        pl.col("current_available").alias("_own_available"),
    )
    features = (
        features.join(totals, on="decision_time", how="left")
        .join(own, on=["instrument_id", "decision_time"], how="left")
        .with_columns(
            (
                pl.col("_total_available").fill_null(0.0)
                - pl.col("_own_available").fill_null(0.0)
            ).alias("cross_market_available_count")
        )
        .drop("_total_available", "_own_available")
    )
    return (
        targets.join(features, on=["instrument_id", "decision_time"], how="inner")
        .sort("decision_time", "instrument_id")
    )


def _fit_references(terminal_root: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    oof_root = terminal_root / "oof"
    manifest = _read_json(oof_root / "manifest.json")
    report_path = oof_root / "evaluation/report.json"
    if _sha256(report_path) != EXPECTED_REPORT_SHA256:
        raise ValueError("retained evaluation report bytes changed")
    report = _read_json(report_path)
    wanted: set[str] = set()
    for model in report["evaluated_models"]:
        if model["model_family"] in {"LOCAL_RIDGE", "POOLED_LOCAL_RIDGE"}:
            wanted.update(model["fold_fit_ids"])
    references = {
        item["semantic_id"]: item
        for item in manifest["fit_children"]
        if item["semantic_id"] in wanted
    }
    if set(references) != wanted:
        raise ValueError("retained OOF manifest does not bind every evaluated baseline fit")
    return references, wanted


def _weights(frame: pl.DataFrame, pooled: bool) -> np.ndarray:
    if not pooled:
        return np.ones(frame.height, dtype=float)
    instruments = frame["instrument_id"].to_list()
    counts: dict[str, int] = {}
    for instrument in instruments:
        counts[instrument] = counts.get(instrument, 0) + 1
    total_per_instrument = len(instruments) / len(counts)
    return np.asarray(
        [total_per_instrument / counts[instrument] for instrument in instruments],
        dtype=float,
    )


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    ordered_values = values[order]
    ordered_weights = weights[order]
    cutoff = float(ordered_weights.sum()) / 2
    return float(
        ordered_values[np.searchsorted(np.cumsum(ordered_weights), cutoff, side="left")]
    )


def _fit_preprocessing(
    matrix: np.ndarray,
    feature_names: Sequence[str],
    indicators: set[str],
    weights: np.ndarray,
) -> tuple[np.ndarray, dict[str, object]]:
    weights = weights * (len(weights) / float(weights.sum()))
    medians: list[float | None] = []
    means: list[float | None] = []
    scales: list[float | None] = []
    active: list[str] = []
    all_null: list[str] = []
    zero_variance: list[str] = []
    transformed: list[np.ndarray] = []
    for position, name in enumerate(feature_names):
        column = matrix[:, position]
        if name in indicators:
            binary = np.where(np.isnan(column), 0.0, column)
            medians.append(None)
            means.append(None)
            scales.append(None)
            if np.ptp(binary) == 0:
                zero_variance.append(name)
            else:
                active.append(name)
                transformed.append(binary)
            continue
        observed = ~np.isnan(column)
        if not observed.any():
            medians.append(None)
            means.append(None)
            scales.append(None)
            all_null.append(name)
            continue
        median_value = _weighted_median(column[observed], weights[observed])
        filled = np.where(np.isnan(column), median_value, column)
        mean = float(np.average(filled, weights=weights))
        variance = float(np.average((filled - mean) ** 2, weights=weights))
        medians.append(median_value)
        if variance <= 0:
            means.append(None)
            scales.append(None)
            zero_variance.append(name)
        else:
            scale = float(np.sqrt(variance))
            means.append(mean)
            scales.append(scale)
            active.append(name)
            transformed.append((filled - mean) / scale)
    x = np.column_stack(transformed) if transformed else np.empty((matrix.shape[0], 0))
    return x, {
        "feature_names": list(feature_names),
        "indicator_feature_names": sorted(indicators, key=feature_names.index),
        "medians": medians,
        "means": means,
        "scales": scales,
        "active_feature_names": active,
        "dropped_all_null_feature_names": all_null,
        "dropped_zero_variance_feature_names": zero_variance,
        "weights": weights,
    }


def _transform(matrix: np.ndarray, state: dict[str, object]) -> np.ndarray:
    feature_names = cast(list[str], state["feature_names"])
    active = set(cast(list[str], state["active_feature_names"]))
    indicators = set(cast(list[str], state["indicator_feature_names"]))
    medians = cast(list[float | None], state["medians"])
    means = cast(list[float | None], state["means"])
    scales = cast(list[float | None], state["scales"])
    columns: list[np.ndarray] = []
    for position, name in enumerate(feature_names):
        if name not in active:
            continue
        column = matrix[:, position]
        if name in indicators:
            columns.append(np.where(np.isnan(column), 0.0, column))
        else:
            median_value = medians[position]
            mean = means[position]
            scale = scales[position]
            if median_value is None or mean is None or scale is None:
                raise ValueError("active continuous feature has incomplete preprocessing")
            columns.append((np.where(np.isnan(column), median_value, column) - mean) / scale)
    return np.column_stack(columns)


def _maximum_state_delta(state: dict[str, object], retained: dict[str, Any]) -> float:
    for field in (
        "feature_names",
        "indicator_feature_names",
        "active_feature_names",
        "dropped_all_null_feature_names",
        "dropped_zero_variance_feature_names",
    ):
        if list(cast(list[str], state[field])) != retained[field]:
            raise ValueError(f"LAB preprocessing differs from retained fit: {field}")
    maximum = 0.0
    for field in ("medians", "means", "scales"):
        observed = cast(list[float | None], state[field])
        expected = retained[field]
        if len(observed) != len(expected):
            raise ValueError(f"LAB preprocessing length differs: {field}")
        for left, right in zip(observed, expected, strict=True):
            if left is None or right is None:
                if left is not right:
                    raise ValueError(f"LAB preprocessing null status differs: {field}")
            else:
                maximum = max(maximum, abs(float(left) - float(right)))
    return maximum


def _instrument_balanced_mse(frame: pl.DataFrame, prediction: str) -> float:
    values = (
        frame.with_columns(
            ((pl.col(prediction) - pl.col("target_return")) ** 2).alias("_se")
        )
        .group_by("instrument_id")
        .agg(pl.col("_se").mean().alias("_mse"))
    )
    return float(cast(float, values["_mse"].mean()))


def reconstruct_baseline(
    output_root: Path,
    terminal_root: Path,
    retained_folds: Sequence[dict[str, Any]],
) -> dict[str, object]:
    """Refit exact retained outer models on LAB rows and replay their OOF predictions."""

    rows = _load_baseline_rows(output_root)
    fold_by_id = {fold["fold_id"]: fold for fold in retained_folds}
    references, wanted = _fit_references(terminal_root)
    oof_root = terminal_root / "oof"
    predictions: list[pl.DataFrame] = []
    fit_checks: list[dict[str, object]] = []

    for fit_id in sorted(wanted):
        reference = references[fit_id]
        path = oof_root / reference["path"]
        if _sha256(path) != reference["sha256"]:
            raise ValueError(f"retained fit bytes changed: {path}")
        fit = _read_json(path)
        family = fit["model_family"]
        pooled = family == "POOLED_LOCAL_RIDGE"
        if family not in {"LOCAL_RIDGE", "POOLED_LOCAL_RIDGE"}:
            continue
        fold = fold_by_id[fit["outer_fold_id"]]
        training_ids = cast(list[str], fold["training_target_ids"])
        validation_ids = cast(list[str], fold["validation_target_ids"])
        training = rows.filter(pl.col("target_id").is_in(training_ids))
        validation = rows.filter(
            pl.col("target_id").is_in(validation_ids)
            & (pl.col("target_available_at") < EXPECTED_HOLDOUT_START)
        )
        if not pooled:
            instrument = fit["target_instrument_id"]
            training = training.filter(pl.col("instrument_id") == instrument)
            validation = validation.filter(pl.col("instrument_id") == instrument)
        training = training.sort("decision_time", "instrument_id")
        validation = validation.sort("decision_time", "instrument_id")
        if training.height != fit["fit_row_count"]:
            raise ValueError(
                f"LAB training support differs for {fit_id}: "
                f"{training.height} != {fit['fit_row_count']}"
            )

        retained_preprocessing = fit["preprocessing"]
        feature_names = retained_preprocessing["feature_names"]
        train_matrix = training.select(feature_names).to_numpy().astype(float)
        validation_matrix = validation.select(feature_names).to_numpy().astype(float)
        weights = _weights(training, pooled)
        x_train, state = _fit_preprocessing(
            train_matrix,
            feature_names,
            set(retained_preprocessing["indicator_feature_names"]),
            weights,
        )
        state_delta = _maximum_state_delta(state, retained_preprocessing)
        x_validation = _transform(validation_matrix, state)
        coefficient_names = list(
            cast(list[str], state["active_feature_names"])
        )
        if pooled:
            identity_names = [
                name
                for name in fit["coefficient_feature_names"]
                if name.startswith("instrument_identity::")
            ]
            order = [name.split("::", maxsplit=1)[1] for name in identity_names]
            positions = {instrument: index for index, instrument in enumerate(order)}
            train_identity = np.zeros((training.height, len(order)), dtype=float)
            validation_identity = np.zeros((validation.height, len(order)), dtype=float)
            for row_index, instrument in enumerate(training["instrument_id"]):
                train_identity[row_index, positions[instrument]] = 1.0
            for row_index, instrument in enumerate(validation["instrument_id"]):
                validation_identity[row_index, positions[instrument]] = 1.0
            x_train = np.column_stack((x_train, train_identity))
            x_validation = np.column_stack((x_validation, validation_identity))
            coefficient_names.extend(identity_names)
        if coefficient_names != fit["coefficient_feature_names"]:
            raise ValueError(f"LAB coefficient schema differs for retained fit {fit_id}")

        model = Ridge(
            alpha=float(fit["selected_alpha"]),
            solver="lsqr",
            tol=1e-8,
            max_iter=10_000,
            fit_intercept=not pooled,
        )
        model.fit(
            x_train,
            training["target_return"].to_numpy(),
            sample_weight=np.asarray(state["weights"], dtype=float),
        )
        retained_coefficients = np.asarray(fit["coefficients"], dtype=float)
        coefficient_delta = float(
            np.max(np.abs(np.asarray(model.coef_) - retained_coefficients))
        )
        intercept_delta = abs(float(model.intercept_) - float(fit["intercept"]))
        prediction = model.predict(x_validation)
        predictions.append(
            validation.select(
                "instrument_id",
                "decision_time",
                "target_return",
                "block",
            ).with_columns(
                pl.Series(family, prediction),
            )
        )
        fit_checks.append(
            {
                "fit_id": fit_id,
                "model_family": family,
                "target_instrument_id": fit["target_instrument_id"],
                "outer_fold_id": fit["outer_fold_id"],
                "fit_rows": training.height,
                "validation_rows": validation.height,
                "preprocessing_max_abs_delta": state_delta,
                "coefficient_max_abs_delta": coefficient_delta,
                "intercept_abs_delta": intercept_delta,
            }
        )

    local = pl.concat(
        [frame for frame in predictions if "LOCAL_RIDGE" in frame.columns],
        how="diagonal",
    ).select(
        "instrument_id",
        "decision_time",
        "target_return",
        "block",
        "LOCAL_RIDGE",
    )
    pooled = pl.concat(
        [frame for frame in predictions if "POOLED_LOCAL_RIDGE" in frame.columns],
        how="diagonal",
    ).select(
        "instrument_id",
        "decision_time",
        "POOLED_LOCAL_RIDGE",
    )
    common = local.join(
        pooled,
        on=["instrument_id", "decision_time"],
        how="inner",
        validate="1:1",
    )
    observed = {
        "support": common.height,
        "ZERO_RETURN": _instrument_balanced_mse(
            common.with_columns(pl.lit(0.0).alias("ZERO_RETURN")),
            "ZERO_RETURN",
        ),
        "POOLED_LOCAL_RIDGE": _instrument_balanced_mse(
            common,
            "POOLED_LOCAL_RIDGE",
        ),
        "LOCAL_RIDGE": _instrument_balanced_mse(common, "LOCAL_RIDGE"),
    }
    maximum_metric_delta = max(
        abs(float(observed[key]) - float(EXPECTED_BASELINE[key]))
        for key in ("ZERO_RETURN", "POOLED_LOCAL_RIDGE", "LOCAL_RIDGE")
    )
    ordering = (
        observed["ZERO_RETURN"]
        < observed["POOLED_LOCAL_RIDGE"]
        < observed["LOCAL_RIDGE"]
    )
    exact_support = observed["support"] == EXPECTED_BASELINE["support"]
    maximum_preprocessing_delta = max(
        cast(float, item["preprocessing_max_abs_delta"]) for item in fit_checks
    )
    maximum_coefficient_delta = max(
        cast(float, item["coefficient_max_abs_delta"]) for item in fit_checks
    )
    maximum_intercept_delta = max(
        cast(float, item["intercept_abs_delta"]) for item in fit_checks
    )
    if (
        not exact_support
        or maximum_metric_delta > METRIC_ABS_TOLERANCE
        or maximum_preprocessing_delta > PREPROCESSING_ABS_TOLERANCE
        or maximum_coefficient_delta > COEFFICIENT_ABS_TOLERANCE
        or maximum_intercept_delta > INTERCEPT_ABS_TOLERANCE
        or not ordering
    ):
        raise ValueError(
            "mandatory LAB-derived baseline reconstruction diverged: "
            f"observed={observed}, maximum_metric_delta={maximum_metric_delta}, "
            f"maximum_preprocessing_delta={maximum_preprocessing_delta}, "
            f"maximum_coefficient_delta={maximum_coefficient_delta}, "
            f"maximum_intercept_delta={maximum_intercept_delta}"
        )
    return {
        "contract": "qtrad-r2-historical-lab-baseline-reconstruction-v2",
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "method": "LAB_DERIVATION_OUTER_RIDGE_REFIT_AND_OOF_PREDICTION",
        "expected": EXPECTED_BASELINE,
        "observed": observed,
        "support_exact": exact_support,
        "maximum_metric_abs_delta": maximum_metric_delta,
        "ordering_zero_pooled_local": ordering,
        "fit_count": len(fit_checks),
        "maximum_preprocessing_abs_delta": maximum_preprocessing_delta,
        "maximum_coefficient_abs_delta": maximum_coefficient_delta,
        "maximum_intercept_abs_delta": maximum_intercept_delta,
        "tolerances": {
            "metric_abs": METRIC_ABS_TOLERANCE,
            "preprocessing_abs": PREPROCESSING_ABS_TOLERANCE,
            "coefficient_abs": COEFFICIENT_ABS_TOLERANCE,
            "intercept_abs": INTERCEPT_ABS_TOLERANCE,
        },
        "numerical_discrepancy_cause": (
            "Stage 7 decimal-string prices are decoded to Float64 in the LAB and the "
            "fold-local Ridge models are independently refit; sub-ulp feature/target and "
            "linear-solver differences propagate to MSE at less than 1e-14 absolute."
        ),
        "fit_checks": fit_checks,
        "retained_evaluation_report_sha256": EXPECTED_REPORT_SHA256,
    }
