"""Deterministic, current-cutoff R2 raw-feature materialisation."""

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from math import cos, log, pi, sin, sqrt
from types import MappingProxyType
from typing import Protocol

from qtrad.application.foundation import observation_availability_time
from qtrad.application.r2_readiness import (
    source_active_intervals_from_evidence,
    verify_exact_r1_bindings,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import (
    AvailabilityBasis,
    FoundationConfig,
    InstrumentRole,
    PanelAuditDisposition,
    PanelDataset,
    PanelRow,
    PanelStatus,
    TargetDataset,
)
from qtrad.domain.foundation_bundle import FoundationBundle
from qtrad.domain.market_data import MarketDataSourceClass, PriceBasis
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_registry,
    feature_set_id,
)
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily, R2ExperimentConfig
from qtrad.domain.research import ObservationDataset, ObservationRow

type ObservationKey = tuple[str, PriceBasis, datetime, datetime]
type ObservationIndex = Mapping[ObservationKey, tuple[ObservationRow, ...]]


@dataclass(frozen=True, slots=True)
class _RollingWindow:
    observed_count: int
    interval_coverage: float | None
    range_values: tuple[float, ...]
    range_coverage: float | None
    returns: tuple[float, ...]
    return_coverage: float | None
    range_lineage: tuple[str, ...]
    return_lineage: tuple[str, ...]


type SelectedCacheKey = tuple[str, PriceBasis, datetime, datetime, datetime, AvailabilityBasis]


def _empty_selected_cache() -> dict[SelectedCacheKey, ObservationRow | None]:
    return {}


def _empty_return_cache() -> dict[tuple[str, datetime, int], tuple[float | None, tuple[str, ...]]]:
    return {}


def _empty_rolling_cache() -> dict[tuple[str, datetime, int], _RollingWindow]:
    return {}


@dataclass(slots=True)
class _RowCache:
    selected: dict[SelectedCacheKey, ObservationRow | None] = field(
        default_factory=_empty_selected_cache
    )
    returns: dict[tuple[str, datetime, int], tuple[float | None, tuple[str, ...]]] = field(
        default_factory=_empty_return_cache
    )
    rolling: dict[tuple[str, datetime, int], _RollingWindow] = field(
        default_factory=_empty_rolling_cache
    )


@dataclass(frozen=True, slots=True)
class R2FoundationInputs:
    """Complete, independently verified R1 children consumed by R2.B."""

    bundle: FoundationBundle
    configuration: FoundationConfig
    observations: ObservationDataset
    panel: PanelDataset
    targets: TargetDataset
    folds: FoldDataset
    availability_evidence: Mapping[str, JsonValue]
    _authenticated_source_active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]] = (
        field(init=False, repr=False)
    )

    def __post_init__(self) -> None:
        intervals = source_active_intervals_from_evidence(self.availability_evidence)
        object.__setattr__(
            self,
            "_authenticated_source_active_intervals",
            MappingProxyType(
                {instrument: tuple(values) for instrument, values in intervals.items()}
            ),
        )

    @property
    def source_active_intervals(self) -> Mapping[str, tuple[tuple[datetime, datetime], ...]]:
        """Return the parsed activity evidence derived from the authenticated payload."""
        return self._authenticated_source_active_intervals


class FeatureLineageError(ValueError):
    """Raised when cutoff selection cannot identify one source revision."""


def feature_schema_for_set(
    experiment: R2ExperimentConfig,
    feature_set_name: str,
) -> tuple[FeatureDefinition, ...]:
    """Resolve one declared feature set to its deterministic ordered schema."""
    feature_set = next(
        (item for item in experiment.feature_sets if item.name == feature_set_name), None
    )
    if feature_set is None:
        raise ValueError(f"unknown R2 feature set: {feature_set_name}")
    registry = feature_registry(experiment)
    families = set(feature_set.families)
    schema = tuple(item for item in registry if item.family in families)
    if not schema:
        raise ValueError(f"declared R2 feature set has no schema: {feature_set_name}")
    _reject_unsupported_eligible_families(experiment, schema)
    return schema


def materialise_r2_features(
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str,
) -> R2FeatureDataset:
    """Materialise a small in-memory dataset after authenticating every R1 child binding."""
    _verify_foundation_bindings(foundation, experiment)
    schema = feature_schema_for_set(experiment, feature_set_name)
    rows = build_raw_feature_rows(
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    return R2FeatureDataset.create(
        rows,
        feature_schema=schema,
        feature_set_name=feature_set_name,
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
    """Run the sole R1 identity gate before deriving any feature value."""
    verify_exact_r1_bindings(foundation, experiment)
    if set(foundation.source_active_intervals) != set(foundation.configuration.ordered_instruments):
        raise ValueError("R2 foundation binding has incomplete source-active evidence")
    if tuple(foundation.configuration.ordered_instruments) != tuple(experiment.ordered_instruments):
        raise ValueError("R2 foundation binding has a different configured universe")


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


def select_current_cutoff(
    observations: Sequence[ObservationRow],
    *,
    instrument_id: str,
    basis: PriceBasis,
    interval_start: datetime,
    latest_feature_bar_end: datetime,
    feature_data_asof: datetime,
    availability_basis: AvailabilityBasis,
) -> ObservationRow | None:
    """Select one exact interval's highest revision visible at the causal cutoff."""
    if latest_feature_bar_end > feature_data_asof:
        return None
    candidates = [
        row
        for row in observations
        if row.instrument_id == instrument_id
        and row.basis is basis
        and row.interval_start == interval_start
        and row.interval_end == latest_feature_bar_end
        and observation_availability_time(row, availability_basis) <= feature_data_asof
    ]
    if not candidates:
        return None
    sources = {_source_identity(row) for row in candidates}
    if len(sources) != 1:
        raise FeatureLineageError("feature interval has ambiguous source lineage")
    revision = max(row.revision for row in candidates)
    selected = [row for row in candidates if row.revision == revision]
    if len(selected) != 1:
        raise FeatureLineageError("feature interval has ambiguous highest revision")
    return selected[0]


def iter_raw_feature_rows(
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str,
) -> Iterator[RawFeatureRow]:
    """Yield canonical feature rows without retaining the output row collection."""
    _verify_foundation_bindings(foundation, experiment)
    schema = feature_schema_for_set(experiment, feature_set_name)
    set_identity = feature_set_id(experiment.configuration_id, feature_set_name, schema)
    yield from _iter_raw_feature_rows(
        foundation,
        experiment,
        schema=schema,
        feature_set_name=feature_set_name,
        feature_set_identity=set_identity,
    )


def build_raw_feature_rows(
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str,
) -> tuple[RawFeatureRow, ...]:
    """Build canonical rows for bounded unit fixtures."""
    return tuple(
        iter_raw_feature_rows(
            foundation,
            experiment,
            feature_set_name=feature_set_name,
        )
    )


def _iter_raw_feature_rows(
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    schema: Sequence[FeatureDefinition],
    feature_set_name: str,
    feature_set_identity: str,
) -> Iterator[RawFeatureRow]:
    holdout_start, holdout_end = experiment.holdout_range
    panels = sorted(
        (
            row
            for row in foundation.panel.rows
            if row.instrument_id in experiment.target_instruments
            and row.basis is PriceBasis.MID
            and row.status in {PanelStatus.OBSERVED, PanelStatus.MISSING_AS_OF_CUTOFF}
            and not (holdout_start <= row.decision_time < holdout_end)
        ),
        key=_panel_key,
    )
    indexed = _index(foundation.observations.rows)
    for panel in panels:
        yield _build_row(
            panel,
            indexed,
            schema,
            experiment,
            foundation,
            feature_set_identity,
        )


def _panel_key(row: PanelRow) -> tuple[object, ...]:
    return (
        row.decision_time,
        row.instrument_id,
        row.basis.value,
        row.feature_data_asof,
        row.latest_feature_bar_end,
    )


def _build_row(
    panel: PanelRow,
    indexed: ObservationIndex,
    schema: Sequence[FeatureDefinition],
    experiment: R2ExperimentConfig,
    foundation: R2FoundationInputs,
    feature_set_identity: str,
) -> RawFeatureRow:
    cache = _RowCache()
    values = tuple(
        RawFeatureValue(
            definition.name,
            *_calculate(
                definition.name,
                panel,
                indexed,
                experiment,
                foundation,
                cache,
            ),
        )
        for definition in schema
    )
    return RawFeatureRow(
        target_instrument_id=panel.instrument_id,
        decision_time=panel.decision_time,
        feature_data_asof=panel.feature_data_asof,
        latest_feature_bar_end=panel.latest_feature_bar_end,
        feature_set_id=feature_set_identity,
        values=values,
    )


def _calculate(
    name: str,
    panel: PanelRow,
    indexed: ObservationIndex,
    experiment: R2ExperimentConfig,
    foundation: R2FoundationInputs,
    cache: _RowCache,
) -> tuple[float | None, tuple[str, ...]]:
    end, cutoff = panel.latest_feature_bar_end, panel.feature_data_asof
    current = _at(
        indexed,
        panel.instrument_id,
        PriceBasis.MID,
        end,
        cutoff,
        foundation,
        cache,
    )
    if name.startswith("return_contrast_"):
        suffixes = name.removeprefix("return_contrast_").split("_")
        if len(suffixes) != 2:
            raise ValueError(f"invalid return contrast feature: {name}")
        short, short_ids = _return(
            f"return_{suffixes[0]}",
            indexed,
            panel.instrument_id,
            end,
            cutoff,
            foundation,
            cache,
        )
        long, long_ids = _return(
            f"return_{suffixes[1]}",
            indexed,
            panel.instrument_id,
            end,
            cutoff,
            foundation,
            cache,
        )
        lineage = _merge_ids(short_ids, long_ids)
        if short is None or long is None:
            return None, lineage
        return short - long, lineage
    if name.startswith("return_") and name.endswith("_available"):
        value, lineage = _return(
            name.removesuffix("_available"),
            indexed,
            panel.instrument_id,
            end,
            cutoff,
            foundation,
            cache,
        )
        return (1.0 if value is not None else 0.0), lineage
    if name.startswith("return_"):
        suffix = name.removeprefix("return_")
        if suffix.endswith("s") and suffix[:-1].isdigit():
            return _return(
                name,
                indexed,
                panel.instrument_id,
                end,
                cutoff,
                foundation,
                cache,
            )
    if name == "source_active":
        active = _source_active(
            foundation.source_active_intervals,
            panel.instrument_id,
            end,
            cutoff,
            foundation.configuration.grid_resolution,
        )
        return (1.0 if active else 0.0), _ids(current)
    if name == "quality_healthy":
        return (1.0 if current and current.quality.value == "HEALTHY" else 0.0), _ids(current)
    if name == "gap_known_by_cutoff":
        return (
            1.0
            if panel.audit_disposition is PanelAuditDisposition.RECORDED_GAP_KNOWN_BY_CUTOFF
            else 0.0
        ), ()
    if name.startswith("utc_"):
        minute = panel.decision_time.hour * 60 + panel.decision_time.minute
        if name == "utc_minute_sin":
            return sin(2 * pi * minute / 1440), ()
        if name == "utc_minute_cos":
            return cos(2 * pi * minute / 1440), ()
        day = panel.decision_time.weekday()
        return (sin if name == "utc_day_sin" else cos)(2 * pi * day / 7), ()
    if name == "target_feature_missing_fraction":
        return _missing_fraction(indexed, panel, foundation, experiment, cache)
    if name == "cross_market_available_count":
        selected = tuple(
            _at(
                indexed,
                instrument,
                PriceBasis.MID,
                end,
                cutoff,
                foundation,
                cache,
            )
            for instrument in _cross_asset_peers(experiment, panel.instrument_id)
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
            indexed,
            end,
            cutoff,
            foundation,
            experiment,
            cache,
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
        return _spread(name, panel, indexed, foundation, experiment, cache)
    if name in {"quote_imbalance", "quote_imbalance_available"}:
        return None, ()
    if name.startswith(("loo_", "vix_context_")):
        return _context(name, panel, indexed, experiment, foundation, cache)
    if name in {"cross_market_missing_count", "cross_market_source_active_count"}:
        peers = _cross_asset_peers(experiment, panel.instrument_id)
        active = tuple(
            instrument
            for instrument in peers
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
            _at(
                indexed,
                instrument,
                PriceBasis.MID,
                end,
                cutoff,
                foundation,
                cache,
            )
            for instrument in active
        )
        available = tuple(row for row in selected if row is not None)
        return float(len(active) - len(available)), tuple(str(row.event_id) for row in available)
    return None, ()


def _return(
    name: str,
    indexed: ObservationIndex,
    instrument_id: str,
    end: datetime,
    cutoff: datetime,
    foundation: R2FoundationInputs,
    cache: _RowCache,
) -> tuple[float | None, tuple[str, ...]]:
    suffix = name.removeprefix("return_").removesuffix("s")
    if not suffix.isdigit() or int(suffix) <= 0:
        raise ValueError(f"invalid return feature: {name}")
    seconds = int(suffix)
    key = (instrument_id, end, seconds)
    if key in cache.returns:
        return cache.returns[key]
    first = _at(
        indexed,
        instrument_id,
        PriceBasis.MID,
        end - timedelta(seconds=seconds),
        cutoff,
        foundation,
        cache,
    )
    last = _at(
        indexed,
        instrument_id,
        PriceBasis.MID,
        end,
        cutoff,
        foundation,
        cache,
    )
    lineage = _merge_ids(_ids(first), _ids(last))
    if first is None or last is None:
        result = (None, lineage)
    elif _source_identity(first) != _source_identity(last):
        raise FeatureLineageError("return endpoints cross source lineage")
    elif first.close <= 0 or last.close <= 0:
        result = (None, lineage)
    else:
        result = (log(float(last.close)) - log(float(first.close)), lineage)
    cache.returns[key] = result
    return result


def _rolling(
    name: str,
    instrument_id: str,
    indexed: ObservationIndex,
    end: datetime,
    cutoff: datetime,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    cache: _RowCache,
) -> tuple[float | None, tuple[str, ...]]:
    suffix = name.rsplit("_", 1)[-1].removesuffix("s")
    if not suffix.isdigit():
        raise ValueError(f"invalid rolling feature: {name}")
    seconds = int(suffix)
    window = _rolling_window(instrument_id, seconds, indexed, end, cutoff, foundation, cache)
    if name.startswith("available_interval_count_"):
        return float(window.observed_count), window.range_lineage
    if name.startswith("window_coverage_"):
        return window.interval_coverage, window.range_lineage
    threshold = experiment.feature_coverage_thresholds[FeatureFamily.LOCAL_VOLATILITY_RANGE]
    if name.startswith("mean_log_range_"):
        if window.range_coverage is None or window.range_coverage < threshold:
            return None, window.range_lineage
        return sum(window.range_values) / len(window.range_values), window.range_lineage
    if window.return_coverage is None or window.return_coverage < threshold:
        return None, window.return_lineage
    if name.startswith("realised_std_"):
        mean = sum(window.returns) / len(window.returns)
        value = sqrt(sum((item - mean) ** 2 for item in window.returns) / len(window.returns))
        return value, window.return_lineage
    if name.startswith("mean_absolute_return_"):
        return sum(abs(item) for item in window.returns) / len(
            window.returns
        ), window.return_lineage
    if name.startswith("return_sign_balance_"):
        return sum(item > 0 for item in window.returns) / len(window.returns), window.return_lineage
    raise ValueError(f"unsupported rolling feature: {name}")


def _rolling_window(
    instrument_id: str,
    seconds: int,
    indexed: ObservationIndex,
    end: datetime,
    cutoff: datetime,
    foundation: R2FoundationInputs,
    cache: _RowCache,
) -> _RollingWindow:
    key = (instrument_id, end, seconds)
    if key in cache.rolling:
        return cache.rolling[key]
    resolution = foundation.configuration.grid_resolution
    if seconds <= 0 or seconds % 60 or resolution != timedelta(minutes=1):
        raise ValueError(
            "rolling features require positive whole-minute windows and one-minute bars"
        )
    minutes = seconds // 60
    endpoint_ends = tuple(end - timedelta(minutes=offset) for offset in range(minutes, -1, -1))
    active_slots = tuple(
        _source_active(
            foundation.source_active_intervals,
            instrument_id,
            interval_end,
            cutoff,
            resolution,
        )
        for interval_end in endpoint_ends
    )
    selected = tuple(
        _at(indexed, instrument_id, PriceBasis.MID, interval_end, cutoff, foundation, cache)
        if active
        else None
        for interval_end, active in zip(endpoint_ends, active_slots, strict=True)
    )
    observed_endpoints = tuple(row for row in selected if row is not None)
    if len({_source_identity(row) for row in observed_endpoints}) > 1:
        raise FeatureLineageError("rolling feature window crosses source lineage")
    observed_range = tuple(row for row in selected[1:] if row is not None)
    expected_range = sum(active_slots[1:])
    interval_coverage = len(observed_range) / expected_range if expected_range else None
    range_values = tuple(
        log(float(row.high)) - log(float(row.low))
        for row in observed_range
        if row.low > 0 and row.high > 0
    )
    range_coverage = len(range_values) / expected_range if expected_range else None
    expected_pairs = sum(
        left_active and right_active for left_active, right_active in pairwise(active_slots)
    )
    returns = tuple(
        log(float(right.close)) - log(float(left.close))
        for left, right in pairwise(selected)
        if left is not None
        and right is not None
        and left.interval_end + resolution == right.interval_end
        and left.close > 0
        and right.close > 0
    )
    return_coverage = len(returns) / expected_pairs if expected_pairs else None
    result = _RollingWindow(
        observed_count=len(observed_range),
        interval_coverage=interval_coverage,
        range_values=range_values,
        range_coverage=range_coverage,
        returns=returns,
        return_coverage=return_coverage,
        range_lineage=tuple(str(row.event_id) for row in observed_range),
        return_lineage=tuple(str(row.event_id) for row in observed_endpoints),
    )
    cache.rolling[key] = result
    return result


def _cross_asset_peers(
    experiment: R2ExperimentConfig,
    instrument_id: str,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            instrument
            for instrument in experiment.target_instruments
            if instrument != instrument_id
        )
    )


def _context(
    name: str,
    panel: PanelRow,
    indexed: ObservationIndex,
    experiment: R2ExperimentConfig,
    foundation: R2FoundationInputs,
    cache: _RowCache,
) -> tuple[float | None, tuple[str, ...]]:
    suffix = name.rsplit("_", 1)[-1]
    seconds = int(suffix.removesuffix("s")) if suffix.endswith("s") else 60

    def returns_for(instruments: Sequence[str]) -> tuple[list[float], list[str]]:
        values: list[float] = []
        lineage: list[str] = []
        for instrument in instruments:
            value, ids = _return(
                f"return_{seconds}s",
                indexed,
                instrument,
                panel.latest_feature_bar_end,
                panel.feature_data_asof,
                foundation,
                cache,
            )
            if value is not None:
                values.append(value)
                lineage.extend(ids)
        return values, lineage

    if name.startswith("vix_context_return"):
        context_universe = tuple(
            sorted(
                instrument
                for instrument in experiment.ordered_instruments
                if InstrumentRole(experiment.instrument_roles[instrument]) is InstrumentRole.CONTEXT
                and instrument == "index:volatility"
            )
        )
        values, lineage = returns_for(context_universe)
        return (values[0] if values else None), tuple(lineage)
    if "market_group" in name:
        group = experiment.market_groups.get(panel.instrument_id)
        if group is None:
            return None, ()
        instruments = tuple(
            instrument
            for instrument in _cross_asset_peers(experiment, panel.instrument_id)
            if experiment.market_groups.get(instrument) == group
        )
    else:
        instruments = _cross_asset_peers(experiment, panel.instrument_id)
    values, lineage = returns_for(instruments)
    is_count = name.startswith(("loo_available_count", "loo_market_group_available_count"))
    if is_count:
        return float(len(values)), tuple(lineage)
    expected = len(instruments)
    coverage = len(values) / expected if expected else None
    threshold = experiment.feature_coverage_thresholds[FeatureFamily.POOLED_CROSS_ASSET]
    if coverage is None or coverage < threshold:
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
    return None, tuple(lineage)


def _spread(
    name: str,
    panel: PanelRow,
    indexed: ObservationIndex,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    cache: _RowCache,
) -> tuple[float | None, tuple[str, ...]]:
    if name in {"rolling_spread_mean", "rolling_spread_change", "spread_coverage"}:
        window = max(experiment.feature_windows)
        if window.total_seconds() % 60:
            raise ValueError("spread windows require whole minutes")
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
                indexed,
                panel.instrument_id,
                interval_end,
                panel.feature_data_asof,
                foundation,
                cache,
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
        foundation,
        cache,
    )
    if name == "close_spread" or name == "spread_fraction" or name == "spread_bps":
        return _spread_value(name, value, direct_lineage, indexed, panel, foundation, cache)
    return value, direct_lineage


def _spread_value(
    name: str,
    value: float | None,
    lineage: tuple[str, ...],
    indexed: ObservationIndex,
    panel: PanelRow,
    foundation: R2FoundationInputs,
    cache: _RowCache,
) -> tuple[float | None, tuple[str, ...]]:
    if value is None:
        return None, lineage
    mid = _at(
        indexed,
        panel.instrument_id,
        PriceBasis.MID,
        panel.latest_feature_bar_end,
        panel.feature_data_asof,
        foundation,
        cache,
    )
    if mid is None or mid.close <= 0:
        return None, lineage
    if name == "close_spread":
        return value, lineage
    if name == "spread_fraction":
        return value / float(mid.close), lineage
    return value * 10_000 / float(mid.close), lineage


def _spread_at(
    indexed: ObservationIndex,
    instrument_id: str,
    interval_end: datetime,
    cutoff: datetime,
    foundation: R2FoundationInputs,
    cache: _RowCache,
) -> tuple[float | None, tuple[str, ...], tuple[object, ...] | None]:
    bid = _at(
        indexed,
        instrument_id,
        PriceBasis.BID,
        interval_end,
        cutoff,
        foundation,
        cache,
    )
    ask = _at(
        indexed,
        instrument_id,
        PriceBasis.ASK,
        interval_end,
        cutoff,
        foundation,
        cache,
    )
    mid = _at(
        indexed,
        instrument_id,
        PriceBasis.MID,
        interval_end,
        cutoff,
        foundation,
        cache,
    )
    ids = _merge_ids(_ids(bid), _ids(ask), _ids(mid))
    if bid is None or ask is None or mid is None or mid.close <= 0:
        return None, ids, None
    if _spread_identity(bid) != _spread_identity(ask) or _spread_identity(bid) != _spread_identity(
        mid
    ):
        return None, ids, None
    if ask.close < bid.close:
        return None, ids, None
    return float(ask.close - bid.close), ids, _source_identity(mid)


def _at(
    indexed: ObservationIndex,
    instrument_id: str,
    basis: PriceBasis,
    interval_end: datetime,
    cutoff: datetime,
    foundation: R2FoundationInputs,
    cache: _RowCache,
) -> ObservationRow | None:
    resolution = foundation.configuration.grid_resolution
    interval_start = interval_end - resolution
    if not _source_active(
        foundation.source_active_intervals,
        instrument_id,
        interval_end,
        cutoff,
        resolution,
    ):
        return None
    key = (
        instrument_id,
        basis,
        interval_start,
        interval_end,
        cutoff,
        foundation.configuration.availability_basis,
    )
    if key not in cache.selected:
        cache.selected[key] = select_current_cutoff(
            indexed.get((instrument_id, basis, interval_start, interval_end), ()),
            instrument_id=instrument_id,
            basis=basis,
            interval_start=interval_start,
            latest_feature_bar_end=interval_end,
            feature_data_asof=cutoff,
            availability_basis=foundation.configuration.availability_basis,
        )
    return cache.selected[key]


def _index(rows: Sequence[ObservationRow]) -> dict[ObservationKey, tuple[ObservationRow, ...]]:
    grouped: defaultdict[ObservationKey, list[ObservationRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.instrument_id, row.basis, row.interval_start, row.interval_end)].append(row)
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
        active_start <= expected_start and interval_end <= active_end
        for active_start, active_end in intervals.get(instrument_id, ())
    )


def _missing_fraction(
    indexed: ObservationIndex,
    panel: PanelRow,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    cache: _RowCache | None = None,
) -> tuple[float | None, tuple[str, ...]]:
    row_cache = cache or _RowCache()
    window = max(experiment.feature_windows)
    if window.total_seconds() % 60:
        raise ValueError("missingness windows require whole minutes")
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
            indexed,
            panel.instrument_id,
            PriceBasis.MID,
            interval_end,
            panel.feature_data_asof,
            foundation,
            row_cache,
        )
        if selected is not None:
            observed += 1
            lineage.append(str(selected.event_id))
    if expected_active == 0:
        return None, tuple(lineage)
    return 1.0 - observed / expected_active, tuple(lineage)


class R2FeatureManifestBindings(Protocol):
    """Persisted feature metadata required before replaying any row."""

    @property
    def feature_schema(self) -> tuple[FeatureDefinition, ...]: ...

    @property
    def feature_set_name(self) -> str: ...

    @property
    def feature_set_id(self) -> str: ...

    @property
    def observation_dataset_id(self) -> str: ...

    @property
    def panel_dataset_id(self) -> str: ...

    @property
    def target_dataset_id(self) -> str: ...

    @property
    def fold_dataset_id(self) -> str: ...

    @property
    def experiment_configuration_id(self) -> str: ...

    @property
    def evidence_class(self) -> EvidenceClass: ...

    @property
    def market_data_source_class(self) -> MarketDataSourceClass: ...

    @property
    def holdout_excluded(self) -> bool: ...


def verify_raw_feature_manifest_bindings(
    manifest: R2FeatureManifestBindings,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str,
) -> None:
    """Authenticate persisted feature metadata before reading feature rows."""
    _verify_foundation_bindings(foundation, experiment)
    schema = feature_schema_for_set(experiment, feature_set_name)
    expected_set_id = feature_set_id(experiment.configuration_id, feature_set_name, schema)
    expected_ids = (
        ("observation", manifest.observation_dataset_id, foundation.observations.dataset_id),
        ("panel", manifest.panel_dataset_id, foundation.panel.dataset_id),
        ("target", manifest.target_dataset_id, foundation.targets.dataset_id),
        ("fold", manifest.fold_dataset_id, foundation.folds.dataset_id),
    )
    for name, actual, expected in expected_ids:
        if actual != expected:
            raise ValueError(f"feature manifest {name} identity differs from verified foundation")
    if manifest.experiment_configuration_id != experiment.configuration_id:
        raise ValueError("feature manifest configuration identity differs from experiment")
    if manifest.feature_set_name != feature_set_name or manifest.feature_set_id != expected_set_id:
        raise ValueError("feature manifest feature-set identity differs from experiment")
    if manifest.feature_schema != schema:
        raise ValueError("feature manifest schema differs from declared feature set")
    if manifest.evidence_class != experiment.evidence_class:
        raise ValueError("feature manifest evidence class differs from experiment")
    if manifest.market_data_source_class != experiment.market_data_source_class:
        raise ValueError("feature manifest source class differs from experiment")
    if not manifest.holdout_excluded:
        raise ValueError("feature manifest does not exclude the locked holdout")


def verify_raw_feature_dataset(
    dataset: R2FeatureDataset,
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str,
) -> None:
    """Reconstruct and compare a small in-memory feature child."""
    verify_raw_feature_manifest_bindings(
        dataset,
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    schema = feature_schema_for_set(experiment, feature_set_name)
    expected_rows = build_raw_feature_rows(
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    if dataset.rows != expected_rows:
        raise ValueError("feature dataset rows differ from deterministic causal replay")
    expected = R2FeatureDataset.create(
        expected_rows,
        feature_schema=schema,
        feature_set_name=feature_set_name,
        observation_dataset_id=foundation.observations.dataset_id,
        panel_dataset_id=foundation.panel.dataset_id,
        target_dataset_id=foundation.targets.dataset_id,
        fold_dataset_id=foundation.folds.dataset_id,
        experiment_configuration_id=experiment.configuration_id,
        evidence_class=experiment.evidence_class,
        market_data_source_class=experiment.market_data_source_class,
    )
    if dataset != expected:
        raise ValueError("feature dataset semantic identity differs from deterministic replay")


def verify_raw_feature_rows(
    rows: Iterator[RawFeatureRow],
    foundation: R2FoundationInputs,
    experiment: R2ExperimentConfig,
    *,
    feature_set_name: str,
) -> int:
    """Replay persisted rows in lockstep without materialising the expected dataset."""
    _verify_foundation_bindings(foundation, experiment)
    schema = feature_schema_for_set(experiment, feature_set_name)
    expected_set_id = feature_set_id(experiment.configuration_id, feature_set_name, schema)
    expected = iter_raw_feature_rows(
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    count = 0
    actual_iterator = iter(rows)
    while True:
        try:
            actual = next(actual_iterator)
        except StopIteration:
            try:
                next(expected)
            except StopIteration:
                return count
            raise ValueError(
                "persisted feature rows are missing from deterministic causal replay"
            ) from None
        if actual.feature_set_id != expected_set_id:
            raise ValueError("persisted feature row feature-set identity differs from experiment")
        if tuple(value.name for value in actual.values) != tuple(item.name for item in schema):
            raise ValueError("persisted feature row schema differs from declared feature set")
        try:
            wanted = next(expected)
        except StopIteration as error:
            raise ValueError("persisted feature rows contain an unexpected extra row") from error
        if actual != wanted:
            raise ValueError("persisted feature row differs from deterministic causal replay")
        count += 1


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
