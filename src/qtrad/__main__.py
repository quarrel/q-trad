"""q-trad command-line entry point."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import uvicorn
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from qtrad import __version__
from qtrad.adapters.clock import SystemClock
from qtrad.adapters.ig.market_data import IgDemoConfig, IgDemoMarketDataAdapter
from qtrad.adapters.parquet.observations import ParquetObservationStore
from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore, R2FeatureManifest
from qtrad.adapters.parquet.store import ParquetResearchStore
from qtrad.adapters.postgres.ibkr_historical import PostgresIbkrHistoricalExecutionStore
from qtrad.adapters.postgres.storage_measurement import PostgresStorageInspector
from qtrad.adapters.postgres.store import PostgresAuditStore, StreamVersionConflict
from qtrad.api.app import create_app
from qtrad.application.backfill_planning import (
    backfill_plan_payload,
    backfill_requests,
    build_backfill_plan,
)
from qtrad.application.capture_feed import CaptureFeedCursor, advance_capture_feed_cursor
from qtrad.application.foundation import build_asof_panel, build_frozen_targets
from qtrad.application.ibkr_canary import (
    build_adjacent_ibkr_canary_cases,
    freeze_ibkr_request_profile_from_canary,
    ibkr_historical_selection_asset_classes,
    run_ibkr_historical_canary,
    validate_ibkr_historical_canary_representatives,
    validate_ibkr_historical_canary_selection,
)
from qtrad.application.ibkr_capability import (
    build_ibkr_capability_preflight,
    build_ibkr_capability_review,
)
from qtrad.application.ibkr_historical import (
    configured_image_digest,
    derive_qtrad_commit,
)
from qtrad.application.ibkr_results import (
    build_ibkr_historical_result_artifact,
    verify_ibkr_historical_execution_snapshot,
)
from qtrad.application.ingestion import IngestionService
from qtrad.application.listing_review import build_listing_review_manifest
from qtrad.application.provider_history import build_provider_history_dataset
from qtrad.application.r2_features import (
    R2FoundationInputs,
    feature_schema_for_set,
    iter_raw_feature_rows,
    verify_raw_feature_manifest_bindings,
    verify_raw_feature_rows,
)
from qtrad.application.r2_readiness import evaluate_r2_readiness
from qtrad.application.replay import semantic_bar_hash
from qtrad.application.research_observations import (
    build_observation_dataset,
    measure_availability_delay,
    measure_revision_delay,
)
from qtrad.application.run_reconciliation import build_run_reconciliation_plan
from qtrad.application.universe_promotion import promote_reviewed_universe
from qtrad.application.walk_forward import build_expanding_folds, build_zero_return_forecasts
from qtrad.domain.events import EventEnvelope, JsonValue, to_json_value
from qtrad.domain.historical_coverage import BackfillPlan, BackfillQuotaEvidence
from qtrad.domain.ibkr_historical import (
    IbkrAcquisitionRuntime,
    IbkrHistoricalPacingPolicy,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId, RunId
from qtrad.domain.instruments import AssetClass, ProviderListing
from qtrad.domain.market_data import (
    BarProvenance,
    DataGap,
    DataQuality,
    MarketBar,
    PriceBasis,
)
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.domain.r2_features import feature_set_id
from qtrad.ports.capture_feed import CaptureFeedIdentity
from qtrad.ports.clock import Clock
from qtrad.ports.market_data import BackfillRequest
from qtrad.runtime.backfill_plan import (
    decode_backfill_plan,
    load_backfill_plan,
    write_backfill_plan,
)
from qtrad.runtime.capture_feed import HttpCaptureFeedClient, load_capture_feed_page
from qtrad.runtime.deployment import load_capture_deployment_descriptor
from qtrad.runtime.foundation_bundle import (
    load_foundation_config,
    persist_foundation_bundle,
    verify_foundation_bundle,
    verify_foundation_configuration_evidence,
    verify_observation_build_evidence,
)
from qtrad.runtime.ibkr_canary import (
    verify_ibkr_historical_canary_evidence,
    write_ibkr_historical_canary_evidence,
)
from qtrad.runtime.ibkr_capability import load_ibkr_capability_probe_spec
from qtrad.runtime.ibkr_foundation import (
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.ibkr_historical import (
    build_ibkr_contract_selection_from_files,
    build_ibkr_historical_plan_from_files,
    build_ibkr_runtime_lock_from_files,
    load_ibkr_contract_selection,
    load_ibkr_historical_plan,
    load_ibkr_historical_plan_artifact,
    load_ibkr_historical_plan_bytes,
    load_ibkr_runtime_lock,
    reserve_create_only_output,
    verify_ibkr_historical_plan,
    verify_ibkr_historical_plan_closure,
    write_ibkr_contract_selection,
    write_ibkr_historical_plan,
    write_ibkr_historical_request_profile,
    write_ibkr_runtime_lock,
)
from qtrad.runtime.ibkr_results import (
    publish_ibkr_historical_result,
    verify_ibkr_historical_result,
    verify_ibkr_historical_result_stream,
)
from qtrad.runtime.logging import configure_logging
from qtrad.runtime.provider_history import (
    publish_provider_history,
    verify_provider_history,
)
from qtrad.runtime.qualification_gap_history import (
    build_qualification_gap_history_artifact,
    build_qualification_gap_plan_set_history_artifact,
    load_qualification_evidence,
    qualification_gap_backfill_scopes,
    validate_qualification_gap_snapshot,
    write_qualification_gap_history_artifact,
)
from qtrad.runtime.qualification_gap_plan_set import (
    QualificationGapPlanEntry,
    QualificationGapPlanSet,
    build_qualification_gap_plan_set,
    load_qualification_gap_plan_set,
    write_qualification_gap_plan_set,
)
from qtrad.runtime.r2_bundles import verify_r2_oof_bundle
from qtrad.runtime.r2_readiness import load_r2_experiment, write_r2_readiness
from qtrad.runtime.r2_verification import (
    build_oof_bundle,
    build_software_bundle,
    load_experiment_and_feature_paths,
    selection_freeze,
    verify_oof_bundle,
    verify_software_bundle,
    verify_software_bundle_async,
)
from qtrad.runtime.research_export import research_export_metadata
from qtrad.runtime.research_snapshot import (
    ResearchSnapshotImport,
    load_research_snapshot_import,
    research_snapshot_metadata,
)
from qtrad.runtime.run_reconciliation import (
    load_run_reconciliation_plan,
    write_run_reconciliation_plan,
)
from qtrad.runtime.settings import Settings
from qtrad.runtime.storage_measurement import (
    build_storage_active_market_review_artifact,
    build_storage_comparison_artifact,
    build_storage_contrast_artifact,
    build_storage_contrast_qualification_artifact,
    build_storage_snapshot,
    load_storage_active_market_review_input,
    load_storage_evidence_artifact,
    load_storage_snapshot,
    write_storage_evidence_artifact,
    write_storage_snapshot,
)
from qtrad.runtime.strategy_experiment import (
    build_strategy_experiment_report,
    load_strategy_experiment,
    write_strategy_experiment_report,
)
from qtrad.runtime.universe import (
    CaptureCandidates,
    CaptureUniverse,
    load_capture_candidates,
    load_capture_universe,
    render_capture_universe_promotion,
)
from qtrad.runtime.universe_promotion import (
    load_explicit_selection_set,
    load_listing_review_evidence,
)

_HEALTH_PERSIST_INTERVAL_SECONDS = 1.0
LOGGER = logging.getLogger(__name__)


def _utc_minute_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be an ISO-8601 UTC minute") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("timestamp must be an ISO-8601 UTC minute")
    if parsed.second or parsed.microsecond:
        raise argparse.ArgumentTypeError("timestamp must be an ISO-8601 UTC minute")
    return parsed


def _availability_delay_argument(value: str) -> timedelta:
    from qtrad.domain.provider_history import parse_declared_delay

    try:
        return parse_declared_delay(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "availability delay must be a whole-second ISO-8601 duration"
        ) from error


def _utc_timestamp_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601 UTC") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601 UTC")
    return parsed


def _require_sha256_argument(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be lower-case SHA-256")


def _add_ibkr_historical_closure_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_plan: bool,
    include_runtime_attestation: bool,
    include_planner_image: bool,
) -> None:
    if include_plan:
        parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--contract-selection", type=Path, required=True)
    parser.add_argument("--operator-selection", type=Path, required=True)
    parser.add_argument("--capability-review", type=Path, required=True)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--probe-spec", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path, required=True)
    parser.add_argument("--gateway-archive", type=Path, required=True)
    parser.add_argument("--api-archive", type=Path, required=True)
    parser.add_argument("--ibc-archive", type=Path, required=True)
    parser.add_argument("--expected-gateway-sha256", required=True)
    parser.add_argument("--expected-api-sha256", required=True)
    parser.add_argument("--expected-ibc-sha256", required=True)
    if include_runtime_attestation:
        parser.add_argument("--expected-runtime-qtrad-commit", required=True)
        parser.add_argument("--expected-runtime-image-digest", required=True)
        parser.add_argument("--expected-gateway-version", required=True)
        parser.add_argument("--expected-api-version", required=True)
        parser.add_argument("--expected-ibc-version", required=True)
        parser.add_argument("--expected-api-host", default="127.0.0.1")
        parser.add_argument("--expected-api-port", type=int, default=4002)
        parser.add_argument("--expected-client-id-policy", default="DEDICATED_NONZERO_CLIENT_ID")
    parser.add_argument("--request-profile", type=Path, required=True)
    parser.add_argument("--canary-evidence", type=Path, required=True)
    parser.add_argument("--profile-frozen-by", required=True)
    parser.add_argument("--profile-frozen-at", type=_utc_timestamp_argument, required=True)
    parser.add_argument("--maximum-in-flight-requests", type=int, default=1)
    parser.add_argument("--request-timeout-seconds", type=int, default=60)
    parser.add_argument("--retry-count", type=int, default=1)
    parser.add_argument(
        "--duplicate-request-protection",
        default="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
    )
    parser.add_argument("--identical-request-cooldown-seconds", type=int, default=15)
    parser.add_argument("--max-requests-per-contract-window", type=int, default=5)
    parser.add_argument("--max-requests-per-rolling-window", type=int, default=55)
    parser.add_argument("--start", type=_utc_minute_argument, required=True)
    parser.add_argument("--end", type=_utc_minute_argument, required=True)
    if include_planner_image:
        parser.add_argument("--planner-image-digest", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qtrad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db = subparsers.add_parser("db", help="database operations")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("upgrade", help="apply migrations and seed instruments")

    deployment = subparsers.add_parser("deployment", help="capture deployment operations")
    deployment_sub = deployment.add_subparsers(dest="deployment_command", required=True)
    deployment_inspect = deployment_sub.add_parser(
        "inspect", help="validate and emit a deployment descriptor"
    )
    deployment_inspect.add_argument("--descriptor", type=Path, required=True)
    deployment_inspect.add_argument("--repository-root", type=Path, default=Path.cwd())

    runs = subparsers.add_parser("runs", help="operational run evidence")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    reconcile_plan = runs_sub.add_parser(
        "reconcile-plan", help="plan exact stale ingestion-run reconciliation"
    )
    reconcile_plan.add_argument("--universe", type=Path)
    reconcile_plan.add_argument("--cutoff", type=_utc_timestamp_argument, required=True)
    reconcile_plan.add_argument("--output", type=Path, required=True)
    reconcile = runs_sub.add_parser(
        "reconcile", help="execute one confirmed stale-run reconciliation plan"
    )
    reconcile.add_argument("--plan", type=Path, required=True)
    reconcile.add_argument("--confirm-plan-hash", required=True)

    instruments = subparsers.add_parser("instruments", help="instrument operations")
    instrument_sub = instruments.add_subparsers(dest="instrument_command", required=True)
    sync = instrument_sub.add_parser("sync", help="discover and persist IG demo listings")
    sync.add_argument(
        "--universe",
        type=Path,
        help="explicit reviewed universe; defaults to QTRAD_CAPTURE_UNIVERSE_PATH",
    )
    review = instrument_sub.add_parser(
        "review", help="review provider listings or preflight an account-gated review"
    )
    review.add_argument("--provider", choices=("ig", "ibkr"), default="ig")
    review.add_argument("--environment", choices=("demo", "paper"))
    review.add_argument("--catalogue", type=Path)
    review.add_argument("--preflight", action="store_true")
    review.add_argument("--probe-spec", type=Path)
    review.add_argument("--execute-account-probe", action="store_true")
    review.add_argument("--output", type=Path)
    select = instrument_sub.add_parser(
        "select", help="freeze exact IBKR contract decisions from a verified capability review"
    )
    select.add_argument("--provider", choices=("ibkr",), required=True)
    select.add_argument("--capability-review", type=Path, required=True)
    select.add_argument("--catalogue", type=Path, required=True)
    select.add_argument("--probe-spec", type=Path, required=True)
    select.add_argument("--selection", type=Path, required=True)
    select.add_argument("--frozen-by", required=True)
    select.add_argument("--output", type=Path, required=True)

    historical = subparsers.add_parser("historical", help="historical provider-data operations")
    historical_sub = historical.add_subparsers(dest="historical_provider", required=True)
    historical_ibkr = historical_sub.add_parser("ibkr", help="IBKR historical operations")
    historical_ibkr_sub = historical_ibkr.add_subparsers(
        dest="historical_ibkr_command", required=True
    )
    runtime_lock = historical_ibkr_sub.add_parser(
        "runtime-lock", help="hash IBKR runtime archives and inspect the acquisition environment"
    )
    runtime_lock.add_argument("--gateway-archive", type=Path, required=True)
    runtime_lock.add_argument("--api-archive", type=Path, required=True)
    runtime_lock.add_argument("--ibc-archive", type=Path, required=True)
    runtime_lock.add_argument("--gateway-version")
    runtime_lock.add_argument("--api-version")
    runtime_lock.add_argument("--ibc-version", default="3.24.1")
    runtime_lock.add_argument("--image-digest")
    runtime_lock.add_argument("--output", type=Path, required=True)
    canary_run = historical_ibkr_sub.add_parser(
        "canary-run", help="execute the bounded Stage 5 IBKR historical canary"
    )
    canary_run.add_argument("--runtime-lock", type=Path, required=True)
    canary_run.add_argument("--contract-selection", type=Path, required=True)
    canary_run.add_argument(
        "--fx-representative-id",
        "--fx-representative",
        "--fx-instrument-id",
        dest="fx_representative_id",
        required=True,
    )
    canary_run.add_argument(
        "--index-representative-id",
        "--index-representative",
        "--index-instrument-id",
        dest="index_representative_id",
        required=True,
    )
    canary_run.add_argument(
        "--commodity-representative-id",
        "--commodity-representative",
        "--commodity-instrument-id",
        dest="commodity_representative_id",
        required=True,
    )
    canary_run.add_argument("--anchor-end", type=_utc_timestamp_argument, required=True)
    canary_run.add_argument("--output", type=Path, required=True)
    canary_run.add_argument("--execute-account-canary", action="store_true")

    canary_verify = historical_ibkr_sub.add_parser(
        "canary-verify", help="verify immutable Stage 5 canary evidence without provider I/O"
    )
    canary_verify.add_argument("--evidence", type=Path, required=True)
    canary_verify.add_argument("--expected-runtime-sha256")
    canary_verify.add_argument("--expected-selection-sha256")

    profile_freeze = historical_ibkr_sub.add_parser(
        "profile-freeze", help="freeze a request profile from verified Stage 5 canary evidence"
    )
    profile_freeze.add_argument("--canary-evidence", type=Path, required=True)
    profile_freeze.add_argument("--canary-evidence-filename")
    profile_freeze.add_argument("--output", type=Path, required=True)
    profile_freeze.add_argument("--frozen-by", required=True)
    profile_freeze.add_argument("--frozen-at", type=_utc_timestamp_argument, required=True)
    profile_freeze.add_argument("--maximum-in-flight-requests", type=int, default=1)
    profile_freeze.add_argument("--request-timeout-seconds", type=int, default=60)
    profile_freeze.add_argument("--retry-count", type=int, default=1)
    profile_freeze.add_argument(
        "--duplicate-request-protection",
        default="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
    )
    profile_freeze.add_argument("--identical-request-cooldown-seconds", type=int, default=15)
    profile_freeze.add_argument("--max-requests-per-contract-window", type=int, default=5)
    profile_freeze.add_argument("--max-requests-per-rolling-window", type=int, default=55)

    historical_plan = historical_ibkr_sub.add_parser(
        "plan", help="build a deterministic IBKR historical request plan without provider I/O"
    )
    _add_ibkr_historical_closure_arguments(
        historical_plan,
        include_plan=False,
        include_runtime_attestation=True,
        include_planner_image=True,
    )
    historical_plan.add_argument("--output", type=Path, required=True)

    historical_plan_verify = historical_ibkr_sub.add_parser(
        "plan-verify", help="independently replay an IBKR plan and its lower-artifact closure"
    )
    _add_ibkr_historical_closure_arguments(
        historical_plan_verify,
        include_plan=True,
        include_runtime_attestation=True,
        include_planner_image=True,
    )

    historical_register = historical_ibkr_sub.add_parser(
        "register", help="register one verified IBKR historical plan for execution"
    )
    _add_ibkr_historical_closure_arguments(
        historical_register,
        include_plan=True,
        include_runtime_attestation=True,
        include_planner_image=True,
    )
    historical_register.add_argument("--confirm-plan-hash", required=True)

    historical_execute = historical_ibkr_sub.add_parser(
        "execute", help="execute one registered IBKR historical plan"
    )
    historical_execute.add_argument("--plan-id", required=True)
    _add_ibkr_historical_closure_arguments(
        historical_execute,
        include_plan=False,
        include_runtime_attestation=False,
        include_planner_image=False,
    )
    historical_result_build = historical_ibkr_sub.add_parser(
        "result-build", help="publish and verify one completed IBKR historical result closure"
    )
    historical_result_build.add_argument("--plan", type=Path, required=True)
    historical_result_build.add_argument("--output", type=Path, required=True)
    historical_result_verify = historical_ibkr_sub.add_parser(
        "verify", help="independently verify an IBKR historical result closure from files"
    )
    historical_result_verify.add_argument("--result", type=Path, required=True)

    promote = instrument_sub.add_parser(
        "promote", help="verify explicit reviewed selections and emit an undeployed universe"
    )
    promote.add_argument("--catalogue", type=Path, required=True)
    promote.add_argument("--review", type=Path, required=True)
    promote.add_argument("--selections", type=Path, required=True)
    promote.add_argument("--release-name", required=True)
    promote.add_argument("--output", type=Path, required=True)

    ingest = subparsers.add_parser("ingest", help="run IG demo ingestion")
    ingest.add_argument("--environment", choices=["ig-demo"], default="ig-demo")
    ingest.add_argument("--max-seconds", type=float)
    ingest.add_argument("--force-reconnect-after-seconds", type=float)

    backfill = subparsers.add_parser("backfill", help="reviewed historical-coverage operations")
    backfill_sub = backfill.add_subparsers(dest="backfill_command", required=True)
    backfill_plan = backfill_sub.add_parser("plan", help="create an explicit non-overwriting plan")
    backfill_plan.add_argument("--universe", type=Path, required=True)
    backfill_plan.add_argument("--start", type=_utc_minute_argument, required=True)
    backfill_plan.add_argument("--end", type=_utc_minute_argument, required=True)
    backfill_plan.add_argument("--remaining-allowance", type=int, required=True)
    backfill_plan.add_argument("--output", type=Path, required=True)
    backfill_plan.add_argument("instruments", type=InstrumentId, nargs="+")
    backfill_register = backfill_sub.add_parser(
        "register", help="persist a reviewed plan and its coverage gaps"
    )
    backfill_register.add_argument("--plan", type=Path, required=True)
    backfill_register.add_argument("--confirm-plan-hash", required=True)
    backfill_execute = backfill_sub.add_parser(
        "execute", help="execute one registered plan by its exact hash"
    )
    backfill_execute.add_argument("--plan-hash", required=True)

    qualification = subparsers.add_parser(
        "qualification", help="offline capture-qualification evidence operations"
    )
    qualification_sub = qualification.add_subparsers(dest="qualification_command", required=True)
    gap_history = qualification_sub.add_parser(
        "gap-history", help="compare candidate live gaps with verified historical bars"
    )
    gap_history.add_argument("--evidence", type=Path, required=True)
    gap_history_plan = gap_history.add_mutually_exclusive_group(required=True)
    gap_history_plan.add_argument("--plan", type=Path)
    gap_history_plan.add_argument("--plan-set", type=Path)
    gap_history.add_argument("--manifest", type=Path, required=True)
    gap_history.add_argument("--output", type=Path, required=True)
    gap_plan = qualification_sub.add_parser(
        "gap-plan", help="derive a reviewed historical plan from candidate gaps"
    )
    gap_plan.add_argument("--evidence", type=Path, required=True)
    gap_plan.add_argument("--snapshot-import-evidence", type=Path, required=True)
    gap_plan.add_argument("--universe", type=Path, required=True)
    gap_plan.add_argument("--remaining-allowance", type=int, required=True)
    gap_plan.add_argument("--output", type=Path, required=True)
    gap_register = qualification_sub.add_parser(
        "gap-register", help="register every reviewed plan in an exact sparse plan set"
    )
    gap_register.add_argument("--plan-set", type=Path, required=True)
    gap_register.add_argument("--snapshot-import-evidence", type=Path, required=True)
    gap_register.add_argument("--confirm-plan-set-hash", required=True)
    gap_execute = qualification_sub.add_parser(
        "gap-execute", help="execute an exact sparse plan set through one IG demo session"
    )
    gap_execute.add_argument("--plan-set", type=Path, required=True)
    gap_execute.add_argument("--snapshot-import-evidence", type=Path, required=True)
    gap_execute.add_argument("--confirm-plan-set-hash", required=True)

    research = subparsers.add_parser("research", help="research-store operations")
    research_sub = research.add_subparsers(dest="research_command", required=True)
    research_export = research_sub.add_parser(
        "export", help="export latest bar revisions to an immutable Parquet manifest"
    )
    research_export.add_argument("--universe", type=Path, required=True)
    research_export.add_argument("--start", type=_utc_minute_argument, required=True)
    research_export.add_argument("--end", type=_utc_minute_argument, required=True)
    research_export.add_argument("--snapshot-import-evidence", type=Path)
    research_rank = research_sub.add_parser(
        "rank", help="build a deterministic shadow-strategy report from a verified snapshot"
    )
    research_rank.add_argument("--manifest", type=Path, required=True)
    research_rank.add_argument("--experiment", type=Path, required=True)
    research_rank.add_argument("--snapshot-import-evidence", type=Path, required=True)
    research_rank.add_argument("--output", type=Path, required=True)
    research_observations = research_sub.add_parser(
        "observations", help="build or verify causal native observation datasets"
    )
    research_observations_sub = research_observations.add_subparsers(
        dest="observations_command", required=True
    )
    observations_build = research_observations_sub.add_parser(
        "build", help="build observations from an isolated verified snapshot"
    )
    observations_build.add_argument("--universe", type=Path, required=True)
    observations_build.add_argument("--start", type=_utc_minute_argument, required=True)
    observations_build.add_argument("--end", type=_utc_minute_argument, required=True)
    observations_build.add_argument("--calibration-start", type=_utc_minute_argument, required=True)
    observations_build.add_argument("--calibration-end", type=_utc_minute_argument, required=True)
    observations_build.add_argument("--snapshot-import-evidence", type=Path, required=True)
    observations_build.add_argument("--availability-percentile", type=float, required=True)
    observations_build.add_argument(
        "--availability-safety-margin-seconds", type=float, required=True
    )
    observations_verify = research_observations_sub.add_parser(
        "verify", help="independently verify an observation manifest and its Parquet files"
    )
    observations_verify.add_argument("--manifest", type=Path, required=True)
    provider_history_build = research_observations_sub.add_parser(
        "build-provider-history",
        help="build provider-history observations from a verified IBKR result",
    )
    provider_history_build.add_argument("--historical-result", type=Path, required=True)
    provider_history_build.add_argument(
        "--availability-delay", type=_availability_delay_argument, required=True
    )
    provider_history_build.add_argument("--output", type=Path, required=True)
    provider_history_verify = research_observations_sub.add_parser(
        "verify-provider-history",
        help="independently verify provider-history observations and their source closure",
    )
    provider_history_verify.add_argument("--manifest", type=Path, required=True)
    research_foundation = research_sub.add_parser(
        "foundation", help="verify an immutable causal foundation bundle"
    )
    research_foundation_sub = research_foundation.add_subparsers(
        dest="foundation_command", required=True
    )
    foundation_verify = research_foundation_sub.add_parser(
        "verify", help="verify every foundation child and cross-reference"
    )
    foundation_verify.add_argument("--bundle", type=Path, required=True)
    foundation_readiness = research_foundation_sub.add_parser(
        "readiness", help="report fixed IBKR historical foundation readiness"
    )
    foundation_readiness.add_argument("--bundle", type=Path, required=True)
    foundation_build = research_foundation_sub.add_parser(
        "build", help="build an immutable causal foundation bundle from one source"
    )
    foundation_source = foundation_build.add_mutually_exclusive_group(required=True)
    foundation_source.add_argument("--observations-manifest", type=Path)
    foundation_source.add_argument("--provider-history-manifest", type=Path)
    foundation_build.add_argument("--configuration", type=Path, required=True)
    foundation_build.add_argument("--output", type=Path, required=True)
    baselines = research_sub.add_parser("baselines", help="R2 baseline research operations")
    baselines_sub = baselines.add_subparsers(dest="baselines_command", required=True)
    baselines_readiness = baselines_sub.add_parser(
        "readiness", help="verify R2 experiment bindings and report independent readiness gates"
    )
    baselines_readiness.add_argument("--foundation-bundle", type=Path, required=True)
    baselines_readiness.add_argument("--experiment", type=Path, required=True)
    baselines_readiness.add_argument("--software-bundle", type=Path)
    baselines_readiness.add_argument("--output", type=Path, required=True)
    baselines_features = baselines_sub.add_parser(
        "features", help="materialise and verify an OOF R2 raw-feature child"
    )
    baselines_features_verify = baselines_sub.add_parser(
        "features-verify", help="independently verify a persisted R2 raw-feature child"
    )
    baselines_features_verify.add_argument("--foundation-bundle", type=Path, required=True)
    baselines_features_verify.add_argument("--experiment", type=Path, required=True)
    baselines_features_verify.add_argument("--feature-set", required=True)
    baselines_features_verify.add_argument("--manifest", type=Path, required=True)
    baselines_features.add_argument("--foundation-bundle", type=Path, required=True)
    baselines_features.add_argument("--experiment", type=Path, required=True)
    baselines_features.add_argument("--feature-set", required=True)
    baselines_features.add_argument("--output", type=Path, required=True)

    baselines_oof_build = baselines_sub.add_parser(
        "oof-build", help="authenticate R2 feature children and build an OOF bundle"
    )
    baselines_oof_build.add_argument("--foundation-bundle", type=Path, required=True)
    baselines_oof_build.add_argument("--experiment", type=Path, required=True)
    baselines_oof_build.add_argument("--feature-manifest", action="append", required=True)
    baselines_oof_build.add_argument("--output", type=Path, required=True)
    baselines_oof_verify = baselines_sub.add_parser(
        "oof-verify", help="independently verify an R2 OOF bundle"
    )
    baselines_oof_verify.add_argument("--bundle", type=Path, required=True)

    baselines_selection_freeze = baselines_sub.add_parser(
        "selection-freeze", help="freeze disposable implementation selection mechanics"
    )
    baselines_selection_freeze.add_argument("--oof-bundle", type=Path, required=True)
    baselines_selection_freeze.add_argument("--frozen-by", required=True)
    baselines_selection_freeze.add_argument("--output", type=Path, required=True)

    baselines_software_build = baselines_sub.add_parser(
        "software-build", help="build the R2 synthetic and representative verification bundle"
    )
    baselines_software_build.add_argument("--representative-oof-bundle", type=Path, required=True)
    baselines_software_build.add_argument("--representative-selection", type=Path, required=True)
    baselines_software_build.add_argument("--output", type=Path, required=True)
    baselines_software_verify = baselines_sub.add_parser(
        "software-verify", help="independently replay the R2 software bundle"
    )
    baselines_software_verify.add_argument("--bundle", type=Path, required=True)
    replay = subparsers.add_parser("replay", help="verify a research manifest")
    replay.add_argument("--manifest", type=Path, required=True)

    projections = subparsers.add_parser("projections", help="projection operations")
    projection_sub = projections.add_subparsers(dest="projection_command", required=True)
    projection_sub.add_parser("rebuild", help="rebuild projections from events")

    storage = subparsers.add_parser("storage", help="read-only capture-storage measurement")
    storage_sub = storage.add_subparsers(dest="storage_command", required=True)
    storage_snapshot = storage_sub.add_parser(
        "snapshot", help="write one hash-verified physical-storage observation"
    )
    storage_snapshot.add_argument("--universe", type=Path, required=True)
    storage_snapshot.add_argument("--output", type=Path, required=True)
    storage_compare = storage_sub.add_parser(
        "compare", help="write a hash-verified comparison of two storage observations"
    )
    storage_compare.add_argument("--output", type=Path, required=True)
    storage_compare.add_argument("before", type=Path)
    storage_compare.add_argument("after", type=Path)
    storage_contrast = storage_sub.add_parser(
        "contrast", help="contrast two release-bound storage comparisons without database access"
    )
    storage_contrast.add_argument("--output", type=Path, required=True)
    storage_contrast.add_argument("baseline", type=Path)
    storage_contrast.add_argument("candidate", type=Path)
    storage_review = storage_sub.add_parser(
        "review", help="bind one operator active-market review to a storage comparison"
    )
    storage_review.add_argument("--output", type=Path, required=True)
    storage_review.add_argument("comparison", type=Path)
    storage_review.add_argument("review", type=Path)
    storage_qualify = storage_sub.add_parser(
        "qualify", help="qualify a storage contrast against two operator review artifacts"
    )
    storage_qualify.add_argument("--output", type=Path, required=True)
    storage_qualify.add_argument("contrast", type=Path)
    storage_qualify.add_argument("baseline_review", type=Path)
    storage_qualify.add_argument("candidate_review", type=Path)

    feed = subparsers.add_parser("feed", help="capture-feed contract operations")
    feed_sub = feed.add_subparsers(dest="feed_command", required=True)
    feed_verify = feed_sub.add_parser("verify", help="verify saved feed pages without network I/O")
    feed_verify.add_argument("--source-id", required=True)
    feed_verify.add_argument("--universe-name", required=True)
    feed_verify.add_argument("--configuration-hash", required=True)
    feed_verify.add_argument("--after-position", type=int, default=0)
    feed_verify.add_argument("pages", type=Path, nargs="+")
    feed_probe = feed_sub.add_parser(
        "probe", help="fetch and validate one bounded page through a loopback tunnel"
    )
    feed_probe.add_argument("--endpoint", required=True)
    feed_probe.add_argument("--source-id", required=True)
    feed_probe.add_argument("--universe-name", required=True)
    feed_probe.add_argument("--configuration-hash", required=True)
    feed_probe.add_argument("--after-position", type=int, default=0)
    feed_probe.add_argument("--limit", type=int, default=500)

    api = subparsers.add_parser("api", help="run the read-only operator API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings()
    clock = SystemClock()
    configure_logging(settings.log_level)

    if args.command == "db" and args.db_command == "upgrade":
        _upgrade_database(settings)
        asyncio.run(_seed(settings))
    elif args.command == "deployment" and args.deployment_command == "inspect":
        descriptor = load_capture_deployment_descriptor(
            args.descriptor, repository_root=args.repository_root
        )
        print(descriptor.to_json())
    elif args.command == "runs" and args.runs_command == "reconcile-plan":
        asyncio.run(
            _plan_run_reconciliation(
                settings,
                clock,
                universe_path=args.universe,
                cutoff=args.cutoff,
                output_path=args.output,
            )
        )
    elif args.command == "runs" and args.runs_command == "reconcile":
        asyncio.run(
            _reconcile_runs(
                settings,
                clock,
                plan_path=args.plan,
                confirmed_plan_hash=args.confirm_plan_hash,
            )
        )
    elif args.command == "instruments" and args.instrument_command == "sync":
        asyncio.run(_sync_instruments(settings, clock, universe_path=args.universe))
    elif args.command == "instruments" and args.instrument_command == "review":
        asyncio.run(
            _review_instruments(
                settings,
                clock,
                catalogue_path=args.catalogue,
                output_path=args.output,
                provider=args.provider,
                environment=args.environment,
                preflight=args.preflight,
                probe_spec_path=args.probe_spec,
                execute_account_probe=args.execute_account_probe,
            )
        )
    elif args.command == "instruments" and args.instrument_command == "select":
        _select_ibkr_instruments(
            clock,
            capability_review_path=args.capability_review,
            selection_path=args.selection,
            catalogue_path=args.catalogue,
            probe_spec_path=args.probe_spec,
            frozen_by=args.frozen_by,
            output_path=args.output,
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "runtime-lock"
    ):
        _inspect_ibkr_runtime_lock(
            settings,
            clock,
            gateway_archive=args.gateway_archive,
            api_archive=args.api_archive,
            ibc_archive=args.ibc_archive,
            gateway_version=args.gateway_version,
            api_version=args.api_version,
            ibc_version=args.ibc_version,
            image_digest=args.image_digest,
            output_path=args.output,
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "canary-verify"
    ):
        _verify_ibkr_historical_canary(
            args.evidence,
            expected_runtime_sha256=args.expected_runtime_sha256,
            expected_selection_sha256=args.expected_selection_sha256,
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "canary-run"
    ):
        asyncio.run(
            _run_ibkr_historical_canary(
                settings,
                clock,
                runtime_lock_path=args.runtime_lock,
                contract_selection_path=args.contract_selection,
                fx_representative_id=args.fx_representative_id,
                index_representative_id=args.index_representative_id,
                commodity_representative_id=args.commodity_representative_id,
                anchor_end=args.anchor_end,
                output_path=args.output,
                execute_account_canary=args.execute_account_canary,
            )
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "profile-freeze"
    ):
        _freeze_ibkr_historical_profile(
            args.canary_evidence,
            output_path=args.output,
            canary_evidence_filename=args.canary_evidence_filename,
            frozen_by=args.frozen_by,
            frozen_at=args.frozen_at,
            maximum_in_flight_requests=args.maximum_in_flight_requests,
            request_timeout_seconds=args.request_timeout_seconds,
            retry_count=args.retry_count,
            duplicate_request_protection=args.duplicate_request_protection,
            identical_request_cooldown_seconds=args.identical_request_cooldown_seconds,
            max_requests_per_contract_window=args.max_requests_per_contract_window,
            max_requests_per_rolling_window=args.max_requests_per_rolling_window,
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "plan"
    ):
        _plan_ibkr_historical(
            contract_selection_path=args.contract_selection,
            operator_selection_path=args.operator_selection,
            capability_review_path=args.capability_review,
            catalogue_path=args.catalogue,
            probe_spec_path=args.probe_spec,
            runtime_lock_path=args.runtime_lock,
            gateway_archive=args.gateway_archive,
            api_archive=args.api_archive,
            ibc_archive=args.ibc_archive,
            expected_gateway_sha256=args.expected_gateway_sha256,
            expected_api_sha256=args.expected_api_sha256,
            expected_ibc_sha256=args.expected_ibc_sha256,
            expected_runtime_qtrad_commit=args.expected_runtime_qtrad_commit,
            expected_runtime_image_digest=args.expected_runtime_image_digest,
            expected_gateway_version=args.expected_gateway_version,
            expected_api_version=args.expected_api_version,
            expected_ibc_version=args.expected_ibc_version,
            expected_api_host=args.expected_api_host,
            expected_api_port=args.expected_api_port,
            expected_client_id_policy=args.expected_client_id_policy,
            request_profile_path=args.request_profile,
            canary_evidence_path=args.canary_evidence,
            expected_profile_frozen_by=args.profile_frozen_by,
            expected_profile_frozen_at=args.profile_frozen_at,
            maximum_in_flight_requests=args.maximum_in_flight_requests,
            request_timeout_seconds=args.request_timeout_seconds,
            retry_count=args.retry_count,
            duplicate_request_protection=args.duplicate_request_protection,
            identical_request_cooldown_seconds=args.identical_request_cooldown_seconds,
            max_requests_per_contract_window=args.max_requests_per_contract_window,
            max_requests_per_rolling_window=args.max_requests_per_rolling_window,
            start=args.start,
            end=args.end,
            planner_image_digest=args.planner_image_digest,
            output_path=args.output,
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "plan-verify"
    ):
        _verify_ibkr_historical_plan(
            plan_path=args.plan,
            contract_selection_path=args.contract_selection,
            operator_selection_path=args.operator_selection,
            capability_review_path=args.capability_review,
            catalogue_path=args.catalogue,
            probe_spec_path=args.probe_spec,
            runtime_lock_path=args.runtime_lock,
            gateway_archive=args.gateway_archive,
            api_archive=args.api_archive,
            ibc_archive=args.ibc_archive,
            expected_gateway_sha256=args.expected_gateway_sha256,
            expected_api_sha256=args.expected_api_sha256,
            expected_ibc_sha256=args.expected_ibc_sha256,
            expected_runtime_qtrad_commit=args.expected_runtime_qtrad_commit,
            expected_runtime_image_digest=args.expected_runtime_image_digest,
            expected_gateway_version=args.expected_gateway_version,
            expected_api_version=args.expected_api_version,
            expected_ibc_version=args.expected_ibc_version,
            expected_api_host=args.expected_api_host,
            expected_api_port=args.expected_api_port,
            expected_client_id_policy=args.expected_client_id_policy,
            request_profile_path=args.request_profile,
            canary_evidence_path=args.canary_evidence,
            expected_profile_frozen_by=args.profile_frozen_by,
            expected_profile_frozen_at=args.profile_frozen_at,
            maximum_in_flight_requests=args.maximum_in_flight_requests,
            request_timeout_seconds=args.request_timeout_seconds,
            retry_count=args.retry_count,
            duplicate_request_protection=args.duplicate_request_protection,
            identical_request_cooldown_seconds=args.identical_request_cooldown_seconds,
            max_requests_per_contract_window=args.max_requests_per_contract_window,
            max_requests_per_rolling_window=args.max_requests_per_rolling_window,
            expected_start=args.start,
            expected_end=args.end,
            planner_image_digest=args.planner_image_digest,
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "register"
    ):
        asyncio.run(
            _register_ibkr_historical_plan(
                settings,
                clock,
                plan_path=args.plan,
                confirmed_plan_hash=args.confirm_plan_hash,
                contract_selection_path=args.contract_selection,
                operator_selection_path=args.operator_selection,
                capability_review_path=args.capability_review,
                catalogue_path=args.catalogue,
                probe_spec_path=args.probe_spec,
                runtime_lock_path=args.runtime_lock,
                gateway_archive=args.gateway_archive,
                api_archive=args.api_archive,
                ibc_archive=args.ibc_archive,
                expected_gateway_sha256=args.expected_gateway_sha256,
                expected_api_sha256=args.expected_api_sha256,
                expected_ibc_sha256=args.expected_ibc_sha256,
                expected_runtime_qtrad_commit=args.expected_runtime_qtrad_commit,
                expected_runtime_image_digest=args.expected_runtime_image_digest,
                expected_gateway_version=args.expected_gateway_version,
                expected_api_version=args.expected_api_version,
                expected_ibc_version=args.expected_ibc_version,
                expected_api_host=args.expected_api_host,
                expected_api_port=args.expected_api_port,
                expected_client_id_policy=args.expected_client_id_policy,
                request_profile_path=args.request_profile,
                canary_evidence_path=args.canary_evidence,
                expected_profile_frozen_by=args.profile_frozen_by,
                expected_profile_frozen_at=args.profile_frozen_at,
                maximum_in_flight_requests=args.maximum_in_flight_requests,
                request_timeout_seconds=args.request_timeout_seconds,
                retry_count=args.retry_count,
                duplicate_request_protection=args.duplicate_request_protection,
                identical_request_cooldown_seconds=args.identical_request_cooldown_seconds,
                max_requests_per_contract_window=args.max_requests_per_contract_window,
                max_requests_per_rolling_window=args.max_requests_per_rolling_window,
                expected_start=args.start,
                expected_end=args.end,
                planner_image_digest=args.planner_image_digest,
            )
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "execute"
    ):
        asyncio.run(
            _execute_ibkr_historical_plan(
                settings,
                clock,
                plan_id=args.plan_id,
                contract_selection_path=args.contract_selection,
                operator_selection_path=args.operator_selection,
                capability_review_path=args.capability_review,
                catalogue_path=args.catalogue,
                probe_spec_path=args.probe_spec,
                runtime_lock_path=args.runtime_lock,
                gateway_archive=args.gateway_archive,
                api_archive=args.api_archive,
                ibc_archive=args.ibc_archive,
                expected_gateway_sha256=args.expected_gateway_sha256,
                expected_api_sha256=args.expected_api_sha256,
                expected_ibc_sha256=args.expected_ibc_sha256,
                request_profile_path=args.request_profile,
                canary_evidence_path=args.canary_evidence,
                expected_profile_frozen_by=args.profile_frozen_by,
                expected_profile_frozen_at=args.profile_frozen_at,
                maximum_in_flight_requests=args.maximum_in_flight_requests,
                request_timeout_seconds=args.request_timeout_seconds,
                retry_count=args.retry_count,
                duplicate_request_protection=args.duplicate_request_protection,
                identical_request_cooldown_seconds=args.identical_request_cooldown_seconds,
                max_requests_per_contract_window=args.max_requests_per_contract_window,
                max_requests_per_rolling_window=args.max_requests_per_rolling_window,
                expected_start=args.start,
                expected_end=args.end,
            )
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "result-build"
    ):
        asyncio.run(
            _build_ibkr_historical_result(
                settings,
                clock,
                plan_path=args.plan,
                output_path=args.output,
            )
        )
    elif (
        args.command == "historical"
        and args.historical_provider == "ibkr"
        and args.historical_ibkr_command == "verify"
    ):
        _verify_ibkr_historical_result(args.result)
    elif args.command == "instruments" and args.instrument_command == "promote":
        _promote_universe(
            clock,
            catalogue_path=args.catalogue,
            review_path=args.review,
            selections_path=args.selections,
            release_name=args.release_name,
            output_path=args.output,
        )
    elif args.command == "ingest":
        asyncio.run(
            _ingest(
                settings,
                clock,
                maximum_seconds=args.max_seconds,
                force_reconnect_after_seconds=args.force_reconnect_after_seconds,
            )
        )
    elif args.command == "backfill" and args.backfill_command == "plan":
        asyncio.run(
            _plan_backfill(
                settings,
                clock,
                universe_path=args.universe,
                start=args.start,
                end=args.end,
                remaining_allowance=args.remaining_allowance,
                output_path=args.output,
                instrument_ids=args.instruments,
            )
        )
    elif args.command == "backfill" and args.backfill_command == "register":
        asyncio.run(
            _register_backfill(
                settings,
                plan_path=args.plan,
                confirmed_plan_hash=args.confirm_plan_hash,
            )
        )
    elif args.command == "backfill" and args.backfill_command == "execute":
        asyncio.run(_execute_backfill(settings, clock, plan_hash=args.plan_hash))
    elif args.command == "qualification" and args.qualification_command == "gap-history":
        asyncio.run(
            _review_qualification_gap_history(
                settings,
                clock,
                evidence_path=args.evidence,
                plan_path=args.plan,
                plan_set_path=args.plan_set,
                manifest_path=args.manifest,
                output_path=args.output,
            )
        )
    elif args.command == "qualification" and args.qualification_command == "gap-plan":
        asyncio.run(
            _plan_qualification_gap_history(
                settings,
                clock,
                evidence_path=args.evidence,
                snapshot_import_path=args.snapshot_import_evidence,
                universe_path=args.universe,
                remaining_allowance=args.remaining_allowance,
                output_path=args.output,
            )
        )
    elif args.command == "qualification" and args.qualification_command == "gap-register":
        asyncio.run(
            _register_qualification_gap_plan_set(
                settings,
                plan_set_path=args.plan_set,
                snapshot_import_path=args.snapshot_import_evidence,
                confirmed_plan_set_hash=args.confirm_plan_set_hash,
            )
        )
    elif args.command == "qualification" and args.qualification_command == "gap-execute":
        asyncio.run(
            _execute_qualification_gap_plan_set(
                settings,
                clock,
                plan_set_path=args.plan_set,
                snapshot_import_path=args.snapshot_import_evidence,
                confirmed_plan_set_hash=args.confirm_plan_set_hash,
            )
        )
    elif args.command == "research" and args.research_command == "export":
        asyncio.run(
            _export(
                settings,
                clock,
                universe_path=args.universe,
                start=args.start,
                end=args.end,
                snapshot_import_path=args.snapshot_import_evidence,
            )
        )
    elif args.command == "research" and args.research_command == "rank":
        asyncio.run(
            _rank_research(
                settings,
                clock,
                manifest_path=args.manifest,
                experiment_path=args.experiment,
                snapshot_import_path=args.snapshot_import_evidence,
                output_path=args.output,
            )
        )
    elif (
        args.command == "research"
        and args.research_command == "observations"
        and args.observations_command == "build"
    ):
        asyncio.run(
            _build_research_observations(
                settings,
                clock,
                universe_path=args.universe,
                start=args.start,
                end=args.end,
                calibration_start=args.calibration_start,
                calibration_end=args.calibration_end,
                snapshot_import_path=args.snapshot_import_evidence,
                availability_percentile=args.availability_percentile,
                availability_safety_margin=timedelta(
                    seconds=args.availability_safety_margin_seconds
                ),
            )
        )
    elif (
        args.command == "research"
        and args.research_command == "observations"
        and args.observations_command == "verify"
    ):
        asyncio.run(_verify_research_observations(settings, clock, args.manifest))
    elif (
        args.command == "research"
        and args.research_command == "observations"
        and args.observations_command == "build-provider-history"
    ):
        _build_provider_history(
            historical_result_path=args.historical_result,
            availability_delay=args.availability_delay,
            output_path=args.output,
        )
    elif (
        args.command == "research"
        and args.research_command == "observations"
        and args.observations_command == "verify-provider-history"
    ):
        _verify_provider_history(args.manifest)
    elif (
        args.command == "research"
        and args.research_command == "foundation"
        and args.foundation_command == "build"
    ):
        asyncio.run(
            _build_foundation_bundle(
                settings,
                clock,
                observations_manifest_path=args.observations_manifest,
                provider_history_manifest_path=args.provider_history_manifest,
                configuration_path=args.configuration,
                output_path=args.output,
            )
        )
    elif (
        args.command == "research"
        and args.research_command == "foundation"
        and args.foundation_command == "verify"
    ):
        asyncio.run(_verify_foundation_bundle(settings, clock, args.bundle))
    elif (
        args.command == "research"
        and args.research_command == "foundation"
        and args.foundation_command == "readiness"
    ):
        _report_ibkr_foundation_readiness(args.bundle)
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "readiness"
    ):
        asyncio.run(
            _report_r2_readiness(
                settings,
                clock,
                foundation_bundle_path=args.foundation_bundle,
                experiment_path=args.experiment,
                software_bundle_path=args.software_bundle,
                output_path=args.output,
            )
        )
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "oof-build"
    ):
        asyncio.run(
            _build_r2_oof(
                settings,
                clock,
                foundation_bundle_path=args.foundation_bundle,
                experiment_path=args.experiment,
                feature_arguments=args.feature_manifest,
                output_path=args.output,
            )
        )
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "oof-verify"
    ):
        bundle = verify_oof_bundle(args.bundle)
        print(json.dumps(bundle.as_json(), sort_keys=True))
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "selection-freeze"
    ):
        selection_freeze(
            oof_bundle_path=args.oof_bundle,
            frozen_by=args.frozen_by,
            output=args.output,
        )
        print(json.dumps({"selection": str(args.output)}, sort_keys=True))
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "software-build"
    ):
        build_software_bundle(
            representative_oof_bundle_path=args.representative_oof_bundle,
            representative_selection_path=args.representative_selection,
            output=args.output,
        )
        print(json.dumps({"software_bundle": str(args.output / "manifest.json")}, sort_keys=True))
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "software-verify"
    ):
        bundle = verify_software_bundle(args.bundle)
        print(json.dumps(bundle.as_json(), sort_keys=True))
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "features"
    ):
        asyncio.run(
            _materialise_r2_features(
                settings,
                clock,
                foundation_bundle_path=args.foundation_bundle,
                experiment_path=args.experiment,
                feature_set_name=args.feature_set,
                output_path=args.output,
            )
        )
    elif (
        args.command == "research"
        and args.research_command == "baselines"
        and args.baselines_command == "features-verify"
    ):
        asyncio.run(
            _verify_persisted_r2_features(
                settings,
                clock,
                foundation_bundle_path=args.foundation_bundle,
                experiment_path=args.experiment,
                feature_set_name=args.feature_set,
                manifest_path=args.manifest,
            )
        )
    elif args.command == "replay":
        asyncio.run(_replay(settings, clock, args.manifest))
    elif args.command == "projections" and args.projection_command == "rebuild":
        asyncio.run(_rebuild(settings))
    elif args.command == "storage" and args.storage_command == "snapshot":
        asyncio.run(
            _storage_snapshot(
                settings,
                universe_path=args.universe,
                output_path=args.output,
            )
        )
    elif args.command == "storage" and args.storage_command == "compare":
        _compare_storage_snapshots(args.before, args.after, args.output)
    elif args.command == "storage" and args.storage_command == "contrast":
        _contrast_storage_comparisons(args.baseline, args.candidate, args.output)
    elif args.command == "storage" and args.storage_command == "review":
        _record_storage_active_market_review(args.comparison, args.review, args.output)
    elif args.command == "storage" and args.storage_command == "qualify":
        _qualify_storage_contrast(
            args.contrast,
            args.baseline_review,
            args.candidate_review,
            args.output,
        )
    elif args.command == "feed" and args.feed_command == "verify":
        _verify_capture_feed_pages(
            source_id=args.source_id,
            universe_name=args.universe_name,
            configuration_hash=args.configuration_hash,
            after_position=args.after_position,
            page_paths=args.pages,
        )
    elif args.command == "feed" and args.feed_command == "probe":
        asyncio.run(
            _probe_capture_feed(
                endpoint=args.endpoint,
                source_id=args.source_id,
                universe_name=args.universe_name,
                configuration_hash=args.configuration_hash,
                after_position=args.after_position,
                limit=args.limit,
            )
        )
    elif args.command == "api":
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    else:
        raise RuntimeError("unhandled command")


async def _report_r2_readiness(
    settings: Settings,
    clock: Clock,
    *,
    foundation_bundle_path: Path,
    experiment_path: Path,
    software_bundle_path: Path | None,
    output_path: Path,
) -> None:
    verified = await verify_foundation_bundle(
        root=settings.research_root,
        bundle_path=foundation_bundle_path,
        clock=clock,
    )
    experiment = load_r2_experiment(experiment_path)
    software_verified = False
    if software_bundle_path is not None:
        software = await verify_software_bundle_async(software_bundle_path)
        representative = verify_r2_oof_bundle(
            software_bundle_path.parent / software.representative_oof_bundle.path
        )
        software_verified = (
            representative.foundation_bundle_id == verified.bundle.bundle_id
            and representative.experiment_configuration_id == experiment.configuration_id
            and representative.source_class is experiment.market_data_source_class
            and representative.evidence_class is experiment.evidence_class
        )
        if not software_verified:
            raise ValueError("software bundle does not bind the exact foundation and experiment")
    report = evaluate_r2_readiness(verified, experiment, software_verified=software_verified)
    write_r2_readiness(output_path, report)
    print(json.dumps(report.as_json(), sort_keys=True))


async def _build_r2_oof(
    settings: Settings,
    clock: Clock,
    *,
    foundation_bundle_path: Path,
    experiment_path: Path,
    feature_arguments: list[str],
    output_path: Path,
) -> None:
    verified = await verify_foundation_bundle(
        root=settings.research_root,
        bundle_path=foundation_bundle_path,
        clock=clock,
    )
    experiment, feature_paths = load_experiment_and_feature_paths(
        experiment_path=experiment_path,
        feature_arguments=feature_arguments,
    )
    manifest = build_oof_bundle(
        verified=verified,
        experiment=experiment,
        feature_manifest_paths=feature_paths,
        research_root=settings.research_root,
        clock=clock,
        output=output_path,
        replay_inputs={
            "foundation": foundation_bundle_path,
            "experiment": experiment_path,
            **feature_paths,
        },
    )
    print(json.dumps({"oof_bundle": str(manifest)}, sort_keys=True))


async def _materialise_r2_features(
    settings: Settings,
    clock: Clock,
    *,
    foundation_bundle_path: Path,
    experiment_path: Path,
    feature_set_name: str,
    output_path: Path,
) -> None:
    verified = await verify_foundation_bundle(
        root=settings.research_root,
        bundle_path=foundation_bundle_path,
        clock=clock,
    )
    experiment = load_r2_experiment(experiment_path)
    foundation = R2FoundationInputs(
        bundle=verified.bundle,
        configuration=verified.configuration,
        observations=verified.observations,
        panel=verified.panel,
        targets=verified.targets,
        folds=verified.folds,
        availability_evidence=verified.availability_evidence,
    )
    schema = feature_schema_for_set(experiment, feature_set_name)
    set_identity = feature_set_id(
        experiment.configuration_id,
        feature_set_name,
        schema,
        experiment.market_data_source_class,
    )
    store = ParquetR2FeatureStore(settings.research_root, clock)
    manifest = store.write(
        output_path,
        iter_raw_feature_rows(
            foundation,
            experiment,
            feature_set_name=feature_set_name,
        ),
        feature_set_name=feature_set_name,
        feature_set_id=set_identity,
        feature_schema=schema,
        observation_dataset_id=verified.observations.dataset_id,
        panel_dataset_id=verified.panel.dataset_id,
        target_dataset_id=verified.targets.dataset_id,
        fold_dataset_id=verified.folds.dataset_id,
        experiment_configuration_id=experiment.configuration_id,
        evidence_class=experiment.evidence_class,
        holdout_excluded=True,
        application_version=__version__,
        image_identity=settings.image,
    )
    verify_raw_feature_manifest_bindings(
        manifest,
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    row_count = verify_raw_feature_rows(
        store.iter_rows(output_path),
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    if row_count != manifest.row_count:
        raise ValueError("verified R2 feature row count differs from its manifest")
    print(json.dumps(_r2_feature_manifest_summary(manifest), sort_keys=True))


async def _verify_persisted_r2_features(
    settings: Settings,
    clock: Clock,
    *,
    foundation_bundle_path: Path,
    experiment_path: Path,
    feature_set_name: str,
    manifest_path: Path,
) -> None:
    verified = await verify_foundation_bundle(
        root=settings.research_root,
        bundle_path=foundation_bundle_path,
        clock=clock,
    )
    experiment = load_r2_experiment(experiment_path)
    foundation = R2FoundationInputs(
        bundle=verified.bundle,
        configuration=verified.configuration,
        observations=verified.observations,
        panel=verified.panel,
        targets=verified.targets,
        folds=verified.folds,
        availability_evidence=verified.availability_evidence,
    )
    store = ParquetR2FeatureStore(settings.research_root, clock)
    manifest = store.read_manifest(manifest_path)
    verify_raw_feature_manifest_bindings(
        manifest,
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    row_count = verify_raw_feature_rows(
        store.iter_rows(manifest_path),
        foundation,
        experiment,
        feature_set_name=feature_set_name,
    )
    if row_count != manifest.row_count:
        raise ValueError("verified R2 feature row count differs from its manifest")
    print(json.dumps(_r2_feature_manifest_summary(manifest), sort_keys=True))


def _r2_feature_manifest_summary(
    manifest: R2FeatureManifest,
) -> dict[str, JsonValue]:
    return {
        "contract": manifest.CONTRACT,
        "semantic_dataset_id": manifest.semantic_dataset_id,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "manifest_path": manifest.manifest_path,
        "feature_set_name": manifest.feature_set_name,
        "feature_set_id": manifest.feature_set_id,
        "rows": manifest.row_count,
        "chunks": len(manifest.chunks),
        "chunk_row_limit": manifest.chunk_row_limit,
    }


async def _build_research_observations(
    settings: Settings,
    clock: Clock,
    *,
    universe_path: Path,
    start: datetime,
    end: datetime,
    calibration_start: datetime,
    calibration_end: datetime,
    snapshot_import_path: Path,
    availability_percentile: float,
    availability_safety_margin: timedelta,
) -> None:
    if end <= start:
        raise ValueError("observation build end must follow start")
    if calibration_end <= calibration_start or calibration_start < start or calibration_end > end:
        raise ValueError("availability calibration range must be contained in observation bounds")
    if availability_safety_margin < timedelta(0):
        raise ValueError("availability safety margin must not be negative")
    universe = load_capture_universe(universe_path)
    snapshot = load_research_snapshot_import(snapshot_import_path)
    _validate_observation_snapshot(settings, universe, snapshot, required_end=end)
    await _require_database_at_migration_head(settings)
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    instruments = tuple(instrument.instrument_id for instrument in universe.instruments)
    encoded_instruments = json.dumps([str(instrument_id) for instrument_id in instruments])
    try:
        candidates = await store.read_quote_derived_bar_candidates(
            instrument_ids=instruments,
            interval_start=start,
            interval_end=end,
        )
        gap_rows = await store.query(
            """
            SELECT instrument_id, interval_start, interval_end, reason, detected_at, repaired_at
            FROM read_model.data_gaps
            WHERE instrument_id IN (
                SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
            )
              AND interval_start < :interval_end
              AND interval_end > :interval_start
            ORDER BY instrument_id, interval_start, detected_at
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
        listing_rows = await store.query(
            """
            SELECT instrument_id, valid_from, valid_to
            FROM reference.provider_listings
            WHERE instrument_id IN (
                SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
            )
              AND valid_from < :interval_end
              AND COALESCE(valid_to, :interval_end) > :interval_start
            ORDER BY instrument_id, valid_from, valid_to
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
    finally:
        await engine.dispose()
    dataset = build_observation_dataset(
        candidates,
        configuration={
            "universe_name": universe.name,
            "universe_configuration_hash": universe.configuration_hash,
            "ordered_instruments": [str(instrument_id) for instrument_id in instruments],
            "interval_start": start.isoformat(),
            "interval_end": end.isoformat(),
        },
        source_dataset_ids=(snapshot.import_sha256,),
        selection_policies={
            "provenance": BarProvenance.QUOTE_DERIVED.value,
            "availability_basis": "persisted_at",
            "canonical_lineage": "GLOBAL_POSITION_EXACT",
        },
    )
    availability = measure_availability_delay(
        dataset,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
        configured_percentile=availability_percentile,
        safety_margin=availability_safety_margin,
        grid_resolution=timedelta(minutes=1),
    )
    revisions = measure_revision_delay(
        dataset,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
    )
    gaps = tuple(_data_gap_from_row(row) for row in gap_rows)
    active_intervals = _source_active_intervals(instruments, listing_rows, start=start, end=end)
    event_types: dict[str, int] = {}
    for row in dataset.rows:
        event_types[row.event_type] = event_types.get(row.event_type, 0) + 1
    build_evidence: dict[str, JsonValue] = {
        "availability_delay_report": availability.as_json(),
        "revision_delay_report": revisions.as_json(),
        "data_gaps": [_data_gap_json(gap) for gap in gaps],
        "source_active_intervals": {
            instrument_id: [[left.isoformat(), right.isoformat()] for left, right in intervals]
            for instrument_id, intervals in active_intervals.items()
        },
        "lineage_summary": {
            "row_count": len(dataset.rows),
            "event_type_counts": dict(sorted(event_types.items())),
            "minimum_global_position": min(
                (row.global_position for row in dataset.rows), default=None
            ),
            "maximum_global_position": max(
                (row.global_position for row in dataset.rows), default=None
            ),
        },
        "observation_bounds": {
            "interval_start": start.isoformat(),
            "interval_end": end.isoformat(),
        },
    }
    research = ParquetResearchStore(settings.research_root, clock)
    manifest = await research.write_observations(
        dataset,
        metadata={
            "universe_name": universe.name,
            "universe_configuration_hash": universe.configuration_hash,
            "revision_count": sum(row.revision > 1 for row in dataset.rows),
        },
        application_version=__version__,
        image_identity=settings.image,
        source_snapshot=research_snapshot_metadata(snapshot),
        build_evidence=build_evidence,
    )
    await ParquetResearchStore(settings.research_root, clock).read_observations(
        manifest.manifest_id
    )
    await _verify_research_observations(
        settings,
        clock,
        settings.research_root / "manifests" / f"{manifest.manifest_id}.json",
    )


async def _verify_research_observations(
    settings: Settings, clock: Clock, manifest_path: Path
) -> None:
    expected_directory = settings.research_root.resolve() / "manifests"
    if manifest_path.parent.resolve() != expected_directory:
        raise ValueError("observation manifest must be inside the configured research root")
    verified = await ParquetObservationStore(settings.research_root, clock).verify(
        manifest_path.stem
    )
    dataset = await ParquetObservationStore(settings.research_root, clock).read_observations(
        manifest_path.stem
    )
    evidence = verify_observation_build_evidence(verified, dataset)
    print(
        json.dumps(
            {
                "contract": verified.contract,
                "dataset_id": dataset.dataset_id,
                "manifest_id": verified.manifest_id,
                "manifest_sha256": verified.manifest_sha256,
                "rows": len(dataset.rows),
                "files": len(verified.files),
                "availability_delay_report": evidence.payload["availability_delay_report"],
                "revision_delay_report": evidence.payload["revision_delay_report"],
            },
            sort_keys=True,
        )
    )


def _build_provider_history(
    *,
    historical_result_path: Path,
    availability_delay: timedelta,
    output_path: Path,
) -> None:
    source_artifact = verify_ibkr_historical_result_stream(historical_result_path)
    for _ in source_artifact.iter_request_results():
        pass
    dataset = build_provider_history_dataset(
        source_artifact,
        availability_delay=availability_delay,
    )
    manifest_path = publish_provider_history(
        output_path,
        source_manifest=historical_result_path,
        source_artifact=source_artifact,
        dataset=dataset,
    )
    verified = verify_provider_history(manifest_path)
    if verified.dataset_sha256 != dataset.dataset_sha256:
        raise RuntimeError("provider-history changed between publication and verification")
    print(
        json.dumps(
            {
                "contract": verified.CONTRACT,
                "manifest": str(manifest_path),
                "dataset_sha256": verified.dataset_sha256,
                "availability_delay": verified.availability_policy.delay_text,
                "rows": verified.row_count,
                "verified": True,
            },
            sort_keys=True,
        )
    )


def _verify_provider_history(manifest_path: Path) -> None:
    dataset = verify_provider_history(manifest_path)
    print(
        json.dumps(
            {
                "contract": dataset.CONTRACT,
                "manifest": str(manifest_path),
                "dataset_sha256": dataset.dataset_sha256,
                "availability_delay": dataset.availability_policy.delay_text,
                "rows": dataset.row_count,
                "verified": True,
            },
            sort_keys=True,
        )
    )


async def _build_foundation_bundle(
    settings: Settings,
    clock: Clock,
    *,
    observations_manifest_path: Path | None,
    provider_history_manifest_path: Path | None,
    configuration_path: Path,
    output_path: Path,
) -> None:
    if (observations_manifest_path is None) == (provider_history_manifest_path is None):
        raise ValueError("exactly one foundation source must be provided")
    configuration = load_foundation_config(configuration_path)
    if provider_history_manifest_path is not None:
        build = write_ibkr_foundation(
            output_path,
            provider_manifest=provider_history_manifest_path,
            configuration=configuration,
        )
        print(
            json.dumps(
                {
                    "contract": build.readiness.CONTRACT,
                    "output": str(output_path),
                    "source_class": "IBKR_HISTORICAL_RESEARCH",
                    "readiness": build.readiness.as_json(),
                    "provider_history_dataset_sha256": (build.provider_history.dataset_sha256),
                },
                sort_keys=True,
            )
        )
        return

    if observations_manifest_path is None:
        raise ValueError("observation manifest must be provided for the native source")
    expected_directory = settings.research_root.resolve() / "manifests"
    if observations_manifest_path.parent.resolve() != expected_directory:
        raise ValueError("observation manifest must be inside the configured research root")
    observation_store = ParquetResearchStore(settings.research_root, clock)
    observations = await observation_store.read_observations(observations_manifest_path.stem)
    observation_manifest = await ParquetObservationStore(settings.research_root, clock).verify(
        observations_manifest_path.stem
    )
    evidence = verify_observation_build_evidence(observation_manifest, observations)
    verify_foundation_configuration_evidence(configuration, observations, evidence)
    panel = build_asof_panel(
        observations,
        configuration,
        gaps=evidence.gaps,
        source_active_intervals=evidence.source_active_intervals,
    )
    targets = build_frozen_targets(
        observations,
        configuration,
        horizons=configuration.target_horizons,
    )
    folds = build_expanding_folds(targets, configuration)
    forecasts = build_zero_return_forecasts(panel, targets, folds, configuration)
    bundle = await persist_foundation_bundle(
        root=settings.research_root,
        clock=clock,
        output_path=output_path,
        observation_manifest=observation_manifest,
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        availability_evidence=evidence.payload,
        application_version=__version__,
        image_identity=settings.image,
    )
    print(
        json.dumps(
            {
                "bundle_id": bundle.bundle_id,
                "output": str(output_path),
                "children": {child.name: child.manifest_id for child in bundle.children},
            },
            sort_keys=True,
        )
    )


async def _verify_foundation_bundle(settings: Settings, clock: Clock, bundle_path: Path) -> None:
    if bundle_path.is_file() and not bundle_path.is_symlink():
        try:
            document = json.loads(bundle_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            document = None
        if (
            isinstance(document, dict)
            and document.get("contract") == "qtrad-ibkr-historical-foundation-v1"
        ):
            verified = verify_ibkr_foundation(bundle_path)
            print(
                json.dumps(
                    {
                        "contract": verified.readiness.CONTRACT,
                        "bundle": str(bundle_path),
                        "source_class": "IBKR_HISTORICAL_RESEARCH",
                        "readiness": verified.readiness.as_json(),
                    },
                    sort_keys=True,
                )
            )
            return
    verified = await verify_foundation_bundle(
        root=settings.research_root,
        bundle_path=bundle_path,
        clock=clock,
    )
    print(
        json.dumps(
            {
                "contract": verified.bundle.CONTRACT,
                "bundle_id": verified.bundle.bundle_id,
                "children": {
                    child.name: {
                        "dataset_id": child.dataset_id,
                        "manifest_id": child.manifest_id,
                        "rows": child.row_count,
                    }
                    for child in verified.bundle.children
                },
                "coverage": [summary.as_json() for summary in verified.bundle.coverage],
            },
            sort_keys=True,
        )
    )


def _report_ibkr_foundation_readiness(bundle_path: Path) -> None:
    """Verify and report Stage 8 readiness only; no R2 artefact is loaded."""
    verified = verify_ibkr_foundation(bundle_path)
    print(json.dumps(verified.readiness.as_json(), sort_keys=True))


def _validate_observation_snapshot(
    settings: Settings,
    universe: CaptureUniverse,
    snapshot: ResearchSnapshotImport,
    *,
    required_end: datetime,
) -> None:
    database_name = make_url(settings.database_url).database
    if database_name is None or not database_name.startswith("qtrad_research_"):
        raise ValueError("observation build requires an isolated research database")
    if database_name != snapshot.target_database:
        raise ValueError("research snapshot evidence does not identify the configured database")
    if settings.capture_source_id != snapshot.capture_source_id:
        raise ValueError("research snapshot evidence does not identify the configured source")
    if universe.configuration_hash != snapshot.universe_hash:
        raise ValueError("research snapshot evidence does not identify the selected universe")
    if snapshot.universe_name != universe.name:
        raise ValueError("research snapshot evidence has a different universe name")
    if snapshot.source_created_at < required_end:
        raise ValueError("research snapshot predates the required observation range")


def _data_gap_from_row(row: dict[str, object]) -> DataGap:
    repaired = row["repaired_at"]
    return DataGap(
        instrument_id=InstrumentId(str(row["instrument_id"])),
        interval_start=_as_utc(row["interval_start"]),
        interval_end=_as_utc(row["interval_end"]),
        reason=str(row["reason"]),
        detected_at=_as_utc(row["detected_at"]),
        repaired_at=None if repaired is None else _as_utc(repaired),
    )


def _data_gap_json(gap: DataGap) -> dict[str, JsonValue]:
    return {
        "instrument_id": str(gap.instrument_id),
        "interval_start": gap.interval_start.isoformat(),
        "interval_end": gap.interval_end.isoformat(),
        "reason": gap.reason,
        "detected_at": gap.detected_at.isoformat(),
        "repaired_at": gap.repaired_at.isoformat() if gap.repaired_at is not None else None,
    }


def _source_active_intervals(
    instruments: Sequence[InstrumentId],
    rows: Sequence[dict[str, object]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, tuple[tuple[datetime, datetime], ...]]:
    intervals: dict[str, list[tuple[datetime, datetime]]] = {
        str(instrument_id): [] for instrument_id in instruments
    }
    for row in rows:
        instrument_id = str(row["instrument_id"])
        active_start = max(start, _as_utc(row["valid_from"]))
        valid_to = row["valid_to"]
        active_end = min(end, end if valid_to is None else _as_utc(valid_to))
        if active_end > active_start:
            intervals[instrument_id].append((active_start, active_end))
    return {
        instrument_id: tuple(sorted(set(values)))
        for instrument_id, values in sorted(intervals.items())
    }


async def _review_qualification_gap_history(
    settings: Settings,
    clock: Clock,
    *,
    evidence_path: Path,
    plan_path: Path | None,
    plan_set_path: Path | None,
    manifest_path: Path,
    output_path: Path,
) -> None:
    evidence = load_qualification_evidence(evidence_path)
    if (plan_path is None) == (plan_set_path is None):
        raise ValueError("exactly one backfill plan or qualification plan set is required")
    expected_manifest_directory = settings.research_root.resolve() / "manifests"
    if manifest_path.parent.resolve() != expected_manifest_directory:
        raise ValueError("research manifest must be inside the configured research root")
    manifest_id = manifest_path.stem
    research = ParquetResearchStore(settings.research_root, clock)
    manifest = await research.read_manifest(manifest_id)
    bars = await research.read_bars(manifest_id)
    if plan_set_path is not None:
        plan_set, plans = load_qualification_gap_plan_set(plan_set_path)
        artifact = build_qualification_gap_plan_set_history_artifact(
            evidence=evidence,
            plan_set=plan_set,
            plans=plans,
            manifest=manifest,
            bars=bars,
            generated_at=clock.now(),
        )
    else:
        if plan_path is None:
            raise AssertionError("single-plan path must be present")
        plan = load_backfill_plan(plan_path)
        artifact = build_qualification_gap_history_artifact(
            evidence=evidence,
            plan=plan,
            manifest=manifest,
            bars=bars,
            generated_at=clock.now(),
        )
    write_qualification_gap_history_artifact(output_path, artifact)
    print(
        json.dumps(
            {
                "artifact_sha256": artifact.artifact_sha256,
                "gaps": len(artifact.results),
                "historical_data_present": sum(
                    result.historical_data_status == "HISTORICAL_DATA_PRESENT"
                    for result in artifact.results
                ),
                "output": str(output_path),
            },
            sort_keys=True,
        )
    )


async def _plan_qualification_gap_history(
    settings: Settings,
    clock: Clock,
    *,
    evidence_path: Path,
    snapshot_import_path: Path,
    universe_path: Path,
    remaining_allowance: int,
    output_path: Path,
) -> None:
    evidence = load_qualification_evidence(evidence_path)
    snapshot = load_research_snapshot_import(snapshot_import_path)
    universe = load_capture_universe(universe_path)
    database_name = make_url(settings.database_url).database
    if database_name is None:
        raise ValueError("configured database URL does not identify a database")
    validate_qualification_gap_snapshot(
        evidence=evidence,
        snapshot=snapshot,
        database_name=database_name,
        configured_capture_source_id=settings.capture_source_id,
        universe_name=universe.name,
        universe_hash=universe.configuration_hash,
    )
    await _require_database_at_migration_head(settings)
    scopes = qualification_gap_backfill_scopes(evidence)
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(f"qualification gap plan-set output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"qualification gap plan-set output directory does not exist: {output_path.parent}"
        )
    plan_paths = tuple(
        output_path.with_name(f"{output_path.stem}-{index:03d}.json")
        for index in range(1, len(scopes) + 1)
    )
    existing = [path for path in plan_paths if path.exists() or path.is_symlink()]
    if existing:
        raise FileExistsError(f"qualification gap plan output already exists: {existing[0]}")
    engine = _engine(settings)
    try:
        store = PostgresAuditStore(engine)
        all_instruments = tuple(
            sorted({instrument for scope in scopes for instrument in scope.instrument_ids})
        )
        listings = await store.active_provider_listings(all_instruments)
    finally:
        await engine.dispose()
    observed_at = clock.now()
    plans = tuple(
        build_backfill_plan(
            universe_name=universe.name,
            universe_hash=universe.configuration_hash,
            instrument_ids=scope.instrument_ids,
            listings=tuple(
                listing for listing in listings if listing.instrument_id in scope.instrument_ids
            ),
            preferred_epics=universe.preferred_epics,
            start=scope.start,
            end=scope.end,
            remaining_allowance=remaining_allowance,
            quota_observed_at=observed_at,
            created_at=observed_at,
        )
        for scope in scopes
    )
    quota = BackfillQuotaEvidence(
        allowance_name="historical_points_weekly_operator_reported",
        remaining_points=remaining_allowance,
        observed_at=observed_at,
        reserve_fraction=Decimal("0.2"),
    )
    entries = tuple(
        QualificationGapPlanEntry(
            file=path.name,
            plan_hash=plan.plan_hash,
            gap_ids=scope.gap_ids,
            requested_points=plan.requested_points,
        )
        for path, plan, scope in zip(plan_paths, plans, scopes, strict=True)
    )
    plan_set = build_qualification_gap_plan_set(
        qualification_evidence_sha256=evidence.evidence_sha256,
        snapshot_import_sha256=snapshot.import_sha256,
        capture_source_id=evidence.release.capture_source_id,
        universe_name=universe.name,
        universe_hash=universe.configuration_hash,
        created_at=observed_at,
        remaining_allowance=remaining_allowance,
        reserve_points=remaining_allowance - quota.usable_points,
        entries=entries,
    )
    created_paths: list[Path] = []
    try:
        for path, plan in zip(plan_paths, plans, strict=True):
            write_backfill_plan(path, plan)
            created_paths.append(path)
        write_qualification_gap_plan_set(output_path, plan_set)
    except BaseException:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    print(
        json.dumps(
            {
                "plan_set_hash": plan_set.plan_set_hash,
                "plan_count": len(plan_set.entries),
                "gap_count": sum(len(entry.gap_ids) for entry in plan_set.entries),
                "requested_points": plan_set.requested_points,
                "remaining_allowance": plan_set.remaining_allowance,
                "reserve_points": plan_set.reserve_points,
                "selection_authority": False,
                "registered": False,
            },
            sort_keys=True,
        )
    )


async def _register_qualification_gap_plan_set(
    settings: Settings,
    *,
    plan_set_path: Path,
    snapshot_import_path: Path,
    confirmed_plan_set_hash: str,
) -> None:
    plan_set, plans = load_qualification_gap_plan_set(plan_set_path)
    _confirm_qualification_gap_plan_set(
        settings,
        plan_set=plan_set,
        snapshot_import_path=snapshot_import_path,
        confirmed_plan_set_hash=confirmed_plan_set_hash,
    )
    await _require_database_at_migration_head(settings)
    engine = _engine(settings)
    try:
        store = PostgresAuditStore(engine)
        statuses = [
            {
                "plan_hash": plan.plan_hash,
                "status": await store.register_backfill_plan(plan, backfill_plan_payload(plan)),
            }
            for plan in plans
        ]
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "plan_set_hash": plan_set.plan_set_hash,
                "registered_plans": len(statuses),
                "statuses": statuses,
            },
            sort_keys=True,
        )
    )


async def _execute_qualification_gap_plan_set(
    settings: Settings,
    clock: Clock,
    *,
    plan_set_path: Path,
    snapshot_import_path: Path,
    confirmed_plan_set_hash: str,
) -> None:
    plan_set, plans = load_qualification_gap_plan_set(plan_set_path)
    _confirm_qualification_gap_plan_set(
        settings,
        plan_set=plan_set,
        snapshot_import_path=snapshot_import_path,
        confirmed_plan_set_hash=confirmed_plan_set_hash,
    )
    await _require_database_at_migration_head(settings)
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    adapter = _ig_backfill_adapter(settings, clock)
    run_id: RunId | None = None
    terminal_status = "FAILED"
    results: list[dict[str, JsonValue]] = []
    current_plan_hash: str | None = None
    current_plan_claimed = False
    current_plan_completed = False
    try:
        hashes = tuple(plan.plan_hash for plan in plans)
        rows = await store.query(
            """
            SELECT plan_hash, status
            FROM ops.backfill_plans
            WHERE plan_hash IN (
                SELECT jsonb_array_elements_text(CAST(:plan_hashes AS jsonb))
            )
            """,
            {"plan_hashes": json.dumps(hashes)},
        )
        statuses = {str(row["plan_hash"]): str(row["status"]) for row in rows}
        if set(statuses) != set(hashes):
            raise RuntimeError("qualification gap plan set is not completely registered")
        run_id = await store.start_run(
            kind=RunKind.BACKFILL,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=plan_set.universe_hash,
            started_at=clock.now(),
        )
        await store.record_quota_state(
            provider="ig",
            environment="demo",
            allowance_name="historical_points_weekly_operator_reported",
            remaining=plan_set.remaining_allowance,
            observed_at=plan_set.created_at,
        )
        await adapter.connect()
        for expected_plan in plans:
            if statuses[expected_plan.plan_hash] == "COMPLETED":
                results.append(
                    {"plan_hash": expected_plan.plan_hash, "status": "ALREADY_COMPLETED"}
                )
                continue
            current_plan_hash = expected_plan.plan_hash
            current_plan_claimed = False
            current_plan_completed = False
            payload = await store.claim_backfill_plan(current_plan_hash)
            current_plan_claimed = True
            plan = decode_backfill_plan(json.dumps(payload, sort_keys=True))
            if plan != expected_plan:
                raise RuntimeError("claimed backfill plan differs from the reviewed plan set")
            listings = tuple([await store.provider_listing_version(item) for item in plan.items])
            received: dict[tuple[InstrumentId, PriceBasis], set[datetime]] = {}
            written = 0
            for request in backfill_requests(plan, listings):
                request_id = uuid4()
                await store.start_historical_request_usage(
                    request_id=request_id,
                    run_id=run_id,
                    plan_hash=plan.plan_hash,
                    instrument_id=request.instrument_id,
                    listing_id=request.listing.listing_id,
                    interval_start=request.start,
                    interval_end=request.end,
                    requested_points=request.maximum_points,
                    started_at=clock.now(),
                )
                returned_points: set[datetime] = set()
                async for bar in adapter.backfill(request):
                    _validate_planned_bar(plan, request, bar)
                    returned_points.add(bar.interval_start)
                    received.setdefault((bar.instrument_id, bar.basis), set()).add(
                        bar.interval_start
                    )
                    event = await _append_bar(store, bar, received_time=clock.now())
                    if event is not None:
                        written += 1
                await store.complete_historical_request_usage(
                    request_id,
                    returned_points=len(returned_points),
                    provider_remaining=adapter.historical_allowance_remaining,
                    completed_at=clock.now(),
                )
            observed_points = {
                (item.instrument_id, basis): len(received.get((item.instrument_id, basis), set()))
                for item in plan.items
                for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)
            }
            point_counts = tuple(observed_points.values())
            if any(points == 0 for points in point_counts) and not all(
                points == 0 for points in point_counts
            ):
                raise RuntimeError("historical diagnostic returned only a subset of required bases")
            await store.complete_backfill_plan(
                plan,
                observed_points=observed_points,
                executed_at=clock.now(),
                allow_empty=True,
            )
            current_plan_completed = True
            results.append(
                {
                    "plan_hash": plan.plan_hash,
                    "status": "COMPLETED",
                    "points_received": sum(point_counts),
                    "bars_written": written,
                }
            )
        provider_remaining = adapter.historical_allowance_remaining
        if provider_remaining is not None:
            await store.record_quota_state(
                provider="ig",
                environment="demo",
                allowance_name="historical_points_weekly_provider_reported",
                remaining=provider_remaining,
                observed_at=clock.now(),
            )
        terminal_status = "COMPLETED"
        print(
            json.dumps(
                {
                    "plan_set_hash": plan_set.plan_set_hash,
                    "plans": results,
                    "provider_remaining_allowance": provider_remaining,
                },
                sort_keys=True,
            )
        )
    except BaseException:
        if current_plan_hash is not None and current_plan_claimed and not current_plan_completed:
            await store.fail_backfill_plan(current_plan_hash, executed_at=clock.now())
        raise
    finally:
        await adapter.disconnect()
        if run_id is not None:
            await store.finish_run(
                run_id,
                status=terminal_status,
                finished_at=clock.now(),
                detail={
                    "plan_set_hash": plan_set.plan_set_hash,
                    "plan_count": len(plans),
                    "completed_or_skipped": len(results),
                    "provider_remaining_allowance": adapter.historical_allowance_remaining,
                },
            )
        await engine.dispose()


def _confirm_qualification_gap_plan_set(
    settings: Settings,
    *,
    plan_set: QualificationGapPlanSet,
    snapshot_import_path: Path,
    confirmed_plan_set_hash: str,
) -> None:
    if confirmed_plan_set_hash != plan_set.plan_set_hash:
        raise ValueError("confirmed qualification gap plan-set hash does not match reviewed set")
    snapshot = load_research_snapshot_import(snapshot_import_path)
    database_name = make_url(settings.database_url).database
    if database_name is None or not database_name.startswith("qtrad_research_"):
        raise ValueError("qualification gap plan set requires an isolated research database")
    if snapshot.target_database != database_name:
        raise ValueError("snapshot import evidence does not identify the configured database")
    if (
        snapshot.import_sha256 != plan_set.snapshot_import_sha256
        or snapshot.capture_source_id != plan_set.capture_source_id
        or settings.capture_source_id != plan_set.capture_source_id
        or snapshot.universe_hash != plan_set.universe_hash
    ):
        raise ValueError("qualification gap plan set differs from snapshot or configured identity")


async def _require_database_at_migration_head(settings: Settings) -> None:
    migration_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    if migration_head is None:
        raise RuntimeError("migration script directory has no current head")
    engine = _engine(settings)
    try:
        rows = await PostgresAuditStore(engine).query("SELECT version_num FROM alembic_version")
    finally:
        await engine.dispose()
    if len(rows) != 1 or rows[0]["version_num"] != migration_head:
        raise RuntimeError(
            "isolated research database is not at the reviewed migration head: "
            f"expected {migration_head}"
        )


def _upgrade_database(settings: Settings) -> None:
    os.environ["QTRAD_MIGRATION_DATABASE_URL"] = settings.migration_database_url
    command.upgrade(Config("alembic.ini"), "head")


def _engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def _ig_adapter(
    settings: Settings, clock: Clock, *, universe: CaptureUniverse | None = None
) -> IgDemoMarketDataAdapter:
    username, password, api_key, account_id = settings.require_ig_credentials()
    selected_universe = universe if universe is not None else _capture_universe(settings)
    return IgDemoMarketDataAdapter(
        IgDemoConfig(
            username=username,
            password=password,
            api_key=api_key,
            account_id=account_id,
        ),
        clock,
        instruments_by_id=selected_universe.instruments_by_id,
        preferred_epics=selected_universe.preferred_epics,
    )


def _ig_review_adapter(
    settings: Settings, clock: Clock, candidates: CaptureCandidates
) -> IgDemoMarketDataAdapter:
    username, password, api_key, account_id = settings.require_ig_credentials()
    return IgDemoMarketDataAdapter(
        IgDemoConfig(
            username=username,
            password=password,
            api_key=api_key,
            account_id=account_id,
        ),
        clock,
        instruments_by_id={
            instrument.instrument_id: instrument for instrument in candidates.instruments
        },
        preferred_epics={},
    )


def _ibkr_capability_adapter(settings: Settings, *, checkpoint=None, pacing_reserver=None):
    """Compose the isolated market-data-only Stage 1 adapter on explicit account-probe execution."""

    from qtrad.adapters.ibkr.capability import (
        IbkrApiIdentity,
        IbkrGatewayEndpoint,
        OfficialIbkrCapabilityAdapter,
    )

    return OfficialIbkrCapabilityAdapter(
        IbkrGatewayEndpoint(
            host=settings.ibkr_gateway_host,
            port=settings.ibkr_gateway_port,
            client_id=settings.ibkr_client_id,
        ),
        request_timeout_seconds=settings.ibkr_historical_timeout_seconds,
        upstream_recovery_timeout_seconds=settings.ibkr_upstream_recovery_timeout_seconds,
        connect_timeout_seconds=settings.ibkr_connect_timeout_seconds,
        handshake_timeout_seconds=settings.ibkr_handshake_timeout_seconds,
        server_time_timeout_seconds=settings.ibkr_server_time_timeout_seconds,
        contract_timeout_seconds=settings.ibkr_contract_timeout_seconds,
        historical_timeout_seconds=settings.ibkr_historical_timeout_seconds,
        checkpoint=checkpoint,
        pacing_reserver=pacing_reserver,
        api_identity=(
            IbkrApiIdentity(
                package_fingerprint=settings.ibkr_api_package_fingerprint,
                version=settings.ibkr_api_version,
            )
            if settings.ibkr_api_package_fingerprint is not None
            else None
        ),
    )


def _ibkr_historical_canary_adapter(
    settings: Settings,
    *,
    pacing_reserver,
    clock: Clock,
):
    """Compose the bounded official Stage 5 historical adapter."""

    from qtrad.adapters.ibkr.capability import IbkrApiIdentity, IbkrGatewayEndpoint
    from qtrad.adapters.ibkr.historical import OfficialIbkrHistoricalAdapter

    api_package_fingerprint = settings.ibkr_api_package_fingerprint
    if api_package_fingerprint is None:
        raise ValueError(
            "IBKR canary execution requires the verified official API package fingerprint"
        )
    return OfficialIbkrHistoricalAdapter(
        IbkrGatewayEndpoint(
            host=settings.ibkr_gateway_host,
            port=settings.ibkr_gateway_port,
            client_id=settings.ibkr_client_id,
        ),
        request_timeout_seconds=settings.ibkr_historical_timeout_seconds,
        upstream_recovery_timeout_seconds=settings.ibkr_upstream_recovery_timeout_seconds,
        connect_timeout_seconds=settings.ibkr_connect_timeout_seconds,
        handshake_timeout_seconds=settings.ibkr_handshake_timeout_seconds,
        server_time_timeout_seconds=settings.ibkr_server_time_timeout_seconds,
        contract_timeout_seconds=settings.ibkr_contract_timeout_seconds,
        historical_timeout_seconds=settings.ibkr_historical_timeout_seconds,
        api_identity=IbkrApiIdentity(
            package_fingerprint=api_package_fingerprint,
            version=settings.ibkr_api_version,
        ),
        pacing_reserver=pacing_reserver,
        clock=clock.now,
    )


def _ig_backfill_adapter(settings: Settings, clock: Clock) -> IgDemoMarketDataAdapter:
    username, password, api_key, account_id = settings.require_ig_credentials()
    return IgDemoMarketDataAdapter(
        IgDemoConfig(
            username=username,
            password=password,
            api_key=api_key,
            account_id=account_id,
        ),
        clock,
        instruments_by_id={},
        preferred_epics={},
    )


async def _seed(settings: Settings) -> None:
    engine = _engine(settings)
    try:
        await PostgresAuditStore(engine).seed_instruments(_capture_universe(settings).instruments)
    finally:
        await engine.dispose()


async def _plan_run_reconciliation(
    settings: Settings,
    clock: Clock,
    *,
    universe_path: Path | None,
    cutoff: datetime,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise FileExistsError(f"run reconciliation plan output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"run reconciliation plan output directory does not exist: {output_path.parent}"
        )
    universe = load_capture_universe(universe_path or settings.capture_universe_path)
    engine = _engine(settings)
    try:
        store = PostgresAuditStore(engine)
        database_name = await store.database_name()
        targets = await store.stale_running_ingestion_runs(
            cutoff=cutoff,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=universe.configuration_hash,
        )
        if not targets:
            raise ValueError("no eligible stale ingestion runs exist before the cutoff")
        plan = build_run_reconciliation_plan(
            targets=targets,
            created_at=clock.now(),
            cutoff=cutoff,
            capture_source_id=settings.capture_source_id,
            database_name=database_name,
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            application_version=__version__,
            application_image=settings.image,
            environment=BrokerEnvironment.IG_DEMO,
        )
        write_run_reconciliation_plan(output_path, plan)
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "plan_hash": plan.plan_hash,
                "target_count": len(plan.targets),
                "cutoff": plan.cutoff.isoformat().replace("+00:00", "Z"),
                "application_image": plan.application_image,
                "executed": False,
            },
            sort_keys=True,
        )
    )


async def _reconcile_runs(
    settings: Settings,
    clock: Clock,
    *,
    plan_path: Path,
    confirmed_plan_hash: str,
) -> None:
    _require_sha256_argument(confirmed_plan_hash, "run reconciliation plan hash")
    plan = load_run_reconciliation_plan(plan_path)
    if confirmed_plan_hash != plan.plan_hash:
        raise ValueError("confirmed run reconciliation hash does not match the reviewed plan")
    universe = _capture_universe(settings)
    if settings.capture_source_id != plan.capture_source_id:
        raise ValueError("run reconciliation plan targets a different capture source")
    if (
        universe.name != plan.universe_name
        or universe.configuration_hash != plan.configuration_hash
    ):
        raise ValueError("run reconciliation plan targets a different capture universe")
    if __version__ != plan.application_version or settings.image != plan.application_image:
        raise ValueError("run reconciliation plan targets a different application image")
    engine = _engine(settings)
    try:
        reconciled = await PostgresAuditStore(engine).reconcile_stale_ingestion_runs(
            plan,
            reconciled_at=clock.now(),
        )
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "plan_hash": plan.plan_hash,
                "reconciled_run_count": reconciled,
                "terminal_status": plan.terminal_status,
                "finished_at_basis": plan.finished_at_basis,
                "application_image": plan.application_image,
            },
            sort_keys=True,
        )
    )


async def _sync_instruments(
    settings: Settings, clock: Clock, *, universe_path: Path | None
) -> None:
    universe = (
        load_capture_universe(universe_path)
        if universe_path is not None
        else _capture_universe(settings)
    )
    engine = _engine(settings)
    adapter = _ig_adapter(settings, clock, universe=universe)
    store = PostgresAuditStore(engine)
    try:
        await store.seed_instruments(universe.instruments)
        await adapter.connect()
        listings = await adapter.discover_listings(
            [instrument.instrument_id for instrument in universe.instruments]
        )
        for listing in listings:
            await store.validate_provider_listing(
                listing, universe_hash=universe.configuration_hash, observed_at=clock.now()
            )
        print(
            json.dumps(
                {
                    "universe_name": universe.name,
                    "configuration_hash": universe.configuration_hash,
                    "listing_count": len(listings),
                    "listings": [str(item.listing_id) for item in listings],
                    "ingestion_started": False,
                },
                sort_keys=True,
            )
        )
    finally:
        await adapter.disconnect()
        await engine.dispose()


def _select_ibkr_instruments(
    clock: Clock,
    *,
    capability_review_path: Path,
    selection_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
    frozen_by: str,
    output_path: Path,
) -> None:
    selection = build_ibkr_contract_selection_from_files(
        capability_review_path=capability_review_path,
        selection_path=selection_path,
        catalogue_path=catalogue_path,
        probe_spec_path=probe_spec_path,
        frozen_by=frozen_by,
        frozen_at=clock.now(),
    )
    write_ibkr_contract_selection(output_path, selection)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "selection_sha256": selection.selection_sha256,
                "decision_count": len(selection.decisions),
                "acquisition_eligible_count": sum(
                    decision.acquisition_eligible for decision in selection.decisions
                ),
            },
            sort_keys=True,
        )
    )


def _plan_ibkr_historical(
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
    planner_image_digest: str,
    output_path: Path,
) -> None:
    planner_commit = derive_qtrad_commit()
    plan = build_ibkr_historical_plan_from_files(
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
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds,
            2,
            max_requests_per_contract_window,
            600,
            max_requests_per_rolling_window,
        ),
        start=start,
        end=end,
        planner_qtrad_commit=planner_commit,
        planner_qtrad_image_digest=planner_image_digest,
    )
    write_ibkr_historical_plan(output_path, plan)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "plan_sha256": plan.plan_sha256,
                "eligible_contract_count": len(plan.eligible_contracts),
                "request_count": len(plan.requests),
            },
            sort_keys=True,
        )
    )


def _verify_ibkr_historical_plan(
    *,
    plan_path: Path,
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
    expected_start: datetime,
    expected_end: datetime,
    planner_image_digest: str,
) -> None:
    plan = verify_ibkr_historical_plan(
        plan_path,
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
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds,
            2,
            max_requests_per_contract_window,
            600,
            max_requests_per_rolling_window,
        ),
        expected_start=expected_start,
        expected_end=expected_end,
        planner_qtrad_commit=derive_qtrad_commit(),
        planner_qtrad_image_digest=planner_image_digest,
    )
    print(
        json.dumps(
            {
                "plan": str(plan_path),
                "plan_sha256": plan.plan_sha256,
                "eligible_contract_count": len(plan.eligible_contracts),
                "request_count": len(plan.requests),
                "verified": True,
            },
            sort_keys=True,
        )
    )


async def _register_ibkr_historical_plan(
    settings: Settings,
    clock: Clock,
    *,
    plan_path: Path,
    confirmed_plan_hash: str,
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
    expected_start: datetime,
    expected_end: datetime,
    planner_image_digest: str,
) -> None:
    _require_sha256_argument(confirmed_plan_hash, "IBKR historical plan hash")
    plan, plan_bytes = load_ibkr_historical_plan_artifact(plan_path)
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
        identical_request_cooldown_seconds=identical_request_cooldown_seconds,
        max_requests_per_contract_window=max_requests_per_contract_window,
        max_requests_per_rolling_window=max_requests_per_rolling_window,
        start=expected_start,
        end=expected_end,
        planner_qtrad_commit=derive_qtrad_commit(),
        planner_qtrad_image_digest=planner_image_digest,
    )
    if verified.plan.as_json_value() != plan.as_json_value():
        raise ValueError("IBKR plan does not replay from its authenticated lower artefacts")
    if plan.plan_sha256 != confirmed_plan_hash:
        raise ValueError("confirmed IBKR historical plan hash does not match the reviewed plan")
    await _require_database_at_migration_head(settings)
    engine = _engine(settings)
    try:
        status = await PostgresIbkrHistoricalExecutionStore(engine).register_ibkr_historical_plan(
            plan,
            plan_bytes=plan_bytes,
            registered_at=clock.now(),
        )
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "plan_sha256": plan.plan_sha256,
                "request_count": len(plan.requests),
                "status": status.value,
            },
            sort_keys=True,
        )
    )


async def _execute_ibkr_historical_plan(
    settings: Settings,
    clock: Clock,
    *,
    plan_id: str,
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
    expected_start: datetime,
    expected_end: datetime,
) -> None:
    _require_sha256_argument(plan_id, "IBKR historical plan ID")
    await _require_database_at_migration_head(settings)
    engine = _engine(settings)
    try:
        store = PostgresIbkrHistoricalExecutionStore(engine)
        snapshot = await store.read_ibkr_historical_execution(plan_sha256=plan_id)
        plan = load_ibkr_historical_plan_bytes(snapshot.plan.plan_bytes)
        if plan.plan_sha256 != plan_id:
            raise RuntimeError("registered IBKR plan bytes do not match the requested plan ID")

        current_commit = derive_qtrad_commit()
        current_image_digest = configured_image_digest()
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
            expected_runtime_qtrad_commit=current_commit,
            expected_runtime_image_digest=current_image_digest,
            expected_gateway_version=settings.ibkr_gateway_version,
            expected_api_version=settings.ibkr_api_version,
            expected_ibc_version=settings.ibkr_ibc_version,
            expected_api_host=settings.ibkr_gateway_host,
            expected_api_port=settings.ibkr_gateway_port,
            expected_client_id_policy=settings.ibkr_client_id_policy,
            request_profile_path=request_profile_path,
            canary_evidence_path=canary_evidence_path,
            expected_profile_frozen_by=expected_profile_frozen_by,
            expected_profile_frozen_at=expected_profile_frozen_at,
            maximum_in_flight_requests=maximum_in_flight_requests,
            request_timeout_seconds=request_timeout_seconds,
            retry_count=retry_count,
            duplicate_request_protection=duplicate_request_protection,
            identical_request_cooldown_seconds=identical_request_cooldown_seconds,
            max_requests_per_contract_window=max_requests_per_contract_window,
            max_requests_per_rolling_window=max_requests_per_rolling_window,
            start=expected_start,
            end=expected_end,
            planner_qtrad_commit=plan.planner_qtrad_commit,
            planner_qtrad_image_digest=plan.planner_qtrad_image_digest,
        )
        if verified.plan.as_json_value() != plan.as_json_value():
            raise ValueError("registered IBKR plan does not replay from its lower-artifact closure")
        if not (
            verified.runtime.runtime_sha256
            == plan.runtime_sha256
            == verified.request_profile.canary_runtime_sha256
        ):
            raise ValueError("IBKR runtime identity is not bound across plan and request profile")
        api_package_fingerprint = settings.ibkr_api_package_fingerprint
        if api_package_fingerprint is None:
            raise ValueError(
                "IBKR historical execution requires the verified official API package fingerprint"
            )
        if api_package_fingerprint != verified.selection.api_package_fingerprint:
            raise ValueError("current IBKR API package fingerprint differs from the selection")
        request_profile = verified.request_profile
        verify_ibkr_historical_execution_snapshot(
            plan,
            snapshot,
            maximum_attempts=request_profile.retry_count + 1,
            allow_recoverable_started=True,
        )
        recovered = await store.recover_ibkr_historical_execution(
            plan_sha256=plan.plan_sha256,
            recovered_at=clock.now(),
            maximum_attempts=request_profile.retry_count + 1,
        )
        snapshot = await store.read_ibkr_historical_execution(plan_sha256=plan.plan_sha256)
        verify_ibkr_historical_execution_snapshot(
            plan,
            snapshot,
            maximum_attempts=request_profile.retry_count + 1,
        )

        from qtrad.adapters.ibkr.capability import IbkrApiIdentity, IbkrGatewayEndpoint
        from qtrad.adapters.ibkr.historical import OfficialIbkrHistoricalAdapter
        from qtrad.adapters.ibkr.pacing import IbkrPostgresPacing
        from qtrad.application.ibkr_execution import IbkrHistoricalExecutor

        provider = OfficialIbkrHistoricalAdapter(
            IbkrGatewayEndpoint(
                host=settings.ibkr_gateway_host,
                port=settings.ibkr_gateway_port,
                client_id=settings.ibkr_client_id,
            ),
            request_timeout_seconds=request_profile.request_timeout_seconds,
            upstream_recovery_timeout_seconds=settings.ibkr_upstream_recovery_timeout_seconds,
            connect_timeout_seconds=settings.ibkr_connect_timeout_seconds,
            handshake_timeout_seconds=settings.ibkr_handshake_timeout_seconds,
            server_time_timeout_seconds=settings.ibkr_server_time_timeout_seconds,
            contract_timeout_seconds=settings.ibkr_contract_timeout_seconds,
            historical_timeout_seconds=request_profile.request_timeout_seconds,
            api_identity=IbkrApiIdentity(
                package_fingerprint=api_package_fingerprint,
                version=settings.ibkr_api_version,
            ),
        )
        pacer = IbkrPostgresPacing(
            PostgresAuditStore(engine),
            request_profile_sha256=request_profile.profile_sha256,
            pacing_policy=request_profile.pacing_policy,
            clock=clock.now,
        )
        summary = await IbkrHistoricalExecutor(
            store,
            provider,
            pacer,
            clock=clock.now,
        ).execute_pending(
            plan,
            request_profile,
            recovered_outcomes=recovered,
        )
    finally:
        await engine.dispose()
    status_counts: dict[str, int] = {}
    for outcome in summary.outcomes:
        status = outcome.request_status.value
        status_counts[status] = status_counts.get(status, 0) + 1
    print(
        json.dumps(
            {
                "connection_generation": summary.connection_generation,
                "outcome_count": len(summary.outcomes),
                "plan_sha256": summary.plan_sha256,
                "request_status_counts": status_counts,
                "executed": True,
            },
            sort_keys=True,
        )
    )


async def _build_ibkr_historical_result(
    settings: Settings,
    clock: Clock,
    *,
    plan_path: Path,
    output_path: Path,
) -> None:
    await _require_database_at_migration_head(settings)
    plan = load_ibkr_historical_plan(plan_path)
    engine = _engine(settings)
    try:
        store = PostgresIbkrHistoricalExecutionStore(engine)
        snapshot = await store.read_ibkr_historical_execution(plan_sha256=plan.plan_sha256)
        artifact = build_ibkr_historical_result_artifact(plan, snapshot)
        if output_path.exists():
            manifest_path = output_path / "manifest.json" if output_path.is_dir() else output_path
        else:
            manifest_path = publish_ibkr_historical_result(output_path, artifact)
        verified = verify_ibkr_historical_result(manifest_path)
        if verified.aggregate.aggregate_sha256 != artifact.aggregate.aggregate_sha256:
            raise RuntimeError("IBKR result changed between publication and verification")
        published_at = clock.now()
        await store.mark_ibkr_historical_requests_published(
            plan_sha256=plan.plan_sha256,
            publications=tuple(
                (result.request_sha256, result.result_sha256) for result in artifact.request_results
            ),
            published_at=published_at,
        )
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "aggregate_sha256": artifact.aggregate.aggregate_sha256,
                "manifest": str(manifest_path),
                "plan_sha256": plan.plan_sha256,
                "published": True,
                "request_count": len(artifact.request_results),
                "verified": True,
            },
            sort_keys=True,
        )
    )


def _verify_ibkr_historical_result(result_path: Path) -> None:
    artifact = verify_ibkr_historical_result(result_path)
    print(
        json.dumps(
            {
                "aggregate_sha256": artifact.aggregate.aggregate_sha256,
                "manifest": str(result_path),
                "plan_sha256": artifact.plan.plan_sha256,
                "request_count": len(artifact.request_results),
                "verified": True,
            },
            sort_keys=True,
        )
    )


_IBKR_IMAGE_REPOSITORY = "qtrad-ibkr"


def _require_ibkr_image_binding(runtime: IbkrAcquisitionRuntime) -> None:
    image_reference = configured_image_digest()
    repository, at_separator, digest = image_reference.rpartition("@")
    algorithm, digest_separator, image_digest = digest.partition(":")
    if (
        repository.rsplit("/", 1)[-1] != _IBKR_IMAGE_REPOSITORY
        or at_separator != "@"
        or algorithm != "sha256"
        or digest_separator != ":"
        or len(image_digest) != 64
        or any(character not in "0123456789abcdef" for character in image_digest)
    ):
        raise ValueError(
            "IBKR canary requires QTRAD_IMAGE_DIGEST to be an immutable "
            "qtrad-ibkr@sha256 image reference"
        )
    expected_digest = f"sha256:{image_digest}"
    if runtime.qtrad_image_digest != expected_digest:
        raise ValueError(
            "configured qtrad-ibkr image digest differs from the locked runtime image digest"
        )


async def _run_ibkr_historical_canary(
    settings: Settings,
    clock: Clock,
    *,
    runtime_lock_path: Path,
    contract_selection_path: Path,
    fx_representative_id: str,
    index_representative_id: str,
    commodity_representative_id: str,
    anchor_end: datetime,
    output_path: Path,
    execute_account_canary: bool,
) -> None:
    """Execute the explicit, bounded Stage 5 canary and publish create-only evidence."""

    if not execute_account_canary:
        raise RuntimeError(
            "IBKR canary execution is account-gated; pass --execute-account-canary "
            "only after Gateway access is authorised"
        )
    with reserve_create_only_output(
        output_path, "IBKR historical canary evidence"
    ) as output_reservation:
        runtime = load_ibkr_runtime_lock(runtime_lock_path)
        _require_ibkr_image_binding(runtime)
        selection = load_ibkr_contract_selection(contract_selection_path)
        if runtime.gateway_version != settings.ibkr_gateway_version:
            raise ValueError("IBKR runtime lock Gateway version differs from current settings")
        if runtime.api_version != settings.ibkr_api_version:
            raise ValueError("IBKR runtime lock API version differs from current settings")
        if runtime.api_host != settings.ibkr_gateway_host:
            raise ValueError("IBKR runtime lock API host differs from current settings")
        if runtime.api_port != settings.ibkr_gateway_port:
            raise ValueError("IBKR runtime lock API port differs from current settings")
        if runtime.client_id_policy != settings.ibkr_client_id_policy:
            raise ValueError("IBKR runtime lock client-ID policy differs from current settings")
        api_package_fingerprint = settings.ibkr_api_package_fingerprint
        if api_package_fingerprint is None:
            raise ValueError(
                "IBKR canary execution requires the verified official API package fingerprint"
            )
        if selection.api_version != settings.ibkr_api_version:
            raise ValueError("IBKR contract selection API version differs from current settings")
        if selection.api_package_fingerprint != api_package_fingerprint:
            raise ValueError("current IBKR API package fingerprint differs from the selection")

        representative_ids = {
            AssetClass.FX: InstrumentId(fx_representative_id),
            AssetClass.INDEX: InstrumentId(index_representative_id),
            AssetClass.COMMODITY: InstrumentId(commodity_representative_id),
        }
        asset_class_by_instrument = ibkr_historical_selection_asset_classes(selection)
        representatives = validate_ibkr_historical_canary_representatives(
            selection, representatives=representative_ids
        )
        cases = build_adjacent_ibkr_canary_cases(representatives, anchor_end=anchor_end)
        pacing_identity = hashlib.sha256(
            json.dumps(
                {
                    "contract": "qtrad-ibkr-historical-canary-v1",
                    "runtime_sha256": runtime.runtime_sha256,
                    "selection_sha256": selection.selection_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        await _require_database_at_migration_head(settings)
        engine = _engine(settings)
        try:
            from qtrad.adapters.ibkr.pacing import IbkrPostgresPacing

            pacing = IbkrPostgresPacing(
                PostgresAuditStore(engine),
                request_profile_sha256=pacing_identity,
                pacing_policy=IbkrHistoricalPacingPolicy(15, 2, 5, 600, 55),
                clock=clock.now,
            )
            adapter = _ibkr_historical_canary_adapter(
                settings, pacing_reserver=pacing.reserve, clock=clock
            )
            evidence = await run_ibkr_historical_canary(
                adapter,
                cases,
                runtime_sha256=runtime.runtime_sha256,
                selection_sha256=selection.selection_sha256,
                clock=clock.now,
            )
            validate_ibkr_historical_canary_selection(
                evidence,
                selection=selection,
                asset_class_by_instrument=asset_class_by_instrument,
            )
            write_ibkr_historical_canary_evidence(
                output_path, evidence, reservation=output_reservation
            )
        finally:
            await engine.dispose()
    print(
        json.dumps(
            {
                "output": str(output_path),
                "evidence_sha256": evidence.evidence_sha256,
                "executed": True,
            },
            sort_keys=True,
        )
    )


def _verify_ibkr_historical_canary(
    evidence_path: Path,
    *,
    expected_runtime_sha256: str | None,
    expected_selection_sha256: str | None,
) -> None:
    for value, field in (
        (expected_runtime_sha256, "expected runtime hash"),
        (expected_selection_sha256, "expected selection hash"),
    ):
        if value is not None:
            _require_sha256_argument(value, field)
    evidence = verify_ibkr_historical_canary_evidence(
        evidence_path,
        expected_runtime_sha256=expected_runtime_sha256,
        expected_selection_sha256=expected_selection_sha256,
    )
    print(
        json.dumps(
            {
                "evidence": str(evidence_path),
                "evidence_sha256": evidence.evidence_sha256,
                "verified": True,
            },
            sort_keys=True,
        )
    )


def _freeze_ibkr_historical_profile(
    evidence_path: Path,
    *,
    output_path: Path,
    canary_evidence_filename: str | None,
    frozen_by: str,
    frozen_at: datetime,
    maximum_in_flight_requests: int,
    request_timeout_seconds: int,
    retry_count: int,
    duplicate_request_protection: str,
    identical_request_cooldown_seconds: int,
    max_requests_per_contract_window: int,
    max_requests_per_rolling_window: int,
) -> None:
    evidence = verify_ibkr_historical_canary_evidence(evidence_path)
    canary_evidence_file_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    profile = freeze_ibkr_request_profile_from_canary(
        evidence,
        canary_evidence_filename=canary_evidence_filename or evidence_path.name,
        canary_evidence_file_sha256=canary_evidence_file_sha256,
        frozen_by=frozen_by,
        frozen_at=frozen_at,
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
    write_ibkr_historical_request_profile(output_path, profile)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "profile_sha256": profile.profile_sha256,
                "canary_evidence_sha256": profile.canary_evidence_sha256,
                "frozen": True,
            },
            sort_keys=True,
        )
    )


def _inspect_ibkr_runtime_lock(
    settings: Settings,
    clock: Clock,
    *,
    gateway_archive: Path,
    api_archive: Path,
    ibc_archive: Path,
    gateway_version: str | None,
    api_version: str | None,
    ibc_version: str,
    image_digest: str | None,
    output_path: Path,
) -> None:
    runtime = build_ibkr_runtime_lock_from_files(
        gateway_archive=gateway_archive,
        api_archive=api_archive,
        ibc_archive=ibc_archive,
        gateway_version=gateway_version or settings.ibkr_gateway_version,
        api_version=api_version or settings.ibkr_api_version,
        ibc_version=ibc_version,
        qtrad_image_digest=configured_image_digest(image_digest),
        frozen_at=clock.now(),
        api_host=settings.ibkr_gateway_host,
        api_port=settings.ibkr_gateway_port,
    )
    write_ibkr_runtime_lock(output_path, runtime)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "runtime_sha256": runtime.runtime_sha256,
                "gateway_version": runtime.gateway_version,
                "api_version": runtime.api_version,
                "ibc_version": runtime.ibc_version,
            },
            sort_keys=True,
        )
    )


async def _review_instruments(
    settings: Settings,
    clock: Clock,
    *,
    catalogue_path: Path | None,
    output_path: Path | None,
    provider: str = "ig",
    environment: str | None = None,
    preflight: bool = False,
    probe_spec_path: Path | None = None,
    execute_account_probe: bool = False,
) -> None:
    if output_path is not None:
        if output_path.exists():
            raise FileExistsError(f"listing review output already exists: {output_path}")
        if not output_path.parent.is_dir():
            raise FileNotFoundError(
                f"listing review output directory does not exist: {output_path.parent}"
            )
    if provider == "ibkr":
        if environment not in (None, "paper"):
            raise ValueError("IBKR listing review supports only the paper environment")
        if preflight:
            if probe_spec_path is not None or execute_account_probe:
                raise ValueError("IBKR preflight cannot accept a probe spec or execute account I/O")
            candidates = load_capture_candidates(
                catalogue_path or Path("config/capture-ibkr-v1-candidates.toml")
            )
            result = build_ibkr_capability_preflight(
                catalogue_name=candidates.name,
                catalogue_hash=candidates.configuration_hash,
                candidate_count=len(candidates.instruments),
                gateway_host=settings.ibkr_gateway_host,
                gateway_port=settings.ibkr_gateway_port,
                client_id=settings.ibkr_client_id,
            )
            _emit_json_artifact(result.as_json_value(), output_path)
            return
        if not execute_account_probe:
            raise RuntimeError(
                "IBKR capability review is account-gated; run --preflight first, then explicitly "
                "pass --execute-account-probe after Gateway access is authorised"
            )
        if output_path is None:
            raise ValueError("IBKR account probe requires --output")
        if probe_spec_path is None:
            raise ValueError("IBKR account probe requires --probe-spec")
        candidates = load_capture_candidates(
            catalogue_path or Path("config/capture-ibkr-v1-candidates.toml")
        )
        probe_spec = load_ibkr_capability_probe_spec(probe_spec_path)
        candidate_ids = {instrument.instrument_id for instrument in candidates.instruments}
        query_ids = {query.instrument_id for query in probe_spec.queries}
        if candidate_ids != query_ids:
            raise ValueError("IBKR capability probe spec must cover each candidate exactly")
        from qtrad.adapters.ibkr.checkpoint import (
            IbkrCapabilityCheckpointIdentity,
            JsonIbkrCapabilityCheckpoint,
        )

        checkpoint_configuration = hashlib.sha256(
            json.dumps(
                {
                    "gateway_host": settings.ibkr_gateway_host,
                    "gateway_port": settings.ibkr_gateway_port,
                    "client_id": settings.ibkr_client_id,
                    "api_fingerprint": settings.ibkr_api_package_fingerprint,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if not settings.ibkr_checkpoint_root.is_absolute():
            raise ValueError("IBKR capability checkpoints require an absolute persistent path")
        settings.ibkr_checkpoint_root.mkdir(parents=True, exist_ok=True)
        checkpoint_path = settings.ibkr_checkpoint_root / f"{probe_spec.configuration_hash}.json"
        checkpoint = JsonIbkrCapabilityCheckpoint(
            checkpoint_path,
            IbkrCapabilityCheckpointIdentity(
                catalogue_hash=candidates.configuration_hash,
                probe_spec_hash=probe_spec.configuration_hash,
                api_version=settings.ibkr_api_version,
                gateway_version=settings.ibkr_gateway_version,
                configuration_hash=checkpoint_configuration,
            ),
        )
        from qtrad.adapters.ibkr.pacing import IbkrPostgresPacing
        from qtrad.domain.ibkr_historical import IbkrHistoricalPacingPolicy

        engine = _engine(settings)
        adapter = None
        try:
            pacing = IbkrPostgresPacing(
                PostgresAuditStore(engine),
                request_profile_sha256=checkpoint_configuration,
                pacing_policy=IbkrHistoricalPacingPolicy(15, 2, 5, 600, 55),
            )
            adapter = _ibkr_capability_adapter(
                settings,
                checkpoint=checkpoint,
                pacing_reserver=pacing.reserve,
            )
            await adapter.connect()
            results = await adapter.probe(probe_spec.queries)
            review = build_ibkr_capability_review(
                catalogue_name=candidates.name,
                catalogue_hash=candidates.configuration_hash,
                instruments=candidates.instruments,
                probe_spec_name=probe_spec.name,
                probe_spec_hash=probe_spec.configuration_hash,
                api_version=settings.ibkr_api_version,
                api_package_fingerprint=settings.ibkr_api_package_fingerprint or "",
                results=results,
                observed_at=clock.now(),
            )
            _emit_json_artifact(review.as_json_value(), output_path, review.review_hash)
        finally:
            if adapter is not None:
                await adapter.disconnect()
            await engine.dispose()
        return
    if provider != "ig":
        raise ValueError(f"unsupported listing-review provider: {provider}")
    if environment not in (None, "demo"):
        raise ValueError("IG listing review supports only the demo environment")
    if preflight:
        raise ValueError("--preflight is only valid for the IBKR provider")
    if probe_spec_path is not None or execute_account_probe:
        raise ValueError("IBKR account-probe options are only valid for the IBKR provider")

    candidates = load_capture_candidates(
        catalogue_path or Path("config/capture-v2-candidates.toml")
    )
    adapter = _ig_review_adapter(settings, clock, candidates)
    try:
        await adapter.connect()
        reviews = await adapter.review_listings(
            [instrument.instrument_id for instrument in candidates.instruments],
            exact_epics=candidates.exact_review_epics,
        )
        manifest = build_listing_review_manifest(
            catalogue_name=candidates.name,
            catalogue_hash=candidates.configuration_hash,
            instruments=candidates.instruments,
            reviews=reviews,
            observed_at=clock.now(),
        )
        _emit_json_artifact(manifest.as_json_value(), output_path, manifest.review_hash)
    finally:
        await adapter.disconnect()


def _emit_json_artifact(
    payload: dict[str, JsonValue], output_path: Path | None, artifact_hash: str | None = None
) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if output_path is None:
        print(encoded, end="")
        return
    with output_path.open("x", encoding="utf-8") as output:
        output.write(encoded)
    response: dict[str, JsonValue] = {"output": str(output_path)}
    if artifact_hash is not None:
        response["review_hash"] = artifact_hash
    else:
        response["preflight_hash"] = payload["preflight_hash"]
    print(json.dumps(response, sort_keys=True))


def _promote_universe(
    clock: Clock,
    *,
    catalogue_path: Path,
    review_path: Path,
    selections_path: Path,
    release_name: str,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise FileExistsError(f"capture universe output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"capture universe output directory does not exist: {output_path.parent}"
        )
    candidates = load_capture_candidates(catalogue_path)
    evidence = load_listing_review_evidence(review_path, candidates.instruments)
    selection_set = load_explicit_selection_set(selections_path)
    promotion = promote_reviewed_universe(
        release_name=release_name,
        catalogue_name=candidates.name,
        catalogue_hash=candidates.configuration_hash,
        instruments=candidates.instruments,
        review_catalogue_name=evidence.catalogue_name,
        review_catalogue_hash=evidence.catalogue_hash,
        review_hash=evidence.review_hash,
        reviews=evidence.reviews,
        selection_set=selection_set,
        promoted_at=clock.now(),
    )
    rendered, universe = render_capture_universe_promotion(promotion)
    with output_path.open("x", encoding="utf-8") as output:
        output.write(rendered)
    print(
        json.dumps(
            {
                "configuration_hash": universe.configuration_hash,
                "output": str(output_path),
                "selection_hash": promotion.selection_hash,
                "source_review_hash": promotion.source_review_hash,
            },
            sort_keys=True,
        )
    )


def _verify_capture_feed_pages(
    *,
    source_id: str,
    universe_name: str,
    configuration_hash: str,
    after_position: int,
    page_paths: Sequence[Path],
) -> None:
    identity = CaptureFeedIdentity(
        feed_schema_version=1,
        source_id=source_id,
        universe_name=universe_name,
        configuration_hash=configuration_hash,
    )
    cursor = CaptureFeedCursor.initial(identity, after_position=after_position)
    event_count = 0
    for page_path in page_paths:
        page = load_capture_feed_page(page_path)
        cursor = advance_capture_feed_cursor(cursor, page)
        event_count += len(page.events)
    print(
        json.dumps(
            {
                "caught_up": cursor.position == cursor.observed_high_water_position,
                "event_count": event_count,
                "page_count": len(page_paths),
                "position": cursor.position,
                "source_id": cursor.identity.source_id,
                "universe_name": cursor.identity.universe_name,
                "configuration_hash": cursor.identity.configuration_hash,
                "observed_high_water_position": cursor.observed_high_water_position,
            },
            sort_keys=True,
        )
    )


async def _probe_capture_feed(
    *,
    endpoint: str,
    source_id: str,
    universe_name: str,
    configuration_hash: str,
    after_position: int,
    limit: int,
) -> None:
    identity = CaptureFeedIdentity(
        feed_schema_version=1,
        source_id=source_id,
        universe_name=universe_name,
        configuration_hash=configuration_hash,
    )
    cursor = CaptureFeedCursor.initial(identity, after_position=after_position)
    async with HttpCaptureFeedClient(endpoint) as client:
        page = await client.fetch_page(after_position=after_position, limit=limit)
    candidate_cursor = advance_capture_feed_cursor(cursor, page)
    print(
        json.dumps(
            {
                "caught_up": candidate_cursor.position
                == candidate_cursor.observed_high_water_position,
                "event_count": len(page.events),
                "next_position": candidate_cursor.position,
                "observed_high_water_position": candidate_cursor.observed_high_water_position,
                "source_id": candidate_cursor.identity.source_id,
                "universe_name": candidate_cursor.identity.universe_name,
                "configuration_hash": candidate_cursor.identity.configuration_hash,
                "cursor_persisted": False,
            },
            sort_keys=True,
        )
    )


async def _synchronise_capture_universe(
    store: PostgresAuditStore,
    adapter: IgDemoMarketDataAdapter,
    universe: CaptureUniverse,
    clock: Clock,
) -> tuple[ProviderListing, ...]:
    """Validate an approved release through the collector's existing IG session."""

    instrument_ids = tuple(instrument.instrument_id for instrument in universe.instruments)
    await store.seed_instruments(universe.instruments)
    active = await store.active_provider_listings(instrument_ids)
    by_instrument = {listing.instrument_id: listing for listing in active}
    if len(by_instrument) != len(active):
        raise RuntimeError("multiple active provider listings exist for a capture instrument")
    needs_sync = tuple(
        instrument_id
        for instrument_id in instrument_ids
        if instrument_id not in by_instrument
        or by_instrument[instrument_id].listing_id.external_id
        != universe.preferred_epics[instrument_id]
    )
    if needs_sync:
        discovered = await adapter.discover_capture_universe(
            needs_sync,
            instruments_by_id=universe.instruments_by_id,
            preferred_epics=universe.preferred_epics,
        )
        if len(discovered) != len(needs_sync):
            raise RuntimeError("IG discovery did not return every approved capture instrument")
        for listing in discovered:
            await store.validate_provider_listing(
                listing,
                universe_hash=universe.configuration_hash,
                observed_at=clock.now(),
            )
        active = await store.active_provider_listings(instrument_ids)
        by_instrument = {listing.instrument_id: listing for listing in active}
    if len(active) != len(instrument_ids) or len(by_instrument) != len(instrument_ids):
        raise RuntimeError("capture universe listing activation is incomplete")
    for instrument_id in instrument_ids:
        listing = by_instrument[instrument_id]
        if listing.listing_id.external_id != universe.preferred_epics[instrument_id]:
            raise RuntimeError(
                f"active provider listing does not match release for {instrument_id}"
            )
    return tuple(by_instrument[instrument_id] for instrument_id in instrument_ids)


async def _ingest(
    settings: Settings,
    clock: Clock,
    *,
    maximum_seconds: float | None = None,
    force_reconnect_after_seconds: float | None = None,
) -> None:
    if maximum_seconds is not None and maximum_seconds <= 0:
        raise ValueError("maximum seconds must be positive")
    if force_reconnect_after_seconds is not None:
        if force_reconnect_after_seconds <= 0:
            raise ValueError("forced reconnect interval must be positive")
        if maximum_seconds is not None and force_reconnect_after_seconds >= maximum_seconds:
            raise ValueError("forced reconnect must occur before maximum seconds")
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    universe = _capture_universe(settings)
    adapter = _ig_adapter(settings, clock)
    service = IngestionService(store, producer="ig-demo-adapter", producer_version="0.1.0")
    run_id: RunId | None = None
    terminal_status = "FAILED"
    reconnect_task: asyncio.Task[None] | None = None
    reconnect_error: Exception | None = None
    disconnect_error: Exception | None = None
    forced_reconnect_completed = False
    bounded_deadline_reached = False
    reload_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal_installed = False
    try:
        await adapter.connect()
        listings = await _synchronise_capture_universe(store, adapter, universe, clock)
        await adapter.subscribe(listings)
        initial_health = await adapter.health()
        await store.record_adapter_health(initial_health)
        run_id = await store.start_run(
            kind=RunKind.INGESTION,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=universe.configuration_hash,
            started_at=clock.now(),
        )
        try:
            loop.add_signal_handler(signal.SIGHUP, reload_event.set)
            signal_installed = True
        except (NotImplementedError, RuntimeError):
            LOGGER.warning("capture_universe_reload_signal_unavailable")

        async def force_reconnect() -> None:
            assert force_reconnect_after_seconds is not None
            await asyncio.sleep(force_reconnect_after_seconds)
            await adapter.force_reconnect()
            await store.record_adapter_health(await adapter.health())

        if force_reconnect_after_seconds is not None:
            reconnect_task = asyncio.create_task(force_reconnect())

        async def consume() -> None:
            async for record in adapter.records():
                await service.process(record)
                await service.advance_bars(record.received_time)

        async def persist_health() -> None:
            while True:
                await asyncio.sleep(_HEALTH_PERSIST_INTERVAL_SECONDS)
                await store.record_adapter_health(await adapter.health())

        async def reload_universe() -> None:
            nonlocal run_id, universe
            while True:
                await reload_event.wait()
                reload_event.clear()
                candidate_run_id: RunId | None = None
                try:
                    candidate = _capture_universe(settings)
                    if candidate.configuration_hash == universe.configuration_hash:
                        LOGGER.info(
                            "capture_universe_reload_unchanged",
                            extra={"configuration_hash": universe.configuration_hash},
                        )
                        continue
                    candidate_listings = await _synchronise_capture_universe(
                        store, adapter, candidate, clock
                    )
                    candidate_run_id = await store.start_run(
                        kind=RunKind.INGESTION,
                        environment=BrokerEnvironment.IG_DEMO,
                        configuration_hash=candidate.configuration_hash,
                        started_at=clock.now(),
                    )
                except Exception:
                    LOGGER.exception("capture_universe_reload_rejected")
                    continue
                try:
                    await adapter.replace_subscriptions(
                        candidate_listings,
                        instruments_by_id=candidate.instruments_by_id,
                        preferred_epics=candidate.preferred_epics,
                    )
                except Exception:
                    await store.finish_run(
                        candidate_run_id,
                        status="FAILED",
                        finished_at=clock.now(),
                        detail={"reason": "capture universe reload rejected"},
                    )
                    LOGGER.exception("capture_universe_reload_rejected")
                    continue
                transition_time = clock.now()
                if run_id is None:
                    raise RuntimeError("active ingestion run is unavailable during reload")
                await store.finish_run(
                    run_id,
                    status="STOPPED",
                    finished_at=transition_time,
                    detail={
                        "reason": "capture universe replaced",
                        "replacement_configuration_hash": candidate.configuration_hash,
                    },
                )
                run_id = candidate_run_id
                universe = candidate
                await store.record_adapter_health(await adapter.health())
                LOGGER.info(
                    "capture_universe_reloaded",
                    extra={
                        "configuration_hash": candidate.configuration_hash,
                        "instrument_count": len(candidate.instruments),
                    },
                )

        async def consume_with_health_supervision() -> None:
            tasks = (
                asyncio.create_task(consume()),
                asyncio.create_task(persist_health()),
                asyncio.create_task(reload_universe()),
            )
            done: set[asyncio.Task[None]] = set()
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                pending = tuple(task for task in tasks if not task.done())
                for task in pending:
                    task.cancel()
                for task in pending:
                    with suppress(asyncio.CancelledError):
                        await task
            for task in done:
                task.result()

        if maximum_seconds is None:
            await consume_with_health_supervision()
            raise RuntimeError("unbounded IG ingestion iterator ended unexpectedly")
        else:
            try:
                async with asyncio.timeout(maximum_seconds):
                    await consume_with_health_supervision()
            except TimeoutError:
                bounded_deadline_reached = True
                terminal_status = "STOPPED"
            else:
                raise RuntimeError("bounded IG ingestion iterator ended before its timeout")
    except (KeyboardInterrupt, asyncio.CancelledError):
        terminal_status = "STOPPED"
    finally:
        if signal_installed:
            loop.remove_signal_handler(signal.SIGHUP)
        if reconnect_task is not None:
            if reconnect_task.done():
                try:
                    reconnect_task.result()
                except Exception as error:
                    reconnect_error = error
                    terminal_status = "FAILED"
                else:
                    forced_reconnect_completed = True
            else:
                if bounded_deadline_reached:
                    reconnect_error = RuntimeError(
                        "forced reconnect did not complete before the ingestion deadline"
                    )
                    terminal_status = "FAILED"
                reconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reconnect_task
        try:
            await adapter.disconnect()
        except Exception as error:
            disconnect_error = error
            terminal_status = "FAILED"
        final_health = await adapter.health()
        await store.record_adapter_health(final_health)
        if run_id is not None:
            await store.finish_run(
                run_id,
                status=terminal_status,
                finished_at=clock.now(),
                detail={
                    "adapter_health": final_health.detail,
                    "forced_reconnect_requested": force_reconnect_after_seconds is not None,
                    "forced_reconnect_completed": forced_reconnect_completed,
                },
            )
        await engine.dispose()
        if reconnect_error is not None:
            raise reconnect_error
        if disconnect_error is not None:
            raise disconnect_error


async def _plan_backfill(
    settings: Settings,
    clock: Clock,
    *,
    universe_path: Path,
    start: datetime,
    end: datetime,
    remaining_allowance: int,
    output_path: Path,
    instrument_ids: Sequence[InstrumentId],
) -> None:
    if output_path.exists():
        raise FileExistsError(f"backfill plan output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"backfill plan output directory does not exist: {output_path.parent}"
        )
    universe = load_capture_universe(universe_path)
    available = set(universe.instruments_by_id)
    unknown = sorted(str(item) for item in set(instrument_ids) - available)
    if unknown:
        raise ValueError(f"backfill instruments are not in the selected universe: {unknown}")
    engine = _engine(settings)
    try:
        store = PostgresAuditStore(engine)
        listings = await store.active_provider_listings(instrument_ids)
        observed_at = clock.now()
        plan = build_backfill_plan(
            universe_name=universe.name,
            universe_hash=universe.configuration_hash,
            instrument_ids=instrument_ids,
            listings=listings,
            preferred_epics=universe.preferred_epics,
            start=start,
            end=end,
            remaining_allowance=remaining_allowance,
            quota_observed_at=observed_at,
            created_at=observed_at,
        )
        write_backfill_plan(output_path, plan)
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "plan_hash": plan.plan_hash,
                "instrument_count": len(plan.items),
                "points_per_instrument": plan.points_per_instrument,
                "requested_points": plan.requested_points,
                "selection_authority": False,
                "registered": False,
            },
            sort_keys=True,
        )
    )


async def _register_backfill(
    settings: Settings,
    *,
    plan_path: Path,
    confirmed_plan_hash: str,
) -> None:
    plan = load_backfill_plan(plan_path)
    if confirmed_plan_hash != plan.plan_hash:
        raise ValueError("confirmed backfill plan hash does not match the reviewed plan")
    engine = _engine(settings)
    try:
        status = await PostgresAuditStore(engine).register_backfill_plan(
            plan,
            backfill_plan_payload(plan),
        )
    finally:
        await engine.dispose()
    print(json.dumps({"plan_hash": plan.plan_hash, "status": status}, sort_keys=True))


async def _execute_backfill(settings: Settings, clock: Clock, *, plan_hash: str) -> None:
    _require_sha256_argument(plan_hash, "backfill plan hash")
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    adapter: IgDemoMarketDataAdapter | None = None
    plan: BackfillPlan | None = None
    plan_claimed = False
    run_id: RunId | None = None
    plan_completed = False
    terminal_status = "FAILED"
    written = 0
    received: dict[tuple[InstrumentId, PriceBasis], set[datetime]] = {}
    try:
        payload = await store.claim_backfill_plan(plan_hash)
        plan_claimed = True
        plan = decode_backfill_plan(json.dumps(payload, sort_keys=True))
        if plan.plan_hash != plan_hash:
            raise RuntimeError("claimed backfill plan content does not match the requested hash")
        adapter = _ig_backfill_adapter(settings, clock)
        run_id = await store.start_run(
            kind=RunKind.BACKFILL,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=plan.universe_hash,
            started_at=clock.now(),
        )
        await store.record_quota_state(
            provider="ig",
            environment="demo",
            allowance_name=plan.quota.allowance_name,
            remaining=plan.quota.remaining_points,
            observed_at=plan.quota.observed_at,
        )
        listings = tuple([await store.provider_listing_version(item) for item in plan.items])
        try:
            await adapter.connect()
            for request in backfill_requests(plan, listings):
                request_id = uuid4()
                await store.start_historical_request_usage(
                    request_id=request_id,
                    run_id=run_id,
                    plan_hash=plan.plan_hash,
                    instrument_id=request.instrument_id,
                    listing_id=request.listing.listing_id,
                    interval_start=request.start,
                    interval_end=request.end,
                    requested_points=request.maximum_points,
                    started_at=clock.now(),
                )
                returned_points: set[datetime] = set()
                async for bar in adapter.backfill(request):
                    _validate_planned_bar(plan, request, bar)
                    returned_points.add(bar.interval_start)
                    received.setdefault((bar.instrument_id, bar.basis), set()).add(
                        bar.interval_start
                    )
                    event = await _append_bar(store, bar, received_time=clock.now())
                    if event is not None:
                        written += 1
                await store.complete_historical_request_usage(
                    request_id,
                    returned_points=len(returned_points),
                    provider_remaining=adapter.historical_allowance_remaining,
                    completed_at=clock.now(),
                )
        finally:
            await adapter.disconnect()
        observed_points = {
            (item.instrument_id, basis): len(received.get((item.instrument_id, basis), set()))
            for item in plan.items
            for basis in (PriceBasis.BID, PriceBasis.ASK, PriceBasis.MID)
        }
        if any(points <= 0 for points in observed_points.values()):
            raise RuntimeError("planned historical range returned no data for a required basis")
        provider_remaining = adapter.historical_allowance_remaining
        if provider_remaining is not None:
            await store.record_quota_state(
                provider="ig",
                environment="demo",
                allowance_name="historical_points_weekly_provider_reported",
                remaining=provider_remaining,
                observed_at=clock.now(),
            )
        await store.complete_backfill_plan(
            plan,
            observed_points=observed_points,
            executed_at=clock.now(),
        )
        plan_completed = True
        terminal_status = "COMPLETED"
        print(
            json.dumps(
                {
                    "plan_hash": plan.plan_hash,
                    "points_received": sum(len(points) for points in received.values()),
                    "bars_written": written,
                    "provider_remaining_allowance": provider_remaining,
                },
                sort_keys=True,
            )
        )
    except BaseException:
        if plan_claimed and not plan_completed:
            await store.fail_backfill_plan(plan_hash, executed_at=clock.now())
        raise
    finally:
        if run_id is not None and plan is not None:
            await store.finish_run(
                run_id,
                status=terminal_status,
                finished_at=clock.now(),
                detail={
                    "plan_hash": plan.plan_hash,
                    "bars_written": written,
                    "points_received": sum(len(points) for points in received.values()),
                    "provider_remaining_allowance": (
                        adapter.historical_allowance_remaining if adapter is not None else None
                    ),
                },
            )
        await engine.dispose()


def _validate_planned_bar(plan: BackfillPlan, request: BackfillRequest, bar: MarketBar) -> None:
    if bar.provenance is not BarProvenance.IG_HISTORICAL:
        raise RuntimeError("provider returned a non-historical bar for a backfill plan")
    if bar.instrument_id != request.instrument_id:
        raise RuntimeError("provider returned a historical bar for another instrument")
    if bar.source_listing_id != request.listing.listing_id:
        raise RuntimeError("provider returned a historical bar for another listing")
    if not request.start <= bar.interval_start < request.end:
        raise RuntimeError("provider returned a historical bar outside the planned request")
    if (bar.interval_end - bar.interval_start).total_seconds() != 60:
        raise RuntimeError("provider returned a historical bar at an unexpected resolution")
    if request.resolution is not plan.resolution:
        raise RuntimeError("provider request resolution differs from its backfill plan")


async def _append_bar(
    store: PostgresAuditStore, bar: MarketBar, *, received_time: datetime
) -> EventEnvelope | None:
    source = bar.source_listing_id
    stream_id = (
        f"historical-bar:{bar.instrument_id}:{bar.basis}:"
        f"{source.provider}:{source.environment}:{source.external_id}:"
        f"{bar.interval_start.isoformat()}"
    )
    previous = await store.latest_stream_version(stream_id)
    if previous:
        rows = await store.query(
            """
            SELECT payload FROM canonical.events
            WHERE stream_id = :stream_id AND stream_version = :stream_version
            """,
            {"stream_id": stream_id, "stream_version": previous},
        )
        payload = to_json_value(bar)
        if len(rows) != 1:
            raise RuntimeError(f"latest historical bar event is missing for {stream_id}")
        existing_payload = rows[0]["payload"]
        if not isinstance(payload, dict) or not isinstance(existing_payload, dict):
            raise RuntimeError(f"historical bar payload is malformed for {stream_id}")
        comparable_payload = {key: value for key, value in payload.items() if key != "revision"}
        comparable_existing = {
            key: value for key, value in existing_payload.items() if key != "revision"
        }
        if comparable_existing == comparable_payload:
            return None
        bar = replace(bar, revision=previous + 1)
    event = EventEnvelope.create(
        stream_id=stream_id,
        stream_version=previous + 1,
        event_type="MarketBarClosed" if previous == 0 else "MarketBarCorrected",
        event_time=bar.interval_end,
        received_time=received_time,
        producer="ig-demo-backfill",
        producer_version="0.1.0",
        payload=bar,
    )
    try:
        return await store.append(event, expected_stream_version=previous)
    except StreamVersionConflict:
        return await _append_bar(store, bar, received_time=received_time)


async def _storage_snapshot(
    settings: Settings,
    *,
    universe_path: Path,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise FileExistsError(f"storage snapshot output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"storage snapshot output directory does not exist: {output_path.parent}"
        )
    universe = load_capture_universe(universe_path)
    engine = _engine(settings)
    try:
        measurement = await PostgresStorageInspector(engine).measure()
        snapshot = build_storage_snapshot(
            measurement,
            capture_source_id=settings.capture_source_id,
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            application_version=__version__,
            application_image=settings.image,
        )
        write_storage_snapshot(output_path, snapshot)
        print(
            json.dumps(
                {
                    "snapshot_sha256": snapshot.snapshot_sha256,
                    "observed_at": snapshot.observed_at.isoformat(),
                    "raw_message_count": snapshot.raw_message_count,
                    "canonical_event_count": snapshot.canonical_event_count,
                    "database_bytes": snapshot.database_bytes,
                    "output": str(output_path),
                },
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


def _compare_storage_snapshots(before_path: Path, after_path: Path, output_path: Path) -> None:
    artifact = build_storage_comparison_artifact(
        load_storage_snapshot(before_path),
        load_storage_snapshot(after_path),
    )
    write_storage_evidence_artifact(output_path, artifact)
    print(
        json.dumps(
            {
                "artifact_kind": artifact.artifact_kind,
                "artifact_sha256": artifact.artifact_sha256,
                "output": str(output_path),
            },
            sort_keys=True,
        )
    )


def _contrast_storage_comparisons(
    baseline_path: Path,
    candidate_path: Path,
    output_path: Path,
) -> None:
    artifact = build_storage_contrast_artifact(
        load_storage_evidence_artifact(baseline_path),
        load_storage_evidence_artifact(candidate_path),
    )
    write_storage_evidence_artifact(output_path, artifact)
    print(
        json.dumps(
            {
                "artifact_kind": artifact.artifact_kind,
                "artifact_sha256": artifact.artifact_sha256,
                "output": str(output_path),
            },
            sort_keys=True,
        )
    )


def _record_storage_active_market_review(
    comparison_path: Path,
    review_path: Path,
    output_path: Path,
) -> None:
    artifact = build_storage_active_market_review_artifact(
        load_storage_evidence_artifact(comparison_path),
        load_storage_active_market_review_input(review_path),
    )
    write_storage_evidence_artifact(output_path, artifact)
    print(
        json.dumps(
            {
                "artifact_kind": artifact.artifact_kind,
                "artifact_sha256": artifact.artifact_sha256,
                "active_market_representative": artifact.payload["active_market_representative"],
                "output": str(output_path),
            },
            sort_keys=True,
        )
    )


def _qualify_storage_contrast(
    contrast_path: Path,
    baseline_review_path: Path,
    candidate_review_path: Path,
    output_path: Path,
) -> None:
    artifact = build_storage_contrast_qualification_artifact(
        load_storage_evidence_artifact(contrast_path),
        load_storage_evidence_artifact(baseline_review_path),
        load_storage_evidence_artifact(candidate_review_path),
    )
    write_storage_evidence_artifact(output_path, artifact)
    print(
        json.dumps(
            {
                "artifact_kind": artifact.artifact_kind,
                "artifact_sha256": artifact.artifact_sha256,
                "qualification_status": artifact.payload["qualification_status"],
                "storage_decision_accepted": False,
                "output": str(output_path),
            },
            sort_keys=True,
        )
    )


async def _export(
    settings: Settings,
    clock: Clock,
    *,
    universe_path: Path,
    start: datetime,
    end: datetime,
    snapshot_import_path: Path | None = None,
) -> None:
    if end <= start:
        raise ValueError("research export end must follow start")
    universe = load_capture_universe(universe_path)
    snapshot_metadata: dict[str, JsonValue] = {
        "kind": "unbound-database",
        "capture_source_id": settings.capture_source_id,
    }
    if snapshot_import_path is not None:
        snapshot_import = load_research_snapshot_import(snapshot_import_path)
        database_name = make_url(settings.database_url).database
        if database_name != snapshot_import.target_database:
            raise ValueError("research snapshot evidence does not identify the configured database")
        if settings.capture_source_id != snapshot_import.capture_source_id:
            raise ValueError("research snapshot evidence does not identify the configured source")
        if universe.configuration_hash != snapshot_import.universe_hash:
            raise ValueError("research snapshot evidence does not identify the selected universe")
        if snapshot_import.universe_name not in {"unknown-v1", universe.name}:
            raise ValueError("research snapshot evidence has a different universe name")
        snapshot_metadata = research_snapshot_metadata(snapshot_import)
    engine = _engine(settings)
    store = PostgresAuditStore(engine)
    research = ParquetResearchStore(settings.research_root, clock)
    instrument_ids = tuple(str(instrument.instrument_id) for instrument in universe.instruments)
    encoded_instruments = json.dumps(instrument_ids)
    run_id = await store.start_run(
        kind=RunKind.EXPORT,
        environment=BrokerEnvironment.NONE,
        configuration_hash=universe.configuration_hash,
        started_at=clock.now(),
    )
    terminal_status = "FAILED"
    try:
        rows = await store.query(
            """
                SELECT DISTINCT ON (
                    instrument_id, basis, interval_start, provenance,
                    source_provider, source_environment, source_external_id
                )
                    * FROM read_model.market_bars
                  WHERE instrument_id IN (
                      SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
                  )
                    AND interval_start >= :interval_start
                    AND interval_end <= :interval_end
                ORDER BY instrument_id, basis, interval_start, provenance,
                         source_provider, source_environment, source_external_id,
                         revision DESC
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
        bars = tuple(_bar_from_projection(row) for row in rows)
        live_gaps = await store.query(
            """
            SELECT gap_id, instrument_id, interval_start, interval_end, reason,
                   detected_at, repaired_at
            FROM read_model.data_gaps
              WHERE instrument_id IN (
                  SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
              )
                AND interval_start < :interval_end
                AND interval_end > :interval_start
            ORDER BY instrument_id, interval_start, gap_id
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
        historical_coverage = await store.query(
            """
            SELECT instrument_id, source_provider, source_environment, source_external_id,
                   source_listing_valid_from, source_listing_metadata_version,
                   provenance, basis, resolution, interval_start, interval_end,
                     detected_at, detected_by_plan_hash, request_completed_at,
                     returned_points, covered_at, covered_by_plan_hash, observed_points
            FROM read_model.historical_coverage_gaps
              WHERE instrument_id IN (
                  SELECT jsonb_array_elements_text(CAST(:instrument_ids AS jsonb))
              )
                AND interval_start < :interval_end
                AND interval_end > :interval_start
            ORDER BY instrument_id, interval_start, basis, detected_by_plan_hash
            """,
            {
                "instrument_ids": encoded_instruments,
                "interval_start": start,
                "interval_end": end,
            },
        )
        metadata = research_export_metadata(
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            instrument_ids=tuple(instrument.instrument_id for instrument in universe.instruments),
            interval_start=start,
            interval_end=end,
            bars=bars,
            live_gaps=live_gaps,
            historical_coverage=historical_coverage,
            application_version=__version__,
            application_image=settings.image,
        )
        metadata["source_snapshot"] = snapshot_metadata
        manifest = await research.write_bars(
            bars,
            universe_name=universe.name,
            configuration_hash=universe.configuration_hash,
            metadata=metadata,
        )
        await store.record_manifest(manifest)
        terminal_status = "COMPLETED"
        print(
            json.dumps(
                {
                    "manifest_id": manifest.manifest_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "universe_name": manifest.universe_name,
                    "configuration_hash": manifest.configuration_hash,
                    "rows": manifest.row_count,
                },
                sort_keys=True,
            )
        )
    finally:
        await store.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={
                "universe_name": universe.name,
                "interval_start": start.isoformat(),
                "interval_end": end.isoformat(),
            },
        )
        await engine.dispose()


async def _rank_research(
    settings: Settings,
    clock: Clock,
    *,
    manifest_path: Path,
    experiment_path: Path,
    snapshot_import_path: Path,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise FileExistsError(f"strategy report output already exists: {output_path}")
    if manifest_path.parent.name != "manifests" or manifest_path.suffix != ".json":
        raise ValueError("strategy report manifest must be JSON inside a manifests directory")
    experiment = load_strategy_experiment(experiment_path)
    snapshot_import = load_research_snapshot_import(snapshot_import_path)
    database_name = make_url(settings.database_url).database
    if database_name != snapshot_import.target_database:
        raise ValueError("strategy report requires the verified snapshot target database")
    if settings.capture_source_id != snapshot_import.capture_source_id:
        raise ValueError("strategy report snapshot has a different capture source")
    store = ParquetResearchStore(manifest_path.parent.parent, clock)
    manifest = await store.read_manifest(manifest_path.stem)
    if manifest.configuration_hash != snapshot_import.universe_hash:
        raise ValueError("strategy report manifest and snapshot universe differ")
    source_snapshot = manifest.metadata["source_snapshot"]
    if not isinstance(source_snapshot, dict):
        raise TypeError("strategy report manifest source_snapshot must be an object")
    if source_snapshot["import_sha256"] != snapshot_import.import_sha256:
        raise ValueError("strategy report manifest does not bind the verified snapshot import")
    bars = tuple(await store.read_bars(manifest.manifest_id))

    engine = _engine(settings)
    audit = PostgresAuditStore(engine)
    try:
        provider_rows = await audit.query(
            """
            SELECT metadata_version, currency, minimum_deal_size, economics
            FROM reference.provider_listings
            WHERE instrument_id = :instrument_id
              AND valid_from <= :decision_start
              AND (valid_to IS NULL OR valid_to > :decision_start)
            ORDER BY valid_from DESC
            """,
            {
                "instrument_id": str(experiment.instrument_id),
                "decision_start": experiment.decision_start,
            },
        )
        if len(provider_rows) != 1:
            raise ValueError("strategy report requires one effective provider economics row")
        quote_rows = await audit.query(
            """
            SELECT global_position, event_time, received_time, payload
            FROM canonical.events
            WHERE event_type = 'MarketQuoteObserved'
              AND payload->>'instrument_id' = :instrument_id
              AND received_time >= :quote_start
              AND received_time <= :quote_end
            ORDER BY global_position
            """,
            {
                "instrument_id": str(experiment.instrument_id),
                "quote_start": experiment.decision_start,
                "quote_end": experiment.query_end + timedelta(minutes=1),
            },
        )
        first = build_strategy_experiment_report(
            experiment=experiment,
            manifest=manifest,
            bars=bars,
            quote_rows=quote_rows,
            provider_row=provider_rows[0],
        )
        second = build_strategy_experiment_report(
            experiment=experiment,
            manifest=manifest,
            bars=tuple(reversed(bars)),
            quote_rows=quote_rows,
            provider_row=provider_rows[0],
        )
        if first != second:
            raise RuntimeError("strategy report replay differs")
        write_strategy_experiment_report(output_path, first)
        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "report_sha256": first.report_sha256,
                    "dataset_sha256": first.payload["dataset_sha256"],
                    "ranking": first.payload["ranking"],
                    "profitability_claim": False,
                },
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


async def _replay(settings: Settings, clock: Clock, manifest_path: Path) -> None:
    if manifest_path.parent.name != "manifests" or manifest_path.suffix != ".json":
        raise ValueError("replay manifest must be a JSON file inside a manifests directory")
    manifest_id = manifest_path.stem
    root = manifest_path.parent.parent
    store = ParquetResearchStore(root, clock)
    manifest = await store.read_manifest(manifest_id)
    engine = _engine(settings)
    audit = PostgresAuditStore(engine)
    run_id = await audit.start_run(
        kind=RunKind.REPLAY,
        environment=BrokerEnvironment.NONE,
        configuration_hash=manifest.configuration_hash,
        started_at=clock.now(),
    )
    terminal_status = "FAILED"
    try:
        first = tuple(await store.read_bars(manifest_id))
        second = tuple(await store.read_bars(manifest_id))
        first_hash = semantic_bar_hash(first)
        second_hash = semantic_bar_hash(second)
        if first_hash != second_hash:
            raise RuntimeError("replay hashes differ")
        terminal_status = "COMPLETED"
        print(
            json.dumps(
                {
                    "manifest_id": manifest_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "rows": len(first),
                    "sha256": first_hash,
                },
                sort_keys=True,
            )
        )
    finally:
        await audit.finish_run(
            run_id,
            status=terminal_status,
            finished_at=clock.now(),
            detail={
                "manifest_id": manifest_id,
                "manifest_sha256": manifest.manifest_sha256,
                "universe_name": manifest.universe_name,
            },
        )
        await engine.dispose()


async def _rebuild(settings: Settings) -> None:
    engine = _engine(settings)
    try:
        count = await PostgresAuditStore(engine).rebuild_projections()
        print(json.dumps({"events_projected": count}))
    finally:
        await engine.dispose()


def _bar_from_projection(row: dict[str, object]) -> MarketBar:
    return MarketBar(
        instrument_id=InstrumentId(str(row["instrument_id"])),
        basis=PriceBasis(str(row["basis"])),
        interval_start=_as_utc(row["interval_start"]),
        interval_end=_as_utc(row["interval_end"]),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        sample_count=int(str(row["sample_count"])),
        revision=int(str(row["revision"])),
        provenance=BarProvenance(str(row["provenance"])),
        quality=DataQuality(str(row["quality"])),
        source_listing_id=ProviderListingId(
            str(row["source_provider"]),
            str(row["source_environment"]),
            str(row["source_external_id"]),
        ),
    )


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected datetime")
    return value.astimezone(UTC)


def _capture_universe(settings: Settings) -> CaptureUniverse:
    return load_capture_universe(settings.capture_universe_path)


def _configuration_hash(settings: Settings) -> str:
    return _capture_universe(settings).configuration_hash


if __name__ == "__main__":
    main()
