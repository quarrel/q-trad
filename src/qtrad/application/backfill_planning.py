"""Deterministic construction and serialisation of reviewed backfill plans."""

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from qtrad.domain.events import JsonValue
from qtrad.domain.historical_coverage import (
    BackfillPlan,
    BackfillPlanItem,
    BackfillQuotaEvidence,
    HistoricalResolution,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import ProviderListing
from qtrad.ports.market_data import BackfillRequest

_OPERATOR_ALLOWANCE = "historical_points_weekly_operator_reported"


def build_backfill_plan(
    *,
    universe_name: str,
    universe_hash: str,
    instrument_ids: Sequence[InstrumentId],
    listings: Sequence[ProviderListing],
    preferred_epics: Mapping[InstrumentId, str],
    start: datetime,
    end: datetime,
    remaining_allowance: int,
    quota_observed_at: datetime,
    created_at: datetime,
    reserve_fraction: Decimal = Decimal("0.2"),
    request_chunk_points: int = 1000,
) -> BackfillPlan:
    """Build one exact range plan without selecting or substituting provider listings."""

    if len(set(instrument_ids)) != len(instrument_ids):
        raise ValueError("backfill plan instrument IDs must be unique")
    requested = set(instrument_ids)
    by_instrument: dict[InstrumentId, list[ProviderListing]] = {
        instrument_id: [] for instrument_id in instrument_ids
    }
    for listing in listings:
        if listing.instrument_id not in requested:
            raise ValueError(f"unexpected backfill listing for {listing.instrument_id}")
        by_instrument[listing.instrument_id].append(listing)
    invalid = {
        str(instrument_id): len(matches)
        for instrument_id, matches in by_instrument.items()
        if len(matches) != 1
    }
    if invalid:
        raise ValueError(f"backfill plan requires one active listing per instrument: {invalid}")
    missing_preferences = requested - set(preferred_epics)
    if missing_preferences:
        raise ValueError(
            "backfill plan requires configured provider listings for: "
            + ", ".join(sorted(str(item) for item in missing_preferences))
        )
    mismatches = sorted(
        str(instrument_id)
        for instrument_id, matches in by_instrument.items()
        if matches and matches[0].listing_id.external_id != preferred_epics[instrument_id]
    )
    if mismatches:
        raise ValueError(
            "active listing does not match the selected universe for: " + ", ".join(mismatches)
        )

    quota = BackfillQuotaEvidence(
        allowance_name=_OPERATOR_ALLOWANCE,
        remaining_points=remaining_allowance,
        observed_at=quota_observed_at,
        reserve_fraction=reserve_fraction,
    )
    items = tuple(
        BackfillPlanItem(
            instrument_id=instrument_id,
            listing_id=by_instrument[instrument_id][0].listing_id,
            listing_valid_from=by_instrument[instrument_id][0].valid_from,
            listing_metadata_version=by_instrument[instrument_id][0].metadata_version,
        )
        for instrument_id in sorted(requested)
    )
    unhashed = _backfill_plan_payload(
        plan_hash=None,
        universe_name=universe_name,
        universe_hash=universe_hash,
        created_at=created_at,
        start=start,
        end=end,
        resolution=HistoricalResolution.MINUTE,
        request_chunk_points=request_chunk_points,
        quota=quota,
        items=items,
    )
    plan_hash = hashlib.sha256(
        json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return BackfillPlan(
        plan_hash=plan_hash,
        universe_name=universe_name,
        universe_hash=universe_hash,
        created_at=created_at,
        start=start,
        end=end,
        resolution=HistoricalResolution.MINUTE,
        request_chunk_points=request_chunk_points,
        quota=quota,
        items=items,
    )


def backfill_plan_payload(plan: BackfillPlan) -> dict[str, JsonValue]:
    return _backfill_plan_payload(
        plan_hash=plan.plan_hash,
        universe_name=plan.universe_name,
        universe_hash=plan.universe_hash,
        created_at=plan.created_at,
        start=plan.start,
        end=plan.end,
        resolution=plan.resolution,
        request_chunk_points=plan.request_chunk_points,
        quota=plan.quota,
        items=plan.items,
    )


def verify_backfill_plan_hash(plan: BackfillPlan) -> None:
    payload = backfill_plan_payload(plan)
    observed_hash = str(payload.pop("plan_hash"))
    calculated_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if observed_hash != calculated_hash:
        raise ValueError("backfill plan hash does not match its canonical content")


def backfill_requests(
    plan: BackfillPlan,
    listings: Sequence[ProviderListing],
) -> Iterator[BackfillRequest]:
    """Expand the exact reviewed range into provider-bounded requests."""

    by_identity = {(listing.listing_id, listing.valid_from): listing for listing in listings}
    if len(by_identity) != len(listings):
        raise ValueError("planned backfill listings must be unique")
    for item in plan.items:
        try:
            listing = by_identity[(item.listing_id, item.listing_valid_from)]
        except KeyError as error:
            raise ValueError(
                f"planned listing version was not supplied: {item.listing_id}"
            ) from error
        if (
            listing.instrument_id != item.instrument_id
            or listing.metadata_version != item.listing_metadata_version
        ):
            raise ValueError("supplied listing does not match the planned listing version")
        request_start = plan.start
        while request_start < plan.end:
            request_end = min(
                plan.end,
                request_start + timedelta(minutes=plan.request_chunk_points),
            )
            points = int((request_end - request_start).total_seconds() // 60)
            yield BackfillRequest(
                instrument_id=item.instrument_id,
                listing=listing,
                start=request_start,
                end=request_end,
                maximum_points=points,
                resolution=plan.resolution,
            )
            request_start = request_end


def _backfill_plan_payload(
    *,
    plan_hash: str | None,
    universe_name: str,
    universe_hash: str,
    created_at: datetime,
    start: datetime,
    end: datetime,
    resolution: HistoricalResolution,
    request_chunk_points: int,
    quota: BackfillQuotaEvidence,
    items: Sequence[BackfillPlanItem],
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "universe_name": universe_name,
        "universe_hash": universe_hash,
        "created_at": _utc_text(created_at),
        "start": _utc_text(start),
        "end": _utc_text(end),
        "resolution": resolution.value,
        "request_chunk_points": request_chunk_points,
        "quota": {
            "allowance_name": quota.allowance_name,
            "remaining_points": quota.remaining_points,
            "observed_at": _utc_text(quota.observed_at),
            "reserve_fraction": format(quota.reserve_fraction, "f"),
        },
        "items": [
            {
                "instrument_id": str(item.instrument_id),
                "listing_id": str(item.listing_id),
                "listing_valid_from": _utc_text(item.listing_valid_from),
                "listing_metadata_version": item.listing_metadata_version,
            }
            for item in items
        ],
    }
    if plan_hash is not None:
        return {"plan_hash": plan_hash, **payload}
    return payload


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
