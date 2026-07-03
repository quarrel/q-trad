"""Operational domain values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from qtrad.domain.identifiers import RunId
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.domain.time import require_utc


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
