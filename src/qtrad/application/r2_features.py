"""Deterministic, current-cutoff R2 raw feature materialisation."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from math import cos, log, pi, sin, sqrt

from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import (
    FoundationConfig,
    PanelDataset,
    PanelRow,
    PanelStatus,
    TargetDataset,
)
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


@dataclass(frozen=True, slots=True)
class R2FoundationInputs:
    """Complete, independently verified R1 children consumed by R2.B."""

    bundle_id: str
    configuration: FoundationConfig
    observations: ObservationDataset
    panel: PanelDataset
    targets: TargetDataset
    folds: FoldDataset
    source_active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]]


def materialise_r2_features(
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str = "L1",
) -> R2FeatureDataset:
    """Materialise only after authenticating every R1 child binding."""
    _verify_foundation_bindings(foundation, experiment)
    rows = build_raw_feature_rows(
        foundation,
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
    _reject_unsupported_eligible_families(experiment, schema)
    return R2FeatureDataset.create(
        rows,
        feature_schema=schema,
        observation_dataset_id=foundation.observations.dataset_id,
        panel_dataset_id=foundation.panel.dataset_id,
        target_dataset_id=foundation.targets.dataset_id,
        fold_dataset_id=foundation.folds.dataset_id,
        experiment_configuration_id=experiment.configuration_id,
        evidence_class=experiment.evidence_class,
    )


def _verify_foundation_bindings(
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
) -> None:
    configuration = foundation.configuration
    observations = foundation.observations
    panel = foundation.panel
    targets = foundation.targets
    folds = foundation.folds
    bindings = (
        (experiment.r1_bundle_id, foundation.bundle_id, "R1 bundle"),
        (experiment.observation_dataset_id, observations.dataset_id, "observation dataset"),
        (
            experiment.observation_dataset_id,
            configuration.observation_dataset_id,
            "observation configuration",
        ),
        (
            observations.dataset_id,
            panel.observation_dataset_id,
            "panel observation dataset",
        ),
        (
            observations.dataset_id,
            targets.observation_dataset_id,
            "target observation dataset",
        ),
        (
            experiment.foundation_configuration_id,
            configuration.configuration_id,
            "foundation configuration",
        ),
        (
            experiment.foundation_configuration_id,
            panel.foundation_configuration_id,
            "panel configuration",
        ),
        (
            experiment.foundation_configuration_id,
            targets.foundation_configuration_id,
            "target configuration",
        ),
        (
            experiment.foundation_configuration_id,
            folds.foundation_configuration_id,
            "fold configuration",
        ),
        (experiment.panel_dataset_id, panel.dataset_id, "panel dataset"),
        (experiment.target_dataset_id, targets.dataset_id, "target dataset"),
        (experiment.fold_dataset_id, folds.dataset_id, "fold dataset"),
        (folds.target_dataset_id, targets.dataset_id, "fold target dataset"),
    )
    for expected, actual, label in bindings:
        if expected != actual:
            raise ValueError(f"R2 experiment {label} binding differs from verified foundation")
    if experiment.ordered_instruments != configuration.ordered_instruments:
        raise ValueError("R2 experiment universe differs from verified foundation")
    if dict(experiment.instrument_roles) != dict(configuration.instrument_roles):
        raise ValueError("R2 experiment roles differ from verified foundation")
    if set(foundation.source_active_intervals) != set(configuration.ordered_instruments):
        raise ValueError("source-active evidence differs from verified foundation universe")


def _reject_unsupported_eligible_families(
    experiment: R2ExperimentConfig,
    schema: Sequence[FeatureDefinition],
) -> None:
    families = {item.family for item in schema}
    if (
        FeatureFamily.QUOTE_IMBALANCE in families
        and experiment.feature_eligibility[FeatureFamily.QUOTE_IMBALANCE].state.value == "ELIGIBLE"
    ):
        raise ValueError("eligible quote imbalance requires a validated quote-size source")


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
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str = "L1",
) -> tuple[RawFeatureRow, ...]:
    """Build OOF rows from one complete verified foundation."""
    _verify_foundation_bindings(foundation, experiment)
    feature_set = next(
        (item for item in experiment.feature_sets if item.name == feature_set_name), None
    )
    if feature_set is None:
        raise ValueError(f"unknown R2 feature set: {feature_set_name}")
    registry = feature_registry(experiment)
    families = set(feature_set.families)
    schema = tuple(item for item in registry if item.family in families)
    _reject_unsupported_eligible_families(experiment, schema)
    holdout_start, holdout_end = experiment.holdout_range
    panels = tuple(
        row
        for row in foundation.panel.rows
        if row.instrument_id in experiment.target_instruments
        and row.basis is PriceBasis.MID
        and row.status in {PanelStatus.OBSERVED, PanelStatus.MISSING_AS_OF_CUTOFF}
        and not (holdout_start <= row.decision_time < holdout_end)
    )
    indexed = _index(foundation.observations.rows)
    return tuple(
        sorted(
            (
                _build_row(row, indexed, panels, schema, experiment, feature_set_name, foundation)
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
    foundation: R2FoundationInputs,
) -> RawFeatureRow:
    target = indexed.get((panel.instrument_id, PriceBasis.MID), ())
    values = tuple(
        RawFeatureValue(
            definition.name,
            *_calculate(
                definition.name,
                panel,
                target,
                indexed,
                panels,
                experiment,
                foundation,
            ),
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
    foundation: R2FoundationInputs,
) -> tuple[float | None, tuple[str, ...]]:
    end, cutoff = panel.latest_feature_bar_end, panel.feature_data_asof
    current = _at(target, end, cutoff)
    if name.startswith("return_contrast_"):
        suffixes = name.removeprefix("return_contrast_").split("_")
        if len(suffixes) != 2:
            raise ValueError(f"invalid return contrast feature: {name}")
        short, short_ids = _return(f"return_{suffixes[0]}", target, end, cutoff)
        long, long_ids = _return(f"return_{suffixes[1]}", target, end, cutoff)
        lineage = _merge_ids(short_ids, long_ids)
        if short is None or long is None:
            return None, lineage
        return short - long, lineage
    if name.startswith("return_") and name.endswith("_available"):
        return_name = name.removesuffix("_available")
        value, lineage = _return(return_name, target, end, cutoff)
        return (1.0 if value is not None else 0.0), lineage
    return_suffix = name.removeprefix("return_")
    if name.startswith("return_") and return_suffix.endswith("s") and return_suffix[:-1].isdigit():
        return _return(name, target, end, cutoff)
    if name == "source_active":
        active = _source_active(
            foundation.source_active_intervals,
            panel.instrument_id,
            panel.latest_feature_bar_end,
            panel.feature_data_asof,
            foundation.configuration.grid_resolution,
        )
        return (1.0 if active else 0.0), _ids(current)
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
        return _missing_fraction(target, panel, foundation, experiment)
    if name == "cross_market_available_count":
        selected = tuple(
            _at(indexed.get((instrument, PriceBasis.MID), ()), end, cutoff)
            for instrument in sorted(
                {row.instrument_id for row in panels if row.decision_time == panel.decision_time}
            )
        )
        available = tuple(row for row in selected if row is not None)
        return float(len(available)), tuple(str(row.event_id) for row in available)
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
        return _rolling(
            name,
            panel.instrument_id,
            target,
            end,
            cutoff,
            foundation,
            experiment,
        )
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
        return _spread(name, panel, indexed, foundation, experiment)
    if name in {"quote_imbalance", "quote_imbalance_available"}:
        return None, ()
    if name.startswith(("loo_", "vix_context_")):
        return _context(name, panel, indexed, panels, experiment)
    if name in {"cross_market_missing_count", "cross_market_source_active_count"}:
        instruments = tuple(
            sorted(
                {row.instrument_id for row in panels if row.decision_time == panel.decision_time}
            )
        )
        active = tuple(
            instrument
            for instrument in instruments
            if _source_active(
                foundation.source_active_intervals,
                instrument,
                end,
                cutoff,
                foundation.configuration.grid_resolution,
            )
        )
        if name == "cross_market_source_active_count":
            return float(len(active)), ()
        selected = tuple(
            _at(indexed.get((instrument, PriceBasis.MID), ()), end, cutoff) for instrument in active
        )
        available = tuple(row for row in selected if row is not None)
        return float(len(active) - len(available)), tuple(str(row.event_id) for row in available)
    return None, ()


def _return(
    name: str, rows: Sequence[ObservationRow], end: datetime, cutoff: datetime
) -> tuple[float | None, tuple[str, ...]]:
    seconds = int(name.removeprefix("return_").removesuffix("s"))
    first = _at(rows, end - timedelta(seconds=seconds), cutoff)
    last = _at(rows, end, cutoff)
    if first is None or last is None:
        return None, _ids(first) + _ids(last)
    if _source_identity(first) != _source_identity(last):
        raise FeatureLineageError("return endpoints cross source lineage")
    if first.close <= 0 or last.close <= 0:
        return None, _ids(first) + _ids(last)
    return log(float(last.close)) - log(float(first.close)), _ids(first) + _ids(last)


def _rolling(
    name: str,
    instrument_id: str,
    rows: Sequence[ObservationRow],
    end: datetime,
    cutoff: datetime,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
) -> tuple[float | None, tuple[str, ...]]:
    seconds = int(name.rsplit("_", 1)[-1].removesuffix("s"))
    if seconds % 60:
        raise ValueError("rolling feature windows must be whole minutes")
    expected_ends = tuple(
        end - timedelta(minutes=offset) for offset in range(seconds // 60 - 1, -1, -1)
    )
    selected: list[ObservationRow | None] = []
    active_slots: list[bool] = []
    for interval_end in expected_ends:
        active = _source_active(
            foundation.source_active_intervals,
            instrument_id,
            interval_end,
            cutoff,
            foundation.configuration.grid_resolution,
        )
        active_slots.append(active)
        selected.append(_at(rows, interval_end, cutoff) if active else None)
    observed = tuple(row for row in selected if row is not None)
    lineage = tuple(str(row.event_id) for row in observed)
    if len({_source_identity(row) for row in observed}) > 1:
        raise FeatureLineageError("rolling feature window crosses source lineage")
    expected_active = sum(active_slots)
    coverage = len(observed) / expected_active if expected_active else None
    if name.startswith("available_interval_count_"):
        return float(len(observed)), lineage
    if name.startswith("window_coverage_"):
        return coverage, lineage
    threshold = experiment.feature_coverage_thresholds[FeatureFamily.LOCAL_VOLATILITY_RANGE]
    if coverage is None or coverage < threshold:
        return None, lineage
    if name.startswith("mean_log_range_"):
        ranges = [
            log(float(row.high)) - log(float(row.low))
            for row in observed
            if row.low > 0 and row.high > 0
        ]
        return (sum(ranges) / len(ranges) if ranges else None), lineage
    expected_pairs = sum(left and right for left, right in pairwise(active_slots))
    returns = [
        log(float(right.close)) - log(float(left.close))
        for left, right in pairwise(selected)
        if left is not None
        and right is not None
        and left.interval_end + timedelta(minutes=1) == right.interval_end
        and left.close > 0
        and right.close > 0
    ]
    return_coverage = len(returns) / expected_pairs if expected_pairs else None
    if return_coverage is None or return_coverage < threshold:
        return None, lineage
    if name.startswith("realised_std_"):
        mean = sum(returns) / len(returns)
        return sqrt(sum((value - mean) ** 2 for value in returns) / len(returns)), lineage
    if name.startswith("mean_absolute_return_"):
        return sum(abs(value) for value in returns) / len(returns), lineage
    if name.startswith("return_sign_balance_"):
        return sum(value > 0 for value in returns) / len(returns), lineage
    raise ValueError(f"unsupported rolling feature: {name}")


def _context(
    name: str,
    panel: PanelRow,
    indexed: Mapping[tuple[str, PriceBasis], tuple[ObservationRow, ...]],
    panels: Sequence[PanelRow],
    experiment: R2ExperimentConfig,
) -> tuple[float | None, tuple[str, ...]]:
    suffix = name.rsplit("_", 1)[-1]
    seconds = int(suffix.removesuffix("s")) if suffix.endswith("s") else 60

    def returns_for(instruments: Sequence[str]) -> tuple[list[float], list[str]]:
        values: list[float] = []
        lineage: list[str] = []
        for instrument in instruments:
            value, ids = _return(
                f"return_{seconds}s",
                indexed.get((instrument, PriceBasis.MID), ()),
                panel.latest_feature_bar_end,
                panel.feature_data_asof,
            )
            if value is not None:
                values.append(value)
                lineage.extend(ids)
        return values, lineage

    peers = tuple(
        instrument
        for instrument in experiment.target_instruments
        if instrument != panel.instrument_id
    )
    group = experiment.market_groups[panel.instrument_id]
    group_peers = tuple(
        instrument for instrument in peers if experiment.market_groups[instrument] == group
    )
    if name.startswith("vix_context_return"):
        vix = tuple(
            instrument
            for instrument in experiment.ordered_instruments
            if experiment.instrument_roles[instrument].value == "CONTEXT"
            and instrument == "index:volatility"
        )
        values, lineage = returns_for(vix)
        return (values[0] if values else None), tuple(lineage)

    instruments = group_peers if "market_group" in name else peers
    values, lineage = returns_for(instruments)
    if not values:
        return None, tuple(lineage)
    mean = sum(values) / len(values)
    if name.startswith(("loo_mean_return", "loo_market_group_mean")):
        return mean, tuple(lineage)
    if name.startswith("loo_median_return"):
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = (
            ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        )
        return median, tuple(lineage)
    if name.startswith(("loo_return_dispersion", "loo_market_group_dispersion")):
        return sqrt(sum((value - mean) ** 2 for value in values) / len(values)), tuple(lineage)
    if name.startswith("loo_positive_proportion"):
        return sum(value > 0 for value in values) / len(values), tuple(lineage)
    if name.startswith(("loo_available_count", "loo_market_group_available_count")):
        return float(len(values)), tuple(lineage)
    return None, tuple(lineage)


def _spread(
    name: str,
    panel: PanelRow,
    indexed: Mapping[tuple[str, PriceBasis], tuple[ObservationRow, ...]],
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
) -> tuple[float | None, tuple[str, ...]]:
    if name in {"rolling_spread_mean", "rolling_spread_change", "spread_coverage"}:
        window = max(experiment.feature_windows)
        minutes = int(window.total_seconds() // 60)
        values: list[float] = []
        lineage: list[str] = []
        expected_active = 0
        sources: set[tuple[object, ...]] = set()
        for offset in range(minutes - 1, -1, -1):
            interval_end = panel.latest_feature_bar_end - timedelta(minutes=offset)
            if not _source_active(
                foundation.source_active_intervals,
                panel.instrument_id,
                interval_end,
                panel.feature_data_asof,
                foundation.configuration.grid_resolution,
            ):
                continue
            expected_active += 1
            value, ids, source = _spread_at(
                indexed, panel.instrument_id, interval_end, panel.feature_data_asof
            )
            lineage.extend(ids)
            if source is not None:
                sources.add(source)
            if value is not None:
                values.append(value)
        if len(sources) > 1:
            raise FeatureLineageError("rolling spread window crosses source lineage")
        coverage = len(values) / expected_active if expected_active else None
        if name == "spread_coverage":
            return coverage, tuple(lineage)
        threshold = experiment.feature_coverage_thresholds[FeatureFamily.SPREAD]
        if coverage is None or coverage < threshold or not values:
            return None, tuple(lineage)
        if name == "rolling_spread_mean":
            return sum(values) / len(values), tuple(lineage)
        return values[-1] - values[0], tuple(lineage)
    value, direct_lineage, _ = _spread_at(
        indexed,
        panel.instrument_id,
        panel.latest_feature_bar_end,
        panel.feature_data_asof,
        name=name,
    )
    return value, direct_lineage


def _merge_ids(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in groups for item in group))


def _source_identity(row: ObservationRow) -> tuple[object, ...]:
    return (
        row.source_provider,
        row.source_environment,
        row.source_external_id,
        row.provenance,
    )


def _spread_identity(row: ObservationRow) -> tuple[object, ...]:
    return (
        row.interval_start,
        row.interval_end,
        *_source_identity(row),
        row.sample_count,
        row.quality,
    )


def _spread_at(
    indexed: Mapping[tuple[str, PriceBasis], tuple[ObservationRow, ...]],
    instrument_id: str,
    interval_end: datetime,
    cutoff: datetime,
    *,
    name: str = "spread_fraction",
) -> tuple[float | None, tuple[str, ...], tuple[object, ...] | None]:
    bid = _at(indexed.get((instrument_id, PriceBasis.BID), ()), interval_end, cutoff)
    ask = _at(indexed.get((instrument_id, PriceBasis.ASK), ()), interval_end, cutoff)
    mid = _at(indexed.get((instrument_id, PriceBasis.MID), ()), interval_end, cutoff)
    ids = _ids(bid) + _ids(ask) + _ids(mid)
    if bid is None or ask is None or mid is None or mid.close <= 0:
        return None, ids, None
    if _spread_identity(bid) != _spread_identity(ask) or _spread_identity(bid) != _spread_identity(
        mid
    ):
        return None, ids, None
    if ask.close < bid.close:
        return None, ids, None
    spread = float(ask.close - bid.close)
    source = _source_identity(mid)
    if name == "close_spread":
        return spread, ids, source
    if name == "spread_fraction":
        return spread / float(mid.close), ids, source
    if name == "spread_bps":
        return spread * 10_000 / float(mid.close), ids, source
    return spread, ids, source


def _at(rows: Sequence[ObservationRow], end: datetime, cutoff: datetime) -> ObservationRow | None:
    starts = {row.interval_start for row in rows if row.interval_end == end}
    if not starts:
        return None
    if len(starts) != 1:
        raise FeatureLineageError("feature interval has ambiguous interval identity")
    return select_current_cutoff(
        rows,
        instrument_id=rows[0].instrument_id,
        basis=rows[0].basis,
        interval_start=min(starts),
        latest_feature_bar_end=end,
        feature_data_asof=cutoff,
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


def _source_active(
    intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]],
    instrument_id: str,
    interval_end: datetime,
    feature_data_asof: datetime,
    resolution: timedelta,
) -> bool:
    if interval_end > feature_data_asof:
        return False
    expected_start = interval_end - resolution
    return any(
        active_start <= expected_start and active_end >= interval_end
        for active_start, active_end in intervals.get(instrument_id, ())
    )


def _missing_fraction(
    rows: Sequence[ObservationRow],
    panel: PanelRow,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
) -> tuple[float | None, tuple[str, ...]]:
    window = max(experiment.feature_windows)
    minutes = int(window.total_seconds() // 60)
    expected_active = 0
    observed = 0
    lineage: list[str] = []
    for offset in range(minutes - 1, -1, -1):
        interval_end = panel.latest_feature_bar_end - timedelta(minutes=offset)
        if not _source_active(
            foundation.source_active_intervals,
            panel.instrument_id,
            interval_end,
            panel.feature_data_asof,
            foundation.configuration.grid_resolution,
        ):
            continue
        expected_active += 1
        selected = _at(
            rows,
            interval_end,
            panel.feature_data_asof,
        )
        if selected is not None:
            observed += 1
            lineage.append(str(selected.event_id))
    if expected_active == 0:
        return None, tuple(lineage)
    return 1.0 - observed / expected_active, tuple(lineage)


def verify_raw_feature_dataset(
    dataset: R2FeatureDataset,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
) -> None:
    """Reconstruct and compare a feature child against verified R1 inputs."""
    _verify_foundation_bindings(foundation, experiment)
    expected_ids = (
        ("observation", dataset.observation_dataset_id, foundation.observations.dataset_id),
        ("panel", dataset.panel_dataset_id, foundation.panel.dataset_id),
        ("target", dataset.target_dataset_id, foundation.targets.dataset_id),
        ("fold", dataset.fold_dataset_id, foundation.folds.dataset_id),
    )
    for name, actual, expected in expected_ids:
        if actual != expected:
            raise ValueError(f"feature dataset {name} identity differs from verified foundation")
    if dataset.experiment_configuration_id != experiment.configuration_id:
        raise ValueError("feature dataset configuration identity differs from experiment")
    feature_set_ids = {row.feature_set_id for row in dataset.rows}
    matching_sets = tuple(
        feature_set.name
        for feature_set in experiment.feature_sets
        if len(feature_set_ids) == 1
        and feature_set_id(
            experiment.configuration_id,
            feature_set.name,
            dataset.feature_schema,
        )
        == next(iter(feature_set_ids), None)
    )
    if len(matching_sets) != 1:
        raise ValueError(
            "feature dataset schema does not identify exactly one declared feature set"
        )
    expected_rows = build_raw_feature_rows(
        foundation,
        experiment,
        feature_set_name=matching_sets[0],
    )
    if dataset.rows != expected_rows:
        raise ValueError("feature dataset rows differ from deterministic causal replay")
    expected = R2FeatureDataset.create(
        expected_rows,
        feature_schema=dataset.feature_schema,
        observation_dataset_id=foundation.observations.dataset_id,
        panel_dataset_id=foundation.panel.dataset_id,
        target_dataset_id=foundation.targets.dataset_id,
        fold_dataset_id=foundation.folds.dataset_id,
        experiment_configuration_id=experiment.configuration_id,
        evidence_class=experiment.evidence_class,
    )
    if dataset != expected:
        raise ValueError("feature dataset semantic identity differs from deterministic replay")
