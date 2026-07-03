from datetime import UTC, datetime, timedelta
from decimal import Decimal

from qtrad.application.gaps import GapDetector
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import DataQuality, MarketQuote


def make_quote(second: int, quality: DataQuality = DataQuality.HEALTHY) -> MarketQuote:
    event_time = datetime(2026, 7, 2, 10, 0, tzinfo=UTC) + timedelta(seconds=second)
    return MarketQuote(
        instrument_id=InstrumentId("fx:aud-usd"),
        listing_id=ProviderListingId("fixture", "test", "AUDUSD"),
        event_time=event_time,
        received_time=event_time,
        bid=Decimal("0.65"),
        ask=Decimal("0.6502"),
        quality=quality,
    )


def test_detects_unexplained_healthy_stream_gap() -> None:
    detector = GapDetector(maximum_silence=timedelta(minutes=2))
    assert detector.observe(make_quote(0), detected_at=make_quote(0).received_time) is None
    current = make_quote(181)
    gap = detector.observe(current, detected_at=current.received_time)
    assert gap is not None
    assert gap.interval_end - gap.interval_start == timedelta(seconds=181)


def test_does_not_classify_non_healthy_transition_as_gap() -> None:
    detector = GapDetector(maximum_silence=timedelta(minutes=2))
    detector.observe(make_quote(0, DataQuality.PARTIAL), detected_at=make_quote(0).received_time)
    current = make_quote(181)
    assert detector.observe(current, detected_at=current.received_time) is None
