"""Outcome-safe R4.C execution harness.

Authenticated runs use a canonical output layout: ``input/`` contains the
LAB-0-bound manifest and foundation/support capsules, ``register/`` contains
closed development records, ``model/`` contains serialized family states,
``support/`` and ``prediction/`` contain terminal artefacts, and ``metrics/``
contains the final report. Every artifact is create-only and G0-bound.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from .attempt_artifacts import (
    AttemptIdentity,
    CreateOnlyAttemptJournal,
    JournalRecord,
    preserve_failed_staging,
    publish_attempt_bundle,
    seal_attempt_staging,
    strict_json_object,
    verify_attempt_bundle,
)
from .cuda_boundary import CUBLAS_WORKSPACE_CONFIG, PRE_TORCH_ENVIRONMENT_CONFIGURED
from .foundation import (
    ALL_INSTRUMENTS,
    DEVELOPMENT_BLOCKS,
    MANIFEST_SHA256,
    TERMINAL_START,
    load_parent_rows,
    reconstruct_authenticated_linear_forecasts,
)
from .grouped import configure_deterministic_cuda
from .preparation_reference import (
    preparation_entry,
    preparation_g0,
    preparation_root,
    reference_process,
)
from .runtime import (
    EXPERIMENT_CLASS,
    FITTED_FAMILY_IDS,
    MANIFEST_IDENTITY,
    OUTPUT_POLICY,
    PARENT_IDENTITY,
    PRIMARY_SCHEDULE,
    PRIMARY_SEEDS,
    RESIDUAL_FOUNDATION_IDENTITY,
    RUNTIME_VERSION,
    SOURCE_CLASS,
    TERMINAL_PREDICATE,
    TIMESTAMP_MATERIALISATION_POLICY,
    CreateOnlyArtifacts,
    FrozenRuntimeConfig,
    _canonical,
    _sha256,
    model_parameter_count,
    require_cuda,
    training_preprocessor_identity,
)
from .supervisor import wrapper_receipt_identity
from .tensor import _as_utc, _masked_tensor_identity
from .terminal_support import ALLOWED_TERMINAL_COLUMNS

if not PRE_TORCH_ENVIRONMENT_CONFIGURED:
    raise RuntimeError("deterministic CUDA process boundary was not configured")

_EXECUTION_VERSION = "R4.C-G0-EXECUTION-HARNESS-V1"
_DEVELOPMENT_STAGES = ("DEV_2", "DEV_3")
_TERMINAL_STAGE = "TERMINAL_FORMER_HOLDOUT"
_aggregate_outcomes_loaded: ContextVar[bool] = ContextVar(
    "r4_aggregate_outcomes_loaded", default=False
)
_prediction_attempt_started: ContextVar[bool] = ContextVar(
    "r4_prediction_attempt_started", default=False
)
_development_attempt_started: ContextVar[bool] = ContextVar(
    "r4_development_attempt_started", default=False
)
_cache_build_allowed: ContextVar[bool] = ContextVar("r4_cache_build_allowed", default=True)
_development_epoch_id: ContextVar[str | None] = ContextVar("r4_development_epoch_id", default=None)
_development_cache_receipt: ContextVar[str | None] = ContextVar(
    "r4_development_cache_receipt", default=None
)


class _ImplementationInvariantError(ValueError):
    """Raised only when execution evidence proves an implementation defect."""


_EXPECTED_DEVELOPMENT_SLOTS = tuple(
    slot for slot in PRIMARY_SCHEDULE if slot[2] in _DEVELOPMENT_STAGES
)
_SOURCE_FILES = (
    "experiments/r4_residual_graph/__init__.py",
    "experiments/r4_residual_graph/execution.py",
    "experiments/r4_residual_graph/foundation.py",
    "experiments/r4_residual_graph/graph.py",
    "experiments/r4_residual_graph/runtime.py",
    "experiments/r4_residual_graph/tensor.py",
    "experiments/r4_residual_graph/terminal_support.py",
    "uv.lock",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _application_identity(root: Path) -> str:
    digests = {
        relative: _file_digest(root / relative)
        for relative in _SOURCE_FILES
        if (root / relative).is_file()
    }
    if len(digests) != len(_SOURCE_FILES):
        missing = sorted(set(_SOURCE_FILES) - set(digests))
        raise FileNotFoundError(f"G0 application inputs are missing: {missing}")
    return _sha256(digests)


def _code_head(root: Path) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "--verify", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    head = completed.stdout.strip()
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise ValueError("G0 code head is not a canonical commit identity")
    return head


@dataclass(frozen=True)
class G0ExecutionIdentity:
    """Cryptographic join for one immutable G0 execution root."""

    code_head: str
    lock_identity: str
    application_identity: str
    runtime_identity: str
    config_identity: str
    register_identity: str
    output_root_identity: str
    release_identity: str = RUNTIME_VERSION
    parent_identity: str = PARENT_IDENTITY
    harness_identity: str = _EXECUTION_VERSION

    def __post_init__(self) -> None:
        if len(self.code_head) != 40 or any(
            character not in "0123456789abcdef" for character in self.code_head
        ):
            raise ValueError("G0 code head must be a lowercase 40-hex commit identity")
        for field in (
            "lock_identity",
            "application_identity",
            "runtime_identity",
            "config_identity",
            "register_identity",
            "output_root_identity",
        ):
            value = getattr(self, field)
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"G0 {field} must be a SHA-256 identity")
        if self.release_identity != RUNTIME_VERSION:
            raise ValueError("G0 release identity is not canonical")
        if self.parent_identity != PARENT_IDENTITY:
            raise ValueError("G0 parent identity is not canonical")
        if self.harness_identity != _EXECUTION_VERSION:
            raise ValueError("G0 harness identity is not canonical")

    @property
    def identity(self) -> str:
        return _sha256(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"identity": self.identity}


def capture_g0_identity(
    output_root: Path, config: FrozenRuntimeConfig | None = None
) -> G0ExecutionIdentity:
    """Capture exact code, lock, runtime, config, register and output-root identities."""

    root = _repository_root()
    active_config = config or FrozenRuntimeConfig()
    resolved_output = Path(output_root).resolve()
    return G0ExecutionIdentity(
        code_head=_code_head(root),
        lock_identity=_file_digest(root / "uv.lock"),
        application_identity=_application_identity(root),
        runtime_identity=active_config.identity,
        config_identity=active_config.identity,
        register_identity=active_config.register_identity,
        output_root_identity=_sha256({"root": str(resolved_output), "policy": OUTPUT_POLICY}),
    )


def _identity_path(root: Path) -> Path:
    return root / "config" / "g0-identity.json"


def _write_json_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        handle.write("\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required execution artifact is missing: {path}")
    payload = strict_json_object(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"execution artifact is not an object: {path}")
    return payload


def _require_identity(
    output_root: Path, config: FrozenRuntimeConfig | None = None
) -> G0ExecutionIdentity:
    root = Path(output_root).resolve()
    recorded = _read_json(_identity_path(root))
    current = capture_g0_identity(root, config)
    if recorded.get("identity") != current.identity or recorded != current.to_dict():
        raise ValueError("G0 identity drift detected")
    return current


def _retain_preflight_failure(
    output_root: Path,
    config: FrozenRuntimeConfig | None,
    phase: str,
    error: BaseException,
    identity: G0ExecutionIdentity | None = None,
) -> None:
    try:
        write_outcome_blind_closure_report(
            Path(output_root).resolve(),
            status=_failure_status(error),
            reason=str(error),
            identity=identity,
            config=config,
            phase=phase,
            outcomes_loaded=False,
        )
    except Exception as retention_error:
        error.add_note(f"closure retention failed: {retention_error}")


def _fixture_prepare_execution(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
    manifest_path: Path | None = None,
) -> G0ExecutionIdentity:
    """Prepare a synthetic fixture root for explicitly capable tests."""
    root = Path(output_root).resolve()
    if _is_real_execution(root):
        raise ValueError("fixture capability cannot prepare authenticated execution")
    if manifest_path is not None:
        raise ValueError("fixture preparation does not accept an authenticated manifest")

    if root.exists() and any(root.iterdir()):
        raise FileExistsError("G0 preparation requires a new empty output root")
    identity = capture_g0_identity(root, config)
    _write_json_once(_identity_path(root), identity.to_dict())
    artifacts = CreateOnlyArtifacts(root)
    artifacts.create_json(
        "support",
        "prepared-foundation",
        {
            "artifact_type": "R4.C_FIXTURE_AUTHENTICATED_FOUNDATION",
            "g0_identity": identity.identity,
            "parent_identity": PARENT_IDENTITY,
            "runtime_identity": identity.runtime_identity,
            "outcome_blind": True,
            "outcomes_loaded": False,
            "terminal_boundary": TERMINAL_START.isoformat(),
        },
    )
    artifacts.create_json(
        "support",
        "prepared-support",
        {
            "artifact_type": "R4.C_FIXTURE_OUTCOME_BLIND_SUPPORT",
            "g0_identity": identity.identity,
            "support_identity": RESIDUAL_FOUNDATION_IDENTITY,
            "allowed_columns": sorted(ALLOWED_TERMINAL_COLUMNS),
            "outcome_blind": True,
            "outcomes_loaded": False,
        },
    )
    return identity


def prepare_execution(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
    manifest_path: Path | None = None,
) -> G0ExecutionIdentity:
    """Prepare authenticated execution inputs from the frozen manifest."""
    if manifest_path is None:
        raise ValueError("authenticated preparation requires manifest_path")
    return prepare_authenticated_execution(
        Path(output_root).resolve(), manifest_path=manifest_path, config=config
    )


def _parse_slot(value: str) -> tuple[str, int, str]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("slot must be FAMILY:SEED:STAGE")
    family, seed_text, stage = parts
    try:
        seed = int(seed_text)
    except ValueError as exc:
        raise ValueError("slot seed must be an integer") from exc
    slot = (family, seed, stage)
    if slot not in PRIMARY_SCHEDULE:
        raise ValueError("slot is outside the exact frozen primary schedule")
    return slot


def _attempt_schedule() -> tuple[tuple[str, int, str], ...]:
    terminal = tuple(
        (family, seed, _TERMINAL_STAGE) for family in FITTED_FAMILY_IDS for seed in PRIMARY_SEEDS
    )
    return PRIMARY_SCHEDULE + terminal


def _journal_records(root: Path) -> tuple[JournalRecord, ...]:
    return CreateOnlyAttemptJournal(root).ordered_records(_attempt_schedule())


def _slot_journal_records(root: Path, slot_id: str) -> tuple[JournalRecord, ...]:
    return tuple(
        record for record in _journal_records(root) if dict(record["attempt"])["slot_id"] == slot_id
    )


def _fixture_run_development_slots(
    output_root: Path,
    *,
    slots: tuple[tuple[str, int, str], ...] | None = None,
    all_development: bool = False,
    config: FrozenRuntimeConfig | None = None,
) -> tuple[str, ...]:
    """Record one or more synthetic PRIMARY development slots, never terminal outcomes."""

    identity = _require_identity(output_root, config)
    root = Path(output_root).resolve()
    selected = (
        _EXPECTED_DEVELOPMENT_SLOTS
        if all_development
        else (slots or (_EXPECTED_DEVELOPMENT_SLOTS[0],))
    )
    if _is_real_execution(root):
        raise ValueError("fixture capability cannot run against authenticated execution")
    if not selected:
        raise ValueError("at least one development slot is required")
    if any(slot not in _EXPECTED_DEVELOPMENT_SLOTS for slot in selected):
        raise ValueError("development stage accepts only DEV_2 and DEV_3 slots")
    if len(set(selected)) != len(selected):
        raise ValueError("development slots must be unique")
    root = Path(output_root).resolve()
    journal = CreateOnlyAttemptJournal(root)
    fixture_foundation_identity = _sha256(
        {"g0": identity.identity, "fixture": "development-foundation"}
    )
    for family, seed, stage in selected:
        slot_id = f"{family}:{seed}:{stage}"
        attempt = AttemptIdentity(
            release_identity=RUNTIME_VERSION,
            g0_identity=identity.identity,
            output_root=str(root),
            slot_id=slot_id,
            family_id=family,
            seed=seed,
            stage=stage,
            attempt=_next_attempt(journal, slot_id),
            mode="PRIMARY",
        )
        journal.append(
            attempt,
            "STARTED",
            {
                "input_identity": RESIDUAL_FOUNDATION_IDENTITY,
                "foundation_content_identity": fixture_foundation_identity,
            },
        )
        staging = root / "staging" / "attempt" / attempt.identity
        staging.mkdir(parents=True)
        _write_json_once(
            staging / "result.json",
            {
                "artifact_type": "R4.C_FIXTURE_DEVELOPMENT_SLOT",
                "slot_id": slot_id,
                "g0_identity": identity.identity,
                "status": "SUCCEEDED",
                "outcome_blind": True,
                "outcomes_loaded": False,
            },
        )
        seal = seal_attempt_staging(staging, attempt, required_paths=("result.json",))
        bundle = root / "attempts" / slot_id / f"attempt-{attempt.attempt}"
        publish_attempt_bundle(staging, bundle, attempt)
        journal.append(
            attempt,
            "SUCCEEDED",
            {
                "bundle": bundle.relative_to(root).as_posix(),
                "seal_identity": seal["seal_identity"],
            },
        )
    return tuple(f"{family}:{seed}:{stage}" for family, seed, stage in selected)


@reference_process
def run_development_slots(
    output_root: Path,
    *,
    slots: tuple[tuple[str, int, str], ...] | None = None,
    all_development: bool = False,
    config: FrozenRuntimeConfig | None = None,
) -> tuple[str, ...]:
    root = Path(output_root).resolve()
    try:
        identity = _require_identity(root, config)
    except FileExistsError:
        raise
    except Exception as exc:
        _retain_preflight_failure(root, config, "DEVELOPMENT_PRE_ATTEMPT_VALIDATION", exc)
        raise
    try:
        selected = (
            _EXPECTED_DEVELOPMENT_SLOTS
            if all_development
            else (slots or (_EXPECTED_DEVELOPMENT_SLOTS[0],))
        )
        if not selected:
            raise ValueError("at least one development slot is required")
        if any(slot not in _EXPECTED_DEVELOPMENT_SLOTS for slot in selected):
            raise ValueError("development stage accepts only DEV_2 and DEV_3 slots")
        if len(set(selected)) != len(selected):
            raise ValueError("development slots must be unique")
    except FileExistsError:
        raise
    except Exception as exc:
        _retain_preflight_failure(root, config, "DEVELOPMENT_PRE_ATTEMPT_VALIDATION", exc, identity)
        raise
    attempt_token = _development_attempt_started.set(False)
    try:
        return run_authenticated_development_slots(root, selected, config, identity)
    except Exception as exc:
        phase = (
            "DEVELOPMENT_ATTEMPT"
            if _development_attempt_started.get()
            else "DEVELOPMENT_PRE_ATTEMPT_VALIDATION"
        )
        _retain_preflight_failure(root, config, phase, exc, identity)
        raise
    finally:
        _development_attempt_started.reset(attempt_token)


def _fixture_close_development_register(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    """Cryptographically close the complete DEV_2/DEV_3 register."""

    identity = _require_identity(output_root, config)
    root = Path(output_root).resolve()
    if _is_real_execution(root):
        raise ValueError("fixture capability cannot close authenticated execution")
    expected_ids = {
        f"{family}:{seed}:{stage}" for family, seed, stage in _EXPECTED_DEVELOPMENT_SLOTS
    }
    records = _journal_records(root)
    succeeded = {
        str(dict(record["attempt"])["slot_id"])
        for record in records
        if record["status"] == "SUCCEEDED"
        and dict(record["attempt"])["mode"] == "PRIMARY"
        and dict(record["attempt"])["stage"] in _DEVELOPMENT_STAGES
    }
    if succeeded != expected_ids:
        missing = sorted(expected_ids - succeeded)
        extra = sorted(succeeded - expected_ids)
        raise ValueError(f"development register is incomplete (missing={missing}, extra={extra})")
    artifact_hashes: dict[str, str] = {}
    for slot_id in sorted(expected_ids):
        succeeded_record = next(
            record
            for record in records
            if record["status"] == "SUCCEEDED" and dict(record["attempt"])["slot_id"] == slot_id
        )
        path = root / str(dict(succeeded_record["payload"])["bundle"]) / "result.json"
        if not path.is_file():
            raise ValueError(f"development slot artifact is missing: {path}")
        artifact_hashes[slot_id] = _file_digest(path)
    payload: dict[str, Any] = {
        "artifact_type": "R4.C_COMPLETE_DEVELOPMENT_REGISTER",
        "g0_identity": identity.identity,
        "expected_slots": sorted(expected_ids),
        "terminal_dispositions": {
            f"{family}:{seed}:{_TERMINAL_STAGE}": "UNOPENED"
            for family in FITTED_FAMILY_IDS
            for seed in PRIMARY_SEEDS
        },
        "artifact_hashes": artifact_hashes,
        "outcome_blind": True,
        "outcomes_loaded": False,
    }
    register_identity = _sha256(payload)
    payload["register_identity"] = register_identity
    _write_json_once(root / "register" / "development-register.json", payload)
    return register_identity


@reference_process
def close_development_register(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    root = Path(output_root).resolve()
    try:
        identity = _require_identity(root, config)
    except FileExistsError:
        raise
    except Exception as exc:
        _retain_preflight_failure(root, config, "DEVELOPMENT_PRE_ATTEMPT_VALIDATION", exc)
        raise
    from .supervisor import verify_epoch_caches

    verify_epoch_caches(root, f"register-close-{uuid.uuid4().hex}")
    return close_authenticated_register(root, identity, config=config)


def _terminal_prediction_identity(payload: dict[str, Any]) -> str:
    return _sha256(
        {
            "prediction_input_identity": payload["prediction_input_identity"],
            "input_identity": payload.get("input_identity"),
            "model_file_sha256": payload.get("model_file_sha256"),
            "model_metadata_file_sha256": payload.get("model_metadata_file_sha256"),
            "target_keys": tuple(payload["target_keys"]),
            "local_ridge_forecast": payload["local_ridge_forecast"],
            "fully_pooled_local_ridge_forecast": payload["fully_pooled_local_ridge_forecast"],
            "linear_control_identity": payload["linear_control_identity"],
            "linear_controls": payload.get("linear_controls"),
            "linear_control_identities": payload.get("linear_control_identities"),
            "linear_control_support_identity": payload.get("linear_control_support_identity"),
            "residual_prediction": payload["residual_prediction"],
            "total_forecast": payload["total_forecast"],
        }
    )


def _terminal_prediction_payload_identity(payload: dict[str, Any]) -> str:
    check = dict(payload)
    check.pop("prediction_identity", None)
    return _sha256(check)


def _validate_linear_control_payload(
    payload: dict[str, Any], expected_period: dict[str, Any]
) -> None:
    import math

    if payload.get("linear_control_identity") != expected_period.get("identity"):
        raise ValueError("linear-control period identity drifted")
    if payload.get("linear_controls") != expected_period.get("controls"):
        raise ValueError("linear-control values drifted")
    if payload.get("linear_control_identities") != expected_period.get("control_identities"):
        raise ValueError("linear-control identities drifted")
    if payload.get("linear_control_support_identity") != expected_period.get("support_identity"):
        raise ValueError("linear-control support identity drifted")
    expected_controls = expected_period.get("controls")
    if not isinstance(expected_controls, dict):
        raise ValueError("linear-control values are not canonical")
    for field, control_name in (
        ("local_ridge_forecast", "LOCAL_RIDGE"),
        ("fully_pooled_local_ridge_forecast", "FULLY_POOLED_LOCAL_RIDGE"),
    ):
        supplied = payload.get(field)
        expected = expected_controls.get(control_name)
        if not isinstance(supplied, list) or not isinstance(expected, list):
            raise ValueError("linear-control forecast arrays are missing")
        if len(supplied) != len(expected) or any(
            not isinstance(value, (int, float))
            or not isinstance(expected_value, (int, float))
            or not math.isclose(float(value), float(expected_value), rel_tol=0.0, abs_tol=1e-6)
            for value, expected_value in zip(supplied, expected, strict=True)
        ):
            raise ValueError(f"linear-control {control_name} forecast drifted")


def _validate_terminal_forecast_payload(payload: dict[str, Any], prediction_input: Any) -> str:
    from .runtime import apply_residual_correction

    expected_keys = tuple(key for key, _ in prediction_input.forecasts)
    keys = tuple(payload.get("target_keys", ()))
    if keys != expected_keys:
        raise ValueError("terminal prediction target-key order is not authenticated")
    arrays: dict[str, list[Any]] = {}
    import math

    import torch

    for field in (
        "local_ridge_forecast",
        "fully_pooled_local_ridge_forecast",
        "residual_prediction",
        "total_forecast",
    ):
        values = payload.get(field)
        if not isinstance(values, list) or len(values) != len(keys):
            raise ValueError("terminal forecast arrays are missing or misaligned")
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("terminal forecast arrays must be finite")
        arrays[field] = values
    forecasts_by_key = dict(prediction_input.forecasts)
    expected_local = torch.as_tensor(
        [forecasts_by_key[key] for key in expected_keys], dtype=torch.float32
    )
    supplied_local = torch.as_tensor(arrays["local_ridge_forecast"], dtype=torch.float32)
    if not torch.equal(supplied_local, expected_local):
        raise ValueError("terminal LOCAL_RIDGE forecast drifted from authenticated input")
    if payload.get("prediction") != arrays["residual_prediction"]:
        raise ValueError("terminal residual prediction alias drifted")
    corrected = apply_residual_correction(
        torch.as_tensor(arrays["local_ridge_forecast"], dtype=torch.float32),
        torch.as_tensor(arrays["residual_prediction"], dtype=torch.float32),
    )
    corrected_values = corrected.detach().cpu().tolist()
    if corrected_values != arrays["total_forecast"]:
        raise ValueError("terminal total forecast does not equal local forecast plus residual")
    forecast_identity = _terminal_prediction_identity(payload)
    if payload.get("forecast_identity") != forecast_identity:
        raise ValueError("terminal forecast identity is invalid")
    return forecast_identity


def _validate_prediction_payload(
    payload: dict[str, Any],
    path: Path,
    *,
    identity: G0ExecutionIdentity,
    slot_id: str,
) -> str:
    prediction_identity = payload.get("prediction_identity")
    check = dict(payload)
    check.pop("prediction_identity", None)
    if prediction_identity != _sha256(check):
        raise ValueError(f"prediction identity is invalid: {path}")
    if (
        payload.get("slot_id") != slot_id
        or payload.get("g0_identity") != identity.identity
        or payload.get("outcomes_loaded") is not False
        or payload.get("prediction_closed") is not True
    ):
        raise ValueError(f"prediction artifact is not closed and outcome-blind: {path}")
    return str(prediction_identity)


def _validate_terminal_capsule_payload(
    payload: dict[str, Any],
    *,
    config: FrozenRuntimeConfig | None = None,
    expected_payload: dict[str, Any] | None = None,
) -> None:
    from .terminal_support import TerminalSupportCapsule

    if payload.get("artifact_type") != "R4P0_TERMINAL_SUPPORT":
        raise ValueError("terminal support capsule artifact type is not canonical")
    try:
        capsule = TerminalSupportCapsule(
            payload["parent_identity"],
            payload["manifest_sha256"],
            payload["child_closure_sha256"],
            payload["graph_identity"],
            payload["config_identity"],
            tuple(payload["node_order"]),
            tuple(payload["feature_names"]),
            payload["feature_semantic_sha256"],
            payload["lookback_minutes"],
            payload["evidence_label"],
            payload["source_class"],
            payload["mode"],
            payload["source_active"],
            tuple(payload["keys"]),
            payload["key_count"],
            payload["decision_time_count"],
            tuple(sorted((str(k), int(v)) for k, v in payload["per_instrument_counts"].items())),
            tuple((str(k), str(v)) for k, v in payload["input_identities"]),
            tuple(sorted((str(k), str(v)) for k, v in payload["key_input_identities"].items())),
            payload["ordered_key_sha256"],
            payload["predicate_identity"],
            payload["artifact_identity"],
            payload.get("terminal_metadata_identity", ""),
            payload.get("history_content_identity", ""),
            payload.get("history_row_count", 0),
            payload.get("first_terminal_lookback_identity", ""),
            payload.get("first_terminal_lookback_row_count", 0),
            payload.get("first_terminal_lookback_instrument_count", 0),
            payload.get("first_terminal_lookback_minute_count", 0),
        )
        capsule._validate()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("terminal support capsule is not canonical") from exc
    if payload != capsule.to_dict():
        raise ValueError("terminal support capsule representation is not canonical")
    if config is not None and capsule.config_identity != config.identity:
        raise ValueError("terminal support capsule config identity drifted")
    if expected_payload is not None and payload != expected_payload:
        raise ValueError("terminal support capsule differs from current authenticated support")


def _development_journal_records(root: Path) -> tuple[JournalRecord, ...]:
    """Project development records only after authenticating the complete journal."""
    return tuple(
        record
        for record in _journal_records(root)
        if dict(record["attempt"])["stage"] != _TERMINAL_STAGE
    )


def _development_journal_identity(root: Path) -> str:
    return _sha256(_development_journal_records(root))


def _validate_development_attempt_ledger(
    attempts_path: Path,
    *,
    expected_slots: tuple[str, ...],
    runtime_config: FrozenRuntimeConfig,
    foundation_content_identity: str,
    output_identity: str,
    canonical_output_root: Path,
) -> dict[str, AttemptIdentity]:
    """Authenticate the create-only PRIMARY development-attempt lifecycle."""
    del output_identity
    journal = CreateOnlyAttemptJournal(canonical_output_root)
    if attempts_path != journal.path:
        raise ValueError("development journal path is not canonical")
    records = _development_journal_records(canonical_output_root)
    if not records:
        raise ValueError("development attempt journal is empty")
    expected = set(expected_slots)
    by_slot: dict[str, list[tuple[AttemptIdentity, str]]] = {slot: [] for slot in expected_slots}
    for record in records:
        raw_attempt = cast(Mapping[str, object], record["attempt"])
        attempt = AttemptIdentity(
            release_identity=str(raw_attempt["release_identity"]),
            g0_identity=str(raw_attempt["g0_identity"]),
            output_root=str(raw_attempt["output_root"]),
            slot_id=str(raw_attempt["slot_id"]),
            family_id=str(raw_attempt["family_id"]),
            seed=int(cast(int, raw_attempt["seed"])),
            stage=str(raw_attempt["stage"]),
            attempt=int(cast(int, raw_attempt["attempt"])),
            mode=str(raw_attempt["mode"]),
        )
        if record["attempt_id"] != attempt.identity:
            raise ValueError("development journal attempt identity is invalid")
        if attempt.slot_id not in expected or attempt.mode != "PRIMARY":
            raise ValueError("development journal contains an unexpected slot")
        payload = cast(Mapping[str, object], record["payload"])
        if record["status"] == "STARTED" and (
            payload.get("config_identity") != runtime_config.identity
            or payload.get("foundation_content_identity") != foundation_content_identity
        ):
            raise ValueError("development STARTED record semantic identity drifted")
        by_slot[attempt.slot_id].append((attempt, str(record["status"])))
    accepted: dict[str, AttemptIdentity] = {}
    direct = ((0, "STARTED"), (0, "SUCCEEDED"))
    retry = ((0, "STARTED"), (0, "FAILED"), (1, "STARTED"), (1, "SUCCEEDED"))
    for slot_id, slot_records in by_slot.items():
        lifecycle = tuple((attempt.attempt, status) for attempt, status in slot_records)
        if lifecycle not in (direct, retry):
            raise ValueError(f"development attempt lifecycle is not canonical: {slot_id}")
        accepted[slot_id] = slot_records[-1][0]
    return accepted


_DEVELOPMENT_RESULT_FIELDS = frozenset(
    {
        "artifact_type",
        "slot_id",
        "attempt",
        "g0_identity",
        "fit",
        "model_metadata_file_sha256",
        "prediction_file_sha256",
        "prediction_identity",
        "outcomes_loaded",
        "prediction_closed",
    }
)
_DEVELOPMENT_MODEL_METADATA_FIELDS = frozenset(
    {
        "artifact_type",
        "slot_id",
        "family_id",
        "seed",
        "stage",
        "attempt",
        "g0_identity",
        "config_identity",
        "foundation_content_identity",
        "training_batch_identity",
        "preprocessor_identity",
        "prediction_input_identity",
        "architecture",
        "architecture_identity",
        "graph_identity",
        "state_sha256",
        "model_file_sha256",
    }
)
_DEVELOPMENT_PREDICTION_FIELDS = frozenset(
    {
        "artifact_type",
        "slot_id",
        "attempt",
        "g0_identity",
        "prediction",
        "residual_prediction",
        "target_keys",
        "local_ridge_forecast",
        "fully_pooled_local_ridge_forecast",
        "linear_control_identity",
        "linear_controls",
        "linear_control_identities",
        "linear_control_support_identity",
        "total_forecast",
        "model_file_sha256",
        "model_metadata_file_sha256",
        "input_identity",
        "preprocessor_identity",
        "outcomes_loaded",
        "prediction_closed",
        "prediction_identity",
    }
)


def _require_artifact_identity(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"development artifact {field} is not a SHA-256 identity")
    return value


_FIT_EVIDENCE_FIELDS = frozenset(
    {
        "elapsed_seconds",
        "parameter_count",
        "epochs",
        "target_instruments",
        "equal_instrument_loss",
        "training_batch_identity",
    }
)


def _validate_fit_evidence(
    value: object,
    *,
    slot_id: str,
    expected_training_batch_identity: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FIT_EVIDENCE_FIELDS:
        raise ValueError(f"fit evidence schema is not canonical: {slot_id}")
    typed_value = cast(dict[str, Any], value)
    elapsed = typed_value["elapsed_seconds"]
    parameter_count = typed_value["parameter_count"]
    epochs = typed_value["epochs"]
    target_instruments = typed_value["target_instruments"]
    if (
        not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0.0
        or not isinstance(parameter_count, int)
        or isinstance(parameter_count, bool)
        or parameter_count <= 0
        or not isinstance(epochs, int)
        or isinstance(epochs, bool)
        or epochs <= 0
        or target_instruments != len(ALL_INSTRUMENTS)
        or typed_value["equal_instrument_loss"] is not True
    ):
        raise ValueError(f"fit evidence values are not canonical: {slot_id}")
    training_batch_identity = _require_artifact_identity(
        typed_value["training_batch_identity"], "training_batch_identity"
    )
    if (
        expected_training_batch_identity is not None
        and training_batch_identity != expected_training_batch_identity
    ):
        raise ValueError(f"fit evidence training batch identity drifted: {slot_id}")
    return typed_value


def _summarize_fit_evidence(
    fit_evidence: Mapping[str, Mapping[str, Any]],
    *,
    expected_slots: Sequence[str],
) -> dict[str, Any]:
    expected = tuple(sorted(expected_slots))
    actual = tuple(sorted(fit_evidence))
    if actual != expected:
        raise ValueError("fit evidence does not cover the canonical slot set")
    by_stage: dict[str, list[dict[str, Any]]] = {}
    by_family: dict[str, list[dict[str, Any]]] = {}
    slots: dict[str, dict[str, Any]] = {}
    for slot_id in expected:
        family, seed_text, stage = slot_id.split(":", 2)
        evidence = dict(fit_evidence[slot_id])
        record = {
            "slot_id": slot_id,
            "family_id": family,
            "seed": int(seed_text),
            "stage": stage,
            **evidence,
        }
        slots[slot_id] = record
        by_stage.setdefault(stage, []).append(record)
        by_family.setdefault(family, []).append(record)
    family_parameter_counts: dict[str, int | None] = {}
    for family, records in by_family.items():
        counts = {int(record["parameter_count"]) for record in records}
        if len(counts) != 1:
            raise ValueError(f"fit parameter count differs across slots: {family}")
        family_parameter_counts[family] = next(iter(counts))
    pooled_count = family_parameter_counts.get("POOLED_NON_GRAPH_RESIDUAL")
    local_count = family_parameter_counts.get("LOCAL_TEMPORAL_RESIDUAL")
    learned_count = family_parameter_counts.get("LEARNED_STATIC_GRAPH_RESIDUAL")
    fixed_count = family_parameter_counts.get("FIXED_ECONOMIC_GRAPH_RESIDUAL")
    capacity = {
        "controls": "N/A",
        "parameter_count_by_family": family_parameter_counts,
        "fixed_vs_shuffled_equal": family_parameter_counts.get("FIXED_ECONOMIC_GRAPH_RESIDUAL")
        == family_parameter_counts.get("SHUFFLED_FIXED_GRAPH_RESIDUAL"),
        "pooled_non_graph_difference": (
            pooled_count - local_count
            if isinstance(pooled_count, int) and isinstance(local_count, int)
            else None
        ),
        "learned_adjacency_extra": (
            learned_count - fixed_count
            if isinstance(learned_count, int) and isinstance(fixed_count, int)
            else None
        ),
    }
    return {
        "slot_count": len(expected),
        "slots": slots,
        "by_stage": by_stage,
        "by_family": by_family,
        "capacity": capacity,
        "capacity_differences": capacity,
    }


def _validate_development_artifact_metadata(
    *,
    slot_id: str,
    attempt: int,
    identity: G0ExecutionIdentity,
    runtime_config: FrozenRuntimeConfig,
    foundation_content_identity: str,
    expected_preprocessor_identity: str,
    result_payload: dict[str, Any],
    metadata_payload: dict[str, Any],
    prediction_payload: dict[str, Any],
    model_digest: str,
    metadata_digest: str,
    prediction_digest: str,
    prediction_path: Path,
) -> str:
    family_id, seed_text, stage = slot_id.split(":", 2)
    if set(result_payload) != _DEVELOPMENT_RESULT_FIELDS:
        raise ValueError(f"development result schema is not canonical: {slot_id}")
    if set(metadata_payload) != _DEVELOPMENT_MODEL_METADATA_FIELDS:
        raise ValueError(f"development model metadata schema is not canonical: {slot_id}")
    if set(prediction_payload) != _DEVELOPMENT_PREDICTION_FIELDS:
        raise ValueError(f"development prediction schema is not canonical: {slot_id}")

    expected_sha_fields = (
        "g0_identity",
        "config_identity",
        "foundation_content_identity",
        "training_batch_identity",
        "preprocessor_identity",
        "prediction_input_identity",
        "architecture_identity",
        "state_sha256",
        "model_file_sha256",
    )
    for field in expected_sha_fields:
        _require_artifact_identity(metadata_payload.get(field), field)
    graph_identity = metadata_payload["graph_identity"]
    if graph_identity is not None:
        _require_artifact_identity(graph_identity, "graph_identity")
    if metadata_payload["g0_identity"] != identity.identity:
        raise ValueError(f"development model metadata G0 identity drifted: {slot_id}")
    if metadata_payload["config_identity"] != runtime_config.identity:
        raise ValueError(f"development model metadata config identity drifted: {slot_id}")
    if metadata_payload["foundation_content_identity"] != foundation_content_identity:
        raise ValueError(f"development model metadata foundation identity drifted: {slot_id}")
    if metadata_payload["preprocessor_identity"] != expected_preprocessor_identity:
        raise ValueError(f"development model metadata preprocessor identity drifted: {slot_id}")
    if not isinstance(metadata_payload["architecture"], dict):
        raise ValueError(f"development model architecture schema drifted: {slot_id}")
    if metadata_payload["architecture_identity"] != _sha256(metadata_payload["architecture"]):
        raise ValueError(f"development model architecture identity drifted: {slot_id}")
    if metadata_payload["model_file_sha256"] != model_digest:
        raise ValueError(f"development model file identity drifted: {slot_id}")
    if metadata_payload["state_sha256"] != model_digest:
        raise ValueError(f"development model state identity drifted: {slot_id}")
    if (
        metadata_payload["artifact_type"] != "R4.C_PRIMARY_MODEL_METADATA"
        or metadata_payload["slot_id"] != slot_id
        or metadata_payload["family_id"] != family_id
        or metadata_payload["seed"] != int(seed_text)
        or metadata_payload["stage"] != stage
        or metadata_payload["attempt"] != attempt
        or (
            metadata_payload["graph_identity"] is not None
            and (
                not isinstance(metadata_payload["graph_identity"], str)
                or len(metadata_payload["graph_identity"]) != 64
            )
        )
    ):
        raise ValueError(f"development model metadata linkage drifted: {slot_id}")

    prediction_identity = _validate_prediction_payload(
        prediction_payload, prediction_path, identity=identity, slot_id=slot_id
    )
    for field in (
        "model_file_sha256",
        "model_metadata_file_sha256",
        "input_identity",
        "preprocessor_identity",
    ):
        _require_artifact_identity(prediction_payload.get(field), field)
    if (
        prediction_payload["attempt"] != attempt
        or prediction_payload["artifact_type"] != "R4.C_PRIMARY_PREDICTION"
        or prediction_payload["model_file_sha256"] != model_digest
        or prediction_payload["model_metadata_file_sha256"] != metadata_digest
        or prediction_payload["input_identity"] != metadata_payload["prediction_input_identity"]
        or prediction_payload["preprocessor_identity"] != expected_preprocessor_identity
        or prediction_payload["prediction"] != prediction_payload["residual_prediction"]
    ):
        raise ValueError(f"development prediction linkage drifted: {slot_id}")

    if (
        result_payload["artifact_type"] != "R4.C_PRIMARY_RESULT"
        or result_payload["slot_id"] != slot_id
        or result_payload["attempt"] != attempt
        or result_payload["g0_identity"] != identity.identity
        or result_payload["model_metadata_file_sha256"] != metadata_digest
        or result_payload["prediction_file_sha256"] != prediction_digest
        or result_payload["prediction_identity"] != prediction_identity
        or result_payload["outcomes_loaded"] is not False
        or result_payload["prediction_closed"] is not True
    ):
        raise ValueError(f"development result linkage drifted: {slot_id}")
    _require_artifact_identity(result_payload["g0_identity"], "g0_identity")
    _require_artifact_identity(
        result_payload["model_metadata_file_sha256"], "model_metadata_file_sha256"
    )
    _require_artifact_identity(result_payload["prediction_file_sha256"], "prediction_file_sha256")
    if (
        result_payload["model_metadata_file_sha256"] != metadata_digest
        or result_payload["prediction_file_sha256"] != prediction_digest
    ):
        raise ValueError(f"development result digest linkage drifted: {slot_id}")
    return prediction_identity


def _validate_development_register_payload(
    root: Path,
    identity: G0ExecutionIdentity,
    *,
    config: FrozenRuntimeConfig | None = None,
    payload: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    runtime_config = config or FrozenRuntimeConfig()
    payload = (
        payload
        if payload is not None
        else _read_json(root / "register" / "development-register.json")
    )
    expected_slots = sorted(
        f"{family}:{seed}:{stage}" for family, seed, stage in _EXPECTED_DEVELOPMENT_SLOTS
    )
    expected_dispositions = {
        f"{family}:{seed}:{_TERMINAL_STAGE}": "UNOPENED"
        for family in FITTED_FAMILY_IDS
        for seed in PRIMARY_SEEDS
    }
    required_keys = {
        "artifact_type",
        "g0_identity",
        "expected_slots",
        "terminal_dispositions",
        "artifact_hashes",
        "slot_provenance",
        "attempts_file_sha256",
        "foundation_content_identity",
        "preprocessor_identity",
        "config_identity",
        "runtime_identity",
        "input_identity",
        "outcome_blind",
        "outcomes_loaded",
        "metric_register_identity",
        "metric_register_file_sha256",
        "register_identity",
    }
    if set(payload) != required_keys:
        raise ValueError("development register schema is not canonical")
    check = dict(payload)
    register_identity = check.pop("register_identity")
    if register_identity != _sha256(check):
        raise ValueError("development register identity is invalid")
    if (
        payload["artifact_type"] != "R4.C_COMPLETE_AUTHENTICATED_DEVELOPMENT_REGISTER"
        or payload["g0_identity"] != identity.identity
        or payload["expected_slots"] != expected_slots
        or payload["terminal_dispositions"] != expected_dispositions
        or payload["outcome_blind"] is not True
        or payload["outcomes_loaded"] is not False
    ):
        raise ValueError("development register is not canonical or outcome-blind")
    capsule = _load_development_execution_capsule(root, identity, runtime_config)
    if (
        payload["foundation_content_identity"] != capsule["foundation_content_identity"]
        or payload["preprocessor_identity"] != capsule["preparation_preprocessor_identity"]
        or payload["config_identity"] != runtime_config.identity
        or payload["runtime_identity"] != runtime_config.identity
        or payload["input_identity"] != RESIDUAL_FOUNDATION_IDENTITY
    ):
        raise ValueError("development register current execution identities drifted")
    artifact_hashes = payload["artifact_hashes"]
    provenance = payload["slot_provenance"]
    if (
        not isinstance(artifact_hashes, dict)
        or set(artifact_hashes) != set(expected_slots)
        or not isinstance(provenance, dict)
        or set(provenance) != set(expected_slots)
    ):
        raise ValueError("development register artifacts are incomplete")
    metric_path = root / "register" / "development-metrics.json"
    if (
        not metric_path.is_file()
        or _file_digest(metric_path) != payload["metric_register_file_sha256"]
    ):
        raise ValueError("development metric register hash drifted")
    metric_payload = _read_json(metric_path)
    metric_identity = metric_payload.get("metric_identity")
    metric_check = dict(metric_payload)
    metric_check.pop("metric_identity", None)
    if metric_identity != payload["metric_register_identity"] or metric_identity != _sha256(
        metric_check
    ):
        raise ValueError("development metric register identity is invalid")
    from .evaluation import historical_r3h_cost_grid

    if (
        metric_payload.get("artifact_type") != "R4.C_DEVELOPMENT_METRIC_GATE_REGISTER"
        or metric_payload.get("periods") != ["DEV_2", "DEV_3"]
        or metric_payload.get("weighting") != "union_within_instrument_then_equal_twenty"
        or metric_payload.get("total_forecast_definition")
        != "local_ridge_forecast_plus_residual_correction"
        or metric_payload.get("r3h_cost_grid") != historical_r3h_cost_grid()
        or metric_payload.get("support_row_count")
        != sum(len(capsule["stages"][stage]["target_keys"]) for stage in _DEVELOPMENT_STAGES)
    ):
        raise ValueError("development metric register is not canonical")
    metric_families = metric_payload.get("families")
    if not isinstance(metric_families, dict) or set(metric_families) != set(FITTED_FAMILY_IDS):
        raise ValueError("development metric register family set is not canonical")
    expected_metric_seeds = {str(seed) for seed in PRIMARY_SEEDS}
    for family_entry in metric_families.values():
        if (
            not isinstance(family_entry, dict)
            or set(family_entry.get("seeds", {})) != expected_metric_seeds
            or not isinstance(family_entry.get("primary"), dict)
            or family_entry["primary"].get("seed") != min(PRIMARY_SEEDS)
        ):
            raise ValueError("development metric register seed views are not canonical")
    metric_controls = metric_payload.get("controls")
    if not isinstance(metric_controls, dict) or set(metric_controls) != {
        "ZERO_RETURN",
        "LOCAL_RIDGE",
        "FULLY_POOLED_LOCAL_RIDGE",
    }:
        raise ValueError("development metric register controls are not canonical")
    metric_coverage = metric_payload.get("coverage")
    if not isinstance(metric_coverage, dict) or any(
        not isinstance(metric_coverage.get(period), dict)
        or set(metric_coverage[period]) != set(ALL_INSTRUMENTS)
        or any(int(metric_coverage[period][instrument]) <= 0 for instrument in ALL_INSTRUMENTS)
        for period in ("DEV_2", "DEV_3")
    ):
        raise ValueError("development metric register coverage is not canonical")
    attempts_path = root / "register" / "attempts"
    if _development_journal_identity(root) != payload["attempts_file_sha256"]:
        raise ValueError("development register attempt journal identity drifted")
    succeeded = _validate_development_attempt_ledger(
        attempts_path,
        expected_slots=tuple(expected_slots),
        runtime_config=runtime_config,
        foundation_content_identity=capsule["foundation_content_identity"],
        output_identity=identity.output_root_identity,
        canonical_output_root=root,
    )
    stage_preprocessor_identities = {
        stage: capsule["stages"][stage]["preprocessor_identity"] for stage in _DEVELOPMENT_STAGES
    }
    for slot_id in expected_slots:
        entry = artifact_hashes[slot_id]
        slot_entry = provenance[slot_id]
        if (
            not isinstance(entry, dict)
            or set(entry) != {"result", "model", "model_metadata", "prediction"}
            or not isinstance(slot_entry, dict)
            or set(slot_entry)
            != {
                "attempt",
                "foundation_content_identity",
                "config_identity",
                "runtime_identity",
                "input_identity",
                "preprocessor_identity",
                "prediction_input_identity",
                "model_file_sha256",
                "model_metadata_file_sha256",
                "prediction_file_sha256",
                "prediction_identity",
                "result_file_sha256",
            }
        ):
            raise ValueError(f"development register artifact set is not canonical: {slot_id}")
        attempt = succeeded[slot_id].attempt
        bundle = root / "attempts" / slot_id / f"attempt-{attempt}"
        verify_attempt_bundle(bundle, succeeded[slot_id])
        result_path = bundle / "result.json"
        model_path = bundle / "model.pt"
        metadata_path = bundle / "model.json"
        prediction_path = bundle / "prediction.json"
        if not all(
            path.is_file() for path in (result_path, model_path, metadata_path, prediction_path)
        ):
            raise ValueError(f"development slot artifacts are missing: {slot_id}")
        observed_hashes = {
            "result": _file_digest(result_path),
            "model": _file_digest(model_path),
            "model_metadata": _file_digest(metadata_path),
            "prediction": _file_digest(prediction_path),
        }
        if entry != observed_hashes:
            raise ValueError(f"development register artifact hash drifted: {slot_id}")
        result_payload = _read_json(result_path)
        metadata = _read_json(metadata_path)
        prediction_payload = _read_json(prediction_path)
        family_id, seed_text, stage = slot_id.split(":", 2)
        expected_stage_preprocessor_identity = stage_preprocessor_identities[stage]
        _validate_development_artifact_metadata(
            slot_id=slot_id,
            attempt=attempt,
            identity=identity,
            runtime_config=runtime_config,
            foundation_content_identity=capsule["foundation_content_identity"],
            expected_preprocessor_identity=expected_stage_preprocessor_identity,
            result_payload=result_payload,
            metadata_payload=metadata,
            prediction_payload=prediction_payload,
            model_digest=observed_hashes["model"],
            metadata_digest=observed_hashes["model_metadata"],
            prediction_digest=observed_hashes["prediction"],
            prediction_path=prediction_path,
        )
        prediction_identity = _validate_prediction_payload(
            prediction_payload, prediction_path, identity=identity, slot_id=slot_id
        )
        if (
            result_payload.get("artifact_type") != "R4.C_PRIMARY_RESULT"
            or result_payload.get("slot_id") != slot_id
            or result_payload.get("attempt") != attempt
            or result_payload.get("g0_identity") != identity.identity
            or result_payload.get("outcomes_loaded") is not False
            or result_payload.get("prediction_closed") is not True
            or prediction_payload.get("artifact_type") != "R4.C_PRIMARY_PREDICTION"
            or prediction_payload.get("attempt") != attempt
            or prediction_payload.get("model_file_sha256") != observed_hashes["model"]
            or prediction_payload.get("model_metadata_file_sha256")
            != observed_hashes["model_metadata"]
            or prediction_payload.get("preprocessor_identity")
            != expected_stage_preprocessor_identity
            or prediction_payload.get("input_identity") != metadata.get("prediction_input_identity")
            or result_payload.get("prediction_identity") != prediction_identity
            or result_payload.get("prediction_file_sha256") != observed_hashes["prediction"]
            or result_payload.get("model_metadata_file_sha256") != observed_hashes["model_metadata"]
            or metadata.get("artifact_type") != "R4.C_PRIMARY_MODEL_METADATA"
            or metadata.get("slot_id") != slot_id
            or metadata.get("family_id") != family_id
            or metadata.get("seed") != int(seed_text)
            or metadata.get("stage") != stage
            or metadata.get("attempt") != attempt
            or metadata.get("g0_identity") != identity.identity
            or metadata.get("config_identity") != runtime_config.identity
            or metadata.get("foundation_content_identity") != capsule["foundation_content_identity"]
            or metadata.get("preprocessor_identity") != expected_stage_preprocessor_identity
            or metadata.get("model_file_sha256") != observed_hashes["model"]
            or metadata.get("state_sha256") != observed_hashes["model"]
            or metadata.get("architecture_identity") != _sha256(metadata.get("architecture"))
            or not all(
                key in metadata
                for key in (
                    "training_batch_identity",
                    "preprocessor_identity",
                    "prediction_input_identity",
                    "graph_identity",
                )
            )
        ):
            raise ValueError(f"development register artifact linkage drifted: {slot_id}")
        expected_provenance = {
            "attempt": attempt,
            "foundation_content_identity": metadata["foundation_content_identity"],
            "config_identity": metadata["config_identity"],
            "runtime_identity": runtime_config.identity,
            "input_identity": RESIDUAL_FOUNDATION_IDENTITY,
            "preprocessor_identity": expected_stage_preprocessor_identity,
            "prediction_input_identity": metadata["prediction_input_identity"],
            "model_file_sha256": observed_hashes["model"],
            "model_metadata_file_sha256": observed_hashes["model_metadata"],
            "prediction_file_sha256": observed_hashes["prediction"],
            "prediction_identity": prediction_identity,
            "result_file_sha256": observed_hashes["result"],
        }
        if slot_entry != expected_provenance:
            raise ValueError(f"development register provenance drifted: {slot_id}")
    return payload


def _validate_fixture_register_payload(root: Path, identity: G0ExecutionIdentity) -> dict[str, Any]:
    payload = _read_json(Path(root).resolve() / "register" / "development-register.json")
    check = dict(payload)
    register_identity = check.pop("register_identity", None)
    if register_identity != _sha256(check):
        raise ValueError("development register identity is invalid")
    expected_slots = sorted(
        f"{family}:{seed}:{stage}" for family, seed, stage in _EXPECTED_DEVELOPMENT_SLOTS
    )
    expected_dispositions = {
        f"{family}:{seed}:{_TERMINAL_STAGE}": "UNOPENED"
        for family in FITTED_FAMILY_IDS
        for seed in PRIMARY_SEEDS
    }
    if (
        payload.get("artifact_type") != "R4.C_COMPLETE_DEVELOPMENT_REGISTER"
        or payload.get("g0_identity") != identity.identity
        or payload.get("expected_slots") != expected_slots
        or payload.get("terminal_dispositions") != expected_dispositions
        or payload.get("outcome_blind") is not True
        or payload.get("outcomes_loaded") is not False
    ):
        raise ValueError("fixture development register is not canonical or outcome-blind")
    artifact_hashes = payload.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != set(expected_slots):
        raise ValueError("fixture development register artifacts are incomplete")
    successful = {
        record["attempt"]["slot_id"]: record
        for record in _journal_records(root)
        if record["status"] == "SUCCEEDED" and record["attempt"]["stage"] in {"DEV_2", "DEV_3"}
    }
    if set(successful) != set(expected_slots):
        raise ValueError("fixture development register journal is incomplete")
    for slot_id, artifact_hash in artifact_hashes.items():
        record = successful[slot_id]
        bundle = root / str(record["payload"]["bundle"])
        verify_attempt_bundle(bundle)
        result_path = bundle / "result.json"
        if _file_digest(result_path) != artifact_hash:
            raise ValueError(f"fixture development register artifact drifted: {slot_id}")
    return payload


def _require_complete_register(
    output_root: Path,
    identity: G0ExecutionIdentity,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    if _is_real_execution(root):
        return _validate_development_register_payload(root, identity, config=config)
    return _validate_fixture_register_payload(root, identity)


def _fixture_materialise_terminal_support_stage(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    """Materialise an outcome-blind terminal-support marker after register closure."""

    identity = _require_identity(output_root, config)
    root = Path(output_root).resolve()
    if _is_real_execution(root):
        raise ValueError("fixture capability cannot materialise authenticated execution")
    register = _require_complete_register(output_root, identity, config=config)
    payload: dict[str, Any] = {
        "artifact_type": "R4.C_TERMINAL_SUPPORT_MATERIALISATION",
        "g0_identity": identity.identity,
        "register_identity": register["register_identity"],
        "terminal_predicate": TERMINAL_PREDICATE,
        "allowed_columns": sorted(ALLOWED_TERMINAL_COLUMNS),
        "outcome_blind": True,
        "outcomes_loaded": False,
    }
    payload["support_identity"] = _sha256(payload)
    _write_json_once(Path(output_root).resolve() / "support" / "terminal-support.json", payload)
    return str(payload["support_identity"])


@reference_process
def materialise_terminal_support_stage(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    """Materialise authenticated terminal support."""
    root = Path(output_root).resolve()
    try:
        identity = _require_identity(root, config)
    except FileExistsError:
        raise
    except Exception as exc:
        _retain_preflight_failure(root, config, "TERMINAL_PRE_ATTEMPT_VALIDATION", exc)
        raise
    return materialise_authenticated_terminal_support(root, config, identity)


def _fixture_predict_terminal_slot(
    output_root: Path,
    *,
    family_id: str,
    seed: int,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    """Create one synthetic terminal prediction only after closed register/support."""

    identity = _require_identity(output_root, config)
    root = Path(output_root).resolve()
    if _is_real_execution(root):
        raise ValueError("fixture capability cannot predict authenticated execution")
    register = _require_complete_register(output_root, identity)
    support = _read_json(Path(output_root).resolve() / "support" / "terminal-support.json")
    if support.get("g0_identity") != identity.identity:
        raise ValueError("terminal support G0 identity drift detected")
    support_check = dict(support)
    support_identity = support_check.pop("support_identity", None)
    if support_identity != _sha256(support_check):
        raise ValueError("terminal support identity is invalid")
    if support.get("outcomes_loaded") is not False:
        raise ValueError("terminal support is not outcome-blind")
    if family_id not in FITTED_FAMILY_IDS or seed not in PRIMARY_SEEDS:
        raise ValueError("terminal prediction family or seed is outside the frozen register")
    slot_id = f"{family_id}:{seed}:{_TERMINAL_STAGE}"
    payload: dict[str, Any] = {
        "artifact_type": "R4.C_TERMINAL_SLOT_PREDICTION",
        "slot_id": slot_id,
        "g0_identity": identity.identity,
        "register_identity": register["register_identity"],
        "support_identity": support["support_identity"],
        "outcomes_loaded": False,
        "prediction_closed": True,
    }
    prediction_identity = _sha256(payload)
    payload["prediction_identity"] = prediction_identity
    _write_json_once(
        Path(output_root).resolve() / "prediction" / f"{slot_id.replace(':', '_')}.json",
        payload,
    )
    return prediction_identity


@reference_process
def predict_terminal_slot(
    output_root: Path,
    *,
    family_id: str,
    seed: int,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    """Create one authenticated terminal prediction."""
    root = Path(output_root).resolve()
    try:
        identity = _require_identity(root, config)
    except FileExistsError:
        raise
    except Exception as exc:
        _retain_preflight_failure(root, config, "TERMINAL_PRE_ATTEMPT_VALIDATION", exc)
        raise
    return predict_authenticated_terminal_slot(root, family_id, seed, config, identity)


def _fixture_aggregate_metrics(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    """Open the post-prediction outcome gate without loading outcomes in fixture mode."""

    identity = _require_identity(output_root, config)
    root = Path(output_root).resolve()
    if _is_real_execution(root):
        raise ValueError("fixture capability cannot aggregate authenticated execution")
    _require_complete_register(output_root, identity)
    prediction_paths = sorted((root / "prediction").glob("*.json"))
    if not prediction_paths:
        raise ValueError("metrics require closed terminal predictions")
    predictions = [_read_json(path) for path in prediction_paths]
    if any(
        payload.get("g0_identity") != identity.identity
        or payload.get("prediction_closed") is not True
        or payload.get("outcomes_loaded") is not False
        for payload in predictions
    ):
        raise ValueError("metrics require exact closed outcome-free predictions")
    payload: dict[str, Any] = {
        "artifact_type": "R4.C_METRICS_REPORT_INPUT",
        "g0_identity": identity.identity,
        "prediction_identities": sorted(
            str(prediction["prediction_identity"]) for prediction in predictions
        ),
        "outcome_gate": "OPEN_AFTER_CREATE_ONLY_PREDICTIONS",
        "outcomes_loaded": False,
    }
    payload["report_input_identity"] = _sha256(payload)
    _write_json_once(root / "metric" / "report-input.json", payload)
    return str(payload["report_input_identity"])


@reference_process
def aggregate_metrics(
    output_root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> str:
    """Aggregate authenticated terminal metrics."""
    root = Path(output_root).resolve()
    try:
        identity = _require_identity(root, config)
    except FileExistsError:
        raise
    except Exception as exc:
        _retain_preflight_failure(root, config, "TERMINAL_PRE_ATTEMPT_VALIDATION", exc)
        raise
    return aggregate_authenticated_metrics(root, config, identity)


def _smoke_terminal_support(
    root: Path, config: FrozenRuntimeConfig, decision_time: datetime | None
) -> Any:
    from .graph import build_fixed_economic_graph
    from .tensor import FEATURE_SEMANTIC_SHA256, P0_FEATURE_NAMES
    from .terminal_support import TerminalSupportConfig, build_terminal_support

    graph = build_fixed_economic_graph()
    time = max(decision_time or TERMINAL_START, TERMINAL_START + timedelta(minutes=1))
    rows = [
        {
            "instrument_id": instrument,
            "decision_time": time,
            "block": "TERMINAL_FORMER_HOLDOUT",
            "target_valid": 1,
            "target_available_at": time + timedelta(minutes=15),
            "dependency_start": time - timedelta(minutes=60),
            "dependency_end": time,
            "feature_data_asof": time,
            "feature_available_at": time,
            "latest_feature_bar_end": time,
            "feature_schema_identity": "SMOKE_FEATURE_SCHEMA",
            "feature_identity": "SMOKE_FEATURE",
            "manifest_sha256": config.manifest_identity,
            "child_closure_sha256": config.child_closure_identity,
            "parent_identity": config.parent_identity,
            "feature_semantic_sha256": FEATURE_SEMANTIC_SHA256,
            "feature_mask": (True,) * len(P0_FEATURE_NAMES),
            "availability_mask": (True,) * len(P0_FEATURE_NAMES),
            "node_mask": True,
            "context_schema_identity": "SMOKE_CONTEXT_SCHEMA",
            "context_identity": "SMOKE_CONTEXT",
            "history_start": time - timedelta(minutes=60),
            "history_end": time,
            "history_identity": "SMOKE_HISTORY",
            "forecast_exists": True,
            "forecast_identity": "SMOKE_FORECAST",
            "graph_identity": graph.identity,
            "config_identity": config.identity,
            "evidence_label": "SMOKE_SYNTHETIC",
            "source_class": "SMOKE_SYNTHETIC",
            "source_active": True,
        }
        for instrument in ALL_INSTRUMENTS
    ]
    return build_terminal_support(
        rows,
        config=TerminalSupportConfig.for_smoke(config, graph, forecast_identity="SMOKE_FORECAST"),
        output_path=root / "support" / "terminal-capsule.json",
        development_register_closed=True,
        actual_terminal=False,
    )


def _smoke_dispatch_evidence(telemetry: Any, *, training: bool) -> dict[str, Any]:
    """Require four real cached timestamp chunks, without assuming physical overlap."""
    if (
        telemetry.rows != 256 * len(ALL_INSTRUMENTS)
        or telemetry.timestamps != 256
        or telemetry.timestamp_batch_calls != 4
        or telemetry.forward_calls != 4
        or telemetry.materialisation_calls != 4
        or telemetry.prefetch_calls != 4
        or telemetry.h2d_calls != 4 + int(training)
        or telemetry.h2d_bytes <= 0
    ):
        raise ValueError("SMOKE requires batch64 multi-chunk cached pinned-prefetch dispatch")
    return asdict(telemetry)


def run_bounded_smoke(
    output_root: Path,
    *,
    reference_root: Path,
    config: FrozenRuntimeConfig | None = None,
    decision_time: datetime | None = None,
) -> dict[str, Any]:
    """Exercise production persistence and cached CUDA numerics using only synthetic inputs."""
    import torch

    from .preparation_reference import reference_binding
    from .qualification import _oracle_scalar_forward, semantic_model_hash, semantic_prediction_hash
    from .runtime import RuntimeTelemetry, fit_one_model, predict_residual
    from .stage_cache import load_stage_cache_batches
    from .synthetic_qualification import build_synthetic_cache, synthetic_qualification_inputs

    root = Path(output_root).resolve()
    reference = Path(reference_root).resolve()
    runtime_config = config or FrozenRuntimeConfig()
    if runtime_config != FrozenRuntimeConfig():
        raise ValueError("SMOKE requires the canonical synthetic cache and model configuration")
    if root == reference or root.is_relative_to(reference) or reference.is_relative_to(root):
        raise ValueError("SMOKE requires a distinct, non-overlapping output root")
    with preparation_entry(reference) as capability:
        if capability is None:
            raise ValueError("SMOKE requires an authenticated preparation reference")
        binding = reference_binding(reference)
    # The reference capability is expired before any distinct-root identity or attempt work.
    require_cuda(runtime_config.device)
    identity = capture_g0_identity(root, runtime_config)
    root.mkdir(parents=True, exist_ok=False)
    _write_json_once(_identity_path(root), identity.to_dict())
    _write_json_once(
        root / "config" / "smoke.json",
        {
            "mode": "SMOKE",
            "source_class": "SMOKE_SYNTHETIC",
            "g0_identity": identity.identity,
            "reference": binding,
            "numerical_config": runtime_config.to_dict(),
        },
    )
    inputs = synthetic_qualification_inputs(
        dev1_timestamp_count=256, prediction_timestamp_count=256, vary_values=True
    )
    cache_root, cache_input = build_synthetic_cache(root, inputs=inputs)
    del inputs
    training, prediction_batch, cache = load_stage_cache_batches(cache_root, expected=cache_input)
    support = _smoke_terminal_support(root, runtime_config, decision_time)
    journal = CreateOnlyAttemptJournal(root)
    results: list[dict[str, Any]] = []
    keys = tuple(prediction_batch.row_keys)
    controls = {
        "ZERO_RETURN": [0.0] * len(keys),
        "LOCAL_RIDGE": [0.05] * len(keys),
        "FULLY_POOLED_LOCAL_RIDGE": [0.05] * len(keys),
    }
    control_identities = {name: _sha256(values) for name, values in controls.items()}
    for family in FITTED_FAMILY_IDS:
        attempt = AttemptIdentity(
            release_identity=identity.release_identity,
            g0_identity=identity.identity,
            output_root=str(root),
            slot_id=f"{family}:17:DEV_2",
            family_id=family,
            seed=17,
            stage="DEV_2",
            attempt=0,
            mode="SMOKE",
        )
        journal.append(
            attempt,
            "STARTED",
            {
                "config_identity": runtime_config.identity,
                "cache_identity": cache.cache_identity,
                "source_class": "SMOKE_SYNTHETIC",
            },
        )
        try:
            configure_deterministic_cuda(17)
            model = _build_real_family_model(family)
            train_telemetry = RuntimeTelemetry()
            fit = fit_one_model(
                model, training, training_blocks=("DEV_1",), _telemetry=train_telemetry
            )
            prediction_telemetry = RuntimeTelemetry()
            prediction = predict_residual(model, prediction_batch, _telemetry=prediction_telemetry)
            dispatch = {
                "training": _smoke_dispatch_evidence(train_telemetry, training=True),
                "prediction": _smoke_dispatch_evidence(prediction_telemetry, training=False),
            }
            # Cover every instrument at the first timestamp of each cached chunk.
            oracle_rows = [
                offset + node for offset in range(0, len(keys), 1280) for node in range(20)
            ]
            assert prediction_batch.cached_arrays is not None
            with torch.no_grad():
                oracle_prediction = torch.stack(
                    [
                        _oracle_scalar_forward(
                            model,
                            prediction_batch.cached_arrays,
                            int(prediction_batch.cached_arrays["row_timestamp"][row]),
                            prediction_batch.target_nodes[row],
                            dtype=next(model.parameters()).dtype,
                        )
                        for row in oracle_rows
                    ]
                )
            torch.testing.assert_close(
                prediction[oracle_rows], oracle_prediction, rtol=1e-5, atol=1e-6
            )
            oracle_delta = float((prediction[oracle_rows] - oracle_prediction).abs().max().item())
            del oracle_prediction
            model_hash = semantic_model_hash(model)
            prediction_hash = semantic_prediction_hash(prediction)
            persistence = _persist_real_model(
                root,
                attempt.slot_id,
                model,
                fit,
                prediction,
                identity,
                attempt_identity=attempt,
                prediction_batch=prediction_batch,
                config=runtime_config,
                foundation_content_identity=training.input_identity,
                training_batch_identity=training.content_identity,
                preprocessor_identity=training.preprocessor_identity,
                prediction_input_identity=prediction_batch.input_identity,
                target_keys=keys,
                local_ridge_forecasts=tuple(controls["LOCAL_RIDGE"]),
                fully_pooled_local_ridge_forecasts=tuple(controls["FULLY_POOLED_LOCAL_RIDGE"]),
                linear_control_identity=_sha256(control_identities),
                linear_controls=controls,
                linear_control_identities=control_identities,
                linear_control_support_identity=prediction_batch.support_identity,
            )
            del model, prediction
            configure_deterministic_cuda(17)
            repeated = _build_real_family_model(family)
            repeat_train = RuntimeTelemetry()
            fit_one_model(repeated, training, training_blocks=("DEV_1",), _telemetry=repeat_train)
            repeat_predict = RuntimeTelemetry()
            repeated_prediction = predict_residual(
                repeated, prediction_batch, _telemetry=repeat_predict
            )
            if (
                semantic_model_hash(repeated) != model_hash
                or semantic_prediction_hash(repeated_prediction) != prediction_hash
            ):
                raise ValueError("SMOKE deterministic repeat changed model or predictions")
            repeat_dispatch = {
                "training": _smoke_dispatch_evidence(repeat_train, training=True),
                "prediction": _smoke_dispatch_evidence(repeat_predict, training=False),
            }
            del repeated, repeated_prediction
            bundle = root / "attempts" / attempt.slot_id / "attempt-0"
            sealed = verify_attempt_bundle(bundle, attempt)
            journal.append(
                attempt,
                "SUCCEEDED",
                {
                    "seal_identity": cast(Mapping[str, object], sealed["seal"])["seal_identity"],
                },
            )
            results.append(
                {
                    "family_id": family,
                    "mode": "SMOKE",
                    "attempt_identity": attempt.identity,
                    "model_hash": model_hash,
                    "prediction_hash": prediction_hash,
                    "reload_prediction_equivalent": True,
                    "deterministic_repeat": True,
                    "scalar_oracle_max_abs_delta": oracle_delta,
                    "scalar_oracle_rows": len(oracle_rows),
                    "persistence": persistence,
                    "dispatch": dispatch,
                    "repeat_dispatch": repeat_dispatch,
                }
            )
        except Exception as exc:
            try:
                journal.append(attempt, "FAILED", {"error": str(exc)})
            except Exception as persistence_error:
                exc.add_note(f"SMOKE failure persistence failed: {persistence_error}")
            raise
    receipt = {
        "mode": "SMOKE",
        "source_class": "SMOKE_SYNTHETIC",
        "g0_identity": identity.identity,
        "reference": binding,
        "cache_identity": cache.cache_identity,
        "tensor_shape": [61, 20, 26],
        "training_timestamps": 256,
        "prediction_timestamps": 256,
        "batch_size": 64,
        "epochs": 1,
        "seed": 17,
        "families": results,
        "terminal_support_identity": support.artifact_identity,
        "outcomes_loaded": False,
        "scientific_performance": "NOT_COMPUTED",
    }
    _write_json_once(root / "smoke-receipt.json", receipt)
    return receipt


def _build_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--reference-root", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--manifest-path", type=Path, required=True)
    development = commands.add_parser("development-slot")
    development.add_argument("--all", action="store_true", dest="all_development")
    development.add_argument("--slot", action="append", default=[])
    wrapper = commands.add_parser("development-slot-wrapper")
    wrapper.add_argument("--slot", required=True)
    wrapper.add_argument("--epoch-id", required=True)
    wrapper.add_argument("--cache-receipt-identity", required=True)
    wrapper.add_argument("--handshake-fd", required=True, type=int)
    commands.add_parser("build-development-caches")
    commands.add_parser("development-supervise")
    commands.add_parser("close-register")
    commands.add_parser("terminal-support")
    prediction = commands.add_parser("terminal-predict")
    prediction.add_argument("--family", required=True)
    prediction.add_argument("--seed", required=True, type=int)
    commands.add_parser("metrics")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.output_root).resolve()
    if args.command == "smoke":
        result = run_bounded_smoke(root, reference_root=args.reference_root)
        print(json.dumps(result, sort_keys=True))
        return 0
    with preparation_entry(root):
        if preparation_root(root) != root and args.command in {
            "prepare",
            "build-development-caches",
        }:
            raise PermissionError("preparation reference inputs are immutable")
        return _main(argv)


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command != "prepare" and not _is_real_execution(Path(args.output_root).resolve()):
        raise ValueError("production CLI requires authenticated preparation artifacts")
    if args.command == "prepare":
        result: object = prepare_execution(
            args.output_root, manifest_path=args.manifest_path
        ).to_dict()
    elif args.command == "development-slot-wrapper":
        slot = _parse_slot(args.slot)
        result = {
            "slots": _run_receipted_development_slot(
                Path(args.output_root).resolve(),
                slot=slot,
                epoch_id=args.epoch_id,
                cache_receipt_identity=args.cache_receipt_identity,
                handshake_fd=args.handshake_fd,
            )
        }
    elif args.command == "development-slot":
        if args.all_development:
            raise ValueError("production development-slot rejects --all; use development-supervise")
        slots = tuple(_parse_slot(value) for value in args.slot)
        if len(slots) != 1:
            raise ValueError("production development-slot requires exactly one --slot")
        result = {
            "slots": run_development_slots(
                args.output_root,
                slots=slots,
                all_development=False,
            )
        }
    elif args.command == "build-development-caches":
        root = Path(args.output_root).resolve()
        identity = _require_identity(root)
        runtime_config = FrozenRuntimeConfig()
        built = _build_prepared_development_stage_cache(root, runtime_config, identity)
        result = {
            "cache_identities": {
                stage: values["verified_cache"].cache_identity for stage, values in built.items()
            }
        }
    elif args.command == "development-supervise":
        from .supervisor import (
            next_development_slot,
            reconcile_attempts,
            verify_epoch_caches,
            write_supervisor_session,
        )

        root = Path(args.output_root).resolve()
        identity = _require_identity(root)
        epoch_id = uuid.uuid4().hex
        pre_receipts = list(verify_epoch_caches(root, f"{epoch_id}-pre"))
        journal = CreateOnlyAttemptJournal(root)
        reconciliation = reconcile_attempts(root, journal)
        if reconciliation.live is not None:
            result = {
                "epoch_id": epoch_id,
                "live_slot": reconciliation.live.slot_id,
                "recovered": list(reconciliation.recovered),
                "slots": [],
            }
        else:
            write_supervisor_session(
                root,
                epoch_id,
                exact_head=identity.code_head,
                g0_identity=identity.identity,
                cache_receipts=pre_receipts,
            )
            cache_receipt_identity = pre_receipts[0]
            completed: list[str] = []
            while (selected := next_development_slot(journal)) is not None:
                family, seed, stage = selected
                slot_id = f"{family}:{seed}:{stage}"
                owned_attempt = _select_development_attempt(
                    root,
                    journal,
                    identity,
                    family=family,
                    seed=seed,
                    stage=stage,
                )
                wrapper_identity = wrapper_receipt_identity(
                    epoch_id, cache_receipt_identity, owned_attempt.identity
                )
                handshake_read, handshake_write = os.pipe()
                try:
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "experiments.r4_residual_graph.execution",
                            "--output-root",
                            str(root),
                            "development-slot-wrapper",
                            "--slot",
                            slot_id,
                            "--epoch-id",
                            epoch_id,
                            "--cache-receipt-identity",
                            cache_receipt_identity,
                            "--handshake-fd",
                            str(handshake_write),
                        ],
                        env={
                            **dict(os.environ),
                            "CUBLAS_WORKSPACE_CONFIG": CUBLAS_WORKSPACE_CONFIG,
                        },
                        pass_fds=(handshake_write,),
                    )
                finally:
                    os.close(handshake_write)
                try:
                    handshake = _read_wrapper_handshake(handshake_read)
                finally:
                    os.close(handshake_read)
                if handshake != f"{owned_attempt.identity}:{wrapper_identity}":
                    return_code = child.wait()
                    raise RuntimeError(
                        f"development wrapper failed before durable ownership for {slot_id}: "
                        f"{return_code}"
                    )
                return_code = child.wait()
                if return_code != 0:
                    reconcile_attempts(root, journal)
                    raise RuntimeError(
                        f"development slot child failed for {slot_id}: {return_code}"
                    )
                completed.append(slot_id)
                reconciliation = reconcile_attempts(root, journal)
                if reconciliation.live is not None:
                    raise RuntimeError("slot returned while its owned process remains live")
            final_receipts = list(verify_epoch_caches(root, f"{epoch_id}-final"))
            write_supervisor_session(
                root,
                f"{epoch_id}-final",
                exact_head=identity.code_head,
                g0_identity=identity.identity,
                cache_receipts=final_receipts,
            )
            result = {
                "epoch_id": epoch_id,
                "recovered": list(reconciliation.recovered),
                "slots": completed,
            }
    elif args.command == "close-register":
        result = {"register_identity": close_development_register(args.output_root)}
    elif args.command == "terminal-support":
        result = {"support_identity": materialise_terminal_support_stage(args.output_root)}
    elif args.command == "terminal-predict":
        result = {
            "prediction_identity": predict_terminal_slot(
                args.output_root,
                family_id=args.family,
                seed=args.seed,
            )
        }
    else:
        result = {"report_input_identity": aggregate_metrics(args.output_root)}
    print(json.dumps(result, sort_keys=True))
    return 0


# -- Authenticated execution path -------------------------------------------------
# The fixture path above remains available only to unit tests. Production CLI
# preparation binds an authenticated LAB-0 manifest; later stages derive inputs
# from that immutable binding.


def _real_manifest_path(root: Path) -> Path:
    return root / "config" / "execution-input.json"


def _write_bytes_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _support_payload(support: Any, *, g0_identity: str) -> dict[str, Any]:
    return {
        "artifact_type": "R4.C_AUTHENTICATED_SUPPORT_CAPSULE",
        "g0_identity": g0_identity,
        "keys": list(support.keys),
        "target_keys": list(support.target_keys),
        "target_nodes": list(support.target_nodes),
        "ordered_key_sha256": support.ordered_key_sha256,
        "key_count": support.key_count,
        "contract_identity": support.contract_identity,
        "lookback_minutes": support.lookback_minutes,
        "node_order": list(support.node_order),
        "feature_names": list(support.feature_names),
        "source_identity": support.source_identity,
        "manifest_sha256": support.manifest_sha256,
        "child_closure_sha256": support.child_closure_sha256,
        "row_content_identity": support.row_content_identity,
        "tensor_identities": [list(item) for item in support.tensor_identities],
        "mode": support.mode,
        "parent_identity": support.parent_identity,
        "support_identity": support.identity,
    }


def _build_execution_support_input(
    corpus: Any,
    parent: Any,
    decision_times: tuple[Any, ...],
    *,
    outcome_free: bool = False,
) -> Any:
    from .tensor import candidate_independent_support_input

    return candidate_independent_support_input(
        corpus.supplied_rows,
        decision_times,
        authenticated_parent=parent,
        _authenticated_corpus=corpus,
        _eligible_blocks=frozenset({"DEV_1", "DEV_2", "DEV_3"}),
        _outcome_free=outcome_free,
    )


def _build_execution_support(
    rows: Any,
    parent: Any,
    decision_times: tuple[Any, ...],
    *,
    outcome_free: bool = False,
    corpus: Any | None = None,
) -> Any:
    from .foundation import project_authenticated_parent_support_rows
    from .tensor import authenticate_support_corpus, bind_support_tensor_identities

    authenticated_corpus = corpus or authenticate_support_corpus(
        project_authenticated_parent_support_rows(parent), parent, outcome_free=outcome_free
    )
    capability = _build_execution_support_input(
        authenticated_corpus, parent, decision_times, outcome_free=outcome_free
    )
    tensor_identities = getattr(rows, "tensor_identities", None)
    if tensor_identities is not None:
        identities = {key: tensor_identities[key] for key in capability.support.keys}
    else:
        tensors = _build_tensors(rows, capability.support)
        identities = {key: _masked_tensor_identity(tensors[key]) for key in capability.support.keys}
    return bind_support_tensor_identities(capability, identities)


def _build_tensors(rows: Any, support: Any) -> Mapping[str, Any]:
    from datetime import datetime, timedelta

    import polars as pl

    from .tensor import LazyTensorMapping, build_masked_sequence

    duplicate = rows.select(
        pl.struct(["instrument_id", "decision_time"]).is_duplicated().any()
    ).item()
    if duplicate:
        raise ValueError("duplicate tensor key")
    indexed_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows.iter_rows(named=True):
        timestamp = row["decision_time"].isoformat()
        indexed_rows.setdefault(timestamp, []).append(dict(row))

    def factory(key: str) -> Any:
        timestamp = datetime.fromisoformat(key)
        window_rows: list[dict[str, Any]] = []
        for offset in range(61):
            minute = timestamp - timedelta(minutes=60 - offset)
            window_rows.extend(indexed_rows.get(minute.isoformat(), ()))
        return build_masked_sequence(window_rows, timestamp)

    return LazyTensorMapping(support.keys, factory, worker_rows=indexed_rows)


def _build_training_partition(context: dict[str, Any]) -> tuple[Any, Sequence[Any]]:
    from datetime import datetime

    import experiments.r4_residual_graph.tensor as tensor_module

    from .tensor import LazyTensorSequence, build_training_tensor_partition
    from .tensor_store import RawTensorStore, _PreparationTensorSequence

    support, tensors = context["support"], context["tensors"]
    if not support.keys:
        raise ValueError("training preprocessing requires an authenticated tensor set")
    tensor_sequence = (
        LazyTensorSequence(support.keys, lambda key: tensors[key])
        if isinstance(tensors, Mapping)
        else tensors
    )
    if "_preparation_capability" in context:
        tensor_sequence = _PreparationTensorSequence(
            cast(RawTensorStore, tensors), support.keys, context["_preparation_capability"]
        )
    partition = build_training_tensor_partition(
        tensor_sequence,
        row_keys=support.keys,
        source_identity=support.source_identity,
        support_identity=support.identity,
        training_cutoff=datetime.fromisoformat(support.keys[-1]),
        support=support,
        authenticated_parent=context["parent"],
        tensor_identities=tuple(identity for _, identity in support.tensor_identities),
        _capability=tensor_module._TRAINING_PARTITION_BUILDER_CAPABILITY,
    )
    return partition, tensor_sequence


def _build_training_objects(context: dict[str, Any]) -> tuple[Any, Any, Sequence[Any]]:
    from .tensor import fit_training_preprocessor

    partition, tensor_sequence = _build_training_partition(context)
    return partition, fit_training_preprocessor(partition), tensor_sequence


def _development_stage_preprocessor_identity(context: dict[str, Any], stage: str) -> str:
    import polars as pl

    train_blocks = ("DEV_1",) if stage == "DEV_2" else ("DEV_1", "DEV_2")
    outcome_free = "target_return" not in context["rows"].columns
    train_times = tuple(
        sorted(
            context["rows"]
            .filter(pl.col("block").is_in(train_blocks))
            .get_column("decision_time")
            .unique()
            .to_list()
        )
    )
    stage_support = _build_execution_support(
        context["tensors"],
        context["parent"],
        train_times,
        outcome_free=outcome_free,
        corpus=context.get("support_corpus"),
    )
    _, stage_preprocessor, _ = _build_training_objects(
        {"support": stage_support, "tensors": context["tensors"], "parent": context["parent"]}
    )
    return training_preprocessor_identity(stage_preprocessor)


def _development_stage_preprocessor_identities(
    context: dict[str, Any], stages: tuple[str, ...]
) -> dict[str, str]:
    identities: dict[str, str] = {}
    for stage in stages:
        if stage not in identities:
            identities[stage] = _development_stage_preprocessor_identity(context, stage)
    return identities


def _persist_prepared_development_stages(
    root: Path,
    context: dict[str, Any],
    raw_store: Any,
    global_partition: Any,
    raw_tensor_identities: Mapping[str, str],
) -> tuple[dict[str, dict[str, str]], Any]:
    """Seal stage-specific support, preprocessing and row mappings during preparation."""
    import polars as pl

    from .prepared_stage import persist_prepared_stage
    from .runtime import (
        PredictionBatch,
        ResidualTrainingBatch,
        project_authenticated_training_rows,
        validate_residual_foundation,
    )
    from .tensor import (
        bind_support_tensor_identities,
        fit_training_preprocessors,
    )

    foundation_capability = validate_residual_foundation(context["foundation"])
    stage_inputs: dict[str, tuple[Any, Any, str]] = {}
    partitions = {"GLOBAL": global_partition}
    for stage in _DEVELOPMENT_STAGES:
        train_blocks = ("DEV_1",) if stage == "DEV_2" else ("DEV_1", "DEV_2")
        eval_block = "DEV_2" if stage == "DEV_2" else "DEV_3"
        train_times = tuple(
            sorted(
                context["rows"]
                .filter(pl.col("block").is_in(train_blocks))
                .get_column("decision_time")
                .unique()
                .to_list()
            )
        )
        stage_input = _build_execution_support_input(
            context["support_corpus"], context["parent"], train_times
        )
        stage_support = bind_support_tensor_identities(
            stage_input,
            {key: raw_tensor_identities[key] for key in stage_input.support.keys},
        )
        partition, _ = _build_training_partition(
            {
                "support": stage_support,
                "tensors": raw_store,
                "parent": context["parent"],
                **(
                    {"_preparation_capability": context["_preparation_capability"]}
                    if "_preparation_capability" in context
                    else {}
                ),
            }
        )
        partitions[stage] = partition
        eval_times = tuple(
            sorted(
                context["rows"]
                .filter(pl.col("block") == eval_block)
                .get_column("decision_time")
                .unique()
                .to_list()
            )
        )
        eval_input = _build_execution_support_input(
            context["support_corpus"], context["parent"], eval_times
        )
        eval_support = bind_support_tensor_identities(
            eval_input,
            {key: raw_tensor_identities[key] for key in eval_input.support.keys},
        )
        stage_inputs[stage] = (stage_support, eval_support, eval_block)

    preprocessors = fit_training_preprocessors(partitions)
    persisted: dict[str, dict[str, str]] = {}
    for stage in _DEVELOPMENT_STAGES:
        stage_support, eval_support, eval_block = stage_inputs[stage]
        preprocessor = preprocessors[stage]
        eval_tensor_identities = dict(eval_support.tensor_identities)
        input_identity = _sha256(
            {
                "support_identity": eval_support.identity,
                "row_keys": eval_support.target_keys,
                "tensor_identities": tuple(
                    eval_tensor_identities[key.rsplit("|", 1)[-1]]
                    for key in eval_support.target_keys
                ),
                "preprocessor_identity": training_preprocessor_identity(preprocessor),
                "policy": TIMESTAMP_MATERIALISATION_POLICY,
            }
        )
        projected_training_rows = project_authenticated_training_rows(
            context["foundation"],
            stage_support,
            foundation_capability=foundation_capability,
        )
        training = ResidualTrainingBatch.from_authenticated_oof(
            tensors=raw_store,
            foundation=context["foundation"],
            support=stage_support,
            preprocessor=preprocessor,
            projected_rows=projected_training_rows,
            foundation_capability=foundation_capability,
            stream=True,
        )
        prediction = PredictionBatch.from_authenticated_support(
            tensors=raw_store,
            support=eval_support,
            preprocessor=preprocessor,
            input_identity=input_identity,
            stream=True,
        )
        path = persist_prepared_stage(
            root / "input" / "prepared-development",
            stage=stage,
            raw_store=raw_store,
            training=training,
            prediction=prediction,
            preprocessor=preprocessor,
            evaluation_block=eval_block,
        )
        persisted[stage] = {
            "path": str(path.resolve()),
            "stage_identity": path.name,
            "raw_store_path": str(raw_store.root.resolve()),
            "raw_store_identity": raw_store.manifest["store_identity"],
        }
    return persisted, preprocessors["GLOBAL"]


def _build_development_stage_cache(
    root: Path,
    context: dict[str, Any],
    foundation: Any,
    support: Any,
    tensors: Mapping[str, Any],
    selected: tuple[tuple[str, int, str], ...],
    runtime_config: FrozenRuntimeConfig,
    identity: G0ExecutionIdentity,
) -> dict[str, dict[str, Any]]:
    """Build, authenticate and mmap each development stage cache exactly once."""
    import polars as pl

    from .runtime import PredictionBatch, ResidualTrainingBatch, environment_identity
    from .stage_cache import CacheInput, build_stage_cache, load_stage_cache_batches
    from .tensor import TensorContract

    stage_cache: dict[str, dict[str, Any]] = {}
    for stage in tuple(dict.fromkeys(slot[2] for slot in selected)):
        train_blocks = ("DEV_1",) if stage == "DEV_2" else ("DEV_1", "DEV_2")
        eval_block = "DEV_2" if stage == "DEV_2" else "DEV_3"
        train_times = tuple(
            sorted(
                context["rows"]
                .filter(pl.col("block").is_in(train_blocks))
                .get_column("decision_time")
                .unique()
                .to_list()
            )
        )
        stage_support = _build_execution_support(context["rows"], context["parent"], train_times)
        stage_tensors = _build_tensors(context["rows"], stage_support)
        _, stage_preprocessor, _ = _build_training_objects(
            {"support": stage_support, "tensors": stage_tensors, "parent": context["parent"]}
        )
        eval_times = tuple(
            sorted(
                context["rows"]
                .filter(pl.col("block") == eval_block)
                .get_column("decision_time")
                .unique()
                .to_list()
            )
        )
        eval_support = _build_execution_support(context["rows"], context["parent"], eval_times)
        eval_tensors = _build_tensors(context["rows"], eval_support)
        eval_tensor_identities = (
            dict(eval_support.tensor_identities)
            if hasattr(eval_support, "tensor_identities")
            else {
                key.rsplit("|", 1)[-1]: _masked_tensor_identity(
                    eval_tensors[key.rsplit("|", 1)[-1]]
                )
                for key in eval_support.target_keys
            }
        )
        preprocessor_identity = training_preprocessor_identity(stage_preprocessor)
        input_identity = _sha256(
            {
                "support_identity": eval_support.identity,
                "row_keys": eval_support.target_keys,
                "tensor_identities": tuple(
                    eval_tensor_identities[key.rsplit("|", 1)[-1]]
                    for key in eval_support.target_keys
                ),
                "preprocessor_identity": preprocessor_identity,
                "policy": TIMESTAMP_MATERIALISATION_POLICY,
            }
        )
        prediction_batch = PredictionBatch.from_authenticated_support(
            tensors=eval_tensors,
            support=eval_support,
            preprocessor=stage_preprocessor,
            input_identity=input_identity,
            stream=True,
        )
        training_batch = ResidualTrainingBatch.from_authenticated_oof(
            tensors=tensors,
            foundation=foundation,
            support=support,
            preprocessor=stage_preprocessor,
            stream=True,
        )
        cache_input = CacheInput(
            stage=stage,
            training_blocks=train_blocks,
            foundation_identity=foundation.content_identity,
            support_identity=stage_support.identity,
            tensor_identity=TensorContract().identity,
            preprocessor_identity=preprocessor_identity,
            config_identity=runtime_config.identity,
            numerical_runtime_identity=_sha256(environment_identity()),
            producer_head=identity.code_head,
            universe=tuple(ALL_INSTRUMENTS),
            node_order=tuple(ALL_INSTRUMENTS),
            feature_order=tuple(TensorContract().feature_names),
        )
        stage_root = root / "stage-cache" / stage
        existing = tuple(stage_root.iterdir()) if stage_root.is_dir() else ()
        if len(existing) > 1:
            raise ValueError("development stage has multiple cache candidates")
        if existing:
            cached_training, cached_prediction, verified_cache = load_stage_cache_batches(
                existing[0], expected=cache_input
            )
        elif _cache_build_allowed.get():
            verified_cache = build_stage_cache(
                root,
                cache_input,
                training_batch=training_batch,
                prediction_batch=prediction_batch,
                build_id=f"{identity.identity}-{stage}",
            )
            cached_training, cached_prediction, _ = load_stage_cache_batches(
                verified_cache.root, expected=cache_input, full_verify=False
            )
        else:
            raise FileNotFoundError(f"sealed {stage} stage cache is required before execution")
        stage_cache[stage] = {
            "support": stage_support,
            "tensors": stage_tensors,
            "preprocessor": stage_preprocessor,
            "preprocessor_identity": preprocessor_identity,
            "training_batch": cached_training,
            "prediction_batch": cached_prediction,
            "eval_support": eval_support,
            "input_identity": input_identity,
            "verified_cache": verified_cache,
        }
    if _cache_build_allowed.get() and set(stage_cache) == set(_DEVELOPMENT_STAGES):
        _persist_development_execution_capsule(root, identity, runtime_config, context, stage_cache)
    return stage_cache


def _build_prepared_development_stage_cache(
    root: Path,
    runtime_config: FrozenRuntimeConfig,
    identity: G0ExecutionIdentity,
) -> dict[str, dict[str, Any]]:
    """Build caches solely from receipt-authenticated prepared mmap stage inputs."""
    root = Path(root).resolve()
    from .prepared_stage import load_prepared_stage
    from .runtime import environment_identity
    from .stage_cache import CacheInput, build_stage_cache, load_stage_cache_batches
    from .tensor import TensorContract
    from .tensor_store import load_raw_tensor_store

    binding = _read_json(_real_manifest_path(root))
    if binding["g0_identity"] != identity.identity:
        raise ValueError("prepared cache execution binding drifted")
    control = _read_json(Path(binding["control_regression_path"]))
    if (
        _file_digest(Path(binding["control_regression_path"]))
        != binding["control_regression_file_sha256"]
        or control["artifact_identity"] != binding["control_regression_identity"]
    ):
        raise ValueError("prepared cache control receipt drifted")
    preprocessor_payload = _read_json(root / "input" / "preprocessor.json")
    raw_store_identity = binding["raw_tensor_store_identity"]
    expected_raw_store_root = root / "input" / "raw-tensors" / raw_store_identity
    if binding["raw_tensor_store_path"] != str(expected_raw_store_root):
        raise ValueError("prepared development raw tensor store binding drifted")
    authenticated_raw_store = load_raw_tensor_store(
        expected_raw_store_root, expected_identity=raw_store_identity
    )
    stage_cache: dict[str, dict[str, Any]] = {}
    for stage in _DEVELOPMENT_STAGES:
        prepared_binding = binding["prepared_development_stages"][stage]
        expected_stage_identity = prepared_binding["stage_identity"]
        expected_prepared_root = (
            root / "input" / "prepared-development" / stage / expected_stage_identity
        )
        if (
            prepared_binding["path"] != str(expected_prepared_root)
            or binding["raw_tensor_store_path"] != str(expected_raw_store_root)
            or prepared_binding["raw_store_path"] != str(expected_raw_store_root)
            or prepared_binding["raw_store_identity"] != binding["raw_tensor_store_identity"]
        ):
            raise ValueError("prepared development stage binding drifted")
        training, prediction, preprocessor, _prepared = load_prepared_stage(
            expected_prepared_root,
            expected_stage_identity=expected_stage_identity,
            expected_raw_store_path=expected_raw_store_root,
            expected_raw_store_identity=binding["raw_tensor_store_identity"],
            trusted_root=root,
            authenticated_raw_store=authenticated_raw_store,
        )
        train_blocks = ("DEV_1",) if stage == "DEV_2" else ("DEV_1", "DEV_2")
        cache_input = CacheInput(
            stage=stage,
            training_blocks=train_blocks,
            foundation_identity=binding["foundation_identity"],
            support_identity=training.support_identity,
            tensor_identity=TensorContract().identity,
            preprocessor_identity=training_preprocessor_identity(preprocessor),
            config_identity=runtime_config.identity,
            numerical_runtime_identity=_sha256(environment_identity()),
            producer_head=identity.code_head,
            universe=tuple(ALL_INSTRUMENTS),
            node_order=tuple(ALL_INSTRUMENTS),
            feature_order=tuple(TensorContract().feature_names),
        )
        verified = build_stage_cache(
            root,
            cache_input,
            training_batch=training,
            prediction_batch=prediction,
            build_id=f"{identity.identity}-{stage}",
        )
        cached_training, cached_prediction, _ = load_stage_cache_batches(
            verified.root, expected=cache_input, full_verify=False
        )
        stage_cache[stage] = {
            "preprocessor": preprocessor,
            "preprocessor_identity": training_preprocessor_identity(preprocessor),
            "training_batch": cached_training,
            "prediction_batch": cached_prediction,
            "eval_support": SimpleNamespace(target_keys=tuple(prediction.row_keys)),
            "input_identity": prediction.input_identity,
            "verified_cache": verified,
        }
    context = {
        "foundation": SimpleNamespace(content_identity=binding["foundation_identity"]),
        "support": SimpleNamespace(identity=binding["support_identity"]),
        "preprocessor": SimpleNamespace(
            training_partition_identity=preprocessor_payload["training_partition_identity"]
        ),
        "binding": binding,
        "control_regression": control,
    }
    _persist_development_execution_capsule(root, identity, runtime_config, context, stage_cache)
    return stage_cache


def _development_execution_capsule_path(root: Path) -> Path:
    return root.resolve() / "input" / "development-execution-context.json"


def _development_stage_capsule_path(root: Path, stage: str) -> Path:
    return root.resolve() / "input" / "development-execution-stages" / f"{stage}.json"


def _development_stage_bulk_path(root: Path, stage: str, name: str) -> Path:
    return root.resolve() / "input" / "development-execution-stages" / stage / f"{name}.npy"


def _write_npy_once(path: Path, value: Any) -> dict[str, Any]:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        np.save(handle, value, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _file_digest(path),
        "dtype": array.dtype.str,
        "shape": list(array.shape),
    }


def _load_sealed_stage_array(descriptor: Mapping[str, Any], expected_path: Path) -> Any:
    import numpy as np

    path = Path(str(descriptor["path"]))
    if (
        path != expected_path
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != descriptor["size_bytes"]
        or _file_digest(path) != descriptor["sha256"]
    ):
        raise ValueError("development execution stage bulk descriptor drifted")
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    if array.dtype.str != descriptor["dtype"] or list(array.shape) != descriptor["shape"]:
        raise ValueError("development execution stage bulk array drifted")
    return array


def _persist_development_execution_capsule(
    root: Path,
    identity: G0ExecutionIdentity,
    runtime_config: FrozenRuntimeConfig,
    context: dict[str, Any],
    stage_cache: Mapping[str, Mapping[str, Any]],
) -> None:
    import numpy as np

    stages: dict[str, Any] = {}
    stage_root = root.resolve() / "input" / "development-execution-stages"
    stage_root.mkdir(exist_ok=True)
    for stage in _DEVELOPMENT_STAGES:
        item = stage_cache[stage]
        verified = item["verified_cache"]
        manifest = _read_json(verified.root / "manifest.json")
        eval_block = "DEV_2" if stage == "DEV_2" else "DEV_3"
        target_keys = tuple(item["eval_support"].target_keys)
        target_width = max(map(len, target_keys))
        target_descriptor = _write_npy_once(
            _development_stage_bulk_path(root, stage, "target-keys"),
            np.asarray(target_keys, dtype=f"<U{target_width}"),
        )
        control_bytes = _canonical(context["control_regression"]["periods"][eval_block])
        control_descriptor = _write_npy_once(
            _development_stage_bulk_path(root, stage, "control-period"),
            np.frombuffer(control_bytes, dtype=np.uint8),
        )
        stage_payload = {
            "schema": "R4-P0-DEVELOPMENT-EXECUTION-STAGE-V2",
            "stage": stage,
            "cache_root": str(verified.root.resolve()),
            "cache_identity": verified.cache_identity,
            "semantic_inputs": manifest["semantic_inputs"],
            "training_batch_identity": item["training_batch"].content_identity,
            "prediction_batch_identity": item["prediction_batch"].content_identity,
            "preprocessor_identity": item["preprocessor_identity"],
            "prediction_input_identity": item["input_identity"],
            "target_keys": target_descriptor,
            "eval_block": eval_block,
            "control_period": control_descriptor,
        }
        stage_payload["stage_identity"] = _sha256(stage_payload)
        stage_path = _development_stage_capsule_path(root, stage)
        if stage_path.exists():
            if _read_json(stage_path) != stage_payload:
                raise FileExistsError("development execution stage capsule differs")
        else:
            _write_json_once(stage_path, stage_payload)
        stage_stat = stage_path.stat()
        stages[stage] = {
            "path": str(stage_path),
            "size_bytes": stage_stat.st_size,
            "sha256": _file_digest(stage_path),
            "stage_identity": stage_payload["stage_identity"],
        }
    payload: dict[str, Any] = {
        "schema": "R4-P0-DEVELOPMENT-EXECUTION-CONTEXT-V2",
        "g0_identity": identity.identity,
        "config_identity": runtime_config.identity,
        "foundation_content_identity": context["foundation"].content_identity,
        "support_identity": context["support"].identity,
        "training_partition_identity": context["preprocessor"].training_partition_identity,
        "preparation_preprocessor_identity": context["binding"]["preprocessor_identity"],
        "control_regression_identity": context["control_regression"]["artifact_identity"],
        "stages": stages,
    }
    payload["capsule_identity"] = _sha256(payload)
    path = _development_execution_capsule_path(root)
    if path.exists():
        if _read_json(path) != payload:
            raise FileExistsError("development execution context capsule differs")
        return
    _write_json_once(path, payload)


def _load_development_execution_capsule(
    root: Path,
    identity: G0ExecutionIdentity,
    runtime_config: FrozenRuntimeConfig,
    *,
    selected_stages: Sequence[str] | None = None,
    metadata_only: bool = False,
) -> dict[str, Any]:
    expected_g0 = preparation_g0(root, identity.identity)
    root = preparation_root(root)
    path = _development_execution_capsule_path(root)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("sealed development execution context capsule is required")
    payload = _read_json(path)
    capsule_identity = payload.pop("capsule_identity")
    if _sha256(payload) != capsule_identity:
        raise ValueError("development execution context capsule identity drifted")
    payload["capsule_identity"] = capsule_identity
    if (
        payload["schema"] != "R4-P0-DEVELOPMENT-EXECUTION-CONTEXT-V2"
        or payload["g0_identity"] != expected_g0
        or payload["config_identity"] != runtime_config.identity
        or set(payload["stages"]) != set(_DEVELOPMENT_STAGES)
    ):
        raise ValueError("development execution context capsule binding drifted")
    requested = tuple(_DEVELOPMENT_STAGES if selected_stages is None else selected_stages)
    if not requested or not set(requested).issubset(_DEVELOPMENT_STAGES):
        raise ValueError("development execution stage selection drifted")
    loaded: dict[str, Any] = {}
    for stage in requested:
        descriptor = payload["stages"][stage]
        stage_path = _development_stage_capsule_path(root, stage)
        if (
            descriptor["path"] != str(stage_path)
            or stage_path.is_symlink()
            or not stage_path.is_file()
            or stage_path.stat().st_size != descriptor["size_bytes"]
            or _file_digest(stage_path) != descriptor["sha256"]
        ):
            raise ValueError("development execution stage descriptor drifted")
        stage_payload = _read_json(stage_path)
        stage_identity = stage_payload.pop("stage_identity")
        if (
            _sha256(stage_payload) != stage_identity
            or stage_identity != descriptor["stage_identity"]
            or stage_payload["schema"] != "R4-P0-DEVELOPMENT-EXECUTION-STAGE-V2"
            or stage_payload["stage"] != stage
        ):
            raise ValueError("development execution stage capsule identity drifted")
        if metadata_only:
            stage_payload["stage_identity"] = stage_identity
            loaded[stage] = stage_payload
            continue
        target_array = _load_sealed_stage_array(
            stage_payload["target_keys"],
            _development_stage_bulk_path(root, stage, "target-keys"),
        )
        control_array = _load_sealed_stage_array(
            stage_payload["control_period"],
            _development_stage_bulk_path(root, stage, "control-period"),
        )
        stage_payload["target_keys"] = tuple(str(value) for value in target_array)
        control_period = strict_json_object(bytes(control_array).decode())
        control_identity = dict(control_period)
        expected_identity = control_identity.pop("identity")
        if _sha256(control_identity) != expected_identity:
            raise ValueError("development execution stage control identity drifted")
        stage_payload["control_period"] = control_period
        stage_payload["stage_identity"] = stage_identity
        loaded[stage] = stage_payload
    payload["stages"] = loaded
    return payload


def _capsule_cache_input(stage_payload: Mapping[str, Any], identity: G0ExecutionIdentity) -> Any:
    from .stage_cache import CacheInput

    raw = stage_payload["semantic_inputs"]
    return CacheInput(
        stage=raw["stage"],
        training_blocks=tuple(raw["training_blocks"]),
        foundation_identity=raw["foundation_identity"],
        support_identity=raw["support_identity"],
        tensor_identity=raw["tensor_identity"],
        preprocessor_identity=raw["preprocessor_identity"],
        config_identity=raw["config_identity"],
        numerical_runtime_identity=raw["numerical_runtime_identity"],
        producer_head=identity.code_head,
        universe=tuple(raw["universe"]),
        node_order=tuple(raw["node_order"]),
        feature_order=tuple(raw["feature_order"]),
    )


def _load_authenticated_parent_context(
    root: Path, config: FrozenRuntimeConfig | None = None
) -> dict[str, Any]:
    """Authenticate only the immutable G0/LAB-0 parent boundary."""
    from .foundation import authenticate_parent

    root = Path(root).resolve()
    current_identity = _require_identity(root, config)
    binding = _read_json(_real_manifest_path(preparation_root(root)))
    if binding["g0_identity"] != preparation_g0(root, current_identity.identity):
        raise ValueError("authenticated execution binding G0 identity drifted")
    if (
        binding["manifest_sha256"] != MANIFEST_SHA256
        or binding["manifest_identity"] != MANIFEST_IDENTITY
    ):
        raise ValueError("authenticated LAB-0 manifest is not the frozen manifest")
    manifest_path = Path(binding["manifest_path"]).resolve()
    if not manifest_path.is_file() or _file_digest(manifest_path) != MANIFEST_SHA256:
        raise ValueError("authenticated LAB-0 manifest drift detected")
    parent = authenticate_parent(
        manifest_path=manifest_path, expected_manifest_sha256=MANIFEST_SHA256
    )
    if parent.manifest_sha256 != MANIFEST_SHA256:
        raise ValueError("authenticated LAB-0 manifest identity drifted")
    return {
        "current_identity": current_identity,
        "binding": binding,
        "parent": parent,
        "runtime_config": config or FrozenRuntimeConfig(),
    }


def _validate_persisted_support_keys(
    persisted_rows: Any, persisted_record: Any, support: Any
) -> None:
    persisted_row_keys = tuple(
        f"{row['instrument_id']!s}|{_as_utc(row['decision_time']).isoformat()}"
        for row in persisted_rows.iter_rows(named=True)
    )
    if (
        persisted_record.keys != support.keys
        or persisted_record.target_keys != support.target_keys
        or persisted_row_keys != support.target_keys
    ):
        raise ValueError("persisted execution support canonical key drift detected")


def _load_outcome_free_tensor_store(root: Path, binding: Mapping[str, Any]) -> Any:
    """Authenticate the accepted raw store once, without reconstructing source windows."""
    from .tensor_store import load_raw_tensor_store

    store_identity = binding["raw_tensor_store_identity"]
    store_root = root / "input" / "raw-tensors" / store_identity
    if binding["raw_tensor_store_path"] != str(store_root):
        raise ValueError("outcome-free raw tensor store path binding drifted")
    return load_raw_tensor_store(store_root, expected_identity=store_identity)


def _restore_outcome_free_preprocessor(
    root: Path, binding: Mapping[str, Any], support: Any, tensors: Any
) -> Any:
    """Restore accepted statistics after binding their partition to authenticated inputs."""
    from .tensor import TensorContract, load_authenticated_training_preprocessor

    contract = TensorContract()
    if (
        tuple(tensors) != support.keys
        or tensors.manifest["timestamps"] != len(support.keys)
        or tensors.manifest["source_identity"] != support.source_identity
        or tensors.manifest["tensor_contract_identity"] != contract.identity
        or support.contract_identity != contract.identity
    ):
        raise ValueError("outcome-free raw tensor store source, keys or contract drifted")
    tensor_identities = tensors.tensor_identities
    identities = tuple(tensor_identities[key] for key in support.keys)
    if tuple(support.tensor_identities) != tuple(zip(support.keys, identities, strict=True)):
        raise ValueError("outcome-free support tensor identity binding drifted")
    cutoff = support.keys[-1]
    partition_seal = _sha256(
        {
            "row_keys": support.keys,
            "source_identity": support.source_identity,
            "support_identity": support.identity,
            "training_cutoff": cutoff,
            "tensor_identities": identities,
        }
    )
    partition_path = root / "input" / "training-partition.json"
    if _file_digest(partition_path) != binding["partition_file_sha256"]:
        raise ValueError("persisted training partition drift detected")
    partition_payload = _read_json(partition_path)
    if partition_payload != {
        "seal": partition_seal,
        "support_identity": support.identity,
        "source_identity": support.source_identity,
        "row_keys": list(support.keys),
        "tensor_identities": list(identities),
        "tensor_identity": _sha256({"tensor_identities": identities}),
    }:
        raise ValueError("persisted training partition binding drift detected")
    preprocessor_path = root / "input" / "preprocessor.json"
    if _file_digest(preprocessor_path) != binding["preprocessor_file_sha256"]:
        raise ValueError("persisted preprocessor drift detected")
    preprocessor_payload = _read_json(preprocessor_path)
    if (
        preprocessor_payload["training_partition_identity"] != partition_seal
        or preprocessor_payload["training_cutoff"] != cutoff
        or preprocessor_payload["contract_identity"] != contract.identity
    ):
        raise ValueError("persisted preprocessor partition binding drift detected")
    return load_authenticated_training_preprocessor(
        {**preprocessor_payload, "preprocessor_identity": binding["preprocessor_identity"]}
    )


def _load_real_context(
    root: Path, config: FrozenRuntimeConfig | None = None, *, outcome_free: bool = False
) -> dict[str, Any]:
    import polars as pl

    from .foundation import (
        authenticate_oof_residual_foundation,
        authenticate_parent,
        load_parent_rows,
        reconstruct_authenticated_linear_forecasts,
    )

    current_identity = _require_identity(root, config)
    root = preparation_root(root)
    binding = _read_json(_real_manifest_path(root))
    retained_identity = _read_json(_identity_path(root))["identity"]
    if binding.get("g0_identity") != retained_identity:
        raise ValueError("authenticated execution binding G0 identity drifted")
    if (
        binding.get("manifest_sha256") != MANIFEST_SHA256
        or binding.get("manifest_identity") != MANIFEST_IDENTITY
    ):
        raise ValueError("authenticated LAB-0 manifest is not the frozen manifest")
    manifest_path = Path(str(binding["manifest_path"])).resolve()
    if not manifest_path.is_file() or _file_digest(manifest_path) != MANIFEST_SHA256:
        raise ValueError("authenticated LAB-0 manifest drift detected")
    parent = authenticate_parent(
        manifest_path=manifest_path, expected_manifest_sha256=MANIFEST_SHA256
    )
    if parent.manifest_sha256 != MANIFEST_SHA256:
        raise ValueError("authenticated LAB-0 manifest identity drift detected")

    def bound_path(key: str) -> Path:
        path = Path(str(binding[key])).resolve()
        if path.parent != (root / "input").resolve():
            raise ValueError(f"authenticated execution path escapes canonical input layout: {key}")
        return path

    foundation_path = bound_path("foundation_path")
    support_path = bound_path("support_path")
    development_support_path = bound_path("development_support_path")
    control_regression_path = bound_path("control_regression_path")
    if _file_digest(foundation_path) != binding["foundation_file_sha256"]:
        raise ValueError("persisted residual foundation drift detected")
    if _file_digest(development_support_path) != binding["development_support_file_sha256"]:
        raise ValueError("persisted development support drift detected")
    if _file_digest(support_path) != binding["support_file_sha256"]:
        raise ValueError("persisted execution support drift detected")
    if _file_digest(control_regression_path) != binding["control_regression_file_sha256"]:
        raise ValueError("persisted control regression drift detected")
    control_payload = _read_json(control_regression_path)
    control_identity = control_payload.get("artifact_identity")
    control_check = dict(control_payload)
    control_check.pop("artifact_identity", None)
    if control_identity != _sha256(control_check):
        raise ValueError("persisted control regression self-hash drifted")
    if (
        control_identity != binding["control_regression_identity"]
        or control_payload.get("g0_identity") != retained_identity
        or control_payload.get("parent_identity") != parent.child_closure_sha256
        or control_payload.get("manifest_sha256") != parent.manifest_sha256
    ):
        raise ValueError("persisted control regression identity drifted")
    foundation = authenticate_oof_residual_foundation(
        pl.read_parquet(foundation_path), capsule=parent
    )
    if foundation.content_identity != binding["foundation_identity"]:
        raise ValueError("persisted residual foundation identity drift detected")
    raw_store = _load_outcome_free_tensor_store(root, binding) if outcome_free else None
    support = _build_execution_support(
        raw_store if outcome_free else load_parent_rows(parent, include_return=True),
        parent,
        tuple(sorted({row["decision_time"] for row in foundation.rows.iter_rows(named=True)})),
        outcome_free=outcome_free,
    )
    support_payload = _read_json(support_path.with_suffix(".json"))
    support_content_identity = support_payload.get("content_identity")
    support_check = dict(support_payload)
    support_check.pop("content_identity", None)
    if support_content_identity != _sha256(support_check):
        raise ValueError("persisted execution support sidecar self-hash drifted")
    if (
        support_payload["g0_identity"] != binding["g0_identity"]
        or support_payload["support_identity"] != binding["support_identity"]
        or support_payload["support_identity"] != support.identity
        or support_payload["row_content_identity"] != support.row_content_identity
        or support_payload["g0_identity"] != retained_identity
    ):
        raise ValueError("persisted execution support identity drift detected")
    persisted_support = pl.read_parquet(support_path)
    from .tensor import _SUPPORT_ALLOWED_COLUMNS, candidate_independent_support

    persisted_support_record = candidate_independent_support(
        persisted_support.select(sorted(_SUPPORT_ALLOWED_COLUMNS)),
        tuple(sorted({row["decision_time"] for row in persisted_support.iter_rows(named=True)})),
        authenticated_parent=parent,
        _eligible_blocks=frozenset(DEVELOPMENT_BLOCKS),
        _outcome_free=outcome_free,
        _defer_tensor_identities=outcome_free,
    )
    _validate_persisted_support_keys(persisted_support, persisted_support_record, support)
    if outcome_free:
        rows = load_parent_rows(parent, include_return=False)
        if "target_return" in rows.columns:
            raise ValueError("outcome-free terminal context unexpectedly contains target_return")
    else:
        rows = load_parent_rows(parent, include_return=True)
        expected_regression = _reconstruct_control_regression(rows, capsule=parent)
        if not _replay_values_equal(control_payload.get("regression"), expected_regression):
            raise ValueError("persisted control regression values drifted")
        expected_stage_controls = reconstruct_authenticated_linear_forecasts(
            rows,
            validation_by_period={
                period: persisted_support.filter(pl.col("block") == period)
                for period in DEVELOPMENT_BLOCKS
            },
            periods=DEVELOPMENT_BLOCKS,
            capsule=parent,
            support_identity=support.identity,
            support=support,
        )
        for period in DEVELOPMENT_BLOCKS:
            expected_period = expected_stage_controls[period]
            actual_period = control_payload.get("periods", {}).get(period)
            if not isinstance(actual_period, dict):
                raise ValueError("persisted control regression period is missing")
            expected_keys = tuple(expected_period["keys"])
            expected_controls = {
                "ZERO_RETURN": [0.0] * len(expected_keys),
                "LOCAL_RIDGE": list(expected_period["LOCAL_RIDGE"]),
                "FULLY_POOLED_LOCAL_RIDGE": list(expected_period["FULLY_POOLED_LOCAL_RIDGE"]),
            }
            expected_control_identities = {
                name: _sha256(
                    {
                        "control": name,
                        "policy": "FIXED_LAB_S_LINEAR_CONTROLS",
                        "support_identity": support.identity,
                        "keys": list(expected_keys),
                        "values": values,
                        "manifest_sha256": parent.manifest_sha256,
                        "child_closure_sha256": parent.child_closure_sha256,
                    }
                )
                for name, values in expected_controls.items()
            }
            if (
                tuple(actual_period.get("keys", ())) != expected_keys
                or actual_period.get("controls") != expected_controls
                or actual_period.get("control_identities") != expected_control_identities
                or actual_period.get("support_identity") != support.identity
                or actual_period.get("identity") != expected_period.get("identity")
            ):
                raise ValueError("persisted control regression period drifted")
            period_identity = dict(actual_period)
            period_identity.pop("identity", None)
            if actual_period.get("identity") != _sha256(period_identity):
                raise ValueError("persisted control regression period identity drifted")
    tensors = raw_store if outcome_free else _build_tensors(rows, support)
    context: dict[str, Any] = {
        "current_identity": current_identity,
        "binding": binding,
        "parent": parent,
        "rows": rows,
        "foundation": foundation,
        "support": support,
        "tensors": tensors,
        "control_regression": control_payload,
    }
    if outcome_free:
        context["preprocessor"] = _restore_outcome_free_preprocessor(
            root, binding, support, tensors
        )
        return context
    partition, preprocessor, _ = _build_training_objects(context)
    partition_payload = _read_json(root / "input" / "training-partition.json")
    if (
        _file_digest(root / "input" / "training-partition.json") != binding["partition_file_sha256"]
        or partition_payload["seal"] != partition.seal
        or partition_payload["support_identity"] != support.identity
        or tuple(partition_payload["row_keys"]) != tuple(partition.row_keys)
        or tuple(partition_payload["tensor_identities"])
        != tuple(_masked_tensor_identity(item) for item in partition.tensors)
    ):
        raise ValueError("persisted training partition drift detected")
    preprocessor_payload = _read_json(root / "input" / "preprocessor.json")
    if _file_digest(root / "input" / "preprocessor.json") != binding["preprocessor_file_sha256"]:
        raise ValueError("persisted preprocessor drift detected")
    if training_preprocessor_identity(preprocessor) != binding["preprocessor_identity"]:
        raise ValueError("persisted preprocessor identity drift detected")
    if (
        tuple(preprocessor_payload["feature_names"]) != preprocessor.feature_names
        or tuple(preprocessor_payload["means"]) != tuple(preprocessor.means.tolist())
        or tuple(preprocessor_payload["scales"]) != tuple(preprocessor.scales.tolist())
        or preprocessor_payload["training_cutoff"] != preprocessor.training_cutoff.isoformat()
        or preprocessor_payload["contract_identity"] != preprocessor.contract_identity
        or preprocessor_payload["mode"] != preprocessor.mode
        or preprocessor_payload["fit_partition"] != preprocessor.fit_partition
        or tuple(preprocessor_payload["binary_features"]) != preprocessor.binary_features
        or preprocessor_payload["training_partition_identity"]
        != preprocessor.training_partition_identity
        or preprocessor_payload["reduction_algorithm"] != preprocessor.reduction_algorithm
    ):
        raise ValueError("persisted preprocessor contents drift detected")
    context["preprocessor"] = preprocessor
    return context


def _next_attempt(journal: CreateOnlyAttemptJournal, slot_id: str) -> int:
    records = _slot_journal_records(journal.root, slot_id)
    if not records:
        return 0
    attempts = {record["attempt"]["attempt"]: str(record["status"]) for record in records}
    if attempts.get(0) != "FAILED":
        raise ValueError(f"attempt slot already has a non-failed attempt: {slot_id}")
    if 1 in attempts:
        raise ValueError(f"attempt slot already used its retry: {slot_id}")
    return 1


def _select_development_attempt(
    root: Path,
    journal: CreateOnlyAttemptJournal,
    identity: G0ExecutionIdentity,
    *,
    family: str,
    seed: int,
    stage: str,
) -> AttemptIdentity:
    slot_id = f"{family}:{seed}:{stage}"
    attempt_number = _next_attempt(journal, slot_id)
    return AttemptIdentity(
        release_identity=identity.release_identity,
        g0_identity=identity.identity,
        output_root=str(root.resolve()),
        slot_id=slot_id,
        family_id=family,
        seed=seed,
        stage=stage,
        attempt=attempt_number,
        mode="PRIMARY",
    )


def _read_wrapper_handshake(fd: int) -> str:
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = os.read(fd, 256)
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > 256:
            raise RuntimeError("development wrapper handshake exceeds canonical bound")
    return payload.decode("ascii").rstrip("\n")


def _run_receipted_development_slot(
    root: Path,
    *,
    slot: tuple[str, int, str],
    epoch_id: str,
    cache_receipt_identity: str,
    handshake_fd: int,
) -> tuple[str, ...]:
    with preparation_entry(root):
        return _run_receipted_development_slot_impl(
            root,
            slot=slot,
            epoch_id=epoch_id,
            cache_receipt_identity=cache_receipt_identity,
            handshake_fd=handshake_fd,
        )


def _run_receipted_development_slot_impl(
    root: Path,
    *,
    slot: tuple[str, int, str],
    epoch_id: str,
    cache_receipt_identity: str,
    handshake_fd: int,
) -> tuple[str, ...]:
    from .supervisor import (
        read_process_ownership,
        validate_epoch_cache_metadata,
        write_process_ownership,
    )

    family, seed, stage = slot
    identity = _require_identity(root)
    journal = CreateOnlyAttemptJournal(root)
    attempt = _select_development_attempt(
        root, journal, identity, family=family, seed=seed, stage=stage
    )
    validate_epoch_cache_metadata(root, f"{epoch_id}-pre", cache_receipt_identity)
    wrapper_identity = wrapper_receipt_identity(epoch_id, cache_receipt_identity, attempt.identity)
    ownership = read_process_ownership(
        pid=os.getpid(),
        exact_head=identity.code_head,
        g0_identity=identity.identity,
        output_root=root,
        slot_id=attempt.slot_id,
        attempt=attempt.attempt,
        wrapper_receipt_identity=wrapper_identity,
        supervisor_epoch_id=epoch_id,
        cache_receipt_identity=cache_receipt_identity,
    )
    write_process_ownership(root, attempt, ownership)
    os.write(handshake_fd, f"{attempt.identity}:{wrapper_identity}\n".encode("ascii"))
    os.close(handshake_fd)
    epoch_token = _development_epoch_id.set(epoch_id)
    receipt_token = _development_cache_receipt.set(cache_receipt_identity)
    try:
        try:
            result = run_development_slots(root, slots=(slot,), all_development=False)
        except BaseException as primary:
            try:
                validate_epoch_cache_metadata(root, f"{epoch_id}-pre", cache_receipt_identity)
            except BaseException as drift:
                raise BaseExceptionGroup(
                    "scientific execution and post-cache validation both failed", [primary, drift]
                ) from None
            raise
        validate_epoch_cache_metadata(root, f"{epoch_id}-pre", cache_receipt_identity)
        return result
    finally:
        _development_cache_receipt.reset(receipt_token)
        _development_epoch_id.reset(epoch_token)


def _is_real_execution(root: Path) -> bool:
    return _real_manifest_path(preparation_root(root)).is_file()


_REPLAY_FLOAT_REL_TOL = 1e-11
_REPLAY_FLOAT_ABS_TOL = 1e-15


def _replay_values_equal(left: Any, right: Any) -> bool:
    """Compare replayed regression data without relaxing its structure."""
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        right_float = cast(float, right)
        if not math.isfinite(left) or not math.isfinite(right_float):
            return False
        return math.isclose(
            left,
            right_float,
            rel_tol=_REPLAY_FLOAT_REL_TOL,
            abs_tol=_REPLAY_FLOAT_ABS_TOL,
        )
    if isinstance(left, Mapping):
        right_mapping = cast(Mapping[Any, Any], right)
        if len(left) != len(right_mapping):
            return False
        remaining = list(right_mapping.items())
        for left_key, left_value in left.items():
            for index, (right_key, right_value) in enumerate(remaining):
                if type(left_key) is type(right_key) and left_key == right_key:
                    remaining.pop(index)
                    if not _replay_values_equal(left_value, right_value):
                        return False
                    break
            else:
                return False
        return not remaining
    if isinstance(left, (list, tuple)):
        right_sequence = cast(Sequence[Any], right)
        return len(left) == len(right_sequence) and all(
            _replay_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right_sequence, strict=True)
        )
    return left == right


def _reconstruct_control_regression(rows: Any, *, capsule: Any) -> Any:
    from .foundation import reconstruct_linear_controls

    return reconstruct_linear_controls(
        _foundation_rows(rows), capsule=capsule, require_expected=True
    )


def _foundation_rows(rows: Any) -> Any:
    """Exclude terminal-boundary warmups from OOF/foundation targets."""
    import polars as pl

    return rows.filter(pl.col("target_available_at") < pl.lit(TERMINAL_START))


def _terminal_history_rows(parent: Any, first_terminal_time: Any) -> Any:
    import polars as pl

    history = load_parent_rows(parent, include_invalid=True, include_return=False).filter(
        pl.col("decision_time") < first_terminal_time
    )
    if "target_return" in history.columns:
        raise ValueError("outcome-free terminal history unexpectedly contains target_return")
    if history.is_empty():
        raise ValueError("terminal causal history is empty before first decision")
    return history


def _authenticated_dev_control_training_rows(parent: Any, terminal_start: Any) -> Any:
    import polars as pl

    from .foundation import _DEV_CONTROL_COLUMNS, authenticate_dev_control_training
    from .tensor import P0_FEATURE_NAMES

    rows = load_parent_rows(parent, include_return=True)
    if "target_return" not in rows.columns:
        raise ValueError("DEV-control training projection lacks target_return")
    allowed_blocks = {"TRAINING_ONLY", "DEV_1", "DEV_2", "DEV_3"}
    if set(rows.get_column("block").unique().to_list()) - allowed_blocks:
        raise ValueError("DEV-control training includes an unauthorized block")
    if rows.filter(pl.col("target_return").is_null()).height:
        raise ValueError("DEV-control training contains null target_return")
    if rows.filter(~pl.col("target_valid")).height:
        raise ValueError("DEV-control training contains invalid targets")
    projected = (
        rows.filter(pl.col("target_available_at") < terminal_start)
        .select([*_DEV_CONTROL_COLUMNS, *P0_FEATURE_NAMES])
        .sort(["decision_time", "instrument_id"])
    )
    return authenticate_dev_control_training(parent, projected, terminal_start).rows


def _ordered_terminal_targets(
    target_by_key: dict[str, float], capsule_target_keys: tuple[str, ...]
) -> dict[str, float]:
    if len(set(capsule_target_keys)) != len(capsule_target_keys) or not set(
        capsule_target_keys
    ).issubset(target_by_key):
        raise ValueError("authenticated terminal outcomes do not exactly match sealed valid keys")
    return {key: target_by_key[key] for key in capsule_target_keys}


def prepare_authenticated_execution(
    output_root: Path, *, manifest_path: Path, config: FrozenRuntimeConfig | None = None
) -> G0ExecutionIdentity:
    import polars as pl

    from .foundation import (
        EXPECTED_COUNTS,
        _preparation_parent,
        authenticate_oof_residual_foundation,
        authenticate_parent,
        build_oof_residual_foundation,
        load_parent_rows,
        materialise_development_support,
        project_authenticated_parent_support_rows,
        reconstruct_authenticated_linear_forecasts,
    )

    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("G0 preparation requires a new empty output root")
    manifest = Path(manifest_path).resolve()
    if not manifest.is_file():
        raise FileNotFoundError(f"authenticated LAB-0 manifest is missing: {manifest}")
    if _file_digest(manifest) != MANIFEST_SHA256:
        raise ValueError("authenticated LAB-0 manifest is not the frozen manifest")
    parent = authenticate_parent(manifest_path=manifest, expected_manifest_sha256=MANIFEST_SHA256)
    parent = _preparation_parent(parent)
    rows = load_parent_rows(parent)
    foundation_rows = _foundation_rows(rows)
    control_regression = _reconstruct_control_regression(rows, capsule=parent)
    identity = capture_g0_identity(root, config)
    _write_json_once(_identity_path(root), identity.to_dict())
    residual_rows = build_oof_residual_foundation(foundation_rows, capsule=parent)
    foundation_path = root / "input" / "residual-foundation.parquet"
    foundation = authenticate_oof_residual_foundation(
        residual_rows, capsule=parent, output_path=foundation_path
    )
    support_path = root / "input" / "development-support.parquet"
    materialise_development_support(foundation_rows, capsule=parent, output_path=support_path)
    decision_times = tuple(
        sorted({row["decision_time"] for row in foundation.rows.iter_rows(named=True)})
    )
    from .tensor import (
        authenticate_support_corpus,
        bind_support_tensor_identities,
    )
    from .tensor_store import build_raw_tensor_store

    projected_support_rows = project_authenticated_parent_support_rows(parent)
    support_corpus = authenticate_support_corpus(projected_support_rows, parent)
    support_input = _build_execution_support_input(support_corpus, parent, decision_times)
    raw_tensor_store = build_raw_tensor_store(
        root / "input" / "raw-tensors",
        _build_tensors(rows, support_input.support),
        support_input.support.keys,
        support_input_identity=support_input.identity,
        source_identity=support_input.support.source_identity,
    )
    raw_tensor_identities = raw_tensor_store.tensor_identities
    support = bind_support_tensor_identities(support_input, raw_tensor_identities)
    execution_support_path = root / "input" / "execution-support.parquet"
    if execution_support_path.exists() or execution_support_path.with_suffix(".json").exists():
        raise FileExistsError(f"execution support is create-only: {execution_support_path}")
    execution_support_path.parent.mkdir(parents=True, exist_ok=True)
    execution_support_rows = projected_support_rows.filter(
        pl.col("decision_time").is_in(decision_times)
    )
    execution_support_rows.write_parquet(execution_support_path)
    expected_support_rows = int(EXPECTED_COUNTS["lab_s_support"]["development"])
    if execution_support_rows.height != expected_support_rows:
        raise ValueError(
            f"authenticated development support cardinality differs: "
            f"{execution_support_rows.height} != {expected_support_rows}"
        )
    support_validation_rows = execution_support_rows
    control_forecasts = reconstruct_authenticated_linear_forecasts(
        rows,
        validation_by_period={
            period: support_validation_rows.filter(pl.col("block") == period)
            for period in DEVELOPMENT_BLOCKS
        },
        periods=DEVELOPMENT_BLOCKS,
        capsule=parent,
        support_identity=support.identity,
        support=support,
    )
    control_periods: dict[str, Any] = {}
    for period in DEVELOPMENT_BLOCKS:
        period_controls = control_forecasts[period]
        keys = tuple(period_controls["keys"])
        values = {
            "ZERO_RETURN": tuple(0.0 for _ in keys),
            "LOCAL_RIDGE": tuple(period_controls["LOCAL_RIDGE"]),
            "FULLY_POOLED_LOCAL_RIDGE": tuple(period_controls["FULLY_POOLED_LOCAL_RIDGE"]),
        }
        control_values = {name: list(series) for name, series in values.items()}
        control_identities = {}
        for name, series in control_values.items():
            control_identity_payload = {
                "control": name,
                "policy": "FIXED_LAB_S_LINEAR_CONTROLS",
                "support_identity": support.identity,
                "keys": list(keys),
                "values": series,
                "manifest_sha256": parent.manifest_sha256,
                "child_closure_sha256": parent.child_closure_sha256,
            }
            control_identities[name] = _sha256(control_identity_payload)
        period_payload: dict[str, Any] = {
            "period": period,
            "support_identity": support.identity,
            "keys": list(keys),
            "controls": control_values,
            "control_identities": control_identities,
            "policy": "FIXED_LAB_S_LINEAR_CONTROLS",
            "manifest_sha256": parent.manifest_sha256,
            "child_closure_sha256": parent.child_closure_sha256,
            "LOCAL_RIDGE": list(period_controls["LOCAL_RIDGE"]),
            "FULLY_POOLED_LOCAL_RIDGE": list(period_controls["FULLY_POOLED_LOCAL_RIDGE"]),
        }
        period_payload["identity"] = _sha256(period_payload)
        control_periods[period] = period_payload
    control_capsule: dict[str, Any] = {
        "artifact_type": "R4.C_AUTHENTICATED_CONTROL_REGRESSION",
        "g0_identity": identity.identity,
        "parent_identity": parent.child_closure_sha256,
        "manifest_sha256": parent.manifest_sha256,
        "support_identity": support.identity,
        "regression": control_regression,
        "periods": control_periods,
    }
    control_capsule["artifact_identity"] = _sha256(control_capsule)
    control_path = root / "input" / "control-regression.json"
    _write_json_once(control_path, control_capsule)
    context = {
        "parent": parent,
        "rows": rows,
        "foundation": foundation,
        "support": support,
        "support_corpus": support_corpus,
        "tensors": raw_tensor_store,
        "control_regression": control_capsule,
    }
    from .tensor_store import _preparation_partition_construction

    with _preparation_partition_construction(
        raw_tensor_store, support_corpus, support_input
    ) as capability:
        context["_preparation_capability"] = capability
        partition, _ = _build_training_partition(context)
        prepared_development, preprocessor = _persist_prepared_development_stages(
            root, context, raw_tensor_store, partition, raw_tensor_identities
        )
    del context["_preparation_capability"]
    support_payload = _support_payload(support, g0_identity=identity.identity)
    support_payload["content_identity"] = _sha256(support_payload)
    _write_json_once(execution_support_path.with_suffix(".json"), support_payload)
    _write_json_once(
        root / "input" / "training-partition.json",
        {
            "seal": partition.seal,
            "support_identity": support.identity,
            "source_identity": support.source_identity,
            "row_keys": list(partition.row_keys),
            "tensor_identities": [raw_tensor_identities[key] for key in partition.row_keys],
            "tensor_identity": _sha256(
                {"tensor_identities": [raw_tensor_identities[key] for key in partition.row_keys]}
            ),
        },
    )
    _write_json_once(
        root / "input" / "preprocessor.json",
        {
            "mode": preprocessor.mode,
            "feature_names": list(preprocessor.feature_names),
            "means": preprocessor.means.tolist(),
            "scales": preprocessor.scales.tolist(),
            "training_cutoff": preprocessor.training_cutoff.isoformat(),
            "contract_identity": preprocessor.contract_identity,
            "fit_partition": preprocessor.fit_partition,
            "binary_features": list(preprocessor.binary_features),
            "training_partition_identity": preprocessor.training_partition_identity,
            "reduction_algorithm": preprocessor.reduction_algorithm,
        },
    )
    _write_json_once(
        _real_manifest_path(root),
        {
            "artifact_type": "R4.C_AUTHENTICATED_EXECUTION_INPUT",
            "g0_identity": identity.identity,
            "manifest_path": str(manifest),
            "manifest_sha256": MANIFEST_SHA256,
            "manifest_identity": MANIFEST_IDENTITY,
            "parent_identity": PARENT_IDENTITY,
            "foundation_path": str(foundation_path),
            "foundation_identity": foundation.content_identity,
            "foundation_file_sha256": _file_digest(foundation_path),
            "support_path": str(execution_support_path),
            "support_identity": support.identity,
            "support_file_sha256": _file_digest(execution_support_path),
            "development_support_path": str(support_path),
            "control_regression_path": str(control_path),
            "control_regression_identity": control_capsule["artifact_identity"],
            "control_regression_file_sha256": _file_digest(control_path),
            "development_support_file_sha256": _file_digest(support_path),
            "preprocessor_identity": training_preprocessor_identity(preprocessor),
            "raw_tensor_store_path": str(raw_tensor_store.root),
            "raw_tensor_store_identity": raw_tensor_store.manifest["store_identity"],
            "prepared_development_stages": prepared_development,
            "partition_file_sha256": _file_digest(root / "input" / "training-partition.json"),
            "preprocessor_file_sha256": _file_digest(root / "input" / "preprocessor.json"),
            "formats": {
                "foundation": "parquet+json",
                "support": "parquet+json",
                "capsules": "canonical-json",
            },
        },
    )
    return identity


def _build_real_family_model(family_id: str) -> Any:
    from .graph import build_fixed_economic_graph, shuffle_economic_graph
    from .runtime import build_family_model, family_spec

    spec = family_spec(family_id)
    if spec.graph_mode == "fixed":
        return build_family_model(family_id, fixed_graph=build_fixed_economic_graph())
    if spec.graph_mode == "shuffled":
        return build_family_model(
            family_id, shuffled_graph=shuffle_economic_graph(build_fixed_economic_graph())
        )
    return build_family_model(family_id)


def _reload_real_model(
    model_path: Path,
    slot_id: str,
    model_metadata: Mapping[str, Any],
    fit: Mapping[str, Any],
    *,
    device: str,
) -> Any:
    """Verify the same serialised state contract for every fitted family."""
    import torch

    family_id, seed_text, _stage = slot_id.split(":", 2)
    torch.manual_seed(int(seed_text))
    model = _build_real_family_model(family_id)
    state = torch.load(model_path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict) or not state:
        raise ValueError(f"development model state is not reloadable: {model_path}")
    model.load_state_dict(state)
    model.to(device)
    architecture = model_metadata["architecture"]
    if (
        model.architecture() != architecture
        or _sha256(architecture) != model_metadata["architecture_identity"]
    ):
        raise ValueError(f"development model architecture drifted: {model_path}")
    graph = model.adjacency_matrix()
    graph_identity = None if graph is None else _sha256(graph.detach().cpu().tolist())
    if graph_identity != model_metadata["graph_identity"]:
        raise ValueError(f"development model graph provenance drifted: {model_path}")
    if int(fit["parameter_count"]) != model_parameter_count(model):
        raise ValueError(f"development fit parameter count drifted: {slot_id}")
    return model


def _persist_real_model(
    root: Path,
    slot_id: str,
    model: Any,
    fit: Any,
    prediction: Any,
    identity: G0ExecutionIdentity,
    *,
    attempt_identity: AttemptIdentity,
    prediction_batch: Any,
    config: FrozenRuntimeConfig | None = None,
    foundation_content_identity: str,
    training_batch_identity: str,
    preprocessor_identity: str,
    prediction_input_identity: str,
    target_keys: tuple[str, ...],
    local_ridge_forecasts: tuple[float, ...],
    fully_pooled_local_ridge_forecasts: tuple[float, ...],
    linear_control_identity: str,
    linear_controls: dict[str, list[float]],
    linear_control_identities: dict[str, str],
    linear_control_support_identity: str,
) -> dict[str, Any]:
    import io

    import torch

    from .runtime import RuntimeTelemetry, predict_residual

    persist_started = time.monotonic()
    runtime_config = config or FrozenRuntimeConfig()
    family_id, seed_text, stage = slot_id.split(":", 2)
    if (
        attempt_identity.release_identity != identity.release_identity
        or attempt_identity.g0_identity != identity.identity
        or attempt_identity.output_root != str(root.resolve())
        or attempt_identity.slot_id != slot_id
    ):
        raise ValueError("persistence attempt differs from execution identity")
    records = CreateOnlyAttemptJournal(root).records(attempt_identity.identity)
    if set(records) != {"STARTED"} or records["STARTED"]["attempt"] != attempt_identity.to_dict():
        raise ValueError("persistence requires the authenticated open attempt")
    attempt = attempt_identity.attempt
    classification = f"R4.C_{attempt_identity.mode}"
    staging = root / "staging" / "attempts" / attempt_identity.identity
    staging.mkdir(parents=True)
    state = io.BytesIO()
    torch.save(model.state_dict(), state)
    blob = state.getvalue()
    model_path = staging / "model.pt"
    _write_bytes_once(model_path, blob)
    graph = model.adjacency_matrix()
    graph_identity = None if graph is None else _sha256(graph.detach().cpu().tolist())
    model_metadata = {
        "artifact_type": f"{classification}_MODEL_METADATA",
        "slot_id": slot_id,
        "family_id": family_id,
        "seed": int(seed_text),
        "stage": stage,
        "attempt": attempt,
        "g0_identity": identity.identity,
        "config_identity": runtime_config.identity,
        "foundation_content_identity": foundation_content_identity,
        "training_batch_identity": training_batch_identity,
        "preprocessor_identity": preprocessor_identity,
        "prediction_input_identity": prediction_input_identity,
        "architecture": model.architecture(),
        "architecture_identity": _sha256(model.architecture()),
        "graph_identity": graph_identity,
        "state_sha256": hashlib.sha256(blob).hexdigest(),
        "model_file_sha256": _file_digest(model_path),
    }
    model_metadata_path = staging / "model.json"
    _write_json_once(model_metadata_path, model_metadata)
    prediction_path = staging / "prediction.json"
    residual_prediction = prediction.detach().to(device="cpu", dtype=torch.float32).reshape(-1)
    residual_values = residual_prediction.tolist()
    total_values = (
        residual_prediction + torch.as_tensor(local_ridge_forecasts, dtype=torch.float32)
    ).tolist()
    prediction_payload = {
        "artifact_type": f"{classification}_PREDICTION",
        "slot_id": slot_id,
        "attempt": attempt,
        "g0_identity": identity.identity,
        "prediction": residual_values,
        "residual_prediction": residual_values,
        "target_keys": list(target_keys),
        "local_ridge_forecast": list(local_ridge_forecasts),
        "fully_pooled_local_ridge_forecast": list(fully_pooled_local_ridge_forecasts),
        "linear_control_identity": linear_control_identity,
        "linear_controls": linear_controls,
        "linear_control_identities": linear_control_identities,
        "linear_control_support_identity": linear_control_support_identity,
        "total_forecast": [float(value) for value in total_values],
        "model_file_sha256": _file_digest(model_path),
        "model_metadata_file_sha256": _file_digest(model_metadata_path),
        "input_identity": prediction_input_identity,
        "preprocessor_identity": preprocessor_identity,
        "outcomes_loaded": False,
        "prediction_closed": True,
    }
    prediction_payload["prediction_identity"] = _sha256(prediction_payload)
    _write_json_once(prediction_path, prediction_payload)
    result_payload = {
        "artifact_type": f"{classification}_RESULT",
        "slot_id": slot_id,
        "attempt": attempt,
        "g0_identity": identity.identity,
        "fit": fit,
        "model_metadata_file_sha256": _file_digest(model_metadata_path),
        "prediction_file_sha256": _file_digest(prediction_path),
        "prediction_identity": prediction_payload["prediction_identity"],
        "outcomes_loaded": False,
        "prediction_closed": True,
    }
    _write_json_once(staging / "result.json", result_payload)
    reloaded = _reload_real_model(
        model_path, slot_id, model_metadata, fit, device=runtime_config.device
    )
    reload_telemetry = RuntimeTelemetry()
    torch.testing.assert_close(
        predict_residual(reloaded, prediction_batch, _telemetry=reload_telemetry)
        .detach()
        .cpu()
        .reshape(-1),
        residual_prediction,
        rtol=0,
        atol=0,
    )
    seal_attempt_staging(
        staging,
        attempt_identity,
        required_paths=("model.pt", "model.json", "prediction.json", "result.json"),
    )
    publish_attempt_bundle(
        staging,
        root / "attempts" / slot_id / f"attempt-{attempt}",
        attempt_identity,
    )
    destination = root / "attempts" / slot_id / f"attempt-{attempt}"
    return {
        "elapsed_seconds": time.monotonic() - persist_started,
        "reload_prediction": asdict(reload_telemetry),
        "published_bytes": sum(path.stat().st_size for path in destination.iterdir()),
    }


def run_authenticated_development_slots(
    root: Path,
    selected: tuple[tuple[str, int, str], ...],
    config: FrozenRuntimeConfig | None,
    identity: G0ExecutionIdentity,
) -> tuple[str, ...]:
    from .runtime import fit_one_model, predict_residual
    from .stage_cache import load_stage_cache_batches

    epoch_id = _development_epoch_id.get()
    cache_receipt = _development_cache_receipt.get()
    if _is_real_execution(root) and (epoch_id is None or cache_receipt is None):
        raise RuntimeError(
            "authenticated development execution requires a verified supervisor cache receipt"
        )

    runtime_config = config or FrozenRuntimeConfig()
    requested_stages = tuple(dict.fromkeys(slot[2] for slot in selected))
    capsule = _load_development_execution_capsule(
        root, identity, runtime_config, selected_stages=requested_stages
    )
    attempt_log = CreateOnlyAttemptJournal(root)
    stage_cache: dict[str, dict[str, Any]] = {}
    for stage in requested_stages:
        stage_payload = capsule["stages"][stage]
        cache_input = _capsule_cache_input(stage_payload, identity)
        training, prediction, verified = load_stage_cache_batches(
            Path(stage_payload["cache_root"]), expected=cache_input, full_verify=False
        )
        if verified.cache_identity != stage_payload["cache_identity"]:
            raise ValueError("development cache differs from execution context capsule")
        if (
            training.content_identity != stage_payload["training_batch_identity"]
            or prediction.content_identity != stage_payload["prediction_batch_identity"]
        ):
            raise ValueError("development batch identity differs from execution context capsule")
        stage_cache[stage] = {
            "training_batch": training,
            "prediction_batch": prediction,
            "payload": stage_payload,
        }
    for family, seed, stage in selected:
        _development_attempt_started.set(False)
        slot_id = f"{family}:{seed}:{stage}"
        train_blocks = ("DEV_1",) if stage == "DEV_2" else ("DEV_1", "DEV_2")
        stage_objects = stage_cache[stage]
        stage_payload = stage_objects["payload"]
        stage_preprocessor_identity = stage_payload["preprocessor_identity"]
        batch = stage_objects["training_batch"]
        prediction_batch = stage_objects["prediction_batch"]
        input_identity = stage_payload["prediction_input_identity"]
        started = _select_development_attempt(
            root,
            attempt_log,
            identity,
            family=family,
            seed=seed,
            stage=stage,
        )
        attempt_log.append(
            started,
            "STARTED",
            {
                "config_identity": runtime_config.identity,
                "foundation_content_identity": capsule["foundation_content_identity"],
                "supervisor_epoch_id": _development_epoch_id.get(),
                "cache_receipt_identity": _development_cache_receipt.get(),
            },
        )
        _development_attempt_started.set(True)
        try:
            configure_deterministic_cuda(seed)
            model = _build_real_family_model(family)
            fit = fit_one_model(model, batch, training_blocks=train_blocks)
            prediction = predict_residual(model, prediction_batch)
            control_period = stage_payload["control_period"]
            expected_keys = tuple(stage_payload["target_keys"])
            if tuple(control_period["keys"]) != expected_keys:
                raise ValueError("authenticated linear-control keys differ from stage support")
            local_ridge_forecasts = tuple(control_period["LOCAL_RIDGE"])
            pooled_ridge_forecasts = tuple(control_period["FULLY_POOLED_LOCAL_RIDGE"])
            persistence = _persist_real_model(
                root,
                slot_id,
                model,
                fit,
                prediction,
                identity,
                attempt_identity=started,
                prediction_batch=prediction_batch,
                config=runtime_config,
                foundation_content_identity=capsule["foundation_content_identity"],
                training_batch_identity=batch.content_identity,
                preprocessor_identity=stage_preprocessor_identity,
                prediction_input_identity=input_identity,
                target_keys=expected_keys,
                local_ridge_forecasts=local_ridge_forecasts,
                fully_pooled_local_ridge_forecasts=pooled_ridge_forecasts,
                linear_control_identity=control_period["identity"],
                linear_controls=control_period["controls"],
                linear_control_identities=control_period["control_identities"],
                linear_control_support_identity=control_period["support_identity"],
            )
            bundle = root / "attempts" / slot_id / f"attempt-{started.attempt}"
            sealed = verify_attempt_bundle(bundle, started)
            attempt_log.append(
                started,
                "SUCCEEDED",
                {
                    "seal_identity": cast(Mapping[str, object], sealed["seal"])["seal_identity"],
                    "persistence": persistence,
                },
            )
        except Exception as exc:
            try:
                attempt_log.append(started, "FAILED", {"error": str(exc)})
            except Exception as persistence_error:
                exc.add_note(f"terminal failure persistence failed: {persistence_error}")
            raise
    return tuple(f"{family}:{seed}:{stage}" for family, seed, stage in selected)


def _load_authenticated_development_outcomes(
    root: Path,
    identity: G0ExecutionIdentity,
    capsule: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    import polars as pl

    expected_g0 = preparation_g0(root, identity.identity)
    root = preparation_root(root)
    binding = _read_json(_real_manifest_path(root))
    if binding["g0_identity"] != expected_g0:
        raise ValueError("authenticated execution binding G0 identity drifted")
    if binding["foundation_identity"] != capsule["foundation_content_identity"]:
        raise ValueError("execution capsule foundation identity drifted")
    encoded_foundation_path = Path(str(binding["foundation_path"]))
    if encoded_foundation_path.is_symlink():
        raise ValueError("authenticated foundation path must not be a symlink")
    foundation_path = encoded_foundation_path.resolve()
    if foundation_path.parent != (root / "input").resolve():
        raise ValueError("authenticated foundation path escapes canonical input layout")
    if _file_digest(foundation_path) != binding["foundation_file_sha256"]:
        raise ValueError("persisted residual foundation drift detected")
    rows = pl.read_parquet(
        foundation_path,
        columns=["block", "instrument_id", "decision_time", "target_return"],
    ).filter(pl.col("block").is_in(["DEV_2", "DEV_3"]))
    return {
        f"{row['instrument_id']}|{row['decision_time'].isoformat()}": {
            "period": row["block"],
            "instrument_id": row["instrument_id"],
            "decision_time": row["decision_time"].isoformat(),
            "target_return": float(row["target_return"]),
        }
        for row in rows.iter_rows(named=True)
    }


def _close_authenticated_register_impl(
    root: Path, identity: G0ExecutionIdentity, *, config: FrozenRuntimeConfig | None = None
) -> str:
    root = Path(root).resolve()
    runtime_config = config or FrozenRuntimeConfig()
    capsule = _load_development_execution_capsule(root, identity, runtime_config)
    expected_slots = tuple(
        sorted(f"{family}:{seed}:{stage}" for family, seed, stage in _EXPECTED_DEVELOPMENT_SLOTS)
    )
    stage_preprocessor_identities = {
        stage: capsule["stages"][stage]["preprocessor_identity"] for stage in _DEVELOPMENT_STAGES
    }
    attempts_path = root / "register" / "attempts"
    succeeded = _validate_development_attempt_ledger(
        attempts_path,
        expected_slots=expected_slots,
        runtime_config=runtime_config,
        foundation_content_identity=capsule["foundation_content_identity"],
        output_identity=identity.output_root_identity,
        canonical_output_root=root,
    )
    hashes: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    prediction_payloads: list[dict[str, Any]] = []
    fit_resources: dict[str, Any] = {}
    for slot_id in expected_slots:
        successful_identity = succeeded[slot_id]
        successful_attempt = successful_identity.attempt
        bundle = root / "attempts" / slot_id / f"attempt-{successful_attempt}"
        verify_attempt_bundle(bundle, successful_identity)
        result_path = bundle / "result.json"
        model_path = bundle / "model.pt"
        model_metadata_path = bundle / "model.json"
        prediction_path = bundle / "prediction.json"
        if not all(
            (
                result_path.is_file(),
                model_path.is_file(),
                model_metadata_path.is_file(),
                prediction_path.is_file(),
            )
        ):
            raise ValueError(f"development slot artifacts are missing: {slot_id}")
        result_payload = _read_json(result_path)
        model_metadata = _read_json(model_metadata_path)
        prediction_payload = _read_json(prediction_path)
        _validate_prediction_payload(
            prediction_payload, prediction_path, identity=identity, slot_id=slot_id
        )
        fit_evidence = _validate_fit_evidence(
            result_payload["fit"],
            slot_id=slot_id,
            expected_training_batch_identity=model_metadata["training_batch_identity"],
        )
        fit_resources[slot_id] = fit_evidence
        hashes[slot_id] = {
            "result": _file_digest(result_path),
            "model": _file_digest(model_path),
            "model_metadata": _file_digest(model_metadata_path),
            "prediction": _file_digest(prediction_path),
        }
        _validate_development_artifact_metadata(
            slot_id=slot_id,
            attempt=successful_attempt,
            identity=identity,
            runtime_config=runtime_config,
            foundation_content_identity=capsule["foundation_content_identity"],
            expected_preprocessor_identity=stage_preprocessor_identities[slot_id.split(":", 2)[2]],
            result_payload=result_payload,
            metadata_payload=model_metadata,
            prediction_payload=prediction_payload,
            model_digest=hashes[slot_id]["model"],
            metadata_digest=hashes[slot_id]["model_metadata"],
            prediction_digest=hashes[slot_id]["prediction"],
            prediction_path=prediction_path,
        )
        if (
            result_payload.get("slot_id") != slot_id
            or result_payload.get("attempt") != successful_attempt
            or result_payload.get("g0_identity") != identity.identity
            or result_payload.get("outcomes_loaded") is not False
            or result_payload.get("prediction_closed") is not True
            or result_payload.get("prediction_identity")
            != prediction_payload.get("prediction_identity")
            or result_payload.get("prediction_file_sha256") != hashes[slot_id]["prediction"]
            or result_payload.get("model_metadata_file_sha256") != hashes[slot_id]["model_metadata"]
            or model_metadata.get("config_identity") != runtime_config.identity
            or model_metadata.get("foundation_content_identity")
            != capsule["foundation_content_identity"]
            or model_metadata.get("model_file_sha256") != hashes[slot_id]["model"]
            or model_metadata.get("state_sha256") != hashes[slot_id]["model"]
            or prediction_payload.get("model_file_sha256") != hashes[slot_id]["model"]
            or prediction_payload.get("model_metadata_file_sha256")
            != hashes[slot_id]["model_metadata"]
            or prediction_payload.get("preprocessor_identity")
            != model_metadata.get("preprocessor_identity")
            or prediction_payload.get("input_identity")
            != model_metadata.get("prediction_input_identity")
        ):
            raise ValueError("development slot artifact identity or outcome state is invalid")
        stage = slot_id.split(":", 2)[2]
        expected_period = capsule["stages"][stage]["control_period"]
        if not isinstance(expected_period, dict):
            raise ValueError("development linear-control period is missing")
        _validate_linear_control_payload(prediction_payload, expected_period)
        _reload_real_model(
            model_path, slot_id, model_metadata, fit_evidence, device=runtime_config.device
        )
        provenance[slot_id] = {
            "attempt": successful_attempt,
            "foundation_content_identity": model_metadata["foundation_content_identity"],
            "config_identity": model_metadata["config_identity"],
            "runtime_identity": runtime_config.identity,
            "input_identity": RESIDUAL_FOUNDATION_IDENTITY,
            "preprocessor_identity": model_metadata["preprocessor_identity"],
            "prediction_input_identity": model_metadata["prediction_input_identity"],
            "model_file_sha256": hashes[slot_id]["model"],
            "model_metadata_file_sha256": hashes[slot_id]["model_metadata"],
            "prediction_file_sha256": hashes[slot_id]["prediction"],
            "prediction_identity": prediction_payload["prediction_identity"],
            "result_file_sha256": hashes[slot_id]["result"],
        }
        prediction_payloads.append(prediction_payload)
    from .evaluation import evaluate_development_register

    outcomes = _load_authenticated_development_outcomes(root, identity, capsule)
    metric_payload = evaluate_development_register(
        prediction_payloads,
        outcomes,
        identities={
            "g0_identity": identity.identity,
            "foundation_content_identity": capsule["foundation_content_identity"],
            "config_identity": runtime_config.identity,
            "runtime_identity": runtime_config.identity,
            "input_identity": RESIDUAL_FOUNDATION_IDENTITY,
            "slot_provenance": provenance,
        },
    )
    metric_payload["attempts_file_sha256"] = _development_journal_identity(root)
    metric_payload["prediction_identities"] = {
        payload["slot_id"]: payload["prediction_identity"] for payload in prediction_payloads
    }
    metric_payload["fit_resources"] = fit_resources
    metric_payload["fit_evidence"] = _summarize_fit_evidence(
        fit_resources, expected_slots=expected_slots
    )
    metric_payload["metric_identity"] = _sha256(metric_payload)
    metric_path = root / "register" / "development-metrics.json"
    _write_json_once(metric_path, metric_payload)
    terminal_dispositions = {
        f"{family}:{seed}:{_TERMINAL_STAGE}": "UNOPENED"
        for family in FITTED_FAMILY_IDS
        for seed in PRIMARY_SEEDS
    }
    payload: dict[str, Any] = {
        "artifact_type": "R4.C_COMPLETE_AUTHENTICATED_DEVELOPMENT_REGISTER",
        "g0_identity": identity.identity,
        "expected_slots": list(expected_slots),
        "terminal_dispositions": terminal_dispositions,
        "artifact_hashes": hashes,
        "slot_provenance": provenance,
        "attempts_file_sha256": _development_journal_identity(root),
        "foundation_content_identity": capsule["foundation_content_identity"],
        "preprocessor_identity": capsule["preparation_preprocessor_identity"],
        "config_identity": runtime_config.identity,
        "runtime_identity": runtime_config.identity,
        "input_identity": RESIDUAL_FOUNDATION_IDENTITY,
        "outcome_blind": True,
        "outcomes_loaded": False,
        "metric_register_identity": metric_payload["metric_identity"],
        "metric_register_file_sha256": _file_digest(metric_path),
    }
    payload["register_identity"] = _sha256(payload)
    _validate_development_register_payload(root, identity, config=runtime_config, payload=payload)
    _write_json_once(root / "register" / "development-register.json", payload)
    _validate_development_register_payload(root, identity, config=runtime_config)
    return str(payload["register_identity"])


def _terminal_rows_from_input(root: Path, metadata: Any | None = None) -> Any:
    import polars as pl

    from .terminal_support import (
        ALLOWED_TERMINAL_COLUMNS,
        REQUIRED_TERMINAL_COLUMNS,
        _canonical_row_value,
    )

    path = root / "input" / "terminal-support-input.parquet"
    if not path.is_file():
        raise FileNotFoundError(
            "real terminal support requires input/terminal-support-input.parquet"
        )
    schema = pl.read_parquet(path, n_rows=0)
    columns = set(schema.columns)
    missing = REQUIRED_TERMINAL_COLUMNS - columns
    extra = columns - ALLOWED_TERMINAL_COLUMNS
    if missing or extra:
        raise ValueError(
            "terminal support input schema mismatch; "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    rows = pl.read_parquet(path, columns=sorted(ALLOWED_TERMINAL_COLUMNS & columns))
    if metadata is not None:
        expected_rows = tuple(dict(row) for row in metadata.to_rows())
        actual_rows = tuple(rows.iter_rows(named=True))
        expected_columns = set(expected_rows[0]) if expected_rows else set()
        if set(rows.columns) != expected_columns:
            raise ValueError("terminal support input is not an exact metadata projection")

        def canonical(row: Any) -> dict[str, Any]:
            return {
                column: _canonical_row_value(row[column]) for column in sorted(expected_columns)
            }

        if tuple(canonical(row) for row in actual_rows) != tuple(
            canonical(row) for row in expected_rows
        ):
            raise ValueError("terminal support input differs from authenticated metadata")
    return rows


_DEVELOPMENT_CLOSURE_INDEX_FIELDS = frozenset(
    {
        "artifact_type",
        "schema_version",
        "register",
        "g0_identity",
        "code_head",
        "lock_identity",
        "application_identity",
        "runtime_identity",
        "config_identity",
        "output_root_identity",
        "release_identity",
        "parent_identity",
        "harness_identity",
        "foundation_content_identity",
        "preprocessor_identity",
        "input_identity",
        "support_identity",
        "control_identity",
        "slots",
        "metric_register",
        "attempt_ledger",
        "closure_index_identity",
    }
)
_DEVELOPMENT_CLOSURE_REGISTER_FIELDS = frozenset({"path", "sha256", "identity"})
_DEVELOPMENT_CLOSURE_METRIC_FIELDS = frozenset({"path", "sha256", "identity", "coverage_identity"})
_DEVELOPMENT_CLOSURE_LEDGER_FIELDS = frozenset({"path", "sha256", "lifecycles"})
_DEVELOPMENT_CLOSURE_SLOT_FIELDS = frozenset(
    {
        "accepted_attempt",
        "disposition",
        "lifecycle",
        "prediction_identity",
        "artifact_paths",
        "artifact_digests",
        "foundation_content_identity",
        "preprocessor_identity",
        "prediction_input_identity",
        "control_identity",
        "control_support_identity",
        "control_identities",
    }
)
_DEVELOPMENT_CLOSURE_ARTIFACT_PATH_FIELDS = frozenset(
    {"result", "model", "model_metadata", "prediction"}
)
_DEVELOPMENT_CLOSURE_ARTIFACT_DIGEST_FIELDS = _DEVELOPMENT_CLOSURE_ARTIFACT_PATH_FIELDS


def _validate_development_register_metadata(
    root: Path, identity: G0ExecutionIdentity, *, config: FrozenRuntimeConfig | None = None
) -> dict[str, Any]:
    """Authenticate development-register closure without opening model or outcome data."""
    register_path = Path(root).resolve() / "register" / "development-register.json"
    payload = _read_json(register_path)
    expected_slots = sorted(
        f"{family}:{seed}:{stage}" for family, seed, stage in _EXPECTED_DEVELOPMENT_SLOTS
    )
    expected_dispositions = {
        f"{family}:{seed}:{_TERMINAL_STAGE}": "UNOPENED"
        for family in FITTED_FAMILY_IDS
        for seed in PRIMARY_SEEDS
    }
    required_keys = {
        "artifact_type",
        "g0_identity",
        "expected_slots",
        "terminal_dispositions",
        "artifact_hashes",
        "slot_provenance",
        "attempts_file_sha256",
        "foundation_content_identity",
        "preprocessor_identity",
        "config_identity",
        "runtime_identity",
        "input_identity",
        "outcome_blind",
        "outcomes_loaded",
        "metric_register_identity",
        "metric_register_file_sha256",
        "register_identity",
    }
    if set(payload) != required_keys:
        raise ValueError("development register schema is not canonical")
    check = dict(payload)
    register_identity = check.pop("register_identity")
    if register_identity != _sha256(check):
        raise ValueError("development register identity is invalid")
    if (
        payload["artifact_type"] != "R4.C_COMPLETE_AUTHENTICATED_DEVELOPMENT_REGISTER"
        or payload["g0_identity"] != identity.identity
        or payload["expected_slots"] != expected_slots
        or payload["terminal_dispositions"] != expected_dispositions
        or payload["outcome_blind"] is not True
        or payload["outcomes_loaded"] is not False
    ):
        raise ValueError("development register is not canonical or outcome-blind")
    runtime_config = config or FrozenRuntimeConfig()
    capsule = _load_development_execution_capsule(
        Path(root).resolve(), identity, runtime_config, metadata_only=True
    )
    stage_preprocessor_identities = {
        stage: capsule["stages"][stage]["preprocessor_identity"] for stage in _DEVELOPMENT_STAGES
    }
    if (
        payload["foundation_content_identity"] != capsule["foundation_content_identity"]
        or payload["preprocessor_identity"] != capsule["preparation_preprocessor_identity"]
        or payload["input_identity"] != RESIDUAL_FOUNDATION_IDENTITY
    ):
        raise ValueError("development register authenticated identities drifted")
    if (
        payload["config_identity"] != runtime_config.identity
        or payload["runtime_identity"] != runtime_config.identity
    ):
        raise ValueError("development register runtime identity drifted")
    for field in (
        "foundation_content_identity",
        "preprocessor_identity",
        "input_identity",
        "metric_register_identity",
        "metric_register_file_sha256",
        "attempts_file_sha256",
    ):
        value = payload[field]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError(f"development register {field} is not a SHA-256 identity")
    metric_path = register_path.parent / "development-metrics.json"
    if (
        not metric_path.is_file()
        or _file_digest(metric_path) != payload["metric_register_file_sha256"]
    ):
        raise ValueError("development metric register hash drifted")
    metric_payload = _read_json(metric_path)
    metric_identity = metric_payload["metric_identity"]
    metric_check = dict(metric_payload)
    metric_check.pop("metric_identity")
    from .evaluation import historical_r3h_cost_grid

    if (
        metric_identity != payload["metric_register_identity"]
        or metric_identity != _sha256(metric_check)
        or metric_payload["artifact_type"] != "R4.C_DEVELOPMENT_METRIC_GATE_REGISTER"
        or metric_payload["periods"] != ["DEV_2", "DEV_3"]
        or metric_payload["weighting"] != "union_within_instrument_then_equal_twenty"
        or metric_payload["total_forecast_definition"]
        != "local_ridge_forecast_plus_residual_correction"
        or metric_payload["r3h_cost_grid"] != historical_r3h_cost_grid()
        or metric_payload.get("support_row_count")
        != sum(capsule["stages"][stage]["target_keys"]["shape"][0] for stage in _DEVELOPMENT_STAGES)
    ):
        raise ValueError("development metric register identity or schema drifted")
    metric_families = metric_payload["families"]
    expected_metric_seeds = {str(seed) for seed in PRIMARY_SEEDS}
    if not isinstance(metric_families, dict) or set(metric_families) != set(FITTED_FAMILY_IDS):
        raise ValueError("development metric register family set is not canonical")
    for family_entry in metric_families.values():
        if (
            not isinstance(family_entry, dict)
            or set(family_entry.get("seeds", {})) != expected_metric_seeds
            or not isinstance(family_entry.get("primary"), dict)
            or family_entry["primary"].get("seed") != min(PRIMARY_SEEDS)
        ):
            raise ValueError("development metric register seed views are not canonical")
    metric_controls = metric_payload["controls"]
    if not isinstance(metric_controls, dict) or set(metric_controls) != {
        "ZERO_RETURN",
        "LOCAL_RIDGE",
        "FULLY_POOLED_LOCAL_RIDGE",
    }:
        raise ValueError("development metric register controls are not canonical")
    metric_coverage = metric_payload["coverage"]
    if not isinstance(metric_coverage, dict) or any(
        not isinstance(metric_coverage.get(period), dict)
        or set(metric_coverage[period]) != set(ALL_INSTRUMENTS)
        or any(int(metric_coverage[period][instrument]) <= 0 for instrument in ALL_INSTRUMENTS)
        for period in ("DEV_2", "DEV_3")
    ):
        raise ValueError("development metric register coverage is not canonical")
    artifact_hashes = payload["artifact_hashes"]
    provenance = payload["slot_provenance"]
    if (
        not isinstance(artifact_hashes, dict)
        or set(artifact_hashes) != set(expected_slots)
        or not isinstance(provenance, dict)
        or set(provenance) != set(expected_slots)
    ):
        raise ValueError("development register artifacts are incomplete")
    artifact_fields = {"result", "model", "model_metadata", "prediction"}
    provenance_fields = {
        "attempt",
        "foundation_content_identity",
        "config_identity",
        "runtime_identity",
        "input_identity",
        "preprocessor_identity",
        "prediction_input_identity",
        "model_file_sha256",
        "model_metadata_file_sha256",
        "prediction_file_sha256",
        "prediction_identity",
        "result_file_sha256",
    }
    attempts_path = register_path.parent / "attempts"
    if (
        not attempts_path.is_dir()
        or _development_journal_identity(Path(root)) != payload["attempts_file_sha256"]
    ):
        raise ValueError("development register attempt journal identity drifted")
    succeeded = _validate_development_attempt_ledger(
        attempts_path,
        expected_slots=tuple(expected_slots),
        runtime_config=runtime_config,
        foundation_content_identity=payload["foundation_content_identity"],
        output_identity=identity.output_root_identity,
        canonical_output_root=Path(root),
    )
    for slot_id in expected_slots:
        hashes = artifact_hashes[slot_id]
        slot_provenance = provenance[slot_id]
        if not isinstance(hashes, dict) or set(hashes) != artifact_fields:
            raise ValueError(f"development register artifact set is not canonical: {slot_id}")
        if not isinstance(slot_provenance, dict) or set(slot_provenance) != provenance_fields:
            raise ValueError(f"development register provenance is not canonical: {slot_id}")
        attempt = succeeded[slot_id].attempt
        bundle = root / "attempts" / slot_id / f"attempt-{attempt}"
        paths = {
            "result": bundle / "result.json",
            "model": bundle / "model.pt",
            "model_metadata": bundle / "model.json",
            "prediction": bundle / "prediction.json",
        }
        if not all(path.is_file() for path in paths.values()):
            raise ValueError(f"development slot artifacts are missing: {slot_id}")
        observed = {name: _file_digest(path) for name, path in paths.items()}
        if observed != hashes:
            raise ValueError(f"development register artifact hash drifted: {slot_id}")
        result_payload = _read_json(paths["result"])
        metadata_payload = _read_json(paths["model_metadata"])
        prediction_payload = _read_json(paths["prediction"])
        _family_id, _seed_text, stage = slot_id.split(":", 2)
        _validate_development_artifact_metadata(
            slot_id=slot_id,
            attempt=attempt,
            identity=identity,
            runtime_config=runtime_config,
            foundation_content_identity=payload["foundation_content_identity"],
            expected_preprocessor_identity=stage_preprocessor_identities[stage],
            result_payload=result_payload,
            metadata_payload=metadata_payload,
            prediction_payload=prediction_payload,
            model_digest=observed["model"],
            metadata_digest=observed["model_metadata"],
            prediction_digest=observed["prediction"],
            prediction_path=paths["prediction"],
        )
        expected_provenance = {
            "attempt": attempt,
            "foundation_content_identity": metadata_payload["foundation_content_identity"],
            "config_identity": metadata_payload["config_identity"],
            "runtime_identity": runtime_config.identity,
            "input_identity": RESIDUAL_FOUNDATION_IDENTITY,
            "preprocessor_identity": metadata_payload["preprocessor_identity"],
            "prediction_input_identity": metadata_payload["prediction_input_identity"],
            "model_file_sha256": observed["model"],
            "model_metadata_file_sha256": observed["model_metadata"],
            "prediction_file_sha256": observed["prediction"],
            "prediction_identity": prediction_payload["prediction_identity"],
            "result_file_sha256": observed["result"],
        }
        if slot_provenance != expected_provenance:
            raise ValueError(f"development register provenance drifted: {slot_id}")
    return payload


def _materialise_authenticated_terminal_support_impl(
    root: Path, config: FrozenRuntimeConfig | None, identity: G0ExecutionIdentity
) -> str:
    from .foundation import authenticate_terminal_metadata, terminal_history_bindings
    from .graph import build_fixed_economic_graph
    from .terminal_support import TerminalSupportConfig, build_terminal_support

    parent_context = _load_authenticated_parent_context(root, config)
    current_identity = parent_context["current_identity"]
    if current_identity.identity != identity.identity:
        raise ValueError("supplied G0 identity does not match the authenticated root")
    register_payload = _validate_development_register_metadata(root, identity, config=config)
    register_identity = register_payload["register_identity"]
    runtime_config = config or FrozenRuntimeConfig()
    graph = build_fixed_economic_graph()
    parent = parent_context["parent"]
    terminal_config = TerminalSupportConfig.from_authenticated_parent(parent, runtime_config, graph)
    metadata = authenticate_terminal_metadata(
        parent=parent, runtime_config=runtime_config, graph=graph
    )
    history_bindings = terminal_history_bindings(
        parent,
        min(
            _as_utc(row["decision_time"])
            for row in metadata.to_rows()
            if row["target_valid"] is True or row["target_valid"] == 1
        ),
    )
    rows = _terminal_rows_from_input(root, metadata)
    expected_capsule = build_terminal_support(
        rows,
        config=terminal_config,
        development_register_closed=True,
        authenticated_metadata=metadata,
        **history_bindings,
    )
    if any(count <= 0 for _, count in expected_capsule.per_instrument_counts):
        raise ValueError("authenticated terminal support lacks all-instrument coverage")
    capsule_path = root / "support" / "terminal-support-canonical.json"
    capsule = build_terminal_support(
        rows,
        config=terminal_config,
        output_path=capsule_path,
        development_register_closed=True,
        authenticated_metadata=metadata,
        **history_bindings,
    )
    _validate_terminal_capsule_payload(
        capsule.to_dict(), config=runtime_config, expected_payload=expected_capsule.to_dict()
    )
    _write_json_once(
        root / "support" / "terminal-support-binding.json",
        {
            "capsule_identity": capsule.artifact_identity,
            "terminal_metadata_identity": metadata.content_identity,
            "capsule_file_sha256": _file_digest(capsule_path),
            "register_identity": register_identity,
            "register_file_sha256": _file_digest(root / "register" / "development-register.json"),
            "terminal_input_file_sha256": _file_digest(
                root / "input" / "terminal-support-input.parquet"
            ),
            "g0_identity": identity.identity,
        },
    )
    return capsule.artifact_identity


def _record_closure_report_best_effort(*args: Any, **kwargs: Any) -> None:
    try:
        write_outcome_blind_closure_report(*args, **kwargs)
    except Exception:
        # Retention is best effort at an exception boundary; the triggering error wins.
        return


@reference_process
def close_authenticated_register(
    root: Path, identity: G0ExecutionIdentity, *, config: FrozenRuntimeConfig | None = None
) -> str:
    runtime_config = config or FrozenRuntimeConfig()
    try:
        return _close_authenticated_register_impl(root, identity, config=config)
    except FileExistsError:
        raise
    except Exception as exc:
        try:
            write_outcome_blind_closure_report(
                root,
                status="RUN_FAILED",
                reason=f"development register closure failed: {exc}",
                phase="DEVELOPMENT_CLOSURE",
                identity=identity,
                config=runtime_config,
            )
        except Exception as report_error:
            exc.add_note(f"closure report retention failed: {report_error}")
        raise


@reference_process
def materialise_authenticated_terminal_support(
    root: Path, config: FrozenRuntimeConfig | None, identity: G0ExecutionIdentity
) -> str:
    runtime_config = config or FrozenRuntimeConfig()
    try:
        return _materialise_authenticated_terminal_support_impl(root, config, identity)
    except FileExistsError:
        raise
    except Exception as exc:
        try:
            write_outcome_blind_closure_report(
                root,
                status="RUN_FAILED",
                reason=f"terminal support materialisation failed: {exc}",
                phase="TERMINAL_SUPPORT_MATERIALISATION",
                identity=identity,
                config=runtime_config,
            )
        except Exception as report_error:
            exc.add_note(f"closure report retention failed: {report_error}")
        raise


@dataclass(frozen=True)
class _PreparedTerminalPrediction:
    """Process-local authenticated inputs, with no fitted model or optimiser state."""

    root: Path
    identity: G0ExecutionIdentity
    runtime_config: FrozenRuntimeConfig
    context: Mapping[str, Any]
    metadata: Any
    capsule_payload: Mapping[str, Any]
    register_identity: str
    prediction_input: Any
    prediction_input_identity: str
    support: Any
    linear_controls: Mapping[str, Any]
    preprocessor: Any
    prediction_batch: Any


def _predict_authenticated_terminal_slot_impl(
    root: Path,
    family_id: str,
    seed: int,
    config: FrozenRuntimeConfig | None,
    identity: G0ExecutionIdentity,
) -> str:
    if family_id not in FITTED_FAMILY_IDS:
        raise ValueError("terminal family is outside the frozen fitted schedule")
    if seed not in PRIMARY_SEEDS:
        raise ValueError("terminal seed is outside the frozen primary schedule")
    prepared = _prepare_authenticated_terminal_prediction(root, config, identity)
    return _predict_prepared_authenticated_terminal_slot(prepared, family_id, seed)


def _prepare_authenticated_terminal_prediction(
    root: Path, config: FrozenRuntimeConfig | None, identity: G0ExecutionIdentity
) -> _PreparedTerminalPrediction:
    """Prepare the unchanged outcome-blind inputs once for the frozen terminal slots."""
    import polars as pl

    from .foundation import (
        _seal_dev_control_training,
        authenticate_terminal_metadata,
        authenticate_terminal_prediction_input,
        terminal_history_bindings,
    )
    from .graph import build_fixed_economic_graph
    from .runtime import PredictionBatch
    from .terminal_support import TerminalSupportConfig, build_terminal_support

    parent_context = _load_authenticated_parent_context(root, config)
    current_identity = parent_context["current_identity"]
    if current_identity.identity != identity.identity:
        raise ValueError("supplied G0 identity does not match the authenticated root")
    runtime_config = config or FrozenRuntimeConfig()
    register_path = root / "register" / "development-register.json"
    capsule_path = root / "support" / "terminal-support-canonical.json"
    binding_path = root / "support" / "terminal-support-binding.json"
    capsule_payload = _read_json(capsule_path)
    terminal_binding = _read_json(binding_path)
    graph = build_fixed_economic_graph()
    parent = parent_context["parent"]
    terminal_config = TerminalSupportConfig.from_authenticated_parent(parent, runtime_config, graph)
    metadata = authenticate_terminal_metadata(
        parent=parent, runtime_config=runtime_config, graph=graph
    )
    history_bindings = terminal_history_bindings(
        parent,
        min(
            _as_utc(row["decision_time"])
            for row in metadata.to_rows()
            if row["target_valid"] is True or row["target_valid"] == 1
        ),
    )
    terminal_rows = _terminal_rows_from_input(root, metadata)
    expected_terminal = build_terminal_support(
        terminal_rows,
        config=terminal_config,
        development_register_closed=True,
        authenticated_metadata=metadata,
        **history_bindings,
    )
    _validate_terminal_capsule_payload(
        capsule_payload,
        config=runtime_config,
        expected_payload=expected_terminal.to_dict(),
    )
    register_payload = _validate_development_register_metadata(root, identity, config=config)
    register_identity = register_payload["register_identity"]
    if (
        terminal_binding["g0_identity"] != identity.identity
        or terminal_binding["terminal_metadata_identity"] != metadata.content_identity
        or _file_digest(capsule_path) != terminal_binding["capsule_file_sha256"]
        or capsule_payload["artifact_identity"] != terminal_binding["capsule_identity"]
        or _file_digest(register_path) != terminal_binding["register_file_sha256"]
        or _development_journal_identity(root) != register_payload["attempts_file_sha256"]
        or _file_digest(root / "input" / "terminal-support-input.parquet")
        != terminal_binding["terminal_input_file_sha256"]
        or register_identity != terminal_binding["register_identity"]
        or register_payload["outcomes_loaded"] is not False
        or register_payload["outcome_blind"] is not True
    ):
        raise ValueError("terminal support or register binding drift detected")
    # The sealed capsule and its binding gate all outcome-bearing capability.
    context = _load_real_context(root, config, outcome_free=True)
    register_payload = _validate_development_register_payload(
        root, identity, config=runtime_config, context=context
    )
    register_identity = register_payload["register_identity"]
    dev_control_rows = _authenticated_dev_control_training_rows(parent, TERMINAL_START)
    dev_control_capability = _seal_dev_control_training(parent, dev_control_rows, TERMINAL_START)
    prediction_input = authenticate_terminal_prediction_input(
        parent=parent,
        runtime_config=runtime_config,
        graph=graph,
        terminal_metadata=metadata,
        terminal_capsule=expected_terminal,
        preprocessor_identity=training_preprocessor_identity(context["preprocessor"]),
        training_capability=dev_control_capability,
    )
    prediction_input_identity = prediction_input.content_identity
    support_rows = pl.DataFrame(prediction_input.to_rows())
    terminal_times = tuple(
        sorted({row["decision_time"] for row in support_rows.iter_rows(named=True)})
    )
    from .tensor import _SUPPORT_ALLOWED_COLUMNS, candidate_independent_support

    canonical_terminal_rows = support_rows.select(sorted(_SUPPORT_ALLOWED_COLUMNS))
    first_terminal_time = min(terminal_times)
    history_rows = _terminal_history_rows(context["parent"], first_terminal_time).select(
        sorted(_SUPPORT_ALLOWED_COLUMNS)
    )
    support_input_rows = pl.concat(
        [history_rows, canonical_terminal_rows], how="vertical_relaxed"
    ).sort(["decision_time", "instrument_id"])
    support = candidate_independent_support(
        support_input_rows,
        terminal_times,
        authenticated_parent=context["parent"],
        terminal_prediction_input=prediction_input,
        _eligible_blocks=frozenset({"TERMINAL_FORMER_HOLDOUT"}),
        _allow_terminal=True,
    )
    capsule_times = tuple(sorted({key.rsplit("|", 1)[0] for key in capsule_payload["keys"]}))
    if (
        tuple(support.keys) != capsule_times
        or support.key_count != capsule_payload["decision_time_count"]
    ):
        raise ValueError("persisted terminal support capsule does not match tensors")
    tensors = _build_tensors(support_input_rows, support)
    linear_controls = reconstruct_authenticated_linear_forecasts(
        dev_control_rows,
        validation_by_period={_TERMINAL_STAGE: canonical_terminal_rows},
        periods=(_TERMINAL_STAGE,),
        capsule=context["parent"],
        support_identity=support.identity,
        support=support,
        authenticated_source_rows=dev_control_rows,
        terminal_input=prediction_input,
    )
    preprocessor = context["preprocessor"]
    support_tensor_identities = dict(support.tensor_identities) if support.target_keys else {}
    prediction_input_identity = _sha256(
        {
            "support_identity": support.identity,
            "row_keys": support.target_keys,
            "tensor_identities": tuple(
                support_tensor_identities[key.rsplit("|", 1)[-1]] for key in support.target_keys
            ),
            "preprocessor_identity": training_preprocessor_identity(preprocessor),
            "policy": TIMESTAMP_MATERIALISATION_POLICY,
        }
    )
    prediction_batch = PredictionBatch.from_authenticated_support(
        tensors=tensors,
        support=support,
        preprocessor=preprocessor,
        input_identity=prediction_input_identity,
        stream=True,
    )
    prediction_input_identity = prediction_input.content_identity
    return _PreparedTerminalPrediction(
        root=root,
        identity=identity,
        runtime_config=runtime_config,
        context=context,
        metadata=metadata,
        capsule_payload=capsule_payload,
        register_identity=register_identity,
        prediction_input=prediction_input,
        prediction_input_identity=prediction_input_identity,
        support=support,
        linear_controls=linear_controls,
        preprocessor=preprocessor,
        prediction_batch=prediction_batch,
    )


def _predict_prepared_authenticated_terminal_slot(
    prepared: _PreparedTerminalPrediction, family_id: str, seed: int
) -> str:
    """Consume shared inputs with the original per-slot attempt and numerical operations."""
    if family_id not in FITTED_FAMILY_IDS:
        raise ValueError("terminal family is outside the frozen fitted schedule")
    if seed not in PRIMARY_SEEDS:
        raise ValueError("terminal seed is outside the frozen primary schedule")
    import io

    import torch

    from .runtime import ResidualTrainingBatch, fit_one_model

    root = prepared.root
    identity = prepared.identity
    runtime_config = prepared.runtime_config
    context = prepared.context
    metadata = prepared.metadata
    prediction_input_identity = prepared.prediction_input_identity
    preprocessor = prepared.preprocessor
    journal = CreateOnlyAttemptJournal(root)
    slot_id = f"{family_id}:{seed}:{_TERMINAL_STAGE}"
    attempt = _next_attempt(journal, slot_id)
    attempt_identity = AttemptIdentity(
        release_identity=RUNTIME_VERSION,
        g0_identity=identity.identity,
        output_root=str(root),
        slot_id=slot_id,
        family_id=family_id,
        seed=seed,
        stage=_TERMINAL_STAGE,
        attempt=attempt,
        mode="PRIMARY",
    )
    journal.append(
        attempt_identity,
        "STARTED",
        {
            "config_identity": runtime_config.identity,
            "foundation_content_identity": context["foundation"].content_identity,
            "prediction_input_identity": prediction_input_identity,
        },
    )
    _prediction_attempt_started.set(True)
    staging = root / "staging" / "attempt" / attempt_identity.identity
    destination = root / "attempts" / slot_id / f"attempt-{attempt}"
    if staging.exists() or destination.exists():
        raise FileExistsError("terminal attempt destination already exists")
    staging.mkdir(parents=True)
    model_path, model_metadata_path = staging / "model.pt", staging / "model.json"
    try:
        training_batch = ResidualTrainingBatch.from_authenticated_oof(
            tensors=context["tensors"],
            foundation=context["foundation"],
            support=context["support"],
            preprocessor=preprocessor,
            stream=True,
        )
        torch.manual_seed(seed)
        model = _build_real_family_model(family_id).to(require_cuda())
        fit_result = fit_one_model(model, training_batch)
        state_buffer = io.BytesIO()
        torch.save(model.state_dict(), state_buffer)
        _write_bytes_once(model_path, state_buffer.getvalue())
        if not isinstance(fit_result, dict):
            raise ValueError("terminal fit resource metadata is missing")
        graph = model.adjacency_matrix()
        graph_identity = None if graph is None else _sha256(graph.detach().cpu().tolist())
        model_metadata = {
            "artifact_type": "R4.C_TERMINAL_MODEL_METADATA",
            "slot_id": slot_id,
            "family_id": family_id,
            "seed": seed,
            "stage": _TERMINAL_STAGE,
            "attempt": attempt,
            "g0_identity": identity.identity,
            "terminal_metadata_identity": metadata.content_identity,
            "prediction_input_identity": prediction_input_identity,
            "config_identity": runtime_config.identity,
            "foundation_content_identity": context["foundation"].content_identity,
            "training_batch_identity": training_batch.content_identity,
            "preprocessor_identity": training_preprocessor_identity(preprocessor),
            "architecture": model.architecture(),
            "architecture_identity": _sha256(model.architecture()),
            "graph_identity": graph_identity,
            "state_sha256": _file_digest(model_path),
            "model_file_sha256": _file_digest(model_path),
            "fit": fit_result,
        }
        _write_json_once(model_metadata_path, model_metadata)
        return _publish_prepared_terminal_prediction(
            prepared, model, fit_result, attempt_identity, staging
        )
    except Exception as exc:
        try:
            if staging.exists():
                preserve_failed_staging(
                    staging,
                    root / "failures" / slot_id / f"attempt-{attempt}",
                    {"attempt_id": attempt_identity.identity, "error": str(exc)},
                )
            journal.append(attempt_identity, "FAILED", {"error": str(exc)})
        except Exception as terminal_error:
            exc.add_note(f"terminal failure persistence failed: {terminal_error}")
        raise


def _publish_prepared_terminal_prediction(
    prepared: _PreparedTerminalPrediction,
    model: Any,
    fit_result: Mapping[str, Any],
    attempt_identity: AttemptIdentity,
    staging: Path,
) -> str:
    """Publish the original inference result from fresh or exactly restored fitted state."""
    import torch

    from .runtime import apply_residual_correction, predict_residual

    root = prepared.root
    identity = prepared.identity
    metadata = prepared.metadata
    capsule_payload = prepared.capsule_payload
    register_identity = prepared.register_identity
    prediction_input = prepared.prediction_input
    prediction_input_identity = prepared.prediction_input_identity
    support = prepared.support
    linear_controls = prepared.linear_controls
    preprocessor = prepared.preprocessor
    prediction_batch = prepared.prediction_batch
    slot_id = attempt_identity.slot_id
    attempt = attempt_identity.attempt
    journal = CreateOnlyAttemptJournal(root)
    destination = root / "attempts" / slot_id / f"attempt-{attempt}"
    model_path, model_metadata_path = staging / "model.pt", staging / "model.json"
    prediction_path, result_path = staging / "prediction.json", staging / "result.json"
    prediction = predict_residual(model, prediction_batch)
    forecasts_by_key = dict(prediction_input.forecasts)
    local_forecast = torch.as_tensor(
        [forecasts_by_key[key] for key in support.target_keys],
        dtype=prediction.dtype,
        device=prediction.device,
    )
    if local_forecast.shape != prediction.shape:
        if local_forecast.numel() != prediction.numel():
            raise ValueError("terminal local forecast and residual prediction shapes differ")
        local_forecast = local_forecast.reshape(prediction.shape)
    total_forecast = apply_residual_correction(local_forecast, prediction)
    control_period = linear_controls[_TERMINAL_STAGE]
    if tuple(control_period["keys"]) != tuple(support.target_keys):
        raise ValueError("authenticated terminal linear-control keys differ from support")
    pooled_forecast = torch.as_tensor(
        tuple(control_period["FULLY_POOLED_LOCAL_RIDGE"]),
        dtype=prediction.dtype,
        device=prediction.device,
    )
    if pooled_forecast.shape != local_forecast.shape:
        raise ValueError("terminal pooled forecast and local forecast shapes differ")
    forecast_payload = {
        "prediction_input_identity": prediction_input_identity,
        "input_identity": prediction_batch.input_identity,
        "model_file_sha256": _file_digest(model_path),
        "model_metadata_file_sha256": _file_digest(model_metadata_path),
        "target_keys": list(support.target_keys),
        "local_ridge_forecast": local_forecast.detach().cpu().tolist(),
        "fully_pooled_local_ridge_forecast": pooled_forecast.detach().cpu().tolist(),
        "linear_control_identity": control_period["identity"],
        "linear_controls": control_period["controls"],
        "linear_control_identities": control_period["control_identities"],
        "linear_control_support_identity": control_period["support_identity"],
        "residual_prediction": prediction.detach().cpu().tolist(),
        "total_forecast": total_forecast.detach().cpu().tolist(),
    }
    forecast_identity = _terminal_prediction_identity(forecast_payload)
    payload = {
        "artifact_type": "R4.C_TERMINAL_SLOT_PREDICTION",
        "slot_id": slot_id,
        "attempt_id": attempt_identity.identity,
        "attempt": attempt,
        "g0_identity": identity.identity,
        "terminal_metadata_identity": metadata.content_identity,
        "support_identity": support.identity,
        "terminal_capsule_identity": capsule_payload["artifact_identity"],
        "register_identity": register_identity,
        "target_keys": list(support.target_keys),
        "prediction": prediction.detach().cpu().tolist(),
        "residual_prediction": prediction.detach().cpu().tolist(),
        "local_ridge_forecast": local_forecast.detach().cpu().tolist(),
        "fully_pooled_local_ridge_forecast": pooled_forecast.detach().cpu().tolist(),
        "linear_control_identity": control_period["identity"],
        "linear_controls": control_period["controls"],
        "linear_control_identities": control_period["control_identities"],
        "linear_control_support_identity": control_period["support_identity"],
        "fit": fit_result,
        "total_forecast": total_forecast.detach().cpu().tolist(),
        "forecast_identity": forecast_identity,
        "input_identity": prediction_batch.input_identity,
        "prediction_input_identity": prediction_input_identity,
        "preprocessor_identity": training_preprocessor_identity(preprocessor),
        "outcomes_loaded": False,
        "prediction_closed": True,
        "model_file_sha256": _file_digest(model_path),
        "model_metadata_file_sha256": _file_digest(model_metadata_path),
    }
    payload["prediction_identity"] = _terminal_prediction_payload_identity(payload)
    _write_json_once(prediction_path, payload)
    _write_json_once(
        result_path,
        {
            "artifact_type": "R4.C_TERMINAL_ATTEMPT_RESULT",
            "slot_id": slot_id,
            "attempt": attempt,
            "prediction_identity": payload["prediction_identity"],
            "outcomes_loaded": False,
        },
    )
    seal = seal_attempt_staging(
        staging,
        attempt_identity,
        required_paths=("model.pt", "model.json", "prediction.json", "result.json"),
    )
    publish_attempt_bundle(staging, destination, attempt_identity)
    journal.append(
        attempt_identity,
        "SUCCEEDED",
        {
            "bundle": destination.relative_to(root).as_posix(),
            "seal_identity": seal["seal_identity"],
            "prediction_identity": payload["prediction_identity"],
        },
    )
    return str(payload["prediction_identity"])


def _recover_prepared_terminal_prediction(
    prepared: _PreparedTerminalPrediction,
    *,
    expected_attempt_id: str,
    model_sha256: str,
    model_metadata_sha256: str,
) -> str:
    """Complete the sole operator-authorised learned29 inference recovery; never fit or retry."""
    import io

    import torch

    from .runtime import ResidualTrainingBatch

    root = prepared.root
    slot_id = f"LEARNED_STATIC_GRAPH_RESIDUAL:29:{_TERMINAL_STAGE}"
    records = _slot_journal_records(root, slot_id)
    if len(records) != 1 or records[0]["status"] != "STARTED":
        raise ValueError("inference recovery requires the original open attempt")
    attempt_identity = AttemptIdentity(
        release_identity=RUNTIME_VERSION,
        g0_identity=prepared.identity.identity,
        output_root=str(root),
        slot_id=slot_id,
        family_id="LEARNED_STATIC_GRAPH_RESIDUAL",
        seed=29,
        stage=_TERMINAL_STAGE,
        attempt=0,
        mode="PRIMARY",
    )
    if (
        records[0]["attempt_id"] != expected_attempt_id
        or attempt_identity.identity != expected_attempt_id
    ):
        raise ValueError("inference recovery attempt identity changed")
    _prediction_attempt_started.set(True)
    started = {
        "config_identity": prepared.runtime_config.identity,
        "foundation_content_identity": prepared.context["foundation"].content_identity,
        "prediction_input_identity": prepared.prediction_input_identity,
    }
    if records[0]["payload"] != started:
        raise ValueError("inference recovery STARTED input identities changed")
    source = root / "staging" / "attempt" / expected_attempt_id
    staging = root / "staging" / "recovery" / expected_attempt_id
    destination = root / "attempts" / slot_id / "attempt-0"
    if staging.exists() or destination.exists():
        raise FileExistsError("inference recovery destination already exists")
    if {path.name for path in source.iterdir()} != {"model.pt", "model.json"}:
        raise ValueError("interrupted staging is not the exact retained model pair")
    model_bytes = (source / "model.pt").read_bytes()
    metadata_bytes = (source / "model.json").read_bytes()
    if hashlib.sha256(model_bytes).hexdigest() != model_sha256 or (
        hashlib.sha256(metadata_bytes).hexdigest() != model_metadata_sha256
    ):
        raise ValueError("inference recovery retained model hashes changed")
    model_metadata = strict_json_object(metadata_bytes)
    expected_metadata = {
        "artifact_type": "R4.C_TERMINAL_MODEL_METADATA",
        "slot_id": slot_id,
        "family_id": attempt_identity.family_id,
        "seed": 29,
        "stage": _TERMINAL_STAGE,
        "attempt": 0,
        "g0_identity": prepared.identity.identity,
        "terminal_metadata_identity": prepared.metadata.content_identity,
        **started,
        "preprocessor_identity": training_preprocessor_identity(prepared.preprocessor),
        "state_sha256": model_sha256,
        "model_file_sha256": model_sha256,
    }
    if any(model_metadata[key] != value for key, value in expected_metadata.items()):
        raise ValueError("inference recovery model input metadata changed")
    training_batch = ResidualTrainingBatch.from_authenticated_oof(
        tensors=prepared.context["tensors"],
        foundation=prepared.context["foundation"],
        support=prepared.context["support"],
        preprocessor=prepared.preprocessor,
        stream=True,
    )
    if model_metadata["training_batch_identity"] != training_batch.content_identity:
        raise ValueError("inference recovery training batch identity changed")
    model = _reload_real_model(
        source / "model.pt",
        slot_id,
        model_metadata,
        model_metadata["fit"],
        device=prepared.runtime_config.device,
    )
    retained_state = torch.load(io.BytesIO(model_bytes), map_location="cpu", weights_only=True)
    restored_state = model.state_dict()
    if set(retained_state) != set(restored_state):
        raise ValueError("inference recovery state keys changed")
    for key, retained in retained_state.items():
        restored = restored_state[key].detach().cpu()
        if (
            retained.shape != restored.shape
            or retained.dtype != restored.dtype
            or (not torch.isfinite(retained).all())
            or not torch.equal(
                retained.reshape(-1).view(torch.uint8), restored.reshape(-1).view(torch.uint8)
            )
        ):
            raise ValueError(f"inference recovery state tensor changed: {key}")
    staging.mkdir(parents=True)
    _write_bytes_once(staging / "model.pt", model_bytes)
    _write_bytes_once(staging / "model.json", metadata_bytes)
    # The retained source stays untouched, including on publication or journal failure.
    return _publish_prepared_terminal_prediction(
        prepared, model, model_metadata["fit"], attempt_identity, staging
    )


def _ledger_dispositions(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Summarise slots while deriving lifecycle from unique attempt numbers."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["slot_id"]), []).append(record)
    dispositions: list[dict[str, Any]] = []
    for slot_id in sorted(grouped):
        slot_records = sorted(grouped[slot_id], key=lambda record: int(record["attempt"]))
        attempts_by_number: dict[int, list[Mapping[str, Any]]] = {}
        for record in slot_records:
            attempts_by_number.setdefault(int(record["attempt"]), []).append(record)
        grouped_attempts: list[list[Mapping[str, Any]]] = [
            attempt_records for _, attempt_records in sorted(attempts_by_number.items())
        ]
        for position, attempt_group in enumerate(grouped_attempts):
            statuses = [str(record["status"]) for record in attempt_group]
            if statuses not in (
                ["STARTED"],
                ["STARTED", "SUCCEEDED"],
                ["STARTED", "FAILED"],
            ):
                raise ValueError("attempt lifecycle is not canonical")
            if position < len(grouped_attempts) - 1 and statuses != ["STARTED", "FAILED"]:
                raise ValueError("retry lifecycle is not canonical")
            if position > 0:
                previous = grouped_attempts[position - 1]
                if previous[-1]["status"] != "FAILED":
                    raise ValueError("retry lifecycle is not canonical")
        final = grouped_attempts[-1][-1]
        final_status = final["status"]
        dispositions.append(
            {
                "slot_id": slot_id,
                "mode": final["mode"],
                "stage": final["stage"],
                "attempts": [
                    {
                        "attempt": int(record["attempt"]),
                        "status": record["status"],
                        "error": record.get("error"),
                    }
                    for record in slot_records
                ],
                "final_status": final_status,
                "disposition": (
                    "SUCCEEDED"
                    if final_status == "SUCCEEDED"
                    else "FAILED"
                    if final_status == "FAILED"
                    else "OPEN"
                ),
            }
        )
    return dispositions


@reference_process
def predict_authenticated_terminal_slot(
    root: Path,
    family_id: str,
    seed: int,
    config: FrozenRuntimeConfig | None,
    identity: G0ExecutionIdentity,
) -> str:
    """Run a terminal slot and retain any pre-attempt failure as closure evidence."""
    token = _prediction_attempt_started.set(False)
    runtime_config = config or FrozenRuntimeConfig()
    try:
        return _predict_authenticated_terminal_slot_impl(root, family_id, seed, config, identity)
    except FileExistsError:
        raise
    except Exception as exc:
        if not _prediction_attempt_started.get():
            try:
                write_outcome_blind_closure_report(
                    root,
                    status=_failure_status(exc),
                    reason=f"terminal prediction failed: {exc}",
                    phase="TERMINAL_PRE_ATTEMPT_VALIDATION",
                    identity=identity,
                    config=runtime_config,
                    outcomes_loaded=False,
                )
            except Exception as report_error:
                exc.add_note(f"closure report retention failed: {report_error}")
        raise
    finally:
        _prediction_attempt_started.reset(token)


def _aggregate_authenticated_metrics_impl(
    root: Path, config: FrozenRuntimeConfig | None, identity: G0ExecutionIdentity
) -> str:
    import polars as pl

    _aggregate_outcomes_loaded.set(False)
    runtime_config = config or FrozenRuntimeConfig()
    context = _load_real_context(root, runtime_config, outcome_free=True)
    terminal_binding = _read_json(root / "support" / "terminal-support-binding.json")
    capsule_payload = _read_json(root / "support" / "terminal-support-canonical.json")
    _validate_terminal_capsule_payload(capsule_payload, config=config)
    register_payload = _validate_development_register_payload(root, identity, config=config)
    development_metric_payload = _read_json(root / "register" / "development-metrics.json")
    if (
        terminal_binding["g0_identity"] != identity.identity
        or terminal_binding.get("terminal_metadata_identity")
        != capsule_payload.get("terminal_metadata_identity")
        or _file_digest(root / "support" / "terminal-support-canonical.json")
        != terminal_binding["capsule_file_sha256"]
        or capsule_payload["artifact_identity"] != terminal_binding["capsule_identity"]
        or _file_digest(root / "register" / "development-register.json")
        != terminal_binding["register_file_sha256"]
        or _development_journal_identity(root) != register_payload["attempts_file_sha256"]
        or _file_digest(root / "input" / "terminal-support-input.parquet")
        != terminal_binding["terminal_input_file_sha256"]
        or register_payload["outcomes_loaded"] is not False
        or register_payload["outcome_blind"] is not True
    ):
        raise ValueError("terminal binding artifacts drifted before metrics")
    journal_records = _journal_records(root)
    terminal_records = [
        {
            **dict(record["attempt"]),
            **dict(record["payload"]),
            "status": record["status"],
            "attempt_id": record["attempt_id"],
        }
        for record in journal_records
        if dict(record["attempt"])["stage"] == _TERMINAL_STAGE
    ]
    terminal_statuses: dict[str, set[str]] = {}
    for record in terminal_records:
        terminal_statuses.setdefault(str(record["attempt_id"]), set()).add(str(record["status"]))
    if any(statuses == {"STARTED"} for statuses in terminal_statuses.values()):
        _record_closure_report_best_effort(
            root,
            status="RUN_FAILED",
            reason="terminal attempt journal has open attempts",
            identity=identity,
            config=runtime_config,
        )
        raise ValueError("metrics require closed terminal attempt journal")
    terminal_attempts = terminal_records
    expected_terminal = {
        f"{family}:{seed}:{_TERMINAL_STAGE}"
        for family in FITTED_FAMILY_IDS
        for seed in PRIMARY_SEEDS
    }
    successful_terminal_records = [
        record for record in terminal_attempts if record["status"] == "SUCCEEDED"
    ]
    if any(
        record["mode"] != "PRIMARY" or record["stage"] != _TERMINAL_STAGE
        for record in successful_terminal_records
    ):
        _record_closure_report_best_effort(
            root,
            status="RUN_INVALIDATED_IMPLEMENTATION",
            reason="terminal journal contains a non-primary successful attempt",
            identity=identity,
            config=runtime_config,
        )
        raise _ImplementationInvariantError("metrics require only PRIMARY terminal attempts")
    from .foundation import (
        _seal_dev_control_training,
        authenticate_terminal_metadata,
        authenticate_terminal_prediction_input,
        terminal_history_bindings,
    )
    from .graph import build_fixed_economic_graph
    from .runtime import ResidualTrainingBatch
    from .terminal_support import TerminalSupportConfig, build_terminal_support

    training_batch_identity = ResidualTrainingBatch.from_authenticated_oof(
        tensors=context["tensors"],
        foundation=context["foundation"],
        support=context["support"],
        preprocessor=context["preprocessor"],
        stream=True,
    ).content_identity
    preprocessor_identity = training_preprocessor_identity(context["preprocessor"])
    runtime_config = config or FrozenRuntimeConfig()
    graph = build_fixed_economic_graph()
    metadata = authenticate_terminal_metadata(
        parent=context["parent"], runtime_config=runtime_config, graph=graph
    )
    history_bindings = terminal_history_bindings(
        context["parent"],
        min(
            _as_utc(row["decision_time"])
            for row in metadata.to_rows()
            if row["target_valid"] is True or row["target_valid"] == 1
        ),
    )
    terminal_rows = _terminal_rows_from_input(root, metadata)
    expected_capsule = build_terminal_support(
        terminal_rows,
        config=TerminalSupportConfig.from_authenticated_parent(
            context["parent"], runtime_config, graph
        ),
        development_register_closed=True,
        authenticated_metadata=metadata,
        **history_bindings,
    )
    _validate_terminal_capsule_payload(
        capsule_payload,
        config=config,
        expected_payload=expected_capsule.to_dict(),
    )
    dev_control_rows = _authenticated_dev_control_training_rows(context["parent"], TERMINAL_START)
    dev_control_capability = _seal_dev_control_training(
        context["parent"], dev_control_rows, TERMINAL_START
    )
    prediction_input = authenticate_terminal_prediction_input(
        parent=context["parent"],
        runtime_config=runtime_config,
        graph=graph,
        terminal_metadata=metadata,
        terminal_capsule=expected_capsule,
        preprocessor_identity=preprocessor_identity,
        training_capability=dev_control_capability,
    )
    prediction_input_identity = prediction_input.content_identity
    from .tensor import _SUPPORT_ALLOWED_COLUMNS, candidate_independent_support

    prediction_rows = pl.DataFrame(prediction_input.to_rows())
    terminal_rows = prediction_rows.select(sorted(_SUPPORT_ALLOWED_COLUMNS))
    terminal_times = tuple(
        sorted({row["decision_time"] for row in terminal_rows.iter_rows(named=True)})
    )

    first_terminal_time = min(terminal_times)
    history_rows = _terminal_history_rows(context["parent"], first_terminal_time).select(
        sorted(_SUPPORT_ALLOWED_COLUMNS)
    )
    support_input_rows = pl.concat([history_rows, terminal_rows], how="vertical_relaxed").sort(
        ["decision_time", "instrument_id"]
    )
    terminal_support = candidate_independent_support(
        support_input_rows,
        terminal_times,
        authenticated_parent=context["parent"],
        terminal_prediction_input=prediction_input,
        _eligible_blocks=frozenset({_TERMINAL_STAGE}),
        _allow_terminal=True,
    )
    terminal_support_identity = terminal_support.identity
    linear_controls = reconstruct_authenticated_linear_forecasts(
        dev_control_rows,
        validation_by_period={_TERMINAL_STAGE: terminal_rows},
        periods=(_TERMINAL_STAGE,),
        capsule=context["parent"],
        support_identity=terminal_support_identity,
        support=terminal_support,
        authenticated_source_rows=dev_control_rows,
        terminal_input=prediction_input,
    )
    control_period = linear_controls[_TERMINAL_STAGE]
    linear_control_identity = control_period["identity"]
    successful_terminal = {
        record["slot_id"]: record for record in terminal_attempts if record["status"] == "SUCCEEDED"
    }
    if set(successful_terminal) != expected_terminal:
        _record_closure_report_best_effort(
            root,
            status="RUN_FAILED",
            reason="terminal register is incomplete",
            identity=identity,
            config=runtime_config,
        )
        raise ValueError("metrics require every terminal slot to close successfully")
    if len(successful_terminal_records) != len(expected_terminal):
        _record_closure_report_best_effort(
            root,
            status="RUN_FAILED",
            reason="terminal register has duplicate successful attempts",
            identity=identity,
            config=runtime_config,
        )
        raise ValueError("metrics require exactly one successful terminal attempt per slot")
    prediction_paths: list[Path] = []
    for slot_id in sorted(expected_terminal):
        record = successful_terminal[slot_id]
        attempt = cast(int, record["attempt"])
        bundle = root / "attempts" / slot_id / f"attempt-{attempt}"
        expected_attempt = AttemptIdentity(
            release_identity=RUNTIME_VERSION,
            g0_identity=identity.identity,
            output_root=str(root),
            slot_id=slot_id,
            family_id=str(record["family_id"]),
            seed=cast(int, record["seed"]),
            stage=_TERMINAL_STAGE,
            attempt=attempt,
            mode="PRIMARY",
        )
        seal = cast(Mapping[str, Any], verify_attempt_bundle(bundle, expected_attempt)["seal"])
        if record.get("seal_identity") != seal["seal_identity"]:
            raise ValueError("terminal journal seal identity drifted")
        prediction_paths.append(bundle / "prediction.json")
    expected = len(FITTED_FAMILY_IDS) * len(PRIMARY_SEEDS)
    if len(prediction_paths) != expected:
        raise ValueError(f"metrics require exactly {expected} terminal predictions")
    predictions = [_read_json(path) for path in prediction_paths]
    capsule_target_keys = tuple(
        f"{key.rsplit('|', 1)[1]}|{key.rsplit('|', 1)[0]}" for key in capsule_payload["keys"]
    )
    seen_slots: set[str] = set()
    terminal_fit_resources: dict[str, dict[str, Any]] = {}
    graph_identity_by_slot: dict[str, str | None] = {}
    for payload, path in zip(predictions, prediction_paths, strict=True):
        slot_id = payload["slot_id"]
        if (
            slot_id not in expected_terminal
            or slot_id in seen_slots
            or int(payload.get("attempt", -1)) != cast(int, successful_terminal[slot_id]["attempt"])
        ):
            raise ValueError(f"terminal prediction slot or attempt is not canonical: {path}")
        seen_slots.add(slot_id)
        prediction_identity = _validate_prediction_payload(
            payload, path, identity=identity, slot_id=slot_id
        )
        if payload.get("support_identity") != terminal_support_identity:
            raise ValueError(f"terminal prediction support identity drifted: {path}")
        if payload.get("terminal_metadata_identity") != metadata.content_identity:
            raise ValueError(f"terminal prediction metadata identity drifted: {path}")
        if payload.get("prediction_input_identity") != prediction_input_identity:
            raise ValueError(f"terminal prediction input identity drifted: {path}")
        if payload.get("linear_control_identity") != linear_control_identity:
            raise ValueError(f"terminal linear-control identity drifted: {path}")
        _validate_linear_control_payload(payload, control_period)
        if payload.get("prediction_identity") != _terminal_prediction_payload_identity(payload):
            raise ValueError(f"terminal prediction payload identity drifted: {path}")
        _validate_terminal_forecast_payload(payload, prediction_input)
        payload_keys = tuple(payload.get("target_keys", ()))
        if payload_keys != capsule_target_keys:
            raise ValueError(f"terminal prediction keys do not match capsule order: {path}")
        if len(payload_keys) != len(set(payload_keys)):
            raise ValueError(f"terminal prediction contains duplicate keys: {path}")
        if payload.get("attempt_id") != successful_terminal[slot_id]["attempt_id"]:
            raise ValueError(f"terminal prediction attempt identity drifted: {path}")
        attempt = int(payload.get("attempt", 0))
        bundle = path.parent
        model_path = bundle / "model.pt"
        model_metadata_path = bundle / "model.json"
        model_metadata = _read_json(model_metadata_path)
        if (
            not model_metadata_path.is_file()
            or payload["g0_identity"] != identity.identity
            or payload["outcomes_loaded"] is not False
            or payload["prediction_closed"] is not True
            or payload["terminal_capsule_identity"] != capsule_payload["artifact_identity"]
            or payload["register_identity"] != terminal_binding["register_identity"]
            or payload.get("preprocessor_identity") != preprocessor_identity
            or _file_digest(model_path) != payload["model_file_sha256"]
            or model_metadata.get("artifact_type") != "R4.C_TERMINAL_MODEL_METADATA"
            or model_metadata.get("slot_id") != slot_id
            or model_metadata.get("family_id") != slot_id.split(":", 2)[0]
            or model_metadata.get("seed") != int(slot_id.split(":", 2)[1])
            or model_metadata.get("stage") != _TERMINAL_STAGE
            or model_metadata.get("attempt", 0) != attempt
            or model_metadata.get("g0_identity") != identity.identity
            or model_metadata.get("terminal_metadata_identity") != metadata.content_identity
            or model_metadata.get("model_file_sha256") != payload["model_file_sha256"]
            or model_metadata.get("state_sha256") != payload["model_file_sha256"]
            or model_metadata.get("config_identity") != (config or FrozenRuntimeConfig()).identity
            or model_metadata.get("foundation_content_identity")
            != context["foundation"].content_identity
            or model_metadata.get("training_batch_identity") != training_batch_identity
            or model_metadata.get("preprocessor_identity") != preprocessor_identity
            or model_metadata.get("prediction_input_identity") != prediction_input_identity
            or model_metadata.get("architecture_identity")
            != _sha256(model_metadata.get("architecture"))
            or payload.get("model_metadata_file_sha256") != _file_digest(model_metadata_path)
            or prediction_identity != payload["prediction_identity"]
        ):
            raise ValueError(f"terminal prediction artifact is not closed and bound: {path}")
        graph_identity = model_metadata.get("graph_identity")
        graph_identity_by_slot[slot_id] = graph_identity
        fit_evidence = _validate_fit_evidence(
            payload.get("fit"),
            slot_id=slot_id,
            expected_training_batch_identity=training_batch_identity,
        )
        if fit_evidence != model_metadata.get("fit"):
            raise ValueError(f"terminal fit evidence is not canonical: {path}")
        terminal_fit_resources[slot_id] = fit_evidence
        import torch

        state = torch.load(model_path, map_location="cpu", weights_only=True)
        if not isinstance(state, dict) or not state:
            raise ValueError(f"terminal model state is not reloadable: {model_path}")
        family_id, seed_text, _stage = slot_id.split(":", 2)
        torch.manual_seed(int(seed_text))
        reload_model = _build_real_family_model(family_id)

        reload_model.load_state_dict(state)
        if reload_model.architecture() != model_metadata["architecture"]:
            raise ValueError(f"terminal model architecture drifted: {model_path}")
        reloaded_graph = reload_model.adjacency_matrix()
        reloaded_graph_identity = (
            None if reloaded_graph is None else _sha256(reloaded_graph.detach().cpu().tolist())
        )
        if reloaded_graph_identity != model_metadata.get("graph_identity"):
            raise ValueError(f"terminal model graph provenance drifted: {model_path}")
        if int(fit_evidence["parameter_count"]) != model_parameter_count(reload_model):
            raise ValueError(f"terminal fit parameter count drifted: {slot_id}")
    graph_identity_by_family = _terminal_primary_graph_identities(graph_identity_by_slot)
    import math

    from .foundation import _target_frame

    targets = _target_frame(
        context["parent"], include_return=True, blocks=("TERMINAL_FORMER_HOLDOUT",)
    )
    _aggregate_outcomes_loaded.set(True)
    targets = targets.filter(pl.col("target_valid"))
    target_by_key: dict[str, float] = {}
    for row in targets.iter_rows(named=True):
        target = float(row["target_return"])
        if not math.isfinite(target):
            raise ValueError("authenticated terminal outcome is not finite")
        key = f"{row['instrument_id']}|{row['decision_time'].isoformat()}"
        if key in target_by_key:
            raise ValueError(f"authenticated terminal outcome is duplicated: {key}")
        target_by_key[key] = target
    target_by_key = _ordered_terminal_targets(target_by_key, capsule_target_keys)
    terminal_outcomes = {
        key: {
            "period": _TERMINAL_STAGE,
            "instrument_id": key.split("|", 1)[0],
            "decision_time": key.rsplit("|", 1)[-1],
            "target_return": value,
        }
        for key, value in target_by_key.items()
    }
    from .foundation import _reconstruct_full_lab_terminal_regression

    full_lab_terminal_regression = _reconstruct_full_lab_terminal_regression(
        context["parent"], dev_control_rows, targets
    )
    if tuple(control_period["keys"]) != capsule_target_keys:
        raise ValueError("terminal control keys do not match sealed support")
    squared_errors: list[float] = []
    absolute_errors: list[float] = []
    slot_metrics: list[dict[str, Any]] = []
    pair_count = 0
    from .evaluation import instrument_balanced_mse

    candidate_zero_mse = instrument_balanced_mse(
        [
            {"instrument_id": key.split("|", 1)[0], "target_return": value, "forecast": 0.0}
            for key, value in target_by_key.items()
        ]
    )

    def _equal_instrument_mae(rows: list[dict[str, Any]]) -> float:
        by_instrument: dict[str, list[float]] = {}
        for row in rows:
            by_instrument.setdefault(str(row["instrument_id"]), []).append(abs(float(row["error"])))
        if not by_instrument:
            raise ValueError("terminal slot has no instrument support")
        return sum(sum(values) / len(values) for values in by_instrument.values()) / len(
            by_instrument
        )

    for payload in predictions:
        keys = tuple(payload["target_keys"])
        values = payload.get("total_forecast")
        if values is None or len(keys) != len(values) or len(keys) != len(capsule_target_keys):
            raise ValueError("terminal prediction keys and values are misaligned")
        slot_squared: list[float] = []
        slot_absolute: list[float] = []
        slot_rows: list[dict[str, Any]] = []
        for key, value in zip(keys, values, strict=True):
            if key not in target_by_key:
                raise ValueError(f"terminal prediction has no authenticated outcome: {key}")
            prediction_value = float(value)
            if not math.isfinite(prediction_value):
                raise ValueError("terminal prediction is not finite")
            error = prediction_value - target_by_key[key]
            squared = error * error
            absolute = abs(error)
            squared_errors.append(squared)
            absolute_errors.append(absolute)
            slot_squared.append(squared)
            slot_absolute.append(absolute)
            slot_rows.append(
                {
                    "instrument_id": key.split("|", 1)[0],
                    "target_return": target_by_key[key],
                    "forecast": prediction_value,
                    "error": error,
                }
            )
            pair_count += 1
        if len(slot_squared) != len(capsule_target_keys):
            raise ValueError("terminal slot pair count does not match capsule key count")
        family_id, seed_text, stage = payload["slot_id"].split(":", 2)
        slot_metrics.append(
            {
                "slot_id": payload["slot_id"],
                "family_id": family_id,
                "seed": int(seed_text),
                "stage": stage,
                "mse": instrument_balanced_mse(slot_rows),
                "mae": _equal_instrument_mae(slot_rows),
                "raw_pair_mse": sum(slot_squared) / len(slot_squared),
                "raw_pair_mae": sum(slot_absolute) / len(slot_absolute),
                "pair_count": len(slot_squared),
                "attempt": int(payload["attempt"]),
                "attempt_id": payload.get("attempt_id"),
                "fit": payload["fit"],
                "parameter_count": payload["fit"]["parameter_count"],
                "prediction_identity": payload["prediction_identity"],
                "model_file_sha256": payload["model_file_sha256"],
                "model_metadata_file_sha256": payload["model_metadata_file_sha256"],
                "support_identity": payload["support_identity"],
                "prediction_input_identity": payload["prediction_input_identity"],
            }
        )
    expected_pairs = len(predictions) * len(capsule_target_keys)
    if pair_count != expected_pairs:
        raise ValueError("terminal metrics pair count does not match slot coverage")
    family_summaries: list[dict[str, Any]] = []
    for family_id in FITTED_FAMILY_IDS:
        family_slots = [slot for slot in slot_metrics if slot["family_id"] == family_id]
        if len(family_slots) != len(PRIMARY_SEEDS):
            raise ValueError("terminal metrics require all seeds per family")
        primary_slot = next(
            slot for slot in family_slots if int(slot["seed"]) == int(PRIMARY_SEEDS[0])
        )
        family_summaries.append(
            {
                "family_id": family_id,
                "seed_count": len(family_slots),
                "primary_seed": int(PRIMARY_SEEDS[0]),
                "aggregation": "equal_instrument_primary_seed",
                "mse": float(primary_slot["mse"]),
                "mae": float(primary_slot["mae"]),
                "pair_count": int(primary_slot["pair_count"]),
                "raw_pair_mse_secondary": float(primary_slot["raw_pair_mse"]),
                "raw_pair_mae_secondary": float(primary_slot["raw_pair_mae"]),
                "auxiliary_seed_metrics": [
                    {
                        "seed": int(slot["seed"]),
                        "mse": float(slot["mse"]),
                        "mae": float(slot["mae"]),
                        "pair_count": int(slot["pair_count"]),
                        "raw_pair_mse": float(slot["raw_pair_mse"]),
                        "raw_pair_mae": float(slot["raw_pair_mae"]),
                    }
                    for slot in family_slots
                    if int(slot["seed"]) != int(PRIMARY_SEEDS[0])
                ],
            }
        )
    from .evaluation import apply_historical_nomination_rule, evaluate_terminal_register

    evaluation = evaluate_terminal_register(
        predictions,
        terminal_outcomes,
        development_register=development_metric_payload,
        identities={
            "g0_identity": identity.identity,
            "terminal_support_identity": terminal_support_identity,
            "terminal_metadata_identity": metadata.content_identity,
            "prediction_input_identity": prediction_input_identity,
            "register_identity": terminal_binding["register_identity"],
        },
    )
    evaluation["metric_identity"] = _sha256(evaluation)
    scientific_performance = evaluation["scientific_performance"]
    if scientific_performance != "COMPUTED_POST_HOC_HISTORICAL_EXPLORATORY":
        raise ValueError("post-outcome scientific performance label is not canonical")
    terminal_fit_summary = _summarize_fit_evidence(
        terminal_fit_resources, expected_slots=tuple(sorted(expected_terminal))
    )
    metric_rows = {
        "artifact_type": "R4.C_TERMINAL_METRICS",
        "terminal_metadata_identity": metadata.content_identity,
        "prediction_input_identity": prediction_input_identity,
        "g0_identity": identity.identity,
        "config_identity": (config or FrozenRuntimeConfig()).identity,
        "prediction_count": len(prediction_paths),
        "pair_count": pair_count,
        "total_pair_count": pair_count,
        "primary_seed": int(PRIMARY_SEEDS[0]),
        "aggregation": "equal_instrument_primary_seed",
        "slot_metrics": slot_metrics,
        "family_summaries": family_summaries,
        "fit_resources": terminal_fit_resources,
        "fit_evidence": terminal_fit_summary,
        "development_metric_register_identity": development_metric_payload["metric_identity"],
        "development_metric_register_file_sha256": _file_digest(
            root / "register" / "development-metrics.json"
        ),
        "evaluation": evaluation,
        "control_bindings": evaluation.get("control_bindings", {}),
        "full_lab_terminal_regression": full_lab_terminal_regression,
        "graph_identity_by_slot": graph_identity_by_slot,
        "r3h_cost_grid": evaluation["r3h_cost_grid"],
        "scientific_performance": scientific_performance,
        "mse": sum(
            float(slot["mse"])
            for slot in slot_metrics
            if int(slot["seed"]) == int(PRIMARY_SEEDS[0])
        )
        / len(FITTED_FAMILY_IDS),
        "mae": sum(
            float(slot["mae"])
            for slot in slot_metrics
            if int(slot["seed"]) == int(PRIMARY_SEEDS[0])
        )
        / len(FITTED_FAMILY_IDS),
        "raw_pair_mse_secondary": sum(squared_errors) / pair_count,
        "raw_pair_mae_secondary": sum(absolute_errors) / pair_count,
        "target_rows": targets.height,
        "outcomes_loaded": True,
        "source_parent_identity": context["parent"].manifest_sha256,
        "foundation_content_identity": context["foundation"].content_identity,
        "development_support_identity": context["support"].identity,
        "terminal_support_identity": terminal_support_identity,
        "preprocessor_identity": preprocessor_identity,
        "terminal_capsule_identity": capsule_payload["artifact_identity"],
        "terminal_capsule_file_sha256": _file_digest(
            root / "support" / "terminal-support-canonical.json"
        ),
        "register_identity": terminal_binding["register_identity"],
        "register_file_sha256": _file_digest(root / "register" / "development-register.json"),
        "terminal_attempts_file_sha256": _sha256(journal_records),
        "terminal_input_file_sha256": _file_digest(
            root / "input" / "terminal-support-input.parquet"
        ),
        "prediction_file_sha256": {
            payload["slot_id"]: _file_digest(path)
            for payload, path in zip(predictions, prediction_paths, strict=True)
        },
        "model_file_sha256": {
            payload["slot_id"]: payload["model_file_sha256"] for payload in predictions
        },
        "model_metadata_file_sha256": {
            payload["slot_id"]: payload["model_metadata_file_sha256"] for payload in predictions
        },
    }
    metric_rows["metric_identity"] = _sha256(metric_rows)
    _write_json_once(root / "metrics" / "terminal-metrics.json", metric_rows)
    nomination_results = {
        candidate: apply_historical_nomination_rule(evaluation, candidate=candidate)
        for candidate in (
            "FIXED_ECONOMIC_GRAPH_RESIDUAL",
            "LEARNED_STATIC_GRAPH_RESIDUAL",
        )
    }
    nominated_candidates = [
        candidate
        for candidate, result in nomination_results.items()
        if result["verdict"] == "HYPOTHESIS_NOMINATED"
    ]
    for candidate, result in nomination_results.items():
        result["disposition"] = "NOMINATED" if candidate in nominated_candidates else "NEGATIVE"
        result["identity_bindings"] = {
            **dict(evaluation.get("identities", {})),
            "candidate": candidate,
            "primary_seed": PRIMARY_SEEDS[0],
            "config_identity": runtime_config.identity,
            "graph_identity": graph_identity_by_family.get(candidate),
            "support_identity": terminal_support_identity,
            "result_identity": evaluation["metric_identity"],
        }
    nomination_payload: dict[str, Any] = {
        "artifact_type": "R4.C_HISTORICAL_NOMINATION",
        "schema_version": "R4.C-1",
        "terminal_metrics_identity": metric_rows["metric_identity"],
        "candidate": list(nominated_candidates),
        "nominated_candidates": list(nominated_candidates),
        "verdict": ("HYPOTHESIS_NOMINATED" if nominated_candidates else "NO_HYPOTHESIS_NOMINATED"),
        "gates": {
            candidate: dict(result["gates"]) for candidate, result in nomination_results.items()
        },
        "gate_results": nomination_results,
        "nomination_rule": "R4_P0_SECTION_10_5",
        "r3h_cost_grid": evaluation["r3h_cost_grid"],
        "scientific_performance": evaluation["scientific_performance"],
        "promotion_authority": "NONE",
    }
    nomination_payload["content_identity"] = _sha256(nomination_payload)
    nomination_path = root / "result" / "nomination.json"
    _write_json_once(nomination_path, nomination_payload)

    development_attempts = [
        {
            **cast(Mapping[str, Any], record["attempt"]),
            "status": record["status"],
            "error": cast(Mapping[str, Any], record["payload"]).get("error"),
        }
        for record in _development_journal_records(root)
    ]
    terminal_dispositions = _ledger_dispositions(terminal_attempts)
    development_dispositions = _ledger_dispositions(development_attempts)
    for slot in slot_metrics:
        if int(slot["seed"]) != int(PRIMARY_SEEDS[0]):
            slot["disposition"] = "AUXILIARY"
            continue
        slot_mse = float(slot["mse"])
        slot["disposition"] = (
            "POSITIVE"
            if slot_mse < candidate_zero_mse
            else "NEGATIVE"
            if slot_mse > candidate_zero_mse
            else "INCONCLUSIVE"
        )
    all_fit_resources = {
        **dict(development_metric_payload.get("fit_resources", {})),
        **terminal_fit_resources,
    }
    fit_evidence_summary = _summarize_fit_evidence(
        all_fit_resources,
        expected_slots=tuple(
            f"{family}:{seed}:{stage}"
            for family, seed, stage in PRIMARY_SCHEDULE
            if family in FITTED_FAMILY_IDS
        ),
    )
    report_payload: dict[str, Any] = {
        "artifact_type": "R4.C_FINAL_EXPLORATORY_REPORT",
        "schema_version": "R4.C-1",
        "status": nomination_payload["verdict"],
        "experiment_class": runtime_config.experiment_class,
        "source_class": runtime_config.source_class,
        "evidence_class": "POST_HOC_HISTORICAL_EXPLORATORY",
        "scientific_performance": _POST_OUTCOME_SCIENTIFIC_PERFORMANCE,
        "authority": {
            "plan": "docs/R4_P0_EXECUTION_PLAN.md",
            "sections": ["10.1", "10.2", "10.3", "10.4", "10.5", "11", "13", "15"],
            "promotion": "NONE",
            "native_or_executable_claim": False,
        },
        "source": {
            "parent_identity": context["parent"].manifest_sha256,
            "foundation_content_identity": context["foundation"].content_identity,
            "r3h_cost_grid": evaluation["r3h_cost_grid"],
        },
        "configuration": runtime_config.to_dict(),
        "g0_identity": identity.identity,
        "config_identity": runtime_config.identity,
        "register_identity": terminal_binding["register_identity"],
        "terminal_metrics_identity": metric_rows["metric_identity"],
        "nomination_identity": nomination_payload["content_identity"],
        "foundation_content_identity": context["foundation"].content_identity,
        "development_support_identity": context["support"].identity,
        "terminal_support_identity": terminal_support_identity,
        "terminal_control_identity": linear_control_identity,
        "prediction_input_identity": prediction_input_identity,
        "support": {
            "development": development_metric_payload.get("support"),
            "terminal": {
                "identity": terminal_support_identity,
                "key_count": len(capsule_target_keys),
                "order": "sealed_terminal_capsule",
            },
            "weighting": "union_within_instrument_then_equal_twenty",
        },
        "full_lab_terminal_regression": full_lab_terminal_regression,
        "fit_evidence": {
            "sections": ["10.4", "11"],
            **fit_evidence_summary,
        },
        "slot_dispositions": sorted(slot_metrics, key=lambda item: str(item["slot_id"])),
        "attempt_journal": {
            "path": "register/attempts",
            "sha256": _sha256(journal_records),
            "development_dispositions": development_dispositions,
            "terminal_dispositions": terminal_dispositions,
        },
        "evaluation_identity": evaluation["metric_identity"],
        "diagnostics": {
            "families": evaluation["families"],
            "controls": evaluation.get("controls", {}),
            "coverage": evaluation.get("coverage", {}),
            "concentration": {
                family: evaluation["families"][family]["primary"].get("concentration", {})
                for family in evaluation.get("families", {})
            },
            "r3h_cost_grid": evaluation["r3h_cost_grid"],
        },
        "all_twenty_core_six_views_identity": _sha256(
            {"families": evaluation["families"], "core_six": evaluation.get("core_six", {})}
        ),
        "nominations": nomination_results,
        "nominated_candidates": list(nominated_candidates),
        "nomination_verdict": nomination_payload["verdict"],
        "r3h_cost_grid": evaluation["r3h_cost_grid"],
        "limitations": [
            "historical midpoint evidence is non-executable",
            "post-outcome scientific performance is computed as historical exploratory evidence",
            "no profitability, promotion, R5, native, or live-capital claim",
        ],
        "exploratory": True,
        "decision_grade": False,
    }
    report_payload["content_identity"] = _sha256(report_payload)
    _write_json_once(root / "result" / "final-exploratory-report.json", report_payload)
    return str(metric_rows["metric_identity"])


def _closure_report_count(root: Path, *, config: FrozenRuntimeConfig | None = None) -> int:
    return len(_closure_report_records(root, config=config))


def _closure_report_count_best_effort(root: Path) -> int:
    try:
        return _closure_report_count(root)
    except Exception:
        return -1


def _failure_status(exc: BaseException) -> str:
    """Classify only explicit implementation defects as invalidation."""
    return (
        "RUN_INVALIDATED_IMPLEMENTATION"
        if isinstance(exc, _ImplementationInvariantError)
        else "RUN_FAILED"
    )


_POST_OUTCOME_SCIENTIFIC_PERFORMANCE = "COMPUTED_POST_HOC_HISTORICAL_EXPLORATORY"


def _validate_post_outcome_artifact_labels(root: Path) -> None:
    """Require one authenticated post-outcome label across aggregate artifacts."""
    paths = (
        root / "metrics" / "terminal-metrics.json",
        root / "result" / "nomination.json",
        root / "result" / "final-exploratory-report.json",
    )
    for path in paths:
        payload = _read_json(path)
        if payload.get("scientific_performance") != _POST_OUTCOME_SCIENTIFIC_PERFORMANCE:
            raise ValueError(f"post-outcome scientific performance label is invalid: {path.name}")


@reference_process
def aggregate_authenticated_metrics(
    root: Path, config: FrozenRuntimeConfig | None, identity: G0ExecutionIdentity
) -> str:
    root = Path(root).resolve()
    runtime_config = config or FrozenRuntimeConfig()
    before = _closure_report_count_best_effort(root)
    try:
        result = _aggregate_authenticated_metrics_impl(root, config, identity)
        _validate_post_outcome_artifact_labels(root)
        return result
    except Exception as exc:
        after = _closure_report_count_best_effort(root)
        if after == before:
            try:
                write_outcome_blind_closure_report(
                    root,
                    status=_failure_status(exc),
                    reason=f"authenticated metrics failed: {exc}",
                    phase=(
                        "TERMINAL_POST_OUTCOME_EVALUATION"
                        if _aggregate_outcomes_loaded.get()
                        else "TERMINAL_PRE_OUTCOME_VALIDATION"
                    ),
                    identity=identity,
                    config=runtime_config,
                    outcomes_loaded=_aggregate_outcomes_loaded.get(),
                )
            except Exception as report_error:
                exc.add_note(f"closure report retention failed: {report_error}")
        raise
    finally:
        _aggregate_outcomes_loaded.set(False)


def _validate_closure_report_relative(report_relative: str, sequence: int) -> None:
    relative = Path(report_relative)
    parts = report_relative.split("/")
    if (
        len(parts) != 2
        or parts[0] != "result"
        or relative.parts != tuple(parts)
        or relative.name != parts[1]
        or not parts[1].startswith("closure-report-")
        or not parts[1].endswith(".json")
    ):
        raise ValueError("closure report path is not canonical")
    stem = parts[1][len("closure-report-") : -len(".json")]
    token_parts = stem.split("-")
    if (
        len(token_parts) != 2
        or token_parts[0] != f"{sequence:06d}"
        or len(token_parts[1]) != 64
        or any(character not in "0123456789abcdef" for character in token_parts[1])
    ):
        raise ValueError("closure report path sequence or token is not canonical")


def _canonical_closure_report_path(root: Path, report_relative: str, sequence: int) -> Path:
    _validate_closure_report_relative(report_relative, sequence)
    result_directory = root / "result"
    if result_directory.is_symlink() or not result_directory.is_dir():
        raise ValueError("closure report result directory is not canonical")
    candidate = result_directory / Path(report_relative).name
    if candidate.is_symlink():
        raise ValueError("closure report path is symlinked")
    resolved = candidate.resolve()
    if resolved.parent != result_directory.resolve():
        raise ValueError("closure report path escapes result directory")
    if not candidate.is_file():
        raise ValueError("closure report is missing or escapes output root")
    return resolved


_CLOSURE_REPORT_FIELDS = frozenset(
    {
        "artifact_type",
        "schema_version",
        "status",
        "reason",
        "exception",
        "phase",
        "closure_sequence",
        "experiment_class",
        "source_class",
        "evidence_class",
        "authority",
        "source",
        "provenance",
        "runtime_configuration",
        "register_identity",
        "capsule_identity",
        "support",
        "slot_dispositions",
        "attempt_ledgers",
        "diagnostics",
        "scientific_performance",
        "nominations",
        "limitations",
        "outcome_blind",
        "outcomes_loaded",
        "exploratory",
        "decision_grade",
        "report_path",
        "content_identity",
    }
)
_CLOSURE_G0_FIELDS = frozenset(
    {
        "code_head",
        "lock_identity",
        "application_identity",
        "runtime_identity",
        "config_identity",
        "register_identity",
        "output_root_identity",
        "release_identity",
        "parent_identity",
        "harness_identity",
        "identity",
    }
)
_CLOSURE_SNAPSHOT_FIELDS = frozenset(
    {
        "path",
        "sha256",
        "size",
        "line_count",
        "prefix_sha256",
        "byte_length",
        "parse_status",
        "lifecycle_status",
        "dispositions",
        "snapshot_identity",
    }
)
_CLOSURE_STATUSES = frozenset(
    {
        "HYPOTHESIS_NOMINATED",
        "NO_HYPOTHESIS_NOMINATED",
        "RUN_INVALIDATED_IMPLEMENTATION",
        "RUN_FAILED",
    }
)
_CLOSURE_LIFECYCLE_STATUSES = frozenset({"EMPTY", "OPEN", "PARTIAL_CLOSED", "COMPLETE"})
_CLOSURE_AUTHORITY = {
    "plan": "docs/R4_P0_EXECUTION_PLAN.md",
    "sections": ["3", "6.3", "9.4", "10.1", "10.2", "10.3", "10.4", "10.5", "11", "13", "15"],
    "promotion": "NONE",
    "native_or_executable_claim": False,
}


def _require_closure_sha256(value: object, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"closure report {field} is not a SHA-256 identity")
    return value


def _validate_closure_ledger_snapshot(
    snapshot: object,
    expected_path: str,
    *,
    root: Path | None = None,
    config: FrozenRuntimeConfig | None = None,
) -> None:
    if not isinstance(snapshot, dict):
        raise ValueError("closure report ledger snapshot is not an object")
    snapshot = cast(dict[str, Any], snapshot)
    required = set(_CLOSURE_SNAPSHOT_FIELDS)
    for optional in ("observation_errors",):
        if optional in snapshot:
            required.add(optional)
    parse_status = snapshot.get("parse_status")
    if parse_status == "VALID":
        required.add("records_identity")
        if "parse_error" in snapshot or "observation_errors" in snapshot:
            raise ValueError("valid closure report ledger has failure metadata")
    elif parse_status == "INVALID":
        required.add("parse_error")
    else:
        raise ValueError("closure report ledger parse status is invalid")
    if set(snapshot) != required:
        raise ValueError("closure report ledger snapshot schema is not canonical")
    if snapshot["path"] != expected_path:
        raise ValueError("closure report ledger path drifted")
    sha256 = _require_closure_sha256(snapshot["sha256"], "ledger sha256", optional=True)
    prefix_sha256 = _require_closure_sha256(
        snapshot["prefix_sha256"], "ledger prefix sha256", optional=True
    )
    for field in ("size", "line_count", "byte_length"):
        value = snapshot[field]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"closure report ledger {field} is invalid")
    if (sha256 is None) != (prefix_sha256 is None):
        raise ValueError("closure report ledger prefix digest metadata is contradictory")
    if (snapshot["size"] is None) != (snapshot["byte_length"] is None):
        raise ValueError("closure report ledger prefix length metadata is contradictory")
    if sha256 is not None and prefix_sha256 != sha256:
        raise ValueError("closure report ledger prefix digest metadata drifted")
    if snapshot["size"] is not None and snapshot["byte_length"] != snapshot["size"]:
        raise ValueError("closure report ledger prefix length metadata drifted")
    if parse_status == "VALID" and (
        sha256 is None
        or prefix_sha256 is None
        or snapshot["size"] is None
        or snapshot["byte_length"] is None
        or snapshot["line_count"] is None
    ):
        raise ValueError("valid closure report ledger lacks observed byte metadata")
    lifecycle_status = snapshot["lifecycle_status"]
    dispositions = snapshot["dispositions"]
    if not isinstance(dispositions, list):
        raise ValueError("closure report ledger dispositions are invalid")
    slot_ids: list[str] = []
    for disposition_value in dispositions:
        if not isinstance(disposition_value, dict) or set(disposition_value) != {
            "slot_id",
            "mode",
            "stage",
            "attempts",
            "final_status",
            "disposition",
        }:
            raise ValueError("closure report ledger disposition schema is invalid")
        disposition = cast(dict[str, Any], disposition_value)
        if not all(isinstance(disposition[field], str) for field in ("slot_id", "mode", "stage")):
            raise ValueError("closure report ledger disposition identity is invalid")
        slot_ids.append(disposition["slot_id"])
        if disposition["final_status"] not in {"STARTED", "SUCCEEDED", "FAILED"}:
            raise ValueError("closure report ledger disposition status is invalid")
        expected_disposition = {
            "STARTED": "OPEN",
            "SUCCEEDED": "SUCCEEDED",
            "FAILED": "FAILED",
        }[disposition["final_status"]]
        if disposition["disposition"] != expected_disposition:
            raise ValueError("closure report ledger disposition status is contradictory")
        raw_attempts = disposition["attempts"]
        if not isinstance(raw_attempts, list) or not raw_attempts:
            raise ValueError("closure report ledger attempts are invalid")
        attempts: list[dict[str, Any]] = []
        for raw_attempt in raw_attempts:
            if not isinstance(raw_attempt, dict) or set(raw_attempt) != {
                "attempt",
                "status",
                "error",
            }:
                raise ValueError("closure report ledger attempt schema is invalid")
            attempt = cast(dict[str, Any], raw_attempt)
            if (
                isinstance(attempt["attempt"], bool)
                or not isinstance(attempt["attempt"], int)
                or attempt["attempt"] < 0
                or attempt["status"] not in {"STARTED", "SUCCEEDED", "FAILED"}
                or (attempt["error"] is not None and not isinstance(attempt["error"], str))
            ):
                raise ValueError("closure report ledger attempt is invalid")
            attempts.append(attempt)
        numbers = [attempt["attempt"] for attempt in attempts]
        if numbers and (
            numbers != sorted(numbers) or sorted(set(numbers)) != list(range(max(numbers) + 1))
        ):
            raise ValueError("closure report ledger attempt order is not canonical")
        grouped_attempts: list[list[dict[str, Any]]] = []
        for number in sorted(set(numbers)):
            grouped_attempts.append(
                [attempt for attempt in attempts if attempt["attempt"] == number]
            )
        for position, grouped in enumerate(grouped_attempts):
            statuses = [attempt["status"] for attempt in grouped]
            if statuses not in (["STARTED"], ["STARTED", "SUCCEEDED"], ["STARTED", "FAILED"]):
                raise ValueError("closure report ledger attempt lifecycle is invalid")
            if position < len(grouped_attempts) - 1 and statuses != ["STARTED", "FAILED"]:
                raise ValueError("closure report ledger retry lifecycle is invalid")
            if position > 0 and grouped_attempts[position - 1][-1]["status"] != "FAILED":
                raise ValueError("closure report ledger retry lifecycle is invalid")
            for attempt in grouped:
                status = attempt["status"]
                if status in {"STARTED", "SUCCEEDED"} and attempt["error"] is not None:
                    raise ValueError("closure report ledger attempt error is contradictory")
                if status == "FAILED" and (
                    not isinstance(attempt["error"], str) or not attempt["error"]
                ):
                    raise ValueError("closure report ledger failed attempt lacks error")
    if slot_ids != sorted(set(slot_ids)):
        raise ValueError("closure report ledger disposition order is not canonical")
    if parse_status == "INVALID":
        if lifecycle_status != "INVALID" or dispositions:
            raise ValueError("invalid closure report ledger exposes lifecycle data")
        parse_error = snapshot["parse_error"]
        if not isinstance(parse_error, dict) or set(parse_error) != {"classification", "context"}:
            raise ValueError("closure report ledger parse error is invalid")
        if (
            not all(
                isinstance(parse_error[field], str) and parse_error[field]
                for field in ("classification", "context")
            )
            or _sanitise_failure_reason(parse_error["context"]) != parse_error["context"]
        ):
            raise ValueError("closure report ledger parse error is invalid")
        classification = parse_error["classification"]
        if "observation_errors" in snapshot:
            observation_errors = snapshot["observation_errors"]
            if not isinstance(observation_errors, list) or not observation_errors:
                raise ValueError("closure report ledger observation errors are invalid")
            for observation_error in observation_errors:
                if (
                    not isinstance(observation_error, dict)
                    or set(observation_error) != {"classification", "context"}
                    or not all(
                        isinstance(observation_error[field], str) and observation_error[field]
                        for field in ("classification", "context")
                    )
                    or _sanitise_failure_reason(observation_error["context"])
                    != observation_error["context"]
                ):
                    raise ValueError("closure report ledger observation errors are invalid")
            if classification != "UNAVAILABLE_LEDGER":
                raise ValueError("observation errors require unavailable ledger")
        if classification in {"MISSING_LEDGER", "UNAVAILABLE_LEDGER"}:
            if (
                sha256 is not None
                or prefix_sha256 is not None
                or snapshot["size"] is not None
                or snapshot["byte_length"] is not None
                or snapshot["line_count"] is not None
            ):
                raise ValueError("unavailable closure report ledger has observed metadata")
        elif (
            sha256 is None
            or prefix_sha256 is None
            or snapshot["size"] is None
            or snapshot["byte_length"] is None
            or snapshot["line_count"] is None
        ):
            raise ValueError("invalid closure report ledger is missing observed metadata")
    else:
        if sha256 is None or snapshot["size"] is None or snapshot["line_count"] is None:
            raise ValueError("valid closure report ledger is missing observed metadata")
        records_identity = _require_closure_sha256(snapshot["records_identity"], "records identity")
        if lifecycle_status not in _CLOSURE_LIFECYCLE_STATUSES:
            raise ValueError("closure report ledger lifecycle status is invalid")
        for disposition in dispositions:
            terminal_attempts = [
                attempt["status"]
                for attempt in disposition["attempts"]
                if attempt["status"] in {"SUCCEEDED", "FAILED"}
            ]
            expected_final = terminal_attempts[-1] if terminal_attempts else "STARTED"
            if disposition["final_status"] != expected_final:
                raise ValueError("closure report ledger final status is contradictory")
        expected_lifecycle = _closure_lifecycle_from_dispositions(expected_path, dispositions)
        if lifecycle_status != expected_lifecycle:
            raise ValueError("closure report ledger lifecycle status is contradictory")
        if lifecycle_status == "EMPTY":
            if dispositions or records_identity != _sha256([]):
                raise ValueError("empty closure report ledger has dispositions")
        elif lifecycle_status == "OPEN":
            if not any(item["final_status"] == "STARTED" for item in dispositions):
                raise ValueError("open closure report ledger has no open disposition")
        elif not dispositions or any(item["final_status"] == "STARTED" for item in dispositions):
            raise ValueError("closed closure report ledger has open disposition")
        if lifecycle_status == "COMPLETE":
            expected_slots = (
                {f"{family}:{seed}:{stage}": stage for family, seed, stage in _attempt_schedule()}
                if expected_path == "register/attempts"
                else {}
            )
            if not expected_slots or {item["slot_id"] for item in dispositions} != set(
                expected_slots
            ):
                raise ValueError("complete closure report ledger slot set is not canonical")
            if any(
                item["mode"] != "PRIMARY" or item["stage"] != expected_slots[item["slot_id"]]
                for item in dispositions
            ):
                raise ValueError("complete closure report ledger slots are not canonical")
    if "observation_errors" in snapshot:
        errors = snapshot["observation_errors"]
        if not isinstance(errors, list):
            raise ValueError("closure report ledger observation errors are invalid")
        for error in errors:
            if not isinstance(error, dict) or set(error) != {"classification", "context"}:
                raise ValueError("closure report ledger observation error is invalid")
            if (
                not all(
                    isinstance(error[field], str) and error[field]
                    for field in ("classification", "context")
                )
                or _sanitise_failure_reason(error["context"]) != error["context"]
            ):
                raise ValueError("closure report ledger observation error is invalid")
    if root is not None and parse_status == "VALID" and expected_path == "register/attempts":
        observed_record_keys = {
            (disposition["slot_id"], attempt["attempt"], attempt["status"])
            for disposition in dispositions
            for attempt in disposition["attempts"]
        }
        current = _closure_create_only_journal_snapshot(
            root, observed_record_keys=observed_record_keys
        )
        for field in (
            "sha256",
            "size",
            "line_count",
            "prefix_sha256",
            "byte_length",
            "records_identity",
            "dispositions",
            "lifecycle_status",
        ):
            if current[field] != snapshot[field]:
                raise ValueError(f"closure report create-only journal {field} drifted")
        identity = snapshot["snapshot_identity"]
        _require_closure_sha256(identity, "snapshot identity")
        if identity != _sha256(
            {key: value for key, value in snapshot.items() if key != "snapshot_identity"}
        ):
            raise ValueError("closure report ledger snapshot identity drifted")
        return
    if root is not None and parse_status == "VALID":
        raise ValueError("closure reports accept only the create-only attempt journal")
    identity = snapshot["snapshot_identity"]
    _require_closure_sha256(identity, "snapshot identity")
    if identity != _sha256(
        {key: value for key, value in snapshot.items() if key != "snapshot_identity"}
    ):
        raise ValueError("closure report ledger snapshot identity drifted")


def _validate_closure_report_payload(
    root: Path,
    report_relative: str,
    report: object,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> None:
    if not isinstance(report, dict) or set(report) != _CLOSURE_REPORT_FIELDS:
        raise ValueError("closure report schema is not canonical")
    report = cast(dict[str, Any], report)
    if report["artifact_type"] != "R4.C_FINAL_EXPLORATORY_REPORT":
        raise ValueError("closure report artifact type is not canonical")
    if report["schema_version"] != "R4.C-1":
        raise ValueError("closure report schema version is not canonical")
    status = report["status"]
    if status not in _CLOSURE_STATUSES:
        raise ValueError("closure report status is not canonical")
    reason = report["reason"]
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or _sanitise_failure_reason(reason) != reason
    ):
        raise ValueError("closure report reason is not canonical")
    exception = report["exception"]
    if not isinstance(exception, dict) or set(exception) != {"classification", "context"}:
        raise ValueError("closure report exception is not canonical")
    if exception["classification"] != status or exception["context"] != reason:
        raise ValueError("closure report exception is contradictory")
    if (
        not isinstance(report["phase"], str)
        or not report["phase"].strip()
        or _sanitise_failure_reason(report["phase"]) != report["phase"]
    ):
        raise ValueError("closure report phase is not canonical")
    sequence = report["closure_sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise ValueError("closure report sequence is not canonical")
    if report["experiment_class"] != EXPERIMENT_CLASS or report["source_class"] != SOURCE_CLASS:
        raise ValueError("closure report classes are not canonical")
    if report["evidence_class"] != "POST_HOC_HISTORICAL_EXPLORATORY":
        raise ValueError("closure report evidence class is not canonical")
    if report["authority"] != _CLOSURE_AUTHORITY:
        raise ValueError("closure report authority is not canonical")
    runtime_configuration = report["runtime_configuration"]
    if not isinstance(runtime_configuration, dict):
        raise ValueError("closure report runtime configuration is not canonical")
    expected_config = config or FrozenRuntimeConfig()
    if (
        _canonical(runtime_configuration) != _canonical(expected_config.to_dict())
        or _sha256(runtime_configuration) != expected_config.identity
    ):
        raise ValueError("closure report runtime configuration drifted")
    config_identity = _sha256(runtime_configuration)
    source = report["source"]
    if not isinstance(source, dict) or set(source) != {
        "parent_identity",
        "runtime_identity",
        "r3h_cost_grid",
    }:
        raise ValueError("closure report source is not canonical")
    provenance = report["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {
        "g0_identity",
        "config_identity",
        "output_root_identity",
        "register_identity",
        "capsule_identity",
    }:
        raise ValueError("closure report provenance is not canonical")
    g0 = provenance["g0_identity"]
    if g0 is None:
        g0_identity = None
    else:
        if not isinstance(g0, dict) or set(g0) != _CLOSURE_G0_FIELDS:
            raise ValueError("closure report G0 identity is not canonical")
        if (
            not isinstance(g0["code_head"], str)
            or len(g0["code_head"]) != 40
            or any(character not in "0123456789abcdef" for character in g0["code_head"])
        ):
            raise ValueError("closure report G0 code head is not canonical")
        for field in (
            "lock_identity",
            "application_identity",
            "runtime_identity",
            "config_identity",
            "register_identity",
            "output_root_identity",
        ):
            _require_closure_sha256(g0[field], f"G0 {field}")
        if g0["release_identity"] != RUNTIME_VERSION or g0["parent_identity"] != PARENT_IDENTITY:
            raise ValueError("closure report G0 release or parent identity drifted")
        if g0["harness_identity"] != _EXECUTION_VERSION:
            raise ValueError("closure report G0 harness identity drifted")
        g0_identity = g0["identity"]
        _require_closure_sha256(g0_identity, "G0 identity")
    expected_output = _sha256({"root": str(root.resolve()), "policy": OUTPUT_POLICY})
    if g0 is not None:
        if g0_identity != _sha256({key: value for key, value in g0.items() if key != "identity"}):
            raise ValueError("closure report G0 identity is invalid")
        if g0["output_root_identity"] != expected_output:
            raise ValueError("closure report output root identity drifted")
    if provenance["output_root_identity"] != expected_output:
        raise ValueError("closure report provenance output identity drifted")
    if provenance["config_identity"] != config_identity:
        raise ValueError("closure report configuration identity drifted")
    _require_closure_sha256(provenance["config_identity"], "configuration identity")
    if g0 is not None and (
        g0["runtime_identity"] != config_identity or g0["config_identity"] != config_identity
    ):
        raise ValueError("closure report G0 configuration identity drifted")
    if (
        runtime_configuration.get("source_class") != report["source_class"]
        or runtime_configuration.get("experiment_class") != report["experiment_class"]
    ):
        raise ValueError("closure report runtime classes drifted")
    if (g0 is not None and source["parent_identity"] != g0["parent_identity"]) or source[
        "runtime_identity"
    ] != config_identity:
        raise ValueError("closure report source identities drifted")
    from .evaluation import historical_r3h_cost_grid

    if source["r3h_cost_grid"] != historical_r3h_cost_grid():
        raise ValueError("closure report source grid drifted")
    for field in ("register_identity", "capsule_identity"):
        _require_closure_sha256(provenance[field], f"provenance {field}", optional=True)
        if provenance[field] != report[field]:
            raise ValueError(f"closure report {field} drifted")
    outcomes_loaded = report["outcomes_loaded"]
    if not isinstance(outcomes_loaded, bool) or report["outcome_blind"] is not (
        not outcomes_loaded
    ):
        raise ValueError("closure report outcome state is not canonical")
    support = report["support"]
    if not isinstance(support, dict) or set(support) != {
        "status",
        "outcome_blind",
        "outcomes_loaded",
    }:
        raise ValueError("closure report support schema is not canonical")
    if support["status"] != "NOT_MATERIALISED" or support["outcomes_loaded"] is not outcomes_loaded:
        raise ValueError("closure report support outcome state drifted")
    if support["outcome_blind"] is not (not outcomes_loaded):
        raise ValueError("closure report support blindness drifted")
    attempt_ledgers = report["attempt_ledgers"]
    if not isinstance(attempt_ledgers, dict) or set(attempt_ledgers) != {"development", "terminal"}:
        raise ValueError("closure report attempt ledger schema is not canonical")
    _validate_closure_ledger_snapshot(
        attempt_ledgers["development"],
        "register/attempts",
        root=root,
        config=expected_config,
    )
    _validate_closure_ledger_snapshot(
        attempt_ledgers["terminal"],
        "register/attempts",
        root=root,
        config=expected_config,
    )
    slot_dispositions = report["slot_dispositions"]
    if not isinstance(slot_dispositions, dict) or set(slot_dispositions) != {
        "development",
        "terminal",
    }:
        raise ValueError("closure report slot disposition schema is not canonical")
    if (
        slot_dispositions["development"] != attempt_ledgers["development"]["dispositions"]
        or slot_dispositions["terminal"] != attempt_ledgers["terminal"]["dispositions"]
    ):
        raise ValueError("closure report slot dispositions drifted")
    if report["diagnostics"] != {"scientific_performance": "NOT_COMPUTED"}:
        raise ValueError("closure report diagnostics are not canonical")
    if report["scientific_performance"] != "NOT_COMPUTED":
        raise ValueError("closure report scientific performance is not canonical")
    nominations = report["nominations"]
    expected_verdict = (
        status if status in {"HYPOTHESIS_NOMINATED", "NO_HYPOTHESIS_NOMINATED"} else "NOT_EVALUATED"
    )
    if nominations != {"verdict": expected_verdict, "promotion_authority": "NONE"}:
        raise ValueError("closure report nomination state is not canonical")
    expected_limitations = [
        "terminal outcomes were not loaded"
        if not outcomes_loaded
        else "terminal outcomes were loaded before the post-outcome failure",
        "scientific_performance=NOT_COMPUTED",
        "no profitability, promotion, R5, native, or live-capital claim",
    ]
    if report["limitations"] != expected_limitations:
        raise ValueError("closure report limitations are not canonical")
    if report["exploratory"] is not True or report["decision_grade"] is not False:
        raise ValueError("closure report decision labels are not canonical")
    if report["report_path"] != report_relative:
        raise ValueError("closure report path binding drifted")
    unsigned = dict(report)
    content_identity = unsigned.pop("content_identity")
    _require_closure_sha256(content_identity, "content identity")
    if content_identity != _sha256(unsigned):
        raise ValueError("closure report content identity drifted")
    _validate_closure_report_relative(report_relative, sequence)
    report_name = Path(report_relative).name
    token = report_name[len("closure-report-") : -len(".json")].split("-")[1]
    expected_token = _sha256(
        {
            "g0_identity": g0_identity,
            "sequence": sequence,
            "status": status,
            "phase": report["phase"],
            "reason": reason,
        }
    )
    if token != expected_token:
        raise ValueError("closure report path token drifted")


def _closure_report_records(
    root: Path,
    *,
    config: FrozenRuntimeConfig | None = None,
) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    result_directory = root / "result"
    if result_directory.is_symlink() or (
        result_directory.exists() and not result_directory.is_dir()
    ):
        raise ValueError("closure report result directory is not canonical")
    if not result_directory.is_dir():
        return []

    reports: list[tuple[int, str, Path]] = []
    for report_path in result_directory.glob("closure-report-*.json"):
        if report_path.is_symlink() or not report_path.is_file():
            raise ValueError("closure report is not a regular file")
        name = report_path.name
        stem = name[len("closure-report-") : -len(".json")]
        sequence_text = stem.split("-", 1)[0]
        if len(sequence_text) != 6 or not sequence_text.isdigit():
            raise ValueError("closure report sequence is not canonical")
        sequence = int(sequence_text)
        relative = report_path.relative_to(root).as_posix()
        _canonical_closure_report_path(root, relative, sequence)
        reports.append((sequence, relative, report_path))

    reports.sort(key=lambda item: item[0])
    if [sequence for sequence, _, _ in reports] != list(range(1, len(reports) + 1)):
        raise ValueError("closure report sequence is not contiguous")

    records: list[dict[str, Any]] = []
    for sequence, relative, report_path in reports:
        report = _read_json(report_path)
        _validate_closure_report_payload(root, relative, report, config=config)
        if report["closure_sequence"] != sequence:
            raise ValueError("closure report sequence does not match filename")
        records.append(report)
    return records


def _sanitise_failure_reason(reason: str) -> str:
    return " ".join(reason.split())[:1000]


def _closure_lifecycle_from_dispositions(
    relative: str, dispositions: Sequence[Mapping[str, Any]]
) -> str:
    if not dispositions:
        return "EMPTY"
    if any(item["final_status"] == "STARTED" for item in dispositions):
        return "OPEN"
    expected = (
        {f"{family}:{seed}:{stage}": stage for family, seed, stage in _attempt_schedule()}
        if relative == "register/attempts"
        else {}
    )
    canonical = (
        bool(expected)
        and {str(item["slot_id"]) for item in dispositions} == set(expected)
        and all(
            item["mode"] == "PRIMARY"
            and item["stage"] == expected[str(item["slot_id"])]
            and item["final_status"] in {"SUCCEEDED", "FAILED"}
            for item in dispositions
        )
    )
    return "COMPLETE" if canonical else "PARTIAL_CLOSED"


def _closure_lifecycle_status(
    relative: str,
    records: Sequence[Mapping[str, Any]],
    dispositions: Sequence[Mapping[str, Any]],
    *,
    has_open_attempts: bool,
) -> str:
    if not records:
        return "EMPTY"
    if has_open_attempts:
        return "OPEN"
    lifecycle = _closure_lifecycle_from_dispositions(relative, dispositions)
    if lifecycle != "COMPLETE":
        return lifecycle
    expected = (
        {f"{family}:{seed}:{stage}": stage for family, seed, stage in _attempt_schedule()}
        if relative == "register/attempts"
        else {}
    )
    return (
        "COMPLETE"
        if expected
        and all(
            record["mode"] == "PRIMARY" and str(record["slot_id"]) in expected for record in records
        )
        else "PARTIAL_CLOSED"
    )


def _closure_create_only_journal_snapshot(
    root: Path, *, observed_record_keys: set[tuple[str, int, str]] | None = None
) -> dict[str, Any]:
    journal = CreateOnlyAttemptJournal(root)
    journal_records = journal.ordered_records(_attempt_schedule())
    if observed_record_keys is not None:
        # Historical reports bind their observed immutable records, not later appends.
        journal_records = tuple(
            record
            for record in journal_records
            if (record["attempt"]["slot_id"], record["attempt"]["attempt"], record["status"])
            in observed_record_keys
        )
    records: list[dict[str, Any]] = []
    for record in journal_records:
        attempt = cast(Mapping[str, Any], record["attempt"])
        payload = cast(Mapping[str, Any], record["payload"])
        records.append({**attempt, "status": record["status"], "error": payload.get("error")})
    dispositions = _ledger_dispositions(records)
    identity = _sha256(journal_records)
    byte_length = sum(
        journal._record_path(record["attempt_id"], record["status"]).stat().st_size
        for record in journal_records
    )
    snapshot: dict[str, Any] = {
        "path": "register/attempts",
        "sha256": identity,
        "size": byte_length,
        "line_count": len(journal_records),
        "prefix_sha256": identity,
        "byte_length": byte_length,
        "parse_status": "VALID",
        "lifecycle_status": _closure_lifecycle_status(
            "register/attempts",
            records,
            dispositions,
            has_open_attempts=any(item["disposition"] == "OPEN" for item in dispositions),
        ),
        "records_identity": _sha256(records),
        "dispositions": dispositions,
    }
    snapshot["snapshot_identity"] = _sha256(snapshot)
    return snapshot


def _closure_ledger_snapshot(
    root: Path, relative: str, *, config: FrozenRuntimeConfig
) -> dict[str, Any]:
    del config
    if relative != "register/attempts":
        raise ValueError("closure reports accept only the create-only attempt journal")
    return _closure_create_only_journal_snapshot(root)


def write_outcome_blind_closure_report(
    root: Path,
    *,
    status: str,
    reason: str,
    identity: G0ExecutionIdentity | None = None,
    config: FrozenRuntimeConfig | None = None,
    phase: str = "UNKNOWN",
    outcomes_loaded: bool = False,
) -> str:
    """Persist one authenticated create-only closure report."""
    if status not in _CLOSURE_STATUSES:
        raise ValueError(f"unsupported outcome-blind closure status: {status}")
    if not reason.strip():
        raise ValueError("outcome-blind closure requires a reason")
    if not isinstance(phase, str) or not phase.strip() or _sanitise_failure_reason(phase) != phase:
        raise ValueError("outcome-blind closure phase is not canonical")
    if not isinstance(outcomes_loaded, bool):
        raise ValueError("outcome-blind closure outcome state is not canonical")
    root = Path(root).resolve()
    runtime_config = config or FrozenRuntimeConfig()
    current_identity = identity
    if current_identity is None:
        try:
            current_identity = _require_identity(root, runtime_config)
        except Exception:
            current_identity = None
    existing = _closure_report_records(root, config=runtime_config)
    sequence = len(existing) + 1
    identity_payload = current_identity.to_dict() if current_identity is not None else None
    output_root_identity = _sha256({"root": str(root.resolve()), "policy": OUTPUT_POLICY})
    journal_snapshot = _closure_ledger_snapshot(root, "register/attempts", config=runtime_config)
    ledgers: dict[str, Any] = {
        "development": journal_snapshot,
        "terminal": journal_snapshot,
    }
    register_identity = None
    register_path = root / "register" / "development-register.json"
    if register_path.is_file():
        try:
            register_identity = _read_json(register_path).get("register_identity")
        except Exception:
            register_identity = None
    capsule_identity = None
    capsule_path = root / "support" / "terminal-support-canonical.json"
    if capsule_path.is_file():
        try:
            capsule_identity = _read_json(capsule_path).get("artifact_identity")
        except Exception:
            capsule_identity = None
    from .evaluation import historical_r3h_cost_grid

    clean_reason = _sanitise_failure_reason(reason)
    token = _sha256(
        {
            "g0_identity": current_identity.identity if current_identity is not None else None,
            "sequence": sequence,
            "status": status,
            "phase": phase,
            "reason": clean_reason,
        }
    )
    report_relative = f"result/closure-report-{sequence:06d}-{token}.json"
    payload: dict[str, Any] = {
        "artifact_type": "R4.C_FINAL_EXPLORATORY_REPORT",
        "schema_version": "R4.C-1",
        "status": status,
        "reason": clean_reason,
        "exception": {
            "classification": status,
            "context": clean_reason,
        },
        "phase": phase,
        "closure_sequence": sequence,
        "experiment_class": runtime_config.experiment_class,
        "source_class": runtime_config.source_class,
        "evidence_class": "POST_HOC_HISTORICAL_EXPLORATORY",
        "authority": {
            "plan": "docs/R4_P0_EXECUTION_PLAN.md",
            "sections": [
                "3",
                "6.3",
                "9.4",
                "10.1",
                "10.2",
                "10.3",
                "10.4",
                "10.5",
                "11",
                "13",
                "15",
            ],
            "promotion": "NONE",
            "native_or_executable_claim": False,
        },
        "source": {
            "parent_identity": (
                identity_payload.get("parent_identity", identity_payload["identity"])
                if identity_payload is not None
                else PARENT_IDENTITY
            ),
            "runtime_identity": runtime_config.identity,
            "r3h_cost_grid": historical_r3h_cost_grid(),
        },
        "provenance": {
            "g0_identity": identity_payload,
            "config_identity": runtime_config.identity,
            "output_root_identity": output_root_identity,
            "register_identity": register_identity,
            "capsule_identity": capsule_identity,
        },
        "runtime_configuration": runtime_config.to_dict(),
        "register_identity": register_identity,
        "capsule_identity": capsule_identity,
        "support": {
            "status": "NOT_MATERIALISED",
            "outcome_blind": not outcomes_loaded,
            "outcomes_loaded": outcomes_loaded,
        },
        "slot_dispositions": {
            "development": ledgers["development"]["dispositions"],
            "terminal": ledgers["terminal"]["dispositions"],
        },
        "attempt_ledgers": ledgers,
        "diagnostics": {"scientific_performance": "NOT_COMPUTED"},
        "scientific_performance": "NOT_COMPUTED",
        "nominations": {
            "verdict": (
                status
                if status in {"HYPOTHESIS_NOMINATED", "NO_HYPOTHESIS_NOMINATED"}
                else "NOT_EVALUATED"
            ),
            "promotion_authority": "NONE",
        },
        "limitations": [
            (
                "terminal outcomes were not loaded"
                if not outcomes_loaded
                else "terminal outcomes were loaded before the post-outcome failure"
            ),
            "scientific_performance=NOT_COMPUTED",
            "no profitability, promotion, R5, native, or live-capital claim",
        ],
        "outcome_blind": not outcomes_loaded,
        "outcomes_loaded": outcomes_loaded,
        "exploratory": True,
        "decision_grade": False,
        "report_path": report_relative,
    }
    payload["content_identity"] = _sha256(payload)
    report_relative = str(payload["report_path"])
    _validate_closure_report_payload(root, report_relative, payload, config=runtime_config)
    report_path = root / report_relative
    result_directory = root / "result"
    if result_directory.is_symlink() or (
        result_directory.exists() and not result_directory.is_dir()
    ):
        raise ValueError("closure report result directory is not canonical")
    if report_path.is_symlink() or report_path.exists():
        raise FileExistsError(report_path)
    _write_json_once(report_path, payload)
    try:
        committed = _closure_report_records(root, config=runtime_config)
        if (
            len(committed) != sequence
            or committed[-1]["content_identity"] != payload["content_identity"]
        ):
            raise ValueError("closure report readback validation is incomplete")
    except Exception as exc:
        raise RuntimeError("closure report readback validation failed") from exc
    return str(payload["content_identity"])


def _terminal_primary_graph_identities(
    graph_identity_by_slot: Mapping[str, str | None],
) -> dict[str, str | None]:
    """Attribute primary graphs after authenticating each seed's persisted model."""
    by_family: dict[str, dict[int, str | None]] = {}
    for slot_id, identity in graph_identity_by_slot.items():
        family, seed, _ = slot_id.split(":", 2)
        by_family.setdefault(family, {})[int(seed)] = identity
    primary: dict[str, str | None] = {}
    for family, seeds in by_family.items():
        if family != "LEARNED_STATIC_GRAPH_RESIDUAL" and len(set(seeds.values())) != 1:
            raise ValueError(f"terminal graph identity drifted for frozen family {family}")
        primary[family] = seeds[min(PRIMARY_SEEDS)]
    return primary


if __name__ == "__main__":
    raise SystemExit(main())
