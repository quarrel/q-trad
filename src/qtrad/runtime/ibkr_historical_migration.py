"""Retained-file-only PR-H4 migration and equivalence orchestration.

This module is intentionally migration-only.  Normal Stage 6/7/8 writers, readers,
verification commands and promotion APIs do not import it.  The real retained-file
migration is a separately authorised operation; tests exercise only disposable
fixtures.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from qtrad.application.ibkr_foundation import IBKRFoundationBuild
from qtrad.application.ibkr_historical import derive_qtrad_commit
from qtrad.application.ibkr_results import (
    build_ibkr_historical_aggregate_result,
    replay_ibkr_historical_request_result,
)
from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_results import (
    IbkrHistoricalRequestResult,
    IbkrHistoricalResultArtifact,
    canonical_json_bytes,
)
from qtrad.runtime.ibkr_foundation import (
    _authenticate_foundation_manifest,
    _authenticate_ibkr_foundation_migration_v2,
    _AuthenticatedFoundationManifest,
    _child_reference_dataset_ids,
    _child_rows,
    _supported_child_kinds,
    _v3_readiness_projection,
    _verify_children_blind,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.ibkr_foundation_promotion import (
    _authenticate_ibkr_foundation_promotion_migration_v2,
    create_ibkr_foundation_confirmatory_promotion,
)
from qtrad.runtime.ibkr_results import (
    _read_legacy_ibkr_historical_result_v2_header,
    publish_ibkr_historical_result,
    verify_ibkr_historical_result,
)
from qtrad.runtime.provider_history_v2 import (
    PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT,
    _read_provider_history_v2_manifest,
    authenticate_provider_history_v2,
)
from qtrad.runtime.provider_history_v3 import (
    build_provider_history,
    verify_provider_history,
)

MIGRATION_CONTRACT = "qtrad-ibkr-historical-v2-to-v3-migration-v1"
MIGRATION_SCHEMA_VERSION = 1
_STAGE6_OUTPUT = "stage6-result-v3"
_STAGE6_RECEIPT = "stage6-result-v3-verification-receipt.json"
_STAGE7_OUTPUT = "provider-history-v3"
_STAGE7_RECEIPT = "provider-history-v3-verification-receipt.json"
_STAGE8_OUTPUT = "foundation-v3.json"
_STAGE8_RECEIPT = "foundation-v3-verification-receipt.json"
_PROMOTION_OUTPUT = "foundation-v3-confirmatory-promotion.json"
_RECORD_OUTPUT = "migration-equivalence-record.json"
_FAILURE_OUTPUT = "migration-failure-record.json"
_CAPACITY_OVERHEAD_BYTES = 1_048_576
_CAPACITY_SAFETY_MULTIPLIER = 2

_STAGE8_READINESS_FIELDS = frozenset(
    {
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
)
_STAGE8_READINESS_EVIDENCE_FIELDS = (
    "provider_row_count",
    "provider_gap_count",
    "total_provider_gap_count",
    "raw_provider_gaps",
    "coverage_cells",
    "coverage_threshold",
    "blocking_coverage_cells",
    "coverage_diagnostics",
    "target_row_count",
    "fold_count",
    "primary_horizon_seconds",
    "request_evidence",
    "source_coverage_summary",
    "source_entitlement_summary",
)
_STAGE8_READINESS_COMMON_AUTHORITY_FIELDS = (
    "source_contract_selection_sha256",
    "source_plan_sha256",
    "source_runtime_sha256",
)
_STAGE8_READINESS_V2_AUTHORITY_FIELDS = ("source_aggregate_sha256",)
_STAGE8_READINESS_V3_AUTHORITY_FIELDS = (
    "source_result_id",
    "source_closure_id",
    "source_verification_id",
)
# These values are lineage, physical, or implementation identities.  They are
# deliberately excluded from the scientific observation projection.
_OBSERVATION_NON_SEMANTIC_FIELDS = frozenset(
    {
        "observation_sha256",
        "request_sha256",
        "result_sha256",
        "aggregate_sha256",
        "attempt_id",
        "attempt_started_at",
        "attempt_completed_at",
        "acquisition_started_at",
        "acquisition_completed_at",
    }
)
_CHILD_NON_SEMANTIC_FIELDS = frozenset(
    {
        "dataset_id",
        "projection_id",
        "source_dataset_id",
        "target_dataset_id",
        "foundation_configuration_id",
        "manifest_id",
        "manifest_path",
        "manifest_sha256",
        "file",
        "file_sha256",
        "rows_sha256",
        "lineage",
    }
)


@dataclass(frozen=True, slots=True)
class PromotionAuthorisation:
    """Explicit operator authority required for the new confirmatory promotion."""

    authorized_by: str
    authorized_at: datetime
    authorization_reference: str

    def __post_init__(self) -> None:
        if not self.authorized_by.strip():
            raise ValueError("migration promotion authorisation requires an operator")
        if not self.authorization_reference.strip():
            raise ValueError("migration promotion authorisation requires a reference")
        if self.authorized_at.tzinfo is None or self.authorized_at.utcoffset() != UTC.utcoffset(
            self.authorized_at
        ):
            raise ValueError("migration promotion authorisation time must use UTC")


@dataclass(frozen=True, slots=True)
class MigrationPaths:
    """Exact retained inputs and create-only outputs for one H4 attempt."""

    source_stage6_manifest: Path
    source_stage7_manifest: Path
    source_stage7_receipt: Path
    source_stage8_foundation: Path
    source_stage8_receipt: Path
    source_promotion: Path
    destination_root: Path

    @property
    def stage6_manifest(self) -> Path:
        return self.destination_root / _STAGE6_OUTPUT / "manifest.json"

    @property
    def stage6_receipt(self) -> Path:
        return self.destination_root / _STAGE6_RECEIPT

    @property
    def stage7_manifest(self) -> Path:
        return self.destination_root / _STAGE7_OUTPUT / "manifest.json"

    @property
    def stage7_receipt(self) -> Path:
        return self.destination_root / _STAGE7_RECEIPT

    @property
    def stage8_foundation(self) -> Path:
        return self.destination_root / _STAGE8_OUTPUT

    @property
    def stage8_receipt(self) -> Path:
        return self.destination_root / _STAGE8_RECEIPT

    @property
    def promotion(self) -> Path:
        return self.destination_root / _PROMOTION_OUTPUT

    @property
    def record(self) -> Path:
        return self.destination_root / _RECORD_OUTPUT

    @property
    def failure_record(self) -> Path:
        return self.destination_root / _FAILURE_OUTPUT

    def output_paths(self) -> tuple[Path, ...]:
        return (
            self.destination_root,
            self.stage6_manifest.parent,
            self.stage6_receipt,
            self.stage7_manifest.parent,
            self.stage7_receipt,
            self.stage8_foundation,
            self.stage8_foundation.parent / f"{self.stage8_foundation.name}.children",
            self.stage8_receipt,
            self.promotion,
            self.record,
        )


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """Read-only preflight result; no destination is created by this operation."""

    implementation_commit: str
    paths: MigrationPaths
    source_stage6_result_id: str
    source_stage6_closure_id: str
    source_stage6_request_count: int
    source_stage7_dataset_id: str
    source_stage7_manifest_sha256: str
    source_stage7_part_count: int
    source_stage8_build_id: str
    source_stage8_manifest_sha256: str
    source_promotion_id: str
    capacity_required_bytes: int
    capacity_available_bytes: int

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "contract": MIGRATION_CONTRACT,
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "implementation_commit": self.implementation_commit,
            "source": {
                "stage6_result_id": self.source_stage6_result_id,
                "stage6_closure_id": self.source_stage6_closure_id,
                "stage6_request_count": self.source_stage6_request_count,
                "stage7_dataset_id": self.source_stage7_dataset_id,
                "stage7_manifest_sha256": self.source_stage7_manifest_sha256,
                "stage7_part_count": self.source_stage7_part_count,
                "stage8_build_id": self.source_stage8_build_id,
                "stage8_manifest_sha256": self.source_stage8_manifest_sha256,
                "promotion_id": self.source_promotion_id,
            },
            "capacity": {
                "required_bytes": self.capacity_required_bytes,
                "available_bytes": self.capacity_available_bytes,
            },
            "destination_root": str(self.paths.destination_root),
            "outputs": [str(path) for path in self.paths.output_paths()[1:]],
        }


@dataclass(frozen=True, slots=True)
class MigrationWorkCounts:
    """Deterministic counts captured during one migration attempt."""

    old_stage6_request_children: int
    new_stage6_request_children: int
    old_stage7_parts_read: int
    new_stage7_parts_read: int
    old_stage7_rows_decoded: int
    new_stage7_rows_decoded: int
    old_stage8_child_rows_read: int
    new_stage8_child_rows_read: int
    old_stage6_semantic_replays: int
    stage6_semantic_replays: int
    stage7_semantic_replays: int
    stage8_semantic_replays: int
    promotion_semantic_replays: int

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "old_stage6_request_children": self.old_stage6_request_children,
            "new_stage6_request_children": self.new_stage6_request_children,
            "old_stage7_parts_read": self.old_stage7_parts_read,
            "new_stage7_parts_read": self.new_stage7_parts_read,
            "old_stage7_rows_decoded": self.old_stage7_rows_decoded,
            "new_stage7_rows_decoded": self.new_stage7_rows_decoded,
            "old_stage8_child_rows_read": self.old_stage8_child_rows_read,
            "new_stage8_child_rows_read": self.new_stage8_child_rows_read,
            "old_stage6_semantic_replays": self.old_stage6_semantic_replays,
            "stage6_semantic_replays": self.stage6_semantic_replays,
            "stage7_semantic_replays": self.stage7_semantic_replays,
            "stage8_semantic_replays": self.stage8_semantic_replays,
            "promotion_semantic_replays": self.promotion_semantic_replays,
        }


@dataclass(slots=True)
class _MigrationCounters:
    old_stage6_request_children: int = 0
    new_stage6_request_children: int = 0
    old_stage7_parts_read: int = 0
    new_stage7_parts_read: int = 0
    old_stage7_rows_decoded: int = 0
    new_stage7_rows_decoded: int = 0
    old_stage8_child_rows_read: int = 0
    new_stage8_child_rows_read: int = 0
    old_stage6_semantic_replays: int = 0
    stage6_semantic_replays: int = 0
    stage7_semantic_replays: int = 0
    stage8_semantic_replays: int = 0
    promotion_semantic_replays: int = 0


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Durable migration/equivalence facts returned after create-only publication."""

    record_path: Path
    record: dict[str, JsonValue]

    @property
    def work_counts(self) -> MigrationWorkCounts:
        value = _mapping(self.record["work_counts"], "migration work counts")
        return MigrationWorkCounts(
            old_stage6_request_children=_integer(
                value["old_stage6_request_children"], "old Stage 6 child count"
            ),
            new_stage6_request_children=_integer(
                value["new_stage6_request_children"], "new Stage 6 child count"
            ),
            old_stage7_parts_read=_integer(
                value["old_stage7_parts_read"], "old Stage 7 part count"
            ),
            new_stage7_parts_read=_integer(
                value["new_stage7_parts_read"], "new Stage 7 part count"
            ),
            old_stage7_rows_decoded=_integer(
                value["old_stage7_rows_decoded"], "old Stage 7 row count"
            ),
            new_stage7_rows_decoded=_integer(
                value["new_stage7_rows_decoded"], "new Stage 7 row count"
            ),
            old_stage8_child_rows_read=_integer(
                value["old_stage8_child_rows_read"], "old Stage 8 row count"
            ),
            new_stage8_child_rows_read=_integer(
                value["new_stage8_child_rows_read"], "new Stage 8 row count"
            ),
            old_stage6_semantic_replays=_integer(
                value["old_stage6_semantic_replays"], "old Stage 6 replay count"
            ),
            stage6_semantic_replays=_integer(
                value["stage6_semantic_replays"], "Stage 6 replay count"
            ),
            stage7_semantic_replays=_integer(
                value["stage7_semantic_replays"], "Stage 7 replay count"
            ),
            stage8_semantic_replays=_integer(
                value["stage8_semantic_replays"], "Stage 8 replay count"
            ),
            promotion_semantic_replays=_integer(
                value["promotion_semantic_replays"], "promotion replay count"
            ),
        )


def plan_retained_ibkr_migration(
    paths: MigrationPaths,
    *,
    implementation_commit: str,
) -> MigrationPlan:
    """Preflight retained paths, identity and capacity without writing."""

    _require_implementation_commit(implementation_commit)
    for path, field in (
        (paths.source_stage6_manifest, "retained Stage 6 manifest"),
        (paths.source_stage7_manifest, "retained Stage 7 manifest"),
        (paths.source_stage7_receipt, "retained Stage 7 receipt"),
        (paths.source_stage8_foundation, "retained Stage 8 foundation"),
        (paths.source_stage8_receipt, "retained Stage 8 receipt"),
        (paths.source_promotion, "retained Stage 8 promotion"),
    ):
        _require_regular_file(path, field)
    _preflight_destination(paths)
    capacity_required_bytes, capacity_available_bytes = _capacity_preflight(paths)

    stage6 = _read_legacy_ibkr_historical_result_v2_header(
        paths.source_stage6_manifest, require_exact_tree=True
    )
    stage7 = _read_provider_history_v2_manifest(paths.source_stage7_manifest)
    if stage7.document["contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT:
        raise ValueError("retained Stage 7 migration input must be provider-history v2")
    stage8_document = _read_json(paths.source_stage8_foundation, "retained Stage 8 foundation")
    promotion_document = _read_json(paths.source_promotion, "retained Stage 8 promotion")
    source_stage8_build_id = _string(stage8_document["build_sha256"], "retained Stage 8 build")
    source_promotion_id = _string(promotion_document["promotion_sha256"], "retained promotion")
    return MigrationPlan(
        implementation_commit=implementation_commit,
        paths=paths,
        source_stage6_result_id=stage6.aggregate.result_id,
        source_stage6_closure_id=stage6.aggregate.closure_id,
        source_stage6_request_count=len(stage6.aggregate.request_results),
        source_stage7_dataset_id=stage7.dataset.dataset_sha256,
        source_stage7_manifest_sha256=_file_sha256(paths.source_stage7_manifest),
        source_stage7_part_count=len(stage7.parts),
        source_stage8_build_id=source_stage8_build_id,
        source_stage8_manifest_sha256=_file_sha256(paths.source_stage8_foundation),
        source_promotion_id=source_promotion_id,
        capacity_required_bytes=capacity_required_bytes,
        capacity_available_bytes=capacity_available_bytes,
    )


def migrate_retained_ibkr_evidence(
    paths: MigrationPaths,
    *,
    implementation_commit: str,
    promotion_authorisation: PromotionAuthorisation,
) -> MigrationResult:
    """Migrate retained files once into the current v3 chain.

    The destination root is create-only.  A failure writes one durable failure
    record and leaves the attempt unclaimable; callers must choose a fresh root.
    """

    plan = plan_retained_ibkr_migration(
        paths,
        implementation_commit=implementation_commit,
    )
    # Read-only plan/preflight failures above this boundary do not create an attempt.
    # Once the fresh root exists, every migration-phase failure is durably classified.
    if paths.destination_root.exists():
        raise FileExistsError(
            f"migration destination root already exists: {paths.destination_root}"
        )
    paths.destination_root.mkdir()
    phase = "old-stage8-authentication"
    try:
        old_stage8_auth = _authenticate_ibkr_foundation_migration_v2(
            paths.source_stage8_foundation,
            receipt=paths.source_stage8_receipt,
        )
        phase = "old-promotion-authentication"
        old_promotion_auth = _authenticate_ibkr_foundation_promotion_migration_v2(
            paths.source_stage8_foundation,
            receipt=paths.source_stage8_receipt,
            promotion=paths.source_promotion,
        )
        phase = "old-stage7-authentication"
        old_stage7_evidence = authenticate_provider_history_v2(
            paths.source_stage7_manifest,
            receipt=paths.source_stage7_receipt,
        )
        phase = "old-stage7-manifest"
        old_stage7_manifest = _read_provider_history_v2_manifest(paths.source_stage7_manifest)
        phase = "old-stage8-manifest-authentication"
        old_authenticated_manifest = _authenticate_foundation_manifest(
            paths.source_stage8_foundation,
            provider_closure=False,
        )
        phase = "old-stage6-header"
        stage6_stream = _read_legacy_ibkr_historical_result_v2_header(
            paths.source_stage6_manifest,
            require_exact_tree=True,
        )
        phase = "old-stage6-child-read"
        old_results = tuple(stage6_stream.iter_request_results())
        if len(old_results) != plan.source_stage6_request_count:
            raise ValueError("retained Stage 6 child count changed during migration")
        counters = _MigrationCounters(
            old_stage6_request_children=len(old_results),
            old_stage7_parts_read=len(old_stage7_manifest.parts),
            old_stage7_rows_decoded=len(old_stage7_evidence.observations),
        )
        phase = "old-stage6-semantic-replay"
        stage6_equivalence = _replay_legacy_stage6(stage6_stream, old_results)
        counters.old_stage6_semantic_replays += 1

        phase = "stage6-build"
        current_aggregate = build_ibkr_historical_aggregate_result(
            stage6_stream.plan,
            stage6_stream.plan_bytes,
            old_results,
        )
        artifact = IbkrHistoricalResultArtifact(
            plan=stage6_stream.plan,
            plan_bytes=stage6_stream.plan_bytes,
            request_results=old_results,
            aggregate=current_aggregate,
        )
        phase = "stage6-publication"
        published_stage6 = publish_ibkr_historical_result(
            paths.stage6_manifest.parent,
            artifact,
        )
        phase = "stage6-verification"
        counters.stage6_semantic_replays += 1
        verified_stage6 = verify_ibkr_historical_result(
            published_stage6,
            receipt_output=paths.stage6_receipt,
        )
        counters.new_stage6_request_children = len(verified_stage6.request_results)

        phase = "stage7-build"
        published_stage7 = build_provider_history(
            published_stage6,
            stage6_receipt=paths.stage6_receipt,
            output=paths.stage7_manifest.parent,
        )
        phase = "stage7-verification"
        counters.stage7_semantic_replays += 1
        new_stage7_evidence = verify_provider_history(
            published_stage7,
            stage6_manifest=published_stage6,
            stage6_receipt=paths.stage6_receipt,
            receipt_output=paths.stage7_receipt,
        )
        counters.new_stage7_parts_read = len(new_stage7_evidence.dataset.partitions)
        counters.new_stage7_rows_decoded = len(new_stage7_evidence.observations)
        phase = "stage7-equivalence"
        stage7_equivalence = _compare_stage7(
            old_stage7_evidence,
            new_stage7_evidence,
        )

        phase = "stage8-build"
        write_ibkr_foundation(
            paths.stage8_foundation,
            stage7_manifest=published_stage7,
            stage7_receipt=paths.stage7_receipt,
            configuration=old_authenticated_manifest.configuration,
        )
        phase = "stage8-verification"
        counters.stage8_semantic_replays += 1
        verified_build = verify_ibkr_foundation(
            paths.stage8_foundation,
            stage7_manifest=published_stage7,
            stage7_receipt=paths.stage7_receipt,
            receipt_output=paths.stage8_receipt,
        )
        phase = "stage8-equivalence"
        stage8_equivalence, old_stage8_rows, new_stage8_rows = _compare_stage8(
            old_authenticated_manifest,
            verified_build,
        )
        counters.old_stage8_child_rows_read = old_stage8_rows
        counters.new_stage8_child_rows_read = new_stage8_rows

        phase = "promotion"
        new_promotion_auth = create_ibkr_foundation_confirmatory_promotion(
            paths.stage8_foundation,
            receipt=paths.stage8_receipt,
            output=paths.promotion,
            authorized_by=promotion_authorisation.authorized_by,
            authorized_at=promotion_authorisation.authorized_at,
            authorization_reference=promotion_authorisation.authorization_reference,
        )
        phase = "record"
        record = _migration_record(
            plan=plan,
            old_stage8_auth=old_stage8_auth,
            old_promotion_auth=old_promotion_auth,
            new_promotion_auth=new_promotion_auth,
            promotion_authorisation=promotion_authorisation,
            stage6_equivalence=stage6_equivalence,
            stage7_equivalence=stage7_equivalence,
            stage8_equivalence=stage8_equivalence,
            work_counts=MigrationWorkCounts(
                old_stage6_request_children=counters.old_stage6_request_children,
                new_stage6_request_children=counters.new_stage6_request_children,
                old_stage7_parts_read=counters.old_stage7_parts_read,
                new_stage7_parts_read=counters.new_stage7_parts_read,
                old_stage7_rows_decoded=counters.old_stage7_rows_decoded,
                new_stage7_rows_decoded=counters.new_stage7_rows_decoded,
                old_stage8_child_rows_read=counters.old_stage8_child_rows_read,
                new_stage8_child_rows_read=counters.new_stage8_child_rows_read,
                old_stage6_semantic_replays=counters.old_stage6_semantic_replays,
                stage6_semantic_replays=counters.stage6_semantic_replays,
                stage7_semantic_replays=counters.stage7_semantic_replays,
                stage8_semantic_replays=counters.stage8_semantic_replays,
                promotion_semantic_replays=counters.promotion_semantic_replays,
            ),
        )
        _write_create_only(paths.record, canonical_json_bytes(record))
        return MigrationResult(record_path=paths.record, record=record)
    except Exception as error:
        try:
            _write_failure_record(paths, plan, phase=phase, error=error)
        except Exception as record_error:
            error.add_note(f"migration failure record could not be written: {record_error}")
        raise


def _replay_legacy_stage6(
    stream: object,
    results: tuple[IbkrHistoricalRequestResult, ...],
) -> dict[str, JsonValue]:
    plan = cast(Any, stream).plan
    aggregate = cast(Any, stream).aggregate
    plan_bytes = cast(bytes, cast(Any, stream).plan_bytes)
    expected_requests = tuple(sorted(plan.requests, key=lambda item: item.request_sha256))
    actual_results = tuple(sorted(results, key=lambda item: item.request_sha256))
    if len(actual_results) != len(expected_requests):
        raise ValueError("retained Stage 6 v2 replay request count changed")
    for request, result in zip(expected_requests, actual_results, strict=True):
        replay_ibkr_historical_request_result(request, result)
    rebuilt = build_ibkr_historical_aggregate_result(plan, plan_bytes, actual_results)
    old_semantic = {
        "plan_semantic_id": aggregate.plan.semantic_sha256,
        "request_result_semantic_ids": [item.semantic_sha256 for item in aggregate.request_results],
        "coverage_summary": _json_value(aggregate.coverage_summary),
        "entitlement_summary": _json_value(aggregate.entitlement_summary),
    }
    rebuilt_semantic = rebuilt.semantic_identity_payload()
    if any(old_semantic[key] != rebuilt_semantic[key] for key in old_semantic):
        raise ValueError("retained Stage 6 v2 semantic replay is not equivalent")
    return {
        "old_semantic_projection_sha256": _digest_json(old_semantic),
        "new_semantic_projection_sha256": _digest_json(rebuilt_semantic),
        "equivalent": True,
    }


def _compare_stage7(
    old: ProviderHistorySourceEvidence,
    new: ProviderHistorySourceEvidence,
) -> dict[str, JsonValue]:
    old_dataset = old.dataset
    new_dataset = new.dataset
    if old_dataset.row_count != new_dataset.row_count:
        raise ValueError("Stage 7 migration row count is not equivalent")
    old_policy = old_dataset.availability_policy.as_json_value()
    new_policy = new_dataset.availability_policy.as_json_value()
    if old_policy != new_policy:
        raise ValueError("Stage 7 migration availability policy changed")
    old_summary = old.observation_summary
    new_summary = new.observation_summary
    if old_summary is None or new_summary is None:
        raise ValueError("Stage 7 migration observation summary is missing")
    old_summary_json = _observation_summary_json(old_summary)
    new_summary_json = _observation_summary_json(new_summary)
    if old_summary_json != new_summary_json:
        raise ValueError("Stage 7 migration observation summary changed")
    new_request_index = _request_evidence_index(new.request_evidence)
    old_digest, old_count = _observation_digest(
        old.observations,
        projection=lambda value: _stage7_observation_projection(
            value, request_index=new_request_index, legacy=True
        ),
    )
    new_digest, new_count = _observation_digest(
        new.observations,
        projection=lambda value: _stage7_observation_projection(
            value, request_index=None, legacy=False
        ),
    )
    if (old_digest, old_count) != (new_digest, new_count):
        raise ValueError("Stage 7 migration observation semantics changed")
    old_evidence = _evidence_digest(old.request_evidence)
    new_evidence = _evidence_digest(new.request_evidence)
    if old_evidence != new_evidence:
        raise ValueError("Stage 7 migration request evidence changed")
    relocation: dict[str, JsonValue] = {
        "contract": "qtrad-stage7-schedule-evidence-relocation-v1",
        "source": "retained v2 row schedule_evidence",
        "destination": "authenticated v3 Stage 6 request_evidence",
        "rows_checked": old_count,
        "equivalent": True,
        "legacy_disposition_normalization": {"BAR_ACCEPTED": "SUCCEEDED"},
        "explanation": (
            "Each retained row schedule request/result/session payload was matched to the "
            "authenticated current request evidence; current rows carry this summary at the "
            "Stage 6 request level."
        ),
    }
    return {
        "row_count": old_count,
        "old_semantic_projection_sha256": old_digest,
        "new_semantic_projection_sha256": new_digest,
        "availability_policy": cast(JsonValue, old_policy),
        "observation_summary": old_summary_json,
        "request_evidence_sha256": old_evidence,
        "schedule_evidence_relocation": relocation,
    }


def _stage8_readiness_semantic_projection(value: object, field: str) -> dict[str, JsonValue]:
    readiness = _mapping(value, field)
    if set(readiness) != _STAGE8_READINESS_FIELDS:
        raise ValueError(f"{field} has unexpected schema")
    evidence = _mapping(readiness["evidence"], f"{field} evidence")
    semantic_fields = set(_STAGE8_READINESS_EVIDENCE_FIELDS)
    common_fields = set(_STAGE8_READINESS_COMMON_AUTHORITY_FIELDS)
    v2_fields = set(_STAGE8_READINESS_V2_AUTHORITY_FIELDS)
    v3_fields = set(_STAGE8_READINESS_V3_AUTHORITY_FIELDS)
    if frozenset(evidence) not in (
        frozenset(semantic_fields | common_fields | v2_fields),
        frozenset(semantic_fields | common_fields | v3_fields),
    ):
        raise ValueError(f"{field} evidence has unexpected schema")
    for authority_field in (
        *_STAGE8_READINESS_COMMON_AUTHORITY_FIELDS,
        *_STAGE8_READINESS_V2_AUTHORITY_FIELDS,
        *_STAGE8_READINESS_V3_AUTHORITY_FIELDS,
    ):
        if authority_field in evidence:
            _string(evidence[authority_field], f"{field} evidence {authority_field}")
    return _v3_readiness_projection(readiness)


def _stage8_readiness_authority(value: object, field: str) -> dict[str, JsonValue]:
    _stage8_readiness_semantic_projection(value, field)
    evidence = _mapping(_mapping(value, field)["evidence"], f"{field} evidence")
    authority_fields = set(evidence) & (
        set(_STAGE8_READINESS_COMMON_AUTHORITY_FIELDS)
        | set(_STAGE8_READINESS_V2_AUTHORITY_FIELDS)
        | set(_STAGE8_READINESS_V3_AUTHORITY_FIELDS)
    )
    return {
        key: _string(evidence[key], f"{field} evidence {key}") for key in sorted(authority_fields)
    }


def _compare_stage8_readiness(
    old_value: object, new_value: object
) -> tuple[dict[str, JsonValue], dict[str, JsonValue], dict[str, JsonValue]]:
    old_projection = _stage8_readiness_semantic_projection(old_value, "retained readiness")
    new_projection = _stage8_readiness_semantic_projection(new_value, "new readiness")
    if old_projection != new_projection:
        raise ValueError("Stage 8 migration readiness semantics changed")
    return (
        old_projection,
        new_projection,
        {
            "old": _stage8_readiness_authority(old_value, "retained readiness"),
            "new": _stage8_readiness_authority(new_value, "new readiness"),
        },
    )


def _compare_stage8(
    old_manifest: _AuthenticatedFoundationManifest,
    new_build: IBKRFoundationBuild,
) -> tuple[dict[str, JsonValue], int, int]:
    old_rows = _verify_old_stage8_rows(old_manifest)
    new_rows = _child_rows(new_build)
    if set(old_rows) != set(new_rows):
        raise ValueError("Stage 8 migration child kinds changed")
    child_projection: dict[str, JsonValue] = {}
    old_count = 0
    new_count = 0
    for kind in sorted(old_rows):
        old_digest, old_kind_count = _mapping_rows_digest(old_rows[kind])
        new_digest, new_kind_count = _mapping_rows_digest(new_rows[kind])
        if (old_digest, old_kind_count) != (new_digest, new_kind_count):
            raise ValueError(f"Stage 8 migration {kind} semantics changed")
        child_projection[kind] = {
            "row_count": old_kind_count,
            "old_semantic_projection_sha256": old_digest,
            "new_semantic_projection_sha256": new_digest,
        }
        old_count += old_kind_count
        new_count += new_kind_count
    _old_readiness_projection, new_readiness_projection, readiness_authority = (
        _compare_stage8_readiness(old_manifest.payload["readiness"], new_build.readiness.as_json())
    )
    old_active_intervals = _json_value(old_manifest.payload["active_intervals"])
    new_active_intervals = _active_intervals_json(new_build.active_intervals)
    if old_active_intervals != new_active_intervals:
        raise ValueError("Stage 8 migration active intervals changed")
    old_gaps = _json_value(old_manifest.payload["provider_gaps"])
    new_gaps = _json_value(new_build.provider_gaps)
    if old_gaps != new_gaps:
        raise ValueError("Stage 8 migration provider gaps changed")
    return (
        {
            "readiness": cast(JsonValue, new_readiness_projection),
            "readiness_authority": cast(JsonValue, readiness_authority),
            "active_intervals": old_active_intervals,
            "provider_gaps": old_gaps,
            "children": child_projection,
        },
        old_count,
        new_count,
    )


def _verify_old_stage8_rows(
    old_manifest: _AuthenticatedFoundationManifest,
) -> dict[str, tuple[dict[str, JsonValue], ...]]:
    children = old_manifest.children
    kinds = _supported_child_kinds(children)
    expected_ids = _child_reference_dataset_ids(children, child_kinds=kinds)
    rows = _verify_children_blind(
        old_manifest.path.parent,
        children,
        expected_ids,
        old_manifest.expected_lineage,
        decode_rows=True,
        decode_base=True,
        child_kinds=kinds,
    )
    return rows


def _active_intervals_json(
    active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]],
) -> dict[str, JsonValue]:
    return {
        instrument_id: [
            [interval_start.isoformat(), interval_end.isoformat()]
            for interval_start, interval_end in intervals
        ]
        for instrument_id, intervals in sorted(active_intervals.items())
    }


def _observation_summary_json(summary: object) -> dict[str, JsonValue]:
    value = cast(Any, summary)
    intervals: list[JsonValue] = []
    for request_sha256, request_intervals in value.accepted_intervals_by_request:
        intervals.append(
            [
                request_sha256,
                [
                    [interval_start.isoformat(), interval_end.isoformat()]
                    for interval_start, interval_end in request_intervals
                ],
            ]
        )
    return {
        "accepted_intervals_by_request": intervals,
        "source_start": value.source_start.isoformat(),
        "source_end": value.source_end.isoformat(),
    }


_SCHEDULE_EVIDENCE_FIELDS = frozenset(
    {"request_sha256", "result_sha256", "schedule_state", "sessions"}
)


def _stage7_observation_projection(
    value: Mapping[str, object],
    *,
    request_index: Mapping[str, Mapping[str, JsonValue]] | None,
    legacy: bool,
) -> dict[str, JsonValue]:
    schedule = _mapping(value["schedule_evidence"], "provider observation schedule evidence")
    if request_index is not None:
        if set(schedule) != _SCHEDULE_EVIDENCE_FIELDS:
            raise ValueError("retained Stage 7 schedule evidence schema changed")
        request_ids_value = schedule["request_sha256"]
        result_ids_value = schedule["result_sha256"]
        sessions_value = schedule["sessions"]
        if not isinstance(request_ids_value, list) or not isinstance(result_ids_value, list):
            raise ValueError("retained Stage 7 schedule request/result identities changed")
        if len(request_ids_value) != len(result_ids_value):
            raise ValueError("retained Stage 7 schedule request/result identities changed")
        request_ids = [
            _string(item, "retained Stage 7 schedule request identity")
            for item in request_ids_value
        ]
        result_ids = [
            _string(item, "retained Stage 7 schedule result identity") for item in result_ids_value
        ]
        if not request_ids or not result_ids:
            raise ValueError("retained Stage 7 schedule request/result identities changed")
        if len(set(request_ids)) != len(request_ids) or len(set(result_ids)) != len(result_ids):
            raise ValueError("retained Stage 7 schedule request/result identities changed")
        sessions = _json_value(sessions_value)
        if not isinstance(sessions, list):
            raise ValueError("retained Stage 7 schedule sessions changed")
        if _string(schedule["schedule_state"], "retained Stage 7 schedule state") != "ACTIVE":
            raise ValueError("retained Stage 7 schedule state changed")
        reconstructed_sessions: list[JsonValue] = []
        for request_id, result_id in zip(request_ids, result_ids, strict=True):
            evidence = request_index.get(request_id)
            if evidence is None or evidence["result_sha256"] != result_id:
                raise ValueError("retained Stage 7 schedule request/result evidence changed")
            evidence_sessions = evidence["sessions"]
            if not isinstance(evidence_sessions, list):
                raise ValueError("authenticated Stage 6 request sessions changed")
            reconstructed_sessions.extend(evidence_sessions)
        if reconstructed_sessions != sessions:
            raise ValueError("retained Stage 7 schedule sessions changed")
        for session in sessions:
            session_mapping = _mapping(session, "retained Stage 7 schedule session")
            if session_mapping.get("active") is not True:
                raise ValueError("retained Stage 7 schedule session state changed")
            provider_session = session_mapping.get("provider_session")
            if (
                provider_session is not None
                and _mapping(provider_session, "retained Stage 7 provider session").get("active")
                is not True
            ):
                raise ValueError("retained Stage 7 provider session state changed")
    else:
        if schedule:
            raise ValueError("new Stage 7 schedule evidence must be empty")
    disposition = _string(value["gap_disposition"], "provider observation gap disposition")
    if legacy:
        if disposition != "BAR_ACCEPTED":
            raise ValueError("retained Stage 7 disposition changed")
        disposition = "SUCCEEDED"
    elif disposition != "SUCCEEDED":
        raise ValueError("new Stage 7 disposition changed")
    projected: dict[str, JsonValue] = {}
    for key, item in value.items():
        if key == "schedule_evidence" or key in _OBSERVATION_NON_SEMANTIC_FIELDS:
            continue
        projected[key] = _json_value(item)
    projected["gap_disposition"] = disposition
    return projected


def _observation_digest(
    rows: object,
    *,
    projection: Callable[[Mapping[str, object]], dict[str, JsonValue]] | None = None,
) -> tuple[str, int]:
    digest = sha256()
    count = 0
    iterator = iter(cast(Iterable[object], rows))
    for row in iterator:
        value = _mapping(cast(Any, row).as_json_value(), "provider observation")
        projected = (
            projection(value)
            if projection is not None
            else {
                key: _json_value(item)
                for key, item in value.items()
                if key not in _OBSERVATION_NON_SEMANTIC_FIELDS
            }
        )
        encoded = canonical_json_bytes(projected)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return digest.hexdigest(), count


def _mapping_rows_digest(rows: object) -> tuple[str, int]:
    digest = sha256()
    count = 0
    for row in cast(Iterable[Mapping[str, object]], rows):
        projected = _project_child_mapping(row)
        encoded = canonical_json_bytes(projected)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return digest.hexdigest(), count


def _project_child_mapping(value: Mapping[str, object]) -> dict[str, JsonValue]:
    projected: dict[str, JsonValue] = {}
    for key, item in value.items():
        if key in _CHILD_NON_SEMANTIC_FIELDS:
            continue
        projected[key] = _json_value(item)
    return projected


def _request_evidence_entry(item: object) -> dict[str, JsonValue]:
    if isinstance(item, Mapping):
        mapping = cast(Mapping[str, object], item)
        request_sha256 = _string(mapping["request_sha256"], "request identity")
        result_sha256 = _string(mapping["result_sha256"], "result identity")
        disposition_value = mapping["evidence_disposition"]
        accepted_row_count_value = mapping["accepted_row_count"]
        sessions_value = mapping["sessions"]
    else:
        evidence = cast(Any, item)
        request_sha256 = _string(evidence.request_sha256, "request identity")
        result_sha256 = _string(evidence.result_sha256, "result identity")
        disposition_value = evidence.evidence_disposition
        accepted_row_count_value = evidence.accepted_row_count
        sessions_value = evidence.sessions
    disposition_candidate = cast(Any, disposition_value)
    evidence_disposition = _string(
        disposition_candidate.value
        if hasattr(disposition_candidate, "value")
        else disposition_candidate,
        "evidence disposition",
    )
    accepted_row_count = _integer(accepted_row_count_value, "accepted row count")
    sessions = _json_value(sessions_value)
    if not isinstance(sessions, list):
        raise ValueError("request evidence sessions must be a list")
    return {
        "request_sha256": request_sha256,
        "result_sha256": result_sha256,
        "evidence_disposition": evidence_disposition,
        "accepted_row_count": accepted_row_count,
        "sessions": sessions,
    }


def _request_evidence_index(value: object) -> dict[str, dict[str, JsonValue]]:
    index: dict[str, dict[str, JsonValue]] = {}
    for item in cast(Iterable[object], value):
        entry = _request_evidence_entry(item)
        request_sha256 = cast(str, entry["request_sha256"])
        if request_sha256 in index:
            raise ValueError("duplicate Stage 7 request evidence identity")
        index[request_sha256] = entry
    return index


def _evidence_digest(value: object) -> str:
    entries = [_request_evidence_entry(item) for item in cast(Iterable[object], value)]
    return _file_sha256_bytes(
        canonical_json_bytes(cast(dict[str, JsonValue], {"entries": entries}))
    )


def _migration_record(
    *,
    plan: MigrationPlan,
    old_stage8_auth: Mapping[str, JsonValue],
    old_promotion_auth: object,
    new_promotion_auth: object,
    promotion_authorisation: PromotionAuthorisation,
    stage6_equivalence: Mapping[str, JsonValue],
    stage7_equivalence: Mapping[str, JsonValue],
    stage8_equivalence: Mapping[str, JsonValue],
    work_counts: MigrationWorkCounts,
) -> dict[str, JsonValue]:
    old_stage8_manifest = _read_json(
        plan.paths.source_stage8_foundation, "retained Stage 8 foundation"
    )
    old_receipt = _read_json(plan.paths.source_stage8_receipt, "retained Stage 8 receipt")
    old_promotion = _read_json(plan.paths.source_promotion, "retained promotion")
    new_foundation = _read_json(plan.paths.stage8_foundation, "new Stage 8 foundation")
    new_receipt = _read_json(plan.paths.stage8_receipt, "new Stage 8 receipt")
    new_promotion = _read_json(plan.paths.promotion, "new promotion")
    old_stage7 = _read_provider_history_v2_manifest(plan.paths.source_stage7_manifest)
    new_stage7 = _read_json(plan.paths.stage7_manifest, "new Stage 7 manifest")
    stage6 = _read_json(plan.paths.stage6_manifest, "new Stage 6 manifest")
    new_stage6_receipt = _read_json(plan.paths.stage6_receipt, "new Stage 6 receipt")

    stage8_readiness_authority = _mapping(
        stage8_equivalence["readiness_authority"], "Stage 8 readiness authority"
    )
    old_stage8_readiness_authority = _mapping(
        stage8_readiness_authority["old"], "old Stage 8 readiness authority"
    )
    new_stage8_readiness_authority = _mapping(
        stage8_readiness_authority["new"], "new Stage 8 readiness authority"
    )
    stage7_schedule_relocation = _mapping(
        stage7_equivalence["schedule_evidence_relocation"],
        "Stage 7 schedule evidence relocation",
    )
    identity_classification: dict[str, JsonValue] = {
        "stage6": {
            "semantic": {
                "old": plan.source_stage6_result_id,
                "new": _string(stage6["result_id"], "new Stage 6 result"),
                "equivalent": True,
                "explanation": (
                    "v3 separates semantic result_id from the retained v2 aggregate identity."
                ),
            },
            "closure": {
                "old": plan.source_stage6_closure_id,
                "new": _string(stage6["closure_id"], "new Stage 6 closure"),
                "equivalent": False,
                "explanation": (
                    "v3 closure_id binds the newly published manifest tree and child bytes."
                ),
            },
        },
        "stage7": {
            "semantic": {
                "old": old_stage7.dataset.dataset_sha256,
                "new": _string(
                    _mapping(new_stage7["dataset"], "new Stage 7 dataset")["dataset_sha256"],
                    "new Stage 7 dataset",
                ),
                "equivalent": True,
                "explanation": (
                    "v3 dataset identity is rebuilt from the retained scientific "
                    "observation projection."
                ),
            },
            "closure": {
                "old": _string(
                    old_stage7.document["physical_manifest_sha256"], "old Stage 7 closure"
                ),
                "new": _string(new_stage7["closure_id"], "new Stage 7 closure"),
                "equivalent": False,
                "explanation": "v3 closure binds only the direct Stage 7 manifest and parts.",
            },
            "schedule_evidence_relocation": _json_value(stage7_schedule_relocation),
        },
        "stage8": {
            "semantic": {
                "old": _string(old_stage8_manifest["build_sha256"], "old Stage 8 foundation"),
                "new": _string(new_foundation["foundation_id"], "new foundation"),
                "equivalent": True,
                "explanation": (
                    "v3 foundation_id is a semantic identity; the retained build "
                    "hash covered the old payload contract."
                ),
            },
            "closure": {
                "old": _file_sha256(plan.paths.source_stage8_foundation),
                "new": _string(new_foundation["closure_id"], "new foundation closure"),
                "equivalent": False,
                "explanation": "v3 closure_id binds the new foundation manifest and child tree.",
            },
            "readiness_authority": {
                "old": _json_value(old_stage8_readiness_authority),
                "new": _json_value(new_stage8_readiness_authority),
                "equivalent": False,
                "explanation": (
                    "v2 aggregate and v3 result/closure/verification plus source "
                    "contract/plan/runtime identities are authority or provenance "
                    "fields excluded from semantic readiness equivalence."
                ),
            },
        },
    }
    record_identity: dict[str, JsonValue] = {
        "contract": MIGRATION_CONTRACT,
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "implementation_commit": plan.implementation_commit,
        "capacity": {
            "required_bytes": plan.capacity_required_bytes,
            "available_bytes": plan.capacity_available_bytes,
        },
        "source": {
            "stage6_result_id": plan.source_stage6_result_id,
            "stage6_closure_id": plan.source_stage6_closure_id,
            "stage7_dataset_id": old_stage7.dataset.dataset_sha256,
            "stage7_closure_id": _string(
                old_stage7.document["physical_manifest_sha256"], "old Stage 7 closure"
            ),
            "stage8_foundation_id": _string(
                old_stage8_manifest["build_sha256"], "old Stage 8 foundation"
            ),
            "promotion_id": _string(old_promotion["promotion_sha256"], "old promotion"),
        },
        "stage6": {
            "old_result_id": plan.source_stage6_result_id,
            "old_closure_id": plan.source_stage6_closure_id,
            "old_manifest_sha256": _file_sha256(plan.paths.source_stage6_manifest),
            "new_result_id": _string(stage6["result_id"], "new Stage 6 result"),
            "new_closure_id": _string(stage6["closure_id"], "new Stage 6 closure"),
            "new_verification_id": _string(
                new_stage6_receipt["verification_id"], "new Stage 6 verification"
            ),
        },
        "stage7": {
            "old_dataset_id": old_stage7.dataset.dataset_sha256,
            "old_manifest_sha256": _file_sha256(plan.paths.source_stage7_manifest),
            "old_closure_id": _string(
                old_stage7.document["physical_manifest_sha256"], "old Stage 7 closure"
            ),
            "old_verification_id": _string(
                _read_json(plan.paths.source_stage7_receipt, "old Stage 7 receipt")[
                    "receipt_sha256"
                ],
                "old Stage 7 verification",
            ),
            "new_dataset_id": _string(
                _mapping(new_stage7["dataset"], "new Stage 7 dataset")["dataset_sha256"],
                "new Stage 7 dataset",
            ),
            "new_closure_id": _string(new_stage7["closure_id"], "new Stage 7 closure"),
            "new_verification_id": _string(
                new_receipt["verification_id"], "new Stage 7 verification"
            ),
        },
        "stage8": {
            "old_foundation_id": _string(
                old_stage8_manifest["build_sha256"], "old Stage 8 foundation"
            ),
            "old_closure_id": _file_sha256(plan.paths.source_stage8_foundation),
            "old_verification_id": _string(
                old_receipt["receipt_sha256"], "old Stage 8 verification"
            ),
            "new_foundation_id": _string(new_foundation["foundation_id"], "new foundation"),
            "new_closure_id": _string(new_foundation["closure_id"], "new foundation closure"),
            "new_verification_id": _string(
                new_receipt["verification_id"], "new Stage 8 verification"
            ),
        },
        "promotion": {
            "old_promotion_id": _string(old_promotion["promotion_sha256"], "old promotion"),
            "new_promotion_id": _string(new_promotion["promotion_sha256"], "new promotion"),
        },
        "identity_classification": identity_classification,
        "equivalence": {
            "stage6": dict(stage6_equivalence),
            "stage7": dict(stage7_equivalence),
            "stage8": dict(stage8_equivalence),
        },
        "operator_authorization": {
            "authorized_by": promotion_authorisation.authorized_by,
            "authorized_at": promotion_authorisation.authorized_at.isoformat(),
            "authorization_reference": promotion_authorisation.authorization_reference,
        },
        "work_counts": work_counts.as_json_value(),
        "safety": {
            "provider_calls": 0,
            "database_reacquisitions": 0,
            "holdout_access": 0,
            "retained_evidence_mutations": 0,
            "promotion_semantic_replays": work_counts.promotion_semantic_replays,
            "old_authority_authenticated": bool(old_stage8_auth and old_promotion_auth),
            "new_authority_created": bool(new_promotion_auth),
        },
    }
    return {
        **record_identity,
        "record_sha256": _digest_json(record_identity),
    }


def _failure_output_snapshot(paths: MigrationPaths) -> list[JsonValue]:
    snapshots: list[JsonValue] = []
    for candidate in paths.output_paths()[1:]:
        path = _absolute_lexical(candidate, "migration output")
        entry: dict[str, JsonValue] = {
            "path": str(path),
            "exists": path.exists(),
        }
        if path.is_file() and not path.is_symlink():
            payload = path.read_bytes()
            entry["bytes"] = len(payload)
            entry["sha256"] = sha256(payload).hexdigest()
        snapshots.append(entry)
    return snapshots


def _write_failure_record(
    paths: MigrationPaths,
    plan: MigrationPlan,
    *,
    phase: str,
    error: Exception,
) -> None:
    identity: dict[str, JsonValue] = {
        "contract": MIGRATION_CONTRACT,
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "kind": "failure",
        "phase": phase,
        "implementation_commit": plan.implementation_commit,
        "source": {
            "stage6_result_id": plan.source_stage6_result_id,
            "stage6_closure_id": plan.source_stage6_closure_id,
            "stage7_dataset_id": plan.source_stage7_dataset_id,
            "stage7_manifest_sha256": plan.source_stage7_manifest_sha256,
            "stage8_build_id": plan.source_stage8_build_id,
            "stage8_manifest_sha256": plan.source_stage8_manifest_sha256,
            "promotion_id": plan.source_promotion_id,
        },
        "capacity": {
            "required_bytes": plan.capacity_required_bytes,
            "available_bytes": plan.capacity_available_bytes,
        },
        "error": {
            "type": type(error).__name__,
            "message": str(error),
        },
        "outputs": _failure_output_snapshot(paths),
        "old_authority_untouched": True,
    }
    record = {**identity, "record_sha256": _digest_json(identity)}
    _write_create_only(paths.failure_record, canonical_json_bytes(record))


def authenticate_migration_equivalence_record(path: Path) -> dict[str, JsonValue]:
    """Authenticate one durable successful migration/equivalence record."""
    document = _read_json(path, "migration equivalence record")
    if document["contract"] != MIGRATION_CONTRACT:
        raise ValueError("migration equivalence record contract mismatch")
    if document["schema_version"] != MIGRATION_SCHEMA_VERSION:
        raise ValueError("migration equivalence record schema mismatch")
    record_sha256 = _string(document["record_sha256"], "migration record identity")
    identity = dict(document)
    identity.pop("record_sha256")
    if _digest_json(identity) != record_sha256:
        raise ValueError("migration equivalence record identity changed")
    for field in (
        "implementation_commit",
        "capacity",
        "stage6",
        "stage7",
        "stage8",
        "promotion",
        "identity_classification",
        "equivalence",
        "operator_authorization",
        "work_counts",
        "safety",
    ):
        if field not in document:
            raise ValueError(f"migration equivalence record missing required field: {field}")
    if document.get("kind") == "failure":
        raise ValueError("migration equivalence record is a failure record")
    return cast(dict[str, JsonValue], document)


def _write_create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _require_implementation_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("migration implementation commit must be a lowercase 40-character SHA-1")
    actual = derive_qtrad_commit(require_clean=True)
    if actual != value:
        raise ValueError(
            "migration implementation commit does not match the clean checked-out runtime"
        )
    return actual


def _source_closure_roots(paths: MigrationPaths) -> tuple[Path, ...]:
    source_paths = (
        paths.source_stage6_manifest,
        paths.source_stage7_manifest,
        paths.source_stage7_receipt,
        paths.source_stage8_foundation,
        paths.source_stage8_receipt,
        paths.source_promotion,
    )
    candidates = {
        _absolute_lexical(path.parent, "retained source closure") for path in source_paths
    }
    roots: list[Path] = []
    for candidate in sorted(candidates, key=lambda item: (len(item.parts), str(item))):
        if not any(candidate == root or candidate in root.parents for root in roots):
            roots.append(candidate)
    return tuple(roots)


def _retained_source_paths(paths: MigrationPaths) -> tuple[Path, ...]:
    files: set[Path] = set()
    for path, field in (
        (paths.source_stage6_manifest, "retained Stage 6 manifest"),
        (paths.source_stage7_manifest, "retained Stage 7 manifest"),
        (paths.source_stage7_receipt, "retained Stage 7 receipt"),
        (paths.source_stage8_foundation, "retained Stage 8 foundation"),
        (paths.source_stage8_receipt, "retained Stage 8 receipt"),
        (paths.source_promotion, "retained Stage 8 promotion"),
    ):
        files.add(_require_regular_file(path, field))
    for root in _source_closure_roots(paths):
        if root.is_file():
            files.add(root)
            continue
        pending = [root]
        while pending:
            current = pending.pop()
            for entry in current.iterdir():
                if entry.is_symlink():
                    raise ValueError(f"retained closure contains a symlink: {entry}")
                if entry.is_dir():
                    pending.append(entry)
                elif entry.is_file():
                    files.add(entry)
                else:
                    raise ValueError(f"retained closure contains unsupported entry: {entry}")
    return tuple(sorted(files, key=str))


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _capacity_preflight(paths: MigrationPaths) -> tuple[int, int]:
    source_bytes = sum(path.stat().st_size for path in _retained_source_paths(paths))
    required = max(1, source_bytes) * _CAPACITY_SAFETY_MULTIPLIER + _CAPACITY_OVERHEAD_BYTES
    statvfs = os.statvfs(str(paths.destination_root.parent))
    available = statvfs.f_bavail * statvfs.f_frsize
    if available < required:
        raise OSError(
            f"migration destination filesystem capacity is insufficient: "
            f"required={required} available={available}"
        )
    return required, available


def _preflight_destination(paths: MigrationPaths) -> None:
    destination = _absolute_lexical(paths.destination_root, "migration destination root")
    if destination.exists():
        raise FileExistsError(f"migration destination root already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"migration destination parent is missing: {destination.parent}")
    for source in (*_source_closure_roots(paths), *_retained_source_paths(paths)):
        if _paths_overlap(destination, source):
            raise ValueError(
                f"migration destination overlaps retained source closure or file: {destination}"
            )
    for path in (*paths.output_paths()[1:], paths.failure_record):
        candidate = _absolute_lexical(path, "migration destination")
        if candidate.exists():
            raise FileExistsError(f"migration destination already exists: {candidate}")


def _absolute_lexical(path: Path, field: str) -> Path:
    raw_parts = str(path).replace("\\", "/").split("/")
    if any(part in {".", ".."} for part in raw_parts):
        raise ValueError(f"{field} path is not canonical: {path}")
    candidate = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (candidate, *candidate.parents):
        if ancestor.is_symlink():
            raise ValueError(f"{field} path contains a symlink: {path}")
    return candidate


def _require_regular_file(path: Path, field: str) -> Path:
    candidate = _absolute_lexical(path, field)
    if not candidate.is_file() or candidate.is_symlink():
        raise FileNotFoundError(f"{field} is not a regular file: {path}")
    return candidate


def _read_json(path: Path, field: str) -> dict[str, object]:
    payload = _require_regular_file(path, field).read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{field} is not valid JSON") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    return dict(cast(Mapping[str, object], value))


def _file_sha256(path: Path) -> str:
    return _file_sha256_bytes(_require_regular_file(path, "migration input").read_bytes())


def _file_sha256_bytes(payload: bytes) -> str:
    if not payload:
        raise ValueError("migration cannot hash empty bytes")
    return sha256(payload).hexdigest()


def _digest_json(value: object) -> str:
    normalized = _json_value(value)
    if not isinstance(normalized, Mapping):
        raise TypeError("migration identity must be a JSON object")
    return _file_sha256_bytes(canonical_json_bytes(cast(Mapping[str, JsonValue], normalized)))


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    candidate = cast(Any, value)
    try:
        method = candidate.as_json_value
    except AttributeError:
        method = None
    if callable(method):
        return _json_value(method())
    try:
        method = candidate.as_json
    except AttributeError:
        method = None
    if callable(method):
        return _json_value(method())
    raise TypeError(f"migration value is not JSON-compatible: {type(value).__name__}")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


__all__ = [
    "MIGRATION_CONTRACT",
    "MIGRATION_SCHEMA_VERSION",
    "MigrationPaths",
    "MigrationPlan",
    "MigrationResult",
    "MigrationWorkCounts",
    "PromotionAuthorisation",
    "authenticate_migration_equivalence_record",
    "migrate_retained_ibkr_evidence",
    "plan_retained_ibkr_migration",
]
