"""Persistence and independent verification for thin R1 foundation bundles."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from pathlib import Path
from typing import cast
from uuid import UUID

from qtrad.adapters.parquet.foundation import (
    FoundationChildManifest,
    ParquetFoundationArtifactStore,
)
from qtrad.adapters.parquet.observations import (
    ObservationManifest,
    ParquetObservationStore,
    _observation_from_row,
)
from qtrad.application.foundation import (
    build_asof_panel,
    build_frozen_targets,
    summarise_horizon_coverage,
)
from qtrad.application.foundation_bundle import (
    build_foundation_bundle,
    verify_foundation_children,
)
from qtrad.application.walk_forward import build_expanding_folds, build_zero_return_forecasts
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
from qtrad.domain.market_data import DataGap, DataQuality, MarketDataSourceClass, PriceBasis
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
from qtrad.domain.research import (
    AvailabilityDelayReport,
    ObservationDataset,
    RevisionDelayReport,
    build_availability_delay_report,
    build_revision_delay_report,
)
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
    "target_revision_policy_reason",
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
    availability_report: AvailabilityDelayReport
    revision_report: RevisionDelayReport
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
    source_active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]]
    availability_evidence: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class OutcomeBlindVerifiedFoundationBundle:
    """R1 evidence verified without decoding the outcome-bearing target child."""

    bundle: FoundationBundle
    configuration: FoundationConfig
    observations: ObservationDataset
    panel: PanelDataset
    targets: R2OutcomeBlindTargetView
    folds: FoldDataset
    source_active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]]
    availability_evidence: Mapping[str, JsonValue]
    g2_feature_source: "G2FeatureSourceAuthority | None" = None


@dataclass(frozen=True, slots=True)
class G2FeatureSourceAuthority:
    """Opaque, outcome-free R1 child authority decoded only after verified G1."""

    root: Path
    foundation_bundle_id: str
    foundation_configuration_id: str
    observation_dataset_id: str
    panel_dataset_id: str
    observation_configuration: Mapping[str, JsonValue]
    observation_source_dataset_ids: tuple[str, ...]
    observation_selection_policies: Mapping[str, JsonValue]
    holdout_range: tuple[datetime, datetime]
    observation_reference: ArtifactReference
    panel_reference: ArtifactReference
    source_id: str


@dataclass(frozen=True, slots=True)
class VerifiedG2FeatureSource:
    """Independently verified market-data and panel projection with no target child."""

    observations: R2G2ObservationView
    panel: R2G2PanelView
    source_id: str


def _g2_feature_source_id(
    *,
    foundation_bundle_id: str,
    foundation_configuration_id: str,
    observation_dataset_id: str,
    panel_dataset_id: str,
    observation_configuration: Mapping[str, JsonValue],
    observation_source_dataset_ids: tuple[str, ...],
    observation_selection_policies: Mapping[str, JsonValue],
    holdout_range: tuple[datetime, datetime],
    observation_reference: ArtifactReference,
    panel_reference: ArtifactReference,
) -> str:
    return _hash_json(
        {
            "contract": "qtrad-r2-g2-feature-source-authority-v1",
            "foundation_bundle_id": foundation_bundle_id,
            "foundation_configuration_id": foundation_configuration_id,
            "observation_dataset_id": observation_dataset_id,
            "panel_dataset_id": panel_dataset_id,
            "observation_configuration": observation_configuration,
            "observation_source_dataset_ids": list(observation_source_dataset_ids),
            "observation_selection_policies": observation_selection_policies,
            "holdout_range": [item.isoformat() for item in holdout_range],
            "observation_reference": observation_reference.as_json(),
            "panel_reference": panel_reference.as_json(),
        }
    )


def write_foundation_bundle(path: Path, bundle: FoundationBundle) -> None:
    """Write one bounded thin bundle without replacing evidence."""

    _preflight_bundle_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(bundle.as_json(), sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > _MAX_BUNDLE_BYTES:
        raise ValueError("foundation bundle exceeds the 4 MiB limit")
    with path.open("x", encoding="utf-8") as output:
        output.write(encoded)


def _preflight_bundle_output(path: Path) -> None:
    if path.is_symlink() or path.exists():
        raise ValueError("foundation bundle output must be a new regular file")


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
        "source_class",
        "bundle_id",
    }
    if set(payload) != expected:
        raise ValueError("foundation bundle has an unexpected schema")
    if payload["contract"] != FoundationBundle.CONTRACT or payload["schema_version"] != 2:
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
        market_data_source_class=MarketDataSourceClass(_text(payload["source_class"])),
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
        target_revision_policy_reason=_optional_text(payload["target_revision_policy_reason"]),
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
    """Strictly decode causal delay, gap, activity and lineage evidence."""

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
    availability_report = _availability_delay_report(_mapping(payload["availability_delay_report"]))
    revision_report = _revision_delay_report(_mapping(payload["revision_delay_report"]))
    lineage = _mapping(payload["lineage_summary"])
    if set(lineage) != {
        "row_count",
        "event_type_counts",
        "minimum_global_position",
        "maximum_global_position",
    }:
        raise ValueError("observation lineage summary has an unexpected schema")
    _int(lineage["row_count"])
    for event_type, count in _mapping(lineage["event_type_counts"]).items():
        _text(event_type)
        _int(count)
    _optional_int(lineage["minimum_global_position"])
    _optional_int(lineage["maximum_global_position"])
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
        availability_report=availability_report,
        revision_report=revision_report,
        payload=payload,
    )


def verify_observation_build_evidence(
    manifest: ObservationManifest, dataset: ObservationDataset
) -> ObservationBuildEvidence:
    """Recompute authenticated observation evidence and verify source-universe lineage."""

    evidence = load_observation_build_evidence(manifest)
    expected_configuration_keys = {
        "universe_name",
        "universe_configuration_hash",
        "ordered_instruments",
        "interval_start",
        "interval_end",
    }
    if set(dataset.configuration) != expected_configuration_keys:
        raise ValueError("observation configuration has unknown or missing fields")
    ordered_instruments = tuple(
        _text(item) for item in _sequence(dataset.configuration["ordered_instruments"])
    )
    if not ordered_instruments or len(set(ordered_instruments)) != len(ordered_instruments):
        raise ValueError("observation universe must be non-empty and unique")
    if any(
        ":" not in instrument_id or instrument_id != instrument_id.lower()
        for instrument_id in ordered_instruments
    ):
        raise ValueError("observation universe must use canonical instrument IDs")
    if any(row.instrument_id not in ordered_instruments for row in dataset.rows):
        raise ValueError("observation row is outside the declared instrument universe")
    if set(evidence.source_active_intervals) != set(ordered_instruments):
        raise ValueError("source-active evidence does not match the observation universe")
    if any(str(gap.instrument_id) not in ordered_instruments for gap in evidence.gaps):
        raise ValueError("observation gap evidence references an unknown instrument")
    expected_selection_policies: dict[str, JsonValue] = {
        "provenance": "QUOTE_DERIVED",
        "availability_basis": "persisted_at",
        "canonical_lineage": "GLOBAL_POSITION_EXACT",
    }
    if dataset.selection_policies != expected_selection_policies:
        raise ValueError("observation selection policies are unsupported")

    snapshot = manifest.source_snapshot
    if snapshot.get("kind") != "verified-capture-snapshot":
        raise ValueError("observation manifest lacks verified snapshot/import evidence")
    import_sha256 = _text(snapshot["import_sha256"])
    _require_sha256(import_sha256, "snapshot import identity")
    if dataset.source_dataset_ids != (import_sha256,):
        raise ValueError("observation source dataset identity differs from its snapshot import")
    universe_name = _text(dataset.configuration["universe_name"])
    universe_hash = _text(dataset.configuration["universe_configuration_hash"])
    _require_sha256(universe_hash, "observation universe hash")
    snapshot_universe_name = _text(snapshot["universe_name"])
    snapshot_universe_hash = _text(snapshot["universe_hash"])
    _require_sha256(snapshot_universe_hash, "snapshot universe hash")
    if universe_name != snapshot_universe_name or universe_hash != snapshot_universe_hash:
        raise ValueError("observation universe identity differs from its source snapshot")

    source_start = _datetime(dataset.configuration["interval_start"])
    source_end = _datetime(dataset.configuration["interval_end"])
    if source_end <= source_start:
        raise ValueError("observation source bounds must be positive")
    if any(
        active_start < source_start or active_end > source_end
        for intervals in evidence.source_active_intervals.values()
        for active_start, active_end in intervals
    ):
        raise ValueError("source-active evidence falls outside observation bounds")
    for report in (evidence.availability_report, evidence.revision_report):
        if report.calibration_start < source_start or report.calibration_end > source_end:
            raise ValueError("delay calibration range falls outside observation bounds")
    rebuilt_availability = build_availability_delay_report(
        dataset.rows,
        calibration_start=evidence.availability_report.calibration_start,
        calibration_end=evidence.availability_report.calibration_end,
        configured_percentile=evidence.availability_report.configured_percentile,
        safety_margin=evidence.availability_report.safety_margin,
        grid_resolution=timedelta(minutes=1),
    )
    rebuilt_revisions = build_revision_delay_report(
        dataset.rows,
        calibration_start=evidence.revision_report.calibration_start,
        calibration_end=evidence.revision_report.calibration_end,
    )
    if rebuilt_availability != evidence.availability_report:
        raise ValueError("availability-delay evidence does not match observation rows")
    if rebuilt_revisions != evidence.revision_report:
        raise ValueError("revision-delay evidence does not match observation rows")

    event_type_counts: dict[str, int] = {}
    for row in dataset.rows:
        event_type_counts[row.event_type] = event_type_counts.get(row.event_type, 0) + 1
    expected_lineage: dict[str, JsonValue] = {
        "row_count": len(dataset.rows),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "minimum_global_position": min((row.global_position for row in dataset.rows), default=None),
        "maximum_global_position": max((row.global_position for row in dataset.rows), default=None),
    }
    if evidence.payload["lineage_summary"] != expected_lineage:
        raise ValueError("observation lineage summary does not match its rows")
    return evidence


def verify_foundation_configuration_evidence(
    configuration: FoundationConfig,
    observations: ObservationDataset,
    evidence: ObservationBuildEvidence,
) -> None:
    ordered_observation_instruments = tuple(
        _text(item) for item in _sequence(observations.configuration["ordered_instruments"])
    )
    if configuration.ordered_instruments != ordered_observation_instruments:
        raise ValueError("foundation instrument universe differs from observations")
    if set(evidence.source_active_intervals) != set(configuration.ordered_instruments):
        raise ValueError("source-active evidence differs from the foundation universe")
    if configuration.availability_basis is not AvailabilityBasis.PERSISTED_AT:
        raise ValueError("R1 delay evidence supports only persisted-at availability")

    report = evidence.availability_report
    if (
        configuration.feature_lag_calibration_range
        != (report.calibration_start, report.calibration_end)
        or configuration.feature_lag_percentile != report.configured_percentile
        or configuration.feature_lag_safety_margin != report.safety_margin
    ):
        raise ValueError("foundation feature-lag policy differs from measured evidence")
    if (
        report.maximum_delay is not None
        and report.calibration_end + report.maximum_delay > configuration.range_start
    ):
        raise ValueError("availability calibration was not mature before the decision range")
    if configuration.feature_lag_policy == "MEASURED":
        if configuration.selected_feature_lag != report.selected_lag:
            raise ValueError("measured feature lag differs from availability evidence")
    elif configuration.selected_feature_lag < report.selected_lag:
        raise ValueError("provisional feature lag is less conservative than measured evidence")

    revision_report = evidence.revision_report
    if (
        revision_report.calibration_start != report.calibration_start
        or revision_report.calibration_end != report.calibration_end
    ):
        raise ValueError("feature and revision delay evidence use different calibration ranges")
    if (
        revision_report.maximum_delay is not None
        and revision_report.calibration_end + revision_report.maximum_delay
        > configuration.range_start
    ):
        raise ValueError("revision calibration was not mature before the decision range")
    measured_revision_delay = (
        _ceil_to_grid(revision_report.maximum_delay, configuration.grid_resolution)
        if revision_report.maximum_delay is not None
        else None
    )
    if configuration.target_revision_policy == "MEASURED":
        if measured_revision_delay is None:
            raise ValueError("measured target revision policy lacks correction evidence")
        if configuration.target_revision_delay != measured_revision_delay:
            raise ValueError("target revision delay differs from measured maturity evidence")
    else:
        if configuration.target_revision_delay <= timedelta(0):
            raise ValueError("provisional target revision delay must be positive")
        if (
            measured_revision_delay is not None
            and configuration.target_revision_delay < measured_revision_delay
        ):
            raise ValueError(
                "provisional target revision delay is less conservative than maturity evidence"
            )


def _ceil_to_grid(value: timedelta, grid: timedelta) -> timedelta:
    return grid * ceil(value.total_seconds() / grid.total_seconds())


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

    _preflight_bundle_output(output_path)
    evidence = verify_observation_build_evidence(observation_manifest, observations)
    if evidence.payload != availability_evidence:
        raise ValueError("availability evidence differs from the observation manifest")
    verify_foundation_configuration_evidence(configuration, observations, evidence)
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
    target_index = R2HoldoutTargetIndex.create(targets)
    causal_metadata = R2HoldoutCausalMetadata.create(panel)
    blind_observations = R2OutcomeBlindObservationView.from_dataset(
        observations, holdout_start=configuration.holdout_range[0]
    )
    blind_panel = R2OutcomeBlindPanelView.from_dataset(
        panel, holdout_start=configuration.holdout_range[0]
    )
    g2_observations = R2G2ObservationView.from_dataset(
        observations, holdout_range=configuration.holdout_range
    )
    g2_panel = R2G2PanelView.from_dataset(panel, holdout_range=configuration.holdout_range)
    target_instruments = tuple(
        instrument_id
        for instrument_id in configuration.ordered_instruments
        if InstrumentRole(configuration.instrument_roles[instrument_id]) is InstrumentRole.TARGET
    )
    pre_holdout_target = R2PreHoldoutTargetProjection.create_from_target_dataset(
        targets,
        holdout_start=configuration.holdout_range[0],
        primary_horizon_seconds=int(configuration.primary_vertical_horizon.total_seconds()),
        target_instruments=target_instruments,
    )
    target_index_manifest = await store.write(
        kind="r2-target-index",
        contract=R2HoldoutTargetIndex.CONTRACT,
        schema_version=R2HoldoutTargetIndex.SCHEMA_VERSION,
        dataset_id=target_index.dataset_id,
        rows=tuple(item.as_json() for item in target_index.targets),
        lineage={
            "target_dataset_id": targets.dataset_id,
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    causal_metadata_manifest = await store.write(
        kind="r2-causal-metadata",
        contract=R2HoldoutCausalMetadata.CONTRACT,
        schema_version=R2HoldoutCausalMetadata.SCHEMA_VERSION,
        dataset_id=causal_metadata.dataset_id,
        rows=tuple(item.as_json() for item in causal_metadata.rows),
        lineage={
            "panel_dataset_id": panel.dataset_id,
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    blind_observations_manifest = await store.write(
        kind="r2-blind-observations",
        contract=R2OutcomeBlindObservationView.CONTRACT,
        schema_version=1,
        dataset_id=blind_observations.projection_id,
        rows=tuple(row.as_json() for row in blind_observations.rows),
        lineage={
            "observation_dataset_id": observations.dataset_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    blind_panel_manifest = await store.write(
        kind="r2-blind-panel",
        contract=R2OutcomeBlindPanelView.CONTRACT,
        schema_version=1,
        dataset_id=blind_panel.projection_id,
        rows=tuple(row.as_json() for row in blind_panel.rows),
        lineage={
            "panel_dataset_id": panel.dataset_id,
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    g2_observations_manifest = await store.write(
        kind="r2-g2-observations",
        contract=R2G2ObservationView.CONTRACT,
        schema_version=R2G2ObservationView.SCHEMA_VERSION,
        dataset_id=g2_observations.projection_id,
        rows=tuple(row.as_json() for row in g2_observations.rows),
        lineage={
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
            "holdout_end": configuration.holdout_range[1].isoformat(),
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    g2_panel_manifest = await store.write(
        kind="r2-g2-panel",
        contract=R2G2PanelView.CONTRACT,
        schema_version=R2G2PanelView.SCHEMA_VERSION,
        dataset_id=g2_panel.projection_id,
        rows=tuple(row.as_json() for row in g2_panel.rows),
        lineage={
            "panel_dataset_id": panel.dataset_id,
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
            "holdout_end": configuration.holdout_range[1].isoformat(),
        },
        application_version=application_version,
        image_identity=image_identity,
    )
    pre_holdout_target_manifest = await store.write(
        kind="r2-pre-holdout-target",
        contract=R2PreHoldoutTargetProjection.CONTRACT,
        schema_version=R2PreHoldoutTargetProjection.SCHEMA_VERSION,
        dataset_id=pre_holdout_target.projection_id,
        rows=(pre_holdout_target.as_json(),),
        lineage={
            "target_dataset_id": targets.dataset_id,
            "observation_dataset_id": observations.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
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
        "outcome_blind_projections": {
            "target_index": _child_reference("target-index", target_index_manifest).as_json(),
            "causal_metadata": _child_reference(
                "causal-metadata", causal_metadata_manifest
            ).as_json(),
            "observations": _child_reference(
                "blind-observations", blind_observations_manifest
            ).as_json(),
            "panel": _child_reference("blind-panel", blind_panel_manifest).as_json(),
            "g2_observations": _child_reference(
                "g2-observations", g2_observations_manifest
            ).as_json(),
            "g2_panel": _child_reference("g2-panel", g2_panel_manifest).as_json(),
            "pre_holdout_target": _child_reference(
                "pre-holdout-target", pre_holdout_target_manifest
            ).as_json(),
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
    observations = await observation_store.read_observations(observation_manifest.manifest_id)
    evidence = verify_observation_build_evidence(observation_manifest, observations)

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
    verify_foundation_configuration_evidence(configuration, observations, evidence)
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
    standard_build_summary_keys = {
        "application_version",
        "image_identity",
        "row_counts",
    }
    extension_build_summary_keys = standard_build_summary_keys | {"outcome_blind_projections"}
    build_summary_keys = frozenset(build_summary)
    if build_summary_keys not in {
        frozenset(standard_build_summary_keys),
        frozenset(extension_build_summary_keys),
    }:
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
    expected_panel = build_asof_panel(
        observations,
        configuration,
        gaps=evidence.gaps,
        source_active_intervals=evidence.source_active_intervals,
    )
    if panel != expected_panel:
        raise ValueError("foundation panel differs from deterministic causal replay")
    expected_targets = build_frozen_targets(
        observations,
        configuration,
        horizons=configuration.target_horizons,
    )
    if targets != expected_targets:
        raise ValueError("foundation targets differ from deterministic causal replay")
    expected_folds = build_expanding_folds(expected_targets, configuration)
    if folds != expected_folds:
        raise ValueError("foundation folds differ from deterministic causal replay")
    expected_forecasts = build_zero_return_forecasts(
        expected_panel,
        expected_targets,
        expected_folds,
        configuration,
    )
    if forecasts != expected_forecasts:
        raise ValueError("foundation forecasts differ from deterministic causal replay")
    expected_coverage = summarise_horizon_coverage(expected_targets, configuration)
    if bundle.coverage != expected_coverage:
        raise ValueError("foundation coverage differs from deterministic target replay")
    verify_foundation_children(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        coverage=bundle.coverage,
    )
    blind_projection_payload = build_summary.get("outcome_blind_projections")
    if blind_projection_payload is not None:
        projection_payload = _mapping(blind_projection_payload)
        projection_names = {
            "target_index": "target-index",
            "causal_metadata": "causal-metadata",
            "observations": "blind-observations",
            "panel": "blind-panel",
            "pre_holdout_target": "pre-holdout-target",
        }
        g2_projection_names = {
            "g2_observations": "g2-observations",
            "g2_panel": "g2-panel",
        }
        if set(projection_payload) == set(projection_names) | set(g2_projection_names):
            projection_names.update(g2_projection_names)
        elif set(projection_payload) != set(projection_names):
            raise ValueError("foundation outcome-blind projection set is incomplete")
        projection_manifests: dict[str, FoundationChildManifest] = {}
        projection_rows: dict[str, tuple[dict[str, JsonValue], ...]] = {}
        for key, name in projection_names.items():
            reference = _reference(name, projection_payload[key])
            manifest = await store.verify(reference.manifest_id)
            _verify_child_reference(reference, manifest)
            if (
                manifest.application_version != application_version
                or manifest.image_identity != image_identity
            ):
                raise ValueError("foundation projection build identity differs from the bundle")
            projection_manifests[key] = manifest
            projection_rows[key] = await store.read_rows(reference.manifest_id)
        _require_lineage(
            projection_manifests["target_index"],
            {
                "target_dataset_id": targets.dataset_id,
                "observation_dataset_id": observations.dataset_id,
                "foundation_configuration_id": configuration.configuration_id,
            },
        )
        _require_lineage(
            projection_manifests["causal_metadata"],
            {
                "panel_dataset_id": panel.dataset_id,
                "observation_dataset_id": observations.dataset_id,
                "foundation_configuration_id": configuration.configuration_id,
            },
        )
        _require_lineage(
            projection_manifests["observations"],
            {
                "observation_dataset_id": observations.dataset_id,
                "holdout_start": configuration.holdout_range[0].isoformat(),
            },
        )
        _require_lineage(
            projection_manifests["panel"],
            {
                "panel_dataset_id": panel.dataset_id,
                "observation_dataset_id": observations.dataset_id,
                "foundation_configuration_id": configuration.configuration_id,
                "holdout_start": configuration.holdout_range[0].isoformat(),
            },
        )
        _require_lineage(
            projection_manifests["pre_holdout_target"],
            {
                "target_dataset_id": targets.dataset_id,
                "observation_dataset_id": observations.dataset_id,
                "foundation_configuration_id": configuration.configuration_id,
                "holdout_start": configuration.holdout_range[0].isoformat(),
            },
        )
        expected_target_index = R2HoldoutTargetIndex.create(targets)
        target_index = R2HoldoutTargetIndex.from_rows(
            source_target_dataset_id=targets.dataset_id,
            observation_dataset_id=observations.dataset_id,
            foundation_configuration_id=configuration.configuration_id,
            rows=projection_rows["target_index"],
        )
        if (
            target_index != expected_target_index
            or target_index.dataset_id != projection_manifests["target_index"].dataset_id
        ):
            raise ValueError("foundation target-index projection differs from full replay")
        expected_causal_metadata = R2HoldoutCausalMetadata.create(panel)
        causal_metadata = R2HoldoutCausalMetadata.from_rows(
            source_panel_dataset_id=panel.dataset_id,
            rows=projection_rows["causal_metadata"],
        )
        if (
            causal_metadata != expected_causal_metadata
            or causal_metadata.dataset_id != projection_manifests["causal_metadata"].dataset_id
        ):
            raise ValueError("foundation causal metadata projection differs from full replay")
        expected_blind_observations = R2OutcomeBlindObservationView.from_dataset(
            observations, holdout_start=configuration.holdout_range[0]
        )
        actual_blind_observations = R2OutcomeBlindObservationView(
            dataset_id=observations.dataset_id,
            rows=tuple(
                _observation_from_row(cast(Mapping[str, object], row))
                for row in projection_rows["observations"]
            ),
            configuration=observations.configuration,
            source_dataset_ids=observations.source_dataset_ids,
            selection_policies=observations.selection_policies,
            projection_id=projection_manifests["observations"].dataset_id,
        )
        if actual_blind_observations != expected_blind_observations:
            raise ValueError("foundation blind observation projection differs from full replay")
        expected_blind_panel = R2OutcomeBlindPanelView.from_dataset(
            panel, holdout_start=configuration.holdout_range[0]
        )
        actual_blind_panel = R2OutcomeBlindPanelView(
            dataset_id=panel.dataset_id,
            observation_dataset_id=observations.dataset_id,
            foundation_configuration_id=configuration.configuration_id,
            rows=tuple(_panel_row(row) for row in projection_rows["panel"]),
            projection_id=projection_manifests["panel"].dataset_id,
        )
        if actual_blind_panel != expected_blind_panel:
            raise ValueError("foundation blind panel projection differs from full replay")
        if "g2_observations" in projection_manifests:
            _require_lineage(
                projection_manifests["g2_observations"],
                {
                    "observation_dataset_id": observations.dataset_id,
                    "foundation_configuration_id": configuration.configuration_id,
                    "holdout_start": configuration.holdout_range[0].isoformat(),
                    "holdout_end": configuration.holdout_range[1].isoformat(),
                },
            )
            _require_lineage(
                projection_manifests["g2_panel"],
                {
                    "panel_dataset_id": panel.dataset_id,
                    "observation_dataset_id": observations.dataset_id,
                    "foundation_configuration_id": configuration.configuration_id,
                    "holdout_start": configuration.holdout_range[0].isoformat(),
                    "holdout_end": configuration.holdout_range[1].isoformat(),
                },
            )
            actual_g2_observations = R2G2ObservationView(
                dataset_id=observations.dataset_id,
                rows=tuple(
                    _observation_from_row(cast(Mapping[str, object], row))
                    for row in projection_rows["g2_observations"]
                ),
                configuration=observations.configuration,
                source_dataset_ids=observations.source_dataset_ids,
                selection_policies=observations.selection_policies,
                holdout_range=configuration.holdout_range,
                projection_id=projection_manifests["g2_observations"].dataset_id,
            )
            actual_g2_panel = R2G2PanelView(
                dataset_id=panel.dataset_id,
                observation_dataset_id=observations.dataset_id,
                foundation_configuration_id=configuration.configuration_id,
                rows=tuple(_panel_row(row) for row in projection_rows["g2_panel"]),
                holdout_range=configuration.holdout_range,
                projection_id=projection_manifests["g2_panel"].dataset_id,
            )
            if actual_g2_observations != R2G2ObservationView.from_dataset(
                observations, holdout_range=configuration.holdout_range
            ):
                raise ValueError("foundation G2 observation projection differs from full replay")
            if actual_g2_panel != R2G2PanelView.from_dataset(
                panel, holdout_range=configuration.holdout_range
            ):
                raise ValueError("foundation G2 panel projection differs from full replay")
        pre_holdout_target = R2PreHoldoutTargetProjection.from_json(
            _single_row(projection_rows, "pre_holdout_target")
        )
        expected_pre_holdout_target = R2PreHoldoutTargetProjection.create_from_target_dataset(
            targets,
            holdout_start=configuration.holdout_range[0],
            primary_horizon_seconds=int(configuration.primary_vertical_horizon.total_seconds()),
            target_instruments=tuple(
                instrument_id
                for instrument_id in configuration.ordered_instruments
                if InstrumentRole(configuration.instrument_roles[instrument_id])
                is InstrumentRole.TARGET
            ),
        )
        if (
            pre_holdout_target != expected_pre_holdout_target
            or pre_holdout_target.projection_id
            != projection_manifests["pre_holdout_target"].dataset_id
        ):
            raise ValueError("foundation pre-holdout target projection differs from full replay")
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
        source_active_intervals=evidence.source_active_intervals,
        availability_evidence=availability,
    )


async def verify_outcome_blind_foundation_bundle(
    *,
    root: Path,
    bundle_path: Path,
    clock: Clock,
    holdout_target_source: R2HoldoutTargetSource,
) -> OutcomeBlindVerifiedFoundationBundle:
    """Verify only authenticated outcome-blind R1 projections needed by F2."""
    bundle = load_foundation_bundle(bundle_path)
    build_summary = _mapping(bundle.build_summary)
    if set(build_summary) != {
        "application_version",
        "image_identity",
        "row_counts",
        "outcome_blind_projections",
    }:
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

    observation_store = ParquetObservationStore(root, clock)
    observation_manifest = await observation_store.verify_file(bundle.observations.manifest_id)
    _verify_observation_reference(bundle.observations, observation_manifest)
    evidence = load_observation_build_evidence(observation_manifest)

    store = ParquetFoundationArtifactStore(root, clock)
    standard_references = (
        bundle.configuration,
        bundle.availability,
        bundle.panel,
        bundle.targets,
        bundle.folds,
        bundle.forecasts,
    )
    manifests: dict[str, FoundationChildManifest] = {}
    rows: dict[str, tuple[dict[str, JsonValue], ...]] = {}
    for reference in standard_references:
        manifest = (
            await store.verify_file(reference.manifest_id)
            if reference.name
            in {
                "panel",
                "targets",
                "forecasts",
            }
            else await store.verify(reference.manifest_id)
        )
        _verify_child_reference(reference, manifest)
        manifests[reference.name] = manifest
        if reference.name in {"configuration", "availability", "folds"}:
            rows[reference.name] = await store.read_rows(reference.manifest_id)

    projection_payload = _mapping(build_summary["outcome_blind_projections"])
    projection_names = {
        "target_index": "target-index",
        "causal_metadata": "causal-metadata",
        "observations": "blind-observations",
        "panel": "blind-panel",
        "pre_holdout_target": "pre-holdout-target",
    }
    g2_projection_names = {
        "g2_observations": "g2-observations",
        "g2_panel": "g2-panel",
    }
    has_g2_feature_source = set(projection_payload) == set(projection_names) | set(
        g2_projection_names
    )
    if has_g2_feature_source:
        projection_names.update(g2_projection_names)
    elif set(projection_payload) != set(projection_names):
        raise ValueError("foundation outcome-blind projection set is incomplete")
    projection_manifests: dict[str, FoundationChildManifest] = {}
    projection_rows: dict[str, tuple[dict[str, JsonValue], ...]] = {}
    projection_references: dict[str, ArtifactReference] = {}
    for key, name in projection_names.items():
        reference = _reference(name, projection_payload[key])
        manifest = (
            await store.verify_file(reference.manifest_id)
            if key in g2_projection_names
            else await store.verify(reference.manifest_id)
        )
        _verify_child_reference(reference, manifest)
        projection_manifests[key] = manifest
        projection_references[key] = reference
        if key not in g2_projection_names:
            projection_rows[key] = await store.read_rows(reference.manifest_id)

    configuration = decode_foundation_config(_single_row(rows, "configuration"))
    availability = cast(Mapping[str, JsonValue], _single_row(rows, "availability"))
    if availability != evidence.payload:
        raise ValueError("availability child differs from observation build evidence")
    expected_availability_id = _hash_json(
        {
            "contract": AVAILABILITY_EVIDENCE_CONTRACT,
            "observation_dataset_id": observation_manifest.dataset_id,
            "evidence": availability,
        }
    )
    if manifests["availability"].dataset_id != expected_availability_id:
        raise ValueError("availability child semantic identity is invalid")

    blind_observation_rows = tuple(
        _observation_from_row(cast(Mapping[str, object], row))
        for row in projection_rows["observations"]
    )
    blind_observations = R2OutcomeBlindObservationView(
        dataset_id=observation_manifest.dataset_id,
        rows=blind_observation_rows,
        configuration=observation_manifest.configuration,
        source_dataset_ids=observation_manifest.source_dataset_ids,
        selection_policies=observation_manifest.selection_policies,
        projection_id=projection_manifests["observations"].dataset_id,
    )
    if blind_observations.projection_id != R2OutcomeBlindObservationView.compute_projection_id(
        source_dataset_id=blind_observations.dataset_id,
        holdout_start=configuration.holdout_range[0],
        rows=blind_observation_rows,
    ):
        raise ValueError("outcome-blind observation projection identity is invalid")
    blind_panel_rows = tuple(_panel_row(row) for row in projection_rows["panel"])
    blind_panel = R2OutcomeBlindPanelView(
        dataset_id=manifests["panel"].dataset_id,
        observation_dataset_id=observation_manifest.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
        rows=blind_panel_rows,
        projection_id=projection_manifests["panel"].dataset_id,
    )
    if blind_panel.projection_id != R2OutcomeBlindPanelView.compute_projection_id(
        source_dataset_id=blind_panel.dataset_id,
        holdout_start=configuration.holdout_range[0],
        rows=blind_panel_rows,
    ):
        raise ValueError("outcome-blind panel projection identity is invalid")
    verify_foundation_configuration_evidence(
        configuration, cast(ObservationDataset, blind_observations), evidence
    )

    _require_lineage(
        manifests["configuration"], {"observation_dataset_id": observation_manifest.dataset_id}
    )
    _require_lineage(
        manifests["availability"],
        {
            "observation_dataset_id": observation_manifest.dataset_id,
            "observation_manifest_id": observation_manifest.manifest_id,
        },
    )
    _require_lineage(
        manifests["targets"],
        {
            "observation_dataset_id": observation_manifest.dataset_id,
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
        projection_manifests["target_index"],
        {
            "target_dataset_id": manifests["targets"].dataset_id,
            "observation_dataset_id": observation_manifest.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
    )
    _require_lineage(
        projection_manifests["causal_metadata"],
        {
            "panel_dataset_id": manifests["panel"].dataset_id,
            "observation_dataset_id": observation_manifest.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
        },
    )
    _require_lineage(
        projection_manifests["observations"],
        {
            "observation_dataset_id": observation_manifest.dataset_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
        },
    )
    _require_lineage(
        projection_manifests["panel"],
        {
            "panel_dataset_id": manifests["panel"].dataset_id,
            "observation_dataset_id": observation_manifest.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
        },
    )
    _require_lineage(
        projection_manifests["pre_holdout_target"],
        {
            "target_dataset_id": manifests["targets"].dataset_id,
            "observation_dataset_id": observation_manifest.dataset_id,
            "foundation_configuration_id": configuration.configuration_id,
            "holdout_start": configuration.holdout_range[0].isoformat(),
        },
    )
    g2_feature_source: G2FeatureSourceAuthority | None = None
    if has_g2_feature_source:
        _require_lineage(
            projection_manifests["g2_observations"],
            {
                "observation_dataset_id": observation_manifest.dataset_id,
                "foundation_configuration_id": configuration.configuration_id,
                "holdout_start": configuration.holdout_range[0].isoformat(),
                "holdout_end": configuration.holdout_range[1].isoformat(),
            },
        )
        _require_lineage(
            projection_manifests["g2_panel"],
            {
                "panel_dataset_id": manifests["panel"].dataset_id,
                "observation_dataset_id": observation_manifest.dataset_id,
                "foundation_configuration_id": configuration.configuration_id,
                "holdout_start": configuration.holdout_range[0].isoformat(),
                "holdout_end": configuration.holdout_range[1].isoformat(),
            },
        )
        observation_reference = projection_references["g2_observations"]
        panel_reference = projection_references["g2_panel"]
        source_id = _g2_feature_source_id(
            foundation_bundle_id=bundle.bundle_id,
            foundation_configuration_id=configuration.configuration_id,
            observation_dataset_id=observation_manifest.dataset_id,
            panel_dataset_id=manifests["panel"].dataset_id,
            observation_configuration=observation_manifest.configuration,
            observation_source_dataset_ids=observation_manifest.source_dataset_ids,
            observation_selection_policies=observation_manifest.selection_policies,
            holdout_range=configuration.holdout_range,
            observation_reference=observation_reference,
            panel_reference=panel_reference,
        )
        g2_feature_source = G2FeatureSourceAuthority(
            root=root,
            foundation_bundle_id=bundle.bundle_id,
            foundation_configuration_id=configuration.configuration_id,
            observation_dataset_id=observation_manifest.dataset_id,
            panel_dataset_id=manifests["panel"].dataset_id,
            observation_configuration=dict(observation_manifest.configuration),
            observation_source_dataset_ids=observation_manifest.source_dataset_ids,
            observation_selection_policies=dict(observation_manifest.selection_policies),
            holdout_range=configuration.holdout_range,
            observation_reference=observation_reference,
            panel_reference=panel_reference,
            source_id=source_id,
        )
    for manifest in (*manifests.values(), *projection_manifests.values()):
        if (
            manifest.application_version != application_version
            or manifest.image_identity != image_identity
        ):
            raise ValueError("foundation child build identity differs from the bundle")

    target_index = R2HoldoutTargetIndex.from_rows(
        source_target_dataset_id=manifests["targets"].dataset_id,
        observation_dataset_id=observation_manifest.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
        rows=projection_rows["target_index"],
    )
    if target_index.dataset_id != projection_manifests["target_index"].dataset_id:
        raise ValueError("holdout target index child identity is invalid")
    causal_metadata = R2HoldoutCausalMetadata.from_rows(
        source_panel_dataset_id=manifests["panel"].dataset_id,
        rows=projection_rows["causal_metadata"],
    )
    if causal_metadata.dataset_id != projection_manifests["causal_metadata"].dataset_id:
        raise ValueError("holdout causal metadata child identity is invalid")
    pre_holdout_target = R2PreHoldoutTargetProjection.from_json(
        _single_row(projection_rows, "pre_holdout_target")
    )
    if (
        pre_holdout_target.source_target_dataset_id != manifests["targets"].dataset_id
        or pre_holdout_target.observation_dataset_id != observation_manifest.dataset_id
        or pre_holdout_target.foundation_configuration_id != configuration.configuration_id
        or pre_holdout_target.holdout_start != configuration.holdout_range[0]
        or pre_holdout_target.primary_horizon_seconds
        != int(configuration.primary_vertical_horizon.total_seconds())
        or pre_holdout_target.target_instruments
        != tuple(sorted(holdout_target_source.target_instruments))
        or pre_holdout_target.projection_id != projection_manifests["pre_holdout_target"].dataset_id
        or pre_holdout_target.projected_target_dataset
        != holdout_target_source.pre_holdout_target_dataset
    ):
        raise ValueError("pre-holdout target projection is not bound to the holdout source")
    holdout_target_source.verify_target_index(target_index)
    holdout_target_source.verify_r1_causal_evidence(
        causal_metadata=causal_metadata,
        source_active_intervals=evidence.source_active_intervals,
        data_gaps=tuple(
            (gap.instrument_id.value, gap.interval_start, gap.interval_end) for gap in evidence.gaps
        ),
        availability_evidence_id=manifests["availability"].dataset_id,
    )
    if (
        holdout_target_source.source_target_dataset_id != manifests["targets"].dataset_id
        or holdout_target_source.observation_dataset_id != observation_manifest.dataset_id
        or holdout_target_source.foundation_configuration_id != configuration.configuration_id
        or len(holdout_target_source.targets) != manifests["targets"].row_count
    ):
        raise ValueError("holdout target source is not bound to the authenticated target child")
    expected_target_instruments = tuple(
        instrument_id
        for instrument_id in configuration.ordered_instruments
        if InstrumentRole(configuration.instrument_roles[instrument_id]) is InstrumentRole.TARGET
    )
    if (
        holdout_target_source.holdout_range != configuration.holdout_range
        or holdout_target_source.primary_horizon_seconds
        != int(configuration.primary_vertical_horizon.total_seconds())
        or holdout_target_source.target_instruments != expected_target_instruments
    ):
        raise ValueError("holdout target source policy differs from authenticated configuration")
    folds = FoldDataset(
        folds=tuple(_fold(row) for row in rows["folds"]),
        target_dataset_id=_lineage_text(manifests["folds"], "target_dataset_id"),
        foundation_configuration_id=_lineage_text(
            manifests["folds"], "foundation_configuration_id"
        ),
        dataset_id=manifests["folds"].dataset_id,
    )
    target_ids = {item.target_id for item in holdout_target_source.targets}
    for fold in folds.folds:
        if not set(fold.training_target_ids) | set(fold.validation_target_ids) <= target_ids:
            raise ValueError("fold membership is not covered by the holdout target source")
    expected_folds = build_expanding_folds(
        cast(TargetDataset, R2OutcomeBlindTargetView.from_source(holdout_target_source)),
        configuration,
    )
    if folds != expected_folds:
        raise ValueError("foundation folds differ from deterministic pre-holdout replay")
    return OutcomeBlindVerifiedFoundationBundle(
        bundle=bundle,
        configuration=configuration,
        observations=cast(ObservationDataset, blind_observations),
        panel=cast(PanelDataset, blind_panel),
        targets=R2OutcomeBlindTargetView.from_source(holdout_target_source),
        folds=folds,
        source_active_intervals=evidence.source_active_intervals,
        availability_evidence=availability,
        g2_feature_source=g2_feature_source,
    )


async def verify_g2_feature_source(
    authority: G2FeatureSourceAuthority,
    *,
    clock: Clock,
) -> VerifiedG2FeatureSource:
    """Decode only the exact G2-safe children authenticated before G1."""

    expected_source_id = _g2_feature_source_id(
        foundation_bundle_id=authority.foundation_bundle_id,
        foundation_configuration_id=authority.foundation_configuration_id,
        observation_dataset_id=authority.observation_dataset_id,
        panel_dataset_id=authority.panel_dataset_id,
        observation_configuration=authority.observation_configuration,
        observation_source_dataset_ids=authority.observation_source_dataset_ids,
        observation_selection_policies=authority.observation_selection_policies,
        holdout_range=authority.holdout_range,
        observation_reference=authority.observation_reference,
        panel_reference=authority.panel_reference,
    )
    if authority.source_id != expected_source_id:
        raise ValueError("G2 feature source authority identity is invalid")

    store = ParquetFoundationArtifactStore(authority.root, clock)
    observation_manifest = await store.verify(authority.observation_reference.manifest_id)
    panel_manifest = await store.verify(authority.panel_reference.manifest_id)
    _verify_child_reference(authority.observation_reference, observation_manifest)
    _verify_child_reference(authority.panel_reference, panel_manifest)
    _require_lineage(
        observation_manifest,
        {
            "observation_dataset_id": authority.observation_dataset_id,
            "foundation_configuration_id": authority.foundation_configuration_id,
            "holdout_start": authority.holdout_range[0].isoformat(),
            "holdout_end": authority.holdout_range[1].isoformat(),
        },
    )
    _require_lineage(
        panel_manifest,
        {
            "panel_dataset_id": authority.panel_dataset_id,
            "observation_dataset_id": authority.observation_dataset_id,
            "foundation_configuration_id": authority.foundation_configuration_id,
            "holdout_start": authority.holdout_range[0].isoformat(),
            "holdout_end": authority.holdout_range[1].isoformat(),
        },
    )
    observation_rows = tuple(
        _observation_from_row(cast(Mapping[str, object], row))
        for row in await store.read_rows(authority.observation_reference.manifest_id)
    )
    panel_rows = tuple(
        _panel_row(row) for row in await store.read_rows(authority.panel_reference.manifest_id)
    )
    observations = R2G2ObservationView(
        dataset_id=authority.observation_dataset_id,
        rows=observation_rows,
        configuration=authority.observation_configuration,
        source_dataset_ids=authority.observation_source_dataset_ids,
        selection_policies=authority.observation_selection_policies,
        holdout_range=authority.holdout_range,
        projection_id=observation_manifest.dataset_id,
    )
    panel = R2G2PanelView(
        dataset_id=authority.panel_dataset_id,
        observation_dataset_id=authority.observation_dataset_id,
        foundation_configuration_id=authority.foundation_configuration_id,
        rows=panel_rows,
        holdout_range=authority.holdout_range,
        projection_id=panel_manifest.dataset_id,
    )
    if observations.projection_id != R2G2ObservationView.compute_projection_id(
        source_dataset_id=authority.observation_dataset_id,
        holdout_range=authority.holdout_range,
        rows=observation_rows,
    ):
        raise ValueError("G2 observation projection identity is invalid")
    if panel.projection_id != R2G2PanelView.compute_projection_id(
        source_dataset_id=authority.panel_dataset_id,
        holdout_range=authority.holdout_range,
        rows=panel_rows,
    ):
        raise ValueError("G2 panel projection identity is invalid")
    if any(row.interval_end > authority.holdout_range[1] for row in observation_rows):
        raise ValueError("G2 observations exceed the frozen holdout boundary")
    if any(
        row.decision_time < authority.holdout_range[0]
        or row.decision_time >= authority.holdout_range[1]
        for row in panel_rows
    ):
        raise ValueError("G2 panel row lies outside the frozen holdout range")
    return VerifiedG2FeatureSource(
        observations=observations,
        panel=panel,
        source_id=expected_source_id,
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


def _availability_delay_report(payload: Mapping[str, object]) -> AvailabilityDelayReport:
    expected = {
        "calibration_start",
        "calibration_end",
        "eligible_row_count",
        "excluded_row_count",
        "delay_percentiles_seconds",
        "maximum_delay_seconds",
        "configured_percentile",
        "safety_margin_seconds",
        "selected_lag_seconds",
    }
    if set(payload) != expected:
        raise ValueError("availability-delay report has an unexpected schema")
    percentiles = {
        _text(name): _float(value)
        for name, value in _mapping(payload["delay_percentiles_seconds"]).items()
    }
    maximum = _optional_float(payload["maximum_delay_seconds"])
    report = AvailabilityDelayReport(
        calibration_start=_datetime(payload["calibration_start"]),
        calibration_end=_datetime(payload["calibration_end"]),
        eligible_row_count=_int(payload["eligible_row_count"]),
        excluded_row_count=_int(payload["excluded_row_count"]),
        delay_percentiles=percentiles,
        maximum_delay=timedelta(seconds=maximum) if maximum is not None else None,
        configured_percentile=_float(payload["configured_percentile"]),
        safety_margin=_duration(payload["safety_margin_seconds"]),
        selected_lag=_duration(payload["selected_lag_seconds"]),
    )
    if report.as_json() != payload:
        raise ValueError("availability-delay report is not canonical")
    return report


def _revision_delay_report(payload: Mapping[str, object]) -> RevisionDelayReport:
    expected = {
        "calibration_start",
        "calibration_end",
        "eligible_correction_count",
        "excluded_correction_count",
        "delay_percentiles_seconds",
        "maximum_delay_seconds",
    }
    if set(payload) != expected:
        raise ValueError("revision-delay report has an unexpected schema")
    percentiles = {
        _text(name): _float(value)
        for name, value in _mapping(payload["delay_percentiles_seconds"]).items()
    }
    maximum = _optional_float(payload["maximum_delay_seconds"])
    report = RevisionDelayReport(
        calibration_start=_datetime(payload["calibration_start"]),
        calibration_end=_datetime(payload["calibration_end"]),
        eligible_correction_count=_int(payload["eligible_correction_count"]),
        excluded_correction_count=_int(payload["excluded_correction_count"]),
        delay_percentiles=percentiles,
        maximum_delay=timedelta(seconds=maximum) if maximum is not None else None,
    )
    if report.as_json() != payload:
        raise ValueError("revision-delay report is not canonical")
    return report


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


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


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


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")
