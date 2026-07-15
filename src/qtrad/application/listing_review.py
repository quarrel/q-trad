"""Deterministic, non-authoritative provider-listing review manifests."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.instruments import AssetClass, Instrument, ProductType
from qtrad.domain.time import require_utc
from qtrad.ports.market_data import InstrumentListingReview, ListingReviewCandidate


@dataclass(frozen=True, slots=True)
class ListingReviewManifest:
    """A hash-addressed review artefact that cannot authorise capture."""

    catalogue_name: str
    catalogue_hash: str
    observed_at: datetime
    review_hash: str
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        require_utc(self.observed_at, "listing review observed_at")
        if not self.catalogue_name or len(self.catalogue_hash) != 64:
            raise ValueError("listing review requires a named, hashed catalogue")
        if len(self.review_hash) != 64:
            raise ValueError("listing review hash must be SHA-256")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.payload, "review_hash": self.review_hash}


def build_listing_review_manifest(
    *,
    catalogue_name: str,
    catalogue_hash: str,
    instruments: Sequence[Instrument],
    reviews: Sequence[InstrumentListingReview],
    observed_at: datetime,
) -> ListingReviewManifest:
    """Build a complete manifest and reject missing, duplicate or extraneous review results."""

    require_utc(observed_at, "listing review observed_at")
    if not instruments or len(instruments) > 100:
        raise ValueError("listing review requires between one and 100 instruments")
    instruments_by_id = {instrument.instrument_id: instrument for instrument in instruments}
    if len(instruments_by_id) != len(instruments):
        raise ValueError("listing review instruments must be unique")
    reviews_by_id = {review.instrument_id: review for review in reviews}
    if len(reviews_by_id) != len(reviews):
        raise ValueError("listing review results must be unique per instrument")
    expected = set(instruments_by_id)
    actual = set(reviews_by_id)
    if missing := expected - actual:
        raise ValueError(
            "listing review is missing instruments: " + ", ".join(sorted(map(str, missing)))
        )
    if extraneous := actual - expected:
        raise ValueError(
            "listing review contains extraneous instruments: "
            + ", ".join(sorted(map(str, extraneous)))
        )

    instrument_payloads: list[JsonValue] = []
    for instrument in instruments:
        review = reviews_by_id[instrument.instrument_id]
        candidates = tuple(sorted(review.candidates, key=lambda item: item.listing_id.external_id))
        for candidate in candidates:
            _validate_candidate(candidate, instrument)
        eligible_count = sum(candidate.eligible for candidate in candidates)
        instrument_payloads.append(
            {
                "instrument_id": str(instrument.instrument_id),
                "display_name": instrument.display_name,
                "expected_product_type": _expected_product_type(instrument).value,
                "expected_currency": instrument.quote_currency,
                "status": (
                    "OPERATOR_SELECTION_REQUIRED" if eligible_count else "NO_ELIGIBLE_CANDIDATE"
                ),
                "eligible_candidate_count": eligible_count,
                "candidates": [_candidate_payload(candidate) for candidate in candidates],
            }
        )

    base_payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "provider": "ig",
        "environment": "demo",
        "catalogue_name": catalogue_name,
        "catalogue_hash": catalogue_hash,
        "observed_at": to_json_value(observed_at),
        "selection_authority": False,
        "instruments": instrument_payloads,
    }
    review_hash = _payload_hash(base_payload)
    return ListingReviewManifest(
        catalogue_name=catalogue_name,
        catalogue_hash=catalogue_hash,
        observed_at=observed_at,
        review_hash=review_hash,
        payload=base_payload,
    )


def _candidate_payload(candidate: ListingReviewCandidate) -> dict[str, JsonValue]:
    return {
        "listing_id": str(candidate.listing_id),
        "display_name": candidate.display_name,
        "product_type": candidate.product_type.value,
        "expiry_kind": candidate.expiry_kind.value,
        "market_state": candidate.market_state.value,
        "currency": candidate.currency,
        "minimum_deal_size": to_json_value(candidate.minimum_deal_size),
        "economics": dict(candidate.economics),
        "metadata_version": candidate.metadata_version,
        "eligible": candidate.eligible,
        "rejection_reasons": [reason.value for reason in candidate.rejection_reasons],
    }


def _expected_product_type(instrument: Instrument) -> ProductType:
    return (
        ProductType.SPOT_FX if instrument.asset_class is AssetClass.FX else ProductType.ROLLING_CFD
    )


def _validate_candidate(candidate: ListingReviewCandidate, instrument: Instrument) -> None:
    if candidate.listing_id.provider != "ig" or candidate.listing_id.environment != "demo":
        raise ValueError("listing review manifest accepts only IG demo candidates")
    if not candidate.eligible:
        return
    if candidate.product_type is not _expected_product_type(instrument):
        raise ValueError(
            f"eligible listing review has the wrong product type for {instrument.instrument_id}"
        )
    if (
        candidate.currency is None
        or candidate.currency.upper() != instrument.quote_currency.upper()
    ):
        raise ValueError(
            f"eligible listing review has the wrong currency for {instrument.instrument_id}"
        )


def _payload_hash(payload: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
