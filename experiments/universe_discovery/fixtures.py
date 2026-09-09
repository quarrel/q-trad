# pyright: strict
"""Entirely invented prices, products, dates, sessions and nuisance labels."""

from datetime import date, timedelta
from decimal import Decimal
from random import Random

from experiments.universe_discovery.panel import Bar, Contract, Market, instant, sessions


def fixture_panel(families: int = 24) -> tuple[tuple[Market, ...], tuple[Bar, ...]]:
    if not 6 <= families <= 150:
        raise ValueError("Representative fixture supports 6-150 invented families")
    groups = ("rates", "equity", "fx", "energy", "metals", "agriculture")
    markets: list[Market] = []
    bars: list[Bar] = []
    days = sessions(date(2018, 1, 1), date(2020, 12, 31))
    for i in range(families):
        family = f"SYNTHETIC_{i:03d}"
        identity = f"{family}_CONTRACT"
        market = Market(
            family,
            groups[i % len(groups)],
            date(2010, 1, 1),
            None,
            instant(date(2010, 1, 1)),
            (Contract(identity, date(2010, 1, 1), date(2040, 12, 31), instant(date(2010, 1, 1))),),
            anchor=i < 20,
        )
        markets.append(market)
        rng = Random(9401 + i)
        level = Decimal(1000)
        prior_move = Decimal(0)
        coefficient = (Decimal("0.8"), Decimal("-0.8"), Decimal("0"))[(i // 6) % 3]
        for day in days:
            innovation = Decimal(rng.randrange(-100, 101)) / 100
            move = (coefficient * prior_move + innovation).quantize(Decimal("0.0001"))
            opened = level + Decimal(rng.randrange(-10, 11)) / 100
            closed = level + move
            bars.append(
                Bar(
                    family,
                    day,
                    identity,
                    opened,
                    max(opened, closed) + Decimal("0.1"),
                    min(opened, closed) - Decimal("0.1"),
                    closed,
                    instant(day, 14),
                    instant(day),
                    instant(day) + timedelta(hours=1),
                    Decimal(10000 + i),
                    instant(day) + timedelta(days=1),
                    Decimal(20000 + i),
                    instant(day) + timedelta(days=2),
                )
            )
            level = closed
            prior_move = move
    return tuple(markets), tuple(bars)
