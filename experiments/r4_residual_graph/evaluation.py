"""Frozen §10 evaluation and historical nomination helpers for R4-P0.

The functions in this module are deliberately pure: they consume authenticated rows
and forecasts, apply the fixed instrument-balanced definitions, and return JSON-ready
records.  Execution is responsible for authenticating and persisting their inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from statistics import mean
from typing import Any, cast

import numpy as np

from .foundation import ALL_INSTRUMENTS, CORE_SIX

ALL_PERIODS = ("DEV_2", "DEV_3", "TERMINAL_FORMER_HOLDOUT")
DEVELOPMENT_PERIODS = ("DEV_2", "DEV_3")
CONTROL_NAMES = ("ZERO_RETURN", "LOCAL_RIDGE", "FULLY_POOLED_LOCAL_RIDGE")
FITTED_FAMILY_IDS = (
    "LOCAL_TEMPORAL_RESIDUAL",
    "POOLED_NON_GRAPH_RESIDUAL",
    "FIXED_ECONOMIC_GRAPH_RESIDUAL",
    "LEARNED_STATIC_GRAPH_RESIDUAL",
    "SHUFFLED_FIXED_GRAPH_RESIDUAL",
)

# R3.H is immutable historical context only.  It is deliberately kept as a
# compact source binding; no cost/profitability result is recomputed or promoted.
R3H_COST_GRID = {
    "status": "AVAILABLE_HISTORICAL",
    "evidence_class": "HISTORICAL_EXPLORATORY",
    "label": "POST_HOC_HISTORICAL_EXPLORATORY",
    "source_path": "docs/archive/r3/R3_HISTORICAL_EXPLORATORY_REPORT.md",
    "source_sha256": "74a371a5f4481893be1608072a3ee641b2d347b13f447156bab75e8ca5034b4d",
    "semantic_identity": "ac43c8f474652e43e4994131ea8fa56e99799992607e3ff46439d65b3c4a16fc",
    "executable": False,
    "profitability_claim": False,
    "aggregate_break_even_cost": 5.8401438e-05,
    "all_in_cost_sensitivity": [
        {
            "break_even_cost": 5.8401438e-05,
            "cost": cost,
            "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
            "net_mean": net_mean,
            "unit": "fraction_of_notional",
        }
        for cost, net_mean in zip(
            (0.0, 0.0005, 0.001, 0.002),
            (2.935e-09, -2.2192e-08, -4.7319e-08, -9.7573e-08),
            strict=True,
        )
    ],
    "aggregate_costs": [0.0, 0.0005, 0.001, 0.002],
    "aggregate_net_mean": [2.935e-09, -2.2192e-08, -4.7319e-08, -9.7573e-08],
}


def historical_r3h_cost_grid() -> dict[str, Any]:
    """Return a detached binding to the frozen, non-executable R3.H context."""
    return json.loads(json.dumps(R3H_COST_GRID, sort_keys=True))


def _records(rows: Iterable[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if hasattr(rows, "iter_rows"):
        table = cast(Any, rows)
        return [dict(row) for row in table.iter_rows(named=True)]
    return [dict(row) for row in rows]


def _finite(value: Any, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _period_rows(rows: Sequence[Mapping[str, Any]], period: str) -> list[Mapping[str, Any]]:
    return [row for row in rows if str(row["period"]) == period]


def _mse_values(rows: Sequence[Mapping[str, Any]], forecast_key: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        instrument = str(row["instrument_id"])
        error = _finite(row[forecast_key], forecast_key) - _finite(
            row["target_return"], "target_return"
        )
        grouped[instrument].append(error * error)
    return {instrument: float(mean(values)) for instrument, values in sorted(grouped.items())}


def mse_by_instrument(
    rows: Iterable[Mapping[str, Any]] | Any, forecast_key: str = "forecast"
) -> dict[str, float]:
    """Return row-mean squared loss for every instrument on exact supplied support."""
    records = _records(rows)
    if not records:
        raise ValueError("MSE support cannot be empty")
    return _mse_values(records, forecast_key)


def instrument_balanced_mse(
    rows: Iterable[Mapping[str, Any]] | Any,
    forecast_key: str = "forecast",
    instruments: Sequence[str] = ALL_INSTRUMENTS,
) -> float:
    """Return the equal-weighted mean of per-instrument row MSEs."""
    values = mse_by_instrument(rows, forecast_key)
    expected = tuple(instruments)
    if set(values) != set(expected):
        raise ValueError("MSE support must contain every frozen instrument")
    return float(sum(values[instrument] for instrument in expected) / len(expected))


def comparison_delta(
    candidate_rows: Iterable[Mapping[str, Any]] | Any,
    comparator_rows: Iterable[Mapping[str, Any]] | Any,
    candidate_key: str = "forecast",
    comparator_key: str = "forecast",
) -> dict[str, float]:
    """Return comparator MSE minus candidate MSE per instrument."""
    candidate = _records(candidate_rows)
    comparator = _records(comparator_rows)
    candidate_keys = {
        (str(row["period"]), str(row["instrument_id"]), str(row.get("decision_time", "")))
        for row in candidate
    }
    comparator_keys = {
        (str(row["period"]), str(row["instrument_id"]), str(row.get("decision_time", "")))
        for row in comparator
    }
    if candidate_keys != comparator_keys:
        raise ValueError("comparison requires exact common support")
    comparator_mse = _mse_values(comparator, comparator_key)
    candidate_mse = _mse_values(candidate, candidate_key)
    return {
        instrument: comparator_mse[instrument] - candidate_mse[instrument]
        for instrument in sorted(candidate_mse)
    }


def concentration_shares(
    deltas: Mapping[str, float],
    period_deltas: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Apply the fixed positive-contribution clipping only for concentration."""
    instrument_contributions = {key: max(0.0, float(value)) for key, value in deltas.items()}
    instrument_total = sum(instrument_contributions.values())
    instrument_share = (
        1.0
        if instrument_total == 0.0
        else max(instrument_contributions.values()) / instrument_total
    )
    result: dict[str, Any] = {
        "instrument_contributions": instrument_contributions,
        "best_instrument_share": float(instrument_share),
        "instrument_positive_sum": float(instrument_total),
        "concentration_gate": bool(instrument_total > 0.0 and instrument_share <= 0.8),
    }
    if period_deltas is not None:
        period_contributions = {key: max(0.0, float(value)) for key, value in period_deltas.items()}
        period_total = sum(period_contributions.values())
        period_share = (
            1.0 if period_total == 0.0 else max(period_contributions.values()) / period_total
        )
        result.update(
            {
                "period_contributions": period_contributions,
                "best_period_share": float(period_share),
                "period_positive_sum": float(period_total),
                "period_concentration_gate": bool(period_total > 0.0 and period_share <= 0.8),
            }
        )
    return result


def _average_tie_ranks(values: np.ndarray) -> np.ndarray:
    """Return deterministic one-based average ranks for tied values."""
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    ranks = np.empty(len(values), dtype=float)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = ((start + 1) + end) / 2.0
        start = end
    return ranks


def _correlation(x: np.ndarray, y: np.ndarray, method: str) -> float | None:
    if len(x) < 2 or np.ptp(x) == 0.0 or np.ptp(y) == 0.0:
        return None
    if method == "pearson":
        return float(np.corrcoef(x, y)[0, 1])
    ranks_x = _average_tie_ranks(x)
    ranks_y = _average_tie_ranks(y)
    return float(np.corrcoef(ranks_x, ranks_y)[0, 1])


def _calibration(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    if len(x) < 2 or np.ptp(x) == 0.0:
        return None, None
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def _diagnostics(rows: Sequence[Mapping[str, Any]], forecast_key: str) -> dict[str, Any]:
    targets = np.asarray(
        [_finite(row["target_return"], "target_return") for row in rows], dtype=float
    )
    forecasts = np.asarray([_finite(row[forecast_key], forecast_key) for row in rows], dtype=float)
    errors = forecasts - targets
    residual_corrections = np.asarray(
        [_finite(row["residual_correction"], "residual_correction") for row in rows],
        dtype=float,
    )
    directions = np.sign(forecasts) == np.sign(targets)
    buckets: list[dict[str, Any]] = []
    if len(rows) >= 5 and np.ptp(forecasts) > 0:
        order = np.argsort(forecasts, kind="stable")
        for bucket_index, indices in enumerate(np.array_split(order, 5), start=1):
            if len(indices):
                buckets.append(
                    {
                        "bucket": bucket_index,
                        "count": len(indices),
                        "mean_forecast": float(np.mean(forecasts[indices])),
                        "mean_target": float(np.mean(targets[indices])),
                    }
                )
    return {
        "count": len(rows),
        "mse": float(np.mean(errors * errors)),
        "mae": float(np.mean(np.abs(errors))),
        "pearson": _correlation(forecasts, targets, "pearson"),
        "spearman": _correlation(forecasts, targets, "spearman"),
        "calibration_slope": _calibration(forecasts, targets)[0],
        "calibration_intercept": _calibration(forecasts, targets)[1],
        "direction_accuracy": float(np.mean(directions)) if len(rows) else None,
        "forecast_mean": float(np.mean(forecasts)),
        "forecast_std": float(np.std(forecasts)),
        "target_mean": float(np.mean(targets)),
        "target_std": float(np.std(targets)),
        "mean_error": float(np.mean(errors)),
        "forecast_magnitude_mean": float(np.mean(np.abs(forecasts))),
        "target_magnitude_mean": float(np.mean(np.abs(targets))),
        "residual_correction_magnitude_mean": float(np.mean(np.abs(residual_corrections))),
        "residual_correction_magnitude_std": float(np.std(np.abs(residual_corrections))),
        "residual_correction_magnitude_max": float(np.max(np.abs(residual_corrections))),
        "forecast_buckets": buckets,
    }


def _metric_for_rows(rows: Sequence[Mapping[str, Any]], forecast_key: str) -> dict[str, Any]:
    if not rows:
        raise ValueError("evaluation support cannot be empty")
    per_instrument: dict[str, dict[str, Any]] = {}
    for instrument in sorted({str(row["instrument_id"]) for row in rows}):
        instrument_rows = [row for row in rows if str(row["instrument_id"]) == instrument]
        per_instrument[instrument] = _diagnostics(instrument_rows, forecast_key)
    aggregate = instrument_balanced_mse(rows, forecast_key, tuple(sorted(per_instrument)))
    result = _diagnostics(rows, forecast_key)
    result.update(
        {
            "instrument_balanced_mse": aggregate,
            "per_instrument": per_instrument,
            "coverage": {
                "row_count": len(rows),
                "instrument_count": len(per_instrument),
                "instruments": sorted(per_instrument),
            },
            "market_groups": {
                group: _diagnostics(
                    [row for row in rows if str(row["instrument_id"]).split(":", 1)[0] == group],
                    forecast_key,
                )
                for group in ("commodity", "fx", "index")
                if any(str(row["instrument_id"]).startswith(f"{group}:") for row in rows)
            },
        }
    )
    return result


def _combine_rows(
    rows: Sequence[Mapping[str, Any]], periods: Sequence[str]
) -> list[Mapping[str, Any]]:
    return [row for row in rows if str(row["period"]) in periods]


def evaluate_forecast_rows(
    rows: Iterable[Mapping[str, Any]] | Any,
    *,
    forecast_key: str = "forecast",
    comparator_keys: Mapping[str, str] | None = None,
    expected_periods: Sequence[str] = ALL_PERIODS,
) -> dict[str, Any]:
    """Evaluate one forecast and frozen controls on exact common support."""
    records = _records(rows)
    if not records:
        raise ValueError("evaluation rows cannot be empty")
    periods = tuple(str(period) for period in expected_periods)
    result: dict[str, Any] = {
        "forecast_key": forecast_key,
        "periods": {},
        "combined_development": None,
        "controls": {},
    }
    for period in periods:
        period_rows = _period_rows(records, period)
        if not period_rows:
            raise ValueError(f"evaluation support is missing period {period}")
        result["periods"][period] = _metric_for_rows(period_rows, forecast_key)
    combined = _combine_rows(records, DEVELOPMENT_PERIODS)
    if all(str(period) in periods for period in DEVELOPMENT_PERIODS):
        result["combined_development"] = _metric_for_rows(combined, forecast_key)
    if comparator_keys:
        for name, key in comparator_keys.items():
            control = evaluate_forecast_rows(
                records,
                forecast_key=key,
                expected_periods=periods,
            )
            result["controls"][name] = control
            deltas: dict[str, float] = {}
            for period in periods:
                candidate_metric = result["periods"][period]
                control_metric = control["periods"][period]
                deltas[period] = float(
                    control_metric["instrument_balanced_mse"]
                    - candidate_metric["instrument_balanced_mse"]
                )
            result.setdefault("deltas", {})[name] = deltas
            combined_result = result.get("combined_development")
            combined_control = control.get("combined_development")
            if isinstance(combined_result, Mapping) and isinstance(combined_control, Mapping):
                deltas["combined_development"] = float(
                    combined_control["instrument_balanced_mse"]
                    - combined_result["instrument_balanced_mse"]
                )
    return result


def _payload_rows(
    payloads: Iterable[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    periods: Sequence[str],
) -> list[dict[str, Any]]:
    outcome_map: dict[str, Mapping[str, Any]] = {}
    expected_keys_by_period: dict[str, list[str]] = defaultdict(list)
    if isinstance(outcomes, Mapping):
        typed_outcomes = cast(Mapping[str, Mapping[str, Any]], outcomes)
        for raw_key, value in typed_outcomes.items():
            key = str(raw_key)
            if key in outcome_map:
                raise ValueError(f"duplicate evaluation outcome key: {key}")
            outcome_map[key] = value
            if not isinstance(value, Mapping):
                raise ValueError("evaluation mapping outcomes require canonical mapping records")
            value_record = value
            if value_record.get("period") is not None:
                expected_keys_by_period[str(value_record["period"])].append(key)
    else:
        for row in outcomes:
            key = str(
                row.get("target_key")
                or row.get("key")
                or f"{row['instrument_id']}|{row['decision_time']}"
            )
            if key in outcome_map:
                raise ValueError(f"duplicate evaluation outcome key: {key}")
            outcome_map[key] = row
            expected_keys_by_period[str(row["period"])].append(key)
    result: list[dict[str, Any]] = []
    payload_list = list(payloads)
    if not payload_list:
        raise ValueError("evaluation requires forecast payloads")
    seen_slots: set[str] = set()
    for payload in payload_list:
        stage = str(payload.get("stage") or str(payload["slot_id"]).split(":", 2)[-1])
        if stage not in periods:
            continue
        slot_id = str(payload["slot_id"])
        if slot_id in seen_slots:
            raise ValueError("evaluation contains duplicate payload slot")
        seen_slots.add(slot_id)
        keys = tuple(str(key) for key in payload["target_keys"])
        if len(set(keys)) != len(keys):
            raise ValueError("forecast payload contains duplicate target keys")
        expected_keys = expected_keys_by_period.get(stage)
        for key in keys:
            value = outcome_map.get(key)
            if isinstance(value, Mapping) and value.get("period") not in (None, stage):
                raise ValueError("outcome period does not match forecast stage")
        if expected_keys is None or set(expected_keys) != set(keys):
            raise ValueError("outcome key is out of forecast support")
        if keys != tuple(expected_keys):
            raise ValueError("forecast payload target keys differ from canonical support order")
        total = payload.get("total_forecast")
        residual = payload.get("residual_prediction")
        local = payload.get("local_ridge_forecast")
        pooled = payload.get("fully_pooled_local_ridge_forecast")
        if not all(isinstance(values, list) for values in (total, residual, local, pooled)):
            raise ValueError(
                "forecast payload requires canonical total, residual, local, and pooled arrays"
            )
        total_values = cast(list[Any], total)
        residual_values = cast(list[Any], residual)
        local_values = cast(list[Any], local)
        pooled_values = cast(list[Any], pooled)
        if (
            len(keys) != len(total_values)
            or len(keys) != len(residual_values)
            or len(keys) != len(local_values)
            or len(keys) != len(pooled_values)
        ):
            raise ValueError("forecast payload arrays are misaligned")
        for (
            key,
            local_value,
            residual_value,
            total_value,
            pooled_value,
        ) in zip(keys, local_values, residual_values, total_values, pooled_values, strict=True):
            outcome = outcome_map.get(key)
            if outcome is None:
                raise ValueError(f"evaluation outcome is missing for {key}")
            if not isinstance(outcome, Mapping):
                raise ValueError("evaluation outcomes require canonical mapping records")
            outcome_record = outcome
            target = _finite(outcome_record["target_return"], "target_return")
            period = str(outcome_record.get("period", stage))
            instrument = str(outcome_record.get("instrument_id", key.split("|", 1)[0]))
            decision_time = outcome_record.get("decision_time", key.rsplit("|", 1)[-1])
            if period != stage:
                raise ValueError("outcome period does not match forecast stage")
            total_numeric = _finite(total_value, "total_forecast")
            residual_numeric = _finite(residual_value, "residual_prediction")
            local_numeric = _finite(local_value, "local_ridge_forecast")
            pooled_numeric = _finite(pooled_value, "fully_pooled_local_ridge_forecast")
            canonical_total = float(
                np.add(np.float32(local_numeric), np.float32(residual_numeric), dtype=np.float32)
            )
            if total_numeric != canonical_total:
                raise ValueError("total forecast is not local forecast plus residual")
            result.append(
                {
                    "period": stage,
                    "instrument_id": instrument,
                    "decision_time": decision_time,
                    "target_return": target,
                    "forecast": total_numeric,
                    "residual_correction": residual_numeric,
                    "local_forecast": local_numeric,
                    "pooled_local_forecast": pooled_numeric,
                    "zero_forecast": 0.0,
                    "payload_slot_id": str(payload["slot_id"]),
                    "seed": int(str(payload["slot_id"]).split(":", 2)[1]),
                    "family_id": str(payload["slot_id"]).split(":", 2)[0],
                    "support_identity": payload.get("support_identity")
                    or payload.get("linear_control_support_identity"),
                    "linear_control_identity": payload.get("linear_control_identity"),
                    "linear_control_identities": payload.get("linear_control_identities"),
                }
            )
    expected_keys = {
        str(key)
        for payload in payload_list
        for key in payload["target_keys"]
        if str(payload.get("stage") or str(payload["slot_id"]).split(":", 2)[-1]) in periods
    }
    unexpected = set(outcome_map) - expected_keys
    if unexpected:
        raise ValueError(f"evaluation outcome is out of forecast support: {sorted(unexpected)[0]}")
    if not result:
        raise ValueError("evaluation support is empty")
    return result


def _deduplicate_control_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated per-family control payloads onto one exact support.

    Fitted slots repeat the same authenticated linear controls.  Metrics and
    coverage must not count those repeats as extra observations; disagreement
    indicates a binding failure rather than a weighting choice.
    """
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row["period"]), str(row["instrument_id"]), str(row["decision_time"]))
        grouped[key].append(row)
    result: list[dict[str, Any]] = []
    for key in sorted(grouped):
        entries = grouped[key]
        first = entries[0]
        for entry in entries[1:]:
            for field in ("local_forecast", "pooled_local_forecast", "zero_forecast"):
                if not math.isclose(
                    float(first[field]), float(entry[field]), rel_tol=0.0, abs_tol=1e-12
                ):
                    raise ValueError("linear control values drifted across repeated support")
            for field in (
                "support_identity",
                "linear_control_identity",
                "linear_control_identities",
            ):
                if first.get(field) != entry.get(field):
                    raise ValueError("linear control identity drifted across repeated support")
        result.append(dict(first))
    return result


def _control_binding(rows: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any]:
    # Controls bind period-specific target keys and values, even for ZERO_RETURN.
    by_period: dict[str, set[str | None]] = defaultdict(set)
    for row in rows:
        controls = row.get("linear_control_identities")
        identity = controls.get(name) if isinstance(controls, Mapping) else None
        by_period[str(row["period"])].add(identity)
    period_identities: dict[str, str | None] = {}
    for period, identities in sorted(by_period.items()):
        if len(identities) != 1:
            raise ValueError(f"control identity drifted for {name} in {period}")
        period_identities[period] = next(iter(identities))
    if len(period_identities) == 1:
        identity = next(iter(period_identities.values()))
    elif any(value is not None for value in period_identities.values()):
        identity = hashlib.sha256(
            json.dumps(period_identities, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
    else:
        identity = None
    support = {
        row.get("support_identity") for row in rows if row.get("support_identity") is not None
    }
    if len(support) > 1:
        raise ValueError(f"control support identity drifted for {name}")
    value_key = {
        "ZERO_RETURN": "zero_forecast",
        "LOCAL_RIDGE": "local_forecast",
        "FULLY_POOLED_LOCAL_RIDGE": "pooled_local_forecast",
    }[name]
    values = [
        (
            str(row["period"]),
            str(row["instrument_id"]),
            str(row["decision_time"]),
            float(row[value_key]),
        )
        for row in rows
    ]
    content_identity = hashlib.sha256(
        json.dumps(values, separators=(",", ":"), sort_keys=False).encode("utf-8")
    ).hexdigest()
    return {
        "identity": identity,
        "period_identities": period_identities,
        "support_identity": next(iter(support), None),
        "content_identity": content_identity,
    }


def _family_metrics(
    rows: Sequence[Mapping[str, Any]],
    family_id: str,
    seed: int | None = None,
    *,
    fitted_comparators: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if row["family_id"] == family_id and (seed is None or int(row["seed"]) == seed)
    ]
    if not selected:
        raise ValueError(f"evaluation rows missing family {family_id}")
    selected = [dict(row) for row in selected]
    if fitted_comparators:
        selected_keys = tuple(
            (str(row["period"]), str(row["instrument_id"]), str(row["decision_time"]))
            for row in selected
        )
        for comparator_family, comparator_rows in fitted_comparators.items():
            comparator_by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
            for row in comparator_rows:
                key = (str(row["period"]), str(row["instrument_id"]), str(row["decision_time"]))
                if key in comparator_by_key:
                    raise ValueError(f"duplicate fitted comparator support: {comparator_family}")
                comparator_by_key[key] = row
            if tuple(comparator_by_key) != selected_keys:
                raise ValueError(
                    f"fitted comparator support differs for {family_id} versus {comparator_family}"
                )
            for row in selected:
                key = (str(row["period"]), str(row["instrument_id"]), str(row["decision_time"]))
                row[f"fitted:{comparator_family}"] = float(comparator_by_key[key]["forecast"])
    forecasts: dict[str, str] = {
        family_id: "forecast",
        "ZERO_RETURN": "zero_forecast",
        "LOCAL_RIDGE": "local_forecast",
        "FULLY_POOLED_LOCAL_RIDGE": "pooled_local_forecast",
    }
    comparator_keys = {
        name: key
        for name, key in (
            ("ZERO_RETURN", "zero_forecast"),
            ("LOCAL_RIDGE", "local_forecast"),
            ("FULLY_POOLED_LOCAL_RIDGE", "pooled_local_forecast"),
        )
        if name != family_id
    }
    if fitted_comparators:
        comparator_keys.update(
            {name: f"fitted:{name}" for name in fitted_comparators if name != family_id}
        )
    result = evaluate_forecast_rows(
        selected,
        forecast_key=forecasts[family_id],
        comparator_keys=comparator_keys,
        expected_periods=tuple(sorted({str(row["period"]) for row in selected})),
    )
    deltas_by_instrument: dict[str, dict[str, dict[str, float]]] = {}
    concentration: dict[str, dict[str, Any]] = {}
    for name, key in comparator_keys.items():
        period_deltas: dict[str, dict[str, float]] = {}
        concentration[name] = {}
        for period in result["periods"]:
            period_rows = [row for row in selected if str(row["period"]) == period]
            candidate_rows = [dict(row, forecast=row["forecast"]) for row in period_rows]
            comparator_rows = [dict(row, forecast=row[key]) for row in period_rows]
            deltas = comparison_delta(candidate_rows, comparator_rows)
            period_deltas[period] = deltas
            concentration[name][period] = concentration_shares(deltas)
        deltas_by_instrument[name] = period_deltas
        combined_rows = [row for row in selected if str(row["period"]) in DEVELOPMENT_PERIODS]
        if combined_rows:
            candidate_rows = [dict(row, forecast=row["forecast"]) for row in combined_rows]
            comparator_rows = [dict(row, forecast=row[key]) for row in combined_rows]
            period_deltas["combined_development"] = comparison_delta(
                candidate_rows, comparator_rows
            )
            concentration[name]["combined_development"] = concentration_shares(
                period_deltas["combined_development"]
            )
    result["deltas_by_instrument"] = deltas_by_instrument
    result["incremental_mse"] = dict(result.get("deltas", {}))
    zero_periods = result["controls"].get("ZERO_RETURN", {}).get("periods", {})
    direct_zero_skill: dict[str, float | None] = {}
    for period, candidate_metric in result.get("periods", {}).items():
        if period not in zero_periods:
            continue
        zero_mse = float(zero_periods[period]["instrument_balanced_mse"])
        candidate_mse = float(candidate_metric["instrument_balanced_mse"])
        direct_zero_skill[period] = None if zero_mse == 0.0 else 1.0 - candidate_mse / zero_mse
    combined_result = result.get("combined_development")
    combined_zero = result["controls"].get("ZERO_RETURN", {}).get("combined_development")
    if isinstance(combined_result, Mapping) and isinstance(combined_zero, Mapping):
        zero_mse = float(combined_zero["instrument_balanced_mse"])
        candidate_mse = float(combined_result["instrument_balanced_mse"])
        direct_zero_skill["combined_development"] = (
            None if zero_mse == 0.0 else 1.0 - candidate_mse / zero_mse
        )
    result["direct_zero_skill"] = direct_zero_skill
    result["concentration"] = concentration
    result["family_id"] = family_id
    if seed is not None:
        result["seed"] = seed
    return result


def evaluate_development_register(
    payloads: Iterable[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    identities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the authenticated development metric/gate register in memory."""
    rows = _payload_rows(payloads, outcomes, periods=DEVELOPMENT_PERIODS)
    control_rows = _deduplicate_control_rows(rows)
    families = sorted({str(row["family_id"]) for row in rows})
    rows_by_family_seed = {
        (family, seed): [
            row for row in rows if str(row["family_id"]) == family and int(row["seed"]) == seed
        ]
        for family in families
        for seed in {int(row["seed"]) for row in rows if str(row["family_id"]) == family}
    }
    candidate_metrics: dict[str, Any] = {}
    for family in families:
        family_rows = [row for row in rows if row["family_id"] == family]
        family_seeds = sorted({int(row["seed"]) for row in family_rows})
        if not family_seeds:
            raise ValueError(f"evaluation rows missing family {family}")
        primary_seed = family_seeds[0]
        candidate_metrics[family] = {
            "seeds": {
                str(seed): _family_metrics(
                    rows_by_family_seed[(family, seed)],
                    family,
                    seed,
                    fitted_comparators={
                        other_family: rows_by_family_seed[(other_family, seed)]
                        for other_family in families
                        if other_family != family and (other_family, seed) in rows_by_family_seed
                    },
                )
                for seed in family_seeds
            },
            "primary": _family_metrics(
                rows_by_family_seed[(family, primary_seed)],
                family,
                primary_seed,
                fitted_comparators={
                    other_family: rows_by_family_seed[(other_family, primary_seed)]
                    for other_family in families
                    if other_family != family
                    and (other_family, primary_seed) in rows_by_family_seed
                },
            ),
        }
        candidate_metrics[family]["primary"]["core_six"] = {
            period: _metric_for_rows(
                [
                    row
                    for row in family_rows
                    if str(row["period"]) == period and str(row["instrument_id"]) in CORE_SIX
                ],
                "forecast",
            )
            for period in DEVELOPMENT_PERIODS
        }

    control_keys = {
        "ZERO_RETURN": "zero_forecast",
        "LOCAL_RIDGE": "local_forecast",
        "FULLY_POOLED_LOCAL_RIDGE": "pooled_local_forecast",
    }
    controls: dict[str, Any] = {}
    control_bindings: dict[str, Any] = {}
    for name, key in control_keys.items():
        control = evaluate_forecast_rows(
            control_rows,
            forecast_key=key,
            expected_periods=DEVELOPMENT_PERIODS,
        )
        binding = _control_binding(control_rows, name)
        control["identity"] = binding["identity"]
        control["support_identity"] = binding["support_identity"]
        control["content_identity"] = binding["content_identity"]
        controls[name] = control
        control_bindings[name] = binding

    coverage = {
        period: {
            instrument: sum(
                1
                for row in control_rows
                if str(row["period"]) == period and str(row["instrument_id"]) == instrument
            )
            for instrument in ALL_INSTRUMENTS
        }
        for period in DEVELOPMENT_PERIODS
    }
    if any(
        set(coverage[period]) != set(ALL_INSTRUMENTS)
        or any(count <= 0 for count in coverage[period].values())
        for period in DEVELOPMENT_PERIODS
    ):
        raise ValueError(
            "development evaluation support must contain positive coverage for every "
            "frozen instrument"
        )
    support_keys = {
        period: [
            f"{row['instrument_id']}|{row['decision_time']}"
            for row in control_rows
            if str(row["period"]) == period
        ]
        for period in DEVELOPMENT_PERIODS
    }
    support_identity = next(
        (
            row.get("support_identity")
            for row in control_rows
            if row.get("support_identity") is not None
        ),
        None,
    )
    register: dict[str, Any] = {
        "artifact_type": "R4.C_DEVELOPMENT_METRIC_GATE_REGISTER",
        "evaluation_policy": "R4_P0_SECTION_10",
        "periods": list(DEVELOPMENT_PERIODS),
        "families": candidate_metrics,
        "controls": controls,
        "control_bindings": control_bindings,
        "coverage": coverage,
        "support": {
            "identity": support_identity,
            "periods": support_keys,
            "weighting": "union_within_instrument_then_equal_twenty",
            "order": "canonical_payload_order",
        },
        "row_count": len(rows),
        "support_row_count": len(control_rows),
        "total_forecast_definition": "local_ridge_forecast_plus_residual_correction",
        "weighting": "union_within_instrument_then_equal_twenty",
        "identities": dict(identities or {}),
        "r3h_cost_grid": historical_r3h_cost_grid(),
    }
    return register


def _merge_metric_views(
    development: Mapping[str, Any], terminal: Mapping[str, Any]
) -> dict[str, Any]:
    """Join development and terminal metric views without reweighting either block."""
    merged = dict(terminal)
    merged["periods"] = {
        **dict(development.get("periods", {})),
        **dict(terminal.get("periods", {})),
    }
    merged["combined_development"] = development.get("combined_development")
    merged["deltas"] = {
        name: {
            **dict(development.get("deltas", {}).get(name, {})),
            **dict(terminal.get("deltas", {}).get(name, {})),
        }
        for name in sorted(set(development.get("deltas", {})) | set(terminal.get("deltas", {})))
    }
    merged["deltas_by_instrument"] = {
        name: {
            **dict(development.get("deltas_by_instrument", {}).get(name, {})),
            **dict(terminal.get("deltas_by_instrument", {}).get(name, {})),
        }
        for name in sorted(
            set(development.get("deltas_by_instrument", {}))
            | set(terminal.get("deltas_by_instrument", {}))
        )
    }
    merged["concentration"] = {
        name: {
            **dict(development.get("concentration", {}).get(name, {})),
            **dict(terminal.get("concentration", {}).get(name, {})),
        }
        for name in sorted(
            set(development.get("concentration", {})) | set(terminal.get("concentration", {}))
        )
    }
    merged["core_six"] = development.get("core_six", {})
    controls: dict[str, Any] = {}
    for name in sorted(set(development.get("controls", {})) | set(terminal.get("controls", {}))):
        dev_control = development.get("controls", {}).get(name, {})
        terminal_control = terminal.get("controls", {}).get(name, {})
        control = dict(terminal_control)
        control["periods"] = {
            **dict(dev_control.get("periods", {})),
            **dict(terminal_control.get("periods", {})),
        }
        control["combined_development"] = dev_control.get("combined_development")
        controls[name] = control
    merged["controls"] = controls
    return merged


def evaluate_terminal_register(
    payloads: Iterable[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    development_register: Mapping[str, Any],
    identities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate terminal rows and immutably join the closed development register."""
    if development_register.get("artifact_type") != "R4.C_DEVELOPMENT_METRIC_GATE_REGISTER":
        raise ValueError("terminal evaluation requires a closed development metric register")
    terminal_rows = _payload_rows(payloads, outcomes, periods=(_TERMINAL_STAGE,))
    terminal_control_rows = _deduplicate_control_rows(terminal_rows)
    terminal_families: dict[str, Any] = {}
    terminal_family_ids = sorted({str(row["family_id"]) for row in terminal_rows})
    terminal_rows_by_family_seed = {
        (family, seed): [
            row
            for row in terminal_rows
            if str(row["family_id"]) == family and int(row["seed"]) == seed
        ]
        for family in terminal_family_ids
        for seed in {int(row["seed"]) for row in terminal_rows if str(row["family_id"]) == family}
    }
    for family in terminal_family_ids:
        family_rows = [row for row in terminal_rows if row["family_id"] == family]
        terminal_seeds = {
            str(seed): _family_metrics(
                terminal_rows_by_family_seed[(family, seed)],
                family,
                seed,
                fitted_comparators={
                    other_family: terminal_rows_by_family_seed[(other_family, seed)]
                    for other_family in terminal_family_ids
                    if other_family != family
                    and (other_family, seed) in terminal_rows_by_family_seed
                },
            )
            for seed in sorted({int(row["seed"]) for row in family_rows})
        }
        dev_entry = development_register.get("families", {}).get(family, {})
        dev_seeds = dev_entry.get("seeds", {}) if isinstance(dev_entry, Mapping) else {}
        merged_seeds = {
            seed: _merge_metric_views(dev_seeds[seed], metric) if seed in dev_seeds else metric
            for seed, metric in terminal_seeds.items()
        }
        for seed, merged in merged_seeds.items():
            terminal_core_six = _metric_for_rows(
                [
                    row
                    for row in terminal_rows_by_family_seed[(family, int(seed))]
                    if str(row["instrument_id"]) in CORE_SIX
                ],
                "forecast",
            )
            core_six = dict(merged.get("core_six", {}))
            core_six[_TERMINAL_STAGE] = terminal_core_six
            merged["core_six"] = core_six
        primary_seed = min(merged_seeds, key=int)
        terminal_families[family] = {
            "seeds": merged_seeds,
            "primary": merged_seeds[primary_seed],
        }

    control_keys = {
        "ZERO_RETURN": "zero_forecast",
        "LOCAL_RIDGE": "local_forecast",
        "FULLY_POOLED_LOCAL_RIDGE": "pooled_local_forecast",
    }
    controls: dict[str, Any] = {}
    control_bindings: dict[str, Any] = {}
    for name, key in control_keys.items():
        control = evaluate_forecast_rows(
            terminal_control_rows,
            forecast_key=key,
            expected_periods=(_TERMINAL_STAGE,),
        )
        binding = _control_binding(terminal_control_rows, name)
        control["identity"] = binding["identity"]
        control["support_identity"] = binding["support_identity"]
        control["content_identity"] = binding["content_identity"]
        controls[name] = control
        control_bindings[name] = binding
    coverage = {
        _TERMINAL_STAGE: {
            instrument: sum(
                1 for row in terminal_control_rows if str(row["instrument_id"]) == instrument
            )
            for instrument in ALL_INSTRUMENTS
        }
    }
    if set(coverage[_TERMINAL_STAGE]) != set(ALL_INSTRUMENTS) or any(
        count <= 0 for count in coverage[_TERMINAL_STAGE].values()
    ):
        raise ValueError(
            "terminal evaluation support must contain positive coverage for every frozen instrument"
        )
    result: dict[str, Any] = {
        "artifact_type": "R4.C_TERMINAL_METRIC_GATE_REGISTER",
        "evaluation_policy": "R4_P0_SECTION_10",
        "periods": ["DEV_2", "DEV_3", _TERMINAL_STAGE],
        "row_count": len(terminal_rows),
        "support_row_count": len(terminal_control_rows),
        "families": terminal_families,
        "controls": controls,
        "control_bindings": control_bindings,
        "coverage": {**dict(development_register.get("coverage", {})), **coverage},
        "development_metric_register_identity": development_register.get("metric_identity"),
        "development_metric_register": dict(development_register),
        "identities": dict(identities or {}),
        "weighting": "union_within_instrument_then_equal_twenty",
        "total_forecast_definition": "local_ridge_forecast_plus_residual_correction",
        "r3h_cost_grid": historical_r3h_cost_grid(),
        "scientific_performance": "COMPUTED_POST_HOC_HISTORICAL_EXPLORATORY",
    }
    return result


def apply_historical_nomination_rule(
    evaluation: Mapping[str, Any],
    *,
    candidate: str,
    pooled_name: str = "POOLED_NON_GRAPH_RESIDUAL",
) -> dict[str, Any]:
    """Apply the fixed nine-gate §10.5 nomination rule."""
    allowed_candidates = {
        "FIXED_ECONOMIC_GRAPH_RESIDUAL",
        "LEARNED_STATIC_GRAPH_RESIDUAL",
    }
    if candidate not in allowed_candidates:
        raise ValueError("historical nomination candidate must be an authorised graph family")
    families = evaluation.get("families", {})
    candidate_entry = families.get(candidate)
    pooled_entry = families.get(pooled_name)
    if not isinstance(candidate_entry, Mapping):
        raise ValueError(f"candidate family is missing: {candidate}")
    if not isinstance(pooled_entry, Mapping):
        raise ValueError(f"pooled comparator family is missing: {pooled_name}")
    primary = candidate_entry.get("primary")
    pooled_primary = pooled_entry.get("primary")
    if not isinstance(primary, Mapping) or not isinstance(pooled_primary, Mapping):
        raise ValueError("candidate and pooled primary metrics are required")
    periods = ("DEV_2", "DEV_3", _TERMINAL_STAGE)
    candidate_periods = primary.get("periods", {})
    pooled_periods = pooled_primary.get("periods", {})
    period_improvement = {
        period: (
            period in candidate_periods
            and period in pooled_periods
            and float(candidate_periods[period]["instrument_balanced_mse"])
            < float(pooled_periods[period]["instrument_balanced_mse"])
        )
        for period in periods
    }
    direct = primary.get("combined_development", {})
    controls = primary.get("controls", {})
    zero = controls.get("ZERO_RETURN", {}) if isinstance(controls, Mapping) else {}
    direct_gate = float(
        zero.get("combined_development", {}).get("instrument_balanced_mse", math.inf)
    ) > float(direct.get("instrument_balanced_mse", math.inf)) and float(
        zero.get("periods", {}).get(_TERMINAL_STAGE, {}).get("instrument_balanced_mse", math.inf)
    ) > float(candidate_periods.get(_TERMINAL_STAGE, {}).get("instrument_balanced_mse", math.inf))
    deltas = primary.get("deltas_by_instrument", {}).get(pooled_name, {})
    breadth = {
        period: sum(float(value) > 0.0 for value in deltas.get(period, {}).values()) >= 7
        for period in ("combined_development", _TERMINAL_STAGE)
    }
    instrument_contributions = {
        instrument: max(
            0.0,
            mean(float(deltas.get(period, {}).get(instrument, 0.0)) for period in periods),
        )
        for instrument in ALL_INSTRUMENTS
    }
    instrument_total = sum(instrument_contributions.values())
    instrument_share = (
        1.0
        if instrument_total == 0.0
        else max(instrument_contributions.values()) / instrument_total
    )
    period_contributions = [
        max(0.0, float(primary.get("deltas", {}).get(pooled_name, {}).get(period, 0.0)))
        for period in periods
    ]
    period_total = sum(period_contributions)
    period_share = 1.0 if period_total == 0.0 else max(period_contributions) / period_total
    concentration_gate = (
        instrument_total > 0.0
        and instrument_share <= 0.8
        and period_total > 0.0
        and period_share <= 0.8
    )
    seed_gate_periods = ("combined_development", _TERMINAL_STAGE)
    seeds = candidate_entry.get("seeds", {})
    stable_seed_count = sum(
        1
        for seed_metric in seeds.values()
        if isinstance(seeds, Mapping)
        if isinstance(seed_metric, Mapping)
        and all(
            float(seed_metric.get("deltas", {}).get(pooled_name, {}).get(period, 0.0)) > 0.0
            for period in seed_gate_periods
        )
    )
    seed_gate = stable_seed_count >= 2

    def _primary(name: str) -> Mapping[str, Any] | None:
        entry = families.get(name)
        value = entry.get("primary") if isinstance(entry, Mapping) else None
        return value if isinstance(value, Mapping) else None

    fixed, learned, shuffled = (
        _primary("FIXED_ECONOMIC_GRAPH_RESIDUAL"),
        _primary("LEARNED_STATIC_GRAPH_RESIDUAL"),
        _primary("SHUFFLED_FIXED_GRAPH_RESIDUAL"),
    )

    def _better(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        return all(
            float(left.get("periods", {}).get(period, {}).get("instrument_balanced_mse", math.inf))
            < float(
                right.get("periods", {}).get(period, {}).get("instrument_balanced_mse", -math.inf)
            )
            for period in periods
        )

    graph_controls = True
    if candidate == "FIXED_ECONOMIC_GRAPH_RESIDUAL":
        graph_controls = fixed is not None and shuffled is not None and _better(fixed, shuffled)
    elif candidate == "LEARNED_STATIC_GRAPH_RESIDUAL":
        graph_controls = (
            learned is not None
            and fixed is not None
            and shuffled is not None
            and _better(learned, fixed)
            and _better(learned, shuffled)
        )
    calibration_gate = (
        primary.get("combined_development", {}).get("calibration_slope") is not None
        and float(primary["combined_development"]["calibration_slope"]) > 0.0
        and primary.get("periods", {}).get(_TERMINAL_STAGE, {}).get("calibration_slope") is not None
        and float(primary["periods"][_TERMINAL_STAGE]["calibration_slope"]) > 0.0
    )
    coverage = evaluation.get("coverage", {})
    coverage_gate = all(
        set(coverage.get(period, {})) == set(ALL_INSTRUMENTS)
        and all(int(count) > 0 for count in coverage.get(period, {}).values())
        for period in periods
    )
    gates = {
        "development_period_improvement": period_improvement["DEV_2"]
        and period_improvement["DEV_3"],
        "terminal_improvement": period_improvement[_TERMINAL_STAGE],
        "direct_skill": direct_gate,
        "breadth": breadth["combined_development"] and breadth[_TERMINAL_STAGE],
        "concentration": concentration_gate,
        "seed_stability": seed_gate,
        "graph_controls": graph_controls,
        "calibration": calibration_gate,
        "coverage": coverage_gate,
    }
    verdict = "HYPOTHESIS_NOMINATED" if all(gates.values()) else "NO_HYPOTHESIS_NOMINATED"
    return {
        "candidate": candidate,
        "verdict": verdict,
        "gates": gates,
        "period_improvement": period_improvement,
        "breadth_by_block": breadth,
        "concentration": {
            "instrument_contributions": instrument_contributions,
            "best_instrument_share": instrument_share,
            "period_contributions": {
                period: contribution
                for period, contribution in zip(periods, period_contributions, strict=True)
            },
            "best_period_share": period_share,
        },
        "seed_positive_count": stable_seed_count,
        "calibration": {
            "combined_development_slope": primary.get("combined_development", {}).get(
                "calibration_slope"
            ),
            "terminal_slope": primary.get("periods", {})
            .get(_TERMINAL_STAGE, {})
            .get("calibration_slope"),
        },
        "nomination_rule": "R4_P0_SECTION_10_5",
    }


_TERMINAL_STAGE = "TERMINAL_FORMER_HOLDOUT"
__all__ = [
    "ALL_INSTRUMENTS",
    "ALL_PERIODS",
    "CORE_SIX",
    "DEVELOPMENT_PERIODS",
    "apply_historical_nomination_rule",
    "comparison_delta",
    "concentration_shares",
    "evaluate_development_register",
    "evaluate_forecast_rows",
    "evaluate_terminal_register",
    "instrument_balanced_mse",
    "mse_by_instrument",
]
