#!/usr/bin/env python3
"""Exercise q-trad's IG disconnect and invalid-token recovery on one demo connection."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import queue
import re
import threading
from collections import Counter
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

from qtrad.adapters.clock import SystemClock
from qtrad.adapters.ig.market_data import IgDemoConfig, IgDemoMarketDataAdapter
from qtrad.domain.identifiers import ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.operations import HealthStatus
from qtrad.runtime.settings import Settings
from qtrad.runtime.universe import CaptureUniverse, load_capture_universe

_ACKNOWLEDGEMENT = "COLLECTOR_STOPPED_AND_NO_OTHER_STREAM"
_PROVIDER_CODE = re.compile(r"\b(error\.[a-z0-9._-]+|endpoint\.[a-z0-9._-]+)\b")
_INVALID_SESSION_VALUE = "qtrad-invalid-session-probe"


class _DisconnectableClient(Protocol):
    def disconnect(self) -> None: ...


class _Session(Protocol):
    headers: Mapping[str, str]


class _Service(Protocol):
    session: _Session


@dataclass(frozen=True, slots=True)
class _Arguments:
    output: Path
    universe_path: Path
    phase_timeout_seconds: int
    phase_observation_seconds: int
    provider_operation_timeout_seconds: int


def _parse_args() -> _Arguments:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--universe", type=Path, default=Path("config/capture-v1.toml"))
    parser.add_argument("--phase-timeout-seconds", type=int, default=180)
    parser.add_argument("--phase-observation-seconds", type=int, default=10)
    parser.add_argument("--provider-operation-timeout-seconds", type=int, default=30)
    values = parser.parse_args()
    for name in (
        "phase_timeout_seconds",
        "phase_observation_seconds",
        "provider_operation_timeout_seconds",
    ):
        if getattr(values, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if values.phase_timeout_seconds > 600 or values.phase_observation_seconds > 60:
        parser.error("recovery experiment timeouts exceed their bounded maximum")
    return _Arguments(
        output=values.output,
        universe_path=values.universe,
        phase_timeout_seconds=values.phase_timeout_seconds,
        phase_observation_seconds=values.phase_observation_seconds,
        provider_operation_timeout_seconds=values.provider_operation_timeout_seconds,
    )


def _listings(universe: CaptureUniverse, now: datetime) -> tuple[ProviderListing, ...]:
    return tuple(
        ProviderListing(
            listing_id=ProviderListingId(
                "ig", "demo", universe.preferred_epics[instrument.instrument_id]
            ),
            instrument_id=instrument.instrument_id,
            display_name=instrument.display_name,
            product_type=ProductType.ROLLING_CFD,
            currency=instrument.quote_currency,
            minimum_deal_size=Decimal("1"),
            price_increment=None,
            valid_from=now,
            valid_to=None,
            metadata_version="provider-recovery-experiment-v1",
        )
        for instrument in universe.instruments
    )


def _bounded_call(name: str, timeout_seconds: float, operation: Callable[[], object]) -> object:
    outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            outcome.put((True, operation()))
        except BaseException as error:
            outcome.put((False, error))

    worker = threading.Thread(target=run, name=f"qtrad-{name}", daemon=True)
    worker.start()
    try:
        successful, value = outcome.get(timeout=timeout_seconds)
    except queue.Empty as error:
        raise TimeoutError(f"provider operation timed out: {name}") from error
    worker.join(timeout=1)
    if worker.is_alive():
        raise RuntimeError(f"provider operation worker did not terminate: {name}")
    if not successful:
        raise cast(BaseException, value)
    return value


async def _wait_for(
    description: str,
    timeout_seconds: int,
    condition: Callable[[], bool],
) -> None:
    async with asyncio.timeout(timeout_seconds):
        while not condition():
            await asyncio.sleep(0.1)
    if not condition():
        raise RuntimeError(f"condition changed after waiting: {description}")


def _adapter_snapshot(adapter: IgDemoMarketDataAdapter) -> dict[str, object]:
    quote_times = adapter._quote_received_times  # pyright: ignore[reportPrivateUsage]
    expected = adapter._expected_epics  # pyright: ignore[reportPrivateUsage]
    health_frequency = adapter._real_max_frequency_by_epic  # pyright: ignore[reportPrivateUsage]
    return {
        "generation": adapter._generation,  # pyright: ignore[reportPrivateUsage]
        "reconnects": adapter._reconnect_count,  # pyright: ignore[reportPrivateUsage]
        "rest_reauthentications": adapter._rest_reauthentications,  # pyright: ignore[reportPrivateUsage]
        "expected_subscriptions": len(expected),
        "subscribed_subscriptions": len(
            adapter._subscribed_epics  # pyright: ignore[reportPrivateUsage]
        ),
        "updated_subscriptions": len(
            adapter._updated_epics  # pyright: ignore[reportPrivateUsage]
        ),
        "fresh_subscription_times": {
            epic: value.astimezone(UTC).isoformat().replace("+00:00", "Z")
            for epic, value in sorted(quote_times.items())
        },
        "frequency_evidence": dict(sorted(health_frequency.items())),
        "heartbeat_subscribed": adapter._heartbeat_subscribed,  # pyright: ignore[reportPrivateUsage]
        "heartbeat_events": adapter._heartbeat_events,  # pyright: ignore[reportPrivateUsage]
        "heartbeat_frequency": adapter._heartbeat_real_max_frequency,  # pyright: ignore[reportPrivateUsage]
        "lightstreamer_lost_updates": adapter._lightstreamer_lost_updates,  # pyright: ignore[reportPrivateUsage]
        "qtrad_dropped_records": adapter._dropped_records,  # pyright: ignore[reportPrivateUsage]
        "subscription_errors": adapter._subscription_errors,  # pyright: ignore[reportPrivateUsage]
        "server_errors": adapter._server_errors,  # pyright: ignore[reportPrivateUsage]
        "queue_high_water": adapter._queue_high_water,  # pyright: ignore[reportPrivateUsage]
        "transport_status": adapter._last_stream_status,  # pyright: ignore[reportPrivateUsage]
    }


def _phase_ready(adapter: IgDemoMarketDataAdapter, reconnects: int, reauthentications: int) -> bool:
    return (
        adapter._status is HealthStatus.HEALTHY  # pyright: ignore[reportPrivateUsage]
        and adapter._reconnect_count == reconnects  # pyright: ignore[reportPrivateUsage]
        and adapter._rest_reauthentications == reauthentications  # pyright: ignore[reportPrivateUsage]
        and adapter._expected_epics  # pyright: ignore[reportPrivateUsage]
        == adapter._subscribed_epics  # pyright: ignore[reportPrivateUsage]
        == adapter._updated_epics  # pyright: ignore[reportPrivateUsage]
        and adapter._heartbeat_subscribed  # pyright: ignore[reportPrivateUsage]
        and adapter._heartbeat_events > 0  # pyright: ignore[reportPrivateUsage]
        and adapter._heartbeat_real_max_frequency  # pyright: ignore[reportPrivateUsage]
        is not None
        and len(
            adapter._real_max_frequency_by_epic  # pyright: ignore[reportPrivateUsage]
        )
        == len(adapter._expected_epics)  # pyright: ignore[reportPrivateUsage]
    )


def _fresh_counts(
    current: Mapping[str, int], baseline: Mapping[str, int], expected: set[str]
) -> bool:
    return all(current.get(instrument, 0) > baseline.get(instrument, 0) for instrument in expected)


def _safe_error_code(error: BaseException) -> str | None:
    match = _PROVIDER_CODE.search(str(error))
    return match.group(1) if match is not None else None


def _write(path: Path, evidence: dict[str, object]) -> None:
    evidence["evidence_sha256"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(evidence, stream, sort_keys=True, indent=2)
        stream.write("\n")


async def _run(arguments: _Arguments) -> dict[str, object]:
    if os.environ.get("QTRAD_PROVIDER_EXPERIMENT_SINGLE_CONNECTION_ACK") != _ACKNOWLEDGEMENT:
        raise RuntimeError(
            "set QTRAD_PROVIDER_EXPERIMENT_SINGLE_CONNECTION_ACK=" + _ACKNOWLEDGEMENT
        )
    if arguments.output.exists():
        raise FileExistsError("recovery evidence path must not already exist")

    settings = Settings()
    username, password, api_key, account_id = settings.require_ig_credentials()
    universe = load_capture_universe(arguments.universe_path)
    if len(universe.instruments) != 7:
        raise RuntimeError("provider recovery requires the reviewed seven-instrument capture-v1")
    clock = SystemClock()
    config = IgDemoConfig(
        username=username,
        password=password,
        api_key=api_key,
        account_id=account_id,
        readiness_timeout_seconds=float(arguments.phase_timeout_seconds),
        provider_operation_timeout_seconds=float(arguments.provider_operation_timeout_seconds),
    )
    adapter = IgDemoMarketDataAdapter(
        config,
        clock,
        instruments_by_id=universe.instruments_by_id,
        preferred_epics=universe.preferred_epics,
    )
    listings = _listings(universe, clock.now())
    expected_instruments = {str(instrument.instrument_id) for instrument in universe.instruments}
    record_counts: Counter[str] = Counter()
    consumer_error: BaseException | None = None

    async def consume() -> None:
        nonlocal consumer_error
        try:
            async for record in adapter.records():
                if record.quote is not None:
                    record_counts[str(record.quote.instrument_id)] += 1
        except BaseException as error:
            consumer_error = error
            raise

    started_at = clock.now()
    phases: list[dict[str, object]] = []
    run_error: BaseException | None = None
    consumer: asyncio.Task[None] | None = None
    shutdown_verified = False
    try:
        await adapter.connect()
        await adapter.subscribe(listings)
        consumer = asyncio.create_task(consume())
        await asyncio.sleep(arguments.phase_observation_seconds)
        initial_counts = dict(record_counts)
        if not _fresh_counts(initial_counts, {}, expected_instruments):
            raise RuntimeError(
                "initial phase did not deliver every instrument to the record iterator"
            )
        phases.append(
            {
                "phase": "INITIAL_READY",
                "observed_at": clock.now().isoformat(),
                "adapter": _adapter_snapshot(adapter),
                "record_counts": initial_counts,
            }
        )

        client = adapter._stream_client  # pyright: ignore[reportPrivateUsage]
        if client is None:
            raise RuntimeError("stream client unavailable for disconnect fault injection")
        _bounded_call(
            "recovery-disconnect-fault",
            arguments.provider_operation_timeout_seconds,
            cast(_DisconnectableClient, client).disconnect,
        )
        await _wait_for(
            "automatic stream reconnect",
            arguments.phase_timeout_seconds,
            lambda: _phase_ready(adapter, reconnects=1, reauthentications=0),
        )
        await asyncio.sleep(arguments.phase_observation_seconds)
        disconnect_counts = dict(record_counts)
        if not _fresh_counts(disconnect_counts, initial_counts, expected_instruments):
            raise RuntimeError("disconnect recovery lacked fresh records for every instrument")
        phases.append(
            {
                "phase": "DISCONNECT_RECOVERED",
                "observed_at": clock.now().isoformat(),
                "adapter": _adapter_snapshot(adapter),
                "record_counts": disconnect_counts,
            }
        )

        service = adapter._service  # pyright: ignore[reportPrivateUsage]
        if service is None:
            raise RuntimeError("REST service unavailable for invalid-token fault injection")
        headers = cast(MutableMapping[str, str], cast(_Service, service).session.headers)
        headers["CST"] = _INVALID_SESSION_VALUE
        headers["X-SECURITY-TOKEN"] = _INVALID_SESSION_VALUE
        await adapter.review_listings([universe.instruments[0].instrument_id])
        await _wait_for(
            "invalid-token reauthentication",
            arguments.phase_timeout_seconds,
            lambda: _phase_ready(adapter, reconnects=2, reauthentications=1),
        )
        await asyncio.sleep(arguments.phase_observation_seconds)
        token_counts = dict(record_counts)
        if not _fresh_counts(token_counts, disconnect_counts, expected_instruments):
            raise RuntimeError("token recovery lacked fresh records for every instrument")
        phases.append(
            {
                "phase": "INVALID_TOKEN_RECOVERED",
                "observed_at": clock.now().isoformat(),
                "adapter": _adapter_snapshot(adapter),
                "record_counts": token_counts,
            }
        )
    except BaseException as error:
        run_error = error
    finally:
        try:
            await adapter.disconnect()
        except BaseException as error:
            run_error = run_error or error
        if consumer is not None:
            try:
                await asyncio.wait_for(consumer, timeout=arguments.phase_timeout_seconds)
            except BaseException as error:
                run_error = run_error or error
        shutdown_verified = (
            adapter._status is HealthStatus.STOPPED  # pyright: ignore[reportPrivateUsage]
            and adapter._stream_client is None  # pyright: ignore[reportPrivateUsage]
            and adapter._service is None  # pyright: ignore[reportPrivateUsage]
            and not adapter._provider_threads  # pyright: ignore[reportPrivateUsage]
            and adapter._reconnect_task is None  # pyright: ignore[reportPrivateUsage]
            and consumer is not None
            and consumer.done()
            and consumer_error is None
        )

    final_snapshot = _adapter_snapshot(adapter)
    checks = {
        "initial_ready": any(phase["phase"] == "INITIAL_READY" for phase in phases),
        "disconnect_recovered": any(phase["phase"] == "DISCONNECT_RECOVERED" for phase in phases),
        "invalid_token_recovered": any(
            phase["phase"] == "INVALID_TOKEN_RECOVERED" for phase in phases
        ),
        "exact_reconnect_count": final_snapshot["reconnects"] == 2,
        "exact_rest_reauthentication_count": final_snapshot["rest_reauthentications"] == 1,
        "zero_qtrad_drops": final_snapshot["qtrad_dropped_records"] == 0,
        "zero_lightstreamer_loss": final_snapshot["lightstreamer_lost_updates"] == 0,
        "zero_subscription_errors": final_snapshot["subscription_errors"] == 0,
        "zero_server_errors": final_snapshot["server_errors"] == 0,
        "shutdown_verified": shutdown_verified,
        "provider_operations_completed": run_error is None,
    }
    return {
        "schema_version": 1,
        "experiment": "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY",
        "started_at": started_at.isoformat(),
        "finished_at": clock.now().isoformat(),
        "universe_name": universe.name,
        "configuration_hash": universe.configuration_hash,
        "phases": phases,
        "final_adapter": final_snapshot,
        "checks": checks,
        "failure": None
        if run_error is None
        else {"type": type(run_error).__name__, "code": _safe_error_code(run_error)},
        "result": "PASS" if all(checks.values()) else "FAIL",
    }


def main() -> None:
    arguments = _parse_args()
    evidence = asyncio.run(_run(arguments))
    _write(arguments.output, evidence)
    print(f"{evidence['result']} {evidence['evidence_sha256']} {arguments.output}")
    if evidence["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
