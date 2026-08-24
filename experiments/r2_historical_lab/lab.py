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
from pathlib import Path
from typing import Any

import polars as pl

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.baseline import ORIGINAL_TARGETS
from experiments.r2_historical_lab.baseline import (
    reconstruct_baseline as reconstruct_baseline_impl,
)
from experiments.r2_historical_lab.features import (
    FEATURE_LAG,
    FEATURE_NAMES,
    add_pooled_features,
    build_context,
    build_local_features,
    opportunity_times,
)
from experiments.r2_historical_lab.harness import MANIFEST_CONTRACT
from qtrad.domain.foundation import target_identity
from qtrad.domain.market_data import PriceBasis

EXPECTED_BASE_SHA = "f31cf4731fc233726f45f67f54064c40965d01d7"
EXPECTED_STAGE7_DATASET_ID = "b77a0f9a7192ed756b7958666f89baa6ce16a741056fd32a3a710cdb1ba65bd3"
EXPECTED_STAGE7_MANIFEST_SHA256 = "a261211e67fba58736c00d1be4918900bb20bac3cc61a2022265120cf666cbd6"
EXPECTED_STAGE7_ROWS = 3_376_258
EXPECTED_FOUNDATION_SHA256 = "be5c38189d8f597322f8134934a03b04d08e135777f1efe4a6a701962bdf4d95"
EXPECTED_AVAILABILITY_POLICY = "BAR_END_PLUS_DECLARED_PROVIDER_DELAY"
EXPECTED_AVAILABILITY_DELAY = "PT5M"
EXPECTED_FEATURE_MANIFESTS = {
    "L0": "1a5419a00419d7aae65395aac2fca9070807d5e427ff82b5a93393befc9c7cc0",
    "L1": "40fa90817cac87c9c556e91683f1490237c72d27384e261455c89b4e719312af",
    "P0": "a4f91fdfaa168fe55e264fed2bb4f9bea1b9d91f10d88ab6d0807c05cd5230ff",
    "P1": "718cd8e955782ae8a334e37db56b125b3313617cbbaabf5b3a1e3d6a486cef75",
}
HOLDOUT_START = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 8, 1, 23, 36, tzinfo=UTC)
HORIZONS = (5, 15, 30, 60)
GRID_SECONDS = 60
TARGET_REVISION_DELAY_SECONDS = 300
TARGET_REVISION_POLICY = "PROVISIONAL_CONSERVATIVE"


@dataclass(frozen=True, slots=True)
class LabConfig:
    job_id: str
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
            job_id=document["job_id"],
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
) -> tuple[
    dict[str, tuple[tuple[datetime, datetime], ...]],
    dict[str, tuple[tuple[datetime, datetime], ...]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    foundation_path = config.terminal_run_root / "foundation"
    if _sha256(foundation_path) != EXPECTED_FOUNDATION_SHA256:
        raise ValueError("terminal foundation bytes differ from LAB authority")
    document = _read_json(foundation_path)
    if document["source_class"] != SOURCE_CLASS:
        raise ValueError("terminal foundation source class differs from LAB authority")
    payload = document["payload"]
    intervals = {
        instrument: tuple((_parse_utc(start), _parse_utc(end)) for start, end in values)
        for instrument, values in payload["active_intervals"].items()
    }
    gaps: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for gap in payload["provider_gaps"]:
        gaps[gap["instrument_id"]].append(
            (_parse_utc(gap["interval_start"]), _parse_utc(gap["interval_end"]))
        )
    fold_rows: list[dict[str, Any]] = []
    fold_hashes = []
    for reference in payload["children"]["folds"]:
        path = _safe_source_part(config.terminal_run_root, reference["file"])
        if _sha256(path) != reference["file_sha256"]:
            raise ValueError(f"retained fold part bytes changed: {path}")
        fold_hashes.append(reference["file_sha256"])
        frame = pl.read_parquet(path, columns=["payload"])
        fold_rows.extend(json.loads(raw) for raw in frame["payload"].to_list())
    if len(fold_rows) != 3:
        raise ValueError("terminal foundation does not contain the original three OOF folds")
    identity = {
        "file_sha256": EXPECTED_FOUNDATION_SHA256,
        "manifest_id": document["manifest_sha256"],
        "closure_id": document["closure_id"],
        "foundation_id": document["foundation_id"],
        "fold_child_sha256": fold_hashes,
    }
    return intervals, {key: tuple(value) for key, value in gaps.items()}, fold_rows, identity


def _feature_schema(config: LabConfig) -> dict[str, object]:
    schemas: dict[str, list[str]] = {}
    for feature_set, expected_sha in EXPECTED_FEATURE_MANIFESTS.items():
        path = config.terminal_run_root / f"features-{feature_set}.json"
        if _sha256(path) != expected_sha:
            raise ValueError(f"retained {feature_set} feature manifest bytes changed")
        schemas[feature_set] = [
            item["name"] for item in _read_json(path)["feature_schema"]
        ]
    if schemas["P1"] != list(FEATURE_NAMES):
        raise ValueError("LAB P1 feature order differs from retained supported schema")
    if schemas["L1"] != schemas["P0"]:
        raise ValueError("retained L1/P0 local schemas unexpectedly differ")
    return {
        "feature_names": schemas["P1"],
        "feature_sets": schemas,
        "retained_manifest_sha256": EXPECTED_FEATURE_MANIFESTS,
        "semantic_sha256": hashlib.sha256(_canonical_json(schemas)).hexdigest(),
    }


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
    """Compatibility wrapper for focused exact-local-feature tests."""

    return build_local_features(source, intervals, instrument_id)


def _target_disposition() -> pl.Expr:
    return (
        pl.when(pl.col("start_close").is_null() & pl.col("end_close").is_null())
        .then(pl.lit("MISSING_BOTH_ENDPOINTS"))
        .when(pl.col("start_close").is_null())
        .then(pl.lit("MISSING_START_ENDPOINT"))
        .when(pl.col("end_close").is_null())
        .then(pl.lit("MISSING_END_ENDPOINT"))
        .when(pl.col("start_available_at") > pl.col("target_available_at"))
        .then(pl.lit("START_UNAVAILABLE_AT_MATURITY"))
        .when(pl.col("end_available_at") > pl.col("target_available_at"))
        .then(pl.lit("END_UNAVAILABLE_AT_MATURITY"))
        .when((pl.col("start_close") <= 0) | (pl.col("end_close") <= 0))
        .then(pl.lit("INVALID_ENDPOINT_PRICE"))
        .otherwise(pl.lit("VALID"))
        .alias("target_disposition")
    )


def _targets(
    source: pl.DataFrame,
    intervals: Sequence[tuple[datetime, datetime]],
    horizon_minutes: int,
    blocks: Sequence[tuple[str, datetime, datetime]],
    instrument_id: str = "UNKNOWN",
    *,
    start_at: datetime | None = None,
    end_before: datetime | None = None,
) -> pl.DataFrame:
    opportunities = pl.DataFrame(
        {
            "decision_time": opportunity_times(
                intervals,
                start_at=start_at,
                end_before=end_before,
            )
        },
        schema={"decision_time": pl.Datetime("us", "UTC")},
    )
    prices = source.select(
        pl.col("interval_end").alias("price_time"),
        "close",
        "available_at",
    )
    horizon = timedelta(minutes=horizon_minutes)
    return (
        opportunities.with_columns(
            (pl.col("decision_time") + horizon).alias("target_end"),
            (
                pl.col("decision_time")
                + horizon
                + timedelta(seconds=TARGET_REVISION_DELAY_SECONDS)
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
            pl.lit(horizon_minutes).cast(pl.Int16).alias("horizon_minutes"),
            pl.lit(TARGET_REVISION_POLICY).alias("target_revision_policy"),
            _block_expression(blocks),
            pl.lit(SOURCE_CLASS).alias("source_class"),
            pl.lit(LABEL).alias("evidence_label"),
        )
        .with_columns(
            (pl.col("target_disposition") == "VALID").alias("target_valid"),
            pl.col("decision_time")
            .map_elements(
                lambda decision_time: target_identity(
                    instrument_id=instrument_id,
                    decision_time=decision_time,
                    horizon=horizon,
                    target_basis=PriceBasis.MID,
                    target_revision_policy=TARGET_REVISION_POLICY,
                ),
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


def _slug(instrument_id: str) -> str:
    return instrument_id.replace(":", "--")


def _write_parquet(frame: pl.DataFrame, path: Path) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path, compression="zstd", statistics=True)
    return {"path": str(path), "row_count": frame.height, "sha256": _sha256(path)}


def _partitioned_write(
    frame: pl.DataFrame,
    root: Path,
    *,
    kind: str,
    instrument_id: str,
    horizon_minutes: int | None = None,
) -> list[dict[str, object]]:
    partitioned = frame.with_columns(
        pl.col("decision_time").dt.year().alias("_year"),
        pl.col("decision_time").dt.month().alias("_month"),
    ).partition_by("_year", "_month", as_dict=True, maintain_order=True)
    references = []
    for key, part in partitioned.items():
        year, month = (int(value) for value in key)
        path = (
            root
            / f"instrument={_slug(instrument_id)}"
            / f"year={year:04d}"
            / f"month={month:02d}"
            / "part.parquet"
        )
        reference = _write_parquet(part.drop("_year", "_month"), path)
        reference.update(
            {
                "kind": kind,
                "instrument_id": instrument_id,
                "year": year,
                "month": month,
            }
        )
        if horizon_minutes is not None:
            reference["horizon_minutes"] = horizon_minutes
        references.append(reference)
    return references


def _read_month_context(
    references: Sequence[dict[str, object]],
    year: int,
    month: int,
) -> pl.DataFrame:
    paths = [
        str(item["path"])
        for item in references
        if item["year"] == year and item["month"] == month
    ]
    if not paths:
        raise ValueError(f"no context parts for {year:04d}-{month:02d}")
    return pl.read_parquet(paths)


def _builder_id() -> dict[str, str]:
    root = Path(__file__).parent
    return {
        name: _sha256(root / name)
        for name in ("lab.py", "features.py", "baseline.py", "harness.py")
    }


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
                    "latest_training_decision_time_inclusive": start - maturity,
                    "target_maturity_seconds": int(maturity.total_seconds()),
                    "embargo_seconds": 0,
                    "terminal_selection_prohibited": name == "TERMINAL_FORMER_HOLDOUT",
                    "evidence_label": LABEL,
                }
            )
    return pl.DataFrame(rows), blocks


def replay_baseline(config: LabConfig) -> dict[str, object]:
    """Run the genuine LAB-derived baseline reconstruction on an existing build."""

    _, _, retained_folds, _ = _retained_inventory(config)
    return reconstruct_baseline_impl(
        config.output_root,
        config.terminal_run_root,
        retained_folds,
    )


def build(
    config: LabConfig,
    *,
    instruments: Sequence[str] | None = None,
) -> dict[str, object]:
    """Build a create-only, partitioned exploratory derivation."""

    if config.base_sha != EXPECTED_BASE_SHA:
        raise ValueError("lab configuration is not bound to the authorised repository SHA")
    manifest, parts = authenticate_stage7_manifest(config.stage7_manifest)
    active_intervals, _provider_gaps, retained_folds, foundation = _retained_inventory(
        config
    )
    feature_schema = _feature_schema(config)
    fold_frame, blocks = _fold_rows(retained_folds)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for part in parts:
        grouped[part["instrument_id"]].append(part)
    selected = sorted(grouped) if instruments is None else sorted(instruments)
    if not selected or not set(selected).issubset(grouped):
        raise ValueError("requested instrument selection is empty or outside Stage 7")

    output = config.output_root
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"lab output is create-only for this run: {output}")
    output.mkdir(parents=True, exist_ok=True)

    feature_refs: list[dict[str, object]] = []
    context_refs: list[dict[str, object]] = []
    target_refs: list[dict[str, object]] = []
    counts: list[dict[str, object]] = []
    consumed_source_parts: list[dict[str, object]] = []
    for instrument in selected:
        references = grouped[instrument]
        paths = [
            _safe_source_part(config.stage7_manifest.parent, item["path"])
            for item in references
        ]
        for path, reference in zip(paths, references, strict=True):
            if _sha256(path) != reference["bytes_sha256"]:
                raise ValueError(f"consumed Stage 7 part bytes changed: {path}")
            consumed_source_parts.append(
                {
                    "path": reference["path"],
                    "sha256": reference["bytes_sha256"],
                    "row_count": reference["row_count"],
                    "instrument_id": instrument,
                }
            )
        source = _source_frame(paths)
        if source.height != sum(item["row_count"] for item in references):
            raise ValueError(f"Stage 7 part row count differs for {instrument}")
        intervals = active_intervals[instrument]
        local = build_local_features(
            source,
            intervals,
            instrument,
        )
        feature_refs.extend(
            _partitioned_write(
                local,
                output / "features",
                kind="feature",
                instrument_id=instrument,
            )
        )
        context = build_context(source, intervals, instrument)
        context_refs.extend(
            _partitioned_write(
                context,
                output / "context",
                kind="context",
                instrument_id=instrument,
            )
        )
        for horizon in HORIZONS:
            target = _targets(
                source,
                intervals,
                horizon,
                blocks,
                instrument,
            )
            target_refs.extend(
                _partitioned_write(
                    target,
                    output / "targets" / f"horizon={horizon}m",
                    kind="target",
                    instrument_id=instrument,
                    horizon_minutes=horizon,
                )
            )
            grouped_counts = target.group_by("block", "target_disposition").agg(
                pl.len().alias("row_count")
            )
            for row in grouped_counts.to_dicts():
                counts.append(
                    {
                        "instrument_id": instrument,
                        "horizon_minutes": horizon,
                        **row,
                    }
                )

    for reference in feature_refs:
        path = Path(str(reference["path"]))
        local = pl.read_parquet(path)
        context = _read_month_context(
            context_refs,
            int(str(reference["year"])),
            int(str(reference["month"])),
        )
        enriched = add_pooled_features(
            local,
            context,
            str(reference["instrument_id"]),
        )
        updated = _write_parquet(enriched, path)
        reference.update(updated)

    folds_path = output / "folds" / "blocks.parquet"
    fold_reference = _write_parquet(fold_frame, folds_path)
    fold_reference["kind"] = "fold"
    written = [*feature_refs, *context_refs, *target_refs, fold_reference]

    baseline: dict[str, object] | None = None
    if set(selected) == set(grouped):
        baseline = reconstruct_baseline_impl(
            output,
            config.terminal_run_root,
            retained_folds,
        )
        baseline_path = output / "baseline-reconstruction.json"
        baseline_path.write_bytes(_canonical_json(baseline))
        written.append(
            {
                "kind": "baseline",
                "path": str(baseline_path),
                "row_count": 1,
                "sha256": _sha256(baseline_path),
            }
        )

    dictionary_path = output / "data-dictionary.md"
    dictionary_path.write_text(_data_dictionary(), encoding="utf-8")
    written.append(
        {
            "kind": "data_dictionary",
            "path": str(dictionary_path),
            "row_count": 1,
            "sha256": _sha256(dictionary_path),
        }
    )
    block_manifest = [
        {
            "name": name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "selection_prohibited": name == "TERMINAL_FORMER_HOLDOUT",
        }
        for name, start, end in blocks
    ]
    lab_manifest = {
        "contract": MANIFEST_CONTRACT,
        "job_id": config.job_id,
        "base_sha": config.base_sha,
        "builder_identity": _builder_id(),
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "authoritative": False,
        "status": "COMPLETE" if baseline is not None else "SMOKE_ONLY",
        "stage7": {
            "manifest": str(config.stage7_manifest),
            "manifest_sha256": EXPECTED_STAGE7_MANIFEST_SHA256,
            "dataset_id": EXPECTED_STAGE7_DATASET_ID,
            "row_count": manifest["dataset"]["row_count"],
            "consumed_parts": consumed_source_parts,
        },
        "terminal_foundation": {
            "path": str(config.terminal_run_root / "foundation"),
            **foundation,
        },
        "availability": {
            "policy": EXPECTED_AVAILABILITY_POLICY,
            "delay": EXPECTED_AVAILABILITY_DELAY,
            "feature_lag_seconds": int(FEATURE_LAG.total_seconds()),
            "target_revision_delay_seconds": TARGET_REVISION_DELAY_SECONDS,
            "grid_resolution_seconds": GRID_SECONDS,
        },
        "instruments": selected,
        "original_target_instruments": list(ORIGINAL_TARGETS),
        "market_groups": {
            item: item.split(":", maxsplit=1)[0].upper() for item in selected
        },
        "horizons_minutes": list(HORIZONS),
        "feature_schema": feature_schema,
        "feature_semantics": {
            "price_basis": "MID",
            "feature_windows_seconds": [60, 300],
            "rolling_slots": "SOURCE_ACTIVE_EXACT_ONE_MINUTE",
            "return_endpoints": "EXACT_CAUSALLY_AVAILABLE_NO_FORWARD_FILL",
            "standard_deviation": "POPULATION_DDOF_0",
            "time_basis": "UTC_MINUTE_OF_DAY_AND_WEEKDAY",
            "cross_asset_peers": "ALL_SELECTED_TARGETS_EXCLUDING_SELF",
            "market_groups": "CANONICAL_INSTRUMENT_PREFIX",
            "gap_known_by_cutoff": "ZERO_NO_GAPS_SUPPLIED_TO_RETAINED_PANEL_BUILD",
            "vix_context": "NULL_NO_RETAINED_VOLATILITY_INSTRUMENT",
        },
        "fold_blocks": block_manifest,
        "parts": written,
        "counts": sorted(
            counts,
            key=lambda item: (
                str(item["instrument_id"]),
                str(item["horizon_minutes"]),
                str(item["block"]),
                str(item["target_disposition"]),
            ),
        ),
        "baseline_reconstruction": baseline,
        "downstream": {
            "loader": "experiments.r2_historical_lab.harness.load_parts",
            "attempt_register": "experiments.r2_historical_lab.harness.append_attempt",
            "finalist_freeze": "experiments.r2_historical_lab.harness.freeze_finalists",
            "terminal_access": "denied unless exact finalist freeze authorises configuration",
        },
    }
    manifest_path = output / "lab-manifest.json"
    manifest_path.write_bytes(_canonical_json(lab_manifest))
    return {
        "manifest": str(manifest_path),
        "sha256": _sha256(manifest_path),
        **lab_manifest,
    }


def _data_dictionary() -> str:
    return """# LAB-0-v2 data dictionary

Every row is EXPLORATORY_POST_HOC_ONLY with source class
IBKR_HISTORICAL_RESEARCH. This is hypothesis-generation input, not another
holdout, confirmation, promotion, or decision-grade result.

## Features

Feature rows are keyed by instrument_id and decision_time and partitioned by
instrument and calendar month. latest_feature_bar_end equals decision_time
minus PT5M even when the exact bar is missing. All L0/L1/P0/P1 values use the
retained one-minute active-slot, causal-availability, population-statistic,
weekday and minute-of-day semantics. No value is forward-filled. The retained
foundation build did not supply data gaps to the panel builder, so its
`gap_known_by_cutoff` contract is exactly zero. The manifest binds the exact
ordered schemas. Cross-asset values use all twenty LAB target candidates; the
baseline gate recomputes context with the original six targets.

## Context

Context partitions retain only causal per-instrument inputs needed to recompute
leave-one-out features for another frozen target universe. They are not
forecasts or outcomes.

## Targets

Target rows are keyed by stable target_id, instrument_id, decision_time and
horizon_minutes. Opportunities begin only inside retained source-active
half-open intervals. Exact start/end MID bars must be visible by target_end
plus PT5M. Invalid rows remain present with a specific target_disposition.

## Folds and terminal block

DEV_1 through DEV_3 are the original pre-holdout validation blocks. The
baseline reconstruction consumes the authenticated retained training and
validation target-ID memberships rather than inferring membership from block
timestamps. TERMINAL_FORMER_HOLDOUT is consumed, post-hoc and
selection-prohibited. The authenticated harness denies terminal
loading until a create-only finalist freeze authorises the exact configuration.

## Baseline

The full build authenticates retained fit children, rebuilds fold-local
preprocessing, refits the original L1 local and P0 pooled-local Ridge models,
predicts the three OOF blocks from LAB-derived rows and computes
instrument-balanced MSE directly against ZERO_RETURN.

## Loading and run register

Use harness.py to authenticate the manifest, hash selected consumed parts, load
DEV blocks by default, append every attempt to JSONL, freeze a small finalist
set, then evaluate each authorised finalist once on the terminal block. Do not
re-read Stage 7 or replay R2 ancestry downstream.
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
        smoke_config = replace(
            config,
            job_id=f"{config.job_id}-smoke-remediated",
            output_root=config.output_root.with_name(
                f"{config.job_id}-smoke-remediated"
            ),
        )
        result = build(smoke_config, instruments=instrument)
    else:
        result = build(config, instruments=args.instrument)
    print(json.dumps(result, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
