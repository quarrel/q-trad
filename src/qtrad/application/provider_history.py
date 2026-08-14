"""Build and replay provider-history observations from verified IBKR results."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
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
    sha256_json,
    utc_text,
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


class ProviderHistoryObservationRows(Protocol):
    """Re-iterable provider rows without requiring one in-memory tuple."""

    def __iter__(self) -> Iterator[ProviderHistoricalObservation]: ...

    def __len__(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ProviderHistoryObservationSummary:
    """Compact accepted-row coverage captured during authoritative verification."""

    accepted_intervals_by_request: tuple[
        tuple[str, tuple[tuple[datetime, datetime], ...]],
        ...,
    ]
    source_start: datetime
    source_end: datetime

    def __post_init__(self) -> None:
        require_utc(self.source_start, "provider-history source start")
        require_utc(self.source_end, "provider-history source end")
        if self.source_end <= self.source_start:
            raise ValueError("provider-history source bounds are invalid")
        previous_request: str | None = None
        for request_sha256, intervals in self.accepted_intervals_by_request:
            if not request_sha256 or (
                previous_request is not None and request_sha256 <= previous_request
            ):
                raise ValueError("provider-history summary requests are not canonical")
            previous_request = request_sha256
            previous_end: datetime | None = None
            for interval_start, interval_end in intervals:
                require_utc(interval_start, "provider-history accepted interval start")
                require_utc(interval_end, "provider-history accepted interval end")
                if interval_end <= interval_start or (
                    previous_end is not None and interval_start <= previous_end
                ):
                    raise ValueError("provider-history accepted intervals are not canonical")
                previous_end = interval_end

    def intervals_by_request(self) -> dict[str, tuple[tuple[datetime, datetime], ...]]:
        return dict(self.accepted_intervals_by_request)


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


_PROVIDER_HISTORY_SELECTION_CONTRACT = "qtrad-provider-history-selection-v1"


@dataclass(frozen=True, slots=True)
class ProviderHistorySelection:
    """Authenticated physical-part selection over one semantic parent dataset."""

    parent_manifest_sha256: str
    parent_dataset_sha256: str
    requested_instrument_ids: tuple[str, ...]
    interval_start: datetime
    interval_end: datetime
    selected_part_sha256: tuple[str, ...]
    row_count_upper_bound: int
    selection_sha256: str

    @classmethod
    def create(
        cls,
        *,
        parent_manifest_sha256: str,
        parent_dataset_sha256: str,
        requested_instrument_ids: Sequence[str],
        interval_start: datetime,
        interval_end: datetime,
        selected_part_references: Sequence[Mapping[str, JsonValue]],
        row_count_upper_bound: int,
    ) -> ProviderHistorySelection:
        instruments = tuple(sorted(set(requested_instrument_ids)))
        part_sha256 = tuple(sha256_json(reference) for reference in selected_part_references)
        identity: dict[str, JsonValue] = {
            "contract": _PROVIDER_HISTORY_SELECTION_CONTRACT,
            "parent_manifest_sha256": parent_manifest_sha256,
            "parent_dataset_sha256": parent_dataset_sha256,
            "requested_instrument_ids": list(instruments),
            "interval_start": utc_text(interval_start),
            "interval_end": utc_text(interval_end),
            "selected_part_sha256": list(part_sha256),
            "row_count_upper_bound": row_count_upper_bound,
        }
        return cls(
            parent_manifest_sha256=parent_manifest_sha256,
            parent_dataset_sha256=parent_dataset_sha256,
            requested_instrument_ids=instruments,
            interval_start=interval_start,
            interval_end=interval_end,
            selected_part_sha256=part_sha256,
            row_count_upper_bound=row_count_upper_bound,
            selection_sha256=sha256_json(identity),
        )

    def __post_init__(self) -> None:
        for value, field in (
            (self.parent_manifest_sha256, "selection parent manifest identity"),
            (self.parent_dataset_sha256, "selection parent dataset identity"),
            (self.selection_sha256, "selection identity"),
            *((value, "selection part identity") for value in self.selected_part_sha256),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{field} must be a lowercase SHA-256 digest")
        if not self.requested_instrument_ids or self.requested_instrument_ids != tuple(
            sorted(set(self.requested_instrument_ids))
        ):
            raise ValueError("provider-history selection instruments are not canonical")
        require_utc(self.interval_start, "provider-history selection start")
        require_utc(self.interval_end, "provider-history selection end")
        if self.interval_end <= self.interval_start:
            raise ValueError("provider-history selection interval is invalid")
        if len(set(self.selected_part_sha256)) != len(self.selected_part_sha256):
            raise ValueError("provider-history selected part identities are not unique")
        if self.row_count_upper_bound < 0:
            raise ValueError("provider-history selection row bound must not be negative")
        if self.selection_sha256 != sha256_json(self.as_json_value(include_identity=False)):
            raise ValueError("provider-history selection identity changed")

    def as_json_value(self, *, include_identity: bool = True) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "contract": _PROVIDER_HISTORY_SELECTION_CONTRACT,
            "parent_manifest_sha256": self.parent_manifest_sha256,
            "parent_dataset_sha256": self.parent_dataset_sha256,
            "requested_instrument_ids": list(self.requested_instrument_ids),
            "interval_start": utc_text(self.interval_start),
            "interval_end": utc_text(self.interval_end),
            "selected_part_sha256": list(self.selected_part_sha256),
            "row_count_upper_bound": self.row_count_upper_bound,
        }
        if include_identity:
            value["selection_sha256"] = self.selection_sha256
        return value


@dataclass(frozen=True, slots=True)
class ProviderHistorySourceEvidence:
    """Verified Stage 6 closure and Stage 7 rows used by foundation readiness."""

    dataset: ProviderHistoricalDataset
    observations: ProviderHistoryObservationRows
    source_artifact: ProviderHistorySource
    request_evidence: tuple[ProviderHistoryRequestEvidence, ...] = ()
    observation_summary: ProviderHistoryObservationSummary | None = None
    selection: ProviderHistorySelection | None = None

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
        if self.selection is None:
            if len(self.observations) != self.dataset.row_count:
                raise ValueError(
                    "provider-history source observation count differs from its dataset"
                )
            return
        if (
            self.selection.parent_dataset_sha256 != self.dataset.dataset_sha256
            or self.selection.row_count_upper_bound != len(self.observations)
            or self.selection.row_count_upper_bound > self.dataset.row_count
        ):
            raise ValueError("provider-history selection differs from its source evidence")


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
