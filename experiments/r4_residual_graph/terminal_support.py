"""Outcome-blind, create-only terminal support capsule boundary."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from .foundation import ALL_INSTRUMENTS, LABEL, TERMINAL_BLOCK, TERMINAL_START
from .tensor import FEATURE_SEMANTIC_SHA256, P0_FEATURE_NAMES, _as_utc, _canonical_bytes

ALLOWED_TERMINAL_COLUMNS = frozenset(
    {
        "instrument_id",
        "decision_time",
        "block",
        "fold",
        "target_valid",
        "target_available_at",
        "dependency_start",
        "dependency_end",
        "dependency_interval_start",
        "dependency_interval_end",
        "feature_data_asof",
        "feature_available_at",
        "latest_feature_bar_end",
        "feature_schema",
        "feature_schema_identity",
        "feature_identity",
        "feature_semantic_sha256",
        "feature_mask",
        "availability_mask",
        "node_mask",
        "context_schema_identity",
        "context_identity",
        "history_start",
        "history_end",
        "history_identity",
        "source_active",
        "source_class",
        "evidence_label",
        "forecast_exists",
        "forecast_identity",
        "support_key",
        "support_identity",
        "graph_identity",
        "config_identity",
        "manifest_sha256",
        "child_closure_sha256",
        "parent_identity",
    }
)

REQUIRED_TERMINAL_COLUMNS = frozenset(
    {
        "instrument_id",
        "decision_time",
        "block",
        "target_valid",
        "target_available_at",
        "dependency_start",
        "dependency_end",
        "feature_data_asof",
        "feature_available_at",
        "latest_feature_bar_end",
        "feature_schema_identity",
        "feature_identity",
        "feature_semantic_sha256",
        "feature_mask",
        "availability_mask",
        "node_mask",
        "context_schema_identity",
        "context_identity",
        "history_start",
        "history_end",
        "history_identity",
        "forecast_exists",
        "forecast_identity",
        "graph_identity",
        "config_identity",
        "manifest_sha256",
        "child_closure_sha256",
        "parent_identity",
        "evidence_label",
        "source_class",
        "source_active",
    }
)
FORBIDDEN_TERMINAL_TOKENS = (
    "target_return",
    "outcome_price",
    "realised_return",
    "realized_return",
    "candidate_prediction",
    "prediction",
    "metric",
    "pnl",
    "profit",
    "loss",
)

_TERMINAL_SUPPORT_SEAL = object()


@dataclass(frozen=True, init=False)
class TerminalSupportConfig:
    """Authenticated terminal-support configuration.

    Primary configurations can only be made from an authenticated LAB-0 capsule,
    frozen runtime configuration and canonical economic graph. Synthetic smoke
    support uses the explicit for_smoke capability and can never be primary.
    """

    manifest_sha256: str
    child_closure_sha256: str
    parent_identity: str
    graph_identity: str
    config_identity: str
    node_order: tuple[str, ...]
    feature_names: tuple[str, ...]
    lookback_minutes: int
    feature_semantic_sha256: str
    terminal_block: str
    forecast_identity: str | None
    source_class: str
    evidence_label: str
    mode: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("terminal support configurations require an authenticated factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> TerminalSupportConfig:
        if token is not _TERMINAL_SUPPORT_SEAL:
            raise TypeError("terminal support configuration seal is private")
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_sealed", _TERMINAL_SUPPORT_SEAL)
        instance._validate()
        return instance

    @classmethod
    def from_authenticated_parent(
        cls,
        parent: Any,
        runtime_config: Any,
        graph: Any,
        *,
        forecast_identity: str | None = None,
    ) -> TerminalSupportConfig:
        """Bind primary support to authenticated parent and frozen runtime objects."""
        from .foundation import _LAB0_SEAL, Lab0Capsule
        from .graph import FrozenEconomicGraph, build_fixed_economic_graph
        from .runtime import FrozenRuntimeConfig

        if (
            not isinstance(parent, Lab0Capsule)
            or getattr(parent, "_authenticated_parent", None) is not _LAB0_SEAL
        ):
            raise TypeError("terminal support requires an authenticated LAB-0 parent")
        if not isinstance(runtime_config, FrozenRuntimeConfig):
            raise TypeError("terminal support requires the frozen runtime configuration")
        if not isinstance(graph, FrozenEconomicGraph):
            raise TypeError("terminal support requires the canonical fixed economic graph")
        canonical_graph = build_fixed_economic_graph()
        if (
            graph.identity != canonical_graph.identity
            or runtime_config.fixed_graph_identity != graph.identity
        ):
            raise ValueError("terminal support graph is not the authenticated canonical graph")
        if runtime_config.manifest_identity != parent.manifest_sha256:
            raise ValueError("terminal support manifest is not bound to authenticated LAB-0")
        if runtime_config.child_closure_identity != parent.child_closure_sha256:
            raise ValueError("terminal support child closure is not bound to authenticated LAB-0")
        return cls._create(
            token=_TERMINAL_SUPPORT_SEAL,
            manifest_sha256=parent.manifest_sha256,
            child_closure_sha256=parent.child_closure_sha256,
            parent_identity=runtime_config.parent_identity,
            graph_identity=graph.identity,
            config_identity=runtime_config.identity,
            node_order=tuple(ALL_INSTRUMENTS),
            feature_names=P0_FEATURE_NAMES,
            lookback_minutes=60,
            feature_semantic_sha256=FEATURE_SEMANTIC_SHA256,
            terminal_block=TERMINAL_BLOCK,
            forecast_identity=forecast_identity,
            source_class=runtime_config.source_class,
            evidence_label=LABEL,
            mode="PRIMARY",
        )

    @classmethod
    def for_smoke(
        cls,
        runtime_config: Any,
        graph: Any,
        *,
        forecast_identity: str | None = None,
    ) -> TerminalSupportConfig:
        """Create the separate synthetic smoke capability."""
        from .graph import FrozenEconomicGraph, build_fixed_economic_graph
        from .runtime import FrozenRuntimeConfig

        if not isinstance(runtime_config, FrozenRuntimeConfig) or not isinstance(
            graph, FrozenEconomicGraph
        ):
            raise TypeError("synthetic support requires frozen runtime and canonical graph")
        if graph.identity != build_fixed_economic_graph().identity:
            raise ValueError("synthetic support graph is not canonical")
        return cls._create(
            token=_TERMINAL_SUPPORT_SEAL,
            manifest_sha256=runtime_config.manifest_identity,
            child_closure_sha256=runtime_config.child_closure_identity,
            parent_identity=runtime_config.parent_identity,
            graph_identity=graph.identity,
            config_identity=runtime_config.identity,
            node_order=tuple(ALL_INSTRUMENTS),
            feature_names=P0_FEATURE_NAMES,
            lookback_minutes=60,
            feature_semantic_sha256=FEATURE_SEMANTIC_SHA256,
            terminal_block=TERMINAL_BLOCK,
            forecast_identity=forecast_identity,
            source_class="SMOKE_SYNTHETIC",
            evidence_label="SMOKE_SYNTHETIC",
            mode="SMOKE",
        )

    def _validate(self) -> None:
        if self.node_order != tuple(ALL_INSTRUMENTS):
            raise ValueError("terminal support requires canonical twenty-node order")
        if self.feature_names != P0_FEATURE_NAMES:
            raise ValueError("terminal support requires canonical P0 features")
        if self.lookback_minutes != 60:
            raise ValueError("terminal support requires the canonical 60-minute lookback")
        if self.terminal_block != TERMINAL_BLOCK:
            raise ValueError("terminal support is restricted to the terminal block")
        if self.feature_semantic_sha256 != FEATURE_SEMANTIC_SHA256:
            raise ValueError("terminal feature semantics do not match authenticated P0 schema")
        if self.mode not in {"PRIMARY", "SMOKE"}:
            raise ValueError("terminal support mode is invalid")
        if self.mode == "PRIMARY" and (
            self.source_class == "SMOKE_SYNTHETIC" or self.evidence_label == "SMOKE_SYNTHETIC"
        ):
            raise ValueError("synthetic identities cannot be used for primary support")
        if self.mode == "SMOKE" and (
            self.source_class != "SMOKE_SYNTHETIC" or self.evidence_label != "SMOKE_SYNTHETIC"
        ):
            raise ValueError("smoke support identities are fixed")
        for name, identity in (
            ("manifest_sha256", self.manifest_sha256),
            ("child_closure_sha256", self.child_closure_sha256),
            ("parent_identity", self.parent_identity),
            ("graph_identity", self.graph_identity),
            ("config_identity", self.config_identity),
            ("source_class", self.source_class),
            ("evidence_label", self.evidence_label),
        ):
            if not isinstance(identity, str) or not identity:
                raise ValueError(f"terminal support requires a non-empty {name}")


@dataclass(frozen=True)
class TerminalSupportCapsule:
    parent_identity: str
    manifest_sha256: str
    child_closure_sha256: str
    graph_identity: str
    config_identity: str
    node_order: tuple[str, ...]
    feature_names: tuple[str, ...]
    feature_semantic_sha256: str
    lookback_minutes: int
    evidence_label: str
    source_class: str
    mode: str
    source_active: bool
    keys: tuple[str, ...]
    key_count: int
    decision_time_count: int
    per_instrument_counts: tuple[tuple[str, int], ...]
    input_identities: tuple[tuple[str, str], ...]
    key_input_identities: tuple[tuple[str, str], ...]
    ordered_key_sha256: str
    predicate_identity: str
    artifact_identity: str
    terminal_metadata_identity: str = ""
    history_content_identity: str = ""
    history_row_count: int = 0
    first_terminal_lookback_identity: str = ""
    first_terminal_lookback_row_count: int = 0
    first_terminal_lookback_instrument_count: int = 0
    first_terminal_lookback_minute_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": "R4P0_TERMINAL_SUPPORT",
            "parent_identity": self.parent_identity,
            "manifest_sha256": self.manifest_sha256,
            "child_closure_sha256": self.child_closure_sha256,
            "graph_identity": self.graph_identity,
            "config_identity": self.config_identity,
            "node_order": list(self.node_order),
            "feature_names": list(self.feature_names),
            "feature_semantic_sha256": self.feature_semantic_sha256,
            "lookback_minutes": self.lookback_minutes,
            "evidence_label": self.evidence_label,
            "source_class": self.source_class,
            "mode": self.mode,
            "source_active": self.source_active,
            "keys": list(self.keys),
            "key_count": self.key_count,
            "decision_time_count": self.decision_time_count,
            "per_instrument_counts": dict(self.per_instrument_counts),
            "input_identities": [list(pair) for pair in self.input_identities],
            "key_input_identities": dict(self.key_input_identities),
            "ordered_key_sha256": self.ordered_key_sha256,
            "predicate_identity": self.predicate_identity,
            "artifact_identity": self.artifact_identity,
            "terminal_metadata_identity": self.terminal_metadata_identity,
            "history_content_identity": self.history_content_identity,
            "history_row_count": self.history_row_count,
            "first_terminal_lookback_identity": self.first_terminal_lookback_identity,
            "first_terminal_lookback_row_count": self.first_terminal_lookback_row_count,
            "first_terminal_lookback_instrument_count": (
                self.first_terminal_lookback_instrument_count
            ),
            "first_terminal_lookback_minute_count": self.first_terminal_lookback_minute_count,
        }

    def _validate(self) -> None:
        if (
            not self.parent_identity
            or not self.manifest_sha256
            or not self.child_closure_sha256
            or not self.graph_identity
            or not self.config_identity
            or not self.evidence_label
            or not self.source_class
            or self.mode not in {"PRIMARY", "SMOKE"}
            or self.source_active is not True
        ):
            raise ValueError("terminal support source identity is not canonical")
        if self.mode == "PRIMARY" and self.source_class == "SMOKE_SYNTHETIC":
            raise ValueError("synthetic terminal support cannot be primary")
        if self.mode == "PRIMARY" and not self.terminal_metadata_identity:
            raise ValueError("primary terminal support requires metadata identity")
        if self.mode == "PRIMARY" and (
            not self.history_content_identity
            or self.history_row_count <= 0
            or not self.first_terminal_lookback_identity
            or self.first_terminal_lookback_row_count != 20 * 60
            or self.first_terminal_lookback_instrument_count != 20
            or self.first_terminal_lookback_minute_count != 60
        ):
            raise ValueError("primary terminal support requires authenticated history coverage")
        if self.mode == "SMOKE" and self.source_class != "SMOKE_SYNTHETIC":
            raise ValueError("smoke terminal support source identity is invalid")
        if len(self.keys) != self.key_count:
            raise ValueError("terminal support key count is inconsistent")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("terminal support keys are not unique")
        if tuple(self.keys) != tuple(sorted(self.keys)):
            raise ValueError("terminal support keys are not in canonical order")
        if hashlib.sha256(_canonical_bytes(self.keys)).hexdigest() != self.ordered_key_sha256:
            raise ValueError("terminal support ordered key identity mismatch")
        payload = {
            "parent_identity": self.parent_identity,
            "manifest_sha256": self.manifest_sha256,
            "child_closure_sha256": self.child_closure_sha256,
            "graph_identity": self.graph_identity,
            "config_identity": self.config_identity,
            "node_order": self.node_order,
            "feature_names": self.feature_names,
            "feature_semantic_sha256": self.feature_semantic_sha256,
            "lookback_minutes": self.lookback_minutes,
            "evidence_label": self.evidence_label,
            "source_class": self.source_class,
            "mode": self.mode,
            "source_active": self.source_active,
            "keys": self.keys,
            "decision_time_count": self.decision_time_count,
            "per_instrument_counts": self.per_instrument_counts,
            "input_identities": self.input_identities,
            "key_input_identities": self.key_input_identities,
            "ordered_key_sha256": self.ordered_key_sha256,
            "predicate_identity": self.predicate_identity,
            "terminal_metadata_identity": self.terminal_metadata_identity,
            "history_content_identity": self.history_content_identity,
            "history_row_count": self.history_row_count,
            "first_terminal_lookback_identity": self.first_terminal_lookback_identity,
            "first_terminal_lookback_row_count": self.first_terminal_lookback_row_count,
            "first_terminal_lookback_instrument_count": (
                self.first_terminal_lookback_instrument_count
            ),
            "first_terminal_lookback_minute_count": self.first_terminal_lookback_minute_count,
        }
        expected = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        if self.artifact_identity != expected:
            raise ValueError("terminal support artifact identity mismatch")


def _validate_columns(columns: set[str]) -> None:
    for column in columns:
        lowered = column.lower()
        if any(token in lowered for token in FORBIDDEN_TERMINAL_TOKENS):
            raise ValueError(f"outcome-bearing terminal column is prohibited: {column}")
    for column in columns:
        if column not in ALLOWED_TERMINAL_COLUMNS:
            raise ValueError(f"terminal support column is not allowlisted: {column}")
    missing = REQUIRED_TERMINAL_COLUMNS - columns
    if missing:
        raise ValueError(
            f"terminal support metadata is missing required columns: {sorted(missing)}"
        )


def _validate_schema(rows: Iterable[Mapping[str, Any]]) -> None:
    _validate_columns({column for row in rows for column in row})


def _metadata_rows(rows: Iterable[Mapping[str, Any]] | Any) -> Iterator[dict[str, Any]]:
    """Read allowlisted metadata without touching rejected column values."""
    if hasattr(rows, "columns") and hasattr(rows, "select"):
        dataframe = cast(Any, rows)
        columns: set[str] = set(cast(Iterable[str], dataframe.columns))
        _validate_columns(columns)
        selected = sorted(columns & ALLOWED_TERMINAL_COLUMNS)
        for row in dataframe.select(selected).iter_rows(named=True):
            yield dict(row)
        return
    if isinstance(rows, Mapping):
        iterable = (rows,)
        for row in iterable:
            typed_row = cast(Mapping[str, Any], row)
            columns = set(cast(Iterable[str], typed_row))
            _validate_columns(columns)
            allowed = columns & ALLOWED_TERMINAL_COLUMNS
            yield {column: typed_row[column] for column in allowed}
        return
    iterable = cast(Iterable[Mapping[str, Any]], rows)
    if iter(iterable) is iterable:
        raise ValueError("terminal metadata requires a re-iterable row source")
    for row in iterable:
        if not isinstance(row, Mapping):
            raise TypeError("terminal metadata rows must be mappings")
        typed_row = row
        _validate_columns(set(cast(Iterable[str], typed_row)))
    for row in iterable:
        typed_row = row
        allowed = set(cast(Iterable[str], typed_row)) & ALLOWED_TERMINAL_COLUMNS
        yield {column: typed_row[column] for column in allowed}


def _predicate_identity(config: TerminalSupportConfig) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            {
                "predicate": (
                    "target_valid AND canonical individual-key metadata (masked nodes permitted)"
                ),
                "target_values_loaded": False,
                "config_identity": config.config_identity,
                "lookback_minutes": config.lookback_minutes,
                "terminal_block": config.terminal_block,
                "required_columns": sorted(REQUIRED_TERMINAL_COLUMNS),
            }
        )
    ).hexdigest()


def _required_timestamp(row: Mapping[str, Any], column: str) -> datetime:
    value = row[column]
    if value is None:
        raise ValueError(f"terminal support metadata {column} must be present")
    return _as_utc(value)


def _require_identity(row: Mapping[str, Any], column: str) -> str:
    value = row[column]
    if not isinstance(value, str) or not value:
        raise ValueError(f"terminal support metadata {column} must be a non-empty identity")
    return value


def _validate_terminal_row(
    row: Mapping[str, Any], *, config: TerminalSupportConfig
) -> tuple[str, datetime]:
    instrument = row["instrument_id"]
    if instrument not in config.node_order:
        raise ValueError(f"unknown terminal instrument: {instrument}")
    timestamp = _required_timestamp(row, "decision_time")
    if timestamp < TERMINAL_START:
        raise ValueError("terminal support decision precedes terminal start")
    if row["block"] != config.terminal_block:
        raise ValueError(f"terminal support row is outside {config.terminal_block}")
    target_available_at = _required_timestamp(row, "target_available_at")
    if target_available_at <= timestamp:
        raise ValueError("terminal target availability must follow decision time")
    dependency_start = _required_timestamp(row, "dependency_start")
    optional_times = {
        column: None if row[column] is None else _as_utc(row[column])
        for column in (
            "dependency_end",
            "feature_data_asof",
            "feature_available_at",
            "latest_feature_bar_end",
        )
    }
    for column, value in optional_times.items():
        if value is not None and value > timestamp:
            raise ValueError(f"terminal {column} is after decision time")
    feature_data_asof = optional_times["feature_data_asof"]
    feature_available_at = optional_times["feature_available_at"]
    latest_feature_bar_end = optional_times["latest_feature_bar_end"]
    if (
        feature_data_asof is not None
        and feature_available_at is not None
        and feature_data_asof > feature_available_at
    ):
        raise ValueError("terminal feature data is after availability")
    if (
        latest_feature_bar_end is not None
        and feature_data_asof is not None
        and latest_feature_bar_end > feature_data_asof
    ):
        raise ValueError("terminal feature bar is after feature data asof")
    node_available = (
        feature_data_asof is not None
        and feature_available_at is not None
        and row["source_active"] is not None
        and float(row["source_active"]) > 0.0
    )
    if row["node_mask"] != node_available:
        raise ValueError("terminal node mask differs from timestamp/source availability")
    dependency_end = optional_times["dependency_end"]
    if dependency_start > timestamp or (
        node_available and dependency_end is not None and dependency_start > dependency_end
    ):
        raise ValueError("terminal dependency interval must end by decision time")
    history_start = _required_timestamp(row, "history_start")
    history_end = _required_timestamp(row, "history_end")
    if history_end != timestamp or history_start > timestamp - timedelta(
        minutes=config.lookback_minutes
    ):
        raise ValueError("terminal history does not cover the declared lookback")
    for column in ("feature_mask", "availability_mask"):
        mask = row[column]
        if (
            not isinstance(mask, (list, tuple))
            or not mask
            or any(not isinstance(value, bool) for value in mask)
        ):
            raise ValueError(f"terminal support metadata {column} must contain Boolean masks")
        if not node_available and any(mask):
            raise ValueError("terminal unavailable node cannot expose available feature masks")
    if len(row["feature_mask"]) != len(row["availability_mask"]) or any(
        value and not available
        for value, available in zip(row["feature_mask"], row["availability_mask"], strict=True)
    ):
        raise ValueError("terminal value masks must be a subset of availability masks")
    _require_identity(row, "feature_schema_identity")
    _require_identity(row, "feature_identity")
    if _require_identity(row, "feature_semantic_sha256") != config.feature_semantic_sha256:
        raise ValueError("terminal feature semantic identity does not match config")
    _require_identity(row, "context_schema_identity")
    _require_identity(row, "context_identity")
    _require_identity(row, "history_identity")
    if _require_identity(row, "manifest_sha256") != config.manifest_sha256:
        raise ValueError("terminal manifest identity does not match authenticated config")
    if _require_identity(row, "child_closure_sha256") != config.child_closure_sha256:
        raise ValueError("terminal child closure identity does not match authenticated config")
    if _require_identity(row, "parent_identity") != config.parent_identity:
        raise ValueError("terminal parent identity does not match authenticated config")
    if _require_identity(row, "evidence_label") != config.evidence_label:
        raise ValueError("terminal evidence identity does not match config")
    if _require_identity(row, "source_class") != config.source_class:
        raise ValueError("terminal source identity does not match config")
    if row["forecast_exists"] is not True and row["forecast_exists"] != 1:
        raise ValueError("terminal support requires an existing local forecast")
    forecast_identity = _require_identity(row, "forecast_identity")
    if config.forecast_identity is not None and forecast_identity != config.forecast_identity:
        raise ValueError("terminal forecast identity does not match config")
    if _require_identity(row, "graph_identity") != config.graph_identity:
        raise ValueError("terminal graph identity does not match config")
    if _require_identity(row, "config_identity") != config.config_identity:
        raise ValueError("terminal config identity does not match config")
    return instrument, timestamp


def build_terminal_support(
    rows: Iterable[Mapping[str, Any]] | Any,
    *,
    config: TerminalSupportConfig,
    output_path: str | Path | None = None,
    development_register_closed: bool = False,
    actual_terminal: bool = False,
    authenticated_metadata: AuthenticatedTerminalMetadata | None = None,
    history_content_identity: str | None = None,
    history_row_count: int = 0,
    first_terminal_lookback_identity: str | None = None,
    first_terminal_lookback_row_count: int = 0,
    first_terminal_lookback_instrument_count: int = 0,
    first_terminal_lookback_minute_count: int = 0,
) -> TerminalSupportCapsule:
    """Build metadata-only support; actual terminal execution is rejected in R4.B."""
    if (
        not isinstance(config, TerminalSupportConfig)
        or getattr(config, "_sealed", None) is not _TERMINAL_SUPPORT_SEAL
    ):
        raise TypeError("terminal support requires an authenticated configuration capability")
    config._validate()
    if not development_register_closed:
        raise RuntimeError("terminal support requires a closed development register")
    if actual_terminal:
        raise RuntimeError("actual terminal support is prohibited in R4.B")
    if config.mode == "PRIMARY" and not isinstance(
        authenticated_metadata, AuthenticatedTerminalMetadata
    ):
        raise TypeError("primary terminal support requires authenticated terminal metadata")
    metadata_rows = tuple(_metadata_rows(rows))
    if config.mode == "PRIMARY" and (
        not history_content_identity
        or history_row_count <= 0
        or not first_terminal_lookback_identity
        or first_terminal_lookback_row_count != 20 * 60
        or first_terminal_lookback_instrument_count != 20
        or first_terminal_lookback_minute_count != 60
    ):
        raise TypeError("primary terminal support requires authenticated history bindings")
    if authenticated_metadata is not None:
        expected_rows = tuple(dict(row) for row in authenticated_metadata.to_rows())
        supplied_bytes = _canonical_bytes(
            [
                {column: _canonical_row_value(value) for column, value in row.items()}
                for row in metadata_rows
            ]
        )
        expected_bytes = _canonical_bytes(
            [
                {column: _canonical_row_value(value) for column, value in row.items()}
                for row in expected_rows
            ]
        )
        if supplied_bytes != expected_bytes:
            raise ValueError("terminal support rows differ from authenticated terminal metadata")
        metadata_rows = expected_rows
    by_time: dict[datetime, set[str]] = {}
    seen: set[tuple[datetime, str]] = set()
    input_identities: set[tuple[str, str]] = set()
    key_input_identities: dict[str, str] = {}
    evidence_labels: set[str] = set()
    source_classes: set[str] = set()
    for row in metadata_rows:
        instrument, timestamp = _validate_terminal_row(row, config=config)
        key = (timestamp, instrument)
        if key in seen:
            raise ValueError(
                f"duplicate terminal support key: {instrument} at {timestamp.isoformat()}"
            )
        seen.add(key)
        target_valid = row["target_valid"]
        if target_valid is not True and target_valid != 1:
            continue
        evidence_label = _require_identity(row, "evidence_label")
        source_class = _require_identity(row, "source_class")
        evidence_labels.add(evidence_label)
        source_classes.add(source_class)
        identities = tuple(
            (
                column,
                (
                    str(bool(row[column]))
                    if column == "source_active"
                    else _require_identity(row, column)
                ),
            )
            for column in (
                "feature_schema_identity",
                "feature_identity",
                "context_schema_identity",
                "context_identity",
                "history_identity",
                "forecast_identity",
                "graph_identity",
                "config_identity",
                "evidence_label",
                "source_class",
                "source_active",
            )
        )
        input_identities.update(identities)
        key_string = f"{timestamp.isoformat()}|{instrument}"
        key_identity = hashlib.sha256(
            _canonical_bytes({"key": key_string, "identities": identities})
        ).hexdigest()
        key_input_identities[key_string] = key_identity
        by_time.setdefault(timestamp, set()).add(instrument)
    eligible_times = tuple(sorted(by_time))
    if not eligible_times:
        raise ValueError("terminal support is empty after causal eligibility checks")
    keys = tuple(
        f"{timestamp.isoformat()}|{instrument}"
        for timestamp in eligible_times
        for instrument in config.node_order
        if instrument in by_time[timestamp]
    )
    per_instrument_counts = tuple(
        (
            instrument,
            sum(instrument in instruments for instruments in by_time.values()),
        )
        for instrument in config.node_order
    )
    if any(count <= 0 for _, count in per_instrument_counts):
        raise ValueError("terminal support lacks positive coverage for every instrument")
    key_input_identity_items = tuple((key, key_input_identities[key]) for key in keys)
    ordered_hash = hashlib.sha256(_canonical_bytes(keys)).hexdigest()
    predicate_identity = _predicate_identity(config)
    if len(evidence_labels) != 1 or len(source_classes) != 1:
        raise ValueError("terminal support source identities must be uniform")
    evidence_label = next(iter(evidence_labels))
    source_class = next(iter(source_classes))
    payload = {
        "parent_identity": config.parent_identity,
        "manifest_sha256": config.manifest_sha256,
        "child_closure_sha256": config.child_closure_sha256,
        "graph_identity": config.graph_identity,
        "config_identity": config.config_identity,
        "node_order": config.node_order,
        "feature_names": config.feature_names,
        "feature_semantic_sha256": config.feature_semantic_sha256,
        "lookback_minutes": config.lookback_minutes,
        "evidence_label": evidence_label,
        "source_class": source_class,
        "mode": config.mode,
        "source_active": True,
        "keys": keys,
        "decision_time_count": len(eligible_times),
        "per_instrument_counts": per_instrument_counts,
        "input_identities": tuple(sorted(input_identities)),
        "key_input_identities": key_input_identity_items,
        "ordered_key_sha256": ordered_hash,
        "predicate_identity": predicate_identity,
        "terminal_metadata_identity": (
            authenticated_metadata.content_identity if authenticated_metadata is not None else ""
        ),
        "history_content_identity": history_content_identity or "",
        "history_row_count": history_row_count,
        "first_terminal_lookback_identity": first_terminal_lookback_identity or "",
        "first_terminal_lookback_row_count": first_terminal_lookback_row_count,
        "first_terminal_lookback_instrument_count": first_terminal_lookback_instrument_count,
        "first_terminal_lookback_minute_count": first_terminal_lookback_minute_count,
    }
    artifact_identity = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    capsule = TerminalSupportCapsule(
        config.parent_identity,
        config.manifest_sha256,
        config.child_closure_sha256,
        config.graph_identity,
        config.config_identity,
        config.node_order,
        config.feature_names,
        config.feature_semantic_sha256,
        config.lookback_minutes,
        evidence_label,
        source_class,
        config.mode,
        True,
        keys,
        len(keys),
        len(eligible_times),
        per_instrument_counts,
        tuple(sorted(input_identities)),
        key_input_identity_items,
        ordered_hash,
        predicate_identity,
        artifact_identity,
        authenticated_metadata.content_identity if authenticated_metadata is not None else "",
        history_content_identity or "",
        history_row_count,
        first_terminal_lookback_identity or "",
        first_terminal_lookback_row_count,
        first_terminal_lookback_instrument_count,
        first_terminal_lookback_minute_count,
    )
    capsule._validate()
    if output_path is not None:
        path = Path(output_path)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(capsule.to_dict(), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
    return capsule


materialise_terminal_support = build_terminal_support
materialize_terminal_support = build_terminal_support


__all__ = [
    "ALLOWED_TERMINAL_COLUMNS",
    "FORBIDDEN_TERMINAL_TOKENS",
    "REQUIRED_TERMINAL_COLUMNS",
    "AuthenticatedTerminalMetadata",
    "AuthenticatedTerminalPredictionInput",
    "TerminalSupportCapsule",
    "TerminalSupportConfig",
    "build_terminal_support",
    "materialise_terminal_support",
    "materialize_terminal_support",
]


_TERMINAL_METADATA_SEAL = object()


@dataclass(frozen=True, init=False)
class AuthenticatedTerminalMetadata:
    """Sealed outcome-blind terminal metadata composed from authenticated LAB-0 children."""

    rows: tuple[Mapping[str, Any], ...]
    content_identity: str
    row_count: int
    parent_identity: str
    manifest_sha256: str
    child_closure_sha256: str
    graph_identity: str
    config_identity: str
    forecast_configuration_id: str
    forecast_fold: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("authenticated terminal metadata requires an authenticated factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> AuthenticatedTerminalMetadata:
        if token is not _TERMINAL_METADATA_SEAL:
            raise TypeError("authenticated terminal metadata seal is private")
        instance = object.__new__(cls)
        source_rows = tuple(fields.pop("rows"))
        immutable_rows = tuple(MappingProxyType(dict(row)) for row in source_rows)
        object.__setattr__(instance, "rows", immutable_rows)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_sealed", _TERMINAL_METADATA_SEAL)
        instance._validate()
        return instance

    def _validate(self) -> None:
        if getattr(self, "_sealed", None) is not _TERMINAL_METADATA_SEAL:
            raise TypeError("authenticated terminal metadata is not sealed")
        if self.row_count != len(self.rows) or not self.rows:
            raise ValueError("authenticated terminal metadata row count is inconsistent")
        seen: set[tuple[str, datetime]] = set()
        for row in self.rows:
            columns = set(row)
            _validate_columns(columns)
            if "target_return" in columns:
                raise ValueError("authenticated terminal metadata cannot contain target outcomes")
            key = (str(row["instrument_id"]), _as_utc(row["decision_time"]))
            if key in seen:
                raise ValueError("authenticated terminal metadata has duplicate row keys")
            seen.add(key)
        payload = [
            {column: _canonical_row_value(value) for column, value in row.items()}
            for row in self.rows
        ]
        expected = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        if self.content_identity != expected:
            raise ValueError("authenticated terminal metadata content identity mismatch")
        for name in (
            "parent_identity",
            "manifest_sha256",
            "child_closure_sha256",
            "graph_identity",
            "config_identity",
            "forecast_configuration_id",
            "forecast_fold",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"authenticated terminal metadata requires {name}")

    def to_rows(self) -> tuple[Mapping[str, Any], ...]:
        """Return immutable canonical rows for downstream exact matching."""
        return self.rows


_TERMINAL_PREDICTION_INPUT_SEAL = object()


@dataclass(frozen=True, init=False)
class AuthenticatedTerminalPredictionInput:
    """Sealed feature/forecast capability created only after terminal support closes."""

    rows: tuple[Mapping[str, Any], ...]
    forecasts: tuple[tuple[str, float], ...]
    content_identity: str
    terminal_metadata_identity: str
    terminal_support_identity: str
    parent_identity: str
    manifest_sha256: str
    child_closure_sha256: str
    graph_identity: str
    config_identity: str
    preprocessor_identity: str
    history_identity: str
    history_content_identity: str = ""
    history_row_count: int = 0
    first_terminal_lookback_identity: str = ""
    first_terminal_lookback_row_count: int = 0
    first_terminal_lookback_instrument_count: int = 0
    first_terminal_lookback_minute_count: int = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("authenticated terminal prediction input requires an authenticated factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> AuthenticatedTerminalPredictionInput:
        if token is not _TERMINAL_PREDICTION_INPUT_SEAL:
            raise TypeError("authenticated terminal prediction input seal is private")
        instance = object.__new__(cls)
        source_rows = tuple(fields.pop("rows"))
        source_forecasts = tuple((str(key), float(value)) for key, value in fields.pop("forecasts"))
        object.__setattr__(
            instance, "rows", tuple(MappingProxyType(dict(row)) for row in source_rows)
        )
        object.__setattr__(instance, "forecasts", source_forecasts)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_sealed", _TERMINAL_PREDICTION_INPUT_SEAL)
        instance._validate()
        return instance

    def _validate(self) -> None:
        from .tensor import P0_FEATURE_NAMES

        if getattr(self, "_sealed", None) is not _TERMINAL_PREDICTION_INPUT_SEAL:
            raise TypeError("authenticated terminal prediction input is not sealed")
        if not self.rows or len(self.rows) != len(self.forecasts):
            raise ValueError("terminal prediction input rows and forecasts are inconsistent")
        keys: list[str] = []
        seen: set[str] = set()
        for row in self.rows:
            extra = set(row) - (ALLOWED_TERMINAL_COLUMNS | set(P0_FEATURE_NAMES))
            if extra:
                raise ValueError(
                    f"terminal prediction input contains non-canonical columns: {sorted(extra)}"
                )
            key = f"{row['instrument_id']}|{_as_utc(row['decision_time']).isoformat()}"
            if key in seen:
                raise ValueError("terminal prediction input has duplicate keys")
            seen.add(key)
            keys.append(key)
        forecast_keys = tuple(key for key, _ in self.forecasts)
        if forecast_keys != tuple(keys):
            raise ValueError("terminal prediction input forecast keys do not match rows")
        if any(not math.isfinite(value) for _, value in self.forecasts):
            raise ValueError("terminal prediction input forecasts must be finite")
        for name in (
            "content_identity",
            "terminal_metadata_identity",
            "terminal_support_identity",
            "parent_identity",
            "manifest_sha256",
            "child_closure_sha256",
            "graph_identity",
            "config_identity",
            "preprocessor_identity",
            "history_identity",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"terminal prediction input requires {name}")
        if self.history_content_identity:
            if self.history_row_count <= 0:
                raise ValueError("terminal prediction input requires history row count")
            if (
                self.first_terminal_lookback_row_count != 20 * 60
                or self.first_terminal_lookback_instrument_count != 20
                or self.first_terminal_lookback_minute_count != 60
                or not self.first_terminal_lookback_identity
            ):
                raise ValueError("terminal prediction input requires lookback coverage")
        payload = {
            "rows": [
                {column: _canonical_row_value(value) for column, value in row.items()}
                for row in self.rows
            ],
            "forecasts": self.forecasts,
            "terminal_metadata_identity": self.terminal_metadata_identity,
            "terminal_support_identity": self.terminal_support_identity,
            "parent_identity": self.parent_identity,
            "manifest_sha256": self.manifest_sha256,
            "child_closure_sha256": self.child_closure_sha256,
            "graph_identity": self.graph_identity,
            "config_identity": self.config_identity,
            "preprocessor_identity": self.preprocessor_identity,
            "history_identity": self.history_identity,
        }
        if self.history_content_identity:
            payload.update(
                {
                    "history_content_identity": self.history_content_identity,
                    "history_row_count": self.history_row_count,
                    "first_terminal_lookback_identity": self.first_terminal_lookback_identity,
                    "first_terminal_lookback_row_count": self.first_terminal_lookback_row_count,
                    "first_terminal_lookback_instrument_count": (
                        self.first_terminal_lookback_instrument_count
                    ),
                    "first_terminal_lookback_minute_count": (
                        self.first_terminal_lookback_minute_count
                    ),
                }
            )
        if hashlib.sha256(_canonical_bytes(payload)).hexdigest() != self.content_identity:
            raise ValueError("terminal prediction input content identity mismatch")

    def to_rows(self) -> tuple[Mapping[str, Any], ...]:
        return self.rows

    def forecast_for(self, key: str) -> float:
        for candidate, value in self.forecasts:
            if candidate == key:
                return value
        raise KeyError(key)


def _canonical_row_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    if isinstance(value, (tuple, list)):
        return tuple(_canonical_row_value(item) for item in value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_row_value(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return _canonical_row_value(value.tolist())
    return value
