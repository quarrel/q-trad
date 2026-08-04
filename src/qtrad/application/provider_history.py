"""Build and replay provider-history observations from verified IBKR results."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from qtrad.application.ibkr_results import replay_ibkr_historical_aggregate_result
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import IbkrHistoricalRequest, IbkrHistoricalRequestKind
from qtrad.domain.ibkr_results import (
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
)
from qtrad.domain.time import require_utc


def build_provider_history_dataset(
    artifact: IbkrHistoricalResultArtifact,
    *,
    availability_delay: timedelta,
) -> ProviderHistoricalDataset:
    """Translate only the independently replayable aggregate closure into observations."""
    policy = ProviderHistoricalAvailabilityPolicy(
        selector=PROVIDER_HISTORY_DECLARED_DELAY,
        policy=PROVIDER_HISTORY_POLICY,
        delay=availability_delay,
    )
    eligible_instruments = _provider_history_eligible_instruments(artifact)
    rows = tuple(
        row
        for request_result in artifact.request_results
        for request in (_plan_request(artifact, request_result),)
        if (
            request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
            and str(request.instrument_id) in eligible_instruments
        )
        for row in _build_observations(artifact, request, request_result, policy=policy)
    )
    return ProviderHistoricalDataset.create(
        rows=rows,
        contract_selection_sha256=artifact.plan.contract_selection_sha256,
        plan_sha256=artifact.plan.plan_sha256,
        runtime_sha256=artifact.plan.runtime_sha256,
        aggregate_sha256=artifact.aggregate.aggregate_sha256,
        availability_policy=policy,
    )


def provider_history_partition_row_bounds(
    artifact: IbkrHistoricalResultArtifact,
) -> dict[tuple[str, date], int]:
    eligible_instruments = _provider_history_eligible_instruments(artifact)
    bounds: dict[tuple[str, date], int] = {}
    for request in artifact.plan.requests:
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
    artifact: IbkrHistoricalResultArtifact,
) -> frozenset[str]:
    replay_ibkr_historical_aggregate_result(
        artifact.plan,
        artifact.plan_bytes,
        artifact.request_results,
        artifact.aggregate,
    )
    raw = artifact.aggregate.entitlement_summary.get("provider_history_eligible_instruments")
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
    """Recompute row, availability, and dataset identities from persisted rows."""
    rebuilt_rows = tuple(
        ProviderHistoricalObservation.create(**_observation_values(row)) for row in dataset.rows
    )
    return ProviderHistoricalDataset.create(
        rows=rebuilt_rows,
        contract_selection_sha256=dataset.contract_selection_sha256,
        plan_sha256=dataset.plan_sha256,
        runtime_sha256=dataset.runtime_sha256,
        aggregate_sha256=dataset.aggregate_sha256,
        availability_policy=dataset.availability_policy,
    )


def _plan_request(
    artifact: IbkrHistoricalResultArtifact,
    request_result: IbkrHistoricalRequestResult,
) -> IbkrHistoricalRequest:
    request = next(
        (
            item
            for item in artifact.plan.requests
            if item.request_sha256 == request_result.request_sha256
        ),
        None,
    )
    if request is None:
        raise ValueError("provider-history result request is absent from the verified plan")
    if request.as_json_value() != request_result.request_payload:
        raise ValueError("provider-history request payload differs from the verified plan")
    return request


def _build_observations(
    artifact: IbkrHistoricalResultArtifact,
    request: IbkrHistoricalRequest,
    request_result: IbkrHistoricalRequestResult,
    *,
    policy: ProviderHistoricalAvailabilityPolicy,
) -> tuple[ProviderHistoricalObservation, ...]:
    if request_result.evidence_disposition is not IbkrHistoricalEvidenceDisposition.SUCCEEDED:
        if request_result.accepted_rows:
            raise ValueError(
                "provider-history cannot accept bars from unsuccessful request evidence"
            )
        return ()
    if not request_result.accepted_rows:
        return ()
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
    schedule_evidence = _schedule_evidence(
        artifact,
        request.instrument_id,
    )
    rows: list[ProviderHistoricalObservation] = []
    for raw in request_result.accepted_rows:
        row = dict(raw)
        start = _timestamp(row["bar_start"], "bar_start")
        end = _timestamp(row["bar_end"], "bar_end")
        rows.append(
            ProviderHistoricalObservation.create(
                source_class=PROVIDER_HISTORY_SOURCE_CLASS,
                provider=PROVIDER_HISTORY_PROVIDER,
                environment=PROVIDER_HISTORY_ENVIRONMENT,
                instrument_id=str(request.instrument_id),
                contract_selection_identity=artifact.plan.contract_selection_sha256,
                plan_sha256=artifact.plan.plan_sha256,
                interval_start=start,
                interval_end=end,
                basis=PROVIDER_HISTORY_BAR_BASIS,
                open=_price(row["open"], "open"),
                high=_price(row["high"], "high"),
                low=_price(row["low"], "low"),
                close=_price(row["close"], "close"),
                request_sha256=request.request_sha256,
                result_sha256=request_result.result_sha256,
                aggregate_sha256=artifact.aggregate.aggregate_sha256,
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
        )
    return tuple(rows)


def _schedule_evidence(
    artifact: IbkrHistoricalResultArtifact,
    instrument_id: object,
) -> dict[str, JsonValue]:
    schedule_results = [
        result
        for result in artifact.request_results
        if (
            _plan_request(artifact, result).instrument_id == instrument_id
            and _plan_request(artifact, result).kind is IbkrHistoricalRequestKind.SCHEDULE
        )
    ]
    if len(schedule_results) != 1:
        raise ValueError("provider-history requires exactly one schedule result per instrument")
    result = schedule_results[0]
    if result.evidence_disposition is not IbkrHistoricalEvidenceDisposition.SUCCEEDED:
        raise ValueError("provider-history requires successful schedule evidence")
    return {
        "request_sha256": _plan_request(artifact, result).request_sha256,
        "result_sha256": result.result_sha256,
        "schedule_state": result.session_state,
        "sessions": list(result.sessions),
    }


def _observation_values(row: ProviderHistoricalObservation) -> dict[str, object]:
    return {
        "source_class": row.source_class,
        "provider": row.provider,
        "environment": row.environment,
        "instrument_id": row.instrument_id,
        "contract_selection_identity": row.contract_selection_identity,
        "plan_sha256": row.plan_sha256,
        "interval_start": row.interval_start,
        "interval_end": row.interval_end,
        "basis": row.basis,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "request_sha256": row.request_sha256,
        "result_sha256": row.result_sha256,
        "aggregate_sha256": row.aggregate_sha256,
        "attempt_id": row.attempt_id,
        "attempt_started_at": row.attempt_started_at,
        "attempt_completed_at": row.attempt_completed_at,
        "acquisition_started_at": row.acquisition_started_at,
        "acquisition_completed_at": row.acquisition_completed_at,
        "available_at": row.available_at,
        "availability_selector": row.availability_selector,
        "availability_policy": row.availability_policy,
        "availability_delay": row.availability_delay,
        "correction_policy": row.correction_policy,
        "schedule_evidence": dict(row.schedule_evidence),
        "gap_disposition": row.gap_disposition,
        "volume": row.volume,
        "wap": row.wap,
        "count": row.count,
        "callback_sequence": row.callback_sequence,
    }


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
