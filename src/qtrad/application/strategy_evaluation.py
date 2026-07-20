"""Deterministic, causal strategy forecasting and realised-outcome evaluation."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import DataQuality, MarketBar, PriceBasis
from qtrad.domain.strategy import (
    Forecast,
    ForecastTarget,
    RealisedOutcome,
    ScoreContract,
    StrategyDefinition,
    forecast_identity,
)
from qtrad.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class StrategyScore:
    strategy_id: str
    strategy_configuration_hash: str
    strategy_state: str
    forecast_count: int
    outcome_count: int
    coverage: Decimal
    rank_ic: Decimal | None
    eligible_for_ranking: bool


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    forecasts: tuple[Forecast, ...]
    outcomes: tuple[RealisedOutcome, ...]
    scores: tuple[StrategyScore, ...]
    ranking: tuple[str, ...]


def evaluate_strategies(
    bars: Sequence[MarketBar],
    definitions: Sequence[StrategyDefinition],
    *,
    score_contract: ScoreContract,
    decision_start: datetime | None = None,
    decision_end: datetime | None = None,
) -> EvaluationResult:
    """Evaluate simple strategy definitions on completed healthy midpoint bars.

    Each forecast is formed only after its observation bar closes. Its outcome is paired to an
    exactly matching later close, which keeps missing intervals visible instead of filling them.
    """

    if not definitions or len({item.strategy_id for item in definitions}) != len(definitions):
        raise ValueError("strategy definitions must be non-empty with unique IDs")
    if any(definition.horizon != score_contract.horizon for definition in definitions):
        raise ValueError("strategy horizons must match the score contract")
    if decision_start is not None:
        require_utc(decision_start, "strategy decision_start")
    if decision_end is not None:
        require_utc(decision_end, "strategy decision_end")
    if decision_start is not None and decision_end is not None and decision_end <= decision_start:
        raise ValueError("strategy decision_end must follow decision_start")
    midpoint_bars = _eligible_midpoint_bars(bars)
    forecasts = _forecasts(
        midpoint_bars,
        definitions,
        decision_start=decision_start,
        decision_end=decision_end,
    )
    outcomes = _outcomes(midpoint_bars, forecasts)
    scores = _scores(definitions, forecasts, outcomes, score_contract.minimum_samples)
    ranking = tuple(
        item.strategy_id
        for item in sorted(
            (score for score in scores if score.eligible_for_ranking),
            key=lambda score: (
                -(score.rank_ic if score.rank_ic is not None else Decimal("-Infinity")),
                score.strategy_id,
            ),
        )
    )
    return EvaluationResult(
        forecasts=forecasts,
        outcomes=outcomes,
        scores=scores,
        ranking=ranking,
    )


def _eligible_midpoint_bars(bars: Sequence[MarketBar]) -> tuple[MarketBar, ...]:
    eligible = tuple(
        sorted(
            (
                bar
                for bar in bars
                if bar.basis is PriceBasis.MID and bar.quality is DataQuality.HEALTHY
            ),
            key=lambda bar: (str(bar.instrument_id), bar.interval_end, bar.revision),
        )
    )
    identities: set[tuple[InstrumentId, datetime]] = set()
    for bar in eligible:
        identity = (bar.instrument_id, bar.interval_end)
        if identity in identities:
            raise ValueError("evaluation requires one eligible midpoint revision per interval")
        identities.add(identity)
    return eligible


def _forecasts(
    bars: Sequence[MarketBar],
    definitions: Sequence[StrategyDefinition],
    *,
    decision_start: datetime | None,
    decision_end: datetime | None,
) -> tuple[Forecast, ...]:
    histories: dict[InstrumentId, list[MarketBar]] = defaultdict(list)
    results: list[Forecast] = []
    for bar in bars:
        history = histories[bar.instrument_id]
        history.append(bar)
        if decision_start is not None and bar.interval_end < decision_start:
            continue
        if decision_end is not None and bar.interval_end >= decision_end:
            continue
        for definition in definitions:
            if len(history) <= definition.lookback_bars:
                continue
            prior = history[-definition.lookback_bars - 1]
            observed_return = bar.close / prior.close - Decimal("1")
            strength = _strategy_strength(definition, observed_return)
            results.append(
                Forecast(
                    forecast_id=forecast_identity(definition, bar.instrument_id, bar.interval_end),
                    strategy_id=definition.strategy_id,
                    strategy_version=definition.strategy_version,
                    strategy_configuration_hash=definition.configuration_hash,
                    strategy_state=definition.state,
                    instrument_id=bar.instrument_id,
                    observation_end=bar.interval_end,
                    decision_time=bar.interval_end,
                    horizon=definition.horizon,
                    target=ForecastTarget.MID_CLOSE_RETURN,
                    strength=strength,
                    rationale=(
                        f"{definition.kind};lookback={definition.lookback_bars};"
                        f"observed_return={observed_return}"
                    ),
                )
            )
    return tuple(
        sorted(
            results,
            key=lambda item: (item.decision_time, item.strategy_id, str(item.instrument_id)),
        )
    )


def _strategy_strength(definition: StrategyDefinition, observed_return: Decimal) -> Decimal:
    if definition.kind == "NO_SIGNAL":
        return Decimal("0")
    if definition.kind not in {"PERSISTENCE", "REVERSAL"}:
        raise ValueError(f"unsupported strategy kind: {definition.kind}")
    if abs(observed_return) <= definition.threshold:
        return Decimal("0")
    direction = Decimal("1") if observed_return > 0 else Decimal("-1")
    return direction if definition.kind == "PERSISTENCE" else -direction


def _outcomes(
    bars: Sequence[MarketBar], forecasts: Sequence[Forecast]
) -> tuple[RealisedOutcome, ...]:
    prices = {(bar.instrument_id, bar.interval_end): bar.close for bar in bars}
    results: list[RealisedOutcome] = []
    for forecast in forecasts:
        decision_mid = prices[(forecast.instrument_id, forecast.decision_time)]
        realised_mid = prices.get((forecast.instrument_id, forecast.target_time))
        if realised_mid is None:
            continue
        results.append(
            RealisedOutcome(
                forecast_id=forecast.forecast_id,
                instrument_id=forecast.instrument_id,
                decision_time=forecast.decision_time,
                target_time=forecast.target_time,
                decision_mid=decision_mid,
                realised_mid=realised_mid,
                realised_return=realised_mid / decision_mid - Decimal("1"),
            )
        )
    return tuple(results)


def _scores(
    definitions: Sequence[StrategyDefinition],
    forecasts: Sequence[Forecast],
    outcomes: Sequence[RealisedOutcome],
    minimum_samples: int,
) -> tuple[StrategyScore, ...]:
    outcome_by_forecast = {outcome.forecast_id: outcome for outcome in outcomes}
    results: list[StrategyScore] = []
    for definition in sorted(definitions, key=lambda item: item.strategy_id):
        strategy_forecasts = tuple(
            forecast for forecast in forecasts if forecast.strategy_id == definition.strategy_id
        )
        pairs = tuple(
            (forecast.strength, outcome_by_forecast[forecast.forecast_id].realised_return)
            for forecast in strategy_forecasts
            if forecast.forecast_id in outcome_by_forecast
        )
        rank_ic = _spearman_rank_ic(pairs) if len(pairs) >= minimum_samples else None
        results.append(
            StrategyScore(
                strategy_id=definition.strategy_id,
                strategy_configuration_hash=definition.configuration_hash,
                strategy_state=definition.state.value,
                forecast_count=len(strategy_forecasts),
                outcome_count=len(pairs),
                coverage=(
                    Decimal(len(pairs)) / Decimal(len(strategy_forecasts))
                    if strategy_forecasts
                    else Decimal("0")
                ),
                rank_ic=rank_ic,
                eligible_for_ranking=rank_ic is not None,
            )
        )
    return tuple(results)


def _spearman_rank_ic(pairs: Sequence[tuple[Decimal, Decimal]]) -> Decimal | None:
    left = _ranks(tuple(pair[0] for pair in pairs))
    right = _ranks(tuple(pair[1] for pair in pairs))
    left_mean = sum(left) / Decimal(len(left))
    right_mean = sum(right) / Decimal(len(right))
    covariance = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_variance = sum((value - left_mean) ** 2 for value in left)
    right_variance = sum((value - right_mean) ** 2 for value in right)
    if left_variance == 0 or right_variance == 0:
        return None
    return covariance / (left_variance * right_variance).sqrt()


def _ranks(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    positions: dict[Decimal, list[int]] = defaultdict(list)
    for position, value in enumerate(sorted(values), start=1):
        positions[value].append(position)
    rank_by_value = {
        value: sum(Decimal(position) for position in value_positions)
        / Decimal(len(value_positions))
        for value, value_positions in positions.items()
    }
    return tuple(rank_by_value[value] for value in values)
