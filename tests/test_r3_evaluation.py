from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

import qtrad.application.r3_evaluation as evaluation_app
from qtrad.application.r3_evaluation import build_fixture_inputs, run_fixture
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.r3_evaluation import (
    EvaluationDisposition,
    OutcomeClosure,
    VerificationReceipt,
    build_outcome_closures,
    evaluate_independently,
    identity,
    reconcile_positions,
)
from qtrad.domain.r3_rounding import cost_states_identity


def test_fixture_persists_authenticated_report_create_only(tmp_path):
    report = run_fixture(tmp_path)
    assert report.source_class.value == "IG_NATIVE_CAPTURE"
    assert report.evidence_purpose.value == "FIXTURE_IMPLEMENTATION"
    assert report.outcome_identities
    assert report.expected_cost["ASSET_A"] == Decimal("0.0055")
    assert (tmp_path / "decision.json").exists()
    assert (tmp_path / "target.json").exists()
    assert (tmp_path / "target-receipt.json").exists()
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report-receipt.json").exists()
    assert not (tmp_path / "decision-receipt.json").exists()
    assert not (tmp_path / "outcome-ASSET_A-receipt.json").exists()
    with pytest.raises(FileExistsError):
        run_fixture(tmp_path)


def test_missing_executable_side_is_fail_closed():
    decision, quotes = build_fixture_inputs()
    report = evaluate_independently(
        decision,
        quotes[:1],
        latency=timedelta(seconds=1),
    )
    unavailable = next(item for item in report.assets if item.asset_id == "ASSET_A")
    assert unavailable.disposition is EvaluationDisposition.UNAVAILABLE
    assert "MISSING_QUOTE" in unavailable.reason_codes


def test_zero_delta_outcome_is_accepted_without_quotes():
    decision, _ = build_fixture_inputs()
    outcome = OutcomeClosure(
        "ASSET_A",
        decision.decision_time,
        decision.expiry_time,
        timedelta(seconds=1),
        None,
        None,
        EvaluationDisposition.ACCEPTED,
        source_class=decision.source_class,
        evidence_purpose=decision.evidence_purpose,
        decision_semantic_identity=decision.semantic_identity,
        decision_closure_identity=decision.closure_identity,
    )
    assert outcome.entry is None and outcome.exit is None


@pytest.mark.parametrize(
    ("disposition", "reason_codes"),
    (
        (EvaluationDisposition.ACCEPTED, ()),
        (EvaluationDisposition.UNAVAILABLE, ("MISSING_QUOTE",)),
        (EvaluationDisposition.BLOCKED, ("BLOCKED_BY_POLICY",)),
    ),
)
def test_quote_less_outcome_binds_source_evidence_and_decision(disposition, reason_codes):
    decision, _ = build_fixture_inputs()
    outcome = OutcomeClosure(
        asset_id="ASSET_A",
        decision_time=decision.decision_time,
        target_time=decision.expiry_time,
        latency=timedelta(seconds=1),
        entry=None,
        exit=None,
        disposition=disposition,
        reason_codes=reason_codes,
        physical_delta=Decimal("0"),
        source_class=decision.source_class,
        evidence_purpose=decision.evidence_purpose,
        decision_semantic_identity=decision.semantic_identity,
        decision_closure_identity=decision.closure_identity,
    )
    assert outcome.canonical_payload["source_class"] is decision.source_class
    assert outcome.canonical_payload["evidence_purpose"] is decision.evidence_purpose
    assert outcome.canonical_payload["decision_semantic_identity"] == decision.semantic_identity
    assert outcome.canonical_payload["decision_closure_identity"] == decision.closure_identity
    for field in ("source_class", "evidence_purpose"):
        with pytest.raises(ValueError, match="declared enums"):
            replace(outcome, **{field: None}, closure_identity="")


def test_fixture_rejects_stale_persisted_outcome_parent(monkeypatch, tmp_path):
    original = evaluation_app.build_outcome_closures

    def stale_outcomes(*args, **kwargs):
        return tuple(
            replace(item, decision_semantic_identity="0" * 64, closure_identity="")
            for item in original(*args, **kwargs)
        )

    monkeypatch.setattr(evaluation_app, "build_outcome_closures", stale_outcomes)
    with pytest.raises(ValueError, match="persisted outcome does not bind"):
        run_fixture(tmp_path)


def test_fixture_rejects_stale_report_parent(monkeypatch, tmp_path):
    original = evaluation_app.evaluate_independently

    def stale_report(*args, **kwargs):
        return replace(original(*args, **kwargs), decision_identity="0" * 64)

    monkeypatch.setattr(evaluation_app, "evaluate_independently", stale_report)
    with pytest.raises(ValueError, match="evaluation report does not bind"):
        run_fixture(tmp_path)


def test_bad_fx_fails_closed():
    decision, quotes = build_fixture_inputs()
    report = evaluate_independently(
        decision, quotes, latency=timedelta(seconds=1), fx_translation={"ASSET_A": Decimal("-1")}
    )
    asset = report.assets[0]
    assert asset.disposition is EvaluationDisposition.UNAVAILABLE
    assert "BAD_FX" in asset.reason_codes


def test_attribution_mutation_is_rejected():
    decision, _ = build_fixture_inputs()
    mutated = replace(
        decision.attributions[0],
        requested_delta=Decimal("0.9"),
        external_delta_share=Decimal("0.4"),
    )
    with pytest.raises(ValueError, match=r"rounded target|reconcile"):
        replace(decision, attributions=(mutated, *decision.attributions[1:]))


def test_stale_closed_and_incomplete_quotes_fail_closed():
    decision, quotes = build_fixture_inputs()
    for mutation, reason in (
        ({"healthy": False}, "STALE_QUOTE"),
        ({"session_open": False}, "CLOSED_SESSION"),
        ({"ask": None}, "INCOMPLETE_QUOTE"),
    ):
        candidates = tuple(replace(quote, **mutation) for quote in quotes)
        report = evaluate_independently(decision, candidates, latency=timedelta(seconds=1))
        asset = report.assets[0]
        assert asset.disposition is EvaluationDisposition.UNAVAILABLE
        assert reason in asset.reason_codes


def test_exit_quote_at_exact_target_time_is_valid():
    decision, quotes = build_fixture_inputs()
    target = decision.expiry_time
    candidates = tuple(
        replace(quote, received_time=target) if quote.received_time > target else quote
        for quote in quotes
    )
    report = evaluate_independently(decision, candidates, latency=timedelta(seconds=1))
    assert report.assets[0].disposition is EvaluationDisposition.ACCEPTED


def test_latency_movement_is_diagnostic_not_double_charged():
    decision, quotes = build_fixture_inputs()
    candidates = tuple(
        replace(quote, bid=Decimal("100.9"), ask=Decimal("101.1"))
        if quote.received_time == decision.decision_time + timedelta(seconds=2)
        else quote
        for quote in quotes
    )
    report = evaluate_independently(decision, candidates, latency=timedelta(seconds=1))
    pnl = report.assets[0].realised
    assert pnl is not None
    assert pnl.latency_movement == Decimal("0.5")
    assert pnl.net_pnl == pnl.gross_midpoint_pnl - pnl.total_cost


def test_report_rejects_forged_gross_bridge_and_nonpositive_notional():
    decision, quotes = build_fixture_inputs()
    report = evaluate_independently(decision, quotes, latency=timedelta(seconds=1))
    with pytest.raises(ValueError, match="gross contribution"):
        replace(
            report,
            gross_expected_contribution={"ASSET_A": Decimal("999")},
            expected_net_contribution={"ASSET_A": Decimal("998.9945")},
        )
    with pytest.raises(ValueError, match="physical notional"):
        replace(report, physical_notional={"ASSET_A": Decimal("-50")})


def test_builder_and_evaluator_share_missing_decision_reference_disposition():
    decision, quotes = build_fixture_inputs()
    built = build_outcome_closures(decision, quotes[1:], latency=timedelta(seconds=1))
    assert built[0].disposition is EvaluationDisposition.UNAVAILABLE
    assert "MISSING_DECISION_REFERENCE" in built[0].reason_codes
    report = evaluate_independently(decision, quotes[1:], latency=timedelta(seconds=1))
    assert report.assets[0].disposition is EvaluationDisposition.UNAVAILABLE
    assert "MISSING_DECISION_REFERENCE" in report.assets[0].reason_codes


def test_decision_requires_target_receipt_and_full_sleeve_keys():
    decision, _ = build_fixture_inputs()
    with pytest.raises(ValueError, match="rounded target"):
        replace(decision, rounded_target=None)
    with pytest.raises(ValueError, match="verification identity"):
        replace(decision, target_verification_identity="")
    keyless = replace(decision.attributions[0], key=None)
    with pytest.raises(ValueError, match="full sleeve keys"):
        replace(decision, attributions=(keyless, *decision.attributions[1:]))


def test_decision_requires_aud_reporting_currency():
    decision, _ = build_fixture_inputs()
    with pytest.raises(ValueError, match="must be AUD"):
        replace(decision, reporting_currency="USD")


@pytest.mark.parametrize("price_basis", (PriceBasis.BID, PriceBasis.ASK))
def test_non_mid_quote_evidence_is_unavailable(price_basis):
    decision, quotes = build_fixture_inputs()
    candidates = tuple(replace(quote, price_basis=price_basis) for quote in quotes)
    report = evaluate_independently(decision, candidates, latency=timedelta(seconds=1))
    asset = report.assets[0]
    assert asset.disposition is EvaluationDisposition.UNAVAILABLE
    assert "UNSUPPORTED_PRICE_BASIS" in asset.reason_codes
    assert asset.realised is None
    outcomes = build_outcome_closures(decision, candidates, latency=timedelta(seconds=1))
    assert outcomes[0].disposition is EvaluationDisposition.UNAVAILABLE
    assert "UNSUPPORTED_PRICE_BASIS" in outcomes[0].reason_codes


def test_outcome_rejects_non_mid_quote():
    decision, quotes = build_fixture_inputs()
    reference, entry, exit_quote = quotes
    with pytest.raises(ValueError, match="MID"):
        OutcomeClosure(
            asset_id="ASSET_A",
            decision_time=decision.decision_time,
            target_time=decision.expiry_time,
            latency=timedelta(seconds=1),
            entry=replace(entry, price_basis=PriceBasis.ASK),
            exit=exit_quote,
            disposition=EvaluationDisposition.ACCEPTED,
            physical_delta=decision.physical_delta[0],
            decision_reference=reference,
            reference_to_entry_latency=entry.received_time - reference.received_time,
            source_class=decision.source_class,
            evidence_purpose=decision.evidence_purpose,
            decision_semantic_identity=decision.semantic_identity,
            decision_closure_identity=decision.closure_identity,
        )


def test_verification_receipt_rejects_forged_identity():
    receipt = VerificationReceipt(
        artefact_contract="fixture-artifact-v1",
        semantic_identity=identity({"semantic": "fixture"}),
        closure_identity=identity({"closure": "fixture"}),
        parent_verification_identity=identity({"parent": "fixture"}),
        verifier_contract="fixture-verifier-v1",
        checks=("canonical-bytes",),
    )
    assert receipt.receipt_identity == identity(receipt.canonical_payload)
    with pytest.raises(ValueError, match="canonical payload"):
        replace(receipt, receipt_identity="0" * 64)


def test_nonzero_r3d_repair_reconciles_and_reports_allocations():
    decision, quotes = build_fixture_inputs()
    target = decision.rounded_target
    assert target is not None
    target_quantity = Decimal("0.6")
    model = decision.cost_models["ASSET_A"]
    expected_state = model.evaluate(
        current_quantity=decision.current_position[0],
        target_quantity=target_quantity,
        decision_time=decision.decision_time,
        internal_cross_quantity=Decimal("0.5"),
    )
    expected_costs = {"ASSET_A": expected_state}
    financing = next(
        component.reporting_amount
        for component in expected_state.components
        if component.component.value == "FINANCING"
    )
    if financing is None:
        raise AssertionError("fixture financing component has no total")
    total_cost = expected_state.require_total_reporting()
    if total_cost is None:
        raise AssertionError("fixture cost state has no total")
    long_target = replace(
        target.attributions[0],
        external_delta_share=Decimal("0.6"),
        repair_delta=Decimal("0.1"),
    )
    short_target = replace(
        target.attributions[1], external_delta_share=Decimal("0"), repair_delta=Decimal("0")
    )
    repaired_target = replace(
        target,
        target_position=(target_quantity,),
        physical_delta=(target_quantity,),
        expected_costs=expected_costs,
        expected_cost_reporting=total_cost - financing,
        expected_financing_reporting=financing,
        attributions=(long_target, short_target),
        cost_state_identity=cost_states_identity(expected_costs),
    )
    repaired_attributions = (
        replace(
            decision.attributions[0],
            external_delta_share=Decimal("0.6"),
            repair_delta=Decimal("0.1"),
        ),
        replace(
            decision.attributions[1],
            external_delta_share=Decimal("0"),
            repair_delta=Decimal("0"),
        ),
    )
    repaired_decision = replace(
        decision,
        target_position=(target_quantity,),
        physical_delta=(target_quantity,),
        expected_costs=expected_costs,
        attributions=repaired_attributions,
        rounded_target=repaired_target,
    )

    assert reconcile_positions(repaired_decision) == {"ASSET_A": Decimal("0")}
    report = evaluate_independently(repaired_decision, quotes, latency=timedelta(seconds=1))
    allocations = {item.sleeve_id: item for item in report.sleeve_allocations}
    assert allocations["long"].physical_delta == Decimal("0.6")
    assert allocations["long"].repair_delta == Decimal("0.1")
    assert allocations["short"].physical_delta == Decimal("0")
    assert allocations["short"].repair_delta == Decimal("0")


def test_legacy_fixture_identity_and_canonical_replay(tmp_path):
    first = run_fixture(tmp_path / "first")
    second = run_fixture(tmp_path / "second")
    assert first.semantic_identity == (
        "6368344e3a73de55a022da25ed22ee5ba527cbf26597b67a066b935b139ceefb"
    )
    assert first.canonical_bytes == second.canonical_bytes
    assert first.semantic_identity == second.semantic_identity


def test_multihorizon_lifecycle_nets_once_per_event_and_finances_held_sleeves(tmp_path):
    report = run_fixture(tmp_path, (5, 15, 30, 60))
    decision, _ = build_fixture_inputs((5, 15, 30, 60))
    events = report.lifecycle_events
    assert events is not None
    assert tuple(event.event_time for event in events) == (
        decision.decision_time,
        *sorted({item.expiry_time for item in decision.horizon_states}),
    )
    assert events[-1].physical_position == (("ASSET_A", Decimal("0")),)
    assert events[-1].physical_delta == (("ASSET_A", Decimal("-0.125")),)
    for index, event in enumerate(events):
        previous = Decimal("0") if index == 0 else events[index - 1].physical_position[0][1]
        assert event.physical_position[0][1] - previous == event.physical_delta[0][1]
        assert (
            sum((value for _, value in event.sleeve_transaction_costs), Decimal("0"))
            == event.transaction_cost[0][1]
        )
        assert (
            sum((value for _, value in event.sleeve_financing_costs), Decimal("0"))
            == event.financing_cost[0][1]
        )
        assert event.target_identity and event.cost_state_identity
        assert event.netting_identity and event.decision_identity
    assert any(
        sum((value for _, value in event.sleeve_financing_costs), Decimal("0")) > 0
        for event in events[1:]
    )


def test_lifecycle_event_identity_chain_rejects_mutation(tmp_path):
    report = run_fixture(tmp_path, (5, 15, 30))
    events = report.lifecycle_events
    assert events is not None
    with pytest.raises(ValueError, match="identity chain"):
        replace(events[0], target_identity="0" * 64)


def test_horizon_permutation_replays_identical_canonical_report(tmp_path):
    canonical = run_fixture(tmp_path / "canonical", (5, 15, 30, 60))
    permuted = run_fixture(tmp_path / "permuted", (60, 30, 5, 15))
    assert canonical.canonical_bytes == permuted.canonical_bytes
    assert canonical.semantic_identity == permuted.semantic_identity


def test_lifecycle_cost_largest_remainder_uses_stable_ties_without_residual():
    weights = (
        ("sleeve-a", Decimal("1")),
        ("sleeve-b", Decimal("1")),
        ("sleeve-c", Decimal("1")),
    )
    allocated = evaluation_app._allocate_lifecycle_cost(Decimal("4e-28"), weights, Decimal("3"))
    assert allocated == (
        ("sleeve-a", Decimal("2e-28")),
        ("sleeve-b", Decimal("1e-28")),
        ("sleeve-c", Decimal("1e-28")),
    )
    assert sum((value for _, value in allocated), Decimal("0")) == Decimal("4e-28")


def test_lifecycle_report_rejects_omitted_or_stale_event_sequence(tmp_path):
    report = run_fixture(tmp_path, (5, 15, 30, 60))
    events = report.lifecycle_events
    assert events is not None
    with pytest.raises(ValueError, match="required"):
        replace(report, lifecycle_events=())
    with pytest.raises(ValueError, match="close all physical positions"):
        replace(report, lifecycle_events=events[:-1])


@pytest.mark.parametrize(
    "field",
    (
        "target_identity",
        "risk_state_identity",
        "outcome_identities",
        "report_identity",
        "receipt_identity",
    ),
)
def test_lifecycle_event_rejects_forged_component_links(tmp_path, field):
    report = run_fixture(tmp_path, (5, 15, 30))
    events = report.lifecycle_events
    assert events is not None
    value = ("0" * 64,) if field == "outcome_identities" else "0" * 64
    with pytest.raises(ValueError, match="identity chain"):
        replace(events[0], **{field: value})


def test_lifecycle_crossing_and_no_trade_financing_are_explicit(tmp_path):
    report = run_fixture(tmp_path, (5, 15, 30, 60))
    events = report.lifecycle_events
    assert events is not None
    first = events[0]
    assert sum(
        (item.internal_cross_quantity for item in first.sleeve_allocations), Decimal("0")
    ) == Decimal("1.000")
    assert first.physical_delta == (("ASSET_A", Decimal("0.500")),)
    later = events[2]
    assert later.physical_delta == (("ASSET_A", Decimal("-0.125")),)
    assert later.financing_cost[0][1] > Decimal("0")
    assert any(value != Decimal("0") for _, value in later.sleeve_financing_costs)
