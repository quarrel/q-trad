import sys
from queue import Queue
from types import ModuleType, SimpleNamespace

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
        assert market_data_type in {1, 3}
        self._market_data_type = market_data_type

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

    def cancelHistoricalData(self, request_id: int) -> None:
        return None


class _AmbiguousClient(_FakeClient):
    def __init__(self, callbacks: Queue[capability._Callback], returned_count: int) -> None:
        super().__init__(callbacks)
        self._returned_count = returned_count
        self.market_request_count = 0

    def reqContractDetails(self, request_id: int, contract: object) -> None:
        super().reqContractDetails(request_id, contract)
        details = self._callbacks.get_nowait()
        end = self._callbacks.get_nowait()
        for _ in range(self._returned_count):
            self._callbacks.put(details)
        self._callbacks.put(end)

    def reqMktData(
        self,
        request_id: int,
        contract: object,
        generic_tick_list: str,
        snapshot: bool,
        regulatory_snapshot: bool,
        options: list[object],
    ) -> None:
        self.market_request_count += 1
        super().reqMktData(
            request_id,
            contract,
            generic_tick_list,
            snapshot,
            regulatory_snapshot,
            options,
        )


class _ContractFailureClient(_FakeClient):
    def __init__(self, callbacks: Queue[capability._Callback], mode: str) -> None:
        super().__init__(callbacks)
        self._mode = mode
        self.market_request_count = 0

    def reqContractDetails(self, request_id: int, contract: object) -> None:
        if self._mode == "partial_error":
            super().reqContractDetails(request_id, contract)
            details = self._callbacks.get_nowait()
            self._callbacks.get_nowait()
            self._callbacks.put(details)
        if self._mode != "timeout":
            self._callbacks.put(
                capability._Callback("error", request_id, (1_785_000_000, 200, "REQUEST_ERROR"))
            )

    def reqMktData(
        self,
        request_id: int,
        contract: object,
        generic_tick_list: str,
        snapshot: bool,
        regulatory_snapshot: bool,
        options: list[object],
    ) -> None:
        self.market_request_count += 1
        super().reqMktData(
            request_id,
            contract,
            generic_tick_list,
            snapshot,
            regulatory_snapshot,
            options,
        )


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
        request_timeout_seconds=0.1,
        client_factory=factory,
        sleep=_immediate_sleep,
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
    assert requests["LIVE_TOP_OF_BOOK"].bid_usable is True
    assert requests["DELAYED_ENABLED_TOP_OF_BOOK"].market_data_type == "LIVE"
    assert requests["DELAYED_ENABLED_TOP_OF_BOOK"].availability == "LIVE_AVAILABLE"
    assert requests["ONE_SECOND_MIDPOINT_ALL"].row_count == 1
    assert requests["EARLIEST_MIDPOINT"].earliest_timestamp is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_count", (2, 50))
async def test_ambiguous_contract_results_retain_count_without_downstream_probe(
    monkeypatch: pytest.MonkeyPatch, returned_count: int
) -> None:
    created: list[_AmbiguousClient] = []

    def factory(callbacks: Queue[capability._Callback]) -> _AmbiguousClient:
        client = _AmbiguousClient(callbacks, returned_count)
        created.append(client)
        return client

    monkeypatch.setattr(capability, "_contract", lambda query: SimpleNamespace())
    adapter = capability.OfficialIbkrCapabilityAdapter(
        capability.IbkrGatewayEndpoint(host="127.0.0.1", port=4002, client_id=71),
        request_timeout_seconds=0.1,
        client_factory=factory,
        sleep=_immediate_sleep,
    )
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

    assert result.contracts == ()
    assert result.requests[0].status == "AMBIGUOUS"
    assert result.requests[0].returned_contract_count == returned_count
    assert created[0].market_request_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_count"),
    (("partial_error", "ERROR", 1), ("error", "ERROR", 0), ("timeout", "TIMEOUT", 0)),
)
async def test_contract_query_failure_never_probes_partial_or_missing_results(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_status: str,
    expected_count: int,
) -> None:
    created: list[_ContractFailureClient] = []

    def factory(callbacks: Queue[capability._Callback]) -> _ContractFailureClient:
        client = _ContractFailureClient(callbacks, mode)
        created.append(client)
        return client

    monkeypatch.setattr(capability, "_contract", lambda query: SimpleNamespace())
    adapter = capability.OfficialIbkrCapabilityAdapter(
        capability.IbkrGatewayEndpoint(host="127.0.0.1", port=4002, client_id=71),
        request_timeout_seconds=0.001,
        client_factory=factory,
        sleep=_immediate_sleep,
    )
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

    assert result.contracts == ()
    assert result.requests[0].status == expected_status
    assert result.requests[0].returned_contract_count == expected_count
    assert created[0].market_request_count == 0


async def _immediate_sleep(seconds: float) -> None:
    return None


def test_current_official_error_callback_retains_error_time_and_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_module = ModuleType("ibapi.client")
    wrapper_module = ModuleType("ibapi.wrapper")

    class EClient:
        def __init__(self, wrapper: object) -> None:
            return None

    class EWrapper:
        pass

    client_module.__dict__["EClient"] = EClient
    wrapper_module.__dict__["EWrapper"] = EWrapper
    monkeypatch.setitem(sys.modules, "ibapi.client", client_module)
    monkeypatch.setitem(sys.modules, "ibapi.wrapper", wrapper_module)
    monkeypatch.setattr(capability, "_verify_official_api_distribution", lambda identity: None)
    callbacks: Queue[capability._Callback] = Queue()
    client = capability._official_client(
        callbacks, capability.IbkrApiIdentity(package_fingerprint="a" * 64)
    )

    client.error(7, 1_785_000_000, 200, "ambiguous contract", "")

    callback = callbacks.get_nowait()
    assert callback.values == (1_785_000_000, 200, "REQUEST_ERROR")
    assert capability._error_codes((callback,)) == ("IBKR_200",)


@pytest.mark.parametrize(
    ("error_code", "classification"),
    (
        (2104, "INFORMATIONAL"),
        (2106, "INFORMATIONAL"),
        (2107, "INFORMATIONAL"),
        (2108, "INFORMATIONAL"),
        (2158, "INFORMATIONAL"),
        (1100, "CONNECTION_LOST"),
        (1101, "CONNECTION_RESTORED_DATA_LOST"),
        (1102, "CONNECTION_RESTORED_DATA_MAINTAINED"),
        (1300, "PORT_RESET"),
        (200, "REQUEST_ERROR"),
    ),
)
def test_error_classifier_handles_connection_and_request_messages(
    error_code: int, classification: str
) -> None:
    assert capability._error_classification(error_code) == classification


@pytest.mark.parametrize(
    ("data_type", "bid_type", "ask_type"),
    (("LIVE", 1, 2), ("DELAYED", 66, 67)),
)
def test_unavailable_price_sentinel_is_present_but_not_usable(
    data_type: str, bid_type: int, ask_type: int
) -> None:
    callbacks = (
        capability._Callback("tick_price", 1, (bid_type, -1.0)),
        capability._Callback("tick_price", 1, (ask_type, -1.0)),
    )
    bid_types, ask_types, _, _ = capability._tick_family(data_type)

    assert capability._tick_types(callbacks) == {bid_type, ask_type}
    assert capability._usable_price_seen(callbacks, bid_types) is False
    assert capability._usable_price_seen(callbacks, ask_types) is False
    assert capability._market_availability(callbacks, data_type, False, False) == "UNAVAILABLE"


def test_missing_market_data_type_inspects_both_tick_families_but_is_uncertain() -> None:
    bid_types, ask_types, _, _ = capability._tick_family(None)
    callbacks = (
        capability._Callback("tick_price", 1, (66, 1.0)),
        capability._Callback("tick_price", 1, (67, 1.1)),
    )

    assert capability._usable_price_seen(callbacks, bid_types) is True
    assert capability._usable_price_seen(callbacks, ask_types) is True
    assert (
        capability._market_availability(callbacks, None, True, True)
        == "MARKET_DATA_TYPE_UNCONFIRMED"
    )


def test_global_notice_without_market_callback_does_not_make_request_successful() -> None:
    callbacks = (
        capability._Callback(
            "error", -1, (1_785_000_000, 1102, "CONNECTION_RESTORED_DATA_MAINTAINED")
        ),
    )

    assert capability._window_status(callbacks) == "TIMEOUT"


def test_market_data_farm_connecting_notice_is_informational() -> None:
    assert capability._error_classification(2119) == "INFORMATIONAL"


def test_finite_negative_futures_quote_is_usable_except_exact_sentinel() -> None:
    callbacks = (
        capability._Callback("tick_price", 1, (1, -2.5)),
        capability._Callback("tick_price", 1, (2, 0.0)),
    )

    assert capability._usable_price_seen(callbacks, {1}) is True
    assert capability._usable_price_seen(callbacks, {2}) is True
    assert capability._market_availability(callbacks, "LIVE", True, True) == "LIVE_AVAILABLE"


def _collector_adapter() -> capability.OfficialIbkrCapabilityAdapter:
    return capability.OfficialIbkrCapabilityAdapter(
        capability.IbkrGatewayEndpoint(host="127.0.0.1", port=4002, client_id=71),
        request_timeout_seconds=0.001,
        client_factory=lambda callbacks: object(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "classification"), ((1100, "CONNECTION_LOST"), (1300, "PORT_RESET"))
)
async def test_global_connection_failure_terminates_market_data_collection(
    error_code: int, classification: str
) -> None:
    adapter = _collector_adapter()
    adapter._callbacks.put(
        capability._Callback("error", -1, (1_785_000_000, error_code, classification))
    )

    with pytest.raises(capability.IbkrConnectionIntegrityError, match=f"IBKR_{error_code}"):
        await adapter._collect_for(1, 0.001)


@pytest.mark.asyncio
async def test_restored_with_lost_subscriptions_terminates_historical_collection() -> None:
    adapter = _collector_adapter()
    adapter._callbacks.put(
        capability._Callback("error", -1, (1_785_000_000, 1101, "CONNECTION_RESTORED_DATA_LOST"))
    )

    with pytest.raises(capability.IbkrConnectionIntegrityError, match="IBKR_1101"):
        await adapter._collect_until(1, "historical_data_end")


@pytest.mark.asyncio
async def test_restored_connection_with_data_maintained_remains_request_evidence() -> None:
    adapter = _collector_adapter()
    adapter._callbacks.put(
        capability._Callback(
            "error", -1, (1_785_000_000, 1102, "CONNECTION_RESTORED_DATA_MAINTAINED")
        )
    )
    adapter._callbacks.put(capability._Callback("historical_data_end", 1, ("", "")))

    callbacks = await adapter._collect_until(1, "historical_data_end")

    assert capability._error_codes(callbacks) == ("IBKR_1102",)
    assert capability._status(callbacks, "historical_data_end") == "SUCCESS"


@pytest.mark.asyncio
async def test_historical_timeout_retains_prior_connection_notice() -> None:
    adapter = _collector_adapter()

    class Client:
        def reqHistoricalData(self, request_id: int, *args: object) -> None:
            adapter._callbacks.put(
                capability._Callback(
                    "error", -1, (1_785_000_000, 1102, "CONNECTION_RESTORED_DATA_MAINTAINED")
                )
            )

        def cancelHistoricalData(self, request_id: int) -> None:
            return None

    adapter._client = Client()

    evidence = await adapter._historical_evidence(object(), 1, "MIDPOINT", False)

    assert evidence.status == "TIMEOUT"
    assert evidence.error_codes == ("IBKR_1102",)
    assert evidence.error_times == (1_785_000_000,)
