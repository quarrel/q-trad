import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from qtrad.application.listing_review import build_listing_review_manifest
from qtrad.application.universe_promotion import (
    ExplicitListingSelection,
    ExplicitSelectionSet,
    promote_reviewed_universe,
)
from qtrad.domain.identifiers import ProviderListingId
from qtrad.domain.instruments import AssetClass, Instrument, ProductType
from qtrad.ports.market_data import (
    InstrumentListingReview,
    ListingExpiryKind,
    ListingMarketState,
    ListingReviewCandidate,
    ListingReviewRejection,
)
from qtrad.runtime.universe import (
    CaptureCandidates,
    load_capture_candidates,
    load_capture_universe,
    render_capture_universe_promotion,
)
from qtrad.runtime.universe_promotion import (
    ListingReviewEvidence,
    load_explicit_selection_set,
    load_listing_review_evidence,
)

FIXED_TIME = datetime(2026, 7, 18, 5, 6, 7, tzinfo=UTC)


def _epic(instrument: Instrument, suffix: str) -> str:
    stem = str(instrument.instrument_id).replace(":", ".").replace("-", ".").upper()
    return f"FIXTURE.{stem}.{suffix}.IP"


def _candidate(
    instrument: Instrument,
    *,
    suffix: str = "STANDARD",
    eligible: bool = True,
) -> ListingReviewCandidate:
    expected_product = (
        ProductType.SPOT_FX if instrument.asset_class is AssetClass.FX else ProductType.ROLLING_CFD
    )
    return ListingReviewCandidate(
        instrument_id=instrument.instrument_id,
        listing_id=ProviderListingId("ig", "demo", _epic(instrument, suffix)),
        display_name=f"{instrument.display_name} {suffix.title()}",
        product_type=expected_product,
        expiry_kind=ListingExpiryKind.ROLLING,
        market_state=ListingMarketState.TRADEABLE,
        currency=instrument.quote_currency if eligible else "ZZZ",
        minimum_deal_size=Decimal("0.5"),
        economics={
            "quantity_unit": "CONTRACTS",
            "contract_size": "1",
            "lot_size": None,
            "one_pip_means": None,
            "value_of_one_pip": None,
            "minimum_quantity": "0.5",
            "price_increment": None,
        },
        metadata_version=f"fixture-{suffix.lower()}",
        rejection_reasons=() if eligible else (ListingReviewRejection.WRONG_CURRENCY,),
    )


def _reviews(catalogue: CaptureCandidates) -> tuple[InstrumentListingReview, ...]:
    reviews: list[InstrumentListingReview] = []
    for index, instrument in enumerate(catalogue.instruments):
        candidates = [_candidate(instrument)]
        if index == 0:
            candidates.append(_candidate(instrument, suffix="WRONG", eligible=False))
        reviews.append(InstrumentListingReview(instrument.instrument_id, tuple(candidates)))
    return tuple(reviews)


def _manifest(catalogue: CaptureCandidates):
    return build_listing_review_manifest(
        catalogue_name=catalogue.name,
        catalogue_hash=catalogue.configuration_hash,
        instruments=catalogue.instruments,
        reviews=_reviews(catalogue),
        observed_at=FIXED_TIME,
    )


def _selections(evidence: ListingReviewEvidence) -> ExplicitSelectionSet:
    return ExplicitSelectionSet(
        catalogue_hash=evidence.catalogue_hash,
        review_hash=evidence.review_hash,
        selections=tuple(
            ExplicitListingSelection(
                review.instrument_id,
                next(candidate.listing_id for candidate in review.candidates if candidate.eligible),
            )
            for review in evidence.reviews
        ),
    )


def _promote(
    catalogue: CaptureCandidates,
    evidence: ListingReviewEvidence,
    selections: ExplicitSelectionSet,
):
    return promote_reviewed_universe(
        release_name="capture-v2",
        catalogue_name=catalogue.name,
        catalogue_hash=catalogue.configuration_hash,
        instruments=catalogue.instruments,
        review_catalogue_name=evidence.catalogue_name,
        review_catalogue_hash=evidence.catalogue_hash,
        review_hash=evidence.review_hash,
        reviews=evidence.reviews,
        selection_set=selections,
        promoted_at=FIXED_TIME,
    )


def _write_review(path: Path, catalogue: CaptureCandidates) -> None:
    path.write_text(json.dumps(_manifest(catalogue).as_json_value(), sort_keys=True))


def _write_selections(path: Path, selection_set: ExplicitSelectionSet) -> None:
    lines = [
        "schema_version = 1",
        f'catalogue_hash = "{selection_set.catalogue_hash}"',
        f'review_hash = "{selection_set.review_hash}"',
    ]
    for selection in selection_set.selections:
        lines.extend(
            [
                "",
                "[[selection]]",
                f'instrument_id = "{selection.instrument_id}"',
                f'listing_id = "{selection.listing_id}"',
            ]
        )
    path.write_text("\n".join(lines) + "\n")


def test_exact_review_and_selections_render_a_deterministic_undeployed_universe(
    tmp_path: Path,
) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    review_path = tmp_path / "review.json"
    _write_review(review_path, catalogue)
    evidence = load_listing_review_evidence(review_path, catalogue.instruments)
    selections_path = tmp_path / "selections.toml"
    _write_selections(selections_path, _selections(evidence))
    selection_set = load_explicit_selection_set(selections_path)

    first = _promote(catalogue, evidence, selection_set)
    reordered = replace(selection_set, selections=tuple(reversed(selection_set.selections)))
    second = _promote(catalogue, evidence, reordered)
    first_toml, first_universe = render_capture_universe_promotion(first)
    second_toml, second_universe = render_capture_universe_promotion(second)

    assert first.selection_hash == second.selection_hash
    assert first_toml == second_toml
    assert first_universe.configuration_hash == second_universe.configuration_hash
    assert first_universe.name == "capture-v2"
    assert len(first_universe.instruments) == 20
    assert len(first_universe.preferred_epics) == 20
    assert "source_review_hash" in first_toml
    assert "selection_hash" in first_toml
    output = tmp_path / "capture-v2.toml"
    output.write_text(first_toml)
    loaded = load_capture_universe(output)
    assert loaded.configuration_hash == first_universe.configuration_hash


def test_review_loader_rejects_tampered_manifest_content(tmp_path: Path) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    path = tmp_path / "tampered.json"
    payload = _manifest(catalogue).as_json_value()
    payload["catalogue_name"] = "tampered"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="hash does not match"):
        load_listing_review_evidence(path, catalogue.instruments)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("catalogue_hash", "stale catalogue hash"),
        ("review_hash", "stale review hash"),
    ],
)
def test_promotion_rejects_stale_selection_hashes(tmp_path: Path, field: str, message: str) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    path = tmp_path / "review.json"
    _write_review(path, catalogue)
    evidence = load_listing_review_evidence(path, catalogue.instruments)
    selection_set = _selections(evidence)
    stale = replace(selection_set, **{field: "0" * 64})

    with pytest.raises(ValueError, match=message):
        _promote(catalogue, evidence, stale)


def test_promotion_rejects_omitted_and_duplicate_instrument_selections(tmp_path: Path) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    path = tmp_path / "review.json"
    _write_review(path, catalogue)
    evidence = load_listing_review_evidence(path, catalogue.instruments)
    selection_set = _selections(evidence)

    omitted = replace(selection_set, selections=selection_set.selections[:-1])
    with pytest.raises(ValueError, match="missing an instrument with eligible reviewed listings"):
        _promote(catalogue, evidence, omitted)

    duplicate = replace(
        selection_set,
        selections=(*selection_set.selections, selection_set.selections[0]),
    )
    with pytest.raises(ValueError, match="unique per instrument"):
        _promote(catalogue, evidence, duplicate)


def test_promotion_quarantines_an_omitted_instrument_without_eligible_listings(
    tmp_path: Path,
) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    path = tmp_path / "review.json"
    _write_review(path, catalogue)
    evidence = load_listing_review_evidence(path, catalogue.instruments)
    last_review = evidence.reviews[-1]
    quarantined_review = replace(
        last_review,
        candidates=tuple(
            replace(
                candidate,
                currency="ZZZ",
                rejection_reasons=(ListingReviewRejection.WRONG_CURRENCY,),
            )
            for candidate in last_review.candidates
        ),
    )
    selections = _selections(evidence)
    evidence = replace(evidence, reviews=(*evidence.reviews[:-1], quarantined_review))
    selections = replace(selections, selections=selections.selections[:-1])

    promotion = _promote(catalogue, evidence, selections)
    rendered, universe = render_capture_universe_promotion(promotion)

    assert promotion.quarantined_instrument_ids == (last_review.instrument_id,)
    assert len(universe.instruments) == len(catalogue.instruments) - 1
    assert f'quarantined_instrument_ids = ["{last_review.instrument_id}"]' in rendered


def test_promotion_rejects_ineligible_unseen_and_reused_listings(tmp_path: Path) -> None:
    catalogue = load_capture_candidates(Path("config/capture-v2-candidates.toml"))
    path = tmp_path / "review.json"
    _write_review(path, catalogue)
    evidence = load_listing_review_evidence(path, catalogue.instruments)
    selection_set = _selections(evidence)
    first_review = evidence.reviews[0]
    ineligible_id = next(
        candidate.listing_id for candidate in first_review.candidates if not candidate.eligible
    )
    ineligible = replace(
        selection_set,
        selections=(
            replace(selection_set.selections[0], listing_id=ineligible_id),
            *selection_set.selections[1:],
        ),
    )
    with pytest.raises(ValueError, match="is ineligible"):
        _promote(catalogue, evidence, ineligible)

    unseen = replace(
        selection_set,
        selections=(
            replace(
                selection_set.selections[0],
                listing_id=ProviderListingId("ig", "demo", "UNSEEN"),
            ),
            *selection_set.selections[1:],
        ),
    )
    with pytest.raises(ValueError, match="was not reviewed"):
        _promote(catalogue, evidence, unseen)

    reused = replace(
        selection_set,
        selections=(
            selection_set.selections[0],
            replace(
                selection_set.selections[1],
                listing_id=selection_set.selections[0].listing_id,
            ),
            *selection_set.selections[2:],
        ),
    )
    with pytest.raises(ValueError, match="cannot be selected for multiple"):
        _promote(catalogue, evidence, reused)
