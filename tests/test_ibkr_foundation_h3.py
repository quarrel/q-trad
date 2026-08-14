from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qtrad import __main__ as cli
from qtrad.runtime.ibkr_foundation import (
    authenticate_ibkr_foundation,
    load_ibkr_foundation,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.ibkr_foundation_promotion import (
    authenticate_ibkr_foundation_promotion,
)
from qtrad.runtime.settings import Settings
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
    import inspect

    assert (
        inspect.signature(verify_ibkr_foundation).parameters["receipt_output"].default
        is inspect.Parameter.empty
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


def test_stage8_v3_cli_dispatch_requires_complete_stage7_parent(tmp_path: Path) -> None:
    bundle = tmp_path / "foundation.json"
    bundle.write_text(
        json.dumps({"contract": "qtrad-ibkr-historical-foundation-v2"}) + "\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "foundation-receipt.json"
    base = [
        "research",
        "foundation",
        "verify",
        "--bundle",
        str(bundle),
        "--receipt-output",
        str(receipt),
    ]

    class _Clock:
        def now(self) -> datetime:
            return datetime(2026, 8, 14, tzinfo=UTC)

    for parent_args in (
        (),
        ("--stage7-manifest", "stage7/manifest.json"),
        ("--stage7-receipt", "stage7/receipt.json"),
    ):
        parsed = cli.build_parser().parse_args([*base, *parent_args])
        with pytest.raises(
            ValueError, match="Stage 8 v2 verification requires Stage 7 manifest and receipt"
        ):
            asyncio.run(
                cli._verify_foundation_bundle(
                    Settings(research_root=tmp_path),
                    _Clock(),
                    bundle,
                    receipt_output=receipt,
                    stage7_manifest_path=parsed.stage7_manifest,
                    stage7_receipt_path=parsed.stage7_receipt,
                )
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


def test_stage8_foundation_identity_classifies_selected_input_and_readiness() -> None:
    from copy import deepcopy

    import qtrad.runtime.ibkr_foundation as foundation_runtime

    payload: Any = {
        "provider_history": {
            "stage7": {
                "dataset_sha256": "d" * 64,
                "result_id": "r" * 64,
                "contract_selection_sha256": "s" * 64,
                "selected_input": {
                    "contract": "qtrad-stage7-selected-input-semantic-v1",
                    "parent_dataset_sha256": "d" * 64,
                    "requested_instrument_ids": ["fx:eur-usd"],
                    "interval_start": "2026-01-01T00:00:00+00:00",
                    "interval_end": "2026-01-02T00:00:00+00:00",
                    "row_count_upper_bound": 10,
                    "semantic_id": "x",
                },
            },
            "dataset": {"contract_selection_sha256": "s" * 64},
        },
        "configuration": {"configuration_id": "config"},
        "semantic_children": {"observations": "o" * 64},
        "readiness_semantics": {
            "projection_contract": "qtrad-stage8-readiness-semantics-v1",
            "state": "READY",
            "causes": [],
        },
    }
    payload["provider_history"]["stage7"]["selected_input"]["semantic_id"] = (
        foundation_runtime._sha(
            {
                key: value
                for key, value in payload["provider_history"]["stage7"]["selected_input"].items()
                if key != "semantic_id"
            }
        )
    )
    original_id = foundation_runtime._v3_foundation_id(payload)

    changed_selection = deepcopy(payload)
    selected = changed_selection["provider_history"]["stage7"]["selected_input"]
    selected["row_count_upper_bound"] = 11
    selected["semantic_id"] = foundation_runtime._sha(
        {key: value for key, value in selected.items() if key != "semantic_id"}
    )
    assert foundation_runtime._v3_foundation_id(changed_selection) != original_id

    physical_change = deepcopy(payload)
    physical_stage7 = physical_change["provider_history"]["stage7"]
    physical_stage7["closure_id"] = "c" * 64
    physical_stage7["verification_id"] = "v" * 64
    physical_stage7["manifest_sha256"] = "m" * 64
    assert foundation_runtime._v3_foundation_id(physical_change) == original_id

    readiness_change = deepcopy(payload)
    readiness_change["readiness_semantics"]["state"] = "NOT_READY"
    assert foundation_runtime._v3_foundation_id(readiness_change) != original_id


@pytest.mark.parametrize("entry_kind", ("symlink", "directory"))
def test_stage8_v3_authentication_rejects_symlink_and_orphan_directory(
    tmp_path: Path, entry_kind: str
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
    document = json.loads(output.read_bytes())
    child = document["payload"]["children"]["observations"][0]
    child_path = output.parent / child["file"]
    child_root = output.parent / Path(child["file"]).parts[0]
    orphan = child_root / f"orphan-{entry_kind}"
    if entry_kind == "symlink":
        orphan.symlink_to(child_path)
    else:
        orphan.mkdir()
    with pytest.raises(ValueError, match="child tree"):
        authenticate_ibkr_foundation(output, receipt=foundation_receipt)


def test_stage8_promotion_output_preflight_rejects_unsafe_paths_before_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    import qtrad.runtime.ibkr_foundation_promotion as promotion_runtime

    def fail_if_authenticated(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unsafe promotion path reached foundation authority")

    monkeypatch.setattr(promotion_runtime, "authenticate_ibkr_foundation", fail_if_authenticated)
    foundation = tmp_path / "foundation.json"
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    outputs = (
        tmp_path / ".." / "outside" / "traversal-promotion.json",
        linked / "symlink-promotion.json",
    )
    for output in outputs:
        with pytest.raises(ValueError, match="promotion output"):
            promotion_runtime._create_v3_promotion(
                foundation,
                receipt=tmp_path / "receipt.json",
                output=output,
                authorized_by="operator",
                authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
                authorization_reference="fixture",
            )
    assert not (outside / "traversal-promotion.json").exists()
    assert not (outside / "symlink-promotion.json").exists()


def _rewrite_v3_manifest_and_receipt(bundle: Path, receipt: Path) -> None:
    import qtrad.runtime.ibkr_foundation as foundation_runtime

    document = json.loads(bundle.read_bytes())
    unsigned = {key: value for key, value in document.items() if key != "manifest_sha256"}
    document["manifest_sha256"] = foundation_runtime._sha(unsigned)
    bundle.write_bytes(foundation_runtime._json_bytes(document) + b"\n")

    receipt_document = json.loads(receipt.read_bytes())
    receipt_document["foundation_manifest_sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
    receipt_identity = {
        key: value for key, value in receipt_document.items() if key != "verification_id"
    }
    receipt_document["verification_id"] = foundation_runtime._sha(receipt_identity)
    receipt.write_bytes(foundation_runtime._json_bytes(receipt_document) + b"\n")


@pytest.mark.parametrize(
    "location",
    (
        ("payload",),
        ("payload", "configuration"),
        ("payload", "provider_history"),
        ("payload", "provider_history", "stage7"),
        ("payload", "provider_history", "stage7", "selected_input"),
        ("payload", "readiness"),
        ("payload", "readiness", "evidence"),
        ("payload", "readiness_semantics"),
    ),
)
def test_stage8_v3_rejects_unknown_nested_payload_fields(
    tmp_path: Path, location: tuple[str, ...]
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

    document = json.loads(output.read_bytes())
    target: dict[str, Any] = document
    for key in location:
        target = target[key]
    target["unexpected"] = "rejected"
    output.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    _rewrite_v3_manifest_and_receipt(output, foundation_receipt)

    with pytest.raises(ValueError, match=r"fields are not exact|schema"):
        authenticate_ibkr_foundation(output, receipt=foundation_receipt)


def test_stage8_deep_verifier_preflights_invalid_tree_before_stage7(
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
    child_root = output.parent / "foundation.json.children"
    (child_root / "orphan-before-stage7").write_bytes(b"orphan")
    receipt = tmp_path / "foundation-receipt.json"
    stage7_calls: list[object] = []

    import qtrad.runtime.ibkr_foundation as foundation_runtime

    def reject_stage7(*_args: object, **_kwargs: object) -> object:
        stage7_calls.append(None)
        raise AssertionError("invalid Stage 8 tree reached Stage 7")

    monkeypatch.setattr(foundation_runtime, "_stage7_source_v3", reject_stage7)
    with pytest.raises(ValueError, match="child tree"):
        verify_ibkr_foundation(
            output,
            stage7_manifest=stage7_manifest,
            stage7_receipt=stage7_receipt,
            receipt_output=receipt,
            workers=1,
        )
    assert stage7_calls == []
    assert not receipt.exists()


def test_stage8_public_authentication_is_current_v3_only(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy-foundation.json"
    legacy.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="current Stage 8 v3"):
        authenticate_ibkr_foundation(legacy, receipt=tmp_path / "receipt.json")
    with pytest.raises(ValueError, match="current Stage 8 v3"):
        authenticate_ibkr_foundation_promotion(
            legacy,
            receipt=tmp_path / "receipt.json",
            promotion=tmp_path / "promotion.json",
        )


def test_stage8_v3_cli_authenticate_promotion_reports_current_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    import qtrad.__main__ as cli

    monkeypatch.setattr(
        cli,
        "authenticate_ibkr_foundation_promotion",
        lambda *_args, **_kwargs: SimpleNamespace(
            foundation_bundle_id="f" * 64,
            promotion_sha256="p" * 64,
        ),
    )
    cli.main(
        [
            "research",
            "foundation",
            "authenticate-promotion",
            "--bundle",
            "foundation.json",
            "--receipt",
            "foundation-receipt.json",
            "--promotion",
            "promotion.json",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "contract": "qtrad-ibkr-foundation-confirmatory-promotion-v2",
        "foundation_id": "f" * 64,
        "promotion_sha256": "p" * 64,
        "state": "CONFIRMATORY_PROMOTED",
    }
