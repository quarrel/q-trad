from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrad.domain.events import EventEnvelope
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import INITIAL_INSTRUMENTS
from qtrad.domain.market_data import MarketQuote


def test_initial_universe_is_exact_and_stable() -> None:
    assert tuple(str(item.instrument_id) for item in INITIAL_INSTRUMENTS) == (
        "fx:aud-usd",
        "fx:eur-usd",
        "fx:usd-jpy",
        "fx:gbp-usd",
        "index:australia-200",
        "index:us-500",
        "index:ftse-100",
    )


def test_instrument_id_rejects_non_canonical_values() -> None:
    with pytest.raises(ValueError):
        InstrumentId("AUDUSD")
    with pytest.raises(ValueError):
        InstrumentId("fx:AUD-USD")


def test_quote_enforces_utc_spread_and_decimal_precision() -> None:
    now = datetime(2026, 7, 2, 1, 2, 3, tzinfo=UTC)
    quote = MarketQuote(
        instrument_id=InstrumentId("fx:usd-jpy"),
        listing_id=ProviderListingId("fixture", "test", "USDJPY"),
        event_time=now,
        received_time=now,
        bid=Decimal("143.001"),
        ask=Decimal("143.003"),
    )
    assert quote.ask is not None
    assert quote.bid is not None
    assert quote.ask - quote.bid == Decimal("0.002")
    field_name = "bid"
    with pytest.raises(FrozenInstanceError):
        setattr(quote, field_name, Decimal("1"))


def test_quote_rejects_naive_time_and_crossed_market() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketQuote(
            instrument_id=InstrumentId("fx:aud-usd"),
            listing_id=ProviderListingId("fixture", "test", "AUDUSD"),
            event_time=datetime(2026, 7, 2),
            received_time=datetime(2026, 7, 2, tzinfo=UTC),
            bid=Decimal("1"),
            ask=Decimal("2"),
        )
    with pytest.raises(ValueError, match="must not exceed"):
        MarketQuote(
            instrument_id=InstrumentId("fx:aud-usd"),
            listing_id=ProviderListingId("fixture", "test", "AUDUSD"),
            event_time=datetime(2026, 7, 2, tzinfo=UTC),
            received_time=datetime(2026, 7, 2, tzinfo=UTC),
            bid=Decimal("2"),
            ask=Decimal("1"),
        )


def test_event_payload_serialises_domain_values() -> None:
    now = datetime(2026, 7, 2, tzinfo=UTC)
    quote = MarketQuote(
        instrument_id=InstrumentId("fx:aud-usd"),
        listing_id=ProviderListingId("fixture", "test", "AUDUSD"),
        event_time=now,
        received_time=now,
        bid=Decimal("0.65001"),
        ask=Decimal("0.65003"),
    )
    event = EventEnvelope.create(
        stream_id="market-quote:fx:aud-usd",
        stream_version=1,
        event_type="MarketQuoteObserved",
        event_time=now,
        received_time=now,
        producer="test",
        producer_version="1",
        payload=quote,
    )
    assert event.payload["instrument_id"] == "fx:aud-usd"
    assert event.payload["bid"] == "0.65001"
    assert event.payload["event_time"] == "2026-07-02T00:00:00Z"
