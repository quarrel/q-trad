"""Verified provenance for an isolated collector-snapshot import."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from qtrad.domain.events import JsonValue

_MAX_IMPORT_EVIDENCE_BYTES = 64 * 1024


class ResearchSnapshotImport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)

    contract: Literal["qtrad-research-snapshot-import-v1"] = Field(alias="schema")
    imported_at: datetime
    target_database: str = Field(pattern=r"^qtrad_research_[a-z0-9_]{1,40}$")
    source_manifest_schema: Literal["qtrad-capture-backup-v1", "qtrad-capture-backup-v2"]
    source_manifest_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_created_at: datetime
    capture_source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    universe_name: str = Field(pattern=r"^(unknown-v1|[a-z0-9][a-z0-9._-]{0,63})$")
    universe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_image: str = Field(pattern=r"^\S+@sha256:[0-9a-f]{64}$", max_length=500)
    postgres_image: str = Field(pattern=r"^\S+@sha256:[0-9a-f]{64}$", max_length=500)
    migration_version: str = Field(pattern=r"^[0-9a-f]{4,32}$")
    raw_message_count: int = Field(gt=0)
    canonical_event_count: int = Field(ge=0)
    import_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def load_research_snapshot_import(path: Path) -> ResearchSnapshotImport:
    encoded = path.read_bytes()
    if len(encoded) > _MAX_IMPORT_EVIDENCE_BYTES:
        raise ValueError("research snapshot import evidence exceeds its bounded size")
    evidence = ResearchSnapshotImport.model_validate_json(encoded)
    _validate_import(evidence)
    return evidence


def research_snapshot_metadata(evidence: ResearchSnapshotImport) -> dict[str, JsonValue]:
    _validate_import(evidence)
    return {
        "kind": "verified-capture-snapshot",
        "import_sha256": evidence.import_sha256,
        "capture_source_id": evidence.capture_source_id,
        "source_created_at": _utc_text(evidence.source_created_at),
        "source_archive_sha256": evidence.source_archive_sha256,
        "source_manifest_schema": evidence.source_manifest_schema,
        "source_manifest_identity_sha256": evidence.source_manifest_identity_sha256,
        "source_migration_version": evidence.migration_version,
        "source_capture_image": evidence.capture_image,
        "source_postgres_image": evidence.postgres_image,
    }


def _validate_import(evidence: ResearchSnapshotImport) -> None:
    _utc_text(evidence.imported_at)
    _utc_text(evidence.source_created_at)
    calculated = _sha256_json(_import_identity(evidence))
    if calculated != evidence.import_sha256:
        raise ValueError("research snapshot import hash does not match its canonical content")


def _import_identity(evidence: ResearchSnapshotImport) -> dict[str, JsonValue]:
    return {
        "schema": evidence.contract,
        "imported_at": _utc_text(evidence.imported_at),
        "target_database": evidence.target_database,
        "source_manifest_schema": evidence.source_manifest_schema,
        "source_manifest_file_sha256": evidence.source_manifest_file_sha256,
        "source_manifest_identity_sha256": evidence.source_manifest_identity_sha256,
        "source_archive_sha256": evidence.source_archive_sha256,
        "source_created_at": _utc_text(evidence.source_created_at),
        "capture_source_id": evidence.capture_source_id,
        "universe_name": evidence.universe_name,
        "universe_hash": evidence.universe_hash,
        "capture_image": evidence.capture_image,
        "postgres_image": evidence.postgres_image,
        "migration_version": evidence.migration_version,
        "raw_message_count": evidence.raw_message_count,
        "canonical_event_count": evidence.canonical_event_count,
    }


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("research snapshot evidence time must use UTC")
    return value.isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
