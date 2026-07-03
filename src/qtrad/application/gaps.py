"""Conservative gap detection over canonical quote time."""

from datetime import datetime, timedelta

from qtrad.domain.market_data import DataGap, DataQuality, MarketQuote


class GapDetector:
    def __init__(self, *, maximum_silence: timedelta = timedelta(minutes=2)) -> None:
        if maximum_silence <= timedelta(0):
            raise ValueError("maximum silence must be positive")
        self._maximum_silence = maximum_silence
        self._last_quote: dict[str, MarketQuote] = {}

    def observe(self, quote: MarketQuote, *, detected_at: datetime) -> DataGap | None:
        key = str(quote.instrument_id)
        previous = self._last_quote.get(key)
        self._last_quote[key] = quote
        if (
            previous is None
            or previous.quality is not DataQuality.HEALTHY
            or quote.quality is not DataQuality.HEALTHY
            or quote.event_time <= previous.event_time + self._maximum_silence
        ):
            return None
        return DataGap(
            instrument_id=quote.instrument_id,
            interval_start=previous.event_time,
            interval_end=quote.event_time,
            reason="NO_HEALTHY_QUOTE_DURING_EXPECTED_STREAM",
            detected_at=detected_at,
        )
