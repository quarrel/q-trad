"""Focused R3.C one-horizon virtual/physical target checks."""

from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from qtrad.application.r3_portfolio import solve_continuous_target
from qtrad.domain.economics import (
    DEFAULT_SOLVER_POLICY,
    ComponentCost,
    ContinuousCostComponent,
    ContinuousCostModel,
    CostBasis,
    CostComponentKind,
    CostSchedule,
    ExpectedCostState,
    GrossForecast,
    ImpactDisposition,
    InputStatus,
    ProductEconomics,
    SessionState,
)
from qtrad.domain.portfolio import (
    ONE_HORIZON,
    AssetNetting,
    ContinuousTargetInputs,
    DecisionDisposition,
    NettingResult,
    SleeveAttribution,
    SleeveKey,
    VirtualPosition,
    VirtualPositionTransition,
    construct_horizon_intent,
    independent_continuous_feasibility,
    match_internal_opposing_changes,
    replay_virtual_transitions,
)
from qtrad.domain.risk import (
    ExposureMapping,
    RiskCaps,
    RiskEstimatorConfig,
    RiskObservation,
    estimate_ordered_risk_state,
)

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)
ASSETS = ("asset:a", "asset:b")


def _economics(asset: str) -> ProductEconomics:
    zero_delta = CostSchedule.documented_zero(
        currency="AUD",
        basis=CostBasis.PHYSICAL_DELTA,
        version="commission-v1",
        provenance="fixture",
    )
    zero_holding = CostSchedule.documented_zero(
        currency="AUD",
        basis=CostBasis.PHYSICAL_HOLDING,
        version="financing-v1",
        provenance="fixture",
    )
    return ProductEconomics(
        asset_id=asset,
        source_class="FIXTURE",
        source_product_id=f"product:{asset}",
        price_currency="AUD",
        settlement_currency="AUD",
        reporting_currency="AUD",
        contract_size=Decimal("1"),
        value_per_price_unit=Decimal("1"),
        minimum_quantity=Decimal("1"),
        quantity_increment=Decimal("1"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        commission=zero_delta,
        financing=zero_holding,
        impact_disposition=ImpactDisposition.SUPPORTED_MODEL,
        session_state=SessionState.ELIGIBLE,
        session_version="session-v1",
        effective_from=NOW - timedelta(hours=1),
        observed_at=NOW - timedelta(minutes=1),
        economics_max_age=timedelta(hours=1),
        version="economics-v1",
        provenance="fixture",
        fx_price_to_settlement=None,
        fx_settlement_to_reporting=None,
        impact_version="impact-v1",
    )


def _cost_state(
    *, current: str = "0", target: str = "2", unit_amount: str = "1"
) -> ExpectedCostState:
    delta = abs(Decimal(target) - Decimal(current))
    components: list[ComponentCost] = []
    for component in CostComponentKind:
        if component is CostComponentKind.FINANCING:
            components.append(
                ComponentCost.supported(
                    component=component,
                    basis=CostBasis.PHYSICAL_HOLDING,
                    native_amount=Decimal(target),
                    native_currency="AUD",
                    reporting_amount=Decimal(target),
                    reporting_currency="AUD",
                    conversion_rate=Decimal("1"),
                    conversion_source="fixture-fx",
                    conversion_version="fx-v1",
                    holding_interval=ONE_HORIZON,
                    version="component-v1",
                    provenance="fixture",
                )
            )
        else:
            components.append(
                ComponentCost.supported(
                    component=component,
                    basis=CostBasis.PHYSICAL_DELTA,
                    native_amount=Decimal(unit_amount) * delta,
                    native_currency="AUD",
                    reporting_amount=Decimal(unit_amount) * delta,
                    reporting_currency="AUD",
                    conversion_rate=Decimal("1"),
                    conversion_source="fixture-fx",
                    conversion_version="fx-v1",
                    quantity_basis=delta,
                    version="component-v1",
                    provenance="fixture",
                )
            )
    return ExpectedCostState(
        decision_time=NOW,
        current_quantity=Decimal(current),
        target_quantity=Decimal(target),
        holding_interval=ONE_HORIZON,
        components=tuple(components),
        reporting_currency="AUD",
        version="cost-v1",
        provenance="fixture",
    )


def _continuous_model(asset: str, unit_amount: str = "1") -> ContinuousCostModel:
    economics = _economics(asset)
    components = tuple(
        ContinuousCostComponent(
            component=component,
            basis=(
                CostBasis.PHYSICAL_HOLDING
                if component is CostComponentKind.FINANCING
                else CostBasis.PHYSICAL_DELTA
            ),
            native_currency="AUD",
            reporting_currency="AUD",
            conversion_rate=Decimal("1"),
            conversion_source="fixture-fx",
            conversion_version="fx-v1",
            slopes=(
                (
                    Decimal("1")
                    if component is CostComponentKind.FINANCING
                    else Decimal(unit_amount),
                )
            ),
            breakpoints=(),
            form="LINEAR",
            version=(
                economics.commission.version
                if component is CostComponentKind.COMMISSION
                else economics.financing.version
                if component is CostComponentKind.FINANCING
                else economics.impact_version or ""
                if component is CostComponentKind.IMPACT
                else "continuous-v1"
            ),
            provenance="fixture",
            status=InputStatus.AVAILABLE,
        )
        for component in CostComponentKind
    )
    return ContinuousCostModel(
        asset_id=asset,
        horizon=ONE_HORIZON,
        reporting_currency="AUD",
        components=components,
        economics_identity=economics.semantic_identity,
        commission_version=economics.commission.version,
        commission_provenance=economics.commission.provenance,
        financing_version=economics.financing.version,
        financing_provenance=economics.financing.provenance,
        impact_version=economics.impact_version or "",
        impact_disposition=economics.impact_disposition,
        impact_status=InputStatus.AVAILABLE,
        version="continuous-model-v1",
        provenance="fixture",
    )


def _risk_state():
    config = RiskEstimatorConfig(
        horizon=ONE_HORIZON,
        lookback=timedelta(hours=1),
        maximum_age=timedelta(hours=1),
        minimum_observations=2,
    )
    caps = RiskCaps(
        asset_caps=(10.0, 10.0),
        gross_cap=20.0,
        net_cap=20.0,
        concentration_cap=1.0,
        portfolio_risk_cap=20.0,
    )
    return estimate_ordered_risk_state(
        asset_order=ASSETS,
        observations=(
            RiskObservation(NOW - timedelta(minutes=2), (0.01, -0.01)),
            RiskObservation(NOW - timedelta(minutes=1), (-0.01, 0.01)),
            RiskObservation(NOW, (0.02, -0.02)),
        ),
        as_of=NOW,
        observation_cutoff=NOW,
        config=config,
        exposure_mapping=ExposureMapping(),
        caps=caps,
        provenance="fixture-risk",
    )


def _target_inputs(
    *,
    alpha: tuple[Decimal, Decimal] = (Decimal("1"), Decimal("0")),
    requested_target: tuple[Decimal, Decimal] = (Decimal("1"), Decimal("0")),
    unit_amount: str = "1",
    netting: NettingResult | None = None,
) -> ContinuousTargetInputs:
    return ContinuousTargetInputs(
        asset_order=ASSETS,
        current_position=(Decimal("0"), Decimal("0")),
        requested_target=requested_target,
        alpha_return=alpha,
        gross_sleeve_value=Decimal("100"),
        decision_time=NOW,
        economics={asset: _economics(asset) for asset in ASSETS},
        continuous_costs={asset: _continuous_model(asset, unit_amount) for asset in ASSETS},
        expected_costs={
            asset: _cost_state(target=str(requested_target[index]), unit_amount=unit_amount)
            for index, asset in enumerate(ASSETS)
        },
        risk=_risk_state(),
        solver_policy=DEFAULT_SOLVER_POLICY,
        netting=netting,
    )


def _key(asset: str, *, source: str = "SRC") -> SleeveKey:
    return SleeveKey(source, "experiment", "config", asset)


def _forecast(value: str, model: str = "model") -> GrossForecast:
    return GrossForecast(Decimal(value), ONE_HORIZON, "RETURN", model)


def test_intents_net_opposing_changes_and_replay_are_ordered() -> None:
    first = VirtualPosition(_key("asset:a", source="A"), Decimal("0"))
    second = VirtualPosition(_key("asset:a", source="B"), Decimal("2"))
    intent_a = construct_horizon_intent(
        position=first,
        gross_forecast=_forecast("1"),
        requested_quantity=Decimal("2"),
        gross_sleeve_value=Decimal("100"),
        decision_time=NOW,
        expiry_time=NOW + ONE_HORIZON,
    )
    intent_b = construct_horizon_intent(
        position=second,
        gross_forecast=_forecast("-1"),
        requested_quantity=Decimal("0"),
        gross_sleeve_value=Decimal("100"),
        decision_time=NOW,
        expiry_time=NOW + ONE_HORIZON,
    )
    netted = match_internal_opposing_changes((intent_b, intent_a))
    assert netted.asset_order == ("asset:a",)
    assert netted.external_deltas == (Decimal("0"),)
    assert netted.internal_cross_quantity == Decimal("2")
    assert sum(item.requested_delta for item in netted.sleeves) == Decimal("0")

    transitions = (
        VirtualPositionTransition(
            key=intent_b.key,
            prior_quantity=Decimal("2"),
            next_quantity=Decimal("0"),
            gross_forecast=_forecast("-1"),
            decision_time=NOW,
            expiry_time=NOW + ONE_HORIZON,
            model_identity="model",
            risk_policy_identity="risk",
            cost_policy_identity="cost",
            prior_state_identity=second.state_identity,
            successor_state_identity=VirtualPosition(intent_b.key, Decimal("0")).state_identity,
            internal_cross_quantity=Decimal("2"),
        ),
        VirtualPositionTransition(
            key=intent_a.key,
            prior_quantity=Decimal("0"),
            next_quantity=Decimal("2"),
            gross_forecast=_forecast("1"),
            decision_time=NOW,
            expiry_time=NOW + ONE_HORIZON,
            model_identity="model",
            risk_policy_identity="risk",
            cost_policy_identity="cost",
            prior_state_identity=first.state_identity,
            successor_state_identity=VirtualPosition(intent_a.key, Decimal("2")).state_identity,
            internal_cross_quantity=Decimal("2"),
        ),
    )
    initial = {intent_a.key: first, intent_b.key: second}
    replay = replay_virtual_transitions(initial, transitions)
    reversed_replay = replay_virtual_transitions(initial, tuple(reversed(transitions)))
    assert replay.transition_count == 2
    assert replay.semantic_identity == reversed_replay.semantic_identity


def test_zero_forecast_cannot_open_increase_or_reverse() -> None:
    position = VirtualPosition(_key("asset:a"), Decimal("1"))
    intent = construct_horizon_intent(
        position=position,
        gross_forecast=_forecast("0"),
        requested_quantity=Decimal("-2"),
        gross_sleeve_value=Decimal("100"),
        decision_time=NOW,
        expiry_time=NOW + ONE_HORIZON,
    )
    assert intent.requested_quantity == Decimal("1")
    assert "ZERO_FORECAST_NEW_EXPOSURE_BLOCKED" in intent.reason_codes


def test_target_costs_once_and_reconciles_delta() -> None:
    inputs = _target_inputs()
    target = solve_continuous_target(inputs, runner=lambda inputs: ("optimal", (1.0, 0.0)))
    assert target.disposition is DecisionDisposition.ACCEPTED
    assert target.target_position == (Decimal("1"), Decimal("0"))
    assert target.physical_delta == (Decimal("1"), Decimal("0"))
    assert target.expected_cost_reporting == Decimal("5")
    assert target.expected_financing_reporting == Decimal("1")
    feasible, residual, reasons = independent_continuous_feasibility(target.target_position, inputs)
    assert feasible and residual == Decimal("0") and not reasons


@pytest.mark.parametrize("status", ["inaccurate", "infeasible", "max_iters"])
def test_solver_failure_has_no_partial_target(status: str) -> None:
    target = solve_continuous_target(_target_inputs(), runner=lambda inputs: (status, (1.0, 0.0)))
    assert target.disposition is DecisionDisposition.BLOCKED
    assert target.target_position == ()
    assert target.physical_delta == ()


def test_zero_forecast_target_is_flat_and_missing_inputs_fail_closed() -> None:
    inputs = _target_inputs(
        alpha=(Decimal("0"), Decimal("0")),
        requested_target=(Decimal("0"), Decimal("0")),
    )
    target = solve_continuous_target(inputs, runner=lambda inputs: ("optimal", (0.0, 0.0)))
    assert target.disposition is DecisionDisposition.ACCEPTED
    assert target.target_position == (Decimal("0"), Decimal("0"))
    with pytest.raises(ValueError, match="expected-cost"):
        ContinuousTargetInputs(
            asset_order=ASSETS,
            current_position=(Decimal("0"), Decimal("0")),
            requested_target=(Decimal("1"), Decimal("0")),
            alpha_return=(Decimal("1"), Decimal("0")),
            gross_sleeve_value=Decimal("100"),
            decision_time=NOW,
            economics={asset: _economics(asset) for asset in ASSETS},
            continuous_costs={asset: _continuous_model(asset) for asset in ASSETS},
            expected_costs={"asset:a": _cost_state()},
            risk=_risk_state(),
            solver_policy=DEFAULT_SOLVER_POLICY,
        )


def test_transition_binds_state_identity_and_zero_forecast() -> None:
    key = _key("asset:a")
    prior = VirtualPosition(key, Decimal("1"))
    successor = VirtualPosition(key, Decimal("1"))
    identity_mismatch = VirtualPositionTransition(
        key=key,
        prior_quantity=Decimal("1"),
        next_quantity=Decimal("1"),
        gross_forecast=_forecast("1"),
        decision_time=NOW,
        expiry_time=NOW + ONE_HORIZON,
        model_identity="model",
        risk_policy_identity="risk",
        cost_policy_identity="cost",
        prior_state_identity="0" * 64,
        successor_state_identity=successor.state_identity,
    )
    with pytest.raises(ValueError, match="prior state identity"):
        replay_virtual_transitions({key: prior}, (identity_mismatch,))
    with pytest.raises(ValueError, match="zero forecast"):
        VirtualPositionTransition(
            key=key,
            prior_quantity=Decimal("1"),
            next_quantity=Decimal("1.0000000000000000001"),
            gross_forecast=_forecast("0"),
            decision_time=NOW,
            expiry_time=NOW + ONE_HORIZON,
            model_identity="model",
            risk_policy_identity="risk",
            cost_policy_identity="cost",
            prior_state_identity=prior.state_identity,
            successor_state_identity=VirtualPosition(
                key, Decimal("1.0000000000000000001")
            ).state_identity,
        )


def test_native_target_not_bound_to_requested_cost_state() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1"), Decimal("0")))
    accepted = solve_continuous_target(inputs, runner=lambda inputs: ("optimal", (2.0, 0.0)))
    assert accepted.disposition is DecisionDisposition.ACCEPTED
    assert accepted.target_position == (Decimal("2"), Decimal("0"))
    assert accepted.expected_costs["asset:a"].target_quantity == Decimal("2")
    assert accepted.expected_costs["asset:a"].current_quantity == Decimal("0")


def test_decimal_component_money_is_reconciled_without_float_artifact() -> None:
    inputs = _target_inputs(
        requested_target=(Decimal("3"), Decimal("0")),
        unit_amount="0.10",
    )
    target = solve_continuous_target(inputs, runner=lambda inputs: ("optimal", (3.0, 0.0)))
    assert target.disposition is DecisionDisposition.ACCEPTED
    assert target.expected_cost_reporting == Decimal("1.50")
    assert target.expected_financing_reporting == Decimal("3")


def test_virtual_position_rejects_non_intrinsic_identity() -> None:
    key = _key("EURUSD")
    with pytest.raises(ValueError, match="does not match"):
        VirtualPosition(key, Decimal("1"), "0" * 64)


def test_blocked_transition_cannot_change_position() -> None:
    key = _key("EURUSD")
    with pytest.raises(ValueError, match="blocked transition"):
        VirtualPositionTransition(
            key=key,
            prior_quantity=Decimal("0"),
            next_quantity=Decimal("1"),
            gross_forecast=_forecast("1"),
            decision_time=NOW,
            expiry_time=NOW + ONE_HORIZON,
            model_identity="model",
            risk_policy_identity="risk",
            cost_policy_identity="cost",
            prior_state_identity=VirtualPosition(key, Decimal("0")).state_identity,
            successor_state_identity=VirtualPosition(key, Decimal("1")).state_identity,
            external_delta_share=Decimal("1"),
            disposition=DecisionDisposition.BLOCKED,
            reason_codes=("blocked",),
        )


def test_netting_cross_is_bound_to_final_cost_states() -> None:
    attribution_a = (
        SleeveAttribution(_key("asset:a", source="A"), Decimal("2"), Decimal("2"), Decimal("0")),
        SleeveAttribution(_key("asset:a", source="B"), Decimal("2"), Decimal("0"), Decimal("2")),
    )
    netting = NettingResult(
        assets=(
            AssetNetting("asset:a", Decimal("2"), Decimal("2"), Decimal("1"), attribution_a),
            AssetNetting("asset:b", Decimal("0"), Decimal("0"), Decimal("0"), ()),
        ),
        sleeves=attribution_a,
    )
    target = solve_continuous_target(
        _target_inputs(
            netting=netting,
            requested_target=(Decimal("2"), Decimal("0")),
            alpha=(Decimal("1"), Decimal("0")),
        ),
        runner=lambda inputs: ("optimal", (1.0, 0.0)),
    )
    assert target.disposition is DecisionDisposition.ACCEPTED
    assert target.netting == netting
    assert target.expected_costs["asset:a"].internal_cross_quantity == Decimal("1")
    assert target.expected_costs["asset:a"].components[0].quantity_basis == Decimal("1")


def test_target_identity_binds_model_and_component_semantics() -> None:
    target = solve_continuous_target(
        _target_inputs(),
        runner=lambda inputs: ("optimal", (1.0, 0.0)),
    )
    assert target.disposition is DecisionDisposition.ACCEPTED
    changed_model = replace(
        _target_inputs().continuous_costs["asset:a"],
        provenance="different-provenance",
    )
    malformed = replace(
        target,
        cost_model_identities={
            "asset:a": changed_model.semantic_identity,
            "asset:b": target.cost_model_identities["asset:b"],
        },
    )
    assert malformed.semantic_identity != target.semantic_identity


def test_target_rejects_mismatched_final_cost_state() -> None:
    inputs = _target_inputs()
    target = solve_continuous_target(
        inputs,
        runner=lambda inputs: ("optimal", (1.0, 0.0)),
    )
    alternate = inputs.continuous_costs["asset:a"].evaluate(
        current_quantity=Decimal("0"),
        target_quantity=Decimal("2"),
        decision_time=NOW,
    )
    costs = dict(target.expected_costs)
    costs["asset:a"] = alternate
    with pytest.raises(ValueError, match="binding mismatch"):
        replace(target, expected_costs=costs)


def test_positive_minimum_cost_is_rejected_before_solver() -> None:
    economics = _economics("asset:a")
    commission = CostSchedule.available(
        currency="AUD",
        per_quantity=Decimal("1"),
        minimum=Decimal("5"),
        basis=CostBasis.PHYSICAL_DELTA,
        version="commission-v2",
        provenance="fixture",
    )
    with pytest.raises(ValueError, match="minimum"):
        ContinuousTargetInputs(
            asset_order=ASSETS,
            current_position=(Decimal("0"), Decimal("0")),
            requested_target=(Decimal("1"), Decimal("0")),
            alpha_return=(Decimal("1"), Decimal("0")),
            gross_sleeve_value=Decimal("100"),
            decision_time=NOW,
            economics={
                "asset:a": replace(economics, commission=commission),
                "asset:b": _economics("asset:b"),
            },
            continuous_costs={asset: _continuous_model(asset) for asset in ASSETS},
            risk=_risk_state(),
            solver_policy=DEFAULT_SOLVER_POLICY,
        )


def test_accepted_target_snapshots_required_mappings() -> None:
    target = solve_continuous_target(
        _target_inputs(),
        runner=lambda inputs: ("optimal", (1.0, 0.0)),
    )
    identity = target.semantic_identity
    for mapping in (
        target.expected_costs,
        target.cost_model_identities,
        target.reporting_currencies,
    ):
        with pytest.raises(AttributeError):
            mapping.clear()  # type: ignore[attr-defined]
    assert target.semantic_identity == identity
    assert target.expected_costs["asset:a"].target_quantity == Decimal("1")


def test_accepted_target_rejects_netting_external_delta_mismatch() -> None:
    target = solve_continuous_target(
        _target_inputs(),
        runner=lambda inputs: ("optimal", (1.0, 0.0)),
    )
    mismatch_attribution = SleeveAttribution(
        _key("asset:a", source="MISMATCH"), Decimal("2"), Decimal("0"), Decimal("2")
    )
    mismatched = NettingResult(
        assets=(
            AssetNetting(
                "asset:a", Decimal("2"), Decimal("2"), Decimal("0"), (mismatch_attribution,)
            ),
            AssetNetting("asset:b", Decimal("0"), Decimal("0"), Decimal("0"), ()),
        ),
        sleeves=(mismatch_attribution,),
    )
    with pytest.raises(ValueError, match="external deltas"):
        replace(target, netting=mismatched)


def test_continuous_inputs_snapshot_authorities_and_reject_mutation() -> None:
    inputs = _target_inputs()
    with pytest.raises(TypeError):
        cast(MutableMapping[str, ProductEconomics], inputs.economics)["asset:a"] = _economics(
            "replacement"
        )
    with pytest.raises(TypeError):
        cast(MutableMapping[str, ContinuousCostModel], inputs.continuous_costs)["asset:a"] = (
            _continuous_model("replacement")
        )
    with pytest.raises(TypeError):
        cast(MutableMapping[str, ExpectedCostState], inputs.expected_costs)["asset:a"] = (
            _cost_state()
        )
    component = inputs.continuous_costs["asset:a"].components[0]
    with pytest.raises(ValueError, match="tuples"):
        replace(component, slopes=[Decimal("1")])


def test_continuous_target_snapshots_maps_and_binds_input_identity() -> None:
    target = solve_continuous_target(
        _target_inputs(), runner=lambda inputs: ("optimal", (1.0, 0.0))
    )
    with pytest.raises(TypeError):
        cast(MutableMapping[str, str], target.cost_model_identities)["asset:a"] = "0" * 64
    with pytest.raises(TypeError):
        cast(MutableMapping[str, str], target.reporting_currencies)["asset:a"] = "USD"
    with pytest.raises(ValueError, match="SHA-256"):
        replace(target, decision_input_identity="A" * 64)


def test_blocked_target_rejects_partial_output_and_missing_reason() -> None:
    blocked = solve_continuous_target(
        _target_inputs(), runner=lambda inputs: ("infeasible", (1.0, 0.0))
    )
    with pytest.raises(ValueError, match="reason codes"):
        replace(blocked, reason_codes=())
    with pytest.raises(ValueError, match="partial output"):
        replace(blocked, target_position=(Decimal("1"),))


@pytest.mark.parametrize("field", ["variable_order", "accepted_statuses"])
def test_solver_policy_rejects_mutable_identity_sequences(field: str) -> None:
    values = list(getattr(DEFAULT_SOLVER_POLICY, field))
    with pytest.raises(ValueError, match="tuples"):
        replace(
            DEFAULT_SOLVER_POLICY,
            **{field: cast(tuple[str, ...], values)},
        )


@pytest.mark.parametrize("field", ["variable_order", "accepted_statuses"])
def test_solver_policy_rejects_nested_mutable_sequence(field: str) -> None:
    nested = (
        ("physical_target", ["nested"]) if field == "variable_order" else ("optimal", ["nested"])
    )
    with pytest.raises(ValueError, match="elements"):
        replace(DEFAULT_SOLVER_POLICY, **{field: cast(tuple[str, ...], nested)})


def test_solver_policy_is_immutable_and_decision_identity_is_stable() -> None:
    inputs = _target_inputs()
    identity = inputs.decision_input_identity
    with pytest.raises(FrozenInstanceError):
        setattr(inputs.solver_policy, "variable_order", ("mutated",))  # noqa: B010
    assert inputs.solver_policy.variable_order == ("physical_target",)
    assert inputs.decision_input_identity == identity


@pytest.mark.parametrize(
    ("component_kind", "invalid_basis"),
    [
        (CostComponentKind.COMMISSION, CostBasis.PHYSICAL_HOLDING),
        (CostComponentKind.FINANCING, CostBasis.PHYSICAL_DELTA),
    ],
)
def test_continuous_model_rejects_schedule_basis_mismatch(
    component_kind: CostComponentKind,
    invalid_basis: CostBasis,
) -> None:
    model = _continuous_model("asset:a")
    components = tuple(
        replace(component, basis=invalid_basis)
        if component.component is component_kind
        else component
        for component in model.components
    )
    with pytest.raises(ValueError, match="basis"):
        replace(model, components=components)
