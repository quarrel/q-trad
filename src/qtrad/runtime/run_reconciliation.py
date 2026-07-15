"""Strict JSON boundary for reviewed run-reconciliation plans."""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from qtrad.application.run_reconciliation import (
    run_reconciliation_plan_payload,
    verify_run_reconciliation_plan_hash,
)
from qtrad.domain.identifiers import RunId
from qtrad.domain.modes import BrokerEnvironment
from qtrad.domain.operations import RunReconciliationPlan, RunReconciliationTarget

_MAX_RUN_RECONCILIATION_PLAN_BYTES = 256 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _TargetModel(_StrictModel):
    run_id: UUID
    started_at: datetime


class _PlanModel(_StrictModel):
    schema_version: Literal[1]
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    cutoff: datetime
    capture_source_id: str = Field(min_length=1, max_length=64)
    database_name: str = Field(min_length=1, max_length=63)
    universe_name: str = Field(min_length=1, max_length=64)
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_version: str = Field(min_length=1, max_length=64)
    application_image: str = Field(
        pattern=r"^[^\s]+@sha256:[0-9a-f]{64}$",
        max_length=500,
    )
    environment: Literal["IG_DEMO"]
    terminal_status: Literal["FAILED"]
    reason_code: Literal["PRE_CANDIDATE_PROCESS_INTERRUPTED"]
    finished_at_basis: Literal["OPERATOR_ASSERTED_CUTOFF_UPPER_BOUND"]
    targets: list[_TargetModel] = Field(min_length=1, max_length=100)


def decode_run_reconciliation_plan(value: str) -> RunReconciliationPlan:
    if len(value.encode("utf-8")) > _MAX_RUN_RECONCILIATION_PLAN_BYTES:
        raise ValueError("run reconciliation plan exceeds the 256 KiB limit")
    model = _PlanModel.model_validate_json(value)
    plan = RunReconciliationPlan(
        plan_hash=model.plan_hash,
        created_at=model.created_at,
        cutoff=model.cutoff,
        capture_source_id=model.capture_source_id,
        database_name=model.database_name,
        universe_name=model.universe_name,
        configuration_hash=model.configuration_hash,
        application_version=model.application_version,
        application_image=model.application_image,
        environment=BrokerEnvironment(model.environment),
        terminal_status=model.terminal_status,
        reason_code=model.reason_code,
        finished_at_basis=model.finished_at_basis,
        targets=tuple(
            RunReconciliationTarget(run_id=RunId(target.run_id), started_at=target.started_at)
            for target in model.targets
        ),
    )
    verify_run_reconciliation_plan_hash(plan)
    return plan


def load_run_reconciliation_plan(path: Path) -> RunReconciliationPlan:
    return decode_run_reconciliation_plan(path.read_text(encoding="utf-8"))


def write_run_reconciliation_plan(path: Path, plan: RunReconciliationPlan) -> None:
    if path.exists():
        raise FileExistsError(f"run reconciliation plan output already exists: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(
            f"run reconciliation plan output directory does not exist: {path.parent}"
        )
    encoded = json.dumps(run_reconciliation_plan_payload(plan), sort_keys=True, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8")
