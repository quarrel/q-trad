"""Fixed linear probes, descriptive diagnostics and equal-instrument reducers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from experiments.r4_residual_graph.foundation import ALL_INSTRUMENTS
from experiments.r4_residual_graph.graph import build_fixed_economic_graph, shuffle_economic_graph

from .data import Array, BoolArray, Panel, training_keys
from .ridge import ridge_prediction

FAMILIES = ("local", "pooled", "fixed", "shuffled")


def require_freeze(register: Path, *, neural: bool = False) -> dict[str, Any]:
    if not register.exists():
        raise ValueError("R4P1_POLICY_FREEZE is required before empirical metrics")
    entries = [json.loads(line) for line in register.read_text().splitlines()]
    freezes = [entry for entry in entries if entry["event"] == "R4P1_POLICY_FREEZE"]
    if len(freezes) != 1:
        raise ValueError("exactly one R4P1_POLICY_FREEZE is required")
    freeze = freezes[0]
    if freeze["decision"] not in {"CAPABILITY_ESTABLISHED", "TRAINING_CAPABILITY_NOT_ESTABLISHED"}:
        raise ValueError("invalid policy-freeze decision")
    if neural and freeze["decision"] != "CAPABILITY_ESTABLISHED":
        raise ValueError("empirical neural fitting requires capability")
    return freeze


def append_event(register: Path, event: str, **fields: Any) -> None:
    if event == "R4P1_POLICY_FREEZE" and register.exists():
        entries = [json.loads(line) for line in register.read_text().splitlines()]
        if any(entry["event"] == event for entry in entries):
            raise ValueError("policy already frozen")
    with register.open("a") as handle:
        handle.write(json.dumps({"event": event, **fields}, sort_keys=True, allow_nan=False) + "\n")


def context_design(values: Array, observed: BoolArray, family: str) -> Array:
    if family == "local":
        return values
    if family == "pooled":
        weights = np.ones((20, 20)) - np.eye(20)
    elif family in {"fixed", "shuffled"}:
        graph = build_fixed_economic_graph()
        weights = (
            shuffle_economic_graph(graph).normalized_adjacency
            if family == "shuffled"
            else graph.normalized_adjacency
        )
    else:
        raise ValueError("unknown linear family")
    weighted = np.einsum("ij,tjf->tif", weights, values * observed)
    available = np.einsum("ij,tjf->tif", weights, observed.astype(float))
    # Missing context is explicitly masked to zero, never a missing forecast.
    context = np.divide(weighted, available, out=np.zeros_like(weighted), where=available > 0)
    return np.concatenate((values, context), axis=-1)


def sufficient_statistics(
    target: Array, prediction: Array, residual: Array, correction: Array, mask: BoolArray
) -> dict[str, Any]:
    if prediction.shape != target.shape or correction.shape != target.shape:
        raise ValueError("candidate-specific support mismatch")
    if not np.isfinite(prediction[mask]).all() or not np.isfinite(correction[mask]).all():
        raise ValueError("candidate-specific non-finite forecast")
    counts = mask.sum(axis=0)
    if np.any(counts == 0):
        raise ValueError("common support must cover all twenty instruments")
    sse = np.where(mask, np.square(prediction - target), 0).sum(axis=0)
    residual_sse = np.where(mask, np.square(correction - residual), 0).sum(axis=0)
    return {"counts": counts.tolist(), "sse": sse.tolist(), "residual_sse": residual_sse.tolist()}


def reduce_statistics(statistics: dict[str, Any]) -> dict[str, Any]:
    counts = np.array(statistics["counts"])
    mse = np.array(statistics["sse"]) / counts
    residual_mse = np.array(statistics["residual_sse"]) / counts
    groups = {
        group: float(mse[[name.split(":")[0] == group for name in ALL_INSTRUMENTS]].mean())
        for group in ("commodity", "fx", "index")
    }
    return {
        **statistics,
        "mse": float(mse.mean()),
        "residual_mse": float(residual_mse.mean()),
        "instrument_mse": dict(zip(ALL_INSTRUMENTS, mse.tolist(), strict=True)),
        "group_mse": groups,
    }


def complete_table(blocks: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    combined: dict[str, dict[str, Any]] = {}
    for family in blocks["DEV_2"]:
        first, second = blocks["DEV_2"][family], blocks["DEV_3"][family]
        combined[family] = {
            key: (np.array(first[key]) + np.array(second[key])).tolist()
            for key in ("counts", "sse", "residual_sse")
        }
    result = {
        block: {family: reduce_statistics(stats) for family, stats in families.items()}
        for block, families in {**blocks, "DEV_COMBINED": combined}.items()
    }
    for families in result.values():
        zero = families["zero"]["mse"]
        for stats in families.values():
            stats["skill_zero"] = 1 - stats["mse"] / zero
    return result


def comparison(table: dict[str, Any], candidate: str, baseline: str) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for block in ("DEV_2", "DEV_3", "DEV_COMBINED"):
        c, b = table[block][candidate], table[block][baseline]
        by_instrument = {
            name: b["instrument_mse"][name] - c["instrument_mse"][name] for name in ALL_INSTRUMENTS
        }
        positive = np.maximum(0, np.array(list(by_instrument.values())))
        deltas[block] = {
            "delta": b["mse"] - c["mse"],
            "instrument_delta": by_instrument,
            "breadth": int(np.count_nonzero(positive)),
            "best_instrument_share": float(positive.max() / positive.sum())
            if positive.sum() > 0
            else 1.0,
            "group_breadth": {
                group: sum(
                    value > 0
                    for name, value in by_instrument.items()
                    if name.startswith(group + ":")
                )
                for group in ("commodity", "fx", "index")
            },
        }
    return deltas


def graph_gate(table: dict[str, Any]) -> dict[str, Any]:
    pooled = comparison(table, "fixed", "pooled")
    shuffled = comparison(table, "fixed", "shuffled")
    requirements = {
        "pooled_both_blocks": all(pooled[block]["delta"] > 0 for block in ("DEV_2", "DEV_3")),
        "shuffled_both_blocks": all(shuffled[block]["delta"] > 0 for block in ("DEV_2", "DEV_3")),
        "positive_skill_both": all(
            table[block]["fixed"]["skill_zero"] > 0 for block in ("DEV_2", "DEV_3")
        ),
        "breadth_seven": pooled["DEV_COMBINED"]["breadth"] >= 7,
        "concentration": pooled["DEV_COMBINED"]["best_instrument_share"] <= 0.8,
    }
    return {
        "passed": all(requirements.values()),
        "requirements": requirements,
        "fixed_vs_pooled": pooled,
        "fixed_vs_shuffled": shuffled,
    }


def linear_probes(panel: Panel, register: Path) -> dict[str, Any]:
    require_freeze(register)
    blocks: dict[str, dict[str, dict[str, Any]]] = {"DEV_2": {}, "DEV_3": {}}
    for stage in (2, 3):
        evaluation = panel.selected((stage,))
        training, train_mask = training_keys(
            panel, panel.selected(tuple(range(1, stage))), evaluation
        )
        mask = panel.eligible[evaluation]
        output = blocks[f"DEV_{stage}"]
        for name, prediction in [
            ("zero", np.zeros_like(panel.target[evaluation])),
            ("ridge", panel.local[evaluation]),
        ]:
            output[name] = sufficient_statistics(
                panel.target[evaluation],
                prediction,
                panel.residual[evaluation],
                prediction - panel.local[evaluation],
                mask,
            )
        for family in FAMILIES:
            append_event(register, "LINEAR_FIT_STARTED", stage=stage, family=family, alpha=1.0)
            training_x = context_design(panel.values[training], panel.observed[training], family)[
                train_mask
            ]
            evaluation_x = context_design(
                panel.values[evaluation], panel.observed[evaluation], family
            )[mask]
            predicted = ridge_prediction(
                training_x, panel.residual[training][train_mask], evaluation_x
            )
            correction = np.zeros_like(panel.residual[evaluation])
            correction[mask] = predicted
            output[family] = sufficient_statistics(
                panel.target[evaluation],
                panel.local[evaluation] + correction,
                panel.residual[evaluation],
                correction,
                mask,
            )
            append_event(
                register,
                "LINEAR_FIT_COMPLETED",
                stage=stage,
                family=family,
                training_rows=int(train_mask.sum()),
                evaluation_rows=int(mask.sum()),
            )
    table = complete_table(blocks)
    return {
        "table": table,
        "graph_gate": graph_gate(table),
        "vs_ridge": {family: comparison(table, family, "ridge") for family in FAMILIES},
    }


def edge_sets(panel: Panel) -> dict[str, list[tuple[int, int]]]:
    fixed = build_fixed_economic_graph()
    shuffled = shuffle_economic_graph(fixed)

    def pairs(matrix: Array) -> list[tuple[int, int]]:
        return [(int(i), int(j)) for i, j in np.argwhere(matrix > 0) if i != j]

    edges = pairs(fixed.adjacency)
    support = panel.eligible[panel.block > 0].sum(axis=0)
    nonedges: list[tuple[int, int]] = []
    for i, j in edges:
        candidates = [k for k in range(20) if k != i and fixed.adjacency[i, k] == 0]
        selected = min(candidates, key=lambda k: (abs(int(support[k]) - int(support[j])), k))
        nonedges.append((i, selected))
    return {"fixed": edges, "shuffled": pairs(shuffled.adjacency), "matched_nonedge": nonedges}


def residual_diagnostics(panel: Panel, register: Path) -> dict[str, Any]:
    require_freeze(register)
    sets = edge_sets(panel)
    records: list[dict[str, Any]] = []
    for stage in (1, 2, 3):
        keys = panel.selected((stage,))
        for lag in (0, 15, 30, 60):
            prior_times = panel.times[keys] - lag * 60_000_000
            prior = np.searchsorted(panel.times, prior_times)
            aligned = prior < len(panel.times)
            prior = np.minimum(prior, len(panel.times) - 1)
            aligned &= panel.times[prior] == prior_times
            for view in ("raw", "common_removed"):
                current_values = panel.residual[keys].copy()
                source_values = panel.residual[prior].copy()
                if view == "common_removed":
                    for values, positions in [(current_values, keys), (source_values, prior)]:
                        weights = panel.eligible[positions].copy()
                        if lag and values is source_values:
                            weights &= panel.maturity[positions] <= panel.times[keys, None]
                        common = (values * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1)
                        values -= common[:, None]
                for kind, edges in sets.items():
                    for source, target in edges:
                        valid = (
                            aligned & panel.eligible[keys, target] & panel.eligible[prior, source]
                        )
                        valid &= panel.block[prior] == stage
                        if lag:
                            valid &= panel.maturity[prior, source] <= panel.times[keys]
                        x, y = source_values[valid, source], current_values[valid, target]
                        correlation = None
                        if len(x) > 2 and np.std(x) > 0 and np.std(y) > 0:
                            correlation = float(np.corrcoef(x, y)[0, 1])
                        records.append(
                            {
                                "block": f"DEV_{stage}",
                                "lag_minutes": lag,
                                "view": view,
                                "edge_set": kind,
                                "source": ALL_INSTRUMENTS[source],
                                "target": ALL_INSTRUMENTS[target],
                                "rows": len(x),
                                "correlation": correlation,
                                "interpretation": "DESCRIPTIVE_NON_CAUSAL"
                                if lag == 0
                                else "MATURITY_FILTERED_LEAD_LAG",
                            }
                        )
    summaries: list[dict[str, Any]] = []
    for stage in (1, 2, 3):
        for lag in (0, 15, 30, 60):
            for view in ("raw", "common_removed"):
                for kind in sets:
                    subset = [
                        r
                        for r in records
                        if r["block"] == f"DEV_{stage}"
                        and r["lag_minutes"] == lag
                        and r["view"] == view
                        and r["edge_set"] == kind
                        and r["correlation"] is not None
                    ]
                    correlations = np.array([r["correlation"] for r in subset])
                    concentration = {
                        direction: {
                            node: float(
                                sum(abs(r["correlation"]) for r in subset if r[direction] == node)
                            )
                            for node in ALL_INSTRUMENTS
                        }
                        for direction in ("source", "target")
                    }
                    summaries.append(
                        {
                            "block": f"DEV_{stage}",
                            "lag_minutes": lag,
                            "view": view,
                            "edge_set": kind,
                            "pairs": len(subset),
                            "positive_pairs": int((correlations > 0).sum()),
                            "mean": float(correlations.mean()) if len(correlations) else None,
                            "quantiles": np.quantile(correlations, [0, 0.25, 0.5, 0.75, 1]).tolist()
                            if len(correlations)
                            else [],
                            "absolute_correlation_contribution": concentration,
                            "market_group_mean": {
                                group: float(
                                    np.mean(
                                        [
                                            r["correlation"]
                                            for r in subset
                                            if r["source"].startswith(group + ":")
                                        ]
                                    )
                                )
                                for group in ("commodity", "fx", "index")
                                if any(r["source"].startswith(group + ":") for r in subset)
                            },
                        }
                    )
    return {
        "matching_policy": (
            "Exact source; nearest target DEV support count; ties canonical node order; "
            "matching with replacement; outcome-blind"
        ),
        "edge_sets": {
            kind: [[ALL_INSTRUMENTS[i], ALL_INSTRUMENTS[j]] for i, j in edges]
            for kind, edges in sets.items()
        },
        "records": records,
        "summaries": summaries,
    }
