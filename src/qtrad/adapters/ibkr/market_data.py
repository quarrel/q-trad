"""Continuous, market-data-only IBKR native Level-1 capture."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from queue import Queue
from time import monotonic
from typing import Any, cast

from qtrad.adapters.ibkr.capability import (
    IbkrApiIdentity,
    IbkrConnectionIntegrityError,
    IbkrGatewayEndpoint,
    OfficialIbkrCapabilityAdapter,
    _Callback,
    _contract_from_evidence,
    _error_code,
    _error_disposition,
    _is_global_error,
)
from qtrad.adapters.ibkr.session import (
    IbkrRecoveryAction,
    IbkrSessionState,
    IbkrSubscription,
    IbkrSystemCode,
)
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProviderListing
from qtrad.domain.market_data import DataQuality, MarketBar, MarketQuote
from qtrad.domain.modes import BrokerEnvironment
from qtrad.domain.operations import AdapterHealth, HealthStatus, RecoveryAction
from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.ports.market_data import (
    BackfillRequest,
    InstrumentListingReview,
    ListingExpiryKind,
    ListingMarketState,
    ListingReviewCandidate,
    MarketDataAdapter,
    MarketDataRecord,
)

_BID = 1
_ASK = 2
_BID_SIZE = 0
_ASK_SIZE = 3
_DELAYED_BID = 66
_DELAYED_ASK = 67
_DELAYED_BID_SIZE = 69
_DELAYED_ASK_SIZE = 70
_LIVE = 1
_FROZEN = 2
_DELAYED = 3
_DELAYED_FROZEN = 4
_DEFAULT_TICKS = ("BID", "ASK", "BID_SIZE", "ASK_SIZE")
_KNOWN_MARKET_DATA_TYPES = {
    _LIVE: "LIVE",
    _FROZEN: "FROZEN",
    _DELAYED: "DELAYED",
    _DELAYED_FROZEN: "DELAYED_FROZEN",
}
_LIVE_PRICE_TICKS = frozenset({_BID, _ASK})
_LIVE_SIZE_TICKS = frozenset({_BID_SIZE, _ASK_SIZE})
_DELAYED_PRICE_TICKS = frozenset({_DELAYED_BID, _DELAYED_ASK})
_DELAYED_SIZE_TICKS = frozenset({_DELAYED_BID_SIZE, _DELAYED_ASK_SIZE})
_VALID_TICKS = _LIVE_PRICE_TICKS | _LIVE_SIZE_TICKS | _DELAYED_PRICE_TICKS | _DELAYED_SIZE_TICKS
_MAX_RETIRED_REQUESTS = 1024


@dataclass(frozen=True, slots=True)
class _SubscriptionBinding:
    listing: ProviderListing
    evidence: IbkrContractEvidence
    subscription: IbkrSubscription


class _RecoveryFailure(IbkrConnectionIntegrityError):
    def __init__(self, message: str, callbacks: Sequence[_Callback]) -> None:
        super().__init__(message)
        self.callbacks = tuple(callbacks)


class IbkrNativeMarketDataAdapter(OfficialIbkrCapabilityAdapter, MarketDataAdapter):
    """Capture exact, pre-reviewed IBKR contracts through the shared API boundary.

    The adapter intentionally accepts contract evidence as input.  It does not
    discover or choose a provider contract and it has no account or order
    methods.  Each current-generation Level-1 callback becomes one raw
    provider-neutral record; callbacks are never merged or forward-filled.
    """

    def __init__(
        self,
        endpoint: IbkrGatewayEndpoint,
        *,
        pre_reviewed_listings: Sequence[ProviderListing],
        contract_evidence: Mapping[ProviderListingId, IbkrContractEvidence],
        environment: BrokerEnvironment = BrokerEnvironment.IBKR_PAPER,
        request_timeout_seconds: float = 10.0,
        upstream_recovery_timeout_seconds: float = 180.0,
        connect_timeout_seconds: float = 5.0,
        handshake_timeout_seconds: float = 15.0,
        server_time_timeout_seconds: float = 10.0,
        client_factory: Callable[[Queue[_Callback]], Any] | None = None,
        api_identity: IbkrApiIdentity | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        freshness_max_age_seconds: float | None = None,
    ) -> None:
        if not pre_reviewed_listings:
            raise ValueError("IBKR native capture requires pre-reviewed listings")
        listings = tuple(pre_reviewed_listings)
        if len({item.listing_id for item in listings}) != len(listings):
            raise ValueError("IBKR native capture listing IDs must be unique")
        if not contract_evidence:
            raise ValueError("IBKR native capture requires exact contract evidence")
        missing = tuple(
            str(item.listing_id) for item in listings if item.listing_id not in contract_evidence
        )
        if missing:
            raise ValueError("missing exact IBKR contract evidence for " + ", ".join(missing))
        evidence_values = tuple(contract_evidence[item.listing_id] for item in listings)
        if len({item.con_id for item in evidence_values}) != len(evidence_values):
            raise ValueError("IBKR native capture conIds must be unique")
        if environment is not BrokerEnvironment.IBKR_PAPER:
            raise ValueError("IBKR native capture requires the IBKR_PAPER environment")
        super().__init__(
            endpoint,
            request_timeout_seconds=request_timeout_seconds,
            upstream_recovery_timeout_seconds=upstream_recovery_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            handshake_timeout_seconds=handshake_timeout_seconds,
            server_time_timeout_seconds=server_time_timeout_seconds,
            client_factory=client_factory,
            api_identity=api_identity,
            sleep=sleep,
        )
        self._pre_reviewed_listings = listings
        self._environment = environment
        self._listings_by_id = {item.listing_id: item for item in listings}
        self._contract_evidence = dict(contract_evidence)
        self._clock = clock
        if freshness_max_age_seconds is not None and freshness_max_age_seconds <= 0:
            raise ValueError("IBKR evidence freshness threshold must be positive")
        self._freshness_max_age_seconds = freshness_max_age_seconds
        self._current_bindings: dict[int, _SubscriptionBinding] = {}
        self._request_ids_by_listing: dict[str, int] = {}
        self._retired_bindings: OrderedDict[int, tuple[int, _SubscriptionBinding]] = OrderedDict()
        self._market_data_types: dict[int, str] = {}
        self._bid_seen: set[str] = set()
        self._ask_seen: set[str] = set()
        self._last_prices: dict[tuple[str, str], Decimal] = {}
        self._first_bid_at: dict[str, datetime] = {}
        self._last_bid_at: dict[str, datetime] = {}
        self._first_ask_at: dict[str, datetime] = {}
        self._last_ask_at: dict[str, datetime] = {}
        self._last_message_at: datetime | None = None
        self._connected_once = False
        self._terminal_error: IbkrConnectionIntegrityError | None = None
        self._superseded_callbacks = 0
        self._unknown_request_callbacks = 0
        self._cancelled_request_callbacks = 0
        self._pending_records: deque[MarketDataRecord] = deque()

    async def connect(self) -> None:
        if self._current_bindings:
            raise RuntimeError("IBKR native capture cannot connect with active subscriptions")
        self._session.register_subscriptions(())
        self._reset_capture_state()
        await super().connect()
        self._connected_once = True

    async def disconnect(self) -> None:
        try:
            await super().disconnect()
        finally:
            self._current_bindings.clear()
            self._request_ids_by_listing.clear()
            self._retired_bindings.clear()
            self._market_data_types.clear()
            self._session.register_subscriptions(())

    async def force_reconnect(self) -> None:
        """Perform an explicit new connection generation for operator tests."""

        listings = tuple(binding.listing for binding in self._current_binding_values())
        if not listings:
            raise RuntimeError("IBKR native capture has no active subscriptions to reconnect")
        await self.disconnect()
        await self.connect()
        await self.subscribe(listings)

    async def discover_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[ProviderListing]:
        wanted = set(instrument_ids)
        return tuple(item for item in self._pre_reviewed_listings if item.instrument_id in wanted)

    async def review_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[InstrumentListingReview]:
        wanted = set(instrument_ids)
        reviews: list[InstrumentListingReview] = []
        by_instrument: dict[InstrumentId, list[ProviderListing]] = {}
        for listing in self._pre_reviewed_listings:
            if listing.instrument_id in wanted:
                by_instrument.setdefault(listing.instrument_id, []).append(listing)
        for instrument_id in instrument_ids:
            if instrument_id in {item.instrument_id for item in reviews}:
                continue
            candidates = tuple(
                _review_candidate(listing, self._contract_evidence[listing.listing_id])
                for listing in by_instrument.get(instrument_id, ())
            )
            reviews.append(InstrumentListingReview(instrument_id, candidates))
        return tuple(reviews)

    async def subscribe(self, listings: Sequence[ProviderListing]) -> None:
        self._raise_if_terminal()
        if self._client is None:
            raise RuntimeError("IBKR native capture is not connected")
        requested = tuple(listings)
        if not requested:
            raise ValueError("IBKR native capture requires at least one subscription")
        if len({item.listing_id for item in requested}) != len(requested):
            raise ValueError("IBKR native capture subscription listing IDs must be unique")
        bindings = tuple(self._binding(item) for item in requested)
        self._session.register_subscriptions(tuple(item.subscription for item in bindings))
        await self._issue_subscriptions(bindings)

    async def records(self) -> AsyncIterator[MarketDataRecord]:
        if self._client is None:
            raise RuntimeError("IBKR native capture is not connected")
        while True:
            if self._pending_records:
                yield self._pending_records.popleft()
                continue
            self._raise_if_terminal()
            try:
                callback = await self._next_callback(monotonic() + self._request_timeout_seconds)
            except IbkrConnectionIntegrityError as error:
                self._terminal_error = error
                raise
            except TimeoutError:
                continue
            if not self._session.accept_callback(callback.generation):
                self._superseded_callbacks += 1
                continue
            self._update_last_message_at(self._callback_received_time(callback))
            if callback.kind == "current_time":
                if self._session.state is IbkrSessionState.WAITING_SERVER_TIME:
                    self._session.mark_server_time()
                continue
            if _is_global_error(callback):
                record = await self._handle_global_callback(callback)
                if record is not None:
                    yield record
                continue
            binding = self._current_bindings.get(callback.request_id)
            if binding is None:
                binding = self._retired_binding(callback)
                if binding is not None:
                    yield self._normalise_cancelled_request_callback(callback, binding)
                    continue
                yield self._normalise_unknown_request_callback(callback)
                continue
            if callback.kind == "error":
                yield self._normalise_error_callback(callback, binding)
                continue
            record = self._normalise_level1_callback(callback, binding)
            yield record

    async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
        raise NotImplementedError("IBKR native capture does not provide historical backfill")
        yield request  # pragma: no cover

    async def health(self) -> AdapterHealth:
        observed_at = self._received_time()
        snapshot = self._session.snapshot()
        reasons = set(snapshot.reason_codes)
        if self._callbacks.overflowed.is_set():
            reasons.add("CALLBACK_QUEUE_OVERFLOW")
        if self._terminal_error is not None:
            reasons.add("CONNECTION_INTEGRITY_FAILED")
        if self._unknown_request_callbacks:
            reasons.add("UNKNOWN_REQUEST_ID")
        if self._client is None:
            reasons.add("IBKR_SOCKET_NOT_CONNECTED")
        if snapshot.state in {
            IbkrSessionState.WAITING_HANDSHAKE,
            IbkrSessionState.CONNECTING,
        }:
            reasons.add("IBKR_HANDSHAKE_NOT_READY")
        if snapshot.state is IbkrSessionState.WAITING_SERVER_TIME:
            reasons.add("IBKR_SERVER_TIME_NOT_READY")
        if not self._current_bindings:
            reasons.add("NO_ACTIVE_SUBSCRIPTIONS")
        if snapshot.active_subscriptions != snapshot.desired_subscriptions:
            reasons.add("SUBSCRIPTIONS_INCOMPLETE")
        farms = dict(snapshot.farms)
        if any(
            farms.get(name) not in {"CONNECTED", "INACTIVE"}
            for name in ("market_data", "historical")
        ):
            reasons.add("IBKR_REQUIRED_FARM_NOT_READY")
        if any(
            self._market_data_types.get(request_id) != "LIVE"
            for request_id in self._current_bindings
        ):
            reasons.add("MARKET_DATA_TYPE_NOT_LIVE")
        if any(
            str(binding.listing.listing_id) not in self._bid_seen
            for binding in self._current_bindings.values()
        ):
            reasons.add("BID_EVIDENCE_MISSING")
        if any(
            str(binding.listing.listing_id) not in self._ask_seen
            for binding in self._current_bindings.values()
        ):
            reasons.add("ASK_EVIDENCE_MISSING")
        if self._freshness_max_age_seconds is not None:
            for binding in self._current_bindings.values():
                listing_id = str(binding.listing.listing_id)
                if (
                    listing_id in self._last_bid_at
                    and (observed_at - self._last_bid_at[listing_id]).total_seconds()
                    > self._freshness_max_age_seconds
                ):
                    reasons.add("BID_EVIDENCE_STALE")
                if (
                    listing_id in self._last_ask_at
                    and (observed_at - self._last_ask_at[listing_id]).total_seconds()
                    > self._freshness_max_age_seconds
                ):
                    reasons.add("ASK_EVIDENCE_STALE")
        healthy = (
            self._client is not None
            and snapshot.state is IbkrSessionState.CONNECTED
            and not any(_health_reason_blocks(reason) for reason in reasons)
        )
        if healthy:
            status = HealthStatus.HEALTHY
        elif self._client is None and self._connected_once:
            status = HealthStatus.DISCONNECTED
        elif self._client is None:
            status = HealthStatus.STOPPED
        elif snapshot.state is IbkrSessionState.FAILED_OPERATOR:
            status = HealthStatus.DEGRADED
        else:
            status = (
                HealthStatus.STARTING
                if snapshot.state
                in {
                    IbkrSessionState.CONNECTING,
                    IbkrSessionState.WAITING_HANDSHAKE,
                    IbkrSessionState.WAITING_SERVER_TIME,
                    IbkrSessionState.SUBSCRIBING,
                }
                else HealthStatus.DEGRADED
            )
        action = snapshot.recovery_action
        attributes = (
            ("connection_generation", str(snapshot.generation)),
            ("recovery_epoch", str(snapshot.recovery_epoch)),
            ("desired_subscriptions", str(snapshot.desired_subscriptions)),
            ("active_subscriptions", str(snapshot.active_subscriptions)),
            ("callback_queue_capacity", "50000"),
            ("superseded_callbacks", str(self._superseded_callbacks)),
            ("unknown_request_callbacks", str(self._unknown_request_callbacks)),
            ("cancelled_request_callbacks", str(self._cancelled_request_callbacks)),
            ("retired_request_tombstones", str(len(self._retired_bindings))),
            ("market_data_types", _market_data_type_summary(self._market_data_types)),
            ("farms", ",".join(f"{name}={value}" for name, value in snapshot.farms)),
            ("freshness_max_age_seconds", str(self._freshness_max_age_seconds or "disabled")),
            ("first_bid_at", _earliest_time(self._first_bid_at)),
            ("last_bid_at", _latest_time(self._last_bid_at)),
            ("first_ask_at", _earliest_time(self._first_ask_at)),
            ("last_ask_at", _latest_time(self._last_ask_at)),
        )
        return AdapterHealth(
            adapter_name="ibkr-native-capture",
            environment=self._environment,
            status=status,
            observed_at=observed_at,
            last_message_at=self._last_message_at,
            detail=(str(self._terminal_error) if self._terminal_error is not None else None),
            reason_codes=tuple(sorted(reasons)),
            recovery_action=RecoveryAction(action.value),
            attributes=attributes,
        )

    async def _issue_subscriptions(
        self, bindings: Sequence[_SubscriptionBinding], *, resubscribe: bool = False
    ) -> None:
        client = self._client
        if client is None:
            raise RuntimeError("IBKR native capture is not connected")
        if resubscribe:
            current = self._current_binding_values()
            if tuple(bindings) != current:
                raise RuntimeError("IBKR resubscription bindings changed within one recovery epoch")
            request_ids = {
                binding.listing.listing_id: request_id
                for request_id, binding in self._current_bindings.items()
            }
            self._reset_subscription_evidence()
        else:
            self._session.reset_subscription_activity()
            self._reset_subscription_evidence()
            for request_id in tuple(self._current_bindings):
                self._retire_binding(request_id, self._current_bindings[request_id])
                client.cancelMktData(request_id)
            self._current_bindings.clear()
            self._request_ids_by_listing.clear()
            self._market_data_types.clear()
            request_ids = {}
        for binding in bindings:
            if binding.listing.listing_id in request_ids:
                request_id = request_ids[binding.listing.listing_id]
            else:
                request_id = self._request_id()
            if not resubscribe:
                self._current_bindings[request_id] = binding
                self._request_ids_by_listing[str(binding.listing.listing_id)] = request_id
            client.reqMarketDataType(_LIVE)
            client.reqMktData(
                request_id,
                _contract_from_evidence(binding.evidence),
                "",
                False,
                False,
                [],
            )

    async def _handle_global_callback(self, callback: _Callback) -> MarketDataRecord | None:
        error_code = _error_code(callback)
        decision = self._session.on_system_message(
            error_code,
            generation=callback.generation,
        )
        if error_code == int(IbkrSystemCode.UPSTREAM_DISCONNECTED):
            try:
                callbacks, resubscribe_at = await self._await_native_upstream_recovery()
                self._pending_records.extend(
                    await self._process_recovery_callbacks(callbacks, resubscribe_at=resubscribe_at)
                )
            except _RecoveryFailure as error:
                self._pending_records.extend(
                    await self._process_recovery_callbacks(error.callbacks)
                )
                self._terminal_error = error
        elif error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST) and decision.resubscribe:
            await self._issue_subscriptions(tuple(self._current_binding_values()), resubscribe=True)
        elif decision.revalidate_server_time:
            try:
                callbacks = await self._await_server_time_revalidation()
                self._pending_records.extend(await self._process_recovery_callbacks(callbacks))
            except _RecoveryFailure as error:
                self._pending_records.extend(
                    await self._process_recovery_callbacks(error.callbacks)
                )
                self._terminal_error = error
        elif decision.action in {
            IbkrRecoveryAction.RESTART_ADAPTER,
            IbkrRecoveryAction.RESTART_GATEWAY,
            IbkrRecoveryAction.OPERATOR,
        }:
            self._terminal_error = IbkrConnectionIntegrityError(
                f"IBKR native capture recovery requires {decision.action.value} after {error_code}"
            )
        return self._normalise_error_callback(callback)

    async def _await_native_upstream_recovery(
        self,
    ) -> tuple[tuple[_Callback, ...], int | None]:
        callbacks: list[_Callback] = []
        resubscribe_at: int | None = None
        deadline = monotonic() + self._upstream_recovery_timeout_seconds
        while True:
            try:
                callback = await self._next_callback(deadline)
            except TimeoutError as error:
                raise _RecoveryFailure(
                    "IBKR upstream did not recover within the bounded recovery window",
                    callbacks,
                ) from error
            if not self._session.accept_callback(callback.generation):
                self._superseded_callbacks += 1
                continue
            callbacks.append(callback)
            if not _is_global_error(callback):
                continue
            error_code = _error_code(callback)
            decision = self._session.on_system_message(
                error_code,
                generation=callback.generation,
            )
            if error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST):
                if decision.resubscribe:
                    resubscribe_at = callback.arrival_sequence
                return tuple(callbacks), resubscribe_at
            if error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_MAINTAINED):
                if decision.revalidate_server_time:
                    try:
                        callbacks.extend(await self._await_server_time_revalidation())
                    except _RecoveryFailure as error:
                        raise _RecoveryFailure(
                            str(error), (*callbacks, *error.callbacks)
                        ) from error
                return tuple(callbacks), resubscribe_at
            if error_code == int(IbkrSystemCode.UPSTREAM_DISCONNECTED):
                continue
            if decision.action is not IbkrRecoveryAction.NONE:
                raise _RecoveryFailure(
                    f"IBKR native capture recovery failed after {error_code}",
                    callbacks,
                )

    async def _await_server_time_revalidation(self) -> tuple[_Callback, ...]:
        client = self._client
        if client is None:
            raise RuntimeError("IBKR server-time revalidation requires a connected client")
        client.reqCurrentTime()
        callbacks: list[_Callback] = []
        deadline = monotonic() + self._server_time_timeout_seconds
        while True:
            try:
                callback = await self._next_callback(deadline)
            except TimeoutError as error:
                raise _RecoveryFailure(
                    "IBKR server-time revalidation timed out", callbacks
                ) from error
            if not self._session.accept_callback(callback.generation):
                self._superseded_callbacks += 1
                continue
            callbacks.append(callback)
            if callback.kind == "current_time" and callback.request_id == -1:
                self._session.mark_server_time()
                return tuple(callbacks)
            if not _is_global_error(callback):
                continue
            error_code = _error_code(callback)
            decision = self._session.on_system_message(
                error_code,
                generation=callback.generation,
            )
            if decision.action is not IbkrRecoveryAction.NONE:
                raise _RecoveryFailure(
                    f"IBKR server-time revalidation failed after {error_code}",
                    callbacks,
                )

    async def _process_recovery_callbacks(
        self,
        callbacks: Sequence[_Callback],
        *,
        resubscribe_at: int | None = None,
    ) -> tuple[MarketDataRecord, ...]:
        records: list[MarketDataRecord] = []
        for callback in callbacks:
            if callback.arrival_sequence == resubscribe_at:
                await self._issue_subscriptions(
                    tuple(self._current_binding_values()), resubscribe=True
                )
            if callback.kind == "current_time":
                continue
            if _is_global_error(callback):
                records.append(self._normalise_error_callback(callback))
                continue
            binding = self._current_bindings.get(callback.request_id)
            if binding is None:
                binding = self._retired_binding(callback)
                if binding is not None:
                    records.append(self._normalise_cancelled_request_callback(callback, binding))
                    continue
                records.append(self._normalise_unknown_request_callback(callback))
                continue
            records.append(self._normalise_level1_callback(callback, binding))
        return tuple(records)

    def _normalise_level1_callback(
        self, callback: _Callback, binding: _SubscriptionBinding
    ) -> MarketDataRecord:
        self._session.mark_subscription_active(
            str(binding.listing.listing_id), generation=callback.generation
        )
        received = self._callback_received_time(callback)
        self._update_last_message_at(received)
        tick_type = (
            _int_value(callback.values[0]) if callback.kind in {"tick_price", "tick_size"} else None
        )
        raw = self._raw_payload(callback, binding, received, tick_type=tick_type)
        quote: MarketQuote | None = None
        error_code: str | None = None
        error_detail: str | None = None
        if callback.kind == "market_data_type":
            data_type = _KNOWN_MARKET_DATA_TYPES.get(_int_value(callback.values[0]))
            if data_type is None:
                error_code = "IBKR_UNKNOWN_MARKET_DATA_TYPE"
                error_detail = str(callback.values[0])
            else:
                self._market_data_types[callback.request_id] = data_type
                if data_type != "LIVE":
                    error_code = "IBKR_NON_LIVE_MARKET_DATA_TYPE"
                    error_detail = data_type
        elif callback.kind == "tick_price":
            quote, error_code, error_detail = self._quote_from_price_callback(
                callback, binding, received, tick_type
            )
        elif callback.kind == "tick_size":
            if tick_type in _LIVE_SIZE_TICKS | _DELAYED_SIZE_TICKS:
                error_code = "IBKR_TOP_OF_BOOK_SIZE_EVIDENCE"
                error_detail = "bid/ask size is not trade volume"
            else:
                error_code = "IBKR_UNSUPPORTED_MARKET_DATA_TICK"
                error_detail = f"tick_size:{tick_type}"
        else:
            error_code = "IBKR_UNSUPPORTED_MARKET_DATA_CALLBACK"
            error_detail = callback.kind
        return self._record(
            callback=callback,
            binding=binding,
            received=received,
            raw=raw,
            quote=quote,
            error_code=error_code,
            error_detail=error_detail,
        )

    def _quote_from_price_callback(
        self,
        callback: _Callback,
        binding: _SubscriptionBinding,
        received: datetime,
        tick_type: int | None,
    ) -> tuple[MarketQuote | None, str | None, str | None]:
        if tick_type not in _VALID_TICKS:
            return None, "IBKR_UNKNOWN_TICK_TYPE", str(tick_type)
        if tick_type in _LIVE_SIZE_TICKS | _DELAYED_SIZE_TICKS:
            return None, "IBKR_TOP_OF_BOOK_SIZE_EVIDENCE", "bid/ask size is not trade volume"
        value = _decimal_price(callback.values[1])
        if value is None:
            return None, "IBKR_UNAVAILABLE_OR_INVALID_PRICE", str(callback.values[1])
        listing_id = str(binding.listing.listing_id)
        side = "BID" if tick_type in {_BID, _DELAYED_BID} else "ASK"
        opposite = "ASK" if side == "BID" else "BID"
        prior_opposite = self._last_prices.get((listing_id, opposite))
        if prior_opposite is not None and (
            (side == "BID" and value > prior_opposite) or (side == "ASK" and value < prior_opposite)
        ):
            return None, "IBKR_CROSSED_QUOTE", f"{side}={value} crossed {opposite}={prior_opposite}"
        self._last_prices[(listing_id, side)] = value
        if side == "BID":
            self._bid_seen.add(listing_id)
            self._first_bid_at.setdefault(listing_id, received)
            self._last_bid_at[listing_id] = received
        else:
            self._ask_seen.add(listing_id)
            self._first_ask_at.setdefault(listing_id, received)
            self._last_ask_at[listing_id] = received
        data_type = self._market_data_types.get(callback.request_id)
        quality = (
            DataQuality.HEALTHY
            if data_type == "LIVE"
            else DataQuality.DELAYED
            if data_type == "DELAYED"
            else DataQuality.STALE
            if data_type in {"FROZEN", "DELAYED_FROZEN"}
            else DataQuality.PARTIAL
        )
        if side == "BID":
            quote = MarketQuote(
                instrument_id=binding.listing.instrument_id,
                listing_id=binding.listing.listing_id,
                event_time=received,
                received_time=received,
                bid=value,
                ask=None,
                bid_time=received,
                quality=quality,
                source_sequence=str(callback.arrival_sequence),
            )
        else:
            quote = MarketQuote(
                instrument_id=binding.listing.instrument_id,
                listing_id=binding.listing.listing_id,
                event_time=received,
                received_time=received,
                bid=None,
                ask=value,
                ask_time=received,
                quality=quality,
                source_sequence=str(callback.arrival_sequence),
            )
        if data_type != "LIVE":
            return quote, "IBKR_NON_LIVE_MARKET_DATA_TYPE", data_type or "UNCONFIRMED"
        return quote, None, None

    def _normalise_error_callback(
        self, callback: _Callback, binding: _SubscriptionBinding | None = None
    ) -> MarketDataRecord:
        if binding is None and callback.request_id >= 0:
            return self._normalise_unknown_request_callback(callback)
        received = self._callback_received_time(callback)
        self._update_last_message_at(received)
        raw = {
            "callback_type": callback.kind,
            "request_id": callback.request_id,
            "connection_generation": callback.generation,
            "arrival_sequence": callback.arrival_sequence,
            "error_code": _error_code(callback),
            "classification": _error_disposition(callback),
            "diagnostic": callback.diagnostic,
            "message_sha256": callback.message_sha256,
            "callback_values": callback.values,
            "listing_id": str(binding.listing.listing_id) if binding is not None else None,
            "con_id": binding.evidence.con_id if binding is not None else None,
            "received_time": received.isoformat().replace("+00:00", "Z"),
        }
        raw = cast(dict[str, JsonValue], to_json_value(raw))
        return self._record(
            callback=callback,
            binding=binding,
            received=received,
            raw=raw,
            quote=None,
            error_code=f"IBKR_{_error_code(callback)}",
            error_detail=callback.diagnostic or _error_disposition(callback),
        )

    def _normalise_unknown_request_callback(self, callback: _Callback) -> MarketDataRecord:
        self._unknown_request_callbacks += 1
        received = self._callback_received_time(callback)
        self._update_last_message_at(received)
        tick_type = (
            _int_value(callback.values[0]) if callback.kind in {"tick_price", "tick_size"} else None
        )
        raw = self._raw_payload(callback, None, received, tick_type=tick_type)
        return self._record(
            callback=callback,
            binding=None,
            received=received,
            raw=raw,
            quote=None,
            error_code="IBKR_UNKNOWN_REQUEST_ID",
            error_detail=(
                f"request_id={callback.request_id}"
                if callback.kind != "error"
                else (f"request_id={callback.request_id};provider_error={_error_code(callback)}")
            ),
        )

    def _normalise_cancelled_request_callback(
        self, callback: _Callback, binding: _SubscriptionBinding
    ) -> MarketDataRecord:
        self._cancelled_request_callbacks += 1
        received = self._callback_received_time(callback)
        self._update_last_message_at(received)
        tick_type = (
            _int_value(callback.values[0]) if callback.kind in {"tick_price", "tick_size"} else None
        )
        raw = self._raw_payload(callback, binding, received, tick_type=tick_type)
        provider_error = (
            f";provider_error={_error_code(callback)}" if callback.kind == "error" else ""
        )
        return self._record(
            callback=callback,
            binding=binding,
            received=received,
            raw=raw,
            quote=None,
            error_code="IBKR_CANCELLED_REQUEST_CALLBACK",
            error_detail=f"request_id={callback.request_id}{provider_error}",
        )

    def _record(
        self,
        *,
        callback: _Callback,
        binding: _SubscriptionBinding | None,
        received: datetime,
        raw: Mapping[str, JsonValue],
        quote: MarketQuote | None,
        error_code: str | None,
        error_detail: str | None,
    ) -> MarketDataRecord:
        identity = (
            f"{callback.generation}:{callback.arrival_sequence}:{callback.request_id}:"
            f"{binding.evidence.con_id if binding is not None else 'global'}"
        )
        digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return MarketDataRecord(
            provider="ibkr",
            environment=self._environment.value,
            subscription=(
                str(binding.listing.listing_id)
                if binding is not None
                else (
                    "IBKR:UNKNOWN_REQUEST"
                    if error_code == "IBKR_UNKNOWN_REQUEST_ID"
                    else "IBKR:SYSTEM"
                )
            ),
            deduplication_key=f"{identity}:{digest}",
            received_time=received,
            raw_payload=raw,
            payload_representation=RawPayloadRepresentation.CHANGED_FIELDS,
            quote=quote,
            error_code=error_code,
            error_detail=error_detail,
            connection_generation=callback.generation,
            arrival_sequence=callback.arrival_sequence,
        )

    def _raw_payload(
        self,
        callback: _Callback,
        binding: _SubscriptionBinding | None,
        received: datetime,
        *,
        tick_type: int | None,
    ) -> dict[str, JsonValue]:
        evidence = binding.evidence if binding is not None else None
        return cast(
            dict[str, JsonValue],
            to_json_value(
                {
                    "callback_type": callback.kind,
                    "request_id": callback.request_id,
                    "connection_generation": callback.generation,
                    "arrival_sequence": callback.arrival_sequence,
                    "listing_id": (
                        str(binding.listing.listing_id) if binding is not None else None
                    ),
                    "con_id": evidence.con_id if evidence is not None else None,
                    "symbol": evidence.symbol if evidence is not None else None,
                    "local_symbol": evidence.local_symbol if evidence is not None else None,
                    "security_type": evidence.security_type if evidence is not None else None,
                    "exchange": evidence.exchange if evidence is not None else None,
                    "currency": evidence.currency if evidence is not None else None,
                    "trading_class": evidence.trading_class if evidence is not None else None,
                    "tick_type": tick_type,
                    "raw_value": callback.values[1] if len(callback.values) > 1 else None,
                    "callback_values": callback.values,
                    "provider_event_time": None,
                    "event_time_basis": "LOCAL_RECEIVE_TIME_NO_PROVIDER_TICK_TIME",
                    "received_time": received.isoformat().replace("+00:00", "Z"),
                    "message_sha256": callback.message_sha256,
                }
            ),
        )

    def _binding(self, listing: ProviderListing) -> _SubscriptionBinding:
        configured = self._listings_by_id.get(listing.listing_id)
        if configured != listing:
            raise ValueError(
                f"IBKR listing is not the exact pre-reviewed listing: {listing.listing_id}"
            )
        evidence = self._contract_evidence[listing.listing_id]
        subscription = IbkrSubscription(
            listing_id=str(listing.listing_id),
            con_id=evidence.con_id,
            market_data_type="LIVE",
            ticks=_DEFAULT_TICKS,
        )
        return _SubscriptionBinding(listing, evidence, subscription)

    def _current_binding_values(self) -> tuple[_SubscriptionBinding, ...]:
        return tuple(
            binding
            for request_id, binding in self._current_bindings.items()
            if self._request_ids_by_listing.get(str(binding.listing.listing_id)) == request_id
        )

    def _reset_capture_state(self) -> None:
        self._current_bindings.clear()
        self._request_ids_by_listing.clear()
        self._retired_bindings.clear()
        self._reset_subscription_evidence()
        self._last_message_at = None
        self._terminal_error = None
        self._superseded_callbacks = 0
        self._unknown_request_callbacks = 0
        self._cancelled_request_callbacks = 0
        self._pending_records.clear()

    def _reset_subscription_evidence(self) -> None:
        self._market_data_types.clear()
        self._bid_seen.clear()
        self._ask_seen.clear()
        self._last_prices.clear()
        self._first_bid_at.clear()
        self._last_bid_at.clear()
        self._first_ask_at.clear()
        self._last_ask_at.clear()

    def _callback_received_time(self, callback: _Callback) -> datetime:
        if callback.received_time is not None:
            return require_utc(callback.received_time, "IBKR native callback receive time")
        return require_utc(self._clock(), "IBKR native callback receive time")

    def _update_last_message_at(self, received: datetime) -> None:
        if self._last_message_at is None or received > self._last_message_at:
            self._last_message_at = received

    def _retire_binding(self, request_id: int, binding: _SubscriptionBinding) -> None:
        self._retired_bindings[request_id] = (self._session.generation, binding)
        self._retired_bindings.move_to_end(request_id)
        while len(self._retired_bindings) > _MAX_RETIRED_REQUESTS:
            self._retired_bindings.popitem(last=False)

    def _retired_binding(self, callback: _Callback) -> _SubscriptionBinding | None:
        retired = self._retired_bindings.get(callback.request_id)
        if retired is None or retired[0] != callback.generation:
            return None
        return retired[1]

    def _received_time(self) -> datetime:
        return require_utc(self._clock(), "IBKR native observation time")

    def _raise_if_terminal(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error


def _review_candidate(
    listing: ProviderListing, evidence: IbkrContractEvidence
) -> ListingReviewCandidate:
    return ListingReviewCandidate(
        instrument_id=listing.instrument_id,
        listing_id=listing.listing_id,
        display_name=listing.display_name,
        product_type=listing.product_type,
        expiry_kind=ListingExpiryKind.ROLLING,
        market_state=ListingMarketState.TRADEABLE,
        currency=listing.currency,
        minimum_deal_size=listing.minimum_deal_size,
        economics={
            "contract_size": evidence.multiplier,
            "price_increment": str(listing.price_increment)
            if listing.price_increment is not None
            else None,
        },
        metadata_version=listing.metadata_version,
        rejection_reasons=(),
    )


def _market_data_type_summary(values: Mapping[int, str]) -> str:
    return (
        ",".join(f"{request_id}={data_type}" for request_id, data_type in sorted(values.items()))
        or "none"
    )


def _earliest_time(values: Mapping[str, datetime]) -> str:
    return min(values.values()).isoformat() if values else "none"


def _latest_time(values: Mapping[str, datetime]) -> str:
    return max(values.values()).isoformat() if values else "none"


def _health_reason_blocks(reason: str) -> bool:
    return reason not in {
        "IBKR_UPSTREAM_RESTORED_DATA_LOST",
        "SUPERSEDED_GENERATION",
        "SECURITY_DEFINITION_FARM_DISCONNECTED",
    } and not reason.endswith("_FARM_CONNECTED")


def _int_value(value: object) -> int:
    try:
        return int(cast(str | int | float, value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"IBKR callback integer value is invalid: {value!r}") from error


def _decimal_price(value: object) -> Decimal | None:
    try:
        scalar = cast(str | int | float | Decimal, value)
        number = float(scalar)
        if not isfinite(number) or number <= 0:
            return None
        decimal = Decimal(str(scalar))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    return decimal if decimal > 0 else None
