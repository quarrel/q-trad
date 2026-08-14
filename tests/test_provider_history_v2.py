from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import polars as pl
import pytest

import qtrad.runtime.ibkr_foundation as foundation_runtime
import qtrad.runtime.ibkr_results as ibkr_results_runtime
from qtrad.domain.events import JsonValue
from qtrad.domain.provider_history import ProviderHistoricalDataset
from qtrad.domain.research import ObservationDataset
from qtrad.runtime import provider_history_v2 as provider_history_v2_runtime
from qtrad.runtime.ibkr_foundation import verify_ibkr_foundation, write_ibkr_foundation
from qtrad.runtime.ibkr_results import IbkrHistoricalResultStream
from qtrad.runtime.provider_history import (
    authenticate_provider_history,
    read_provider_history_source_evidence,
    verify_provider_history,
)
from qtrad.runtime.provider_history_v2 import repack_provider_history_v2
from tests import test_provider_history as provider_history_test_runtime
from tests.test_provider_history import (
    _INSTRUMENT,
    _START,
    _build_stage6_artifact,
    _published_provider_history,
)
from tests.test_r1_foundation import _config


def _repack_fixture(
    tmp_path: Path, *, day_count: int = 182
) -> tuple[Path, Path, ProviderHistoricalDataset]:
    artifact = _build_stage6_artifact(day_count=day_count)
    _, dataset, v1_manifest = _published_provider_history(tmp_path, artifact=artifact)
    v1_receipt = tmp_path / "provider-history-v1-receipt.json"
    verify_provider_history(v1_manifest, receipt_output=v1_receipt)
    v2_receipt = tmp_path / "provider-history-v2-receipt.json"
    v2_manifest, repacked = repack_provider_history_v2(
        v1_manifest,
        v1_receipt,
        tmp_path / "provider-v2",
        receipt_output=v2_receipt,
    )
    assert repacked == dataset
    return v2_manifest, v2_receipt, dataset


def test_v2_repack_preserves_semantics_and_monthly_physical_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _build_stage6_artifact(day_count=182)
    _, dataset, v1_manifest = _published_provider_history(tmp_path, artifact=artifact)
    v1_receipt = tmp_path / "provider-history-v1-receipt.json"
    verify_provider_history(v1_manifest, receipt_output=v1_receipt)

    def reject_stage6_replay(self: IbkrHistoricalResultStream):
        raise AssertionError("Stage 6 semantic replay reached")

    monkeypatch.setattr(IbkrHistoricalResultStream, "iter_request_results", reject_stage6_replay)
    v2_receipt = tmp_path / "provider-history-v2-receipt.json"
    v2_manifest, repacked = repack_provider_history_v2(
        v1_manifest,
        v1_receipt,
        tmp_path / "provider-v2",
        receipt_output=v2_receipt,
    )

    document = json.loads(v2_manifest.read_bytes())
    parts = document["parts"]
    assert repacked.dataset_sha256 == dataset.dataset_sha256
    assert 1 < len(parts) < len(dataset.partitions)
    assert all(part["part_ordinal"] == 1 for part in parts)
    assert all(
        (v2_manifest.parent / part["path"]).stat().st_size
        <= provider_history_v2_runtime._TARGET_PART_BYTES
        for part in parts
    )
    assert sum(part["row_count"] for part in parts) == dataset.row_count
    assert verify_provider_history(v2_manifest) == dataset
    assert authenticate_provider_history(v2_manifest, receipt=v2_receipt).dataset == dataset
    v1_rows = authenticate_provider_history(v1_manifest, receipt=v1_receipt).observations
    v2_rows = read_provider_history_source_evidence(v2_manifest).observations
    assert tuple(v2_rows) == tuple(v1_rows)

    observed_buffers: list[int] = []
    original_split = provider_history_v2_runtime._split_rows

    def bounded_split(rows):
        observed_buffers.append(len(rows))
        return original_split(rows)

    monkeypatch.setattr(provider_history_v2_runtime, "_MAX_PART_ROWS", 10)
    monkeypatch.setattr(provider_history_v2_runtime, "_split_rows", bounded_split)
    second_manifest, second_dataset = repack_provider_history_v2(
        v1_manifest,
        v1_receipt,
        tmp_path / "provider-v2-split",
        receipt_output=tmp_path / "provider-history-v2-split-receipt.json",
    )
    second_document = json.loads(second_manifest.read_bytes())
    assert second_dataset.dataset_sha256 == dataset.dataset_sha256
    assert second_document["physical_manifest_sha256"] != document["physical_manifest_sha256"]
    assert any(part["part_ordinal"] > 1 for part in second_document["parts"])
    assert observed_buffers and max(observed_buffers) <= 10
    assert verify_provider_history(second_manifest) == dataset


def test_v2_authentication_prunes_before_decode_and_filters_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, receipt, dataset = _repack_fixture(tmp_path, day_count=90)
    start = _START + timedelta(days=44)
    end = start + timedelta(days=3)
    original_read = provider_history_v2_runtime._read_v2_part

    def reject_read(*_args: object) -> None:
        raise AssertionError("provider-history v2 row decoding reached")

    monkeypatch.setattr(provider_history_v2_runtime, "_read_v2_part", reject_read)
    with pytest.raises(ValueError, match="inside"):
        verify_provider_history(
            manifest,
            receipt_output=manifest.parent / "invalid-receipt.json",
        )
    monkeypatch.setattr(provider_history_v2_runtime, "_read_v2_part", original_read)
    decoded: list[Path] = []

    def record_read(path: Path, reference):
        decoded.append(path)
        return original_read(path, reference)

    monkeypatch.setattr(provider_history_v2_runtime, "_read_v2_part", record_read)
    evidence = authenticate_provider_history(
        manifest,
        receipt=receipt,
        instrument_ids=(str(_INSTRUMENT),),
        interval_start=start,
        interval_end=end,
    )
    rows = tuple(evidence.observations)

    assert evidence.dataset == dataset
    assert evidence.selection is not None
    assert len(decoded) == 1
    assert "month-2026-03" in decoded[0].as_posix()
    assert all(start <= row.interval_start and row.interval_end <= end for row in rows)
    assert {row.instrument_id for row in rows} == {str(_INSTRUMENT)}


def test_v2_repack_is_create_only_and_selected_part_mutation_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _build_stage6_artifact(day_count=40)
    _, _, v1_manifest = _published_provider_history(tmp_path, artifact=artifact)
    source_before = {
        path.relative_to(v1_manifest.parent).as_posix(): path.read_bytes()
        for path in v1_manifest.parent.rglob("*")
        if path.is_file()
    }
    v1_receipt = tmp_path / "provider-history-v1-receipt.json"
    verify_provider_history(v1_manifest, receipt_output=v1_receipt)
    output = tmp_path / "provider-v2"
    receipt = tmp_path / "provider-history-v2-receipt.json"
    manifest, _ = repack_provider_history_v2(
        v1_manifest,
        v1_receipt,
        output,
        receipt_output=receipt,
    )

    with pytest.raises(FileExistsError):
        repack_provider_history_v2(
            v1_manifest,
            v1_receipt,
            output,
            receipt_output=tmp_path / "unused-receipt.json",
        )
    blocked_receipt = tmp_path / "existing-receipt.json"
    blocked_receipt.write_bytes(b"existing")
    blocked_output = tmp_path / "blocked-provider-v2"
    with pytest.raises(FileExistsError):
        repack_provider_history_v2(
            v1_manifest,
            v1_receipt,
            blocked_output,
            receipt_output=blocked_receipt,
        )
    assert not blocked_output.exists()

    original_rename = provider_history_v2_runtime.os.rename
    raced_output = tmp_path / "raced-provider-v2"
    racer = raced_output / "racer"

    def race_rename(source: Path, destination: Path) -> None:
        Path(destination).mkdir()
        racer.write_bytes(b"racer")
        original_rename(source, destination)

    monkeypatch.setattr(provider_history_v2_runtime.os, "rename", race_rename)
    with pytest.raises(OSError):
        repack_provider_history_v2(
            v1_manifest,
            v1_receipt,
            raced_output,
            receipt_output=tmp_path / "raced-receipt.json",
        )
    assert racer.read_bytes() == b"racer"
    monkeypatch.setattr(provider_history_v2_runtime.os, "rename", original_rename)

    assert {
        path.relative_to(v1_manifest.parent).as_posix(): path.read_bytes()
        for path in v1_manifest.parent.rglob("*")
        if path.is_file()
    } == source_before

    reference = provider_history_v2_runtime.ProviderHistoryV2PartReference.from_json_value(
        json.loads(manifest.read_bytes())["parts"][0]
    )
    part = manifest.parent / reference.path
    original_part = part.read_bytes()
    part.write_bytes(original_part + b"mutation")
    with pytest.raises(ValueError, match="physical part bytes changed"):
        authenticate_provider_history(
            manifest,
            receipt=receipt,
            instrument_ids=(reference.instrument_id,),
            interval_start=reference.minimum_interval_start,
            interval_end=reference.maximum_interval_end,
        )

    part.write_bytes(original_part)
    orphan = manifest.parent / "orphan"
    orphan.write_bytes(b"orphan")
    with pytest.raises(ValueError, match="tree differs"):
        verify_provider_history(manifest)


def test_v2_physical_and_semantic_mutations_are_rejected(tmp_path: Path) -> None:
    manifest, _, _ = _repack_fixture(tmp_path, day_count=10)
    original_manifest = manifest.read_bytes()
    original_document = json.loads(original_manifest)
    original_reference = provider_history_v2_runtime.ProviderHistoryV2PartReference.from_json_value(
        original_document["parts"][0]
    )
    part = manifest.parent / original_reference.path
    original_part = part.read_bytes()

    def publish_document(document: dict[str, object]) -> None:
        identity = dict(document)
        identity.pop("physical_manifest_sha256")
        document["physical_manifest_sha256"] = provider_history_v2_runtime._sha256_json(identity)
        manifest.write_bytes(
            provider_history_v2_runtime.canonical_json_bytes(cast(dict[str, JsonValue], document))
        )

    document = json.loads(original_manifest)
    document["parts"][0]["path"] = "non-canonical.parquet"
    publish_document(document)
    with pytest.raises(ValueError, match="path is not canonical"):
        verify_provider_history(manifest)

    document = json.loads(original_manifest)
    document["parts"][0]["row_count"] += 1
    publish_document(document)
    with pytest.raises(ValueError, match="row count changed"):
        verify_provider_history(manifest)

    empty = io.BytesIO()
    pl.read_parquet(io.BytesIO(original_part)).slice(0, 0).write_parquet(empty)
    empty_payload = empty.getvalue()
    part.write_bytes(empty_payload)
    document = json.loads(original_manifest)
    document["parts"][0]["bytes_sha256"] = hashlib.sha256(empty_payload).hexdigest()
    publish_document(document)
    with pytest.raises(ValueError, match="row count changed"):
        verify_provider_history(manifest)

    part.write_bytes(original_part)
    rows = list(provider_history_v2_runtime._read_v2_part(part, original_reference))
    first = rows[0]
    changed = replace(
        first,
        open=first.open + Decimal(1),
        high=first.high + Decimal(1),
        low=first.low + Decimal(1),
        close=first.close + Decimal(1),
        _identity_validation_bypass=True,
    )
    rows[0] = replace(
        changed,
        observation_sha256=provider_history_v2_runtime.sha256_json(changed.identity_payload()),
        _identity_validation_bypass=False,
    )
    changed_payload = provider_history_v2_runtime._parquet_bytes(tuple(rows))
    part.write_bytes(changed_payload)
    document = json.loads(original_manifest)
    document["parts"][0]["bytes_sha256"] = hashlib.sha256(changed_payload).hexdigest()
    document["parts"][0]["ordered_row_sha256"] = provider_history_v2_runtime.sha256_json(
        {"observation_sha256": [row.observation_sha256 for row in rows]}
    )
    publish_document(document)
    with pytest.raises(ValueError, match="semantic dataset identity changed"):
        verify_provider_history(manifest)


def test_stage8_identities_are_equal_for_v1_and_pruned_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_start = _START + timedelta(days=1, hours=23)
    monkeypatch.setattr(provider_history_test_runtime, "_START", fixture_start)
    artifact = _build_stage6_artifact(
        day_count=180,
        minute_span=True,
        session_weekdays_only=True,
    )
    _, dataset, v1_manifest = _published_provider_history(tmp_path, artifact=artifact)
    v1_receipt = tmp_path / "provider-history-v1-receipt.json"
    verify_provider_history(v1_manifest, receipt_output=v1_receipt)
    monkeypatch.setattr(provider_history_v2_runtime, "_MAX_PART_ROWS", 10)
    v2_receipt = tmp_path / "provider-history-v2-receipt.json"
    v2_manifest, _ = repack_provider_history_v2(
        v1_manifest,
        v1_receipt,
        tmp_path / "provider-v2",
        receipt_output=v2_receipt,
    )
    start = fixture_start + timedelta(minutes=10)
    end = start + timedelta(minutes=3)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=start,
        end=end,
    )
    monkeypatch.setattr(foundation_runtime, "_BOUNDED_PROVIDER_HISTORY_ROWS", 0)

    write_ibkr_foundation(
        tmp_path / "foundation-v1.json",
        provider_manifest=v1_manifest,
        provider_history_receipt=v1_receipt,
        configuration=configuration,
    )
    document = json.loads(v2_manifest.read_bytes())
    selected_paths = {
        v2_manifest.parent / item["path"]
        for item in document["parts"]
        if item["instrument_id"] == str(_INSTRUMENT)
        and item["maximum_interval_end"] > configuration.required_observation_start.isoformat()
        and item["minimum_interval_start"] < configuration.required_observation_end.isoformat()
    }
    unselected_paths = {
        v2_manifest.parent / item["path"] for item in document["parts"]
    } - selected_paths
    source_manifest = v2_manifest.parent / document["source_result"]["path"]
    source_document = json.loads(source_manifest.read_bytes())
    unselected_source_children = {
        source_manifest.parent / item["path"] for item in source_document["request_results"]
    }
    assert selected_paths and unselected_paths and unselected_source_children
    original_read = provider_history_v2_runtime._read_bounded
    original_source_read = ibkr_results_runtime._read_bytes

    def reject_unselected(path: Path, field: str) -> bytes:
        if path in unselected_paths:
            raise AssertionError("unselected provider-history part was read")
        return original_read(path, field)

    def reject_source_children(
        path: Path,
        field: str,
        *,
        maximum: int = ibkr_results_runtime.MAX_IBKR_RESULT_BYTES,
    ) -> bytes:
        if path in unselected_source_children:
            raise AssertionError("embedded Stage 6 result child was read")
        return original_source_read(path, field, maximum=maximum)

    monkeypatch.setattr(provider_history_v2_runtime, "_read_bounded", reject_unselected)
    monkeypatch.setattr(ibkr_results_runtime, "_read_bytes", reject_source_children)
    write_ibkr_foundation(
        tmp_path / "foundation-v2.json",
        provider_manifest=v2_manifest,
        provider_history_receipt=v2_receipt,
        configuration=configuration,
        checkpoint_root=tmp_path / "stage8-checkpoint",
    )

    v1 = verify_ibkr_foundation(
        tmp_path / "foundation-v1.json",
        provider_history_receipt=v1_receipt,
    )
    v2 = verify_ibkr_foundation(
        tmp_path / "foundation-v2.json",
        provider_history_receipt=v2_receipt,
    )

    def persisted_observations(bundle: Path) -> tuple[dict[str, JsonValue], ...]:
        document = json.loads(bundle.read_bytes())
        references = document["payload"]["children"]["observations"]
        return tuple(
            row
            for reference in references
            for row in foundation_runtime._read_child_rows(
                bundle.parent / reference["file"],
                expected_row_count=reference["row_count"],
            )
        )

    v1_rows = persisted_observations(tmp_path / "foundation-v1.json")
    v2_rows = persisted_observations(tmp_path / "foundation-v2.json")
    assert dataset.row_count == 180
    assert 0 < len(v1_rows) < dataset.row_count
    assert v2_rows == v1_rows
    assert v2.observations.dataset_id == v1.observations.dataset_id
    assert v2.panel.dataset_id == v1.panel.dataset_id
    assert v2.targets.dataset_id == v1.targets.dataset_id
    assert v2.folds.dataset_id == v1.folds.dataset_id
    assert v2.readiness.as_json() == v1.readiness.as_json()

    selected = next(iter(selected_paths))
    selected.write_bytes(selected.read_bytes() + b"\n")
    rejected = tmp_path / "checkpoint-reuse-foundation.json"
    with pytest.raises(ValueError, match="physical part bytes changed"):
        write_ibkr_foundation(
            rejected,
            provider_manifest=v2_manifest,
            provider_history_receipt=v2_receipt,
            configuration=configuration,
            checkpoint_root=tmp_path / "stage8-checkpoint",
        )
    assert not rejected.exists()
