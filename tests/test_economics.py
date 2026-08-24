from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrad.domain.economics import (
    DEFAULT_SOLVER_POLICY,
    ComponentCost,
    CostBasis,
    CostComponentKind,
    CostSchedule,
    ExpectedCostState,
    FXRate,
    GrossForecast,
    ImpactDisposition,
    InputStatus,
    ProductEconomics,
    SessionState,
    derive_expected_net,
)

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _economics(
    *,
    commission: CostSchedule | None = None,
    financing: CostSchedule | None = None,
    fx: FXRate | None = None,
    impact: ImpactDisposition = ImpactDisposition.SUPPORTED_MODEL,
    impact_max_quantity: Decimal | None = None,
    impact_version: str | None = "impact-v1",
    impact_reason: str | None = None,
) -> ProductEconomics:
    return ProductEconomics(
        asset_id="asset:test",
        source_class="FIXTURE",
        source_product_id="product:test",
        price_currency="AUD",
        settlement_currency="AUD",
        reporting_currency="AUD",
        contract_size=Decimal("1"),
        value_per_price_unit=Decimal("1"),
        minimum_quantity=Decimal("1"),
        quantity_increment=Decimal("1"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        commission=commission
        or CostSchedule.documented_zero(
            currency="AUD", basis="physical-delta", version="commission-v1", provenance="fixture"
        ),
        financing=financing
        or CostSchedule.documented_zero(
            currency="AUD", basis="physical-holding", version="financing-v1", provenance="fixture"
        ),
        impact_disposition=impact,
        session_state=SessionState.ELIGIBLE,
        session_version="session-v1",
        effective_from=NOW - timedelta(hours=1),
        observed_at=NOW - timedelta(minutes=1),
        version="economics-v1",
        provenance="fixture",
        fx_to_reporting=fx,
        impact_version=impact_version,
        impact_max_quantity=impact_max_quantity,
        impact_reason=impact_reason,
    )


def _cost_state(
    *, complete: bool = True, current: str = "0", target: str = "2"
) -> ExpectedCostState:
    delta = abs(Decimal(target) - Decimal(current))
    components = []
    for kind in (
        CostComponentKind.SPREAD,
        CostComponentKind.LATENCY_MOVEMENT,
        CostComponentKind.ADVERSE_SLIPPAGE,
        CostComponentKind.COMMISSION,
        CostComponentKind.FINANCING,
        CostComponentKind.IMPACT,
    ):
        if kind is CostComponentKind.FINANCING:
            if complete:
                components.append(
                    ComponentCost.supported(
                        component=kind,
                        basis=CostBasis.PHYSICAL_HOLDING,
                        native_amount=Decimal("1"),
                        native_currency="AUD",
                        reporting_amount=Decimal("1"),
                        reporting_currency="AUD",
                        holding_interval=timedelta(minutes=15),
                        version="component-v1",
                        provenance="fixture",
                    )
                )
            else:
                components.append(
                    ComponentCost.missing(
                        component=kind,
                        basis=CostBasis.PHYSICAL_HOLDING,
                        reporting_currency="AUD",
                        holding_interval=timedelta(minutes=15),
                        version="component-v1",
                        provenance="fixture",
                        reason="not supplied",
                    )
                )
        elif complete:
            components.append(
                ComponentCost.supported(
                    component=kind,
                    basis=CostBasis.PHYSICAL_DELTA,
                    native_amount=Decimal("1"),
                    native_currency="AUD",
                    reporting_amount=Decimal("1"),
                    reporting_currency="AUD",
                    quantity_basis=delta,
                    version="component-v1",
                    provenance="fixture",
                )
            )
        else:
            components.append(
                ComponentCost.missing(
                    component=kind,
                    basis=CostBasis.PHYSICAL_DELTA,
                    reporting_currency="AUD",
                    quantity_basis=delta,
                    version="component-v1",
                    provenance="fixture",
                    reason="not supplied",
                )
            )
    return ExpectedCostState(
        decision_time=NOW,
        current_quantity=Decimal(current),
        target_quantity=Decimal(target),
        holding_interval=timedelta(minutes=15),
        components=tuple(components),
        reporting_currency="AUD",
        version="cost-v1",
        provenance="fixture",
    )


def test_product_economics_is_fail_closed_for_missing_inputs() -> None:
    economics = _economics(
        commission=CostSchedule.missing(
            basis="physical-delta",
            version="commission-v1",
            provenance="fixture",
            reason="no schedule",
        ),
        impact=ImpactDisposition.UNSUPPORTED_BLOCKING,
        impact_version=None,
        impact_reason="no model",
    )
    result = economics.eligibility(decision_time=NOW)
    assert not result.eligible
    assert "COMMISSION_MISSING" in result.reasons
    assert "IMPACT_UNSUPPORTED" in result.reasons


def test_documented_zero_is_not_missing() -> None:
    result = _economics().eligibility(decision_time=NOW)
    assert result == type(result)(True)


def test_fx_requires_health_and_staleness() -> None:
    fx = FXRate(
        "USD",
        "AUD",
        Decimal("1.5"),
        NOW - timedelta(hours=2),
        timedelta(hours=1),
        InputStatus.AVAILABLE,
        "fixture",
        "fx-v1",
    )
    economics = replace(_economics(), price_currency="USD", fx_to_reporting=fx)
    assert "FX_UNAVAILABLE_OR_STALE" in economics.eligibility(decision_time=NOW).reasons


def test_cost_state_binds_one_physical_delta_and_financing_interval() -> None:
    state = _cost_state()
    assert state.physical_delta == Decimal("2")
    assert state.expected_total_reporting == Decimal("6")
    assert state.internal_cross_quantity == Decimal("0")


def test_expected_net_recomputes_from_gross_and_cost() -> None:
    state = _cost_state()
    net = derive_expected_net(
        gross_forecast=GrossForecast(
            Decimal("0.05"), timedelta(minutes=15), "LOG_RETURN", "model-v1"
        ),
        gross_contribution=Decimal("10"),
        physical_notional=Decimal("100"),
        expected_cost=state,
    )
    assert net.expected_net_contribution == Decimal("4")
    assert net.expected_net_return == Decimal("-0.01")


def test_incomplete_cost_cannot_be_silently_zero() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        _cost_state(complete=False).require_total_reporting()
    with pytest.raises(ValueError, match="incomplete"):
        derive_expected_net(
            gross_forecast=GrossForecast(
                Decimal("0.05"), timedelta(minutes=15), "LOG_RETURN", "model-v1"
            ),
            gross_contribution=Decimal("10"),
            physical_notional=Decimal("100"),
            expected_cost=_cost_state(complete=False),
        )


def test_solver_policy_is_semantic_and_deterministic() -> None:
    assert DEFAULT_SOLVER_POLICY.backend == "CLARABEL"
    assert DEFAULT_SOLVER_POLICY.backend_version == "0.11.1"
    assert DEFAULT_SOLVER_POLICY.warm_start is False
    assert len(DEFAULT_SOLVER_POLICY.semantic_identity) == 64


def test_capped_impact_requires_quantity_and_caps_it() -> None:
    economics = _economics(
        impact=ImpactDisposition.CAPPED_NO_IMPACT_RANGE,
        impact_max_quantity=Decimal("2"),
    )
    assert not economics.eligibility(decision_time=NOW).eligible
    assert economics.eligibility(decision_time=NOW, proposed_quantity=Decimal("2")).eligible
    assert "IMPACT_QUANTITY_EXCEEDS_CAP" in economics.eligibility(
        decision_time=NOW, proposed_quantity=Decimal("3")
    ).reasons


def test_solver_policy_rejects_non_optimal_status() -> None:
    with pytest.raises(ValueError, match="only the optimal"):
        replace(DEFAULT_SOLVER_POLICY, accepted_statuses=("optimal", "infeasible"))


def test_expected_cost_rejects_internal_cross_component() -> None:
    components = list(_cost_state().components)
    components[0] = ComponentCost.supported(
        component=CostComponentKind.SPREAD,
        basis=CostBasis.INTERNAL_CROSS,
        native_amount=Decimal("0"),
        native_currency="AUD",
        reporting_amount=Decimal("0"),
        reporting_currency="AUD",
        version="component-v1",
        provenance="fixture",
    )
    with pytest.raises(ValueError, match="physical delta"):
        ExpectedCostState(
            decision_time=NOW,
            current_quantity=Decimal("0"),
            target_quantity=Decimal("2"),
            holding_interval=timedelta(minutes=15),
            components=tuple(components),
            reporting_currency="AUD",
            version="cost-v1",
            provenance="fixture",
        )
