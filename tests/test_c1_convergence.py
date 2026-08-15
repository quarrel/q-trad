from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_provider_history_v3 import _stage6_closure, _stage8_configuration

C1_CALL_COUNT_MATRIX: dict[str, tuple[int | None, str]] = {
    "Stage 6 publish": (0, "0"),
    "Stage 6 verify": (None, "1"),
    "Stage 6 authenticate": (0, "0"),
    "Stage 7 build": (0, "transformation once"),
    "Stage 7 verify": (0, "1"),
    "Stage 7 authenticate": (0, "0"),
    "Stage 8 build": (0, "transformation once"),
    "Stage 8 verify": (0, "1"),
    "Stage 8 authenticate": (0, "0"),
    "Stage 8 promotion": (0, "0"),
    "R1 build": (0, "transformation once"),
    "R1 verify": (0, "1"),
    "R1 authenticate": (0, "0"),
    "R2 build": (0, "calculation once"),
    "R2 verify": (0, "1"),
    "R2 authenticate": (0, "0"),
    "F2 promotion": (0, "0"),
    "G1 verify": (0, "G1 only"),
    "G2 verify": (0, "G2 only"),
}


def test_c1_call_count_matrix_is_explicit_and_parent_replay_free() -> None:
    assert len(C1_CALL_COUNT_MATRIX) == 19
    assert all(parent in (0, None) for parent, _own in C1_CALL_COUNT_MATRIX.values())
    assert C1_CALL_COUNT_MATRIX["Stage 7 build"] == (0, "transformation once")
    assert C1_CALL_COUNT_MATRIX["Stage 8 verify"] == (0, "1")
    assert C1_CALL_COUNT_MATRIX["R2 build"] == (0, "calculation once")
    assert C1_CALL_COUNT_MATRIX["G1 verify"] == (0, "G1 only")
    assert C1_CALL_COUNT_MATRIX["G2 verify"] == (0, "G2 only")


def test_c1_stage6_stage7_stage8_counts(tmp_path: Path, monkeypatch: Any) -> None:
    import qtrad.runtime.ibkr_foundation as stage8
    import qtrad.runtime.ibkr_results as stage6
    import qtrad.runtime.provider_history_v3 as stage7

    stage6_manifest, stage6_receipt = _stage6_closure(tmp_path)
    stage6_replays = {"count": 0}
    original_stage6_replay = stage6.replay_ibkr_historical_aggregate_result

    def count_stage6_replay(*args: Any, **kwargs: Any) -> None:
        stage6_replays["count"] += 1
        return original_stage6_replay(*args, **kwargs)

    monkeypatch.setattr(stage6, "replay_ibkr_historical_aggregate_result", count_stage6_replay)
    stage6_verify_receipt = tmp_path / "stage6-verify-receipt.json"
    stage6.verify_ibkr_historical_result(stage6_manifest, receipt_output=stage6_verify_receipt)
    assert stage6_replays["count"] == 1
    stage6.authenticate_ibkr_historical_result(stage6_manifest, receipt=stage6_verify_receipt)
    assert stage6_replays["count"] == 1

    stage7_derivations = {"count": 0}
    original_stage7_derive = stage7._derive_rows

    def count_stage7_derive(*args: Any, **kwargs: Any) -> Any:
        stage7_derivations["count"] += 1
        return original_stage7_derive(*args, **kwargs)

    monkeypatch.setattr(stage7, "_derive_rows", count_stage7_derive)
    stage7_manifest = stage7.build_provider_history(
        stage6_manifest,
        stage6_receipt=stage6_receipt,
        output=tmp_path / "stage7",
    )
    stage7_receipt = tmp_path / "stage7-receipt.json"
    stage7.verify_provider_history(
        stage7_manifest,
        stage6_manifest=stage6_manifest,
        stage6_receipt=stage6_receipt,
        receipt_output=stage7_receipt,
    )
    assert stage7_derivations["count"] == 2

    stage7_part_reads: list[Path] = []
    original_stage7_part_read = stage7._read_part

    def count_stage7_part_read(path: Path, reference: Any) -> Any:
        stage7_part_reads.append(path)
        return original_stage7_part_read(path, reference)

    monkeypatch.setattr(stage7, "_read_part", count_stage7_part_read)
    authenticated = stage7.authenticate_provider_history_v3(stage7_manifest, receipt=stage7_receipt)
    assert tuple(authenticated.observations)
    assert len(stage7_part_reads) == 1

    parent_replays = {"stage7": 0}

    def reject_stage7_replay(*args: Any, **kwargs: Any) -> Any:
        parent_replays["stage7"] += 1
        raise AssertionError("Stage 8 reopened Stage 7 semantic verification")

    monkeypatch.setattr(stage7, "verify_provider_history", reject_stage7_replay)
    stage8_builds = {"count": 0}
    original_stage8_build = stage8.build_ibkr_foundation

    def count_stage8_build(*args: Any, **kwargs: Any) -> Any:
        stage8_builds["count"] += 1
        return original_stage8_build(*args, **kwargs)

    monkeypatch.setattr(stage8, "build_ibkr_foundation", count_stage8_build)
    foundation = tmp_path / "foundation.json"
    stage8.write_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=_stage8_configuration(),
        workers=1,
    )
    foundation_receipt = tmp_path / "foundation-receipt.json"
    stage8.verify_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        receipt_output=foundation_receipt,
        workers=1,
    )
    assert stage8_builds["count"] == 2
    assert parent_replays == {"stage7": 0}
    stage8.authenticate_ibkr_foundation(foundation, receipt=foundation_receipt)
    assert stage8_builds["count"] == 2
