"""Market-data adapter port."""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from qtrad.domain.events import JsonValue
from qtrad.domain.historical_coverage import HistoricalResolution
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.market_data import MarketBar, MarketQuote
from qtrad.domain.operations import AdapterHealth

_LISTING_REVIEW_ECONOMICS_KEYS = {
    "quantity_unit",
    "contract_size",
    "lot_size",
    "one_pip_means",
    "value_of_one_pip",
    "minimum_quantity",
    "price_increment",
}
_MAX_LISTING_REVIEW_CANDIDATES = 100


@dataclass(frozen=True, slots=True)
class MarketDataRecord:
    provider: str
    environment: str
    subscription: str
    deduplication_key: str
    received_time: datetime
    raw_payload: Mapping[str, JsonValue]
    quote: MarketQuote | None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    instrument_id: InstrumentId
    listing: ProviderListing
    start: datetime
    end: datetime
    maximum_points: int
    resolution: HistoricalResolution = HistoricalResolution.MINUTE


class ListingExpiryKind(StrEnum):
    """Canonical classification of a provider listing's expiry semantics."""

    ROLLING = "ROLLING"
    DATED = "DATED"
    UNKNOWN = "UNKNOWN"


class ListingMarketState(StrEnum):
    """Bounded market-state evidence used only during listing review."""

    TRADEABLE = "TRADEABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ListingReviewRejection(StrEnum):
    """Stable fail-closed reasons; provider error text must never enter a review."""

    WRONG_PRODUCT_TYPE = "WRONG_PRODUCT_TYPE"
    NON_ROLLING_EXPIRY = "NON_ROLLING_EXPIRY"
    UNAVAILABLE_MARKET = "UNAVAILABLE_MARKET"
    UNKNOWN_MARKET_STATE = "UNKNOWN_MARKET_STATE"
    MISSING_CURRENCY = "MISSING_CURRENCY"
    WRONG_CURRENCY = "WRONG_CURRENCY"
    MISSING_MINIMUM_DEAL_SIZE = "MISSING_MINIMUM_DEAL_SIZE"
    INVALID_MINIMUM_DEAL_SIZE = "INVALID_MINIMUM_DEAL_SIZE"


@dataclass(frozen=True, slots=True)
class ListingReviewCandidate:
    """Bounded provider listing evidence with no selection or ingestion authority."""

    instrument_id: InstrumentId
    listing_id: ProviderListingId
    display_name: str
    product_type: ProductType
    expiry_kind: ListingExpiryKind
    market_state: ListingMarketState
    currency: str | None
    minimum_deal_size: Decimal | None
    economics: Mapping[str, JsonValue]
    metadata_version: str | None
    rejection_reasons: tuple[ListingReviewRejection, ...]

    def __post_init__(self) -> None:
        if not self.display_name or len(self.display_name) > 200:
            raise ValueError("listing review display name must contain at most 200 characters")
        if len(self.listing_id.external_id) > 200:
            raise ValueError("listing review external ID must contain at most 200 characters")
        if self.currency is not None and (not self.currency or len(self.currency) > 12):
            raise ValueError("listing review currency must contain at most 12 characters")
        if self.metadata_version is not None and (
            not self.metadata_version or len(self.metadata_version) > 64
        ):
            raise ValueError("listing review metadata version must contain at most 64 characters")
        if len(set(self.rejection_reasons)) != len(self.rejection_reasons):
            raise ValueError("listing review rejection reasons must be unique")
        unexpected_economics = set(self.economics) - _LISTING_REVIEW_ECONOMICS_KEYS
        if unexpected_economics:
            raise ValueError(
                "listing review economics contains unexpected fields: "
                + ", ".join(sorted(unexpected_economics))
            )
        if any(
            value is not None and (not isinstance(value, str) or len(value) > 128)
            for value in self.economics.values()
        ):
            raise ValueError("listing review economics values must be bounded strings")
        if (
            self.minimum_deal_size is not None
            and self.minimum_deal_size <= 0
            and ListingReviewRejection.INVALID_MINIMUM_DEAL_SIZE not in self.rejection_reasons
        ):
            raise ValueError("non-positive minimum deal size must be rejected")
        if not self.rejection_reasons:
            if self.product_type is ProductType.UNKNOWN:
                raise ValueError("eligible listing review requires a known product type")
            if self.expiry_kind is not ListingExpiryKind.ROLLING:
                raise ValueError("eligible listing review requires rolling expiry")
            if self.market_state is not ListingMarketState.TRADEABLE:
                raise ValueError("eligible listing review requires a tradeable market")
            if not self.currency or self.minimum_deal_size is None:
                raise ValueError("eligible listing review requires currency and minimum deal size")

    @property
    def eligible(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True, slots=True)
class InstrumentListingReview:
    """All bounded candidates returned for one requested canonical instrument."""

    instrument_id: InstrumentId
    candidates: tuple[ListingReviewCandidate, ...]

    def __post_init__(self) -> None:
        if any(candidate.instrument_id != self.instrument_id for candidate in self.candidates):
            raise ValueError("listing review candidates must match their instrument")
        identifiers = [candidate.listing_id for candidate in self.candidates]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("listing review candidate IDs must be unique")
        if len(self.candidates) > _MAX_LISTING_REVIEW_CANDIDATES:
            raise ValueError(
                "listing review cannot contain more than "
                f"{_MAX_LISTING_REVIEW_CANDIDATES} candidates"
            )


class MarketDataAdapter(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def discover_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[ProviderListing]: ...

    async def review_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[InstrumentListingReview]: ...

    async def subscribe(self, listings: Sequence[ProviderListing]) -> None: ...

    def records(self) -> AsyncIterator[MarketDataRecord]: ...

    def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]: ...

    async def health(self) -> AdapterHealth: ...
