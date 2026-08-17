"""Create-only confirmatory promotion for one verified IBKR Stage 8 foundation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_foundation import (
    _IBKR_FOUNDATION_PROMOTION_AUTHORITY_TOKEN,
    IBKRFoundationReadinessState,
    VerifiedIbkrFoundationPromotion,
)
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.runtime.ibkr_foundation import (
    authenticate_ibkr_foundation,
    ibkr_foundation_target_opportunity_policy_id,
)
from qtrad.runtime.r2_bundles import atomic_create

_MAX_BYTES = 4 * 1024 * 1024


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _document(path: Path, field: str) -> tuple[Path, bytes, dict[str, object]]:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file():
        raise ValueError(f"{field} must be a regular file")
    payload = resolved.read_bytes()
    if len(payload) > _MAX_BYTES:
        raise ValueError(f"{field} exceeds the 4 MiB limit")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{field} is not valid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return resolved, payload, cast(dict[str, object], value)


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, field: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value or (maximum is not None and len(value) > maximum):
        raise ValueError(f"{field} must be a bounded non-empty string")
    return value


def _sha256(value: object, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lower-case SHA-256")
    return text


def _operator_authorization(
    *,
    authorized_by: str,
    authorized_at: datetime,
    authorization_reference: str,
) -> dict[str, JsonValue]:
    if authorized_at.utcoffset() != timedelta(0):
        raise ValueError("confirmatory promotion authorization time must be UTC")
    return {
        "authorized_by": _text(authorized_by, "promotion authorizer", maximum=200),
        "authorized_at": authorized_at.isoformat(),
        "authorization_reference": _text(
            authorization_reference, "promotion authorization reference", maximum=500
        ),
    }


def _runtime(runtime: Mapping[str, str]) -> dict[str, JsonValue]:
    application = _text(runtime.get("application_identity"), "application identity")
    image = _text(runtime.get("image_identity"), "image identity")
    marker = "+git:"
    image_marker = "+image:"
    if marker not in application or image_marker not in application:
        raise ValueError("application identity does not bind an exact commit and image")
    commit = application.split(marker, 1)[1].split(image_marker, 1)[0]
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("application identity does not contain an exact commit")
    if (
        not image.startswith("sha256:")
        or len(image) != 71
        or any(character not in "0123456789abcdef" for character in image[7:])
    ):
        raise ValueError("runtime image identity is not an immutable SHA-256 digest")
    return {
        "application_commit": commit,
        "application_identity": application,
        "image_identity": image,
    }


def _authority(document: Mapping[str, object]) -> VerifiedIbkrFoundationPromotion:
    stage8 = _mapping(document["stage8"], "promotion Stage 8 binding")
    return VerifiedIbkrFoundationPromotion._create(
        _IBKR_FOUNDATION_PROMOTION_AUTHORITY_TOKEN,
        foundation_bundle_id=_sha256(
            stage8.get("foundation_build_sha256"), "promoted foundation identity"
        ),
        promotion_sha256=_sha256(document.get("promotion_sha256"), "promotion identity"),
    )


_PROMOTION_V3_CONTRACT = "qtrad-ibkr-foundation-confirmatory-promotion-v2"
_PROMOTION_V3_SCHEMA_VERSION = 2
_PROMOTION_V3_VERIFIER_CONTRACT = "qtrad-stage8-confirmatory-promotion-verifier-v3"
_PROMOTION_V3_VERIFIER_VERSION = 2
_PROMOTION_V3_CHECKS = (
    "stage8-receipt-authentication",
    "target-opportunity-policy-authentication",
    "qualifying-readiness",
    "confirmatory-source-class",
    "operator-authorization",
)
_FOUNDATION_V3_CONTRACT = "qtrad-ibkr-historical-foundation-v2"


def _promotion_v3_verifier_identity() -> str:
    return _sha(
        {
            "contract": _PROMOTION_V3_VERIFIER_CONTRACT,
            "version": _PROMOTION_V3_VERIFIER_VERSION,
            "completed_checks": list(_PROMOTION_V3_CHECKS),
        }
    )


def _foundation_v3_bindings(
    foundation: Path,
    receipt: Path,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue], dict[str, JsonValue]]:
    _foundation_path, foundation_bytes, foundation_document = _document(
        foundation, "IBKR foundation"
    )
    _receipt_path, receipt_bytes, receipt_document = _document(receipt, "IBKR foundation receipt")
    if foundation_document.get("contract") != _FOUNDATION_V3_CONTRACT:
        raise ValueError("Stage 8 v3 foundation is required for current promotion")
    payload = _mapping(foundation_document.get("payload"), "Stage 8 foundation payload")
    readiness = cast(dict[str, JsonValue], _mapping(payload.get("readiness"), "Stage 8 readiness"))
    foundation_id = _sha256(foundation_document.get("foundation_id"), "Stage 8 foundation identity")
    closure_id = _sha256(foundation_document.get("closure_id"), "Stage 8 closure identity")
    receipt_id = _sha256(receipt_document.get("verification_id"), "Stage 8 verification identity")
    policy_id = _sha256(
        payload.get("target_opportunity_policy_id"), "Stage 8 target-opportunity policy"
    )
    if policy_id != ibkr_foundation_target_opportunity_policy_id():
        raise ValueError("Stage 8 target-opportunity policy is unsupported")
    stage8: dict[str, JsonValue] = {
        "foundation_id": foundation_id,
        "closure_id": closure_id,
        "foundation_manifest_sha256": hashlib.sha256(foundation_bytes).hexdigest(),
        "verification_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "verification_id": receipt_id,
        "verifier_contract": _text(
            receipt_document.get("verifier_contract"), "Stage 8 verifier contract"
        ),
        "verifier_version": cast(JsonValue, receipt_document.get("verifier_version")),
        "verifier_identity": _sha256(
            receipt_document.get("verifier_identity"), "Stage 8 verifier identity"
        ),
        "evidence_class": _text(receipt_document.get("evidence_class"), "Stage 8 evidence class"),
        "target_opportunity_policy_id": policy_id,
    }
    if (
        stage8["verifier_contract"] != "qtrad-stage8-foundation-semantic-verifier-v3"
        or stage8["verifier_version"] != 2
        or stage8["evidence_class"] != EvidenceClass.IMPLEMENTATION.value
    ):
        raise ValueError("Stage 8 verification receipt is not accepted")
    return (
        stage8,
        readiness,
        {
            "foundation_id": foundation_id,
            "closure_id": closure_id,
            "foundation_manifest_sha256": stage8["foundation_manifest_sha256"],
            "verification_receipt_sha256": stage8["verification_receipt_sha256"],
            "verification_id": receipt_id,
        },
    )


def _v3_authority(
    document: Mapping[str, object],
) -> VerifiedIbkrFoundationPromotion:
    stage8 = _mapping(document["stage8"], "promotion Stage 8 binding")
    return VerifiedIbkrFoundationPromotion._create(
        _IBKR_FOUNDATION_PROMOTION_AUTHORITY_TOKEN,
        foundation_bundle_id=_sha256(stage8.get("foundation_id"), "promoted foundation identity"),
        promotion_sha256=_sha256(document.get("promotion_sha256"), "promotion identity"),
    )


def _authenticate_v3_promotion(
    foundation: Path,
    *,
    receipt: Path,
    promotion: Path,
) -> VerifiedIbkrFoundationPromotion:
    authentication = authenticate_ibkr_foundation(foundation, receipt=receipt)
    stage8, readiness, foundation_ids = _foundation_v3_bindings(foundation, receipt)
    _promotion_path, promotion_bytes, document = _document(promotion, "IBKR confirmatory promotion")
    expected_fields = {
        "contract",
        "schema_version",
        "profile",
        "stage8",
        "readiness",
        "runtime",
        "operator_authorization",
        "verifier_contract",
        "verifier_version",
        "verifier_identity",
        "completed_checks",
        "evidence_class",
        "promotion_sha256",
    }
    if set(document) != expected_fields:
        raise ValueError("IBKR v3 confirmatory promotion fields are not exact")
    identity = dict(document)
    claimed = _sha256(identity.pop("promotion_sha256"), "promotion identity")
    if claimed != _sha(identity):
        raise ValueError("IBKR v3 promotion identity does not match")
    if (
        document["contract"] != _PROMOTION_V3_CONTRACT
        or document["schema_version"] != _PROMOTION_V3_SCHEMA_VERSION
        or document["profile"] != "CONFIRMATORY"
        or document["evidence_class"] != EvidenceClass.CONFIRMATORY.value
        or document["verifier_contract"] != _PROMOTION_V3_VERIFIER_CONTRACT
        or document["verifier_version"] != _PROMOTION_V3_VERIFIER_VERSION
        or document["verifier_identity"] != _promotion_v3_verifier_identity()
        or document["completed_checks"] != list(_PROMOTION_V3_CHECKS)
    ):
        raise ValueError("IBKR v3 promotion contract or verifier is not accepted")
    if _mapping(document["stage8"], "promotion Stage 8 binding") != stage8:
        raise ValueError("IBKR v3 promotion Stage 8 binding changed")
    if _mapping(document["readiness"], "promotion readiness") != readiness:
        raise ValueError("IBKR v3 promotion readiness changed")
    if (
        readiness.get("state") != IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY.value
        or readiness.get("causes") != []
    ):
        raise ValueError("nonqualifying IBKR foundation cannot be promoted")
    if (
        _mapping(document["stage8"], "promotion Stage 8 binding")["evidence_class"]
        != EvidenceClass.IMPLEMENTATION.value
    ):
        raise ValueError("IBKR v3 promotion requires an implementation verification receipt")
    _validate_runtime_and_operator(document)
    if promotion_bytes != _canonical_bytes(document) + b"\n":
        raise ValueError("IBKR v3 confirmatory promotion bytes changed")
    _ = authentication
    _ = foundation_ids
    return _v3_authority(document)


def _validate_runtime_and_operator(document: Mapping[str, object]) -> None:
    runtime = _mapping(document["runtime"], "promotion runtime")
    if set(runtime) != {"application_commit", "application_identity", "image_identity"}:
        raise ValueError("IBKR v3 promotion runtime fields are not exact")
    if _runtime(cast(Mapping[str, str], runtime)) != runtime:
        raise ValueError("IBKR v3 promotion runtime identity is invalid")
    operator = _mapping(document["operator_authorization"], "promotion operator authorization")
    if set(operator) != {"authorized_by", "authorized_at", "authorization_reference"}:
        raise ValueError("IBKR v3 promotion authorization fields are not exact")
    try:
        authorized_at = datetime.fromisoformat(
            _text(operator["authorized_at"], "promotion authorization time")
        )
    except ValueError as error:
        raise ValueError("promotion authorization time is not ISO-8601") from error
    if (
        _operator_authorization(
            authorized_by=_text(operator["authorized_by"], "promotion authorizer"),
            authorized_at=authorized_at,
            authorization_reference=_text(
                operator["authorization_reference"], "promotion authorization reference"
            ),
        )
        != operator
    ):
        raise ValueError("IBKR v3 promotion authorization is invalid")


def _foundation_v3_contract(path: Path) -> bool:
    try:
        _resolved, _bytes, document = _document(path, "IBKR foundation")
    except (FileNotFoundError, ValueError):
        return False
    return document.get("contract") == _FOUNDATION_V3_CONTRACT


def authenticate_ibkr_foundation_promotion(
    foundation: Path,
    *,
    receipt: Path,
    promotion: Path,
) -> VerifiedIbkrFoundationPromotion:
    """Authenticate current Stage 8 v3 promotion authority without replay."""

    if not _foundation_v3_contract(foundation):
        raise ValueError("current Stage 8 v3 promotion is required")
    return _authenticate_v3_promotion(foundation, receipt=receipt, promotion=promotion)


def _require_detached_source() -> None:
    repository = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), "symbolic-ref", "--quiet", "HEAD"),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeError("cannot establish detached source identity") from error
    if result.returncode == 0:
        raise RuntimeError("confirmatory promotion requires a detached source commit")
    if result.returncode != 1:
        raise RuntimeError("cannot establish detached source identity")


def _preflight_v3_promotion_output(output: Path, foundation: Path) -> Path:
    """Validate the operator-selected lexical destination before authority work."""

    destination = output if output.is_absolute() else Path.cwd() / output
    if any(part in {".", "..", ""} for part in PurePosixPath(destination).parts):
        raise ValueError(f"promotion output path is not canonical: {output}")
    for ancestor in (destination, *destination.parents):
        if ancestor.is_symlink():
            raise ValueError(f"promotion output path contains a symlink: {output}")
        if ancestor != destination and ancestor.exists() and not ancestor.is_dir():
            raise ValueError(f"promotion output ancestor is not a directory: {output}")
    if destination.exists():
        raise FileExistsError(f"create-only promotion output already exists: {destination}")
    foundation_path = foundation if foundation.is_absolute() else Path.cwd() / foundation
    closure_root = foundation_path.parent / f"{foundation_path.name}.children"
    if destination.is_relative_to(closure_root):
        raise ValueError("promotion cannot be written inside the authenticated closure")
    return destination


def _create_v3_promotion(
    foundation: Path,
    *,
    receipt: Path,
    output: Path,
    authorized_by: str,
    authorized_at: datetime,
    authorization_reference: str,
) -> VerifiedIbkrFoundationPromotion:
    resolved_output = _preflight_v3_promotion_output(output, foundation)
    authentication = authenticate_ibkr_foundation(foundation, receipt=receipt)
    stage8, readiness, _foundation_ids = _foundation_v3_bindings(foundation, receipt)
    if (
        readiness.get("state") != IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY.value
        or readiness.get("causes") != []
    ):
        raise ValueError("nonqualifying IBKR foundation cannot be promoted")
    foundation_path = foundation.resolve()
    closure_root = foundation_path.parent / f"{foundation_path.name}.children"
    if resolved_output.is_relative_to(closure_root.resolve()):
        raise ValueError("promotion cannot be written inside the authenticated closure")
    foundation_document = _mapping(_document(foundation, "IBKR foundation")[2], "IBKR foundation")
    if foundation_document.get("source_class") != "IBKR_HISTORICAL_RESEARCH":
        raise ValueError("confirmatory promotion requires the accepted IBKR source class")
    operator = _operator_authorization(
        authorized_by=authorized_by,
        authorized_at=authorized_at,
        authorization_reference=authorization_reference,
    )
    from qtrad.runtime.r2_verification import runtime_identities

    runtime = _runtime(runtime_identities())
    _require_detached_source()
    identity: dict[str, JsonValue] = {
        "contract": _PROMOTION_V3_CONTRACT,
        "schema_version": _PROMOTION_V3_SCHEMA_VERSION,
        "profile": "CONFIRMATORY",
        "stage8": stage8,
        "readiness": readiness,
        "runtime": runtime,
        "operator_authorization": operator,
        "verifier_contract": _PROMOTION_V3_VERIFIER_CONTRACT,
        "verifier_version": _PROMOTION_V3_VERIFIER_VERSION,
        "verifier_identity": _promotion_v3_verifier_identity(),
        "completed_checks": list(_PROMOTION_V3_CHECKS),
        "evidence_class": EvidenceClass.CONFIRMATORY.value,
    }
    document = {**identity, "promotion_sha256": _sha(identity)}
    atomic_create(resolved_output, _canonical_bytes(document) + b"\n")
    _ = authentication
    return _v3_authority(document)


def create_ibkr_foundation_confirmatory_promotion(
    foundation: Path,
    *,
    receipt: Path,
    output: Path,
    authorized_by: str,
    authorized_at: datetime,
    authorization_reference: str,
) -> VerifiedIbkrFoundationPromotion:
    """Create current Stage 8 confirmatory authority without semantic replay."""

    if not _foundation_v3_contract(foundation):
        raise ValueError("current confirmatory promotion requires a Stage 8 v3 foundation")
    return _create_v3_promotion(
        foundation,
        receipt=receipt,
        output=output,
        authorized_by=authorized_by,
        authorized_at=authorized_at,
        authorization_reference=authorization_reference,
    )


__all__ = [
    "authenticate_ibkr_foundation_promotion",
    "create_ibkr_foundation_confirmatory_promotion",
]
