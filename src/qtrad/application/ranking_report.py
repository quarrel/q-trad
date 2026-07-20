"""Deterministic strategy-ranking report assembly."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qtrad.application.paper_execution import PaperEvaluation
from qtrad.application.strategy_evaluation import EvaluationResult
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.strategy import ScoreContract


@dataclass(frozen=True, slots=True)
class RankingReport:
    payload: Mapping[str, JsonValue]
    report_sha256: str


def build_ranking_report(
    *,
    dataset_sha256: str,
    dataset_evidence: Mapping[str, JsonValue],
    score_contract: ScoreContract,
    strategy_evaluation: EvaluationResult,
    paper_evaluations: Sequence[PaperEvaluation],
) -> RankingReport:
    if len(dataset_sha256) != 64:
        raise ValueError("ranking report dataset_sha256 must be SHA-256")
    if not paper_evaluations:
        raise ValueError("ranking report requires a base paper model")
    ordered_paper = tuple(
        sorted(
            paper_evaluations,
            key=lambda item: (
                item.model.latency,
                item.model.adverse_slippage_increments,
                item.model.model_version,
            ),
        )
    )
    base = ordered_paper[0]
    if any(item.economics_hashes != base.economics_hashes for item in ordered_paper):
        raise ValueError("ranking report sensitivity models must use identical paper economics")
    scores = {score.strategy_id: score for score in strategy_evaluation.scores}
    base_ledgers = {ledger.strategy_id: ledger for ledger in base.ledgers}
    if set(scores) != set(base_ledgers):
        raise ValueError("ranking report score and ledger strategy sets must match")

    strategy_rows: list[JsonValue] = []
    for strategy_id in sorted(scores):
        score = scores[strategy_id]
        ledger = base_ledgers[strategy_id]
        strategy_rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_configuration_hash": score.strategy_configuration_hash,
                "strategy_state": score.strategy_state,
                "forecast_count": score.forecast_count,
                "outcome_count": score.outcome_count,
                "coverage": str(score.coverage),
                "rank_ic": None if score.rank_ic is None else str(score.rank_ic),
                "eligible_for_ranking": score.eligible_for_ranking,
                "paper_round_trip_count": ledger.round_trip_count,
                "gross_mid_pnl": str(ledger.gross_mid_pnl),
                "execution_cost": str(ledger.execution_cost),
                "net_pnl": str(ledger.net_pnl),
                "maximum_drawdown": str(ledger.maximum_drawdown),
                "turnover": str(ledger.turnover),
                "reporting_currency": ledger.reporting_currency,
            }
        )
    sensitivity_rows: list[JsonValue] = []
    for evaluation in ordered_paper:
        sensitivity_rows.append(
            {
                "paper_model_hash": evaluation.model.configuration_hash,
                "model_version": evaluation.model.model_version,
                "latency_microseconds": int(evaluation.model.latency.total_seconds() * 1_000_000),
                "adverse_slippage_increments": evaluation.model.adverse_slippage_increments,
                "strategy_net_pnl": {
                    ledger.strategy_id: str(ledger.net_pnl) for ledger in evaluation.ledgers
                },
            }
        )

    payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "report_kind": "DETERMINISTIC_SHADOW_STRATEGY_RANKING",
        "dataset_sha256": dataset_sha256,
        "dataset_evidence": dict(dataset_evidence),
        "score_contract": {
            "configuration_hash": score_contract.configuration_hash,
            "contract_version": score_contract.contract_version,
            "horizon_seconds": int(score_contract.horizon.total_seconds()),
            "basis": score_contract.basis,
            "sampling": score_contract.sampling,
            "window": score_contract.window,
            "overlapping_observations": score_contract.overlapping_observations,
            "minimum_samples": score_contract.minimum_samples,
            "target": score_contract.target.value,
        },
        "paper_economics_hashes": {
            instrument_id: digest for instrument_id, digest in base.economics_hashes
        },
        "trace_hashes": {
            "forecasts": _trace_hash(_forecast_rows(strategy_evaluation)),
            "outcomes": _trace_hash([to_json_value(item) for item in strategy_evaluation.outcomes]),
            "paper_round_trips": _trace_hash([to_json_value(item) for item in base.round_trips]),
            "ledgers": _trace_hash([to_json_value(item) for item in base.ledgers]),
            "scores": _trace_hash([to_json_value(item) for item in strategy_evaluation.scores]),
        },
        "ranking": list(strategy_evaluation.ranking),
        "strategies": strategy_rows,
        "cost_latency_sensitivity": sensitivity_rows,
        "profitability_claim": False,
    }
    return RankingReport(payload=payload, report_sha256=_payload_hash(payload))


def _forecast_rows(evaluation: EvaluationResult) -> list[JsonValue]:
    return [
        {
            "forecast_id": forecast.forecast_id,
            "strategy_id": forecast.strategy_id,
            "strategy_version": forecast.strategy_version,
            "strategy_configuration_hash": forecast.strategy_configuration_hash,
            "strategy_state": forecast.strategy_state.value,
            "instrument_id": str(forecast.instrument_id),
            "observation_end": to_json_value(forecast.observation_end),
            "decision_time": to_json_value(forecast.decision_time),
            "horizon_seconds": int(forecast.horizon.total_seconds()),
            "target": forecast.target.value,
            "strength": str(forecast.strength),
            "rationale": forecast.rationale,
        }
        for forecast in evaluation.forecasts
    ]


def _trace_hash(rows: Sequence[JsonValue]) -> str:
    return _payload_hash(list(rows))


def _payload_hash(payload: JsonValue) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
