"""Persistence and independent verification for thin R1 foundation bundles."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from qtrad.adapters.parquet.foundation import (
    FoundationChildManifest,
    ParquetFoundationArtifactStore,
)
from qtrad.adapters.parquet.observations import ObservationManifest, ParquetObservationStore
from qtrad.application.foundation_bundle import (
    build_foundation_bundle,
    verify_foundation_children,
)
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.folds import Fold, FoldDataset
from qtrad.domain.forecasts import ForecastDataset, ForecastRow, ReturnUnit
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    FoundationConfig,
    HorizonCoverageSummary,
    InstrumentRole,
    PanelAuditDisposition,
    PanelDataset,
    PanelRow,
    PanelStatus,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.foundation_bundle import (
    AVAILABILITY_EVIDENCE_CONTRACT,
    ArtifactReference,
    FoundationBundle,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import DataGap, DataQuality, PriceBasis
from qtrad.domain.research import ObservationDataset
from qtrad.domain.time import require_utc
from qtrad.ports.clock import Clock

_MAX_BUNDLE_BYTES = 4 * 1024 * 1024
_CONFIGURATION_KEYS = {
    "contract",
    "schema_version",
    "name",
    "observation_dataset_id",
    "ordered_instruments",
    "instrument_roles",
    "range_start",
    "range_end",
    "grid_resolution_seconds",
    "availability_basis",
    "feature_lag_policy",
    "feature_lag_calibration_range",
    "feature_lag_percentile",
    "feature_lag_safety_margin_seconds",
    "selected_feature_lag_seconds",
    "target_horizons_seconds",
    "primary_vertical_horizon_seconds",
    "target_revision_delay_seconds",
    "target_revision_policy",
    "required_feature_bases",
    "target_basis",
    "fold_policy",
    "holdout_range",
    "embargo_seconds",
    "minimum_training_duration_seconds",
    "minimum_validation_duration_seconds",
}


@dataclass(frozen=True, slots=True)
class ObservationBuildEvidence:
    gaps: tuple[DataGap, ...]
    source_active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]]
    payload: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class VerifiedFoundationBundle:
    bundle: FoundationBundle
    configuration: FoundationConfig
    observations: ObservationDataset
    panel: PanelDataset
    targets: TargetDataset
    folds: FoldDataset
    forecasts: ForecastDataset
    availability_evidence: Mapping[str, JsonValue]


def write_foundation_bundle(path: Path, bundle: FoundationBundle) -> None:
    """Write one bounded thin bundle without replacing evidence."""

    if path.is_symlink() or path.exists():
        raise ValueError("foundation bundle output must be a new regular file")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(bundle.as_json(), sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > _MAX_BUNDLE_BYTES:
        raise ValueError("foundation bundle exceeds the 4 MiB limit")
    with path.open("x", encoding="utf-8") as output:
        output.write(encoded)


def load_foundation_bundle(path: Path) -> FoundationBundle:
    """Load only the bounded top-level references; child verification is separate."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("foundation bundle must be a regular non-symlink file")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_BUNDLE_BYTES:
        raise ValueError("foundation bundle exceeds the 4 MiB limit")
    payload = _mapping(json.loads(encoded))
    expected = {
        "contract",
        "schema_version",
        "children",
        "ordered_instruments",
        "range_start",
        "range_end",
        "coverage",
        "build_summary",
        "bundle_id",
    }
    if set(payload) != expected:
        raise ValueError("foundation bundle has an unexpected schema")
    if payload["contract"] != FoundationBundle.CONTRACT or payload["schema_version"] != 1:
        raise ValueError("foundation bundle contract is unsupported")
    children = _mapping(payload["children"])
    child_names = {
        "configuration",
        "observations",
        "availability",
        "panel",
        "targets",
        "folds",
        "forecasts",
    }
    if set(children) != child_names:
        raise ValueError("foundation bundle child set is incomplete")
    return FoundationBundle(
        configuration=_reference("configuration", children["configuration"]),
        observations=_reference("observations", children["observations"]),
        availability=_reference("availability", children["availability"]),
        panel=_reference("panel", children["panel"]),
        targets=_reference("targets", children["targets"]),
        folds=_reference("folds", children["folds"]),
        forecasts=_reference("forecasts", children["forecasts"]),
        ordered_instruments=tuple(
            _text(item) for item in _sequence(payload["ordered_instruments"])
        ),
        range_start=_datetime(payload["range_start"]),
        range_end=_datetime(payload["range_end"]),
        coverage=tuple(_coverage(_mapping(item)) for item in _sequence(payload["coverage"])),
        build_summary=cast(Mapping[str, JsonValue], _mapping(payload["build_summary"])),
        bundle_id=_text(payload["bundle_id"]),
    )


def load_foundation_config(path: Path) -> FoundationConfig:
    """Load a strict standalone foundation configuration JSON document."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("foundation configuration must be a regular non-symlink file")
    return decode_foundation_config(_mapping(json.loads(path.read_text(encoding="utf-8"))))


def decode_foundation_config(payload: Mapping[str, object]) -> FoundationConfig:
    if set(payload) != _CONFIGURATION_KEYS:
        raise ValueError("foundation configuration has unknown or missing fields")
    if payload["contract"] != FoundationConfig.CONTRACT:
        raise ValueError("foundation configuration contract is unsupported")
    calibration = _sequence(payload["feature_lag_calibration_range"])
    holdout = _sequence(payload["holdout_range"])
    if len(calibration) != 2 or len(holdout) != 2:
        raise ValueError("foundation configuration ranges require exactly two timestamps")
    return FoundationConfig(
        name=_text(payload["name"]),
        schema_version=_int(payload["schema_version"]),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        ordered_instruments=tuple(
            _text(item) for item in _sequence(payload["ordered_instruments"])
        ),
        instrument_roles={
            _text(key): InstrumentRole(_text(value))
            for key, value in _mapping(payload["instrument_roles"]).items()
        },
        range_start=_datetime(payload["range_start"]),
        range_end=_datetime(payload["range_end"]),
        grid_resolution=_duration(payload["grid_resolution_seconds"]),
        availability_basis=AvailabilityBasis(_text(payload["availability_basis"])),
        feature_lag_policy=_text(payload["feature_lag_policy"]),
        feature_lag_calibration_range=(_datetime(calibration[0]), _datetime(calibration[1])),
        feature_lag_percentile=_float(payload["feature_lag_percentile"]),
        feature_lag_safety_margin=_duration(payload["feature_lag_safety_margin_seconds"]),
        selected_feature_lag=_duration(payload["selected_feature_lag_seconds"]),
        target_horizons=tuple(
            _duration(item) for item in _sequence(payload["target_horizons_seconds"])
        ),
        primary_vertical_horizon=_duration(payload["primary_vertical_horizon_seconds"]),
        target_revision_delay=_duration(payload["target_revision_delay_seconds"]),
        target_revision_policy=_text(payload["target_revision_policy"]),
        required_feature_bases=tuple(
            PriceBasis(_text(item)) for item in _sequence(payload["required_feature_bases"])
        ),
        target_basis=PriceBasis(_text(payload["target_basis"])),
        fold_policy=_text(payload["fold_policy"]),
        holdout_range=(_datetime(holdout[0]), _datetime(holdout[1])),
        embargo=_duration(payload["embargo_seconds"]),
        minimum_training_duration=_duration(payload["minimum_training_duration_seconds"]),
        minimum_validation_duration=_duration(payload["minimum_validation_duration_seconds"]),
    )


def load_observation_build_evidence(manifest: ObservationManifest) -> ObservationBuildEvidence:
    """Decode bounded causal gap/activity evidence authenticated by the observation manifest."""

    payload = manifest.build_evidence
    required = {
        "availability_delay_report",
        "revision_delay_report",
        "data_gaps",
        "source_active_intervals",
        "lineage_summary",
        "observation_bounds",
    }
    if set(payload) != required:
        raise ValueError("observation build evidence is incomplete or unexpected")
    bounds = _mapping(payload["observation_bounds"])
    if set(bounds) != {"interval_start", "interval_end"}:
        raise ValueError("observation build bounds have an unexpected schema")
    if (
        bounds["interval_start"] != manifest.configuration["interval_start"]
        or bounds["interval_end"] != manifest.configuration["interval_end"]
    ):
        raise ValueError("observation build bounds differ from dataset configuration")
    gaps = tuple(_data_gap(_mapping(item)) for item in _sequence(payload["data_gaps"]))
    intervals: dict[str, tuple[tuple[datetime, datetime], ...]] = {}
    for instrument_id, raw_intervals in _mapping(payload["source_active_intervals"]).items():
        decoded: list[tuple[datetime, datetime]] = []
        for raw in _sequence(raw_intervals):
            pair = _sequence(raw)
            if len(pair) != 2:
                raise ValueError("source-active interval must contain exactly two timestamps")
            start, end = _datetime(pair[0]), _datetime(pair[1])
            if end <= start:
                raise ValueError("source-active interval must be positive")
            decoded.append((start, end))
        intervals[instrument_id] = tuple(decoded)
    return ObservationBuildEvidence(
        gaps=gaps,
        source_active_intervals=intervals,
        payload=payload,
    )


async def persist_foundation_bundle(
    *,
    root: Path,
    clock: Clock,
    output_path: Path,
    observation_manifest: ObservationManifest,
    configuration: FoundationConfig,
    observations: ObservationDataset,
    panel: PanelDataset,
    targets: TargetDataset,
    folds: FoldDataset,
    forecasts: ForecastDataset,
    availability_evidence: Mapping[str, JsonValue],
    application_version: str,
    image_identity: str,
) -> FoundationBundle:
    """Persist each child independently and write a thin bundle of references."""

    store = ParquetFoundationArtifactStore(root, clock)
    config_manifest = await store.write(
        kind="configuration",
        contract=FoundationConfig.CONTRACT,
        schema_version=1,
        dataset_id=configuration.configuration_id,
        rows=(configuration.as_json(),),
        lineage={"observation_dataset_id": observations.dataset_id},
        application_version=application_version,
        image_identity=image_identity,
    )
    availability_id = _hash_json(
        {
            "contract": AVAILABILITY_EVIDENCE_CONTRACT,
            "observation_dataset_id": observations.dataset_id,
            "evidence": availability_evidence,
        }
    )
    availability_manifest = await store.write(
        kind="availability",
        contract=AVAILABILITY_EVIDENCE_CONTRACT,
        schema_version=1,
        dataset_id=availability_id,
        rows=(availability_evidence,),
        lineage={
            "observation_dataset_id": observations.dataset_id,
            "observation_manifest_id": observation_manifest.manifest_id,
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    panel_manifest = await store.write(
        kind="panel",
        contract=PanelRow.CONTRACT,
        schema_version=1,
        dataset_id=panel.dataset_id,
        rows=tuple(row.as_json() for row in panel.rows),
        lineage={
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    target_manifest = await store.write(
        kind="targets",
        contract=TargetRow.CONTRACT,
        schema_version=1,
        dataset_id=targets.dataset_id,
        rows=tuple(row.as_json() for row in targets.rows),
        lineage={
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    fold_manifest = await store.write(
        kind="folds",
        contract=FoldDataset.CONTRACT,
        schema_version=1,
        dataset_id=folds.dataset_id,
        rows=tuple(fold.as_json() for fold in folds.folds),
        lineage={
            "target_dataset_id": targets.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    forecast_manifest = await store.write(
        kind="forecasts",
        contract=ForecastDataset.CONTRACT,
        schema_version=1,
        dataset_id=forecasts.dataset_id,
        rows=tuple(row.as_json() for row in forecasts.rows),
        lineage={
            "observation_dataset_id": observations.dataset_id,
            "panel_dataset_id": panel.dataset_id,
            "target_dataset_id": targets.dataset_id,
            "fold_dataset_id": folds.dataset_id,
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    build_summary: dict[str, JsonValue] = {
        "application_version": application_version,
        "image_identity": image_identity,
        "row_counts": {
            "observations": len(observations.rows),
            "panel": len(panel.rows),
            "targets": len(targets.rows),
            "folds": len(folds.folds),
            "forecasts": len(forecasts.rows),
        },
    }
    bundle = build_foundation_bundle(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        configuration_reference=_child_reference("configuration", config_manifest),
        observation_reference=_observation_reference(observation_manifest),
        availability_reference=_child_reference("availability", availability_manifest),
        panel_reference=_child_reference("panel", panel_manifest),
        target_reference=_child_reference("targets", target_manifest),
        fold_reference=_child_reference("folds", fold_manifest),
        forecast_reference=_child_reference("forecasts", forecast_manifest),
        build_summary=build_summary,
    )
    write_foundation_bundle(output_path, bundle)
    await verify_foundation_bundle(root=root, bundle_path=output_path, clock=clock)
    return bundle


async def verify_foundation_bundle(
    *, root: Path, bundle_path: Path, clock: Clock
) -> VerifiedFoundationBundle:
    """Verify child bytes, manifests, semantic identities and all cross-references."""

    bundle = load_foundation_bundle(bundle_path)
    observation_store = ParquetObservationStore(root, clock)
    observation_manifest = await observation_store.verify(bundle.observations.manifest_id)
    _verify_observation_reference(bundle.observations, observation_manifest)
    if observation_manifest.source_snapshot.get("kind") != "verified-capture-snapshot":
        raise ValueError("foundation observations lack verified snapshot/import evidence")
    if not observation_manifest.source_snapshot.get("import_sha256"):
        raise ValueError("foundation observations lack snapshot import identity")
    observations = await observation_store.read_observations(observation_manifest.manifest_id)
    evidence = load_observation_build_evidence(observation_manifest)

    store = ParquetFoundationArtifactStore(root, clock)
    manifests: dict[str, FoundationChildManifest] = {}
    rows: dict[str, tuple[dict[str, JsonValue], ...]] = {}
    for reference in (
        bundle.configuration,
        bundle.availability,
        bundle.panel,
        bundle.targets,
        bundle.folds,
        bundle.forecasts,
    ):
        manifest = await store.verify(reference.manifest_id)
        _verify_child_reference(reference, manifest)
        manifests[reference.name] = manifest
        rows[reference.name] = await store.read_rows(reference.manifest_id)

    configuration = decode_foundation_config(_single_row(rows, "configuration"))
    availability = cast(Mapping[str, JsonValue], _single_row(rows, "availability"))
    if availability != evidence.payload:
        raise ValueError("availability child differs from observation build evidence")
    expected_availability_id = _hash_json(
        {
            "contract": AVAILABILITY_EVIDENCE_CONTRACT,
            "observation_dataset_id": observations.dataset_id,
            "evidence": availability,
        }
    )
    if manifests["availability"].dataset_id != expected_availability_id:
        raise ValueError("availability child semantic identity is invalid")

    _require_lineage(
        manifests["configuration"],
        {"observation_dataset_id": observations.dataset_id},
    )
    _require_lineage(
        manifests["availability"],
        {
            "observation_dataset_id": observations.dataset_id,
            "observation_manifest_id": observation_manifest.manifest_id,
        },
    )
    _require_lineage(
        manifests["panel"],
        {
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
    )
    _require_lineage(
        manifests["targets"],
        {
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
    )
    _require_lineage(
        manifests["folds"],
        {
            "target_dataset_id": manifests["targets"].dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
    )
    _require_lineage(
        manifests["forecasts"],
        {
            "observation_dataset_id": observations.dataset_id,
            "panel_dataset_id": manifests["panel"].dataset_id,
            "target_dataset_id": manifests["targets"].dataset_id,
            "fold_dataset_id": manifests["folds"].dataset_id,
        },
    )
    build_summary = _mapping(bundle.build_summary)
    if set(build_summary) != {"application_version", "image_identity", "row_counts"}:
        raise ValueError("foundation build summary has an unexpected schema")
    application_version = _text(build_summary["application_version"])
    image_identity = _text(build_summary["image_identity"])
    expected_row_counts: dict[str, JsonValue] = {
        "observations": bundle.observations.row_count,
        "panel": bundle.panel.row_count,
        "targets": bundle.targets.row_count,
        "folds": bundle.folds.row_count,
        "forecasts": bundle.forecasts.row_count,
    }
    if _mapping(build_summary["row_counts"]) != expected_row_counts:
        raise ValueError("foundation build summary row counts are invalid")
    for manifest in manifests.values():
        if (
            manifest.application_version != application_version
            or manifest.image_identity != image_identity
        ):
            raise ValueError("foundation child build identity differs from the bundle")
    panel = PanelDataset(
        rows=tuple(_panel_row(row) for row in rows["panel"]),
        observation_dataset_id=_lineage_text(manifests["panel"], "observation_dataset_id"),
        foundation_configuration_id=_lineage_text(
            manifests["panel"], "foundation_configuration_id"
        ),
        dataset_id=manifests["panel"].dataset_id,
    )
    targets = TargetDataset(
        rows=tuple(_target(row) for row in rows["targets"]),
        observation_dataset_id=_lineage_text(manifests["targets"], "observation_dataset_id"),
        foundation_configuration_id=_lineage_text(
            manifests["targets"], "foundation_configuration_id"
        ),
        dataset_id=manifests["targets"].dataset_id,
    )
    folds = FoldDataset(
        folds=tuple(_fold(row) for row in rows["folds"]),
        target_dataset_id=_lineage_text(manifests["folds"], "target_dataset_id"),
        foundation_configuration_id=_lineage_text(
            manifests["folds"], "foundation_configuration_id"
        ),
        dataset_id=manifests["folds"].dataset_id,
    )
    forecasts = ForecastDataset(
        rows=tuple(_forecast(row) for row in rows["forecasts"]),
        observation_dataset_id=_lineage_text(manifests["forecasts"], "observation_dataset_id"),
        panel_dataset_id=_lineage_text(manifests["forecasts"], "panel_dataset_id"),
        target_dataset_id=_lineage_text(manifests["forecasts"], "target_dataset_id"),
        fold_dataset_id=_lineage_text(manifests["forecasts"], "fold_dataset_id"),
        dataset_id=manifests["forecasts"].dataset_id,
    )
    verify_foundation_children(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        coverage=bundle.coverage,
    )
    if (
        bundle.configuration.dataset_id != configuration.configuration_id
        or bundle.ordered_instruments != configuration.ordered_instruments
        or bundle.range_start != configuration.range_start
        or bundle.range_end != configuration.range_end
    ):
        raise ValueError("foundation bundle metadata differs from its configuration")
    return VerifiedFoundationBundle(
        bundle=bundle,
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        availability_evidence=availability,
    )


def _reference(name: str, value: object) -> ArtifactReference:
    payload = _mapping(value)
    expected = {
        "name",
        "contract",
        "schema_version",
        "dataset_id",
        "manifest_id",
        "manifest_sha256",
        "manifest_path",
        "row_count",
    }
    if set(payload) != expected or payload["name"] != name:
        raise ValueError("foundation child reference has an unexpected schema")
    return ArtifactReference(
        name=name,
        contract=_text(payload["contract"]),
        schema_version=_int(payload["schema_version"]),
        dataset_id=_text(payload["dataset_id"]),
        manifest_id=_text(payload["manifest_id"]),
        manifest_sha256=_text(payload["manifest_sha256"]),
        manifest_path=_text(payload["manifest_path"]),
        row_count=_int(payload["row_count"]),
    )


def _child_reference(name: str, manifest: FoundationChildManifest) -> ArtifactReference:
    return ArtifactReference(
        name=name,
        contract=manifest.contract,
        schema_version=manifest.schema_version,
        dataset_id=manifest.dataset_id,
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.manifest_sha256,
        manifest_path=manifest.manifest_path,
        row_count=manifest.row_count,
    )


def _observation_reference(manifest: ObservationManifest) -> ArtifactReference:
    return ArtifactReference(
        name="observations",
        contract=manifest.contract,
        schema_version=manifest.schema_version,
        dataset_id=manifest.dataset_id,
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.manifest_sha256,
        manifest_path=f"manifests/{manifest.manifest_id}.json",
        row_count=manifest.row_count,
    )


def _verify_child_reference(
    reference: ArtifactReference, manifest: FoundationChildManifest
) -> None:
    if reference != _child_reference(reference.name, manifest):
        raise ValueError(f"foundation {reference.name} reference differs from its manifest")


def _verify_observation_reference(
    reference: ArtifactReference, manifest: ObservationManifest
) -> None:
    if reference != _observation_reference(manifest):
        raise ValueError("foundation observation reference differs from its manifest")


def _single_row(
    rows: Mapping[str, tuple[dict[str, JsonValue], ...]], name: str
) -> dict[str, JsonValue]:
    values = rows[name]
    if len(values) != 1:
        raise ValueError(f"foundation {name} child must contain exactly one row")
    return values[0]


def _lineage_text(manifest: FoundationChildManifest, key: str) -> str:
    return _text(manifest.lineage[key])


def _require_lineage(manifest: FoundationChildManifest, expected: Mapping[str, JsonValue]) -> None:
    if manifest.lineage != expected:
        raise ValueError(f"foundation {manifest.kind} child lineage is invalid")


def _panel_row(payload: Mapping[str, object]) -> PanelRow:
    return PanelRow(
        decision_time=_datetime(payload["decision_time"]),
        instrument_id=_text(payload["instrument_id"]),
        basis=PriceBasis(_text(payload["basis"])),
        feature_data_asof=_datetime(payload["feature_data_asof"]),
        latest_feature_bar_end=_datetime(payload["latest_feature_bar_end"]),
        status=PanelStatus(_text(payload["status"])),
        audit_disposition=(
            None
            if payload["audit_disposition"] is None
            else PanelAuditDisposition(_text(payload["audit_disposition"]))
        ),
        selected_event_id=_uuid(payload["selected_event_id"]),
        selected_stream_version=_optional_int(payload["selected_stream_version"]),
        selected_global_position=_optional_int(payload["selected_global_position"]),
        selected_availability_time=_optional_datetime(payload["selected_availability_time"]),
        selected_revision=_optional_int(payload["selected_revision"]),
        interval_start=_optional_datetime(payload["interval_start"]),
        interval_end=_optional_datetime(payload["interval_end"]),
        open=_optional_decimal(payload["open"]),
        high=_optional_decimal(payload["high"]),
        low=_optional_decimal(payload["low"]),
        close=_optional_decimal(payload["close"]),
        sample_count=_optional_int(payload["sample_count"]),
        quality=(None if payload["quality"] is None else DataQuality(_text(payload["quality"]))),
    )


def _target(payload: Mapping[str, object]) -> TargetRow:
    return TargetRow(
        instrument_id=_text(payload["instrument_id"]),
        decision_time=_datetime(payload["decision_time"]),
        horizon=_duration(payload["horizon_seconds"]),
        target_basis=PriceBasis(_text(payload["target_basis"])),
        target_revision_policy=_text(payload["target_revision_policy"]),
        target_start_time=_datetime(payload["target_start_time"]),
        target_end_time=_datetime(payload["target_end_time"]),
        target_freeze_at=_datetime(payload["target_freeze_at"]),
        target_available_at=_datetime(payload["target_available_at"]),
        label_start_close=_optional_decimal(payload["label_start_close"]),
        label_end_close=_optional_decimal(payload["label_end_close"]),
        log_return=_optional_float(payload["log_return"]),
        return_disposition=ReturnDisposition(_text(payload["return_disposition"])),
        start_event_id=_uuid(payload["start_event_id"]),
        end_event_id=_uuid(payload["end_event_id"]),
        upper_log_excursion=_optional_float(payload["upper_log_excursion"]),
        lower_log_excursion=_optional_float(payload["lower_log_excursion"]),
        excursion_disposition=ExcursionDisposition(_text(payload["excursion_disposition"])),
    )


def _fold(payload: Mapping[str, object]) -> Fold:
    return Fold(
        fold_id=_text(payload["fold_id"]),
        training_start=_datetime(payload["training_start"]),
        training_cutoff=_datetime(payload["training_cutoff"]),
        validation_start=_datetime(payload["validation_start"]),
        validation_end=_datetime(payload["validation_end"]),
        embargo_end=_datetime(payload["embargo_end"]),
        training_target_ids=tuple(
            _text(item) for item in _sequence(payload["training_target_ids"])
        ),
        validation_target_ids=tuple(
            _text(item) for item in _sequence(payload["validation_target_ids"])
        ),
        holdout_excluded=_bool(payload["holdout_excluded"]),
        membership_hash=_text(payload["membership_hash"]),
    )


def _forecast(payload: Mapping[str, object]) -> ForecastRow:
    return ForecastRow(
        forecast_id=_text(payload["forecast_id"]),
        instrument_id=_text(payload["instrument_id"]),
        decision_time=_datetime(payload["decision_time"]),
        horizon=_duration(payload["horizon_seconds"]),
        expected_return=_float(payload["expected_return"]),
        return_unit=ReturnUnit(_text(payload["return_unit"])),
        feature_data_asof=_datetime(payload["feature_data_asof"]),
        training_cutoff=_datetime(payload["training_cutoff"]),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        panel_dataset_id=_text(payload["panel_dataset_id"]),
        target_dataset_id=_text(payload["target_dataset_id"]),
        target_id=_text(payload["target_id"]),
        fold_dataset_id=_text(payload["fold_dataset_id"]),
        experiment_id=_text(payload["experiment_id"]),
        fold_id=_text(payload["fold_id"]),
        model_id=_text(payload["model_id"]),
        model_contract=_text(payload["model_contract"]),
    )


def _coverage(payload: Mapping[str, object]) -> HorizonCoverageSummary:
    return HorizonCoverageSummary(
        horizon=_duration(payload["horizon_seconds"]),
        total_target_count=_int(payload["total_target_count"]),
        valid_return_count=_int(payload["valid_return_count"]),
        valid_excursion_count=_int(payload["valid_excursion_count"]),
        unavailable_by_freeze_count=_int(payload["unavailable_by_freeze_count"]),
        return_coverage=_float(payload["return_coverage"]),
        excursion_coverage=_float(payload["excursion_coverage"]),
        return_disposition_counts=_counts(payload["return_disposition_counts"]),
        excursion_disposition_counts=_counts(payload["excursion_disposition_counts"]),
    )


def _data_gap(payload: Mapping[str, object]) -> DataGap:
    expected = {
        "instrument_id",
        "interval_start",
        "interval_end",
        "reason",
        "detected_at",
        "repaired_at",
    }
    if set(payload) != expected:
        raise ValueError("observation data-gap evidence has an unexpected schema")
    return DataGap(
        instrument_id=InstrumentId(_text(payload["instrument_id"])),
        interval_start=_datetime(payload["interval_start"]),
        interval_end=_datetime(payload["interval_end"]),
        reason=_text(payload["reason"]),
        detected_at=_datetime(payload["detected_at"]),
        repaired_at=_optional_datetime(payload["repaired_at"]),
    )


def _counts(value: object) -> tuple[tuple[str, int], ...]:
    counts: list[tuple[str, int]] = []
    for item in _sequence(value):
        pair = _sequence(item)
        if len(pair) != 2:
            raise ValueError("foundation bundle disposition count is invalid")
        counts.append((_text(pair[0]), _int(pair[1])))
    return tuple(counts)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("expected an object with string keys")
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected an array")
    return cast(list[object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("expected non-empty text")
    return value


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    require_utc(parsed, "foundation timestamp")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _duration(value: object) -> timedelta:
    return timedelta(seconds=_float(value))


def _decimal(value: object) -> Decimal:
    return Decimal(_text(value))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _uuid(value: object) -> UUID | None:
    return None if value is None else UUID(_text(value))


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected an integer")
    return value


def _optional_int(value: object) -> int | None:
    return None if value is None else _int(value)


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("expected a number")
    return float(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else _float(value)


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("expected a boolean")
    return value


def _hash_json(value: object) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
