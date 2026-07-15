"""Deterministic planning for explicit abandoned-run reconciliation."""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime

from qtrad.domain.events import JsonValue
from qtrad.domain.modes import BrokerEnvironment
from qtrad.domain.operations import (
    RUN_RECONCILIATION_FINISHED_AT_BASIS,
    RUN_RECONCILIATION_REASON,
    RUN_RECONCILIATION_STATUS,
    RunReconciliationPlan,
    RunReconciliationTarget,
)


def build_run_reconciliation_plan(
    *,
    targets: Sequence[RunReconciliationTarget],
    created_at: datetime,
    cutoff: datetime,
    capture_source_id: str,
    database_name: str,
    universe_name: str,
    configuration_hash: str,
    application_version: str,
    application_image: str,
    environment: BrokerEnvironment,
) -> RunReconciliationPlan:
    ordered = tuple(sorted(targets, key=lambda item: (item.started_at, str(item.run_id))))
    unhashed = _run_reconciliation_plan_payload(
        plan_hash=None,
        created_at=created_at,
        cutoff=cutoff,
        capture_source_id=capture_source_id,
        database_name=database_name,
        universe_name=universe_name,
        configuration_hash=configuration_hash,
        application_version=application_version,
        application_image=application_image,
        environment=environment,
        terminal_status=RUN_RECONCILIATION_STATUS,
        reason_code=RUN_RECONCILIATION_REASON,
        finished_at_basis=RUN_RECONCILIATION_FINISHED_AT_BASIS,
        targets=ordered,
    )
    return RunReconciliationPlan(
        plan_hash=_sha256_json(unhashed),
        created_at=created_at,
        cutoff=cutoff,
        capture_source_id=capture_source_id,
        database_name=database_name,
        universe_name=universe_name,
        configuration_hash=configuration_hash,
        application_version=application_version,
        application_image=application_image,
        environment=environment,
        terminal_status=RUN_RECONCILIATION_STATUS,
        reason_code=RUN_RECONCILIATION_REASON,
        finished_at_basis=RUN_RECONCILIATION_FINISHED_AT_BASIS,
        targets=ordered,
    )


def run_reconciliation_plan_payload(plan: RunReconciliationPlan) -> dict[str, JsonValue]:
    return _run_reconciliation_plan_payload(
        plan_hash=plan.plan_hash,
        created_at=plan.created_at,
        cutoff=plan.cutoff,
        capture_source_id=plan.capture_source_id,
        database_name=plan.database_name,
        universe_name=plan.universe_name,
        configuration_hash=plan.configuration_hash,
        application_version=plan.application_version,
        application_image=plan.application_image,
        environment=plan.environment,
        terminal_status=plan.terminal_status,
        reason_code=plan.reason_code,
        finished_at_basis=plan.finished_at_basis,
        targets=plan.targets,
    )


def verify_run_reconciliation_plan_hash(plan: RunReconciliationPlan) -> None:
    payload = run_reconciliation_plan_payload(plan)
    observed_hash = str(payload.pop("plan_hash"))
    if observed_hash != _sha256_json(payload):
        raise ValueError("run reconciliation plan hash does not match its canonical content")


def _run_reconciliation_plan_payload(
    *,
    plan_hash: str | None,
    created_at: datetime,
    cutoff: datetime,
    capture_source_id: str,
    database_name: str,
    universe_name: str,
    configuration_hash: str,
    application_version: str,
    application_image: str,
    environment: BrokerEnvironment,
    terminal_status: str,
    reason_code: str,
    finished_at_basis: str,
    targets: Sequence[RunReconciliationTarget],
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "created_at": _utc_text(created_at),
        "cutoff": _utc_text(cutoff),
        "capture_source_id": capture_source_id,
        "database_name": database_name,
        "universe_name": universe_name,
        "configuration_hash": configuration_hash,
        "application_version": application_version,
        "application_image": application_image,
        "environment": environment.value,
        "terminal_status": terminal_status,
        "reason_code": reason_code,
        "finished_at_basis": finished_at_basis,
        "targets": [
            {"run_id": str(target.run_id), "started_at": _utc_text(target.started_at)}
            for target in targets
        ],
    }
    if plan_hash is not None:
        return {"plan_hash": plan_hash, **payload}
    return payload


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
