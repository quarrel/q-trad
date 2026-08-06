from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from queue import Queue
from types import SimpleNamespace

import pytest

from qtrad.adapters.ibkr import capability, historical
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrHistoricalCallback,
    IbkrHistoricalCallbackKind,
    IbkrHistoricalTerminalError,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
    ibkr_end_date_time,
    sha256_json,
)
from qtrad.domain.identifiers import InstrumentId

_START = datetime(2026, 2, 1, tzinfo=UTC)
_FINGERPRINT = IbkrContractFingerprint(
    con_id=42,
    symbol="EUR",
    security_type="CASH",
    currency="USD",
    exchange="IDEALPRO",
    primary_exchange=None,
    local_symbol="EUR.USD",
    trading_class="EUR.USD",
    multiplier=None,
    underlying_con_id=None,
    contract_month=None,
)


def _request(kind: IbkrHistoricalRequestKind) -> IbkrHistoricalRequest:
    end = _START + timedelta(hours=2)
    bar_size = "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day"
    what_to_show = "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
    format_date = 2
    identity: dict[str, JsonValue] = {
        "instrument_id": "fx:eur-usd",
        "fingerprint": _FINGERPRINT.as_json_value(),
        "kind": kind.value,
        "interval_start": _START.isoformat().replace("+00:00", "Z"),
        "interval_end": end.isoformat().replace("+00:00", "Z"),
        "end_date_time": ibkr_end_date_time(end),
        "duration": "1 D",
        "bar_size": bar_size,
        "what_to_show": what_to_show,
        "use_rth": False,
        "format_date": format_date,
        "keep_up_to_date": False,
    }
    return IbkrHistoricalRequest(
        instrument_id=InstrumentId("fx:eur-usd"),
        fingerprint=_FINGERPRINT,
        kind=kind,
        interval_start=_START,
        interval_end=end,
        end_date_time=ibkr_end_date_time(end),
        duration="1 D",
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=False,
        format_date=format_date,
        keep_up_to_date=False,
        request_sha256=sha256_json(identity),
    )


class _HistoricalClient:
    def __init__(
        self,
        callbacks: Queue[capability._Callback],
        *,
        error: bool = False,
        error_text: str = "provider message",
    ) -> None:
        self._callbacks = callbacks
        self._error = error
        self._error_text = error_text
        self.cancelled: list[int] = []
        self.historical_calls: list[tuple[int, tuple[object, ...]]] = []
        self.contract_calls: list[int] = []

    def connect(self, host: str, port: int, *, clientId: int) -> None:
        assert (host, port, clientId) == ("127.0.0.1", 4002, 71)

    def run(self) -> None:
        self._callbacks.put(capability._Callback("next_valid_id", -1, (1,)))

    def disconnect(self) -> None:
        return None

    def reqCurrentTime(self) -> None:
        self._callbacks.put(capability._Callback("current_time", -1, (1_770_000_000,)))

    def reqContractDetails(self, request_id: int, contract: object) -> None:
        self.contract_calls.append(request_id)
        details = SimpleNamespace(
            contract=SimpleNamespace(
                conId=42,
                symbol="EUR",
                localSymbol="EUR.USD",
                secType="CASH",
                exchange="IDEALPRO",
                currency="USD",
                tradingClass="EUR.USD",
                multiplier="",
                primaryExchange="",
                lastTradeDateOrContractMonth="",
            ),
            minTick="0.00005",
            marketRuleIds="26",
            validExchanges="IDEALPRO",
            longName="EUR.USD",
            underConId=0,
            timeZoneId="US/Eastern",
            tradingHours="20260201:1700-1700",
            liquidHours="20260201:1700-1700",
        )
        self._callbacks.put(capability._Callback("contract_details", request_id, (details,)))
        self._callbacks.put(capability._Callback("contract_details_end", request_id, ()))

    def reqHistoricalData(self, request_id: int, *args: object) -> None:
        self.historical_calls.append((request_id, args))
        what_to_show = args[4]
        if self._error:
            self._callbacks.put(
                capability._Callback(
                    "error",
                    request_id,
                    (1_770_000_000, 162, "REQUEST_ERROR"),
                    diagnostic=self._error_text,
                    message_sha256=sha256_json({"message": self._error_text}),
                )
            )
            return
        if what_to_show == "SCHEDULE":
            session = SimpleNamespace(
                startDateTime="20260201-00:00:00",
                endDateTime="20260201-02:00:00",
                refDate="20260201",
            )
            self._callbacks.put(
                capability._Callback(
                    "historical_schedule",
                    request_id,
                    (
                        "20260201-00:00:00",
                        "20260201-02:00:00",
                        "UTC",
                        (session,),
                    ),
                )
            )
        else:
            self._callbacks.put(
                capability._Callback(
                    "historical_data",
                    request_id,
                    (
                        SimpleNamespace(
                            date=str(int((_START + timedelta(minutes=1)).timestamp())),
                            open=1.1000,
                            high=1.1010,
                            low=1.0990,
                            close=1.1005,
                            volume=7.0,
                            wap=1.1001,
                            count=3,
                        ),
                    ),
                )
            )
            self._callbacks.put(
                capability._Callback(
                    "historical_data_end",
                    request_id,
                    ("20260131 19:00:00 US/Eastern", "20260131 21:00:00 US/Eastern"),
                )
            )

    def cancelHistoricalData(self, request_id: int) -> None:
        self.cancelled.append(request_id)


def test_historical_deferred_callback_budget_matches_api_queue() -> None:
    adapter = object.__new__(historical.OfficialIbkrHistoricalAdapter)
    adapter._deferred_callbacks = deque()
    item = capability._Callback("historical_data", 1, ())

    for _ in range(2_001):
        adapter._defer_callback(item)

    assert len(adapter._deferred_callbacks) == 2_001


@pytest.mark.asyncio
async def test_historical_adapter_correlates_bars_and_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(historical, "_contract", lambda query: SimpleNamespace())
    created: list[_HistoricalClient] = []

    def factory(callbacks: Queue[capability._Callback]) -> _HistoricalClient:
        client = _HistoricalClient(callbacks)
        created.append(client)
        return client

    adapter = historical.OfficialIbkrHistoricalAdapter(
        capability.IbkrGatewayEndpoint("127.0.0.1", 4002, 71),
        request_timeout_seconds=0.2,
        historical_timeout_seconds=0.2,
        client_factory=factory,
        sleep=lambda _: _immediate_sleep(),
    )
    connection = await adapter.connect()
    received: list[IbkrHistoricalCallback] = []

    async def collect(item: IbkrHistoricalCallback) -> None:
        received.append(item)

    await adapter.request_historical(
        _request(IbkrHistoricalRequestKind.MIDPOINT_BARS),
        request_id=10,
        connection_session_id=connection.connection_session_id,
        connection_generation=connection.connection_generation,
        callback=collect,
    )
    bars = [item for item in received if item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR]
    assert bars[0].payload["date"] == int((_START + timedelta(minutes=1)).timestamp())
    assert bars[0].payload["open"] == "1.1"
    assert received[-1].kind is IbkrHistoricalCallbackKind.COMPLETION
    assert received[-1].payload == {
        "start": "2026-02-01T00:00:00Z",
        "end": "2026-02-01T02:00:00Z",
    }
    first_call = created[0].historical_calls[0][1]
    assert isinstance(first_call[0], SimpleNamespace)
    assert first_call[1:] == (
        ibkr_end_date_time(_START + timedelta(hours=2)).removesuffix(" UTC"),
        "1 D",
        "1 min",
        "MIDPOINT",
        0,
        2,
        False,
        [],
    )

    received.clear()
    await adapter.request_historical(
        _request(IbkrHistoricalRequestKind.SCHEDULE),
        request_id=11,
        connection_session_id=connection.connection_session_id,
        connection_generation=connection.connection_generation,
        callback=collect,
    )
    schedule = next(item for item in received if item.kind is IbkrHistoricalCallbackKind.SCHEDULE)
    sessions = schedule.payload["sessions"]
    assert isinstance(sessions, list)
    session = sessions[0]
    assert isinstance(session, dict)
    assert session["active"] is True
    assert session["start"] == "2026-02-01T00:00:00Z"
    assert received[-1].kind is IbkrHistoricalCallbackKind.COMPLETION
    schedule_call = created[0].historical_calls[1][1]
    assert isinstance(schedule_call[0], SimpleNamespace)
    assert schedule_call[1:] == (
        ibkr_end_date_time(_START + timedelta(hours=2)).removesuffix(" UTC"),
        "1 D",
        "1 day",
        "SCHEDULE",
        0,
        2,
        False,
        [],
    )
    await adapter.disconnect()


@pytest.mark.parametrize(
    "error_text",
    (
        "No market data permissions for this request",
        "Historical market data Service error message",
        "No data of type MIDPOINT available for this request",
    ),
)
@pytest.mark.asyncio
async def test_historical_adapter_reauthenticates_and_retains_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
    error_text: str,
) -> None:
    monkeypatch.setattr(historical, "_contract", lambda query: SimpleNamespace())
    created: list[_HistoricalClient] = []

    def factory(callbacks: Queue[capability._Callback]) -> _HistoricalClient:
        client = _HistoricalClient(callbacks, error=bool(created), error_text=error_text)
        created.append(client)
        return client

    adapter = historical.OfficialIbkrHistoricalAdapter(
        capability.IbkrGatewayEndpoint("127.0.0.1", 4002, 71),
        request_timeout_seconds=0.2,
        historical_timeout_seconds=0.2,
        client_factory=factory,
        sleep=lambda _: _immediate_sleep(),
    )
    connection = await adapter.connect()
    evidence = await adapter.reauthenticate_contract(_FINGERPRINT)
    assert evidence.status == "MATCH"
    assert evidence.observed == (_FINGERPRINT,)

    created[0]._error = True
    received: list[IbkrHistoricalCallback] = []

    async def collect(item: IbkrHistoricalCallback) -> None:
        received.append(item)

    with pytest.raises(IbkrHistoricalTerminalError) as raised:
        await adapter.request_historical(
            _request(IbkrHistoricalRequestKind.MIDPOINT_BARS),
            request_id=20,
            connection_session_id=connection.connection_session_id,
            connection_generation=connection.connection_generation,
            callback=collect,
        )
    assert getattr(raised.value, "disposition", None) is IbkrTerminalDisposition.PROVIDER_REJECTED
    error = next(item for item in received if item.kind is IbkrHistoricalCallbackKind.ERROR)
    assert error.payload["error_code"] == 162
    assert error.payload["diagnostic"] == "IBKR_162_REQUEST_ERROR"
    assert "message_sha256" in error.payload
    assert "provider message" not in str(error.payload)
    await adapter.disconnect()


async def _immediate_sleep() -> None:
    return None


@pytest.mark.parametrize(
    ("values", "expected"),
    (
        (
            ("20260804 00:00:00 US/Eastern", "20260805 00:00:00 US/Eastern"),
            {
                "start": "2026-08-04T04:00:00Z",
                "end": "2026-08-05T04:00:00Z",
            },
        ),
        (
            ("2026-08-04T00:00:00Z", "2026-08-05T00:00:00+00:00"),
            {
                "start": "2026-08-04T00:00:00Z",
                "end": "2026-08-05T00:00:00Z",
            },
        ),
    ),
)
def test_completion_payload_normalizes_timezone_forms(
    values: tuple[str, str], expected: dict[str, str]
) -> None:
    assert historical._completion_payload(values) == expected
