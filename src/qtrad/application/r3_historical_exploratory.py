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
STAGE8_AUTHENTICATION_COMMAND: Final = (
    "qtrad research baselines holdout-target-source "
    "--foundation-bundle <stage8> --foundation-receipt <stage8-receipt> "
    "--foundation-promotion <stage8-promotion> --experiment <experiment> --output <new-json>"
)
_REVIEWED_SEMANTIC_IDENTITY: Final = (
    "bf5661eb4881e63f33d8c86d109e27d39f05a535c0b50e63228c10799ff9aeee"
)
_NON_EXECUTABLE_CLAIMS: Final = (
    "midpoint_only",
    "historical_exploratory",
    "implementation_evidence_only",
    "not_executable_evidence",
    "no_effectiveness_claim",
)
_TARGET_IDS: Final = (
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
    "commodity:spot-gold",
    "commodity:us-crude",
)
_GROUP_IDS: Final = ("FX", "indices", "commodities")
_TARGET_GROUP_MAP: Final = {
    "fx:aud-usd": "FX",
    "fx:eur-usd": "FX",
    "index:australia-200": "indices",
    "index:us-500": "indices",
    "commodity:spot-gold": "commodities",
    "commodity:us-crude": "commodities",
}
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
        "target_group_resolution",
        "retained_parents",
        "authentication_chain",
        "temporal_contract",
        "retained_loader",
        "scale_projection",
        "observation_contract",
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
        "stage8_foundation",
        "stage8_receipt",
        "stage8_promotion",
        "stage8_foundation_id",
        "stage8_closure_id",
        "stage8_verification_id",
        "stage8_manifest_sha256",
        "stage8_verification_receipt_sha256",
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
    "stage8_foundation_id",
    "stage8_closure_id",
    "stage8_verification_id",
    "stage8_manifest_sha256",
    "stage8_verification_receipt_sha256",
    "terminal_report_sha256",
    "terminal_approval_sha256",
    "stage8_promotion_id",
)
_SECTION_KEYS: Final = {
    "target_group_resolution": frozenset(
        {"metadata_source", "target_ids", "group_ids", "mapping", "expected_identity"}
    ),
    "authentication_chain": frozenset({"stage7", "stage8", "terminal"}),
    "auth_stage": frozenset({"command", "receipt_required", "promotion_required"}),
    "temporal_contract": frozenset(
        {
            "decision_time_field",
            "feature_availability_field",
            "target_maturity_field",
            "dependency_interval_fields",
            "purge_rule",
            "embargo_rule",
            "fold_rule",
        }
    ),
    "retained_loader": frozenset(
        {
            "manifest_contract",
            "required_children",
            "required_columns",
            "identity_bindings",
            "decode_policy",
        }
    ),
    "scale_projection": frozenset(
        {
            "retained_row_count",
            "target_count",
            "group_count",
            "fixture_row_count",
            "projected_peak_memory_mb",
            "projected_elapsed_seconds",
            "stop_conditions",
        }
    ),
    "observation_contract": frozenset(
        {"event_aware", "durable_output", "resource_limits", "stop_conditions"}
    ),
}
_STATISTICAL_KEYS: Final = frozenset({"oof", "controls", "metrics", "views"})
_OUTPUT_KEYS: Final = frozenset({"report_contract", "create_only", "post_result_expansion"})


class FreezeError(ValueError):
    """Raised when a proposed configuration is not the frozen bounded experiment."""


@dataclass(frozen=True, slots=True)
class FixtureRow:
    """Causal row used by the outcome-blind implementation-only micro-run."""

    timestamp: str
    decision_time: str
    target_id: str
    asset: str
    group: str
    horizon_minutes: int
    period: str
    prediction: float
    realised_return: float
    available_at: str
    target_available_at: str
    dependency_start: str
    dependency_end: str
    feature_value: float
    # Retained for input compatibility only; economic code always derives the delta.
    target_position_change: float | None = None

    def __post_init__(self) -> None:
        if self.horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")
        if self.timestamp != self.decision_time:
            raise ValueError("timestamp and decision_time must match")
        if not self.target_id or not self.asset or not self.group:
            raise ValueError("fixture identity fields must be non-empty")
        if self.available_at > self.decision_time:
            raise ValueError("feature availability cannot follow decision time")
        if not self.target_available_at or not self.dependency_start or not self.dependency_end:
            raise ValueError("causal maturity and dependency interval fields are required")
        if self.dependency_start > self.dependency_end:
            raise ValueError("dependency interval start must precede its end")
        numeric_values = (
            self.prediction,
            self.realised_return,
            self.feature_value,
        )
        if self.target_position_change is not None:
            numeric_values += (self.target_position_change,)
        if not all(math.isfinite(value) for value in numeric_values):
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
            "physical_turnover=sum(abs(target_position_change)); "
            "target_position=prediction; change=target_position-prior_target_position; "
            "initial prior=0; one unit is one notional unit traded"
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
        return json.dumps(_thaw_value(self.report), sort_keys=True, indent=2) + "\n"


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

    targets = raw["targets"]
    groups = raw["groups"]
    if targets != list(_TARGET_IDS) or groups != list(_GROUP_IDS):
        raise FreezeError("targets/groups differ from retained target metadata")
    resolution = raw["target_group_resolution"]
    if not isinstance(resolution, dict):
        raise FreezeError("target_group_resolution must be an object")
    resolution_object = cast(dict[str, Any], resolution)
    _reject_unknown(
        resolution_object, _SECTION_KEYS["target_group_resolution"], "target_group_resolution"
    )
    if (
        resolution_object["target_ids"] != list(_TARGET_IDS)
        or resolution_object["group_ids"] != list(_GROUP_IDS)
        or resolution_object["mapping"] != _TARGET_GROUP_MAP
        or not isinstance(resolution_object["expected_identity"], str)
    ):
        raise FreezeError("target/group metadata resolution is not frozen")

    auth_chain = raw["authentication_chain"]
    if not isinstance(auth_chain, dict):
        raise FreezeError("authentication_chain must be an object")
    auth_object = cast(dict[str, Any], auth_chain)
    _reject_unknown(auth_object, _SECTION_KEYS["authentication_chain"], "authentication_chain")
    for name in ("stage7", "stage8", "terminal"):
        entry = auth_object[name]
        if not isinstance(entry, dict):
            raise FreezeError("authentication chain entry must be an object")
        entry_object = cast(dict[str, Any], entry)
        _reject_unknown(entry_object, _SECTION_KEYS["auth_stage"], f"authentication_chain.{name}")
        if set(entry_object) != set(_SECTION_KEYS["auth_stage"]):
            raise FreezeError("authentication chain entry is incomplete")

    for name in (
        "temporal_contract",
        "retained_loader",
        "scale_projection",
        "observation_contract",
    ):
        section = raw[name]
        if not isinstance(section, dict):
            raise FreezeError(f"{name} must be an object")
        section_object = cast(dict[str, Any], section)
        _reject_unknown(section_object, _SECTION_KEYS[name], name)
        if set(section_object) != set(_SECTION_KEYS[name]):
            raise FreezeError(f"{name} is incomplete")

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
        "stage8_foundation": (
            "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/foundation"
        ),
        "stage8_receipt": (
            "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/"
            "foundation-verification-receipt.json"
        ),
        "stage8_promotion": (
            "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/"
            "foundation-confirmatory-promotion.json"
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


def _decision_identity(row: FixtureRow) -> tuple[str, str, str, str, int, str]:
    return (
        row.decision_time,
        row.target_id,
        row.asset,
        row.group,
        row.horizon_minutes,
        row.period,
    )


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
    prediction_mask: Sequence[bool] | None = None,
) -> dict[str, Any]:
    if len(rows) != len(predictions) or not rows:
        raise FreezeError("fixture control produced no aligned predictions")
    mask = tuple(True for _ in rows) if prediction_mask is None else tuple(prediction_mask)
    if len(mask) != len(rows):
        raise FreezeError("fixture control prediction mask is not aligned")
    if any(not math.isfinite(prediction) for prediction in predictions):
        raise FreezeError("fixture control produced non-finite predictions")
    selected_indices = tuple(index for index, included in enumerate(mask) if included)
    prediction_trace = [
        round(prediction, 12) if mask[index] else None
        for index, prediction in enumerate(predictions)
    ]
    if not selected_indices:
        return {
            "status": "FAILED",
            "mse": None,
            "rank_correlation": None,
            "coverage": 0.0,
            "support": 0,
            "prediction_trace": prediction_trace,
            "prediction_mask": list(mask),
        }
    selected_rows = [rows[index] for index in selected_indices]
    selected_predictions = [predictions[index] for index in selected_indices]
    if any(not math.isfinite(row.realised_return) for row in selected_rows):
        raise FreezeError("fixture control produced non-finite realised returns")
    realised = [row.realised_return for row in selected_rows]
    errors = [
        prediction - actual
        for prediction, actual in zip(selected_predictions, realised, strict=True)
    ]
    mse = sum(error * error for error in errors) / len(errors)
    prediction_ranks = _rank_values(selected_predictions)
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
    status = "FAILED" if len(selected_rows) < minimum_support else "INCONCLUSIVE"
    if baseline_mse is not None and mse > baseline_mse + 1e-15 and status != "FAILED":
        status = "NEGATIVE"
    return {
        "status": status,
        "mse": round(mse, 12),
        "rank_correlation": round(rank_correlation, 12) if rank_correlation is not None else None,
        "coverage": len(selected_rows) / len(rows),
        "support": len(selected_rows),
        "prediction_trace": prediction_trace,
        "prediction_mask": list(mask),
    }


def _causal_training_indices(rows: Sequence[FixtureRow], evaluation_time: str) -> list[int]:
    return [
        index
        for index, row in enumerate(rows)
        if row.decision_time < evaluation_time
        and row.target_available_at <= evaluation_time
        and row.dependency_end < evaluation_time
    ]


def _first_training_fold(rows: Sequence[FixtureRow]) -> tuple[list[int], str]:
    timestamps = sorted({row.decision_time for row in rows})
    for evaluation_time in timestamps[1:]:
        training = _causal_training_indices(rows, evaluation_time)
        if training:
            return training, evaluation_time
    raise FreezeError("fixture has no matured causal training fold")


def _evaluation_mask(rows: Sequence[FixtureRow], training_indices: Sequence[int]) -> list[bool]:
    if not training_indices:
        raise FreezeError("fixture has no causal fit rows")
    cutoff = max(rows[index].decision_time for index in training_indices)
    return [row.decision_time > cutoff for row in rows]


def _chronological_oof(rows: Sequence[FixtureRow]) -> tuple[dict[str, Any], int, list[FixtureRow]]:
    ordered = sorted(rows, key=_decision_identity)
    seen: set[tuple[str, str, str, str, int, str]] = set()
    errors: list[float] = []
    for row in ordered:
        identity = _decision_identity(row)
        if _TARGET_GROUP_MAP.get(row.target_id) != row.group:
            raise FreezeError("fixture target/group identity differs from retained metadata")
        if row.available_at > row.decision_time:
            raise FreezeError("OOF row uses a value unavailable at its decision time")
        if identity in seen:
            raise FreezeError("duplicate decision identity would make chronology ambiguous")
        seen.add(identity)
        errors.append(row.prediction - row.realised_return)
    if set(row.target_id for row in ordered) != set(_TARGET_IDS):
        raise FreezeError("fixture must exercise all six retained targets")
    if set(row.group for row in ordered) != set(_GROUP_IDS):
        raise FreezeError("fixture must exercise all three retained groups")
    if not errors:
        raise FreezeError("fixture must contain at least one chronological row")

    folds: list[dict[str, Any]] = []
    timestamps = sorted({row.decision_time for row in ordered})
    for evaluation_time in timestamps[1:]:
        prior = [index for index, row in enumerate(ordered) if row.decision_time < evaluation_time]
        training = _causal_training_indices(ordered, evaluation_time)
        purged = [index for index in prior if ordered[index].dependency_end >= evaluation_time]
        embargoed = [
            index for index in prior if ordered[index].target_available_at > evaluation_time
        ]
        evaluation_count = sum(row.decision_time == evaluation_time for row in ordered)
        if training and evaluation_count:
            folds.append(
                {
                    "evaluation_time": evaluation_time,
                    "training_rows": len(training),
                    "evaluation_rows": evaluation_count,
                    "purged_rows": len(purged),
                    "embargoed_rows": len(embargoed),
                }
            )
    if not folds:
        raise FreezeError("fixture has no chronological evaluation fold")

    mse = sum(error * error for error in errors) / len(errors)
    return (
        {
            "formulation": "chronological_oof_mean_squared_error",
            "ordering": "decision_time,target_id,asset,group,horizon_minutes,period",
            "decision_identity": "decision_time|target_id|asset|group|horizon_minutes|period",
            "rows": len(ordered),
            "first_timestamp": ordered[0].decision_time,
            "last_timestamp": ordered[-1].decision_time,
            "mse": round(mse, 12),
            "causal": True,
            "folds": folds,
            "purge_embargo": {
                "applied": True,
                "rows_excluded": sum(
                    fold["purged_rows"] + fold["embargoed_rows"] for fold in folds
                ),
                "reason": (
                    "dependency_end overlap and target maturity are checked "
                    "before every evaluation fold"
                ),
            },
        },
        len(ordered),
        ordered,
    )


def _position_trace(rows: Sequence[FixtureRow], predictions: Sequence[float]) -> dict[str, Any]:
    if len(rows) != len(predictions):
        raise FreezeError("economic prediction trace length mismatch")
    order = sorted(
        range(len(rows)),
        key=lambda index: (
            rows[index].decision_time,
            rows[index].period,
            rows[index].target_id,
            rows[index].asset,
            rows[index].horizon_minutes,
        ),
    )
    prior: dict[tuple[str, str, int], float] = {}
    positions = [0.0] * len(rows)
    changes = [0.0] * len(rows)
    gross_values = [0.0] * len(rows)
    for index in order:
        row = rows[index]
        position = float(predictions[index])
        if not math.isfinite(position):
            raise FreezeError("economic prediction must be finite")
        key = (row.target_id, row.asset, row.horizon_minutes)
        previous = prior.get(key, 0.0)
        positions[index] = position
        changes[index] = position - previous
        gross_values[index] = position * row.realised_return
        prior[key] = position
    return {
        "positions": positions,
        "changes": changes,
        "gross_values": gross_values,
    }


def _economic_views(
    rows: Sequence[FixtureRow],
    config: FreezeConfig,
    predictions: Sequence[float],
    *,
    trace_id: str,
) -> dict[str, Any]:
    trace = _position_trace(rows, predictions)
    positions = cast(list[float], trace["positions"])
    changes = cast(list[float], trace["changes"])
    gross_values = cast(list[float], trace["gross_values"])
    indices_by_group: dict[str, dict[str, list[int]]] = {
        "period": defaultdict(list),
        "asset": defaultdict(list),
        "horizon": defaultdict(list),
    }
    for index, row in enumerate(rows):
        indices_by_group["period"][row.period].append(index)
        indices_by_group["asset"][row.asset].append(index)
        indices_by_group["horizon"][str(row.horizon_minutes)].append(index)

    def view(indices: Sequence[int]) -> dict[str, Any]:
        gross_total = sum(gross_values[index] for index in indices)
        turnover = sum(abs(changes[index]) for index in indices)
        count = len(indices)
        gross_mean = gross_total / count if count else 0.0
        break_even_cost = gross_total / turnover if turnover else None
        sensitivities = [
            {
                "cost": float(point["value"]),
                "unit": point["unit"],
                "net_mean": (
                    round(gross_mean - float(point["value"]) * turnover / count, 12)
                    if count
                    else None
                ),
                "break_even_cost": (
                    round(break_even_cost, 12) if break_even_cost is not None else None
                ),
                "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
            }
            for point in config.document["cost_grid"]
        ]
        return {
            "gross_total": round(gross_total, 12),
            "gross_mean": round(gross_mean, 12),
            "turnover": round(turnover, 12),
            "break_even_cost": (
                round(break_even_cost, 12) if break_even_cost is not None else None
            ),
            "all_in_cost_sensitivity": sensitivities,
            "position_trace": [
                {
                    "target_id": rows[index].target_id,
                    "decision_time": rows[index].decision_time,
                    "target_position": round(positions[index], 12),
                    "target_position_change": round(changes[index], 12),
                    "realised_gross": round(gross_values[index], 12),
                }
                for index in indices
            ],
        }

    all_indices = tuple(range(len(rows)))
    all_view = view(all_indices)
    return {
        "trace_id": trace_id,
        "physical_turnover_definition": config.document["turnover_definition"],
        "asset": {
            name: view(indices) for name, indices in sorted(indices_by_group["asset"].items())
        },
        "horizon": {
            name: view(indices) for name, indices in sorted(indices_by_group["horizon"].items())
        },
        "period": {
            name: view(indices) for name, indices in sorted(indices_by_group["period"].items())
        },
        **all_view,
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
        groups[row.decision_time].append(index)
    return dict(groups)


def _fit_ridge(rows: Sequence[FixtureRow], training_indices: Sequence[int]) -> tuple[float, float]:
    features = [rows[index].feature_value for index in training_indices]
    targets = [rows[index].realised_return for index in training_indices]
    mean_feature = sum(features) / len(features)
    mean_target = sum(targets) / len(targets)
    denominator = sum((feature - mean_feature) ** 2 for feature in features)
    slope = (
        sum(
            (feature - mean_feature) * (target - mean_target)
            for feature, target in zip(features, targets, strict=True)
        )
        / denominator
        if denominator
        else 0.0
    )
    return mean_target - slope * mean_feature, slope


def _fit_huber(rows: Sequence[FixtureRow], training_indices: Sequence[int]) -> tuple[float, float]:
    intercept, slope = _fit_ridge(rows, training_indices)
    for _ in range(4):
        weighted_features: list[float] = []
        weighted_targets: list[float] = []
        weights: list[float] = []
        for index in training_indices:
            residual = rows[index].realised_return - (intercept + slope * rows[index].feature_value)
            weight = min(1.0, 0.02 / max(abs(residual), 0.02))
            weighted_features.append(rows[index].feature_value)
            weighted_targets.append(rows[index].realised_return)
            weights.append(weight)
        weight_total = sum(weights)
        mean_feature = (
            sum(
                feature * weight for feature, weight in zip(weighted_features, weights, strict=True)
            )
            / weight_total
        )
        mean_target = (
            sum(target * weight for target, weight in zip(weighted_targets, weights, strict=True))
            / weight_total
        )
        denominator = sum(
            weight * (feature - mean_feature) ** 2
            for feature, weight in zip(weighted_features, weights, strict=True)
        )
        slope = (
            sum(
                weight * (feature - mean_feature) * (target - mean_target)
                for feature, target, weight in zip(
                    weighted_features, weighted_targets, weights, strict=True
                )
            )
            / denominator
            if denominator
            else 0.0
        )
        intercept = mean_target - slope * mean_feature
    return intercept, slope


def _apply_causal_linear(
    rows: Sequence[FixtureRow],
    training_indices: Sequence[int],
    coefficients: tuple[float, float],
) -> list[float]:
    cutoff = max(rows[index].decision_time for index in training_indices)
    intercept, slope = coefficients
    return [
        0.0 if row.decision_time <= cutoff else intercept + slope * row.feature_value
        for row in rows
    ]


def _graph_neighbours(rows: Sequence[FixtureRow]) -> list[float]:
    groups = _timestamp_groups(rows)
    neighbours = [0.0] * len(rows)
    for indexes in groups.values():
        for index in indexes:
            others = [other for other in indexes if other != index]
            neighbours[index] = (
                sum(rows[other].feature_value for other in others) / len(others)
                if others
                else rows[index].feature_value
            )
    return neighbours


def _shuffled_graph_predictions(rows: Sequence[FixtureRow]) -> list[float]:
    groups = _timestamp_groups(rows)
    predictions = [0.0] * len(rows)
    for indexes in groups.values():
        shuffled = list(reversed(indexes))
        for position, index in enumerate(indexes):
            predictions[index] = rows[shuffled[position]].feature_value
    return predictions


def _fit_message_passing(
    rows: Sequence[FixtureRow],
    training_indices: Sequence[int],
    neighbours: Sequence[float],
) -> tuple[list[float], list[float], list[float], list[float], list[float], float]:
    hidden_local = [0.05 * (index + 1) for index in range(4)]
    hidden_neighbour = [-0.03 * (index + 1) for index in range(4)]
    hidden_bias = [0.0] * 4
    output_weights = [0.25] * 4
    output_bias = sum(rows[index].realised_return for index in training_indices) / len(
        training_indices
    )
    learning_rate = 0.05 / len(training_indices)
    for _ in range(8):
        gradients_local = [0.0] * 4
        gradients_neighbour = [0.0] * 4
        gradients_bias = [0.0] * 4
        gradients_output = [0.0] * 4
        gradients_output_bias = 0.0
        for index in training_indices:
            local = rows[index].feature_value
            neighbour = neighbours[index]
            hidden = [
                math.tanh(
                    hidden_local[unit] * local
                    + hidden_neighbour[unit] * neighbour
                    + hidden_bias[unit]
                )
                for unit in range(4)
            ]
            prediction = output_bias + sum(
                weight * value for weight, value in zip(output_weights, hidden, strict=True)
            )
            error = prediction - rows[index].realised_return
            gradients_output_bias += error
            for unit in range(4):
                derivative = error * output_weights[unit] * (1.0 - hidden[unit] ** 2)
                gradients_output[unit] += error * hidden[unit]
                gradients_local[unit] += derivative * local
                gradients_neighbour[unit] += derivative * neighbour
                gradients_bias[unit] += derivative
        output_bias -= learning_rate * gradients_output_bias
        for unit in range(4):
            output_weights[unit] -= learning_rate * gradients_output[unit]
            hidden_local[unit] -= learning_rate * gradients_local[unit]
            hidden_neighbour[unit] -= learning_rate * gradients_neighbour[unit]
            hidden_bias[unit] -= learning_rate * gradients_bias[unit]
    return (
        hidden_local,
        hidden_neighbour,
        hidden_bias,
        output_weights,
        [rows[index].feature_value for index in training_indices],
        output_bias,
    )


def _message_prediction(
    row: FixtureRow,
    neighbour: float,
    model: tuple[list[float], list[float], list[float], list[float], list[float], float],
) -> float:
    hidden_local, hidden_neighbour, hidden_bias, output_weights, _, output_bias = model
    hidden = [
        math.tanh(
            hidden_local[unit] * row.feature_value
            + hidden_neighbour[unit] * neighbour
            + hidden_bias[unit]
        )
        for unit in range(4)
    ]
    return output_bias + sum(
        weight * value for weight, value in zip(output_weights, hidden, strict=True)
    )


def _graph_predictions(
    rows: Sequence[FixtureRow],
    pooled_predictions: Sequence[float],
    training_indices: Sequence[int],
    tiny_graph: Mapping[str, Any],
) -> tuple[dict[str, list[float]], int, int]:
    if tiny_graph["layers"] != 1 or tiny_graph["hidden_units"] != 4:
        raise FreezeError(
            "tiny graph configuration is outside the frozen one-layer four-unit bound"
        )
    neighbours = _graph_neighbours(rows)
    shuffled = _shuffled_graph_predictions(rows)
    learned = [0.0] * len(rows)
    model = _fit_message_passing(rows, training_indices, neighbours)
    cutoff = max(rows[index].decision_time for index in training_indices)
    for index, row in enumerate(rows):
        if row.decision_time > cutoff:
            learned[index] = _message_prediction(row, neighbours[index], model)
    graph_fit_executions = 1 if training_indices else 0
    return (
        {
            "local_non_graph": [row.prediction for row in rows],
            "pooled_non_graph": list(pooled_predictions),
            "fixed_graph": neighbours,
            "shuffled_graph": shuffled,
            "tiny_learned_graph": learned,
        },
        graph_fit_executions,
        graph_fit_executions,
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
    """Run bounded causal models against synthetic/fixture rows only."""
    started = time.monotonic()
    limits = cast(Mapping[str, Any], config.document["compute_limits"])
    if len(rows) > limits["max_rows"]:
        raise FreezeError("fixture exceeds max_rows")

    oof, row_count, ordered = _chronological_oof(rows)
    training_indices, first_evaluation_time = _first_training_fold(ordered)
    evaluation_mask = _evaluation_mask(ordered, training_indices)
    all_mask = [True] * row_count
    oof["first_fit_evaluation_time"] = first_evaluation_time
    oof["first_fit_prediction_mask"] = evaluation_mask
    candidate_ids = list(_CANDIDATE_IDS)
    if len(candidate_ids) > limits["max_candidates"]:
        raise FreezeError("fixture analysis exceeded frozen candidate limits")

    zero_predictions = [0.0] * row_count
    local_predictions = [row.prediction for row in ordered]

    pooled_coefficients = _fit_ridge(ordered, training_indices)
    pooled_predictions = _apply_causal_linear(ordered, training_indices, pooled_coefficients)
    pooled_fit_executions = 1 if training_indices else 0

    huber_coefficients = _fit_huber(ordered, training_indices)
    huber_predictions = _apply_causal_linear(ordered, training_indices, huber_coefficients)
    huber_fit_executions = 1 if training_indices else 0

    candidate_predictions = {
        "linear_ridge": local_predictions,
        "linear_zero_return": zero_predictions,
        "nonlinear_huber": huber_predictions,
    }
    candidate_masks = {
        "linear_ridge": all_mask,
        "linear_zero_return": all_mask,
        "nonlinear_huber": evaluation_mask,
    }
    zero_metrics = _metrics(
        ordered,
        zero_predictions,
        baseline_mse=None,
        prediction_mask=all_mask,
    )
    baseline_mse = float(zero_metrics["mse"])
    local_reference_mse = float(
        _metrics(ordered, local_predictions, baseline_mse=None, prediction_mask=all_mask)["mse"]
    )
    candidate_fit_executions = {
        "linear_ridge": 0,
        "linear_zero_return": 0,
        "nonlinear_huber": huber_fit_executions,
    }
    candidate_metrics = {
        candidate_id: {
            **_metrics(
                ordered,
                candidate_predictions[candidate_id],
                baseline_mse=baseline_mse if candidate_id != "linear_zero_return" else None,
                minimum_support=19 if candidate_id == "nonlinear_huber" else 1,
                prediction_mask=candidate_masks[candidate_id],
            ),
            "fit_executions": candidate_fit_executions[candidate_id],
            "training_rows": len(training_indices) if candidate_fit_executions[candidate_id] else 0,
            "fit_evaluation_time": (
                first_evaluation_time if candidate_fit_executions[candidate_id] else None
            ),
        }
        for candidate_id in candidate_ids
    }

    control_predictions = {
        "zero_return": zero_predictions,
        "local_ridge": local_predictions,
        "pooled_local_ridge": pooled_predictions,
    }
    control_masks = {
        "zero_return": all_mask,
        "local_ridge": all_mask,
        "pooled_local_ridge": evaluation_mask,
    }
    control_fit_executions = {
        "zero_return": 0,
        "local_ridge": 0,
        "pooled_local_ridge": pooled_fit_executions,
    }
    control_metrics = {
        control_id: {
            **_metrics(
                ordered,
                predictions,
                baseline_mse=(local_reference_mse if control_id == "zero_return" else baseline_mse),
                prediction_mask=control_masks[control_id],
            ),
            "fit_executions": control_fit_executions[control_id],
            "training_rows": len(training_indices) if control_fit_executions[control_id] else 0,
            "fit_evaluation_time": (
                first_evaluation_time if control_fit_executions[control_id] else None
            ),
        }
        for control_id, predictions in control_predictions.items()
    }
    oof_metrics = control_metrics["local_ridge"]
    statistical = {
        "oof": {
            **oof,
            "rank_correlation": oof_metrics["rank_correlation"],
            "coverage": oof_metrics["coverage"],
            "support": oof_metrics["support"],
            "prediction_mask": oof_metrics["prediction_mask"],
        },
        "candidates": [
            {"id": candidate_id, **candidate_metrics[candidate_id]}
            for candidate_id in candidate_ids
        ],
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

    tiny_graph = cast(Mapping[str, Any], config.document["tiny_graph_candidate"])
    if not tiny_graph["enabled"]:
        raise FreezeError("tiny learned graph is required and enabled")
    graph_predictions, graph_fit_count, graph_fit_executions = _graph_predictions(
        ordered, pooled_predictions, training_indices, tiny_graph
    )
    fits = sum(candidate_fit_executions.values()) + pooled_fit_executions + graph_fit_count
    if fits > limits["max_fits"]:
        raise FreezeError("fixture analysis exceeded frozen fit limits")
    graph_masks = {control_id: all_mask for control_id in _GRAPH_CONTROL_IDS}
    graph_masks["pooled_non_graph"] = evaluation_mask
    graph_masks["tiny_learned_graph"] = evaluation_mask
    graph_metrics = {
        control_id: _metrics(
            ordered,
            predictions,
            baseline_mse=baseline_mse,
            prediction_mask=graph_masks[control_id],
        )
        for control_id, predictions in graph_predictions.items()
    }
    graph_metrics["tiny_learned_graph"]["fit_executions"] = graph_fit_executions
    for control_id in _GRAPH_CONTROL_IDS:
        graph_metrics[control_id]["fit_executions"] = 0
    graph = {
        "tiny_learned_graph": {
            "id": "tiny_learned_graph",
            **graph_metrics["tiny_learned_graph"],
            "model": "deterministic_one_hidden_layer_message_passing",
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
    economic_predictions = {
        **candidate_predictions,
        **control_predictions,
        **graph_predictions,
    }
    economic = _economic_views(
        ordered,
        config,
        local_predictions,
        trace_id="linear_ridge",
    )
    economic["configurations"] = {
        trace_id: _economic_views(ordered, config, trace, trace_id=trace_id)
        for trace_id, trace in economic_predictions.items()
    }
    measured = measurement or _runtime_measurement(started)
    _check_hard_limits(limits, measured)
    statuses = {
        candidate_id: metric["status"] for candidate_id, metric in candidate_metrics.items()
    }
    statuses.update(
        {control_id: metric["status"] for control_id, metric in control_metrics.items()}
    )
    statuses.update({control_id: metric["status"] for control_id, metric in graph_metrics.items()})
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
        "target_group_resolution": config.document["target_group_resolution"],
        "retained_parents": {
            "paths": dict(retained_input_paths()),
            "identities": parent_identities,
            "authentication_chain": config.document["authentication_chain"],
            "authentication_command": AUTHENTICATION_COMMAND,
            "stage8_authentication_command": STAGE8_AUTHENTICATION_COMMAND,
            "authentication_performed": False,
            "outcome_decode_performed": False,
        },
        "loader_contract": config.document["retained_loader"],
        "scale_projection": config.document["scale_projection"],
        "observation_contract": config.document["observation_contract"],
        "economic": economic,
        "statistical": statistical,
        "graph": graph,
        "work": {
            "rows": row_count,
            "candidate_count": len(candidate_ids),
            "fit_count": fits,
            "fit_executions": {
                **candidate_fit_executions,
                "pooled_local_ridge": pooled_fit_executions,
                "tiny_learned_graph": graph_fit_executions,
            },
            "graph_fit_count": graph_fit_count,
            "graph_control_count": len(_GRAPH_CONTROL_IDS),
            "within_hard_limits": True,
            "limits": dict(limits),
            "measurement": {
                "elapsed_seconds": round(measured.elapsed_seconds, 9),
                "memory_mb": round(measured.memory_mb, 6),
            },
        },
        "result_classification": {
            "negative": [key for key, status in statuses.items() if status == "NEGATIVE"],
            "failed": [key for key, status in statuses.items() if status == "FAILED"],
            "inconclusive": [key for key, status in statuses.items() if status == "INCONCLUSIVE"],
        },
        "create_only_destination": "operator-selected future R3.H report path",
        "no_post_result_expansion": True,
    }
    return MicroRun(report, {"rows": row_count, "fits": fits, "candidates": len(candidate_ids)})


def synthetic_fixture() -> tuple[FixtureRow, ...]:
    """Deterministic six-target/three-group fixture with mature causal folds."""

    targets = tuple(_TARGET_GROUP_MAP.items())
    decisions = ("00:00", "00:05", "00:10")
    available = ("23:59", "00:04", "00:09")
    mature = ("00:02", "00:07", "00:12")
    dependency_start = ("23:58", "00:03", "00:08")
    dependency_end = ("00:02", "00:07", "00:12")
    rows: list[FixtureRow] = []
    for cycle, (decision_minute, available_minute, mature_minute) in enumerate(
        zip(decisions, available, mature, strict=True)
    ):
        for target_index, (target_id, group) in enumerate(targets):
            feature_value = 0.01 * (target_index + 1) + cycle * 0.002
            target_maturity = (
                "2026-01-01T00:07:00Z"
                if cycle == 0 and target_index == 0
                else f"2026-01-01T{mature_minute}:00Z"
            )
            dependency_end_value = (
                "2026-01-01T00:06:00Z"
                if cycle == 0 and target_index == 0
                else f"2026-01-01T{dependency_end[cycle]}:00Z"
            )
            rows.append(
                FixtureRow(
                    timestamp=f"2026-01-01T{decision_minute}:00Z",
                    decision_time=f"2026-01-01T{decision_minute}:00Z",
                    target_id=target_id,
                    asset=target_id,
                    group=group,
                    horizon_minutes=15,
                    period=f"period-{cycle}",
                    prediction=feature_value * (0.8 + 0.05 * cycle),
                    realised_return=feature_value * (0.5 + 0.1 * cycle),
                    available_at=(
                        f"2025-12-31T{available_minute}:00Z"
                        if cycle == 0
                        else f"2026-01-01T{available_minute}:00Z"
                    ),
                    target_available_at=target_maturity,
                    dependency_start=(
                        f"2025-12-31T{dependency_start[cycle]}:00Z"
                        if cycle == 0
                        else f"2026-01-01T{dependency_start[cycle]}:00Z"
                    ),
                    dependency_end=dependency_end_value,
                    feature_value=feature_value,
                )
            )
    return tuple(rows)


def fixture_from_json(path: str | Path) -> tuple[FixtureRow, ...]:
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise FreezeError("fixture root must be a list")
    return tuple(FixtureRow(**cast(dict[str, Any], row)) for row in cast(list[Any], raw))
