from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from qtrad.__main__ import build_parser
from qtrad.application.ibkr_results import build_ibkr_historical_result_artifact
from qtrad.domain.ibkr_results import canonical_json_bytes
from qtrad.domain.provider_history import (
    PROVIDER_HISTORY_DECLARED_DELAY,
    PROVIDER_HISTORY_POLICY,
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalDatasetV3,
    ProviderHistoricalObservation,
)
from qtrad.runtime.ibkr_results import (
    verify_ibkr_historical_result,
    write_ibkr_historical_result,
)
from qtrad.runtime.provider_history_v3 import (
    ProviderHistoryV3PartReference,
    authenticate_provider_history_v3,
    build_provider_history,
    verify_provider_history,
    verify_provider_history_file_only,
)
from tests.test_ibkr_historical_results import _build_fixture


def _stage6_closure(tmp_path: Path) -> tuple[Path, Path]:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_root = tmp_path / "stage6"
    manifest = write_ibkr_historical_result(result_root, artifact)
    receipt = tmp_path / "stage6-receipt.json"
    verify_ibkr_historical_result(manifest, receipt_output=receipt)
    return manifest, receipt


def _stage7_closure(tmp_path: Path) -> tuple[Path, Path, Path]:
    stage6_manifest, stage6_receipt = _stage6_closure(tmp_path)
    stage7_root = tmp_path / "stage7"
    manifest = build_provider_history(
        stage6_manifest,
        stage6_receipt=stage6_receipt,
        output=stage7_root,
    )
    return manifest, stage6_manifest, stage6_receipt


def test_v3_build_and_deep_verify_write_direct_receipt_backed_closure(
    tmp_path: Path,
) -> None:
    manifest, stage6_manifest, stage6_receipt = _stage7_closure(tmp_path)
    document = json.loads(manifest.read_bytes())

    assert document["contract"] == "qtrad-provider-historical-observations-v3"
    assert document["schema_version"] == 3
    assert "aggregate_sha256" not in document
    assert "source" not in document
    assert all(item["path"].startswith("observations/") for item in document["parts"])
    assert not (manifest.parent / "stage6").exists()

    receipt = tmp_path / "stage7-receipt.json"
    verified = verify_provider_history(
        manifest,
        stage6_manifest=stage6_manifest,
        stage6_receipt=stage6_receipt,
        receipt_output=receipt,
    )

    assert isinstance(verified.dataset, ProviderHistoricalDatasetV3)
    assert receipt.is_file()
    assert verified.dataset.row_count == len(verified.observations)
    assert list(verified.observations)


def test_v3_authentication_never_reads_stage6_or_unselected_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _, _ = _stage7_closure(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    verify_provider_history(
        manifest,
        stage6_manifest=tmp_path / "stage6" / "manifest.json",
        stage6_receipt=tmp_path / "stage6-receipt.json",
        receipt_output=stage7_receipt,
    )

    import qtrad.runtime.provider_history_v3 as runtime

    original_read = runtime._read_part
    part_reads: list[Path] = []

    def counted_part_read(
        path: Path, reference: ProviderHistoryV3PartReference
    ) -> tuple[ProviderHistoricalObservation, ...]:
        part_reads.append(path)
        return original_read(path, reference)

    monkeypatch.setattr(runtime, "_read_part", counted_part_read)
    authenticated = authenticate_provider_history_v3(manifest, receipt=stage7_receipt)
    assert len(authenticated.observations) == authenticated.dataset.row_count
    assert part_reads == []

    rows = list(authenticated.observations)
    assert rows
    assert len(part_reads) == 1
    assert list(authenticated.observations)
    assert len(part_reads) == 1
    selected = authenticate_provider_history_v3(
        manifest,
        receipt=stage7_receipt,
        instrument_ids=("fx:aud-usd",),
        interval_start=rows[0].interval_start,
        interval_end=rows[-1].interval_end,
    )
    assert selected.selection is not None


def test_v3_file_only_verifier_rejects_orphan_and_receipt_mutation(
    tmp_path: Path,
) -> None:
    manifest, stage6_manifest, stage6_receipt = _stage7_closure(tmp_path)
    orphan_root = tmp_path / "orphan"
    shutil.copytree(manifest.parent, orphan_root)
    (orphan_root / "orphan.bin").write_bytes(b"orphan")
    with pytest.raises(ValueError, match="closure tree changed"):
        verify_provider_history_file_only(orphan_root / "manifest.json")
    with pytest.raises(ValueError, match="closure tree changed"):
        verify_provider_history(
            orphan_root / "manifest.json",
            stage6_manifest=stage6_manifest,
            stage6_receipt=stage6_receipt,
            receipt_output=tmp_path / "orphan-receipt.json",
        )

    part_path = json.loads(manifest.read_bytes())["parts"][0]["path"]
    (orphan_root / "orphan-link").symlink_to(orphan_root / part_path)
    with pytest.raises(ValueError, match="closure tree contains a symlink"):
        verify_provider_history_file_only(orphan_root / "manifest.json")

    receipt = tmp_path / "stage7-receipt.json"
    verify_provider_history(
        manifest,
        stage6_manifest=stage6_manifest,
        stage6_receipt=stage6_receipt,
        receipt_output=receipt,
    )
    receipt_document = json.loads(receipt.read_bytes())
    receipt_document["stage6_result_id"] = "0" * 64
    receipt.write_bytes(canonical_json_bytes(receipt_document))
    with pytest.raises(ValueError, match="receipt identity changed"):
        authenticate_provider_history_v3(manifest, receipt=receipt)


def test_v3_cli_verify_requires_stage6_parent_and_receipt_output() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "research",
                "observations",
                "verify-provider-history",
                "--manifest",
                "stage7/manifest.json",
                "--stage6-manifest",
                "stage6/manifest.json",
                "--stage6-receipt",
                "stage6-receipt.json",
            ]
        )


def test_v3_deep_verifier_rejects_changed_availability_policy(tmp_path: Path) -> None:
    stage6_manifest, stage6_receipt = _stage6_closure(tmp_path)
    changed_policy = ProviderHistoricalAvailabilityPolicy(
        selector=PROVIDER_HISTORY_DECLARED_DELAY,
        policy=PROVIDER_HISTORY_POLICY,
        delay=timedelta(minutes=10),
    )
    manifest = build_provider_history(
        stage6_manifest,
        stage6_receipt=stage6_receipt,
        output=tmp_path / "stage7-custom-policy",
        availability_policy=changed_policy,
    )
    with pytest.raises(ValueError, match="availability policy changed"):
        verify_provider_history(
            manifest,
            stage6_manifest=stage6_manifest,
            stage6_receipt=stage6_receipt,
            receipt_output=tmp_path / "stage7-custom-receipt.json",
        )


def test_v3_publish_rejects_symlinked_output_ancestor(tmp_path: Path) -> None:
    stage6_manifest, stage6_receipt = _stage6_closure(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="output path escapes its root"):
        build_provider_history(
            stage6_manifest,
            stage6_receipt=stage6_receipt,
            output=linked / "stage7",
        )
    assert not (outside / "stage7").exists()
