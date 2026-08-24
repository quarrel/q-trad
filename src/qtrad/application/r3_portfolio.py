"""CVXPY application seam for the R3.C continuous target kernel."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Protocol, cast

import cvxpy as cp

from qtrad.domain.economics import CostComponentKind
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


def _cp_sum(value: Any) -> cp.Expression:
    result = cp.sum(value)  # pyright: ignore[reportUnknownMemberType]
    return cast(cp.Expression, result)


def _cp_multiply(left: Any, right: Any) -> cp.Expression:
    result = cp.multiply(left, right)  # pyright: ignore[reportUnknownMemberType]
    return cast(cp.Expression, result)


def _cp_quad_form(vector: Any, matrix: Any) -> cp.Expression:
    result = cp.quad_form(vector, matrix, assume_PSD=True)  # pyright: ignore[reportUnknownMemberType]
    return cast(cp.Expression, result)


def _cp_sum_squares(value: Any) -> cp.Expression:
    result = cp.sum_squares(value)  # pyright: ignore[reportUnknownMemberType]
    return cast(cp.Expression, result)


def _cost_rates(inputs: ContinuousTargetInputs) -> tuple[tuple[float, ...], tuple[float, ...]]:
    transaction: list[float] = []
    financing: list[float] = []
    for asset, current, requested in zip(
        inputs.asset_order,
        inputs.current_position,
        inputs.requested_target,
        strict=True,
    ):
        state = inputs.expected_costs[asset]
        total = state.require_total_reporting()
        financing_amount = Decimal("0")
        for component in state.components:
            if component.component is CostComponentKind.FINANCING:
                amount = component.reporting_amount
                if amount is None:
                    raise ValueError("financing cost reporting amount is required")
                financing_amount += amount
        transaction_amount = total - financing_amount
        requested_delta = abs(requested - current)
        transaction_rate = (
            transaction_amount / requested_delta if requested_delta != 0 else Decimal("0")
        )
        holding_base = abs(requested)
        financing_rate = financing_amount / holding_base if holding_base != 0 else Decimal("0")
        transaction.append(float(transaction_rate))
        financing.append(float(financing_rate))
    return tuple(transaction), tuple(financing)


def _run_cvxpy(inputs: ContinuousTargetInputs) -> tuple[str, Sequence[float] | None]:
    n = len(inputs.asset_order)
    target = cp.Variable(n, name=inputs.solver_policy.variable_order[0])
    current = tuple(float(value) for value in inputs.current_position)
    requested = tuple(float(value) for value in inputs.requested_target)
    alpha = tuple(float(value) for value in inputs.alpha_return)
    transaction_rate, financing_rate = _cost_rates(inputs)
    gross = _cp_sum(cp.abs(target))
    constraints: list[cp.Constraint] = []
    caps = inputs.risk.caps
    constraints.extend(
        [
            cast(cp.Constraint, cp.abs(target) <= cp.Constant(caps.asset_caps)),
            cast(cp.Constraint, gross <= caps.gross_cap),
            cast(cp.Constraint, _cp_sum(target) <= caps.net_cap),
            cast(cp.Constraint, _cp_sum(target) >= -caps.net_cap),
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
                cast(
                    cp.Constraint,
                    cp.abs(target[index]) <= caps.concentration_cap * gross,
                )
            )
    if inputs.risk.group_exposure_matrix:
        for row, cap in zip(
            inputs.risk.group_exposure_matrix,
            inputs.risk.group_caps,
            strict=True,
        ):
            constraints.append(cast(cp.Constraint, cp.abs(row @ target) <= cap))
    if inputs.risk.currency_exposure_matrix:
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
                constraints.append(cast(cp.Constraint, target[index] >= 0))
                constraints.append(cast(cp.Constraint, target[index] <= current_value))
            else:
                constraints.append(cast(cp.Constraint, target[index] <= 0))
                constraints.append(cast(cp.Constraint, target[index] >= current_value))
    turnover = _cp_sum(_cp_multiply(transaction_rate, cp.abs(target - current)))
    holding = _cp_sum(_cp_multiply(financing_rate, cp.abs(target)))
    forecast_value = float(inputs.gross_sleeve_value) * _cp_sum(_cp_multiply(alpha, target))
    objective = cp.Maximize(
        forecast_value - turnover - holding - 1e-12 * _cp_sum_squares(target - requested)
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


def _blocked(
    inputs: ContinuousTargetInputs,
    *,
    status: str,
    reasons: tuple[str, ...],
) -> ContinuousTarget:
    return ContinuousTarget(
        asset_order=inputs.asset_order,
        current_position=inputs.current_position,
        target_position=(),
        physical_delta=(),
        expected_cost_reporting=Decimal("0"),
        expected_financing_reporting=Decimal("0"),
        solver_status=status,
        feasibility_residual=Decimal("0"),
        solver_policy_identity=inputs.solver_policy.semantic_identity,
        disposition=DecisionDisposition.BLOCKED,
        reason_codes=reasons,
    )


def solve_continuous_target(
    inputs: ContinuousTargetInputs,
    *,
    runner: SolverRunner | None = None,
) -> ContinuousTarget:
    """Solve one continuous physical target or return an explicit blocked result.

    The runner seam is intentionally small so tests can inject non-optimal,
    inaccurate and infeasible outcomes without touching CVXPY.
    """

    solve = runner or _run_cvxpy
    try:
        status, values = solve(inputs)
    except (ArithmeticError, RuntimeError, TypeError, ValueError):
        return _blocked(inputs, status=SolverResultStatus.ERROR.value, reasons=("SOLVER_ERROR",))
    normalised_status = status.lower()
    if normalised_status != SolverResultStatus.OPTIMAL.value:
        if "inaccurate" in normalised_status:
            reason = "SOLVER_INACCURATE"
        elif "infeasible" in normalised_status:
            reason = "SOLVER_INFEASIBLE"
        elif normalised_status in {"error", "solver_error"}:
            reason = "SOLVER_ERROR"
        else:
            reason = "SOLVER_NON_OPTIMAL"
        return _blocked(inputs, status=status, reasons=(reason,))
    if values is None or len(values) != len(inputs.asset_order):
        return _blocked(inputs, status=status, reasons=("SOLVER_RESULT_INVALID",))
    try:
        target = tuple(Decimal(str(float(value))) for value in values)
        feasible, residual, reasons = independent_continuous_feasibility(target, inputs)
    except (ArithmeticError, RuntimeError, TypeError, ValueError, OverflowError):
        return _blocked(inputs, status=status, reasons=("SOLVER_RESULT_INVALID",))
    if not feasible:
        return _blocked(inputs, status=status, reasons=("SOLVER_RESULT_INVALID", *reasons))
    delta = tuple(
        target_value - current
        for target_value, current in zip(target, inputs.current_position, strict=True)
    )
    expected_total = Decimal("0")
    expected_financing = Decimal("0")
    transaction_rates, financing_rates = _cost_rates(inputs)
    for index, (current, target_value) in enumerate(
        zip(inputs.current_position, target, strict=True)
    ):
        expected_total += Decimal(str(transaction_rates[index])) * abs(target_value - current)
        expected_financing += Decimal(str(financing_rates[index])) * abs(target_value)
    return ContinuousTarget(
        asset_order=inputs.asset_order,
        current_position=inputs.current_position,
        target_position=target,
        physical_delta=delta,
        expected_cost_reporting=expected_total,
        expected_financing_reporting=expected_financing,
        solver_status=status,
        feasibility_residual=residual,
        solver_policy_identity=inputs.solver_policy.semantic_identity,
    )


# Explicit noun-first alias for application callers.
construct_continuous_target = solve_continuous_target
