from __future__ import annotations

import json
from pathlib import Path

import pytest

from qtrad.runtime.ibkr_foundation import (
    authenticate_ibkr_foundation,
    load_ibkr_foundation,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.ibkr_foundation_promotion import (
    authenticate_ibkr_foundation_promotion,
)
from tests.test_provider_history_v3 import _authenticated_v3_source, _stage8_configuration


def test_stage8_v3_build_verify_and_authentication_are_parent_receipt_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    output = tmp_path / "foundation.json"
    build = write_ibkr_foundation(
        output,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=_stage8_configuration(),
        workers=1,
    )
    document = json.loads(output.read_bytes())
    assert document["contract"] == "qtrad-ibkr-historical-foundation-v2"
    assert set(document) == {
        "contract",
        "schema_version",
        "source_class",
        "foundation_id",
        "closure_id",
        "payload",
        "manifest_sha256",
    }
    assert "provider_history_manifest" not in document
    assert build.provider_history.dataset_sha256
    import qtrad.runtime.provider_history_v3 as stage7_runtime

    monkeypatch.setattr(
        stage7_runtime,
        "verify_provider_history",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Stage 8 deep verifier reopened Stage 7 deep verification")
        ),
    )

    receipt = tmp_path / "foundation-receipt.json"
    replay = verify_ibkr_foundation(
        output,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        receipt_output=receipt,
        workers=1,
    )
    assert replay.readiness.as_json() == build.readiness.as_json()
    assert receipt.is_file()

    import qtrad.runtime.ibkr_foundation as runtime

    def reject_stage7_replay(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ordinary Stage 8 auth reopened Stage 7")

    monkeypatch.setattr(
        runtime, "authenticate_provider_history_v3", reject_stage7_replay, raising=False
    )
    authenticated = authenticate_ibkr_foundation(output, receipt=receipt)
    assert authenticated["foundation_id"] == document["foundation_id"]
    assert authenticated["closure_id"] == document["closure_id"]
    loaded = load_ibkr_foundation(output, receipt=receipt)
    assert loaded.provider_history.dataset_sha256 == build.provider_history.dataset_sha256


def test_stage8_v3_verifier_requires_create_only_receipt(
    tmp_path: Path,
) -> None:
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    output = tmp_path / "foundation.json"
    write_ibkr_foundation(
        output,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=_stage8_configuration(),
        workers=1,
    )
    with pytest.raises(ValueError, match="receipt output"):
        verify_ibkr_foundation(
            output,
            stage7_manifest=stage7_manifest,
            stage7_receipt=stage7_receipt,
            workers=1,
        )


def test_stage8_v3_promotion_authentication_does_not_require_stage7_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    output = tmp_path / "foundation.json"
    write_ibkr_foundation(
        output,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=_stage8_configuration(),
        workers=1,
    )
    foundation_receipt = tmp_path / "foundation-receipt.json"
    verify_ibkr_foundation(
        output,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        receipt_output=foundation_receipt,
        workers=1,
    )
    import qtrad.runtime.ibkr_foundation as foundation_runtime

    monkeypatch.setattr(
        foundation_runtime,
        "authenticate_provider_history_v3",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("promotion reopened Stage 7")
        ),
        raising=False,
    )
    with pytest.raises(ValueError, match="promotion"):
        authenticate_ibkr_foundation_promotion(
            output,
            receipt=foundation_receipt,
            promotion=tmp_path / "missing-promotion.json",
        )


def test_stage8_v3_cli_uses_stage7_parent_and_promotion_has_no_replay_flags() -> None:
    from qtrad.__main__ import build_parser

    parsed = build_parser().parse_args(
        [
            "research",
            "foundation",
            "verify",
            "--bundle",
            "foundation.json",
            "--stage7-manifest",
            "stage7/manifest.json",
            "--stage7-receipt",
            "stage7/receipt.json",
            "--receipt-output",
            "foundation-receipt.json",
        ]
    )
    assert parsed.stage7_manifest == Path("stage7/manifest.json")
    assert parsed.stage7_receipt == Path("stage7/receipt.json")
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "research",
                "foundation",
                "promote-confirmatory",
                "--bundle",
                "foundation.json",
                "--receipt",
                "foundation-receipt.json",
                "--provider-history-receipt",
                "stage7/receipt.json",
                "--authorized-by",
                "operator",
                "--authorized-at",
                "2026-08-14T00:00:00+00:00",
                "--authorization-reference",
                "approval",
                "--output",
                "promotion.json",
            ]
        )


def test_stage8_v3_authentication_rejects_mutated_child_bytes(tmp_path: Path) -> None:
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    output = tmp_path / "foundation.json"
    write_ibkr_foundation(
        output,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=_stage8_configuration(),
        workers=1,
    )
    foundation_receipt = tmp_path / "foundation-receipt.json"
    verify_ibkr_foundation(
        output,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        receipt_output=foundation_receipt,
        workers=1,
    )
    document = json.loads(output.read_bytes())
    child = document["payload"]["children"]["observations"][0]
    child_path = output.parent / child["file"]
    child_path.write_bytes(child_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match=r"closure|child"):
        authenticate_ibkr_foundation(output, receipt=foundation_receipt)
