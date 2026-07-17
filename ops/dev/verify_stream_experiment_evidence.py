#!/usr/bin/env python3
"""Verify q-trad provider-stream experiment evidence without provider access."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

_CONTRAST = "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST"
_RECOVERY = "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY"
_CONTRAST_CHECKS = {
    "all_channels_data_ready",
    "all_channels_current_at_stop",
    "all_applied_frequencies_observed",
    "transport_connected",
    "no_queue_drops",
    "no_lightstreamer_lost_updates",
    "no_subscription_errors",
    "no_server_errors",
    "no_unexplained_discrepancies",
    "shutdown_verified",
    "unsubscriptions_verified",
    "logout_completed",
    "http_session_close_completed",
    "provider_workers_terminated",
    "provider_operation_completed",
}
_RECOVERY_CHECKS = {
    "initial_ready",
    "disconnect_recovered",
    "invalid_token_recovered",
    "exact_reconnect_count",
    "exact_rest_reauthentication_count",
    "provider_rate_limits_observed",
    "zero_qtrad_drops",
    "zero_lightstreamer_loss",
    "zero_subscription_errors",
    "zero_server_errors",
    "shutdown_verified",
    "no_abandoned_provider_operation",
    "provider_operations_completed",
}
_EVENT_KINDS = {
    "EXPERIMENT_STARTED",
    "EXPERIMENT_STOP_REQUESTED",
    "EXPERIMENT_STOPPED",
    "SUBSCRIBED",
    "UNSUBSCRIBED",
    "UPDATE",
    "LOST_UPDATES",
    "SUBSCRIPTION_ERROR",
    "REAL_MAX_FREQUENCY",
    "TRANSPORT_STATUS",
    "SERVER_ERROR",
}
_EVENT_KEYS = {
    "sequence",
    "received_at",
    "kind",
    "channel",
    "feed",
    "instrument_id",
    "epic",
    "changed_fields",
    "provider_timestamp",
    "code",
    "count",
    "value",
}
_CHANNEL_EVENT_KINDS = {
    "SUBSCRIBED",
    "UNSUBSCRIBED",
    "UPDATE",
    "LOST_UPDATES",
    "SUBSCRIPTION_ERROR",
    "REAL_MAX_FREQUENCY",
}


def _parse_args() -> Path:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    return cast(Path, parser.parse_args().manifest)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{context} must be a JSON object with string keys")
    return cast(dict[str, object], value)


def _text(value: Mapping[str, object], key: str) -> str:
    candidate = value[key]
    if not isinstance(candidate, str) or not candidate:
        raise TypeError(f"{key} must be a non-empty string")
    return candidate


def _integer(value: Mapping[str, object], key: str) -> int:
    candidate = value[key]
    if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
        raise TypeError(f"{key} must be a non-negative integer")
    return candidate


def _verify_event_record(record: Mapping[str, object], context: str) -> int:
    unexpected = set(record) - _EVENT_KEYS
    if unexpected:
        raise ValueError(f"{context} contains unexpected fields: {sorted(unexpected)}")
    sequence = _integer(record, "sequence")
    if sequence == 0:
        raise ValueError(f"{context} sequence must be positive")
    received_at = _text(record, "received_at")
    if not received_at.endswith("Z"):
        raise ValueError(f"{context} received_at must be UTC")
    try:
        parsed_received_at = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{context} received_at must be ISO 8601") from error
    if parsed_received_at.utcoffset() != timedelta(0):
        raise ValueError(f"{context} received_at must be UTC")
    kind = _text(record, "kind")
    if kind not in _EVENT_KINDS:
        raise ValueError(f"{context} has an unsupported kind")
    if kind in _CHANNEL_EVENT_KINDS:
        _text(record, "channel")
        _text(record, "feed")
        for key in ("instrument_id", "epic"):
            candidate = record[key]
            if candidate is not None and (not isinstance(candidate, str) or not candidate):
                raise TypeError(f"{context} {key} must be null or a non-empty string")
    if "changed_fields" in record:
        fields = record["changed_fields"]
        if not isinstance(fields, list) or any(
            not isinstance(field, str) or not field for field in fields
        ):
            raise TypeError(f"{context} changed_fields must be a list of non-empty strings")
    for key in ("provider_timestamp", "value"):
        if key in record:
            candidate = _text(record, key)
            if len(candidate) > 64:
                raise ValueError(f"{context} {key} exceeds its bounded length")
    if kind in {"SUBSCRIPTION_ERROR", "SERVER_ERROR"}:
        _integer(record, "code")
    if kind == "LOST_UPDATES":
        _integer(record, "count")
    if kind in {"REAL_MAX_FREQUENCY", "TRANSPORT_STATUS", "EXPERIMENT_STOPPED"}:
        _text(record, "value")
    return sequence


def _verify_contrast(manifest_path: Path, manifest: Mapping[str, object]) -> dict[str, object]:
    stream = _object(manifest["event_stream"], "event_stream")
    if _text(stream, "encoding") != "gzip-json-lines":
        raise ValueError("unsupported contrast event encoding")
    relative = Path(_text(stream, "path"))
    if relative.is_absolute():
        raise ValueError("contrast event path must be relative to its manifest")
    manifest_parent = manifest_path.resolve().parent
    event_path = (manifest_parent / relative).resolve()
    if event_path.parent != manifest_parent:
        raise ValueError("contrast event path must remain beside its manifest")

    digest = hashlib.sha256()
    count = 0
    previous_sequence = 0
    with gzip.open(event_path, "rb") as handle:
        for line in handle:
            digest.update(line)
            record = _object(json.loads(line), f"event {count + 1}")
            sequence = _verify_event_record(record, f"event {count + 1}")
            if sequence <= previous_sequence:
                raise ValueError("contrast event sequences must be strictly increasing")
            previous_sequence = sequence
            count += 1
    if count != _integer(stream, "record_count"):
        raise ValueError("contrast event record count does not match its manifest")
    if digest.hexdigest() != _text(stream, "uncompressed_sha256"):
        raise ValueError("contrast event stream hash does not match its manifest")
    summary_value = manifest["summary"]
    if summary_value is None:
        if count != 0:
            raise ValueError("contrast evidence without a summary must have an empty event stream")
        event_attempts = 0
        queue_drops = 0
    else:
        summary = _object(summary_value, "summary")
        event_attempts = _integer(summary, "event_attempts")
        queue_drops = _integer(summary, "queue_drops")
        if event_attempts != count + queue_drops:
            raise ValueError(
                "contrast event attempts do not reconcile with records and queue drops"
            )
        if previous_sequence > event_attempts:
            raise ValueError("contrast event sequence exceeds the recorded attempt count")
    return {
        "event_path": event_path.name,
        "event_records": count,
        "event_attempts": event_attempts,
        "queue_drops": queue_drops,
        "event_sha256": digest.hexdigest(),
    }


def verify(manifest_path: Path) -> dict[str, object]:
    manifest = _object(json.loads(manifest_path.read_text(encoding="utf-8")), "manifest")
    if _integer(manifest, "schema_version") != 1:
        raise ValueError("unsupported stream experiment schema version")
    expected_hash = _text(manifest, "evidence_sha256")
    unsigned = dict(manifest)
    del unsigned["evidence_sha256"]
    actual_hash = _canonical_hash(unsigned)
    if actual_hash != expected_hash:
        raise ValueError("experiment manifest self-hash does not match")

    experiment = _text(manifest, "experiment")
    checks = _object(manifest["checks"], "checks")
    if not checks or any(not isinstance(value, bool) for value in checks.values()):
        raise TypeError("experiment checks must be a non-empty object of booleans")
    detail: dict[str, object] = {}
    if experiment == _CONTRAST:
        if set(checks) != _CONTRAST_CHECKS:
            raise ValueError("contrast evidence does not contain the exact v1 check set")
        detail = _verify_contrast(manifest_path, manifest)
    elif experiment == _RECOVERY:
        if set(checks) != _RECOVERY_CHECKS:
            raise ValueError("recovery evidence does not contain the exact v1 check set")
    else:
        raise ValueError(f"unsupported stream experiment: {experiment}")
    result = _text(manifest, "result")
    if result not in {"PASS", "FAIL"}:
        raise ValueError("experiment result must be PASS or FAIL")
    expected_result = "PASS" if all(cast(bool, value) for value in checks.values()) else "FAIL"
    if result != expected_result:
        raise ValueError("experiment result does not agree with its checks")
    return {
        "verified": True,
        "experiment": experiment,
        "result": result,
        "evidence_sha256": actual_hash,
        **detail,
    }


def main() -> None:
    result = verify(_parse_args())
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
