"""LAB-U universe and pooling comparison over the authenticated LAB-0 dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.baseline import (
    _fit_preprocessing,
    _transform,
    _weights,
)
from experiments.r2_historical_lab.harness import (
    TERMINAL_BLOCK,
    append_attempt,
    configuration_id,
    evaluate_against_zero,
    freeze_finalists,
    load_parts,
)

DEVELOPMENT_BLOCKS = ("DEV_1", "DEV_2", "DEV_3")
ALL_PRE_TERMINAL_BLOCKS = ("TRAINING_ONLY", *DEVELOPMENT_BLOCKS)
CORE_6 = (
    "commodity:spot-gold",
    "commodity:us-crude",
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
)
ALL_20 = (
    "commodity:spot-gold",
    "commodity:spot-silver",
    "commodity:us-crude",
    "fx:aud-usd",
    "fx:eur-jpy",
    "fx:eur-usd",
    "fx:gbp-usd",
    "fx:nzd-usd",
    "fx:usd-cad",
    "fx:usd-chf",
    "fx:usd-jpy",
    "index:australia-200",
    "index:eu-stocks-50",
    "index:ftse-100",
    "index:germany-40",
    "index:hong-kong-hs50",
    "index:japan-225",
    "index:us-500",
    "index:us-tech-100",
    "index:wall-street",
)
OMITTED_14 = tuple(item for item in ALL_20 if item not in CORE_6)
GROUP_BY_INSTRUMENT = {
    instrument: instrument.split(":", maxsplit=1)[0].upper() for instrument in ALL_20
}
UNIVERSES = {"CORE_6": CORE_6, "OMITTED_14": OMITTED_14, "ALL_20": ALL_20}
MATRICES = (
    ("CORE_6", "CORE_6"),
    ("ALL_20", "CORE_6"),
    ("OMITTED_14", "OMITTED_14"),
    ("ALL_20", "ALL_20"),
)
MODELS = ("LOCAL_RIDGE", "FULLY_POOLED_LOCAL_RIDGE", "GROUP_POOLED_RIDGE")
ALPHA_GRID = (0.01, 0.1, 1.0, 10.0)
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
INDICATORS = {
    "return_60s_available",
    "return_300s_available",
    "source_active",
    "quality_healthy",
    "gap_known_by_cutoff",
}
MINIMUM_TRAINING_ROWS = 100
MINIMUM_INNER_VALIDATION_ROWS = 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _configuration(train_universe: str, evaluation_universe: str, model: str) -> dict[str, Any]:
    return {
        "job_id": "LAB-U",
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "horizon_minutes": 15,
        "decision_cadence_seconds": 60,
        "feature_policy": "ORIGINAL_L1_P0_LOCAL",
        "preprocessing_policy": "TRAINING_MEDIAN_STANDARDISE_V1",
        "alpha_grid": list(ALPHA_GRID),
        "inner_validation_policy": "CHRONOLOGICAL_TAIL_PURGED_V1",
        "ridge_solver": "lsqr",
        "ridge_tolerance": 1e-8,
        "ridge_max_iterations": 10_000,
        "training_universe": train_universe,
        "evaluation_universe": evaluation_universe,
        "model": model,
    }


def _load(
    manifest_path: Path,
    manifest_sha256: str,
    blocks: Sequence[str],
    *,
    finalist_freeze: Path | None = None,
    finalist_freeze_sha256: str | None = None,
    finalist_configuration_id: str | None = None,
    instruments: Sequence[str] = ALL_20,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    common = {
        "manifest_path": manifest_path,
        "expected_manifest_sha256": manifest_sha256,
        "instruments": instruments,
        "blocks": blocks,
        "finalist_freeze": finalist_freeze,
        "expected_finalist_freeze_sha256": finalist_freeze_sha256,
        "configuration_id": finalist_configuration_id,
    }
    features = load_parts(**common, kind="feature").collect()
    context = load_parts(**common, kind="context").collect()
    targets = (
        load_parts(**common, kind="target", horizons=(15,))
        .filter(pl.col("target_valid"))
        .collect()
    )
    return features, context, targets


def _rows_for_universe(
    features: pl.DataFrame,
    context: pl.DataFrame,
    targets: pl.DataFrame,
    universe: Sequence[str],
) -> pl.DataFrame:
    selected = list(universe)
    selected_context = context.filter(pl.col("instrument_id").is_in(selected))
    totals = selected_context.group_by("decision_time").agg(
        pl.col("current_available").sum().alias("_universe_available")
    )
    own = selected_context.select(
        "instrument_id",
        "decision_time",
        pl.col("current_available").alias("_own_available"),
    )
    scoped_features = (
        features.filter(pl.col("instrument_id").is_in(selected))
        .drop("cross_market_available_count")
        .join(totals, on="decision_time", how="left", validate="m:1")
        .join(own, on=["instrument_id", "decision_time"], how="left", validate="1:1")
        .with_columns(
            (
                pl.col("_universe_available").fill_null(0.0)
                - pl.col("_own_available").fill_null(0.0)
            ).alias("cross_market_available_count")
        )
        .drop("_universe_available", "_own_available")
    )
    return (
        targets.filter(pl.col("instrument_id").is_in(selected))
        .join(scoped_features, on=["instrument_id", "decision_time"], how="inner", validate="1:1")
        .sort("decision_time", "instrument_id")
    )


def _training_and_validation(
    rows: pl.DataFrame,
    block: str,
    evaluation_instruments: Sequence[str],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    validation = rows.filter(
        (pl.col("block") == block)
        & pl.col("instrument_id").is_in(list(evaluation_instruments))
    )
    if validation.is_empty():
        raise ValueError(f"no validation rows for {block}")
    validation_start = cast(datetime, validation["decision_time"].min())
    training = rows.filter(
        (pl.col("decision_time") < validation_start)
        & (pl.col("target_end") <= validation_start)
        & (pl.col("target_available_at") <= validation_start)
    )
    return training.sort("decision_time", "instrument_id"), validation.sort(
        "decision_time", "instrument_id"
    )


def _identity(
    frame: pl.DataFrame, order: Sequence[str]
) -> np.ndarray:
    positions = {instrument: index for index, instrument in enumerate(order)}
    values = np.zeros((frame.height, len(order)), dtype=float)
    for row_index, instrument in enumerate(frame["instrument_id"]):
        if instrument not in positions:
            raise ValueError(f"instrument lacks pooled identity: {instrument}")
        values[row_index, positions[instrument]] = 1.0
    return values


def _design(
    frame: pl.DataFrame,
    state: dict[str, object],
    identity_order: Sequence[str],
) -> np.ndarray:
    matrix = frame.select(FEATURE_NAMES).to_numpy().astype(float)
    transformed = _transform(matrix, state)
    if identity_order:
        return np.column_stack((transformed, _identity(frame, identity_order)))
    return transformed


def _select_alpha(
    training: pl.DataFrame,
    identity_order: Sequence[str],
) -> float:
    ordered = training.sort("decision_time", "instrument_id")
    if ordered.height < MINIMUM_TRAINING_ROWS + MINIMUM_INNER_VALIDATION_ROWS:
        raise ValueError("training support is insufficient for original Ridge selection")
    position = max(0, ordered.height - MINIMUM_INNER_VALIDATION_ROWS)
    validation_start = cast(datetime, ordered["decision_time"][position])
    inner_validation = ordered.filter(pl.col("decision_time") >= validation_start)
    inner_fit = ordered.filter(
        (pl.col("decision_time") < validation_start)
        & (pl.col("target_end") <= validation_start)
        & (pl.col("target_available_at") <= validation_start)
    )
    if inner_fit.height < MINIMUM_TRAINING_ROWS:
        raise ValueError("purged inner fit has fewer than 100 rows")
    pooled = bool(identity_order)
    weights = _weights(inner_fit, pooled)
    x_fit, state = _fit_preprocessing(
        inner_fit.select(FEATURE_NAMES).to_numpy().astype(float),
        FEATURE_NAMES,
        INDICATORS,
        weights,
    )
    x_validation = _design(inner_validation, state, ())
    if identity_order:
        x_fit = np.column_stack((x_fit, _identity(inner_fit, identity_order)))
        x_validation = np.column_stack(
            (x_validation, _identity(inner_validation, identity_order))
        )
    y_fit = inner_fit["target_return"].to_numpy()
    y_validation = inner_validation["target_return"].to_numpy()
    scored: list[tuple[float, float]] = []
    for alpha in ALPHA_GRID:
        model = Ridge(
            alpha=alpha,
            solver="lsqr",
            tol=1e-8,
            max_iter=10_000,
            fit_intercept=not pooled,
        )
        model.fit(x_fit, y_fit, sample_weight=np.asarray(state["weights"], dtype=float))
        loss = float(np.mean((model.predict(x_validation) - y_validation) ** 2))
        scored.append((loss, -alpha))
    return -min(scored)[1]


def _fit_predict_one(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    identity_order: Sequence[str],
) -> np.ndarray:
    if training.height < MINIMUM_TRAINING_ROWS:
        raise ValueError("outer fit has fewer than 100 rows")
    alpha = _select_alpha(training, identity_order)
    pooled = bool(identity_order)
    weights = _weights(training, pooled)
    x_training, state = _fit_preprocessing(
        training.select(FEATURE_NAMES).to_numpy().astype(float),
        FEATURE_NAMES,
        INDICATORS,
        weights,
    )
    x_validation = _design(validation, state, ())
    if identity_order:
        x_training = np.column_stack((x_training, _identity(training, identity_order)))
        x_validation = np.column_stack(
            (x_validation, _identity(validation, identity_order))
        )
    model = Ridge(
        alpha=alpha,
        solver="lsqr",
        tol=1e-8,
        max_iter=10_000,
        fit_intercept=not pooled,
    )
    model.fit(
        x_training,
        training["target_return"].to_numpy(),
        sample_weight=np.asarray(state["weights"], dtype=float),
    )
    return np.asarray(model.predict(x_validation), dtype=float)


def _predict(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    model: str,
    training_universe: Sequence[str],
    evaluation_universe: Sequence[str],
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    if model == "LOCAL_RIDGE":
        scopes = [
            ((instrument,), (instrument,))
            for instrument in evaluation_universe
        ]
    elif model == "FULLY_POOLED_LOCAL_RIDGE":
        scopes = [(tuple(training_universe), tuple(evaluation_universe))]
    elif model == "GROUP_POOLED_RIDGE":
        scopes = []
        for group in ("COMMODITY", "FX", "INDEX"):
            train_group = tuple(
                item for item in training_universe if GROUP_BY_INSTRUMENT[item] == group
            )
            evaluate_group = tuple(
                item for item in evaluation_universe if GROUP_BY_INSTRUMENT[item] == group
            )
            if evaluate_group:
                scopes.append((train_group, evaluate_group))
    else:
        raise ValueError(f"unsupported LAB-U model: {model}")

    for train_scope, evaluate_scope in scopes:
        fit_rows = training.filter(pl.col("instrument_id").is_in(list(train_scope)))
        validation_rows = validation.filter(
            pl.col("instrument_id").is_in(list(evaluate_scope))
        )
        identity_order = () if model == "LOCAL_RIDGE" else train_scope
        prediction = _fit_predict_one(fit_rows, validation_rows, identity_order)
        frames.append(
            validation_rows.select(
                "instrument_id", "decision_time", "horizon_minutes"
            ).with_columns(pl.Series("expected_return", prediction))
        )
    if not frames:
        raise ValueError("model produced no prediction scopes")
    return pl.concat(frames).sort("decision_time", "instrument_id")


def _evaluate(
    predictions: pl.DataFrame,
    validation: pl.DataFrame,
    model: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = cast(
        dict[str, Any],
        evaluate_against_zero(predictions, validation, model_name=model),
    )
    keys = ["instrument_id", "decision_time", "horizon_minutes"]
    scored = (
        validation.join(predictions, on=keys, how="inner", validate="1:1")
        .with_columns(
            (pl.col("target_return") ** 2).alias("_zero_se"),
            ((pl.col("expected_return") - pl.col("target_return")) ** 2).alias(
                "_model_se"
            ),
        )
        .join(
            pl.DataFrame(
                {
                    "instrument_id": list(GROUP_BY_INSTRUMENT),
                    "market_group": list(GROUP_BY_INSTRUMENT.values()),
                }
            ),
            on="instrument_id",
            how="left",
            validate="m:1",
        )
    )
    instruments = scored.group_by("market_group", "instrument_id").agg(
        pl.len().alias("support"),
        pl.col("_zero_se").mean().alias("zero_mse"),
        pl.col("_model_se").mean().alias("model_mse"),
        (pl.col("_zero_se") - pl.col("_model_se")).sum().alias("improvement"),
    )
    positive_total = float(
        cast(float, instruments["improvement"].clip(lower_bound=0.0).sum())
    )
    instruments = instruments.with_columns(
        (1.0 - pl.col("model_mse") / pl.col("zero_mse")).alias("skill_versus_zero"),
        (
            pl.col("improvement").clip(lower_bound=0.0) / positive_total
            if positive_total > 0
            else pl.lit(0.0)
        ).alias("positive_improvement_share"),
    )
    groups = instruments.group_by("market_group").agg(
        pl.col("support").sum().alias("support"),
        pl.col("zero_mse").mean().alias("zero_mse"),
        pl.col("model_mse").mean().alias("model_mse"),
        pl.col("improvement").sum().alias("improvement"),
        pl.col("positive_improvement_share").sum().alias("positive_improvement_share"),
    ).with_columns(
        (1.0 - pl.col("model_mse") / pl.col("zero_mse")).alias("skill_versus_zero")
    )
    equal_group_zero = float(cast(float, groups["zero_mse"].mean()))
    equal_group_model = float(cast(float, groups["model_mse"].mean()))
    result["equal_group_then_instrument_zero_mse"] = equal_group_zero
    result["equal_group_then_instrument_model_mse"] = equal_group_model
    result["equal_group_then_instrument_delta_mse"] = (
        equal_group_model - equal_group_zero
    )
    result["equal_group_then_instrument_skill"] = (
        1.0 - equal_group_model / equal_group_zero
    )
    detail: list[dict[str, Any]] = []
    for row in groups.iter_rows(named=True):
        detail.append({"level": "GROUP", "name": row["market_group"], **row})
    for row in instruments.iter_rows(named=True):
        detail.append({"level": "INSTRUMENT", "name": row["instrument_id"], **row})
    return result, detail


def _flatten_result(
    configuration: dict[str, Any],
    identifier: str,
    stage: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "stage": stage,
        "configuration_id": identifier,
        "training_universe": configuration["training_universe"],
        "evaluation_universe": configuration["evaluation_universe"],
        "model": configuration["model"],
        **{
            key: value
            for key, value in result.items()
            if isinstance(value, (int, float, bool)) or value is None
        },
    }


def _run_configuration(
    rows: pl.DataFrame,
    configuration: dict[str, Any],
    blocks: Sequence[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    training_universe = UNIVERSES[cast(str, configuration["training_universe"])]
    evaluation_universe = UNIVERSES[cast(str, configuration["evaluation_universe"])]
    predictions: list[pl.DataFrame] = []
    validations: list[pl.DataFrame] = []
    for block in blocks:
        training, validation = _training_and_validation(
            rows, block, evaluation_universe
        )
        predictions.append(
            _predict(
                training,
                validation,
                cast(str, configuration["model"]),
                training_universe,
                evaluation_universe,
            )
        )
        validations.append(validation)
    return _evaluate(
        pl.concat(predictions),
        pl.concat(validations),
        cast(str, configuration["model"]),
    )


def _choose_finalists(rows: Sequence[dict[str, Any]]) -> list[str]:
    core = [
        row
        for row in rows
        if row["evaluation_universe"] == "CORE_6"
        and row["model"] == "FULLY_POOLED_LOCAL_RIDGE"
    ]
    broad = [
        row
        for row in rows
        if row["training_universe"] == "ALL_20"
        and row["evaluation_universe"] == "ALL_20"
        and row["model"] in {"LOCAL_RIDGE", "FULLY_POOLED_LOCAL_RIDGE"}
    ]
    group = [
        row
        for row in rows
        if row["training_universe"] == "ALL_20"
        and row["evaluation_universe"] == "ALL_20"
        and row["model"] == "GROUP_POOLED_RIDGE"
    ]
    if not core or not broad or len(group) != 1:
        raise ValueError("mandatory finalist categories are incomplete")
    selected = [
        max(core, key=lambda item: item["skill_versus_zero"]),
        max(broad, key=lambda item: item["equal_group_then_instrument_skill"]),
        group[0],
    ]
    return list(dict.fromkeys(cast(str, item["configuration_id"]) for item in selected))


def _write_result(
    path: Path,
    manifest_path: Path,
    manifest_sha256: str,
    rows: Sequence[dict[str, Any]],
    finalist_ids: Sequence[str],
) -> None:
    development = [row for row in rows if row["stage"] == "DEVELOPMENT"]
    terminal = [row for row in rows if row["stage"] == "TERMINAL_POST_HOC"]
    lines = [
        "# LAB-U universe experiment",
        "",
        "STATUS: COMPLETE — EXPLORATORY_POST_HOC_ONLY",
        "",
        "This is hypothesis generation over scientifically consumed historical IBKR evidence. "
        "It is not confirmation, promotion, a second holdout, or a decision-grade conclusion.",
        "",
        f"- Input LAB manifest: {manifest_path}",
        f"- Input LAB manifest SHA-256: {manifest_sha256}",
        f"- Development configurations attempted: {len(development)}",
        f"- Frozen terminal finalists: {len(finalist_ids)}",
        "",
        "## Development results",
        "",
        (
            "| Train | Evaluate | Model | Equal-instrument skill | "
            "Equal-group/instrument skill | Positive blocks | Positive instruments |"
        ),
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in development:
        lines.append(
            f"| {row['training_universe']} | {row['evaluation_universe']} | {row['model']} "
            f"| {row['skill_versus_zero']:.8g} "
            f"| {row['equal_group_then_instrument_skill']:.8g} "
            f"| {row['positive_chronological_block_count']}/{row['chronological_block_count']} "
            f"| {row['positive_instrument_count']}/{row['instrument_count']} |"
        )
    lines.extend(
        [
            "",
            "## Former consumed holdout as terminal post-hoc development block",
            "",
            "| Train | Evaluate | Model | Equal-instrument skill | Equal-group/instrument skill |",
            "|---|---|---|---:|---:|",
        ]
    )
    for row in terminal:
        lines.append(
            f"| {row['training_universe']} | {row['evaluation_universe']} | {row['model']} "
            f"| {row['skill_versus_zero']:.8g} "
            f"| {row['equal_group_then_instrument_skill']:.8g} |"
        )
    lines.extend(
        [
            "",
            "Failures here concern exploratory target/model eligibility only. They do not justify "
            "removing any instrument from future native capture.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _run(config: dict[str, Any]) -> None:
    manifest_path = Path(config["manifest_path"])
    manifest_sha256 = cast(str, config["manifest_sha256"])
    output_root = Path(config["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    register = output_root / "run-register.jsonl"
    freeze_path = output_root / "finalist-freeze.json"
    deliverables = (
        register,
        freeze_path,
        output_root / "universe-matrix.parquet",
        output_root / "group-results.parquet",
        output_root / "result.md",
    )
    existing = [path for path in deliverables if path.exists()]
    if existing:
        raise FileExistsError(f"LAB-U outputs are create-only for a run: {existing}")

    features, context, targets = _load(
        manifest_path, manifest_sha256, ALL_PRE_TERMINAL_BLOCKS
    )
    rows_by_universe = {
        name: _rows_for_universe(features, context, targets, universe)
        for name, universe in UNIVERSES.items()
    }
    matrix_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    configurations: dict[str, dict[str, Any]] = {}
    for training_name, evaluation_name in MATRICES:
        for model in MODELS:
            configuration = _configuration(training_name, evaluation_name, model)
            identifier = configuration_id(configuration)
            configurations[identifier] = configuration
            try:
                result, details = _run_configuration(
                    rows_by_universe[training_name],
                    configuration,
                    DEVELOPMENT_BLOCKS,
                )
            except Exception as error:
                append_attempt(
                    register,
                    workstream="LAB-U",
                    configuration=configuration,
                    result={
                        "status": "FAILED",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    manifest_sha256=manifest_sha256,
                )
                raise
            append_attempt(
                register,
                workstream="LAB-U",
                configuration=configuration,
                result=result,
                manifest_sha256=manifest_sha256,
            )
            flattened = _flatten_result(
                configuration, identifier, "DEVELOPMENT", result
            )
            matrix_rows.append(flattened)
            detail_rows.extend(
                {
                    **item,
                    "stage": "DEVELOPMENT",
                    "configuration_id": identifier,
                    "training_universe": training_name,
                    "evaluation_universe": evaluation_name,
                    "model": model,
                }
                for item in details
            )

    finalist_ids = _choose_finalists(matrix_rows)
    freeze_finalists(
        register,
        freeze_path,
        workstream="LAB-U",
        finalist_configuration_ids=finalist_ids,
        manifest_sha256=manifest_sha256,
    )
    freeze_sha256 = _sha256(freeze_path)
    terminal_features, terminal_context, terminal_targets = _load(
        manifest_path,
        manifest_sha256,
        (*ALL_PRE_TERMINAL_BLOCKS, TERMINAL_BLOCK),
        finalist_freeze=freeze_path,
        finalist_freeze_sha256=freeze_sha256,
        finalist_configuration_id=finalist_ids[0],
    )
    terminal_rows_by_universe = {
        name: _rows_for_universe(
            terminal_features, terminal_context, terminal_targets, universe
        )
        for name, universe in UNIVERSES.items()
    }
    for identifier in finalist_ids:
        configuration = configurations[identifier]
        result, details = _run_configuration(
            terminal_rows_by_universe[cast(str, configuration["training_universe"])],
            configuration,
            (TERMINAL_BLOCK,),
        )
        append_attempt(
            register,
            workstream="LAB-U",
            configuration=configuration,
            result=result,
            manifest_sha256=manifest_sha256,
        )
        flattened = _flatten_result(
            configuration, identifier, "TERMINAL_POST_HOC", result
        )
        matrix_rows.append(flattened)
        detail_rows.extend(
            {
                **item,
                "stage": "TERMINAL_POST_HOC",
                "configuration_id": identifier,
                "training_universe": configuration["training_universe"],
                "evaluation_universe": configuration["evaluation_universe"],
                "model": configuration["model"],
            }
            for item in details
        )

    pl.DataFrame(matrix_rows).write_parquet(output_root / "universe-matrix.parquet")
    pl.DataFrame(detail_rows).write_parquet(output_root / "group-results.parquet")
    _write_result(
        output_root / "result.md",
        manifest_path,
        manifest_sha256,
        matrix_rows,
        finalist_ids,
    )


def _smoke(config: dict[str, Any]) -> None:
    manifest_path = Path(config["manifest_path"])
    manifest_sha256 = cast(str, config["manifest_sha256"])
    instruments = CORE_6[:2]
    features, context, targets = _load(
        manifest_path,
        manifest_sha256,
        ("TRAINING_ONLY", "DEV_1"),
        instruments=instruments,
    )
    rows = _rows_for_universe(features, context, targets, instruments)
    training, validation = _training_and_validation(rows, "DEV_1", instruments)
    predictions = _predict(
        training,
        validation.head(200),
        "FULLY_POOLED_LOCAL_RIDGE",
        instruments,
        instruments,
    )
    result, _ = _evaluate(predictions, validation.head(200), "FULLY_POOLED_LOCAL_RIDGE")
    print(json.dumps(result, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("smoke", "run"))
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    value = json.loads(arguments.config.read_bytes())
    if arguments.command == "smoke":
        _smoke(value)
    else:
        _run(value)


if __name__ == "__main__":
    main()
