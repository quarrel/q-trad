import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrad.application.ibkr_capability import build_ibkr_capability_review
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass, Instrument
from qtrad.ports.ibkr_capability import (
    IbkrCandidateCapability,
    IbkrContractEvidence,
    IbkrContractQuery,
    IbkrRequestEvidence,
)


def _instrument() -> Instrument:
    return Instrument(
        instrument_id=InstrumentId("fx:eur-usd"),
        display_name="EUR/USD",
        asset_class=AssetClass.FX,
        base_currency="EUR",
        quote_currency="USD",
        search_aliases=("EUR/USD",),
    )


def _result() -> IbkrCandidateCapability:
    query = IbkrContractQuery(
        instrument_id=InstrumentId("fx:eur-usd"),
        symbol="EUR",
        security_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
    )
    contract = IbkrContractEvidence(
        con_id=12087792,
        symbol="EUR",
        local_symbol="EUR.USD",
        security_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
        trading_class=None,
        multiplier=None,
        minimum_tick=Decimal("0.00005"),
        market_rule_ids=("26",),
        valid_exchanges=("IDEALPRO",),
        long_name="EUR.USD",
        underlier_con_id=None,
        timezone="US/Eastern",
        trading_hours="20260729:1700-1700",
        liquid_hours="20260729:1700-1700",
    )
    return IbkrCandidateCapability(
        query=query,
        contracts=(contract,),
        requests=(
            IbkrRequestEvidence(kind="CONTRACT_DETAILS", status="SUCCESS", latency_milliseconds=12),
            IbkrRequestEvidence(
                kind="LIVE_TOP_OF_BOOK",
                status="SUCCESS",
                latency_milliseconds=15,
                market_data_type="LIVE",
                bid_seen=True,
                ask_seen=True,
                bid_size_seen=True,
                ask_size_seen=True,
            ),
        ),
    )


def test_ibkr_capability_review_is_complete_non_authoritative_and_deterministic() -> None:
    kwargs = {
        "catalogue_name": "capture-ibkr-v1-candidates",
        "catalogue_hash": "a" * 64,
        "instruments": (_instrument(),),
        "probe_spec_name": "operator-probe-v1",
        "probe_spec_hash": "b" * 64,
        "results": (_result(),),
        "observed_at": datetime(2026, 7, 29, tzinfo=UTC),
    }

    first = build_ibkr_capability_review(**kwargs)
    second = build_ibkr_capability_review(**kwargs)
    payload = first.as_json_value()

    assert first.review_hash == second.review_hash
    assert payload["selection_authority"] is False
    assert payload["external_io_performed"] is True
    instruments = payload["instruments"]
    assert isinstance(instruments, list)
    first_instrument = instruments[0]
    assert isinstance(first_instrument, dict)
    assert first_instrument["status"] == "OPERATOR_SELECTION_REQUIRED"
    queries = first_instrument["queries"]
    assert isinstance(queries, list)
    first_query = queries[0]
    assert isinstance(first_query, dict)
    contracts = first_query["contracts"]
    assert isinstance(contracts, list)
    first_contract = contracts[0]
    assert isinstance(first_contract, dict)
    assert first_contract["con_id"] == 12087792
    encoded = json.dumps(payload)
    assert "username" not in encoded.lower()
    assert "password" not in encoded.lower()
    assert "account" not in encoded.lower()


def test_ibkr_capability_review_rejects_missing_candidate_query_result() -> None:
    with pytest.raises(ValueError, match="missing a query result"):
        build_ibkr_capability_review(
            catalogue_name="capture-ibkr-v1-candidates",
            catalogue_hash="a" * 64,
            instruments=(_instrument(),),
            probe_spec_name="operator-probe-v1",
            probe_spec_hash="b" * 64,
            results=(),
            observed_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
