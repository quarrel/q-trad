"""Focused R3.H Stage 1 freeze and micro-run tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from qtrad.application.r3_historical_exploratory import (
    FixtureMeasurement,
    FreezeConfig,
    FreezeError,
    analyse_fixture,
    synthetic_fixture,
)

CONFIG = Path("docs/archive/r3/r3-historical-exploratory-freeze.json")


def _rehashed(document: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in document.items() if key != "semantic_identity"}
    document["semantic_identity"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return document


def test_freeze_is_deterministic_and_rejects_unknown_expansion_and_mutation() -> None:
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

    mutations = (
        ("cost_grid", lambda value: value[0].__setitem__("value", 0.002)),
        ("targets", lambda value: value.__setitem__(0, "MUTATED_TARGET")),
        (
            "statistical_formulations",
            lambda value: value.__setitem__("metrics", ["mutated_metric"]),
        ),
        (
            "retained_parents",
            lambda value: value.__setitem__("stage7_semantic_id", "mutated-parent"),
        ),
        (
            "nonlinear_candidates",
            lambda value: value[0].__setitem__("degree", 7),
        ),
        ("tiny_graph_candidate", lambda value: value.__setitem__("hidden_units", 8)),
        ("compute_limits", lambda value: value.__setitem__("max_rows", 63)),
        (
            "output_contract",
            lambda value: value.__setitem__("post_result_expansion", True),
        ),
    )
    for section, mutate in mutations:
        candidate = json.loads(first.canonical_json())
        mutate(candidate[section])
        with pytest.raises(FreezeError):
            FreezeConfig.from_mapping(_rehashed(candidate))


def test_fixture_micro_run_covers_economic_statistical_graph_and_labels() -> None:
    result = analyse_fixture(synthetic_fixture(), FreezeConfig.from_path(CONFIG))
    report = result.report
    assert {"economic", "statistical", "graph"} <= report.keys()
    assert len(report["economic"]["all_in_cost_sensitivity"]) == 4
    assert {"asset", "horizon", "period"} <= report["economic"].keys()
    assert report["economic"]["asset"]["A"]["turnover"] == 0.25
    assert report["economic"]["asset"]["B"]["turnover"] == 0.25
    assert report["economic"]["all_in_cost_sensitivity"][0]["break_even_cost"] is not None
    assert report["statistical"]["oof"]["causal"] is True
    assert report["statistical"]["oof"]["support"] == 4
    assert report["statistical"]["oof"]["coverage"] == 1.0
    assert report["statistical"]["negative_failed_inconclusive_rendered"] is True
    assert report["graph"]["tiny_learned_graph"]["feasibility_only"] is True
    assert report["graph"]["r4_replacement_required"] is True
    assert {control["id"] for control in report["graph"]["controls"]} == {
        "local_non_graph",
        "pooled_non_graph",
        "fixed_graph",
        "shuffled_graph",
    }
    for control in report["graph"]["controls"]:
        assert {"status", "mse", "rank_correlation", "coverage", "support"} <= control.keys()
    assert report["retained_parents"]["authentication_performed"] is False
    assert report["retained_parents"]["outcome_decode_performed"] is False
    assert report["claims"] == [
        "midpoint_only",
        "historical_exploratory",
        "implementation_evidence_only",
        "not_executable_evidence",
        "no_effectiveness_claim",
    ]


def test_synchronised_timestamps_are_allowed_but_duplicate_identity_fails() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    rows[1] = replace(rows[1], timestamp=rows[0].timestamp, asset=rows[0].asset, horizon_minutes=30)
    result = analyse_fixture(rows, config)
    assert result.report["statistical"]["oof"]["rows"] == 4

    rows[1] = replace(rows[1], horizon_minutes=rows[0].horizon_minutes, period=rows[0].period)
    with pytest.raises(FreezeError, match="duplicate decision identity"):
        analyse_fixture(rows, config)


def test_work_and_resource_limits_fail_closed() -> None:
    config = FreezeConfig.from_path(CONFIG)
    base = synthetic_fixture()[0]
    too_many_rows = tuple(
        replace(
            base,
            timestamp=f"2026-02-01T00:{index:02}:00Z",
            period=f"period-{index}",
        )
        for index in range(65)
    )
    with pytest.raises(FreezeError, match="max_rows"):
        analyse_fixture(too_many_rows, config)

    with pytest.raises(FreezeError, match="max_elapsed_seconds"):
        analyse_fixture(
            synthetic_fixture(),
            config,
            measurement=FixtureMeasurement(elapsed_seconds=61, memory_mb=1),
        )
    with pytest.raises(FreezeError, match="max_memory_mb"):
        analyse_fixture(
            synthetic_fixture(),
            config,
            measurement=FixtureMeasurement(elapsed_seconds=1, memory_mb=513),
        )


def _control(report: Mapping[str, Any], control_id: str) -> dict[str, Any]:
    return next(control for control in report["graph"]["controls"] if control["id"] == control_id)


def _simple_control(report: Mapping[str, Any], control_id: str) -> dict[str, Any]:
    return next(
        control
        for control in report["statistical"]["simple_controls"]
        if control["id"] == control_id
    )


def test_pooled_and_graph_controls_are_causal_and_use_frozen_graph() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    rows[1] = replace(rows[1], timestamp=rows[0].timestamp, asset="B")
    baseline = analyse_fixture(tuple(rows), config).report
    future = replace(
        rows[-1],
        timestamp="2026-02-01T00:10:00Z",
        period="period-future",
        prediction=9.0,
        realised_return=0.9,
    )
    extended = analyse_fixture((*rows, future), config).report

    baseline_pooled = _simple_control(baseline, "pooled_local_ridge")["prediction_trace"]
    extended_pooled = _simple_control(extended, "pooled_local_ridge")["prediction_trace"]
    assert extended_pooled[: len(rows)] == baseline_pooled

    baseline_tiny = baseline["graph"]["tiny_learned_graph"]["prediction_trace"]
    extended_tiny = extended["graph"]["tiny_learned_graph"]["prediction_trace"]
    assert extended_tiny[: len(rows)] == baseline_tiny
    assert baseline["graph"]["tiny_learned_graph"]["layers"] == 1
    assert baseline["graph"]["tiny_learned_graph"]["hidden_units"] == 4
    assert baseline["work"]["graph_fit_count"] == 1
    assert baseline["graph"]["tiny_learned_graph"]["walk_forward_fit_executions"] == 1

    fixed = _control(baseline, "fixed_graph")["prediction_trace"]
    shuffled = _control(baseline, "shuffled_graph")["prediction_trace"]
    assert fixed[0] == rows[1].prediction
    assert shuffled[0] == rows[1].prediction


def test_report_carries_frozen_parent_identities_and_code_provenance() -> None:
    config = FreezeConfig.from_path(CONFIG)
    report = analyse_fixture(synthetic_fixture(), config).report
    retained = config.document["retained_parents"]
    identities = report["retained_parents"]["identities"]
    for key in (
        "stage7_semantic_id",
        "stage7_dataset_id",
        "stage7_closure_id",
        "stage7_verification_id",
        "terminal_report_sha256",
        "terminal_approval_sha256",
        "stage8_promotion_id",
    ):
        assert identities[key] == retained[key]
    provenance = report["code_provenance"]
    assert provenance["application_contract"] == "qtrad-r3-historical-exploratory-implementation-v2"
    assert len(provenance["module_sha256"]) == 64
    assert len(provenance["python_version"].split(".")) == 3
