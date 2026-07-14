"""Operational domain values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from qtrad.domain.identifiers import RunId
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.domain.time import require_utc

RUN_RECONCILIATION_STATUS = "FAILED"
RUN_RECONCILIATION_REASON = "PRE_CANDIDATE_PROCESS_INTERRUPTED"
RUN_RECONCILIATION_FINISHED_AT_BASIS = "OPERATOR_ASSERTED_CUTOFF_UPPER_BOUND"


class HealthStatus(StrEnum):
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    adapter_name: str
    environment: BrokerEnvironment
    status: HealthStatus
    observed_at: datetime
    last_message_at: datetime | None
    detail: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.observed_at, "observed_at")
        if self.last_message_at is not None:
            require_utc(self.last_message_at, "last_message_at")


@dataclass(frozen=True, slots=True)
class DataRun:
    run_id: RunId
    kind: RunKind
    started_at: datetime
    finished_at: datetime | None
    configuration_hash: str

    def __post_init__(self) -> None:
        require_utc(self.started_at, "started_at")
        if self.finished_at is not None:
            require_utc(self.finished_at, "finished_at")


@dataclass(frozen=True, slots=True)
class RunReconciliationTarget:
    run_id: RunId
    started_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.started_at, "run reconciliation target started_at")


@dataclass(frozen=True, slots=True)
class RunReconciliationPlan:
    plan_hash: str
    created_at: datetime
    cutoff: datetime
    capture_source_id: str
    database_name: str
    universe_name: str
    configuration_hash: str
    application_version: str
    application_image: str
    environment: BrokerEnvironment
    terminal_status: str
    reason_code: str
    finished_at_basis: str
    targets: tuple[RunReconciliationTarget, ...]

    def __post_init__(self) -> None:
        require_utc(self.created_at, "run reconciliation created_at")
        require_utc(self.cutoff, "run reconciliation cutoff")
        if self.created_at < self.cutoff:
            raise ValueError("run reconciliation cannot be created before its cutoff")
        if not self.capture_source_id or len(self.capture_source_id) > 64:
            raise ValueError("run reconciliation capture source ID is invalid")
        if any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in self.capture_source_id
        ):
            raise ValueError("run reconciliation capture source ID is invalid")
        if not self.database_name or len(self.database_name) > 63:
            raise ValueError("run reconciliation database name is invalid")
        if not self.universe_name or len(self.universe_name) > 64:
            raise ValueError("run reconciliation universe name is invalid")
        if len(self.configuration_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.configuration_hash
        ):
            raise ValueError("run reconciliation configuration hash must be lower-case SHA-256")
        if not self.application_version or len(self.application_version) > 64:
            raise ValueError("run reconciliation application version is invalid")
        repository, separator, digest = self.application_image.rpartition("@sha256:")
        if (
            not repository
            or not separator
            or len(self.application_image) > 500
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("run reconciliation application image must be pinned by digest")
        if len(self.plan_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.plan_hash
        ):
            raise ValueError("run reconciliation plan hash must be lower-case SHA-256")
        if self.terminal_status != RUN_RECONCILIATION_STATUS:
            raise ValueError("run reconciliation terminal status is unsupported")
        if self.reason_code != RUN_RECONCILIATION_REASON:
            raise ValueError("run reconciliation reason code is unsupported")
        if self.finished_at_basis != RUN_RECONCILIATION_FINISHED_AT_BASIS:
            raise ValueError("run reconciliation terminal-time basis is unsupported")
        if not self.targets or len(self.targets) > 100:
            raise ValueError("run reconciliation requires between one and 100 targets")
        if len({target.run_id.value for target in self.targets}) != len(self.targets):
            raise ValueError("run reconciliation target IDs must be unique")
        if any(target.started_at >= self.cutoff for target in self.targets):
            raise ValueError("run reconciliation targets must start before the cutoff")
        ordered = tuple(sorted(self.targets, key=lambda item: (item.started_at, str(item.run_id))))
        if self.targets != ordered:
            raise ValueError("run reconciliation targets must use canonical order")
