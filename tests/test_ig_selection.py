from decimal import Decimal

import pytest

from qtrad.adapters.ig.market_data import (
    _Candidate,
    _search_row_can_match,
    _select_candidate,
)
from qtrad.domain.instruments import INITIAL_INSTRUMENTS


def candidate(
    epic: str,
    minimum: str,
    *,
    instrument_type: str = "INDICES",
    expiry: str = "DFB",
    status: str = "TRADEABLE",
    currency: str = "USD",
) -> _Candidate:
    return _Candidate(
        epic=epic,
        name=epic,
        instrument_type=instrument_type,
        expiry=expiry,
        market_status=status,
        currency=currency,
        minimum_deal_size=Decimal(minimum),
        metadata={},
    )


def test_discovery_selects_unique_smallest_rolling_candidate() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS if str(item.instrument_id) == "index:us-500"
    )
    selected = _select_candidate(
        (candidate("US500-LARGE", "1"), candidate("US500-MINI", "0.1")), instrument
    )
    assert selected.epic == "US500-MINI"


def test_fx_discovery_prefers_standard_contract_over_mini() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS if str(item.instrument_id) == "fx:usd-jpy"
    )
    selected = _select_candidate(
        (
            candidate(
                "CS.D.USDJPY.CFD.IP",
                "0.5",
                instrument_type="CURRENCIES",
                currency="JPY",
            ),
            candidate(
                "CS.D.USDJPY.MINI.IP",
                "0.1",
                instrument_type="CURRENCIES",
                currency="JPY",
            ),
        ),
        instrument,
        preferred_epic="CS.D.USDJPY.CFD.IP",
    )
    assert selected.epic == "CS.D.USDJPY.CFD.IP"


def test_discovery_rejects_listing_in_wrong_quote_currency() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS if str(item.instrument_id) == "index:ftse-100"
    )
    with pytest.raises(RuntimeError, match="no tradeable"):
        _select_candidate(
            (candidate("FTSE-AUD", "0.5"),),
            instrument,
            preferred_epic="FTSE-AUD",
        )


def test_discovery_fails_closed_on_ambiguous_candidates() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS if str(item.instrument_id) == "index:ftse-100"
    )
    with pytest.raises(RuntimeError, match="ambiguous"):
        _select_candidate(
            (
                candidate("FTSE-A", "0.1", currency="GBP"),
                candidate("FTSE-B", "0.1", currency="GBP"),
            ),
            instrument,
        )


def test_discovery_rejects_dated_or_closed_candidates() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS if str(item.instrument_id) == "index:australia-200"
    )
    with pytest.raises(RuntimeError, match="no tradeable"):
        _select_candidate(
            (
                candidate("DATED", "0.1", expiry="SEP-26"),
                candidate("CLOSED", "0.1", status="CLOSED"),
            ),
            instrument,
        )


def test_discovery_prefilters_irrelevant_search_rows_before_detail_fetch() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS if str(item.instrument_id) == "fx:aud-usd"
    )
    assert _search_row_can_match(
        {
            "instrumentType": "CURRENCIES",
            "expiry": "DFB",
            "marketStatus": "TRADEABLE",
        },
        instrument,
    )
    assert not _search_row_can_match(
        {
            "instrumentType": "BINARY",
            "expiry": "DAILY",
            "marketStatus": "TRADEABLE",
        },
        instrument,
    )
