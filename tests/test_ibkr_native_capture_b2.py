"""B2 composition, identity, freshness and bounded-persistence fixtures."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrad.application.persistence import (
    BoundedPersistenceWorker,
    PersistenceBackpressureError,
    PersistenceWorkerError,
)
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.modes import BrokerEnvironment
from qtrad.domain.operations import AdapterHealth, HealthStatus, RecoveryAction
from qtrad.ports.capture_feed import CaptureIdentity
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.ports.market_data import MarketDataRecord
from qtrad.runtime.ibkr_native_capture import IbkrNativeCaptureConfiguration

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _listing() -> ProviderListing:
    return ProviderListing(
        listing_id=ProviderListingId("ibkr", "IBKR_PAPER", "eur-usd"),
        instrument_id=InstrumentId("fx:eur-usd"),
        display_name="EUR/USD",
        product_type=ProductType.SPOT_FX,
        currency="USD",
        minimum_deal_size=Decimal("1"),
        price_increment=Decimal("0.00005"),
        valid_from=_NOW,
        valid_to=None,
        metadata_version="review-v1",
    )


def _evidence() -> IbkrContractEvidence:
    return IbkrContractEvidence(
        con_id=123,
        symbol="EUR",
        local_symbol="EUR.USD",
        security_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
        trading_class="",
        multiplier="",
        minimum_tick=Decimal("0.00005"),
        market_rule_ids=("26",),
        valid_exchanges=("IDEALPRO",),
        long_name="EUR.USD",
        underlier_con_id=None,
        timezone="UTC",
        trading_hours="20260808:0000-2400",
        liquid_hours="20260808:0000-2400",
    )


def test_reviewed_native_configuration_is_immutable_and_exact() -> None:
    listing = _listing()
    configuration = IbkrNativeCaptureConfiguration.from_reviewed(
        (listing,), {listing.listing_id: _evidence()}
    )

    assert configuration.identity == CaptureIdentity(
        provider="ibkr",
        environment=BrokerEnvironment.IBKR_PAPER.value,
        source_class=MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
        capture_source_id="ibkr-paper-v1",
        universe_id="capture-ibkr-v1",
        configuration_hash=configuration.configuration_hash,
    )

    with pytest.raises(ValueError, match="exact contract evidence"):
        IbkrNativeCaptureConfiguration.from_reviewed((listing,), {})


class _PersistenceService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.processed = 0

    async def process(self, record: MarketDataRecord) -> None:
        self.processed += 1
        if self.fail:
            raise ConnectionError("database unavailable")


def _record(sequence: int) -> MarketDataRecord:
    return MarketDataRecord(
        provider="ibkr",
        environment="IBKR_PAPER",
        subscription="ibkr:IBKR_PAPER:eur-usd",
        deduplication_key=f"callback-{sequence}",
        received_time=_NOW,
        raw_payload={"arrival_sequence": sequence},
        payload_representation=RawPayloadRepresentation.CHANGED_FIELDS,
        quote=None,
        connection_generation=1,
        arrival_sequence=sequence,
    )


def _health() -> AdapterHealth:
    return AdapterHealth(
        adapter_name="ibkr-native-capture",
        environment=BrokerEnvironment.IBKR_PAPER,
        status=HealthStatus.HEALTHY,
        observed_at=_NOW,
        last_message_at=_NOW,
    )


@pytest.mark.asyncio
async def test_bounded_persistence_reports_drops_and_fails_closed() -> None:
    service = _PersistenceService()
    worker = BoundedPersistenceWorker(service, capacity=1)
    worker.start()
    assert worker.submit_nowait(_record(1)) is True
    with pytest.raises(PersistenceBackpressureError):
        worker.submit_nowait(_record(2))
    await worker.drain_and_stop()

    snapshot = worker.snapshot()
    assert snapshot.records_received == 2
    assert snapshot.persisted == 0
    assert snapshot.failed == 0
    assert snapshot.dropped == 2
    composed = worker.compose_health(_health())
    assert composed.status is HealthStatus.DEGRADED
    assert composed.recovery_action is RecoveryAction.OPERATOR
    assert "PERSISTENCE_RECORDS_DROPPED" in composed.reason_codes
    assert dict(composed.attributes)["queue_capacity"] == "1"


@pytest.mark.asyncio
async def test_persistence_failure_is_visible_in_composed_health() -> None:
    worker = BoundedPersistenceWorker(_PersistenceService(fail=True), capacity=2)
    worker.start()
    assert worker.submit_nowait(_record(1)) is True
    with pytest.raises(PersistenceWorkerError):
        await asyncio.wait_for(worker.wait_for_failure(), timeout=1)
    await worker.drain_and_stop()

    snapshot = worker.snapshot()
    assert snapshot.persisted == 0
    assert snapshot.failed == 1
    composed = worker.compose_health(_health())
    assert composed.status is HealthStatus.DEGRADED
    assert composed.recovery_action is RecoveryAction.OPERATOR
    assert "PERSISTENCE_FAILURE" in composed.reason_codes


def test_native_identity_rejects_mismatched_source_class() -> None:
    with pytest.raises(ValueError, match="provider ig"):
        CaptureIdentity(
            provider="ibkr",
            environment="IBKR_PAPER",
            source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
            capture_source_id="ibkr-paper-v1",
            universe_id="capture-ibkr-v1",
            configuration_hash="a" * 64,
        )
