"""Restart-safe application executor for IBKR historical requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from itertools import count
from uuid import UUID

from qtrad.domain.ibkr_execution import (
    IbkrHistoricalAttemptOutcome,
    IbkrHistoricalCallback,
    IbkrHistoricalDisconnected,
    IbkrHistoricalExecutionSummary,
    IbkrHistoricalIncomplete,
    IbkrHistoricalRetryableError,
    IbkrHistoricalTerminalError,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import (
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
    IbkrHistoricalRequestProfile,
)
from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_historical import (
    IbkrHistoricalDataPort,
    IbkrHistoricalExecutionStore,
    IbkrHistoricalPacer,
)


class IbkrHistoricalExecutor:
    """Execute a registered plan without retaining correctness-critical progress in memory."""

    def __init__(
        self,
        store: IbkrHistoricalExecutionStore,
        provider: IbkrHistoricalDataPort,
        pacer: IbkrHistoricalPacer,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        request_id_start: int = 1,
    ) -> None:
        if request_id_start <= 0:
            raise ValueError("IBKR request ID start must be positive")
        self._store = store
        self._provider = provider
        self._pacer = pacer
        self._clock = clock
        self._sleep = sleep
        self._request_ids = count(request_id_start)

    async def execute(
        self,
        plan: IbkrHistoricalPlan,
        request_profile: IbkrHistoricalRequestProfile,
    ) -> IbkrHistoricalExecutionSummary:
        """Recover durable work, then execute every still-pending planned request."""

        if plan.request_profile_sha256 != request_profile.profile_sha256:
            raise ValueError("IBKR execution profile does not match the registered plan")
        if self._pacer.request_profile_sha256 != request_profile.profile_sha256:
            raise ValueError("IBKR pacing profile does not match the execution profile")
        maximum_attempts = request_profile.retry_count + 1
        recovered_at = self._now("IBKR execution recovery time")
        recovered = tuple(
            await self._store.recover_ibkr_historical_execution(
                plan_sha256=plan.plan_sha256,
                recovered_at=recovered_at,
                maximum_attempts=maximum_attempts,
            )
        )
        request_by_hash = {request.request_sha256: request for request in plan.requests}
        pending_hashes = await self._store.pending_ibkr_historical_requests(plan.plan_sha256)
        if not pending_hashes:
            return IbkrHistoricalExecutionSummary(plan.plan_sha256, recovered, None)
        unknown = set(pending_hashes) - set(request_by_hash)
        if unknown:
            raise RuntimeError("durable IBKR state contains a request outside the registered plan")

        generation = await self._provider.connect()
        if generation <= 0:
            raise RuntimeError(
                "IBKR historical provider returned a non-positive connection generation"
            )
        outcomes: list[IbkrHistoricalAttemptOutcome] = list(recovered)
        try:
            semaphore = asyncio.Semaphore(request_profile.maximum_in_flight_requests)

            async def run(request: IbkrHistoricalRequest) -> None:
                async with semaphore:
                    outcome = await self._execute_request(
                        plan=plan,
                        request=request,
                        request_profile=request_profile,
                        connection_generation=generation,
                    )
                    if outcome is not None:
                        outcomes.append(outcome)

            async with asyncio.TaskGroup() as group:
                for request_hash in pending_hashes:
                    request = request_by_hash[request_hash]
                    group.create_task(run(request))
        finally:
            try:
                await self._store.invalidate_ibkr_historical_attempts(
                    plan_sha256=plan.plan_sha256,
                    connection_generation=generation,
                    invalidated_at=self._now("IBKR execution disconnect time"),
                    maximum_attempts=maximum_attempts,
                )
            finally:
                await self._provider.disconnect()

        return IbkrHistoricalExecutionSummary(plan.plan_sha256, tuple(outcomes), generation)

    async def _execute_request(
        self,
        *,
        plan: IbkrHistoricalPlan,
        request: IbkrHistoricalRequest,
        request_profile: IbkrHistoricalRequestProfile,
        connection_generation: int,
    ) -> IbkrHistoricalAttemptOutcome | None:
        maximum_attempts = request_profile.retry_count + 1
        while True:
            while True:
                delay = await self._pacer.reserve(
                    "historical",
                    str(request.fingerprint.con_id),
                    request.request_sha256,
                    1,
                    request_profile_sha256=request_profile.profile_sha256,
                    pacing_policy=request_profile.pacing_policy,
                )
                if delay == 0:
                    break
                await self._sleep(delay)

            attempt = await self._store.start_ibkr_historical_attempt(
                plan_sha256=plan.plan_sha256,
                request_sha256=request.request_sha256,
                connection_generation=connection_generation,
                provider_request_id=next(self._request_ids),
                started_at=self._now("IBKR attempt start time"),
                maximum_attempts=maximum_attempts,
            )
            if attempt is None:
                return None

            callback_sink = partial(self._append_callback, attempt.attempt_id)

            try:
                await asyncio.wait_for(
                    self._provider.request_historical(
                        request,
                        request_id=attempt.provider_request_id,
                        connection_generation=connection_generation,
                        callback=callback_sink,
                    ),
                    timeout=request_profile.request_timeout_seconds,
                )
            except IbkrHistoricalDisconnected:
                await self._store.invalidate_ibkr_historical_attempts(
                    plan_sha256=plan.plan_sha256,
                    connection_generation=connection_generation,
                    invalidated_at=self._now("IBKR disconnect time"),
                    maximum_attempts=maximum_attempts,
                )
                raise
            except IbkrHistoricalTerminalError as error:
                outcome = await self._store.fail_ibkr_historical_attempt(
                    attempt_id=attempt.attempt_id,
                    failed_at=self._now("IBKR terminal failure time"),
                    disposition=error.disposition.value,
                    detail=error.detail,
                    retryable=False,
                    maximum_attempts=maximum_attempts,
                )
            except (IbkrHistoricalRetryableError, TimeoutError, ConnectionError) as error:
                detail = str(error) or type(error).__name__
                outcome = await self._store.fail_ibkr_historical_attempt(
                    attempt_id=attempt.attempt_id,
                    failed_at=self._now("IBKR retryable failure time"),
                    disposition=IbkrTerminalDisposition.PROVIDER_REJECTED.value,
                    detail=detail[:2_000],
                    retryable=True,
                    maximum_attempts=maximum_attempts,
                )
            else:
                try:
                    outcome = await self._store.finalize_ibkr_historical_attempt(
                        attempt_id=attempt.attempt_id,
                        completed_at=self._now("IBKR completion time"),
                    )
                except IbkrHistoricalIncomplete:
                    disposition = (
                        IbkrTerminalDisposition.SESSION_EVIDENCE_UNAVAILABLE
                        if request.kind is IbkrHistoricalRequestKind.SCHEDULE
                        else IbkrTerminalDisposition.INCOMPLETE_RESPONSE
                    )
                    outcome = await self._store.fail_ibkr_historical_attempt(
                        attempt_id=attempt.attempt_id,
                        failed_at=self._now("IBKR incomplete-response time"),
                        disposition=disposition.value,
                        detail="provider call completed without a valid completion marker",
                        retryable=False,
                        maximum_attempts=maximum_attempts,
                    )

            if outcome.request_status is not IbkrRequestStatus.PENDING:
                return outcome

    async def _append_callback(
        self,
        attempt_id: UUID,
        callback: IbkrHistoricalCallback,
    ) -> None:
        await self._store.append_ibkr_historical_callback(
            attempt_id=attempt_id,
            callback=callback,
        )

    def _now(self, label: str) -> datetime:
        value = self._clock()
        require_utc(value, label)
        return value.astimezone(UTC)
