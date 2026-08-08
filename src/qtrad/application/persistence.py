"""Bounded raw/canonical persistence for continuous capture."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from qtrad.domain.operations import AdapterHealth, HealthStatus
from qtrad.ports.capture_feed import CaptureIdentity
from qtrad.ports.market_data import MarketDataRecord


@dataclass(frozen=True, slots=True)
class PersistenceSnapshot:
    records_received: int
    persisted: int
    failed: int
    dropped: int
    queue_depth: int
    queue_capacity: int
    last_persisted_at: datetime | None
    last_error: str | None


class RecordPersistence(Protocol):
    async def process(self, record: MarketDataRecord) -> object: ...


class PersistenceWorkerError(RuntimeError):
    """The native capture cannot continue without durable persistence."""


class PersistenceBackpressureError(PersistenceWorkerError):
    """The bounded persistence queue could not accept an offered record."""


class BoundedPersistenceWorker:
    """Serialise the provider-neutral ingestion service behind a bounded queue."""

    def __init__(self, service: RecordPersistence, *, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("persistence queue capacity must be positive")
        self._service = service
        self._queue: asyncio.Queue[MarketDataRecord] = asyncio.Queue(maxsize=capacity)
        self._capacity = capacity
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._records_received = 0
        self._persisted = 0
        self._failed = 0
        self._dropped = 0
        self._last_persisted_at: datetime | None = None
        self._last_error: str | None = None
        self._terminal_error: PersistenceWorkerError | None = None
        self._failure_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("persistence worker already started")
        self._task = asyncio.create_task(self._run(), name="qtrad-native-capture-persistence")

    def submit_nowait(self, record: MarketDataRecord) -> bool:
        if self._task is None or self._stopping:
            raise RuntimeError("persistence worker is not accepting records")
        if self._terminal_error is not None:
            raise self._terminal_error
        self._records_received += 1
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull as error:
            self._dropped += 1
            terminal = PersistenceBackpressureError(
                "native capture persistence queue is full; capture must stop"
            )
            self._set_terminal_error(terminal)
            raise terminal from error
        return True

    async def wait_for_failure(self) -> None:
        await self._failure_event.wait()
        if self._terminal_error is not None:
            raise self._terminal_error

    async def drain_and_stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        if self._terminal_error is None:
            join_task = asyncio.create_task(self._queue.join())
            failure_task = asyncio.create_task(self._failure_event.wait())
            done, _ = await asyncio.wait(
                (join_task, failure_task), return_when=asyncio.FIRST_COMPLETED
            )
            if self._terminal_error is not None or failure_task in done:
                self._discard_pending()
            else:
                await join_task
            for task in (join_task, failure_task):
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        else:
            self._discard_pending()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stopping or not self._queue.empty():
            if self._terminal_error is not None:
                self._discard_pending()
                return
            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except TimeoutError:
                continue
            try:
                await self._service.process(record)
            except Exception as error:
                self._failed += 1
                self._last_error = type(error).__name__
                self._set_terminal_error(
                    PersistenceWorkerError(
                        f"native capture persistence failed: {type(error).__name__}"
                    )
                )
                self._queue.task_done()
                return
            else:
                self._persisted += 1
                self._last_persisted_at = datetime.now(UTC)
                self._queue.task_done()

    def _set_terminal_error(self, error: PersistenceWorkerError) -> None:
        if self._terminal_error is None:
            self._terminal_error = error
            self._last_error = type(error).__name__
            self._failure_event.set()

    def _discard_pending(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            else:
                self._dropped += 1
                self._queue.task_done()

    def snapshot(self) -> PersistenceSnapshot:
        return PersistenceSnapshot(
            records_received=self._records_received,
            persisted=self._persisted,
            failed=self._failed,
            dropped=self._dropped,
            queue_depth=self._queue.qsize(),
            queue_capacity=self._capacity,
            last_persisted_at=self._last_persisted_at,
            last_error=self._last_error,
        )

    def compose_health(
        self,
        health: AdapterHealth,
        *,
        identity: CaptureIdentity | None = None,
        capture_session_id: str | None = None,
    ) -> AdapterHealth:
        snapshot = self.snapshot()
        reasons = set(health.reason_codes)
        if snapshot.failed:
            reasons.add("PERSISTENCE_FAILURE")
        if snapshot.dropped:
            reasons.add("PERSISTENCE_RECORDS_DROPPED")
        if snapshot.failed or snapshot.dropped:
            status = (
                HealthStatus.DISCONNECTED
                if health.status is HealthStatus.DISCONNECTED
                else HealthStatus.DEGRADED
            )
        else:
            status = health.status
        attributes = dict(health.attributes)
        for key, value in (
            ("records_received", str(snapshot.records_received)),
            ("persisted", str(snapshot.persisted)),
            ("failed", str(snapshot.failed)),
            ("dropped", str(snapshot.dropped)),
            ("queue_depth", str(snapshot.queue_depth)),
            ("queue_capacity", str(snapshot.queue_capacity)),
        ):
            attributes[key] = value
        if snapshot.last_persisted_at is not None:
            attributes["last_persisted_at"] = snapshot.last_persisted_at.isoformat()
        if snapshot.last_error is not None:
            attributes["last_persistence_error"] = snapshot.last_error
        if identity is not None:
            attributes.update(
                {
                    "provider": identity.provider,
                    "source_class": identity.source_class.value,
                    "capture_source_id": identity.capture_source_id,
                    "universe_id": identity.universe_id,
                    "configuration_hash": identity.configuration_hash,
                }
            )
        if capture_session_id is not None:
            attributes["capture_session_id"] = capture_session_id
        return replace(
            health,
            status=status,
            reason_codes=tuple(sorted(reasons)),
            attributes=tuple(attributes.items()),
        )
