"""Lightweight shared derivation for the post-hoc R2 IBKR historical lab.

This module deliberately has no dependency on R2 receipts, promotions, reveal state, or
provider adapters. It consumes retained local bytes and emits ordinary exploratory Parquet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import pi
from pathlib import Path
from typing import Any

import polars as pl

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS

EXPECTED_BASE_SHA = "f31cf4731fc233726f45f67f54064c40965d01d7"
EXPECTED_STAGE7_DATASET_ID = "b77a0f9a7192ed756b7958666f89baa6ce16a741056fd32a3a710cdb1ba65bd3"
EXPECTED_STAGE7_MANIFEST_SHA256 = "a261211e67fba58736c00d1be4918900bb20bac3cc61a2022265120cf666cbd6"
EXPECTED_STAGE7_ROWS = 3_376_258
EXPECTED_AVAILABILITY_POLICY = "BAR_END_PLUS_DECLARED_PROVIDER_DELAY"
EXPECTED_AVAILABILITY_DELAY = "PT5M"
EXPECTED_BASELINE = {
    "support": 239_535,
    "ZERO_RETURN": 0.0000028404586671320294,
    "POOLED_LOCAL_RIDGE": 0.000002841663414474555,
    "LOCAL_RIDGE": 0.0000028481068080631273,
}
HOLDOUT_START = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 8, 1, 23, 36, tzinfo=UTC)
FEATURE_LAG = timedelta(minutes=5)
HORIZONS = (5, 15, 30, 60)


@dataclass(frozen=True, slots=True)
class LabConfig:
    stage7_manifest: Path
    terminal_run_root: Path
    output_root: Path
    base_sha: str = EXPECTED_BASE_SHA

    @classmethod
    def read(cls, path: Path) -> LabConfig:
        document = _read_json(path)
        if document["evidence_label"] != LABEL or document["source_class"] != SOURCE_CLASS:
            raise ValueError("lab configuration has the wrong evidence label or source class")
        if document["horizons_minutes"] != list(HORIZONS):
            raise ValueError("lab configuration must retain 5/15/30/60-minute horizons")
        if document["feature_lag_seconds"] != int(FEATURE_LAG.total_seconds()):
            raise ValueError("lab configuration must retain the five-minute availability lag")
        return cls(
            stage7_manifest=Path(document["stage7_manifest"]),
            terminal_run_root=Path(document["terminal_run_root"]),
            output_root=Path(document["output_root"]),
            base_sha=document["base_sha"],
        )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _safe_source_part(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if path == root.resolve() or root.resolve() not in path.parents:
        raise ValueError(f"Stage 7 part escapes its root: {relative}")
    return path


def authenticate_stage7_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Authenticate the compact Stage 7 manifest without replaying Stage 7 semantics."""

    if _sha256(path) != EXPECTED_STAGE7_MANIFEST_SHA256:
        raise ValueError("retained Stage 7 manifest SHA-256 differs from LAB-0 authority")
    manifest = _read_json(path)
    dataset = manifest["dataset"]
    availability = manifest["availability_policy"]
    if dataset["dataset_sha256"] != EXPECTED_STAGE7_DATASET_ID:
        raise ValueError("retained Stage 7 dataset identity differs from LAB-0 authority")
    if dataset["source_class"] != SOURCE_CLASS or dataset["row_count"] != EXPECTED_STAGE7_ROWS:
        raise ValueError("retained Stage 7 source class or row count differs from LAB-0 authority")
    if availability["policy"] != EXPECTED_AVAILABILITY_POLICY:
        raise ValueError("retained Stage 7 availability policy differs from LAB-0 authority")
    if availability["delay"] != EXPECTED_AVAILABILITY_DELAY:
        raise ValueError("retained Stage 7 availability delay differs from LAB-0 authority")
    parts = manifest["parts"]
    if not isinstance(parts, list) or len({item["instrument_id"] for item in parts}) != 20:
        raise ValueError("retained Stage 7 manifest does not contain the 20-instrument universe")
    return manifest, parts


def inspect_source(config: LabConfig) -> dict[str, object]:
    """Return bounded schema and schedule samples for a Stage 7 part."""

    _, parts = authenticate_stage7_manifest(config.stage7_manifest)
    first = parts[0]
    path = _safe_source_part(config.stage7_manifest.parent, first["path"])
    frame = pl.read_parquet(path, n_rows=3)
    return {
        "part": str(path),
        "schema": {name: str(dtype) for name, dtype in frame.schema.items()},
        "rows": frame.select(
            "instrument_id",
            "interval_start",
            "interval_end",
            "available_at",
            "open",
            "high",
            "low",
            "close",
            "schedule_evidence",
            "gap_disposition",
        ).to_dicts(),
    }


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not UTC-aware: {value}")
    return parsed.astimezone(UTC)


def _retained_inventory(
    config: LabConfig,
) -> tuple[dict[str, tuple[tuple[datetime, datetime], ...]], list[dict[str, Any]], str]:
    """Load source-active intervals and original folds from the compact foundation inventory."""

    foundation_path = config.terminal_run_root / "foundation"
    document = _read_json(foundation_path)
    payload = document["payload"]
    if document["source_class"] != SOURCE_CLASS:
        raise ValueError("terminal foundation source class differs from LAB-0 authority")
    intervals = {
        instrument: tuple((_parse_utc(start), _parse_utc(end)) for start, end in values)
        for instrument, values in payload["active_intervals"].items()
    }
    fold_rows: list[dict[str, Any]] = []
    for reference in payload["children"]["folds"]:
        path = _safe_source_part(config.terminal_run_root, reference["file"])
        if _sha256(path) != reference["file_sha256"]:
            raise ValueError(f"retained fold part bytes changed: {path}")
        frame = pl.read_parquet(path, columns=["payload"])
        fold_rows.extend(json.loads(raw) for raw in frame["payload"].to_list())
    if len(fold_rows) != 3:
        raise ValueError("terminal foundation does not contain the original three OOF folds")
    return intervals, fold_rows, _sha256(foundation_path)


def _opportunity_times(intervals: Sequence[tuple[datetime, datetime]]) -> pl.DataFrame:
    values: list[datetime] = []
    for start, end in intervals:
        cursor = start
        while cursor < end:
            values.append(cursor)
            cursor += timedelta(minutes=1)
    return pl.DataFrame(
        {"decision_time": values},
        schema={"decision_time": pl.Datetime("us", "UTC")},
    )


def _block_expression(blocks: Sequence[tuple[str, datetime, datetime]]) -> pl.Expr:
    expression: pl.Expr = pl.lit("TRAINING_ONLY")
    for name, start, end in reversed(blocks):
        expression = (
            pl.when(
                (pl.col("decision_time") >= start) & (pl.col("decision_time") < end)
            )
            .then(pl.lit(name))
            .otherwise(expression)
        )
    return expression.alias("block")


def _source_frame(paths: Sequence[Path]) -> pl.DataFrame:
    frame = pl.concat(
        [
            pl.read_parquet(
                path,
                columns=[
                    "instrument_id",
                    "interval_start",
                    "interval_end",
                    "available_at",
                    "open",
                    "high",
                    "low",
                    "close",
                    "schedule_evidence",
                    "gap_disposition",
                ],
            )
            for path in paths
        ],
        how="vertical",
    )
    return frame.with_columns(
        pl.col("interval_start").str.to_datetime(time_zone="UTC"),
        pl.col("interval_end").str.to_datetime(time_zone="UTC"),
        pl.col("available_at").str.to_datetime(time_zone="UTC"),
        pl.col("open", "high", "low", "close").cast(pl.Float64),
    ).sort("interval_end")


def _features(
    source: pl.DataFrame,
    intervals: Sequence[tuple[datetime, datetime]],
    instrument_id: str,
) -> pl.DataFrame:
    one_minute = timedelta(minutes=1)
    five_minutes = timedelta(minutes=5)
    contiguous_60s = pl.col("interval_end") - pl.col("interval_end").shift(1) == one_minute
    contiguous_300s = pl.col("interval_end") - pl.col("interval_end").shift(5) == five_minutes
    computed = (
        source.with_columns(
            pl.when(contiguous_60s)
            .then((pl.col("close") / pl.col("close").shift(1)).log())
            .otherwise(None)
            .alias("return_60s"),
            pl.when(contiguous_300s)
            .then((pl.col("close") / pl.col("close").shift(5)).log())
            .otherwise(None)
            .alias("return_300s"),
            (pl.col("high") / pl.col("low")).log().alias("log_range_60s"),
        )
        .with_columns(
            (pl.col("return_60s") - pl.col("return_300s")).alias(
                "return_contrast_60s_300s"
            ),
            pl.when(contiguous_300s)
            .then(pl.col("return_60s").rolling_std(5))
            .otherwise(None)
            .alias("realised_std_300s"),
            pl.when(contiguous_300s)
            .then(pl.col("return_60s").abs().rolling_mean(5))
            .otherwise(None)
            .alias("mean_absolute_return_300s"),
            pl.when(contiguous_300s)
            .then(pl.col("log_range_60s").rolling_mean(5))
            .otherwise(None)
            .alias("mean_log_range_300s"),
        )
        .with_columns((pl.col("interval_end") + FEATURE_LAG).alias("decision_time"))
    )
    opportunities = _opportunity_times(intervals)
    minute = pl.col("decision_time").dt.hour() * 60 + pl.col("decision_time").dt.minute()
    day = pl.col("decision_time").dt.ordinal_day()
    return (
        opportunities.join(computed, on="decision_time", how="left")
        .with_columns(
            (minute * 2 * pi / 1440).sin().alias("utc_minute_sin"),
            (minute * 2 * pi / 1440).cos().alias("utc_minute_cos"),
            (day * 2 * pi / 366).sin().alias("utc_day_sin"),
            (day * 2 * pi / 366).cos().alias("utc_day_cos"),
            pl.lit(instrument_id).alias("instrument_id"),
            pl.lit(True).alias("source_active"),
            pl.lit(SOURCE_CLASS).alias("source_class"),
            pl.lit(LABEL).alias("evidence_label"),
        )
        .select(
            "instrument_id",
            "decision_time",
            pl.col("interval_end").alias("latest_feature_bar_end"),
            pl.col("available_at").alias("feature_available_at"),
            "return_60s",
            "return_300s",
            "return_contrast_60s_300s",
            "log_range_60s",
            "realised_std_300s",
            "mean_absolute_return_300s",
            "mean_log_range_300s",
            "utc_minute_sin",
            "utc_minute_cos",
            "utc_day_sin",
            "utc_day_cos",
            "source_active",
            "source_class",
            "evidence_label",
        )
    )


def _targets(
    source: pl.DataFrame,
    intervals: Sequence[tuple[datetime, datetime]],
    horizon_minutes: int,
    blocks: Sequence[tuple[str, datetime, datetime]],
) -> pl.DataFrame:
    opportunities = _opportunity_times(intervals)
    prices = source.select(pl.col("interval_end").alias("price_time"), "close", "available_at")
    horizon = timedelta(minutes=horizon_minutes)
    return (
        opportunities.with_columns(
            (pl.col("decision_time") + horizon).alias("target_end"),
            (pl.col("decision_time") + horizon + FEATURE_LAG).alias("target_available_at"),
        )
        .join(prices, left_on="decision_time", right_on="price_time", how="left")
        .rename({"close": "start_close", "available_at": "start_available_at"})
        .join(prices, left_on="target_end", right_on="price_time", how="left")
        .rename({"close": "end_close", "available_at": "end_available_at"})
        .with_columns(
            pl.when(
                pl.col("start_close").is_not_null()
                & pl.col("end_close").is_not_null()
                & (pl.col("start_available_at") <= pl.col("target_available_at"))
                & (pl.col("end_available_at") <= pl.col("target_available_at"))
            )
            .then((pl.col("end_close") / pl.col("start_close")).log())
            .otherwise(None)
            .alias("target_return"),
            pl.lit(horizon_minutes).cast(pl.Int16).alias("horizon_minutes"),
            _block_expression(blocks),
            pl.lit(SOURCE_CLASS).alias("source_class"),
            pl.lit(LABEL).alias("evidence_label"),
        )
        .with_columns(pl.col("target_return").is_not_null().alias("target_valid"))
        .select(
            "decision_time",
            "target_end",
            "target_available_at",
            "horizon_minutes",
            "target_return",
            "target_valid",
            "block",
            "source_class",
            "evidence_label",
        )
    )


def _slug(instrument_id: str) -> str:
    return instrument_id.replace(":", "--")


def _write_parquet(frame: pl.DataFrame, path: Path) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path, compression="zstd", statistics=True)
    return {"path": str(path), "row_count": frame.height, "sha256": _sha256(path)}


def _fold_rows(
    retained_folds: Sequence[dict[str, Any]],
) -> tuple[pl.DataFrame, list[tuple[str, datetime, datetime]]]:
    blocks: list[tuple[str, datetime, datetime]] = []
    for index, fold in enumerate(retained_folds, start=1):
        blocks.append(
            (
                f"DEV_{index}",
                _parse_utc(fold["validation_start"]),
                _parse_utc(fold["validation_end"]),
            )
        )
    blocks.append(("TERMINAL_FORMER_HOLDOUT", HOLDOUT_START, HOLDOUT_END))
    rows = []
    for horizon in HORIZONS:
        maturity = timedelta(minutes=horizon) + FEATURE_LAG
        for index, (name, start, end) in enumerate(blocks):
            rows.append(
                {
                    "block": name,
                    "original_fold_id": (
                        retained_folds[index]["fold_id"]
                        if index < len(retained_folds)
                        else None
                    ),
                    "horizon_minutes": horizon,
                    "validation_start": start,
                    "validation_end": end,
                    "training_cutoff": start,
                    "latest_training_decision_time_exclusive": start - maturity,
                    "target_maturity_seconds": int(maturity.total_seconds()),
                    "embargo_seconds": 0,
                    "terminal_selection_prohibited": name == "TERMINAL_FORMER_HOLDOUT",
                    "evidence_label": LABEL,
                }
            )
    return pl.DataFrame(rows), blocks


def replay_baseline(config: LabConfig) -> dict[str, object]:
    """Reconstruct the frozen OOF headline from the retained terminal evaluation report."""

    report_path = config.terminal_run_root / "oof/evaluation/report.json"
    report = _read_json(report_path)
    comparisons = report["comparisons"]
    observed: dict[str, float | int] = {"support": 0}
    for comparison in comparisons:
        if comparison["comparator"] == "ZERO_RETURN":
            observed["ZERO_RETURN"] = comparison["comparator_instrument_balanced_mse"]["value"]
        if comparison["candidate"] in {"LOCAL_RIDGE", "POOLED_LOCAL_RIDGE"}:
            observed[comparison["candidate"]] = comparison[
                "candidate_instrument_balanced_mse"
            ]["value"]
    # The 15 retained LOCAL_RIDGE own-support buckets partition common support exactly once.
    support = sum(
        int(bucket["row_count"])
        for bucket in report["bucket_metrics"]
        if bucket["model_family"] == "LOCAL_RIDGE" and bucket["support"] == "OWN_SUPPORT"
    )
    observed["support"] = support
    exact = observed == EXPECTED_BASELINE
    ordering = (
        observed.get("ZERO_RETURN", 1.0)
        < observed.get("POOLED_LOCAL_RIDGE", 0.0)
        < observed.get("LOCAL_RIDGE", 0.0)
    )
    result = {
        "contract": "qtrad-r2-historical-lab-baseline-replay-v1",
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "input_report": str(report_path),
        "input_report_sha256": _sha256(report_path),
        "expected": EXPECTED_BASELINE,
        "observed": observed,
        "exact": exact,
        "ordering_zero_pooled_local": ordering,
    }
    if not exact or not ordering:
        raise ValueError(f"mandatory baseline replay diverged: {result}")
    return result


def build(config: LabConfig, *, instruments: Sequence[str] | None = None) -> dict[str, object]:
    """Build the compact shared lab dataset, one source instrument at a time."""

    if config.base_sha != EXPECTED_BASE_SHA:
        raise ValueError("lab configuration is not bound to the authorised repository SHA")
    manifest, parts = authenticate_stage7_manifest(config.stage7_manifest)
    active_intervals, retained_folds, foundation_manifest_sha256 = _retained_inventory(config)
    fold_frame, blocks = _fold_rows(retained_folds)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for part in parts:
        grouped[part["instrument_id"]].append(part)
    selected = sorted(grouped) if instruments is None else list(instruments)
    if not selected or not set(selected).issubset(grouped):
        raise ValueError("requested instrument selection is empty or outside Stage 7")
    output = config.output_root
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"LAB-0 output is create-only for this run: {output}")
    output.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, object]] = []
    counts: list[dict[str, object]] = []
    for instrument in selected:
        references = grouped[instrument]
        paths = [
            _safe_source_part(config.stage7_manifest.parent, item["path"])
            for item in references
        ]
        for path, reference in zip(paths, references, strict=True):
            if _sha256(path) != reference["bytes_sha256"]:
                raise ValueError(f"consumed Stage 7 part bytes changed: {path}")
        source = _source_frame(paths)
        if source.height != sum(item["row_count"] for item in references):
            raise ValueError(f"Stage 7 part row count differs for {instrument}")
        intervals = active_intervals[instrument]
        feature_path = output / "features" / f"instrument={_slug(instrument)}" / "part.parquet"
        written.append(_write_parquet(_features(source, intervals, instrument), feature_path))
        for horizon in HORIZONS:
            target = _targets(source, intervals, horizon, blocks).with_columns(
                pl.lit(instrument).alias("instrument_id")
            )
            target_path = (
                output
                / "targets"
                / f"horizon={horizon}m"
                / f"instrument={_slug(instrument)}"
                / "part.parquet"
            )
            written.append(_write_parquet(target, target_path))
            for row in target.group_by("block").agg(
                pl.len().alias("row_count"), pl.col("target_valid").sum().alias("valid_count")
            ).to_dicts():
                counts.append({"instrument_id": instrument, "horizon_minutes": horizon, **row})
    folds_path = output / "folds" / "blocks.parquet"
    written.append(_write_parquet(fold_frame, folds_path))
    baseline = replay_baseline(config)
    baseline_path = output / "baseline-replay.json"
    baseline_path.write_bytes(_canonical_json(baseline))
    dictionary_path = output / "data-dictionary.md"
    dictionary_path.write_text(_data_dictionary(), encoding="utf-8")
    written.extend(
        [
            {"path": str(baseline_path), "row_count": 1, "sha256": _sha256(baseline_path)},
            {"path": str(dictionary_path), "row_count": 1, "sha256": _sha256(dictionary_path)},
        ]
    )
    lab_manifest = {
        "contract": "qtrad-r2-historical-lab-manifest-v1",
        "base_sha": config.base_sha,
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "authoritative": False,
        "stage7_manifest": str(config.stage7_manifest),
        "stage7_manifest_sha256": EXPECTED_STAGE7_MANIFEST_SHA256,
        "stage7_dataset_id": EXPECTED_STAGE7_DATASET_ID,
        "stage7_row_count": manifest["dataset"]["row_count"],
        "terminal_foundation_manifest": str(config.terminal_run_root / "foundation"),
        "terminal_foundation_manifest_sha256": foundation_manifest_sha256,
        "availability_policy": EXPECTED_AVAILABILITY_POLICY,
        "availability_delay": EXPECTED_AVAILABILITY_DELAY,
        "instruments": selected,
        "horizons_minutes": list(HORIZONS),
        "feature_lag_seconds": int(FEATURE_LAG.total_seconds()),
        "parts": written,
        "counts": sorted(
            counts,
            key=lambda item: (
                str(item["instrument_id"]),
                str(item["horizon_minutes"]),
                str(item["block"]),
            ),
        ),
        "baseline_replay": baseline,
        "downstream_authentication": (
            "SHA-256 this manifest, then SHA-256 only each listed part actually consumed; "
            "never reinterpret the evidence label or source class"
        ),
    }
    manifest_path = output / "lab-manifest.json"
    manifest_path.write_bytes(_canonical_json(lab_manifest))
    return {"manifest": str(manifest_path), "sha256": _sha256(manifest_path), **lab_manifest}


def _data_dictionary() -> str:
    return """# LAB-0 data dictionary

Every row is `EXPLORATORY_POST_HOC_ONLY` and source class
`IBKR_HISTORICAL_RESEARCH`. Nothing here is a second holdout, confirmation,
promotion, or decision-grade result.

## Features

Feature rows are keyed by `instrument_id, decision_time`. `decision_time` is five
minutes after `latest_feature_bar_end`; `feature_available_at` must be no later than
that decision. The local L0 inputs are the 60/300-second returns, their availability
through nullness, the return contrast, UTC cyclical terms, and `source_active`. L1
adds retained local range/volatility summaries. P0 uses the same local columns in a
pooled fit. P1 may join other instruments at the exact same `decision_time`; missing
markets remain missing and are never forward-filled.

## Targets

Target rows are keyed by `instrument_id, decision_time, horizon_minutes`. An
opportunity exists only inside a retained source-active half-open interval. A target
is valid only when both exact minute endpoints exist and were available by
`target_available_at = target_end + PT5M`. `target_return` is the log MID return.

## Folds

`DEV_1` through `DEV_3` are the original chronological pre-holdout blocks.
`TERMINAL_FORMER_HOLDOUT` is the scientifically consumed former holdout and is
selection-prohibited. `latest_training_decision_time_exclusive` supplies the
horizon-specific target-maturity purge; embargo remains the original zero seconds.

## Loading

Read only selected Parquet parts lazily. First hash `lab-manifest.json`, then hash
the listed parts actually consumed and verify their path, row count, evidence label,
and source class. Do not re-read Stage 7 or replay R2 ancestry downstream.
"""


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "smoke", "build", "replay-baseline"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--instrument", action="append")
    args = parser.parse_args(argv)
    config = LabConfig.read(args.config)
    if args.command == "inspect":
        result = inspect_source(config)
    elif args.command == "replay-baseline":
        result = replay_baseline(config)
    elif args.command == "smoke":
        _, parts = authenticate_stage7_manifest(config.stage7_manifest)
        instrument = args.instrument or [parts[0]["instrument_id"]]
        smoke_config = replace(config, output_root=config.output_root.with_name("LAB-0-smoke"))
        result = build(smoke_config, instruments=instrument)
    else:
        result = build(config, instruments=args.instrument)
    print(json.dumps(result, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
