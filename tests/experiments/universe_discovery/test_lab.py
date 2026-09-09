# pyright: strict
import itertools
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from experiments.universe_discovery.atlas import describe, select
from experiments.universe_discovery.fixtures import fixture_panel
from experiments.universe_discovery.panel import (
    Bar,
    Contract,
    Market,
    SyntheticPartition,
    index_panel,
    instant,
    load_panel,
    sessions,
    write_panel,
)
from experiments.universe_discovery.payoff import EntryState, positions, prepare


@pytest.fixture(scope="module")
def invented() -> tuple[tuple[Market, ...], tuple[Bar, ...]]:
    return fixture_panel(12)


def test_future_mutation_cannot_change_eligibility_cohort_or_entry_history(
    invented: tuple[tuple[Market, ...], tuple[Bar, ...]],
) -> None:
    markets, bars = invented
    cutoff = instant(date(2018, 12, 31), 23)
    indexed = index_panel(bars)
    mutated = tuple(
        replace(
            b,
            open=b.open + 500,
            close=b.close + 500,
            high=b.high + 500,
            low=b.low + 500,
            volume=Decimal(999999),
        )
        if b.available_at > cutoff
        else b
        for b in bars
    )
    mi = index_panel(mutated)
    ds = tuple(describe(m, indexed[m.family], cutoff) for m in markets)
    md = tuple(describe(m, mi[m.family], cutoff) for m in markets)
    assert ds == md
    for probe in ("continuation", "reversal"):
        assert select(ds, markets, probe) == select(md, markets, probe)
    before = prepare(markets[0], indexed[markets[0].family])
    after = prepare(markets[0], mi[markets[0].family])
    assert {d: v for d, v in before.items() if d <= cutoff.date()} == {
        d: v for d, v in after.items() if d <= cutoff.date()
    }


def test_delayed_fields_listing_delisting_and_missing_cost(
    invented: tuple[tuple[Market, ...], tuple[Bar, ...]],
) -> None:
    markets, bars = invented
    market = markets[0]
    rows = index_panel(bars)[market.family]
    cutoff = instant(date(2018, 12, 31), 23)
    delayed = tuple(
        replace(
            b,
            volume_at=max(b.volume_at, cutoff + timedelta(days=1)),
            open_interest_at=max(b.open_interest_at, cutoff + timedelta(days=2)),
        )
        for b in rows
    )
    d = describe(market, delayed, cutoff)
    assert d.reason == "LIQUIDITY_UNAVAILABLE"
    assert d.open_interest is None
    assert (
        describe(replace(market, listed=date(2019, 1, 1)), rows, cutoff).reason
        == "NOT_LISTED_AS_OF"
    )
    assert describe(replace(market, delisted=date(2018, 12, 1)), rows, cutoff).reason == "DELISTED"
    assert describe(replace(market, cost=None), rows, cutoff).reason == "COST_UNRESOLVED"
    late = tuple(replace(b, available_at=instant(date(2025, 1, 1))) for b in rows)
    prepared = prepare(market, late)
    assert all(s.scale is None and s.signal5 is None for s in prepared.values())


def test_predecode_reserve_and_unapproved_input_denied(tmp_path: Path) -> None:
    partition = SyntheticPartition(date(2018, 1, 1), date(2020, 12, 31), date(2021, 1, 1))
    absent = tmp_path / "must-not-open.parquet"
    with pytest.raises(PermissionError, match="synthetic development"):
        load_panel(absent, partition, date(2021, 1, 1), date(2021, 1, 2), "")
    with pytest.raises(PermissionError, match="NONE_APPROVED"):
        load_panel(
            absent, replace(partition, source="EMPIRICAL"), date(2019, 1, 1), date(2019, 1, 2), ""
        )


def test_parquet_identity_and_invalid_prices(
    tmp_path: Path,
    invented: tuple[tuple[Market, ...], tuple[Bar, ...]],
) -> None:
    _, bars = invented
    sample = bars[:5]
    path = tmp_path / "daily.parquet"
    checksum = write_panel(path, sample)
    partition = SyntheticPartition(sample[0].session, sample[-1].session, date(2021, 1, 1))
    assert load_panel(path, partition, partition.start, partition.end, checksum) == sample
    with pytest.raises(ValueError, match="checksum"):
        load_panel(path, partition, partition.start, partition.end, "bad")
    with pytest.raises(ValueError, match="Non-finite"):
        replace(sample[0], close=Decimal("NaN"))
    with pytest.raises(ValueError, match="OHLC"):
        replace(sample[0], high=sample[0].low - 1)
    with pytest.raises(ValueError, match="Adjusted"):
        replace(sample[0], adjustment="BACK_ADJUSTED")


def toy(
    signal: Decimal = Decimal(1), move: Decimal = Decimal(1), cost: Decimal = Decimal(0)
) -> tuple[Market, dict[date, EntryState]]:
    start, end = date(2019, 1, 7), date(2019, 1, 18)
    market = Market(
        "S",
        "energy",
        date(2000, 1, 1),
        None,
        instant(date(2000, 1, 1)),
        (Contract("C", date(2000, 1, 1), end, instant(date(2000, 1, 1))),),
        multiplier=Decimal(1),
        cost=cost,
        commission=Decimal(0),
        carry=Decimal(0),
    )
    result: dict[date, EntryState] = {}
    value = Decimal(-10)  # Nonpositive prices remain valid contract money.
    for day in sessions(start, end):
        close = value + move
        bar = Bar(
            "S",
            day,
            "C",
            value,
            max(value, close),
            min(value, close),
            close,
            instant(day, 14),
            instant(day),
            instant(day),
            Decimal(1000),
            instant(day),
            Decimal(1000),
            instant(day),
        )
        result[day] = EntryState(bar, Decimal(2), signal, signal, instant(day - timedelta(days=1)))
        value = close
    return market, result


def test_planted_zero_friction_and_single_position_money() -> None:
    market, states = toy()
    cutoff = instant(date(2019, 1, 6), 23)
    out = positions(market, states, cutoff, date(2019, 1, 18), "continuation")
    assert out.score() == 0.5
    assert len({s.trade_id for s in out.sessions}) == 2
    assert sum(s.turnover for s in out.sessions) == 4
    assert all(s.exit_reason == "WEEKEND" for s in out.sessions if s.session == date(2019, 1, 11))
    assert all(s.scale == Decimal(2) for s in out.sessions)
    assert sum(s.gross for s in out.sessions) == Decimal(10)
    reverse = positions(market, states, cutoff, date(2019, 1, 18), "reversal")
    assert reverse.score() == -0.5
    assert sum(s.turnover for s in reverse.sessions) == 20
    zero_market, zero_states = toy(move=Decimal(0))
    assert (
        positions(zero_market, zero_states, cutoff, date(2019, 1, 18), "continuation").score() == 0
    )
    costly = positions(
        replace(zero_market, cost=Decimal("0.5")),
        zero_states,
        cutoff,
        date(2019, 1, 18),
        "continuation",
    )
    assert costly.score() == -0.1
    assert costly.score(Decimal(2)) == -0.2
    flat_market, flat_states = toy(signal=Decimal(0))
    flat = positions(flat_market, flat_states, cutoff, date(2019, 1, 18), "continuation")
    assert flat.score() == 0
    assert len(flat.sessions) == 10
    assert all(s.turnover == 0 for s in flat.sessions)


def test_roll_delivery_missing_and_fixed_entry_scale() -> None:
    market, states = toy()
    known = instant(date(2000, 1, 1))
    market = replace(
        market,
        contracts=(
            Contract("C", date(2000, 1, 1), date(2019, 1, 9), known),
            Contract("D", date(2019, 1, 10), date(2019, 1, 18), known),
        ),
    )
    changed = {
        day: replace(
            state,
            bar=replace(
                state.bar,
                contract="D",
                open=state.bar.open + 1000,
                close=state.bar.close + 1000,
                high=state.bar.high + 1000,
                low=state.bar.low + 1000,
            ),
            scale=Decimal(999),
        )
        if day >= date(2019, 1, 10)
        else state
        for day, state in states.items()
    }
    out = positions(
        market, changed, instant(date(2019, 1, 6), 23), date(2019, 1, 18), "continuation"
    )
    assert out.sessions[2].exit_reason == "ROLL_OR_DELIVERY"
    assert sum(s.gross for s in out.sessions) == 10  # Never includes the1000-point splice.
    assert [s.scale for s in out.sessions[:3]] == [Decimal(2)] * 3
    del changed[date(2019, 1, 15)]
    missing = positions(
        market, changed, instant(date(2019, 1, 6), 23), date(2019, 1, 18), "continuation"
    )
    assert missing.score() is None
    assert missing.missing_sessions == (date(2019, 1, 15),)


def test_decimal_risk_and_nonpositive_descriptor_domain(
    invented: tuple[tuple[Market, ...], tuple[Bar, ...]],
) -> None:
    markets, bars = invented
    market = markets[0]
    rows = index_panel(bars)[market.family]
    shifted = tuple(
        replace(b, open=b.open - 2000, close=b.close - 2000, high=b.high - 2000, low=b.low - 2000)
        for b in rows
    )
    cutoff = instant(date(2018, 12, 31), 23)
    original = describe(market, rows, cutoff)
    negative = describe(market, shifted, cutoff)
    assert original.volatility == negative.volatility
    assert negative.percentage_domain == "UNAVAILABLE_NONPOSITIVE_OR_MISSING"
    prepared = prepare(market, rows)
    state = prepared[date(2019, 1, 1)]
    past = [b for b in rows if b.available_at < state.bar.open_at][-61:]
    monetary = [(b.close - a.close) * market.multiplier for a, b in itertools.pairwise(past)]
    average = sum(monetary, Decimal(0)) / 60
    expected = (sum(((v - average) ** 2 for v in monetary), Decimal(0)) / 60).sqrt()
    assert state.scale == expected
    assert state.scale is not None
    assert abs(float(state.scale) - float(expected)) < 1e-12
