from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrad.application.r3_evaluation import build_fixture_inputs, run_fixture
from qtrad.domain.r3_evaluation import (
    EvaluationDisposition,
    OutcomeClosure,
    build_outcome_closures,
    evaluate_independently,
)


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
    outcome = OutcomeClosure(
        "ASSET_A",
        datetime(2025, 1, 2, tzinfo=UTC),
        datetime(2025, 1, 2, 0, 15, tzinfo=UTC),
        timedelta(seconds=1),
        None,
        None,
        EvaluationDisposition.ACCEPTED,
    )
    assert outcome.entry is None and outcome.exit is None


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
