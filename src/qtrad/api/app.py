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
    universe = load_capture_universe(configuration.capture_universe_path)

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
        return {"status": "ok", "mode": "data-only", "broker_environment": "IG_DEMO"}

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        result = await queries.readiness(
            tuple(str(instrument.instrument_id) for instrument in universe.instruments),
            universe.configuration_hash,
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
        page = await queries.event_page(after_position=after_position, limit=limit)
        if after_position > page.high_water_position:
            raise HTTPException(status_code=409, detail="cursor exceeds source high-water position")
        events = [FeedEventResponse.from_event(event) for event in page.events]
        next_position = events[-1].global_position if events else after_position
        return FeedPageResponse(
            feed_schema_version=1,
            source_id=configuration.capture_source_id,
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            after_position=after_position,
            high_water_position=page.high_water_position,
            next_position=next_position,
            has_more=next_position < page.high_water_position,
            events=events,
        )

    @app.get("/api/v1/instruments")
    async def instruments() -> Any:
        return jsonable_encoder(await queries.instruments())

    @app.get("/api/v1/instruments/{instrument_id:path}")
    async def instrument(instrument_id: str) -> Any:
        result = await queries.instrument(instrument_id)
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

    @app.get("/api/v1/runs")
    async def runs() -> Any:
        return jsonable_encoder(await queries.runs())

    @app.get("/api/v1/manifests")
    async def manifests() -> Any:
        return jsonable_encoder(await queries.manifests())

    @app.get("/", response_class=HTMLResponse)
    async def console(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={"system": await queries.system(), "instruments": await queries.instruments()},
        )

    @app.get("/console/overview", response_class=HTMLResponse)
    async def console_overview(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="_overview.html",
            context={
                "system": await queries.system(),
                "instruments": await queries.instruments(),
            },
        )

    @app.get("/console/instruments/{instrument_id:path}", response_class=HTMLResponse)
    async def instrument_page(request: Request, instrument_id: str) -> HTMLResponse:
        result = await queries.instrument(instrument_id)
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
