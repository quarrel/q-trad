"""Prespecified one-minute LAB-H extension over authenticated retained local bytes."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.lab_0.baseline import ORIGINAL_TARGETS
from experiments.r2_historical_lab.lab_0.harness import (
    TERMINAL_BLOCK,
    append_attempt,
    authenticate_manifest,
    configuration_id,
    evaluate_against_zero,
    freeze_finalists,
)
from experiments.r2_historical_lab.lab_0.lab import (
    TARGET_REVISION_DELAY_SECONDS,
    TARGET_REVISION_POLICY,
    _source_frame,
    _target_disposition,
)
from experiments.r2_historical_lab.lab_h.horizons import (
    DEVELOPMENT_BLOCKS,
    MANIFEST_SHA256,
    HorizonConfig,
    _block_ends,
    _block_starts,
    _canonical_json,
    _development_targets,
    _evaluation_row,
    _fold_membership,
    _load_joined,
    _mature_block_rows,
    _oof_predictions,
    _sha256,
    _terminal_predictions,
    _write_parquet,
)
from qtrad.domain.foundation import target_identity
from qtrad.domain.market_data import PriceBasis

WORKSTREAM = "LAB-H-1M"
HORIZON_MINUTES = 1
SCAFFOLD_HORIZON_MINUTES = 15
CONFIGURATION = {"stage": "PRESPECIFIED_ONE_MINUTE_EXTENSION", "horizon_minutes": 1}
TARGET_COLUMNS = {
    "target_id",
    "target_end",
    "target_available_at",
    "horizon_minutes",
    "target_revision_policy",
    "start_close",
    "end_close",
    "target_return",
    "target_valid",
    "target_disposition",
    "block",
    "source_class",
    "evidence_label",
}


@dataclass(frozen=True, slots=True)
class OneMinuteConfig:
    repository_sha: str
    manifest_path: Path
    manifest_sha256: str
    output_root: Path

    @classmethod
    def load(cls, path: Path) -> OneMinuteConfig:
        value = cast(dict[str, object], json.loads(path.read_bytes()))
        if value["job_id"] != WORKSTREAM:
            raise ValueError("one-minute configuration has the wrong job ID")
        if value["evidence_label"] != LABEL or value["source_class"] != SOURCE_CLASS:
            raise ValueError("one-minute configuration crosses the exploratory source boundary")
        if value["horizon_minutes"] != HORIZON_MINUTES:
            raise ValueError("this extension is fixed to the one-minute horizon")
        if value["manifest_sha256"] != MANIFEST_SHA256:
            raise ValueError("one-minute configuration does not bind the canonical LAB-0 manifest")
        return cls(
            repository_sha=str(value["repository_sha"]),
            manifest_path=Path(str(value["manifest_path"])),
            manifest_sha256=str(value["manifest_sha256"]),
            output_root=Path(str(value["output_root"])),
        )


def _horizon_config(config: OneMinuteConfig) -> HorizonConfig:
    return HorizonConfig(
        base_sha=config.repository_sha,
        manifest_path=config.manifest_path,
        manifest_sha256=config.manifest_sha256,
        output_root=config.output_root,
        horizons_minutes=(SCAFFOLD_HORIZON_MINUTES,),
        finalist_count=1,
    )


def _selected_sources(
    manifest: dict[str, Any],
) -> tuple[dict[str, pl.DataFrame], list[dict[str, object]]]:
    stage7 = cast(dict[str, Any], manifest["stage7"])
    stage7_manifest = Path(str(stage7["manifest"]))
    if _sha256(stage7_manifest) != str(stage7["manifest_sha256"]):
        raise ValueError("LAB-0-bound Stage 7 manifest bytes changed")
    root = stage7_manifest.parent.resolve()
    references_by_instrument: dict[str, list[dict[str, object]]] = defaultdict(list)
    for raw_reference in cast(list[dict[str, object]], stage7["consumed_parts"]):
        instrument = str(raw_reference["instrument_id"])
        if instrument in ORIGINAL_TARGETS:
            references_by_instrument[instrument].append(raw_reference)
    if set(references_by_instrument) != set(ORIGINAL_TARGETS):
        raise ValueError("LAB-0 lacks a consumed source part for an original target")

    sources: dict[str, pl.DataFrame] = {}
    consumed: list[dict[str, object]] = []
    for instrument in ORIGINAL_TARGETS:
        paths: list[Path] = []
        expected_rows = 0
        for reference in references_by_instrument[instrument]:
            path = (root / str(reference["path"])).resolve()
            if not path.is_relative_to(root):
                raise ValueError(f"Stage 7 part escapes its manifest root: {path}")
            expected_sha = str(reference["sha256"])
            if _sha256(path) != expected_sha:
                raise ValueError(f"LAB-0-bound Stage 7 part bytes changed: {path}")
            rows = int(cast(int, reference["row_count"]))
            expected_rows += rows
            paths.append(path)
            consumed.append(
                {
                    "instrument_id": instrument,
                    "path": str(reference["path"]),
                    "row_count": rows,
                    "sha256": expected_sha,
                }
            )
        source = _source_frame(paths)
        if source.height != expected_rows:
            raise ValueError(f"Stage 7 part row count differs for {instrument}")
        sources[instrument] = source
    return sources, consumed


def _one_minute_target_id(instrument_id: str, decision_time: datetime) -> str:
    return target_identity(
        instrument_id=instrument_id,
        decision_time=decision_time,
        horizon=timedelta(minutes=HORIZON_MINUTES),
        target_basis=PriceBasis.MID,
        target_revision_policy=TARGET_REVISION_POLICY,
    )


def _one_minute_targets(
    scaffold: pl.DataFrame,
    source: pl.DataFrame,
    instrument_id: str,
) -> pl.DataFrame:
    opportunities = scaffold.filter(pl.col("instrument_id") == instrument_id).select(
        "decision_time", "block"
    )
    if opportunities.is_empty():
        raise ValueError(f"no LAB-0 opportunities for {instrument_id}")
    if opportunities.n_unique("decision_time") != opportunities.height:
        raise ValueError(f"duplicate LAB-0 opportunities for {instrument_id}")
    prices = source.select(pl.col("interval_end").alias("price_time"), "close", "available_at")
    horizon = timedelta(minutes=HORIZON_MINUTES)
    return (
        opportunities.with_columns(
            (pl.col("decision_time") + horizon).alias("target_end"),
            (
                pl.col("decision_time") + horizon + timedelta(seconds=TARGET_REVISION_DELAY_SECONDS)
            ).alias("target_available_at"),
        )
        .join(prices, left_on="decision_time", right_on="price_time", how="left")
        .rename({"close": "start_close", "available_at": "start_available_at"})
        .join(prices, left_on="target_end", right_on="price_time", how="left")
        .rename({"close": "end_close", "available_at": "end_available_at"})
        .with_columns(_target_disposition())
        .with_columns(
            pl.when(pl.col("target_disposition") == "VALID")
            .then((pl.col("end_close") / pl.col("start_close")).log())
            .otherwise(None)
            .alias("target_return"),
            pl.lit(instrument_id).alias("instrument_id"),
            pl.lit(HORIZON_MINUTES).cast(pl.Int16).alias("horizon_minutes"),
            pl.lit(TARGET_REVISION_POLICY).alias("target_revision_policy"),
            pl.lit(SOURCE_CLASS).alias("source_class"),
            pl.lit(LABEL).alias("evidence_label"),
        )
        .with_columns(
            (pl.col("target_disposition") == "VALID").alias("target_valid"),
            pl.col("decision_time")
            .map_elements(
                lambda value: _one_minute_target_id(instrument_id, value),
                return_dtype=pl.String,
            )
            .alias("target_id"),
        )
        .select(
            "target_id",
            "instrument_id",
            "decision_time",
            "target_end",
            "target_available_at",
            "horizon_minutes",
            "target_revision_policy",
            "start_close",
            "end_close",
            "target_return",
            "target_valid",
            "target_disposition",
            "block",
            "source_class",
            "evidence_label",
        )
    )


def _one_minute_rows(
    scaffold: pl.DataFrame,
    sources: dict[str, pl.DataFrame],
) -> pl.DataFrame:
    feature_columns = [name for name in scaffold.columns if name not in TARGET_COLUMNS]
    features = scaffold.select(feature_columns)
    if features.n_unique(("instrument_id", "decision_time")) != features.height:
        raise ValueError("LAB-0 feature scaffold is not unique by opportunity")
    targets = pl.concat(
        [
            _one_minute_targets(scaffold, sources[instrument], instrument)
            for instrument in ORIGINAL_TARGETS
        ],
        how="vertical",
    )
    joined = targets.join(
        features,
        on=("instrument_id", "decision_time"),
        how="inner",
        validate="1:1",
    )
    if joined.height != scaffold.height:
        raise ValueError("one-minute target derivation changed the LAB-0 opportunity scaffold")
    return joined.sort("decision_time", "instrument_id")


def _write_result(
    path: Path,
    horizon_screen: pl.DataFrame,
    cadence_screen: pl.DataFrame,
) -> None:
    lines = [
        "# LAB-H prespecified one-minute extension",
        "",
        f"Status: **{LABEL}**",
        f"Source class: `{SOURCE_CLASS}`",
        "",
        "This is gross, post-hoc model-development evidence over scientifically consumed "
        "historical IBKR MID data. It includes no costs, is not confirmation, and is not a "
        "decision-grade or executable conclusion.",
        "",
        "The one-minute target was derived from the exact retained Stage 7 one-minute bars "
        "already consumed and byte-bound by LAB-0. It uses the same endpoint and five-minute "
        "target-availability policy, LAB-0 features, retained fold decision membership, and "
        "original Ridge policy.",
        "",
        "## Results",
        "",
        "| Split | Model | Zero MSE | Model MSE | Skill vs zero | Positive blocks | "
        "Positive instruments | Calibration slope | Spearman | Coverage | Support |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = pl.concat(
        (
            horizon_screen,
            cadence_screen.filter(pl.col("split") == "FORMER_HOLDOUT_POST_HOC"),
        ),
        how="vertical",
    )
    for row in rows.sort("split", "model_name").iter_rows(named=True):
        lines.append(
            "| "
            + " | ".join(
                str(row[name])
                for name in (
                    "split",
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
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "## Interpretation boundary",
            "",
            "Direct comparison is against ZERO_RETURN. Any apparent gross signal would still "
            "need a future untouched experiment and realistic costs; this run cannot support "
            "either an effectiveness or executability claim.",
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(config: OneMinuteConfig, *, smoke: bool = False) -> dict[str, object]:
    if smoke:
        config = replace(
            config, output_root=config.output_root.with_name(f"{config.output_root.name}-smoke")
        )
    if config.output_root.exists():
        raise FileExistsError(f"one-minute LAB-H output is create-only: {config.output_root}")
    manifest = authenticate_manifest(config.manifest_path, config.manifest_sha256)
    block_starts = _block_starts(manifest)
    block_ends = _block_ends(manifest)
    terminal_start = block_starts[TERMINAL_BLOCK]
    sources, consumed_parts = _selected_sources(manifest)
    lab_h_config = _horizon_config(config)
    development_scaffold = _load_joined(
        lab_h_config,
        blocks=(*DEVELOPMENT_BLOCKS, "TRAINING_ONLY"),
        horizons=(SCAFFOLD_HORIZON_MINUTES,),
    )
    fold_membership = _fold_membership(manifest, development_scaffold)
    development_rows = _one_minute_rows(development_scaffold, sources)
    development_targets = _development_targets(development_rows, fold_membership, terminal_start)
    predictions = {
        "LOCAL_RIDGE": _oof_predictions(
            development_rows,
            fold_membership,
            block_starts,
            terminal_start,
            pooled=False,
        ),
        "POOLED_LOCAL_RIDGE": _oof_predictions(
            development_rows,
            fold_membership,
            block_starts,
            terminal_start,
            pooled=True,
        ),
    }

    register = config.output_root / "run-register.jsonl"
    horizon_rows: list[dict[str, object]] = []
    for model_name, model_predictions in predictions.items():
        evaluation = evaluate_against_zero(
            model_predictions, development_targets, model_name=model_name
        )
        append_attempt(
            register,
            workstream=WORKSTREAM,
            configuration=CONFIGURATION,
            result=evaluation,
            manifest_sha256=config.manifest_sha256,
        )
        horizon_rows.append(
            _evaluation_row(
                model_predictions,
                development_targets,
                model_name=model_name,
                horizon=HORIZON_MINUTES,
                split="PRE_HOLDOUT",
                cadence="EVERY_1_MINUTE",
                phase_offset=0,
            )
        )
    horizon_screen = pl.DataFrame(horizon_rows)

    identifier = configuration_id(CONFIGURATION)
    freeze_path = config.output_root / "finalists.json"
    freeze_finalists(
        register,
        freeze_path,
        workstream=WORKSTREAM,
        finalist_configuration_ids=[identifier],
        manifest_sha256=config.manifest_sha256,
    )
    freeze_sha = _sha256(freeze_path)

    terminal_rows: list[dict[str, object]] = []
    if not smoke:
        terminal_scaffold = _load_joined(
            lab_h_config,
            blocks=(*DEVELOPMENT_BLOCKS, "TRAINING_ONLY", TERMINAL_BLOCK),
            horizons=(SCAFFOLD_HORIZON_MINUTES,),
            finalist_freeze=freeze_path,
            expected_finalist_freeze_sha256=freeze_sha,
            configuration_id=identifier,
        )
        terminal_data = _one_minute_rows(terminal_scaffold, sources)
        terminal_targets = _mature_block_rows(
            terminal_data, TERMINAL_BLOCK, block_ends[TERMINAL_BLOCK]
        )
        for model_name, pooled in (
            ("LOCAL_RIDGE", False),
            ("POOLED_LOCAL_RIDGE", True),
        ):
            model_predictions = _terminal_predictions(
                terminal_data,
                terminal_start,
                block_ends[TERMINAL_BLOCK],
                pooled=pooled,
            )
            evaluation = evaluate_against_zero(
                model_predictions, terminal_targets, model_name=model_name
            )
            append_attempt(
                register,
                workstream=WORKSTREAM,
                configuration=CONFIGURATION,
                result=evaluation,
                manifest_sha256=config.manifest_sha256,
            )
            terminal_rows.append(
                _evaluation_row(
                    model_predictions,
                    terminal_targets,
                    model_name=model_name,
                    horizon=HORIZON_MINUTES,
                    split="FORMER_HOLDOUT_POST_HOC",
                    cadence="EVERY_1_MINUTE",
                    phase_offset=0,
                )
            )

    cadence_screen = pl.DataFrame((*horizon_rows, *terminal_rows))
    config.output_root.mkdir(parents=True, exist_ok=True)
    _write_parquet(horizon_screen, config.output_root / "horizon-screen.parquet")
    _write_parquet(cadence_screen, config.output_root / "cadence-screen.parquet")
    (config.output_root / "source-parts.json").write_bytes(
        _canonical_json(
            {
                "lab0_manifest_sha256": config.manifest_sha256,
                "stage7_manifest": manifest["stage7"]["manifest"],
                "stage7_manifest_sha256": manifest["stage7"]["manifest_sha256"],
                "consumed_parts": consumed_parts,
            }
        )
    )
    summary = {
        "status": "COMPLETE",
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "repository_sha": config.repository_sha,
        "manifest_path": str(config.manifest_path),
        "manifest_sha256": config.manifest_sha256,
        "horizon_minutes": HORIZON_MINUTES,
        "target_revision_delay_seconds": TARGET_REVISION_DELAY_SECONDS,
        "source_part_count": len(consumed_parts),
        "configuration_id": identifier,
        "finalist_freeze_sha256": freeze_sha,
        "horizon_screen_rows": horizon_screen.height,
        "cadence_screen_rows": cadence_screen.height,
        "terminal_accessed": not smoke,
        "smoke": smoke,
    }
    (config.output_root / "run-summary.json").write_bytes(_canonical_json(summary))
    _write_result(config.output_root / "result.md", horizon_screen, cadence_screen)
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("smoke", "run"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        json.dumps(run(OneMinuteConfig.load(args.config), smoke=args.command == "smoke"), indent=2)
    )


if __name__ == "__main__":
    main()
