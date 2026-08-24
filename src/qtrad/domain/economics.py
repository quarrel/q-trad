"""Provider-neutral R3 product economics and expected-cost contracts.

The module contains immutable input states used by the R3 portfolio boundary.  It
does not import a provider or an optimiser.  Missing and unsupported inputs stay
explicit so callers can fail closed rather than silently treating them as zero.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from math import floor
from typing import Final

from qtrad.domain.time import require_utc

ECONOMICS_CONTRACT: Final = "qtrad-r3-economics-v1"
SOLVER_POLICY_CONTRACT: Final = "qtrad-r3-solver-policy-v1"
COST_ADJUSTED_RETURN_UNIT: Final = "REPORTING_RETURN"


class InputStatus(StrEnum):
    """Availability semantics for an economic input."""

    AVAILABLE = "AVAILABLE"
    DOCUMENTED_ZERO = "DOCUMENTED_ZERO"
    MISSING = "MISSING"
    UNSUPPORTED = "UNSUPPORTED"


class ImpactDisposition(StrEnum):
    """How a quantity-dependent impact assumption is bounded."""

    SUPPORTED_MODEL = "SUPPORTED_MODEL"
    CAPPED_NO_IMPACT_RANGE = "CAPPED_NO_IMPACT_RANGE"
    UNSUPPORTED_BLOCKING = "UNSUPPORTED_BLOCKING"


class CostComponentKind(StrEnum):
    """The physical cost components retained by R3."""

    SPREAD = "SPREAD"
    LATENCY_MOVEMENT = "LATENCY_MOVEMENT"
    ADVERSE_SLIPPAGE = "ADVERSE_SLIPPAGE"
    COMMISSION = "COMMISSION"
    FINANCING = "FINANCING"
    IMPACT = "IMPACT"


class CostBasis(StrEnum):
    """The physical quantity or interval to which a cost is charged."""

    PHYSICAL_DELTA = "PHYSICAL_DELTA"
    PHYSICAL_HOLDING = "PHYSICAL_HOLDING"
    INTERNAL_CROSS = "INTERNAL_CROSS"


class SessionState(StrEnum):
    """Eligibility of the reviewed paper session at a decision boundary."""

    ELIGIBLE = "ELIGIBLE"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CostSchedule:
    """A versioned per-quantity and minimum charge schedule.

    `MISSING` and `UNSUPPORTED` deliberately carry no amount.  A zero
    schedule is represented by `DOCUMENTED_ZERO` and never inferred from an
    absent field.
    """

    status: InputStatus
    currency: str | None
    per_quantity: Decimal | None
    minimum: Decimal | None
    basis: CostBasis
    version: str
    provenance: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not InputStatus or type(self.basis) is not CostBasis:
            raise ValueError("cost schedule status and basis must use declared enums")
        if self.currency is not None:
            _require_currency(self.currency, "cost schedule currency")
        if not self.version or not self.provenance:
            raise ValueError("cost schedule version and provenance are required")
        if self.status in {InputStatus.MISSING, InputStatus.UNSUPPORTED}:
            if self.per_quantity is not None or self.minimum is not None:
                raise ValueError("missing or unsupported schedule cannot carry an amount")
            if not self.reason:
                raise ValueError("missing or unsupported schedule requires a reason")
            return
        if self.currency is None or self.per_quantity is None or self.minimum is None:
            raise ValueError("available schedule requires currency and amounts")
        _require_nonnegative(self.per_quantity, "cost schedule per_quantity")
        _require_nonnegative(self.minimum, "cost schedule minimum")
        if self.status is InputStatus.DOCUMENTED_ZERO and (
            self.per_quantity != Decimal("0") or self.minimum != Decimal("0")
        ):
            raise ValueError("documented-zero schedule must have zero amounts")

    @classmethod
    def available(
        cls,
        *,
        currency: str,
        per_quantity: Decimal,
        minimum: Decimal,
        basis: CostBasis,
        version: str,
        provenance: str,
    ) -> CostSchedule:
        return cls(
            InputStatus.AVAILABLE,
            currency,
            per_quantity,
            minimum,
            basis,
            version,
            provenance,
        )

    @classmethod
    def documented_zero(
        cls,
        *,
        currency: str,
        basis: CostBasis,
        version: str,
        provenance: str,
    ) -> CostSchedule:
        return cls(
            InputStatus.DOCUMENTED_ZERO,
            currency,
            Decimal("0"),
            Decimal("0"),
            basis,
            version,
            provenance,
        )

    @classmethod
    def missing(
        cls,
        *,
        basis: CostBasis,
        version: str,
        provenance: str,
        reason: str,
        currency: str | None = None,
    ) -> CostSchedule:
        return cls(InputStatus.MISSING, currency, None, None, basis, version, provenance, reason)

    @classmethod
    def unsupported(
        cls,
        *,
        basis: CostBasis,
        version: str,
        provenance: str,
        reason: str,
        currency: str | None = None,
    ) -> CostSchedule:
        return cls(
            InputStatus.UNSUPPORTED,
            currency,
            None,
            None,
            basis,
            version,
            provenance,
            reason,
        )

    @property
    def is_available(self) -> bool:
        return self.status in {InputStatus.AVAILABLE, InputStatus.DOCUMENTED_ZERO}

    def amount_for(self, quantity: Decimal) -> Decimal | None:
        """Return the charge for a non-negative physical quantity, if supported."""

        _require_nonnegative(quantity, "cost schedule quantity")
        if not self.is_available:
            return None
        if quantity == 0:
            return Decimal("0")
        assert self.per_quantity is not None
        assert self.minimum is not None
        return max(self.minimum, quantity * self.per_quantity)


@dataclass(frozen=True, slots=True)
class FXRate:
    """A causal, health-checked conversion rate.

    `rate` is quote currency per one base currency.  A direct inverse is
    supported without introducing a second, unbound path.
    """

    base_currency: str
    quote_currency: str
    rate: Decimal | None
    observed_at: datetime
    max_age: timedelta
    status: InputStatus
    source: str
    version: str
    healthy: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not InputStatus:
            raise ValueError("FX status must use the declared InputStatus enum")
        _require_currency(self.base_currency, "FX base currency")
        _require_currency(self.quote_currency, "FX quote currency")
        require_utc(self.observed_at, "FX observed_at")
        if self.max_age <= timedelta(0):
            raise ValueError("FX max_age must be positive")
        if not self.source or not self.version:
            raise ValueError("FX source and version are required")
        if self.status in {InputStatus.MISSING, InputStatus.UNSUPPORTED}:
            if self.rate is not None or not self.reason:
                raise ValueError("missing or unsupported FX requires a reason and no rate")
            return
        if self.rate is None or not self.rate.is_finite() or self.rate <= 0:
            raise ValueError("available FX rate must be finite and positive")
        if self.base_currency == self.quote_currency and self.rate != Decimal("1"):
            raise ValueError("identity FX rate must equal one")

    @classmethod
    def identity(
        cls,
        *,
        currency: str,
        observed_at: datetime,
        max_age: timedelta,
        source: str,
        version: str,
    ) -> FXRate:
        return cls(
            currency,
            currency,
            Decimal("1"),
            observed_at,
            max_age,
            InputStatus.AVAILABLE,
            source,
            version,
        )

    @classmethod
    def missing(
        cls,
        *,
        base_currency: str,
        quote_currency: str,
        observed_at: datetime,
        max_age: timedelta,
        source: str,
        version: str,
        reason: str,
    ) -> FXRate:
        return cls(
            base_currency,
            quote_currency,
            None,
            observed_at,
            max_age,
            InputStatus.MISSING,
            source,
            version,
            reason=reason,
        )

    @classmethod
    def unsupported(
        cls,
        *,
        base_currency: str,
        quote_currency: str,
        observed_at: datetime,
        max_age: timedelta,
        source: str,
        version: str,
        reason: str,
    ) -> FXRate:
        return cls(
            base_currency,
            quote_currency,
            None,
            observed_at,
            max_age,
            InputStatus.UNSUPPORTED,
            source,
            version,
            reason=reason,
        )

    def is_current(self, at: datetime) -> bool:
        require_utc(at, "FX decision time")
        return (
            self.status is InputStatus.AVAILABLE
            and self.healthy
            and self.observed_at <= at <= self.observed_at + self.max_age
        )

    def covers(self, from_currency: str, to_currency: str, *, at: datetime) -> bool:
        """Return whether this causal rate covers a current direct conversion."""
        _require_currency(from_currency, "FX from currency")
        _require_currency(to_currency, "FX to currency")
        if from_currency == to_currency:
            return True
        return self.is_current(at) and (
            (self.base_currency == from_currency and self.quote_currency == to_currency)
            or (self.base_currency == to_currency and self.quote_currency == from_currency)
        )

    def factor_to(self, currency: str, *, at: datetime) -> Decimal | None:
        """Return a direct factor from `base_currency` into `currency`."""

        _require_currency(currency, "FX conversion currency")
        if not self.is_current(at) or self.rate is None:
            return None
        if currency == self.base_currency:
            return Decimal("1")
        if currency == self.quote_currency:
            return self.rate
        return None

    def convert(
        self,
        amount: Decimal,
        *,
        from_currency: str,
        to_currency: str,
        at: datetime,
    ) -> Decimal | None:
        _require_finite(amount, "FX amount")
        _require_currency(from_currency, "FX from currency")
        _require_currency(to_currency, "FX to currency")
        if from_currency == to_currency:
            return amount
        if not self.is_current(at) or self.rate is None:
            return None
        if from_currency == self.base_currency and to_currency == self.quote_currency:
            return amount * self.rate
        if from_currency == self.quote_currency and to_currency == self.base_currency:
            return amount / self.rate
        return None


@dataclass(frozen=True, slots=True)
class Eligibility:
    """Fail-closed paper-eligibility result."""

    eligible: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.eligible and self.reasons:
            raise ValueError("eligible result cannot contain denial reasons")
        if not self.eligible and not self.reasons:
            raise ValueError("ineligible result requires a reason")


@dataclass(frozen=True, slots=True)
class ProductEconomics:
    """Reviewed, provider-neutral economics for one canonical asset."""

    asset_id: str
    source_class: str
    source_product_id: str
    price_currency: str
    settlement_currency: str
    reporting_currency: str
    contract_size: Decimal
    value_per_price_unit: Decimal
    minimum_quantity: Decimal
    quantity_increment: Decimal
    tick_size: Decimal
    tick_value: Decimal
    commission: CostSchedule
    financing: CostSchedule
    impact_disposition: ImpactDisposition
    session_state: SessionState
    session_version: str
    effective_from: datetime
    observed_at: datetime
    economics_max_age: timedelta
    version: str
    provenance: str
    fx_price_to_settlement: FXRate | None
    fx_settlement_to_reporting: FXRate | None
    impact_version: str | None = None
    impact_max_quantity: Decimal | None = None
    impact_reason: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.impact_disposition) is not ImpactDisposition
            or type(self.session_state) is not SessionState
        ):
            raise ValueError("impact disposition and session state must use declared enums")
        for value, field_name in (
            (self.price_currency, "price currency"),
            (self.settlement_currency, "settlement currency"),
            (self.reporting_currency, "reporting currency"),
        ):
            _require_currency(value, field_name)
        for value, field_name in (
            (self.contract_size, "contract size"),
            (self.value_per_price_unit, "value per price unit"),
            (self.minimum_quantity, "minimum quantity"),
            (self.quantity_increment, "quantity increment"),
            (self.tick_size, "tick size"),
            (self.tick_value, "tick value"),
        ):
            _require_positive(value, field_name)
        if not self.asset_id or not self.source_class or not self.source_product_id:
            raise ValueError("asset, source class and source product identity are required")
        if not self.session_version or not self.version or not self.provenance:
            raise ValueError("economics version, session version and provenance are required")
        require_utc(self.effective_from, "economics effective_from")
        require_utc(self.observed_at, "economics observed_at")
        if self.observed_at < self.effective_from:
            raise ValueError("economics observation cannot precede effective_from")
        if self.economics_max_age <= timedelta(0):
            raise ValueError("economics max age must be positive")
        if self.commission.basis is not CostBasis.PHYSICAL_DELTA:
            raise ValueError("commission must bind the physical delta")
        if self.financing.basis is not CostBasis.PHYSICAL_HOLDING:
            raise ValueError("financing must bind the physical holding")
        if (self.minimum_quantity / self.quantity_increment) % 1 != 0:
            raise ValueError("minimum quantity must be a quantity-increment multiple")
        if self.impact_disposition is ImpactDisposition.SUPPORTED_MODEL and not self.impact_version:
            raise ValueError("supported impact requires a model version")
        if self.impact_disposition is ImpactDisposition.CAPPED_NO_IMPACT_RANGE:
            if self.impact_max_quantity is None or self.impact_max_quantity <= 0:
                raise ValueError("capped impact requires a positive maximum quantity")
            if not self.impact_version:
                raise ValueError("capped impact requires a version")
        if (
            self.impact_disposition is ImpactDisposition.UNSUPPORTED_BLOCKING
            and not self.impact_reason
        ):
            raise ValueError("unsupported impact requires a reason")
        if self.impact_max_quantity is not None:
            _require_positive(self.impact_max_quantity, "impact maximum quantity")
        for source, target, fx, label in (
            (
                self.price_currency,
                self.settlement_currency,
                self.fx_price_to_settlement,
                "price FX",
            ),
            (
                self.settlement_currency,
                self.reporting_currency,
                self.fx_settlement_to_reporting,
                "settlement FX",
            ),
        ):
            if fx is None:
                continue
            if source == target:
                if (
                    fx.base_currency != source
                    or fx.quote_currency != target
                    or fx.rate != Decimal("1")
                ):
                    raise ValueError(f"{label} must be an identity conversion")
            elif {fx.base_currency, fx.quote_currency} != {source, target}:
                raise ValueError(f"{label} does not cover the required currency pair")

    def eligibility(
        self,
        *,
        decision_time: datetime,
        proposed_quantity: Decimal | None = None,
    ) -> Eligibility:
        """Return eligibility without inventing any missing economic input."""

        require_utc(decision_time, "economics decision_time")
        if proposed_quantity is not None:
            _require_nonnegative(proposed_quantity, "proposed quantity")
        reasons: list[str] = []
        if decision_time < self.effective_from:
            reasons.append("ECONOMICS_NOT_EFFECTIVE")
        if decision_time < self.observed_at:
            reasons.append("ECONOMICS_OBSERVED_IN_FUTURE")
        elif decision_time > self.observed_at + self.economics_max_age:
            reasons.append("ECONOMICS_OBSERVATION_STALE")
        if self.session_state is not SessionState.ELIGIBLE:
            reasons.append("SESSION_NOT_ELIGIBLE")
        if not self.commission.is_available:
            reasons.append(f"COMMISSION_{self.commission.status.value}")
        if not self.financing.is_available:
            reasons.append(f"FINANCING_{self.financing.status.value}")
        for source, target, fx, label in (
            (
                self.price_currency,
                self.settlement_currency,
                self.fx_price_to_settlement,
                "PRICE_FX",
            ),
            (
                self.settlement_currency,
                self.reporting_currency,
                self.fx_settlement_to_reporting,
                "SETTLEMENT_FX",
            ),
        ):
            if source == target:
                continue
            if fx is None:
                reasons.append(f"{label}_MISSING")
            elif not fx.covers(source, target, at=decision_time):
                reasons.append(f"{label}_UNAVAILABLE_OR_STALE")
        if self.impact_disposition is ImpactDisposition.UNSUPPORTED_BLOCKING:
            reasons.append("IMPACT_UNSUPPORTED")
        elif self.impact_disposition is ImpactDisposition.CAPPED_NO_IMPACT_RANGE:
            if proposed_quantity is None:
                reasons.append("IMPACT_QUANTITY_REQUIRED")
            elif (
                self.impact_max_quantity is not None
                and proposed_quantity > self.impact_max_quantity
            ):
                reasons.append("IMPACT_QUANTITY_EXCEEDS_CAP")
        return Eligibility(not reasons, tuple(reasons))

    def round_quantity(self, quantity: Decimal) -> Decimal:
        """Conservatively round a signed quantity to valid product increments."""

        _require_finite(quantity, "quantity")
        if quantity == 0:
            return Decimal("0")
        magnitude = abs(quantity)
        increments = floor(magnitude / self.quantity_increment)
        rounded = self.quantity_increment * increments
        if rounded < self.minimum_quantity:
            return Decimal("0")
        return rounded if quantity > 0 else -rounded


@dataclass(frozen=True, slots=True)
class ComponentCost:
    """One expected physical cost component in native and reporting money."""

    component: CostComponentKind
    status: InputStatus
    basis: CostBasis
    native_amount: Decimal | None
    native_currency: str | None
    reporting_amount: Decimal | None
    reporting_currency: str
    quantity_basis: Decimal | None
    holding_interval: timedelta | None
    version: str
    provenance: str
    reason: str | None = None
    conversion_rate: Decimal | None = None
    conversion_source: str | None = None
    conversion_version: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.component) is not CostComponentKind
            or type(self.status) is not InputStatus
            or type(self.basis) is not CostBasis
        ):
            raise ValueError("component, status and basis must use declared enums")
        if not self.reporting_currency or not self.version or not self.provenance:
            raise ValueError(
                "component cost reporting currency, version and provenance are required"
            )
        _require_currency(self.reporting_currency, "component reporting currency")
        if self.native_currency is not None:
            _require_currency(self.native_currency, "component native currency")
        for value, field_name in (
            (self.native_amount, "component native amount"),
            (self.reporting_amount, "component reporting amount"),
        ):
            if value is not None:
                _require_nonnegative(value, field_name)
        if self.quantity_basis is not None:
            _require_nonnegative(self.quantity_basis, "component quantity basis")
        if self.status in {InputStatus.MISSING, InputStatus.UNSUPPORTED}:
            if self.native_amount is not None or self.native_currency is not None:
                raise ValueError("missing or unsupported component cannot carry native money")
            if self.reporting_amount is not None:
                raise ValueError("missing or unsupported component cannot carry an amount")
            if (
                self.conversion_rate is not None
                or self.conversion_source
                or self.conversion_version
            ):
                raise ValueError(
                    "missing or unsupported component cannot carry conversion evidence"
                )
            if not self.reason:
                raise ValueError("missing or unsupported component requires a reason")
        else:
            if (
                self.native_amount is None
                or self.native_currency is None
                or self.reporting_amount is None
                or self.conversion_rate is None
                or not self.conversion_source
                or not self.conversion_version
            ):
                raise ValueError(
                    "available component requires conversion-bound native and reporting amounts"
                )
            _require_positive(self.conversion_rate, "component conversion rate")
            if self.native_currency == self.reporting_currency and self.conversion_rate != Decimal(
                "1"
            ):
                raise ValueError("identity component conversion rate must equal one")
            if self.native_amount * self.conversion_rate != self.reporting_amount:
                raise ValueError("component conversion evidence does not reconcile amounts")
            if self.status is InputStatus.DOCUMENTED_ZERO and (
                self.native_amount != Decimal("0") or self.reporting_amount != Decimal("0")
            ):
                raise ValueError("documented-zero component must have zero amounts")
        if self.basis is CostBasis.PHYSICAL_DELTA:
            if self.quantity_basis is None or self.quantity_basis < 0:
                raise ValueError("physical-delta component requires non-negative quantity basis")
            if self.holding_interval is not None:
                raise ValueError("physical-delta component cannot carry a holding interval")
        elif self.basis is CostBasis.PHYSICAL_HOLDING:
            if self.holding_interval is None or self.holding_interval < timedelta(0):
                raise ValueError("physical-holding component requires an interval")
            if self.quantity_basis is not None:
                raise ValueError("physical-holding component cannot carry quantity basis")
        elif self.basis is CostBasis.INTERNAL_CROSS:
            if self.native_amount not in {None, Decimal("0")} or self.reporting_amount not in {
                None,
                Decimal("0"),
            }:
                raise ValueError("internal-cross component must be zero cost")

    @classmethod
    def supported(
        cls,
        *,
        component: CostComponentKind,
        basis: CostBasis,
        native_amount: Decimal,
        native_currency: str,
        reporting_amount: Decimal,
        reporting_currency: str,
        version: str,
        provenance: str,
        conversion_rate: Decimal,
        conversion_source: str,
        conversion_version: str,
        quantity_basis: Decimal | None = None,
        holding_interval: timedelta | None = None,
    ) -> ComponentCost:
        return cls(
            component,
            InputStatus.AVAILABLE,
            basis,
            native_amount,
            native_currency,
            reporting_amount,
            reporting_currency,
            quantity_basis,
            holding_interval,
            version,
            provenance,
            conversion_rate=conversion_rate,
            conversion_source=conversion_source,
            conversion_version=conversion_version,
        )

    @classmethod
    def documented_zero(
        cls,
        *,
        component: CostComponentKind,
        basis: CostBasis,
        currency: str,
        reporting_currency: str,
        version: str,
        provenance: str,
        conversion_rate: Decimal,
        conversion_source: str,
        conversion_version: str,
        quantity_basis: Decimal | None = None,
        holding_interval: timedelta | None = None,
    ) -> ComponentCost:
        return cls(
            component,
            InputStatus.DOCUMENTED_ZERO,
            basis,
            Decimal("0"),
            currency,
            Decimal("0"),
            reporting_currency,
            quantity_basis,
            holding_interval,
            version,
            provenance,
            conversion_rate=conversion_rate,
            conversion_source=conversion_source,
            conversion_version=conversion_version,
        )

    @classmethod
    def missing(
        cls,
        *,
        component: CostComponentKind,
        basis: CostBasis,
        reporting_currency: str,
        version: str,
        provenance: str,
        reason: str,
        quantity_basis: Decimal | None = None,
        holding_interval: timedelta | None = None,
    ) -> ComponentCost:
        return cls(
            component,
            InputStatus.MISSING,
            basis,
            None,
            None,
            None,
            reporting_currency,
            quantity_basis,
            holding_interval,
            version,
            provenance,
            reason,
        )

    @classmethod
    def unsupported(
        cls,
        *,
        component: CostComponentKind,
        basis: CostBasis,
        reporting_currency: str,
        version: str,
        provenance: str,
        reason: str,
        quantity_basis: Decimal | None = None,
        holding_interval: timedelta | None = None,
    ) -> ComponentCost:
        return cls(
            component,
            InputStatus.UNSUPPORTED,
            basis,
            None,
            None,
            None,
            reporting_currency,
            quantity_basis,
            holding_interval,
            version,
            provenance,
            reason,
        )


_COMPONENT_ORDER: Final[tuple[CostComponentKind, ...]] = (
    CostComponentKind.SPREAD,
    CostComponentKind.LATENCY_MOVEMENT,
    CostComponentKind.ADVERSE_SLIPPAGE,
    CostComponentKind.COMMISSION,
    CostComponentKind.FINANCING,
    CostComponentKind.IMPACT,
)


@dataclass(frozen=True, slots=True)
class ExpectedCostState:
    """Versioned expected cost state for one physical target transition."""

    decision_time: datetime
    current_quantity: Decimal
    target_quantity: Decimal
    holding_interval: timedelta
    reporting_currency: str
    components: tuple[ComponentCost, ...]
    version: str
    provenance: str
    internal_cross_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "expected cost decision_time")
        _require_finite(self.current_quantity, "expected cost current quantity")
        _require_finite(self.target_quantity, "expected cost target quantity")
        if self.holding_interval < timedelta(0):
            raise ValueError("expected cost holding interval cannot be negative")
        _require_currency(self.reporting_currency, "expected cost reporting currency")
        if not self.version or not self.provenance:
            raise ValueError("expected cost version and provenance are required")
        _require_nonnegative(self.internal_cross_quantity, "internal cross quantity")
        delta = abs(self.target_quantity - self.current_quantity)
        if tuple(component.component for component in self.components) != _COMPONENT_ORDER:
            raise ValueError("expected cost components must use canonical component order")
        for component in self.components:
            if component.reporting_currency != self.reporting_currency:
                raise ValueError("all expected cost components must use reporting currency")
            if component.component is CostComponentKind.FINANCING:
                if component.basis is not CostBasis.PHYSICAL_HOLDING:
                    raise ValueError("financing must be charged on physical holding")
                if component.holding_interval != self.holding_interval:
                    raise ValueError("financing must bind the physical holding interval")
            else:
                if component.basis is not CostBasis.PHYSICAL_DELTA:
                    raise ValueError("non-financing cost must bind the physical delta")
                assert component.quantity_basis is not None
                if component.quantity_basis != delta:
                    raise ValueError("transaction cost must bind the final physical delta")

    @property
    def physical_delta(self) -> Decimal:
        return self.target_quantity - self.current_quantity

    @property
    def complete(self) -> bool:
        return all(
            component.status in {InputStatus.AVAILABLE, InputStatus.DOCUMENTED_ZERO}
            for component in self.components
        )

    @property
    def expected_total_reporting(self) -> Decimal | None:
        if not self.complete:
            return None
        return sum(
            (component.reporting_amount or Decimal("0") for component in self.components),
            Decimal("0"),
        )

    def require_total_reporting(self) -> Decimal:
        total = self.expected_total_reporting
        if total is None:
            raise ValueError("expected cost is incomplete; refusing a silent zero")
        return total


@dataclass(frozen=True, slots=True)
class GrossForecast:
    """Gross forecast return, kept independent from physical costs."""

    expected_return: Decimal
    horizon: timedelta
    return_unit: str
    model_identity: str

    def __post_init__(self) -> None:
        _require_finite(self.expected_return, "gross forecast expected_return")
        if self.horizon <= timedelta(0):
            raise ValueError("gross forecast horizon must be positive")
        if not self.return_unit or not self.model_identity:
            raise ValueError("gross forecast unit and model identity are required")

    @property
    def gross_return(self) -> Decimal:
        return self.expected_return


@dataclass(frozen=True, slots=True)
class ExpectedNet:
    """Derived expected net contribution; the input forecast remains gross."""

    gross_forecast: GrossForecast
    gross_contribution: Decimal
    physical_notional: Decimal
    physical_notional_currency: str
    expected_cost: ExpectedCostState
    expected_net_contribution: Decimal
    expected_net_return: Decimal

    def __post_init__(self) -> None:
        _require_finite(self.gross_contribution, "gross contribution")
        _require_positive(self.physical_notional, "physical notional")
        _require_currency(self.physical_notional_currency, "physical notional currency")
        if self.gross_forecast.return_unit != COST_ADJUSTED_RETURN_UNIT:
            raise ValueError("gross forecast return unit must be reporting-return compatible")
        if self.expected_cost.reporting_currency != self.physical_notional_currency:
            raise ValueError("physical notional and expected cost currencies must match")
        expected_cost = self.expected_cost.expected_total_reporting
        if expected_cost is None:
            raise ValueError("cannot derive expected net from incomplete costs")
        if self.expected_net_contribution != self.gross_contribution - expected_cost:
            raise ValueError("expected net contribution must reconcile from gross and costs")
        expected_return = (
            self.gross_forecast.expected_return - expected_cost / self.physical_notional
        )
        if self.expected_net_return != expected_return:
            raise ValueError("expected net return must be recomputed from gross and costs")

    @classmethod
    def derive(
        cls,
        *,
        gross_forecast: GrossForecast,
        gross_contribution: Decimal,
        physical_notional: Decimal,
        physical_notional_currency: str,
        expected_cost: ExpectedCostState,
    ) -> ExpectedNet:
        total = expected_cost.require_total_reporting()
        return cls(
            gross_forecast,
            gross_contribution,
            physical_notional,
            physical_notional_currency,
            expected_cost,
            gross_contribution - total,
            gross_forecast.expected_return - total / physical_notional,
        )


@dataclass(frozen=True, slots=True)
class SolverPolicy:
    """Semantic selection of the exact convex solver boundary for R3.C."""

    policy_version: str
    python_version: str
    library: str
    library_version: str
    backend: str
    backend_version: str
    licence: str
    objective_scaling: Decimal
    absolute_tolerance: Decimal
    relative_tolerance: Decimal
    max_iterations: int
    variable_order: tuple[str, ...]
    accepted_statuses: tuple[str, ...]
    warm_start: bool = False

    def __post_init__(self) -> None:
        values = (
            self.policy_version,
            self.python_version,
            self.library,
            self.library_version,
            self.backend,
            self.backend_version,
            self.licence,
        )
        if any(not value for value in values):
            raise ValueError("solver policy identity and version fields are required")
        if self.python_version != "3.13":
            raise ValueError("R3 solver policy must target Python 3.13")
        if self.library.lower() != "cvxpy":
            raise ValueError("R3.A freezes CVXPY as the candidate convex modelling library")
        if self.objective_scaling <= 0:
            raise ValueError("solver objective scaling must be positive")
        if self.absolute_tolerance <= 0 or self.relative_tolerance <= 0:
            raise ValueError("solver tolerances must be positive")
        if self.max_iterations <= 0 or not self.variable_order or not self.accepted_statuses:
            raise ValueError("solver policy requires bounds, variable order and statuses")
        if self.variable_order[0] != "physical_target":
            raise ValueError("solver variable order must begin with physical_target")
        if self.accepted_statuses != ("optimal",):
            raise ValueError("solver policy accepts only the optimal status")
        if self.warm_start:
            raise ValueError("R3 deterministic policy requires warm-start disabled")

    @property
    def semantic_identity(self) -> str:
        payload = {
            "contract": SOLVER_POLICY_CONTRACT,
            "policy_version": self.policy_version,
            "python_version": self.python_version,
            "library": self.library,
            "library_version": self.library_version,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "licence": self.licence,
            "objective_scaling": str(self.objective_scaling),
            "absolute_tolerance": str(self.absolute_tolerance),
            "relative_tolerance": str(self.relative_tolerance),
            "max_iterations": self.max_iterations,
            "variable_order": list(self.variable_order),
            "accepted_statuses": list(self.accepted_statuses),
            "warm_start": self.warm_start,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


DEFAULT_SOLVER_POLICY: Final[SolverPolicy] = SolverPolicy(
    policy_version="r3-convex-15m-v1",
    python_version="3.13",
    library="cvxpy",
    library_version="1.7.3",
    backend="CLARABEL",
    backend_version="0.11.1",
    licence="Apache-2.0",
    objective_scaling=Decimal("1"),
    absolute_tolerance=Decimal("1e-8"),
    relative_tolerance=Decimal("1e-8"),
    max_iterations=1000,
    variable_order=("physical_target",),
    accepted_statuses=("optimal",),
)


def derive_expected_net(
    *,
    gross_forecast: GrossForecast,
    gross_contribution: Decimal,
    physical_notional: Decimal,
    physical_notional_currency: str,
    expected_cost: ExpectedCostState,
) -> ExpectedNet:
    """Recompute expected net fields from gross forecast and complete costs."""

    return ExpectedNet.derive(
        gross_forecast=gross_forecast,
        gross_contribution=gross_contribution,
        physical_notional=physical_notional,
        physical_notional_currency=physical_notional_currency,
        expected_cost=expected_cost,
    )


def _require_currency(value: str, field_name: str) -> None:
    if len(value) != 3 or not value.isalpha() or value != value.upper():
        raise ValueError(f"{field_name} must be an ISO-like upper-case currency code")


def _require_finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite Decimal")


def _require_positive(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_nonnegative(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
