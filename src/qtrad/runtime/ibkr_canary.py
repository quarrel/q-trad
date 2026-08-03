"""Create-only runtime boundary for Stage 5 IBKR canary evidence."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

from qtrad.application.ibkr_canary import (
    IBKR_CANARY_CONTRACT,
    IBKR_CANARY_SCHEMA_VERSION,
    IbkrHistoricalCanaryCase,
    IbkrHistoricalCanaryCaseResult,
    IbkrHistoricalCanaryEvidence,
    IbkrHistoricalCanaryRequestResult,
    replay_ibkr_historical_canary_evidence,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import IbkrHistoricalCallbackKind
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.ports.ibkr_historical import IbkrContractReauthentication
from qtrad.runtime import ibkr_historical as runtime

_CANARY_KEYS = {
    "contract",
    "schema_version",
    "runtime_sha256",
    "selection_sha256",
    "started_at",
    "completed_at",
    "reauthentication",
    "cases",
    "stop_reason",
    "evidence_sha256",
}
_REAUTH_KEYS = {
    "request_id",
    "connection_generation",
    "expected",
    "observed",
    "status",
    "error_codes",
    "diagnostics",
}
_CASE_KEYS = {"case", "requests", "status", "stop_reason"}
_REQUEST_RESULT_KEYS = {
    "request",
    "status",
    "callback_count",
    "bar_count",
    "schedule_session_count",
    "error_codes",
    "callbacks",
    "expected_connection_session_id",
    "expected_provider_request_id",
    "expected_connection_generation",
    "detail",
    "stop_reason",
}
_CALLBACK_KEYS = {
    "connection_session_id",
    "provider_request_id",
    "connection_generation",
    "kind",
    "received_at",
    "payload",
}
_MAX_DIAGNOSTIC_LENGTH = 2_000


def write_ibkr_historical_canary_evidence(
    path: Path, evidence: IbkrHistoricalCanaryEvidence
) -> None:
    """Write immutable canary evidence using the shared bounded JSON boundary."""

    runtime._write_create_only(path, evidence.as_json_value(), "IBKR historical canary evidence")


def load_ibkr_historical_canary_evidence(
    path: Path,
) -> IbkrHistoricalCanaryEvidence:
    """Load and replay canonical canary evidence without provider access."""
    document = runtime._read_json_object(path, "IBKR historical canary evidence")
    return replay_ibkr_historical_canary_evidence(_evidence_from_json(document))


def verify_ibkr_historical_canary_evidence(
    path: Path,
    *,
    expected_runtime_sha256: str | None = None,
    expected_selection_sha256: str | None = None,
) -> IbkrHistoricalCanaryEvidence:
    """Verify canonical canary evidence and optional lower-layer hash bindings."""

    evidence = load_ibkr_historical_canary_evidence(path)
    if expected_runtime_sha256 is not None and evidence.runtime_sha256 != expected_runtime_sha256:
        raise ValueError("IBKR canary evidence runtime hash does not match the expected runtime")
    if (
        expected_selection_sha256 is not None
        and evidence.selection_sha256 != expected_selection_sha256
    ):
        raise ValueError(
            "IBKR canary evidence selection hash does not match the expected contract selection"
        )
    return evidence


def _evidence_from_json(
    document: dict[str, object],
) -> IbkrHistoricalCanaryEvidence:
    runtime._require_exact_keys(document, _CANARY_KEYS, "IBKR historical canary evidence")
    if (
        document.get("contract") != IBKR_CANARY_CONTRACT
        or document.get("schema_version") != IBKR_CANARY_SCHEMA_VERSION
    ):
        raise ValueError("IBKR historical canary contract or schema version is unsupported")

    reauthentication_value = document.get("reauthentication")
    cases_value = document.get("cases")
    if not isinstance(reauthentication_value, list) or not isinstance(cases_value, list):
        raise ValueError("IBKR historical canary arrays are invalid")

    evidence = IbkrHistoricalCanaryEvidence(
        runtime_sha256=runtime._string(document, "runtime_sha256"),
        selection_sha256=runtime._string(document, "selection_sha256"),
        started_at=runtime._datetime(document, "started_at"),
        completed_at=runtime._datetime(document, "completed_at"),
        reauthentication=tuple(
            _reauthentication_from_json(value) for value in reauthentication_value
        ),
        cases=tuple(_case_result_from_json(value) for value in cases_value),
        stop_reason=_nullable_diagnostic(document, "stop_reason"),
        evidence_sha256=runtime._string(document, "evidence_sha256"),
    )
    if evidence.as_json_value() != document:
        raise ValueError("IBKR historical canary evidence contains non-canonical fields")
    return evidence


def _reauthentication_from_json(value: object) -> IbkrContractReauthentication:
    item = _mapping(value, "IBKR canary reauthentication")
    runtime._require_exact_keys(item, _REAUTH_KEYS, "IBKR canary reauthentication")
    observed = item.get("observed")
    if not isinstance(observed, list):
        raise ValueError("IBKR canary reauthentication observed value must be an array")
    return IbkrContractReauthentication(
        request_id=runtime._integer(item, "request_id"),
        connection_generation=runtime._integer(item, "connection_generation"),
        expected=runtime._fingerprint_from_json(item.get("expected")),
        observed=tuple(runtime._fingerprint_from_json(observed_item) for observed_item in observed),
        status=runtime._string(item, "status"),
        error_codes=_non_negative_integer_tuple(item.get("error_codes"), "error_codes"),
        diagnostics=_bounded_string_tuple(item.get("diagnostics"), "diagnostics"),
    )


def _case_result_from_json(value: object) -> IbkrHistoricalCanaryCaseResult:
    item = _mapping(value, "IBKR canary case result")
    runtime._require_exact_keys(item, _CASE_KEYS, "IBKR canary case result")
    request_values = item.get("requests")
    if not isinstance(request_values, list):
        raise ValueError("IBKR canary case requests must be an array")
    return IbkrHistoricalCanaryCaseResult(
        case=_case_from_json(item.get("case")),
        requests=tuple(_request_result_from_json(request) for request in request_values),
        status=runtime._string(item, "status"),
        stop_reason=_nullable_diagnostic(item, "stop_reason"),
    )


def _case_from_json(value: object) -> IbkrHistoricalCanaryCase:
    item = _mapping(value, "IBKR canary case")
    expected = {
        "group",
        "instrument_id",
        "fingerprint",
        "duration",
        "interval_start",
        "interval_end",
    }
    runtime._require_exact_keys(item, expected, "IBKR canary case")
    try:
        group = AssetClass(runtime._string(item, "group"))
    except ValueError as error:
        raise ValueError("IBKR canary case group is invalid") from error
    return IbkrHistoricalCanaryCase(
        group=group,
        instrument_id=InstrumentId(runtime._string(item, "instrument_id")),
        fingerprint=runtime._fingerprint_from_json(item.get("fingerprint")),
        duration=runtime._string(item, "duration"),
        interval_start=runtime._datetime(item, "interval_start"),
        interval_end=runtime._datetime(item, "interval_end"),
    )


def _request_result_from_json(value: object) -> IbkrHistoricalCanaryRequestResult:
    item = _mapping(value, "IBKR canary request result")
    runtime._require_exact_keys(item, _REQUEST_RESULT_KEYS, "IBKR canary request result")
    callbacks = item.get("callbacks")
    if not isinstance(callbacks, list):
        raise ValueError("IBKR canary callbacks must be an array")
    return IbkrHistoricalCanaryRequestResult(
        request=runtime._historical_request_from_json(item.get("request")),
        status=runtime._string(item, "status"),
        callback_count=runtime._integer(item, "callback_count"),
        bar_count=runtime._integer(item, "bar_count"),
        schedule_session_count=runtime._integer(item, "schedule_session_count"),
        error_codes=_non_negative_integer_tuple(item.get("error_codes"), "error_codes"),
        callbacks=tuple(_callback_from_json(value) for value in callbacks),
        expected_connection_session_id=_nullable_uuid(item, "expected_connection_session_id"),
        expected_provider_request_id=_nullable_integer(item, "expected_provider_request_id"),
        expected_connection_generation=_nullable_integer(item, "expected_connection_generation"),
        detail=_nullable_diagnostic(item, "detail"),
        stop_reason=_nullable_diagnostic(item, "stop_reason"),
    )


def _nullable_uuid(value: Mapping[str, object], field: str) -> UUID | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{field} must be a canonical UUID or null")
    try:
        parsed = UUID(item)
    except ValueError as error:
        raise ValueError(f"{field} must be a canonical UUID or null") from error
    if str(parsed) != item:
        raise ValueError(f"{field} must be a canonical UUID or null")
    return parsed


def _nullable_integer(value: Mapping[str, object], field: str) -> int | None:
    item = value.get(field)
    if item is None:
        return None
    return runtime._integer(value, field)


def _callback_from_json(value: object) -> dict[str, JsonValue]:
    item = _mapping(value, "IBKR canary callback")
    runtime._require_exact_keys(item, _CALLBACK_KEYS, "IBKR canary callback")
    session_text = runtime._string(item, "connection_session_id")
    try:
        session_id = UUID(session_text)
    except ValueError as error:
        raise ValueError("IBKR canary callback session ID is invalid") from error
    if str(session_id) != session_text:
        raise ValueError("IBKR canary callback session ID is not canonical")
    provider_request_id = runtime._integer(item, "provider_request_id")
    generation = runtime._integer(item, "connection_generation")
    if provider_request_id <= 0 or generation <= 0:
        raise ValueError("IBKR canary callback identity is invalid")
    kind_text = runtime._string(item, "kind")
    try:
        IbkrHistoricalCallbackKind(kind_text)
    except ValueError as error:
        raise ValueError("IBKR canary callback kind is invalid") from error
    payload = _mapping(item.get("payload"), "IBKR canary callback payload")
    if any(key.lower() in {"message", "errorstring"} for key in payload):
        raise ValueError("IBKR canary callback contains raw provider diagnostic text")
    return {
        "connection_session_id": session_text,
        "provider_request_id": provider_request_id,
        "connection_generation": generation,
        "kind": kind_text,
        "received_at": runtime._datetime(item, "received_at").isoformat().replace("+00:00", "Z"),
        "payload": dict(cast(Mapping[str, JsonValue], payload)),
    }


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(cast(Mapping[str, object], value))


def _nullable_diagnostic(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if (
        not isinstance(item, str)
        or not item
        or len(item) > _MAX_DIAGNOSTIC_LENGTH
        or any(character in item for character in "\r\n\x00")
    ):
        raise ValueError(f"{field} must be a bounded diagnostic or null")
    return item


def _bounded_string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array of strings")
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > _MAX_DIAGNOSTIC_LENGTH
            or any(character in item for character in "\r\n\x00")
        ):
            raise ValueError(f"{field} must contain bounded strings")
        result.append(item)
    return tuple(result)


def _non_negative_integer_tuple(value: object, field: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array of integers")
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"{field} must contain non-negative integers")
        result.append(item)
    return tuple(result)


__all__ = [
    "load_ibkr_historical_canary_evidence",
    "verify_ibkr_historical_canary_evidence",
    "write_ibkr_historical_canary_evidence",
]
