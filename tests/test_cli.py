import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from qtrad import __main__ as cli
from qtrad.runtime.settings import Settings


@pytest.fixture
def cli_environment(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = cast(Settings, SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", Mock())
    return settings


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
    arguments: list[str],
    target: str,
    expected: tuple[tuple[str, object], ...],
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(cli, target, operation)

    cli.main(arguments)

    positional: list[object] = [cli_environment]
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
            maximum_seconds=maximum_seconds,
            force_reconnect_after_seconds=reconnect_seconds,
        )


def test_parser_rejects_non_demo_ingestion_environment() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ingest", "--environment", "live"])


def test_main_does_not_leave_an_event_loop_running(
    monkeypatch: pytest.MonkeyPatch, cli_environment: Settings
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(cli, "_rebuild", operation)

    cli.main(["projections", "rebuild"])

    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()
