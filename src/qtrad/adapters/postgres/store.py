"""PostgreSQL implementation of raw capture, canonical events and projections."""

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from qtrad.domain.events import EventEnvelope, JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId, RunId
from qtrad.domain.instruments import INITIAL_INSTRUMENTS, Instrument, ProductType, ProviderListing
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.domain.operations import AdapterHealth
from qtrad.ports.storage import (
    AppendResult,
    AuditStore,
    EventPage,
    RawMessage,
    ResearchManifest,
)


class StreamVersionConflict(RuntimeError):
    """The caller appended against a stale stream version."""


class PostgresAuditStore(AuditStore):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def seed_instruments(
        self, instruments: Sequence[Instrument] = INITIAL_INSTRUMENTS
    ) -> None:
        statement = text(
            """
            INSERT INTO reference.instruments (
                instrument_id, display_name, asset_class, base_currency,
                quote_currency, search_aliases
            ) VALUES (
                :instrument_id, :display_name, :asset_class, :base_currency,
                :quote_currency, CAST(:search_aliases AS jsonb)
            )
            ON CONFLICT (instrument_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                asset_class = EXCLUDED.asset_class,
                base_currency = EXCLUDED.base_currency,
                quote_currency = EXCLUDED.quote_currency,
                search_aliases = EXCLUDED.search_aliases
            """
        )
        async with self._engine.begin() as connection:
            for instrument in instruments:
                await connection.execute(
                    statement,
                    {
                        "instrument_id": str(instrument.instrument_id),
                        "display_name": instrument.display_name,
                        "asset_class": instrument.asset_class.value,
                        "base_currency": instrument.base_currency,
                        "quote_currency": instrument.quote_currency,
                        "search_aliases": json.dumps(instrument.search_aliases),
                    },
                )

    async def upsert_provider_listing(
        self,
        listing: ProviderListing,
        metadata: Mapping[str, JsonValue],
        *,
        universe_hash: str | None = None,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE reference.provider_listings SET valid_to = :valid_from
                    WHERE provider = :provider AND environment = :environment
                      AND external_id = :external_id AND valid_to IS NULL
                      AND metadata_version <> :metadata_version
                    """
                ),
                {
                    "provider": listing.listing_id.provider,
                    "environment": listing.listing_id.environment,
                    "external_id": listing.listing_id.external_id,
                    "valid_from": listing.valid_from,
                    "metadata_version": listing.metadata_version,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO reference.provider_listings (
                        provider, environment, external_id, instrument_id,
                        display_name, product_type, currency, minimum_deal_size,
                          price_increment, valid_from, valid_to, metadata_version, metadata,
                          economics, universe_hash
                    ) VALUES (
                        :provider, :environment, :external_id, :instrument_id,
                        :display_name, :product_type, :currency, :minimum_deal_size,
                          :price_increment, :valid_from, :valid_to, :metadata_version,
                          CAST(:metadata AS jsonb), CAST(:economics AS jsonb), :universe_hash
                    )
                    ON CONFLICT (provider, environment, external_id, valid_from)
                    DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        product_type = EXCLUDED.product_type,
                        currency = EXCLUDED.currency,
                        minimum_deal_size = EXCLUDED.minimum_deal_size,
                        price_increment = EXCLUDED.price_increment,
                        valid_to = EXCLUDED.valid_to,
                        metadata_version = EXCLUDED.metadata_version,
                          metadata = EXCLUDED.metadata,
                          economics = EXCLUDED.economics,
                          universe_hash = EXCLUDED.universe_hash
                    """
                ),
                {
                    "provider": listing.listing_id.provider,
                    "environment": listing.listing_id.environment,
                    "external_id": listing.listing_id.external_id,
                    "instrument_id": str(listing.instrument_id),
                    "display_name": listing.display_name,
                    "product_type": listing.product_type.value,
                    "currency": listing.currency,
                    "minimum_deal_size": listing.minimum_deal_size,
                    "price_increment": listing.price_increment,
                    "valid_from": listing.valid_from,
                    "valid_to": listing.valid_to,
                    "metadata_version": listing.metadata_version,
                    "metadata": json.dumps(metadata, sort_keys=True),
                    "economics": json.dumps(listing.economics, sort_keys=True),
                    "universe_hash": universe_hash,
                },
            )

    async def validate_provider_listing(
        self, listing: ProviderListing, *, universe_hash: str, observed_at: datetime
    ) -> EventEnvelope | None:
        """Record a bounded listing-validation fact before changing its projection."""

        existing = await self.query(
            """
            SELECT metadata_version, universe_hash FROM reference.provider_listings
            WHERE provider = :provider AND environment = :environment AND external_id = :external_id
              AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1
            """,
            {
                "provider": listing.listing_id.provider,
                "environment": listing.listing_id.environment,
                "external_id": listing.listing_id.external_id,
            },
        )
        if existing and (
            existing[0]["metadata_version"] == listing.metadata_version
            and existing[0]["universe_hash"] == universe_hash
        ):
            return None
        stream_id = (
            f"provider-listing:{listing.listing_id.provider}:{listing.listing_id.environment}:"
            f"{listing.listing_id.external_id}"
        )
        previous = await self.latest_stream_version(stream_id)
        event = EventEnvelope.create(
            stream_id=stream_id,
            stream_version=previous + 1,
            event_type="ProviderListingValidated",
            event_time=observed_at,
            received_time=observed_at,
            producer="ig-demo-discovery",
            producer_version="0.1.0",
            payload={"listing": listing, "universe_hash": universe_hash},
        )
        try:
            persisted = await self.append(event, expected_stream_version=previous)
        except StreamVersionConflict:
            return await self.validate_provider_listing(
                listing, universe_hash=universe_hash, observed_at=observed_at
            )
        metadata = to_json_value(listing)
        if not isinstance(metadata, dict):
            raise TypeError("provider listing did not serialise to an object")
        await self.upsert_provider_listing(listing, metadata, universe_hash=universe_hash)
        return persisted

    async def active_provider_listings(
        self, instrument_ids: Sequence[InstrumentId] | None = None
    ) -> tuple[ProviderListing, ...]:
        identifiers = [str(identifier) for identifier in instrument_ids] if instrument_ids else None
        rows = await self.query(
            """
            SELECT DISTINCT ON (provider, environment, external_id)
                provider, environment, external_id, instrument_id, display_name,
                product_type, currency, minimum_deal_size, price_increment,
                valid_from, valid_to, metadata_version
            FROM reference.provider_listings
              WHERE (valid_to IS NULL OR valid_to > clock_timestamp())
                AND (CAST(:instrument_ids AS jsonb) IS NULL
                     OR instrument_id IN (
                         SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
                     ))
            ORDER BY provider, environment, external_id, valid_from DESC
            """,
            {"instrument_ids": json.dumps(identifiers) if identifiers is not None else None},
        )
        return tuple(
            ProviderListing(
                listing_id=ProviderListingId(
                    provider=str(row["provider"]),
                    environment=str(row["environment"]),
                    external_id=str(row["external_id"]),
                ),
                instrument_id=InstrumentId(str(row["instrument_id"])),
                display_name=str(row["display_name"]),
                product_type=ProductType(str(row["product_type"])),
                currency=str(row["currency"]),
                minimum_deal_size=row["minimum_deal_size"],
                price_increment=row["price_increment"],
                valid_from=_utc(row["valid_from"]),
                valid_to=_utc(row["valid_to"]) if row["valid_to"] else None,
                metadata_version=str(row["metadata_version"]),
            )
            for row in rows
        )

    async def record_adapter_health(self, health: AdapterHealth) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ops.adapter_health (
                        adapter_name, environment, status, observed_at,
                        last_message_at, detail
                    ) VALUES (
                        :adapter_name, :environment, :status, :observed_at,
                        :last_message_at, :detail
                    )
                    ON CONFLICT (adapter_name) DO UPDATE SET
                        environment = EXCLUDED.environment,
                        status = EXCLUDED.status,
                        observed_at = EXCLUDED.observed_at,
                        last_message_at = EXCLUDED.last_message_at,
                        detail = EXCLUDED.detail
                    """
                ),
                {
                    "adapter_name": health.adapter_name,
                    "environment": health.environment.value,
                    "status": health.status.value,
                    "observed_at": health.observed_at,
                    "last_message_at": health.last_message_at,
                    "detail": health.detail,
                },
            )

    async def start_run(
        self,
        *,
        kind: RunKind,
        environment: BrokerEnvironment,
        configuration_hash: str,
        started_at: datetime,
    ) -> RunId:
        run_id = RunId.new()
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ops.runs (
                        run_id, kind, status, environment, started_at,
                        configuration_hash, detail
                    ) VALUES (
                        :run_id, :kind, 'RUNNING', :environment, :started_at,
                        :configuration_hash, '{}'::jsonb
                    )
                    """
                ),
                {
                    "run_id": run_id.value,
                    "kind": kind.value,
                    "environment": environment.value,
                    "started_at": started_at,
                    "configuration_hash": configuration_hash,
                },
            )
        return run_id

    async def finish_run(
        self,
        run_id: RunId,
        *,
        status: str,
        finished_at: datetime,
        detail: Mapping[str, JsonValue],
    ) -> None:
        if status not in {"COMPLETED", "FAILED", "STOPPED"}:
            raise ValueError("invalid terminal run status")
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE ops.runs
                    SET status = :status,
                        finished_at = :finished_at,
                        detail = CAST(:detail AS jsonb)
                    WHERE run_id = :run_id
                    """
                ),
                {
                    "run_id": run_id.value,
                    "status": status,
                    "finished_at": finished_at,
                    "detail": json.dumps(detail, sort_keys=True),
                },
            )

    async def record_manifest(self, manifest: ResearchManifest) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ops.research_manifests (
                        manifest_id, created_at, schema_version, row_count,
                        minimum_event_time, maximum_event_time, content_sha256,
                        configuration_hash, files, metadata
                    ) VALUES (
                        :manifest_id, :created_at, :schema_version, :row_count,
                        :minimum_event_time, :maximum_event_time, :content_sha256,
                        :configuration_hash, CAST(:files AS jsonb), CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (manifest_id) DO NOTHING
                    """
                ),
                {
                    "manifest_id": manifest.manifest_id,
                    "created_at": manifest.created_at,
                    "schema_version": manifest.schema_version,
                    "row_count": manifest.row_count,
                    "minimum_event_time": manifest.minimum_event_time,
                    "maximum_event_time": manifest.maximum_event_time,
                    "content_sha256": manifest.content_sha256,
                    "configuration_hash": manifest.configuration_hash,
                    "files": json.dumps(manifest.files),
                    "metadata": json.dumps(manifest.metadata, sort_keys=True),
                },
            )

    async def record_quota_state(
        self,
        *,
        provider: str,
        environment: str,
        allowance_name: str,
        remaining: int,
        observed_at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ops.quota_state (
                        provider, environment, allowance_name, remaining, observed_at
                    ) VALUES (
                        :provider, :environment, :allowance_name, :remaining, :observed_at
                    )
                    ON CONFLICT (provider, environment, allowance_name) DO UPDATE SET
                        remaining = EXCLUDED.remaining,
                        observed_at = EXCLUDED.observed_at
                    """
                ),
                {
                    "provider": provider,
                    "environment": environment,
                    "allowance_name": allowance_name,
                    "remaining": remaining,
                    "observed_at": observed_at,
                },
            )

    async def capture(self, message: RawMessage) -> int:
        async with self._engine.begin() as connection:
            raw_id, _ = await self._capture(connection, message)
            return raw_id

    async def quarantine(self, message: RawMessage, *, reason_code: str, detail: str) -> int:
        async with self._engine.begin() as connection:
            raw_id, _ = await self._capture(connection, message)
            await connection.execute(
                text(
                    """
                    INSERT INTO raw.quarantine (raw_record_id, reason_code, detail)
                    VALUES (:raw_record_id, :reason_code, :detail)
                    ON CONFLICT (raw_record_id) DO NOTHING
                    """
                ),
                {
                    "raw_record_id": raw_id,
                    "reason_code": reason_code,
                    "detail": detail,
                },
            )
            return raw_id

    async def latest_stream_version(self, stream_id: str) -> int:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(stream_version), 0)
                    FROM canonical.events
                    WHERE stream_id = :stream_id
                    """
                ),
                {"stream_id": stream_id},
            )
            return int(result.scalar_one())

    async def append(self, event: EventEnvelope, *, expected_stream_version: int) -> EventEnvelope:
        async with self._engine.begin() as connection:
            return await self._append(
                connection,
                event,
                expected_stream_version=expected_stream_version,
                raw_record_id=None,
            )

    async def capture_and_append(
        self,
        message: RawMessage,
        event: EventEnvelope,
        *,
        expected_stream_version: int,
    ) -> AppendResult:
        async with self._engine.begin() as connection:
            raw_id, duplicate = await self._capture(connection, message)
            if duplicate:
                return AppendResult(event=None, raw_record_id=raw_id, duplicate=True)
            persisted = await self._append(
                connection,
                event,
                expected_stream_version=expected_stream_version,
                raw_record_id=raw_id,
            )
            return AppendResult(event=persisted, raw_record_id=raw_id, duplicate=False)

    async def _capture(self, connection: AsyncConnection, message: RawMessage) -> tuple[int, bool]:
        safe_payload = _redact_mapping(message.payload)
        payload_text = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_text.encode()).hexdigest()
        result = await connection.execute(
            text(
                """
                INSERT INTO raw.market_messages (
                    provider, environment, subscription, deduplication_key,
                    received_time, payload, payload_sha256, adapter_version
                ) VALUES (
                    :provider, :environment, :subscription, :deduplication_key,
                    :received_time, CAST(:payload AS jsonb), :payload_sha256,
                    :adapter_version
                )
                ON CONFLICT (provider, environment, deduplication_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "provider": message.provider,
                "environment": message.environment,
                "subscription": message.subscription,
                "deduplication_key": message.deduplication_key,
                "received_time": message.received_time,
                "payload": payload_text,
                "payload_sha256": payload_hash,
                "adapter_version": message.adapter_version,
            },
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return int(inserted), False
        existing = await connection.execute(
            text(
                """
                SELECT id FROM raw.market_messages
                WHERE provider = :provider
                  AND environment = :environment
                  AND deduplication_key = :deduplication_key
                """
            ),
            {
                "provider": message.provider,
                "environment": message.environment,
                "deduplication_key": message.deduplication_key,
            },
        )
        return int(existing.scalar_one()), True

    async def _append(
        self,
        connection: AsyncConnection,
        event: EventEnvelope,
        *,
        expected_stream_version: int,
        raw_record_id: int | None,
    ) -> EventEnvelope:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:stream_id))"),
            {"stream_id": event.stream_id},
        )
        current_result = await connection.execute(
            text(
                """
                SELECT COALESCE(MAX(stream_version), 0)
                FROM canonical.events
                WHERE stream_id = :stream_id
                """
            ),
            {"stream_id": event.stream_id},
        )
        current = int(current_result.scalar_one())
        if current != expected_stream_version or event.stream_version != current + 1:
            raise StreamVersionConflict(
                f"{event.stream_id} expected {expected_stream_version}, current {current}"
            )
        try:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO canonical.events (
                        event_id, stream_id, stream_version, event_type, schema_version,
                        event_time, received_time, correlation_id, causation_id,
                        producer, producer_version, payload, raw_record_id
                    ) VALUES (
                        :event_id, :stream_id, :stream_version, :event_type, :schema_version,
                        :event_time, :received_time, :correlation_id, :causation_id,
                        :producer, :producer_version, CAST(:payload AS jsonb), :raw_record_id
                    )
                    RETURNING global_position, persisted_time
                    """
                ),
                {
                    "event_id": event.event_id,
                    "stream_id": event.stream_id,
                    "stream_version": event.stream_version,
                    "event_type": event.event_type,
                    "schema_version": event.schema_version,
                    "event_time": event.event_time,
                    "received_time": event.received_time,
                    "correlation_id": event.correlation_id,
                    "causation_id": event.causation_id,
                    "producer": event.producer,
                    "producer_version": event.producer_version,
                    "payload": json.dumps(event.payload, sort_keys=True),
                    "raw_record_id": raw_record_id,
                },
            )
        except IntegrityError as error:
            raise StreamVersionConflict(str(error)) from error
        row = result.mappings().one()
        persisted = replace(
            event,
            global_position=int(row["global_position"]),
            persisted_time=_utc(row["persisted_time"]),
            raw_record_id=raw_record_id,
        )
        await self._project(connection, persisted)
        await connection.execute(
            text(
                """
                INSERT INTO ops.projection_checkpoints (
                    projection_name, global_position, updated_at
                ) VALUES (
                    'core', :global_position, clock_timestamp()
                )
                ON CONFLICT (projection_name) DO UPDATE SET
                    global_position = EXCLUDED.global_position,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {"global_position": persisted.global_position},
        )
        return persisted

    async def _project(self, connection: AsyncConnection, event: EventEnvelope) -> None:
        if event.event_type == "MarketQuoteObserved":
            payload = event.payload
            listing = _mapping(payload["listing_id"])
            await connection.execute(
                text(
                    """
                    INSERT INTO read_model.latest_quotes (
                        instrument_id, provider, environment, external_id,
                        event_time, received_time, bid, ask, bid_size, ask_size,
                        quality, global_position
                    ) VALUES (
                        :instrument_id, :provider, :environment, :external_id,
                        :event_time, :received_time, :bid, :ask, :bid_size, :ask_size,
                        :quality, :global_position
                    )
                    ON CONFLICT (instrument_id) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        environment = EXCLUDED.environment,
                        external_id = EXCLUDED.external_id,
                        event_time = EXCLUDED.event_time,
                        received_time = EXCLUDED.received_time,
                        bid = EXCLUDED.bid,
                        ask = EXCLUDED.ask,
                        bid_size = EXCLUDED.bid_size,
                        ask_size = EXCLUDED.ask_size,
                        quality = EXCLUDED.quality,
                        global_position = EXCLUDED.global_position
                    WHERE read_model.latest_quotes.global_position < EXCLUDED.global_position
                    """
                ),
                {
                    "instrument_id": str(payload["instrument_id"]),
                    "provider": str(listing["provider"]),
                    "environment": str(listing["environment"]),
                    "external_id": str(listing["external_id"]),
                    "event_time": _parse_datetime(payload["event_time"]),
                    "received_time": _parse_datetime(payload["received_time"]),
                    "bid": payload.get("bid"),
                    "ask": payload.get("ask"),
                    "bid_size": payload.get("bid_size"),
                    "ask_size": payload.get("ask_size"),
                    "quality": str(payload["quality"]),
                    "global_position": event.global_position,
                },
            )
        elif event.event_type in {"MarketBarClosed", "MarketBarCorrected"}:
            payload = event.payload
            source_listing = _mapping(payload["source_listing_id"])
            await connection.execute(
                text(
                    """
                    INSERT INTO read_model.market_bars (
                        instrument_id, basis, interval_start, interval_end,
                        open, high, low, close, sample_count, revision,
                        provenance, quality, source_provider, source_environment,
                        source_external_id, global_position
                    ) VALUES (
                        :instrument_id, :basis, :interval_start, :interval_end,
                        :open, :high, :low, :close, :sample_count, :revision,
                        :provenance, :quality, :source_provider, :source_environment,
                        :source_external_id, :global_position
                    )
                    ON CONFLICT (
                        instrument_id, basis, interval_start, revision,
                        provenance, source_provider, source_environment, source_external_id
                    ) DO NOTHING
                    """
                ),
                {
                    "instrument_id": str(payload["instrument_id"]),
                    "basis": str(payload["basis"]),
                    "interval_start": _parse_datetime(payload["interval_start"]),
                    "interval_end": _parse_datetime(payload["interval_end"]),
                    "open": payload["open"],
                    "high": payload["high"],
                    "low": payload["low"],
                    "close": payload["close"],
                    "sample_count": payload["sample_count"],
                    "revision": payload["revision"],
                    "provenance": str(payload["provenance"]),
                    "quality": str(payload["quality"]),
                    "source_provider": str(source_listing["provider"]),
                    "source_environment": str(source_listing["environment"]),
                    "source_external_id": str(source_listing["external_id"]),
                    "global_position": event.global_position,
                },
            )
        elif event.event_type == "MarketDataGapDetected":
            payload = event.payload
            await connection.execute(
                text(
                    """
                    INSERT INTO read_model.data_gaps (
                        gap_id, instrument_id, interval_start, interval_end,
                        reason, detected_at, repaired_at
                    ) VALUES (
                        :gap_id, :instrument_id, :interval_start, :interval_end,
                        :reason, :detected_at, :repaired_at
                    )
                    ON CONFLICT (gap_id) DO NOTHING
                    """
                ),
                {
                    "gap_id": event.event_id,
                    "instrument_id": str(payload["instrument_id"]),
                    "interval_start": _parse_datetime(payload["interval_start"]),
                    "interval_end": _parse_datetime(payload["interval_end"]),
                    "reason": str(payload["reason"]),
                    "detected_at": _parse_datetime(payload["detected_at"]),
                    "repaired_at": (
                        _parse_datetime(payload["repaired_at"])
                        if payload.get("repaired_at")
                        else None
                    ),
                },
            )

    async def rebuild_projections(self) -> int:
        async with self._engine.begin() as connection:
            await connection.execute(text("TRUNCATE read_model.latest_quotes"))
            await connection.execute(text("TRUNCATE read_model.market_bars"))
            await connection.execute(text("TRUNCATE read_model.data_gaps"))
            rows = await connection.execute(
                text("SELECT * FROM canonical.events ORDER BY global_position")
            )
            count = 0
            last_position = 0
            for row in rows.mappings():
                event = _event_from_row(row)
                await self._project(connection, event)
                count += 1
                last_position = event.global_position or last_position
            await connection.execute(
                text(
                    """
                    INSERT INTO ops.projection_checkpoints (
                        projection_name, global_position, updated_at
                    ) VALUES (
                        'core', :global_position, clock_timestamp()
                    )
                    ON CONFLICT (projection_name) DO UPDATE SET
                        global_position = EXCLUDED.global_position,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {"global_position": last_position},
            )
            return count

    async def query(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> list[dict[str, Any]]:
        async with self._engine.connect() as connection:
            result = await connection.execute(text(statement), parameters or {})
            return [dict(row) for row in result.mappings()]

    async def read_all(self, *, after_position: int = 0) -> AsyncIterator[EventEnvelope]:
        async with self._engine.connect() as connection:
            result = await connection.stream(
                text(
                    """
                    SELECT * FROM canonical.events
                    WHERE global_position > :after_position
                    ORDER BY global_position
                    """
                ),
                {"after_position": after_position},
            )
            async for row in result.mappings():
                yield _event_from_row(row)

    async def read_page(self, *, after_position: int, limit: int) -> EventPage:
        if after_position < 0:
            raise ValueError("event cursor cannot be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("event page limit must be between 1 and 1000")
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT * FROM canonical.events
                    WHERE global_position > :after_position
                    ORDER BY global_position
                    LIMIT :limit
                    """
                ),
                {"after_position": after_position, "limit": limit},
            )
            events = tuple(_event_from_row(row) for row in result.mappings())
            high_water_result = await connection.execute(
                text("SELECT COALESCE(MAX(global_position), 0) FROM canonical.events")
            )
            high_water_position = int(high_water_result.scalar_one())
        return EventPage(events=events, high_water_position=high_water_position)


def _event_from_row(row: RowMapping) -> EventEnvelope:
    return EventEnvelope(
        event_id=UUID(str(row["event_id"])),
        stream_id=str(row["stream_id"]),
        stream_version=int(row["stream_version"]),
        event_type=str(row["event_type"]),
        schema_version=int(row["schema_version"]),
        event_time=_utc(row["event_time"]),
        received_time=_utc(row["received_time"]),
        persisted_time=_utc(row["persisted_time"]),
        correlation_id=UUID(str(row["correlation_id"])),
        causation_id=UUID(str(row["causation_id"])) if row["causation_id"] else None,
        producer=str(row["producer"]),
        producer_version=str(row["producer_version"]),
        payload=cast(dict[str, JsonValue], dict(row["payload"])),
        global_position=int(row["global_position"]),
        raw_record_id=int(row["raw_record_id"]) if row["raw_record_id"] else None,
    )


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("database timestamp is not a datetime")
    return value.astimezone(UTC)


def _parse_datetime(value: JsonValue) -> datetime:
    if not isinstance(value, str):
        raise TypeError("event datetime payload is not a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise TypeError("event payload value is not an object")
    return value


_SENSITIVE_KEY_FRAGMENTS = ("password", "api_key", "apikey", "token", "secret", "cst")


def _redact_mapping(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    redacted: dict[str, JsonValue] = {}
    for key, item in value.items():
        normalised = key.lower().replace("-", "_")
        if any(fragment in normalised for fragment in _SENSITIVE_KEY_FRAGMENTS):
            redacted[key] = "[REDACTED]"
        elif isinstance(item, dict):
            redacted[key] = _redact_mapping(item)
        elif isinstance(item, list):
            redacted[key] = [
                _redact_mapping(child) if isinstance(child, dict) else child for child in item
            ]
        else:
            redacted[key] = item
    return redacted
