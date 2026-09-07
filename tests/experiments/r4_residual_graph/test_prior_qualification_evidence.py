"""Tests for authenticated retained qualification inventory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import experiments.r4_residual_graph.prior_qualification_evidence as evidence
from experiments.r4_residual_graph.attempt_artifacts import sha256_bytes


def _roots(tmp_path: Path) -> tuple[tuple[str, Path, bool], ...]:
    present_a = tmp_path / "generation-1"
    present_a.mkdir()
    (present_a / "a.json").write_bytes(b"a" * 42)
    nested = present_a / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(b"b" * 58)
    present_b = tmp_path / "generation-2"
    present_b.mkdir()
    (present_b / "cache.npy").write_bytes(b"c" * 4_113_012)
    return (
        ("generation-1-benchmark", present_a, True),
        ("generation-1-cache", tmp_path / "generation-1-cache", False),
        ("generation-2-cache", present_b, True),
    )


def test_prior_inventory_derives_bytes_and_rejects_file_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _roots(tmp_path)
    monkeypatch.setattr(evidence, "_PRIOR_ROOTS", roots)
    output = tmp_path / "output"
    output.mkdir()
    receipt = evidence.create_prior_qualification_evidence(output, candidate_identity="candidate")
    payload = json.loads(receipt.read_text())
    assert payload["prior_qualification_bytes"] == 4_113_112
    assert (
        evidence.authenticate_prior_qualification_evidence(payload, candidate_identity="candidate")
        == 4_113_112
    )
    assert [root["root"] for root in payload["roots"]] == [
        str(specification[1]) for specification in roots
    ]
    assert all(root["root_identity"] for root in payload["roots"])
    seal = json.loads((output / "prior-qualification-evidence-seal.json").read_text())
    assert seal["evidence_sha256"] == sha256_bytes(receipt.read_bytes())

    tracked = roots[0][1] / "a.json"
    tracked.write_bytes(b"z" * 42)
    with pytest.raises(ValueError, match="inventory drift"):
        evidence.authenticate_prior_qualification_evidence(payload, candidate_identity="candidate")
    tracked.write_bytes(b"a" * 42)

    added = roots[0][1] / "added"
    added.write_bytes(b"new")
    with pytest.raises(ValueError, match="inventory drift"):
        evidence.authenticate_prior_qualification_evidence(payload, candidate_identity="candidate")
    added.unlink()

    removed = roots[0][1] / "nested/b.bin"
    held = tmp_path / "held"
    removed.rename(held)
    with pytest.raises(ValueError, match="inventory drift"):
        evidence.authenticate_prior_qualification_evidence(payload, candidate_identity="candidate")
    held.rename(removed)

    symlink = roots[0][1] / "link"
    symlink.symlink_to(tracked)
    with pytest.raises(ValueError, match="non-regular"):
        evidence.authenticate_prior_qualification_evidence(payload, candidate_identity="candidate")
