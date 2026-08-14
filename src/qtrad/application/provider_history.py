"""Build and replay provider-history observations from verified IBKR results."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol, cast

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
    ProviderHistoricalDatasetV3,
    ProviderHistoricalObservation,
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

    dataset: Any
    observations: ProviderHistoryObservationRows
    source_artifact: ProviderHistorySource
    request_evidence: tuple[ProviderHistoryRequestEvidence, ...] = ()
    observation_summary: ProviderHistoryObservationSummary | None = None
    selection: ProviderHistorySelection | None = None

    def __post_init__(self) -> None:
        if isinstance(self.dataset, ProviderHistoricalDatasetV3):
            source_result = getattr(self.source_artifact, "source_result", None)
            if source_result is None:
                raise ValueError("provider-history v3 source result summary is missing")
            if (
                self.dataset.contract_selection_sha256
                != self.source_artifact.plan.contract_selection_sha256
            ):
                raise ValueError(
                    "provider-history v3 source contract selection differs from its dataset"
                )
            if self.dataset.stage6_result_id != source_result.result_id:
                raise ValueError("provider-history v3 Stage 6 result differs from its dataset")
            if self.dataset.stage6_closure_id != source_result.closure_id:
                raise ValueError("provider-history v3 Stage 6 closure differs from its dataset")
            if (
                getattr(self.source_artifact, "verification_id", None)
                != self.dataset.stage6_verification_id
            ):
                raise ValueError(
                    "provider-history v3 Stage 6 verification differs from its dataset"
                )
            if self.dataset.stage6_plan_sha256 != self.source_artifact.plan.plan_sha256:
                raise ValueError("provider-history v3 Stage 6 plan differs from its dataset")
        else:
            if (
                self.dataset.contract_selection_sha256
                != self.source_artifact.plan.contract_selection_sha256
            ):
                raise ValueError(
                    "provider-history source contract selection differs from its dataset"
                )
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


def _provider_history_eligible_instruments(
    source: ProviderHistorySource,
) -> frozenset[str]:
    source_result = getattr(source, "source_result", None)
    if source_result is None:
        source_result = source.aggregate
    raw_value: object = source_result.entitlement_summary.get(
        "provider_history_eligible_instruments"
    )
    if not isinstance(raw_value, list):
        raise ValueError("IBKR source-result eligibility summary is invalid")
    raw: list[object] = cast(list[object], raw_value)
    eligible_items: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ValueError("IBKR source-result eligibility summary is invalid")
        eligible_items.append(item)
    eligible = frozenset(eligible_items)
    if len(eligible) != len(raw):
        raise ValueError("IBKR source-result eligibility summary is not unique")
    return eligible


@dataclass(frozen=True, slots=True)
class ProviderHistoryStage6Summary:
    """Minimal Stage 6 identity and entitlement handoff for Stage 8."""

    result_id: str | None
    closure_id: str | None
    verification_id: str | None
    coverage_summary: Mapping[str, JsonValue]
    entitlement_summary: Mapping[str, JsonValue]
    legacy_aggregate_sha256: str | None


def provider_history_stage6_summary(
    source_evidence: ProviderHistorySourceEvidence,
) -> ProviderHistoryStage6Summary:
    """Return explicit Stage 6 source-result metadata for current consumers.

    The v2 aggregate branch is retained only for named migration readers and is
    deliberately not represented as a v3 result identity.
    """

    if isinstance(source_evidence.dataset, ProviderHistoricalDatasetV3):
        source_result = getattr(source_evidence.source_artifact, "source_result", None)
        if source_result is None:
            raise ValueError("provider-history v3 source result summary is missing")
        verification_id = getattr(source_evidence.source_artifact, "verification_id", None)
        return ProviderHistoryStage6Summary(
            result_id=source_result.result_id,
            closure_id=source_result.closure_id,
            verification_id=verification_id,
            coverage_summary=cast(Mapping[str, JsonValue], source_result.coverage_summary),
            entitlement_summary=cast(Mapping[str, JsonValue], source_result.entitlement_summary),
            legacy_aggregate_sha256=None,
        )

    aggregate = source_evidence.source_artifact.aggregate
    return ProviderHistoryStage6Summary(
        result_id=None,
        closure_id=None,
        verification_id=None,
        coverage_summary=aggregate.coverage_summary,
        entitlement_summary=aggregate.entitlement_summary,
        legacy_aggregate_sha256=aggregate.aggregate_sha256,
    )
