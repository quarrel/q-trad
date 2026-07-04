"""q-trad command-line entry point."""

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from qtrad.adapters.clock import SystemClock
from qtrad.adapters.ig.market_data import IgDemoConfig, IgDemoMarketDataAdapter
from qtrad.adapters.parquet.store import ParquetResearchStore
from qtrad.adapters.postgres.store import PostgresAuditStore, StreamVersionConflict
from qtrad.api.app import create_app
from qtrad.application.ingestion import IngestionService
from qtrad.application.quota import points_per_instrument
from qtrad.application.replay import semantic_bar_hash
from qtrad.domain.events import EventEnvelope, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import INITIAL_INSTRUMENTS
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    PriceBasis,
)
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.ports.clock import Clock
from qtrad.ports.market_data import BackfillRequest
from qtrad.runtime.logging import configure_logging
from qtrad.runtime.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qtrad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db = subparsers.add_parser("db", help="database operations")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("upgrade", help="apply migrations and seed instruments")

    instruments = subparsers.add_parser("instruments", help="instrument operations")
    instrument_sub = instruments.add_subparsers(dest="instrument_command", required=True)
    instrument_sub.add_parser("sync", help="discover and persist IG demo listings")

    ingest = subparsers.add_parser("ingest", help="run IG demo ingestion")
    ingest.add_argument("--environment", choices=["ig-demo"], default="ig-demo")
    ingest.add_argument("--max-seconds", type=float)
    ingest.add_argument("--force-reconnect-after-seconds", type=float)

    backfill = subparsers.add_parser("backfill", help="bounded IG demo backfill")
    backfill.add_argument("--max-points", type=int, default=1000)
    backfill.add_argument("--remaining-allowance", type=int, default=10_000)

    research = subparsers.add_parser("research", help="research-store operations")
    research_sub = research.add_subparsers(dest="research_command", required=True)
    research_sub.add_parser("export", help="export latest bar revisions to Parquet")

    replay = subparsers.add_parser("replay", help="verify a research manifest")
    replay.add_argument("--manifest", required=True)

    projections = subparsers.add_parser("projections", help="projection operations")
    projection_sub = projections.add_subparsers(dest="projection_command", required=True)
    projection_sub.add_parser("rebuild", help="rebuild projections from events")

    api = subparsers.add_parser("api", help="run the read-only operator API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings()
    clock = SystemClock()
    configure_logging(settings.log_level)

    if args.command == "db" and args.db_command == "upgrade":
        _upgrade_database(settings)
        asyncio.run(_seed(settings))
    elif args.command == "instruments" and args.instrument_command == "sync":
        asyncio.run(_sync_instruments(settings, clock))
    elif args.command == "ingest":
        asyncio.run(
            _ingest(
                settings,
                clock,
                maximum_seconds=args.max_seconds,
                force_reconnect_after_seconds=args.force_reconnect_after_seconds,
            )
        )
    elif args.command == "backfill":
        asyncio.run(
            _backfill(
                settings,
                clock,
                maximum_points=args.max_points,
                remaining_allowance=args.remaining_allowance,
            )
        )
    elif args.command == "research" and args.research_command == "export":
        asyncio.run(_export(settings, clock))
    elif args.command == "replay":
        asyncio.run(_replay(settings, clock, Path(args.manifest)))
    elif args.command == "projections" and args.projection_command == "rebuild":
        asyncio.run(_rebuild(settings))
    elif args.command == "api":
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    else:
        raise RuntimeError("unhandled command")


def _upgrade_database(settings: Settings) -> None:
    os.environ["QTRAD_MIGRATION_DATABASE_URL"] = settings.migration_database_url
    command.upgrade(Config("alembic.ini"), "head")


def _engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def _ig_adapter(settings: Settings, clock: Clock) -> IgDemoMarketDataAdapter:
    username, password, api_key, account_id = settings.require_ig_credentials()
    return IgDemoMarketDataAdapter(
        IgDemoConfig(
            username=username,
            password=password,
            api_key=api_key,
            account_id=account_id,
        ),
        clock,
    )


async def _seed(settings: Settings) -> None:
    engine = _engine(settings)
    try:
        await PostgresAuditStore(engine).seed_instruments()
    finally:
        await engine.dispose()


async def _sync_instruments(settings: Settings, clock: Clock) -> None:
    engine = _engine(settings)
    adapter = _ig_adapter(settings, clock)
    store = PostgresAuditStore(engine)
    try:
        await store.seed_instruments()
        await adapter.connect()
        listings = await adapter.discover_listings(
            [instrument.instrument_id for instrument in INITIAL_INSTRUMENTS]
        )
        for listing in listings:
            metadata = to_json_value(listing)
            if not isinstance(metadata, dict):
                raise TypeError("listing metadata did not serialise to an object")
            await store.upsert_provider_listing(listing, metadata)
        print(json.dumps({"listings": [str(item.listing_id) for item in listings]}))
    finally:
        await adapter.disconnect()
        await engine.dispose()


async def _ingest(
    settings: Settings,
    clock: Clock,
    *,
    maximum_seconds: float | None = None,
    force_reconnect_after_seconds: float | None = None,
) -> None:
    if maximum_seconds is not None and maximum_seconds <= 0:
        raise ValueError("maximum seconds must be positive")
    if force_reconnect_after_seconds is not None:
        if force_reconnect_after_seconds <= 0:
            raise ValueError("forced reconnect interval must be positive")
        if maximum_seconds is not None and force_reconnect_after_seconds >= maximum_seconds:
            raise ValueError("forced reconnect must occur before maximum seconds")
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    adapter = _ig_adapter(settings, clock)
    service = IngestionService(store, producer="ig-demo-adapter", producer_version="0.1.0")
    run_id = await store.start_run(
        kind=RunKind.INGESTION,
        environment=BrokerEnvironment.IG_DEMO,
        configuration_hash=_configuration_hash(),
        started_at=clock.now(),
    )
    terminal_status = "FAILED"
    reconnect_task: asyncio.Task[None] | None = None
    reconnect_error: Exception | None = None
    try:
        listings = await store.active_provider_listings()
        if len(listings) != len(INITIAL_INSTRUMENTS):
            raise RuntimeError("run 'qtrad instruments sync' before ingestion")
        await adapter.connect()
        await adapter.subscribe(listings)
        await store.record_adapter_health(await adapter.health())

        async def force_reconnect() -> None:
            assert force_reconnect_after_seconds is not None
            await asyncio.sleep(force_reconnect_after_seconds)
            await adapter.force_reconnect()
            await store.record_adapter_health(await adapter.health())

        if force_reconnect_after_seconds is not None:
            reconnect_task = asyncio.create_task(force_reconnect())

        async def consume() -> None:
            async for record in adapter.records():
                await service.process(record)
                await service.advance_bars(clock.now())
                await store.record_adapter_health(await adapter.health())

        if maximum_seconds is None:
            await consume()
            terminal_status = "COMPLETED"
        else:
            try:
                async with asyncio.timeout(maximum_seconds):
                    await consume()
            except TimeoutError:
                terminal_status = "STOPPED"
            else:
                terminal_status = "COMPLETED"
    except (KeyboardInterrupt, asyncio.CancelledError):
        terminal_status = "STOPPED"
    finally:
        if reconnect_task is not None:
            if reconnect_task.done():
                try:
                    reconnect_task.result()
                except Exception as error:
                    reconnect_error = error
                    terminal_status = "FAILED"
            else:
                reconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reconnect_task
        await adapter.disconnect()
        final_health = await adapter.health()
        await store.record_adapter_health(final_health)
        await store.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={
                "adapter_health": final_health.detail,
                "forced_reconnect": force_reconnect_after_seconds is not None,
            },
        )
        await engine.dispose()
        if reconnect_error is not None:
            raise reconnect_error


async def _backfill(
    settings: Settings,
    clock: Clock,
    *,
    maximum_points: int,
    remaining_allowance: int,
) -> None:
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    adapter = _ig_adapter(settings, clock)
    run_id = await store.start_run(
        kind=RunKind.BACKFILL,
        environment=BrokerEnvironment.IG_DEMO,
        configuration_hash=_configuration_hash(),
        started_at=clock.now(),
    )
    terminal_status = "FAILED"
    written = 0
    received_points: set[tuple[str, datetime]] = set()
    try:
        listings = await store.active_provider_listings()
        if len(listings) != len(INITIAL_INSTRUMENTS):
            raise RuntimeError("run 'qtrad instruments sync' before backfill")
        points = points_per_instrument(
            remaining_allowance=remaining_allowance,
            instrument_count=len(listings),
            maximum_points=maximum_points,
        )
        if points <= 0:
            raise RuntimeError("historical allowance reserve leaves no points to request")
        await store.record_quota_state(
            provider="ig",
            environment="demo",
            allowance_name="historical_points_weekly_operator_reported",
            remaining=remaining_allowance,
            observed_at=clock.now(),
        )
        await adapter.connect()
        end = clock.now().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=points)
        for listing in listings:
            request = BackfillRequest(
                instrument_id=listing.instrument_id,
                listing=listing,
                start=start,
                end=end,
                maximum_points=points,
            )
            async for bar in adapter.backfill(request):
                received_points.add((str(bar.source_listing_id), bar.interval_start))
                event = await _append_bar(store, bar, received_time=clock.now())
                if event is not None:
                    written += 1
        provider_remaining = adapter.historical_allowance_remaining
        if provider_remaining is not None:
            await store.record_quota_state(
                provider="ig",
                environment="demo",
                allowance_name="historical_points_weekly_provider_reported",
                remaining=provider_remaining,
                observed_at=clock.now(),
            )
        terminal_status = "COMPLETED"
        print(
            json.dumps(
                {
                    "per_instrument_points": points,
                    "points_received": len(received_points),
                    "bars_written": written,
                    "provider_remaining_allowance": provider_remaining,
                }
            )
        )
    finally:
        await adapter.disconnect()
        await store.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={
                "bars_written": written,
                "points_received": len(received_points),
                "provider_remaining_allowance": adapter.historical_allowance_remaining,
            },
        )
        await engine.dispose()


async def _append_bar(
    store: PostgresAuditStore, bar: MarketBar, *, received_time: datetime
) -> EventEnvelope | None:
    source = bar.source_listing_id
    stream_id = (
        f"historical-bar:{bar.instrument_id}:{bar.basis}:"
        f"{source.provider}:{source.environment}:{source.external_id}:"
        f"{bar.interval_start.isoformat()}"
    )
    previous = await store.latest_stream_version(stream_id)
    if previous:
        rows = await store.query(
            """
            SELECT payload FROM canonical.events
            WHERE stream_id = :stream_id AND stream_version = 1
            """,
            {"stream_id": stream_id},
        )
        payload = to_json_value(bar)
        if len(rows) != 1 or rows[0]["payload"] != payload:
            raise RuntimeError(f"historical bar conflict for {stream_id}")
        return None
    event = EventEnvelope.create(
        stream_id=stream_id,
        stream_version=1,
        event_type="MarketBarClosed",
        event_time=bar.interval_end,
        received_time=received_time,
        producer="ig-demo-backfill",
        producer_version="0.1.0",
        payload=bar,
    )
    try:
        return await store.append(event, expected_stream_version=0)
    except StreamVersionConflict:
        return await _append_bar(store, bar, received_time=received_time)


async def _export(settings: Settings, clock: Clock) -> None:
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    research = ParquetResearchStore(settings.research_root, clock)
    run_id = await store.start_run(
        kind=RunKind.EXPORT,
        environment=BrokerEnvironment.NONE,
        configuration_hash=_configuration_hash(),
        started_at=clock.now(),
    )
    terminal_status = "FAILED"
    try:
        rows = await store.query(
            """
                SELECT DISTINCT ON (
                    instrument_id, basis, interval_start, provenance,
                    source_provider, source_environment, source_external_id
                )
                    * FROM read_model.market_bars
                ORDER BY instrument_id, basis, interval_start, provenance,
                         source_provider, source_environment, source_external_id,
                         revision DESC
            """
        )
        bars = tuple(_bar_from_projection(row) for row in rows)
        configuration_hash = _configuration_hash()
        manifest = await research.write_bars(
            bars,
            configuration_hash=configuration_hash,
            metadata={"universe_size": len(INITIAL_INSTRUMENTS), "gap_count": 0},
        )
        await store.record_manifest(manifest)
        terminal_status = "COMPLETED"
        print(json.dumps({"manifest_id": manifest.manifest_id, "rows": manifest.row_count}))
    finally:
        await store.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={},
        )
        await engine.dispose()


async def _replay(settings: Settings, clock: Clock, manifest_path: Path) -> None:
    manifest_id = manifest_path.stem
    root = (
        manifest_path.parent.parent
        if manifest_path.parent.name == "manifests"
        else settings.research_root
    )
    store = ParquetResearchStore(root, clock)
    engine = _engine(settings)
    audit = PostgresAuditStore(engine)
    run_id = await audit.start_run(
        kind=RunKind.REPLAY,
        environment=BrokerEnvironment.NONE,
        configuration_hash=_configuration_hash(),
        started_at=clock.now(),
    )
    terminal_status = "FAILED"
    try:
        first = tuple(await store.read_bars(manifest_id))
        second = tuple(await store.read_bars(manifest_id))
        first_hash = semantic_bar_hash(first)
        second_hash = semantic_bar_hash(second)
        if first_hash != second_hash:
            raise RuntimeError("replay hashes differ")
        terminal_status = "COMPLETED"
        print(json.dumps({"manifest_id": manifest_id, "rows": len(first), "sha256": first_hash}))
    finally:
        await audit.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={"manifest_id": manifest_id},
        )
        await engine.dispose()


async def _rebuild(settings: Settings) -> None:
    engine = _engine(settings)
    try:
        count = await PostgresAuditStore(engine).rebuild_projections()
        print(json.dumps({"events_projected": count}))
    finally:
        await engine.dispose()


def _bar_from_projection(row: dict[str, object]) -> MarketBar:
    return MarketBar(
        instrument_id=InstrumentId(str(row["instrument_id"])),
        basis=PriceBasis(str(row["basis"])),
        interval_start=_as_utc(row["interval_start"]),
        interval_end=_as_utc(row["interval_end"]),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        sample_count=int(str(row["sample_count"])),
        revision=int(str(row["revision"])),
        provenance=BarProvenance(str(row["provenance"])),
        quality=DataQuality(str(row["quality"])),
        source_listing_id=ProviderListingId(
            str(row["source_provider"]),
            str(row["source_environment"]),
            str(row["source_external_id"]),
        ),
    )


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected datetime")
    return value.astimezone(UTC)


def _configuration_hash() -> str:
    import hashlib

    values = [str(instrument.instrument_id) for instrument in INITIAL_INSTRUMENTS]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


if __name__ == "__main__":
    main()
