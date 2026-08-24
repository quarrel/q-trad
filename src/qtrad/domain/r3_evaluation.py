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
from decimal import Decimal
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
from qtrad.domain.r3_rounding import RoundedTarget
from qtrad.domain.risk import RiskState
from qtrad.domain.time import require_utc

EVALUATION_CONTRACT = "qtrad-r3-independent-evaluation-v1"
DECISION_CONTRACT = "qtrad-r3-decision-closure-v1"
OUTCOME_CONTRACT = "qtrad-r3-outcome-closure-v1"
REPORT_CONTRACT = "qtrad-r3-independent-report-v1"
RECEIPT_CONTRACT = "qtrad-r3-verification-receipt-v1"


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
    """Independent copy of final sleeve movement, including non-alpha repair."""

    sleeve_id: str
    asset_id: str
    requested_delta: Decimal
    internal_cross_quantity: Decimal
    external_delta_share: Decimal
    repair_delta: Decimal = Decimal("0")
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sleeve_id or not self.asset_id:
            raise ValueError("sleeve and asset identity are required")
        for value, name in (
            (self.requested_delta, "requested delta"),
            (self.internal_cross_quantity, "internal cross quantity"),
            (self.external_delta_share, "external delta share"),
            (self.repair_delta, "repair delta"),
        ):
            _decimal(value, name)
        if self.internal_cross_quantity < 0:
            raise ValueError("internal cross quantity must be non-negative")
        # The pre-repair external movement is the amount requested after internal matching.
        if abs(self.external_delta_share - self.repair_delta) + self.internal_cross_quantity != abs(
            self.requested_delta
        ):
            raise ValueError("sleeve attribution does not reconcile requested delta")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def canonical_key(self) -> tuple[str, str]:
        return (self.asset_id, self.sleeve_id)


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
        if not self.reporting_currency:
            raise ValueError("decision reporting currency is required")
        _digest(self.decision_input_identity, "decision input identity")
        _digest(self.parent_verification_identity, "parent verification identity")
        if self.rounded_target is not None:
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
            expected_attributions = {
                (item.key.asset_id, item.key.configuration_id): item for item in target.attributions
            }
            actual_attributions = {
                (item.asset_id, item.sleeve_id): item for item in self.attributions
            }
            if set(expected_attributions) != set(actual_attributions):
                raise ValueError("decision attributions do not bind rounded target")
            for key, item in expected_attributions.items():
                actual = actual_attributions[key]
                if (
                    actual.requested_delta != item.requested_delta
                    or actual.internal_cross_quantity != item.internal_cross_quantity
                    or actual.external_delta_share != item.external_delta_share
                ):
                    raise ValueError("decision attribution differs from rounded target")
        if self.risk_state is not None and (
            self.risk_state.source_class is not self.source_class
            or self.risk_state.evidence_purpose is not self.evidence_purpose
            or self.risk_state.asset_order != assets
        ):
            raise ValueError("decision risk state does not bind source, evidence or asset order")
        object.__setattr__(
            self, "gross_forecast_return", MappingProxyType(dict(self.gross_forecast_return))
        )
        object.__setattr__(
            self, "gross_contribution", MappingProxyType(dict(self.gross_contribution))
        )
        object.__setattr__(self, "expected_costs", MappingProxyType(dict(self.expected_costs)))
        object.__setattr__(self, "cost_models", MappingProxyType(dict(self.cost_models)))
        if set(self.gross_forecast_return) != set(assets) or set(self.gross_contribution) != set(
            assets
        ):
            raise ValueError("gross fields must cover every asset")
        if set(self.expected_costs) != set(assets) or set(self.cost_models) != set(assets):
            raise ValueError("cost fields must cover every asset")
        for value in (*self.gross_forecast_return.values(), *self.gross_contribution.values()):
            _decimal(value, "gross value")
        if (
            tuple(sorted(self.attributions, key=lambda item: item.canonical_key))
            != self.attributions
        ):
            raise ValueError("decision attributions must be canonical")
        if any(item.asset_id not in assets for item in self.attributions):
            raise ValueError("decision attribution asset is unknown")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
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

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "outcome decision time")
        require_utc(self.target_time, "outcome target time")
        _decimal(self.physical_delta, "outcome physical delta")
        if self.target_time <= self.decision_time or self.latency < timedelta(0):
            raise ValueError("outcome times are invalid")
        if type(self.disposition) is not EvaluationDisposition:
            raise ValueError("outcome disposition must be a declared enum")
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
        if self.exit is not None and self.exit.received_time <= self.target_time:
            raise ValueError("exit quote must be strictly after target time")
        if self.entry is not None and self.exit is not None:
            if self.entry.received_time >= self.exit.received_time:
                raise ValueError("entry quote must precede exit quote")
            if self.entry.closure_identity == self.exit.closure_identity:
                raise ValueError("entry and exit quote evidence must be distinct")
            if (
                self.entry.source_class is not self.exit.source_class
                or self.entry.evidence_purpose is not self.exit.evidence_purpose
            ):
                raise ValueError("entry and exit source/evidence mismatch")
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
        expected = self.gross_midpoint_pnl - (
            self.spread
            + self.latency_movement
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
        return (
            self.spread
            + self.latency_movement
            + self.adverse_slippage
            + self.commission
            + self.impact
        )

    @property
    def total_cost(self) -> Decimal:
        return self.transaction_cost + self.financing + self.fx_translation

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "gross_midpoint_pnl": self.gross_midpoint_pnl,
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
class EvaluationReport:
    """Immutable report closure; all values are independently recomputed."""

    source_class: MarketDataSourceClass
    evidence_purpose: EvidencePurpose
    decision_identity: str
    decision_closure_identity: str
    outcome_identities: tuple[str, ...]
    unavailable_outcome_identities: tuple[str, ...]
    assets: tuple[AssetReconciliation, ...]
    gross_forecast: Mapping[str, Decimal]
    expected_cost: Mapping[str, Decimal]
    expected_net_contribution: Mapping[str, Decimal]
    blocked_outcome_identities: tuple[str, ...] = ()
    gross_expected_contribution: Mapping[str, Decimal] = MappingProxyType({})
    expected_net_return: Mapping[str, Decimal] = MappingProxyType({})
    expected_cost_components: Mapping[str, Mapping[str, Decimal]] = MappingProxyType({})
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
    report_contract: str = REPORT_CONTRACT

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
        mappings = {
            "gross forecast": MappingProxyType(dict(self.gross_forecast)),
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
        object.__setattr__(self, "gross_forecast", mappings["gross forecast"])
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
            != self.gross_forecast[asset] - self.expected_cost[asset]
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
        object.__setattr__(self, "expected_cost_components", MappingProxyType(components))
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
        if self.fx_translation_identity:
            _digest(self.fx_translation_identity, "FX translation identity")

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
            "gross_forecast": tuple(self.gross_forecast.items()),
            "gross_expected_contribution": tuple(self.gross_expected_contribution.items()),
            "expected_cost": tuple(self.expected_cost.items()),
            "expected_cost_components": tuple(
                (asset, tuple(values.items()))
                for asset, values in self.expected_cost_components.items()
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
        if not self.receipt_identity:
            object.__setattr__(self, "receipt_identity", identity(self.canonical_payload))
        else:
            _digest(self.receipt_identity, "receipt identity")

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
            and quote.healthy
            and quote.session_open
            and quote.complete
        ),
        key=lambda item: (item.received_time, item.sequence),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _recompute_expected_costs(
    decision: DecisionClosure,
) -> tuple[dict[str, Decimal], dict[str, dict[CostComponentKind, Decimal]]]:
    """Recompute component money from the final physical movement.

    The supplied ExpectedCostState is checked as an immutable closure, never
    used as the source of the total. Transaction costs apply once to the final
    physical delta; financing remains bound to the final position.
    """

    totals: dict[str, Decimal] = {}
    components_by_asset: dict[str, dict[CostComponentKind, Decimal]] = {}
    for index, asset in enumerate(decision.asset_order):
        model = decision.cost_models[asset]
        supplied = decision.expected_costs[asset]
        authoritative = model.evaluate(
            current_quantity=decision.current_position[index],
            target_quantity=decision.target_position[index],
            decision_time=decision.decision_time,
            internal_cross_quantity=supplied.internal_cross_quantity,
        )
        if _cost_state_payload(supplied) != _cost_state_payload(authoritative):
            raise ValueError(f"expected cost state identity does not bind economics for {asset}")
        external_quantity = abs(decision.physical_delta[index])
        computed: dict[CostComponentKind, Decimal] = {}
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
            computed[component.component] = component.evaluate(quantity) * component.conversion_rate
        components_by_asset[asset] = computed
        totals[asset] = sum(computed.values(), Decimal("0"))
    return totals, components_by_asset


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
        # Every internal match is represented once per sleeve and therefore cancels pairwise.
        if crosses != expected_crosses or requested != expected_delta + sum(
            item.repair_delta for item in attributions
        ):
            raise ValueError(f"position attribution does not reconcile for {asset}")
        if external != expected_delta + sum(item.repair_delta for item in attributions):
            raise ValueError(f"external movement does not reconcile for {asset}")
        residuals[asset] = expected_delta - (
            external - sum(item.repair_delta for item in attributions)
        )
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


def _risk_projection(decision: DecisionClosure) -> _RiskProjection:
    """Project positions through the authoritative ordered R3.D risk state."""
    risk = decision.risk_state
    if risk is None:
        raise ValueError("decision risk state is required for independent evaluation")
    risk_state = risk
    before = tuple(float(value) for value in decision.current_position)
    after = tuple(float(value) for value in decision.target_position)
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
            for asset, target in zip(decision.asset_order, decision.target_position, strict=True)
        ),
        portfolio_before,
        portfolio_after,
        max(Decimal("0"), portfolio_after - tolerance),
        tolerance,
    )


def evaluate_independently(
    decision: DecisionClosure,
    quotes: Sequence[QuoteEvidence],
    *,
    latency: timedelta,
    adverse_slippage_increments: int = 0,
    fx_translation: Mapping[str, Decimal] | None = None,
    fx_translation_identity: str | None = None,
) -> EvaluationReport:
    """Recompute position, expected costs and executable-side P&L independently."""
    if latency < timedelta(0) or adverse_slippage_increments < 0:
        raise ValueError("latency and slippage must be non-negative")
    reconcile_positions(decision)
    expected, computed_components = _recompute_expected_costs(decision)
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
        reasons = tuple(dict.fromkeys((*entry_reasons, *exit_reasons)))
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
        gross_mid = (exit_quote.midpoint - entry.midpoint) * quantity * Decimal(direction)
        reference = _decision_reference_quote(quotes, decision=decision, asset_id=asset)
        latency_movement = (
            (entry.midpoint - reference.midpoint) * quantity * Decimal(direction)
            if reference is not None and reference.midpoint is not None
            else Decimal("0")
        )
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
        net = (
            gross_mid - spread - latency_movement - slip - commission - financing - impact - fx_cost
        )
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
        gross_forecast=decision.gross_forecast_return,
        expected_cost=expected,
        gross_expected_contribution=decision.gross_contribution,
        expected_net_return={
            asset: decision.gross_forecast_return[asset] - expected[asset]
            for asset in decision.asset_order
        },
        expected_cost_components={
            asset: {
                component.value: amount for component, amount in computed_components[asset].items()
            }
            for asset in decision.asset_order
        },
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
                )
            )
            continue
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
        reasons = tuple(dict.fromkeys((*entry_reasons, *exit_reasons)))
        outcomes.append(
            OutcomeClosure(
                asset,
                decision.decision_time,
                decision.expiry_time,
                latency,
                entry,
                exit_quote,
                EvaluationDisposition.ACCEPTED
                if entry is not None and exit_quote is not None
                else EvaluationDisposition.UNAVAILABLE,
                reasons,
                physical_delta=decision.physical_delta[index],
            )
        )
    return tuple(outcomes)
