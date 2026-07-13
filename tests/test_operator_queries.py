from datetime import UTC, datetime
from typing import Any, cast

import pytest

from qtrad.adapters.postgres.queries import OperatorQueries
from qtrad.adapters.postgres.store import PostgresAuditStore


class ReadinessStore:
    async def query(
        self, statement: str, parameters: dict[str, object] | None = None
    ) -> list[dict[str, Any]]:
        assert "adapter_name = 'ig-market-data'" in statement
        assert parameters is not None
        return [
            {
                "ingestion_running": True,
                "adapter_healthy": True,
                "fresh_quote_count": 7,
                "global_position": 100,
                "checkpoint_position": 100,
                "checkpoint_updated_at": datetime(2026, 7, 13, tzinfo=UTC),
            }
        ]


@pytest.mark.asyncio
async def test_readiness_uses_the_persisted_ig_adapter_identity() -> None:
    queries = OperatorQueries(cast(PostgresAuditStore, ReadinessStore()))

    result = await queries.readiness(tuple(f"instrument-{index}" for index in range(7)))

    assert result["ready"] is True
    assert result["reasons"] == []
