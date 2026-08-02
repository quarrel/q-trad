"""Narrow, market-data-only boundary for the IBKR Stage 1 capability probe."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class IbkrContractQuery:
    """A non-authoritative contract-details query supplied by the operator."""

    instrument_id: InstrumentId
    symbol: str
    security_type: str
    exchange: str
    currency: str
    local_symbol: str | None = None
    trading_class: str | None = None
    multiplier: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("symbol", self.symbol),
            ("security type", self.security_type),
            ("exchange", self.exchange),
            ("currency", self.currency),
        ):
            if not value or len(value) > 80 or any(character.isspace() for character in value):
                raise ValueError(f"IBKR contract query {field_name} must be a bounded token")
        for field_name, value in (
            ("local symbol", self.local_symbol),
            ("trading class", self.trading_class),
            ("multiplier", self.multiplier),
        ):
            if value is not None and (not value or len(value) > 80):
                raise ValueError(f"IBKR contract query {field_name} must be bounded when present")
        if self.security_type not in {"CASH", "CFD", "IND", "STK", "FUT"}:
            raise ValueError("IBKR capability probe does not support this security type")
        if self.security_type == "FUT" and self.local_symbol is None:
            raise ValueError("IBKR futures capability queries require an exact local symbol")


@dataclass(frozen=True, slots=True)
class IbkrContractEvidence:
    """Provider values normalised at the adapter boundary; no TWS types escape."""

    con_id: int
    symbol: str
    local_symbol: str
    security_type: str
    exchange: str
    currency: str
    trading_class: str | None
    multiplier: str | None
    minimum_tick: Decimal | None
    market_rule_ids: tuple[str, ...]
    valid_exchanges: tuple[str, ...]
    long_name: str | None
    underlier_con_id: int | None
    timezone: str | None
    trading_hours: str | None
    liquid_hours: str | None
    primary_exchange: str | None = None
    contract_month: str | None = None

    def __post_init__(self) -> None:
        if self.con_id <= 0:
            raise ValueError("IBKR contract evidence requires a positive conId")
        for field_name, value in (
            ("symbol", self.symbol),
            ("local symbol", self.local_symbol),
            ("security type", self.security_type),
            ("exchange", self.exchange),
            ("currency", self.currency),
        ):
            if not value or len(value) > 200:
                raise ValueError(f"IBKR contract evidence {field_name} is required and bounded")
        if self.minimum_tick is not None and self.minimum_tick <= 0:
            raise ValueError("IBKR minimum tick must be positive when present")
        if self.underlier_con_id is not None and self.underlier_con_id <= 0:
            raise ValueError("IBKR underlier conId must be positive when present")


@dataclass(frozen=True, slots=True)
class IbkrRequestEvidence:
    """One bounded capability request, retaining classifications but not provider prose."""

    kind: str
    status: str
    latency_milliseconds: int
    contract_con_id: int | None = None
    market_data_type: str | None = None
    availability: str | None = None
    bid_seen: bool = False
    ask_seen: bool = False
    bid_usable: bool = False
    ask_usable: bool = False
    bid_size_seen: bool = False
    ask_size_seen: bool = False
    row_count: int | None = None
    earliest_timestamp: datetime | None = None
    timezone: str | None = None
    use_rth: bool | None = None
    error_codes: tuple[str, ...] = ()
    error_times: tuple[int, ...] = ()
    returned_contract_count: int | None = None

    def __post_init__(self) -> None:
        if not self.kind or len(self.kind) > 80:
            raise ValueError("IBKR request evidence kind must be bounded")
        if self.status not in {"SUCCESS", "UNAVAILABLE", "ERROR", "TIMEOUT", "AMBIGUOUS"}:
            raise ValueError("IBKR request evidence status is invalid")
        if self.availability not in {
            None,
            "LIVE_AVAILABLE",
            "DELAYED_AVAILABLE",
            "FROZEN_OR_DELAYED_FROZEN",
            "MARKET_DATA_TYPE_UNCONFIRMED",
            "UNAVAILABLE",
        }:
            raise ValueError("IBKR market-data availability classification is invalid")
        if self.latency_milliseconds < 0:
            raise ValueError("IBKR request latency cannot be negative")
        if self.contract_con_id is not None and self.contract_con_id <= 0:
            raise ValueError("IBKR request contract conId must be positive when present")
        if self.row_count is not None and self.row_count < 0:
            raise ValueError("IBKR request row count cannot be negative")
        if self.returned_contract_count is not None and self.returned_contract_count < 0:
            raise ValueError("IBKR returned contract count cannot be negative")
        if self.earliest_timestamp is not None:
            require_utc(self.earliest_timestamp, "IBKR earliest timestamp")
        if len(set(self.error_codes)) != len(self.error_codes):
            raise ValueError("IBKR request error codes must be unique")
        if any(not code or len(code) > 32 for code in self.error_codes):
            raise ValueError("IBKR request error codes must be bounded")
        if any(error_time < 0 for error_time in self.error_times):
            raise ValueError("IBKR request error times must be non-negative")


@dataclass(frozen=True, slots=True)
class IbkrCandidateCapability:
    """Evidence for one canonical concept and one explicit provider query."""

    query: IbkrContractQuery
    contracts: tuple[IbkrContractEvidence, ...]
    requests: tuple[IbkrRequestEvidence, ...]

    def __post_init__(self) -> None:
        if len({contract.con_id for contract in self.contracts}) != len(self.contracts):
            raise ValueError("IBKR capability contracts must have unique conIds per query")
        if not self.requests:
            raise ValueError("IBKR capability requires request evidence")


class IbkrCapabilityAdapter(Protocol):
    """The only Stage 1 I/O surface; it deliberately has no account or order operations."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def probe(
        self, queries: Sequence[IbkrContractQuery]
    ) -> Sequence[IbkrCandidateCapability]: ...
