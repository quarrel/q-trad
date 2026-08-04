"""Immutable Stage 1 contracts for IBKR historical acquisition."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.domain.time import require_utc

CONTRACT_SELECTION_CONTRACT = "qtrad-ibkr-contract-selection-v1"
RUNTIME_LOCK_CONTRACT = "qtrad-ibkr-acquisition-runtime-v1"
REQUEST_PROFILE_CONTRACT = "qtrad-ibkr-historical-request-profile-v1"
HISTORICAL_PLAN_CONTRACT = "qtrad-ibkr-historical-plan-v1"
SCHEMA_VERSION = 1
MAX_PLANNED_REQUESTS = 20_000
MAX_IBKR_HISTORICAL_COOLDOWN_SECONDS = 3_600
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

    filename: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.filename or not self.filename.strip():
            raise ValueError("IBKR runtime archive filename is required")
        _require_sha256(self.sha256, "IBKR runtime archive hash")
        parsed = PurePosixPath(self.filename)
        if len(parsed.parts) != 1 or parsed.name != self.filename or self.filename in {".", ".."}:
            raise ValueError("IBKR runtime archive filename must be a safe basename")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {"filename": self.filename, "sha256": self.sha256}


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


_DURATION = re.compile(r"^([1-9][0-9]?) ([DW])$")


class IbkrHistoricalRequestKind(StrEnum):
    MIDPOINT_BARS = "MIDPOINT_BARS"
    SCHEDULE = "SCHEDULE"


@dataclass(frozen=True, slots=True)
class IbkrHistoricalPacingPolicy:
    """Conservative limits frozen before historical execution exists."""

    identical_request_cooldown_seconds: int
    per_contract_window_seconds: int
    max_requests_per_contract_window: int
    rolling_window_seconds: int
    max_requests_per_rolling_window: int

    def __post_init__(self) -> None:
        if not (
            15 <= self.identical_request_cooldown_seconds <= MAX_IBKR_HISTORICAL_COOLDOWN_SECONDS
        ):
            raise ValueError("IBKR identical-request cooldown must be between 15 and 3600 seconds")
        if self.per_contract_window_seconds != 2:
            raise ValueError("IBKR per-contract pacing window must be exactly two seconds")
        if not 1 <= self.max_requests_per_contract_window <= 5:
            raise ValueError("IBKR per-contract pacing must remain below the provider burst limit")
        if self.rolling_window_seconds != 600:
            raise ValueError("IBKR rolling pacing window must be exactly ten minutes")
        if not 1 <= self.max_requests_per_rolling_window <= 59:
            raise ValueError("IBKR rolling pacing must remain below the provider limit")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "identical_request_cooldown_seconds": self.identical_request_cooldown_seconds,
            "per_contract_window_seconds": self.per_contract_window_seconds,
            "max_requests_per_contract_window": self.max_requests_per_contract_window,
            "rolling_window_seconds": self.rolling_window_seconds,
            "max_requests_per_rolling_window": self.max_requests_per_rolling_window,
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalRequestProfile:
    """Immutable, canary-bound request and pacing policy accepted by the pure planner."""

    canary_evidence_filename: str
    canary_evidence_sha256: str
    frozen_by: str
    frozen_at: datetime
    permitted_bar_durations: tuple[str, ...]
    permitted_schedule_durations: tuple[str, ...]
    bar_duration_by_asset_class: Mapping[AssetClass, str]
    schedule_duration: str
    maximum_in_flight_requests: int
    request_timeout_seconds: int
    retry_count: int
    duplicate_request_protection: str
    pacing_policy: IbkrHistoricalPacingPolicy
    profile_sha256: str

    CONTRACT = REQUEST_PROFILE_CONTRACT
    SCHEMA_VERSION = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_safe_filename(self.canary_evidence_filename, "IBKR canary evidence filename")
        _require_sha256(self.canary_evidence_sha256, "IBKR request-profile canary evidence hash")
        if not self.frozen_by or len(self.frozen_by) > 200:
            raise ValueError("IBKR request profile frozen_by is required and bounded")
        require_utc(self.frozen_at, "IBKR request profile frozen_at")
        if self.duplicate_request_protection != "PLAN_REQUEST_ID_UNIQUE_NO_RERUN":
            raise ValueError("IBKR duplicate-request protection policy is unsupported")
        _require_sha256(self.profile_sha256, "IBKR request-profile hash")
        _validate_duration_set(self.permitted_bar_durations, "IBKR permitted bar durations")
        _validate_duration_set(
            self.permitted_schedule_durations, "IBKR permitted schedule durations"
        )
        if set(self.bar_duration_by_asset_class) != {
            AssetClass.FX,
            AssetClass.INDEX,
            AssetClass.COMMODITY,
        }:
            raise ValueError(
                "IBKR request profile requires FX, INDEX and COMMODITY durations exactly"
            )
        for asset_class, duration in self.bar_duration_by_asset_class.items():
            if asset_class not in {AssetClass.FX, AssetClass.INDEX, AssetClass.COMMODITY}:
                raise ValueError("IBKR request profile has an unsupported asset class")
            if duration not in self.permitted_bar_durations:
                raise ValueError("IBKR product bar duration must be explicitly permitted")
        if self.schedule_duration not in self.permitted_schedule_durations:
            raise ValueError("IBKR schedule duration must be explicitly permitted")
        if not 1 <= self.maximum_in_flight_requests <= 10:
            raise ValueError("IBKR maximum in-flight requests must be between one and ten")
        if not 1 <= self.request_timeout_seconds <= 300:
            raise ValueError("IBKR request timeout must be between one and 300 seconds")
        if not 0 <= self.retry_count <= 5:
            raise ValueError("IBKR retry count must be between zero and five")
        if self.profile_sha256 != _sha256_json(self.identity_payload()):
            raise ValueError("IBKR request-profile hash does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "canary_evidence_filename": self.canary_evidence_filename,
            "canary_evidence_sha256": self.canary_evidence_sha256,
            "frozen_by": self.frozen_by,
            "frozen_at": _utc_text(self.frozen_at),
            "permitted_bar_durations": list(self.permitted_bar_durations),
            "permitted_schedule_durations": list(self.permitted_schedule_durations),
            "bar_duration_by_asset_class": {
                item.value: self.bar_duration_by_asset_class[item]
                for item in sorted(self.bar_duration_by_asset_class, key=lambda value: value.value)
            },
            "schedule_duration": self.schedule_duration,
            "maximum_in_flight_requests": self.maximum_in_flight_requests,
            "request_timeout_seconds": self.request_timeout_seconds,
            "retry_count": self.retry_count,
            "duplicate_request_protection": self.duplicate_request_protection,
            "pacing_policy": self.pacing_policy.as_json_value(),
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "profile_sha256": self.profile_sha256}


@dataclass(frozen=True, slots=True)
class IbkrPlannedContract:
    """The acquisition-eligible exact selection closure copied into a plan for file-only replay."""

    instrument_id: InstrumentId
    fingerprint: IbkrContractFingerprint

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "instrument_id": str(self.instrument_id),
            "fingerprint": self.fingerprint.as_json_value(),
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalRequest:
    """One exact request and its half-open ownership interval."""

    instrument_id: InstrumentId
    fingerprint: IbkrContractFingerprint
    kind: IbkrHistoricalRequestKind
    interval_start: datetime
    interval_end: datetime
    end_date_time: str
    duration: str
    bar_size: str | None
    what_to_show: str | None
    use_rth: bool
    format_date: int | None
    keep_up_to_date: bool
    request_sha256: str

    def __post_init__(self) -> None:
        require_utc(self.interval_start, "IBKR request interval start")
        require_utc(self.interval_end, "IBKR request interval end")
        if self.interval_end <= self.interval_start:
            raise ValueError("IBKR request interval must be non-empty")
        if any(
            value.second or value.microsecond for value in (self.interval_start, self.interval_end)
        ):
            raise ValueError("IBKR request interval must align to UTC minutes")
        if self.end_date_time != ibkr_end_date_time(self.interval_end):
            raise ValueError("IBKR request endDateTime must equal the UTC interval end")
        duration = _duration_delta(self.duration)
        if self.interval_end - self.interval_start > duration:
            raise ValueError("IBKR request ownership interval exceeds its provider duration")
        if self.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
            expected_what_to_show = (
                "TRADES" if self.fingerprint.security_type == "IND" else "MIDPOINT"
            )
            if (
                self.bar_size != "1 min"
                or self.what_to_show != expected_what_to_show
                or self.use_rth
                or self.format_date != 2
                or self.keep_up_to_date
            ):
                raise ValueError(
                    "IBKR one-minute bar request parameters are not the frozen profile"
                )
        elif self.kind is IbkrHistoricalRequestKind.SCHEDULE:
            if (
                self.bar_size != "1 day"
                or self.what_to_show != "SCHEDULE"
                or self.use_rth
                or self.format_date != 2
                or self.keep_up_to_date
            ):
                raise ValueError(
                    "IBKR schedule request parameters are not the frozen one-day profile"
                )
        else:
            raise ValueError("IBKR historical request kind is unsupported")
        _require_sha256(self.request_sha256, "IBKR historical request hash")
        if self.request_sha256 != _sha256_json(self.identity_payload()):
            raise ValueError("IBKR historical request hash does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "instrument_id": str(self.instrument_id),
            "fingerprint": self.fingerprint.as_json_value(),
            "kind": self.kind.value,
            "interval_start": _utc_text(self.interval_start),
            "interval_end": _utc_text(self.interval_end),
            "end_date_time": self.end_date_time,
            "duration": self.duration,
            "bar_size": self.bar_size,
            "what_to_show": self.what_to_show,
            "use_rth": self.use_rth,
            "format_date": self.format_date,
            "keep_up_to_date": self.keep_up_to_date,
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "request_sha256": self.request_sha256}


@dataclass(frozen=True, slots=True)
class IbkrHistoricalPlan:
    """Create-only, database-independent collection of exact IBKR historical requests."""

    contract_selection_sha256: str
    runtime_sha256: str
    request_profile_sha256: str
    provider: str
    environment: str
    planner_qtrad_commit: str
    planner_qtrad_image_digest: str
    start: datetime
    end: datetime
    eligible_contracts: tuple[IbkrPlannedContract, ...]
    requests: tuple[IbkrHistoricalRequest, ...]
    plan_sha256: str

    CONTRACT = HISTORICAL_PLAN_CONTRACT
    SCHEMA_VERSION = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name, value in (
            ("contract selection hash", self.contract_selection_sha256),
            ("runtime hash", self.runtime_sha256),
            ("request-profile hash", self.request_profile_sha256),
            ("plan hash", self.plan_sha256),
        ):
            _require_sha256(value, f"IBKR historical {field_name}")
        if self.provider != "ibkr" or self.environment != "paper":
            raise ValueError("IBKR historical plan supports only the IBKR paper provider")
        if not _COMMIT.fullmatch(self.planner_qtrad_commit):
            raise ValueError("IBKR historical planner commit must be a full lower-case Git commit")
        if not _IMAGE.fullmatch(self.planner_qtrad_image_digest):
            raise ValueError("IBKR historical planner image must be an immutable sha256 digest")
        require_utc(self.start, "IBKR historical plan start")
        require_utc(self.end, "IBKR historical plan end")
        if self.end <= self.start:
            raise ValueError("IBKR historical plan end must follow start")
        if any(value.second or value.microsecond for value in (self.start, self.end)):
            raise ValueError("IBKR historical plan range must align to UTC minutes")
        if not self.eligible_contracts:
            raise ValueError("IBKR historical plan requires at least one eligible contract")
        expected = {
            contract.instrument_id: contract.fingerprint for contract in self.eligible_contracts
        }
        if len(expected) != len(self.eligible_contracts):
            raise ValueError("IBKR historical plan eligible contracts must be unique")
        if not self.requests:
            raise ValueError("IBKR historical plan requires requests")
        if len({request.request_sha256 for request in self.requests}) != len(self.requests):
            raise ValueError("IBKR historical plan request identities must be unique")
        observed = {request.instrument_id for request in self.requests}
        if observed != set(expected):
            raise ValueError(
                "IBKR historical plan request contracts do not match its eligible closure"
            )
        for request in self.requests:
            if request.fingerprint != expected[request.instrument_id]:
                raise ValueError(
                    "IBKR historical request fingerprint differs from its eligible closure"
                )
        for instrument_id in expected:
            for kind in IbkrHistoricalRequestKind:
                _verify_request_coverage(
                    [
                        request
                        for request in self.requests
                        if request.instrument_id == instrument_id and request.kind is kind
                    ],
                    self.start,
                    self.end,
                    instrument_id,
                    kind,
                )
        if self.plan_sha256 != _sha256_json(self.identity_payload()):
            raise ValueError("IBKR historical plan hash does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "contract_selection_sha256": self.contract_selection_sha256,
            "runtime_sha256": self.runtime_sha256,
            "request_profile_sha256": self.request_profile_sha256,
            "provider": self.provider,
            "environment": self.environment,
            "planner_qtrad_commit": self.planner_qtrad_commit,
            "planner_qtrad_image_digest": self.planner_qtrad_image_digest,
            "start": _utc_text(self.start),
            "end": _utc_text(self.end),
            "eligible_contracts": [
                item.as_json_value()
                for item in sorted(
                    self.eligible_contracts, key=lambda value: str(value.instrument_id)
                )
            ],
            "requests": [
                item.as_json_value()
                for item in sorted(self.requests, key=_historical_request_sort_key)
            ],
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "plan_sha256": self.plan_sha256}


def ibkr_end_date_time(value: datetime) -> str:
    """Return the exact UTC endDateTime accepted by the planned IBKR request contract."""

    require_utc(value, "IBKR request endDateTime")
    if value.second or value.microsecond:
        raise ValueError("IBKR request endDateTime must align to UTC minutes")
    return value.strftime("%Y%m%d-%H:%M:%S UTC")


def duration_timedelta(value: str) -> timedelta:
    """Parse the deliberately small, calendar-free safe duration language for one-minute history."""

    return _duration_delta(value)


def _duration_delta(value: str) -> timedelta:
    matched = _DURATION.fullmatch(value)
    if matched is None:
        raise ValueError("IBKR duration must be a canonical whole-day or whole-week string")
    amount = int(matched.group(1))
    days = amount * (7 if matched.group(2) == "W" else 1)
    if days > 28:
        raise ValueError(
            "IBKR one-minute duration must not exceed four weeks before canary qualification"
        )
    return timedelta(days=days)


def _validate_duration_set(values: tuple[str, ...], field_name: str) -> None:
    if not values:
        raise ValueError(f"{field_name} are required")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique")
    if tuple(sorted(values, key=lambda value: (_duration_delta(value), value))) != values:
        raise ValueError(f"{field_name} must use ascending canonical duration order")
    for value in values:
        _duration_delta(value)


def _verify_request_coverage(
    requests: Sequence[IbkrHistoricalRequest],
    start: datetime,
    end: datetime,
    instrument_id: InstrumentId,
    kind: IbkrHistoricalRequestKind,
) -> None:
    if not requests:
        raise ValueError(f"IBKR historical plan has no {kind.value} request for {instrument_id}")
    ordered = sorted(requests, key=lambda item: item.interval_start)
    cursor = start
    for request in ordered:
        if request.interval_start != cursor:
            raise ValueError(
                f"IBKR historical {kind.value} requests do not cover {instrument_id} contiguously"
            )
        if request.interval_end > end:
            raise ValueError(f"IBKR historical {kind.value} request exceeds the planned range")
        cursor = request.interval_end
    if cursor != end:
        raise ValueError(f"IBKR historical {kind.value} requests do not reach the planned end")


def _historical_request_sort_key(value: IbkrHistoricalRequest) -> tuple[str, str, datetime, str]:
    return (str(value.instrument_id), value.kind.value, value.interval_start, value.request_sha256)


def _require_safe_filename(value: str, field_name: str) -> None:
    parsed = PurePosixPath(value)
    if not value or len(parsed.parts) != 1 or parsed.name != value or value in {".", ".."}:
        raise ValueError(f"{field_name} must be a safe basename")


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
