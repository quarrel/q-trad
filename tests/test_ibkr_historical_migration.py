from __future__ import annotations

import json
from dataclasses import dataclass, replace
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


def test_attempt3_root_name_matches_immutable_packet_literal() -> None:
    assert migration._ATTEMPT3_ROOT_NAME == "r2-simplification-h4-670e04e-attempt3"


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
    schedule_evidence: dict[str, object] | None = None
    gap_disposition: str = "SUCCEEDED"

    def as_json_value(self) -> dict[str, object]:
        schedule = (
            self.schedule_evidence
            if self.schedule_evidence is not None
            else {
                "request_sha256": ["r" * 64],
                "result_sha256": ["s" * 64],
                "schedule_state": "ACTIVE",
                "sessions": [{"active": True}],
            }
        )
        return {
            "instrument_id": "fx:aud-usd",
            "close": self.close,
            "observation_sha256": self.observation_sha256,
            "schedule_evidence": schedule,
            "gap_disposition": self.gap_disposition,
        }


def _stage7_evidence(
    *,
    close: str = "1.1000",
    observation_sha256: str = "a" * 64,
    schedule_evidence: dict[str, object] | None = None,
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
        result_sha256="s" * 64,
        evidence_disposition="ACCEPTED",
        accepted_row_count=1,
        sessions=({"active": True},),
    )
    return SimpleNamespace(
        dataset=dataset,
        observations=(_Observation(close, observation_sha256, schedule_evidence),),
        observation_summary=summary,
        request_evidence=(evidence,),
    )


def _stage8_readiness(*, lineage: str) -> dict[str, object]:
    evidence: dict[str, object] = {
        "provider_row_count": 10,
        "provider_gap_count": 0,
        "total_provider_gap_count": 0,
        "raw_provider_gaps": [],
        "coverage_cells": [{"instrument_id": "fx:aud-usd", "passed": True}],
        "coverage_threshold": 1.0,
        "blocking_coverage_cells": [],
        "coverage_diagnostics": {},
        "target_row_count": 10,
        "fold_count": 1,
        "primary_horizon_seconds": 60,
        "request_evidence": {},
        "source_coverage_summary": {},
        "source_entitlement_summary": {},
        "source_contract_selection_sha256": "c" * 64,
        "source_plan_sha256": "p" * 64,
        "source_runtime_sha256": "t" * 64,
    }
    if lineage == "v2":
        evidence["source_aggregate_sha256"] = "a" * 64
    else:
        evidence.update(
            {
                "source_result_id": "r" * 64,
                "source_closure_id": "l" * 64,
                "source_verification_id": "v" * 64,
            }
        )
    candidates = [
        "fx:aud-usd",
        "fx:eur-usd",
        "index:australia-200",
        "index:us-500",
        "commodity:spot-gold",
        "commodity:us-crude",
    ]
    return {
        "contract": "qtrad-ibkr-historical-foundation-v1",
        "schema_version": 1,
        "state": "INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION",
        "causes": ["INSUFFICIENT_ROWS"],
        "candidate_instruments": candidates,
        "groups": ["FX", "indices", "commodities"],
        "common_support_start": "2026-01-01T00:00:00+00:00",
        "common_support_end": "2026-01-02T00:00:00+00:00",
        "common_support_rows": 10,
        "rows_by_candidate": dict.fromkeys(candidates, 10),
        "evidence": evidence,
    }


def test_stage8_readiness_lineage_change_is_not_semantic() -> None:
    old = _stage8_readiness(lineage="v2")
    new = _stage8_readiness(lineage="v3")
    old_projection, new_projection, authority = migration._compare_stage8_readiness(old, new)
    assert old_projection == new_projection
    assert authority == {
        "old": {
            "source_aggregate_sha256": "a" * 64,
            "source_contract_selection_sha256": "c" * 64,
            "source_plan_sha256": "p" * 64,
            "source_runtime_sha256": "t" * 64,
        },
        "new": {
            "source_closure_id": "l" * 64,
            "source_contract_selection_sha256": "c" * 64,
            "source_plan_sha256": "p" * 64,
            "source_result_id": "r" * 64,
            "source_runtime_sha256": "t" * 64,
            "source_verification_id": "v" * 64,
        },
    }


@pytest.mark.parametrize("mutation", ["state", "causes", "support", "coverage"])
def test_stage8_readiness_semantic_mutation_is_rejected(mutation: str) -> None:
    old = _stage8_readiness(lineage="v2")
    new = _stage8_readiness(lineage="v3")
    if mutation == "state":
        new["state"] = "QUALIFYING_HISTORY_READY"
    elif mutation == "causes":
        new["causes"] = []
    elif mutation == "support":
        new["common_support_rows"] = 11
    else:
        evidence = cast(dict[str, object], new["evidence"])
        evidence["coverage_cells"] = [{"instrument_id": "fx:aud-usd", "passed": False}]
    with pytest.raises(ValueError, match="readiness semantics"):
        migration._compare_stage8_readiness(old, new)


def test_stage8_readiness_projection_requires_exact_schema() -> None:
    unknown = _stage8_readiness(lineage="v2")
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected schema"):
        migration._stage8_readiness_semantic_projection(unknown, "readiness")
    missing = _stage8_readiness(lineage="v2")
    del missing["state"]
    with pytest.raises(ValueError, match="unexpected schema"):
        migration._stage8_readiness_semantic_projection(missing, "readiness")


def test_stage7_equivalence_excludes_physical_observation_identity() -> None:
    old = _stage7_evidence(observation_sha256="a" * 64)
    old = SimpleNamespace(
        **{
            **vars(old),
            "observations": (replace(old.observations[0], gap_disposition="BAR_ACCEPTED"),),
        }
    )
    new = _stage7_evidence(observation_sha256="b" * 64, schedule_evidence={})
    result = migration._compare_stage7(cast(Any, old), cast(Any, new))
    assert result["row_count"] == 1
    assert result["old_semantic_projection_sha256"] == result["new_semantic_projection_sha256"]


def test_stage7_equivalence_rejects_semantic_observation_change() -> None:
    old = _stage7_evidence(close="1.1000")
    old = SimpleNamespace(
        **{
            **vars(old),
            "observations": (replace(old.observations[0], gap_disposition="BAR_ACCEPTED"),),
        }
    )
    with pytest.raises(ValueError, match="observation semantics"):
        migration._compare_stage7(
            cast(Any, old),
            cast(Any, _stage7_evidence(close="1.1001", schedule_evidence={})),
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
        old_stage6_semantic_replays=1,
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
        "old_stage6_semantic_replays": 1,
        "stage6_semantic_replays": 1,
        "stage7_semantic_replays": 1,
        "stage8_semantic_replays": 1,
        "promotion_semantic_replays": 0,
    }


def test_migration_plan_json_names_implementation_and_source_authority(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    plan = migration.MigrationPlan(
        implementation_commit="a" * 40,
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
        capacity_required_bytes=1024,
        capacity_available_bytes=2048,
    )
    value = plan.as_json_value()
    assert value["implementation_commit"] == "a" * 40
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


def test_disposable_migration_orchestrator_uses_each_boundary_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    plan = migration.MigrationPlan(
        implementation_commit="a" * 40,
        paths=paths,
        source_stage6_result_id="r" * 64,
        source_stage6_closure_id="c" * 64,
        source_stage6_request_count=1,
        source_stage7_dataset_id="d" * 64,
        source_stage7_manifest_sha256="m" * 64,
        source_stage7_part_count=1,
        source_stage8_build_id="f" * 64,
        source_stage8_manifest_sha256="s" * 64,
        source_promotion_id="p" * 64,
        capacity_required_bytes=1024,
        capacity_available_bytes=2048,
    )
    calls: list[str] = []

    class _Stream:
        plan = SimpleNamespace()
        plan_bytes = b"plan"
        aggregate = SimpleNamespace(
            result_id="r" * 64,
            closure_id="c" * 64,
            request_results=(object(),),
        )

        def iter_request_results(self) -> tuple[object, ...]:
            calls.append("stage6-child-read")
            return (object(),)

    fake_old_stage7_manifest = SimpleNamespace(
        dataset=SimpleNamespace(dataset_sha256="d" * 64),
        document={"physical_manifest_sha256": "m" * 64},
        parts=(object(),),
    )
    fake_old_stage7 = _stage7_evidence()
    fake_old_stage7.dataset.partitions = (object(),)
    fake_new_stage7 = _stage7_evidence()
    fake_new_stage7.dataset.partitions = (object(),)

    def fake_read_json(_path: Path, field: str) -> dict[str, object]:
        values = {
            "retained Stage 8 foundation": {"build_sha256": "f" * 64},
            "retained Stage 8 receipt": {"receipt_sha256": "v" * 64},
            "retained promotion": {"promotion_sha256": "p" * 64},
            "old Stage 7 receipt": {"receipt_sha256": "v" * 64},
            "new Stage 6 manifest": {"result_id": "r" * 64, "closure_id": "c" * 64},
            "new Stage 6 receipt": {"verification_id": "v" * 64},
            "new Stage 7 manifest": {
                "closure_id": "c" * 64,
                "dataset": {"dataset_sha256": "d" * 64},
            },
            "new Stage 7 receipt": {"verification_id": "v" * 64},
            "new Stage 8 foundation": {
                "foundation_id": "f" * 64,
                "closure_id": "g" * 64,
            },
            "new Stage 8 receipt": {"verification_id": "v" * 64},
            "new promotion": {"promotion_sha256": "p" * 64},
        }
        return cast(dict[str, object], values[field])

    real_read_json = migration._read_json
    monkeypatch.setattr(migration, "plan_retained_ibkr_migration", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(migration, "_read_json", fake_read_json)
    monkeypatch.setattr(migration, "_file_sha256", lambda *_args, **_kwargs: "x" * 64)
    monkeypatch.setattr(
        migration,
        "_authenticate_ibkr_foundation_migration_v2",
        lambda *_args, **_kwargs: {"authenticated": True},
    )
    monkeypatch.setattr(
        migration,
        "_authenticate_ibkr_foundation_promotion_migration_v2",
        lambda *_args, **_kwargs: {"authenticated": True},
    )
    monkeypatch.setattr(
        migration,
        "authenticate_provider_history_v2",
        lambda *_args, **_kwargs: fake_old_stage7,
    )
    monkeypatch.setattr(
        migration,
        "_read_provider_history_v2_manifest",
        lambda *_args, **_kwargs: fake_old_stage7_manifest,
    )
    monkeypatch.setattr(
        migration,
        "_authenticate_foundation_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(configuration=object()),
    )
    monkeypatch.setattr(
        migration,
        "_read_legacy_ibkr_historical_result_v2_header",
        lambda *_args, **_kwargs: _Stream(),
    )
    monkeypatch.setattr(
        migration,
        "_replay_legacy_stage6",
        lambda *_args, **_kwargs: calls.append("stage6-old-replay") or {},
    )
    monkeypatch.setattr(
        migration,
        "build_ibkr_historical_aggregate_result",
        lambda *_args, **_kwargs: calls.append("stage6-build") or object(),
    )
    monkeypatch.setattr(migration, "IbkrHistoricalResultArtifact", lambda **_kwargs: object())
    monkeypatch.setattr(
        migration,
        "publish_ibkr_historical_result",
        lambda *_args, **_kwargs: calls.append("stage6-publish") or paths.stage6_manifest,
    )
    monkeypatch.setattr(
        migration,
        "verify_ibkr_historical_result",
        lambda *_args, **_kwargs: (
            calls.append("stage6-verify") or SimpleNamespace(request_results=(object(),))
        ),
    )
    monkeypatch.setattr(
        migration,
        "build_provider_history",
        lambda *_args, **_kwargs: calls.append("stage7-build") or paths.stage7_manifest,
    )
    monkeypatch.setattr(
        migration,
        "verify_provider_history",
        lambda *_args, **_kwargs: calls.append("stage7-verify") or fake_new_stage7,
    )
    monkeypatch.setattr(
        migration,
        "_compare_stage7",
        lambda *_args, **_kwargs: (
            calls.append("stage7-compare")
            or {
                "schedule_evidence_relocation": {
                    "contract": "fixture",
                    "equivalent": True,
                }
            }
        ),
    )
    monkeypatch.setattr(
        migration,
        "write_ibkr_foundation",
        lambda *_args, **_kwargs: calls.append("stage8-build"),
    )
    monkeypatch.setattr(
        migration,
        "verify_ibkr_foundation",
        lambda *_args, **_kwargs: calls.append("stage8-verify") or object(),
    )
    monkeypatch.setattr(
        migration,
        "_compare_stage8",
        lambda *_args, **_kwargs: (
            calls.append("stage8-compare")
            or (
                {
                    "readiness_authority": {
                        "old": {"source_aggregate_sha256": "a" * 64},
                        "new": {
                            "source_result_id": "r" * 64,
                            "source_closure_id": "c" * 64,
                            "source_verification_id": "v" * 64,
                        },
                    }
                },
                1,
                1,
            )
        ),
    )
    monkeypatch.setattr(
        migration,
        "create_ibkr_foundation_confirmatory_promotion",
        lambda *_args, **_kwargs: calls.append("promotion") or object(),
    )

    source_bytes = paths.source_stage6_manifest.read_bytes()
    result = migration.migrate_retained_ibkr_evidence(
        paths,
        implementation_commit="a" * 40,
        promotion_authorisation=migration.PromotionAuthorisation(
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
            authorization_reference="fixture",
        ),
    )

    assert calls == [
        "stage6-child-read",
        "stage6-old-replay",
        "stage6-build",
        "stage6-publish",
        "stage6-verify",
        "stage7-build",
        "stage7-verify",
        "stage7-compare",
        "stage8-build",
        "stage8-verify",
        "stage8-compare",
        "promotion",
    ]
    assert result.work_counts.old_stage6_semantic_replays == 1
    assert result.work_counts.stage6_semantic_replays == 1
    assert result.work_counts.stage7_semantic_replays == 1
    assert result.work_counts.stage8_semantic_replays == 1
    assert result.work_counts.promotion_semantic_replays == 0
    assert result.work_counts.old_stage6_request_children == 1
    assert result.work_counts.new_stage6_request_children == 1
    assert result.record_path == paths.record
    assert paths.record.is_file()
    persisted = json.loads(paths.record.read_text(encoding="utf-8"))
    assert persisted["work_counts"] == result.record["work_counts"]
    classification = cast(dict[str, object], persisted["identity_classification"])
    stage7_classification = cast(dict[str, object], classification["stage7"])
    assert stage7_classification["schedule_evidence_relocation"] == {
        "contract": "fixture",
        "equivalent": True,
    }
    stage8_classification = cast(dict[str, object], classification["stage8"])
    readiness_authority = cast(dict[str, object], stage8_classification["readiness_authority"])
    assert readiness_authority["old"] == {"source_aggregate_sha256": "a" * 64}
    assert readiness_authority["new"] == {
        "source_closure_id": "c" * 64,
        "source_result_id": "r" * 64,
        "source_verification_id": "v" * 64,
    }
    monkeypatch.setattr(migration, "_read_json", real_read_json)
    authenticated = migration.authenticate_migration_equivalence_record(paths.record)
    assert authenticated["operator_authorization"] == {
        "authorized_by": "operator",
        "authorized_at": "2026-08-14T00:00:00+00:00",
        "authorization_reference": "fixture",
    }
    mutated = json.loads(paths.record.read_text(encoding="utf-8"))
    mutated["operator_authorization"]["authorized_by"] = "different-operator"
    mutated_path = tmp_path / "mutated-record.json"
    mutated_path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="identity changed"):
        migration.authenticate_migration_equivalence_record(mutated_path)
    assert paths.source_stage6_manifest.read_bytes() == source_bytes


def test_destination_preflight_rejects_retained_closure_overlap(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    nested = replace(
        paths,
        destination_root=paths.source_stage6_manifest.parent / "nested-attempt",
    )
    with pytest.raises(ValueError, match="overlaps retained source"):
        migration._preflight_destination(nested)


def test_capacity_preflight_is_deterministic_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        migration.os,
        "statvfs",
        lambda _path: SimpleNamespace(f_bavail=1, f_frsize=1),
    )
    with pytest.raises(OSError, match="capacity"):
        migration._capacity_preflight(paths)
    monkeypatch.setattr(
        migration.os,
        "statvfs",
        lambda _path: SimpleNamespace(f_bavail=10_000_000, f_frsize=1),
    )
    required, available = migration._capacity_preflight(paths)
    assert required == 1_048_612
    assert available == 10_000_000


def test_old_stage6_replay_uses_existing_request_replay_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(request_sha256="r" * 64)
    result = SimpleNamespace(request_sha256="r" * 64)
    semantic = {
        "contract": "qtrad-ibkr-historical-result-v3",
        "schema_version": 3,
        "plan_semantic_id": "p" * 64,
        "request_result_semantic_ids": ["s" * 64],
        "coverage_summary": {},
        "entitlement_summary": {},
    }
    rebuilt = SimpleNamespace(
        CONTRACT="qtrad-ibkr-historical-result-v3",
        SCHEMA_VERSION=3,
        semantic_identity_payload=lambda: semantic,
    )
    stream = SimpleNamespace(
        plan=SimpleNamespace(requests=(request,)),
        plan_bytes=b"plan",
        aggregate=SimpleNamespace(
            plan=SimpleNamespace(semantic_sha256="p" * 64),
            request_results=(SimpleNamespace(semantic_sha256="s" * 64),),
            coverage_summary={},
            entitlement_summary={},
        ),
    )
    replayed: list[str] = []
    monkeypatch.setattr(
        migration,
        "replay_ibkr_historical_request_result",
        lambda _request, _result: replayed.append("request"),
    )
    monkeypatch.setattr(
        migration,
        "build_ibkr_historical_aggregate_result",
        lambda *_args, **_kwargs: rebuilt,
    )
    migration._replay_legacy_stage6(stream, cast(Any, (result,)))
    assert replayed == ["request"]
    mismatched = dict(semantic)
    mismatched["coverage_summary"] = {"changed": True}
    monkeypatch.setattr(
        migration,
        "build_ibkr_historical_aggregate_result",
        lambda *_args, **_kwargs: SimpleNamespace(semantic_identity_payload=lambda: mismatched),
    )
    with pytest.raises(ValueError, match="semantic replay is not equivalent"):
        migration._replay_legacy_stage6(stream, cast(Any, (result,)))


@pytest.mark.parametrize(
    "failure_phase",
    [
        "old-stage8-authentication",
        "old-stage7-authentication",
        "old-stage6-semantic-replay",
        "stage6-build",
        "stage7-verification",
    ],
)
def test_execution_failure_writes_create_only_failure_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    paths = _paths(tmp_path)
    plan = migration.MigrationPlan(
        implementation_commit="a" * 40,
        paths=paths,
        source_stage6_result_id="r" * 64,
        source_stage6_closure_id="c" * 64,
        source_stage6_request_count=1,
        source_stage7_dataset_id="d" * 64,
        source_stage7_manifest_sha256="m" * 64,
        source_stage7_part_count=1,
        source_stage8_build_id="f" * 64,
        source_stage8_manifest_sha256="s" * 64,
        source_promotion_id="p" * 64,
        capacity_required_bytes=1024,
        capacity_available_bytes=2048,
    )
    retained_before = {
        path: path.read_bytes()
        for path in (
            paths.source_stage6_manifest,
            paths.source_stage7_manifest,
            paths.source_stage7_receipt,
            paths.source_stage8_foundation,
            paths.source_stage8_receipt,
            paths.source_promotion,
        )
    }

    class _Stream:
        plan = SimpleNamespace()
        plan_bytes = b"plan"
        aggregate = SimpleNamespace(request_results=(object(),))

        def iter_request_results(self) -> tuple[object, ...]:
            return (object(),)

    fake_stage7 = _stage7_evidence()
    fake_stage7.dataset.partitions = (object(),)
    monkeypatch.setattr(migration, "plan_retained_ibkr_migration", lambda *_args, **_kwargs: plan)
    if failure_phase == "old-stage8-authentication":
        monkeypatch.setattr(
            migration,
            "_authenticate_ibkr_foundation_migration_v2",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("old-stage8-authentication failure")
            ),
        )
    else:
        monkeypatch.setattr(
            migration,
            "_authenticate_ibkr_foundation_migration_v2",
            lambda *_args, **_kwargs: {"authenticated": True},
        )
    monkeypatch.setattr(
        migration,
        "_authenticate_ibkr_foundation_promotion_migration_v2",
        lambda *_args, **_kwargs: {"authenticated": True},
    )
    if failure_phase == "old-stage7-authentication":
        monkeypatch.setattr(
            migration,
            "authenticate_provider_history_v2",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("old-stage7-authentication failure")
            ),
        )
    else:
        monkeypatch.setattr(
            migration,
            "authenticate_provider_history_v2",
            lambda *_args, **_kwargs: fake_stage7,
        )
    monkeypatch.setattr(
        migration,
        "_read_provider_history_v2_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(parts=(object(),)),
    )
    monkeypatch.setattr(
        migration,
        "_authenticate_foundation_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(configuration=object()),
    )
    monkeypatch.setattr(
        migration,
        "_read_legacy_ibkr_historical_result_v2_header",
        lambda *_args, **_kwargs: _Stream(),
    )
    if failure_phase == "old-stage6-semantic-replay":
        monkeypatch.setattr(
            migration,
            "_replay_legacy_stage6",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("old-stage6-semantic-replay failure")
            ),
        )
    else:
        monkeypatch.setattr(
            migration,
            "_replay_legacy_stage6",
            lambda *_args, **_kwargs: {},
        )
    if failure_phase == "stage6-build":
        monkeypatch.setattr(
            migration,
            "build_ibkr_historical_aggregate_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stage6-build failure")),
        )
    else:
        monkeypatch.setattr(
            migration,
            "build_ibkr_historical_aggregate_result",
            lambda *_args, **_kwargs: object(),
        )
        monkeypatch.setattr(migration, "IbkrHistoricalResultArtifact", lambda **_kwargs: object())
        monkeypatch.setattr(
            migration,
            "publish_ibkr_historical_result",
            lambda *_args, **_kwargs: paths.stage6_manifest,
        )
        monkeypatch.setattr(
            migration,
            "verify_ibkr_historical_result",
            lambda *_args, **_kwargs: SimpleNamespace(request_results=(object(),)),
        )
        monkeypatch.setattr(
            migration,
            "build_provider_history",
            lambda *_args, **_kwargs: paths.stage7_manifest,
        )
        if failure_phase == "stage7-verification":
            monkeypatch.setattr(
                migration,
                "verify_provider_history",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("stage7-verification failure")
                ),
            )
        else:
            monkeypatch.setattr(
                migration,
                "verify_provider_history",
                lambda *_args, **_kwargs: fake_stage7,
            )

    with pytest.raises(RuntimeError, match=failure_phase):
        migration.migrate_retained_ibkr_evidence(
            paths,
            implementation_commit="a" * 40,
            promotion_authorisation=migration.PromotionAuthorisation(
                authorized_by="operator",
                authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
                authorization_reference="failure-fixture",
            ),
        )
    assert paths.destination_root.is_dir()
    assert paths.failure_record.is_file()
    failure = json.loads(paths.failure_record.read_text(encoding="utf-8"))
    assert failure["kind"] == "failure"
    assert failure["phase"] == failure_phase
    assert failure["old_authority_untouched"] is True
    assert not paths.record.exists()
    failure_bytes = paths.failure_record.read_bytes()
    with pytest.raises(FileExistsError, match="destination root"):
        migration.migrate_retained_ibkr_evidence(
            paths,
            implementation_commit="a" * 40,
            promotion_authorisation=migration.PromotionAuthorisation(
                authorized_by="operator",
                authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
                authorization_reference="failure-fixture-reuse",
            ),
        )
    assert paths.failure_record.read_bytes() == failure_bytes
    assert {
        path: path.read_bytes()
        for path in (
            paths.source_stage6_manifest,
            paths.source_stage7_manifest,
            paths.source_stage7_receipt,
            paths.source_stage8_foundation,
            paths.source_stage8_receipt,
            paths.source_promotion,
        )
    } == retained_before


def test_implementation_commit_is_exact_and_matches_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration, "derive_qtrad_commit", lambda **_kwargs: "b" * 40)
    with pytest.raises(ValueError, match="40-character"):
        migration._require_implementation_commit("b" * 39)
    with pytest.raises(ValueError, match="does not match"):
        migration._require_implementation_commit("a" * 40)
    assert migration._require_implementation_commit("b" * 40) == "b" * 40


def test_destination_preflight_rejects_both_overlap_directions(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    source_root = paths.source_stage6_manifest.parent
    assert migration._paths_overlap(source_root, source_root / "nested")
    assert migration._paths_overlap(source_root / "nested", source_root)
    ancestor = replace(paths, destination_root=tmp_path)
    with pytest.raises(FileExistsError, match="destination root"):
        migration._preflight_destination(ancestor)


def test_migration_record_schema_and_contract_are_bound(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    document = {
        "contract": migration.MIGRATION_CONTRACT,
        "schema_version": migration.MIGRATION_SCHEMA_VERSION,
        "record_sha256": "0" * 64,
        "kind": "failure",
    }
    path.write_bytes(json.dumps(document).encode())
    with pytest.raises(ValueError, match="record contract"):
        bad_contract = dict(document)
        bad_contract["contract"] = "other"
        bad_contract["record_sha256"] = migration._digest_json(
            {key: value for key, value in bad_contract.items() if key != "record_sha256"}
        )
        path.write_bytes(json.dumps(bad_contract).encode())
        migration.authenticate_migration_equivalence_record(path)


def test_stage7_schedule_relocation_normalizes_only_legacy_disposition() -> None:
    old = _stage7_evidence()
    old_row = replace(old.observations[0], gap_disposition="BAR_ACCEPTED")
    old = SimpleNamespace(**{**vars(old), "observations": (old_row,)})
    result = migration._compare_stage7(
        cast(Any, old), cast(Any, _stage7_evidence(schedule_evidence={}))
    )
    relocation = cast(dict[str, object], result["schedule_evidence_relocation"])
    assert relocation["equivalent"] is True
    assert relocation["legacy_disposition_normalization"] == {"BAR_ACCEPTED": "SUCCEEDED"}


@pytest.mark.parametrize("mutation", ["request", "result", "sessions", "state", "disposition"])
def test_stage7_schedule_relocation_rejects_mutations(mutation: str) -> None:
    old = _stage7_evidence()
    schedule = cast(dict[str, object], old.observations[0].as_json_value()["schedule_evidence"])
    if mutation == "request":
        schedule["request_sha256"] = ["x" * 64]
    elif mutation == "result":
        schedule["result_sha256"] = ["x" * 64]
    elif mutation == "sessions":
        schedule["sessions"] = []
    elif mutation == "state":
        schedule["schedule_state"] = "INACTIVE"
    old_row = replace(
        old.observations[0],
        schedule_evidence=schedule,
        gap_disposition="MISSING" if mutation == "disposition" else "BAR_ACCEPTED",
    )
    mutated = SimpleNamespace(**{**vars(old), "observations": (old_row,)})
    with pytest.raises(ValueError):
        migration._compare_stage7(
            cast(Any, mutated), cast(Any, _stage7_evidence(schedule_evidence={}))
        )


def test_stage7_request_evidence_result_identity_is_compared() -> None:
    old = _stage7_evidence()
    new = _stage7_evidence(schedule_evidence={})
    new_evidence = SimpleNamespace(**{**vars(new.request_evidence[0]), "result_sha256": "x" * 64})
    new = SimpleNamespace(**{**vars(new), "request_evidence": (new_evidence,)})
    with pytest.raises(ValueError, match=r"request.*evidence"):
        migration._compare_stage7(cast(Any, old), cast(Any, new))


@pytest.mark.parametrize(
    "schedule_evidence",
    [
        {"schedule_state": "INACTIVE"},
        {
            "request_sha256": ["r" * 64],
            "result_sha256": ["s" * 64],
            "schedule_state": "ACTIVE",
            "sessions": [],
        },
    ],
)
def test_stage7_current_schedule_evidence_must_be_empty(
    schedule_evidence: dict[str, object],
) -> None:
    old = _stage7_evidence()
    old = SimpleNamespace(
        **{
            **vars(old),
            "observations": (replace(old.observations[0], gap_disposition="BAR_ACCEPTED"),),
        }
    )
    with pytest.raises(ValueError, match="new Stage 7 schedule evidence"):
        migration._compare_stage7(
            cast(Any, old),
            cast(Any, _stage7_evidence(schedule_evidence=schedule_evidence)),
        )


@pytest.mark.parametrize("mutation", ["empty", "duplicate-request", "duplicate-result"])
def test_stage7_schedule_reconstruction_rejects_identity_multiplicity(
    mutation: str,
) -> None:
    old = _stage7_evidence()
    schedule = cast(dict[str, object], old.observations[0].as_json_value()["schedule_evidence"])
    if mutation == "empty":
        schedule["request_sha256"] = []
        schedule["result_sha256"] = []
    elif mutation == "duplicate-request":
        schedule["request_sha256"] = ["r" * 64, "r" * 64]
        schedule["result_sha256"] = ["s" * 64, "s" * 64]
    else:
        schedule["request_sha256"] = ["r" * 64, "x" * 64]
        schedule["result_sha256"] = ["s" * 64, "s" * 64]
    old_row = replace(
        old.observations[0],
        schedule_evidence=schedule,
        gap_disposition="BAR_ACCEPTED",
    )
    old = SimpleNamespace(**{**vars(old), "observations": (old_row,)})
    with pytest.raises(ValueError, match="request/result identities"):
        migration._compare_stage7(
            cast(Any, old),
            cast(Any, _stage7_evidence(schedule_evidence={})),
        )


def test_stage7_request_evidence_reordering_is_equivalent() -> None:
    first = _stage7_evidence()
    second_evidence = SimpleNamespace(
        request_sha256="t" * 64,
        result_sha256="u" * 64,
        evidence_disposition="ACCEPTED",
        accepted_row_count=0,
        sessions=({"active": True},),
    )
    old = SimpleNamespace(
        **{
            **vars(first),
            "observations": (replace(first.observations[0], gap_disposition="BAR_ACCEPTED"),),
            "request_evidence": (first.request_evidence[0], second_evidence),
        }
    )
    new = SimpleNamespace(
        **{
            **vars(first),
            "observations": (replace(first.observations[0], schedule_evidence={}),),
            "request_evidence": (second_evidence, first.request_evidence[0]),
        }
    )
    result = migration._compare_stage7(cast(Any, old), cast(Any, new))
    assert result["request_evidence_sha256"] == migration._evidence_digest(
        (first.request_evidence[0], second_evidence)
    )


def test_stage7_request_evidence_duplicate_is_rejected() -> None:
    evidence = _stage7_evidence().request_evidence[0]
    with pytest.raises(ValueError, match="duplicate Stage 7 request evidence"):
        migration._evidence_digest((evidence, evidence))


@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_stage7_request_evidence_missing_or_changed_entry_is_rejected(mutation: str) -> None:
    first = _stage7_evidence()
    second = SimpleNamespace(
        request_sha256="t" * 64,
        result_sha256="u" * 64,
        evidence_disposition="ACCEPTED",
        accepted_row_count=0,
        sessions=({"active": True},),
    )
    retained_entries = (first.request_evidence[0], second)
    if mutation == "missing":
        current_entries = (first.request_evidence[0],)
    else:
        current_entries = (
            first.request_evidence[0],
            SimpleNamespace(**{**vars(second), "result_sha256": "v" * 64}),
        )
    old = SimpleNamespace(
        **{
            **vars(first),
            "observations": (replace(first.observations[0], gap_disposition="BAR_ACCEPTED"),),
            "request_evidence": retained_entries,
        }
    )
    new = SimpleNamespace(
        **{
            **vars(first),
            "observations": (replace(first.observations[0], schedule_evidence={}),),
            "request_evidence": current_entries,
        }
    )
    with pytest.raises(ValueError, match="request evidence"):
        migration._compare_stage7(cast(Any, old), cast(Any, new))


def _write_attempt3_failure_fixture(
    paths: migration.MigrationPaths,
    *,
    implementation_commit: str = "0" * 40,
    phase: str = "stage8-equivalence",
) -> None:
    paths.destination_root.mkdir()
    identity: dict[str, object] = {
        "contract": migration.MIGRATION_CONTRACT,
        "schema_version": migration.MIGRATION_SCHEMA_VERSION,
        "kind": "failure",
        "phase": phase,
        "implementation_commit": implementation_commit,
        "source": {
            "stage6_result_id": "a" * 64,
            "stage6_closure_id": "b" * 64,
            "stage7_dataset_id": "c" * 64,
            "stage7_manifest_sha256": "d" * 64,
            "stage8_build_id": "e" * 64,
            "stage8_manifest_sha256": "f" * 64,
            "promotion_id": "g" * 64,
        },
        "capacity": {"required_bytes": 1, "available_bytes": 2},
        "error": {"type": "ValueError", "message": "Stage 8 migration child kinds changed"},
        "outputs": migration._failure_output_snapshot(paths),
        "old_authority_untouched": True,
    }
    record = {**identity, "record_sha256": migration._digest_json(identity)}
    paths.failure_record.write_bytes(migration.canonical_json_bytes(cast(Any, record)))


@pytest.mark.parametrize("mutation", ["phase", "commit", "authority", "output"])
def test_attempt3_invalidation_rejects_mutated_checkpoint_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    base_paths = _paths(tmp_path)
    paths = replace(base_paths, destination_root=tmp_path / migration._ATTEMPT3_ROOT_NAME)
    _write_attempt3_failure_fixture(paths)
    failure = json.loads(paths.failure_record.read_text(encoding="utf-8"))
    if mutation == "phase":
        failure["phase"] = "stage7-equivalence"
    elif mutation == "commit":
        failure["implementation_commit"] = "1" * 40
    elif mutation == "authority":
        failure["old_authority_untouched"] = False
    else:
        output = paths.destination_root / "stage6-result-v3"
        output.mkdir()
        failure["outputs"] = migration._failure_output_snapshot(paths)
        failure["outputs"][0]["exists"] = False
    identity = {key: value for key, value in failure.items() if key != "record_sha256"}
    failure["record_sha256"] = migration._digest_json(identity)
    paths.failure_record.write_bytes(migration.canonical_json_bytes(failure))
    calls: list[str] = []

    def fail_stage6(*_args: object, **_kwargs: object) -> Any:
        calls.append("stage6")
        raise AssertionError("replay reached")

    def fail_stage7(*_args: object, **_kwargs: object) -> Any:
        calls.append("stage7")
        raise AssertionError("replay reached")

    monkeypatch.setattr(migration, "authenticate_ibkr_historical_result", fail_stage6)
    monkeypatch.setattr(migration, "authenticate_provider_history_v3", fail_stage7)
    with pytest.raises(ValueError):
        migration._invalidate_failed_stage8_attempt3(
            paths,
            completion_root=tmp_path / "completion",
            expected_implementation_commit="0" * 40,
            promotion_authorisation=migration.PromotionAuthorisation(
                authorized_by="operator",
                authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
                authorization_reference="attempt3-invalidation-fixture",
            ),
        )
    assert calls == []


def test_attempt3_invalidation_requires_absent_completion_root_without_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_paths = _paths(tmp_path)
    paths = replace(base_paths, destination_root=tmp_path / migration._ATTEMPT3_ROOT_NAME)
    _write_attempt3_failure_fixture(paths)
    completion = tmp_path / "completion"
    completion.mkdir()

    def fail_auth(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("authentication must not run")

    monkeypatch.setattr(migration, "authenticate_ibkr_historical_result", fail_auth)
    with pytest.raises(FileExistsError, match="completion root"):
        migration._invalidate_failed_stage8_attempt3(
            paths,
            completion_root=completion,
            expected_implementation_commit="0" * 40,
            promotion_authorisation=migration.PromotionAuthorisation(
                authorized_by="operator",
                authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
                authorization_reference="attempt3-invalidation-fixture",
            ),
        )
    assert not (completion / migration._RECORD_OUTPUT).exists()


@pytest.mark.parametrize("mode", ["retained", "attempt", "symlink", "nondirectory"])
def test_attempt3_invalidation_rejects_unsafe_completion_root_before_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    base_paths = _paths(tmp_path)
    paths = replace(base_paths, destination_root=tmp_path / migration._ATTEMPT3_ROOT_NAME)
    _write_attempt3_failure_fixture(paths)
    if mode == "retained":
        completion = paths.source_stage6_manifest.parent / "completion"
    elif mode == "attempt":
        completion = paths.destination_root / "nested-completion"
    elif mode == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        linked = tmp_path / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        completion = linked / "completion"
    else:
        non_directory = tmp_path / "not-a-directory"
        non_directory.write_bytes(b"x")
        completion = non_directory / "completion"

    def fail_auth(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("authentication must not run")

    monkeypatch.setattr(migration, "authenticate_ibkr_historical_result", fail_auth)
    with pytest.raises((ValueError, FileNotFoundError)):
        migration._invalidate_failed_stage8_attempt3(
            paths,
            completion_root=completion,
            expected_implementation_commit=migration._ATTEMPT3_COMMIT,
            promotion_authorisation=migration.PromotionAuthorisation(
                authorized_by="operator",
                authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
                authorization_reference="unsafe-completion-fixture",
            ),
        )
    assert not (tmp_path / "outside" / migration._INVALIDATION_RECORD_OUTPUT).exists()


def test_attempt3_invalidation_writes_bound_no_promotion_record_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_paths = _paths(tmp_path)
    paths = replace(base_paths, destination_root=tmp_path / migration._ATTEMPT3_ROOT_NAME)
    _write_attempt3_failure_fixture(paths, implementation_commit=migration._ATTEMPT3_COMMIT)
    for file_path in (
        paths.stage6_manifest,
        paths.stage6_receipt,
        paths.stage7_manifest,
        paths.stage7_receipt,
        paths.stage8_foundation,
        paths.stage8_receipt,
    ):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"{}")
    paths.stage8_foundation.parent.joinpath("foundation-v3.json.children").mkdir()
    failure = json.loads(paths.failure_record.read_text(encoding="utf-8"))
    failure["outputs"] = migration._failure_output_snapshot(paths)
    old_stage6 = SimpleNamespace(aggregate=SimpleNamespace(result_id="a" * 64, closure_id="b" * 64))
    old_stage7 = SimpleNamespace(dataset=SimpleNamespace(dataset_sha256="c" * 64))
    old_stage8 = {"foundation_build_sha256": "d" * 64, "verification_receipt_id": "e" * 64}
    old_promotion = SimpleNamespace(foundation_bundle_id="f" * 64, promotion_sha256="0" * 64)
    current_stage6 = SimpleNamespace(
        aggregate=SimpleNamespace(result_id="1" * 64, closure_id="2" * 64)
    )
    current_stage7 = SimpleNamespace(dataset=SimpleNamespace(dataset_sha256="3" * 64))
    current_stage8 = {
        "foundation_id": "4" * 64,
        "closure_id": "5" * 64,
        "verification_id": "6" * 64,
    }
    source_stage8_manifest_sha = migration._file_sha256(paths.source_stage8_foundation)
    source_stage7_manifest_sha = migration._file_sha256(paths.source_stage7_manifest)
    failure["source"] = {
        "stage6_result_id": "a" * 64,
        "stage6_closure_id": "b" * 64,
        "stage7_dataset_id": "c" * 64,
        "stage7_manifest_sha256": source_stage7_manifest_sha,
        "stage8_build_id": "d" * 64,
        "stage8_manifest_sha256": source_stage8_manifest_sha,
        "promotion_id": "0" * 64,
    }
    identity = {key: value for key, value in failure.items() if key != "record_sha256"}
    failure["record_sha256"] = migration._digest_json(identity)
    paths.failure_record.write_bytes(migration.canonical_json_bytes(failure))
    old_payload = {
        "children": {"folds": [{"dataset_id": "8" * 64, "row_count": 9}]},
        "readiness": {"state": "QUALIFYING_HISTORY_READY", "causes": []},
    }
    new_payload = {
        "children": {"folds": [{"dataset_id": "9" * 64, "row_count": 0}]},
        "readiness": {
            "state": "INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION",
            "causes": [
                "INSUFFICIENT_COMMON_SUPPORT",
                "INSUFFICIENT_BLOCK_COVERAGE",
                "INSUFFICIENT_DURATION",
                "MISSING_CONFIRMATORY_TARGET",
            ],
        },
    }
    real_read_json = migration._read_json

    def read_json(path: Path, field: str) -> dict[str, object]:
        if path == paths.source_stage8_foundation:
            return {"payload": old_payload}
        if path == paths.stage8_foundation:
            return {"payload": new_payload}
        if path == paths.stage7_receipt:
            return {"verification_id": "7" * 64}
        return real_read_json(path, field)

    calls: list[str] = []
    monkeypatch.setattr(migration, "_read_json", read_json)
    monkeypatch.setattr(
        migration,
        "_read_legacy_ibkr_historical_result_v2_header",
        lambda *_args, **_kwargs: old_stage6,
    )
    monkeypatch.setattr(
        migration,
        "_read_provider_history_v2_manifest",
        lambda *_args, **_kwargs: old_stage7,
    )
    monkeypatch.setattr(
        migration,
        "authenticate_provider_history_v2",
        lambda *_args, **_kwargs: calls.append("old-stage7"),
    )
    monkeypatch.setattr(
        migration,
        "_authenticate_ibkr_foundation_migration_v2",
        lambda *_args, **_kwargs: old_stage8,
    )
    monkeypatch.setattr(
        migration,
        "_authenticate_ibkr_foundation_promotion_migration_v2",
        lambda *_args, **_kwargs: old_promotion,
    )
    monkeypatch.setattr(
        migration,
        "authenticate_ibkr_historical_result",
        lambda *_args, **_kwargs: calls.append("stage6") or current_stage6,
    )
    monkeypatch.setattr(
        migration,
        "authenticate_provider_history_v3",
        lambda *_args, **_kwargs: calls.append("stage7") or current_stage7,
    )
    monkeypatch.setattr(
        migration,
        "authenticate_ibkr_foundation",
        lambda *_args, **_kwargs: calls.append("stage8") or current_stage8,
    )
    commit_calls: list[dict[str, bool]] = []
    monkeypatch.setattr(
        migration,
        "derive_qtrad_commit",
        lambda **kwargs: commit_calls.append(kwargs) or "1" * 40,
    )
    completion = tmp_path / "completion"
    record_path = migration._invalidate_failed_stage8_attempt3(
        paths,
        completion_root=completion,
        expected_implementation_commit=migration._ATTEMPT3_COMMIT,
        promotion_authorisation=migration.PromotionAuthorisation(
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 14, tzinfo=UTC),
            authorization_reference="attempt3-invalidation-success-fixture",
        ),
    )
    assert record_path.name == migration._INVALIDATION_RECORD_OUTPUT
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["equivalent"] is False
    assert record["promotion_created"] is False
    assert record["authority_usable"] is False
    assert record["old_authority"]["status"] == "SUPERSEDED_NOT_CARRIED_FORWARD"
    assert record["work_counts"]["stage8_row_decodes"] == 0
    assert calls == ["old-stage7", "stage6", "stage7", "stage8"]
    assert commit_calls == [{"require_clean": True}]
    assert record["failure_implementation_commit"] == migration._ATTEMPT3_COMMIT
    assert record["finalizer_implementation_commit"] == "1" * 40
    authenticated = migration.authenticate_migration_invalidation_record(record_path)
    assert authenticated["record_sha256"] == record["record_sha256"]
    valid_bytes = migration.canonical_json_bytes(record)
    failure_bytes = paths.failure_record.read_bytes()
    divergence_mutation = json.loads(valid_bytes)
    divergence_mutation["semantic_divergence"]["fold_count"]["old"] = 8
    divergence_identity = {
        key: value for key, value in divergence_mutation.items() if key != "record_sha256"
    }
    divergence_mutation["record_sha256"] = migration._digest_json(divergence_identity)
    record_path.write_bytes(migration.canonical_json_bytes(divergence_mutation))
    with pytest.raises(ValueError, match=r"(target disposition|fold count)"):
        migration.authenticate_migration_invalidation_record(record_path)
    timestamp_mutation = json.loads(valid_bytes)
    timestamp_mutation["operator_authorization"]["authorized_at"] = "2026-08-14T00:00:00"
    timestamp_identity = {
        key: value for key, value in timestamp_mutation.items() if key != "record_sha256"
    }
    timestamp_mutation["record_sha256"] = migration._digest_json(timestamp_identity)
    record_path.write_bytes(migration.canonical_json_bytes(timestamp_mutation))
    with pytest.raises(ValueError, match="must be UTC"):
        migration.authenticate_migration_invalidation_record(record_path)
    record_path.write_bytes(valid_bytes)
    paths.failure_record.write_bytes(b"{}")
    with pytest.raises(ValueError, match="failure record bytes changed"):
        migration.authenticate_migration_invalidation_record(record_path)
    paths.failure_record.write_bytes(failure_bytes)
    (paths.destination_root / migration._RECORD_OUTPUT).write_bytes(b"marker")
    with pytest.raises(ValueError, match="success or promotion"):
        migration.authenticate_migration_invalidation_record(record_path)
    mutated = dict(record)
    mutated["finalizer_implementation_commit"] = "2" * 40
    record_path.write_bytes(migration.canonical_json_bytes(mutated))
    with pytest.raises(ValueError, match="identity changed"):
        migration.authenticate_migration_invalidation_record(record_path)
