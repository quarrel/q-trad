"""File-boundary loaders, verifiers and create-only writers for IBKR Stage 1."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from qtrad.application.ibkr_historical import (
    build_ibkr_contract_selection,
    build_ibkr_runtime_lock,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import (
    IbkrAcquisitionRuntime,
    IbkrArchiveIdentity,
    IbkrContractDecision,
    IbkrContractFingerprint,
    IbkrContractSelection,
    IbkrContractSelectionDecision,
)
from qtrad.domain.identifiers import InstrumentId

_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_SELECTION_KEYS = {
    "contract",
    "schema_version",
    "capability_review_sha256",
    "catalogue_name",
    "catalogue_hash",
    "probe_spec_name",
    "probe_spec_hash",
    "api_version",
    "api_package_fingerprint",
    "frozen_by",
    "frozen_at",
    "decisions",
    "selection_sha256",
}
_RUNTIME_KEYS = {
    "contract",
    "schema_version",
    "gateway_version",
    "gateway_archive",
    "api_version",
    "api_archive",
    "ibc_version",
    "ibc_archive",
    "qtrad_commit",
    "qtrad_image_digest",
    "python_version",
    "library_versions",
    "gateway_configuration_identity",
    "paper_account_environment",
    "api_host",
    "api_port",
    "client_id_policy",
    "frozen_at",
    "runtime_sha256",
}
_FINGERPRINT_KEYS = {
    "con_id",
    "symbol",
    "security_type",
    "currency",
    "exchange",
    "primary_exchange",
    "local_symbol",
    "trading_class",
    "multiplier",
    "underlying_con_id",
    "contract_month",
}
_DECISION_KEYS = {
    "instrument_id",
    "decision",
    "acquisition_eligible",
    "fingerprint",
    "reason",
    "descriptive_metadata",
}


def load_ibkr_capability_review(path: Path) -> dict[str, object]:
    """Load and authenticate an existing capability-review manifest."""

    from qtrad.application.ibkr_historical import _verify_capability_review

    document = _read_json_object(path, "IBKR capability review")
    _verify_capability_review(document)
    return document


def load_ibkr_operator_selection(path: Path) -> dict[str, object]:
    """Load the strict operator-authored selection input without authorising it."""

    document = _read_json_document(path, "IBKR operator selection")
    _require_exact_keys(
        document,
        {"schema_version", "decisions", "capability_review_sha256"},
        "IBKR operator selection",
        allow_missing={"capability_review_sha256"},
    )
    if document.get("schema_version") != 1:
        raise ValueError("IBKR operator selection requires schema_version = 1")
    decisions_value = document.get("decisions")
    if not isinstance(decisions_value, list):
        raise ValueError("IBKR operator selection decisions must be an array")
    for decision in decisions_value:
        _validate_operator_decision(decision)
    return document


def build_ibkr_contract_selection_from_files(
    *,
    capability_review_path: Path,
    selection_path: Path,
    frozen_by: str,
    frozen_at: datetime,
) -> IbkrContractSelection:
    return build_ibkr_contract_selection(
        capability_review=load_ibkr_capability_review(capability_review_path),
        operator_selection=load_ibkr_operator_selection(selection_path),
        frozen_by=frozen_by,
        frozen_at=frozen_at,
    )


def write_ibkr_contract_selection(path: Path, selection: IbkrContractSelection) -> None:
    _write_create_only(path, selection.as_json_value(), "IBKR contract selection")


def load_ibkr_contract_selection(path: Path) -> IbkrContractSelection:
    document = _read_json_object(path, "IBKR contract selection")
    _require_exact_keys(document, _SELECTION_KEYS, "IBKR contract selection")
    if document.get("contract") != IbkrContractSelection.CONTRACT:
        raise ValueError("IBKR contract selection contract is unsupported")
    if document.get("schema_version") != IbkrContractSelection.SCHEMA_VERSION:
        raise ValueError("IBKR contract selection schema version is unsupported")
    decisions_value = document.get("decisions")
    if not isinstance(decisions_value, list):
        raise ValueError("IBKR contract selection decisions must be an array")
    decisions = tuple(_decision_from_json(item) for item in decisions_value)
    selection = IbkrContractSelection(
        capability_review_sha256=_string(document, "capability_review_sha256"),
        catalogue_name=_string(document, "catalogue_name"),
        catalogue_hash=_string(document, "catalogue_hash"),
        probe_spec_name=_string(document, "probe_spec_name"),
        probe_spec_hash=_string(document, "probe_spec_hash"),
        api_version=_string(document, "api_version"),
        api_package_fingerprint=_string(document, "api_package_fingerprint"),
        frozen_by=_string(document, "frozen_by"),
        frozen_at=_datetime(document, "frozen_at"),
        decisions=decisions,
        selection_sha256=_string(document, "selection_sha256"),
    )
    if selection.as_json_value() != document:
        raise ValueError("IBKR contract selection contains non-canonical fields")
    return selection


def verify_ibkr_contract_selection(
    path: Path,
    *,
    capability_review_path: Path | None = None,
) -> IbkrContractSelection:
    """Verify the artifact independently and optionally rebind it to its capability review."""

    selection = load_ibkr_contract_selection(path)
    if capability_review_path is not None:
        review = load_ibkr_capability_review(capability_review_path)
        operator_selection = {
            "schema_version": 1,
            "capability_review_sha256": selection.capability_review_sha256,
            "decisions": [decision.as_json_value() for decision in selection.decisions],
        }
        rebuilt = build_ibkr_contract_selection(
            capability_review=review,
            operator_selection=operator_selection,
            frozen_by=selection.frozen_by,
            frozen_at=selection.frozen_at,
        )
        if rebuilt.as_json_value() != selection.as_json_value():
            raise ValueError("IBKR contract selection does not replay from its capability review")
    return selection


def build_ibkr_runtime_lock_from_files(
    *,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    gateway_version: str,
    api_version: str,
    ibc_version: str,
    qtrad_commit: str,
    qtrad_image_digest: str,
    frozen_at: datetime,
    python_version: str | None = None,
    library_versions: Mapping[str, str] | None = None,
    gateway_configuration_identity: str | None = None,
    api_host: str = "127.0.0.1",
    api_port: int = 4002,
    client_id_policy: str = "DEDICATED_NONZERO_CLIENT_ID",
) -> IbkrAcquisitionRuntime:
    return build_ibkr_runtime_lock(
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        gateway_version=gateway_version,
        api_version=api_version,
        ibc_version=ibc_version,
        qtrad_commit=qtrad_commit,
        qtrad_image_digest=qtrad_image_digest,
        frozen_at=frozen_at,
        python_version=python_version,
        library_versions=library_versions,
        gateway_configuration_identity=gateway_configuration_identity,
        api_host=api_host,
        api_port=api_port,
        client_id_policy=client_id_policy,
    )


def write_ibkr_runtime_lock(path: Path, runtime: IbkrAcquisitionRuntime) -> None:
    _write_create_only(path, runtime.as_json_value(), "IBKR runtime lock")


def load_ibkr_runtime_lock(path: Path) -> IbkrAcquisitionRuntime:
    document = _read_json_object(path, "IBKR runtime lock")
    _require_exact_keys(document, _RUNTIME_KEYS, "IBKR runtime lock")
    if document.get("contract") != IbkrAcquisitionRuntime.CONTRACT:
        raise ValueError("IBKR runtime lock contract is unsupported")
    if document.get("schema_version") != IbkrAcquisitionRuntime.SCHEMA_VERSION:
        raise ValueError("IBKR runtime lock schema version is unsupported")
    runtime = IbkrAcquisitionRuntime(
        gateway_version=_string(document, "gateway_version"),
        gateway_archive=_archive_from_json(document.get("gateway_archive")),
        api_version=_string(document, "api_version"),
        api_archive=_archive_from_json(document.get("api_archive")),
        ibc_version=_string(document, "ibc_version"),
        ibc_archive=_archive_from_json(document.get("ibc_archive")),
        qtrad_commit=_string(document, "qtrad_commit"),
        qtrad_image_digest=_string(document, "qtrad_image_digest"),
        python_version=_string(document, "python_version"),
        library_versions=_string_mapping(document.get("library_versions"), "library_versions"),
        gateway_configuration_identity=_string(document, "gateway_configuration_identity"),
        paper_account_environment=_string(document, "paper_account_environment"),
        api_host=_string(document, "api_host"),
        api_port=_integer(document, "api_port"),
        client_id_policy=_string(document, "client_id_policy"),
        frozen_at=_datetime(document, "frozen_at"),
        runtime_sha256=_string(document, "runtime_sha256"),
    )
    if runtime.as_json_value() != document:
        raise ValueError("IBKR runtime lock contains non-canonical fields")
    return runtime


def verify_ibkr_runtime_lock(path: Path) -> IbkrAcquisitionRuntime:
    """Verify semantic identity and re-hash all three archives from the published paths."""

    runtime = load_ibkr_runtime_lock(path)
    for archive in (runtime.gateway_archive, runtime.api_archive, runtime.ibc_archive):
        archive_path = Path(archive.path)
        _require_readable_path(archive_path, "IBKR runtime archive")
        observed = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        if observed != archive.sha256:
            raise ValueError(f"IBKR runtime archive hash mismatch: {archive.path}")
    return runtime


def _read_json_document(path: Path, label: str) -> dict[str, object]:
    if path.suffix.lower() == ".toml":
        _require_readable_path(path, label)
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"{label} is invalid") from error
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} must be an object")
        return cast(dict[str, object], parsed)
    return _read_json_object(path, label)


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    _require_readable_path(path, label)
    encoded = path.read_bytes()
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} exceeds its bounded size")
    try:
        parsed = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], parsed)


def _write_create_only(path: Path, payload: Mapping[str, JsonValue], label: str) -> None:
    _require_output_path(path, label)
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} exceeds its bounded size")
    try:
        with path.open("xb") as output:
            output.write(encoded)
    except FileExistsError as error:
        raise FileExistsError(f"{label} output already exists: {path}") from error


def _decision_from_json(value: object) -> IbkrContractSelectionDecision:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR selection decision must be an object")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(value, _DECISION_KEYS, "IBKR contract selection decision")
    fingerprint_value = value.get("fingerprint")
    fingerprint = None if fingerprint_value is None else _fingerprint_from_json(fingerprint_value)
    metadata = value.get("descriptive_metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("IBKR selection descriptive_metadata must be an object")
    try:
        decision = IbkrContractDecision(_string(value, "decision"))
    except ValueError as error:
        raise ValueError("IBKR selection decision value is invalid") from error
    eligible = value.get("acquisition_eligible")
    if not isinstance(eligible, bool):
        raise ValueError("IBKR selection acquisition_eligible must be boolean")
    reason = value.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("IBKR selection reason must be a string or null")
    return IbkrContractSelectionDecision(
        instrument_id=InstrumentId(_string(value, "instrument_id")),
        decision=decision,
        acquisition_eligible=eligible,
        fingerprint=fingerprint,
        reason=reason,
        descriptive_metadata=dict(cast(Mapping[str, JsonValue], metadata)),
    )


def _fingerprint_from_json(value: object) -> IbkrContractFingerprint:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR contract fingerprint must be an object")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(value, _FINGERPRINT_KEYS, "IBKR contract fingerprint")
    return IbkrContractFingerprint(
        con_id=_integer(value, "con_id"),
        symbol=_string(value, "symbol"),
        security_type=_string(value, "security_type"),
        currency=_string(value, "currency"),
        exchange=_string(value, "exchange"),
        primary_exchange=_optional_string(value, "primary_exchange"),
        local_symbol=_string(value, "local_symbol"),
        trading_class=_optional_string(value, "trading_class"),
        multiplier=_optional_string(value, "multiplier"),
        underlying_con_id=_optional_integer(value, "underlying_con_id"),
        contract_month=_optional_string(value, "contract_month"),
    )


def _validate_operator_decision(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR operator selection decisions must be objects")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(
        value,
        _DECISION_KEYS,
        "IBKR operator selection decision",
        allow_missing={"reason", "descriptive_metadata"},
    )
    if value.get("fingerprint") is not None:
        _fingerprint_from_json(value.get("fingerprint"))
    if not isinstance(value.get("acquisition_eligible"), bool):
        raise ValueError("IBKR operator selection acquisition_eligible must be boolean")
    metadata = value.get("descriptive_metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("IBKR operator selection descriptive_metadata must be an object")


def _archive_from_json(value: object) -> IbkrArchiveIdentity:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR runtime archive must be an object")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(value, {"path", "sha256"}, "IBKR runtime archive")
    return IbkrArchiveIdentity(path=_string(value, "path"), sha256=_string(value, "sha256"))


def _string_mapping(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    value = cast(Mapping[str, object], value)
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise ValueError(f"{field} must contain non-empty string pairs")
        result[key] = item
    return result


def _string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item


def _optional_string(value: Mapping[str, object], field: str) -> str | None:
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


def _optional_integer(value: Mapping[str, object], field: str) -> int | None:
    item = value.get(field)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field} must be an integer when present")
    return item


def _datetime(value: Mapping[str, object], field: str) -> datetime:
    item = _string(value, field)
    try:
        parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field} must use UTC")
    return parsed


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    context: str,
    *,
    allow_missing: set[str] | None = None,
) -> None:
    allowed_missing = allow_missing or set()
    keys = set(value)
    if keys - expected or (expected - allowed_missing) - keys:
        raise ValueError(f"{context} has unknown or missing fields")


def _require_readable_path(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise ValueError(f"{label} path escapes its artifact root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"{label} path contains a symlink: {path}")
    if not current.is_file():
        raise FileNotFoundError(f"{label} is not a regular file: {path}")


def _require_output_path(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise ValueError(f"{label} output path escapes its artifact root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    if current.exists() and current.is_symlink():
        raise ValueError(f"{label} output cannot be a symlink: {path}")
    for ancestor in current.parents:
        if ancestor.is_symlink():
            raise ValueError(f"{label} output path contains a symlink: {path}")
    if not current.parent.is_dir():
        raise FileNotFoundError(f"{label} output directory does not exist: {current.parent}")
