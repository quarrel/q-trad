"""Pure verification and create-only publication of IBKR qualification evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_qualification import (
    IBKR_QUALIFICATION_CONTRACT,
    IbkrQualificationStage,
    IbkrQualifiedContract,
    VerifiedIbkrCaptureQualification,
)
from qtrad.domain.identifiers import InstrumentId

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class IbkrQualificationExpectation:
    stage: IbkrQualificationStage
    release_contract: str
    release_sha256: str
    configuration_hash: str
    capture_source_id: str
    universe_id: str
    instruments: frozenset[InstrumentId]
    contracts: tuple[IbkrQualifiedContract, ...]
    application_commit: str
    image_digest: str
    api_package_sha256: str
    gateway_archive_sha256: str
    gateway_version: str
    ibc_version: str
    database_name: str
    schema_head: str
    freshness_threshold: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        for field_name in (
            "release_sha256",
            "configuration_hash",
            "api_package_sha256",
            "gateway_archive_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        if not _COMMIT.fullmatch(self.application_commit):
            raise ValueError("qualification application commit must be a full Git SHA")
        if not _IMAGE.fullmatch(self.image_digest):
            raise ValueError("qualification image must be immutable by digest")
        if not self.instruments:
            raise ValueError("qualification requires at least one expected instrument")
        if (
            len(self.contracts) != len(self.instruments)
            or {item.instrument_id for item in self.contracts} != self.instruments
            or len({item.listing_id for item in self.contracts}) != len(self.contracts)
            or len({item.con_id for item in self.contracts}) != len(self.contracts)
        ):
            raise ValueError("qualification contract identities must be exact and unique")
        if self.freshness_threshold <= timedelta(0):
            raise ValueError("qualification freshness threshold must be positive")


def seal_qualification_artifact(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Return a canonical artifact with a content hash over all evidence fields."""

    if "artifact_sha256" in payload:
        raise ValueError("unsealed qualification payload must omit artifact_sha256")
    sealed = dict(payload)
    sealed["artifact_sha256"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return sealed


def write_qualification_artifact(path: Path, payload: Mapping[str, JsonValue]) -> None:
    """Publish one qualification artifact without replacing prior evidence."""

    sealed = seal_qualification_artifact(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_pretty_bytes(sealed))


def verify_ibkr_capture_qualification(
    path: Path,
    expectation: IbkrQualificationExpectation,
) -> VerifiedIbkrCaptureQualification:
    """Replay one immutable qualification artifact into a runtime-only capability."""

    document = _load_object(path)
    artifact_sha256 = _string(document, "artifact_sha256")
    _require_sha256(artifact_sha256, "qualification artifact_sha256")
    unsigned = {
        key: cast(JsonValue, value) for key, value in document.items() if key != "artifact_sha256"
    }
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != artifact_sha256:
        raise ValueError("IBKR qualification artifact hash does not replay")
    if _string(document, "contract") != IBKR_QUALIFICATION_CONTRACT:
        raise ValueError("IBKR qualification contract marker is unsupported")
    if _string(document, "stage") != expectation.stage:
        raise ValueError("IBKR qualification stage does not match the expected release stage")
    if _string(document, "result") != "QUALIFIED":
        raise ValueError("IBKR qualification result is not QUALIFIED")
    if _string_list(document, "reason_codes"):
        raise ValueError("QUALIFIED IBKR evidence cannot retain reason codes")

    release = _mapping(document, "release")
    _require_equal(release, "contract", expectation.release_contract)
    _require_equal(release, "artifact_sha256", expectation.release_sha256)
    _require_equal(release, "configuration_hash", expectation.configuration_hash)
    _require_equal(release, "capture_source_id", expectation.capture_source_id)
    _require_equal(release, "universe_id", expectation.universe_id)

    runtime = _mapping(document, "runtime")
    _require_equal(runtime, "application_commit", expectation.application_commit)
    _require_equal(runtime, "image_digest", expectation.image_digest)
    _require_equal(runtime, "api_package_sha256", expectation.api_package_sha256)
    _require_equal(runtime, "gateway_archive_sha256", expectation.gateway_archive_sha256)
    _require_equal(runtime, "gateway_version", expectation.gateway_version)
    _require_equal(runtime, "ibc_version", expectation.ibc_version)
    _require_equal(runtime, "database_name", expectation.database_name)
    _require_equal(runtime, "schema_head", expectation.schema_head)

    started_at = _datetime(_mapping(document, "window"), "started_at")
    ended_at = _datetime(_mapping(document, "window"), "ended_at")
    generated_at = _datetime(document, "generated_at")
    if not started_at < ended_at <= generated_at:
        raise ValueError("IBKR qualification window ordering is invalid")

    sessions = _string_list(document, "capture_session_ids")
    if not sessions or len(set(sessions)) != len(sessions):
        raise ValueError("IBKR qualification requires distinct capture sessions")
    generations = _integer_list(document, "connection_generations")
    if (
        not generations
        or any(value < 1 for value in generations)
        or len(set(generations)) != len(generations)
    ):
        raise ValueError("IBKR qualification generations are invalid")

    expected_ids = {str(item) for item in expectation.instruments}
    expected_contracts = {item.instrument_id: item for item in expectation.contracts}
    subscriptions = _mapping(document, "subscriptions")
    for field in ("configured", "desired", "active"):
        values = set(_string_list(subscriptions, field))
        if values != expected_ids:
            raise ValueError(f"IBKR qualification {field} subscriptions are not exact")

    instruments = _sequence(document, "instruments")
    if len(instruments) != len(expected_ids):
        raise ValueError("IBKR qualification instrument cardinality is not exact")
    observed_ids: set[str] = set()
    for raw in instruments:
        item = _object(raw, "qualification instrument")
        instrument_id = _string(item, "instrument_id")
        if instrument_id not in expected_ids or instrument_id in observed_ids:
            raise ValueError("IBKR qualification instrument set is not exact")
        observed_ids.add(instrument_id)
        contract = expected_contracts[InstrumentId(instrument_id)]
        if (
            _string(item, "listing_id") != str(contract.listing_id)
            or _integer(item, "con_id") != contract.con_id
        ):
            raise ValueError(f"{instrument_id} contract identity is not exact")
        if _string(item, "market_activity") != "ACTIVE":
            raise ValueError(f"{instrument_id} lacks authenticated ACTIVE evidence")
        if _string(item, "market_data_type") != "LIVE":
            raise ValueError(f"{instrument_id} did not produce LIVE market data")
        active_started_at = _datetime(item, "active_started_at")
        active_ended_at = _datetime(item, "active_ended_at")
        if not started_at <= active_started_at < active_ended_at <= ended_at:
            raise ValueError(
                f"{instrument_id} ACTIVE evidence lies outside the qualification window"
            )
        for side in ("bid", "ask"):
            if _integer(item, f"{side}_count") < 1:
                raise ValueError(f"{instrument_id} lacks {side} evidence")
            first_at = _datetime(item, f"first_{side}_at")
            recent_at = _datetime(item, f"recent_{side}_at")
            if not active_started_at <= first_at <= recent_at <= active_ended_at:
                raise ValueError(f"{instrument_id} {side} chronology is invalid")
            if active_ended_at - recent_at > expectation.freshness_threshold:
                raise ValueError(f"{instrument_id} {side} evidence was stale while ACTIVE")

    persistence = _mapping(document, "persistence")
    received = _integer(persistence, "records_received")
    persisted = _integer(persistence, "persisted")
    if received < 1 or persisted != received:
        raise ValueError("IBKR qualification persistence is incomplete")
    for field in ("failed", "dropped", "reconciliation_loss"):
        if _integer(persistence, field) != 0:
            raise ValueError(f"IBKR qualification persistence {field} is non-zero")

    health = _mapping(document, "health")
    if _string(health, "status") != "HEALTHY":
        raise ValueError("IBKR qualification health is not HEALTHY")
    health_at = _datetime(health, "observed_at")
    if not started_at <= health_at <= generated_at:
        raise ValueError("IBKR qualification health observation is outside the evidence window")

    reconnect = _mapping(document, "reconnect")
    from_generation = _integer(reconnect, "from_generation")
    to_generation = _integer(reconnect, "to_generation")
    if from_generation >= to_generation or {from_generation, to_generation} - set(generations):
        raise ValueError("IBKR qualification reconnect generations are invalid")
    for field in ("expected_subscriptions", "reconstructed_subscriptions", "fresh_instruments"):
        if set(_string_list(reconnect, field)) != expected_ids:
            raise ValueError(f"IBKR qualification reconnect {field} is not exact")
    if _integer(reconnect, "duplicate_subscriptions") != 0:
        raise ValueError("IBKR qualification reconnect duplicated subscriptions")
    if _integer(reconnect, "stale_generation_callbacks") != 0:
        raise ValueError("IBKR qualification reconnect retained stale-generation callbacks")

    backup = _mapping(document, "backup_restore")
    if backup.get("verified") is not True:
        raise ValueError("IBKR qualification backup/restore is not verified")
    _require_equal(backup, "configuration_hash", expectation.configuration_hash)
    backup_at = _datetime(backup, "observed_at")
    if not ended_at <= backup_at <= generated_at:
        raise ValueError("IBKR qualification backup/restore chronology is invalid")

    return VerifiedIbkrCaptureQualification._from_verified_artifact(
        stage=expectation.stage,
        artifact_sha256=artifact_sha256,
        release_contract=expectation.release_contract,
        release_sha256=expectation.release_sha256,
        configuration_hash=expectation.configuration_hash,
        capture_source_id=expectation.capture_source_id,
        universe_id=expectation.universe_id,
        instruments=expectation.instruments,
        contracts=expectation.contracts,
        qualified_at=generated_at,
    )


def _load_object(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read IBKR qualification artifact: {path}") from error
    return _object(value, "IBKR qualification artifact")


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _mapping(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    return _object(value.get(field), f"IBKR qualification {field}")


def _sequence(value: Mapping[str, object], field: str) -> Sequence[object]:
    raw = value.get(field)
    if not isinstance(raw, list):
        raise ValueError(f"IBKR qualification {field} must be an array")
    return cast(Sequence[object], raw)


def _string(value: Mapping[str, object], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"IBKR qualification {field} must be a non-empty string")
    return raw


def _string_list(value: Mapping[str, object], field: str) -> tuple[str, ...]:
    raw = _sequence(value, field)
    if any(not isinstance(item, str) or not item for item in raw):
        raise ValueError(f"IBKR qualification {field} must contain strings")
    return tuple(cast(str, item) for item in raw)


def _integer(value: Mapping[str, object], field: str) -> int:
    raw = value.get(field)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"IBKR qualification {field} must be an integer")
    return raw


def _integer_list(value: Mapping[str, object], field: str) -> tuple[int, ...]:
    raw = _sequence(value, field)
    if any(not isinstance(item, int) or isinstance(item, bool) for item in raw):
        raise ValueError(f"IBKR qualification {field} must contain integers")
    return tuple(cast(int, item) for item in raw)


def _datetime(value: Mapping[str, object], field: str) -> datetime:
    raw = _string(value, field)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ValueError(f"IBKR qualification {field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"IBKR qualification {field} must be UTC")
    return parsed.astimezone(UTC)


def _require_equal(value: Mapping[str, object], field: str, expected: str) -> None:
    if _string(value, field) != expected:
        raise ValueError(f"IBKR qualification {field} identity mismatch")


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")


def _canonical_bytes(value: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _pretty_bytes(value: Mapping[str, JsonValue]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
