"""Create-only Stage 6 IBKR result publication and file-only verification."""

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

from qtrad.application.ibkr_results import replay_ibkr_historical_aggregate_result
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrAttemptStatus,
    IbkrHistoricalCallbackKind,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import (
    HISTORICAL_PLAN_CONTRACT,
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
    sha256_json,
)
from qtrad.domain.ibkr_results import (
    HISTORICAL_RESULT_CONTRACT,
    HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT,
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
    IbkrHistoricalResultVerificationReceipt,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.runtime.ibkr_historical import load_ibkr_historical_plan

_MANIFEST_NAME = "manifest.json"
_PLAN_NAME = "plan.json"
_REQUEST_DIRECTORY = "requests"
_STAGE6_VERIFIER_VERSION = "1"
_STAGE6_COMPLETED_CHECKS = (
    "canonical_manifest",
    "exact_declared_tree",
    "plan_and_request_closure",
    "request_result_semantics",
    "aggregate_semantics",
)
_MAX_RECEIPT_BYTES = 64 * 1024

# Temporary PR-H4 migration reader for retained Stage 6 v2 closures only.
_LEGACY_HISTORICAL_RESULT_V2_CONTRACT = "qtrad-ibkr-historical-result-v2"
_LEGACY_HISTORICAL_RESULT_V2_SCHEMA_VERSION = 2


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
    """Structurally check, then atomically publish an unverified Stage 6 closure."""

    destination = _absolute_output_path(output_directory)
    _require_new_output_directory(destination)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=str(destination.parent))
    )
    try:
        staged_manifest = write_ibkr_historical_result(staging, artifact)
        staged_stream = verify_ibkr_historical_result_stream(staged_manifest)
        if (
            staged_stream.aggregate.result_id != artifact.aggregate.result_id
            or staged_stream.aggregate.closure_id != artifact.aggregate.closure_id
        ):
            raise RuntimeError("IBKR result changed between structural publication checks")
        if destination.exists():
            raise FileExistsError(f"IBKR result output already exists: {destination}")
        os.rename(staging, destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return destination / _MANIFEST_NAME


class IbkrHistoricalResultStream:
    """Read authenticated Stage 6 request-result children without semantic replay."""

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
        request_ids = tuple(item.request_sha256 for item in order)
        expected_ids = {item.request_sha256 for item in self.plan.requests}
        if (
            len(request_ids) != len(set(request_ids))
            or not set(request_ids).issubset(expected_ids)
        ):
            raise ValueError("IBKR result stream request order differs from the plan")
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
            yield result


def _read_ibkr_historical_result_header(path: Path) -> IbkrHistoricalResultStream:
    """Authenticate Stage 6 manifest and plan metadata without walking result children."""

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
    if any(
        reference.contract != REQUEST_RESULT_CONTRACT for reference in aggregate.request_results
    ):
        raise ValueError("IBKR aggregate request child contract is unsupported")
    return IbkrHistoricalResultStream(
        source_root=root,
        plan=plan,
        plan_bytes=plan_bytes,
        aggregate=aggregate,
        references_by_path=references_by_path,
    )


def _read_legacy_ibkr_historical_result_v2_header(
    path: Path,
    *,
    require_exact_tree: bool = False,
) -> IbkrHistoricalResultStream:
    """Read retained S7.3 v2 metadata until PR-H4 migrates and deletes it.

    This path is deliberately outside the current Stage 6 writer and CLI. It
    authenticates retained v2 metadata and immediate-child declarations without
    replaying Stage 6 semantics; PR-H4 is the deletion trigger.
    """
    manifest_path = _require_file(path, "legacy IBKR v2 result manifest")
    root = manifest_path.parent
    manifest_bytes = _read_bytes(manifest_path, "legacy IBKR v2 aggregate result")
    document = _parse_json(manifest_bytes, "legacy IBKR v2 aggregate result")
    _require_exact_keys(
        document,
        {
            "aggregate_sha256",
            "contract",
            "coverage_summary",
            "entitlement_summary",
            "plan",
            "request_results",
            "runtime_sha256",
            "schema_version",
        },
        "legacy IBKR v2 aggregate result",
    )
    if (
        document["contract"] != _LEGACY_HISTORICAL_RESULT_V2_CONTRACT
        or document["schema_version"] != _LEGACY_HISTORICAL_RESULT_V2_SCHEMA_VERSION
    ):
        raise ValueError("legacy IBKR v2 aggregate result contract is unsupported")
    if manifest_bytes != canonical_json_bytes(cast(dict[str, JsonValue], document)):
        raise ValueError("legacy IBKR v2 aggregate result bytes are not canonical")
    aggregate_sha256 = _require_legacy_sha256(
        document["aggregate_sha256"], "legacy IBKR v2 aggregate identity"
    )
    plan_reference = _child_reference(document["plan"], "legacy IBKR v2 plan reference")
    if plan_reference.path != _PLAN_NAME or plan_reference.contract != HISTORICAL_PLAN_CONTRACT:
        raise ValueError("legacy IBKR v2 plan reference is not canonical")
    plan_path = _safe_child(root, plan_reference.path, "legacy IBKR v2 plan child")
    plan_bytes = _read_bytes(plan_path, "legacy IBKR v2 plan child")
    if sha256_bytes(plan_bytes) != plan_reference.bytes_sha256:
        raise ValueError("legacy IBKR v2 plan bytes digest does not match its reference")
    plan = load_ibkr_historical_plan(plan_path)
    if plan_bytes != canonical_json_bytes(plan.as_json_value()):
        raise ValueError("legacy IBKR v2 plan bytes are not canonical")
    if plan.plan_sha256 != plan_reference.semantic_sha256:
        raise ValueError("legacy IBKR v2 plan semantic identity does not match its reference")
    runtime_sha256 = _require_legacy_sha256(
        document["runtime_sha256"], "legacy IBKR v2 runtime identity"
    )
    if plan.runtime_sha256 != runtime_sha256:
        raise ValueError("legacy IBKR v2 runtime identity differs from its plan")
    raw_request_results = document["request_results"]
    if not isinstance(raw_request_results, list) or not raw_request_results:
        raise ValueError("legacy IBKR v2 request-result references are missing")
    request_results = tuple(
        _child_reference(item, "legacy IBKR v2 request-result reference")
        for item in raw_request_results
    )
    if len(request_results) > MAX_IBKR_RESULT_CHILDREN:
        raise ValueError("legacy IBKR v2 request-result references exceed their bound")
    request_hashes = {request.request_sha256 for request in plan.requests}
    expected_paths = {
        f"{_REQUEST_DIRECTORY}/{request_hash}.json" for request_hash in request_hashes
    }
    references_by_path = {reference.path: reference for reference in request_results}
    if len(references_by_path) != len(request_results) or set(references_by_path) != expected_paths:
        raise ValueError("legacy IBKR v2 request-result closure differs from its plan")
    if any(reference.contract != REQUEST_RESULT_CONTRACT for reference in request_results):
        raise ValueError("legacy IBKR v2 request-result path or contract is unsupported")
    if len({reference.semantic_sha256 for reference in request_results}) != len(request_results):
        raise ValueError("legacy IBKR v2 request-result identities are duplicated")
    if require_exact_tree:
        _require_exact_tree(
            root,
            {_REQUEST_DIRECTORY, _MANIFEST_NAME, _PLAN_NAME, *references_by_path},
        )
    legacy_aggregate = object.__new__(IbkrHistoricalAggregateResult)
    object.__setattr__(legacy_aggregate, "plan", plan_reference)
    object.__setattr__(legacy_aggregate, "runtime_sha256", runtime_sha256)
    object.__setattr__(legacy_aggregate, "request_results", request_results)
    object.__setattr__(
        legacy_aggregate,
        "coverage_summary",
        cast(
            dict[str, JsonValue],
            dict(_mapping(document["coverage_summary"], "legacy coverage summary")),
        ),
    )
    object.__setattr__(
        legacy_aggregate,
        "entitlement_summary",
        cast(
            dict[str, JsonValue],
            dict(_mapping(document["entitlement_summary"], "legacy entitlement summary")),
        ),
    )
    object.__setattr__(legacy_aggregate, "result_id", aggregate_sha256)
    object.__setattr__(legacy_aggregate, "closure_id", aggregate_sha256)
    object.__setattr__(legacy_aggregate, "publication_status", "PUBLISHED_UNVERIFIED")
    object.__setattr__(legacy_aggregate, "aggregate_sha256", aggregate_sha256)
    return IbkrHistoricalResultStream(
        source_root=root,
        plan=plan,
        plan_bytes=plan_bytes,
        aggregate=legacy_aggregate,
        references_by_path=references_by_path,
    )


def verify_ibkr_historical_result_stream(path: Path) -> IbkrHistoricalResultStream:
    """Verify a Stage 6 closure header and return a one-child-at-a-time reader."""

    stream = _read_ibkr_historical_result_header(path)
    for reference in stream.aggregate.request_results:
        _safe_child(stream.source_root, reference.path, "IBKR request-result child")
    _require_exact_tree(
        stream.source_root,
        {_REQUEST_DIRECTORY, *stream.source_files},
    )
    return stream


def verify_ibkr_historical_result(
    path: Path,
    *,
    receipt_output: Path | None = None,
) -> IbkrHistoricalResultArtifact:
    """Independently replay Stage 6 once and optionally persist its receipt."""
    stream = verify_ibkr_historical_result_stream(path)
    receipt_path = (
        _preflight_verification_receipt(path, receipt_output)
        if receipt_output is not None
        else None
    )
    results = tuple(stream.iter_request_results())
    replay_ibkr_historical_aggregate_result(
        stream.plan,
        stream.plan_bytes,
        results,
        stream.aggregate,
    )
    artifact = IbkrHistoricalResultArtifact(
        plan=stream.plan,
        plan_bytes=stream.plan_bytes,
        request_results=results,
        aggregate=stream.aggregate,
    )
    if receipt_path is not None:
        receipt = _build_verification_receipt(path, stream.aggregate, stream.plan)
        _write_create_only(
            receipt_path,
            canonical_json_bytes(receipt.as_json_value()),
            "IBKR result verification receipt",
        )
    return artifact


def authenticate_ibkr_historical_result(
    path: Path,
    *,
    receipt: Path,
) -> IbkrHistoricalResultStream:
    """Authenticate Stage 6 bytes and receipt without semantic or child replay."""
    stream = verify_ibkr_historical_result_stream(path)
    manifest_path = _require_file(path, "IBKR result manifest")
    receipt_path = _require_file(receipt, "IBKR result verification receipt")
    if receipt_path.is_relative_to(manifest_path.parent):
        raise ValueError("IBKR result verification receipt must be outside the immutable closure")
    receipt_bytes = _read_bytes(
        receipt_path,
        "IBKR result verification receipt",
        maximum=_MAX_RECEIPT_BYTES,
    )
    document = _parse_json(receipt_bytes, "IBKR result verification receipt")
    if receipt_bytes != canonical_json_bytes(cast(dict[str, JsonValue], document)):
        raise ValueError("IBKR result verification receipt is not canonical")
    parsed = _verification_receipt_from_json(document)
    expected_manifest_sha256 = sha256_bytes(
        _read_bytes(manifest_path, "IBKR aggregate result")
    )
    expected_verifier_identity = sha256_json(
        {
            "contract": HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT,
            "version": _STAGE6_VERIFIER_VERSION,
            "completed_checks": list(_STAGE6_COMPLETED_CHECKS),
        }
    )
    if (
        parsed.result_id != stream.aggregate.result_id
        or parsed.closure_id != stream.aggregate.closure_id
        or parsed.result_contract != stream.aggregate.CONTRACT
        or parsed.result_schema_version != stream.aggregate.SCHEMA_VERSION
        or parsed.manifest_sha256 != expected_manifest_sha256
        or parsed.plan_semantic_id != stream.aggregate.plan.semantic_sha256
        or parsed.verifier_contract != HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT
        or parsed.verifier_version != _STAGE6_VERIFIER_VERSION
        or parsed.completed_checks != _STAGE6_COMPLETED_CHECKS
        or parsed.verifier_identity != expected_verifier_identity
    ):
        raise ValueError("IBKR result verification receipt binding is not accepted")
    return stream


def _preflight_verification_receipt(manifest: Path, receipt_output: Path) -> Path:
    manifest_path = _require_file(manifest, "IBKR result manifest")
    receipt_path = _absolute_output_path(receipt_output)
    if receipt_path.is_relative_to(manifest_path.parent):
        raise ValueError("IBKR result verification receipt must be outside the immutable closure")
    if receipt_path.exists():
        raise FileExistsError(f"IBKR result verification receipt already exists: {receipt_path}")
    if not receipt_path.parent.is_dir():
        raise FileNotFoundError(
            f"IBKR result verification receipt parent directory does not exist: "
            f"{receipt_path.parent}"
        )
    return receipt_path


def _build_verification_receipt(
    manifest: Path,
    aggregate: IbkrHistoricalAggregateResult,
    plan: IbkrHistoricalPlan,
) -> IbkrHistoricalResultVerificationReceipt:
    manifest_path = _require_file(manifest, "IBKR result manifest")
    manifest_sha256 = sha256_bytes(_read_bytes(manifest_path, "IBKR aggregate result"))
    verifier_identity = sha256_json(
        {
            "contract": HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT,
            "version": _STAGE6_VERIFIER_VERSION,
            "completed_checks": list(_STAGE6_COMPLETED_CHECKS),
        }
    )
    identity = {
        "contract": HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT,
        "result_contract": aggregate.CONTRACT,
        "result_schema_version": aggregate.SCHEMA_VERSION,
        "result_id": aggregate.result_id,
        "closure_id": aggregate.closure_id,
        "manifest_sha256": manifest_sha256,
        "plan_semantic_id": plan.plan_sha256,
        "verifier_contract": HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT,
        "verifier_version": _STAGE6_VERIFIER_VERSION,
        "completed_checks": list(_STAGE6_COMPLETED_CHECKS),
        "verifier_identity": verifier_identity,
    }
    return IbkrHistoricalResultVerificationReceipt(
        result_id=aggregate.result_id,
        closure_id=aggregate.closure_id,
        result_contract=aggregate.CONTRACT,
        result_schema_version=aggregate.SCHEMA_VERSION,
        manifest_sha256=manifest_sha256,
        plan_semantic_id=plan.plan_sha256,
        verifier_contract=HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT,
        verifier_version=_STAGE6_VERIFIER_VERSION,
        completed_checks=_STAGE6_COMPLETED_CHECKS,
        verifier_identity=verifier_identity,
        verification_id=sha256_json(identity),
    )


def _verification_receipt_from_json(
    value: Mapping[str, object],
) -> IbkrHistoricalResultVerificationReceipt:
    _require_exact_keys(
        value,
        {
            "contract",
            "result_contract",
            "result_schema_version",
            "result_id",
            "closure_id",
            "manifest_sha256",
            "plan_semantic_id",
            "verifier_contract",
            "verifier_version",
            "completed_checks",
            "verifier_identity",
            "verification_id",
        },
        "IBKR result verification receipt",
    )
    if value["contract"] != HISTORICAL_RESULT_VERIFICATION_RECEIPT_CONTRACT:
        raise ValueError("IBKR result verification receipt contract is unsupported")
    checks = _list(value["completed_checks"], "IBKR result receipt completed checks")
    if any(not isinstance(item, str) or not item for item in checks):
        raise ValueError("IBKR result receipt completed checks are invalid")
    completed_checks = tuple(cast(str, item) for item in checks)
    receipt = IbkrHistoricalResultVerificationReceipt(
        result_id=_string(value, "result_id"),
        closure_id=_string(value, "closure_id"),
        result_contract=_string(value, "result_contract"),
        result_schema_version=_integer(value, "result_schema_version"),
        manifest_sha256=_string(value, "manifest_sha256"),
        plan_semantic_id=_string(value, "plan_semantic_id"),
        verifier_contract=_string(value, "verifier_contract"),
        verifier_version=_string(value, "verifier_version"),
        completed_checks=completed_checks,
        verifier_identity=_string(value, "verifier_identity"),
        verification_id=_string(value, "verification_id"),
    )
    if receipt.as_json_value() != dict(value):
        raise ValueError("IBKR result verification receipt contains non-canonical fields")
    return receipt


def _aggregate_from_json(value: Mapping[str, object]) -> IbkrHistoricalAggregateResult:
    _require_exact_keys(
        value,
        {
            "contract",
            "schema_version",
            "plan_semantic_id",
            "request_result_semantic_ids",
            "result_id",
            "closure_id",
            "publication_status",
            "plan",
            "runtime_sha256",
            "request_results",
            "coverage_summary",
            "entitlement_summary",
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
    plan_ref = _child_reference(value["plan"], "IBKR aggregate plan")
    request_refs = tuple(
        _child_reference(item, "IBKR aggregate request child") for item in request_values
    )
    if value["plan_semantic_id"] != plan_ref.semantic_sha256:
        raise ValueError("IBKR aggregate semantic plan identity differs from its reference")
    if value["request_result_semantic_ids"] != [
        item.semantic_sha256 for item in request_refs
    ]:
        raise ValueError("IBKR aggregate semantic request identities differ from its references")
    return IbkrHistoricalAggregateResult(
        plan=plan_ref,
        runtime_sha256=_string(value, "runtime_sha256"),
        request_results=request_refs,
        coverage_summary=coverage,
        entitlement_summary=entitlement,
        result_id=_string(value, "result_id"),
        closure_id=_string(value, "closure_id"),
        publication_status=_string(value, "publication_status"),
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


def _require_legacy_sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lower-case SHA-256")
    return value


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
    "IbkrHistoricalResultStream",
    "authenticate_ibkr_historical_result",
    "publish_ibkr_historical_result",
    "verify_ibkr_historical_result",
    "verify_ibkr_historical_result_stream",
    "write_ibkr_historical_result",
]
