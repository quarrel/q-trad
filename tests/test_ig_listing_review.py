from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import qtrad.adapters.ig.market_data as ig_market_data
from qtrad.adapters.ig.market_data import IgDemoConfig, IgDemoMarketDataAdapter
from qtrad.application.listing_review import build_listing_review_manifest
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import AssetClass, ProductType
from qtrad.ports.market_data import (
    InstrumentListingReview,
    ListingExpiryKind,
    ListingMarketState,
    ListingReviewCandidate,
    ListingReviewRejection,
)
from qtrad.runtime.universe import load_capture_candidates


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 18, 4, 5, 6, tzinfo=UTC)


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def close(self) -> None:
        pass


class ReviewService:
    def __init__(
        self, rows: list[dict[str, object]], details: dict[str, dict[str, object]]
    ) -> None:
        self.session = FakeSession()
        self.rows = rows
        self.details = details
        self.fetched: list[str] = []
        self.searches: list[str] = []

    def create_session(self) -> dict[str, str]:
        return {
            "lightstreamerEndpoint": "https://stream.invalid",
            "currentAccountId": "not-a-real-account",
        }

    def search_markets(self, search_term: str) -> dict[str, object]:
        self.searches.append(search_term)
        return {"markets": self.rows}

    def fetch_market_by_epic(self, epic: str) -> dict[str, object]:
        self.fetched.append(epic)
        return self.details[epic]

    def fetch_historical_prices_by_epic_and_date_range(
        self, epic: str, resolution: str, start: str, end: str
    ) -> object:
        raise AssertionError(f"unexpected historical request: {epic} {resolution} {start} {end}")

    def logout(self) -> None:
        pass


def config() -> IgDemoConfig:
    return IgDemoConfig(
        username="demo",
        password="not-a-real-password",
        api_key="not-a-real-key",
        account_id="not-a-real-account",
        initial_backoff_seconds=0.01,
        maximum_backoff_seconds=0.02,
        reconnect_cooldown_seconds=0.03,
        maximum_reconnect_cycles=1,
        stale_after_seconds=1,
        stale_reconnect_after_seconds=2,
        readiness_timeout_seconds=1,
        retry_watchdog_seconds=1,
        shutdown_timeout_seconds=1,
        provider_operation_timeout_seconds=1,
    )


def detail(
    *,
    epic: str,
    currency: str,
    minimum: str,
    bid: str,
    instrument_type: str = "CURRENCIES",
) -> dict[str, object]:
    return {
        "instrument": {
            "epic": epic,
            "name": epic,
            "type": instrument_type,
            "expiry": "DFB",
            "currencies": [{"code": currency}],
            "contractSize": "1",
            "onePipMeans": "USD 10",
        },
        "dealingRules": {"minDealSize": {"value": minimum}},
        "snapshot": {"marketStatus": "TRADEABLE", "bid": bid},
    }


@pytest.mark.asyncio
async def test_review_enumerates_bounded_candidates_without_selecting_one() -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = next(
        item for item in catalogue.instruments if item.instrument_id == InstrumentId("fx:aud-usd")
    )
    rows: list[dict[str, object]] = [
        {
            "epic": "CS.D.AUDUSD.CFD.IP",
            "instrumentName": "AUD/USD",
            "instrumentType": "CURRENCIES",
            "expiry": "DFB",
            "marketStatus": "TRADEABLE",
        },
        {
            "epic": "CS.D.AUDUSD.WRONG.IP",
            "instrumentName": "AUD/USD wrong currency",
            "instrumentType": "CURRENCIES",
            "expiry": "DFB",
            "marketStatus": "TRADEABLE",
        },
        {
            "epic": "CS.D.AUDUSD.MINI.IP",
            "instrumentName": "AUD/USD Mini",
            "instrumentType": "CURRENCIES",
            "expiry": "DFB",
            "marketStatus": "TRADEABLE",
        },
        {
            "epic": "CS.D.AUDUSD.SEP26.IP",
            "instrumentName": "AUD/USD September 2026",
            "instrumentType": "CURRENCIES",
            "expiry": "SEP-26",
            "marketStatus": "TRADEABLE",
        },
        {
            "epic": "BI.D.AUDUSD.IP",
            "instrumentName": "AUD/USD binary",
            "instrumentType": "BINARY",
            "expiry": "DAILY",
            "marketStatus": "TRADEABLE",
        },
    ]
    service = ReviewService(
        rows,
        {
            "CS.D.AUDUSD.CFD.IP": detail(
                epic="CS.D.AUDUSD.CFD.IP", currency="USD", minimum="0.5", bid="0.6500"
            ),
            "CS.D.AUDUSD.WRONG.IP": detail(
                epic="CS.D.AUDUSD.WRONG.IP", currency="AUD", minimum="0.1", bid="1.5000"
            ),
            "CS.D.AUDUSD.MINI.IP": detail(
                epic="CS.D.AUDUSD.MINI.IP", currency="USD", minimum="0.1", bid="0.6501"
            ),
        },
    )
    adapter = IgDemoMarketDataAdapter(
        config(),
        FixedClock(),
        instruments_by_id={instrument.instrument_id: instrument},
        preferred_epics={},
        service_factory=lambda _: service,
    )

    await adapter.connect()
    try:
        reviews = await adapter.review_listings((instrument.instrument_id,))
    finally:
        await adapter.disconnect()

    assert service.fetched == [
        "CS.D.AUDUSD.CFD.IP",
        "CS.D.AUDUSD.WRONG.IP",
        "CS.D.AUDUSD.MINI.IP",
    ]
    assert service.searches == ["AUD/USD"]
    assert len(reviews) == 1
    candidates = reviews[0].candidates
    assert tuple(item.listing_id.external_id for item in candidates) == (
        "CS.D.AUDUSD.CFD.IP",
        "CS.D.AUDUSD.MINI.IP",
        "CS.D.AUDUSD.SEP26.IP",
        "CS.D.AUDUSD.WRONG.IP",
    )
    assert candidates[0].eligible is True
    assert candidates[0].minimum_deal_size == Decimal("0.5")
    assert candidates[0].economics["contract_size"] == "1"
    assert candidates[1].eligible is True
    assert candidates[1].minimum_deal_size == Decimal("0.1")
    assert candidates[2].rejection_reasons == (ListingReviewRejection.NON_ROLLING_EXPIRY,)
    assert candidates[3].rejection_reasons == (ListingReviewRejection.WRONG_CURRENCY,)

    manifest = build_listing_review_manifest(
        catalogue_name="fixture-candidates",
        catalogue_hash="a" * 64,
        instruments=(instrument,),
        reviews=reviews,
        observed_at=FixedClock().now(),
    )
    encoded = str(manifest.as_json_value())
    assert "0.6500" not in encoded
    assert "not-a-real" not in encoded
    assert manifest.as_json_value()["selection_authority"] is False
    instrument_payload = manifest.as_json_value()["instruments"]
    assert isinstance(instrument_payload, list)
    first_instrument = instrument_payload[0]
    assert isinstance(first_instrument, dict)
    assert first_instrument["eligible_candidate_count"] == 2
    assert first_instrument["status"] == "OPERATOR_SELECTION_REQUIRED"


@pytest.mark.parametrize(
    ("instrument_id", "provider_type", "asset_class"),
    (
        ("commodity:spot-gold", "COMMODITIES", AssetClass.COMMODITY),
        ("crypto:bitcoin-usd", "CURRENCIES", AssetClass.CRYPTO),
    ),
)
@pytest.mark.asyncio
async def test_review_supports_non_fx_rolling_cfd_candidates(
    instrument_id: str,
    provider_type: str,
    asset_class: AssetClass,
) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = next(
        item for item in catalogue.instruments if item.instrument_id == InstrumentId(instrument_id)
    )
    assert instrument.asset_class is asset_class
    epic = f"CS.D.{instrument_id.upper().replace(':', '').replace('-', '')}.CFD.IP"
    row: dict[str, object] = {
        "epic": epic,
        "instrumentName": instrument.display_name,
        "instrumentType": provider_type,
        "expiry": "DFB",
        "marketStatus": "TRADEABLE",
    }
    service = ReviewService(
        [row],
        {
            epic: detail(
                epic=epic,
                currency=instrument.quote_currency,
                minimum="0.1",
                bid="1",
                instrument_type=provider_type,
            )
        },
    )
    adapter = IgDemoMarketDataAdapter(
        config(),
        FixedClock(),
        instruments_by_id={instrument.instrument_id: instrument},
        preferred_epics={},
        service_factory=lambda _: service,
    )

    await adapter.connect()
    try:
        reviews = await adapter.review_listings((instrument.instrument_id,))
    finally:
        await adapter.disconnect()

    assert len(reviews) == 1
    assert len(reviews[0].candidates) == 1
    assert reviews[0].candidates[0].eligible is True
    assert reviews[0].candidates[0].product_type is ProductType.ROLLING_CFD


@pytest.mark.asyncio
async def test_review_fails_on_a_provider_row_without_an_epic() -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = catalogue.instruments[0]
    service = ReviewService(
        [
            {
                "instrumentName": "AUD/USD",
                "instrumentType": "CURRENCIES",
                "expiry": "DFB",
                "marketStatus": "TRADEABLE",
            }
        ],
        {},
    )
    adapter = IgDemoMarketDataAdapter(
        config(),
        FixedClock(),
        instruments_by_id={instrument.instrument_id: instrument},
        preferred_epics={},
        service_factory=lambda _: service,
    )

    await adapter.connect()
    try:
        with pytest.raises(RuntimeError, match="without an epic"):
            await adapter.review_listings((instrument.instrument_id,))
    finally:
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_review_enforces_a_global_search_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = replace(catalogue.instruments[0], search_aliases=("AUD/USD", "Australian dollar"))
    service = ReviewService([], {})
    adapter = IgDemoMarketDataAdapter(
        config(),
        FixedClock(),
        instruments_by_id={instrument.instrument_id: instrument},
        preferred_epics={},
        service_factory=lambda _: service,
    )
    monkeypatch.setattr(ig_market_data, "_MAX_LISTING_REVIEW_SEARCH_REQUESTS", 1)

    await adapter.connect()
    try:
        with pytest.raises(RuntimeError, match="global search-request budget"):
            await adapter.review_listings((instrument.instrument_id,))
    finally:
        await adapter.disconnect()

    assert service.searches == ["AUD/USD"]


@pytest.mark.asyncio
async def test_review_enforces_a_global_detail_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = catalogue.instruments[0]
    epics = ("CS.D.AUDUSD.ONE.IP", "CS.D.AUDUSD.TWO.IP")
    rows: list[dict[str, object]] = [
        {
            "epic": epic,
            "instrumentName": "AUD/USD",
            "instrumentType": "CURRENCIES",
            "expiry": "DFB",
            "marketStatus": "TRADEABLE",
        }
        for epic in epics
    ]
    service = ReviewService(
        rows,
        {epic: detail(epic=epic, currency="USD", minimum="0.5", bid="0.6500") for epic in epics},
    )
    adapter = IgDemoMarketDataAdapter(
        config(),
        FixedClock(),
        instruments_by_id={instrument.instrument_id: instrument},
        preferred_epics={},
        service_factory=lambda _: service,
    )
    monkeypatch.setattr(ig_market_data, "_MAX_LISTING_REVIEW_DETAIL_REQUESTS", 1)

    await adapter.connect()
    try:
        with pytest.raises(RuntimeError, match="global detail-request budget"):
            await adapter.review_listings((instrument.instrument_id,))
    finally:
        await adapter.disconnect()

    assert service.fetched == ["CS.D.AUDUSD.ONE.IP"]


def test_manifest_hash_is_deterministic_and_covers_observation_time() -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = catalogue.instruments[0]
    empty_review = InstrumentListingReview(instrument.instrument_id, ())

    first = build_listing_review_manifest(
        catalogue_name=catalogue.name,
        catalogue_hash=catalogue.configuration_hash,
        instruments=(instrument,),
        reviews=(empty_review,),
        observed_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    repeated = build_listing_review_manifest(
        catalogue_name=catalogue.name,
        catalogue_hash=catalogue.configuration_hash,
        instruments=(instrument,),
        reviews=(empty_review,),
        observed_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    later = build_listing_review_manifest(
        catalogue_name=catalogue.name,
        catalogue_hash=catalogue.configuration_hash,
        instruments=(instrument,),
        reviews=(empty_review,),
        observed_at=datetime(2026, 7, 18, 0, 0, 1, tzinfo=UTC),
    )

    assert first.review_hash == repeated.review_hash
    assert first.review_hash != later.review_hash
    instrument_payload = first.as_json_value()["instruments"]
    assert isinstance(instrument_payload, list)
    first_instrument = instrument_payload[0]
    assert isinstance(first_instrument, dict)
    assert first_instrument["status"] == "NO_ELIGIBLE_CANDIDATE"


def test_manifest_requires_exactly_one_result_per_instrument() -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = catalogue.instruments[0]

    with pytest.raises(ValueError, match="missing instruments"):
        build_listing_review_manifest(
            catalogue_name=catalogue.name,
            catalogue_hash=catalogue.configuration_hash,
            instruments=(instrument,),
            reviews=(),
            observed_at=FixedClock().now(),
        )


def test_manifest_revalidates_eligible_currency_against_the_catalogue() -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    instrument = catalogue.instruments[0]
    incorrectly_eligible = ListingReviewCandidate(
        instrument_id=instrument.instrument_id,
        listing_id=ProviderListingId("ig", "demo", "CS.D.AUDUSD.WRONG.IP"),
        display_name="AUD/USD wrong currency",
        product_type=ProductType.SPOT_FX,
        expiry_kind=ListingExpiryKind.ROLLING,
        market_state=ListingMarketState.TRADEABLE,
        currency="AUD",
        minimum_deal_size=Decimal("0.1"),
        economics={},
        metadata_version="fixture-version",
        rejection_reasons=(),
    )

    with pytest.raises(ValueError, match="wrong currency"):
        build_listing_review_manifest(
            catalogue_name=catalogue.name,
            catalogue_hash=catalogue.configuration_hash,
            instruments=(instrument,),
            reviews=(InstrumentListingReview(instrument.instrument_id, (incorrectly_eligible,)),),
            observed_at=FixedClock().now(),
        )


def test_candidate_rejects_unbounded_economics_fields() -> None:
    with pytest.raises(ValueError, match="unexpected fields"):
        ListingReviewCandidate(
            instrument_id=InstrumentId("fx:aud-usd"),
            listing_id=ProviderListingId("ig", "demo", "CS.D.AUDUSD.CFD.IP"),
            display_name="AUD/USD",
            product_type=ProductType.SPOT_FX,
            expiry_kind=ListingExpiryKind.ROLLING,
            market_state=ListingMarketState.TRADEABLE,
            currency="USD",
            minimum_deal_size=Decimal("0.5"),
            economics={"snapshot": "must-not-enter-a-review"},
            metadata_version="fixture-version",
            rejection_reasons=(),
        )
