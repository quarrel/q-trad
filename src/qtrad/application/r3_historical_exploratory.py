"""Outcome-blind R3.H historical exploratory freeze and fixture runner.

This module contains no provider/evidence authentication or outcome decoder.  It freezes a
bounded, deterministic analysis contract that can later consume authenticated R2 children through
the ordinary immediate-parent boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import resource
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

CONTRACT: Final = "qtrad-r3-historical-exploratory-freeze-v1"
REPORT_CONTRACT: Final = "qtrad-r3-historical-exploratory-report-v1"
AUTHENTICATION_COMMAND: Final = (
    "qtrad research observations authenticate-provider-history "
    "--manifest <stage7-v3-manifest> --receipt <stage7-v3-receipt>"
)

_REVIEWED_SEMANTIC_IDENTITY: Final = (
    "482e941019fbeffc9396bff5b8a947bcef9b8fba71e30ef854722a96cab5c4d8"
)
_NON_EXECUTABLE_CLAIMS: Final = (
    "midpoint_only",
    "historical_exploratory",
    "implementation_evidence_only",
    "not_executable_evidence",
    "no_effectiveness_claim",
)
_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "contract",
        "schema_version",
        "stage",
        "evidence_class",
        "source_class",
        "price_basis",
        "primary_horizon_minutes",
        "targets",
        "groups",
        "retained_parents",
        "cost_grid",
        "turnover_definition",
        "statistical_formulations",
        "nonlinear_candidates",
        "tiny_graph_candidate",
        "graph_controls",
        "compute_limits",
        "output_contract",
        "semantic_identity",
    }
)
_CANDIDATE_IDS: Final = ("linear_ridge", "linear_zero_return", "nonlinear_huber")
_GRAPH_CONTROL_IDS: Final = ("local_non_graph", "pooled_non_graph", "fixed_graph", "shuffled_graph")
_CANDIDATE_KEYS: Final = frozenset({"id", "family", "degree", "enabled"})
_GRAPH_KEYS: Final = frozenset({"id", "kind", "enabled"})
_TINY_GRAPH_KEYS: Final = frozenset({"id", "family", "layers", "hidden_units", "enabled"})
_COST_KEYS: Final = frozenset({"name", "value", "unit"})
_LIMIT_KEYS: Final = frozenset(
    {"max_rows", "max_fits", "max_candidates", "max_elapsed_seconds", "max_memory_mb"}
)
_RETAINED_KEYS: Final = frozenset(
    {
        "stage7_manifest",
        "stage7_receipt",
        "stage7_semantic_id",
        "stage7_dataset_id",
        "stage7_closure_id",
        "stage7_verification_id",
        "terminal_report_sha256",
        "terminal_approval_sha256",
        "stage8_promotion_id",
    }
)
_RETAINED_IDENTITY_KEYS: Final = (
    "stage7_semantic_id",
    "stage7_dataset_id",
    "stage7_closure_id",
    "stage7_verification_id",
    "terminal_report_sha256",
    "terminal_approval_sha256",
    "stage8_promotion_id",
)
_STATISTICAL_KEYS: Final = frozenset({"oof", "controls", "metrics", "views"})
_OUTPUT_KEYS: Final = frozenset({"report_contract", "create_only", "post_result_expansion"})


class FreezeError(ValueError):
    """Raised when a proposed configuration is not the frozen bounded experiment."""


@dataclass(frozen=True, slots=True)
class FixtureRow:
    """Minimal causal row used by the implementation-only micro-run."""

    timestamp: str
    asset: str
    horizon_minutes: int
    period: str
    prediction: float
    realised_return: float
    target_position_change: float
    available_at: str

    def __post_init__(self) -> None:
        if self.horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")
        if self.available_at > self.timestamp:
            raise ValueError("availability cannot follow decision timestamp")
        if not all(
            math.isfinite(value)
            for value in (self.prediction, self.realised_return, self.target_position_change)
        ):
            raise ValueError("fixture numeric values must be finite")


@dataclass(frozen=True, slots=True)
class FreezeConfig:
    """Immutable, outcome-blind R3.H Stage 1 configuration."""

    document: Mapping[str, Any]
    semantic_identity: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> FreezeConfig:
        _reject_unknown(raw, _TOP_LEVEL_KEYS, "configuration")
        missing = sorted((_TOP_LEVEL_KEYS - {"semantic_identity"}) - set(raw))
        if missing:
            raise FreezeError(f"configuration missing required keys: {', '.join(missing)}")
        if raw["contract"] != CONTRACT or raw["schema_version"] != 1:
            raise FreezeError("unsupported freeze contract")
        if raw["stage"] != "R3.H" or raw["evidence_class"] != "HISTORICAL_EXPLORATORY":
            raise FreezeError("configuration is not an R3.H historical exploratory freeze")
        if raw["price_basis"] != "MIDPOINT_OHLC":
            raise FreezeError("R3.H freeze must label MIDPOINT_OHLC")
        if raw["primary_horizon_minutes"] != 15:
            raise FreezeError("R3.H primary horizon is fixed at 15 minutes")
        _validate_nested_sections(raw)
        _validate_candidates(raw["nonlinear_candidates"])
        _validate_tiny_graph(raw["tiny_graph_candidate"])
        _validate_graph_controls(raw["graph_controls"])
        _validate_cost_grid(raw["cost_grid"])
        _validate_limits(raw["compute_limits"])
        if raw["turnover_definition"] != (
            "physical_turnover=sum(abs(target_position-change)); "
            "one unit is one notional unit traded"
        ):
            raise FreezeError("turnover definition is not the frozen physical definition")
        if raw["output_contract"] != {
            "report_contract": REPORT_CONTRACT,
            "create_only": True,
            "post_result_expansion": False,
        }:
            raise FreezeError("output contract permits an unbounded or mutable result")
        computed = _semantic_hash(raw)
        supplied = raw.get("semantic_identity")
        if computed != _REVIEWED_SEMANTIC_IDENTITY:
            raise FreezeError("configuration does not match reviewed frozen semantic identity")
        if not isinstance(supplied, str) or supplied != _REVIEWED_SEMANTIC_IDENTITY:
            raise FreezeError("semantic_identity does not match reviewed frozen identity")
        frozen = _freeze_value(json.loads(json.dumps(raw, sort_keys=True, separators=(",", ":"))))
        if not isinstance(frozen, Mapping):
            raise FreezeError("configuration could not be frozen")
        return cls(cast(Mapping[str, Any], frozen), computed)

    @classmethod
    def from_path(cls, path: str | Path) -> FreezeConfig:
        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise FreezeError("configuration root must be an object")
        return cls.from_mapping(cast(dict[str, Any], raw))

    def canonical_json(self) -> str:
        return json.dumps(_thaw_value(self.document), sort_keys=True, separators=(",", ":")) + "\n"


@dataclass(frozen=True, slots=True)
class FixtureMeasurement:
    """Deterministic fixture-run resource sample; tests may inject this seam."""

    elapsed_seconds: float
    memory_mb: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be finite and non-negative")
        if not math.isfinite(self.memory_mb) or self.memory_mb < 0:
            raise ValueError("memory_mb must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class MicroRun:
    """Deterministic report generated without retained-data access."""

    report: Mapping[str, Any]
    work_count: Mapping[str, int]

    def canonical_json(self) -> str:
        return json.dumps(self.report, sort_keys=True, indent=2) + "\n"


def _reject_unknown(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise FreezeError(f"{label} has unknown keys: {', '.join(unknown)}")


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        object_value = cast(dict[str, Any], value)
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in object_value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in cast(list[Any], value))
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        object_value = cast(Mapping[str, Any], value)
        return {str(key): _thaw_value(item) for key, item in object_value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in cast(tuple[Any, ...], value)]
    return value


def _validate_nested_sections(raw: Mapping[str, Any]) -> None:
    retained = raw["retained_parents"]
    if not isinstance(retained, dict):
        raise FreezeError("retained_parents must be an object")
    retained_object = cast(dict[str, Any], retained)
    _reject_unknown(retained_object, _RETAINED_KEYS, "retained_parents")
    if set(retained_object) != set(_RETAINED_KEYS) or any(
        not isinstance(value, str) for value in retained_object.values()
    ):
        raise FreezeError("retained_parents must identify every named immutable parent")
    statistical = raw["statistical_formulations"]
    if not isinstance(statistical, dict):
        raise FreezeError("statistical_formulations must be an object")
    statistical_object = cast(dict[str, Any], statistical)
    _reject_unknown(statistical_object, _STATISTICAL_KEYS, "statistical_formulations")
    if set(statistical_object) != set(_STATISTICAL_KEYS):
        raise FreezeError("statistical formulations are incomplete")
    output = raw["output_contract"]
    if not isinstance(output, dict):
        raise FreezeError("output_contract must be an object")
    _reject_unknown(cast(dict[str, Any], output), _OUTPUT_KEYS, "output_contract")


def _validate_candidates(value: Any) -> None:
    if not isinstance(value, list):
        raise FreezeError("candidate expansion is forbidden")
    candidates = cast(list[Any], value)
    if len(candidates) != len(_CANDIDATE_IDS):
        raise FreezeError("candidate expansion is forbidden")
    ids: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            raise FreezeError("candidate must be an object")
        candidate = cast(dict[str, Any], item)
        _reject_unknown(candidate, _CANDIDATE_KEYS, "candidate")
        if set(candidate) != set(_CANDIDATE_KEYS) or candidate["enabled"] is not True:
            raise FreezeError("candidates are frozen and enabled")
        if not isinstance(candidate["id"], str) or not isinstance(candidate["degree"], int):
            raise FreezeError("candidate fields have invalid types")
        ids.append(candidate["id"])
    if tuple(ids) != _CANDIDATE_IDS:
        raise FreezeError("candidate ids differ from the frozen set")


def _validate_tiny_graph(value: Any) -> None:
    if not isinstance(value, dict):
        raise FreezeError("tiny_graph_candidate must be an object")
    tiny_graph = cast(dict[str, Any], value)
    _reject_unknown(tiny_graph, _TINY_GRAPH_KEYS, "tiny_graph_candidate")
    if set(tiny_graph) != set(_TINY_GRAPH_KEYS) or tiny_graph != {
        "id": "tiny_learned_graph",
        "family": "gnn",
        "layers": 1,
        "hidden_units": 4,
        "enabled": True,
    }:
        raise FreezeError("exactly one tiny learned graph configuration is required")


def _validate_graph_controls(value: Any) -> None:
    if not isinstance(value, list):
        raise FreezeError("graph controls must contain exactly four controls")
    controls = cast(list[Any], value)
    if len(controls) != len(_GRAPH_CONTROL_IDS):
        raise FreezeError("graph controls must contain exactly four controls")
    ids: list[str] = []
    for item in controls:
        if not isinstance(item, dict):
            raise FreezeError("graph control must be an object")
        control = cast(dict[str, Any], item)
        _reject_unknown(control, _GRAPH_KEYS, "graph control")
        if set(control) != set(_GRAPH_KEYS) or control["enabled"] is not True:
            raise FreezeError("all graph controls are required")
        ids.append(cast(str, control["id"]))
    if tuple(ids) != _GRAPH_CONTROL_IDS:
        raise FreezeError("graph controls differ from the frozen set")


def _validate_cost_grid(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise FreezeError("cost grid must not be empty")
    for item in cast(list[Any], value):
        if not isinstance(item, dict):
            raise FreezeError("cost point must be an object")
        point = cast(dict[str, Any], item)
        _reject_unknown(point, _COST_KEYS, "cost point")
        if set(point) != set(_COST_KEYS) or point["unit"] != "fraction_of_notional":
            raise FreezeError("cost points must use fraction_of_notional units")
        if not isinstance(point["value"], (int, float)) or point["value"] < 0:
            raise FreezeError("cost point must be non-negative")


def _validate_limits(value: Any) -> None:
    if not isinstance(value, dict):
        raise FreezeError("compute_limits must be an object")
    limits = cast(dict[str, Any], value)
    _reject_unknown(limits, _LIMIT_KEYS, "compute_limits")
    if set(limits) != set(_LIMIT_KEYS) or any(
        not isinstance(limits[key], int) or limits[key] <= 0 for key in _LIMIT_KEYS
    ):
        raise FreezeError("compute limits must be positive integers")


def _semantic_hash(raw: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in raw.items() if key != "semantic_identity"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def retained_input_paths() -> Mapping[str, str]:
    """Return exact operator-retained paths; this Stage 1 runner never opens them."""

    return {
        "stage7_manifest": (
            "/workspace/tmp/ibkr-historical-r2-20260810T081317Z/remediation/"
            "r2-simplification-h4-670e04e-attempt3/provider-history-v3/manifest.json"
        ),
        "stage7_receipt": (
            "/workspace/tmp/ibkr-historical-r2-20260810T081317Z/remediation/"
            "r2-simplification-h4-670e04e-attempt3/provider-history-v3-verification-receipt.json"
        ),
        "terminal_report": (
            "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/"
            "r2-scientific-report.md"
        ),
        "terminal_approval": (
            "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z-authority/"
            "r2-scientific-report-review.json"
        ),
    }


def _decision_identity(row: FixtureRow) -> tuple[str, str, int, str]:
    return (row.timestamp, row.asset, row.horizon_minutes, row.period)


def _rank_values(values: Sequence[float]) -> list[float]:
    ranks = [0.0] * len(values)
    for index, value in enumerate(values):
        lower = sum(other < value for other in values)
        equal = sum(other == value for other in values)
        ranks[index] = lower + (equal + 1) / 2
    return ranks


def _metrics(
    rows: Sequence[FixtureRow],
    predictions: Sequence[float],
    *,
    baseline_mse: float | None,
    minimum_support: int = 1,
) -> dict[str, Any]:
    if len(rows) != len(predictions) or not rows:
        raise FreezeError("fixture control produced no aligned predictions")
    if any(
        not math.isfinite(prediction) or not math.isfinite(row.realised_return)
        for row, prediction in zip(rows, predictions, strict=True)
    ):
        raise FreezeError("fixture control produced non-finite values")
    realised = [row.realised_return for row in rows]
    errors = [prediction - actual for prediction, actual in zip(predictions, realised, strict=True)]
    mse = sum(error * error for error in errors) / len(errors)
    prediction_ranks = _rank_values(predictions)
    realised_ranks = _rank_values(realised)
    mean_prediction_rank = sum(prediction_ranks) / len(prediction_ranks)
    mean_realised_rank = sum(realised_ranks) / len(realised_ranks)
    covariance = sum(
        (left - mean_prediction_rank) * (right - mean_realised_rank)
        for left, right in zip(prediction_ranks, realised_ranks, strict=True)
    )
    prediction_scale = sum((rank - mean_prediction_rank) ** 2 for rank in prediction_ranks)
    realised_scale = sum((rank - mean_realised_rank) ** 2 for rank in realised_ranks)
    rank_correlation = (
        covariance / math.sqrt(prediction_scale * realised_scale)
        if prediction_scale and realised_scale
        else None
    )
    status = "FAILED" if len(rows) < minimum_support else "INCONCLUSIVE"
    if baseline_mse is not None and mse > baseline_mse + 1e-15 and status != "FAILED":
        status = "NEGATIVE"
    return {
        "status": status,
        "mse": round(mse, 12),
        "rank_correlation": round(rank_correlation, 12) if rank_correlation is not None else None,
        "coverage": len(rows) / len(rows),
        "support": len(rows),
        "prediction_trace": [round(prediction, 12) for prediction in predictions],
    }


def _chronological_oof(rows: Sequence[FixtureRow]) -> tuple[dict[str, Any], int, list[FixtureRow]]:
    ordered = sorted(rows, key=_decision_identity)
    seen: set[tuple[str, str, int, str]] = set()
    errors: list[float] = []
    for row in ordered:
        identity = _decision_identity(row)
        if row.available_at > row.timestamp:
            raise FreezeError("OOF row uses a value unavailable at its decision time")
        if identity in seen:
            raise FreezeError("duplicate decision identity would make chronology ambiguous")
        seen.add(identity)
        errors.append(row.prediction - row.realised_return)
    if not errors:
        raise FreezeError("fixture must contain at least one chronological row")
    mse = sum(error * error for error in errors) / len(errors)
    return (
        {
            "formulation": "chronological_oof_mean_squared_error",
            "ordering": "timestamp,asset,horizon_minutes,period",
            "decision_identity": "timestamp|asset|horizon_minutes|period",
            "rows": len(ordered),
            "first_timestamp": ordered[0].timestamp,
            "last_timestamp": ordered[-1].timestamp,
            "mse": round(mse, 12),
            "causal": True,
            "purge_embargo": {
                "applied": False,
                "rows_excluded": 0,
                "reason": "fixture carries no dependency interval; availability was checked",
            },
        },
        len(ordered),
        ordered,
    )


def _economic_views(rows: Sequence[FixtureRow], config: FreezeConfig) -> dict[str, Any]:
    turnover = sum(abs(row.target_position_change) for row in rows)
    gross = sum(row.prediction * row.realised_return for row in rows) / len(rows)
    periods: dict[str, list[FixtureRow]] = defaultdict(list)
    assets: dict[str, list[FixtureRow]] = defaultdict(list)
    horizons: dict[str, list[FixtureRow]] = defaultdict(list)
    for row in rows:
        periods[row.period].append(row)
        assets[row.asset].append(row)
        horizons[str(row.horizon_minutes)].append(row)

    def view(groups: Mapping[str, Sequence[FixtureRow]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, group in sorted(groups.items()):
            group_turnover = sum(abs(item.target_position_change) for item in group)
            group_gross = sum(item.prediction * item.realised_return for item in group) / len(group)
            values[name] = {
                "gross_mean": round(group_gross, 12),
                "turnover": round(group_turnover, 12),
                "break_even_cost": round(group_gross * len(group) / group_turnover, 12)
                if group_turnover
                else None,
            }
        return values

    sensitivities = [
        {
            "cost": float(point["value"]),
            "unit": point["unit"],
            "net_mean": round(gross - float(point["value"]) * turnover / len(rows), 12),
            "break_even_cost": round(gross * len(rows) / turnover, 12) if turnover else None,
            "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
        }
        for point in config.document["cost_grid"]
    ]
    return {
        "physical_turnover_definition": config.document["turnover_definition"],
        "all_in_cost_sensitivity": sensitivities,
        "asset": view(assets),
        "horizon": view(horizons),
        "period": view(periods),
    }


def _runtime_measurement(started: float) -> FixtureMeasurement:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return FixtureMeasurement(time.monotonic() - started, usage / divisor)


def _check_hard_limits(limits: Mapping[str, Any], measurement: FixtureMeasurement) -> None:
    if measurement.elapsed_seconds > limits["max_elapsed_seconds"]:
        raise FreezeError("fixture analysis exceeded max_elapsed_seconds")
    if measurement.memory_mb > limits["max_memory_mb"]:
        raise FreezeError("fixture analysis exceeded max_memory_mb")


def _timestamp_groups(rows: Sequence[FixtureRow]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[row.timestamp].append(index)
    return dict(groups)


def _pooled_predictions(rows: Sequence[FixtureRow], values: Sequence[float]) -> list[float]:
    if len(rows) != len(values):
        raise FreezeError("pooled control received unaligned values")
    groups = _timestamp_groups(rows)
    predictions = [0.0] * len(rows)
    for indexes in groups.values():
        mean_value = sum(values[index] for index in indexes) / len(indexes)
        for index in indexes:
            predictions[index] = mean_value
    return predictions


def _fixed_graph_neighbors(rows: Sequence[FixtureRow], values: Sequence[float]) -> list[float]:
    groups = _timestamp_groups(rows)
    neighbours = [0.0] * len(rows)
    for indexes in groups.values():
        for index in indexes:
            others = [other for other in indexes if other != index]
            neighbours[index] = (
                sum(values[other] for other in others) / len(others) if others else values[index]
            )
    return neighbours


def _shuffled_graph_predictions(rows: Sequence[FixtureRow], values: Sequence[float]) -> list[float]:
    groups = _timestamp_groups(rows)
    predictions = [0.0] * len(rows)
    for indexes in groups.values():
        shuffled = list(reversed(indexes))
        for position, index in enumerate(indexes):
            predictions[index] = values[shuffled[position]]
    return predictions


def _fit_tiny_graph_weights(
    rows: Sequence[FixtureRow],
    values: Sequence[float],
    neighbours: Sequence[float],
    training_indices: Sequence[int],
) -> tuple[float, float, float, float]:
    totals = [0.0] * 4
    squares = [0.0] * 4
    for index in training_indices:
        features = (
            values[index],
            neighbours[index],
            values[index] * neighbours[index],
            1.0,
        )
        target = rows[index].realised_return
        for feature_index, feature in enumerate(features):
            totals[feature_index] += feature * target
            squares[feature_index] += feature * feature
    return cast(
        tuple[float, float, float, float],
        tuple(
            total / square if square else 0.0 for total, square in zip(totals, squares, strict=True)
        ),
    )


def _graph_predictions(
    rows: Sequence[FixtureRow],
    tiny_graph: Mapping[str, Any],
) -> tuple[dict[str, list[float]], int, int]:
    if tiny_graph["layers"] != 1 or tiny_graph["hidden_units"] != 4:
        raise FreezeError(
            "tiny graph configuration is outside the frozen one-layer four-unit bound"
        )
    values = [row.prediction for row in rows]
    pooled = _pooled_predictions(rows, values)
    neighbours = _fixed_graph_neighbors(rows, values)
    shuffled = _shuffled_graph_predictions(rows, values)
    groups = _timestamp_groups(rows)
    timestamps = sorted(groups)
    learned = [0.0] * len(rows)
    fit_executions = 0
    if len(timestamps) > 1:
        weights = _fit_tiny_graph_weights(rows, values, neighbours, groups[timestamps[0]])
        fit_executions = 1
        for index in (item for timestamp in timestamps[1:] for item in groups[timestamp]):
            features = (
                values[index],
                neighbours[index],
                values[index] * neighbours[index],
                1.0,
            )
            learned[index] = max(
                -0.05,
                min(
                    0.05,
                    sum(weight * feature for weight, feature in zip(weights, features, strict=True))
                    / 4.0,
                ),
            )
    return (
        {
            "local_non_graph": values,
            "pooled_non_graph": pooled,
            "fixed_graph": neighbours,
            "shuffled_graph": shuffled,
            "tiny_learned_graph": learned,
        },
        int(bool(fit_executions)),
        fit_executions,
    )


def _code_provenance() -> dict[str, str]:
    try:
        source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError as exc:
        raise FreezeError("cannot establish application code identity") from exc
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    return {
        "application_contract": "qtrad-r3-historical-exploratory-implementation-v2",
        "module_sha256": source_sha256,
        "python_version": python_version,
    }


def analyse_fixture(
    rows: Sequence[FixtureRow],
    config: FreezeConfig,
    *,
    measurement: FixtureMeasurement | None = None,
) -> MicroRun:
    """Run all three bounded components against synthetic/fixture rows only."""
    started = time.monotonic()
    limits = cast(Mapping[str, Any], config.document["compute_limits"])
    if len(rows) > limits["max_rows"]:
        raise FreezeError("fixture exceeds max_rows")
    oof, row_count, ordered = _chronological_oof(rows)
    economic = _economic_views(ordered, config)
    candidate_ids = list(_CANDIDATE_IDS)
    zero_predictions = [0.0] * row_count
    local_predictions = [row.prediction for row in ordered]
    pooled_predictions = _pooled_predictions(ordered, local_predictions)
    candidate_predictions = {
        "linear_ridge": local_predictions,
        "linear_zero_return": zero_predictions,
        "nonlinear_huber": [max(-0.05, min(0.05, row.prediction)) for row in ordered],
    }
    zero_metrics = _metrics(ordered, zero_predictions, baseline_mse=None)
    baseline_mse = float(zero_metrics["mse"])
    candidate_metrics = {
        candidate_id: _metrics(
            ordered,
            candidate_predictions[candidate_id],
            baseline_mse=baseline_mse if candidate_id != "linear_zero_return" else None,
            minimum_support=5 if candidate_id == "nonlinear_huber" else 1,
        )
        for candidate_id in candidate_ids
    }
    tiny_graph = cast(Mapping[str, Any], config.document["tiny_graph_candidate"])
    if not tiny_graph["enabled"] or len(candidate_ids) > limits["max_candidates"]:
        raise FreezeError("fixture analysis exceeded frozen candidate limits")
    control_predictions = {
        "zero_return": zero_predictions,
        "local_ridge": local_predictions,
        "pooled_local_ridge": pooled_predictions,
    }
    control_metrics = {
        control_id: _metrics(
            ordered,
            predictions,
            baseline_mse=baseline_mse if control_id != "zero_return" else None,
        )
        for control_id, predictions in control_predictions.items()
    }
    oof_metrics = control_metrics["local_ridge"]
    statistical = {
        "oof": {
            **oof,
            "rank_correlation": oof_metrics["rank_correlation"],
            "coverage": oof_metrics["coverage"],
            "support": oof_metrics["support"],
        },
        "simple_controls": [
            {"id": control_id, **control_metrics[control_id]}
            for control_id in ("zero_return", "local_ridge", "pooled_local_ridge")
        ],
        "negative_failed_inconclusive_rendered": {
            metric["status"] for metric in (*candidate_metrics.values(), *control_metrics.values())
        }
        >= {"NEGATIVE", "FAILED", "INCONCLUSIVE"},
        "post_result_selection": False,
    }
    graph_predictions, graph_fit_count, graph_fit_executions = _graph_predictions(
        ordered, tiny_graph
    )
    fits = len(candidate_metrics) + graph_fit_count
    if fits > limits["max_fits"]:
        raise FreezeError("fixture analysis exceeded frozen fit limits")
    graph_metrics = {
        control_id: _metrics(
            ordered,
            predictions,
            baseline_mse=baseline_mse,
        )
        for control_id, predictions in graph_predictions.items()
    }
    graph = {
        "tiny_learned_graph": {
            "id": "tiny_learned_graph",
            **graph_metrics["tiny_learned_graph"],
            "layers": tiny_graph["layers"],
            "hidden_units": tiny_graph["hidden_units"],
            "feasibility_only": True,
            "fits": graph_fit_count,
            "walk_forward_fit_executions": graph_fit_executions,
        },
        "controls": [
            {
                "id": control_id,
                **graph_metrics[control_id],
                "feasibility_only": True,
            }
            for control_id in _GRAPH_CONTROL_IDS
        ],
        "r4_replacement_required": True,
    }
    measured = measurement or _runtime_measurement(started)
    _check_hard_limits(limits, measured)
    statuses = {
        candidate_id: metric["status"] for candidate_id, metric in candidate_metrics.items()
    }
    graph_statuses = {control_id: metric["status"] for control_id, metric in graph_metrics.items()}
    retained = cast(Mapping[str, Any], config.document["retained_parents"])
    parent_identities = {key: retained[key] for key in _RETAINED_IDENTITY_KEYS}
    report: dict[str, Any] = {
        "contract": REPORT_CONTRACT,
        "schema_version": 1,
        "config_semantic_identity": config.semantic_identity,
        "source_class": config.document["source_class"],
        "price_basis": config.document["price_basis"],
        "evidence_class": "HISTORICAL_EXPLORATORY_IMPLEMENTATION_EVIDENCE",
        "claims": list(_NON_EXECUTABLE_CLAIMS),
        "code_provenance": _code_provenance(),
        "retained_parents": {
            "paths": dict(retained_input_paths()),
            "identities": parent_identities,
            "authentication_command": AUTHENTICATION_COMMAND,
            "authentication_performed": False,
            "outcome_decode_performed": False,
        },
        "economic": economic,
        "statistical": statistical,
        "graph": graph,
        "work": {
            "rows": row_count,
            "candidate_count": len(candidate_ids),
            "fit_count": fits,
            "graph_fit_count": graph_fit_count,
            "graph_control_count": len(graph_metrics) - graph_fit_count,
            "within_hard_limits": True,
            "limits": dict(limits),
            "measurement": {
                "elapsed_seconds": round(measured.elapsed_seconds, 9),
                "memory_mb": round(measured.memory_mb, 6),
            },
        },
        "result_classification": {
            "negative": [
                key
                for key, status in {**statuses, **graph_statuses}.items()
                if status == "NEGATIVE"
            ],
            "failed": [
                key for key, status in {**statuses, **graph_statuses}.items() if status == "FAILED"
            ],
            "inconclusive": [
                key
                for key, status in {**statuses, **graph_statuses}.items()
                if status == "INCONCLUSIVE"
            ],
        },
        "create_only_destination": "operator-selected future R3.H report path",
        "no_post_result_expansion": True,
    }
    return MicroRun(
        report,
        {"rows": row_count, "fits": fits, "candidates": len(candidate_ids)},
    )


def synthetic_fixture() -> tuple[FixtureRow, ...]:
    """Small deterministic fixture preserving all component boundary shapes."""

    return tuple(
        FixtureRow(
            timestamp=f"2026-01-01T00:0{index}:00Z",
            asset=asset,
            horizon_minutes=15,
            period="fixture-period",
            prediction=prediction,
            realised_return=realised,
            target_position_change=position_change,
            available_at=f"2025-12-31T23:5{index}:00Z",
        )
        for index, (asset, prediction, realised, position_change) in enumerate(
            (
                ("A", 0.02, 0.01, 0.20),
                ("B", -0.01, 0.00, -0.10),
                ("A", 0.01, -0.01, 0.05),
                ("B", 0.00, 0.01, -0.15),
            ),
            start=1,
        )
    )


def fixture_from_json(path: str | Path) -> tuple[FixtureRow, ...]:
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise FreezeError("fixture root must be a list")
    return tuple(FixtureRow(**cast(dict[str, Any], row)) for row in cast(list[Any], raw))
