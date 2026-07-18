#!/usr/bin/env python3
"""Run one bounded IG PRICE/CHART:TICK/heartbeat continuity contrast.

This diagnostic owns the only Lightstreamer connection for its IG API key. It never
writes to a q-trad database and records no credentials, account identifier, session
token, provider error message or price value.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from qtrad.adapters.ig.lightstreamer_compat import install_lightstreamer_compatibility
from qtrad.runtime.settings import Settings
from qtrad.runtime.universe import CaptureUniverse, load_capture_universe

_ACKNOWLEDGEMENT = "COLLECTOR_STOPPED_AND_NO_OTHER_STREAM"
_HEARTBEAT_ITEM = "TRADE:HB.U.HEARTBEAT.IP"
_PRICE_FIELDS = (
    "TIMESTAMP",
    "BIDPRICE1",
    "ASKPRICE1",
    "BIDSIZE1",
    "ASKSIZE1",
    "DLG_FLAG",
    "DELAY",
)
_CHART_FIELDS = ("BID", "OFR", "UTM")
_HEARTBEAT_FIELDS = ("HEARTBEAT",)
_PROVIDER_CODE = re.compile(r"\b(error\.[a-z0-9._-]+|endpoint\.[a-z0-9._-]+)\b")


class _ItemUpdate(Protocol):
    def getValue(self, field: str | int, /) -> object | None: ...

    def isValueChanged(self, field: str | int, /) -> bool: ...


class _ConnectionDetails(Protocol):
    def setUser(self, user: str) -> None: ...

    def setPassword(self, password: str) -> None: ...


class _Subscription(Protocol):
    def addListener(self, listener: object) -> None: ...

    def setDataAdapter(self, data_adapter: str) -> None: ...


class _Client(Protocol):
    connectionDetails: _ConnectionDetails

    def addListener(self, listener: object) -> None: ...

    def connect(self) -> None: ...

    def subscribe(self, subscription: _Subscription) -> None: ...

    def unsubscribe(self, subscription: _Subscription) -> None: ...

    def disconnect(self) -> None: ...

    def getStatus(self) -> str: ...


class _ClosableSession(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Arguments:
    output_manifest: Path
    output_events: Path
    universe_path: Path
    duration_seconds: int
    readiness_timeout_seconds: int
    shutdown_timeout_seconds: int
    silence_seconds: int
    queue_capacity: int
    provider_operation_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class _Channel:
    key: str
    feed: str
    instrument_id: str | None
    epic: str | None
    item: str
    fields: tuple[str, ...]
    mode: str
    data_adapter: str | None


@dataclass(slots=True)
class _ChannelState:
    subscribed: bool = False
    subscription_events: int = 0
    renewals: int = 0
    updates: int = 0
    lost_updates: int = 0
    errors: int = 0
    last_update_monotonic: float | None = None
    real_max_frequency: str | None = None


@dataclass(slots=True)
class _ContrastState:
    channels: Mapping[str, _Channel]
    _states: dict[str, _ChannelState] = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _sequence: int = field(default=0, init=False)
    _queue_drops: int = field(default=0, init=False)
    _queue_high_water: int = field(default=0, init=False)
    _server_errors: int = field(default=0, init=False)
    _last_server_error_code: int | None = field(default=None, init=False)
    _transport_status: str = field(default="UNOBSERVED", init=False)
    _transport_events: int = field(default=0, init=False)
    _ever_connected: bool = field(default=False, init=False)
    _discrepancies: list[dict[str, object]] = field(default_factory=list, init=False)
    _open_discrepancies: dict[tuple[str, str], int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._states = {key: _ChannelState() for key in self.channels}

    def event(
        self,
        event_queue: queue.Queue[dict[str, object] | None],
        *,
        kind: str,
        channel_key: str | None = None,
        fields: Sequence[str] = (),
        provider_timestamp: str | None = None,
        code: int | None = None,
        count: int | None = None,
        value: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        monotonic = time.monotonic()
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            if channel_key is not None:
                state = self._states[channel_key]
                if kind == "SUBSCRIBED":
                    if state.subscription_events > 0:
                        state.renewals += 1
                        state.last_update_monotonic = None
                    state.subscription_events += 1
                    state.subscribed = True
                elif kind == "UNSUBSCRIBED":
                    state.subscribed = False
                elif kind == "UPDATE":
                    state.updates += 1
                    state.last_update_monotonic = monotonic
                elif kind == "LOST_UPDATES":
                    state.lost_updates += count or 0
                elif kind == "SUBSCRIPTION_ERROR":
                    state.errors += 1
                elif kind == "REAL_MAX_FREQUENCY":
                    state.real_max_frequency = value
            elif kind == "TRANSPORT_STATUS":
                self._transport_status = value or "UNKNOWN"
                self._transport_events += 1
                if self._transport_status.startswith("CONNECTED"):
                    self._ever_connected = True
            elif kind == "SERVER_ERROR":
                self._server_errors += 1
                self._last_server_error_code = code

            channel = self.channels.get(channel_key) if channel_key is not None else None
            record: dict[str, object] = {
                "sequence": sequence,
                "received_at": _utc_text(now),
                "kind": kind,
            }
            if channel is not None:
                record.update(
                    {
                        "channel": channel.key,
                        "feed": channel.feed,
                        "instrument_id": channel.instrument_id,
                        "epic": channel.epic,
                    }
                )
            if fields:
                record["changed_fields"] = list(fields)
            if provider_timestamp is not None:
                record["provider_timestamp"] = provider_timestamp[:64]
            if code is not None:
                record["code"] = code
            if count is not None:
                record["count"] = count
            if value is not None:
                record["value"] = value[:64]
        try:
            event_queue.put_nowait(record)
        except queue.Full:
            with self._lock:
                self._queue_drops += 1
        else:
            with self._lock:
                self._queue_high_water = max(self._queue_high_water, event_queue.qsize())

    def ready(self) -> bool:
        with self._lock:
            return all(
                state.subscribed and state.last_update_monotonic is not None
                for state in self._states.values()
            )

    def current(self, *, now: float, threshold_seconds: int) -> bool:
        with self._lock:
            return all(
                state.subscribed and _fresh(state, now, threshold_seconds)
                for state in self._states.values()
            )

    def failed(self) -> bool:
        with self._lock:
            return (
                self._queue_drops > 0
                or self._server_errors > 0
                or any(
                    state.errors > 0 or state.lost_updates > 0 for state in self._states.values()
                )
            )

    def all_unsubscribed(self) -> bool:
        with self._lock:
            return all(not state.subscribed for state in self._states.values())

    def transport_connected(self) -> bool:
        with self._lock:
            return self._transport_status.startswith("CONNECTED")

    def inspect_silence(self, *, now: float, threshold_seconds: int) -> None:
        with self._lock:
            heartbeat = self._states["heartbeat"]
            heartbeat_fresh = _fresh(heartbeat, now, threshold_seconds)
            heartbeat_identity = ("heartbeat", "HEARTBEAT_SILENT")
            if not heartbeat_fresh and heartbeat_identity not in self._open_discrepancies:
                self._discrepancies.append(
                    {
                        "instrument_id": None,
                        "condition": "HEARTBEAT_SILENT",
                        "detected_at": _utc_text(datetime.now(UTC)),
                        "resolved_at": None,
                        "transport_status": self._transport_status,
                        "heartbeat_fresh": False,
                    }
                )
                self._open_discrepancies[heartbeat_identity] = len(self._discrepancies) - 1
            elif heartbeat_fresh and heartbeat_identity in self._open_discrepancies:
                index = self._open_discrepancies.pop(heartbeat_identity)
                self._discrepancies[index]["resolved_at"] = _utc_text(datetime.now(UTC))
            active_instruments = {
                channel.instrument_id
                for key, channel in self.channels.items()
                if channel.instrument_id is not None
                and _fresh(self._states[key], now, threshold_seconds)
            }
            for channel in self.channels.values():
                if channel.feed != "PRICE" or channel.instrument_id is None:
                    continue
                price = self._states[channel.key]
                chart = self._states[f"chart:{channel.instrument_id}"]
                price_fresh = _fresh(price, now, threshold_seconds)
                chart_fresh = _fresh(chart, now, threshold_seconds)
                condition: str | None = None
                if not price_fresh and chart_fresh:
                    condition = "PRICE_SILENT_CHART_ACTIVE"
                elif price_fresh and not chart_fresh:
                    condition = "CHART_SILENT_PRICE_ACTIVE"
                elif (
                    not price_fresh
                    and not chart_fresh
                    and heartbeat_fresh
                    and active_instruments - {channel.instrument_id}
                ):
                    condition = "BOTH_FEEDS_SILENT_OTHER_ITEMS_ACTIVE"
                for candidate in (
                    "PRICE_SILENT_CHART_ACTIVE",
                    "CHART_SILENT_PRICE_ACTIVE",
                    "BOTH_FEEDS_SILENT_OTHER_ITEMS_ACTIVE",
                ):
                    identity = (channel.instrument_id, candidate)
                    if candidate == condition and identity not in self._open_discrepancies:
                        self._discrepancies.append(
                            {
                                "instrument_id": channel.instrument_id,
                                "condition": candidate,
                                "detected_at": _utc_text(datetime.now(UTC)),
                                "resolved_at": None,
                                "transport_status": self._transport_status,
                                "heartbeat_fresh": heartbeat_fresh,
                            }
                        )
                        self._open_discrepancies[identity] = len(self._discrepancies) - 1
                    elif candidate != condition and identity in self._open_discrepancies:
                        index = self._open_discrepancies.pop(identity)
                        self._discrepancies[index]["resolved_at"] = _utc_text(datetime.now(UTC))

    def summary(self) -> dict[str, object]:
        with self._lock:
            return {
                "channels": {
                    key: {
                        "subscribed": state.subscribed,
                        "subscription_events": state.subscription_events,
                        "renewals": state.renewals,
                        "updates": state.updates,
                        "lost_updates": state.lost_updates,
                        "errors": state.errors,
                        "real_max_frequency": state.real_max_frequency,
                    }
                    for key, state in sorted(self._states.items())
                },
                "queue_drops": self._queue_drops,
                "queue_high_water": self._queue_high_water,
                "server_errors": self._server_errors,
                "last_server_error_code": self._last_server_error_code,
                "transport_status": self._transport_status,
                "transport_events": self._transport_events,
                "ever_connected": self._ever_connected,
                "event_attempts": self._sequence,
                "discrepancies": list(self._discrepancies),
            }


def _fresh(state: _ChannelState, now: float, threshold_seconds: int) -> bool:
    return (
        state.last_update_monotonic is not None
        and now - state.last_update_monotonic <= threshold_seconds
    )


def _parse_args() -> _Arguments:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_manifest", type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--universe", type=Path, default=Path("config/capture-v1.toml"))
    parser.add_argument("--duration-seconds", type=int, default=10_800)
    parser.add_argument("--readiness-timeout-seconds", type=int, default=180)
    parser.add_argument("--shutdown-timeout-seconds", type=int, default=30)
    parser.add_argument("--silence-seconds", type=int, default=180)
    parser.add_argument("--queue-capacity", type=int, default=100_000)
    parser.add_argument("--provider-operation-timeout-seconds", type=int, default=30)
    values = parser.parse_args()
    for name in (
        "duration_seconds",
        "readiness_timeout_seconds",
        "shutdown_timeout_seconds",
        "silence_seconds",
        "queue_capacity",
        "provider_operation_timeout_seconds",
    ):
        if getattr(values, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if values.duration_seconds > 21_600:
        parser.error("--duration-seconds cannot exceed 21600")
    if values.output_manifest == values.events:
        parser.error("manifest and event paths must differ")
    return _Arguments(
        output_manifest=values.output_manifest,
        output_events=values.events,
        universe_path=values.universe,
        duration_seconds=values.duration_seconds,
        readiness_timeout_seconds=values.readiness_timeout_seconds,
        shutdown_timeout_seconds=values.shutdown_timeout_seconds,
        silence_seconds=values.silence_seconds,
        queue_capacity=values.queue_capacity,
        provider_operation_timeout_seconds=values.provider_operation_timeout_seconds,
    )


def _channels(universe: CaptureUniverse, account_id: str) -> dict[str, _Channel]:
    channels = {
        "heartbeat": _Channel(
            key="heartbeat",
            feed="HEARTBEAT",
            instrument_id=None,
            epic=None,
            item=_HEARTBEAT_ITEM,
            fields=_HEARTBEAT_FIELDS,
            mode="MERGE",
            data_adapter=None,
        )
    }
    for instrument in universe.instruments:
        instrument_id = str(instrument.instrument_id)
        epic = universe.preferred_epics[instrument.instrument_id]
        channels[f"price:{instrument_id}"] = _Channel(
            key=f"price:{instrument_id}",
            feed="PRICE",
            instrument_id=instrument_id,
            epic=epic,
            item=f"PRICE:{account_id}:{epic}",
            fields=_PRICE_FIELDS,
            mode="MERGE",
            data_adapter="Pricing",
        )
        channels[f"chart:{instrument_id}"] = _Channel(
            key=f"chart:{instrument_id}",
            feed="CHART_TICK",
            instrument_id=instrument_id,
            epic=epic,
            item=f"CHART:{epic}:TICK",
            fields=_CHART_FIELDS,
            mode="DISTINCT",
            data_adapter=None,
        )
    return channels


def _provider_timestamp(channel: _Channel, update: _ItemUpdate) -> str | None:
    position = 3 if channel.feed == "CHART_TICK" else 1
    value = update.getValue(position)
    return None if value is None else str(value)


def _safe_error_code(error: BaseException) -> str | None:
    match = _PROVIDER_CODE.search(str(error))
    return match.group(1) if match is not None else None


def _single_record(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return cast(Mapping[str, object], result)
    raise TypeError("IG session response is not a single record")


def _required_text(value: Mapping[str, object], key: str) -> str:
    candidate = value[key]
    if not isinstance(candidate, str) or not candidate:
        raise RuntimeError(f"IG session response lacks {key}")
    return candidate


def _bounded_call(name: str, timeout_seconds: int, operation: Callable[[], object]) -> object:
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


def _writer(
    path: Path,
    events: queue.Queue[dict[str, object] | None],
    outcome: queue.Queue[tuple[int, str] | BaseException],
) -> None:
    digest = hashlib.sha256()
    count = 0
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with (
            os.fdopen(descriptor, "wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream,
        ):
            while True:
                event = events.get()
                if event is None:
                    break
                encoded = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
                digest.update(encoded)
                stream.write(encoded)
                count += 1
        outcome.put((count, digest.hexdigest()))
    except BaseException as error:
        outcome.put(error)


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _empty_event_stream(path: Path) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    events: queue.Queue[dict[str, object] | None] = queue.Queue()
    outcome: queue.Queue[tuple[int, str] | BaseException] = queue.Queue(maxsize=1)
    events.put(None)
    _writer(path, events, outcome)
    result = outcome.get_nowait()
    if isinstance(result, BaseException):
        raise result
    return result


def _prestream_failure(
    *,
    arguments: _Arguments,
    universe: CaptureUniverse,
    started_at: datetime,
    error: BaseException,
) -> dict[str, object]:
    event_count, event_sha256 = _empty_event_stream(arguments.output_events)
    return {
        "schema_version": 1,
        "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
        "started_at": _utc_text(started_at),
        "finished_at": _utc_text(datetime.now(UTC)),
        "duration_seconds": arguments.duration_seconds,
        "universe_name": universe.name,
        "configuration_hash": universe.configuration_hash,
        "subscription_count": 15,
        "event_stream": {
            "path": arguments.output_events.name,
            "encoding": "gzip-json-lines",
            "record_count": event_count,
            "uncompressed_sha256": event_sha256,
        },
        "summary": None,
        "checks": {
            "all_channels_data_ready": False,
            "all_channels_current_at_stop": False,
            "all_applied_frequencies_observed": False,
            "transport_connected": False,
            "no_queue_drops": True,
            "no_lightstreamer_lost_updates": True,
            "no_subscription_errors": True,
            "no_server_errors": True,
            "no_unexplained_discrepancies": False,
            "shutdown_verified": False,
            "unsubscriptions_verified": True,
            "logout_completed": False,
            "http_session_close_completed": False,
            "provider_workers_terminated": False,
            "provider_operation_completed": False,
        },
        "failure": {"type": type(error).__name__, "code": _safe_error_code(error)},
        "result": "FAIL",
    }


def _run(arguments: _Arguments) -> dict[str, object]:
    if os.environ.get("QTRAD_PROVIDER_EXPERIMENT_SINGLE_CONNECTION_ACK") != _ACKNOWLEDGEMENT:
        raise RuntimeError(
            "set QTRAD_PROVIDER_EXPERIMENT_SINGLE_CONNECTION_ACK=" + _ACKNOWLEDGEMENT
        )
    if arguments.output_manifest.exists() or arguments.output_events.exists():
        raise FileExistsError("contrast evidence paths must not already exist")

    universe = load_capture_universe(arguments.universe_path)
    if len(universe.instruments) != 7:
        raise RuntimeError("provider contrast requires the reviewed seven-instrument capture-v1")
    started_at = datetime.now(UTC)

    try:
        settings = Settings()
        username, password, api_key, configured_account = settings.require_ig_credentials()

        install_lightstreamer_compatibility()
        from lightstreamer.client import (
            ClientListener,
            ItemUpdate,
            LightstreamerClient,
            Subscription,
            SubscriptionListener,
        )
        from trading_ig.rest import IGService

        service = IGService(
            username,
            password,
            api_key,
            acc_type="DEMO",
            acc_number=configured_account,
            use_rate_limiter=False,
        )
        session = _single_record(
            _bounded_call(
                "contrast-login",
                arguments.provider_operation_timeout_seconds,
                service.create_session,
            )
        )
        endpoint = _required_text(session, "lightstreamerEndpoint")
        account_id = _required_text(session, "currentAccountId")
        cst = service.session.headers["CST"]
        security_token = service.session.headers["X-SECURITY-TOKEN"]
    except BaseException as error:
        return _prestream_failure(
            arguments=arguments,
            universe=universe,
            started_at=started_at,
            error=error,
        )

    channels = _channels(universe, account_id)
    state = _ContrastState(channels=channels)
    events: queue.Queue[dict[str, object] | None] = queue.Queue(maxsize=arguments.queue_capacity)
    writer_outcome: queue.Queue[tuple[int, str] | BaseException] = queue.Queue(maxsize=1)
    arguments.output_events.parent.mkdir(parents=True, exist_ok=True)
    writer = threading.Thread(
        target=_writer,
        args=(arguments.output_events, events, writer_outcome),
        name="qtrad-contrast-evidence-writer",
        daemon=False,
    )
    writer.start()

    class ChannelListener(SubscriptionListener):
        def __init__(self, channel: _Channel) -> None:
            self._channel = channel

        def onSubscription(self) -> None:
            state.event(events, kind="SUBSCRIBED", channel_key=self._channel.key)

        def onUnsubscription(self) -> None:
            state.event(events, kind="UNSUBSCRIBED", channel_key=self._channel.key)

        def onItemUpdate(self, update: ItemUpdate) -> None:
            changed = tuple(
                field
                for position, field in enumerate(self._channel.fields, start=1)
                if update.isValueChanged(position)
            )
            state.event(
                events,
                kind="UPDATE",
                channel_key=self._channel.key,
                fields=changed,
                provider_timestamp=_provider_timestamp(self._channel, cast(_ItemUpdate, update)),
            )

        def onSubscriptionError(self, code: int, message: str | None) -> None:
            del message
            state.event(
                events,
                kind="SUBSCRIPTION_ERROR",
                channel_key=self._channel.key,
                code=code,
            )

        def onItemLostUpdates(self, itemName: str | None, itemPos: int, lostUpdates: int) -> None:
            del itemName, itemPos
            state.event(
                events,
                kind="LOST_UPDATES",
                channel_key=self._channel.key,
                count=lostUpdates,
            )

        def onRealMaxFrequency(self, frequency: str | None) -> None:
            state.event(
                events,
                kind="REAL_MAX_FREQUENCY",
                channel_key=self._channel.key,
                value=frequency or "unlimited",
            )

    class StatusListener(ClientListener):
        def onStatusChange(self, status: str) -> None:
            state.event(events, kind="TRANSPORT_STATUS", value=status)

        def onServerError(self, code: int, message: str | None) -> None:
            del message
            state.event(events, kind="SERVER_ERROR", code=code)

    client = cast(_Client, LightstreamerClient(endpoint, None))
    client.connectionDetails.setUser(account_id)
    client.connectionDetails.setPassword(f"CST-{cst}|XST-{security_token}")
    client.addListener(StatusListener())
    subscriptions: list[_Subscription] = []
    run_error: BaseException | None = None
    shutdown_verified = False
    unsubscriptions_verified = False
    logout_completed = False
    http_session_close_completed = False
    became_ready = False
    all_channels_current_at_stop = False
    transport_connected_at_stop = False
    try:
        state.event(events, kind="EXPERIMENT_STARTED")
        _bounded_call(
            "contrast-connect", arguments.provider_operation_timeout_seconds, client.connect
        )
        for channel in channels.values():
            subscription = cast(
                _Subscription,
                Subscription(
                    mode=channel.mode,
                    items=[channel.item],
                    fields=list(channel.fields),
                ),
            )
            if channel.data_adapter is not None:
                subscription.setDataAdapter(channel.data_adapter)
            subscription.addListener(ChannelListener(channel))
            _bounded_call(
                "contrast-subscribe",
                arguments.provider_operation_timeout_seconds,
                lambda subscription=subscription: client.subscribe(subscription),
            )
            subscriptions.append(subscription)

        readiness_deadline = time.monotonic() + arguments.readiness_timeout_seconds
        while not state.ready() and time.monotonic() < readiness_deadline and not state.failed():
            time.sleep(0.1)
        if not state.ready():
            raise TimeoutError("all 15 provider contrast channels did not become data-ready")
        became_ready = True

        deadline = time.monotonic() + arguments.duration_seconds
        while time.monotonic() < deadline and not state.failed():
            state.inspect_silence(now=time.monotonic(), threshold_seconds=arguments.silence_seconds)
            time.sleep(1.0)
    except BaseException as error:
        run_error = error
    finally:
        all_channels_current_at_stop = state.current(
            now=time.monotonic(),
            threshold_seconds=arguments.silence_seconds,
        )
        transport_connected_at_stop = state.transport_connected()
        state.event(events, kind="EXPERIMENT_STOP_REQUESTED")
        for subscription in reversed(subscriptions):
            try:
                _bounded_call(
                    "contrast-unsubscribe",
                    arguments.provider_operation_timeout_seconds,
                    lambda subscription=subscription: client.unsubscribe(subscription),
                )
            except BaseException as error:
                run_error = run_error or error
        try:
            _bounded_call(
                "contrast-disconnect",
                arguments.provider_operation_timeout_seconds,
                client.disconnect,
            )
            shutdown_deadline = time.monotonic() + arguments.shutdown_timeout_seconds
            while time.monotonic() < shutdown_deadline:
                if client.getStatus() == "DISCONNECTED":
                    shutdown_verified = True
                    break
                time.sleep(0.1)
            if not shutdown_verified:
                run_error = run_error or TimeoutError("Lightstreamer disconnect was not verified")
            unsubscription_deadline = time.monotonic() + arguments.shutdown_timeout_seconds
            while time.monotonic() < unsubscription_deadline:
                if state.all_unsubscribed():
                    unsubscriptions_verified = True
                    break
                time.sleep(0.1)
            if not unsubscriptions_verified:
                run_error = run_error or TimeoutError(
                    "Lightstreamer unsubscriptions were not verified"
                )
        except BaseException as error:
            run_error = run_error or error
        try:
            _bounded_call(
                "contrast-logout",
                arguments.provider_operation_timeout_seconds,
                service.logout,
            )
            logout_completed = True
        except BaseException as error:
            run_error = run_error or error
        try:
            _bounded_call(
                "contrast-http-session-close",
                arguments.provider_operation_timeout_seconds,
                cast(_ClosableSession, service.session).close,
            )
            http_session_close_completed = True
        except BaseException as error:
            run_error = run_error or error
        state.event(
            events,
            kind="EXPERIMENT_STOPPED",
            value="VERIFIED" if shutdown_verified and unsubscriptions_verified else "UNVERIFIED",
        )
        if writer.is_alive():
            try:
                events.put(None, timeout=arguments.shutdown_timeout_seconds)
            except queue.Full:
                run_error = run_error or TimeoutError(
                    "evidence queue did not accept the shutdown marker"
                )
        writer.join(arguments.shutdown_timeout_seconds)
        if writer.is_alive():
            run_error = run_error or TimeoutError("evidence writer did not stop")

    try:
        writer_result = writer_outcome.get_nowait()
    except queue.Empty as error:
        raise RuntimeError("evidence writer produced no terminal result") from error
    if isinstance(writer_result, BaseException):
        raise writer_result
    event_count, event_sha256 = writer_result
    summary = state.summary()
    provider_workers_terminated = not any(
        thread.is_alive() and thread.name.startswith("qtrad-contrast-")
        for thread in threading.enumerate()
    )
    checks = {
        "all_channels_data_ready": became_ready,
        "all_channels_current_at_stop": all_channels_current_at_stop,
        "all_applied_frequencies_observed": all(
            cast(Mapping[str, object], item)["real_max_frequency"] is not None
            for item in cast(Mapping[str, object], summary["channels"]).values()
        ),
        "transport_connected": transport_connected_at_stop,
        "no_queue_drops": summary["queue_drops"] == 0,
        "no_lightstreamer_lost_updates": all(
            cast(Mapping[str, object], item)["lost_updates"] == 0
            for item in cast(Mapping[str, object], summary["channels"]).values()
        ),
        "no_subscription_errors": all(
            cast(Mapping[str, object], item)["errors"] == 0
            for item in cast(Mapping[str, object], summary["channels"]).values()
        ),
        "no_server_errors": summary["server_errors"] == 0,
        "no_unexplained_discrepancies": not summary["discrepancies"],
        "shutdown_verified": shutdown_verified,
        "unsubscriptions_verified": unsubscriptions_verified,
        "logout_completed": logout_completed,
        "http_session_close_completed": http_session_close_completed,
        "provider_workers_terminated": provider_workers_terminated,
        "provider_operation_completed": run_error is None,
    }
    finished_at = datetime.now(UTC)
    return {
        "schema_version": 1,
        "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
        "started_at": _utc_text(started_at),
        "finished_at": _utc_text(finished_at),
        "duration_seconds": arguments.duration_seconds,
        "universe_name": universe.name,
        "configuration_hash": universe.configuration_hash,
        "subscription_count": len(channels),
        "event_stream": {
            "path": arguments.output_events.name,
            "encoding": "gzip-json-lines",
            "record_count": event_count,
            "uncompressed_sha256": event_sha256,
        },
        "summary": summary,
        "checks": checks,
        "failure": None
        if run_error is None
        else {"type": type(run_error).__name__, "code": _safe_error_code(run_error)},
        "result": "PASS" if all(checks.values()) else "FAIL",
    }


def main() -> None:
    arguments = _parse_args()
    evidence = _run(arguments)
    _write_manifest(arguments.output_manifest, evidence)
    print(f"{evidence['result']} {evidence['evidence_sha256']} {arguments.output_manifest}")
    if evidence["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
