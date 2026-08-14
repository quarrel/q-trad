from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

import qtrad.runtime.provider_history_v2 as provider_history_v2_runtime
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


def test_fixture_is_forward_only_v2(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="forward-only v2"):
        _published_provider_history(tmp_path, artifact=object())

    assert (_FIXTURE_ROOT / "v1" / "manifest.json").is_file()
