from __future__ import annotations

import gzip
import hashlib
import json
import queue
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from ops.dev.provider_stream_contrast import (
    _Arguments,
    _Channel,
    _ContrastState,
    _prestream_failure,
    _write_manifest,
    _writer,
)
from ops.dev.verify_stream_experiment_evidence import verify
from qtrad.runtime.universe import load_capture_universe


def _channels() -> dict[str, _Channel]:
    return {
        "heartbeat": _Channel(
            key="heartbeat",
            feed="HEARTBEAT",
            instrument_id=None,
            epic=None,
            item="TRADE:HB.U.HEARTBEAT.IP",
            fields=("HEARTBEAT",),
            mode="MERGE",
            data_adapter=None,
        ),
        "price:fx:aud-usd": _Channel(
            key="price:fx:aud-usd",
            feed="PRICE",
            instrument_id="fx:aud-usd",
            epic="CS.D.AUDUSD.CFD.IP",
            item="redacted-account-item",
            fields=("TIMESTAMP",),
            mode="MERGE",
            data_adapter="Pricing",
        ),
        "chart:fx:aud-usd": _Channel(
            key="chart:fx:aud-usd",
            feed="CHART_TICK",
            instrument_id="fx:aud-usd",
            epic="CS.D.AUDUSD.CFD.IP",
            item="CHART:CS.D.AUDUSD.CFD.IP:TICK",
            fields=("UTM",),
            mode="DISTINCT",
            data_adapter=None,
        ),
        "price:fx:eur-usd": _Channel(
            key="price:fx:eur-usd",
            feed="PRICE",
            instrument_id="fx:eur-usd",
            epic="CS.D.EURUSD.CFD.IP",
            item="redacted-account-item",
            fields=("TIMESTAMP",),
            mode="MERGE",
            data_adapter="Pricing",
        ),
        "chart:fx:eur-usd": _Channel(
            key="chart:fx:eur-usd",
            feed="CHART_TICK",
            instrument_id="fx:eur-usd",
            epic="CS.D.EURUSD.CFD.IP",
            item="CHART:CS.D.EURUSD.CFD.IP:TICK",
            fields=("UTM",),
            mode="DISTINCT",
            data_adapter=None,
        ),
    }


def _make_ready(state: _ContrastState, events: queue.Queue[dict[str, object] | None]) -> None:
    for channel_key in state.channels:
        state.event(events, kind="SUBSCRIBED", channel_key=channel_key)
        state.event(events, kind="UPDATE", channel_key=channel_key)


def test_contrast_requires_every_channel_subscription_and_update() -> None:
    state = _ContrastState(channels=_channels())
    events: queue.Queue[dict[str, object] | None] = queue.Queue(maxsize=100)

    for channel_key in tuple(state.channels)[:-1]:
        state.event(events, kind="SUBSCRIBED", channel_key=channel_key)
        state.event(events, kind="UPDATE", channel_key=channel_key)

    assert not state.ready()

    final = tuple(state.channels)[-1]
    state.event(events, kind="SUBSCRIBED", channel_key=final)
    assert not state.ready()
    state.event(events, kind="UPDATE", channel_key=final)
    assert state.ready()

    state.event(events, kind="SUBSCRIBED", channel_key=final)
    assert not state.ready()
    state.event(events, kind="UPDATE", channel_key=final)
    assert state.ready()
    channels = cast(Mapping[str, Mapping[str, object]], state.summary()["channels"])
    assert channels[final]["subscription_events"] == 2
    assert channels[final]["renewals"] == 1


def test_contrast_identifies_price_silence_while_chart_and_connection_are_active() -> None:
    state = _ContrastState(channels=_channels())
    events: queue.Queue[dict[str, object] | None] = queue.Queue(maxsize=100)
    _make_ready(state, events)
    now = time.monotonic()
    state._states["price:fx:aud-usd"].last_update_monotonic = now - 181

    state.inspect_silence(now=now, threshold_seconds=180)

    discrepancies = cast(list[dict[str, object]], state.summary()["discrepancies"])
    assert len(discrepancies) == 1
    assert discrepancies[0]["instrument_id"] == "fx:aud-usd"
    assert discrepancies[0]["condition"] == "PRICE_SILENT_CHART_ACTIVE"
    assert discrepancies[0]["transport_status"] == "UNOBSERVED"
    assert discrepancies[0]["heartbeat_fresh"] is True
    assert discrepancies[0]["resolved_at"] is None

    state._states["price:fx:aud-usd"].last_update_monotonic = now
    state.inspect_silence(now=now, threshold_seconds=180)
    resolved = cast(list[dict[str, object]], state.summary()["discrepancies"])
    assert resolved[0]["resolved_at"] is not None


def test_contrast_loss_and_queue_overflow_fail_closed() -> None:
    state = _ContrastState(channels=_channels())
    events: queue.Queue[dict[str, object] | None] = queue.Queue(maxsize=1)

    state.event(events, kind="SUBSCRIBED", channel_key="heartbeat")
    state.event(events, kind="UPDATE", channel_key="heartbeat")
    state.event(
        events,
        kind="LOST_UPDATES",
        channel_key="price:fx:aud-usd",
        count=3,
    )

    summary = state.summary()
    assert state.failed()
    assert summary["queue_drops"] == 2
    channels = cast(Mapping[str, Mapping[str, object]], summary["channels"])
    assert channels["price:fx:aud-usd"]["lost_updates"] == 3


def test_event_stream_and_manifest_are_non_overwriting_and_self_hashed(tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl.gz"
    manifest_path = tmp_path / "manifest.json"
    events: queue.Queue[dict[str, object] | None] = queue.Queue()
    outcome: queue.Queue[tuple[int, str] | BaseException] = queue.Queue()
    events.put({"sequence": 1, "kind": "UPDATE"})
    events.put(None)

    worker = threading.Thread(target=_writer, args=(event_path, events, outcome))
    worker.start()
    worker.join(5)
    result = outcome.get_nowait()
    assert not isinstance(result, BaseException)
    count, digest = result
    with gzip.open(event_path, "rb") as stream:
        encoded = stream.read()
    assert count == 1
    assert digest == hashlib.sha256(encoded).hexdigest()

    payload: dict[str, object] = {"result": "PASS"}
    _write_manifest(manifest_path, payload)
    stored = json.loads(manifest_path.read_text())
    evidence_hash = stored.pop("evidence_sha256")
    assert (
        evidence_hash
        == hashlib.sha256(
            json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert event_path.stat().st_mode & 0o777 == 0o600
    assert manifest_path.stat().st_mode & 0o777 == 0o600

    with pytest.raises(FileExistsError):
        _write_manifest(manifest_path, {"result": "PASS"})


def test_prestream_failure_retains_empty_bounded_evidence(tmp_path: Path) -> None:
    arguments = _Arguments(
        output_manifest=tmp_path / "manifest.json",
        output_events=tmp_path / "events.jsonl.gz",
        universe_path=Path("config/capture-v1.toml"),
        duration_seconds=300,
        readiness_timeout_seconds=30,
        shutdown_timeout_seconds=10,
        silence_seconds=180,
        queue_capacity=100,
        provider_operation_timeout_seconds=10,
    )

    evidence = _prestream_failure(
        arguments=arguments,
        universe=load_capture_universe(arguments.universe_path),
        started_at=datetime.now(UTC),
        error=TimeoutError("redacted detail"),
    )

    assert evidence["result"] == "FAIL"
    assert evidence["failure"] == {"type": "TimeoutError", "code": None}
    assert evidence["event_stream"] == {
        "path": "events.jsonl.gz",
        "encoding": "gzip-json-lines",
        "record_count": 0,
        "uncompressed_sha256": hashlib.sha256(b"").hexdigest(),
    }
    with gzip.open(arguments.output_events, "rb") as stream:
        assert stream.read() == b""
    _write_manifest(arguments.output_manifest, evidence)
    assert verify(arguments.output_manifest)["result"] == "FAIL"
