#!/usr/bin/env python
"""Run a bounded callback-to-PostgreSQL streaming load experiment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from qtrad.adapters.clock import SystemClock
from qtrad.adapters.ig.market_data import (
    IgDemoConfig,
    IgDemoMarketDataAdapter,
    _ConnectionState,
    _IgRestService,
)
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.application.ingestion import IngestionService
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import AssetClass, Instrument, ProductType, ProviderListing
from qtrad.domain.operations import HealthStatus

_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class _Arguments:
    output: Path
    duration_seconds: float
    callbacks_per_second: float
    instruments: int
    queue_capacity: int
    persistence_delay_ms: float
    renewal_at_seconds: float | None
    maximum_persistence_lag_seconds: float
    drain_timeout_seconds: float


class _SyntheticUpdate:
    def __init__(self, values: dict[str, object | None], changed: set[str]) -> None:
        self._values = values
        self._changed = changed

    @staticmethod
    def _field_name(field: str | int) -> str:
        if isinstance(field, int):
            return (
                "TIMESTAMP",
                "BIDPRICE1",
                "ASKPRICE1",
                "BIDSIZE1",
                "ASKSIZE1",
                "DLG_FLAG",
                "DELAY",
            )[field - 1]
        return field

    def getValue(self, field: str | int) -> object | None:
        return self._values.get(self._field_name(field))

    def isValueChanged(self, field: str | int) -> bool:
        return self._field_name(field) in self._changed


def _parse_args() -> _Arguments:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration-seconds", type=float, default=300)
    parser.add_argument("--callbacks-per-second", type=float, default=200)
    parser.add_argument("--instruments", type=int, default=40)
    parser.add_argument("--queue-capacity", type=int, default=10_000)
    parser.add_argument("--persistence-delay-ms", type=float, default=1)
    parser.add_argument("--renewal-at-seconds", type=float)
    parser.add_argument("--maximum-persistence-lag-seconds", type=float, default=30)
    parser.add_argument("--drain-timeout-seconds", type=float, default=300)
    values = parser.parse_args()
    arguments = _Arguments(
        output=values.output,
        duration_seconds=values.duration_seconds,
        callbacks_per_second=values.callbacks_per_second,
        instruments=values.instruments,
        queue_capacity=values.queue_capacity,
        persistence_delay_ms=values.persistence_delay_ms,
        renewal_at_seconds=values.renewal_at_seconds,
        maximum_persistence_lag_seconds=values.maximum_persistence_lag_seconds,
        drain_timeout_seconds=values.drain_timeout_seconds,
    )
    if arguments.duration_seconds <= 0:
        raise ValueError("duration must be positive")
    if arguments.callbacks_per_second <= 0:
        raise ValueError("callback rate must be positive")
    if not 1 <= arguments.instruments <= 40:
        raise ValueError("instrument count must be between one and 40")
    if arguments.queue_capacity <= 0:
        raise ValueError("queue capacity must be positive")
    if arguments.persistence_delay_ms < 0:
        raise ValueError("persistence delay cannot be negative")
    if arguments.renewal_at_seconds is not None and not (
        0 < arguments.renewal_at_seconds < arguments.duration_seconds
    ):
        raise ValueError("renewal time must fall inside the experiment interval")
    if arguments.maximum_persistence_lag_seconds <= 0:
        raise ValueError("maximum persistence lag must be positive")
    if arguments.drain_timeout_seconds <= 0:
        raise ValueError("drain timeout must be positive")
    return arguments


def _database_url() -> tuple[str, str]:
    value = os.environ["QTRAD_TEST_DATABASE_URL"]
    parsed = make_url(value)
    if parsed.host not in {"test-db", "127.0.0.1", "localhost"}:
        raise RuntimeError(f"refusing non-local experiment database host: {parsed.host}")
    database = parsed.database
    if database is None or not database.startswith("qtrad_test_"):
        raise RuntimeError("experiment database name must begin qtrad_test_")
    return value, database


def _config(queue_capacity: int) -> IgDemoConfig:
    return IgDemoConfig(
        username="synthetic",
        password="synthetic-not-a-provider-secret",
        api_key="synthetic-not-a-provider-key",
        account_id="synthetic",
        queue_capacity=queue_capacity,
    )


def _listings(count: int, observed_at: datetime) -> tuple[ProviderListing, ...]:
    return tuple(
        ProviderListing(
            listing_id=ProviderListingId("synthetic", "local", f"SYNTHETIC.{index:02d}"),
            instrument_id=InstrumentId(f"synthetic:instrument-{index:02d}"),
            display_name=f"Synthetic instrument {index:02d}",
            product_type=ProductType.ROLLING_CFD,
            currency="USD",
            minimum_deal_size=Decimal("1"),
            price_increment=Decimal("0.00001"),
            valid_from=observed_at,
            valid_to=None,
            metadata_version="stream-load-v1",
        )
        for index in range(count)
    )


def _instruments(listings: tuple[ProviderListing, ...]) -> tuple[Instrument, ...]:
    return tuple(
        Instrument(
            instrument_id=listing.instrument_id,
            display_name=listing.display_name,
            asset_class=AssetClass.INDEX,
            base_currency=None,
            quote_currency=listing.currency,
            search_aliases=(listing.listing_id.external_id,),
        )
        for listing in listings
    )


def _price(points: int) -> str:
    return f"{100 + points // 100_000}.{points % 100_000:05d}"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without observations")
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_evidence(path: Path, payload: dict[str, object]) -> None:
    payload["evidence_sha256"] = _sha256_json(payload)
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(encoded)


async def _run(arguments: _Arguments) -> dict[str, object]:
    database_url, expected_database = _database_url()
    engine = create_async_engine(database_url)
    store = PostgresAuditStore(engine)
    clock = SystemClock()
    adapter = IgDemoMarketDataAdapter(_config(arguments.queue_capacity), clock)
    adapter._loop = asyncio.get_running_loop()
    adapter._service = cast(_IgRestService, cast(Any, object()))
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY
    adapter._transport_connected = True
    listings = _listings(arguments.instruments, clock.now())
    await store.seed_instruments(_instruments(listings))
    adapter._listings_by_epic = {listing.listing_id.external_id: listing for listing in listings}
    service = IngestionService(store, producer="stream-load-experiment", producer_version="1")
    target_callbacks = max(1, round(arguments.duration_seconds * arguments.callbacks_per_second))
    callback_interval = 1 / arguments.callbacks_per_second
    start_epoch_ms = int(clock.now().timestamp() * 1_000)
    renewal_sequence = (
        round(arguments.renewal_at_seconds * arguments.callbacks_per_second)
        if arguments.renewal_at_seconds is not None
        else None
    )
    persistence_lags: list[float] = []
    process_durations: list[float] = []
    full_state_required: set[str] = set()
    processed = 0
    normalised = 0
    quarantined = 0
    normalisation_errors: dict[str, int] = {}

    async def consume() -> None:
        nonlocal normalised, processed, quarantined
        async for record in adapter.records():
            if record.quote is None:
                quarantined += 1
                error_code = record.error_code or "MISSING_ERROR_CODE"
                normalisation_errors[error_code] = normalisation_errors.get(error_code, 0) + 1
            else:
                normalised += 1
            if arguments.persistence_delay_ms:
                await asyncio.sleep(arguments.persistence_delay_ms / 1_000)
            process_started = time.perf_counter()
            await service.process(record)
            await service.advance_bars(record.received_time)
            process_durations.append(time.perf_counter() - process_started)
            persistence_lags.append((clock.now() - record.received_time).total_seconds())
            processed += 1

    consumer = asyncio.create_task(consume())

    def produce() -> None:
        started = time.perf_counter()
        for sequence in range(target_callbacks):
            deadline = started + sequence * callback_interval
            remaining = deadline - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            listing = listings[sequence % len(listings)]
            if sequence == renewal_sequence:
                full_state_required.update(item.listing_id.external_id for item in listings)
                for item in listings:
                    adapter._on_subscription(item.listing_id.external_id, generation=0)
            round_index = sequence // len(listings)
            timestamp = str(start_epoch_ms + round_index * max(1, round(1_000 / 5)))
            bid_points = round_index % 1_000
            values: dict[str, object | None] = {
                "TIMESTAMP": timestamp,
                "BIDPRICE1": _price(bid_points),
                "ASKPRICE1": _price(bid_points + 2),
            }
            changed = {"TIMESTAMP", "BIDPRICE1", "ASKPRICE1"}
            if round_index == 0 or listing.listing_id.external_id in full_state_required:
                values.update(
                    {
                        "DLG_FLAG": "DEAL",
                        "DELAY": "0",
                    }
                )
                changed.update({"DLG_FLAG", "DELAY"})
                full_state_required.discard(listing.listing_id.external_id)
            adapter._on_update(
                listing.listing_id.external_id,
                _SyntheticUpdate(values, changed),
            )

    started_at = clock.now()
    monotonic_started = time.perf_counter()
    try:
        await asyncio.to_thread(produce)
        accepted = target_callbacks - adapter._dropped_records

        async def wait_for_drain() -> None:
            while processed < accepted:
                if consumer.done():
                    consumer.result()
                await asyncio.sleep(0.01)

        async with asyncio.timeout(arguments.drain_timeout_seconds):
            await wait_for_drain()
        adapter._stopping = True
        async with asyncio.timeout(2):
            await consumer
        finished_at = clock.now()
        elapsed = time.perf_counter() - monotonic_started
        health = await adapter.health()
        async with engine.connect() as connection:
            database = str(
                (await connection.execute(text("SELECT current_database()"))).scalar_one()
            )
            raw_count = int(
                (
                    await connection.execute(text("SELECT count(*) FROM raw.market_messages"))
                ).scalar_one()
            )
            quote_count = int(
                (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM canonical.events "
                            "WHERE event_type = 'MarketQuoteObserved'"
                        )
                    )
                ).scalar_one()
            )
        if database != expected_database:
            raise RuntimeError("experiment database identity changed during the run")
        maximum_lag = max(persistence_lags, default=float("inf"))
        checks = {
            "database_is_disposable": database.startswith("qtrad_test_"),
            "all_callbacks_persisted": processed == target_callbacks == raw_count,
            "all_callbacks_normalised": normalised == target_callbacks == quote_count
            and quarantined == 0,
            "zero_qtrad_drops": adapter._dropped_records == 0,
            "zero_lightstreamer_reported_loss": adapter._lightstreamer_lost_updates == 0,
            "queue_remained_bounded": adapter._queue_high_water <= arguments.queue_capacity,
            "persistence_lag_within_limit": maximum_lag
            <= arguments.maximum_persistence_lag_seconds,
            "consumer_exited": consumer.done() and consumer.exception() is None,
            "renewal_reestablished_complete_state": arguments.renewal_at_seconds is None
            or (adapter._subscription_events == arguments.instruments and not full_state_required),
        }
        return {
            "schema_version": _SCHEMA_VERSION,
            "experiment": "CALLBACK_TO_POSTGRES_LOAD",
            "started_at": _utc_text(started_at),
            "finished_at": _utc_text(finished_at),
            "database_name": database,
            "parameters": {
                "duration_seconds": arguments.duration_seconds,
                "callbacks_per_second": arguments.callbacks_per_second,
                "instruments": arguments.instruments,
                "queue_capacity": arguments.queue_capacity,
                "persistence_delay_ms": arguments.persistence_delay_ms,
                "renewal_at_seconds": arguments.renewal_at_seconds,
                "maximum_persistence_lag_seconds": arguments.maximum_persistence_lag_seconds,
                "drain_timeout_seconds": arguments.drain_timeout_seconds,
            },
            "measurements": {
                "target_callbacks": target_callbacks,
                "processed_callbacks": processed,
                "normalised_callbacks": normalised,
                "quarantined_callbacks": quarantined,
                "normalisation_errors": normalisation_errors,
                "raw_messages": raw_count,
                "quote_events": quote_count,
                "elapsed_seconds": elapsed,
                "effective_persistence_rate_per_second": processed / elapsed,
                "queue_high_water": adapter._queue_high_water,
                "qtrad_dropped_records": adapter._dropped_records,
                "lightstreamer_reported_lost_updates": adapter._lightstreamer_lost_updates,
                "subscription_renewal_events": adapter._subscription_events,
                "persistence_lag_p50_seconds": _percentile(persistence_lags, 0.50),
                "persistence_lag_p95_seconds": _percentile(persistence_lags, 0.95),
                "persistence_lag_p99_seconds": _percentile(persistence_lags, 0.99),
                "persistence_lag_max_seconds": maximum_lag,
                "process_duration_p95_seconds": _percentile(process_durations, 0.95),
                "adapter_health": health.status.value,
                "adapter_health_detail": health.detail,
            },
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
        }
    finally:
        adapter._stopping = True
        if not consumer.done():
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
        await engine.dispose()


def main() -> None:
    arguments = _parse_args()
    evidence = asyncio.run(_run(arguments))
    _write_evidence(arguments.output, evidence)
    print(f"{evidence['result']} {evidence['evidence_sha256']} {arguments.output}")
    if evidence["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
