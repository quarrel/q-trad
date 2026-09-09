# pyright: strict
"""Past-only descriptors and frozen cohort membership for the synthetic H-DAY laboratory."""

from __future__ import annotations

import calendar
import itertools
import math
import random
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from experiments.universe_discovery.panel import Bar, Market, sessions

Probe = Literal["continuation", "reversal"]


def month_before(day: date, count: int) -> date:
    number = day.year * 12 + day.month - 1 - count
    year, month = divmod(number, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    if statistics.pstdev(left) == 0 or statistics.pstdev(right) == 0:
        return None
    return statistics.correlation(left, right)


def changes(bars: tuple[Bar, ...]) -> list[tuple[date, Decimal]]:
    return [
        (b.session, b.close - a.close)
        for a, b in itertools.pairwise(bars)
        if a.contract == b.contract and sessions(a.session, b.session) == (a.session, b.session)
    ]


@dataclass(frozen=True)
class Descriptor:
    family: str
    group: str
    coverage: float
    history: int
    volatility: float | None
    mean_range: float | None
    volatility_of_volatility: float | None
    efficiency: float | None
    lag_one: float | None
    adverse_gap: float | None
    drawdown: float | None
    liquidity: float | None
    open_interest: float | None
    range_to_cost: float | None
    percentage_domain: str
    eligible: bool
    reason: str
    nuisance: tuple[str, str, str, str, str]
    daily_changes: tuple[tuple[date, float], ...]


def describe(
    market: Market, rows: tuple[Bar, ...], cutoff: datetime, weeks: int | None = None
) -> Descriptor:
    from datetime import timedelta

    start = (
        month_before(cutoff.date(), 12) if weeks is None else cutoff.date() - timedelta(weeks=weeks)
    )
    past = tuple(
        b
        for b in rows
        if start <= b.session <= cutoff.date()
        and b.available_at <= cutoff
        and market.known_at <= cutoff
        and market.listed <= b.session
        and (market.delisted is None or b.session <= market.delisted)
        and (contract := market.contract(b.session, cutoff)) is not None
        and contract.identity == b.contract
    )
    # Coverage is against the frozen synthetic calendar, including pre-listing missing history.
    denominator = len(sessions(start, cutoff.date()))
    coverage = len(past) / denominator if denominator else 0.0
    moves = changes(past)
    values = [float(v) for _, v in moves]
    ranges = [float(b.high - b.low) for b in past]
    volumes = [float(b.volume) for b in past if b.volume is not None and b.volume_at <= cutoff]
    interests = [
        float(b.open_interest)
        for b in past
        if b.open_interest is not None and b.open_interest_at <= cutoff
    ]
    vol = statistics.pstdev(values) if len(values) >= 2 else None
    rolling = [statistics.pstdev(values[i - 20 : i]) for i in range(20, len(values) + 1)]
    total = sum(abs(x) for x in values)
    efficiency = abs(sum(values)) / total if total else None
    lag = correlation(values[:-1], values[1:])
    mean_range = statistics.fmean(ranges) if ranges else None
    range_cost = (
        float(
            Decimal(str(mean_range)) * market.multiplier / (2 * (market.cost + market.commission))
        )
        if mean_range is not None
        and market.cost is not None
        and market.cost + market.commission > 0
        else None
    )
    path = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        path += value
        peak = max(peak, path)
        drawdown = min(drawdown, path - peak)
    gaps = [
        abs(float(b.open - a.close))
        for a, b in itertools.pairwise(past)
        if a.contract == b.contract
    ]
    liquidity = statistics.fmean(volumes) if volumes else None
    reason = "ELIGIBLE"
    if market.known_at > cutoff or market.listed > cutoff.date():
        reason = "NOT_LISTED_AS_OF"
    elif market.delisted is not None and market.delisted <= cutoff.date():
        reason = "DELISTED"
    elif len(moves) < 60 or coverage < 0.9:
        reason = "HISTORY_OR_COVERAGE"
    elif market.cost is None:
        reason = "COST_UNRESOLVED"
    elif liquidity is None:
        reason = "LIQUIDITY_UNAVAILABLE"
    elif vol is None or Decimal(str(vol)) * market.multiplier <= market.minimum_scale:
        reason = "RISK_SCALE_UNAVAILABLE"
    elif market.contract(cutoff.date(), cutoff) is None:
        reason = "CONTRACT_UNAVAILABLE"
    return Descriptor(
        market.family,
        market.group,
        coverage,
        len(past),
        vol,
        mean_range,
        statistics.pstdev(rolling) if rolling else None,
        efficiency,
        lag,
        max(gaps) if gaps else None,
        drawdown if values else None,
        liquidity,
        statistics.fmean(interests) if interests else None,
        range_cost,
        "VALID"
        if past and all(b.close > 0 for b in past)
        else "UNAVAILABLE_NONPOSITIVE_OR_MISSING",
        reason == "ELIGIBLE",
        reason,
        (
            market.group,
            "liquid" if liquidity is not None and liquidity >= 1000 else "thin",
            "low_cost" if range_cost is not None and range_cost >= 10 else "high_cost",
            market.session_band,
            "full_year" if coverage >= 0.95 else "partial_year",
        ),
        tuple((day, float(value)) for day, value in moves),
    )


def clusters(descriptors: tuple[Descriptor, ...]) -> dict[str, str]:
    """Canonical connected components at |past correlation| >= .95, no outcome inputs."""
    result = {d.family: d.family for d in descriptors}
    for i, left in enumerate(descriptors):
        lm = dict(left.daily_changes)
        for right in descriptors[i + 1 :]:
            rm = dict(right.daily_changes)
            common = sorted(lm.keys() & rm.keys())
            corr = correlation([lm[d] for d in common], [rm[d] for d in common])
            if corr is not None and abs(corr) >= 0.95:
                old = result[right.family]
                new = result[left.family]
                result = {k: new if v == old else v for k, v in result.items()}
    return result


@dataclass(frozen=True)
class Cohorts:
    selected: tuple[str, ...]
    anchors: tuple[str, ...]
    simple: tuple[str, ...]
    broad: tuple[tuple[str, ...], ...]
    matched: tuple[tuple[str, ...], ...] | None
    clusters: dict[str, str]
    matching_reason: str
    overlaps: tuple[int, ...]
    anchor_size_matched: tuple[tuple[str, ...], ...] | None = None
    requested_size: int = 6
    selection_reason: str = "COMPLETE"


def select(
    descriptors: tuple[Descriptor, ...],
    markets: tuple[Market, ...],
    probe: Probe,
    size: int = 6,
    draws: int = 100,
    seed: int = 731,
) -> Cohorts:
    eligible = tuple(sorted((d for d in descriptors if d.eligible), key=lambda d: d.family))
    cluster = clusters(eligible)

    def score(d: Descriptor) -> float:
        value = (
            d.efficiency
            if probe == "continuation"
            else -d.lag_one
            if d.lag_one is not None
            else None
        )
        return value if value is not None and math.isfinite(value) else -math.inf

    selected: list[str] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for d in sorted(eligible, key=lambda d: (-score(d), d.family)):
        if len(selected) >= size:
            break
        if score(d) == -math.inf or cluster[d.family] in seen or counts.get(d.group, 0) >= 2:
            continue
        selected.append(d.family)
        seen.add(cluster[d.family])
        counts[d.group] = counts.get(d.group, 0) + 1
    chosen = tuple(selected)
    simple = tuple(
        d.family
        for d in sorted(
            eligible,
            key=lambda d: (
                -(d.range_to_cost if d.range_to_cost is not None else -math.inf),
                d.family,
            ),
        )[: len(chosen)]
    )
    anchor_ids = {m.family for m in markets if m.anchor}
    anchors = tuple(d.family for d in eligible if d.family in anchor_ids)
    anchor_views = (
        tuple(
            tuple(random.Random(seed + draw).sample(anchors, len(chosen))) for draw in range(draws)
        )
        if len(anchors) >= len(chosen) and chosen
        else None
    )
    by_id = {d.family: d for d in eligible}
    # Broad controls are economic-group-stratified with the selected group counts.
    group_pools = {
        d.group: tuple(e.family for e in eligible if e.group == d.group) for d in eligible
    }
    broad: list[tuple[str, ...]] = []
    matched: list[tuple[str, ...]] = []
    for draw in range(draws):
        rng = random.Random(seed + draw)
        broad.append(
            tuple(
                family
                for group, count in sorted(counts.items())
                for family in rng.sample(group_pools[group], count)
            )
        )
        # Exact nuisance cells are disjoint; sampling each cell without replacement
        # gives a complete bijection and cannot fail through greedy assignment.
        cells = {by_id[m].nuisance for m in chosen}
        assignments: dict[str, str] = {}
        for cell in sorted(cells):
            slots = [m for m in chosen if by_id[m].nuisance == cell]
            pool = [d.family for d in eligible if d.nuisance == cell]
            if len(pool) < len(slots):
                return Cohorts(
                    chosen,
                    anchors,
                    simple,
                    tuple(broad),
                    None,
                    cluster,
                    "INFEASIBLE_COMPLETE_MATCHING",
                    (),
                )
            assignments.update(zip(slots, rng.sample(pool, len(slots)), strict=True))
        matched.append(tuple(assignments[m] for m in chosen))
    return Cohorts(
        chosen,
        anchors,
        simple,
        tuple(broad),
        tuple(matched),
        cluster,
        "COMPLETE_EXACT_NUISANCE_CELLS",
        tuple(len(set(chosen) & set(draw)) for draw in matched),
        anchor_views,
        size,
        "COMPLETE" if len(chosen) == size else "INSUFFICIENT_DISTINCT_GROUP_CLUSTERS",
    )
