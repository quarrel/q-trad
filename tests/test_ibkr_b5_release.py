from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from qtrad.domain.ibkr_qualification import (
    IbkrQualificationStage,
    IbkrQualifiedContract,
    VerifiedB3Qualification,
)
from qtrad.domain.instruments import AssetClass
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime import ibkr_b5
from qtrad.runtime.ibkr_b4 import (
    B4_INSTRUMENTS,
    B4_RELEASE_CONTRACT,
    IbkrB4DeploymentDescriptor,
)
from qtrad.runtime.ibkr_b5 import promote_b5_configuration
from qtrad.runtime.ibkr_native_capture import IbkrNativeCaptureConfiguration
from qtrad.runtime.ibkr_release import IbkrAuthorityPaths, sha256_path
from qtrad.runtime.universe import load_capture_candidates

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_FROZEN_AT = datetime(2026, 8, 5, 14, 11, tzinfo=UTC)
_PARENT_VALID_FROM = datetime(2026, 8, 10, tzinfo=UTC)


def _evidence(index: int, *, fx: bool, currency: str) -> IbkrContractEvidence:
    return IbkrContractEvidence(
        con_id=1000 + index,
        symbol=f"S{index}",
        local_symbol=f"L{index}",
        security_type="CASH" if fx else "CFD",
        exchange="IDEALPRO" if fx else "SMART",
        currency=currency,
        trading_class=f"T{index}",
        multiplier=None,
        minimum_tick=Decimal("0.00005") if fx else Decimal("0.1"),
        market_rule_ids=("1",),
        valid_exchanges=("SMART",),
        long_name=f"Contract {index}",
        underlier_con_id=None,
        timezone="UTC",
        trading_hours="20260810:0000-20260810:2359",
        liquid_hours="20260810:0000-20260810:2359",
        primary_exchange=None,
        contract_month=None,
    )


def _qualification(
    parent: IbkrNativeCaptureConfiguration, parent_path: Path
) -> VerifiedB3Qualification:
    capability = object.__new__(VerifiedB3Qualification)
    values = {
        "stage": IbkrQualificationStage.B4_EXACT_SIX,
        "artifact_sha256": "9" * 64,
        "release_contract": B4_RELEASE_CONTRACT,
        "release_sha256": sha256_path(parent_path, label="B4 parent"),
        "configuration_hash": parent.configuration_hash,
        "capture_source_id": "ibkr-paper-v1",
        "universe_id": "capture-ibkr-v1",
        "instruments": B4_INSTRUMENTS,
        "contracts": tuple(
            IbkrQualifiedContract(
                instrument_id=listing.instrument_id,
                listing_id=listing.listing_id,
                con_id=parent.contract_evidence[listing.listing_id].con_id,
            )
            for listing in parent.listings
        ),
        "qualified_at": _PARENT_VALID_FROM,
    }
    for name, value in values.items():
        object.__setattr__(capability, f"_{name}", value)
    return capability


def test_b5_promotes_reviewed_twenty_and_preserves_b4_listings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = load_capture_candidates(
        _REPOSITORY_ROOT / "config/capture-ibkr-v1-candidates.toml"
    )
    evidence = {
        candidate.instrument_id: _evidence(
            index,
            fx=candidate.asset_class is AssetClass.FX,
            currency=candidate.quote_currency,
        )
        for index, candidate in enumerate(candidates.instruments, start=1)
    }
    parent_listings = [
        ibkr_b5._expected_listing(
            candidate.instrument_id,
            display_name=candidate.display_name,
            currency=candidate.quote_currency,
            contract=evidence[candidate.instrument_id],
            valid_from=_PARENT_VALID_FROM,
        )
        for candidate in candidates.instruments
        if candidate.instrument_id in B4_INSTRUMENTS
    ]
    parent = IbkrNativeCaptureConfiguration.from_reviewed(
        parent_listings,
        {listing.listing_id: evidence[listing.instrument_id] for listing in parent_listings},
    )
    parent_path = tmp_path / "b4.json"
    parent_path.write_text("authenticated B4 parent\n", encoding="utf-8")
    paths = []
    for name in ("capability", "operator", "selection", "catalogue", "probe"):
        path = tmp_path / name
        path.write_text(f"{name}\n", encoding="utf-8")
        paths.append(path)
    authority_paths = IbkrAuthorityPaths(*paths)
    decisions = tuple(
        SimpleNamespace(
            instrument_id=candidate.instrument_id,
            acquisition_eligible=True,
            fingerprint=SimpleNamespace(con_id=evidence[candidate.instrument_id].con_id),
        )
        for candidate in candidates.instruments
    )
    monkeypatch.setattr(
        ibkr_b5,
        "verify_ibkr_contract_selection",
        lambda *_args, **_kwargs: SimpleNamespace(
            catalogue_hash=candidates.configuration_hash,
            frozen_at=_FROZEN_AT,
            decisions=decisions,
        ),
    )
    monkeypatch.setattr(ibkr_b5, "load_ibkr_capability_review", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(ibkr_b5, "load_capture_candidates", lambda _path: candidates)
    monkeypatch.setattr(
        ibkr_b5,
        "review_contract_evidence",
        lambda _review, instrument_id, _con_id, *, label: evidence[instrument_id],
    )
    monkeypatch.setattr(
        ibkr_b5, "load_authenticated_b4_configuration", lambda *_args, **_kwargs: parent
    )
    monkeypatch.setattr(
        ibkr_b5, "has_verified_ibkr_capture_qualification_provenance", lambda _value: True
    )
    qualification = _qualification(parent, parent_path)

    promotion = promote_b5_configuration(
        authority_paths=authority_paths,
        parent_release_path=parent_path,
        parent_authority_paths=authority_paths,
        parent_descriptor=cast(
            IbkrB4DeploymentDescriptor,
            SimpleNamespace(
                parent_release_path=parent_path,
                parent_authority_paths=authority_paths,
            ),
        ),
        b3_qualification=qualification,
        b4_qualification=qualification,
    )

    promoted = {item.instrument_id: item for item in promotion.configuration.listings}
    inherited = {item.instrument_id: item for item in parent.listings}
    assert len(promoted) == 20
    assert set(promoted) == {item.instrument_id for item in candidates.instruments}
    assert all(promoted[instrument_id] == listing for instrument_id, listing in inherited.items())
    assert all(
        listing.valid_from == _FROZEN_AT
        for instrument_id, listing in promoted.items()
        if instrument_id not in B4_INSTRUMENTS
    )
