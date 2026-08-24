"""CVXPY application seam for the R3.C continuous target kernel."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Protocol, cast

import cvxpy as cp

from qtrad.domain.economics import CostBasis, CostComponentKind
from qtrad.domain.portfolio import (
    ContinuousTarget,
    ContinuousTargetInputs,
    DecisionDisposition,
    SolverResultStatus,
    independent_continuous_feasibility,
)


class SolverRunner(Protocol):
    def __call__(
        self,
        inputs: ContinuousTargetInputs,
    ) -> tuple[str, Sequence[float] | None]:
        """Return the exact solver status and a candidate variable vector."""
        ...


def _cp_quad_form(vector: Any, matrix: Any) -> cp.Expression:
    return cast(
        cp.Expression,
        cp.quad_form(vector, matrix, assume_PSD=True),  # pyright: ignore[reportUnknownMemberType]
    )


def _piecewise_cost(model: Any, quantity: Any) -> cp.Expression:
    """Build the exact convex piecewise-linear expression for one model."""
    expression = float(model.slopes[0]) * quantity
    previous_slope = model.slopes[0]
    for breakpoint, slope in zip(model.breakpoints, model.slopes[1:], strict=True):
        expression += float(slope - previous_slope) * cp.pos(  # pyright: ignore[reportUnknownMemberType]
            quantity - float(breakpoint)
        )
        previous_slope = slope
    return cast(cp.Expression, expression * float(model.conversion_rate))


def _run_cvxpy(inputs: ContinuousTargetInputs) -> tuple[str, Sequence[float] | None]:
    n = len(inputs.asset_order)
    target = cp.Variable(n, name=inputs.solver_policy.variable_order[0])
    current = tuple(float(value) for value in inputs.current_position)
    alpha = tuple(float(value) for value in inputs.alpha_return)
    constraints: list[cp.Constraint] = []
    caps = inputs.risk.caps
    gross = cp.sum(cp.abs(target))  # pyright: ignore[reportUnknownMemberType]
    constraints.extend(
        [
            cast(cp.Constraint, cp.abs(target) <= cp.Constant(caps.asset_caps)),
            cast(cp.Constraint, gross <= caps.gross_cap),
            cast(cp.Constraint, cp.sum(target) <= caps.net_cap),  # pyright: ignore[reportUnknownMemberType]
            cast(cp.Constraint, cp.sum(target) >= -caps.net_cap),  # pyright: ignore[reportUnknownMemberType]
            cast(
                cp.Constraint,
                _cp_quad_form(target, cp.Constant(inputs.risk.covariance))
                <= caps.portfolio_risk_cap**2,
            ),
        ]
    )
    if caps.concentration_cap < 1:
        for index in range(n):
            constraints.append(
                cast(cp.Constraint, cp.abs(target[index]) <= caps.concentration_cap * gross)
            )
    for row, cap in zip(
        inputs.risk.group_exposure_matrix,
        inputs.risk.group_caps,
        strict=True,
    ):
        constraints.append(cast(cp.Constraint, cp.abs(row @ target) <= cap))
    for row, cap in zip(
        inputs.risk.currency_exposure_matrix,
        inputs.risk.currency_caps,
        strict=True,
    ):
        constraints.append(cast(cp.Constraint, cp.abs(row @ target) <= cap))
    for index, (current_value, alpha_value) in enumerate(zip(current, alpha, strict=True)):
        if alpha_value == 0:
            if current_value == 0:
                constraints.append(cast(cp.Constraint, target[index] == 0))
            elif current_value > 0:
                constraints.extend(
                    [
                        cast(cp.Constraint, target[index] >= 0),
                        cast(cp.Constraint, target[index] <= current_value),
                    ]
                )
            else:
                constraints.extend(
                    [
                        cast(cp.Constraint, target[index] <= 0),
                        cast(cp.Constraint, target[index] >= current_value),
                    ]
                )
    cost_terms: list[cp.Expression] = []
    for index, asset in enumerate(inputs.asset_order):
        model = inputs.continuous_costs[asset]
        delta_quantity = cp.abs(target[index] - current[index])
        holding_quantity = cp.abs(target[index])
        for component in model.components:
            quantity = (
                delta_quantity if component.basis is CostBasis.PHYSICAL_DELTA else holding_quantity
            )
            cost_terms.append(_piecewise_cost(component, quantity))
    objective = cp.Maximize(
        float(inputs.gross_sleeve_value) * cp.sum(cp.multiply(alpha, target))  # pyright: ignore[reportUnknownMemberType]
        - cp.sum(cost_terms)  # pyright: ignore[reportUnknownMemberType]
    )
    problem = cp.Problem(objective, constraints)
    try:
        problem.solve(  # pyright: ignore[reportUnknownMemberType]
            solver=inputs.solver_policy.backend,
            warm_start=inputs.solver_policy.warm_start,
            tol_gap_abs=float(inputs.solver_policy.absolute_tolerance),
            tol_gap_rel=float(inputs.solver_policy.relative_tolerance),
            tol_feas=float(inputs.solver_policy.absolute_tolerance),
            max_iter=inputs.solver_policy.max_iterations,
            verbose=False,
        )
    except Exception:
        return SolverResultStatus.ERROR.value, None
    if target.value is None:
        return SolverResultStatus.ERROR.value, None
    values = cast(Sequence[float], target.value)
    return str(problem.status), tuple(float(value) for value in values)


def _normalise_target(
    values: Sequence[float],
    inputs: ContinuousTargetInputs,
) -> tuple[Decimal, ...]:
    tolerance = max(
        inputs.solver_policy.absolute_tolerance,
        inputs.solver_policy.relative_tolerance,
    )
    result: list[Decimal] = []
    for value, current, requested in zip(
        values,
        inputs.current_position,
        inputs.requested_target,
        strict=True,
    ):
        candidate = Decimal(str(value))
        if not candidate.is_finite():
            raise ValueError("solver target is non-finite")
        scale = max(Decimal(1), abs(current), abs(requested))
        threshold = tolerance * scale
        if abs(candidate) <= threshold:
            candidate = Decimal(0)
        elif abs(candidate - current) <= threshold:
            candidate = current
        elif abs(candidate - requested) <= threshold:
            candidate = requested
        result.append(candidate)
    return tuple(result)


def _evaluate_costs(
    target: tuple[Decimal, ...],
    inputs: ContinuousTargetInputs,
) -> tuple[dict[str, Any], Decimal, Decimal]:
    states: dict[str, Any] = {}
    total = Decimal(0)
    financing = Decimal(0)
    for index, asset in enumerate(inputs.asset_order):
        internal_cross_quantity = next(
            item.internal_cross_quantity for item in inputs.netting.assets if item.asset_id == asset
        )
        state = inputs.continuous_costs[asset].evaluate(
            current_quantity=inputs.current_position[index],
            target_quantity=target[index],
            decision_time=inputs.decision_time,
            internal_cross_quantity=internal_cross_quantity,
        )
        if (
            state.current_quantity != inputs.current_position[index]
            or state.target_quantity != target[index]
            or state.decision_time != inputs.decision_time
            or state.holding_interval != inputs.risk.horizon
            or not state.complete
        ):
            raise ValueError("continuous model returned a mismatched or incomplete point state")
        expected = state.require_total_reporting()
        financing_component = next(
            component
            for component in state.components
            if component.component is CostComponentKind.FINANCING
        )
        financing_amount = financing_component.reporting_amount
        if financing_amount is None:
            raise ValueError("financing cost reporting amount is required")
        states[asset] = state
        total += expected
        financing += financing_amount
    return states, total - financing, financing


def _blocked(
    inputs: ContinuousTargetInputs,
    *,
    status: str,
    reasons: tuple[str, ...],
) -> ContinuousTarget:
    return ContinuousTarget(
        source_class=inputs.source_class,
        evidence_purpose=inputs.evidence_purpose,
        asset_order=inputs.asset_order,
        current_position=inputs.current_position,
        target_position=(),
        physical_delta=(),
        expected_cost_reporting=Decimal("0"),
        expected_financing_reporting=Decimal("0"),
        solver_status=status,
        feasibility_residual=Decimal("0"),
        solver_policy_identity=inputs.solver_policy.semantic_identity,
        decision_input_identity=inputs.decision_input_identity,
        expected_costs={},
        disposition=DecisionDisposition.BLOCKED,
        reason_codes=reasons,
        requested_position=inputs.requested_target,
        decision_time=inputs.decision_time,
        cost_model_identities={
            asset: inputs.continuous_costs[asset].semantic_identity for asset in inputs.asset_order
        },
        reporting_currencies={
            asset: inputs.continuous_costs[asset].reporting_currency for asset in inputs.asset_order
        },
        netting=inputs.netting,
    )


def solve_continuous_target(
    inputs: ContinuousTargetInputs,
    *,
    runner: SolverRunner | None = None,
) -> ContinuousTarget:
    """Solve and independently validate one continuous physical target."""
    solve = runner or _run_cvxpy
    try:
        status, values = solve(inputs)
    except (ArithmeticError, RuntimeError, TypeError, ValueError):
        return _blocked(inputs, status=SolverResultStatus.ERROR.value, reasons=("SOLVER_ERROR",))
    normalised_status = status.lower()
    if normalised_status != SolverResultStatus.OPTIMAL.value:
        reason = (
            "SOLVER_INACCURATE"
            if "inaccurate" in normalised_status
            else "SOLVER_INFEASIBLE"
            if "infeasible" in normalised_status
            else "SOLVER_ERROR"
            if normalised_status in {"error", "solver_error"}
            else "SOLVER_NON_OPTIMAL"
        )
        return _blocked(inputs, status=status, reasons=(reason,))
    if values is None or len(values) != len(inputs.asset_order):
        return _blocked(inputs, status=status, reasons=("SOLVER_RESULT_INVALID",))
    try:
        target = _normalise_target(values, inputs)
        feasible, residual, reasons = independent_continuous_feasibility(target, inputs)
        if not feasible:
            return _blocked(inputs, status=status, reasons=("SOLVER_RESULT_INVALID", *reasons))
        states, expected_total, expected_financing = _evaluate_costs(target, inputs)
    except (ArithmeticError, RuntimeError, TypeError, ValueError, OverflowError):
        return _blocked(inputs, status=status, reasons=("SOLVER_RESULT_INVALID",))
    delta = tuple(
        target_value - current
        for target_value, current in zip(target, inputs.current_position, strict=True)
    )
    return ContinuousTarget(
        source_class=inputs.source_class,
        evidence_purpose=inputs.evidence_purpose,
        asset_order=inputs.asset_order,
        current_position=inputs.current_position,
        target_position=target,
        physical_delta=delta,
        expected_cost_reporting=expected_total,
        expected_financing_reporting=expected_financing,
        solver_status=SolverResultStatus.OPTIMAL.value,
        feasibility_residual=residual,
        solver_policy_identity=inputs.solver_policy.semantic_identity,
        decision_input_identity=inputs.decision_input_identity,
        expected_costs=states,
        requested_position=inputs.requested_target,
        decision_time=inputs.decision_time,
        cost_model_identities={
            asset: inputs.continuous_costs[asset].semantic_identity for asset in inputs.asset_order
        },
        reporting_currencies={
            asset: inputs.continuous_costs[asset].reporting_currency for asset in inputs.asset_order
        },
        netting=inputs.netting,
    )


construct_continuous_target = solve_continuous_target
