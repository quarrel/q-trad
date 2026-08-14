"""Create-only confirmatory promotion for one verified IBKR Stage 8 foundation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_foundation import (
    _IBKR_FOUNDATION_PROMOTION_AUTHORITY_TOKEN,
    IBKRFoundationReadinessState,
    VerifiedIbkrFoundationPromotion,
)
from qtrad.domain.provider_history import PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.runtime.ibkr_foundation import (
    authenticate_ibkr_foundation,
    verify_ibkr_foundation,
)
from qtrad.runtime.provider_history import (
    is_provider_history_v1_verifier_sha256_accepted,
    provider_history_verifier_sha256,
)
from qtrad.runtime.provider_history_v2 import (
    PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT,
    authenticate_provider_history_v2,
    provider_history_v2_verifier_sha256,
    replay_provider_history_v2_stage6,
    verify_provider_history_v2,
)
from qtrad.runtime.r2_bundles import atomic_create

_PROMOTION_CONTRACT = "qtrad-ibkr-foundation-confirmatory-promotion-v1"
_PROMOTION_SCHEMA_VERSION = 1
_PROMOTION_VERIFIER_CONTRACT = "qtrad-stage8-confirmatory-promotion-verifier-v1"
_PROMOTION_VERIFIER_VERSION = 1
_PROMOTION_CHECKS = (
    "clean-immutable-runtime",
    "stage6-result-and-aggregate-replay",
    "stage7-provider-history-independent-replay",
    "stage8-foundation-independent-replay",
    "qualifying-readiness",
    "implementation-receipt-authentication",
)
_PROMOTION_FIELDS = {
    "contract",
    "schema_version",
    "profile",
    "stage6",
    "stage7",
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
_MAX_BYTES = 4 * 1024 * 1024


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def ibkr_foundation_promotion_verifier_identity() -> str:
    """Return the claim-scoped identity of the accepted S8.4 verifier."""

    return _sha(
        {
            "contract": _PROMOTION_VERIFIER_CONTRACT,
            "version": _PROMOTION_VERIFIER_VERSION,
            "completed_checks": list(_PROMOTION_CHECKS),
        }
    )


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


def _bindings(
    foundation: Path,
    receipt: Path,
    authentication: Mapping[str, object],
) -> tuple[dict[str, JsonValue], Path, str]:
    foundation_path, foundation_bytes, foundation_document = _document(
        foundation, "IBKR foundation"
    )
    _receipt_path, receipt_bytes, receipt_document = _document(receipt, "IBKR foundation receipt")
    provider_relative = _text(
        foundation_document.get("provider_history_manifest"),
        "provider-history manifest path",
    )
    provider_path, provider_bytes, provider_document = _document(
        foundation_path.parent / provider_relative,
        "provider-history manifest",
    )
    provider_contract = _text(provider_document.get("contract"), "provider-history contract")
    if provider_contract == PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT:
        provider_verifier_sha256 = provider_history_verifier_sha256()
    elif provider_contract == PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT:
        provider_verifier_sha256 = provider_history_v2_verifier_sha256()
    else:
        raise ValueError("provider-history contract is unsupported for promotion")
    provider_dataset = _mapping(provider_document.get("dataset"), "provider-history dataset")
    source_result = _mapping(provider_document.get("source_result"), "Stage 6 source result")
    stage6: dict[str, JsonValue] = {
        "aggregate_sha256": _sha256(
            provider_dataset.get("aggregate_sha256"), "Stage 6 aggregate identity"
        ),
        "result": cast(JsonValue, source_result),
    }
    stage7: dict[str, JsonValue] = {
        "provider_history_manifest_sha256": hashlib.sha256(provider_bytes).hexdigest(),
        "provider_history_dataset_sha256": _sha256(
            provider_dataset.get("dataset_sha256"), "Stage 7 dataset identity"
        ),
        "plan_sha256": _sha256(provider_dataset.get("plan_sha256"), "Stage 7 plan identity"),
        "runtime_sha256": _sha256(
            provider_dataset.get("runtime_sha256"), "Stage 7 runtime identity"
        ),
        "provider_verifier_sha256": provider_verifier_sha256,
    }
    stage8: dict[str, JsonValue] = {
        "foundation_manifest_sha256": hashlib.sha256(foundation_bytes).hexdigest(),
        "foundation_build_sha256": _sha256(
            foundation_document.get("build_sha256"), "Stage 8 build identity"
        ),
        "verification_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "verification_receipt_id": _sha256(
            receipt_document.get("receipt_sha256"), "Stage 8 receipt identity"
        ),
        "verifier_contract": _text(
            receipt_document.get("verifier_contract"), "Stage 8 verifier contract"
        ),
        "verifier_version": cast(JsonValue, receipt_document.get("verifier_version")),
        "verifier_identity": _sha256(
            receipt_document.get("verifier_identity"), "Stage 8 verifier identity"
        ),
    }
    readiness = cast(JsonValue, authentication.get("readiness"))
    return (
        {
            "stage6": stage6,
            "stage7": stage7,
            "stage8": stage8,
            "readiness": readiness,
        },
        provider_path,
        provider_contract,
    )


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
_PROMOTION_V3_VERIFIER_CONTRACT = "qtrad-stage8-confirmatory-promotion-verifier-v2"
_PROMOTION_V3_CHECKS = (
    "stage8-receipt-authentication",
    "qualifying-readiness",
    "confirmatory-source-class",
    "operator-authorization",
)
_FOUNDATION_V3_CONTRACT = "qtrad-ibkr-historical-foundation-v2"


def _promotion_v3_verifier_identity() -> str:
    return _sha(
        {
            "contract": _PROMOTION_V3_VERIFIER_CONTRACT,
            "version": 1,
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
    }
    if (
        stage8["verifier_contract"] != "qtrad-stage8-foundation-semantic-verifier-v2"
        or stage8["verifier_version"] != 1
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
        or document["verifier_version"] != 1
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
    """Authenticate S8.4 without repeating cumulative semantic replay."""
    if _foundation_v3_contract(foundation):
        return _authenticate_v3_promotion(foundation, receipt=receipt, promotion=promotion)

    authentication = authenticate_ibkr_foundation(foundation, receipt=receipt)
    bindings, _provider_path, _provider_contract = _bindings(foundation, receipt, authentication)
    _promotion_path, promotion_bytes, document = _document(promotion, "IBKR confirmatory promotion")
    if set(document) != _PROMOTION_FIELDS:
        raise ValueError("IBKR confirmatory promotion fields are not exact")
    identity = dict(document)
    claimed = _sha256(identity.pop("promotion_sha256"), "promotion identity")
    if claimed != _sha(identity):
        raise ValueError("IBKR confirmatory promotion identity does not match")
    if (
        document["contract"] != _PROMOTION_CONTRACT
        or document["schema_version"] != _PROMOTION_SCHEMA_VERSION
        or document["profile"] != "CONFIRMATORY"
        or document["evidence_class"] != EvidenceClass.CONFIRMATORY.value
    ):
        raise ValueError("IBKR confirmatory promotion contract is unsupported")
    if (
        document["verifier_contract"] != _PROMOTION_VERIFIER_CONTRACT
        or document["verifier_version"] != _PROMOTION_VERIFIER_VERSION
        or document["verifier_identity"] != ibkr_foundation_promotion_verifier_identity()
        or document["completed_checks"] != list(_PROMOTION_CHECKS)
    ):
        raise ValueError("IBKR confirmatory promotion verifier is not accepted")
    for name in ("stage6", "stage7", "stage8", "readiness"):
        claimed_binding = document[name]
        if name == "stage7":
            stage7 = dict(_mapping(claimed_binding, "promotion Stage 7 binding"))
            verifier_sha256 = _sha256(
                stage7.get("provider_verifier_sha256"),
                "promotion Stage 7 verifier identity",
            )
            expected_stage7 = _mapping(bindings[name], "expected Stage 7 binding")
            expected_verifier = _sha256(
                expected_stage7.get("provider_verifier_sha256"),
                "expected promotion Stage 7 verifier identity",
            )
            if (
                expected_verifier == provider_history_verifier_sha256()
                and is_provider_history_v1_verifier_sha256_accepted(verifier_sha256)
            ):
                stage7["provider_verifier_sha256"] = expected_verifier
            claimed_binding = stage7
        if claimed_binding != bindings[name]:
            raise ValueError(f"IBKR confirmatory promotion {name} binding changed")
    readiness = _mapping(document["readiness"], "promotion readiness")
    if (
        readiness.get("state") != IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY.value
        or readiness.get("causes") != []
    ):
        raise ValueError("nonqualifying IBKR foundation cannot be promoted")
    runtime = _mapping(document["runtime"], "promotion runtime")
    if set(runtime) != {"application_commit", "application_identity", "image_identity"}:
        raise ValueError("IBKR confirmatory promotion runtime fields are not exact")
    if _runtime(cast(Mapping[str, str], runtime)) != runtime:
        raise ValueError("IBKR confirmatory promotion runtime identity is invalid")
    operator = _mapping(document["operator_authorization"], "promotion operator authorization")
    if set(operator) != {"authorized_by", "authorized_at", "authorization_reference"}:
        raise ValueError("IBKR confirmatory promotion authorization fields are not exact")
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
        raise ValueError("IBKR confirmatory promotion authorization is invalid")
    if promotion_bytes != _canonical_bytes(document) + b"\n":
        raise ValueError("IBKR confirmatory promotion bytes changed")
    return _authority(document)


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


def _create_v3_promotion(
    foundation: Path,
    *,
    receipt: Path,
    output: Path,
    authorized_by: str,
    authorized_at: datetime,
    authorization_reference: str,
) -> VerifiedIbkrFoundationPromotion:
    resolved_output = output.resolve()
    if output.is_symlink() or resolved_output.exists():
        raise FileExistsError(f"create-only promotion output already exists: {resolved_output}")
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
        "verifier_version": 1,
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
    provider_history_receipt: Path | None = None,
    output: Path,
    authorized_by: str,
    authorized_at: datetime,
    authorization_reference: str,
    replay_checkpoint_root: Path | None = None,
    workers: int = 4,
) -> VerifiedIbkrFoundationPromotion:
    """Cumulatively replay S6-S8 once and create the S8.4 authority."""
    if _foundation_v3_contract(foundation):
        return _create_v3_promotion(
            foundation,
            receipt=receipt,
            output=output,
            authorized_by=authorized_by,
            authorized_at=authorized_at,
            authorization_reference=authorization_reference,
        )

    resolved_output = output.resolve()
    if output.is_symlink() or resolved_output.exists():
        raise FileExistsError(f"create-only promotion output already exists: {resolved_output}")
    operator = _operator_authorization(
        authorized_by=authorized_by,
        authorized_at=authorized_at,
        authorization_reference=authorization_reference,
    )
    from qtrad.runtime.r2_verification import runtime_identities

    runtime = _runtime(runtime_identities())
    _require_detached_source()
    authentication = authenticate_ibkr_foundation(foundation, receipt=receipt)
    bindings, provider_path, provider_contract = _bindings(foundation, receipt, authentication)
    readiness = _mapping(bindings["readiness"], "promotion readiness")
    if (
        readiness.get("state") != IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY.value
        or readiness.get("causes") != []
    ):
        raise ValueError("nonqualifying IBKR foundation cannot be promoted")
    foundation_path = foundation.resolve()
    immutable_roots = (
        foundation_path.parent / f"{foundation_path.name}.children",
        provider_path.parent,
    )
    if any(resolved_output.is_relative_to(root.resolve()) for root in immutable_roots):
        raise ValueError("promotion cannot be written inside an authenticated closure")
    if provider_contract != PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT:
        raise ValueError("new confirmatory promotions require provider-history v2")
    if provider_history_receipt is None:
        raise ValueError("v2 confirmatory promotion requires its Stage 7 receipt")
    authenticate_provider_history_v2(provider_path, receipt=provider_history_receipt)
    replay_provider_history_v2_stage6(provider_path)
    verify_provider_history_v2(provider_path)
    replay = verify_ibkr_foundation(
        foundation,
        provider_history_receipt=provider_history_receipt,
        replay_checkpoint_root=replay_checkpoint_root,
        workers=workers,
    )
    if replay.readiness.state is not IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY:
        raise ValueError("cumulative replay did not establish qualifying readiness")
    replayed_authentication = authenticate_ibkr_foundation(foundation, receipt=receipt)
    replayed_bindings, _replayed_provider_path, _replayed_provider_contract = _bindings(
        foundation, receipt, replayed_authentication
    )
    if replayed_bindings != bindings:
        raise ValueError("foundation roots changed during cumulative replay")
    identity: dict[str, JsonValue] = {
        "contract": _PROMOTION_CONTRACT,
        "schema_version": _PROMOTION_SCHEMA_VERSION,
        "profile": "CONFIRMATORY",
        "stage6": bindings["stage6"],
        "stage7": bindings["stage7"],
        "stage8": bindings["stage8"],
        "readiness": bindings["readiness"],
        "runtime": runtime,
        "operator_authorization": operator,
        "verifier_contract": _PROMOTION_VERIFIER_CONTRACT,
        "verifier_version": _PROMOTION_VERIFIER_VERSION,
        "verifier_identity": ibkr_foundation_promotion_verifier_identity(),
        "completed_checks": list(_PROMOTION_CHECKS),
        "evidence_class": EvidenceClass.CONFIRMATORY.value,
    }
    document = {**identity, "promotion_sha256": _sha(identity)}
    atomic_create(resolved_output, _canonical_bytes(document) + b"\n")
    return _authority(document)


__all__ = [
    "authenticate_ibkr_foundation_promotion",
    "create_ibkr_foundation_confirmatory_promotion",
    "ibkr_foundation_promotion_verifier_identity",
]
