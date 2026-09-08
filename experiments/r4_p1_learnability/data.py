"""Immediate-parent authentication and outcome-blind panel construction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from experiments.r4_residual_graph.foundation import (
    ALL_INSTRUMENTS,
    DEVELOPMENT_BLOCKS,
    LOCAL_CONFIG_ID,
    MANIFEST_PATH,
    MANIFEST_SHA256,
    TERMINAL_START,
)
from experiments.r4_residual_graph.preparation_reference import (
    ACCEPTED_RECEIPT,
    ACCEPTED_RECEIPT_SHA256,
    PREPARATION_ROOT,
)
from experiments.r4_residual_graph.tensor import P0_FEATURE_NAMES

Array = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]
FOUNDATION_SHA = "953fb9f4c198ab07850d38a0791af6e8fd51be261d35e0c23d838b669da37d3a"


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_development(
    blocks: tuple[str, ...] = DEVELOPMENT_BLOCKS,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    # Check requests before any filesystem operation.
    if not blocks or any(block not in DEVELOPMENT_BLOCKS for block in blocks):
        raise ValueError("P1 loader permits only DEV_1, DEV_2 and DEV_3")
    if digest(MANIFEST_PATH) != MANIFEST_SHA256:
        raise ValueError("LAB-0 manifest digest mismatch")
    manifest = json.loads(MANIFEST_PATH.read_text())
    if tuple(manifest["instruments"]) != ALL_INSTRUMENTS:
        raise ValueError("LAB-0 universe mismatch")
    if digest(ACCEPTED_RECEIPT) != ACCEPTED_RECEIPT_SHA256:
        raise ValueError("accepted preparation receipt digest mismatch")
    receipt = json.loads(ACCEPTED_RECEIPT.read_text())
    if receipt["status"] != "PASS":
        raise ValueError("preparation was not accepted")
    foundation = PREPARATION_ROOT / "input/residual-foundation.parquet"
    if digest(foundation) != FOUNDATION_SHA:
        raise ValueError("accepted residual foundation digest mismatch")
    rows = pl.read_parquet(foundation)
    if rows.filter(~pl.col("block").is_in(DEVELOPMENT_BLOCKS)).height:
        raise ValueError("foundation contains prohibited rows")
    if rows["local_configuration_id"].unique().to_list() != [LOCAL_CONFIG_ID]:
        raise ValueError("local Ridge configuration mismatch")
    if rows["manifest_sha256"].unique().to_list() != [MANIFEST_SHA256]:
        raise ValueError("foundation LAB parent mismatch")
    if rows.height != 771140 or rows["target_id"].n_unique() != rows.height:
        raise ValueError("foundation support mismatch")
    if not np.allclose(
        rows["local_residual"].to_numpy(),
        rows["target_return"].to_numpy() - rows["local_forecast"].to_numpy(),
        rtol=0,
        atol=1e-15,
    ):
        raise ValueError("residual equation mismatch")
    consumed: list[dict[str, str]] = []
    paths: dict[str, list[Path]] = {"feature": [], "context": []}
    for part in manifest["parts"]:
        if part["kind"] not in paths:
            continue
        path = MANIFEST_PATH.parent / part["path"]
        if digest(path) != part["sha256"]:
            raise ValueError(f"LAB child digest mismatch: {path}")
        paths[part["kind"]].append(path)
        consumed.append({"path": str(path), "sha256": part["sha256"]})
    features = (
        pl.scan_parquet(paths["feature"]).filter(pl.col("decision_time") < TERMINAL_START).collect()
    )
    context = (
        pl.scan_parquet(paths["context"])
        .filter(pl.col("decision_time") < TERMINAL_START)
        .select("instrument_id", "decision_time", "current_available")
        .collect()
    )
    counts = context.group_by("decision_time").agg(
        pl.col("current_available").sum().alias("_total")
    )
    features = (
        features.join(counts, on="decision_time", how="left")
        .join(context, on=["instrument_id", "decision_time"], how="left", validate="1:1")
        .with_columns(
            (pl.col("_total").fill_null(0) - pl.col("current_available").fill_null(0)).alias(
                "cross_market_available_count"
            )
        )
        .drop("_total", "current_available")
    )
    for block in DEVELOPMENT_BLOCKS:
        if set(rows.filter(pl.col("block") == block)["instrument_id"].to_list()) != set(
            ALL_INSTRUMENTS
        ):
            raise ValueError("foundation lacks all-twenty block coverage")
    provenance = {
        "manifest_sha256": MANIFEST_SHA256,
        "foundation_sha256": FOUNDATION_SHA,
        "accepted_receipt_sha256": ACCEPTED_RECEIPT_SHA256,
        "consumed_children": consumed,
        "local_configuration_id": LOCAL_CONFIG_ID,
        "foundation_rows": rows.height,
    }
    return rows.filter(pl.col("block").is_in(blocks)), features, provenance


@dataclass(frozen=True)
class Panel:
    times: IntArray
    values: Array
    observed: BoolArray
    residual: Array
    local: Array
    target: Array
    eligible: BoolArray
    maturity: IntArray
    block: IntArray

    def selected(self, block_ids: tuple[int, ...], limit: int | None = None) -> IntArray:
        keys = np.flatnonzero(np.isin(self.block, block_ids) & self.eligible.any(axis=1))
        if limit is not None and len(keys) > limit:
            keys = keys[np.linspace(0, len(keys) - 1, limit, dtype=np.int64)]
        return keys


def build_panel(rows: pl.DataFrame, features: pl.DataFrame) -> Panel:
    features = features.sort("decision_time", "instrument_id")
    if features.select("decision_time", "instrument_id").unique().height != features.height:
        raise ValueError("duplicate feature key")
    timestamps = features["decision_time"].cast(pl.Int64).to_numpy().astype(np.int64)
    times = np.unique(timestamps)
    node_map = {name: index for index, name in enumerate(ALL_INSTRUMENTS)}
    ti = np.searchsorted(times, timestamps)
    ni = np.array([node_map[value] for value in features["instrument_id"]], dtype=np.int64)
    shape = (len(times), 20, len(P0_FEATURE_NAMES))
    values = np.zeros(shape)
    observed = np.zeros(shape, dtype=bool)
    active = (
        (
            (features["feature_data_asof"] <= features["decision_time"])
            & (features["feature_available_at"] <= features["decision_time"])
            & (features["source_active"] > 0)
        )
        .fill_null(False)
        .to_numpy()
    )
    for index, name in enumerate(P0_FEATURE_NAMES):
        column = features[name].cast(pl.Float64).to_numpy()
        mask = active & np.isfinite(column)
        if name + "_available" in features.columns:
            mask &= features[name + "_available"].fill_null(False).to_numpy().astype(bool)
        values[ti, ni, index] = np.where(mask, column, 0)
        observed[ti, ni, index] = mask
    target = np.zeros(shape[:2])
    residual = np.zeros(shape[:2])
    local = np.zeros(shape[:2])
    eligible = np.zeros(shape[:2], dtype=bool)
    maturity = np.zeros(shape[:2], dtype=np.int64)
    block = np.zeros(len(times), dtype=np.int64)
    rt = rows["decision_time"].cast(pl.Int64).to_numpy().astype(np.int64)
    ri = np.searchsorted(times, rt)
    if np.any(ri >= len(times)) or not np.array_equal(times[ri], rt):
        raise ValueError("residual row missing feature timestamp")
    rn = np.array([node_map[value] for value in rows["instrument_id"]], dtype=np.int64)
    for destination, name in [
        (target, "target_return"),
        (residual, "local_residual"),
        (local, "local_forecast"),
    ]:
        raw = rows[name].to_numpy()
        if not np.isfinite(raw).all():
            raise ValueError(f"non-finite required {name}")
        destination[ri, rn] = raw
    maturity[ri, rn] = rows["target_available_at"].cast(pl.Int64).to_numpy()
    eligible[ri, rn] = observed[ri, rn].any(axis=-1)
    for index, name in enumerate(DEVELOPMENT_BLOCKS, 1):
        selected = rows["block"].to_numpy() == name
        block[ri[selected]] = index
    return Panel(times, values, observed, residual, local, target, eligible, maturity, block)


def training_keys(panel: Panel, keys: IntArray, evaluation: IntArray) -> tuple[IntArray, BoolArray]:
    mask = panel.eligible[keys] & (panel.maturity[keys] < panel.times[evaluation[0]])
    return keys, mask
