"""R3.D application seam: Decimal rounding, conservative repair and attribution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Final

from qtrad.domain.economics import CostComponentKind, ExpectedCostState, ProductEconomics
from qtrad.domain.portfolio import (
    ContinuousTarget,
    ContinuousTargetInputs,
    NettingResult,
)
from qtrad.domain.r3_rounding import (
    REASON_CODE_VERSION,
    RepairedSleeveAttribution,
    RoundedTarget,
    RoundingDisposition,
    RoundingPolicy,
    RoundingReasonCode,
    cost_states_identity,
    order_reason_codes,
)

_REASON_CAPS: Final[tuple[tuple[str, str], ...]] = (
    ("asset", RoundingReasonCode.ASSET_CAP_REPAIR.value),
    ("gross", RoundingReasonCode.GROSS_CAP_REPAIR.value),
    ("net", RoundingReasonCode.NET_CAP_REPAIR.value),
    ("concentration", RoundingReasonCode.CONCENTRATION_CAP_REPAIR.value),
    ("group", RoundingReasonCode.GROUP_CAP_REPAIR.value),
    ("currency", RoundingReasonCode.CURRENCY_CAP_REPAIR.value),
    ("risk", RoundingReasonCode.PORTFOLIO_RISK_REPAIR.value),
)

_REASON_CAPS_BY_NAME: Final[dict[str, str]] = dict(_REASON_CAPS)


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is Decimal and value.is_finite():
        return value
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite numeric value")
    if isinstance(value, (int, float)):
        result = Decimal(str(value))
        if result.is_finite():
            return result
    raise ValueError(f"{name} must be a finite numeric value")


def _normalise_reason(reason: str) -> str:
    value = reason.upper()
    mapping = {
        "ZERO_FORECAST_NEW_EXPOSURE": RoundingReasonCode.ZERO_FORECAST_NEW_EXPOSURE_BLOCKED.value,
        "SOLVER_INACCURATE": RoundingReasonCode.SOLVER_NON_OPTIMAL.value,
        "SOLVER_NON_OPTIMAL": RoundingReasonCode.SOLVER_NON_OPTIMAL.value,
        "SOLVER_ERROR": RoundingReasonCode.SOLVER_ERROR.value,
        "SOLVER_INFEASIBLE": RoundingReasonCode.SOLVER_INFEASIBLE.value,
        "SOLVER_RESULT_INVALID": RoundingReasonCode.SOLVER_RESULT_INVALID.value,
        "ASSET_CAP": RoundingReasonCode.ASSET_CAP_REPAIR.value,
        "GROSS_CAP": RoundingReasonCode.GROSS_CAP_REPAIR.value,
        "NET_CAP": RoundingReasonCode.NET_CAP_REPAIR.value,
        "CONCENTRATION_CAP": RoundingReasonCode.CONCENTRATION_CAP_REPAIR.value,
        "GROUP_CAP": RoundingReasonCode.GROUP_CAP_REPAIR.value,
        "CURRENCY_CAP": RoundingReasonCode.CURRENCY_CAP_REPAIR.value,
        "PORTFOLIO_RISK_CAP": RoundingReasonCode.PORTFOLIO_RISK_REPAIR.value,
    }
    return mapping.get(
        value,
        value
        if value in {item.value for item in RoundingReasonCode}
        else RoundingReasonCode.DECISION_BLOCKED.value,
    )


def _reason_for_input(reason: str) -> str:
    upper = reason.upper()
    if "FX" in upper or "CURRENCY" in upper:
        return RoundingReasonCode.INPUT_FX_MISSING.value
    if any(token in upper for token in ("COMMISSION", "FINANCING", "IMPACT", "COST")):
        return RoundingReasonCode.INPUT_COST_INVALID.value
    return RoundingReasonCode.ASSET_PAPER_INELIGIBLE.value


def _quantity_valid(quantity: Decimal, economics: ProductEconomics) -> bool:
    try:
        return economics.round_quantity(quantity) == quantity
    except (ArithmeticError, TypeError, ValueError):
        return False


def _violations(
    values: Sequence[Decimal], inputs: ContinuousTargetInputs
) -> tuple[tuple[str, Decimal], ...]:
    """Return ordered positive cap residuals using the risk state's exact policy."""
    risk = inputs.risk
    try:
        floats = tuple(float(value) for value in values)
        residuals: list[tuple[str, Decimal]] = []
        asset_residual = max(
            (abs(value) - cap for value, cap in zip(floats, risk.caps.asset_caps, strict=True)),
            default=0.0,
        )
        residuals.append(("asset", Decimal(str(max(asset_residual, 0.0)))))
        residuals.append(
            (
                "gross",
                Decimal(str(max(sum(abs(value) for value in floats) - risk.caps.gross_cap, 0.0))),
            )
        )
        residuals.append(("net", Decimal(str(max(abs(sum(floats)) - risk.caps.net_cap, 0.0)))))
        gross = sum(abs(value) for value in floats)
        concentration = 0.0 if gross == 0 else max(abs(value) for value in floats) / gross
        residuals.append(
            ("concentration", Decimal(str(max(concentration - risk.caps.concentration_cap, 0.0))))
        )
        for name, exposures, caps in (
            ("group", risk.group_exposure(floats), risk.group_caps),
            ("currency", risk.currency_exposure(floats), risk.currency_caps),
        ):
            residuals.append(
                (
                    name,
                    Decimal(
                        str(
                            max(
                                (
                                    abs(value) - cap
                                    for value, cap in zip(exposures, caps, strict=True)
                                ),
                                default=0.0,
                            )
                        )
                    ),
                )
            )
        residuals.append(
            (
                "risk",
                Decimal(str(max(risk.portfolio_risk(floats) - risk.caps.portfolio_risk_cap, 0.0))),
            )
        )
        return tuple((name, residual) for name, residual in residuals if residual > Decimal("0"))
    except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("risk validation failed") from exc


def _reduce_one(value: Decimal, increment: Decimal) -> Decimal:
    magnitude = abs(value)
    if magnitude <= increment:
        return Decimal("0")
    result = magnitude - increment
    return result if value > 0 else -result


def _repair(
    values: list[Decimal],
    inputs: ContinuousTargetInputs,
    reasons: set[str],
    policy: RoundingPolicy,
) -> tuple[list[Decimal], bool]:
    repaired = False
    for _ in range(policy.max_repair_steps):
        violations = _violations(values, inputs)
        if not violations:
            return values, repaired
        for name, _ in violations:
            reasons.add(_REASON_CAPS_BY_NAME[name])
        candidates: list[tuple[tuple[Decimal, ...], int, list[Decimal]]] = []
        quantity_blocked = False
        for index, value in enumerate(values):
            if value == 0:
                continue
            asset = inputs.asset_order[index]
            increment = inputs.economics[asset].quantity_increment
            trial = list(values)
            trial[index] = _reduce_one(value, increment)
            if not _quantity_valid(trial[index], inputs.economics[asset]):
                quantity_blocked = True
                continue
            try:
                trial_violations = _violations(trial, inputs)
            except ValueError:
                continue
            score = (
                sum((residual for _, residual in trial_violations), Decimal("0")),
                Decimal(str(sum(abs(float(item)) for item in trial))),
            )
            candidates.append((score, index, trial))
        if not candidates:
            if quantity_blocked:
                reasons.add(RoundingReasonCode.MINIMUM_QUANTITY_NOT_MET.value)
            break
        _, _, values = min(candidates, key=lambda item: (item[0], item[1]))
        repaired = True
    return values, repaired


def _repaired_attributions(
    netting: NettingResult,
    final_delta: Sequence[Decimal],
    economics: Mapping[str, ProductEconomics],
    reasons: set[str],
) -> tuple[tuple[RepairedSleeveAttribution, ...], Decimal]:
    attributions: list[RepairedSleeveAttribution] = []
    residual = Decimal("0")
    for asset_index, asset_item in enumerate(netting.assets):
        final = final_delta[asset_index]
        original = asset_item.attributions
        if not original:
            residual += abs(final)
            continue
        parent_external = sum((item.external_delta_share for item in original), Decimal("0"))
        changed = final != parent_external
        allocated: dict[object, Decimal] = {}
        if changed:
            reasons.add(RoundingReasonCode.ATTRIBUTION_RESIDUAL_REPAIRED.value)
            increment = economics[asset_item.asset_id].quantity_increment
            direction = Decimal("1") if final > 0 else Decimal("-1")
            candidates = [item for item in original if item.external_delta_share * direction > 0]
            if final != 0 and candidates:
                total_weight = sum(
                    (abs(item.external_delta_share) for item in candidates), Decimal("0")
                )
                units = int(abs(final) / increment)
                base_units: dict[object, int] = {}
                remainders: dict[object, Decimal] = {}
                for item in candidates:
                    exact = Decimal(units) * abs(item.external_delta_share) / total_weight
                    base = int(exact)
                    base_units[item.key] = base
                    remainders[item.key] = exact - Decimal(base)
                remaining = units - sum(base_units.values())
                ranked = sorted(
                    candidates,
                    key=lambda item: (-remainders[item.key], item.key.canonical_tuple),
                )
                for item in ranked[:remaining]:
                    base_units[item.key] += 1
                for item in candidates:
                    allocated[item.key] = direction * increment * Decimal(base_units[item.key])
            elif final != 0:
                residual += abs(final)
        for item in original:
            external = (
                item.external_delta_share if not changed else allocated.get(item.key, Decimal("0"))
            )
            attributions.append(
                RepairedSleeveAttribution(
                    item.key,
                    item.requested_delta,
                    item.internal_cross_quantity,
                    external,
                    repair_delta=external - item.external_delta_share,
                    reason_codes=(
                        (RoundingReasonCode.ATTRIBUTION_RESIDUAL_REPAIRED.value,) if changed else ()
                    ),
                )
            )
    ordered = tuple(sorted(attributions, key=lambda item: item.key.canonical_tuple))
    expected = sum(final_delta, Decimal("0"))
    actual = sum((item.external_delta_share for item in ordered), Decimal("0"))
    residual += abs(expected - actual)
    if residual:
        reasons.add(RoundingReasonCode.ATTRIBUTION_RESIDUAL_REPAIRED.value)
    return ordered, residual


def _blocked(
    target: ContinuousTarget,
    inputs: ContinuousTargetInputs,
    policy: RoundingPolicy,
    reasons: set[str],
) -> RoundedTarget:
    reasons.add(RoundingReasonCode.DECISION_BLOCKED.value)
    try:
        decision_input_identity = inputs.decision_input_identity
    except (AttributeError, KeyError, TypeError, ValueError):
        decision_input_identity = target.decision_input_identity
    return RoundedTarget(
        source_class=inputs.source_class,
        evidence_purpose=inputs.evidence_purpose,
        asset_order=inputs.asset_order,
        current_position=inputs.current_position,
        continuous_target=inputs.current_position,
        target_position=(),
        physical_delta=(),
        disposition=RoundingDisposition.BLOCKED,
        reason_codes=order_reason_codes(tuple(reasons)),
        expected_costs={},
        expected_cost_reporting=Decimal("0"),
        expected_financing_reporting=Decimal("0"),
        netting=inputs.netting,
        attributions=(),
        policy_identity=policy.semantic_identity,
        decision_input_identity=decision_input_identity,
        continuous_target_identity=target.semantic_identity,
    )


def round_and_repair_target(
    target: ContinuousTarget,
    inputs: ContinuousTargetInputs,
    *,
    policy: RoundingPolicy | None = None,
) -> RoundedTarget:
    """Convert one continuous target to a valid Decimal target or fail closed."""
    selected_policy = policy or RoundingPolicy()
    reasons: set[str] = {_normalise_reason(reason) for reason in target.reason_codes}
    try:
        if target.decision_input_identity != inputs.decision_input_identity:
            reasons.add(RoundingReasonCode.DECISION_BLOCKED.value)
            return _blocked(target, inputs, selected_policy, reasons)
    except (AttributeError, KeyError, TypeError, ValueError):
        reasons.add(
            RoundingReasonCode.INPUT_RISK_INVALID.value
            if getattr(inputs, "risk", None) is None
            else RoundingReasonCode.INPUT_ECONOMICS_MISSING.value
        )
        return _blocked(target, inputs, selected_policy, reasons)
    if RoundingReasonCode.DECISION_BLOCKED.value in reasons:
        return _blocked(target, inputs, selected_policy, reasons)
    try:
        if target.asset_order != inputs.asset_order:
            raise ValueError("target and input asset order mismatch")
        if (
            target.source_class is not inputs.source_class
            or target.evidence_purpose is not inputs.evidence_purpose
        ):
            raise ValueError("target and input source boundary mismatch")
        continuous = (
            tuple(target.target_position)
            if target.disposition.value == "ACCEPTED"
            and len(target.target_position) == len(inputs.asset_order)
            else tuple(inputs.current_position)
        )
        current = list(inputs.current_position)
        projected = target.disposition.value != "ACCEPTED"
        try:
            for index, asset in enumerate(inputs.asset_order):
                economics = inputs.economics[asset]
                eligibility = economics.eligibility(
                    decision_time=inputs.decision_time,
                    proposed_quantity=abs(current[index]),
                )
                if not eligibility.eligible:
                    reasons.update(_reason_for_input(reason) for reason in eligibility.reasons)
                    reasons.add(RoundingReasonCode.ASSET_PAPER_INELIGIBLE.value)
                    return _blocked(target, inputs, selected_policy, reasons)
                if not _quantity_valid(current[index], economics):
                    current[index] = economics.round_quantity(current[index])
                    projected = True
                    reasons.add(RoundingReasonCode.CURRENT_POSITION_PROJECTED.value)
                    reasons.add(RoundingReasonCode.QUANTITY_ROUNDED.value)
                    if current[index] == 0:
                        reasons.add(RoundingReasonCode.MINIMUM_QUANTITY_NOT_MET.value)
        except (ArithmeticError, KeyError, TypeError, ValueError, AttributeError):
            reasons.add(RoundingReasonCode.INPUT_ECONOMICS_MISSING.value)
            return _blocked(target, inputs, selected_policy, reasons)
        values: list[Decimal] = []
        for index, asset in enumerate(inputs.asset_order):
            economics = inputs.economics[asset]
            candidate = _decimal(continuous[index], f"continuous target {asset}")
            rounded = economics.round_quantity(candidate)
            if rounded != candidate:
                reasons.add(RoundingReasonCode.QUANTITY_ROUNDED.value)
            if candidate != 0 and rounded == 0:
                reasons.add(RoundingReasonCode.MINIMUM_QUANTITY_NOT_MET.value)
            if inputs.alpha_return[index] == 0:
                prior = current[index]
                if abs(rounded) > abs(prior) or (
                    prior != 0 and rounded != 0 and (prior > 0) != (rounded > 0)
                ):
                    rounded = prior
                    projected = True
                    reasons.add(RoundingReasonCode.ZERO_FORECAST_NEW_EXPOSURE_BLOCKED.value)
                    reasons.add(RoundingReasonCode.NEW_ALPHA_EXPOSURE_BLOCKED.value)
            values.append(rounded)
        if target.disposition.value != "ACCEPTED":
            projected = True
            reasons.add(RoundingReasonCode.CURRENT_POSITION_PROJECTED.value)
        try:
            values, repaired = _repair(values, inputs, reasons, selected_policy)
            for index, asset in enumerate(inputs.asset_order):
                if not _quantity_valid(values[index], inputs.economics[asset]):
                    reasons.add(RoundingReasonCode.MINIMUM_QUANTITY_NOT_MET.value)
                    return _blocked(target, inputs, selected_policy, reasons)
            violations = _violations(values, inputs)
            if violations:
                for name, _ in violations:
                    reasons.add(_REASON_CAPS_BY_NAME[name])
                reasons.add(RoundingReasonCode.DECISION_BLOCKED.value)
                return _blocked(target, inputs, selected_policy, reasons)
            inputs.risk.validate_position(tuple(float(value) for value in values))
        except (ArithmeticError, AttributeError, TypeError, ValueError, OverflowError):
            reasons.add(RoundingReasonCode.INPUT_RISK_INVALID.value)
            return _blocked(target, inputs, selected_policy, reasons)
        projected = projected or repaired
        try:
            for index, asset in enumerate(inputs.asset_order):
                eligibility = inputs.economics[asset].eligibility(
                    decision_time=inputs.decision_time,
                    proposed_quantity=abs(values[index]),
                )
                if not eligibility.eligible:
                    reasons.update(_reason_for_input(reason) for reason in eligibility.reasons)
                    reasons.add(RoundingReasonCode.ASSET_PAPER_INELIGIBLE.value)
                    return _blocked(target, inputs, selected_policy, reasons)
        except (ArithmeticError, KeyError, TypeError, ValueError, AttributeError):
            reasons.add(RoundingReasonCode.INPUT_ECONOMICS_MISSING.value)
            return _blocked(target, inputs, selected_policy, reasons)
        try:
            attributions, residual = _repaired_attributions(
                inputs.netting,
                tuple(values[i] - current[i] for i in range(len(values))),
                inputs.economics,
                reasons,
            )
        except (ArithmeticError, KeyError, TypeError, ValueError, AttributeError):
            reasons.add(RoundingReasonCode.ATTRIBUTION_RESIDUAL_REPAIRED.value)
            reasons.add(RoundingReasonCode.DECISION_BLOCKED.value)
            return _blocked(target, inputs, selected_policy, reasons)
        if residual:
            return _blocked(target, inputs, selected_policy, reasons)
        expected_costs: dict[str, ExpectedCostState] = {}
        expected_cost = Decimal("0")
        expected_financing = Decimal("0")
        target_values = tuple(values)
        for index, asset in enumerate(inputs.asset_order):
            state = inputs.continuous_costs[asset].evaluate(
                current_quantity=current[index],
                target_quantity=target_values[index],
                decision_time=inputs.decision_time,
                internal_cross_quantity=inputs.netting.assets[index].internal_cross_quantity,
            )
            if not state.complete:
                raise ValueError("cost state is incomplete")
            total = state.require_total_reporting()
            finance = next(
                item for item in state.components if item.component is CostComponentKind.FINANCING
            )
            if finance.reporting_amount is None:
                raise ValueError("financing reporting amount is missing")
            expected_cost += total - finance.reporting_amount
            expected_financing += finance.reporting_amount
            expected_costs[asset] = state
        return RoundedTarget(
            source_class=inputs.source_class,
            evidence_purpose=inputs.evidence_purpose,
            asset_order=inputs.asset_order,
            current_position=tuple(current),
            continuous_target=tuple(continuous),
            target_position=target_values,
            physical_delta=tuple(target_values[i] - current[i] for i in range(len(target_values))),
            disposition=RoundingDisposition.PROJECTED
            if projected
            else RoundingDisposition.ACCEPTED,
            reason_codes=order_reason_codes(tuple(reasons)),
            expected_costs=expected_costs,
            expected_cost_reporting=expected_cost,
            expected_financing_reporting=expected_financing,
            netting=inputs.netting,
            attributions=attributions,
            policy_identity=selected_policy.semantic_identity,
            decision_input_identity=inputs.decision_input_identity,
            continuous_target_identity=target.semantic_identity,
            cost_state_identity=cost_states_identity(expected_costs),
            attribution_residual=Decimal("0"),
        )
    except KeyError:
        reasons.add(RoundingReasonCode.INPUT_ECONOMICS_MISSING.value)
        return _blocked(target, inputs, selected_policy, reasons)
    except (ArithmeticError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        reasons.add(
            RoundingReasonCode.INPUT_FX_MISSING.value
            if "FX" in str(exc).upper()
            else RoundingReasonCode.INPUT_COST_INVALID.value
        )
        return _blocked(target, inputs, selected_policy, reasons)


construct_rounded_target = round_and_repair_target
round_continuous_target = round_and_repair_target
apply_rounding_and_repair = round_and_repair_target
repair_continuous_target = round_and_repair_target

__all__ = [
    "REASON_CODE_VERSION",
    "RoundedTarget",
    "RoundingPolicy",
    "apply_rounding_and_repair",
    "construct_rounded_target",
    "repair_continuous_target",
    "round_and_repair_target",
    "round_continuous_target",
]
