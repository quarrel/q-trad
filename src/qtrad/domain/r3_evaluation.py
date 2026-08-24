"""Independent R3.E position, physical-cost and executable P&L reconciliation.

This module deliberately does not construct targets or consume target aggregates.  The
evaluator accepts immutable decision inputs, recomputes ledgers from physical deltas,
and keeps expected and realised values separate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.r3_rounding import RoundedTarget
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
    evidence_identity: str = ""

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("quote asset identity is required")
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
        if not self.evidence_identity:
            object.__setattr__(self, "evidence_identity", identity(self.canonical_payload))
        else:
            _digest(self.evidence_identity, "quote evidence identity")

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
            "sequence": self.sequence,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)


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

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "outcome decision time")
        require_utc(self.target_time, "outcome target time")
        if self.target_time <= self.decision_time or self.latency < timedelta(0):
            raise ValueError("outcome times are invalid")
        if (
            self.disposition is EvaluationDisposition.ACCEPTED
            and (self.entry is None or self.exit is None)
            and not (self.entry is None and self.exit is None and not self.reason_codes)
        ):
            raise ValueError("accepted outcome requires entry and exit evidence")
        if self.entry is not None and self.entry.asset_id != self.asset_id:
            raise ValueError("entry asset mismatch")
        if self.exit is not None and self.exit.asset_id != self.asset_id:
            raise ValueError("exit asset mismatch")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "asset_id": self.asset_id,
            "decision_time": self.decision_time,
            "target_time": self.target_time,
            "latency": self.latency,
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

    def __post_init__(self) -> None:
        _decimal(self.position_residual, "position residual")
        _decimal(self.expected_cost, "expected cost")
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
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Immutable report closure; all values are independently recomputed."""

    source_class: MarketDataSourceClass
    evidence_purpose: EvidencePurpose
    decision_identity: str
    decision_closure_identity: str
    outcome_identities: tuple[str, ...]
    assets: tuple[AssetReconciliation, ...]
    gross_forecast: Mapping[str, Decimal]
    expected_cost: Mapping[str, Decimal]
    expected_net_contribution: Mapping[str, Decimal]
    group_exposure: tuple[tuple[str, Decimal], ...] = ()
    currency_exposure: tuple[tuple[str, Decimal], ...] = ()
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
        if any(len(value) != 64 for value in self.outcome_identities):
            raise ValueError("outcome identities must be SHA-256 digests")
        object.__setattr__(self, "gross_forecast", MappingProxyType(dict(self.gross_forecast)))
        object.__setattr__(self, "expected_cost", MappingProxyType(dict(self.expected_cost)))
        object.__setattr__(
            self,
            "expected_net_contribution",
            MappingProxyType(dict(self.expected_net_contribution)),
        )
        object.__setattr__(self, "group_exposure", tuple(self.group_exposure))
        object.__setattr__(self, "currency_exposure", tuple(self.currency_exposure))
        _decimal(self.risk_residual, "risk residual")
        _decimal(self.risk_tolerance, "risk tolerance")
        if self.risk_residual < 0 or self.risk_tolerance < 0:
            raise ValueError("risk residual and tolerance must be non-negative")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.report_contract,
            "source_class": self.source_class,
            "evidence_purpose": self.evidence_purpose,
            "decision_identity": self.decision_identity,
            "decision_closure_identity": self.decision_closure_identity,
            "outcome_identities": self.outcome_identities,
            "assets": tuple(item.canonical_payload for item in self.assets),
            "gross_forecast": tuple(sorted(self.gross_forecast.items())),
            "expected_cost": tuple(sorted(self.expected_cost.items())),
            "expected_net_contribution": tuple(sorted(self.expected_net_contribution.items())),
            "group_exposure": self.group_exposure,
            "currency_exposure": self.currency_exposure,
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
                "conversion_rate": item.conversion_rate,
                "conversion_source": item.conversion_source,
                "conversion_version": item.conversion_version,
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
) -> tuple[QuoteEvidence | None, tuple[str, ...]]:
    candidates = tuple(
        quote
        for quote in sorted(quotes, key=lambda item: (item.received_time, item.sequence))
        if quote.asset_id == asset_id and quote.received_time > minimum_time
    )
    if not candidates:
        return None, (EvaluationReasonCode.MISSING_QUOTE.value,)
    for quote in candidates:
        if not quote.healthy:
            continue
        if not quote.session_open:
            continue
        if not quote.complete:
            continue
        if direction > 0 and quote.ask is None:
            continue
        if direction < 0 and quote.bid is None:
            continue
        return quote, ()
    reasons: list[str] = []
    if any(not item.healthy for item in candidates):
        reasons.append(EvaluationReasonCode.STALE_QUOTE.value)
    if any(not item.session_open for item in candidates):
        reasons.append(EvaluationReasonCode.CLOSED_SESSION.value)
    if any(not item.complete for item in candidates):
        reasons.append(EvaluationReasonCode.INCOMPLETE_QUOTE.value)
    return None, tuple(dict.fromkeys(reasons or [EvaluationReasonCode.MISSING_QUOTE.value]))


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
        supplied = decision.expected_costs[asset]
        supplied_by_component = {
            component.component: component.reporting_amount for component in supplied.components
        }
        if any(supplied_by_component[k] != value for k, value in computed.items()):
            raise ValueError(f"expected cost state does not independently reconcile for {asset}")
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


def _risk_projection(
    decision: DecisionClosure,
) -> tuple[tuple[tuple[str, Decimal], ...], tuple[tuple[str, Decimal], ...], Decimal, Decimal]:
    """Recompute ordered exposure and the target-owned risk residual."""
    external = (
        decision.rounded_target.netting.external_deltas
        if decision.rounded_target is not None
        else decision.physical_delta
    )
    group_exposure = tuple(
        (asset, delta) for asset, delta in zip(decision.asset_order, external, strict=True)
    )
    currency_exposure = ((decision.reporting_currency, sum(external, Decimal("0"))),)
    residual = sum(
        (
            abs(delta - (target - current))
            for target, current, delta in zip(
                decision.target_position, decision.current_position, external, strict=True
            )
        ),
        Decimal("0"),
    )
    return group_exposure, currency_exposure, residual, Decimal("0")


def evaluate_independently(
    decision: DecisionClosure,
    quotes: Sequence[QuoteEvidence],
    *,
    latency: timedelta,
    adverse_slippage_increments: int = 0,
    fx_translation: Mapping[str, Decimal] | None = None,
) -> EvaluationReport:
    """Recompute position, expected costs and executable-side P&L independently."""

    if latency < timedelta(0) or adverse_slippage_increments < 0:
        raise ValueError("latency and slippage must be non-negative")
    reconcile_positions(decision)
    expected, computed_components = _recompute_expected_costs(decision)
    fx = dict(fx_translation or {})
    assets: list[AssetReconciliation] = []
    outcome_ids: list[str] = []
    for index, asset in enumerate(decision.asset_order):
        if asset in fx and fx[asset] < 0:
            assets.append(
                AssetReconciliation(
                    asset,
                    EvaluationDisposition.UNAVAILABLE,
                    Decimal("0"),
                    expected[asset],
                    None,
                    (EvaluationReasonCode.BAD_FX.value,),
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
            assets.append(
                AssetReconciliation(
                    asset, EvaluationDisposition.ACCEPTED, Decimal("0"), expected[asset], None
                )
            )
            continue
        entry, reasons = _select_quote(
            quotes,
            asset_id=asset,
            minimum_time=decision.decision_time + latency,
            direction=direction,
        )
        exit_quote, exit_reasons = _select_quote(
            quotes, asset_id=asset, minimum_time=decision.expiry_time, direction=-direction
        )
        if entry is None or exit_quote is None:
            all_reasons = tuple(dict.fromkeys((*reasons, *exit_reasons)))
            assets.append(
                AssetReconciliation(
                    asset,
                    EvaluationDisposition.UNAVAILABLE,
                    Decimal("0"),
                    expected[asset],
                    None,
                    all_reasons,
                )
            )
            continue
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
        # Costs are physical, not sleeve counts. Spread and adverse slippage are signed
        # conservative deductions from the midpoint result.
        spread = abs(entry.ask - entry.bid) * quantity / Decimal("2") + abs(
            exit_quote.ask - exit_quote.bid
        ) * quantity / Decimal("2")
        slip = Decimal(adverse_slippage_increments) * quantity
        by_component = computed_components[asset]
        financing = by_component[CostComponentKind.FINANCING]
        commission = by_component[CostComponentKind.COMMISSION]
        impact = by_component[CostComponentKind.IMPACT]
        latency_cost = by_component[CostComponentKind.LATENCY_MOVEMENT]
        fx_cost = fx.get(asset, Decimal("0"))
        _decimal(fx_cost, f"FX translation for {asset}")
        net = gross_mid - spread - latency_cost - slip - commission - financing - impact - fx_cost
        pnl = PnlBreakdown(
            gross_mid, spread, latency_cost, slip, commission, financing, impact, fx_cost, net
        )
        outcome = OutcomeClosure(
            asset,
            decision.decision_time,
            decision.expiry_time,
            latency,
            entry,
            exit_quote,
            EvaluationDisposition.ACCEPTED,
        )
        outcome_ids.append(outcome.semantic_identity)
        assets.append(
            AssetReconciliation(
                asset, EvaluationDisposition.ACCEPTED, Decimal("0"), expected[asset], pnl
            )
        )
    group_exposure, currency_exposure, risk_residual, risk_tolerance = _risk_projection(decision)
    return EvaluationReport(
        source_class=decision.source_class,
        evidence_purpose=decision.evidence_purpose,
        decision_identity=decision.semantic_identity,
        decision_closure_identity=decision.closure_identity,
        outcome_identities=tuple(outcome_ids),
        assets=tuple(assets),
        gross_forecast=decision.gross_forecast_return,
        expected_cost=expected,
        expected_net_contribution={
            asset: decision.gross_contribution[asset] - expected[asset]
            for asset in decision.asset_order
        },
        group_exposure=group_exposure,
        currency_exposure=currency_exposure,
        risk_residual=risk_residual,
        risk_tolerance=risk_tolerance,
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
                )
            )
            continue
        entry, entry_reasons = _select_quote(
            quotes,
            asset_id=asset,
            minimum_time=decision.decision_time + latency,
            direction=direction,
        )
        exit_quote, exit_reasons = _select_quote(
            quotes,
            asset_id=asset,
            minimum_time=decision.expiry_time,
            direction=-direction,
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
            )
        )
    return tuple(outcomes)
