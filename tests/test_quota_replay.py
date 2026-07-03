from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrad.application.quota import points_per_instrument
from qtrad.application.replay import (
    ReplayClock,
    ordered_events,
    semantic_bar_hash,
    semantic_event_hash,
)
from qtrad.domain.events import EventEnvelope
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    PriceBasis,
)


def sample_bar() -> MarketBar:
    return MarketBar(
        instrument_id=InstrumentId("index:us-500"),
        basis=PriceBasis.MID,
        interval_start=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
        interval_end=datetime(2026, 7, 2, 10, 1, tzinfo=UTC),
        open=Decimal("6000.1"),
        high=Decimal("6001.2"),
        low=Decimal("5999.9"),
        close=Decimal("6000.8"),
        sample_count=4,
        revision=1,
        provenance=BarProvenance.QUOTE_DERIVED,
        source_listing_id=ProviderListingId("fixture", "test", "US500"),
        quality=DataQuality.HEALTHY,
    )


def test_quota_preserves_twenty_percent_for_seven_instruments() -> None:
    assert points_per_instrument(remaining_allowance=10_000) == 1000
    assert points_per_instrument(remaining_allowance=3500) == 400
    with pytest.raises(ValueError):
        points_per_instrument(remaining_allowance=-1)


def test_bar_hash_is_deterministic() -> None:
    bar = sample_bar()
    assert semantic_bar_hash((bar,)) == semantic_bar_hash((bar,))


def sample_event(*, stream_id: str, stream_version: int, minute: int) -> EventEnvelope:
    timestamp = datetime(2026, 7, 2, 10, minute, tzinfo=UTC)
    return EventEnvelope.create(
        stream_id=stream_id,
        stream_version=stream_version,
        event_type="MarketBarObserved",
        event_time=timestamp,
        received_time=timestamp,
        producer="test",
        producer_version="1",
        payload={"close": "6000.8"},
    )


def test_replay_order_and_hash_do_not_depend_on_input_order_or_event_identity() -> None:
    first = sample_event(stream_id="bar:first", stream_version=1, minute=0)
    second = sample_event(stream_id="bar:second", stream_version=1, minute=1)
    equivalent_first = replace(
        sample_event(stream_id="bar:first", stream_version=1, minute=0),
        correlation_id=second.correlation_id,
    )

    assert ordered_events((second, first)) == (first, second)
    assert semantic_event_hash((second, first)) == semantic_event_hash((equivalent_first, second))
    assert semantic_event_hash((first, second)) != semantic_event_hash(
        (replace(first, payload={"close": "6001.0"}), second)
    )


def test_replay_clock_is_utc_and_monotonic() -> None:
    start = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    clock = ReplayClock(start)

    clock.advance_to(start + timedelta(minutes=1))
    assert clock.now() == start + timedelta(minutes=1)
    with pytest.raises(ValueError, match="cannot move backwards"):
        clock.advance_to(start)
    with pytest.raises(ValueError, match="timezone-aware"):
        ReplayClock(datetime(2026, 7, 2, 10, 0))
