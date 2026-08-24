"""Controlled temporal-representation experiment over the authenticated LAB-0 dataset."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.harness import (
    TERMINAL_BLOCK,
    append_attempt,
    authenticate_manifest,
    configuration_id,
    evaluate_against_zero,
    freeze_finalists,
    load_parts,
)

WORKSTREAM = "LAB-L"
DEVELOPMENT_BLOCKS = ("DEV_1", "DEV_2", "DEV_3")
TRAINING_BLOCKS = {
    "DEV_1": ("TRAINING_ONLY",),
    "DEV_2": ("TRAINING_ONLY", "DEV_1"),
    "DEV_3": ("TRAINING_ONLY", "DEV_1", "DEV_2"),
    TERMINAL_BLOCK: ("TRAINING_ONLY", "DEV_1", "DEV_2", "DEV_3"),
}
METADATA_COLUMNS = {
    "instrument_id",
    "decision_time",
    "latest_feature_bar_end",
    "feature_data_asof",
    "feature_available_at",
    "source_class",
    "evidence_label",
}
SEQUENCE_COLUMNS = (
    "return_60s",
    "mean_log_range_60s",
    "return_60s_available",
    "window_coverage_60s",
    "source_active",
    "quality_healthy",
    "gap_known_by_cutoff",
    "utc_minute_sin",
    "utc_minute_cos",
    "utc_day_sin",
    "utc_day_cos",
)
GROUPS = ("COMMODITY", "FX", "INDEX")


@dataclass(frozen=True, slots=True)
class LabConfig:
    path: Path
    job_id: str
    base_sha: str
    manifest_path: Path
    manifest_sha256: str
    output_root: Path
    horizon_minutes: int
    core_instruments: tuple[str, ...]
    lookbacks: tuple[int, ...]
    hidden_sizes: tuple[int, ...]
    seeds: tuple[int, ...]
    ridge_alpha: float
    mlp_hidden_size: int
    learning_rate: float
    weight_decay: float
    batch_size: int
    maximum_epochs: int
    patience: int
    validation_fraction: float
    maximum_fit_rows: int
    maximum_validation_rows: int

    @classmethod
    def read(cls, path: Path) -> LabConfig:
        value = json.loads(path.read_bytes())
        if value["evidence_label"] != LABEL or value["source_class"] != SOURCE_CLASS:
            raise ValueError("sequence configuration crosses the exploratory source boundary")
        if value["base_sha"] != "f31cf4731fc233726f45f67f54064c40965d01d7":
            raise ValueError("sequence configuration does not bind the authorised base")
        return cls(
            path=path,
            job_id=value["job_id"],
            base_sha=value["base_sha"],
            manifest_path=Path(value["lab_manifest"]),
            manifest_sha256=value["lab_manifest_sha256"],
            output_root=Path(value["output_root"]),
            horizon_minutes=int(value["horizon_minutes"]),
            core_instruments=tuple(value["core_instruments"]),
            lookbacks=tuple(int(item) for item in value["lookbacks_minutes"]),
            hidden_sizes=tuple(int(item) for item in value["hidden_sizes"]),
            seeds=tuple(int(item) for item in value["seeds"]),
            ridge_alpha=float(value["ridge_alpha"]),
            mlp_hidden_size=int(value["mlp_hidden_size"]),
            learning_rate=float(value["learning_rate"]),
            weight_decay=float(value["weight_decay"]),
            batch_size=int(value["batch_size"]),
            maximum_epochs=int(value["maximum_epochs"]),
            patience=int(value["early_stopping_patience"]),
            validation_fraction=float(value["validation_fraction"]),
            maximum_fit_rows=int(value["maximum_fit_rows_per_fold"]),
            maximum_validation_rows=int(value["maximum_validation_rows_per_fold"]),
        )


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    model_name: str
    family: str
    seed: int
    lookback: int | None = None
    hidden_size: int | None = None

    def as_dict(
        self, *, scope: str, config: LabConfig, instruments: Sequence[str]
    ) -> dict[str, Any]:
        return {
            "workstream": WORKSTREAM,
            "scope": scope,
            "model_name": self.model_name,
            "family": self.family,
            "seed": self.seed,
            "lookback_minutes": self.lookback,
            "hidden_size": self.hidden_size,
            "horizon_minutes": config.horizon_minutes,
            "instruments": list(instruments),
            "ridge_alpha": config.ridge_alpha if self.family == "RIDGE" else None,
            "mlp_hidden_size": config.mlp_hidden_size if self.family == "MLP" else None,
            "learning_rate": config.learning_rate if self.family != "RIDGE" else None,
            "weight_decay": config.weight_decay if self.family != "RIDGE" else None,
            "batch_size": config.batch_size if self.family != "RIDGE" else None,
            "maximum_epochs": config.maximum_epochs if self.family != "RIDGE" else None,
            "early_stopping_patience": config.patience if self.family != "RIDGE" else None,
            "validation_fraction": config.validation_fraction,
            "maximum_fit_rows_per_fold": config.maximum_fit_rows,
            "maximum_validation_rows_per_fold": config.maximum_validation_rows,
            "evidence_label": LABEL,
            "source_class": SOURCE_CLASS,
        }


@dataclass(slots=True)
class Standardiser:
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, values: np.ndarray) -> Standardiser:
        finite = np.isfinite(values)
        counts = finite.sum(axis=0)
        totals = np.where(finite, values, 0.0).sum(axis=0)
        mean = np.divide(
            totals,
            counts,
            out=np.zeros_like(totals),
            where=counts > 0,
        )
        filled = np.where(np.isfinite(values), values, mean)
        scale = filled.std(axis=0)
        scale = np.where(scale > 0.0, scale, 1.0)
        return cls(mean=mean, scale=scale)

    def transform(self, values: np.ndarray) -> np.ndarray:
        filled = np.where(np.isfinite(values), values, self.mean)
        return ((filled - self.mean) / self.scale).astype(np.float32)


@dataclass(slots=True)
class SequenceIndex:
    times: dict[str, np.ndarray]
    values: dict[str, np.ndarray]
    instruments: tuple[str, ...]
    market_groups: dict[str, str]

    @classmethod
    def build(
        cls,
        features: pl.DataFrame,
        instruments: Sequence[str],
        market_groups: dict[str, str],
    ) -> SequenceIndex:
        times: dict[str, np.ndarray] = {}
        values: dict[str, np.ndarray] = {}
        for instrument in instruments:
            frame = features.filter(pl.col("instrument_id") == instrument).sort("decision_time")
            if frame.is_empty():
                raise ValueError(f"no feature rows for {instrument}")
            times[instrument] = frame["decision_time"].cast(pl.Int64).to_numpy()
            raw = frame.select(SEQUENCE_COLUMNS).to_numpy().astype(np.float64, copy=False)
            values[instrument] = raw
        return cls(times, values, tuple(instruments), market_groups)

    @property
    def width(self) -> int:
        return len(SEQUENCE_COLUMNS) + 1 + len(self.instruments) + len(GROUPS)

    def matrix(self, rows: pl.DataFrame, lookback: int) -> np.ndarray:
        result = np.zeros((rows.height, lookback, self.width), dtype=np.float32)
        instrument_offset = len(SEQUENCE_COLUMNS) + 1
        group_offset = instrument_offset + len(self.instruments)
        minute_us = 60_000_000
        row_offset = 0
        requested = rows.with_row_index("_position")
        for instrument_index, instrument in enumerate(self.instruments):
            selected = requested.filter(pl.col("instrument_id") == instrument)
            if selected.is_empty():
                continue
            positions = selected["_position"].to_numpy()
            requested_times = selected["decision_time"].cast(pl.Int64).to_numpy()
            source_times = self.times[instrument]
            source_values = self.values[instrument]
            endpoints = np.searchsorted(source_times, requested_times)
            if np.any(endpoints >= source_times.size) or np.any(
                source_times[endpoints] != requested_times
            ):
                raise ValueError(f"sequence endpoint is absent for {instrument}")
            offsets = np.arange(lookback - 1, -1, -1, dtype=np.int64)
            expected_times = requested_times[:, None] - offsets[None, :] * minute_us
            indices = np.searchsorted(source_times, expected_times)
            clipped = np.clip(indices, 0, source_times.size - 1)
            present = (indices < source_times.size) & (source_times[clipped] == expected_times)
            dynamic = source_values[clipped]
            dynamic = np.where(np.isfinite(dynamic), dynamic, 0.0)
            dynamic = np.where(present[:, :, None], dynamic, 0.0)
            result[positions, :, : len(SEQUENCE_COLUMNS)] = dynamic.astype(np.float32)
            result[positions, :, len(SEQUENCE_COLUMNS)] = present.astype(np.float32)
            result[positions, :, instrument_offset + instrument_index] = 1.0
            group_index = GROUPS.index(self.market_groups[instrument])
            result[positions, :, group_offset + group_index] = 1.0
            row_offset += selected.height
        if row_offset != rows.height:
            raise ValueError("sequence rows contain an instrument outside the configured scope")
        return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_configurations(config: LabConfig, *, smoke: bool) -> tuple[ModelConfiguration, ...]:
    seed = config.seeds[0]
    values = [
        ModelConfiguration("POOLED_RIDGE_ENGINEERED", "RIDGE", seed),
        ModelConfiguration("MLP_ENGINEERED", "MLP", seed, hidden_size=config.mlp_hidden_size),
    ]
    lstms = [
        ModelConfiguration(
            f"LSTM_L{lookback}_H{hidden}_S{candidate_seed}",
            "LSTM",
            candidate_seed,
            lookback,
            hidden,
        )
        for lookback in config.lookbacks
        for hidden in config.hidden_sizes
        for candidate_seed in config.seeds
    ]
    values.extend(lstms[:1] if smoke else lstms)
    return tuple(values)


def _load_non_terminal(
    config: LabConfig, instruments: Sequence[str]
) -> tuple[dict[str, Any], pl.DataFrame, pl.DataFrame]:
    manifest = authenticate_manifest(config.manifest_path, config.manifest_sha256)
    blocks = ("TRAINING_ONLY", *DEVELOPMENT_BLOCKS)
    features = load_parts(
        config.manifest_path,
        config.manifest_sha256,
        kind="feature",
        instruments=instruments,
        blocks=blocks,
    ).collect()
    targets = load_parts(
        config.manifest_path,
        config.manifest_sha256,
        kind="target",
        instruments=instruments,
        horizons=(config.horizon_minutes,),
        blocks=blocks,
    ).collect()
    return manifest, features, targets


def _joined_rows(
    features: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    blocks: Sequence[str],
    maturity_before: datetime | None,
) -> pl.DataFrame:
    selected = targets.filter(pl.col("block").is_in(blocks) & pl.col("target_valid"))
    if maturity_before is not None:
        selected = selected.filter(pl.col("target_available_at") < maturity_before)
    keys = ["instrument_id", "decision_time"]
    return selected.join(features, on=keys, how="inner", validate="1:1").sort(
        "decision_time", "instrument_id"
    )


def _bounded_chronological_rows(frame: pl.DataFrame, maximum: int) -> pl.DataFrame:
    if frame.height <= maximum:
        return frame
    indices = np.linspace(0, frame.height - 1, maximum, dtype=np.int64)
    return frame[indices]


def chronological_fit_validation(
    frame: pl.DataFrame,
    *,
    validation_fraction: float,
    maximum_fit_rows: int,
    maximum_validation_rows: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    times = frame["decision_time"].unique().sort()
    if len(times) < 2:
        raise ValueError("chronological training data has fewer than two distinct times")
    split_index = max(1, min(len(times) - 1, math.floor(len(times) * (1.0 - validation_fraction))))
    split = cast(datetime, times[split_index])
    fit = frame.filter(pl.col("decision_time") < split)
    validation = frame.filter(pl.col("decision_time") >= split)
    if fit.is_empty() or validation.is_empty():
        raise ValueError("chronological early-stopping split is empty")
    return (
        _bounded_chronological_rows(fit, maximum_fit_rows),
        _bounded_chronological_rows(validation, maximum_validation_rows),
    )


def _engineered_columns(features: pl.DataFrame) -> tuple[str, ...]:
    return tuple(
        name
        for name, dtype in features.schema.items()
        if name not in METADATA_COLUMNS and dtype.is_numeric()
    )


def _engineered_matrix(
    rows: pl.DataFrame,
    *,
    columns: Sequence[str],
    instruments: Sequence[str],
    market_groups: dict[str, str],
) -> np.ndarray:
    numeric = rows.select(columns).to_numpy().astype(np.float64, copy=False)
    identity = np.zeros((rows.height, len(instruments) + len(GROUPS)), dtype=np.float64)
    instrument_values = rows["instrument_id"].to_list()
    instrument_positions = {value: index for index, value in enumerate(instruments)}
    for row, instrument in enumerate(instrument_values):
        identity[row, instrument_positions[instrument]] = 1.0
        identity[row, len(instruments) + GROUPS.index(market_groups[instrument])] = 1.0
    return np.concatenate((numeric, identity), axis=1)


def _target_vector(rows: pl.DataFrame) -> np.ndarray:
    return rows["target_return"].to_numpy().astype(np.float32, copy=False)


def _set_deterministic(seed: int) -> Any:
    torch = importlib.import_module("torch")

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(4, max(1, torch.get_num_threads())))
    return torch


def _fit_neural(
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    predict_x: np.ndarray,
    *,
    model_family: str,
    input_width: int,
    hidden_size: int,
    seed: int,
    config: LabConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    torch = _set_deterministic(seed)
    nn = importlib.import_module("torch.nn")
    torch_data = importlib.import_module("torch.utils.data")
    DataLoader = torch_data.DataLoader
    TensorDataset = torch_data.TensorDataset

    class MLP(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layers = nn.Sequential(
                nn.Linear(input_width, hidden_size),
                nn.ReLU(),
                nn.Linear(hidden_size, 1),
            )

        def forward(self, values: Any) -> Any:
            return self.layers(values).squeeze(-1)

    class LSTM(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.recurrent = nn.LSTM(input_width, hidden_size, batch_first=True)
            self.output = nn.Linear(hidden_size, 1)

        def forward(self, values: Any) -> Any:
            recurrent, _ = self.recurrent(values)
            return self.output(recurrent[:, -1, :]).squeeze(-1)

    target_mean = float(train_y.mean())
    target_scale = float(train_y.std())
    if not target_scale > 0.0:
        raise ValueError("training targets have zero variance")
    scaled_train_y = ((train_y - target_mean) / target_scale).astype(np.float32)
    scaled_validation_y = ((validation_y - target_mean) / target_scale).astype(np.float32)
    model = MLP() if model_family == "MLP" else LSTM()
    optimiser = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    loss_function = nn.MSELoss()
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(train_x), torch.from_numpy(scaled_train_y)),
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=0,
    )
    validation_tensor = torch.from_numpy(validation_x)
    validation_target = torch.from_numpy(scaled_validation_y)
    best_state: dict[str, Any] | None = None
    best_loss = math.inf
    remaining = config.patience
    curve: list[dict[str, float | int]] = []
    for epoch in range(1, config.maximum_epochs + 1):
        model.train()
        total_loss = 0.0
        batches = 0
        for batch_x, batch_y in loader:
            optimiser.zero_grad(set_to_none=True)
            loss = loss_function(model(batch_x), batch_y)
            loss.backward()
            optimiser.step()
            total_loss += float(loss.detach())
            batches += 1
        model.eval()
        with torch.no_grad():
            validation_loss = float(loss_function(model(validation_tensor), validation_target))
        curve.append(
            {
                "epoch": epoch,
                "training_standardised_mse": total_loss / batches,
                "validation_standardised_mse": validation_loss,
            }
        )
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_state = {
                name: value.detach().clone() for name, value in model.state_dict().items()
            }
            remaining = config.patience
        else:
            remaining -= 1
            if remaining == 0:
                break
    if best_state is None:
        raise RuntimeError("neural training did not produce an early-stopping state")
    model.load_state_dict(best_state)
    model.eval()
    predictions: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, predict_x.shape[0], config.batch_size):
            scaled = model(torch.from_numpy(predict_x[start : start + config.batch_size]))
            predictions.append(scaled.numpy())
    return (
        np.concatenate(predictions).astype(np.float64) * target_scale + target_mean,
        {"best_validation_standardised_mse": best_loss, "epochs": curve},
    )


def _fit_predict(
    model: ModelConfiguration,
    fit: pl.DataFrame,
    validation: pl.DataFrame,
    score: pl.DataFrame,
    *,
    config: LabConfig,
    engineered_columns: Sequence[str],
    instruments: Sequence[str],
    market_groups: dict[str, str],
    sequence_index: SequenceIndex,
) -> tuple[np.ndarray, dict[str, Any]]:
    fit_y = _target_vector(fit)
    validation_y = _target_vector(validation)
    if model.family in {"RIDGE", "MLP"}:
        fit_raw = _engineered_matrix(
            fit, columns=engineered_columns, instruments=instruments, market_groups=market_groups
        )
        validation_raw = _engineered_matrix(
            validation,
            columns=engineered_columns,
            instruments=instruments,
            market_groups=market_groups,
        )
        score_raw = _engineered_matrix(
            score, columns=engineered_columns, instruments=instruments, market_groups=market_groups
        )
        standardiser = Standardiser.fit(fit_raw)
        fit_x = standardiser.transform(fit_raw)
        validation_x = standardiser.transform(validation_raw)
        score_x = standardiser.transform(score_raw)
        if model.family == "RIDGE":
            estimator = Ridge(alpha=config.ridge_alpha, solver="lsqr")
            estimator.fit(fit_x, fit_y)
            return estimator.predict(score_x).astype(np.float64), {
                "fit_rows": fit.height,
                "validation_rows": validation.height,
                "epochs": [],
            }
        return _fit_neural(
            fit_x,
            fit_y,
            validation_x,
            validation_y,
            score_x,
            model_family="MLP",
            input_width=fit_x.shape[1],
            hidden_size=cast(int, model.hidden_size),
            seed=model.seed,
            config=config,
        )
    lookback = cast(int, model.lookback)
    fit_raw = sequence_index.matrix(fit, lookback)
    validation_raw = sequence_index.matrix(validation, lookback)
    score_raw = sequence_index.matrix(score, lookback)
    standardiser = Standardiser.fit(fit_raw.reshape(-1, sequence_index.width))
    fit_x = standardiser.transform(fit_raw)
    validation_x = standardiser.transform(validation_raw)
    score_x = standardiser.transform(score_raw)
    return _fit_neural(
        fit_x,
        fit_y,
        validation_x,
        validation_y,
        score_x,
        model_family="LSTM",
        input_width=sequence_index.width,
        hidden_size=cast(int, model.hidden_size),
        seed=model.seed,
        config=config,
    )


def _prediction_frame(rows: pl.DataFrame, predictions: np.ndarray, horizon: int) -> pl.DataFrame:
    return rows.select("instrument_id", "decision_time").with_columns(
        pl.lit(horizon, dtype=pl.Int16).alias("horizon_minutes"),
        pl.Series("expected_return", predictions),
    )


def _run_model(
    model: ModelConfiguration,
    *,
    blocks: Sequence[str],
    features: pl.DataFrame,
    targets: pl.DataFrame,
    config: LabConfig,
    instruments: Sequence[str],
    market_groups: dict[str, str],
    smoke: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], pl.DataFrame]:
    sequence_index = SequenceIndex.build(features, instruments, market_groups)
    engineered_columns = _engineered_columns(features)
    predictions: list[pl.DataFrame] = []
    evaluated_targets: list[pl.DataFrame] = []
    curves: list[dict[str, Any]] = []
    block_by_name = {
        item["name"]: item
        for item in authenticate_manifest(config.manifest_path, config.manifest_sha256)[
            "fold_blocks"
        ]
    }
    for block in blocks:
        block_start = datetime.fromisoformat(block_by_name[block]["start"])
        train = _joined_rows(
            features,
            targets,
            blocks=TRAINING_BLOCKS[block],
            maturity_before=block_start,
        )
        score = _joined_rows(features, targets, blocks=(block,), maturity_before=None)
        if smoke:
            train = _bounded_chronological_rows(train, 2000)
            score = _bounded_chronological_rows(score, 1000)
        fit, validation = chronological_fit_validation(
            train,
            validation_fraction=config.validation_fraction,
            maximum_fit_rows=min(config.maximum_fit_rows, 1500)
            if smoke
            else config.maximum_fit_rows,
            maximum_validation_rows=(
                min(config.maximum_validation_rows, 500)
                if smoke
                else config.maximum_validation_rows
            ),
        )
        values, curve = _fit_predict(
            model,
            fit,
            validation,
            score,
            config=config,
            engineered_columns=engineered_columns,
            instruments=instruments,
            market_groups=market_groups,
            sequence_index=sequence_index,
        )
        curve.update(
            {
                "model_name": model.model_name,
                "block": block,
                "fit_rows": fit.height,
                "validation_rows": validation.height,
                "score_rows": score.height,
            }
        )
        curves.append(curve)
        predictions.append(_prediction_frame(score, values, config.horizon_minutes))
        evaluated_targets.append(
            score.select(
                "instrument_id",
                "decision_time",
                "horizon_minutes",
                "target_return",
                "target_valid",
                "block",
            )
        )
    prediction_frame = pl.concat(predictions)
    target_frame = pl.concat(evaluated_targets)
    evaluation = evaluate_against_zero(
        prediction_frame,
        target_frame,
        model_name=model.model_name,
    )
    return evaluation, curves, prediction_frame


def screening_gate(
    evaluation: dict[str, Any],
    *,
    mlp_evaluation: dict[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    failures = []
    if cast(float, evaluation["skill_versus_zero"]) <= 0.0:
        failures.append("aggregate pre-holdout skill does not beat ZERO_RETURN")
    if cast(int, evaluation["positive_chronological_block_count"]) < 2:
        failures.append("fewer than two chronological development blocks improve on ZERO_RETURN")
    if cast(int, evaluation["positive_instrument_count"]) < 2:
        failures.append("improvement is absent outside a single instrument")
    if cast(float, evaluation["model_instrument_balanced_mse"]) > cast(
        float, mlp_evaluation["model_instrument_balanced_mse"]
    ):
        failures.append("sequence model is dominated by the engineered-feature MLP")
    return not failures, tuple(failures)


def _result_row(
    *,
    scope: str,
    model: ModelConfiguration,
    configuration: dict[str, Any],
    evaluation_stage: str,
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "job_id": WORKSTREAM,
        "scope": scope,
        "configuration_id": configuration_id(configuration),
        "model_name": model.model_name,
        "family": model.family,
        "evaluation_stage": evaluation_stage,
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "support": evaluation["support"],
        "forecast_coverage": evaluation["forecast_coverage"],
        "zero_return_instrument_balanced_mse": evaluation["zero_return_instrument_balanced_mse"],
        "model_instrument_balanced_mse": evaluation["model_instrument_balanced_mse"],
        "direct_delta_mse_versus_zero": evaluation["direct_delta_mse_versus_zero"],
        "skill_versus_zero": evaluation["skill_versus_zero"],
        "positive_chronological_block_count": evaluation["positive_chronological_block_count"],
        "chronological_block_count": evaluation["chronological_block_count"],
        "positive_instrument_count": evaluation["positive_instrument_count"],
        "instrument_count": evaluation["instrument_count"],
        "calibration_slope": evaluation["calibration_slope"],
        "spearman_correlation": evaluation["spearman_correlation"],
        "best_instrument_contribution": evaluation["best_instrument_contribution"],
        "best_period_contribution": evaluation["best_period_contribution"],
        "configuration_json": json.dumps(configuration, sort_keys=True, separators=(",", ":")),
    }


def _write_result_summary(
    path: Path,
    *,
    scope: str,
    attempted: int,
    evaluations: dict[str, dict[str, Any]],
    finalist_names: Sequence[str],
    gate_failures: dict[str, tuple[str, ...]],
    terminal_evaluations: dict[str, dict[str, Any]],
) -> None:
    lines = [
        f"# {WORKSTREAM} temporal representation result",
        "",
        f"STATUS: COMPLETE — {LABEL}",
        "",
        (
            "This is post-hoc hypothesis generation over consumed "
            f"{SOURCE_CLASS} data. It is not a second holdout, confirmation, promotion, "
            "or decision-grade conclusion."
        ),
        "",
        f"- Scope: {scope}",
        f"- Configurations attempted: {attempted}",
        f"- Frozen sequence finalists: {', '.join(finalist_names) if finalist_names else 'none'}",
        "",
        "## Pre-holdout development results",
        "",
        "| Model | MSE | Delta vs zero | Skill vs zero | Positive blocks | Positive instruments |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, evaluation in evaluations.items():
        lines.append(
            f"| {name} | {evaluation['model_instrument_balanced_mse']:.18g} | "
            f"{evaluation['direct_delta_mse_versus_zero']:.18g} | "
            f"{evaluation['skill_versus_zero']:.9g} | "
            f"{evaluation['positive_chronological_block_count']}/"
            f"{evaluation['chronological_block_count']} | "
            f"{evaluation['positive_instrument_count']}/{evaluation['instrument_count']} |"
        )
    lines.extend(["", "## Screening", ""])
    if gate_failures:
        for name, failures in gate_failures.items():
            lines.append(f"- {name}: {'; '.join(failures) if failures else 'PASSED'}")
    else:
        lines.append("- No sequence model completed.")
    lines.extend(["", "## Former consumed holdout development block", ""])
    if terminal_evaluations:
        for name, evaluation in terminal_evaluations.items():
            lines.append(
                f"- {name}: MSE {evaluation['model_instrument_balanced_mse']:.18g}; "
                f"skill vs zero {evaluation['skill_versus_zero']:.9g}. "
                f"Label: {LABEL}."
            )
    else:
        lines.append(
            "- Not accessed because no sequence candidate passed every screening condition."
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "- An LSTM improvement over Ridge without improvement over the MLP would support "
                "nonlinearity, not temporal memory."
            ),
            (
                "- Only an LSTM improvement over both controls would support further sequence "
                "investigation in a future untouched experiment."
            ),
            "- No result here is confirmatory.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(config: LabConfig, *, scope: str, smoke: bool, output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"sequence output is create-only: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest, all_features, all_targets = _load_non_terminal(
        config,
        config.core_instruments
        if scope == "CORE_6"
        else authenticate_manifest(config.manifest_path, config.manifest_sha256)["instruments"],
    )
    instruments = tuple(config.core_instruments if scope == "CORE_6" else manifest["instruments"])
    market_groups = cast(dict[str, str], manifest["market_groups"])
    models = _model_configurations(config, smoke=smoke)
    register = output / "run-register.jsonl"
    result_rows: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    evaluations: dict[str, dict[str, Any]] = {}
    configurations: dict[str, dict[str, Any]] = {}
    for model in models:
        configuration = model.as_dict(scope=scope, config=config, instruments=instruments)
        configurations[model.model_name] = configuration
        try:
            evaluation, model_curves, _ = _run_model(
                model,
                blocks=DEVELOPMENT_BLOCKS[:1] if smoke else DEVELOPMENT_BLOCKS,
                features=all_features,
                targets=all_targets,
                config=config,
                instruments=instruments,
                market_groups=market_groups,
                smoke=smoke,
            )
            append_attempt(
                register,
                workstream=WORKSTREAM,
                configuration=configuration,
                result=evaluation,
                manifest_sha256=config.manifest_sha256,
            )
            evaluations[model.model_name] = evaluation
            curves.extend(model_curves)
            result_rows.append(
                _result_row(
                    scope=scope,
                    model=model,
                    configuration=configuration,
                    evaluation_stage="PRE_HOLDOUT_DEVELOPMENT",
                    evaluation=evaluation,
                )
            )
        except Exception as error:
            append_attempt(
                register,
                workstream=WORKSTREAM,
                configuration=configuration,
                result={
                    "status": "FAILED",
                    "failure_type": type(error).__name__,
                    "failure": str(error),
                },
                manifest_sha256=config.manifest_sha256,
            )
    gate_failures: dict[str, tuple[str, ...]] = {}
    finalists: list[ModelConfiguration] = []
    mlp = evaluations.get("MLP_ENGINEERED")
    if not smoke and mlp is not None:
        candidates = []
        for model in models:
            if model.family != "LSTM" or model.model_name not in evaluations:
                continue
            passed, failures = screening_gate(evaluations[model.model_name], mlp_evaluation=mlp)
            gate_failures[model.model_name] = failures
            if passed:
                candidates.append(model)
        finalists = sorted(
            candidates,
            key=lambda item: cast(float, evaluations[item.model_name]["skill_versus_zero"]),
            reverse=True,
        )[:2]
    terminal_evaluations: dict[str, dict[str, Any]] = {}
    if finalists:
        ids = [configuration_id(configurations[item.model_name]) for item in finalists]
        freeze_path = output / "finalists.json"
        freeze_finalists(
            register,
            freeze_path,
            workstream=WORKSTREAM,
            finalist_configuration_ids=ids,
            manifest_sha256=config.manifest_sha256,
        )
        freeze_sha = _sha256(freeze_path)
        terminal_features = load_parts(
            config.manifest_path,
            config.manifest_sha256,
            kind="feature",
            instruments=instruments,
            blocks=(TERMINAL_BLOCK,),
            finalist_freeze=freeze_path,
            expected_finalist_freeze_sha256=freeze_sha,
            configuration_id=ids[0],
        ).collect()
        terminal_targets = load_parts(
            config.manifest_path,
            config.manifest_sha256,
            kind="target",
            instruments=instruments,
            horizons=(config.horizon_minutes,),
            blocks=(TERMINAL_BLOCK,),
            finalist_freeze=freeze_path,
            expected_finalist_freeze_sha256=freeze_sha,
            configuration_id=ids[0],
        ).collect()
        complete_features = pl.concat((all_features, terminal_features), how="vertical")
        complete_targets = pl.concat((all_targets, terminal_targets), how="vertical")
        for model in finalists:
            evaluation, model_curves, _ = _run_model(
                model,
                blocks=(TERMINAL_BLOCK,),
                features=complete_features,
                targets=complete_targets,
                config=config,
                instruments=instruments,
                market_groups=market_groups,
                smoke=False,
            )
            append_attempt(
                register,
                workstream=WORKSTREAM,
                configuration=configurations[model.model_name],
                result=evaluation,
                manifest_sha256=config.manifest_sha256,
            )
            terminal_evaluations[model.model_name] = evaluation
            curves.extend(model_curves)
            result_rows.append(
                _result_row(
                    scope=scope,
                    model=model,
                    configuration=configurations[model.model_name],
                    evaluation_stage="FORMER_HOLDOUT_POST_HOC",
                    evaluation=evaluation,
                )
            )
    pl.DataFrame(result_rows, infer_schema_length=None).write_parquet(
        output / "sequence-results.parquet"
    )
    (output / "learning-curve-summary.json").write_text(
        json.dumps(curves, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "sequence-configurations.json").write_bytes(config.path.read_bytes())
    _write_result_summary(
        output / "result.md",
        scope=scope,
        attempted=len(models),
        evaluations=evaluations,
        finalist_names=[item.model_name for item in finalists],
        gate_failures=gate_failures,
        terminal_evaluations=terminal_evaluations,
    )
    return {
        "status": "COMPLETE",
        "evidence_label": LABEL,
        "scope": scope,
        "output": str(output),
        "configurations_attempted": len(models),
        "configurations_completed": len(evaluations),
        "finalists": [item.model_name for item in finalists],
        "terminal_accessed": bool(finalists),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scope", choices=("CORE_6", "ALL_20"), default="CORE_6")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    config = LabConfig.read(arguments.config)
    output = arguments.output or config.output_root
    print(json.dumps(run(config, scope=arguments.scope, smoke=arguments.smoke, output=output)))


if __name__ == "__main__":
    main()
