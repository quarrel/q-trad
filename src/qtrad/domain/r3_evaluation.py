"""Independent R3.E position, physical-cost and executable P&L reconciliation.

This module deliberately does not construct targets or consume target aggregates.  The
evaluator accepts immutable decision inputs, recomputes ledgers from physical deltas,
and keeps expected and realised values separate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from qtrad.domain.economics import (
    ContinuousCostModel,
    CostBasis,
    CostComponentKind,
    ExpectedCostState,
    InputStatus,
)
from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass, PriceBasis
from qtrad.domain.portfolio import ContinuousTarget, HorizonState, NettingResult, SleeveKey
from qtrad.domain.r3_rounding import RoundedTarget
from qtrad.domain.risk import RiskState
from qtrad.domain.time import require_utc

EVALUATION_CONTRACT = "qtrad-r3-independent-evaluation-v1"
DECISION_CONTRACT = "qtrad-r3-decision-closure-v1"
OUTCOME_CONTRACT = "qtrad-r3-outcome-closure-v1"
REPORT_CONTRACT = "qtrad-r3-independent-report-v1"
RECEIPT_CONTRACT = "qtrad-r3-verification-receipt-v1"
PNL_BASIS_REFERENCE_TO_EXIT = "REFERENCE_TO_EXIT_MIDPOINT"


class EvaluationDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


class EvaluationReasonCode(StrEnum):
    MISSING_QUOTE = "MISSING_QUOTE"
    STALE_QUOTE = "STALE_QUOTE"
    CLOSED_SESSION = "CLOSED_SESSION"
    INCOMPLETE_QUOTE = "INCOMPLETE_QUOTE"
    SOURCE_EVIDENCE_MISMATCH = "SOURCE_EVIDENCE_MISMATCH"
    BAD_FX = "BAD_FX"
    ATTRIBUTION_MUTATION = "ATTRIBUTION_MUTATION"
    DOUBLE_COUNTING = "DOUBLE_COUNTING"
    POSITION_RESIDUAL = "POSITION_RESIDUAL"
    COST_RESIDUAL = "COST_RESIDUAL"
    MISSING_DECISION_REFERENCE = "MISSING_DECISION_REFERENCE"
    UNSUPPORTED_PRICE_BASIS = "UNSUPPORTED_PRICE_BASIS"


def _canonical(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("identity Decimal must be finite")
        return str(value)
    if isinstance(value, datetime):
        require_utc(value, "identity datetime")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(k): _canonical(v) for k, v in sorted(mapping.items(), key=lambda i: str(i[0]))}
    if isinstance(value, (tuple, list)):
        sequence = cast(Sequence[object], value)
        return [_canonical(item) for item in sequence]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported identity value: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def identity(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _decimal(value: Decimal, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


def _digest(value: str, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a SHA-256 digest")


def _ordered_assets(asset_order: Sequence[str]) -> tuple[str, ...]:
    result = tuple(asset_order)
    if not result or tuple(sorted(result)) != result or len(set(result)) != len(result):
        raise ValueError("asset order must be non-empty, sorted and unique")
    return result


def _ordered_decimal_pairs(
    values: Sequence[tuple[str, Decimal]], name: str
) -> tuple[tuple[str, Decimal], ...]:
    result = tuple(values)
    if tuple(sorted(result, key=lambda item: item[0])) != result:
        raise ValueError(f"{name} must use canonical key order")
    keys = tuple(item[0] for item in result)
    if any(type(key) is not str or not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError(f"{name} keys must be non-empty and unique")
    for value in result:
        _decimal(value[1], f"{name} value")
    return result


@dataclass(frozen=True, slots=True)
class SleeveAttribution:
    """Independent copy of final sleeve movement, retaining full R3.D identity."""

    sleeve_id: str
    asset_id: str
    requested_delta: Decimal
    internal_cross_quantity: Decimal
    external_delta_share: Decimal
    repair_delta: Decimal = Decimal("0")
    reason_codes: tuple[str, ...] = ()
    key: SleeveKey | None = None

    def __post_init__(self) -> None:
        if not self.sleeve_id or not self.asset_id:
            raise ValueError("sleeve and asset identity are required")
        if self.key is not None and (
            self.key.asset_id != self.asset_id or self.key.configuration_id != self.sleeve_id
        ):
            raise ValueError("sleeve key does not bind sleeve and asset identity")
        for value, name in (
            (self.requested_delta, "requested delta"),
            (self.internal_cross_quantity, "internal cross quantity"),
            (self.external_delta_share, "external delta share"),
            (self.repair_delta, "repair delta"),
        ):
            _decimal(value, name)
        if self.internal_cross_quantity < 0:
            raise ValueError("internal cross quantity must be non-negative")
        if abs(self.external_delta_share - self.repair_delta) + self.internal_cross_quantity != abs(
            self.requested_delta
        ):
            raise ValueError("sleeve attribution does not reconcile requested delta")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "sleeve_id": self.sleeve_id,
            "asset_id": self.asset_id,
            "key": self.key.as_json() if self.key is not None else None,
            "requested_delta": self.requested_delta,
            "internal_cross_quantity": self.internal_cross_quantity,
            "external_delta_share": self.external_delta_share,
            "repair_delta": self.repair_delta,
            "reason_codes": self.reason_codes,
        }

    @property
    def canonical_key(self) -> tuple[object, ...]:
        return self.key.canonical_tuple if self.key is not None else (self.asset_id, self.sleeve_id)

    @property
    def sleeve_key(self) -> SleeveKey | None:
        return self.key


@dataclass(frozen=True, slots=True)
class DecisionClosure:
    """Immutable, target-independent decision closure consumed by R3.E."""

    source_class: MarketDataSourceClass
    evidence_purpose: EvidencePurpose
    asset_order: tuple[str, ...]
    current_position: tuple[Decimal, ...]
    target_position: tuple[Decimal, ...]
    physical_delta: tuple[Decimal, ...]
    decision_time: datetime
    expiry_time: datetime
    holding_interval: timedelta
    gross_forecast_return: Mapping[str, Decimal]
    gross_contribution: Mapping[str, Decimal]
    physical_notional: Mapping[str, Decimal]
    expected_costs: Mapping[str, ExpectedCostState]
    cost_models: Mapping[str, ContinuousCostModel]
    attributions: tuple[SleeveAttribution, ...]
    decision_input_identity: str
    parent_verification_identity: str
    rounded_target: RoundedTarget | None = None
    target_verification_identity: str = ""
    reporting_currency: str = "AUD"
    contract: str = DECISION_CONTRACT
    risk_state: RiskState | None = None
    horizon_states: tuple[HorizonState, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.source_class) is not MarketDataSourceClass
            or type(self.evidence_purpose) is not EvidencePurpose
        ):
            raise ValueError("decision source and evidence purpose must be declared enums")
        assets = _ordered_assets(self.asset_order)
        object.__setattr__(self, "asset_order", assets)
        for values, name in (
            (self.current_position, "current position"),
            (self.target_position, "target position"),
            (self.physical_delta, "physical delta"),
        ):
            if len(values) != len(assets):
                raise ValueError(f"{name} must match asset order")
            for value in values:
                _decimal(value, name)
        if any(
            target - current != delta
            for current, target, delta in zip(
                self.current_position, self.target_position, self.physical_delta, strict=True
            )
        ):
            raise ValueError("decision physical delta does not reconcile")
        require_utc(self.decision_time, "decision time")
        require_utc(self.expiry_time, "expiry time")
        if self.expiry_time <= self.decision_time or self.holding_interval <= timedelta(0):
            raise ValueError("decision interval is invalid")
        if self.reporting_currency != "AUD":
            raise ValueError("decision reporting currency must be AUD")
        _digest(self.decision_input_identity, "decision input identity")
        _digest(self.parent_verification_identity, "parent verification identity")
        if self.rounded_target is None:
            raise ValueError("decision closure requires rounded target receipt")
        if self.target_verification_identity == "":
            raise ValueError("rounded target verification identity is required")
        _digest(self.target_verification_identity, "target verification identity")
        target = self.rounded_target
        if (
            target.source_class is not self.source_class
            or target.evidence_purpose is not self.evidence_purpose
            or target.asset_order != assets
            or target.current_position != self.current_position
            or target.target_position != self.target_position
            or target.physical_delta != self.physical_delta
            or target.decision_input_identity != self.decision_input_identity
            or dict(target.expected_costs) != dict(self.expected_costs)
        ):
            raise ValueError("decision closure does not bind rounded target")
        if self.parent_verification_identity != self.target_verification_identity:
            raise ValueError("decision parent must be the rounded-target receipt")
        if not self.attributions or not target.attributions:
            raise ValueError("decision requires rounded target attributions")
        if any(item.key is None for item in self.attributions):
            raise ValueError("decision attributions require full sleeve keys")
        expected_attributions = {item.key.canonical_tuple: item for item in target.attributions}
        actual_attributions = {actual.canonical_key: actual for actual in self.attributions}
        if set(expected_attributions) != set(actual_attributions):
            raise ValueError("decision attributions do not bind rounded target")
        for key, item in expected_attributions.items():
            actual = actual_attributions[key]
            if (
                actual.key != item.key
                or actual.requested_delta != item.requested_delta
                or actual.internal_cross_quantity != item.internal_cross_quantity
                or actual.external_delta_share != item.external_delta_share
                or actual.repair_delta != item.repair_delta
                or actual.reason_codes != item.reason_codes
            ):
                raise ValueError("decision attribution differs from rounded target")
        if self.risk_state is not None and (
            self.risk_state.source_class is not self.source_class
            or self.risk_state.evidence_purpose is not self.evidence_purpose
            or self.risk_state.asset_order != assets
        ):
            raise ValueError("decision risk state does not bind source, evidence or asset order")
        horizon_states = tuple(self.horizon_states)
        if (
            tuple(sorted(horizon_states, key=lambda item: item.key.canonical_tuple))
            != horizon_states
        ):
            raise ValueError("decision horizon states must use canonical sleeve order")
        if len({item.key for item in horizon_states}) != len(horizon_states):
            raise ValueError("decision horizon states must be unique")
        if any(
            item.key.source_class is not self.source_class
            or item.key.evidence_purpose is not self.evidence_purpose
            or item.key.asset_id not in assets
            for item in horizon_states
        ):
            raise ValueError("decision horizon state source, evidence or asset mismatch")
        object.__setattr__(self, "horizon_states", horizon_states)
        if horizon_states:
            expected_horizon_ids = tuple(item.semantic_identity for item in horizon_states)
            expected_pairs = tuple(
                (item.semantic_identity, item.closure_identity) for item in horizon_states
            )
            if (
                self.risk_state is None
                or self.risk_state.horizon_state_identities != expected_horizon_ids
            ):
                raise ValueError("decision risk state horizon identities do not bind lifecycle")
            if self.rounded_target.horizon_state_identities != expected_pairs:
                raise ValueError("decision rounded target horizon identities do not bind lifecycle")
        elif self.risk_state is not None and self.risk_state.horizon_state_identities:
            raise ValueError("decision risk state has unexpected horizon identities")
        object.__setattr__(
            self, "gross_forecast_return", MappingProxyType(dict(self.gross_forecast_return))
        )
        object.__setattr__(
            self, "gross_contribution", MappingProxyType(dict(self.gross_contribution))
        )
        object.__setattr__(
            self, "physical_notional", MappingProxyType(dict(self.physical_notional))
        )
        object.__setattr__(self, "expected_costs", MappingProxyType(dict(self.expected_costs)))
        object.__setattr__(self, "cost_models", MappingProxyType(dict(self.cost_models)))
        if (
            set(self.gross_forecast_return) != set(assets)
            or set(self.gross_contribution) != set(assets)
            or set(self.physical_notional) != set(assets)
        ):
            raise ValueError("gross fields must cover every asset")
        if set(self.expected_costs) != set(assets) or set(self.cost_models) != set(assets):
            raise ValueError("cost fields must cover every asset")
        for value in (
            *self.gross_forecast_return.values(),
            *self.gross_contribution.values(),
            *self.physical_notional.values(),
        ):
            _decimal(value, "gross value")
        if any(value <= 0 for value in self.physical_notional.values()):
            raise ValueError("physical notional must be finite and non-zero")
        if any(
            self.gross_contribution[asset]
            != self.gross_forecast_return[asset] * self.physical_notional[asset]
            for asset in assets
        ):
            raise ValueError("gross contribution must equal gross return times physical notional")
        if (
            tuple(sorted(self.attributions, key=lambda item: item.canonical_key))
            != self.attributions
        ):
            raise ValueError("decision attributions must be canonical")
        if any(item.asset_id not in assets for item in self.attributions):
            raise ValueError("decision attribution asset is unknown")

    @property
    def canonical_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract": self.contract,
            "source_class": self.source_class,
            "evidence_purpose": self.evidence_purpose,
            "asset_order": self.asset_order,
            "current_position": self.current_position,
            "target_position": self.target_position,
            "physical_delta": self.physical_delta,
            "decision_time": self.decision_time,
            "expiry_time": self.expiry_time,
            "holding_interval": self.holding_interval,
            "gross_forecast_return": tuple(
                (asset, self.gross_forecast_return[asset]) for asset in self.asset_order
            ),
            "gross_contribution": tuple(
                (asset, self.gross_contribution[asset]) for asset in self.asset_order
            ),
            "physical_notional": tuple(
                (asset, self.physical_notional[asset]) for asset in self.asset_order
            ),
            "expected_cost_identities": tuple(
                (asset, identity(_cost_state_payload(self.expected_costs[asset])))
                for asset in self.asset_order
            ),
            "cost_model_identities": tuple(
                (asset, self.cost_models[asset].semantic_identity) for asset in self.asset_order
            ),
            "attributions": tuple(_attribution_payload(item) for item in self.attributions),
            "decision_input_identity": self.decision_input_identity,
            "parent_verification_identity": self.parent_verification_identity,
            "rounded_target_identity": self.rounded_target.semantic_identity
            if self.rounded_target
            else None,
            "target_verification_identity": self.target_verification_identity,
            "reporting_currency": self.reporting_currency,
            "risk_state_identity": self.risk_state.semantic_id
            if self.risk_state is not None
            else None,
        }
        if self.horizon_states:
            payload["horizon_states"] = tuple(
                item.canonical_payload for item in self.horizon_states
            )
        return payload

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)

    @property
    def semantic_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def closure_identity(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class QuoteEvidence:
    """One executable quote observation, ordered by receive/global position."""

    asset_id: str
    received_time: datetime
    bid: Decimal | None
    ask: Decimal | None
    healthy: bool = True
    session_open: bool = True
    source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE
    evidence_purpose: EvidencePurpose = EvidencePurpose.FIXTURE_IMPLEMENTATION
    sequence: int = 0
    evidence_identity: str = field(default="", init=False)
    price_basis: PriceBasis = PriceBasis.MID

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("quote asset identity is required")
        if type(self.source_class) is not MarketDataSourceClass:
            raise ValueError("quote source class must be a declared enum")
        if type(self.evidence_purpose) is not EvidencePurpose:
            raise ValueError("quote evidence purpose must be a declared enum")
        if type(self.price_basis) is not PriceBasis:
            raise ValueError("quote price basis must be a declared enum")
        require_utc(self.received_time, "quote received time")
        if self.bid is not None:
            _decimal(self.bid, "quote bid")
            if self.bid <= 0:
                raise ValueError("quote bid must be positive")
        if self.ask is not None:
            _decimal(self.ask, "quote ask")
            if self.ask <= 0:
                raise ValueError("quote ask must be positive")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("quote bid must not exceed ask")
        object.__setattr__(self, "evidence_identity", identity(self.canonical_payload))

    @property
    def complete(self) -> bool:
        return self.bid is not None and self.ask is not None

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "received_time": self.received_time,
            "bid": self.bid,
            "ask": self.ask,
            "healthy": self.healthy,
            "session_open": self.session_open,
            "source_class": self.source_class,
            "evidence_purpose": self.evidence_purpose,
            "price_basis": self.price_basis,
            "sequence": self.sequence,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)

    @property
    def closure_identity(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class OutcomeClosure:
    """Immutable subsequent executable-side evidence."""

    asset_id: str
    decision_time: datetime
    target_time: datetime
    latency: timedelta
    entry: QuoteEvidence | None
    exit: QuoteEvidence | None
    disposition: EvaluationDisposition
    reason_codes: tuple[str, ...] = ()
    contract: str = OUTCOME_CONTRACT
    physical_delta: Decimal = Decimal("0")
    closure_identity: str = ""
    decision_reference: QuoteEvidence | None = None
    reference_to_entry_latency: timedelta | None = None
    source_class: MarketDataSourceClass = field(kw_only=True)
    evidence_purpose: EvidencePurpose = field(kw_only=True)
    decision_semantic_identity: str = field(kw_only=True)
    decision_closure_identity: str = field(kw_only=True)

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "outcome decision time")
        require_utc(self.target_time, "outcome target time")
        _decimal(self.physical_delta, "outcome physical delta")
        if self.target_time <= self.decision_time or self.latency < timedelta(0):
            raise ValueError("outcome times are invalid")
        if type(self.disposition) is not EvaluationDisposition:
            raise ValueError("outcome disposition must be a declared enum")
        if (
            type(self.source_class) is not MarketDataSourceClass
            or type(self.evidence_purpose) is not EvidencePurpose
        ):
            raise ValueError("outcome source and evidence purpose must be declared enums")
        _digest(self.decision_semantic_identity, "decision semantic identity")
        _digest(self.decision_closure_identity, "decision closure identity")
        reference = self.decision_reference
        for quote in (reference, self.entry, self.exit):
            if quote is not None and (
                quote.source_class is not self.source_class
                or quote.evidence_purpose is not self.evidence_purpose
            ):
                raise ValueError("outcome source/evidence mismatch")
        if any(
            quote is not None and quote.price_basis is not PriceBasis.MID
            for quote in (self.entry, self.exit)
        ):
            raise ValueError("outcome quote requires MID price basis")
        if reference is not None:
            if reference.asset_id != self.asset_id:
                raise ValueError("decision reference asset mismatch")
            if reference.price_basis is not PriceBasis.MID:
                raise ValueError("decision reference quote requires MID price basis")
            if (
                reference.received_time > self.decision_time
                or not reference.healthy
                or not reference.session_open
                or not reference.complete
                or reference.midpoint is None
            ):
                raise ValueError("decision reference quote is not valid")
            if self.entry is not None:
                bridge = self.entry.received_time - reference.received_time
                if bridge < self.latency:
                    raise ValueError("entry quote precedes decision reference latency boundary")
                if self.reference_to_entry_latency is None:
                    object.__setattr__(self, "reference_to_entry_latency", bridge)
                elif self.reference_to_entry_latency != bridge:
                    raise ValueError("decision reference latency bridge does not bind entry")
            elif self.reference_to_entry_latency is not None:
                raise ValueError("decision reference latency bridge requires entry evidence")
        elif self.reference_to_entry_latency is not None:
            raise ValueError("decision reference latency bridge requires reference evidence")
        if (
            self.disposition is EvaluationDisposition.ACCEPTED
            and (self.entry is None or self.exit is None)
            and not (
                self.entry is None
                and self.exit is None
                and not self.reason_codes
                and self.physical_delta == Decimal("0")
            )
        ):
            raise ValueError("accepted nonzero outcome requires entry and exit evidence")
        if (
            self.disposition is EvaluationDisposition.ACCEPTED
            and self.physical_delta != Decimal("0")
            and reference is None
        ):
            raise ValueError("accepted nonzero outcome requires decision reference evidence")
        if self.disposition is not EvaluationDisposition.ACCEPTED and not self.reason_codes:
            raise ValueError("unavailable or blocked outcome requires a reason")
        if self.entry is not None and self.entry.asset_id != self.asset_id:
            raise ValueError("entry asset mismatch")
        if self.exit is not None and self.exit.asset_id != self.asset_id:
            raise ValueError("exit asset mismatch")
        if self.entry is not None and (
            self.entry.received_time < self.decision_time + self.latency
            or self.entry.received_time >= self.target_time
        ):
            raise ValueError("entry quote is outside the causal decision boundary")
        if self.exit is not None and self.exit.received_time < self.target_time:
            raise ValueError("exit quote must be strictly after target time")
        if self.entry is not None and self.exit is not None:
            if self.entry.received_time >= self.exit.received_time:
                raise ValueError("entry quote must precede exit quote")
            if self.entry.closure_identity == self.exit.closure_identity:
                raise ValueError("entry and exit quote evidence must be distinct")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        expected_identity = identity(self.canonical_payload)
        if not self.closure_identity:
            object.__setattr__(self, "closure_identity", expected_identity)
        else:
            _digest(self.closure_identity, "outcome closure identity")
            if self.closure_identity != expected_identity:
                raise ValueError("outcome closure identity does not bind content")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "asset_id": self.asset_id,
            "decision_time": self.decision_time,
            "target_time": self.target_time,
            "latency": self.latency,
            "physical_delta": self.physical_delta,
            "source_class": self.source_class,
            "evidence_purpose": self.evidence_purpose,
            "decision_semantic_identity": self.decision_semantic_identity,
            "decision_closure_identity": self.decision_closure_identity,
            "decision_reference": self.decision_reference.canonical_payload
            if self.decision_reference
            else None,
            "reference_to_entry_latency": self.reference_to_entry_latency,
            "entry": self.entry.canonical_payload if self.entry else None,
            "exit": self.exit.canonical_payload if self.exit else None,
            "disposition": self.disposition,
            "reason_codes": self.reason_codes,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)

    @property
    def semantic_identity(self) -> str:
        return identity(self.canonical_payload)


@dataclass(frozen=True, slots=True)
class PnlBreakdown:
    """Signed gross midpoint P&L and independently enumerated physical costs."""

    gross_midpoint_pnl: Decimal
    spread: Decimal
    latency_movement: Decimal
    adverse_slippage: Decimal
    commission: Decimal
    financing: Decimal
    impact: Decimal
    fx_translation: Decimal
    net_pnl: Decimal
    basis: str = PNL_BASIS_REFERENCE_TO_EXIT

    def __post_init__(self) -> None:
        values = (
            self.gross_midpoint_pnl,
            self.spread,
            self.latency_movement,
            self.adverse_slippage,
            self.commission,
            self.financing,
            self.impact,
            self.fx_translation,
            self.net_pnl,
        )
        for value in values:
            _decimal(value, "P&L value")
        if self.basis != PNL_BASIS_REFERENCE_TO_EXIT:
            raise ValueError("unsupported P&L basis")
        expected = self.gross_midpoint_pnl - (
            self.spread
            + self.adverse_slippage
            + self.commission
            + self.financing
            + self.impact
            + self.fx_translation
        )
        if self.net_pnl != expected:
            raise ValueError("net P&L does not reconcile component costs")

    @property
    def transaction_cost(self) -> Decimal:
        return self.spread + self.adverse_slippage + self.commission + self.impact

    @property
    def total_cost(self) -> Decimal:
        return self.transaction_cost + self.financing + self.fx_translation

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "gross_midpoint_pnl": self.gross_midpoint_pnl,
            "basis": self.basis,
            "spread": self.spread,
            "latency_movement": self.latency_movement,
            "adverse_slippage": self.adverse_slippage,
            "commission": self.commission,
            "financing": self.financing,
            "impact": self.impact,
            "fx_translation": self.fx_translation,
            "net_pnl": self.net_pnl,
        }


@dataclass(frozen=True, slots=True)
class AssetReconciliation:
    asset_id: str
    disposition: EvaluationDisposition
    position_residual: Decimal
    expected_cost: Decimal
    realised: PnlBreakdown | None
    reason_codes: tuple[str, ...] = ()
    outcome_identity: str = ""

    def __post_init__(self) -> None:
        if not self.asset_id or type(self.disposition) is not EvaluationDisposition:
            raise ValueError("asset reconciliation identity and disposition are required")
        _decimal(self.position_residual, "position residual")
        _decimal(self.expected_cost, "expected cost")
        if self.outcome_identity:
            _digest(self.outcome_identity, "outcome identity")
        elif self.disposition is not EvaluationDisposition.ACCEPTED and self.realised is None:
            raise ValueError(
                "non-accepted reconciliation requires outcome identity or unavailable evidence"
            )
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "disposition": self.disposition,
            "position_residual": self.position_residual,
            "expected_cost": self.expected_cost,
            "realised": self.realised.canonical_payload if self.realised else None,
            "reason_codes": self.reason_codes,
            "outcome_identity": self.outcome_identity,
        }


@dataclass(frozen=True, slots=True)
class ComponentCostReconciliation:
    """One authoritative component return and AUD money equation."""

    component: CostComponentKind
    basis: CostBasis
    quantity_basis: Decimal
    native_amount: Decimal
    native_currency: str
    reporting_amount: Decimal
    reporting_currency: str
    conversion_rate: Decimal
    cost_return: Decimal
    aud_notional_basis: Decimal

    def __post_init__(self) -> None:
        for value, name in (
            (self.quantity_basis, "component quantity basis"),
            (self.native_amount, "component native amount"),
            (self.reporting_amount, "component reporting amount"),
            (self.conversion_rate, "component conversion rate"),
            (self.cost_return, "component cost return"),
            (self.aud_notional_basis, "component AUD notional basis"),
        ):
            _decimal(value, name)
        if self.aud_notional_basis <= 0:
            raise ValueError("component AUD notional basis must be finite and non-zero")
        if not self.native_currency or self.reporting_currency != "AUD":
            raise ValueError("component currencies must bind an AUD reporting basis")
        if self.reporting_amount != self.native_amount * self.conversion_rate:
            raise ValueError("component reporting amount does not reconcile conversion")
        if self.reporting_amount != self.cost_return * self.aud_notional_basis:
            raise ValueError("component return does not reconcile AUD basis")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "component": self.component,
            "basis": self.basis,
            "quantity_basis": self.quantity_basis,
            "native_amount": self.native_amount,
            "native_currency": self.native_currency,
            "reporting_amount": self.reporting_amount,
            "reporting_currency": self.reporting_currency,
            "conversion_rate": self.conversion_rate,
            "cost_return": self.cost_return,
            "aud_notional_basis": self.aud_notional_basis,
        }


@dataclass(frozen=True, slots=True)
class SleeveReconciliation:
    """Canonical Decimal cost and realised P&L allocation for one final sleeve movement."""

    sleeve_id: str
    asset_id: str
    physical_delta: Decimal
    repair_delta: Decimal
    expected_cost_components: Mapping[str, Decimal]
    expected_cost: Decimal
    realised: PnlBreakdown | None
    key: SleeveKey | None = None
    requested_delta: Decimal = Decimal("0")
    internal_cross_quantity: Decimal = Decimal("0")
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sleeve_id or not self.asset_id:
            raise ValueError("sleeve allocation identity is required")
        if self.key is not None and (
            self.key.asset_id != self.asset_id or self.key.configuration_id != self.sleeve_id
        ):
            raise ValueError("sleeve allocation key does not bind identity")
        for value, name in (
            (self.physical_delta, "sleeve physical delta"),
            (self.repair_delta, "sleeve repair delta"),
            (self.expected_cost, "sleeve expected cost"),
            (self.requested_delta, "sleeve requested delta"),
            (self.internal_cross_quantity, "sleeve internal cross quantity"),
        ):
            _decimal(value, name)
        if self.internal_cross_quantity < 0:
            raise ValueError("sleeve internal cross quantity must be non-negative")
        components = MappingProxyType(dict(self.expected_cost_components))
        expected_kinds = tuple(kind.value for kind in CostComponentKind)
        if tuple(components) != expected_kinds:
            raise ValueError("sleeve cost components must use canonical cost order")
        for value in components.values():
            _decimal(value, "sleeve expected component")
        if sum(components.values(), Decimal("0")) != self.expected_cost:
            raise ValueError("sleeve expected cost does not reconcile components")
        if self.requested_delta and abs(
            self.physical_delta - self.repair_delta
        ) + self.internal_cross_quantity != abs(self.requested_delta):
            raise ValueError("sleeve allocation does not reconcile requested delta")
        object.__setattr__(self, "expected_cost_components", components)
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def canonical_key(self) -> tuple[object, ...]:
        return self.key.canonical_tuple if self.key is not None else (self.asset_id, self.sleeve_id)

    @property
    def sleeve_key(self) -> SleeveKey | None:
        return self.key

    @property
    def external_delta_share(self) -> Decimal:
        return self.physical_delta

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "sleeve_id": self.sleeve_id,
            "asset_id": self.asset_id,
            "key": self.key.as_json() if self.key is not None else None,
            "requested_delta": self.requested_delta,
            "internal_cross_quantity": self.internal_cross_quantity,
            "external_delta_share": self.external_delta_share,
            "repair_delta": self.repair_delta,
            "reason_codes": self.reason_codes,
            "expected_cost_components": tuple(self.expected_cost_components.items()),
            "expected_cost": self.expected_cost,
            "realised": self.realised.canonical_payload if self.realised else None,
        }


def lifecycle_component_identities(components: Mapping[str, object]) -> dict[str, object]:
    """Return authoritative identities for one lifecycle component graph."""
    netting = cast(NettingResult, components["netting"])
    continuous_target = cast(ContinuousTarget, components["continuous_target"])
    rounded_target = cast(RoundedTarget, components["rounded_target"])
    risk_state = cast(RiskState, components["risk_state"])
    decision = cast(DecisionClosure, components["decision"])
    outcomes = cast(tuple[OutcomeClosure, ...], components["outcomes"])
    report = cast(EvaluationReport, components["report"])
    receipt = cast(VerificationReceipt, components["receipt"])
    return {
        "netting": netting.semantic_identity,
        "continuous_target": continuous_target.semantic_identity,
        "rounded_target": rounded_target.semantic_identity,
        "target": rounded_target.semantic_identity,
        "cost": rounded_target.cost_state_identity,
        "risk": risk_state.semantic_identity,
        "decision": decision.semantic_identity,
        "outcomes": tuple(item.semantic_identity for item in outcomes),
        "report": report.semantic_identity,
        "receipt": receipt.receipt_identity,
    }


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """One deterministic virtual-to-physical lifecycle boundary."""

    event_time: datetime
    next_event_time: datetime
    active_state_identities: tuple[str, ...]
    reviewed_state_identities: tuple[str, ...]
    expired_state_identities: tuple[str, ...]
    physical_position: tuple[tuple[str, Decimal], ...]
    physical_delta: tuple[tuple[str, Decimal], ...]
    transaction_cost: tuple[tuple[str, Decimal], ...]
    financing_cost: tuple[tuple[str, Decimal], ...]
    sleeve_allocations: tuple[SleeveAttribution, ...]
    target_identity: str
    risk_state_identity: str
    decision_identity: str
    cost_state_identity: str
    netting_identity: str
    outcome_identities: tuple[str, ...] = ()
    report_identity: str = ""
    receipt_identity: str = ""
    sleeve_transaction_costs: tuple[tuple[str, Decimal], ...] = ()
    sleeve_financing_costs: tuple[tuple[str, Decimal], ...] = ()
    continuous_target_identity: str = ""
    rounded_target_identity: str = ""
    _netting_component: NettingResult | None = field(default=None, repr=False, compare=False)
    _continuous_target_component: ContinuousTarget | None = field(
        default=None, repr=False, compare=False
    )
    _rounded_target_component: RoundedTarget | None = field(default=None, repr=False, compare=False)
    _risk_state_component: RiskState | None = field(default=None, repr=False, compare=False)
    _decision_component: DecisionClosure | None = field(default=None, repr=False, compare=False)
    _outcome_components: tuple[OutcomeClosure, ...] | None = field(
        default=None, repr=False, compare=False
    )
    _report_component: EvaluationReport | None = field(default=None, repr=False, compare=False)
    _receipt_component: VerificationReceipt | None = field(default=None, repr=False, compare=False)
    _sleeve_transaction_cost_component: tuple[tuple[str, Decimal], ...] | None = field(
        default=None, repr=False, compare=False
    )
    _sleeve_financing_cost_component: tuple[tuple[str, Decimal], ...] | None = field(
        default=None, repr=False, compare=False
    )
    _parent_horizon_state_components: tuple[HorizonState, ...] | None = field(
        default=None, repr=False, compare=False
    )
    _horizon_state_components: tuple[HorizonState, ...] | None = field(
        default=None, repr=False, compare=False
    )
    _boundary_times: tuple[datetime, ...] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        require_utc(self.event_time, "lifecycle event time")
        require_utc(self.next_event_time, "lifecycle next event time")
        if self.next_event_time < self.event_time:
            raise ValueError("lifecycle next event must not precede event time")
        for name, values in (
            ("active state", self.active_state_identities),
            ("reviewed state", self.reviewed_state_identities),
            ("expired state", self.expired_state_identities),
            ("outcome", self.outcome_identities),
        ):
            ordered = tuple(values)
            if ordered != tuple(sorted(ordered)) or len(set(ordered)) != len(ordered):
                raise ValueError(f"lifecycle {name} identities must be canonical and unique")
            for value in ordered:
                _digest(value, f"lifecycle {name} identity")
        for name, values in (
            ("physical position", self.physical_position),
            ("physical delta", self.physical_delta),
            ("transaction cost", self.transaction_cost),
            ("financing cost", self.financing_cost),
        ):
            ordered = tuple(values)
            if ordered != tuple(sorted(ordered, key=lambda item: item[0])):
                raise ValueError(f"lifecycle {name} must use canonical asset order")
            if len({item[0] for item in ordered}) != len(ordered):
                raise ValueError(f"lifecycle {name} must use unique assets")
            for _, value in ordered:
                _decimal(value, f"lifecycle {name} value")
        allocations = tuple(self.sleeve_allocations)
        if allocations != tuple(sorted(allocations, key=lambda item: item.canonical_key)):
            raise ValueError("lifecycle sleeve allocations must be canonical")
        object.__setattr__(self, "sleeve_allocations", allocations)
        for name, values in (
            ("sleeve transaction cost", self.sleeve_transaction_costs),
            ("sleeve financing cost", self.sleeve_financing_costs),
        ):
            ordered = tuple(values)
            if ordered != tuple(sorted(ordered, key=lambda item: item[0])):
                raise ValueError(f"{name} allocations must be canonical")
            if len({item[0] for item in ordered}) != len(ordered):
                raise ValueError(f"{name} allocations must use unique sleeves")
            for _, value in ordered:
                _decimal(value, name)
        if sum((value for _, value in self.sleeve_transaction_costs), Decimal("0")) != sum(
            value for _, value in self.transaction_cost
        ):
            raise ValueError("lifecycle transaction cost attribution does not reconcile")
        if sum((value for _, value in self.sleeve_financing_costs), Decimal("0")) != sum(
            value for _, value in self.financing_cost
        ):
            raise ValueError("lifecycle financing cost attribution does not reconcile")
        object.__setattr__(self, "sleeve_transaction_costs", tuple(self.sleeve_transaction_costs))
        object.__setattr__(self, "sleeve_financing_costs", tuple(self.sleeve_financing_costs))
        for name, value in (
            ("target", self.target_identity),
            ("continuous target", self.continuous_target_identity),
            ("rounded target", self.rounded_target_identity),
            ("risk", self.risk_state_identity),
            ("decision", self.decision_identity),
        ):
            _digest(value, f"lifecycle {name} identity")
        for name, value in (("report", self.report_identity), ("receipt", self.receipt_identity)):
            if value:
                _digest(value, f"lifecycle {name} identity")
        _digest(self.cost_state_identity, "lifecycle cost identity")
        _digest(self.netting_identity, "lifecycle netting identity")
        if any(
            component is None
            for component in (
                self._netting_component,
                self._continuous_target_component,
                self._rounded_target_component,
                self._risk_state_component,
                self._decision_component,
                self._outcome_components,
                self._report_component,
                self._receipt_component,
                self._sleeve_transaction_cost_component,
                self._sleeve_financing_cost_component,
            )
        ):
            raise ValueError("lifecycle event requires authoritative component objects")
        expected = lifecycle_component_identities(
            {
                "netting": self._netting_component,
                "continuous_target": self._continuous_target_component,
                "rounded_target": self._rounded_target_component,
                "risk_state": self._risk_state_component,
                "decision": self._decision_component,
                "outcomes": self._outcome_components,
                "report": self._report_component,
                "receipt": self._receipt_component,
            }
        )
        supplied = {
            "netting": self.netting_identity,
            "target": self.target_identity,
            "continuous_target": self.continuous_target_identity,
            "rounded_target": self.rounded_target_identity,
            "cost": self.cost_state_identity,
            "risk": self.risk_state_identity,
            "decision": self.decision_identity,
            "outcomes": self.outcome_identities,
            "report": self.report_identity,
            "receipt": self.receipt_identity,
        }
        expected_values = {
            "netting": expected["netting"],
            "target": expected["target"],
            "continuous_target": expected["continuous_target"],
            "rounded_target": expected["rounded_target"],
            "cost": expected["cost"],
            "risk": expected["risk"],
            "decision": expected["decision"],
            "outcomes": expected["outcomes"],
            "report": expected["report"],
            "receipt": expected["receipt"],
        }
        if supplied != expected_values:
            raise ValueError("lifecycle identity chain does not bind authoritative components")

        assert self._netting_component is not None
        assert self._continuous_target_component is not None
        assert self._rounded_target_component is not None
        assert self._decision_component is not None
        assert self._outcome_components is not None
        assert self._report_component is not None
        assert self._receipt_component is not None
        assert self._sleeve_transaction_cost_component is not None
        assert self._sleeve_financing_cost_component is not None
        netting = self._netting_component
        continuous = self._continuous_target_component
        rounded = self._rounded_target_component
        decision = self._decision_component
        outcomes = self._outcome_components
        report = self._report_component
        receipt = self._receipt_component

        if report.lifecycle_events not in (None, ()):
            raise ValueError("lifecycle event report must have an empty lifecycle chain")
        expected_report_horizons = tuple(
            (state.semantic_identity, state.closure_identity) for state in decision.horizon_states
        )
        if report.horizon_state_identities != expected_report_horizons:
            raise ValueError("lifecycle event report horizon states do not bind event decision")
        if (
            report.source_class is not decision.source_class
            or report.evidence_purpose is not decision.evidence_purpose
            or report.decision_identity != decision.semantic_identity
            or report.decision_closure_identity != decision.closure_identity
            or report.risk_state_identity
            != (decision.risk_state.semantic_identity if decision.risk_state is not None else "")
            or report.risk_current_position != decision.current_position
            or report.risk_target_position != decision.target_position
        ):
            raise ValueError(
                "lifecycle event report does not bind event decision; "
                "evaluation report does not bind"
            )
        if (
            receipt.semantic_identity != report.semantic_identity
            or receipt.closure_identity != report.closure_identity
            or receipt.parent_verification_identity != decision.parent_verification_identity
        ):
            raise ValueError("lifecycle event receipt does not bind event report and parent")
        if any(
            outcome.asset_id not in decision.asset_order
            or outcome.decision_semantic_identity != decision.semantic_identity
            or outcome.decision_closure_identity != decision.closure_identity
            or outcome.source_class is not decision.source_class
            or outcome.evidence_purpose is not decision.evidence_purpose
            for outcome in outcomes
        ):
            raise ValueError(
                "lifecycle outcomes do not bind event decision; persisted outcome does not bind"
            )
        outcome_by_asset = {outcome.asset_id: outcome for outcome in outcomes}
        if tuple(outcome_by_asset) != decision.asset_order:
            raise ValueError("lifecycle outcomes do not cover event assets")
        if tuple(item.asset_id for item in report.assets) != decision.asset_order:
            raise ValueError("lifecycle report assets do not bind event assets")
        for reconciliation in report.assets:
            outcome = outcome_by_asset[reconciliation.asset_id]
            if (
                reconciliation.disposition is not outcome.disposition
                or reconciliation.outcome_identity != outcome.semantic_identity
                or reconciliation.expected_cost
                != decision.expected_costs[reconciliation.asset_id].require_total_reporting()
            ):
                raise ValueError("lifecycle report asset does not bind event outcome")
        expected_costs = {
            asset: decision.expected_costs[asset].require_total_reporting()
            for asset in decision.asset_order
        }
        expected_cost_components: dict[str, dict[str, Decimal]] = {}
        for asset in decision.asset_order:
            component_values: dict[str, Decimal] = {}
            for component in decision.expected_costs[asset].components:
                if component.reporting_amount is None:
                    raise ValueError("lifecycle decision cost component is unavailable")
                component_values[component.component.value] = component.reporting_amount
            expected_cost_components[asset] = component_values
        expected_net_return = {
            asset: decision.gross_forecast_return[asset]
            - expected_costs[asset] / decision.physical_notional[asset]
            for asset in decision.asset_order
        }
        expected_net_contribution = {
            asset: decision.gross_contribution[asset] - expected_costs[asset]
            for asset in decision.asset_order
        }
        risk = _risk_projection(decision)
        if (
            dict(report.gross_forecast) != dict(decision.gross_forecast_return)
            or dict(report.physical_notional) != dict(decision.physical_notional)
            or dict(report.expected_cost) != expected_costs
            or dict(report.expected_cost_components) != expected_cost_components
            or dict(report.expected_net_return) != expected_net_return
            or dict(report.gross_expected_contribution) != dict(decision.gross_contribution)
            or dict(report.expected_net_contribution) != expected_net_contribution
            or report.group_exposure != risk.group_after
            or report.currency_exposure != risk.currency_after
            or report.group_exposure_before != risk.group_before
            or report.currency_exposure_before != risk.currency_before
            or report.marginal_risk_before != risk.marginal_before
            or report.marginal_risk_after != risk.marginal_after
            or report.allocations != risk.allocations
            or report.portfolio_risk_before != risk.portfolio_before
            or report.portfolio_risk_after != risk.portfolio_after
            or report.risk_residual != risk.residual
            or report.risk_tolerance != risk.tolerance
        ):
            raise ValueError("lifecycle report economics or risk do not bind event decision")
        expected_attributions = {
            item.key.canonical_tuple: item for item in decision.attributions if item.key is not None
        }
        actual_attributions = {
            item.key.canonical_tuple: item
            for item in report.sleeve_allocations
            if item.key is not None
        }
        if set(actual_attributions) != set(expected_attributions):
            raise ValueError("lifecycle report attributions do not bind event decision")
        for key, expected_attribution in expected_attributions.items():
            actual_attribution = actual_attributions[key]
            if (
                actual_attribution.asset_id != expected_attribution.asset_id
                or actual_attribution.physical_delta != expected_attribution.external_delta_share
                or actual_attribution.repair_delta != expected_attribution.repair_delta
                or actual_attribution.requested_delta != expected_attribution.requested_delta
                or actual_attribution.internal_cross_quantity
                != expected_attribution.internal_cross_quantity
                or actual_attribution.reason_codes != expected_attribution.reason_codes
            ):
                raise ValueError("lifecycle report attributions do not bind event decision")
        authorities = (continuous, rounded, decision)
        asset_order = rounded.asset_order
        if any(authority.asset_order != asset_order for authority in authorities) or (
            netting.asset_order != asset_order
        ):
            raise ValueError("lifecycle vectors do not share authoritative asset order")
        expected_position = tuple(zip(asset_order, rounded.target_position, strict=True))
        expected_delta = tuple(zip(asset_order, rounded.physical_delta, strict=True))
        if self.physical_position != expected_position:
            raise ValueError("lifecycle physical position does not bind authoritative target")
        if self.physical_delta != expected_delta:
            raise ValueError("lifecycle physical delta does not bind authoritative target")
        if any(
            authority.target_position != rounded.target_position
            or authority.physical_delta != rounded.physical_delta
            for authority in authorities
        ):
            raise ValueError("lifecycle vectors do not bind authoritative targets")
        if tuple(zip(asset_order, netting.external_deltas, strict=True)) != expected_delta:
            raise ValueError("lifecycle physical delta does not bind authoritative netting")

        def expected_cost_parts(
            states: Mapping[str, ExpectedCostState],
        ) -> tuple[tuple[tuple[str, Decimal], ...], tuple[tuple[str, Decimal], ...]]:
            transactions: list[tuple[str, Decimal]] = []
            financing: list[tuple[str, Decimal]] = []
            if tuple(states) != asset_order:
                raise ValueError("lifecycle cost states do not cover authoritative assets")
            for asset in asset_order:
                state = states[asset]
                total = state.require_total_reporting()
                finance_component = next(
                    component
                    for component in state.components
                    if component.component is CostComponentKind.FINANCING
                )
                if finance_component.reporting_amount is None:
                    raise ValueError("lifecycle financing amount is required")
                transactions.append((asset, total - finance_component.reporting_amount))
                financing.append((asset, finance_component.reporting_amount))
            return tuple(transactions), tuple(financing)

        expected_transaction, expected_financing = expected_cost_parts(rounded.expected_costs)
        if (
            self.transaction_cost != expected_transaction
            or self.financing_cost != expected_financing
        ):
            raise ValueError("lifecycle costs do not bind authoritative cost states")
        for authority in (continuous, rounded):
            if authority.expected_cost_reporting != sum(
                (value for _, value in expected_transaction), Decimal("0")
            ) or authority.expected_financing_reporting != sum(
                (value for _, value in expected_financing), Decimal("0")
            ):
                raise ValueError("lifecycle costs do not bind authoritative target totals")
        if expected_cost_parts(decision.expected_costs) != (
            expected_transaction,
            expected_financing,
        ):
            raise ValueError("lifecycle costs do not bind authoritative decision states")

        if any(item.key is None for item in decision.attributions) or any(
            item.key is None for item in allocations
        ):
            raise ValueError("lifecycle sleeve allocations require full authoritative keys")
        decision_attributions = {
            item.key.canonical_tuple: item for item in decision.attributions if item.key is not None
        }
        rounded_attributions = {item.key.canonical_tuple: item for item in rounded.attributions}
        netting_attributions = {item.key.canonical_tuple: item for item in netting.sleeves}
        public_attributions = {
            item.key.canonical_tuple: item for item in allocations if item.key is not None
        }
        if (
            len(public_attributions) != len(allocations)
            or set(public_attributions) != set(decision_attributions)
            or set(public_attributions) != set(rounded_attributions)
            or set(public_attributions) != set(netting_attributions)
        ):
            raise ValueError("lifecycle sleeve allocations do not bind authoritative sleeves")
        for key, actual in public_attributions.items():
            expected = decision_attributions[key]
            repaired = rounded_attributions[key]
            parent = netting_attributions[key]
            if (
                actual != expected
                or actual.requested_delta != repaired.requested_delta
                or actual.internal_cross_quantity != repaired.internal_cross_quantity
                or actual.external_delta_share != repaired.external_delta_share
                or actual.repair_delta != repaired.repair_delta
                or actual.reason_codes != repaired.reason_codes
                or actual.requested_delta != parent.requested_delta
                or actual.internal_cross_quantity != parent.internal_cross_quantity
                or actual.external_delta_share - actual.repair_delta != parent.external_delta_share
            ):
                raise ValueError("lifecycle sleeve allocations do not bind authoritative sleeves")

        expected_transaction_sleeves = self._sleeve_transaction_cost_component
        expected_financing_sleeves = self._sleeve_financing_cost_component
        if (
            self.sleeve_transaction_costs != expected_transaction_sleeves
            or self.sleeve_financing_costs != expected_financing_sleeves
        ):
            raise ValueError(
                "lifecycle sleeve cost allocations do not bind authoritative allocations"
            )
        sleeve_ids = {item.sleeve_id for item in allocations}
        if (
            not {sleeve_id for sleeve_id, _ in expected_transaction_sleeves} <= sleeve_ids
            or not {sleeve_id for sleeve_id, _ in expected_financing_sleeves} <= sleeve_ids
        ):
            raise ValueError("lifecycle sleeve cost allocations do not cover authoritative sleeves")

        if (
            self._parent_horizon_state_components is None
            or self._horizon_state_components is None
            or self._boundary_times is None
        ):
            raise ValueError(
                "lifecycle event requires authoritative horizon and boundary components"
            )
        horizon_states = tuple(self._parent_horizon_state_components)
        event_states = tuple(self._horizon_state_components)
        boundaries = tuple(self._boundary_times)
        if not boundaries or tuple(sorted(set(boundaries))) != boundaries:
            raise ValueError("lifecycle boundary times must be canonical and unique")
        if self.event_time not in boundaries:
            raise ValueError("lifecycle event time is not an authoritative boundary")
        index = boundaries.index(self.event_time)
        expected_next = boundaries[index + 1] if index + 1 < len(boundaries) else self.event_time
        if self.next_event_time != expected_next:
            raise ValueError("lifecycle next event time is not the authoritative boundary")
        if (
            tuple(sorted(horizon_states, key=lambda state: state.key.canonical_tuple))
            != horizon_states
        ):
            raise ValueError("lifecycle horizon states must use canonical sleeve order")
        if len(set(state.key for state in horizon_states)) != len(horizon_states):
            raise ValueError("lifecycle horizon states must be unique")
        for state in horizon_states:
            if state.decision_time > self.event_time:
                raise ValueError("lifecycle horizon state decision follows event time")
        expected_states = (
            tuple(
                sorted(
                    (state for state in horizon_states if state.expiry_time > self.event_time),
                    key=lambda state: state.key.canonical_tuple,
                )
            )
            + tuple(
                sorted(
                    (state for state in horizon_states if state.expiry_time == self.event_time),
                    key=lambda state: state.key.canonical_tuple,
                )
            )
            + tuple(
                sorted(
                    (state for state in horizon_states if state.expiry_time < self.event_time),
                    key=lambda state: state.key.canonical_tuple,
                )
            )
        )
        if event_states != expected_states:
            raise ValueError("lifecycle event horizon states do not bind authoritative membership")
        expected_active = tuple(
            sorted(
                state.semantic_identity
                for state in horizon_states
                if state.expiry_time > self.event_time
            )
        )
        expected_reviewed = tuple(
            sorted(
                state.semantic_identity
                for state in horizon_states
                if state.expiry_time == self.event_time
            )
        )
        expected_expired = tuple(
            sorted(
                state.semantic_identity
                for state in horizon_states
                if state.expiry_time < self.event_time
            )
        )
        if (
            self.active_state_identities != expected_active
            or self.reviewed_state_identities != expected_reviewed
            or self.expired_state_identities != expected_expired
        ):
            raise ValueError(
                "lifecycle state identity classification does not bind authoritative states"
            )

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "event_time": self.event_time,
            "next_event_time": self.next_event_time,
            "active_state_identities": self.active_state_identities,
            "reviewed_state_identities": self.reviewed_state_identities,
            "expired_state_identities": self.expired_state_identities,
            "physical_position": self.physical_position,
            "physical_delta": self.physical_delta,
            "transaction_cost": self.transaction_cost,
            "financing_cost": self.financing_cost,
            "sleeve_allocations": tuple(item.canonical_payload for item in self.sleeve_allocations),
            "target_identity": self.target_identity,
            "continuous_target_identity": self.continuous_target_identity,
            "rounded_target_identity": self.rounded_target_identity,
            "risk_state_identity": self.risk_state_identity,
            "decision_identity": self.decision_identity,
            "cost_state_identity": self.cost_state_identity,
            "netting_identity": self.netting_identity,
            "outcome_identities": self.outcome_identities,
            "report_identity": self.report_identity,
            "receipt_identity": self.receipt_identity,
            "sleeve_transaction_costs": self.sleeve_transaction_costs,
            "sleeve_financing_costs": self.sleeve_financing_costs,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)

    @property
    def semantic_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def closure_identity(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Immutable report closure; all values are independently recomputed."""

    source_class: MarketDataSourceClass
    evidence_purpose: EvidencePurpose
    decision_identity: str
    decision_closure_identity: str
    outcome_identities: tuple[str, ...]
    unavailable_outcome_identities: tuple[str, ...]
    assets: tuple[AssetReconciliation, ...]
    sleeve_allocations: tuple[SleeveReconciliation, ...]
    gross_forecast: Mapping[str, Decimal]
    physical_notional: Mapping[str, Decimal]
    expected_cost: Mapping[str, Decimal]
    expected_net_contribution: Mapping[str, Decimal]
    blocked_outcome_identities: tuple[str, ...] = ()
    gross_expected_contribution: Mapping[str, Decimal] = MappingProxyType({})
    expected_net_return: Mapping[str, Decimal] = MappingProxyType({})
    expected_cost_components: Mapping[str, Mapping[str, Decimal]] = MappingProxyType({})
    expected_cost_component_details: Mapping[str, tuple[ComponentCostReconciliation, ...]] = (
        MappingProxyType({})
    )
    fx_translation_identity: str = ""
    group_exposure: tuple[tuple[str, Decimal], ...] = ()
    currency_exposure: tuple[tuple[str, Decimal], ...] = ()
    group_exposure_before: tuple[tuple[str, Decimal], ...] = ()
    currency_exposure_before: tuple[tuple[str, Decimal], ...] = ()
    marginal_risk_before: tuple[tuple[str, Decimal], ...] = ()
    marginal_risk_after: tuple[tuple[str, Decimal], ...] = ()
    allocations: tuple[tuple[str, Decimal], ...] = ()
    portfolio_risk_before: Decimal = Decimal("0")
    portfolio_risk_after: Decimal = Decimal("0")
    risk_residual: Decimal = Decimal("0")
    risk_tolerance: Decimal = Decimal("0")
    allocation_cost_residuals: tuple[tuple[str, Decimal], ...] = ()
    allocation_pnl_residuals: tuple[tuple[str, Decimal], ...] = ()
    risk_state: RiskState | None = None
    risk_current_position: tuple[Decimal, ...] = ()
    risk_target_position: tuple[Decimal, ...] = ()
    risk_state_identity: str = ""
    report_contract: str = REPORT_CONTRACT
    horizon_state_identities: tuple[tuple[str, str], ...] = ()
    lifecycle_events: tuple[LifecycleEvent, ...] | None = None

    def __post_init__(self) -> None:
        if (
            type(self.source_class) is not MarketDataSourceClass
            or type(self.evidence_purpose) is not EvidencePurpose
        ):
            raise ValueError("report source and evidence purpose must be declared enums")
        _digest(self.decision_identity, "decision identity")
        _digest(self.decision_closure_identity, "decision closure identity")
        object.__setattr__(self, "outcome_identities", tuple(self.outcome_identities))
        object.__setattr__(
            self, "unavailable_outcome_identities", tuple(self.unavailable_outcome_identities)
        )
        object.__setattr__(
            self, "blocked_outcome_identities", tuple(self.blocked_outcome_identities)
        )
        all_ids = (
            *self.outcome_identities,
            *self.unavailable_outcome_identities,
            *self.blocked_outcome_identities,
        )
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("outcome disposition identities must be unique and disjoint")
        for value in all_ids:
            _digest(value, "outcome identity")
        assets = tuple(self.assets)
        asset_ids = tuple(item.asset_id for item in assets)
        if (
            not asset_ids
            or asset_ids != tuple(sorted(asset_ids))
            or len(set(asset_ids)) != len(asset_ids)
        ):
            raise ValueError("report assets must use canonical sorted order")
        accepted_ids = {
            item.outcome_identity
            for item in assets
            if item.disposition is EvaluationDisposition.ACCEPTED
        }
        unavailable_ids = {
            item.outcome_identity
            for item in assets
            if item.disposition is EvaluationDisposition.UNAVAILABLE
        }
        blocked_ids = {
            item.outcome_identity
            for item in assets
            if item.disposition is EvaluationDisposition.BLOCKED
        }
        if (
            accepted_ids != set(self.outcome_identities)
            or unavailable_ids != set(self.unavailable_outcome_identities)
            or blocked_ids != set(self.blocked_outcome_identities)
        ):
            raise ValueError("report outcome identities do not match asset dispositions")
        object.__setattr__(self, "assets", assets)
        sleeves = tuple(self.sleeve_allocations)
        if tuple(sorted(sleeves, key=lambda item: item.canonical_key)) != sleeves:
            raise ValueError("sleeve allocations must use canonical order")
        expected_sleeves = tuple((item.asset_id, item.sleeve_id) for item in sleeves)
        if any(item.asset_id not in asset_ids for item in sleeves) or len(
            set(expected_sleeves)
        ) != len(sleeves):
            raise ValueError("sleeve allocations must bind known unique sleeves")
        object.__setattr__(self, "sleeve_allocations", sleeves)

        cost_residuals = tuple(
            (
                asset,
                self.expected_cost[asset]
                - sum(
                    (item.expected_cost for item in sleeves if item.asset_id == asset),
                    Decimal("0"),
                ),
            )
            for asset in asset_ids
        )
        pnl_residuals_list: list[tuple[str, Decimal]] = []
        for asset in asset_ids:
            realised = next(item.realised for item in assets if item.asset_id == asset)
            residual = Decimal("0")
            if realised is not None:
                residual = realised.net_pnl - sum(
                    (
                        item.realised.net_pnl
                        for item in sleeves
                        if item.asset_id == asset and item.realised is not None
                    ),
                    Decimal("0"),
                )
            pnl_residuals_list.append((asset, residual))
        pnl_residuals = tuple(pnl_residuals_list)
        if any(value != 0 for _, value in cost_residuals + pnl_residuals):
            raise ValueError("sleeve allocation residuals must reconcile exactly")
        object.__setattr__(self, "allocation_cost_residuals", cost_residuals)
        object.__setattr__(self, "allocation_pnl_residuals", pnl_residuals)
        mappings = {
            "gross forecast": MappingProxyType(dict(self.gross_forecast)),
            "physical notional": MappingProxyType(dict(self.physical_notional)),
            "expected cost": MappingProxyType(dict(self.expected_cost)),
            "expected net contribution": MappingProxyType(dict(self.expected_net_contribution)),
            "gross expected contribution": MappingProxyType(dict(self.gross_expected_contribution)),
            "expected net return": MappingProxyType(dict(self.expected_net_return)),
        }
        for name, mapping in mappings.items():
            if tuple(mapping) != asset_ids:
                raise ValueError(f"{name} keys must match canonical asset order")
            for value in mapping.values():
                _decimal(value, f"report {name} value")
        if any(self.physical_notional[asset] <= 0 for asset in asset_ids):
            raise ValueError("report physical notional must be finite and strictly positive")
        if any(
            self.gross_expected_contribution[asset]
            != self.gross_forecast[asset] * self.physical_notional[asset]
            for asset in asset_ids
        ):
            raise ValueError(
                "report gross contribution must equal gross forecast return times physical notional"
            )
        object.__setattr__(self, "gross_forecast", mappings["gross forecast"])
        object.__setattr__(self, "physical_notional", mappings["physical notional"])
        object.__setattr__(self, "expected_cost", mappings["expected cost"])
        object.__setattr__(self, "expected_net_contribution", mappings["expected net contribution"])
        object.__setattr__(
            self, "gross_expected_contribution", mappings["gross expected contribution"]
        )
        object.__setattr__(self, "expected_net_return", mappings["expected net return"])
        if any(
            self.expected_net_contribution[asset]
            != self.gross_expected_contribution[asset] - self.expected_cost[asset]
            for asset in asset_ids
        ):
            raise ValueError("expected net contribution does not reconcile gross and cost")
        if any(
            self.expected_net_return[asset]
            != self.gross_forecast[asset]
            - self.expected_cost[asset] / self.physical_notional[asset]
            for asset in asset_ids
        ):
            raise ValueError("expected net return does not reconcile gross forecast and cost")
        components = {
            asset: MappingProxyType(dict(values))
            for asset, values in self.expected_cost_components.items()
        }
        if tuple(components) != asset_ids:
            raise ValueError("expected cost component keys must match canonical asset order")
        expected_kinds = tuple(kind.value for kind in CostComponentKind)
        for asset, values in components.items():
            if tuple(values) != expected_kinds:
                raise ValueError("expected cost components must include each canonical kind")
            for value in values.values():
                _decimal(value, "report expected component")
            if sum(values.values(), Decimal("0")) != self.expected_cost[asset]:
                raise ValueError("expected cost components do not reconcile total")
        details = {
            asset: tuple(values) for asset, values in self.expected_cost_component_details.items()
        }
        if tuple(details) != asset_ids:
            raise ValueError("expected cost component details must match canonical asset order")
        expected_kinds = tuple(kind.value for kind in CostComponentKind)
        for asset, values in details.items():
            if tuple(item.component.value for item in values) != expected_kinds:
                raise ValueError("expected cost details must use canonical component order")
            if any(
                item.reporting_amount != components[asset][item.component.value] for item in values
            ):
                raise ValueError("expected cost detail amounts do not reconcile")
        object.__setattr__(self, "expected_cost_components", MappingProxyType(components))
        object.__setattr__(self, "expected_cost_component_details", MappingProxyType(details))
        object.__setattr__(
            self, "group_exposure", _ordered_decimal_pairs(self.group_exposure, "group exposure")
        )
        object.__setattr__(
            self,
            "currency_exposure",
            _ordered_decimal_pairs(self.currency_exposure, "currency exposure"),
        )
        object.__setattr__(
            self,
            "group_exposure_before",
            _ordered_decimal_pairs(self.group_exposure_before, "group exposure before"),
        )
        object.__setattr__(
            self,
            "currency_exposure_before",
            _ordered_decimal_pairs(self.currency_exposure_before, "currency exposure before"),
        )
        marginal_before = _ordered_decimal_pairs(self.marginal_risk_before, "marginal risk before")
        marginal_after = _ordered_decimal_pairs(self.marginal_risk_after, "marginal risk after")
        allocations = _ordered_decimal_pairs(self.allocations, "allocations")
        if (
            tuple(key for key, _ in marginal_before) != asset_ids
            or tuple(key for key, _ in marginal_after) != asset_ids
            or tuple(key for key, _ in allocations) != asset_ids
        ):
            raise ValueError("risk tuples must use canonical asset order")
        object.__setattr__(self, "marginal_risk_before", marginal_before)
        object.__setattr__(self, "marginal_risk_after", marginal_after)
        object.__setattr__(self, "allocations", allocations)
        for value, name in (
            (self.portfolio_risk_before, "portfolio risk before"),
            (self.portfolio_risk_after, "portfolio risk after"),
            (self.risk_residual, "risk residual"),
            (self.risk_tolerance, "risk tolerance"),
        ):
            _decimal(value, name)
        if (
            self.portfolio_risk_before < 0
            or self.portfolio_risk_after < 0
            or self.risk_residual < 0
            or self.risk_tolerance < 0
        ):
            raise ValueError("risk values must be non-negative")
        if self.risk_residual != max(Decimal("0"), self.portfolio_risk_after - self.risk_tolerance):
            raise ValueError("risk residual does not reconcile portfolio risk and tolerance")
        if self.risk_state is not None:
            if (
                not self.risk_state_identity
                or self.risk_state_identity != self.risk_state.semantic_identity
            ):
                raise ValueError("report risk state identity is not authoritative")
            if len(self.risk_current_position) != len(asset_ids) or len(
                self.risk_target_position
            ) != len(asset_ids):
                raise ValueError("report risk positions must bind canonical assets")
            projection = _risk_projection_positions(
                self.risk_state, self.risk_current_position, self.risk_target_position
            )
            supplied = (
                self.group_exposure,
                self.currency_exposure,
                self.group_exposure_before,
                self.currency_exposure_before,
                self.marginal_risk_before,
                self.marginal_risk_after,
                self.allocations,
                self.portfolio_risk_before,
                self.portfolio_risk_after,
                self.risk_residual,
                self.risk_tolerance,
            )
            expected = (
                projection.group_after,
                projection.currency_after,
                projection.group_before,
                projection.currency_before,
                projection.marginal_before,
                projection.marginal_after,
                projection.allocations,
                projection.portfolio_before,
                projection.portfolio_after,
                projection.residual,
                projection.tolerance,
            )
            if supplied != expected:
                raise ValueError("report risk equations do not match authoritative risk state")
        if self.fx_translation_identity:
            _digest(self.fx_translation_identity, "FX translation identity")
        horizon_identities = tuple(self.horizon_state_identities)
        if len({item[0] for item in horizon_identities}) != len(horizon_identities):
            raise ValueError("report horizon identities must be unique")
        for semantic_id, closure_id in horizon_identities:
            _digest(semantic_id, "report horizon semantic identity")
            _digest(closure_id, "report horizon closure identity")
        object.__setattr__(self, "horizon_state_identities", horizon_identities)
        lifecycle_events = None if self.lifecycle_events is None else tuple(self.lifecycle_events)
        if lifecycle_events is not None:
            if self.horizon_state_identities and not lifecycle_events:
                raise ValueError("report lifecycle events are required for horizon states")
            if lifecycle_events != tuple(
                sorted(lifecycle_events, key=lambda item: item.event_time)
            ):
                raise ValueError("report lifecycle events must be time ordered")
            if len({item.event_time for item in lifecycle_events}) != len(lifecycle_events):
                raise ValueError("report lifecycle events must use unique event times")
            previous_position: dict[str, Decimal] = {}
            for index, event in enumerate(lifecycle_events):
                if (
                    not event.outcome_identities
                    or not event.report_identity
                    or not event.receipt_identity
                ):
                    raise ValueError("report lifecycle event must bind outcome, report and receipt")
                if index and event.event_time != lifecycle_events[index - 1].next_event_time:
                    raise ValueError("report lifecycle events must form a contiguous sequence")
                current = dict(event.physical_position)
                delta = dict(event.physical_delta)
                if set(current) != set(delta) and set(current) | set(delta):
                    raise ValueError("report lifecycle position and delta assets must match")
                expected = {
                    asset: previous_position.get(asset, Decimal("0"))
                    + delta.get(asset, Decimal("0"))
                    for asset in sorted(set(previous_position) | set(delta) | set(current))
                }
                if current != expected:
                    raise ValueError("report lifecycle physical position does not follow delta")
                previous_position = current
            if lifecycle_events and any(
                value != Decimal("0") for value in previous_position.values()
            ):
                raise ValueError("report lifecycle sequence must close all physical positions")
        object.__setattr__(self, "lifecycle_events", lifecycle_events)

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.report_contract,
            "source_class": self.source_class,
            "evidence_purpose": self.evidence_purpose,
            "decision_identity": self.decision_identity,
            "decision_closure_identity": self.decision_closure_identity,
            "outcome_identities": self.outcome_identities,
            "unavailable_outcome_identities": self.unavailable_outcome_identities,
            "blocked_outcome_identities": self.blocked_outcome_identities,
            "assets": tuple(item.canonical_payload for item in self.assets),
            "sleeve_allocations": tuple(item.canonical_payload for item in self.sleeve_allocations),
            "gross_forecast": tuple(self.gross_forecast.items()),
            "physical_notional": tuple(self.physical_notional.items()),
            "gross_expected_contribution": tuple(self.gross_expected_contribution.items()),
            "expected_cost": tuple(self.expected_cost.items()),
            "expected_cost_components": tuple(
                (asset, tuple(values.items()))
                for asset, values in self.expected_cost_components.items()
            ),
            "expected_cost_component_details": tuple(
                (asset, tuple(item.canonical_payload for item in values))
                for asset, values in self.expected_cost_component_details.items()
            ),
            "expected_net_contribution": tuple(self.expected_net_contribution.items()),
            "expected_net_return": tuple(self.expected_net_return.items()),
            "fx_translation_identity": self.fx_translation_identity,
            "group_exposure": self.group_exposure,
            "currency_exposure": self.currency_exposure,
            "group_exposure_before": self.group_exposure_before,
            "currency_exposure_before": self.currency_exposure_before,
            "marginal_risk_before": self.marginal_risk_before,
            "marginal_risk_after": self.marginal_risk_after,
            "allocations": self.allocations,
            "portfolio_risk_before": self.portfolio_risk_before,
            "portfolio_risk_after": self.portfolio_risk_after,
            "risk_residual": self.risk_residual,
            "risk_tolerance": self.risk_tolerance,
            "allocation_cost_residuals": self.allocation_cost_residuals,
            "allocation_pnl_residuals": self.allocation_pnl_residuals,
            "risk_state_identity": self.risk_state_identity,
            "risk_current_position": self.risk_current_position,
            "risk_target_position": self.risk_target_position,
            **(
                {"horizon_state_identities": self.horizon_state_identities}
                if self.horizon_state_identities
                else {}
            ),
            **(
                {
                    "lifecycle_events": tuple(
                        item.canonical_payload for item in self.lifecycle_events
                    )
                }
                if self.lifecycle_events
                else {}
            ),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)

    @property
    def semantic_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def closure_identity(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class VerificationReceipt:
    artefact_contract: str
    semantic_identity: str
    closure_identity: str
    parent_verification_identity: str
    verifier_contract: str
    checks: tuple[str, ...]
    receipt_identity: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.semantic_identity, "semantic identity"),
            (self.closure_identity, "closure identity"),
            (self.parent_verification_identity, "parent verification identity"),
        ):
            _digest(value, name)
        if not self.artefact_contract or not self.verifier_contract or not self.checks:
            raise ValueError("receipt contract and check set are required")
        expected_identity = identity(self.canonical_payload)
        if not self.receipt_identity:
            object.__setattr__(self, "receipt_identity", expected_identity)
        else:
            _digest(self.receipt_identity, "receipt identity")
            if self.receipt_identity != expected_identity:
                raise ValueError("receipt identity does not bind canonical payload")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": RECEIPT_CONTRACT,
            "artefact_contract": self.artefact_contract,
            "semantic_identity": self.semantic_identity,
            "closure_identity": self.closure_identity,
            "parent_verification_identity": self.parent_verification_identity,
            "verifier_contract": self.verifier_contract,
            "checks": self.checks,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(
            {**self.canonical_payload, "receipt_identity": self.receipt_identity}
        )


def _attribution_payload(item: SleeveAttribution) -> dict[str, object]:
    return {
        "sleeve_id": item.sleeve_id,
        "asset_id": item.asset_id,
        "key": item.key.as_json() if item.key is not None else None,
        "requested_delta": item.requested_delta,
        "internal_cross_quantity": item.internal_cross_quantity,
        "external_delta_share": item.external_delta_share,
        "repair_delta": item.repair_delta,
        "reason_codes": item.reason_codes,
    }


def _cost_state_payload(state: ExpectedCostState) -> object:
    return {
        "decision_time": state.decision_time,
        "current_quantity": state.current_quantity,
        "target_quantity": state.target_quantity,
        "holding_interval": state.holding_interval,
        "reporting_currency": state.reporting_currency,
        "internal_cross_quantity": state.internal_cross_quantity,
        "components": tuple(
            {
                "component": item.component,
                "status": item.status,
                "basis": item.basis,
                "native_amount": item.native_amount,
                "native_currency": item.native_currency,
                "reporting_amount": item.reporting_amount,
                "reporting_currency": item.reporting_currency,
                "quantity_basis": item.quantity_basis,
                "holding_interval": item.holding_interval,
                "version": item.version,
                "provenance": item.provenance,
                "conversion_rate": item.conversion_rate,
                "conversion_source": item.conversion_source,
                "conversion_version": item.conversion_version,
                "reason": item.reason,
            }
            for item in state.components
        ),
        "version": state.version,
        "provenance": state.provenance,
    }


def _select_quote(
    quotes: Sequence[QuoteEvidence],
    *,
    asset_id: str,
    minimum_time: datetime,
    direction: int,
    maximum_time: datetime | None = None,
    source_class: MarketDataSourceClass,
    evidence_purpose: EvidencePurpose,
) -> tuple[QuoteEvidence | None, tuple[str, ...]]:
    candidates = tuple(
        quote
        for quote in sorted(quotes, key=lambda item: (item.received_time, item.sequence))
        if (
            quote.asset_id == asset_id
            and quote.received_time >= minimum_time
            and (maximum_time is None or quote.received_time < maximum_time)
        )
    )
    if not candidates:
        return None, (EvaluationReasonCode.MISSING_QUOTE.value,)
    for quote in candidates:
        if quote.price_basis is not PriceBasis.MID:
            continue
        if quote.source_class is not source_class or quote.evidence_purpose is not evidence_purpose:
            continue
        if not quote.healthy or not quote.session_open or not quote.complete:
            continue
        if direction > 0 and quote.ask is None:
            continue
        if direction < 0 and quote.bid is None:
            continue
        return quote, ()
    reasons: list[str] = []
    if any(
        item.source_class is not source_class or item.evidence_purpose is not evidence_purpose
        for item in candidates
    ):
        reasons.append(EvaluationReasonCode.SOURCE_EVIDENCE_MISMATCH.value)
    matching = tuple(
        item
        for item in candidates
        if item.source_class is source_class and item.evidence_purpose is evidence_purpose
    )
    if any(item.price_basis is not PriceBasis.MID for item in matching):
        reasons.append(EvaluationReasonCode.UNSUPPORTED_PRICE_BASIS.value)
    if any(not item.healthy for item in matching):
        reasons.append(EvaluationReasonCode.STALE_QUOTE.value)
    if any(not item.session_open for item in matching):
        reasons.append(EvaluationReasonCode.CLOSED_SESSION.value)
    if any(not item.complete for item in matching):
        reasons.append(EvaluationReasonCode.INCOMPLETE_QUOTE.value)
    return None, tuple(dict.fromkeys(reasons or [EvaluationReasonCode.MISSING_QUOTE.value]))


def _decision_reference_quote(
    quotes: Sequence[QuoteEvidence],
    *,
    decision: DecisionClosure,
    asset_id: str,
) -> QuoteEvidence | None:
    candidates = sorted(
        (
            quote
            for quote in quotes
            if quote.asset_id == asset_id
            and quote.received_time <= decision.decision_time
            and quote.source_class is decision.source_class
            and quote.evidence_purpose is decision.evidence_purpose
            and quote.price_basis is PriceBasis.MID
            and quote.healthy
            and quote.session_open
            and quote.complete
        ),
        key=lambda item: (item.received_time, item.sequence),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _decision_reference_reasons(
    quotes: Sequence[QuoteEvidence],
    *,
    decision: DecisionClosure,
    asset_id: str,
) -> tuple[str, ...]:
    candidates = tuple(
        quote
        for quote in quotes
        if quote.asset_id == asset_id
        and quote.received_time <= decision.decision_time
        and quote.source_class is decision.source_class
        and quote.evidence_purpose is decision.evidence_purpose
    )
    if any(item.price_basis is not PriceBasis.MID for item in candidates):
        return (EvaluationReasonCode.UNSUPPORTED_PRICE_BASIS.value,)
    return (EvaluationReasonCode.MISSING_DECISION_REFERENCE.value,)


def _recompute_expected_costs(
    decision: DecisionClosure,
    *,
    holding_interval: timedelta | None = None,
) -> tuple[
    dict[str, Decimal],
    dict[str, dict[CostComponentKind, Decimal]],
    dict[str, tuple[ComponentCostReconciliation, ...]],
]:
    """Recompute component money and retain each return/AUD-basis equation."""
    totals: dict[str, Decimal] = {}
    components_by_asset: dict[str, dict[CostComponentKind, Decimal]] = {}
    details_by_asset: dict[str, tuple[ComponentCostReconciliation, ...]] = {}
    for index, asset in enumerate(decision.asset_order):
        model = decision.cost_models[asset]
        supplied = decision.expected_costs[asset]
        authoritative = model.evaluate(
            current_quantity=decision.current_position[index],
            target_quantity=decision.target_position[index],
            decision_time=decision.decision_time,
            internal_cross_quantity=supplied.internal_cross_quantity,
            holding_interval=holding_interval,
        )
        if _cost_state_payload(supplied) != _cost_state_payload(authoritative):
            raise ValueError(f"expected cost state identity does not bind economics for {asset}")
        external_quantity = abs(decision.physical_delta[index])
        aud_basis = decision.physical_notional[asset]
        if aud_basis <= 0 or not aud_basis.is_finite():
            raise ValueError(f"physical AUD notional must be finite and non-zero for {asset}")
        computed: dict[CostComponentKind, Decimal] = {}
        details: list[ComponentCostReconciliation] = []
        supplied_by_kind = {item.component: item for item in supplied.components}
        authoritative_by_kind = {item.component: item for item in authoritative.components}
        for component in model.components:
            quantity = (
                abs(decision.target_position[index])
                if component.basis is CostBasis.PHYSICAL_HOLDING
                else external_quantity
            )
            if component.status in {InputStatus.MISSING, InputStatus.UNSUPPORTED}:
                raise ValueError(
                    f"cost component unavailable for {asset}: {component.component.value}"
                )
            if holding_interval is None:
                money = component.evaluate(quantity) * component.conversion_rate
            else:
                authoritative_component = authoritative_by_kind[component.component]
                if authoritative_component.reporting_amount is None:
                    raise ValueError(
                        f"cost component amount is unavailable for "
                        f"{asset}: {component.component.value}"
                    )
                money = authoritative_component.reporting_amount
            state_component = supplied_by_kind[component.component]
            if state_component.reporting_amount != money:
                raise ValueError(
                    "cost component amount does not reconcile for "
                    f"{asset}: {component.component.value}"
                )
            computed[component.component] = money
            if (
                state_component.native_amount is None
                or state_component.native_currency is None
                or state_component.conversion_rate is None
            ):
                raise ValueError(
                    f"cost component basis is unavailable for {asset}: {component.component.value}"
                )
            if holding_interval is None:
                cost_return = money / aud_basis
            else:
                with localcontext() as context:
                    context.prec = 60
                    cost_return = money / aud_basis
            details.append(
                ComponentCostReconciliation(
                    component=component.component,
                    basis=component.basis,
                    quantity_basis=quantity,
                    native_amount=state_component.native_amount,
                    native_currency=state_component.native_currency,
                    reporting_amount=money,
                    reporting_currency=state_component.reporting_currency,
                    conversion_rate=state_component.conversion_rate,
                    cost_return=cost_return,
                    aud_notional_basis=aud_basis,
                )
            )
        components_by_asset[asset] = computed
        details_by_asset[asset] = tuple(details)
        totals[asset] = sum(computed.values(), Decimal("0"))
    return totals, components_by_asset, details_by_asset


def reconcile_positions(decision: DecisionClosure) -> dict[str, Decimal]:
    """Recompute physical movement and sleeve attribution without target code."""

    residuals: dict[str, Decimal] = {}
    for index, asset in enumerate(decision.asset_order):
        attributions = tuple(item for item in decision.attributions if item.asset_id == asset)
        requested = sum((item.requested_delta for item in attributions), Decimal("0"))
        external = sum((item.external_delta_share for item in attributions), Decimal("0"))
        crosses = sum((item.internal_cross_quantity for item in attributions), Decimal("0"))
        expected_delta = decision.target_position[index] - decision.current_position[index]
        expected_crosses = (
            decision.rounded_target.netting.internal_cross_quantity * Decimal("2")
            if decision.rounded_target is not None
            else crosses
        )
        # Repair deltas describe final external movement minus the original movement.
        # Reconcile requests against the pre-repair external movement, then require the
        # final external movement to equal the target physical delta.
        repair_delta = sum((item.repair_delta for item in attributions), Decimal("0"))
        original_external = external - repair_delta
        if crosses != expected_crosses or requested != original_external:
            raise ValueError(f"position attribution does not reconcile for {asset}")
        if external != expected_delta:
            raise ValueError(f"external movement does not reconcile for {asset}")
        residuals[asset] = expected_delta - external
    if any(value != 0 for value in residuals.values()):
        raise ValueError("position reconciliation residual is non-zero")
    return residuals


@dataclass(frozen=True, slots=True)
class _RiskProjection:
    group_after: tuple[tuple[str, Decimal], ...]
    currency_after: tuple[tuple[str, Decimal], ...]
    group_before: tuple[tuple[str, Decimal], ...]
    currency_before: tuple[tuple[str, Decimal], ...]
    marginal_before: tuple[tuple[str, Decimal], ...]
    marginal_after: tuple[tuple[str, Decimal], ...]
    allocations: tuple[tuple[str, Decimal], ...]
    portfolio_before: Decimal
    portfolio_after: Decimal
    residual: Decimal
    tolerance: Decimal


def _risk_projection_positions(
    risk_state: RiskState,
    current_position: Sequence[Decimal],
    target_position: Sequence[Decimal],
) -> _RiskProjection:
    """Recompute ordered risk equations from immutable state and positions."""
    before = tuple(float(value) for value in current_position)
    after = tuple(float(value) for value in target_position)
    risk_state.validate_position(before)
    risk_state.validate_position(after)

    def decimal(value: float) -> Decimal:
        return Decimal(str(value))

    def marginal(position: tuple[float, ...]) -> tuple[tuple[str, Decimal], ...]:
        denominator = risk_state.portfolio_risk(position)
        values = tuple(
            sum(
                risk_state.covariance[row][column] * position[column]
                for column in range(len(position))
            )
            / denominator
            if denominator > 0
            else 0.0
            for row in range(len(position))
        )
        return tuple(
            (asset, decimal(value))
            for asset, value in zip(risk_state.asset_order, values, strict=True)
        )

    group_before = tuple(
        (key, decimal(value))
        for key, value in zip(risk_state.group_keys, risk_state.group_exposure(before), strict=True)
    )
    group_after = tuple(
        (key, decimal(value))
        for key, value in zip(risk_state.group_keys, risk_state.group_exposure(after), strict=True)
    )
    currency_before = tuple(
        (key, decimal(value))
        for key, value in zip(
            risk_state.currency_keys, risk_state.currency_exposure(before), strict=True
        )
    )
    currency_after = tuple(
        (key, decimal(value))
        for key, value in zip(
            risk_state.currency_keys, risk_state.currency_exposure(after), strict=True
        )
    )
    portfolio_before = decimal(risk_state.portfolio_risk(before))
    portfolio_after = decimal(risk_state.portfolio_risk(after))
    tolerance = decimal(risk_state.caps.portfolio_risk_cap)
    return _RiskProjection(
        group_after,
        currency_after,
        group_before,
        currency_before,
        marginal(before),
        marginal(after),
        tuple(
            (asset, target)
            for asset, target in zip(risk_state.asset_order, target_position, strict=True)
        ),
        portfolio_before,
        portfolio_after,
        max(Decimal("0"), portfolio_after - tolerance),
        tolerance,
    )


def _risk_projection(decision: DecisionClosure) -> _RiskProjection:
    if decision.risk_state is None:
        raise ValueError("decision risk state is required for independent evaluation")
    return _risk_projection_positions(
        decision.risk_state, decision.current_position, decision.target_position
    )


def _allocate_sleeve_reconciliations(
    decision: DecisionClosure,
    assets: Sequence[AssetReconciliation],
    computed_components: Mapping[str, Mapping[CostComponentKind, Decimal]],
    *,
    cost_holding_interval: timedelta | None = None,
) -> tuple[SleeveReconciliation, ...]:
    """Allocate canonical physical costs and P&L to final R3.D sleeve movements."""
    result: list[SleeveReconciliation] = []
    for attribution in decision.attributions:
        index = decision.asset_order.index(attribution.asset_id)
        asset = decision.asset_order[index]
        siblings = tuple(item for item in decision.attributions if item.asset_id == asset)
        denominator = sum((abs(item.external_delta_share) for item in siblings), Decimal("0"))
        weight = (
            abs(attribution.external_delta_share) / denominator
            if denominator
            else Decimal("1") / Decimal(len(siblings))
        )
        components: dict[str, Decimal] = {}
        for kind in CostComponentKind:
            if (
                cost_holding_interval is not None
                and attribution.sleeve_id == siblings[-1].sleeve_id
            ):
                prior = sum(
                    (
                        item.expected_cost_components[kind.value]
                        for item in result
                        if item.asset_id == asset
                    ),
                    Decimal("0"),
                )
                components[kind.value] = computed_components[asset][kind] - prior
            else:
                components[kind.value] = computed_components[asset][kind] * weight
        if cost_holding_interval is not None and attribution.sleeve_id == siblings[-1].sleeve_id:
            prior_total = sum(
                (item.expected_cost for item in result if item.asset_id == asset),
                Decimal("0"),
            )
            components[CostComponentKind.FINANCING.value] += sum(
                computed_components[asset].values(), Decimal("0")
            ) - (prior_total + sum(components.values(), Decimal("0")))
        realised = assets[index].realised
        allocated = None
        previous: tuple[PnlBreakdown, ...] = ()
        if realised is not None:
            delta = decision.physical_delta[index]
            gross = (
                realised.gross_midpoint_pnl * attribution.external_delta_share / delta
                if delta
                else Decimal("0")
            )
            latency = (
                realised.latency_movement * attribution.external_delta_share / delta
                if delta
                else Decimal("0")
            )
            spread = realised.spread * weight
            slippage = realised.adverse_slippage * weight
            commission = realised.commission * weight
            financing = realised.financing * weight
            impact = realised.impact * weight
            fx = realised.fx_translation * weight
            if (
                cost_holding_interval is not None
                and attribution.sleeve_id == siblings[-1].sleeve_id
            ):
                previous = tuple(
                    item.realised
                    for item in result
                    if item.asset_id == asset and item.realised is not None
                )
                gross = realised.gross_midpoint_pnl - sum(
                    (item.gross_midpoint_pnl for item in previous), Decimal("0")
                )
                latency = realised.latency_movement - sum(
                    (item.latency_movement for item in previous), Decimal("0")
                )
                spread = realised.spread - sum((item.spread for item in previous), Decimal("0"))
                slippage = realised.adverse_slippage - sum(
                    (item.adverse_slippage for item in previous), Decimal("0")
                )
                commission = realised.commission - sum(
                    (item.commission for item in previous), Decimal("0")
                )
                financing = realised.financing - sum(
                    (item.financing for item in previous), Decimal("0")
                )
                impact = realised.impact - sum((item.impact for item in previous), Decimal("0"))
                fx = realised.fx_translation - sum(
                    (item.fx_translation for item in previous), Decimal("0")
                )
            if cost_holding_interval is None:
                net = gross - spread - slippage - commission - financing - impact - fx
            else:
                net = gross - (spread + slippage + commission + financing + impact + fx)
            if (
                cost_holding_interval is not None
                and attribution.sleeve_id == siblings[-1].sleeve_id
            ):
                target_net = realised.net_pnl - sum(
                    (item.net_pnl for item in previous), Decimal("0")
                )
                for _ in range(20):
                    net = gross - (spread + slippage + commission + financing + impact + fx)
                    financing -= target_net - net
                net = gross - (spread + slippage + commission + financing + impact + fx)
            allocated = PnlBreakdown(
                gross, spread, latency, slippage, commission, financing, impact, fx, net
            )
        result.append(
            SleeveReconciliation(
                attribution.sleeve_id,
                asset,
                attribution.external_delta_share,
                attribution.repair_delta,
                components,
                sum(components.values(), Decimal("0")),
                allocated,
                key=attribution.key,
                requested_delta=attribution.requested_delta,
                internal_cross_quantity=attribution.internal_cross_quantity,
                reason_codes=attribution.reason_codes,
            )
        )
    return tuple(sorted(result, key=lambda item: item.canonical_key))


def evaluate_independently(
    decision: DecisionClosure,
    quotes: Sequence[QuoteEvidence],
    *,
    latency: timedelta,
    adverse_slippage_increments: int = 0,
    fx_translation: Mapping[str, Decimal] | None = None,
    fx_translation_identity: str | None = None,
    cost_holding_interval: timedelta | None = None,
) -> EvaluationReport:
    """Recompute position, expected costs and executable-side P&L independently."""
    if latency < timedelta(0) or adverse_slippage_increments < 0:
        raise ValueError("latency and slippage must be non-negative")
    reconcile_positions(decision)
    expected, computed_components, component_details = _recompute_expected_costs(
        decision, holding_interval=cost_holding_interval
    )
    fx = dict(fx_translation or {})
    for asset, value in fx.items():
        _decimal(value, f"FX translation for {asset}")
    fx_payload = {
        "source_class": decision.source_class,
        "evidence_purpose": decision.evidence_purpose,
        "values": tuple(sorted(fx.items())),
    }
    expected_fx_identity = identity(fx_payload)
    if fx_translation_identity is not None and fx_translation_identity != expected_fx_identity:
        raise ValueError("FX translation identity does not bind source, evidence and values")
    assets: list[AssetReconciliation] = []
    outcome_ids: list[str] = []
    unavailable_outcome_ids: list[str] = []
    blocked_outcome_ids: list[str] = []
    for index, asset in enumerate(decision.asset_order):
        if asset in fx and fx[asset] < 0:
            outcome = OutcomeClosure(
                asset,
                decision.decision_time,
                decision.expiry_time,
                latency,
                None,
                None,
                EvaluationDisposition.UNAVAILABLE,
                (EvaluationReasonCode.BAD_FX.value,),
                physical_delta=decision.physical_delta[index],
                source_class=decision.source_class,
                evidence_purpose=decision.evidence_purpose,
                decision_semantic_identity=decision.semantic_identity,
                decision_closure_identity=decision.closure_identity,
            )
            unavailable_outcome_ids.append(outcome.semantic_identity)
            assets.append(
                AssetReconciliation(
                    asset,
                    EvaluationDisposition.UNAVAILABLE,
                    Decimal("0"),
                    expected[asset],
                    None,
                    (EvaluationReasonCode.BAD_FX.value,),
                    outcome_identity=outcome.semantic_identity,
                )
            )
            continue
        direction = (
            1
            if decision.physical_delta[index] > 0
            else -1
            if decision.physical_delta[index] < 0
            else 0
        )
        if direction == 0:
            outcome = OutcomeClosure(
                asset,
                decision.decision_time,
                decision.expiry_time,
                latency,
                None,
                None,
                EvaluationDisposition.ACCEPTED,
                physical_delta=decision.physical_delta[index],
                source_class=decision.source_class,
                evidence_purpose=decision.evidence_purpose,
                decision_semantic_identity=decision.semantic_identity,
                decision_closure_identity=decision.closure_identity,
            )
            outcome_ids.append(outcome.semantic_identity)
            assets.append(
                AssetReconciliation(
                    asset,
                    EvaluationDisposition.ACCEPTED,
                    Decimal("0"),
                    expected[asset],
                    None,
                    outcome_identity=outcome.semantic_identity,
                )
            )
            continue
        reference = _decision_reference_quote(quotes, decision=decision, asset_id=asset)
        entry, entry_reasons = _select_quote(
            quotes,
            asset_id=asset,
            minimum_time=decision.decision_time + latency,
            maximum_time=decision.expiry_time,
            direction=direction,
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
        )
        exit_quote, exit_reasons = _select_quote(
            quotes,
            asset_id=asset,
            minimum_time=decision.expiry_time,
            direction=-direction,
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
        )
        missing_reference = (
            ()
            if reference is not None
            else _decision_reference_reasons(quotes, decision=decision, asset_id=asset)
        )
        reasons = tuple(dict.fromkeys((*missing_reference, *entry_reasons, *exit_reasons)))
        if entry is None or exit_quote is None:
            outcome = OutcomeClosure(
                asset,
                decision.decision_time,
                decision.expiry_time,
                latency,
                entry,
                exit_quote,
                EvaluationDisposition.UNAVAILABLE,
                reasons,
                physical_delta=decision.physical_delta[index],
                decision_reference=reference,
                source_class=decision.source_class,
                evidence_purpose=decision.evidence_purpose,
                decision_semantic_identity=decision.semantic_identity,
                decision_closure_identity=decision.closure_identity,
            )
            unavailable_outcome_ids.append(outcome.semantic_identity)
            assets.append(
                AssetReconciliation(
                    asset,
                    EvaluationDisposition.UNAVAILABLE,
                    Decimal("0"),
                    expected[asset],
                    None,
                    reasons,
                    outcome_identity=outcome.semantic_identity,
                )
            )
            continue
        if entry.received_time >= exit_quote.received_time:
            raise ValueError("entry quote must precede exit quote")
        assert entry.midpoint is not None and exit_quote.midpoint is not None
        if (
            entry.bid is None
            or entry.ask is None
            or exit_quote.bid is None
            or exit_quote.ask is None
        ):
            raise ValueError("complete quote lost bid/ask during evaluation")
        quantity = abs(decision.physical_delta[index])
        if reference is None or reference.midpoint is None:
            outcome = OutcomeClosure(
                asset,
                decision.decision_time,
                decision.expiry_time,
                latency,
                entry,
                exit_quote,
                EvaluationDisposition.UNAVAILABLE,
                reasons,
                physical_delta=decision.physical_delta[index],
                decision_reference=reference,
                source_class=decision.source_class,
                evidence_purpose=decision.evidence_purpose,
                decision_semantic_identity=decision.semantic_identity,
                decision_closure_identity=decision.closure_identity,
            )
            unavailable_outcome_ids.append(outcome.semantic_identity)
            assets.append(
                AssetReconciliation(
                    asset,
                    EvaluationDisposition.UNAVAILABLE,
                    Decimal("0"),
                    expected[asset],
                    None,
                    reasons,
                    outcome_identity=outcome.semantic_identity,
                )
            )
            continue
        latency_movement = (entry.midpoint - reference.midpoint) * quantity * Decimal(direction)
        basis_midpoint = reference.midpoint
        gross_mid = (exit_quote.midpoint - basis_midpoint) * quantity * Decimal(direction)
        spread = abs(entry.ask - entry.bid) * quantity / Decimal("2") + abs(
            exit_quote.ask - exit_quote.bid
        ) * quantity / Decimal("2")
        slip = Decimal(adverse_slippage_increments) * quantity
        by_component = computed_components[asset]
        financing = by_component[CostComponentKind.FINANCING]
        commission = by_component[CostComponentKind.COMMISSION]
        impact = by_component[CostComponentKind.IMPACT]
        fx_cost = fx.get(asset, Decimal("0"))
        _decimal(fx_cost, f"FX translation for {asset}")
        if cost_holding_interval is None:
            net = gross_mid - spread - slip - commission - financing - impact - fx_cost
        else:
            net = gross_mid - (spread + slip + commission + financing + impact + fx_cost)
        pnl = PnlBreakdown(
            gross_mid, spread, latency_movement, slip, commission, financing, impact, fx_cost, net
        )
        outcome = OutcomeClosure(
            asset,
            decision.decision_time,
            decision.expiry_time,
            latency,
            entry,
            exit_quote,
            EvaluationDisposition.ACCEPTED,
            physical_delta=decision.physical_delta[index],
            decision_reference=reference,
            reference_to_entry_latency=entry.received_time - reference.received_time,
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
            decision_semantic_identity=decision.semantic_identity,
            decision_closure_identity=decision.closure_identity,
        )
        outcome_ids.append(outcome.semantic_identity)
        assets.append(
            AssetReconciliation(
                asset,
                EvaluationDisposition.ACCEPTED,
                Decimal("0"),
                expected[asset],
                pnl,
                outcome_identity=outcome.semantic_identity,
            )
        )
    sleeve_allocations = _allocate_sleeve_reconciliations(
        decision, assets, computed_components, cost_holding_interval=cost_holding_interval
    )
    risk = _risk_projection(decision)
    return EvaluationReport(
        source_class=decision.source_class,
        evidence_purpose=decision.evidence_purpose,
        decision_identity=decision.semantic_identity,
        decision_closure_identity=decision.closure_identity,
        outcome_identities=tuple(outcome_ids),
        unavailable_outcome_identities=tuple(unavailable_outcome_ids),
        blocked_outcome_identities=tuple(blocked_outcome_ids),
        assets=tuple(assets),
        sleeve_allocations=sleeve_allocations,
        gross_forecast=decision.gross_forecast_return,
        physical_notional=decision.physical_notional,
        expected_cost=expected,
        gross_expected_contribution=decision.gross_contribution,
        expected_net_return={
            asset: decision.gross_forecast_return[asset]
            - expected[asset] / decision.physical_notional[asset]
            for asset in decision.asset_order
        },
        expected_cost_components={
            asset: {
                component.value: amount for component, amount in computed_components[asset].items()
            }
            for asset in decision.asset_order
        },
        expected_cost_component_details=component_details,
        expected_net_contribution={
            asset: decision.gross_contribution[asset] - expected[asset]
            for asset in decision.asset_order
        },
        group_exposure=risk.group_after,
        currency_exposure=risk.currency_after,
        group_exposure_before=risk.group_before,
        currency_exposure_before=risk.currency_before,
        marginal_risk_before=risk.marginal_before,
        marginal_risk_after=risk.marginal_after,
        allocations=risk.allocations,
        portfolio_risk_before=risk.portfolio_before,
        portfolio_risk_after=risk.portfolio_after,
        risk_residual=risk.residual,
        risk_tolerance=risk.tolerance,
        fx_translation_identity=expected_fx_identity,
        risk_state=decision.risk_state,
        risk_current_position=decision.current_position,
        risk_target_position=decision.target_position,
        risk_state_identity=(decision.risk_state.semantic_identity or "")
        if decision.risk_state is not None
        else "",
        horizon_state_identities=()
        if not decision.horizon_states
        else tuple(
            (item.semantic_identity, item.closure_identity) for item in decision.horizon_states
        ),
    )


__all__ = [
    "DECISION_CONTRACT",
    "EVALUATION_CONTRACT",
    "REPORT_CONTRACT",
    "DecisionClosure",
    "EvaluationDisposition",
    "EvaluationReasonCode",
    "EvaluationReport",
    "OutcomeClosure",
    "PnlBreakdown",
    "QuoteEvidence",
    "SleeveAttribution",
    "VerificationReceipt",
    "build_outcome_closures",
    "canonical_bytes",
    "evaluate_independently",
    "identity",
    "reconcile_positions",
]


def build_outcome_closures(
    decision: DecisionClosure,
    quotes: Sequence[QuoteEvidence],
    *,
    latency: timedelta,
) -> tuple[OutcomeClosure, ...]:
    """Build immutable per-asset outcome closures using the causal quote rule."""
    if latency < timedelta(0):
        raise ValueError("latency must be non-negative")
    outcomes: list[OutcomeClosure] = []
    for index, asset in enumerate(decision.asset_order):
        direction = (
            1
            if decision.physical_delta[index] > 0
            else -1
            if decision.physical_delta[index] < 0
            else 0
        )
        if direction == 0:
            outcomes.append(
                OutcomeClosure(
                    asset,
                    decision.decision_time,
                    decision.expiry_time,
                    latency,
                    None,
                    None,
                    EvaluationDisposition.ACCEPTED,
                    physical_delta=decision.physical_delta[index],
                    source_class=decision.source_class,
                    evidence_purpose=decision.evidence_purpose,
                    decision_semantic_identity=decision.semantic_identity,
                    decision_closure_identity=decision.closure_identity,
                )
            )
            continue
        reference = _decision_reference_quote(quotes, decision=decision, asset_id=asset)
        entry, entry_reasons = _select_quote(
            quotes,
            asset_id=asset,
            minimum_time=decision.decision_time + latency,
            maximum_time=decision.expiry_time,
            direction=direction,
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
        )
        exit_quote, exit_reasons = _select_quote(
            quotes,
            asset_id=asset,
            minimum_time=decision.expiry_time,
            direction=-direction,
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
        )
        missing_reference = (
            ()
            if reference is not None
            else _decision_reference_reasons(quotes, decision=decision, asset_id=asset)
        )
        reasons = tuple(dict.fromkeys((*missing_reference, *entry_reasons, *exit_reasons)))
        outcomes.append(
            OutcomeClosure(
                asset,
                decision.decision_time,
                decision.expiry_time,
                latency,
                entry,
                exit_quote,
                EvaluationDisposition.ACCEPTED
                if entry is not None and exit_quote is not None and reference is not None
                else EvaluationDisposition.UNAVAILABLE,
                reasons,
                physical_delta=decision.physical_delta[index],
                decision_reference=reference,
                reference_to_entry_latency=(
                    entry.received_time - reference.received_time
                    if entry is not None and reference is not None
                    else None
                ),
                source_class=decision.source_class,
                evidence_purpose=decision.evidence_purpose,
                decision_semantic_identity=decision.semantic_identity,
                decision_closure_identity=decision.closure_identity,
            )
        )
    return tuple(outcomes)
