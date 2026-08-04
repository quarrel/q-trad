"""Domain contracts for verified IBKR provider-history observations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.ibkr_results import canonical_json_bytes
from qtrad.domain.time import require_utc

PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT = "qtrad-provider-historical-observations-v1"
PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT = (
    "qtrad-provider-history-availability-selector-v1"
)
PROVIDER_HISTORY_SCHEMA_VERSION = 1
PROVIDER_HISTORY_SOURCE_CLASS = "IBKR_HISTORICAL_RESEARCH"
PROVIDER_HISTORY_PROVIDER = "ibkr"
PROVIDER_HISTORY_ENVIRONMENT = "paper"
PROVIDER_HISTORY_BAR_BASIS = "MIDPOINT"
PROVIDER_HISTORY_POLICY = "BAR_END_PLUS_DECLARED_PROVIDER_DELAY"
PROVIDER_HISTORY_CORRECTION_POLICY = (
    "FROZEN_FIRST_SUCCESSFUL_RESPONSE_NO_REFETCH_MERGE"
)
NATIVE_MEASURED_AVAILABILITY = "NATIVE_MEASURED_AVAILABILITY"
PROVIDER_HISTORY_DECLARED_DELAY = "PROVIDER_HISTORY_DECLARED_DELAY"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DURATION = re.compile(
    r"^PT(?:(?P<hours>[0-9]+)H)?(?:(?P<minutes>[0-9]+)M)?(?:(?P<seconds>[0-9]+)S)?$"
)
_FORBIDDEN_NATIVE_FIELDS = {"received_at", "persisted_at"}


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def utc_text(value: datetime) -> str:
    require_utc(value, "provider-history timestamp")
    return value.isoformat().replace("+00:00", "Z")


def parse_declared_delay(value: str) -> timedelta:
    match = _DURATION.fullmatch(value)
    if match is None or all(part is None for part in match.groupdict().values()):
        raise ValueError("provider-history availability delay must be an ISO-8601 time duration")
    seconds = (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )
    delay = timedelta(seconds=seconds)
    if delay > timedelta(days=7):
        raise ValueError("provider-history availability delay is unreasonably large")
    return delay


def duration_text(value: timedelta) -> str:
    if value < timedelta(0) or value.microseconds:
        raise ValueError("provider-history availability delay must be whole seconds")
    seconds = int(value.total_seconds())
    if seconds % 3600 == 0:
        return f"PT{seconds // 3600}H"
    if seconds % 60 == 0:
        return f"PT{seconds // 60}M"
    return f"PT{seconds}S"


def _require_sha256(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")


def _object(value: object, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a JSON object")
    converted = to_json_value(cast(Mapping[str, JsonValue], value))
    if not isinstance(converted, dict):
        raise TypeError(f"{field} must serialise to a JSON object")
    return converted


def _reject_native_fields(value: object, field: str) -> None:
    if isinstance(value, Mapping):
        for key, child in cast(Mapping[str, object], value).items():
            if key in _FORBIDDEN_NATIVE_FIELDS:
                raise ValueError(f"{field} must not contain native timestamp field {key}")
            _reject_native_fields(child, f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(cast(list[object] | tuple[object, ...], value)):
            _reject_native_fields(child, f"{field}[{index}]")


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


def _positive_text(value: str, field: str) -> None:
    if not value or len(value) > 200 or any(character.isspace() for character in value):
        raise ValueError(f"{field} must be bounded and non-empty")


class ProviderHistoryAvailabilitySelector(StrEnum):
    """Versioned selectors understood at the foundation boundary."""

    NATIVE_MEASURED_AVAILABILITY = NATIVE_MEASURED_AVAILABILITY
    PROVIDER_HISTORY_DECLARED_DELAY = PROVIDER_HISTORY_DECLARED_DELAY


@dataclass(frozen=True, slots=True)
class ProviderHistoricalAvailabilityPolicy:
    """Authenticated, declared availability calculation for provider history."""

    selector: str
    policy: str
    delay: timedelta

    CONTRACT = PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT
    SCHEMA_VERSION = PROVIDER_HISTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.selector != PROVIDER_HISTORY_DECLARED_DELAY:
            raise ValueError("provider-history availability selector is unsupported")
        if self.policy != PROVIDER_HISTORY_POLICY:
            raise ValueError("provider-history availability policy is unsupported")
        parse_delay = parse_declared_delay(duration_text(self.delay))
        if parse_delay != self.delay:
            raise ValueError("provider-history availability delay is not canonical")

    @property
    def delay_text(self) -> str:
        return duration_text(self.delay)

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selector": self.selector,
            "policy": self.policy,
            "delay": self.delay_text,
            "formula": "interval_end_plus_declared_delay",
        }

    @classmethod
    def from_json_value(cls, value: object) -> ProviderHistoricalAvailabilityPolicy:
        data = _object(value, "availability policy")
        expected = {"contract", "schema_version", "selector", "policy", "delay", "formula"}
        if set(data) != expected:
            raise ValueError("availability policy fields are not exact")
        if data["contract"] != cls.CONTRACT or data["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("availability policy contract or schema is unsupported")
        if data["formula"] != "interval_end_plus_declared_delay":
            raise ValueError("availability policy formula is unsupported")
        delay_text = data["delay"]
        if not isinstance(delay_text, str):
            raise TypeError("availability policy delay must be a string")
        delay = parse_declared_delay(delay_text)
        if duration_text(delay) != delay_text:
            raise ValueError("availability policy delay is not canonical")
        return cls(
            selector=str(data["selector"]),
            policy=str(data["policy"]),
            delay=delay,
        )


@dataclass(frozen=True, slots=True)
class ProviderHistoricalObservation:
    """One provider-history bar without native receive or persistence timestamps."""

    source_class: str
    provider: str
    environment: str
    instrument_id: str
    contract_selection_identity: str
    plan_sha256: str
    interval_start: datetime
    interval_end: datetime
    basis: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    request_sha256: str
    result_sha256: str
    aggregate_sha256: str
    attempt_id: UUID
    attempt_started_at: datetime
    attempt_completed_at: datetime
    acquisition_started_at: datetime
    acquisition_completed_at: datetime
    available_at: datetime
    availability_selector: str
    availability_policy: str
    availability_delay: str
    correction_policy: str
    schedule_evidence: Mapping[str, JsonValue]
    gap_disposition: str
    volume: str | None = None
    wap: str | None = None
    count: int | None = None
    callback_sequence: int | None = None
    observation_sha256: str = ""
    _identity_validation_bypass: bool = field(default=False, repr=False, compare=False)

    CONTRACT = PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT
    SCHEMA_VERSION = PROVIDER_HISTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.source_class != PROVIDER_HISTORY_SOURCE_CLASS:
            raise ValueError("provider-history source class is unsupported")
        if (
            self.provider != PROVIDER_HISTORY_PROVIDER
            or self.environment != PROVIDER_HISTORY_ENVIRONMENT
        ):
            raise ValueError("provider-history provider/environment is unsupported")
        if self.basis != PROVIDER_HISTORY_BAR_BASIS:
            raise ValueError("provider-history bar basis must be MIDPOINT")
        _positive_text(self.instrument_id, "provider-history instrument")
        _require_sha256(
            self.contract_selection_identity,
            "provider-history contract selection identity",
        )
        _require_sha256(self.plan_sha256, "provider-history plan identity")
        _require_sha256(self.request_sha256, "provider-history request identity")
        _require_sha256(self.result_sha256, "provider-history request-result identity")
        _require_sha256(self.aggregate_sha256, "provider-history aggregate identity")
        _require_sha256(self.observation_sha256, "provider-history observation identity")
        require_utc(self.interval_start, "provider-history interval start")
        require_utc(self.interval_end, "provider-history interval end")
        if self.interval_end <= self.interval_start:
            raise ValueError("provider-history interval must be non-empty")
        if any(
            value.second or value.microsecond
            for value in (self.interval_start, self.interval_end)
        ):
            raise ValueError("provider-history interval must align to UTC minutes")
        for field_name, value in (
            ("attempt start", self.attempt_started_at),
            ("attempt completion", self.attempt_completed_at),
            ("acquisition start", self.acquisition_started_at),
            ("acquisition completion", self.acquisition_completed_at),
            ("available_at", self.available_at),
        ):
            require_utc(value, f"provider-history {field_name}")
        if self.attempt_completed_at < self.attempt_started_at:
            raise ValueError("provider-history attempt completion precedes attempt start")
        if self.acquisition_completed_at < self.acquisition_started_at:
            raise ValueError("provider-history acquisition completion precedes acquisition start")
        delay = parse_declared_delay(self.availability_delay)
        if self.availability_selector != PROVIDER_HISTORY_DECLARED_DELAY:
            raise ValueError("provider-history availability selector is unsupported")
        if self.availability_policy != PROVIDER_HISTORY_POLICY:
            raise ValueError("provider-history availability policy is unsupported")
        if self.available_at != self.interval_end + delay:
            raise ValueError("provider-history available_at does not match declared delay")
        if self.correction_policy != PROVIDER_HISTORY_CORRECTION_POLICY:
            raise ValueError("provider-history correction policy is unsupported")
        if not self.gap_disposition or len(self.gap_disposition) > 100:
            raise ValueError("provider-history gap disposition is required")
        schedule = _object(self.schedule_evidence, "provider-history schedule evidence")
        _reject_native_fields(schedule, "provider-history schedule evidence")
        if self.count is not None and self.count < 0:
            raise ValueError("provider-history bar count cannot be negative")
        for field_name, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"provider-history {field_name} must be a non-negative finite decimal"
                )
        if self.high < max(self.open, self.close, self.low) or self.low > min(
            self.open, self.close, self.high
        ):
            raise ValueError("provider-history OHLC values are inconsistent")
        for field_name, value in (("volume", self.volume), ("wap", self.wap)):
            if value is not None:
                _decimal(value, f"provider-history {field_name}")
        if self.callback_sequence is not None and self.callback_sequence < 0:
            raise ValueError("provider-history callback sequence cannot be negative")
        if (
            not self._identity_validation_bypass
            and self.observation_sha256 != sha256_json(self.identity_payload())
        ):
            raise ValueError(
                "provider-history observation identity does not match canonical content"
            )

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "source_class": self.source_class,
            "provider": self.provider,
            "environment": self.environment,
            "instrument_id": self.instrument_id,
            "contract_selection_identity": self.contract_selection_identity,
            "plan_sha256": self.plan_sha256,
            "interval_start": utc_text(self.interval_start),
            "interval_end": utc_text(self.interval_end),
            "basis": self.basis,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": self.volume,
            "wap": self.wap,
            "count": self.count,
            "callback_sequence": self.callback_sequence,
            "request_sha256": self.request_sha256,
            "result_sha256": self.result_sha256,
            "aggregate_sha256": self.aggregate_sha256,
            "attempt_id": str(self.attempt_id),
            "attempt_started_at": utc_text(self.attempt_started_at),
            "attempt_completed_at": utc_text(self.attempt_completed_at),
            "acquisition_started_at": utc_text(self.acquisition_started_at),
            "acquisition_completed_at": utc_text(self.acquisition_completed_at),
            "available_at": utc_text(self.available_at),
            "availability_selector": self.availability_selector,
            "availability_policy": self.availability_policy,
            "availability_delay": self.availability_delay,
            "correction_policy": self.correction_policy,
            "schedule_evidence": dict(self.schedule_evidence),
            "gap_disposition": self.gap_disposition,
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "observation_sha256": self.observation_sha256}

    @classmethod
    def create(cls, **values: object) -> ProviderHistoricalObservation:
        provisional = cls(
            **cast(dict[str, Any], values),
            observation_sha256="0" * 64,
            _identity_validation_bypass=True,
        )
        return replace(
            provisional,
            observation_sha256=sha256_json(provisional.identity_payload()),
            _identity_validation_bypass=False,
        )

    @classmethod
    def from_json_value(cls, value: object) -> ProviderHistoricalObservation:
        data = _object(value, "provider-history observation")
        expected = {
            "contract",
            "schema_version",
            "source_class",
            "provider",
            "environment",
            "instrument_id",
            "contract_selection_identity",
            "plan_sha256",
            "interval_start",
            "interval_end",
            "basis",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "wap",
            "count",
            "callback_sequence",
            "request_sha256",
            "result_sha256",
            "aggregate_sha256",
            "attempt_id",
            "attempt_started_at",
            "attempt_completed_at",
            "acquisition_started_at",
            "acquisition_completed_at",
            "available_at",
            "availability_selector",
            "availability_policy",
            "availability_delay",
            "correction_policy",
            "schedule_evidence",
            "gap_disposition",
            "observation_sha256",
        }
        if set(data) != expected:
            raise ValueError("provider-history observation fields are not exact")
        if data["contract"] != cls.CONTRACT or data["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("provider-history observation contract or schema is unsupported")
        return cls(
            source_class=str(data["source_class"]),
            provider=str(data["provider"]),
            environment=str(data["environment"]),
            instrument_id=str(data["instrument_id"]),
            contract_selection_identity=str(data["contract_selection_identity"]),
            plan_sha256=str(data["plan_sha256"]),
            interval_start=_parse_time(str(data["interval_start"]), "interval_start"),
            interval_end=_parse_time(str(data["interval_end"]), "interval_end"),
            basis=str(data["basis"]),
            open=_decimal(str(data["open"]), "open"),
            high=_decimal(str(data["high"]), "high"),
            low=_decimal(str(data["low"]), "low"),
            close=_decimal(str(data["close"]), "close"),
            request_sha256=str(data["request_sha256"]),
            result_sha256=str(data["result_sha256"]),
            aggregate_sha256=str(data["aggregate_sha256"]),
            attempt_id=UUID(str(data["attempt_id"])),
            attempt_started_at=_parse_time(str(data["attempt_started_at"]), "attempt_started_at"),
            attempt_completed_at=_parse_time(
                str(data["attempt_completed_at"]), "attempt_completed_at"
            ),
            acquisition_started_at=_parse_time(
                str(data["acquisition_started_at"]), "acquisition_started_at"
            ),
            acquisition_completed_at=_parse_time(
                str(data["acquisition_completed_at"]), "acquisition_completed_at"
            ),
            available_at=_parse_time(str(data["available_at"]), "available_at"),
            availability_selector=str(data["availability_selector"]),
            availability_policy=str(data["availability_policy"]),
            availability_delay=str(data["availability_delay"]),
            correction_policy=str(data["correction_policy"]),
            schedule_evidence=_object(
                data["schedule_evidence"], "provider-history schedule evidence"
            ),
            gap_disposition=str(data["gap_disposition"]),
            volume=None if data["volume"] is None else str(data["volume"]),
            wap=None if data["wap"] is None else str(data["wap"]),
            count=None if data["count"] is None else _json_int(data["count"], "count"),
            callback_sequence=(
                None
                if data["callback_sequence"] is None
                else _json_int(data["callback_sequence"], "callback_sequence")
            ),
            observation_sha256=str(data["observation_sha256"]),
        )


def _json_int(value: JsonValue, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"provider-history {field} must be an integer")
    return value


def _parse_time(value: str, field: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError(f"provider-history {field} must use UTC Z notation")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"provider-history {field} is not an ISO timestamp") from exc
    require_utc(result, f"provider-history {field}")
    return result


@dataclass(frozen=True, slots=True)
class ProviderHistoricalDataset:
    """Semantic identity for the complete provider-history observation set."""

    rows: tuple[ProviderHistoricalObservation, ...]
    contract_selection_sha256: str
    plan_sha256: str
    runtime_sha256: str
    aggregate_sha256: str
    availability_policy: ProviderHistoricalAvailabilityPolicy
    dataset_sha256: str
    _identity_validation_bypass: bool = field(default=False, repr=False, compare=False)

    CONTRACT = PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT
    SCHEMA_VERSION = PROVIDER_HISTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name, value in (
            ("contract selection identity", self.contract_selection_sha256),
            ("plan identity", self.plan_sha256),
            ("runtime identity", self.runtime_sha256),
            ("aggregate identity", self.aggregate_sha256),
            ("dataset identity", self.dataset_sha256),
        ):
            _require_sha256(value, f"provider-history {field_name}")
        if tuple(sorted(self.rows, key=lambda row: row_sort_key(row))) != self.rows:
            raise ValueError("provider-history rows must be in canonical order")
        identities = [row.observation_sha256 for row in self.rows]
        if len(set(identities)) != len(identities):
            raise ValueError("provider-history observation identities must be unique")
        for row in self.rows:
            if (
                row.contract_selection_identity != self.contract_selection_sha256
                or row.plan_sha256 != self.plan_sha256
                or row.aggregate_sha256 != self.aggregate_sha256
            ):
                raise ValueError("provider-history row lineage differs from dataset lineage")
            if row.availability_delay != self.availability_policy.delay_text:
                raise ValueError("provider-history row delay differs from dataset policy")
        if (
            not self._identity_validation_bypass
            and self.dataset_sha256 != sha256_json(self.identity_payload())
        ):
            raise ValueError("provider-history dataset identity does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "source_class": PROVIDER_HISTORY_SOURCE_CLASS,
            "provider": PROVIDER_HISTORY_PROVIDER,
            "environment": PROVIDER_HISTORY_ENVIRONMENT,
            "contract_selection_sha256": self.contract_selection_sha256,
            "plan_sha256": self.plan_sha256,
            "runtime_sha256": self.runtime_sha256,
            "aggregate_sha256": self.aggregate_sha256,
            "availability_policy": self.availability_policy.as_json_value(),
            "observation_sha256": [row.observation_sha256 for row in self.rows],
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            **self.identity_payload(),
            "row_count": len(self.rows),
            "dataset_sha256": self.dataset_sha256,
        }

    @classmethod
    def create(
        cls,
        rows: tuple[ProviderHistoricalObservation, ...],
        *,
        contract_selection_sha256: str,
        plan_sha256: str,
        runtime_sha256: str,
        aggregate_sha256: str,
        availability_policy: ProviderHistoricalAvailabilityPolicy,
    ) -> ProviderHistoricalDataset:
        ordered = tuple(sorted(rows, key=row_sort_key))
        provisional = cls(
            rows=ordered,
            contract_selection_sha256=contract_selection_sha256,
            plan_sha256=plan_sha256,
            runtime_sha256=runtime_sha256,
            aggregate_sha256=aggregate_sha256,
            availability_policy=availability_policy,
            dataset_sha256="0" * 64,
            _identity_validation_bypass=True,
        )
        return replace(
            provisional,
            dataset_sha256=sha256_json(provisional.identity_payload()),
            _identity_validation_bypass=False,
        )


def row_sort_key(row: ProviderHistoricalObservation) -> tuple[str, datetime, str]:
    return (row.instrument_id, row.interval_start, row.request_sha256)


def canonical_json(value: Mapping[str, JsonValue]) -> bytes:
    return canonical_json_bytes(value)
