from decimal import Decimal

import pytest

from qtrad.adapters.ig.market_data import _Candidate, _select_candidate
from qtrad.domain.instruments import INITIAL_INSTRUMENTS


def candidate(
    epic: str,
    minimum: str,
    *,
    instrument_type: str = "INDICES",
    expiry: str = "DFB",
    status: str = "TRADEABLE",
) -> _Candidate:
    return _Candidate(
        epic=epic,
        name=epic,
        instrument_type=instrument_type,
        expiry=expiry,
        market_status=status,
        currency="USD",
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


def test_discovery_fails_closed_on_ambiguous_candidates() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS if str(item.instrument_id) == "index:ftse-100"
    )
    with pytest.raises(RuntimeError, match="ambiguous"):
        _select_candidate(
            (candidate("FTSE-A", "0.1"), candidate("FTSE-B", "0.1")), instrument
        )


def test_discovery_rejects_dated_or_closed_candidates() -> None:
    instrument = next(
        item for item in INITIAL_INSTRUMENTS
        if str(item.instrument_id) == "index:australia-200"
    )
    with pytest.raises(RuntimeError, match="no tradeable"):
        _select_candidate(
            (
                candidate("DATED", "0.1", expiry="SEP-26"),
                candidate("CLOSED", "0.1", status="CLOSED"),
            ),
            instrument,
        )
