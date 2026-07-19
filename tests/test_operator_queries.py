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


class SystemStore:
    def __init__(self, *, observation_age_seconds: float = 1.2) -> None:
        self.observation_age_seconds = observation_age_seconds

    async def query(
        self, statement: str, parameters: dict[str, object] | None = None
    ) -> list[dict[str, Any]]:
        assert parameters is None
        if "raw.market_messages" in statement:
            return [
                {
                    "raw_messages": 10,
                    "canonical_events": 9,
                    "current_quotes": 7,
                    "bars": 3,
                    "global_position": 9,
                    "observed_at": datetime(2026, 7, 19, 6, tzinfo=UTC),
                }
            ]
        if "ops.adapter_health" in statement:
            return [
                {
                    "adapter_name": "ig-market-data",
                    "environment": "IG_DEMO",
                    "status": "HEALTHY",
                    "observed_at": datetime(2026, 7, 19, 6, tzinfo=UTC),
                    "last_message_at": None,
                    "observation_age_seconds": self.observation_age_seconds,
                    "detail": (
                        "data only; heartbeat_events=42; "
                        "last_heartbeat_at=2026-07-19T06:00:00+00:00; "
                        "heartbeat_stale=false; heartbeat_transport_current=true"
                    ),
                }
            ]
        if "ops.projection_checkpoints" in statement or "ops.quota_state" in statement:
            return []
        raise AssertionError(f"unexpected system query: {statement}")


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


@pytest.mark.asyncio
async def test_system_reports_current_heartbeat_evidence() -> None:
    queries = OperatorQueries(cast(PostgresAuditStore, SystemStore()))

    result = await queries.system()

    assert result["observed_at"] == datetime(2026, 7, 19, 6, tzinfo=UTC)
    assert result["heartbeat"] == {
        "status": "HEALTHY",
        "adapter_status": "HEALTHY",
        "events": 42,
        "last_heartbeat_at": "2026-07-19T06:00:00+00:00",
        "observed_at": datetime(2026, 7, 19, 6, tzinfo=UTC),
        "observation_age_seconds": 1.2,
        "transport_current": True,
    }


@pytest.mark.asyncio
async def test_system_rejects_a_static_heartbeat_health_sample() -> None:
    queries = OperatorQueries(cast(PostgresAuditStore, SystemStore(observation_age_seconds=10.1)))

    result = await queries.system()

    assert result["heartbeat"]["status"] == "UNHEALTHY"
