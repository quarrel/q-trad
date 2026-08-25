"""Deterministic, provider-neutral discrete target contracts for R3.D.

The continuous R3.C kernel intentionally stops before product quantity
rounding.  This module contains only the immutable values needed to describe
the next boundary.  The application seam performs rounding and repair; these
values make its output explicit, replayable and easy to validate independently.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast

from qtrad.domain.economics import CostComponentKind, ExpectedCostState
from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.portfolio import NettingResult, SleeveKey

ROUNDING_CONTRACT: Final = "qtrad-r3-rounding-repair-v1"
REASON_CODE_VERSION: Final = "qtrad-r3-reason-codes-v1"


class RoundingDisposition(StrEnum):
    """Stable outcome classes at the discrete target boundary."""

    ACCEPTED = "ACCEPTED"
    PROJECTED = "PROJECTED"
    BLOCKED = "BLOCKED"


class RoundingReasonCode(StrEnum):
    """Versioned, ordered reasons introduced by R3.D."""

    INPUT_ECONOMICS_MISSING = "INPUT_ECONOMICS_MISSING"
    INPUT_FX_MISSING = "INPUT_FX_MISSING"
    INPUT_COST_INVALID = "INPUT_COST_INVALID"
    INPUT_RISK_INVALID = "INPUT_RISK_INVALID"
    ZERO_FORECAST_NEW_EXPOSURE_BLOCKED = "ZERO_FORECAST_NEW_EXPOSURE_BLOCKED"
    ASSET_PAPER_INELIGIBLE = "ASSET_PAPER_INELIGIBLE"
    SOLVER_ERROR = "SOLVER_ERROR"
    SOLVER_NON_OPTIMAL = "SOLVER_NON_OPTIMAL"
    SOLVER_INFEASIBLE = "SOLVER_INFEASIBLE"
    SOLVER_RESULT_INVALID = "SOLVER_RESULT_INVALID"
    QUANTITY_ROUNDED = "QUANTITY_ROUNDED"
    MINIMUM_QUANTITY_NOT_MET = "MINIMUM_QUANTITY_NOT_MET"
    ASSET_CAP_REPAIR = "ASSET_CAP_REPAIR"
    GROSS_CAP_REPAIR = "GROSS_CAP_REPAIR"
    NET_CAP_REPAIR = "NET_CAP_REPAIR"
    CONCENTRATION_CAP_REPAIR = "CONCENTRATION_CAP_REPAIR"
    GROUP_CAP_REPAIR = "GROUP_CAP_REPAIR"
    CURRENCY_CAP_REPAIR = "CURRENCY_CAP_REPAIR"
    PORTFOLIO_RISK_REPAIR = "PORTFOLIO_RISK_REPAIR"
    CURRENT_POSITION_PROJECTED = "CURRENT_POSITION_PROJECTED"
    NEW_ALPHA_EXPOSURE_BLOCKED = "NEW_ALPHA_EXPOSURE_BLOCKED"
    ATTRIBUTION_RESIDUAL_REPAIRED = "ATTRIBUTION_RESIDUAL_REPAIRED"
    DECISION_BLOCKED = "DECISION_BLOCKED"


REASON_CODE_ORDER: Final[tuple[str, ...]] = tuple(item.value for item in RoundingReasonCode)
_REASON_PRIORITY: Final = {reason: index for index, reason in enumerate(REASON_CODE_ORDER)}


def order_reason_codes(codes: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate and sort reason codes by the frozen R3.D priority."""

    values = tuple(str(code) for code in codes)
    unique = tuple(dict.fromkeys(values))
    return tuple(
        sorted(
            unique, key=lambda value: (_REASON_PRIORITY.get(value, len(REASON_CODE_ORDER)), value)
        )
    )


def _canonical(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            str(key): _canonical(item)
            for key, item in sorted(mapping.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (tuple, list)):
        items = cast(tuple[object, ...] | list[object], value)
        return [_canonical(item) for item in items]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported rounding identity value: {type(value).__name__}")


def _identity(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _cost_state_payload(state: ExpectedCostState) -> object:
    return {
        "decision_time": state.decision_time.isoformat(),
        "current_quantity": state.current_quantity,
        "target_quantity": state.target_quantity,
        "holding_interval": state.holding_interval.total_seconds(),
        "reporting_currency": state.reporting_currency,
        "version": state.version,
        "provenance": state.provenance,
        "internal_cross_quantity": state.internal_cross_quantity,
        "components": tuple(
            (
                component.component,
                component.status,
                component.basis,
                component.native_amount,
                component.native_currency,
                component.reporting_amount,
                component.reporting_currency,
                component.quantity_basis,
                component.holding_interval.total_seconds()
                if component.holding_interval is not None
                else None,
                component.version,
                component.provenance,
                component.reason,
                component.conversion_rate,
                component.conversion_source,
                component.conversion_version,
            )
            for component in state.components
        ),
    }


def cost_states_identity(costs: Mapping[str, ExpectedCostState]) -> str:
    return _identity(
        tuple(
            (asset, _cost_state_payload(state))
            for asset, state in sorted(costs.items(), key=lambda item: item[0])
        )
    )


def _finite_decimal(value: object, name: str) -> Decimal:
    if type(value) is Decimal and value.is_finite():
        return value
    raise ValueError(f"{name} must be a finite Decimal")


@dataclass(frozen=True, slots=True)
class RoundingPolicy:
    """Frozen deterministic policy for one rounding/repair pass."""

    version: str = ROUNDING_CONTRACT
    reason_code_version: str = REASON_CODE_VERSION
    max_repair_steps: int = 100_000

    def __post_init__(self) -> None:
        if not self.version or not self.reason_code_version:
            raise ValueError("rounding policy versions are required")
        if self.max_repair_steps <= 0:
            raise ValueError("rounding policy max_repair_steps must be positive")

    @property
    def semantic_identity(self) -> str:
        return _identity(
            {
                "contract": ROUNDING_CONTRACT,
                "version": self.version,
                "reason_code_version": self.reason_code_version,
                "max_repair_steps": self.max_repair_steps,
                "reason_code_order": REASON_CODE_ORDER,
            }
        )


@dataclass(frozen=True, slots=True)
class RepairedSleeveAttribution:
    """Final sleeve attribution after physical rounding and repair."""

    key: SleeveKey
    requested_delta: Decimal
    internal_cross_quantity: Decimal
    external_delta_share: Decimal
    reason_codes: tuple[str, ...] = ()
    repair_delta: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _finite_decimal(self.requested_delta, "attribution requested delta")
        _finite_decimal(self.internal_cross_quantity, "attribution internal cross quantity")
        _finite_decimal(self.external_delta_share, "attribution external delta share")
        _finite_decimal(self.repair_delta, "attribution repair delta")
        if self.internal_cross_quantity < 0:
            raise ValueError("attribution internal cross quantity must be non-negative")
        original_external = self.external_delta_share - self.repair_delta
        if abs(original_external) + self.internal_cross_quantity != abs(self.requested_delta):
            raise ValueError("repaired sleeve attribution does not reconcile requested delta")
        object.__setattr__(self, "reason_codes", order_reason_codes(self.reason_codes))
        if any(code not in REASON_CODE_ORDER for code in self.reason_codes):
            raise ValueError("repaired sleeve attribution reason code is unknown")


@dataclass(frozen=True, slots=True)
class RoundedTarget:
    """Immutable final physical target and independently reconciled costs."""

    source_class: MarketDataSourceClass
    evidence_purpose: EvidencePurpose
    asset_order: tuple[str, ...]
    current_position: tuple[Decimal, ...]
    continuous_target: tuple[Decimal, ...]
    target_position: tuple[Decimal, ...]
    physical_delta: tuple[Decimal, ...]
    disposition: RoundingDisposition
    reason_codes: tuple[str, ...]
    expected_costs: Mapping[str, ExpectedCostState]
    expected_cost_reporting: Decimal
    expected_financing_reporting: Decimal
    netting: NettingResult
    attributions: tuple[RepairedSleeveAttribution, ...]
    policy_identity: str
    decision_input_identity: str
    continuous_target_identity: str
    cost_state_identity: str = ""
    attribution_residual: Decimal = Decimal("0")
    horizon_state_identities: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.source_class) is not MarketDataSourceClass
            or type(self.evidence_purpose) is not EvidencePurpose
        ):
            raise ValueError("rounded target source and evidence purpose must use declared enums")
        object.__setattr__(self, "asset_order", tuple(self.asset_order))
        for values, label in (
            (self.current_position, "current position"),
            (self.continuous_target, "continuous target"),
            (self.target_position, "target position"),
            (self.physical_delta, "physical delta"),
        ):
            if type(values) is not tuple:
                raise ValueError("rounded target vectors must be tuples")
            for value in values:
                _finite_decimal(value, label)
        if (
            not self.asset_order
            or tuple(sorted(self.asset_order)) != self.asset_order
            or len(set(self.asset_order)) != len(self.asset_order)
        ):
            raise ValueError("rounded target asset order must be canonical and unique")
        n = len(self.asset_order)
        blocked_empty = (
            self.disposition is RoundingDisposition.BLOCKED
            and not self.target_position
            and not self.physical_delta
        )
        if not blocked_empty and any(
            len(values) != n
            for values in (
                self.current_position,
                self.continuous_target,
                self.target_position,
                self.physical_delta,
            )
        ):
            raise ValueError("rounded target vectors must match asset order")
        if not blocked_empty and any(
            target - current != delta
            for current, target, delta in zip(
                self.current_position, self.target_position, self.physical_delta, strict=True
            )
        ):
            raise ValueError("rounded target physical delta does not reconcile")
        if self.netting.asset_order != self.asset_order:
            raise ValueError("rounded target netting does not match asset order")
        if not blocked_empty and not self.netting.external_deltas:
            raise ValueError("rounded target netting has no external delta vector")
        if type(self.attributions) is not tuple:
            raise ValueError("rounded target attributions must be a tuple")
        if blocked_empty and self.attributions:
            raise ValueError("blocked rounded target cannot carry attributions")
        if not blocked_empty:
            ordered_attributions = tuple(
                sorted(self.attributions, key=lambda item: item.key.canonical_tuple)
            )
            if ordered_attributions != self.attributions:
                raise ValueError("rounded target attributions must be canonical")
            if len({item.key for item in self.attributions}) != len(self.attributions):
                raise ValueError("rounded target attributions must be unique")
            parent_sleeves = {item.key: item for item in self.netting.sleeves}
            for asset_index, asset in enumerate(self.asset_order):
                asset_attributions = tuple(
                    item for item in self.attributions if item.key.asset_id == asset
                )
                if (
                    sum((item.external_delta_share for item in asset_attributions), Decimal("0"))
                    != self.physical_delta[asset_index]
                ):
                    raise ValueError(
                        "rounded target sleeve attribution does not reconcile physical delta"
                    )
                netted_asset = self.netting.assets[asset_index]
                if (
                    sum((item.requested_delta for item in asset_attributions), Decimal("0"))
                    != netted_asset.requested_delta
                ):
                    raise ValueError("rounded target sleeve requests do not reconcile")
                if sum(
                    (item.internal_cross_quantity for item in asset_attributions), Decimal("0")
                ) != netted_asset.internal_cross_quantity * Decimal("2"):
                    raise ValueError("rounded target internal cross does not reconcile")
                for item in asset_attributions:
                    parent = parent_sleeves.get(item.key)
                    if parent is None:
                        raise ValueError("rounded target attribution key is not in parent netting")
                    if (
                        item.requested_delta != parent.requested_delta
                        or item.internal_cross_quantity != parent.internal_cross_quantity
                        or item.external_delta_share - item.repair_delta
                        != parent.external_delta_share
                    ):
                        raise ValueError(
                            "rounded target attribution does not preserve parent netting"
                        )
        if type(self.disposition) is not RoundingDisposition:
            raise ValueError("rounded target disposition is invalid")
        object.__setattr__(self, "reason_codes", order_reason_codes(self.reason_codes))
        if any(code not in REASON_CODE_ORDER for code in self.reason_codes):
            raise ValueError("rounded target reason code is unknown")
        object.__setattr__(self, "expected_costs", MappingProxyType(dict(self.expected_costs)))
        _finite_decimal(self.expected_cost_reporting, "rounded target expected cost")
        _finite_decimal(self.expected_financing_reporting, "rounded target expected financing")
        _finite_decimal(self.attribution_residual, "rounded target attribution residual")
        horizon_states = tuple(self.horizon_state_identities)
        if len({semantic for semantic, _ in horizon_states}) != len(horizon_states):
            raise ValueError("rounded target horizon state identities must be unique")
        for semantic, closure in horizon_states:
            for value, label in ((semantic, "semantic"), (closure, "closure")):
                if (
                    type(value) is not str
                    or len(value) != 64
                    or any(char not in "0123456789abcdef" for char in value)
                ):
                    raise ValueError(f"rounded target horizon {label} identity is invalid")
        object.__setattr__(self, "horizon_state_identities", horizon_states)
        if self.attribution_residual < 0:
            raise ValueError("rounded target attribution residual must be non-negative")
        if (
            self.disposition is not RoundingDisposition.BLOCKED
            and self.attribution_residual != Decimal("0")
        ):
            raise ValueError("accepted rounded target attribution residual must be zero")
        for identity, label in (
            (self.policy_identity, "policy identity"),
            (self.decision_input_identity, "decision input identity"),
            (self.continuous_target_identity, "continuous target identity"),
        ):
            if (
                type(identity) is not str
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise ValueError(f"rounded target {label} must be a SHA-256 hex digest")
        if self.disposition is RoundingDisposition.BLOCKED:
            if self.target_position or self.physical_delta or self.expected_costs:
                raise ValueError("blocked rounded target cannot carry partial output")
            if self.expected_cost_reporting != Decimal(
                "0"
            ) or self.expected_financing_reporting != Decimal("0"):
                raise ValueError("blocked rounded target costs must be zero")
            if not self.reason_codes:
                raise ValueError("blocked rounded target requires reason codes")
        else:
            if set(self.expected_costs) != set(self.asset_order):
                raise ValueError("accepted rounded target costs must cover every asset")
            non_financing = Decimal("0")
            financing = Decimal("0")
            for asset in self.asset_order:
                state = self.expected_costs[asset]
                if not state.complete:
                    raise ValueError("rounded target requires complete costs")
                if (
                    state.current_quantity != self.current_position[self.asset_order.index(asset)]
                    or state.target_quantity != self.target_position[self.asset_order.index(asset)]
                    or state.internal_cross_quantity
                    != self.netting.assets[self.asset_order.index(asset)].internal_cross_quantity
                ):
                    raise ValueError("rounded target cost state binding mismatch")
                total = state.require_total_reporting()
                finance = next(
                    component
                    for component in state.components
                    if component.component is CostComponentKind.FINANCING
                )
                if finance.reporting_amount is None:
                    raise ValueError("rounded target financing amount is required")
                non_financing += total - finance.reporting_amount
                financing += finance.reporting_amount
            if (
                non_financing != self.expected_cost_reporting
                or financing != self.expected_financing_reporting
            ):
                raise ValueError("rounded target costs do not reconcile")
            if self.cost_state_identity != cost_states_identity(self.expected_costs):
                raise ValueError("rounded target cost state identity mismatch")

    @property
    def canonical_bytes(self) -> bytes:
        payload: dict[str, object] = {
            "contract": ROUNDING_CONTRACT,
            "source_class": self.source_class,
            "evidence_purpose": self.evidence_purpose,
            "asset_order": self.asset_order,
            "current_position": self.current_position,
            "continuous_target": self.continuous_target,
            "target_position": self.target_position,
            "physical_delta": self.physical_delta,
            "disposition": self.disposition,
            "reason_codes": self.reason_codes,
            "expected_costs": tuple(
                (asset, self.expected_costs[asset].require_total_reporting())
                for asset in self.asset_order
                if asset in self.expected_costs
            ),
            "expected_cost_reporting": self.expected_cost_reporting,
            "expected_financing_reporting": self.expected_financing_reporting,
            "netting": self.netting.semantic_identity,
            "attributions": tuple(
                (
                    item.key.as_json(),
                    item.requested_delta,
                    item.internal_cross_quantity,
                    item.external_delta_share,
                    item.repair_delta,
                    item.reason_codes,
                )
                for item in self.attributions
            ),
            "policy_identity": self.policy_identity,
            "decision_input_identity": self.decision_input_identity,
            "continuous_target_identity": self.continuous_target_identity,
            "cost_state_identity": self.cost_state_identity,
            "attribution_residual": self.attribution_residual,
        }
        if self.horizon_state_identities:
            payload["horizon_state_identities"] = self.horizon_state_identities
        return json.dumps(
            _canonical(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()

    @property
    def semantic_identity(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def closure_identity(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "semantic_identity": self.semantic_identity,
                    "closure": self.canonical_bytes.hex(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @property
    def final_target(self) -> tuple[Decimal, ...]:
        return self.target_position


RoundingResult = RoundedTarget
DiscreteTarget = RoundedTarget
