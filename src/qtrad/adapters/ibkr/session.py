"""Provider-bound IBKR session and recovery policy.

The module contains no TWS types. It turns documented IBKR connection and farm
notifications into deterministic state transitions that can be driven by either
the capability probe or the continuous collector.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from time import monotonic
from typing import Final


class IbkrSessionState(StrEnum):
    STOPPED = "STOPPED"
    CONNECTING = "CONNECTING"
    WAITING_HANDSHAKE = "WAITING_HANDSHAKE"
    WAITING_SERVER_TIME = "WAITING_SERVER_TIME"
    CONNECTED = "CONNECTED"
    SUBSCRIBING = "SUBSCRIBING"
    DEGRADED = "DEGRADED"
    BACKOFF = "BACKOFF"
    FAILED_OPERATOR = "FAILED_OPERATOR"


class IbkrRecoveryAction(StrEnum):
    NONE = "NONE"
    RESTART_ADAPTER = "RESTART_ADAPTER"
    RESTART_GATEWAY = "RESTART_GATEWAY"
    OPERATOR = "OPERATOR"


class IbkrSystemCode(IntEnum):
    """IBKR system notifications used by the recovery policy."""

    UPSTREAM_DISCONNECTED = 1100
    UPSTREAM_RESTORED_DATA_LOST = 1101
    UPSTREAM_RESTORED_DATA_MAINTAINED = 1102
    PORT_RESET = 1300
    MARKET_DATA_FARM_DISCONNECTED = 2103
    MARKET_DATA_FARM_CONNECTED = 2104
    HISTORICAL_FARM_DISCONNECTED = 2105
    HISTORICAL_FARM_CONNECTED = 2106
    MARKET_DATA_FARM_INACTIVE = 2107
    HISTORICAL_FARM_INACTIVE = 2108
    TWS_SERVER_DISCONNECTED = 2110
    MARKET_DATA_FARM_CONNECTING = 2119
    SECURITY_DEFINITION_FARM_DISCONNECTED = 2157
    SECURITY_DEFINITION_FARM_CONNECTED = 2158
    PACING_VIOLATION = 100


_FARM_DOWN: Final[Mapping[IbkrSystemCode, str]] = {
    IbkrSystemCode.MARKET_DATA_FARM_DISCONNECTED: "market_data",
    IbkrSystemCode.HISTORICAL_FARM_DISCONNECTED: "historical",
    IbkrSystemCode.SECURITY_DEFINITION_FARM_DISCONNECTED: "security_definition",
}
_FARM_UP: Final[Mapping[IbkrSystemCode, str]] = {
    IbkrSystemCode.MARKET_DATA_FARM_CONNECTED: "market_data",
    IbkrSystemCode.HISTORICAL_FARM_CONNECTED: "historical",
    IbkrSystemCode.SECURITY_DEFINITION_FARM_CONNECTED: "security_definition",
}
_FARM_INACTIVE: Final[frozenset[IbkrSystemCode]] = frozenset(
    {
        IbkrSystemCode.MARKET_DATA_FARM_INACTIVE,
        IbkrSystemCode.HISTORICAL_FARM_INACTIVE,
    }
)
_REQUIRED_FARMS: Final[tuple[str, ...]] = ("market_data", "historical")


@dataclass(frozen=True, slots=True)
class IbkrSessionTimeouts:
    connect_seconds: float = 5.0
    handshake_seconds: float = 15.0
    server_time_seconds: float = 10.0
    contract_seconds: float = 30.0
    historical_seconds: float = 60.0
    upstream_recovery_seconds: float = 180.0
    gateway_restart_after_seconds: float = 300.0
    gateway_restart_cooldown_seconds: float = 900.0
    max_gateway_restarts_per_hour: int = 3

    def __post_init__(self) -> None:
        values = (
            self.connect_seconds,
            self.handshake_seconds,
            self.server_time_seconds,
            self.contract_seconds,
            self.historical_seconds,
            self.upstream_recovery_seconds,
            self.gateway_restart_after_seconds,
            self.gateway_restart_cooldown_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("IBKR session timeouts must be positive")
        if self.max_gateway_restarts_per_hour < 1:
            raise ValueError("IBKR gateway restart limit must be positive")


@dataclass(frozen=True, slots=True)
class IbkrSubscription:
    listing_id: str
    con_id: int
    market_data_type: str
    ticks: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.listing_id or self.con_id <= 0 or not self.market_data_type:
            raise ValueError("IBKR subscription identity is invalid")
        if not self.ticks or len(set(self.ticks)) != len(self.ticks):
            raise ValueError("IBKR subscription ticks must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class IbkrRecoveryDecision:
    action: IbkrRecoveryAction
    reason_code: str
    code: int | None = None
    resubscribe: bool = False
    revalidate_server_time: bool = False


@dataclass(frozen=True, slots=True)
class IbkrSessionSnapshot:
    state: IbkrSessionState
    generation: int
    recovery_epoch: int
    failed_reconnect_cycles: int
    gateway_restart_count: int
    farms: tuple[tuple[str, str], ...]
    desired_subscriptions: int
    active_subscriptions: int
    reason_codes: tuple[str, ...]
    recovery_action: IbkrRecoveryAction = IbkrRecoveryAction.NONE


class IbkrSession:
    """Deterministic state reducer for socket and IBKR upstream lifecycle."""

    def __init__(
        self,
        *,
        timeouts: IbkrSessionTimeouts | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._timeouts = timeouts or IbkrSessionTimeouts()
        self._clock = clock
        self._state = IbkrSessionState.STOPPED
        self._generation = 0
        self._recovery_epoch = 0
        self._failed_reconnect_cycles = 0
        self._gateway_restart_count = 0
        self._gateway_restart_times: list[float] = []
        self._upstream_lost_at: float | None = None
        self._resubscribe_pending = False
        self._last_gateway_restart_at: float | None = None
        self._handshake_seen = False
        self._server_time_seen = False
        self._farms: dict[str, str] = {
            "market_data": "UNKNOWN",
            "historical": "UNKNOWN",
            "security_definition": "UNKNOWN",
        }
        self._desired: dict[str, IbkrSubscription] = {}
        self._active: set[str] = set()
        self._reason_codes: set[str] = set()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def state(self) -> IbkrSessionState:
        return self._state

    def begin_connection(self) -> int:
        self._generation += 1
        self._state = IbkrSessionState.CONNECTING
        self._handshake_seen = False
        self._server_time_seen = False
        self._resubscribe_pending = False
        self._active.clear()
        for farm in self._farms:
            self._farms[farm] = "UNKNOWN"
        self._reason_codes = {"CONNECTING"}
        return self._generation

    def stop(self) -> None:
        """Stop the session without reusing its generation number."""
        self._state = IbkrSessionState.STOPPED
        self._active.clear()
        self._resubscribe_pending = False
        self._reason_codes = {"STOPPED"}

    def mark_socket_connected(self) -> None:
        self._require_state(IbkrSessionState.CONNECTING)
        self._state = IbkrSessionState.WAITING_HANDSHAKE

    def mark_handshake(self) -> None:
        if self._state not in {
            IbkrSessionState.WAITING_HANDSHAKE,
            IbkrSessionState.WAITING_SERVER_TIME,
        }:
            raise RuntimeError("IBKR handshake arrived in an invalid state")
        self._handshake_seen = True
        self._state = IbkrSessionState.WAITING_SERVER_TIME

    def mark_server_time(self) -> None:
        if not self._handshake_seen:
            raise RuntimeError("IBKR server time cannot precede the handshake")
        self._server_time_seen = True
        self._failed_reconnect_cycles = 0
        self._upstream_lost_at = None
        self._reason_codes.discard("CONNECTING")
        self._reason_codes.discard("IBKR_UPSTREAM_DISCONNECTED")
        self._refresh_state()

    def _mark_degraded(self) -> None:
        if self._state not in {
            IbkrSessionState.CONNECTING,
            IbkrSessionState.WAITING_HANDSHAKE,
        }:
            self._state = IbkrSessionState.DEGRADED

    def _refresh_state(self) -> None:
        if self._state in {
            IbkrSessionState.STOPPED,
            IbkrSessionState.CONNECTING,
            IbkrSessionState.WAITING_HANDSHAKE,
            IbkrSessionState.BACKOFF,
            IbkrSessionState.FAILED_OPERATOR,
        }:
            return
        if not self._handshake_seen or not self._server_time_seen:
            return
        if (
            self._upstream_lost_at is not None
            or any(self._farms[name] == "DISCONNECTED" for name in _REQUIRED_FARMS)
            or "IBKR_TWS_SERVER_DISCONNECTED" in self._reason_codes
            or any(reason.startswith("UNKNOWN_GLOBAL_CODE_") for reason in self._reason_codes)
        ):
            self._state = IbkrSessionState.DEGRADED
        elif self._active == set(self._desired):
            self._state = IbkrSessionState.CONNECTED
        elif self._desired:
            self._state = IbkrSessionState.SUBSCRIBING
        else:
            self._state = IbkrSessionState.CONNECTED

    def register_subscriptions(self, subscriptions: tuple[IbkrSubscription, ...]) -> None:
        if len({item.listing_id for item in subscriptions}) != len(subscriptions):
            raise ValueError("IBKR subscription listing IDs must be unique")
        self._desired = {item.listing_id: item for item in subscriptions}
        self._active.intersection_update(self._desired)
        self._refresh_state()

    def reset_subscription_activity(self) -> None:
        """Start a fresh delivery epoch for the current desired subscriptions."""
        self._active.clear()
        self._refresh_state()

    def mark_subscription_active(self, listing_id: str, *, generation: int) -> None:
        if not self.accept_callback(generation):
            return
        if listing_id not in self._desired:
            raise KeyError(f"unknown IBKR subscription {listing_id}")
        self._active.add(listing_id)
        self._refresh_state()

    def accept_callback(self, generation: int) -> bool:
        """Accept only callbacks from the current socket generation."""
        if generation == self._generation:
            return True
        self._reason_codes.add("SUPERSEDED_GENERATION")
        return False

    def mark_reconnect_failed(self) -> None:
        self._failed_reconnect_cycles += 1
        self._state = IbkrSessionState.BACKOFF
        self._reason_codes.add("RECONNECT_FAILED")

    def mark_socket_closed(self) -> IbkrRecoveryDecision:
        self._state = IbkrSessionState.BACKOFF
        self._active.clear()
        self._reason_codes.add("API_SOCKET_CLOSED")
        self._failed_reconnect_cycles += 1
        return IbkrRecoveryDecision(
            IbkrRecoveryAction.RESTART_ADAPTER,
            "API_SOCKET_CLOSED",
        )

    def on_system_message(
        self,
        code: int,
        *,
        request_id: int = -1,
        generation: int | None = None,
        now: float | None = None,
    ) -> IbkrRecoveryDecision:
        observed_at = self._clock() if now is None else now
        if generation is not None and generation != self._generation:
            self._reason_codes.add("SUPERSEDED_GENERATION")
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "SUPERSEDED_GENERATION",
                code=code,
            )
        if request_id >= 0:
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "REQUEST_FAILED",
                code=code,
            )
        try:
            system_code = IbkrSystemCode(code)
        except ValueError:
            self._mark_degraded()
            reason = f"UNKNOWN_GLOBAL_CODE_{code}"
            self._reason_codes.add(reason)
            return IbkrRecoveryDecision(IbkrRecoveryAction.NONE, reason, code=code)

        if system_code == IbkrSystemCode.UPSTREAM_DISCONNECTED:
            self._mark_degraded()
            self._resubscribe_pending = False
            if self._upstream_lost_at is None:
                self._upstream_lost_at = observed_at
            self._reason_codes.add("IBKR_UPSTREAM_DISCONNECTED")
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "IBKR_UPSTREAM_DISCONNECTED",
                code=code,
            )
        if system_code == IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST:
            if self._resubscribe_pending:
                return IbkrRecoveryDecision(
                    IbkrRecoveryAction.NONE,
                    "IBKR_RESUBSCRIPTION_ALREADY_REQUESTED",
                    code=code,
                )
            self._recovery_epoch += 1
            self._upstream_lost_at = None
            self._reason_codes.discard("IBKR_UPSTREAM_DISCONNECTED")
            self._state = (
                IbkrSessionState.SUBSCRIBING if self._desired else IbkrSessionState.CONNECTED
            )
            self._active.clear()
            self._resubscribe_pending = True
            self._reason_codes.add("IBKR_UPSTREAM_RESTORED_DATA_LOST")
            self._refresh_state()
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "IBKR_UPSTREAM_RESTORED_DATA_LOST",
                code=code,
                resubscribe=bool(self._desired),
            )
        if system_code == IbkrSystemCode.UPSTREAM_RESTORED_DATA_MAINTAINED:
            self._state = IbkrSessionState.WAITING_SERVER_TIME
            self._server_time_seen = False
            self._reason_codes.discard("IBKR_UPSTREAM_DISCONNECTED")
            self._resubscribe_pending = False
            self._upstream_lost_at = None
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "IBKR_UPSTREAM_RESTORED_DATA_MAINTAINED",
                code=code,
                revalidate_server_time=True,
            )
        if system_code == IbkrSystemCode.PORT_RESET:
            self._generation += 1
            self._handshake_seen = False
            self._server_time_seen = False
            self._state = IbkrSessionState.BACKOFF
            self._active.clear()
            self._reason_codes.add("IBKR_PORT_RESET")
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.RESTART_ADAPTER,
                "IBKR_PORT_RESET",
                code=code,
            )
        if system_code in _FARM_DOWN:
            farm = _FARM_DOWN[system_code]
            self._farms[farm] = "DISCONNECTED"
            if farm in _REQUIRED_FARMS:
                self._mark_degraded()
            else:
                self._refresh_state()
            reason = f"{farm.upper()}_FARM_DISCONNECTED"
            self._reason_codes.add(reason)
            return IbkrRecoveryDecision(IbkrRecoveryAction.NONE, reason, code=code)
        if system_code in _FARM_UP:
            farm = _FARM_UP[system_code]
            self._farms[farm] = "CONNECTED"
            self._reason_codes.discard(f"{farm.upper()}_FARM_DISCONNECTED")
            self._refresh_state()
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                f"{farm.upper()}_FARM_CONNECTED",
                code=code,
            )
        if system_code in _FARM_INACTIVE:
            farm = (
                "market_data"
                if system_code == IbkrSystemCode.MARKET_DATA_FARM_INACTIVE
                else "historical"
            )
            self._farms[farm] = "INACTIVE"
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                f"{farm.upper()}_FARM_INACTIVE",
                code=code,
            )
        if system_code == IbkrSystemCode.MARKET_DATA_FARM_CONNECTING:
            self._farms["market_data"] = "CONNECTING"
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "MARKET_DATA_FARM_CONNECTING",
                code=code,
            )
        if system_code == IbkrSystemCode.TWS_SERVER_DISCONNECTED:
            self._mark_degraded()
            self._reason_codes.add("IBKR_TWS_SERVER_DISCONNECTED")
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "IBKR_TWS_SERVER_DISCONNECTED",
                code=code,
            )
        if system_code == IbkrSystemCode.PACING_VIOLATION:
            self._state = IbkrSessionState.FAILED_OPERATOR
            self._reason_codes.add("IBKR_PACING_VIOLATION")
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.OPERATOR,
                "IBKR_PACING_VIOLATION",
                code=code,
            )
        raise AssertionError(f"unhandled IBKR system code {code}")

    def poll(self, *, now: float | None = None) -> IbkrRecoveryDecision:
        observed_at = self._clock() if now is None else now
        if self._upstream_lost_at is None:
            return IbkrRecoveryDecision(IbkrRecoveryAction.NONE, "NO_RECOVERY_REQUIRED")
        if observed_at - self._upstream_lost_at < self._timeouts.gateway_restart_after_seconds:
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "UPSTREAM_RECOVERY_PENDING",
            )
        self._gateway_restart_times = [
            value for value in self._gateway_restart_times if observed_at - value < 3600
        ]
        if self._failed_reconnect_cycles < 2:
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.RESTART_ADAPTER,
                "RECONNECT_BEFORE_GATEWAY",
            )
        if (
            self._last_gateway_restart_at is not None
            and observed_at - self._last_gateway_restart_at
            < self._timeouts.gateway_restart_cooldown_seconds
        ):
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.NONE,
                "GATEWAY_RESTART_COOLDOWN",
            )
        if len(self._gateway_restart_times) >= self._timeouts.max_gateway_restarts_per_hour:
            self._state = IbkrSessionState.FAILED_OPERATOR
            self._reason_codes.add("GATEWAY_RESTART_LIMIT_EXCEEDED")
            return IbkrRecoveryDecision(
                IbkrRecoveryAction.OPERATOR,
                "GATEWAY_RESTART_LIMIT_EXCEEDED",
            )
        self._gateway_restart_times.append(observed_at)
        self._last_gateway_restart_at = observed_at
        self._gateway_restart_count += 1
        self._state = IbkrSessionState.BACKOFF
        self._reason_codes.add("GATEWAY_RESTART_REQUIRED")
        return IbkrRecoveryDecision(
            IbkrRecoveryAction.RESTART_GATEWAY,
            "GATEWAY_RESTART_REQUIRED",
        )

    def snapshot(self) -> IbkrSessionSnapshot:
        recovery_action = IbkrRecoveryAction.NONE
        if self._state is IbkrSessionState.FAILED_OPERATOR:
            recovery_action = IbkrRecoveryAction.OPERATOR
        elif "GATEWAY_RESTART_REQUIRED" in self._reason_codes:
            recovery_action = IbkrRecoveryAction.RESTART_GATEWAY
        elif "IBKR_PORT_RESET" in self._reason_codes:
            recovery_action = IbkrRecoveryAction.RESTART_ADAPTER
        return IbkrSessionSnapshot(
            state=self._state,
            generation=self._generation,
            recovery_epoch=self._recovery_epoch,
            failed_reconnect_cycles=self._failed_reconnect_cycles,
            gateway_restart_count=self._gateway_restart_count,
            farms=tuple(sorted(self._farms.items())),
            desired_subscriptions=len(self._desired),
            active_subscriptions=len(self._active),
            reason_codes=tuple(sorted(self._reason_codes)),
            recovery_action=recovery_action,
        )

    def reconnect_delay(self, attempt: int, random_fraction: float) -> float:
        if attempt < 0 or not 0 <= random_fraction <= 1:
            raise ValueError("IBKR reconnect jitter inputs are invalid")
        cap = min(30.0, float(2**attempt))
        return cap * random_fraction

    def _require_state(self, expected: IbkrSessionState) -> None:
        if self._state != expected:
            raise RuntimeError(f"IBKR session expected {expected}, got {self._state}")
