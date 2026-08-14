"""Retained-file-only PR-H4 migration and equivalence orchestration.

This module is intentionally migration-only.  Normal Stage 6/7/8 writers, readers,
verification commands and promotion APIs do not import it.  The real retained-file
migration is a separately authorised operation; tests exercise only disposable
fixtures.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from qtrad.application.ibkr_foundation import IBKRFoundationBuild
from qtrad.application.ibkr_results import build_ibkr_historical_aggregate_result
from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_results import IbkrHistoricalResultArtifact, canonical_json_bytes
from qtrad.runtime.ibkr_foundation import (
    _authenticate_foundation_manifest,
    _authenticate_ibkr_foundation_migration_v2,
    _AuthenticatedFoundationManifest,
    _child_reference_dataset_ids,
    _child_rows,
    _supported_child_kinds,
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
            "stage6_semantic_replays": self.stage6_semantic_replays,
            "stage7_semantic_replays": self.stage7_semantic_replays,
            "stage8_semantic_replays": self.stage8_semantic_replays,
            "promotion_semantic_replays": self.promotion_semantic_replays,
        }


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
    """Preflight retained paths and create-only destinations without writing."""

    if not implementation_commit or len(implementation_commit) < 7:
        raise ValueError("migration implementation commit is required")
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

    stage6 = _read_legacy_ibkr_historical_result_v2_header(
        paths.source_stage6_manifest, require_exact_tree=True
    )
    stage7 = _read_provider_history_v2_manifest(paths.source_stage7_manifest)
    if stage7.document["contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT:
        raise ValueError("retained Stage 7 migration input must be provider-history v2")
    stage8_document = _read_json(paths.source_stage8_foundation, "retained Stage 8 foundation")
    promotion_document = _read_json(paths.source_promotion, "retained Stage 8 promotion")
    source_stage8_build_id = _string(stage8_document.get("build_sha256"), "retained Stage 8 build")
    source_promotion_id = _string(promotion_document.get("promotion_sha256"), "retained promotion")
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
    )


def migrate_retained_ibkr_evidence(
    paths: MigrationPaths,
    *,
    implementation_commit: str,
    promotion_authorisation: PromotionAuthorisation,
) -> MigrationResult:
    """Migrate retained files once into the current v3 chain.

    The destination root is create-only.  A failure leaves any already-published
    destination unclaimed; callers must choose a fresh attempt directory.
    """

    plan = plan_retained_ibkr_migration(
        paths,
        implementation_commit=implementation_commit,
    )
    old_stage8_auth = _authenticate_ibkr_foundation_migration_v2(
        paths.source_stage8_foundation,
        receipt=paths.source_stage8_receipt,
    )
    old_promotion_auth = _authenticate_ibkr_foundation_promotion_migration_v2(
        paths.source_stage8_foundation,
        receipt=paths.source_stage8_receipt,
        promotion=paths.source_promotion,
    )
    old_stage7_evidence = authenticate_provider_history_v2(
        paths.source_stage7_manifest,
        receipt=paths.source_stage7_receipt,
    )

    paths.destination_root.mkdir()
    stage6_stream = _read_legacy_ibkr_historical_result_v2_header(
        paths.source_stage6_manifest, require_exact_tree=True
    )
    old_results = tuple(stage6_stream.iter_request_results())
    if len(old_results) != plan.source_stage6_request_count:
        raise ValueError("retained Stage 6 child count changed during migration")
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
    published_stage6 = publish_ibkr_historical_result(
        paths.stage6_manifest.parent,
        artifact,
    )
    verify_ibkr_historical_result(
        published_stage6,
        receipt_output=paths.stage6_receipt,
    )

    published_stage7 = build_provider_history(
        published_stage6,
        stage6_receipt=paths.stage6_receipt,
        output=paths.stage7_manifest.parent,
    )
    new_stage7_evidence = verify_provider_history(
        published_stage7,
        stage6_manifest=published_stage6,
        stage6_receipt=paths.stage6_receipt,
        receipt_output=paths.stage7_receipt,
    )
    stage7_equivalence = _compare_stage7(
        old_stage7_evidence,
        new_stage7_evidence,
    )

    old_authenticated_manifest = _authenticate_foundation_manifest(
        paths.source_stage8_foundation,
        provider_closure=False,
    )
    write_ibkr_foundation(
        paths.stage8_foundation,
        stage7_manifest=published_stage7,
        stage7_receipt=paths.stage7_receipt,
        configuration=old_authenticated_manifest.configuration,
    )
    verified_build = verify_ibkr_foundation(
        paths.stage8_foundation,
        stage7_manifest=published_stage7,
        stage7_receipt=paths.stage7_receipt,
        receipt_output=paths.stage8_receipt,
    )
    stage8_equivalence, old_stage8_rows, new_stage8_rows = _compare_stage8(
        old_authenticated_manifest,
        verified_build,
    )

    new_promotion_auth = create_ibkr_foundation_confirmatory_promotion(
        paths.stage8_foundation,
        receipt=paths.stage8_receipt,
        output=paths.promotion,
        authorized_by=promotion_authorisation.authorized_by,
        authorized_at=promotion_authorisation.authorized_at,
        authorization_reference=promotion_authorisation.authorization_reference,
    )
    record = _migration_record(
        plan=plan,
        old_stage8_auth=old_stage8_auth,
        old_promotion_auth=old_promotion_auth,
        new_promotion_auth=new_promotion_auth,
        stage7_equivalence=stage7_equivalence,
        stage8_equivalence=stage8_equivalence,
        work_counts=MigrationWorkCounts(
            old_stage6_request_children=len(old_results),
            new_stage6_request_children=len(old_results),
            old_stage7_parts_read=len(
                _read_provider_history_v2_manifest(paths.source_stage7_manifest).parts
            ),
            old_stage7_rows_decoded=len(old_stage7_evidence.observations),
            new_stage7_parts_read=len(new_stage7_evidence.dataset.partitions),
            new_stage7_rows_decoded=len(new_stage7_evidence.observations),
            old_stage8_child_rows_read=old_stage8_rows,
            new_stage8_child_rows_read=new_stage8_rows,
            stage6_semantic_replays=1,
            stage7_semantic_replays=1,
            stage8_semantic_replays=1,
            promotion_semantic_replays=0,
        ),
    )
    _write_create_only(paths.record, canonical_json_bytes(record))
    return MigrationResult(record_path=paths.record, record=record)


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
    old_digest, old_count = _observation_digest(old.observations)
    new_digest, new_count = _observation_digest(new.observations)
    if (old_digest, old_count) != (new_digest, new_count):
        raise ValueError("Stage 7 migration observation semantics changed")
    old_evidence = _evidence_digest(old.request_evidence)
    new_evidence = _evidence_digest(new.request_evidence)
    if old_evidence != new_evidence:
        raise ValueError("Stage 7 migration request evidence changed")
    return {
        "row_count": old_count,
        "old_semantic_projection_sha256": old_digest,
        "new_semantic_projection_sha256": new_digest,
        "availability_policy": cast(JsonValue, old_policy),
        "observation_summary": old_summary_json,
        "request_evidence_sha256": old_evidence,
    }


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
    old_readiness = _mapping(old_manifest.payload["readiness"], "retained readiness")
    new_readiness = new_build.readiness.as_json()
    if old_readiness != new_readiness:
        raise ValueError("Stage 8 migration readiness changed")
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
            "readiness": cast(JsonValue, new_readiness),
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


def _observation_digest(rows: object) -> tuple[str, int]:
    digest = sha256()
    count = 0
    iterator = iter(cast(Iterable[object], rows))
    for row in iterator:
        value = _mapping(cast(Any, row).as_json_value(), "provider observation")
        projected = {
            key: _json_value(item)
            for key, item in value.items()
            if key not in _OBSERVATION_NON_SEMANTIC_FIELDS
        }
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


def _evidence_digest(value: object) -> str:
    entries: list[dict[str, JsonValue]] = []
    for item in cast(Iterable[object], value):
        if isinstance(item, Mapping):
            mapping = item
            request_sha256 = _string(mapping.get("request_sha256"), "request identity")
            evidence_disposition = _string(
                mapping.get("evidence_disposition"), "evidence disposition"
            )
            accepted_row_count = _integer(mapping.get("accepted_row_count"), "accepted row count")
            sessions = _json_value(mapping.get("sessions"))
        else:
            evidence = cast(Any, item)
            request_sha256 = _string(evidence.request_sha256, "request identity")
            evidence_disposition = _string(
                evidence.evidence_disposition.value
                if hasattr(evidence.evidence_disposition, "value")
                else evidence.evidence_disposition,
                "evidence disposition",
            )
            accepted_row_count = _integer(evidence.accepted_row_count, "accepted row count")
            sessions = _json_value(evidence.sessions)
        entries.append(
            {
                "request_sha256": request_sha256,
                "evidence_disposition": evidence_disposition,
                "accepted_row_count": accepted_row_count,
                "sessions": sessions,
            }
        )
    return _file_sha256_bytes(
        canonical_json_bytes(cast(dict[str, JsonValue], {"entries": entries}))
    )


def _migration_record(
    *,
    plan: MigrationPlan,
    old_stage8_auth: Mapping[str, JsonValue],
    old_promotion_auth: object,
    new_promotion_auth: object,
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
    record_identity: dict[str, JsonValue] = {
        "contract": MIGRATION_CONTRACT,
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "implementation_commit": plan.implementation_commit,
        "stage6": {
            "old_result_id": plan.source_stage6_result_id,
            "old_closure_id": plan.source_stage6_closure_id,
            "old_manifest_sha256": _file_sha256(plan.paths.source_stage6_manifest),
            "new_result_id": _string(stage6.get("result_id"), "new Stage 6 result"),
            "new_closure_id": _string(stage6.get("closure_id"), "new Stage 6 closure"),
            "new_verification_id": _string(
                _read_json(plan.paths.stage6_receipt, "new Stage 6 receipt").get("verification_id"),
                "new Stage 6 verification",
            ),
        },
        "stage7": {
            "old_dataset_id": old_stage7.dataset.dataset_sha256,
            "old_manifest_sha256": _file_sha256(plan.paths.source_stage7_manifest),
            "old_closure_id": _string(
                old_stage7.document.get("physical_manifest_sha256"),
                "old Stage 7 closure",
            ),
            "old_verification_id": _string(
                _read_json(plan.paths.source_stage7_receipt, "old Stage 7 receipt").get(
                    "receipt_sha256"
                ),
                "old Stage 7 verification",
            ),
            "new_dataset_id": _string(
                _mapping(new_stage7.get("dataset"), "new Stage 7 dataset").get("dataset_sha256"),
                "new Stage 7 dataset",
            ),
            "new_closure_id": _string(new_stage7.get("closure_id"), "new Stage 7 closure"),
            "new_verification_id": _string(
                _read_json(plan.paths.stage7_receipt, "new Stage 7 receipt").get("verification_id"),
                "new Stage 7 verification",
            ),
        },
        "stage8": {
            "old_foundation_id": _string(
                old_stage8_manifest.get("build_sha256"), "old Stage 8 foundation"
            ),
            "old_closure_id": _file_sha256(plan.paths.source_stage8_foundation),
            "old_verification_id": _string(
                old_receipt.get("receipt_sha256"), "old Stage 8 verification"
            ),
            "new_foundation_id": _string(new_foundation.get("foundation_id"), "new foundation"),
            "new_closure_id": _string(new_foundation.get("closure_id"), "new foundation closure"),
            "new_verification_id": _string(
                new_receipt.get("verification_id"), "new Stage 8 verification"
            ),
        },
        "promotion": {
            "old_promotion_id": _string(old_promotion.get("promotion_sha256"), "old promotion"),
            "new_promotion_id": _string(new_promotion.get("promotion_sha256"), "new promotion"),
        },
        "equivalence": {
            "stage7": dict(stage7_equivalence),
            "stage8": dict(stage8_equivalence),
        },
        "work_counts": work_counts.as_json_value(),
        "safety": {
            "provider_calls": 0,
            "database_reacquisitions": 0,
            "holdout_access": 0,
            "retained_evidence_mutations": 0,
            "promotion_semantic_replays": 0,
            "old_authority_authenticated": bool(old_stage8_auth and old_promotion_auth),
            "new_authority_created": bool(new_promotion_auth),
        },
    }
    return {
        **record_identity,
        "record_sha256": _digest_json(record_identity),
    }


def _write_create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _preflight_destination(paths: MigrationPaths) -> None:
    destination = _absolute_lexical(paths.destination_root, "migration destination root")
    if destination.exists():
        raise FileExistsError(f"migration destination root already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"migration destination parent is missing: {destination.parent}")
    for path in paths.output_paths()[1:]:
        _absolute_lexical(path, "migration destination")
        if path.exists():
            raise FileExistsError(f"migration destination already exists: {path}")


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
    "migrate_retained_ibkr_evidence",
    "plan_retained_ibkr_migration",
]
