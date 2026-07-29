from queue import Queue
from types import SimpleNamespace

import pytest

from qtrad.adapters.ibkr import capability
from qtrad.domain.identifiers import InstrumentId
from qtrad.ports.ibkr_capability import IbkrContractQuery


class _FakeClient:
    def __init__(self, callbacks: Queue[capability._Callback]) -> None:
        self._callbacks = callbacks
        self.disconnected = False

    def connect(self, host: str, port: int, *, clientId: int) -> None:
        assert (host, port, clientId) == ("127.0.0.1", 4002, 71)

    def run(self) -> None:
        self._callbacks.put(capability._Callback("next_valid_id", -1, (1,)))

    def disconnect(self) -> None:
        self.disconnected = True

    def reqCurrentTime(self) -> None:
        self._callbacks.put(capability._Callback("current_time", -1, (0,)))

    def reqContractDetails(self, request_id: int, contract: object) -> None:
        details = SimpleNamespace(
            contract=SimpleNamespace(
                conId=1,
                symbol="EUR",
                localSymbol="EUR.USD",
                secType="CASH",
                exchange="IDEALPRO",
                currency="USD",
                tradingClass="",
                multiplier="",
            ),
            minTick="0.00005",
            marketRuleIds="26",
            validExchanges="IDEALPRO",
            longName="EUR.USD",
            underConId=0,
            timeZoneId="US/Eastern",
            tradingHours="20260729:1700-1700",
            liquidHours="20260729:1700-1700",
        )
        self._callbacks.put(capability._Callback("contract_details", request_id, (details,)))
        self._callbacks.put(capability._Callback("contract_details_end", request_id, ()))

    def reqMarketDataType(self, market_data_type: int) -> None:
        assert market_data_type == 1

    def reqMktData(
        self,
        request_id: int,
        contract: object,
        generic_tick_list: str,
        snapshot: bool,
        regulatory_snapshot: bool,
        options: list[object],
    ) -> None:
        self._callbacks.put(capability._Callback("market_data_type", request_id, (1,)))
        for tick_type in (0, 1, 2, 3):
            kind = "tick_size" if tick_type in {0, 3} else "tick_price"
            self._callbacks.put(capability._Callback(kind, request_id, (tick_type, 1)))

    def cancelMktData(self, request_id: int) -> None:
        return None

    def reqHistoricalData(self, request_id: int, *args: object) -> None:
        self._callbacks.put(capability._Callback("historical_data", request_id, (object(),)))
        self._callbacks.put(capability._Callback("historical_data_end", request_id, ("", "")))

    def reqHeadTimeStamp(self, request_id: int, *args: object) -> None:
        self._callbacks.put(capability._Callback("head_timestamp", request_id, ("1785369600",)))

    def cancelHeadTimeStamp(self, request_id: int) -> None:
        return None


@pytest.mark.asyncio
async def test_direct_capability_adapter_collects_bounded_market_data_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakeClient] = []

    def factory(callbacks: Queue[capability._Callback]) -> _FakeClient:
        client = _FakeClient(callbacks)
        created.append(client)
        return client

    monkeypatch.setattr(capability, "_contract", lambda query: SimpleNamespace())
    adapter = capability.OfficialIbkrCapabilityAdapter(
        capability.IbkrGatewayEndpoint(host="127.0.0.1", port=4002, client_id=71),
        request_timeout_seconds=0.001,
        client_factory=factory,
    )
    assert not hasattr(adapter, "placeOrder")
    assert not hasattr(adapter, "place_order")
    query = IbkrContractQuery(
        instrument_id=InstrumentId("fx:eur-usd"),
        symbol="EUR",
        security_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
    )

    await adapter.connect()
    result = (await adapter.probe((query,)))[0]
    await adapter.disconnect()

    assert created[0].disconnected is True
    assert result.contracts[0].con_id == 1
    requests = {request.kind: request for request in result.requests}
    assert requests["LIVE_TOP_OF_BOOK"].market_data_type == "LIVE"
    assert requests["LIVE_TOP_OF_BOOK"].bid_seen is True
    assert requests["ONE_SECOND_MIDPOINT_ALL"].row_count == 1
    assert requests["EARLIEST_MIDPOINT"].earliest_timestamp is not None
