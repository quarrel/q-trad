"""R2 OOF and software-verification orchestration.

The module deliberately keeps bundles thin: data and model artefacts remain children,
while manifests bind their immutable identities, source class and evidence class.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import numpy
import sklearn

from qtrad import __version__
from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore, R2FeatureManifest
from qtrad.application.r2_baselines import build_local_ridge_oof
from qtrad.application.r2_evaluation import (
    EvaluationModel,
    build_r2_evaluation,
    build_selection_manifest,
)
from qtrad.application.r2_features import (
    R2FoundationInputs,
    feature_schema_for_set,
    verify_raw_feature_manifest_bindings,
    verify_raw_feature_rows,
)
from qtrad.application.r2_pooled import build_pooled_ridge_oof
from qtrad.application.r2_preprocessing import (
    build_pooled_preprocessing_selection,
    build_r2_preprocessing_selection,
)
from qtrad.application.r2_readiness import R1FoundationBindings, _availability_dataset_id
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, FoldDataset, membership_hash
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    InstrumentRole,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.market_data import MarketDataSourceClass, PriceBasis
from qtrad.domain.r2_bundles import (
    ArtifactReference,
    R2ForecastManifest,
    R2OofBundle,
    R2SoftwareVerificationBundle,
    R2_EVALUATION_REGISTER_CONTRACT,
)
from qtrad.domain.r2_evaluation import (
    R2_EVALUATION_CONTRACT,
    R2_SELECTION_CONTRACT,
    ConfigurationDisposition,
    ConfigurationRecord,
    MetricAvailability,
    MetricValue,
    SelectionDecision,
    SelectionGateOutcome,
    SelectionManifest,
)
from qtrad.domain.r2_features import (
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_set_id,
)
from qtrad.domain.r2_models import PreprocessingFeatureKind, derive_r2_preprocessing_schema
from qtrad.domain.r2_readiness import (
    EligibilityDecision,
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    FeatureSet,
    ModelFamily,
    R2ExperimentConfig,
)
from qtrad.ports.clock import Clock
from qtrad.runtime.foundation_bundle import verify_foundation_bundle
from qtrad.runtime.r2_bundles import (
    atomic_create,
    canonical_bytes,
    reference_for_json,
    verify_r2_oof_bundle,
    verify_r2_software_bundle,
    write_r2_oof_bundle,
    write_r2_software_bundle,
)
from qtrad.runtime.r2_readiness import load_r2_experiment

OOF_DESCRIPTOR_CONTRACT = "qtrad-r2-oof-run-descriptor-v1"
_REQUIRED_FEATURE_SETS = frozenset({"L0", "L1", "P0", "P1"})


def runtime_identities() -> dict[str, str]:
    """Derive identities from the running source tree and installed libraries."""
    repository = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        )
        status = subprocess.run(
            ("git", "-C", str(repository), "status", "--porcelain", "--untracked-files=all"),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot derive the application commit identity") from exc
    if status.stdout.strip():
        raise RuntimeError("application identity requires a clean source tree")
    commit = result.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RuntimeError("git did not return a verified commit identity")
    declared_commit = os.environ.get("QTRAD_APPLICATION_COMMIT")
    if declared_commit is not None and declared_commit != commit:
        raise RuntimeError("declared application commit differs from the running source")
    image_digest = os.environ.get("QTRAD_IMAGE_DIGEST")
    if image_digest is None:
        raise RuntimeError("QTRAD_IMAGE_DIGEST must identify the immutable application image")
    if (
        not image_digest.startswith("sha256:")
        or len(image_digest) != len("sha256:") + 64
        or any(character not in "0123456789abcdef" for character in image_digest[7:])
    ):
        raise RuntimeError("QTRAD_IMAGE_DIGEST must be a verified sha256 digest")
    application = f"qtrad-{__version__}+git:{commit}+image:{image_digest}"
    return {
        "application_identity": application,
        "python_identity": sys.version.split()[0],
        "numpy_identity": numpy.__version__,
        "sklearn_identity": sklearn.__version__,
    }


def parse_feature_manifest_arguments(arguments: list[str]) -> dict[str, Path]:
    """Parse and validate repeated NAME=PATH arguments without accepting duplicates."""
    parsed: dict[str, Path] = {}
    for argument in arguments:
        name, separator, raw_path = argument.partition("=")
        if not separator or not name or not raw_path:
            raise ValueError("feature manifest must use NAME=PATH")
        if name in parsed:
            raise ValueError(f"duplicate feature manifest: {name}")
        parsed[name] = Path(raw_path)
    if set(parsed) != _REQUIRED_FEATURE_SETS:
        missing = sorted(_REQUIRED_FEATURE_SETS - set(parsed))
        extra = sorted(set(parsed) - _REQUIRED_FEATURE_SETS)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("undeclared " + ", ".join(extra))
        raise ValueError(
            "feature manifests must cover exactly L0/L1/P0/P1 (" + "; ".join(detail) + ")"
        )
    return parsed


def _foundation_inputs(verified: R1FoundationBindings) -> R2FoundationInputs:
    return R2FoundationInputs(
        bundle=verified.bundle,
        configuration=verified.configuration,
        observations=verified.observations,
        panel=verified.panel,
        targets=verified.targets,
        folds=verified.folds,
        availability_evidence=verified.availability_evidence,
    )


def _manifest_payload(manifest: R2FeatureManifest) -> dict[str, JsonValue]:
    return manifest.as_json()


def _load_and_verify_feature_manifests(
    *,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    feature_manifest_paths: dict[str, Path],
    root: Path,
    clock: Clock,
) -> dict[str, dict[str, JsonValue]]:
    foundation = _foundation_inputs(verified)
    store = ParquetR2FeatureStore(root, clock)
    payloads: dict[str, dict[str, JsonValue]] = {}
    for name in sorted(feature_manifest_paths):
        path = feature_manifest_paths[name]
        manifest = store.read_manifest(path)
        verify_raw_feature_manifest_bindings(
            manifest, foundation, experiment, feature_set_name=name
        )
        payloads[name] = _manifest_payload(manifest)
    return payloads


def _descriptor_payload(
    *,
    foundation_bundle_id: str,
    experiment: R2ExperimentConfig,
    feature_names: tuple[str, ...],
    run_kind: str,
    identities: dict[str, str],
) -> dict[str, JsonValue]:
    semantic: dict[str, JsonValue] = {
        "contract": OOF_DESCRIPTOR_CONTRACT,
        "schema_version": 1,
        "foundation_bundle_id": foundation_bundle_id,
        "experiment_configuration_id": experiment.configuration_id,
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
        "feature_sets": list(feature_names),
        "run_kind": run_kind,
        "application_identity": identities["application_identity"],
        "python_identity": identities["python_identity"],
        "numpy_identity": identities["numpy_identity"],
        "sklearn_identity": identities["sklearn_identity"],
        "holdout_range": [item.isoformat() for item in experiment.holdout_range],
        "acceptance_thresholds": dict(experiment.acceptance_thresholds),
        "target_instruments": list(experiment.target_instruments),
        "primary_horizon_seconds": experiment.primary_horizon.total_seconds(),
        "holdout_excluded": True,
    }
    descriptor_id = sha256(canonical_bytes(semantic)).hexdigest()
    return {**semantic, "descriptor_id": descriptor_id}


def _replay_input_payload(
    *,
    research_root: Path,
    paths: Mapping[str, Path],
) -> dict[str, object]:
    expected = {"foundation", "experiment", *_REQUIRED_FEATURE_SETS}
    if set(paths) != expected:
        raise ValueError(
            "representative replay inputs must include foundation, experiment and L0/L1/P0/P1"
        )
    children: dict[str, object] = {}
    for name, path in sorted(paths.items()):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"replay input must be a regular non-symlink file: {name}")
        children[name] = {
            "path": str(path.resolve()),
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    return {"research_root": str(research_root.resolve()), "children": children}


def _descriptor_reference(
    *,
    output: Path,
    relative_path: str,
    payload: Mapping[str, object],
) -> ArtifactReference:
    return reference_for_json(
        path=relative_path,
        contract=str(payload["contract"]),
        semantic_id=str(payload["descriptor_id"]),
        content=payload,
    )


def _load_feature_datasets(
    *,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    feature_manifest_paths: dict[str, Path],
    root: Path,
    clock: Clock,
) -> tuple[dict[str, R2FeatureDataset], dict[str, R2FeatureManifest]]:
    foundation = _foundation_inputs(verified)
    store = ParquetR2FeatureStore(root, clock)
    datasets: dict[str, R2FeatureDataset] = {}
    manifests: dict[str, R2FeatureManifest] = {}
    for name in sorted(feature_manifest_paths):
        manifest = store.read_manifest(feature_manifest_paths[name])
        verify_raw_feature_manifest_bindings(
            manifest, foundation, experiment, feature_set_name=name
        )
        rows = tuple(store.iter_rows(feature_manifest_paths[name]))
        verify_raw_feature_rows(iter(rows), foundation, experiment, feature_set_name=name)
        dataset = R2FeatureDataset.create(
            rows,
            feature_schema=manifest.feature_schema,
            feature_set_name=name,
            feature_set_id=manifest.feature_set_id,
            observation_dataset_id=manifest.observation_dataset_id,
            panel_dataset_id=manifest.panel_dataset_id,
            target_dataset_id=manifest.target_dataset_id,
            fold_dataset_id=manifest.fold_dataset_id,
            experiment_configuration_id=manifest.experiment_configuration_id,
            evidence_class=manifest.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )
        if dataset.dataset_id != manifest.semantic_dataset_id:
            raise ValueError(
                "feature manifest semantic dataset identity differs from replayed rows"
            )
        datasets[name] = dataset
        manifests[name] = manifest
    return datasets, manifests


def _dataset_payload(
    dataset: R2FeatureDataset, manifest: R2FeatureManifest | Mapping[str, object]
) -> dict[str, object]:
    manifest_payload = (
        manifest.as_json() if isinstance(manifest, R2FeatureManifest) else dict(manifest)
    )
    return {
        "contract": dataset.CONTRACT,
        "schema_version": dataset.SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "manifest": manifest_payload,
        **{
            key: value
            for key, value in dataset.manifest_json().items()
            if key not in {"contract", "schema_version", "dataset_id"}
        },
        "rows": [row.as_json() for row in dataset.rows],
    }


def _forecast_payload(
    dataset: ForecastDataset,
    *,
    source_class: MarketDataSourceClass,
    evidence_class: EvidenceClass,
) -> dict[str, object]:
    return {
        "contract": dataset.CONTRACT,
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "observation_dataset_id": dataset.observation_dataset_id,
        "panel_dataset_id": dataset.panel_dataset_id,
        "target_dataset_id": dataset.target_dataset_id,
        "fold_dataset_id": dataset.fold_dataset_id,
        "source_class": source_class.value,
        "evidence_class": evidence_class.value,
        "rows": [row.as_json() for row in dataset.rows],
    }


def _payload_identity(payload: Mapping[str, object]) -> str:
    for key in (
        "dataset_id",
        "artifact_id",
        "manifest_id",
        "selection_id",
        "fit_id",
        "coverage_id",
        "summary_id",
        "report_id",
        "descriptor_id",
        "scenario_id",
        "ablation_id",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    raise ValueError("R2 child payload has no semantic identity")


def _child_reference(path: str, payload: Mapping[str, object]) -> ArtifactReference:
    contract = payload.get("contract")
    if not isinstance(contract, str):
        raise ValueError("R2 child payload has no contract")
    return reference_for_json(
        path=path,
        contract=contract,
        semantic_id=_payload_identity(payload),
        content=payload,
    )


def _configuration_record(
    *,
    family: ModelFamily,
    feature_set_id: str | None,
    forecast_dataset_id: str | None,
    reason: str,
    market_data_source_class: MarketDataSourceClass,
) -> ConfigurationRecord:
    semantic = {
        "model_family": family.value,
        "feature_set_id": feature_set_id,
        "forecast_dataset_id": forecast_dataset_id,
        "reason": reason,
        "market_data_source_class": market_data_source_class.value,
    }
    return ConfigurationRecord(
        configuration_id=sha256(canonical_bytes(semantic)).hexdigest(),
        model_family=family,
        feature_set_id=feature_set_id,
        disposition=ConfigurationDisposition.EVALUATED,
        reason=reason,
        forecast_dataset_id=forecast_dataset_id,
        evaluated_model_manifest_id=None,
        market_data_source_class=market_data_source_class,
    )


def _model_forecasts(
    fold_results: tuple[Any, ...],
    *,
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
) -> ForecastDataset:
    rows = tuple(row for result in fold_results for row in result.forecasts.rows)
    return ForecastDataset.create(
        rows,
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        target_dataset_id=target_dataset_id,
        fold_dataset_id=fold_dataset_id,
    )


def _synthetic_pipeline_inputs() -> tuple[
    R1FoundationBindings,
    R2ExperimentConfig,
    dict[str, R2FeatureDataset],
    dict[str, Mapping[str, object]],
]:
    """Create deterministic typed inputs for the same R2 build path used in production."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = timedelta(minutes=15)
    holdout = (start + timedelta(hours=6), start + timedelta(hours=24))
    target_names = ("index:synthetic-a", "index:synthetic-b")
    context_name = "index:volatility"
    ordered = (*target_names, context_name)
    r1_id = sha256(b"r2-synthetic-r1").hexdigest()
    observation_id = sha256(b"r2-synthetic-observations").hexdigest()
    foundation_id = sha256(b"r2-synthetic-foundation-config").hexdigest()
    panel_id = sha256(b"r2-synthetic-panel").hexdigest()

    def eligibility(subject: str, state: FeatureEligibility) -> EligibilityDecision:
        return EligibilityDecision.create(
            subject=subject,
            state=state,
            evidence_start=start - timedelta(days=1),
            evidence_end=start + timedelta(hours=1),
            reason="deterministic software-verification fixture",
        )

    roles = {name: InstrumentRole.TARGET for name in target_names}
    roles[context_name] = InstrumentRole.CONTEXT
    feature_eligibility = {
        family: eligibility(
            family.value,
            FeatureEligibility.NOT_ELIGIBLE
            if family in {FeatureFamily.SPREAD, FeatureFamily.QUOTE_IMBALANCE}
            else FeatureEligibility.ELIGIBLE,
        )
        for family in FeatureFamily
    }
    local_families = (
        FeatureFamily.LOCAL_RETURNS,
        FeatureFamily.TIME_AVAILABILITY,
        FeatureFamily.LOCAL_VOLATILITY_RANGE,
    )
    experiment = R2ExperimentConfig(
        name="r2-software-verification-synthetic",
        schema_version=1,
        r1_bundle_id=r1_id,
        observation_dataset_id=observation_id,
        foundation_configuration_id=foundation_id,
        panel_dataset_id=panel_id,
        target_dataset_id="a" * 64,
        fold_dataset_id="b" * 64,
        r1_application_version="synthetic",
        r1_image_identity="qtrad@sha256:" + "1" * 64,
        ordered_instruments=ordered,
        instrument_roles=roles,
        target_instrument_eligibility={
            name: eligibility(name, FeatureEligibility.ELIGIBLE) for name in target_names
        },
        target_instruments=target_names,
        confirmatory_target_instruments=target_names,
        market_groups={target_names[0]: "synthetic-0", target_names[1]: "synthetic-1"},
        horizons=(horizon,),
        primary_horizon=horizon,
        feature_sets=(
            FeatureSet("L0", local_families[:2]),
            FeatureSet("L1", local_families),
            FeatureSet("P0", local_families),
            FeatureSet("P1", (*local_families, FeatureFamily.POOLED_CROSS_ASSET)),
        ),
        feature_windows=(timedelta(minutes=1), timedelta(minutes=5)),
        feature_coverage_thresholds={family: 0.0 for family in FeatureFamily},
        feature_eligibility=feature_eligibility,
        preprocessing_policy="TRAINING_MEDIAN_STANDARDISE_V1",
        alpha_grid=(0.01, 0.1, 1.0, 10.0),
        inner_validation_policy="CHRONOLOGICAL_TAIL_PURGED_V1",
        ridge_solver="lsqr",
        ridge_tolerance=1e-8,
        ridge_max_iterations=10_000,
        pooled_weighting_policy="EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE",
        minimum_training_rows=2,
        minimum_inner_validation_rows=1,
        minimum_outer_validation_rows=1,
        metric_policy="R2_METRICS_V1",
        forecast_bucket_policy="TRAINING_QUANTILES_V1",
        state_bucket_policy="TRAINING_THRESHOLDS_V1",
        model_selection_policy="OOF_PRIMARY_MSE_V1",
        acceptance_thresholds={
            "maximum_best_instrument_contribution": 1.0,
            "maximum_best_period_contribution": 1.0,
            "maximum_primary_mse_degradation": 0.0,
            "minimum_common_support": 0.0,
            "minimum_improving_fold_proportion": 0.0,
            "minimum_improving_instrument_proportion": 0.0,
        },
        holdout_range=holdout,
        numeric_replay_relative_tolerance=1e-10,
        numeric_replay_absolute_tolerance=1e-12,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        market_data_source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        model_families=tuple(ModelFamily),
    )
    targets_rows: list[TargetRow] = []
    for index in range(8):
        decision = start + timedelta(minutes=15 * index)
        for instrument_index, instrument in enumerate(target_names):
            targets_rows.append(
                TargetRow(
                    instrument_id=instrument,
                    decision_time=decision,
                    horizon=horizon,
                    target_basis=PriceBasis.MID,
                    target_revision_policy="LATEST_AVAILABLE_BEFORE_FREEZE",
                    target_start_time=decision,
                    target_end_time=decision + horizon,
                    target_freeze_at=decision + horizon,
                    target_available_at=decision + horizon,
                    label_start_close=Decimal("100"),
                    label_end_close=Decimal("101"),
                    log_return=0.01 * (index + instrument_index),
                    return_disposition=ReturnDisposition.VALID,
                    start_event_id=UUID(int=index * 2 + instrument_index + 1),
                    end_event_id=UUID(int=index * 2 + instrument_index + 2),
                    upper_log_excursion=0.02,
                    lower_log_excursion=-0.01,
                    excursion_disposition=ExcursionDisposition.VALID,
                )
            )
    targets = TargetDataset.create(
        targets_rows,
        observation_dataset_id=observation_id,
        foundation_configuration_id=foundation_id,
    )
    training_ids = tuple(
        row.target_id
        for row in targets.rows
        if row.target_end_time <= start + timedelta(minutes=75)
    )
    validation_ids = tuple(
        row.target_id
        for row in targets.rows
        if row.target_start_time >= start + timedelta(minutes=90)
    )
    fold = Fold(
        fold_id="synthetic-outer-0",
        training_start=start,
        training_cutoff=start + timedelta(minutes=75),
        validation_start=start + timedelta(minutes=90),
        validation_end=start + timedelta(minutes=120),
        embargo_end=start + timedelta(minutes=90),
        training_target_ids=training_ids,
        validation_target_ids=validation_ids,
        holdout_excluded=True,
        membership_hash=membership_hash(training_ids, validation_ids),
    )
    folds = FoldDataset.create(
        (fold,),
        target_dataset_id=targets.dataset_id,
        foundation_configuration_id=foundation_id,
    )
    experiment = replace(
        experiment,
        target_dataset_id=targets.dataset_id,
        fold_dataset_id=folds.dataset_id,
    )
    datasets: dict[str, R2FeatureDataset] = {}
    manifests: dict[str, Mapping[str, object]] = {}
    for feature_set in experiment.feature_sets:
        schema = feature_schema_for_set(experiment, feature_set.name)
        preprocessing_schema = derive_r2_preprocessing_schema(schema)
        set_id = feature_set_id(
            experiment.configuration_id,
            feature_set.name,
            schema,
            experiment.market_data_source_class,
        )
        set_rows = []
        for target in targets.rows:
            values = []
            for definition, transformed in zip(schema, preprocessing_schema.features, strict=True):
                if transformed.kind is PreprocessingFeatureKind.BINARY_INDICATOR:
                    value = 1.0
                elif definition.family is FeatureFamily.LOCAL_RETURNS:
                    value = float(target.decision_time.minute) / 15.0
                elif definition.family is FeatureFamily.POOLED_CROSS_ASSET:
                    value = float(target.instrument_id == target_names[1])
                elif definition.family is FeatureFamily.TIME_AVAILABILITY:
                    value = 1.0
                else:
                    value = 0.0
                values.append(RawFeatureValue(definition.name, value))
            set_rows.append(
                RawFeatureRow(
                    target.instrument_id,
                    target.decision_time,
                    target.decision_time,
                    target.decision_time,
                    set_id,
                    tuple(values),
                )
            )
        dataset = R2FeatureDataset.create(
            set_rows,
            feature_schema=schema,
            feature_set_name=feature_set.name,
            feature_set_id=set_id,
            observation_dataset_id=observation_id,
            panel_dataset_id=panel_id,
            target_dataset_id=targets.dataset_id,
            fold_dataset_id=folds.dataset_id,
            experiment_configuration_id=experiment.configuration_id,
            evidence_class=experiment.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )
        datasets[feature_set.name] = dataset
        manifests[feature_set.name] = {
            "contract": "qtrad-r2-parquet-feature-manifest-v1",
            "schema_version": 1,
            "semantic_dataset_id": dataset.dataset_id,
            "feature_set_name": feature_set.name,
            "feature_set_id": dataset.feature_set_id,
            "source_class": experiment.market_data_source_class.value,
            "evidence_class": experiment.evidence_class.value,
            "holdout_excluded": True,
        }
    evidence: dict[str, JsonValue] = {
        "availability_delay_report": {},
        "revision_delay_report": {},
        "data_gaps": [],
        "source_active_intervals": {name: [] for name in ordered},
        "lineage_summary": {},
        "observation_bounds": {
            "interval_start": start.isoformat(),
            "interval_end": holdout[1].isoformat(),
        },
    }
    availability_id = _availability_dataset_id(observation_id, evidence)
    verified = cast(
        R1FoundationBindings,
        SimpleNamespace(
            bundle=SimpleNamespace(
                bundle_id=r1_id,
                market_data_source_class=experiment.market_data_source_class,
                ordered_instruments=ordered,
                range_start=start,
                range_end=holdout[1],
                configuration=SimpleNamespace(dataset_id=foundation_id),
                observations=SimpleNamespace(dataset_id=observation_id),
                availability=SimpleNamespace(dataset_id=availability_id),
                panel=SimpleNamespace(dataset_id=panel_id),
                targets=SimpleNamespace(dataset_id=targets.dataset_id),
                folds=SimpleNamespace(dataset_id=folds.dataset_id),
                build_summary={
                    "application_version": "synthetic",
                    "image_identity": "qtrad@sha256:" + "1" * 64,
                },
            ),
            configuration=SimpleNamespace(
                configuration_id=foundation_id,
                observation_dataset_id=observation_id,
                ordered_instruments=ordered,
                instrument_roles=roles,
                target_horizons=(horizon,),
                holdout_range=holdout,
                range_start=start,
                range_end=holdout[1],
                availability_basis=AvailabilityBasis.PERSISTED_AT,
            ),
            observations=SimpleNamespace(
                dataset_id=observation_id,
                selection_policies={"availability_basis": "persisted_at"},
            ),
            panel=SimpleNamespace(
                dataset_id=panel_id,
                observation_dataset_id=observation_id,
                foundation_configuration_id=foundation_id,
            ),
            targets=targets,
            folds=folds,
            forecasts=SimpleNamespace(),
            availability_evidence=evidence,
        ),
    )
    return verified, experiment, datasets, manifests


def build_oof_bundle(
    *,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    feature_manifest_paths: dict[str, Path],
    research_root: Path,
    clock: Clock,
    output: Path,
    run_kind: str = "REPRESENTATIVE",
    dataset_overrides: Mapping[str, R2FeatureDataset] | None = None,
    manifest_overrides: Mapping[str, Mapping[str, object]] | None = None,
    replay_inputs: Mapping[str, Path] | None = None,
) -> Path:
    """Build and persist the complete R2.C--F1 OOF run from authenticated children."""
    foundation_source = getattr(verified.bundle, "market_data_source_class", None)
    if foundation_source is not experiment.market_data_source_class:
        raise ValueError("R2 experiment source class differs from the R1 foundation")
    if dataset_overrides is None:
        datasets, manifests = _load_feature_datasets(
            verified=verified,
            experiment=experiment,
            feature_manifest_paths=feature_manifest_paths,
            root=research_root,
            clock=clock,
        )
    else:
        datasets = dict(dataset_overrides)
        manifests = dict(manifest_overrides or {})
    if set(datasets) != _REQUIRED_FEATURE_SETS:
        raise ValueError("OOF build requires exactly L0/L1/P0/P1 feature datasets")
    identities = runtime_identities()
    local_datasets = tuple(datasets[name] for name in ("L0", "L1"))
    pooled_datasets = (datasets["P0"], datasets["P1"])
    selections_local = tuple(
        build_r2_preprocessing_selection(
            verified,
            datasets[name],
            experiment,
            model_family=ModelFamily.LOCAL_RIDGE,
            horizon=experiment.primary_horizon,
            outer_fold_id=fold.fold_id,
            target_instruments=(instrument,),
            application_image_identity=identities["application_identity"],
            sklearn_library_identity=identities["sklearn_identity"],
        )
        for name in ("L0", "L1")
        for instrument in experiment.target_instruments
        for fold in verified.folds.folds
    )
    selections_pooled = tuple(
        build_pooled_preprocessing_selection(
            verified,
            datasets[name],
            experiment,
            model_family=(
                ModelFamily.POOLED_LOCAL_RIDGE
                if name == "P0"
                else ModelFamily.POOLED_CROSS_ASSET_RIDGE
            ),
            horizon=experiment.primary_horizon,
            outer_fold_id=fold.fold_id,
            target_instruments=experiment.target_instruments,
            application_image_identity=identities["application_identity"],
            sklearn_library_identity=identities["sklearn_identity"],
        )
        for name in ("P0", "P1")
        for fold in verified.folds.folds
    )
    local_result = build_local_ridge_oof(
        verified,
        local_datasets,
        experiment,
        selections_local,
        application_image_identity=identities["application_identity"],
        numpy_library_identity=identities["numpy_identity"],
        sklearn_library_identity=identities["sklearn_identity"],
    )
    pooled_result = build_pooled_ridge_oof(
        verified,
        pooled_datasets,
        experiment,
        selections_pooled,
        local_result,
        datasets["L1"],
        application_image_identity=identities["application_identity"],
        numpy_library_identity=identities["numpy_identity"],
        sklearn_library_identity=identities["sklearn_identity"],
    )
    models: list[EvaluationModel] = []
    for family, name in (
        (ModelFamily.POOLED_LOCAL_RIDGE, "P0"),
        (ModelFamily.POOLED_CROSS_ASSET_RIDGE, "P1"),
    ):
        fold_results = tuple(
            result for result in pooled_result.fold_results if result.fit.model_family is family
        )
        models.append(
            EvaluationModel(
                family,
                datasets[name].feature_set_id,
                datasets[name],
                _model_forecasts(
                    fold_results,
                    observation_dataset_id=verified.observations.dataset_id,
                    panel_dataset_id=verified.panel.dataset_id,
                    target_dataset_id=verified.targets.dataset_id,
                    fold_dataset_id=verified.folds.dataset_id,
                ),
                fold_results,
            )
        )
    configurations = (
        _configuration_record(
            family=ModelFamily.ZERO_RETURN,
            feature_set_id=None,
            forecast_dataset_id=None,
            reason="zero-return control",
            market_data_source_class=experiment.market_data_source_class,
        ),
        *(
            _configuration_record(
                family=ModelFamily.LOCAL_RIDGE,
                feature_set_id=dataset.feature_set_id,
                forecast_dataset_id=_model_forecasts(
                    tuple(
                        result
                        for result in local_result.fold_results
                        if result.fit.feature_set_id == dataset.feature_set_id
                    ),
                    observation_dataset_id=verified.observations.dataset_id,
                    panel_dataset_id=verified.panel.dataset_id,
                    target_dataset_id=verified.targets.dataset_id,
                    fold_dataset_id=verified.folds.dataset_id,
                ).dataset_id,
                reason=f"local {dataset.feature_set_name} Ridge",
                market_data_source_class=experiment.market_data_source_class,
            )
            for dataset in local_datasets
        ),
        *(
            _configuration_record(
                family=model.model_family,
                feature_set_id=model.feature_set_id,
                forecast_dataset_id=model.forecasts.dataset_id,
                reason=f"pooled {model.feature_set_id} Ridge",
                market_data_source_class=experiment.market_data_source_class,
            )
            for model in models
        ),
    )
    local_comparator, evaluation = build_r2_evaluation(
        verified,
        experiment,
        local_result,
        models,
        configurations,
        local_feature_set_id=datasets["L1"].feature_set_id,
        local_feature_datasets=local_datasets,
    )
    selection_preview = build_selection_manifest(
        evaluation,
        local_comparator,
        experiment,
        primary_metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
        secondary_metrics=("RMSE",),
        final_fitting_procedure="PENDING_R2_H_INTEGRATION",
        application_image_identity=identities["application_identity"],
        frozen_at=clock.now(),
        frozen_by="oof-build-replay",
    )

    children: dict[str, dict[str, object]] = {}
    feature_refs: list[ArtifactReference] = []
    for name in sorted(datasets):
        payload = _dataset_payload(datasets[name], manifests[name])
        path = f"features/{name}.json"
        children[path] = payload
        feature_refs.append(_child_reference(path, payload))

    preprocessing_refs: list[ArtifactReference] = []
    for index, selection in enumerate((*selections_local, *selections_pooled)):
        payload = cast(dict[str, object], selection.as_json())
        path = f"preprocessing/{index:04d}.json"
        children[path] = payload
        preprocessing_refs.append(_child_reference(path, payload))

    fit_refs: list[ArtifactReference] = []
    coverage_refs: list[ArtifactReference] = []
    forecast_manifest_refs: list[ArtifactReference] = []
    evaluation_refs: list[ArtifactReference] = []
    forecast_child_refs: list[ArtifactReference] = []
    all_results = (*local_result.fold_results, *pooled_result.fold_results)
    for index, result in enumerate(all_results):
        fit_payload = cast(dict[str, object], result.fit.as_json())
        fit_path = f"fits/{index:04d}.json"
        children[fit_path] = fit_payload
        fit_refs.append(_child_reference(fit_path, fit_payload))
        coverage_payload = cast(
            dict[str, object],
            {
                **result.coverage.as_json(),
                "source_class": experiment.market_data_source_class.value,
                "evidence_class": experiment.evidence_class.value,
            },
        )
        coverage_path = f"coverage/{index:04d}.json"
        children[coverage_path] = coverage_payload
        coverage_refs.append(_child_reference(coverage_path, coverage_payload))
        forecast_payload = _forecast_payload(
            result.forecasts,
            source_class=experiment.market_data_source_class,
            evidence_class=experiment.evidence_class,
        )
        forecast_child_path = f"forecasts/{index:04d}.data.json"
        children[forecast_child_path] = forecast_payload
        forecast_child_ref = _child_reference(forecast_child_path, forecast_payload)
        forecast_child_refs.append(forecast_child_ref)
        forecast_manifest = R2ForecastManifest.create(
            forecast_dataset_id=result.forecasts.dataset_id,
            experiment_configuration_id=experiment.configuration_id,
            source_class=experiment.market_data_source_class,
            evidence_class=experiment.evidence_class,
            forecast_child=forecast_child_ref,
        )
        forecast_manifest_payload = cast(dict[str, object], forecast_manifest.as_json())
        forecast_manifest_path = f"forecasts/{index:04d}.manifest.json"
        children[forecast_manifest_path] = forecast_manifest_payload
        forecast_manifest_refs.append(
            _child_reference(forecast_manifest_path, forecast_manifest_payload)
        )

    for summary, name in (
        (local_result.coefficient_stability, "local"),
        (pooled_result.coefficient_stability, "pooled"),
    ):
        payload = cast(
            dict[str, object],
            {
                **summary.as_json(),
                "source_class": experiment.market_data_source_class.value,
                "evidence_class": experiment.evidence_class.value,
            },
        )
        path = f"evaluation/{name}-stability.json"
        children[path] = payload
        evaluation_refs.append(_child_reference(path, payload))
    local_comparator_payload: dict[str, object] = {
        **local_comparator.as_json(),
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
    }
    local_comparator_path = "evaluation/local-comparator.json"
    children[local_comparator_path] = local_comparator_payload
    local_comparator_ref = _child_reference(local_comparator_path, local_comparator_payload)
    evaluation_refs.append(local_comparator_ref)

    evaluated_model_refs: list[ArtifactReference] = []
    for model in evaluation.evaluated_models:
        model_payload: dict[str, object] = {
            **model.as_json(),
            "source_class": experiment.market_data_source_class.value,
            "evidence_class": experiment.evidence_class.value,
        }
        model_path = f"evaluation/models/{model.manifest_id}.json"
        children[model_path] = model_payload
        model_ref = _child_reference(model_path, model_payload)
        evaluated_model_refs.append(model_ref)
        evaluation_refs.append(model_ref)

    evaluation_payload: dict[str, object] = {
        **evaluation.as_json(),
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
    }
    evaluation_path = "evaluation/report.json"
    children[evaluation_path] = evaluation_payload
    evaluation_ref = _child_reference(evaluation_path, evaluation_payload)
    evaluation_refs.append(evaluation_ref)

    ablation = pooled_result.ablation
    ablation_payload: dict[str, object] = {
        "contract": "qtrad-r2-pooled-ablation-v1",
        "schema_version": 1,
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
        "local_fold_fit_ids": list(ablation.local_fold_fit_ids),
        "pooled_local_fold_fit_ids": list(ablation.pooled_local_fold_fit_ids),
        "pooled_context_fold_fit_ids": list(ablation.pooled_context_fold_fit_ids),
        "local_target_ids": list(ablation.local_target_ids),
        "pooled_local_target_ids": list(ablation.pooled_local_target_ids),
        "pooled_context_target_ids": list(ablation.pooled_context_target_ids),
        "common_target_ids": list(ablation.common_target_ids),
        "holdout_excluded": True,
    }
    ablation_payload["ablation_id"] = sha256(
        canonical_bytes(
            {key: value for key, value in ablation_payload.items() if key != "ablation_id"}
        )
    ).hexdigest()
    ablation_path = "evaluation/pooled-ablation.json"
    children[ablation_path] = ablation_payload
    ablation_ref = _child_reference(ablation_path, ablation_payload)
    evaluation_refs.append(ablation_ref)
    register: dict[str, object] = {
        "contract": R2_EVALUATION_REGISTER_CONTRACT,
        "schema_version": 2,
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
        "local_comparator": local_comparator_ref.as_json(),
        "evaluated_models": [item.as_json() for item in evaluated_model_refs],
        "evaluation": evaluation_ref.as_json(),
        "forecast_manifests": [item.as_json() for item in forecast_manifest_refs],
        "coverage": [item.as_json() for item in coverage_refs],
        "pooled_ablation": ablation_ref.as_json(),
        "configurations": [item.as_json() for item in configurations],
        "selection_evaluation_report_id": evaluation.report_id,
        "selection_decisions": [item.as_json() for item in selection_preview.decisions],
        "selection_selected_configuration_ids": list(selection_preview.selected_configuration_ids),
        "selection_holdout_comparator_configuration_ids": list(
            selection_preview.holdout_comparator_configuration_ids
        ),
        "holdout_excluded": True,
    }
    register["report_id"] = sha256(canonical_bytes(register)).hexdigest()
    register_path = "evaluation/register.json"
    children[register_path] = register
    evaluation_refs.append(_child_reference(register_path, register))
    evaluation_refs.extend(forecast_child_refs)
    descriptor = _descriptor_payload(
        foundation_bundle_id=verified.bundle.bundle_id,
        experiment=experiment,
        feature_names=tuple(sorted(datasets)),
        run_kind=run_kind,
        identities=identities,
    )
    descriptor.update(
        {
            "fit_count": len(fit_refs),
            "forecast_manifest_count": len(forecast_manifest_refs),
            "coverage_count": len(coverage_refs),
            "evaluation_report_id": evaluation.report_id,
        }
    )
    if replay_inputs is not None:
        descriptor["replay_inputs"] = cast(
            dict[str, JsonValue],
            _replay_input_payload(research_root=research_root, paths=replay_inputs),
        )
    descriptor["descriptor_id"] = sha256(
        canonical_bytes({key: value for key, value in descriptor.items() if key != "descriptor_id"})
    ).hexdigest()
    descriptor_path = "evaluation/run-descriptor.json"
    children[descriptor_path] = cast(dict[str, object], descriptor)
    evaluation_refs.append(
        _descriptor_reference(output=output, relative_path=descriptor_path, payload=descriptor)
    )
    bundle = R2OofBundle.create(
        foundation_bundle_id=verified.bundle.bundle_id,
        experiment_configuration_id=experiment.configuration_id,
        source_class=experiment.market_data_source_class,
        evidence_class=experiment.evidence_class,
        feature_children=tuple(feature_refs),
        preprocessing_children=tuple(preprocessing_refs),
        fit_children=tuple(fit_refs),
        forecast_manifests=tuple(forecast_manifest_refs),
        coverage_children=tuple(coverage_refs),
        evaluation_children=tuple(evaluation_refs),
    )
    return write_r2_oof_bundle(output, bundle, children)


def _load_selection(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("selection manifest must be a regular non-symlink file")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("selection manifest must be a JSON object")
    return value


def _oof_child_payload(bundle_path: Path, bundle: R2OofBundle, contract: str) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    for reference in (*bundle.evaluation_children, *bundle.forecast_manifests):
        if reference.contract != contract:
            continue
        payload = _load_selection(bundle_path.parent / reference.path)
        if contract == R2_EVALUATION_CONTRACT and "report_id" not in payload:
            continue
        matches.append(payload)
    if len(matches) != 1:
        raise ValueError(f"OOF bundle must contain exactly one required {contract} child")
    return matches[0]


def _selection_decisions_from_payload(value: object) -> tuple[SelectionDecision, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("OOF register has no persisted selection-gate decisions")

    def metric(raw: object) -> MetricValue:
        if not isinstance(raw, dict):
            raise ValueError("selection gate metric is not an object")
        raw = cast(dict[str, object], raw)
        availability = MetricAvailability(str(raw["availability"]))
        if availability is MetricAvailability.DEFINED:
            number = raw["value"]
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError("defined selection metric has no numeric value")
            if raw.get("reason") is not None:
                raise ValueError("defined selection metric has an unexpected reason")
            return MetricValue.defined(float(number))
        reason = raw["reason"]
        if not isinstance(reason, str) or not reason:
            raise ValueError("undefined selection metric has no reason")
        if raw.get("value") is not None:
            raise ValueError("undefined selection metric has an unexpected value")
        return MetricValue.not_defined(reason)

    decisions: list[SelectionDecision] = []
    for raw_decision in value:
        if not isinstance(raw_decision, dict):
            raise ValueError("selection decision is not an object")
        raw_decision = cast(dict[str, object], raw_decision)
        configuration_id = str(raw_decision["configuration_id"])
        gates_raw = raw_decision["gates"]
        if not isinstance(gates_raw, list):
            raise ValueError("selection decision gates are not a list")
        gates: list[SelectionGateOutcome] = []
        for raw_gate in gates_raw:
            if not isinstance(raw_gate, dict):
                raise ValueError("selection gate is not an object")
            raw_gate = cast(dict[str, object], raw_gate)
            gates.append(
                SelectionGateOutcome(
                    configuration_id=configuration_id,
                    name=str(raw_gate["name"]),
                    passed=bool(raw_gate["passed"]),
                    observed=metric(raw_gate["observed"]),
                    threshold=metric(raw_gate["threshold"]),
                    reason=str(raw_gate["reason"]),
                )
            )
        decisions.append(
            SelectionDecision(
                configuration_id=configuration_id,
                disposition=ConfigurationDisposition(str(raw_decision["disposition"])),
                reason=str(raw_decision["reason"]),
                gates=tuple(gates),
            )
        )
    ordered = tuple(sorted(decisions, key=lambda item: item.configuration_id))
    if ordered != tuple(decisions) or len({item.configuration_id for item in decisions}) != len(
        decisions
    ):
        raise ValueError("selection decisions are not unique and ordered")
    return ordered


def selection_freeze(
    *,
    oof_bundle_path: Path,
    frozen_by: str,
    output: Path,
) -> Path:
    """Create a typed, holdout-free SelectionManifest from the OOF register."""
    if not frozen_by.strip():
        raise ValueError("frozen-by must be non-empty")
    bundle = verify_r2_oof_bundle(oof_bundle_path)
    register = _oof_child_payload(oof_bundle_path, bundle, R2_EVALUATION_REGISTER_CONTRACT)
    descriptor = _oof_child_payload(oof_bundle_path, bundle, OOF_DESCRIPTOR_CONTRACT)
    raw_configurations = register.get("configurations")
    if not isinstance(raw_configurations, list) or not raw_configurations:
        raise ValueError("OOF evaluation register has no complete configuration set")
    configurations: list[dict[str, object]] = []
    for item in raw_configurations:
        if not isinstance(item, dict):
            raise ValueError("OOF evaluation register contains an invalid configuration")
        configurations.append(cast(dict[str, object], item))
    evaluated_ids = tuple(sorted(str(item["configuration_id"]) for item in configurations))
    raw_selection_decisions = register.get("selection_decisions")
    decisions = _selection_decisions_from_payload(raw_selection_decisions)
    if tuple(item.configuration_id for item in decisions) != evaluated_ids:
        raise ValueError("persisted selection decisions do not cover the evaluation register")
    holdout_range_value = descriptor.get("holdout_range")
    if (
        not isinstance(holdout_range_value, list)
        or len(holdout_range_value) != 2
        or not all(isinstance(value, str) for value in holdout_range_value)
    ):
        raise ValueError("OOF descriptor has no authenticated holdout range")
    thresholds = descriptor.get("acceptance_thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("OOF descriptor has no authenticated selection thresholds")
    evaluation_payload = _oof_child_payload(oof_bundle_path, bundle, R2_EVALUATION_CONTRACT)
    evaluation_report_id = evaluation_payload.get("report_id")
    report_ref = register.get("evaluation")
    local_ref = register.get("local_comparator")
    selected_values = register.get("selection_selected_configuration_ids")
    holdout_values = register.get("selection_holdout_comparator_configuration_ids")
    if (
        not isinstance(evaluation_report_id, str)
        or register.get("selection_evaluation_report_id") != evaluation_report_id
        or not isinstance(report_ref, dict)
        or report_ref.get("semantic_id") != evaluation_report_id
        or not isinstance(local_ref, dict)
        or not isinstance(selected_values, list)
        or not isinstance(holdout_values, list)
        or not all(isinstance(item, str) for item in (*selected_values, *holdout_values))
    ):
        raise ValueError("OOF register has incomplete evaluation lineage or selection replay")
    selected_ids = tuple(sorted(cast(list[str], selected_values)))
    holdout_ids = tuple(sorted(cast(list[str], holdout_values)))
    if selected_ids != tuple(
        item.configuration_id
        for item in decisions
        if item.disposition is ConfigurationDisposition.SELECTED_CANDIDATE
    ) or holdout_ids != tuple(
        item.configuration_id
        for item in decisions
        if item.disposition
        in (ConfigurationDisposition.RETAINED_CONTROL, ConfigurationDisposition.SELECTED_CANDIDATE)
    ):
        raise ValueError("persisted selection IDs differ from replayed decisions")
    local_ref_payload = cast(dict[str, object], local_ref)
    local_comparator_id = local_ref_payload.get("semantic_id")
    if not isinstance(local_comparator_id, str):
        raise ValueError("OOF register local comparator has no semantic ID")
    threshold_values: list[tuple[str, float]] = []
    for name, value in thresholds.items():
        if not isinstance(name, str) or not isinstance(value, (int, float)):
            raise ValueError("OOF descriptor has invalid selection thresholds")
        threshold_values.append((name, float(value)))
    manifest = SelectionManifest.create(
        experiment_configuration_id=bundle.experiment_configuration_id,
        evidence_class=bundle.evidence_class,
        evaluation_report_id=evaluation_report_id,
        local_comparator_manifest_id=local_comparator_id,
        evaluated_configuration_ids=evaluated_ids,
        predeclared_comparators=tuple(ModelFamily),
        primary_metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
        secondary_metrics=("RMSE",),
        acceptance_thresholds=tuple(threshold_values),
        decisions=tuple(decisions),
        selected_configuration_ids=selected_ids,
        holdout_comparator_configuration_ids=holdout_ids,
        final_fitting_procedure="PENDING_R2_H_INTEGRATION",
        holdout_range=(
            datetime.fromisoformat(str(holdout_range_value[0])),
            datetime.fromisoformat(str(holdout_range_value[1])),
        ),
        application_image_identity=str(descriptor["application_identity"]),
        frozen_at=datetime.now(UTC),
        frozen_by=frozen_by,
        market_data_source_class=bundle.source_class,
        foundation_bundle_id=bundle.foundation_bundle_id,
        oof_bundle_id=bundle.bundle_id,
    )
    atomic_create(output, canonical_bytes(cast(dict[str, object], manifest.as_json())))
    return output


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ValueError("bundle source must be a regular directory")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"software output child already exists: {destination}")
    for source_path in sorted(source.rglob("*")):
        relative = source_path.relative_to(source)
        target = destination / relative
        if source_path.is_symlink():
            raise ValueError("software bundle cannot copy symlink children")
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source_path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_create(target, source_path.read_bytes())
        else:
            raise ValueError("software bundle child must be a regular file or directory")


def _copy_file(source: Path, destination: Path) -> dict[str, object]:
    if source.is_symlink() or not source.is_file():
        raise ValueError("selection must be a regular non-symlink file")
    payload = _load_selection(source)
    atomic_create(destination, canonical_bytes(payload))
    return payload


def _selection_reference(path: str, payload: dict[str, object]) -> ArtifactReference:
    identity = next(
        (
            payload[key]
            for key in ("selection_id", "artifact_id", "manifest_id", "bundle_id")
            if key in payload
        ),
        None,
    )
    if not isinstance(identity, str):
        raise ValueError("selection manifest has no semantic identity")
    contract = payload.get("contract")
    if not isinstance(contract, str):
        raise ValueError("selection manifest has no contract")
    return reference_for_json(
        path=path,
        contract=contract,
        semantic_id=identity,
        content=payload,
    )


def build_software_bundle(
    *,
    representative_oof_bundle_path: Path,
    representative_selection_path: Path,
    output: Path,
) -> Path:
    """Build the top-level software bundle after independently verifying the representative run."""
    representative_oof = verify_r2_oof_bundle(representative_oof_bundle_path)
    _replay_representative_oof(representative_oof_bundle_path)
    if representative_oof.source_class is not MarketDataSourceClass.IG_NATIVE_CAPTURE:
        raise ValueError("representative software integration must use IG_NATIVE_CAPTURE")
    if representative_oof.evidence_class is not EvidenceClass.IMPLEMENTATION:
        raise ValueError("representative software integration must use implementation evidence")
    descriptor = _oof_child_payload(
        representative_oof_bundle_path, representative_oof, OOF_DESCRIPTOR_CONTRACT
    )
    if descriptor.get("run_kind") != "REPRESENTATIVE":
        raise ValueError("representative software integration requires a representative run")
    if descriptor.get("feature_sets") != sorted(_REQUIRED_FEATURE_SETS):
        raise ValueError("representative run must cover exactly L0/L1/P0/P1")
    expected_targets = {
        "fx:aud-usd",
        "fx:eur-usd",
        "index:australia-200",
        "index:us-500",
        "commodity:spot-gold",
        "commodity:us-crude",
    }
    target_instruments = descriptor.get("target_instruments")
    if not isinstance(target_instruments, list) or set(target_instruments) != expected_targets:
        raise ValueError("representative run is not the fixed capture-v4 integration")
    selection = _load_selection(representative_selection_path)
    if selection.get("contract") != R2_SELECTION_CONTRACT:
        raise ValueError("representative selection must be a typed SelectionManifest")
    evaluation_payload = _oof_child_payload(
        representative_oof_bundle_path, representative_oof, R2_EVALUATION_CONTRACT
    )
    if selection.get("evaluation_report_id") != evaluation_payload.get("report_id"):
        raise ValueError("representative selection does not bind the supplied OOF report")
    output.mkdir(parents=True, exist_ok=False)
    _copy_tree(representative_oof_bundle_path.parent, output / "representative" / "oof")
    representative_selection_target = output / "representative" / "selection.json"
    representative_selection_target.parent.mkdir(parents=True, exist_ok=True)
    representative_selection = _copy_file(
        representative_selection_path, representative_selection_target
    )
    identities = runtime_identities()

    synthetic_root = output / "synthetic" / "oof"
    synthetic_verified, synthetic_experiment, synthetic_datasets, synthetic_manifests = (
        _synthetic_pipeline_inputs()
    )
    synthetic_manifest = build_oof_bundle(
        verified=synthetic_verified,
        experiment=synthetic_experiment,
        feature_manifest_paths={},
        research_root=output,
        clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
        output=synthetic_root,
        run_kind="SYNTHETIC",
        dataset_overrides=synthetic_datasets,
        manifest_overrides=synthetic_manifests,
    )
    synthetic_selection_path = output / "synthetic" / "selection.json"
    selection_freeze(
        oof_bundle_path=synthetic_manifest,
        frozen_by="software-verification",
        output=synthetic_selection_path,
    )
    synthetic_bundle = verify_r2_oof_bundle(synthetic_manifest)
    synthetic_selection_payload = _load_selection(synthetic_selection_path)
    synthetic_ref = _selection_reference("synthetic/selection.json", synthetic_selection_payload)
    representative_oof_ref = reference_for_json(
        path="representative/oof/manifest.json",
        contract=representative_oof.CONTRACT,
        semantic_id=representative_oof.bundle_id,
        content=representative_oof.as_json(),
    )
    representative_selection_ref = _selection_reference(
        "representative/selection.json", representative_selection
    )
    synthetic_oof_ref = reference_for_json(
        path="synthetic/oof/manifest.json",
        contract=synthetic_bundle.CONTRACT,
        semantic_id=synthetic_bundle.bundle_id,
        content=synthetic_bundle.as_json(),
    )
    identities = runtime_identities()
    software = R2SoftwareVerificationBundle.create(
        synthetic_oof_bundle=synthetic_oof_ref,
        representative_oof_bundle=representative_oof_ref,
        synthetic_selection=synthetic_ref,
        representative_selection=representative_selection_ref,
        **identities,
        representative_integration_ready="READY",
        evidence_disposition="IMPLEMENTATION_EVIDENCE_ONLY",
        research_disposition="RESEARCH_EVIDENCE_PENDING",
    )
    return write_r2_software_bundle(output, software)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        candidate.relative_to(root).as_posix(): candidate.read_bytes()
        for candidate in root.rglob("*")
        if candidate.is_file()
    }


async def _replay_representative_oof_async(path: Path) -> None:
    bundle = verify_r2_oof_bundle(path)
    descriptor = _oof_child_payload(path, bundle, OOF_DESCRIPTOR_CONTRACT)
    if descriptor.get("run_kind") != "REPRESENTATIVE":
        raise ValueError("representative replay requires a representative OOF run")
    raw_inputs = descriptor.get("replay_inputs")
    if not isinstance(raw_inputs, dict):
        raise ValueError("representative OOF descriptor has no authenticated replay inputs")
    raw_root = raw_inputs.get("research_root")
    raw_children = raw_inputs.get("children")
    if not isinstance(raw_root, str) or not isinstance(raw_children, dict):
        raise ValueError("representative replay inputs are malformed")
    paths: dict[str, Path] = {}
    for name, value in raw_children.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise ValueError("representative replay input child is malformed")
        raw_path = value.get("path")
        expected_digest = value.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(expected_digest, str):
            raise ValueError("representative replay input identity is incomplete")
        candidate = Path(raw_path)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"representative replay input is unavailable: {name}")
        if sha256(candidate.read_bytes()).hexdigest() != expected_digest:
            raise ValueError(f"representative replay input changed: {name}")
        paths[name] = candidate
    replay = _replay_input_payload(research_root=Path(raw_root), paths=paths)
    if replay != raw_inputs:
        raise ValueError("representative replay input identity does not authenticate")
    verified = await verify_foundation_bundle(
        root=Path(raw_root),
        bundle_path=paths["foundation"],
        clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
    )
    experiment = load_r2_experiment(paths["experiment"])
    feature_paths = {name: paths[name] for name in _REQUIRED_FEATURE_SETS}
    with TemporaryDirectory() as temporary:
        expected_root = Path(temporary) / "oof"
        build_oof_bundle(
            verified=verified,
            experiment=experiment,
            feature_manifest_paths=feature_paths,
            research_root=Path(raw_root),
            clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
            output=expected_root,
            run_kind="REPRESENTATIVE",
            replay_inputs=paths,
        )
        if _tree_bytes(path.parent) != _tree_bytes(expected_root):
            raise ValueError(
                "representative OOF bundle does not replay to the authenticated pipeline"
            )


def _replay_representative_oof(path: Path) -> None:
    asyncio.run(_replay_representative_oof_async(path))


def _replay_synthetic_oof(path: Path) -> None:
    bundle = verify_r2_oof_bundle(path)
    descriptor = _oof_child_payload(path, bundle, OOF_DESCRIPTOR_CONTRACT)
    if descriptor.get("run_kind") != "SYNTHETIC":
        return
    verified, experiment, datasets, manifests = _synthetic_pipeline_inputs()
    with TemporaryDirectory() as temporary:
        expected_root = Path(temporary) / "oof"
        build_oof_bundle(
            verified=verified,
            experiment=experiment,
            feature_manifest_paths={},
            research_root=Path(temporary),
            clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
            output=expected_root,
            run_kind="SYNTHETIC",
            dataset_overrides=datasets,
            manifest_overrides=manifests,
        )
        if _tree_bytes(path.parent) != _tree_bytes(expected_root):
            raise ValueError("synthetic OOF bundle does not replay to the authenticated pipeline")


def verify_oof_bundle(path: Path) -> R2OofBundle:
    """Verify an OOF envelope and replay the deterministic synthetic scenario."""
    bundle = verify_r2_oof_bundle(path)
    _replay_synthetic_oof(path)
    return bundle


def _verify_software_bundle_envelope(path: Path) -> R2SoftwareVerificationBundle:
    """Verify both nested OOF integrations and their holdout-free selections."""
    software = verify_r2_software_bundle(path)
    identities = runtime_identities()
    for key, expected in identities.items():
        if getattr(software, key) != expected:
            raise ValueError(f"software bundle {key} differs from the running environment")
    if software.representative_integration_ready != "READY":
        raise ValueError("software bundle representative integration is not READY")
    if software.evidence_disposition != "IMPLEMENTATION_EVIDENCE_ONLY":
        raise ValueError("software bundle evidence disposition is not implementation-only")
    if software.research_disposition != "RESEARCH_EVIDENCE_PENDING":
        raise ValueError("software bundle research disposition is not pending")
    root = path.parent
    synthetic = root / software.synthetic_oof_bundle.path
    representative = root / software.representative_oof_bundle.path
    synthetic_oof = verify_oof_bundle(synthetic)
    representative_oof = verify_r2_oof_bundle(representative)
    if synthetic_oof.source_class is not MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
        raise ValueError("synthetic software integration must use IBKR_HISTORICAL_RESEARCH")
    if representative_oof.source_class is not MarketDataSourceClass.IG_NATIVE_CAPTURE:
        raise ValueError("representative software integration must use IG_NATIVE_CAPTURE")
    if synthetic_oof.evidence_class is not EvidenceClass.IMPLEMENTATION:
        raise ValueError("synthetic software integration must use implementation evidence")
    if representative_oof.evidence_class is not EvidenceClass.IMPLEMENTATION:
        raise ValueError("representative software integration must use implementation evidence")
    expected_targets = {
        "fx:aud-usd",
        "fx:eur-usd",
        "index:australia-200",
        "index:us-500",
        "commodity:spot-gold",
        "commodity:us-crude",
    }
    for oof, oof_path, selection_reference in (
        (synthetic_oof, synthetic, software.synthetic_selection),
        (representative_oof, representative, software.representative_selection),
    ):
        descriptor = _oof_child_payload(oof_path, oof, OOF_DESCRIPTOR_CONTRACT)
        if descriptor.get("feature_sets") != sorted(_REQUIRED_FEATURE_SETS):
            raise ValueError("software OOF child does not cover exactly L0/L1/P0/P1")
        target_instruments = descriptor.get("target_instruments")
        if oof is representative_oof and (
            not isinstance(target_instruments, list) or set(target_instruments) != expected_targets
        ):
            raise ValueError("representative software OOF child has the wrong target universe")
        payload = _load_selection(root / selection_reference.path)
        if payload.get("contract") != "qtrad-r2-selection-v1":
            raise ValueError("software selection child is not a typed SelectionManifest")
        if payload.get("manifest_id") != selection_reference.semantic_id:
            raise ValueError("software selection manifest ID does not match its reference")
        if payload.get("oof_bundle_id") != oof.bundle_id:
            raise ValueError("software selection does not bind its OOF bundle")
        if payload.get("foundation_bundle_id") != oof.foundation_bundle_id:
            raise ValueError("software selection does not bind its foundation")
        if payload.get("source_class") != oof.source_class.value:
            raise ValueError("software selection source class differs from its OOF bundle")
        if payload.get("evidence_class") != oof.evidence_class.value:
            raise ValueError("software selection evidence class differs from its OOF bundle")
        if payload.get("holdout_state_verification") != "PENDING_R2_H_INTEGRATION":
            raise ValueError("software selection must leave holdout verification pending")
        evaluation_payload = _oof_child_payload(oof_path, oof, R2_EVALUATION_CONTRACT)
        if payload.get("evaluation_report_id") != evaluation_payload.get("report_id"):
            raise ValueError("software selection does not bind the OOF evaluation report")
        if payload.get("application_image_identity") != identities["application_identity"]:
            raise ValueError("software selection identity differs from the running application")
    return software


def verify_software_bundle(path: Path) -> R2SoftwareVerificationBundle:
    software = _verify_software_bundle_envelope(path)
    representative = path.parent / software.representative_oof_bundle.path
    _replay_representative_oof(representative)
    return software


async def verify_software_bundle_async(path: Path) -> R2SoftwareVerificationBundle:
    software = _verify_software_bundle_envelope(path)
    representative = path.parent / software.representative_oof_bundle.path
    await _replay_representative_oof_async(representative)
    return software


def load_experiment_and_feature_paths(
    *,
    experiment_path: Path,
    feature_arguments: list[str],
) -> tuple[R2ExperimentConfig, dict[str, Path]]:
    experiment = load_r2_experiment(experiment_path)
    return experiment, parse_feature_manifest_arguments(feature_arguments)
