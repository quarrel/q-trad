"""PostgreSQL implementation of raw capture, canonical events and projections."""

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from qtrad.application.run_reconciliation import verify_run_reconciliation_plan_hash
from qtrad.domain.events import EventEnvelope, JsonValue, to_json_value
from qtrad.domain.historical_coverage import BackfillPlan, BackfillPlanItem
from qtrad.domain.identifiers import InstrumentId, ProviderListingId, RunId
from qtrad.domain.instruments import INITIAL_INSTRUMENTS, Instrument, ProductType, ProviderListing
from qtrad.domain.market_data import BarProvenance, DataQuality, MarketBar, PriceBasis
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.domain.operations import (
    AdapterHealth,
    RunReconciliationPlan,
    RunReconciliationTarget,
)
from qtrad.domain.research import ObservationCandidate, ProjectedBar
from qtrad.domain.time import require_utc
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
        if universe_hash is not None:
            _require_sha256(universe_hash, "provider listing universe hash")
        async with self._engine.begin() as connection:
            await self._upsert_provider_listing_projection(
                connection,
                listing,
                metadata,
                universe_hash=universe_hash,
            )

    async def _upsert_provider_listing_projection(
        self,
        connection: AsyncConnection,
        listing: ProviderListing,
        metadata: Mapping[str, JsonValue],
        *,
        universe_hash: str | None,
    ) -> None:
        if listing.valid_to is None:
            await connection.execute(
                text(
                    """
                    UPDATE reference.provider_listings SET valid_to = :valid_from
                    WHERE provider = :provider AND environment = :environment
                      AND instrument_id = :instrument_id
                      AND valid_from < :valid_from
                      AND (valid_to IS NULL OR valid_to > :valid_from)
                    """
                ),
                {
                    "provider": listing.listing_id.provider,
                    "environment": listing.listing_id.environment,
                    "instrument_id": str(listing.instrument_id),
                    "valid_from": listing.valid_from,
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

        _require_sha256(universe_hash, "provider listing universe hash")
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
        effective_listing = replace(listing, valid_from=observed_at, valid_to=None)
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
            payload={"listing": effective_listing, "universe_hash": universe_hash},
        )
        try:
            persisted = await self.append(event, expected_stream_version=previous)
        except StreamVersionConflict:
            return await self.validate_provider_listing(
                listing, universe_hash=universe_hash, observed_at=observed_at
            )
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
        _require_sha256(configuration_hash, "run configuration hash")
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
            result = await connection.execute(
                text(
                    """
                    UPDATE ops.runs
                    SET status = :status,
                        finished_at = :finished_at,
                        detail = CAST(:detail AS jsonb)
                    WHERE run_id = :run_id
                      AND status = 'RUNNING'
                    RETURNING run_id
                    """
                ),
                {
                    "run_id": run_id.value,
                    "status": status,
                    "finished_at": finished_at,
                    "detail": json.dumps(detail, sort_keys=True),
                },
            )
            if result.scalar_one_or_none() != run_id.value:
                raise RuntimeError("run does not exist or is already terminal")

    async def database_name(self) -> str:
        async with self._engine.connect() as connection:
            value = (await connection.execute(text("SELECT current_database()"))).scalar_one()
        if not isinstance(value, str) or not value:
            raise TypeError("PostgreSQL current_database() did not return a non-empty string")
        return value

    async def stale_running_ingestion_runs(
        self,
        *,
        cutoff: datetime,
        environment: BrokerEnvironment,
        configuration_hash: str,
    ) -> tuple[RunReconciliationTarget, ...]:
        require_utc(cutoff, "run reconciliation cutoff")
        _require_sha256(configuration_hash, "run reconciliation configuration hash")
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT run_id, started_at
                        FROM ops.runs
                        WHERE kind = 'INGESTION'
                          AND status = 'RUNNING'
                          AND environment = :environment
                          AND configuration_hash = :configuration_hash
                          AND started_at < :cutoff
                        ORDER BY started_at, run_id
                        """
                    ),
                    {
                        "cutoff": cutoff,
                        "environment": environment.value,
                        "configuration_hash": configuration_hash,
                    },
                )
            ).mappings()
            return tuple(
                RunReconciliationTarget(
                    run_id=RunId(row["run_id"]),
                    started_at=_utc(row["started_at"]),
                )
                for row in rows
            )

    async def reconcile_stale_ingestion_runs(
        self,
        plan: RunReconciliationPlan,
        *,
        reconciled_at: datetime,
    ) -> int:
        """Atomically fail only the complete, unchanged target set in a reviewed plan."""

        verify_run_reconciliation_plan_hash(plan)
        require_utc(reconciled_at, "run reconciliation execution time")
        if reconciled_at < plan.created_at:
            raise ValueError("run reconciliation execution predates its reviewed plan")
        detail: dict[str, JsonValue] = {
            "previous_status": "RUNNING",
            "reason_code": plan.reason_code,
            "reconciliation_plan_hash": plan.plan_hash,
            "reconciled_at": _utc_text(reconciled_at),
            "finished_at_basis": plan.finished_at_basis,
            "cutoff": _utc_text(plan.cutoff),
        }
        async with self._engine.begin() as connection:
            await connection.execute(text("LOCK TABLE ops.runs IN SHARE ROW EXCLUSIVE MODE"))
            database_name = (
                await connection.execute(text("SELECT current_database()"))
            ).scalar_one()
            if database_name != plan.database_name:
                raise ValueError("run reconciliation plan targets a different database")
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT run_id, started_at
                        FROM ops.runs
                        WHERE kind = 'INGESTION'
                          AND status = 'RUNNING'
                          AND environment = :environment
                          AND configuration_hash = :configuration_hash
                          AND started_at < :cutoff
                        ORDER BY started_at, run_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "cutoff": plan.cutoff,
                        "environment": plan.environment.value,
                        "configuration_hash": plan.configuration_hash,
                    },
                )
            ).mappings()
            observed = tuple(
                RunReconciliationTarget(
                    run_id=RunId(row["run_id"]),
                    started_at=_utc(row["started_at"]),
                )
                for row in rows
            )
            if observed != plan.targets:
                raise ValueError(
                    "run reconciliation target set changed or omitted an eligible stale run"
                )
            for target in plan.targets:
                updated = await connection.execute(
                    text(
                        """
                        UPDATE ops.runs
                        SET status = :status,
                            finished_at = :finished_at,
                            detail = CAST(:detail AS jsonb)
                        WHERE run_id = :run_id
                          AND kind = 'INGESTION'
                          AND status = 'RUNNING'
                          AND environment = :environment
                          AND configuration_hash = :configuration_hash
                          AND started_at = :started_at
                          AND started_at < :cutoff
                        RETURNING run_id
                        """
                    ),
                    {
                        "run_id": target.run_id.value,
                        "status": plan.terminal_status,
                        "finished_at": plan.cutoff,
                        "detail": json.dumps(detail, sort_keys=True),
                        "environment": plan.environment.value,
                        "configuration_hash": plan.configuration_hash,
                        "started_at": target.started_at,
                        "cutoff": plan.cutoff,
                    },
                )
                if updated.scalar_one_or_none() != target.run_id.value:
                    raise RuntimeError("run reconciliation target changed while locked")
        return len(plan.targets)

    async def register_backfill_plan(
        self,
        plan: BackfillPlan,
        payload: Mapping[str, JsonValue],
    ) -> str:
        """Persist one reviewed plan and project its still-open historical coverage ranges."""

        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        plan_id = uuid5(NAMESPACE_URL, f"qtrad-backfill:{plan.plan_hash}")
        async with self._engine.begin() as connection:
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO ops.backfill_plans (
                        plan_id, plan_hash, universe_hash, status, plan, created_at
                    ) VALUES (
                        :plan_id, :plan_hash, :universe_hash, 'PLANNED',
                        CAST(:plan AS jsonb), :created_at
                    )
                    ON CONFLICT (plan_hash) DO NOTHING
                    RETURNING plan_hash
                    """
                ),
                {
                    "plan_id": plan_id,
                    "plan_hash": plan.plan_hash,
                    "universe_hash": plan.universe_hash,
                    "plan": encoded,
                    "created_at": plan.created_at,
                },
            )
            if inserted.scalar_one_or_none() is None:
                existing = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT universe_hash, plan, status
                            FROM ops.backfill_plans
                            WHERE plan_hash = :plan_hash
                            """
                            ),
                            {"plan_hash": plan.plan_hash},
                        )
                    )
                    .mappings()
                    .one()
                )
                if existing["universe_hash"] != plan.universe_hash or existing["plan"] != dict(
                    payload
                ):
                    raise RuntimeError("persisted backfill plan content conflicts with its hash")
                status = str(existing["status"])
            else:
                status = "PLANNED"
            for item in plan.items:
                for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO read_model.historical_coverage_gaps (
                                instrument_id, source_provider, source_environment,
                                source_external_id, source_listing_valid_from,
                                source_listing_metadata_version, provenance, basis, resolution,
                                interval_start, interval_end, detected_at, detected_by_plan_hash
                            ) VALUES (
                                :instrument_id, :provider, :environment, :external_id,
                                :listing_valid_from, :listing_metadata_version,
                                :provenance, :basis, :resolution,
                                :interval_start, :interval_end, :detected_at, :plan_hash
                            )
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        _coverage_parameters(plan, item, basis),
                    )
            return status

    async def claim_backfill_plan(self, plan_hash: str) -> Mapping[str, JsonValue]:
        """Atomically claim a registered or explicitly retried failed plan."""

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE ops.backfill_plans
                    SET status = 'EXECUTING', executed_at = NULL
                    WHERE plan_hash = :plan_hash AND status IN ('PLANNED', 'FAILED')
                    RETURNING plan
                    """
                ),
                {"plan_hash": plan_hash},
            )
            payload = result.scalar_one_or_none()
            if payload is not None:
                return cast(Mapping[str, JsonValue], payload)
            status = (
                await connection.execute(
                    text("SELECT status FROM ops.backfill_plans WHERE plan_hash = :plan_hash"),
                    {"plan_hash": plan_hash},
                )
            ).scalar_one_or_none()
            if status is None:
                raise RuntimeError("backfill plan is not registered")
            raise RuntimeError(f"backfill plan cannot execute from status {status}")

    async def fail_backfill_plan(self, plan_hash: str, *, executed_at: datetime) -> None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE ops.backfill_plans
                    SET status = 'FAILED', executed_at = :executed_at
                    WHERE plan_hash = :plan_hash AND status = 'EXECUTING'
                    RETURNING plan_hash
                    """
                ),
                {"plan_hash": plan_hash, "executed_at": executed_at},
            )
            if result.scalar_one_or_none() is None:
                raise RuntimeError("backfill plan was not executing when failure was recorded")

    async def complete_backfill_plan(
        self,
        plan: BackfillPlan,
        *,
        observed_points: Mapping[tuple[InstrumentId, PriceBasis], int],
        executed_at: datetime,
        allow_empty: bool = False,
    ) -> None:
        """Close a plan after recording each request result and any returned coverage."""

        async with self._engine.begin() as connection:
            for item in plan.items:
                for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID):
                    points = observed_points[(item.instrument_id, basis)]
                    if points < 0 or (points == 0 and not allow_empty):
                        raise ValueError(
                            "historical coverage requires observed points for every basis"
                        )
                    result = await connection.execute(
                        text(
                            """
                            UPDATE read_model.historical_coverage_gaps
                            SET request_completed_at = :executed_at,
                                returned_points = :observed_points,
                                covered_at = :covered_at,
                                covered_by_plan_hash = :covered_by_plan_hash,
                                observed_points = :covered_points
                            WHERE instrument_id = :instrument_id
                              AND source_provider = :provider
                              AND source_environment = :environment
                              AND source_external_id = :external_id
                              AND source_listing_valid_from = :listing_valid_from
                              AND source_listing_metadata_version = :listing_metadata_version
                              AND provenance = :provenance
                              AND basis = :basis
                                AND resolution = :resolution
                                AND interval_start = :interval_start
                                AND interval_end = :interval_end
                                AND detected_by_plan_hash = :plan_hash
                              RETURNING instrument_id
                            """
                        ),
                        {
                            **_coverage_parameters(plan, item, basis),
                            "executed_at": executed_at,
                            "observed_points": points,
                            "covered_at": executed_at if points > 0 else None,
                            "covered_by_plan_hash": plan.plan_hash if points > 0 else None,
                            "covered_points": points if points > 0 else None,
                        },
                    )
                    if result.scalar_one_or_none() is None:
                        raise RuntimeError("planned historical coverage row is missing")
            completed = await connection.execute(
                text(
                    """
                    UPDATE ops.backfill_plans
                    SET status = 'COMPLETED', executed_at = :executed_at
                    WHERE plan_hash = :plan_hash AND status = 'EXECUTING'
                    RETURNING plan_hash
                    """
                ),
                {"plan_hash": plan.plan_hash, "executed_at": executed_at},
            )
            if completed.scalar_one_or_none() is None:
                raise RuntimeError("backfill plan was not executing at completion")

    async def provider_listing_version(self, item: BackfillPlanItem) -> ProviderListing:
        rows = await self.query(
            """
            SELECT provider, environment, external_id, instrument_id, display_name,
                   product_type, currency, minimum_deal_size, price_increment,
                   valid_from, valid_to, metadata_version
            FROM reference.provider_listings
            WHERE provider = :provider AND environment = :environment
              AND external_id = :external_id AND valid_from = :valid_from
            """,
            {
                "provider": item.listing_id.provider,
                "environment": item.listing_id.environment,
                "external_id": item.listing_id.external_id,
                "valid_from": item.listing_valid_from,
            },
        )
        if len(rows) != 1:
            raise RuntimeError(f"planned provider listing version is missing: {item.listing_id}")
        listing = _provider_listing(rows[0])
        if listing.instrument_id != item.instrument_id:
            raise RuntimeError("planned provider listing belongs to another instrument")
        if listing.metadata_version != item.listing_metadata_version:
            raise RuntimeError("planned provider listing metadata version changed")
        return listing

    async def record_manifest(self, manifest: ResearchManifest) -> None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO ops.research_manifests (
                        manifest_id, manifest_sha256, created_at, schema_version,
                        universe_name, row_count,
                        minimum_event_time, maximum_event_time, content_sha256,
                        configuration_hash, files, file_sha256, metadata
                    ) VALUES (
                        :manifest_id, :manifest_sha256, :created_at, :schema_version,
                        :universe_name, :row_count,
                        :minimum_event_time, :maximum_event_time, :content_sha256,
                        :configuration_hash, CAST(:files AS jsonb),
                        CAST(:file_sha256 AS jsonb), CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (manifest_id) DO NOTHING
                    RETURNING manifest_id
                    """
                ),
                {
                    "manifest_id": manifest.manifest_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "created_at": manifest.created_at,
                    "schema_version": manifest.schema_version,
                    "universe_name": manifest.universe_name,
                    "row_count": manifest.row_count,
                    "minimum_event_time": manifest.minimum_event_time,
                    "maximum_event_time": manifest.maximum_event_time,
                    "content_sha256": manifest.content_sha256,
                    "configuration_hash": manifest.configuration_hash,
                    "files": json.dumps(manifest.files),
                    "file_sha256": (
                        json.dumps(manifest.file_sha256, sort_keys=True)
                        if manifest.schema_version == 2
                        else None
                    ),
                    "metadata": json.dumps(manifest.metadata, sort_keys=True),
                },
            )
            if result.scalar_one_or_none() is not None:
                return
            existing = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT manifest_sha256, created_at, schema_version, universe_name,
                                   row_count, minimum_event_time, maximum_event_time,
                                   content_sha256, configuration_hash, files,
                                   file_sha256, metadata
                            FROM ops.research_manifests
                            WHERE manifest_id = :manifest_id
                            """
                        ),
                        {"manifest_id": manifest.manifest_id},
                    )
                )
                .mappings()
                .one()
            )
            expected = {
                "manifest_sha256": manifest.manifest_sha256,
                "created_at": manifest.created_at,
                "schema_version": manifest.schema_version,
                "universe_name": manifest.universe_name,
                "row_count": manifest.row_count,
                "minimum_event_time": manifest.minimum_event_time,
                "maximum_event_time": manifest.maximum_event_time,
                "content_sha256": manifest.content_sha256,
                "configuration_hash": manifest.configuration_hash,
                "files": list(manifest.files),
                "file_sha256": (
                    dict(manifest.file_sha256) if manifest.schema_version == 2 else None
                ),
                "metadata": dict(manifest.metadata),
            }
            if dict(existing) != expected:
                raise RuntimeError("persisted research manifest conflicts with its identity")

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

    async def start_historical_request_usage(
        self,
        *,
        request_id: UUID,
        run_id: RunId,
        plan_hash: str,
        instrument_id: InstrumentId,
        listing_id: ProviderListingId,
        interval_start: datetime,
        interval_end: datetime,
        requested_points: int,
        started_at: datetime,
    ) -> None:
        """Append the approved maximum before a provider historical request starts."""

        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ops.historical_request_usage (
                        request_id, run_id, plan_hash, instrument_id,
                        source_provider, source_environment, source_external_id,
                        interval_start, interval_end, requested_points, started_at
                    ) VALUES (
                        :request_id, :run_id, :plan_hash, :instrument_id,
                        :source_provider, :source_environment, :source_external_id,
                        :interval_start, :interval_end, :requested_points, :started_at
                    )
                    """
                ),
                {
                    "request_id": request_id,
                    "run_id": run_id.value,
                    "plan_hash": plan_hash,
                    "instrument_id": str(instrument_id),
                    "source_provider": listing_id.provider,
                    "source_environment": listing_id.environment,
                    "source_external_id": listing_id.external_id,
                    "interval_start": interval_start,
                    "interval_end": interval_end,
                    "requested_points": requested_points,
                    "started_at": started_at,
                },
            )

    async def complete_historical_request_usage(
        self,
        request_id: UUID,
        *,
        returned_points: int,
        provider_remaining: int | None,
        completed_at: datetime,
    ) -> None:
        """Complete one usage row without overwriting an earlier attempt."""

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE ops.historical_request_usage
                    SET returned_points = :returned_points,
                        provider_remaining = :provider_remaining,
                        completed_at = :completed_at
                    WHERE request_id = :request_id AND completed_at IS NULL
                    RETURNING request_id
                    """
                ),
                {
                    "request_id": request_id,
                    "returned_points": returned_points,
                    "provider_remaining": provider_remaining,
                    "completed_at": completed_at,
                },
            )
            if result.scalar_one_or_none() is None:
                raise RuntimeError("historical request usage row is missing or already completed")

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
                      received_time, payload, payload_sha256, payload_representation,
                      adapter_version
                  ) VALUES (
                      :provider, :environment, :subscription, :deduplication_key,
                      :received_time, CAST(:payload AS jsonb), :payload_sha256,
                      :payload_representation, :adapter_version
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
                "payload_representation": int(message.payload_representation),
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
        if event.event_type == "ProviderListingValidated":
            payload = event.payload
            listing = _provider_listing_from_event(_mapping(payload["listing"]))
            universe_hash = str(payload["universe_hash"])
            _require_sha256(universe_hash, "provider listing event universe hash")
            metadata = to_json_value(listing)
            if not isinstance(metadata, dict):
                raise TypeError("provider listing did not serialise to an object")
            await self._upsert_provider_listing_projection(
                connection,
                listing,
                metadata,
                universe_hash=universe_hash,
            )
        elif event.event_type == "MarketQuoteObserved":
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
            await connection.execute(
                text("DELETE FROM reference.provider_listings WHERE universe_hash IS NOT NULL")
            )
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

    async def read_quote_derived_bar_candidates(
        self,
        *,
        instrument_ids: Sequence[InstrumentId],
        interval_start: datetime,
        interval_end: datetime,
    ) -> tuple[ObservationCandidate, ...]:
        """Read every native bar revision with a fail-closed lineage join."""

        require_utc(interval_start, "observation interval_start")
        require_utc(interval_end, "observation interval_end")
        if interval_end <= interval_start:
            raise ValueError("observation interval must be positive")
        if not instrument_ids:
            raise ValueError("observation export requires at least one instrument")
        encoded_instruments = json.dumps([str(instrument_id) for instrument_id in instrument_ids])
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT m.*, 
                           e.event_id AS canonical_event_id,
                           e.stream_id AS canonical_stream_id,
                           e.stream_version AS canonical_stream_version,
                           e.event_type AS canonical_event_type,
                           e.schema_version AS canonical_schema_version,
                           e.event_time AS canonical_event_time,
                           e.received_time AS canonical_received_time,
                           e.persisted_time AS canonical_persisted_time,
                           e.correlation_id AS canonical_correlation_id,
                           e.causation_id AS canonical_causation_id,
                           e.producer AS canonical_producer,
                           e.producer_version AS canonical_producer_version,
                           e.payload AS canonical_payload,
                           e.raw_record_id AS canonical_raw_record_id
                    FROM read_model.market_bars AS m
                    LEFT JOIN canonical.events AS e
                      ON e.global_position = m.global_position
                    WHERE m.instrument_id IN (
                        SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
                    )
                      AND m.interval_start >= :interval_start
                      AND m.interval_end <= :interval_end
                      AND m.provenance = 'QUOTE_DERIVED'
                    ORDER BY m.instrument_id, m.basis, m.interval_start,
                             m.source_provider, m.source_environment,
                             m.source_external_id, m.revision, m.global_position
                    """
                ),
                {
                    "instrument_ids": encoded_instruments,
                    "interval_start": interval_start,
                    "interval_end": interval_end,
                },
            )
            candidates: list[ObservationCandidate] = []
            for row in result.mappings():
                projection = ProjectedBar(
                    bar=_market_bar_from_projection(dict(row)),
                    global_position=int(str(row["global_position"])),
                )
                event = (
                    None
                    if row["canonical_event_id"] is None
                    else _event_from_joined_row(dict(row))
                )
                candidates.append(ObservationCandidate(projection=projection, event=event))
        return tuple(candidates)


def _event_from_joined_row(row: Mapping[str, object]) -> EventEnvelope:
    payload = row["canonical_payload"]
    if not isinstance(payload, dict):
        raise TypeError("canonical event payload is not an object")
    return EventEnvelope(
        event_id=UUID(str(row["canonical_event_id"])),
        stream_id=str(row["canonical_stream_id"]),
        stream_version=int(str(row["canonical_stream_version"])),
        event_type=str(row["canonical_event_type"]),
        schema_version=int(str(row["canonical_schema_version"])),
        event_time=_utc(row["canonical_event_time"]),
        received_time=_utc(row["canonical_received_time"]),
        persisted_time=_utc(row["canonical_persisted_time"]),
        correlation_id=UUID(str(row["canonical_correlation_id"])),
        causation_id=(
            UUID(str(row["canonical_causation_id"]))
            if row["canonical_causation_id"]
            else None
        ),
        producer=str(row["canonical_producer"]),
        producer_version=str(row["canonical_producer_version"]),
        payload=cast(dict[str, JsonValue], payload),
        global_position=int(str(row["global_position"])),
        raw_record_id=(
            int(str(row["canonical_raw_record_id"]))
            if row["canonical_raw_record_id"]
            else None
        ),
    )


def _market_bar_from_projection(row: Mapping[str, object]) -> MarketBar:
    return MarketBar(
        instrument_id=InstrumentId(str(row["instrument_id"])),
        basis=PriceBasis(str(row["basis"])),
        interval_start=_utc(row["interval_start"]),
        interval_end=_utc(row["interval_end"]),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        sample_count=int(str(row["sample_count"])),
        revision=int(str(row["revision"])),
        provenance=BarProvenance(str(row["provenance"])),
        quality=DataQuality(str(row["quality"])),
        source_listing_id=ProviderListingId(
            provider=str(row["source_provider"]),
            environment=str(row["source_environment"]),
            external_id=str(row["source_external_id"]),
        ),
    )


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


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be lower-case SHA-256")


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("database timestamp is not a datetime")
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    require_utc(value, "database evidence time")
    return value.isoformat().replace("+00:00", "Z")


def _provider_listing(row: Mapping[str, object]) -> ProviderListing:
    return ProviderListing(
        listing_id=ProviderListingId(
            provider=str(row["provider"]),
            environment=str(row["environment"]),
            external_id=str(row["external_id"]),
        ),
        instrument_id=InstrumentId(str(row["instrument_id"])),
        display_name=str(row["display_name"]),
        product_type=ProductType(str(row["product_type"])),
        currency=str(row["currency"]),
        minimum_deal_size=cast(Decimal, row["minimum_deal_size"]),
        price_increment=cast(Decimal | None, row["price_increment"]),
        valid_from=_utc(row["valid_from"]),
        valid_to=_utc(row["valid_to"]) if row["valid_to"] else None,
        metadata_version=str(row["metadata_version"]),
    )


def _provider_listing_from_event(value: Mapping[str, JsonValue]) -> ProviderListing:
    listing_id = _mapping(value["listing_id"])
    price_increment = value["price_increment"]
    valid_to = value["valid_to"]
    return ProviderListing(
        listing_id=ProviderListingId(
            provider=str(listing_id["provider"]),
            environment=str(listing_id["environment"]),
            external_id=str(listing_id["external_id"]),
        ),
        instrument_id=InstrumentId(str(value["instrument_id"])),
        display_name=str(value["display_name"]),
        product_type=ProductType(str(value["product_type"])),
        currency=str(value["currency"]),
        minimum_deal_size=Decimal(str(value["minimum_deal_size"])),
        price_increment=(Decimal(str(price_increment)) if price_increment is not None else None),
        valid_from=_parse_datetime(value["valid_from"]),
        valid_to=_parse_datetime(valid_to) if valid_to is not None else None,
        metadata_version=str(value["metadata_version"]),
        economics=_mapping(value["economics"]),
    )


def _coverage_parameters(
    plan: BackfillPlan,
    item: BackfillPlanItem,
    basis: PriceBasis,
) -> dict[str, object]:
    return {
        "instrument_id": str(item.instrument_id),
        "provider": item.listing_id.provider,
        "environment": item.listing_id.environment,
        "external_id": item.listing_id.external_id,
        "listing_valid_from": item.listing_valid_from,
        "listing_metadata_version": item.listing_metadata_version,
        "provenance": BarProvenance.IG_HISTORICAL.value,
        "basis": basis.value,
        "resolution": plan.resolution.value,
        "interval_start": plan.start,
        "interval_end": plan.end,
        "detected_at": plan.created_at,
        "plan_hash": plan.plan_hash,
    }


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
