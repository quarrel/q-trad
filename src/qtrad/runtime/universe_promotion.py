"""Strict file-boundary parsing for listing review and explicit selection evidence."""

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from qtrad.application.universe_promotion import (
    ExplicitListingSelection,
    ExplicitSelectionSet,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import AssetClass, Instrument, ProductType
from qtrad.domain.time import require_utc
from qtrad.ports.market_data import (
    InstrumentListingReview,
    ListingExpiryKind,
    ListingMarketState,
    ListingReviewCandidate,
    ListingReviewRejection,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _ReviewCandidateModel(_StrictModel):
    listing_id: str
    display_name: str
    product_type: Literal["SPOT_FX", "ROLLING_CFD", "UNKNOWN"]
    expiry_kind: Literal["ROLLING", "DATED", "UNKNOWN"]
    market_state: Literal["TRADEABLE", "UNAVAILABLE", "UNKNOWN"]
    currency: str | None
    minimum_deal_size: str | None
    economics: dict[str, str | None]
    metadata_version: str | None
    eligible: bool
    rejection_reasons: list[
        Literal[
            "WRONG_PRODUCT_TYPE",
            "NON_ROLLING_EXPIRY",
            "UNAVAILABLE_MARKET",
            "UNKNOWN_MARKET_STATE",
            "MISSING_CURRENCY",
            "WRONG_CURRENCY",
            "MISSING_MINIMUM_DEAL_SIZE",
            "INVALID_MINIMUM_DEAL_SIZE",
        ]
    ]


class _ReviewInstrumentModel(_StrictModel):
    instrument_id: str
    display_name: str
    expected_product_type: Literal["SPOT_FX", "ROLLING_CFD"]
    expected_currency: str
    status: Literal["OPERATOR_SELECTION_REQUIRED", "NO_ELIGIBLE_CANDIDATE"]
    eligible_candidate_count: int
    candidates: list[_ReviewCandidateModel]


class _ReviewManifestModel(_StrictModel):
    schema_version: Literal[1]
    provider: Literal["ig"]
    environment: Literal["demo"]
    catalogue_name: str
    catalogue_hash: str
    observed_at: str
    selection_authority: Literal[False]
    instruments: list[_ReviewInstrumentModel]
    review_hash: str


class _SelectionModel(_StrictModel):
    instrument_id: str
    listing_id: str


class _SelectionSetModel(_StrictModel):
    schema_version: Literal[1]
    catalogue_hash: str
    review_hash: str
    selection: list[_SelectionModel]


@dataclass(frozen=True, slots=True)
class ListingReviewEvidence:
    catalogue_name: str
    catalogue_hash: str
    review_hash: str
    observed_at: datetime
    reviews: tuple[InstrumentListingReview, ...]


def load_listing_review_evidence(
    path: Path, instruments: tuple[Instrument, ...]
) -> ListingReviewEvidence:
    """Parse, hash-check and bind a review manifest to the exact candidate catalogue."""

    model = _ReviewManifestModel.model_validate_json(path.read_text(encoding="utf-8"))
    payload = model.model_dump(mode="json", exclude={"review_hash"})
    actual_hash = _canonical_hash(payload)
    if actual_hash != model.review_hash:
        raise ValueError("listing review hash does not match its canonical content")

    expected_by_id = {instrument.instrument_id: instrument for instrument in instruments}
    if len(expected_by_id) != len(instruments):
        raise ValueError("candidate catalogue instruments must be unique")
    reviews: list[InstrumentListingReview] = []
    seen: set[InstrumentId] = set()
    for review_model in model.instruments:
        instrument_id = InstrumentId(review_model.instrument_id)
        if instrument_id in seen:
            raise ValueError(f"listing review repeats instrument {instrument_id}")
        seen.add(instrument_id)
        try:
            instrument = expected_by_id[instrument_id]
        except KeyError as error:
            raise ValueError(
                f"listing review contains instrument outside the catalogue: {instrument_id}"
            ) from error
        _validate_instrument_evidence(review_model, instrument)
        candidates = tuple(
            _candidate_from_model(candidate, instrument_id) for candidate in review_model.candidates
        )
        eligible_count = sum(candidate.eligible for candidate in candidates)
        if eligible_count != review_model.eligible_candidate_count:
            raise ValueError(f"listing review eligible count is invalid for {instrument_id}")
        expected_status = (
            "OPERATOR_SELECTION_REQUIRED" if eligible_count else "NO_ELIGIBLE_CANDIDATE"
        )
        if review_model.status != expected_status:
            raise ValueError(f"listing review status is invalid for {instrument_id}")
        reviews.append(InstrumentListingReview(instrument_id, candidates))
    if missing := set(expected_by_id) - seen:
        raise ValueError(
            "listing review is missing catalogue instruments: "
            + ", ".join(sorted(map(str, missing)))
        )

    observed_at = _utc_datetime(model.observed_at, "listing review observed_at")
    return ListingReviewEvidence(
        catalogue_name=model.catalogue_name,
        catalogue_hash=model.catalogue_hash,
        review_hash=model.review_hash,
        observed_at=observed_at,
        reviews=tuple(reviews),
    )


def load_explicit_selection_set(path: Path) -> ExplicitSelectionSet:
    """Parse an operator-authored exact selection set without inferring any listing."""

    model = _SelectionSetModel.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    selections = tuple(
        ExplicitListingSelection(
            instrument_id=InstrumentId(selection.instrument_id),
            listing_id=_listing_id(selection.listing_id),
        )
        for selection in model.selection
    )
    return ExplicitSelectionSet(
        catalogue_hash=model.catalogue_hash,
        review_hash=model.review_hash,
        selections=selections,
    )


def _validate_instrument_evidence(review: _ReviewInstrumentModel, instrument: Instrument) -> None:
    expected_product_type = (
        ProductType.SPOT_FX if instrument.asset_class is AssetClass.FX else ProductType.ROLLING_CFD
    )
    if review.display_name != instrument.display_name:
        raise ValueError(f"listing review display name is invalid for {instrument.instrument_id}")
    if review.expected_product_type != expected_product_type.value:
        raise ValueError(f"listing review product type is invalid for {instrument.instrument_id}")
    if review.expected_currency != instrument.quote_currency:
        raise ValueError(f"listing review currency is invalid for {instrument.instrument_id}")


def _candidate_from_model(
    model: _ReviewCandidateModel, instrument_id: InstrumentId
) -> ListingReviewCandidate:
    minimum_deal_size: Decimal | None = None
    if model.minimum_deal_size is not None:
        try:
            minimum_deal_size = Decimal(model.minimum_deal_size)
        except InvalidOperation as error:
            raise ValueError(
                f"listing review has invalid minimum size for {instrument_id}: {model.listing_id}"
            ) from error
        if not minimum_deal_size.is_finite():
            raise ValueError(
                f"listing review has non-finite minimum size for {instrument_id}: "
                f"{model.listing_id}"
            )
    rejection_reasons = tuple(ListingReviewRejection(value) for value in model.rejection_reasons)
    if model.eligible != (not rejection_reasons):
        raise ValueError(
            f"listing review eligibility contradicts rejection reasons for {model.listing_id}"
        )
    return ListingReviewCandidate(
        instrument_id=instrument_id,
        listing_id=_listing_id(model.listing_id),
        display_name=model.display_name,
        product_type=ProductType(model.product_type),
        expiry_kind=ListingExpiryKind(model.expiry_kind),
        market_state=ListingMarketState(model.market_state),
        currency=model.currency,
        minimum_deal_size=minimum_deal_size,
        economics=model.economics,
        metadata_version=model.metadata_version,
        rejection_reasons=rejection_reasons,
    )


def _listing_id(value: str) -> ProviderListingId:
    parts = value.split(":", maxsplit=2)
    if len(parts) != 3:
        raise ValueError("provider listing ID must be '<provider>:<environment>:<external-id>'")
    return ProviderListingId(*parts)


def _utc_datetime(value: str, name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require_utc(parsed, name)
    return parsed


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
