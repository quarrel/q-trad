"""Build and replay provider-history observations from verified IBKR results."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

from qtrad.application.ibkr_results import replay_ibkr_historical_aggregate_result
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import (
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
)
from qtrad.domain.ibkr_results import (
    IbkrHistoricalAggregateResult,
    IbkrHistoricalEvidenceDisposition,
    IbkrHistoricalRequestResult,
    IbkrHistoricalResultArtifact,
)
from qtrad.domain.provider_history import (
    PROVIDER_HISTORY_BAR_BASIS,
    PROVIDER_HISTORY_CORRECTION_POLICY,
    PROVIDER_HISTORY_DECLARED_DELAY,
    PROVIDER_HISTORY_ENVIRONMENT,
    PROVIDER_HISTORY_POLICY,
    PROVIDER_HISTORY_PROVIDER,
    PROVIDER_HISTORY_SOURCE_CLASS,
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalDataset,
    ProviderHistoricalObservation,
    ProviderHistoricalPartition,
    ProviderHistoricalPartitionReference,
)
from qtrad.domain.time import require_utc


class ProviderHistoryResultSource(Protocol):
    plan: IbkrHistoricalPlan
    plan_bytes: bytes
    aggregate: IbkrHistoricalAggregateResult

    def iter_request_results(
        self,
        *,
        request_order: Sequence[IbkrHistoricalRequest] | None = None,
    ) -> Iterator[IbkrHistoricalRequestResult]: ...


ProviderHistorySource = IbkrHistoricalResultArtifact | ProviderHistoryResultSource


@dataclass(frozen=True, slots=True)
class ProviderHistoryRequestEvidence:
    """Compact request evidence retained by the foundation builder."""

    request_sha256: str
    result_sha256: str
    evidence_disposition: IbkrHistoricalEvidenceDisposition
    accepted_row_count: int
    sessions: tuple[dict[str, JsonValue], ...]

    @classmethod
    def from_result(cls, result: IbkrHistoricalRequestResult) -> ProviderHistoryRequestEvidence:
        return cls(
            request_sha256=result.request_sha256,
            result_sha256=result.result_sha256,
            evidence_disposition=result.evidence_disposition,
            accepted_row_count=len(result.accepted_rows),
            sessions=tuple(dict(session) for session in result.sessions),
        )


@dataclass(frozen=True, slots=True)
class ProviderHistorySourceEvidence:
    """Verified Stage 6 closure and Stage 7 rows used by foundation readiness."""

    dataset: ProviderHistoricalDataset
    observations: tuple[ProviderHistoricalObservation, ...]
    source_artifact: ProviderHistorySource
    request_evidence: tuple[ProviderHistoryRequestEvidence, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.dataset.contract_selection_sha256
            != self.source_artifact.plan.contract_selection_sha256
        ):
            raise ValueError("provider-history source contract selection differs from its dataset")
        if self.dataset.plan_sha256 != self.source_artifact.plan.plan_sha256:
            raise ValueError("provider-history source plan differs from its dataset")
        if self.dataset.runtime_sha256 != self.source_artifact.plan.runtime_sha256:
            raise ValueError("provider-history source runtime differs from its dataset")
        if self.dataset.aggregate_sha256 != self.source_artifact.aggregate.aggregate_sha256:
            raise ValueError("provider-history source aggregate differs from its dataset")
        if len(self.observations) != self.dataset.row_count:
            raise ValueError("provider-history source observation count differs from its dataset")


def request_evidence_by_hash(
    source_evidence: ProviderHistorySourceEvidence,
) -> dict[str, ProviderHistoryRequestEvidence]:
    """Return compact request evidence without forcing a streaming source to materialise."""

    explicit_evidence = getattr(source_evidence, "request_evidence", ())
    if explicit_evidence:
        evidence = explicit_evidence
    else:
        request_results = getattr(source_evidence.source_artifact, "request_results", ())
        evidence = tuple(
            ProviderHistoryRequestEvidence.from_result(result) for result in request_results
        )
    result = {item.request_sha256: item for item in evidence}
    if len(result) != len(evidence):
        raise ValueError("provider-history request evidence identities are not unique")
    return result


def build_provider_history_dataset(
    source: ProviderHistorySource,
    *,
    availability_delay: timedelta,
) -> ProviderHistoricalDataset:
    """Build bounded partition identities from a streaming Stage 6 result closure."""
    policy = ProviderHistoricalAvailabilityPolicy(
        selector=PROVIDER_HISTORY_DECLARED_DELAY,
        policy=PROVIDER_HISTORY_POLICY,
        delay=availability_delay,
    )
    partitions = tuple(
        ProviderHistoricalPartitionReference.from_partition(partition)
        for partition in iter_provider_history_partitions(source, policy=policy)
    )
    return ProviderHistoricalDataset.create(
        partitions=partitions,
        contract_selection_sha256=source.plan.contract_selection_sha256,
        plan_sha256=source.plan.plan_sha256,
        runtime_sha256=source.plan.runtime_sha256,
        aggregate_sha256=source.aggregate.aggregate_sha256,
        availability_policy=policy,
    )


def provider_history_partition_row_bounds(
    source: ProviderHistorySource,
) -> dict[tuple[str, date], int]:
    eligible_instruments = _provider_history_eligible_instruments(source)
    return _partition_row_bounds(source, eligible_instruments)


def _partition_row_bounds(
    source: ProviderHistorySource,
    eligible_instruments: frozenset[str],
) -> dict[tuple[str, date], int]:
    bounds: dict[tuple[str, date], int] = {}
    for request in source.plan.requests:
        if (
            request.kind is not IbkrHistoricalRequestKind.MIDPOINT_BARS
            or str(request.instrument_id) not in eligible_instruments
        ):
            continue
        day = request.interval_start.replace(hour=0, minute=0, second=0, microsecond=0)
        while day < request.interval_end:
            next_day = day + timedelta(days=1)
            overlap_start = max(request.interval_start, day)
            overlap_end = min(request.interval_end, next_day)
            minutes = int((overlap_end - overlap_start).total_seconds() // 60)
            if minutes:
                key = (str(request.instrument_id), day.date())
                bounds[key] = bounds.get(key, 0) + minutes
            day = next_day
    return bounds


@dataclass(slots=True)
class _ScheduleEvidenceState:
    request_hashes: list[str]
    result_hashes: list[str]
    states: set[str]
    sessions: list[dict[str, JsonValue]]
    failed: bool = False

    def __init__(self) -> None:
        self.request_hashes = []
        self.result_hashes = []
        self.states = set()
        self.sessions = []
        self.failed = False


def iter_provider_history_partitions(
    source: ProviderHistorySource,
    *,
    policy: ProviderHistoricalAvailabilityPolicy,
) -> Iterator[ProviderHistoricalPartition]:
    """Yield one bounded canonical instrument-day partition at a time."""
    eligible_instruments = _provider_history_eligible_instruments(source)
    bounds = _partition_row_bounds(source, eligible_instruments)
    request_order = tuple(
        sorted(
            source.plan.requests,
            key=lambda item: (
                str(item.instrument_id),
                0 if item.kind is IbkrHistoricalRequestKind.SCHEDULE else 1,
                item.interval_start,
                item.interval_end,
                item.request_sha256,
            ),
        )
    )
    request_by_hash = {item.request_sha256: item for item in source.plan.requests}
    if len(request_by_hash) != len(source.plan.requests):
        raise ValueError("provider-history plan request identities are not unique")

    current_key: tuple[str, date] | None = None
    current_rows: list[ProviderHistoricalObservation] = []
    schedule_states: dict[str, _ScheduleEvidenceState] = {}
    for request_result in _source_request_results(source, request_order):
        request = request_by_hash.get(request_result.request_sha256)
        if request is None:
            raise ValueError("provider-history result request is absent from the verified plan")
        instrument_id = str(request.instrument_id)
        if request.kind is IbkrHistoricalRequestKind.SCHEDULE:
            if instrument_id in eligible_instruments:
                _record_schedule_evidence(
                    schedule_states.setdefault(instrument_id, _ScheduleEvidenceState()),
                    request,
                    request_result,
                )
            continue
        if instrument_id not in eligible_instruments:
            continue
        schedule_evidence = _schedule_evidence(schedule_states.get(instrument_id))
        for row in _iter_observations(
            source,
            request,
            request_result,
            policy=policy,
            schedule_evidence=schedule_evidence,
        ):
            key = (row.instrument_id, row.interval_start.date())
            if current_key is not None and key < current_key:
                raise ValueError("provider-history observations are not ordered by partition")
            if current_key is not None and key != current_key:
                yield ProviderHistoricalPartition.create(rows=tuple(current_rows))
                current_rows = []
            current_key = key
            row_upper_bound = bounds.get(key)
            if row_upper_bound is None:
                raise ValueError("provider-history partition is absent from the source plan")
            if len(current_rows) >= row_upper_bound:
                raise ValueError("provider-history partition rows exceed source-plan capacity")
            current_rows.append(row)
    if current_rows:
        yield ProviderHistoricalPartition.create(rows=tuple(current_rows))


def _source_request_results(
    source: ProviderHistorySource,
    request_order: Sequence[IbkrHistoricalRequest],
) -> Iterator[IbkrHistoricalRequestResult]:
    if isinstance(source, IbkrHistoricalResultArtifact):
        result_by_hash = {item.request_sha256: item for item in source.request_results}
        for request in request_order:
            result = result_by_hash.get(request.request_sha256)
            if result is None:
                raise ValueError("provider-history result request is absent from the verified plan")
            yield result
        return
    yield from source.iter_request_results(request_order=request_order)


def _record_schedule_evidence(
    state: _ScheduleEvidenceState,
    request: IbkrHistoricalRequest,
    result: IbkrHistoricalRequestResult,
) -> None:
    state.request_hashes.append(request.request_sha256)
    state.result_hashes.append(result.result_sha256)
    state.states.add(result.session_state or "UNKNOWN")
    state.sessions.extend(result.sessions)
    if result.evidence_disposition is not IbkrHistoricalEvidenceDisposition.SUCCEEDED:
        state.failed = True


def _schedule_evidence(
    state: _ScheduleEvidenceState | None,
) -> dict[str, JsonValue]:
    if state is None or not state.request_hashes:
        raise ValueError("provider-history requires schedule evidence per instrument")
    if state.failed:
        raise ValueError("provider-history requires successful schedule evidence")
    sessions_json = cast(list[JsonValue], state.sessions)
    request_sha256: JsonValue = (
        state.request_hashes[0]
        if len(state.request_hashes) == 1
        else cast(list[JsonValue], state.request_hashes)
    )
    result_sha256: JsonValue = (
        state.result_hashes[0]
        if len(state.result_hashes) == 1
        else cast(list[JsonValue], state.result_hashes)
    )
    return {
        "request_sha256": request_sha256,
        "result_sha256": result_sha256,
        "schedule_state": next(iter(state.states)) if len(state.states) == 1 else "MIXED",
        "sessions": sessions_json,
    }


def _provider_history_eligible_instruments(
    source: ProviderHistorySource,
) -> frozenset[str]:
    if isinstance(source, IbkrHistoricalResultArtifact):
        replay_ibkr_historical_aggregate_result(
            source.plan,
            source.plan_bytes,
            source.request_results,
            source.aggregate,
        )
    raw = source.aggregate.entitlement_summary.get("provider_history_eligible_instruments")
    if not isinstance(raw, list):
        raise ValueError("IBKR aggregate provider-history eligibility summary is invalid")
    eligible_items: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ValueError("IBKR aggregate provider-history eligibility summary is invalid")
        eligible_items.append(item)
    eligible = frozenset(eligible_items)
    if len(eligible) != len(raw):
        raise ValueError("IBKR aggregate provider-history eligibility summary is not unique")
    return eligible


def replay_provider_history_dataset(
    dataset: ProviderHistoricalDataset,
) -> ProviderHistoricalDataset:
    """Recompute the root identity from bounded semantic partition references."""
    return ProviderHistoricalDataset.create(
        partitions=dataset.partitions,
        contract_selection_sha256=dataset.contract_selection_sha256,
        plan_sha256=dataset.plan_sha256,
        runtime_sha256=dataset.runtime_sha256,
        aggregate_sha256=dataset.aggregate_sha256,
        availability_policy=dataset.availability_policy,
    )


def _iter_observations(
    source: ProviderHistorySource,
    request: IbkrHistoricalRequest,
    request_result: IbkrHistoricalRequestResult,
    *,
    policy: ProviderHistoricalAvailabilityPolicy,
    schedule_evidence: dict[str, JsonValue],
) -> Iterator[ProviderHistoricalObservation]:
    if request_result.evidence_disposition is not IbkrHistoricalEvidenceDisposition.SUCCEEDED:
        if request_result.accepted_rows:
            raise ValueError(
                "provider-history cannot accept bars from unsuccessful request evidence"
            )
        return
    if not request_result.accepted_rows:
        return
    attempt = next(
        (
            item
            for item in request_result.attempts
            if item.attempt_id == request_result.selected_attempt_id
        ),
        None,
    )
    if attempt is None or attempt.terminal_at is None:
        raise ValueError("provider-history successful bars require completed selected attempt")
    for raw in request_result.accepted_rows:
        row = dict(raw)
        start = _timestamp(row["bar_start"], "bar_start")
        end = _timestamp(row["bar_end"], "bar_end")
        yield ProviderHistoricalObservation.create(
            source_class=PROVIDER_HISTORY_SOURCE_CLASS,
            provider=PROVIDER_HISTORY_PROVIDER,
            environment=PROVIDER_HISTORY_ENVIRONMENT,
            instrument_id=str(request.instrument_id),
            contract_selection_identity=source.plan.contract_selection_sha256,
            plan_sha256=source.plan.plan_sha256,
            interval_start=start,
            interval_end=end,
            basis=PROVIDER_HISTORY_BAR_BASIS,
            open=_price(row["open"], "open"),
            high=_price(row["high"], "high"),
            low=_price(row["low"], "low"),
            close=_price(row["close"], "close"),
            request_sha256=request.request_sha256,
            result_sha256=request_result.result_sha256,
            aggregate_sha256=source.aggregate.aggregate_sha256,
            attempt_id=attempt.attempt_id,
            attempt_started_at=attempt.started_at,
            attempt_completed_at=attempt.terminal_at,
            acquisition_started_at=request_result.acquisition_started_at,
            acquisition_completed_at=request_result.acquisition_completed_at,
            available_at=end + policy.delay,
            availability_selector=policy.selector,
            availability_policy=policy.policy,
            availability_delay=policy.delay_text,
            correction_policy=PROVIDER_HISTORY_CORRECTION_POLICY,
            schedule_evidence=schedule_evidence,
            gap_disposition="BAR_ACCEPTED",
            volume=None if row.get("volume") is None else str(row["volume"]),
            wap=None if row.get("wap") is None else str(row["wap"]),
            count=_optional_int(row.get("count"), "count"),
            callback_sequence=(
                None
                if row.get("callback_sequence") is None
                else _optional_int(row.get("callback_sequence"), "callback_sequence")
            ),
        )


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"IBKR accepted {field} must use UTC Z notation")
    result = datetime.fromisoformat(value[:-1] + "+00:00")
    require_utc(result, f"IBKR accepted {field}")
    return result


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"IBKR accepted {field} must be an integer when present")
    return value


def _price(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError(f"IBKR accepted {field} must be a decimal string")
    try:
        price = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"IBKR accepted {field} must be a finite decimal") from error
    if not price.is_finite():
        raise ValueError(f"IBKR accepted {field} must be a finite decimal")
    return price
