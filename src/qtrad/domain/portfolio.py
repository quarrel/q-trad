"""Provider-neutral one-horizon portfolio and target contracts.

R3.C deliberately stops at continuous quantities.  Product rounding, repair,
paper fills and durable receipts belong to later boundaries.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from typing import Final, cast

from qtrad.domain.economics import (
    ContinuousCostModel,
    CostComponentKind,
    ExpectedCostState,
    GrossForecast,
    InputStatus,
    ProductEconomics,
    SolverPolicy,
)
from qtrad.domain.risk import RiskState
from qtrad.domain.time import require_utc

PORTFOLIO_CONTRACT: Final = "qtrad-r3-one-horizon-portfolio-v2"
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


def _require_identity(value: str, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a SHA-256 hex digest")


def _identity(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _position_identity(key: SleeveKey, quantity: Decimal) -> str:
    return _identity(
        {
            "contract": PORTFOLIO_CONTRACT,
            "key": key.as_json(),
            "quantity": quantity,
        }
    )


def _zero_forecast_blocks(prior: Decimal, proposed: Decimal) -> bool:
    """Return whether a zero forecast would introduce new directional exposure."""
    increases = abs(proposed) > abs(prior)
    reverses = prior != 0 and proposed != 0 and (prior > 0) != (proposed > 0)
    opens = prior == 0 and proposed != 0
    return increases or reverses or opens


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
        if not self.state_identity:
            object.__setattr__(self, "state_identity", _position_identity(self.key, self.quantity))
        _require_identity(self.state_identity, "virtual state identity")
        expected_identity = _position_identity(self.key, self.quantity)
        if self.state_identity != expected_identity:
            raise ValueError("virtual state identity does not match key and quantity")

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
    if forecast == 0 and _zero_forecast_blocks(position.quantity, requested_quantity):
        requested_quantity = position.quantity
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
        if sum((item.internal_cross_quantity for item in self.attributions), Decimal("0")) != (
            self.internal_cross_quantity * Decimal("2")
        ):
            raise ValueError("asset internal attribution does not reconcile")
        ordered = tuple(sorted(self.attributions, key=lambda item: item.key.canonical_tuple))
        if ordered != self.attributions:
            raise ValueError("asset attributions must be canonical")
        if len({item.key for item in self.attributions}) != len(self.attributions):
            raise ValueError("asset attributions must be unique")
        if any(item.key.asset_id != self.asset_id for item in self.attributions):
            raise ValueError("asset attribution asset mismatch")


@dataclass(frozen=True, slots=True)
class NettingResult:
    assets: tuple[AssetNetting, ...]
    sleeves: tuple[SleeveAttribution, ...]

    def __post_init__(self) -> None:
        if not self.assets:
            raise ValueError("netting must contain at least one asset")
        ordered = tuple(sorted(self.assets, key=lambda item: item.asset_id))
        if ordered != self.assets:
            raise ValueError("netting assets must be canonical")
        if len({item.asset_id for item in self.assets}) != len(self.assets):
            raise ValueError("netting assets must be unique")
        ordered_sleeves = tuple(sorted(self.sleeves, key=lambda item: item.key.canonical_tuple))
        if ordered_sleeves != self.sleeves:
            raise ValueError("netting sleeves must be canonical")
        if len({item.key for item in self.sleeves}) != len(self.sleeves):
            raise ValueError("netting sleeves must be unique")
        attributions = tuple(
            attribution for asset in self.assets for attribution in asset.attributions
        )
        if attributions != self.sleeves:
            raise ValueError("netting sleeve attributions must reconcile")

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
    gross_forecast: GrossForecast
    decision_time: datetime
    expiry_time: datetime
    model_identity: str
    risk_policy_identity: str
    cost_policy_identity: str
    prior_state_identity: str
    successor_state_identity: str
    disposition: DecisionDisposition = DecisionDisposition.ACCEPTED
    accepted_physical_quantity: Decimal = Decimal("0")
    external_delta_share: Decimal = Decimal("0")
    internal_cross_quantity: Decimal = Decimal("0")
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.gross_forecast) is not GrossForecast:
            raise ValueError("transition gross forecast must be a GrossForecast")
        if type(self.disposition) is not DecisionDisposition:
            raise ValueError("transition disposition must be explicit")
        for value, name in (
            (self.prior_quantity, "transition prior quantity"),
            (self.next_quantity, "transition next quantity"),
            (self.accepted_physical_quantity, "transition accepted physical quantity"),
            (self.external_delta_share, "transition external delta share"),
        ):
            _decimal(value, name)
        _nonnegative(self.internal_cross_quantity, "transition internal cross quantity")
        require_utc(self.decision_time, "transition decision time")
        require_utc(self.expiry_time, "transition expiry time")
        if self.expiry_time <= self.decision_time:
            raise ValueError("transition expiry must follow decision time")
        if self.expiry_time - self.decision_time != self.gross_forecast.horizon:
            raise ValueError("transition expiry must match gross forecast horizon")
        if self.gross_forecast.horizon != self.key.horizon:
            raise ValueError("transition forecast horizon must match sleeve horizon")
        if self.gross_forecast.model_identity != self.model_identity:
            raise ValueError("transition model identity must match gross forecast")
        if self.gross_forecast.expected_return == 0 and _zero_forecast_blocks(
            self.prior_quantity, self.next_quantity
        ):
            raise ValueError("zero forecast cannot create new exposure")
        if abs(self.external_delta_share) + self.internal_cross_quantity != abs(
            self.requested_delta
        ):
            raise ValueError("transition attribution does not reconcile requested delta")
        _require_identity(self.prior_state_identity, "transition prior state identity")
        _require_identity(self.successor_state_identity, "transition successor state identity")
        expected_successor = _position_identity(self.key, self.next_quantity)
        if self.successor_state_identity != expected_successor:
            raise ValueError("transition successor state identity does not match next quantity")
        if self.disposition is DecisionDisposition.BLOCKED and (
            self.next_quantity != self.prior_quantity
            or self.successor_state_identity != _position_identity(self.key, self.prior_quantity)
        ):
            raise ValueError("blocked transition must preserve the prior position")
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
            "gross_forecast": {
                "expected_return": self.gross_forecast.expected_return,
                "horizon": self.gross_forecast.horizon,
                "return_unit": self.gross_forecast.return_unit,
                "model_identity": self.gross_forecast.model_identity,
            },
            "decision_time": self.decision_time,
            "expiry_time": self.expiry_time,
            "model_identity": self.model_identity,
            "risk_policy_identity": self.risk_policy_identity,
            "cost_policy_identity": self.cost_policy_identity,
            "prior_state_identity": self.prior_state_identity,
            "successor_state_identity": self.successor_state_identity,
            "disposition": self.disposition,
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
    transition_identities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.transition_count) is not int or self.transition_count < 0:
            raise ValueError("replay transition count must be non-negative")
        if tuple(self.positions) != tuple(
            sorted(self.positions, key=lambda item: item.key.canonical_tuple)
        ):
            raise ValueError("replay positions must be canonical")
        if len({position.key for position in self.positions}) != len(self.positions):
            raise ValueError("replay positions must have unique sleeves")
        for position in self.positions:
            _require_identity(position.state_identity, "replay position state identity")
        if len(self.transition_identities) != self.transition_count:
            raise ValueError("replay transition identities must match transition count")
        for identity in self.transition_identities:
            _require_identity(identity, "replay transition identity")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "contract": PORTFOLIO_CONTRACT,
                "transition_count": self.transition_count,
                "transition_identities": self.transition_identities,
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
        previous_identity = (
            previous.state_identity
            if previous is not None
            else _position_identity(transition.key, Decimal("0"))
        )
        if previous_quantity != transition.prior_quantity:
            raise ValueError("virtual transition prior quantity does not match replay state")
        if previous_identity != transition.prior_state_identity:
            raise ValueError("virtual transition prior state identity does not match replay state")
        if transition.gross_forecast.expected_return == 0 and _zero_forecast_blocks(
            previous_quantity, transition.next_quantity
        ):
            raise ValueError("zero forecast cannot create new exposure during replay")
        current[transition.key] = VirtualPosition(
            transition.key,
            transition.next_quantity,
            transition.successor_state_identity,
        )
    return VirtualReplay(
        positions=tuple(
            current[key] for key in sorted(current, key=lambda item: item.canonical_tuple)
        ),
        transition_count=len(ordered),
        transition_identities=tuple(item.semantic_identity for item in ordered),
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
    continuous_costs: Mapping[str, ContinuousCostModel]
    risk: RiskState
    solver_policy: SolverPolicy
    # Legacy point states are retained only as an output/reference surface.  The
    # optimiser never consumes them.
    expected_costs: Mapping[str, ExpectedCostState] = field(
        default_factory=lambda: dict[str, ExpectedCostState]()
    )
    netting: NettingResult | None = None

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
        if set(self.continuous_costs) != set(self.asset_order):
            raise ValueError("target continuous-cost keys do not match asset order")
        if self.expected_costs and set(self.expected_costs) != set(self.asset_order):
            raise ValueError("target expected-cost keys do not match asset order")
        if self.netting is not None:
            if self.netting.asset_order != self.asset_order:
                raise ValueError("target netting order does not match asset order")
            expected_external = tuple(
                target - current
                for target, current in zip(
                    self.requested_target, self.current_position, strict=True
                )
            )
            if self.netting.external_deltas != expected_external:
                raise ValueError("target netting external deltas do not match request")
        for _index, asset in enumerate(self.asset_order):
            economics = self.economics[asset]
            model = self.continuous_costs[asset]
            eligibility = economics.eligibility(decision_time=self.decision_time)
            if not eligibility.eligible:
                raise ValueError(f"asset {asset} economics are not eligible: {eligibility.reasons}")
            for schedule, label in (
                (economics.commission, "commission"),
                (economics.financing, "financing"),
            ):
                if schedule.status is InputStatus.AVAILABLE and schedule.minimum != Decimal("0"):
                    raise ValueError(f"{asset} {label} minimum charge is unsupported for R3.C")
            if model.asset_id != asset:
                raise ValueError(f"asset {asset} continuous model identity mismatch")
            if model.reporting_currency != economics.reporting_currency:
                raise ValueError(f"asset {asset} cost reporting currency mismatch")
            if model.horizon != self.risk.horizon:
                raise ValueError(f"asset {asset} continuous cost horizon mismatch")
            if model.horizon != ONE_HORIZON:
                raise ValueError(f"asset {asset} continuous cost horizon must be 15m")


def _cost_state_payload(state: ExpectedCostState) -> dict[str, object]:
    return {
        "decision_time": state.decision_time,
        "current_quantity": state.current_quantity,
        "target_quantity": state.target_quantity,
        "holding_interval": state.holding_interval,
        "reporting_currency": state.reporting_currency,
        "internal_cross_quantity": state.internal_cross_quantity,
        "version": state.version,
        "provenance": state.provenance,
        "components": tuple(
            {
                "component": component.component,
                "status": component.status,
                "basis": component.basis,
                "native_amount": component.native_amount,
                "native_currency": component.native_currency,
                "reporting_amount": component.reporting_amount,
                "reporting_currency": component.reporting_currency,
                "quantity_basis": component.quantity_basis,
                "holding_interval": component.holding_interval,
                "version": component.version,
                "provenance": component.provenance,
                "reason": component.reason,
                "conversion_rate": component.conversion_rate,
                "conversion_source": component.conversion_source,
                "conversion_version": component.conversion_version,
            }
            for component in state.components
        ),
    }


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
    expected_costs: Mapping[str, ExpectedCostState]
    disposition: DecisionDisposition = DecisionDisposition.ACCEPTED
    reason_codes: tuple[str, ...] = ()
    requested_position: tuple[Decimal, ...] = ()
    decision_time: datetime | None = None
    cost_model_identities: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    reporting_currencies: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    netting: NettingResult | None = None

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.asset_order))
        if not ordered or len(set(ordered)) != len(self.asset_order) or ordered != self.asset_order:
            raise ValueError("target asset order must be non-empty and canonical")
        if self.disposition is DecisionDisposition.ACCEPTED:
            if self.solver_status != SolverResultStatus.OPTIMAL.value:
                raise ValueError("accepted target requires exact optimal status")
            _decimal(self.expected_cost_reporting, "target expected cost reporting")
            _decimal(self.expected_financing_reporting, "target expected financing reporting")
            _decimal(self.feasibility_residual, "target feasibility residual")
            n = len(self.asset_order)
            if (
                len(self.current_position) != n
                or len(self.requested_position) != n
                or len(self.target_position) != n
                or len(self.physical_delta) != n
            ):
                raise ValueError("accepted target vectors must have matching lengths")
            for values, label in (
                (self.current_position, "target current position"),
                (self.requested_position, "target requested position"),
                (self.target_position, "target position"),
                (self.physical_delta, "target physical delta"),
            ):
                for value in values:
                    _decimal(value, label)
            if any(
                target - current != delta
                for current, target, delta in zip(
                    self.current_position, self.target_position, self.physical_delta, strict=True
                )
            ):
                raise ValueError("physical target delta does not reconcile")
            if self.decision_time is None:
                raise ValueError("accepted target decision time is required")
            require_utc(self.decision_time, "accepted target decision time")
            if set(self.expected_costs) != set(self.asset_order):
                raise ValueError("accepted target cost states must cover every asset")
            if set(self.cost_model_identities) != set(self.asset_order):
                raise ValueError("accepted target model identities must cover every asset")
            if set(self.reporting_currencies) != set(self.asset_order):
                raise ValueError("accepted target currencies must cover every asset")
            for asset, identity in self.cost_model_identities.items():
                _require_identity(identity, f"{asset} cost model identity")
            non_financing = Decimal("0")
            financing = Decimal("0")
            netting_by_asset = (
                {item.asset_id: item for item in self.netting.assets}
                if self.netting is not None
                else {}
            )
            if self.netting is not None and self.netting.asset_order != self.asset_order:
                raise ValueError("accepted target netting order mismatch")
            for index, asset in enumerate(self.asset_order):
                state = self.expected_costs[asset]
                if not state.complete:
                    raise ValueError("accepted target requires complete cost states")
                if (
                    state.decision_time != self.decision_time
                    or state.current_quantity != self.current_position[index]
                    or state.target_quantity != self.target_position[index]
                    or state.holding_interval != ONE_HORIZON
                    or state.reporting_currency != self.reporting_currencies[asset]
                ):
                    raise ValueError("accepted target cost state binding mismatch")
                if not self.reporting_currencies[asset]:
                    raise ValueError("accepted target reporting currency is required")
                state_total = state.require_total_reporting()
                finance_component = next(
                    component
                    for component in state.components
                    if component.component is CostComponentKind.FINANCING
                )
                if finance_component.reporting_amount is None:
                    raise ValueError("accepted target financing amount is required")
                financing += finance_component.reporting_amount
                non_financing += state_total - finance_component.reporting_amount
                if self.netting is None:
                    if state.internal_cross_quantity != Decimal("0"):
                        raise ValueError("unbound internal crossing attribution")
                elif (
                    state.internal_cross_quantity != netting_by_asset[asset].internal_cross_quantity
                ):
                    raise ValueError("accepted target internal crossing mismatch")
            if non_financing != self.expected_cost_reporting:
                raise ValueError("accepted target cost total does not reconcile")
            if financing != self.expected_financing_reporting:
                raise ValueError("accepted target financing total does not reconcile")
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
                "requested_position": self.requested_position,
                "target_position": self.target_position,
                "physical_delta": self.physical_delta,
                "expected_cost_reporting": self.expected_cost_reporting,
                "expected_financing_reporting": self.expected_financing_reporting,
                "solver_status": self.solver_status,
                "feasibility_residual": self.feasibility_residual,
                "solver_policy_identity": self.solver_policy_identity,
                "decision_time": self.decision_time,
                "cost_model_identities": tuple(
                    (asset, self.cost_model_identities[asset])
                    for asset in self.asset_order
                    if asset in self.cost_model_identities
                ),
                "reporting_currencies": tuple(
                    (asset, self.reporting_currencies[asset])
                    for asset in self.asset_order
                    if asset in self.reporting_currencies
                ),
                "expected_costs": tuple(
                    (asset, _cost_state_payload(self.expected_costs[asset]))
                    for asset in self.asset_order
                    if asset in self.expected_costs
                ),
                "netting": self.netting.semantic_identity if self.netting is not None else None,
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
        if alpha == 0 and _zero_forecast_blocks(current, value):
            violations.append(abs(value - current))
            reasons.append("ZERO_FORECAST_NEW_EXPOSURE")
    residual = max(violations, default=Decimal("0"))
    return not reasons, residual, tuple(dict.fromkeys(reasons))


# Compatibility aliases for callers that prefer noun-first names.
build_horizon_intent = construct_horizon_intent
net_horizon_intents = match_internal_opposing_changes
replay_virtual_position_transitions = replay_virtual_transitions
