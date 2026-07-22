"""PostgreSQL read-model queries for the operator API."""

import json
from typing import Any

from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.ports.storage import EventPage

_HEARTBEAT_OBSERVATION_MAX_AGE_SECONDS = 10.0


def _boolean_health_field(fields: dict[str, str], name: str) -> bool | None:
    value = fields.get(name)
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"invalid boolean adapter-health field {name}: {value!r}")


def _heartbeat_summary(health_rows: list[dict[str, Any]]) -> dict[str, Any]:
    row = next((item for item in health_rows if item["adapter_name"] == "ig-market-data"), None)
    if row is None:
        return {
            "status": "UNAVAILABLE",
            "adapter_status": None,
            "events": None,
            "last_heartbeat_at": None,
            "observed_at": None,
            "observation_age_seconds": None,
            "transport_current": None,
        }
    detail = row["detail"]
    if detail is None:
        fields: dict[str, str] = {}
    elif isinstance(detail, str):
        fields = {}
        for segment in detail.split(";"):
            key, separator, value = segment.strip().partition("=")
            if separator:
                fields[key] = value
    else:
        raise TypeError("IG adapter-health detail must be text or null")
    events_text = fields.get("heartbeat_events")
    events = int(events_text) if events_text is not None else None
    last_heartbeat_text = fields.get("last_heartbeat_at")
    last_heartbeat_at = None if last_heartbeat_text in {None, "none"} else last_heartbeat_text
    transport_current = _boolean_health_field(fields, "heartbeat_transport_current")
    heartbeat_stale = _boolean_health_field(fields, "heartbeat_stale")
    observation_age_seconds = float(row["observation_age_seconds"])
    has_current_evidence = (
        row["status"] == "HEALTHY"
        and transport_current is True
        and heartbeat_stale is False
        and events is not None
        and events > 0
        and last_heartbeat_at is not None
        and 0 <= observation_age_seconds <= _HEARTBEAT_OBSERVATION_MAX_AGE_SECONDS
    )
    return {
        "status": "HEALTHY" if has_current_evidence else "UNHEALTHY",
        "adapter_status": row["status"],
        "events": events,
        "last_heartbeat_at": last_heartbeat_at,
        "observed_at": row["observed_at"],
        "observation_age_seconds": round(observation_age_seconds, 1),
        "transport_current": transport_current,
    }


class OperatorQueries:
    def __init__(self, store: PostgresAuditStore) -> None:
        self._store = store

    async def system(self) -> dict[str, Any]:
        counts = await self._store.query(
            """
            SELECT
                (SELECT COUNT(*) FROM raw.market_messages) AS raw_messages,
                (SELECT COUNT(*) FROM canonical.events) AS canonical_events,
                (SELECT COUNT(*) FROM read_model.latest_quotes) AS current_quotes,
                (SELECT COUNT(*) FROM read_model.market_bars) AS bars,
                (SELECT MAX(global_position) FROM canonical.events) AS global_position,
                clock_timestamp() AS observed_at
            """
        )
        health = await self._store.query(
            """
            SELECT *, EXTRACT(EPOCH FROM clock_timestamp() - observed_at) AS observation_age_seconds
            FROM ops.adapter_health ORDER BY adapter_name
            """
        )
        checkpoints = await self._store.query(
            "SELECT * FROM ops.projection_checkpoints ORDER BY projection_name"
        )
        quotas = await self._store.query(
            "SELECT * FROM ops.quota_state ORDER BY provider, allowance_name"
        )
        count_row = counts[0]
        return {
            "observed_at": count_row["observed_at"],
            "counts": {key: value for key, value in count_row.items() if key != "observed_at"},
            "adapter_health": health,
            "heartbeat": _heartbeat_summary(health),
            "projection_checkpoints": checkpoints,
            "quotas": quotas,
        }

    async def readiness(
        self,
        expected_instrument_ids: tuple[str, ...],
        expected_configuration_hash: str,
    ) -> dict[str, Any]:
        """Return collector readiness rather than API/database liveness."""

        if not expected_instrument_ids:
            raise ValueError("collector readiness requires expected instruments")
        if len(expected_configuration_hash) != 64 or any(
            character not in "0123456789abcdef" for character in expected_configuration_hash
        ):
            raise ValueError("collector readiness requires a lower-case SHA-256 configuration hash")
        rows = await self._store.query(
            """
            SELECT
                EXISTS (
                      SELECT 1 FROM ops.runs
                      WHERE kind = 'INGESTION' AND status = 'RUNNING'
                        AND configuration_hash = :configuration_hash
                ) AS ingestion_running,
                EXISTS (
                    SELECT 1 FROM ops.adapter_health
                    WHERE adapter_name = 'ig-market-data' AND environment = 'IG_DEMO'
                      AND status = 'HEALTHY'
                ) AS adapter_healthy,
                (
                    SELECT COUNT(*) FROM read_model.latest_quotes
                    WHERE instrument_id IN (
                        SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
                    )
                    AND received_time >= clock_timestamp() - INTERVAL '5 minutes'
                ) AS fresh_quote_count,
                COALESCE((SELECT MAX(global_position) FROM canonical.events), 0) AS global_position,
                COALESCE((
                    SELECT global_position FROM ops.projection_checkpoints
                    WHERE projection_name = 'core'
                ), 0) AS checkpoint_position,
                (
                    SELECT updated_at FROM ops.projection_checkpoints
                    WHERE projection_name = 'core'
                ) AS checkpoint_updated_at
            """,
            {
                "configuration_hash": expected_configuration_hash,
                "instrument_ids": json.dumps(expected_instrument_ids),
            },
        )
        row = rows[0]
        reasons: list[str] = []
        if not row["ingestion_running"]:
            reasons.append("matching ingestion configuration is not running")
        if not row["adapter_healthy"]:
            reasons.append("IG adapter is not healthy")
        if int(row["global_position"]) - int(row["checkpoint_position"]) > 100:
            reasons.append("projection is more than 100 events behind")
        checkpoint_updated_at = row["checkpoint_updated_at"]
        if checkpoint_updated_at is None:
            reasons.append("projection checkpoint is unknown")
        return {
            "ready": not reasons,
            "reasons": reasons,
            "expected_instruments": len(expected_instrument_ids),
            "fresh_quote_count": int(row["fresh_quote_count"]),
            "global_position": int(row["global_position"]),
            "checkpoint_position": int(row["checkpoint_position"]),
            "checkpoint_updated_at": checkpoint_updated_at,
            "configuration_hash": expected_configuration_hash,
        }

    async def instruments(self) -> list[dict[str, Any]]:
        return await self._store.query(
            """
            SELECT i.*, q.provider, q.environment, q.external_id,
                   q.event_time AS quote_event_time, q.received_time,
                   q.bid, q.ask,
                   CASE
                       WHEN q.received_time < clock_timestamp() - INTERVAL '30 seconds'
                       THEN 'STALE'
                       ELSE q.quality
                   END AS quality
            FROM reference.instruments i
            LEFT JOIN read_model.latest_quotes q USING (instrument_id)
            ORDER BY i.instrument_id
            """
        )

    async def instrument(self, instrument_id: str) -> dict[str, Any] | None:
        instruments = await self._store.query(
            """
            SELECT i.*, q.provider, q.environment, q.external_id,
                   q.event_time AS quote_event_time, q.received_time,
                   q.bid, q.ask, q.bid_size, q.ask_size,
                   CASE
                       WHEN q.received_time < clock_timestamp() - INTERVAL '30 seconds'
                       THEN 'STALE'
                       ELSE q.quality
                   END AS quality
            FROM reference.instruments i
            LEFT JOIN read_model.latest_quotes q USING (instrument_id)
            WHERE i.instrument_id = :instrument_id
            """,
            {"instrument_id": instrument_id},
        )
        if not instruments:
            return None
        bars = await self.bars(instrument_id=instrument_id, limit=300)
        listings = await self._store.query(
            """
            SELECT * FROM reference.provider_listings
            WHERE instrument_id = :instrument_id
            ORDER BY valid_from DESC
            """,
            {"instrument_id": instrument_id},
        )
        return {"instrument": instruments[0], "bars": bars, "listings": listings}

    async def bars(
        self, *, instrument_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        return await self._store.query(
            """
                SELECT DISTINCT ON (
                    instrument_id, basis, interval_start, provenance,
                    source_provider, source_environment, source_external_id
                )
                    instrument_id, basis, interval_start, interval_end,
                    open, high, low, close, sample_count, revision,
                    provenance, quality, source_provider, source_environment,
                    source_external_id, global_position
                FROM read_model.market_bars
                  WHERE (
                      CAST(:instrument_id AS TEXT) IS NULL
                      OR instrument_id = CAST(:instrument_id AS TEXT)
                  )
                ORDER BY instrument_id, basis, interval_start DESC, provenance,
                         source_provider, source_environment, source_external_id,
                         revision DESC
            LIMIT :limit
            """,
            {"instrument_id": instrument_id, "limit": limit},
        )

    async def gaps(self) -> list[dict[str, Any]]:
        return await self._store.query(
            "SELECT * FROM read_model.data_gaps ORDER BY detected_at DESC"
        )

    async def historical_coverage(
        self,
        *,
        instrument_id: str | None = None,
        only_open: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return plan-scoped historical coverage without conflating live-stream gaps."""

        return await self._store.query(
            """
            SELECT instrument_id, source_provider, source_environment, source_external_id,
                   source_listing_valid_from, source_listing_metadata_version,
                   provenance, basis, resolution, interval_start, interval_end,
                   detected_at, detected_by_plan_hash, covered_at,
                   covered_by_plan_hash, observed_points
            FROM read_model.historical_coverage_gaps
            WHERE (
                CAST(:instrument_id AS text) IS NULL
                OR instrument_id = CAST(:instrument_id AS text)
            )
              AND (NOT :only_open OR covered_at IS NULL)
            ORDER BY detected_at DESC, instrument_id, basis
            LIMIT :limit
            """,
            {
                "instrument_id": instrument_id,
                "only_open": only_open,
                "limit": limit,
            },
        )

    async def runs(self) -> list[dict[str, Any]]:
        return await self._store.query("SELECT * FROM ops.runs ORDER BY started_at DESC LIMIT 100")

    async def manifests(self) -> list[dict[str, Any]]:
        return await self._store.query(
            "SELECT * FROM ops.research_manifests ORDER BY created_at DESC"
        )

    async def event_page(self, *, after_position: int, limit: int) -> EventPage:
        return await self._store.read_page(after_position=after_position, limit=limit)
