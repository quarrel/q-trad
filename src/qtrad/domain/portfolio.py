"""Provider-neutral one-horizon portfolio and target contracts.

R3.C deliberately stops at continuous quantities.  Product rounding, repair,
paper fills and durable receipts belong to later boundaries.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from typing import Final, cast

from qtrad.domain.economics import (
    ExpectedCostState,
    GrossForecast,
    ProductEconomics,
    SolverPolicy,
)
from qtrad.domain.risk import RiskState
from qtrad.domain.time import require_utc

PORTFOLIO_CONTRACT: Final = "qtrad-r3-one-horizon-portfolio-v1"
TARGET_CONTRACT: Final = "qtrad-r3-continuous-target-v1"
ONE_HORIZON: Final = timedelta(minutes=15)


class DecisionDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"


class SolverResultStatus(StrEnum):
    OPTIMAL = "optimal"
    NON_OPTIMAL = "non_optimal"
    INACCURATE = "inaccurate"
    INFEASIBLE = "infeasible"
    ERROR = "error"


def _decimal(value: Decimal, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


def _nonnegative(value: Decimal, name: str) -> Decimal:
    result = _decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _utc_json(value: datetime) -> str:
    require_utc(value, "canonical identity datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _utc_json(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(mapping.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        sequence = cast(tuple[object, ...] | list[object], value)
        return [_canonical_value(item) for item in sequence]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Encode identity-bearing values without representation-dependent whitespace."""

    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _identity(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class SleeveKey:
    """Canonical identity of one source/configuration/asset/horizon sleeve."""

    source_class: str
    experiment_id: str
    configuration_id: str
    asset_id: str
    horizon: timedelta = ONE_HORIZON

    def __post_init__(self) -> None:
        if not all(
            type(value) is str and value
            for value in (
                self.source_class,
                self.experiment_id,
                self.configuration_id,
                self.asset_id,
            )
        ):
            raise ValueError("sleeve identity fields are required")
        if self.horizon != ONE_HORIZON:
            raise ValueError("R3.C supports exactly the 15-minute horizon")

    @property
    def canonical_tuple(self) -> tuple[str, str, str, str, int]:
        return (
            self.source_class,
            self.experiment_id,
            self.configuration_id,
            self.asset_id,
            int(self.horizon.total_seconds()),
        )

    def as_json(self) -> dict[str, object]:
        return {
            "source_class": self.source_class,
            "experiment_id": self.experiment_id,
            "configuration_id": self.configuration_id,
            "asset_id": self.asset_id,
            "horizon_seconds": int(self.horizon.total_seconds()),
        }


@dataclass(frozen=True, slots=True)
class VirtualPosition:
    key: SleeveKey
    quantity: Decimal
    state_identity: str = ""

    def __post_init__(self) -> None:
        _decimal(self.quantity, "virtual quantity")
        if self.state_identity and len(self.state_identity) != 64:
            raise ValueError("virtual state identity must be a SHA-256 hex digest")

    @property
    def semantic_identity(self) -> str:
        return _identity(
            {
                "contract": PORTFOLIO_CONTRACT,
                "key": self.key.as_json(),
                "quantity": self.quantity,
            }
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "contract": PORTFOLIO_CONTRACT,
                "key": self.key.as_json(),
                "quantity": self.quantity,
                "state_identity": self.state_identity,
            }
        )


@dataclass(frozen=True, slots=True)
class HorizonIntent:
    """One immutable virtual sleeve request before global physical netting."""

    key: SleeveKey
    prior_quantity: Decimal
    requested_quantity: Decimal
    gross_forecast: GrossForecast
    gross_sleeve_value: Decimal
    decision_time: datetime
    expiry_time: datetime
    model_identity: str
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _decimal(self.prior_quantity, "intent prior quantity")
        _decimal(self.requested_quantity, "intent requested quantity")
        _nonnegative(self.gross_sleeve_value, "gross sleeve value")
        require_utc(self.decision_time, "intent decision time")
        require_utc(self.expiry_time, "intent expiry time")
        if self.expiry_time <= self.decision_time:
            raise ValueError("intent expiry must follow decision time")
        if self.gross_forecast.horizon != ONE_HORIZON:
            raise ValueError("intent forecast must use the 15-minute horizon")
        if self.gross_forecast.model_identity != self.model_identity:
            raise ValueError("intent model identity must match gross forecast")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def requested_delta(self) -> Decimal:
        return self.requested_quantity - self.prior_quantity

    @property
    def canonical_tuple(self) -> tuple[str, str, str, str, int]:
        return self.key.canonical_tuple

    def as_json(self) -> dict[str, object]:
        return {
            "key": self.key.as_json(),
            "prior_quantity": self.prior_quantity,
            "requested_quantity": self.requested_quantity,
            "gross_forecast": {
                "expected_return": self.gross_forecast.expected_return,
                "horizon_seconds": int(self.gross_forecast.horizon.total_seconds()),
                "return_unit": self.gross_forecast.return_unit,
                "model_identity": self.gross_forecast.model_identity,
            },
            "gross_sleeve_value": self.gross_sleeve_value,
            "decision_time": self.decision_time,
            "expiry_time": self.expiry_time,
            "model_identity": self.model_identity,
            "reason_codes": self.reason_codes,
        }


def construct_horizon_intent(
    *,
    position: VirtualPosition,
    gross_forecast: GrossForecast,
    requested_quantity: Decimal,
    gross_sleeve_value: Decimal,
    decision_time: datetime,
    expiry_time: datetime,
    reason_codes: Sequence[str] = (),
) -> HorizonIntent:
    """Construct an intent while enforcing the zero-forecast no-new-direction rule.

    A zero forecast may retain or reduce a valid position, but a proposed opening,
    increase or reversal is replaced by the existing quantity.
    """

    _decimal(requested_quantity, "intent requested quantity")
    forecast = gross_forecast.expected_return
    reasons = list(reason_codes)
    if forecast == 0:
        prior = position.quantity
        proposed = requested_quantity
        increases = abs(proposed) > abs(prior)
        reverses = prior != 0 and proposed != 0 and (prior > 0) != (proposed > 0)
        opens = prior == 0 and proposed != 0
        if increases or reverses or opens:
            requested_quantity = prior
            if "ZERO_FORECAST_NEW_EXPOSURE_BLOCKED" not in reasons:
                reasons.append("ZERO_FORECAST_NEW_EXPOSURE_BLOCKED")
    return HorizonIntent(
        key=position.key,
        prior_quantity=position.quantity,
        requested_quantity=requested_quantity,
        gross_forecast=gross_forecast,
        gross_sleeve_value=gross_sleeve_value,
        decision_time=decision_time,
        expiry_time=expiry_time,
        model_identity=gross_forecast.model_identity,
        reason_codes=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class SleeveAttribution:
    key: SleeveKey
    requested_delta: Decimal
    internal_cross_quantity: Decimal
    external_delta_share: Decimal

    def __post_init__(self) -> None:
        _decimal(self.requested_delta, "attribution requested delta")
        _nonnegative(self.internal_cross_quantity, "attribution internal cross quantity")
        _decimal(self.external_delta_share, "attribution external delta share")
        if abs(self.external_delta_share) + self.internal_cross_quantity != abs(
            self.requested_delta
        ):
            raise ValueError("sleeve attribution does not reconcile requested delta")


@dataclass(frozen=True, slots=True)
class AssetNetting:
    asset_id: str
    requested_delta: Decimal
    external_delta: Decimal
    internal_cross_quantity: Decimal
    attributions: tuple[SleeveAttribution, ...]

    def __post_init__(self) -> None:
        _decimal(self.requested_delta, "asset requested delta")
        _decimal(self.external_delta, "asset external delta")
        _nonnegative(self.internal_cross_quantity, "asset internal cross quantity")
        if self.external_delta != self.requested_delta:
            raise ValueError("internal crosses must not change physical delta")
        if (
            sum((item.external_delta_share for item in self.attributions), Decimal("0"))
            != self.external_delta
        ):
            raise ValueError("asset external attribution does not reconcile")


@dataclass(frozen=True, slots=True)
class NettingResult:
    assets: tuple[AssetNetting, ...]
    sleeves: tuple[SleeveAttribution, ...]

    @property
    def asset_order(self) -> tuple[str, ...]:
        return tuple(item.asset_id for item in self.assets)

    @property
    def external_deltas(self) -> tuple[Decimal, ...]:
        return tuple(item.external_delta for item in self.assets)

    @property
    def internal_cross_quantity(self) -> Decimal:
        return sum((item.internal_cross_quantity for item in self.assets), Decimal("0"))

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "contract": PORTFOLIO_CONTRACT,
                "assets": [
                    {
                        "asset_id": item.asset_id,
                        "requested_delta": item.requested_delta,
                        "external_delta": item.external_delta,
                        "internal_cross_quantity": item.internal_cross_quantity,
                        "attributions": [
                            {
                                "key": attribution.key.as_json(),
                                "requested_delta": attribution.requested_delta,
                                "internal_cross_quantity": attribution.internal_cross_quantity,
                                "external_delta_share": attribution.external_delta_share,
                            }
                            for attribution in item.attributions
                        ],
                    }
                    for item in self.assets
                ],
            }
        )

    @property
    def semantic_identity(self) -> str:
        return _identity(self.canonical_bytes)


def match_internal_opposing_changes(intents: Sequence[HorizonIntent]) -> NettingResult:
    """Match opposing sleeve changes in canonical sleeve order.

    Internal matches are quantity-only: they never alter the resulting physical
    asset delta and never attract external transaction cost.
    """

    ordered = sorted(intents, key=lambda item: item.canonical_tuple)
    if len({item.key for item in ordered}) != len(ordered):
        raise ValueError("duplicate sleeve intent is not deterministic")
    by_asset: dict[str, list[HorizonIntent]] = defaultdict(list)
    for intent in ordered:
        by_asset[intent.key.asset_id].append(intent)
    assets: list[AssetNetting] = []
    all_attributions: list[SleeveAttribution] = []
    for asset_id in sorted(by_asset):
        sleeves = by_asset[asset_id]
        deltas = {intent.key: intent.requested_delta for intent in sleeves}
        crosses = {intent.key: Decimal("0") for intent in sleeves}
        buyers = [intent for intent in sleeves if deltas[intent.key] > 0]
        sellers = [intent for intent in sleeves if deltas[intent.key] < 0]
        buyer_index = seller_index = 0
        while buyer_index < len(buyers) and seller_index < len(sellers):
            buyer = buyers[buyer_index]
            seller = sellers[seller_index]
            amount = min(deltas[buyer.key], -deltas[seller.key])
            crosses[buyer.key] += amount
            crosses[seller.key] += amount
            deltas[buyer.key] -= amount
            deltas[seller.key] += amount
            if deltas[buyer.key] == 0:
                buyer_index += 1
            if deltas[seller.key] == 0:
                seller_index += 1
        attributions = tuple(
            SleeveAttribution(
                key=intent.key,
                requested_delta=intent.requested_delta,
                internal_cross_quantity=crosses[intent.key],
                external_delta_share=(
                    (Decimal("1") if intent.requested_delta >= 0 else Decimal("-1"))
                    * (abs(intent.requested_delta) - crosses[intent.key])
                ),
            )
            for intent in sleeves
        )
        requested_delta = sum((intent.requested_delta for intent in sleeves), Decimal("0"))
        external_delta = sum((item.external_delta_share for item in attributions), Decimal("0"))
        internal_cross = sum(
            (item.internal_cross_quantity for item in attributions), Decimal("0")
        ) / Decimal("2")
        assets.append(
            AssetNetting(
                asset_id=asset_id,
                requested_delta=requested_delta,
                external_delta=external_delta,
                internal_cross_quantity=internal_cross,
                attributions=attributions,
            )
        )
        all_attributions.extend(attributions)
    return NettingResult(tuple(assets), tuple(all_attributions))


@dataclass(frozen=True, slots=True)
class VirtualPositionTransition:
    """Create-only-friendly transition record used by deterministic replay."""

    key: SleeveKey
    prior_quantity: Decimal
    next_quantity: Decimal
    gross_forecast: Decimal
    decision_time: datetime
    expiry_time: datetime
    model_identity: str
    risk_policy_identity: str
    cost_policy_identity: str
    accepted_physical_quantity: Decimal = Decimal("0")
    external_delta_share: Decimal = Decimal("0")
    internal_cross_quantity: Decimal = Decimal("0")
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.prior_quantity, "transition prior quantity"),
            (self.next_quantity, "transition next quantity"),
            (self.gross_forecast, "transition gross forecast"),
            (self.accepted_physical_quantity, "transition accepted physical quantity"),
            (self.external_delta_share, "transition external delta share"),
        ):
            _decimal(value, name)
        _nonnegative(self.internal_cross_quantity, "transition internal cross quantity")
        require_utc(self.decision_time, "transition decision time")
        require_utc(self.expiry_time, "transition expiry time")
        if self.expiry_time <= self.decision_time:
            raise ValueError("transition expiry must follow decision time")
        if (
            not self.model_identity
            or not self.risk_policy_identity
            or not self.cost_policy_identity
        ):
            raise ValueError("transition policy and model identities are required")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def requested_delta(self) -> Decimal:
        return self.next_quantity - self.prior_quantity

    @property
    def canonical_tuple(self) -> tuple[object, ...]:
        return (*self.key.canonical_tuple, self.decision_time)

    def as_json(self) -> dict[str, object]:
        return {
            "key": self.key.as_json(),
            "prior_quantity": self.prior_quantity,
            "next_quantity": self.next_quantity,
            "gross_forecast": self.gross_forecast,
            "decision_time": self.decision_time,
            "expiry_time": self.expiry_time,
            "model_identity": self.model_identity,
            "risk_policy_identity": self.risk_policy_identity,
            "cost_policy_identity": self.cost_policy_identity,
            "accepted_physical_quantity": self.accepted_physical_quantity,
            "external_delta_share": self.external_delta_share,
            "internal_cross_quantity": self.internal_cross_quantity,
            "reason_codes": self.reason_codes,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes({"contract": PORTFOLIO_CONTRACT, "transition": self.as_json()})

    @property
    def semantic_identity(self) -> str:
        return _identity({"contract": PORTFOLIO_CONTRACT, "transition": self.as_json()})


@dataclass(frozen=True, slots=True)
class VirtualReplay:
    positions: tuple[VirtualPosition, ...]
    transition_count: int

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "contract": PORTFOLIO_CONTRACT,
                "transition_count": self.transition_count,
                "positions": [
                    {
                        "key": position.key.as_json(),
                        "quantity": position.quantity,
                        "state_identity": position.state_identity,
                    }
                    for position in self.positions
                ],
            }
        )

    @property
    def semantic_identity(self) -> str:
        return _identity(self.canonical_bytes)


def replay_virtual_transitions(
    initial_positions: Mapping[SleeveKey, VirtualPosition | Decimal],
    transitions: Sequence[VirtualPositionTransition],
) -> VirtualReplay:
    current: dict[SleeveKey, VirtualPosition] = {}
    for key, value in initial_positions.items():
        position = value if isinstance(value, VirtualPosition) else VirtualPosition(key, value)
        if position.key != key:
            raise ValueError("initial virtual position key mismatch")
        current[key] = position
    ordered = sorted(transitions, key=lambda item: item.canonical_tuple)
    seen: set[tuple[SleeveKey, datetime]] = set()
    for transition in ordered:
        marker = (transition.key, transition.decision_time)
        if marker in seen:
            raise ValueError("duplicate sleeve transition is not deterministic")
        seen.add(marker)
    for transition in ordered:
        previous = current.get(transition.key)
        previous_quantity = previous.quantity if previous is not None else Decimal("0")
        if previous_quantity != transition.prior_quantity:
            raise ValueError("virtual transition prior quantity does not match replay state")
        current[transition.key] = VirtualPosition(
            transition.key,
            transition.next_quantity,
            transition.semantic_identity,
        )
    return VirtualReplay(
        positions=tuple(
            current[key] for key in sorted(current, key=lambda item: item.canonical_tuple)
        ),
        transition_count=len(ordered),
    )


@dataclass(frozen=True, slots=True)
class ContinuousTargetInputs:
    """Validated application boundary for one continuous physical target."""

    asset_order: tuple[str, ...]
    current_position: tuple[Decimal, ...]
    requested_target: tuple[Decimal, ...]
    alpha_return: tuple[Decimal, ...]
    gross_sleeve_value: Decimal
    decision_time: datetime
    economics: Mapping[str, ProductEconomics]
    expected_costs: Mapping[str, ExpectedCostState]
    risk: RiskState
    solver_policy: SolverPolicy

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.asset_order))
        if not ordered or len(set(ordered)) != len(ordered) or ordered != self.asset_order:
            raise ValueError("target asset order must be non-empty, unique and canonical")
        n = len(ordered)
        if any(
            len(values) != n
            for values in (self.current_position, self.requested_target, self.alpha_return)
        ):
            raise ValueError("target vectors must match asset order")
        for values, label in (
            (self.current_position, "current position"),
            (self.requested_target, "requested target"),
            (self.alpha_return, "alpha return"),
        ):
            for value in values:
                _decimal(value, label)
        _nonnegative(self.gross_sleeve_value, "gross sleeve value")
        require_utc(self.decision_time, "target decision time")
        if self.risk.asset_order != self.asset_order:
            raise ValueError("target risk state order does not match target assets")
        if set(self.economics) != set(self.asset_order):
            raise ValueError("target economics keys do not match asset order")
        if set(self.expected_costs) != set(self.asset_order):
            raise ValueError("target expected-cost keys do not match asset order")
        for asset in self.asset_order:
            eligibility = self.economics[asset].eligibility(decision_time=self.decision_time)
            if not eligibility.eligible:
                raise ValueError(f"asset {asset} economics are not eligible: {eligibility.reasons}")
            if not self.expected_costs[asset].complete:
                raise ValueError(f"asset {asset} expected cost is incomplete")
            if (
                self.expected_costs[asset].reporting_currency
                != self.economics[asset].reporting_currency
            ):
                raise ValueError(f"asset {asset} cost reporting currency mismatch")


@dataclass(frozen=True, slots=True)
class ContinuousTarget:
    """Solver-independent accepted continuous physical target."""

    asset_order: tuple[str, ...]
    current_position: tuple[Decimal, ...]
    target_position: tuple[Decimal, ...]
    physical_delta: tuple[Decimal, ...]
    expected_cost_reporting: Decimal
    expected_financing_reporting: Decimal
    solver_status: str
    feasibility_residual: Decimal
    solver_policy_identity: str
    disposition: DecisionDisposition = DecisionDisposition.ACCEPTED
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition is DecisionDisposition.ACCEPTED:
            if len(self.asset_order) != len(self.target_position) or len(
                self.target_position
            ) != len(self.physical_delta):
                raise ValueError("accepted target vectors must have matching lengths")
            if any(
                target - current != delta
                for current, target, delta in zip(
                    self.current_position, self.target_position, self.physical_delta, strict=True
                )
            ):
                raise ValueError("physical target delta does not reconcile")
            if self.feasibility_residual < 0 or not self.feasibility_residual.is_finite():
                raise ValueError("target feasibility residual must be finite and non-negative")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "contract": TARGET_CONTRACT,
                "asset_order": self.asset_order,
                "current_position": self.current_position,
                "target_position": self.target_position,
                "physical_delta": self.physical_delta,
                "expected_cost_reporting": self.expected_cost_reporting,
                "expected_financing_reporting": self.expected_financing_reporting,
                "solver_status": self.solver_status,
                "feasibility_residual": self.feasibility_residual,
                "solver_policy_identity": self.solver_policy_identity,
                "disposition": self.disposition,
                "reason_codes": self.reason_codes,
            }
        )

    @property
    def semantic_identity(self) -> str:
        return _identity(self.canonical_bytes)


def independent_continuous_feasibility(
    position: Sequence[Decimal],
    inputs: ContinuousTargetInputs,
) -> tuple[bool, Decimal, tuple[str, ...]]:
    """Recompute ordered-risk and global-cap feasibility without CVXPY."""

    values = tuple(_decimal(value, "feasibility position") for value in position)
    if len(values) != len(inputs.asset_order):
        return False, Decimal("Infinity"), ("POSITION_WIDTH_INVALID",)
    float_values = tuple(float(value) for value in values)
    if any(not isfinite(value) for value in float_values):
        return False, Decimal("Infinity"), ("POSITION_NONFINITE",)
    violations: list[Decimal] = []
    reasons: list[str] = []
    caps = inputs.risk.caps
    tolerance = max(
        inputs.risk.finite_tolerance,
        float(inputs.solver_policy.absolute_tolerance),
    )
    checks = (
        (
            max(
                (
                    abs(value) - cap
                    for value, cap in zip(float_values, caps.asset_caps, strict=True)
                ),
                default=0.0,
            ),
            "ASSET_CAP",
        ),
        (sum(abs(value) for value in float_values) - caps.gross_cap, "GROSS_CAP"),
        (abs(sum(float_values)) - caps.net_cap, "NET_CAP"),
        (
            max(
                (
                    abs(value) - caps.concentration_cap * sum(abs(item) for item in float_values)
                    for value in float_values
                ),
                default=0.0,
            ),
            "CONCENTRATION_CAP",
        ),
    )
    violations.extend(Decimal(str(max(value, 0.0))) for value, _ in checks)
    reasons.extend(name for value, name in checks if value > tolerance)
    for exposures, limits, name in (
        (inputs.risk.group_exposure(float_values), inputs.risk.group_caps, "GROUP_CAP"),
        (inputs.risk.currency_exposure(float_values), inputs.risk.currency_caps, "CURRENCY_CAP"),
    ):
        for exposure, limit in zip(exposures, limits, strict=True):
            violation = max(abs(exposure) - limit, 0.0)
            violations.append(Decimal(str(violation)))
            if violation > tolerance:
                reasons.append(name)
    risk_violation = max(inputs.risk.portfolio_risk(float_values) - caps.portfolio_risk_cap, 0.0)
    violations.append(Decimal(str(risk_violation)))
    if risk_violation > tolerance:
        reasons.append("PORTFOLIO_RISK_CAP")
    for value, current, alpha in zip(
        values, inputs.current_position, inputs.alpha_return, strict=True
    ):
        if alpha == 0:
            if abs(value) > abs(current) + Decimal(str(tolerance)):
                violations.append(abs(value) - abs(current))
                reasons.append("ZERO_FORECAST_NEW_EXPOSURE")
            if current != 0 and value != 0 and (current > 0) != (value > 0):
                violations.append(abs(value))
                reasons.append("ZERO_FORECAST_DIRECTION")
    residual = max(violations, default=Decimal("0"))
    return not reasons, residual, tuple(dict.fromkeys(reasons))


# Compatibility aliases for callers that prefer noun-first names.
build_horizon_intent = construct_horizon_intent
net_horizon_intents = match_internal_opposing_changes
replay_virtual_position_transitions = replay_virtual_transitions
