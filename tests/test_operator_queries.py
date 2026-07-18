from datetime import UTC, datetime
from typing import Any, cast

import pytest

from qtrad.adapters.postgres.queries import OperatorQueries
from qtrad.adapters.postgres.store import PostgresAuditStore


class ReadinessStore:
    def __init__(self, *, ingestion_running: bool = True, fresh_quote_count: int = 7) -> None:
        self.ingestion_running = ingestion_running
        self.fresh_quote_count = fresh_quote_count

    async def query(
        self, statement: str, parameters: dict[str, object] | None = None
    ) -> list[dict[str, Any]]:
        assert "adapter_name = 'ig-market-data'" in statement
        assert "configuration_hash = :configuration_hash" in statement
        assert parameters is not None
        assert parameters["configuration_hash"] == "a" * 64
        return [
            {
                "ingestion_running": self.ingestion_running,
                "adapter_healthy": True,
                "fresh_quote_count": self.fresh_quote_count,
                "global_position": 100,
                "checkpoint_position": 100,
                "checkpoint_updated_at": datetime(2026, 7, 13, tzinfo=UTC),
            }
        ]


@pytest.mark.asyncio
async def test_readiness_uses_the_persisted_ig_adapter_identity() -> None:
    queries = OperatorQueries(cast(PostgresAuditStore, ReadinessStore()))

    result = await queries.readiness(
        tuple(f"instrument-{index}" for index in range(7)),
        "a" * 64,
    )

    assert result["ready"] is True
    assert result["reasons"] == []
    assert result["configuration_hash"] == "a" * 64


@pytest.mark.asyncio
async def test_readiness_fails_when_the_running_ingestion_uses_another_configuration() -> None:
    queries = OperatorQueries(cast(PostgresAuditStore, ReadinessStore(ingestion_running=False)))

    result = await queries.readiness(("instrument-1",), "a" * 64)

    assert result["ready"] is False
    assert "matching ingestion configuration is not running" in result["reasons"]


@pytest.mark.asyncio
async def test_quote_recency_is_reported_without_failing_operational_readiness() -> None:
    queries = OperatorQueries(cast(PostgresAuditStore, ReadinessStore(fresh_quote_count=0)))

    result = await queries.readiness(("instrument-1",), "a" * 64)

    assert result["ready"] is True
    assert result["fresh_quote_count"] == 0
    assert result["reasons"] == []
