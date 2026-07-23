"""Pure R1.B builders for the causal panel and frozen midpoint targets."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from math import isfinite, log

from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    FoundationConfig,
    HorizonCoverageSummary,
    InstrumentRole,
    PanelAuditDisposition,
    PanelDataset,
    PanelRow,
    PanelStatus,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.market_data import DataGap, PriceBasis
from qtrad.domain.research import ObservationDataset, ObservationRow


def build_asof_panel(
    dataset: ObservationDataset,
    config: FoundationConfig,
    *,
    gaps: Sequence[DataGap] = (),
    source_active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]] | None = None,
) -> PanelDataset:
    """Build every configured MID cell using only revisions visible at its cutoff."""

    _require_dataset(dataset, config)
    source_active_intervals = source_active_intervals or {}
    index = _observation_index(dataset.rows)
    rows: list[PanelRow] = []
    for decision_time in _grid_times(config.range_start, config.range_end, config.grid_resolution):
        feature_data_asof = decision_time - config.selected_feature_lag
        for instrument_id in config.ordered_instruments:
            for basis in config.required_feature_bases:
                interval_start = feature_data_asof - config.grid_resolution
                candidates = index.get((instrument_id, basis, feature_data_asof), ())
                eligible = [
                    row
                    for row in candidates
                    if _availability_time(row, config.availability_basis) <= feature_data_asof
                ]
                source_keys = {_source_key(row) for row in eligible}
                if len(source_keys) > 1:
                    rows.append(
                        _missing_panel_row(
                            decision_time=decision_time,
                            instrument_id=instrument_id,
                            basis=basis,
                            feature_data_asof=feature_data_asof,
                            audit_disposition=PanelAuditDisposition.AMBIGUOUS_OR_INVALID_SOURCE,
                        )
                    )
                    continue
                selected = max(eligible, key=_revision_key, default=None)
                if selected is not None:
                    rows.append(
                        PanelRow(
                            decision_time=decision_time,
                            instrument_id=instrument_id,
                            basis=basis,
                            feature_data_asof=feature_data_asof,
                            latest_feature_bar_end=feature_data_asof,
                            status=PanelStatus.OBSERVED,
                            audit_disposition=None,
                            selected_event_id=selected.event_id,
                            selected_stream_version=selected.stream_version,
                            selected_global_position=selected.global_position,
                            selected_availability_time=_availability_time(
                                selected, config.availability_basis
                            ),
                            selected_revision=selected.revision,
                            interval_start=selected.interval_start,
                            interval_end=selected.interval_end,
                            open=selected.open,
                            high=selected.high,
                            low=selected.low,
                            close=selected.close,
                            sample_count=selected.sample_count,
                            quality=selected.quality,
                        )
                    )
                else:
                    rows.append(
                        _missing_panel_row(
                            decision_time=decision_time,
                            instrument_id=instrument_id,
                            basis=basis,
                            feature_data_asof=feature_data_asof,
                            audit_disposition=_panel_audit_disposition(
                                candidates=candidates,
                                instrument_id=instrument_id,
                                interval_start=interval_start,
                                interval_end=feature_data_asof,
                                feature_data_asof=feature_data_asof,
                                gaps=gaps,
                                source_active_intervals=source_active_intervals,
                            ),
                        )
                    )
    return PanelDataset.create(
        rows,
        observation_dataset_id=dataset.dataset_id,
        foundation_configuration_id=config.configuration_id,
    )


def build_frozen_targets(
    dataset: ObservationDataset,
    config: FoundationConfig,
    *,
    horizons: Sequence[timedelta] | None = None,
) -> TargetDataset:
    """Build frozen endpoint returns directly from observations, never from the panel."""

    _require_dataset(dataset, config)
    selected_horizons = tuple(
        (config.primary_vertical_horizon,) if horizons is None else horizons
    )
    if not selected_horizons or any(
        horizon not in config.target_horizons for horizon in selected_horizons
    ):
        raise ValueError(
            "target builder received a horizon absent from the foundation configuration"
        )
    if len(set(selected_horizons)) != len(selected_horizons):
        raise ValueError("target builder horizons must be unique")
    index = _observation_index(dataset.rows)
    rows: list[TargetRow] = []
    target_instruments = tuple(
        instrument_id
        for instrument_id in config.ordered_instruments
        if InstrumentRole(config.instrument_roles[instrument_id]) is InstrumentRole.TARGET
    )
    for decision_time in _grid_times(config.range_start, config.range_end, config.grid_resolution):
        for instrument_id in target_instruments:
            for horizon in selected_horizons:
                target_end = decision_time + horizon
                freeze_at = target_end + config.target_revision_delay
                start_row, start_state = _select_target_row(
                    index.get((instrument_id, config.target_basis, decision_time), ()),
                    freeze_at,
                    config.availability_basis,
                )
                end_row, end_state = _select_target_row(
                    index.get((instrument_id, config.target_basis, target_end), ()),
                    freeze_at,
                    config.availability_basis,
                )
                return_disposition = _return_disposition(start_row, start_state, end_row, end_state)
                label_start = start_row.close if start_row else None
                label_end = end_row.close if end_row else None
                log_return = (
                    _log_return(label_start, label_end)
                    if return_disposition is ReturnDisposition.VALID
                    else None
                )
                upper, lower, excursion_disposition = _excursions(
                    index=index,
                    instrument_id=instrument_id,
                    basis=config.target_basis,
                    start_time=decision_time,
                    end_time=target_end,
                    freeze_at=freeze_at,
                    availability_basis=config.availability_basis,
                    start_row=start_row,
                    end_row=end_row,
                    return_disposition=return_disposition,
                    grid_resolution=config.grid_resolution,
                )
                rows.append(
                    TargetRow(
                        instrument_id=instrument_id,
                        decision_time=decision_time,
                        horizon=horizon,
                        target_basis=config.target_basis,
                        target_revision_policy=config.target_revision_policy,
                        target_start_time=decision_time,
                        target_end_time=target_end,
                        target_freeze_at=freeze_at,
                        target_available_at=freeze_at,
                        label_start_close=label_start,
                        label_end_close=label_end,
                        log_return=log_return,
                        return_disposition=return_disposition,
                        start_event_id=start_row.event_id if start_row else None,
                        end_event_id=end_row.event_id if end_row else None,
                        upper_log_excursion=upper,
                        lower_log_excursion=lower,
                        excursion_disposition=excursion_disposition,
                    )
                )
    return TargetDataset.create(
        rows,
        observation_dataset_id=dataset.dataset_id,
        foundation_configuration_id=config.configuration_id,
    )


def summarise_horizon_coverage(
    dataset: TargetDataset,
    config: FoundationConfig,
    *,
    horizons: Sequence[timedelta] | None = None,
) -> tuple[HorizonCoverageSummary, ...]:
    """Summarise endpoint and path coverage without conflating dispositions."""

    if dataset.observation_dataset_id != config.observation_dataset_id:
        raise ValueError("target dataset observation lineage does not match configuration")
    if dataset.foundation_configuration_id != config.configuration_id:
        raise ValueError("target dataset configuration lineage does not match configuration")
    selected_horizons = tuple(config.target_horizons if horizons is None else horizons)
    if not selected_horizons or any(
        horizon not in config.target_horizons for horizon in selected_horizons
    ):
        raise ValueError("coverage summary received an unconfigured horizon")
    if len(set(selected_horizons)) != len(selected_horizons):
        raise ValueError("coverage summary horizons must be unique")

    summaries: list[HorizonCoverageSummary] = []
    for horizon in selected_horizons:
        rows = tuple(row for row in dataset.rows if row.horizon == horizon)
        return_counts = Counter(row.return_disposition.value for row in rows)
        excursion_counts = Counter(row.excursion_disposition.value for row in rows)
        total = len(rows)
        valid_returns = return_counts[ReturnDisposition.VALID.value]
        valid_excursions = excursion_counts[ExcursionDisposition.VALID.value]
        summaries.append(
            HorizonCoverageSummary(
                horizon=horizon,
                total_target_count=total,
                valid_return_count=valid_returns,
                valid_excursion_count=valid_excursions,
                unavailable_by_freeze_count=return_counts[
                    ReturnDisposition.UNAVAILABLE_BY_FREEZE.value
                ],
                return_coverage=valid_returns / total if total else 0.0,
                excursion_coverage=valid_excursions / total if total else 0.0,
                return_disposition_counts=tuple(sorted(return_counts.items())),
                excursion_disposition_counts=tuple(sorted(excursion_counts.items())),
            )
        )
    return tuple(summaries)


def _select_target_row(
    candidates: Sequence[ObservationRow],
    freeze_at: datetime,
    availability_basis: AvailabilityBasis,
) -> tuple[ObservationRow | None, str]:
    eligible = [
        row for row in candidates if _availability_time(row, availability_basis) <= freeze_at
    ]
    if len({_source_key(row) for row in eligible}) > 1:
        return None, "AMBIGUOUS"
    if eligible:
        return max(eligible, key=_revision_key), "OBSERVED"
    if candidates:
        return None, "UNAVAILABLE"
    return None, "MISSING"


def _return_disposition(
    start_row: ObservationRow | None,
    start_state: str,
    end_row: ObservationRow | None,
    end_state: str,
) -> ReturnDisposition:
    if start_state == "AMBIGUOUS" or end_state == "AMBIGUOUS":
        return ReturnDisposition.AMBIGUOUS_SOURCE
    if start_row is None:
        return (
            ReturnDisposition.UNAVAILABLE_BY_FREEZE
            if start_state == "UNAVAILABLE"
            else ReturnDisposition.MISSING_START
        )
    if end_row is None:
        return (
            ReturnDisposition.UNAVAILABLE_BY_FREEZE
            if end_state == "UNAVAILABLE"
            else ReturnDisposition.MISSING_END
        )
    if start_row.close <= 0 or not start_row.close.is_finite():
        return ReturnDisposition.NON_POSITIVE_START
    if end_row.close <= 0 or not end_row.close.is_finite():
        return ReturnDisposition.NON_POSITIVE_END
    return ReturnDisposition.VALID


def _excursions(
    *,
    index: Mapping[tuple[str, PriceBasis, datetime], Sequence[ObservationRow]],
    instrument_id: str,
    basis: PriceBasis,
    start_time: datetime,
    end_time: datetime,
    freeze_at: datetime,
    availability_basis: AvailabilityBasis,
    start_row: ObservationRow | None,
    end_row: ObservationRow | None,
    return_disposition: ReturnDisposition,
    grid_resolution: timedelta,
) -> tuple[float | None, float | None, ExcursionDisposition]:
    if return_disposition in {
        ReturnDisposition.MISSING_START,
        ReturnDisposition.UNAVAILABLE_BY_FREEZE,
    }:
        return None, None, ExcursionDisposition.MISSING_START
    if return_disposition in {ReturnDisposition.MISSING_END}:
        return None, None, ExcursionDisposition.MISSING_END
    if return_disposition is ReturnDisposition.AMBIGUOUS_SOURCE:
        return None, None, ExcursionDisposition.AMBIGUOUS_SOURCE
    if (
        start_row is None
        or end_row is None
        or start_row.close <= 0
        or not start_row.close.is_finite()
    ):
        return None, None, ExcursionDisposition.INCOMPLETE_PATH

    path: list[ObservationRow] = []
    current = start_time + grid_resolution
    while current <= end_time:
        selected, state = _select_target_row(
            index.get((instrument_id, basis, current), ()), freeze_at, availability_basis
        )
        if state == "AMBIGUOUS":
            return None, None, ExcursionDisposition.AMBIGUOUS_SOURCE
        if selected is None:
            return None, None, ExcursionDisposition.INCOMPLETE_PATH
        path.append(selected)
        current += grid_resolution

    try:
        upper = max(_log_ratio(row.high, start_row.close) for row in path)
        lower = min(_log_ratio(row.low, start_row.close) for row in path)
    except (ValueError, OverflowError):
        return None, None, ExcursionDisposition.INCOMPLETE_PATH
    return upper, lower, ExcursionDisposition.VALID


def _log_return(start: Decimal | None, end: Decimal | None) -> float:
    if start is None or end is None:
        raise ValueError("log return requires both endpoint prices")
    return _log_ratio(end, start)


def _log_ratio(numerator: Decimal, denominator: Decimal) -> float:
    if (
        numerator <= 0
        or denominator <= 0
        or not numerator.is_finite()
        or not denominator.is_finite()
    ):
        raise ValueError("log return prices must be finite and positive")
    value = log(float(numerator / denominator))
    if not isfinite(value):
        raise ValueError("log return must be finite")
    return value


def _missing_panel_row(
    *,
    decision_time: datetime,
    instrument_id: str,
    basis: PriceBasis,
    feature_data_asof: datetime,
    audit_disposition: PanelAuditDisposition,
) -> PanelRow:
    return PanelRow(
        decision_time=decision_time,
        instrument_id=instrument_id,
        basis=basis,
        feature_data_asof=feature_data_asof,
        latest_feature_bar_end=feature_data_asof,
        status=PanelStatus.MISSING_AS_OF_CUTOFF,
        audit_disposition=audit_disposition,
        selected_event_id=None,
        selected_stream_version=None,
        selected_global_position=None,
        selected_availability_time=None,
        selected_revision=None,
        interval_start=None,
        interval_end=None,
        open=None,
        high=None,
        low=None,
        close=None,
        sample_count=None,
        quality=None,
    )


def _panel_audit_disposition(
    *,
    candidates: Sequence[ObservationRow],
    instrument_id: str,
    interval_start: datetime,
    interval_end: datetime,
    feature_data_asof: datetime,
    gaps: Sequence[DataGap],
    source_active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]],
) -> PanelAuditDisposition:
    if len({_source_key(row) for row in candidates}) > 1:
        return PanelAuditDisposition.AMBIGUOUS_OR_INVALID_SOURCE
    if candidates:
        return PanelAuditDisposition.EVENTUALLY_OBSERVED_LATE
    matching_gaps = [
        gap
        for gap in gaps
        if gap.instrument_id.value == instrument_id
        and gap.interval_start < interval_end
        and gap.interval_end > interval_start
    ]
    if any(gap.detected_at <= feature_data_asof for gap in matching_gaps):
        return PanelAuditDisposition.RECORDED_GAP_KNOWN_BY_CUTOFF
    if matching_gaps:
        return PanelAuditDisposition.RECORDED_GAP_DETECTED_LATER
    if instrument_id in source_active_intervals and not any(
        active_start <= interval_start and active_end >= interval_end
        for active_start, active_end in source_active_intervals[instrument_id]
    ):
        return PanelAuditDisposition.SOURCE_NOT_ACTIVE
    return PanelAuditDisposition.NO_NATIVE_EVIDENCE


def _observation_index(
    rows: Sequence[ObservationRow],
) -> dict[tuple[str, PriceBasis, datetime], tuple[ObservationRow, ...]]:
    grouped: defaultdict[tuple[str, PriceBasis, datetime], list[ObservationRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.instrument_id, row.basis, row.interval_end)].append(row)
    return {key: tuple(value) for key, value in grouped.items()}


def _availability_time(row: ObservationRow, basis: AvailabilityBasis) -> datetime:
    return (
        row.persisted_at
        if AvailabilityBasis(basis) is AvailabilityBasis.PERSISTED_AT
        else row.received_at
    )


def _revision_key(row: ObservationRow) -> tuple[int, int]:
    return row.stream_version, row.global_position


def _source_key(row: ObservationRow) -> tuple[str, str, str]:
    return row.source_provider, row.source_environment, row.source_external_id


def _grid_times(start: datetime, end: datetime, resolution: timedelta) -> tuple[datetime, ...]:
    times: list[datetime] = []
    current = start
    while current < end:
        times.append(current)
        current += resolution
    return tuple(times)


def _require_dataset(dataset: ObservationDataset, config: FoundationConfig) -> None:
    if dataset.dataset_id != config.observation_dataset_id:
        raise ValueError("observation dataset does not match foundation configuration")
