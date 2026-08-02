"""Immutable Stage 1 contracts for IBKR historical acquisition."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.time import require_utc

CONTRACT_SELECTION_CONTRACT = "qtrad-ibkr-contract-selection-v1"
RUNTIME_LOCK_CONTRACT = "qtrad-ibkr-acquisition-runtime-v1"
SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")


class IbkrContractDecision(StrEnum):
    ACCEPTED_EXACT_CONTRACT = "ACCEPTED_EXACT_CONTRACT"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class IbkrContractFingerprint:
    """Identity-bearing IBKR fields; descriptive capability fields remain outside the identity."""

    con_id: int
    symbol: str
    security_type: str
    currency: str
    exchange: str
    primary_exchange: str | None
    local_symbol: str
    trading_class: str | None
    multiplier: str | None
    underlying_con_id: int | None
    contract_month: str | None

    def __post_init__(self) -> None:
        if self.con_id <= 0:
            raise ValueError("IBKR contract fingerprint conId must be positive")
        for field_name, value in (
            ("symbol", self.symbol),
            ("security type", self.security_type),
            ("currency", self.currency),
            ("exchange", self.exchange),
            ("local symbol", self.local_symbol),
        ):
            if not value or len(value) > 200 or any(character.isspace() for character in value):
                raise ValueError(f"IBKR contract fingerprint {field_name} is bounded and non-empty")
        for field_name, value in (
            ("primary exchange", self.primary_exchange),
            ("trading class", self.trading_class),
            ("multiplier", self.multiplier),
            ("contract month", self.contract_month),
        ):
            if value is not None and (not value or len(value) > 200):
                raise ValueError(f"IBKR contract fingerprint {field_name} is bounded when present")
        if self.underlying_con_id is not None and self.underlying_con_id <= 0:
            raise ValueError("IBKR underlying conId must be positive when present")

    def as_json_value(self) -> dict[str, JsonValue]:
        """Return all fields, including absent optional fields, in stable names."""

        return {
            "con_id": self.con_id,
            "symbol": self.symbol,
            "security_type": self.security_type,
            "currency": self.currency,
            "exchange": self.exchange,
            "primary_exchange": self.primary_exchange,
            "local_symbol": self.local_symbol,
            "trading_class": self.trading_class,
            "multiplier": self.multiplier,
            "underlying_con_id": self.underlying_con_id,
            "contract_month": self.contract_month,
        }


@dataclass(frozen=True, slots=True)
class IbkrContractSelectionDecision:
    """One operator decision for exactly one canonical instrument."""

    instrument_id: InstrumentId
    decision: IbkrContractDecision
    acquisition_eligible: bool
    fingerprint: IbkrContractFingerprint | None
    reason: str | None = None
    descriptive_metadata: Mapping[str, JsonValue] = field(
        default_factory=lambda: cast(dict[str, JsonValue], {})
    )

    def __post_init__(self) -> None:
        if self.decision is not IbkrContractDecision.ACCEPTED_EXACT_CONTRACT:
            if self.acquisition_eligible:
                raise ValueError("only an accepted exact IBKR contract can be acquisition eligible")
            if not self.reason:
                raise ValueError("quarantined or rejected IBKR decisions require a reason")
        elif self.fingerprint is None:
            raise ValueError("accepted exact IBKR decisions require a contract fingerprint")
        if not self.instrument_id.value:
            raise ValueError("IBKR selection instrument ID is required")
        for key in self.descriptive_metadata:
            if not key:
                raise ValueError("IBKR descriptive metadata keys must be non-empty strings")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "instrument_id": str(self.instrument_id),
            "decision": self.decision.value,
            "acquisition_eligible": self.acquisition_eligible,
            "fingerprint": (
                self.fingerprint.as_json_value() if self.fingerprint is not None else None
            ),
            "reason": self.reason,
            "descriptive_metadata": dict(self.descriptive_metadata),
        }


@dataclass(frozen=True, slots=True)
class IbkrContractSelection:
    """Create-only selection evidence bound to one authenticated capability review."""

    capability_review_sha256: str
    catalogue_name: str
    catalogue_hash: str
    probe_spec_name: str
    probe_spec_hash: str
    api_version: str
    api_package_fingerprint: str
    frozen_by: str
    frozen_at: datetime
    decisions: tuple[IbkrContractSelectionDecision, ...]
    selection_sha256: str

    CONTRACT = CONTRACT_SELECTION_CONTRACT
    SCHEMA_VERSION = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name, value in (
            ("capability review hash", self.capability_review_sha256),
            ("catalogue hash", self.catalogue_hash),
            ("probe spec hash", self.probe_spec_hash),
            ("API package fingerprint", self.api_package_fingerprint),
            ("selection hash", self.selection_sha256),
        ):
            _require_sha256(value, field_name)
        if not self.catalogue_name or not self.probe_spec_name or not self.api_version:
            raise ValueError("IBKR contract selection source identities are required")
        if not self.frozen_by or len(self.frozen_by) > 200:
            raise ValueError("IBKR contract selection frozen_by is required and bounded")
        require_utc(self.frozen_at, "IBKR contract selection frozen_at")
        if not self.decisions:
            raise ValueError("IBKR contract selection requires decisions")
        instrument_ids = [decision.instrument_id for decision in self.decisions]
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("IBKR contract selection decisions must be unique")
        if self.selection_sha256 != _sha256_json(self.identity_payload()):
            raise ValueError("IBKR contract selection hash does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "capability_review_sha256": self.capability_review_sha256,
            "catalogue_name": self.catalogue_name,
            "catalogue_hash": self.catalogue_hash,
            "probe_spec_name": self.probe_spec_name,
            "probe_spec_hash": self.probe_spec_hash,
            "api_version": self.api_version,
            "api_package_fingerprint": self.api_package_fingerprint,
            "frozen_by": self.frozen_by,
            "frozen_at": _utc_text(self.frozen_at),
            "decisions": [decision.as_json_value() for decision in self.decisions],
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "selection_sha256": self.selection_sha256}


@dataclass(frozen=True, slots=True)
class IbkrArchiveIdentity:
    """A runtime archive and the exact bytes hashed during lock creation."""

    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.path or not self.path.strip():
            raise ValueError("IBKR runtime archive path is required")
        _require_sha256(self.sha256, "IBKR runtime archive hash")
        parsed = PurePosixPath(self.path)
        if any(part in {"", ".", ".."} for part in parsed.parts):
            raise ValueError("IBKR runtime archive path is not canonical")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class IbkrAcquisitionRuntime:
    """Authenticated, non-secret identity of the environment permitted to acquire history."""

    gateway_version: str
    gateway_archive: IbkrArchiveIdentity
    api_version: str
    api_archive: IbkrArchiveIdentity
    ibc_version: str
    ibc_archive: IbkrArchiveIdentity
    qtrad_commit: str
    qtrad_image_digest: str
    python_version: str
    library_versions: Mapping[str, str]
    gateway_configuration_identity: str
    paper_account_environment: str
    api_host: str
    api_port: int
    client_id_policy: str
    frozen_at: datetime
    runtime_sha256: str

    CONTRACT = RUNTIME_LOCK_CONTRACT
    SCHEMA_VERSION = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name, value in (("q-trad commit", self.qtrad_commit),):
            if not _COMMIT.fullmatch(value):
                raise ValueError(f"{field_name} must be a full lower-case Git commit")
        if not _IMAGE.fullmatch(self.qtrad_image_digest):
            raise ValueError("q-trad image must be an immutable sha256 digest")
        _require_sha256(self.gateway_configuration_identity, "Gateway configuration identity")
        if self.gateway_version != self.api_version:
            raise ValueError("IBKR Gateway and API versions must match")
        if not self.gateway_version or not self.api_version or not self.ibc_version:
            raise ValueError("IBKR runtime versions are required")
        if self.paper_account_environment != "paper":
            raise ValueError("IBKR runtime lock supports only the paper environment")
        if not self.api_host or len(self.api_host) > 253 or any(c.isspace() for c in self.api_host):
            raise ValueError("IBKR runtime API host is bounded and non-whitespace")
        if not 1 <= self.api_port <= 65535:
            raise ValueError("IBKR runtime API port must be between 1 and 65535")
        if self.client_id_policy != "DEDICATED_NONZERO_CLIENT_ID":
            raise ValueError("IBKR runtime client-ID policy is unsupported")
        if not self.python_version:
            raise ValueError("Python version is required")
        if not self.library_versions or any(
            not name or not version for name, version in self.library_versions.items()
        ):
            raise ValueError("IBKR runtime library versions are required")
        require_utc(self.frozen_at, "IBKR runtime lock frozen_at")
        if self.runtime_sha256 != _sha256_json(self.identity_payload()):
            raise ValueError("IBKR runtime lock hash does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "gateway_version": self.gateway_version,
            "gateway_archive": self.gateway_archive.as_json_value(),
            "api_version": self.api_version,
            "api_archive": self.api_archive.as_json_value(),
            "ibc_version": self.ibc_version,
            "ibc_archive": self.ibc_archive.as_json_value(),
            "qtrad_commit": self.qtrad_commit,
            "qtrad_image_digest": self.qtrad_image_digest,
            "python_version": self.python_version,
            "library_versions": dict(sorted(self.library_versions.items())),
            "gateway_configuration_identity": self.gateway_configuration_identity,
            "paper_account_environment": self.paper_account_environment,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "client_id_policy": self.client_id_policy,
            "frozen_at": _utc_text(self.frozen_at),
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "runtime_sha256": self.runtime_sha256}


def _require_sha256(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lower-case SHA-256 digest")


def _utc_text(value: datetime) -> str:
    require_utc(value, "IBKR artefact time")
    return value.isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def sha256_json(value: object) -> str:
    """Public canonical JSON identity helper for the runtime boundary."""

    return _sha256_json(value)


def utc_text(value: datetime) -> str:
    """Public UTC serialisation helper for artifact builders."""

    return _utc_text(value)


def ordered_decisions(
    decisions: Sequence[IbkrContractSelectionDecision],
) -> tuple[IbkrContractSelectionDecision, ...]:
    return tuple(sorted(decisions, key=lambda item: str(item.instrument_id)))
