"""Fixture evidence for the IBKR native Level-1 capture adapter."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from queue import Empty, Queue
from types import SimpleNamespace
from typing import Any, cast

import pytest

from qtrad.adapters.ibkr import capability
from qtrad.adapters.ibkr import market_data as native
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.market_data import DataQuality
from qtrad.domain.modes import BrokerEnvironment
from qtrad.ports.ibkr_capability import IbkrContractEvidence

_NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _listing(external_id: str = "eur-usd") -> ProviderListing:
    return ProviderListing(
        listing_id=ProviderListingId("ibkr", "IBKR_PAPER", external_id),
        instrument_id=InstrumentId(f"fx:{external_id}"),
        display_name=external_id.upper(),
        product_type=ProductType.SPOT_FX,
        currency="USD",
        minimum_deal_size=Decimal("1"),
        price_increment=Decimal("0.00005"),
        valid_from=_NOW,
        valid_to=None,
        metadata_version="fixture-v1",
    )


def _evidence(con_id: int, symbol: str = "EUR") -> IbkrContractEvidence:
    return IbkrContractEvidence(
        con_id=con_id,
        symbol=symbol,
        local_symbol=f"{symbol}.USD",
        security_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
        trading_class="",
        multiplier="",
        minimum_tick=Decimal("0.00005"),
        market_rule_ids=("26",),
        valid_exchanges=("IDEALPRO",),
        long_name=f"{symbol}.USD",
        underlier_con_id=None,
        timezone="US/Eastern",
        trading_hours="20260807:1700-1700",
        liquid_hours="20260807:1700-1700",
    )


class _FakeClient:
    def __init__(self, callbacks: Queue[capability._Callback]) -> None:
        self.callbacks = callbacks
        self.disconnected = False
        self.market_data_requests: list[tuple[int, object]] = []
        self.market_data_types: list[int] = []
        self.cancelled: list[int] = []
        self.on_market_data: deque[tuple[str, int, tuple[object, ...]]] = deque()

    def connect(self, host: str, port: int, *, clientId: int) -> None:
        assert (host, port, clientId) == ("127.0.0.1", 4002, 71)

    def run(self) -> None:
        self.callbacks.put(capability._Callback("next_valid_id", -1, (1,)))

    def disconnect(self) -> None:
        self.disconnected = True

    def reqCurrentTime(self) -> None:
        self.callbacks.put(capability._Callback("current_time", -1, (0,)))

    def reqMarketDataType(self, market_data_type: int) -> None:
        self.market_data_types.append(market_data_type)

    def reqMktData(
        self,
        request_id: int,
        contract: object,
        generic_tick_list: str,
        snapshot: bool,
        regulatory_snapshot: bool,
        options: list[object],
    ) -> None:
        assert (generic_tick_list, snapshot, regulatory_snapshot, options) == ("", False, False, [])
        self.market_data_requests.append((request_id, contract))
        while self.on_market_data:
            kind, callback_request_id, values = self.on_market_data.popleft()
            self.callbacks.put(capability._Callback(kind, callback_request_id, values))

    def cancelMktData(self, request_id: int) -> None:
        self.cancelled.append(request_id)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeClient,
    listings: tuple[ProviderListing, ...] = (_listing(),),
) -> native.IbkrNativeMarketDataAdapter:
    evidence = {
        listing.listing_id: _evidence(index + 100) for index, listing in enumerate(listings)
    }
    monkeypatch.setattr(
        native,
        "_contract_from_evidence",
        lambda item: SimpleNamespace(
            conId=item.con_id,
            symbol=item.symbol,
            localSymbol=item.local_symbol,
            secType=item.security_type,
            exchange=item.exchange,
            currency=item.currency,
            tradingClass=item.trading_class,
            multiplier=item.multiplier,
        ),
    )
    return native.IbkrNativeMarketDataAdapter(
        capability.IbkrGatewayEndpoint(host="127.0.0.1", port=4002, client_id=71),
        pre_reviewed_listings=listings,
        contract_evidence=evidence,
        client_factory=lambda callbacks: _attach_callbacks(client, callbacks),
        clock=lambda: _NOW,
    )


def _attach_callbacks(client: _FakeClient, callbacks: Queue[capability._Callback]) -> _FakeClient:
    while True:
        try:
            callbacks.put(client.callbacks.get_nowait())
        except Empty:
            break
    client.callbacks = callbacks
    return client


async def _connect_and_subscribe(
    adapter: native.IbkrNativeMarketDataAdapter, listing: ProviderListing
) -> None:
    await adapter.connect()
    await adapter.subscribe((listing,))


async def _take(iterator: Any, count: int) -> list[native.MarketDataRecord]:
    return [await anext(iterator) for _ in range(count)]


@pytest.mark.asyncio
async def test_exact_mapping_and_one_sided_callbacks_are_identity_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(Queue())
    client.on_market_data.extend(
        (
            ("market_data_type", 1, (1,)),
            ("tick_price", 1, (1, 1.1000)),
            ("tick_price", 1, (2, 1.1002)),
            ("tick_size", 1, (0, 10)),
            ("tick_size", 1, (3, 12)),
        )
    )
    listing = _listing()
    adapter = _adapter(monkeypatch, client)

    await _connect_and_subscribe(adapter, listing)
    records = await _take(adapter.records(), 5)

    assert client.market_data_types == [1]
    assert len(client.market_data_requests) == 1
    request_id, contract = client.market_data_requests[0]
    assert request_id == 1
    api_contract = cast(Any, contract)
    assert api_contract.conId == 100
    assert api_contract.localSymbol == "EUR.USD"
    assert [record.arrival_sequence for record in records] == [3, 4, 5, 6, 7]
    assert all(record.connection_generation == 1 for record in records)
    assert all(record.raw_payload["request_id"] == 1 for record in records)
    assert records[1].quote is not None
    assert records[1].quote.bid == Decimal("1.1")
    assert records[1].quote.ask is None
    assert records[2].quote is not None
    assert records[2].quote.bid is None
    assert records[2].quote.ask == Decimal("1.1002")
    assert records[3].quote is None
    assert records[3].error_code == "IBKR_TOP_OF_BOOK_SIZE_EVIDENCE"
    assert records[3].raw_payload["raw_value"] == 10
    assert records[3].raw_payload["callback_type"] == "tick_size"
    assert not hasattr(adapter, "placeOrder")
    assert not hasattr(adapter, "place_order")


@pytest.mark.asyncio
async def test_payload_equal_callbacks_keep_distinct_request_and_contract_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listings = (_listing("eur-usd"), _listing("gbp-usd"))
    client = _FakeClient(Queue())
    client.on_market_data.extend(
        (
            ("market_data_type", 1, (1,)),
            ("market_data_type", 2, (1,)),
            ("tick_price", 1, (1, 1.1)),
            ("tick_price", 2, (1, 1.1)),
        )
    )
    adapter = _adapter(monkeypatch, client, listings)
    await adapter.connect()
    await adapter.subscribe(listings)

    records = await _take(adapter.records(), 4)
    bid_records = records[2:]
    assert bid_records[0].deduplication_key != bid_records[1].deduplication_key
    assert bid_records[0].raw_payload["con_id"] == 100
    assert bid_records[1].raw_payload["con_id"] == 101
    assert bid_records[0].subscription != bid_records[1].subscription
    assert bid_records[0].raw_payload["raw_value"] == bid_records[1].raw_payload["raw_value"]


@pytest.mark.asyncio
async def test_superseded_generation_is_rejected_without_cross_generation_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(Queue())
    adapter = _adapter(monkeypatch, client)
    listing = _listing()
    await _connect_and_subscribe(adapter, listing)
    client.callbacks.put(capability._Callback("tick_price", 1, (1, 1.1), generation=0))
    client.callbacks.put(capability._Callback("tick_price", 1, (1, 1.2)))

    record = await anext(adapter.records())
    health = await adapter.health()
    assert record.raw_payload["raw_value"] == 1.2
    assert record.arrival_sequence == 4
    assert "SUPERSEDED_GENERATION" in health.reason_codes
    assert health.attributes[0] == ("connection_generation", "1")


@pytest.mark.asyncio
async def test_recovery_transitions_use_one_session_epoch_and_exact_resubscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(Queue())
    client.on_market_data.append(("market_data_type", 1, (1,)))
    adapter = _adapter(monkeypatch, client)
    listing = _listing()
    await _connect_and_subscribe(adapter, listing)
    stream = adapter.records()
    assert (await anext(stream)).raw_payload["callback_type"] == "market_data_type"

    client.callbacks.put(
        capability._Callback("error", -1, (1_785_000_000, 1100, "CONNECTION_LOST"))
    )
    client.callbacks.put(
        capability._Callback("error", -1, (1_785_000_001, 1101, "CONNECTION_RESTORED_DATA_LOST"))
    )
    first = await anext(stream)
    second = await anext(stream)
    assert first.raw_payload["error_code"] == 1100
    assert second.raw_payload["error_code"] == 1101
    assert len(client.market_data_requests) == 2
    assert client.market_data_requests[0][0] == client.market_data_requests[1][0] == 1
    assert adapter._session.snapshot().recovery_epoch == 1

    client.callbacks.put(
        capability._Callback("error", -1, (1_785_000_002, 1101, "CONNECTION_RESTORED_DATA_LOST"))
    )
    duplicate = await anext(stream)
    assert duplicate.raw_payload["error_code"] == 1101
    assert len(client.market_data_requests) == 2

    client.callbacks.put(
        capability._Callback(
            "error", -1, (1_785_000_003, 1102, "CONNECTION_RESTORED_DATA_MAINTAINED")
        )
    )
    maintained = await anext(stream)
    assert maintained.raw_payload["error_code"] == 1102
    assert len(client.market_data_requests) == 2
    assert adapter._session.snapshot().recovery_epoch == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "classification"),
    ((1300, "PORT_RESET"), (100, "PACING_VIOLATION"), (9999, "UNKNOWN")),
)
async def test_terminal_and_unknown_transitions_are_visible_and_bounded(
    monkeypatch: pytest.MonkeyPatch, error_code: int, classification: str
) -> None:
    client = _FakeClient(Queue())
    adapter = _adapter(monkeypatch, client)
    await _connect_and_subscribe(adapter, _listing())
    client.callbacks.put(
        capability._Callback("error", -1, (1_785_000_000, error_code, classification))
    )

    record = await anext(adapter.records())
    health = await adapter.health()
    assert record.error_code == f"IBKR_{error_code}"
    assert health.status is not native.HealthStatus.HEALTHY
    if error_code == 1300:
        assert health.recovery_action.value == "RESTART_ADAPTER"
        with pytest.raises(capability.IbkrConnectionIntegrityError):
            await anext(adapter.records())
    elif error_code == 100:
        assert health.recovery_action.value == "OPERATOR"
    else:
        assert "UNKNOWN_GLOBAL_CODE_9999" in health.reason_codes


@pytest.mark.asyncio
async def test_live_health_requires_farms_live_type_and_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(Queue())
    for code in (2104, 2106, 2158):
        client.callbacks.put(capability._Callback("error", -1, (1_785_000_000, code, "CONNECTED")))
    client.on_market_data.extend(
        (
            ("market_data_type", 1, (1,)),
            ("tick_price", 1, (1, 1.1)),
            ("tick_price", 1, (2, 1.2)),
        )
    )
    adapter = _adapter(monkeypatch, client)
    await _connect_and_subscribe(adapter, _listing())
    await _take(adapter.records(), 3)
    health = await adapter.health()
    assert health.status is native.HealthStatus.HEALTHY
    assert health.recovery_action.value == "NONE"
    assert health.reason_codes == ()
    assert health.environment is BrokerEnvironment.IBKR_PAPER


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("market_data_type", "price_tick_types", "expected_quality"),
    (
        (2, (1, 2), DataQuality.STALE),
        (3, (66, 67), DataQuality.DELAYED),
        (4, (66, 67), DataQuality.STALE),
    ),
)
async def test_delayed_and_crossed_quotes_are_visible_but_not_healthy_native_evidence(
    monkeypatch: pytest.MonkeyPatch,
    market_data_type: int,
    price_tick_types: tuple[int, int],
    expected_quality: DataQuality,
) -> None:
    client = _FakeClient(Queue())
    client.on_market_data.extend(
        (
            ("market_data_type", 1, (market_data_type,)),
            ("tick_price", 1, (price_tick_types[0], 1.1)),
            ("tick_price", 1, (price_tick_types[1], 1.2)),
            ("tick_price", 1, (price_tick_types[1], 1.0)),
        )
    )
    adapter = _adapter(monkeypatch, client)
    await _connect_and_subscribe(adapter, _listing())
    records = await _take(adapter.records(), 4)
    assert records[0].error_code == "IBKR_NON_LIVE_MARKET_DATA_TYPE"
    assert records[1].quote is not None
    assert records[1].quote.quality is expected_quality
    assert records[2].quote is not None
    assert records[2].quote.quality is expected_quality
    assert records[3].quote is None
    assert records[3].error_code == "IBKR_CROSSED_QUOTE"
    health = await adapter.health()
    assert health.status is not native.HealthStatus.HEALTHY
    assert "MARKET_DATA_TYPE_NOT_LIVE" in health.reason_codes
