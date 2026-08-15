from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import qtrad.runtime.ibkr_foundation_promotion as promotion_runtime
from qtrad.runtime.ibkr_foundation_promotion import (
    authenticate_ibkr_foundation_promotion,
    create_ibkr_foundation_confirmatory_promotion,
)
from tests.test_ibkr_foundation import _verified_fixture


def test_qualifying_promotion_is_create_only_and_authenticates_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foundation, _stage7_manifest, _stage7_receipt, receipt = _verified_fixture(tmp_path)
    monkeypatch.setattr(promotion_runtime, "_require_detached_source", lambda: None)
    output = tmp_path / "promotion.json"
    try:
        authority = create_ibkr_foundation_confirmatory_promotion(
            foundation,
            receipt=receipt,
            output=output,
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 15, tzinfo=UTC),
            authorization_reference="review-approval",
        )
    except ValueError as error:
        if "nonqualifying" not in str(error):
            raise
        with pytest.raises(ValueError, match="nonqualifying"):
            create_ibkr_foundation_confirmatory_promotion(
                foundation,
                receipt=receipt,
                output=output,
                authorized_by="operator",
                authorized_at=datetime(2026, 8, 15, tzinfo=UTC),
                authorization_reference="review-approval",
            )
        return
    assert authority.promotion_id
    assert output.is_file()
    with pytest.raises(FileExistsError):
        create_ibkr_foundation_confirmatory_promotion(
            foundation,
            receipt=receipt,
            output=output,
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 15, tzinfo=UTC),
            authorization_reference="review-approval",
        )
    authenticated = authenticate_ibkr_foundation_promotion(
        foundation,
        receipt=receipt,
        promotion=output,
    )
    assert authenticated.promotion_id == authority.promotion_id


def test_promotion_fails_closed_before_replay_and_rejects_nonqualifying(
    tmp_path: Path,
) -> None:
    foundation, _stage7_manifest, _stage7_receipt, receipt = _verified_fixture(tmp_path)
    with pytest.raises(ValueError, match=r"nonqualifying|promotion"):
        create_ibkr_foundation_confirmatory_promotion(
            foundation,
            receipt=receipt,
            output=tmp_path / "promotion.json",
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 15, tzinfo=UTC),
            authorization_reference="review",
        )


def test_promotion_rejects_masquerade_root_changes_and_verifier_revocation(
    tmp_path: Path,
) -> None:
    foundation, _stage7_manifest, _stage7_receipt, receipt = _verified_fixture(tmp_path)
    masquerade = tmp_path / "not-foundation.json"
    masquerade.write_bytes(foundation.read_bytes())
    with pytest.raises(ValueError, match="promotion"):
        authenticate_ibkr_foundation_promotion(
            masquerade,
            receipt=receipt,
            promotion=tmp_path / "missing-promotion.json",
        )
    mutated = json.loads(receipt.read_bytes())
    mutated["foundation_id"] = "0" * 64
    receipt.write_text(json.dumps(mutated) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"receipt|identity"):
        authenticate_ibkr_foundation_promotion(
            foundation,
            receipt=receipt,
            promotion=tmp_path / "missing-promotion.json",
        )


def test_promotion_authentication_rejects_legacy_contract_without_fallback(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-foundation.json"
    legacy.write_text(
        json.dumps({"contract": "qtrad-ibkr-historical-foundation-v1"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="current Stage 8 v3 promotion"):
        authenticate_ibkr_foundation_promotion(
            legacy,
            receipt=tmp_path / "receipt.json",
            promotion=tmp_path / "promotion.json",
        )
