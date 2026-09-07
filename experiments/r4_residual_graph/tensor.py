"""Causal masked tensor and candidate-independent support contracts for R4.B.

This module contains only deterministic, numpy-backed data preparation. It deliberately
does not fit a model or read target outcomes.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import numpy as np

from .foundation import _STAGE_WINDOWS, ALL_INSTRUMENTS, TERMINAL_START

P0_FEATURE_NAMES: tuple[str, ...] = (
    "return_60s",
    "return_60s_available",
    "return_300s",
    "return_300s_available",
    "return_contrast_60s_300s",
    "realised_std_60s",
    "mean_absolute_return_60s",
    "mean_log_range_60s",
    "return_sign_balance_60s",
    "available_interval_count_60s",
    "window_coverage_60s",
    "realised_std_300s",
    "mean_absolute_return_300s",
    "mean_log_range_300s",
    "return_sign_balance_300s",
    "available_interval_count_300s",
    "window_coverage_300s",
    "utc_minute_sin",
    "utc_minute_cos",
    "utc_day_sin",
    "utc_day_cos",
    "source_active",
    "target_feature_missing_fraction",
    "cross_market_available_count",
    "quality_healthy",
    "gap_known_by_cutoff",
)

FEATURE_SEMANTIC_SHA256 = "61b4955c8b1536e84c80a574a5a10924d6fd235a1e6b70da3b76de43c9b45282"
DEFAULT_LOOKBACK_MINUTES = 60
BINARY_FEATURE_NAMES = frozenset(
    name
    for name in P0_FEATURE_NAMES
    if name.endswith("_available")
    or name in {"source_active", "quality_healthy", "gap_known_by_cutoff"}
)
_TENSOR_COLUMNS = frozenset(
    {"instrument_id", "decision_time", "feature_data_asof", "feature_available_at", "source_active"}
    | set(P0_FEATURE_NAMES)
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    result = value.astimezone(UTC)
    if result.second or result.microsecond:
        raise ValueError("timestamps must be minute aligned")
    return result


def _rows_dicts(rows: Any, *, columns: frozenset[str] | None = None) -> Iterator[Mapping[str, Any]]:
    """Stream rows, projecting requested columns before reading values."""
    if hasattr(rows, "iter_rows"):
        if columns is None:
            iterator = rows.iter_rows(named=True)
        else:
            source_columns = set(cast(Iterable[str], rows.columns))
            selected = sorted(source_columns & columns)
            iterator = rows.select(selected).iter_rows(named=True)
    elif isinstance(rows, Mapping):
        iterator = iter((rows,))
    else:
        iterator = iter(rows)
    for row in iterator:
        if not isinstance(row, Mapping):
            raise TypeError("rows must contain mapping records")
        if columns is None:
            yield row
        else:
            yield {column: row[column] for column in columns if column in row}


@dataclass(frozen=True)
class TensorContract:
    """Immutable ordering and leakage policy shared by every candidate family."""

    node_order: tuple[str, ...] = ALL_INSTRUMENTS
    feature_names: tuple[str, ...] = P0_FEATURE_NAMES
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES
    feature_semantic_sha256: str = FEATURE_SEMANTIC_SHA256
    preprocessing_partition: str = "TRAINING_ONLY"
    time_basis: str = "UTC_ONE_MINUTE"
    fill_policy: str = "NO_FORWARD_FILL"

    def __post_init__(self) -> None:
        if self.node_order != tuple(ALL_INSTRUMENTS):
            raise ValueError("R4.B requires the canonical twenty-node order")
        if self.feature_names != P0_FEATURE_NAMES:
            raise ValueError("R4.B requires the canonical P0 feature order")
        if self.lookback_minutes < 0:
            raise ValueError("lookback_minutes must be non-negative")
        if self.preprocessing_partition != "TRAINING_ONLY":
            raise ValueError("continuous preprocessing is training-only")
        if self.fill_policy != "NO_FORWARD_FILL":
            raise ValueError("forward filling is prohibited")
        if self.time_basis != "UTC_ONE_MINUTE":
            raise ValueError("tensor time basis must be UTC_ONE_MINUTE")
        if self.feature_semantic_sha256 != FEATURE_SEMANTIC_SHA256:
            raise ValueError("tensor feature semantics do not match authenticated P0 schema")

    @property
    def identity(self) -> str:
        payload = {
            "node_order": self.node_order,
            "feature_names": self.feature_names,
            "lookback_minutes": self.lookback_minutes,
            "feature_semantic_sha256": self.feature_semantic_sha256,
            "preprocessing_partition": self.preprocessing_partition,
            "time_basis": self.time_basis,
            "fill_policy": self.fill_policy,
        }
        return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class MaskedTensor:
    """One causal sequence with explicit value and availability masks.

    Arrays are shaped (lookback_minutes + 1, twenty_nodes, feature_count). Missing cells
    remain NaN and are never filled from another timestamp.
    """

    values: np.ndarray
    value_mask: np.ndarray
    availability_mask: np.ndarray
    node_mask: np.ndarray
    decision_time: datetime
    contract_identity: str
    contract: TensorContract | None = None

    def __post_init__(self) -> None:
        if self.values.ndim != 3:
            raise ValueError("values must be three-dimensional")
        expected = self.values.shape
        if self.value_mask.shape != expected or self.availability_mask.shape != expected:
            raise ValueError("value and availability masks must match values")
        if self.node_mask.shape != expected[:2]:
            raise ValueError("node_mask must have shape (time, node)")
        if self.values.shape[1] != len(ALL_INSTRUMENTS):
            raise ValueError("tensor must contain exactly twenty canonical nodes")
        if self.values.shape[2] != len(P0_FEATURE_NAMES):
            raise ValueError("tensor must contain the canonical P0 feature count")
        active_contract = self.contract or TensorContract()
        if self.values.shape[0] != active_contract.lookback_minutes + 1:
            raise ValueError("tensor time dimension must equal inclusive lookback plus one")
        if any(
            array.dtype != bool
            for array in (self.value_mask, self.availability_mask, self.node_mask)
        ):
            raise ValueError("tensor masks must use boolean dtype")
        if np.any(self.value_mask & ~self.availability_mask):
            raise ValueError("value mask cannot mark an unavailable feature")
        if np.any(self.availability_mask & ~self.node_mask[..., None]):
            raise ValueError("unavailable nodes cannot expose feature availability")
        if np.any(self.value_mask & ~np.isfinite(self.values)):
            raise ValueError("value mask cannot mark non-finite values")
        _as_utc(self.decision_time)
        if self.contract_identity != active_contract.identity:
            raise ValueError("tensor contract identity does not match canonical contract")
        for array in (self.values, self.value_mask, self.availability_mask, self.node_mask):
            array.setflags(write=False)

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.values.shape


def _feature_available(row: Mapping[str, Any], feature: str, timestamp: datetime) -> bool:
    if "feature_data_asof" not in row or row["feature_data_asof"] is None:
        return False
    if _as_utc(row["feature_data_asof"]) > timestamp:
        return False
    if "feature_available_at" not in row or row["feature_available_at"] is None:
        return False
    if _as_utc(row["feature_available_at"]) > timestamp:
        return False
    if "source_active" not in row or row["source_active"] is None:
        return False
    if float(row["source_active"]) <= 0.0:
        return False
    indicator_key = f"{feature}_available"
    if indicator_key in row:
        return float(row[indicator_key]) > 0.0
    return True


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"feature value is not numeric: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"feature value must be finite: {value!r}")
    return result


def build_masked_sequence(
    rows: Iterable[Mapping[str, Any]] | Any,
    decision_time: datetime,
    *,
    contract: TensorContract | None = None,
) -> MaskedTensor:
    """Construct a canonical one-minute sequence without forward filling.

    Rows outside the predeclared interval are ignored, so future rows cannot enter a
    sequence. Duplicate (instrument_id, decision_time) rows fail closed.
    """

    active_contract = contract or TensorContract()
    cutoff = _as_utc(decision_time)
    lookback = active_contract.lookback_minutes
    start = cutoff - timedelta(minutes=lookback)
    grid = tuple(start + timedelta(minutes=offset) for offset in range(lookback + 1))
    by_key: dict[tuple[str, datetime], Mapping[str, Any]] = {}
    allowed_times = set(grid)
    for row in _rows_dicts(rows, columns=_TENSOR_COLUMNS):
        instrument = row["instrument_id"]
        timestamp = _as_utc(row["decision_time"])
        if timestamp not in allowed_times or instrument not in active_contract.node_order:
            continue
        key = (instrument, timestamp)
        if key in by_key:
            raise ValueError(f"duplicate tensor key: {instrument} at {timestamp.isoformat()}")
        by_key[key] = row

    node_index = {instrument: index for index, instrument in enumerate(active_contract.node_order)}
    values = np.full(
        (len(grid), len(active_contract.node_order), len(active_contract.feature_names)), np.nan
    )
    value_mask = np.zeros(values.shape, dtype=bool)
    availability_mask = np.zeros(values.shape, dtype=bool)
    node_mask = np.zeros((len(grid), len(active_contract.node_order)), dtype=bool)

    for time_index, timestamp in enumerate(grid):
        for instrument in active_contract.node_order:
            row = by_key.get((instrument, timestamp))
            if row is None:
                continue
            node = node_index[instrument]
            row_available = True
            for metadata_column in ("feature_data_asof", "feature_available_at", "source_active"):
                if metadata_column not in row or row[metadata_column] is None:
                    row_available = False
                    break
            if row_available and _as_utc(row["feature_data_asof"]) > timestamp:
                row_available = False
            if row_available and _as_utc(row["feature_available_at"]) > timestamp:
                row_available = False
            if row_available and float(row["source_active"]) <= 0.0:
                row_available = False
            node_mask[time_index, node] = row_available
            if not row_available:
                continue
            for feature_index, feature in enumerate(active_contract.feature_names):
                available = _feature_available(row, feature, timestamp)
                availability_mask[time_index, node, feature_index] = available
                if not available:
                    continue
                value = _finite_float(row.get(feature))
                if value is not None:
                    values[time_index, node, feature_index] = value
                    value_mask[time_index, node, feature_index] = True

    return MaskedTensor(
        values=values,
        value_mask=value_mask,
        availability_mask=availability_mask,
        node_mask=node_mask,
        decision_time=cutoff,
        contract_identity=active_contract.identity,
        contract=active_contract,
    )


_SUPPORT_SEAL = object()
_PRIMARY_SUPPORT_CAPABILITY = object()
_SMOKE_SUPPORT_CAPABILITY = object()


@dataclass(frozen=True, init=False)
class SupportRecord:
    keys: tuple[str, ...]
    ordered_key_sha256: str
    key_count: int
    contract_identity: str
    lookback_minutes: int
    node_order: tuple[str, ...]
    feature_names: tuple[str, ...]
    target_keys: tuple[str, ...] = ()
    target_nodes: tuple[int, ...] = ()
    source_identity: str = ""
    manifest_sha256: str = ""
    child_closure_sha256: str = ""
    row_content_identity: str = ""
    tensor_identities: tuple[tuple[str, str], ...] = ()
    mode: str = ""
    parent_identity: str = ""
    _provenance: object = None
    _sealed: object = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("support records must be created by an authenticated factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> SupportRecord:
        if token is not _SUPPORT_SEAL:
            raise TypeError("support record seal is private")
        fields.setdefault("target_keys", ())
        fields.setdefault("target_nodes", ())
        fields.setdefault("source_identity", "")
        fields.setdefault("manifest_sha256", "")
        fields.setdefault("child_closure_sha256", "")
        fields.setdefault("row_content_identity", "")
        fields.setdefault("tensor_identities", ())
        fields.setdefault("mode", "")
        fields.setdefault("parent_identity", "")
        fields.setdefault("_provenance", None)
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_sealed", token)
        instance._validate()
        return instance

    def _validate(self) -> None:
        if self._sealed is not _SUPPORT_SEAL:
            raise TypeError("support record is not sealed")
        if self.keys != tuple(self.keys) or tuple(sorted(self.keys)) != self.keys:
            raise ValueError("support keys must be sorted and ordered")
        if len(set(self.keys)) != len(self.keys) or self.key_count != len(self.keys):
            raise ValueError("support keys must be unique and cardinality-bound")
        if self.node_order != tuple(ALL_INSTRUMENTS) or self.feature_names != P0_FEATURE_NAMES:
            raise ValueError("support record node/feature order is not canonical")
        if self.target_keys or self.target_nodes:
            if len(self.target_nodes) != len(self.target_keys):
                raise ValueError("support target metadata must align")
            target_key_set = set(self.target_keys)
            expected_targets = tuple(
                (timestamp, instrument)
                for timestamp in self.keys
                for instrument in self.node_order
                if f"{instrument}|{timestamp}" in target_key_set
            )
            actual_targets = tuple(
                (key.split("|", 1)[1], key.split("|", 1)[0])
                for key in self.target_keys
                if "|" in key
            )
            if (
                len(actual_targets) != len(self.target_keys)
                or actual_targets != expected_targets
                or len(set(self.target_keys)) != len(self.target_keys)
                or self.target_nodes
                != tuple(self.node_order.index(instrument) for _, instrument in actual_targets)
            ):
                raise ValueError("support target metadata is not canonical")
        if not isinstance(self.contract_identity, str) or len(self.contract_identity) != 64:
            raise ValueError("support record contract is not canonical")
        if self.lookback_minutes < 0:
            raise ValueError("support lookback must be non-negative")
        for name, identity in (
            ("source_identity", self.source_identity),
            ("manifest_sha256", self.manifest_sha256),
            ("child_closure_sha256", self.child_closure_sha256),
            ("row_content_identity", self.row_content_identity),
        ):
            if not isinstance(identity, str) or len(identity) != 64:
                raise ValueError(f"support record {name} is not authenticated")
        if (
            not isinstance(self.parent_identity, str)
            or len(self.parent_identity) != 40
            or any(character not in "0123456789abcdef" for character in self.parent_identity)
        ):
            raise ValueError("support record parent identity must be a 40-hex code identity")
        from .runtime import PARENT_IDENTITY

        if self.parent_identity != PARENT_IDENTITY:
            raise ValueError("support record parent identity is not canonical")
        if self.mode not in {"PRIMARY", "SMOKE"}:
            raise ValueError("support record provenance mode is invalid")
        expected_provenance = (
            _PRIMARY_SUPPORT_CAPABILITY if self.mode == "PRIMARY" else _SMOKE_SUPPORT_CAPABILITY
        )
        if self._provenance is not expected_provenance:
            raise TypeError("support record provenance capability is invalid")
        if self.tensor_identities and tuple(key for key, _ in self.tensor_identities) != self.keys:
            raise ValueError("support tensor identities are not aligned with support keys")
        expected_hash = hashlib.sha256(_canonical_bytes(self.keys)).hexdigest()
        if self.ordered_key_sha256 != expected_hash:
            raise ValueError("support record key digest does not match keys")

    @property
    def identity(self) -> str:
        payload = {
            "keys": self.keys,
            "ordered_key_sha256": self.ordered_key_sha256,
            "key_count": self.key_count,
            "contract_identity": self.contract_identity,
            "lookback_minutes": self.lookback_minutes,
            "node_order": self.node_order,
            "feature_names": self.feature_names,
            "target_keys": self.target_keys,
            "target_nodes": self.target_nodes,
            "source_identity": self.source_identity,
            "manifest_sha256": self.manifest_sha256,
            "child_closure_sha256": self.child_closure_sha256,
            "row_content_identity": self.row_content_identity,
            "tensor_identities": self.tensor_identities,
            "mode": self.mode,
            "parent_identity": self.parent_identity,
        }
        return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


_AUTHENTICATED_SUPPORT_CORPUS_SEAL = object()


def _support_source_identity(
    authentication: tuple[str, str, str, str], keys: tuple[str, ...]
) -> str:
    manifest_sha256, child_closure_sha256, source_content_identity, _ = authentication
    return hashlib.sha256(
        _canonical_bytes(
            {
                "manifest_sha256": manifest_sha256,
                "child_closure_sha256": child_closure_sha256,
                "source_content_identity": source_content_identity,
                "support_keys": keys,
            }
        )
    ).hexdigest()


@dataclass(frozen=True)
class AuthenticatedSupportCorpus:
    """Sealed canonical parent rows and their one-time authentication result."""

    supplied_rows: tuple[Mapping[str, Any], ...]
    authentication: tuple[str, str, str, str]
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _AUTHENTICATED_SUPPORT_CORPUS_SEAL:
            raise TypeError("authenticated support corpus is not sealed")
        if not self.supplied_rows:
            raise ValueError("authenticated support corpus cannot be empty")


@dataclass(frozen=True)
class SupportInputCapability:
    """Authenticated support eligibility and source inputs before tensor materialisation."""

    support: SupportRecord

    def __post_init__(self) -> None:
        self.support._validate()
        if self.support.tensor_identities:
            raise ValueError("support input capability must not claim tensor identities")

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            _canonical_bytes({"schema": "R4-P0-SUPPORT-INPUT-V1", "support": self.support.identity})
        ).hexdigest()


def bind_support_tensor_identities(
    capability: SupportInputCapability,
    tensor_identities: Mapping[str, str],
) -> SupportRecord:
    """Finalise support from one fully authenticated raw-store identity table."""
    support = capability.support
    ordered = tuple((key, tensor_identities[key]) for key in support.keys)
    if tuple(tensor_identities) != support.keys:
        raise ValueError("raw tensor identity capability does not exactly match support keys")
    return SupportRecord._create(
        token=_SUPPORT_SEAL,
        keys=support.keys,
        ordered_key_sha256=support.ordered_key_sha256,
        key_count=support.key_count,
        contract_identity=support.contract_identity,
        lookback_minutes=support.lookback_minutes,
        node_order=support.node_order,
        feature_names=support.feature_names,
        target_keys=support.target_keys,
        target_nodes=support.target_nodes,
        source_identity=support.source_identity,
        manifest_sha256=support.manifest_sha256,
        child_closure_sha256=support.child_closure_sha256,
        row_content_identity=support.row_content_identity,
        tensor_identities=ordered,
        mode=support.mode,
        parent_identity=support.parent_identity,
        _provenance=support._provenance,
    )


_SUPPORT_COLUMNS = frozenset(
    {"instrument_id", "decision_time", "block", "target_valid", "target_available_at"}
)
_SUPPORT_ALLOWED_COLUMNS = _TENSOR_COLUMNS | _SUPPORT_COLUMNS


def _reject_unknown_support_columns(rows: Any) -> None:
    if hasattr(rows, "columns"):
        unknown = set(cast(Iterable[str], rows.columns)) - _SUPPORT_ALLOWED_COLUMNS
        if unknown:
            raise ValueError(
                "candidate support rows contain non-canonical or outcome-bearing columns: "
                f"{sorted(unknown)}"
            )
    elif isinstance(rows, Mapping):
        unknown = set(rows) - _SUPPORT_ALLOWED_COLUMNS
        if unknown:
            raise ValueError(
                "candidate support rows contain non-canonical or outcome-bearing columns: "
                f"{sorted(unknown)}"
            )


def _require_reiterable(rows: Any) -> None:
    if isinstance(rows, Mapping) or hasattr(rows, "iter_rows"):
        return
    iterator = iter(rows)
    if iterator is rows:
        raise ValueError("candidate-independent support requires a re-iterable row source")


def _canonical_row_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        return [_canonical_row_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _canonical_row_value(item) for key, item in value.items()}
    return value


def _assert_authenticated_parent_rows(
    rows: Sequence[Mapping[str, Any]],
    parent: Any,
    *,
    allow_terminal: bool = False,
    terminal_metadata: Any | None = None,
    terminal_prediction_input: Any | None = None,
    outcome_free: bool = False,
) -> tuple[str, str, str, str]:
    from .foundation import _LAB0_SEAL, Lab0Capsule, load_parent_rows
    from .terminal_support import (
        AuthenticatedTerminalMetadata,
        AuthenticatedTerminalPredictionInput,
    )

    unknown = set().union(*(row.keys() for row in rows)) - _SUPPORT_ALLOWED_COLUMNS
    if unknown:
        raise ValueError(
            "candidate support rows contain non-canonical or outcome-bearing columns: "
            f"{sorted(unknown)}"
        )
    if (
        not isinstance(parent, Lab0Capsule)
        or getattr(parent, "_authenticated_parent", None) is not _LAB0_SEAL
    ):
        raise TypeError("candidate support requires an authenticated LAB-0 parent")
    terminal_parent_identity = parent.child_closure_sha256
    if allow_terminal:
        if terminal_prediction_input is not None:
            if not isinstance(terminal_prediction_input, AuthenticatedTerminalPredictionInput):
                raise TypeError("terminal support requires authenticated terminal prediction input")
            terminal_capability = terminal_prediction_input
            terminal_rows = tuple(terminal_capability.to_rows())
            source_content_identity = terminal_capability.content_identity
            terminal_parent_identity = terminal_capability.parent_identity
        else:
            if not isinstance(terminal_metadata, AuthenticatedTerminalMetadata):
                raise TypeError("terminal support requires authenticated terminal metadata")
            terminal_capability = terminal_metadata
            terminal_rows = tuple(terminal_capability.to_rows())
            source_content_identity = terminal_capability.content_identity
            terminal_parent_identity = terminal_capability.parent_identity
        parent_frame = load_parent_rows(parent, include_invalid=True, include_return=False)
        if "target_return" in parent_frame.columns:
            raise ValueError("outcome-free terminal history unexpectedly contains target_return")
        parent_rows = tuple(parent_frame.iter_rows(named=True))
        authenticated_rows = parent_rows + terminal_rows
    else:
        if terminal_metadata is not None:
            raise TypeError("terminal metadata is only valid for terminal support")
        parent_frame = load_parent_rows(parent, include_return=not outcome_free)
        if outcome_free and "target_return" in parent_frame.columns:
            raise ValueError(
                "outcome-free authenticated parent unexpectedly contains target_return"
            )
        authenticated_rows = tuple(parent_frame.iter_rows(named=True))
    by_key = {
        (str(row["instrument_id"]), _as_utc(row["decision_time"])): row
        for row in authenticated_rows
    }
    for row in rows:
        key = (str(row["instrument_id"]), _as_utc(row["decision_time"]))
        expected = by_key.get(key)
        if expected is None:
            raise ValueError("candidate support row is not present in authenticated parent")
        if allow_terminal and str(expected.get("block")) not in (
            "TRAINING_ONLY",
            "DEV_1",
            "DEV_2",
            "DEV_3",
            "TERMINAL_FORMER_HOLDOUT",
        ):
            raise ValueError("terminal support capability row has an unauthorised block")
        for column in _SUPPORT_ALLOWED_COLUMNS:
            if column not in expected:
                raise ValueError(f"authenticated parent lacks canonical support column: {column}")
            if _canonical_row_value(row.get(column)) != _canonical_row_value(expected[column]):
                raise ValueError(
                    f"candidate support row content differs from authenticated parent: {column}"
                )
    if allow_terminal:
        ordered_rows = sorted(
            authenticated_rows,
            key=lambda item: (str(item["decision_time"]), str(item["instrument_id"])),
        )
        source_content_identity = hashlib.sha256(
            _canonical_bytes(
                [
                    {
                        column: _canonical_row_value(row[column])
                        for column in sorted(_SUPPORT_ALLOWED_COLUMNS)
                    }
                    for row in ordered_rows
                ]
            )
        ).hexdigest()
        return (
            parent.manifest_sha256,
            parent.child_closure_sha256,
            source_content_identity,
            terminal_parent_identity,
        )
    canonical_rows = [
        {column: _canonical_row_value(row[column]) for column in sorted(_SUPPORT_ALLOWED_COLUMNS)}
        for row in sorted(
            authenticated_rows,
            key=lambda item: (str(item["decision_time"]), str(item["instrument_id"])),
        )
    ]
    content_identity = hashlib.sha256(_canonical_bytes(canonical_rows)).hexdigest()
    from .runtime import PARENT_IDENTITY

    return parent.manifest_sha256, parent.child_closure_sha256, content_identity, PARENT_IDENTITY


def authenticate_support_corpus(
    rows: Iterable[Mapping[str, Any]] | Any,
    authenticated_parent: Any,
    *,
    terminal_metadata: Any | None = None,
    terminal_prediction_input: Any | None = None,
    allow_terminal: bool = False,
    outcome_free: bool = False,
) -> AuthenticatedSupportCorpus:
    """Authenticate canonical parent rows once for all support projections."""
    _reject_unknown_support_columns(rows)
    _require_reiterable(rows)
    supplied_rows = tuple(_rows_dicts(rows))
    if not supplied_rows:
        raise ValueError("candidate-independent support requires authenticated rows")
    if allow_terminal:
        authentication = _assert_authenticated_parent_rows(
            supplied_rows,
            authenticated_parent,
            allow_terminal=True,
            terminal_metadata=terminal_metadata,
            terminal_prediction_input=terminal_prediction_input,
        )
    elif terminal_metadata is None:
        authentication = _assert_authenticated_parent_rows(
            supplied_rows,
            authenticated_parent,
            **({"outcome_free": True} if outcome_free else {}),
        )
    else:
        authentication = _assert_authenticated_parent_rows(
            supplied_rows,
            authenticated_parent,
            terminal_metadata=terminal_metadata,
            terminal_prediction_input=terminal_prediction_input,
            **({"outcome_free": True} if outcome_free else {}),
        )
    return AuthenticatedSupportCorpus(
        supplied_rows=supplied_rows,
        authentication=authentication,
        _seal=_AUTHENTICATED_SUPPORT_CORPUS_SEAL,
    )


def candidate_independent_support(
    rows: Iterable[Mapping[str, Any]] | Any,
    decision_times: Sequence[datetime],
    *,
    contract: TensorContract | None = None,
    candidate_id: str | None = None,
    authenticated_parent: Any | None = None,
    terminal_metadata: Any | None = None,
    terminal_prediction_input: Any | None = None,
    _synthetic: bool = False,
    _eligible_blocks: frozenset[str] = frozenset({"DEV_2", "DEV_3"}),
    _allow_terminal: bool = False,
    _outcome_free: bool = False,
    _authenticated_corpus: AuthenticatedSupportCorpus | None = None,
    _defer_tensor_identities: bool = False,
) -> SupportRecord:
    """Return support bound to authenticated parent; smoke uses the explicit helper."""
    del candidate_id
    active_contract = contract or TensorContract()
    if _authenticated_corpus is None:
        _reject_unknown_support_columns(rows)
        _require_reiterable(rows)
        supplied_rows = tuple(_rows_dicts(rows))
    else:
        if _synthetic:
            raise TypeError("synthetic support cannot use an authenticated support corpus")
        if _authenticated_corpus._seal is not _AUTHENTICATED_SUPPORT_CORPUS_SEAL:
            raise TypeError("authenticated support corpus is not sealed")
        supplied_rows = _authenticated_corpus.supplied_rows
    if not supplied_rows:
        raise ValueError("candidate-independent support requires authenticated rows")
    unknown = set().union(*(row.keys() for row in supplied_rows)) - _SUPPORT_ALLOWED_COLUMNS
    if unknown:
        raise ValueError(
            "candidate support rows contain non-canonical or outcome-bearing columns: "
            f"{sorted(unknown)}"
        )
    required = _SUPPORT_ALLOWED_COLUMNS.difference(supplied_rows[0])
    if required:
        raise ValueError(
            f"candidate-independent support lacks required metadata: {sorted(required)}"
        )
    if _synthetic:
        manifest_sha256 = hashlib.sha256(b"SMOKE_MANIFEST").hexdigest()
        child_closure_sha256 = hashlib.sha256(b"SMOKE_CLOSURE").hexdigest()
        from .runtime import PARENT_IDENTITY

        parent_identity = PARENT_IDENTITY
        source_content_identity = hashlib.sha256(
            _canonical_bytes(
                [
                    {column: _canonical_row_value(value) for column, value in row.items()}
                    for row in supplied_rows
                ]
            )
        ).hexdigest()
        mode = "SMOKE"
        provenance = _SMOKE_SUPPORT_CAPABILITY
    else:
        if _authenticated_corpus is None:
            if authenticated_parent is None:
                raise TypeError(
                    "candidate-independent support requires authenticated parent capability"
                )
            corpus = authenticate_support_corpus(
                supplied_rows,
                authenticated_parent,
                allow_terminal=_allow_terminal,
                terminal_metadata=terminal_metadata,
                terminal_prediction_input=terminal_prediction_input,
                outcome_free=_outcome_free,
            )
            auth = corpus.authentication
        else:
            auth = _authenticated_corpus.authentication
        (
            manifest_sha256,
            child_closure_sha256,
            source_content_identity,
            parent_identity,
        ) = auth
        mode = "PRIMARY"
        provenance = _PRIMARY_SUPPORT_CAPABILITY
    requested = tuple(sorted(_as_utc(timestamp) for timestamp in decision_times))
    if not requested:
        raise ValueError("candidate-independent support requires decision times")
    if len(set(requested)) != len(requested):
        raise ValueError("candidate-independent support rejects duplicate decision times")
    requested_set = set(requested)
    eligible: set[tuple[str, datetime]] = set()
    seen: set[tuple[str, datetime]] = set()
    for row in supplied_rows:
        instrument = row["instrument_id"]
        timestamp = _as_utc(row["decision_time"])
        key = (instrument, timestamp)
        if timestamp not in requested_set:
            continue
        if key in seen:
            raise ValueError(f"duplicate support key: {instrument} at {timestamp.isoformat()}")
        seen.add(key)
        if row.get("block") not in _eligible_blocks:
            continue
        if row.get("block") in _STAGE_WINDOWS:
            start, end = _STAGE_WINDOWS[str(row["block"])]
            if not start <= timestamp < end:
                raise ValueError(
                    "candidate-independent support decision time is outside its stage window"
                )
        target_available_at = row.get("target_available_at")
        if row.get("target_valid") is not True and row.get("target_valid") != 1:
            continue
        mature_at = None if target_available_at is None else _as_utc(target_available_at)
        if (
            mature_at is None
            or mature_at <= timestamp
            or (mature_at >= TERMINAL_START and not _allow_terminal)
        ):
            continue
        eligible.add(key)
    min_needed = requested[0] - timedelta(minutes=active_contract.lookback_minutes)
    max_needed = requested[-1]
    tensor_rows: dict[tuple[str, datetime], Mapping[str, Any]] = {}
    for row in supplied_rows:
        instrument = row["instrument_id"]
        timestamp = _as_utc(row["decision_time"])
        if (
            instrument not in active_contract.node_order
            or timestamp < min_needed
            or timestamp > max_needed
        ):
            continue
        key = (instrument, timestamp)
        if key in tensor_rows:
            raise ValueError(f"duplicate tensor key: {instrument} at {timestamp.isoformat()}")
        tensor_rows[key] = row
    keys = []
    target_keys = []
    target_nodes = []
    tensor_identities = []
    for timestamp in requested:
        sequence = None
        if not _defer_tensor_identities:
            window_rows = tuple(
                row
                for offset in range(active_contract.lookback_minutes + 1)
                for instrument in active_contract.node_order
                if (row := tensor_rows.get((instrument, timestamp - timedelta(minutes=offset))))
                is not None
            )
            sequence = build_masked_sequence(window_rows, timestamp, contract=active_contract)
        available = {
            instrument
            for instrument in active_contract.node_order
            if (instrument, timestamp) in eligible
        }
        if not available:
            continue
        key = timestamp.isoformat()
        keys.append(key)
        if sequence is not None:
            tensor_identities.append((key, _masked_tensor_identity(sequence)))
        for instrument in active_contract.node_order:
            if instrument in available:
                target_keys.append(f"{instrument}|{timestamp.isoformat()}")
                target_nodes.append(active_contract.node_order.index(instrument))
    if not keys:
        raise ValueError("candidate-independent support is empty after causal eligibility checks")
    ordered_hash = hashlib.sha256(_canonical_bytes(keys)).hexdigest()
    row_content_identity = hashlib.sha256(
        _canonical_bytes(
            [
                {column: _canonical_row_value(value) for column, value in row.items()}
                for row in supplied_rows
            ]
        )
    ).hexdigest()
    source_identity = _support_source_identity(
        (manifest_sha256, child_closure_sha256, source_content_identity, parent_identity),
        tuple(keys),
    )
    return SupportRecord._create(
        token=_SUPPORT_SEAL,
        keys=tuple(keys),
        ordered_key_sha256=ordered_hash,
        key_count=len(keys),
        contract_identity=active_contract.identity,
        lookback_minutes=active_contract.lookback_minutes,
        node_order=active_contract.node_order,
        feature_names=active_contract.feature_names,
        target_keys=tuple(target_keys),
        target_nodes=tuple(target_nodes),
        source_identity=source_identity,
        manifest_sha256=manifest_sha256,
        child_closure_sha256=child_closure_sha256,
        row_content_identity=row_content_identity,
        tensor_identities=tuple(tensor_identities),
        mode=mode,
        parent_identity=parent_identity,
        _provenance=provenance,
    )


def candidate_independent_support_input(
    rows: Iterable[Mapping[str, Any]] | Any,
    decision_times: Sequence[datetime],
    **kwargs: Any,
) -> SupportInputCapability:
    """Authenticate eligibility/source inputs without constructing tensor sequences."""
    corpus = kwargs.pop("_authenticated_corpus", None)
    if corpus is None and not kwargs.get("_synthetic", False):
        authenticated_parent = kwargs.get("authenticated_parent")
        if authenticated_parent is None:
            raise TypeError(
                "candidate-independent support requires authenticated parent capability"
            )
        corpus = authenticate_support_corpus(
            rows,
            authenticated_parent,
            allow_terminal=kwargs.get("_allow_terminal", False),
            terminal_metadata=kwargs.get("terminal_metadata"),
            terminal_prediction_input=kwargs.get("terminal_prediction_input"),
            outcome_free=kwargs.get("_outcome_free", False),
        )
    support = candidate_independent_support(
        rows,
        decision_times,
        _authenticated_corpus=corpus,
        _defer_tensor_identities=True,
        **kwargs,
    )
    return SupportInputCapability(support)


def support_for_candidates(
    rows: Iterable[Mapping[str, Any]] | Any,
    decision_times: Sequence[datetime],
    candidate_ids: Sequence[str],
    *,
    contract: TensorContract | None = None,
    authenticated_parent: Any | None = None,
) -> dict[str, SupportRecord]:
    """Materialise one authenticated support record and reuse it for every candidate."""
    support = candidate_independent_support(
        rows, decision_times, contract=contract, authenticated_parent=authenticated_parent
    )
    return {candidate: support for candidate in candidate_ids}


def candidate_independent_support_smoke(
    rows: Iterable[Mapping[str, Any]] | Any,
    decision_times: Sequence[datetime],
    *,
    contract: TensorContract | None = None,
) -> SupportRecord:
    """Build synthetic support for smoke tests only."""
    return candidate_independent_support(rows, decision_times, contract=contract, _synthetic=True)


_PREPROCESSOR_SEAL = object()
_PRIMARY_PREPROCESSOR_CAPABILITY = object()
_SMOKE_PREPROCESSOR_CAPABILITY = object()
_AUTHENTICATED_PREPROCESSOR_RECEIPT = object()
_PREPROCESSOR_REDUCTION_ALGORITHM = "R4.D.CHUNKED_FLOAT64_V1"


@dataclass(frozen=True, init=False)
class FittedTrainingPreprocessor:
    means: np.ndarray
    scales: np.ndarray
    feature_names: tuple[str, ...]
    training_cutoff: datetime
    contract_identity: str
    fit_partition: str = "TRAINING_ONLY"
    binary_features: tuple[bool, ...] = ()
    training_partition_identity: str = ""
    mode: str = ""
    reduction_algorithm: str = _PREPROCESSOR_REDUCTION_ALGORITHM
    _provenance: object = None
    _partition_capability: object = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("fitted preprocessors must be created by an authenticated factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> FittedTrainingPreprocessor:
        if token is not _PREPROCESSOR_SEAL:
            raise TypeError("preprocessor seal is private")
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        instance.__post_init__()
        return instance

    def __post_init__(self) -> None:
        if not self.training_partition_identity:
            raise ValueError("preprocessor requires an authenticated training partition identity")
        if len(self.training_partition_identity) != 64 or any(
            character not in "0123456789abcdef" for character in self.training_partition_identity
        ):
            raise ValueError("training partition identity must be a lowercase SHA-256 identity")
        if self.fit_partition != "TRAINING_ONLY":
            raise ValueError("preprocessor identity must remain training-only")
        if self.mode not in {"PRIMARY", "SMOKE"}:
            raise ValueError("preprocessor provenance mode is invalid")
        if self.reduction_algorithm != _PREPROCESSOR_REDUCTION_ALGORITHM:
            raise ValueError("preprocessor reduction algorithm is not canonical")
        expected_provenance = (
            _PRIMARY_PREPROCESSOR_CAPABILITY
            if self.mode == "PRIMARY"
            else _SMOKE_PREPROCESSOR_CAPABILITY
        )
        if self._provenance is not expected_provenance:
            raise TypeError("preprocessor provenance capability is invalid")
        if self.mode == "PRIMARY":
            if self._partition_capability is _AUTHENTICATED_PREPROCESSOR_RECEIPT:
                pass
            elif not isinstance(self._partition_capability, TrainingTensorPartition):
                raise TypeError("PRIMARY preprocessor requires an authenticated training partition")
            else:
                self._partition_capability._validate()
                if self._partition_capability.seal != self.training_partition_identity:
                    raise ValueError("preprocessor partition identity does not match capability")
        elif self._partition_capability is not None:
            raise TypeError("SMOKE preprocessor cannot carry primary partition capability")
        cutoff = _as_utc(self.training_cutoff)
        if cutoff >= TERMINAL_START:
            raise ValueError("preprocessor cutoff must precede terminal boundary")
        if self.feature_names != P0_FEATURE_NAMES:
            raise ValueError("preprocessor feature order must be canonical")
        valid_contract_identities = {
            TensorContract(lookback_minutes=lookback).identity for lookback in range(0, 241)
        }
        if self.contract_identity not in valid_contract_identities:
            raise ValueError("preprocessor contract identity must match canonical TensorContract")
        if self.means.ndim != 1 or self.scales.ndim != 1 or self.means.shape != self.scales.shape:
            raise ValueError("preprocessor statistics must be one-dimensional")
        if self.binary_features:
            if len(self.binary_features) != self.means.shape[0]:
                raise ValueError("binary feature classification must match feature count")
        else:
            object.__setattr__(
                self,
                "binary_features",
                tuple(feature in BINARY_FEATURE_NAMES for feature in self.feature_names),
            )
        if not np.isfinite(self.means).all() or not np.isfinite(self.scales).all():
            raise ValueError("preprocessor statistics must be finite")
        if np.any(self.scales == 0):
            raise ValueError("preprocessor scales must be non-zero")
        for array in (self.means, self.scales):
            array.setflags(write=False)

    def _validate(self) -> None:
        self.__post_init__()

    def transform(self, tensor: MaskedTensor) -> MaskedTensor:
        if tensor.contract_identity != self.contract_identity:
            raise ValueError("tensor contract does not match fitted preprocessor")
        if tensor.values.shape[-1] != self.means.shape[0]:
            raise ValueError("feature dimension does not match fitted preprocessor")
        transformed = np.array(tensor.values, dtype=float, copy=True)
        for feature_index, (mean, scale, is_binary) in enumerate(
            zip(self.means, self.scales, self.binary_features, strict=True)
        ):
            feature_values = transformed[..., feature_index]
            observed = tensor.value_mask[..., feature_index]
            if is_binary:
                feature_values[~observed] = np.nan
            else:
                feature_values[~observed] = mean
                feature_values[...] = (feature_values[...] - mean) / scale
        if not np.isfinite(transformed[tensor.value_mask]).all():
            raise ValueError("preprocessor transform produced non-finite observed values")
        return MaskedTensor(
            values=transformed,
            value_mask=tensor.value_mask.copy(),
            availability_mask=tensor.availability_mask.copy(),
            node_mask=tensor.node_mask.copy(),
            decision_time=tensor.decision_time,
            contract_identity=tensor.contract_identity,
            contract=tensor.contract,
        )


def load_authenticated_training_preprocessor(
    fields: Mapping[str, Any],
) -> FittedTrainingPreprocessor:
    """Restore statistics from a self-authenticated preparation receipt."""
    payload = dict(fields)
    expected_identity = str(payload.pop("preprocessor_identity"))
    preprocessor = FittedTrainingPreprocessor._create(
        token=_PREPROCESSOR_SEAL,
        means=np.asarray(payload["means"], dtype=np.float64),
        scales=np.asarray(payload["scales"], dtype=np.float64),
        feature_names=tuple(payload["feature_names"]),
        training_cutoff=datetime.fromisoformat(payload["training_cutoff"]),
        contract_identity=str(payload["contract_identity"]),
        fit_partition=str(payload["fit_partition"]),
        binary_features=tuple(payload["binary_features"]),
        training_partition_identity=str(payload["training_partition_identity"]),
        mode=str(payload["mode"]),
        reduction_algorithm=str(payload["reduction_algorithm"]),
        _provenance=_PRIMARY_PREPROCESSOR_CAPABILITY,
        _partition_capability=_AUTHENTICATED_PREPROCESSOR_RECEIPT,
    )
    from .runtime import training_preprocessor_identity

    if training_preprocessor_identity(preprocessor) != expected_identity:
        raise ValueError("authenticated preprocessor receipt identity drifted")
    return preprocessor


def _masked_tensor_identity(tensor: MaskedTensor) -> str:
    payload = b"|".join(
        (
            tensor.contract_identity.encode(),
            tensor.decision_time.isoformat().encode(),
            hashlib.sha256(tensor.values.tobytes()).hexdigest().encode(),
            hashlib.sha256(tensor.value_mask.tobytes()).hexdigest().encode(),
            hashlib.sha256(tensor.availability_mask.tobytes()).hexdigest().encode(),
            hashlib.sha256(tensor.node_mask.tobytes()).hexdigest().encode(),
        )
    )
    return hashlib.sha256(payload).hexdigest()


_TRAINING_PARTITION_SEAL = object()
_TRAINING_PARTITION_BUILDER_CAPABILITY = object()


class LazyTensorSequence(Sequence[MaskedTensor]):
    """Re-iterable ordered provider that materialises at most one tensor per access."""

    def __init__(self, keys: Sequence[str], factory: Any) -> None:
        self._keys = tuple(keys)
        self._factory = factory

    def __len__(self) -> int:
        return len(self._keys)

    def __getitem__(self, index: int | slice) -> Any:
        if isinstance(index, slice):
            return tuple(self._factory(key) for key in self._keys[index])
        return self._factory(self._keys[index])

    def __iter__(self) -> Iterator[MaskedTensor]:
        for key in self._keys:
            yield self._factory(key)


class LazyTensorMapping(Mapping[str, MaskedTensor]):
    """Mapping-compatible lazy provider preserving canonical key order."""

    def __init__(
        self,
        keys: Sequence[str],
        factory: Any,
        *,
        worker_rows: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        self._keys = tuple(keys)
        self._key_index = {key: position for position, key in enumerate(self._keys)}
        if len(self._key_index) != len(self._keys):
            raise ValueError("lazy tensor keys must be unique")
        self._factory = factory
        self._worker_rows = worker_rows

    def worker_rows(self) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        if self._worker_rows is None:
            raise TypeError("lazy tensor source has no explicit worker-row representation")
        return self._worker_rows

    def __len__(self) -> int:
        return len(self._keys)

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __getitem__(self, key: str) -> MaskedTensor:
        if key not in self._key_index:
            raise KeyError(key)
        return self._factory(key)

    def __contains__(self, key: object) -> bool:
        return key in self._key_index

    def items(self) -> Any:
        for key in self._keys:
            yield key, self._factory(key)


@dataclass(frozen=True, init=False)
class TrainingTensorPartition:
    """Authenticated ordered tensor set used to fit PRIMARY preprocessing."""

    tensors: Sequence[MaskedTensor]
    row_keys: tuple[str, ...]
    source_identity: str
    support_identity: str
    training_cutoff: datetime
    tensor_identities: tuple[str, ...]
    seal: str

    def __init__(
        self,
        tensors: Sequence[MaskedTensor],
        row_keys: tuple[str, ...],
        source_identity: str,
        support_identity: str,
        training_cutoff: datetime,
        tensor_identities: tuple[str, ...] | None = None,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _TRAINING_PARTITION_SEAL:
            raise TypeError("training partition must be authenticated")
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "tensors", tensors)
        object.__setattr__(self, "row_keys", tuple(row_keys))
        object.__setattr__(self, "source_identity", source_identity)
        object.__setattr__(self, "support_identity", support_identity)
        cutoff = _as_utc(training_cutoff)
        object.__setattr__(self, "training_cutoff", cutoff)
        sealed_identities = (
            tuple(_masked_tensor_identity(tensor) for tensor in tensors)
            if tensor_identities is None
            else tuple(tensor_identities)
        )
        object.__setattr__(self, "tensor_identities", sealed_identities)
        payload = {
            "row_keys": tuple(row_keys),
            "source_identity": source_identity,
            "support_identity": support_identity,
            "training_cutoff": cutoff.isoformat(),
            "tensor_identities": self.tensor_identities,
        }
        object.__setattr__(self, "seal", hashlib.sha256(_canonical_bytes(payload)).hexdigest())
        from .tensor_store import _PreparationTensorSequence

        if isinstance(tensors, _PreparationTensorSequence):
            tensors._authenticate(self.row_keys, self.tensor_identities, self.source_identity)
            object.__setattr__(self, "_preparation_sequence", tensors)
            self._validate_metadata()
            previous: datetime | None = None
            for key in self.row_keys:
                timestamp = _as_utc(datetime.fromisoformat(key))
                if timestamp > cutoff or (previous is not None and timestamp <= previous):
                    raise ValueError("training partition timestamps violate chronology or cutoff")
                previous = timestamp
            if cutoff >= TERMINAL_START:
                raise ValueError("training partition cutoff must precede terminal boundary")
        else:
            self._validate()

    @property
    def tensor(self) -> MaskedTensor:
        """Compatibility view of the first tensor; preprocessing uses ``tensors``."""
        if not self.tensors:
            raise ValueError("training partition tensor set is empty")
        return self.tensors[0]

    def _validate(self) -> None:
        self._validate_metadata()
        previous: datetime | None = None
        for tensor in self.tensors:
            if not isinstance(tensor, MaskedTensor):
                raise TypeError("training partition requires authenticated tensors")
            if tensor.contract_identity != TensorContract().identity:
                raise ValueError("training partition tensor contract is not canonical")
            if tensor.decision_time > self.training_cutoff:
                raise ValueError("training partition contains rows after its cutoff")
            if self.training_cutoff >= TERMINAL_START:
                raise ValueError("training partition cutoff must precede terminal boundary")
            if previous is not None and tensor.decision_time <= previous:
                raise ValueError("training partition tensors must be chronologically ordered")
            previous = tensor.decision_time

    def _validate_metadata(self) -> None:
        if not self.tensors:
            raise ValueError("training partition tensor set must be non-empty")
        if len(self.tensors) != len(self.row_keys):
            raise ValueError("training partition tensors and row keys must align")
        if len(self.tensor_identities) != len(self.tensors):
            raise ValueError("training partition identities and tensors must align")
        if len(self.row_keys) != len(set(self.row_keys)):
            raise ValueError("training partition row keys must be unique")
        for name, value in (
            ("source_identity", self.source_identity),
            ("support_identity", self.support_identity),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"training partition {name} must be a lowercase SHA-256 identity")
        payload = {
            "row_keys": self.row_keys,
            "source_identity": self.source_identity,
            "support_identity": self.support_identity,
            "training_cutoff": self.training_cutoff.isoformat(),
            "tensor_identities": self.tensor_identities,
        }
        if self.seal != hashlib.sha256(_canonical_bytes(payload)).hexdigest():
            raise ValueError("training partition seal mismatch")


def build_training_tensor_partition(
    tensor: MaskedTensor | Sequence[MaskedTensor],
    *,
    row_keys: tuple[str, ...],
    source_identity: str,
    support_identity: str,
    training_cutoff: datetime,
    support: SupportRecord | None = None,
    authenticated_parent: Any | None = None,
    tensor_identities: tuple[str, ...] | None = None,
    _capability: object | None = None,
) -> TrainingTensorPartition:
    if _capability is not _TRAINING_PARTITION_BUILDER_CAPABILITY:
        raise TypeError("training partition requires authenticated parent/support capability")
    if support is None or authenticated_parent is None:
        raise TypeError("training partition requires authenticated support and parent")
    from .foundation import _LAB0_SEAL, Lab0Capsule

    if not isinstance(support, SupportRecord):
        raise TypeError("training partition support must be an authenticated SupportRecord")
    support._validate()
    if (
        support.contract_identity != TensorContract().identity
        or support.lookback_minutes != TensorContract().lookback_minutes
    ):
        raise ValueError("training partition support contract is not canonical")
    if (
        not isinstance(authenticated_parent, Lab0Capsule)
        or getattr(authenticated_parent, "_authenticated_parent", None) is not _LAB0_SEAL
    ):
        raise TypeError("training partition parent must be authenticated")
    tensors: Sequence[MaskedTensor] = (tensor,) if isinstance(tensor, MaskedTensor) else tensor
    if tuple(row_keys) != support.keys:
        raise ValueError("training partition row keys do not match authenticated support")
    if len(tensors) != len(row_keys):
        raise ValueError("training partition tensor set must cover every support key")
    if support_identity != support.identity:
        raise ValueError("training partition support identity does not match support")
    if source_identity != support.source_identity:
        raise ValueError("training partition source identity does not match support")
    if support.manifest_sha256 != authenticated_parent.manifest_sha256:
        raise ValueError("training partition manifest does not match authenticated parent")
    if support.child_closure_sha256 != authenticated_parent.child_closure_sha256:
        raise ValueError("training partition child closure does not match authenticated parent")
    expected = tuple(identity for _, identity in support.tensor_identities)
    if tensor_identities is None:
        tensor_identities = tuple(_masked_tensor_identity(item) for item in tensors)
    if tuple(tensor_identities) != expected:
        raise ValueError("training partition tensor content does not match authenticated support")
    return TrainingTensorPartition(
        tensors,
        tuple(row_keys),
        source_identity,
        support_identity,
        training_cutoff,
        tuple(tensor_identities),
        _seal=_TRAINING_PARTITION_SEAL,
    )


def _fit_training_preprocessor_from_tensor(
    training_values: MaskedTensor | Sequence[MaskedTensor],
    *,
    training_cutoff: datetime,
    contract: TensorContract | None = None,
    training_partition_identity: str = "",
    mode: str = "SMOKE",
    provenance: object = _SMOKE_PREPROCESSOR_CAPABILITY,
    partition_capability: TrainingTensorPartition | None = None,
) -> FittedTrainingPreprocessor:
    active_contract = contract or TensorContract()
    cutoff = _as_utc(training_cutoff)
    tensors: Sequence[MaskedTensor] = (
        (training_values,) if isinstance(training_values, MaskedTensor) else training_values
    )
    if not tensors:
        raise ValueError("training preprocessing requires an authenticated tensor set")
    feature_count: int | None = None
    counts: np.ndarray | None = None
    sums: np.ndarray | None = None
    sumsquares: np.ndarray | None = None
    for tensor in tensors:
        if not isinstance(tensor, MaskedTensor):
            raise TypeError("training preprocessing requires authenticated MaskedTensors")
        if tensor.contract_identity != active_contract.identity:
            raise ValueError("training tensor does not match preprocessing contract")
        if tensor.decision_time > cutoff:
            raise ValueError("training tensor is after the declared training cutoff")
        if tensor.values.ndim != 3 or tensor.values.shape[1] != len(ALL_INSTRUMENTS):
            raise ValueError("training values must have shape (time, twenty nodes, features)")
        if tensor.values.shape != tensor.value_mask.shape:
            raise ValueError("training values and value mask must have equal shapes")
        if np.any(tensor.value_mask & ~np.isfinite(tensor.values)):
            raise ValueError("training observations must be finite where value_mask is true")
        if feature_count is None:
            feature_count = tensor.values.shape[-1]
            counts = np.zeros(tensor.values.shape[-1], dtype=np.int64)
            sums = np.zeros(tensor.values.shape[-1], dtype=np.float64)
            sumsquares = np.zeros(tensor.values.shape[-1], dtype=np.float64)
        if tensor.values.shape[-1] != feature_count:
            raise ValueError("training feature dimensions must agree")
        if feature_count is None:
            raise RuntimeError("training preprocessing feature count unavailable")
        assert counts is not None and sums is not None and sumsquares is not None
        for index in range(feature_count):
            observed = tensor.values[..., index][tensor.value_mask[..., index]]
            if observed.size:
                counts[index] += observed.size
                sums[index] += np.sum(observed, dtype=np.float64)
                sumsquares[index] += np.sum(observed * observed, dtype=np.float64)
    if feature_count is None or counts is None or sums is None or sumsquares is None:
        raise RuntimeError("training preprocessing reduction did not observe any tensors")
    if feature_count != len(active_contract.feature_names):
        raise ValueError("training feature count does not match canonical P0 features")
    means = np.zeros(int(feature_count), dtype=float)
    scales = np.ones(int(feature_count), dtype=float)
    for index, feature in enumerate(active_contract.feature_names):
        if feature in BINARY_FEATURE_NAMES:
            continue
        if counts[index]:
            means[index] = sums[index] / counts[index]
            variance = (sumsquares[index] / counts[index]) - means[index] * means[index]
            scales[index] = math.sqrt(max(variance, 0.0)) or 1.0
    binary_features = tuple(
        feature in BINARY_FEATURE_NAMES for feature in active_contract.feature_names
    )
    partition_identity = (
        training_partition_identity
        or hashlib.sha256(
            _canonical_bytes(
                {
                    "smoke": True,
                    "tensors": tuple(_masked_tensor_identity(tensor) for tensor in tensors),
                    "cutoff": cutoff.isoformat(),
                    "reduction_algorithm": _PREPROCESSOR_REDUCTION_ALGORITHM,
                }
            )
        ).hexdigest()
    )
    return FittedTrainingPreprocessor._create(
        token=_PREPROCESSOR_SEAL,
        means=means,
        scales=scales,
        feature_names=active_contract.feature_names,
        training_cutoff=cutoff,
        contract_identity=active_contract.identity,
        binary_features=binary_features,
        training_partition_identity=partition_identity,
        mode=mode,
        reduction_algorithm=_PREPROCESSOR_REDUCTION_ALGORITHM,
        _provenance=provenance,
        _partition_capability=partition_capability,
    )


def fit_training_preprocessors(
    training_partitions: Mapping[str, TrainingTensorPartition],
    *,
    contract: TensorContract | None = None,
) -> dict[str, FittedTrainingPreprocessor]:
    """Fit nested PRIMARY partitions in one pass over their largest tensor stream."""
    from .tensor_store import _PreparationTensorSequence

    if not training_partitions:
        raise ValueError(
            "bulk preprocessing requires at least one authenticated training partition"
        )
    partitions = dict(training_partitions)
    if any(not isinstance(partition, TrainingTensorPartition) for partition in partitions.values()):
        raise TypeError("PRIMARY preprocessing requires authenticated training partitions")
    active_contract = contract or TensorContract()
    if active_contract.identity != TensorContract().identity:
        raise ValueError("PRIMARY preprocessing requires the canonical 60-minute TensorContract")

    largest_name, largest = max(partitions.items(), key=lambda item: len(item[1].row_keys))
    largest_keys = largest.row_keys
    largest_key_set = set(largest_keys)
    largest_identity_by_key = dict(zip(largest_keys, largest.tensor_identities, strict=True))
    memberships: dict[str, set[str]] = {}
    for name, partition in partitions.items():
        if getattr(partition, "_seal", None) is not _TRAINING_PARTITION_SEAL:
            raise TypeError("training partition seal is private")
        preparation_sequence = getattr(partition, "_preparation_sequence", None)
        if isinstance(partition.tensors, _PreparationTensorSequence):
            if preparation_sequence is not partition.tensors:
                raise TypeError("preparation partition capability is missing or mismatched")
            partition.tensors._authenticate(
                partition.row_keys, partition.tensor_identities, partition.source_identity
            )
        elif preparation_sequence is not None:
            raise ValueError("preparation partition tensor provider drifted")
        if _as_utc(partition.training_cutoff) >= TERMINAL_START:
            raise ValueError("training partition cutoff must precede terminal boundary")
        membership = set(partition.row_keys)
        if (
            not partition.row_keys
            or len(partition.row_keys) != len(partition.tensor_identities)
            or len(membership) != len(partition.row_keys)
            or not membership <= largest_key_set
        ):
            raise ValueError(
                "bulk preprocessing partitions must be non-empty nested subsets "
                "of the largest partition"
            )
        expected_order = tuple(key for key in largest_keys if key in membership)
        if partition.row_keys != expected_order:
            raise ValueError("bulk preprocessing partitions must preserve canonical ordering")
        expected_identities = tuple(largest_identity_by_key[key] for key in partition.row_keys)
        if partition.tensor_identities != expected_identities:
            raise ValueError(
                "bulk preprocessing partition tensor identities do not match largest partition"
            )
        payload = {
            "row_keys": partition.row_keys,
            "source_identity": partition.source_identity,
            "support_identity": partition.support_identity,
            "training_cutoff": partition.training_cutoff.isoformat(),
            "tensor_identities": partition.tensor_identities,
        }
        if partition.seal != hashlib.sha256(_canonical_bytes(payload)).hexdigest():
            raise ValueError("training partition seal mismatch")
        memberships[name] = membership

    feature_count = len(active_contract.feature_names)
    accumulators = {
        name: (
            np.zeros(feature_count, dtype=np.int64),
            np.zeros(feature_count, dtype=np.float64),
            np.zeros(feature_count, dtype=np.float64),
        )
        for name in partitions
    }
    observed_keys: set[str] = set()
    previous: datetime | None = None
    for key, tensor in zip(largest_keys, largest.tensors, strict=True):
        if not isinstance(tensor, MaskedTensor):
            raise TypeError("training preprocessing requires authenticated MaskedTensors")
        if tensor.contract_identity != active_contract.identity:
            raise ValueError("training tensor does not match preprocessing contract")
        timestamp = _as_utc(tensor.decision_time)
        if previous is not None and timestamp <= previous:
            raise ValueError("training partition tensors must be chronologically ordered")
        if isinstance(largest.tensors, _PreparationTensorSequence) and key != timestamp.isoformat():
            raise ValueError("preparation tensor decision time does not match its key")
        previous = timestamp
        if tensor.values.ndim != 3 or tensor.values.shape[1] != len(ALL_INSTRUMENTS):
            raise ValueError("training values must have shape (time, twenty nodes, features)")
        if tensor.values.shape != tensor.value_mask.shape:
            raise ValueError("training values and value mask must have equal shapes")
        if tensor.values.shape[-1] != feature_count:
            raise ValueError("training feature count does not match canonical P0 features")
        if not isinstance(largest.tensors, _PreparationTensorSequence) and np.any(
            tensor.value_mask & ~np.isfinite(tensor.values)
        ):
            raise ValueError("training observations must be finite where value_mask is true")
        observed_keys.add(key)
        for name, partition in partitions.items():
            if key not in memberships[name]:
                continue
            if tensor.decision_time > partition.training_cutoff:
                raise ValueError("training tensor is after the declared training cutoff")
            counts, sums, sumsquares = accumulators[name]
            for index in range(feature_count):
                observed = tensor.values[..., index][tensor.value_mask[..., index]]
                if observed.size:
                    counts[index] += observed.size
                    sums[index] += np.sum(observed, dtype=np.float64)
                    sumsquares[index] += np.sum(observed * observed, dtype=np.float64)
    if observed_keys != largest_key_set:
        raise RuntimeError(
            f"bulk preprocessing did not traverse largest partition {largest_name!r}"
        )

    binary_features = tuple(
        feature in BINARY_FEATURE_NAMES for feature in active_contract.feature_names
    )
    fitted: dict[str, FittedTrainingPreprocessor] = {}
    for name, partition in partitions.items():
        counts, sums, sumsquares = accumulators[name]
        means = np.zeros(feature_count, dtype=float)
        scales = np.ones(feature_count, dtype=float)
        for index, feature in enumerate(active_contract.feature_names):
            if feature in BINARY_FEATURE_NAMES:
                continue
            if counts[index]:
                means[index] = sums[index] / counts[index]
                variance = (sumsquares[index] / counts[index]) - means[index] * means[index]
                scales[index] = math.sqrt(max(variance, 0.0)) or 1.0
        fitted[name] = FittedTrainingPreprocessor._create(
            token=_PREPROCESSOR_SEAL,
            means=means,
            scales=scales,
            feature_names=active_contract.feature_names,
            training_cutoff=partition.training_cutoff,
            contract_identity=active_contract.identity,
            binary_features=binary_features,
            training_partition_identity=partition.seal,
            mode="PRIMARY",
            reduction_algorithm=_PREPROCESSOR_REDUCTION_ALGORITHM,
            _provenance=_PRIMARY_PREPROCESSOR_CAPABILITY,
            _partition_capability=_AUTHENTICATED_PREPROCESSOR_RECEIPT,
        )
    return fitted


def fit_training_preprocessor(
    training_partition: TrainingTensorPartition,
    *,
    contract: TensorContract | None = None,
) -> FittedTrainingPreprocessor:
    """Fit PRIMARY statistics only from an authenticated training partition."""
    if not isinstance(training_partition, TrainingTensorPartition):
        raise TypeError("PRIMARY preprocessing requires an authenticated training partition")
    training_partition._validate()
    active_contract = contract or TensorContract()
    if active_contract.identity != TensorContract().identity:
        raise ValueError("PRIMARY preprocessing requires the canonical 60-minute TensorContract")
    return _fit_training_preprocessor_from_tensor(
        training_partition.tensors,
        training_cutoff=training_partition.training_cutoff,
        contract=active_contract,
        training_partition_identity=training_partition.seal,
        mode="PRIMARY",
        provenance=_PRIMARY_PREPROCESSOR_CAPABILITY,
        partition_capability=training_partition,
    )


def fit_training_preprocessor_smoke(
    training_values: MaskedTensor,
    *,
    training_cutoff: datetime,
    contract: TensorContract | None = None,
) -> FittedTrainingPreprocessor:
    """SMOKE-only preprocessor factory; never produces PRIMARY lineage."""
    return _fit_training_preprocessor_from_tensor(
        training_values,
        training_cutoff=training_cutoff,
        contract=contract,
        mode="SMOKE",
        provenance=_SMOKE_PREPROCESSOR_CAPABILITY,
    )


CANONICAL_NODE_ORDER = ALL_INSTRUMENTS
CANONICAL_FEATURE_ORDER = P0_FEATURE_NAMES
build_causal_tensor = build_masked_sequence
materialise_support = candidate_independent_support


__all__ = [
    "BINARY_FEATURE_NAMES",
    "CANONICAL_FEATURE_ORDER",
    "CANONICAL_NODE_ORDER",
    "DEFAULT_LOOKBACK_MINUTES",
    "FEATURE_SEMANTIC_SHA256",
    "P0_FEATURE_NAMES",
    "FittedTrainingPreprocessor",
    "MaskedTensor",
    "SupportRecord",
    "TensorContract",
    "build_causal_tensor",
    "build_masked_sequence",
    "candidate_independent_support",
    "fit_training_preprocessor",
    "materialise_support",
    "support_for_candidates",
]
