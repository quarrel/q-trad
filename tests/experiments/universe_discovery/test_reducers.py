# pyright: strict
"""Discriminating fixtures for the plan's fixed reducers."""

from dataclasses import replace
from math import isclose

import pytest

from experiments.universe_discovery.reducers import VintageScores, reduce


def fixture(vintage: str, selected: dict[str, float]) -> VintageScores:
    members = tuple(selected)
    controls = tuple(f"control-{market}" for market in members)
    scores: dict[str, float | None] = dict(selected)
    scores.update(dict.fromkeys(controls, 0.0))
    return VintageScores(
        vintage,
        members,
        controls,
        controls,
        (controls,),
        (controls,),
        scores,
        dict.fromkeys(scores, "group"),
    )


def test_unequal_appearance_fixture() -> None:
    result = reduce([fixture(str(v), {"A": 3.0, f"B{v}": 1.0, f"C{v}": 1.0}) for v in range(8)])
    concentration = result["concentration"]
    effect = result["overall_deltas"]["matched"]
    market_share = concentration["best_market_share"]
    quarter_share = concentration["best_quarter_share"]
    assert effect is not None and isclose(effect, 5 / 3)
    assert isclose(concentration["market"]["A"], 1)
    assert market_share is not None and isclose(market_share, 0.60)
    assert quarter_share is not None and isclose(quarter_share, 0.125)
    assert concentration["selection_frequency"]["A"] == 8
    assert isclose(sum(concentration["market"].values()), 5 / 3)
    assert isclose(sum(concentration["quarter"].values()), 5 / 3)


def test_unequal_sizes_and_signed_cancellation_before_clipping() -> None:
    result = reduce([fixture("1", {"A": 4.0}), fixture("2", {"A": -8.0, "B": 4.0})])
    concentration = result["concentration"]
    assert result["overall_deltas"]["matched"] == 1
    assert concentration["market"] == {"A": 0, "B": 1}
    assert concentration["quarter"] == {"1": 2, "2": -1}
    assert concentration["best_market_share"] == 1
    assert concentration["best_quarter_share"] == 1


def test_all_draws_used_and_group_balanced_is_separate() -> None:
    vintage = fixture("1", {"A": 5.0, "B": 1.0, "C": 0.0})
    vintage.scores.update({"x": 3.0, "y": 3.0, "z": 3.0})
    vintage.groups.update({"x": "g", "y": "g", "z": "g", "A": "other"})
    draws = (vintage.anchors, ("x", "y", "z"))
    result = reduce([replace(vintage, broad=draws, matched=draws)])
    assert result["vintages"][0]["draws"]["matched"] == [0.0, 3.0]
    assert result["overall_deltas"]["matched"] == 0.5
    assert isclose(sum(result["concentration"]["market"].values()), 0.5)
    assert result["diagnostics"]["group_balanced_delta"] == 1.25


def test_missing_member_draw_and_failed_matching_remain_visible() -> None:
    good = fixture("good", {"A": 1.0, "B": 1.0})
    missing = replace(good, vintage="missing", scores={**good.scores, "B": None})
    bad_draw = replace(good, vintage="draw", matched=(good.anchors, ("A", "missing")))
    bad_draw.scores["missing"] = None
    bad_draw.groups["missing"] = "group"
    failed = replace(good, vintage="failed", matched=None)
    result = reduce([good, missing, bad_draw, failed])
    assert result["overall_deltas"]["matched"] == 1
    assert result["vintages"][1]["missing_selected_fraction"] == 0.5
    assert result["vintages"][1]["selected"] is None
    assert result["vintages"][2]["draws"]["matched"] == [0.0, None]
    assert result["vintages"][2]["deltas"]["matched"] is None
    assert result["vintages"][3]["unavailable"]["matched"] == "MATCHING_UNAVAILABLE"
    assert result["concentration"]["selection_frequency"] == {"A": 1, "B": 1}


def test_no_positive_contribution_and_no_scorable_vintage() -> None:
    negative = reduce([fixture("1", {"A": -1.0})])
    assert negative["concentration"]["best_market_share"] == 1
    assert negative["concentration"]["positive_market_contribution"] is False
    empty = reduce([])
    assert empty["status"] == "INSUFFICIENT_EVIDENCE"
    assert empty["overall_deltas"]["matched"] is None
    assert empty["concentration"]["best_market_share"] is None
    assert empty["diagnostics"]["quarter_bootstrap_interval"] == []


def test_missing_mapping_and_misaligned_slots_raise() -> None:
    vintage = fixture("1", {"A": 1.0})
    with pytest.raises(ValueError, match="matched slots"):
        reduce([replace(vintage, matched=(("A", "control-A"),))])
    with pytest.raises(KeyError):
        reduce([replace(vintage, scores={})])


def test_primary_requires_all_comparators_and_matched_sensitivity_is_separate() -> None:
    complete = fixture("complete", {"A": 2.0})
    incomplete = replace(fixture("incomplete", {"A": 6.0}), anchors=())
    result = reduce([complete, incomplete])
    assert result["overall_deltas"]["matched"] == 2.0
    assert result["diagnostics"]["matched_complete_delta"] == 4.0
    assert result["vintages"][1]["primary_scorable"] is False
    assert result["concentration"]["quarter"] == {"complete": 2.0}
