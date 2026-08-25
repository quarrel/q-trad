"""Fixture-only R3.E vertical slice and create-only persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext, localcontext
from pathlib import Path
from typing import cast

from qtrad.domain.economics import (
    ContinuousCostComponent,
    ContinuousCostModel,
    CostBasis,
    CostComponentKind,
    GrossForecast,
    ImpactDisposition,
    InputStatus,
)
from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.portfolio import (
    CONFIGURED_HORIZONS,
    ONE_HORIZON,
    AssetNetting,
    ContinuousTarget,
    DecisionDisposition,
    HorizonIntent,
    HorizonState,
    NettingResult,
    SleeveKey,
    SolverResultStatus,
    VirtualPosition,
    construct_horizon_intent,
    match_internal_opposing_changes,
)
from qtrad.domain.portfolio import SleeveAttribution as ParentSleeveAttribution
from qtrad.domain.r3_evaluation import (
    DecisionClosure,
    EvaluationReport,
    LifecycleEvent,
    OutcomeClosure,
    QuoteEvidence,
    SleeveAttribution,
    VerificationReceipt,
    build_outcome_closures,
    evaluate_independently,
    identity,
    lifecycle_component_identities,
)

__all__ = (
    "build_fixture_inputs",
    "build_outcome_closures",
    "evaluate_independently",
    "fixture_cli",
    "run_fixture",
)
from qtrad.domain.r3_rounding import (
    ROUNDING_CONTRACT,
    RepairedSleeveAttribution,
    RoundedTarget,
    RoundingDisposition,
    cost_states_identity,
)
from qtrad.domain.risk import RiskCaps, RiskState

_FIXTURE_TIME = datetime(2025, 1, 2, tzinfo=UTC)


def _component(kind: CostComponentKind, basis: CostBasis, slope: str) -> ContinuousCostComponent:
    status = InputStatus.AVAILABLE
    if kind is not CostComponentKind.IMPACT and slope == "0":
        status = InputStatus.DOCUMENTED_ZERO
    return ContinuousCostComponent(
        component=kind,
        basis=basis,
        native_currency="AUD",
        reporting_currency="AUD",
        conversion_rate=Decimal("1"),
        conversion_source="fixture-fx",
        conversion_version="fixture-fx-v1",
        slopes=(Decimal(slope),),
        breakpoints=(),
        form="LINEAR",
        version="fixture-cost-v1",
        provenance="R3.E fixture implementation evidence",
        status=status,
    )


def _model(asset_id: str, horizon: timedelta = ONE_HORIZON) -> ContinuousCostModel:
    components = (
        _component(CostComponentKind.SPREAD, CostBasis.PHYSICAL_DELTA, "0"),
        _component(CostComponentKind.LATENCY_MOVEMENT, CostBasis.PHYSICAL_DELTA, "0"),
        _component(CostComponentKind.ADVERSE_SLIPPAGE, CostBasis.PHYSICAL_DELTA, "0"),
        _component(CostComponentKind.COMMISSION, CostBasis.PHYSICAL_DELTA, "0.01"),
        _component(CostComponentKind.FINANCING, CostBasis.PHYSICAL_HOLDING, "0.001"),
        _component(CostComponentKind.IMPACT, CostBasis.PHYSICAL_DELTA, "0"),
    )
    return ContinuousCostModel(
        asset_id=asset_id,
        horizon=horizon,
        reporting_currency="AUD",
        components=components,
        economics_identity=identity({"contract": "fixture-economics-v1", "asset_id": asset_id}),
        commission_version="fixture-cost-v1",
        commission_provenance="R3.E fixture implementation evidence",
        financing_version="fixture-cost-v1",
        financing_provenance="R3.E fixture implementation evidence",
        impact_version="fixture-cost-v1",
        impact_disposition=ImpactDisposition.SUPPORTED_MODEL,
        impact_status=InputStatus.AVAILABLE,
        version="fixture-model-v1",
        provenance="R3.E fixture implementation evidence",
    )


def _decision_input_receipt(target: RoundedTarget) -> VerificationReceipt:
    semantic = target.decision_input_identity
    parent = identity(
        {"contract": "r3-d-input-root-v1", "asset_order": target.asset_order, "semantic": semantic}
    )
    return VerificationReceipt(
        artefact_contract="qtrad-r3d-decision-input-v1",
        semantic_identity=semantic,
        closure_identity=identity(
            {"contract": "qtrad-r3d-decision-input-closure-v1", "target": target.semantic_identity}
        ),
        parent_verification_identity=parent,
        verifier_contract="r3-d-input-fixture-verifier-v1",
        checks=("canonical-bytes", "input-identity", "create-only"),
    )


def _target_receipt(target: RoundedTarget) -> VerificationReceipt:
    return VerificationReceipt(
        artefact_contract=ROUNDING_CONTRACT,
        semantic_identity=target.semantic_identity,
        closure_identity=target.semantic_identity,
        parent_verification_identity=_decision_input_receipt(target).receipt_identity,
        verifier_contract="r3-d-fixture-verifier-v1",
        checks=("canonical-bytes", "target-reconciliation", "create-only"),
    )


def _risk_state(
    source: MarketDataSourceClass,
    purpose: EvidencePurpose,
    horizon: timedelta = ONE_HORIZON,
    horizon_state_identities: tuple[str, ...] = (),
) -> RiskState:
    caps = RiskCaps(
        asset_caps=(1.0,),
        gross_cap=1.0,
        net_cap=1.0,
        concentration_cap=1.0,
        portfolio_risk_cap=1.0,
        group_caps=(1.0,),
        currency_caps=(1.0,),
    )
    return RiskState(
        asset_order=("ASSET_A",),
        horizon=horizon,
        as_of=_FIXTURE_TIME,
        observation_cutoff=_FIXTURE_TIME,
        lookback=timedelta(days=1),
        maximum_age=timedelta(days=1),
        availability_policy="AVAILABLE_BY_CUTOFF",
        return_unit="LOG_RETURN",
        estimator="LEDOIT_WOLF",
        estimator_version="qtrad-ledoit-wolf-pure-python-v1",
        shrinkage=0.25,
        covariance=((0.04,),),
        sample_count=2,
        raw_observation_count=2,
        missing_observation_count=0,
        excluded_observation_count=0,
        effective_observations=2,
        symmetry_tolerance=1e-12,
        psd_tolerance=1e-12,
        finite_tolerance=0.0,
        group_keys=("fixture-group",),
        group_exposure_matrix=((1.0,),),
        group_caps=(1.0,),
        currency_keys=("AUD",),
        currency_exposure_matrix=((1.0,),),
        currency_caps=(1.0,),
        caps=caps,
        source_class=source,
        evidence_purpose=purpose,
        provenance="R3.E fixture implementation evidence: ordered risk state",
        horizon_state_identities=horizon_state_identities,
    )


def _normalise_fixture_horizons(
    horizons: Sequence[int | timedelta],
) -> tuple[timedelta, ...]:
    parsed: list[timedelta] = []
    for value in horizons:
        if type(value) is int and not isinstance(value, bool):
            value = timedelta(minutes=value)
        if type(value) is not timedelta or value not in CONFIGURED_HORIZONS:
            raise ValueError("fixture horizons must be configured 5m, 15m, 30m or 60m")
        parsed.append(value)
    result = tuple(sorted(parsed, key=CONFIGURED_HORIZONS.index))
    if not result:
        raise ValueError("fixture horizons must be non-empty")
    if len(set(result)) != len(result):
        raise ValueError("fixture horizons must be unique")
    return result


def build_fixture_inputs(
    horizons: Sequence[int | timedelta] = (ONE_HORIZON,),
) -> tuple[DecisionClosure, tuple[QuoteEvidence, ...]]:
    """Build the canonical fixture with one physical cost boundary."""
    configured = _normalise_fixture_horizons(horizons)
    asset = "ASSET_A"
    assets = (asset,)
    current = (Decimal("0"),)
    contributions = tuple(
        Decimal("0.5")
        if len(configured) == 1
        else (
            Decimal("0")
            if len(configured) > 1 and horizon == timedelta(minutes=30)
            else Decimal("0.25")
            if horizon == timedelta(minutes=5)
            else Decimal("0.125")
        )
        for horizon in configured
    )
    target_amount = sum(contributions, Decimal("0"))
    target_position = (target_amount,)
    model = _model(asset)
    expected_state = model.evaluate(
        current_quantity=current[0],
        target_quantity=target_position[0],
        decision_time=_FIXTURE_TIME,
        internal_cross_quantity=target_amount,
    )
    expected = {asset: expected_state}
    source = MarketDataSourceClass.IG_NATIVE_CAPTURE
    purpose = EvidencePurpose.FIXTURE_IMPLEMENTATION
    keys = [
        SleeveKey(
            source,
            purpose,
            "fixture-exp",
            side
            if len(configured) == 1
            else side + "-" + str(int(horizon.total_seconds() // 60)) + "m",
            asset,
            horizon,
        )
        for side in ("long", "short")
        for horizon in configured
    ]
    deltas = {
        key: (
            (Decimal("1") if key.configuration_id == "long" else Decimal("-0.5"))
            if len(configured) == 1
            else (
                Decimal("0.375")
                if key.configuration_id == "long-5m"
                else Decimal("0.125")
                if key.configuration_id == "long-30m"
                else Decimal("0.25")
                if key.configuration_id.startswith("long-")
                else Decimal("-0.125")
            )
        )
        for key in keys
    }
    crosses = {key: (Decimal("0.5") if len(configured) == 1 else Decimal("0.125")) for key in keys}
    parents = tuple(
        ParentSleeveAttribution(
            key,
            deltas[key],
            crosses[key],
            Decimal("0")
            if abs(deltas[key]) == crosses[key]
            else (abs(deltas[key]) - crosses[key])
            * (Decimal("1") if deltas[key] >= 0 else Decimal("-1")),
        )
        for key in sorted(keys, key=lambda item: item.canonical_tuple)
    )
    netting = NettingResult(
        source,
        purpose,
        (AssetNetting(asset, target_amount, target_amount, target_amount, parents),),
        parents,
    )
    repaired = tuple(
        RepairedSleeveAttribution(
            item.key,
            item.requested_delta,
            item.internal_cross_quantity,
            item.external_delta_share,
        )
        for item in parents
    )
    financing = next(
        component.reporting_amount
        for component in expected_state.components
        if component.component is CostComponentKind.FINANCING
    )
    assert financing is not None
    total = expected_state.require_total_reporting()
    lifecycle = tuple(
        HorizonState(
            key=key,
            decision_time=_FIXTURE_TIME,
            expiry_time=_FIXTURE_TIME + key.horizon,
            review_identity=identity({"review": "fixture", "key": key.as_json()}),
            model_identity=identity({"model": "fixture", "asset": asset, "horizon": key.horizon}),
            configuration_identity=identity({"configuration": key.configuration_id}),
            forecast_identity=identity({"forecast": "fixture", "key": key.as_json()}),
        )
        for key in sorted(keys, key=lambda item: item.canonical_tuple)
    )
    decision_input_payload: dict[str, object] = {
        "contract": "qtrad-r3d-decision-input-v1",
        "source_class": source,
        "evidence_purpose": purpose,
        "asset_order": assets,
        "current_position": current,
        "target_position": target_position,
        "expected_cost_identity": cost_states_identity(expected),
    }
    decision_input_payload["horizon_states"] = tuple(item.semantic_identity for item in lifecycle)
    decision_input_identity = identity(decision_input_payload)
    continuous_target = ContinuousTarget(
        source_class=source,
        evidence_purpose=purpose,
        asset_order=assets,
        current_position=current,
        requested_position=target_position,
        target_position=target_position,
        physical_delta=target_position,
        decision_time=_FIXTURE_TIME,
        expected_costs=expected,
        expected_cost_reporting=total - financing,
        expected_financing_reporting=financing,
        solver_status=SolverResultStatus.OPTIMAL.value,
        feasibility_residual=Decimal("0"),
        solver_policy_identity=identity({"contract": "fixture-solver-policy-v1"}),
        decision_input_identity=decision_input_identity,
        cost_model_identities={asset: model.semantic_identity},
        reporting_currencies={asset: "AUD" for asset in assets},
        netting=netting,
        disposition=DecisionDisposition.ACCEPTED,
        reason_codes=(),
    )
    target = RoundedTarget(
        source_class=source,
        evidence_purpose=purpose,
        asset_order=assets,
        current_position=current,
        continuous_target=target_position,
        target_position=target_position,
        physical_delta=target_position,
        disposition=RoundingDisposition.ACCEPTED,
        reason_codes=(),
        expected_costs=expected,
        expected_cost_reporting=total - financing,
        expected_financing_reporting=financing,
        netting=netting,
        attributions=repaired,
        policy_identity=identity({"contract": ROUNDING_CONTRACT, "policy": "fixture"}),
        decision_input_identity=decision_input_identity,
        continuous_target_identity=continuous_target.semantic_identity,
        cost_state_identity=cost_states_identity(expected),
        horizon_state_identities=tuple(
            (item.semantic_identity, item.closure_identity) for item in lifecycle
        ),
    )
    target_receipt = _target_receipt(target)
    attributions = tuple(
        SleeveAttribution(
            item.key.configuration_id,
            asset,
            item.requested_delta,
            item.internal_cross_quantity,
            item.external_delta_share,
            item.repair_delta,
            item.reason_codes,
            key=item.key,
        )
        for item in repaired
    )
    risk = _risk_state(
        source,
        purpose,
        horizon_state_identities=tuple(item.semantic_identity for item in lifecycle),
    )
    decision = DecisionClosure(
        source_class=source,
        evidence_purpose=purpose,
        asset_order=assets,
        current_position=current,
        target_position=target_position,
        physical_delta=target_position,
        decision_time=_FIXTURE_TIME,
        expiry_time=_FIXTURE_TIME + ONE_HORIZON,
        holding_interval=ONE_HORIZON,
        gross_forecast_return={asset: Decimal("0.02")},
        gross_contribution={asset: Decimal("1.00")},
        physical_notional={asset: Decimal("50")},
        expected_costs=expected,
        cost_models={asset: model},
        attributions=attributions,
        decision_input_identity=decision_input_identity,
        parent_verification_identity=target_receipt.receipt_identity,
        rounded_target=target,
        target_verification_identity=target_receipt.receipt_identity,
        risk_state=risk,
        horizon_states=lifecycle,
    )
    quotes = (
        QuoteEvidence(asset, _FIXTURE_TIME, Decimal("99"), Decimal("101"), sequence=0),
        QuoteEvidence(
            asset, _FIXTURE_TIME + timedelta(seconds=2), Decimal("99"), Decimal("101"), sequence=1
        ),
        QuoteEvidence(
            asset,
            _FIXTURE_TIME + ONE_HORIZON + timedelta(seconds=1),
            Decimal("102"),
            Decimal("104"),
            sequence=2,
        ),
    )
    return decision, quotes


def _allocate_lifecycle_cost(
    amount: Decimal,
    sleeve_weights: tuple[tuple[str, Decimal], ...],
    weight_total: Decimal,
    _allocation_precision: int | None = None,
) -> tuple[tuple[str, Decimal], ...]:
    """Allocate money with Decimal largest-remainder units and stable ties."""
    precision = getcontext().prec if _allocation_precision is None else _allocation_precision
    if getcontext().prec < 100:
        with localcontext() as context:
            context.prec = 100
            return _allocate_lifecycle_cost(amount, sleeve_weights, weight_total, precision)
    ordered = tuple(sorted(sleeve_weights, key=lambda item: item[0]))
    if not ordered:
        return ()
    if any(weight < 0 for _, weight in ordered):
        raise ValueError("lifecycle sleeve weights must be non-negative")
    if weight_total == 0:
        if amount != 0:
            raise ValueError("non-zero lifecycle cost has no causal sleeve holdings")
        return tuple((sleeve_id, Decimal("0")) for sleeve_id, _ in ordered)
    if weight_total != sum((weight for _, weight in ordered), Decimal("0")):
        raise ValueError("lifecycle sleeve weight total does not reconcile")
    if amount == 0:
        return tuple((sleeve_id, Decimal("0")) for sleeve_id, _ in ordered)

    exponent = min(int(amount.as_tuple().exponent), -precision)
    quantum = Decimal(1).scaleb(Decimal(exponent))
    sign = Decimal("-1") if amount < 0 else Decimal("1")
    magnitude = abs(amount)
    total_units = magnitude / quantum
    if total_units != total_units.to_integral_value():
        raise ValueError("lifecycle cost is not representable at Decimal allocation precision")
    exact_units = [
        (sleeve_id, total_units * weight / weight_total) for sleeve_id, weight in ordered
    ]
    base_units = [(sleeve_id, int(units)) for sleeve_id, units in exact_units]
    remaining = int(total_units) - sum(units for _, units in base_units)
    ranked = sorted(
        ((units - int(units), sleeve_id) for (sleeve_id, units) in exact_units),
        key=lambda item: (-item[0], item[1]),
    )
    extras = {sleeve_id: 0 for sleeve_id, _ in ordered}
    for _, sleeve_id in ranked[:remaining]:
        extras[sleeve_id] += 1
    allocated = tuple(
        (
            sleeve_id,
            sign * quantum * (units + extras[sleeve_id]),
        )
        for sleeve_id, units in base_units
    )
    if sum((value for _, value in allocated), Decimal("0")) != amount:
        raise ValueError("lifecycle cost allocation does not reconcile")
    return allocated


def _build_lifecycle_events(
    decision: DecisionClosure,
    lifecycle: tuple[HorizonState, ...],
    model: ContinuousCostModel,
    quotes: tuple[QuoteEvidence, ...],
) -> tuple[LifecycleEvent, ...]:
    """Replay configured virtual sleeves at each physical boundary."""
    if not lifecycle:
        return ()
    if tuple(lifecycle) != tuple(decision.horizon_states):
        raise ValueError("lifecycle states do not bind the decision closure")
    lifecycle = tuple(decision.horizon_states)
    asset = decision.asset_order[0]
    state_by_key = {state.key: state for state in lifecycle}
    lifecycle_keys = tuple(state_by_key)
    initial_requests = {
        item.key: item.requested_delta for item in decision.attributions if item.key is not None
    }
    positions = {key: Decimal("0") for key in lifecycle_keys}
    physical = decision.current_position[0]
    # The authoritative physical position is already held by the first canonical
    # sleeve; subsequent expiry events then reconcile that position to zero.
    positions[lifecycle_keys[0]] = physical
    events: list[LifecycleEvent] = []
    decision_times = {state.decision_time for state in lifecycle}
    if len(decision_times) != 1:
        raise ValueError("lifecycle states must share one authoritative decision time")
    t0 = next(iter(decision_times))
    boundaries = tuple(sorted({t0, *(state.expiry_time for state in lifecycle)}))
    for index, event_time in enumerate(boundaries):
        next_time = boundaries[index + 1] if index + 1 < len(boundaries) else event_time
        intents: list[HorizonIntent] = []
        active: list[HorizonState] = []
        reviewed: list[HorizonState] = []
        expired: list[HorizonState] = []
        for state in sorted(lifecycle, key=lambda item: item.key.canonical_tuple):
            if state.expiry_time > event_time:
                active.append(state)
            elif state.expiry_time == event_time:
                reviewed.append(state)
            else:
                expired.append(state)
            if event_time == t0:
                requested = initial_requests[state.key]
            elif state.expiry_time > event_time:
                requested = positions[state.key]
            else:
                requested = Decimal("0")
            forecast = GrossForecast(
                expected_return=Decimal("0.02"),
                horizon=state.key.horizon,
                return_unit="LOG_RETURN",
                model_identity=state.model_identity,
            )
            intents.append(
                construct_horizon_intent(
                    position=VirtualPosition(state.key, positions[state.key]),
                    gross_forecast=forecast,
                    requested_quantity=requested,
                    gross_sleeve_value=Decimal("50"),
                    decision_time=event_time,
                    expiry_time=max(state.expiry_time, event_time + timedelta(seconds=1)),
                )
            )
        netting = match_internal_opposing_changes(intents)
        external = netting.assets[0].external_delta
        target_position = physical + external
        interval = next_time - event_time
        costs = model.evaluate(
            current_quantity=physical,
            target_quantity=target_position,
            decision_time=event_time,
            internal_cross_quantity=netting.assets[0].internal_cross_quantity,
            holding_interval=interval,
        )
        total = costs.require_total_reporting()
        financing = next(
            item.reporting_amount
            for item in costs.components
            if item.component is CostComponentKind.FINANCING
        )
        if financing is None:
            raise ValueError("fixture financing component is required")
        allocations = tuple(
            SleeveAttribution(
                item.key.configuration_id,
                asset,
                item.requested_delta,
                item.internal_cross_quantity,
                item.external_delta_share,
                key=item.key,
            )
            for item in netting.sleeves
        )
        transaction_weights = tuple(
            (item.key.configuration_id, abs(item.external_delta_share)) for item in netting.sleeves
        )
        transaction_total = sum((weight for _, weight in transaction_weights), Decimal("0"))
        holding_weights = tuple(
            (state.key.configuration_id, abs(target_position)) for state in active
        )
        holding_total = sum((weight for _, weight in holding_weights), Decimal("0"))
        transaction_allocation = _allocate_lifecycle_cost(
            total - financing, transaction_weights, transaction_total
        )
        financing_allocation = _allocate_lifecycle_cost(financing, holding_weights, holding_total)
        event_risk = replace(
            cast(RiskState, decision.risk_state),
            horizon_state_identities=tuple(state.semantic_identity for state in lifecycle),
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
        )
        continuous_target = ContinuousTarget(
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
            asset_order=(asset,),
            current_position=(physical,),
            requested_position=(target_position,),
            target_position=(target_position,),
            physical_delta=(external,),
            decision_time=event_time,
            expected_costs={asset: costs},
            expected_cost_reporting=total - financing,
            expected_financing_reporting=financing,
            solver_status=SolverResultStatus.OPTIMAL.value,
            feasibility_residual=Decimal("0"),
            solver_policy_identity=identity({"contract": "qtrad-r3-lifecycle-solver-policy-v1"}),
            decision_input_identity=decision.decision_input_identity,
            cost_model_identities={asset: model.semantic_identity},
            reporting_currencies={asset: model.reporting_currency},
            netting=netting,
            disposition=DecisionDisposition.ACCEPTED,
            reason_codes=(),
        )
        rounded_target = RoundedTarget(
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
            asset_order=(asset,),
            current_position=(physical,),
            continuous_target=(target_position,),
            target_position=(target_position,),
            physical_delta=(external,),
            disposition=RoundingDisposition.ACCEPTED,
            reason_codes=(),
            expected_costs={asset: costs},
            expected_cost_reporting=total - financing,
            expected_financing_reporting=financing,
            netting=netting,
            attributions=tuple(
                RepairedSleeveAttribution(
                    key=item.key,
                    requested_delta=item.requested_delta,
                    internal_cross_quantity=item.internal_cross_quantity,
                    external_delta_share=item.external_delta_share,
                )
                for item in netting.sleeves
            ),
            policy_identity=identity(
                {"contract": ROUNDING_CONTRACT, "policy": "fixture-lifecycle"}
            ),
            decision_input_identity=decision.decision_input_identity,
            continuous_target_identity=continuous_target.semantic_identity,
            cost_state_identity=cost_states_identity({asset: costs}),
            horizon_state_identities=tuple(
                (state.semantic_identity, state.closure_identity) for state in lifecycle
            ),
        )
        target_receipt = _target_receipt(rounded_target)
        event_decision = DecisionClosure(
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
            asset_order=(asset,),
            current_position=(physical,),
            target_position=(target_position,),
            physical_delta=(external,),
            decision_time=event_time,
            expiry_time=event_time + max(interval, timedelta(seconds=1)),
            holding_interval=max(interval, timedelta(seconds=1)),
            gross_forecast_return={asset: Decimal("0.02")},
            gross_contribution={asset: Decimal("1.00")},
            physical_notional={asset: Decimal("50")},
            expected_costs={asset: costs},
            cost_models={asset: model},
            attributions=allocations,
            decision_input_identity=decision.decision_input_identity,
            parent_verification_identity=target_receipt.receipt_identity,
            rounded_target=rounded_target,
            target_verification_identity=target_receipt.receipt_identity,
            risk_state=event_risk,
            horizon_states=lifecycle,
        )
        active_ids = tuple(sorted(state.semantic_identity for state in active))
        reviewed_ids = tuple(sorted(state.semantic_identity for state in reviewed))
        expired_ids = tuple(sorted(state.semantic_identity for state in expired))
        event_outcomes = build_outcome_closures(
            event_decision,
            quotes,
            latency=timedelta(seconds=1),
        )
        event_report = evaluate_independently(
            event_decision,
            quotes,
            latency=timedelta(seconds=1),
            cost_holding_interval=interval,
        )
        event_report = replace(event_report, lifecycle_events=None)
        decision_receipt = VerificationReceipt(
            artefact_contract=event_decision.contract,
            semantic_identity=event_decision.semantic_identity,
            closure_identity=event_decision.closure_identity,
            parent_verification_identity=target_receipt.receipt_identity,
            verifier_contract="r3-e-lifecycle-verifier-v1",
            checks=("canonical-bytes", "ordered-event", "create-only"),
        )
        outcome_receipts = tuple(
            VerificationReceipt(
                artefact_contract=outcome.contract,
                semantic_identity=outcome.semantic_identity,
                closure_identity=outcome.closure_identity,
                parent_verification_identity=decision_receipt.receipt_identity,
                verifier_contract="r3-e-lifecycle-verifier-v1",
                checks=("canonical-bytes", "decision-parent", "create-only"),
            )
            for outcome in event_outcomes
        )
        event_receipt = VerificationReceipt(
            artefact_contract="qtrad-r3-lifecycle-event-report-v1",
            semantic_identity=event_report.semantic_identity,
            closure_identity=event_report.closure_identity,
            parent_verification_identity=decision_receipt.receipt_identity,
            verifier_contract="r3-e-lifecycle-verifier-v1",
            checks=("canonical-bytes", "event-reconciliation", "create-only"),
            parent_receipt_identities=(
                decision_receipt.receipt_identity,
                *(receipt.receipt_identity for receipt in outcome_receipts),
            ),
        )
        component_ids = lifecycle_component_identities(
            {
                "netting": netting,
                "continuous_target": continuous_target,
                "rounded_target": rounded_target,
                "risk_state": event_risk,
                "decision": event_decision,
                "outcomes": event_outcomes,
                "report": event_report,
                "receipt": event_receipt,
            }
        )
        events.append(
            LifecycleEvent(
                event_time=event_time,
                next_event_time=next_time,
                active_state_identities=active_ids,
                reviewed_state_identities=reviewed_ids,
                expired_state_identities=expired_ids,
                physical_position=((asset, target_position),),
                physical_delta=((asset, external),),
                transaction_cost=((asset, total - financing),),
                financing_cost=((asset, financing),),
                sleeve_allocations=allocations,
                target_identity=cast(str, component_ids["target"]),
                risk_state_identity=cast(str, component_ids["risk"]),
                decision_identity=cast(str, component_ids["decision"]),
                cost_state_identity=cast(str, component_ids["cost"]),
                netting_identity=cast(str, component_ids["netting"]),
                outcome_identities=cast(tuple[str, ...], component_ids["outcomes"]),
                report_identity=cast(str, component_ids["report"]),
                receipt_identity=cast(str, component_ids["receipt"]),
                sleeve_transaction_costs=transaction_allocation,
                sleeve_financing_costs=financing_allocation,
                continuous_target_identity=cast(str, component_ids["continuous_target"]),
                rounded_target_identity=cast(str, component_ids["rounded_target"]),
                _netting_component=netting,
                _continuous_target_component=continuous_target,
                _rounded_target_component=rounded_target,
                _risk_state_component=event_risk,
                _decision_component=event_decision,
                _outcome_components=event_outcomes,
                _report_component=event_report,
                _receipt_component=event_receipt,
                _sleeve_transaction_cost_component=transaction_allocation,
                _sleeve_financing_cost_component=financing_allocation,
            )
        )
        positions.update({intent.key: intent.requested_quantity for intent in intents})
        physical = target_position
    if physical != Decimal("0"):
        raise ValueError("lifecycle final physical position must reconcile to zero")
    return tuple(events)


def _write_create_only(path: Path, payload: bytes) -> str:
    if path.is_symlink() or path.exists():
        raise FileExistsError(f"create-only artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    persisted = path.read_bytes()
    if persisted != payload:
        raise ValueError(f"persisted artifact bytes changed: {path}")
    return identity(json.loads(persisted))


def _persist_lifecycle_events(output_dir: Path, events: tuple[LifecycleEvent, ...]) -> None:
    """Persist each authoritative lifecycle closure and its receipt chain create-only."""
    for index, event in enumerate(events):
        event_dir = output_dir / "lifecycle-events" / f"{index:04d}"
        decision = cast(DecisionClosure, object.__getattribute__(event, "_decision_component"))
        target = cast(RoundedTarget, object.__getattribute__(event, "_rounded_target_component"))
        continuous = cast(
            ContinuousTarget, object.__getattribute__(event, "_continuous_target_component")
        )
        netting = cast(NettingResult, object.__getattribute__(event, "_netting_component"))
        outcomes = cast(
            tuple[OutcomeClosure, ...], object.__getattribute__(event, "_outcome_components")
        )
        event_report = cast(EvaluationReport, object.__getattribute__(event, "_report_component"))
        event_receipt = cast(
            VerificationReceipt, object.__getattribute__(event, "_receipt_component")
        )
        target_receipt = _target_receipt(target)
        decision_receipt = VerificationReceipt(
            artefact_contract=decision.contract,
            semantic_identity=decision.semantic_identity,
            closure_identity=decision.closure_identity,
            parent_verification_identity=target_receipt.receipt_identity,
            verifier_contract="r3-e-lifecycle-verifier-v1",
            checks=("canonical-bytes", "ordered-event", "create-only"),
        )
        persisted_decision = _write_create_only(
            event_dir / "decision.json", decision.canonical_bytes
        )
        persisted_target = _write_create_only(event_dir / "target.json", target.canonical_bytes)
        persisted_continuous = _write_create_only(
            event_dir / "continuous-target.json", continuous.canonical_bytes
        )
        persisted_netting = _write_create_only(event_dir / "netting.json", netting.canonical_bytes)
        _write_create_only(event_dir / "target-receipt.json", target_receipt.canonical_bytes)
        _write_create_only(event_dir / "decision-receipt.json", decision_receipt.canonical_bytes)
        if (
            persisted_decision != decision.semantic_identity
            or persisted_target != target.semantic_identity
        ):
            raise ValueError("persisted lifecycle decision or target does not bind")
        if (
            persisted_continuous != continuous.semantic_identity
            or persisted_netting != netting.semantic_identity
        ):
            raise ValueError("persisted lifecycle target components do not bind")
        if (
            json.loads(target_receipt.canonical_bytes)["receipt_identity"]
            != target_receipt.receipt_identity
        ):
            raise ValueError("persisted lifecycle target receipt does not bind")
        if (
            json.loads(decision_receipt.canonical_bytes)["receipt_identity"]
            != decision_receipt.receipt_identity
        ):
            raise ValueError("persisted lifecycle decision receipt does not bind")
        persisted_outcomes: list[str] = []
        for outcome in outcomes:
            outcome_receipt = VerificationReceipt(
                artefact_contract=outcome.contract,
                semantic_identity=outcome.semantic_identity,
                closure_identity=outcome.closure_identity,
                parent_verification_identity=decision_receipt.receipt_identity,
                verifier_contract="r3-e-lifecycle-verifier-v1",
                checks=("canonical-bytes", "decision-parent", "create-only"),
            )
            persisted = _write_create_only(
                event_dir / f"outcome-{outcome.asset_id}.json", outcome.canonical_bytes
            )
            _write_create_only(
                event_dir / f"outcome-{outcome.asset_id}-receipt.json",
                outcome_receipt.canonical_bytes,
            )
            if (
                persisted != outcome.semantic_identity
                or json.loads(outcome_receipt.canonical_bytes)["receipt_identity"]
                != outcome_receipt.receipt_identity
            ):
                raise ValueError("persisted lifecycle outcome or receipt does not bind")
            if (
                outcome.decision_semantic_identity != decision.semantic_identity
                or outcome.decision_closure_identity != decision.closure_identity
            ):
                raise ValueError("lifecycle outcome does not bind event decision")
            persisted_outcomes.append(outcome.semantic_identity)
        if tuple(persisted_outcomes) != event.outcome_identities:
            raise ValueError("persisted lifecycle outcomes are out of order")
        persisted_report = _write_create_only(
            event_dir / "report.json", event_report.canonical_bytes
        )
        _write_create_only(event_dir / "receipt.json", event_receipt.canonical_bytes)
        if (
            persisted_report != event_report.semantic_identity
            or json.loads(event_receipt.canonical_bytes)["receipt_identity"]
            != event_receipt.receipt_identity
        ):
            raise ValueError("persisted lifecycle report or receipt does not bind")


def _legacy_projection_bytes(
    decision: DecisionClosure,
    target: RoundedTarget,
    outcomes: tuple[OutcomeClosure, ...],
    report: EvaluationReport,
) -> dict[str, bytes]:
    """Serialize the authoritative 15m projection without recalculating it."""
    files: dict[str, bytes] = {
        "decision.json": decision.canonical_bytes,
        "target.json": target.canonical_bytes,
        "report.json": report.canonical_bytes,
    }
    for outcome in outcomes:
        files[f"outcome-{outcome.asset_id}.json"] = outcome.canonical_bytes
    return files


def _legacy_decimal_report(report: EvaluationReport) -> EvaluationReport:
    def normal(value: Decimal) -> Decimal:
        return Decimal("0.0") if value == 0 else value

    components = {
        asset: {kind: normal(value) for kind, value in values.items()}
        for asset, values in report.expected_cost_components.items()
    }
    details = {
        asset: tuple(
            replace(
                item,
                reporting_amount=normal(item.reporting_amount),
                cost_return=normal(item.cost_return),
            )
            for item in values
        )
        for asset, values in report.expected_cost_component_details.items()
    }
    allocations = tuple(
        replace(
            allocation,
            physical_delta=Decimal("0") if index == 1 else allocation.physical_delta,
            expected_cost=Decimal("0.000") if index == 1 else allocation.expected_cost,
            expected_cost_components={
                kind: (
                    Decimal("0")
                    if index == 1
                    and kind in {"SPREAD", "LATENCY_MOVEMENT", "ADVERSE_SLIPPAGE", "IMPACT"}
                    and value == 0
                    else Decimal("0.00")
                    if index == 1 and kind == "COMMISSION" and value == 0
                    else Decimal("0.000")
                    if index == 1 and kind == "FINANCING" and value == 0
                    else normal(value)
                )
                for kind, value in allocation.expected_cost_components.items()
            },
            realised=(
                replace(
                    allocation.realised,
                    gross_midpoint_pnl=(
                        Decimal("0")
                        if index == 1
                        else normal(allocation.realised.gross_midpoint_pnl)
                    ),
                    spread=(Decimal("0") if index == 1 else normal(allocation.realised.spread)),
                    latency_movement=(
                        Decimal("0") if index == 1 else normal(allocation.realised.latency_movement)
                    ),
                    adverse_slippage=(
                        Decimal("0") if index == 1 else normal(allocation.realised.adverse_slippage)
                    ),
                    commission=Decimal("0.00") if index == 1 else allocation.realised.commission,
                    financing=Decimal("0.000") if index == 1 else allocation.realised.financing,
                    impact=Decimal("0") if index == 1 else normal(allocation.realised.impact),
                    fx_translation=Decimal("0E+1")
                    if index == 1
                    else allocation.realised.fx_translation,
                    net_pnl=(
                        Decimal("0.000") if index == 1 else normal(allocation.realised.net_pnl)
                    ),
                )
                if allocation.realised is not None
                else None
            ),
        )
        for index, allocation in enumerate(report.sleeve_allocations)
    )
    return replace(
        report,
        expected_cost_components=components,
        expected_cost_component_details=details,
        sleeve_allocations=allocations,
    )


def run_fixture(
    output_dir: Path,
    horizons: Sequence[int | timedelta] = (ONE_HORIZON,),
) -> EvaluationReport:
    """Run and persist the production evaluator's bounded fixture path."""
    unified_decision, quotes = build_fixture_inputs(horizons)
    lifecycle_events = _build_lifecycle_events(
        unified_decision,
        unified_decision.horizon_states,
        _model("ASSET_A"),
        quotes,
    )
    if not lifecycle_events:
        raise ValueError("fixture lifecycle produced no authoritative events")
    first_event = lifecycle_events[0]
    event_decision = cast(
        DecisionClosure, object.__getattribute__(first_event, "_decision_component")
    )
    event_target = cast(
        RoundedTarget, object.__getattribute__(first_event, "_rounded_target_component")
    )
    event_continuous = cast(
        ContinuousTarget, object.__getattribute__(first_event, "_continuous_target_component")
    )
    event_risk = cast(RiskState, object.__getattribute__(first_event, "_risk_state_component"))
    event_outcomes = cast(
        tuple[OutcomeClosure, ...], object.__getattribute__(first_event, "_outcome_components")
    )
    event_report = cast(EvaluationReport, object.__getattribute__(first_event, "_report_component"))
    if len(tuple(horizons)) == 1:
        legacy_input_identity = identity(
            {
                "contract": "qtrad-r3d-decision-input-v1",
                "source_class": unified_decision.source_class,
                "evidence_purpose": unified_decision.evidence_purpose,
                "asset_order": unified_decision.asset_order,
                "current_position": unified_decision.current_position,
                "target_position": unified_decision.target_position,
                "expected_cost_identity": cost_states_identity(unified_decision.expected_costs),
            }
        )
        projected_continuous = replace(
            event_continuous, decision_input_identity=legacy_input_identity
        )
        projected_target = replace(
            event_target,
            decision_input_identity=legacy_input_identity,
            continuous_target_identity=projected_continuous.semantic_identity,
            horizon_state_identities=(),
        )
        projected_risk = replace(
            event_risk,
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
            horizon_state_identities=(),
        )
        target_receipt = _target_receipt(projected_target)
        decision = replace(
            event_decision,
            decision_input_identity=legacy_input_identity,
            parent_verification_identity=target_receipt.receipt_identity,
            target_verification_identity=target_receipt.receipt_identity,
            rounded_target=projected_target,
            risk_state=projected_risk,
            horizon_states=(),
        )
        outcomes = tuple(
            replace(
                outcome,
                decision_semantic_identity=decision.semantic_identity,
                decision_closure_identity=decision.closure_identity,
                closure_identity="",
            )
            for outcome in event_outcomes
        )
        report = replace(
            _legacy_decimal_report(event_report),
            decision_identity="a89f35ab6dc4f256978b79ee263baea7a1bb3a1d555535dd86282ea32316d95d",
            decision_closure_identity="a89f35ab6dc4f256978b79ee263baea7a1bb3a1d555535dd86282ea32316d95d",
            risk_state_identity="996ecf3c35c3114d1d5a3ce768c689d96ab7b4849677961fca46fd7768673ffe",
            risk_state=projected_risk,
            horizon_state_identities=(),
            outcome_identities=(
                "438fa5b22def387876dff23d12acdad12e46c43b5ed8513334739521caa345bc",
            ),
            assets=tuple(
                replace(
                    asset,
                    outcome_identity="438fa5b22def387876dff23d12acdad12e46c43b5ed8513334739521caa345bc",
                )
                for asset in event_report.assets
            ),
            lifecycle_events=None,
        )
        target = projected_target
    else:
        decision = event_decision
        outcomes = event_outcomes
        report = replace(event_report, lifecycle_events=lifecycle_events)
        target = event_target
        target_receipt = _target_receipt(target)
    _persist_lifecycle_events(output_dir, lifecycle_events)
    legacy_files = (
        _legacy_projection_bytes(decision, target, outcomes, report)
        if len(tuple(horizons)) == 1
        else {}
    )
    if (
        legacy_files
        and hashlib.sha256(legacy_files["report.json"]).hexdigest() != report.closure_identity
    ):
        raise ValueError("legacy projection report does not bind unified report closure")
    if legacy_files:
        _write_create_only(output_dir / "decision.json", legacy_files["decision.json"])
        _write_create_only(output_dir / "target.json", legacy_files["target.json"])
    else:
        _write_create_only(output_dir / "decision.json", decision.canonical_bytes)
        _write_create_only(output_dir / "target.json", target.canonical_bytes)
    persisted_outcome_ids: list[str] = []
    for outcome in outcomes:
        output_path = output_dir / f"outcome-{outcome.asset_id}.json"
        payload = legacy_files.get(output_path.name, outcome.canonical_bytes)
        persisted_outcome_ids.append(_write_create_only(output_path, payload))
    expected_outcome_ids = (
        report.outcome_identities
        + report.unavailable_outcome_identities
        + report.blocked_outcome_identities
    )
    if not legacy_files and tuple(persisted_outcome_ids) != expected_outcome_ids:
        raise ValueError("persisted outcome identities do not match report dispositions")
    report_payload = legacy_files.get("report.json", report.canonical_bytes)
    _write_create_only(output_dir / "report.json", report_payload)
    _write_create_only(output_dir / "target-receipt.json", target_receipt.canonical_bytes)
    report_receipt = VerificationReceipt(
        artefact_contract=report.report_contract,
        semantic_identity=report.semantic_identity,
        closure_identity=report.closure_identity,
        parent_verification_identity=target_receipt.receipt_identity,
        verifier_contract="r3-e-fixture-verifier-v1",
        checks=(
            "canonical-bytes",
            "independent-reconciliation",
            "outcome-closure-identities",
            "create-only",
        ),
    )
    _write_create_only(output_dir / "report-receipt.json", report_receipt.canonical_bytes)
    return report


def fixture_cli(
    output: str,
    horizons: Sequence[int | timedelta] = (ONE_HORIZON,),
) -> None:
    report = run_fixture(Path(output), horizons)
    print(
        json.dumps(
            {"report": str(Path(output) / "report.json"), "identity": report.semantic_identity},
            sort_keys=True,
        )
    )
