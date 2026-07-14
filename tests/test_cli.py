import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from qtrad import __main__ as cli
from qtrad.domain.identifiers import InstrumentId
from qtrad.ports.clock import Clock
from qtrad.ports.market_data import InstrumentListingReview
from qtrad.runtime.settings import Settings
from qtrad.runtime.universe import load_capture_universe


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
            [
                "instruments",
                "review",
                "--catalogue",
                "config/capture-v2-candidates.toml",
                "--output",
                "review.json",
            ],
            "_review_instruments",
            (
                ("catalogue_path", Path("config/capture-v2-candidates.toml")),
                ("output_path", Path("review.json")),
            ),
        ),
        (
            ["ingest", "--max-seconds", "60", "--force-reconnect-after-seconds", "20"],
            "_ingest",
            (("maximum_seconds", 60.0), ("force_reconnect_after_seconds", 20.0)),
        ),
        (
            [
                "backfill",
                "plan",
                "--universe",
                "config/capture-v1.toml",
                "--start",
                "2026-07-17T22:00:00Z",
                "--end",
                "2026-07-18T00:00:00Z",
                "--remaining-allowance",
                "500",
                "--output",
                "backfill-plan.json",
                "fx:aud-usd",
            ],
            "_plan_backfill",
            (
                ("universe_path", Path("config/capture-v1.toml")),
                ("start", datetime(2026, 7, 17, 22, tzinfo=UTC)),
                ("end", datetime(2026, 7, 18, tzinfo=UTC)),
                ("remaining_allowance", 500),
                ("output_path", Path("backfill-plan.json")),
                ("instrument_ids", [InstrumentId("fx:aud-usd")]),
            ),
        ),
        (
            [
                "backfill",
                "register",
                "--plan",
                "backfill-plan.json",
                "--confirm-plan-hash",
                "a" * 64,
            ],
            "_register_backfill",
            (
                ("plan_path", Path("backfill-plan.json")),
                ("confirmed_plan_hash", "a" * 64),
            ),
        ),
        (
            ["backfill", "execute", "--plan-hash", "a" * 64],
            "_execute_backfill",
            (("plan_hash", "a" * 64),),
        ),
        (
            [
                "research",
                "export",
                "--universe",
                "config/capture-v1.toml",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ],
            "_export",
            (
                ("universe_path", Path("config/capture-v1.toml")),
                ("start", datetime(2026, 7, 1, tzinfo=UTC)),
                ("end", datetime(2026, 7, 2, tzinfo=UTC)),
            ),
        ),
        (["replay", "--manifest", "research/manifests/example.json"], "_replay", ()),
        (["projections", "rebuild"], "_rebuild", ()),
        (
            [
                "storage",
                "snapshot",
                "--universe",
                "config/capture-v1.toml",
                "--output",
                "storage-before.json",
            ],
            "_storage_snapshot",
            (
                ("universe_path", Path("config/capture-v1.toml")),
                ("output_path", Path("storage-before.json")),
            ),
        ),
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
    if target not in {"_rebuild", "_register_backfill", "_storage_snapshot"}:
        positional.append(cli_clock)
    if target == "_replay":
        positional.append(Path("research/manifests/example.json"))
    operation.assert_awaited_once_with(*positional, **dict(expected))


def test_storage_comparison_dispatches_without_database_access(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_compare_storage_snapshots", operation)

    cli.main(["storage", "compare", "before.json", "after.json"])

    operation.assert_called_once_with(Path("before.json"), Path("after.json"))


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


def test_universe_promotion_dispatches_without_settings_or_provider_io(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
    cli_clock: Clock,
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_promote_universe", operation)

    cli.main(
        [
            "instruments",
            "promote",
            "--catalogue",
            "candidates.toml",
            "--review",
            "review.json",
            "--selections",
            "selections.toml",
            "--release-name",
            "capture-v2",
            "--output",
            "capture-v2.toml",
        ]
    )

    operation.assert_called_once_with(
        cli_clock,
        catalogue_path=Path("candidates.toml"),
        review_path=Path("review.json"),
        selections_path=Path("selections.toml"),
        release_name="capture-v2",
        output_path=Path("capture-v2.toml"),
    )


def test_universe_promotion_refuses_existing_output_before_reading_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "capture-v2.toml"
    output.write_text("existing evidence")

    with pytest.raises(FileExistsError, match="already exists"):
        cli._promote_universe(
            Mock(spec=Clock),
            catalogue_path=tmp_path / "missing-catalogue.toml",
            review_path=tmp_path / "missing-review.json",
            selections_path=tmp_path / "missing-selections.toml",
            release_name="capture-v2",
            output_path=output,
        )


def test_feed_verification_dispatches_saved_pages_without_network_io(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_verify_capture_feed_pages", operation)

    cli.main(
        [
            "feed",
            "verify",
            "--source-id",
            "oci-sydney-capture-1",
            "--universe-name",
            "capture-v1",
            "--configuration-hash",
            "a" * 64,
            "--after-position",
            "41",
            "page-1.json",
            "page-2.json",
        ]
    )

    operation.assert_called_once_with(
        source_id="oci-sydney-capture-1",
        universe_name="capture-v1",
        configuration_hash="a" * 64,
        after_position=41,
        page_paths=[Path("page-1.json"), Path("page-2.json")],
    )


def test_feed_probe_dispatches_one_bounded_loopback_request(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
) -> None:
    del cli_environment
    operation = AsyncMock()
    monkeypatch.setattr(cli, "_probe_capture_feed", operation)

    cli.main(
        [
            "feed",
            "probe",
            "--endpoint",
            "http://127.0.0.1:18080",
            "--source-id",
            "oci-sydney-capture-1",
            "--universe-name",
            "capture-v1",
            "--configuration-hash",
            "a" * 64,
            "--after-position",
            "41",
            "--limit",
            "25",
        ]
    )

    operation.assert_awaited_once_with(
        endpoint="http://127.0.0.1:18080",
        source_id="oci-sydney-capture-1",
        universe_name="capture-v1",
        configuration_hash="a" * 64,
        after_position=41,
        limit=25,
    )


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
async def test_listing_review_writes_new_non_authoritative_manifest_without_database_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.connect_count = 0
            self.disconnect_count = 0

        async def connect(self) -> None:
            self.connect_count += 1

        async def disconnect(self) -> None:
            self.disconnect_count += 1

        async def review_listings(
            self, instrument_ids: Sequence[InstrumentId]
        ) -> tuple[InstrumentListingReview, ...]:
            return tuple(
                InstrumentListingReview(instrument_id, ()) for instrument_id in instrument_ids
            )

    adapter = FakeAdapter()
    clock = Mock(spec=Clock)
    clock.now.return_value = datetime(2026, 7, 18, tzinfo=UTC)
    monkeypatch.setattr(
        cli, "_ig_review_adapter", lambda settings, selected_clock, candidates: adapter
    )
    output = tmp_path / "review.json"

    await cli._review_instruments(
        cast(Settings, SimpleNamespace()),
        cast(Clock, clock),
        catalogue_path=Path("config/capture-v2-candidates.toml"),
        output_path=output,
    )

    manifest = json.loads(output.read_text())
    assert manifest["selection_authority"] is False
    assert manifest["catalogue_name"] == "capture-v2-candidates"
    assert len(manifest["instruments"]) == 20
    assert adapter.connect_count == 1
    assert adapter.disconnect_count == 1

    with pytest.raises(FileExistsError, match="already exists"):
        await cli._review_instruments(
            cast(Settings, SimpleNamespace()),
            cast(Clock, clock),
            catalogue_path=Path("config/capture-v2-candidates.toml"),
            output_path=output,
        )
    assert adapter.connect_count == 1


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

        async def active_provider_listings(self, instrument_ids: object) -> list[object]:
            del instrument_ids
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
    monkeypatch.setattr(
        cli,
        "_capture_universe",
        lambda settings: load_capture_universe(Path("config/capture-v1.toml")),
    )

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
