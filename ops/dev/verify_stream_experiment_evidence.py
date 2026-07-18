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


def _boolean(value: Mapping[str, object], key: str) -> bool:
    candidate = value[key]
    if not isinstance(candidate, bool):
        raise TypeError(f"{key} must be a boolean")
    return candidate


def _positive_integer_value(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _rate_evidence_valid(value: Mapping[str, object]) -> bool:
    published_trading = value["published_trading_requests_per_minute"]
    published_non_trading = value["published_non_trading_requests_per_minute"]
    effective_trading = value["effective_trading_requests_per_minute"]
    effective_non_trading = value["effective_non_trading_requests_per_minute"]
    positive = (
        _positive_integer_value(published_trading)
        and _positive_integer_value(published_non_trading)
        and _positive_integer_value(effective_trading)
        and _positive_integer_value(effective_non_trading)
    )
    if not positive:
        return False
    return (
        cast(int, effective_trading) == cast(int, published_trading) - 2
        and cast(int, effective_non_trading) == cast(int, published_non_trading) - 2
    )


def _verify_ready_recovery_phase(
    phase: Mapping[str, object],
    *,
    expected_name: str,
    expected_reconnects: int,
    expected_reauthentications: int,
    previous_counts: Mapping[str, int],
) -> dict[str, int]:
    if _text(phase, "phase") != expected_name:
        raise ValueError("recovery phases are missing or out of order")
    _text(phase, "observed_at")
    adapter = _object(phase["adapter"], f"{expected_name} adapter")
    if _text(adapter, "health_status") != "HEALTHY":
        raise ValueError(f"{expected_name} adapter is not healthy")
    if not _boolean(adapter, "stream_client_present") or not _boolean(
        adapter, "rest_service_present"
    ):
        raise ValueError(f"{expected_name} lacks active client/session evidence")
    if _integer(adapter, "provider_worker_count") != 0 or _boolean(
        adapter, "reconnect_task_present"
    ):
        raise ValueError(f"{expected_name} has incomplete provider operations")
    if (
        _integer(adapter, "reconnects") != expected_reconnects
        or _integer(adapter, "rest_reauthentications") != expected_reauthentications
    ):
        raise ValueError(f"{expected_name} has incorrect recovery counters")
    if not _rate_evidence_valid(adapter):
        raise ValueError(f"{expected_name} lacks validated provider rate evidence")
    for key in ("expected_subscriptions", "subscribed_subscriptions", "updated_subscriptions"):
        if _integer(adapter, key) != 7:
            raise ValueError(f"{expected_name} lacks seven-channel readiness")
    if not _boolean(adapter, "heartbeat_subscribed") or not _boolean(
        adapter, "heartbeat_transport_current"
    ):
        raise ValueError(f"{expected_name} lacks current heartbeat evidence")
    if _integer(adapter, "heartbeat_events") == 0:
        raise ValueError(f"{expected_name} has no heartbeat event")
    _text(adapter, "heartbeat_frequency")
    frequencies = _object(adapter["frequency_evidence"], f"{expected_name} frequencies")
    fresh_times = _object(adapter["fresh_subscription_times"], f"{expected_name} freshness")
    if len(frequencies) != 7 or len(fresh_times) != 7:
        raise ValueError(f"{expected_name} lacks per-channel frequency/freshness evidence")
    if set(frequencies) != set(fresh_times):
        raise ValueError(f"{expected_name} frequency and freshness channels differ")
    for value in frequencies.values():
        if not isinstance(value, str) or not value:
            raise TypeError(f"{expected_name} has malformed frequency evidence")
    for value in fresh_times.values():
        if not isinstance(value, str) or not value:
            raise TypeError(f"{expected_name} has malformed freshness evidence")
    for key in (
        "lightstreamer_lost_updates",
        "qtrad_dropped_records",
        "subscription_errors",
        "server_errors",
    ):
        if _integer(adapter, key) != 0:
            raise ValueError(f"{expected_name} contains loss or error evidence")
    if _boolean(adapter, "abandoned_provider_operation"):
        raise ValueError(f"{expected_name} abandoned a provider operation")

    counts_raw = _object(phase["record_counts"], f"{expected_name} record_counts")
    if len(counts_raw) != 7:
        raise ValueError(f"{expected_name} record counts do not cover seven instruments")
    counts = {instrument: _integer(counts_raw, instrument) for instrument in counts_raw}
    if previous_counts and set(counts) != set(previous_counts):
        raise ValueError("recovery phase instrument sets differ")
    if any(count <= previous_counts.get(instrument, 0) for instrument, count in counts.items()):
        raise ValueError(f"{expected_name} lacks fresh records for every instrument")
    return counts


def _verify_recovery(
    manifest: Mapping[str, object], checks: Mapping[str, object]
) -> dict[str, object]:
    phases_value = manifest["phases"]
    if not isinstance(phases_value, list):
        raise TypeError("recovery phases must be a JSON array")
    expected = (
        ("INITIAL_READY", 0, 0),
        ("DISCONNECT_RECOVERED", 1, 0),
        ("INVALID_TOKEN_RECOVERED", 2, 1),
    )
    if len(phases_value) > len(expected):
        raise ValueError("recovery evidence contains unexpected phases")
    previous_counts: dict[str, int] = {}
    observed_names: list[str] = []
    for index, value in enumerate(phases_value):
        phase = _object(value, f"recovery phase {index + 1}")
        name, reconnects, reauthentications = expected[index]
        previous_counts = _verify_ready_recovery_phase(
            phase,
            expected_name=name,
            expected_reconnects=reconnects,
            expected_reauthentications=reauthentications,
            previous_counts=previous_counts,
        )
        observed_names.append(name)

    final = _object(manifest["final_adapter"], "final_adapter")
    shutdown = _object(manifest["shutdown"], "shutdown")
    rates_observed = _rate_evidence_valid(final)
    shutdown_verified = (
        _text(final, "health_status") == "STOPPED"
        and not _boolean(final, "stream_client_present")
        and not _boolean(final, "rest_service_present")
        and _integer(final, "provider_worker_count") == 0
        and not _boolean(final, "reconnect_task_present")
        and _boolean(shutdown, "consumer_created")
        and _boolean(shutdown, "consumer_done")
        and not _boolean(shutdown, "consumer_error")
    )
    derived = {
        "initial_ready": "INITIAL_READY" in observed_names,
        "disconnect_recovered": "DISCONNECT_RECOVERED" in observed_names,
        "invalid_token_recovered": "INVALID_TOKEN_RECOVERED" in observed_names,
        "exact_reconnect_count": _integer(final, "reconnects") == 2,
        "exact_rest_reauthentication_count": _integer(final, "rest_reauthentications") == 1,
        "provider_rate_limits_observed": rates_observed,
        "zero_qtrad_drops": _integer(final, "qtrad_dropped_records") == 0,
        "zero_lightstreamer_loss": _integer(final, "lightstreamer_lost_updates") == 0,
        "zero_subscription_errors": _integer(final, "subscription_errors") == 0,
        "zero_server_errors": _integer(final, "server_errors") == 0,
        "shutdown_verified": shutdown_verified,
        "no_abandoned_provider_operation": not _boolean(final, "abandoned_provider_operation"),
        "provider_operations_completed": manifest["failure"] is None,
    }
    if any(checks[key] is not value for key, value in derived.items()):
        raise ValueError("recovery checks do not agree with structured lifecycle evidence")
    return {
        "recovery_phases": observed_names,
        "final_reconnects": _integer(final, "reconnects"),
        "final_rest_reauthentications": _integer(final, "rest_reauthentications"),
    }


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
        detail = _verify_recovery(manifest, checks)
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
