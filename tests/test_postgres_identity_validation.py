from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.domain.identifiers import ProviderListingId
from qtrad.domain.instruments import INITIAL_INSTRUMENTS, ProductType, ProviderListing
from qtrad.domain.modes import BrokerEnvironment, RunKind


def _store_without_database() -> PostgresAuditStore:
    return PostgresAuditStore(cast(AsyncEngine, object()))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_hash", ["", "a" * 63, "A" * 64, "g" * 64])
async def test_start_run_rejects_invalid_configuration_hash_before_database_access(
    invalid_hash: str,
) -> None:
    store = _store_without_database()

    with pytest.raises(ValueError, match="run configuration hash must be lower-case SHA-256"):
        await store.start_run(
            kind=RunKind.INGESTION,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=invalid_hash,
            started_at=datetime(2026, 7, 14, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_listing_validation_rejects_invalid_universe_hash_before_database_access() -> None:
    instrument = INITIAL_INSTRUMENTS[0]
    listing = ProviderListing(
        listing_id=ProviderListingId("ig", "demo", "CS.D.AUDUSD.CFD.IP"),
        instrument_id=instrument.instrument_id,
        display_name="AUD/USD",
        product_type=ProductType.SPOT_FX,
        currency="USD",
        minimum_deal_size=Decimal("0.5"),
        price_increment=Decimal("0.0001"),
        valid_from=datetime(2026, 7, 14, tzinfo=UTC),
        valid_to=None,
        metadata_version="fixture",
    )
    store = _store_without_database()

    with pytest.raises(
        ValueError, match="provider listing universe hash must be lower-case SHA-256"
    ):
        await store.validate_provider_listing(
            listing,
            universe_hash="not-a-sha256",
            observed_at=datetime(2026, 7, 14, tzinfo=UTC),
        )
