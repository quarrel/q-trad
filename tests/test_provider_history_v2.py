from __future__ import annotations

import hashlib
import io
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import polars as pl
import pytest

import qtrad.runtime.provider_history_v2 as provider_history_v2_runtime
from qtrad.domain.events import JsonValue
from qtrad.runtime.provider_history_v2 import (
    authenticate_provider_history_v2,
    verify_provider_history_v2,
)
from tests.test_provider_history import (
    _FIXTURE_ROOT,
    _provider_history_receipt,
    _published_provider_history,
)


def test_v2_fixture_deep_verifies_and_authenticates(tmp_path: Path) -> None:
    _, dataset, manifest = _published_provider_history(tmp_path)
    receipt = _provider_history_receipt(manifest)

    verified = verify_provider_history_v2(manifest)
    authenticated = authenticate_provider_history_v2(manifest, receipt=receipt)

    assert verified.dataset.dataset_sha256 == dataset.dataset_sha256
    assert authenticated.dataset.dataset_sha256 == dataset.dataset_sha256


def test_selected_part_is_hashed_and_unselected_parts_are_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_root = tmp_path / "provider"
    shutil.copytree(_FIXTURE_ROOT / "v2-prunable", provider_root)
    receipt = tmp_path / "provider-history-v2-receipt.json"
    shutil.copy2(_FIXTURE_ROOT / "v2-prunable-receipt.json", receipt)
    manifest = provider_root / "manifest.json"
    document = json.loads(manifest.read_bytes())
    parts = document["parts"]
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 2, 2, tzinfo=UTC)
    selected = next(
        item
        for item in parts
        if datetime.fromisoformat(item["maximum_interval_end"].replace("Z", "+00:00")) >= start
        and datetime.fromisoformat(item["minimum_interval_start"].replace("Z", "+00:00")) < end
    )
    unselected = next(item for item in parts if item["path"] != selected["path"])
    unselected_path = manifest.parent / unselected["path"]
    original_read = provider_history_v2_runtime._read_bounded

    def guarded_read(path: Path, field: str) -> bytes:
        if path == unselected_path:
            raise AssertionError("unselected physical part read")
        return original_read(path, field)

    monkeypatch.setattr(provider_history_v2_runtime, "_read_bounded", guarded_read)
    authenticated = authenticate_provider_history_v2(
        manifest,
        receipt=receipt,
        instrument_ids=("fx:aud-usd",),
        interval_start=start,
        interval_end=end,
    )
    assert authenticated.selection is not None
    assert len(authenticated.selection.selected_part_sha256) == 1

    selected_path = manifest.parent / selected["path"]
    selected_path.write_bytes(selected_path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="physical part bytes changed"):
        authenticate_provider_history_v2(
            manifest,
            receipt=receipt,
            instrument_ids=("fx:aud-usd",),
            interval_start=start,
            interval_end=end,
        )


def test_deep_verifier_rejects_exact_tree_changes(tmp_path: Path) -> None:
    orphan_root = tmp_path / "orphan"
    shutil.copytree(_FIXTURE_ROOT / "v2", orphan_root)
    orphan_manifest = orphan_root / "manifest.json"
    (orphan_root / "orphan").write_bytes(b"orphan")
    with pytest.raises(ValueError, match="tree differs"):
        verify_provider_history_v2(orphan_manifest)

    missing_root = tmp_path / "missing"
    shutil.copytree(_FIXTURE_ROOT / "v2", missing_root)
    missing_manifest = missing_root / "manifest.json"
    missing_document = json.loads(missing_manifest.read_bytes())
    (missing_root / missing_document["parts"][0]["path"]).unlink()
    with pytest.raises(FileNotFoundError, match="not a regular file"):
        verify_provider_history_v2(missing_manifest)


def test_deep_verifier_rejects_rehashed_physical_and_semantic_mutations(
    tmp_path: Path,
) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
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
        verify_provider_history_v2(manifest)

    document = json.loads(original_manifest)
    document["parts"][0]["row_count"] += 1
    publish_document(document)
    with pytest.raises(ValueError, match="row count changed"):
        verify_provider_history_v2(manifest)

    empty = io.BytesIO()
    pl.read_parquet(io.BytesIO(original_part)).slice(0, 0).write_parquet(empty)
    empty_payload = empty.getvalue()
    part.write_bytes(empty_payload)
    document = json.loads(original_manifest)
    document["parts"][0]["bytes_sha256"] = hashlib.sha256(empty_payload).hexdigest()
    publish_document(document)
    with pytest.raises(ValueError, match="row count changed"):
        verify_provider_history_v2(manifest)

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
    frame = pl.read_parquet(io.BytesIO(original_part)).with_row_index("_row")
    frame = frame.with_columns(
        *(
            pl.when(pl.col("_row") == 0)
            .then(pl.lit(str(getattr(rows[0], field))))
            .otherwise(pl.col(field))
            .alias(field)
            for field in ("open", "high", "low", "close")
        ),
        pl.when(pl.col("_row") == 0)
        .then(pl.lit(rows[0].observation_sha256))
        .otherwise(pl.col("observation_sha256"))
        .alias("observation_sha256"),
    ).drop("_row")
    changed_buffer = io.BytesIO()
    frame.write_parquet(changed_buffer)
    changed_payload = changed_buffer.getvalue()
    part.write_bytes(changed_payload)
    document = json.loads(original_manifest)
    document["parts"][0]["bytes_sha256"] = hashlib.sha256(changed_payload).hexdigest()
    document["parts"][0]["ordered_row_sha256"] = provider_history_v2_runtime.sha256_json(
        {"observation_sha256": [row.observation_sha256 for row in rows]}
    )
    publish_document(document)
    with pytest.raises(ValueError, match="semantic dataset identity changed"):
        verify_provider_history_v2(manifest)


def test_fixture_is_forward_only_v2(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="forward-only v2"):
        _published_provider_history(tmp_path, artifact=object())

    assert (_FIXTURE_ROOT / "v1" / "manifest.json").is_file()
