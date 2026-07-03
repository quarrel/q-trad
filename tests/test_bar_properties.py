from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from qtrad.application.bars import OneMinuteBarBuilder
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import MarketQuote, PriceBasis


@given(
    st.lists(
        st.decimals(
            min_value=Decimal("0.1"),
            max_value=Decimal("10000"),
            places=5,
            allow_nan=False,
            allow_infinity=False,
        ),
        min_size=1,
        max_size=50,
    )
)
def test_bid_bar_ohlc_invariants(prices: list[Decimal]) -> None:
    builder = OneMinuteBarBuilder()
    for index, price in enumerate(prices):
        second = index % 60
        event_time = datetime(2026, 7, 2, 10, 0, second, tzinfo=UTC)
        builder.on_quote(
            MarketQuote(
                instrument_id=InstrumentId("fx:eur-usd"),
                listing_id=ProviderListingId("fixture", "test", "EURUSD"),
                event_time=event_time,
                received_time=event_time,
                bid=price,
                ask=None,
            )
        )
    bars = builder.advance(datetime(2026, 7, 2, 10, 1, 5, tzinfo=UTC))
    bid = next(bar for bar in bars if bar.basis is PriceBasis.BID)
    assert bid.low <= bid.open <= bid.high
    assert bid.low <= bid.close <= bid.high
    assert bid.low == min(prices)
    assert bid.high == max(prices)
