from datetime import UTC, datetime

import pytest

from qtrad.adapters.parquet.store import ParquetResearchStore
from qtrad.application.replay import semantic_bar_hash
from tests.test_quota_replay import sample_bar


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 2, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_parquet_manifest_round_trip(tmp_path) -> None:
    store = ParquetResearchStore(tmp_path, FixedClock())
    bars = (sample_bar(),)
    manifest = await store.write_bars(
        bars,
        configuration_hash="config-hash",
        metadata={"gap_count": 0},
    )
    restored = tuple(await store.read_bars(manifest.manifest_id))
    assert manifest.row_count == 1
    assert semantic_bar_hash(restored) == semantic_bar_hash(bars)
    assert (tmp_path / "manifests" / f"{manifest.manifest_id}.json").exists()
