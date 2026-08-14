from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from qtrad.runtime import ibkr_historical_migration as migration


def _paths(tmp_path: Path, destination: Path | None = None) -> migration.MigrationPaths:
    source = tmp_path / "retained"
    source.mkdir(parents=True)
    files = {
        "stage6-manifest.json": b"{}\n",
        "stage7-manifest.json": b"{}\n",
        "stage7-receipt.json": b"{}\n",
        "stage8-foundation.json": b"{}\n",
        "stage8-receipt.json": b"{}\n",
        "promotion.json": b"{}\n",
    }
    paths = {name: source / name for name in files}
    for name, path in paths.items():
        path.write_bytes(files[name])
    destination_parent = tmp_path / "remediation"
    destination_parent.mkdir(parents=True)
    return migration.MigrationPaths(
        source_stage6_manifest=paths["stage6-manifest.json"],
        source_stage7_manifest=paths["stage7-manifest.json"],
        source_stage7_receipt=paths["stage7-receipt.json"],
        source_stage8_foundation=paths["stage8-foundation.json"],
        source_stage8_receipt=paths["stage8-receipt.json"],
        source_promotion=paths["promotion.json"],
        destination_root=destination or destination_parent / "attempt-1",
    )


def test_migration_paths_are_explicit_create_only_outputs(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert paths.output_paths() == (
        paths.destination_root,
        paths.destination_root / "stage6-result-v3",
        paths.destination_root / "stage6-result-v3-verification-receipt.json",
        paths.destination_root / "provider-history-v3",
        paths.destination_root / "provider-history-v3-verification-receipt.json",
        paths.destination_root / "foundation-v3.json",
        paths.destination_root / "foundation-v3.json.children",
        paths.destination_root / "foundation-v3-verification-receipt.json",
        paths.destination_root / "foundation-v3-confirmatory-promotion.json",
        paths.destination_root / "migration-equivalence-record.json",
    )


def test_destination_preflight_rejects_existing_root_without_writing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    retained = (
        paths.source_stage6_manifest,
        paths.source_stage7_manifest,
        paths.source_stage7_receipt,
        paths.source_stage8_foundation,
        paths.source_stage8_receipt,
        paths.source_promotion,
    )
    before = {path: path.read_bytes() for path in retained}
    paths.destination_root.mkdir()
    with pytest.raises(FileExistsError, match="destination root"):
        migration._preflight_destination(paths)
    assert tuple(paths.destination_root.iterdir()) == ()
    assert {path: path.read_bytes() for path in retained} == before


def test_destination_preflight_rejects_traversal_and_symlink_ancestors(
    tmp_path: Path,
) -> None:
    traversal = _paths(tmp_path / "traversal", tmp_path / "safe" / ".." / "attempt")
    with pytest.raises(ValueError, match="canonical"):
        migration._preflight_destination(traversal)

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    symlinked = _paths(tmp_path / "symlink", linked / "attempt")
    with pytest.raises(ValueError, match="symlink"):
        migration._preflight_destination(symlinked)
    assert tuple(outside.iterdir()) == ()


def test_create_only_record_writer_rejects_reuse(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    migration._write_create_only(output, b"first")
    with pytest.raises(FileExistsError):
        migration._write_create_only(output, b"second")
    assert output.read_bytes() == b"first"


def test_promotion_authorisation_requires_explicit_utc_operator() -> None:
    with pytest.raises(ValueError, match="operator"):
        migration.PromotionAuthorisation(
            authorized_by=" ",
            authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
            authorization_reference="approval",
        )
    with pytest.raises(ValueError, match="UTC"):
        migration.PromotionAuthorisation(
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 14),
            authorization_reference="approval",
        )


@dataclass(frozen=True)
class _Policy:
    name: str = "policy"

    def as_json_value(self) -> dict[str, str]:
        return {"name": self.name}


@dataclass(frozen=True)
class _Observation:
    close: str
    observation_sha256: str

    def as_json_value(self) -> dict[str, str]:
        return {
            "instrument_id": "fx:aud-usd",
            "close": self.close,
            "observation_sha256": self.observation_sha256,
        }


def _stage7_evidence(
    *, close: str = "1.1000", observation_sha256: str = "a" * 64
) -> SimpleNamespace:
    summary = SimpleNamespace(
        accepted_intervals_by_request=(
            (
                "r" * 64,
                ((datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)),),
            ),
        ),
        source_start=datetime(2026, 1, 1, tzinfo=UTC),
        source_end=datetime(2026, 1, 2, tzinfo=UTC),
    )
    dataset = SimpleNamespace(
        row_count=1,
        availability_policy=_Policy(),
    )
    evidence = SimpleNamespace(
        request_sha256="r" * 64,
        evidence_disposition="ACCEPTED",
        accepted_row_count=1,
        sessions=({"session": "closed"},),
    )
    return SimpleNamespace(
        dataset=dataset,
        observations=(_Observation(close, observation_sha256),),
        observation_summary=summary,
        request_evidence=(evidence,),
    )


def test_stage7_equivalence_excludes_physical_observation_identity() -> None:
    old = _stage7_evidence(observation_sha256="a" * 64)
    new = _stage7_evidence(observation_sha256="b" * 64)
    result = migration._compare_stage7(cast(Any, old), cast(Any, new))
    assert result["row_count"] == 1
    assert result["old_semantic_projection_sha256"] == result["new_semantic_projection_sha256"]


def test_stage7_equivalence_rejects_semantic_observation_change() -> None:
    with pytest.raises(ValueError, match="observation semantics"):
        migration._compare_stage7(
            cast(Any, _stage7_evidence(close="1.1000")),
            cast(Any, _stage7_evidence(close="1.1001")),
        )


def test_stage8_child_projection_excludes_physical_fields() -> None:
    projected = migration._project_child_mapping(
        {
            "semantic_value": "kept",
            "file": "foundation-v3.json.children/observations.json",
            "file_sha256": "f" * 64,
            "rows_sha256": "r" * 64,
            "lineage": {"closure_id": "c" * 64},
        }
    )
    assert projected == {"semantic_value": "kept"}


def test_work_counts_are_deterministic_and_round_trip() -> None:
    counts = migration.MigrationWorkCounts(
        old_stage6_request_children=2,
        new_stage6_request_children=2,
        old_stage7_parts_read=3,
        new_stage7_parts_read=3,
        old_stage7_rows_decoded=4,
        new_stage7_rows_decoded=4,
        old_stage8_child_rows_read=8,
        new_stage8_child_rows_read=8,
        stage6_semantic_replays=1,
        stage7_semantic_replays=1,
        stage8_semantic_replays=1,
        promotion_semantic_replays=0,
    )
    record = migration.MigrationResult(
        record_path=Path("migration-equivalence-record.json"),
        record={"work_counts": counts.as_json_value()},
    )
    assert record.work_counts == counts
    assert record.work_counts.as_json_value() == {
        "old_stage6_request_children": 2,
        "new_stage6_request_children": 2,
        "old_stage7_parts_read": 3,
        "new_stage7_parts_read": 3,
        "old_stage7_rows_decoded": 4,
        "new_stage7_rows_decoded": 4,
        "old_stage8_child_rows_read": 8,
        "new_stage8_child_rows_read": 8,
        "stage6_semantic_replays": 1,
        "stage7_semantic_replays": 1,
        "stage8_semantic_replays": 1,
        "promotion_semantic_replays": 0,
    }


def test_migration_plan_json_names_implementation_and_source_authority(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    plan = migration.MigrationPlan(
        implementation_commit="670e04e",
        paths=paths,
        source_stage6_result_id="r" * 64,
        source_stage6_closure_id="c" * 64,
        source_stage6_request_count=2,
        source_stage7_dataset_id="d" * 64,
        source_stage7_manifest_sha256="m" * 64,
        source_stage7_part_count=3,
        source_stage8_build_id="f" * 64,
        source_stage8_manifest_sha256="s" * 64,
        source_promotion_id="p" * 64,
    )
    value = plan.as_json_value()
    assert value["implementation_commit"] == "670e04e"
    source = cast(dict[str, object], value["source"])
    outputs = cast(list[str], value["outputs"])
    assert source["stage6_result_id"] == "r" * 64
    assert str(paths.stage6_receipt) in outputs
    assert str(paths.record) in outputs


def test_json_identity_digest_requires_an_object() -> None:
    with pytest.raises(TypeError, match="JSON object"):
        migration._digest_json(["not", "an", "identity"])


def test_normal_cli_has_no_retained_migration_entrypoint() -> None:
    import qtrad.__main__ as cli
    from qtrad.__main__ import build_parser

    assert not hasattr(cli, "migrate_retained_ibkr_evidence")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["research", "ibkr", "migrate-retained"])
