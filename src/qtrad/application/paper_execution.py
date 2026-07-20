"""Causal bid/ask shadow-paper fills and isolated strategy ledgers."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import DataQuality, MarketQuote
from qtrad.domain.paper import (
    PaperInstrumentEconomics,
    PaperModel,
    PaperRoundTrip,
    StrategyLedger,
)
from qtrad.domain.strategy import Forecast


@dataclass(frozen=True, slots=True)
class PaperEvaluation:
    model: PaperModel
    economics_hashes: tuple[tuple[str, str], ...]
    round_trips: tuple[PaperRoundTrip, ...]
    ledgers: tuple[StrategyLedger, ...]


def evaluate_shadow_paper(
    forecasts: Sequence[Forecast],
    quotes: Sequence[MarketQuote],
    economics: Mapping[InstrumentId, PaperInstrumentEconomics],
    model: PaperModel,
) -> PaperEvaluation:
    if any(key != value.instrument_id for key, value in economics.items()):
        raise ValueError("paper economics mapping keys must match instrument identity")
    reporting_currencies = {item.reporting_currency for item in economics.values()}
    if len(reporting_currencies) != 1:
        raise ValueError("paper evaluation requires one explicit reporting currency")
    quotes_by_instrument: dict[InstrumentId, list[MarketQuote]] = defaultdict(list)
    for quote in sorted(
        quotes,
        key=lambda item: (
            item.received_time,
            item.global_position or 0,
            item.event_time,
            str(item.instrument_id),
            str(item.listing_id),
        ),
    ):
        if (
            quote.quality is DataQuality.HEALTHY
            and quote.bid is not None
            and quote.ask is not None
            and quote.instrument_id in economics
            and economics[quote.instrument_id].session_profile.allows(quote.received_time)
        ):
            quotes_by_instrument[quote.instrument_id].append(quote)

    round_trips: list[PaperRoundTrip] = []
    for forecast in sorted(
        forecasts,
        key=lambda item: (item.decision_time, item.strategy_id, str(item.instrument_id)),
    ):
        direction = _direction(forecast.strength)
        if direction == 0:
            continue
        instrument_economics = economics.get(forecast.instrument_id)
        if instrument_economics is None:
            continue
        instrument_quotes = quotes_by_instrument[forecast.instrument_id]
        entry = next(
            (
                quote
                for quote in instrument_quotes
                if quote.received_time > forecast.decision_time
                and quote.received_time >= forecast.decision_time + model.latency
                and quote.received_time < forecast.target_time
            ),
            None,
        )
        if entry is None:
            continue
        exit_quote = next(
            (
                quote
                for quote in instrument_quotes
                if quote.received_time >= forecast.target_time
                and quote.received_time > entry.received_time
            ),
            None,
        )
        if exit_quote is None:
            continue
        round_trips.append(
            _round_trip(forecast, direction, entry, exit_quote, instrument_economics, model)
        )

    reporting_currency = next(iter(reporting_currencies))
    return PaperEvaluation(
        model=model,
        economics_hashes=tuple(
            sorted((str(key), value.configuration_hash) for key, value in economics.items())
        ),
        round_trips=tuple(round_trips),
        ledgers=_ledgers(forecasts, round_trips, reporting_currency),
    )


def verify_paper_evaluation(
    evaluation: PaperEvaluation,
    economics: Mapping[InstrumentId, PaperInstrumentEconomics],
) -> None:
    expected_hashes = tuple(
        sorted((str(key), value.configuration_hash) for key, value in economics.items())
    )
    if evaluation.economics_hashes != expected_hashes:
        raise ValueError("paper evaluation economics identity does not verify")
    for trip in evaluation.round_trips:
        instrument_economics = economics[trip.instrument_id]
        factor = (
            instrument_economics.quantity
            * instrument_economics.value_per_price_unit
            * instrument_economics.quote_to_reporting_rate
        )
        expected_gross = Decimal(trip.direction) * (trip.exit_mid - trip.entry_mid) * factor
        expected_net = Decimal(trip.direction) * (trip.exit_price - trip.entry_price) * factor
        if trip.gross_mid_pnl != expected_gross or trip.net_pnl != expected_net:
            raise ValueError("paper round-trip P&L does not independently verify")
        if trip.execution_cost != expected_gross - expected_net:
            raise ValueError("paper round-trip execution cost does not independently verify")
        if trip.turnover != (trip.entry_price + trip.exit_price) * factor:
            raise ValueError("paper round-trip turnover does not independently verify")
    ledger_by_strategy = {ledger.strategy_id: ledger for ledger in evaluation.ledgers}
    if len(ledger_by_strategy) != len(evaluation.ledgers):
        raise ValueError("paper evaluation ledger identities are not unique")
    for strategy_id, ledger in ledger_by_strategy.items():
        trips = tuple(trip for trip in evaluation.round_trips if trip.strategy_id == strategy_id)
        cumulative = Decimal("0")
        peak = Decimal("0")
        drawdown = Decimal("0")
        for trip in trips:
            cumulative += trip.net_pnl
            peak = max(peak, cumulative)
            drawdown = max(drawdown, peak - cumulative)
        if (
            ledger.round_trip_count != len(trips)
            or ledger.gross_mid_pnl != sum((trip.gross_mid_pnl for trip in trips), Decimal("0"))
            or ledger.execution_cost != sum((trip.execution_cost for trip in trips), Decimal("0"))
            or ledger.net_pnl != sum((trip.net_pnl for trip in trips), Decimal("0"))
            or ledger.turnover != sum((trip.turnover for trip in trips), Decimal("0"))
            or ledger.maximum_drawdown != drawdown
        ):
            raise ValueError("paper strategy ledger does not independently verify")


def _direction(strength: Decimal) -> int:
    if strength > 0:
        return 1
    if strength < 0:
        return -1
    return 0


def _round_trip(
    forecast: Forecast,
    direction: int,
    entry: MarketQuote,
    exit_quote: MarketQuote,
    economics: PaperInstrumentEconomics,
    model: PaperModel,
) -> PaperRoundTrip:
    if entry.bid is None or entry.ask is None or exit_quote.bid is None or exit_quote.ask is None:
        raise ValueError("paper round trip requires complete bid/ask quotes")
    slippage = economics.price_increment * model.adverse_slippage_increments
    entry_mid = (entry.bid + entry.ask) / Decimal("2")
    exit_mid = (exit_quote.bid + exit_quote.ask) / Decimal("2")
    if direction > 0:
        entry_price = entry.ask + slippage
        exit_price = exit_quote.bid - slippage
    else:
        entry_price = entry.bid - slippage
        exit_price = exit_quote.ask + slippage
    factor = economics.quantity * economics.value_per_price_unit * economics.quote_to_reporting_rate
    gross_mid_pnl = Decimal(direction) * (exit_mid - entry_mid) * factor
    net_pnl = Decimal(direction) * (exit_price - entry_price) * factor
    execution_cost = gross_mid_pnl - net_pnl
    turnover = (entry_price + exit_price) * factor
    return PaperRoundTrip(
        forecast_id=forecast.forecast_id,
        strategy_id=forecast.strategy_id,
        instrument_id=forecast.instrument_id,
        direction=direction,
        entry_received_time=entry.received_time,
        exit_received_time=exit_quote.received_time,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_mid=entry_mid,
        exit_mid=exit_mid,
        quantity=economics.quantity,
        gross_mid_pnl=gross_mid_pnl,
        execution_cost=execution_cost,
        net_pnl=net_pnl,
        turnover=turnover,
        reporting_currency=economics.reporting_currency,
        model_configuration_hash=model.configuration_hash,
        economics_configuration_hash=economics.configuration_hash,
        session_profile_version=economics.session_profile.profile_version,
    )


def _ledgers(
    forecasts: Sequence[Forecast],
    round_trips: Sequence[PaperRoundTrip],
    reporting_currency: str,
) -> tuple[StrategyLedger, ...]:
    strategy_ids = sorted({forecast.strategy_id for forecast in forecasts})
    results: list[StrategyLedger] = []
    for strategy_id in strategy_ids:
        trips = tuple(trip for trip in round_trips if trip.strategy_id == strategy_id)
        cumulative = Decimal("0")
        peak = Decimal("0")
        maximum_drawdown = Decimal("0")
        for trip in trips:
            cumulative += trip.net_pnl
            peak = max(peak, cumulative)
            maximum_drawdown = max(maximum_drawdown, peak - cumulative)
        results.append(
            StrategyLedger(
                strategy_id=strategy_id,
                reporting_currency=reporting_currency,
                round_trip_count=len(trips),
                gross_mid_pnl=sum((trip.gross_mid_pnl for trip in trips), Decimal("0")),
                execution_cost=sum((trip.execution_cost for trip in trips), Decimal("0")),
                net_pnl=sum((trip.net_pnl for trip in trips), Decimal("0")),
                maximum_drawdown=maximum_drawdown,
                turnover=sum((trip.turnover for trip in trips), Decimal("0")),
            )
        )
    return tuple(results)
