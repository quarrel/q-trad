"""File-boundary loaders, verifiers and create-only writers for IBKR Stage 1."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, cast

from qtrad.application import ibkr_historical as ibkr_application
from qtrad.application.ibkr_historical import (
    build_ibkr_contract_selection,
    build_ibkr_historical_plan,
    build_ibkr_historical_request_profile,
    build_ibkr_runtime_lock,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import (
    IbkrAcquisitionRuntime,
    IbkrArchiveIdentity,
    IbkrContractDecision,
    IbkrContractFingerprint,
    IbkrContractSelection,
    IbkrContractSelectionDecision,
    IbkrHistoricalPacingPolicy,
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
    IbkrHistoricalRequestProfile,
    IbkrPlannedContract,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.runtime.ibkr_capability import ibkr_capability_probe_spec_from_document
from qtrad.runtime.universe import capture_candidates_from_document

_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_SELECTION_KEYS = {
    "contract",
    "schema_version",
    "capability_review_sha256",
    "catalogue_name",
    "catalogue_hash",
    "probe_spec_name",
    "probe_spec_hash",
    "api_version",
    "api_package_fingerprint",
    "frozen_by",
    "frozen_at",
    "decisions",
    "selection_sha256",
}
_RUNTIME_KEYS = {
    "contract",
    "schema_version",
    "gateway_version",
    "gateway_archive",
    "api_version",
    "api_archive",
    "ibc_version",
    "ibc_archive",
    "qtrad_commit",
    "qtrad_image_digest",
    "python_version",
    "library_versions",
    "gateway_configuration_identity",
    "paper_account_environment",
    "api_host",
    "api_port",
    "client_id_policy",
    "frozen_at",
    "runtime_sha256",
}
_FINGERPRINT_KEYS = {
    "con_id",
    "symbol",
    "security_type",
    "currency",
    "exchange",
    "primary_exchange",
    "local_symbol",
    "trading_class",
    "multiplier",
    "underlying_con_id",
    "contract_month",
}
_DECISION_KEYS = {
    "instrument_id",
    "decision",
    "acquisition_eligible",
    "fingerprint",
    "reason",
    "descriptive_metadata",
}


_REQUEST_PROFILE_KEYS = {
    "contract",
    "schema_version",
    "canary_evidence_filename",
    "canary_evidence_sha256",
    "canary_evidence_file_sha256",
    "canary_runtime_sha256",
    "canary_selection_sha256",
    "frozen_by",
    "frozen_at",
    "permitted_bar_durations",
    "permitted_schedule_durations",
    "bar_duration_by_asset_class",
    "schedule_duration",
    "maximum_in_flight_requests",
    "request_timeout_seconds",
    "retry_count",
    "duplicate_request_protection",
    "pacing_policy",
    "profile_sha256",
}
_PACING_POLICY_KEYS = {
    "identical_request_cooldown_seconds",
    "per_contract_window_seconds",
    "max_requests_per_contract_window",
    "rolling_window_seconds",
    "max_requests_per_rolling_window",
}
_PLAN_KEYS = {
    "contract",
    "schema_version",
    "contract_selection_sha256",
    "runtime_sha256",
    "request_profile_sha256",
    "provider",
    "environment",
    "planner_qtrad_commit",
    "planner_qtrad_image_digest",
    "start",
    "end",
    "eligible_contracts",
    "requests",
    "plan_sha256",
}
_PLANNED_CONTRACT_KEYS = {"instrument_id", "fingerprint"}
_REQUEST_KEYS = {
    "instrument_id",
    "fingerprint",
    "kind",
    "interval_start",
    "interval_end",
    "end_date_time",
    "duration",
    "bar_size",
    "what_to_show",
    "use_rth",
    "format_date",
    "keep_up_to_date",
    "request_sha256",
}


@dataclass(frozen=True, slots=True)
class IbkrHistoricalPlanVerification:
    """Verified lower-artifact closure and the deterministic plan it produces."""

    plan: IbkrHistoricalPlan
    selection: IbkrContractSelection
    runtime: IbkrAcquisitionRuntime
    request_profile: IbkrHistoricalRequestProfile


def load_ibkr_capability_review(
    path: Path, *, catalogue_path: Path, probe_spec_path: Path
) -> dict[str, object]:
    """Load a review and prove it covers the independently reloaded canonical closure."""
    from qtrad.application.ibkr_historical import _verify_capability_review

    candidates, probe_spec = _load_canonical_inputs(
        catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    document = _read_json_object(path, "IBKR capability review")
    _verify_capability_review(
        document,
        canonical_instrument_ids=frozenset(item.instrument_id for item in candidates.instruments),
        canonical_queries=frozenset(probe_spec.queries),
    )
    if (
        document["catalogue_name"] != candidates.name
        or document["catalogue_hash"] != candidates.configuration_hash
    ):
        raise ValueError("IBKR capability review targets a different canonical catalogue")
    if (
        document["probe_spec_name"] != probe_spec.name
        or document["probe_spec_hash"] != probe_spec.configuration_hash
    ):
        raise ValueError("IBKR capability review targets a different canonical probe specification")
    return document


def load_ibkr_operator_selection(path: Path) -> dict[str, object]:
    document = _read_json_document(path, "IBKR operator selection")
    _require_exact_keys(
        document,
        {"schema_version", "decisions", "capability_review_sha256"},
        "IBKR operator selection",
        allow_missing={"capability_review_sha256"},
    )
    if document.get("schema_version") != 1:
        raise ValueError("IBKR operator selection requires schema_version = 1")
    decisions_value = document.get("decisions")
    if not isinstance(decisions_value, list):
        raise ValueError("IBKR operator selection decisions must be an array")
    for decision in decisions_value:
        _validate_operator_decision(decision)
    return document


def build_ibkr_contract_selection_from_files(
    *,
    capability_review_path: Path,
    selection_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
    frozen_by: str,
    frozen_at: datetime,
) -> IbkrContractSelection:
    candidates, probe_spec = _load_canonical_inputs(
        catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    return build_ibkr_contract_selection(
        capability_review=load_ibkr_capability_review(
            capability_review_path, catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
        ),
        operator_selection=load_ibkr_operator_selection(selection_path),
        canonical_instrument_ids=frozenset(item.instrument_id for item in candidates.instruments),
        canonical_queries=frozenset(probe_spec.queries),
        frozen_by=frozen_by,
        frozen_at=frozen_at,
    )


def write_ibkr_contract_selection(path: Path, selection: IbkrContractSelection) -> None:
    _write_create_only(path, selection.as_json_value(), "IBKR contract selection")


def load_ibkr_contract_selection(path: Path) -> IbkrContractSelection:
    document = _read_json_object(path, "IBKR contract selection")
    _require_exact_keys(document, _SELECTION_KEYS, "IBKR contract selection")
    if (
        document.get("contract") != IbkrContractSelection.CONTRACT
        or document.get("schema_version") != IbkrContractSelection.SCHEMA_VERSION
    ):
        raise ValueError("IBKR contract selection contract or schema version is unsupported")
    values = document.get("decisions")
    if not isinstance(values, list):
        raise ValueError("IBKR contract selection decisions must be an array")
    selection = IbkrContractSelection(
        capability_review_sha256=_string(document, "capability_review_sha256"),
        catalogue_name=_string(document, "catalogue_name"),
        catalogue_hash=_string(document, "catalogue_hash"),
        probe_spec_name=_string(document, "probe_spec_name"),
        probe_spec_hash=_string(document, "probe_spec_hash"),
        api_version=_string(document, "api_version"),
        api_package_fingerprint=_string(document, "api_package_fingerprint"),
        frozen_by=_string(document, "frozen_by"),
        frozen_at=_datetime(document, "frozen_at"),
        decisions=tuple(_decision_from_json(item) for item in values),
        selection_sha256=_string(document, "selection_sha256"),
    )
    if selection.as_json_value() != document:
        raise ValueError("IBKR contract selection contains non-canonical fields")
    return selection


def verify_ibkr_contract_selection(
    path: Path,
    *,
    capability_review_path: Path,
    operator_selection_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
) -> IbkrContractSelection:
    """Replay a selection from the capability review and original operator decisions."""

    selection = load_ibkr_contract_selection(path)
    review = load_ibkr_capability_review(
        capability_review_path, catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    operator_selection = load_ibkr_operator_selection(operator_selection_path)
    candidates, probe_spec = _load_canonical_inputs(
        catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    rebuilt = build_ibkr_contract_selection(
        capability_review=review,
        operator_selection=operator_selection,
        canonical_instrument_ids=frozenset(item.instrument_id for item in candidates.instruments),
        canonical_queries=frozenset(probe_spec.queries),
        frozen_by=selection.frozen_by,
        frozen_at=selection.frozen_at,
    )
    if rebuilt.as_json_value() != selection.as_json_value():
        raise ValueError("IBKR contract selection does not replay from its authenticated inputs")
    return selection


def build_ibkr_runtime_lock_from_files(
    *,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    gateway_version: str,
    api_version: str,
    ibc_version: str,
    qtrad_image_digest: str,
    frozen_at: datetime,
    api_host: str = "127.0.0.1",
    api_port: int = 4002,
    client_id_policy: str = "DEDICATED_NONZERO_CLIENT_ID",
) -> IbkrAcquisitionRuntime:
    return build_ibkr_runtime_lock(
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        gateway_version=gateway_version,
        api_version=api_version,
        ibc_version=ibc_version,
        qtrad_image_digest=qtrad_image_digest,
        frozen_at=frozen_at,
        api_host=api_host,
        api_port=api_port,
        client_id_policy=client_id_policy,
    )


def write_ibkr_runtime_lock(path: Path, runtime: IbkrAcquisitionRuntime) -> None:
    _write_create_only(path, runtime.as_json_value(), "IBKR runtime lock")


def load_ibkr_runtime_lock(path: Path) -> IbkrAcquisitionRuntime:
    document = _read_json_object(path, "IBKR runtime lock")
    _require_exact_keys(document, _RUNTIME_KEYS, "IBKR runtime lock")
    if (
        document.get("contract") != IbkrAcquisitionRuntime.CONTRACT
        or document.get("schema_version") != IbkrAcquisitionRuntime.SCHEMA_VERSION
    ):
        raise ValueError("IBKR runtime lock contract or schema version is unsupported")
    runtime = IbkrAcquisitionRuntime(
        gateway_version=_string(document, "gateway_version"),
        gateway_archive=_archive_from_json(document.get("gateway_archive")),
        api_version=_string(document, "api_version"),
        api_archive=_archive_from_json(document.get("api_archive")),
        ibc_version=_string(document, "ibc_version"),
        ibc_archive=_archive_from_json(document.get("ibc_archive")),
        qtrad_commit=_string(document, "qtrad_commit"),
        qtrad_image_digest=_string(document, "qtrad_image_digest"),
        python_version=_string(document, "python_version"),
        library_versions=_string_mapping(document.get("library_versions"), "library_versions"),
        gateway_configuration_identity=_string(document, "gateway_configuration_identity"),
        paper_account_environment=_string(document, "paper_account_environment"),
        api_host=_string(document, "api_host"),
        api_port=_integer(document, "api_port"),
        client_id_policy=_string(document, "client_id_policy"),
        frozen_at=_datetime(document, "frozen_at"),
        runtime_sha256=_string(document, "runtime_sha256"),
    )
    if runtime.as_json_value() != document:
        raise ValueError("IBKR runtime lock contains non-canonical fields")
    return runtime


def verify_ibkr_runtime_lock(
    path: Path,
    *,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    expected_gateway_sha256: str,
    expected_api_sha256: str,
    expected_ibc_sha256: str,
    expected_qtrad_commit: str,
    expected_image_digest: str,
    expected_gateway_version: str,
    expected_api_version: str,
    expected_ibc_version: str,
    expected_api_host: str,
    expected_api_port: int,
    expected_client_id_policy: str,
) -> IbkrAcquisitionRuntime:
    """Replay runtime identity against explicit authoritative attestations."""

    runtime = load_ibkr_runtime_lock(path)
    observed_commit = ibkr_application.derive_qtrad_commit()
    if observed_commit != expected_qtrad_commit:
        raise ValueError(
            "IBKR runtime verifier q-trad commit does not match the observed clean source commit"
        )
    if runtime.qtrad_commit != expected_qtrad_commit:
        raise ValueError(
            "IBKR runtime lock q-trad commit does not match its authoritative attestation"
        )
    expected = (expected_gateway_sha256, expected_api_sha256, expected_ibc_sha256)
    actual_paths = (gateway_archive, api_archive, ibc_archive)
    locked = (runtime.gateway_archive, runtime.api_archive, runtime.ibc_archive)
    for role, archive_path, identity, expected_hash in zip(
        ("Gateway", "API", "IBC"), actual_paths, locked, expected, strict=True
    ):
        _require_sha256(expected_hash, f"expected {role} archive hash")
        _require_readable_path(archive_path, f"IBKR {role} archive")
        if archive_path.name != identity.filename:
            raise ValueError(f"IBKR {role} archive filename does not match the runtime lock")
        observed = _sha256_file(archive_path)
        if observed != expected_hash or identity.sha256 != expected_hash:
            raise ValueError(
                f"IBKR {role} archive does not match its authoritative role attestation"
            )
    rebuilt = ibkr_application._rebuild_ibkr_runtime_lock(
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        gateway_version=expected_gateway_version,
        api_version=expected_api_version,
        ibc_version=expected_ibc_version,
        qtrad_commit=observed_commit,
        qtrad_image_digest=expected_image_digest,
        frozen_at=runtime.frozen_at,
        api_host=expected_api_host,
        api_port=expected_api_port,
        client_id_policy=expected_client_id_policy,
    )
    if rebuilt.as_json_value() != runtime.as_json_value():
        raise ValueError("IBKR runtime lock does not replay from the actual runtime identity")
    return runtime


def load_ibkr_historical_request_profile(path: Path) -> IbkrHistoricalRequestProfile:
    """Load the canonical profile structure; evidence authentication is a separate verifier."""

    document = _read_json_object(path, "IBKR historical request profile")
    _require_exact_keys(document, _REQUEST_PROFILE_KEYS, "IBKR historical request profile")
    if (
        document.get("contract") != IbkrHistoricalRequestProfile.CONTRACT
        or document.get("schema_version") != IbkrHistoricalRequestProfile.SCHEMA_VERSION
    ):
        raise ValueError(
            "IBKR historical request profile contract or schema version is unsupported"
        )
    profile = build_ibkr_historical_request_profile(
        canary_evidence_filename=_string(document, "canary_evidence_filename"),
        canary_evidence_sha256=_string(document, "canary_evidence_sha256"),
        canary_evidence_file_sha256=_string(document, "canary_evidence_file_sha256"),
        canary_runtime_sha256=_string(document, "canary_runtime_sha256"),
        canary_selection_sha256=_string(document, "canary_selection_sha256"),
        frozen_by=_string(document, "frozen_by"),
        frozen_at=_datetime(document, "frozen_at"),
        permitted_bar_durations=_string_tuple(
            document.get("permitted_bar_durations"), "IBKR permitted bar durations"
        ),
        permitted_schedule_durations=_string_tuple(
            document.get("permitted_schedule_durations"), "IBKR permitted schedule durations"
        ),
        bar_duration_by_asset_class=_asset_class_durations(
            document.get("bar_duration_by_asset_class")
        ),
        schedule_duration=_string(document, "schedule_duration"),
        maximum_in_flight_requests=_integer(document, "maximum_in_flight_requests"),
        request_timeout_seconds=_integer(document, "request_timeout_seconds"),
        retry_count=_integer(document, "retry_count"),
        duplicate_request_protection=_string(document, "duplicate_request_protection"),
        pacing_policy=_pacing_policy_from_json(document.get("pacing_policy")),
    )
    if (
        profile.profile_sha256 != _string(document, "profile_sha256")
        or profile.as_json_value() != document
    ):
        raise ValueError("IBKR historical request profile contains non-canonical fields")
    return profile


def verify_ibkr_historical_request_profile(
    path: Path,
    *,
    canary_evidence_path: Path,
    expected_runtime_sha256: str,
    expected_selection_sha256: str,
    selection: IbkrContractSelection,
    asset_class_by_instrument: Mapping[InstrumentId, AssetClass],
    expected_frozen_by: str,
    expected_frozen_at: datetime,
    maximum_in_flight_requests: int,
    request_timeout_seconds: int,
    retry_count: int,
    duplicate_request_protection: str,
    pacing_policy: IbkrHistoricalPacingPolicy,
) -> IbkrHistoricalRequestProfile:
    """Replay the canary and rebuild the complete request profile from trusted inputs."""
    profile = load_ibkr_historical_request_profile(path)
    _require_readable_path(canary_evidence_path, "IBKR canary evidence")
    if canary_evidence_path.name != profile.canary_evidence_filename:
        raise ValueError("IBKR canary evidence filename does not match the request profile")
    serialized_sha256 = _sha256_file(canary_evidence_path)
    if serialized_sha256 != profile.canary_evidence_file_sha256:
        raise ValueError("IBKR serialized canary evidence does not match the request profile")
    from qtrad.application.ibkr_canary import (
        freeze_ibkr_request_profile_from_canary,
        validate_ibkr_historical_canary_selection,
    )
    from qtrad.runtime.ibkr_canary import verify_ibkr_historical_canary_evidence

    evidence = verify_ibkr_historical_canary_evidence(
        canary_evidence_path,
        expected_runtime_sha256=expected_runtime_sha256,
        expected_selection_sha256=expected_selection_sha256,
    )
    if evidence.evidence_sha256 != profile.canary_evidence_sha256:
        raise ValueError("IBKR semantic canary evidence does not match the request profile")
    validate_ibkr_historical_canary_selection(
        evidence,
        selection=selection,
        asset_class_by_instrument=asset_class_by_instrument,
    )
    rebuilt = freeze_ibkr_request_profile_from_canary(
        evidence,
        canary_evidence_filename=profile.canary_evidence_filename,
        canary_evidence_file_sha256=serialized_sha256,
        frozen_by=expected_frozen_by,
        frozen_at=expected_frozen_at,
        maximum_in_flight_requests=maximum_in_flight_requests,
        request_timeout_seconds=request_timeout_seconds,
        retry_count=retry_count,
        duplicate_request_protection=duplicate_request_protection,
        pacing_policy=pacing_policy,
    )
    if profile.as_json_value() != rebuilt.as_json_value():
        raise ValueError("IBKR request profile does not rebuild from verified canary and policy")
    return profile


def write_ibkr_historical_request_profile(
    path: Path, profile: IbkrHistoricalRequestProfile
) -> None:
    _write_create_only(path, profile.as_json_value(), "IBKR historical request profile")


def build_ibkr_historical_plan_from_files(
    *,
    contract_selection_path: Path,
    operator_selection_path: Path,
    capability_review_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
    runtime_lock_path: Path,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    expected_gateway_sha256: str,
    expected_api_sha256: str,
    expected_ibc_sha256: str,
    expected_runtime_qtrad_commit: str,
    expected_runtime_image_digest: str,
    expected_gateway_version: str,
    expected_api_version: str,
    expected_ibc_version: str,
    expected_api_host: str,
    expected_api_port: int,
    expected_client_id_policy: str,
    request_profile_path: Path,
    canary_evidence_path: Path,
    expected_profile_frozen_by: str,
    expected_profile_frozen_at: datetime,
    maximum_in_flight_requests: int,
    request_timeout_seconds: int,
    retry_count: int,
    duplicate_request_protection: str,
    pacing_policy: IbkrHistoricalPacingPolicy,
    start: datetime,
    end: datetime,
    planner_qtrad_commit: str,
    planner_qtrad_image_digest: str,
) -> IbkrHistoricalPlan:
    """Compose the planner only from independently verified lower-layer artefacts."""
    return verify_ibkr_historical_plan_closure(
        contract_selection_path=contract_selection_path,
        operator_selection_path=operator_selection_path,
        capability_review_path=capability_review_path,
        catalogue_path=catalogue_path,
        probe_spec_path=probe_spec_path,
        runtime_lock_path=runtime_lock_path,
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        expected_gateway_sha256=expected_gateway_sha256,
        expected_api_sha256=expected_api_sha256,
        expected_ibc_sha256=expected_ibc_sha256,
        expected_runtime_qtrad_commit=expected_runtime_qtrad_commit,
        expected_runtime_image_digest=expected_runtime_image_digest,
        expected_gateway_version=expected_gateway_version,
        expected_api_version=expected_api_version,
        expected_ibc_version=expected_ibc_version,
        expected_api_host=expected_api_host,
        expected_api_port=expected_api_port,
        expected_client_id_policy=expected_client_id_policy,
        request_profile_path=request_profile_path,
        canary_evidence_path=canary_evidence_path,
        expected_profile_frozen_by=expected_profile_frozen_by,
        expected_profile_frozen_at=expected_profile_frozen_at,
        maximum_in_flight_requests=maximum_in_flight_requests,
        request_timeout_seconds=request_timeout_seconds,
        retry_count=retry_count,
        duplicate_request_protection=duplicate_request_protection,
        identical_request_cooldown_seconds=pacing_policy.identical_request_cooldown_seconds,
        max_requests_per_contract_window=pacing_policy.max_requests_per_contract_window,
        max_requests_per_rolling_window=pacing_policy.max_requests_per_rolling_window,
        start=start,
        end=end,
        planner_qtrad_commit=planner_qtrad_commit,
        planner_qtrad_image_digest=planner_qtrad_image_digest,
    ).plan


def verify_ibkr_historical_plan_closure(
    *,
    contract_selection_path: Path,
    operator_selection_path: Path,
    capability_review_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
    runtime_lock_path: Path,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    expected_gateway_sha256: str,
    expected_api_sha256: str,
    expected_ibc_sha256: str,
    expected_runtime_qtrad_commit: str,
    expected_runtime_image_digest: str,
    expected_gateway_version: str,
    expected_api_version: str,
    expected_ibc_version: str,
    expected_api_host: str,
    expected_api_port: int,
    expected_client_id_policy: str,
    request_profile_path: Path,
    canary_evidence_path: Path,
    expected_profile_frozen_by: str,
    expected_profile_frozen_at: datetime,
    maximum_in_flight_requests: int,
    request_timeout_seconds: int,
    retry_count: int,
    duplicate_request_protection: str,
    identical_request_cooldown_seconds: int,
    max_requests_per_contract_window: int,
    max_requests_per_rolling_window: int,
    start: datetime,
    end: datetime,
    planner_qtrad_commit: str,
    planner_qtrad_image_digest: str,
) -> IbkrHistoricalPlanVerification:
    """Verify the complete lower-artifact closure and rebuild its exact plan."""
    candidates, _ = _load_canonical_inputs(
        catalogue_path=catalogue_path, probe_spec_path=probe_spec_path
    )
    selection = verify_ibkr_contract_selection(
        contract_selection_path,
        capability_review_path=capability_review_path,
        operator_selection_path=operator_selection_path,
        catalogue_path=catalogue_path,
        probe_spec_path=probe_spec_path,
    )
    if candidates.configuration_hash != selection.catalogue_hash:
        raise ValueError("IBKR catalogue changed while reconstructing the historical plan")
    asset_class_by_instrument = {
        instrument.instrument_id: instrument.asset_class for instrument in candidates.instruments
    }
    runtime = verify_ibkr_runtime_lock(
        runtime_lock_path,
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        expected_gateway_sha256=expected_gateway_sha256,
        expected_api_sha256=expected_api_sha256,
        expected_ibc_sha256=expected_ibc_sha256,
        expected_qtrad_commit=expected_runtime_qtrad_commit,
        expected_image_digest=expected_runtime_image_digest,
        expected_gateway_version=expected_gateway_version,
        expected_api_version=expected_api_version,
        expected_ibc_version=expected_ibc_version,
        expected_api_host=expected_api_host,
        expected_api_port=expected_api_port,
        expected_client_id_policy=expected_client_id_policy,
    )
    request_profile = verify_ibkr_historical_request_profile(
        request_profile_path,
        canary_evidence_path=canary_evidence_path,
        expected_runtime_sha256=runtime.runtime_sha256,
        expected_selection_sha256=selection.selection_sha256,
        selection=selection,
        asset_class_by_instrument=asset_class_by_instrument,
        expected_frozen_by=expected_profile_frozen_by,
        expected_frozen_at=expected_profile_frozen_at,
        maximum_in_flight_requests=maximum_in_flight_requests,
        request_timeout_seconds=request_timeout_seconds,
        retry_count=retry_count,
        duplicate_request_protection=duplicate_request_protection,
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds,
            2,
            max_requests_per_contract_window,
            600,
            max_requests_per_rolling_window,
        ),
    )
    plan = build_ibkr_historical_plan(
        selection=selection,
        runtime=runtime,
        request_profile=request_profile,
        asset_class_by_instrument=asset_class_by_instrument,
        start=start,
        end=end,
        planner_qtrad_commit=planner_qtrad_commit,
        planner_qtrad_image_digest=planner_qtrad_image_digest,
    )
    return IbkrHistoricalPlanVerification(plan, selection, runtime, request_profile)


def write_ibkr_historical_plan(path: Path, plan: IbkrHistoricalPlan) -> None:
    _write_create_only(path, plan.as_json_value(), "IBKR historical plan")


def load_ibkr_historical_plan_artifact(path: Path) -> tuple[IbkrHistoricalPlan, bytes]:
    """Load one plan once and retain the exact bytes that were verified."""
    encoded = _read_bounded_bytes(path, "IBKR historical plan")
    return load_ibkr_historical_plan_bytes(encoded), encoded


def load_ibkr_historical_plan(path: Path) -> IbkrHistoricalPlan:
    """Independently load and structurally replay an exact, database-free request plan."""
    return load_ibkr_historical_plan_artifact(path)[0]


def load_ibkr_historical_plan_bytes(encoded: bytes) -> IbkrHistoricalPlan:
    """Load a bounded registered plan without trusting mutable filesystem state."""

    if not encoded:
        raise ValueError("IBKR historical plan bytes cannot be empty")
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError("IBKR historical plan bytes exceed their bounded size")
    try:
        parsed = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("IBKR historical plan bytes are invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("IBKR historical plan bytes must contain an object")
    return _load_ibkr_historical_plan_document(
        cast(dict[str, object], parsed),
        "IBKR historical plan bytes",
    )


def _load_ibkr_historical_plan_document(
    document: dict[str, object],
    label: str,
) -> IbkrHistoricalPlan:
    _require_exact_keys(document, _PLAN_KEYS, label)
    if (
        document.get("contract") != IbkrHistoricalPlan.CONTRACT
        or document.get("schema_version") != IbkrHistoricalPlan.SCHEMA_VERSION
    ):
        raise ValueError(f"{label} contract or schema version is unsupported")
    eligible_values = document.get("eligible_contracts")
    request_values = document.get("requests")
    if not isinstance(eligible_values, list) or not isinstance(request_values, list):
        raise ValueError(f"{label} contracts and requests must be arrays")
    plan = IbkrHistoricalPlan(
        contract_selection_sha256=_string(document, "contract_selection_sha256"),
        runtime_sha256=_string(document, "runtime_sha256"),
        request_profile_sha256=_string(document, "request_profile_sha256"),
        provider=_string(document, "provider"),
        environment=_string(document, "environment"),
        planner_qtrad_commit=_string(document, "planner_qtrad_commit"),
        planner_qtrad_image_digest=_string(document, "planner_qtrad_image_digest"),
        start=_datetime(document, "start"),
        end=_datetime(document, "end"),
        eligible_contracts=tuple(_planned_contract_from_json(value) for value in eligible_values),
        requests=tuple(_historical_request_from_json(value) for value in request_values),
        plan_sha256=_string(document, "plan_sha256"),
    )
    if plan.as_json_value() != document:
        raise ValueError(f"{label} contains non-canonical fields")
    return plan


def verify_ibkr_historical_plan(
    path: Path,
    *,
    contract_selection_path: Path,
    operator_selection_path: Path,
    capability_review_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
    runtime_lock_path: Path,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    expected_gateway_sha256: str,
    expected_api_sha256: str,
    expected_ibc_sha256: str,
    expected_runtime_qtrad_commit: str,
    expected_runtime_image_digest: str,
    expected_gateway_version: str,
    expected_api_version: str,
    expected_ibc_version: str,
    expected_api_host: str,
    expected_api_port: int,
    expected_client_id_policy: str,
    request_profile_path: Path,
    canary_evidence_path: Path,
    expected_profile_frozen_by: str,
    expected_profile_frozen_at: datetime,
    maximum_in_flight_requests: int,
    request_timeout_seconds: int,
    retry_count: int,
    duplicate_request_protection: str,
    pacing_policy: IbkrHistoricalPacingPolicy,
    expected_start: datetime,
    expected_end: datetime,
    planner_qtrad_commit: str,
    planner_qtrad_image_digest: str,
) -> IbkrHistoricalPlan:
    """Replay the plan and its authenticated lower-artifact closure from files only."""

    observed = load_ibkr_historical_plan(path)
    verified = verify_ibkr_historical_plan_closure(
        contract_selection_path=contract_selection_path,
        operator_selection_path=operator_selection_path,
        capability_review_path=capability_review_path,
        catalogue_path=catalogue_path,
        probe_spec_path=probe_spec_path,
        runtime_lock_path=runtime_lock_path,
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        expected_gateway_sha256=expected_gateway_sha256,
        expected_api_sha256=expected_api_sha256,
        expected_ibc_sha256=expected_ibc_sha256,
        expected_runtime_qtrad_commit=expected_runtime_qtrad_commit,
        expected_runtime_image_digest=expected_runtime_image_digest,
        expected_gateway_version=expected_gateway_version,
        expected_api_version=expected_api_version,
        expected_ibc_version=expected_ibc_version,
        expected_api_host=expected_api_host,
        expected_api_port=expected_api_port,
        expected_client_id_policy=expected_client_id_policy,
        request_profile_path=request_profile_path,
        canary_evidence_path=canary_evidence_path,
        expected_profile_frozen_by=expected_profile_frozen_by,
        expected_profile_frozen_at=expected_profile_frozen_at,
        maximum_in_flight_requests=maximum_in_flight_requests,
        request_timeout_seconds=request_timeout_seconds,
        retry_count=retry_count,
        duplicate_request_protection=duplicate_request_protection,
        identical_request_cooldown_seconds=pacing_policy.identical_request_cooldown_seconds,
        max_requests_per_contract_window=pacing_policy.max_requests_per_contract_window,
        max_requests_per_rolling_window=pacing_policy.max_requests_per_rolling_window,
        start=expected_start,
        end=expected_end,
        planner_qtrad_commit=planner_qtrad_commit,
        planner_qtrad_image_digest=planner_qtrad_image_digest,
    )
    if verified.plan.as_json_value() != observed.as_json_value():
        raise ValueError(
            "IBKR historical plan does not replay from its authenticated lower artefacts"
        )
    return observed


def _asset_class_durations(value: object) -> dict[AssetClass, str]:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR bar durations by asset class must be an object")
    raw = cast(Mapping[str, object], value)
    result: dict[AssetClass, str] = {}
    for key, duration in raw.items():
        if not isinstance(key, str):
            raise ValueError("IBKR bar duration asset class must be a string")
        try:
            asset_class = AssetClass(key)
        except ValueError as error:
            raise ValueError("IBKR bar duration asset class is unsupported") from error
        if asset_class in result:
            raise ValueError("IBKR bar duration asset classes must be unique")
        if not isinstance(duration, str):
            raise ValueError("IBKR bar duration must be a string")
        result[asset_class] = duration
    return result


def _pacing_policy_from_json(value: object) -> IbkrHistoricalPacingPolicy:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR pacing policy must be an object")
    policy = cast(Mapping[str, object], value)
    _require_exact_keys(policy, _PACING_POLICY_KEYS, "IBKR pacing policy")
    return IbkrHistoricalPacingPolicy(
        identical_request_cooldown_seconds=_integer(policy, "identical_request_cooldown_seconds"),
        per_contract_window_seconds=_integer(policy, "per_contract_window_seconds"),
        max_requests_per_contract_window=_integer(policy, "max_requests_per_contract_window"),
        rolling_window_seconds=_integer(policy, "rolling_window_seconds"),
        max_requests_per_rolling_window=_integer(policy, "max_requests_per_rolling_window"),
    )


def _planned_contract_from_json(value: object) -> IbkrPlannedContract:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR planned contract must be an object")
    item = cast(Mapping[str, object], value)
    _require_exact_keys(item, _PLANNED_CONTRACT_KEYS, "IBKR planned contract")
    return IbkrPlannedContract(
        instrument_id=InstrumentId(_string(item, "instrument_id")),
        fingerprint=_fingerprint_from_json(item.get("fingerprint")),
    )


def _historical_request_from_json(value: object) -> IbkrHistoricalRequest:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR historical request must be an object")
    item = cast(Mapping[str, object], value)
    _require_exact_keys(item, _REQUEST_KEYS, "IBKR historical request")
    try:
        kind = IbkrHistoricalRequestKind(_string(item, "kind"))
    except ValueError as error:
        raise ValueError("IBKR historical request kind is invalid") from error
    use_rth = item.get("use_rth")
    keep_up_to_date = item.get("keep_up_to_date")
    if not isinstance(use_rth, bool) or not isinstance(keep_up_to_date, bool):
        raise ValueError("IBKR historical request booleans are invalid")
    return IbkrHistoricalRequest(
        instrument_id=InstrumentId(_string(item, "instrument_id")),
        fingerprint=_fingerprint_from_json(item.get("fingerprint")),
        kind=kind,
        interval_start=_datetime(item, "interval_start"),
        interval_end=_datetime(item, "interval_end"),
        end_date_time=_string(item, "end_date_time"),
        duration=_string(item, "duration"),
        bar_size=_nullable_string(item, "bar_size"),
        what_to_show=_nullable_string(item, "what_to_show"),
        use_rth=use_rth,
        format_date=_optional_integer(item, "format_date"),
        keep_up_to_date=keep_up_to_date,
        request_sha256=_string(item, "request_sha256"),
    )


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    return tuple(cast(list[str], value))


def _nullable_string(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string or null")
    return item


def _load_canonical_inputs(*, catalogue_path: Path, probe_spec_path: Path):
    candidates = capture_candidates_from_document(
        _read_toml_object(catalogue_path, "IBKR canonical catalogue")
    )
    probe_spec = ibkr_capability_probe_spec_from_document(
        _read_toml_object(probe_spec_path, "IBKR capability probe specification")
    )
    return candidates, probe_spec


def _read_json_document(path: Path, label: str) -> dict[str, object]:
    if path.suffix.lower() == ".toml":
        return _read_toml_object(path, label)
    return _read_json_object(path, label)


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    encoded = _read_bounded_bytes(path, label)
    try:
        parsed = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], parsed)


def _read_toml_object(path: Path, label: str) -> dict[str, object]:
    encoded = _read_bounded_bytes(path, label)
    try:
        parsed = tomllib.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{label} is invalid") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], parsed)


def _read_bounded_bytes(path: Path, label: str) -> bytes:
    _require_readable_path(path, label)
    if path.stat().st_size > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} exceeds its bounded size")
    with path.open("rb") as source:
        encoded = source.read(_MAX_ARTIFACT_BYTES + 1)
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} exceeds its bounded size")
    return encoded


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_bounded_json(payload: Mapping[str, JsonValue], label: str) -> bytes:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} exceeds its bounded size")
    return encoded


@dataclass(slots=True)
class CreateOnlyOutputReservation:
    """Own a create-only artifact path until bounded JSON is published or aborted."""

    path: Path
    _output: BinaryIO
    _device: int
    _inode: int
    _published: bool = False

    @property
    def published(self) -> bool:
        return self._published

    def __enter__(self) -> CreateOnlyOutputReservation:
        return self

    def __exit__(self, *_: object) -> None:
        if not self._published:
            self.abort()

    def publish(self, payload: Mapping[str, JsonValue], label: str) -> None:
        if self._published:
            raise RuntimeError(f"{label} output was already published: {self.path}")
        _require_output_path(self.path, label)
        self._require_ownership(label)
        encoded = _encode_bounded_json(payload, label)
        self._output.seek(0)
        self._output.write(encoded)
        self._output.flush()
        self._output.close()
        self._published = True

    def abort(self) -> None:
        if not self._output.closed:
            self._output.close()
        if self._published:
            return
        try:
            current = self.path.stat()
        except FileNotFoundError:
            return
        if current.st_dev == self._device and current.st_ino == self._inode:
            self.path.unlink()

    def _require_ownership(self, label: str) -> None:
        if self.path.is_symlink():
            raise ValueError(f"{label} output cannot be a symlink: {self.path}")
        try:
            current = self.path.stat()
        except FileNotFoundError as error:
            raise FileNotFoundError(
                f"{label} output reservation is missing: {self.path}"
            ) from error
        if current.st_dev != self._device or current.st_ino != self._inode:
            raise RuntimeError(f"{label} output reservation is no longer owned: {self.path}")


def reserve_create_only_output(path: Path, label: str) -> CreateOnlyOutputReservation:
    _require_output_path(path, label)
    try:
        output = path.open("x+b")
    except FileExistsError as error:
        raise FileExistsError(f"{label} output already exists: {path}") from error
    identity = os.fstat(output.fileno())
    return CreateOnlyOutputReservation(
        path=path,
        _output=output,
        _device=identity.st_dev,
        _inode=identity.st_ino,
    )


def _write_create_only(path: Path, payload: Mapping[str, JsonValue], label: str) -> None:
    _require_output_path(path, label)
    encoded = _encode_bounded_json(payload, label)
    try:
        with path.open("xb") as output:
            output.write(encoded)
    except FileExistsError as error:
        raise FileExistsError(f"{label} output already exists: {path}") from error


def _decision_from_json(value: object) -> IbkrContractSelectionDecision:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR selection decision must be an object")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(value, _DECISION_KEYS, "IBKR contract selection decision")
    fingerprint_value = value.get("fingerprint")
    fingerprint = None if fingerprint_value is None else _fingerprint_from_json(fingerprint_value)
    metadata = value.get("descriptive_metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("IBKR selection descriptive_metadata must be an object")
    try:
        decision = IbkrContractDecision(_string(value, "decision"))
    except ValueError as error:
        raise ValueError("IBKR selection decision value is invalid") from error
    eligible = value.get("acquisition_eligible")
    if not isinstance(eligible, bool):
        raise ValueError("IBKR selection acquisition_eligible must be boolean")
    reason = value.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("IBKR selection reason must be a string or null")
    return IbkrContractSelectionDecision(
        instrument_id=InstrumentId(_string(value, "instrument_id")),
        decision=decision,
        acquisition_eligible=eligible,
        fingerprint=fingerprint,
        reason=reason,
        descriptive_metadata=dict(cast(Mapping[str, JsonValue], metadata)),
    )


def _fingerprint_from_json(value: object) -> IbkrContractFingerprint:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR contract fingerprint must be an object")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(value, _FINGERPRINT_KEYS, "IBKR contract fingerprint")
    return IbkrContractFingerprint(
        con_id=_integer(value, "con_id"),
        symbol=_string(value, "symbol"),
        security_type=_string(value, "security_type"),
        currency=_string(value, "currency"),
        exchange=_string(value, "exchange"),
        primary_exchange=_optional_string(value, "primary_exchange"),
        local_symbol=_string(value, "local_symbol"),
        trading_class=_optional_string(value, "trading_class"),
        multiplier=_optional_string(value, "multiplier"),
        underlying_con_id=_optional_integer(value, "underlying_con_id"),
        contract_month=_optional_string(value, "contract_month"),
    )


def _validate_operator_decision(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR operator selection decisions must be objects")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(
        value,
        _DECISION_KEYS,
        "IBKR operator selection decision",
        allow_missing={"reason", "descriptive_metadata"},
    )
    if value.get("fingerprint") is not None:
        _fingerprint_from_json(value.get("fingerprint"))
    if not isinstance(value.get("acquisition_eligible"), bool):
        raise ValueError("IBKR operator selection acquisition_eligible must be boolean")


def _archive_from_json(value: object) -> IbkrArchiveIdentity:
    if not isinstance(value, Mapping):
        raise ValueError("IBKR runtime archive must be an object")
    value = cast(Mapping[str, object], value)
    _require_exact_keys(value, {"filename", "sha256"}, "IBKR runtime archive")
    return IbkrArchiveIdentity(filename=_string(value, "filename"), sha256=_string(value, "sha256"))


def _string_mapping(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise ValueError(f"{field} must contain non-empty string pairs")
        result[key] = item
    return result


def _string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item


def _optional_string(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string when present")
    return item


def _integer(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field} must be an integer")
    return item


def _optional_integer(value: Mapping[str, object], field: str) -> int | None:
    item = value.get(field)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field} must be an integer when present")
    return item


def _datetime(value: Mapping[str, object], field: str) -> datetime:
    item = _string(value, field)
    try:
        parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field} must use UTC")
    return parsed


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lower-case SHA-256 digest")


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    context: str,
    *,
    allow_missing: set[str] | None = None,
) -> None:
    allowed_missing = allow_missing or set()
    if set(value) - expected or (expected - allowed_missing) - set(value):
        raise ValueError(f"{context} has unknown or missing fields")


def _require_readable_path(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise ValueError(f"{label} path escapes its artifact root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"{label} path contains a symlink: {path}")
    if not current.is_file():
        raise FileNotFoundError(f"{label} is not a regular file: {path}")


def _require_output_path(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise ValueError(f"{label} output path escapes its artifact root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    if current.exists() and current.is_symlink():
        raise ValueError(f"{label} output cannot be a symlink: {path}")
    for ancestor in current.parents:
        if ancestor.is_symlink():
            raise ValueError(f"{label} output path contains a symlink: {path}")
    if not current.parent.is_dir():
        raise FileNotFoundError(f"{label} output directory does not exist: {current.parent}")
