from __future__ import annotations

from datetime import timedelta

import pytest

from qtrad.application.r3_evaluation import build_fixture_inputs, run_fixture
from qtrad.domain.r3_evaluation import EvaluationDisposition, evaluate_independently


def test_fixture_persists_authenticated_report_create_only(tmp_path):
    report = run_fixture(tmp_path)
    assert report.source_class.value == "IG_NATIVE_CAPTURE"
    assert report.evidence_purpose.value == "FIXTURE_IMPLEMENTATION"
    assert report.outcome_identities
    assert (tmp_path / "decision.json").exists()
    assert (tmp_path / "report.json").exists()
    with pytest.raises(FileExistsError):
        run_fixture(tmp_path)


def test_missing_executable_side_is_fail_closed():
    decision, quotes = build_fixture_inputs()
    report = evaluate_independently(
        decision,
        tuple(quote for quote in quotes if quote.asset_id == "ASSET_A"),
        latency=timedelta(seconds=1),
    )
    unavailable = next(item for item in report.assets if item.asset_id == "ASSET_B")
    assert unavailable.disposition is EvaluationDisposition.UNAVAILABLE
    assert "MISSING_QUOTE" in unavailable.reason_codes
