"""Hash-bound sparse plan set for qualification-gap historical queries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from qtrad.domain.historical_coverage import BackfillPlan
from qtrad.runtime.backfill_plan import load_backfill_plan

_MAX_PLAN_SET_BYTES = 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class QualificationGapPlanEntry(_StrictModel):
    file: str = Field(min_length=1, max_length=200)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    gap_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    requested_points: int = Field(gt=0)

    @field_validator("file")
    @classmethod
    def _safe_sibling_name(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("qualification plan entry must be a sibling file name")
        return value


class QualificationGapPlanSet(_StrictModel):
    schema_name: Literal["qtrad-qualification-gap-plan-set-v1"] = Field(alias="schema")
    plan_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualification_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_import_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_source_id: str = Field(min_length=1, max_length=200)
    universe_name: str = Field(min_length=1, max_length=64)
    universe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    remaining_allowance: int = Field(gt=0)
    reserve_points: int = Field(ge=0)
    requested_points: int = Field(gt=0)
    entries: tuple[QualificationGapPlanEntry, ...] = Field(min_length=1, max_length=100)


def build_qualification_gap_plan_set(
    *,
    qualification_evidence_sha256: str,
    snapshot_import_sha256: str,
    capture_source_id: str,
    universe_name: str,
    universe_hash: str,
    created_at: datetime,
    remaining_allowance: int,
    reserve_points: int,
    entries: Sequence[QualificationGapPlanEntry],
) -> QualificationGapPlanSet:
    """Build one aggregate quota and exact-gap identity over normal backfill plans."""

    requested_points = sum(entry.requested_points for entry in entries)
    if requested_points > remaining_allowance - reserve_points:
        raise ValueError("qualification gap plan set exceeds quota after reserved allowance")
    gap_ids = [gap_id for entry in entries for gap_id in entry.gap_ids]
    if len(set(gap_ids)) != len(gap_ids):
        raise ValueError("qualification gap plan set contains duplicate gap IDs")
    draft = QualificationGapPlanSet(
        schema="qtrad-qualification-gap-plan-set-v1",
        plan_set_hash="0" * 64,
        qualification_evidence_sha256=qualification_evidence_sha256,
        snapshot_import_sha256=snapshot_import_sha256,
        capture_source_id=capture_source_id,
        universe_name=universe_name,
        universe_hash=universe_hash,
        created_at=created_at,
        remaining_allowance=remaining_allowance,
        reserve_points=reserve_points,
        requested_points=requested_points,
        entries=tuple(entries),
    )
    identity = draft.model_dump(mode="json", by_alias=True)
    del identity["plan_set_hash"]
    return draft.model_copy(update={"plan_set_hash": _sha256_json(identity)})


def write_qualification_gap_plan_set(path: Path, plan_set: QualificationGapPlanSet) -> None:
    """Write a non-overwriting plan-set envelope after its plan files exist."""

    if path.exists() or path.is_symlink():
        raise FileExistsError(f"qualification gap plan-set output already exists: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(
            f"qualification gap plan-set directory does not exist: {path.parent}"
        )
    _validate_plan_set(path.parent, plan_set)
    encoded = (
        json.dumps(plan_set.model_dump(mode="json", by_alias=True), sort_keys=True, indent=2) + "\n"
    )
    if len(encoded.encode("utf-8")) > _MAX_PLAN_SET_BYTES:
        raise ValueError("qualification gap plan set exceeds the 1 MiB limit")
    path.write_text(encoded, encoding="utf-8")


def load_qualification_gap_plan_set(
    path: Path,
) -> tuple[QualificationGapPlanSet, tuple[BackfillPlan, ...]]:
    """Load and verify a plan set plus every exact sibling plan it binds."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("qualification gap plan set must be a regular non-symlink file")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_PLAN_SET_BYTES:
        raise ValueError("qualification gap plan set exceeds the 1 MiB limit")
    plan_set = QualificationGapPlanSet.model_validate_json(encoded)
    plans = _validate_plan_set(path.parent, plan_set)
    return plan_set, plans


def _validate_plan_set(
    directory: Path, plan_set: QualificationGapPlanSet
) -> tuple[BackfillPlan, ...]:
    identity = plan_set.model_dump(mode="json", by_alias=True)
    del identity["plan_set_hash"]
    if _sha256_json(identity) != plan_set.plan_set_hash:
        raise ValueError("qualification gap plan-set hash does not match its canonical content")
    if plan_set.requested_points != sum(entry.requested_points for entry in plan_set.entries):
        raise ValueError("qualification gap plan-set requested-point total does not match entries")
    if plan_set.requested_points > plan_set.remaining_allowance - plan_set.reserve_points:
        raise ValueError("qualification gap plan set exceeds quota after reserved allowance")
    gap_ids = [gap_id for entry in plan_set.entries for gap_id in entry.gap_ids]
    if len(set(gap_ids)) != len(gap_ids):
        raise ValueError("qualification gap plan set contains duplicate gap IDs")
    plans: list[BackfillPlan] = []
    for entry in plan_set.entries:
        plan_path = directory / entry.file
        if plan_path.is_symlink() or not plan_path.is_file():
            raise ValueError(f"qualification gap plan is not a regular sibling file: {entry.file}")
        plan = load_backfill_plan(plan_path)
        if plan.plan_hash != entry.plan_hash or plan.requested_points != entry.requested_points:
            raise ValueError(f"qualification gap plan differs from plan-set entry: {entry.file}")
        if (
            plan.universe_name != plan_set.universe_name
            or plan.universe_hash != plan_set.universe_hash
        ):
            raise ValueError(
                f"qualification gap plan has different universe identity: {entry.file}"
            )
        plans.append(plan)
    return tuple(plans)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
