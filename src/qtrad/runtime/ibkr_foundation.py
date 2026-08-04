"""Immutable runtime persistence and replay for the IBKR historical foundation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qtrad.application.ibkr_foundation import (
    IBKRFoundationBuild,
    build_ibkr_foundation,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.foundation import FoundationConfig
from qtrad.domain.ibkr_foundation import (
    IBKR_FOUNDATION_CONTRACT,
    IBKR_FOUNDATION_SCHEMA_VERSION,
)
from qtrad.runtime.foundation_bundle import decode_foundation_config
from qtrad.runtime.provider_history import read_provider_history_observations


def foundation_config_payload(configuration: FoundationConfig) -> dict[str, JsonValue]:
    """Encode the strict configuration child used by the source-specific bundle."""

    return {
        "contract": FoundationConfig.CONTRACT,
        "name": configuration.name,
        "schema_version": configuration.schema_version,
        "observation_dataset_id": configuration.observation_dataset_id,
        "ordered_instruments": list(configuration.ordered_instruments),
        "instrument_roles": {
            key: value.value for key, value in sorted(configuration.instrument_roles.items())
        },
        "range_start": configuration.range_start.isoformat(),
        "range_end": configuration.range_end.isoformat(),
        "grid_resolution_seconds": int(configuration.grid_resolution.total_seconds()),
        "availability_basis": configuration.availability_basis.value,
        "feature_lag_policy": configuration.feature_lag_policy,
        "feature_lag_calibration_range": [
            value.isoformat() for value in configuration.feature_lag_calibration_range
        ],
        "feature_lag_percentile": configuration.feature_lag_percentile,
        "feature_lag_safety_margin_seconds": int(
            configuration.feature_lag_safety_margin.total_seconds()
        ),
        "selected_feature_lag_seconds": int(configuration.selected_feature_lag.total_seconds()),
        "target_horizons_seconds": [
            int(value.total_seconds()) for value in configuration.target_horizons
        ],
        "primary_vertical_horizon_seconds": int(
            configuration.primary_vertical_horizon.total_seconds()
        ),
        "target_revision_delay_seconds": int(configuration.target_revision_delay.total_seconds()),
        "target_revision_policy": configuration.target_revision_policy,
        "target_revision_policy_reason": configuration.target_revision_policy_reason,
        "required_feature_bases": [value.value for value in configuration.required_feature_bases],
        "target_basis": configuration.target_basis.value,
        "fold_policy": configuration.fold_policy,
        "holdout_range": [value.isoformat() for value in configuration.holdout_range],
        "embargo_seconds": int(configuration.embargo.total_seconds()),
        "minimum_training_duration_seconds": int(
            configuration.minimum_training_duration.total_seconds()
        ),
        "minimum_validation_duration_seconds": int(
            configuration.minimum_validation_duration.total_seconds()
        ),
    }


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _build_payload(build: IBKRFoundationBuild) -> dict[str, JsonValue]:
    return {
        "configuration": foundation_config_payload(build.configuration),
        "provider_history": {
            "dataset_sha256": build.provider_history.dataset_sha256,
            "row_count": build.provider_history.row_count,
        },
        "observations": [row.as_json() for row in build.observations.rows],
        "panel": [row.as_json() for row in build.panel.rows],
        "targets": [row.as_json() for row in build.targets.rows],
        "folds": [row.as_json() for row in build.folds.folds],
        "active_intervals": {
            instrument: [[start.isoformat(), end.isoformat()] for start, end in intervals]
            for instrument, intervals in sorted(build.active_intervals.items())
        },
        "provider_gaps": [dict(gap) for gap in build.provider_gaps],
        "readiness": build.readiness.as_json(),
    }


def _manifest_payload(build: IBKRFoundationBuild, provider_manifest: Path) -> dict[str, JsonValue]:
    payload = _build_payload(build)
    return {
        "contract": IBKR_FOUNDATION_CONTRACT,
        "schema_version": IBKR_FOUNDATION_SCHEMA_VERSION,
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "provider_history_manifest": str(provider_manifest.resolve()),
        "provider_history_sha256": hashlib.sha256(provider_manifest.read_bytes()).hexdigest(),
        "build_sha256": _sha(payload),
        "payload": payload,
    }


def write_ibkr_foundation(
    output: Path,
    *,
    provider_manifest: Path,
    configuration: FoundationConfig,
) -> IBKRFoundationBuild:
    """Build and create the source-specific bundle once."""

    provider_dataset, provider_rows = read_provider_history_observations(provider_manifest)
    build = build_ibkr_foundation(provider_dataset, provider_rows, configuration)
    if build.provider_history.dataset_sha256 != provider_dataset.dataset_sha256:
        raise ValueError("provider history changed during foundation construction")
    document = _manifest_payload(build, provider_manifest)
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"IBKR foundation output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        )
    return build


def verify_ibkr_foundation(path: Path) -> IBKRFoundationBuild:
    """Verify source closure and independently replay every foundation child."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("IBKR foundation bundle must be a regular non-symlink file")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("IBKR foundation bundle must be an object")
    required = {
        "contract",
        "schema_version",
        "source_class",
        "provider_history_manifest",
        "provider_history_sha256",
        "build_sha256",
        "payload",
    }
    if set(document) != required:
        raise ValueError("IBKR foundation bundle has unknown or missing fields")
    if document["contract"] != IBKR_FOUNDATION_CONTRACT:
        raise ValueError("IBKR foundation bundle contract is unsupported")
    if document["schema_version"] != IBKR_FOUNDATION_SCHEMA_VERSION:
        raise ValueError("IBKR foundation bundle schema is unsupported")
    if document["source_class"] != "IBKR_HISTORICAL_RESEARCH":
        raise ValueError("IBKR foundation bundle source class is unsupported")
    provider_path = Path(str(document["provider_history_manifest"]))
    provider_dataset, provider_rows = read_provider_history_observations(provider_path)
    if (
        hashlib.sha256(provider_path.read_bytes()).hexdigest()
        != document["provider_history_sha256"]
    ):
        raise ValueError("provider-history manifest bytes changed")
    payload = document["payload"]
    if not isinstance(payload, dict):
        raise ValueError("IBKR foundation payload must be an object")
    if _sha(payload) != document["build_sha256"]:
        raise ValueError("IBKR foundation payload identity does not match")
    configuration_payload = payload.get("configuration")
    if not isinstance(configuration_payload, dict):
        raise ValueError("IBKR foundation configuration child is invalid")
    configuration = decode_foundation_config(configuration_payload)
    replay = build_ibkr_foundation(provider_dataset, provider_rows, configuration)
    expected = _build_payload(replay)
    if expected != payload:
        raise ValueError("IBKR foundation children differ from independent replay")
    if replay.provider_history.dataset_sha256 != provider_dataset.dataset_sha256:
        raise ValueError("IBKR foundation source dataset differs from provider history")
    return replay


def load_ibkr_foundation(path: Path) -> IBKRFoundationBuild:
    """Load only after complete independent verification."""

    return verify_ibkr_foundation(path)
