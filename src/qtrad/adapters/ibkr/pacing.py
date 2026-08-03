"""IBKR pacing policy independent of provider client types."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from qtrad.domain.ibkr_historical import IbkrHistoricalPacingPolicy


@dataclass(frozen=True, slots=True)
class IbkrRequestReservation:
    requested_at: float
    request_kind: str
    contract_key: str
    request_fingerprint: str
    weight: int = 1

    def __post_init__(self) -> None:
        if self.requested_at < 0:
            raise ValueError("IBKR pacing timestamp cannot be negative")
        if not self.request_kind or not self.contract_key or not self.request_fingerprint:
            raise ValueError("IBKR pacing identity is required")
        if self.weight < 1:
            raise ValueError("IBKR pacing weight must be positive")


@dataclass(frozen=True, slots=True)
class IbkrPacingPolicy:
    """Conservative local policy for the documented IBKR request windows."""

    max_messages_per_second: int = 45
    historical_duplicate_seconds: float = 15.0
    historical_contract_window_seconds: float = 2.0
    historical_contract_max_weight: int = 6
    historical_window_seconds: float = 600.0
    historical_max_weight: int = 60
    bid_ask_window_seconds: float = 2.0
    bid_ask_max_weight: int = 6

    def __post_init__(self) -> None:
        if self.max_messages_per_second < 1:
            raise ValueError("IBKR message limit must be positive")
        if any(
            value <= 0
            for value in (
                self.historical_duplicate_seconds,
                self.historical_contract_window_seconds,
                self.historical_window_seconds,
                self.bid_ask_window_seconds,
            )
        ):
            raise ValueError("IBKR pacing windows must be positive")
        if (
            self.historical_contract_max_weight < 1
            or self.historical_max_weight < 1
            or self.bid_ask_max_weight < 1
        ):
            raise ValueError("IBKR pacing limits must be positive")

    def wait_seconds(
        self,
        request: IbkrRequestReservation,
        recent: Iterable[IbkrRequestReservation],
    ) -> float:
        """Return the minimum delay before the request can be sent."""

        records = tuple(recent)
        now = request.requested_at
        waits: list[float] = []

        recent_messages = tuple(item for item in records if now - item.requested_at < 1.0)
        message_weight = sum(item.weight for item in recent_messages)
        if message_weight + request.weight > self.max_messages_per_second:
            oldest = min((item.requested_at for item in recent_messages), default=now)
            waits.append(oldest + 1.0 - now)

        if request.request_kind == "historical":
            duplicate = [
                item
                for item in records
                if item.request_kind == request.request_kind
                and item.contract_key == request.contract_key
                and item.request_fingerprint == request.request_fingerprint
                and now - item.requested_at < self.historical_duplicate_seconds
            ]
            if duplicate:
                waits.append(
                    min(item.requested_at for item in duplicate)
                    + self.historical_duplicate_seconds
                    - now
                )

            contract_window = [
                item
                for item in records
                if item.request_kind == request.request_kind
                and item.contract_key == request.contract_key
                and now - item.requested_at < self.historical_contract_window_seconds
            ]
            contract_weight = sum(item.weight for item in contract_window)
            if contract_weight + request.weight > self.historical_contract_max_weight:
                oldest = min((item.requested_at for item in contract_window), default=now)
                waits.append(oldest + self.historical_contract_window_seconds - now)

            historical_window = [
                item
                for item in records
                if item.request_kind == request.request_kind
                and now - item.requested_at < self.historical_window_seconds
            ]
            historical_weight = sum(item.weight for item in historical_window)
            if historical_weight + request.weight > self.historical_max_weight:
                oldest = min((item.requested_at for item in historical_window), default=now)
                waits.append(oldest + self.historical_window_seconds - now)

        if request.request_kind == "BID_ASK":
            bid_ask_window = [
                item
                for item in records
                if item.request_kind == request.request_kind
                and item.contract_key == request.contract_key
                and now - item.requested_at < self.bid_ask_window_seconds
            ]
            bid_ask_weight = sum(item.weight for item in bid_ask_window)
            if bid_ask_weight + request.weight > self.bid_ask_max_weight:
                oldest = min((item.requested_at for item in bid_ask_window), default=now)
                waits.append(oldest + self.bid_ask_window_seconds - now)

        return max(0.0, max(waits, default=0.0))

    def reserve(
        self,
        request: IbkrRequestReservation,
        recent: list[IbkrRequestReservation],
    ) -> float:
        """Reserve only when admissible; return a required delay otherwise."""

        delay = self.wait_seconds(request, recent)
        if delay == 0:
            recent.append(request)
        return delay


def _require_profile_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("IBKR pacing request profile hash must be a lower-case SHA-256")


class IbkrPacingLedger(Protocol):
    async def reserve_ibkr_request(
        self,
        *,
        requested_at: datetime,
        request_kind: str,
        contract_key: str,
        request_fingerprint: str,
        weight: int,
        request_profile_sha256: str,
        pacing_policy: IbkrHistoricalPacingPolicy,
    ) -> bool: ...


class IbkrPostgresPacing:
    """Adapter callback bound to one authenticated historical request profile."""

    def __init__(
        self,
        ledger: IbkrPacingLedger,
        *,
        request_profile_sha256: str,
        pacing_policy: IbkrHistoricalPacingPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        retry_seconds: float = 1.0,
    ) -> None:
        _require_profile_sha256(request_profile_sha256)
        if retry_seconds <= 0:
            raise ValueError("IBKR pacing retry seconds must be positive")
        self._ledger = ledger
        self._request_profile_sha256 = request_profile_sha256
        self._pacing_policy = pacing_policy
        self._clock = clock
        self._retry_seconds = retry_seconds

    @property
    def request_profile_sha256(self) -> str:
        return self._request_profile_sha256

    async def reserve(
        self,
        request_kind: str,
        contract_key: str,
        request_fingerprint: str,
        weight: int,
        *,
        request_profile_sha256: str | None = None,
        pacing_policy: IbkrHistoricalPacingPolicy | None = None,
    ) -> float:
        bound_profile_sha256 = (
            self._request_profile_sha256
            if request_profile_sha256 is None
            else request_profile_sha256
        )
        bound_pacing_policy = self._pacing_policy if pacing_policy is None else pacing_policy
        if bound_profile_sha256 != self._request_profile_sha256:
            raise ValueError("IBKR pacing request profile hash does not match its binding")
        if bound_pacing_policy != self._pacing_policy:
            raise ValueError("IBKR pacing policy does not match its binding")
        requested_at = self._clock()
        if requested_at.tzinfo is None or requested_at.utcoffset() is None:
            raise ValueError("IBKR pacing clock must return an aware timestamp")
        if not await self._ledger.reserve_ibkr_request(
            requested_at=requested_at.astimezone(UTC),
            request_kind=request_kind,
            contract_key=contract_key,
            request_fingerprint=request_fingerprint,
            weight=weight,
            request_profile_sha256=bound_profile_sha256,
            pacing_policy=bound_pacing_policy,
        ):
            return self._retry_seconds
        return 0.0
