import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from qtrad.application.backfill_planning import (
    backfill_plan_payload,
    backfill_requests,
    build_backfill_plan,
)
from qtrad.domain.identifiers import ProviderListingId
from qtrad.domain.instruments import INITIAL_INSTRUMENTS, ProductType, ProviderListing
from qtrad.runtime.backfill_plan import (
    decode_backfill_plan,
    load_backfill_plan,
    write_backfill_plan,
)

CREATED_AT = datetime(2026, 7, 18, 3, tzinfo=UTC)
START = datetime(2026, 7, 17, 22, tzinfo=UTC)
END = datetime(2026, 7, 18, 0, tzinfo=UTC)


def _listing(index: int = 0, *, metadata_version: str = "version-1") -> ProviderListing:
    instrument = INITIAL_INSTRUMENTS[index]
    return ProviderListing(
        listing_id=ProviderListingId("ig", "demo", f"FIXTURE.{index}"),
        instrument_id=instrument.instrument_id,
        display_name=instrument.display_name,
        product_type=(
            ProductType.SPOT_FX if instrument.base_currency is not None else ProductType.ROLLING_CFD
        ),
        currency=instrument.quote_currency,
        minimum_deal_size=Decimal("0.5"),
        price_increment=Decimal("0.0001"),
        valid_from=datetime(2026, 7, 14, tzinfo=UTC),
        valid_to=None,
        metadata_version=metadata_version,
    )


def _plan(
    *,
    end: datetime = END,
    listings: tuple[ProviderListing, ...] | None = None,
    remaining_allowance: int = 1000,
    preferred_epic: str = "FIXTURE.0",
):
    listing = _listing()
    return build_backfill_plan(
        universe_name="capture-v1",
        universe_hash="a" * 64,
        instrument_ids=(listing.instrument_id,),
        listings=(listing,) if listings is None else listings,
        preferred_epics={listing.instrument_id: preferred_epic},
        start=START,
        end=end,
        remaining_allowance=remaining_allowance,
        quota_observed_at=CREATED_AT,
        created_at=CREATED_AT,
    )


def test_plan_hash_covers_exact_range_listing_version_and_quota() -> None:
    plan = _plan()
    repeated = _plan()

    assert plan == repeated
    assert plan.points_per_instrument == 120
    assert plan.requested_points == 120
    assert plan.items[0].listing_metadata_version == "version-1"
    assert _plan(end=END + timedelta(minutes=1)).plan_hash != plan.plan_hash
    assert _plan(listings=(replace(_listing(), metadata_version="version-2"),)).plan_hash != (
        plan.plan_hash
    )
    assert _plan(remaining_allowance=999).plan_hash != plan.plan_hash


def test_plan_requires_exact_listings_and_reserved_quota() -> None:
    listing = _listing()
    with pytest.raises(ValueError, match="one active listing"):
        _plan(listings=())
    with pytest.raises(ValueError, match="unexpected backfill listing"):
        _plan(listings=(listing, _listing(1)))
    with pytest.raises(ValueError, match="does not match the selected universe"):
        _plan(preferred_epic="FIXTURE.WRONG")
    with pytest.raises(ValueError, match="exceeds quota"):
        _plan(remaining_allowance=100)


def test_plan_decoder_rejects_tampering_and_unknown_fields() -> None:
    payload = backfill_plan_payload(_plan())
    encoded = json.dumps(payload)

    assert decode_backfill_plan(encoded) == _plan()
    payload["end"] = "2026-07-18T00:01:00Z"
    with pytest.raises(ValueError, match="hash does not match"):
        decode_backfill_plan(json.dumps(payload))
    payload = backfill_plan_payload(_plan())
    payload["automatic_listing_substitute"] = True
    with pytest.raises(ValueError, match="automatic_listing_substitute"):
        decode_backfill_plan(json.dumps(payload))


def test_plan_file_is_non_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "reviewed-plan.json"
    plan = _plan()

    write_backfill_plan(path, plan)

    assert load_backfill_plan(path) == plan
    with pytest.raises(FileExistsError, match="already exists"):
        write_backfill_plan(path, plan)


def test_long_exact_range_is_split_into_provider_bounded_requests() -> None:
    end = START + timedelta(minutes=2500)
    plan = _plan(end=end, remaining_allowance=4000)

    requests = tuple(backfill_requests(plan, (_listing(),)))

    assert [request.maximum_points for request in requests] == [1000, 1000, 500]
    assert requests[0].start == START
    assert requests[-1].end == end
    assert all(request.resolution == plan.resolution for request in requests)
