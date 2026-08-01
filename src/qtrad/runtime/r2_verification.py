"""R2 OOF and software-verification orchestration.

The module deliberately keeps bundles thin: data and model artefacts remain children,
while manifests bind their immutable identities, source class and evidence class.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import numpy
import sklearn

from qtrad import __version__
from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore, R2FeatureManifest
from qtrad.application.r2_baselines import build_local_ridge_oof
from qtrad.application.r2_evaluation import EvaluationModel, build_r2_evaluation
from qtrad.application.r2_features import (
    R2FoundationInputs,
    verify_raw_feature_manifest_bindings,
    verify_raw_feature_rows,
)
from qtrad.application.r2_pooled import build_pooled_ridge_oof
from qtrad.application.r2_preprocessing import (
    build_pooled_preprocessing_selection,
    build_r2_preprocessing_selection,
)
from qtrad.application.r2_readiness import R1FoundationBindings
from qtrad.domain.events import JsonValue
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import (
    ArtifactReference,
    R2ForecastManifest,
    R2OofBundle,
    R2SoftwareVerificationBundle,
)
from qtrad.domain.r2_evaluation import (
    ConfigurationDisposition,
    ConfigurationRecord,
)
from qtrad.domain.r2_features import R2FeatureDataset
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily, R2ExperimentConfig
from qtrad.ports.clock import Clock
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
SELECTION_MECHANICS_CONTRACT = "qtrad-r2-selection-mechanics-v1"
SYNTHETIC_SCENARIO_CONTRACT = "qtrad-r2-synthetic-scenario-v1"
_REQUIRED_FEATURE_SETS = frozenset({"L0", "L1", "P0", "P1"})


def runtime_identities() -> dict[str, str]:
    """Derive identities from the running interpreter and installed libraries."""
    application = f"qtrad-{__version__}"
    commit = os.environ.get("QTRAD_APPLICATION_COMMIT")
    if commit:
        application = f"{application}+{commit}"
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
        "holdout_excluded": True,
    }
    descriptor_id = sha256(canonical_bytes(semantic)).hexdigest()
    return {**semantic, "descriptor_id": descriptor_id}


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


def _dataset_payload(dataset: R2FeatureDataset, manifest: R2FeatureManifest) -> dict[str, object]:
    return {
        "contract": dataset.CONTRACT,
        "schema_version": dataset.SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "manifest": manifest.as_json(),
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
) -> ConfigurationRecord:
    semantic = {
        "model_family": family.value,
        "feature_set_id": feature_set_id,
        "forecast_dataset_id": forecast_dataset_id,
        "reason": reason,
    }
    return ConfigurationRecord(
        configuration_id=sha256(canonical_bytes(semantic)).hexdigest(),
        model_family=family,
        feature_set_id=feature_set_id,
        disposition=ConfigurationDisposition.EVALUATED,
        reason=reason,
        forecast_dataset_id=forecast_dataset_id,
        evaluated_model_manifest_id=None,
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


def build_oof_bundle(
    *,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    feature_manifest_paths: dict[str, Path],
    research_root: Path,
    clock: Clock,
    output: Path,
    run_kind: str = "REPRESENTATIVE",
) -> Path:
    """Build and persist the complete R2.C--F1 OOF run from authenticated children."""
    datasets, manifests = _load_feature_datasets(
        verified=verified,
        experiment=experiment,
        feature_manifest_paths=feature_manifest_paths,
        root=research_root,
        clock=clock,
    )
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
            )
            for dataset in local_datasets
        ),
        *(
            _configuration_record(
                family=model.model_family,
                feature_set_id=model.feature_set_id,
                forecast_dataset_id=model.forecasts.dataset_id,
                reason=f"pooled {model.feature_set_id} Ridge",
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

    register: dict[str, object] = {
        "contract": "qtrad-r2-evaluation-register-v1",
        "schema_version": 1,
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
        "local_comparator": local_comparator_ref.as_json(),
        "evaluated_models": [item.as_json() for item in evaluated_model_refs],
        "evaluation": evaluation_ref.as_json(),
        "forecast_manifests": [item.as_json() for item in forecast_manifest_refs],
        "coverage": [item.as_json() for item in coverage_refs],
        "configurations": [item.as_json() for item in configurations],
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


def selection_freeze(
    *,
    oof_bundle_path: Path,
    frozen_by: str,
    output: Path,
) -> Path:
    """Create a disposable, holdout-free selection-mechanics manifest."""
    if not frozen_by.strip():
        raise ValueError("frozen-by must be non-empty")
    bundle = verify_r2_oof_bundle(oof_bundle_path)
    semantic: dict[str, JsonValue] = {
        "contract": SELECTION_MECHANICS_CONTRACT,
        "schema_version": 1,
        "oof_bundle_id": bundle.bundle_id,
        "foundation_bundle_id": bundle.foundation_bundle_id,
        "experiment_configuration_id": bundle.experiment_configuration_id,
        "source_class": bundle.source_class.value,
        "evidence_class": bundle.evidence_class.value,
        "frozen_by": frozen_by,
        "disposition": "PENDING_R2_H_INTEGRATION",
        "holdout_excluded": True,
        "selected_configuration_ids": [],
    }
    selection_id = sha256(canonical_bytes(semantic)).hexdigest()
    atomic_create(output, canonical_bytes({**semantic, "selection_id": selection_id}))
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


def _synthetic_child(
    *,
    path: str,
    contract: str,
    identity_field: str,
    label: str,
    source_class: MarketDataSourceClass,
    evidence_class: EvidenceClass,
    values: Mapping[str, object] | None = None,
) -> tuple[ArtifactReference, dict[str, object]]:
    payload: dict[str, object] = {
        "contract": contract,
        "schema_version": 1,
        "source_class": source_class.value,
        "evidence_class": evidence_class.value,
        "holdout_excluded": True,
        "label": label,
    }
    if values is not None:
        payload.update(values)
    identity = sha256(
        canonical_bytes({"contract": contract, "label": label, "path": path})
    ).hexdigest()
    payload[identity_field] = identity
    return _child_reference(path, payload), payload


def build_software_bundle(
    *,
    representative_oof_bundle_path: Path,
    representative_selection_path: Path,
    output: Path,
) -> Path:
    """Build the top-level software bundle after independently verifying the representative run."""
    representative_oof = verify_r2_oof_bundle(representative_oof_bundle_path)
    if representative_oof.source_class is not MarketDataSourceClass.IG_NATIVE_CAPTURE:
        raise ValueError("representative software integration must use IG_NATIVE_CAPTURE")
    if representative_oof.evidence_class is not EvidenceClass.IMPLEMENTATION:
        raise ValueError("representative software integration must use implementation evidence")
    selection = _load_selection(representative_selection_path)
    if selection.get("contract") != SELECTION_MECHANICS_CONTRACT:
        raise ValueError("representative selection must be a selection-mechanics manifest")
    if selection.get("oof_bundle_id") != representative_oof.bundle_id:
        raise ValueError("representative selection does not bind the supplied OOF bundle")
    output.mkdir(parents=True, exist_ok=False)
    _copy_tree(representative_oof_bundle_path.parent, output / "representative" / "oof")
    representative_selection_target = output / "representative" / "selection.json"
    representative_selection_target.parent.mkdir(parents=True, exist_ok=True)
    representative_selection = _copy_file(
        representative_selection_path, representative_selection_target
    )
    identities = runtime_identities()

    synthetic_root = output / "synthetic" / "oof"
    synthetic_source = MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
    synthetic_evidence = EvidenceClass.IMPLEMENTATION
    synthetic_foundation_id = sha256(b"synthetic-foundation").hexdigest()
    synthetic_experiment_id = sha256(b"synthetic-experiment").hexdigest()
    synthetic_payload: dict[str, object] = {
        "contract": SYNTHETIC_SCENARIO_CONTRACT,
        "schema_version": 1,
        "scenario": "canonical-r2-synthetic-replay-v1",
        "foundation_bundle_id": synthetic_foundation_id,
        "experiment_configuration_id": synthetic_experiment_id,
        "feature_sets": sorted(_REQUIRED_FEATURE_SETS),
        "observations": [
            {"instrument": "synthetic-a", "decision_index": index, "return": index / 1000}
            for index in range(12)
        ],
        "folds": [
            {"fold": "synthetic-0", "train_end": 5, "validation_start": 6, "validation_end": 8},
            {"fold": "synthetic-1", "train_end": 8, "validation_start": 9, "validation_end": 11},
        ],
        "holdout_excluded": True,
        "source_class": synthetic_source.value,
        "evidence_class": synthetic_evidence.value,
    }
    synthetic_payload["scenario_id"] = sha256(canonical_bytes(synthetic_payload)).hexdigest()
    synthetic_children: dict[str, dict[str, object]] = {}
    feature_refs: list[ArtifactReference] = []
    preprocessing_refs: list[ArtifactReference] = []
    fit_refs: list[ArtifactReference] = []
    coverage_refs: list[ArtifactReference] = []
    forecast_manifest_refs: list[ArtifactReference] = []
    forecast_data_refs: list[ArtifactReference] = []
    stability_refs: list[ArtifactReference] = []
    for name in sorted(_REQUIRED_FEATURE_SETS):
        feature_ref, feature_payload = _synthetic_child(
            path=f"features/{name}.json",
            contract="qtrad-r2-features-v1",
            identity_field="dataset_id",
            label=name,
            source_class=synthetic_source,
            evidence_class=synthetic_evidence,
            values={"feature_set_name": name, "rows": [0.0, 0.001, 0.002]},
        )
        synthetic_children[feature_ref.path] = feature_payload
        feature_refs.append(feature_ref)
        selection_ref, selection_payload = _synthetic_child(
            path=f"preprocessing/{name}.json",
            contract="qtrad-r2-preprocessing-selection-v1",
            identity_field="selection_id",
            label=name,
            source_class=synthetic_source,
            evidence_class=synthetic_evidence,
            values={"feature_set_name": name, "selected_alpha": 1.0},
        )
        synthetic_children[selection_ref.path] = selection_payload
        preprocessing_refs.append(selection_ref)
        fit_ref, fit_payload = _synthetic_child(
            path=f"fits/{name}.json",
            contract="qtrad-r2-fold-fit-v1",
            identity_field="artifact_id",
            label=name,
            source_class=synthetic_source,
            evidence_class=synthetic_evidence,
            values={"feature_set_name": name, "disposition": "FITTED", "fit_rows": 8},
        )
        synthetic_children[fit_ref.path] = fit_payload
        fit_refs.append(fit_ref)
        coverage_ref, coverage_payload = _synthetic_child(
            path=f"coverage/{name}.json",
            contract="qtrad-r2-forecast-coverage-v1",
            identity_field="dataset_id",
            label=name,
            source_class=synthetic_source,
            evidence_class=synthetic_evidence,
            values={
                "feature_set_name": name,
                "expected_opportunities": 3,
                "covered_opportunities": 3,
            },
        )
        synthetic_children[coverage_ref.path] = coverage_payload
        coverage_refs.append(coverage_ref)
        stability_ref, stability_payload = _synthetic_child(
            path=f"evaluation/{name}-stability.json",
            contract="qtrad-r2-coefficient-stability-v1",
            identity_field="summary_id",
            label=name,
            source_class=synthetic_source,
            evidence_class=synthetic_evidence,
            values={"feature_set_name": name, "ready_fit_count": 2, "expected_fit_count": 2},
        )
        synthetic_children[stability_ref.path] = stability_payload
        stability_refs.append(stability_ref)
    forecast_data_ref, forecast_data_payload = _synthetic_child(
        path="forecasts/P1.data.json",
        contract="qtrad-research-forecasts-v1",
        identity_field="dataset_id",
        label="P1",
        source_class=synthetic_source,
        evidence_class=synthetic_evidence,
        values={"rows": [0.001, 0.002, 0.003], "target_dataset_id": synthetic_foundation_id},
    )
    synthetic_children[forecast_data_ref.path] = forecast_data_payload
    forecast_data_refs.append(forecast_data_ref)
    forecast_manifest = R2ForecastManifest.create(
        forecast_dataset_id=str(forecast_data_payload["dataset_id"]),
        experiment_configuration_id=synthetic_experiment_id,
        source_class=synthetic_source,
        evidence_class=synthetic_evidence,
        forecast_child=forecast_data_ref,
    )
    forecast_manifest_payload = cast(dict[str, object], forecast_manifest.as_json())
    forecast_manifest_ref = _child_reference(
        "forecasts/P1.manifest.json", forecast_manifest_payload
    )
    synthetic_children[forecast_manifest_ref.path] = forecast_manifest_payload
    forecast_manifest_refs.append(forecast_manifest_ref)
    evaluation_register: dict[str, object] = {
        "contract": "qtrad-r2-evaluation-register-v1",
        "schema_version": 1,
        "source_class": synthetic_source.value,
        "evidence_class": synthetic_evidence.value,
        "configurations": [
            {
                "configuration_id": sha256(name.encode()).hexdigest(),
                "feature_set_name": name,
                "disposition": "EVALUATED",
            }
            for name in sorted(_REQUIRED_FEATURE_SETS)
        ],
        "stability_summary_ids": [ref.semantic_id for ref in stability_refs],
        "holdout_excluded": True,
    }
    evaluation_register["report_id"] = sha256(canonical_bytes(evaluation_register)).hexdigest()
    register_path = "evaluation/register.json"
    synthetic_children[register_path] = evaluation_register
    evaluation_ref = _child_reference(register_path, evaluation_register)
    scenario_path = "evaluation/synthetic-scenario.json"
    synthetic_scenario_ref = _child_reference(scenario_path, synthetic_payload)
    synthetic_children[scenario_path] = synthetic_payload
    synthetic_descriptor: dict[str, object] = {
        "contract": OOF_DESCRIPTOR_CONTRACT,
        "schema_version": 1,
        "foundation_bundle_id": synthetic_foundation_id,
        "experiment_configuration_id": synthetic_experiment_id,
        "source_class": synthetic_source.value,
        "evidence_class": synthetic_evidence.value,
        "feature_sets": sorted(_REQUIRED_FEATURE_SETS),
        "run_kind": "SYNTHETIC",
        "fit_count": len(fit_refs),
        "forecast_manifest_count": len(forecast_manifest_refs),
        "coverage_count": len(coverage_refs),
        "evaluation_report_id": str(evaluation_register["report_id"]),
        **{
            key: identities[key]
            for key in (
                "application_identity",
                "python_identity",
                "numpy_identity",
                "sklearn_identity",
            )
        },
        "holdout_excluded": True,
    }
    synthetic_descriptor["descriptor_id"] = sha256(
        canonical_bytes(synthetic_descriptor)
    ).hexdigest()
    descriptor_path = "evaluation/run-descriptor.json"
    synthetic_children[descriptor_path] = synthetic_descriptor
    synthetic_descriptor_ref = _descriptor_reference(
        output=synthetic_root,
        relative_path=descriptor_path,
        payload=synthetic_descriptor,
    )
    synthetic_bundle = R2OofBundle.create(
        foundation_bundle_id=synthetic_foundation_id,
        experiment_configuration_id=synthetic_experiment_id,
        source_class=synthetic_source,
        evidence_class=synthetic_evidence,
        feature_children=tuple(feature_refs),
        preprocessing_children=tuple(preprocessing_refs),
        fit_children=tuple(fit_refs),
        forecast_manifests=tuple(forecast_manifest_refs),
        coverage_children=tuple(coverage_refs),
        evaluation_children=(
            *stability_refs,
            evaluation_ref,
            synthetic_scenario_ref,
            synthetic_descriptor_ref,
            *forecast_data_refs,
        ),
    )
    write_r2_oof_bundle(synthetic_root, synthetic_bundle, synthetic_children)
    verify_r2_oof_bundle(synthetic_root / "manifest.json")

    synthetic_selection_payload: dict[str, object] = {
        "contract": SELECTION_MECHANICS_CONTRACT,
        "schema_version": 1,
        "oof_bundle_id": synthetic_bundle.bundle_id,
        "foundation_bundle_id": synthetic_bundle.foundation_bundle_id,
        "experiment_configuration_id": synthetic_bundle.experiment_configuration_id,
        "source_class": synthetic_bundle.source_class.value,
        "evidence_class": synthetic_bundle.evidence_class.value,
        "frozen_by": "software-verification",
        "disposition": "PENDING_R2_H_INTEGRATION",
        "holdout_excluded": True,
        "selected_configuration_ids": [],
    }
    synthetic_selection_payload["selection_id"] = sha256(
        canonical_bytes(synthetic_selection_payload)
    ).hexdigest()
    synthetic_selection_path = output / "synthetic" / "selection.json"
    atomic_create(synthetic_selection_path, canonical_bytes(synthetic_selection_payload))
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


def verify_software_bundle(path: Path) -> R2SoftwareVerificationBundle:
    """Verify both nested OOF integrations and their holdout-free selections."""
    software = verify_r2_software_bundle(path)
    identities = runtime_identities()
    for key, expected in identities.items():
        if getattr(software, key) != expected:
            raise ValueError(f"software bundle {key} differs from the running environment")
    root = path.parent
    synthetic = root / software.synthetic_oof_bundle.path
    representative = root / software.representative_oof_bundle.path
    synthetic_oof = verify_r2_oof_bundle(synthetic)
    representative_oof = verify_r2_oof_bundle(representative)
    if synthetic_oof.source_class is not MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
        raise ValueError("synthetic software integration must use IBKR_HISTORICAL_RESEARCH")
    if synthetic_oof.evidence_class is not EvidenceClass.IMPLEMENTATION:
        raise ValueError("synthetic software integration must use implementation evidence")
    for reference, oof in (
        (software.synthetic_selection, synthetic_oof),
        (software.representative_selection, representative_oof),
    ):
        payload = _load_selection(root / reference.path)
        if payload.get("contract") != reference.contract:
            raise ValueError("software selection child contract mismatch")
        if payload.get("oof_bundle_id") != oof.bundle_id:
            raise ValueError("software selection does not bind its OOF bundle")
        if payload.get("source_class") != oof.source_class.value:
            raise ValueError("software selection source class differs from its OOF bundle")
        if payload.get("evidence_class") != oof.evidence_class.value:
            raise ValueError("software selection evidence class differs from its OOF bundle")
        if payload.get("holdout_excluded") is not True:
            raise ValueError("software selection must exclude the locked holdout")
    return software


def load_experiment_and_feature_paths(
    *,
    experiment_path: Path,
    feature_arguments: list[str],
) -> tuple[R2ExperimentConfig, dict[str, Path]]:
    experiment = load_r2_experiment(experiment_path)
    return experiment, parse_feature_manifest_arguments(feature_arguments)
