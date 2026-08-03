"""Ports for transport-independent IBKR historical execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrad.domain.ibkr_execution import (
    IbkrHistoricalAttempt,
    IbkrHistoricalAttemptOutcome,
    IbkrHistoricalCallback,
    IbkrHistoricalCallbackRecord,
    IbkrHistoricalConnection,
    IbkrPlanRegistrationStatus,
)
from qtrad.domain.ibkr_historical import (
    IbkrHistoricalPacingPolicy,
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
)

IbkrHistoricalCallbackSink = Callable[[IbkrHistoricalCallback], Awaitable[None]]


class IbkrHistoricalDataPort(Protocol):
    """Market-data-only provider boundary; no order operation is available."""

    async def connect(self) -> IbkrHistoricalConnection: ...

    async def disconnect(self) -> None: ...

    async def request_historical(
        self,
        request: IbkrHistoricalRequest,
        *,
        request_id: int,
        connection_session_id: UUID,
        connection_generation: int,
        callback: IbkrHistoricalCallbackSink,
    ) -> None: ...


class IbkrHistoricalPacer(Protocol):
    """Durable pacing reservation boundary bound to the authenticated request profile."""

    @property
    def request_profile_sha256(self) -> str: ...

    async def reserve(
        self,
        request_kind: str,
        contract_key: str,
        request_fingerprint: str,
        weight: int,
        *,
        request_profile_sha256: str,
        pacing_policy: IbkrHistoricalPacingPolicy,
    ) -> float: ...


class IbkrHistoricalExecutionStore(Protocol):
    """Durable state boundary consumed by the application executor."""

    async def register_ibkr_historical_plan(
        self,
        plan: IbkrHistoricalPlan,
        *,
        plan_bytes: bytes | None,
        registered_at: datetime,
    ) -> IbkrPlanRegistrationStatus: ...

    async def recover_ibkr_historical_execution(
        self,
        *,
        plan_sha256: str,
        recovered_at: datetime,
        maximum_attempts: int,
    ) -> Sequence[IbkrHistoricalAttemptOutcome]: ...

    async def pending_ibkr_historical_requests(self, plan_sha256: str) -> tuple[str, ...]: ...

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
    ) -> IbkrHistoricalAttempt | None: ...

    async def append_ibkr_historical_callback(
        self,
        *,
        attempt_id: UUID,
        callback: IbkrHistoricalCallback,
    ) -> IbkrHistoricalCallbackRecord: ...

    async def finalize_ibkr_historical_attempt(
        self,
        *,
        attempt_id: UUID,
        completed_at: datetime,
    ) -> IbkrHistoricalAttemptOutcome: ...

    async def fail_ibkr_historical_attempt(
        self,
        *,
        attempt_id: UUID,
        failed_at: datetime,
        disposition: str,
        detail: str,
        retryable: bool,
        maximum_attempts: int,
    ) -> IbkrHistoricalAttemptOutcome: ...

    async def invalidate_ibkr_historical_attempts(
        self,
        *,
        plan_sha256: str,
        connection_session_id: UUID,
        connection_generation: int,
        invalidated_at: datetime,
        maximum_attempts: int,
    ) -> Sequence[IbkrHistoricalAttemptOutcome]: ...

    async def mark_ibkr_historical_request_published(
        self,
        *,
        plan_sha256: str,
        request_sha256: str,
        result_sha256: str,
        published_at: datetime,
    ) -> None: ...


__all__ = [
    "IbkrHistoricalCallbackSink",
    "IbkrHistoricalDataPort",
    "IbkrHistoricalExecutionStore",
    "IbkrHistoricalPacer",
]
