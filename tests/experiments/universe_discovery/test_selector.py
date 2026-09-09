# pyright: strict
"""Exact deterministic synthetic selector checks, not estimated empirical thresholds.

Predeclared tolerance: 1e-12 for arithmetic zero/unit payoff. No-edge has zero
after-friction payoff in every market. Planted classes earn one entry-risk unit;
all other classes earn zero. Continuation's planted class is AR(+.8), reversal's
AR(-.8), set by fixture construction before atlas/selection. One-session forward
support deliberately isolates selection from later adaptation and scale collapse.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from experiments.universe_discovery.atlas import Probe, describe, select
from experiments.universe_discovery.fixtures import fixture_panel
from experiments.universe_discovery.panel import index_panel, instant
from experiments.universe_discovery.payoff import positions, prepare
from experiments.universe_discovery.reducers import VintageScores, reduce

TOLERANCE = 1e-12
DRAWS = 100


@pytest.mark.parametrize("probe", ["continuation", "reversal"])
@pytest.mark.parametrize("planted", [False, True])
def test_actual_selector_no_edge_and_planted_advantage(probe: Probe, planted: bool) -> None:
    markets, bars = fixture_panel(24)
    indexed = index_panel(bars)
    cutoff, forward = instant(date(2018, 12, 31), 23), date(2019, 1, 1)
    descriptors = tuple(describe(m, indexed[m.family], cutoff) for m in markets)
    cohort = select(descriptors, markets, probe, draws=DRAWS, seed=731)
    assert cohort.selection_reason == "COMPLETE"
    assert cohort.matched is not None
    assert len(cohort.broad) == len(cohort.matched) == DRAWS
    assert cohort.anchor_size_matched is not None
    assert len(cohort.anchor_size_matched) == DRAWS
    # Forward labels depend only on predeclared fixture class, never selected IDs.
    planted_class = 0 if probe == "continuation" else 1
    expected_class = {m.family for i, m in enumerate(markets) if (i // 6) % 3 == planted_class}
    scores: dict[str, float | None] = {}
    calculations = 0
    for market in markets:
        original = indexed[market.family]
        state = prepare(market, original)[forward]
        signal = state.signal20 if probe == "continuation" else state.signal5
        assert state.scale is not None and signal is not None and signal != 0
        direction = (1 if signal > 0 else -1) * (1 if probe == "continuation" else -1)
        reward = Decimal(int(planted and market.family in expected_class))
        assert market.cost is not None
        roundtrip = 2 * (market.cost + market.commission)
        opened = state.bar.open
        closed = (
            opened + Decimal(direction) * (reward * state.scale + roundtrip) / market.multiplier
        )
        forward_bar = replace(
            state.bar,
            close=closed,
            high=max(opened, closed) + Decimal(".1"),
            low=min(opened, closed) - Decimal(".1"),
        )
        rows = tuple(forward_bar if b.session == forward else b for b in original)
        outcome = positions(market, prepare(market, rows), cutoff, forward, probe)
        score = outcome.score()
        assert score is not None and abs(score - float(reward)) < TOLERANCE
        scores[market.family] = score
        calculations += 1
    assert calculations == len(markets)  # Every draw references this single shared score map.
    reduced = reduce(
        (
            VintageScores(
                "SYNTHETIC_2019_Q1_ONE_SESSION",
                cohort.selected,
                cohort.anchors,
                cohort.simple,
                cohort.broad,
                cohort.matched,
                scores,
                {m.family: m.group for m in markets},
            ),
        )
    )
    assert reduced["status"] == "SCORABLE"
    for draw in cohort.anchor_size_matched:
        assert len(draw) == len(cohort.selected)
        assert all(scores[m] is not None for m in draw)
        mean = 0.0
        for member in draw:
            value = scores[member]
            assert value is not None
            mean += value / len(draw)
        assert -TOLERANCE <= mean <= (1 if planted else 0) + TOLERANCE
    if not planted:
        assert all(abs(score) < TOLERANCE for score in scores.values() if score is not None)
        assert all(
            delta is not None and abs(delta) < TOLERANCE
            for delta in reduced["overall_deltas"].values()
        )
    else:
        assert set(cohort.selected) <= expected_class
        # A comparator selecting equally good markets may tie. Broad and nuisance
        # controls must show strictly positive selector advantage without tuning.
        assert all(
            delta is not None and delta >= -TOLERANCE
            for delta in reduced["overall_deltas"].values()
        )
        for comparator in ("broad", "matched"):
            delta = reduced["overall_deltas"][comparator]
            assert delta is not None and delta > 0
