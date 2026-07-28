import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from qtrad import __main__ as cli
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import Instrument, ProductType, ProviderListing
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
        (
            [
                "runs",
                "reconcile-plan",
                "--universe",
                "config/capture-v1.toml",
                "--cutoff",
                "2026-07-14T03:05:33.653928Z",
                "--output",
                "run-reconciliation.json",
            ],
            "_plan_run_reconciliation",
            (
                ("universe_path", Path("config/capture-v1.toml")),
                ("cutoff", datetime(2026, 7, 14, 3, 5, 33, 653928, tzinfo=UTC)),
                ("output_path", Path("run-reconciliation.json")),
            ),
        ),
        (
            [
                "qualification",
                "gap-history",
                "--evidence",
                "qualification.json",
                "--plan-set",
                "gap-plan-set.json",
                "--manifest",
                "research/manifests/example.json",
                "--output",
                "gap-history.json",
            ],
            "_review_qualification_gap_history",
            (
                ("evidence_path", Path("qualification.json")),
                ("plan_path", None),
                ("plan_set_path", Path("gap-plan-set.json")),
                ("manifest_path", Path("research/manifests/example.json")),
                ("output_path", Path("gap-history.json")),
            ),
        ),
        (
            [
                "runs",
                "reconcile",
                "--plan",
                "run-reconciliation.json",
                "--confirm-plan-hash",
                "a" * 64,
            ],
            "_reconcile_runs",
            (
                ("plan_path", Path("run-reconciliation.json")),
                ("confirmed_plan_hash", "a" * 64),
            ),
        ),
        (
            [
                "qualification",
                "gap-register",
                "--plan-set",
                "gap-plan-set.json",
                "--snapshot-import-evidence",
                "snapshot-import.json",
                "--confirm-plan-set-hash",
                "a" * 64,
            ],
            "_register_qualification_gap_plan_set",
            (
                ("plan_set_path", Path("gap-plan-set.json")),
                ("snapshot_import_path", Path("snapshot-import.json")),
                ("confirmed_plan_set_hash", "a" * 64),
            ),
        ),
        (
            [
                "qualification",
                "gap-execute",
                "--plan-set",
                "gap-plan-set.json",
                "--snapshot-import-evidence",
                "snapshot-import.json",
                "--confirm-plan-set-hash",
                "a" * 64,
            ],
            "_execute_qualification_gap_plan_set",
            (
                ("plan_set_path", Path("gap-plan-set.json")),
                ("snapshot_import_path", Path("snapshot-import.json")),
                ("confirmed_plan_set_hash", "a" * 64),
            ),
        ),
        (
            ["instruments", "sync", "--universe", "candidate.toml"],
            "_sync_instruments",
            (("universe_path", Path("candidate.toml")),),
        ),
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
                "qualification",
                "gap-history",
                "--evidence",
                "qualification.json",
                "--plan",
                "backfill-plan.json",
                "--manifest",
                "research/manifests/example.json",
                "--output",
                "gap-history.json",
            ],
            "_review_qualification_gap_history",
            (
                ("evidence_path", Path("qualification.json")),
                ("plan_path", Path("backfill-plan.json")),
                ("plan_set_path", None),
                ("manifest_path", Path("research/manifests/example.json")),
                ("output_path", Path("gap-history.json")),
            ),
        ),
        (
            [
                "qualification",
                "gap-plan",
                "--evidence",
                "qualification.json",
                "--snapshot-import-evidence",
                "snapshot-import.json",
                "--universe",
                "config/capture-v1.toml",
                "--remaining-allowance",
                "500",
                "--output",
                "gap-plan.json",
            ],
            "_plan_qualification_gap_history",
            (
                ("evidence_path", Path("qualification.json")),
                ("snapshot_import_path", Path("snapshot-import.json")),
                ("universe_path", Path("config/capture-v1.toml")),
                ("remaining_allowance", 500),
                ("output_path", Path("gap-plan.json")),
            ),
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
                "--snapshot-import-evidence",
                "snapshot-import.json",
            ],
            "_export",
            (
                ("universe_path", Path("config/capture-v1.toml")),
                ("start", datetime(2026, 7, 1, tzinfo=UTC)),
                ("end", datetime(2026, 7, 2, tzinfo=UTC)),
                ("snapshot_import_path", Path("snapshot-import.json")),
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
    if target not in {
        "_rebuild",
        "_register_backfill",
        "_register_qualification_gap_plan_set",
        "_storage_snapshot",
    }:
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

    cli.main(
        [
            "storage",
            "compare",
            "--output",
            "comparison.json",
            "before.json",
            "after.json",
        ]
    )

    operation.assert_called_once_with(
        Path("before.json"), Path("after.json"), Path("comparison.json")
    )


def test_storage_contrast_dispatches_without_database_access(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_contrast_storage_comparisons", operation)

    cli.main(
        [
            "storage",
            "contrast",
            "--output",
            "contrast.json",
            "baseline.json",
            "candidate.json",
        ]
    )

    operation.assert_called_once_with(
        Path("baseline.json"), Path("candidate.json"), Path("contrast.json")
    )


def test_storage_review_dispatches_without_database_access(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_record_storage_active_market_review", operation)

    cli.main(
        [
            "storage",
            "review",
            "--output",
            "review-artifact.json",
            "comparison.json",
            "review-input.json",
        ]
    )

    operation.assert_called_once_with(
        Path("comparison.json"), Path("review-input.json"), Path("review-artifact.json")
    )


def test_storage_qualification_dispatches_without_database_access(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_qualify_storage_contrast", operation)

    cli.main(
        [
            "storage",
            "qualify",
            "--output",
            "qualification.json",
            "contrast.json",
            "baseline-review.json",
            "candidate-review.json",
        ]
    )

    operation.assert_called_once_with(
        Path("contrast.json"),
        Path("baseline-review.json"),
        Path("candidate-review.json"),
        Path("qualification.json"),
    )


@pytest.mark.asyncio
async def test_gap_plan_requires_isolated_database_at_current_migration_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SimpleNamespace(dispose=AsyncMock())
    store = SimpleNamespace(query=AsyncMock(return_value=[{"version_num": "0009"}]))
    monkeypatch.setattr(cli, "_engine", lambda _: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda _: store)
    monkeypatch.setattr(
        cli.ScriptDirectory,
        "from_config",
        Mock(return_value=SimpleNamespace(get_current_head=Mock(return_value="0009"))),
    )
    settings = Settings(database_url="postgresql+asyncpg://qtrad@db/qtrad_research_test")

    await cli._require_database_at_migration_head(settings)

    store.query.return_value = [{"version_num": "0008"}]
    with pytest.raises(RuntimeError, match="migration head"):
        await cli._require_database_at_migration_head(settings)


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


@pytest.mark.asyncio
async def test_listing_sync_can_validate_an_explicit_non_streaming_universe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    universe_path = tmp_path / "historical-candidate.toml"
    universe_path.write_text(
        """
name = "historical-candidate"

[[instrument]]
id = "fx:nzd-usd"
display_name = "NZD/USD"
asset_class = "FX"
base_currency = "NZD"
quote_currency = "USD"
search_aliases = ["NZD/USD", "NZDUSD"]
preferred_epic = "CS.D.NZDUSD.CFD.IP"
"""
    )
    selected_universe = load_capture_universe(universe_path)
    listing = SimpleNamespace(listing_id="ig:demo:CS.D.NZDUSD.CFD.IP")

    class FakeEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class FakeAdapter:
        def __init__(self) -> None:
            self.connected = False
            self.disconnected = False
            self.requested: list[InstrumentId] = []

        async def connect(self) -> None:
            self.connected = True

        async def disconnect(self) -> None:
            self.disconnected = True

        async def discover_listings(self, instrument_ids: Sequence[InstrumentId]) -> list[object]:
            self.requested = list(instrument_ids)
            return [listing]

    class FakeStore:
        def __init__(self) -> None:
            self.seeded: tuple[Instrument, ...] = ()
            self.validated: list[tuple[object, str, datetime]] = []

        async def seed_instruments(self, instruments: Sequence[Instrument]) -> None:
            self.seeded = tuple(instruments)

        async def validate_provider_listing(
            self, selected_listing: object, *, universe_hash: str, observed_at: datetime
        ) -> None:
            self.validated.append((selected_listing, universe_hash, observed_at))

    engine = FakeEngine()
    adapter = FakeAdapter()
    store = FakeStore()
    clock = Mock(spec=Clock)
    observed_at = datetime(2026, 7, 18, tzinfo=UTC)
    clock.now.return_value = observed_at
    adapter_universes: list[object] = []

    def build_adapter(
        settings: Settings, selected_clock: Clock, *, universe: object | None = None
    ) -> FakeAdapter:
        del settings
        assert selected_clock is clock
        adapter_universes.append(universe)
        return adapter

    def reject_runtime_universe(settings: Settings) -> object:
        del settings
        raise AssertionError("explicit listing validation read the runtime universe")

    monkeypatch.setattr(cli, "_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda selected_engine: store)
    monkeypatch.setattr(cli, "_ig_adapter", build_adapter)
    monkeypatch.setattr(
        cli,
        "_capture_universe",
        reject_runtime_universe,
    )

    await cli._sync_instruments(
        cast(Settings, SimpleNamespace()),
        cast(Clock, clock),
        universe_path=universe_path,
    )

    assert adapter_universes == [selected_universe]
    assert [str(item.instrument_id) for item in store.seeded] == ["fx:nzd-usd"]
    assert [str(item) for item in adapter.requested] == ["fx:nzd-usd"]
    assert store.validated == [(listing, selected_universe.configuration_hash, observed_at)]
    assert adapter.connected is True
    assert adapter.disconnected is True
    assert engine.disposed is True
    assert json.loads(capsys.readouterr().out) == {
        "configuration_hash": selected_universe.configuration_hash,
        "ingestion_started": False,
        "listing_count": 1,
        "listings": ["ig:demo:CS.D.NZDUSD.CFD.IP"],
        "universe_name": "historical-candidate",
    }


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
            self,
            instrument_ids: Sequence[InstrumentId],
            *,
            exact_epics: Mapping[InstrumentId, Sequence[str]] | None = None,
        ) -> tuple[InstrumentListingReview, ...]:
            assert exact_epics == {}
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
async def test_ibkr_review_preflight_stops_before_adapter_or_database_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "_ig_review_adapter",
        Mock(side_effect=AssertionError("IG adapter must not be composed")),
    )
    settings = Settings(ibkr_gateway_host="127.0.0.1", ibkr_gateway_port=4002, ibkr_client_id=71)
    output = tmp_path / "ibkr-preflight.json"

    await cli._review_instruments(
        settings,
        Mock(spec=Clock),
        catalogue_path=Path("config/capture-ibkr-v1-candidates.toml"),
        output_path=output,
        provider="ibkr",
        environment="paper",
        preflight=True,
    )

    payload = json.loads(output.read_text())
    assert payload["status"] == "OPERATOR_AUTHENTICATION_REQUIRED"
    assert payload["candidate_count"] == 20
    assert payload["external_io_performed"] is False

    with pytest.raises(RuntimeError, match="account-gated"):
        await cli._review_instruments(
            settings,
            Mock(spec=Clock),
            catalogue_path=None,
            output_path=None,
            provider="ibkr",
            environment="paper",
        )


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
async def test_ingestion_synchronises_missing_approved_listing_before_subscribe() -> None:
    universe = load_capture_universe(Path("config/capture-v1.toml"))
    now = datetime(2026, 7, 22, tzinfo=UTC)

    def provider_listing(instrument: Instrument) -> ProviderListing:
        return ProviderListing(
            listing_id=ProviderListingId(
                "ig", "demo", universe.preferred_epics[instrument.instrument_id]
            ),
            instrument_id=instrument.instrument_id,
            display_name=instrument.display_name,
            product_type=ProductType.ROLLING_CFD,
            currency=instrument.quote_currency,
            minimum_deal_size=Decimal("1"),
            price_increment=None,
            valid_from=now,
            valid_to=None,
            metadata_version="test",
        )

    class Store:
        def __init__(self) -> None:
            self.active = [provider_listing(item) for item in universe.instruments[:-1]]
            self.seeded = False

        async def seed_instruments(self, instruments: object) -> None:
            assert tuple(cast(Sequence[Instrument], instruments)) == universe.instruments
            self.seeded = True

        async def active_provider_listings(self, instrument_ids: object) -> tuple[object, ...]:
            del instrument_ids
            return tuple(self.active)

        async def validate_provider_listing(
            self, listing: ProviderListing, **kwargs: object
        ) -> None:
            assert kwargs["universe_hash"] == universe.configuration_hash
            self.active.append(listing)

    missing = provider_listing(universe.instruments[-1])
    adapter = SimpleNamespace(
        discover_capture_universe=AsyncMock(return_value=(missing,)),
    )
    store = Store()
    clock = Mock(spec=Clock)
    clock.now.return_value = now

    listings = await cli._synchronise_capture_universe(
        cast(cli.PostgresAuditStore, store),
        cast(cli.IgDemoMarketDataAdapter, adapter),
        universe,
        cast(Clock, clock),
    )

    assert store.seeded
    assert len(listings) == 7
    adapter.discover_capture_universe.assert_awaited_once()


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
            return SimpleNamespace(status="STOPPED", detail="state=STOPPED; reconnects=0")

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
        "_synchronise_capture_universe",
        AsyncMock(return_value=[object() for _ in range(7)]),
    )
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


@pytest.mark.asyncio
async def test_ingestion_uses_transport_receive_time_as_bar_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEngine:
        async def dispose(self) -> None:
            pass

    class FakeStore:
        def __init__(self) -> None:
            self.health_writes = 0

        async def start_run(self, **kwargs: object) -> str:
            del kwargs
            return "not-a-real-run"

        async def active_provider_listings(self, instrument_ids: object) -> list[object]:
            del instrument_ids
            return [object() for _ in range(7)]

        async def record_adapter_health(self, health: object) -> None:
            del health
            self.health_writes += 1

        async def finish_run(self, run_id: str, **kwargs: object) -> None:
            assert run_id == "not-a-real-run"
            assert kwargs["status"] == "STOPPED"

    transport_time = datetime(2026, 7, 16, 12, 30, tzinfo=UTC)
    record = SimpleNamespace(received_time=transport_time)

    class FakeAdapter:
        async def connect(self) -> None:
            pass

        async def subscribe(self, listings: object) -> None:
            del listings

        async def health(self) -> SimpleNamespace:
            return SimpleNamespace(status="HEALTHY", detail="state=READY; dropped_records=0")

        async def records(self) -> AsyncIterator[SimpleNamespace]:
            yield record
            await asyncio.Event().wait()

        async def disconnect(self) -> None:
            pass

    class FakeService:
        def __init__(self) -> None:
            self.processed: list[object] = []
            self.watermarks: list[datetime] = []

        async def process(self, observed_record: object) -> None:
            self.processed.append(observed_record)

        async def advance_bars(self, watermark: datetime) -> None:
            self.watermarks.append(watermark)

    store = FakeStore()
    adapter = FakeAdapter()
    service = FakeService()
    clock = Mock(spec=Clock)
    clock.now.return_value = datetime(2026, 7, 16, 12, 40, tzinfo=UTC)
    monkeypatch.setattr(cli, "_engine", lambda settings: FakeEngine())
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda engine: store)
    monkeypatch.setattr(cli, "_ig_adapter", lambda settings, selected_clock: adapter)
    monkeypatch.setattr(
        cli,
        "_synchronise_capture_universe",
        AsyncMock(return_value=[object() for _ in range(7)]),
    )
    monkeypatch.setattr(cli, "IngestionService", lambda *args, **kwargs: service)
    monkeypatch.setattr(cli, "_HEALTH_PERSIST_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(
        cli,
        "_capture_universe",
        lambda settings: load_capture_universe(Path("config/capture-v1.toml")),
    )

    await cli._ingest(
        cast(Settings, SimpleNamespace()),
        cast(Clock, clock),
        maximum_seconds=0.05,
    )

    assert service.processed == [record]
    assert service.watermarks == [transport_time]
    assert store.health_writes >= 3


def test_main_does_not_leave_an_event_loop_running(
    monkeypatch: pytest.MonkeyPatch, cli_environment: Settings
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(cli, "_rebuild", operation)

    cli.main(["projections", "rebuild"])

    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()
