"""Immutable runtime persistence and replay for the IBKR historical foundation."""

from __future__ import annotations

import hashlib
import io
import json
import resource
import shutil
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast

import polars as pl

from qtrad.adapters.parquet.observations import _observation_from_row
from qtrad.application.ibkr_foundation import IBKRFoundationBuild, build_ibkr_foundation
from qtrad.application.provider_history import (
    ProviderHistorySourceEvidence,
    provider_history_stage6_summary,
)
from qtrad.application.r2_ibkr_historical import (
    _availability_dataset_id,
    ibkr_availability_evidence,
)
from qtrad.application.walk_forward import build_expanding_folds
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import FoundationConfig, PanelDataset, TargetDataset
from qtrad.domain.ibkr_foundation import (
    IBKR_CONFIRMATORY_INSTRUMENTS,
    IBKR_FOUNDATION_CONTRACT,
    IBKR_FOUNDATION_SCHEMA_VERSION,
    IBKRFoundationReadiness,
    IBKRFoundationReadinessCause,
    IBKRFoundationReadinessState,
)
from qtrad.domain.ibkr_results import canonical_json_bytes
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.provider_history import (
    PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT,
    PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT,
    PROVIDER_HISTORY_SCHEMA_VERSION,
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalDataset,
)
from qtrad.domain.r2_holdout import (
    R2G2ObservationView,
    R2G2PanelView,
    R2HoldoutCausalMetadata,
    R2HoldoutTargetIndex,
    R2HoldoutTargetSource,
    R2OutcomeBlindObservationView,
    R2OutcomeBlindPanelView,
    R2OutcomeBlindTargetView,
    R2PreHoldoutTargetProjection,
)
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.foundation_bundle import (
    VerifiedG2FeatureSource,
    _fold,
    _panel_row,
    _target,
    decode_foundation_config,
)
from qtrad.runtime.provider_history import (
    _dataset_from_manifest,
    verify_provider_history_file_only,
)

_FOUNDATION_CHILD_CONTRACT = "qtrad-ibkr-historical-foundation-child-v1"
_FOUNDATION_CHILD_SCHEMA_VERSION = 1
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_CHILD_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_CHILD_FILE_BYTES = 64 * 1024 * 1024
_MAX_CHILD_PAYLOAD_BYTES = 32 * 1024 * 1024
_MAX_CHILD_ROWS = 100_000
_MAX_CHILD_PARTS = 20_000
_CHILD_DIRECTORY_SUFFIX = ".children"
_BOUNDED_PROVIDER_HISTORY_ROWS = 500_000
_FOUNDATION_VERIFICATION_CONTRACT = "qtrad-ibkr-foundation-verification-v1"
_FOUNDATION_VERIFICATION_SCHEMA_VERSION = 1
_FOUNDATION_AUTHENTICATION_CONTRACT = "qtrad-ibkr-foundation-authentication-v1"
_FOUNDATION_VERIFIER_CONTRACT = "qtrad-stage8-foundation-semantic-verifier-v1"
_FOUNDATION_VERIFIER_VERSION = 1
_FOUNDATION_VERIFIER_COMPLETED_CHECKS = (
    "provider-history-independent-replay",
    "stage8-independent-derivation",
    "foundation-metadata-equivalence",
    "child-byte-closure",
    "child-semantic-equivalence",
    "readiness-equivalence",
)
_VERIFICATION_RECEIPT_FIELDS = {
    "contract",
    "schema_version",
    "foundation_contract",
    "foundation_schema_version",
    "foundation_manifest_sha256",
    "foundation_build_sha256",
    "provider_history_manifest_sha256",
    "provider_history_dataset_sha256",
    "child_references_sha256",
    "configuration_id",
    "verifier_contract",
    "verifier_version",
    "verifier_identity",
    "completed_checks",
    "readiness_sha256",
    "evidence_class",
    "receipt_sha256",
}
_BASE_CHILD_KINDS = (
    "observations",
    "panel",
    "targets",
    "folds",
)
_LEGACY_EXTENSION_CHILD_KINDS = (
    "target-index",
    "causal-metadata",
    "blind-observations",
    "blind-panel",
    "pre-holdout-target",
)
_G2_EXTENSION_CHILD_KINDS = ("g2-observations", "g2-panel")
_LEGACY_CHILD_KINDS = _BASE_CHILD_KINDS + _LEGACY_EXTENSION_CHILD_KINDS
_CHILD_KINDS = _LEGACY_CHILD_KINDS + _G2_EXTENSION_CHILD_KINDS
_ProgressCallback = Callable[[Mapping[str, object]], None]
_CHILD_FIELDS = {
    "contract",
    "schema_version",
    "kind",
    "dataset_id",
    "part_index",
    "row_count",
    "file",
    "file_sha256",
    "rows_sha256",
    "lineage",
    "manifest_sha256",
}
_REFERENCE_FIELDS = {
    "kind",
    "dataset_id",
    "manifest_id",
    "manifest_path",
    "manifest_sha256",
    "row_count",
    "file",
    "file_sha256",
}


@dataclass(frozen=True, slots=True)
class IBKRG2FeatureSourceAuthority:
    """Opaque authority for exact outcome-free IBKR G2 feature children."""

    path: Path
    receipt: Path
    foundation_bundle_id: str
    foundation_configuration_id: str
    observation_dataset_id: str
    panel_dataset_id: str
    holdout_range: tuple[datetime, datetime]
    child_references_sha256: str
    target_child_references_sha256: str
    source_id: str


@dataclass(frozen=True, slots=True)
class _AuthenticatedFoundationManifest:
    path: Path
    manifest_bytes: bytes
    document: dict[str, object]
    payload: dict[str, object]
    configuration: FoundationConfig
    provider_path: Path
    provider_manifest_sha256: str
    provider_dataset: ProviderHistoricalDataset
    children: Mapping[str, JsonValue]
    expected_lineage: dict[str, JsonValue]


def _ibkr_g2_feature_source_id(
    *,
    foundation_bundle_id: str,
    foundation_configuration_id: str,
    observation_dataset_id: str,
    panel_dataset_id: str,
    holdout_range: tuple[datetime, datetime],
    child_references_sha256: str,
    target_child_references_sha256: str,
) -> str:
    return _sha(
        {
            "contract": "qtrad-r2-ibkr-g2-feature-source-authority-v1",
            "foundation_bundle_id": foundation_bundle_id,
            "foundation_configuration_id": foundation_configuration_id,
            "observation_dataset_id": observation_dataset_id,
            "panel_dataset_id": panel_dataset_id,
            "holdout_range": [item.isoformat() for item in holdout_range],
            "child_references_sha256": child_references_sha256,
            "target_child_references_sha256": target_child_references_sha256,
        }
    )


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
    return json.dumps(
        to_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _build_payload(
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    children: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    source = source_evidence.source_artifact
    provider_dataset = build.provider_history
    return {
        "configuration": foundation_config_payload(build.configuration),
        "provider_history": {
            "dataset_sha256": provider_dataset.dataset_sha256,
            "row_count": provider_dataset.row_count,
            "contract_selection_sha256": provider_dataset.contract_selection_sha256,
            "plan_sha256": provider_dataset.plan_sha256,
            "runtime_sha256": provider_dataset.runtime_sha256,
            "aggregate_sha256": provider_dataset.aggregate_sha256,
        },
        "source_evidence": {
            "eligible_contracts": [
                contract.as_json_value() for contract in source.plan.eligible_contracts
            ],
            "coverage_summary": source.aggregate.coverage_summary,
            "entitlement_summary": source.aggregate.entitlement_summary,
        },
        "children": dict(children),
        "active_intervals": {
            instrument: [[start.isoformat(), end.isoformat()] for start, end in intervals]
            for instrument, intervals in sorted(build.active_intervals.items())
        },
        "provider_gaps": [dict(gap) for gap in build.provider_gaps],
        "readiness": build.readiness.as_json(),
    }


def _manifest_payload(
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    children: Mapping[str, JsonValue],
    provider_manifest: Path,
    bundle_root: Path,
) -> dict[str, JsonValue]:
    payload = _build_payload(build, source_evidence, children)
    provider_path = _relative_path(bundle_root, provider_manifest, "provider-history manifest")
    return {
        "contract": IBKR_FOUNDATION_CONTRACT,
        "schema_version": IBKR_FOUNDATION_SCHEMA_VERSION,
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "provider_history_manifest": provider_path,
        "provider_history_sha256": hashlib.sha256(provider_manifest.read_bytes()).hexdigest(),
        "build_sha256": _sha(payload),
        "payload": payload,
    }


def _provider_history_manifest_identity(
    provider_manifest: Path,
) -> tuple[str, ProviderHistoricalDataset]:
    """Read v2 identity or the retained v1 identity needed by existing authorities."""

    manifest_bytes = _bounded_bytes(
        provider_manifest, _MAX_MANIFEST_BYTES, "provider-history manifest"
    )
    document = _mapping(_parse_json(manifest_bytes, "provider-history manifest"))
    from qtrad.runtime.provider_history_v2 import (
        PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT,
        _read_provider_history_v2_manifest,
    )

    if document["contract"] == PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT:
        manifest = _read_provider_history_v2_manifest(provider_manifest)
        return hashlib.sha256(manifest_bytes).hexdigest(), manifest.dataset
    if set(document) != {
        "contract",
        "schema_version",
        "selector_contract",
        "dataset",
        "availability_policy",
        "source_result",
        "source_plan_row_bound",
        "files",
        "manifest_sha256",
    }:
        raise ValueError("provider-history manifest fields are not exact")
    identity = dict(document)
    manifest_sha256 = _text(identity.pop("manifest_sha256"), "manifest_sha256")
    if document["contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT:
        raise ValueError("provider-history manifest contract is unsupported")
    if document["schema_version"] != PROVIDER_HISTORY_SCHEMA_VERSION:
        raise ValueError("provider-history manifest schema is unsupported")
    if document["selector_contract"] != PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT:
        raise ValueError("provider-history availability selector contract is unsupported")
    if manifest_sha256 != _sha(identity):
        raise ValueError("provider-history manifest identity does not match its content")
    if manifest_bytes != canonical_json_bytes(cast(Mapping[str, JsonValue], document)):
        raise ValueError("provider-history manifest bytes are not canonical")
    policy = ProviderHistoricalAvailabilityPolicy.from_json_value(document["availability_policy"])
    dataset = _dataset_from_manifest(
        _mapping(document["dataset"], "provider-history dataset"), policy
    )
    return hashlib.sha256(manifest_bytes).hexdigest(), dataset


def _stage8_checkpoint_source_identity(provider_manifest: Path) -> tuple[str, str, int]:
    manifest_bytes = _bounded_bytes(
        provider_manifest, _MAX_MANIFEST_BYTES, "provider-history v2 manifest"
    )
    from qtrad.runtime.provider_history_v2 import _read_provider_history_v2_manifest

    manifest = _read_provider_history_v2_manifest(provider_manifest)
    return (
        hashlib.sha256(manifest_bytes).hexdigest(),
        manifest.dataset.dataset_sha256,
        manifest.dataset.row_count,
    )


def _prepare_ibkr_foundation_preflight(
    output: Path,
    *,
    provider_manifest: Path,
    configuration: FoundationConfig,
    checkpoint_root: Path | None,
    workers: int,
) -> tuple[Path, Path, Path, str, str, int, bool, int, str]:
    output = _output_path(output)
    if output.exists():
        raise FileExistsError(f"IBKR foundation output already exists: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"IBKR foundation output parent is not a directory: {output.parent}")
    child_root = output.parent / f"{output.name}{_CHILD_DIRECTORY_SUFFIX}"
    if child_root.exists():
        raise FileExistsError(f"IBKR foundation child directory already exists: {child_root}")

    provider_manifest = _regular_file(provider_manifest, "provider-history manifest")
    _relative_path(output.parent, provider_manifest, "provider-history manifest")
    provider_manifest_sha256, provider_dataset_sha256, provider_row_count = (
        _stage8_checkpoint_source_identity(provider_manifest)
    )
    bounded = provider_row_count > _BOUNDED_PROVIDER_HISTORY_ROWS

    from qtrad.runtime.ibkr_foundation_bounded import _validated_workers

    workers = _validated_workers(workers)
    checkpoint_status = "NOT_REQUIRED" if not bounded else "NOT_CONFIGURED"
    if checkpoint_root is not None and bounded:
        from qtrad.runtime.ibkr_foundation_bounded import prepare_stage8_checkpoint

        checkpoint_status = (
            "REUSED" if (checkpoint_root / "identity.json").is_file() else "INITIALIZED"
        )
        prepare_stage8_checkpoint(
            checkpoint_root,
            provider_manifest_sha256=provider_manifest_sha256,
            provider_dataset_sha256=provider_dataset_sha256,
            configuration_id=configuration.configuration_id,
        )
    return (
        output,
        provider_manifest,
        child_root,
        provider_manifest_sha256,
        provider_dataset_sha256,
        provider_row_count,
        bounded,
        workers,
        checkpoint_status,
    )


def preflight_ibkr_foundation(
    output: Path,
    *,
    provider_manifest: Path,
    configuration: FoundationConfig,
    checkpoint_root: Path | None = None,
    workers: int = 4,
) -> dict[str, JsonValue]:
    """Authenticate a Stage 8 build invocation without decoding provider rows."""

    (
        output,
        provider_manifest,
        child_root,
        provider_manifest_sha256,
        provider_dataset_sha256,
        provider_row_count,
        bounded,
        workers,
        checkpoint_status,
    ) = _prepare_ibkr_foundation_preflight(
        output,
        provider_manifest=provider_manifest,
        configuration=configuration,
        checkpoint_root=checkpoint_root,
        workers=workers,
    )
    return {
        "contract": "qtrad-stage8-foundation-preflight-v1",
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "output": str(output),
        "child_root": str(child_root),
        "provider_history_manifest": str(provider_manifest),
        "provider_history_manifest_sha256": provider_manifest_sha256,
        "provider_history_dataset_sha256": provider_dataset_sha256,
        "provider_history_row_count": provider_row_count,
        "configuration_id": configuration.configuration_id,
        "bounded": bounded,
        "workers": workers,
        "checkpoint_root": str(checkpoint_root.resolve()) if checkpoint_root is not None else None,
        "checkpoint_status": checkpoint_status,
    }


def write_ibkr_foundation(
    output: Path,
    *,
    provider_manifest: Path,
    configuration: FoundationConfig,
    provider_history_receipt: Path | None = None,
    checkpoint_root: Path | None = None,
    workers: int = 4,
    progress_callback: _ProgressCallback | None = None,
) -> IBKRFoundationBuild:
    """Build and create the source-specific bundle once."""

    (
        output,
        provider_manifest,
        child_root,
        provider_manifest_sha256,
        _provider_dataset_sha256,
        _provider_row_count,
        bounded,
        workers,
        _checkpoint_status,
    ) = _prepare_ibkr_foundation_preflight(
        output,
        provider_manifest=provider_manifest,
        configuration=configuration,
        checkpoint_root=checkpoint_root,
        workers=workers,
    )
    if provider_history_receipt is None:
        raise ValueError("Stage 8 construction requires the provider-history v2 receipt")
    from qtrad.runtime.provider_history_v2 import authenticate_provider_history_v2

    source_evidence = authenticate_provider_history_v2(
        provider_manifest,
        receipt=provider_history_receipt,
        instrument_ids=tuple(
            sorted(
                {
                    *configuration.ordered_instruments,
                    *(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS),
                }
            )
        ),
        interval_start=configuration.required_observation_start,
        interval_end=configuration.required_observation_end,
    )
    started = time.monotonic()
    _emit_stage8_progress(progress_callback, started, "source-verification", "started")
    _emit_stage8_progress(
        progress_callback,
        started,
        "source-verification",
        "reused",
        provider_row_count=source_evidence.dataset.row_count,
    )
    build: IBKRFoundationBuild | None = None
    if not bounded:
        build = build_ibkr_foundation(source_evidence, configuration)
        if build.provider_history.dataset_sha256 != source_evidence.dataset.dataset_sha256:
            raise ValueError("provider history changed during foundation construction")

    child_root_created = False
    output_created = False
    children: Mapping[str, JsonValue]
    try:
        child_root.mkdir()
        child_root_created = True
        if bounded:
            from qtrad.runtime.ibkr_foundation_bounded import build_bounded_provider_foundation

            build, bounded_children = build_bounded_provider_foundation(
                source_evidence=source_evidence,
                configuration=configuration,
                child_root=child_root,
                bundle_root=output.parent,
                child_name=child_root.name,
                provider_manifest_sha256=provider_manifest_sha256,
                checkpoint_root=checkpoint_root,
                workers=workers,
                progress_callback=progress_callback,
            )
            children = cast(Mapping[str, JsonValue], bounded_children)
        else:
            assert build is not None
            children = _write_children(
                child_root,
                output.parent,
                build,
                source_evidence,
                provider_manifest,
            )
        assert build is not None
        document = _manifest_payload(
            build,
            source_evidence,
            children,
            provider_manifest,
            output.parent,
        )
        encoded = _json_bytes(document) + b"\n"
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("IBKR foundation manifest exceeds the 4 MiB limit")
        with output.open("xb") as handle:
            output_created = True
            handle.write(encoded)
    except BaseException:
        if child_root_created:
            shutil.rmtree(child_root)
        if output_created:
            output.unlink()
        raise
    assert build is not None
    _emit_stage8_progress(
        progress_callback,
        started,
        "publication",
        "completed",
        output=str(output),
    )
    return build


def _emit_stage8_progress(
    callback: _ProgressCallback | None,
    started: float,
    phase: str,
    event: str,
    **fields: object,
) -> None:
    if callback is None:
        return
    usage = resource.getrusage(resource.RUSAGE_SELF)
    callback(
        {
            "contract": "qtrad-stage8-progress-v1",
            "phase": phase,
            "event": event,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "maximum_rss_kib": int(usage.ru_maxrss),
            **fields,
        }
    )


def ibkr_foundation_verifier_identity() -> str:
    """Return the claim-scoped semantic identity for complete Stage 8 replay."""

    return _sha(
        {
            "contract": _FOUNDATION_VERIFIER_CONTRACT,
            "version": _FOUNDATION_VERIFIER_VERSION,
            "completed_checks": list(_FOUNDATION_VERIFIER_COMPLETED_CHECKS),
        }
    )


def _verify_provider_history_closure(path: Path, expected: ProviderHistoricalDataset) -> None:
    if verify_provider_history_file_only(path) != expected:
        raise ValueError("provider-history file closure differs from its manifest")


def _authenticate_foundation_manifest(
    path: Path,
    *,
    provider_closure: bool,
) -> _AuthenticatedFoundationManifest:
    manifest_path = _regular_file(path, "IBKR foundation manifest")
    manifest_bytes = _bounded_bytes(manifest_path, _MAX_MANIFEST_BYTES, "IBKR foundation manifest")
    document = _mapping(_parse_json(manifest_bytes, "IBKR foundation manifest"))
    if set(document) != {
        "contract",
        "schema_version",
        "source_class",
        "provider_history_manifest",
        "provider_history_sha256",
        "build_sha256",
        "payload",
    }:
        raise ValueError("IBKR foundation bundle has unknown or missing fields")
    if document["contract"] != IBKR_FOUNDATION_CONTRACT:
        raise ValueError("IBKR foundation bundle contract is unsupported")
    if document["schema_version"] != IBKR_FOUNDATION_SCHEMA_VERSION:
        raise ValueError("IBKR foundation bundle schema is unsupported")
    if document["source_class"] != "IBKR_HISTORICAL_RESEARCH":
        raise ValueError("IBKR foundation bundle source class is unsupported")
    if manifest_bytes != _json_bytes(document) + b"\n":
        raise ValueError("IBKR foundation manifest bytes are not canonical")

    provider_path = _safe_child(
        manifest_path.parent,
        _text(document["provider_history_manifest"], "provider-history manifest path"),
        "provider-history manifest",
    )
    provider_manifest_sha256, provider_dataset = _provider_history_manifest_identity(provider_path)
    if provider_manifest_sha256 != _text(
        document["provider_history_sha256"], "provider-history manifest hash"
    ):
        raise ValueError("provider-history manifest bytes changed")
    if provider_closure:
        _verify_provider_history_closure(provider_path, provider_dataset)

    payload = _mapping(document["payload"], "IBKR foundation payload")
    if _sha(payload) != _text(document["build_sha256"], "IBKR foundation build hash"):
        raise ValueError("IBKR foundation payload identity does not match")
    configuration = decode_foundation_config(
        _mapping(payload["configuration"], "IBKR foundation configuration")
    )
    expected_provider = {
        "dataset_sha256": provider_dataset.dataset_sha256,
        "row_count": provider_dataset.row_count,
        "contract_selection_sha256": provider_dataset.contract_selection_sha256,
        "plan_sha256": provider_dataset.plan_sha256,
        "runtime_sha256": provider_dataset.runtime_sha256,
        "aggregate_sha256": provider_dataset.aggregate_sha256,
    }
    if _mapping(payload["provider_history"]) != expected_provider:
        raise ValueError("IBKR foundation provider-history metadata differs from its child")
    children = _mapping(payload["children"], "IBKR foundation children")
    return _AuthenticatedFoundationManifest(
        path=manifest_path,
        manifest_bytes=manifest_bytes,
        document=document,
        payload=payload,
        configuration=configuration,
        provider_path=provider_path,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset=provider_dataset,
        children=cast(dict[str, JsonValue], children),
        expected_lineage={
            "provider_manifest_sha256": provider_manifest_sha256,
            "provider_dataset_sha256": provider_dataset.dataset_sha256,
            "plan_sha256": provider_dataset.plan_sha256,
            "aggregate_sha256": provider_dataset.aggregate_sha256,
        },
    )


def _verification_receipt_document(
    authenticated: _AuthenticatedFoundationManifest,
) -> dict[str, JsonValue]:
    identity: dict[str, JsonValue] = {
        "contract": _FOUNDATION_VERIFICATION_CONTRACT,
        "schema_version": _FOUNDATION_VERIFICATION_SCHEMA_VERSION,
        "foundation_contract": IBKR_FOUNDATION_CONTRACT,
        "foundation_schema_version": IBKR_FOUNDATION_SCHEMA_VERSION,
        "foundation_manifest_sha256": hashlib.sha256(authenticated.manifest_bytes).hexdigest(),
        "foundation_build_sha256": _text(
            authenticated.document["build_sha256"], "IBKR foundation build hash"
        ),
        "provider_history_manifest_sha256": authenticated.provider_manifest_sha256,
        "provider_history_dataset_sha256": authenticated.provider_dataset.dataset_sha256,
        "child_references_sha256": _sha(authenticated.children),
        "configuration_id": authenticated.configuration.configuration_id,
        "verifier_contract": _FOUNDATION_VERIFIER_CONTRACT,
        "verifier_version": _FOUNDATION_VERIFIER_VERSION,
        "verifier_identity": ibkr_foundation_verifier_identity(),
        "completed_checks": list(_FOUNDATION_VERIFIER_COMPLETED_CHECKS),
        "readiness_sha256": _sha(authenticated.payload["readiness"]),
        "evidence_class": EvidenceClass.IMPLEMENTATION.value,
    }
    return {**identity, "receipt_sha256": _sha(identity)}


def _authenticate_verification_receipt(
    authenticated: _AuthenticatedFoundationManifest,
    receipt: Path,
) -> tuple[Path, bytes, dict[str, object]]:
    receipt_path = _regular_file(receipt, "IBKR foundation verification receipt")
    receipt_bytes = _bounded_bytes(
        receipt_path, _MAX_MANIFEST_BYTES, "IBKR foundation verification receipt"
    )
    document = _mapping(_parse_json(receipt_bytes, "IBKR foundation verification receipt"))
    if set(document) != _VERIFICATION_RECEIPT_FIELDS:
        raise ValueError("IBKR foundation verification receipt fields are not exact")
    if receipt_bytes != _json_bytes(document) + b"\n":
        raise ValueError("IBKR foundation verification receipt bytes are not canonical")
    identity = dict(document)
    receipt_sha256 = _text(identity.pop("receipt_sha256"), "verification receipt identity")
    if receipt_sha256 != _sha(identity):
        raise ValueError("IBKR foundation verification receipt identity does not match")
    if document != _verification_receipt_document(authenticated):
        raise ValueError("IBKR foundation verification receipt does not match the foundation")
    return receipt_path, receipt_bytes, document


def _authentication_result(
    authenticated: _AuthenticatedFoundationManifest,
    receipt_path: Path,
    receipt_bytes: bytes,
    receipt_document: Mapping[str, object],
) -> dict[str, JsonValue]:
    return {
        "contract": _FOUNDATION_AUTHENTICATION_CONTRACT,
        "schema_version": 1,
        "foundation": str(authenticated.path),
        "foundation_build_sha256": _text(
            receipt_document["foundation_build_sha256"], "IBKR foundation build hash"
        ),
        "verification_receipt": str(receipt_path),
        "verification_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "verification_receipt_id": _text(
            receipt_document["receipt_sha256"], "verification receipt identity"
        ),
        "verifier_identity": _text(
            receipt_document["verifier_identity"], "Stage 8 verifier identity"
        ),
        "readiness": cast(JsonValue, authenticated.payload["readiness"]),
        "evidence_class": EvidenceClass.IMPLEMENTATION.value,
    }


def authenticate_ibkr_foundation(
    path: Path,
    *,
    receipt: Path,
) -> dict[str, JsonValue]:
    """Authenticate a verified Stage 8 closure without semantic replay."""

    authenticated = _authenticate_foundation_manifest(path, provider_closure=False)
    receipt_path, receipt_bytes, receipt_document = _authenticate_verification_receipt(
        authenticated, receipt
    )
    _verify_provider_history_closure(authenticated.provider_path, authenticated.provider_dataset)
    child_kinds = _supported_child_kinds(authenticated.children)
    child_ids = _child_reference_dataset_ids(
        authenticated.children,
        child_kinds=child_kinds,
    )
    _verify_children_blind(
        authenticated.path.parent,
        authenticated.children,
        child_ids,
        authenticated.expected_lineage,
        decode_rows=False,
        child_kinds=child_kinds,
    )
    return _authentication_result(authenticated, receipt_path, receipt_bytes, receipt_document)


def verify_ibkr_foundation(
    path: Path,
    *,
    replay_checkpoint_root: Path | None = None,
    provider_history_receipt: Path | None = None,
    workers: int = 4,
    receipt_output: Path | None = None,
) -> IBKRFoundationBuild:
    """Independently replay every child and optionally persist its receipt."""

    authenticated = _authenticate_foundation_manifest(path, provider_closure=False)
    receipt_path: Path | None = None
    if receipt_output is not None:
        _supported_child_kinds(authenticated.children)
        receipt_path = _output_path(receipt_output).resolve()
        immutable_roots = (
            (authenticated.path.parent / f"{authenticated.path.name}.children").resolve(),
            authenticated.provider_path.parent.resolve(),
        )
        if any(receipt_path.is_relative_to(root) for root in immutable_roots):
            raise ValueError(
                "verification receipt cannot be written inside an authenticated closure"
            )
    if provider_history_receipt is None:
        raise ValueError("Stage 8 verification requires the provider-history v2 receipt")
    from qtrad.runtime.provider_history_v2 import authenticate_provider_history_v2

    source_evidence = authenticate_provider_history_v2(
        authenticated.provider_path,
        receipt=provider_history_receipt,
        instrument_ids=tuple(
            sorted(
                {
                    *authenticated.configuration.ordered_instruments,
                    *(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS),
                }
            )
        ),
        interval_start=authenticated.configuration.required_observation_start,
        interval_end=authenticated.configuration.required_observation_end,
    )
    if authenticated.provider_dataset.row_count > _BOUNDED_PROVIDER_HISTORY_ROWS:
        from qtrad.runtime.ibkr_foundation_bounded import (
            prepare_stage8_replay_checkpoint,
            verify_bounded_provider_foundation,
        )

        published_bundle_sha256 = hashlib.sha256(authenticated.manifest_bytes).hexdigest()
        prepare_stage8_replay_checkpoint(
            replay_checkpoint_root,
            provider_manifest_sha256=authenticated.provider_manifest_sha256,
            provider_dataset_sha256=authenticated.provider_dataset.dataset_sha256,
            configuration_id=authenticated.configuration.configuration_id,
            published_bundle_sha256=published_bundle_sha256,
        )
        replay = verify_bounded_provider_foundation(
            source_evidence=source_evidence,
            configuration=authenticated.configuration,
            bundle_path=authenticated.path,
            document=authenticated.document,
            payload=authenticated.payload,
            replay_checkpoint_root=replay_checkpoint_root,
            workers=workers,
        )
    else:
        replay = build_ibkr_foundation(source_evidence, authenticated.configuration)
        expected_payload = _build_payload(replay, source_evidence, authenticated.children)
        if expected_payload != authenticated.payload:
            raise ValueError("IBKR foundation metadata differs from independent replay")
        expected_rows = _child_rows(replay)
        expected_dataset_ids = _child_dataset_ids(replay)
        expected_lineage = _child_lineage(
            replay,
            source_evidence,
            authenticated.provider_manifest_sha256,
        )
        child_kinds = _supported_child_kinds(authenticated.children)
        _verify_children(
            authenticated.path.parent,
            authenticated.children,
            expected_rows,
            expected_dataset_ids,
            expected_lineage,
            child_kinds=child_kinds,
        )
        if replay.provider_history.dataset_sha256 != source_evidence.dataset.dataset_sha256:
            raise ValueError("IBKR foundation source dataset differs from provider history")

    if receipt_path is not None:
        _write_create_only(
            receipt_path,
            _json_bytes(_verification_receipt_document(authenticated)) + b"\n",
        )
    return replay


def _load_authenticated_ibkr_foundation(
    path: Path,
    *,
    receipt: Path,
) -> tuple[IBKRFoundationBuild, str]:
    authenticated = _authenticate_foundation_manifest(path, provider_closure=False)
    _authenticate_verification_receipt(authenticated, receipt)
    _verify_provider_history_closure(authenticated.provider_path, authenticated.provider_dataset)
    child_ids = _child_reference_dataset_ids(authenticated.children)
    decoded = _verify_children_blind(
        authenticated.path.parent,
        authenticated.children,
        child_ids,
        authenticated.expected_lineage,
        decode_rows=False,
        decode_base=True,
    )

    configuration = authenticated.configuration
    observation_rows = tuple(_observation_from_row(row) for row in decoded["observations"])
    source_start = (
        min(row.interval_start for row in observation_rows)
        if observation_rows
        else configuration.range_start
    )
    source_end = (
        max(row.interval_end for row in observation_rows)
        if observation_rows
        else configuration.range_end
    )
    observations = ObservationDataset(
        rows=observation_rows,
        configuration={
            "contract": "qtrad-ibkr-historical-observation-adapter-v1",
            "source_class": "IBKR_HISTORICAL_RESEARCH",
            "provider": "ibkr",
            "environment": "paper",
            "ordered_instruments": list(configuration.ordered_instruments),
            "interval_start": configuration.required_observation_start.isoformat(),
            "interval_end": configuration.required_observation_end.isoformat(),
            "observed_interval_start": source_start.isoformat() if observation_rows else None,
            "observed_interval_end": source_end.isoformat() if observation_rows else None,
            "grid_resolution_seconds": int(configuration.grid_resolution.total_seconds()),
            "availability_basis": configuration.availability_basis.value,
            "source_dataset_id": authenticated.provider_dataset.dataset_sha256,
        },
        source_dataset_ids=(authenticated.provider_dataset.dataset_sha256,),
        selection_policies={
            "source_class": "IBKR_HISTORICAL_RESEARCH",
            "availability_policy": (
                authenticated.provider_dataset.availability_policy.as_json_value()
            ),
            "correction_policy": "FROZEN_FIRST_SUCCESSFUL_RESPONSE_NO_REFETCH_MERGE",
        },
        dataset_id=child_ids["observations"],
    )
    panel = PanelDataset(
        rows=tuple(_panel_row(row) for row in decoded["panel"]),
        observation_dataset_id=observations.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
        dataset_id=child_ids["panel"],
    )
    targets = TargetDataset(
        rows=tuple(_target(row) for row in decoded["targets"]),
        observation_dataset_id=observations.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
        dataset_id=child_ids["targets"],
    )
    target_index = R2HoldoutTargetIndex.from_rows(
        source_target_dataset_id=targets.dataset_id,
        observation_dataset_id=observations.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
        rows=decoded["target-index"],
    )
    causal_metadata = R2HoldoutCausalMetadata.from_rows(
        source_panel_dataset_id=panel.dataset_id,
        rows=decoded["causal-metadata"],
    )
    build = IBKRFoundationBuild(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=FoldDataset(
            folds=tuple(_fold(row) for row in decoded["folds"]),
            target_dataset_id=targets.dataset_id,
            foundation_configuration_id=configuration.configuration_id,
            dataset_id=child_ids["folds"],
        ),
        target_index=target_index,
        causal_metadata=causal_metadata,
        provider_history=authenticated.provider_dataset,
        active_intervals=_decode_active_intervals(authenticated.payload["active_intervals"]),
        provider_gaps=tuple(
            cast(Mapping[str, JsonValue], _mapping(item, "IBKR provider gap"))
            for item in _sequence(authenticated.payload["provider_gaps"])
        ),
        readiness=_decode_readiness(authenticated.payload["readiness"]),
    )
    return build, _text(authenticated.document["build_sha256"], "IBKR foundation build hash")


def load_ibkr_foundation(path: Path, *, receipt: Path) -> IBKRFoundationBuild:
    """Load a foundation authenticated by a completed Stage 8 receipt."""

    build, _build_id = _load_authenticated_ibkr_foundation(path, receipt=receipt)
    return build


def load_ibkr_foundation_with_identity(
    path: Path,
    *,
    receipt: Path,
) -> tuple[IBKRFoundationBuild, str]:
    """Load an authenticated foundation and return its build identity."""

    return _load_authenticated_ibkr_foundation(path, receipt=receipt)


def _load_ibkr_foundation_outcome_blind(
    path: Path,
    *,
    receipt: Path,
    holdout_target_source: R2HoldoutTargetSource,
    decode_g2: bool,
    decode_target: bool = False,
) -> tuple[
    IBKRFoundationBuild,
    str,
    IBKRG2FeatureSourceAuthority,
    VerifiedG2FeatureSource | None,
    TargetDataset | None,
]:
    """Load the IBKR foundation without decoding outcome-bearing children.

    The normal loader above is intentionally complete and is used after the
    marker is opened.  Representative OOF must use this narrower path: target,
    panel and observation children are authenticated by manifest/file bytes,
    while only the persisted outcome-blind projections and folds are decoded.
    """

    authenticated = _authenticate_foundation_manifest(path, provider_closure=False)
    receipt_path, _receipt_bytes, _receipt_document = _authenticate_verification_receipt(
        authenticated, receipt
    )
    _verify_provider_history_closure(authenticated.provider_path, authenticated.provider_dataset)
    manifest_path = authenticated.path
    root = manifest_path.parent
    provider_dataset = authenticated.provider_dataset
    payload = authenticated.payload
    configuration = authenticated.configuration
    children = authenticated.children
    child_ids = _child_reference_dataset_ids(children)
    decoded = _verify_children_blind(
        root,
        children,
        child_ids,
        authenticated.expected_lineage,
        decode_g2=decode_g2,
        decode_target=decode_target,
    )
    if child_ids["observations"] != configuration.observation_dataset_id:
        raise ValueError("IBKR foundation observation child differs from configuration")
    if child_ids["blind-observations"] != _blind_observation_projection_id(
        source_dataset_id=child_ids["observations"],
        holdout_start=configuration.holdout_range[0],
        rows=decoded["blind-observations"],
    ):
        raise ValueError("IBKR blind observation projection identity is invalid")
    if child_ids["blind-panel"] != _blind_panel_projection_id(
        source_dataset_id=child_ids["panel"],
        holdout_start=configuration.holdout_range[0],
        rows=decoded["blind-panel"],
    ):
        raise ValueError("IBKR blind panel projection identity is invalid")

    target_index = R2HoldoutTargetIndex.from_rows(
        source_target_dataset_id=child_ids["targets"],
        observation_dataset_id=child_ids["observations"],
        foundation_configuration_id=configuration.configuration_id,
        rows=decoded["target-index"],
    )
    if target_index.dataset_id != child_ids["target-index"]:
        raise ValueError("IBKR holdout target index identity is invalid")
    causal_metadata = R2HoldoutCausalMetadata.from_rows(
        source_panel_dataset_id=child_ids["panel"],
        rows=decoded["causal-metadata"],
    )
    if causal_metadata.dataset_id != child_ids["causal-metadata"]:
        raise ValueError("IBKR holdout causal metadata identity is invalid")
    pre_holdout_target = R2PreHoldoutTargetProjection.from_json(decoded["pre-holdout-target"][0])
    projection_mismatches = []
    if pre_holdout_target.source_target_dataset_id != child_ids["targets"]:
        projection_mismatches.append("source target")
    if pre_holdout_target.observation_dataset_id != child_ids["observations"]:
        projection_mismatches.append("observation")
    if pre_holdout_target.foundation_configuration_id != configuration.configuration_id:
        projection_mismatches.append("configuration")
    if pre_holdout_target.holdout_start != configuration.holdout_range[0]:
        projection_mismatches.append("holdout start")
    if pre_holdout_target.primary_horizon_seconds != int(
        configuration.primary_vertical_horizon.total_seconds()
    ):
        projection_mismatches.append("horizon")
    if pre_holdout_target.target_instruments != tuple(
        sorted(holdout_target_source.target_instruments)
    ):
        projection_mismatches.append("instruments")
    if (
        pre_holdout_target.projected_target_dataset
        != holdout_target_source.pre_holdout_target_dataset
    ):
        projection_mismatches.append("projected rows")
    if pre_holdout_target.projection_id != child_ids["pre-holdout-target"]:
        projection_mismatches.append("projection ID")
    if projection_mismatches:
        raise ValueError(
            "IBKR pre-holdout target projection is not source-authenticated: "
            + ", ".join(projection_mismatches)
        )

    blind_observations = R2OutcomeBlindObservationView(
        dataset_id=child_ids["observations"],
        rows=tuple(_observation_from_row(row) for row in decoded["blind-observations"]),
        configuration={
            "ordered_instruments": list(configuration.ordered_instruments),
            "availability_basis": configuration.availability_basis.value,
        },
        source_dataset_ids=(provider_dataset.dataset_sha256,),
        selection_policies={"availability_basis": configuration.availability_basis.value},
        projection_id=child_ids["blind-observations"],
    )
    blind_panel = R2OutcomeBlindPanelView(
        dataset_id=child_ids["panel"],
        observation_dataset_id=child_ids["observations"],
        foundation_configuration_id=configuration.configuration_id,
        rows=tuple(_panel_row(row) for row in decoded["blind-panel"]),
        projection_id=child_ids["blind-panel"],
    )
    build_id = _text(authenticated.document["build_sha256"], "IBKR foundation build hash")
    child_references_sha256 = _sha({kind: children[kind] for kind in _G2_EXTENSION_CHILD_KINDS})
    target_child_references_sha256 = _sha(children["targets"])
    g2_source_id = _ibkr_g2_feature_source_id(
        foundation_bundle_id=build_id,
        foundation_configuration_id=configuration.configuration_id,
        observation_dataset_id=child_ids["observations"],
        panel_dataset_id=child_ids["panel"],
        holdout_range=configuration.holdout_range,
        child_references_sha256=child_references_sha256,
        target_child_references_sha256=target_child_references_sha256,
    )
    g2_authority = IBKRG2FeatureSourceAuthority(
        path=manifest_path,
        receipt=receipt_path,
        foundation_bundle_id=build_id,
        foundation_configuration_id=configuration.configuration_id,
        observation_dataset_id=child_ids["observations"],
        panel_dataset_id=child_ids["panel"],
        holdout_range=configuration.holdout_range,
        child_references_sha256=child_references_sha256,
        target_child_references_sha256=target_child_references_sha256,
        source_id=g2_source_id,
    )
    g2_source: VerifiedG2FeatureSource | None = None
    if decode_g2:
        g2_observation_rows = tuple(
            _observation_from_row(row) for row in decoded["g2-observations"]
        )
        g2_panel_rows = tuple(_panel_row(row) for row in decoded["g2-panel"])
        g2_observations = R2G2ObservationView(
            dataset_id=child_ids["observations"],
            rows=g2_observation_rows,
            configuration=blind_observations.configuration,
            source_dataset_ids=blind_observations.source_dataset_ids,
            selection_policies=blind_observations.selection_policies,
            holdout_range=configuration.holdout_range,
            projection_id=child_ids["g2-observations"],
        )
        g2_panel = R2G2PanelView(
            dataset_id=child_ids["panel"],
            observation_dataset_id=child_ids["observations"],
            foundation_configuration_id=configuration.configuration_id,
            rows=g2_panel_rows,
            holdout_range=configuration.holdout_range,
            projection_id=child_ids["g2-panel"],
        )
        if g2_observations.projection_id != R2G2ObservationView.compute_projection_id(
            source_dataset_id=child_ids["observations"],
            holdout_range=configuration.holdout_range,
            rows=g2_observation_rows,
        ):
            raise ValueError("IBKR G2 observation projection identity is invalid")
        if g2_panel.projection_id != R2G2PanelView.compute_projection_id(
            source_dataset_id=child_ids["panel"],
            holdout_range=configuration.holdout_range,
            rows=g2_panel_rows,
        ):
            raise ValueError("IBKR G2 panel projection identity is invalid")
        if any(row.interval_end > configuration.holdout_range[1] for row in g2_observation_rows):
            raise ValueError("IBKR G2 observations exceed the holdout boundary")
        if any(
            row.decision_time < configuration.holdout_range[0]
            or row.decision_time >= configuration.holdout_range[1]
            for row in g2_panel_rows
        ):
            raise ValueError("IBKR G2 panel row lies outside the holdout range")
        g2_source = VerifiedG2FeatureSource(
            observations=g2_observations,
            panel=g2_panel,
            source_id=g2_source_id,
        )
    full_targets: TargetDataset | None = None
    if decode_target:
        full_targets = TargetDataset(
            rows=tuple(_target(row) for row in decoded["targets"]),
            observation_dataset_id=child_ids["observations"],
            foundation_configuration_id=configuration.configuration_id,
            dataset_id=child_ids["targets"],
        )
        holdout_target_source.verify_target_dataset(full_targets)

    blind_targets = R2OutcomeBlindTargetView.from_source(holdout_target_source)
    if (
        holdout_target_source.source_target_dataset_id != child_ids["targets"]
        or holdout_target_source.observation_dataset_id != child_ids["observations"]
        or holdout_target_source.foundation_configuration_id != configuration.configuration_id
    ):
        raise ValueError("IBKR holdout target source is not bound to the foundation children")
    holdout_target_source.verify_target_index(target_index)

    active_intervals = _decode_active_intervals(payload["active_intervals"])
    provider_gaps = tuple(
        cast(
            Mapping[str, JsonValue],
            _mapping(item, "IBKR provider gap"),
        )
        for item in _sequence(payload["provider_gaps"])
    )
    readiness = _decode_readiness(payload["readiness"])
    provisional = IBKRFoundationBuild(
        configuration=configuration,
        observations=blind_observations,
        panel=blind_panel,
        targets=blind_targets,
        folds=FoldDataset(
            folds=tuple(_fold(row) for row in decoded["folds"]),
            target_dataset_id=child_ids["targets"],
            foundation_configuration_id=configuration.configuration_id,
            dataset_id=child_ids["folds"],
        ),
        target_index=target_index,
        causal_metadata=causal_metadata,
        provider_history=provider_dataset,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        readiness=readiness,
    )
    availability = ibkr_availability_evidence(provisional)
    holdout_target_source.verify_r1_causal_evidence(
        causal_metadata=causal_metadata,
        source_active_intervals=active_intervals,
        data_gaps=_data_gap_tuples(provider_gaps),
        availability_evidence_id=_availability_dataset_id(
            blind_observations.dataset_id,
            availability,
        ),
    )
    try:
        expected_folds = build_expanding_folds(cast(TargetDataset, blind_targets), configuration)
    except ValueError as error:
        if str(error) != "no scientifically valid expanding folds are available":
            raise
        expected_folds = FoldDataset.create(
            (),
            target_dataset_id=child_ids["targets"],
            foundation_configuration_id=configuration.configuration_id,
        )
    if provisional.folds != expected_folds:
        raise ValueError("IBKR foundation folds differ from deterministic blind replay")
    return provisional, build_id, g2_authority, g2_source, full_targets


def load_ibkr_foundation_outcome_blind_with_identity(
    path: Path,
    *,
    receipt: Path,
    holdout_target_source: R2HoldoutTargetSource,
) -> tuple[IBKRFoundationBuild, str]:
    """Load F2-safe IBKR evidence while authenticating but not decoding G2 rows."""

    build, build_id, _authority, _source, _targets = _load_ibkr_foundation_outcome_blind(
        path,
        receipt=receipt,
        holdout_target_source=holdout_target_source,
        decode_g2=False,
    )
    return build, build_id


def _load_ibkr_foundation_outcome_blind_with_g2_authority(
    path: Path,
    *,
    receipt: Path,
    holdout_target_source: R2HoldoutTargetSource,
) -> tuple[IBKRFoundationBuild, str, IBKRG2FeatureSourceAuthority]:
    """Return the verifier-created G2 feature authority without decoding its rows."""

    build, build_id, authority, _source, _targets = _load_ibkr_foundation_outcome_blind(
        path,
        receipt=receipt,
        holdout_target_source=holdout_target_source,
        decode_g2=False,
    )
    if build.readiness.state is not IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY:
        raise ValueError("nonqualifying IBKR foundation cannot authorise downstream R2 work")
    return build, build_id, authority


def _verify_ibkr_g2_feature_source(
    authority: IBKRG2FeatureSourceAuthority,
    *,
    holdout_target_source: R2HoldoutTargetSource,
) -> VerifiedG2FeatureSource:
    """Decode exact IBKR G2 children only after the confirmatory G1 gate."""

    _build, build_id, replayed_authority, source, _targets = _load_ibkr_foundation_outcome_blind(
        authority.path,
        receipt=authority.receipt,
        holdout_target_source=holdout_target_source,
        decode_g2=True,
    )
    if build_id != authority.foundation_bundle_id or replayed_authority != authority:
        raise ValueError("IBKR G2 feature source differs from verified F2 authority")
    if source is None:
        raise ValueError("IBKR G2 feature source was not decoded")
    return source


def _verify_ibkr_confirmatory_target_dataset(
    authority: IBKRG2FeatureSourceAuthority,
    *,
    holdout_target_source: R2HoldoutTargetSource,
) -> TargetDataset:
    """Decode the exact IBKR target child only after irreversible confirmatory OPENED."""

    _build, build_id, replayed_authority, _source, targets = _load_ibkr_foundation_outcome_blind(
        authority.path,
        receipt=authority.receipt,
        holdout_target_source=holdout_target_source,
        decode_g2=False,
        decode_target=True,
    )
    if build_id != authority.foundation_bundle_id or replayed_authority != authority:
        raise ValueError("IBKR outcome target differs from verified F2 authority")
    if targets is None:
        raise ValueError("IBKR outcome target was not decoded")
    return targets


def _supported_child_kinds(children: Mapping[str, object]) -> tuple[str, ...]:
    child_set = set(children)
    for kinds in (_BASE_CHILD_KINDS, _LEGACY_CHILD_KINDS, _CHILD_KINDS):
        if child_set == set(kinds):
            return kinds
    raise ValueError("IBKR foundation child set is incomplete or unsupported")


def _child_reference_dataset_ids(
    children: Mapping[str, object],
    *,
    child_kinds: Sequence[str] | None = None,
) -> dict[str, str]:
    kinds = _CHILD_KINDS if child_kinds is None else tuple(child_kinds)
    result: dict[str, str] = {}
    if set(children) != set(kinds):
        raise ValueError("IBKR foundation child set is incomplete or duplicated")
    for kind in kinds:
        raw_parts = children[kind]
        if not isinstance(raw_parts, list) or not raw_parts:
            raise ValueError("IBKR foundation child parts are invalid")
        ids = {
            _text(
                _mapping(part, "IBKR foundation child reference")["dataset_id"], "child dataset ID"
            )
            for part in raw_parts
        }
        if len(ids) != 1:
            raise ValueError("IBKR foundation child parts use multiple dataset identities")
        result[kind] = next(iter(ids))
    return result


def _blind_observation_projection_id(
    *,
    source_dataset_id: str,
    holdout_start: datetime,
    rows: Sequence[Mapping[str, JsonValue]],
) -> str:
    typed = tuple(_observation_from_row(cast(Mapping[str, object], row)) for row in rows)
    return R2OutcomeBlindObservationView.compute_projection_id(
        source_dataset_id=source_dataset_id,
        holdout_start=holdout_start,
        rows=typed,
    )


def _blind_panel_projection_id(
    *,
    source_dataset_id: str,
    holdout_start: datetime,
    rows: Sequence[Mapping[str, JsonValue]],
) -> str:
    typed = tuple(_panel_row(cast(Mapping[str, object], row)) for row in rows)
    return R2OutcomeBlindPanelView.compute_projection_id(
        source_dataset_id=source_dataset_id,
        holdout_start=holdout_start,
        rows=typed,
    )


def _decode_active_intervals(value: object) -> dict[str, tuple[tuple[datetime, datetime], ...]]:
    raw = _mapping(value, "IBKR active intervals")
    decoded: dict[str, tuple[tuple[datetime, datetime], ...]] = {}
    for instrument_id, raw_intervals in raw.items():
        intervals: list[tuple[datetime, datetime]] = []
        if not isinstance(raw_intervals, list):
            raise ValueError("IBKR active intervals must be arrays")
        for raw_interval in raw_intervals:
            if not isinstance(raw_interval, list) or len(raw_interval) != 2:
                raise ValueError("IBKR active interval must contain two timestamps")
            start = _timestamp(raw_interval[0], "IBKR active interval start")
            end = _timestamp(raw_interval[1], "IBKR active interval end")
            if end <= start:
                raise ValueError("IBKR active interval must be positive")
            intervals.append((start, end))
        if tuple(sorted(intervals)) != tuple(intervals):
            raise ValueError("IBKR active intervals are not ordered")
        decoded[instrument_id] = tuple(intervals)
    return decoded


def _data_gap_tuples(
    gaps: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, datetime, datetime], ...]:
    result: list[tuple[str, datetime, datetime]] = []
    for gap in gaps:
        result.append(
            (
                _text(gap["instrument_id"], "IBKR gap instrument"),
                _timestamp(gap["interval_start"], "IBKR gap start"),
                _timestamp(gap["interval_end"], "IBKR gap end"),
            )
        )
    return tuple(result)


def _decode_readiness(value: object) -> IBKRFoundationReadiness:
    raw = _mapping(value, "IBKR foundation readiness")
    expected = {
        "contract",
        "schema_version",
        "state",
        "causes",
        "candidate_instruments",
        "groups",
        "common_support_start",
        "common_support_end",
        "common_support_rows",
        "rows_by_candidate",
        "evidence",
    }
    if set(raw) != expected:
        raise ValueError("IBKR foundation readiness has an unexpected schema")
    candidates = tuple(
        InstrumentId(_text(item, "readiness candidate"))
        for item in _sequence(raw["candidate_instruments"])
    )
    groups = tuple(_text(item, "readiness group") for item in _sequence(raw["groups"]))
    rows_by_candidate = {
        key: _int(item, "readiness candidate row count")
        for key, item in _mapping(raw["rows_by_candidate"]).items()
    }
    return IBKRFoundationReadiness(
        state=IBKRFoundationReadinessState(_text(raw["state"], "readiness state")),
        causes=tuple(
            IBKRFoundationReadinessCause(_text(item, "readiness cause"))
            for item in _sequence(raw["causes"])
        ),
        candidate_instruments=candidates,
        groups=groups,
        common_support_start=(
            None
            if raw["common_support_start"] is None
            else _timestamp(raw["common_support_start"], "readiness support start")
        ),
        common_support_end=(
            None
            if raw["common_support_end"] is None
            else _timestamp(raw["common_support_end"], "readiness support end")
        ),
        common_support_rows=_int(raw["common_support_rows"], "readiness support rows"),
        rows_by_candidate=rows_by_candidate,
        evidence=cast(dict[str, JsonValue], _mapping(raw["evidence"])),
    )


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected a JSON array")
    return cast(list[object], value)


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field} must use UTC")
    return parsed


def _child_lineage(
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    provider_manifest_sha256: str,
) -> dict[str, JsonValue]:
    source_summary = provider_history_stage6_summary(source_evidence)
    lineage: dict[str, JsonValue] = {
        "provider_manifest_sha256": provider_manifest_sha256,
        "provider_dataset_sha256": build.provider_history.dataset_sha256,
        "plan_sha256": source_evidence.source_artifact.plan.plan_sha256,
    }
    if source_summary.result_id is not None:
        lineage.update(
            {
                "stage6_result_id": source_summary.result_id,
                "stage6_closure_id": source_summary.closure_id,
                "stage6_verification_id": source_summary.verification_id,
            }
        )
    else:
        legacy_aggregate = source_summary.legacy_aggregate_sha256
        if legacy_aggregate is None:
            raise ValueError("IBKR v2 source aggregate identity is missing")
        lineage["aggregate_sha256"] = legacy_aggregate
    return lineage


def _write_children(
    child_root: Path,
    bundle_root: Path,
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    provider_manifest: Path,
) -> dict[str, JsonValue]:
    provider_manifest_sha256 = hashlib.sha256(provider_manifest.read_bytes()).hexdigest()
    lineage = _child_lineage(build, source_evidence, provider_manifest_sha256)
    rows = _child_rows(build)
    dataset_ids = _child_dataset_ids(build)
    children: dict[str, JsonValue] = {}
    for kind in _CHILD_KINDS:
        children[kind] = _write_child_parts(
            child_root,
            bundle_root,
            kind,
            rows[kind],
            dataset_ids[kind],
            lineage,
        )
    return children


def _payload_byte_count(payload: str) -> int:
    return len(payload.encode("utf-8"))


def _child_payload_chunks(
    rows: Sequence[Mapping[str, JsonValue]],
) -> Iterator[tuple[str, ...]]:
    """Partition variable-sized rows deterministically before Parquet encoding."""

    payloads: list[str] = []
    payload_bytes = 0
    emitted = False
    for row in rows:
        payload = _canonical_row(row)
        encoded_bytes = _payload_byte_count(payload)
        if payloads and (
            len(payloads) >= _MAX_CHILD_ROWS
            or payload_bytes + encoded_bytes > _MAX_CHILD_PAYLOAD_BYTES
        ):
            yield tuple(payloads)
            emitted = True
            payloads.clear()
            payload_bytes = 0
        payloads.append(payload)
        payload_bytes += encoded_bytes
    if payloads or not emitted:
        yield tuple(payloads)


def _write_child_parts(
    child_root: Path,
    bundle_root: Path,
    kind: str,
    rows: tuple[dict[str, JsonValue], ...],
    dataset_id: str,
    lineage: Mapping[str, JsonValue],
) -> list[JsonValue]:
    if kind not in _CHILD_KINDS:
        raise ValueError(f"unsupported IBKR foundation child kind: {kind}")
    parts: list[JsonValue] = []
    for part_index, payloads in enumerate(_child_payload_chunks(rows)):
        parquet_bytes = _parquet_bytes(payloads)
        if not parquet_bytes or len(parquet_bytes) > _MAX_CHILD_FILE_BYTES:
            raise ValueError("IBKR foundation Parquet child exceeds its byte bound")
        relative_file = (
            f"{child_root.name}/parquet/{kind}/"
            f"part-{part_index:06d}-{hashlib.sha256(parquet_bytes).hexdigest()[:24]}.parquet"
        )
        file_path = bundle_root / PurePosixPath(relative_file)
        _write_create_only(file_path, parquet_bytes)
        identity: dict[str, JsonValue] = {
            "contract": _FOUNDATION_CHILD_CONTRACT,
            "schema_version": _FOUNDATION_CHILD_SCHEMA_VERSION,
            "kind": kind,
            "dataset_id": dataset_id,
            "part_index": part_index,
            "row_count": len(payloads),
            "file": relative_file,
            "file_sha256": hashlib.sha256(parquet_bytes).hexdigest(),
            "rows_sha256": _sha(list(payloads)),
            "lineage": dict(lineage),
        }
        manifest_sha256 = _sha(identity)
        manifest: dict[str, JsonValue] = {
            **identity,
            "manifest_sha256": manifest_sha256,
        }
        relative_manifest = (
            f"{child_root.name}/manifests/{kind}/part-{part_index:06d}-{manifest_sha256[:24]}.json"
        )
        manifest_path = bundle_root / PurePosixPath(relative_manifest)
        encoded_manifest = _json_bytes(manifest) + b"\n"
        if len(encoded_manifest) > _MAX_CHILD_MANIFEST_BYTES:
            raise ValueError("IBKR foundation child manifest exceeds the 4 MiB limit")
        _write_create_only(manifest_path, encoded_manifest)
        parts.append(
            {
                "kind": kind,
                "dataset_id": dataset_id,
                "manifest_id": manifest_sha256[:24],
                "manifest_path": relative_manifest,
                "manifest_sha256": manifest_sha256,
                "row_count": len(payloads),
                "file": relative_file,
                "file_sha256": hashlib.sha256(parquet_bytes).hexdigest(),
            }
        )
    if len(parts) > _MAX_CHILD_PARTS:
        raise ValueError("IBKR foundation child part count exceeds its bound")
    return parts


def _verify_children(
    bundle_root: Path,
    children: Mapping[str, object],
    expected_rows: Mapping[str, tuple[dict[str, JsonValue], ...]],
    expected_dataset_ids: Mapping[str, str],
    expected_lineage: Mapping[str, JsonValue],
    *,
    child_kinds: Sequence[str] | None = None,
) -> None:
    kinds = _CHILD_KINDS if child_kinds is None else tuple(child_kinds)
    if set(children) != set(kinds):
        raise ValueError("IBKR foundation child set is incomplete or duplicated")
    expected_files: set[str] = set()
    child_root_names: set[str] = set()
    for kind in kinds:
        raw_parts = children[kind]
        if not isinstance(raw_parts, list) or not raw_parts:
            raise ValueError("IBKR foundation child parts are invalid")
        if len(raw_parts) > _MAX_CHILD_PARTS:
            raise ValueError("IBKR foundation child part count exceeds its bound")
        observed_rows: list[dict[str, JsonValue]] = []
        previous_path = ""
        for part_index, raw_part in enumerate(raw_parts):
            reference = _child_reference(raw_part, kind)
            manifest_reference = _text(
                reference["manifest_path"],
                "child manifest path",
            )
            if manifest_reference <= previous_path:
                raise ValueError("IBKR foundation child references are not canonical")
            previous_path = manifest_reference
            child_root_names.add(PurePosixPath(manifest_reference).parts[0])
            manifest_path = _safe_child(
                bundle_root,
                manifest_reference,
                "IBKR foundation child manifest",
            )
            manifest_bytes = _bounded_bytes(
                manifest_path,
                _MAX_CHILD_MANIFEST_BYTES,
                "IBKR foundation child manifest",
            )
            manifest = _mapping(_parse_json(manifest_bytes, "IBKR foundation child manifest"))
            if set(manifest) != _CHILD_FIELDS:
                raise ValueError("IBKR foundation child manifest has unknown or missing fields")
            if manifest_bytes != _json_bytes(manifest) + b"\n":
                raise ValueError("IBKR foundation child manifest bytes are not canonical")
            if manifest["contract"] != _FOUNDATION_CHILD_CONTRACT:
                raise ValueError("IBKR foundation child contract is unsupported")
            if manifest["schema_version"] != _FOUNDATION_CHILD_SCHEMA_VERSION:
                raise ValueError("IBKR foundation child schema is unsupported")
            identity = dict(manifest)
            manifest_hash = _text(identity.pop("manifest_sha256"), "child manifest hash")
            if manifest_hash != _sha(identity):
                raise ValueError("IBKR foundation child manifest identity does not match")
            if manifest_hash != _text(
                reference["manifest_sha256"],
                "child manifest hash",
            ):
                raise ValueError("IBKR foundation child manifest hash differs from its reference")
            manifest_kind = _text(manifest["kind"], "child kind")
            if manifest_kind != kind:
                raise ValueError("IBKR foundation child kind differs from its reference")
            manifest_dataset_id = _text(manifest["dataset_id"], "child dataset ID")
            if manifest_dataset_id != expected_dataset_ids[kind]:
                raise ValueError("IBKR foundation child dataset differs from replay")
            manifest_part_index = _int(manifest["part_index"], "child part index")
            if manifest_part_index != part_index:
                raise ValueError("IBKR foundation child part index is not contiguous")
            manifest_row_count = _int(manifest["row_count"], "child row count")
            manifest_file = _text(manifest["file"], "child Parquet path")
            manifest_file_sha256 = _text(manifest["file_sha256"], "child Parquet hash")
            manifest_lineage = _mapping(manifest["lineage"], "child lineage")
            if manifest_lineage != dict(expected_lineage):
                raise ValueError("IBKR foundation child lineage differs from replay")
            expected_reference: dict[str, object] = {
                "kind": manifest_kind,
                "dataset_id": manifest_dataset_id,
                "manifest_id": manifest_hash[:24],
                "manifest_path": manifest_reference,
                "manifest_sha256": manifest_hash,
                "row_count": manifest_row_count,
                "file": manifest_file,
                "file_sha256": manifest_file_sha256,
            }
            if reference != expected_reference:
                raise ValueError("IBKR foundation child reference differs from its manifest")
            file_path = _safe_child(
                bundle_root,
                manifest_file,
                "IBKR foundation child Parquet",
            )
            parquet_bytes = _bounded_bytes(
                file_path,
                _MAX_CHILD_FILE_BYTES,
                "IBKR foundation child Parquet",
            )
            file_hash = hashlib.sha256(parquet_bytes).hexdigest()
            if file_hash != manifest_file_sha256:
                raise ValueError("IBKR foundation child Parquet bytes changed")
            rows = _read_child_rows(
                file_path,
                expected_row_count=manifest_row_count,
            )
            if _sha([_canonical_row(row) for row in rows]) != _text(
                manifest["rows_sha256"],
                "child row hash",
            ):
                raise ValueError("IBKR foundation child row identity does not match")
            observed_rows.extend(rows)
            expected_files.update(
                {
                    manifest_reference,
                    manifest_file,
                }
            )
        if tuple(observed_rows) != expected_rows[kind]:
            raise ValueError(f"IBKR foundation {kind} differs from independent replay")

    if len(child_root_names) != 1:
        raise ValueError("IBKR foundation child references use multiple roots")
    child_root = bundle_root / next(iter(child_root_names))
    actual_files = {
        path.relative_to(bundle_root).as_posix() for path in child_root.rglob("*") if path.is_file()
    }
    if actual_files != expected_files:
        raise ValueError("IBKR foundation child closure contains unexpected files")


def _verify_children_blind(
    bundle_root: Path,
    children: Mapping[str, object],
    expected_dataset_ids: Mapping[str, str],
    expected_lineage: Mapping[str, JsonValue],
    *,
    decode_g2: bool = False,
    decode_target: bool = False,
    decode_rows: bool = True,
    decode_base: bool = False,
    child_kinds: Sequence[str] | None = None,
) -> dict[str, tuple[dict[str, JsonValue], ...]]:
    """Verify child bytes while decoding only explicitly requested rows."""

    kinds = _CHILD_KINDS if child_kinds is None else tuple(child_kinds)
    if set(children) != set(kinds):
        raise ValueError("IBKR foundation child set is incomplete or duplicated")
    decoded_kinds = (
        {
            "folds",
            "target-index",
            "causal-metadata",
            "blind-observations",
            "blind-panel",
            "pre-holdout-target",
        }
        if decode_rows
        else set()
    )
    if decode_base:
        decoded_kinds.update((*_BASE_CHILD_KINDS, "target-index", "causal-metadata"))
    if decode_g2:
        decoded_kinds.update(_G2_EXTENSION_CHILD_KINDS)
    if decode_target:
        decoded_kinds.add("targets")
    decoded: dict[str, tuple[dict[str, JsonValue], ...]] = {}
    expected_files: set[str] = set()
    child_root_names: set[str] = set()
    for kind in kinds:
        raw_parts = children[kind]
        if not isinstance(raw_parts, list) or not raw_parts:
            raise ValueError("IBKR foundation child parts are invalid")
        if len(raw_parts) > _MAX_CHILD_PARTS:
            raise ValueError("IBKR foundation child part count exceeds its bound")
        observed_rows: list[dict[str, JsonValue]] = []
        previous_path = ""
        for part_index, raw_part in enumerate(raw_parts):
            reference = _child_reference(raw_part, kind)
            manifest_reference = _text(reference["manifest_path"], "child manifest path")
            if manifest_reference <= previous_path:
                raise ValueError("IBKR foundation child references are not canonical")
            previous_path = manifest_reference
            child_root_names.add(PurePosixPath(manifest_reference).parts[0])
            manifest_path = _safe_child(
                bundle_root,
                manifest_reference,
                "IBKR foundation child manifest",
            )
            manifest_bytes = _bounded_bytes(
                manifest_path,
                _MAX_CHILD_MANIFEST_BYTES,
                "IBKR foundation child manifest",
            )
            manifest = _mapping(_parse_json(manifest_bytes, "IBKR foundation child manifest"))
            if set(manifest) != _CHILD_FIELDS:
                raise ValueError("IBKR foundation child manifest has unknown or missing fields")
            if manifest_bytes != _json_bytes(manifest) + b"\n":
                raise ValueError("IBKR foundation child manifest bytes are not canonical")
            if manifest["contract"] != _FOUNDATION_CHILD_CONTRACT:
                raise ValueError("IBKR foundation child contract is unsupported")
            if manifest["schema_version"] != _FOUNDATION_CHILD_SCHEMA_VERSION:
                raise ValueError("IBKR foundation child schema is unsupported")
            identity = dict(manifest)
            manifest_hash = _text(identity.pop("manifest_sha256"), "child manifest hash")
            if manifest_hash != _sha(identity):
                raise ValueError("IBKR foundation child manifest identity does not match")
            if manifest_hash != _text(reference["manifest_sha256"], "child manifest hash"):
                raise ValueError("IBKR foundation child manifest hash differs from its reference")
            if _text(manifest["kind"], "child kind") != kind:
                raise ValueError("IBKR foundation child kind differs from its reference")
            if _text(manifest["dataset_id"], "child dataset ID") != expected_dataset_ids[kind]:
                raise ValueError("IBKR foundation child dataset differs from its authority")
            if _int(manifest["part_index"], "child part index") != part_index:
                raise ValueError("IBKR foundation child part index is not contiguous")
            manifest_row_count = _int(manifest["row_count"], "child row count")
            if manifest_row_count < 0 or manifest_row_count > _MAX_CHILD_ROWS:
                raise ValueError("IBKR foundation child row count exceeds its bound")
            manifest_file = _text(manifest["file"], "IBKR foundation child Parquet path")
            manifest_lineage = _mapping(manifest["lineage"], "child lineage")
            if manifest_lineage != dict(expected_lineage):
                raise ValueError("IBKR foundation child lineage differs from its authority")
            expected_reference: dict[str, object] = {
                "kind": kind,
                "dataset_id": manifest["dataset_id"],
                "manifest_id": manifest_hash[:24],
                "manifest_path": manifest_reference,
                "manifest_sha256": manifest_hash,
                "row_count": manifest_row_count,
                "file": manifest_file,
                "file_sha256": manifest["file_sha256"],
            }
            if reference != expected_reference:
                raise ValueError("IBKR foundation child reference differs from its manifest")
            file_path = _safe_child(
                bundle_root,
                manifest_file,
                "IBKR foundation child Parquet",
            )
            parquet_bytes = _bounded_bytes(
                file_path,
                _MAX_CHILD_FILE_BYTES,
                "IBKR foundation child Parquet",
            )
            if hashlib.sha256(parquet_bytes).hexdigest() != _text(
                manifest["file_sha256"], "child Parquet hash"
            ):
                raise ValueError("IBKR foundation child Parquet bytes changed")
            if pl.read_parquet_schema(file_path) != {"payload": pl.String}:
                raise ValueError("IBKR foundation child Parquet schema is unsupported")
            if kind in decoded_kinds:
                rows = _read_child_rows(
                    file_path,
                    expected_row_count=manifest_row_count,
                )
                if _sha([_canonical_row(row) for row in rows]) != _text(
                    manifest["rows_sha256"], "child row hash"
                ):
                    raise ValueError("IBKR foundation child row identity does not match")
                observed_rows.extend(rows)
            expected_files.update({manifest_reference, manifest_file})
        if kind in decoded_kinds:
            decoded[kind] = tuple(observed_rows)

    if len(child_root_names) != 1:
        raise ValueError("IBKR foundation child references use multiple roots")
    child_root = bundle_root / next(iter(child_root_names))
    actual_files = {
        path.relative_to(bundle_root).as_posix() for path in child_root.rglob("*") if path.is_file()
    }
    if actual_files != expected_files:
        raise ValueError("IBKR foundation child closure contains unexpected files")
    return decoded


def _child_rows(build: IBKRFoundationBuild) -> dict[str, tuple[dict[str, JsonValue], ...]]:
    observations = cast(ObservationDataset, build.observations)
    panel = cast(PanelDataset, build.panel)
    targets = cast(TargetDataset, build.targets)
    if build.target_index is None or build.causal_metadata is None:
        raise ValueError("foundation outcome-blind extensions are unavailable")
    blind_observations = R2OutcomeBlindObservationView.from_dataset(
        observations,
        holdout_start=build.configuration.holdout_range[0],
    )
    blind_panel = R2OutcomeBlindPanelView.from_dataset(
        panel,
        holdout_start=build.configuration.holdout_range[0],
    )
    g2_observations = R2G2ObservationView.from_dataset(
        observations,
        holdout_range=build.configuration.holdout_range,
    )
    g2_panel = R2G2PanelView.from_dataset(
        panel,
        holdout_range=build.configuration.holdout_range,
    )
    pre_holdout_target = _pre_holdout_target_projection(build, targets)
    return {
        "observations": tuple(row.as_json() for row in observations.rows),
        "panel": tuple(row.as_json() for row in panel.rows),
        "targets": tuple(row.as_json() for row in targets.rows),
        "folds": tuple(row.as_json() for row in build.folds.folds),
        "target-index": tuple(item.as_json() for item in build.target_index.targets),
        "causal-metadata": tuple(item.as_json() for item in build.causal_metadata.rows),
        "blind-observations": tuple(row.as_json() for row in blind_observations.rows),
        "blind-panel": tuple(row.as_json() for row in blind_panel.rows),
        "g2-observations": tuple(row.as_json() for row in g2_observations.rows),
        "g2-panel": tuple(row.as_json() for row in g2_panel.rows),
        "pre-holdout-target": (pre_holdout_target.as_json(),),
    }


def _child_dataset_ids(build: IBKRFoundationBuild) -> dict[str, str]:
    observations = cast(ObservationDataset, build.observations)
    panel = cast(PanelDataset, build.panel)
    if build.target_index is None or build.causal_metadata is None:
        raise ValueError("foundation outcome-blind extensions are unavailable")
    blind_observations = R2OutcomeBlindObservationView.from_dataset(
        observations,
        holdout_start=build.configuration.holdout_range[0],
    )
    blind_panel = R2OutcomeBlindPanelView.from_dataset(
        panel,
        holdout_start=build.configuration.holdout_range[0],
    )
    g2_observations = R2G2ObservationView.from_dataset(
        observations,
        holdout_range=build.configuration.holdout_range,
    )
    g2_panel = R2G2PanelView.from_dataset(
        panel,
        holdout_range=build.configuration.holdout_range,
    )
    pre_holdout_target = _pre_holdout_target_projection(build, cast(TargetDataset, build.targets))
    return {
        "observations": observations.dataset_id,
        "panel": panel.dataset_id,
        "targets": build.targets.dataset_id,
        "folds": build.folds.dataset_id,
        "target-index": build.target_index.dataset_id,
        "causal-metadata": build.causal_metadata.dataset_id,
        "blind-observations": blind_observations.projection_id,
        "blind-panel": blind_panel.projection_id,
        "g2-observations": g2_observations.projection_id,
        "g2-panel": g2_panel.projection_id,
        "pre-holdout-target": pre_holdout_target.projection_id,
    }


def _pre_holdout_target_projection(
    build: IBKRFoundationBuild,
    targets: TargetDataset,
) -> R2PreHoldoutTargetProjection:
    return R2PreHoldoutTargetProjection.create_from_target_dataset(
        targets,
        holdout_start=build.configuration.holdout_range[0],
        primary_horizon_seconds=int(build.configuration.primary_vertical_horizon.total_seconds()),
        target_instruments=tuple(
            instrument_id
            for instrument_id in build.configuration.ordered_instruments
            if build.configuration.instrument_roles[instrument_id].value == "TARGET"
        ),
    )


def _canonical_row(row: Mapping[str, JsonValue]) -> str:
    return json.dumps(
        to_json_value(dict(row)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _parquet_bytes(payloads: Sequence[str]) -> bytes:
    buffer = io.BytesIO()
    pl.DataFrame(
        {"payload": list(payloads)},
        schema={"payload": pl.String},
    ).write_parquet(buffer)
    return buffer.getvalue()


def _read_child_rows(path: Path, *, expected_row_count: int) -> tuple[dict[str, JsonValue], ...]:
    if expected_row_count < 0 or expected_row_count > _MAX_CHILD_ROWS:
        raise ValueError("IBKR foundation child row count exceeds its bound")
    frame = pl.read_parquet(path)
    if frame.schema != {"payload": pl.String}:
        raise ValueError("IBKR foundation child Parquet schema is unsupported")
    values = frame.get_column("payload").to_list()
    if len(values) != expected_row_count:
        raise ValueError("IBKR foundation child row count differs from its manifest")
    rows: list[dict[str, JsonValue]] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("IBKR foundation child payload is not text")
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("IBKR foundation child row is not an object")
        if value != _canonical_row(parsed):
            raise ValueError("IBKR foundation child row is not canonical")
        rows.append(cast(dict[str, JsonValue], parsed))
    return tuple(rows)


def _child_reference(value: object, kind: str) -> dict[str, object]:
    reference = _mapping(value, "IBKR foundation child reference")
    if set(reference) != _REFERENCE_FIELDS:
        raise ValueError("IBKR foundation child reference has an unexpected schema")
    if reference["kind"] != kind:
        raise ValueError("IBKR foundation child reference kind is invalid")
    manifest_hash = _text(reference["manifest_sha256"], "child manifest hash")
    manifest_id = _text(reference["manifest_id"], "child manifest ID")
    if manifest_id != manifest_hash[:24]:
        raise ValueError("IBKR foundation child manifest ID is invalid")
    for field in ("dataset_id", "file_sha256"):
        _require_sha256(_text(reference[field], field), field)
    _require_sha256(manifest_hash, "child manifest hash")
    row_count = _int(reference["row_count"], "child row count")
    if row_count < 0 or row_count > _MAX_CHILD_ROWS:
        raise ValueError("IBKR foundation child row count exceeds its bound")
    _safe_relative(_text(reference["manifest_path"], "child manifest path"))
    _safe_relative(_text(reference["file"], "child Parquet path"))
    return reference


def _regular_file(path: Path, field: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be a regular non-symlink file")
    return path.resolve()


def _output_path(path: Path) -> Path:
    current = path if path.is_absolute() else Path.cwd() / path
    if ".." in current.parts:
        raise ValueError(f"IBKR foundation output path escapes its root: {path}")
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"IBKR foundation output path contains a symlink: {path}")
    return current


def _relative_path(root: Path, path: Path, field: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"{field} must be within the foundation root") from error
    _safe_relative(relative)
    return relative


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe relative foundation path: {value}")
    return path


def _safe_child(root: Path, relative: str, field: str) -> Path:
    _safe_relative(relative)
    child = root / PurePosixPath(relative)
    for ancestor in (child, *child.parents):
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise ValueError(f"{field} path contains a symlink: {relative}")
    if not child.is_file():
        raise FileNotFoundError(f"{field} is not a regular file: {relative}")
    return child


def _write_create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _bounded_bytes(path: Path, limit: int, field: str) -> bytes:
    data = path.read_bytes()
    if not data or len(data) > limit:
        raise ValueError(f"{field} exceeds its byte bound")
    return data


def _parse_json(payload: bytes, field: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field} is not valid JSON") from error


def _mapping(value: object, field: str = "object") -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


__all__ = [
    "authenticate_ibkr_foundation",
    "foundation_config_payload",
    "ibkr_foundation_verifier_identity",
    "load_ibkr_foundation",
    "preflight_ibkr_foundation",
    "verify_ibkr_foundation",
    "write_ibkr_foundation",
]
