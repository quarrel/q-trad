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

CONTRACT: Final = "qtrad-r3-historical-exploratory-freeze-v2"
REPORT_CONTRACT: Final = "qtrad-r3-historical-exploratory-report-v2"
_REVIEWED_SEMANTIC_IDENTITY: Final = (
    "341d15247f3763c0489984e16df4ce241dd22b0cb753e136199ff4423e9ef667"
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
        "terminal_authentication",
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
        "algorithms",
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
        "terminal_report",
        "terminal_approval",
        "terminal_report_sha256",
        "terminal_approval_sha256",
        "selection",
        "consumed",
        "local_forecast",
        "pooled_forecast",
        "zero_forecast",
        "outcome_evidence",
        "selection_manifest_id",
        "consumed_marker_id",
        "g2_manifest_id",
        "local_forecast_dataset_id",
        "pooled_forecast_dataset_id",
        "zero_forecast_dataset_id",
        "outcome_evidence_manifest_id",
    }
)
_RETAINED_IDENTITY_KEYS: Final = (
    "selection_manifest_id",
    "consumed_marker_id",
    "g2_manifest_id",
    "local_forecast_dataset_id",
    "pooled_forecast_dataset_id",
    "zero_forecast_dataset_id",
    "outcome_evidence_manifest_id",
    "terminal_report_sha256",
    "terminal_approval_sha256",
)
_SECTION_KEYS: Final = {
    "target_group_resolution": frozenset(
        {"metadata_source", "target_ids", "group_ids", "mapping", "expected_identity"}
    ),
    "terminal_authentication": frozenset(
        {
            "contract",
            "state",
            "verdict",
            "report_path",
            "report_sha256",
            "report_byte_size",
            "approval_path",
            "approval_sha256",
        }
    ),
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
            "field_mappings",
            "identity_bindings",
            "locators",
            "decode_policy",
            "selection_policy",
            "streaming_policy",
            "decoder_limits",
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
            "selection",
            "source_scan",
            "decoder_limits",
            "streaming_policy",
            "stop_conditions",
        }
    ),
    "observation_contract": frozenset(
        {"event_aware", "durable_output", "resource_limits", "stop_conditions"}
    ),
    "algorithms": frozenset({"ridge", "huber", "graph", "fixed_graph", "shuffled_graph"}),
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
        if raw["contract"] != CONTRACT or raw["schema_version"] != 2:
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
        _validate_algorithms(raw["algorithms"])
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
    """Deterministic report generated by the fixture or retained-loader boundary."""

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
        raise FreezeError("retained_parents must identify every terminal child and locator")

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

    terminal = raw["terminal_authentication"]
    if not isinstance(terminal, dict):
        raise FreezeError("terminal_authentication must be an object")
    terminal_object = cast(dict[str, Any], terminal)
    _reject_unknown(
        terminal_object, _SECTION_KEYS["terminal_authentication"], "terminal_authentication"
    )
    expected_terminal = {
        "contract": "qtrad-r2-decision-grade-report-review-v1",
        "state": "FINAL_AUTHENTICATED",
        "verdict": "APPROVED",
        "report_path": retained_object["terminal_report"],
        "report_sha256": retained_object["terminal_report_sha256"],
        "report_byte_size": 13008,
        "approval_path": retained_object["terminal_approval"],
        "approval_sha256": retained_object["terminal_approval_sha256"],
    }
    if terminal_object != expected_terminal:
        raise FreezeError("terminal authentication authority is not frozen")

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

    loader = cast(dict[str, Any], raw["retained_loader"])
    if loader["required_columns"] != [
        "decision_time",
        "asset",
        "target_id",
        "group",
        "horizon_minutes",
        "period",
        "prediction",
        "realised_return",
        "available_at",
        "target_available_at",
        "dependency_start",
        "dependency_end",
        "feature_value",
    ]:
        raise FreezeError("retained loader columns are not frozen")
    if "target_position_change" in loader["required_columns"]:
        raise FreezeError("target_position_change compatibility field is forbidden")
    if loader["locators"] != {
        "selection": retained_object["selection"],
        "consumed": retained_object["consumed"],
        "local_forecast": retained_object["local_forecast"],
        "pooled_forecast": retained_object["pooled_forecast"],
        "zero_forecast": retained_object["zero_forecast"],
        "outcome_evidence": retained_object["outcome_evidence"],
    }:
        raise FreezeError("retained loader locators differ from terminal children")
    bindings = cast(Mapping[str, Any], loader["identity_bindings"])
    expected_datasets = {
        "LOCAL_RIDGE": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
        "POOLED_LOCAL_RIDGE": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
        "ZERO_RETURN": "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223",
    }
    expected_configs = {
        "LOCAL_RIDGE": "7fad71b132e9ef29fa1d18c9d6c3a2f729f56191d6ec6ddeff767171393f27e8",
        "POOLED_LOCAL_RIDGE": "05e4767b32e5a59b6510eee10f9308c40cbaa18199bcf95a7bf5e61a1636fe28",
        "ZERO_RETURN": "6ea3c2aff09d5dae7d30d8cc7eb7883382bfb2ce7a3b51cf5f80bb1d69604f4b",
    }
    expected_wrappers = {
        "POOLED_LOCAL_RIDGE": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6",
        "ZERO_RETURN": "bfba06f10de85ad356bfc587d2010544a3f3959d13204f987f22773e916cd72d",
    }
    if (
        bindings.get("dataset_ids") != expected_datasets
        or bindings.get("config_ids") != expected_configs
        or bindings.get("wrapper_sha256s") != expected_wrappers
    ):
        raise FreezeError("retained forecast role bindings are not frozen")

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


def _validate_algorithms(value: Any) -> None:
    if not isinstance(value, dict):
        raise FreezeError("algorithms must be an object")
    algorithms = cast(dict[str, Any], value)
    _reject_unknown(algorithms, _SECTION_KEYS["algorithms"], "algorithms")
    expected_keys = {"ridge", "huber", "graph", "fixed_graph", "shuffled_graph"}
    if set(algorithms) != expected_keys:
        raise FreezeError("algorithm formulations are incomplete")
    ridge = algorithms["ridge"]
    huber = algorithms["huber"]
    graph = algorithms["graph"]
    fixed = algorithms["fixed_graph"]
    shuffled = algorithms["shuffled_graph"]
    if ridge != {
        "regularisation": 0.0,
        "fit_intercept": True,
        "iterations": 1,
        "fit_schedule": "first_mature_fold",
    }:
        raise FreezeError("ridge algorithm is not frozen")
    if huber != {
        "loss": "huber",
        "threshold": 0.02,
        "iterations": 4,
        "degree": 1,
        "fit_schedule": "first_mature_fold",
    }:
        raise FreezeError("huber algorithm is not frozen")
    if graph != {
        "node_feature": "feature_value",
        "adjacency": "same_decision_time",
        "self_edge": False,
        "hidden_units": 4,
        "layers": 1,
        "activation": "tanh",
        "initialisation_seed": 17,
        "learning_rate": 0.05,
        "epochs": 8,
        "loss": "mse",
        "fit_schedule": "first_mature_fold",
    }:
        raise FreezeError("graph algorithm is not frozen")
    if fixed != {"construction": "same_decision_time_excluding_self"}:
        raise FreezeError("fixed graph construction is not frozen")
    if shuffled != {"construction": "reverse_timestamp_group", "shuffle_seed": 0}:
        raise FreezeError("shuffled graph construction is not frozen")


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
    if candidates[2]["degree"] != 1:
        raise FreezeError("nonlinear_huber must use the declared linear feature mapping")


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
    """Return exact terminal children; fixture mode never opens them."""

    root = Path("/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z")
    authority = Path(f"{root}-authority")
    forecasts = root / "g2-preparation" / "forecasts"
    return {
        "terminal_report": str(root / "r2-scientific-report.md"),
        "terminal_approval": str(authority / "r2-scientific-report-review.json"),
        "selection": str(root / "g2-preparation" / "selection.json"),
        "consumed": str(root / "g2-preparation" / "consumed.json"),
        "local_forecast": str(
            forecasts / "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9.json"
        ),
        "pooled_forecast": str(
            forecasts / "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json"
        ),
        "zero_forecast": str(
            forecasts / "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json"
        ),
        "outcome_evidence": str(root / "g2-preparation" / "outcome-evidence.json"),
    }


def _canonical_join_key(row: Mapping[str, Any], mappings: Mapping[str, str]) -> tuple[Any, ...]:
    return tuple(
        row[mappings[field]]
        for field in ("decision_time", "target_id", "asset", "group", "horizon_minutes", "period")
    )


def select_synchronised_rows(
    rows: Sequence[FixtureRow], config: FreezeConfig
) -> tuple[tuple[FixtureRow, ...], dict[str, Any]]:
    """Outcome-blind, bounded selection of earliest complete decision groups."""

    policy = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], config.document["retained_loader"])["selection_policy"],
    )
    target_ids = tuple(cast(Sequence[str], policy["required_target_ids"]))
    groups: dict[str, list[FixtureRow]] = defaultdict(list)
    seen: set[tuple[str, str, str, str, int, str]] = set()
    for row in rows:
        identity = _decision_identity(row)
        if identity in seen:
            raise FreezeError("duplicate decision identity")
        seen.add(identity)
        groups[row.decision_time].append(row)
    selected_times: list[str] = []
    for decision_time in sorted(groups):
        group_rows = groups[decision_time]
        row_targets = [row.target_id for row in group_rows]
        if len(group_rows) != len(target_ids) or set(row_targets) != set(target_ids):
            raise FreezeError("incomplete synchronised decision group")
        selected_times.append(decision_time)
        if len(selected_times) == int(policy["n_complete_decision_groups"]):
            break
    if len(selected_times) != int(policy["n_complete_decision_groups"]):
        raise FreezeError("fewer than the frozen number of complete decision groups")
    selected = tuple(
        row
        for row in sorted(rows, key=lambda item: _decision_identity(item))
        if row.decision_time in selected_times
    )
    if len(selected) > int(policy["analysis_row_bound"]):
        raise FreezeError("selected analysis rows exceed frozen bound")
    source_bytes = sum(
        len(
            json.dumps(
                {field: getattr(row, field) for field in FixtureRow.__dataclass_fields__},
                separators=(",", ":"),
            ).encode()
        )
        for row in rows
    )
    selected_bytes = sum(
        len(
            json.dumps(
                {field: getattr(row, field) for field in FixtureRow.__dataclass_fields__},
                separators=(",", ":"),
            ).encode()
        )
        for row in selected
    )
    return selected, {
        "outcome_blind": True,
        "selected_decision_times": selected_times,
        "selected_rows": len(selected),
        "selected_bytes": selected_bytes,
        "selected_parts": 1 if selected else 0,
        "source_rows": len(rows),
        "source_bytes": source_bytes,
        "source_parts": 1 if rows else 0,
        "complete_groups": len(selected_times),
        "target_count": len(target_ids),
        "stop_state": "STOPPED_AFTER_SELECTED_GROUPS",
    }


def _authority_digest(path: Path) -> tuple[str, int]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise FreezeError(f"cannot read terminal authority child: {path}") from exc
    return hashlib.sha256(payload).hexdigest(), len(payload)


def authenticate_terminal_authority(config: FreezeConfig) -> dict[str, Any]:
    """Authenticate only the exact terminal report and independent approval child."""

    authority = cast(Mapping[str, Any], config.document["terminal_authentication"])
    report_path = Path(cast(str, authority["report_path"]))
    approval_path = Path(cast(str, authority["approval_path"]))
    report_hash, report_size = _authority_digest(report_path)
    if report_hash != authority["report_sha256"] or report_size != authority["report_byte_size"]:
        raise FreezeError("terminal report path/hash/size mismatch")
    approval_hash, _ = _authority_digest(approval_path)
    if approval_hash != authority["approval_sha256"]:
        raise FreezeError("terminal approval byte hash mismatch")
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError("terminal approval is not valid JSON") from exc
    if not isinstance(approval, dict):
        raise FreezeError("terminal approval must be an object")
    approval_object = cast(dict[str, Any], approval)
    report_raw = approval_object.get("report")
    review_raw = approval_object.get("review")
    if not isinstance(report_raw, dict) or not isinstance(review_raw, dict):
        raise FreezeError("terminal approval report/review sections are missing")
    report_object = cast(dict[str, Any], report_raw)
    review_object = cast(dict[str, Any], review_raw)
    required: dict[str, Any] = {
        "contract": approval_object.get("contract"),
        "state": approval_object.get("state"),
        "verdict": review_object.get("verdict"),
        "report_path": report_object.get("path"),
        "report_sha256": report_object.get("sha256"),
        "report_byte_size": report_object.get("byte_size"),
    }
    expected = {
        "contract": authority["contract"],
        "state": authority["state"],
        "verdict": authority["verdict"],
        "report_path": authority["report_path"],
        "report_sha256": authority["report_sha256"],
        "report_byte_size": authority["report_byte_size"],
    }
    for key, expected_value in expected.items():
        if required[key] != expected_value:
            raise FreezeError(f"terminal approval field mismatch: {key}")
    return {
        "authentication_performed": True,
        "report_path": str(report_path),
        "approval_path": str(approval_path),
        "report_sha256": report_hash,
        "approval_sha256": approval_hash,
        "state": authority["state"],
        "verdict": authority["verdict"],
    }


def _json_depth(value: Any) -> int:
    if isinstance(value, dict):
        items = cast(Mapping[Any, Any], value).values()
        return 1 + max((_json_depth(item) for item in items), default=0)
    if isinstance(value, list):
        items = cast(list[Any], value)
        return 1 + max((_json_depth(item) for item in items), default=0)
    return 0


def _read_json_document(
    path: Path, limits: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Mapping[str, Any]], int]:
    """Read one frozen wrapper, validating bytes before decoding its parts."""
    if not path.is_file():
        raise FreezeError(f"retained child path does not exist: {path}")
    size = path.stat().st_size
    if size > int(limits["max_source_bytes"]):
        raise FreezeError("retained child exceeds frozen source-byte bound")
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise FreezeError(f"cannot read retained child: {path}") from exc
    wrapper_hash = hashlib.sha256(raw_bytes).hexdigest()
    expected_wrapper_hash = limits.get("expected_wrapper_sha256")
    if expected_wrapper_hash is not None and wrapper_hash != expected_wrapper_hash:
        raise FreezeError(f"retained wrapper byte hash mismatch: {path}")
    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise FreezeError(f"retained child is not valid JSON: {path}") from exc
    if _json_depth(payload) > int(limits["max_nested_depth"]):
        raise FreezeError("retained child exceeds decoder nesting bound")

    metadata: dict[str, Any]
    if isinstance(payload, dict):
        payload_object = cast(dict[str, Any], payload)
        metadata = dict(payload_object)
        expected_contract = limits.get("expected_wrapper_contract")
        expected_identity = limits.get("expected_wrapper_identity")
        if expected_contract is not None and payload_object.get("contract") != expected_contract:
            raise FreezeError("retained wrapper contract mismatch")
        if expected_identity is not None and payload_object.get("identity") != expected_identity:
            raise FreezeError("retained wrapper identity mismatch")
        parts_value = payload_object.get("parts")
        if parts_value is None:
            records_value: Any = payload_object.get("rows", [payload_object])
            consumed_parts: list[dict[str, Any]] = [
                {
                    "locator": str(path),
                    "sha256": wrapper_hash,
                    "rows": len(cast(list[Any], records_value))
                    if isinstance(records_value, list)
                    else 0,
                    "bytes": size,
                }
            ]
        else:
            if not isinstance(parts_value, list) or not parts_value:
                raise FreezeError("retained wrapper must declare non-empty parts")
            records_value = []
            consumed_parts = []
            required_descriptor_keys = {
                "locator",
                "contract",
                "identity",
                "sha256",
                "byte_size",
                "row_count",
            }
            for part_raw in cast(list[Any], parts_value):
                if not isinstance(part_raw, dict):
                    raise FreezeError("retained part descriptor must be an object")
                descriptor = cast(dict[str, Any], part_raw)
                if set(descriptor) != required_descriptor_keys:
                    raise FreezeError("retained part descriptor fields are incomplete")
                locator_value = descriptor["locator"]
                if not isinstance(locator_value, str):
                    raise FreezeError("retained part locator is malformed")
                part_path = Path(locator_value)
                if part_path.is_absolute() or ".." in part_path.parts or not locator_value:
                    raise FreezeError("retained part locator must be safely relative")
                resolved_part = path.parent / part_path
                try:
                    part_bytes = resolved_part.read_bytes()
                except OSError as exc:
                    raise FreezeError("retained part locator does not exist") from exc
                actual_hash = hashlib.sha256(part_bytes).hexdigest()
                if actual_hash != descriptor["sha256"]:
                    raise FreezeError("retained part byte hash mismatch")
                part_size = len(part_bytes)
                if part_size != int(descriptor["byte_size"]):
                    raise FreezeError("retained part byte-size declaration mismatch")
                if part_size > int(limits["max_source_bytes"]):
                    raise FreezeError("retained part exceeds source-byte bound")
                try:
                    part_payload = json.loads(part_bytes)
                except json.JSONDecodeError as exc:
                    raise FreezeError("retained part is not valid JSON") from exc
                if _json_depth(part_payload) > int(limits["max_nested_depth"]):
                    raise FreezeError("retained part exceeds decoder nesting bound")
                if not isinstance(part_payload, list):
                    raise FreezeError("retained part payload must be an array")
                part_rows = cast(list[Any], part_payload)
                if len(part_rows) != int(descriptor["row_count"]):
                    raise FreezeError("retained part row-count declaration mismatch")
                if len(part_rows) > int(limits["max_part_rows"]):
                    raise FreezeError("retained part exceeds row bound")
                required_record_keys = limits.get("required_record_keys")
                for record_raw in part_rows:
                    if not isinstance(record_raw, dict):
                        raise FreezeError("retained row must be an object")
                    record_object = cast(dict[str, Any], record_raw)
                    if required_record_keys is not None and set(record_object) != set(
                        cast(Sequence[str], required_record_keys)
                    ):
                        raise FreezeError("retained row fields do not match frozen mapping")
                    if len(json.dumps(record_object, separators=(",", ":")).encode()) > int(
                        limits["max_row_bytes"]
                    ):
                        raise FreezeError("retained row exceeds decoder byte bound")
                records_value.extend(part_rows)
                consumed_parts.append(
                    {
                        "locator": locator_value,
                        "sha256": actual_hash,
                        "rows": len(part_rows),
                        "bytes": part_size,
                        "contract": descriptor["contract"],
                        "identity": descriptor["identity"],
                    }
                )
    elif isinstance(payload, list):
        metadata = {}
        records_value = cast(Any, payload)
        consumed_parts: list[dict[str, Any]] = [
            {
                "locator": str(path),
                "sha256": wrapper_hash,
                "rows": len(cast(list[Any], payload)),
                "bytes": size,
            }
        ]
    else:
        raise FreezeError("retained child rows must be an array")
    if not isinstance(records_value, list):
        raise FreezeError("retained child object must contain rows")
    records = cast(list[Any], records_value)
    if len(records) > int(limits["max_source_rows"]):
        raise FreezeError("retained child exceeds frozen source-row bound")
    required_record_keys = limits.get("required_record_keys")
    result: list[Mapping[str, Any]] = []
    for record_raw in records:
        if not isinstance(record_raw, dict):
            raise FreezeError("retained row must be an object")
        record_object = cast(dict[str, Any], record_raw)
        if required_record_keys is not None and set(record_object) != set(
            cast(Sequence[str], required_record_keys)
        ):
            raise FreezeError("retained row fields do not match frozen mapping")
        record = cast(Mapping[str, Any], record_object)
        if len(json.dumps(record_object, separators=(",", ":")).encode()) > int(
            limits["max_row_bytes"]
        ):
            raise FreezeError("retained row exceeds decoder byte bound")
        result.append(record)
    metadata["consumed_parts"] = consumed_parts
    metadata["source_scan_rows"] = len(records)
    metadata["source_scan_bytes"] = size + sum(int(part.get("bytes", 0)) for part in consumed_parts)
    metadata["source_scan_parts"] = len(consumed_parts)
    return metadata, result, size


def _read_json_records(  # pyright: ignore[reportUnusedFunction]
    path: Path, limits: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    return _read_json_document(path, limits)[1]


def _validate_child_metadata(
    name: str,
    metadata: Mapping[str, Any],
    loader: Mapping[str, Any],
) -> None:
    if not metadata:
        raise FreezeError(f"retained child metadata is missing: {name}")
    bindings = cast(Mapping[str, Any], loader["identity_bindings"])
    if name == "selection":
        if metadata.get("contract") != "qtrad-r2-selection-v4":
            raise FreezeError("selection child contract mismatch")
        if metadata.get("manifest_id") != bindings["selection_manifest_id"]:
            raise FreezeError("selection child identity mismatch")
        return
    if name == "consumed":
        if metadata.get("contract") != "qtrad-r2-holdout-consumed-v1":
            raise FreezeError("consumed child contract mismatch")
        if metadata.get("marker_id") != bindings["consumed_marker_id"]:
            raise FreezeError("consumed child identity mismatch")
        return
    dataset_key = {
        "local_forecast": "LOCAL_RIDGE",
        "pooled_forecast": "POOLED_LOCAL_RIDGE",
        "zero_forecast": "ZERO_RETURN",
        "outcome_evidence": None,
    }[name]
    if metadata.get("contract") not in {
        loader["manifest_contract"],
        "qtrad-r2-holdout-forecast-seal-v1",
    }:
        raise FreezeError(f"{name} child contract mismatch")
    child_manifest = metadata.get(
        "g2_manifest_id", metadata.get("seal_id", metadata.get("manifest_id"))
    )
    if child_manifest != bindings["g2_manifest_id"]:
        raise FreezeError(f"{name} manifest identity mismatch")
    if dataset_key is not None:
        dataset_id = cast(Mapping[str, Any], bindings["dataset_ids"])[dataset_key]
        config_id = cast(Mapping[str, Any], bindings["config_ids"])[dataset_key]
        if metadata.get("dataset_id") != dataset_id:
            raise FreezeError(f"{name} dataset identity mismatch")
        if metadata.get("configuration_id") != config_id:
            raise FreezeError(f"{name} configuration identity mismatch")
    elif metadata.get("manifest_id") != bindings["outcome_evidence_manifest_id"]:
        raise FreezeError("outcome evidence manifest identity mismatch")
    parts = metadata.get("parts")
    if parts is not None:
        if not isinstance(parts, list):
            raise FreezeError(f"{name} parts metadata is malformed")
        for part_raw in cast(list[Any], parts):
            if not isinstance(part_raw, dict):
                raise FreezeError(f"{name} part metadata is malformed")
            part = cast(Mapping[str, Any], part_raw)
            if not part.get("sha256"):
                raise FreezeError(f"{name} part hash is missing")


def load_retained_rows(
    config: FreezeConfig, *, locators: Mapping[str, str] | None = None
) -> tuple[tuple[FixtureRow, ...], dict[str, Any]]:
    """Load exact terminal children after one terminal authentication boundary."""
    authority = authenticate_terminal_authority(config)
    loader = cast(Mapping[str, Any], config.document["retained_loader"])
    expected_locators = cast(Mapping[str, str], loader["locators"])
    actual_locators = expected_locators if locators is None else locators
    if dict(actual_locators) != dict(expected_locators):
        raise FreezeError("retained loader locator differs from frozen terminal child")
    decoder_limits = cast(Mapping[str, Any], loader["decoder_limits"])
    streaming = cast(Mapping[str, Any], loader["streaming_policy"])
    limits = dict(decoder_limits)
    limits["max_source_rows"] = streaming["max_source_rows"]
    limits["max_source_bytes"] = streaming["max_source_bytes"]

    selection_metadata, selection_records, selection_size = _read_json_document(
        Path(actual_locators["selection"]), limits
    )
    consumed_metadata, consumed_records, consumed_size = _read_json_document(
        Path(actual_locators["consumed"]), limits
    )
    _validate_child_metadata("selection", selection_metadata, loader)
    _validate_child_metadata("consumed", consumed_metadata, loader)
    if len(selection_records) != 1 or len(consumed_records) != 1:
        raise FreezeError("selection and consumed markers must be single objects")
    selection = selection_records[0]
    consumed = consumed_records[0]
    if consumed.get("state") != "CONSUMED":
        raise FreezeError("retained lifecycle marker is not terminal")
    bindings = cast(Mapping[str, Any], loader["identity_bindings"])
    if selection.get("manifest_id") != bindings["selection_manifest_id"]:
        raise FreezeError("selection manifest identity mismatch")
    if consumed.get("marker_id") != bindings["consumed_marker_id"]:
        raise FreezeError("consumed marker identity mismatch")
    if consumed.get("selection_manifest_id") != bindings["selection_manifest_id"]:
        raise FreezeError("consumed selection identity mismatch")
    if consumed.get("g2_manifest_id", consumed.get("seal_id")) != bindings["g2_manifest_id"]:
        raise FreezeError("consumed G2 manifest identity mismatch")

    child_names = ("local_forecast", "pooled_forecast", "zero_forecast", "outcome_evidence")
    mappings = cast(Mapping[str, str], loader["field_mappings"])
    required = set(cast(Sequence[str], loader["required_columns"]))
    expected_fields = {mappings[field] for field in required}
    wrapper_hashes = cast(Mapping[str, Any], bindings.get("wrapper_sha256s", {}))
    wrapper_roles = {
        "pooled_forecast": "POOLED_LOCAL_RIDGE",
        "zero_forecast": "ZERO_RETURN",
    }
    rows_by_key: dict[tuple[Any, ...], dict[str, Mapping[str, Any]]] = {}
    child_summaries: dict[str, tuple[dict[str, Any], int, int]] = {}
    for name in child_names:
        child_limits = dict(limits)
        child_limits["required_record_keys"] = expected_fields
        role = wrapper_roles.get(name)
        if role is not None:
            expected_hash = wrapper_hashes.get(role)
            if not isinstance(expected_hash, str):
                raise FreezeError(f"missing frozen wrapper hash: {name}")
            child_limits["expected_wrapper_sha256"] = expected_hash
        metadata, records, size = _read_json_document(Path(actual_locators[name]), child_limits)
        _validate_child_metadata(name, metadata, loader)
        child_summaries[name] = (metadata, size, len(records))
        for record in records:
            key = _canonical_join_key(record, mappings)
            children = rows_by_key.setdefault(key, {})
            if name in children:
                raise FreezeError(f"duplicate canonical identity in {name}")
            children[name] = record
            if len(rows_by_key) > int(decoder_limits["max_selected_rows"]):
                raise FreezeError("bounded join state exceeds max_selected_rows before selection")

    rows: list[FixtureRow] = []
    for key, children in rows_by_key.items():
        if set(children) != set(child_names):
            raise FreezeError(f"incomplete canonical join for {key}")
        local = children["local_forecast"]
        outcome = children["outcome_evidence"]
        if set(local) != expected_fields or set(outcome) != expected_fields:
            raise FreezeError("retained row fields do not match frozen mapping")
        rows.append(
            FixtureRow(
                **{field: local[mappings[field]] for field in required}
                | {"realised_return": outcome[mappings["realised_return"]]}
            )
        )
    selected, selection_meta = select_synchronised_rows(rows, config)
    source_rows = sum(summary[2] for summary in child_summaries.values())
    source_bytes = (
        selection_size + consumed_size + sum(summary[1] for summary in child_summaries.values())
    )
    consumed_parts = {
        name: list(cast(list[Any], summary[0].get("consumed_parts", [])))
        for name, summary in child_summaries.items()
    }
    source_parts = 2 + sum(len(parts) or 1 for parts in consumed_parts.values())
    return selected, {
        "authority": authority,
        "selection": selection_meta,
        "source_scan_rows": source_rows,
        "source_scan_bytes": source_bytes,
        "source_scan_parts": source_parts,
        "consumed_parts": consumed_parts,
        "selected_rows": len(selected),
        "selected_parts": sum(len(parts) for parts in consumed_parts.values()),
        "stop_reason": "STOPPED_AFTER_SELECTED_GROUPS",
        "outcome_decode_performed": True,
    }


def load_fixture_rows(
    rows: Sequence[FixtureRow], config: FreezeConfig
) -> tuple[tuple[FixtureRow, ...], dict[str, Any]]:
    """Route injected child records through the retained join and selector boundary."""
    if len(rows) > int(config.document["compute_limits"]["max_rows"]):
        raise FreezeError("fixture source exceeds frozen row bound")
    mappings = cast(Mapping[str, str], config.document["retained_loader"]["field_mappings"])
    expected_fields = set(mappings.values())
    fixture_fields = set(FixtureRow.__dataclass_fields__)
    if not expected_fields <= fixture_fields:
        raise FreezeError("fixture fields do not match frozen retained mapping")
    child_keys: set[tuple[Any, ...]] = set()
    for row in rows:
        record = {field: getattr(row, field) for field in FixtureRow.__dataclass_fields__}
        key = _canonical_join_key(record, mappings)
        if key in child_keys:
            raise FreezeError("duplicate canonical identity in fixture child")
        child_keys.add(key)
    selected, selection = select_synchronised_rows(rows, config)
    encoded_rows = [
        json.dumps(
            {field: getattr(row, field) for field in FixtureRow.__dataclass_fields__},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        for row in rows
    ]
    source_bytes = sum(len(encoded) for encoded in encoded_rows)
    child_hashes = {
        name: hashlib.sha256(b"[" + b",".join(encoded_rows) + b"]").hexdigest()
        for name in ("local_forecast", "pooled_forecast", "zero_forecast", "outcome_evidence")
    }
    consumed_parts = {
        name: [
            {
                "locator": f"<fixture:{name}:part-0>",
                "contract": config.document["retained_loader"]["manifest_contract"],
                "identity": f"fixture-{name}-part-0",
                "sha256": child_hashes[name],
                "rows": len(rows),
                "bytes": source_bytes,
            }
        ]
        for name in child_hashes
    }
    return selected, {
        "authority": {
            "authentication_performed": False,
            "contract": config.document["terminal_authentication"]["contract"],
            "state": "FIXTURE_INJECTED",
            "verdict": "NOT_AUTHENTICATED",
        },
        "selection": selection,
        "source_scan_rows": len(rows),
        "source_scan_bytes": source_bytes,
        "source_scan_parts": 4,
        "consumed_parts": consumed_parts,
        "selected_rows": len(selected),
        "selected_parts": 4,
        "selected_bytes": selection["selected_bytes"],
        "stop_reason": "STOPPED_AFTER_SELECTED_GROUPS",
        "outcome_decode_performed": False,
        "fixture_injected": True,
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


def _fit_ridge(
    rows: Sequence[FixtureRow],
    training_indices: Sequence[int],
    algorithm: Mapping[str, Any],
) -> tuple[float, float]:
    if algorithm["fit_schedule"] != "first_mature_fold":
        raise FreezeError("ridge fit schedule is not frozen")
    features = [rows[index].feature_value for index in training_indices]
    targets = [rows[index].realised_return for index in training_indices]
    mean_feature = sum(features) / len(features)
    mean_target = sum(targets) / len(targets)
    denominator = sum((feature - mean_feature) ** 2 for feature in features)
    regularisation = float(algorithm["regularisation"])
    slope = (
        sum(
            (feature - mean_feature) * (target - mean_target)
            for feature, target in zip(features, targets, strict=True)
        )
        / (denominator + regularisation)
        if denominator + regularisation
        else 0.0
    )
    intercept = mean_target - slope * mean_feature if algorithm["fit_intercept"] else 0.0
    for _ in range(int(algorithm["iterations"]) - 1):
        residuals = [
            target - (intercept + slope * feature)
            for feature, target in zip(features, targets, strict=True)
        ]
        mean_residual = sum(residuals) / len(residuals)
        if algorithm["fit_intercept"]:
            intercept += mean_residual
    return intercept, slope


def _fit_huber(
    rows: Sequence[FixtureRow],
    training_indices: Sequence[int],
    algorithm: Mapping[str, Any],
    ridge_algorithm: Mapping[str, Any],
) -> tuple[float, float]:
    if (
        algorithm["loss"] != "huber"
        or algorithm["degree"] != 1
        or algorithm["fit_schedule"] != "first_mature_fold"
    ):
        raise FreezeError("Huber loss/degree/schedule is not frozen")
    intercept, slope = _fit_ridge(rows, training_indices, ridge_algorithm)
    threshold = float(algorithm["threshold"])
    for _ in range(int(algorithm["iterations"])):
        weighted_features: list[float] = []
        weighted_targets: list[float] = []
        weights: list[float] = []
        for index in training_indices:
            residual = rows[index].realised_return - (intercept + slope * rows[index].feature_value)
            weight = min(1.0, threshold / max(abs(residual), threshold))
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
        intercept = (
            mean_target - slope * mean_feature if algorithm.get("fit_intercept", True) else 0.0
        )
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


def _node_feature(row: FixtureRow, node_feature: str) -> float:
    if node_feature != "feature_value":
        raise FreezeError("graph node feature is not frozen")
    return row.feature_value


def _graph_neighbours(
    rows: Sequence[FixtureRow], graph_algorithm: Mapping[str, Any]
) -> list[float]:
    if graph_algorithm["adjacency"] != "same_decision_time":
        raise FreezeError("graph adjacency is not frozen")
    groups = _timestamp_groups(rows)
    neighbours = [0.0] * len(rows)
    include_self = bool(graph_algorithm["self_edge"])
    for indexes in groups.values():
        for index in indexes:
            others = [other for other in indexes if include_self or other != index]
            neighbours[index] = (
                sum(_node_feature(rows[other], graph_algorithm["node_feature"]) for other in others)
                / len(others)
                if others
                else _node_feature(rows[index], graph_algorithm["node_feature"])
            )
    return neighbours


def _shuffled_graph_predictions(
    rows: Sequence[FixtureRow], shuffled_algorithm: Mapping[str, Any]
) -> list[float]:
    if shuffled_algorithm["construction"] != "reverse_timestamp_group":
        raise FreezeError("shuffled graph construction is not frozen")
    seed = int(shuffled_algorithm["shuffle_seed"])
    groups = _timestamp_groups(rows)
    predictions = [0.0] * len(rows)
    for indexes in groups.values():
        order = list(reversed(indexes))
        if order:
            rotation = seed % len(order)
            order = order[rotation:] + order[:rotation]
        for position, index in enumerate(indexes):
            predictions[index] = _node_feature(rows[order[position]], "feature_value")
    return predictions


def _activation(value: float, activation: str) -> float:
    if activation == "tanh":
        return math.tanh(value)
    if activation == "identity":
        return value
    raise FreezeError("graph activation is not frozen")


def _fit_message_passing(
    rows: Sequence[FixtureRow],
    training_indices: Sequence[int],
    neighbours: Sequence[float],
    algorithm: Mapping[str, Any],
) -> tuple[list[float], list[float], list[float], list[float], list[float], float]:
    if algorithm["layers"] != 1 or algorithm["loss"] != "mse":
        raise FreezeError("graph layers/loss are not frozen")
    if algorithm["fit_schedule"] != "first_mature_fold":
        raise FreezeError("graph fit schedule is not frozen")
    hidden_units = int(algorithm["hidden_units"])
    activation = cast(str, algorithm["activation"])
    seed_scale = 1.0 + (int(algorithm["initialisation_seed"]) - 17) * 0.001
    hidden_local = [0.05 * (index + 1) * seed_scale for index in range(hidden_units)]
    hidden_neighbour = [-0.03 * (index + 1) * seed_scale for index in range(hidden_units)]
    hidden_bias = [0.0] * hidden_units
    output_weights = [0.25] * hidden_units
    output_bias = sum(rows[index].realised_return for index in training_indices) / len(
        training_indices
    )
    learning_rate = float(algorithm["learning_rate"]) / len(training_indices)
    for _ in range(int(algorithm["epochs"])):
        gradients_local = [0.0] * hidden_units
        gradients_neighbour = [0.0] * hidden_units
        gradients_bias = [0.0] * hidden_units
        gradients_output = [0.0] * hidden_units
        gradients_output_bias = 0.0
        for index in training_indices:
            local = _node_feature(rows[index], algorithm["node_feature"])
            neighbour = neighbours[index]
            preactivations = [
                hidden_local[unit] * local + hidden_neighbour[unit] * neighbour + hidden_bias[unit]
                for unit in range(hidden_units)
            ]
            hidden = [_activation(value, activation) for value in preactivations]
            prediction = output_bias + sum(
                weight * value for weight, value in zip(output_weights, hidden, strict=True)
            )
            error = prediction - rows[index].realised_return
            gradients_output_bias += error
            for unit in range(hidden_units):
                derivative = (
                    error
                    * output_weights[unit]
                    * (1.0 - hidden[unit] ** 2 if activation == "tanh" else 1.0)
                )
                gradients_output[unit] += error * hidden[unit]
                gradients_local[unit] += derivative * local
                gradients_neighbour[unit] += derivative * neighbour
                gradients_bias[unit] += derivative
        output_bias -= learning_rate * gradients_output_bias
        for unit in range(hidden_units):
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
    activation: str,
    node_feature: str,
) -> float:
    hidden_local, hidden_neighbour, hidden_bias, output_weights, _, output_bias = model
    local = _node_feature(row, node_feature)
    hidden = [
        _activation(
            hidden_local[unit] * local + hidden_neighbour[unit] * neighbour + hidden_bias[unit],
            activation,
        )
        for unit in range(len(hidden_local))
    ]
    return output_bias + sum(
        weight * value for weight, value in zip(output_weights, hidden, strict=True)
    )


def _graph_predictions(
    rows: Sequence[FixtureRow],
    pooled_predictions: Sequence[float],
    training_indices: Sequence[int],
    tiny_graph: Mapping[str, Any],
    graph_algorithm: Mapping[str, Any],
    fixed_algorithm: Mapping[str, Any],
    shuffled_algorithm: Mapping[str, Any],
    graph_controls: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[float]], int, int]:
    if (
        tiny_graph["layers"] != graph_algorithm["layers"]
        or tiny_graph["hidden_units"] != graph_algorithm["hidden_units"]
    ):
        raise FreezeError("tiny graph and algorithm dimensions differ")
    if fixed_algorithm["construction"] != "same_decision_time_excluding_self":
        raise FreezeError("fixed graph construction is not frozen")
    control_by_kind: dict[str, Mapping[str, Any]] = {}
    for descriptor in graph_controls:
        if not descriptor["enabled"]:
            raise FreezeError("disabled graph control cannot enter the execution set")
        kind = str(descriptor["kind"])
        if kind in control_by_kind:
            raise FreezeError(f"duplicate graph control kind: {kind}")
        if kind not in {"non_graph_local", "non_graph_pooled", "fixed_graph", "shuffled_graph"}:
            raise FreezeError(f"unsupported graph control declaration: {kind}")
        control_by_kind[kind] = descriptor
    expected_kinds = {"non_graph_local", "non_graph_pooled", "fixed_graph", "shuffled_graph"}
    if set(control_by_kind) != expected_kinds:
        raise FreezeError("frozen graph controls are incomplete")
    neighbours = _graph_neighbours(rows, graph_algorithm)
    shuffled = _shuffled_graph_predictions(rows, shuffled_algorithm)
    learned = [0.0] * len(rows)
    model = _fit_message_passing(rows, training_indices, neighbours, graph_algorithm)
    cutoff = max(rows[index].decision_time for index in training_indices)
    for index, row in enumerate(rows):
        if row.decision_time > cutoff:
            learned[index] = _message_prediction(
                row,
                neighbours[index],
                model,
                cast(str, graph_algorithm["activation"]),
                cast(str, graph_algorithm["node_feature"]),
            )
    graph_fit_executions = 1 if training_indices else 0
    return (
        {
            str(control_by_kind["non_graph_local"]["id"]): [row.prediction for row in rows],
            str(control_by_kind["non_graph_pooled"]["id"]): list(pooled_predictions),
            str(control_by_kind["fixed_graph"]["id"]): neighbours,
            str(control_by_kind["shuffled_graph"]["id"]): shuffled,
            str(tiny_graph["id"]): learned,
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
    retained_metadata: Mapping[str, Any] | None = None,
) -> MicroRun:
    """Run bounded causal models against synthetic/fixture rows only."""
    started = time.monotonic()
    limits = cast(Mapping[str, Any], config.document["compute_limits"])
    if len(rows) > limits["max_rows"]:
        raise FreezeError("fixture exceeds max_rows")

    selected_rows, selection_metadata = select_synchronised_rows(rows, config)
    oof, row_count, ordered = _chronological_oof(selected_rows)
    training_indices, first_evaluation_time = _first_training_fold(ordered)
    evaluation_mask = _evaluation_mask(ordered, training_indices)
    all_mask = [True] * row_count
    oof["first_fit_evaluation_time"] = first_evaluation_time
    oof["first_fit_prediction_mask"] = evaluation_mask
    candidate_descriptors = cast(list[Mapping[str, Any]], config.document["nonlinear_candidates"])
    if len(candidate_descriptors) > limits["max_candidates"]:
        raise FreezeError("fixture analysis exceeded frozen candidate limits")
    candidate_by_family: dict[str, Mapping[str, Any]] = {}
    for descriptor in candidate_descriptors:
        if not descriptor["enabled"]:
            raise FreezeError("disabled candidate cannot enter the frozen execution set")
        family = str(descriptor["family"])
        if family in candidate_by_family:
            raise FreezeError(f"duplicate candidate family: {family}")
        if family not in {"ridge", "constant_zero", "bounded_huber"} or descriptor[
            "degree"
        ] not in {0, 1}:
            raise FreezeError(f"unsupported candidate declaration: {family}")
        candidate_by_family[family] = descriptor
    if set(candidate_by_family) != {"ridge", "constant_zero", "bounded_huber"}:
        raise FreezeError("frozen candidate declarations are incomplete")
    candidate_ids = [str(descriptor["id"]) for descriptor in candidate_descriptors]

    zero_predictions = [0.0] * row_count
    local_predictions = [row.prediction for row in ordered]
    algorithms = cast(Mapping[str, Any], config.document["algorithms"])
    pooled_coefficients = _fit_ridge(
        ordered, training_indices, cast(Mapping[str, Any], algorithms["ridge"])
    )
    pooled_predictions = _apply_causal_linear(ordered, training_indices, pooled_coefficients)
    pooled_fit_executions = 1 if training_indices else 0
    huber_coefficients = _fit_huber(
        ordered,
        training_indices,
        cast(Mapping[str, Any], algorithms["huber"]),
        cast(Mapping[str, Any], algorithms["ridge"]),
    )
    huber_predictions = _apply_causal_linear(ordered, training_indices, huber_coefficients)
    huber_fit_executions = 1 if training_indices else 0

    candidate_predictions: dict[str, list[float]] = {}
    candidate_masks: dict[str, list[bool]] = {}
    candidate_fit_executions: dict[str, int] = {}
    for descriptor in candidate_descriptors:
        candidate_id = str(descriptor["id"])
        family = str(descriptor["family"])
        if family == "ridge":
            candidate_predictions[candidate_id] = local_predictions
            candidate_masks[candidate_id] = all_mask
            candidate_fit_executions[candidate_id] = 0
        elif family == "constant_zero":
            candidate_predictions[candidate_id] = zero_predictions
            candidate_masks[candidate_id] = all_mask
            candidate_fit_executions[candidate_id] = 0
        else:
            candidate_predictions[candidate_id] = huber_predictions
            candidate_masks[candidate_id] = evaluation_mask
            candidate_fit_executions[candidate_id] = huber_fit_executions

    zero_id = str(candidate_by_family["constant_zero"]["id"])
    huber_id = str(candidate_by_family["bounded_huber"]["id"])
    zero_metrics = _metrics(ordered, zero_predictions, baseline_mse=None, prediction_mask=all_mask)
    baseline_mse = float(zero_metrics["mse"])
    local_reference_mse = float(
        _metrics(ordered, local_predictions, baseline_mse=None, prediction_mask=all_mask)["mse"]
    )
    candidate_metrics: dict[str, dict[str, Any]] = {}
    for descriptor in candidate_descriptors:
        candidate_id = str(descriptor["id"])
        fit_count = candidate_fit_executions[candidate_id]
        candidate_metrics[candidate_id] = {
            **_metrics(
                ordered,
                candidate_predictions[candidate_id],
                baseline_mse=baseline_mse if candidate_id != zero_id else None,
                minimum_support=19 if candidate_id == huber_id else 1,
                prediction_mask=candidate_masks[candidate_id],
            ),
            "fit_executions": fit_count,
            "training_rows": len(training_indices) if fit_count else 0,
            "fit_evaluation_time": first_evaluation_time if fit_count else None,
            "execution_receipt": dict(descriptor),
        }

    control_ids = [
        str(control_id)
        for control_id in cast(list[Any], config.document["statistical_formulations"]["controls"])
    ]
    if len(control_ids) != 3:
        raise FreezeError("frozen control declarations are incomplete")
    zero_control_id, local_control_id, _pooled_control_id = control_ids
    control_predictions = dict(
        zip(control_ids, (zero_predictions, local_predictions, pooled_predictions), strict=True)
    )
    control_masks = dict(zip(control_ids, (all_mask, all_mask, evaluation_mask), strict=True))
    control_fit_executions = dict(zip(control_ids, (0, 0, pooled_fit_executions), strict=True))
    control_metrics: dict[str, dict[str, Any]] = {
        control_id: {
            **_metrics(
                ordered,
                predictions,
                baseline_mse=(
                    local_reference_mse if control_id == zero_control_id else baseline_mse
                ),
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
    oof_metrics = control_metrics[local_control_id]
    statistical: dict[str, Any] = {
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
            {"id": control_id, **control_metrics[control_id]} for control_id in control_ids
        ],
        "negative_failed_inconclusive_rendered": {
            cast(Mapping[str, Any], metric)["status"]
            for metric in (*candidate_metrics.values(), *control_metrics.values())
        }
        >= {"NEGATIVE", "FAILED", "INCONCLUSIVE"},
        "post_result_selection": False,
    }

    tiny_graph = cast(Mapping[str, Any], config.document["tiny_graph_candidate"])
    if not tiny_graph["enabled"]:
        raise FreezeError("tiny learned graph is required and enabled")
    graph_controls = cast(list[Mapping[str, Any]], config.document["graph_controls"])
    graph_predictions, graph_fit_count, graph_fit_executions = _graph_predictions(
        ordered,
        pooled_predictions,
        training_indices,
        tiny_graph,
        cast(Mapping[str, Any], algorithms["graph"]),
        cast(Mapping[str, Any], algorithms["fixed_graph"]),
        cast(Mapping[str, Any], algorithms["shuffled_graph"]),
        graph_controls,
    )
    fits = sum(candidate_fit_executions.values()) + pooled_fit_executions + graph_fit_count
    if fits > limits["max_fits"]:
        raise FreezeError("fixture analysis exceeded frozen fit limits")
    graph_control_ids = [str(control["id"]) for control in graph_controls]
    tiny_graph_id = str(tiny_graph["id"])
    graph_masks = {control_id: all_mask for control_id in graph_control_ids}
    control_by_kind = {str(control["kind"]): control for control in graph_controls}
    graph_masks[str(control_by_kind["non_graph_pooled"]["id"])] = evaluation_mask
    graph_masks[tiny_graph_id] = evaluation_mask
    graph_metrics = {
        control_id: _metrics(
            ordered,
            predictions,
            baseline_mse=baseline_mse,
            prediction_mask=graph_masks[control_id],
        )
        for control_id, predictions in graph_predictions.items()
    }
    graph_metrics[tiny_graph_id]["fit_executions"] = graph_fit_executions
    graph_metrics[tiny_graph_id]["execution_receipt"] = dict(tiny_graph)
    for control in graph_controls:
        control_id = str(control["id"])
        graph_metrics[control_id]["fit_executions"] = 0
        graph_metrics[control_id]["execution_receipt"] = dict(control)
    graph = {
        tiny_graph_id: {
            "id": tiny_graph_id,
            **graph_metrics[tiny_graph_id],
            "model": "deterministic_one_hidden_layer_message_passing",
            "layers": tiny_graph["layers"],
            "hidden_units": tiny_graph["hidden_units"],
            "algorithm": dict(cast(Mapping[str, Any], algorithms["graph"])),
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
            for control_id in graph_control_ids
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
    statuses: dict[str, str] = {
        str(candidate_id): str(metric["status"])
        for candidate_id, metric in candidate_metrics.items()
    }
    statuses.update(
        {str(control_id): str(metric["status"]) for control_id, metric in control_metrics.items()}
    )
    statuses.update(
        {str(control_id): str(metric["status"]) for control_id, metric in graph_metrics.items()}
    )
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
            "terminal_authentication": config.document["terminal_authentication"],
            "authentication_performed": bool(
                retained_metadata
                and retained_metadata.get("authority", {}).get("authentication_performed", False)
            ),
            "outcome_decode_performed": bool(
                retained_metadata and retained_metadata.get("outcome_decode_performed", False)
            ),
        },
        "selection": {
            **selection_metadata,
            **(
                cast(Mapping[str, Any], retained_metadata["selection"])
                if retained_metadata and "selection" in retained_metadata
                else {}
            ),
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
                _pooled_control_id: pooled_fit_executions,
                tiny_graph_id: graph_fit_executions,
            },
            "graph_fit_count": graph_fit_count,
            "graph_control_count": len(graph_control_ids),
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
    expected = set(FixtureRow.__dataclass_fields__)
    rows: list[FixtureRow] = []
    for item_raw in cast(list[Any], raw):
        if not isinstance(item_raw, dict):
            raise FreezeError("fixture row fields do not match frozen contract")
        item = cast(dict[str, Any], item_raw)
        if set(item) != expected:
            raise FreezeError("fixture row fields do not match frozen contract")
        rows.append(FixtureRow(**item))
    return tuple(rows)
