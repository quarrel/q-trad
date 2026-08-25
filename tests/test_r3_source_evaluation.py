from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrad.application.r3_source_evaluation import (
    build_fixture_inputs,
    build_r3i_readiness_input,
    evaluate_fixture,
)
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r3_evaluation import identity
from qtrad.domain.r3_source_evaluation import (
    SourceAlignedOutcome,
    SourceEvaluationClassification,
    SourceOutcomeDisposition,
    SourceQuote,
    SourceResultKind,
    SourceVerificationReceipt,
)


def test_fixture_is_source_aligned_and_decimal_reconciled() -> None:
    report = evaluate_fixture()

    assert report.authority.classification is SourceEvaluationClassification.FIXTURE_IMPLEMENTATION
    assert report.result_kind is SourceResultKind.EXECUTABLE
    assert report.gross_total == Decimal("0.0100")
    assert report.cost_total == Decimal("0.0015")
    assert report.net_total == Decimal("0.0085")
    assert report.outcomes[1].disposition is SourceOutcomeDisposition.UNAVAILABLE


def test_fixture_identity_is_deterministic() -> None:
    first = evaluate_fixture()
    second = evaluate_fixture()

    assert first.semantic_identity == second.semantic_identity
    assert first.closure_identity == second.closure_identity
    assert first.canonical_bytes == second.canonical_bytes


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_product_id", "OTHER-PRODUCT"),
        ("session_version", "other-session"),
        ("product_economics_identity", "a" * 64),
    ),
)
def test_authority_identity_mutations_cannot_borrow_source(field: str, value: str) -> None:
    inputs = build_fixture_inputs()
    mutated = replace(inputs.authority, **{field: value})

    with pytest.raises(ValueError, match="quote does not bind"):
        SourceAlignedOutcome(
            asset_id=inputs.outcomes[0].asset_id,
            authority=mutated,
            target_time=inputs.outcomes[0].target_time,
            physical_delta=Decimal("1"),
            entry=inputs.outcomes[0].entry,
            exit=inputs.outcomes[0].exit,
            disposition=SourceOutcomeDisposition.ACCEPTED,
            gross_return=Decimal("0.01"),
        )


def test_source_class_mutation_is_rejected() -> None:
    inputs = build_fixture_inputs()
    entry = inputs.outcomes[0].entry
    assert entry is not None
    quote = replace(
        entry,
        source_class=MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
    )
    with pytest.raises(ValueError, match="quote does not bind"):
        replace(inputs.outcomes[0], entry=quote)


def test_receive_time_and_executable_evidence_are_causal() -> None:
    inputs = build_fixture_inputs()
    entry = inputs.outcomes[0].entry
    assert entry is not None

    with pytest.raises(ValueError, match="latency boundary"):
        replace(
            inputs.outcomes[0],
            entry=replace(
                entry,
                received_time=inputs.authority.decision_time + timedelta(seconds=1),
            ),
        )

    with pytest.raises(ValueError, match="exit quote"):
        replace(
            inputs.outcomes[0],
            exit=replace(entry, received_time=inputs.outcomes[0].target_time),
        )


def test_historical_midpoint_cannot_emit_executable() -> None:
    with pytest.raises(ValueError, match="historical"):
        SourceQuote(
            asset_id="ASSET_A",
            source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
            source_product_id="IBKR-HIST",
            product_economics_identity="a" * 64,
            session_version="historical-v1",
            received_time=datetime(2026, 1, 1, tzinfo=UTC),
            bid=Decimal("100"),
            ask=Decimal("101"),
        )


def test_unavailable_stale_and_late_quotes_are_recordable() -> None:
    inputs = build_fixture_inputs()
    entry = inputs.outcomes[0].entry
    assert entry is not None
    stale = replace(entry, healthy=False)
    unavailable = SourceAlignedOutcome(
        asset_id="ASSET_A",
        authority=inputs.authority,
        target_time=inputs.outcomes[0].target_time,
        physical_delta=Decimal("1"),
        entry=stale,
        exit=None,
        disposition=SourceOutcomeDisposition.UNAVAILABLE,
        reason_codes=("STALE_ENTRY_QUOTE",),
    )
    assert unavailable.executable is False


def test_missing_unavailable_outcome_requires_reason() -> None:
    inputs = build_fixture_inputs()
    with pytest.raises(ValueError, match="requires a reason"):
        SourceAlignedOutcome(
            asset_id="ASSET_A",
            authority=inputs.authority,
            target_time=inputs.outcomes[0].target_time,
            physical_delta=Decimal("1"),
            entry=None,
            exit=None,
            disposition=SourceOutcomeDisposition.UNAVAILABLE,
        )


def test_receipt_binds_report_identity() -> None:
    report = evaluate_fixture()
    receipt = SourceVerificationReceipt(
        semantic_identity=report.semantic_identity,
        closure_identity=report.closure_identity,
        parent_verification_identity=report.authority.parent_verification_identity,
        classification=report.authority.classification,
        checks=("source-bound",),
    )
    assert len(receipt.receipt_identity) == 64
    with pytest.raises(ValueError, match="receipt identity"):
        replace(receipt, receipt_identity="f" * 64)


def test_readiness_is_future_native_and_never_execution_authority() -> None:
    readiness = build_r3i_readiness_input()

    assert readiness.classification is SourceEvaluationClassification.FUTURE_NATIVE_DECISION_GRADE
    assert readiness.native_execution_authority is False
    with pytest.raises(ValueError, match="cannot grant"):
        replace(readiness, native_execution_authority=True)


def test_readiness_classification_and_source_are_bound() -> None:
    readiness = build_r3i_readiness_input()
    with pytest.raises(ValueError, match="classification"):
        replace(readiness, classification=SourceEvaluationClassification.FIXTURE_IMPLEMENTATION)
    with pytest.raises(ValueError, match="native source"):
        replace(
            readiness,
            source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        )


def test_authority_classification_confusion_is_rejected() -> None:
    inputs = build_fixture_inputs()
    with pytest.raises(ValueError, match="historical"):
        replace(
            inputs.authority,
            source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
            classification=SourceEvaluationClassification.HISTORICAL_EXPLORATORY,
        )
    with pytest.raises(ValueError, match="future native"):
        replace(
            inputs.authority,
            classification=SourceEvaluationClassification.FUTURE_NATIVE_DECISION_GRADE,
            executable_quote_authority=False,
        )


def test_r3g_cli_is_create_only(tmp_path) -> None:
    output = tmp_path / "r3-g"
    command = [
        sys.executable,
        "-m",
        "qtrad",
        "r3-g",
        "--output",
        str(output),
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    assert "semantic_identity" in first.stdout
    second = subprocess.run(command, check=False, capture_output=True, text=True)
    assert second.returncode != 0
    assert "already exists" in second.stderr


def test_r3i_readiness_identity_changes_with_quote_evidence() -> None:
    readiness = build_r3i_readiness_input()
    replacement = replace(
        readiness,
        executable_quote_identities=(identity({"replacement": True}),),
    )
    assert replacement.semantic_identity != readiness.semantic_identity
