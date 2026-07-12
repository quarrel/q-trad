"""FastAPI composition for read-only data health."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from qtrad.adapters.postgres.queries import OperatorQueries
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.runtime.settings import Settings
from qtrad.runtime.universe import load_capture_universe

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


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
            tuple(str(instrument.instrument_id) for instrument in universe.instruments)
        )
        status_code = 200 if result["ready"] else 503
        return JSONResponse(content=jsonable_encoder(result), status_code=status_code)

    @app.get("/api/v1/system")
    async def system() -> Any:
        return jsonable_encoder(await queries.system())

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
