"""Fixture-only R3.E vertical slice and create-only persistence."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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
    HorizonIntent,
    HorizonState,
    NettingResult,
    SleeveKey,
    VirtualPosition,
    construct_horizon_intent,
    match_internal_opposing_changes,
)
from qtrad.domain.portfolio import SleeveAttribution as ParentSleeveAttribution
from qtrad.domain.r3_evaluation import (
    DecisionClosure,
    EvaluationReport,
    LifecycleEvent,
    QuoteEvidence,
    SleeveAttribution,
    VerificationReceipt,
    build_outcome_closures,
    evaluate_independently,
    identity,
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
    result = tuple(parsed)
    if not result or tuple(sorted(result, key=CONFIGURED_HORIZONS.index)) != result:
        raise ValueError("fixture horizons must be non-empty, unique and canonical")
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
    target_position = (Decimal("0.5"),)
    model = _model(asset)
    expected_state = model.evaluate(
        current_quantity=current[0],
        target_quantity=target_position[0],
        decision_time=_FIXTURE_TIME,
        internal_cross_quantity=Decimal("0.5"),
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
        else (Decimal("0.25") if key.configuration_id.startswith("long-") else Decimal("-0.125"))
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
        (AssetNetting(asset, Decimal("0.5"), Decimal("0.5"), Decimal("0.5"), parents),),
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
        continuous_target_identity=identity(
            {
                "contract": "qtrad-continuous-target-v1",
                "asset_order": assets,
                "target": target_position,
            }
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
) -> tuple[tuple[str, Decimal], ...]:
    if weight_total == 0:
        return tuple((sleeve_id, Decimal("0")) for sleeve_id, _ in sleeve_weights)
    allocated: list[tuple[str, Decimal]] = []
    remainder = amount
    for index, (sleeve_id, weight) in enumerate(sleeve_weights):
        value = remainder if index == len(sleeve_weights) - 1 else amount * weight / weight_total
        allocated.append((sleeve_id, value))
        remainder -= value
    return tuple(allocated)


def _build_lifecycle_events(
    decision: DecisionClosure,
    lifecycle: tuple[HorizonState, ...],
    model: ContinuousCostModel,
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
        target = physical + external
        interval = next_time - event_time
        costs = model.evaluate(
            current_quantity=physical,
            target_quantity=target,
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
        sleeve_weights = tuple(
            (item.key.configuration_id, abs(item.external_delta_share)) for item in netting.sleeves
        )
        weight_total = sum((weight for _, weight in sleeve_weights), Decimal("0"))

        transaction_allocation = _allocate_lifecycle_cost(
            total - financing, sleeve_weights, weight_total
        )
        financing_allocation = _allocate_lifecycle_cost(financing, sleeve_weights, weight_total)

        event_payload = {
            "event_time": event_time,
            "next_event_time": next_time,
            "target": ((asset, target),),
            "delta": ((asset, external),),
            "netting": netting.semantic_identity,
            "cost": identity(
                {"current": physical, "target": target, "total": total, "interval": interval}
            ),
            "active": tuple(state.semantic_identity for state in active),
            "reviewed": tuple(state.semantic_identity for state in reviewed),
            "expired": tuple(state.semantic_identity for state in expired),
        }
        target_id = identity({"contract": "r3-f-target-event-v1", **event_payload})
        risk_id = identity({"contract": "r3-f-risk-event-v1", **event_payload})
        decision_id = identity(
            {
                "contract": "r3-f-decision-event-v1",
                "target": target_id,
                "risk": risk_id,
                **event_payload,
            }
        )
        report_id = identity(
            {"contract": "r3-f-report-event-v1", "decision": decision_id, **event_payload}
        )
        receipt_id = identity({"contract": "r3-f-receipt-event-v1", "report": report_id})
        outcome_id = identity({"contract": "r3-f-outcome-event-v1", **event_payload})
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
        events.append(
            LifecycleEvent(
                event_time=event_time,
                next_event_time=next_time,
                active_state_identities=tuple(sorted(state.semantic_identity for state in active)),
                reviewed_state_identities=tuple(
                    sorted(state.semantic_identity for state in reviewed)
                ),
                expired_state_identities=tuple(
                    sorted(state.semantic_identity for state in expired)
                ),
                physical_position=((asset, target),),
                physical_delta=((asset, external),),
                transaction_cost=((asset, total - financing),),
                financing_cost=((asset, financing),),
                sleeve_allocations=allocations,
                target_identity=target_id,
                risk_state_identity=risk_id,
                decision_identity=decision_id,
                report_identity=report_id,
                receipt_identity=receipt_id,
                outcome_identities=(outcome_id,),
                sleeve_transaction_costs=transaction_allocation,
                sleeve_financing_costs=financing_allocation,
            )
        )
        for intent in intents:
            positions[intent.key] = intent.requested_quantity
        physical = target
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
    lifecycle_events = _build_lifecycle_events(decision, decision.horizon_states, _model("ASSET_A"))
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
