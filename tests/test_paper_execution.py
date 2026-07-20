from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from qtrad.application.paper_execution import evaluate_shadow_paper, verify_paper_evaluation
from qtrad.application.ranking_report import build_ranking_report
from qtrad.application.strategy_evaluation import EvaluationResult, StrategyScore
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import DataQuality, MarketQuote
from qtrad.domain.paper import PaperInstrumentEconomics, PaperModel, PaperSessionProfile
from qtrad.domain.strategy import (
    Forecast,
    ForecastTarget,
    ScoreContract,
    StrategyDefinition,
    StrategyState,
    forecast_identity,
)

INSTRUMENT = InstrumentId("fx:eur-usd")
LISTING = ProviderListingId("fixture", "test", "EURUSD")
DECISION = datetime(2026, 7, 20, 8, 1, tzinfo=UTC)


def _session() -> PaperSessionProfile:
    return PaperSessionProfile(
        profile_version="test-weekday-v1",
        timezone_name="UTC",
        local_open=time(0, 0),
        local_close=time(23, 59),
        weekdays=(0, 1, 2, 3, 4),
    )


def _forecast(strategy_id: str, strength: str, state: StrategyState) -> Forecast:
    definition = StrategyDefinition(
        strategy_id=strategy_id,
        strategy_version=1,
        kind="PERSISTENCE",
        lookback_bars=1,
        horizon=timedelta(minutes=2),
        state=state,
    )
    return Forecast(
        forecast_id=forecast_identity(definition, INSTRUMENT, DECISION),
        strategy_id=definition.strategy_id,
        strategy_version=definition.strategy_version,
        strategy_configuration_hash=definition.configuration_hash,
        strategy_state=definition.state,
        instrument_id=INSTRUMENT,
        observation_end=DECISION,
        decision_time=DECISION,
        horizon=definition.horizon,
        target=ForecastTarget.MID_CLOSE_RETURN,
        strength=Decimal(strength),
        rationale="fixture",
    )


def _quote(
    seconds: int, bid: str, ask: str, quality: DataQuality = DataQuality.HEALTHY
) -> MarketQuote:
    timestamp = DECISION + timedelta(seconds=seconds)
    return MarketQuote(
        instrument_id=INSTRUMENT,
        listing_id=LISTING,
        event_time=timestamp,
        received_time=timestamp,
        bid=Decimal(bid),
        ask=Decimal(ask),
        quality=quality,
    )


def _economics() -> dict[InstrumentId, PaperInstrumentEconomics]:
    return {
        INSTRUMENT: PaperInstrumentEconomics(
            instrument_id=INSTRUMENT,
            quantity=Decimal("1000"),
            price_increment=Decimal("0.0001"),
            value_per_price_unit=Decimal("1"),
            quote_currency="USD",
            reporting_currency="AUD",
            quote_to_reporting_rate=Decimal("1.5"),
            session_profile=_session(),
        )
    }


def test_subsequent_healthy_bid_ask_fills_reconcile_isolated_ledgers() -> None:
    forecasts = (
        _forecast("selected-long", "1", StrategyState.SELECTED),
        _forecast("shadow-short", "-1", StrategyState.SHADOW),
        _forecast("shadow-no-signal", "0", StrategyState.SHADOW),
    )
    quotes = (
        _quote(5, "1.0998", "1.1000"),
        _quote(10, "1.1000", "1.1002"),
        _quote(30, "1.1004", "1.1006", DataQuality.STALE),
        _quote(120, "1.1010", "1.1012"),
    )
    model = PaperModel(
        model_version=1,
        latency=timedelta(seconds=10),
        adverse_slippage_increments=1,
    )

    result = evaluate_shadow_paper(forecasts, tuple(reversed(quotes)), _economics(), model)
    repeated = evaluate_shadow_paper(forecasts, quotes, _economics(), model)

    assert result == repeated
    assert len(result.round_trips) == 2
    long_trip = next(trip for trip in result.round_trips if trip.strategy_id == "selected-long")
    short_trip = next(trip for trip in result.round_trips if trip.strategy_id == "shadow-short")
    assert long_trip.entry_received_time == DECISION + timedelta(seconds=10)
    assert long_trip.exit_received_time == DECISION + timedelta(seconds=120)
    assert long_trip.gross_mid_pnl == Decimal("1.50000")
    assert long_trip.execution_cost == Decimal("0.60000")
    assert long_trip.net_pnl == Decimal("0.90000")
    assert short_trip.net_pnl == Decimal("-2.10000")
    no_signal = next(
        ledger for ledger in result.ledgers if ledger.strategy_id == "shadow-no-signal"
    )
    assert no_signal.round_trip_count == 0
    assert no_signal.net_pnl == 0
    assert all(
        ledger.net_pnl == ledger.gross_mid_pnl - ledger.execution_cost for ledger in result.ledgers
    )
    verify_paper_evaluation(result, _economics())

    tampered = replace(
        result,
        round_trips=(
            replace(result.round_trips[0], entry_price=Decimal("1.0000")),
            *result.round_trips[1:],
        ),
    )
    with pytest.raises(ValueError, match="independently verify"):
        verify_paper_evaluation(tampered, _economics())


def test_latency_sensitivity_uses_the_first_quote_after_the_new_boundary() -> None:
    forecast = _forecast("selected-long", "1", StrategyState.SELECTED)
    quotes = (
        _quote(10, "1.1000", "1.1002"),
        _quote(20, "1.1005", "1.1007"),
        _quote(120, "1.1010", "1.1012"),
    )

    fast = evaluate_shadow_paper(
        (forecast,),
        quotes,
        _economics(),
        PaperModel(1, timedelta(seconds=10), 0),
    )
    slow = evaluate_shadow_paper(
        (forecast,),
        quotes,
        _economics(),
        PaperModel(1, timedelta(seconds=20), 0),
    )

    assert fast.round_trips[0].entry_received_time == DECISION + timedelta(seconds=10)
    assert slow.round_trips[0].entry_received_time == DECISION + timedelta(seconds=20)
    assert slow.ledgers[0].net_pnl < fast.ledgers[0].net_pnl


def test_missing_product_economics_denies_paper_fill_without_losing_forecast() -> None:
    forecast = _forecast("selected-long", "1", StrategyState.SELECTED)
    other_instrument = InstrumentId("fx:aud-usd")
    other_economics = PaperInstrumentEconomics(
        instrument_id=other_instrument,
        quantity=Decimal("1000"),
        price_increment=Decimal("0.0001"),
        value_per_price_unit=Decimal("1"),
        quote_currency="USD",
        reporting_currency="AUD",
        quote_to_reporting_rate=Decimal("1.5"),
        session_profile=_session(),
    )
    result = evaluate_shadow_paper(
        (forecast,),
        (_quote(10, "1.1000", "1.1002"), _quote(120, "1.1010", "1.1012")),
        {other_instrument: other_economics},
        PaperModel(1, timedelta(seconds=10), 0),
    )

    assert result.round_trips == ()
    assert result.ledgers[0].strategy_id == forecast.strategy_id
    assert result.ledgers[0].round_trip_count == 0


def test_report_binds_replay_traces_ranking_and_cost_latency_sensitivity() -> None:
    forecasts = (
        _forecast("selected-long", "1", StrategyState.SELECTED),
        _forecast("shadow-short", "-1", StrategyState.SHADOW),
        _forecast("shadow-no-signal", "0", StrategyState.SHADOW),
    )
    strategy_evaluation = EvaluationResult(
        forecasts=forecasts,
        outcomes=(),
        scores=tuple(
            StrategyScore(
                strategy_id=forecast.strategy_id,
                strategy_configuration_hash=forecast.strategy_configuration_hash,
                strategy_state=forecast.strategy_state.value,
                forecast_count=1,
                outcome_count=0,
                coverage=Decimal("0"),
                rank_ic=None,
                eligible_for_ranking=False,
            )
            for forecast in forecasts
        ),
        ranking=(),
    )
    quotes = (
        _quote(10, "1.1000", "1.1002"),
        _quote(20, "1.1005", "1.1007"),
        _quote(120, "1.1010", "1.1012"),
    )
    fast = evaluate_shadow_paper(
        forecasts, quotes, _economics(), PaperModel(1, timedelta(seconds=10), 0)
    )
    costly = evaluate_shadow_paper(
        forecasts, quotes, _economics(), PaperModel(1, timedelta(seconds=20), 2)
    )

    report = build_ranking_report(
        dataset_sha256="d" * 64,
        dataset_evidence={"fixture": True},
        score_contract=ScoreContract(1, 3, timedelta(minutes=2)),
        strategy_evaluation=strategy_evaluation,
        paper_evaluations=(costly, fast),
    )
    repeated = build_ranking_report(
        dataset_sha256="d" * 64,
        dataset_evidence={"fixture": True},
        score_contract=ScoreContract(1, 3, timedelta(minutes=2)),
        strategy_evaluation=strategy_evaluation,
        paper_evaluations=(fast, costly),
    )

    assert repeated == report
    assert len(report.report_sha256) == 64
    assert report.payload["profitability_claim"] is False
    sensitivity = report.payload["cost_latency_sensitivity"]
    strategies = report.payload["strategies"]
    trace_hashes = report.payload["trace_hashes"]
    assert isinstance(sensitivity, list)
    assert isinstance(strategies, list)
    assert isinstance(trace_hashes, dict)
    assert len(sensitivity) == 2
    strategy_ids: set[str] = set()
    for row in strategies:
        assert isinstance(row, dict)
        strategy_id = row["strategy_id"]
        assert isinstance(strategy_id, str)
        strategy_ids.add(strategy_id)
    assert strategy_ids == {forecast.strategy_id for forecast in forecasts}
    assert all(isinstance(value, str) and len(value) == 64 for value in trace_hashes.values())
