"""Strict loading and evidence checks for a reproducible strategy experiment."""

import hashlib
import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from qtrad.application.paper_execution import evaluate_shadow_paper, verify_paper_evaluation
from qtrad.application.ranking_report import RankingReport, build_ranking_report
from qtrad.application.replay import semantic_bar_hash, semantic_quote_hash
from qtrad.application.strategy_evaluation import evaluate_strategies
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import BarProvenance, DataQuality, MarketBar, MarketQuote
from qtrad.domain.paper import PaperInstrumentEconomics, PaperModel, PaperSessionProfile
from qtrad.domain.strategy import ScoreContract, StrategyDefinition, StrategyState
from qtrad.ports.storage import ResearchManifest


@dataclass(frozen=True, slots=True)
class ProviderEconomicsEvidence:
    metadata_version: str
    currency: str
    minimum_deal_size: Decimal
    minimum_quantity: Decimal
    one_pip_means: str
    value_of_one_pip: Decimal


@dataclass(frozen=True, slots=True)
class StrategyExperiment:
    name: str
    configuration_sha256: str
    source_manifest_sha256: str
    instrument_id: InstrumentId
    decision_start: datetime
    decision_end: datetime
    score_contract: ScoreContract
    strategies: tuple[StrategyDefinition, ...]
    economics: PaperInstrumentEconomics
    provider_evidence: ProviderEconomicsEvidence
    paper_models: tuple[PaperModel, ...]

    @property
    def query_start(self) -> datetime:
        warmup = max(strategy.lookback_bars for strategy in self.strategies)
        return self.decision_start - timedelta(minutes=warmup)

    @property
    def query_end(self) -> datetime:
        return self.decision_end + self.score_contract.horizon


def load_strategy_experiment(path: Path) -> StrategyExperiment:
    encoded = path.read_bytes()
    document = tomllib.loads(encoded.decode("utf-8"))
    _expect_keys(
        document,
        {
            "schema_version",
            "name",
            "source_manifest_sha256",
            "instrument_id",
            "decision_start",
            "decision_end",
            "score",
            "strategy",
            "paper_economics",
            "provider_evidence",
            "session",
            "paper_model",
        },
        "strategy experiment",
    )
    if _required_int(document, "schema_version") != 1:
        raise ValueError("strategy experiment schema version is unsupported")
    instrument_id = InstrumentId(_required_string(document, "instrument_id"))
    decision_start = _required_utc(document, "decision_start")
    decision_end = _required_utc(document, "decision_end")
    if decision_end <= decision_start or decision_end - decision_start > timedelta(days=1):
        raise ValueError(
            "strategy experiment decision interval must be positive and at most one day"
        )

    score_document = _required_table(document, "score")
    _expect_keys(
        score_document, {"contract_version", "minimum_samples", "horizon_seconds"}, "score"
    )
    score_contract = ScoreContract(
        contract_version=_required_int(score_document, "contract_version"),
        minimum_samples=_required_int(score_document, "minimum_samples"),
        horizon=timedelta(seconds=_required_int(score_document, "horizon_seconds")),
    )
    strategies = tuple(
        _strategy(item, score_contract.horizon)
        for item in _required_table_sequence(document, "strategy")
    )
    if len({item.strategy_id for item in strategies}) != len(strategies):
        raise ValueError("strategy experiment strategy IDs must be unique")
    if sum(item.kind != "NO_SIGNAL" for item in strategies) < 3:
        raise ValueError("strategy experiment requires at least three signal strategies")
    if not any(item.kind == "NO_SIGNAL" for item in strategies):
        raise ValueError("strategy experiment requires a no-signal benchmark")
    if not any(item.state is StrategyState.SELECTED for item in strategies):
        raise ValueError("strategy experiment requires an explicit selected strategy")

    session_document = _required_table(document, "session")
    _expect_keys(
        session_document,
        {"profile_version", "timezone", "local_open", "local_close", "weekdays", "holidays"},
        "session",
    )
    session = PaperSessionProfile(
        profile_version=_required_string(session_document, "profile_version"),
        timezone_name=_required_string(session_document, "timezone"),
        local_open=_required_time(session_document, "local_open"),
        local_close=_required_time(session_document, "local_close"),
        weekdays=tuple(_required_int_sequence(session_document, "weekdays")),
        holidays=tuple(_required_date_sequence(session_document, "holidays")),
    )
    economics_document = _required_table(document, "paper_economics")
    _expect_keys(
        economics_document,
        {
            "quantity",
            "price_increment",
            "value_per_price_unit",
            "quote_currency",
            "reporting_currency",
            "quote_to_reporting_rate",
        },
        "paper_economics",
    )
    economics = PaperInstrumentEconomics(
        instrument_id=instrument_id,
        quantity=_required_decimal(economics_document, "quantity"),
        price_increment=_required_decimal(economics_document, "price_increment"),
        value_per_price_unit=_required_decimal(economics_document, "value_per_price_unit"),
        quote_currency=_required_string(economics_document, "quote_currency"),
        reporting_currency=_required_string(economics_document, "reporting_currency"),
        quote_to_reporting_rate=_required_decimal(economics_document, "quote_to_reporting_rate"),
        session_profile=session,
    )
    evidence_document = _required_table(document, "provider_evidence")
    _expect_keys(
        evidence_document,
        {
            "metadata_version",
            "currency",
            "minimum_deal_size",
            "minimum_quantity",
            "one_pip_means",
            "value_of_one_pip",
        },
        "provider_evidence",
    )
    provider_evidence = ProviderEconomicsEvidence(
        metadata_version=_required_string(evidence_document, "metadata_version"),
        currency=_required_string(evidence_document, "currency"),
        minimum_deal_size=_required_decimal(evidence_document, "minimum_deal_size"),
        minimum_quantity=_required_decimal(evidence_document, "minimum_quantity"),
        one_pip_means=_required_string(evidence_document, "one_pip_means"),
        value_of_one_pip=_required_decimal(evidence_document, "value_of_one_pip"),
    )
    if economics.quote_currency != provider_evidence.currency:
        raise ValueError("paper quote currency must match provider economics evidence")
    if economics.quantity < max(
        provider_evidence.minimum_deal_size, provider_evidence.minimum_quantity
    ):
        raise ValueError("paper quantity is below provider evidence minimum")
    if (
        economics.value_per_price_unit * economics.price_increment
        != provider_evidence.value_of_one_pip
    ):
        raise ValueError("paper economics do not reproduce provider value_of_one_pip")
    if provider_evidence.one_pip_means != "1 Index Point" or economics.price_increment != 1:
        raise ValueError("paper price increment is not supported by explicit provider pip evidence")

    paper_models = tuple(
        _paper_model(item) for item in _required_table_sequence(document, "paper_model")
    )
    if not paper_models or len({item.configuration_hash for item in paper_models}) != len(
        paper_models
    ):
        raise ValueError("strategy experiment paper models must be non-empty and unique")
    return StrategyExperiment(
        name=_required_string(document, "name"),
        configuration_sha256=hashlib.sha256(encoded).hexdigest(),
        source_manifest_sha256=_required_sha256(document, "source_manifest_sha256"),
        instrument_id=instrument_id,
        decision_start=decision_start,
        decision_end=decision_end,
        score_contract=score_contract,
        strategies=strategies,
        economics=economics,
        provider_evidence=provider_evidence,
        paper_models=paper_models,
    )


def verify_provider_economics(experiment: StrategyExperiment, row: Mapping[str, object]) -> None:
    evidence = experiment.provider_evidence
    economics = _mapping(row["economics"], "provider economics")
    actual = ProviderEconomicsEvidence(
        metadata_version=_string(row["metadata_version"], "metadata_version"),
        currency=_string(row["currency"], "currency"),
        minimum_deal_size=_database_decimal(row["minimum_deal_size"], "minimum_deal_size"),
        minimum_quantity=_database_decimal(economics["minimum_quantity"], "minimum_quantity"),
        one_pip_means=_string(economics["one_pip_means"], "one_pip_means"),
        value_of_one_pip=_database_decimal(economics["value_of_one_pip"], "value_of_one_pip"),
    )
    if actual != evidence:
        raise ValueError("snapshot provider economics do not match the experiment evidence")


def build_strategy_experiment_report(
    *,
    experiment: StrategyExperiment,
    manifest: ResearchManifest,
    bars: Sequence[MarketBar],
    quote_rows: Sequence[Mapping[str, object]],
    provider_row: Mapping[str, object],
) -> RankingReport:
    if manifest.manifest_sha256 != experiment.source_manifest_sha256:
        raise ValueError("strategy experiment source manifest does not match configuration")
    verify_provider_economics(experiment, provider_row)
    eligible_bars = tuple(
        bar
        for bar in bars
        if bar.instrument_id == experiment.instrument_id
        and bar.provenance is BarProvenance.QUOTE_DERIVED
        and experiment.query_start <= bar.interval_end <= experiment.query_end
    )
    if not eligible_bars:
        raise ValueError("strategy experiment has no quote-derived bars in its interval")
    quotes = tuple(_quote_from_row(row, experiment.instrument_id) for row in quote_rows)
    positions = tuple(
        quote.global_position for quote in quotes if quote.global_position is not None
    )
    if len(positions) != len(quotes) or positions != tuple(sorted(set(positions))):
        raise ValueError("strategy experiment quotes require strict canonical position order")
    strategy_evaluation = evaluate_strategies(
        eligible_bars,
        experiment.strategies,
        score_contract=experiment.score_contract,
        decision_start=experiment.decision_start,
        decision_end=experiment.decision_end,
    )
    paper_evaluations = tuple(
        evaluate_shadow_paper(
            strategy_evaluation.forecasts,
            quotes,
            {experiment.instrument_id: experiment.economics},
            model,
        )
        for model in experiment.paper_models
    )
    for paper_evaluation in paper_evaluations:
        verify_paper_evaluation(paper_evaluation, {experiment.instrument_id: experiment.economics})
    bar_sha256 = semantic_bar_hash(eligible_bars)
    quote_sha256 = semantic_quote_hash(quotes)
    dataset_sha256 = _hash_payload(
        {
            "experiment_configuration_sha256": experiment.configuration_sha256,
            "manifest_sha256": experiment.source_manifest_sha256,
            "bar_sha256": bar_sha256,
            "quote_sha256": quote_sha256,
        }
    )
    return build_ranking_report(
        dataset_sha256=dataset_sha256,
        dataset_evidence={
            "experiment_name": experiment.name,
            "experiment_configuration_sha256": experiment.configuration_sha256,
            "manifest_id": manifest.manifest_id,
            "manifest_sha256": experiment.source_manifest_sha256,
            "bar_sha256": bar_sha256,
            "bar_count": len(eligible_bars),
            "quote_sha256": quote_sha256,
            "quote_count": len(quotes),
            "instrument_id": str(experiment.instrument_id),
            "decision_start": experiment.decision_start.isoformat(),
            "decision_end": experiment.decision_end.isoformat(),
        },
        score_contract=experiment.score_contract,
        strategy_evaluation=strategy_evaluation,
        paper_evaluations=paper_evaluations,
    )


def write_strategy_experiment_report(path: Path, report: RankingReport) -> None:
    if path.exists():
        raise FileExistsError(f"strategy report output already exists: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(f"strategy report output directory does not exist: {path.parent}")
    payload = {**report.payload, "report_sha256": report.report_sha256}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _quote_from_row(row: Mapping[str, object], instrument_id: InstrumentId) -> MarketQuote:
    payload = _mapping(row["payload"], "quote payload")
    if InstrumentId(_string(payload["instrument_id"], "instrument_id")) != instrument_id:
        raise ValueError("quote row belongs to a different experiment instrument")
    listing = _mapping(payload["listing_id"], "listing_id")
    event_time = _datetime(payload["event_time"], "event_time")
    received_time = _datetime(payload["received_time"], "received_time")
    if event_time != _datetime(row["event_time"], "envelope event_time"):
        raise ValueError("quote payload event time differs from its envelope")
    if received_time != _datetime(row["received_time"], "envelope received_time"):
        raise ValueError("quote payload receive time differs from its envelope")
    global_position = row["global_position"]
    if not isinstance(global_position, int):
        raise TypeError("quote global_position must be an integer")
    return MarketQuote(
        instrument_id=instrument_id,
        listing_id=ProviderListingId(
            _string(listing["provider"], "listing provider"),
            _string(listing["environment"], "listing environment"),
            _string(listing["external_id"], "listing external_id"),
        ),
        event_time=event_time,
        received_time=received_time,
        bid=_optional_decimal(payload["bid"], "bid"),
        ask=_optional_decimal(payload["ask"], "ask"),
        bid_size=_optional_decimal(payload["bid_size"], "bid_size"),
        ask_size=_optional_decimal(payload["ask_size"], "ask_size"),
        bid_time=_optional_datetime(payload["bid_time"], "bid_time"),
        ask_time=_optional_datetime(payload["ask_time"], "ask_time"),
        quality=DataQuality(_string(payload["quality"], "quality")),
        source_sequence=_optional_string(payload["source_sequence"], "source_sequence"),
        global_position=global_position,
    )


def _hash_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _strategy(document: Mapping[str, object], horizon: timedelta) -> StrategyDefinition:
    _expect_keys(
        document,
        {"id", "version", "kind", "lookback_bars", "state", "threshold"},
        "strategy",
    )
    return StrategyDefinition(
        strategy_id=_required_string(document, "id"),
        strategy_version=_required_int(document, "version"),
        kind=_required_string(document, "kind"),
        lookback_bars=_required_int(document, "lookback_bars"),
        horizon=horizon,
        state=StrategyState(_required_string(document, "state")),
        threshold=_required_decimal(document, "threshold"),
    )


def _paper_model(document: Mapping[str, object]) -> PaperModel:
    _expect_keys(
        document,
        {"version", "latency_milliseconds", "adverse_slippage_increments"},
        "paper_model",
    )
    return PaperModel(
        model_version=_required_int(document, "version"),
        latency=timedelta(milliseconds=_required_int(document, "latency_milliseconds")),
        adverse_slippage_increments=_required_int(document, "adverse_slippage_increments"),
    )


def _expect_keys(document: Mapping[str, object], expected: set[str], field: str) -> None:
    if set(document) != expected:
        raise ValueError(f"{field} fields do not match the versioned contract")


def _required_table(document: Mapping[str, object], field: str) -> Mapping[str, object]:
    return _mapping(document[field], field)


def _required_table_sequence(
    document: Mapping[str, object], field: str
) -> Sequence[Mapping[str, object]]:
    value = document[field]
    if not isinstance(value, list) or not value:
        raise TypeError(f"{field} must be a non-empty table array")
    return tuple(_mapping(item, field) for item in value)


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a table")
    return value


def _required_string(document: Mapping[str, object], field: str) -> str:
    return _string(document[field], field)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TypeError(f"{field} must be a non-empty trimmed string")
    return value


def _required_int(document: Mapping[str, object], field: str) -> int:
    value = document[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    return value


def _required_decimal(document: Mapping[str, object], field: str) -> Decimal:
    return _decimal(document[field], field)


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a decimal string")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _optional_decimal(value: object, field: str) -> Decimal | None:
    return None if value is None else _decimal(value, field)


def _database_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        result = Decimal(value)
    else:
        raise TypeError(f"{field} must be a database decimal or decimal string")
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"{field} must be a datetime")
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")
    return result.astimezone(UTC)


def _optional_datetime(value: object, field: str) -> datetime | None:
    return None if value is None else _datetime(value, field)


def _optional_string(value: object, field: str) -> str | None:
    return None if value is None else _string(value, field)


def _required_sha256(document: Mapping[str, object], field: str) -> str:
    value = _required_string(document, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be SHA-256")
    return value


def _required_utc(document: Mapping[str, object], field: str) -> datetime:
    value = document[field]
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise TypeError(f"{field} must be a UTC offset datetime")
    return value.astimezone(UTC)


def _required_time(document: Mapping[str, object], field: str) -> time:
    value = document[field]
    if not isinstance(value, time) or value.tzinfo is not None:
        raise TypeError(f"{field} must be a local time")
    return value


def _required_int_sequence(document: Mapping[str, object], field: str) -> Sequence[int]:
    value = document[field]
    if not isinstance(value, list) or any(
        not isinstance(item, int) or isinstance(item, bool) for item in value
    ):
        raise TypeError(f"{field} must be an integer array")
    return tuple(value)


def _required_date_sequence(document: Mapping[str, object], field: str) -> Sequence[date]:
    value = document[field]
    if not isinstance(value, list) or any(
        not isinstance(item, date) or isinstance(item, datetime) for item in value
    ):
        raise TypeError(f"{field} must be a local-date array")
    return tuple(value)
