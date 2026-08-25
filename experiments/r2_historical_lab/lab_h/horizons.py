"""LAB-H horizon, cadence, and overlap screen over the authenticated LAB-0 dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.lab_0.baseline import (
    ORIGINAL_TARGETS,
    _fit_preprocessing,
    _transform,
    _weights,
)
from experiments.r2_historical_lab.lab_0.harness import (
    TERMINAL_BLOCK,
    append_attempt,
    authenticate_manifest,
    configuration_id,
    evaluate_against_zero,
    freeze_finalists,
    load_parts,
)

WORKSTREAM = "LAB-H"
MANIFEST_SHA256 = "462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072"
DEVELOPMENT_BLOCKS = ("DEV_1", "DEV_2", "DEV_3")
ALPHA_GRID = (0.01, 0.1, 1.0, 10.0)
INDICATOR_FEATURES = {
    "return_60s_available",
    "return_300s_available",
    "source_active",
    "quality_healthy",
    "gap_known_by_cutoff",
}
LOCAL_FEATURES = (
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


@dataclass(frozen=True, slots=True)
class HorizonConfig:
    base_sha: str
    manifest_path: Path
    manifest_sha256: str
    output_root: Path
    horizons_minutes: tuple[int, ...]
    finalist_count: int

    @classmethod
    def load(cls, path: Path) -> HorizonConfig:
        value = json.loads(path.read_bytes())
        if value["evidence_label"] != LABEL or value["source_class"] != SOURCE_CLASS:
            raise ValueError("LAB-H configuration crosses the exploratory source boundary")
        if value["manifest_sha256"] != MANIFEST_SHA256:
            raise ValueError("LAB-H configuration does not bind the canonical LAB-0 manifest")
        horizons = tuple(int(item) for item in value["horizons_minutes"])
        if horizons != (5, 15, 30, 60):
            raise ValueError("LAB-H requires the fixed 5/15/30/60-minute screen")
        return cls(
            base_sha=str(value["base_sha"]),
            manifest_path=Path(value["manifest_path"]),
            manifest_sha256=str(value["manifest_sha256"]),
            output_root=Path(value["output_root"]),
            horizons_minutes=horizons,
            finalist_count=int(value["finalist_count"]),
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _block_starts(manifest: dict[str, Any]) -> dict[str, datetime]:
    return {
        str(item["name"]): datetime.fromisoformat(str(item["start"]))
        for item in manifest["fold_blocks"]
    }


def _block_ends(manifest: dict[str, Any]) -> dict[str, datetime]:
    return {
        str(item["name"]): datetime.fromisoformat(str(item["end"]))
        for item in manifest["fold_blocks"]
    }


def _fold_membership(
    manifest: dict[str, Any],
    baseline_rows: pl.DataFrame,
) -> dict[str, tuple[pl.DataFrame, pl.DataFrame]]:
    authority = manifest["terminal_foundation"]
    foundation_path = Path(str(authority["path"]))
    if _sha256(foundation_path) != authority["file_sha256"]:
        raise ValueError("terminal foundation bytes differ from the LAB-0 manifest")
    document = json.loads(foundation_path.read_bytes())
    if document["source_class"] != SOURCE_CLASS:
        raise ValueError("terminal foundation source class differs from LAB-H")
    references = document["payload"]["children"]["folds"]
    if [reference["file_sha256"] for reference in references] != authority["fold_child_sha256"]:
        raise ValueError("terminal fold children differ from the LAB-0 manifest")
    fold_rows: list[dict[str, Any]] = []
    root = foundation_path.parent.resolve()
    for reference in references:
        path = (root / reference["file"]).resolve()
        if root not in path.parents or _sha256(path) != reference["file_sha256"]:
            raise ValueError(f"terminal fold child bytes differ from LAB-0: {path}")
        payloads = pl.read_parquet(path, columns=["payload"])["payload"].to_list()
        fold_rows.extend(json.loads(payload) for payload in payloads)
    if len(fold_rows) != len(DEVELOPMENT_BLOCKS):
        raise ValueError("LAB-0 authority does not contain exactly three development folds")
    membership: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}
    for block, fold in zip(
        DEVELOPMENT_BLOCKS,
        sorted(fold_rows, key=lambda item: str(item["validation_start"])),
        strict=True,
    ):
        membership[block] = (
            baseline_rows.filter(pl.col("target_id").is_in(fold["training_target_ids"]))
            .select("instrument_id", "decision_time")
            .unique(),
            baseline_rows.filter(pl.col("target_id").is_in(fold["validation_target_ids"]))
            .select("instrument_id", "decision_time")
            .unique(),
        )
    return membership


def _load_joined(
    config: HorizonConfig,
    *,
    blocks: Sequence[str],
    horizons: Sequence[int],
    finalist_freeze: Path | None = None,
    expected_finalist_freeze_sha256: str | None = None,
    configuration_id: str | None = None,
) -> pl.DataFrame:
    loader = {
        "manifest_path": config.manifest_path,
        "expected_manifest_sha256": config.manifest_sha256,
        "instruments": ORIGINAL_TARGETS,
        "blocks": blocks,
        "finalist_freeze": finalist_freeze,
        "expected_finalist_freeze_sha256": expected_finalist_freeze_sha256,
        "configuration_id": configuration_id,
    }
    features = load_parts(**loader, kind="feature").collect()
    targets = load_parts(**loader, kind="target", horizons=horizons).collect()
    return targets.join(
        features,
        on=["instrument_id", "decision_time"],
        how="left",
        validate="m:1",
    ).sort("decision_time", "instrument_id")


def _identity(
    frame: pl.DataFrame,
    instruments: Sequence[str],
) -> np.ndarray:
    positions = {instrument: index for index, instrument in enumerate(instruments)}
    matrix = np.zeros((frame.height, len(instruments)), dtype=float)
    for row_index, instrument in enumerate(frame["instrument_id"]):
        matrix[row_index, positions[str(instrument)]] = 1.0
    return matrix


def _selected_alpha(training: pl.DataFrame, *, pooled: bool) -> float:
    ordered = training.sort("decision_time", "instrument_id", "target_id")
    position = max(0, ordered.height - 20)
    validation_start = cast(datetime, ordered[position, "decision_time"])
    inner_validation = ordered.filter(pl.col("decision_time") >= validation_start)
    inner_fit = ordered.filter(
        (pl.col("decision_time") < validation_start)
        & (pl.col("target_end") <= validation_start)
        & (pl.col("target_available_at") <= validation_start)
    )
    if inner_fit.height < 100 or inner_validation.height < 20:
        raise ValueError("insufficient chronological inner-selection support")
    weights = _weights(inner_fit, pooled)
    x_fit, state = _fit_preprocessing(
        inner_fit.select(LOCAL_FEATURES).to_numpy().astype(float),
        LOCAL_FEATURES,
        INDICATOR_FEATURES,
        weights,
    )
    x_validation = _transform(
        inner_validation.select(LOCAL_FEATURES).to_numpy().astype(float),
        state,
    )
    if pooled:
        x_fit = np.column_stack((x_fit, _identity(inner_fit, ORIGINAL_TARGETS)))
        x_validation = np.column_stack(
            (x_validation, _identity(inner_validation, ORIGINAL_TARGETS))
        )
    y_fit = inner_fit["target_return"].to_numpy()
    y_validation = inner_validation["target_return"].to_numpy()
    scores = []
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
        scores.append((loss, -alpha, alpha))
    return min(scores)[2]


def _fit_predict(
    training: pl.DataFrame,
    validation: pl.DataFrame,
    *,
    pooled: bool,
) -> pl.DataFrame:
    if pooled:
        scopes = ((None, training, validation),)
    else:
        scopes = tuple(
            (
                instrument,
                training.filter(pl.col("instrument_id") == instrument),
                validation.filter(pl.col("instrument_id") == instrument),
            )
            for instrument in ORIGINAL_TARGETS
        )
    predictions: list[pl.DataFrame] = []
    for _, fit_rows, forecast_rows in scopes:
        if fit_rows.height < 100 or forecast_rows.is_empty():
            continue
        alpha = _selected_alpha(fit_rows, pooled=pooled)
        weights = _weights(fit_rows, pooled)
        x_fit, state = _fit_preprocessing(
            fit_rows.select(LOCAL_FEATURES).to_numpy().astype(float),
            LOCAL_FEATURES,
            INDICATOR_FEATURES,
            weights,
        )
        x_forecast = _transform(
            forecast_rows.select(LOCAL_FEATURES).to_numpy().astype(float),
            state,
        )
        if pooled:
            x_fit = np.column_stack((x_fit, _identity(fit_rows, ORIGINAL_TARGETS)))
            x_forecast = np.column_stack((x_forecast, _identity(forecast_rows, ORIGINAL_TARGETS)))
        model = Ridge(
            alpha=alpha,
            solver="lsqr",
            tol=1e-8,
            max_iter=10_000,
            fit_intercept=not pooled,
        )
        model.fit(
            x_fit,
            fit_rows["target_return"].to_numpy(),
            sample_weight=np.asarray(state["weights"], dtype=float),
        )
        predictions.append(
            forecast_rows.select("instrument_id", "decision_time", "horizon_minutes").with_columns(
                pl.Series("expected_return", model.predict(x_forecast))
            )
        )
    if not predictions:
        raise ValueError("no LAB-H forecasts were produced")
    return pl.concat(predictions).sort("decision_time", "instrument_id")


def _oof_predictions(
    rows: pl.DataFrame,
    fold_membership: dict[str, tuple[pl.DataFrame, pl.DataFrame]],
    block_starts: dict[str, datetime],
    terminal_start: datetime,
    *,
    pooled: bool,
) -> pl.DataFrame:
    frames = []
    for block in DEVELOPMENT_BLOCKS:
        training_keys, validation_keys = fold_membership[block]
        validation = _fold_selected_rows(rows, validation_keys).filter(
            pl.col("target_available_at") < terminal_start
        )
        training = _fold_selected_rows(rows, training_keys).filter(
            pl.col("target_available_at") <= block_starts[block]
        )
        frames.append(_fit_predict(training, validation, pooled=pooled))
    return pl.concat(frames).sort("decision_time", "instrument_id")


def _terminal_predictions(
    rows: pl.DataFrame,
    terminal_start: datetime,
    terminal_end: datetime,
    *,
    pooled: bool,
) -> pl.DataFrame:
    terminal = _mature_block_rows(rows, TERMINAL_BLOCK, terminal_end)
    training = rows.filter(
        pl.col("target_valid")
        & pl.col("target_return").is_not_null()
        & pl.col("target_available_at").is_not_null()
        & (pl.col("target_available_at") <= terminal_start)
        & (pl.col("decision_time") < terminal_start)
    )
    return _fit_predict(training, terminal, pooled=pooled)


def _mature_block_rows(rows: pl.DataFrame, block: str, block_end: datetime) -> pl.DataFrame:
    return rows.filter(
        (pl.col("block") == block)
        & pl.col("target_valid")
        & pl.col("target_return").is_not_null()
        & pl.col("target_available_at").is_not_null()
        & (pl.col("target_available_at") <= block_end)
    )


def _fold_selected_rows(rows: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    return rows.join(keys, on=["instrument_id", "decision_time"], how="semi").filter(
        pl.col("target_valid")
        & pl.col("target_return").is_not_null()
        & pl.col("target_available_at").is_not_null()
    )


def _development_targets(
    rows: pl.DataFrame,
    fold_membership: dict[str, tuple[pl.DataFrame, pl.DataFrame]],
    terminal_start: datetime,
) -> pl.DataFrame:
    return pl.concat(
        [
            _fold_selected_rows(rows, fold_membership[block][1]).filter(
                pl.col("target_available_at") < terminal_start
            )
            for block in DEVELOPMENT_BLOCKS
        ]
    ).sort("decision_time", "instrument_id")


def _phase(frame: pl.DataFrame, cadence_minutes: int, offset: int) -> pl.DataFrame:
    epoch_minute = pl.col("decision_time").dt.epoch("s") // 60
    return frame.filter((epoch_minute % cadence_minutes) == offset)


def _effective_opportunities(targets: pl.DataFrame) -> tuple[int, float]:
    valid = targets.filter(pl.col("target_valid")).sort("instrument_id", "decision_time")
    kept = 0
    for instrument in ORIGINAL_TARGETS:
        next_time: datetime | None = None
        for row in valid.filter(pl.col("instrument_id") == instrument).iter_rows(named=True):
            decision = cast(datetime, row["decision_time"])
            if next_time is None or decision >= next_time:
                kept += 1
                next_time = cast(datetime, row["target_end"])
    if valid.is_empty():
        return 0, 0.0
    return kept, 1.0 - kept / valid.height


def _evaluation_row(
    predictions: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    model_name: str,
    horizon: int,
    split: str,
    cadence: str,
    phase_offset: int | None,
) -> dict[str, object]:
    result = evaluate_against_zero(predictions, targets, model_name=model_name)
    effective, overlap = _effective_opportunities(targets)
    return {
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "split": split,
        "horizon_minutes": horizon,
        "cadence": cadence,
        "phase_offset": phase_offset,
        "model_name": model_name,
        "zero_mse": result["zero_return_instrument_balanced_mse"],
        "model_mse": result["model_instrument_balanced_mse"],
        "direct_delta_mse_versus_zero": result["direct_delta_mse_versus_zero"],
        "skill_versus_zero": result["skill_versus_zero"],
        "positive_block_count": result["positive_chronological_block_count"],
        "block_count": result["chronological_block_count"],
        "positive_instrument_count": result["positive_instrument_count"],
        "instrument_count": result["instrument_count"],
        "calibration_slope": result["calibration_slope"],
        "spearman_correlation": result["spearman_correlation"],
        "forecast_coverage": result["forecast_coverage"],
        "support": result["support"],
        "effective_opportunity_count": effective,
        "target_overlap_ratio": overlap,
        "best_instrument_contribution": result["best_instrument_contribution"],
        "best_period_contribution": result["best_period_contribution"],
    }


def _rank_horizons(screen: pl.DataFrame, finalist_count: int) -> tuple[int, ...]:
    best = (
        screen.group_by("horizon_minutes")
        .agg(
            pl.col("skill_versus_zero").max().alias("skill"),
            pl.col("positive_block_count").max().alias("blocks"),
            pl.col("positive_instrument_count").max().alias("instruments"),
        )
        .filter(pl.col("blocks") > 0)
        .sort(
            "blocks",
            "instruments",
            "skill",
            "horizon_minutes",
            descending=(True, True, True, False),
        )
    )
    if best.height < finalist_count:
        raise ValueError("fewer eligible horizons than the requested finalist count")
    return tuple(int(value) for value in best["horizon_minutes"].head(finalist_count))


def _write_parquet(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path, compression="zstd", statistics=True)


def _format_metric(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _write_result(
    path: Path,
    horizon_screen: pl.DataFrame,
    cadence_screen: pl.DataFrame,
    finalists: Sequence[int],
    baseline: dict[str, Any],
    *,
    smoke: bool,
) -> None:
    lines = [
        "# LAB-H horizon and overlap screen",
        "",
        f"Status: **{LABEL}**",
        f"Source class: `{SOURCE_CLASS}`",
        "",
        "This is post-hoc hypothesis generation over scientifically consumed "
        "historical IBKR input. It is not confirmation, a second holdout, promotion, "
        "or a decision-grade conclusion.",
        "",
        "## Authenticated LAB-0 minimum trust",
        "",
        "LAB-H reused LAB-0's compact authenticated reconstruction; it did not replay the "
        "retained R2 ancestry. LAB-0 observed support "
        f"`{baseline['observed']['support']}`, ZERO_RETURN MSE "
        f"`{baseline['observed']['ZERO_RETURN']}`, POOLED_LOCAL_RIDGE MSE "
        f"`{baseline['observed']['POOLED_LOCAL_RIDGE']}`, and LOCAL_RIDGE MSE "
        f"`{baseline['observed']['LOCAL_RIDGE']}`, preserving ZERO < POOLED < LOCAL.",
        "",
        "The screen below is a new exploratory refit that re-applies the original alpha-selection "
        "policy for each horizon; its 15-minute Ridge values are not a second baseline replay.",
        "",
        "## Pre-holdout horizon screen",
        "",
        "| Horizon | Model | Zero MSE | Model MSE | Skill vs zero | Positive blocks | "
        "Positive instruments | Calibration slope | Spearman | Coverage | Support | "
        "Effective opportunities | Overlap ratio |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in horizon_screen.sort("horizon_minutes", "model_name").iter_rows(named=True):
        lines.append(
            "| "
            + " | ".join(
                _format_metric(row[name])
                for name in (
                    "horizon_minutes",
                    "model_name",
                    "zero_mse",
                    "model_mse",
                    "skill_versus_zero",
                    "positive_block_count",
                    "positive_instrument_count",
                    "calibration_slope",
                    "spearman_correlation",
                    "forecast_coverage",
                    "support",
                    "effective_opportunity_count",
                    "target_overlap_ratio",
                )
            )
            + " |"
        )
    lines.extend(("", f"Frozen finalist horizons: `{list(finalists)}`.", ""))
    non_overlapping = cadence_screen.filter(
        (pl.col("split") == "PRE_HOLDOUT") & (pl.col("cadence") == "NON_OVERLAPPING_ALL_PHASES")
    )
    if not non_overlapping.is_empty():
        lines.extend(
            (
                "## Pre-holdout non-overlapping phase distribution",
                "",
                "| Horizon | Model | Offsets | Minimum skill | Mean skill | Maximum skill | "
                "Positive offsets |",
                "|---:|---|---:|---:|---:|---:|---:|",
            )
        )
        distribution = non_overlapping.group_by("horizon_minutes", "model_name").agg(
            pl.len().alias("offsets"),
            pl.col("skill_versus_zero").min().alias("minimum_skill"),
            pl.col("skill_versus_zero").mean().alias("mean_skill"),
            pl.col("skill_versus_zero").max().alias("maximum_skill"),
            (pl.col("skill_versus_zero") > 0).sum().alias("positive_offsets"),
        )
        for row in distribution.sort("horizon_minutes", "model_name").iter_rows(named=True):
            lines.append(
                "| "
                + " | ".join(
                    _format_metric(row[name])
                    for name in (
                        "horizon_minutes",
                        "model_name",
                        "offsets",
                        "minimum_skill",
                        "mean_skill",
                        "maximum_skill",
                        "positive_offsets",
                    )
                )
                + " |"
            )
        lines.append("")
    terminal = cadence_screen.filter(
        (pl.col("split") == "FORMER_HOLDOUT_POST_HOC") & (pl.col("cadence") == "EVERY_1_MINUTE")
    )
    lines.extend(("## Former-holdout finalist results", ""))
    if terminal.is_empty():
        lines.append(
            "Not accessed in this smoke run." if smoke else "No terminal results were produced."
        )
    else:
        lines.extend(
            (
                "| Horizon | Model | Zero MSE | Model MSE | Skill vs zero | Positive instruments | "
                "Calibration slope | Spearman | Coverage |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
            )
        )
        for row in terminal.sort("horizon_minutes", "model_name").iter_rows(named=True):
            lines.append(
                "| "
                + " | ".join(
                    _format_metric(row[name])
                    for name in (
                        "horizon_minutes",
                        "model_name",
                        "zero_mse",
                        "model_mse",
                        "skill_versus_zero",
                        "positive_instrument_count",
                        "calibration_slope",
                        "spearman_correlation",
                        "forecast_coverage",
                    )
                )
                + " |"
            )
    lines.extend(
        (
            "",
            "## Interpretation boundary",
            "",
            "A horizon is deprioritised when it has no positive pre-holdout block or "
            "when an aggregate improvement is not present across the full non-overlapping "
            "offset distribution. Any surviving pattern is only a hypothesis for a future "
            "untouched native or future-data experiment.",
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(config: HorizonConfig, *, smoke: bool = False) -> dict[str, object]:
    if smoke:
        config = HorizonConfig(
            base_sha=config.base_sha,
            manifest_path=config.manifest_path,
            manifest_sha256=config.manifest_sha256,
            output_root=config.output_root.with_name(f"{config.output_root.name}-smoke"),
            horizons_minutes=config.horizons_minutes,
            finalist_count=config.finalist_count,
        )
    if config.output_root.exists():
        raise FileExistsError(f"LAB-H output is create-only: {config.output_root}")
    manifest = authenticate_manifest(config.manifest_path, config.manifest_sha256)
    block_starts = _block_starts(manifest)
    block_ends = _block_ends(manifest)
    terminal_start = block_starts[TERMINAL_BLOCK]
    register = config.output_root / "run-register.jsonl"
    horizons = (15,) if smoke else config.horizons_minutes
    loaded = _load_joined(
        config,
        blocks=(*DEVELOPMENT_BLOCKS, "TRAINING_ONLY"),
        horizons=horizons,
    )
    fold_membership = _fold_membership(
        manifest,
        loaded.filter(pl.col("horizon_minutes") == 15),
    )
    horizon_rows: list[dict[str, object]] = []
    predictions_by_horizon: dict[int, dict[str, pl.DataFrame]] = {}
    targets_by_horizon: dict[int, pl.DataFrame] = {}
    for horizon in horizons:
        rows = loaded.filter(pl.col("horizon_minutes") == horizon)
        targets = _development_targets(rows, fold_membership, terminal_start)
        targets_by_horizon[horizon] = targets
        local = _oof_predictions(
            rows,
            fold_membership,
            block_starts,
            terminal_start,
            pooled=False,
        )
        pooled = _oof_predictions(
            rows,
            fold_membership,
            block_starts,
            terminal_start,
            pooled=True,
        )
        predictions_by_horizon[horizon] = {
            "LOCAL_RIDGE": local,
            "POOLED_LOCAL_RIDGE": pooled,
        }
        configuration = {"stage": "HORIZON_SCREEN", "horizon_minutes": horizon}
        for model_name, predictions in predictions_by_horizon[horizon].items():
            evaluation = evaluate_against_zero(predictions, targets, model_name=model_name)
            append_attempt(
                register,
                workstream=WORKSTREAM,
                configuration=configuration,
                result=evaluation,
                manifest_sha256=config.manifest_sha256,
            )
            horizon_rows.append(
                _evaluation_row(
                    predictions,
                    targets,
                    model_name=model_name,
                    horizon=horizon,
                    split="PRE_HOLDOUT",
                    cadence="EVERY_1_MINUTE",
                    phase_offset=None,
                )
            )
    horizon_screen = pl.DataFrame(horizon_rows)
    finalists = _rank_horizons(horizon_screen, min(config.finalist_count, len(horizons)))
    finalist_ids = [
        configuration_id({"stage": "HORIZON_SCREEN", "horizon_minutes": horizon})
        for horizon in finalists
    ]
    freeze_path = config.output_root / "finalists.json"
    freeze_finalists(
        register,
        freeze_path,
        workstream=WORKSTREAM,
        finalist_configuration_ids=finalist_ids,
        manifest_sha256=config.manifest_sha256,
    )
    freeze_sha = _sha256(freeze_path)

    cadence_rows: list[dict[str, object]] = []
    for horizon in finalists:
        targets = targets_by_horizon[horizon]
        for model_name, predictions in predictions_by_horizon[horizon].items():
            for cadence_name, cadence_minutes, offsets in (
                ("EVERY_1_MINUTE", 1, (0,)),
                ("EVERY_5_MINUTES_UTC_PHASE_0", 5, (0,)),
                ("NON_OVERLAPPING_ALL_PHASES", horizon, tuple(range(horizon))),
            ):
                for offset in offsets:
                    cadence_rows.append(
                        _evaluation_row(
                            _phase(predictions, cadence_minutes, offset),
                            _phase(targets, cadence_minutes, offset),
                            model_name=model_name,
                            horizon=horizon,
                            split="PRE_HOLDOUT",
                            cadence=cadence_name,
                            phase_offset=offset,
                        )
                    )

    terminal_rows: list[dict[str, object]] = []
    if not smoke:
        for horizon, identifier in zip(finalists, finalist_ids, strict=True):
            terminal_data = _load_joined(
                config,
                blocks=(*DEVELOPMENT_BLOCKS, "TRAINING_ONLY", TERMINAL_BLOCK),
                horizons=(horizon,),
                finalist_freeze=freeze_path,
                expected_finalist_freeze_sha256=freeze_sha,
                configuration_id=identifier,
            )
            terminal_targets = _mature_block_rows(
                terminal_data,
                TERMINAL_BLOCK,
                block_ends[TERMINAL_BLOCK],
            )
            for model_name, pooled in (
                ("LOCAL_RIDGE", False),
                ("POOLED_LOCAL_RIDGE", True),
            ):
                predictions = _terminal_predictions(
                    terminal_data,
                    block_starts[TERMINAL_BLOCK],
                    block_ends[TERMINAL_BLOCK],
                    pooled=pooled,
                )
                terminal_evaluation = evaluate_against_zero(
                    predictions,
                    terminal_targets,
                    model_name=model_name,
                )
                append_attempt(
                    register,
                    workstream=WORKSTREAM,
                    configuration={"stage": "HORIZON_SCREEN", "horizon_minutes": horizon},
                    result=terminal_evaluation,
                    manifest_sha256=config.manifest_sha256,
                )
                for cadence_name, cadence_minutes, offsets in (
                    ("EVERY_1_MINUTE", 1, (0,)),
                    ("EVERY_5_MINUTES_UTC_PHASE_0", 5, (0,)),
                    ("NON_OVERLAPPING_ALL_PHASES", horizon, tuple(range(horizon))),
                ):
                    for offset in offsets:
                        terminal_rows.append(
                            _evaluation_row(
                                _phase(predictions, cadence_minutes, offset),
                                _phase(terminal_targets, cadence_minutes, offset),
                                model_name=model_name,
                                horizon=horizon,
                                split="FORMER_HOLDOUT_POST_HOC",
                                cadence=cadence_name,
                                phase_offset=offset,
                            )
                        )

    cadence_screen = pl.DataFrame((*cadence_rows, *terminal_rows))
    config.output_root.mkdir(parents=True, exist_ok=True)
    _write_parquet(horizon_screen, config.output_root / "horizon-screen.parquet")
    _write_parquet(cadence_screen, config.output_root / "cadence-screen.parquet")
    summary = {
        "status": "COMPLETE",
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "base_sha": config.base_sha,
        "manifest_path": str(config.manifest_path),
        "manifest_sha256": config.manifest_sha256,
        "input_lab_baseline": manifest["baseline_reconstruction"],
        "horizons_attempted": list(horizons),
        "frozen_finalist_horizons": list(finalists),
        "finalist_freeze_sha256": freeze_sha,
        "horizon_screen_rows": horizon_screen.height,
        "cadence_screen_rows": cadence_screen.height,
        "terminal_accessed": not smoke,
        "smoke": smoke,
    }
    (config.output_root / "run-summary.json").write_bytes(_canonical_json(summary))
    _write_result(
        config.output_root / "result.md",
        horizon_screen,
        cadence_screen,
        finalists,
        manifest["baseline_reconstruction"],
        smoke=smoke,
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("smoke", "run"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(HorizonConfig.load(args.config), smoke=args.command == "smoke"), indent=2))


if __name__ == "__main__":
    main()
