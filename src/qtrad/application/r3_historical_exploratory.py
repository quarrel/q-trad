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
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import pairwise
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final, cast

CONTRACT: Final = "qtrad-r3-historical-exploratory-freeze-v2"
REPORT_CONTRACT: Final = "qtrad-r3-historical-exploratory-report-v2"
_REVIEWED_SEMANTIC_IDENTITY: Final = (
    "e72c590fa1f4e1d453ef3c10c4c85da3607aa3b4d1f441dea1f58c71bd6cdba0"
)
_PARTITIONED_ROWS_STORAGE: Final = "qtrad-r2-partitioned-json-rows-v1"
_PARTITIONED_PART_CONTRACT: Final = "qtrad-r2-partitioned-json-row-part-v1"
_MAX_PART_BYTES: Final = 64 * 1024 * 1024


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


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
_REPORT_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "contract",
        "schema_version",
        "config_semantic_identity",
        "source_class",
        "price_basis",
        "evidence_class",
        "claims",
        "code_provenance",
        "target_group_resolution",
        "retained_parents",
        "selection",
        "loader_contract",
        "scale_projection",
        "observation_contract",
        "economic",
        "statistical",
        "graph",
        "work",
        "result_classification",
        "create_only_destination",
        "no_post_result_expansion",
    }
)
_SEMANTIC_EXCLUDED_KEYS: Final = frozenset(
    {
        "execution_receipt",
        "role_binding",
        "code_provenance",
        "execution_provenance",
        "measurement",
        "path",
        "paths",
        "wrapper_sha256",
        "module_sha256",
        "python_version",
        "closure_identity",
        "physical_identity",
    }
)
_CHILD_WRAPPER_NAMES: Final = (
    "selection",
    "consumed",
    "local_forecast",
    "pooled_forecast",
    "zero_forecast",
    "outcome_evidence",
)
_CHILD_WRAPPER_SHA256: Final = {
    "selection": "7f1020f422b01a64a47439342d6b2301be6aeb16e934daab11e2598935de3a53",
    "consumed": "da69bbd8cee38cbd9f0df63e7c0c33f6268ed189dc81fce75c7bd5176bfc708f",
    "local_forecast": "a11e7096d25cb0ffcda3c4c0bd2efd5d6fde51c50ff8607598b957176992fd0a",
    "pooled_forecast": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6",
    "zero_forecast": "bfba06f10de85ad356bfc587d2010544a3f3959d13204f987f22773e916cd72d",
    "outcome_evidence": "44be69c09433f4e237eb78535a2e0ba0cab6de67c96d5b79e6e1d69df28f13b1",
}
_CHILD_WRAPPER_CONTRACTS: Final = {
    "selection": "qtrad-r2-selection-v4",
    "consumed": "qtrad-r2-holdout-consumed-v1",
    "local_forecast": "qtrad-r2-holdout-forecast-v1",
    "pooled_forecast": "qtrad-r2-holdout-forecast-v1",
    "zero_forecast": "qtrad-r2-holdout-forecast-v1",
    "outcome_evidence": "qtrad-r2-holdout-outcome-evidence-v1",
}
_CHILD_WRAPPER_IDENTITY_FIELDS: Final = {
    "selection": (
        "manifest_id",
        "15483908d45455ae7ccc5f8d1a3fdcd19b3226308b3a4c6afda067daedb627dc",
    ),
    "consumed": ("marker_id", "c9bf58cebaa51435369704e629fc9df65b05020bf4354c0541ae17726a802446"),
    "local_forecast": (
        "dataset_id",
        "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
    ),
    "pooled_forecast": (
        "dataset_id",
        "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
    ),
    "zero_forecast": (
        "dataset_id",
        "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223",
    ),
    "outcome_evidence": (
        "outcome_evidence_id",
        "480ef61aec7daff49eadbec7d5ec6dc7c7f6f702c92b6a0bdbc9a7c05a342f8a",
    ),
}
_CHILD_WRAPPER_REQUIRED_KEYS: Final = {
    "selection": frozenset(
        {
            "contract",
            "schema_version",
            "experiment_configuration_id",
            "foundation_bundle_id",
            "oof_id",
            "evaluation_report_id",
            "prior_selection_manifest_id",
            "source_class",
            "evidence_class",
            "holdout_scope",
            "evaluated_configuration_ids",
            "selected_configuration_ids",
            "control_configuration_ids",
            "holdout_configuration_ids",
            "comparator_families",
            "configuration_registry",
            "metric_policy",
            "threshold_policy",
            "evaluation_policy",
            "final_fitting_policy",
            "questions",
            "holdout_range",
            "experiment_count",
            "runtime_identities",
            "frozen_metadata",
            "frozen_at",
            "frozen_by",
            "state",
            "holdout_outcomes_accessed",
            "manifest_id",
        }
    ),
    "consumed": frozenset(
        {
            "contract",
            "schema_version",
            "selection_manifest_id",
            "seal_id",
            "opened_marker_id",
            "consumed_at",
            "consumed_by",
            "evaluation_id",
            "outcome_accessed",
            "state",
            "marker_id",
        }
    ),
    "local_forecast": frozenset(
        {
            "contract",
            "schema_version",
            "selection_manifest_id",
            "feature_dataset_id",
            "configuration_id",
            "final_fit_id",
            "final_fit_ids",
            "rows",
            "expected_opportunity_ids",
            "opportunity_target_ids",
            "source_class",
            "evidence_class",
            "holdout_scope",
            "holdout_outcomes_accessed",
            "dataset_id",
        }
    ),
    "pooled_forecast": frozenset(
        {
            "contract",
            "schema_version",
            "selection_manifest_id",
            "feature_dataset_id",
            "configuration_id",
            "final_fit_id",
            "final_fit_ids",
            "rows",
            "expected_opportunity_ids",
            "opportunity_target_ids",
            "source_class",
            "evidence_class",
            "holdout_scope",
            "holdout_outcomes_accessed",
            "dataset_id",
        }
    ),
    "zero_forecast": frozenset(
        {
            "contract",
            "schema_version",
            "selection_manifest_id",
            "feature_dataset_id",
            "configuration_id",
            "final_fit_id",
            "final_fit_ids",
            "rows",
            "expected_opportunity_ids",
            "opportunity_target_ids",
            "source_class",
            "evidence_class",
            "holdout_scope",
            "holdout_outcomes_accessed",
            "dataset_id",
        }
    ),
    "outcome_evidence": frozenset(
        {
            "contract",
            "schema_version",
            "selection_manifest_id",
            "seal_id",
            "opened_marker_id",
            "experiment_configuration_id",
            "foundation_bundle_id",
            "feature_dataset_id",
            "target_dataset_id",
            "holdout_range",
            "expected_target_ids",
            "source_row_ids",
            "outcomes",
            "source_class",
            "evidence_class",
            "holdout_scope",
            "outcome_evidence_id",
        }
    ),
}
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
            "manifest_root",
            "decode_policy",
            "selection_policy",
            "streaming_policy",
            "decoder_limits",
            "child_wrappers",
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
_STATISTICAL_KEYS: Final = frozenset({"oof", "controls", "control_descriptors", "metrics", "views"})
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


def _semantic_projection(value: Any) -> Any:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        return {
            str(key): _semantic_projection(item)
            for key, item in mapping.items()
            if str(key) not in _SEMANTIC_EXCLUDED_KEYS
        }
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[Any], value)
        return [_semantic_projection(item) for item in sequence]
    return value


def _report_semantic_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the report's semantic identity projection, excluding physical provenance."""

    semantic_fields = (
        "contract",
        "schema_version",
        "config_semantic_identity",
        "source_class",
        "price_basis",
        "evidence_class",
        "claims",
        "target_group_resolution",
        "economic",
        "statistical",
        "graph",
        "work",
        "result_classification",
        "no_post_result_expansion",
    )
    payload = {field: _semantic_projection(report[field]) for field in semantic_fields}
    work = cast(dict[str, Any], payload["work"])
    work.pop("measurement", None)
    return payload


def canonical_report_semantic_identity(report: Mapping[str, Any]) -> str:
    """Hash the stable, machine-verifiable semantic report projection."""

    payload = _report_semantic_payload(report)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_report_mapping(
    report: Mapping[str, Any], section: str, required_keys: frozenset[str]
) -> Mapping[str, Any]:
    value = report[section]
    if not isinstance(value, Mapping):
        raise FreezeError(f"renderer report section {section} must be an object")
    mapping = cast(Mapping[str, Any], value)
    missing = required_keys - set(mapping)
    if missing:
        raise FreezeError(
            f"renderer report section {section} is missing keys: {', '.join(sorted(missing))}"
        )
    return mapping


def _require_report_sequence(report: Mapping[str, Any], section: str) -> Sequence[Any]:
    value = report[section]
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise FreezeError(f"renderer report section {section} must be an array")
    return cast(Sequence[Any], value)


def _require_nested_mapping(
    value: Any, label: str, required_keys: frozenset[str] = frozenset()
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FreezeError(f"renderer {label} must be an object")
    mapping = cast(Mapping[str, Any], value)
    missing = required_keys - set(mapping)
    if missing:
        raise FreezeError(f"renderer {label} is missing keys: {', '.join(sorted(missing))}")
    return mapping


def _require_nested_sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise FreezeError(f"renderer {label} must be an array")
    return cast(Sequence[Any], value)


_ECONOMIC_VIEW_KEYS: Final = frozenset(
    {
        "gross_total",
        "gross_mean",
        "turnover",
        "break_even_cost",
        "all_in_cost_sensitivity",
        "position_trace",
    }
)
_ECONOMIC_SENSITIVITY_KEYS: Final = frozenset(
    {"cost", "unit", "net_mean", "break_even_cost", "label"}
)
_ECONOMIC_POSITION_KEYS: Final = frozenset(
    {"target_id", "decision_time", "target_position", "target_position_change", "realised_gross"}
)


def _validate_economic_view(value: Any, label: str) -> Mapping[str, Any]:
    view = _require_nested_mapping(value, label, _ECONOMIC_VIEW_KEYS)
    sensitivity = _require_nested_sequence(
        view["all_in_cost_sensitivity"], f"{label}.all_in_cost_sensitivity"
    )
    for index, item in enumerate(sensitivity):
        _require_nested_mapping(
            item,
            f"{label}.all_in_cost_sensitivity[{index}]",
            _ECONOMIC_SENSITIVITY_KEYS,
        )
    positions = _require_nested_sequence(view["position_trace"], f"{label}.position_trace")
    for index, item in enumerate(positions):
        _require_nested_mapping(item, f"{label}.position_trace[{index}]", _ECONOMIC_POSITION_KEYS)
    return view


_REPORT_STATUS_VALUES: Final = frozenset({"FAILED", "INCONCLUSIVE", "NEGATIVE"})
_REPORT_METRIC_KEYS: Final = frozenset(
    {
        "id",
        "status",
        "mse",
        "rank_correlation",
        "coverage",
        "support",
        "prediction_trace",
        "prediction_mask",
        "fit_executions",
        "training_rows",
        "fit_evaluation_time",
        "execution_receipt",
    }
)
_REPORT_OOF_KEYS: Final = frozenset(
    {
        "formulation",
        "ordering",
        "decision_identity",
        "rows",
        "first_timestamp",
        "last_timestamp",
        "first_fit_evaluation_time",
        "first_fit_prediction_mask",
        "mse",
        "causal",
        "folds",
        "purge_embargo",
        "rank_correlation",
        "coverage",
        "support",
        "prediction_mask",
    }
)


def _strict_mapping(
    value: Any, label: str, expected: frozenset[str] | None = None
) -> Mapping[str, Any]:
    mapping = _require_nested_mapping(value, label)
    if expected is not None:
        keys = frozenset(mapping)
        if keys != expected:
            missing = sorted(expected - keys)
            unknown = sorted(keys - expected)
            details = [
                *(f"missing {key}" for key in missing),
                *(f"unknown {key}" for key in unknown),
            ]
            raise FreezeError(f"renderer {label} schema mismatch: {', '.join(details)}")
    if any(not key for key in mapping):
        raise FreezeError(f"renderer {label} has an invalid key")
    return mapping


def _strict_sequence(value: Any, label: str, *, length: int | None = None) -> Sequence[Any]:
    sequence = _require_nested_sequence(value, label)
    if length is not None and len(sequence) != length:
        raise FreezeError(f"renderer {label} must contain exactly {length} items")
    return sequence


def _strict_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise FreezeError(f"renderer {label} must be a non-empty string")
    return value


def _strict_hash(value: Any, label: str) -> str:
    text = _strict_text(value, label)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise FreezeError(f"renderer {label} must be a lowercase SHA-256 hex digest")
    return text


def _strict_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise FreezeError(f"renderer {label} must be a boolean")
    return value


def _strict_integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FreezeError(f"renderer {label} must be an integer")
    if minimum is not None and value < minimum:
        raise FreezeError(f"renderer {label} is below its minimum")
    return value


def _strict_number(value: Any, label: str, *, nullable: bool = False) -> float | int | None:
    if value is None and nullable:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise FreezeError(f"renderer {label} must be a finite number")
    return value


def _strict_role_binding(value: Any, label: str) -> Mapping[str, Any]:
    binding = _strict_mapping(
        value,
        label,
        frozenset({"dataset_id", "config_id", "wrapper_sha256"}),
    )
    _strict_hash(binding["dataset_id"], f"{label}.dataset_id")
    _strict_hash(binding["config_id"], f"{label}.config_id")
    _strict_hash(binding["wrapper_sha256"], f"{label}.wrapper_sha256")
    return binding


def _strict_receipt(value: Any, label: str, expected: frozenset[str]) -> Mapping[str, Any]:
    receipt = _strict_mapping(value, label)
    keys = frozenset(receipt)
    optional = frozenset({"path", "wrapper_sha256", "module_sha256", "role_binding"})
    if not expected <= keys or not keys <= expected | optional:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected - optional)
        details = [*(f"missing {key}" for key in missing), *(f"unknown {key}" for key in unknown)]
        raise FreezeError(f"renderer {label} schema mismatch: {', '.join(details)}")
    for key, item in receipt.items():
        if key == "role_binding":
            if item is not None:
                _strict_role_binding(item, f"{label}.role_binding")
        elif key.endswith("_sha256"):
            _strict_hash(item, f"{label}.{key}")
        elif isinstance(item, bool):
            _strict_bool(item, f"{label}.{key}")
        elif isinstance(item, int):
            _strict_integer(item, f"{label}.{key}", minimum=0)
        else:
            _strict_text(item, f"{label}.{key}")
    return receipt


def _strict_metric(
    value: Any,
    label: str,
    receipt_keys: frozenset[str],
    metric_keys: frozenset[str] = _REPORT_METRIC_KEYS,
) -> Mapping[str, Any]:
    metric = _strict_mapping(value, label, metric_keys)
    _strict_text(metric["id"], f"{label}.id")
    if metric["status"] not in _REPORT_STATUS_VALUES:
        raise FreezeError(f"renderer {label}.status is not a frozen status")
    _strict_number(metric["mse"], f"{label}.mse", nullable=True)
    _strict_number(metric["rank_correlation"], f"{label}.rank_correlation", nullable=True)
    coverage = _strict_number(metric["coverage"], f"{label}.coverage")
    if coverage is None or not 0.0 <= float(coverage) <= 1.0:
        raise FreezeError(f"renderer {label}.coverage is outside [0, 1]")
    _strict_integer(metric["support"], f"{label}.support", minimum=0)
    trace = _strict_sequence(metric["prediction_trace"], f"{label}.prediction_trace")
    mask = _strict_sequence(metric["prediction_mask"], f"{label}.prediction_mask")
    if len(mask) != len(trace) or any(not isinstance(item, bool) for item in mask):
        raise FreezeError(f"renderer {label}.prediction_mask is not aligned booleans")
    for index, (item, selected) in enumerate(zip(trace, mask, strict=True)):
        if selected:
            _strict_number(item, f"{label}.prediction_trace[{index}]")
        elif item is not None:
            raise FreezeError(
                f"renderer {label}.prediction_trace[{index}] must be null when mask is false"
            )
    _strict_integer(metric["fit_executions"], f"{label}.fit_executions", minimum=0)
    if "training_rows" in metric:
        _strict_integer(metric["training_rows"], f"{label}.training_rows", minimum=0)
    if "fit_evaluation_time" in metric and metric["fit_evaluation_time"] is not None:
        _strict_text(metric["fit_evaluation_time"], f"{label}.fit_evaluation_time")
    _strict_receipt(metric["execution_receipt"], f"{label}.execution_receipt", receipt_keys)
    return metric


def _strict_economic_view(value: Any, label: str, *, root: bool = False) -> Mapping[str, Any]:
    configuration = not root and isinstance(value, Mapping) and "trace_id" in value
    root_keys = frozenset(
        {
            "trace_id",
            "physical_turnover_definition",
            "asset",
            "horizon",
            "period",
            "gross_total",
            "gross_mean",
            "turnover",
            "break_even_cost",
            "all_in_cost_sensitivity",
            "position_trace",
            "configurations",
        }
    )
    keys = (
        root_keys
        if root
        else root_keys - {"configurations"}
        if configuration
        else _ECONOMIC_VIEW_KEYS
    )
    view = _strict_mapping(value, label, keys)
    for key in ("gross_total", "gross_mean", "turnover"):
        _strict_number(view[key], f"{label}.{key}")
    if view["turnover"] < 0:
        raise FreezeError(f"renderer {label}.turnover must be non-negative")
    _strict_number(view["break_even_cost"], f"{label}.break_even_cost", nullable=True)
    if view["break_even_cost"] is not None and view["break_even_cost"] < 0:
        raise FreezeError(f"renderer {label}.break_even_cost must be non-negative")
    sensitivity = _strict_sequence(
        view["all_in_cost_sensitivity"], f"{label}.all_in_cost_sensitivity"
    )
    for index, item in enumerate(sensitivity):
        entry = _strict_mapping(
            item, f"{label}.all_in_cost_sensitivity[{index}]", _ECONOMIC_SENSITIVITY_KEYS
        )
        _strict_number(entry["cost"], f"{label}.all_in_cost_sensitivity[{index}].cost")
        if entry["cost"] < 0:
            raise FreezeError(
                f"renderer {label}.all_in_cost_sensitivity[{index}].cost must be non-negative"
            )
        _strict_text(entry["unit"], f"{label}.all_in_cost_sensitivity[{index}].unit")
        _strict_number(
            entry["net_mean"], f"{label}.all_in_cost_sensitivity[{index}].net_mean", nullable=True
        )
        _strict_number(
            entry["break_even_cost"],
            f"{label}.all_in_cost_sensitivity[{index}].break_even_cost",
            nullable=True,
        )
        if entry["break_even_cost"] is not None and entry["break_even_cost"] < 0:
            raise FreezeError(
                f"renderer {label}.all_in_cost_sensitivity[{index}].break_even_cost "
                "must be non-negative"
            )
        _strict_text(entry["label"], f"{label}.all_in_cost_sensitivity[{index}].label")
    positions = _strict_sequence(view["position_trace"], f"{label}.position_trace")
    for index, item in enumerate(positions):
        entry = _strict_mapping(item, f"{label}.position_trace[{index}]", _ECONOMIC_POSITION_KEYS)
        _strict_text(entry["target_id"], f"{label}.position_trace[{index}].target_id")
        _strict_text(entry["decision_time"], f"{label}.position_trace[{index}].decision_time")
        for key in ("target_position", "target_position_change", "realised_gross"):
            _strict_number(entry[key], f"{label}.position_trace[{index}].{key}")
    if root or configuration:
        _strict_text(view["trace_id"], f"{label}.trace_id")
        _strict_text(view["physical_turnover_definition"], f"{label}.physical_turnover_definition")
        for dimension in ("asset", "horizon", "period"):
            groups = _strict_mapping(view[dimension], f"{label}.{dimension}")
            if not groups:
                raise FreezeError(f"renderer {label}.{dimension} must not be empty")
            for name, child in groups.items():
                _strict_economic_view(child, f"{label}.{dimension}.{name}")
    if root:
        configurations = _strict_mapping(view["configurations"], f"{label}.configurations")
        if not configurations:
            raise FreezeError(f"renderer {label}.configurations must not be empty")
        for name, child in configurations.items():
            _strict_economic_view(child, f"{label}.configurations.{name}")
    return view


_DECIMAL_QUANTUM = Decimal("0.000000000001")


def _report_decimal(value: Any, label: str, *, nullable: bool = False) -> Decimal | None:
    if value is None:
        if nullable:
            return None
        raise FreezeError(f"{label} must be a finite number")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise FreezeError(f"{label} must be a numeric scalar")
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise FreezeError(f"{label} must be a finite number") from exc
    if not parsed.is_finite():
        raise FreezeError(f"{label} must be a finite number")
    return parsed


def _decimal_matches(actual: Decimal | None, expected: Decimal | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return actual == expected


def _qdecimal(value: Decimal) -> Decimal:
    return value.quantize(_DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN)


def _canonical_position_predecessors(
    trace: Sequence[Any], label: str
) -> dict[tuple[str, str, str], Decimal]:
    entries = [
        _strict_mapping(item, f"{label}.position_trace[{index}]")
        for index, item in enumerate(trace)
    ]
    identities = [
        (
            _strict_text(entry["target_id"], f"{label}.position_trace[{index}].target_id"),
            _strict_text(entry["decision_time"], f"{label}.position_trace[{index}].decision_time"),
        )
        for index, entry in enumerate(entries)
    ]
    ordered_indices = sorted(
        range(len(entries)), key=lambda index: (identities[index][1], identities[index][0])
    )
    prior_positions: dict[str, Decimal] = {}
    predecessors: dict[tuple[str, str, str], Decimal] = {}
    for index in ordered_indices:
        entry = entries[index]
        target_id, decision_time = identities[index]
        position = _report_decimal(
            entry["target_position"], f"{label}.position_trace[{index}].target_position"
        )
        assert position is not None
        identity = (label, target_id, decision_time)
        if identity in predecessors:
            raise FreezeError(f"renderer {label}.position_trace has duplicate identity")
        predecessors[identity] = prior_positions.get(target_id, Decimal("0"))
        prior_positions[target_id] = position
    return predecessors


def _reconcile_economic_view(
    view: Mapping[str, Any],
    label: str,
    cost_grid: Sequence[Any] | None,
    canonical_predecessors: Mapping[tuple[str, str, str], Decimal],
    canonical_scope: str,
) -> None:
    trace = _strict_sequence(view["position_trace"], f"{label}.position_trace")
    if not trace:
        raise FreezeError(f"{label}.position_trace must not be empty")
    gross_total = Decimal("0")
    turnover = Decimal("0")
    entries = [
        _strict_mapping(item, f"{label}.position_trace[{index}]")
        for index, item in enumerate(trace)
    ]
    for index, entry in enumerate(entries):
        target_id = _strict_text(entry["target_id"], f"{label}.position_trace[{index}].target_id")
        decision_time = _strict_text(
            entry["decision_time"], f"{label}.position_trace[{index}].decision_time"
        )
        position = _report_decimal(
            entry["target_position"], f"{label}.position_trace[{index}].target_position"
        )
        realised = _report_decimal(
            entry["realised_gross"], f"{label}.position_trace[{index}].realised_gross"
        )
        change = _report_decimal(
            entry["target_position_change"],
            f"{label}.position_trace[{index}].target_position_change",
        )
        assert position is not None and realised is not None and change is not None
        identity = (canonical_scope, target_id, decision_time)
        if identity not in canonical_predecessors:
            raise FreezeError(
                f"renderer {label}.position_trace identity is absent from canonical root trace"
            )
        expected_change = _qdecimal(position - canonical_predecessors[identity])
        if change != expected_change:
            raise FreezeError(f"renderer {label}.position_trace[{index}] change does not reconcile")
        gross_total += realised
        turnover += abs(change)
    count = Decimal(len(trace))
    expected_total = _qdecimal(gross_total)
    expected_mean = _qdecimal(gross_total / count)
    expected_turnover = _qdecimal(turnover)
    expected_break_even = _qdecimal(gross_total / turnover) if turnover else None
    actual_total = _report_decimal(view["gross_total"], f"{label}.gross_total")
    actual_mean = _report_decimal(view["gross_mean"], f"{label}.gross_mean")
    actual_turnover = _report_decimal(view["turnover"], f"{label}.turnover")
    actual_break_even = _report_decimal(
        view["break_even_cost"], f"{label}.break_even_cost", nullable=True
    )
    if not (
        _decimal_matches(actual_total, expected_total)
        and _decimal_matches(actual_mean, expected_mean)
        and _decimal_matches(actual_turnover, expected_turnover)
    ):
        raise FreezeError(f"renderer {label} economic totals do not reconcile with position trace")
    if actual_turnover is not None and actual_turnover < 0:
        raise FreezeError(f"renderer {label}.turnover must be non-negative")
    if actual_break_even is not None and actual_break_even < 0:
        raise FreezeError(f"renderer {label}.break_even_cost must be non-negative")
    if not _decimal_matches(actual_break_even, expected_break_even):
        raise FreezeError(
            f"renderer {label}.break_even_cost does not reconcile with position trace"
        )
    sensitivity = _strict_sequence(
        view["all_in_cost_sensitivity"], f"{label}.all_in_cost_sensitivity"
    )
    if cost_grid is not None and len(sensitivity) != len(cost_grid):
        raise FreezeError(f"renderer {label} cost-grid cardinality mismatch")
    for index, item in enumerate(sensitivity):
        entry = _strict_mapping(item, f"{label}.all_in_cost_sensitivity[{index}]")
        if cost_grid is not None:
            expected_point = _strict_mapping(cost_grid[index], f"config.cost_grid[{index}]")
            expected_cost = _qdecimal(Decimal(str(expected_point["value"])))
            actual_cost = _report_decimal(
                entry["cost"], f"{label}.all_in_cost_sensitivity[{index}].cost"
            )
            if actual_cost != expected_cost or entry["unit"] != expected_point["unit"]:
                raise FreezeError(f"renderer {label} cost-grid point differs from frozen grid")
            if entry["label"] != "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE":
                raise FreezeError(f"renderer {label} cost-grid label is not frozen")
        cost = _report_decimal(entry["cost"], f"{label}.all_in_cost_sensitivity[{index}].cost")
        net_mean = _report_decimal(
            entry["net_mean"], f"{label}.all_in_cost_sensitivity[{index}].net_mean", nullable=True
        )
        entry_break_even = _report_decimal(
            entry["break_even_cost"],
            f"{label}.all_in_cost_sensitivity[{index}].break_even_cost",
            nullable=True,
        )
        assert cost is not None
        expected_net_mean = _qdecimal(gross_total / count - cost * turnover / count)
        if cost < 0 or net_mean is None or not _decimal_matches(net_mean, expected_net_mean):
            raise FreezeError(
                f"renderer {label}.all_in_cost_sensitivity[{index}] does not reconcile"
            )
        if entry_break_even is not None and entry_break_even < 0:
            raise FreezeError(
                f"renderer {label}.all_in_cost_sensitivity[{index}].break_even_cost "
                "must be non-negative"
            )
        if not _decimal_matches(entry_break_even, expected_break_even):
            raise FreezeError(
                f"renderer {label}.all_in_cost_sensitivity[{index}].break_even_cost "
                "does not reconcile"
            )


def _validate_strict_canonical_report(report: Mapping[str, Any], config: FreezeConfig) -> None:
    config_sections = {
        "target_group_resolution": "target_group_resolution",
        "loader_contract": "retained_loader",
        "scale_projection": "scale_projection",
        "observation_contract": "observation_contract",
    }
    for section, config_section in config_sections.items():
        report_json = json.dumps(
            _thaw_value(report[section]), sort_keys=True, separators=(",", ":")
        )
        config_json = json.dumps(
            _thaw_value(config.document[config_section]), sort_keys=True, separators=(",", ":")
        )
        if report_json != config_json:
            raise FreezeError(f"renderer {section} differs from the frozen canonical schema")
    claims = _strict_sequence(report["claims"], "claims", length=len(_NON_EXECUTABLE_CLAIMS))
    if tuple(claims) != _NON_EXECUTABLE_CLAIMS:
        raise FreezeError("renderer claims differ from the frozen canonical claim set")
    provenance = _strict_mapping(
        report["code_provenance"],
        "code_provenance",
        frozenset({"application_contract", "module_sha256", "python_version"}),
    )
    for key, item in provenance.items():
        if key == "module_sha256":
            _strict_hash(item, "code_provenance.module_sha256")
        else:
            _strict_text(item, f"code_provenance.{key}")
    retained = _strict_mapping(
        report["retained_parents"],
        "retained_parents",
        frozenset(
            {
                "paths",
                "identities",
                "role_bindings",
                "terminal_authentication",
                "authentication_performed",
                "outcome_decode_performed",
            }
        ),
    )
    path_keys = frozenset(
        {
            "terminal_report",
            "terminal_approval",
            "selection",
            "consumed",
            "local_forecast",
            "pooled_forecast",
            "zero_forecast",
            "outcome_evidence",
        }
    )
    identity_keys = frozenset(_RETAINED_IDENTITY_KEYS)
    _strict_mapping(retained["paths"], "retained_parents.paths", path_keys)
    _strict_mapping(retained["identities"], "retained_parents.identities", identity_keys)
    _strict_mapping(retained["role_bindings"], "retained_parents.role_bindings")
    _strict_mapping(
        retained["terminal_authentication"],
        "retained_parents.terminal_authentication",
        frozenset(
            {
                "approval_path",
                "approval_sha256",
                "contract",
                "report_byte_size",
                "report_path",
                "report_sha256",
                "state",
                "verdict",
            }
        ),
    )
    for key in ("authentication_performed", "outcome_decode_performed"):
        _strict_bool(retained[key], f"retained_parents.{key}")
    for key, value in retained["paths"].items():
        _strict_text(value, f"retained_parents.paths.{key}")
    for key, value in retained["identities"].items():
        if key.endswith("_sha256") or key.endswith("_manifest_id") or key.endswith("_marker_id"):
            _strict_hash(value, f"retained_parents.identities.{key}")
        else:
            _strict_text(value, f"retained_parents.identities.{key}")
    terminal_authentication = retained["terminal_authentication"]
    for key in ("approval_sha256", "report_sha256"):
        _strict_hash(
            terminal_authentication[key], f"retained_parents.terminal_authentication.{key}"
        )
    _strict_integer(
        terminal_authentication["report_byte_size"],
        "retained_parents.terminal_authentication.report_byte_size",
        minimum=0,
    )
    expected_parent = _strict_mapping(
        config.document["retained_parents"], "config.retained_parents"
    )
    for key in identity_keys:
        if retained["identities"][key] != expected_parent[key]:
            raise FreezeError(f"renderer retained identity {key} differs from frozen parent")
    expected_terminal = _strict_mapping(
        config.document["terminal_authentication"], "config.terminal_authentication"
    )
    if json.dumps(
        _thaw_value(terminal_authentication), sort_keys=True, separators=(",", ":")
    ) != json.dumps(_thaw_value(expected_terminal), sort_keys=True, separators=(",", ":")):
        raise FreezeError("renderer terminal authentication differs from frozen authority")
    role_bindings = retained["role_bindings"]
    identity_bindings = config.document["retained_loader"]["identity_bindings"]
    if role_bindings:
        expected_roles = frozenset({"LOCAL_RIDGE", "POOLED_LOCAL_RIDGE", "ZERO_RETURN"})
        if frozenset(role_bindings) != expected_roles:
            raise FreezeError("renderer retained role bindings differ from frozen role set")
        expected_datasets = identity_bindings["dataset_ids"]
        expected_configs = identity_bindings["config_ids"]
        for role in sorted(expected_roles):
            binding = _strict_role_binding(
                role_bindings[role], f"retained_parents.role_bindings.{role}"
            )
            expected_wrappers = identity_bindings.get("wrapper_sha256s", {})
            if (
                binding["dataset_id"] != expected_datasets[role]
                or binding["config_id"] != expected_configs[role]
                or (
                    role in expected_wrappers
                    and binding["wrapper_sha256"] != expected_wrappers[role]
                )
            ):
                raise FreezeError(
                    f"renderer retained role binding {role} differs from frozen identity"
                )
    selection_keys = frozenset(
        {
            "outcome_blind",
            "selected_decision_times",
            "selected_rows",
            "selected_bytes",
            "selected_parts",
            "source_rows",
            "source_bytes",
            "source_parts",
            "complete_groups",
            "target_count",
            "stop_state",
        }
    )
    if "stop_reason" in report["selection"]:
        selection_keys |= frozenset({"stop_reason"})
    selection = _strict_mapping(report["selection"], "selection", selection_keys)
    policy = _strict_mapping(
        config.document["retained_loader"]["selection_policy"], "config.selection_policy"
    )
    if selection["outcome_blind"] is not policy["outcome_blind"]:
        raise FreezeError("renderer selection outcome_blind differs from frozen policy")
    required_target_ids = _strict_sequence(
        policy["required_target_ids"], "config.selection_policy.required_target_ids"
    )
    expected_target_count = len(required_target_ids)
    expected_groups = _strict_integer(
        policy["n_complete_decision_groups"],
        "config.selection_policy.n_complete_decision_groups",
        minimum=1,
    )
    analysis_bound = _strict_integer(
        policy["analysis_row_bound"], "config.selection_policy.analysis_row_bound", minimum=1
    )
    for key in (
        "selected_rows",
        "selected_bytes",
        "selected_parts",
        "source_rows",
        "source_bytes",
        "source_parts",
        "complete_groups",
        "target_count",
    ):
        _strict_integer(selection[key], f"selection.{key}", minimum=0)
    if (
        selection["target_count"] != expected_target_count
        or selection["complete_groups"] != expected_groups
    ):
        raise FreezeError("renderer selection counts differ from frozen policy")
    if (
        selection["selected_rows"] != expected_groups * expected_target_count
        or selection["selected_rows"] != analysis_bound
    ):
        raise FreezeError("renderer selected rows do not reconcile with frozen policy")
    times = _strict_sequence(
        selection["selected_decision_times"], "selection.selected_decision_times"
    )
    for index, value in enumerate(times):
        _strict_text(value, f"selection.selected_decision_times[{index}]")
    if (
        len(times) != expected_groups
        or len(set(times)) != len(times)
        or list(times) != sorted(times)
    ):
        raise FreezeError("renderer selected decision times do not reconcile with frozen groups")
    _strict_text(selection["stop_state"], "selection.stop_state")
    allowed_stop_states = frozenset(
        {
            "SCANNED_ALL_ROWS_REQUIRED_NO_ORDER_PROOF",
            "SCANNED_ALL_PARTS_REQUIRED_NO_ORDER_PROOF",
            "STOPPED_AFTER_EARLIEST_COMPLETE_GROUP_BOUND",
        }
    )
    if selection["stop_state"] not in allowed_stop_states:
        raise FreezeError("renderer selection.stop_state is not a truthful frozen state")
    if "stop_reason" in selection:
        _strict_text(selection["stop_reason"], "selection.stop_reason")
        stop_reason_by_state = {
            "SCANNED_ALL_ROWS_REQUIRED_NO_ORDER_PROOF": "FULL_SCAN_REQUIRED_NO_ORDER_PROOF",
            "SCANNED_ALL_PARTS_REQUIRED_NO_ORDER_PROOF": "FULL_SCAN_REQUIRED_NO_ORDER_PROOF",
            "STOPPED_AFTER_EARLIEST_COMPLETE_GROUP_BOUND": "EARLIEST_COMPLETE_GROUP_BOUND",
        }
        if selection["stop_reason"] != stop_reason_by_state[selection["stop_state"]]:
            raise FreezeError("renderer selection.stop_reason does not match stop_state")
    economic = _strict_economic_view(report["economic"], "economic", root=True)
    if economic["physical_turnover_definition"] != config.document["turnover_definition"]:
        raise FreezeError("renderer economic turnover definition differs from frozen contract")
    expected_assets = frozenset(config.document["target_group_resolution"]["target_ids"])
    expected_periods = frozenset({"period-0", "period-1", "period-2"})
    root_trace = _strict_sequence(economic["position_trace"], "economic.position_trace")
    if len(root_trace) != selection["selected_rows"]:
        raise FreezeError("renderer economic root trace does not reconcile with selection rows")
    canonical_predecessors = _canonical_position_predecessors(root_trace, "economic")
    root_decisions = frozenset(item["decision_time"] for item in root_trace)
    configurations = _strict_mapping(economic["configurations"], "economic.configurations")
    for dimension, expected_keys in (
        ("asset", expected_assets),
        ("horizon", frozenset({str(config.document["primary_horizon_minutes"])})),
        ("period", expected_periods),
    ):
        groups = _strict_mapping(economic[dimension], f"economic.{dimension}")
        if frozenset(groups) != expected_keys:
            raise FreezeError(f"renderer economic.{dimension} keys differ from frozen dimensions")
        expected_count = {
            "asset": len(root_trace) // len(expected_assets),
            "horizon": len(root_trace),
            "period": len(root_trace) // len(expected_periods),
        }[dimension]
        for name, child in groups.items():
            child_trace = _strict_sequence(
                child["position_trace"], f"economic.{dimension}.{name}.position_trace"
            )
            if len(child_trace) != expected_count:
                raise FreezeError(
                    f"renderer economic.{dimension}.{name} trace cardinality differs from domain"
                )
            child_decisions = frozenset(item["decision_time"] for item in child_trace)
            if dimension == "period":
                period_index = int(name.rsplit("-", 1)[1])
                expected_decisions = frozenset({sorted(root_decisions)[period_index]})
            else:
                expected_decisions = root_decisions
            if child_decisions != expected_decisions:
                raise FreezeError(
                    f"renderer economic.{dimension}.{name} decision-time domain differs from root"
                )
            child_targets = frozenset(item["target_id"] for item in child_trace)
            if dimension == "asset" and child_targets != frozenset({name}):
                raise FreezeError(
                    f"renderer economic.asset.{name} target domain differs from asset key"
                )
            if dimension in {"horizon", "period"} and child_targets != expected_assets:
                raise FreezeError(
                    f"renderer economic.{dimension}.{name} target domain differs "
                    "from frozen targets"
                )
    expected_configurations = frozenset(
        {
            "linear_ridge",
            "linear_zero_return",
            "nonlinear_huber",
            "zero_return",
            "local_ridge",
            "pooled_local_ridge",
            "local_non_graph",
            "pooled_non_graph",
            "fixed_graph",
            "shuffled_graph",
            "tiny_learned_graph",
        }
    )
    if frozenset(configurations) != expected_configurations:
        raise FreezeError("renderer economic.configurations differ from frozen configuration IDs")
    expected_sensitivity_count = len(config.document["cost_grid"])
    for label, view in configurations.items():
        sensitivity = view["all_in_cost_sensitivity"]
        if len(sensitivity) != expected_sensitivity_count:
            raise FreezeError(
                f"renderer economic.configurations.{label} cost grid cardinality mismatch"
            )
        if len(view["position_trace"]) != selection["selected_rows"]:
            raise FreezeError(
                f"renderer economic.configurations.{label} position trace cardinality mismatch"
            )
    _reconcile_economic_view(
        economic,
        "economic",
        config.document["cost_grid"],
        canonical_predecessors,
        "economic",
    )
    for dimension in ("asset", "horizon", "period"):
        for name, child in economic[dimension].items():
            _reconcile_economic_view(
                child,
                f"economic.{dimension}.{name}",
                config.document["cost_grid"],
                canonical_predecessors,
                "economic",
            )
    for name, configuration in configurations.items():
        configuration_label = f"economic.configurations.{name}"
        configuration_predecessors = _canonical_position_predecessors(
            _strict_sequence(
                configuration["position_trace"], f"{configuration_label}.position_trace"
            ),
            configuration_label,
        )
        _reconcile_economic_view(
            configuration,
            configuration_label,
            config.document["cost_grid"],
            configuration_predecessors,
            configuration_label,
        )
        for dimension in ("asset", "horizon", "period"):
            groups = _strict_mapping(configuration[dimension], f"{configuration_label}.{dimension}")
            for group_name, subgroup in groups.items():
                _reconcile_economic_view(
                    subgroup,
                    f"{configuration_label}.{dimension}.{group_name}",
                    config.document["cost_grid"],
                    configuration_predecessors,
                    configuration_label,
                )
    statistical = _strict_mapping(
        report["statistical"],
        "statistical",
        frozenset(
            {
                "oof",
                "candidates",
                "simple_controls",
                "negative_failed_inconclusive_rendered",
                "post_result_selection",
            }
        ),
    )
    oof = _strict_mapping(statistical["oof"], "statistical.oof", _REPORT_OOF_KEYS)
    for key in (
        "formulation",
        "ordering",
        "decision_identity",
        "first_timestamp",
        "last_timestamp",
    ):
        _strict_text(oof[key], f"statistical.oof.{key}")
    if oof["first_fit_evaluation_time"] is not None:
        _strict_text(oof["first_fit_evaluation_time"], "statistical.oof.first_fit_evaluation_time")
    rows = _strict_integer(oof["rows"], "statistical.oof.rows", minimum=1)
    first_fit_mask = _strict_sequence(
        oof["first_fit_prediction_mask"], "statistical.oof.first_fit_prediction_mask", length=rows
    )
    if any(not isinstance(item, bool) for item in first_fit_mask):
        raise FreezeError(
            "renderer statistical.oof.first_fit_prediction_mask must contain booleans"
        )
    _strict_number(oof["mse"], "statistical.oof.mse")
    _strict_bool(oof["causal"], "statistical.oof.causal")
    _strict_number(oof["rank_correlation"], "statistical.oof.rank_correlation", nullable=True)
    _strict_number(oof["coverage"], "statistical.oof.coverage")
    _strict_integer(oof["support"], "statistical.oof.support", minimum=0)
    prediction_mask = _strict_sequence(
        oof["prediction_mask"], "statistical.oof.prediction_mask", length=rows
    )
    if any(not isinstance(item, bool) for item in prediction_mask):
        raise FreezeError("renderer statistical.oof.prediction_mask must contain booleans")
    oof_support = sum(prediction_mask)
    if oof["support"] != oof_support:
        raise FreezeError("renderer statistical.oof support does not reconcile with mask")
    oof_coverage = _report_decimal(oof["coverage"], "renderer statistical.oof.coverage")
    if oof_coverage != _qdecimal(Decimal(oof_support) / Decimal(rows)):
        raise FreezeError("renderer statistical.oof coverage does not reconcile with mask")
    folds = _strict_sequence(oof["folds"], "statistical.oof.folds")
    for index, fold in enumerate(folds):
        item = _strict_mapping(
            fold,
            f"statistical.oof.folds[{index}]",
            frozenset(
                {
                    "evaluation_time",
                    "training_rows",
                    "evaluation_rows",
                    "purged_rows",
                    "embargoed_rows",
                }
            ),
        )
        _strict_text(item["evaluation_time"], f"statistical.oof.folds[{index}].evaluation_time")
        for key in ("training_rows", "evaluation_rows", "purged_rows", "embargoed_rows"):
            _strict_integer(item[key], f"statistical.oof.folds[{index}].{key}", minimum=0)
    if sum(item["evaluation_rows"] for item in folds) != sum(first_fit_mask):
        raise FreezeError("renderer OOF fold evaluation rows do not reconcile with first-fit mask")
    purge = _strict_mapping(
        oof["purge_embargo"],
        "statistical.oof.purge_embargo",
        frozenset({"applied", "rows_excluded", "reason"}),
    )
    _strict_bool(purge["applied"], "statistical.oof.purge_embargo.applied")
    _strict_integer(
        purge["rows_excluded"], "statistical.oof.purge_embargo.rows_excluded", minimum=0
    )
    _strict_text(purge["reason"], "statistical.oof.purge_embargo.reason")
    candidates = _strict_sequence(statistical["candidates"], "statistical.candidates", length=3)
    for index, item in enumerate(candidates):
        _strict_metric(
            item,
            f"statistical.candidates[{index}]",
            frozenset({"degree", "enabled", "family", "id"}),
        )
    candidate_ids = frozenset(item["id"] for item in candidates)
    expected_candidate_ids = frozenset(
        item["id"] for item in config.document["nonlinear_candidates"]
    )
    if candidate_ids != expected_candidate_ids:
        raise FreezeError("renderer statistical candidate IDs differ from frozen candidates")
    controls = _strict_sequence(
        statistical["simple_controls"], "statistical.simple_controls", length=3
    )
    for index, item in enumerate(controls):
        _strict_metric(
            item,
            f"statistical.simple_controls[{index}]",
            frozenset({"candidate_id", "fit_policy", "id", "kind", "role_binding"}),
        )
    control_ids = frozenset(item["id"] for item in controls)
    expected_control_ids = frozenset(config.document["statistical_formulations"]["controls"])
    if control_ids != expected_control_ids:
        raise FreezeError("renderer statistical control IDs differ from frozen controls")
    _strict_bool(
        statistical["negative_failed_inconclusive_rendered"],
        "statistical.negative_failed_inconclusive_rendered",
    )
    _strict_bool(statistical["post_result_selection"], "statistical.post_result_selection")
    graph = _strict_mapping(
        report["graph"],
        "graph",
        frozenset({"tiny_learned_graph", "controls", "r4_replacement_required"}),
    )
    tiny = _strict_metric(
        graph["tiny_learned_graph"],
        "graph.tiny_learned_graph",
        frozenset({"enabled", "family", "hidden_units", "id", "layers"}),
        (_REPORT_METRIC_KEYS - {"training_rows", "fit_evaluation_time"})
        | frozenset(
            {
                "model",
                "layers",
                "hidden_units",
                "algorithm",
                "feasibility_only",
                "fits",
                "walk_forward_fit_executions",
            }
        ),
    )
    _strict_text(tiny["model"], "graph.tiny_learned_graph.model")
    algorithm = _strict_mapping(
        tiny["algorithm"],
        "graph.tiny_learned_graph.algorithm",
        frozenset(config.document["algorithms"]["graph"]),
    )
    if json.dumps(_thaw_value(algorithm), sort_keys=True, separators=(",", ":")) != json.dumps(
        _thaw_value(config.document["algorithms"]["graph"]), sort_keys=True, separators=(",", ":")
    ):
        raise FreezeError("renderer graph tiny algorithm differs from frozen algorithm schema")
    _strict_bool(tiny["feasibility_only"], "graph.tiny_learned_graph.feasibility_only")
    for key in ("layers", "hidden_units", "fits", "walk_forward_fit_executions"):
        _strict_integer(tiny[key], f"graph.tiny_learned_graph.{key}", minimum=0)
    graph_controls = _strict_sequence(graph["controls"], "graph.controls", length=4)
    for index, item in enumerate(graph_controls):
        control = _strict_metric(
            item,
            f"graph.controls[{index}]",
            frozenset({"enabled", "id", "kind"}),
            (_REPORT_METRIC_KEYS - {"training_rows", "fit_evaluation_time"})
            | frozenset({"feasibility_only"}),
        )
        _strict_bool(control["feasibility_only"], f"graph.controls[{index}].feasibility_only")
    graph_control_ids = frozenset(item["id"] for item in graph_controls)
    expected_graph_control_ids = frozenset(item["id"] for item in config.document["graph_controls"])
    if graph_control_ids != expected_graph_control_ids:
        raise FreezeError("renderer graph control IDs differ from frozen controls")

    def descriptor_projection(
        receipt: Mapping[str, Any], expected: Mapping[str, Any], label: str
    ) -> None:
        receipt_map = _strict_mapping(receipt, label)
        expected_keys = frozenset(expected)
        if not expected_keys.issubset(frozenset(receipt_map)):
            raise FreezeError(f"{label} is missing frozen descriptor fields")
        actual = {key: receipt_map[key] for key in expected_keys}
        if json.dumps(_thaw_value(actual), sort_keys=True, separators=(",", ":")) != json.dumps(
            _thaw_value(expected), sort_keys=True, separators=(",", ":")
        ):
            raise FreezeError(f"renderer {label} differs from frozen descriptor")

    expected_candidates = {item["id"]: item for item in config.document["nonlinear_candidates"]}
    for item in candidates:
        descriptor_projection(
            item["execution_receipt"],
            expected_candidates[item["id"]],
            f"statistical.candidates.{item['id']}.execution_receipt",
        )
    expected_controls = {
        item["id"]: item
        for item in config.document["statistical_formulations"]["control_descriptors"]
    }
    for item in controls:
        descriptor_projection(
            item["execution_receipt"],
            expected_controls[item["id"]],
            f"statistical.simple_controls.{item['id']}.execution_receipt",
        )
    descriptor_projection(
        tiny["execution_receipt"],
        config.document["tiny_graph_candidate"],
        "graph.tiny_learned_graph.execution_receipt",
    )
    expected_graph_controls = {item["id"]: item for item in config.document["graph_controls"]}
    for item in graph_controls:
        descriptor_projection(
            item["execution_receipt"],
            expected_graph_controls[item["id"]],
            f"graph.controls.{item['id']}.execution_receipt",
        )
    metric_outputs = [*candidates, *controls, tiny, *graph_controls]
    for metric in metric_outputs:
        if len(metric["prediction_trace"]) != rows:
            raise FreezeError(
                f"renderer metric {metric['id']} prediction trace is not aligned with OOF rows"
            )
        if len(metric["prediction_mask"]) != rows:
            raise FreezeError(
                f"renderer metric {metric['id']} prediction mask is not aligned with OOF rows"
            )
        mask = metric["prediction_mask"]
        expected_support = sum(mask)
        if metric["support"] != expected_support:
            raise FreezeError(
                f"renderer metric {metric['id']} support does not reconcile with mask"
            )
        expected_coverage = _qdecimal(Decimal(expected_support) / Decimal(rows))
        actual_coverage = _report_decimal(metric["coverage"], f"metric {metric['id']}.coverage")
        if actual_coverage != expected_coverage:
            raise FreezeError(
                f"renderer metric {metric['id']} coverage does not reconcile with mask"
            )
    expected_role_pairs = {
        (identity_bindings["dataset_ids"][role], identity_bindings["config_ids"][role])
        for role in ("LOCAL_RIDGE", "POOLED_LOCAL_RIDGE", "ZERO_RETURN")
    }
    expected_wrappers = identity_bindings.get("wrapper_sha256s", {})
    for metric in metric_outputs:
        role_binding = metric["execution_receipt"].get("role_binding")
        if role_binding is None:
            continue
        pair = (role_binding["dataset_id"], role_binding["config_id"])
        if pair not in expected_role_pairs:
            raise FreezeError(f"renderer metric {metric['id']} has an unknown role binding")
        role = next(
            role
            for role in ("LOCAL_RIDGE", "POOLED_LOCAL_RIDGE", "ZERO_RETURN")
            if pair
            == (identity_bindings["dataset_ids"][role], identity_bindings["config_ids"][role])
        )
        expected_wrapper = expected_wrappers.get(role)
        if expected_wrapper is not None and role_binding["wrapper_sha256"] != expected_wrapper:
            raise FreezeError(
                f"renderer metric {metric['id']} role wrapper differs from frozen role"
            )
    _strict_bool(graph["r4_replacement_required"], "graph.r4_replacement_required")
    work = _strict_mapping(
        report["work"],
        "work",
        frozenset(
            {
                "rows",
                "candidate_count",
                "fit_count",
                "fit_executions",
                "graph_fit_count",
                "graph_control_count",
                "within_hard_limits",
                "limits",
                "measurement",
            }
        ),
    )
    for key in ("rows", "candidate_count", "fit_count", "graph_fit_count", "graph_control_count"):
        _strict_integer(work[key], f"work.{key}", minimum=0)
    fit_executions = _strict_mapping(work["fit_executions"], "work.fit_executions")
    expected_fit_ids = candidate_ids | frozenset({"pooled_local_ridge", "tiny_learned_graph"})
    if frozenset(fit_executions) != expected_fit_ids:
        raise FreezeError("renderer work.fit_executions differs from frozen execution set")
    for key, value in fit_executions.items():
        _strict_integer(value, f"work.fit_executions.{key}", minimum=0)
    if sum(fit_executions.values()) != work["fit_count"]:
        raise FreezeError("renderer work.fit_executions does not reconcile fit_count")
    metric_by_id = {metric["id"]: metric for metric in metric_outputs}
    for execution_id, execution_count in fit_executions.items():
        if metric_by_id[execution_id]["fit_executions"] != execution_count:
            raise FreezeError(f"renderer metric {execution_id} fit count does not reconcile work")
    if work["rows"] != rows or work["rows"] != selection["selected_rows"]:
        raise FreezeError("renderer work rows do not reconcile with selected/OOF rows")
    if work["candidate_count"] != len(candidates):
        raise FreezeError(
            "renderer work candidate_count does not reconcile with emitted candidates"
        )
    limits_document = config.document["compute_limits"]
    if work["candidate_count"] > limits_document["max_candidates"]:
        raise FreezeError("renderer candidate count exceeds frozen compute cap")
    if work["fit_count"] > limits_document["max_fits"]:
        raise FreezeError("renderer fit count exceeds frozen compute cap")
    if work["rows"] > limits_document["max_rows"]:
        raise FreezeError("renderer row count exceeds frozen compute cap")
    if work["graph_fit_count"] != tiny["fits"]:
        raise FreezeError("renderer graph fit count does not reconcile with tiny graph fits")
    if work["graph_control_count"] != len(graph_controls):
        raise FreezeError("renderer graph control count does not reconcile with controls")
    if (
        tiny["walk_forward_fit_executions"] != tiny["fits"]
        or tiny["walk_forward_fit_executions"] != work["graph_fit_count"]
    ):
        raise FreezeError("renderer graph walk-forward count does not reconcile with graph work")
    if not any(first_fit_mask):
        raise FreezeError("renderer OOF first-fit mask has no executable evaluation rows")
    _strict_bool(work["within_hard_limits"], "work.within_hard_limits")
    limits = _strict_mapping(work["limits"], "work.limits")
    if json.dumps(_thaw_value(limits), sort_keys=True, separators=(",", ":")) != json.dumps(
        _thaw_value(config.document["compute_limits"]), sort_keys=True, separators=(",", ":")
    ):
        raise FreezeError("renderer work.limits differs from the frozen compute limits")
    measurement = _strict_mapping(
        work["measurement"], "work.measurement", frozenset({"elapsed_seconds", "memory_mb"})
    )
    elapsed = _report_decimal(
        measurement["elapsed_seconds"], "renderer work.measurement.elapsed_seconds"
    )
    memory = _report_decimal(measurement["memory_mb"], "renderer work.measurement.memory_mb")
    assert elapsed is not None and memory is not None
    max_elapsed = _report_decimal(
        limits_document["max_elapsed_seconds"], "config.compute_limits.max_elapsed_seconds"
    )
    max_memory = _report_decimal(
        limits_document["max_memory_mb"], "config.compute_limits.max_memory_mb"
    )
    assert max_elapsed is not None and max_memory is not None
    within_caps = elapsed <= max_elapsed and memory <= max_memory
    if not within_caps or work["within_hard_limits"] is not within_caps:
        raise FreezeError("renderer measurement does not reconcile with frozen hard limits")
    classification = _strict_mapping(
        report["result_classification"],
        "result_classification",
        frozenset({"negative", "failed", "inconclusive"}),
    )
    seen_classified: set[str] = set()
    expected_statuses = {"negative": "NEGATIVE", "failed": "FAILED", "inconclusive": "INCONCLUSIVE"}
    metric_statuses = {item["id"]: item["status"] for item in metric_outputs}
    for key, expected_status in expected_statuses.items():
        values = _strict_sequence(classification[key], f"result_classification.{key}")
        local_ids: set[str] = set()
        for index, item in enumerate(values):
            identifier = _strict_text(item, f"result_classification.{key}[{index}]")
            if identifier in local_ids or identifier in seen_classified:
                raise FreezeError("renderer result classification is not disjoint")
            if metric_statuses.get(identifier) != expected_status:
                raise FreezeError("renderer result classification status mismatch")
            local_ids.add(identifier)
            seen_classified.add(identifier)
    if seen_classified != set(metric_statuses):
        raise FreezeError("renderer result classification is not a complete metric partition")
    _strict_text(report["create_only_destination"], "create_only_destination")
    _strict_bool(report["no_post_result_expansion"], "no_post_result_expansion")


def _validate_renderable_report(report: Mapping[str, Any], config: FreezeConfig) -> None:
    report_keys = frozenset(report)
    if report_keys != _REPORT_TOP_LEVEL_KEYS:
        missing = sorted(_REPORT_TOP_LEVEL_KEYS - report_keys)
        unknown = sorted(report_keys - _REPORT_TOP_LEVEL_KEYS)
        details = [*(f"missing {key}" for key in missing), *(f"unknown {key}" for key in unknown)]
        raise FreezeError("renderer report schema mismatch: " + ", ".join(details))
    _strict_text(report["contract"], "contract")
    _strict_integer(report["schema_version"], "schema_version", minimum=1)
    _strict_hash(report["config_semantic_identity"], "config_semantic_identity")
    for key in ("source_class", "price_basis", "evidence_class"):
        _strict_text(report[key], key)
    _strict_sequence(report["claims"], "claims")
    if report["contract"] != REPORT_CONTRACT or report["schema_version"] != 1:
        raise FreezeError("renderer received an unsupported report contract")
    if report["config_semantic_identity"] != config.semantic_identity:
        raise FreezeError("renderer report/config semantic identity mismatch")
    if report["source_class"] != "IBKR_HISTORICAL_RESEARCH":
        raise FreezeError("renderer requires IBKR historical research source")
    if report["price_basis"] != "MIDPOINT_OHLC":
        raise FreezeError("renderer requires MIDPOINT-only report")
    if report["evidence_class"] != "HISTORICAL_EXPLORATORY_IMPLEMENTATION_EVIDENCE":
        raise FreezeError("renderer requires historical exploratory evidence")
    if report["claims"] != list(_NON_EXECUTABLE_CLAIMS):
        raise FreezeError("renderer requires the complete non-executable claim set")
    if not isinstance(report["no_post_result_expansion"], bool):
        raise FreezeError("renderer requires a boolean no_post_result_expansion")
    _require_report_sequence(report, "claims")
    _require_report_mapping(
        report,
        "code_provenance",
        frozenset({"application_contract", "module_sha256", "python_version"}),
    )
    _require_report_mapping(
        report,
        "retained_parents",
        frozenset(
            {
                "paths",
                "identities",
                "role_bindings",
                "terminal_authentication",
                "authentication_performed",
                "outcome_decode_performed",
            }
        ),
    )
    _require_report_mapping(
        report, "target_group_resolution", frozenset({"target_ids", "group_ids"})
    )
    _require_report_mapping(report, "selection", frozenset({"selected_rows"}))
    _require_report_mapping(report, "loader_contract", frozenset({"manifest_contract"}))
    _require_report_mapping(report, "scale_projection", frozenset({"retained_row_count"}))
    _require_report_mapping(
        report, "observation_contract", frozenset({"event_aware", "resource_limits"})
    )
    _require_report_mapping(
        report,
        "economic",
        frozenset(
            {
                "trace_id",
                "physical_turnover_definition",
                "gross_total",
                "gross_mean",
                "turnover",
                "break_even_cost",
                "all_in_cost_sensitivity",
                "position_trace",
                "asset",
                "horizon",
                "period",
                "configurations",
            }
        ),
    )
    statistical = _require_report_mapping(
        report,
        "statistical",
        frozenset(
            {
                "oof",
                "candidates",
                "simple_controls",
                "negative_failed_inconclusive_rendered",
                "post_result_selection",
            }
        ),
    )
    economic = _require_nested_mapping(report["economic"], "economic")
    _validate_economic_view(economic, "economic")
    for dimension in ("asset", "horizon", "period"):
        groups = _require_nested_mapping(economic[dimension], f"economic.{dimension}")
        if not groups:
            raise FreezeError(f"renderer economic {dimension} must not be empty")
        for group_name, group in groups.items():
            _validate_economic_view(group, f"economic.{dimension}.{group_name}")
    configurations = _require_nested_mapping(economic["configurations"], "economic.configurations")
    if not configurations:
        raise FreezeError("renderer economic configurations must not be empty")
    for trace_id, configuration in configurations.items():
        _validate_economic_view(configuration, f"economic.configurations.{trace_id}")

    oof = _require_nested_mapping(
        statistical["oof"],
        "statistical.oof",
        frozenset(
            {
                "formulation",
                "ordering",
                "decision_identity",
                "rows",
                "first_timestamp",
                "last_timestamp",
                "mse",
                "causal",
                "folds",
                "purge_embargo",
                "rank_correlation",
                "coverage",
                "support",
                "prediction_mask",
            }
        ),
    )
    folds = _require_nested_sequence(oof["folds"], "statistical.oof.folds")
    for index, fold in enumerate(folds):
        _require_nested_mapping(
            fold,
            f"statistical.oof.folds[{index}]",
            frozenset(
                {
                    "evaluation_time",
                    "training_rows",
                    "evaluation_rows",
                    "purged_rows",
                    "embargoed_rows",
                }
            ),
        )
    _require_nested_mapping(
        oof["purge_embargo"],
        "statistical.oof.purge_embargo",
        frozenset({"applied", "rows_excluded", "reason"}),
    )
    prediction_mask = _require_nested_sequence(
        oof["prediction_mask"], "statistical.oof.prediction_mask"
    )
    if any(not isinstance(value, bool) for value in prediction_mask):
        raise FreezeError("renderer statistical.oof.prediction_mask must contain booleans")
    for item_name in ("candidates", "simple_controls"):
        items = _require_nested_sequence(statistical[item_name], f"statistical.{item_name}")
        for index, item in enumerate(items):
            item_mapping = _require_nested_mapping(
                item,
                f"statistical.{item_name}[{index}]",
                frozenset({"id", "execution_receipt"}),
            )
            _require_nested_mapping(
                item_mapping["execution_receipt"],
                f"statistical.{item_name}[{index}].execution_receipt",
            )
    graph = _require_report_mapping(
        report,
        "graph",
        frozenset({"tiny_learned_graph", "controls", "r4_replacement_required"}),
    )
    tiny_graph = _require_nested_mapping(
        graph["tiny_learned_graph"],
        "graph.tiny_learned_graph",
        frozenset(
            {
                "id",
                "status",
                "mse",
                "rank_correlation",
                "coverage",
                "support",
                "prediction_trace",
                "prediction_mask",
                "fit_executions",
                "execution_receipt",
                "model",
                "layers",
                "hidden_units",
                "algorithm",
                "feasibility_only",
                "fits",
                "walk_forward_fit_executions",
            }
        ),
    )
    _require_nested_mapping(
        tiny_graph["execution_receipt"], "graph.tiny_learned_graph.execution_receipt"
    )
    _require_nested_mapping(tiny_graph["algorithm"], "graph.tiny_learned_graph.algorithm")
    _require_nested_sequence(
        tiny_graph["prediction_trace"], "graph.tiny_learned_graph.prediction_trace"
    )
    _require_nested_sequence(
        tiny_graph["prediction_mask"], "graph.tiny_learned_graph.prediction_mask"
    )
    graph_controls = _require_nested_sequence(graph["controls"], "graph.controls")
    for index, item in enumerate(graph_controls):
        item_mapping = _require_nested_mapping(
            item,
            f"graph.controls[{index}]",
            frozenset({"id", "execution_receipt", "feasibility_only"}),
        )
        _require_nested_mapping(
            item_mapping["execution_receipt"],
            f"graph.controls[{index}].execution_receipt",
        )
    _require_report_mapping(
        report,
        "work",
        frozenset(
            {
                "rows",
                "candidate_count",
                "fit_count",
                "fit_executions",
                "within_hard_limits",
                "measurement",
            }
        ),
    )
    _require_nested_mapping(
        report["work"]["measurement"],
        "work.measurement",
        frozenset({"elapsed_seconds", "memory_mb"}),
    )
    classification = _require_report_mapping(
        report, "result_classification", frozenset({"negative", "failed", "inconclusive"})
    )
    for status in ("negative", "failed", "inconclusive"):
        if isinstance(classification[status], str) or not isinstance(
            classification[status], Sequence
        ):
            raise FreezeError(f"renderer classification {status} must be an array")
    _validate_strict_canonical_report(report, config)


def _render_section(title: str, value: Any) -> str:
    rendered = json.dumps(_thaw_value(value), sort_keys=True, indent=2)
    fence = chr(96) * 3
    return f"## {title}\n\n{fence}json\n{rendered}\n{fence}\n"


def _report_physical_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    statistical = cast(Mapping[str, Any], report["statistical"])
    graph = cast(Mapping[str, Any], report["graph"])
    return {
        "code_provenance": report["code_provenance"],
        "work_measurement": cast(Mapping[str, Any], report["work"])["measurement"],
        "retained_parent_paths": cast(Mapping[str, Any], report["retained_parents"])["paths"],
        "retained_role_bindings": cast(Mapping[str, Any], report["retained_parents"])[
            "role_bindings"
        ],
        "statistical_execution_receipts": {
            section: [
                {"id": item["id"], "execution_receipt": item["execution_receipt"]}
                for item in cast(Sequence[Any], statistical[section])
            ]
            for section in ("candidates", "simple_controls")
        },
        "graph_execution_receipts": {
            "controls": [
                {"id": item["id"], "execution_receipt": item["execution_receipt"]}
                for item in cast(Sequence[Any], graph["controls"])
            ],
            "tiny_graph": {
                "id": graph["tiny_learned_graph"]["id"],
                "execution_receipt": graph["tiny_learned_graph"]["execution_receipt"],
            },
        },
    }


def render_markdown(result: MicroRun, config: FreezeConfig) -> str:
    """Render the frozen canonical R3.H report as deterministic final Markdown."""

    report = result.report
    _validate_renderable_report(report, config)

    semantic_report = _report_semantic_payload(report)
    semantic_identity = canonical_report_semantic_identity(report)
    metadata = {
        "markdown_contract": "qtrad-r3-historical-exploratory-markdown-v1",
        "schema_version": report["schema_version"],
        "canonical_report_contract": report["contract"],
        "canonical_report_semantic_identity": semantic_identity,
        "stage": "R3.H",
        "evidence_class": "HISTORICAL_EXPLORATORY",
        "source_class": report["source_class"],
        "price_basis": report["price_basis"],
        "configuration_semantic_identity": config.semantic_identity,
        "no_post_result_expansion": report["no_post_result_expansion"],
    }
    claim_boundary = {
        "claims": semantic_report["claims"],
        "no_effectiveness_claim": True,
        "no_executable_alpha_claim": True,
        "no_profitability_claim": True,
        "no_native_validity_claim": True,
        "no_promotion_claim": True,
        "no_order_claim": True,
    }
    sections = [
        "# R3.H Historical Exploratory Report\n",
        "This is machine-readably labelled historical, MIDPOINT-only implementation evidence. "
        "It is not executable evidence or a recommendation.\n",
        _render_section("Machine-readable report identity", metadata),
        _render_section(
            "Terminal authority and consumed child identities",
            report["retained_parents"],
        ),
        _render_section(
            "Physical closure, execution, and resource provenance",
            _report_physical_payload(report),
        ),
        _render_section(
            "Frozen configuration and code identity",
            {
                "configuration_semantic_identity": config.semantic_identity,
                "code_provenance": report["code_provenance"],
                "report_contract": report["contract"],
            },
        ),
        _render_section(
            "Loader, selection, resources, and work counts",
            {
                "selection": report["selection"],
                "loader_contract": report["loader_contract"],
                "scale_projection": report["scale_projection"],
                "observation_contract": report["observation_contract"],
                "work": semantic_report["work"],
            },
        ),
        _render_section(
            "Economic break-even and turnover sensitivity", semantic_report["economic"]
        ),
        _render_section(
            "Chronological statistical and bounded nonlinear comparison",
            semantic_report["statistical"],
        ),
        _render_section("Tiny graph/GNN feasibility and controls", semantic_report["graph"]),
        _render_section(
            "Negative, failed, and inconclusive outcomes",
            semantic_report["result_classification"],
        ),
        _render_section("Claim boundary", claim_boundary),
    ]
    return "\n".join(sections)


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
    retained_forecasts_root = Path(
        "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts"
    )
    expected_forecast_paths = {
        "local_forecast": str(
            retained_forecasts_root
            / "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9.json"
        ),
        "pooled_forecast": str(
            retained_forecasts_root
            / "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json"
        ),
        "zero_forecast": str(
            retained_forecasts_root
            / "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json"
        ),
    }
    if any(retained_object[name] != path for name, path in expected_forecast_paths.items()):
        raise FreezeError("retained forecast role locators are reversed or not frozen")

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
    if not isinstance(loader.get("manifest_root"), str) or not loader["manifest_root"]:
        raise FreezeError("retained compact manifest root is not frozen")
    expected_streaming_policy = {
        "parts_first": True,
        "stop_after_selected_groups": False,
        "hash_consumed_parts": True,
        "max_source_rows": 3_376_258,
        "max_source_bytes": 2_147_483_648,
        "max_consumed_parts": 150,
        "expected_source_rows": 1_216_254,
        "expected_source_part_bytes": 373_175_647,
        "expected_source_parts": 150,
        "expected_largest_part_bytes": 4_198_824,
    }
    if loader["streaming_policy"] != expected_streaming_policy:
        raise FreezeError("retained no-order source inventory is not frozen")
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
        "POOLED_LOCAL_RIDGE": _CHILD_WRAPPER_SHA256["pooled_forecast"],
        "ZERO_RETURN": _CHILD_WRAPPER_SHA256["zero_forecast"],
    }
    if (
        bindings.get("dataset_ids") != expected_datasets
        or bindings.get("config_ids") != expected_configs
        or bindings.get("wrapper_sha256s") != expected_wrappers
    ):
        raise FreezeError("retained forecast role bindings are not frozen")
    child_wrappers_raw = loader.get("child_wrappers")
    if not isinstance(child_wrappers_raw, Mapping):
        raise FreezeError("retained child wrapper declarations are incomplete")
    child_wrappers = cast(Mapping[str, Any], child_wrappers_raw)
    if set(child_wrappers) != set(_CHILD_WRAPPER_NAMES):
        raise FreezeError("retained child wrapper declarations are incomplete")
    for name in _CHILD_WRAPPER_NAMES:
        declaration_raw = child_wrappers[name]
        declaration = cast(Mapping[str, Any], declaration_raw)
        if not isinstance(declaration_raw, Mapping):
            raise FreezeError(f"child wrapper declaration is not an object: {name}")
        is_partitioned = name not in {"selection", "consumed"}
        expected_keys = {"contract", "identity_field", "identity", "sha256", "required_keys"}
        if is_partitioned:
            expected_keys |= {
                "physical_required_keys",
                "manifest_relative_path",
                "partition_row_field",
                "partition_fields",
                "partition_mapping_fields",
            }
        if set(declaration) != expected_keys:
            raise FreezeError(f"child wrapper declaration schema mismatch: {name}")
        expected_field, expected_identity = _CHILD_WRAPPER_IDENTITY_FIELDS[name]
        if (
            declaration["contract"] != _CHILD_WRAPPER_CONTRACTS[name]
            or declaration["identity_field"] != expected_field
            or declaration["identity"] != expected_identity
            or declaration["sha256"] != _CHILD_WRAPPER_SHA256[name]
            or frozenset(cast(Sequence[str], declaration["required_keys"]))
            != _CHILD_WRAPPER_REQUIRED_KEYS[name]
        ):
            raise FreezeError(f"child wrapper declaration is not frozen: {name}")
        if is_partitioned:
            logical_row_field = "outcomes" if name == "outcome_evidence" else "rows"
            partition_fields = set(cast(Sequence[str], declaration["partition_fields"]))
            expected_physical = _CHILD_WRAPPER_REQUIRED_KEYS[name] - partition_fields | {
                "storage",
                "identity_field",
                "row_count",
                "parts",
                "header_sha256",
                "partition_row_field",
                "partition_fields",
                "partition_mapping_fields",
            }
            if (
                frozenset(cast(Sequence[str], declaration["physical_required_keys"]))
                != expected_physical
                or declaration["partition_row_field"] != logical_row_field
                or declaration["partition_mapping_fields"] != []
            ):
                raise FreezeError(f"child compact manifest declaration is not frozen: {name}")
            expected_partition_fields = (
                ["expected_target_ids", "source_row_ids", "outcomes"]
                if name == "outcome_evidence"
                else ["rows"]
            )
            if declaration["partition_fields"] != expected_partition_fields:
                raise FreezeError(f"child compact partition fields are not frozen: {name}")
            expected_relative = (
                "outcome-evidence.json"
                if name == "outcome_evidence"
                else f"forecasts/{declaration['identity']}.json"
            )
            if declaration["manifest_relative_path"] != expected_relative:
                raise FreezeError(f"child compact manifest path is not frozen: {name}")

    statistical = raw["statistical_formulations"]
    if not isinstance(statistical, dict):
        raise FreezeError("statistical_formulations must be an object")
    statistical_object = cast(dict[str, Any], statistical)
    _reject_unknown(statistical_object, _STATISTICAL_KEYS, "statistical_formulations")
    if set(statistical_object) != set(_STATISTICAL_KEYS):
        raise FreezeError("statistical formulations are incomplete")
    expected_control_descriptors = [
        {
            "id": "zero_return",
            "kind": "constant_zero",
            "candidate_id": "linear_zero_return",
            "fit_policy": "none",
        },
        {
            "id": "local_ridge",
            "kind": "local_ridge",
            "candidate_id": "linear_ridge",
            "fit_policy": "chronological_oof",
        },
        {
            "id": "pooled_local_ridge",
            "kind": "pooled_ridge",
            "candidate_id": "linear_ridge",
            "fit_policy": "chronological_oof",
        },
    ]
    if statistical_object["controls"] != [item["id"] for item in expected_control_descriptors]:
        raise FreezeError("frozen control IDs differ from declarations")
    if statistical_object["control_descriptors"] != expected_control_descriptors:
        raise FreezeError("frozen control declarations are not explicit")
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
            forecasts / "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json"
        ),
        "zero_forecast": str(
            forecasts / "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json"
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
            if bool(policy["reject_incomplete"]):
                raise FreezeError(f"incomplete canonical decision group: {decision_time}")
            continue
        selected_times.append(decision_time)
        if len(selected_times) == int(policy["n_complete_decision_groups"]):
            break
    if len(selected_times) != int(policy["n_complete_decision_groups"]):
        raise FreezeError(
            "fewer than the frozen number of complete decision groups; incomplete groups remain"
        )
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
        "stop_state": "SCANNED_ALL_ROWS_REQUIRED_NO_ORDER_PROOF",
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


def _declared_partition_paths(
    root: Path,
    manifest_relative_path: str,
    manifest: Mapping[str, Any],
    *,
    identity_field: str,
) -> list[str]:
    """Validate the R2 compact part tree without importing runtime from application."""
    relative_manifest = PurePosixPath(manifest_relative_path)
    if (
        relative_manifest.is_absolute()
        or ".." in relative_manifest.parts
        or str(relative_manifest) != manifest_relative_path
        or root.is_symlink()
    ):
        raise ValueError("unsafe manifest path")
    root_resolved = root.resolve(strict=True)
    references_value: object = manifest["parts"]
    total_rows_value: object = manifest["row_count"]
    if (
        not isinstance(references_value, list)
        or not isinstance(total_rows_value, int)
        or isinstance(total_rows_value, bool)
        or total_rows_value < 0
    ):
        raise ValueError("malformed compact manifest parts")
    references = cast(list[object], references_value)
    total_rows = total_rows_value
    part_prefix = f"{manifest_relative_path}.parts"
    declared: list[str] = []
    expected_rows = 0
    for expected_index, reference_value in enumerate(references):
        if not isinstance(reference_value, Mapping):
            raise ValueError("malformed compact part reference")
        reference = cast(Mapping[str, object], reference_value)
        if set(reference) != {
            "path",
            "sha256",
            "row_count",
            "part_index",
        }:
            raise ValueError("malformed compact part reference")
        relative = reference["path"]
        row_count = reference["row_count"]
        digest = reference["sha256"]
        if (
            not isinstance(relative, str)
            or not isinstance(row_count, int)
            or isinstance(row_count, bool)
            or row_count <= 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or reference["part_index"] != expected_index
            or relative != f"{part_prefix}/part-{expected_index:06d}.json"
        ):
            raise ValueError("non-canonical compact part reference")
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("unsafe compact part path")
        candidate = root.joinpath(*relative_path.parts)
        current = root
        for component in relative_path.parts:
            current = current / component
            if current.is_symlink():
                raise ValueError("symlink in compact part path")
        if not candidate.resolve(strict=False).is_relative_to(root_resolved):
            raise ValueError("compact part escapes root")
        if candidate.stat().st_size > _MAX_PART_BYTES:
            raise ValueError(f"compact part exceeds the 64 MiB limit: {relative}")
        declared.append(relative)
        expected_rows += row_count
    if expected_rows != total_rows:
        raise ValueError("compact part row count mismatch")
    if total_rows and not references:
        raise ValueError("compact manifest has rows but no parts")
    if not total_rows and references:
        raise ValueError("compact empty manifest must not declare parts")
    if manifest.get("identity_field") != identity_field:
        raise ValueError("compact manifest identity field mismatch")
    part_root = root.joinpath(*PurePosixPath(part_prefix).parts)
    if not part_root.is_dir() or part_root.is_symlink():
        if declared:
            raise ValueError("compact part directory is missing or unsafe")
        return declared
    for entry in part_root.rglob("*"):
        if entry.is_symlink() or not entry.is_file():
            raise ValueError("compact part tree contains an unsafe entry")
        if entry.relative_to(root).as_posix() not in declared:
            raise ValueError("compact part tree contains an undeclared file")
    return declared


def _open_partitioned_json_document(
    path: Path, limits: Mapping[str, Any]
) -> tuple[dict[str, Any], Iterator[tuple[dict[str, Any], list[Mapping[str, Any]], int]], int, str]:
    """Open one authoritative R2 compact manifest without materialising its parts."""
    root = Path(cast(str, limits["manifest_root"]))
    manifest_relative = cast(str, limits["manifest_relative_path"])
    if path != root / PurePosixPath(manifest_relative):
        raise FreezeError("retained manifest path differs from frozen preparation root")
    if not path.is_file() or path.is_symlink():
        raise FreezeError("retained compact manifest is missing or unsafe")
    raw_bytes = path.read_bytes()
    if len(raw_bytes) > int(limits["max_source_bytes"]):
        raise FreezeError("retained child exceeds frozen source-byte bound")
    wrapper_hash = hashlib.sha256(raw_bytes).hexdigest()
    expected_wrapper_hash = limits.get("expected_wrapper_sha256")
    if expected_wrapper_hash is not None and wrapper_hash != expected_wrapper_hash:
        raise FreezeError("retained wrapper byte hash mismatch")
    try:
        payload: object = cast(object, json.loads(raw_bytes))
    except json.JSONDecodeError as exc:
        raise FreezeError("retained compact manifest is not valid JSON") from exc
    if (
        not isinstance(payload, dict)
        or _canonical_bytes(cast(dict[str, Any], payload)) != raw_bytes
    ):
        raise FreezeError("retained compact manifest is not canonical JSON")
    metadata = cast(dict[str, Any], payload)
    required_keys = set(cast(Sequence[str], limits["physical_required_keys"]))
    if set(metadata) != required_keys:
        raise FreezeError("retained compact manifest fields are incomplete")
    if metadata.get("storage") != _PARTITIONED_ROWS_STORAGE:
        raise FreezeError("retained compact manifest storage differs from R2 contract")
    identity_field = cast(str, limits["expected_identity_field"])
    if metadata.get("contract") != limits["expected_wrapper_contract"]:
        raise FreezeError("retained wrapper contract mismatch")
    expected_wrapper_identity = limits.get("expected_wrapper_identity")
    if (
        expected_wrapper_identity is not None
        and metadata.get(identity_field) != expected_wrapper_identity
    ):
        raise FreezeError("retained wrapper identity mismatch")
    if metadata.get("identity_field") != identity_field:
        raise FreezeError("retained compact manifest identity field mismatch")
    physical_fields = {"storage", "identity_field", "row_count", "parts", "header_sha256"}
    header = {key: value for key, value in metadata.items() if key not in physical_fields}
    if metadata.get("header_sha256") != hashlib.sha256(_canonical_bytes(header)).hexdigest():
        raise FreezeError("retained compact manifest header digest mismatch")
    if metadata.get("partition_row_field") != limits["partition_row_field"]:
        raise FreezeError("retained compact manifest row register mismatch")
    if tuple(cast(Sequence[str], metadata.get("partition_fields"))) != tuple(
        cast(Sequence[str], limits["partition_fields"])
    ):
        raise FreezeError("retained compact manifest field register mismatch")
    if tuple(cast(Sequence[str], metadata.get("partition_mapping_fields"))) != tuple(
        cast(Sequence[str], limits["partition_mapping_fields"])
    ):
        raise FreezeError("retained compact manifest mapping register mismatch")
    references_value = metadata.get("parts")
    if not isinstance(references_value, list):
        raise FreezeError("retained compact manifest parts are malformed")
    references = cast(list[Mapping[str, Any]], references_value)
    try:
        declared_paths = _declared_partition_paths(
            root, manifest_relative, metadata, identity_field=identity_field
        )
    except (OSError, ValueError) as exc:
        raise FreezeError("retained compact manifest part declarations are invalid") from exc
    if len(declared_paths) > int(limits["max_consumed_parts"]):
        raise FreezeError("retained compact manifest exceeds frozen part bound")
    expected_record_keys = cast(Sequence[str], limits["required_record_keys"])
    outcome_tags = set(cast(Sequence[str], limits.get("partition_fields", ())))

    def iter_parts() -> Iterator[tuple[dict[str, Any], list[Mapping[str, Any]], int]]:
        started = time.monotonic()
        for expected_index, (reference_raw, relative) in enumerate(
            zip(references, declared_paths, strict=True)
        ):
            if time.monotonic() - started > float(limits["max_elapsed_seconds"]):
                raise FreezeError("retained child exceeds elapsed-time bound")
            reference = reference_raw
            if set(reference) != {"path", "sha256", "row_count", "part_index"}:
                raise FreezeError("retained compact part reference fields are incomplete")
            if reference["path"] != relative or reference["part_index"] != expected_index:
                raise FreezeError("retained compact part reference is non-canonical")
            part_path = root / PurePosixPath(relative)
            if not part_path.is_file() or part_path.is_symlink():
                raise FreezeError("retained compact part is missing or unsafe")
            try:
                part_size = part_path.stat().st_size
            except OSError as exc:
                raise FreezeError("retained compact part is missing or unsafe") from exc
            if part_size > _MAX_PART_BYTES:
                raise FreezeError("retained compact part exceeds the 64 MiB limit")
            part_bytes = part_path.read_bytes()
            if hashlib.sha256(part_bytes).hexdigest() != reference["sha256"]:
                raise FreezeError("retained compact part byte hash mismatch")
            try:
                envelope_value: object = cast(object, json.loads(part_bytes))
            except json.JSONDecodeError as exc:
                raise FreezeError("retained compact part is not valid JSON") from exc
            if (
                not isinstance(envelope_value, dict)
                or _canonical_bytes(cast(dict[str, Any], envelope_value)) != part_bytes
            ):
                raise FreezeError("retained compact part is not canonical JSON")
            envelope = cast(dict[str, Any], envelope_value)
            envelope_keys = {
                "contract",
                "schema_version",
                "parent_contract",
                "parent_semantic_id",
                "part_index",
                "rows",
            }
            if set(envelope) != envelope_keys:
                raise FreezeError("retained compact part envelope fields are incomplete")
            if (
                envelope["contract"] != "qtrad-r2-partitioned-json-row-part-v1"
                or envelope["schema_version"] != 1
                or envelope["parent_contract"] != metadata["contract"]
                or envelope["parent_semantic_id"] != metadata[identity_field]
                or envelope["part_index"] != expected_index
            ):
                raise FreezeError("retained compact part lineage mismatch")
            physical_rows_value = envelope["rows"]
            if not isinstance(physical_rows_value, list):
                raise FreezeError("retained compact part row count mismatch")
            physical_rows = cast(list[object], physical_rows_value)
            if len(physical_rows) != reference["row_count"]:
                raise FreezeError("retained compact part row count mismatch")
            if len(physical_rows) > int(limits["max_part_rows"]):
                raise FreezeError("retained compact part exceeds row bound")
            logical_rows: list[Mapping[str, Any]] = []
            for physical_row in physical_rows:
                if not isinstance(physical_row, Mapping):
                    raise FreezeError("retained compact part row must be an object")
                physical = cast(Mapping[str, Any], physical_row)
                if outcome_tags == {"expected_target_ids", "source_row_ids", "outcomes"}:
                    if set(physical) != {"field", "value"} or physical["field"] not in outcome_tags:
                        raise FreezeError("retained outcome partition row is malformed")
                    if physical["field"] != "outcomes":
                        continue
                    value = physical["value"]
                else:
                    if set(physical) != {"value"}:
                        raise FreezeError("retained forecast partition row is malformed")
                    value = physical["value"]
                if not isinstance(value, Mapping):
                    raise FreezeError("retained logical row is malformed")
                logical_rows.append(dict(cast(Mapping[str, Any], value)))
            _validate_part_rows(
                logical_rows, {**limits, "required_record_keys": expected_record_keys}
            )
            yield (
                {
                    "path": relative,
                    "sha256": reference["sha256"],
                    "rows": len(logical_rows),
                    "physical_rows": len(physical_rows),
                    "bytes": len(part_bytes),
                    "part_index": expected_index,
                },
                logical_rows,
                len(part_bytes),
            )

    return metadata, iter_parts(), len(raw_bytes), wrapper_hash


def _open_json_document(
    path: Path, limits: Mapping[str, Any]
) -> tuple[dict[str, Any], Iterator[tuple[dict[str, Any], list[Mapping[str, Any]], int]], int, str]:
    """Validate a wrapper immediately, then decode one declared part per iteration."""
    if "physical_required_keys" in limits:
        return _open_partitioned_json_document(path, limits)
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
    if not isinstance(payload, dict):
        if not isinstance(payload, list):
            raise FreezeError("retained child rows must be an array")
        metadata: dict[str, Any] = {}
        parts_value: Any = None
        inline_rows: list[Any] = cast(list[Any], payload)
    else:
        payload_object = cast(dict[str, Any], payload)
        metadata = dict(payload_object)
        expected_contract = limits.get("expected_wrapper_contract")
        if expected_contract is not None and payload_object.get("contract") != expected_contract:
            raise FreezeError("retained wrapper contract mismatch")
        expected_identity = limits.get("expected_wrapper_identity")
        if expected_identity is not None:
            identity_field = str(limits.get("expected_identity_field", "identity"))
            if payload_object.get(identity_field) != expected_identity:
                raise FreezeError("retained wrapper identity mismatch")
        parts_value = payload_object.get("parts")
        inline_rows_value = payload_object.get("rows")
        if parts_value is None:
            inline_rows = (
                [payload_object]
                if inline_rows_value is None
                else cast(list[Any], inline_rows_value)
            )
        else:
            inline_rows = []
    started = time.monotonic()
    source_rows = 0
    source_bytes = size
    source_parts = 0

    def iter_parts() -> Iterator[tuple[dict[str, Any], list[Mapping[str, Any]], int]]:
        nonlocal source_rows, source_bytes, source_parts
        if parts_value is None:
            part_rows = inline_rows
            descriptor = {
                "locator": str(path),
                "sha256": wrapper_hash,
                "rows": len(part_rows),
                "bytes": size,
            }
            _validate_part_rows(part_rows, limits)
            source_rows = len(part_rows)
            source_parts = 1
            yield descriptor, [cast(Mapping[str, Any], row) for row in part_rows], size
            return
        if not isinstance(parts_value, list) or not parts_value:
            raise FreezeError("retained wrapper must declare non-empty parts")
        required_descriptor_keys = {
            "locator",
            "contract",
            "identity",
            "sha256",
            "byte_size",
            "row_count",
        }
        for part_raw in cast(list[Any], parts_value):
            if time.monotonic() - started > float(limits.get("max_elapsed_seconds", 1e12)):
                raise FreezeError("retained child exceeds elapsed-time bound")
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
            source_bytes += part_size
            source_parts += 1
            if source_bytes > int(limits["max_source_bytes"]):
                raise FreezeError("retained child exceeds source-byte bound")
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
            _validate_part_rows(part_rows, limits)
            source_rows += len(part_rows)
            if source_rows > int(limits["max_source_rows"]):
                raise FreezeError("retained child exceeds frozen source-row bound")
            part_info = {
                "locator": locator_value,
                "sha256": actual_hash,
                "rows": len(part_rows),
                "bytes": part_size,
                "contract": descriptor["contract"],
                "identity": descriptor["identity"],
            }
            yield part_info, [cast(Mapping[str, Any], row) for row in part_rows], part_size

    return metadata, iter_parts(), size, wrapper_hash


def _validate_part_rows(rows: Sequence[Any], limits: Mapping[str, Any]) -> None:
    required_record_keys = limits.get("required_record_keys")
    for record_raw in rows:
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


def _read_json_document(
    path: Path, limits: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Mapping[str, Any]], int]:
    """Compatibility collector; retained loading uses the streaming iterator directly."""
    metadata, parts, size, _wrapper_hash = _open_json_document(path, limits)
    records: list[Mapping[str, Any]] = []
    consumed_parts: list[dict[str, Any]] = []
    rows = 0
    bytes_read = size
    for descriptor, part_rows, part_size in parts:
        records.extend(part_rows)
        consumed_parts.append(descriptor)
        rows += len(part_rows)
        bytes_read += part_size
    metadata["consumed_parts"] = consumed_parts
    metadata["source_scan_rows"] = rows
    metadata["source_scan_bytes"] = bytes_read
    metadata["source_scan_parts"] = len(consumed_parts)
    return metadata, records, size


def _read_json_records(  # pyright: ignore[reportUnusedFunction]
    path: Path, limits: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    return _read_json_document(path, limits)[1]


def _validate_child_metadata(
    name: str,
    metadata: Mapping[str, Any],
    loader: Mapping[str, Any],
    *,
    fixture: bool = False,
) -> None:
    if not metadata:
        raise FreezeError(f"retained child metadata is missing: {name}")
    declarations = cast(Mapping[str, Any], loader["child_wrappers"])
    declaration = cast(Mapping[str, Any], declarations[name])
    if metadata.get("contract") != declaration["contract"]:
        raise FreezeError(f"{name} child contract mismatch")
    identity_field = str(declaration["identity_field"])
    if not fixture and metadata.get(identity_field) != declaration["identity"]:
        raise FreezeError(f"{name} child identity mismatch")
    required = set(cast(Sequence[str], declaration["required_keys"]))
    actual = set(metadata)
    if "physical_required_keys" in declaration:
        physical_required = set(cast(Sequence[str], declaration["physical_required_keys"]))
        if actual != physical_required:
            raise FreezeError(f"{name} compact manifest fields are incomplete")
        return
    if actual != required:
        raise FreezeError(f"{name} child metadata fields are incomplete")
    if name in {"selection", "consumed"} and "parts" in metadata:
        raise FreezeError(f"{name} marker must not declare data parts")


def _validate_declared_source_inventory(
    streaming: Mapping[str, Any], manifests: Mapping[str, Mapping[str, Any]]
) -> None:
    declared_rows = sum(int(manifest["row_count"]) for manifest in manifests.values())
    declared_parts = sum(
        len(cast(Sequence[Any], manifest["parts"])) for manifest in manifests.values()
    )
    if (
        declared_rows != int(streaming["expected_source_rows"])
        or declared_parts != int(streaming["expected_source_parts"])
        or declared_parts > int(streaming["max_consumed_parts"])
    ):
        raise FreezeError("retained declared source inventory differs from freeze")


def _validate_scanned_source_inventory(
    streaming: Mapping[str, Any], receipts: Mapping[str, Sequence[Mapping[str, Any]]]
) -> None:
    scanned_parts = sum(len(parts) for parts in receipts.values())
    scanned_rows = sum(int(part["physical_rows"]) for parts in receipts.values() for part in parts)
    scanned_bytes = sum(int(part["bytes"]) for parts in receipts.values() for part in parts)
    largest_part = max(
        (int(part["bytes"]) for parts in receipts.values() for part in parts), default=0
    )
    if (
        scanned_rows != int(streaming["expected_source_rows"])
        or scanned_parts != int(streaming["expected_source_parts"])
        or scanned_bytes != int(streaming["expected_source_part_bytes"])
        or largest_part != int(streaming["expected_largest_part_bytes"])
    ):
        raise FreezeError("retained scanned source inventory differs from freeze")


def load_retained_rows(
    config: FreezeConfig, *, locators: Mapping[str, str] | None = None, _fixture: bool = False
) -> tuple[tuple[FixtureRow, ...], dict[str, Any]]:
    """Stream frozen children through a bounded, outcome-blind join selector."""
    authority = (
        {
            "authentication_performed": False,
            "contract": config.document["terminal_authentication"]["contract"],
            "state": "FIXTURE_INJECTED",
            "verdict": "NOT_AUTHENTICATED",
        }
        if _fixture
        else authenticate_terminal_authority(config)
    )
    loader = cast(Mapping[str, Any], config.document["retained_loader"])
    expected_locators = cast(Mapping[str, str], loader["locators"])
    actual_locators = expected_locators if locators is None else locators
    if not _fixture and dict(actual_locators) != dict(expected_locators):
        raise FreezeError("retained loader locator differs from frozen terminal child")
    decoder_limits = cast(Mapping[str, Any], loader["decoder_limits"])
    streaming = cast(Mapping[str, Any], loader["streaming_policy"])
    mappings = cast(Mapping[str, str], loader["field_mappings"])
    required = set(cast(Sequence[str], loader["required_columns"]))
    expected_fields = {mappings[field] for field in required}
    child_names = tuple(_CHILD_WRAPPER_NAMES)
    wrappers = cast(Mapping[str, Any], loader["child_wrappers"])
    limits = dict(decoder_limits)
    limits.update(
        {
            "max_source_rows": streaming["max_source_rows"],
            "max_source_bytes": streaming["max_source_bytes"],
            "max_elapsed_seconds": streaming.get("max_elapsed_seconds", 1e12),
            "max_consumed_parts": streaming["max_consumed_parts"],
        }
    )
    opened: dict[
        str,
        tuple[
            dict[str, Any],
            Iterator[tuple[dict[str, Any], list[Mapping[str, Any]], int]],
            int,
            str,
        ],
    ] = {}
    for name in child_names:
        declaration = cast(Mapping[str, Any], wrappers[name])
        child_limits = dict(limits)
        physical = "physical_required_keys" in declaration
        child_limits.update(
            {
                **(
                    {}
                    if _fixture
                    else {
                        "expected_wrapper_sha256": declaration["sha256"],
                        "expected_wrapper_identity": declaration["identity"],
                    }
                ),
                "expected_wrapper_contract": declaration["contract"],
                "expected_identity_field": declaration["identity_field"],
                "required_record_keys": (
                    expected_fields
                    if name not in {"selection", "consumed"}
                    else declaration["required_keys"]
                ),
                **(
                    {
                        "physical_required_keys": declaration["physical_required_keys"],
                        "manifest_relative_path": declaration["manifest_relative_path"],
                        "manifest_root": str(
                            Path(actual_locators["selection"]).parent
                            if _fixture
                            else Path(cast(str, loader["manifest_root"]))
                        ),
                        "partition_row_field": declaration["partition_row_field"],
                        "partition_fields": declaration["partition_fields"],
                        "partition_mapping_fields": declaration["partition_mapping_fields"],
                    }
                    if physical
                    else {}
                ),
            }
        )
        opened[name] = _open_json_document(Path(actual_locators[name]), child_limits)
        _validate_child_metadata(name, opened[name][0], loader, fixture=_fixture)
    if not _fixture:
        _validate_declared_source_inventory(
            streaming,
            {name: opened[name][0] for name in _CHILD_WRAPPER_NAMES[2:]},
        )
    _selection_metadata, selection_parts, _selection_size, _selection_hash = opened["selection"]
    _consumed_metadata, consumed_parts, _consumed_size, _consumed_hash = opened["consumed"]
    consumed_part_receipts: dict[str, list[dict[str, Any]]] = {name: [] for name in child_names}
    selection_records: list[Mapping[str, Any]] = []
    consumed_records: list[Mapping[str, Any]] = []
    source_rows = 0
    source_bytes = sum(opened[name][2] for name in child_names)
    source_parts = 0
    for descriptor, part_rows, _ in selection_parts:
        consumed_part_receipts["selection"].append(descriptor)
        selection_records.extend(part_rows)
        source_rows += int(descriptor.get("physical_rows", len(part_rows)))
    for descriptor, part_rows, _ in consumed_parts:
        consumed_part_receipts["consumed"].append(descriptor)
        consumed_records.extend(part_rows)
        source_rows += int(descriptor.get("physical_rows", len(part_rows)))
    if source_rows > int(streaming["max_source_rows"]) or source_bytes > int(
        streaming["max_source_bytes"]
    ):
        raise FreezeError("retained aggregate marker bounds exceeded")
    if len(selection_records) != 1 or len(consumed_records) != 1:
        raise FreezeError("selection and consumed markers must be single objects")
    selection = selection_records[0]
    consumed = consumed_records[0]
    if consumed.get("state") != "CONSUMED":
        raise FreezeError("retained lifecycle marker is not terminal")
    if (
        not _fixture
        and selection.get("manifest_id")
        != cast(Mapping[str, Any], wrappers["selection"])["identity"]
    ):
        raise FreezeError("selection manifest identity mismatch")
    if (
        not _fixture
        and consumed.get("marker_id") != cast(Mapping[str, Any], wrappers["consumed"])["identity"]
    ):
        raise FreezeError("consumed marker identity mismatch")
    if consumed.get("selection_manifest_id") != selection.get("manifest_id"):
        raise FreezeError("consumed selection identity mismatch")
    policy = cast(Mapping[str, Any], loader["selection_policy"])
    max_rows = int(decoder_limits["max_selected_rows"])
    target_ids = cast(Sequence[str], policy["required_target_ids"])
    max_groups = max(1, max_rows // max(1, len(target_ids)))
    required_groups = int(policy["n_complete_decision_groups"])
    groups: dict[str, dict[tuple[Any, ...], dict[str, Mapping[str, Any]]]] = {}
    seen_by_child: dict[str, set[tuple[Any, ...]]] = {name: set() for name in child_names[2:]}
    started = time.monotonic()
    data_names = tuple(child_names[2:])
    part_iterators = {name: iter(opened[name][1]) for name in data_names}

    def consume_part(
        name: str,
        descriptor: Mapping[str, Any],
        part_rows: Sequence[Mapping[str, Any]],
        part_size: int,
    ) -> None:
        nonlocal source_parts, source_bytes, source_rows
        consumed_part_receipts[name].append(dict(descriptor))
        source_parts += 1
        source_bytes += part_size
        source_rows += int(descriptor.get("physical_rows", len(part_rows)))
        if source_parts > int(streaming["max_consumed_parts"]):
            raise FreezeError("retained aggregate part bound exceeded")
        if source_rows > int(streaming["max_source_rows"]):
            raise FreezeError("retained aggregate row bound exceeded")
        if source_bytes > int(streaming["max_source_bytes"]):
            raise FreezeError("retained aggregate byte bound exceeded")
        if time.monotonic() - started > float(streaming.get("max_elapsed_seconds", 1e12)):
            raise FreezeError("retained aggregate elapsed-time bound exceeded")
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 > float(
            config.document["compute_limits"]["max_memory_mb"]
        ):
            raise FreezeError("retained aggregate memory bound exceeded")
        for record in part_rows:
            key = _canonical_join_key(record, mappings)
            if key in seen_by_child[name]:
                raise FreezeError(f"duplicate canonical identity in {name}")
            seen_by_child[name].add(key)
            decision_time = str(record[mappings["decision_time"]])
            group = groups.get(decision_time)
            if group is None:
                if len(groups) >= max_groups:
                    raise FreezeError(
                        "bounded join selector exhausted before proving complete groups"
                    )
                group = {}
                groups[decision_time] = group
            children = group.setdefault(key, {})
            if name in children:
                raise FreezeError(f"duplicate canonical identity in {name}")
            children[name] = record

    def complete_groups() -> tuple[
        list[tuple[str, dict[tuple[Any, ...], dict[str, Mapping[str, Any]]]]],
        list[str],
    ]:
        complete: list[tuple[str, dict[tuple[Any, ...], dict[str, Mapping[str, Any]]]]] = []
        incomplete: list[str] = []
        for decision_time, group in sorted(groups.items()):
            if len(group) != len(target_ids) or any(
                set(children) != set(data_names) for children in group.values()
            ):
                incomplete.append(decision_time)
                continue
            complete.append((decision_time, group))
        return complete, incomplete

    stop_state = "SCANNED_ALL_PARTS_REQUIRED_NO_ORDER_PROOF"
    stop_reason = "FULL_SCAN_REQUIRED_NO_ORDER_PROOF"
    unopened_parts = {name: "EXHAUSTED" for name in data_names}
    while True:
        part_set: dict[str, tuple[dict[str, Any], list[Mapping[str, Any]], int]] = {}
        for name in data_names:
            try:
                part_set[name] = next(part_iterators[name])
            except StopIteration:
                unopened_parts[name] = "EXHAUSTED"
        if not part_set:
            break
        for name in data_names:
            if name in part_set:
                descriptor, part_rows, part_size = part_set[name]
                consume_part(name, descriptor, part_rows, part_size)

    if not _fixture:
        _validate_scanned_source_inventory(
            streaming,
            {name: consumed_part_receipts[name] for name in data_names},
        )

    complete, incomplete = complete_groups()
    if incomplete and bool(policy["reject_incomplete"]):
        raise FreezeError(
            "incomplete canonical retained decision group in scanned prefix: " + incomplete[0]
        )
    if len(complete) < required_groups:
        raise FreezeError("bounded join selector could not prove required complete groups")
    rows: list[FixtureRow] = []
    role_names = ("local_forecast", "pooled_forecast", "zero_forecast")
    role_predictions: dict[str, list[float]] = {name: [] for name in role_names}
    for _, group in complete[:required_groups]:
        for key in sorted(group):
            children = group[key]
            local = children["local_forecast"]
            outcome = children["outcome_evidence"]
            for role_name in role_names:
                role_predictions[role_name].append(
                    float(children[role_name][mappings["prediction"]])
                )
            rows.append(
                FixtureRow(
                    **{field: local[mappings[field]] for field in required}
                    | {
                        "timestamp": local[mappings["decision_time"]],
                        "realised_return": outcome[mappings["realised_return"]],
                    }
                )
            )
    selected, selection_meta = select_synchronised_rows(rows, config)
    selection_meta["stop_state"] = stop_state
    selection_meta["stop_reason"] = stop_reason
    all_parts = [part for name in data_names for part in consumed_part_receipts[name]]
    scanned_hashes = [str(part["sha256"]) for part in all_parts]
    wrapper_bytes = {name: opened[name][2] for name in child_names}
    wrapper_hashes = {name: opened[name][3] for name in child_names}
    part_bytes = {
        name: sum(int(part.get("bytes", 0)) for part in consumed_part_receipts[name])
        for name in data_names
    }
    part_hashes = {
        name: [str(part["sha256"]) for part in consumed_part_receipts[name]] for name in data_names
    }
    selected_row_bytes = sum(
        len(
            json.dumps(
                {name: getattr(row, name) for name in FixtureRow.__dataclass_fields__},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        for row in selected
    )
    role_wrapper_sha = {
        "LOCAL_RIDGE": cast(Mapping[str, Any], wrappers["local_forecast"])["sha256"],
        "POOLED_LOCAL_RIDGE": cast(Mapping[str, Any], wrappers["pooled_forecast"])["sha256"],
        "ZERO_RETURN": cast(Mapping[str, Any], wrappers["zero_forecast"])["sha256"],
    }
    role_bindings = {
        role: {
            "dataset_id": cast(Mapping[str, Any], loader["identity_bindings"])["dataset_ids"][role],
            "config_id": cast(Mapping[str, Any], loader["identity_bindings"])["config_ids"][role],
            "wrapper_sha256": role_wrapper_sha[role],
        }
        for role in ("LOCAL_RIDGE", "POOLED_LOCAL_RIDGE", "ZERO_RETURN")
    }
    return selected, {
        "authority": authority,
        "selection": selection_meta,
        "source_scan_rows": source_rows,
        "source_scan_bytes": source_bytes,
        "source_scan_parts": source_parts,
        "source_scan_wrapper_bytes": wrapper_bytes,
        "source_scan_wrapper_hashes": wrapper_hashes,
        "source_scan_part_bytes": part_bytes,
        "source_scan_part_hashes": part_hashes,
        "consumed_parts": consumed_part_receipts,
        "consumed_rows": source_rows,
        "consumed_bytes": source_bytes,
        "consumed_parts_count": source_parts,
        "selected_rows": len(selected),
        "selected_bytes": selected_row_bytes,
        "selected_bytes_kind": "logical_serialised_fixture_row_bytes",
        "selected_groups": required_groups,
        "selected_raw_part_provenance": "not individually attributable after bounded join",
        "role_bindings": role_bindings,
        "_role_predictions": role_predictions,
        "scanned_part_hashes": scanned_hashes,
        "stop_state": stop_state,
        "stop_reason": stop_reason,
        "unopened_parts": unopened_parts,
        "outcome_decode_performed": not _fixture,
    }


def load_fixture_rows(
    rows: Sequence[FixtureRow], config: FreezeConfig
) -> tuple[tuple[FixtureRow, ...], dict[str, Any]]:
    """Inject six distinct wrapper/part documents through the retained streaming boundary."""
    if len(rows) > int(config.document["compute_limits"]["max_rows"]):
        raise FreezeError("fixture source exceeds frozen row bound")
    loader = cast(Mapping[str, Any], config.document["retained_loader"])
    mappings = cast(Mapping[str, str], loader["field_mappings"])
    expected_fields = set(mappings.values())
    fixture_fields = set(FixtureRow.__dataclass_fields__)
    if not expected_fields <= fixture_fields:
        raise FreezeError("fixture fields do not match frozen retained mapping")
    records = [{field: getattr(row, field) for field in expected_fields} for row in rows]
    with tempfile.TemporaryDirectory(prefix="qtrad-r3-h-fixture-") as temporary:
        root = Path(temporary)
        locators: dict[str, str] = {}
        declarations = cast(Mapping[str, Any], loader["child_wrappers"])

        def metadata_for(name: str) -> dict[str, Any]:
            declaration = cast(Mapping[str, Any], declarations[name])
            values: dict[str, Any] = {
                key: None for key in cast(Sequence[str], declaration["required_keys"])
            }
            values["contract"] = declaration["contract"]
            values["schema_version"] = 1
            values[cast(str, declaration["identity_field"])] = hashlib.sha256(
                name.encode()
            ).hexdigest()
            if name == "consumed" and "selection_manifest_id" in values:
                values["selection_manifest_id"] = hashlib.sha256(b"selection").hexdigest()
            for key in values:
                if key.endswith("_ids") or key in {
                    "questions",
                    "comparator_families",
                    "runtime_identities",
                    "frozen_metadata",
                    "rows",
                    "outcomes",
                    "source_row_ids",
                }:
                    values[key] = []
            if "rows" in values:
                values["rows"] = len(records)
            for key, value in (
                ("source_class", "FIXTURE"),
                ("evidence_class", "FIXTURE"),
                ("holdout_scope", "FIXTURE"),
                ("state", "CONSUMED" if name == "consumed" else "SEALED_UNOPENED"),
            ):
                if key in values:
                    values[key] = value
            if "outcome_accessed" in values:
                values["outcome_accessed"] = False
            if "holdout_outcomes_accessed" in values:
                values["holdout_outcomes_accessed"] = False
            if "frozen_at" in values:
                values["frozen_at"] = "1970-01-01T00:00:00Z"
            if "frozen_by" in values:
                values["frozen_by"] = "fixture"
            if "experiment_count" in values:
                values["experiment_count"] = 1
            return values

        for name in ("selection", "consumed"):
            path = root / f"{name}.json"
            path.write_text(
                json.dumps(metadata_for(name), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            locators[name] = str(path)
        decision_field = mappings["decision_time"]
        decision_times = sorted({str(record[decision_field]) for record in records})
        if len(decision_times) != 3:
            raise FreezeError("fixture requires exactly three decision-time groups")
        part_records = [
            [record for record in records if str(record[decision_field]) == decision_time]
            for decision_time in decision_times
        ]
        part_records = [part_records[0] + part_records[1], part_records[2]]
        period_field = mappings["period"]
        if any(str(record[period_field]).endswith("-fixture-late-earlier") for record in records):
            late_records = [dict(record) for record in part_records[-1]]
            for record in late_records:
                record[decision_field] = "1969-01-01T00:00:00Z"
                record[mappings["available_at"]] = "1968-12-31T23:55:00Z"
                record[mappings["target_available_at"]] = "1969-01-01T00:05:00Z"
                record[mappings["dependency_start"]] = "1969-01-01T00:00:00Z"
                record[mappings["dependency_end"]] = "1969-01-01T00:05:00Z"
            part_records.append(late_records)
        for name in ("local_forecast", "pooled_forecast", "zero_forecast", "outcome_evidence"):
            declaration = cast(Mapping[str, Any], declarations[name])
            wrapper = metadata_for(name)
            for partition_field in cast(Sequence[str], declaration["partition_fields"]):
                wrapper.pop(partition_field, None)
            row_field = cast(str, declaration["partition_row_field"])
            wrapper["partition_row_field"] = row_field
            wrapper["partition_fields"] = declaration["partition_fields"]
            wrapper["partition_mapping_fields"] = declaration["partition_mapping_fields"]
            child_part_records = part_records
            if name == "outcome_evidence":
                middle_records = part_records[1]
                child_part_records = [
                    part_records[0],
                    middle_records[: len(middle_records) // 2],
                    middle_records[len(middle_records) // 2 :],
                ]
                if len(part_records) == 3:
                    child_part_records.append(part_records[-1])
            if name == "outcome_evidence":
                physical_rows: list[Mapping[str, object]] = [
                    tagged
                    for records in child_part_records
                    for record in records
                    for tagged in (
                        {"field": "expected_target_ids", "value": record[mappings["target_id"]]},
                        {"field": "source_row_ids", "value": record[mappings["target_id"]]},
                        {"field": "outcomes", "value": record},
                    )
                ]
            else:
                physical_rows = [
                    {"value": record} for records in child_part_records for record in records
                ]
            manifest_relative = cast(str, declaration["manifest_relative_path"])
            identity_field = cast(str, declaration["identity_field"])
            compact: dict[str, object] = {
                **wrapper,
                "header_sha256": hashlib.sha256(_canonical_bytes(wrapper)).hexdigest(),
                "storage": _PARTITIONED_ROWS_STORAGE,
                "identity_field": identity_field,
                "row_count": len(physical_rows),
                "parts": [],
            }
            multiplier = 3 if name == "outcome_evidence" else 1
            offsets = [0]
            for records in child_part_records:
                offsets.append(offsets[-1] + len(records) * multiplier)
            references: list[dict[str, object]] = []
            for part_index, (start, end) in enumerate(pairwise(offsets)):
                encoded = _canonical_bytes(
                    {
                        "contract": _PARTITIONED_PART_CONTRACT,
                        "schema_version": 1,
                        "parent_contract": declaration["contract"],
                        "parent_semantic_id": wrapper[declaration["identity_field"]],
                        "part_index": part_index,
                        "rows": physical_rows[start:end],
                    }
                )
                relative = f"{manifest_relative}.parts/part-{part_index:06d}.json"
                part_path = root / relative
                part_path.parent.mkdir(parents=True, exist_ok=True)
                part_path.write_bytes(encoded)
                references.append(
                    {
                        "path": relative,
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "row_count": end - start,
                        "part_index": part_index,
                    }
                )
            compact["parts"] = references
            compact["row_count"] = len(physical_rows)
            manifest_path = root / manifest_relative
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_bytes(_canonical_bytes(compact))
            locators[name] = str(manifest_path)
        return load_retained_rows(config, locators=locators, _fixture=True)


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
        "coverage": round(len(selected_rows) / len(rows), 12),
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
    canonical_positions = {
        index: _qdecimal(Decimal(str(round(positions[index], 12)))) for index in range(len(rows))
    }
    canonical_changes: dict[int, Decimal] = {}
    prior_positions: dict[tuple[str, str, int], Decimal] = {}
    ordered_indices = sorted(
        range(len(rows)),
        key=lambda index: (
            rows[index].decision_time,
            rows[index].period,
            rows[index].target_id,
            rows[index].asset,
            rows[index].horizon_minutes,
        ),
    )
    for index in ordered_indices:
        row = rows[index]
        key = (row.target_id, row.asset, row.horizon_minutes)
        previous = prior_positions.get(key, Decimal("0"))
        canonical_changes[index] = _qdecimal(canonical_positions[index] - previous)
        prior_positions[key] = canonical_positions[index]

    def view(indices: Sequence[int]) -> dict[str, Any]:
        rendered_positions = {index: canonical_positions[index] for index in indices}
        rendered_changes = {index: canonical_changes[index] for index in indices}
        rendered_gross = {
            index: _qdecimal(Decimal(str(round(gross_values[index], 12)))) for index in indices
        }
        gross_total = sum(rendered_gross.values(), Decimal("0"))
        turnover = sum((abs(value) for value in rendered_changes.values()), Decimal("0"))
        count = len(indices)
        gross_mean = gross_total / count if count else Decimal("0")
        break_even_cost = gross_total / turnover if turnover else None
        sensitivities: list[dict[str, Any]] = []
        for point in config.document["cost_grid"]:
            cost = _qdecimal(Decimal(str(point["value"])))
            sensitivities.append(
                {
                    "cost": float(cost),
                    "unit": point["unit"],
                    "net_mean": (
                        float(_qdecimal(gross_mean - cost * turnover / count)) if count else None
                    ),
                    "break_even_cost": (
                        float(_qdecimal(break_even_cost)) if break_even_cost is not None else None
                    ),
                    "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
                }
            )
        return {
            "gross_total": float(_qdecimal(gross_total)),
            "gross_mean": float(_qdecimal(gross_mean)),
            "turnover": float(_qdecimal(turnover)),
            "break_even_cost": (
                float(_qdecimal(break_even_cost)) if break_even_cost is not None else None
            ),
            "all_in_cost_sensitivity": sensitivities,
            "position_trace": [
                {
                    "target_id": rows[index].target_id,
                    "decision_time": rows[index].decision_time,
                    "target_position": float(rendered_positions[index]),
                    "target_position_change": float(rendered_changes[index]),
                    "realised_gross": float(rendered_gross[index]),
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
        if set(descriptor) != {"degree", "enabled", "family", "id"}:
            raise FreezeError("candidate declaration schema is not frozen")
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
    candidate_ids = [str(descriptor["id"]) for descriptor in candidate_descriptors]
    if len(candidate_ids) != len(set(candidate_ids)) or set(candidate_ids) != set(_CANDIDATE_IDS):
        raise FreezeError("frozen candidate declarations have missing or extra IDs")
    if set(candidate_by_family) != {"ridge", "constant_zero", "bounded_huber"}:
        raise FreezeError("frozen candidate declarations are incomplete")

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
    if retained_metadata is not None and "_role_predictions" in retained_metadata:
        role_predictions = cast(Mapping[str, Any], retained_metadata["_role_predictions"])
        loader = cast(Mapping[str, Any], config.document["retained_loader"])
        wrappers = cast(Mapping[str, Any], loader["child_wrappers"])
        identity_bindings = cast(Mapping[str, Any], loader["identity_bindings"])
        expected_role_bindings = {
            "LOCAL_RIDGE": {
                "dataset_id": identity_bindings["dataset_ids"]["LOCAL_RIDGE"],
                "config_id": identity_bindings["config_ids"]["LOCAL_RIDGE"],
                "wrapper_sha256": wrappers["local_forecast"]["sha256"],
            },
            "POOLED_LOCAL_RIDGE": {
                "dataset_id": identity_bindings["dataset_ids"]["POOLED_LOCAL_RIDGE"],
                "config_id": identity_bindings["config_ids"]["POOLED_LOCAL_RIDGE"],
                "wrapper_sha256": wrappers["pooled_forecast"]["sha256"],
            },
            "ZERO_RETURN": {
                "dataset_id": identity_bindings["dataset_ids"]["ZERO_RETURN"],
                "config_id": identity_bindings["config_ids"]["ZERO_RETURN"],
                "wrapper_sha256": wrappers["zero_forecast"]["sha256"],
            },
        }
        actual_role_bindings = cast(Mapping[str, Any], retained_metadata.get("role_bindings", {}))
        if dict(actual_role_bindings) != expected_role_bindings:
            raise FreezeError("retained forecast role bindings are swapped or incomplete")
        try:
            local_role = [float(value) for value in role_predictions["local_forecast"]]
            pooled_role = [float(value) for value in role_predictions["pooled_forecast"]]
            zero_role = [float(value) for value in role_predictions["zero_forecast"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise FreezeError("retained role prediction records are incomplete") from exc
        if not all(len(values) == row_count for values in (local_role, pooled_role, zero_role)):
            raise FreezeError("retained role prediction records do not match selected rows")
        local_predictions = local_role
        pooled_predictions = pooled_role
        zero_predictions = zero_role

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
    if set(candidate_metrics) != set(candidate_ids) or any(
        "execution_receipt" not in metric for metric in candidate_metrics.values()
    ):
        raise FreezeError("candidate execution receipts have missing or extra declarations")
    statistical_formulations = cast(Mapping[str, Any], config.document["statistical_formulations"])
    control_ids = [
        str(control_id) for control_id in cast(list[Any], statistical_formulations["controls"])
    ]
    control_descriptors = [
        cast(Mapping[str, Any], descriptor)
        for descriptor in cast(list[Any], statistical_formulations["control_descriptors"])
    ]
    if any(
        set(descriptor) != {"candidate_id", "fit_policy", "id", "kind"}
        for descriptor in control_descriptors
    ):
        raise FreezeError("control declaration schema is not frozen")
    if len(control_ids) != 3 or len(control_descriptors) != len(control_ids):
        raise FreezeError("frozen control declarations are incomplete")
    if {str(descriptor["id"]) for descriptor in control_descriptors} != set(control_ids):
        raise FreezeError("control declarations have missing or extra IDs")
    descriptor_by_id = {str(descriptor["id"]): descriptor for descriptor in control_descriptors}
    expected_kinds = {
        "zero_return": "constant_zero",
        "local_ridge": "local_ridge",
        "pooled_local_ridge": "pooled_ridge",
    }
    if any(
        str(descriptor["kind"]) != expected_kinds[str(descriptor["id"])]
        for descriptor in control_descriptors
    ):
        raise FreezeError("control declaration kind does not match frozen mapping")
    zero_control_id = next(
        control_id
        for control_id in control_ids
        if descriptor_by_id[control_id]["kind"] == "constant_zero"
    )
    local_control_id = next(
        control_id
        for control_id in control_ids
        if descriptor_by_id[control_id]["kind"] == "local_ridge"
    )
    pooled_control_id = next(
        control_id
        for control_id in control_ids
        if descriptor_by_id[control_id]["kind"] == "pooled_ridge"
    )
    control_predictions: dict[str, list[float]] = {}
    control_masks: dict[str, list[bool]] = {}
    control_fit_executions: dict[str, int] = {}
    for control_id in control_ids:
        kind = str(descriptor_by_id[control_id]["kind"])
        if kind == "constant_zero":
            (
                control_predictions[control_id],
                control_masks[control_id],
                control_fit_executions[control_id],
            ) = zero_predictions, all_mask, 0
        elif kind == "local_ridge":
            (
                control_predictions[control_id],
                control_masks[control_id],
                control_fit_executions[control_id],
            ) = local_predictions, all_mask, 0
        elif kind == "pooled_ridge":
            (
                control_predictions[control_id],
                control_masks[control_id],
                control_fit_executions[control_id],
            ) = pooled_predictions, evaluation_mask, pooled_fit_executions
        else:
            raise FreezeError(f"unsupported control declaration: {kind}")
    role_bindings: Mapping[str, Any] = (
        retained_metadata["role_bindings"]
        if retained_metadata is not None and "role_bindings" in retained_metadata
        else {}
    )
    control_role_by_kind = {
        "constant_zero": "ZERO_RETURN",
        "local_ridge": "LOCAL_RIDGE",
        "pooled_ridge": "POOLED_LOCAL_RIDGE",
    }
    control_metrics: dict[str, dict[str, Any]] = {
        control_id: {
            **_metrics(
                ordered,
                control_predictions[control_id],
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
            "execution_receipt": {
                **dict(descriptor_by_id[control_id]),
                "role_binding": role_bindings.get(
                    control_role_by_kind[str(descriptor_by_id[control_id]["kind"])]
                ),
            },
        }
        for control_id in control_ids
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
    if set(tiny_graph) != {"enabled", "family", "hidden_units", "id", "layers"}:
        raise FreezeError("tiny graph declaration schema is not frozen")
    if not bool(tiny_graph["enabled"]) or str(tiny_graph["id"]) != "tiny_learned_graph":
        raise FreezeError("tiny graph declaration is not enabled and frozen")
    graph_controls = cast(list[Mapping[str, Any]], config.document["graph_controls"])
    expected_graph_ids = {
        "local_non_graph",
        "pooled_non_graph",
        "fixed_graph",
        "shuffled_graph",
    }
    graph_control_ids = [str(control["id"]) for control in graph_controls]
    if (
        len(graph_control_ids) != len(set(graph_control_ids))
        or set(graph_control_ids) != expected_graph_ids
        or any(set(control) != {"enabled", "id", "kind"} for control in graph_controls)
        or any(not bool(control["enabled"]) for control in graph_controls)
    ):
        raise FreezeError("graph control declarations have missing or extra entries")
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
    expected_graph_execution_ids = expected_graph_ids | {str(tiny_graph["id"])}
    if set(graph_predictions) != expected_graph_execution_ids:
        raise FreezeError("graph execution receipts have missing or extra declarations")
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
    graph_role_by_kind = {
        "non_graph_local": "LOCAL_RIDGE",
        "non_graph_pooled": "POOLED_LOCAL_RIDGE",
    }
    frozen_loader = cast(Mapping[str, Any], config.document["retained_loader"])
    frozen_identity_bindings = cast(Mapping[str, Any], frozen_loader["identity_bindings"])
    frozen_wrappers = cast(Mapping[str, Any], frozen_loader["child_wrappers"])
    frozen_role_wrapper_names = {
        "LOCAL_RIDGE": "local_forecast",
        "POOLED_LOCAL_RIDGE": "pooled_forecast",
    }
    for control in graph_controls:
        control_id = str(control["id"])
        graph_metrics[control_id]["fit_executions"] = 0
        receipt = dict(control)
        role_name = graph_role_by_kind.get(str(control["kind"]))
        if role_name is not None and retained_metadata is not None:
            expected_binding = {
                "dataset_id": frozen_identity_bindings["dataset_ids"][role_name],
                "config_id": frozen_identity_bindings["config_ids"][role_name],
                "wrapper_sha256": frozen_wrappers[frozen_role_wrapper_names[role_name]]["sha256"],
            }
            actual_binding = role_bindings.get(role_name)
            if not isinstance(actual_binding, Mapping):
                raise FreezeError(f"graph role binding mismatch: {role_name}")
            actual_binding_map = cast(Mapping[str, Any], actual_binding)
            if dict(actual_binding_map) != expected_binding:
                raise FreezeError(f"graph role binding mismatch: {role_name}")
            receipt["role_binding"] = dict(actual_binding_map)
        graph_metrics[control_id]["execution_receipt"] = receipt
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
            "role_bindings": (
                retained_metadata.get("role_bindings", {}) if retained_metadata else {}
            ),
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
                pooled_control_id: pooled_fit_executions,
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
