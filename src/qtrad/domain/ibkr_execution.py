"""Durable, transport-independent state for IBKR historical execution."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.ibkr_historical import IbkrHistoricalPlan
from qtrad.domain.time import require_utc

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_IBKR_PLAN_BYTES = 16 * 1024 * 1024


class IbkrHistoricalCallbackKind(StrEnum):
    """Provider-neutral callback categories retained by the execution state machine."""

    MIDPOINT_BAR = "MIDPOINT_BAR"
    SCHEDULE = "SCHEDULE"
    COMPLETION = "COMPLETION"
    ERROR = "ERROR"


class IbkrAttemptStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    INVALIDATED = "INVALIDATED"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


class IbkrRequestStatus(StrEnum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    TERMINAL = "TERMINAL"


class IbkrTerminalDisposition(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    CONTRACT_IDENTITY_CHANGED = "CONTRACT_IDENTITY_CHANGED"
    ENTITLEMENT_UNAVAILABLE = "ENTITLEMENT_UNAVAILABLE"
    NO_DATA_RETURNED = "NO_DATA_RETURNED"
    INVALID_REQUEST = "INVALID_REQUEST"
    RETRY_LIMIT_EXHAUSTED = "RETRY_LIMIT_EXHAUSTED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    SESSION_EVIDENCE_UNAVAILABLE = "SESSION_EVIDENCE_UNAVAILABLE"
    INCOMPLETE_RESPONSE = "INCOMPLETE_RESPONSE"


class IbkrPublicationStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"


class IbkrPlanRegistrationStatus(StrEnum):
    REGISTERED = "REGISTERED"
    ALREADY_REGISTERED = "ALREADY_REGISTERED"


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCallback:
    """One callback as received from a historical-data transport."""

    provider_request_id: int
    connection_generation: int
    kind: IbkrHistoricalCallbackKind
    received_at: datetime
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if self.provider_request_id <= 0:
            raise ValueError("IBKR callback provider request ID must be positive")
        if self.connection_generation <= 0:
            raise ValueError("IBKR callback connection generation must be positive")
        require_utc(self.received_at, "IBKR callback received_at")
        serialised = to_json_value(self.payload)
        if not isinstance(serialised, dict):
            raise TypeError("IBKR callback payload must serialise to an object")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalAttempt:
    """An append-only provider attempt created before provider I/O."""

    attempt_id: UUID
    plan_sha256: str
    request_sha256: str
    attempt_ordinal: int
    provider_request_id: int
    connection_generation: int
    started_at: datetime
    status: IbkrAttemptStatus
    terminal_at: datetime | None = None
    terminal_disposition: IbkrTerminalDisposition | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "IBKR attempt plan hash")
        _require_sha256(self.request_sha256, "IBKR attempt request hash")
        if self.attempt_ordinal <= 0:
            raise ValueError("IBKR attempt ordinal must be positive")
        if self.provider_request_id <= 0:
            raise ValueError("IBKR provider request ID must be positive")
        if self.connection_generation <= 0:
            raise ValueError("IBKR attempt connection generation must be positive")
        require_utc(self.started_at, "IBKR attempt started_at")
        if self.terminal_at is not None:
            require_utc(self.terminal_at, "IBKR attempt terminal_at")
            if self.terminal_at < self.started_at:
                raise ValueError("IBKR attempt terminal_at cannot precede started_at")
        if self.detail is not None and (not self.detail or len(self.detail) > 2_000):
            raise ValueError("IBKR attempt detail must be bounded when present")
        if self.status is IbkrAttemptStatus.STARTED:
            if self.terminal_at is not None or self.terminal_disposition is not None:
                raise ValueError("started IBKR attempts cannot have terminal fields")
        elif self.terminal_at is None:
            raise ValueError("finished IBKR attempts require terminal_at")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCallbackRecord:
    """A persisted callback with a state-machine-assigned monotonic sequence."""

    callback_id: int
    attempt_id: UUID
    provider_request_id: int
    connection_generation: int
    sequence: int
    kind: IbkrHistoricalCallbackKind
    received_at: datetime
    payload: Mapping[str, JsonValue]
    closure_eligible: bool

    def __post_init__(self) -> None:
        if self.callback_id <= 0:
            raise ValueError("IBKR callback ID must be positive")
        if self.provider_request_id <= 0:
            raise ValueError("IBKR callback provider request ID must be positive")
        if self.connection_generation <= 0:
            raise ValueError("IBKR callback connection generation must be positive")
        if self.sequence <= 0:
            raise ValueError("IBKR callback sequence must be positive")
        require_utc(self.received_at, "IBKR callback record received_at")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCompletionMarker:
    """The durable completion boundary for one callback closure."""

    marker_id: int
    attempt_id: UUID
    provider_request_id: int
    connection_generation: int
    sequence: int
    completed_at: datetime
    raw_midpoint_bar_callback_count: int
    raw_schedule_callback_count: int
    closure_eligible: bool
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if self.marker_id <= 0:
            raise ValueError("IBKR completion marker ID must be positive")
        if self.provider_request_id <= 0:
            raise ValueError("IBKR completion marker provider request ID must be positive")
        if self.connection_generation <= 0 or self.sequence <= 0:
            raise ValueError("IBKR completion marker identity must be positive")
        require_utc(self.completed_at, "IBKR completion marker completed_at")
        if self.raw_midpoint_bar_callback_count < 0 or self.raw_schedule_callback_count < 0:
            raise ValueError("IBKR completion marker counts cannot be negative")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalAttemptOutcome:
    """The state transition observed after an attempt is closed or recovered."""

    attempt: IbkrHistoricalAttempt
    request_status: IbkrRequestStatus
    disposition: IbkrTerminalDisposition | None

    def __post_init__(self) -> None:
        if self.request_status is IbkrRequestStatus.SUCCEEDED:
            if self.disposition is not IbkrTerminalDisposition.SUCCEEDED:
                raise ValueError("successful IBKR requests require SUCCEEDED disposition")
        elif self.request_status is IbkrRequestStatus.TERMINAL:
            if self.disposition in {None, IbkrTerminalDisposition.SUCCEEDED}:
                raise ValueError("terminal IBKR requests require a failure disposition")
        elif self.disposition is not None:
            raise ValueError("pending IBKR requests cannot have a terminal disposition")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalExecutionSummary:
    """Bounded result of one executor run."""

    plan_sha256: str
    outcomes: tuple[IbkrHistoricalAttemptOutcome, ...]
    connection_generation: int | None

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "IBKR execution summary plan hash")
        if self.connection_generation is not None and self.connection_generation <= 0:
            raise ValueError("IBKR execution summary connection generation must be positive")


class IbkrHistoricalRetryableError(RuntimeError):
    """A provider failure that may consume a frozen retry slot."""


class IbkrHistoricalDisconnected(IbkrHistoricalRetryableError):
    """The provider connection ended before the current attempt was closed."""


class IbkrHistoricalIncomplete(RuntimeError):
    """The provider call returned without a valid completion marker."""


class IbkrHistoricalTerminalError(RuntimeError):
    """A provider response is terminal under the frozen execution policy."""

    def __init__(self, disposition: IbkrTerminalDisposition, detail: str) -> None:
        if disposition in {
            IbkrTerminalDisposition.SUCCEEDED,
            IbkrTerminalDisposition.RETRY_LIMIT_EXHAUSTED,
        }:
            raise ValueError("terminal provider errors require a failure disposition")
        if not detail or len(detail) > 2_000:
            raise ValueError("terminal provider error detail must be bounded")
        self.disposition = disposition
        self.detail = detail
        super().__init__(detail)


def ibkr_historical_plan_bytes(plan: IbkrHistoricalPlan) -> bytes:
    """Encode the same bounded create-only bytes used by the artifact writer."""

    return (
        json.dumps(plan.as_json_value(), sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def ibkr_historical_plan_bytes_sha256(plan_bytes: bytes) -> str:
    if not plan_bytes:
        raise ValueError("IBKR historical plan bytes cannot be empty")
    if len(plan_bytes) > MAX_IBKR_PLAN_BYTES:
        raise ValueError("IBKR historical plan bytes exceed their bounded size")
    return sha256(plan_bytes).hexdigest()


def _require_sha256(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lower-case SHA-256")


def callback_payload(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Return a JSON-safe copy at the transport boundary."""

    serialised = to_json_value(value)
    if not isinstance(serialised, dict):
        raise TypeError("IBKR callback payload must serialise to an object")
    return serialised
