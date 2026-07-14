"""q-trad command-line entry point."""

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from qtrad import __version__
from qtrad.adapters.clock import SystemClock
from qtrad.adapters.ig.market_data import IgDemoConfig, IgDemoMarketDataAdapter
from qtrad.adapters.parquet.store import ParquetResearchStore
from qtrad.adapters.postgres.storage_measurement import PostgresStorageInspector
from qtrad.adapters.postgres.store import PostgresAuditStore, StreamVersionConflict
from qtrad.api.app import create_app
from qtrad.application.backfill_planning import (
    backfill_plan_payload,
    backfill_requests,
    build_backfill_plan,
)
from qtrad.application.capture_feed import CaptureFeedCursor, advance_capture_feed_cursor
from qtrad.application.ingestion import IngestionService
from qtrad.application.listing_review import build_listing_review_manifest
from qtrad.application.replay import semantic_bar_hash
from qtrad.application.universe_promotion import promote_reviewed_universe
from qtrad.domain.events import EventEnvelope, to_json_value
from qtrad.domain.historical_coverage import BackfillPlan
from qtrad.domain.identifiers import InstrumentId, ProviderListingId, RunId
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    PriceBasis,
)
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.ports.capture_feed import CaptureFeedIdentity
from qtrad.ports.clock import Clock
from qtrad.ports.market_data import BackfillRequest
from qtrad.runtime.backfill_plan import (
    decode_backfill_plan,
    load_backfill_plan,
    write_backfill_plan,
)
from qtrad.runtime.capture_feed import HttpCaptureFeedClient, load_capture_feed_page
from qtrad.runtime.logging import configure_logging
from qtrad.runtime.research_export import research_export_metadata
from qtrad.runtime.settings import Settings
from qtrad.runtime.storage_measurement import (
    build_storage_snapshot,
    compare_storage_snapshots,
    load_storage_snapshot,
    write_storage_snapshot,
)
from qtrad.runtime.universe import (
    CaptureCandidates,
    CaptureUniverse,
    load_capture_candidates,
    load_capture_universe,
    render_capture_universe_promotion,
)
from qtrad.runtime.universe_promotion import (
    load_explicit_selection_set,
    load_listing_review_evidence,
)


def _utc_minute_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be an ISO-8601 UTC minute") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("timestamp must be an ISO-8601 UTC minute")
    if parsed.second or parsed.microsecond:
        raise argparse.ArgumentTypeError("timestamp must be an ISO-8601 UTC minute")
    return parsed


def _require_sha256_argument(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be lower-case SHA-256")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qtrad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db = subparsers.add_parser("db", help="database operations")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("upgrade", help="apply migrations and seed instruments")

    instruments = subparsers.add_parser("instruments", help="instrument operations")
    instrument_sub = instruments.add_subparsers(dest="instrument_command", required=True)
    instrument_sub.add_parser("sync", help="discover and persist IG demo listings")
    review = instrument_sub.add_parser(
        "review", help="emit bounded IG demo listing candidates without selecting one"
    )
    review.add_argument(
        "--catalogue",
        type=Path,
        default=Path("config/capture-v2-candidates.toml"),
    )
    review.add_argument("--output", type=Path)
    promote = instrument_sub.add_parser(
        "promote", help="verify explicit reviewed selections and emit an undeployed universe"
    )
    promote.add_argument("--catalogue", type=Path, required=True)
    promote.add_argument("--review", type=Path, required=True)
    promote.add_argument("--selections", type=Path, required=True)
    promote.add_argument("--release-name", required=True)
    promote.add_argument("--output", type=Path, required=True)

    ingest = subparsers.add_parser("ingest", help="run IG demo ingestion")
    ingest.add_argument("--environment", choices=["ig-demo"], default="ig-demo")
    ingest.add_argument("--max-seconds", type=float)
    ingest.add_argument("--force-reconnect-after-seconds", type=float)

    backfill = subparsers.add_parser("backfill", help="reviewed historical-coverage operations")
    backfill_sub = backfill.add_subparsers(dest="backfill_command", required=True)
    backfill_plan = backfill_sub.add_parser("plan", help="create an explicit non-overwriting plan")
    backfill_plan.add_argument("--universe", type=Path, required=True)
    backfill_plan.add_argument("--start", type=_utc_minute_argument, required=True)
    backfill_plan.add_argument("--end", type=_utc_minute_argument, required=True)
    backfill_plan.add_argument("--remaining-allowance", type=int, required=True)
    backfill_plan.add_argument("--output", type=Path, required=True)
    backfill_plan.add_argument("instruments", type=InstrumentId, nargs="+")
    backfill_register = backfill_sub.add_parser(
        "register", help="persist a reviewed plan and its coverage gaps"
    )
    backfill_register.add_argument("--plan", type=Path, required=True)
    backfill_register.add_argument("--confirm-plan-hash", required=True)
    backfill_execute = backfill_sub.add_parser(
        "execute", help="execute one registered plan by its exact hash"
    )
    backfill_execute.add_argument("--plan-hash", required=True)

    research = subparsers.add_parser("research", help="research-store operations")
    research_sub = research.add_subparsers(dest="research_command", required=True)
    research_export = research_sub.add_parser(
        "export", help="export latest bar revisions to an immutable Parquet manifest"
    )
    research_export.add_argument("--universe", type=Path, required=True)
    research_export.add_argument("--start", type=_utc_minute_argument, required=True)
    research_export.add_argument("--end", type=_utc_minute_argument, required=True)

    replay = subparsers.add_parser("replay", help="verify a research manifest")
    replay.add_argument("--manifest", type=Path, required=True)

    projections = subparsers.add_parser("projections", help="projection operations")
    projection_sub = projections.add_subparsers(dest="projection_command", required=True)
    projection_sub.add_parser("rebuild", help="rebuild projections from events")

    storage = subparsers.add_parser("storage", help="read-only capture-storage measurement")
    storage_sub = storage.add_subparsers(dest="storage_command", required=True)
    storage_snapshot = storage_sub.add_parser(
        "snapshot", help="write one hash-verified physical-storage observation"
    )
    storage_snapshot.add_argument("--universe", type=Path, required=True)
    storage_snapshot.add_argument("--output", type=Path, required=True)
    storage_compare = storage_sub.add_parser(
        "compare", help="compare two storage observations without database access"
    )
    storage_compare.add_argument("before", type=Path)
    storage_compare.add_argument("after", type=Path)

    feed = subparsers.add_parser("feed", help="capture-feed contract operations")
    feed_sub = feed.add_subparsers(dest="feed_command", required=True)
    feed_verify = feed_sub.add_parser("verify", help="verify saved feed pages without network I/O")
    feed_verify.add_argument("--source-id", required=True)
    feed_verify.add_argument("--universe-name", required=True)
    feed_verify.add_argument("--configuration-hash", required=True)
    feed_verify.add_argument("--after-position", type=int, default=0)
    feed_verify.add_argument("pages", type=Path, nargs="+")
    feed_probe = feed_sub.add_parser(
        "probe", help="fetch and validate one bounded page through a loopback tunnel"
    )
    feed_probe.add_argument("--endpoint", required=True)
    feed_probe.add_argument("--source-id", required=True)
    feed_probe.add_argument("--universe-name", required=True)
    feed_probe.add_argument("--configuration-hash", required=True)
    feed_probe.add_argument("--after-position", type=int, default=0)
    feed_probe.add_argument("--limit", type=int, default=500)

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
    elif args.command == "instruments" and args.instrument_command == "review":
        asyncio.run(
            _review_instruments(
                settings,
                clock,
                catalogue_path=args.catalogue,
                output_path=args.output,
            )
        )
    elif args.command == "instruments" and args.instrument_command == "promote":
        _promote_universe(
            clock,
            catalogue_path=args.catalogue,
            review_path=args.review,
            selections_path=args.selections,
            release_name=args.release_name,
            output_path=args.output,
        )
    elif args.command == "ingest":
        asyncio.run(
            _ingest(
                settings,
                clock,
                maximum_seconds=args.max_seconds,
                force_reconnect_after_seconds=args.force_reconnect_after_seconds,
            )
        )
    elif args.command == "backfill" and args.backfill_command == "plan":
        asyncio.run(
            _plan_backfill(
                settings,
                clock,
                universe_path=args.universe,
                start=args.start,
                end=args.end,
                remaining_allowance=args.remaining_allowance,
                output_path=args.output,
                instrument_ids=args.instruments,
            )
        )
    elif args.command == "backfill" and args.backfill_command == "register":
        asyncio.run(
            _register_backfill(
                settings,
                plan_path=args.plan,
                confirmed_plan_hash=args.confirm_plan_hash,
            )
        )
    elif args.command == "backfill" and args.backfill_command == "execute":
        asyncio.run(_execute_backfill(settings, clock, plan_hash=args.plan_hash))
    elif args.command == "research" and args.research_command == "export":
        asyncio.run(
            _export(
                settings,
                clock,
                universe_path=args.universe,
                start=args.start,
                end=args.end,
            )
        )
    elif args.command == "replay":
        asyncio.run(_replay(settings, clock, args.manifest))
    elif args.command == "projections" and args.projection_command == "rebuild":
        asyncio.run(_rebuild(settings))
    elif args.command == "storage" and args.storage_command == "snapshot":
        asyncio.run(
            _storage_snapshot(
                settings,
                universe_path=args.universe,
                output_path=args.output,
            )
        )
    elif args.command == "storage" and args.storage_command == "compare":
        _compare_storage_snapshots(args.before, args.after)
    elif args.command == "feed" and args.feed_command == "verify":
        _verify_capture_feed_pages(
            source_id=args.source_id,
            universe_name=args.universe_name,
            configuration_hash=args.configuration_hash,
            after_position=args.after_position,
            page_paths=args.pages,
        )
    elif args.command == "feed" and args.feed_command == "probe":
        asyncio.run(
            _probe_capture_feed(
                endpoint=args.endpoint,
                source_id=args.source_id,
                universe_name=args.universe_name,
                configuration_hash=args.configuration_hash,
                after_position=args.after_position,
                limit=args.limit,
            )
        )
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
    universe = _capture_universe(settings)
    return IgDemoMarketDataAdapter(
        IgDemoConfig(
            username=username,
            password=password,
            api_key=api_key,
            account_id=account_id,
        ),
        clock,
        instruments_by_id=universe.instruments_by_id,
        preferred_epics=universe.preferred_epics,
    )


def _ig_review_adapter(
    settings: Settings, clock: Clock, candidates: CaptureCandidates
) -> IgDemoMarketDataAdapter:
    username, password, api_key, account_id = settings.require_ig_credentials()
    return IgDemoMarketDataAdapter(
        IgDemoConfig(
            username=username,
            password=password,
            api_key=api_key,
            account_id=account_id,
        ),
        clock,
        instruments_by_id={
            instrument.instrument_id: instrument for instrument in candidates.instruments
        },
        preferred_epics={},
    )


def _ig_backfill_adapter(settings: Settings, clock: Clock) -> IgDemoMarketDataAdapter:
    username, password, api_key, account_id = settings.require_ig_credentials()
    return IgDemoMarketDataAdapter(
        IgDemoConfig(
            username=username,
            password=password,
            api_key=api_key,
            account_id=account_id,
        ),
        clock,
        instruments_by_id={},
        preferred_epics={},
    )


async def _seed(settings: Settings) -> None:
    engine = _engine(settings)
    try:
        await PostgresAuditStore(engine).seed_instruments(_capture_universe(settings).instruments)
    finally:
        await engine.dispose()


async def _sync_instruments(settings: Settings, clock: Clock) -> None:
    engine = _engine(settings)
    adapter = _ig_adapter(settings, clock)
    store = PostgresAuditStore(engine)
    try:
        universe = _capture_universe(settings)
        await store.seed_instruments(universe.instruments)
        await adapter.connect()
        listings = await adapter.discover_listings(
            [instrument.instrument_id for instrument in universe.instruments]
        )
        for listing in listings:
            await store.validate_provider_listing(
                listing, universe_hash=universe.configuration_hash, observed_at=clock.now()
            )
        print(json.dumps({"listings": [str(item.listing_id) for item in listings]}))
    finally:
        await adapter.disconnect()
        await engine.dispose()


async def _review_instruments(
    settings: Settings,
    clock: Clock,
    *,
    catalogue_path: Path,
    output_path: Path | None,
) -> None:
    if output_path is not None:
        if output_path.exists():
            raise FileExistsError(f"listing review output already exists: {output_path}")
        if not output_path.parent.is_dir():
            raise FileNotFoundError(
                f"listing review output directory does not exist: {output_path.parent}"
            )
    candidates = load_capture_candidates(catalogue_path)
    adapter = _ig_review_adapter(settings, clock, candidates)
    try:
        await adapter.connect()
        reviews = await adapter.review_listings(
            [instrument.instrument_id for instrument in candidates.instruments]
        )
        manifest = build_listing_review_manifest(
            catalogue_name=candidates.name,
            catalogue_hash=candidates.configuration_hash,
            instruments=candidates.instruments,
            reviews=reviews,
            observed_at=clock.now(),
        )
        encoded = json.dumps(manifest.as_json_value(), sort_keys=True, indent=2) + "\n"
        if output_path is None:
            print(encoded, end="")
        else:
            with output_path.open("x", encoding="utf-8") as output:
                output.write(encoded)
            print(
                json.dumps(
                    {
                        "output": str(output_path),
                        "review_hash": manifest.review_hash,
                    },
                    sort_keys=True,
                )
            )
    finally:
        await adapter.disconnect()


def _promote_universe(
    clock: Clock,
    *,
    catalogue_path: Path,
    review_path: Path,
    selections_path: Path,
    release_name: str,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise FileExistsError(f"capture universe output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"capture universe output directory does not exist: {output_path.parent}"
        )
    candidates = load_capture_candidates(catalogue_path)
    evidence = load_listing_review_evidence(review_path, candidates.instruments)
    selection_set = load_explicit_selection_set(selections_path)
    promotion = promote_reviewed_universe(
        release_name=release_name,
        catalogue_name=candidates.name,
        catalogue_hash=candidates.configuration_hash,
        instruments=candidates.instruments,
        review_catalogue_name=evidence.catalogue_name,
        review_catalogue_hash=evidence.catalogue_hash,
        review_hash=evidence.review_hash,
        reviews=evidence.reviews,
        selection_set=selection_set,
        promoted_at=clock.now(),
    )
    rendered, universe = render_capture_universe_promotion(promotion)
    with output_path.open("x", encoding="utf-8") as output:
        output.write(rendered)
    print(
        json.dumps(
            {
                "configuration_hash": universe.configuration_hash,
                "output": str(output_path),
                "selection_hash": promotion.selection_hash,
                "source_review_hash": promotion.source_review_hash,
            },
            sort_keys=True,
        )
    )


def _verify_capture_feed_pages(
    *,
    source_id: str,
    universe_name: str,
    configuration_hash: str,
    after_position: int,
    page_paths: Sequence[Path],
) -> None:
    identity = CaptureFeedIdentity(
        feed_schema_version=1,
        source_id=source_id,
        universe_name=universe_name,
        configuration_hash=configuration_hash,
    )
    cursor = CaptureFeedCursor.initial(identity, after_position=after_position)
    event_count = 0
    for page_path in page_paths:
        page = load_capture_feed_page(page_path)
        cursor = advance_capture_feed_cursor(cursor, page)
        event_count += len(page.events)
    print(
        json.dumps(
            {
                "caught_up": cursor.position == cursor.observed_high_water_position,
                "event_count": event_count,
                "page_count": len(page_paths),
                "position": cursor.position,
                "source_id": cursor.identity.source_id,
                "universe_name": cursor.identity.universe_name,
                "configuration_hash": cursor.identity.configuration_hash,
                "observed_high_water_position": cursor.observed_high_water_position,
            },
            sort_keys=True,
        )
    )


async def _probe_capture_feed(
    *,
    endpoint: str,
    source_id: str,
    universe_name: str,
    configuration_hash: str,
    after_position: int,
    limit: int,
) -> None:
    identity = CaptureFeedIdentity(
        feed_schema_version=1,
        source_id=source_id,
        universe_name=universe_name,
        configuration_hash=configuration_hash,
    )
    cursor = CaptureFeedCursor.initial(identity, after_position=after_position)
    async with HttpCaptureFeedClient(endpoint) as client:
        page = await client.fetch_page(after_position=after_position, limit=limit)
    candidate_cursor = advance_capture_feed_cursor(cursor, page)
    print(
        json.dumps(
            {
                "caught_up": candidate_cursor.position
                == candidate_cursor.observed_high_water_position,
                "event_count": len(page.events),
                "next_position": candidate_cursor.position,
                "observed_high_water_position": candidate_cursor.observed_high_water_position,
                "source_id": candidate_cursor.identity.source_id,
                "universe_name": candidate_cursor.identity.universe_name,
                "configuration_hash": candidate_cursor.identity.configuration_hash,
                "cursor_persisted": False,
            },
            sort_keys=True,
        )
    )


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
    universe = _capture_universe(settings)
    run_id = await store.start_run(
        kind=RunKind.INGESTION,
        environment=BrokerEnvironment.IG_DEMO,
        configuration_hash=universe.configuration_hash,
        started_at=clock.now(),
    )
    terminal_status = "FAILED"
    reconnect_task: asyncio.Task[None] | None = None
    reconnect_error: Exception | None = None
    disconnect_error: Exception | None = None
    forced_reconnect_completed = False
    bounded_deadline_reached = False
    try:
        listings = await store.active_provider_listings(
            [instrument.instrument_id for instrument in universe.instruments]
        )
        if len(listings) != len(universe.instruments):
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
            raise RuntimeError("unbounded IG ingestion iterator ended unexpectedly")
        else:
            try:
                async with asyncio.timeout(maximum_seconds):
                    await consume()
            except TimeoutError:
                bounded_deadline_reached = True
                terminal_status = "STOPPED"
            else:
                raise RuntimeError("bounded IG ingestion iterator ended before its timeout")
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
                    forced_reconnect_completed = True
            else:
                if bounded_deadline_reached:
                    reconnect_error = RuntimeError(
                        "forced reconnect did not complete before the ingestion deadline"
                    )
                    terminal_status = "FAILED"
                reconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reconnect_task
        try:
            await adapter.disconnect()
        except Exception as error:
            disconnect_error = error
            terminal_status = "FAILED"
        final_health = await adapter.health()
        await store.record_adapter_health(final_health)
        await store.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={
                "adapter_health": final_health.detail,
                "forced_reconnect_requested": force_reconnect_after_seconds is not None,
                "forced_reconnect_completed": forced_reconnect_completed,
            },
        )
        await engine.dispose()
        if reconnect_error is not None:
            raise reconnect_error
        if disconnect_error is not None:
            raise disconnect_error


async def _plan_backfill(
    settings: Settings,
    clock: Clock,
    *,
    universe_path: Path,
    start: datetime,
    end: datetime,
    remaining_allowance: int,
    output_path: Path,
    instrument_ids: Sequence[InstrumentId],
) -> None:
    if output_path.exists():
        raise FileExistsError(f"backfill plan output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"backfill plan output directory does not exist: {output_path.parent}"
        )
    universe = load_capture_universe(universe_path)
    available = set(universe.instruments_by_id)
    unknown = sorted(str(item) for item in set(instrument_ids) - available)
    if unknown:
        raise ValueError(f"backfill instruments are not in the selected universe: {unknown}")
    engine = _engine(settings)
    try:
        store = PostgresAuditStore(engine)
        listings = await store.active_provider_listings(instrument_ids)
        observed_at = clock.now()
        plan = build_backfill_plan(
            universe_name=universe.name,
            universe_hash=universe.configuration_hash,
            instrument_ids=instrument_ids,
            listings=listings,
            preferred_epics=universe.preferred_epics,
            start=start,
            end=end,
            remaining_allowance=remaining_allowance,
            quota_observed_at=observed_at,
            created_at=observed_at,
        )
        write_backfill_plan(output_path, plan)
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "plan_hash": plan.plan_hash,
                "instrument_count": len(plan.items),
                "points_per_instrument": plan.points_per_instrument,
                "requested_points": plan.requested_points,
                "selection_authority": False,
                "registered": False,
            },
            sort_keys=True,
        )
    )


async def _register_backfill(
    settings: Settings,
    *,
    plan_path: Path,
    confirmed_plan_hash: str,
) -> None:
    plan = load_backfill_plan(plan_path)
    if confirmed_plan_hash != plan.plan_hash:
        raise ValueError("confirmed backfill plan hash does not match the reviewed plan")
    engine = _engine(settings)
    try:
        status = await PostgresAuditStore(engine).register_backfill_plan(
            plan,
            backfill_plan_payload(plan),
        )
    finally:
        await engine.dispose()
    print(json.dumps({"plan_hash": plan.plan_hash, "status": status}, sort_keys=True))


async def _execute_backfill(settings: Settings, clock: Clock, *, plan_hash: str) -> None:
    _require_sha256_argument(plan_hash, "backfill plan hash")
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    adapter: IgDemoMarketDataAdapter | None = None
    plan: BackfillPlan | None = None
    plan_claimed = False
    run_id: RunId | None = None
    plan_completed = False
    terminal_status = "FAILED"
    written = 0
    received: dict[tuple[InstrumentId, PriceBasis], set[datetime]] = {}
    try:
        payload = await store.claim_backfill_plan(plan_hash)
        plan_claimed = True
        plan = decode_backfill_plan(json.dumps(payload, sort_keys=True))
        if plan.plan_hash != plan_hash:
            raise RuntimeError("claimed backfill plan content does not match the requested hash")
        adapter = _ig_backfill_adapter(settings, clock)
        run_id = await store.start_run(
            kind=RunKind.BACKFILL,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=plan.universe_hash,
            started_at=clock.now(),
        )
        await store.record_quota_state(
            provider="ig",
            environment="demo",
            allowance_name=plan.quota.allowance_name,
            remaining=plan.quota.remaining_points,
            observed_at=plan.quota.observed_at,
        )
        listings = tuple([await store.provider_listing_version(item) for item in plan.items])
        try:
            await adapter.connect()
            for request in backfill_requests(plan, listings):
                async for bar in adapter.backfill(request):
                    _validate_planned_bar(plan, request, bar)
                    received.setdefault((bar.instrument_id, bar.basis), set()).add(
                        bar.interval_start
                    )
                    event = await _append_bar(store, bar, received_time=clock.now())
                    if event is not None:
                        written += 1
        finally:
            await adapter.disconnect()
        observed_points = {
            (item.instrument_id, basis): len(received.get((item.instrument_id, basis), set()))
            for item in plan.items
            for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)
        }
        if any(points <= 0 for points in observed_points.values()):
            raise RuntimeError("planned historical range returned no data for a required basis")
        provider_remaining = adapter.historical_allowance_remaining
        if provider_remaining is not None:
            await store.record_quota_state(
                provider="ig",
                environment="demo",
                allowance_name="historical_points_weekly_provider_reported",
                remaining=provider_remaining,
                observed_at=clock.now(),
            )
        await store.complete_backfill_plan(
            plan,
            observed_points=observed_points,
            executed_at=clock.now(),
        )
        plan_completed = True
        terminal_status = "COMPLETED"
        print(
            json.dumps(
                {
                    "plan_hash": plan.plan_hash,
                    "points_received": sum(len(points) for points in received.values()),
                    "bars_written": written,
                    "provider_remaining_allowance": provider_remaining,
                },
                sort_keys=True,
            )
        )
    except BaseException:
        if plan_claimed and not plan_completed:
            await store.fail_backfill_plan(plan_hash, executed_at=clock.now())
        raise
    finally:
        if run_id is not None and plan is not None:
            await store.finish_run(
                run_id,
                status=terminal_status,
                finished_at=clock.now(),
                detail={
                    "plan_hash": plan.plan_hash,
                    "bars_written": written,
                    "points_received": sum(len(points) for points in received.values()),
                    "provider_remaining_allowance": (
                        adapter.historical_allowance_remaining if adapter is not None else None
                    ),
                },
            )
        await engine.dispose()


def _validate_planned_bar(plan: BackfillPlan, request: BackfillRequest, bar: MarketBar) -> None:
    if bar.provenance is not BarProvenance.IG_HISTORICAL:
        raise RuntimeError("provider returned a non-historical bar for a backfill plan")
    if bar.instrument_id != request.instrument_id:
        raise RuntimeError("provider returned a historical bar for another instrument")
    if bar.source_listing_id != request.listing.listing_id:
        raise RuntimeError("provider returned a historical bar for another listing")
    if not request.start <= bar.interval_start < request.end:
        raise RuntimeError("provider returned a historical bar outside the planned request")
    if (bar.interval_end - bar.interval_start).total_seconds() != 60:
        raise RuntimeError("provider returned a historical bar at an unexpected resolution")
    if request.resolution is not plan.resolution:
        raise RuntimeError("provider request resolution differs from its backfill plan")


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
            WHERE stream_id = :stream_id AND stream_version = :stream_version
            """,
            {"stream_id": stream_id, "stream_version": previous},
        )
        payload = to_json_value(bar)
        if len(rows) != 1:
            raise RuntimeError(f"latest historical bar event is missing for {stream_id}")
        existing_payload = rows[0]["payload"]
        if not isinstance(payload, dict) or not isinstance(existing_payload, dict):
            raise RuntimeError(f"historical bar payload is malformed for {stream_id}")
        comparable_payload = {key: value for key, value in payload.items() if key != "revision"}
        comparable_existing = {
            key: value for key, value in existing_payload.items() if key != "revision"
        }
        if comparable_existing == comparable_payload:
            return None
        bar = replace(bar, revision=previous + 1)
    event = EventEnvelope.create(
        stream_id=stream_id,
        stream_version=previous + 1,
        event_type="MarketBarClosed" if previous == 0 else "MarketBarCorrected",
        event_time=bar.interval_end,
        received_time=received_time,
        producer="ig-demo-backfill",
        producer_version="0.1.0",
        payload=bar,
    )
    try:
        return await store.append(event, expected_stream_version=previous)
    except StreamVersionConflict:
        return await _append_bar(store, bar, received_time=received_time)


async def _storage_snapshot(
    settings: Settings,
    *,
    universe_path: Path,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise FileExistsError(f"storage snapshot output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"storage snapshot output directory does not exist: {output_path.parent}"
        )
    universe = load_capture_universe(universe_path)
    engine = _engine(settings)
    try:
        measurement = await PostgresStorageInspector(engine).measure()
        snapshot = build_storage_snapshot(
            measurement,
            capture_source_id=settings.capture_source_id,
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            application_version=__version__,
            application_image=settings.image,
        )
        write_storage_snapshot(output_path, snapshot)
        print(
            json.dumps(
                {
                    "snapshot_sha256": snapshot.snapshot_sha256,
                    "observed_at": snapshot.observed_at.isoformat(),
                    "raw_message_count": snapshot.raw_message_count,
                    "canonical_event_count": snapshot.canonical_event_count,
                    "database_bytes": snapshot.database_bytes,
                    "output": str(output_path),
                },
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


def _compare_storage_snapshots(before_path: Path, after_path: Path) -> None:
    comparison = compare_storage_snapshots(
        load_storage_snapshot(before_path),
        load_storage_snapshot(after_path),
    )
    print(json.dumps(comparison, sort_keys=True))


async def _export(
    settings: Settings,
    clock: Clock,
    *,
    universe_path: Path,
    start: datetime,
    end: datetime,
) -> None:
    if end <= start:
        raise ValueError("research export end must follow start")
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    research = ParquetResearchStore(settings.research_root, clock)
    universe = load_capture_universe(universe_path)
    instrument_ids = tuple(str(instrument.instrument_id) for instrument in universe.instruments)
    encoded_instruments = json.dumps(instrument_ids)
    run_id = await store.start_run(
        kind=RunKind.EXPORT,
        environment=BrokerEnvironment.NONE,
        configuration_hash=universe.configuration_hash,
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
                  WHERE instrument_id IN (
                      SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
                  )
                    AND interval_start >= :interval_start
                    AND interval_end <= :interval_end
                ORDER BY instrument_id, basis, interval_start, provenance,
                         source_provider, source_environment, source_external_id,
                         revision DESC
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
        bars = tuple(_bar_from_projection(row) for row in rows)
        live_gaps = await store.query(
            """
            SELECT gap_id, instrument_id, interval_start, interval_end, reason,
                   detected_at, repaired_at
            FROM read_model.data_gaps
              WHERE instrument_id IN (
                  SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
              )
                AND interval_start < :interval_end
                AND interval_end > :interval_start
            ORDER BY instrument_id, interval_start, gap_id
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
        historical_coverage = await store.query(
            """
            SELECT instrument_id, source_provider, source_environment, source_external_id,
                   source_listing_valid_from, source_listing_metadata_version,
                   provenance, basis, resolution, interval_start, interval_end,
                   detected_at, detected_by_plan_hash, covered_at,
                   covered_by_plan_hash, observed_points
            FROM read_model.historical_coverage_gaps
              WHERE instrument_id IN (
                  SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
              )
                AND interval_start < :interval_end
                AND interval_end > :interval_start
            ORDER BY instrument_id, interval_start, basis, detected_by_plan_hash
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
        metadata = research_export_metadata(
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            instrument_ids=tuple(instrument.instrument_id for instrument in universe.instruments),
            interval_start=start,
            interval_end=end,
            bars=bars,
            live_gaps=live_gaps,
            historical_coverage=historical_coverage,
            application_version=__version__,
            application_image=settings.image,
        )
        manifest = await research.write_bars(
            bars,
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            metadata=metadata,
        )
        await store.record_manifest(manifest)
        terminal_status = "COMPLETED"
        print(
            json.dumps(
                {
                    "manifest_id": manifest.manifest_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "universe_name": manifest.universe_name,
                    "configuration_hash": manifest.configuration_hash,
                    "rows": manifest.row_count,
                },
                sort_keys=True,
            )
        )
    finally:
        await store.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={
                "universe_name": universe.name,
                "interval_start": start.isoformat(),
                "interval_end": end.isoformat(),
            },
        )
        await engine.dispose()


async def _replay(settings: Settings, clock: Clock, manifest_path: Path) -> None:
    if manifest_path.parent.name != "manifests" or manifest_path.suffix != ".json":
        raise ValueError("replay manifest must be a JSON file inside a manifests directory")
    manifest_id = manifest_path.stem
    root = manifest_path.parent.parent
    store = ParquetResearchStore(root, clock)
    manifest = await store.read_manifest(manifest_id)
    engine = _engine(settings)
    audit = PostgresAuditStore(engine)
    run_id = await audit.start_run(
        kind=RunKind.REPLAY,
        environment=BrokerEnvironment.NONE,
        configuration_hash=manifest.configuration_hash,
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
        print(
            json.dumps(
                {
                    "manifest_id": manifest_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "rows": len(first),
                    "sha256": first_hash,
                },
                sort_keys=True,
            )
        )
    finally:
        await audit.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={
                "manifest_id": manifest_id,
                "manifest_sha256": manifest.manifest_sha256,
                "universe_name": manifest.universe_name,
            },
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


def _capture_universe(settings: Settings) -> CaptureUniverse:
    return load_capture_universe(settings.capture_universe_path)


def _configuration_hash(settings: Settings) -> str:
    return _capture_universe(settings).configuration_hash


if __name__ == "__main__":
    main()
