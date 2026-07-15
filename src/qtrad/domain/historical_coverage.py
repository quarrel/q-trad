"""Immutable values for reviewed historical-coverage plans."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from qtrad.domain.identifiers import InstrumentId, ProviderListingId


class HistoricalResolution(StrEnum):
    MINUTE = "MINUTE"


@dataclass(frozen=True, slots=True)
class BackfillPlanItem:
    instrument_id: InstrumentId
    listing_id: ProviderListingId
    listing_valid_from: datetime
    listing_metadata_version: str

    def __post_init__(self) -> None:
        _require_utc(self.listing_valid_from, "listing valid-from")
        if not self.listing_metadata_version or len(self.listing_metadata_version) > 256:
            raise ValueError("listing metadata version must contain between 1 and 256 characters")


@dataclass(frozen=True, slots=True)
class BackfillQuotaEvidence:
    allowance_name: str
    remaining_points: int
    observed_at: datetime
    reserve_fraction: Decimal

    def __post_init__(self) -> None:
        if not self.allowance_name or len(self.allowance_name) > 200:
            raise ValueError("backfill quota allowance name is required")
        if self.remaining_points <= 0:
            raise ValueError("backfill quota remaining points must be positive")
        _require_utc(self.observed_at, "quota observation time")
        if not Decimal("0") <= self.reserve_fraction < Decimal("1"):
            raise ValueError("backfill quota reserve fraction must be in [0, 1)")

    @property
    def usable_points(self) -> int:
        return int(
            (
                Decimal(self.remaining_points) * (Decimal("1") - self.reserve_fraction)
            ).to_integral_value(rounding=ROUND_FLOOR)
        )


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    plan_hash: str
    universe_name: str
    universe_hash: str
    created_at: datetime
    start: datetime
    end: datetime
    resolution: HistoricalResolution
    request_chunk_points: int
    quota: BackfillQuotaEvidence
    items: tuple[BackfillPlanItem, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.plan_hash, "backfill plan hash")
        if not self.universe_name or len(self.universe_name) > 64:
            raise ValueError("backfill plan universe name is required")
        _require_sha256(self.universe_hash, "backfill universe hash")
        _require_utc(self.created_at, "backfill plan creation time")
        _require_utc(self.start, "backfill range start")
        _require_utc(self.end, "backfill range end")
        if self.start.second or self.start.microsecond or self.end.second or self.end.microsecond:
            raise ValueError("backfill range must align to UTC minute boundaries")
        if self.end <= self.start:
            raise ValueError("backfill range end must follow its start")
        if self.quota.observed_at > self.created_at:
            raise ValueError("backfill quota evidence cannot postdate plan creation")
        if not 1 <= self.request_chunk_points <= 1000:
            raise ValueError("backfill request chunks must contain between 1 and 1000 points")
        if not self.items:
            raise ValueError("backfill plan requires explicit instruments")
        if len(self.items) > 100:
            raise ValueError("backfill plan cannot exceed 100 instruments")
        instrument_ids = [item.instrument_id for item in self.items]
        listing_ids = [item.listing_id for item in self.items]
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("backfill plan instrument IDs must be unique")
        if len(set(listing_ids)) != len(listing_ids):
            raise ValueError("backfill plan listing IDs must be unique")
        if any(
            item.listing_id.provider != "ig" or item.listing_id.environment != "demo"
            for item in self.items
        ):
            raise ValueError("backfill plans support IG demo listings only")
        if self.requested_points > self.quota.usable_points:
            raise ValueError("backfill plan exceeds quota after its reserved allowance")

    @property
    def points_per_instrument(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    @property
    def requested_points(self) -> int:
        return self.points_per_instrument * len(self.items)


def _require_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError(f"{field} must be timezone-aware")
    if offset.total_seconds() != 0:
        raise ValueError(f"{field} must use UTC")


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be lower-case SHA-256")
