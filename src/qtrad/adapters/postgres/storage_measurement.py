"""Bounded read-only PostgreSQL storage measurements."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_SAMPLE_ROWS = 10_000


@dataclass(frozen=True, slots=True)
class RelationStorage:
    schema_name: str
    relation_name: str
    estimated_rows: int
    heap_bytes: int
    index_bytes: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class IndexStorage:
    schema_name: str
    relation_name: str
    index_name: str
    index_bytes: int
    scans_since_statistics_reset: int


@dataclass(frozen=True, slots=True)
class PayloadSample:
    sample_rows: int
    average_payload_bytes: int
    average_payload_fields: int


@dataclass(frozen=True, slots=True)
class PostgresStorageMeasurement:
    observed_at: datetime
    database_name: str
    database_bytes: int
    raw_message_count: int
    canonical_event_count: int
    relations: tuple[RelationStorage, ...]
    indexes: tuple[IndexStorage, ...]
    raw_payload_sample: PayloadSample
    canonical_payload_sample: PayloadSample


class PostgresStorageInspector:
    """Measure capture storage without mutating application or statistics state."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def measure(self) -> PostgresStorageMeasurement:
        async with self._engine.connect() as connection, connection.begin():
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            summary = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                clock_timestamp() AS observed_at,
                                current_database() AS database_name,
                                pg_database_size(current_database()) AS database_bytes,
                                (SELECT count(*) FROM raw.market_messages) AS raw_message_count,
                                (SELECT count(*) FROM canonical.events) AS canonical_event_count
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
            relation_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                schemaname AS schema_name,
                                relname AS relation_name,
                                GREATEST(n_live_tup, 0)::bigint AS estimated_rows,
                                pg_relation_size(relid) AS heap_bytes,
                                pg_indexes_size(relid) AS index_bytes,
                                pg_total_relation_size(relid) AS total_bytes
                            FROM pg_stat_user_tables
                            WHERE schemaname IN (
                                'raw', 'canonical', 'reference', 'read_model', 'ops'
                            )
                            ORDER BY schemaname, relname
                            """
                        )
                    )
                )
                .mappings()
                .all()
            )
            index_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                schemaname AS schema_name,
                                relname AS relation_name,
                                indexrelname AS index_name,
                                pg_relation_size(indexrelid) AS index_bytes,
                                idx_scan AS scans_since_statistics_reset
                            FROM pg_stat_user_indexes
                            WHERE schemaname IN (
                                'raw', 'canonical', 'reference', 'read_model', 'ops'
                            )
                            ORDER BY schemaname, relname, indexrelname
                            """
                        )
                    )
                )
                .mappings()
                .all()
            )
            raw_sample = await _payload_sample(
                connection,
                relation="raw.market_messages",
                order_column="id",
            )
            canonical_sample = await _payload_sample(
                connection,
                relation="canonical.events",
                order_column="global_position",
            )

        return PostgresStorageMeasurement(
            observed_at=summary["observed_at"],
            database_name=str(summary["database_name"]),
            database_bytes=int(summary["database_bytes"]),
            raw_message_count=int(summary["raw_message_count"]),
            canonical_event_count=int(summary["canonical_event_count"]),
            relations=tuple(
                RelationStorage(
                    schema_name=str(row["schema_name"]),
                    relation_name=str(row["relation_name"]),
                    estimated_rows=int(row["estimated_rows"]),
                    heap_bytes=int(row["heap_bytes"]),
                    index_bytes=int(row["index_bytes"]),
                    total_bytes=int(row["total_bytes"]),
                )
                for row in relation_rows
            ),
            indexes=tuple(
                IndexStorage(
                    schema_name=str(row["schema_name"]),
                    relation_name=str(row["relation_name"]),
                    index_name=str(row["index_name"]),
                    index_bytes=int(row["index_bytes"]),
                    scans_since_statistics_reset=int(row["scans_since_statistics_reset"]),
                )
                for row in index_rows
            ),
            raw_payload_sample=raw_sample,
            canonical_payload_sample=canonical_sample,
        )


async def _payload_sample(
    connection: AsyncConnection,
    *,
    relation: str,
    order_column: str,
) -> PayloadSample:
    if (relation, order_column) not in {
        ("raw.market_messages", "id"),
        ("canonical.events", "global_position"),
    }:
        raise ValueError("storage payload sample relation is not allowlisted")
    row = (
        (
            await connection.execute(
                text(
                    f"""
                WITH sample AS (
                    SELECT payload
                    FROM {relation}
                    ORDER BY {order_column} DESC
                    LIMIT {_SAMPLE_ROWS}
                )
                SELECT
                    count(*) AS sample_rows,
                    COALESCE(round(avg(pg_column_size(payload))), 0)::bigint
                        AS average_payload_bytes,
                    COALESCE(round(avg(jsonb_object_length(payload))), 0)::bigint
                        AS average_payload_fields
                FROM sample
                """
                )
            )
        )
        .mappings()
        .one()
    )
    return PayloadSample(
        sample_rows=int(row["sample_rows"]),
        average_payload_bytes=int(row["average_payload_bytes"]),
        average_payload_fields=int(row["average_payload_fields"]),
    )
