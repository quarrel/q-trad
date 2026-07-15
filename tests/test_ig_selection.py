from dataclasses import replace
from decimal import Decimal

import pytest

from qtrad.adapters.ig.market_data import (
    _bounded_economics,
    _Candidate,
    _candidate,
    _listing_metadata_version,
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


def test_product_economics_preserves_currency_qualified_pip_meaning() -> None:
    economics = _bounded_economics(
        {
            "instrument": {"onePipMeans": "USD 10", "contractSize": "1"},
            "dealingRules": {"minDealSize": {"value": "0.5"}},
        }
    )

    assert economics["one_pip_means"] == "USD 10"
    assert economics["contract_size"] == "1"
    assert economics["minimum_quantity"] == "0.5"


def test_listing_version_excludes_volatile_market_snapshot() -> None:
    first = candidate("US500", "0.5")
    second = candidate("US500", "0.5")
    first = replace(
        first,
        metadata={"instrument": {"contractSize": "1"}, "snapshot": {"bid": 100}},
    )
    second = replace(
        second,
        metadata={"instrument": {"contractSize": "1"}, "snapshot": {"bid": 101}},
    )

    first_economics = _bounded_economics(first.metadata)
    second_economics = _bounded_economics(second.metadata)
    assert _listing_metadata_version(first, first_economics) == _listing_metadata_version(
        second, second_economics
    )


@pytest.mark.parametrize("minimum", [None, "0", "-0.1"])
def test_discovery_fails_closed_without_a_positive_minimum_deal_size(
    minimum: str | None,
) -> None:
    dealing_rules: dict[str, object] = {}
    if minimum is not None:
        dealing_rules["minDealSize"] = {"value": minimum}
    detail = {
        "instrument": {
            "epic": "CS.D.AUDUSD.CFD.IP",
            "name": "AUD/USD",
            "type": "CURRENCIES",
            "expiry": "DFB",
            "currencies": [{"code": "USD"}],
        },
        "dealingRules": dealing_rules,
        "snapshot": {"marketStatus": "TRADEABLE"},
    }

    assert _candidate({"epic": "CS.D.AUDUSD.CFD.IP"}, detail) is None
