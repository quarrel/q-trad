"""Immutable Stage 6 IBKR historical result evidence contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.ibkr_execution import (
    IbkrAttemptStatus,
    IbkrHistoricalCallbackKind,
    IbkrPublicationStatus,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import (
    HISTORICAL_PLAN_CONTRACT,
    IbkrHistoricalPlan,
    sha256_json,
    utc_text,
)
from qtrad.domain.time import require_utc

REQUEST_RESULT_CONTRACT = "qtrad-ibkr-historical-request-result-v2"
HISTORICAL_RESULT_CONTRACT = "qtrad-ibkr-historical-result-v3"
HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT = "qtrad-ibkr-historical-result-verification-v1"
RESULT_SCHEMA_VERSION = 3
REQUEST_RESULT_SCHEMA_VERSION = 2
MAX_IBKR_RESULT_BYTES = 8 * 1024 * 1024
MAX_IBKR_RESULT_REQUEST_BYTES = 32 * 1024 * 1024
MAX_IBKR_RESULT_CHILDREN = 20_000
MAX_IBKR_RESULT_ATTEMPTS = 20_000
MAX_IBKR_RESULT_CALLBACKS = 100_000
MAX_IBKR_RESULT_COMPLETION_MARKERS = 20_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class IbkrHistoricalEvidenceDisposition(StrEnum):
    """Evidence-level outcome derived independently from raw callback closure."""

    SUCCEEDED = "SUCCEEDED"
    CONTRACT_IDENTITY_CHANGED = "CONTRACT_IDENTITY_CHANGED"
    ENTITLEMENT_UNAVAILABLE = "ENTITLEMENT_UNAVAILABLE"
    NO_DATA_RETURNED = "NO_DATA_RETURNED"
    INVALID_REQUEST = "INVALID_REQUEST"
    RETRY_LIMIT_EXHAUSTED = "RETRY_LIMIT_EXHAUSTED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    SESSION_EVIDENCE_UNAVAILABLE = "SESSION_EVIDENCE_UNAVAILABLE"
    INCOMPLETE_RESPONSE = "INCOMPLETE_RESPONSE"
    INVALID_CALLBACK_EVIDENCE = "INVALID_CALLBACK_EVIDENCE"
    CONFLICTING_CALLBACK_EVIDENCE = "CONFLICTING_CALLBACK_EVIDENCE"


@dataclass(frozen=True, slots=True)
class IbkrHistoricalPlanSnapshot:
    """Exact registered plan records read by the Stage 4 publisher."""

    plan_sha256: str
    plan_bytes: bytes
    plan_bytes_sha256: str
    plan_payload: dict[str, JsonValue]
    registered_at: datetime
    publication_status: IbkrPublicationStatus

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "IBKR snapshot plan hash")
        _require_sha256(self.plan_bytes_sha256, "IBKR snapshot plan-bytes hash")
        require_utc(self.registered_at, "IBKR snapshot plan registered_at")
        if not self.plan_bytes:
            raise ValueError("IBKR snapshot plan bytes cannot be empty")
        if sha256_bytes(self.plan_bytes) != self.plan_bytes_sha256:
            raise ValueError("IBKR snapshot plan-bytes hash does not match its bytes")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalRequestSnapshot:
    """Exact durable request state copied into a request-result closure."""

    plan_sha256: str
    request_sha256: str
    request_payload: dict[str, JsonValue]
    instrument_id: str
    request_kind: str
    interval_start: datetime
    interval_end: datetime
    status: IbkrRequestStatus
    attempt_count: int
    selected_attempt_id: UUID | None
    publication_status: IbkrPublicationStatus
    result_sha256: str | None
    published_at: datetime | None

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "IBKR snapshot request plan hash")
        _require_sha256(self.request_sha256, "IBKR snapshot request hash")
        if not self.instrument_id or not self.request_kind:
            raise ValueError("IBKR snapshot request identity is required")
        require_utc(self.interval_start, "IBKR snapshot request interval_start")
        require_utc(self.interval_end, "IBKR snapshot request interval_end")
        if self.interval_end <= self.interval_start:
            raise ValueError("IBKR snapshot request interval must be non-empty")
        if self.attempt_count < 0:
            raise ValueError("IBKR snapshot request attempt_count cannot be negative")
        if self.result_sha256 is not None:
            _require_sha256(self.result_sha256, "IBKR snapshot request result hash")
        if self.published_at is not None:
            require_utc(self.published_at, "IBKR snapshot request published_at")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalAttemptEvidence:
    """Append-only attempt evidence copied from the execution state machine."""

    attempt_id: UUID
    plan_sha256: str
    request_sha256: str
    attempt_ordinal: int
    connection_session_id: UUID
    provider_request_id: int
    connection_generation: int
    started_at: datetime
    status: IbkrAttemptStatus
    terminal_at: datetime | None
    terminal_disposition: IbkrTerminalDisposition | None
    detail: str | None

    def __post_init__(self) -> None:
        if self.attempt_id.int == 0 or self.connection_session_id.int == 0:
            raise ValueError("IBKR attempt evidence UUIDs must be non-zero")
        _require_sha256(self.plan_sha256, "IBKR attempt evidence plan hash")
        _require_sha256(self.request_sha256, "IBKR attempt evidence request hash")
        if self.attempt_ordinal <= 0:
            raise ValueError("IBKR attempt evidence ordinal must be positive")
        if self.provider_request_id <= 0 or self.connection_generation <= 0:
            raise ValueError("IBKR attempt evidence provider identity must be positive")
        require_utc(self.started_at, "IBKR attempt evidence started_at")
        if self.terminal_at is not None:
            require_utc(self.terminal_at, "IBKR attempt evidence terminal_at")
            if self.terminal_at < self.started_at:
                raise ValueError("IBKR attempt evidence terminal_at precedes started_at")
        if self.detail is not None and (not self.detail or len(self.detail) > 2_000):
            raise ValueError("IBKR attempt evidence detail is unbounded")
        if self.status is IbkrAttemptStatus.STARTED:
            if self.terminal_at is not None or self.terminal_disposition is not None:
                raise ValueError("started attempt evidence cannot have terminal fields")
        elif self.terminal_at is None:
            raise ValueError("finished attempt evidence requires terminal_at")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "attempt_id": str(self.attempt_id),
            "plan_sha256": self.plan_sha256,
            "request_sha256": self.request_sha256,
            "attempt_ordinal": self.attempt_ordinal,
            "connection_session_id": str(self.connection_session_id),
            "provider_request_id": self.provider_request_id,
            "connection_generation": self.connection_generation,
            "started_at": utc_text(self.started_at),
            "status": self.status.value,
            "terminal_at": None if self.terminal_at is None else utc_text(self.terminal_at),
            "terminal_disposition": (
                None if self.terminal_disposition is None else self.terminal_disposition.value
            ),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCallbackEvidence:
    """Raw callback evidence, including callbacks fenced from accepted output."""

    callback_id: int
    attempt_id: UUID
    connection_session_id: UUID
    provider_request_id: int
    connection_generation: int
    sequence: int
    kind: IbkrHistoricalCallbackKind
    received_at: datetime
    payload: dict[str, JsonValue]
    closure_eligible: bool

    def __post_init__(self) -> None:
        if self.callback_id <= 0 or self.attempt_id.int == 0:
            raise ValueError("IBKR callback evidence identity must be positive")
        if self.connection_session_id.int == 0:
            raise ValueError("IBKR callback evidence session ID must be non-zero")
        if self.provider_request_id <= 0 or self.connection_generation <= 0 or self.sequence <= 0:
            raise ValueError("IBKR callback evidence transport identity must be positive")
        require_utc(self.received_at, "IBKR callback evidence received_at")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "callback_id": self.callback_id,
            "attempt_id": str(self.attempt_id),
            "connection_session_id": str(self.connection_session_id),
            "provider_request_id": self.provider_request_id,
            "connection_generation": self.connection_generation,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "received_at": utc_text(self.received_at),
            "payload": _json_object(self.payload, "callback payload"),
            "closure_eligible": self.closure_eligible,
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCompletionEvidence:
    """Durable completion marker copied from the execution state machine."""

    marker_id: int
    attempt_id: UUID
    connection_session_id: UUID
    provider_request_id: int
    connection_generation: int
    sequence: int
    completed_at: datetime
    raw_midpoint_bar_callback_count: int
    raw_schedule_callback_count: int
    closure_eligible: bool
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if self.marker_id <= 0 or self.attempt_id.int == 0:
            raise ValueError("IBKR completion evidence identity must be positive")
        if self.connection_session_id.int == 0:
            raise ValueError("IBKR completion evidence session ID must be non-zero")
        if self.provider_request_id <= 0 or self.connection_generation <= 0 or self.sequence <= 0:
            raise ValueError("IBKR completion evidence transport identity must be positive")
        require_utc(self.completed_at, "IBKR completion evidence completed_at")
        if self.raw_midpoint_bar_callback_count < 0 or self.raw_schedule_callback_count < 0:
            raise ValueError("IBKR completion evidence callback counts cannot be negative")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "marker_id": self.marker_id,
            "attempt_id": str(self.attempt_id),
            "connection_session_id": str(self.connection_session_id),
            "provider_request_id": self.provider_request_id,
            "connection_generation": self.connection_generation,
            "sequence": self.sequence,
            "completed_at": utc_text(self.completed_at),
            "raw_midpoint_bar_callback_count": self.raw_midpoint_bar_callback_count,
            "raw_schedule_callback_count": self.raw_schedule_callback_count,
            "closure_eligible": self.closure_eligible,
            "payload": _json_object(self.payload, "completion payload"),
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalExecutionSnapshot:
    """Complete bounded database snapshot consumed by the Stage 4 publisher."""

    plan: IbkrHistoricalPlanSnapshot
    requests: tuple[IbkrHistoricalRequestSnapshot, ...]
    attempts: tuple[IbkrHistoricalAttemptEvidence, ...]
    callbacks: tuple[IbkrHistoricalCallbackEvidence, ...]
    completion_markers: tuple[IbkrHistoricalCompletionEvidence, ...]

    def __post_init__(self) -> None:
        request_keys = {(item.plan_sha256, item.request_sha256) for item in self.requests}
        if len(request_keys) != len(self.requests):
            raise ValueError("IBKR execution snapshot request identities must be unique")
        attempt_ids = {item.attempt_id for item in self.attempts}
        if len(attempt_ids) != len(self.attempts):
            raise ValueError("IBKR execution snapshot attempt identities must be unique")
        callback_ids = {item.callback_id for item in self.callbacks}
        if len(callback_ids) != len(self.callbacks):
            raise ValueError("IBKR execution snapshot callback identities must be unique")
        marker_ids = {item.marker_id for item in self.completion_markers}
        if len(marker_ids) != len(self.completion_markers):
            raise ValueError("IBKR execution snapshot completion identities must be unique")
        if any(item.plan_sha256 != self.plan.plan_sha256 for item in self.requests):
            raise ValueError("IBKR execution snapshot contains a request from another plan")
        if any(item.plan_sha256 != self.plan.plan_sha256 for item in self.attempts):
            raise ValueError("IBKR execution snapshot contains an attempt from another plan")
        request_set = {item.request_sha256 for item in self.requests}
        if any(item.request_sha256 not in request_set for item in self.attempts):
            raise ValueError("IBKR execution snapshot contains an orphan attempt")
        attempt_set = {item.attempt_id for item in self.attempts}
        if any(item.attempt_id not in attempt_set for item in self.callbacks):
            raise ValueError("IBKR execution snapshot contains an orphan callback")
        if any(item.attempt_id not in attempt_set for item in self.completion_markers):
            raise ValueError("IBKR execution snapshot contains an orphan completion marker")


@dataclass(frozen=True, slots=True)
class IbkrHistoricalChildReference:
    """A safe relative path bound to semantic identity and exact bytes."""

    path: str
    contract: str
    semantic_sha256: str
    bytes_sha256: str

    def __post_init__(self) -> None:
        _require_safe_relative_path(self.path, "IBKR result child path")
        if not self.contract:
            raise ValueError("IBKR result child contract is required")
        _require_sha256(self.semantic_sha256, "IBKR result child semantic hash")
        _require_sha256(self.bytes_sha256, "IBKR result child bytes hash")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "path": self.path,
            "contract": self.contract,
            "semantic_sha256": self.semantic_sha256,
            "bytes_sha256": self.bytes_sha256,
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalRequestResult:
    """Independently verifiable result for one planned request."""

    plan_sha256: str
    request_sha256: str
    request_payload: dict[str, JsonValue]
    request_status: IbkrRequestStatus
    terminal_disposition: IbkrTerminalDisposition
    evidence_disposition: IbkrHistoricalEvidenceDisposition
    selected_attempt_id: UUID
    attempts: tuple[IbkrHistoricalAttemptEvidence, ...]
    callbacks: tuple[IbkrHistoricalCallbackEvidence, ...]
    completion_markers: tuple[IbkrHistoricalCompletionEvidence, ...]
    accepted_rows: tuple[dict[str, JsonValue], ...]
    sessions: tuple[dict[str, JsonValue], ...]
    session_state: str | None
    acquisition_started_at: datetime
    acquisition_completed_at: datetime
    retry_history: tuple[dict[str, JsonValue], ...]
    error_classification: dict[str, JsonValue] | None
    result_sha256: str

    CONTRACT = REQUEST_RESULT_CONTRACT
    SCHEMA_VERSION = REQUEST_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "IBKR request result plan hash")
        _require_sha256(self.request_sha256, "IBKR request result request hash")
        if self.request_status not in {IbkrRequestStatus.SUCCEEDED, IbkrRequestStatus.TERMINAL}:
            raise ValueError("IBKR request result must be terminal")
        if self.request_status is IbkrRequestStatus.SUCCEEDED:
            if self.terminal_disposition is not IbkrTerminalDisposition.SUCCEEDED:
                raise ValueError("successful IBKR request result requires SUCCEEDED disposition")
        elif self.terminal_disposition is IbkrTerminalDisposition.SUCCEEDED:
            raise ValueError("terminal IBKR request result cannot have SUCCEEDED disposition")
        if (
            self.request_status is IbkrRequestStatus.TERMINAL
            and self.evidence_disposition is IbkrHistoricalEvidenceDisposition.SUCCEEDED
        ):
            raise ValueError("terminal IBKR request result cannot have successful evidence")
        if self.selected_attempt_id.int == 0:
            raise ValueError("IBKR request result selected attempt must be non-zero")
        if not self.attempts:
            raise ValueError("IBKR request result requires attempt evidence")
        if len(self.attempts) > MAX_IBKR_RESULT_ATTEMPTS:
            raise ValueError("IBKR request result attempts exceed their bound")
        if len(self.callbacks) > MAX_IBKR_RESULT_CALLBACKS:
            raise ValueError("IBKR request result callbacks exceed their bound")
        if len(self.completion_markers) > MAX_IBKR_RESULT_COMPLETION_MARKERS:
            raise ValueError("IBKR request result completion markers exceed their bound")
        if any(item.plan_sha256 != self.plan_sha256 for item in self.attempts):
            raise ValueError("IBKR request result attempt plan identity differs")
        if any(item.request_sha256 != self.request_sha256 for item in self.attempts):
            raise ValueError("IBKR request result attempt request identity differs")
        if self.selected_attempt_id not in {item.attempt_id for item in self.attempts}:
            raise ValueError("IBKR request result selected attempt is absent")
        if self.session_state not in {None, "ACTIVE", "INACTIVE", "UNKNOWN"}:
            raise ValueError("IBKR request result session state is unsupported")
        require_utc(self.acquisition_started_at, "IBKR request result acquisition start")
        require_utc(self.acquisition_completed_at, "IBKR request result acquisition completion")
        if self.acquisition_completed_at < self.acquisition_started_at:
            raise ValueError("IBKR request result acquisition completion precedes start")
        _require_sha256(self.result_sha256, "IBKR request result hash")
        if self.result_sha256 != sha256_json(self.identity_payload()):
            raise ValueError("IBKR request result hash does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "plan_sha256": self.plan_sha256,
            "request_sha256": self.request_sha256,
            "request_payload": _json_object(self.request_payload, "request payload"),
            "request_status": self.request_status.value,
            "terminal_disposition": self.terminal_disposition.value,
            "evidence_disposition": self.evidence_disposition.value,
            "selected_attempt_id": str(self.selected_attempt_id),
            "attempts": [item.as_json_value() for item in self.attempts],
            "callbacks": [item.as_json_value() for item in self.callbacks],
            "completion_markers": [item.as_json_value() for item in self.completion_markers],
            "accepted_rows": [_json_object(item, "accepted row") for item in self.accepted_rows],
            "sessions": [_json_object(item, "session") for item in self.sessions],
            "session_state": self.session_state,
            "acquisition_started_at": utc_text(self.acquisition_started_at),
            "acquisition_completed_at": utc_text(self.acquisition_completed_at),
            "retry_history": [_json_object(item, "retry history") for item in self.retry_history],
            "error_classification": (
                None
                if self.error_classification is None
                else _json_object(self.error_classification, "error classification")
            ),
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "result_sha256": self.result_sha256}


@dataclass(frozen=True, slots=True)
class IbkrHistoricalAggregateResult:
    """Stage 6 aggregate with separate semantic and physical identities."""

    plan: IbkrHistoricalChildReference
    runtime_sha256: str
    request_results: tuple[IbkrHistoricalChildReference, ...]
    coverage_summary: dict[str, JsonValue]
    entitlement_summary: dict[str, JsonValue]
    result_id: str
    closure_id: str
    publication_status: str = "PUBLISHED_UNVERIFIED"

    CONTRACT = HISTORICAL_RESULT_CONTRACT
    SCHEMA_VERSION = RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_sha256(self.runtime_sha256, "IBKR aggregate runtime hash")
        if self.plan.contract != HISTORICAL_PLAN_CONTRACT:
            raise ValueError("IBKR aggregate plan child has an unsupported contract")
        if not self.request_results:
            raise ValueError("IBKR aggregate result requires request-result children")
        if len(self.request_results) > MAX_IBKR_RESULT_CHILDREN:
            raise ValueError("IBKR aggregate result children exceed their bound")
        if len({item.path for item in self.request_results}) != len(self.request_results):
            raise ValueError("IBKR aggregate request-result child paths must be unique")
        if len({item.semantic_sha256 for item in self.request_results}) != len(
            self.request_results
        ):
            raise ValueError("IBKR aggregate request-result child identities must be unique")
        if self.publication_status != "PUBLISHED_UNVERIFIED":
            raise ValueError("IBKR aggregate publication status is unsupported")
        _require_sha256(self.result_id, "IBKR aggregate result identity")
        if self.result_id != sha256_json(self.semantic_identity_payload()):
            raise ValueError("IBKR aggregate result identity does not match semantic content")
        _require_sha256(self.closure_id, "IBKR aggregate closure identity")
        if self.closure_id != sha256_json(self.closure_identity_payload()):
            raise ValueError("IBKR aggregate closure identity does not match physical content")

    def semantic_identity_payload(self) -> dict[str, JsonValue]:
        """Return only scientific Stage 6 meaning, excluding physical references."""
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "plan_semantic_id": self.plan.semantic_sha256,
            "request_result_semantic_ids": [item.semantic_sha256 for item in self.request_results],
            "coverage_summary": _json_object(self.coverage_summary, "coverage summary"),
            "entitlement_summary": _json_object(self.entitlement_summary, "entitlement summary"),
        }

    def closure_identity_payload(self) -> dict[str, JsonValue]:
        """Return exact manifest metadata and declared child bytes."""
        return {
            **self.semantic_identity_payload(),
            "result_id": self.result_id,
            "plan": self.plan.as_json_value(),
            "runtime_sha256": self.runtime_sha256,
            "request_results": [item.as_json_value() for item in self.request_results],
            "publication_status": self.publication_status,
        }

    def identity_payload(self) -> dict[str, JsonValue]:
        """Return canonical manifest content without the compatibility alias."""
        return {**self.closure_identity_payload(), "closure_id": self.closure_id}

    def as_json_value(self) -> dict[str, JsonValue]:
        return self.identity_payload()


@dataclass(frozen=True, slots=True)
class IbkrHistoricalResultVerificationReceipt:
    """Small create-only proof for one immutable Stage 6 closure."""

    result_id: str
    closure_id: str
    result_contract: str
    result_schema_version: int
    manifest_sha256: str
    plan_semantic_id: str
    verifier_contract: str
    verifier_version: str
    completed_checks: tuple[str, ...]
    verifier_identity: str
    verification_id: str

    CONTRACT = HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT

    def __post_init__(self) -> None:
        if (
            self.result_contract != HISTORICAL_RESULT_CONTRACT
            or self.result_schema_version != RESULT_SCHEMA_VERSION
        ):
            raise ValueError("IBKR result receipt Stage 6 contract or schema is unsupported")
        for value, field in (
            (self.result_id, "receipt result identity"),
            (self.closure_id, "receipt closure identity"),
            (self.manifest_sha256, "receipt manifest hash"),
            (self.plan_semantic_id, "receipt plan identity"),
            (self.verifier_identity, "receipt verifier identity"),
            (self.verification_id, "receipt identity"),
        ):
            _require_sha256(value, field)
        if self.verifier_contract != self.CONTRACT or not self.verifier_version:
            raise ValueError("IBKR result receipt verifier contract is unsupported")
        if not self.completed_checks or len(set(self.completed_checks)) != len(
            self.completed_checks
        ):
            raise ValueError("IBKR result receipt completed checks are invalid")
        if self.verifier_identity != sha256_json(self.verifier_identity_payload()):
            raise ValueError("IBKR result receipt verifier identity changed")
        if self.verification_id != sha256_json(self.identity_payload()):
            raise ValueError("IBKR result receipt identity changed")

    def verifier_identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.verifier_contract,
            "version": self.verifier_version,
            "completed_checks": list(self.completed_checks),
        }

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "result_contract": self.result_contract,
            "result_schema_version": self.result_schema_version,
            "result_id": self.result_id,
            "closure_id": self.closure_id,
            "manifest_sha256": self.manifest_sha256,
            "plan_semantic_id": self.plan_semantic_id,
            "verifier_contract": self.verifier_contract,
            "verifier_version": self.verifier_version,
            "completed_checks": list(self.completed_checks),
            "verifier_identity": self.verifier_identity,
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "verification_id": self.verification_id}


@dataclass(frozen=True, slots=True)
class IbkrHistoricalResultArtifact:
    """In-memory publication bundle before create-only file publication."""

    plan: IbkrHistoricalPlan
    plan_bytes: bytes
    request_results: tuple[IbkrHistoricalRequestResult, ...]
    aggregate: IbkrHistoricalAggregateResult

    def __post_init__(self) -> None:
        if not self.plan_bytes:
            raise ValueError("IBKR result artifact plan bytes cannot be empty")
        request_hashes = {item.request_sha256 for item in self.request_results}
        expected_hashes = {item.request_sha256 for item in self.plan.requests}
        if request_hashes != expected_hashes:
            raise ValueError("IBKR result artifact request closure differs from its plan")
        if self.aggregate.runtime_sha256 != self.plan.runtime_sha256:
            raise ValueError("IBKR result artifact runtime identity differs from its plan")


def sha256_bytes(value: bytes) -> str:
    """Hash exact evidence bytes at the file boundary."""

    if not value:
        raise ValueError("IBKR evidence bytes cannot be empty")
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Mapping[str, JsonValue]) -> bytes:
    """Encode bounded evidence with the repository's canonical JSON formatting."""

    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _json_object(value: Mapping[str, JsonValue], field: str) -> dict[str, JsonValue]:
    encoded = to_json_value(value)
    if not isinstance(encoded, dict):
        raise TypeError(f"{field} must serialise to an object")
    return encoded


def _require_safe_relative_path(value: str, field: str) -> None:
    parsed = PurePosixPath(value)
    if (
        not value
        or parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or len(value) > 240
    ):
        raise ValueError(f"{field} must be a safe relative path")


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


__all__ = [
    "HISTORICAL_RESULT_CONTRACT",
    "HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT",
    "MAX_IBKR_RESULT_ATTEMPTS",
    "MAX_IBKR_RESULT_BYTES",
    "MAX_IBKR_RESULT_CALLBACKS",
    "MAX_IBKR_RESULT_CHILDREN",
    "MAX_IBKR_RESULT_COMPLETION_MARKERS",
    "MAX_IBKR_RESULT_REQUEST_BYTES",
    "REQUEST_RESULT_CONTRACT",
    "REQUEST_RESULT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "IbkrHistoricalAggregateResult",
    "IbkrHistoricalAttemptEvidence",
    "IbkrHistoricalCallbackEvidence",
    "IbkrHistoricalChildReference",
    "IbkrHistoricalCompletionEvidence",
    "IbkrHistoricalEvidenceDisposition",
    "IbkrHistoricalExecutionSnapshot",
    "IbkrHistoricalPlanSnapshot",
    "IbkrHistoricalRequestResult",
    "IbkrHistoricalRequestSnapshot",
    "IbkrHistoricalResultArtifact",
    "IbkrHistoricalResultVerificationReceipt",
    "canonical_json_bytes",
    "sha256_bytes",
]
