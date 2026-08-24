"""Focused R3.H Stage 1 freeze and micro-run tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qtrad.application.r3_historical_exploratory import (
    FreezeConfig,
    FreezeError,
    analyse_fixture,
    synthetic_fixture,
)

CONFIG = Path("docs/archive/r3/r3-historical-exploratory-freeze.json")


def test_freeze_is_deterministic_and_rejects_unknown_or_expanded_candidates() -> None:
    first = FreezeConfig.from_path(CONFIG)
    second = FreezeConfig.from_path(CONFIG)
    assert first.semantic_identity == second.semantic_identity
    assert first.canonical_json() == second.canonical_json()

    unknown = json.loads(first.canonical_json())
    unknown["unexpected"] = True
    with pytest.raises(FreezeError, match="unknown keys"):
        FreezeConfig.from_mapping(unknown)

    expanded = json.loads(first.canonical_json())
    expanded["nonlinear_candidates"].append(
        {"id": "extra", "family": "tree", "degree": 3, "enabled": True}
    )
    with pytest.raises(FreezeError, match="candidate expansion"):
        FreezeConfig.from_mapping(expanded)


def test_fixture_micro_run_covers_economic_statistical_graph_and_labels() -> None:
    result = analyse_fixture(synthetic_fixture(), FreezeConfig.from_path(CONFIG))
    report = result.report
    assert {"economic", "statistical", "graph"} <= report.keys()
    assert len(report["economic"]["all_in_cost_sensitivity"]) == 4
    assert {"asset", "horizon", "period"} <= report["economic"].keys()
    assert report["statistical"]["oof"]["causal"] is True
    assert report["statistical"]["negative_failed_inconclusive_rendered"] is True
    assert report["graph"]["tiny_learned_graph"]["feasibility_only"] is True
    assert report["graph"]["r4_replacement_required"] is True
    assert report["retained_parents"]["authentication_performed"] is False
    assert report["retained_parents"]["outcome_decode_performed"] is False
    assert report["claims"] == [
        "midpoint_only",
        "historical_exploratory",
        "implementation_evidence_only",
        "not_executable_evidence",
        "no_effectiveness_claim",
    ]


def test_chronology_and_work_limits_are_fail_closed() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    rows[1] = type(rows[1])(
        timestamp=rows[0].timestamp,
        asset=rows[1].asset,
        horizon_minutes=rows[1].horizon_minutes,
        period=rows[1].period,
        prediction=rows[1].prediction,
        realised_return=rows[1].realised_return,
        available_at=rows[1].available_at,
    )
    with pytest.raises(FreezeError, match="duplicate decision timestamp"):
        analyse_fixture(rows, config)

    limited = json.loads(config.canonical_json())
    limited["compute_limits"]["max_rows"] = 1
    payload = {key: value for key, value in limited.items() if key != "semantic_identity"}
    limited["semantic_identity"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    limited_config = FreezeConfig.from_mapping(limited)
    with pytest.raises(FreezeError, match="max_rows"):
        analyse_fixture(synthetic_fixture(), limited_config)
