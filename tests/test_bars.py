from datetime import UTC, datetime, timedelta
from decimal import Decimal

from qtrad.application.bars import OneMinuteBarBuilder
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import MarketQuote, PriceBasis


def quote(
    second: int,
    bid: str | None,
    ask: str | None,
    *,
    minute: int = 0,
) -> MarketQuote:
    time = datetime(2026, 7, 2, 10, minute, second, tzinfo=UTC)
    return MarketQuote(
        instrument_id=InstrumentId("fx:aud-usd"),
        listing_id=ProviderListingId("fixture", "test", "AUDUSD"),
        event_time=time,
        received_time=time,
        bid=Decimal(bid) if bid else None,
        ask=Decimal(ask) if ask else None,
    )


def test_builds_bid_ask_and_midpoint_bars_after_watermark() -> None:
    builder = OneMinuteBarBuilder()
    builder.on_quote(quote(1, "1.0000", "1.0002"))
    builder.on_quote(quote(30, "1.0003", "1.0005"))

    assert builder.advance(datetime(2026, 7, 2, 10, 1, 4, tzinfo=UTC)) == ()
    bars = builder.advance(datetime(2026, 7, 2, 10, 1, 5, tzinfo=UTC))

    assert {bar.basis for bar in bars} == {
        PriceBasis.BID,
        PriceBasis.ASK,
        PriceBasis.MID,
    }
    bid = next(bar for bar in bars if bar.basis is PriceBasis.BID)
    assert (bid.open, bid.high, bid.low, bid.close) == (
        Decimal("1.0000"),
        Decimal("1.0003"),
        Decimal("1.0000"),
        Decimal("1.0003"),
    )


def test_missing_ask_does_not_invent_midpoint() -> None:
    builder = OneMinuteBarBuilder()
    builder.on_quote(quote(1, "1.0000", None))
    bars = builder.advance(datetime(2026, 7, 2, 10, 1, 5, tzinfo=UTC))
    assert [bar.basis for bar in bars] == [PriceBasis.BID]


def test_late_sample_emits_revision_without_mutating_first_bar() -> None:
    builder = OneMinuteBarBuilder()
    builder.on_quote(quote(30, "1.0000", "1.0002"))
    first = builder.advance(datetime(2026, 7, 2, 10, 1, 5, tzinfo=UTC))
    first_bid = next(bar for bar in first if bar.basis is PriceBasis.BID)

    corrected = builder.on_quote(quote(10, "0.9998", "1.0000"))
    corrected_bid = next(bar for bar in corrected if bar.basis is PriceBasis.BID)

    assert first_bid.revision == 1
    assert first_bid.open == Decimal("1.0000")
    assert corrected_bid.revision == 2
    assert corrected_bid.open == Decimal("0.9998")
    assert corrected_bid.interval_end - corrected_bid.interval_start == timedelta(minutes=1)
