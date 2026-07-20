from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrad.application.strategy_evaluation import evaluate_strategies
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import BarProvenance, DataQuality, MarketBar, PriceBasis
from qtrad.domain.strategy import ScoreContract, StrategyDefinition, StrategyState


def _bars(closes: tuple[str, ...]) -> tuple[MarketBar, ...]:
    start = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    return tuple(
        MarketBar(
            instrument_id=InstrumentId("fx:eur-usd"),
            basis=PriceBasis.MID,
            interval_start=start + timedelta(minutes=index),
            interval_end=start + timedelta(minutes=index + 1),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            sample_count=1,
            revision=1,
            provenance=BarProvenance.QUOTE_DERIVED,
            source_listing_id=ProviderListingId("fixture", "test", "EURUSD"),
            quality=DataQuality.HEALTHY,
        )
        for index, close in enumerate(closes)
    )


def _definitions() -> tuple[StrategyDefinition, ...]:
    return (
        StrategyDefinition(
            strategy_id="persistence-1",
            strategy_version=1,
            kind="PERSISTENCE",
            lookback_bars=1,
            horizon=timedelta(minutes=1),
            state=StrategyState.SELECTED,
        ),
        StrategyDefinition(
            strategy_id="reversal-1",
            strategy_version=1,
            kind="REVERSAL",
            lookback_bars=1,
            horizon=timedelta(minutes=1),
        ),
        StrategyDefinition(
            strategy_id="persistence-2",
            strategy_version=1,
            kind="PERSISTENCE",
            lookback_bars=2,
            horizon=timedelta(minutes=1),
        ),
        StrategyDefinition(
            strategy_id="no-signal",
            strategy_version=1,
            kind="NO_SIGNAL",
            lookback_bars=1,
            horizon=timedelta(minutes=1),
        ),
    )


def _score_contract(minimum_samples: int) -> ScoreContract:
    return ScoreContract(
        contract_version=1,
        minimum_samples=minimum_samples,
        horizon=timedelta(minutes=1),
    )


def test_forecasts_and_outcomes_are_causal_attributable_and_deterministic() -> None:
    bars = _bars(("100", "101", "102", "101", "100", "101", "102", "101", "100"))

    result = evaluate_strategies(bars, _definitions(), score_contract=_score_contract(3))
    repeated = evaluate_strategies(
        tuple(reversed(bars)), _definitions(), score_contract=_score_contract(3)
    )

    assert repeated == result
    assert result.forecasts
    assert result.outcomes
    assert len({forecast.forecast_id for forecast in result.forecasts}) == len(result.forecasts)
    assert all(forecast.observation_end <= forecast.decision_time for forecast in result.forecasts)
    assert all(outcome.target_time > outcome.decision_time for outcome in result.outcomes)
    assert {score.strategy_state for score in result.scores} == {"SELECTED", "SHADOW"}
    assert {forecast.strategy_id for forecast in result.forecasts} == {
        definition.strategy_id for definition in _definitions()
    }
    assert result.ranking[0] == "persistence-1"
    assert result.ranking.index("persistence-1") < result.ranking.index("reversal-1")


def test_missing_exact_horizon_remains_unpaired_and_visible_in_coverage() -> None:
    bars = _bars(("100", "101", "102", "103", "104"))
    bars = (bars[0], bars[1], bars[3], bars[4])

    result = evaluate_strategies(bars, _definitions(), score_contract=_score_contract(2))
    persistence = next(score for score in result.scores if score.strategy_id == "persistence-1")

    assert persistence.outcome_count < persistence.forecast_count
    assert persistence.coverage < Decimal("1")


def test_constant_no_signal_is_not_ranked_as_evidence() -> None:
    result = evaluate_strategies(
        _bars(("100", "101", "102", "101", "100", "101")),
        _definitions(),
        score_contract=_score_contract(2),
    )
    no_signal = next(score for score in result.scores if score.strategy_id == "no-signal")

    assert no_signal.rank_ic is None
    assert not no_signal.eligible_for_ranking
    assert "no-signal" not in result.ranking


def test_duplicate_eligible_revision_is_rejected() -> None:
    bars = _bars(("100", "101", "102"))

    with pytest.raises(ValueError, match="one eligible midpoint revision"):
        evaluate_strategies((*bars, bars[-1]), _definitions(), score_contract=_score_contract(2))
