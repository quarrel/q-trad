import asyncio
import json
import signal
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from qtrad import __main__ as cli
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import AssetClass, Instrument, ProductType, ProviderListing
from qtrad.domain.r2_ibkr_historical import IBKR_HISTORICAL_SOURCE
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
                ("provider", "ig"),
                ("environment", None),
                ("preflight", False),
                ("probe_spec_path", None),
                ("execute_account_probe", False),
            ),
        ),
        (
            ["ingest", "--max-seconds", "60", "--force-reconnect-after-seconds", "20"],
            "_run_ingest",
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
            [
                "historical",
                "ibkr",
                "register",
                "--plan",
                "ibkr-plan.json",
                "--contract-selection",
                "selection.json",
                "--operator-selection",
                "operator.json",
                "--capability-review",
                "review.json",
                "--catalogue",
                "catalogue.toml",
                "--probe-spec",
                "probe.toml",
                "--runtime-lock",
                "runtime.json",
                "--gateway-archive",
                "gateway.zip",
                "--api-archive",
                "api.zip",
                "--ibc-archive",
                "ibc.zip",
                "--expected-gateway-sha256",
                "a" * 64,
                "--expected-api-sha256",
                "b" * 64,
                "--expected-ibc-sha256",
                "c" * 64,
                "--expected-runtime-qtrad-commit",
                "d" * 40,
                "--expected-runtime-image-digest",
                "sha256:" + "e" * 64,
                "--expected-gateway-version",
                "10.49",
                "--expected-api-version",
                "10.49",
                "--expected-ibc-version",
                "3.24.1",
                "--request-profile",
                "profile.json",
                "--canary-evidence",
                "canary.json",
                "--profile-frozen-by",
                "operator@example.invalid",
                "--profile-frozen-at",
                "2026-08-02T12:00:00Z",
                "--duplicate-request-protection",
                "PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
                "--start",
                "2026-02-01T00:00:00Z",
                "--end",
                "2026-08-02T00:00:00Z",
                "--planner-image-digest",
                "sha256:" + "f" * 64,
                "--confirm-plan-hash",
                "a" * 64,
            ],
            "_register_ibkr_historical_plan",
            (
                ("plan_path", Path("ibkr-plan.json")),
                ("confirmed_plan_hash", "a" * 64),
                ("contract_selection_path", Path("selection.json")),
                ("operator_selection_path", Path("operator.json")),
                ("capability_review_path", Path("review.json")),
                ("catalogue_path", Path("catalogue.toml")),
                ("probe_spec_path", Path("probe.toml")),
                ("runtime_lock_path", Path("runtime.json")),
                ("gateway_archive", Path("gateway.zip")),
                ("api_archive", Path("api.zip")),
                ("ibc_archive", Path("ibc.zip")),
                ("expected_gateway_sha256", "a" * 64),
                ("expected_api_sha256", "b" * 64),
                ("expected_ibc_sha256", "c" * 64),
                ("expected_runtime_qtrad_commit", "d" * 40),
                ("expected_runtime_image_digest", "sha256:" + "e" * 64),
                ("expected_gateway_version", "10.49"),
                ("expected_api_version", "10.49"),
                ("expected_ibc_version", "3.24.1"),
                ("expected_api_host", "127.0.0.1"),
                ("expected_api_port", 4002),
                ("expected_client_id_policy", "DEDICATED_NONZERO_CLIENT_ID"),
                ("request_profile_path", Path("profile.json")),
                ("canary_evidence_path", Path("canary.json")),
                ("expected_profile_frozen_by", "operator@example.invalid"),
                ("expected_profile_frozen_at", datetime(2026, 8, 2, 12, tzinfo=UTC)),
                ("maximum_in_flight_requests", 1),
                ("request_timeout_seconds", 60),
                ("retry_count", 1),
                ("duplicate_request_protection", "PLAN_REQUEST_ID_UNIQUE_NO_RERUN"),
                ("identical_request_cooldown_seconds", 15),
                ("max_requests_per_contract_window", 5),
                ("max_requests_per_rolling_window", 55),
                ("expected_start", datetime(2026, 2, 1, tzinfo=UTC)),
                ("expected_end", datetime(2026, 8, 2, tzinfo=UTC)),
                ("planner_image_digest", "sha256:" + "f" * 64),
            ),
        ),
        (
            [
                "historical",
                "ibkr",
                "execute",
                "--plan-id",
                "a" * 64,
                "--contract-selection",
                "selection.json",
                "--operator-selection",
                "operator.json",
                "--capability-review",
                "review.json",
                "--catalogue",
                "catalogue.toml",
                "--probe-spec",
                "probe.toml",
                "--runtime-lock",
                "runtime.json",
                "--gateway-archive",
                "gateway.zip",
                "--api-archive",
                "api.zip",
                "--ibc-archive",
                "ibc.zip",
                "--expected-gateway-sha256",
                "a" * 64,
                "--expected-api-sha256",
                "b" * 64,
                "--expected-ibc-sha256",
                "c" * 64,
                "--request-profile",
                "request-profile.json",
                "--canary-evidence",
                "canary.json",
                "--profile-frozen-by",
                "operator@example.invalid",
                "--profile-frozen-at",
                "2026-08-02T12:00:00Z",
                "--duplicate-request-protection",
                "PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
                "--start",
                "2026-02-01T00:00:00Z",
                "--end",
                "2026-08-02T00:00:00Z",
            ],
            "_execute_ibkr_historical_plan",
            (
                ("plan_id", "a" * 64),
                ("contract_selection_path", Path("selection.json")),
                ("operator_selection_path", Path("operator.json")),
                ("capability_review_path", Path("review.json")),
                ("catalogue_path", Path("catalogue.toml")),
                ("probe_spec_path", Path("probe.toml")),
                ("runtime_lock_path", Path("runtime.json")),
                ("gateway_archive", Path("gateway.zip")),
                ("api_archive", Path("api.zip")),
                ("ibc_archive", Path("ibc.zip")),
                ("expected_gateway_sha256", "a" * 64),
                ("expected_api_sha256", "b" * 64),
                ("expected_ibc_sha256", "c" * 64),
                ("request_profile_path", Path("request-profile.json")),
                ("canary_evidence_path", Path("canary.json")),
                ("expected_profile_frozen_by", "operator@example.invalid"),
                ("expected_profile_frozen_at", datetime(2026, 8, 2, 12, tzinfo=UTC)),
                ("maximum_in_flight_requests", 1),
                ("request_timeout_seconds", 60),
                ("retry_count", 1),
                ("duplicate_request_protection", "PLAN_REQUEST_ID_UNIQUE_NO_RERUN"),
                ("identical_request_cooldown_seconds", 15),
                ("max_requests_per_contract_window", 5),
                ("max_requests_per_rolling_window", 55),
                ("expected_start", datetime(2026, 2, 1, tzinfo=UTC)),
                ("expected_end", datetime(2026, 8, 2, tzinfo=UTC)),
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


def test_ibkr_historical_endpoint_uses_dedicated_client_id() -> None:
    endpoint = cli._ibkr_historical_endpoint(
        Settings(ibkr_client_id=71, ibkr_historical_client_id=72)
    )

    assert endpoint.client_id == 72


@pytest.mark.asyncio
async def test_ibkr_review_preflight_stops_before_adapter_or_database_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "_ig_review_adapter",
        Mock(side_effect=AssertionError("IG adapter must not be composed")),
    )
    settings = Settings(
        ibkr_gateway_host="127.0.0.1",
        ibkr_gateway_port=4002,
        ibkr_client_id=71,
        ibkr_historical_client_id=72,
        ibkr_api_package_fingerprint="a" * 64,
    )
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
    assert payload["gateway"]["client_id"] == 72

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
async def test_ibkr_account_probe_requires_explicit_execution_and_writes_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from qtrad.ports.ibkr_capability import (
        IbkrCandidateCapability,
        IbkrContractEvidence,
        IbkrContractQuery,
        IbkrRequestEvidence,
    )

    class FakeAdapter:
        def __init__(self) -> None:
            self.connected = False
            self.disconnected = False

        async def connect(self) -> None:
            self.connected = True

        async def disconnect(self) -> None:
            self.disconnected = True

        async def probe(
            self, queries: Sequence[IbkrContractQuery]
        ) -> tuple[IbkrCandidateCapability, ...]:
            return tuple(
                IbkrCandidateCapability(
                    query=query,
                    contracts=(
                        IbkrContractEvidence(
                            con_id=index + 1,
                            symbol=query.symbol,
                            local_symbol=query.symbol,
                            security_type=query.security_type,
                            exchange=query.exchange,
                            currency=query.currency,
                            trading_class=None,
                            multiplier=None,
                            minimum_tick=Decimal("0.01"),
                            market_rule_ids=(),
                            valid_exchanges=(query.exchange,),
                            long_name=None,
                            underlier_con_id=None,
                            timezone=None,
                            trading_hours=None,
                            liquid_hours=None,
                        ),
                    ),
                    requests=(
                        IbkrRequestEvidence(
                            kind="CONTRACT_DETAILS", status="SUCCESS", latency_milliseconds=1
                        ),
                    ),
                )
                for index, query in enumerate(queries)
            )

    adapter = FakeAdapter()
    monkeypatch.setattr(cli, "_ibkr_capability_adapter", lambda settings, **kwargs: adapter)
    execution_lock = object()
    acquire_lock = AsyncMock(return_value=execution_lock)
    release_lock = AsyncMock()
    monkeypatch.setattr(cli, "_acquire_ibkr_historical_execution_lock", acquire_lock)
    monkeypatch.setattr(cli, "_release_ibkr_historical_execution_lock", release_lock)
    catalogue = cli.load_capture_candidates(Path("config/capture-ibkr-v1-candidates.toml"))
    probe_spec = tmp_path / "operator-probe.toml"
    probe_spec.write_text(
        'schema_version = 1\nname = "operator-probe-v1"\n'
        + "\n".join(
            "[[query]]\n"
            f'instrument_id = "{instrument.instrument_id}"\n'
            'symbol = "TEST"\n'
            'security_type = "IND"\n'
            'exchange = "SMART"\n'
            'currency = "USD"\n'
            for instrument in catalogue.instruments
        ),
        encoding="utf-8",
    )
    output = tmp_path / "ibkr-review.json"
    clock = Mock(spec=Clock)
    clock.now.return_value = datetime(2026, 7, 29, tzinfo=UTC)
    settings = Settings(
        ibkr_gateway_host="127.0.0.1",
        ibkr_gateway_port=4002,
        ibkr_client_id=71,
        ibkr_historical_client_id=72,
        ibkr_api_package_fingerprint="a" * 64,
        ibkr_checkpoint_root=tmp_path / "checkpoints",
    )

    await cli._review_instruments(
        settings,
        clock,
        catalogue_path=Path("config/capture-ibkr-v1-candidates.toml"),
        output_path=output,
        provider="ibkr",
        environment="paper",
        probe_spec_path=probe_spec,
        execute_account_probe=True,
    )

    payload = json.loads(output.read_text())
    assert adapter.connected is True
    assert adapter.disconnected is True
    assert payload["external_io_performed"] is True
    assert payload["selection_authority"] is False
    assert len(payload["instruments"]) == 20
    acquire_lock.assert_awaited_once()
    release_lock.assert_awaited_once_with(execution_lock)


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


@pytest.mark.asyncio
async def test_sigterm_cancels_ingestion_for_orderly_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    handlers: list[Callable[[], None]] = []
    removed: list[int] = []
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def ingest(*args: object, **kwargs: object) -> None:
        del args, kwargs
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()

    def add_signal_handler(signum: int, callback: Callable[[], None], *args: object) -> None:
        assert signum == signal.SIGTERM
        assert args == ()
        handlers.append(callback)

    def remove_signal_handler(signum: int) -> bool:
        removed.append(signum)
        return True

    monkeypatch.setattr(cli, "_ingest", ingest)
    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)

    task = asyncio.create_task(
        cli._run_ingest(
            cast(Settings, SimpleNamespace()),
            Mock(spec=Clock),
        )
    )
    await started.wait()
    assert len(handlers) == 1

    handlers[0]()
    await asyncio.wait_for(task, timeout=1)

    assert stopped.is_set()
    assert removed == [signal.SIGTERM]


def test_parser_rejects_non_demo_ingestion_environment() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ingest", "--environment", "live"])


def test_parser_accepts_r2_replay_and_rejects_retired_software_operations() -> None:
    parser = cli.build_parser()

    oof = parser.parse_args(
        [
            "research",
            "baselines",
            "oof-build",
            "--foundation-bundle",
            "foundation.json",
            "--foundation-receipt",
            "foundation-receipt.json",
            "--experiment",
            "experiment.json",
            "--feature-manifest",
            "L0=l0.json",
            "--feature-manifest",
            "L1=l1.json",
            "--feature-manifest",
            "P0=p0.json",
            "--feature-manifest",
            "P1=p1.json",
            "--output",
            "run",
        ]
    )

    assert oof.baselines_command == "oof-build"
    assert oof.feature_manifest == ["L0=l0.json", "L1=l1.json", "P0=p0.json", "P1=p1.json"]
    oof_verify = parser.parse_args(
        [
            "research",
            "baselines",
            "oof-verify",
            "--bundle",
            "oof",
            "--receipt-output",
            "oof-receipt.json",
        ]
    )
    assert oof_verify.receipt_output == Path("oof-receipt.json")
    with pytest.raises(SystemExit):
        parser.parse_args(["research", "baselines", "oof-verify", "--bundle", "oof"])
    target_source = parser.parse_args(
        [
            "research",
            "baselines",
            "holdout-target-source",
            "--foundation-bundle",
            "foundation.json",
            "--foundation-receipt",
            "foundation-receipt.json",
            "--foundation-promotion",
            "foundation-promotion.json",
            "--experiment",
            "experiment.json",
            "--output",
            "target-source.json",
        ]
    )
    assert target_source.baselines_command == "holdout-target-source"
    assert target_source.foundation_promotion == Path("foundation-promotion.json")
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "research",
                "baselines",
                "software-verify",
                "--bundle",
                "software/manifest.json",
            ]
        )


def test_oof_verify_dispatches_semantic_receipt_output(
    monkeypatch: pytest.MonkeyPatch, cli_environment: Settings, cli_clock: Clock
) -> None:
    bundle = SimpleNamespace(as_json=lambda: {"contract": "oof"})
    verify = Mock(return_value=bundle)
    monkeypatch.setattr(cli, "verify_r2_oof_semantics", verify)

    cli.main(
        [
            "research",
            "baselines",
            "oof-verify",
            "--bundle",
            "bundle",
            "--receipt-output",
            "receipt.json",
        ]
    )

    verify.assert_called_once_with(Path("bundle"), receipt_output=Path("receipt.json"))


def test_holdout_target_source_dispatches_create_only_output(
    monkeypatch: pytest.MonkeyPatch, cli_environment: Settings, cli_clock: Clock
) -> None:
    holdout_range = ("start", "end")
    target_instruments = ("fx:aud-usd",)
    experiment = SimpleNamespace(
        market_data_source_class=IBKR_HISTORICAL_SOURCE,
        r1_bundle_id="foundation",
        target_dataset_id="target",
        observation_dataset_id="observations",
        foundation_configuration_id="configuration",
        holdout_range=holdout_range,
        primary_horizon=timedelta(minutes=15),
        target_instruments=target_instruments,
    )
    source_json = {"contract": "qtrad-r2-holdout-target-source-v1"}
    source = SimpleNamespace(
        CONTRACT=source_json["contract"],
        source_target_dataset_id="target",
        observation_dataset_id="observations",
        foundation_configuration_id="configuration",
        holdout_range=holdout_range,
        primary_horizon_seconds=900,
        target_instruments=target_instruments,
        source_id="source",
        targets=(object(),),
        opportunities=(object(),),
        pre_holdout_target_dataset=SimpleNamespace(rows=(object(),)),
        as_json=Mock(return_value=source_json),
    )
    monkeypatch.setattr(cli, "load_r2_experiment", lambda _path: experiment)
    monkeypatch.setattr(
        cli,
        "authenticate_ibkr_foundation_promotion",
        Mock(return_value=SimpleNamespace(foundation_bundle_id="foundation")),
    )
    build = Mock(return_value=source)
    monkeypatch.setattr(cli, "build_ibkr_holdout_target_source", build)
    canonical = Mock(return_value=b"source-bytes")
    publish = Mock()
    monkeypatch.setattr(cli, "canonical_bytes", canonical)
    monkeypatch.setattr(cli, "atomic_create", publish)

    cli.main(
        [
            "research",
            "baselines",
            "holdout-target-source",
            "--foundation-bundle",
            "foundation.json",
            "--foundation-receipt",
            "receipt.json",
            "--foundation-promotion",
            "promotion.json",
            "--experiment",
            "experiment.json",
            "--output",
            "source.json",
        ]
    )

    build.assert_called_once_with(
        Path("foundation.json"),
        receipt=Path("receipt.json"),
        target_instruments=target_instruments,
    )
    canonical.assert_called_once_with(source_json)
    publish.assert_called_once_with(Path("source.json"), b"source-bytes")


@pytest.mark.asyncio
async def test_oof_build_requires_foundation_receipt_and_holdout_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish = Mock(side_effect=AssertionError("OOF publish reached"))
    monkeypatch.setattr(
        cli,
        "load_experiment_and_feature_paths",
        lambda **_kwargs: (SimpleNamespace(market_data_source_class=object()), {}),
    )
    monkeypatch.setattr(cli, "build_oof_bundle", publish)
    output = tmp_path / "oof"

    with pytest.raises(
        ValueError, match="OOF build requires an authenticated holdout target source"
    ):
        await cli._build_r2_oof(
            cast(Settings, SimpleNamespace()),
            cast(Clock, SimpleNamespace()),
            foundation_bundle_path=tmp_path / "foundation.json",
            foundation_receipt_path=tmp_path / "receipt.json",
            experiment_path=tmp_path / "experiment.json",
            feature_arguments=[],
            output_path=output,
            holdout_target_source_path=None,
        )

    publish.assert_not_called()
    assert not output.exists()


def test_cli_rejects_manufactured_software_input(
    tmp_path: Path,
    cli_environment: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del cli_environment, capsys
    from qtrad.runtime.r2_bundles import write_r2_oof_bundle
    from qtrad.runtime.r2_verification import selection_freeze
    from tests.test_r2_bundles import _bundle_and_children

    representative_root = tmp_path / "representative"
    representative_root.mkdir()
    representative_bundle, children = _bundle_and_children()
    representative_manifest = write_r2_oof_bundle(
        representative_root, representative_bundle, children
    )
    with pytest.raises(ValueError, match="descriptor"):
        selection_freeze(
            oof_bundle_path=representative_manifest,
            frozen_by="cli-test",
            output=representative_root / "selection.json",
        )


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


def _ibkr_plan_cli_arguments(command: str) -> list[str]:
    arguments = [
        "historical",
        "ibkr",
        command,
        "--contract-selection",
        "selection.json",
        "--operator-selection",
        "operator.json",
        "--capability-review",
        "review.json",
        "--catalogue",
        "catalogue.toml",
        "--probe-spec",
        "probe.toml",
        "--runtime-lock",
        "runtime.json",
        "--gateway-archive",
        "gateway.zip",
        "--api-archive",
        "api.zip",
        "--ibc-archive",
        "ibc.zip",
        "--expected-gateway-sha256",
        "a" * 64,
        "--expected-api-sha256",
        "b" * 64,
        "--expected-ibc-sha256",
        "c" * 64,
        "--expected-runtime-qtrad-commit",
        "d" * 40,
        "--expected-runtime-image-digest",
        "sha256:" + "e" * 64,
        "--expected-gateway-version",
        "10.49",
        "--expected-api-version",
        "10.49",
        "--expected-ibc-version",
        "3.24.1",
        "--request-profile",
        "profile.json",
        "--canary-evidence",
        "canary.json",
        "--profile-frozen-by",
        "operator@example.invalid",
        "--profile-frozen-at",
        "2026-08-02T12:00:00Z",
        "--planner-image-digest",
        "sha256:" + "f" * 64,
        "--start",
        "2026-02-01T00:00:00Z",
        "--end",
        "2026-08-02T00:00:00Z",
    ]
    if command == "plan":
        arguments.extend(["--output", "plan.json"])
    else:
        arguments.extend(["--plan", "plan.json"])
    return arguments


def test_ibkr_historical_plan_dispatches_without_provider_or_database_access(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_plan_ibkr_historical", operation)

    cli.main(_ibkr_plan_cli_arguments("plan"))

    operation.assert_called_once_with(
        contract_selection_path=Path("selection.json"),
        operator_selection_path=Path("operator.json"),
        capability_review_path=Path("review.json"),
        catalogue_path=Path("catalogue.toml"),
        probe_spec_path=Path("probe.toml"),
        runtime_lock_path=Path("runtime.json"),
        gateway_archive=Path("gateway.zip"),
        api_archive=Path("api.zip"),
        ibc_archive=Path("ibc.zip"),
        expected_gateway_sha256="a" * 64,
        expected_api_sha256="b" * 64,
        expected_ibc_sha256="c" * 64,
        expected_runtime_qtrad_commit="d" * 40,
        expected_runtime_image_digest="sha256:" + "e" * 64,
        expected_gateway_version="10.49",
        expected_api_version="10.49",
        expected_ibc_version="3.24.1",
        expected_api_host="127.0.0.1",
        expected_api_port=4002,
        expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        request_profile_path=Path("profile.json"),
        canary_evidence_path=Path("canary.json"),
        expected_profile_frozen_by="operator@example.invalid",
        expected_profile_frozen_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
        maximum_in_flight_requests=1,
        request_timeout_seconds=60,
        retry_count=1,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        identical_request_cooldown_seconds=15,
        max_requests_per_contract_window=5,
        max_requests_per_rolling_window=55,
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 8, 2, tzinfo=UTC),
        planner_image_digest="sha256:" + "f" * 64,
        output_path=Path("plan.json"),
    )
    assert capsys.readouterr().out == ""


def test_ibkr_historical_plan_verify_dispatches_without_provider_or_database_access(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del cli_environment
    operation = Mock()
    monkeypatch.setattr(cli, "_verify_ibkr_historical_plan", operation)

    cli.main(_ibkr_plan_cli_arguments("plan-verify"))

    operation.assert_called_once_with(
        plan_path=Path("plan.json"),
        contract_selection_path=Path("selection.json"),
        operator_selection_path=Path("operator.json"),
        capability_review_path=Path("review.json"),
        catalogue_path=Path("catalogue.toml"),
        probe_spec_path=Path("probe.toml"),
        runtime_lock_path=Path("runtime.json"),
        gateway_archive=Path("gateway.zip"),
        api_archive=Path("api.zip"),
        ibc_archive=Path("ibc.zip"),
        expected_gateway_sha256="a" * 64,
        expected_api_sha256="b" * 64,
        expected_ibc_sha256="c" * 64,
        expected_runtime_qtrad_commit="d" * 40,
        expected_runtime_image_digest="sha256:" + "e" * 64,
        expected_gateway_version="10.49",
        expected_api_version="10.49",
        expected_ibc_version="3.24.1",
        expected_api_host="127.0.0.1",
        expected_api_port=4002,
        expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        request_profile_path=Path("profile.json"),
        canary_evidence_path=Path("canary.json"),
        expected_profile_frozen_by="operator@example.invalid",
        expected_profile_frozen_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
        maximum_in_flight_requests=1,
        request_timeout_seconds=60,
        retry_count=1,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        identical_request_cooldown_seconds=15,
        max_requests_per_contract_window=5,
        max_requests_per_rolling_window=55,
        expected_start=datetime(2026, 2, 1, tzinfo=UTC),
        expected_end=datetime(2026, 8, 2, tzinfo=UTC),
        planner_image_digest="sha256:" + "f" * 64,
    )
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_ibkr_register_replays_closure_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = Mock()
    monkeypatch.setattr(
        cli,
        "load_ibkr_historical_plan_artifact",
        Mock(return_value=(plan, b"plan")),
    )
    verifier = Mock(side_effect=ValueError("closure rejected"))
    monkeypatch.setattr(cli, "verify_ibkr_historical_plan_closure", verifier)
    monkeypatch.setattr(cli, "derive_qtrad_commit", Mock(return_value="d" * 40))
    require_database = AsyncMock(side_effect=AssertionError("database access was premature"))
    engine_factory = Mock(side_effect=AssertionError("database engine was premature"))
    monkeypatch.setattr(cli, "_require_database_at_migration_head", require_database)
    monkeypatch.setattr(cli, "_engine", engine_factory)

    arguments = {
        "plan_path": tmp_path / "plan.json",
        "confirmed_plan_hash": "a" * 64,
        "contract_selection_path": tmp_path / "selection.json",
        "operator_selection_path": tmp_path / "operator.json",
        "capability_review_path": tmp_path / "review.json",
        "catalogue_path": tmp_path / "catalogue.toml",
        "probe_spec_path": tmp_path / "probe.toml",
        "runtime_lock_path": tmp_path / "runtime.json",
        "gateway_archive": tmp_path / "gateway.zip",
        "api_archive": tmp_path / "api.zip",
        "ibc_archive": tmp_path / "ibc.zip",
        "expected_gateway_sha256": "a" * 64,
        "expected_api_sha256": "b" * 64,
        "expected_ibc_sha256": "c" * 64,
        "expected_runtime_qtrad_commit": "d" * 40,
        "expected_runtime_image_digest": "sha256:" + "e" * 64,
        "expected_gateway_version": "10.49",
        "expected_api_version": "10.49",
        "expected_ibc_version": "3.24.1",
        "expected_api_host": "127.0.0.1",
        "expected_api_port": 4002,
        "expected_client_id_policy": "DEDICATED_NONZERO_CLIENT_ID",
        "request_profile_path": tmp_path / "profile.json",
        "canary_evidence_path": tmp_path / "canary.json",
        "expected_profile_frozen_by": "operator@example.invalid",
        "expected_profile_frozen_at": datetime(2026, 8, 2, 12, tzinfo=UTC),
        "maximum_in_flight_requests": 1,
        "request_timeout_seconds": 60,
        "retry_count": 1,
        "duplicate_request_protection": "PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        "identical_request_cooldown_seconds": 15,
        "max_requests_per_contract_window": 5,
        "max_requests_per_rolling_window": 55,
        "expected_start": datetime(2026, 2, 1, tzinfo=UTC),
        "expected_end": datetime(2026, 8, 2, tzinfo=UTC),
        "planner_image_digest": "sha256:" + "f" * 64,
    }

    with pytest.raises(ValueError, match="closure rejected"):
        await cli._register_ibkr_historical_plan(
            cast(Settings, SimpleNamespace()),
            cast(Clock, Mock()),
            **arguments,
        )

    verifier.assert_called_once()
    require_database.assert_not_awaited()
    engine_factory.assert_not_called()


def _canary_run_arguments(*, safety_flag: bool = True) -> list[str]:
    arguments = [
        "historical",
        "ibkr",
        "canary-run",
        "--runtime-lock",
        "runtime.json",
        "--contract-selection",
        "selection.json",
        "--fx-representative-id",
        "fx:eur-usd",
        "--index-representative-id",
        "index:spx",
        "--commodity-representative-id",
        "commodity:spot-gold",
        "--anchor-end",
        "2026-08-05T12:00:00Z",
        "--output",
        "canary.json",
    ]
    if safety_flag:
        arguments.append("--execute-account-canary")
    return arguments


@pytest.mark.parametrize(
    ("command", "command_arguments"),
    (
        (
            "ibkr-qualification-verify",
            ["--qualification", "qualification.json"],
        ),
        (
            "ibkr-qualification-snapshot",
            [
                "--capture-session-id",
                "50838048-5590-4a77-9844-b50cbe8baf81",
                "--started-at",
                "2026-08-10T09:23:52Z",
                "--ended-at",
                "2026-08-10T09:25:53Z",
                "--generated-at",
                "2026-08-10T09:26:55Z",
                "--output",
                "qualification.json",
            ],
        ),
    ),
)
def test_ibkr_qualification_parser_accepts_exact_b4_policy(
    command: str, command_arguments: list[str]
) -> None:
    parsed = cli.build_parser().parse_args(
        [
            "deployment",
            command,
            "--policy",
            "b4-exact-six",
            *command_arguments,
            "--release",
            "release.json",
            "--descriptor",
            "descriptor.toml",
            "--capability-review",
            "review.json",
            "--operator-selection",
            "operator.json",
            "--contract-selection",
            "contracts.json",
            "--catalogue",
            "catalogue.toml",
            "--probe-spec",
            "probe.toml",
        ]
    )

    assert parsed.policy == "b4-exact-six"


def test_canary_run_parser_requires_explicit_inputs() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["historical", "ibkr", "canary-run"])
    arguments = parser.parse_args(_canary_run_arguments())
    assert arguments.runtime_lock == Path("runtime.json")
    assert arguments.contract_selection == Path("selection.json")
    assert arguments.anchor_end == datetime(2026, 8, 5, 12, tzinfo=UTC)
    assert arguments.execute_account_canary is True


def test_canary_run_dispatch_preserves_safety_flag(
    monkeypatch: pytest.MonkeyPatch,
    cli_environment: Settings,
    cli_clock: Clock,
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(cli, "_run_ibkr_historical_canary", operation)

    cli.main(_canary_run_arguments(safety_flag=False))

    operation.assert_awaited_once()
    assert operation.await_args is not None
    await_args = operation.await_args
    assert await_args.kwargs["execute_account_canary"] is False
    assert await_args.kwargs["fx_representative_id"] == "fx:eur-usd"


def test_canary_run_rejects_existing_output_before_provider_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "canary.json"
    output_path.write_text("existing", encoding="utf-8")
    operation = AsyncMock()
    monkeypatch.setattr(cli, "load_ibkr_runtime_lock", operation)

    with pytest.raises(FileExistsError, match="already exists"):
        asyncio.run(
            cli._run_ibkr_historical_canary(
                cast(Settings, SimpleNamespace()),
                cast(Clock, SimpleNamespace()),
                runtime_lock_path=tmp_path / "runtime.json",
                contract_selection_path=tmp_path / "selection.json",
                fx_representative_id="fx:eur-usd",
                index_representative_id="index:spx",
                commodity_representative_id="commodity:spot-gold",
                anchor_end=datetime(2026, 8, 5, 12, tzinfo=UTC),
                output_path=output_path,
                execute_account_canary=True,
            )
        )
    operation.assert_not_awaited()


def test_canary_run_requires_immutable_ibkr_image_before_database_or_provider_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(qtrad_image_digest="sha256:" + "e" * 64)
    load_runtime = Mock(return_value=runtime)
    load_selection = Mock()
    database_gate = AsyncMock()
    engine_factory = Mock()
    adapter_factory = Mock()

    def missing_image() -> str:
        raise RuntimeError("QTRAD_IMAGE_DIGEST is required")

    monkeypatch.setattr(cli, "load_ibkr_runtime_lock", load_runtime)
    monkeypatch.setattr(cli, "load_ibkr_contract_selection", load_selection)
    monkeypatch.setattr(cli, "configured_image_digest", missing_image)
    monkeypatch.setattr(cli, "_require_database_at_migration_head", database_gate)
    monkeypatch.setattr(cli, "_engine", engine_factory)
    monkeypatch.setattr(cli, "_ibkr_historical_canary_adapter", adapter_factory)

    with pytest.raises(RuntimeError, match="QTRAD_IMAGE_DIGEST"):
        asyncio.run(
            cli._run_ibkr_historical_canary(
                cast(Settings, SimpleNamespace()),
                cast(Clock, SimpleNamespace()),
                runtime_lock_path=tmp_path / "runtime.json",
                contract_selection_path=tmp_path / "selection.json",
                fx_representative_id="fx:eur-usd",
                index_representative_id="index:australia-200",
                commodity_representative_id="commodity:spot-gold",
                anchor_end=datetime(2026, 8, 5, 12, tzinfo=UTC),
                output_path=tmp_path / "canary.json",
                execute_account_canary=True,
            )
        )

    load_runtime.assert_called_once()
    load_selection.assert_not_called()
    database_gate.assert_not_awaited()
    engine_factory.assert_not_called()
    adapter_factory.assert_not_called()
    assert not (tmp_path / "canary.json").exists()


@pytest.mark.parametrize(
    ("image_reference", "error"),
    [
        (
            "qtrad-ibkr@sha256:" + "f" * 64,
            "locked runtime image digest",
        ),
        ("qtrad-app@sha256:" + "e" * 64, "qtrad-ibkr@sha256"),
    ],
)
def test_canary_run_rejects_mismatched_ibkr_image_before_database_or_provider_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    image_reference: str,
    error: str,
) -> None:
    runtime = SimpleNamespace(qtrad_image_digest="sha256:" + "e" * 64)
    load_runtime = Mock(return_value=runtime)
    load_selection = Mock()
    database_gate = AsyncMock()
    engine_factory = Mock()
    adapter_factory = Mock()

    monkeypatch.setattr(cli, "load_ibkr_runtime_lock", load_runtime)
    monkeypatch.setattr(cli, "load_ibkr_contract_selection", load_selection)
    monkeypatch.setattr(cli, "configured_image_reference", lambda: image_reference)
    monkeypatch.setattr(cli, "_require_database_at_migration_head", database_gate)
    monkeypatch.setattr(cli, "_engine", engine_factory)
    monkeypatch.setattr(cli, "_ibkr_historical_canary_adapter", adapter_factory)

    with pytest.raises(ValueError, match=error):
        asyncio.run(
            cli._run_ibkr_historical_canary(
                cast(Settings, SimpleNamespace()),
                cast(Clock, SimpleNamespace()),
                runtime_lock_path=tmp_path / "runtime.json",
                contract_selection_path=tmp_path / "selection.json",
                fx_representative_id="fx:eur-usd",
                index_representative_id="index:australia-200",
                commodity_representative_id="commodity:spot-gold",
                anchor_end=datetime(2026, 8, 5, 12, tzinfo=UTC),
                output_path=tmp_path / "canary.json",
                execute_account_canary=True,
            )
        )

    load_runtime.assert_called_once()
    load_selection.assert_not_called()
    database_gate.assert_not_awaited()
    engine_factory.assert_not_called()
    adapter_factory.assert_not_called()
    assert not (tmp_path / "canary.json").exists()


def test_canary_run_rejects_ancestor_symlink_before_provider_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(outside, target_is_directory=True)
    load_runtime = Mock()
    monkeypatch.setattr(cli, "load_ibkr_runtime_lock", load_runtime)

    with pytest.raises(ValueError, match="symlink"):
        asyncio.run(
            cli._run_ibkr_historical_canary(
                cast(Settings, SimpleNamespace()),
                cast(Clock, SimpleNamespace()),
                runtime_lock_path=tmp_path / "runtime.json",
                contract_selection_path=tmp_path / "selection.json",
                fx_representative_id="fx:eur-usd",
                index_representative_id="index:australia-200",
                commodity_representative_id="commodity:spot-gold",
                anchor_end=datetime(2026, 8, 5, 12, tzinfo=UTC),
                output_path=linked_root / "canary.json",
                execute_account_canary=True,
            )
        )

    load_runtime.assert_not_called()


@pytest.mark.asyncio
async def test_canary_run_composes_twelve_cases_with_anchor_and_immutable_hashes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(
        gateway_version="10.49",
        api_version="10.49",
        api_host="127.0.0.1",
        api_port=4002,
        client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        runtime_sha256="a" * 64,
        qtrad_image_digest="sha256:" + "e" * 64,
    )
    selection = SimpleNamespace(
        api_version="10.49",
        api_package_fingerprint="b" * 64,
        selection_sha256="c" * 64,
    )
    anchor_end = datetime(2026, 8, 5, 12, tzinfo=UTC)
    cases = tuple(range(12))
    evidence = SimpleNamespace(evidence_sha256="d" * 64)
    built: dict[str, object] = {}
    runner = AsyncMock(return_value=evidence)
    writer = Mock()
    disposed = False
    adapter = object()
    execution_lock = object()
    acquire_lock = AsyncMock(return_value=execution_lock)
    release_lock = AsyncMock()

    class FakeEngine:
        async def dispose(self) -> None:
            nonlocal disposed
            disposed = True

    class FakePacing:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs

        async def reserve(
            self, request_kind: str, contract_key: str, request_fingerprint: str, weight: int
        ) -> float:
            del request_kind, contract_key, request_fingerprint, weight
            return 0.0

    def build_cases(representatives: object, *, anchor_end: datetime) -> tuple[int, ...]:
        built["representatives"] = representatives
        built["anchor_end"] = anchor_end
        return cases

    monkeypatch.setattr(cli, "load_ibkr_runtime_lock", lambda path: runtime)
    monkeypatch.setattr(
        cli,
        "configured_image_reference",
        lambda: "syd.ocir.io/sdctwkrifhgw/qtrad/qtrad-ibkr@sha256:" + "e" * 64,
    )
    monkeypatch.setattr(cli, "load_ibkr_contract_selection", lambda path: selection)
    monkeypatch.setattr(
        cli,
        "ibkr_historical_selection_asset_classes",
        lambda value: {InstrumentId("fx:eur-usd"): AssetClass.FX},
    )
    monkeypatch.setattr(
        cli,
        "validate_ibkr_historical_canary_representatives",
        lambda value, *, representatives: representatives,
    )
    monkeypatch.setattr(cli, "build_adjacent_ibkr_canary_cases", build_cases)
    monkeypatch.setattr(cli, "_require_database_at_migration_head", AsyncMock())
    monkeypatch.setattr(cli, "_engine", lambda settings: FakeEngine())
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda engine: object())
    monkeypatch.setattr(cli, "_acquire_ibkr_historical_execution_lock", acquire_lock)
    monkeypatch.setattr(cli, "_release_ibkr_historical_execution_lock", release_lock)
    monkeypatch.setattr(
        "qtrad.adapters.ibkr.pacing.IbkrPostgresPacing",
        FakePacing,
    )
    monkeypatch.setattr(
        cli,
        "_ibkr_historical_canary_adapter",
        lambda settings, *, pacing_reserver, clock: adapter,
    )
    monkeypatch.setattr(cli, "run_ibkr_historical_canary", runner)
    monkeypatch.setattr(cli, "validate_ibkr_historical_canary_selection", Mock())
    monkeypatch.setattr(cli, "write_ibkr_historical_canary_evidence", writer)

    settings = Settings(
        ibkr_api_package_fingerprint="b" * 64,
        ibkr_historical_client_id=72,
        ibkr_checkpoint_root=tmp_path,
    )
    clock = cast(Clock, SimpleNamespace(now=lambda: anchor_end))
    await cli._run_ibkr_historical_canary(
        settings,
        clock,
        runtime_lock_path=tmp_path / "runtime.json",
        contract_selection_path=tmp_path / "selection.json",
        fx_representative_id="fx:eur-usd",
        index_representative_id="index:australia-200",
        commodity_representative_id="commodity:spot-gold",
        anchor_end=anchor_end,
        output_path=tmp_path / "canary.json",
        execute_account_canary=True,
    )

    assert len(cases) == 12
    assert built["anchor_end"] == anchor_end
    assert runner.await_args is not None
    assert runner.await_args.args == (adapter, cases)
    assert runner.await_args.kwargs["runtime_sha256"] == "a" * 64
    assert runner.await_args.kwargs["selection_sha256"] == "c" * 64
    assert runner.await_args.kwargs["clock"] is clock.now
    assert writer.call_args is not None
    writer_call = writer.call_args
    assert writer_call.args == (tmp_path / "canary.json", evidence)
    assert writer_call.kwargs["reservation"].path == tmp_path / "canary.json"
    assert disposed is True
    acquire_lock.assert_awaited_once()
    release_lock.assert_awaited_once_with(execution_lock)


@pytest.mark.asyncio
async def test_canary_run_composition_failure_writes_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(
        gateway_version="10.49",
        api_version="10.49",
        api_host="127.0.0.1",
        api_port=4002,
        client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        runtime_sha256="a" * 64,
        qtrad_image_digest="sha256:" + "e" * 64,
    )
    selection = SimpleNamespace(
        api_version="10.49",
        api_package_fingerprint="b" * 64,
        selection_sha256="c" * 64,
    )
    output = tmp_path / "failed-canary.json"
    writer = Mock()
    engine_disposed = False
    execution_lock = object()
    acquire_lock = AsyncMock(return_value=execution_lock)
    release_lock = AsyncMock()

    class FakeEngine:
        async def dispose(self) -> None:
            nonlocal engine_disposed
            engine_disposed = True

    monkeypatch.setattr(cli, "load_ibkr_runtime_lock", lambda path: runtime)
    monkeypatch.setattr(
        cli,
        "configured_image_reference",
        lambda: "syd.ocir.io/sdctwkrifhgw/qtrad/qtrad-ibkr@sha256:" + "e" * 64,
    )
    monkeypatch.setattr(cli, "load_ibkr_contract_selection", lambda path: selection)
    monkeypatch.setattr(
        cli,
        "ibkr_historical_selection_asset_classes",
        lambda value: {InstrumentId("fx:eur-usd"): AssetClass.FX},
    )
    monkeypatch.setattr(
        cli,
        "validate_ibkr_historical_canary_representatives",
        lambda value, *, representatives: representatives,
    )
    monkeypatch.setattr(
        cli,
        "build_adjacent_ibkr_canary_cases",
        lambda representatives, *, anchor_end: tuple(range(12)),
    )
    monkeypatch.setattr(cli, "_require_database_at_migration_head", AsyncMock())
    monkeypatch.setattr(cli, "_engine", lambda settings: FakeEngine())
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda engine: object())
    monkeypatch.setattr(cli, "_acquire_ibkr_historical_execution_lock", acquire_lock)
    monkeypatch.setattr(cli, "_release_ibkr_historical_execution_lock", release_lock)
    monkeypatch.setattr(
        "qtrad.adapters.ibkr.pacing.IbkrPostgresPacing",
        lambda *args, **kwargs: SimpleNamespace(reserve=AsyncMock(return_value=0.0)),
    )
    monkeypatch.setattr(
        cli,
        "_ibkr_historical_canary_adapter",
        lambda settings, *, pacing_reserver, clock: object(),
    )
    monkeypatch.setattr(
        cli,
        "run_ibkr_historical_canary",
        AsyncMock(side_effect=RuntimeError("provider composition failed")),
    )
    monkeypatch.setattr(cli, "write_ibkr_historical_canary_evidence", writer)

    settings = Settings(
        ibkr_api_package_fingerprint="b" * 64,
        ibkr_historical_client_id=72,
        ibkr_checkpoint_root=tmp_path,
    )
    with pytest.raises(RuntimeError, match="provider composition failed"):
        await cli._run_ibkr_historical_canary(
            settings,
            cast(Clock, SimpleNamespace(now=lambda: datetime(2026, 8, 5, tzinfo=UTC))),
            runtime_lock_path=tmp_path / "runtime.json",
            contract_selection_path=tmp_path / "selection.json",
            fx_representative_id="fx:eur-usd",
            index_representative_id="index:australia-200",
            commodity_representative_id="commodity:spot-gold",
            anchor_end=datetime(2026, 8, 5, tzinfo=UTC),
            output_path=output,
            execute_account_canary=True,
        )

    writer.assert_not_called()
    assert not output.exists()
    assert engine_disposed is True
    acquire_lock.assert_awaited_once()
    release_lock.assert_awaited_once_with(execution_lock)


def test_canary_runbook_invokes_installed_console_script_directly() -> None:
    runbook_path = Path(__file__).parents[1] / "docs" / "IBKR-HISTORICAL-ACQUISITION.md"
    runbook = runbook_path.read_text(encoding="utf-8")

    assert "qtrad historical ibkr canary-run" in runbook
    assert "uv run qtrad historical ibkr canary-run" not in runbook
