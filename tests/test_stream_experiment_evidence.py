from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

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
    "zero_qtrad_drops": True,
    "zero_lightstreamer_loss": True,
    "zero_subscription_errors": True,
    "zero_server_errors": True,
    "shutdown_verified": True,
    "provider_operations_completed": True,
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
            {"sequence": 1, "kind": "SUBSCRIBED"},
            {"sequence": 3, "kind": "UPDATE"},
        ],
    )
    evidence: dict[str, object] = {
        "schema_version": 1,
        "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
        "result": "FAIL",
        "checks": {**_CONTRAST_CHECKS, "no_queue_drops": False},
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
    count, digest = _write_events(events, [{"sequence": 1, "kind": "UPDATE"}])
    evidence: dict[str, object] = {
        "schema_version": 1,
        "experiment": "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST",
        "result": "PASS",
        "checks": _CONTRAST_CHECKS,
        "event_stream": {
            "path": events.name,
            "encoding": "gzip-json-lines",
            "record_count": count,
            "uncompressed_sha256": digest,
        },
    }
    write_contrast(manifest, evidence)
    _write_events(events, [{"sequence": 1, "kind": "SERVER_ERROR"}])

    with pytest.raises(ValueError, match="event stream hash"):
        verify(manifest)


def test_verifies_recovery_manifest_without_event_stream(tmp_path: Path) -> None:
    manifest = tmp_path / "recovery.json"
    evidence: dict[str, object] = {
        "schema_version": 1,
        "experiment": "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY",
        "result": "PASS",
        "checks": _RECOVERY_CHECKS,
    }
    write_recovery(manifest, evidence)

    result = verify(manifest)

    assert result["verified"] is True
    assert result["result"] == "PASS"


def test_rejects_manifest_tampering_and_event_path_escape(tmp_path: Path) -> None:
    recovery = tmp_path / "recovery.json"
    evidence: dict[str, object] = {
        "schema_version": 1,
        "experiment": "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY",
        "result": "PASS",
        "checks": _RECOVERY_CHECKS,
    }
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
    write_recovery(
        manifest,
        {
            "schema_version": 1,
            "experiment": "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY",
            "result": "PASS",
            "checks": {**_RECOVERY_CHECKS, "disconnect_recovered": False},
        },
    )

    with pytest.raises(ValueError, match="does not agree"):
        verify(manifest)


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
