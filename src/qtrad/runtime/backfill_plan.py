"""Strict JSON boundary for reviewed historical backfill plans."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from qtrad.application.backfill_planning import backfill_plan_payload, verify_backfill_plan_hash
from qtrad.domain.historical_coverage import (
    BackfillPlan,
    BackfillPlanItem,
    BackfillQuotaEvidence,
    HistoricalResolution,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId

_MAX_BACKFILL_PLAN_BYTES = 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _QuotaModel(_StrictModel):
    allowance_name: str = Field(min_length=1, max_length=200)
    remaining_points: int = Field(gt=0)
    observed_at: datetime
    reserve_fraction: str = Field(pattern=r"^(0(?:\.\d+)?|1(?:\.0+)?)$")


class _ItemModel(_StrictModel):
    instrument_id: str = Field(min_length=3, max_length=200)
    listing_id: str = Field(min_length=5, max_length=500)
    listing_valid_from: datetime
    listing_metadata_version: str = Field(min_length=1, max_length=256)


class _PlanModel(_StrictModel):
    schema_version: Literal[1]
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    universe_name: str = Field(min_length=1, max_length=64)
    universe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    start: datetime
    end: datetime
    resolution: Literal["MINUTE"]
    request_chunk_points: int = Field(ge=1, le=1000)
    quota: _QuotaModel
    items: list[_ItemModel] = Field(min_length=1, max_length=100)


def decode_backfill_plan(value: str) -> BackfillPlan:
    if len(value.encode("utf-8")) > _MAX_BACKFILL_PLAN_BYTES:
        raise ValueError("backfill plan exceeds the 1 MiB limit")
    model = _PlanModel.model_validate_json(value)
    plan = BackfillPlan(
        plan_hash=model.plan_hash,
        universe_name=model.universe_name,
        universe_hash=model.universe_hash,
        created_at=model.created_at,
        start=model.start,
        end=model.end,
        resolution=HistoricalResolution(model.resolution),
        request_chunk_points=model.request_chunk_points,
        quota=BackfillQuotaEvidence(
            allowance_name=model.quota.allowance_name,
            remaining_points=model.quota.remaining_points,
            observed_at=model.quota.observed_at,
            reserve_fraction=Decimal(model.quota.reserve_fraction),
        ),
        items=tuple(
            BackfillPlanItem(
                instrument_id=InstrumentId(item.instrument_id),
                listing_id=_listing_id(item.listing_id),
                listing_valid_from=item.listing_valid_from,
                listing_metadata_version=item.listing_metadata_version,
            )
            for item in model.items
        ),
    )
    verify_backfill_plan_hash(plan)
    return plan


def load_backfill_plan(path: Path) -> BackfillPlan:
    return decode_backfill_plan(path.read_text(encoding="utf-8"))


def write_backfill_plan(path: Path, plan: BackfillPlan) -> None:
    if path.exists():
        raise FileExistsError(f"backfill plan output already exists: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(f"backfill plan output directory does not exist: {path.parent}")
    encoded = json.dumps(backfill_plan_payload(plan), sort_keys=True, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8")


def _listing_id(value: str) -> ProviderListingId:
    parts = value.split(":", maxsplit=2)
    if len(parts) != 3:
        raise ValueError("backfill plan listing ID must contain provider, environment and ID")
    return ProviderListingId(*parts)
