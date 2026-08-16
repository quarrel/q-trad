"""Bounded R2 JSON-row persistence tests."""

from __future__ import annotations

import os
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

import qtrad.runtime.r2_partitioned_rows as partitioned_runtime
from qtrad.runtime.r2_bundles import atomic_create, canonical_bytes
from qtrad.runtime.r2_partitioned_rows import (
    load_partitioned_rows,
    partitioned_manifest_part_paths,
    write_partitioned_rows,
)


def _header() -> dict[str, object]:
    return {
        "contract": "qtrad-r2-test-row-dataset-v1",
        "schema_version": 1,
        "dataset_id": sha256(b"partitioned-test-dataset").hexdigest(),
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "evidence_class": "IMPLEMENTATION",
    }


def _write(
    root: Path,
    rows: tuple[Mapping[str, object], ...],
) -> tuple[dict[str, object], Path]:
    payload = write_partitioned_rows(
        root,
        "rows.json",
        header=_header(),
        identity_field="dataset_id",
        rows=rows,
        expected_row_count=len(rows),
    )
    path = root / "rows.json"
    atomic_create(path, canonical_bytes(payload))
    return payload, path


def test_partitioned_rows_round_trip_is_deterministic_and_reads_each_part_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = tuple({"index": index, "value": f"row-{index}"} for index in range(11))
    first_payload, _first_path = _write(tmp_path / "first", rows)
    second_payload, _second_path = _write(tmp_path / "second", rows)

    assert first_payload == second_payload
    first_paths = partitioned_manifest_part_paths(tmp_path / "first", "rows.json", first_payload)
    second_paths = partitioned_manifest_part_paths(tmp_path / "second", "rows.json", second_payload)
    assert [
        (tmp_path / "first" / path).read_bytes() for path in first_paths
    ] == [(tmp_path / "second" / path).read_bytes() for path in second_paths]

    reads: dict[Path, int] = {}
    original_read_bytes = Path.read_bytes

    def count_reads(path: Path) -> bytes:
        reads[path] = reads.get(path, 0) + 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_reads)
    assert load_partitioned_rows(tmp_path / "first", "rows.json", first_payload) == rows
    expected_paths = {tmp_path / "first" / path for path in first_paths}
    assert set(reads) == expected_paths
    assert all(count == 1 for count in reads.values())


def test_partitioned_rows_split_at_encoded_bound_and_reject_oversized_singleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = tuple({"index": index, "value": "x" * 300} for index in range(4))
    monkeypatch.setattr(partitioned_runtime, "_MAX_PART_BYTES", 900)

    payload, _path = _write(tmp_path / "split", rows)

    references = cast(list[dict[str, object]], payload["parts"])
    assert len(references) > 1
    assert all(
        (tmp_path / "split" / cast(str, reference["path"])).stat().st_size <= 900
        for reference in references
    )
    assert load_partitioned_rows(tmp_path / "split", "rows.json", payload) == rows

    monkeypatch.setattr(partitioned_runtime, "_MAX_PART_BYTES", 250)
    with pytest.raises(ValueError, match="single-row part exceeds"):
        _write(tmp_path / "oversized", ({"value": "x" * 300},))


def test_partitioned_rows_reject_tamper_missing_noncanonical_and_orphan(
    tmp_path: Path,
) -> None:
    rows = ({"index": 0}, {"index": 1})
    payload, _path = _write(tmp_path / "valid", rows)
    references = cast(list[dict[str, object]], payload["parts"])
    first_path = tmp_path / "valid" / cast(str, references[0]["path"])
    encoded = first_path.read_bytes()
    first_path.write_bytes(encoded + b"\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_partitioned_rows(tmp_path / "valid", "rows.json", payload)

    first_path.unlink()
    with pytest.raises(ValueError, match="missing or not regular"):
        partitioned_manifest_part_paths(tmp_path / "valid", "rows.json", payload)

    noncanonical_payload, _path = _write(tmp_path / "noncanonical", rows)
    noncanonical_refs = cast(list[dict[str, object]], noncanonical_payload["parts"])
    noncanonical_refs[0]["path"] = "rows.json.parts/part-weird.json"
    with pytest.raises(ValueError, match="not canonical"):
        partitioned_manifest_part_paths(
            tmp_path / "noncanonical", "rows.json", noncanonical_payload
        )

    orphan_payload, _path = _write(tmp_path / "orphan", rows)
    orphan = tmp_path / "orphan/rows.json.parts/orphan.json"
    orphan.write_bytes(canonical_bytes({"orphan": True}))
    with pytest.raises(ValueError, match="orphan"):
        partitioned_manifest_part_paths(tmp_path / "orphan", "rows.json", orphan_payload)


def test_partitioned_rows_reject_symlink_and_special_entries(tmp_path: Path) -> None:
    rows = ({"index": 0},)
    payload, _path = _write(tmp_path / "valid", rows)
    fifo = tmp_path / "valid/rows.json.parts/fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="special entry"):
        partitioned_manifest_part_paths(tmp_path / "valid", "rows.json", payload)

    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "target", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_partitioned_rows(
            alias,
            "rows.json",
            header=_header(),
            identity_field="dataset_id",
            rows=rows,
            expected_row_count=1,
        )


def test_partitioned_rows_clean_partial_parts_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = tuple({"index": index, "value": "x" * 300} for index in range(4))
    monkeypatch.setattr(partitioned_runtime, "_MAX_PART_BYTES", 900)
    original_atomic_create = partitioned_runtime.atomic_create
    calls = 0

    def fail_second(path: Path, encoded: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("fixture write failure")
        original_atomic_create(path, encoded)

    monkeypatch.setattr(partitioned_runtime, "atomic_create", fail_second)
    with pytest.raises(RuntimeError, match="fixture write failure"):
        write_partitioned_rows(
            tmp_path,
            "rows.json",
            header=_header(),
            identity_field="dataset_id",
            rows=rows,
            expected_row_count=len(rows),
        )

    assert not (tmp_path / "rows.json").exists()
    assert not (tmp_path / "rows.json.parts").exists()
