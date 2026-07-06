import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from qtrad import __main__ as cli
from qtrad.ports.clock import Clock
from qtrad.runtime.settings import Settings


@pytest.fixture
def cli_environment(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = cast(Settings, SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", Mock())
    return settings


@pytest.fixture
def cli_clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Mock(spec=Clock)
    monkeypatch.setattr(cli, "SystemClock", Mock(return_value=clock))
    return cast(Clock, clock)


@pytest.mark.parametrize(
    ("arguments", "target", "expected"),
    [
        (["instruments", "sync"], "_sync_instruments", ()),
        (
            ["ingest", "--max-seconds", "60", "--force-reconnect-after-seconds", "20"],
            "_ingest",
            (("maximum_seconds", 60.0), ("force_reconnect_after_seconds", 20.0)),
        ),
        (
            ["backfill", "--max-points", "25", "--remaining-allowance", "500"],
            "_backfill",
            (("maximum_points", 25), ("remaining_allowance", 500)),
        ),
        (["research", "export"], "_export", ()),
        (["replay", "--manifest", "research/manifests/example.json"], "_replay", ()),
        (["projections", "rebuild"], "_rebuild", ()),
    ],
)
def test_async_command_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
    cli_clock: Clock,
    arguments: list[str],
    target: str,
    expected: tuple[tuple[str, object], ...],
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(cli, target, operation)

    cli.main(arguments)

    positional: list[object] = [cli_environment]
    if target != "_rebuild":
        positional.append(cli_clock)
    if target == "_replay":
        positional.append(Path("research/manifests/example.json"))
    operation.assert_awaited_once_with(*positional, **dict(expected))


def test_database_upgrade_dispatches_migration_and_seed(
    monkeypatch: pytest.MonkeyPatch, cli_environment: Settings
) -> None:
    upgrade = Mock()
    seed = AsyncMock()
    monkeypatch.setattr(cli, "_upgrade_database", upgrade)
    monkeypatch.setattr(cli, "_seed", seed)

    cli.main(["db", "upgrade"])

    upgrade.assert_called_once_with(cli_environment)
    seed.assert_awaited_once_with(cli_environment)


def test_api_dispatches_read_only_application(
    monkeypatch: pytest.MonkeyPatch, cli_environment: Settings
) -> None:
    application = object()
    create_app = Mock(return_value=application)
    run = Mock()
    monkeypatch.setattr(cli, "create_app", create_app)
    monkeypatch.setattr(cli.uvicorn, "run", run)

    cli.main(["api", "--host", "0.0.0.0", "--port", "8123"])

    run.assert_called_once_with(application, host="0.0.0.0", port=8123)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("maximum_seconds", "reconnect_seconds", "message"),
    [
        (0, None, "maximum seconds must be positive"),
        (60, 0, "forced reconnect interval must be positive"),
        (60, 60, "forced reconnect must occur before maximum seconds"),
    ],
)
async def test_ingestion_rejects_invalid_time_bounds_before_io(
    maximum_seconds: float,
    reconnect_seconds: float | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        await cli._ingest(
            cast(Settings, SimpleNamespace()),
            Mock(spec=Clock),
            maximum_seconds=maximum_seconds,
            force_reconnect_after_seconds=reconnect_seconds,
        )


def test_parser_rejects_non_demo_ingestion_environment() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ingest", "--environment", "live"])


@pytest.mark.asyncio
async def test_bounded_ingestion_fails_when_forced_reconnect_does_not_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEngine:
        async def dispose(self) -> None:
            pass

    class FakeStore:
        def __init__(self) -> None:
            self.finished: dict[str, object] | None = None

        async def start_run(self, **kwargs: object) -> str:
            del kwargs
            return "not-a-real-run"

        async def active_provider_listings(self) -> list[object]:
            return [object() for _ in range(7)]

        async def record_adapter_health(self, health: object) -> None:
            del health

        async def finish_run(self, run_id: str, **kwargs: object) -> None:
            assert run_id == "not-a-real-run"
            self.finished = kwargs

    class FakeAdapter:
        async def connect(self) -> None:
            pass

        async def subscribe(self, listings: object) -> None:
            del listings

        async def health(self) -> SimpleNamespace:
            return SimpleNamespace(detail="state=STOPPED; reconnects=0")

        async def force_reconnect(self) -> None:
            await asyncio.Event().wait()

        async def records(self) -> AsyncIterator[object]:
            while True:
                await asyncio.sleep(1)
                yield object()

        async def disconnect(self) -> None:
            pass

    store = FakeStore()
    adapter = FakeAdapter()
    clock = Mock(spec=Clock)
    clock.now.return_value = datetime(2026, 7, 6, tzinfo=UTC)
    monkeypatch.setattr(cli, "_engine", lambda settings: FakeEngine())
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda engine: store)
    monkeypatch.setattr(cli, "_ig_adapter", lambda settings, selected_clock: adapter)

    with pytest.raises(RuntimeError, match="did not complete"):
        await cli._ingest(
            cast(Settings, SimpleNamespace()),
            cast(Clock, clock),
            maximum_seconds=0.02,
            force_reconnect_after_seconds=0.001,
        )

    assert store.finished is not None
    assert store.finished["status"] == "FAILED"
    assert store.finished["detail"] == {
        "adapter_health": "state=STOPPED; reconnects=0",
        "forced_reconnect_requested": True,
        "forced_reconnect_completed": False,
    }


def test_main_does_not_leave_an_event_loop_running(
    monkeypatch: pytest.MonkeyPatch, cli_environment: Settings
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(cli, "_rebuild", operation)

    cli.main(["projections", "rebuild"])

    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()
