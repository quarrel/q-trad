import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qtrad.adapters.parquet.store import ParquetResearchStore
from qtrad.application.replay import semantic_bar_hash
from tests.test_quota_replay import sample_bar


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 2, 12, tzinfo=UTC)


async def _write(store: ParquetResearchStore, *, configuration_hash: str = "a" * 64):
    return await store.write_bars(
        (sample_bar(),),
        universe_name="research-fixture",
        configuration_hash=configuration_hash,
        metadata={"gap_count": 0},
    )


@pytest.mark.asyncio
async def test_parquet_manifest_round_trip_is_content_addressed_and_non_overwriting(
    tmp_path: Path,
) -> None:
    store = ParquetResearchStore(tmp_path, FixedClock())
    bars = (sample_bar(),)

    manifest = await _write(store)
    repeated = await _write(store)
    another_configuration = await _write(store, configuration_hash="b" * 64)
    restored = tuple(await store.read_bars(manifest.manifest_id))

    assert manifest.schema_version == 2
    assert manifest.manifest_sha256 is not None
    assert manifest.manifest_id == manifest.manifest_sha256[:24]
    assert manifest.universe_name == "research-fixture"
    assert manifest.configuration_hash == "a" * 64
    assert all(path.startswith("bars-v2/") for path in manifest.files)
    assert set(manifest.file_sha256) == set(manifest.files)
    assert repeated == manifest
    assert another_configuration.manifest_id != manifest.manifest_id
    assert another_configuration.files == manifest.files
    assert manifest.row_count == 1
    assert semantic_bar_hash(restored) == semantic_bar_hash(bars)
    assert (tmp_path / "manifests" / f"{manifest.manifest_id}.json").exists()

    legacy_partition = tmp_path / manifest.files[0].replace("bars-v2/", "bars/", 1)
    legacy_partition.parent.mkdir(parents=True, exist_ok=True)
    legacy_partition.write_bytes(b"legacy rollback output cannot replace version-two files")
    assert semantic_bar_hash(
        tuple(await store.read_bars(manifest.manifest_id))
    ) == semantic_bar_hash(bars)

    next_day = replace(
        sample_bar(),
        interval_start=sample_bar().interval_start + timedelta(days=1),
        interval_end=sample_bar().interval_end + timedelta(days=1),
    )
    expanded = await store.write_bars(
        (sample_bar(), next_day),
        universe_name="research-fixture",
        configuration_hash="a" * 64,
        metadata={"gap_count": 0},
    )
    assert set(manifest.files) < set(expanded.files)


@pytest.mark.asyncio
async def test_manifest_and_parquet_tampering_fail_verification(tmp_path: Path) -> None:
    store = ParquetResearchStore(tmp_path, FixedClock())
    manifest = await _write(store)
    manifest_path = tmp_path / "manifests" / f"{manifest.manifest_id}.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    payload = json.loads(original_manifest)
    payload["metadata"]["gap_count"] = 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest hash"):
        await store.read_bars(manifest.manifest_id)

    manifest_path.write_text(original_manifest, encoding="utf-8")
    parquet_path = tmp_path / manifest.files[0]
    parquet_path.write_bytes(parquet_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="file hash mismatch"):
        await store.read_bars(manifest.manifest_id)


@pytest.mark.asyncio
async def test_legacy_manifest_remains_replayable_with_semantic_verification(
    tmp_path: Path,
) -> None:
    store = ParquetResearchStore(tmp_path, FixedClock())
    current = await _write(store)
    assert current.minimum_event_time is not None
    assert current.maximum_event_time is not None
    legacy_id = current.content_sha256[:24]
    legacy_files = tuple(path.replace("bars-v2/", "bars/", 1) for path in current.files)
    for current_file, legacy_file in zip(current.files, legacy_files, strict=True):
        destination = tmp_path / legacy_file
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((tmp_path / current_file).read_bytes())
    legacy = {
        "manifest_id": legacy_id,
        "created_at": current.created_at.isoformat(),
        "schema_version": 1,
        "row_count": current.row_count,
        "minimum_event_time": current.minimum_event_time.isoformat(),
        "maximum_event_time": current.maximum_event_time.isoformat(),
        "content_sha256": current.content_sha256,
        "configuration_hash": current.configuration_hash,
        "files": list(legacy_files),
        "metadata": {"gap_count": 0},
    }
    manifest_path = tmp_path / "manifests" / f"{legacy_id}.json"
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = await store.read_manifest(legacy_id)
    restored = tuple(await store.read_bars(legacy_id))

    assert loaded.schema_version == 1
    assert loaded.manifest_sha256 is None
    assert loaded.universe_name is None
    assert semantic_bar_hash(restored) == semantic_bar_hash((sample_bar(),))


@pytest.mark.asyncio
async def test_manifest_paths_cannot_escape_the_research_store(tmp_path: Path) -> None:
    store = ParquetResearchStore(tmp_path, FixedClock())
    current = await _write(store)
    assert current.minimum_event_time is not None
    assert current.maximum_event_time is not None
    legacy_id = current.content_sha256[:24]
    unsafe = {
        "manifest_id": legacy_id,
        "created_at": current.created_at.isoformat(),
        "schema_version": 1,
        "row_count": current.row_count,
        "minimum_event_time": current.minimum_event_time.isoformat(),
        "maximum_event_time": current.maximum_event_time.isoformat(),
        "content_sha256": current.content_sha256,
        "configuration_hash": current.configuration_hash,
        "files": ["../outside.parquet"],
        "metadata": {},
    }
    (tmp_path / "manifests" / f"{legacy_id}.json").write_text(json.dumps(unsafe), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe research file path"):
        await store.read_bars(legacy_id)
