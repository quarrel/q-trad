"""LAB-T pooled nonlinear tabular screen over the authenticated LAB-0 dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor  # type: ignore[reportMissingTypeStubs]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[reportMissingTypeStubs]
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]
from sklearn.neural_network import MLPRegressor  # type: ignore[reportMissingTypeStubs]

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS

WORKSTREAM = "LAB-T"
BASE_SHA = "f31cf4731fc233726f45f67f54064c40965d01d7"
MANIFEST_SHA256 = "462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072"
MANIFEST_CONTRACT = "qtrad-r2-historical-lab-manifest-v2"
DEVELOPMENT_BLOCKS = ("DEV_1", "DEV_2", "DEV_3")
TERMINAL_BLOCK = "TERMINAL_FORMER_HOLDOUT"
CORE_6 = (
    "commodity:spot-gold",
    "commodity:us-crude",
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
)
INDICATOR_FEATURES = {
    "return_60s_available",
    "return_300s_available",
    "source_active",
    "quality_healthy",
    "gap_known_by_cutoff",
}
MODEL_CONFIGURATIONS: tuple[dict[str, Any], ...] = (
    {
        "model_family": "POOLED_LOCAL_RIDGE",
        "variant": "ORIGINAL_GRID",
        "feature_set": "P0",
    },
    {
        "model_family": "HISTOGRAM_GRADIENT_BOOSTING",
        "variant": "FIXED",
        "feature_set": "P0",
        "learning_rate": 0.05,
        "max_iter": 80,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 100,
        "l2_regularization": 0.1,
    },
    {
        "model_family": "HISTOGRAM_GRADIENT_BOOSTING",
        "variant": "CONSERVATIVE_NEARBY",
        "feature_set": "P0",
        "learning_rate": 0.03,
        "max_iter": 120,
        "max_leaf_nodes": 31,
        "min_samples_leaf": 200,
        "l2_regularization": 1.0,
    },
    {
        "model_family": "POOLED_MLP",
        "variant": "WIDTH_16",
        "feature_set": "P0",
        "hidden_width": 16,
        "alpha": 0.0001,
        "max_iter": 12,
    },
    {
        "model_family": "POOLED_MLP",
        "variant": "WIDTH_32_NEARBY",
        "feature_set": "P0",
        "hidden_width": 32,
        "alpha": 0.0001,
        "max_iter": 12,
    },
)
MODEL_FAILURES = (ValueError, FloatingPointError, MemoryError)


@dataclass(frozen=True, slots=True)
class TabularConfig:
    base_sha: str
    manifest_path: Path
    manifest_sha256: str
    output_root: Path
    concentration_limit: float
    mlp_seed: int

    @classmethod
    def load(cls, path: Path) -> TabularConfig:
        value = json.loads(path.read_bytes())
        if value["evidence_label"] != LABEL or value["source_class"] != SOURCE_CLASS:
            raise ValueError("LAB-T configuration crosses the exploratory source boundary")
        if value["base_sha"] != BASE_SHA:
            raise ValueError("LAB-T configuration does not bind the authorised base SHA")
        if value["manifest_sha256"] != MANIFEST_SHA256:
            raise ValueError("LAB-T configuration does not bind the canonical LAB-0 manifest")
        concentration_limit = float(value["concentration_limit"])
        if not 0 < concentration_limit < 1:
            raise ValueError("concentration_limit must lie strictly between zero and one")
        return cls(
            base_sha=str(value["base_sha"]),
            manifest_path=Path(value["manifest_path"]),
            manifest_sha256=str(value["manifest_sha256"]),
            output_root=Path(value["output_root"]),
            concentration_limit=concentration_limit,
            mlp_seed=int(value["mlp_seed"]),
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configuration_id(configuration: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(configuration)).hexdigest()


def authenticate_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    if _sha256(path) != expected_sha256:
        raise ValueError("lab manifest SHA-256 differs from the selected LAB-0 input")
    manifest = json.loads(path.read_bytes())
    if not isinstance(manifest, dict):
        raise ValueError("lab manifest must be a JSON object")
    if manifest["contract"] != MANIFEST_CONTRACT:
        raise ValueError("unsupported lab manifest contract")
    if manifest["evidence_label"] != LABEL or manifest["source_class"] != SOURCE_CLASS:
        raise ValueError("lab manifest crosses the exploratory source boundary")
    if manifest["status"] != "COMPLETE" or manifest["authoritative"] is not False:
        raise ValueError("lab manifest is not a complete non-authoritative LAB build")
    blocks = {item["name"]: item for item in manifest["fold_blocks"]}
    if set(blocks) != {*DEVELOPMENT_BLOCKS, TERMINAL_BLOCK}:
        raise ValueError("lab manifest does not bind the required chronological blocks")
    if blocks[TERMINAL_BLOCK]["selection_prohibited"] is not True:
        raise ValueError("former holdout is not selection-prohibited")
    observed = manifest["baseline_reconstruction"]["observed"]
    expected = {
        "support": 239_535,
        "ZERO_RETURN": 0.0000028404586671320294,
        "POOLED_LOCAL_RIDGE": 0.000002841663414474555,
        "LOCAL_RIDGE": 0.0000028481068080631273,
    }
    if observed["support"] != expected["support"]:
        raise ValueError("LAB-0 baseline support differs from the authorised reconstruction")
    if (
        max(abs(float(observed[key]) - expected[key]) for key in expected if key != "support")
        > 1e-14
    ):
        raise ValueError("LAB-0 baseline metrics exceed the authorised numerical tolerance")
    if not observed["ZERO_RETURN"] < observed["POOLED_LOCAL_RIDGE"] < observed["LOCAL_RIDGE"]:
        raise ValueError("LAB-0 baseline ordering differs from ZERO < POOLED < LOCAL")
    return manifest


def _authorise_terminal(
    freeze_path: Path,
    expected_sha256: str,
    manifest_sha256: str,
    configuration_id_value: str,
) -> None:
    if _sha256(freeze_path) != expected_sha256:
        raise ValueError("finalist freeze SHA-256 differs")
    freeze = json.loads(freeze_path.read_bytes())
    if (
        freeze["contract"] != "qtrad-r2-historical-lab-finalist-freeze-v1"
        or freeze["evidence_label"] != LABEL
        or freeze["manifest_sha256"] != manifest_sha256
        or configuration_id_value not in freeze["finalist_configuration_ids"]
    ):
        raise ValueError("configuration is not authorised by the finalist freeze")


def _selected_parts(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    kind: str,
    instruments: Sequence[str],
    terminal_authority: tuple[Path, str, str] | None,
) -> list[str]:
    if kind not in {"feature", "target"}:
        raise ValueError(f"unsupported LAB-T part kind: {kind}")
    if terminal_authority is not None:
        _authorise_terminal(
            terminal_authority[0],
            terminal_authority[1],
            MANIFEST_SHA256,
            terminal_authority[2],
        )
    selected = set(instruments)
    unknown = selected - set(manifest["instruments"])
    if unknown:
        raise ValueError(f"unknown LAB-T instruments: {sorted(unknown)}")
    references = [
        item
        for item in manifest["parts"]
        if item["kind"] == kind
        and item["instrument_id"] in selected
        and (kind != "target" or item["horizon_minutes"] == 15)
    ]
    if not references:
        raise ValueError("selected LAB-T part set is empty")
    root = manifest_path.parent.resolve()
    paths: list[str] = []
    for reference in references:
        part = Path(reference["path"]).resolve()
        if root not in part.parents:
            raise ValueError(f"LAB-T part escapes the authenticated LAB-0 root: {part}")
        if _sha256(part) != reference["sha256"]:
            raise ValueError(f"LAB-T part SHA-256 differs: {part}")
        paths.append(str(part))
    return paths


def _block_expression(manifest: dict[str, Any], blocks: Sequence[str]) -> pl.Expr:
    named = {item["name"]: item for item in manifest["fold_blocks"]}
    requested = set(blocks)
    if not requested.issubset({*named, "TRAINING_ONLY"}):
        raise ValueError(f"unknown LAB-T block selection: {sorted(requested)}")
    expressions: dict[str, pl.Expr] = {}
    any_named = pl.lit(False)
    for name, block in named.items():
        expression = (pl.col("decision_time") >= datetime.fromisoformat(block["start"])) & (
            pl.col("decision_time") < datetime.fromisoformat(block["end"])
        )
        expressions[name] = expression
        any_named = any_named | expression
    expressions["TRAINING_ONLY"] = ~any_named
    selected = pl.lit(False)
    for name in requested:
        selected = selected | expressions[name]
    return selected


def load_joined(
    config: TabularConfig,
    manifest: dict[str, Any],
    instruments: Sequence[str],
    *,
    blocks: Sequence[str],
    terminal_authority: tuple[Path, str, str] | None = None,
) -> pl.DataFrame:
    if TERMINAL_BLOCK in blocks and terminal_authority is None:
        raise ValueError("terminal loading requires an authenticated finalist freeze")
    feature_paths = _selected_parts(
        config.manifest_path,
        manifest,
        kind="feature",
        instruments=instruments,
        terminal_authority=terminal_authority,
    )
    target_paths = _selected_parts(
        config.manifest_path,
        manifest,
        kind="target",
        instruments=instruments,
        terminal_authority=terminal_authority,
    )
    requested = set(blocks)
    features = pl.scan_parquet(feature_paths).filter(_block_expression(manifest, blocks))
    targets = pl.scan_parquet(target_paths).filter(pl.col("block").is_in(sorted(requested)))
    return (
        targets.join(
            features,
            on=["instrument_id", "decision_time"],
            how="left",
            validate="m:1",
        )
        .sort("decision_time", "instrument_id")
        .collect()
    )


def _instrument_weights(frame: pl.DataFrame) -> np.ndarray:
    instruments = [str(value) for value in frame["instrument_id"]]
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
    return float(ordered_values[np.searchsorted(np.cumsum(ordered_weights), cutoff, side="left")])


def _fit_preprocessing(
    matrix: np.ndarray,
    feature_names: Sequence[str],
    weights: np.ndarray,
) -> tuple[np.ndarray, dict[str, object]]:
    normalised_weights = weights * (len(weights) / float(weights.sum()))
    medians: list[float | None] = []
    means: list[float | None] = []
    scales: list[float | None] = []
    active: list[str] = []
    transformed: list[np.ndarray] = []
    for position, name in enumerate(feature_names):
        column = matrix[:, position]
        if name in INDICATOR_FEATURES:
            values = np.where(np.isnan(column), 0.0, column)
            medians.append(None)
            means.append(None)
            scales.append(None)
            if np.ptp(values) > 0:
                active.append(name)
                transformed.append(values)
            continue
        observed = ~np.isnan(column)
        if not observed.any():
            medians.append(None)
            means.append(None)
            scales.append(None)
            continue
        median = _weighted_median(column[observed], normalised_weights[observed])
        filled = np.where(np.isnan(column), median, column)
        mean = float(np.average(filled, weights=normalised_weights))
        variance = float(np.average((filled - mean) ** 2, weights=normalised_weights))
        medians.append(median)
        if variance <= 0:
            means.append(None)
            scales.append(None)
        else:
            scale = float(np.sqrt(variance))
            means.append(mean)
            scales.append(scale)
            active.append(name)
            transformed.append((filled - mean) / scale)
    transformed_matrix = (
        np.column_stack(transformed).astype(np.float32, copy=False)
        if transformed
        else np.empty((matrix.shape[0], 0), dtype=np.float32)
    )
    return transformed_matrix, {
        "feature_names": list(feature_names),
        "active_feature_names": active,
        "medians": medians,
        "means": means,
        "scales": scales,
    }


def _transform(matrix: np.ndarray, state: dict[str, object]) -> np.ndarray:
    feature_names = cast(list[str], state["feature_names"])
    active = set(cast(list[str], state["active_feature_names"]))
    medians = cast(list[float | None], state["medians"])
    means = cast(list[float | None], state["means"])
    scales = cast(list[float | None], state["scales"])
    columns: list[np.ndarray] = []
    for position, name in enumerate(feature_names):
        if name not in active:
            continue
        column = matrix[:, position]
        if name in INDICATOR_FEATURES:
            columns.append(np.where(np.isnan(column), 0.0, column))
            continue
        median = medians[position]
        mean = means[position]
        scale = scales[position]
        if median is None or mean is None or scale is None:
            raise ValueError("active continuous LAB-T feature has incomplete preprocessing")
        columns.append((np.where(np.isnan(column), median, column) - mean) / scale)
    return np.column_stack(columns).astype(np.float32, copy=False)


def _identity(frame: pl.DataFrame, instruments: Sequence[str]) -> np.ndarray:
    positions = {instrument: position for position, instrument in enumerate(instruments)}
    matrix = np.zeros((frame.height, len(instruments)), dtype=np.float32)
    for row_index, instrument in enumerate(frame["instrument_id"]):
        matrix[row_index, positions[str(instrument)]] = 1.0
    return matrix


def _training_rows(rows: pl.DataFrame, validation_start: datetime) -> pl.DataFrame:
    return rows.filter(
        pl.col("target_valid")
        & pl.col("target_return").is_not_null()
        & pl.col("target_available_at").is_not_null()
        & (pl.col("target_available_at") <= validation_start)
        & (pl.col("decision_time") < validation_start)
        & pl.col("feature_available_at").is_not_null()
        & (pl.col("feature_available_at") <= pl.col("decision_time"))
    ).sort("decision_time", "instrument_id", "target_id")


def _select_ridge_alpha(
    training: pl.DataFrame,
    feature_names: Sequence[str],
    instruments: Sequence[str],
) -> float:
    decision_times = training["decision_time"].unique().sort()
    split_position = max(1, int(len(decision_times) * 0.9))
    if split_position >= len(decision_times):
        raise ValueError("insufficient chronological support for pooled Ridge selection")
    validation_start = cast(datetime, decision_times[split_position])
    inner_fit = _training_rows(training, validation_start)
    inner_validation = training.filter(pl.col("decision_time") >= validation_start)
    if inner_fit.height < 100 or inner_validation.height < 20:
        raise ValueError("insufficient chronological inner-selection support")
    weights = _instrument_weights(inner_fit)
    x_fit, state = _fit_preprocessing(
        inner_fit.select(feature_names).to_numpy().astype(float),
        feature_names,
        weights,
    )
    x_validation = _transform(
        inner_validation.select(feature_names).to_numpy().astype(float),
        state,
    )
    x_fit = np.column_stack((x_fit, _identity(inner_fit, instruments)))
    x_validation = np.column_stack((x_validation, _identity(inner_validation, instruments)))
    scores: list[tuple[float, float]] = []
    for alpha in (0.01, 0.1, 1.0, 10.0):
        model = Ridge(alpha=alpha, solver="lsqr", tol=1e-8, max_iter=10_000, fit_intercept=False)
        model.fit(x_fit, inner_fit["target_return"].to_numpy(), sample_weight=weights)
        scores.append(
            (
                float(
                    np.average(
                        (model.predict(x_validation) - inner_validation["target_return"].to_numpy())
                        ** 2,
                        weights=_instrument_weights(inner_validation),
                    )
                ),
                alpha,
            )
        )
    return min(scores)[1]


def _make_model(configuration: dict[str, Any], seed: int, ridge_alpha: float | None) -> Any:
    family = configuration["model_family"]
    if family == "POOLED_LOCAL_RIDGE":
        if ridge_alpha is None:
            raise ValueError("pooled Ridge requires a fold-local selected alpha")
        return Ridge(
            alpha=ridge_alpha,
            solver="lsqr",
            tol=1e-8,
            max_iter=10_000,
            fit_intercept=False,
        )
    if family == "HISTOGRAM_GRADIENT_BOOSTING":
        return HistGradientBoostingRegressor(
            learning_rate=float(configuration["learning_rate"]),
            max_iter=int(configuration["max_iter"]),
            max_leaf_nodes=int(configuration["max_leaf_nodes"]),
            min_samples_leaf=int(configuration["min_samples_leaf"]),
            l2_regularization=float(configuration["l2_regularization"]),
            early_stopping=False,  # type: ignore[arg-type]
            random_state=seed,
        )
    if family == "POOLED_MLP":
        return MLPRegressor(
            hidden_layer_sizes=(int(configuration["hidden_width"]),),
            activation="relu",
            solver="adam",
            alpha=float(configuration["alpha"]),
            batch_size=4096,  # type: ignore[arg-type]
            learning_rate_init=0.001,
            max_iter=int(configuration["max_iter"]),
            shuffle=False,
            random_state=seed,
            early_stopping=False,  # type: ignore[arg-type]
        )
    raise ValueError(f"unknown LAB-T model family: {family}")


def _fit_predict(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    feature_names: Sequence[str],
    instruments: Sequence[str],
    configuration: dict[str, object],
    seed: int,
) -> tuple[pl.DataFrame, list[dict[str, object]]]:
    weights = _instrument_weights(training)
    x_fit, state = _fit_preprocessing(
        training.select(feature_names).to_numpy().astype(float),
        feature_names,
        weights,
    )
    x_validation = _transform(
        validation.select(feature_names).to_numpy().astype(float),
        state,
    )
    x_fit = np.column_stack((x_fit, _identity(training, instruments)))
    x_validation = np.column_stack((x_validation, _identity(validation, instruments)))
    ridge_alpha = (
        _select_ridge_alpha(training, feature_names, instruments)
        if configuration["model_family"] == "POOLED_LOCAL_RIDGE"
        else None
    )
    model = _make_model(configuration, seed, ridge_alpha)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=ConvergenceWarning)
        model.fit(x_fit, training["target_return"].to_numpy(), sample_weight=weights)
    predicted = np.asarray(model.predict(x_validation), dtype=float)
    predictions = validation.select(
        "instrument_id",
        "decision_time",
        "horizon_minutes",
    ).with_columns(pl.Series("expected_return", predicted))
    importance: list[dict[str, object]] = []
    if configuration["model_family"] == "HISTOGRAM_GRADIENT_BOOSTING":
        target_values = validation["target_return"].to_numpy()
        valid_positions = np.flatnonzero(
            validation["target_valid"].to_numpy() & np.isfinite(target_values)
        )
        sample_size = min(2048, len(valid_positions))
        if sample_size:
            sampled_valid_positions = np.linspace(
                0, len(valid_positions) - 1, sample_size, dtype=int
            )
            sample_positions = valid_positions[sampled_valid_positions]
            sample_x = x_validation[sample_positions].copy()
            sample_y = target_values[sample_positions]
            baseline_loss = float(np.mean((predicted[sample_positions] - sample_y) ** 2))
            rng = np.random.default_rng(seed)
            active_features = cast(list[str], state["active_feature_names"])
            for position, feature_name in enumerate(active_features):
                permuted = sample_x.copy()
                permuted[:, position] = permuted[rng.permutation(sample_size), position]
                loss = float(np.mean((model.predict(permuted) - sample_y) ** 2))
                importance.append(
                    {
                        "feature_name": feature_name,
                        "permutation_delta_mse": loss - baseline_loss,
                    }
                )
    return predictions, importance


def evaluate(
    predictions: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    model_name: str,
) -> dict[str, object]:
    keys = ["instrument_id", "decision_time", "horizon_minutes"]
    valid = targets.filter(pl.col("target_valid"))
    joined = valid.join(predictions, on=keys, how="inner", validate="1:1")
    if joined.is_empty():
        raise ValueError("candidate has no valid LAB-T forecast support")
    scored = joined.with_columns(
        (pl.col("target_return") ** 2).alias("_zero_se"),
        ((pl.col("expected_return") - pl.col("target_return")) ** 2).alias("_model_se"),
        (
            pl.col("target_return") ** 2
            - (pl.col("expected_return") - pl.col("target_return")) ** 2
        ).alias("_improvement"),
    )
    instrument = scored.group_by("instrument_id").agg(
        pl.col("_zero_se").mean().alias("zero_mse"),
        pl.col("_model_se").mean().alias("model_mse"),
        pl.col("_improvement").mean().alias("contribution"),
    )
    block_instrument = scored.group_by("block", "instrument_id").agg(
        pl.col("_zero_se").mean().alias("zero_mse"),
        pl.col("_model_se").mean().alias("model_mse"),
        pl.col("_improvement").mean().alias("contribution"),
    )
    blocks = block_instrument.group_by("block").agg(
        pl.col("zero_mse").mean(),
        pl.col("model_mse").mean(),
        pl.col("contribution").mean(),
    )
    zero_mse = float(cast(float, instrument["zero_mse"].mean()))
    model_mse = float(cast(float, instrument["model_mse"].mean()))
    calibration = scored.select(
        pl.cov("expected_return", "target_return").alias("covariance"),
        pl.col("expected_return").var(ddof=0).alias("forecast_variance"),
        pl.corr("expected_return", "target_return", method="spearman").alias("spearman"),
    ).row(0, named=True)
    variance = calibration["forecast_variance"]
    return {
        "model_name": model_name,
        "support": joined.height,
        "forecast_coverage": joined.height / valid.height,
        "zero_mse": zero_mse,
        "model_mse": model_mse,
        "direct_delta_mse_versus_zero": model_mse - zero_mse,
        "skill_versus_zero": 1.0 - model_mse / zero_mse,
        "positive_block_count": blocks.filter(pl.col("model_mse") < pl.col("zero_mse")).height,
        "block_count": blocks.height,
        "positive_instrument_count": instrument.filter(
            pl.col("model_mse") < pl.col("zero_mse")
        ).height,
        "instrument_count": instrument.height,
        "calibration_slope": (
            float(calibration["covariance"]) / float(variance)
            if variance is not None and variance > 0
            else None
        ),
        "spearman_correlation": (
            float(calibration["spearman"]) if calibration["spearman"] is not None else None
        ),
        "best_instrument_contribution": _positive_contribution_share(
            instrument["contribution"].to_list()
        ),
        "best_period_contribution": _positive_contribution_share(blocks["contribution"].to_list()),
    }


def _positive_contribution_share(values: Sequence[float]) -> float:
    positives = [value for value in values if value > 0]
    return max(positives) / sum(positives) if positives else 0.0


def _zero_predictions(targets: pl.DataFrame) -> pl.DataFrame:
    return targets.select(
        "instrument_id",
        "decision_time",
        "horizon_minutes",
    ).with_columns(pl.lit(0.0).alias("expected_return"))


def _append_attempt(
    path: Path,
    *,
    configuration: dict[str, object],
    universe: str,
    split: str,
    result: dict[str, object],
) -> None:
    entry = {
        "workstream": WORKSTREAM,
        "configuration_id": configuration_id(configuration),
        "configuration": configuration,
        "universe": universe,
        "split": split,
        "result": result,
        "manifest_sha256": MANIFEST_SHA256,
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(_canonical_json(entry))


def _result_row(
    configuration: dict[str, object],
    universe: str,
    split: str,
    result: dict[str, object],
) -> dict[str, object]:
    return {
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "configuration_id": configuration_id(configuration),
        "model_family": configuration["model_family"],
        "variant": configuration["variant"],
        "feature_set": configuration["feature_set"],
        "universe": universe,
        "split": split,
        **result,
    }


def _predictions_for_configuration(
    rows: pl.DataFrame,
    manifest: dict[str, Any],
    instruments: Sequence[str],
    configuration: dict[str, object],
    *,
    terminal: bool,
    seed: int,
    smoke: bool,
) -> tuple[pl.DataFrame, list[dict[str, object]]]:
    feature_names = tuple(manifest["feature_schema"]["feature_sets"][configuration["feature_set"]])
    block_starts = {
        str(item["name"]): datetime.fromisoformat(str(item["start"]))
        for item in manifest["fold_blocks"]
    }
    blocks = (TERMINAL_BLOCK,) if terminal else DEVELOPMENT_BLOCKS
    frames: list[pl.DataFrame] = []
    importances: list[dict[str, object]] = []
    for block in blocks:
        validation = rows.filter(pl.col("block") == block)
        if smoke:
            validation = validation.head(512)
        training = _training_rows(rows, block_starts[block])
        if smoke:
            training = training.tail(5000)
        if training.height < 100 or validation.is_empty():
            raise ValueError(f"insufficient LAB-T support for {block}")
        predicted, fold_importance = _fit_predict(
            training,
            validation,
            feature_names,
            instruments,
            configuration,
            seed,
        )
        frames.append(predicted)
        importances.extend(
            {
                **item,
                "block": block,
            }
            for item in fold_importance
        )
    return pl.concat(frames).sort("decision_time", "instrument_id"), importances


def _qualifies(result: dict[str, Any], concentration_limit: float) -> bool:
    return (
        float(result["skill_versus_zero"]) > 0
        and int(result["positive_block_count"]) > 1
        and int(result["positive_instrument_count"]) > 1
        and float(result["best_instrument_contribution"]) <= concentration_limit
        and float(result["best_period_contribution"]) <= concentration_limit
    )


def select_finalists(
    result_rows: Sequence[dict[str, Any]],
    configurations: Sequence[dict[str, Any]],
    concentration_limit: float,
) -> list[dict[str, Any]]:
    by_id = {configuration_id(item): item for item in configurations}
    finalists: list[dict[str, Any]] = []
    for family in ("HISTOGRAM_GRADIENT_BOOSTING", "POOLED_MLP"):
        candidates: list[tuple[float, str]] = []
        for identifier, configuration in by_id.items():
            if configuration["model_family"] != family:
                continue
            successful = [
                row
                for row in result_rows
                if row["configuration_id"] == identifier
                and row["split"] == "PRE_HOLDOUT"
                and row.get("attempt_status") == "SUCCEEDED"
            ]
            if successful and any(_qualifies(row, concentration_limit) for row in successful):
                candidates.append(
                    (
                        sum(float(row["skill_versus_zero"]) for row in successful),
                        identifier,
                    )
                )
        if candidates:
            finalists.append(by_id[max(candidates)[1]])
    return finalists


def select_secondary_p1_configurations(
    result_rows: Sequence[dict[str, Any]],
    configurations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for family in ("HISTOGRAM_GRADIENT_BOOSTING", "POOLED_MLP"):
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        for configuration in configurations:
            if configuration["model_family"] != family or configuration["feature_set"] != "P0":
                continue
            identifier = configuration_id(configuration)
            successful = [
                row
                for row in result_rows
                if row["configuration_id"] == identifier
                and row["split"] == "PRE_HOLDOUT"
                and row.get("attempt_status") == "SUCCEEDED"
            ]
            if successful:
                candidates.append(
                    (
                        sum(float(row["skill_versus_zero"]) for row in successful),
                        identifier,
                        configuration,
                    )
                )
        if candidates:
            selected.append(max(candidates, key=lambda item: (item[0], item[1]))[2])
    return selected


def _freeze(
    path: Path,
    finalist_configurations: Sequence[dict[str, object]],
    comparator_configuration: dict[str, object],
) -> dict[str, object]:
    if path.exists():
        raise FileExistsError(f"LAB-T finalist freeze is create-only: {path}")
    authorised = (
        [comparator_configuration, *finalist_configurations] if finalist_configurations else []
    )
    value = {
        "contract": "qtrad-r2-historical-lab-finalist-freeze-v1",
        "workstream": WORKSTREAM,
        "manifest_sha256": MANIFEST_SHA256,
        "finalist_configuration_ids": [
            configuration_id(configuration) for configuration in authorised
        ],
        "nonlinear_finalist_configuration_ids": [
            configuration_id(configuration) for configuration in finalist_configurations
        ],
        "evidence_label": LABEL,
        "terminal_evaluation_is_post_hoc_only": True,
        "selection_rule": {
            "positive_aggregate_skill_versus_zero": True,
            "minimum_positive_blocks": 2,
            "minimum_positive_instruments": 2,
            "maximum_best_instrument_contribution": 0.8,
            "maximum_best_period_contribution": 0.8,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json(value))
    return value


def _write_result(
    path: Path,
    results: pl.DataFrame,
    finalists: Sequence[dict[str, object]],
    *,
    smoke: bool,
) -> None:
    lines = [
        "# LAB-T result",
        "",
        f"**Evidence label:** {LABEL}",
        f"**Source class:** {SOURCE_CLASS}",
        "",
        "This is post-hoc hypothesis generation, not a second holdout, confirmation, promotion,",
        "or decision-grade conclusion.",
        "",
        "## Configurations attempted",
        "",
        (
            f"{results.filter(pl.col('split') == 'PRE_HOLDOUT').height} aggregate "
            "pre-holdout result rows."
        ),
        "",
        "## Pre-holdout results",
        "",
        "| Universe | Model | Variant | Features | Skill vs zero | Positive blocks | "
        "Positive instruments | Best instrument share | Best period share | Coverage |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    pre = results.filter(pl.col("split") == "PRE_HOLDOUT")
    for row in pre.sort("universe", "model_family", "variant", "feature_set").iter_rows(named=True):
        lines.append(
            f"| {row['universe']} | {row['model_family']} | {row['variant']} | "
            f"{row['feature_set']} | {_metric(row.get('skill_versus_zero'))} | "
            f"{_metric(row.get('positive_block_count'))} | "
            f"{_metric(row.get('positive_instrument_count'))} | "
            f"{_metric(row.get('best_instrument_contribution'))} | "
            f"{_metric(row.get('best_period_contribution'))} | "
            f"{_metric(row.get('forecast_coverage'))} |"
        )
    lines.extend(("", "## Frozen nonlinear finalists", ""))
    if finalists:
        for item in finalists:
            lines.append(
                f"- {item['model_family']} / {item['variant']} / {item['feature_set']} "
                f"({configuration_id(item)})"
            )
    else:
        lines.append("None: no nonlinear P0 configuration met every advancement criterion.")
    lines.extend(("", "## Former-holdout finalist results", ""))
    terminal = results.filter(pl.col("split") == "FORMER_HOLDOUT_POST_HOC")
    if terminal.is_empty():
        lines.append(
            "Not accessed in the smoke run."
            if smoke
            else "Not accessed because no nonlinear configuration qualified pre-holdout."
        )
    else:
        lines.extend(
            (
                "| Universe | Model | Variant | Skill vs zero | Positive instruments | "
                "Best instrument share | Coverage |",
                "|---|---|---|---:|---:|---:|---:|",
            )
        )
        for row in terminal.sort("universe", "model_family", "variant").iter_rows(named=True):
            lines.append(
                f"| {row['universe']} | {row['model_family']} | {row['variant']} | "
                f"{_metric(row.get('skill_versus_zero'))} | "
                f"{_metric(row.get('positive_instrument_count'))} | "
                f"{_metric(row.get('best_instrument_contribution'))} | "
                f"{_metric(row.get('forecast_coverage'))} |"
            )
    lines.extend(
        (
            "",
            "## Interpretation boundary",
            "",
            "Only patterns that passed the stated pre-holdout screen were eligible for one "
            "post-hoc terminal evaluation. Any surviving pattern is a hypothesis for a future "
            "untouched native or future-data experiment.",
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _metric(value: object) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.9g}"
    return str(value)


def _run_configuration(
    *,
    rows: pl.DataFrame,
    targets: pl.DataFrame,
    manifest: dict[str, Any],
    instruments: Sequence[str],
    universe_name: str,
    configuration: dict[str, object],
    split: str,
    seed: int,
    smoke: bool,
    register: Path,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    try:
        predictions, importance = _predictions_for_configuration(
            rows,
            manifest,
            instruments,
            configuration,
            terminal=split == "FORMER_HOLDOUT_POST_HOC",
            seed=seed,
            smoke=smoke,
        )
        evaluation = evaluate(
            predictions,
            targets,
            model_name=f"{configuration['model_family']}::{configuration['variant']}",
        )
        row = {
            **_result_row(configuration, universe_name, split, evaluation),
            "attempt_status": "SUCCEEDED",
            "failure_type": None,
            "failure_message": None,
        }
        attempt_result: dict[str, object] = evaluation
    except MODEL_FAILURES as error:
        row = {
            "evidence_label": LABEL,
            "source_class": SOURCE_CLASS,
            "configuration_id": configuration_id(configuration),
            "model_family": configuration["model_family"],
            "variant": configuration["variant"],
            "feature_set": configuration["feature_set"],
            "universe": universe_name,
            "split": split,
            "attempt_status": "FAILED",
            "failure_type": type(error).__name__,
            "failure_message": str(error),
        }
        importance = []
        attempt_result = {
            "status": "FAILED",
            "failure_type": type(error).__name__,
            "failure_message": str(error),
        }
    _append_attempt(
        register,
        configuration=configuration,
        universe=universe_name,
        split=split,
        result=attempt_result,
    )
    return row, importance


def run(config: TabularConfig, *, smoke: bool = False) -> dict[str, object]:
    output_root = (
        config.output_root.with_name(f"{config.output_root.name}-smoke")
        if smoke
        else config.output_root
    )
    if output_root.exists():
        raise FileExistsError(f"LAB-T output is create-only: {output_root}")
    manifest = authenticate_manifest(config.manifest_path, config.manifest_sha256)
    all_20 = tuple(str(item) for item in manifest["instruments"])
    universes: tuple[tuple[str, tuple[str, ...]], ...] = (
        (("CORE_6_SMOKE" if smoke else "CORE_6"), (CORE_6[:2] if smoke else CORE_6)),
        *(() if smoke else (("ALL_20", all_20),)),
    )
    register = output_root / "run-register.jsonl"
    results: list[dict[str, object]] = []
    importance_rows: list[dict[str, object]] = []
    development_rows: dict[str, pl.DataFrame] = {}
    configurations = MODEL_CONFIGURATIONS[:3:2] if smoke else MODEL_CONFIGURATIONS

    for universe_name, instruments in universes:
        rows = load_joined(
            config,
            manifest,
            instruments,
            blocks=(*DEVELOPMENT_BLOCKS, "TRAINING_ONLY"),
        )
        development_rows[universe_name] = rows
        targets = rows.filter(pl.col("block").is_in(DEVELOPMENT_BLOCKS))
        zero_configuration: dict[str, object] = {
            "model_family": "ZERO_RETURN",
            "variant": "DIRECT",
            "feature_set": "P0",
        }
        zero_evaluation = evaluate(_zero_predictions(targets), targets, model_name="ZERO_RETURN")
        zero_row = {
            **_result_row(
                zero_configuration,
                universe_name,
                "PRE_HOLDOUT",
                zero_evaluation,
            ),
            "attempt_status": "SUCCEEDED",
            "failure_type": None,
            "failure_message": None,
        }
        results.append(zero_row)
        _append_attempt(
            register,
            configuration=zero_configuration,
            universe=universe_name,
            split="PRE_HOLDOUT",
            result=zero_evaluation,
        )
        for configuration in configurations:
            row, fold_importance = _run_configuration(
                rows=rows,
                targets=targets,
                manifest=manifest,
                instruments=instruments,
                universe_name=universe_name,
                configuration=configuration,
                split="PRE_HOLDOUT",
                seed=config.mlp_seed,
                smoke=smoke,
                register=register,
            )
            results.append(row)
            importance_rows.extend(
                {
                    "evidence_label": LABEL,
                    "source_class": SOURCE_CLASS,
                    "configuration_id": configuration_id(configuration),
                    "model_family": configuration["model_family"],
                    "variant": configuration["variant"],
                    "feature_set": configuration["feature_set"],
                    "universe": universe_name,
                    **item,
                }
                for item in fold_importance
            )

    secondary_configurations: list[dict[str, Any]] = []
    if not smoke:
        for selected_p0 in select_secondary_p1_configurations(results, MODEL_CONFIGURATIONS):
            p1_configuration = {
                **selected_p0,
                "feature_set": "P1",
                "variant": f"{selected_p0['variant']}_P1",
            }
            secondary_configurations.append(p1_configuration)
            for universe_name, instruments in universes:
                rows = development_rows[universe_name]
                targets = rows.filter(pl.col("block").is_in(DEVELOPMENT_BLOCKS))
                row, fold_importance = _run_configuration(
                    rows=rows,
                    targets=targets,
                    manifest=manifest,
                    instruments=instruments,
                    universe_name=universe_name,
                    configuration=p1_configuration,
                    split="PRE_HOLDOUT",
                    seed=config.mlp_seed,
                    smoke=False,
                    register=register,
                )
                results.append(row)
                importance_rows.extend(
                    {
                        "evidence_label": LABEL,
                        "source_class": SOURCE_CLASS,
                        "configuration_id": configuration_id(p1_configuration),
                        "model_family": p1_configuration["model_family"],
                        "variant": p1_configuration["variant"],
                        "feature_set": "P1",
                        "universe": universe_name,
                        **item,
                    }
                    for item in fold_importance
                )
    finalists = (
        []
        if smoke
        else select_finalists(
            results,
            [*MODEL_CONFIGURATIONS, *secondary_configurations],
            config.concentration_limit,
        )
    )

    ridge_configuration = MODEL_CONFIGURATIONS[0]
    freeze_path = output_root / "finalists.json"
    freeze = _freeze(freeze_path, finalists, ridge_configuration)
    freeze_sha = _sha256(freeze_path)

    if finalists and not smoke:
        terminal_configurations = (ridge_configuration, *finalists)
        authority_id = cast(list[str], freeze["finalist_configuration_ids"])[0]
        authority = (freeze_path, freeze_sha, authority_id)
        block_starts = {
            str(item["name"]): datetime.fromisoformat(str(item["start"]))
            for item in manifest["fold_blocks"]
        }
        for universe_name, instruments in universes:
            terminal_rows = load_joined(
                config,
                manifest,
                instruments,
                blocks=(*DEVELOPMENT_BLOCKS, "TRAINING_ONLY", TERMINAL_BLOCK),
                terminal_authority=authority,
            )
            terminal_targets = terminal_rows.filter(pl.col("block") == TERMINAL_BLOCK)
            zero_configuration = {
                "model_family": "ZERO_RETURN",
                "variant": "DIRECT",
                "feature_set": "P0",
            }
            zero_evaluation = evaluate(
                _zero_predictions(terminal_targets),
                terminal_targets,
                model_name="ZERO_RETURN",
            )
            results.append(
                {
                    **_result_row(
                        zero_configuration,
                        universe_name,
                        "FORMER_HOLDOUT_POST_HOC",
                        zero_evaluation,
                    ),
                    "attempt_status": "SUCCEEDED",
                    "failure_type": None,
                    "failure_message": None,
                }
            )
            _append_attempt(
                register,
                configuration=zero_configuration,
                universe=universe_name,
                split="FORMER_HOLDOUT_POST_HOC",
                result=zero_evaluation,
            )
            if block_starts[TERMINAL_BLOCK] is None:
                raise AssertionError("terminal block start must be present")
            for configuration in terminal_configurations:
                row, _ = _run_configuration(
                    rows=terminal_rows,
                    targets=terminal_targets,
                    manifest=manifest,
                    instruments=instruments,
                    universe_name=universe_name,
                    configuration=configuration,
                    split="FORMER_HOLDOUT_POST_HOC",
                    seed=config.mlp_seed,
                    smoke=False,
                    register=register,
                )
                results.append(row)

    output_root.mkdir(parents=True, exist_ok=True)
    result_frame = pl.DataFrame(results, infer_schema_length=None)
    result_frame.write_parquet(output_root / "model-results.parquet")
    importance_frame = (
        pl.DataFrame(importance_rows, infer_schema_length=None)
        if importance_rows
        else pl.DataFrame(
            schema={
                "evidence_label": pl.String,
                "source_class": pl.String,
                "configuration_id": pl.String,
                "model_family": pl.String,
                "variant": pl.String,
                "feature_set": pl.String,
                "universe": pl.String,
                "block": pl.String,
                "feature_name": pl.String,
                "permutation_delta_mse": pl.Float64,
            }
        )
    )
    importance_frame.write_parquet(output_root / "feature-importance.parquet")
    _write_result(output_root / "result.md", result_frame, finalists, smoke=smoke)
    summary = {
        "status": "COMPLETE",
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "base_sha": config.base_sha,
        "manifest_path": str(config.manifest_path),
        "manifest_sha256": config.manifest_sha256,
        "configurations_attempted": len(
            {
                str(row["configuration_id"])
                for row in results
                if row["model_family"] not in {"ZERO_RETURN"}
            }
        ),
        "attempt_rows": len(results),
        "nonlinear_finalist_configuration_ids": [
            configuration_id(configuration) for configuration in finalists
        ],
        "finalist_freeze_sha256": freeze_sha,
        "terminal_accessed": bool(finalists) and not smoke,
        "smoke": smoke,
    }
    (output_root / "run-summary.json").write_bytes(_canonical_json(summary))
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("smoke", "run"))
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            run(TabularConfig.load(arguments.config), smoke=arguments.command == "smoke"),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
