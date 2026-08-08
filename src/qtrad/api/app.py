"""FastAPI composition for read-only data health."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from qtrad.adapters.postgres.queries import OperatorQueries
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.domain.events import EventEnvelope, JsonValue
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.modes import BrokerEnvironment
from qtrad.ports.capture_feed import CaptureIdentity
from qtrad.runtime.ibkr_native_capture import load_reviewed_configuration
from qtrad.runtime.settings import Settings
from qtrad.runtime.universe import load_capture_universe

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


class FeedEventResponse(BaseModel):
    global_position: int = Field(gt=0)
    event_id: UUID
    stream_id: str
    stream_version: int = Field(gt=0)
    event_type: str
    schema_version: int = Field(gt=0)
    event_time: datetime
    received_time: datetime
    persisted_time: datetime
    correlation_id: UUID
    causation_id: UUID | None
    producer: str
    producer_version: str
    payload: dict[str, JsonValue]

    @classmethod
    def from_event(cls, event: EventEnvelope) -> "FeedEventResponse":
        if event.global_position is None or event.persisted_time is None:
            raise ValueError("feed events must be persisted")
        return cls(
            global_position=event.global_position,
            event_id=event.event_id,
            stream_id=event.stream_id,
            stream_version=event.stream_version,
            event_type=event.event_type,
            schema_version=event.schema_version,
            event_time=event.event_time,
            received_time=event.received_time,
            persisted_time=event.persisted_time,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            producer=event.producer,
            producer_version=event.producer_version,
            payload=event.payload,
        )


class FeedPageResponse(BaseModel):
    feed_schema_version: Literal[1] = 1
    source_id: str
    universe_name: str
    configuration_hash: str
    after_position: int = Field(ge=0)
    high_water_position: int = Field(ge=0)
    next_position: int = Field(ge=0)
    has_more: bool
    events: list[FeedEventResponse]


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or Settings()
    engine = create_async_engine(configuration.database_url, pool_pre_ping=True)
    store = PostgresAuditStore(engine)
    queries = OperatorQueries(store)

    def current_universe():
        return load_capture_universe(configuration.capture_universe_path)

    def current_identity() -> tuple[CaptureIdentity, tuple[str, ...] | None]:
        if configuration.provider == "ibkr":
            if configuration.ibkr_capture_configuration_path is not None:
                reviewed = load_reviewed_configuration(
                    configuration.ibkr_capture_configuration_path
                )
                return reviewed.identity, tuple(
                    str(item.instrument_id) for item in reviewed.listings
                )
            if configuration.ibkr_capture_configuration_hash is None:
                raise HTTPException(
                    status_code=503,
                    detail="IBKR native capture configuration identity is not configured",
                )
            return (
                CaptureIdentity(
                    provider="ibkr",
                    environment=BrokerEnvironment.IBKR_PAPER.value,
                    source_class=MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
                    capture_source_id=configuration.ibkr_capture_source_id,
                    universe_id=configuration.ibkr_capture_universe_id,
                    configuration_hash=configuration.ibkr_capture_configuration_hash,
                ),
                None,
            )
        universe = current_universe()
        return (
            CaptureIdentity(
                provider="ig",
                environment=BrokerEnvironment.IG_DEMO.value,
                source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
                capture_source_id=configuration.capture_source_id,
                universe_id=universe.name,
                configuration_hash=universe.configuration_hash,
            ),
            tuple(str(instrument.instrument_id) for instrument in universe.instruments),
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="q-trad data foundation",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.queries = queries

    @app.get("/health")
    async def health() -> dict[str, str]:
        await queries.system()
        return {
            "status": "ok",
            "mode": "data-only",
            "provider": configuration.provider,
            "broker_environment": (
                BrokerEnvironment.IBKR_PAPER.value
                if configuration.provider == "ibkr"
                else BrokerEnvironment.IG_DEMO.value
            ),
        }

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        identity, instrument_ids = current_identity()
        expected_instruments = instrument_ids
        if expected_instruments is None:
            expected_instruments = await queries.source_instrument_ids(
                provider=identity.provider, environment=identity.environment
            )
        if not expected_instruments:
            result = {
                "ready": False,
                "reasons": ["no exact provider listings are persisted for the configured source"],
                "expected_instruments": 0,
                "configuration_hash": identity.configuration_hash,
                "provider": identity.provider,
                "environment": identity.environment,
                "source_class": identity.source_class.value,
            }
            return JSONResponse(content=jsonable_encoder(result), status_code=503)
        result = await queries.readiness(
            expected_instruments,
            identity.configuration_hash,
            provider=identity.provider,
            environment=identity.environment,
            adapter_name=(
                "ibkr-native-capture" if identity.provider == "ibkr" else "ig-market-data"
            ),
            freshness_seconds=(
                configuration.ibkr_capture_freshness_seconds
                if identity.provider == "ibkr"
                else 300.0
            ),
            source_class=identity.source_class.value,
        )
        status_code = 200 if result["ready"] else 503
        return JSONResponse(content=jsonable_encoder(result), status_code=status_code)

    @app.get("/api/v1/system")
    async def system() -> Any:
        return jsonable_encoder(await queries.system())

    @app.get("/api/v1/feed/events", response_model=FeedPageResponse)
    async def feed_events(
        after_position: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> FeedPageResponse:
        identity, _ = current_identity()
        page = await queries.event_page(after_position=after_position, limit=limit)
        if after_position > page.high_water_position:
            raise HTTPException(status_code=409, detail="cursor exceeds source high-water position")
        events = [FeedEventResponse.from_event(event) for event in page.events]
        next_position = events[-1].global_position if events else after_position
        return FeedPageResponse(
            feed_schema_version=1,
            source_id=identity.capture_source_id,
            universe_name=identity.universe_id,
            configuration_hash=identity.configuration_hash,
            after_position=after_position,
            high_water_position=page.high_water_position,
            next_position=next_position,
            has_more=next_position < page.high_water_position,
            events=events,
        )

    @app.get("/api/v1/capture/identity")
    async def capture_identity() -> Any:
        identity, _ = current_identity()
        latest = await queries.capture_identity(
            provider=identity.provider,
            environment=identity.environment,
        )
        return jsonable_encoder(
            {
                "provider": identity.provider,
                "environment": identity.environment,
                "source_class": identity.source_class.value,
                "capture_source_id": identity.capture_source_id,
                "universe_id": identity.universe_id,
                "configuration_hash": identity.configuration_hash,
                "latest_raw_identity": latest,
            }
        )

    @app.get("/api/v1/capture/reconciliation")
    async def capture_reconciliation(capture_session_id: UUID | None = None) -> Any:
        identity, _ = current_identity()
        return jsonable_encoder(
            await queries.capture_reconciliation(
                provider=identity.provider,
                environment=identity.environment,
                capture_session_id=(str(capture_session_id) if capture_session_id else None),
            )
        )

    @app.get("/api/v1/instruments")
    async def instruments() -> Any:
        identity, _ = current_identity()
        return jsonable_encoder(
            await queries.instruments(
                provider=identity.provider,
                environment=identity.environment,
                source_class=identity.source_class.value,
            )
        )

    @app.get("/api/v1/instruments/{instrument_id:path}")
    async def instrument(instrument_id: str) -> Any:
        identity, _ = current_identity()
        result = await queries.instrument(
            instrument_id,
            provider=identity.provider,
            environment=identity.environment,
            source_class=identity.source_class.value,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        return jsonable_encoder(result)

    @app.get("/api/v1/bars")
    async def bars(instrument_id: str | None = None, limit: int = 500) -> Any:
        bounded_limit = min(max(limit, 1), 5000)
        return jsonable_encoder(
            await queries.bars(instrument_id=instrument_id, limit=bounded_limit)
        )

    @app.get("/api/v1/gaps")
    async def gaps() -> Any:
        return jsonable_encoder(await queries.gaps())

    @app.get("/api/v1/historical-coverage")
    async def historical_coverage(
        instrument_id: str | None = None,
        only_open: bool = False,
        limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    ) -> Any:
        return jsonable_encoder(
            await queries.historical_coverage(
                instrument_id=instrument_id,
                only_open=only_open,
                limit=limit,
            )
        )

    @app.get("/api/v1/runs")
    async def runs() -> Any:
        return jsonable_encoder(await queries.runs())

    @app.get("/api/v1/manifests")
    async def manifests() -> Any:
        return jsonable_encoder(await queries.manifests())

    @app.get("/", response_class=HTMLResponse)
    async def console(request: Request) -> HTMLResponse:
        identity, _ = current_identity()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "system": await queries.system(),
                "instruments": await queries.instruments(
                    provider=identity.provider,
                    environment=identity.environment,
                    source_class=identity.source_class.value,
                ),
            },
        )

    @app.get("/console/overview", response_class=HTMLResponse)
    async def console_overview(request: Request) -> HTMLResponse:
        identity, _ = current_identity()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="_overview.html",
            context={
                "system": await queries.system(),
                "instruments": await queries.instruments(
                    provider=identity.provider,
                    environment=identity.environment,
                    source_class=identity.source_class.value,
                ),
            },
        )

    @app.get("/console/instruments/{instrument_id:path}", response_class=HTMLResponse)
    async def instrument_page(request: Request, instrument_id: str) -> HTMLResponse:
        identity, _ = current_identity()
        result = await queries.instrument(
            instrument_id,
            provider=identity.provider,
            environment=identity.environment,
            source_class=identity.source_class.value,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        return TEMPLATES.TemplateResponse(
            request=request,
            name="instrument.html",
            context={"detail": result},
        )

    return app


def engine_from_app(app: FastAPI) -> AsyncEngine:
    return app.state.engine
