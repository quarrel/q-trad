import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest

from qtrad import __main__ as cli
from qtrad.adapters.parquet.store import ParquetResearchStore
from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import RunId
from qtrad.ports.storage import ResearchManifest
from qtrad.runtime.settings import Settings
from qtrad.runtime.universe import load_capture_universe
from tests.test_quota_replay import sample_bar

NOW = datetime(2026, 7, 14, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeAuditStore:
    def __init__(self, *, query_rows: tuple[list[dict[str, object]], ...] = ()) -> None:
        self.query_rows = list(query_rows)
        self.query_parameters: list[Mapping[str, object]] = []
        self.started: dict[str, object] | None = None
        self.finished: dict[str, object] | None = None
        self.manifest: ResearchManifest | None = None

    async def start_run(self, **kwargs: object) -> RunId:
        self.started = kwargs
        return RunId(UUID(int=1))

    async def query(
        self, _: str, parameters: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        if not self.query_rows:
            raise AssertionError("unexpected export query")
        if parameters is None:
            raise AssertionError("export query must be explicitly bounded")
        self.query_parameters.append(parameters)
        return self.query_rows.pop(0)

    async def record_manifest(self, manifest: ResearchManifest) -> None:
        self.manifest = manifest

    async def finish_run(self, _: RunId, **kwargs: object) -> None:
        self.finished = kwargs


def _projection_row() -> dict[str, object]:
    bar = sample_bar()
    return {
        "instrument_id": str(bar.instrument_id),
        "basis": bar.basis.value,
        "interval_start": bar.interval_start,
        "interval_end": bar.interval_end,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "sample_count": bar.sample_count,
        "revision": bar.revision,
        "provenance": bar.provenance.value,
        "quality": bar.quality.value,
        "source_provider": bar.source_listing_id.provider,
        "source_environment": bar.source_listing_id.environment,
        "source_external_id": bar.source_listing_id.external_id,
    }


def _live_gap_row() -> dict[str, object]:
    return {
        "gap_id": UUID(int=2),
        "instrument_id": "index:us-500",
        "interval_start": NOW,
        "interval_end": NOW + timedelta(minutes=1),
        "reason": "fixture",
        "detected_at": NOW + timedelta(minutes=2),
        "repaired_at": None,
    }


def _historical_coverage_row() -> dict[str, object]:
    return {
        "instrument_id": "index:us-500",
        "source_provider": "ig",
        "source_environment": "demo",
        "source_external_id": "FIXTURE",
        "source_listing_valid_from": NOW,
        "source_listing_metadata_version": "fixture-v1",
        "provenance": "IG_HISTORICAL",
        "basis": "MID",
        "resolution": "MINUTE",
        "interval_start": NOW,
        "interval_end": NOW + timedelta(hours=1),
        "detected_at": NOW + timedelta(hours=2),
        "detected_by_plan_hash": "b" * 64,
        "covered_at": None,
        "covered_by_plan_hash": None,
        "observed_points": None,
    }


@pytest.mark.asyncio
async def test_export_binds_selected_universe_and_real_gap_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    universe_path = Path("config/capture-v1.toml")
    universe = load_capture_universe(universe_path)
    audit = FakeAuditStore(
        query_rows=([_projection_row()], [_live_gap_row()], [_historical_coverage_row()])
    )
    engine = FakeEngine()
    monkeypatch.setattr(cli, "_engine", lambda _: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda _: audit)
    settings = Settings(
        database_url="postgresql+asyncpg://qtrad@db/qtrad_research_capture_20260714",
        research_root=tmp_path,
        capture_source_id="oci-sydney-capture-1",
        image="syd.ocir.io/example/qtrad@sha256:" + "c" * 64,
    )
    snapshot_identity = {
        "schema": "qtrad-research-snapshot-import-v1",
        "imported_at": "2026-07-14T10:00:00Z",
        "target_database": "qtrad_research_capture_20260714",
        "source_manifest_schema": "qtrad-capture-backup-v2",
        "source_manifest_file_sha256": "1" * 64,
        "source_manifest_identity_sha256": "2" * 64,
        "source_archive_sha256": "3" * 64,
        "source_created_at": "2026-07-14T00:00:00Z",
        "capture_source_id": "oci-sydney-capture-1",
        "universe_name": universe.name,
        "universe_hash": universe.configuration_hash,
        "capture_image": "example.invalid/qtrad@sha256:" + "4" * 64,
        "postgres_image": "postgres@sha256:" + "5" * 64,
        "migration_version": "0006",
        "raw_message_count": 120,
        "canonical_event_count": 118,
    }
    snapshot_identity["import_sha256"] = hashlib.sha256(
        json.dumps(snapshot_identity, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    snapshot_evidence = tmp_path / "snapshot-import.json"
    snapshot_evidence.write_text(json.dumps(snapshot_identity), encoding="utf-8")

    await cli._export(
        settings,
        FixedClock(),
        universe_path=universe_path,
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 7, 15, tzinfo=UTC),
        snapshot_import_path=snapshot_evidence,
    )

    assert audit.started is not None
    assert audit.started["configuration_hash"] == universe.configuration_hash
    assert audit.manifest is not None
    assert audit.manifest.universe_name == universe.name
    assert audit.manifest.configuration_hash == universe.configuration_hash
    metadata = audit.manifest.metadata
    assert metadata["universe"] == {
        "name": universe.name,
        "configuration_hash": universe.configuration_hash,
        "instrument_ids": sorted(
            str(instrument.instrument_id) for instrument in universe.instruments
        ),
    }
    assert metadata["requested_interval"] == {
        "start": "2026-07-01T00:00:00Z",
        "end": "2026-07-15T00:00:00Z",
    }
    assert metadata["source_snapshot"] == {
        "kind": "verified-capture-snapshot",
        "import_sha256": snapshot_identity["import_sha256"],
        "capture_source_id": "oci-sydney-capture-1",
        "source_created_at": "2026-07-14T00:00:00Z",
        "source_archive_sha256": "3" * 64,
        "source_manifest_schema": "qtrad-capture-backup-v2",
        "source_manifest_identity_sha256": "2" * 64,
        "source_migration_version": "0006",
        "source_capture_image": "example.invalid/qtrad@sha256:" + "4" * 64,
        "source_postgres_image": "postgres@sha256:" + "5" * 64,
    }
    assert len(audit.query_parameters) == 3
    assert all(
        parameters["interval_start"] == datetime(2026, 7, 1, tzinfo=UTC)
        and parameters["interval_end"] == datetime(2026, 7, 15, tzinfo=UTC)
        for parameters in audit.query_parameters
    )
    assert _mapping(metadata["live_gaps"])["count"] == 1
    assert _mapping(metadata["historical_coverage"])["open_count"] == 1
    assert audit.finished is not None
    assert audit.finished["status"] == "COMPLETED"
    assert engine.disposed is True

    wrong_database = Settings(
        database_url="postgresql+asyncpg://qtrad@db/qtrad_research_other",
        research_root=tmp_path,
        capture_source_id="oci-sydney-capture-1",
    )
    with pytest.raises(ValueError, match="configured database"):
        await cli._export(
            wrong_database,
            FixedClock(),
            universe_path=universe_path,
            start=datetime(2026, 7, 1, tzinfo=UTC),
            end=datetime(2026, 7, 15, tzinfo=UTC),
            snapshot_import_path=snapshot_evidence,
        )


@pytest.mark.asyncio
async def test_replay_uses_verified_manifest_identity_not_current_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    research = ParquetResearchStore(tmp_path, FixedClock())
    manifest = await research.write_bars(
        (sample_bar(),),
        universe_name="historic-universe",
        configuration_hash="f" * 64,
        metadata={},
    )
    manifest_path = tmp_path / "manifests" / f"{manifest.manifest_id}.json"
    audit = FakeAuditStore()
    engine = FakeEngine()
    monkeypatch.setattr(cli, "_engine", lambda _: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda _: audit)

    await cli._replay(Settings(), FixedClock(), manifest_path)

    assert audit.started is not None
    assert audit.started["configuration_hash"] == manifest.configuration_hash
    assert audit.finished is not None
    assert audit.finished["status"] == "COMPLETED"
    assert audit.finished["detail"] == {
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "universe_name": manifest.universe_name,
    }
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_replay_rejects_ambiguous_manifest_paths_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Mock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr(cli, "_engine", database)

    with pytest.raises(ValueError, match="inside a manifests directory"):
        await cli._replay(Settings(), FixedClock(), Path("arbitrary.json"))
    database.assert_not_called()


def _mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise TypeError("expected metadata mapping")
    return value
