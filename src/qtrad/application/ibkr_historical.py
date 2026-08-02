"""Pure Stage 1 builders for IBKR historical-acquisition evidence."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import subprocess
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import (
    IbkrAcquisitionRuntime,
    IbkrArchiveIdentity,
    IbkrContractDecision,
    IbkrContractFingerprint,
    IbkrContractSelection,
    IbkrContractSelectionDecision,
    ordered_decisions,
    sha256_json,
    utc_text,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.time import require_utc

_FINGERPRINT_FIELDS = {
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
_DECISION_FIELDS = {
    "instrument_id",
    "decision",
    "acquisition_eligible",
    "fingerprint",
    "reason",
    "descriptive_metadata",
}


def build_ibkr_contract_selection(
    *,
    capability_review: Mapping[str, object],
    operator_selection: Mapping[str, object],
    frozen_by: str,
    frozen_at: datetime,
) -> IbkrContractSelection:
    """Reconstruct an exact operator selection against an authenticated review payload."""

    _verify_capability_review(capability_review)
    _require_exact_keys(
        operator_selection,
        {"schema_version", "decisions", "capability_review_sha256"},
        "IBKR operator selection",
        allow_missing={"capability_review_sha256"},
    )
    if operator_selection.get("schema_version") != 1:
        raise ValueError("IBKR operator selection requires schema_version = 1")
    review_hash = _string(capability_review, "review_hash")
    declared_review_hash = operator_selection.get("capability_review_sha256")
    if declared_review_hash is not None and declared_review_hash != review_hash:
        raise ValueError("operator selection references a different capability review")
    raw_decisions_value = operator_selection.get("decisions")
    if not isinstance(raw_decisions_value, list) or not raw_decisions_value:
        raise ValueError("IBKR operator selection requires decisions")
    raw_decisions = cast(list[object], raw_decisions_value)

    review_by_instrument = _review_contracts(capability_review)
    decisions: list[IbkrContractSelectionDecision] = []
    seen: set[InstrumentId] = set()
    selected_con_ids: set[int] = set()
    for raw_value in raw_decisions:
        if not isinstance(raw_value, Mapping):
            raise ValueError("IBKR operator selection decisions must be objects")
        raw = cast(Mapping[str, object], raw_value)
        _require_exact_keys(
            raw,
            _DECISION_FIELDS,
            "IBKR operator selection decision",
            allow_missing={"reason", "descriptive_metadata"},
        )
        instrument_id = InstrumentId(_string(raw, "instrument_id"))
        if instrument_id in seen:
            raise ValueError(f"IBKR operator selection repeats instrument {instrument_id}")
        seen.add(instrument_id)
        try:
            expected_contracts = review_by_instrument[instrument_id]
        except KeyError as error:
            raise ValueError(
                f"IBKR operator selection contains unknown instrument {instrument_id}"
            ) from error
        try:
            decision = IbkrContractDecision(_string(raw, "decision"))
        except ValueError as error:
            raise ValueError(
                f"IBKR operator selection has an invalid decision for {instrument_id}"
            ) from error
        eligible = raw.get("acquisition_eligible")
        if not isinstance(eligible, bool):
            raise ValueError("IBKR acquisition_eligible must be a boolean")
        reason = raw.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason or len(reason) > 500):
            raise ValueError("IBKR selection reason must be bounded when present")
        metadata = raw.get("descriptive_metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("IBKR descriptive_metadata must be an object")
        fingerprint_payload = raw.get("fingerprint")
        if fingerprint_payload is None:
            fingerprint = None
        elif isinstance(fingerprint_payload, Mapping):
            fingerprint = _fingerprint(cast(Mapping[str, object], fingerprint_payload))
        else:
            raise ValueError("IBKR selection fingerprint must be an object or null")
        if fingerprint is not None and fingerprint not in expected_contracts:
            raise ValueError(
                "IBKR selection fingerprint is not an exact capability-review match "
                f"for {instrument_id}"
            )
        if fingerprint is not None:
            if fingerprint.con_id in selected_con_ids:
                raise ValueError(f"IBKR selection duplicates conId {fingerprint.con_id}")
            selected_con_ids.add(fingerprint.con_id)
        decisions.append(
            IbkrContractSelectionDecision(
                instrument_id=instrument_id,
                decision=decision,
                acquisition_eligible=eligible,
                fingerprint=fingerprint,
                reason=reason,
                descriptive_metadata=dict(cast(Mapping[str, JsonValue], metadata)),
            )
        )

    expected_ids = set(review_by_instrument)
    if seen != expected_ids:
        missing = expected_ids - seen
        extra = seen - expected_ids
        detail: list[str] = []
        if missing:
            detail.append("missing " + ", ".join(sorted(map(str, missing))))
        if extra:
            detail.append("extraneous " + ", ".join(sorted(map(str, extra))))
        raise ValueError(
            "IBKR operator selection does not reconstruct the capability review: "
            + "; ".join(detail)
        )

    require_utc(frozen_at, "IBKR contract selection frozen_at")
    api = _mapping(capability_review, "api")
    api_version = _mapping_string(api, "version")
    api_package_fingerprint = _mapping_string(api, "package_fingerprint")
    identity = {
        "contract": IbkrContractSelection.CONTRACT,
        "schema_version": IbkrContractSelection.SCHEMA_VERSION,
        "capability_review_sha256": review_hash,
        "catalogue_name": _string(capability_review, "catalogue_name"),
        "catalogue_hash": _string(capability_review, "catalogue_hash"),
        "probe_spec_name": _string(capability_review, "probe_spec_name"),
        "probe_spec_hash": _string(capability_review, "probe_spec_hash"),
        "api_version": api_version,
        "api_package_fingerprint": api_package_fingerprint,
        "frozen_by": frozen_by,
        "frozen_at": utc_text(frozen_at),
        "decisions": [decision.as_json_value() for decision in ordered_decisions(decisions)],
    }
    return IbkrContractSelection(
        capability_review_sha256=review_hash,
        catalogue_name=_string(capability_review, "catalogue_name"),
        catalogue_hash=_string(capability_review, "catalogue_hash"),
        probe_spec_name=_string(capability_review, "probe_spec_name"),
        probe_spec_hash=_string(capability_review, "probe_spec_hash"),
        api_version=api_version,
        api_package_fingerprint=api_package_fingerprint,
        frozen_by=frozen_by,
        frozen_at=frozen_at,
        decisions=ordered_decisions(decisions),
        selection_sha256=sha256_json(identity),
    )


def build_ibkr_runtime_lock(
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
    """Hash exact local archives and construct a non-secret runtime lock."""

    require_utc(frozen_at, "IBKR runtime lock frozen_at")
    archives = (
        _archive_identity(gateway_archive),
        _archive_identity(api_archive),
        _archive_identity(ibc_archive),
    )
    config_identity = gateway_configuration_identity or sha256_json(
        {
            "api_host": api_host,
            "api_port": api_port,
            "client_id_policy": client_id_policy,
            "paper_account_environment": "paper",
        }
    )
    versions = dict(library_versions or _installed_library_versions())
    python = python_version or platform.python_version()
    identity = {
        "contract": IbkrAcquisitionRuntime.CONTRACT,
        "schema_version": IbkrAcquisitionRuntime.SCHEMA_VERSION,
        "gateway_version": gateway_version,
        "gateway_archive": archives[0].as_json_value(),
        "api_version": api_version,
        "api_archive": archives[1].as_json_value(),
        "ibc_version": ibc_version,
        "ibc_archive": archives[2].as_json_value(),
        "qtrad_commit": qtrad_commit,
        "qtrad_image_digest": qtrad_image_digest,
        "python_version": python,
        "library_versions": dict(sorted(versions.items())),
        "gateway_configuration_identity": config_identity,
        "paper_account_environment": "paper",
        "api_host": api_host,
        "api_port": api_port,
        "client_id_policy": client_id_policy,
        "frozen_at": utc_text(frozen_at),
    }
    return IbkrAcquisitionRuntime(
        gateway_version=gateway_version,
        gateway_archive=archives[0],
        api_version=api_version,
        api_archive=archives[1],
        ibc_version=ibc_version,
        ibc_archive=archives[2],
        qtrad_commit=qtrad_commit,
        qtrad_image_digest=qtrad_image_digest,
        python_version=python,
        library_versions=dict(sorted(versions.items())),
        gateway_configuration_identity=config_identity,
        paper_account_environment="paper",
        api_host=api_host,
        api_port=api_port,
        client_id_policy=client_id_policy,
        frozen_at=frozen_at,
        runtime_sha256=sha256_json(identity),
    )


def _verify_capability_review(review: Mapping[str, object]) -> None:
    required = {
        "schema_version",
        "provider",
        "environment",
        "catalogue_name",
        "catalogue_hash",
        "probe_spec_name",
        "probe_spec_hash",
        "api",
        "observed_at",
        "selection_authority",
        "external_io_performed",
        "instruments",
        "review_hash",
    }
    _require_exact_keys(review, required, "IBKR capability review")
    if review.get("schema_version") != 1 or review.get("provider") != "ibkr":
        raise ValueError("unsupported IBKR capability review")
    if review.get("environment") != "paper":
        raise ValueError("IBKR capability review must use the paper environment")
    if (
        review.get("selection_authority") is not False
        or review.get("external_io_performed") is not True
    ):
        raise ValueError("IBKR capability review authority flags are invalid")
    review_hash = _string(review, "review_hash")
    unsigned = {key: value for key, value in review.items() if key != "review_hash"}
    if sha256_json(unsigned) != review_hash:
        raise ValueError("IBKR capability review hash does not match canonical content")
    _mapping_string(_mapping(review, "api"), "version")
    _mapping_string(_mapping(review, "api"), "package_fingerprint")
    if not isinstance(review.get("instruments"), list) or not review["instruments"]:
        raise ValueError("IBKR capability review requires instruments")


def _review_contracts(
    review: Mapping[str, object],
) -> dict[InstrumentId, tuple[IbkrContractFingerprint, ...]]:
    instruments_value = review["instruments"]
    if not isinstance(instruments_value, list):
        raise ValueError("IBKR capability review instruments must be an array")
    instruments = cast(list[object], instruments_value)
    result: dict[InstrumentId, tuple[IbkrContractFingerprint, ...]] = {}
    for raw_instrument_value in instruments:
        instrument = _mapping_value(raw_instrument_value, "IBKR capability review instrument")
        _require_exact_keys(
            instrument,
            {"instrument_id", "display_name", "status", "returned_contract_count", "queries"},
            "IBKR capability review instrument",
        )
        instrument_id = InstrumentId(_string(instrument, "instrument_id"))
        if instrument_id in result:
            raise ValueError(f"IBKR capability review repeats instrument {instrument_id}")
        queries_value = instrument.get("queries")
        if not isinstance(queries_value, list) or not queries_value:
            raise ValueError(f"IBKR capability review has no query result for {instrument_id}")
        queries = cast(list[object], queries_value)
        fingerprints: list[IbkrContractFingerprint] = []
        for raw_result_value in queries:
            result_payload = _mapping_value(raw_result_value, "IBKR capability review query result")
            _require_exact_keys(
                result_payload,
                {"query", "contracts", "requests"},
                "IBKR capability review query result",
            )
            query = _mapping_value(result_payload.get("query"), "IBKR capability review query")
            _require_exact_keys(
                query,
                {
                    "instrument_id",
                    "symbol",
                    "security_type",
                    "exchange",
                    "currency",
                    "local_symbol",
                    "trading_class",
                    "multiplier",
                },
                "IBKR capability review query",
            )
            if InstrumentId(_string(query, "instrument_id")) != instrument_id:
                raise ValueError(
                    f"IBKR capability review query is bound to the wrong instrument {instrument_id}"
                )
            contracts_value = result_payload.get("contracts")
            if not isinstance(contracts_value, list):
                raise ValueError(
                    f"IBKR capability review contracts are invalid for {instrument_id}"
                )
            contracts = cast(list[object], contracts_value)
            for raw_contract_value in contracts:
                contract = _mapping_value(raw_contract_value, "IBKR capability review contract")
                fingerprints.append(_fingerprint_from_review_contract(contract))
        if len({fingerprint.con_id for fingerprint in fingerprints}) != len(fingerprints):
            raise ValueError(f"IBKR capability review repeats a contract for {instrument_id}")
        result[instrument_id] = tuple(fingerprints)
    return result


def _fingerprint_from_review_contract(payload: Mapping[str, object]) -> IbkrContractFingerprint:
    allowed = {
        "con_id",
        "symbol",
        "local_symbol",
        "security_type",
        "exchange",
        "currency",
        "trading_class",
        "multiplier",
        "minimum_tick",
        "market_rule_ids",
        "valid_exchanges",
        "long_name",
        "underlier_con_id",
        "timezone",
        "trading_hours",
        "liquid_hours",
        "primary_exchange",
        "contract_month",
    }
    _require_exact_keys(
        payload,
        allowed,
        "IBKR capability review contract",
        allow_missing={"primary_exchange", "contract_month"},
    )
    return IbkrContractFingerprint(
        con_id=_integer(payload, "con_id"),
        symbol=_string(payload, "symbol"),
        security_type=_string(payload, "security_type"),
        currency=_string(payload, "currency"),
        exchange=_string(payload, "exchange"),
        primary_exchange=_optional_string(payload, "primary_exchange"),
        local_symbol=_string(payload, "local_symbol"),
        trading_class=_optional_string(payload, "trading_class"),
        multiplier=_optional_string(payload, "multiplier"),
        underlying_con_id=_optional_integer(payload, "underlier_con_id"),
        contract_month=_optional_string(payload, "contract_month"),
    )


def _fingerprint(payload: Mapping[str, object]) -> IbkrContractFingerprint:
    _require_exact_keys(payload, _FINGERPRINT_FIELDS, "IBKR contract fingerprint")
    return IbkrContractFingerprint(
        con_id=_integer(payload, "con_id"),
        symbol=_string(payload, "symbol"),
        security_type=_string(payload, "security_type"),
        currency=_string(payload, "currency"),
        exchange=_string(payload, "exchange"),
        primary_exchange=_optional_string(payload, "primary_exchange"),
        local_symbol=_string(payload, "local_symbol"),
        trading_class=_optional_string(payload, "trading_class"),
        multiplier=_optional_string(payload, "multiplier"),
        underlying_con_id=_optional_integer(payload, "underlying_con_id"),
        contract_month=_optional_string(payload, "contract_month"),
    )


def _archive_identity(path: Path) -> IbkrArchiveIdentity:
    _require_regular_non_symlink(path, "IBKR runtime archive")
    resolved = path.resolve()
    return IbkrArchiveIdentity(
        path=resolved.as_posix(), sha256=hashlib.sha256(resolved.read_bytes()).hexdigest()
    )


def _require_regular_non_symlink(path: Path, label: str) -> None:
    current = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"{label} path contains a symlink: {path}")
    if not current.is_file():
        raise FileNotFoundError(f"{label} is not a regular file: {path}")


def _installed_library_versions() -> dict[str, str]:
    names = ("pydantic", "polars", "sqlalchemy", "numpy", "scikit-learn")
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def derive_qtrad_commit(*, require_clean: bool = True) -> str:
    """Derive a source commit without accepting a caller-supplied mutable label."""

    repository = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ("git", "-C", str(repository), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if require_clean:
            status = subprocess.run(
                ("git", "-C", str(repository), "status", "--porcelain", "--untracked-files=all"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if status:
                raise RuntimeError("q-trad runtime lock requires a clean source tree")
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("cannot derive q-trad commit identity") from error
    return commit


def configured_image_digest(value: str | None = None) -> str:
    candidate = value or os.environ.get("QTRAD_IMAGE_DIGEST")
    if candidate is None:
        raise RuntimeError("an immutable q-trad image digest is required; set QTRAD_IMAGE_DIGEST")
    return candidate


def _mapping(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    return _mapping_value(value.get(field), field)


def _mapping_value(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item


def _mapping_string(value: Mapping[str, object], field: str) -> str:
    return _string(value, field)


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
