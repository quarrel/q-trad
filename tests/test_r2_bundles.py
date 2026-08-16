"""Round-trip and mutation tests for R2 replay bundles."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

import qtrad.runtime.r2_holdout_source as holdout_source_runtime
import qtrad.runtime.r2_verification as verification
from qtrad.domain.events import JsonValue
from qtrad.domain.foundation import (
    ExcursionDisposition,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.market_data import MarketDataSourceClass, PriceBasis
from qtrad.domain.r2_bundles import (
    ArtifactReference,
    R2ForecastManifest,
    R2OofBundle,
)
from qtrad.domain.r2_holdout import R2HoldoutTargetSource
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.runtime.r2_bundles import (
    atomic_create,
    canonical_bytes,
    verify_r2_oof_bundle,
    verify_r2_reference,
    write_r2_oof_bundle,
)
from qtrad.runtime.r2_holdout_source import (
    _split_part_rows,
    bounded_manifest_part_paths,
    load_r2_holdout_target_source,
    write_r2_holdout_target_source,
)
from qtrad.runtime.r2_verification import (
    _build_synthetic_oof,
    authenticate_r2_oof,
    selection_freeze,
    verify_oof_bundle,
    verify_r2_oof_semantics,
)


def _child(path: str, seed: str) -> tuple[ArtifactReference, dict[str, object]]:
    identity = hashlib.sha256(seed.encode()).hexdigest()
    contract = f"qtrad-test-child-{seed}-v1"
    payload: dict[str, object] = {
        "contract": contract,
        "schema_version": 1,
        "artifact_id": identity,
        "value": seed,
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return ArtifactReference(contract, identity, path, digest), payload


def _bundle_and_children() -> tuple[R2OofBundle, dict[str, dict[str, object]]]:
    refs: list[ArtifactReference] = []
    children: dict[str, dict[str, object]] = {}
    for _index, category in enumerate(
        ("feature", "preprocessing", "fit", "forecast", "coverage", "evaluation")
    ):
        path = f"{category}/child.json"
        reference, payload = _child(path, category)
        refs.append(reference)
        children[path] = payload
    bundle = R2OofBundle.create(
        foundation_bundle_id=hashlib.sha256(b"foundation").hexdigest(),
        experiment_configuration_id=hashlib.sha256(b"experiment").hexdigest(),
        source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        feature_children=(refs[0],),
        preprocessing_children=(refs[1],),
        fit_children=(refs[2],),
        forecast_manifests=(refs[3],),
        coverage_children=(refs[4],),
        evaluation_children=(refs[5],),
    )
    return bundle, children


def _mutated_oof_reference(
    root: Path,
    reference: ArtifactReference,
    identity_field: str,
) -> ArtifactReference:
    path = root / reference.path
    payload = cast(dict[str, object], json.loads(path.read_bytes()))
    payload.pop("oof_id", None)
    payload[identity_field] = reference.semantic_id
    path.write_bytes(canonical_bytes(payload))
    return replace(
        reference,
        sha256=hashlib.sha256(canonical_bytes(payload)).hexdigest(),
    )


def _holdout_source_payload() -> dict[str, object]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for decision_time, value in (
        (now - timedelta(days=1), 0.1),
        (now + timedelta(days=1), 0.2),
    ):
        horizon = timedelta(seconds=900)
        rows.append(
            TargetRow(
                instrument_id="INSTRUMENT_0",
                decision_time=decision_time,
                horizon=horizon,
                target_basis=PriceBasis.MID,
                target_revision_policy="FIXTURE_V1",
                target_start_time=decision_time,
                target_end_time=decision_time + horizon,
                target_freeze_at=decision_time + horizon,
                target_available_at=decision_time + horizon,
                label_start_close=None,
                label_end_close=None,
                log_return=value,
                return_disposition=ReturnDisposition.VALID,
                start_event_id=None,
                end_event_id=None,
                upper_log_excursion=None,
                lower_log_excursion=None,
                excursion_disposition=ExcursionDisposition.INCOMPLETE_PATH,
            )
        )
    target_dataset = TargetDataset.create(
        rows,
        observation_dataset_id=hashlib.sha256(b"observations").hexdigest(),
        foundation_configuration_id=hashlib.sha256(b"foundation").hexdigest(),
    )
    return cast(
        dict[str, object],
        R2HoldoutTargetSource.create_from_target_dataset(
            target_dataset,
            holdout_range=(now, now + timedelta(days=2)),
            primary_horizon_seconds=900,
            target_instruments=("INSTRUMENT_0",),
        ).as_json(),
    )


def test_oof_bundle_round_trip_is_independently_authenticated(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)

    verified = verify_r2_oof_bundle(manifest_path)

    assert verified == bundle
    assert json.loads(manifest_path.read_bytes())["source_class"] == "IG_NATIVE_CAPTURE"


@pytest.mark.parametrize("identity_field", ["bundle_id", "manifest_id", "dataset_id"])
def test_oof_reference_requires_canonical_oof_id(
    tmp_path: Path,
    identity_field: str,
) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    reference = ArtifactReference(
        bundle.CONTRACT,
        bundle.oof_id,
        "manifest.json",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    verify_r2_reference(tmp_path, reference)
    with pytest.raises(ValueError, match="semantic identity mismatch"):
        verify_r2_reference(
            tmp_path,
            replace(reference, semantic_id=hashlib.sha256(b"mismatch").hexdigest()),
        )

    mutated_reference = _mutated_oof_reference(tmp_path, reference, identity_field)
    with pytest.raises(ValueError, match="canonical oof_id"):
        verify_r2_reference(tmp_path, mutated_reference)


def test_oof_bundle_rejects_child_mutation_and_republication(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    child_path = tmp_path / "feature" / "child.json"
    child_path.write_bytes(child_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="digest mismatch"):
        verify_r2_oof_bundle(manifest_path)
    with pytest.raises(FileExistsError):
        write_r2_oof_bundle(tmp_path, bundle, children)


def test_oof_bundle_rejects_orphaned_children(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    (tmp_path / "orphan.json").write_bytes(canonical_bytes({"contract": "orphan-v1"}))

    with pytest.raises(ValueError, match="orphaned child"):
        verify_r2_oof_bundle(manifest_path)


def test_reordered_reference_arrays_replay_to_the_same_identity(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    payload = json.loads(manifest_path.read_bytes())
    payload["evaluation_children"] = list(reversed(payload["evaluation_children"]))
    manifest_path.write_bytes(canonical_bytes(payload))

    assert verify_r2_oof_bundle(manifest_path) == bundle


def test_oof_bundle_binds_the_holdout_target_source_child(tmp_path: Path) -> None:
    base, children = _bundle_and_children()
    source_payload = _holdout_source_payload()
    source_id = str(source_payload["source_id"])
    source_contract = R2HoldoutTargetSource.CONTRACT
    source_path = "holdout/target-source.json"
    source_reference = ArtifactReference(
        source_contract,
        source_id,
        source_path,
        hashlib.sha256(canonical_bytes(source_payload)).hexdigest(),
    )
    bundle = R2OofBundle.create(
        foundation_bundle_id=base.foundation_bundle_id,
        experiment_configuration_id=base.experiment_configuration_id,
        source_class=base.source_class,
        evidence_class=base.evidence_class,
        feature_children=base.feature_children,
        preprocessing_children=base.preprocessing_children,
        fit_children=base.fit_children,
        forecast_manifests=base.forecast_manifests,
        coverage_children=base.coverage_children,
        evaluation_children=base.evaluation_children,
        holdout_target_source=source_reference,
    )
    children[source_path] = source_payload

    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)
    verified = verify_r2_oof_bundle(manifest_path)

    assert verified.holdout_target_source == source_reference


def test_oof_bundle_rejects_an_untyped_holdout_target_source_child(tmp_path: Path) -> None:
    base, children = _bundle_and_children()
    source_id = hashlib.sha256(b"holdout-source").hexdigest()
    source_path = "holdout/target-source.json"
    payload: dict[str, object] = {
        "contract": R2HoldoutTargetSource.CONTRACT,
        "schema_version": 1,
        "source_id": source_id,
    }
    reference = ArtifactReference(
        R2HoldoutTargetSource.CONTRACT,
        source_id,
        source_path,
        hashlib.sha256(canonical_bytes(payload)).hexdigest(),
    )
    bundle = R2OofBundle.create(
        foundation_bundle_id=base.foundation_bundle_id,
        experiment_configuration_id=base.experiment_configuration_id,
        source_class=base.source_class,
        evidence_class=base.evidence_class,
        feature_children=base.feature_children,
        preprocessing_children=base.preprocessing_children,
        fit_children=base.fit_children,
        forecast_manifests=base.forecast_manifests,
        coverage_children=base.coverage_children,
        evaluation_children=base.evaluation_children,
        holdout_target_source=reference,
    )
    children[source_path] = payload
    manifest_path = write_r2_oof_bundle(tmp_path, bundle, children)

    with pytest.raises(ValueError, match="target source"):
        verify_r2_oof_bundle(manifest_path)


def test_bundle_rejects_unsafe_paths_and_duplicate_cross_category_children() -> None:
    with pytest.raises(ValueError, match="unsafe path"):
        ArtifactReference("child-v1", "a" * 64, "../child.json", "b" * 64)

    reference, _ = _child("child.json", "same")
    with pytest.raises(ValueError, match="duplicate identities"):
        R2OofBundle.create(
            foundation_bundle_id="a" * 64,
            experiment_configuration_id="b" * 64,
            source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
            evidence_class=EvidenceClass.IMPLEMENTATION,
            feature_children=(reference,),
            preprocessing_children=(reference,),
            fit_children=(),
            forecast_manifests=(),
            coverage_children=(),
            evaluation_children=(),
        )


def test_create_only_output_rejects_ancestor_symlink_and_oversize_payload(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        atomic_create(link / "child.json", b"{}")
    assert not (outside / "child.json").exists()

    with pytest.raises(ValueError, match="64 MiB"):
        atomic_create(tmp_path / "large.json", b"x" * (64 * 1024 * 1024 + 1))


def test_forecast_manifest_identity_ignores_child_layout_and_digest() -> None:
    first_child = ArtifactReference(
        "qtrad-research-forecasts-v1",
        "a" * 64,
        "forecast/first.json",
        "b" * 64,
    )
    second_child = ArtifactReference(
        first_child.contract,
        first_child.semantic_id,
        "alternate/forecast.json",
        "c" * 64,
    )
    first = R2ForecastManifest.create(
        forecast_dataset_id="d" * 64,
        experiment_configuration_id="e" * 64,
        source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        forecast_child=first_child,
    )
    second = R2ForecastManifest.create(
        forecast_dataset_id=first.forecast_dataset_id,
        experiment_configuration_id=first.experiment_configuration_id,
        source_class=first.source_class,
        evidence_class=first.evidence_class,
        forecast_child=second_child,
    )
    assert second.manifest_id == first.manifest_id
    child_payload = second.as_json()["forecast_child"]
    assert isinstance(child_payload, dict)
    assert child_payload["path"] == "alternate/forecast.json"


def test_oof_id_is_semantic_and_closure_id_binds_physical_children() -> None:
    base, _children = _bundle_and_children()

    def physical_reference(reference: ArtifactReference) -> ArtifactReference:
        return ArtifactReference(
            reference.contract,
            reference.semantic_id,
            f"alternate/{reference.path}",
            "e" * 64,
        )

    alternate = R2OofBundle.create(
        foundation_bundle_id=base.foundation_bundle_id,
        experiment_configuration_id=base.experiment_configuration_id,
        source_class=base.source_class,
        evidence_class=base.evidence_class,
        feature_children=tuple(physical_reference(item) for item in base.feature_children),
        preprocessing_children=tuple(
            physical_reference(item) for item in base.preprocessing_children
        ),
        fit_children=tuple(physical_reference(item) for item in base.fit_children),
        forecast_manifests=tuple(physical_reference(item) for item in base.forecast_manifests),
        coverage_children=tuple(physical_reference(item) for item in base.coverage_children),
        evaluation_children=tuple(physical_reference(item) for item in base.evaluation_children),
    )
    assert alternate.oof_id == base.oof_id
    assert alternate.closure_id != base.closure_id

    different_parent = R2OofBundle.create(
        foundation_bundle_id="f" * 64,
        experiment_configuration_id=base.experiment_configuration_id,
        source_class=base.source_class,
        evidence_class=base.evidence_class,
        feature_children=base.feature_children,
        preprocessing_children=base.preprocessing_children,
        fit_children=base.fit_children,
        forecast_manifests=base.forecast_manifests,
        coverage_children=base.coverage_children,
        evaluation_children=base.evaluation_children,
    )
    assert different_parent.oof_id == base.oof_id
    assert different_parent.closure_id != base.closure_id


def test_synthetic_oof_build_is_replayed_from_typed_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _build_synthetic_oof(tmp_path / "oof")
    monkeypatch.setattr(
        verification,
        "runtime_identities",
        lambda: {
            "application_identity": "current-drifted-application",
            "image_identity": "sha256:" + "f" * 64,
            "python_identity": "current-drifted-python",
            "numpy_identity": "current-drifted-numpy",
            "sklearn_identity": "current-drifted-sklearn",
        },
    )
    selection_freeze(
        oof_bundle_path=manifest_path,
        frozen_by="synthetic-test",
        output=tmp_path / "selection.json",
    )
    assert (
        verify_oof_bundle(manifest_path).source_class
        is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
    )


def test_oof_semantic_receipt_is_replayed_once_and_authenticated_cheaply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verification,
        "runtime_identities",
        lambda: {
            "application_identity": "fixture-application",
            "image_identity": "sha256:" + "1" * 64,
            "python_identity": "fixture-python",
            "numpy_identity": "fixture-numpy",
            "sklearn_identity": "fixture-sklearn",
        },
    )
    manifest_path = _build_synthetic_oof(tmp_path / "oof")
    receipt_path = tmp_path / "oof-receipt.json"
    replay_calls = 0
    original_replay = verification._replay_synthetic_oof

    def count_replay(path: Path) -> None:
        nonlocal replay_calls
        replay_calls += 1
        original_replay(path)

    monkeypatch.setattr(verification, "_replay_synthetic_oof", count_replay)
    verify_r2_oof_semantics(manifest_path, receipt_output=receipt_path)
    assert replay_calls == 1
    assert receipt_path.is_file()
    with pytest.raises(FileExistsError):
        verify_r2_oof_semantics(manifest_path, receipt_output=receipt_path)
    assert replay_calls == 1

    def reject_replay(_: Path) -> None:
        raise AssertionError("ordinary OOF authentication replayed semantic work")

    monkeypatch.setattr(verification, "_replay_synthetic_oof", reject_replay)
    assert authenticate_r2_oof(manifest_path, receipt=receipt_path).oof_id

    receipt_payload = cast(dict[str, object], json.loads(receipt_path.read_bytes()))
    receipt_payload["oof_id"] = "0" * 64
    receipt_identity = {
        key: value for key, value in receipt_payload.items() if key != "verification_id"
    }
    receipt_payload["verification_id"] = hashlib.sha256(
        json.dumps(receipt_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    mutated_receipt = tmp_path / "mutated-receipt.json"
    mutated_receipt.write_bytes(canonical_bytes(receipt_payload))
    with pytest.raises(ValueError, match="binding"):
        authenticate_r2_oof(manifest_path, receipt=mutated_receipt)

    bundle = verify_r2_oof_bundle(manifest_path)
    descriptor_ref = next(
        reference
        for reference in bundle.evaluation_children
        if reference.contract == verification.OOF_DESCRIPTOR_CONTRACT
    )
    descriptor_path = manifest_path.parent / descriptor_ref.path
    descriptor_bytes = descriptor_path.read_bytes()
    descriptor_path.write_bytes(descriptor_bytes + b"\\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        authenticate_r2_oof(manifest_path, receipt=receipt_path)
    descriptor_path.write_bytes(descriptor_bytes)

    (manifest_path.parent / "replay-inputs" / "research").mkdir(parents=True)
    with pytest.raises(ValueError, match="orphaned directory"):
        authenticate_r2_oof(manifest_path, receipt=receipt_path)


def test_oof_rejects_empty_orphan_directories(tmp_path: Path) -> None:
    bundle, children = _bundle_and_children()
    root = tmp_path / "oof"
    manifest_path = write_r2_oof_bundle(root, bundle, children)
    (root / "replay-inputs" / "research").mkdir(parents=True)
    with pytest.raises(ValueError, match="orphaned directory"):
        verify_r2_oof_bundle(manifest_path)



def test_bounded_holdout_source_round_trip_is_create_only(tmp_path: Path) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"

    manifest = write_r2_holdout_target_source(output, source)

    assert manifest["storage"] == "qtrad-r2-holdout-target-source-bounded-parts-v1"
    assert load_r2_holdout_target_source(output).source_id == source.source_id
    part_paths = bounded_manifest_part_paths(output)
    assert part_paths
    assert all(
        (output.parent / path).stat().st_size <= 64 * 1024 * 1024 for path in part_paths
    )
    with pytest.raises(FileExistsError):
        write_r2_holdout_target_source(output, source)


def test_bounded_holdout_source_rejects_undeclared_part(tmp_path: Path) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"
    write_r2_holdout_target_source(output, source)
    orphan = output.parent / f"{output.name}.parts/targets/orphan.json"
    orphan.write_bytes(canonical_bytes({"contract": "orphan"}))

    with pytest.raises(ValueError, match="undeclared part"):
        load_r2_holdout_target_source(output)



def test_bounded_partitions_are_deterministic() -> None:
    rows = cast(tuple[JsonValue, ...], tuple({"value": str(index)} for index in range(7)))

    first = _split_part_rows(source_id="source", kind="targets", rows=rows)
    second = _split_part_rows(source_id="source", kind="targets", rows=rows)

    assert first == second
    assert sum(len(cast(list[object], payload["rows"])) for payload, _ in first) == len(rows)


def test_bounded_source_rejects_noncanonical_part_path(tmp_path: Path) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"
    write_r2_holdout_target_source(output, source)

    payload = cast(dict[str, object], json.loads(output.read_bytes()))
    target_parts = cast(list[dict[str, object]], payload["target_parts"])
    target_parts[0]["path"] = f"{output.name}.parts/targets/part-weird.json"
    output.write_bytes(canonical_bytes(payload))

    with pytest.raises(ValueError, match="canonical"):
        load_r2_holdout_target_source(output)


def test_bounded_source_rejects_special_part_entry(tmp_path: Path) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"
    write_r2_holdout_target_source(output, source)
    fifo = output.parent / f"{output.name}.parts/targets/fifo"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="non-regular"):
        load_r2_holdout_target_source(output)


def test_bounded_source_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"
    write_r2_holdout_target_source(output, source)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        load_r2_holdout_target_source(alias / output.name)


def test_bounded_source_consumes_manifest_and_parts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"
    write_r2_holdout_target_source(output, source)
    part_paths = tuple(output.parent / path for path in bounded_manifest_part_paths(output))
    reads: dict[Path, int] = {}
    original_read_bytes = Path.read_bytes

    def count_reads(path: Path) -> bytes:
        reads[path] = reads.get(path, 0) + 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_reads)
    assert load_r2_holdout_target_source(output).source_id == source.source_id
    assert reads[output] == 1
    assert all(reads[path] == 1 for path in part_paths)
    assert set(reads) == {output, *part_paths}


def test_bounded_source_splits_with_test_scaled_part_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    monkeypatch.setattr(holdout_source_runtime, "_MAX_PART_BYTES", 900)
    output = tmp_path / "target-source.json"

    manifest = write_r2_holdout_target_source(output, source)
    target_parts = cast(list[object], manifest["target_parts"])
    assert len(target_parts) == 2
    assert load_r2_holdout_target_source(output).source_id == source.source_id


def test_bounded_source_rejects_oversized_singleton_with_test_scaled_part_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    monkeypatch.setattr(holdout_source_runtime, "_MAX_PART_BYTES", 700)

    with pytest.raises(ValueError, match="row exceeds"):
        write_r2_holdout_target_source(tmp_path / "target-source.json", source)


def test_bounded_source_rejects_tampered_or_missing_part(tmp_path: Path) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"
    write_r2_holdout_target_source(output, source)
    part_path = output.parent / next(iter(bounded_manifest_part_paths(output)))
    original = part_path.read_bytes()
    part_path.write_bytes(original + b"\n")

    with pytest.raises(ValueError, match="digest or size"):
        load_r2_holdout_target_source(output)

    part_path.unlink()
    with pytest.raises(ValueError, match="unavailable"):
        load_r2_holdout_target_source(output)


def test_bounded_source_cleans_partial_parts_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = R2HoldoutTargetSource.from_json(_holdout_source_payload())
    output = tmp_path / "target-source.json"
    original_atomic_create = holdout_source_runtime.atomic_create
    calls = 0

    def fail_after_first(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("fixture write failure")
        original_atomic_create(path, content)

    monkeypatch.setattr(holdout_source_runtime, "atomic_create", fail_after_first)
    with pytest.raises(RuntimeError, match="fixture write failure"):
        write_r2_holdout_target_source(output, source)
    assert not output.exists()
    assert not (tmp_path / "target-source.json.parts").exists()
