"""Small authenticated loader and common evaluator for downstream LAB workstreams."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import polars as pl

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.lab_0.features import positive_contribution_share

MANIFEST_CONTRACT = "qtrad-r2-historical-lab-manifest-v2"
EVALUATION_CONTRACT = "qtrad-r2-historical-lab-evaluation-v1"
TERMINAL_BLOCK = "TERMINAL_FORMER_HOLDOUT"
DEVELOPMENT_BLOCKS = frozenset({"DEV_1", "DEV_2", "DEV_3"})
EVALUATION_BLOCKS = DEVELOPMENT_BLOCKS | {"TRAINING_ONLY", TERMINAL_BLOCK}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def authenticate_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    if _sha256(path) != expected_sha256:
        raise ValueError("lab manifest SHA-256 differs from the selected LAB-0-v2 input")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("lab manifest must be a JSON object")
    if value["contract"] != MANIFEST_CONTRACT:
        raise ValueError("unsupported lab manifest contract")
    if value["evidence_label"] != LABEL or value["source_class"] != SOURCE_CLASS:
        raise ValueError("lab manifest crosses the exploratory source boundary")
    if value["status"] != "COMPLETE" or value["authoritative"] is not False:
        raise ValueError("lab manifest is not a complete non-authoritative LAB build")
    block_by_name = {item["name"]: item for item in value["fold_blocks"]}
    required_blocks = {
        "DEV_1",
        "DEV_2",
        "DEV_3",
        "TERMINAL_FORMER_HOLDOUT",
    }
    if (
        set(block_by_name) != required_blocks
        or block_by_name[TERMINAL_BLOCK]["selection_prohibited"] is not True
    ):
        raise ValueError("lab manifest does not bind the required development blocks")
    baseline = value["baseline_reconstruction"]
    tolerances = baseline["tolerances"]
    retained_oof_manifest = baseline["retained_oof_manifest"]
    if retained_oof_manifest != {
        "path": (
            "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/oof/manifest.json"
        ),
        "sha256": "ff0bd89fb97448beda6e70565191bb512458c4d3124ec0dc17476b2d43859819",
        "contract": "qtrad-r2-oof-bundle-v2",
        "schema_version": 2,
        "source_class": SOURCE_CLASS,
        "evidence_class": "CONFIRMATORY",
        "oof_id": "c31dddc528936d1a415c4a5af009e59a43eefe27909b7a16267712f9671dfa65",
        "closure_id": "d911eea62786f7e0d99719b78c93da80cd6574118e706812107dd949d2fcd6a6",
    }:
        raise ValueError("lab manifest lacks the exact retained OOF parent binding")
    if (
        baseline["contract"] != "qtrad-r2-historical-lab-baseline-reconstruction-v2"
        or baseline["support_exact"] is not True
        or baseline["ordering_zero_pooled_local"] is not True
        or baseline["fit_count"] != 21
        or baseline["maximum_metric_abs_delta"] > tolerances["metric_abs"]
        or baseline["maximum_preprocessing_abs_delta"] > tolerances["preprocessing_abs"]
        or baseline["maximum_coefficient_abs_delta"] > tolerances["coefficient_abs"]
        or baseline["maximum_intercept_abs_delta"] > tolerances["intercept_abs"]
    ):
        raise ValueError("lab manifest lacks a successful real baseline reconstruction")
    return value


def _selected_references(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    kind: str,
    instruments: Sequence[str] | None,
    horizons: Sequence[int] | None,
) -> list[dict[str, Any]]:
    if kind not in {"feature", "context", "target"}:
        raise ValueError(f"unsupported lab part kind: {kind}")
    selected_instruments = set(manifest["instruments"] if instruments is None else instruments)
    unknown_instruments = selected_instruments - set(manifest["instruments"])
    if unknown_instruments:
        raise ValueError(f"unknown selected instruments: {sorted(unknown_instruments)}")
    selected_horizons = set(manifest["horizons_minutes"] if horizons is None else horizons)
    unknown_horizons = selected_horizons - set(manifest["horizons_minutes"])
    if kind == "target" and unknown_horizons:
        raise ValueError(f"unknown selected horizons: {sorted(unknown_horizons)}")
    if kind != "target" and horizons is not None:
        raise ValueError("horizon selection applies only to target parts")
    references = [
        item
        for item in manifest["parts"]
        if item["kind"] == kind
        and item.get("instrument_id") in selected_instruments
        and (kind != "target" or int(item["horizon_minutes"]) in selected_horizons)
    ]
    if not references:
        raise ValueError("selected lab part set is empty")
    root = manifest_path.parent.resolve()
    for reference in references:
        path = Path(reference["path"]).resolve()
        if root not in path.parents:
            raise ValueError(f"lab part escapes the authenticated output root: {path}")
        if _sha256(path) != reference["sha256"]:
            raise ValueError(f"lab part SHA-256 differs: {path}")
    return references


def _filter_blocks(
    frame: pl.LazyFrame,
    manifest: dict[str, Any],
    *,
    kind: str,
    requested_blocks: set[str],
) -> pl.LazyFrame:
    named = {item["name"]: item for item in manifest["fold_blocks"]}
    allowed = set(named) | {"TRAINING_ONLY"}
    unknown = requested_blocks - allowed
    if unknown:
        raise ValueError(f"unknown selected blocks: {sorted(unknown)}")
    if kind == "target":
        return frame.filter(pl.col("block").is_in(sorted(requested_blocks)))

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
    for name in requested_blocks:
        selected = selected | expressions[name]
    return frame.filter(selected)


def load_parts(
    manifest_path: Path,
    expected_manifest_sha256: str,
    *,
    kind: str,
    instruments: Sequence[str] | None = None,
    horizons: Sequence[int] | None = None,
    blocks: Sequence[str] = ("DEV_1", "DEV_2", "DEV_3"),
    finalist_freeze: Path | None = None,
    expected_finalist_freeze_sha256: str | None = None,
    configuration_id: str | None = None,
) -> pl.LazyFrame:
    """Authenticate and lazily load only selected feature or target parts."""

    manifest = authenticate_manifest(manifest_path, expected_manifest_sha256)
    references = _selected_references(
        manifest_path,
        manifest,
        kind=kind,
        instruments=instruments,
        horizons=horizons,
    )
    requested_blocks = set(blocks)
    if TERMINAL_BLOCK in requested_blocks:
        if (
            finalist_freeze is None
            or expected_finalist_freeze_sha256 is None
            or configuration_id is None
        ):
            raise ValueError(
                "terminal loading requires an authenticated finalist freeze and configuration ID"
            )
        if _sha256(finalist_freeze) != expected_finalist_freeze_sha256:
            raise ValueError("finalist freeze SHA-256 differs")
        freeze = json.loads(finalist_freeze.read_bytes())
        if (
            freeze["contract"] != "qtrad-r2-historical-lab-finalist-freeze-v1"
            or freeze["evidence_label"] != LABEL
            or freeze["manifest_sha256"] != expected_manifest_sha256
            or configuration_id not in freeze["finalist_configuration_ids"]
        ):
            raise ValueError("configuration is not authorised by this finalist freeze")
    frame = pl.scan_parquet([reference["path"] for reference in references])
    return _filter_blocks(
        frame,
        manifest,
        kind=kind,
        requested_blocks=requested_blocks,
    )


def evaluate_against_zero(
    predictions: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    model_name: str,
) -> dict[str, object]:
    required_predictions = {
        "instrument_id",
        "decision_time",
        "horizon_minutes",
        "expected_return",
    }
    required_targets = {
        "instrument_id",
        "decision_time",
        "horizon_minutes",
        "target_return",
        "target_valid",
        "block",
    }
    if not required_predictions.issubset(predictions.columns):
        raise ValueError("prediction columns do not satisfy the lab evaluator contract")
    if not required_targets.issubset(targets.columns):
        raise ValueError("target columns do not satisfy the lab evaluator contract")
    if model_name == "ZERO_RETURN":
        raise ValueError("candidate model name must be distinct from ZERO_RETURN")
    keys = ["instrument_id", "decision_time", "horizon_minutes"]
    valid_targets = targets.filter(pl.col("target_valid"))
    joined = valid_targets.join(predictions, on=keys, how="inner", validate="1:1")
    if joined.is_empty():
        raise ValueError("candidate has no valid forecast support")
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
        pl.col("_improvement").sum().alias("contribution"),
    )
    blocks = scored.group_by("block").agg(
        pl.col("_zero_se").mean().alias("zero_mse"),
        pl.col("_model_se").mean().alias("model_mse"),
        pl.col("_improvement").sum().alias("contribution"),
    )
    zero_mse = float(cast(float, instrument["zero_mse"].mean()))
    model_mse = float(cast(float, instrument["model_mse"].mean()))
    calibration = scored.select(
        pl.cov("expected_return", "target_return").alias("covariance"),
        pl.col("expected_return").var(ddof=0).alias("forecast_variance"),
        pl.corr("expected_return", "target_return", method="spearman").alias("spearman"),
    ).row(0, named=True)
    variance = calibration["forecast_variance"]
    evaluated_blocks = sorted(str(value) for value in scored["block"].unique().to_list())
    return {
        "contract": EVALUATION_CONTRACT,
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "model_name": model_name,
        "evaluated_blocks": evaluated_blocks,
        "terminal_block_accessed": TERMINAL_BLOCK in evaluated_blocks,
        "support": joined.height,
        "forecast_coverage": joined.height / valid_targets.height,
        "zero_return_instrument_balanced_mse": zero_mse,
        "model_instrument_balanced_mse": model_mse,
        "direct_delta_mse_versus_zero": model_mse - zero_mse,
        "skill_versus_zero": 1.0 - model_mse / zero_mse,
        "positive_chronological_block_count": int(
            blocks.filter(pl.col("model_mse") < pl.col("zero_mse")).height
        ),
        "chronological_block_count": blocks.height,
        "positive_instrument_count": int(
            instrument.filter(pl.col("model_mse") < pl.col("zero_mse")).height
        ),
        "instrument_count": instrument.height,
        "calibration_slope": (
            float(calibration["covariance"]) / float(variance)
            if variance is not None and variance > 0
            else None
        ),
        "spearman_correlation": (
            float(calibration["spearman"]) if calibration["spearman"] is not None else None
        ),
        "best_instrument_contribution": positive_contribution_share(
            instrument["contribution"].to_list()
        ),
        "best_period_contribution": positive_contribution_share(blocks["contribution"].to_list()),
    }


def configuration_id(configuration: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(configuration)).hexdigest()


def append_attempt(
    register_path: Path,
    *,
    workstream: str,
    configuration: dict[str, Any],
    result: dict[str, Any],
    manifest_sha256: str,
) -> str:
    identifier = configuration_id(configuration)
    evaluated_blocks: list[str] | None = None
    if result.get("contract") == EVALUATION_CONTRACT:
        if result["evidence_label"] != LABEL or result["source_class"] != SOURCE_CLASS:
            raise ValueError("evaluation result crosses the exploratory source boundary")
        result_blocks = result["evaluated_blocks"]
        terminal_block_accessed = result["terminal_block_accessed"]
        if (
            not isinstance(result_blocks, list)
            or not result_blocks
            or not all(isinstance(block, str) for block in result_blocks)
            or result_blocks != sorted(set(result_blocks))
            or not set(result_blocks).issubset(EVALUATION_BLOCKS)
            or not isinstance(terminal_block_accessed, bool)
            or terminal_block_accessed != (TERMINAL_BLOCK in result_blocks)
        ):
            raise ValueError("evaluation result has inconsistent or non-canonical block provenance")
        evaluated_blocks = cast(list[str], result_blocks)
        attempt_status = "SUCCEEDED"
    elif result.get("status") == "FAILED":
        attempt_status = "FAILED"
        terminal_block_accessed = None
    else:
        raise ValueError("attempt result must be a lab evaluation or explicit failure")
    entry = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "workstream": workstream,
        "configuration_id": identifier,
        "configuration": configuration,
        "result": result,
        "manifest_sha256": manifest_sha256,
        "attempt_status": attempt_status,
        "evaluated_blocks": evaluated_blocks,
        "terminal_block_accessed": terminal_block_accessed,
        "evidence_label": LABEL,
    }
    register_path.parent.mkdir(parents=True, exist_ok=True)
    with register_path.open("ab") as stream:
        stream.write(_canonical_json(entry))
    return identifier


def freeze_finalists(
    register_path: Path,
    output_path: Path,
    *,
    workstream: str,
    finalist_configuration_ids: Sequence[str],
    manifest_sha256: str,
) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(f"finalist freeze is create-only: {output_path}")
    entries = [
        json.loads(line)
        for line in register_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matching_entries = [
        entry
        for entry in entries
        if entry["workstream"] == workstream and entry["manifest_sha256"] == manifest_sha256
    ]
    successful_development = {
        entry["configuration_id"]
        for entry in matching_entries
        if entry["attempt_status"] == "SUCCEEDED"
        and entry["evaluated_blocks"]
        and set(entry["evaluated_blocks"]).issubset(DEVELOPMENT_BLOCKS)
    }
    disqualified = {
        entry["configuration_id"]
        for entry in matching_entries
        if entry["attempt_status"] != "SUCCEEDED"
        or not entry["evaluated_blocks"]
        or not set(entry["evaluated_blocks"]).issubset(DEVELOPMENT_BLOCKS)
    }
    eligible = successful_development - disqualified
    finalists = tuple(finalist_configuration_ids)
    if not finalists or not set(finalists).issubset(eligible):
        raise ValueError(
            "every finalist must have only successful canonical DEV-block registered attempts"
        )
    value = {
        "contract": "qtrad-r2-historical-lab-finalist-freeze-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "workstream": workstream,
        "manifest_sha256": manifest_sha256,
        "finalist_configuration_ids": list(finalists),
        "evidence_label": LABEL,
        "terminal_evaluation_is_post_hoc_only": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_canonical_json(value))
    return value
