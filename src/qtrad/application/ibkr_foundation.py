"""Build and verify the source-specific IBKR historical foundation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid5

from qtrad.application.foundation import build_asof_panel, build_frozen_targets
from qtrad.application.walk_forward import build_expanding_folds
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import (
    FoundationConfig,
    InstrumentRole,
    PanelDataset,
    TargetDataset,
)
from qtrad.domain.ibkr_foundation import (
    IBKR_CONFIRMATORY_INSTRUMENTS,
    IBKR_MINIMUM_COMMON_SUPPORT_ROWS,
    IBKR_MINIMUM_DURATION_SECONDS,
    IBKR_MINIMUM_ROWS_PER_CANDIDATE,
    IBKRFoundationReadiness,
    IBKRFoundationReadinessCause,
    IBKRFoundationReadinessState,
)
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.provider_history import (
    ProviderHistoricalDataset,
    ProviderHistoricalObservation,
)
from qtrad.domain.research import ObservationDataset, ObservationRow

_PROVIDER_EVENT_NAMESPACE = UUID("e0f2e1a2-5c22-4e86-a6a8-f2c7a8c9a9e9")


@dataclass(frozen=True, slots=True)
class IBKRFoundationBuild:
    """All source-specific children required before downstream R2 work."""

    configuration: FoundationConfig
    observations: ObservationDataset
    panel: PanelDataset
    targets: TargetDataset
    folds: FoldDataset
    provider_history: ProviderHistoricalDataset
    active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]]
    provider_gaps: tuple[Mapping[str, JsonValue], ...]
    readiness: IBKRFoundationReadiness


def build_ibkr_foundation(
    provider_dataset: ProviderHistoricalDataset,
    provider_rows: Sequence[ProviderHistoricalObservation],
    configuration: FoundationConfig,
) -> IBKRFoundationBuild:
    """Adapt verified provider history and replay foundation children."""

    candidate_names = {str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS}
    observed_instruments = tuple(sorted({row.instrument_id for row in provider_rows}))
    ordered_instruments = tuple(
        sorted(set(configuration.ordered_instruments) | set(observed_instruments) | candidate_names)
    )
    roles = {
        instrument_id: (
            InstrumentRole.TARGET
            if instrument_id in candidate_names
            else configuration.instrument_roles.get(instrument_id, InstrumentRole.CONTEXT)
        )
        for instrument_id in ordered_instruments
    }
    adapted_configuration = replace(
        configuration,
        observation_dataset_id="0" * 64,
        ordered_instruments=ordered_instruments,
        instrument_roles=roles,
    )

    source_start = (
        min(row.interval_start for row in provider_rows)
        if provider_rows
        else configuration.range_start
    )
    source_end = (
        max(row.interval_end for row in provider_rows) if provider_rows else configuration.range_end
    )
    observation_configuration: dict[str, JsonValue] = {
        "contract": "qtrad-ibkr-historical-observation-adapter-v1",
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "provider": "ibkr",
        "environment": "paper",
        "ordered_instruments": list(ordered_instruments),
        "interval_start": adapted_configuration.required_observation_start.isoformat(),
        "interval_end": adapted_configuration.required_observation_end.isoformat(),
        "observed_interval_start": (source_start.isoformat() if provider_rows else None),
        "observed_interval_end": source_end.isoformat() if provider_rows else None,
        "grid_resolution_seconds": int(adapted_configuration.grid_resolution.total_seconds()),
        "availability_basis": adapted_configuration.availability_basis.value,
        "source_dataset_id": provider_dataset.dataset_sha256,
    }
    rows = tuple(
        _adapt_observation(row, provider_dataset.dataset_sha256, index)
        for index, row in enumerate(
            sorted(
                provider_rows,
                key=lambda item: (
                    item.instrument_id,
                    item.interval_start,
                    item.interval_end,
                ),
            ),
            start=1,
        )
    )
    observations = ObservationDataset.create(
        rows,
        configuration=observation_configuration,
        source_dataset_ids=(provider_dataset.dataset_sha256,),
        selection_policies={
            "source_class": "IBKR_HISTORICAL_RESEARCH",
            "availability_policy": provider_dataset.availability_policy.as_json_value(),
            "correction_policy": ("FROZEN_FIRST_SUCCESSFUL_RESPONSE_NO_REFETCH_MERGE"),
        },
    )
    adapted_configuration = replace(
        adapted_configuration,
        observation_dataset_id=observations.dataset_id,
    )
    active_intervals, provider_gaps = _provider_evidence(provider_rows)
    panel = build_asof_panel(
        observations,
        adapted_configuration,
        source_active_intervals=active_intervals,
    )
    targets = build_frozen_targets(
        observations,
        adapted_configuration,
        horizons=adapted_configuration.target_horizons,
    )
    try:
        folds = build_expanding_folds(targets, adapted_configuration)
    except ValueError as exc:
        if str(exc) != "no scientifically valid expanding folds are available":
            raise
        folds = FoldDataset.create(
            (),
            target_dataset_id=targets.dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
        )
    readiness = evaluate_ibkr_foundation_readiness(
        provider_rows,
        targets,
        source_start=source_start,
        source_end=source_end,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        fold_count=len(folds.folds),
    )
    return IBKRFoundationBuild(
        configuration=adapted_configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        provider_history=provider_dataset,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        readiness=readiness,
    )


def evaluate_ibkr_foundation_readiness(
    provider_rows: Sequence[ProviderHistoricalObservation],
    targets: TargetDataset,
    *,
    source_start: datetime,
    source_end: datetime,
    active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]] | None = None,
    provider_gaps: Sequence[Mapping[str, JsonValue]] = (),
    fold_count: int = 0,
) -> IBKRFoundationReadiness:
    """Replay fixed history gates without registering or reading an R2 experiment."""

    candidate_names = {str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS}
    rows_by_candidate = {
        candidate: sum(1 for row in provider_rows if row.instrument_id == candidate)
        for candidate in sorted(candidate_names)
    }
    valid_target_times = {
        candidate: {
            row.decision_time
            for row in targets.rows
            if row.instrument_id == candidate and row.return_disposition.value == "VALID"
        }
        for candidate in sorted(candidate_names)
    }
    common_times: set[datetime] = set()
    if valid_target_times:
        first_candidate = next(iter(valid_target_times))
        common_times = set(valid_target_times[first_candidate])
        for candidate_times in valid_target_times.values():
            common_times.intersection_update(candidate_times)
    causes: set[IBKRFoundationReadinessCause] = set()
    active_intervals = active_intervals or {}
    provider_instruments = {row.instrument_id for row in provider_rows}
    if not provider_instruments:
        causes.add(IBKRFoundationReadinessCause.ENTITLEMENT_UNAVAILABLE)
    if any(candidate not in provider_instruments for candidate in candidate_names):
        causes.add(IBKRFoundationReadinessCause.ENTITLEMENT_UNAVAILABLE)

    contract_ids_by_instrument: dict[str, set[str]] = {}
    for row in provider_rows:
        contract_id = row.schedule_evidence.get("contract_id")
        if isinstance(contract_id, str):
            contract_ids_by_instrument.setdefault(row.instrument_id, set()).add(contract_id)
    if any(len(contract_ids) > 1 for contract_ids in contract_ids_by_instrument.values()):
        causes.add(IBKRFoundationReadinessCause.CONTRACT_IDENTITY_CHANGED)

    if any(
        not isinstance(row.schedule_evidence.get("sessions"), list)
        or not row.schedule_evidence["sessions"]
        for row in provider_rows
    ) or any(candidate not in active_intervals for candidate in candidate_names):
        causes.add(IBKRFoundationReadinessCause.SESSION_EVIDENCE_UNAVAILABLE)
    if any(not times for times in valid_target_times.values()):
        causes.add(IBKRFoundationReadinessCause.MISSING_CONFIRMATORY_TARGET)
    if len(common_times) < IBKR_MINIMUM_COMMON_SUPPORT_ROWS:
        causes.add(IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT)
    support_start = min(common_times) if common_times else source_start
    support_end = max(common_times) if common_times else support_start
    if (support_end - support_start).total_seconds() < IBKR_MINIMUM_DURATION_SECONDS:
        causes.add(IBKRFoundationReadinessCause.INSUFFICIENT_DURATION)
    if any(
        rows_by_candidate[candidate] < IBKR_MINIMUM_ROWS_PER_CANDIDATE
        for candidate in sorted(candidate_names)
    ):
        causes.add(IBKRFoundationReadinessCause.INSUFFICIENT_ROWS)
    if provider_gaps:
        causes.add(IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT)

    ordered_causes = tuple(cause for cause in IBKRFoundationReadinessCause if cause in causes)
    return IBKRFoundationReadiness(
        state=(
            IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY
            if not ordered_causes
            else IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
        ),
        causes=ordered_causes,
        common_support_start=min(common_times) if common_times else None,
        common_support_end=max(common_times) if common_times else None,
        common_support_rows=len(common_times),
        rows_by_candidate=rows_by_candidate,
        evidence={
            "provider_row_count": len(provider_rows),
            "provider_gap_count": len(provider_gaps),
            "target_row_count": len(targets.rows),
            "fold_count": fold_count,
        },
    )


def _adapt_observation(
    row: ProviderHistoricalObservation,
    source_dataset_id: str,
    position: int,
) -> ObservationRow:
    event_id = uuid5(
        _PROVIDER_EVENT_NAMESPACE,
        f"{source_dataset_id}:{row.observation_sha256}",
    )
    return ObservationRow(
        event_id=event_id,
        stream_id=f"market-bar:{row.instrument_id}:{PriceBasis.MID}",
        stream_version=position,
        event_type="MarketBarClosed",
        event_time=row.interval_end,
        received_at=row.available_at,
        persisted_at=row.available_at,
        global_position=position,
        instrument_id=row.instrument_id,
        basis=PriceBasis.MID,
        interval_start=row.interval_start,
        interval_end=row.interval_end,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        sample_count=max(row.count or 1, 1),
        revision=1,
        provenance=BarProvenance.IBKR_HISTORICAL,
        quality=DataQuality.HEALTHY,
        source_provider=row.provider,
        source_environment=row.environment,
        source_external_id=row.observation_sha256,
    )


def _provider_evidence(
    rows: Sequence[ProviderHistoricalObservation],
) -> tuple[
    dict[str, tuple[tuple[datetime, datetime], ...]],
    tuple[Mapping[str, JsonValue], ...],
]:
    intervals: dict[str, set[tuple[datetime, datetime]]] = {}
    gaps: list[Mapping[str, JsonValue]] = []
    for row in rows:
        evidence = row.schedule_evidence
        raw_sessions = evidence.get("sessions")
        if isinstance(raw_sessions, list):
            for session in raw_sessions:
                if not isinstance(session, Mapping):
                    continue
                session_mapping = cast(Mapping[str, JsonValue], session)
                start = _session_time(session_mapping, "start")
                end = _session_time(session_mapping, "end")
                if start is not None and end is not None and end > start:
                    intervals.setdefault(row.instrument_id, set()).add((start, end))
        disposition = row.gap_disposition
        if disposition not in {"NO_GAP", "ACCEPTED", "BAR_ACCEPTED"}:
            gaps.append(
                {
                    "instrument_id": row.instrument_id,
                    "interval_start": row.interval_start.isoformat(),
                    "interval_end": row.interval_end.isoformat(),
                    "disposition": disposition,
                }
            )
    return (
        {instrument: tuple(sorted(values)) for instrument, values in sorted(intervals.items())},
        tuple(gaps),
    )


def _session_time(session: Mapping[str, object], name: str) -> datetime | None:
    for key in (name, f"{name}_time", f"session_{name}"):
        value = session.get(key)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
    return None
