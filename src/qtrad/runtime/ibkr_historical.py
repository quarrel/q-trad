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
from qtrad.runtime.ibkr_capability import ibkr_capability_probe_spec_from_document
from qtrad.runtime.universe import capture_candidates_from_document

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


def load_ibkr_capability_review(
    path: Path, *, catalogue_path: Path, probe_spec_path: Path
) -> dict[str, object]:
    """Load a review and prove it covers the independently reloaded canonical closure."""
    from qtrad.application.ibkr_historical import _verify_capability_review

    candidates, probe_spec = _load_canonical_inputs(
        catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    document = _read_json_object(path, "IBKR capability review")
    _verify_capability_review(
        document,
        canonical_instrument_ids=frozenset(item.instrument_id for item in candidates.instruments),
        canonical_queries=frozenset(probe_spec.queries),
    )
    if (
        document["catalogue_name"] != candidates.name
        or document["catalogue_hash"] != candidates.configuration_hash
    ):
        raise ValueError("IBKR capability review targets a different canonical catalogue")
    if (
        document["probe_spec_name"] != probe_spec.name
        or document["probe_spec_hash"] != probe_spec.configuration_hash
    ):
        raise ValueError("IBKR capability review targets a different canonical probe specification")
    return document


def load_ibkr_operator_selection(path: Path) -> dict[str, object]:
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
    catalogue_path: Path,
    probe_spec_path: Path,
    frozen_by: str,
    frozen_at: datetime,
) -> IbkrContractSelection:
    candidates, probe_spec = _load_canonical_inputs(
        catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    return build_ibkr_contract_selection(
        capability_review=load_ibkr_capability_review(
            capability_review_path, catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
        ),
        operator_selection=load_ibkr_operator_selection(selection_path),
        canonical_instrument_ids=frozenset(item.instrument_id for item in candidates.instruments),
        canonical_queries=frozenset(probe_spec.queries),
        frozen_by=frozen_by,
        frozen_at=frozen_at,
    )


def write_ibkr_contract_selection(path: Path, selection: IbkrContractSelection) -> None:
    _write_create_only(path, selection.as_json_value(), "IBKR contract selection")


def load_ibkr_contract_selection(path: Path) -> IbkrContractSelection:
    document = _read_json_object(path, "IBKR contract selection")
    _require_exact_keys(document, _SELECTION_KEYS, "IBKR contract selection")
    if (
        document.get("contract") != IbkrContractSelection.CONTRACT
        or document.get("schema_version") != IbkrContractSelection.SCHEMA_VERSION
    ):
        raise ValueError("IBKR contract selection contract or schema version is unsupported")
    values = document.get("decisions")
    if not isinstance(values, list):
        raise ValueError("IBKR contract selection decisions must be an array")
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
        decisions=tuple(_decision_from_json(item) for item in values),
        selection_sha256=_string(document, "selection_sha256"),
    )
    if selection.as_json_value() != document:
        raise ValueError("IBKR contract selection contains non-canonical fields")
    return selection


def verify_ibkr_contract_selection(
    path: Path, *, capability_review_path: Path, catalogue_path: Path, probe_spec_path: Path
) -> IbkrContractSelection:
    """Independently replay a selection; the canonical inputs are deliberately mandatory."""
    selection = load_ibkr_contract_selection(path)
    review = load_ibkr_capability_review(
        capability_review_path, catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    candidates, probe_spec = _load_canonical_inputs(
        catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    rebuilt = build_ibkr_contract_selection(
        capability_review=review,
        operator_selection={
            "schema_version": 1,
            "capability_review_sha256": selection.capability_review_sha256,
            "decisions": [item.as_json_value() for item in selection.decisions],
        },
        canonical_instrument_ids=frozenset(item.instrument_id for item in candidates.instruments),
        canonical_queries=frozenset(probe_spec.queries),
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
    qtrad_image_digest: str,
    frozen_at: datetime,
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
        qtrad_image_digest=qtrad_image_digest,
        frozen_at=frozen_at,
        api_host=api_host,
        api_port=api_port,
        client_id_policy=client_id_policy,
    )


def write_ibkr_runtime_lock(path: Path, runtime: IbkrAcquisitionRuntime) -> None:
    _write_create_only(path, runtime.as_json_value(), "IBKR runtime lock")


def load_ibkr_runtime_lock(path: Path) -> IbkrAcquisitionRuntime:
    document = _read_json_object(path, "IBKR runtime lock")
    _require_exact_keys(document, _RUNTIME_KEYS, "IBKR runtime lock")
    if (
        document.get("contract") != IbkrAcquisitionRuntime.CONTRACT
        or document.get("schema_version") != IbkrAcquisitionRuntime.SCHEMA_VERSION
    ):
        raise ValueError("IBKR runtime lock contract or schema version is unsupported")
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


def verify_ibkr_runtime_lock(
    path: Path,
    *,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    expected_gateway_sha256: str,
    expected_api_sha256: str,
    expected_ibc_sha256: str,
    expected_image_digest: str,
    expected_gateway_version: str,
    expected_api_version: str,
    expected_ibc_version: str,
    expected_api_host: str,
    expected_api_port: int,
    expected_client_id_policy: str,
) -> IbkrAcquisitionRuntime:
    """Replay runtime identity against explicit authoritative archive/image attestations."""
    runtime = load_ibkr_runtime_lock(path)
    expected = (expected_gateway_sha256, expected_api_sha256, expected_ibc_sha256)
    actual_paths = (gateway_archive, api_archive, ibc_archive)
    locked = (runtime.gateway_archive, runtime.api_archive, runtime.ibc_archive)
    for role, archive_path, identity, expected_hash in zip(
        ("Gateway", "API", "IBC"), actual_paths, locked, expected, strict=True
    ):
        _require_sha256(expected_hash, f"expected {role} archive hash")
        _require_readable_path(archive_path, f"IBKR {role} archive")
        if archive_path.name != identity.filename:
            raise ValueError(f"IBKR {role} archive filename does not match the runtime lock")
        observed = _sha256_file(archive_path)
        if observed != expected_hash or identity.sha256 != expected_hash:
            raise ValueError(
                f"IBKR {role} archive does not match its authoritative role attestation"
            )
    rebuilt = build_ibkr_runtime_lock(
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        gateway_version=expected_gateway_version,
        api_version=expected_api_version,
        ibc_version=expected_ibc_version,
        qtrad_image_digest=expected_image_digest,
        frozen_at=runtime.frozen_at,
        api_host=expected_api_host,
        api_port=expected_api_port,
        client_id_policy=expected_client_id_policy,
    )
    if rebuilt.as_json_value() != runtime.as_json_value():
        raise ValueError("IBKR runtime lock does not replay from the actual runtime identity")
    return runtime


def _load_canonical_inputs(*, catalogue_path: Path, probe_spec_path: Path):
    candidates = capture_candidates_from_document(
        _read_toml_object(catalogue_path, "IBKR canonical catalogue")
    )
    probe_spec = ibkr_capability_probe_spec_from_document(
        _read_toml_object(probe_spec_path, "IBKR capability probe specification")
    )
    return candidates, probe_spec


def _read_json_document(path: Path, label: str) -> dict[str, object]:
    if path.suffix.lower() == ".toml":
        return _read_toml_object(path, label)
    return _read_json_object(path, label)


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    encoded = _read_bounded_bytes(path, label)
    try:
        parsed = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], parsed)


def _read_toml_object(path: Path, label: str) -> dict[str, object]:
    encoded = _read_bounded_bytes(path, label)
    try:
        parsed = tomllib.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{label} is invalid") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], parsed)


def _read_bounded_bytes(path: Path, label: str) -> bytes:
    _require_readable_path(path, label)
    if path.stat().st_size > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} exceeds its bounded size")
    with path.open("rb") as source:
        encoded = source.read(_MAX_ARTIFACT_BYTES + 1)
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} exceeds its bounded size")
    return encoded


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def _archive_from_json(value: object) -> IbkrArchiveIdentity:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR runtime archive must be an object")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(value, {"filename", "sha256"}, "IBKR runtime archive")
    return IbkrArchiveIdentity(filename=_string(value, "filename"), sha256=_string(value, "sha256"))


def _string_mapping(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
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


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lower-case SHA-256 digest")


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    context: str,
    *,
    allow_missing: set[str] | None = None,
) -> None:
    allowed_missing = allow_missing or set()
    if set(value) - expected or (expected - allowed_missing) - set(value):
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
