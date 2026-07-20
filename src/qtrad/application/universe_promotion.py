"""Fail-closed promotion of reviewed listings into an explicit universe release."""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import AssetClass, Instrument, ProductType
from qtrad.domain.time import require_utc
from qtrad.ports.market_data import (
    InstrumentListingReview,
    ListingExpiryKind,
    ListingMarketState,
    ListingReviewCandidate,
)

_RELEASE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class ExplicitListingSelection:
    instrument_id: InstrumentId
    listing_id: ProviderListingId


@dataclass(frozen=True, slots=True)
class ExplicitSelectionSet:
    catalogue_hash: str
    review_hash: str
    selections: tuple[ExplicitListingSelection, ...]


@dataclass(frozen=True, slots=True)
class UniversePromotion:
    release_name: str
    source_catalogue_name: str
    source_catalogue_hash: str
    source_review_hash: str
    selection_hash: str
    promoted_at: datetime
    instruments: tuple[Instrument, ...]
    quarantined_instrument_ids: tuple[InstrumentId, ...]
    preferred_epics: Mapping[InstrumentId, str]

    def __post_init__(self) -> None:
        require_utc(self.promoted_at, "universe promotion promoted_at")


def promote_reviewed_universe(
    *,
    release_name: str,
    catalogue_name: str,
    catalogue_hash: str,
    instruments: Sequence[Instrument],
    review_catalogue_name: str,
    review_catalogue_hash: str,
    review_hash: str,
    reviews: Sequence[InstrumentListingReview],
    selection_set: ExplicitSelectionSet,
    promoted_at: datetime,
) -> UniversePromotion:
    """Require an exact explicit selection from eligible, hash-bound review evidence."""

    require_utc(promoted_at, "universe promotion promoted_at")
    if not _RELEASE_NAME.fullmatch(release_name):
        raise ValueError("capture release name must be lower-case words separated by hyphens")
    _require_sha256(catalogue_hash, "catalogue hash")
    _require_sha256(review_hash, "review hash")
    if catalogue_name != review_catalogue_name or catalogue_hash != review_catalogue_hash:
        raise ValueError("listing review does not match the candidate catalogue")
    if selection_set.catalogue_hash != catalogue_hash:
        raise ValueError("listing selections have a stale catalogue hash")
    if selection_set.review_hash != review_hash:
        raise ValueError("listing selections have a stale review hash")

    instruments_by_id = {instrument.instrument_id: instrument for instrument in instruments}
    if not instruments_by_id or len(instruments_by_id) != len(instruments):
        raise ValueError("capture promotion instruments must be present and unique")
    reviews_by_id = {review.instrument_id: review for review in reviews}
    if len(reviews_by_id) != len(reviews):
        raise ValueError("capture promotion reviews must be unique per instrument")
    _require_exact_ids(
        expected=set(instruments_by_id),
        actual=set(reviews_by_id),
        evidence_name="listing review",
    )

    selections_by_id = {
        selection.instrument_id: selection for selection in selection_set.selections
    }
    if len(selections_by_id) != len(selection_set.selections):
        raise ValueError("listing selections must be unique per instrument")
    selected_instrument_ids = set(selections_by_id)
    if not selected_instrument_ids:
        raise ValueError("listing selections must contain at least one instrument")
    if extraneous := selected_instrument_ids - set(instruments_by_id):
        raise ValueError(
            "listing selections contains extraneous instruments: "
            + ", ".join(sorted(map(str, extraneous)))
        )
    selected_listing_ids = [selection.listing_id for selection in selection_set.selections]
    if len(set(selected_listing_ids)) != len(selected_listing_ids):
        raise ValueError("one provider listing cannot be selected for multiple instruments")

    preferred_epics: dict[InstrumentId, str] = {}
    promoted_instruments: list[Instrument] = []
    quarantined_instrument_ids: list[InstrumentId] = []
    for instrument in instruments:
        review = reviews_by_id[instrument.instrument_id]
        for candidate in review.candidates:
            _validate_eligible_candidate(candidate, instrument)
        selection = selections_by_id.get(instrument.instrument_id)
        if selection is None:
            if any(candidate.eligible for candidate in review.candidates):
                raise ValueError(
                    "listing selections is missing an instrument with eligible reviewed listings: "
                    f"{instrument.instrument_id}"
                )
            quarantined_instrument_ids.append(instrument.instrument_id)
            continue
        if selection.listing_id.provider != "ig" or selection.listing_id.environment != "demo":
            raise ValueError("capture promotion accepts only IG demo listing selections")
        matching = [
            candidate
            for candidate in review.candidates
            if candidate.listing_id == selection.listing_id
        ]
        if len(matching) != 1:
            raise ValueError(
                f"selected listing was not reviewed for {instrument.instrument_id}: "
                f"{selection.listing_id}"
            )
        if not matching[0].eligible:
            raise ValueError(
                f"selected listing is ineligible for {instrument.instrument_id}: "
                f"{selection.listing_id}"
            )
        promoted_instruments.append(instrument)
        preferred_epics[instrument.instrument_id] = selection.listing_id.external_id

    selection_hash = _selection_hash(selection_set)
    return UniversePromotion(
        release_name=release_name,
        source_catalogue_name=catalogue_name,
        source_catalogue_hash=catalogue_hash,
        source_review_hash=review_hash,
        selection_hash=selection_hash,
        promoted_at=promoted_at,
        instruments=tuple(promoted_instruments),
        quarantined_instrument_ids=tuple(quarantined_instrument_ids),
        preferred_epics=MappingProxyType(preferred_epics),
    )


def _validate_eligible_candidate(candidate: ListingReviewCandidate, instrument: Instrument) -> None:
    if candidate.listing_id.provider != "ig" or candidate.listing_id.environment != "demo":
        raise ValueError("listing review contains a non-IG-demo candidate")
    if not candidate.eligible:
        return
    expected_type = (
        ProductType.SPOT_FX if instrument.asset_class is AssetClass.FX else ProductType.ROLLING_CFD
    )
    if candidate.product_type is not expected_type:
        raise ValueError(
            f"eligible review candidate has wrong product type for {instrument.instrument_id}"
        )
    if candidate.expiry_kind is not ListingExpiryKind.ROLLING:
        raise ValueError(f"eligible review candidate is not rolling for {instrument.instrument_id}")
    if candidate.market_state is not ListingMarketState.TRADEABLE:
        raise ValueError(
            f"eligible review candidate is not tradeable for {instrument.instrument_id}"
        )
    if (
        candidate.currency is None
        or candidate.currency.upper() != instrument.quote_currency.upper()
    ):
        raise ValueError(
            f"eligible review candidate has wrong currency for {instrument.instrument_id}"
        )
    if candidate.minimum_deal_size is None or candidate.minimum_deal_size <= 0:
        raise ValueError(
            f"eligible review candidate has invalid size for {instrument.instrument_id}"
        )


def _require_exact_ids(
    *, expected: set[InstrumentId], actual: set[InstrumentId], evidence_name: str
) -> None:
    if missing := expected - actual:
        raise ValueError(
            f"{evidence_name} is missing instruments: " + ", ".join(sorted(map(str, missing)))
        )
    if extraneous := actual - expected:
        raise ValueError(
            f"{evidence_name} contains extraneous instruments: "
            + ", ".join(sorted(map(str, extraneous)))
        )


def _selection_hash(selection_set: ExplicitSelectionSet) -> str:
    payload = {
        "schema_version": 1,
        "catalogue_hash": selection_set.catalogue_hash,
        "review_hash": selection_set.review_hash,
        "selections": [
            {
                "instrument_id": str(selection.instrument_id),
                "listing_id": str(selection.listing_id),
            }
            for selection in sorted(
                selection_set.selections, key=lambda item: str(item.instrument_id)
            )
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lower-case SHA-256 value")
