import dataclasses
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from qtrad.adapters.parquet.store import ParquetResearchStore
from qtrad.domain.events import JsonValue
from qtrad.domain.historical_coverage import (
    BackfillPlan,
    BackfillPlanItem,
    BackfillQuotaEvidence,
    HistoricalResolution,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import BarProvenance, PriceBasis
from qtrad.runtime.qualification_gap_history import (
    QualificationEvidence,
    build_qualification_gap_history_artifact,
    build_qualification_gap_plan_set_history_artifact,
    load_qualification_evidence,
    load_qualification_gap_history_artifact,
    qualification_gap_backfill_scope,
    validate_qualification_gap_snapshot,
    write_qualification_gap_history_artifact,
)
from qtrad.runtime.qualification_gap_plan_set import (
    QualificationGapPlanEntry,
    build_qualification_gap_plan_set,
)
from qtrad.runtime.research_snapshot import load_research_snapshot_import
from tests.test_quota_replay import sample_bar

_CONFIGURATION_HASH = "a" * 64
_LISTING_ID = ProviderListingId("ig", "demo", "CS.D.US500.CFD.IP")
_GAP_START = datetime(2026, 7, 14, 20, 38, 23, tzinfo=UTC)
_GAP_END = datetime(2026, 7, 14, 20, 40, 10, tzinfo=UTC)
_EVIDENCE_GENERATED = datetime(2026, 7, 17, 4, 5, 33, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 17, 5, 0, tzinfo=UTC)


def _qualification_evidence() -> QualificationEvidence:
    return QualificationEvidence.model_validate_json(
        json.dumps(
            {
                "schema": "qtrad-capture-qualification-v1",
                "generated_at": _EVIDENCE_GENERATED.isoformat(),
                "candidate_start": "2026-07-14T03:05:33+00:00",
                "not_before_end": "2026-07-17T03:05:33+00:00",
                "release": {
                    "expected_image": "example.invalid/qtrad@sha256:" + "1" * 64,
                    "actual_image": "example.invalid/qtrad@sha256:" + "1" * 64,
                    "postgres_image": "postgres@sha256:" + "9" * 64,
                    "descriptor_commit": "1" * 40,
                    "descriptor_sha256": "2" * 64,
                    "evidence_tool_sha256": "3" * 64,
                    "capture_source_id": "oci-sydney-capture-1",
                    "configuration_hash": _CONFIGURATION_HASH,
                    "migration_version": "0009",
                },
                "candidate_gaps": [
                    {
                        "gap_id": "00000000-0000-0000-0000-000000000099",
                        "instrument_id": "index:us-500",
                        "interval_start": _GAP_START.isoformat(),
                        "interval_end": _GAP_END.isoformat(),
                        "reason": "NO_HEALTHY_QUOTE_DURING_EXPECTED_STREAM",
                        "detected_at": "2026-07-14T20:40:11+00:00",
                        "repaired_at": None,
                    }
                ],
                "evidence_sha256": "b" * 64,
            }
        )
    )


def _plan() -> BackfillPlan:
    return BackfillPlan(
        plan_hash="c" * 64,
        universe_name="capture-v1",
        universe_hash=_CONFIGURATION_HASH,
        created_at=datetime(2026, 7, 17, 4, 30, tzinfo=UTC),
        start=datetime(2026, 7, 14, 20, 30, tzinfo=UTC),
        end=datetime(2026, 7, 14, 21, 0, tzinfo=UTC),
        resolution=HistoricalResolution.MINUTE,
        request_chunk_points=1000,
        quota=BackfillQuotaEvidence(
            allowance_name="historical_points_weekly_operator_reported",
            remaining_points=1000,
            observed_at=datetime(2026, 7, 17, 4, 25, tzinfo=UTC),
            reserve_fraction=Decimal("0.2"),
        ),
        items=(
            BackfillPlanItem(
                instrument_id=InstrumentId("index:us-500"),
                listing_id=_LISTING_ID,
                listing_valid_from=datetime(2026, 7, 14, 3, tzinfo=UTC),
                listing_metadata_version="listing-v1",
            ),
        ),
    )


def _bar(minute: int, basis: PriceBasis):
    start = datetime(2026, 7, 14, 20, minute, tzinfo=UTC)
    return dataclasses.replace(
        sample_bar(),
        basis=basis,
        interval_start=start,
        interval_end=start + timedelta(minutes=1),
        provenance=BarProvenance.IG_HISTORICAL,
        source_listing_id=_LISTING_ID,
    )


def _metadata(evidence: QualificationEvidence, plan: BackfillPlan) -> dict[str, JsonValue]:
    gap = evidence.candidate_gaps[0]
    coverage: list[JsonValue] = [
        {
            "instrument_id": "index:us-500",
            "source_listing_id": str(_LISTING_ID),
            "source_listing_valid_from": "2026-07-14T03:00:00Z",
            "source_listing_metadata_version": "listing-v1",
            "provenance": "IG_HISTORICAL",
            "basis": basis.value,
            "resolution": "MINUTE",
            "interval_start": "2026-07-14T20:30:00Z",
            "interval_end": "2026-07-14T21:00:00Z",
            "detected_at": "2026-07-17T04:31:00Z",
            "detected_by_plan_hash": plan.plan_hash,
            "request_completed_at": "2026-07-17T04:32:00Z",
            "returned_points": 30,
            "covered_at": "2026-07-17T04:32:00Z",
            "covered_by_plan_hash": plan.plan_hash,
            "observed_points": 30,
        }
        for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)
    ]
    return {
        "manifest_contract": "qtrad-research-bars-v2",
        "requested_interval": {
            "start": "2026-07-14T20:30:00Z",
            "end": "2026-07-14T21:00:00Z",
        },
        "source_snapshot": {
            "kind": "verified-capture-snapshot",
            "import_sha256": "d" * 64,
            "capture_source_id": evidence.release.capture_source_id,
            "source_created_at": "2026-07-17T04:10:00Z",
        },
        "live_gaps": {
            "records": [
                {
                    "gap_id": gap.gap_id,
                    "instrument_id": gap.instrument_id,
                    "interval_start": gap.interval_start.isoformat().replace("+00:00", "Z"),
                    "interval_end": gap.interval_end.isoformat().replace("+00:00", "Z"),
                    "reason": gap.reason,
                }
            ]
        },
        "historical_coverage": {"records": coverage},
    }


def _snapshot_import(tmp_path: Path, *, source_created_at: str = "2026-07-17T04:10:00Z"):
    identity = {
        "schema": "qtrad-research-snapshot-import-v1",
        "imported_at": "2026-07-17T04:20:00Z",
        "target_database": "qtrad_research_capture_20260717",
        "source_manifest_schema": "qtrad-capture-backup-v2",
        "source_manifest_file_sha256": "1" * 64,
        "source_manifest_identity_sha256": "2" * 64,
        "source_archive_sha256": "3" * 64,
        "source_created_at": source_created_at,
        "capture_source_id": "oci-sydney-capture-1",
        "universe_name": "capture-v1",
        "universe_hash": _CONFIGURATION_HASH,
        "capture_image": "example.invalid/qtrad@sha256:" + "4" * 64,
        "postgres_image": "postgres@sha256:" + "5" * 64,
        "migration_version": "0003",
        "raw_message_count": 100,
        "canonical_event_count": 100,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    identity["import_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = tmp_path / f"snapshot-{source_created_at[11:16].replace(':', '')}.json"
    path.write_text(json.dumps(identity), encoding="utf-8")
    return load_research_snapshot_import(path)


def test_gap_backfill_scope_and_snapshot_identity_are_derived_fail_closed(
    tmp_path: Path,
) -> None:
    evidence = _qualification_evidence()
    scope = qualification_gap_backfill_scope(evidence)

    assert scope.start == datetime(2026, 7, 14, 20, 38, tzinfo=UTC)
    assert scope.end == datetime(2026, 7, 14, 20, 41, tzinfo=UTC)
    assert scope.instrument_ids == (InstrumentId("index:us-500"),)
    validate_qualification_gap_snapshot(
        evidence=evidence,
        snapshot=_snapshot_import(tmp_path),
        database_name="qtrad_research_capture_20260717",
        configured_capture_source_id="oci-sydney-capture-1",
        universe_name="capture-v1",
        universe_hash=_CONFIGURATION_HASH,
    )
    with pytest.raises(ValueError, match="predates"):
        validate_qualification_gap_snapshot(
            evidence=evidence,
            snapshot=_snapshot_import(tmp_path, source_created_at="2026-07-17T04:00:00Z"),
            database_name="qtrad_research_capture_20260717",
            configured_capture_source_id="oci-sydney-capture-1",
            universe_name="capture-v1",
            universe_hash=_CONFIGURATION_HASH,
        )


@pytest.mark.asyncio
async def test_gap_history_binds_verified_historical_bars_without_classifying_gap(
    tmp_path: Path,
) -> None:
    evidence = _qualification_evidence()
    plan = _plan()
    bars = tuple(
        _bar(minute, basis)
        for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)
        for minute in (38, 39, 40)
    )
    store = ParquetResearchStore(tmp_path, FixedClock())
    manifest = await store.write_bars(
        bars,
        universe_name=plan.universe_name,
        configuration_hash=plan.universe_hash,
        metadata=_metadata(evidence, plan),
    )
    restored = await store.read_bars(manifest.manifest_id)

    artifact = build_qualification_gap_history_artifact(
        evidence=evidence,
        plan=plan,
        manifest=manifest,
        bars=restored,
        generated_at=datetime(2026, 7, 17, 5, 1, tzinfo=UTC),
    )

    assert artifact.interpretation == "CORROBORATING_ONLY_DOES_NOT_CLASSIFY_OR_REPAIR_LIVE_GAPS"
    assert artifact.results[0].historical_data_status == "HISTORICAL_DATA_PRESENT"
    assert [basis.returned_points_in_gap for basis in artifact.results[0].bases] == [3, 3, 3]
    assert all(basis.complete_interval_coverage for basis in artifact.results[0].bases)
    output = tmp_path / "gap-history.json"
    write_qualification_gap_history_artifact(output, artifact)
    assert load_qualification_gap_history_artifact(output) == artifact
    with pytest.raises(FileExistsError):
        write_qualification_gap_history_artifact(output, artifact)
    tampered = artifact.model_copy(update={"capture_source_id": "another-source"})
    with pytest.raises(ValueError, match="hash"):
        write_qualification_gap_history_artifact(tmp_path / "tampered.json", tampered)
    encoded = json.loads(output.read_text(encoding="utf-8"))
    encoded["results"][0]["historical_data_status"] = "NO_HISTORICAL_DATA_RETURNED"
    output.write_text(json.dumps(encoded), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_qualification_gap_history_artifact(output)


@pytest.mark.asyncio
async def test_gap_history_records_no_returned_data_without_inventing_upstream_cause(
    tmp_path: Path,
) -> None:
    evidence = _qualification_evidence()
    plan = _plan()
    bars = tuple(_bar(50, basis) for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID))
    store = ParquetResearchStore(tmp_path, FixedClock())
    manifest = await store.write_bars(
        bars,
        universe_name=plan.universe_name,
        configuration_hash=plan.universe_hash,
        metadata=_metadata(evidence, plan),
    )

    artifact = build_qualification_gap_history_artifact(
        evidence=evidence,
        plan=plan,
        manifest=manifest,
        bars=await store.read_bars(manifest.manifest_id),
        generated_at=datetime(2026, 7, 17, 5, 1, tzinfo=UTC),
    )

    assert artifact.results[0].historical_data_status == "NO_HISTORICAL_DATA_RETURNED"
    assert all(basis.returned_points_in_gap == 0 for basis in artifact.results[0].bases)


@pytest.mark.asyncio
async def test_gap_history_binds_sparse_plan_set_and_all_plan_hashes(tmp_path: Path) -> None:
    evidence = _qualification_evidence()
    plan = _plan()
    plan_set = build_qualification_gap_plan_set(
        qualification_evidence_sha256=evidence.evidence_sha256,
        snapshot_import_sha256="d" * 64,
        capture_source_id=evidence.release.capture_source_id,
        universe_name=plan.universe_name,
        universe_hash=plan.universe_hash,
        created_at=datetime(2026, 7, 17, 4, 30, tzinfo=UTC),
        remaining_allowance=1000,
        reserve_points=200,
        entries=(
            QualificationGapPlanEntry(
                file="gap-plan-001.json",
                plan_hash=plan.plan_hash,
                gap_ids=(evidence.candidate_gaps[0].gap_id,),
                requested_points=plan.requested_points,
            ),
        ),
    )
    bars = tuple(
        _bar(minute, basis)
        for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)
        for minute in (38, 39, 40)
    )
    store = ParquetResearchStore(tmp_path, FixedClock())
    manifest = await store.write_bars(
        bars,
        universe_name=plan.universe_name,
        configuration_hash=plan.universe_hash,
        metadata=_metadata(evidence, plan),
    )

    artifact = build_qualification_gap_plan_set_history_artifact(
        evidence=evidence,
        plan_set=plan_set,
        plans=(plan,),
        manifest=manifest,
        bars=await store.read_bars(manifest.manifest_id),
        generated_at=datetime(2026, 7, 17, 5, 1, tzinfo=UTC),
    )

    assert artifact.schema_name == "qtrad-qualification-gap-history-v2"
    assert artifact.backfill_plan_set_hash == plan_set.plan_set_hash
    assert artifact.backfill_plan_hashes == (plan.plan_hash,)
    output = tmp_path / "gap-history-v2.json"
    write_qualification_gap_history_artifact(output, artifact)
    assert load_qualification_gap_history_artifact(output) == artifact


def test_qualification_evidence_loader_rejects_tampering(tmp_path: Path) -> None:
    payload = _qualification_evidence().model_dump(mode="json", by_alias=True)
    del payload["evidence_sha256"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["evidence_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_qualification_evidence(path)
    assert loaded.candidate_gaps[0].gap_id == "00000000-0000-0000-0000-000000000099"

    payload["candidate_gaps"][0]["reason"] = "tampered"  # type: ignore[index]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_qualification_evidence(path)


@pytest.mark.asyncio
async def test_gap_history_rejects_unverified_or_identity_drifted_research_evidence(
    tmp_path: Path,
) -> None:
    evidence = _qualification_evidence()
    plan = _plan()
    metadata = _metadata(evidence, plan)
    source = cast(dict[str, JsonValue], metadata["source_snapshot"])
    source["kind"] = "unbound-database"
    store = ParquetResearchStore(tmp_path, FixedClock())
    manifest = await store.write_bars(
        tuple(_bar(50, basis) for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)),
        universe_name=plan.universe_name,
        configuration_hash=plan.universe_hash,
        metadata=metadata,
    )

    with pytest.raises(ValueError, match="verified capture snapshot"):
        build_qualification_gap_history_artifact(
            evidence=evidence,
            plan=plan,
            manifest=manifest,
            bars=await store.read_bars(manifest.manifest_id),
            generated_at=datetime(2026, 7, 17, 5, 1, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_gap_history_rejects_plan_configuration_drift(tmp_path: Path) -> None:
    evidence = _qualification_evidence()
    plan = _plan()
    store = ParquetResearchStore(tmp_path, FixedClock())
    manifest = await store.write_bars(
        tuple(_bar(50, basis) for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)),
        universe_name=plan.universe_name,
        configuration_hash=plan.universe_hash,
        metadata=_metadata(evidence, plan),
    )

    with pytest.raises(ValueError, match="qualification configuration"):
        build_qualification_gap_history_artifact(
            evidence=evidence,
            plan=dataclasses.replace(plan, universe_hash="e" * 64),
            manifest=manifest,
            bars=await store.read_bars(manifest.manifest_id),
            generated_at=datetime(2026, 7, 17, 5, 1, tzinfo=UTC),
        )
