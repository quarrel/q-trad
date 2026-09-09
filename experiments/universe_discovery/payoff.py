# pyright: strict
"""Same-contract Decimal positions; scenarios reuse the single physical-change ledger."""

from __future__ import annotations

import bisect
import itertools
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from experiments.universe_discovery.atlas import Probe
from experiments.universe_discovery.panel import Bar, Market, sessions

ZERO = Decimal(0)


@dataclass(frozen=True)
class EntryState:
    bar: Bar
    scale: Decimal | None
    signal20: Decimal | None
    signal5: Decimal | None
    signal_at: datetime | None


def prepare(market: Market, rows: tuple[Bar, ...]) -> dict[date, EntryState]:
    """Incremental publication sweep; each same-contract change is prepared once."""
    ordered = tuple(sorted(rows, key=lambda b: (b.session, b.contract)))
    by_contract: dict[str, list[Bar]] = {}
    for bar in ordered:
        by_contract.setdefault(bar.contract, []).append(bar)
    events: list[tuple[datetime, date, str, Decimal]] = []
    for contract_rows in by_contract.values():
        for a, b in itertools.pairwise(contract_rows):
            if sessions(a.session, b.session) != (a.session, b.session):
                continue
            contract = market.contract(b.session, b.open_at)
            if contract is not None and contract.identity == b.contract:
                events.append(
                    (
                        max(a.available_at, b.available_at, contract.known_at),
                        b.session,
                        b.contract,
                        (b.close - a.close) * market.multiplier,
                    )
                )
    events.sort()
    cursor = 0
    available: list[tuple[date, str, Decimal]] = []
    signals: dict[str, list[tuple[date, Decimal, datetime]]] = {}
    result: dict[date, EntryState] = {}
    for bar in ordered:
        while cursor < len(events) and events[cursor][0] < bar.open_at:
            publication, day, identity, value = events[cursor]
            bisect.insort(available, (day, identity, value))
            bisect.insort(signals.setdefault(identity, []), (day, value, publication))
            cursor += 1
        current = market.contract(bar.session, bar.open_at)
        if current is None or current.identity != bar.contract:
            continue
        scale: Decimal | None = None
        if len(available) >= 60:
            values = [v for _, _, v in available[-60:]]
            mean = sum(values, ZERO) / 60
            variance = sum(((v - mean) ** 2 for v in values), ZERO) / 60
            candidate = variance.sqrt()
            if candidate > market.minimum_scale:
                scale = candidate
        recent = signals.get(bar.contract, [])

        def signal(
            count: int, history: list[tuple[date, Decimal, datetime]], day: date
        ) -> Decimal | None:
            window = history[-count:]
            if len(window) != count or len(sessions(window[0][0], day)) != count + 1:
                return None
            return sum((v for _, v, _ in window), ZERO)

        result[bar.session] = EntryState(
            bar,
            scale,
            signal(20, recent, bar.session),
            signal(5, recent, bar.session),
            max((publication for _, _, publication in recent[-20:]), default=None),
        )
    return result


@dataclass(frozen=True)
class SessionPayoff:
    session: date
    intent: int
    gross: Decimal
    fixed_cost: Decimal
    variable_cost: Decimal
    carry: Decimal
    scale: Decimal | None
    turnover: int
    trade_id: int | None
    signal_at: datetime | None
    entry_at: datetime | None
    exit_reason: str | None
    adverse_gap: Decimal

    def normalised(self, friction: Decimal) -> float:
        if self.scale is None:
            if self.intent != 0 or self.gross != 0:
                raise ValueError("Active outcome lacks a positive entry risk scale")
            return 0.0
        return float(
            (self.gross - self.fixed_cost - friction * self.variable_cost - self.carry) / self.scale
        )


@dataclass(frozen=True)
class Outcome:
    family: str
    sessions: tuple[SessionPayoff, ...]
    required_sessions: int
    missing_sessions: tuple[date, ...]
    unavailable_reason: str | None

    def score(self, friction: Decimal = Decimal(1)) -> float | None:
        if self.unavailable_reason is not None or self.missing_sessions or not self.sessions:
            return None
        return statistics.fmean(s.normalised(friction) for s in self.sessions)

    def summary(self, friction: Decimal = Decimal(1)) -> dict[str, object]:
        values = [s.normalised(friction) for s in self.sessions]
        complete = self.score(friction)
        total = 0.0
        high = 0.0
        drawdown = 0.0
        for value in values:
            total += value
            high = max(high, total)
            drawdown = min(drawdown, total - high)
        trades: dict[int, float] = {}
        for row, value in zip(self.sessions, values, strict=True):
            if row.trade_id is not None:
                trades[row.trade_id] = trades.get(row.trade_id, 0.0) + value
        worst_count = max(1, (len(values) + 19) // 20)
        ordered = sorted(values)
        return {
            "score": complete,
            "gross_score": statistics.fmean(
                float(s.gross / s.scale) if s.scale is not None else 0.0 for s in self.sessions
            )
            if self.sessions and complete is not None
            else None,
            "required_sessions": self.required_sessions,
            "observed_sessions": self.required_sessions - len(self.missing_sessions),
            "evaluated_sessions": len(self.sessions),
            "missing_sessions": self.missing_sessions,
            "coverage": (self.required_sessions - len(self.missing_sessions))
            / self.required_sessions
            if self.required_sessions
            else None,
            "unavailable_reason": self.unavailable_reason,
            "opportunities": self.required_sessions,
            "trades": len(trades) if complete is not None else None,
            "per_trade_expectancy": statistics.fmean(trades.values()) if trades else None,
            "exposure_sessions": sum(s.intent != 0 for s in self.sessions)
            if complete is not None
            else None,
            "turnover": sum(s.turnover for s in self.sessions) if complete is not None else None,
            "worst_session": min(values) if values and complete is not None else None,
            "quarter_sum": sum(values) if complete is not None else None,
            "drawdown": drawdown if complete is not None else None,
            "expected_shortfall_5pct": statistics.fmean(ordered[:worst_count])
            if ordered and complete is not None
            else None,
            "adverse_gap_money": str(max(s.adverse_gap for s in self.sessions))
            if complete is not None
            else None,
            "largest_5pct_absolute_move_sum": sum(sorted(values, key=abs)[-worst_count:])
            if complete is not None
            else None,
            "late_exit": "UNAVAILABLE: daily bars cannot identify a delayed close fill",
            "intraday_stop_depth_limit_exit": "UNRESOLVED",
        }


def positions(
    market: Market,
    prepared: dict[date, EntryState],
    cutoff: datetime,
    end: date,
    probe: Probe,
) -> Outcome:
    start = cutoff.date() + timedelta(days=1)
    schedule = sessions(start, end)
    if market.delisted is not None:
        schedule = tuple(day for day in schedule if day <= market.delisted)
    # Predetermined schedule never drops unexplained missing sessions.
    missing = tuple(day for day in schedule if day not in prepared)
    if market.cost is None:
        return Outcome(market.family, (), len(schedule), missing, "COST_UNRESOLVED")
    if missing:
        return Outcome(market.family, (), len(schedule), missing, "MISSING_REQUIRED_OUTCOME")
    ledger: list[SessionPayoff] = []
    direction = 0
    age = 0
    trade = 0
    scale: Decimal | None = None
    mark = ZERO
    contract_id: str | None = None
    signal_at: datetime | None = None
    entry_at: datetime | None = None
    horizon = 5 if probe == "continuation" else 1
    for i, day in enumerate(schedule):
        state = prepared[day]
        bar = state.bar
        contract = market.contract(day, bar.open_at)
        if contract is None:
            return Outcome(
                market.family, tuple(ledger), len(schedule), (day,), "CONTRACT_UNAVAILABLE"
            )
        turnover = 0
        entered = False
        if direction == 0:
            signal = state.signal20 if probe == "continuation" else state.signal5
            if signal is not None and signal != 0 and state.scale is not None:
                if (
                    state.signal_at is None
                    or not cutoff < bar.open_at
                    or not state.signal_at < bar.open_at
                ):
                    raise ValueError("Signal/cutoff must precede entry")
                direction = (1 if signal > 0 else -1) * (1 if probe == "continuation" else -1)
                scale = state.scale
                age = 0
                trade += 1
                mark = bar.open
                contract_id = bar.contract
                signal_at = state.signal_at
                entry_at = bar.open_at
                turnover += 1
                entered = True
        if direction != 0 and contract_id != bar.contract:
            raise ValueError("Attempted cross-contract outcome")
        gross = Decimal(direction) * (bar.close - mark) * market.multiplier if direction else ZERO
        gap = (
            max(ZERO, -Decimal(direction) * (bar.open - mark) * market.multiplier)
            if direction
            else ZERO
        )
        intent = direction
        reason: str | None = None
        if direction:
            age += 1
            tomorrow = schedule[i + 1] if i + 1 < len(schedule) else None
            next_contract = market.contract(tomorrow, bar.open_at) if tomorrow is not None else None
            if tomorrow is None:
                reason = "QUARTER_OR_LISTING_END"
            elif day.weekday() == 4:
                reason = "WEEKEND"
            elif next_contract is None or next_contract.identity != bar.contract:
                reason = "ROLL_OR_DELIVERY"
            elif age >= horizon:
                reason = "HORIZON"
            if reason is not None:
                turnover += 1
        ledger.append(
            SessionPayoff(
                day,
                intent,
                gross,
                market.commission * turnover,
                market.cost * turnover,
                market.carry if intent and not entered else ZERO,
                scale if intent else None,
                turnover,
                trade if intent else None,
                signal_at if intent else None,
                entry_at if intent else None,
                reason,
                gap,
            )
        )
        mark = bar.close
        if reason is not None:
            direction = 0
            scale = None
    return Outcome(market.family, tuple(ledger), len(schedule), (), None)
