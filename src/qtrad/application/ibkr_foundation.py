"""Build and verify the source-specific IBKR historical foundation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Literal, cast, overload
from uuid import UUID, uuid5

from qtrad.application.foundation import build_asof_panel, build_frozen_targets
from qtrad.application.provider_history import (
    ProviderHistoryRequestEvidence,
    ProviderHistorySourceEvidence,
    request_evidence_by_hash,
)
from qtrad.application.r2_readiness import R2_MINIMUM_COVERAGE, r2_research_blocks
from qtrad.application.walk_forward import build_expanding_folds
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, FoldDataset
from qtrad.domain.foundation import (
    FoundationConfig,
    InstrumentRole,
    PanelDataset,
    ReturnDisposition,
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
from qtrad.domain.ibkr_historical import IbkrHistoricalRequest, IbkrHistoricalRequestKind
from qtrad.domain.ibkr_results import IbkrHistoricalEvidenceDisposition
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.provider_history import (
    ProviderHistoricalDataset,
    ProviderHistoricalObservation,
)
from qtrad.domain.r2_holdout import (
    R2HoldoutCausalMetadata,
    R2HoldoutTargetIndex,
    R2OutcomeBlindObservationView,
    R2OutcomeBlindPanelView,
    R2OutcomeBlindTargetView,
)
from qtrad.domain.research import ObservationDataset, ObservationRow

_PROVIDER_EVENT_NAMESPACE = UUID("e0f2e1a2-5c22-4e86-a6a8-f2c7a8c9a9e9")


@dataclass(frozen=True, slots=True)
class IBKRFoundationBuild:
    """All source-specific children required before downstream R2 work."""

    configuration: FoundationConfig
    observations: ObservationDataset | R2OutcomeBlindObservationView
    panel: PanelDataset | R2OutcomeBlindPanelView
    targets: TargetDataset | R2OutcomeBlindTargetView
    folds: FoldDataset
    target_index: R2HoldoutTargetIndex | None
    causal_metadata: R2HoldoutCausalMetadata | None
    provider_history: ProviderHistoricalDataset
    active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]]
    provider_gaps: tuple[Mapping[str, JsonValue], ...]
    readiness: IBKRFoundationReadiness


def build_ibkr_foundation(
    source_evidence: ProviderHistorySourceEvidence,
    configuration: FoundationConfig,
) -> IBKRFoundationBuild:
    """Adapt verified provider history and replay foundation children."""

    provider_dataset = source_evidence.dataset
    provider_rows = source_evidence.observations
    candidate_names = {str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS}
    observed_instruments = tuple(sorted({row.instrument_id for row in provider_rows}))
    ordered_instruments = tuple(
        sorted(set(configuration.ordered_instruments) | set(observed_instruments) | candidate_names)
    )
    roles = {
        instrument_id: (
            InstrumentRole.TARGET if instrument_id in candidate_names else InstrumentRole.CONTEXT
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
        "observed_interval_start": source_start.isoformat() if provider_rows else None,
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
            "correction_policy": "FROZEN_FIRST_SUCCESSFUL_RESPONSE_NO_REFETCH_MERGE",
        },
    )
    adapted_configuration = replace(
        adapted_configuration,
        observation_dataset_id=observations.dataset_id,
    )
    active_intervals, provider_gaps = _provider_evidence(source_evidence)
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
        source_evidence,
        targets,
        source_start=source_start,
        source_end=source_end,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        primary_horizon=adapted_configuration.primary_vertical_horizon,
        folds=folds.folds,
        holdout_range=adapted_configuration.holdout_range,
    )
    return IBKRFoundationBuild(
        configuration=adapted_configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        target_index=R2HoldoutTargetIndex.create(targets),
        causal_metadata=R2HoldoutCausalMetadata.create(panel),
        provider_history=provider_dataset,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        readiness=readiness,
    )


def evaluate_ibkr_foundation_readiness(
    source_evidence: ProviderHistorySourceEvidence,
    targets: TargetDataset,
    *,
    source_start: datetime,
    source_end: datetime,
    active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]] | None = None,
    provider_gaps: Sequence[Mapping[str, JsonValue]] = (),
    primary_horizon: timedelta,
    folds: Sequence[Fold],
    holdout_range: tuple[datetime, datetime],
) -> IBKRFoundationReadiness:
    """Replay fixed history gates from the verified Stage 6/7 evidence."""

    candidate_names = {str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS}
    source_artifact = source_evidence.source_artifact
    plan = source_artifact.plan
    aggregate = source_artifact.aggregate
    results_by_hash = request_evidence_by_hash(source_evidence)
    requests_by_instrument: dict[
        str,
        list[tuple[IbkrHistoricalRequest, ProviderHistoryRequestEvidence]],
    ] = {}
    for request in plan.requests:
        result = results_by_hash.get(request.request_sha256)
        if result is None:
            raise ValueError("IBKR source evidence is missing a planned request result")
        requests_by_instrument.setdefault(str(request.instrument_id), []).append((request, result))

    rows_by_candidate = {
        candidate: sum(
            partition.row_count
            for partition in source_evidence.dataset.partitions
            if partition.instrument_id == candidate
        )
        for candidate in sorted(candidate_names)
    }
    valid_target_times = {
        candidate: {
            row.decision_time
            for row in targets.rows
            if (
                row.instrument_id == candidate
                and row.horizon == primary_horizon
                and row.return_disposition.value == "VALID"
            )
        }
        for candidate in sorted(candidate_names)
    }
    common_times: set[datetime] = set()
    first_times = True
    for candidate in sorted(candidate_names):
        if first_times:
            common_times = set(valid_target_times[candidate])
            first_times = False
        else:
            common_times.intersection_update(valid_target_times[candidate])

    raw_eligible = aggregate.entitlement_summary["provider_history_eligible_instruments"]
    if not isinstance(raw_eligible, list) or any(
        not isinstance(item, str) or not item for item in raw_eligible
    ):
        raise ValueError("IBKR aggregate provider-history eligibility summary is invalid")
    eligible_instruments = frozenset(raw_eligible)
    if len(eligible_instruments) != len(raw_eligible):
        raise ValueError("IBKR aggregate provider-history eligibility summary is not unique")

    causes: set[IBKRFoundationReadinessCause] = set()
    active_intervals = active_intervals or {}

    def is_confirmatory_gap(gap: Mapping[str, JsonValue]) -> bool:
        instrument_id = gap.get("instrument_id")
        return isinstance(instrument_id, str) and instrument_id in candidate_names

    confirmatory_gaps = tuple(gap for gap in provider_gaps if is_confirmatory_gap(gap))
    coverage_cells, blocking_cells, coverage_diagnostics = _ibkr_opportunity_coverage(
        targets=targets,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        primary_horizon=primary_horizon,
        folds=folds,
        holdout_range=holdout_range,
    )
    request_evidence: dict[str, JsonValue] = {}
    for candidate in sorted(candidate_names):
        candidate_requests = requests_by_instrument.get(candidate, [])
        bar_results = [
            result
            for request, result in candidate_requests
            if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
        ]
        schedule_results = [
            result
            for request, result in candidate_requests
            if request.kind is IbkrHistoricalRequestKind.SCHEDULE
        ]
        dispositions = [result.evidence_disposition.value for result in bar_results]
        schedule_dispositions = [result.evidence_disposition.value for result in schedule_results]
        planned_contract = next(
            (
                contract
                for contract in plan.eligible_contracts
                if str(contract.instrument_id) == candidate
            ),
            None,
        )
        contract_ids: list[JsonValue] = (
            [] if planned_contract is None else [planned_contract.fingerprint.con_id]
        )
        request_evidence[candidate] = cast(
            dict[str, JsonValue],
            {
                "planned": bool(candidate_requests),
                "bar_dispositions": dispositions,
                "schedule_dispositions": schedule_dispositions,
                "eligible": candidate in eligible_instruments,
                "contract_ids": contract_ids,
                "bar_row_count": sum(result.accepted_row_count for result in bar_results),
                "schedule_session_count": sum(len(result.sessions) for result in schedule_results),
            },
        )

        if (
            planned_contract is None
            or not bar_results
            or not any(
                result.evidence_disposition is IbkrHistoricalEvidenceDisposition.SUCCEEDED
                and result.accepted_row_count > 0
                for result in bar_results
            )
        ):
            causes.add(IBKRFoundationReadinessCause.MISSING_CONFIRMATORY_TARGET)
        if any(
            result.evidence_disposition
            is IbkrHistoricalEvidenceDisposition.CONTRACT_IDENTITY_CHANGED
            for _, result in candidate_requests
        ):
            causes.add(IBKRFoundationReadinessCause.CONTRACT_IDENTITY_CHANGED)
        if any(
            result.evidence_disposition is IbkrHistoricalEvidenceDisposition.ENTITLEMENT_UNAVAILABLE
            for _, result in candidate_requests
        ):
            causes.add(IBKRFoundationReadinessCause.ENTITLEMENT_UNAVAILABLE)
        if (
            not schedule_results
            or candidate not in active_intervals
            or any(
                result.evidence_disposition is not IbkrHistoricalEvidenceDisposition.SUCCEEDED
                or not result.sessions
                for result in schedule_results
            )
        ):
            causes.add(IBKRFoundationReadinessCause.SESSION_EVIDENCE_UNAVAILABLE)

    if not common_times:
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
    if not folds:
        causes.add(IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT)
    if blocking_cells:
        causes.add(IBKRFoundationReadinessCause.INSUFFICIENT_BLOCK_COVERAGE)

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
            "provider_row_count": source_evidence.dataset.row_count,
            "provider_gap_count": len(confirmatory_gaps),
            "total_provider_gap_count": len(provider_gaps),
            "raw_provider_gaps": [dict(gap) for gap in sorted(provider_gaps, key=_gap_sort_key)],
            "coverage_cells": list(coverage_cells),
            "coverage_threshold": str(R2_MINIMUM_COVERAGE),
            "blocking_coverage_cells": list(blocking_cells),
            "coverage_diagnostics": coverage_diagnostics,
            "target_row_count": len(targets.rows),
            "fold_count": len(folds),
            "primary_horizon_seconds": primary_horizon.total_seconds(),
            "source_contract_selection_sha256": plan.contract_selection_sha256,
            "source_plan_sha256": plan.plan_sha256,
            "source_runtime_sha256": plan.runtime_sha256,
            "source_aggregate_sha256": aggregate.aggregate_sha256,
            "source_coverage_summary": aggregate.coverage_summary,
            "source_entitlement_summary": aggregate.entitlement_summary,
            "request_evidence": request_evidence,
        },
    )


def _ibkr_opportunity_coverage(
    *,
    targets: TargetDataset,
    active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]],
    provider_gaps: Sequence[Mapping[str, JsonValue]],
    primary_horizon: timedelta,
    folds: Sequence[Fold],
    holdout_range: tuple[datetime, datetime],
) -> tuple[tuple[dict[str, JsonValue], ...], tuple[str, ...], dict[str, JsonValue]]:
    """Classify persisted Stage 8 opportunities against the frozen R2 blocks."""

    candidates = tuple(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS)
    gaps_by_instrument: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    context_gap_count = 0
    for gap in sorted(provider_gaps, key=_gap_sort_key):
        instrument = gap.get("instrument_id")
        if not isinstance(instrument, str):
            raise TypeError("provider gap instrument_id must be a string")
        if instrument not in candidates:
            context_gap_count += 1
            continue
        start = _evidence_time(gap.get("interval_start"), "provider gap interval start")
        end = _evidence_time(gap.get("interval_end"), "provider gap interval end")
        if end <= start:
            raise ValueError("provider gap interval must have positive duration")
        gaps_by_instrument[instrument].append((start, end))

    blocks = r2_research_blocks(folds, holdout_range)
    rows_by_instrument = {
        instrument: tuple(
            sorted(
                (
                    row
                    for row in targets.rows
                    if row.instrument_id == instrument and row.horizon == primary_horizon
                ),
                key=lambda row: (row.decision_time, row.target_start_time, row.target_end_time),
            )
        )
        for instrument in candidates
    }
    cells: list[dict[str, JsonValue]] = []
    blocking: list[str] = []
    totals = {name: 0 for name in ("ELIGIBLE", "GAP", "INACTIVE", "OTHER_INELIGIBLE")}
    for instrument in candidates:
        intervals = tuple(sorted(active_intervals.get(instrument, ())))
        gaps = tuple(sorted(gaps_by_instrument[instrument]))
        for block, block_start, block_end in blocks:
            counts = dict.fromkeys(totals, 0)
            for row in rows_by_instrument[instrument]:
                if not block_start <= row.decision_time < block_end:
                    continue
                is_active = any(
                    start <= row.target_start_time and row.target_end_time <= end
                    for start, end in intervals
                )
                if not is_active:
                    disposition = "INACTIVE"
                elif row.return_disposition is ReturnDisposition.VALID:
                    disposition = "ELIGIBLE"
                else:
                    missing_endpoint = (
                        row.target_start_time
                        if row.return_disposition is ReturnDisposition.MISSING_START
                        else (
                            row.target_end_time
                            if row.return_disposition is ReturnDisposition.MISSING_END
                            else None
                        )
                    )
                    disposition = (
                        "GAP"
                        if missing_endpoint is not None
                        and any(start < missing_endpoint <= end for start, end in gaps)
                        else "OTHER_INELIGIBLE"
                    )
                counts[disposition] += 1
            denominator = counts["ELIGIBLE"] + counts["GAP"]
            coverage = Fraction(counts["ELIGIBLE"], denominator) if denominator else None
            passed = coverage is not None and coverage >= R2_MINIMUM_COVERAGE
            key = f"{instrument}/{block}"
            counts_json: dict[str, JsonValue] = {name: count for name, count in counts.items()}
            cell: dict[str, JsonValue] = {
                "key": key,
                "instrument_id": instrument,
                "block": block,
                "block_start": block_start.isoformat(),
                "block_end": block_end.isoformat(),
                "opportunity_counts": counts_json,
                "coverage": str(coverage) if coverage is not None else None,
                "threshold": str(R2_MINIMUM_COVERAGE),
                "passed": passed,
            }
            cells.append(cell)
            if not passed:
                blocking.append(key)
            for disposition, count in counts.items():
                totals[disposition] += count

    totals_json: dict[str, JsonValue] = {name: count for name, count in totals.items()}
    diagnostics: dict[str, JsonValue] = {
        "authoritative": False,
        "candidate_count": len(candidates),
        "cell_count": len(cells),
        "blocking_cell_count": len(blocking),
        "context_gap_count": context_gap_count,
        "opportunity_counts": totals_json,
    }
    return tuple(cells), tuple(blocking), diagnostics


def _gap_sort_key(gap: Mapping[str, JsonValue]) -> tuple[str, ...]:
    return tuple(
        str(gap.get(key) or "")
        for key in (
            "instrument_id",
            "interval_start",
            "interval_end",
            "disposition",
            "request_sha256",
            "result_sha256",
        )
    )


def _adapt_observation(
    row: ProviderHistoricalObservation,
    source_dataset_id: str,
    position: int,
) -> ObservationRow:
    provider_row = row
    event_id = uuid5(
        _PROVIDER_EVENT_NAMESPACE,
        f"{source_dataset_id}:{provider_row.observation_sha256}",
    )
    return ObservationRow(
        event_id=event_id,
        stream_id=f"market-bar:{provider_row.instrument_id}:{PriceBasis.MID}",
        stream_version=position,
        event_type="MarketBarClosed",
        event_time=provider_row.interval_end,
        received_at=provider_row.available_at,
        persisted_at=provider_row.available_at,
        global_position=position,
        instrument_id=provider_row.instrument_id,
        basis=PriceBasis.MID,
        interval_start=provider_row.interval_start,
        interval_end=provider_row.interval_end,
        open=provider_row.open,
        high=provider_row.high,
        low=provider_row.low,
        close=provider_row.close,
        sample_count=max(provider_row.count or 1, 1),
        revision=1,
        provenance=BarProvenance.IBKR_HISTORICAL,
        quality=DataQuality.HEALTHY,
        source_provider=provider_row.provider,
        source_environment=provider_row.environment,
        source_external_id=provider_row.observation_sha256,
    )


@overload
def _provider_evidence(
    source_evidence: ProviderHistorySourceEvidence,
    *,
    include_bounds: Literal[False] = False,
) -> tuple[
    dict[str, tuple[tuple[datetime, datetime], ...]],
    tuple[Mapping[str, JsonValue], ...],
]: ...


@overload
def _provider_evidence(
    source_evidence: ProviderHistorySourceEvidence,
    *,
    include_bounds: Literal[True],
) -> tuple[
    dict[str, tuple[tuple[datetime, datetime], ...]],
    tuple[Mapping[str, JsonValue], ...],
    datetime,
    datetime,
]: ...


def _provider_evidence(
    source_evidence: ProviderHistorySourceEvidence,
    *,
    include_bounds: bool = False,
) -> (
    tuple[
        dict[str, tuple[tuple[datetime, datetime], ...]],
        tuple[Mapping[str, JsonValue], ...],
    ]
    | tuple[
        dict[str, tuple[tuple[datetime, datetime], ...]],
        tuple[Mapping[str, JsonValue], ...],
        datetime,
        datetime,
    ]
):
    source = source_evidence.source_artifact
    requests_by_hash = {request.request_sha256: request for request in source.plan.requests}
    results_by_hash = request_evidence_by_hash(source_evidence)
    resolved_results: list[tuple[IbkrHistoricalRequest, ProviderHistoryRequestEvidence]] = []
    for request_hash, result in results_by_hash.items():
        request = requests_by_hash.get(request_hash)
        if request is None:
            raise ValueError("IBKR source result request is absent from the verified plan")
        resolved_results.append((request, result))

    observation_summary = source_evidence.observation_summary
    accepted_starts_by_request: dict[str, set[datetime]] = defaultdict(set)
    accepted_intervals_by_request: dict[str, tuple[tuple[datetime, datetime], ...]] = {}
    source_start: datetime | None = None
    source_end: datetime | None = None
    if observation_summary is not None:
        accepted_intervals_by_request = observation_summary.intervals_by_request()
        source_start = observation_summary.source_start
        source_end = observation_summary.source_end
    else:
        for row in source_evidence.observations:
            request_sha256 = getattr(row, "request_sha256", None)
            interval_start = getattr(row, "interval_start", None)
            interval_end = getattr(row, "interval_end", None)
            if isinstance(request_sha256, str) and isinstance(interval_start, datetime):
                accepted_starts_by_request[request_sha256].add(interval_start)
            if isinstance(interval_start, datetime):
                source_start = (
                    interval_start if source_start is None else min(source_start, interval_start)
                )
            if isinstance(interval_end, datetime):
                source_end = interval_end if source_end is None else max(source_end, interval_end)
        if not accepted_starts_by_request:
            for result in getattr(source_evidence.source_artifact, "request_results", ()):
                for raw in result.accepted_rows:
                    accepted_starts_by_request[result.request_sha256].add(
                        _evidence_time(
                            cast(Mapping[str, object], raw)["bar_start"],
                            "bar_start",
                        )
                    )

    intervals: dict[str, set[tuple[datetime, datetime]]] = {}
    for request, result in resolved_results:
        instrument_id = str(request.instrument_id)
        if request.kind is not IbkrHistoricalRequestKind.SCHEDULE:
            continue
        if result.evidence_disposition is IbkrHistoricalEvidenceDisposition.SUCCEEDED:
            for raw_session in result.sessions:
                session = cast(Mapping[str, object], raw_session)
                if session.get("active") is not True:
                    continue
                start = _session_time(session, "start")
                end = _session_time(session, "end")
                if start is not None and end is not None and end > start:
                    intervals.setdefault(instrument_id, set()).add((start, end))

    gaps: list[Mapping[str, JsonValue]] = []
    for request, result in resolved_results:
        instrument_id = str(request.instrument_id)
        if request.kind is not IbkrHistoricalRequestKind.MIDPOINT_BARS:
            continue
        expected_intervals = tuple(
            (
                max(active_start, request.interval_start),
                min(active_end, request.interval_end),
            )
            for active_start, active_end in sorted(intervals.get(instrument_id, ()))
            if max(active_start, request.interval_start) < min(active_end, request.interval_end)
        )
        if result.evidence_disposition is not IbkrHistoricalEvidenceDisposition.SUCCEEDED:
            for expected_start, expected_end in expected_intervals:
                gaps.append(
                    _gap(
                        instrument_id,
                        expected_start,
                        expected_end,
                        request.request_sha256,
                        result.result_sha256,
                        disposition=result.evidence_disposition.value,
                    )
                )
            continue
        if observation_summary is not None:
            accepted_intervals = accepted_intervals_by_request.get(request.request_sha256, ())
            for expected_start, expected_end in expected_intervals:
                for missing_start, missing_end in _missing_provider_intervals(
                    expected_start,
                    expected_end,
                    accepted_intervals,
                ):
                    gaps.append(
                        _gap(
                            instrument_id,
                            missing_start,
                            missing_end,
                            request.request_sha256,
                            result.result_sha256,
                        )
                    )
            continue
        accepted_starts = accepted_starts_by_request.get(request.request_sha256, set())
        for expected_start, expected_end in expected_intervals:
            missing_start: datetime | None = None
            cursor = expected_start
            while cursor < expected_end:
                if cursor not in accepted_starts:
                    if missing_start is None:
                        missing_start = cursor
                elif missing_start is not None:
                    gaps.append(
                        _gap(
                            instrument_id,
                            missing_start,
                            cursor,
                            request.request_sha256,
                            result.result_sha256,
                        )
                    )
                    missing_start = None
                cursor += timedelta(minutes=1)
            if missing_start is not None:
                gaps.append(
                    _gap(
                        instrument_id,
                        missing_start,
                        expected_end,
                        request.request_sha256,
                        result.result_sha256,
                    )
                )
    active_intervals = {
        instrument: tuple(sorted(values)) for instrument, values in sorted(intervals.items())
    }
    provider_gaps = tuple(gaps)
    if include_bounds:
        if source_start is None or source_end is None:
            raise ValueError("provider-history source has no observations")
        return active_intervals, provider_gaps, source_start, source_end
    return active_intervals, provider_gaps


def _missing_provider_intervals(
    expected_start: datetime,
    expected_end: datetime,
    accepted_intervals: Sequence[tuple[datetime, datetime]],
) -> tuple[tuple[datetime, datetime], ...]:
    missing: list[tuple[datetime, datetime]] = []
    cursor = expected_start
    for accepted_start, accepted_end in accepted_intervals:
        if accepted_end <= cursor:
            continue
        if accepted_start >= expected_end:
            break
        if accepted_start > cursor:
            missing.append((cursor, min(accepted_start, expected_end)))
        cursor = max(cursor, min(accepted_end, expected_end))
        if cursor >= expected_end:
            break
    if cursor < expected_end:
        missing.append((cursor, expected_end))
    return tuple(missing)


def _gap(
    instrument_id: str,
    start: datetime,
    end: datetime,
    request_sha256: str,
    result_sha256: str,
    *,
    disposition: str = "MISSING_BAR",
) -> Mapping[str, JsonValue]:
    return {
        "instrument_id": instrument_id,
        "interval_start": start.isoformat(),
        "interval_end": end.isoformat(),
        "disposition": disposition,
        "request_sha256": request_sha256,
        "result_sha256": result_sha256,
    }


def _session_time(session: Mapping[str, object], name: str) -> datetime | None:
    for key in (f"{name}DateTime", name, f"{name}_time", f"interval_{name}", f"session_{name}"):
        value = session.get(key)
        if isinstance(value, str):
            return _evidence_time(value, f"session {name}")
    return None


def _evidence_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"IBKR {field} must be a UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"IBKR {field} must be timezone-aware")
    return parsed.astimezone(UTC)
