"""Read-only PostgreSQL replay for IBKR native-capture qualification.

The snapshot is evidence, never authority.  Authority is minted only after a
second read independently reconstructs the same retained rows from both the
live capture database and a distinct restored database.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from qtrad.adapters.ibkr.market_hours import IbkrMarketActivity
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_qualification import (
    _VERIFIED_IBKR_CAPTURE_QUALIFICATION_TOKEN,
    IBKR_QUALIFICATION_CONTRACT,
    VerifiedIbkrCaptureQualification,
)
from qtrad.runtime.ibkr_native_capture import IbkrNativeCaptureConfiguration
from qtrad.runtime.ibkr_qualification import (
    IbkrQualificationExpectation,
    _verify_ibkr_qualification_summary,
)


class QualificationEvidenceStore(Protocol):
    async def query(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> list[dict[str, Any]]: ...


_RESTORE_EVIDENCE_CONTRACT = "qtrad-ibkr-postgres-restore-v1"
_IBKR_BACKUP_ROOT = Path("/srv/qtrad/postgres/backups")
_RESTORE_EVIDENCE_ROOT = Path("/var/lib/qtrad/ibkr/restore-evidence")
_VERIFIED_RESTORE_EVIDENCE_TOKEN = object()


class VerifiedIbkrRestoreEvidence:
    """Opaque evidence from the hash-checked disposable restore workflow."""

    __slots__ = (
        "_archive_path",
        "_archive_sha256",
        "_artifact_sha256",
        "_completed_at",
        "_restored_database_name",
        "_schema_head",
        "_source_database_name",
        "_started_at",
        "_store",
        "_verifier_token",
    )
    _archive_path: Path
    _archive_sha256: str
    _artifact_sha256: str
    _completed_at: datetime
    _restored_database_name: str
    _schema_head: str
    _source_database_name: str
    _started_at: datetime
    _store: QualificationEvidenceStore
    _verifier_token: object

    def __init__(self) -> None:
        raise TypeError("VerifiedIbkrRestoreEvidence is constructed only by its verifier")

    @classmethod
    def _create(
        cls,
        token: object,
        *,
        archive_path: Path,
        archive_sha256: str,
        artifact_sha256: str,
        source_database_name: str,
        restored_database_name: str,
        schema_head: str,
        started_at: datetime,
        completed_at: datetime,
        store: QualificationEvidenceStore,
    ) -> VerifiedIbkrRestoreEvidence:
        if token is not _VERIFIED_RESTORE_EVIDENCE_TOKEN:
            raise TypeError("VerifiedIbkrRestoreEvidence is constructed only by its verifier")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_verifier_token", token)
        object.__setattr__(instance, "_archive_path", archive_path)
        object.__setattr__(instance, "_archive_sha256", archive_sha256)
        object.__setattr__(instance, "_artifact_sha256", artifact_sha256)
        object.__setattr__(instance, "_source_database_name", source_database_name)
        object.__setattr__(instance, "_restored_database_name", restored_database_name)
        object.__setattr__(instance, "_schema_head", schema_head)
        object.__setattr__(instance, "_started_at", started_at)
        object.__setattr__(instance, "_completed_at", completed_at)
        object.__setattr__(instance, "_store", store)
        return instance

    @property
    def archive_sha256(self) -> str:
        return self._archive_sha256

    @property
    def restored_database_name(self) -> str:
        return self._restored_database_name

    @property
    def source_database_name(self) -> str:
        return self._source_database_name

    @property
    def schema_head(self) -> str:
        return self._schema_head

    def authenticates(self, store: QualificationEvidenceStore) -> bool:
        return (
            type(self) is VerifiedIbkrRestoreEvidence
            and getattr(self, "_verifier_token", None) is _VERIFIED_RESTORE_EVIDENCE_TOKEN
            and getattr(self, "_store", None) is store
        )

    def operation_document(self) -> dict[str, JsonValue]:
        return {
            "contract": _RESTORE_EVIDENCE_CONTRACT,
            "artifact_sha256": self._artifact_sha256,
            "archive_path": str(self._archive_path),
            "archive_sha256": self.archive_sha256,
            "source_database_name": self.source_database_name,
            "restored_database_name": self.restored_database_name,
            "schema_head": self.schema_head,
            "started_at": _iso(self._started_at),
            "completed_at": _iso(self._completed_at),
        }


async def verify_ibkr_restore_evidence(
    path: Path,
    restored_store: QualificationEvidenceStore,
    *,
    expected_source_database: str,
    expected_schema_head: str,
) -> VerifiedIbkrRestoreEvidence:
    """Authenticate an archive and the workflow-marked database restored from it."""

    if not _has_postgres_evidence_provenance(restored_store):
        raise TypeError("restore authority requires an exact PostgreSQL evidence store")
    if (
        path.is_symlink()
        or not path.is_file()
        or path.parent.resolve() != _RESTORE_EVIDENCE_ROOT.resolve()
    ):
        raise ValueError("restore evidence must be a workflow-owned regular non-symlink file")
    raw = json.loads(path.read_text(encoding="utf-8"))
    document = _object(raw, "IBKR restore evidence")
    expected_fields = {
        "contract",
        "archive_path",
        "archive_sha256",
        "source_database_name",
        "restored_database_name",
        "schema_head",
        "started_at",
        "completed_at",
        "restore_marker",
        "artifact_sha256",
    }
    if set(document) != expected_fields:
        raise ValueError("IBKR restore evidence fields are not exact")
    artifact_sha256 = _string(document, "artifact_sha256")
    unsigned = {
        key: cast(JsonValue, value) for key, value in document.items() if key != "artifact_sha256"
    }
    if artifact_sha256 != _sha256_json(unsigned):
        raise ValueError("IBKR restore evidence artifact hash mismatch")
    if _string(document, "contract") != _RESTORE_EVIDENCE_CONTRACT:
        raise ValueError("IBKR restore evidence contract mismatch")

    archive_path = Path(_string(document, "archive_path"))
    if (
        not archive_path.is_absolute()
        or archive_path.is_symlink()
        or not archive_path.is_file()
        or archive_path.parent.resolve() != _IBKR_BACKUP_ROOT.resolve()
        or re.fullmatch(r"qtrad-ibkr-[0-9]{8}T[0-9]{6}Z[.]dump", archive_path.name) is None
    ):
        raise ValueError("IBKR restore archive identity is invalid")
    archive_sha256 = _string(document, "archive_sha256")
    if len(archive_sha256) != 64 or any(item not in "0123456789abcdef" for item in archive_sha256):
        raise ValueError("IBKR restore archive SHA-256 is invalid")
    with archive_path.open("rb") as archive:
        observed_archive_sha256 = hashlib.file_digest(archive, "sha256").hexdigest()
    if observed_archive_sha256 != archive_sha256:
        raise ValueError("IBKR restore archive SHA-256 mismatch")
    checksum_path = Path(f"{archive_path}.sha256")
    if checksum_path.is_symlink() or not checksum_path.is_file():
        raise ValueError("IBKR restore archive checksum record is unavailable")
    checksum_parts = checksum_path.read_text(encoding="utf-8").strip().split(maxsplit=1)
    if (
        len(checksum_parts) != 2
        or checksum_parts[0] != archive_sha256
        or Path(checksum_parts[1]).resolve() != archive_path.resolve()
    ):
        raise ValueError("IBKR restore archive checksum record does not authenticate the archive")

    source_database = _string(document, "source_database_name")
    restored_database = _string(document, "restored_database_name")
    schema_head = _string(document, "schema_head")
    if source_database != expected_source_database:
        raise ValueError("IBKR restore source database identity mismatch")
    if not restored_database.startswith(f"{source_database}_restore_verify_"):
        raise ValueError("IBKR restore target identity is not disposable")
    if schema_head != expected_schema_head:
        raise ValueError("IBKR restore schema head mismatch")
    started_at = _datetime(document, "started_at")
    completed_at = _datetime(document, "completed_at")
    if completed_at < started_at:
        raise ValueError("IBKR restore completion chronology is invalid")

    identity = await restored_store.query(
        "SELECT current_database() AS database_name, "
        "(SELECT version_num FROM alembic_version) AS schema_head, "
        "shobj_description(oid, 'pg_database') AS restore_marker "
        "FROM pg_database WHERE datname = current_database()"
    )
    if len(identity) != 1:
        raise ValueError("restored database identity is unavailable")
    marker = _string(document, "restore_marker")
    expected_marker = f"{_RESTORE_EVIDENCE_CONTRACT}:{restored_database}:{archive_sha256}"
    if marker != expected_marker or identity[0].get("restore_marker") != marker:
        raise ValueError("restored database lacks authenticated workflow provenance")
    if identity[0].get("database_name") != restored_database:
        raise ValueError("restored database name does not match restore evidence")
    if identity[0].get("schema_head") != schema_head:
        raise ValueError("restored database schema does not match restore evidence")

    return VerifiedIbkrRestoreEvidence._create(
        _VERIFIED_RESTORE_EVIDENCE_TOKEN,
        archive_path=archive_path,
        archive_sha256=archive_sha256,
        artifact_sha256=artifact_sha256,
        source_database_name=source_database,
        restored_database_name=restored_database,
        schema_head=schema_head,
        started_at=started_at,
        completed_at=completed_at,
        store=restored_store,
    )


def _has_postgres_evidence_provenance(value: object) -> bool:
    return (
        type(value) is PostgresAuditStore and type(getattr(value, "_engine", None)) is AsyncEngine
    )


@dataclass(frozen=True, slots=True)
class IbkrQualificationWindow:
    capture_session_id: UUID
    started_at: datetime
    ended_at: datetime
    generated_at: datetime

    def __post_init__(self) -> None:
        for value in (self.started_at, self.ended_at, self.generated_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("qualification evidence times must be UTC")
        if not self.started_at < self.ended_at <= self.generated_at:
            raise ValueError("qualification evidence window ordering is invalid")


async def build_ibkr_qualification_snapshot(
    live_store: QualificationEvidenceStore,
    restored_store: QualificationEvidenceStore,
    *,
    restore_evidence: VerifiedIbkrRestoreEvidence,
    expectation: IbkrQualificationExpectation,
    configuration: IbkrNativeCaptureConfiguration,
    window: IbkrQualificationWindow,
) -> dict[str, JsonValue]:
    """Build one deterministic qualification snapshot without changing either store."""

    if not restore_evidence.authenticates(restored_store):
        raise TypeError("qualification requires verifier-authenticated restore evidence")
    if (
        restore_evidence.source_database_name != expectation.database_name
        or restore_evidence.schema_head != expectation.schema_head
    ):
        raise ValueError("qualification restore authority does not match the release database")
    live = await read_ibkr_qualification_evidence(
        live_store,
        configuration_hash=expectation.configuration_hash,
        window=window,
        include_operations=True,
    )
    restored = await read_ibkr_qualification_evidence(
        restored_store,
        configuration_hash=expectation.configuration_hash,
        window=window,
        include_operations=True,
    )
    if live["database_name"] != expectation.database_name:
        raise ValueError("live qualification database identity mismatch")
    restored_database_name = cast(str, restored["database_name"])
    if not restored_database_name.startswith(f"{expectation.database_name}_restore_verify_"):
        raise ValueError("qualification replay requires a disposable restore-verify database")
    if live["schema_head"] != expectation.schema_head:
        raise ValueError("live qualification database schema head mismatch")
    if restored["schema_head"] != expectation.schema_head:
        raise ValueError("restored qualification database schema head mismatch")
    if live["retained_rows"] != restored["retained_rows"]:
        raise ValueError("restored qualification rows do not exactly replay the live database")
    if live["operations"] != restored["operations"]:
        raise ValueError(
            "restored qualification operations do not exactly replay the live database"
        )

    retained = cast(list[JsonValue], restored["retained_rows"])
    operations = cast(Mapping[str, JsonValue], restored["operations"])
    try:
        summary = _derive_summary(
            retained,
            operations,
            expectation=expectation,
            configuration=configuration,
            window=window,
        )
    except ValueError as error:
        summary = _not_qualified_summary(
            expectation=expectation,
            window=window,
            reason_code=_qualification_failure_code(str(error)),
            detail=str(error),
        )
    summary["backup_restore"] = {
        "verified": True,
        "configuration_hash": expectation.configuration_hash,
        "source_database_name": restore_evidence.source_database_name,
        "archive_sha256": restore_evidence.archive_sha256,
        "schema_head": restore_evidence.schema_head,
        "observed_at": _iso(window.generated_at),
    }
    evidence = {
        "capture_session_id": str(window.capture_session_id),
        "live_database_name": cast(str, live["database_name"]),
        "schema_head": expectation.schema_head,
        "retained_row_count": len(retained),
        "retained_rows_sha256": _sha256_json(retained),
        "restored_operations": cast(JsonValue, operations),
        "restore_operation": restore_evidence.operation_document(),
    }
    return {
        **summary,
        "evidence": cast(JsonValue, evidence),
    }


async def replay_ibkr_qualification_snapshot(
    snapshot: Mapping[str, object],
    live_store: QualificationEvidenceStore,
    restored_store: QualificationEvidenceStore,
    *,
    restore_evidence: VerifiedIbkrRestoreEvidence,
    expectation: IbkrQualificationExpectation,
    configuration: IbkrNativeCaptureConfiguration,
) -> dict[str, JsonValue]:
    """Independently rebuild and exactly compare one retained snapshot."""

    evidence = _object(snapshot.get("evidence"), "qualification evidence")
    window_doc = _object(snapshot.get("window"), "qualification window")
    window = IbkrQualificationWindow(
        capture_session_id=UUID(_string(evidence, "capture_session_id")),
        started_at=_datetime(window_doc, "started_at"),
        ended_at=_datetime(window_doc, "ended_at"),
        generated_at=_datetime(snapshot, "generated_at"),
    )
    rebuilt = await build_ibkr_qualification_snapshot(
        live_store,
        restored_store,
        restore_evidence=restore_evidence,
        expectation=expectation,
        configuration=configuration,
        window=window,
    )
    recorded_restore = _object(evidence.get("restore_operation"), "recorded restore operation")
    for field, expected in (
        ("contract", _RESTORE_EVIDENCE_CONTRACT),
        ("archive_sha256", restore_evidence.archive_sha256),
        ("source_database_name", restore_evidence.source_database_name),
        ("schema_head", restore_evidence.schema_head),
    ):
        if recorded_restore.get(field) != expected:
            raise ValueError(f"recorded restore operation {field} does not replay")
    rebuilt_evidence = cast(dict[str, JsonValue], rebuilt["evidence"])
    rebuilt_evidence["restore_operation"] = cast(JsonValue, dict(recorded_restore))
    unsigned = {
        key: cast(JsonValue, value) for key, value in snapshot.items() if key != "artifact_sha256"
    }
    if rebuilt != unsigned:
        raise ValueError("IBKR qualification snapshot does not replay from retained databases")
    return rebuilt


async def verify_ibkr_qualification_evidence(
    path: Path,
    live_store: QualificationEvidenceStore,
    restored_store: QualificationEvidenceStore,
    *,
    restore_evidence: VerifiedIbkrRestoreEvidence,
    expectation: IbkrQualificationExpectation,
    configuration: IbkrNativeCaptureConfiguration,
) -> VerifiedIbkrCaptureQualification:
    """Replay retained databases and mint the only production qualification authority."""

    if not _has_postgres_evidence_provenance(live_store) or not _has_postgres_evidence_provenance(
        restored_store
    ):
        raise TypeError("qualification authority requires exact PostgreSQL evidence stores")
    if not restore_evidence.authenticates(restored_store):
        raise TypeError("qualification authority requires verifier-authenticated restore evidence")

    document, artifact_sha256, generated_at = _verify_ibkr_qualification_summary(path, expectation)
    await replay_ibkr_qualification_snapshot(
        document,
        live_store,
        restored_store,
        restore_evidence=restore_evidence,
        expectation=expectation,
        configuration=configuration,
    )
    return VerifiedIbkrCaptureQualification._create(
        _VERIFIED_IBKR_CAPTURE_QUALIFICATION_TOKEN,
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


async def read_ibkr_qualification_evidence(
    store: QualificationEvidenceStore,
    *,
    configuration_hash: str,
    window: IbkrQualificationWindow,
    include_operations: bool,
) -> dict[str, JsonValue]:
    """Read one bounded session evidence page without changing capture state."""

    identity = await store.query(
        "SELECT current_database() AS database_name, "
        "(SELECT version_num FROM alembic_version) AS schema_head"
    )
    if len(identity) != 1:
        raise ValueError("qualification database identity is unavailable")
    parameters: dict[str, object] = {
        "capture_session_id": window.capture_session_id,
        "configuration_hash": configuration_hash,
        "started_at": window.started_at,
        "ended_at": window.ended_at,
    }
    rows = await store.query(
        """
        SELECT
            raw.id AS raw_record_id,
            raw.received_time,
            raw.payload_sha256,
            raw.connection_generation,
            raw.arrival_sequence,
            raw.payload,
            event.global_position,
            event.event_type,
            event.event_time,
            event.payload AS canonical_payload
        FROM raw.market_messages AS raw
        LEFT JOIN canonical.events AS event ON event.raw_record_id = raw.id
        WHERE raw.capture_session_id = :capture_session_id
          AND raw.configuration_hash = :configuration_hash
          AND raw.source_class = 'IBKR_NATIVE_CAPTURE'
          AND raw.received_time BETWEEN :started_at AND :ended_at
        ORDER BY raw.connection_generation, raw.arrival_sequence, event.global_position
        """,
        parameters,
    )
    retained_rows = [_json_value(row) for row in rows]
    result: dict[str, JsonValue] = {
        "database_name": str(identity[0]["database_name"]),
        "schema_head": str(identity[0]["schema_head"]),
        "retained_rows": retained_rows,
    }
    if include_operations:
        metrics = await store.query(
            """
            SELECT observed_at, records_received, persisted, failed, dropped
            FROM ops.capture_session_metrics
            WHERE capture_session_id = :capture_session_id
              AND configuration_hash = :configuration_hash
            """,
            parameters,
        )
        run = await store.query(
            """
            SELECT run_id, kind, status, environment, started_at, finished_at,
                   configuration_hash, detail
            FROM ops.runs
            WHERE run_id = :capture_session_id
              AND configuration_hash = :configuration_hash
            """,
            parameters,
        )
        if len(metrics) != 1 or len(run) != 1:
            raise ValueError("restored qualification operations evidence is incomplete")
        run_detail = _object(run[0].get("detail"), "qualification run detail")
        result["operations"] = {
            "metrics": _json_value(metrics[0]),
            "health": _json_value(
                _object(run_detail.get("qualification_health"), "qualification run health")
            ),
            "run": _json_value(run[0]),
        }
    return result


def compact_ibkr_qualification_evidence(
    evidence: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Replace retained rows with a deterministic count and digest for API transport."""

    retained = cast(list[JsonValue], evidence["retained_rows"])
    return {
        **{key: value for key, value in evidence.items() if key != "retained_rows"},
        "retained_row_count": len(retained),
        "retained_rows_sha256": _sha256_json(retained),
    }


def _derive_summary(
    retained: Sequence[JsonValue],
    operations: Mapping[str, JsonValue],
    *,
    expectation: IbkrQualificationExpectation,
    configuration: IbkrNativeCaptureConfiguration,
    window: IbkrQualificationWindow,
) -> dict[str, JsonValue]:
    rows = [_object(item, "retained market row") for item in retained]
    if not rows:
        raise ValueError("qualification requires retained market callbacks")
    generations = sorted({_integer(row, "connection_generation") for row in rows})
    if len(generations) != 2 or generations[1] != generations[0] + 1:
        raise ValueError("qualification requires one controlled generation transition")
    transition_at = min(
        _datetime(row, "received_time")
        for row in rows
        if _integer(row, "connection_generation") == generations[1]
    )

    expected_ids = {str(item) for item in expectation.instruments}
    contracts = {str(item.instrument_id): item for item in expectation.contracts}
    if set(contracts) != expected_ids:
        raise ValueError("qualification contract expectation is not exact")
    for instrument_id, contract in contracts.items():
        if configuration.activity(contract.listing_id, window.started_at) is not (
            IbkrMarketActivity.ACTIVE
        ) or configuration.activity(contract.listing_id, window.ended_at) is not (
            IbkrMarketActivity.ACTIVE
        ):
            raise ValueError(f"{instrument_id} qualification window is not authenticated ACTIVE")
    listing_to_instrument = {str(item.listing_id): raw for raw, item in contracts.items()}
    live_seen: set[tuple[int, str]] = set()
    request_ids: dict[tuple[int, str], set[int]] = defaultdict(set)
    side_times: dict[tuple[int, str, str], list[datetime]] = defaultdict(list)
    stale_callbacks = 0

    for row in rows:
        generation = _integer(row, "connection_generation")
        received_at = _datetime(row, "received_time")
        if generation == generations[0] and received_at >= transition_at:
            stale_callbacks += 1
        payload = _object(row.get("payload"), "retained callback payload")
        listing_id = _string(payload, "listing_id")
        instrument_id = listing_to_instrument.get(listing_id)
        if instrument_id is None:
            continue
        if _integer(payload, "con_id") != contracts[instrument_id].con_id:
            raise ValueError(f"{instrument_id} retained callback conId mismatch")
        request_ids[(generation, instrument_id)].add(_integer(payload, "request_id"))
        callback_type = _string(payload, "callback_type")
        if callback_type == "market_data_type":
            values = payload.get("callback_values")
            if isinstance(values, list) and values and values[0] == 1:
                live_seen.add((generation, instrument_id))
            continue
        if callback_type != "tick_price" or (generation, instrument_id) not in live_seen:
            continue
        tick_type = _integer(payload, "tick_type")
        side = "bid" if tick_type == 1 else "ask" if tick_type == 2 else None
        if side is None or row.get("event_type") != "MarketQuoteObserved":
            continue
        canonical = _object(row.get("canonical_payload"), "canonical quote payload")
        if _string(canonical, "quality") != "HEALTHY":
            continue
        if configuration.activity(contracts[instrument_id].listing_id, received_at) is not (
            IbkrMarketActivity.ACTIVE
        ):
            raise ValueError(f"{instrument_id} callback lacks authenticated ACTIVE evidence")
        side_times[(generation, instrument_id, side)].append(received_at)

    if stale_callbacks:
        raise ValueError(
            f"{stale_callbacks} stale generation callbacks were retained after reconnect transition"
        )
    if set(listing_to_instrument.values()) != expected_ids:
        raise ValueError("qualification configuration contract set is not exact")
    for generation in generations:
        for instrument_id in expected_ids:
            if (generation, instrument_id) not in live_seen:
                raise ValueError(f"{instrument_id} lacks LIVE evidence in generation {generation}")
            if len(request_ids[(generation, instrument_id)]) != 1:
                raise ValueError(f"{instrument_id} subscription identity is not exact")
            for side in ("bid", "ask"):
                if not side_times[(generation, instrument_id, side)]:
                    raise ValueError(
                        f"{instrument_id} lacks {side} evidence in generation {generation}"
                    )

    metrics = _object(operations.get("metrics"), "qualification metrics")
    health = _object(operations.get("health"), "qualification health")
    run = _object(operations.get("run"), "qualification run")
    received = _integer(metrics, "records_received")
    persisted = _integer(metrics, "persisted")
    failed = _integer(metrics, "failed")
    dropped = _integer(metrics, "dropped")
    if received != persisted or failed or dropped or persisted != len(rows):
        raise ValueError("qualification retained persistence does not reconcile exactly")
    if _string(health, "status") != "HEALTHY":
        raise ValueError("qualification retained health is not HEALTHY")
    if health.get("reason_codes") not in ([], ()):
        raise ValueError("qualification retained health has reason codes")
    attributes = _object(health.get("attributes"), "qualification health attributes")
    expected_health = {
        "capture_session_id": str(window.capture_session_id),
        "capture_source_id": expectation.capture_source_id,
        "universe_id": expectation.universe_id,
        "configuration_hash": expectation.configuration_hash,
        "source_class": "IBKR_NATIVE_CAPTURE",
        "desired_subscriptions": str(len(expected_ids)),
        "active_subscriptions": str(len(expected_ids)),
        "forced_reconnect_requested": "true",
        "forced_reconnect_completed": "true",
        "reconnect_from_generation": str(generations[0]),
        "reconnect_to_generation": str(generations[1]),
    }
    for field, expected in expected_health.items():
        if attributes.get(field) != expected:
            raise ValueError(f"qualification health {field} is not exact")
    health_at = _datetime(health, "observed_at")
    if not window.started_at <= health_at <= window.ended_at:
        raise ValueError("qualification health is not current for the retained window")
    if window.ended_at - health_at > expectation.freshness_threshold:
        raise ValueError("qualification retained health is stale")
    if _string(run, "kind") != "INGESTION" or _string(run, "environment") != "IBKR_PAPER":
        raise ValueError("qualification run identity is invalid")
    if _string(run, "status") != "STOPPED":
        raise ValueError("qualification retained run was not cleanly stopped before backup")
    finished_at = _datetime(run, "finished_at")
    if not window.ended_at <= finished_at <= window.generated_at:
        raise ValueError("qualification run completion chronology is invalid")

    instruments: list[JsonValue] = []
    for instrument_id in sorted(expected_ids):
        contract = contracts[instrument_id]
        bid = sorted(
            time
            for generation in generations
            for time in side_times[(generation, instrument_id, "bid")]
        )
        ask = sorted(
            time
            for generation in generations
            for time in side_times[(generation, instrument_id, "ask")]
        )
        terminal_bid = sorted(side_times[(generations[1], instrument_id, "bid")])
        terminal_ask = sorted(side_times[(generations[1], instrument_id, "ask")])
        recent = max(terminal_bid[-1], terminal_ask[-1])
        if (
            window.ended_at - min(terminal_bid[-1], terminal_ask[-1])
            > expectation.freshness_threshold
        ):
            raise ValueError(f"{instrument_id} post-reconnect quote evidence is stale")
        instruments.append(
            {
                "instrument_id": instrument_id,
                "listing_id": str(contract.listing_id),
                "con_id": contract.con_id,
                "market_activity": "ACTIVE",
                "market_data_type": "LIVE",
                "active_started_at": _iso(window.started_at),
                "active_ended_at": _iso(window.ended_at),
                "bid_count": len(bid),
                "ask_count": len(ask),
                "first_bid_at": _iso(bid[0]),
                "recent_bid_at": _iso(terminal_bid[-1]),
                "first_ask_at": _iso(ask[0]),
                "recent_ask_at": _iso(terminal_ask[-1]),
                "recent_evidence_at": _iso(recent),
            }
        )

    ids = sorted(expected_ids)
    return cast(
        dict[str, JsonValue],
        {
            "contract": IBKR_QUALIFICATION_CONTRACT,
            "stage": expectation.stage,
            "result": "QUALIFIED",
            "reason_codes": [],
            "release": {
                "contract": expectation.release_contract,
                "artifact_sha256": expectation.release_sha256,
                "configuration_hash": expectation.configuration_hash,
                "capture_source_id": expectation.capture_source_id,
                "universe_id": expectation.universe_id,
            },
            "runtime": {
                "application_commit": expectation.application_commit,
                "image_digest": expectation.image_digest,
                "api_package_sha256": expectation.api_package_sha256,
                "gateway_archive_sha256": expectation.gateway_archive_sha256,
                "gateway_version": expectation.gateway_version,
                "ibc_version": expectation.ibc_version,
                "database_name": expectation.database_name,
                "schema_head": expectation.schema_head,
            },
            "generated_at": _iso(window.generated_at),
            "window": {
                "started_at": _iso(window.started_at),
                "ended_at": _iso(window.ended_at),
            },
            "capture_session_ids": [str(window.capture_session_id)],
            "connection_generations": generations,
            "subscriptions": {"configured": ids, "desired": ids, "active": ids},
            "instruments": instruments,
            "persistence": {
                "records_received": received,
                "persisted": persisted,
                "failed": failed,
                "dropped": dropped,
                "reconciliation_loss": 0,
            },
            "health": {"status": "HEALTHY", "observed_at": _iso(health_at)},
            "reconnect": {
                "from_generation": generations[0],
                "to_generation": generations[1],
                "expected_subscriptions": ids,
                "reconstructed_subscriptions": ids,
                "fresh_instruments": ids,
                "duplicate_subscriptions": 0,
                "stale_generation_callbacks": stale_callbacks,
            },
            "backup_restore": {
                "verified": True,
                "configuration_hash": expectation.configuration_hash,
                "observed_at": _iso(window.generated_at),
            },
        },
    )


def _not_qualified_summary(
    *,
    expectation: IbkrQualificationExpectation,
    window: IbkrQualificationWindow,
    reason_code: str,
    detail: str,
) -> dict[str, JsonValue]:
    return {
        "contract": IBKR_QUALIFICATION_CONTRACT,
        "stage": expectation.stage,
        "result": "NOT_QUALIFIED",
        "reason_codes": [reason_code],
        "detail": detail,
        "release": {
            "contract": expectation.release_contract,
            "artifact_sha256": expectation.release_sha256,
            "configuration_hash": expectation.configuration_hash,
            "capture_source_id": expectation.capture_source_id,
            "universe_id": expectation.universe_id,
        },
        "runtime": {
            "application_commit": expectation.application_commit,
            "image_digest": expectation.image_digest,
            "api_package_sha256": expectation.api_package_sha256,
            "gateway_archive_sha256": expectation.gateway_archive_sha256,
            "gateway_version": expectation.gateway_version,
            "ibc_version": expectation.ibc_version,
            "database_name": expectation.database_name,
            "schema_head": expectation.schema_head,
        },
        "generated_at": _iso(window.generated_at),
        "window": {
            "started_at": _iso(window.started_at),
            "ended_at": _iso(window.ended_at),
        },
    }


def _qualification_failure_code(detail: str) -> str:
    if "authenticated ACTIVE" in detail:
        return "NO_AUTHENTICATED_ACTIVE_WINDOW"
    if "lacks LIVE evidence" in detail:
        return "LIVE_EVIDENCE_INCOMPLETE"
    if "lacks bid evidence" in detail or "lacks ask evidence" in detail:
        return "TOP_OF_BOOK_EVIDENCE_INCOMPLETE"
    if "persistence does not reconcile" in detail:
        return "PERSISTENCE_RECONCILIATION_FAILED"
    if "generation" in detail or "reconnect" in detail or "subscription" in detail:
        return "RECONNECT_EVIDENCE_INCOMPLETE"
    if "health" in detail:
        return "HEALTH_EVIDENCE_INCOMPLETE"
    return "QUALIFICATION_EVIDENCE_INCOMPLETE"


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise TypeError(f"qualification evidence value is not JSON-compatible: {type(value)!r}")


def _sha256_json(value: JsonValue) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: Mapping[str, object], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"qualification evidence {field} must be a string")
    return raw


def _integer(value: Mapping[str, object], field: str) -> int:
    raw = value.get(field)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"qualification evidence {field} must be an integer")
    return raw


def _datetime(value: Mapping[str, object], field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_string(value, field))
    except ValueError as error:
        raise ValueError(f"qualification evidence {field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"qualification evidence {field} must be UTC")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
