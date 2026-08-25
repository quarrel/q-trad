"""Fixture-only R3.E vertical slice and create-only persistence."""

from __future__ import annotations

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
    legacy = configured == (ONE_HORIZON,)
    asset = "ASSET_A"
    assets = (asset,)
    current = (Decimal("0"),)
    target_amount = (
        Decimal("0.5")
        if legacy
        else sum(
            [
                (
                    Decimal("0")
                    if len(configured) > 1 and horizon == timedelta(minutes=30)
                    else Decimal("0.25")
                    if horizon == timedelta(minutes=5)
                    else Decimal("0.125")
                )
                for horizon in configured
            ],
            Decimal("0"),
        )
    )
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
            side if legacy else f"{side}-{int(horizon.total_seconds() // 60)}m",
            asset,
            horizon,
        )
        for side in ("long", "short")
        for horizon in configured
    ]
    deltas = {
        key: (Decimal("1") if key.configuration_id == "long" else Decimal("-0.5"))
        if legacy
        else (
            Decimal("0.375")
            if key.configuration_id == "long-5m"
            else Decimal("0.125")
            if key.configuration_id == "long-30m"
            else Decimal("0.25")
            if key.configuration_id.startswith("long-")
            else Decimal("-0.125")
        )
        for key in keys
    }
    crosses = {key: (Decimal("0.5") if legacy else Decimal("0.125")) for key in keys}
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
    if not legacy:
        decision_input_payload["horizon_states"] = tuple(
            item.semantic_identity for item in lifecycle
        )
    decision_input_identity = identity(decision_input_payload)
    continuous_target: ContinuousTarget | None = None
    if not legacy:
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
        continuous_target_identity=(
            continuous_target.semantic_identity
            if continuous_target is not None
            else identity(
                {
                    "contract": "qtrad-continuous-target-v1",
                    "asset_order": assets,
                    "target": target_position,
                }
            )
        ),
        cost_state_identity=cost_states_identity(expected),
        horizon_state_identities=()
        if legacy
        else tuple((item.semantic_identity, item.closure_identity) for item in lifecycle),
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
        horizon_state_identities=()
        if legacy
        else tuple(item.semantic_identity for item in lifecycle),
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
        horizon_states=() if legacy else lifecycle,
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
    outcomes: tuple[OutcomeClosure, ...],
    report: EvaluationReport,
) -> tuple[LifecycleEvent, ...]:
    """Replay configured virtual sleeves at each physical boundary."""
    if not lifecycle:
        return ()
    asset = decision.asset_order[0]
    state_by_key = {state.key: state for state in lifecycle}
    lifecycle_keys = tuple(state_by_key)
    initial_requests = {
        item.key: item.requested_delta for item in decision.attributions if item.key is not None
    }
    positions = {key: Decimal("0") for key in lifecycle_keys}
    physical = decision.current_position[0]
    events: list[LifecycleEvent] = []
    boundaries = tuple(sorted({_FIXTURE_TIME, *(state.expiry_time for state in lifecycle)}))
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
                expired.append(state)
            else:
                expired.append(state)
            if event_time == _FIXTURE_TIME:
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
            horizon_state_identities=(),
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
            horizon_state_identities=(),
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
            horizon_states=(),
        )
        active_ids = tuple(sorted(state.semantic_identity for state in active))
        reviewed_ids = tuple(sorted(state.semantic_identity for state in reviewed))
        expired_ids = tuple(sorted(state.semantic_identity for state in expired))
        event_report = replace(
            report,
            lifecycle_events=(),
            horizon_state_identities=(),
            decision_identity=event_decision.semantic_identity,
            decision_closure_identity=event_decision.closure_identity,
            risk_state=event_risk,
            risk_state_identity=event_risk.semantic_identity,
        )
        event_receipt = VerificationReceipt(
            artefact_contract="qtrad-r3-lifecycle-event-report-v1",
            semantic_identity=event_report.semantic_identity,
            closure_identity=event_report.closure_identity,
            parent_verification_identity=event_decision.target_verification_identity,
            verifier_contract="qtrad-r3-lifecycle-event-verifier-v1",
            checks=("canonical-bytes", "event-reconciliation", "create-only"),
        )
        component_ids = lifecycle_component_identities(
            {
                "netting": netting,
                "continuous_target": continuous_target,
                "rounded_target": rounded_target,
                "risk_state": event_risk,
                "decision": event_decision,
                "outcomes": outcomes,
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
                _outcome_components=outcomes,
                _report_component=event_report,
                _receipt_component=event_receipt,
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


def run_fixture(
    output_dir: Path,
    horizons: Sequence[int | timedelta] = (ONE_HORIZON,),
) -> EvaluationReport:
    """Run and persist the production evaluator's bounded fixture path."""
    decision, quotes = build_fixture_inputs(horizons)
    latency = timedelta(seconds=1)
    outcomes = build_outcome_closures(decision, quotes, latency=latency)
    report = evaluate_independently(decision, quotes, latency=latency)
    lifecycle_events = _build_lifecycle_events(
        decision,
        decision.horizon_states,
        _model("ASSET_A"),
        tuple(outcomes),
        report,
    )
    if lifecycle_events:
        report = replace(report, lifecycle_events=lifecycle_events)
    if (
        report.source_class is not decision.source_class
        or report.evidence_purpose is not decision.evidence_purpose
        or report.decision_identity != decision.semantic_identity
        or report.decision_closure_identity != decision.closure_identity
    ):
        raise ValueError("evaluation report does not bind decision closure")
    target = decision.rounded_target
    if target is None:
        raise ValueError("fixture decision must bind a rounded target")
    target_receipt = _target_receipt(target)
    _write_create_only(output_dir / "decision.json", decision.canonical_bytes)
    _write_create_only(output_dir / "target.json", target.canonical_bytes)
    persisted_outcome_ids: list[str] = []
    for outcome in outcomes:
        output_path = output_dir / f"outcome-{outcome.asset_id}.json"
        persisted_id = _write_create_only(output_path, outcome.canonical_bytes)
        if persisted_id != outcome.semantic_identity:
            raise ValueError("persisted outcome identity does not match closure")
        persisted_payload = json.loads(output_path.read_bytes())
        if (
            persisted_payload["source_class"] != decision.source_class.value
            or persisted_payload["evidence_purpose"] != decision.evidence_purpose.value
            or persisted_payload["decision_semantic_identity"] != decision.semantic_identity
            or persisted_payload["decision_closure_identity"] != decision.closure_identity
        ):
            raise ValueError("persisted outcome does not bind decision closure")
        persisted_outcome_ids.append(persisted_id)
    expected_outcome_ids = (
        report.outcome_identities
        + report.unavailable_outcome_identities
        + report.blocked_outcome_identities
    )
    if tuple(persisted_outcome_ids) != expected_outcome_ids:
        raise ValueError("persisted outcome identities do not match report dispositions")
    _write_create_only(output_dir / "report.json", report.canonical_bytes)
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
