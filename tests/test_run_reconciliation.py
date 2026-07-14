import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from qtrad.application.run_reconciliation import (
    build_run_reconciliation_plan,
    run_reconciliation_plan_payload,
)
from qtrad.domain.identifiers import RunId
from qtrad.domain.modes import BrokerEnvironment
from qtrad.domain.operations import RunReconciliationPlan, RunReconciliationTarget
from qtrad.runtime.run_reconciliation import (
    decode_run_reconciliation_plan,
    load_run_reconciliation_plan,
    write_run_reconciliation_plan,
)

CUTOFF = datetime(2026, 7, 14, 3, 5, 33, 653928, tzinfo=UTC)
CREATED_AT = datetime(2026, 7, 14, 16, tzinfo=UTC)
APPLICATION_IMAGE = "syd.ocir.io/example/qtrad@sha256:" + "b" * 64


def _targets() -> tuple[RunReconciliationTarget, ...]:
    return (
        RunReconciliationTarget(
            RunId(UUID("00000000-0000-0000-0000-000000000001")),
            CUTOFF - timedelta(minutes=2),
        ),
        RunReconciliationTarget(
            RunId(UUID("00000000-0000-0000-0000-000000000002")),
            CUTOFF - timedelta(minutes=1),
        ),
    )


def _plan(
    targets: tuple[RunReconciliationTarget, ...] | None = None,
) -> RunReconciliationPlan:
    return build_run_reconciliation_plan(
        targets=targets or _targets(),
        created_at=CREATED_AT,
        cutoff=CUTOFF,
        capture_source_id="oci-sydney-capture-1",
        database_name="qtrad_capture",
        universe_name="capture-v1",
        configuration_hash="a" * 64,
        application_version="0.1.0",
        application_image=APPLICATION_IMAGE,
        environment=BrokerEnvironment.IG_DEMO,
    )


def test_run_reconciliation_plan_is_deterministic_and_canonically_ordered() -> None:
    plan = _plan(tuple(reversed(_targets())))
    repeat = _plan()

    assert plan == repeat
    assert plan.targets == _targets()
    assert run_reconciliation_plan_payload(plan) == {
        "plan_hash": plan.plan_hash,
        "schema_version": 1,
        "created_at": "2026-07-14T16:00:00Z",
        "cutoff": "2026-07-14T03:05:33.653928Z",
        "capture_source_id": "oci-sydney-capture-1",
        "database_name": "qtrad_capture",
        "universe_name": "capture-v1",
        "configuration_hash": "a" * 64,
        "application_version": "0.1.0",
        "application_image": APPLICATION_IMAGE,
        "environment": "IG_DEMO",
        "terminal_status": "FAILED",
        "reason_code": "PRE_CANDIDATE_PROCESS_INTERRUPTED",
        "finished_at_basis": "OPERATOR_ASSERTED_CUTOFF_UPPER_BOUND",
        "targets": [
            {
                "run_id": "00000000-0000-0000-0000-000000000001",
                "started_at": "2026-07-14T03:03:33.653928Z",
            },
            {
                "run_id": "00000000-0000-0000-0000-000000000002",
                "started_at": "2026-07-14T03:04:33.653928Z",
            },
        ],
    }


def test_run_reconciliation_plan_round_trip_is_bounded_hash_verified_and_non_overwriting(
    tmp_path: Path,
) -> None:
    plan = _plan()
    path = tmp_path / "reconciliation.json"

    write_run_reconciliation_plan(path, plan)

    assert load_run_reconciliation_plan(path) == plan
    with pytest.raises(FileExistsError):
        write_run_reconciliation_plan(path, plan)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["targets"][0]["started_at"] = "2026-07-14T03:02:33.653928Z"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash does not match"):
        load_run_reconciliation_plan(path)

    oversized = json.dumps({"padding": "x" * (256 * 1024)})
    with pytest.raises(ValueError, match="256 KiB limit"):
        decode_run_reconciliation_plan(oversized)


def test_run_reconciliation_plan_rejects_invalid_time_or_target_sets() -> None:
    with pytest.raises(ValueError, match="created before its cutoff"):
        build_run_reconciliation_plan(
            targets=_targets(),
            created_at=CUTOFF - timedelta(seconds=1),
            cutoff=CUTOFF,
            capture_source_id="source",
            database_name="qtrad_capture",
            universe_name="capture-v1",
            configuration_hash="a" * 64,
            application_version="0.1.0",
            application_image=APPLICATION_IMAGE,
            environment=BrokerEnvironment.IG_DEMO,
        )

    at_cutoff = RunReconciliationTarget(_targets()[0].run_id, CUTOFF)
    with pytest.raises(ValueError, match="must start before the cutoff"):
        _plan((at_cutoff,))

    with pytest.raises(ValueError, match="target IDs must be unique"):
        _plan((_targets()[0], _targets()[0]))

    with pytest.raises(ValueError, match="application image must be pinned"):
        replace(_plan(), application_image="example.invalid/qtrad:latest")


def test_run_reconciliation_semantic_tamper_fails_even_after_rehash() -> None:
    payload = run_reconciliation_plan_payload(_plan())
    payload["terminal_status"] = "STOPPED"
    unhashed = {key: value for key, value in payload.items() if key != "plan_hash"}
    payload["plan_hash"] = hashlib.sha256(
        json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    with pytest.raises(ValueError, match="terminal_status"):
        decode_run_reconciliation_plan(json.dumps(payload))
