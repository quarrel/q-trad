"""Hash-bound comparison of live qualification gaps with historical IG bars."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from qtrad.application.replay import semantic_bar_hash
from qtrad.domain.historical_coverage import BackfillPlan
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import BarProvenance, MarketBar, PriceBasis
from qtrad.ports.storage import ResearchManifest
from qtrad.runtime.qualification_gap_plan_set import QualificationGapPlanSet
from qtrad.runtime.research_snapshot import ResearchSnapshotImport

_MAX_QUALIFICATION_EVIDENCE_BYTES = 16 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_BASES = (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)


class QualificationGap(_StrictModel):
    gap_id: str = Field(min_length=1, max_length=128)
    instrument_id: str = Field(min_length=1, max_length=200)
    interval_start: datetime
    interval_end: datetime
    reason: str = Field(min_length=1, max_length=200)
    detected_at: datetime
    repaired_at: datetime | None


class QualificationRelease(_StrictModel):
    expected_image: str
    actual_image: str
    postgres_image: str
    descriptor_commit: str
    descriptor_sha256: str
    evidence_tool_sha256: str
    capture_source_id: str = Field(min_length=1, max_length=200)
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration_version: str


class QualificationEvidence(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True, populate_by_name=True)

    schema_name: Literal["qtrad-capture-qualification-v1"] = Field(alias="schema")
    generated_at: datetime
    candidate_start: datetime
    not_before_end: datetime
    release: QualificationRelease
    candidate_gaps: tuple[QualificationGap, ...] = Field(max_length=100)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HistoricalBasisEvidence(_StrictModel):
    basis: Literal["BID", "ASK", "MID"]
    expected_minute_intervals: int = Field(gt=0)
    returned_points_in_gap: int = Field(ge=0)
    complete_interval_coverage: bool
    first_interval_start: datetime | None
    last_interval_end: datetime | None
    semantic_bar_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan_observed_points: int = Field(gt=0)


class QualificationGapBackfillScope(_StrictModel):
    start: datetime
    end: datetime
    instrument_ids: tuple[InstrumentId, ...]
    gap_ids: tuple[str, ...] = ()


class GapHistoricalEvidence(_StrictModel):
    gap_id: str
    instrument_id: str
    interval_start: datetime
    interval_end: datetime
    aligned_query_start: datetime
    aligned_query_end: datetime
    source_listing_id: str
    source_listing_valid_from: datetime
    source_listing_metadata_version: str
    historical_data_status: Literal["HISTORICAL_DATA_PRESENT", "NO_HISTORICAL_DATA_RETURNED"]
    bases: tuple[HistoricalBasisEvidence, HistoricalBasisEvidence, HistoricalBasisEvidence]


class QualificationGapHistoryArtifact(_StrictModel):
    schema_name: Literal["qtrad-qualification-gap-history-v1"] = Field(alias="schema")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    qualification_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backfill_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_source_id: str
    universe_name: str
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_import_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: tuple[GapHistoricalEvidence, ...]
    interpretation: Literal["CORROBORATING_ONLY_DOES_NOT_CLASSIFY_OR_REPAIR_LIVE_GAPS"] = (
        "CORROBORATING_ONLY_DOES_NOT_CLASSIFY_OR_REPAIR_LIVE_GAPS"
    )


class QualificationGapPlanSetHistoryArtifact(_StrictModel):
    schema_name: Literal["qtrad-qualification-gap-history-v2"] = Field(alias="schema")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    qualification_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backfill_plan_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    backfill_plan_hashes: tuple[str, ...] = Field(min_length=1, max_length=100)
    research_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_source_id: str
    universe_name: str
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_import_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: tuple[GapHistoricalEvidence, ...]
    interpretation: Literal["CORROBORATING_ONLY_DOES_NOT_CLASSIFY_OR_REPAIR_LIVE_GAPS"] = (
        "CORROBORATING_ONLY_DOES_NOT_CLASSIFY_OR_REPAIR_LIVE_GAPS"
    )


def load_qualification_evidence(path: Path) -> QualificationEvidence:
    """Load and verify one bounded, self-hashed automatic qualification snapshot."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("qualification evidence must be a regular non-symlink file")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_QUALIFICATION_EVIDENCE_BYTES:
        raise ValueError("qualification evidence exceeds the maximum encoded size")
    raw = json.loads(encoded)
    if not isinstance(raw, dict):
        raise TypeError("qualification evidence must be a JSON object")
    recorded_hash = raw["evidence_sha256"]
    if not isinstance(recorded_hash, str):
        raise TypeError("qualification evidence hash must be a string")
    identity = dict(raw)
    del identity["evidence_sha256"]
    if _sha256_json(identity) != recorded_hash:
        raise ValueError("qualification evidence hash does not match its canonical content")
    evidence = QualificationEvidence.model_validate_json(encoded)
    _validate_qualification_evidence(evidence)
    return evidence


def qualification_gap_backfill_scope(
    evidence: QualificationEvidence,
) -> QualificationGapBackfillScope:
    """Derive one deterministic minute-aligned range for all candidate gaps."""

    _validate_qualification_evidence(evidence)
    if not evidence.candidate_gaps:
        raise ValueError("qualification evidence contains no candidate gaps to investigate")
    start = min(gap.interval_start for gap in evidence.candidate_gaps).replace(
        second=0, microsecond=0
    )
    latest_end = max(gap.interval_end for gap in evidence.candidate_gaps)
    end = latest_end.replace(second=0, microsecond=0)
    if end < latest_end:
        end += timedelta(minutes=1)
    instrument_ids = tuple(
        InstrumentId(value)
        for value in sorted({gap.instrument_id for gap in evidence.candidate_gaps})
    )
    return QualificationGapBackfillScope(start=start, end=end, instrument_ids=instrument_ids)


def qualification_gap_backfill_scopes(
    evidence: QualificationEvidence,
) -> tuple[QualificationGapBackfillScope, ...]:
    """Merge only overlapping minute-aligned gaps for each instrument."""

    _validate_qualification_evidence(evidence)
    if not evidence.candidate_gaps:
        raise ValueError("qualification evidence contains no candidate gaps to investigate")
    grouped: dict[str, list[tuple[datetime, datetime, str]]] = {}
    for gap in evidence.candidate_gaps:
        start = gap.interval_start.replace(second=0, microsecond=0)
        end = gap.interval_end.replace(second=0, microsecond=0)
        if end < gap.interval_end:
            end += timedelta(minutes=1)
        grouped.setdefault(gap.instrument_id, []).append((start, end, gap.gap_id))
    scopes: list[QualificationGapBackfillScope] = []
    for instrument_id in sorted(grouped):
        current_start: datetime | None = None
        current_end: datetime | None = None
        current_gap_ids: list[str] = []
        for start, end, gap_id in sorted(grouped[instrument_id]):
            if current_end is None or start > current_end:
                if current_start is not None and current_end is not None:
                    scopes.append(
                        QualificationGapBackfillScope(
                            start=current_start,
                            end=current_end,
                            instrument_ids=(InstrumentId(instrument_id),),
                            gap_ids=tuple(current_gap_ids),
                        )
                    )
                current_start, current_end, current_gap_ids = start, end, [gap_id]
            else:
                current_end = max(current_end, end)
                current_gap_ids.append(gap_id)
        if current_start is not None and current_end is not None:
            scopes.append(
                QualificationGapBackfillScope(
                    start=current_start,
                    end=current_end,
                    instrument_ids=(InstrumentId(instrument_id),),
                    gap_ids=tuple(current_gap_ids),
                )
            )
    return tuple(sorted(scopes, key=lambda scope: (scope.start, str(scope.instrument_ids[0]))))


def validate_qualification_gap_snapshot(
    *,
    evidence: QualificationEvidence,
    snapshot: ResearchSnapshotImport,
    database_name: str,
    configured_capture_source_id: str,
    universe_name: str,
    universe_hash: str,
) -> None:
    """Require a verified post-evidence snapshot and exact isolated target identity."""

    _validate_qualification_evidence(evidence)
    if snapshot.target_database != database_name:
        raise ValueError("snapshot import evidence does not identify the configured database")
    if not database_name.startswith("qtrad_research_"):
        raise ValueError("qualification gap history requires an isolated research database")
    if configured_capture_source_id != evidence.release.capture_source_id:
        raise ValueError("configured capture source does not match qualification evidence")
    if snapshot.capture_source_id != evidence.release.capture_source_id:
        raise ValueError("snapshot import does not match the qualification capture source")
    if snapshot.universe_hash != evidence.release.configuration_hash:
        raise ValueError("snapshot import does not match the qualification configuration")
    if universe_hash != evidence.release.configuration_hash:
        raise ValueError("selected universe does not match the qualification configuration")
    if snapshot.universe_name not in {"unknown-v1", universe_name}:
        raise ValueError("snapshot import has a different capture universe")
    if snapshot.source_created_at < evidence.generated_at:
        raise ValueError("snapshot import predates the automatic qualification evidence")


def build_qualification_gap_history_artifact(
    *,
    evidence: QualificationEvidence,
    plan: BackfillPlan,
    manifest: ResearchManifest,
    bars: Sequence[MarketBar],
    generated_at: datetime,
) -> QualificationGapHistoryArtifact:
    """Compare immutable live gaps with verified historical bars without inferring a cause."""

    _require_utc(generated_at, "historical gap review generation time")
    _validate_qualification_evidence(evidence)
    if not evidence.candidate_gaps:
        raise ValueError("qualification evidence contains no candidate gaps to investigate")
    if generated_at < manifest.created_at:
        raise ValueError("historical gap review cannot predate its research manifest")
    if plan.universe_hash != evidence.release.configuration_hash:
        raise ValueError("backfill plan does not match the qualification configuration")
    if manifest.schema_version != 2 or manifest.manifest_sha256 is None:
        raise ValueError("historical gap review requires a hash-verified version-two manifest")
    if manifest.configuration_hash != plan.universe_hash:
        raise ValueError("research manifest does not match the backfill plan configuration")
    if manifest.universe_name != plan.universe_name:
        raise ValueError("research manifest does not match the backfill plan universe")

    metadata = manifest.metadata
    _require_metadata_identity(metadata, evidence, plan)
    coverage_points = _plan_coverage_points(metadata, plan)
    _require_live_gap_evidence(metadata, evidence)
    plan_items = {str(item.instrument_id): item for item in plan.items}
    results: list[GapHistoricalEvidence] = []
    for gap in sorted(evidence.candidate_gaps, key=lambda item: (item.interval_start, item.gap_id)):
        item = plan_items.get(gap.instrument_id)
        if item is None:
            raise ValueError(
                f"backfill plan omits qualification gap instrument: {gap.instrument_id}"
            )
        aligned_start = gap.interval_start.replace(second=0, microsecond=0)
        aligned_end = gap.interval_end.replace(second=0, microsecond=0)
        if aligned_end < gap.interval_end:
            aligned_end += timedelta(minutes=1)
        if plan.start > aligned_start or plan.end < aligned_end:
            raise ValueError(f"backfill plan does not cover qualification gap: {gap.gap_id}")
        expected_minutes = int((aligned_end - aligned_start).total_seconds() // 60)
        listing_bars = [
            bar
            for bar in bars
            if str(bar.instrument_id) == gap.instrument_id
            and bar.provenance is BarProvenance.IG_HISTORICAL
            and bar.source_listing_id == item.listing_id
            and bar.interval_start < gap.interval_end
            and bar.interval_end > gap.interval_start
        ]
        basis_evidence = (
            _basis_evidence(
                basis=PriceBasis.BID,
                bars=listing_bars,
                aligned_start=aligned_start,
                aligned_end=aligned_end,
                expected_minutes=expected_minutes,
                plan_observed_points=coverage_points[(gap.instrument_id, PriceBasis.BID.value)],
            ),
            _basis_evidence(
                basis=PriceBasis.ASK,
                bars=listing_bars,
                aligned_start=aligned_start,
                aligned_end=aligned_end,
                expected_minutes=expected_minutes,
                plan_observed_points=coverage_points[(gap.instrument_id, PriceBasis.ASK.value)],
            ),
            _basis_evidence(
                basis=PriceBasis.MID,
                bars=listing_bars,
                aligned_start=aligned_start,
                aligned_end=aligned_end,
                expected_minutes=expected_minutes,
                plan_observed_points=coverage_points[(gap.instrument_id, PriceBasis.MID.value)],
            ),
        )
        results.append(
            GapHistoricalEvidence(
                gap_id=gap.gap_id,
                instrument_id=gap.instrument_id,
                interval_start=gap.interval_start,
                interval_end=gap.interval_end,
                aligned_query_start=aligned_start,
                aligned_query_end=aligned_end,
                source_listing_id=str(item.listing_id),
                source_listing_valid_from=item.listing_valid_from,
                source_listing_metadata_version=item.listing_metadata_version,
                historical_data_status=(
                    "HISTORICAL_DATA_PRESENT"
                    if any(value.returned_points_in_gap for value in basis_evidence)
                    else "NO_HISTORICAL_DATA_RETURNED"
                ),
                bases=basis_evidence,
            )
        )

    source_snapshot = _mapping(metadata["source_snapshot"], "source snapshot")
    source_snapshot_import_sha256 = source_snapshot["import_sha256"]
    if not isinstance(source_snapshot_import_sha256, str):
        raise TypeError("source snapshot import hash must be a string")
    identity = {
        "schema": "qtrad-qualification-gap-history-v1",
        "generated_at": generated_at,
        "qualification_evidence_sha256": evidence.evidence_sha256,
        "backfill_plan_hash": plan.plan_hash,
        "research_manifest_sha256": manifest.manifest_sha256,
        "capture_source_id": evidence.release.capture_source_id,
        "universe_name": plan.universe_name,
        "configuration_hash": plan.universe_hash,
        "source_snapshot_import_sha256": source_snapshot_import_sha256,
        "results": tuple(results),
        "interpretation": "CORROBORATING_ONLY_DOES_NOT_CLASSIFY_OR_REPAIR_LIVE_GAPS",
    }
    artifact_hash = _sha256_json(_json_value(identity))
    return QualificationGapHistoryArtifact(
        schema="qtrad-qualification-gap-history-v1",
        artifact_sha256=artifact_hash,
        generated_at=generated_at,
        qualification_evidence_sha256=evidence.evidence_sha256,
        backfill_plan_hash=plan.plan_hash,
        research_manifest_sha256=manifest.manifest_sha256,
        capture_source_id=evidence.release.capture_source_id,
        universe_name=plan.universe_name,
        configuration_hash=plan.universe_hash,
        source_snapshot_import_sha256=source_snapshot_import_sha256,
        results=tuple(results),
    )


def build_qualification_gap_plan_set_history_artifact(
    *,
    evidence: QualificationEvidence,
    plan_set: QualificationGapPlanSet,
    plans: Sequence[BackfillPlan],
    manifest: ResearchManifest,
    bars: Sequence[MarketBar],
    generated_at: datetime,
) -> QualificationGapPlanSetHistoryArtifact:
    """Compare all sparse planned ranges without broadening provider requests."""

    _require_utc(generated_at, "historical gap review generation time")
    _validate_qualification_evidence(evidence)
    if not evidence.candidate_gaps:
        raise ValueError("qualification evidence contains no candidate gaps to investigate")
    if len(plans) != len(plan_set.entries):
        raise ValueError("qualification gap plan set does not match loaded plans")
    if plan_set.qualification_evidence_sha256 != evidence.evidence_sha256:
        raise ValueError("qualification gap plan set does not match automatic evidence")
    if (
        plan_set.capture_source_id != evidence.release.capture_source_id
        or plan_set.universe_hash != evidence.release.configuration_hash
    ):
        raise ValueError("qualification gap plan set has different capture identity")
    plan_by_gap: dict[str, BackfillPlan] = {}
    for entry, plan in zip(plan_set.entries, plans, strict=True):
        if entry.plan_hash != plan.plan_hash:
            raise ValueError("qualification gap plan-set entry differs from loaded plan")
        for gap_id in entry.gap_ids:
            if gap_id in plan_by_gap:
                raise ValueError("qualification gap plan set contains duplicate gap IDs")
            plan_by_gap[gap_id] = plan
    expected_gap_ids = {gap.gap_id for gap in evidence.candidate_gaps}
    if set(plan_by_gap) != expected_gap_ids:
        raise ValueError("qualification gap plan set does not cover the exact evidence gap set")
    if manifest.schema_version != 2 or manifest.manifest_sha256 is None:
        raise ValueError("historical gap review requires a hash-verified version-two manifest")
    if (
        manifest.configuration_hash != plan_set.universe_hash
        or manifest.universe_name != plan_set.universe_name
    ):
        raise ValueError("research manifest does not match the qualification plan set")
    metadata = manifest.metadata
    _require_plan_set_metadata_identity(metadata, evidence, plan_set, plans)
    _require_live_gap_evidence(metadata, evidence)
    coverage_by_hash = {
        plan.plan_hash: _plan_coverage_points(metadata, plan, allow_empty=True) for plan in plans
    }
    results = tuple(
        _gap_result(
            gap=gap,
            plan=plan_by_gap[gap.gap_id],
            bars=bars,
            coverage_points=coverage_by_hash[plan_by_gap[gap.gap_id].plan_hash],
        )
        for gap in sorted(
            evidence.candidate_gaps, key=lambda item: (item.interval_start, item.gap_id)
        )
    )
    source_snapshot = _mapping(metadata["source_snapshot"], "source snapshot")
    source_snapshot_import_sha256 = source_snapshot["import_sha256"]
    if not isinstance(source_snapshot_import_sha256, str):
        raise TypeError("source snapshot import hash must be a string")
    plan_hashes = tuple(entry.plan_hash for entry in plan_set.entries)
    identity = {
        "schema": "qtrad-qualification-gap-history-v2",
        "generated_at": generated_at,
        "qualification_evidence_sha256": evidence.evidence_sha256,
        "backfill_plan_set_hash": plan_set.plan_set_hash,
        "backfill_plan_hashes": plan_hashes,
        "research_manifest_sha256": manifest.manifest_sha256,
        "capture_source_id": evidence.release.capture_source_id,
        "universe_name": plan_set.universe_name,
        "configuration_hash": plan_set.universe_hash,
        "source_snapshot_import_sha256": source_snapshot_import_sha256,
        "results": results,
        "interpretation": "CORROBORATING_ONLY_DOES_NOT_CLASSIFY_OR_REPAIR_LIVE_GAPS",
    }
    artifact_hash = _sha256_json(_json_value(identity))
    return QualificationGapPlanSetHistoryArtifact(
        artifact_sha256=artifact_hash,
        **identity,
    )


def _gap_result(
    *,
    gap: QualificationGap,
    plan: BackfillPlan,
    bars: Sequence[MarketBar],
    coverage_points: Mapping[tuple[str, str], int],
) -> GapHistoricalEvidence:
    item = next(
        (value for value in plan.items if str(value.instrument_id) == gap.instrument_id), None
    )
    if item is None:
        raise ValueError(f"backfill plan omits qualification gap instrument: {gap.instrument_id}")
    aligned_start = gap.interval_start.replace(second=0, microsecond=0)
    aligned_end = gap.interval_end.replace(second=0, microsecond=0)
    if aligned_end < gap.interval_end:
        aligned_end += timedelta(minutes=1)
    if plan.start > aligned_start or plan.end < aligned_end:
        raise ValueError(f"backfill plan does not cover qualification gap: {gap.gap_id}")
    expected_minutes = int((aligned_end - aligned_start).total_seconds() // 60)
    listing_bars = [
        bar
        for bar in bars
        if str(bar.instrument_id) == gap.instrument_id
        and bar.provenance is BarProvenance.IG_HISTORICAL
        and bar.source_listing_id == item.listing_id
        and bar.interval_start < gap.interval_end
        and bar.interval_end > gap.interval_start
    ]
    basis_evidence = tuple(
        _basis_evidence(
            basis=basis,
            bars=listing_bars,
            aligned_start=aligned_start,
            aligned_end=aligned_end,
            expected_minutes=expected_minutes,
            plan_observed_points=coverage_points[(gap.instrument_id, basis.value)],
        )
        for basis in _BASES
    )
    return GapHistoricalEvidence(
        gap_id=gap.gap_id,
        instrument_id=gap.instrument_id,
        interval_start=gap.interval_start,
        interval_end=gap.interval_end,
        aligned_query_start=aligned_start,
        aligned_query_end=aligned_end,
        source_listing_id=str(item.listing_id),
        source_listing_valid_from=item.listing_valid_from,
        source_listing_metadata_version=item.listing_metadata_version,
        historical_data_status=(
            "HISTORICAL_DATA_PRESENT"
            if any(value.returned_points_in_gap for value in basis_evidence)
            else "NO_HISTORICAL_DATA_RETURNED"
        ),
        bases=cast(
            tuple[HistoricalBasisEvidence, HistoricalBasisEvidence, HistoricalBasisEvidence],
            basis_evidence,
        ),
    )


def _require_plan_set_metadata_identity(
    metadata: Mapping[str, object],
    evidence: QualificationEvidence,
    plan_set: QualificationGapPlanSet,
    plans: Sequence[BackfillPlan],
) -> None:
    if metadata["manifest_contract"] != "qtrad-research-bars-v2":
        raise ValueError("research manifest does not use the version-two bars contract")
    requested = _mapping(metadata["requested_interval"], "requested interval")
    if _datetime(requested["start"], "requested interval start") != min(
        plan.start for plan in plans
    ):
        raise ValueError("research export start does not match the qualification plan set")
    if _datetime(requested["end"], "requested interval end") != max(plan.end for plan in plans):
        raise ValueError("research export end does not match the qualification plan set")
    source = _mapping(metadata["source_snapshot"], "source snapshot")
    if source["kind"] != "verified-capture-snapshot":
        raise ValueError("historical gap review requires a verified capture snapshot")
    if source["capture_source_id"] != evidence.release.capture_source_id:
        raise ValueError("research snapshot does not match the qualification capture source")
    if source["import_sha256"] != plan_set.snapshot_import_sha256:
        raise ValueError("research snapshot does not match the qualification plan set")
    if (
        _datetime(source["source_created_at"], "source snapshot creation time")
        < evidence.generated_at
    ):
        raise ValueError("research snapshot predates the automatic qualification evidence")


def write_qualification_gap_history_artifact(
    path: Path, artifact: QualificationGapHistoryArtifact | QualificationGapPlanSetHistoryArtifact
) -> None:
    """Write one bounded artifact without overwriting existing evidence."""

    _validate_artifact_hash(artifact)
    if not path.parent.is_dir():
        raise FileNotFoundError(f"historical gap evidence directory does not exist: {path.parent}")
    if path.is_symlink():
        raise ValueError("historical gap evidence output must not be a symlink")
    encoded = (
        json.dumps(artifact.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True) + "\n"
    )
    if len(encoded.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
        raise ValueError("historical gap evidence exceeds the maximum encoded size")
    with path.open("x", encoding="utf-8") as output:
        output.write(encoded)


def load_qualification_gap_history_artifact(
    path: Path,
) -> QualificationGapHistoryArtifact | QualificationGapPlanSetHistoryArtifact:
    """Load and verify one previously written historical-gap artifact."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("historical gap evidence must be a regular non-symlink file")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError("historical gap evidence exceeds the maximum encoded size")
    raw = json.loads(encoded)
    if not isinstance(raw, dict):
        raise TypeError("historical gap evidence must be a JSON object")
    if raw.get("schema") == "qtrad-qualification-gap-history-v2":
        artifact = QualificationGapPlanSetHistoryArtifact.model_validate_json(encoded)
    else:
        artifact = QualificationGapHistoryArtifact.model_validate_json(encoded)
    _validate_artifact_hash(artifact)
    return artifact


def _validate_qualification_evidence(evidence: QualificationEvidence) -> None:
    _require_utc(evidence.generated_at, "qualification evidence generation time")
    _require_utc(evidence.candidate_start, "qualification candidate start")
    _require_utc(evidence.not_before_end, "qualification not-before end")
    if evidence.generated_at < evidence.not_before_end:
        raise ValueError("qualification evidence predates its not-before end")
    gap_ids = [gap.gap_id for gap in evidence.candidate_gaps]
    if len(set(gap_ids)) != len(gap_ids):
        raise ValueError("qualification evidence contains duplicate gap IDs")
    for gap in evidence.candidate_gaps:
        _require_utc(gap.interval_start, "qualification gap start")
        _require_utc(gap.interval_end, "qualification gap end")
        _require_utc(gap.detected_at, "qualification gap detection time")
        if gap.repaired_at is not None:
            _require_utc(gap.repaired_at, "qualification gap repair time")
        if gap.interval_end <= gap.interval_start:
            raise ValueError(f"qualification gap has an invalid interval: {gap.gap_id}")
        if gap.detected_at < evidence.candidate_start or gap.detected_at > evidence.generated_at:
            raise ValueError(
                f"qualification gap is outside the candidate evidence window: {gap.gap_id}"
            )


def _require_metadata_identity(
    metadata: Mapping[str, object], evidence: QualificationEvidence, plan: BackfillPlan
) -> None:
    if metadata["manifest_contract"] != "qtrad-research-bars-v2":
        raise ValueError("research manifest does not use the version-two bars contract")
    requested = _mapping(metadata["requested_interval"], "requested interval")
    if _datetime(requested["start"], "requested interval start") != plan.start:
        raise ValueError("research export start does not match the backfill plan")
    if _datetime(requested["end"], "requested interval end") != plan.end:
        raise ValueError("research export end does not match the backfill plan")
    source = _mapping(metadata["source_snapshot"], "source snapshot")
    if source["kind"] != "verified-capture-snapshot":
        raise ValueError("historical gap review requires a verified capture snapshot")
    if source["capture_source_id"] != evidence.release.capture_source_id:
        raise ValueError("research snapshot does not match the qualification capture source")
    if (
        _datetime(source["source_created_at"], "source snapshot creation time")
        < evidence.generated_at
    ):
        raise ValueError("research snapshot predates the automatic qualification evidence")
    import_sha = source["import_sha256"]
    if not isinstance(import_sha, str) or not _is_sha256(import_sha):
        raise ValueError("research snapshot import hash must be lower-case SHA-256")


def _plan_coverage_points(
    metadata: Mapping[str, object], plan: BackfillPlan, *, allow_empty: bool = False
) -> dict[tuple[str, str], int]:
    historical = _mapping(metadata["historical_coverage"], "historical coverage")
    records = _sequence(historical["records"], "historical coverage records")
    expected = {
        (str(item.instrument_id), basis.value): item for item in plan.items for basis in _BASES
    }
    observed: dict[tuple[str, str], int] = {}
    for value in records:
        record = _mapping(value, "historical coverage record")
        key = (str(record["instrument_id"]), str(record["basis"]))
        if key not in expected or record["detected_by_plan_hash"] != plan.plan_hash:
            continue
        item = expected[key]
        if record["request_completed_at"] is None:
            raise ValueError(f"historical request is not completed by the selected plan: {key}")
        if record["provenance"] != "IG_HISTORICAL" or record["resolution"] != "MINUTE":
            raise ValueError(f"historical coverage has unexpected provenance or resolution: {key}")
        if record["source_listing_id"] != str(item.listing_id):
            raise ValueError(f"historical coverage has a different provider listing: {key}")
        if (
            _datetime(record["source_listing_valid_from"], "source listing valid-from")
            != item.listing_valid_from
            or record["source_listing_metadata_version"] != item.listing_metadata_version
        ):
            raise ValueError(f"historical coverage has a different listing version: {key}")
        if (
            _datetime(record["interval_start"], "historical coverage start") != plan.start
            or _datetime(record["interval_end"], "historical coverage end") != plan.end
        ):
            raise ValueError(f"historical coverage has a different planned interval: {key}")
        points = record["returned_points"]
        if (
            not isinstance(points, int)
            or isinstance(points, bool)
            or points < 0
            or (points == 0 and not allow_empty)
        ):
            raise ValueError(f"historical request has invalid returned points: {key}")
        if points == 0:
            if record["covered_at"] is not None or record["covered_by_plan_hash"] is not None:
                raise ValueError(f"empty historical request falsely claims coverage: {key}")
            if record["observed_points"] is not None:
                raise ValueError(f"empty historical request records covered points: {key}")
        elif (
            record["covered_at"] is None
            or record["covered_by_plan_hash"] != plan.plan_hash
            or record["observed_points"] != points
        ):
            raise ValueError(f"returned historical data is not recorded as coverage: {key}")
        if key in observed:
            raise ValueError(f"historical coverage contains duplicate plan evidence: {key}")
        observed[key] = points
    missing = sorted(set(expected) - set(observed))
    if missing:
        raise ValueError(f"research manifest omits completed plan coverage: {missing}")
    return observed


def _require_live_gap_evidence(
    metadata: Mapping[str, object], evidence: QualificationEvidence
) -> None:
    live = _mapping(metadata["live_gaps"], "live gaps")
    records = _sequence(live["records"], "live gap records")
    by_id: dict[str, Mapping[str, object]] = {}
    for value in records:
        record = _mapping(value, "live gap record")
        gap_id = record["gap_id"]
        if not isinstance(gap_id, str):
            raise TypeError("live gap ID must be a string")
        if gap_id in by_id:
            raise ValueError(f"research manifest contains duplicate live gap evidence: {gap_id}")
        by_id[gap_id] = record
    for gap in evidence.candidate_gaps:
        record = by_id.get(gap.gap_id)
        if record is None:
            raise ValueError(f"research manifest omits qualification gap: {gap.gap_id}")
        if (
            record["instrument_id"] != gap.instrument_id
            or _datetime(record["interval_start"], "live gap start") != gap.interval_start
            or _datetime(record["interval_end"], "live gap end") != gap.interval_end
            or record["reason"] != gap.reason
        ):
            raise ValueError(
                f"research manifest live gap differs from qualification evidence: {gap.gap_id}"
            )


def _basis_evidence(
    *,
    basis: PriceBasis,
    bars: Sequence[MarketBar],
    aligned_start: datetime,
    aligned_end: datetime,
    expected_minutes: int,
    plan_observed_points: int,
) -> HistoricalBasisEvidence:
    selected = tuple(
        sorted((bar for bar in bars if bar.basis is basis), key=lambda bar: bar.interval_start)
    )
    observed_intervals = {bar.interval_start for bar in selected}
    if len(observed_intervals) != len(selected):
        raise ValueError(f"historical bars contain duplicate {basis.value} intervals")
    if any(
        bar.interval_start.second
        or bar.interval_start.microsecond
        or bar.interval_end != bar.interval_start + timedelta(minutes=1)
        for bar in selected
    ):
        raise ValueError(f"historical {basis.value} bars are not one-minute aligned")
    expected_intervals = {
        aligned_start + timedelta(minutes=offset) for offset in range(expected_minutes)
    }
    if any(interval not in expected_intervals for interval in observed_intervals):
        raise ValueError(f"historical {basis.value} bars fall outside the aligned gap interval")
    return HistoricalBasisEvidence(
        basis=basis.value,
        expected_minute_intervals=expected_minutes,
        returned_points_in_gap=len(selected),
        complete_interval_coverage=observed_intervals == expected_intervals,
        first_interval_start=selected[0].interval_start if selected else None,
        last_interval_end=selected[-1].interval_end if selected else None,
        semantic_bar_sha256=semantic_bar_hash(selected) if selected else None,
        plan_observed_points=plan_observed_points,
    )


def _validate_artifact_hash(
    artifact: QualificationGapHistoryArtifact | QualificationGapPlanSetHistoryArtifact,
) -> None:
    identity = artifact.model_dump(mode="json", by_alias=True)
    recorded = identity.pop("artifact_sha256")
    if _sha256_json(identity) != recorded:
        raise ValueError("historical gap evidence hash does not match its canonical content")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field} keys must be strings")
    return cast(Mapping[str, object], value)


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be an array")
    return value


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_utc(parsed, field)
    return parsed


def _require_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field} must use timezone-aware UTC")


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
