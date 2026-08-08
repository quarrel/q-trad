"""PostgreSQL read-model queries for the operator API."""

import json
from typing import Any

from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.ports.storage import EventPage

# The runtime persists health every second; ten seconds allows scheduler jitter while
# ensuring closed-market exemptions cannot hide a dead collector.
_HEARTBEAT_OBSERVATION_MAX_AGE_SECONDS = 10.0
_ADAPTER_HEALTH_OBSERVATION_MAX_AGE_SECONDS = 10.0


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
        observed = await self._store.query("SELECT clock_timestamp() AS observed_at")
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
        return {
            "observed_at": observed[0]["observed_at"],
            "adapter_health": health,
            "heartbeat": _heartbeat_summary(health),
            "projection_checkpoints": checkpoints,
            "quotas": quotas,
        }

    async def readiness(
        self,
        expected_instrument_ids: tuple[str, ...],
        expected_configuration_hash: str,
        *,
        provider: str = "ig",
        environment: str = "IG_DEMO",
        adapter_name: str = "ig-market-data",
        freshness_seconds: float = 300.0,
        source_class: str = "IG_NATIVE_CAPTURE",
        expected_active_instrument_ids: tuple[str, ...] | None = None,
        health_observation_max_age_seconds: float = _ADAPTER_HEALTH_OBSERVATION_MAX_AGE_SECONDS,
    ) -> dict[str, Any]:
        """Return collector readiness rather than API/database liveness."""

        if not expected_instrument_ids:
            raise ValueError("collector readiness requires expected instruments")
        active_instrument_ids = (
            expected_instrument_ids
            if expected_active_instrument_ids is None
            else expected_active_instrument_ids
        )
        if any(item not in expected_instrument_ids for item in active_instrument_ids):
            raise ValueError("active readiness instruments must be configured instruments")
        if len(expected_configuration_hash) != 64 or any(
            character not in "0123456789abcdef" for character in expected_configuration_hash
        ):
            raise ValueError("collector readiness requires a lower-case SHA-256 configuration hash")
        if freshness_seconds <= 0:
            raise ValueError("collector readiness freshness must be positive")
        if health_observation_max_age_seconds <= 0:
            raise ValueError("collector readiness health observation age must be positive")
        if any(
            not value or "'" in value
            for value in (provider, environment, adapter_name, source_class)
        ):
            raise ValueError("collector readiness source identity is invalid")
        if source_class == "IBKR_NATIVE_CAPTURE":
            fresh_quote_sql = """
                    SELECT COUNT(*) FROM read_model.capture_latest_quotes
                    WHERE source_class = :source_class
                      AND provider = :provider
                      AND environment = :environment
                      AND configuration_hash = :configuration_hash
                      AND instrument_id IN (
                          SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
                      )
                      AND received_time >= clock_timestamp() - (
                          :freshness_seconds * INTERVAL '1 second'
                      )
            """
        else:
            fresh_quote_sql = """
                    SELECT COUNT(*) FROM read_model.latest_quotes
                    WHERE instrument_id IN (
                        SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
                    )
                    AND received_time >= clock_timestamp() - (
                        :freshness_seconds * INTERVAL '1 second'
                    )
            """
        rows = await self._store.query(
            """
            SELECT
                EXISTS (
                      SELECT 1 FROM ops.runs
                      WHERE kind = 'INGESTION' AND status = 'RUNNING'
                        AND environment = :environment
                        AND configuration_hash = :configuration_hash
                ) AS ingestion_running,
                EXISTS (
                    SELECT 1 FROM ops.adapter_health
                    WHERE adapter_name = '"""
            + adapter_name
            + "' AND environment = '"
            + environment
            + "' AND status = 'HEALTHY'"
            + "\n                      AND observed_at >= clock_timestamp() - "
            + "(:health_observation_max_age_seconds * INTERVAL '1 second')"
            + "\n                      AND (:source_class = 'IG_NATIVE_CAPTURE' OR "
            + "attributes->>'source_class' = :source_class)"
            + "\n                      AND (:source_class <> 'IBKR_NATIVE_CAPTURE' OR "
            + "attributes->>'configuration_hash' = :configuration_hash)"
            + """
                ) AS adapter_healthy,
                ("""
            + fresh_quote_sql
            + """) AS fresh_quote_count,
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
                "instrument_ids": json.dumps(active_instrument_ids),
                "freshness_seconds": freshness_seconds,
                "health_observation_max_age_seconds": health_observation_max_age_seconds,
                "environment": environment,
                "provider": provider,
                "source_class": source_class,
            },
        )
        row = rows[0]
        reasons: list[str] = []
        if not row["ingestion_running"]:
            reasons.append("matching ingestion configuration is not running")
        if not row["adapter_healthy"]:
            reasons.append(f"{adapter_name} adapter is not healthy")
        if source_class == "IBKR_NATIVE_CAPTURE" and int(row["fresh_quote_count"]) < len(
            active_instrument_ids
        ):
            reasons.append("one or more native capture instruments lack fresh canonical evidence")
        if int(row["global_position"]) - int(row["checkpoint_position"]) > 100:
            reasons.append("projection is more than 100 events behind")
        checkpoint_updated_at = row["checkpoint_updated_at"]
        if checkpoint_updated_at is None:
            reasons.append("projection checkpoint is unknown")
        return {
            "ready": not reasons,
            "reasons": reasons,
            "expected_instruments": len(expected_instrument_ids),
            "expected_active_instruments": len(active_instrument_ids),
            "fresh_quote_count": int(row["fresh_quote_count"]),
            "global_position": int(row["global_position"]),
            "checkpoint_position": int(row["checkpoint_position"]),
            "checkpoint_updated_at": checkpoint_updated_at,
            "configuration_hash": expected_configuration_hash,
            "provider": provider,
            "environment": environment,
            "source_class": source_class,
        }

    async def source_instrument_ids(
        self, *, provider: str, environment: str, configuration_hash: str | None = None
    ) -> tuple[str, ...]:
        rows = await self._store.query(
            """
            SELECT DISTINCT instrument_id
            FROM reference.provider_listings
            WHERE provider = :provider AND environment = :environment
              AND (CAST(:configuration_hash AS text) IS NULL
                   OR universe_hash = CAST(:configuration_hash AS text))
              AND (valid_to IS NULL OR valid_to > clock_timestamp())
            ORDER BY instrument_id
            """,
            {
                "provider": provider,
                "environment": environment,
                "configuration_hash": configuration_hash,
            },
        )
        return tuple(str(row["instrument_id"]) for row in rows)

    async def capture_identity(
        self,
        *,
        provider: str | None = None,
        environment: str | None = None,
        source_class: str | None = None,
        configuration_hash: str | None = None,
        capture_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        rows = await self._store.query(
            """
            SELECT provider, environment, source_class, capture_source_id, universe_id,
                   configuration_hash, capture_session_id, connection_generation,
                   arrival_sequence, received_time
            FROM raw.market_messages
            WHERE (
                CAST(:provider AS text) IS NULL
                OR provider = CAST(:provider AS text)
            ) AND (
                CAST(:environment AS text) IS NULL
                OR environment = CAST(:environment AS text)
            ) AND (
                CAST(:source_class AS text) IS NULL
                OR source_class = CAST(:source_class AS text)
            ) AND (
                CAST(:configuration_hash AS text) IS NULL
                OR configuration_hash = CAST(:configuration_hash AS text)
            ) AND (
                CAST(:capture_session_id AS uuid) IS NULL
                OR capture_session_id = CAST(:capture_session_id AS uuid)
            )
            ORDER BY id DESC
            LIMIT 1
            """,
            {
                "provider": provider,
                "environment": environment,
                "source_class": source_class,
                "configuration_hash": configuration_hash,
                "capture_session_id": capture_session_id,
            },
        )
        return rows[0] if rows else None

    async def capture_reconciliation(
        self,
        *,
        provider: str,
        environment: str,
        source_class: str | None = None,
        configuration_hash: str | None = None,
        capture_session_id: str | None = None,
        adapter_accepted: int | None = None,
        stale_generation_rejected: int = 0,
        records_dropped: int = 0,
        records_failed: int = 0,
    ) -> dict[str, Any]:
        native = source_class == "IBKR_NATIVE_CAPTURE"
        resolved_capture_session_id = capture_session_id
        if native and resolved_capture_session_id is None:
            identity_parameters = {
                "provider": provider,
                "environment": environment,
                "source_class": source_class,
                "configuration_hash": configuration_hash,
            }
            latest_session_rows = await self._store.query(
                """
                SELECT capture_session_id
                FROM ops.capture_session_metrics
                WHERE provider = :provider
                  AND environment = :environment
                  AND source_class = :source_class
                  AND configuration_hash = :configuration_hash
                ORDER BY observed_at DESC, capture_session_id DESC
                LIMIT 1
                """,
                identity_parameters,
            )
            if not latest_session_rows:
                latest_session_rows = await self._store.query(
                    """
                    SELECT capture_session_id
                    FROM raw.market_messages
                    WHERE provider = :provider
                      AND environment = :environment
                      AND source_class = :source_class
                      AND configuration_hash = :configuration_hash
                      AND capture_session_id IS NOT NULL
                    ORDER BY received_time DESC, id DESC
                    LIMIT 1
                    """,
                    identity_parameters,
                )
            if latest_session_rows:
                resolved_capture_session_id = str(latest_session_rows[0]["capture_session_id"])

        rows = await self._store.query(
            """
            WITH selected AS (
                SELECT id
                FROM raw.market_messages
                WHERE provider = :provider AND environment = :environment
                  AND (
                      CAST(:source_class AS text) IS NULL
                      OR source_class = CAST(:source_class AS text)
                  )
                  AND (
                      CAST(:configuration_hash AS text) IS NULL
                      OR configuration_hash = CAST(:configuration_hash AS text)
                  )
                  AND (
                      CAST(:capture_session_id AS uuid) IS NULL
                      OR capture_session_id = CAST(:capture_session_id AS uuid)
                  )
            ), canonical AS (
                SELECT DISTINCT raw_record_id
                FROM canonical.events
                WHERE raw_record_id IN (SELECT id FROM selected)
            ), quarantined AS (
                SELECT DISTINCT raw_record_id
                FROM raw.quarantine
                WHERE raw_record_id IN (SELECT id FROM selected)
            )
            SELECT
                (SELECT COUNT(*) FROM selected) AS raw_persisted,
                (SELECT COUNT(*) FROM canonical) AS canonical_persisted,
                (SELECT COUNT(*) FROM quarantined) AS quarantined,
                (SELECT COUNT(*) FROM selected)
                    - (SELECT COUNT(*) FROM canonical)
                    - (SELECT COUNT(*) FROM quarantined) AS unclassified
            """,
            {
                "provider": provider,
                "environment": environment,
                "source_class": source_class,
                "configuration_hash": configuration_hash,
                "capture_session_id": resolved_capture_session_id,
            },
        )
        row = rows[0]
        raw_persisted = int(row["raw_persisted"])
        metrics_rows = (
            await self._store.query(
                """
                SELECT records_received, persisted, failed, dropped, observed_at
                FROM ops.capture_session_metrics
                WHERE provider = :provider
                  AND environment = :environment
                  AND source_class = :source_class
                  AND configuration_hash = :configuration_hash
                  AND (
                      CAST(:capture_session_id AS uuid) IS NULL
                      OR capture_session_id = CAST(:capture_session_id AS uuid)
                  )
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                {
                    "provider": provider,
                    "environment": environment,
                    "source_class": source_class,
                    "configuration_hash": configuration_hash,
                    "capture_session_id": resolved_capture_session_id,
                },
            )
            if native
            else []
        )
        if native and metrics_rows:
            metrics = metrics_rows[0]
            accepted: int | None = int(metrics["records_received"])
            raw_offered: int | None = accepted
            effective_dropped = int(metrics["dropped"])
            effective_failed = int(metrics["failed"])
            reconciliation_source = "ops.capture_session_metrics"
        elif native and adapter_accepted is None:
            accepted = None
            raw_offered = None
            effective_dropped = None
            effective_failed = None
            reconciliation_source = "DURABLE_SESSION_METRICS_UNAVAILABLE"
        else:
            accepted = raw_persisted if adapter_accepted is None else adapter_accepted
            raw_offered = accepted
            effective_dropped = records_dropped
            effective_failed = records_failed
            reconciliation_source = (
                "CALLER_COUNTERS" if adapter_accepted is not None else "RAW_PERSISTED_FALLBACK"
            )
        loss = None if accepted is None else max(accepted - raw_persisted, 0)
        return {
            "provider": provider,
            "environment": environment,
            "source_class": source_class,
            "configuration_hash": configuration_hash,
            "capture_session_id": resolved_capture_session_id,
            "adapter_accepted": accepted,
            "raw_offered": raw_offered,
            "raw_persisted": raw_persisted,
            "canonical_eligible": int(row["canonical_persisted"]),
            "canonical_persisted": int(row["canonical_persisted"]),
            "quarantined": int(row["quarantined"]),
            "unclassified_raw": int(row["unclassified"]),
            "stale_generation_rejected": stale_generation_rejected,
            "records_dropped": effective_dropped,
            "records_failed": effective_failed,
            "reconciliation_source": reconciliation_source,
            "loss": loss,
        }

    async def instruments(
        self,
        *,
        provider: str | None = None,
        environment: str | None = None,
        source_class: str | None = None,
        configuration_hash: str | None = None,
    ) -> list[dict[str, Any]]:
        if source_class == "IBKR_NATIVE_CAPTURE":
            return await self._store.query(
                """
                SELECT i.*, q.provider, q.environment, q.external_id,
                       q.configuration_hash, q.event_time AS quote_event_time, q.received_time,
                       q.bid, q.ask,
                       CASE
                           WHEN q.received_time < clock_timestamp() - INTERVAL '30 seconds'
                           THEN 'STALE'
                           ELSE q.quality
                       END AS quality
                FROM reference.instruments i
                LEFT JOIN read_model.capture_latest_quotes q
                  ON q.instrument_id = i.instrument_id
                 AND q.source_class = :source_class
                 AND q.provider = :provider
                 AND q.environment = :environment
                 AND q.configuration_hash = :configuration_hash
                WHERE EXISTS (
                    SELECT 1 FROM reference.provider_listings l
                    WHERE l.instrument_id = i.instrument_id
                      AND l.provider = :provider
                      AND l.environment = :environment
                      AND l.universe_hash = :configuration_hash
                      AND (l.valid_to IS NULL OR l.valid_to > clock_timestamp())
                )
                ORDER BY i.instrument_id
                """,
                {
                    "source_class": source_class,
                    "provider": provider,
                    "environment": environment,
                    "configuration_hash": configuration_hash,
                },
            )
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
            """,
        )

    async def instrument(
        self,
        instrument_id: str,
        *,
        provider: str | None = None,
        environment: str | None = None,
        source_class: str | None = None,
        configuration_hash: str | None = None,
    ) -> dict[str, Any] | None:
        quote_table = "read_model.latest_quotes"
        quote_filter = ""
        quote_parameters: dict[str, Any] = {}
        listing_filter = ""
        if source_class == "IBKR_NATIVE_CAPTURE":
            quote_table = "read_model.capture_latest_quotes"
            quote_filter = """
              AND q.source_class = :source_class
              AND q.provider = :provider
              AND q.environment = :environment
              AND q.configuration_hash = :configuration_hash
            """
            quote_parameters = {
                "source_class": source_class,
                "provider": provider,
                "environment": environment,
                "configuration_hash": configuration_hash,
            }
            listing_filter = """
              AND l.provider = :provider
              AND l.environment = :environment
              AND l.universe_hash = :configuration_hash
            """
        join_clause = (
            f"LEFT JOIN {quote_table} q ON q.instrument_id = i.instrument_id{quote_filter}"
        )
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
            """
            + join_clause
            + """
            WHERE i.instrument_id = :instrument_id
            """,
            {"instrument_id": instrument_id, **quote_parameters},
        )
        if not instruments:
            return None
        bars = await self.bars(instrument_id=instrument_id, limit=300)
        listings = await self._store.query(
            """
            SELECT l.* FROM reference.provider_listings l
            WHERE l.instrument_id = :instrument_id
            """
            + listing_filter
            + """
            ORDER BY l.valid_from DESC
            """,
            {"instrument_id": instrument_id, **quote_parameters},
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

    async def event_page(
        self,
        *,
        after_position: int,
        limit: int,
        source_class: str | None = None,
        provider: str | None = None,
        environment: str | None = None,
        configuration_hash: str | None = None,
    ) -> EventPage:
        return await self._store.read_page(
            after_position=after_position,
            limit=limit,
            source_class=source_class,
            provider=provider,
            environment=environment,
            configuration_hash=configuration_hash,
        )
