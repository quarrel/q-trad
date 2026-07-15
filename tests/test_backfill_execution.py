from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from qtrad import __main__ as cli
from qtrad.application.backfill_planning import backfill_plan_payload, build_backfill_plan
from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import ProviderListingId, RunId
from qtrad.domain.instruments import INITIAL_INSTRUMENTS, ProductType, ProviderListing
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    PriceBasis,
)
from qtrad.ports.clock import Clock
from qtrad.ports.market_data import BackfillRequest
from qtrad.runtime.backfill_plan import write_backfill_plan
from qtrad.runtime.settings import Settings

NOW = datetime(2026, 7, 18, 3, tzinfo=UTC)
START = datetime(2026, 7, 17, 22, tzinfo=UTC)
END = datetime(2026, 7, 17, 23, tzinfo=UTC)


def _listing() -> ProviderListing:
    instrument = INITIAL_INSTRUMENTS[0]
    return ProviderListing(
        listing_id=ProviderListingId("ig", "demo", "BACKFILL.FIXTURE"),
        instrument_id=instrument.instrument_id,
        display_name=instrument.display_name,
        product_type=ProductType.SPOT_FX,
        currency=instrument.quote_currency,
        minimum_deal_size=Decimal("0.5"),
        price_increment=Decimal("0.0001"),
        valid_from=datetime(2026, 7, 14, tzinfo=UTC),
        valid_to=None,
        metadata_version="fixture-version",
    )


def _plan():
    listing = _listing()
    return build_backfill_plan(
        universe_name="capture-v1",
        universe_hash="a" * 64,
        instrument_ids=(listing.instrument_id,),
        listings=(listing,),
        preferred_epics={listing.instrument_id: listing.listing_id.external_id},
        start=START,
        end=END,
        remaining_allowance=1000,
        quota_observed_at=NOW,
        created_at=NOW,
    )


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeStore:
    def __init__(self) -> None:
        self.plan = _plan()
        self.claimed: str | None = None
        self.completed: Mapping[tuple[object, PriceBasis], int] | None = None
        self.failed = False
        self.finished: dict[str, object] | None = None
        self.quotas: list[tuple[str, int]] = []

    async def claim_backfill_plan(self, plan_hash: str) -> Mapping[str, JsonValue]:
        self.claimed = plan_hash
        return backfill_plan_payload(self.plan)

    async def start_run(self, **_: object) -> RunId:
        return RunId(UUID(int=1))

    async def record_quota_state(self, *, allowance_name: str, remaining: int, **_: object) -> None:
        self.quotas.append((allowance_name, remaining))

    async def provider_listing_version(self, _: object) -> ProviderListing:
        return _listing()

    async def complete_backfill_plan(
        self, _: object, *, observed_points: Mapping[tuple[object, PriceBasis], int], **__: object
    ) -> None:
        self.completed = observed_points

    async def fail_backfill_plan(self, *_: object, **__: object) -> None:
        self.failed = True

    async def finish_run(self, _: object, **kwargs: object) -> None:
        self.finished = kwargs


class FakeAdapter:
    historical_allowance_remaining = 900

    def __init__(self) -> None:
        self.connected = False
        self.requests: list[BackfillRequest] = []

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
        assert self.connected
        self.requests.append(request)
        for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID):
            yield MarketBar(
                instrument_id=request.instrument_id,
                basis=basis,
                interval_start=request.start,
                interval_end=request.start + timedelta(minutes=1),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                sample_count=1,
                revision=1,
                provenance=BarProvenance.IG_HISTORICAL,
                source_listing_id=request.listing.listing_id,
                quality=DataQuality.HEALTHY,
            )


@pytest.mark.asyncio
async def test_execute_uses_only_the_claimed_exact_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    store = FakeStore()
    adapter = FakeAdapter()
    append = AsyncMock(return_value=object())
    clock = Mock(spec=Clock)
    clock.now.return_value = NOW
    settings = cast(Settings, SimpleNamespace())
    monkeypatch.setattr(cli, "_engine", lambda _: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda _: store)
    monkeypatch.setattr(cli, "_ig_backfill_adapter", lambda *_: adapter)
    monkeypatch.setattr(cli, "_append_bar", append)

    await cli._execute_backfill(settings, clock, plan_hash=store.plan.plan_hash)

    assert store.claimed == store.plan.plan_hash
    assert len(adapter.requests) == 1
    assert adapter.requests[0].start == START
    assert adapter.requests[0].end == END
    assert adapter.requests[0].listing.metadata_version == "fixture-version"
    assert store.completed is not None
    assert set(store.completed.values()) == {1}
    assert store.failed is False
    assert store.finished is not None
    assert store.finished["status"] == "COMPLETED"
    assert append.await_count == 3
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_execute_fails_plan_when_any_required_basis_has_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    store = FakeStore()

    class PartialAdapter(FakeAdapter):
        async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
            self.requests.append(request)
            yield MarketBar(
                instrument_id=request.instrument_id,
                basis=PriceBasis.BID,
                interval_start=request.start,
                interval_end=request.start + timedelta(minutes=1),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                sample_count=1,
                revision=1,
                provenance=BarProvenance.IG_HISTORICAL,
                source_listing_id=request.listing.listing_id,
                quality=DataQuality.HEALTHY,
            )

    adapter = PartialAdapter()
    clock = Mock(spec=Clock)
    clock.now.return_value = NOW
    monkeypatch.setattr(cli, "_engine", lambda _: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda _: store)
    monkeypatch.setattr(cli, "_ig_backfill_adapter", lambda *_: adapter)
    monkeypatch.setattr(cli, "_append_bar", AsyncMock(return_value=object()))

    with pytest.raises(RuntimeError, match="required basis"):
        await cli._execute_backfill(
            cast(Settings, SimpleNamespace()),
            clock,
            plan_hash=store.plan.plan_hash,
        )

    assert store.completed is None
    assert store.failed is True
    assert store.finished is not None
    assert store.finished["status"] == "FAILED"
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_missing_registered_plan_fails_before_credentials_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()

    class MissingStore:
        async def claim_backfill_plan(self, _: str) -> Mapping[str, JsonValue]:
            raise RuntimeError("backfill plan is not registered")

    monkeypatch.setattr(cli, "_engine", lambda _: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda _: MissingStore())
    provider = Mock(side_effect=AssertionError("provider adapter must not be constructed"))
    monkeypatch.setattr(cli, "_ig_backfill_adapter", provider)

    with pytest.raises(RuntimeError, match="not registered"):
        await cli._execute_backfill(
            cast(Settings, SimpleNamespace()),
            cast(Clock, Mock(spec=Clock)),
            plan_hash="a" * 64,
        )

    provider.assert_not_called()
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_claimed_plan_content_must_match_the_requested_database_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    store = FakeStore()
    provider = Mock(side_effect=AssertionError("provider adapter must not be constructed"))
    clock = Mock(spec=Clock)
    clock.now.return_value = NOW
    monkeypatch.setattr(cli, "_engine", lambda _: engine)
    monkeypatch.setattr(cli, "PostgresAuditStore", lambda _: store)
    monkeypatch.setattr(cli, "_ig_backfill_adapter", provider)

    with pytest.raises(RuntimeError, match="does not match the requested hash"):
        await cli._execute_backfill(
            cast(Settings, SimpleNamespace()),
            clock,
            plan_hash="b" * 64,
        )

    provider.assert_not_called()
    assert store.failed is True
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_registration_requires_the_explicit_reviewed_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "plan.json"
    write_backfill_plan(path, _plan())
    database = Mock(side_effect=AssertionError("hash mismatch must fail before database access"))
    monkeypatch.setattr(cli, "_engine", database)

    with pytest.raises(ValueError, match="does not match the reviewed plan"):
        await cli._register_backfill(
            cast(Settings, SimpleNamespace()),
            plan_path=path,
            confirmed_plan_hash="b" * 64,
        )

    database.assert_not_called()
