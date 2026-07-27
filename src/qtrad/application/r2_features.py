"""Deterministic, current-cutoff R2 raw feature materialisation."""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from itertools import pairwise
from math import cos, log, pi, sin, sqrt

from qtrad.domain.foundation import PanelDataset, PanelRow, PanelStatus
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_registry,
    feature_set_id,
)
from qtrad.domain.r2_readiness import FeatureFamily, R2ExperimentConfig
from qtrad.domain.research import ObservationDataset, ObservationRow


def materialise_r2_features(
    observations: ObservationDataset,
    panel: PanelDataset,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str = "L1",
) -> R2FeatureDataset:
    """Materialise an identity-bound OOF feature child from verified R1 children."""
    rows = build_raw_feature_rows(
        observations,
        panel,
        experiment,
        feature_set_name=feature_set_name,
    )
    feature_set = next(
        (item for item in experiment.feature_sets if item.name == feature_set_name), None
    )
    if feature_set is None:
        raise ValueError(f"unknown R2 feature set: {feature_set_name}")
    registry = feature_registry(experiment)
    schema = tuple(item for item in registry if item.family in set(feature_set.families))
    return R2FeatureDataset.create(
        rows,
        feature_schema=schema,
        observation_dataset_id=observations.dataset_id,
        panel_dataset_id=panel.dataset_id,
        target_dataset_id=experiment.target_dataset_id,
        fold_dataset_id=experiment.fold_dataset_id,
        experiment_configuration_id=experiment.configuration_id,
        evidence_class=experiment.evidence_class,
    )


class FeatureLineageError(ValueError):
    """Raised when cutoff selection cannot identify one source revision."""


def select_current_cutoff(
    observations: Sequence[ObservationRow],
    *,
    instrument_id: str,
    basis: PriceBasis,
    interval_start: datetime,
    latest_feature_bar_end: datetime,
    feature_data_asof: datetime,
) -> ObservationRow | None:
    """Select the highest eligible revision for one exact interval and cutoff."""
    candidates = [
        row
        for row in observations
        if row.instrument_id == instrument_id
        and row.basis is basis
        and row.interval_start == interval_start
        and row.interval_end == latest_feature_bar_end
        and row.persisted_at <= feature_data_asof
    ]
    if not candidates:
        return None
    sources = {
        (row.source_provider, row.source_environment, row.source_external_id) for row in candidates
    }
    if len(sources) != 1:
        raise FeatureLineageError("feature interval has ambiguous source lineage")
    revision = max(row.revision for row in candidates)
    selected = [row for row in candidates if row.revision == revision]
    if len(selected) != 1:
        raise FeatureLineageError("feature interval has ambiguous highest revision")
    return selected[0]


def build_raw_feature_rows(
    observations: ObservationDataset,
    panel: PanelDataset,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str = "L1",
) -> tuple[RawFeatureRow, ...]:
    """Build OOF rows; locked-holdout decision rows are excluded."""
    feature_set = next(
        (item for item in experiment.feature_sets if item.name == feature_set_name), None
    )
    if feature_set is None:
        raise ValueError(f"unknown R2 feature set: {feature_set_name}")
    registry = feature_registry(experiment)
    families = set(feature_set.families)
    schema = tuple(item for item in registry if item.family in families)
    holdout_start, holdout_end = experiment.holdout_range
    panels = tuple(
        row
        for row in panel.rows
        if row.instrument_id in experiment.target_instruments
        and row.basis is PriceBasis.MID
        and row.status in {PanelStatus.OBSERVED, PanelStatus.MISSING_AS_OF_CUTOFF}
        and not (holdout_start <= row.decision_time < holdout_end)
    )
    indexed = _index(observations.rows)
    return tuple(
        sorted(
            (
                _build_row(row, indexed, panels, schema, experiment, feature_set_name)
                for row in panels
            ),
            key=RawFeatureRow.semantic_key,
        )
    )


def _build_row(
    panel: PanelRow,
    indexed: Mapping[tuple[str, PriceBasis], tuple[ObservationRow, ...]],
    panels: Sequence[PanelRow],
    schema: Sequence[FeatureDefinition],
    experiment: R2ExperimentConfig,
    feature_set_name: str,
) -> RawFeatureRow:
    target = indexed.get((panel.instrument_id, PriceBasis.MID), ())
    values = tuple(
        RawFeatureValue(
            definition.name,
            *_calculate(definition.name, panel, target, indexed, panels, experiment),
        )
        for definition in schema
    )
    return RawFeatureRow(
        target_instrument_id=panel.instrument_id,
        decision_time=panel.decision_time,
        feature_data_asof=panel.feature_data_asof,
        latest_feature_bar_end=panel.latest_feature_bar_end,
        feature_set_id=feature_set_id(experiment.configuration_id, feature_set_name, schema),
        values=values,
    )


def _calculate(
    name: str,
    panel: PanelRow,
    target: Sequence[ObservationRow],
    indexed: Mapping[tuple[str, PriceBasis], tuple[ObservationRow, ...]],
    panels: Sequence[PanelRow],
    experiment: R2ExperimentConfig,
) -> tuple[float | None, tuple[str, ...]]:
    end, cutoff = panel.latest_feature_bar_end, panel.feature_data_asof
    current = _at(target, end, cutoff)
    if name == "source_active":
        return (1.0 if current else 0.0), _ids(current)
    if name == "quality_healthy":
        return (1.0 if current and current.quality.value == "HEALTHY" else 0.0), _ids(current)
    if name == "gap_disposition_present":
        return (1.0 if panel.audit_disposition else 0.0), ()
    if name.startswith("utc_"):
        minute = panel.decision_time.hour * 60 + panel.decision_time.minute
        if name == "utc_minute_sin":
            return sin(2 * pi * minute / 1440), ()
        if name == "utc_minute_cos":
            return cos(2 * pi * minute / 1440), ()
        day = panel.decision_time.weekday()
        return (sin if name == "utc_day_sin" else cos)(2 * pi * day / 7), ()
    if name == "target_feature_missing_fraction":
        return _missing_fraction(target, end), ()
    if name == "cross_market_available_count":
        available = sum(
            _at(indexed.get((row.instrument_id, PriceBasis.MID), ()), end, cutoff) is not None
            for row in panels
            if row.decision_time == panel.decision_time
        )
        return float(available), ()
    if name.startswith("return_contrast_"):
        _, _, short, long = name.split("_")
        left, left_ids = _return("return_" + short, target, end, cutoff)
        right, right_ids = _return("return_" + long, target, end, cutoff)
        return (
            left - right if left is not None and right is not None else None
        ), left_ids + right_ids
    if name.startswith("return_"):
        if name.endswith("_available"):
            value, ids = _return(name.removesuffix("_available"), target, end, cutoff)
            return (1.0 if value is not None else 0.0), ids
        return _return(name, target, end, cutoff)
    if name.startswith(
        (
            "realised_std_",
            "mean_absolute_return_",
            "mean_log_range_",
            "return_sign_balance_",
            "available_interval_count_",
            "window_coverage_",
        )
    ):
        return _rolling(name, target, end, cutoff)
    if name in {
        "close_spread",
        "spread_fraction",
        "spread_bps",
        "rolling_spread_mean",
        "rolling_spread_change",
        "spread_coverage",
    }:
        if experiment.feature_eligibility[FeatureFamily.SPREAD].state.value != "ELIGIBLE":
            return None, ()
        return _spread(name, panel, indexed)
    if name in {"quote_imbalance", "quote_imbalance_available"}:
        return None, ()
    if name.startswith(("loo_", "vix_context_")):
        return _context(name, panel, indexed, panels, experiment)
    if name in {"cross_market_missing_count", "cross_market_source_active_count"}:
        available = sum(
            _at(indexed.get((row.instrument_id, PriceBasis.MID), ()), end, cutoff) is not None
            for row in panels
            if row.decision_time == panel.decision_time
        )
        total = len(
            {row.instrument_id for row in panels if row.decision_time == panel.decision_time}
        )
        return float(available if name.endswith("active_count") else total - available), ()
    return None, ()


def _return(
    name: str, rows: Sequence[ObservationRow], end: datetime, cutoff: datetime
) -> tuple[float | None, tuple[str, ...]]:
    seconds = int(name.removeprefix("return_").removesuffix("s"))
    first = _at(rows, end - timedelta(seconds=seconds), cutoff)
    last = _at(rows, end, cutoff)
    if first is None or last is None or first.close <= 0 or last.close <= 0:
        return None, _ids(first) + _ids(last)
    return log(float(last.close)) - log(float(first.close)), _ids(first) + _ids(last)


def _rolling(
    name: str, rows: Sequence[ObservationRow], end: datetime, cutoff: datetime
) -> tuple[float | None, tuple[str, ...]]:
    seconds = int(name.rsplit("_", 1)[-1].removesuffix("s"))
    selected = _latest(
        row
        for row in rows
        if end - timedelta(seconds=seconds) < row.interval_end <= end and row.persisted_at <= cutoff
    )
    lineage = tuple(str(row.event_id) for row in selected)
    expected = max(1, seconds // 60)
    if name.startswith("available_interval_count_"):
        return float(len(selected)), lineage
    if name.startswith("window_coverage_"):
        return len(selected) / expected, lineage
    returns = [
        log(float(right.close)) - log(float(left.close))
        for left, right in pairwise(selected)
        if left.close > 0 and right.close > 0
    ]
    if not returns:
        return None, lineage
    if name.startswith("realised_std_"):
        mean = sum(returns) / len(returns)
        return sqrt(sum((value - mean) ** 2 for value in returns) / len(returns)), lineage
    if name.startswith("mean_absolute_return_"):
        return sum(abs(value) for value in returns) / len(returns), lineage
    if name.startswith("return_sign_balance_"):
        return sum(value > 0 for value in returns) / len(returns), lineage
    ranges = [
        log(float(row.high)) - log(float(row.low))
        for row in selected
        if row.low > 0 and row.high > 0
    ]
    return (sum(ranges) / len(ranges) if ranges else None), lineage


def _context(
    name: str,
    panel: PanelRow,
    indexed: Mapping[tuple[str, PriceBasis], tuple[ObservationRow, ...]],
    panels: Sequence[PanelRow],
    experiment: R2ExperimentConfig,
) -> tuple[float | None, tuple[str, ...]]:
    suffix = name.rsplit("_", 1)[-1]
    seconds = int(suffix.removesuffix("s")) if suffix.endswith("s") else 60
    values: list[float] = []
    lineage: list[str] = []
    for instrument in experiment.target_instruments:
        if instrument == panel.instrument_id:
            continue
        value, ids = _return(
            f"return_{seconds}s",
            indexed.get((instrument, PriceBasis.MID), ()),
            panel.latest_feature_bar_end,
            panel.feature_data_asof,
        )
        if value is not None:
            values.append(value)
            lineage.extend(ids)
    if not values:
        return None, tuple(lineage)
    mean = sum(values) / len(values)
    if name.startswith(("loo_mean_return", "loo_market_group_mean")):
        return mean, tuple(lineage)
    if name.startswith("loo_median_return"):
        return sorted(values)[len(values) // 2], tuple(lineage)
    if name.startswith(("loo_return_dispersion", "loo_market_group_dispersion")):
        return sqrt(sum((value - mean) ** 2 for value in values) / len(values)), tuple(lineage)
    if name.startswith("loo_positive_proportion"):
        return sum(value > 0 for value in values) / len(values), tuple(lineage)
    if name.startswith(("loo_available_count", "loo_market_group_available_count")):
        return float(len(values)), tuple(lineage)
    return None, tuple(lineage)


def _spread(
    name: str, panel: PanelRow, indexed: Mapping[tuple[str, PriceBasis], tuple[ObservationRow, ...]]
) -> tuple[float | None, tuple[str, ...]]:
    bid = _at(
        indexed.get((panel.instrument_id, PriceBasis.BID), ()),
        panel.latest_feature_bar_end,
        panel.feature_data_asof,
    )
    ask = _at(
        indexed.get((panel.instrument_id, PriceBasis.ASK), ()),
        panel.latest_feature_bar_end,
        panel.feature_data_asof,
    )
    mid = _at(
        indexed.get((panel.instrument_id, PriceBasis.MID), ()),
        panel.latest_feature_bar_end,
        panel.feature_data_asof,
    )
    ids = _ids(bid) + _ids(ask) + _ids(mid)
    if bid is None or ask is None or mid is None or mid.close <= 0:
        return None, ids
    spread = float(ask.close - bid.close)
    if name == "close_spread":
        return spread, ids
    if name == "spread_fraction":
        return spread / float(mid.close), ids
    if name == "spread_bps":
        return spread * 10_000 / float(mid.close), ids
    return None, ids


def _at(rows: Sequence[ObservationRow], end: datetime, cutoff: datetime) -> ObservationRow | None:
    starts = {row.interval_start for row in rows if row.interval_end == end}
    if not starts:
        return None
    return select_current_cutoff(
        rows,
        instrument_id=rows[0].instrument_id,
        basis=rows[0].basis,
        interval_start=min(starts),
        latest_feature_bar_end=end,
        feature_data_asof=cutoff,
    )


def _latest(rows: Iterable[ObservationRow]) -> tuple[ObservationRow, ...]:
    grouped: defaultdict[datetime, list[ObservationRow]] = defaultdict(list)
    for row in rows:
        grouped[row.interval_start].append(row)
    return tuple(
        sorted(
            (
                max(group, key=lambda row: (row.revision, row.global_position))
                for group in grouped.values()
            ),
            key=lambda row: row.interval_start,
        )
    )


def _index(
    rows: Sequence[ObservationRow],
) -> dict[tuple[str, PriceBasis], tuple[ObservationRow, ...]]:
    grouped: defaultdict[tuple[str, PriceBasis], list[ObservationRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.instrument_id, row.basis)].append(row)
    return {key: tuple(value) for key, value in grouped.items()}


def _ids(row: ObservationRow | None) -> tuple[str, ...]:
    return () if row is None else (str(row.event_id),)


def _missing_fraction(rows: Sequence[ObservationRow], end: datetime) -> float:
    expected = max(
        1, int((end - min((row.interval_end for row in rows), default=end)).total_seconds() // 60)
    )
    return max(
        0.0,
        min(
            1.0, 1.0 - len({row.interval_end for row in rows if row.interval_end <= end}) / expected
        ),
    )


def verify_raw_feature_dataset(
    dataset: R2FeatureDataset,
    observations: ObservationDataset,
    experiment: R2ExperimentConfig,
) -> None:
    """Verify feature lineage and cutoff evidence independently of stored values."""
    if dataset.observation_dataset_id != observations.dataset_id:
        raise ValueError("feature dataset observation identity differs from verified observations")
    if dataset.experiment_configuration_id != experiment.configuration_id:
        raise ValueError("feature dataset configuration identity differs from experiment")
    start, end = experiment.holdout_range
    by_event = {str(row.event_id): row for row in observations.rows}
    for feature_row in dataset.rows:
        if start <= feature_row.decision_time < end:
            raise ValueError("OOF feature dataset contains a locked holdout row")
        for value in feature_row.values:
            for event_id in value.source_event_ids:
                source = by_event.get(event_id)
                if source is None:
                    raise ValueError("feature lineage references an unknown observation event")
                if source.persisted_at > feature_row.feature_data_asof:
                    raise ValueError("feature lineage references an observation after its cutoff")
                if source.interval_end > feature_row.latest_feature_bar_end:
                    raise ValueError(
                        "feature lineage references an observation after its bar cutoff"
                    )
