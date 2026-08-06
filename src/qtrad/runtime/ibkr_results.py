"""Create-only Stage 4 IBKR result publication and file-only verification."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import cast
from uuid import UUID

from qtrad.application.ibkr_results import (
    IbkrHistoricalAggregateReplay,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrAttemptStatus,
    IbkrHistoricalCallbackKind,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import IbkrHistoricalPlan, IbkrHistoricalRequest
from qtrad.domain.ibkr_results import (
    HISTORICAL_RESULT_CONTRACT,
    MAX_IBKR_RESULT_BYTES,
    MAX_IBKR_RESULT_CHILDREN,
    MAX_IBKR_RESULT_REQUEST_BYTES,
    REQUEST_RESULT_CONTRACT,
    IbkrHistoricalAggregateResult,
    IbkrHistoricalAttemptEvidence,
    IbkrHistoricalCallbackEvidence,
    IbkrHistoricalChildReference,
    IbkrHistoricalCompletionEvidence,
    IbkrHistoricalEvidenceDisposition,
    IbkrHistoricalRequestResult,
    IbkrHistoricalResultArtifact,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.runtime.ibkr_historical import load_ibkr_historical_plan

_MANIFEST_NAME = "manifest.json"
_PLAN_NAME = "plan.json"
_REQUEST_DIRECTORY = "requests"


def write_ibkr_historical_result(
    output_directory: Path,
    artifact: IbkrHistoricalResultArtifact,
) -> Path:
    """Publish one bounded result directory without replacing any existing bytes."""

    if artifact.plan_bytes != canonical_json_bytes(artifact.plan.as_json_value()):
        raise ValueError("IBKR published plan bytes are not canonical")
    _prepare_output_directory(output_directory)
    request_bytes = {
        f"requests/{result.request_sha256}.json": canonical_json_bytes(result.as_json_value())
        for result in artifact.request_results
    }
    manifest_bytes = canonical_json_bytes(artifact.aggregate.as_json_value())
    files: dict[str, bytes] = {
        _PLAN_NAME: artifact.plan_bytes,
        **request_bytes,
        _MANIFEST_NAME: manifest_bytes,
    }
    if len(files) > MAX_IBKR_RESULT_CHILDREN:
        raise ValueError("IBKR result file closure exceeds its bound")
    if any(
        not payload
        or len(payload)
        > (
            MAX_IBKR_RESULT_REQUEST_BYTES
            if relative_path.startswith(f"{_REQUEST_DIRECTORY}/")
            else MAX_IBKR_RESULT_BYTES
        )
        for relative_path, payload in files.items()
    ):
        raise ValueError("IBKR result file exceeds its bounded size")
    total_bytes = sum(len(payload) for payload in files.values())
    if total_bytes > MAX_IBKR_RESULT_CHILDREN * MAX_IBKR_RESULT_BYTES:
        raise ValueError("IBKR result closure exceeds its byte bound")
    requests_directory = output_directory / _REQUEST_DIRECTORY
    requests_directory.mkdir()
    for relative_path, payload in files.items():
        target = output_directory / relative_path
        target.parent.mkdir(exist_ok=True)
        _write_create_only(target, payload, f"IBKR result file {relative_path}")
    return output_directory / _MANIFEST_NAME


def publish_ibkr_historical_result(
    output_directory: Path,
    artifact: IbkrHistoricalResultArtifact,
) -> Path:
    """Stage, verify, and create-only commit one result directory."""

    destination = _absolute_output_path(output_directory)
    _require_new_output_directory(destination)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=str(destination.parent))
    )
    try:
        staged_manifest = write_ibkr_historical_result(staging, artifact)
        verified = verify_ibkr_historical_result(staged_manifest)
        if verified.aggregate.aggregate_sha256 != artifact.aggregate.aggregate_sha256:
            raise RuntimeError("IBKR result changed between staging and verification")
        if destination.exists():
            raise FileExistsError(f"IBKR result output already exists: {destination}")
        os.rename(staging, destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return destination / _MANIFEST_NAME


class IbkrHistoricalResultStream:
    """Read and replay one Stage 6 request-result child at a time."""

    def __init__(
        self,
        *,
        source_root: Path,
        plan: IbkrHistoricalPlan,
        plan_bytes: bytes,
        aggregate: IbkrHistoricalAggregateResult,
        references_by_path: Mapping[str, IbkrHistoricalChildReference],
    ) -> None:
        self.source_root = source_root
        self.plan = plan
        self.plan_bytes = plan_bytes
        self.aggregate = aggregate
        self._references_by_path = references_by_path
        self.source_files = tuple(sorted({_MANIFEST_NAME, _PLAN_NAME, *references_by_path}))

    def iter_request_results(
        self,
        *,
        request_order: Sequence[IbkrHistoricalRequest] | None = None,
    ) -> Iterator[IbkrHistoricalRequestResult]:
        if request_order is None:
            order = tuple(sorted(self.plan.requests, key=lambda item: item.request_sha256))
        else:
            order = tuple(request_order)
        if len(order) != len(self.plan.requests) or {item.request_sha256 for item in order} != {
            item.request_sha256 for item in self.plan.requests
        }:
            raise ValueError("IBKR result stream request order differs from the plan")
        replay = IbkrHistoricalAggregateReplay(self.plan, self.plan_bytes, self.aggregate)
        for request in order:
            relative_path = f"{_REQUEST_DIRECTORY}/{request.request_sha256}.json"
            reference = self._references_by_path[relative_path]
            child_path = _safe_child(self.source_root, relative_path, "IBKR request-result child")
            child_bytes = _read_bytes(
                child_path, "IBKR request-result child", maximum=MAX_IBKR_RESULT_REQUEST_BYTES
            )
            if sha256_bytes(child_bytes) != reference.bytes_sha256:
                raise ValueError(
                    "IBKR request-result child bytes digest does not match its manifest"
                )
            result = _request_result_from_json(_parse_json(child_bytes, "IBKR request result"))
            if child_bytes != canonical_json_bytes(result.as_json_value()):
                raise ValueError("IBKR request result bytes are not canonical")
            if result.plan_sha256 != self.plan.plan_sha256:
                raise ValueError("IBKR request result belongs to another plan")
            if result.request_sha256 != request.request_sha256:
                raise ValueError("IBKR request result does not match its planned request")
            if result.result_sha256 != reference.semantic_sha256:
                raise ValueError(
                    "IBKR request result semantic identity does not match its manifest"
                )
            replay.accept(request, result)
            yield result
        replay.finish()


def verify_ibkr_historical_result_stream(path: Path) -> IbkrHistoricalResultStream:
    """Verify a Stage 6 closure header and return a one-child-at-a-time reader."""
    manifest_path = _require_file(path, "IBKR result manifest")
    root = manifest_path.parent
    manifest_bytes = _read_bytes(manifest_path, "IBKR aggregate result")
    aggregate = _aggregate_from_json(_parse_json(manifest_bytes, "IBKR aggregate result"))
    if manifest_bytes != canonical_json_bytes(aggregate.as_json_value()):
        raise ValueError("IBKR aggregate result bytes are not canonical")
    if aggregate.plan.path != _PLAN_NAME:
        raise ValueError("IBKR aggregate plan child path is not canonical")
    plan_path = _safe_child(root, aggregate.plan.path, "IBKR plan child")
    plan_bytes = _read_bytes(plan_path, "IBKR published plan")
    if sha256_bytes(plan_bytes) != aggregate.plan.bytes_sha256:
        raise ValueError("IBKR published plan bytes digest does not match its manifest")
    plan = load_ibkr_historical_plan(plan_path)
    if plan_bytes != canonical_json_bytes(plan.as_json_value()):
        raise ValueError("IBKR published plan bytes are not canonical")
    if plan.plan_sha256 != aggregate.plan.semantic_sha256:
        raise ValueError("IBKR published plan semantic identity does not match its manifest")
    if aggregate.runtime_sha256 != plan.runtime_sha256:
        raise ValueError("IBKR aggregate runtime identity differs from its plan")
    if len(aggregate.request_results) > MAX_IBKR_RESULT_CHILDREN:
        raise ValueError("IBKR aggregate child count exceeds its bound")
    request_hashes = {request.request_sha256 for request in plan.requests}
    references_by_path = {reference.path: reference for reference in aggregate.request_results}
    expected_paths = {
        f"{_REQUEST_DIRECTORY}/{request_hash}.json" for request_hash in request_hashes
    }
    if set(references_by_path) != expected_paths:
        raise ValueError("IBKR aggregate child closure differs from its plan")
    for reference in aggregate.request_results:
        if reference.contract != REQUEST_RESULT_CONTRACT:
            raise ValueError("IBKR aggregate request child contract is unsupported")
        _safe_child(root, reference.path, "IBKR request-result child")
    _require_exact_tree(root, {_MANIFEST_NAME, _PLAN_NAME, _REQUEST_DIRECTORY, *expected_paths})
    return IbkrHistoricalResultStream(
        source_root=root,
        plan=plan,
        plan_bytes=plan_bytes,
        aggregate=aggregate,
        references_by_path=references_by_path,
    )


def verify_ibkr_historical_result(path: Path) -> IbkrHistoricalResultArtifact:
    """Verify a result directory from files only; PostgreSQL is never queried."""
    stream = verify_ibkr_historical_result_stream(path)
    results = tuple(stream.iter_request_results())
    return IbkrHistoricalResultArtifact(
        plan=stream.plan,
        plan_bytes=stream.plan_bytes,
        request_results=results,
        aggregate=stream.aggregate,
    )


def _aggregate_from_json(value: Mapping[str, object]) -> IbkrHistoricalAggregateResult:
    _require_exact_keys(
        value,
        {
            "contract",
            "schema_version",
            "plan",
            "runtime_sha256",
            "request_results",
            "coverage_summary",
            "entitlement_summary",
            "aggregate_sha256",
        },
        "IBKR aggregate result",
    )
    if (
        value["contract"] != HISTORICAL_RESULT_CONTRACT
        or value["schema_version"] != IbkrHistoricalAggregateResult.SCHEMA_VERSION
    ):
        raise ValueError("IBKR aggregate result contract or schema version is unsupported")
    request_values = value["request_results"]
    if not isinstance(request_values, list):
        raise ValueError("IBKR aggregate request_results must be an array")
    coverage = _json_object(value["coverage_summary"], "IBKR coverage summary")
    entitlement = _json_object(value["entitlement_summary"], "IBKR entitlement summary")
    return IbkrHistoricalAggregateResult(
        plan=_child_reference(value["plan"], "IBKR aggregate plan"),
        runtime_sha256=_string(value, "runtime_sha256"),
        request_results=tuple(
            _child_reference(item, "IBKR aggregate request child") for item in request_values
        ),
        coverage_summary=coverage,
        entitlement_summary=entitlement,
        aggregate_sha256=_string(value, "aggregate_sha256"),
    )


def _request_result_from_json(value: Mapping[str, object]) -> IbkrHistoricalRequestResult:
    _require_exact_keys(
        value,
        {
            "contract",
            "schema_version",
            "plan_sha256",
            "request_sha256",
            "request_payload",
            "request_status",
            "terminal_disposition",
            "evidence_disposition",
            "selected_attempt_id",
            "attempts",
            "callbacks",
            "completion_markers",
            "accepted_rows",
            "sessions",
            "session_state",
            "acquisition_started_at",
            "acquisition_completed_at",
            "retry_history",
            "error_classification",
            "result_sha256",
        },
        "IBKR request result",
    )
    if (
        value["contract"] != REQUEST_RESULT_CONTRACT
        or value["schema_version"] != IbkrHistoricalRequestResult.SCHEMA_VERSION
    ):
        raise ValueError("IBKR request result contract or schema version is unsupported")
    attempts = _list(value["attempts"], "IBKR request result attempts")
    callbacks = _list(value["callbacks"], "IBKR request result callbacks")
    markers = _list(value["completion_markers"], "IBKR request result completion markers")
    rows = _list(value["accepted_rows"], "IBKR request result accepted rows")
    sessions = _list(value["sessions"], "IBKR request result sessions")
    retry_history = _list(value["retry_history"], "IBKR request result retry history")
    error_value = value["error_classification"]
    error = None if error_value is None else _json_object(error_value, "IBKR error classification")
    result = IbkrHistoricalRequestResult(
        plan_sha256=_string(value, "plan_sha256"),
        request_sha256=_string(value, "request_sha256"),
        request_payload=_json_object(value["request_payload"], "IBKR request payload"),
        request_status=_enum(value["request_status"], IbkrRequestStatus, "request_status"),
        terminal_disposition=_enum(
            value["terminal_disposition"], IbkrTerminalDisposition, "terminal_disposition"
        ),
        evidence_disposition=_enum(
            value["evidence_disposition"],
            IbkrHistoricalEvidenceDisposition,
            "evidence_disposition",
        ),
        selected_attempt_id=_uuid(value["selected_attempt_id"], "selected_attempt_id"),
        attempts=tuple(_attempt_from_json(item) for item in attempts),
        callbacks=tuple(_callback_from_json(item) for item in callbacks),
        completion_markers=tuple(_completion_from_json(item) for item in markers),
        accepted_rows=tuple(_json_object(item, "IBKR accepted row") for item in rows),
        sessions=tuple(_json_object(item, "IBKR session") for item in sessions),
        session_state=_nullable_string(value, "session_state"),
        acquisition_started_at=_datetime(value, "acquisition_started_at"),
        acquisition_completed_at=_datetime(value, "acquisition_completed_at"),
        retry_history=tuple(_json_object(item, "IBKR retry history") for item in retry_history),
        error_classification=error,
        result_sha256=_string(value, "result_sha256"),
    )
    if result.as_json_value() != dict(value):
        raise ValueError("IBKR request result contains non-canonical fields")
    return result


def _attempt_from_json(value: object) -> IbkrHistoricalAttemptEvidence:
    mapping = _mapping(value, "IBKR attempt evidence")
    _require_exact_keys(
        mapping,
        {
            "attempt_id",
            "plan_sha256",
            "request_sha256",
            "attempt_ordinal",
            "connection_session_id",
            "provider_request_id",
            "connection_generation",
            "started_at",
            "status",
            "terminal_at",
            "terminal_disposition",
            "detail",
        },
        "IBKR attempt evidence",
    )
    terminal_disposition = mapping["terminal_disposition"]
    return IbkrHistoricalAttemptEvidence(
        attempt_id=_uuid(mapping["attempt_id"], "attempt_id"),
        plan_sha256=_string(mapping, "plan_sha256"),
        request_sha256=_string(mapping, "request_sha256"),
        attempt_ordinal=_integer(mapping, "attempt_ordinal"),
        connection_session_id=_uuid(mapping["connection_session_id"], "connection_session_id"),
        provider_request_id=_integer(mapping, "provider_request_id"),
        connection_generation=_integer(mapping, "connection_generation"),
        started_at=_datetime(mapping, "started_at"),
        status=_enum(mapping["status"], IbkrAttemptStatus, "status"),
        terminal_at=None if mapping["terminal_at"] is None else _datetime(mapping, "terminal_at"),
        terminal_disposition=(
            None
            if terminal_disposition is None
            else _enum(terminal_disposition, IbkrTerminalDisposition, "terminal_disposition")
        ),
        detail=_nullable_string(mapping, "detail"),
    )


def _callback_from_json(value: object) -> IbkrHistoricalCallbackEvidence:
    mapping = _mapping(value, "IBKR callback evidence")
    _require_exact_keys(
        mapping,
        {
            "callback_id",
            "attempt_id",
            "connection_session_id",
            "provider_request_id",
            "connection_generation",
            "sequence",
            "kind",
            "received_at",
            "payload",
            "closure_eligible",
        },
        "IBKR callback evidence",
    )
    closure_eligible = mapping["closure_eligible"]
    if not isinstance(closure_eligible, bool):
        raise ValueError("IBKR callback closure_eligible must be boolean")
    return IbkrHistoricalCallbackEvidence(
        callback_id=_integer(mapping, "callback_id"),
        attempt_id=_uuid(mapping["attempt_id"], "attempt_id"),
        connection_session_id=_uuid(mapping["connection_session_id"], "connection_session_id"),
        provider_request_id=_integer(mapping, "provider_request_id"),
        connection_generation=_integer(mapping, "connection_generation"),
        sequence=_integer(mapping, "sequence"),
        kind=_enum(mapping["kind"], IbkrHistoricalCallbackKind, "kind"),
        received_at=_datetime(mapping, "received_at"),
        payload=_json_object(mapping["payload"], "IBKR callback payload"),
        closure_eligible=closure_eligible,
    )


def _completion_from_json(value: object) -> IbkrHistoricalCompletionEvidence:
    mapping = _mapping(value, "IBKR completion evidence")
    _require_exact_keys(
        mapping,
        {
            "marker_id",
            "attempt_id",
            "connection_session_id",
            "provider_request_id",
            "connection_generation",
            "sequence",
            "completed_at",
            "raw_midpoint_bar_callback_count",
            "raw_schedule_callback_count",
            "closure_eligible",
            "payload",
        },
        "IBKR completion evidence",
    )
    closure_eligible = mapping["closure_eligible"]
    if not isinstance(closure_eligible, bool):
        raise ValueError("IBKR completion closure_eligible must be boolean")
    return IbkrHistoricalCompletionEvidence(
        marker_id=_integer(mapping, "marker_id"),
        attempt_id=_uuid(mapping["attempt_id"], "attempt_id"),
        connection_session_id=_uuid(mapping["connection_session_id"], "connection_session_id"),
        provider_request_id=_integer(mapping, "provider_request_id"),
        connection_generation=_integer(mapping, "connection_generation"),
        sequence=_integer(mapping, "sequence"),
        completed_at=_datetime(mapping, "completed_at"),
        raw_midpoint_bar_callback_count=_integer(mapping, "raw_midpoint_bar_callback_count"),
        raw_schedule_callback_count=_integer(mapping, "raw_schedule_callback_count"),
        closure_eligible=closure_eligible,
        payload=_json_object(mapping["payload"], "IBKR completion payload"),
    )


def _child_reference(value: object, field: str) -> IbkrHistoricalChildReference:
    mapping = _mapping(value, field)
    _require_exact_keys(
        mapping,
        {"path", "contract", "semantic_sha256", "bytes_sha256"},
        field,
    )
    return IbkrHistoricalChildReference(
        path=_string(mapping, "path"),
        contract=_string(mapping, "contract"),
        semantic_sha256=_string(mapping, "semantic_sha256"),
        bytes_sha256=_string(mapping, "bytes_sha256"),
    )


def _prepare_output_directory(path: Path) -> None:
    current = _absolute_output_path(path)
    if current.exists():
        if not current.is_dir():
            raise FileExistsError(f"IBKR result output is not a directory: {path}")
        if any(current.iterdir()):
            raise FileExistsError(f"IBKR result output directory is not empty: {path}")
        return
    if not current.parent.is_dir():
        raise FileNotFoundError(
            f"IBKR result output parent directory does not exist: {current.parent}"
        )
    current.mkdir()


def _absolute_output_path(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError(f"IBKR result output path escapes its root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"IBKR result output path contains a symlink: {path}")
    return current


def _require_new_output_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise FileExistsError(f"IBKR result output is not a directory: {path}")
        raise FileExistsError(f"IBKR result output already exists: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(
            f"IBKR result output parent directory does not exist: {path.parent}"
        )


def _write_create_only(path: Path, payload: bytes, field: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{field} cannot replace a symlink")
    try:
        with path.open("xb") as output:
            output.write(payload)
    except FileExistsError as error:
        raise FileExistsError(f"{field} already exists: {path}") from error


def _require_exact_tree(root: Path, expected_paths: set[str]) -> None:
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"IBKR result tree contains a symlink: {path}")
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_directories.add(relative)
        else:
            raise ValueError(f"IBKR result tree contains a non-regular path: {path}")
    if actual_directories != {_REQUEST_DIRECTORY}:
        raise ValueError("IBKR result directory closure contains an unexpected directory")
    expected_files = expected_paths - {_REQUEST_DIRECTORY}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unexpected = sorted(actual_files - expected_files)
        raise ValueError(
            f"IBKR result child closure differs from its manifest; missing={missing}, "
            f"unexpected={unexpected}"
        )


def _safe_child(root: Path, relative: str, field: str) -> Path:
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise ValueError(f"{field} path is unsafe: {relative}")
    child = root / relative
    for ancestor in (child, *child.parents):
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise ValueError(f"{field} path contains a symlink: {relative}")
    if child.parent != root and not child.parent.is_dir():
        raise FileNotFoundError(f"{field} parent directory is missing: {relative}")
    return child


def _require_file(path: Path, field: str) -> Path:
    if ".." in path.parts:
        raise ValueError(f"{field} path escapes its root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"{field} path contains a symlink: {path}")
    if not current.is_file():
        raise FileNotFoundError(f"{field} is not a regular file: {path}")
    return current


def _read_bytes(path: Path, field: str, *, maximum: int = MAX_IBKR_RESULT_BYTES) -> bytes:
    path = _require_file(path, field)
    size = path.stat().st_size
    if size <= 0 or size > maximum:
        raise ValueError(f"{field} exceeds its bounded size")
    payload = path.read_bytes()
    if len(payload) != size:
        raise ValueError(f"{field} changed while being read")
    return payload


def _read_json(path: Path, field: str) -> dict[str, object]:
    return _parse_json(_read_bytes(path, field), field)


def _parse_json(payload: bytes, field: str) -> dict[str, object]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{field} is not valid JSON") from error
    if not isinstance(document, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    return dict(cast(Mapping[str, object], document))


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _json_object(value: object, field: str) -> dict[str, JsonValue]:
    mapping = _mapping(value, field)
    return cast(dict[str, JsonValue], dict(mapping))


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return cast(list[object], value)


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    field: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} has unknown or missing fields")


def _string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item


def _nullable_string(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string when present")
    return item


def _integer(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field} must be an integer")
    return item


def _uuid(value: object, field: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError(f"{field} must be a UUID string") from error


def _datetime(value: Mapping[str, object], field: str) -> datetime:
    item = _string(value, field)
    try:
        parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field} must use UTC")
    return parsed


def _enum[T: Enum](value: object, enum_type: type[T], field: str) -> T:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field} has an unsupported value") from error


__all__ = [
    "publish_ibkr_historical_result",
    "verify_ibkr_historical_result",
    "write_ibkr_historical_result",
]
