from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from qtrad.adapters.postgres.ibkr_historical import PostgresIbkrHistoricalExecutionStore
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.application.ibkr_execution import IbkrHistoricalExecutor
from qtrad.application.ibkr_historical import build_ibkr_historical_request_profile
from qtrad.application.ibkr_results import verify_ibkr_historical_execution_snapshot
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrAttemptStatus,
    IbkrHistoricalAttempt,
    IbkrHistoricalAttemptOutcome,
    IbkrHistoricalCallback,
    IbkrHistoricalCallbackKind,
    IbkrHistoricalCallbackRecord,
    IbkrHistoricalCompletionMarker,
    IbkrHistoricalConnection,
    IbkrHistoricalDisconnected,
    IbkrHistoricalIncomplete,
    IbkrHistoricalRetryableError,
    IbkrHistoricalTerminalError,
    IbkrPlanRegistrationStatus,
    IbkrPublicationStatus,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
    ibkr_historical_plan_bytes,
)
from qtrad.domain.ibkr_historical import (
    HISTORICAL_PLAN_CONTRACT,
    IbkrContractFingerprint,
    IbkrHistoricalPacingPolicy,
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
    IbkrHistoricalRequestProfile,
    IbkrPlannedContract,
    sha256_json,
    utc_text,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.ports.ibkr_historical import (
    IbkrContractReauthentication,
    IbkrHistoricalCallbackSink,
    IbkrHistoricalDataPort,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_SESSION_ID = UUID("00000000-0000-0000-0000-000000000001")
_INSTRUMENT = InstrumentId("fx:aud-usd")
_FINGERPRINT = IbkrContractFingerprint(
    con_id=42,
    symbol="AUD",
    security_type="CASH",
    currency="USD",
    exchange="IDEALPRO",
    primary_exchange=None,
    local_symbol="AUD.USD",
    trading_class="AUD.USD",
    multiplier=None,
    underlying_con_id=None,
    contract_month=None,
)


def _request(kind: IbkrHistoricalRequestKind) -> IbkrHistoricalRequest:
    identity: dict[str, JsonValue] = {
        "instrument_id": str(_INSTRUMENT),
        "fingerprint": _FINGERPRINT.as_json_value(),
        "kind": kind.value,
        "interval_start": utc_text(_NOW),
        "interval_end": utc_text(_NOW + timedelta(days=1)),
        "end_date_time": "20260102-00:00:00 UTC",
        "duration": "1 D",
        "bar_size": "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day",
        "what_to_show": (
            "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
        ),
        "use_rth": False,
        "format_date": 2,
        "keep_up_to_date": False,
    }
    return IbkrHistoricalRequest(
        instrument_id=_INSTRUMENT,
        fingerprint=_FINGERPRINT,
        kind=kind,
        interval_start=_NOW,
        interval_end=_NOW + timedelta(days=1),
        end_date_time="20260102-00:00:00 UTC",
        duration="1 D",
        bar_size=cast(str | None, identity["bar_size"]),
        what_to_show=cast(str | None, identity["what_to_show"]),
        use_rth=False,
        format_date=cast(int | None, identity["format_date"]),
        keep_up_to_date=False,
        request_sha256=sha256_json(identity),
    )


def _profile(
    *,
    retry_count: int = 1,
    pacing_policy: IbkrHistoricalPacingPolicy | None = None,
) -> IbkrHistoricalRequestProfile:
    if pacing_policy is None:
        pacing_policy = IbkrHistoricalPacingPolicy(15, 2, 5, 600, 55)
    return build_ibkr_historical_request_profile(
        canary_evidence_filename="canary.json",
        canary_evidence_sha256="a" * 64,
        canary_evidence_file_sha256="b" * 64,
        canary_runtime_sha256="c" * 64,
        canary_selection_sha256="d" * 64,
        frozen_by="test-operator",
        frozen_at=_NOW,
        permitted_bar_durations=("1 D",),
        permitted_schedule_durations=("1 D",),
        bar_duration_by_asset_class={
            AssetClass.FX: "1 D",
            AssetClass.INDEX: "1 D",
            AssetClass.COMMODITY: "1 D",
        },
        schedule_duration="1 D",
        maximum_in_flight_requests=1,
        request_timeout_seconds=1,
        retry_count=retry_count,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        pacing_policy=pacing_policy,
    )


def _plan() -> tuple[IbkrHistoricalPlan, IbkrHistoricalRequestProfile]:
    requests = tuple(_request(kind) for kind in IbkrHistoricalRequestKind)
    profile = _profile()
    eligible = (IbkrPlannedContract(_INSTRUMENT, _FINGERPRINT),)
    identity: dict[str, JsonValue] = {
        "contract": HISTORICAL_PLAN_CONTRACT,
        "schema_version": 1,
        "contract_selection_sha256": "b" * 64,
        "runtime_sha256": "c" * 64,
        "request_profile_sha256": profile.profile_sha256,
        "provider": "ibkr",
        "environment": "paper",
        "planner_qtrad_commit": "d" * 40,
        "planner_qtrad_image_digest": "sha256:" + "e" * 64,
        "start": utc_text(_NOW),
        "end": utc_text(_NOW + timedelta(days=1)),
        "eligible_contracts": [eligible[0].as_json_value()],
        "requests": [request.as_json_value() for request in requests],
    }
    plan = IbkrHistoricalPlan(
        contract_selection_sha256="b" * 64,
        runtime_sha256="c" * 64,
        request_profile_sha256=profile.profile_sha256,
        provider="ibkr",
        environment="paper",
        planner_qtrad_commit="d" * 40,
        planner_qtrad_image_digest="sha256:" + "e" * 64,
        start=_NOW,
        end=_NOW + timedelta(days=1),
        eligible_contracts=eligible,
        requests=requests,
        plan_sha256=sha256_json(identity),
    )
    return plan, profile


class MemoryExecutionStore:
    def __init__(self) -> None:
        self.plans: dict[str, bytes] = {}
        self.plan_requests: dict[str, dict[str, IbkrHistoricalRequest]] = {}
        self.request_status: dict[tuple[str, str], IbkrRequestStatus] = {}
        self.attempt_count: dict[tuple[str, str], int] = defaultdict(int)
        self.attempts: dict[UUID, IbkrHistoricalAttempt] = {}
        self.callbacks: dict[UUID, list[IbkrHistoricalCallbackRecord]] = defaultdict(list)
        self.markers: dict[UUID, list[IbkrHistoricalCompletionMarker]] = defaultdict(list)
        self._callback_ids = 0
        self._marker_ids = 0
        self.fail_invalidation = False
        self.publication_status: dict[tuple[str, str], IbkrPublicationStatus] = {}
        self.result_hashes: dict[tuple[str, str], str] = {}
        self.plan_publication_status: dict[str, IbkrPublicationStatus] = {}
        self.selected_attempt_ids: dict[tuple[str, str], UUID | None] = {}

    async def register_ibkr_historical_plan(
        self,
        plan: IbkrHistoricalPlan,
        *,
        plan_bytes: bytes | None,
        registered_at: datetime,
    ) -> IbkrPlanRegistrationStatus:
        del registered_at
        encoded = plan_bytes if plan_bytes is not None else ibkr_historical_plan_bytes(plan)
        if plan.plan_sha256 in self.plans:
            if self.plans[plan.plan_sha256] != encoded:
                raise RuntimeError("plan bytes conflict")
            return IbkrPlanRegistrationStatus.ALREADY_REGISTERED
        self.plans[plan.plan_sha256] = encoded
        self.plan_requests[plan.plan_sha256] = {
            request.request_sha256: request for request in plan.requests
        }
        self.plan_publication_status[plan.plan_sha256] = IbkrPublicationStatus.PENDING
        for request in plan.requests:
            key = (plan.plan_sha256, request.request_sha256)
            self.request_status[key] = IbkrRequestStatus.PENDING
            self.publication_status[key] = IbkrPublicationStatus.PENDING
            self.selected_attempt_ids[key] = None
        return IbkrPlanRegistrationStatus.REGISTERED

    async def recover_ibkr_historical_execution(
        self,
        *,
        plan_sha256: str,
        recovered_at: datetime,
        maximum_attempts: int,
    ) -> tuple[IbkrHistoricalAttemptOutcome, ...]:
        outcomes: list[IbkrHistoricalAttemptOutcome] = []
        for attempt in tuple(self.attempts.values()):
            if attempt.plan_sha256 == plan_sha256 and attempt.status is IbkrAttemptStatus.STARTED:
                try:
                    outcomes.append(
                        await self.finalize_ibkr_historical_attempt(
                            attempt_id=attempt.attempt_id,
                            completed_at=recovered_at,
                        )
                    )
                except IbkrHistoricalIncomplete:
                    outcomes.append(
                        await self._invalidate(attempt.attempt_id, recovered_at, maximum_attempts)
                    )
        return tuple(outcomes)

    async def pending_ibkr_historical_requests(self, plan_sha256: str) -> tuple[str, ...]:
        return tuple(
            request_sha256
            for request_sha256 in sorted(self.plan_requests[plan_sha256])
            if self.request_status[(plan_sha256, request_sha256)] is IbkrRequestStatus.PENDING
        )

    async def start_ibkr_historical_attempt(
        self,
        *,
        plan_sha256: str,
        request_sha256: str,
        connection_session_id: UUID,
        connection_generation: int,
        provider_request_id: int,
        started_at: datetime,
        maximum_attempts: int,
    ) -> IbkrHistoricalAttempt | None:
        key = (plan_sha256, request_sha256)
        if self.request_status[key] is not IbkrRequestStatus.PENDING:
            return None
        ordinal = self.attempt_count[key] + 1
        if ordinal > maximum_attempts:
            raise RuntimeError("retry budget exceeded")
        self.attempt_count[key] = ordinal
        attempt = IbkrHistoricalAttempt(
            attempt_id=uuid4(),
            plan_sha256=plan_sha256,
            request_sha256=request_sha256,
            connection_session_id=connection_session_id,
            attempt_ordinal=ordinal,
            provider_request_id=provider_request_id,
            connection_generation=connection_generation,
            started_at=started_at,
            status=IbkrAttemptStatus.STARTED,
        )
        self.attempts[attempt.attempt_id] = attempt
        self.request_status[key] = IbkrRequestStatus.IN_FLIGHT
        return attempt

    async def append_ibkr_historical_callback(
        self,
        *,
        attempt_id: UUID,
        callback: IbkrHistoricalCallback,
    ) -> IbkrHistoricalCallbackRecord:
        attempt = self.attempts[attempt_id]
        sequence = len(self.callbacks[attempt_id]) + 1
        eligible = (
            attempt.status is IbkrAttemptStatus.STARTED
            and callback.connection_session_id == attempt.connection_session_id
            and callback.provider_request_id == attempt.provider_request_id
            and callback.connection_generation == attempt.connection_generation
        )
        self._callback_ids += 1
        payload = dict(callback.payload)
        record = IbkrHistoricalCallbackRecord(
            callback_id=self._callback_ids,
            attempt_id=attempt_id,
            connection_session_id=callback.connection_session_id,
            provider_request_id=callback.provider_request_id,
            connection_generation=callback.connection_generation,
            sequence=sequence,
            kind=callback.kind,
            received_at=callback.received_at,
            payload=payload,
            closure_eligible=eligible,
        )
        self.callbacks[attempt_id].append(record)
        if callback.kind is IbkrHistoricalCallbackKind.COMPLETION:
            self._marker_ids += 1
            self.markers[attempt_id].append(
                IbkrHistoricalCompletionMarker(
                    marker_id=self._marker_ids,
                    attempt_id=attempt_id,
                    connection_session_id=callback.connection_session_id,
                    provider_request_id=callback.provider_request_id,
                    connection_generation=callback.connection_generation,
                    sequence=sequence,
                    completed_at=callback.received_at,
                    raw_midpoint_bar_callback_count=sum(
                        item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR
                        and item.closure_eligible
                        for item in self.callbacks[attempt_id]
                        if item.sequence < sequence
                    ),
                    raw_schedule_callback_count=sum(
                        item.kind is IbkrHistoricalCallbackKind.SCHEDULE and item.closure_eligible
                        for item in self.callbacks[attempt_id]
                        if item.sequence < sequence
                    ),
                    closure_eligible=eligible,
                    payload=payload,
                )
            )
        return record

    async def finalize_ibkr_historical_attempt(
        self,
        *,
        attempt_id: UUID,
        completed_at: datetime,
    ) -> IbkrHistoricalAttemptOutcome:
        attempt = self.attempts[attempt_id]
        if attempt.status is not IbkrAttemptStatus.STARTED:
            return self._outcome(attempt)
        marker = next(
            (
                item
                for item in self.markers[attempt_id]
                if item.closure_eligible
                and item.provider_request_id == attempt.provider_request_id
                and item.connection_session_id == attempt.connection_session_id
                and item.connection_generation == attempt.connection_generation
            ),
            None,
        )
        if marker is None:
            raise IbkrHistoricalIncomplete("no eligible marker")
        request = self.plan_requests[attempt.plan_sha256][attempt.request_sha256]
        has_error = any(
            item.kind is IbkrHistoricalCallbackKind.ERROR
            and item.closure_eligible
            and item.sequence < marker.sequence
            for item in self.callbacks[attempt_id]
        )
        if has_error:
            status = IbkrAttemptStatus.TERMINAL_FAILURE
            request_status = IbkrRequestStatus.TERMINAL
            disposition = IbkrTerminalDisposition.PROVIDER_REJECTED
            detail = "provider emitted an error callback before completion"
        elif (
            request.kind is IbkrHistoricalRequestKind.SCHEDULE
            and marker.raw_schedule_callback_count == 0
        ):
            raise IbkrHistoricalIncomplete("schedule request has no raw schedule callback")
        else:
            status = IbkrAttemptStatus.SUCCEEDED
            request_status = IbkrRequestStatus.SUCCEEDED
            disposition = IbkrTerminalDisposition.SUCCEEDED
            detail = None
        terminal_at = max(completed_at, attempt.started_at)
        updated = replace(
            attempt,
            status=status,
            terminal_at=terminal_at,
            terminal_disposition=disposition,
            detail=detail,
        )
        self.attempts[attempt_id] = updated
        self.request_status[(attempt.plan_sha256, attempt.request_sha256)] = request_status
        if request_status is not IbkrRequestStatus.PENDING:
            self.selected_attempt_ids[(attempt.plan_sha256, attempt.request_sha256)] = attempt_id
        return self._outcome(updated)

    async def fail_ibkr_historical_attempt(
        self,
        *,
        attempt_id: UUID,
        failed_at: datetime,
        disposition: str,
        detail: str,
        retryable: bool,
        maximum_attempts: int,
    ) -> IbkrHistoricalAttemptOutcome:
        attempt = self.attempts[attempt_id]
        if attempt.status is not IbkrAttemptStatus.STARTED:
            return self._outcome(attempt)
        parsed = IbkrTerminalDisposition(disposition)
        retry = retryable and attempt.attempt_ordinal < maximum_attempts
        status = (
            IbkrAttemptStatus.RETRYABLE_FAILURE if retry else IbkrAttemptStatus.TERMINAL_FAILURE
        )
        final = (
            parsed
            if retry
            else (IbkrTerminalDisposition.RETRY_LIMIT_EXHAUSTED if retryable else parsed)
        )
        request_status = IbkrRequestStatus.PENDING if retry else IbkrRequestStatus.TERMINAL
        updated = replace(
            attempt,
            status=status,
            terminal_at=max(failed_at, attempt.started_at),
            terminal_disposition=final,
            detail=detail,
        )
        self.attempts[attempt_id] = updated
        self.request_status[(attempt.plan_sha256, attempt.request_sha256)] = request_status
        if request_status is IbkrRequestStatus.TERMINAL:
            self.selected_attempt_ids[(attempt.plan_sha256, attempt.request_sha256)] = attempt_id
        return self._outcome(updated)

    async def invalidate_ibkr_historical_attempts(
        self,
        *,
        plan_sha256: str,
        connection_session_id: UUID,
        connection_generation: int,
        invalidated_at: datetime,
        maximum_attempts: int,
    ) -> tuple[IbkrHistoricalAttemptOutcome, ...]:
        if self.fail_invalidation:
            raise RuntimeError("injected invalidation failure")
        outcomes: list[IbkrHistoricalAttemptOutcome] = []
        for attempt in tuple(self.attempts.values()):
            if (
                attempt.plan_sha256 == plan_sha256
                and attempt.connection_session_id == connection_session_id
                and attempt.connection_generation == connection_generation
                and attempt.status is IbkrAttemptStatus.STARTED
            ):
                try:
                    outcome = await self.finalize_ibkr_historical_attempt(
                        attempt_id=attempt.attempt_id,
                        completed_at=invalidated_at,
                    )
                except IbkrHistoricalIncomplete:
                    outcome = await self._invalidate(
                        attempt.attempt_id,
                        invalidated_at,
                        maximum_attempts,
                    )
                outcomes.append(outcome)
        return tuple(outcomes)

    async def mark_ibkr_historical_request_published(
        self,
        *,
        plan_sha256: str,
        request_sha256: str,
        result_sha256: str,
        published_at: datetime,
    ) -> None:
        del published_at
        key = (plan_sha256, request_sha256)
        if key not in self.request_status:
            raise RuntimeError("IBKR publication targets an unknown request")
        if self.request_status[key] not in {
            IbkrRequestStatus.SUCCEEDED,
            IbkrRequestStatus.TERMINAL,
        }:
            raise RuntimeError("IBKR publication requires a terminal request")
        selected_id = self.selected_attempt_ids[key]
        selected = self.attempts.get(selected_id) if selected_id is not None else None
        if selected is None:
            raise RuntimeError("IBKR publication requires a selected terminal attempt")
        if selected.plan_sha256 != plan_sha256 or selected.request_sha256 != request_sha256:
            raise RuntimeError("IBKR publication requires a request-bound selected attempt")
        if self.request_status[key] is IbkrRequestStatus.SUCCEEDED:
            if (
                selected.status is not IbkrAttemptStatus.SUCCEEDED
                or selected.terminal_disposition is not IbkrTerminalDisposition.SUCCEEDED
            ):
                raise RuntimeError("IBKR publication requires a successful selected attempt")
        elif (
            selected.status is not IbkrAttemptStatus.TERMINAL_FAILURE
            or selected.terminal_disposition in {None, IbkrTerminalDisposition.SUCCEEDED}
        ):
            raise RuntimeError("IBKR publication requires a non-success selected attempt")
        if self.publication_status[key] is IbkrPublicationStatus.PUBLISHED:
            if self.result_hashes[key] != result_sha256:
                raise RuntimeError("IBKR publication identity conflicts with its immutable result")
            return
        self.publication_status[key] = IbkrPublicationStatus.PUBLISHED
        self.result_hashes[key] = result_sha256
        if all(
            status is IbkrPublicationStatus.PUBLISHED
            for status in (
                self.publication_status[(plan_sha256, request.request_sha256)]
                for request in self.plan_requests[plan_sha256].values()
            )
        ):
            self.plan_publication_status[plan_sha256] = IbkrPublicationStatus.PUBLISHED

    async def _invalidate(
        self, attempt_id: UUID, invalidated_at: datetime, maximum_attempts: int
    ) -> IbkrHistoricalAttemptOutcome:
        attempt = self.attempts[attempt_id]
        exhausted = attempt.attempt_ordinal >= maximum_attempts
        updated = replace(
            attempt,
            status=(
                IbkrAttemptStatus.TERMINAL_FAILURE if exhausted else IbkrAttemptStatus.INVALIDATED
            ),
            terminal_at=max(invalidated_at, attempt.started_at),
            terminal_disposition=(
                IbkrTerminalDisposition.RETRY_LIMIT_EXHAUSTED if exhausted else None
            ),
            detail="connection generation disconnected before attempt completion",
        )
        self.attempts[attempt_id] = updated
        self.request_status[(attempt.plan_sha256, attempt.request_sha256)] = (
            IbkrRequestStatus.TERMINAL if exhausted else IbkrRequestStatus.PENDING
        )
        if exhausted:
            self.selected_attempt_ids[(attempt.plan_sha256, attempt.request_sha256)] = attempt_id
        return self._outcome(updated)

    def _outcome(self, attempt: IbkrHistoricalAttempt) -> IbkrHistoricalAttemptOutcome:
        status = self.request_status[(attempt.plan_sha256, attempt.request_sha256)]
        return IbkrHistoricalAttemptOutcome(
            attempt=attempt,
            request_status=status,
            disposition=(
                IbkrTerminalDisposition.SUCCEEDED
                if status is IbkrRequestStatus.SUCCEEDED
                else attempt.terminal_disposition
                if status is IbkrRequestStatus.TERMINAL
                else None
            ),
        )


class FakePacer:
    def __init__(self, request_profile_sha256: str) -> None:
        self.request_profile_sha256 = request_profile_sha256
        self.calls: list[tuple[str, str, str, IbkrHistoricalPacingPolicy]] = []

    async def reserve(
        self,
        request_kind: str,
        contract_key: str,
        request_fingerprint: str,
        weight: int,
        *,
        request_profile_sha256: str,
        pacing_policy: IbkrHistoricalPacingPolicy,
    ) -> float:
        if request_profile_sha256 != self.request_profile_sha256:
            raise ValueError("fake pacer received an unexpected profile")
        self.calls.append((request_kind, contract_key, request_fingerprint, pacing_policy))
        assert weight == 1
        return 0.0


class FakeHistoricalDataPort(IbkrHistoricalDataPort):
    def __init__(self, behaviours: Mapping[str, list[str]]) -> None:
        self.behaviours = {key: list(value) for key, value in behaviours.items()}
        self.calls: list[str] = []
        self.reauthentication_calls: list[tuple[IbkrContractFingerprint, ...]] = []
        self.reauthentication_status = "MATCH"
        self.connect_count = 0
        self.disconnect_count = 0

    async def connect(self) -> IbkrHistoricalConnection:
        self.connect_count += 1
        return IbkrHistoricalConnection(
            connection_session_id=uuid4(),
            connection_generation=self.connect_count,
        )

    async def disconnect(self) -> None:
        self.disconnect_count += 1

    async def reauthenticate_contracts(
        self, fingerprints: Sequence[IbkrContractFingerprint]
    ) -> tuple[IbkrContractReauthentication, ...]:
        expected = tuple(fingerprints)
        self.reauthentication_calls.append(expected)
        return tuple(
            IbkrContractReauthentication(
                request_id=index,
                connection_generation=self.connect_count,
                expected=fingerprint,
                observed=(fingerprint,),
                status=self.reauthentication_status,
            )
            for index, fingerprint in enumerate(expected, start=100)
        )

    async def request_historical(
        self,
        request: IbkrHistoricalRequest,
        *,
        request_id: int,
        connection_session_id: UUID,
        connection_generation: int,
        callback: IbkrHistoricalCallbackSink,
    ) -> None:
        self.calls.append(request.request_sha256)
        action = self.behaviours[request.kind.value].pop(0)
        if action == "crash_before":
            raise RuntimeError("injected crash before provider callbacks")
        if action == "timeout":
            raise IbkrHistoricalRetryableError("temporary provider failure")
        if action == "disconnect":
            raise IbkrHistoricalDisconnected("injected disconnect")
        if action == "terminal":
            raise IbkrHistoricalTerminalError(
                IbkrTerminalDisposition.ENTITLEMENT_UNAVAILABLE,
                "entitlement unavailable",
            )
        callback_generation = (
            connection_generation + 1 if action == "stale" else connection_generation
        )
        callback_request_id = request_id + 1 if action == "wrong_request_id" else request_id
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS and action != "stale":
            await callback(
                IbkrHistoricalCallback(
                    connection_session_id=connection_session_id,
                    provider_request_id=callback_request_id,
                    connection_generation=callback_generation,
                    kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
                    received_at=_NOW,
                    payload={"open": "1", "close": "1"},
                )
            )
            if action == "crash_during_callback":
                raise RuntimeError("injected crash during callback")
        elif request.kind is IbkrHistoricalRequestKind.SCHEDULE and action != "stale":
            await callback(
                IbkrHistoricalCallback(
                    connection_session_id=connection_session_id,
                    provider_request_id=callback_request_id,
                    connection_generation=callback_generation,
                    kind=IbkrHistoricalCallbackKind.SCHEDULE,
                    received_at=_NOW,
                    payload={"active": False},
                )
            )
        await callback(
            IbkrHistoricalCallback(
                connection_session_id=connection_session_id,
                provider_request_id=callback_request_id,
                connection_generation=callback_generation,
                kind=IbkrHistoricalCallbackKind.COMPLETION,
                received_at=_NOW,
                payload={},
            )
        )
        if action == "crash_after_completion":
            raise RuntimeError("injected crash after completion marker")


def _executor(
    store: MemoryExecutionStore,
    provider: FakeHistoricalDataPort,
) -> tuple[IbkrHistoricalExecutor, IbkrHistoricalPlan, object]:
    plan, profile = _plan()
    executor = IbkrHistoricalExecutor(
        store,
        provider,
        FakePacer(profile.profile_sha256),
        clock=lambda: _NOW,
        sleep=asyncio.sleep,
    )
    return executor, plan, profile


@pytest.mark.asyncio
async def test_contract_reauthentication_mismatch_prevents_historical_request() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {kind.value: ["success"] for kind in IbkrHistoricalRequestKind}
    )
    provider.reauthentication_status = "MISMATCH"
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)

    with pytest.raises(RuntimeError, match="current-generation MATCH"):
        await IbkrHistoricalExecutor(
            store,
            provider,
            FakePacer(profile.profile_sha256),
            clock=lambda: _NOW,
        ).execute(plan, profile)

    assert provider.calls == []
    assert provider.reauthentication_calls == [
        tuple(contract.fingerprint for contract in plan.eligible_contracts)
    ]


@pytest.mark.asyncio
async def test_success_is_idempotent_and_callbacks_are_monotonic() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {kind.value: ["success"] for kind in IbkrHistoricalRequestKind}
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)
    executor = IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    )

    first = await executor.execute(plan, profile)
    second = await executor.execute(plan, profile)

    assert {outcome.disposition for outcome in first.outcomes} == {
        IbkrTerminalDisposition.SUCCEEDED
    }
    assert second.outcomes == ()
    assert len(provider.calls) == 2
    assert provider.connect_count == 1
    for callbacks in store.callbacks.values():
        assert [record.sequence for record in callbacks] == [1, 2]
        assert all(record.closure_eligible for record in callbacks)


@pytest.mark.asyncio
async def test_transient_retry_and_unrelated_terminal_request() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {
            IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["timeout", "success"],
            IbkrHistoricalRequestKind.SCHEDULE.value: ["terminal"],
        }
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)

    summary = await IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    ).execute(plan, profile)

    assert {outcome.request_status for outcome in summary.outcomes} == {
        IbkrRequestStatus.SUCCEEDED,
        IbkrRequestStatus.TERMINAL,
    }
    assert len(provider.calls) == 3
    assert (
        sum(
            attempt.status is IbkrAttemptStatus.RETRYABLE_FAILURE
            for attempt in store.attempts.values()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_crash_before_send_is_invalidated_and_restarted() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {
            IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["crash_before", "success"],
            IbkrHistoricalRequestKind.SCHEDULE.value: ["success"],
        }
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)
    executor = IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    )

    with pytest.raises(ExceptionGroup) as error:
        await executor.execute(plan, profile)
    assert "before provider callbacks" in str(error.value.exceptions[0])
    assert any(
        attempt.status is IbkrAttemptStatus.INVALIDATED for attempt in store.attempts.values()
    )

    summary = await executor.execute(plan, profile)
    assert any(
        outcome.disposition is IbkrTerminalDisposition.SUCCEEDED for outcome in summary.outcomes
    )
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_crash_during_callbacks_is_invalidated_and_restarted() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {
            IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["crash_during_callback", "success"],
            IbkrHistoricalRequestKind.SCHEDULE.value: ["success"],
        }
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)
    executor = IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    )

    with pytest.raises(ExceptionGroup) as error:
        await executor.execute(plan, profile)
    assert "during callback" in str(error.value.exceptions[0])
    invalidated = next(
        attempt
        for attempt in store.attempts.values()
        if attempt.status is IbkrAttemptStatus.INVALIDATED
    )
    assert [record.sequence for record in store.callbacks[invalidated.attempt_id]] == [1]

    summary = await executor.execute(plan, profile)
    assert any(
        outcome.disposition is IbkrTerminalDisposition.SUCCEEDED for outcome in summary.outcomes
    )
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_crash_after_completion_is_recovered_without_rerun() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {
            IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["crash_after_completion"],
            IbkrHistoricalRequestKind.SCHEDULE.value: ["success"],
        }
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)
    executor = IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    )

    with pytest.raises(ExceptionGroup) as error:
        await executor.execute(plan, profile)
    assert "after completion marker" in str(error.value.exceptions[0])
    calls_before_restart = len(provider.calls)

    summary = await executor.execute(plan, profile)

    assert len(provider.calls) == calls_before_restart
    assert summary.outcomes == ()
    assert any(attempt.status is IbkrAttemptStatus.SUCCEEDED for attempt in store.attempts.values())
    assert provider.connect_count == 1


@pytest.mark.asyncio
async def test_stale_generation_callbacks_cannot_enter_successful_closure() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {
            IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["stale"],
            IbkrHistoricalRequestKind.SCHEDULE.value: ["success"],
        }
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)

    summary = await IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    ).execute(plan, profile)

    stale_outcome = next(
        outcome
        for outcome in summary.outcomes
        if outcome.attempt.request_sha256
        == next(
            request.request_sha256
            for request in plan.requests
            if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
        )
    )
    assert stale_outcome.disposition is IbkrTerminalDisposition.INCOMPLETE_RESPONSE
    assert any(
        not callback.closure_eligible
        for callbacks in store.callbacks.values()
        for callback in callbacks
    )


@pytest.mark.asyncio
async def test_disconnect_invalidates_unfinished_attempt_for_restart_and_reauthenticates() -> None:
    store = MemoryExecutionStore()
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {
            IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["disconnect", "success"],
            IbkrHistoricalRequestKind.SCHEDULE.value: ["success"],
        }
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)
    executor = IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    )

    with pytest.raises(ExceptionGroup) as error:
        await executor.execute(plan, profile)
    assert "injected disconnect" in str(error.value.exceptions[0])
    assert any(
        attempt.status is IbkrAttemptStatus.INVALIDATED for attempt in store.attempts.values()
    )

    await executor.execute(plan, profile)
    assert len(provider.calls) == 3
    expected_fingerprints = tuple(contract.fingerprint for contract in plan.eligible_contracts)
    assert provider.reauthentication_calls == [expected_fingerprints, expected_fingerprints]


def _unique_plan() -> tuple[IbkrHistoricalPlan, IbkrHistoricalRequestProfile]:
    plan, profile = _plan()
    commit = uuid4().hex + uuid4().hex[:8]
    identity = plan.identity_payload()
    identity["planner_qtrad_commit"] = commit
    candidate = replace(
        plan,
        planner_qtrad_commit=commit,
        plan_sha256=sha256_json(identity),
    )
    return candidate, profile


@pytest.mark.skipif(
    not os.getenv("QTRAD_TEST_DATABASE_URL"),
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_postgres_store_executes_recovers_and_publishes_durably() -> None:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    assert database_url is not None
    engine = create_async_engine(database_url)
    audit_store = PostgresAuditStore(engine)
    await audit_store.seed_instruments()
    store = PostgresIbkrHistoricalExecutionStore(engine)
    plan, profile = _unique_plan()
    plan_bytes = ibkr_historical_plan_bytes(plan)

    assert (
        await store.register_ibkr_historical_plan(
            plan,
            plan_bytes=plan_bytes,
            registered_at=_NOW,
        )
        is IbkrPlanRegistrationStatus.REGISTERED
    )
    assert (
        await store.register_ibkr_historical_plan(
            plan,
            plan_bytes=plan_bytes,
            registered_at=_NOW,
        )
        is IbkrPlanRegistrationStatus.ALREADY_REGISTERED
    )
    with pytest.raises(RuntimeError, match="immutable bytes"):
        await store.register_ibkr_historical_plan(
            plan,
            plan_bytes=json.dumps(plan.as_json_value(), sort_keys=True).encode("utf-8"),
            registered_at=_NOW,
        )

    provider = FakeHistoricalDataPort(
        {kind.value: ["success"] for kind in IbkrHistoricalRequestKind}
    )
    executor = IbkrHistoricalExecutor(
        store, provider, FakePacer(profile.profile_sha256), clock=lambda: _NOW
    )
    first = await executor.execute(plan, profile)
    assert len(provider.calls) == len(plan.requests)
    assert all(
        outcome.disposition is IbkrTerminalDisposition.SUCCEEDED for outcome in first.outcomes
    )
    second = await executor.execute(plan, profile)
    assert second.outcomes == ()
    assert len(provider.calls) == len(plan.requests)
    assert provider.connect_count == 1

    request_rows = await audit_store.query(
        """
        SELECT request_sha256, status, publication_status
        FROM ops.ibkr_historical_requests
        WHERE plan_sha256 = :plan_sha256
        ORDER BY request_sha256
        """,
        {"plan_sha256": plan.plan_sha256},
    )
    assert [row["status"] for row in request_rows] == ["SUCCEEDED", "SUCCEEDED"]
    assert [row["publication_status"] for row in request_rows] == ["PENDING", "PENDING"]
    callback_rows = await audit_store.query(
        """
        SELECT attempt_id, sequence, connection_generation, closure_eligible
        FROM ops.ibkr_historical_callbacks
        WHERE attempt_id IN (
            SELECT attempt_id
            FROM ops.ibkr_historical_attempts
            WHERE plan_sha256 = :plan_sha256
        )
        ORDER BY attempt_id, sequence
        """,
        {"plan_sha256": plan.plan_sha256},
    )
    assert [row["sequence"] for row in callback_rows] == [1, 2, 1, 2]
    assert all(row["closure_eligible"] for row in callback_rows)

    result_hashes = [f"{index:064x}" for index, _ in enumerate(plan.requests, start=1)]
    snapshot = await store.read_ibkr_historical_execution(plan_sha256=plan.plan_sha256)
    assert snapshot.plan.plan_sha256 == plan.plan_sha256
    assert len(snapshot.requests) == len(plan.requests)
    assert len(snapshot.attempts) == len(plan.requests)
    assert len(snapshot.callbacks) == len(plan.requests) * 2
    assert len(snapshot.completion_markers) == len(plan.requests)
    verify_ibkr_historical_execution_snapshot(
        plan,
        snapshot,
        maximum_attempts=profile.retry_count + 1,
    )
    expected_fingerprints = tuple(contract.fingerprint for contract in plan.eligible_contracts)
    assert provider.reauthentication_calls == [expected_fingerprints]

    with pytest.raises(RuntimeError, match="unknown request"):
        await store.mark_ibkr_historical_requests_published(
            plan_sha256=plan.plan_sha256,
            publications=(
                (plan.requests[0].request_sha256, result_hashes[0]),
                ("a" * 64, "b" * 64),
            ),
            published_at=_NOW,
        )
    pending_after_failed_batch = await audit_store.query(
        """
        SELECT publication_status
        FROM ops.ibkr_historical_requests
        WHERE plan_sha256 = :plan_sha256
        ORDER BY request_sha256
        """,
        {"plan_sha256": plan.plan_sha256},
    )
    assert [row["publication_status"] for row in pending_after_failed_batch] == [
        "PENDING",
        "PENDING",
    ]

    await store.mark_ibkr_historical_requests_published(
        plan_sha256=plan.plan_sha256,
        publications=tuple(
            (request.request_sha256, result_hash)
            for request, result_hash in zip(plan.requests, result_hashes, strict=True)
        ),
        published_at=_NOW,
    )
    await store.mark_ibkr_historical_request_published(
        plan_sha256=plan.plan_sha256,
        request_sha256=plan.requests[0].request_sha256,
        result_sha256=result_hashes[0],
        published_at=_NOW,
    )
    with pytest.raises(RuntimeError, match="immutable result"):
        await store.mark_ibkr_historical_request_published(
            plan_sha256=plan.plan_sha256,
            request_sha256=plan.requests[0].request_sha256,
            result_sha256="f" * 64,
            published_at=_NOW,
        )
    plan_row = await audit_store.query(
        "SELECT publication_status FROM ops.ibkr_historical_plans WHERE plan_sha256 = :plan_sha256",
        {"plan_sha256": plan.plan_sha256},
    )
    assert plan_row == [{"publication_status": "PUBLISHED"}]

    recovery_plan, _ = _unique_plan()
    await store.register_ibkr_historical_plan(
        recovery_plan,
        plan_bytes=None,
        registered_at=_NOW,
    )
    recovery_request = recovery_plan.requests[0]
    started = await store.start_ibkr_historical_attempt(
        plan_sha256=recovery_plan.plan_sha256,
        request_sha256=recovery_request.request_sha256,
        connection_session_id=_SESSION_ID,
        connection_generation=7,
        provider_request_id=700,
        started_at=_NOW,
        maximum_attempts=2,
    )
    assert started is not None
    recovered = await store.recover_ibkr_historical_execution(
        plan_sha256=recovery_plan.plan_sha256,
        recovered_at=_NOW,
        maximum_attempts=2,
    )
    assert len(recovered) == 1
    assert recovered[0].attempt.status is IbkrAttemptStatus.INVALIDATED
    assert recovered[0].request_status is IbkrRequestStatus.PENDING

    stale = await store.start_ibkr_historical_attempt(
        plan_sha256=recovery_plan.plan_sha256,
        request_sha256=recovery_request.request_sha256,
        connection_session_id=_SESSION_ID,
        connection_generation=8,
        provider_request_id=800,
        started_at=_NOW,
        maximum_attempts=2,
    )
    assert stale is not None
    await store.append_ibkr_historical_callback(
        attempt_id=stale.attempt_id,
        callback=IbkrHistoricalCallback(
            connection_session_id=_SESSION_ID,
            provider_request_id=800,
            connection_generation=9,
            kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
            received_at=_NOW,
            payload={"close": "1"},
        ),
    )
    await store.append_ibkr_historical_callback(
        attempt_id=stale.attempt_id,
        callback=IbkrHistoricalCallback(
            connection_session_id=_SESSION_ID,
            provider_request_id=800,
            connection_generation=9,
            kind=IbkrHistoricalCallbackKind.COMPLETION,
            received_at=_NOW,
            payload={},
        ),
    )
    with pytest.raises(IbkrHistoricalIncomplete):
        await store.finalize_ibkr_historical_attempt(
            attempt_id=stale.attempt_id,
            completed_at=_NOW,
        )
    stale_outcome = await store.fail_ibkr_historical_attempt(
        attempt_id=stale.attempt_id,
        failed_at=_NOW,
        disposition=IbkrTerminalDisposition.SESSION_EVIDENCE_UNAVAILABLE.value,
        detail="stale generation completion was not eligible",
        retryable=False,
        maximum_attempts=2,
    )
    assert stale_outcome.request_status is IbkrRequestStatus.TERMINAL
    assert stale_outcome.disposition is IbkrTerminalDisposition.SESSION_EVIDENCE_UNAVAILABLE

    stale_rows = await audit_store.query(
        """
        SELECT closure_eligible
        FROM ops.ibkr_historical_callbacks
        WHERE attempt_id = :attempt_id
        ORDER BY sequence
        """,
        {"attempt_id": stale.attempt_id},
    )
    assert stale_rows == [{"closure_eligible": False}, {"closure_eligible": False}]
    await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("QTRAD_TEST_DATABASE_URL"),
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_postgres_execution_snapshot_rejects_request_closure_mutations() -> None:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    assert database_url is not None
    engine = create_async_engine(database_url)
    audit_store = PostgresAuditStore(engine)
    await audit_store.seed_instruments()
    store = PostgresIbkrHistoricalExecutionStore(engine)

    try:
        for mutation in ("deleted", "altered", "extra", "terminal"):
            plan, profile = _unique_plan()
            await store.register_ibkr_historical_plan(
                plan,
                plan_bytes=ibkr_historical_plan_bytes(plan),
                registered_at=_NOW,
            )
            async with engine.begin() as connection:
                if mutation == "deleted":
                    await connection.execute(
                        text(
                            """
                            DELETE FROM ops.ibkr_historical_requests
                            WHERE plan_sha256 = :plan_sha256
                              AND request_sha256 = :request_sha256
                            """
                        ),
                        {
                            "plan_sha256": plan.plan_sha256,
                            "request_sha256": plan.requests[0].request_sha256,
                        },
                    )
                elif mutation == "altered":
                    await connection.execute(
                        text(
                            """
                            UPDATE ops.ibkr_historical_requests
                            SET request_payload = request_payload || '{"duration":"2 D"}'::jsonb
                            WHERE plan_sha256 = :plan_sha256
                              AND request_sha256 = :request_sha256
                            """
                        ),
                        {
                            "plan_sha256": plan.plan_sha256,
                            "request_sha256": plan.requests[0].request_sha256,
                        },
                    )
                elif mutation == "extra":
                    await connection.execute(
                        text(
                            """
                            INSERT INTO ops.ibkr_historical_requests (
                                plan_sha256, request_sha256, request_payload, instrument_id,
                                request_kind, interval_start, interval_end, status,
                                attempt_count, publication_status
                            )
                            SELECT plan_sha256, :extra_request_sha256, request_payload,
                                   instrument_id, request_kind, interval_start, interval_end,
                                   'PENDING', 0, 'PENDING'
                            FROM ops.ibkr_historical_requests
                            WHERE plan_sha256 = :plan_sha256
                            ORDER BY request_sha256
                            LIMIT 1
                            """
                        ),
                        {
                            "plan_sha256": plan.plan_sha256,
                            "extra_request_sha256": "f" * 64,
                        },
                    )
                else:
                    await connection.execute(
                        text(
                            """
                            UPDATE ops.ibkr_historical_requests
                            SET status = 'TERMINAL', selected_attempt_id = NULL
                            WHERE plan_sha256 = :plan_sha256
                              AND request_sha256 = :request_sha256
                            """
                        ),
                        {
                            "plan_sha256": plan.plan_sha256,
                            "request_sha256": plan.requests[0].request_sha256,
                        },
                    )

            snapshot = await store.read_ibkr_historical_execution(plan_sha256=plan.plan_sha256)
            match = {
                "deleted": "request closure",
                "altered": "payload or canonical",
                "extra": "request closure",
                "terminal": "terminal IBKR request",
            }[mutation]
            with pytest.raises(ValueError, match=match):
                verify_ibkr_historical_execution_snapshot(
                    plan,
                    snapshot,
                    maximum_attempts=profile.retry_count + 1,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_generation_wrong_provider_request_id_cannot_close_attempt() -> None:
    store = MemoryExecutionStore()
    plan, _ = _plan()
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)
    request = next(
        request
        for request in plan.requests
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    attempt = await store.start_ibkr_historical_attempt(
        plan_sha256=plan.plan_sha256,
        request_sha256=request.request_sha256,
        connection_session_id=_SESSION_ID,
        connection_generation=1,
        provider_request_id=101,
        started_at=_NOW,
        maximum_attempts=2,
    )
    assert attempt is not None

    record = await store.append_ibkr_historical_callback(
        attempt_id=attempt.attempt_id,
        callback=IbkrHistoricalCallback(
            connection_session_id=_SESSION_ID,
            provider_request_id=102,
            connection_generation=1,
            kind=IbkrHistoricalCallbackKind.COMPLETION,
            received_at=_NOW,
            payload={},
        ),
    )

    assert record.provider_request_id == 102
    assert not record.closure_eligible
    with pytest.raises(IbkrHistoricalIncomplete, match="no eligible marker"):
        await store.finalize_ibkr_historical_attempt(
            attempt_id=attempt.attempt_id,
            completed_at=_NOW,
        )


@pytest.mark.asyncio
async def test_schedule_completion_without_schedule_callback_is_incomplete() -> None:
    store = MemoryExecutionStore()
    plan, _ = _plan()
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)
    request = next(
        request for request in plan.requests if request.kind is IbkrHistoricalRequestKind.SCHEDULE
    )
    attempt = await store.start_ibkr_historical_attempt(
        plan_sha256=plan.plan_sha256,
        request_sha256=request.request_sha256,
        connection_session_id=_SESSION_ID,
        connection_generation=1,
        provider_request_id=201,
        started_at=_NOW,
        maximum_attempts=2,
    )
    assert attempt is not None

    await store.append_ibkr_historical_callback(
        attempt_id=attempt.attempt_id,
        callback=IbkrHistoricalCallback(
            connection_session_id=_SESSION_ID,
            provider_request_id=201,
            connection_generation=1,
            kind=IbkrHistoricalCallbackKind.COMPLETION,
            received_at=_NOW,
            payload={},
        ),
    )

    assert store.markers[attempt.attempt_id][0].raw_schedule_callback_count == 0
    with pytest.raises(IbkrHistoricalIncomplete, match="no raw schedule callback"):
        await store.finalize_ibkr_historical_attempt(
            attempt_id=attempt.attempt_id,
            completed_at=_NOW,
        )


@pytest.mark.asyncio
async def test_disconnect_cleanup_disconnects_after_invalidation_failure() -> None:
    store = MemoryExecutionStore()
    store.fail_invalidation = True
    plan, profile = _plan()
    provider = FakeHistoricalDataPort(
        {
            IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["disconnect"],
            IbkrHistoricalRequestKind.SCHEDULE.value: ["success"],
        }
    )
    await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)

    with pytest.raises(RuntimeError, match="injected invalidation failure"):
        await IbkrHistoricalExecutor(
            store,
            provider,
            FakePacer(profile.profile_sha256),
            clock=lambda: _NOW,
        ).execute(plan, profile)

    assert provider.disconnect_count == 1


@pytest.mark.asyncio
async def test_terminal_requests_are_publishable_after_execution() -> None:
    cases = (
        (
            {
                IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["success"],
                IbkrHistoricalRequestKind.SCHEDULE.value: ["terminal"],
            },
            IbkrTerminalDisposition.ENTITLEMENT_UNAVAILABLE,
        ),
        (
            {
                IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["success"],
                IbkrHistoricalRequestKind.SCHEDULE.value: ["timeout", "timeout"],
            },
            IbkrTerminalDisposition.RETRY_LIMIT_EXHAUSTED,
        ),
    )

    for behaviours, expected_disposition in cases:
        store = MemoryExecutionStore()
        plan, profile = _unique_plan()
        provider = FakeHistoricalDataPort(behaviours)
        await store.register_ibkr_historical_plan(plan, plan_bytes=None, registered_at=_NOW)

        summary = await IbkrHistoricalExecutor(
            store,
            provider,
            FakePacer(profile.profile_sha256),
            clock=lambda: _NOW,
        ).execute(plan, profile)

        assert expected_disposition in {outcome.disposition for outcome in summary.outcomes}
        assert all(
            outcome.request_status in {IbkrRequestStatus.SUCCEEDED, IbkrRequestStatus.TERMINAL}
            for outcome in summary.outcomes
        )
        for index, request in enumerate(plan.requests, start=1):
            await store.mark_ibkr_historical_request_published(
                plan_sha256=plan.plan_sha256,
                request_sha256=request.request_sha256,
                result_sha256=f"{index:064x}",
                published_at=_NOW,
            )
        assert store.plan_publication_status[plan.plan_sha256] is IbkrPublicationStatus.PUBLISHED


@pytest.mark.skipif(
    not os.getenv("QTRAD_TEST_DATABASE_URL"),
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_postgres_store_publishes_terminal_attempts() -> None:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    assert database_url is not None
    engine = create_async_engine(database_url)
    try:
        audit_store = PostgresAuditStore(engine)
        await audit_store.seed_instruments()
        store = PostgresIbkrHistoricalExecutionStore(engine)
        cases = (
            (
                {
                    IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["success"],
                    IbkrHistoricalRequestKind.SCHEDULE.value: ["terminal"],
                },
                IbkrTerminalDisposition.ENTITLEMENT_UNAVAILABLE,
            ),
            (
                {
                    IbkrHistoricalRequestKind.MIDPOINT_BARS.value: ["success"],
                    IbkrHistoricalRequestKind.SCHEDULE.value: ["timeout", "timeout"],
                },
                IbkrTerminalDisposition.RETRY_LIMIT_EXHAUSTED,
            ),
        )

        for case_index, (behaviours, expected_disposition) in enumerate(cases, start=1):
            plan, profile = _unique_plan()
            await store.register_ibkr_historical_plan(
                plan,
                plan_bytes=ibkr_historical_plan_bytes(plan),
                registered_at=_NOW,
            )
            summary = await IbkrHistoricalExecutor(
                store,
                FakeHistoricalDataPort(behaviours),
                FakePacer(profile.profile_sha256),
                clock=lambda: _NOW,
                request_id_start=case_index * 100,
            ).execute(plan, profile)

            assert expected_disposition in {outcome.disposition for outcome in summary.outcomes}
            assert any(
                outcome.request_status is IbkrRequestStatus.TERMINAL for outcome in summary.outcomes
            )
            for index, request in enumerate(plan.requests, start=1):
                await store.mark_ibkr_historical_request_published(
                    plan_sha256=plan.plan_sha256,
                    request_sha256=request.request_sha256,
                    result_sha256=f"{index:064x}",
                    published_at=_NOW,
                )

            request_rows = await audit_store.query(
                """
                SELECT status, publication_status
                FROM ops.ibkr_historical_requests
                WHERE plan_sha256 = :plan_sha256
                """,
                {"plan_sha256": plan.plan_sha256},
            )
            assert all(row["status"] in {"SUCCEEDED", "TERMINAL"} for row in request_rows)
            assert all(row["publication_status"] == "PUBLISHED" for row in request_rows)
            plan_rows = await audit_store.query(
                """
                SELECT publication_status
                FROM ops.ibkr_historical_plans
                WHERE plan_sha256 = :plan_sha256
                """,
                {"plan_sha256": plan.plan_sha256},
            )
            assert plan_rows == [{"publication_status": "PUBLISHED"}]
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("QTRAD_TEST_DATABASE_URL"),
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_postgres_fresh_session_namespaces_restarted_request_ids() -> None:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    assert database_url is not None
    engine = create_async_engine(database_url)
    try:
        audit_store = PostgresAuditStore(engine)
        await audit_store.seed_instruments()
        store = PostgresIbkrHistoricalExecutionStore(engine)
        plan, profile = _unique_plan()
        await store.register_ibkr_historical_plan(
            plan,
            plan_bytes=ibkr_historical_plan_bytes(plan),
            registered_at=_NOW,
        )

        first_provider = FakeHistoricalDataPort(
            {kind.value: [] for kind in IbkrHistoricalRequestKind}
        )
        first_connection = await first_provider.connect()
        started = await store.start_ibkr_historical_attempt(
            plan_sha256=plan.plan_sha256,
            request_sha256=plan.requests[0].request_sha256,
            connection_session_id=first_connection.connection_session_id,
            connection_generation=first_connection.connection_generation,
            provider_request_id=1,
            started_at=_NOW,
            maximum_attempts=2,
        )
        assert started is not None
        await first_provider.disconnect()

        second_provider = FakeHistoricalDataPort(
            {kind.value: ["success"] for kind in IbkrHistoricalRequestKind}
        )
        summary = await IbkrHistoricalExecutor(
            store,
            second_provider,
            FakePacer(profile.profile_sha256),
            clock=lambda: _NOW,
        ).execute(plan, profile)

        assert second_provider.connect_count == 1
        assert summary.connection_generation == 1
        assert sum(
            outcome.request_status is IbkrRequestStatus.SUCCEEDED for outcome in summary.outcomes
        ) == len(plan.requests)
        attempt_rows = await audit_store.query(
            """
            SELECT connection_session_id, connection_generation, provider_request_id
            FROM ops.ibkr_historical_attempts
            WHERE plan_sha256 = :plan_sha256
            ORDER BY attempt_id
            """,
            {"plan_sha256": plan.plan_sha256},
        )
        assert len(attempt_rows) == len(plan.requests) + 1
        assert {int(row["connection_generation"]) for row in attempt_rows} == {1}
        assert sum(int(row["provider_request_id"]) == 1 for row in attempt_rows) == 2
        assert len({str(row["connection_session_id"]) for row in attempt_rows}) == 2
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("QTRAD_TEST_DATABASE_URL"),
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_postgres_selected_attempt_cannot_be_mutated_across_requests() -> None:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    assert database_url is not None
    engine = create_async_engine(database_url)
    try:
        audit_store = PostgresAuditStore(engine)
        await audit_store.seed_instruments()
        store = PostgresIbkrHistoricalExecutionStore(engine)
        plan, profile = _unique_plan()
        await store.register_ibkr_historical_plan(
            plan,
            plan_bytes=ibkr_historical_plan_bytes(plan),
            registered_at=_NOW,
        )
        await IbkrHistoricalExecutor(
            store,
            FakeHistoricalDataPort({kind.value: ["success"] for kind in IbkrHistoricalRequestKind}),
            FakePacer(profile.profile_sha256),
            clock=lambda: _NOW,
        ).execute(plan, profile)
        request_rows = await audit_store.query(
            """
            SELECT request_sha256, selected_attempt_id
            FROM ops.ibkr_historical_requests
            WHERE plan_sha256 = :plan_sha256
            ORDER BY request_sha256
            """,
            {"plan_sha256": plan.plan_sha256},
        )
        assert len(request_rows) == 2
        assert all(row["selected_attempt_id"] is not None for row in request_rows)
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE ops.ibkr_historical_requests
                        SET selected_attempt_id = :selected_attempt_id
                        WHERE plan_sha256 = :plan_sha256
                          AND request_sha256 = :request_sha256
                        """
                    ),
                    {
                        "selected_attempt_id": request_rows[1]["selected_attempt_id"],
                        "plan_sha256": plan.plan_sha256,
                        "request_sha256": request_rows[0]["request_sha256"],
                    },
                )
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("QTRAD_TEST_DATABASE_URL"),
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_postgres_result_snapshot_is_repeatable_read_under_mutation() -> None:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    assert database_url is not None
    engine = create_async_engine(database_url)
    try:
        audit_store = PostgresAuditStore(engine)
        await audit_store.seed_instruments()
        store = PostgresIbkrHistoricalExecutionStore(engine)
        plan, profile = _unique_plan()
        await store.register_ibkr_historical_plan(
            plan,
            plan_bytes=ibkr_historical_plan_bytes(plan),
            registered_at=_NOW,
        )
        await IbkrHistoricalExecutor(
            store,
            FakeHistoricalDataPort({kind.value: ["success"] for kind in IbkrHistoricalRequestKind}),
            FakePacer(profile.profile_sha256),
            clock=lambda: _NOW,
        ).execute(plan, profile)

        snapshot_started = threading.Event()
        mutation_done = threading.Event()

        def pause_after_plan_snapshot(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            if (
                "SELECT PLAN_SHA256, PLAN_BYTES" not in statement.upper()
                or snapshot_started.is_set()
            ):
                return
            snapshot_started.set()
            if not mutation_done.wait(timeout=30):
                raise RuntimeError("concurrent publication mutation did not complete")

        sync_database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

        def mutate_publication_state() -> None:
            try:
                with (
                    psycopg.connect(sync_database_url, autocommit=True) as connection,
                    connection.cursor() as cursor,
                ):
                    if not snapshot_started.wait(timeout=30):
                        raise RuntimeError("result snapshot did not start")
                    cursor.execute(
                        """
                            UPDATE ops.ibkr_historical_requests
                            SET publication_status = %s, result_sha256 = %s, published_at = %s
                            WHERE plan_sha256 = %s
                            """,
                        ("PUBLISHED", "a" * 64, _NOW, plan.plan_sha256),
                    )
            finally:
                mutation_done.set()

        event.listen(engine.sync_engine, "after_cursor_execute", pause_after_plan_snapshot)
        mutation_task = asyncio.create_task(asyncio.to_thread(mutate_publication_state))
        try:
            snapshot = await store.read_ibkr_historical_execution(plan_sha256=plan.plan_sha256)
        finally:
            event.remove(engine.sync_engine, "after_cursor_execute", pause_after_plan_snapshot)
            await mutation_task

        assert all(
            request.publication_status is IbkrPublicationStatus.PENDING
            for request in snapshot.requests
        )
        current_rows = await audit_store.query(
            """
            SELECT publication_status
            FROM ops.ibkr_historical_requests
            WHERE plan_sha256 = :plan_sha256
            """,
            {"plan_sha256": plan.plan_sha256},
        )
        assert all(row["publication_status"] == "PUBLISHED" for row in current_rows)
    finally:
        await engine.dispose()
