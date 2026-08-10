from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_qualification import (
    IbkrQualificationStage,
    IbkrQualifiedContract,
    VerifiedB3Qualification,
    has_verified_ibkr_capture_qualification_provenance,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import INSTRUMENTS_BY_ID, AssetClass, ProductType, ProviderListing
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime import ibkr_b4
from qtrad.runtime.ibkr_b4 import (
    B4_INSTRUMENTS,
    B4_RELEASE_CONTRACT,
    B4_RELEASE_STAGE,
    IbkrB4DeploymentDescriptor,
    b4_qualification_expectation,
    load_authenticated_b4_configuration,
    promote_b4_configuration,
    verify_b4_configuration,
    write_b4_release,
)
from qtrad.runtime.ibkr_native_capture import IbkrNativeCaptureConfiguration
from qtrad.runtime.ibkr_release import (
    IbkrAuthorityPaths,
    configuration_payload,
    sha256_path,
    write_release,
)

_NOW = datetime(2026, 8, 10, 1, tzinfo=UTC)
_SPECS = (
    ("fx:aud-usd", "aud-usd", "AUD/USD", AssetClass.FX, "AUD", "USD", "CASH", 101),
    ("fx:eur-usd", "eur-usd", "EUR/USD", AssetClass.FX, "EUR", "USD", "CASH", 102),
    (
        "index:australia-200",
        "australia-200",
        "Australia 200",
        AssetClass.INDEX,
        None,
        "AUD",
        "CFD",
        103,
    ),
    ("index:us-500", "us-500", "US 500", AssetClass.INDEX, None, "USD", "CFD", 104),
    (
        "commodity:spot-gold",
        "spot-gold",
        "Gold",
        AssetClass.COMMODITY,
        "XAU",
        "USD",
        "CFD",
        105,
    ),
    (
        "commodity:us-crude",
        "us-crude",
        "US Crude",
        AssetClass.COMMODITY,
        None,
        "USD",
        "CFD",
        106,
    ),
)


def _evidence(con_id: int, security_type: str, currency: str) -> IbkrContractEvidence:
    return IbkrContractEvidence(
        con_id=con_id,
        symbol=f"S{con_id}",
        local_symbol=f"L{con_id}",
        security_type=security_type,
        exchange="IDEALPRO" if security_type == "CASH" else "SMART",
        currency=currency,
        trading_class=f"T{con_id}",
        multiplier=None,
        minimum_tick=Decimal("0.00005") if security_type == "CASH" else Decimal("0.1"),
        market_rule_ids=("1",),
        valid_exchanges=("SMART",),
        long_name=f"Contract {con_id}",
        underlier_con_id=None,
        timezone="UTC",
        trading_hours="20260810:0000-20260810:2359",
        liquid_hours="20260810:0000-20260810:2359",
        primary_exchange=None,
        contract_month=None,
    )


def _source() -> IbkrNativeCaptureConfiguration:
    listings: list[ProviderListing] = []
    evidence: dict[ProviderListingId, IbkrContractEvidence] = {}
    for (
        instrument,
        external,
        display,
        _asset,
        _base,
        currency,
        security_type,
        con_id,
    ) in _SPECS:
        listing_id = ProviderListingId("ibkr", "IBKR_PAPER", external)
        listing = ProviderListing(
            listing_id=listing_id,
            instrument_id=InstrumentId(instrument),
            display_name=display,
            product_type=(
                ProductType.SPOT_FX if security_type == "CASH" else ProductType.ROLLING_CFD
            ),
            currency=currency,
            minimum_deal_size=Decimal("1"),
            price_increment=Decimal("0.00005") if security_type == "CASH" else Decimal("0.1"),
            valid_from=datetime(
                2026,
                8,
                8 if instrument in {"fx:aud-usd", "index:australia-200"} else 10,
                tzinfo=UTC,
            ),
            valid_to=None,
            metadata_version=(
                "ibkr-b3-review-v1"
                if instrument in {"fx:aud-usd", "index:australia-200"}
                else "ibkr-b4-review-v1"
            ),
            economics={},
        )
        listings.append(listing)
        evidence[listing_id] = _evidence(con_id, security_type, currency)
    return IbkrNativeCaptureConfiguration.from_reviewed(listings, evidence)


def _parent(source: IbkrNativeCaptureConfiguration) -> IbkrNativeCaptureConfiguration:
    ids = {InstrumentId("fx:aud-usd"), InstrumentId("index:australia-200")}
    listings = [item for item in source.listings if item.instrument_id in ids]
    evidence = {item.listing_id: source.contract_evidence[item.listing_id] for item in listings}
    return IbkrNativeCaptureConfiguration.from_reviewed(listings, evidence)


def _paths(tmp_path: Path, prefix: str) -> IbkrAuthorityPaths:
    values: list[Path] = []
    for name in ("capability", "operator", "selection", "catalogue", "probe"):
        path = tmp_path / f"{prefix}-{name}"
        path.write_text(f"{prefix}-{name}\n", encoding="utf-8")
        values.append(path)
    return IbkrAuthorityPaths(*values)


def _review(source: IbkrNativeCaptureConfiguration) -> dict[str, object]:
    instruments: list[object] = []
    for listing in source.listings:
        contract = source.contract_evidence[listing.listing_id]
        instruments.append(
            {
                "instrument_id": str(listing.instrument_id),
                "queries": [
                    {
                        "contracts": [
                            {
                                "con_id": contract.con_id,
                                "symbol": contract.symbol,
                                "local_symbol": contract.local_symbol,
                                "security_type": contract.security_type,
                                "exchange": contract.exchange,
                                "currency": contract.currency,
                                "trading_class": contract.trading_class,
                                "multiplier": contract.multiplier,
                                "minimum_tick": str(contract.minimum_tick),
                                "market_rule_ids": list(contract.market_rule_ids),
                                "valid_exchanges": list(contract.valid_exchanges),
                                "long_name": contract.long_name,
                                "underlier_con_id": contract.underlier_con_id,
                                "timezone": contract.timezone,
                                "trading_hours": contract.trading_hours,
                                "liquid_hours": contract.liquid_hours,
                                "primary_exchange": contract.primary_exchange,
                                "contract_month": contract.contract_month,
                            }
                        ]
                    }
                ],
            }
        )
    return {"instruments": instruments}


def _install_authority(
    monkeypatch: pytest.MonkeyPatch,
    source: IbkrNativeCaptureConfiguration,
    *,
    duplicate_con_id: bool = False,
) -> None:
    catalogue_hash = "a" * 64
    decisions = []
    for index, listing in enumerate(source.listings):
        contract = source.contract_evidence[listing.listing_id]
        con_id = 101 if duplicate_con_id and index == 1 else contract.con_id
        decisions.append(
            SimpleNamespace(
                instrument_id=listing.instrument_id,
                acquisition_eligible=True,
                fingerprint=SimpleNamespace(con_id=con_id),
            )
        )
    candidates = []
    by_id = {item[0]: item for item in _SPECS}
    for instrument in B4_INSTRUMENTS:
        spec = by_id[str(instrument)]
        candidates.append(
            SimpleNamespace(
                instrument_id=instrument,
                display_name=spec[2],
                asset_class=spec[3],
                base_currency=spec[4],
                quote_currency=spec[5],
            )
        )
    monkeypatch.setattr(
        ibkr_b4,
        "verify_ibkr_contract_selection",
        lambda *_args, **_kwargs: SimpleNamespace(
            catalogue_hash=catalogue_hash, decisions=decisions
        ),
    )
    monkeypatch.setattr(
        ibkr_b4, "load_ibkr_capability_review", lambda *_args, **_kwargs: _review(source)
    )
    monkeypatch.setattr(
        ibkr_b4,
        "load_capture_candidates",
        lambda _path: SimpleNamespace(configuration_hash=catalogue_hash, instruments=candidates),
    )


def _qualification_values(
    parent: IbkrNativeCaptureConfiguration, parent_path: Path
) -> dict[str, object]:
    return {
        "stage": IbkrQualificationStage.B3_EXACT_TWO,
        "artifact_sha256": "9" * 64,
        "release_contract": "qtrad-ibkr-native-release-v1",
        "release_sha256": sha256_path(parent_path, label="test parent"),
        "configuration_hash": parent.configuration_hash,
        "capture_source_id": "ibkr-paper-v1",
        "universe_id": "capture-ibkr-v1",
        "instruments": frozenset((InstrumentId("fx:aud-usd"), InstrumentId("index:australia-200"))),
        "contracts": tuple(
            IbkrQualifiedContract(
                instrument_id=listing.instrument_id,
                listing_id=listing.listing_id,
                con_id=parent.contract_evidence[listing.listing_id].con_id,
            )
            for listing in parent.listings
        ),
        "qualified_at": _NOW,
    }


def _qualification(
    parent: IbkrNativeCaptureConfiguration, parent_path: Path
) -> VerifiedB3Qualification:
    capability = object.__new__(VerifiedB3Qualification)
    for name, value in _qualification_values(parent, parent_path).items():
        object.__setattr__(capability, f"_{name}", value)
    return capability


def _replace_qualification(
    qualification: VerifiedB3Qualification, **changes: object
) -> VerifiedB3Qualification:
    values = {
        "stage": qualification.stage,
        "artifact_sha256": qualification.artifact_sha256,
        "release_contract": qualification.release_contract,
        "release_sha256": qualification.release_sha256,
        "configuration_hash": qualification.configuration_hash,
        "capture_source_id": qualification.capture_source_id,
        "universe_id": qualification.universe_id,
        "instruments": qualification.instruments,
        "contracts": qualification.contracts,
        "qualified_at": qualification.qualified_at,
    }
    values.update(changes)
    replacement = object.__new__(VerifiedB3Qualification)
    for name, value in values.items():
        object.__setattr__(replacement, f"_{name}", value)
    return replacement


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    IbkrNativeCaptureConfiguration,
    IbkrNativeCaptureConfiguration,
    Path,
    IbkrAuthorityPaths,
    IbkrAuthorityPaths,
    VerifiedB3Qualification,
]:
    source = _source()
    parent = _parent(source)
    parent_path = tmp_path / "b3.json"
    parent_path.write_text("authenticated-parent\n", encoding="utf-8")
    b4_paths = _paths(tmp_path, "b4")
    b3_paths = _paths(tmp_path, "b3")
    qualification = _qualification(parent, parent_path)
    monkeypatch.setattr(
        ibkr_b4, "load_authenticated_b3_configuration", lambda *_args, **_kwargs: parent
    )
    monkeypatch.setattr(
        ibkr_b4,
        "has_verified_ibkr_capture_qualification_provenance",
        lambda value: type(value) is VerifiedB3Qualification,
    )
    _install_authority(monkeypatch, source)
    return source, parent, parent_path, b4_paths, b3_paths, qualification


def test_b4_exact_six_are_registered_canonical_instruments() -> None:
    for instrument_id, _external, display, asset, base, currency, _security_type, _con_id in _SPECS:
        instrument = INSTRUMENTS_BY_ID[InstrumentId(instrument_id)]
        assert (
            instrument.display_name,
            instrument.asset_class,
            instrument.base_currency,
            instrument.quote_currency,
        ) == (display, asset, base, currency)


def test_b4_promotes_exact_six_only_from_verified_b3_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )

    promotion = promote_b4_configuration(
        source,
        authority_paths=b4_paths,
        parent_release_path=parent_path,
        parent_authority_paths=b3_paths,
        qualification=qualification,
    )

    assert {item.instrument_id for item in promotion.configuration.listings} == B4_INSTRUMENTS
    assert promotion.parent_release.qualification_artifact_sha256 == "9" * 64
    assert promotion.parent_release.artifact_sha256 == qualification.release_sha256
    inherited = {
        (
            listing.instrument_id,
            listing.listing_id,
            promotion.configuration.contract_evidence[listing.listing_id].con_id,
        )
        for listing in promotion.configuration.listings
        if listing.instrument_id in qualification.instruments
    }
    assert inherited == {
        (item.instrument_id, item.listing_id, item.con_id) for item in qualification.contracts
    }


def test_b4_promotion_rejects_matching_duck_typed_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, parent, parent_path, b4_paths, b3_paths, _qualification_fixture = _setup(
        tmp_path, monkeypatch
    )
    forged = cast(
        VerifiedB3Qualification,
        SimpleNamespace(**_qualification_values(parent, parent_path)),
    )
    monkeypatch.setattr(
        ibkr_b4,
        "has_verified_ibkr_capture_qualification_provenance",
        has_verified_ibkr_capture_qualification_provenance,
    )

    with pytest.raises(ValueError, match="verifier-minted"):
        promote_b4_configuration(
            source,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=forged,
        )


def test_b4_release_loading_rejects_object_new_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent, parent_path, b4_paths, b3_paths, qualification = _setup(tmp_path, monkeypatch)
    promotion = promote_b4_configuration(
        source,
        authority_paths=b4_paths,
        parent_release_path=parent_path,
        parent_authority_paths=b3_paths,
        qualification=qualification,
    )
    release_path = tmp_path / "b4-release.json"
    write_b4_release(release_path, promotion)
    monkeypatch.setattr(
        ibkr_b4,
        "has_verified_ibkr_capture_qualification_provenance",
        has_verified_ibkr_capture_qualification_provenance,
    )

    with pytest.raises(ValueError, match="verifier-minted"):
        load_authenticated_b4_configuration(
            release_path,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_rejects_changed_con_id_for_b3_inherited_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent, parent_path, b4_paths, b3_paths, qualification = _setup(tmp_path, monkeypatch)
    aud = next(item for item in source.listings if str(item.instrument_id) == "fx:aud-usd")
    evidence = dict(source.contract_evidence)
    evidence[aud.listing_id] = replace(evidence[aud.listing_id], con_id=901)
    changed_source = IbkrNativeCaptureConfiguration.from_reviewed(source.listings, evidence)
    _install_authority(monkeypatch, changed_source)

    with pytest.raises(ValueError, match="does not preserve B3-qualified conId"):
        promote_b4_configuration(
            changed_source,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_rejects_mismatched_b3_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    bad = _replace_qualification(qualification, configuration_hash="8" * 64)

    with pytest.raises(ValueError, match="matching independently verified"):
        promote_b4_configuration(
            source,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=bad,
        )


def test_b4_rejects_qualification_contract_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    first = qualification.contracts[0]
    bad = _replace_qualification(
        qualification,
        contracts=(replace(first, con_id=first.con_id + 1), *qualification.contracts[1:]),
    )

    with pytest.raises(ValueError, match="matching independently verified"):
        promote_b4_configuration(
            source,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=bad,
        )


def test_b4_rejects_wrong_cardinality_before_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    reduced_listings = list(source.listings[:-1])
    reduced = IbkrNativeCaptureConfiguration.from_reviewed(
        reduced_listings,
        {item.listing_id: source.contract_evidence[item.listing_id] for item in reduced_listings},
    )

    with pytest.raises(ValueError, match="exactly the fixed six"):
        promote_b4_configuration(
            reduced,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_rejects_duplicate_authenticated_con_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    _install_authority(monkeypatch, source, duplicate_con_id=True)

    with pytest.raises(ValueError, match="duplicate conIds"):
        promote_b4_configuration(
            source,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_rejects_unsupported_authenticated_product_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    gold = next(
        item for item in source.listings if str(item.instrument_id) == "commodity:spot-gold"
    )
    evidence = dict(source.contract_evidence)
    evidence[gold.listing_id] = replace(evidence[gold.listing_id], security_type="CMDTY")
    altered = IbkrNativeCaptureConfiguration.from_reviewed(source.listings, evidence)
    _install_authority(monkeypatch, altered)

    with pytest.raises(ValueError, match="no reviewed listing mapping"):
        promote_b4_configuration(
            altered,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_rejects_every_mutable_listing_semantic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    first = source.listings[0]
    altered_listing = replace(first, display_name="tampered")
    listings = [altered_listing, *source.listings[1:]]
    altered = IbkrNativeCaptureConfiguration.from_reviewed(listings, source.contract_evidence)

    with pytest.raises(ValueError, match="display_name"):
        promote_b4_configuration(
            altered,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_release_round_trip_replays_parent_qualification_and_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    promotion = promote_b4_configuration(
        source,
        authority_paths=b4_paths,
        parent_release_path=parent_path,
        parent_authority_paths=b3_paths,
        qualification=qualification,
    )
    release_path = tmp_path / "b4-release.json"
    write_b4_release(release_path, promotion)

    loaded = load_authenticated_b4_configuration(
        release_path,
        authority_paths=b4_paths,
        parent_release_path=parent_path,
        parent_authority_paths=b3_paths,
        qualification=qualification,
    )
    descriptor = cast(
        IbkrB4DeploymentDescriptor,
        SimpleNamespace(
            deployment=SimpleNamespace(
                application_commit="1" * 40,
                image="registry/qtrad-ibkr@sha256:" + "2" * 64,
                api_package_fingerprint="3" * 64,
                gateway_archive_sha256="4" * 64,
                gateway_version="10.49",
                ibc_version="3.24.1",
                database_name="qtrad_ibkr",
                schema_head="0014",
            ),
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
        ),
    )
    qualified_configuration, expectation = b4_qualification_expectation(
        release_path=release_path,
        authority_paths=b4_paths,
        descriptor=descriptor,
        parent_qualification=qualification,
    )
    assert qualified_configuration == loaded
    assert expectation.stage is IbkrQualificationStage.B4_EXACT_SIX
    assert expectation.release_contract == B4_RELEASE_CONTRACT
    assert expectation.instruments == B4_INSTRUMENTS
    assert len(expectation.contracts) == 6

    report = verify_b4_configuration(loaded, observed_at=_NOW)

    document = cast(dict[str, JsonValue], json.loads(release_path.read_text()))
    assert document["contract"] == B4_RELEASE_CONTRACT
    assert document["release_stage"] == B4_RELEASE_STAGE
    assert report["valid"] is True
    assert report["instrument_count"] == 6


def test_b4_release_rejects_any_changed_authority_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    promotion = promote_b4_configuration(
        source,
        authority_paths=b4_paths,
        parent_release_path=parent_path,
        parent_authority_paths=b3_paths,
        qualification=qualification,
    )
    release_path = tmp_path / "b4-release.json"
    write_b4_release(release_path, promotion)
    b4_paths.probe_spec_path.write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="authority identity mismatch"):
        load_authenticated_b4_configuration(
            release_path,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_release_rejects_rehashed_provider_evidence_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    promotion = promote_b4_configuration(
        source,
        authority_paths=b4_paths,
        parent_release_path=parent_path,
        parent_authority_paths=b3_paths,
        qualification=qualification,
    )
    listing = promotion.configuration.listings[0]
    evidence = dict(promotion.configuration.contract_evidence)
    evidence[listing.listing_id] = replace(evidence[listing.listing_id], exchange="TAMPERED")
    altered = IbkrNativeCaptureConfiguration.from_reviewed(
        promotion.configuration.listings, evidence
    )
    release_path = tmp_path / "tampered.json"
    write_release(
        release_path,
        configuration_payload(
            altered,
            promotion.authority,
            contract=B4_RELEASE_CONTRACT,
            release_stage=B4_RELEASE_STAGE,
            parent_release=promotion.parent_release.as_json_value(),
        ),
    )

    with pytest.raises(ValueError, match="does not match authenticated review"):
        load_authenticated_b4_configuration(
            release_path,
            authority_paths=b4_paths,
            parent_release_path=parent_path,
            parent_authority_paths=b3_paths,
            qualification=qualification,
        )


def test_b4_publication_is_create_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _parent_config, parent_path, b4_paths, b3_paths, qualification = _setup(
        tmp_path, monkeypatch
    )
    promotion = promote_b4_configuration(
        source,
        authority_paths=b4_paths,
        parent_release_path=parent_path,
        parent_authority_paths=b3_paths,
        qualification=qualification,
    )
    release_path = tmp_path / "b4-release.json"
    write_b4_release(release_path, promotion)

    with pytest.raises(FileExistsError):
        write_b4_release(release_path, promotion)
