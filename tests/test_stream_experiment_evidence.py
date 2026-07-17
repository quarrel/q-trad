from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from ops.dev.provider_recovery_experiment import _write as write_recovery
from ops.dev.provider_stream_contrast import _write_manifest as write_contrast
from ops.dev.verify_stream_experiment_evidence import verify

_CONTRAST_CHECKS = {
    "all_channels_data_ready": True,
    "all_channels_current_at_stop": True,
    "all_applied_frequencies_observed": True,
    "transport_connected": True,
    "no_queue_drops": True,
    "no_lightstreamer_lost_updates": True,
    "no_subscription_errors": True,
    "no_server_errors": True,
    "no_unexplained_discrepancies": True,
    "shutdown_verified": True,
    "unsubscriptions_verified": True,
    "logout_completed": True,
    "http_session_close_completed": True,
    "provider_workers_terminated": True,
    "provider_operation_completed": True,
}
_RECOVERY_CHECKS = {
    "initial_ready": True,
    "disconnect_recovered": True,
    "invalid_token_recovered": True,
    "exact_reconnect_count": True,
    "exact_rest_reauthentication_count": True,
    "provider_rate_limits_observed": True,
    "zero_qtrad_drops": True,
    "zero_lightstreamer_loss": True,
    "zero_subscription_errors": True,
    "zero_server_errors": True,
    "shutdown_verified": True,
    "no_abandoned_provider_operation": True,
    "provider_operations_completed": True,
}


def _recovery_snapshot(
    *, reconnects: int, reauthentications: int, running: bool
) -> dict[str, object]:
    epics = [f"epic-{index}" for index in range(7)]
    return {
        "health_status": "HEALTHY" if running else "STOPPED",
        "stream_client_present": running,
        "rest_service_present": running,
        "provider_worker_count": 0,
        "reconnect_task_present": False,
        "reconnects": reconnects,
        "rest_reauthentications": reauthentications,
        "published_trading_requests_per_minute": 9,
        "published_non_trading_requests_per_minute": 25,
        "effective_trading_requests_per_minute": 7,
        "effective_non_trading_requests_per_minute": 23,
        "expected_subscriptions": 7 if running else 0,
        "subscribed_subscriptions": 7 if running else 0,
        "updated_subscriptions": 7 if running else 0,
        "fresh_subscription_times": {epic: "2026-07-17T00:00:00Z" for epic in epics}
        if running
        else {},
        "frequency_evidence": {epic: "unlimited" for epic in epics} if running else {},
        "heartbeat_subscribed": running,
        "heartbeat_transport_current": running,
        "heartbeat_events": 1 if running else 0,
        "heartbeat_frequency": "unlimited" if running else None,
        "lightstreamer_lost_updates": 0,
        "qtrad_dropped_records": 0,
        "subscription_errors": 0,
        "server_errors": 0,
        "abandoned_provider_operation": False,
    }


def _recovery_evidence(*, checks: dict[str, bool] | None = None) -> dict[str, object]:
    instruments = [f"instrument-{index}" for index in range(7)]
    phases = []
    for index, (name, reconnects, reauthentications) in enumerate(
        (
            ("INITIAL_READY", 0, 0),
            ("DISCONNECT_RECOVERED", 1, 0),
            ("INVALID_TOKEN_RECOVERED", 2, 1),
        ),
        start=1,
    ):
        phases.append(
            {
                "phase": name,
                "observed_at": f"2026-07-17T00:0{index}:00+00:00",
                "adapter": _recovery_snapshot(
                    reconnects=reconnects,
                    reauthentications=reauthentications,
                    running=True,
                ),
                "record_counts": {instrument: index for instrument in instruments},
            }
        )
    return {
        "schema_version": 1,
        "experiment": "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY",
        "result": "PASS",
        "phases": phases,
        "final_adapter": _recovery_snapshot(reconnects=2, reauthentications=1, running=False),
        "shutdown": {
            "consumer_created": True,
            "consumer_done": True,
            "consumer_error": False,
        },
        "failure": None,
        "checks": checks or dict(_RECOVERY_CHECKS),
    }


def _write_events(path: Path, records: list[dict[str, object]]) -> tuple[int, str]:
    encoded = b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for record in records
    )
    with gzip.GzipFile(filename=str(path), mode="wb", mtime=0) as stream:
        stream.write(encoded)
    return len(records), hashlib.sha256(encoded).hexdigest()


def test_verifies_contrast_manifest_and_event_stream(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl.gz"
    manifest = tmp_path / "manifest.json"
    count, digest = _write_events(
        events,
        [
            {"sequence": 1, "received_at": "2026-07-17T00:00:00Z", "kind": "EXPERIMENT_STARTED"},
            {
                "sequence": 3,
                "received_at": "2026-07-17T00:01:00Z",
                "kind": "EXPERIMENT_STOP_REQUESTED",
            },
        ],
    )
    evidence: dict[str, object] = {
        "schema_version": 1,
        "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
        "result": "FAIL",
        "checks": {**_CONTRAST_CHECKS, "no_queue_drops": False},
        "summary": {"event_attempts": 3, "queue_drops": 1},
        "event_stream": {
            "path": events.name,
            "encoding": "gzip-json-lines",
            "record_count": count,
            "uncompressed_sha256": digest,
        },
    }
    write_contrast(manifest, evidence)

    result = verify(manifest)

    assert result["verified"] is True
    assert result["result"] == "FAIL"
    assert result["event_records"] == 2
    assert result["event_sha256"] == digest


def test_rejects_tampered_contrast_event_stream(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl.gz"
    manifest = tmp_path / "manifest.json"
    count, digest = _write_events(
        events,
        [{"sequence": 1, "received_at": "2026-07-17T00:00:00Z", "kind": "EXPERIMENT_STARTED"}],
    )
    evidence: dict[str, object] = {
        "schema_version": 1,
        "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
        "result": "PASS",
        "checks": _CONTRAST_CHECKS,
        "summary": {"event_attempts": 1, "queue_drops": 0},
        "event_stream": {
            "path": events.name,
            "encoding": "gzip-json-lines",
            "record_count": count,
            "uncompressed_sha256": digest,
        },
    }
    write_contrast(manifest, evidence)
    _write_events(
        events,
        [
            {
                "sequence": 1,
                "received_at": "2026-07-17T00:00:00Z",
                "kind": "SERVER_ERROR",
                "code": 1,
            }
        ],
    )

    with pytest.raises(ValueError, match="event stream hash"):
        verify(manifest)


def test_rejects_unreviewed_event_fields_and_inconsistent_drop_evidence(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl.gz"
    manifest = tmp_path / "manifest.json"
    count, digest = _write_events(
        events,
        [
            {
                "sequence": 1,
                "received_at": "2026-07-17T00:00:00Z",
                "kind": "EXPERIMENT_STARTED",
                "account_id": "must-not-be-retained",
            }
        ],
    )
    evidence: dict[str, object] = {
        "schema_version": 1,
        "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
        "result": "FAIL",
        "checks": {**_CONTRAST_CHECKS, "no_queue_drops": False},
        "summary": {"event_attempts": 1, "queue_drops": 0},
        "event_stream": {
            "path": events.name,
            "encoding": "gzip-json-lines",
            "record_count": count,
            "uncompressed_sha256": digest,
        },
    }
    write_contrast(manifest, evidence)

    with pytest.raises(ValueError, match="unexpected fields"):
        verify(manifest)

    count, digest = _write_events(
        events,
        [{"sequence": 1, "received_at": "2026-07-17T00:00:00Z", "kind": "EXPERIMENT_STARTED"}],
    )
    evidence["summary"] = {"event_attempts": 2, "queue_drops": 0}
    evidence["event_stream"] = {
        "path": events.name,
        "encoding": "gzip-json-lines",
        "record_count": count,
        "uncompressed_sha256": digest,
    }
    del evidence["evidence_sha256"]
    manifest.unlink()
    write_contrast(manifest, evidence)
    with pytest.raises(ValueError, match="do not reconcile"):
        verify(manifest)


def test_verifies_recovery_manifest_without_event_stream(tmp_path: Path) -> None:
    manifest = tmp_path / "recovery.json"
    evidence = _recovery_evidence()
    write_recovery(manifest, evidence)

    result = verify(manifest)

    assert result["verified"] is True
    assert result["result"] == "PASS"


def test_rejects_manifest_tampering_and_event_path_escape(tmp_path: Path) -> None:
    recovery = tmp_path / "recovery.json"
    evidence = _recovery_evidence()
    write_recovery(recovery, evidence)
    stored = json.loads(recovery.read_text())
    stored["result"] = "FAIL"
    recovery.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="self-hash"):
        verify(recovery)

    contrast = tmp_path / "contrast.json"
    event_stream: dict[str, object] = {
        "path": "../events.jsonl.gz",
        "encoding": "gzip-json-lines",
        "record_count": 0,
        "uncompressed_sha256": hashlib.sha256(b"").hexdigest(),
    }
    write_contrast(
        contrast,
        {
            "schema_version": 1,
            "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
            "result": "FAIL",
            "checks": {**_CONTRAST_CHECKS, "no_queue_drops": False},
            "event_stream": event_stream,
        },
    )
    with pytest.raises(ValueError, match="remain beside"):
        verify(contrast)


def test_rejects_result_that_disagrees_with_checks(tmp_path: Path) -> None:
    manifest = tmp_path / "recovery.json"
    evidence = _recovery_evidence()
    evidence["result"] = "FAIL"
    write_recovery(
        manifest,
        evidence,
    )

    with pytest.raises(ValueError, match="does not agree"):
        verify(manifest)


def test_rejects_recovery_checks_without_matching_structured_lifecycle_evidence(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "recovery.json"
    evidence = _recovery_evidence()
    final = cast(dict[str, object], evidence["final_adapter"])
    final["reconnects"] = 1
    write_recovery(manifest, evidence)

    with pytest.raises(ValueError, match="structured lifecycle evidence"):
        verify(manifest)


def test_verifies_structured_recovery_failure_before_initial_readiness(tmp_path: Path) -> None:
    manifest = tmp_path / "recovery-failure.json"
    evidence = _recovery_evidence()
    evidence["phases"] = []
    final = cast(dict[str, object], evidence["final_adapter"])
    final["reconnects"] = 0
    final["rest_reauthentications"] = 0
    for key in (
        "published_trading_requests_per_minute",
        "published_non_trading_requests_per_minute",
        "effective_trading_requests_per_minute",
        "effective_non_trading_requests_per_minute",
    ):
        final[key] = None
    evidence["shutdown"] = {
        "consumer_created": False,
        "consumer_done": False,
        "consumer_error": False,
    }
    evidence["failure"] = {"type": "TimeoutError", "code": None}
    evidence["checks"] = {
        **_RECOVERY_CHECKS,
        "initial_ready": False,
        "disconnect_recovered": False,
        "invalid_token_recovered": False,
        "exact_reconnect_count": False,
        "exact_rest_reauthentication_count": False,
        "provider_rate_limits_observed": False,
        "shutdown_verified": False,
        "provider_operations_completed": False,
    }
    evidence["result"] = "FAIL"
    write_recovery(manifest, evidence)

    result = verify(manifest)

    assert result["verified"] is True
    assert result["result"] == "FAIL"
    assert result["recovery_phases"] == []


def test_rejects_incomplete_check_set(tmp_path: Path) -> None:
    manifest = tmp_path / "recovery.json"
    write_recovery(
        manifest,
        {
            "schema_version": 1,
            "experiment": "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY",
            "result": "PASS",
            "checks": {"initial_ready": True},
        },
    )

    with pytest.raises(ValueError, match="exact v1 check set"):
        verify(manifest)
