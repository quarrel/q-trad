from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrad.application.quota import points_per_instrument
from qtrad.application.replay import semantic_bar_hash
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
