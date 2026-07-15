import hashlib
import json
from pathlib import Path

import pytest

from qtrad.runtime.research_snapshot import (
    load_research_snapshot_import,
    research_snapshot_metadata,
)


def _identity() -> dict[str, object]:
    return {
        "schema": "qtrad-research-snapshot-import-v1",
        "imported_at": "2026-07-14T10:00:00Z",
        "target_database": "qtrad_research_capture_20260714",
        "source_manifest_schema": "qtrad-capture-backup-v2",
        "source_manifest_file_sha256": "1" * 64,
        "source_manifest_identity_sha256": "2" * 64,
        "source_archive_sha256": "3" * 64,
        "source_created_at": "2026-07-14T00:00:00Z",
        "capture_source_id": "oci-sydney-capture-1",
        "universe_name": "capture-v1",
        "universe_hash": "4" * 64,
        "capture_image": "example.invalid/qtrad@sha256:" + "5" * 64,
        "postgres_image": "postgres@sha256:" + "6" * 64,
        "migration_version": "0006",
        "raw_message_count": 120,
        "canonical_event_count": 118,
    }


def _write_evidence(path: Path, *, tamper: bool = False) -> None:
    identity = _identity()
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    identity["import_sha256"] = digest
    if tamper:
        identity["raw_message_count"] = 121
    path.write_text(json.dumps(identity), encoding="utf-8")


def test_research_snapshot_import_is_verified_and_exposes_bounded_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "import.json"
    _write_evidence(path)

    evidence = load_research_snapshot_import(path)
    metadata = research_snapshot_metadata(evidence)

    assert metadata["kind"] == "verified-capture-snapshot"
    assert metadata["import_sha256"] == evidence.import_sha256
    assert metadata["capture_source_id"] == "oci-sydney-capture-1"
    assert metadata["source_archive_sha256"] == "3" * 64


def test_research_snapshot_import_fails_on_tampering_or_oversize(tmp_path: Path) -> None:
    tampered = tmp_path / "tampered.json"
    _write_evidence(tampered, tamper=True)
    with pytest.raises(ValueError, match="hash does not match"):
        load_research_snapshot_import(tampered)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="bounded size"):
        load_research_snapshot_import(oversized)
