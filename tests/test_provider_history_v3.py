from __future__ import annotations

import io
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import polars as pl
import pytest

from qtrad.__main__ import build_parser
from qtrad.application.ibkr_foundation import build_ibkr_foundation
from qtrad.application.ibkr_results import build_ibkr_historical_result_artifact
from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.domain.foundation import FoundationConfig
from qtrad.domain.ibkr_results import canonical_json_bytes, sha256_bytes
from qtrad.domain.provider_history import (
    PROVIDER_HISTORY_DECLARED_DELAY,
    PROVIDER_HISTORY_POLICY,
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalDatasetV3,
    ProviderHistoricalObservation,
)
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.ibkr_results import (
    verify_ibkr_historical_result,
    write_ibkr_historical_result,
)
from qtrad.runtime.provider_history_v3 import (
    ProviderHistoryV3PartReference,
    _read_manifest,
    _read_part,
    authenticate_provider_history_v3,
    build_provider_history,
    verify_provider_history,
    verify_provider_history_file_only,
)
from tests.test_ibkr_historical_results import _build_fixture
from tests.test_r1_foundation import _config


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


def _rewrite_part_physical_schema(
    manifest: Path, *, field_name: str, aggregate: object
) -> tuple[Path, ProviderHistoryV3PartReference]:
    import qtrad.runtime.provider_history_v3 as runtime

    reference = _read_manifest(manifest).parts[0]
    part_path = manifest.parent / reference.path
    frame = pl.read_parquet(part_path)
    fields: list[str] = list(runtime.OBSERVATION_FIELDS)
    fields.insert(fields.index("attempt_id"), field_name)
    frame = frame.with_columns(pl.lit(aggregate).alias(field_name)).select(fields)
    output = io.BytesIO()
    frame.write_parquet(output, compression="zstd")
    payload = output.getvalue()
    part_path.write_bytes(payload)
    return part_path, replace(reference, bytes_sha256=sha256_bytes(payload))


def test_v3_reader_accepts_the_sole_retained_aggregate_column(tmp_path: Path) -> None:
    manifest, _, _ = _stage7_closure(tmp_path)
    part_path, reference = _rewrite_part_physical_schema(
        manifest, field_name="aggregate_sha256", aggregate="a" * 64
    )

    rows = _read_part(part_path, reference)

    assert rows


def test_v3_reader_rejects_unknown_retained_physical_column(tmp_path: Path) -> None:
    manifest, _, _ = _stage7_closure(tmp_path)
    part_path, reference = _rewrite_part_physical_schema(
        manifest, field_name="unknown_column", aggregate="a" * 64
    )

    with pytest.raises(ValueError, match="selected part shape changed"):
        _read_part(part_path, reference)


def test_v3_reader_rejects_invalid_retained_aggregate_value(tmp_path: Path) -> None:
    manifest, _, _ = _stage7_closure(tmp_path)
    part_path, reference = _rewrite_part_physical_schema(
        manifest, field_name="aggregate_sha256", aggregate="not-a-sha256"
    )

    with pytest.raises(ValueError, match="retained aggregate_sha256"):
        _read_part(part_path, reference)


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


def test_v3_orphan_directory_rejected_across_auth_routes(tmp_path: Path) -> None:
    manifest, stage6_manifest, stage6_receipt = _stage7_closure(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    verify_provider_history(
        manifest,
        stage6_manifest=stage6_manifest,
        stage6_receipt=stage6_receipt,
        receipt_output=stage7_receipt,
    )
    orphan_root = tmp_path / "orphan-directory"
    shutil.copytree(manifest.parent, orphan_root)
    (orphan_root / "orphan-dir").mkdir()

    with pytest.raises(ValueError, match="closure tree changed"):
        verify_provider_history_file_only(orphan_root / "manifest.json")
    with pytest.raises(ValueError, match="closure tree changed"):
        authenticate_provider_history_v3(orphan_root / "manifest.json", receipt=stage7_receipt)
    with pytest.raises(ValueError, match="closure tree changed"):
        verify_provider_history(
            orphan_root / "manifest.json",
            stage6_manifest=stage6_manifest,
            stage6_receipt=stage6_receipt,
            receipt_output=tmp_path / "orphan-directory-receipt.json",
        )


def test_v3_receipt_output_rejects_noncanonical_and_symlink_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, stage6_manifest, stage6_receipt = _stage7_closure(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-receipt-outside"
    linked = tmp_path / "receipt-link"
    linked.symlink_to(outside, target_is_directory=True)

    import qtrad.runtime.provider_history_v3 as runtime

    def reject_stage6_replay(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Stage 6 replay reached before receipt preflight")

    monkeypatch.setattr(runtime, "authenticate_ibkr_historical_result", reject_stage6_replay)
    for receipt_output in (
        tmp_path / ".." / outside.name / "receipt.json",
        linked / "receipt.json",
    ):
        with pytest.raises(ValueError, match="output path"):
            verify_provider_history(
                manifest,
                stage6_manifest=stage6_manifest,
                stage6_receipt=stage6_receipt,
                receipt_output=receipt_output,
            )
    assert not outside.exists()


def _stage8_configuration() -> FoundationConfig:
    return _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC) + timedelta(minutes=30),
    )


def _authenticated_v3_source(
    tmp_path: Path,
) -> tuple[Path, ProviderHistorySourceEvidence]:
    manifest, stage6_manifest, stage6_receipt = _stage7_closure(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    verify_provider_history(
        manifest,
        stage6_manifest=stage6_manifest,
        stage6_receipt=stage6_receipt,
        receipt_output=stage7_receipt,
    )
    source = authenticate_provider_history_v3(manifest, receipt=stage7_receipt)
    return manifest, source


def test_v3_source_evidence_feeds_normal_stage8_without_stage6_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source = _authenticated_v3_source(tmp_path)
    assert source.dataset.stage6_result_id
    source_result = getattr(source.source_artifact, "source_result", None)
    assert source_result is not None
    assert source_result.result_id == source.dataset.stage6_result_id
    assert source.source_artifact.plan.eligible_contracts

    import qtrad.runtime.provider_history_v3 as runtime

    def reject_stage6_replay(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Stage 6 replay reopened from normal Stage 8 build")

    monkeypatch.setattr(runtime, "authenticate_ibkr_historical_result", reject_stage6_replay)
    build = build_ibkr_foundation(source, _stage8_configuration())
    assert build.provider_history.dataset_sha256 == source.dataset.dataset_sha256
    assert build.readiness.evidence["source_result_id"] == source.dataset.stage6_result_id
    assert "source_aggregate_sha256" not in build.readiness.evidence
