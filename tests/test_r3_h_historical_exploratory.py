"""Focused R3.H Stage 1 freeze and micro-run tests."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import runpy
import sys
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import pytest

from qtrad.application.r3_historical_exploratory import (
    FixtureMeasurement,
    FreezeConfig,
    FreezeError,
    MicroRun,
    analyse_fixture,
    canonical_report_semantic_identity,
    render_markdown,
    synthetic_fixture,
)
from qtrad.runtime.r2_bundles import canonical_bytes as runtime_canonical_bytes
from qtrad.runtime.r2_holdout import _compact_header_digest, _verify_compact_header
from qtrad.runtime.r2_partitioned_rows import _MAX_PART_BYTES as runtime_max_part_bytes

CONFIG = Path("docs/archive/r3/r3-historical-exploratory-freeze.json")


def _rehashed(document: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in document.items() if key != "semantic_identity"}
    document["semantic_identity"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return document


def test_compact_partitioned_bytes_match_runtime_writer_with_unicode() -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    header: dict[str, object] = {
        "contract": "qtrad-r2-holdout-forecast-dataset-v1",
        "target_instrument_id": "βeta",
        "storage": "qtrad-r2-partitioned-json-rows-v1",
        "identity_field": "dataset_id",
        "row_count": 1,
        "parts": [],
    }
    header["header_sha256"] = _compact_header_digest(header)

    encoded = implementation._canonical_bytes(header)
    assert encoded == runtime_canonical_bytes(header)
    assert b"\\u03b2" in encoded
    assert (
        encoded
        != (
            json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode()
    )

    _verify_compact_header(header, encoded=encoded)
    with pytest.raises(ValueError, match="not canonical"):
        _verify_compact_header(
            header,
            encoded=(
                json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            ).encode(),
        )


def test_freeze_is_deterministic_and_rejects_unknown_expansion_and_mutation() -> None:
    first = FreezeConfig.from_path(CONFIG)
    second = FreezeConfig.from_path(CONFIG)
    assert first.semantic_identity == second.semantic_identity
    assert first.canonical_json() == second.canonical_json()

    unknown = json.loads(first.canonical_json())
    unknown["unexpected"] = True
    with pytest.raises(FreezeError, match="unknown keys"):
        FreezeConfig.from_mapping(unknown)

    expanded = json.loads(first.canonical_json())
    expanded["nonlinear_candidates"].append(
        {"id": "extra", "family": "tree", "degree": 3, "enabled": True}
    )
    with pytest.raises(FreezeError, match="candidate expansion"):
        FreezeConfig.from_mapping(expanded)

    mutations = (
        ("cost_grid", lambda value: value[0].__setitem__("value", 0.002)),
        ("targets", lambda value: value.__setitem__(0, "MUTATED_TARGET")),
        (
            "statistical_formulations",
            lambda value: value.__setitem__("metrics", ["mutated_metric"]),
        ),
        (
            "retained_parents",
            lambda value: value.__setitem__("stage7_semantic_id", "mutated-parent"),
        ),
        (
            "nonlinear_candidates",
            lambda value: value[0].__setitem__("degree", 7),
        ),
        ("tiny_graph_candidate", lambda value: value.__setitem__("hidden_units", 8)),
        ("compute_limits", lambda value: value.__setitem__("max_rows", 63)),
        (
            "output_contract",
            lambda value: value.__setitem__("post_result_expansion", True),
        ),
    )
    for section, mutate in mutations:
        candidate = json.loads(first.canonical_json())
        mutate(candidate[section])
        with pytest.raises(FreezeError):
            FreezeConfig.from_mapping(_rehashed(candidate))


def test_fixture_micro_run_covers_economic_statistical_graph_and_labels() -> None:
    result = analyse_fixture(synthetic_fixture(), FreezeConfig.from_path(CONFIG))
    report = result.report
    assert {"economic", "statistical", "graph"} <= report.keys()
    assert len(report["economic"]["all_in_cost_sensitivity"]) == 4
    assert {"asset", "horizon", "period"} <= report["economic"].keys()
    assert report["economic"]["asset"]["fx:aud-usd"]["turnover"] == 0.0126
    assert report["economic"]["asset"]["fx:eur-usd"]["turnover"] == 0.0216
    assert report["economic"]["all_in_cost_sensitivity"][0]["break_even_cost"] is not None
    assert report["statistical"]["oof"]["causal"] is True
    assert report["statistical"]["oof"]["support"] == 18
    assert report["statistical"]["oof"]["coverage"] == 1.0
    assert report["statistical"]["negative_failed_inconclusive_rendered"] is True
    assert report["graph"]["tiny_learned_graph"]["feasibility_only"] is True
    assert report["graph"]["r4_replacement_required"] is True
    assert {control["id"] for control in report["graph"]["controls"]} == {
        "local_non_graph",
        "pooled_non_graph",
        "fixed_graph",
        "shuffled_graph",
    }
    for control in report["graph"]["controls"]:
        assert {"status", "mse", "rank_correlation", "coverage", "support"} <= control.keys()
    assert report["retained_parents"]["authentication_performed"] is False
    assert report["retained_parents"]["outcome_decode_performed"] is False
    assert report["claims"] == [
        "midpoint_only",
        "historical_exploratory",
        "implementation_evidence_only",
        "not_executable_evidence",
        "no_effectiveness_claim",
    ]


def test_synchronised_timestamps_are_allowed_but_duplicate_identity_fails() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    rows[1] = replace(
        rows[1],
        timestamp=rows[0].timestamp,
        asset=rows[0].asset,
        horizon_minutes=30,
    )
    result = analyse_fixture(rows, config)
    assert result.report["statistical"]["oof"]["rows"] == 18

    rows[1] = replace(
        rows[1],
        target_id=rows[0].target_id,
        group=rows[0].group,
        horizon_minutes=rows[0].horizon_minutes,
        period=rows[0].period,
        asset=rows[0].asset,
    )
    with pytest.raises(FreezeError, match="duplicate decision identity"):
        analyse_fixture(rows, config)


def test_work_and_resource_limits_fail_closed() -> None:
    config = FreezeConfig.from_path(CONFIG)
    base = synthetic_fixture()[0]
    too_many_rows = tuple(
        replace(
            base,
            timestamp=f"2026-02-01T00:{index:02}:00Z",
            decision_time=f"2026-02-01T00:{index:02}:00Z",
            period=f"period-{index}",
        )
        for index in range(65)
    )
    with pytest.raises(FreezeError, match="max_rows"):
        analyse_fixture(too_many_rows, config)

    with pytest.raises(FreezeError, match="max_elapsed_seconds"):
        analyse_fixture(
            synthetic_fixture(),
            config,
            measurement=FixtureMeasurement(elapsed_seconds=61, memory_mb=1),
        )
    with pytest.raises(FreezeError, match="max_memory_mb"):
        analyse_fixture(
            synthetic_fixture(),
            config,
            measurement=FixtureMeasurement(elapsed_seconds=1, memory_mb=513),
        )


def _control(report: Mapping[str, Any], control_id: str) -> dict[str, Any]:
    return next(control for control in report["graph"]["controls"] if control["id"] == control_id)


def _simple_control(report: Mapping[str, Any], control_id: str) -> dict[str, Any]:
    return next(
        control
        for control in report["statistical"]["simple_controls"]
        if control["id"] == control_id
    )


def test_pooled_and_graph_controls_are_causal_and_use_frozen_graph() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    rows[1] = replace(rows[1], timestamp=rows[0].timestamp, asset="B")
    baseline = analyse_fixture(tuple(rows), config).report
    future = replace(
        rows[-1],
        timestamp="2026-01-01T00:15:00Z",
        decision_time="2026-01-01T00:15:00Z",
        period="period-future",
        prediction=9.0,
        realised_return=0.9,
    )
    extended = analyse_fixture((*rows, future), config).report

    baseline_pooled = _simple_control(baseline, "pooled_local_ridge")["prediction_trace"]
    extended_pooled = _simple_control(extended, "pooled_local_ridge")["prediction_trace"]
    assert extended_pooled[: len(rows)] == baseline_pooled

    baseline_tiny = baseline["graph"]["tiny_learned_graph"]["prediction_trace"]
    extended_tiny = extended["graph"]["tiny_learned_graph"]["prediction_trace"]
    assert extended_tiny[: len(rows)] == baseline_tiny
    assert baseline["graph"]["tiny_learned_graph"]["layers"] == 1
    assert baseline["graph"]["tiny_learned_graph"]["hidden_units"] == 4
    assert baseline["work"]["graph_fit_count"] == 1
    assert baseline["graph"]["tiny_learned_graph"]["walk_forward_fit_executions"] == 1

    fixed = _control(baseline, "fixed_graph")["prediction_trace"]
    shuffled = _control(baseline, "shuffled_graph")["prediction_trace"]
    assert fixed[0] == pytest.approx(0.032)
    assert shuffled[0] == pytest.approx(0.04)


def test_report_carries_frozen_parent_identities_and_code_provenance() -> None:
    config = FreezeConfig.from_path(CONFIG)
    report = analyse_fixture(synthetic_fixture(), config).report
    retained = config.document["retained_parents"]
    identities = report["retained_parents"]["identities"]
    for key in (
        "selection_manifest_id",
        "consumed_marker_id",
        "g2_manifest_id",
        "local_forecast_dataset_id",
        "pooled_forecast_dataset_id",
        "zero_forecast_dataset_id",
        "outcome_evidence_manifest_id",
        "terminal_report_sha256",
        "terminal_approval_sha256",
    ):
        assert identities[key] == retained[key]
    assert "authentication_chain" not in report["retained_parents"]
    assert "authentication_command" not in report["retained_parents"]
    provenance = report["code_provenance"]
    assert provenance["application_contract"] == "qtrad-r3-historical-exploratory-implementation-v2"
    assert len(provenance["module_sha256"]) == 64
    assert len(provenance["python_version"].split(".")) == 3


def test_dependency_maturity_and_overlap_are_purged_causally() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    late_outcome = replace(rows[0], target_available_at="2026-01-01T00:20:00Z")
    late_report = analyse_fixture(tuple([late_outcome, *rows[1:]]), config).report
    assert late_report["statistical"]["oof"]["folds"][0]["embargoed_rows"] >= 1

    overlap = replace(rows[0], dependency_end="2026-01-01T00:06:00Z")
    overlap_report = analyse_fixture(tuple([overlap, *rows[1:]]), config).report
    assert overlap_report["statistical"]["oof"]["folds"][0]["purged_rows"] >= 1


def test_micro_fixture_shape_is_exact_and_fit_counts_are_executions() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = synthetic_fixture()
    assert len(rows) == 18
    assert len({row.target_id for row in rows}) == 6
    assert len({row.group for row in rows}) == 3
    assert len({row.decision_time for row in rows}) == 3
    report = analyse_fixture(rows, config).report
    assert report["work"]["fit_count"] == 3
    assert report["work"]["fit_executions"] == {
        "linear_ridge": 0,
        "linear_zero_return": 0,
        "nonlinear_huber": 1,
        "pooled_local_ridge": 1,
        "tiny_learned_graph": 1,
    }


def test_economic_positions_derive_signed_deltas_and_costs() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    base = analyse_fixture(tuple(rows), config).report
    assert "target_position_change" not in config.document["retained_loader"]["required_columns"]
    linear = base["economic"]["configurations"]["linear_ridge"]
    aud_trace = [item for item in linear["position_trace"] if item["target_id"] == "fx:aud-usd"]
    assert aud_trace[0]["target_position"] == rows[0].prediction
    assert aud_trace[0]["target_position_change"] == rows[0].prediction
    assert aud_trace[1]["target_position_change"] == pytest.approx(
        rows[6].prediction - rows[0].prediction
    )
    assert aud_trace[0]["realised_gross"] == pytest.approx(
        rows[0].prediction * rows[0].realised_return
    )

    assert base["retained_parents"]["outcome_decode_performed"] is False

    signed = list(rows)
    aud_rows = [index for index, row in enumerate(signed) if row.target_id == "fx:aud-usd"]
    for index, prediction in zip(aud_rows, (-0.2, 0.0, 0.2), strict=True):
        signed[index] = replace(signed[index], prediction=prediction)
    signed_report = analyse_fixture(tuple(signed), config).report
    signed_linear = signed_report["economic"]["configurations"]["linear_ridge"]
    signed_trace = [
        item for item in signed_linear["position_trace"] if item["target_id"] == "fx:aud-usd"
    ]
    assert [item["target_position_change"] for item in signed_trace] == [
        pytest.approx(-0.2),
        pytest.approx(0.2),
        pytest.approx(0.2),
    ]
    signed_asset = signed_linear["asset"]["fx:aud-usd"]
    assert signed_asset["turnover"] == pytest.approx(0.6)
    signed_gross = sum(item["realised_gross"] for item in signed_trace)
    assert signed_asset["gross_total"] == pytest.approx(signed_gross)
    assert signed_asset["break_even_cost"] == pytest.approx(signed_gross / 0.6)
    first_cost = signed_linear["all_in_cost_sensitivity"][0]
    assert first_cost["net_mean"] == pytest.approx(
        signed_linear["gross_mean"] - first_cost["cost"] * signed_linear["turnover"] / len(signed)
    )

    flat = list(rows)
    for index in aud_rows:
        flat[index] = replace(flat[index], prediction=0.0)
    flat_report = analyse_fixture(tuple(flat), config).report
    flat_asset = flat_report["economic"]["configurations"]["linear_ridge"]["asset"]["fx:aud-usd"]
    assert flat_asset["turnover"] == 0.0
    assert flat_asset["break_even_cost"] is None


def test_causal_model_diagnostics_mask_prefit_rows() -> None:
    report = analyse_fixture(synthetic_fixture(), FreezeConfig.from_path(CONFIG)).report
    candidates = {item["id"]: item for item in report["statistical"]["candidates"]}
    controls = {item["id"]: item for item in report["statistical"]["simple_controls"]}
    graph_controls = {item["id"]: item for item in report["graph"]["controls"]}
    assert set(candidates) == {"linear_ridge", "linear_zero_return", "nonlinear_huber"}
    assert {
        "status",
        "mse",
        "rank_correlation",
        "coverage",
        "support",
        "prediction_trace",
        "prediction_mask",
        "fit_executions",
        "training_rows",
        "fit_evaluation_time",
    } <= candidates["nonlinear_huber"].keys()
    assert candidates["nonlinear_huber"]["status"] == "FAILED"
    assert candidates["nonlinear_huber"]["support"] == 12
    assert candidates["nonlinear_huber"]["coverage"] == pytest.approx(2 / 3)
    assert candidates["nonlinear_huber"]["prediction_trace"][:6] == [None] * 6
    assert candidates["nonlinear_huber"]["prediction_mask"] == [False] * 6 + [True] * 12
    assert controls["pooled_local_ridge"]["support"] == 12
    assert controls["pooled_local_ridge"]["coverage"] == pytest.approx(2 / 3)
    assert controls["pooled_local_ridge"]["prediction_trace"][:6] == [None] * 6
    pooled_graph = graph_controls["pooled_non_graph"]
    assert pooled_graph["support"] == 12
    assert pooled_graph["coverage"] == pytest.approx(2 / 3)
    assert pooled_graph["prediction_trace"][:6] == [None] * 6
    assert pooled_graph["prediction_mask"] == [False] * 6 + [True] * 12
    assert pooled_graph["mse"] == controls["pooled_local_ridge"]["mse"]
    assert pooled_graph["rank_correlation"] == controls["pooled_local_ridge"]["rank_correlation"]


def test_future_maturity_mutation_does_not_rewrite_earlier_predictions() -> None:
    config = FreezeConfig.from_path(CONFIG)
    rows = synthetic_fixture()
    baseline = analyse_fixture(rows, config).report
    mutated_last = replace(
        rows[-1],
        target_available_at="2026-01-01T23:00:00Z",
        dependency_end="2026-01-01T23:00:00Z",
        realised_return=99.0,
    )
    mutated = analyse_fixture((*rows[:-1], mutated_last), config).report
    baseline_pooled = _simple_control(baseline, "pooled_local_ridge")["prediction_trace"]
    mutated_pooled = _simple_control(mutated, "pooled_local_ridge")["prediction_trace"]
    assert mutated_pooled[:-1] == baseline_pooled[:-1]
    baseline_tiny = baseline["graph"]["tiny_learned_graph"]["prediction_trace"]
    mutated_tiny = mutated["graph"]["tiny_learned_graph"]["prediction_trace"]
    assert mutated_tiny[:-1] == baseline_tiny[:-1]


def test_each_economic_configuration_reports_subgroup_cost_sensitivity() -> None:
    report = analyse_fixture(synthetic_fixture(), FreezeConfig.from_path(CONFIG)).report
    for economic in report["economic"]["configurations"].values():
        assert len(economic["all_in_cost_sensitivity"]) == 4
        for dimension in ("asset", "horizon", "period"):
            assert set(economic[dimension])
            for subgroup in economic[dimension].values():
                sensitivity = subgroup["all_in_cost_sensitivity"]
                assert len(sensitivity) == 4
                assert all(
                    item["break_even_cost"] == subgroup["break_even_cost"] for item in sensitivity
                )
                assert all(item["unit"] == "fraction_of_notional" for item in sensitivity)


def test_outcome_blind_selector_rejects_duplicate_and_incomplete_groups() -> None:
    from qtrad.application.r3_historical_exploratory import select_synchronised_rows

    config = FreezeConfig.from_path(CONFIG)
    rows = synthetic_fixture()
    with pytest.raises(FreezeError, match="duplicate"):
        select_synchronised_rows((*rows, rows[0]), config)
    with pytest.raises(FreezeError, match="incomplete"):
        select_synchronised_rows(rows[:-1], config)


def test_fixture_loader_rejects_unknown_fields_and_retained_locator_mismatch(
    tmp_path: Path,
) -> None:
    from qtrad.application.r3_historical_exploratory import fixture_from_json, load_retained_rows

    fixture_path = tmp_path / "unknown.json"
    first = synthetic_fixture()[0]
    row = {field: getattr(first, field) for field in first.__dataclass_fields__}
    row["unexpected"] = True
    fixture_path.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(FreezeError, match="fields"):
        fixture_from_json(fixture_path)
    config = FreezeConfig.from_path(CONFIG)
    locators = {key: str(tmp_path / key) for key in config.document["retained_loader"]["locators"]}
    with pytest.raises(FreezeError, match="locator"):
        load_retained_rows(config, locators=locators)


def test_algorithm_parameters_are_reported_and_consumed_from_frozen_config() -> None:
    config = FreezeConfig.from_path(CONFIG)
    report = analyse_fixture(synthetic_fixture(), config).report
    assert report["loader_contract"]["decode_policy"].startswith("stream all authenticated")
    algorithms = config.document["algorithms"]
    assert algorithms["ridge"]["regularisation"] == 0.0
    assert algorithms["huber"]["threshold"] == 0.02
    assert algorithms["graph"]["epochs"] == 8
    assert (
        report["graph"]["tiny_learned_graph"]["hidden_units"] == algorithms["graph"]["hidden_units"]
    )


def test_retained_child_identity_binding_is_strict() -> None:
    from qtrad.application.r3_historical_exploratory import _validate_child_metadata

    config = FreezeConfig.from_path(CONFIG)
    loader = config.document["retained_loader"]
    declaration = loader["child_wrappers"]["local_forecast"]
    metadata: dict[str, Any] = {key: None for key in declaration["physical_required_keys"]}
    metadata.update(
        {
            "contract": declaration["contract"],
            "dataset_id": declaration["identity"],
        }
    )
    _validate_child_metadata("local_forecast", metadata, loader)
    metadata["dataset_id"] = "wrong-dataset"
    with pytest.raises(FreezeError, match="identity mismatch"):
        _validate_child_metadata("local_forecast", metadata, loader)


def test_forecast_wrapper_schema_matches_producer_and_fixture_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation
    from qtrad.domain.market_data import MarketDataSourceClass
    from qtrad.domain.r2_holdout import HoldoutScope, R2HoldoutForecastDataset
    from qtrad.domain.r2_readiness import EvidenceClass

    opportunity_id = "c" * 64
    target_id = "d" * 64
    producer = R2HoldoutForecastDataset.create(
        selection_manifest_id="a" * 64,
        feature_dataset_id=None,
        configuration_id="b" * 64,
        final_fit_id=None,
        rows=(),
        expected_opportunity_ids=(opportunity_id,),
        opportunity_target_ids=((opportunity_id, target_id),),
        source_class=next(iter(MarketDataSourceClass)),
        evidence_class=next(iter(EvidenceClass)),
        holdout_scope=next(iter(HoldoutScope)),
    )
    producer_keys = tuple(producer.semantic_json())
    expected_wrapper_keys = (*producer_keys, "dataset_id")
    assert producer_keys[9] == "opportunity_target_ids"

    config = FreezeConfig.from_path(CONFIG)
    loader = config.document["retained_loader"]
    forecast_children = ("local_forecast", "pooled_forecast", "zero_forecast")
    for name in forecast_children:
        declaration = loader["child_wrappers"][name]
        assert tuple(declaration["required_keys"]) == expected_wrapper_keys

        metadata: dict[str, Any] = {key: None for key in declaration["physical_required_keys"]}
        metadata["contract"] = declaration["contract"]
        metadata["dataset_id"] = declaration["identity"]
        implementation._validate_child_metadata(name, metadata, loader)
        legacy_unknown = set(metadata) - (set(declaration["required_keys"]) | {"parts"})
        assert legacy_unknown == {
            "header_sha256",
            "identity_field",
            "partition_fields",
            "partition_mapping_fields",
            "partition_row_field",
            "row_count",
            "storage",
        }

        missing = dict(metadata)
        del missing["header_sha256"]
        with pytest.raises(FreezeError, match="incomplete"):
            implementation._validate_child_metadata(name, missing, loader)

        unknown = dict(metadata)
        unknown["unknown"] = True
        with pytest.raises(FreezeError, match="incomplete"):
            implementation._validate_child_metadata(name, unknown, loader)

    seen_fixture_metadata: dict[str, Mapping[str, Any]] = {}
    original_validator = implementation._validate_child_metadata

    def capture_fixture_metadata(
        name: str,
        metadata: Mapping[str, Any],
        fixture_loader: Mapping[str, Any],
        *,
        fixture: bool = False,
    ) -> None:
        if fixture and name in forecast_children:
            seen_fixture_metadata[name] = dict(metadata)
        original_validator(name, metadata, fixture_loader, fixture=fixture)

    monkeypatch.setattr(implementation, "_validate_child_metadata", capture_fixture_metadata)
    implementation.load_fixture_rows(synthetic_fixture(), config)

    assert set(seen_fixture_metadata) == set(forecast_children)
    for fixture_metadata in seen_fixture_metadata.values():
        assert "opportunity_target_ids" in fixture_metadata
        assert set(fixture_metadata) == set(
            loader["child_wrappers"]["local_forecast"]["physical_required_keys"]
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "header_digest",
        "storage",
        "register",
        "reference_keys",
        "reference_path",
        "reference_index",
        "reference_hash",
        "reference_count",
        "part_envelope",
        "part_lineage",
        "outcome_tag",
        "logical_record",
        "manifest_root",
        "role_swap",
        "oversized_part",
        "target_source_wrapper_symlink",
        "target_source_family_symlink",
        "target_source_part_symlink",
    ),
)
def test_compact_fixture_contract_mutations_fail_closed(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    config = FreezeConfig.from_path(CONFIG)
    original_loader = implementation.load_retained_rows

    def write_document(path: Path, document: dict[str, Any]) -> None:
        path.write_bytes(implementation._canonical_bytes(document))

    def mutate_part(
        fixture_root: Path,
        manifest: dict[str, Any],
        mutator: Callable[[dict[str, Any]], None],
    ) -> None:
        reference = manifest["parts"][0]
        part_path = fixture_root / reference["path"]
        envelope = json.loads(part_path.read_text(encoding="utf-8"))
        mutator(envelope)
        encoded = implementation._canonical_bytes(envelope)
        part_path.write_bytes(encoded)
        reference["sha256"] = hashlib.sha256(encoded).hexdigest()

    def mutated_loader(
        fixture_config: FreezeConfig,
        *,
        locators: Mapping[str, str] | None = None,
        _fixture: bool = False,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        assert _fixture
        assert locators is not None
        actual_locators = dict(locators)
        if mutation.startswith("target_source_"):
            native_manifest = Path(actual_locators["target_source"])
            if mutation == "target_source_wrapper_symlink":
                real_manifest = native_manifest.with_name(native_manifest.name + ".real")
                native_manifest.rename(real_manifest)
                native_manifest.symlink_to(real_manifest.name)
            else:
                family_name = "targets"
                family = native_manifest.parent / f"{native_manifest.name}.parts" / family_name
                if mutation == "target_source_part_symlink":
                    part = family / "part-000000.json"
                    real_part = part.with_name(part.name + ".real")
                    part.rename(real_part)
                    part.symlink_to(real_part.name)
                else:
                    real_family = family.with_name(family.name + ".real")
                    family.rename(real_family)
                    family.symlink_to(real_family.name, target_is_directory=True)
            return original_loader(fixture_config, locators=actual_locators, _fixture=True)
        if mutation == "manifest_root":
            actual_locators["local_forecast"] = str(
                Path(actual_locators["local_forecast"]).with_name("outside.json")
            )
        elif mutation == "role_swap":
            actual_locators["local_forecast"] = actual_locators["pooled_forecast"]
        elif mutation == "oversized_part":
            assert runtime_max_part_bytes == implementation._MAX_PART_BYTES
            manifest_path = Path(actual_locators["local_forecast"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            part_path = Path(actual_locators["selection"]).parent / manifest["parts"][0]["path"]
            original_lstat = os.lstat

            def oversized_lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
                actual_stat = original_lstat(path, *args, **kwargs)
                if not args and not kwargs and Path(path) == part_path:
                    return SimpleNamespace(
                        st_mode=actual_stat.st_mode,
                        st_size=runtime_max_part_bytes + 1,
                    )
                return actual_stat

            monkeypatch.setattr(os, "lstat", oversized_lstat)
        else:
            name = "outcome_evidence" if mutation == "outcome_tag" else "local_forecast"
            manifest_path = Path(actual_locators[name])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if mutation == "header_digest":
                manifest["header_sha256"] = "0" * 64
            elif mutation == "storage":
                manifest["storage"] = "other"
            elif mutation == "register":
                manifest["partition_mapping_fields"] = ["unexpected"]
            elif mutation == "reference_keys":
                del manifest["parts"][0]["path"]
            elif mutation == "reference_path":
                manifest["parts"][0]["path"] = "../escape.json"
            elif mutation == "reference_index":
                manifest["parts"][0]["part_index"] = 1
            elif mutation == "reference_hash":
                manifest["parts"][0]["sha256"] = "0" * 64
            elif mutation == "reference_count":
                manifest["parts"][0]["row_count"] = 0
            elif mutation == "part_envelope":
                mutate_part(
                    Path(actual_locators["selection"]).parent,
                    manifest,
                    lambda envelope: envelope.__setitem__("contract", "other"),
                )
            elif mutation == "part_lineage":
                mutate_part(
                    Path(actual_locators["selection"]).parent,
                    manifest,
                    lambda envelope: envelope.__setitem__("parent_semantic_id", "0" * 64),
                )
            elif mutation == "outcome_tag":
                mutate_part(
                    Path(actual_locators["selection"]).parent,
                    manifest,
                    lambda envelope: envelope["rows"][0].__setitem__("field", "other"),
                )
            elif mutation == "logical_record":
                mutate_part(
                    Path(actual_locators["selection"]).parent,
                    manifest,
                    lambda envelope: envelope["rows"][0]["value"].pop("asset"),
                )
            else:
                raise AssertionError(f"unhandled mutation: {mutation}")
            write_document(manifest_path, manifest)
        return original_loader(fixture_config, locators=actual_locators, _fixture=True)

    monkeypatch.setattr(implementation, "load_retained_rows", mutated_loader)
    with pytest.raises(FreezeError):
        implementation.load_fixture_rows(synthetic_fixture(), config)


def test_compact_partition_cardinality_matches_runtime_contract(tmp_path: Path) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    assert runtime_max_part_bytes == implementation._MAX_PART_BYTES
    empty_manifest = {"parts": [], "row_count": 0, "identity_field": "dataset_id"}
    assert (
        implementation._declared_partition_paths(
            tmp_path, "empty.json", empty_manifest, identity_field="dataset_id"
        )
        == []
    )

    with pytest.raises(ValueError, match="non-canonical"):
        implementation._declared_partition_paths(
            tmp_path,
            "zero.json",
            {
                "parts": [
                    {
                        "path": "zero.json.parts/part-000000.json",
                        "sha256": "a" * 64,
                        "row_count": 0,
                        "part_index": 0,
                    }
                ],
                "row_count": 0,
                "identity_field": "dataset_id",
            },
            identity_field="dataset_id",
        )

    part_bytes = b"{}"
    part_path = tmp_path / "normal.json.parts" / "part-000000.json"
    part_path.parent.mkdir()
    part_path.write_bytes(part_bytes)
    normal_manifest = {
        "parts": [
            {
                "path": "normal.json.parts/part-000000.json",
                "sha256": hashlib.sha256(part_bytes).hexdigest(),
                "row_count": 1,
                "part_index": 0,
            }
        ],
        "row_count": 1,
        "identity_field": "dataset_id",
    }
    assert implementation._declared_partition_paths(
        tmp_path, "normal.json", normal_manifest, identity_field="dataset_id"
    ) == ["normal.json.parts/part-000000.json"]


def test_parts_wrapper_validates_hash_and_reports_consumed_parts(tmp_path: Path) -> None:
    from qtrad.application.r3_historical_exploratory import _read_json_document

    limits = {
        "max_source_bytes": 100_000,
        "max_source_rows": 10,
        "max_row_bytes": 10_000,
        "max_nested_depth": 8,
        "max_part_rows": 3,
    }
    part_rows = [{"decision_time": "t", "target_id": "x"}]
    part_bytes = json.dumps(part_rows, separators=(",", ":")).encode()
    wrapper = {
        "contract": "test-wrapper",
        "parts": [
            {
                "locator": "part.json",
                "sha256": hashlib.sha256(part_bytes).hexdigest(),
                "contract": "test-part",
                "identity": "part-1",
                "byte_size": len(part_bytes),
                "row_count": len(part_rows),
            }
        ],
    }
    (tmp_path / "part.json").write_bytes(part_bytes)
    path = tmp_path / "wrapper.json"
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    metadata, rows, _ = _read_json_document(path, limits)
    assert len(rows) == 1
    assert metadata["consumed_parts"][0]["sha256"] == hashlib.sha256(part_bytes).hexdigest()
    (tmp_path / "part.json").write_bytes(b"[]")
    with pytest.raises(FreezeError, match="byte hash"):
        _read_json_document(path, limits)


def test_native_loader_relative_and_absolute_roots_share_authenticated_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    part_rows = [{"decision_time": "t", "target_id": "x"}]
    part_bytes = json.dumps(part_rows, separators=(",", ":")).encode()
    wrapper = {
        "contract": "test-wrapper",
        "parts": [
            {
                "locator": "part.json",
                "sha256": hashlib.sha256(part_bytes).hexdigest(),
                "contract": "test-part",
                "identity": "part-1",
                "byte_size": len(part_bytes),
                "row_count": len(part_rows),
            }
        ],
    }
    (tmp_path / "part.json").write_bytes(part_bytes)
    wrapper_path = tmp_path / "wrapper.json"
    wrapper_path.write_text(json.dumps(wrapper), encoding="utf-8")
    limits = {
        "max_source_bytes": 100_000,
        "max_source_rows": 10,
        "max_row_bytes": 10_000,
        "max_nested_depth": 8,
        "max_part_rows": 3,
    }

    absolute_result = implementation._read_json_document(wrapper_path, limits)
    monkeypatch.chdir(tmp_path.parent)
    relative_path = Path(tmp_path.name) / "wrapper.json"
    relative_result = implementation._read_json_document(relative_path, limits)
    assert relative_result == absolute_result

    alias = tmp_path.parent / "relative-root-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FreezeError):
        implementation._read_json_document(alias / "wrapper.json", limits)

    traversed_path = Path(tmp_path.name) / ".." / tmp_path.name / "wrapper.json"
    with pytest.raises(FreezeError):
        implementation._read_json_document(traversed_path, limits)


def test_native_compact_loader_relative_and_absolute_roots_share_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    logical_row = {"decision_time": "t", "target_id": "x"}
    envelope = {
        "contract": "qtrad-r2-partitioned-json-row-part-v1",
        "schema_version": 1,
        "parent_contract": "test-wrapper",
        "parent_semantic_id": "dataset-1",
        "part_index": 0,
        "rows": [{"value": logical_row}],
    }
    part_bytes = implementation._canonical_bytes(envelope)
    part_relative = "wrapper.json.parts/part-000000.json"
    (tmp_path / part_relative).parent.mkdir()
    (tmp_path / part_relative).write_bytes(part_bytes)
    manifest = {
        "contract": "test-wrapper",
        "dataset_id": "dataset-1",
        "storage": implementation._PARTITIONED_ROWS_STORAGE,
        "identity_field": "dataset_id",
        "row_count": 1,
        "parts": [
            {
                "path": part_relative,
                "sha256": hashlib.sha256(part_bytes).hexdigest(),
                "row_count": 1,
                "part_index": 0,
            }
        ],
        "partition_row_field": "value",
        "partition_fields": [],
        "partition_mapping_fields": [],
    }
    physical_fields = {"storage", "identity_field", "row_count", "parts", "header_sha256"}
    header = {key: value for key, value in manifest.items() if key not in physical_fields}
    manifest["header_sha256"] = hashlib.sha256(implementation._canonical_bytes(header)).hexdigest()
    wrapper_path = tmp_path / "wrapper.json"
    wrapper_bytes = implementation._canonical_bytes(manifest)
    wrapper_path.write_bytes(wrapper_bytes)
    limits = {
        "manifest_root": str(tmp_path),
        "manifest_relative_path": "wrapper.json",
        "max_source_bytes": 100_000,
        "physical_required_keys": list(manifest),
        "expected_identity_field": "dataset_id",
        "expected_wrapper_contract": "test-wrapper",
        "expected_wrapper_identity": "dataset-1",
        "partition_row_field": "value",
        "partition_fields": [],
        "partition_mapping_fields": [],
        "required_record_keys": ["decision_time", "target_id"],
        "max_consumed_parts": 2,
        "max_elapsed_seconds": 10,
        "max_part_rows": 3,
        "max_row_bytes": 10_000,
        "max_source_rows": 10,
        "_physical_budget": None,
    }

    absolute_metadata, absolute_iterator, _, _ = implementation._open_partitioned_json_document(
        wrapper_path, limits
    )
    absolute_parts = list(absolute_iterator)
    monkeypatch.chdir(tmp_path.parent)
    relative_limits = {**limits, "manifest_root": tmp_path.name}
    relative_path = Path(tmp_path.name) / "wrapper.json"
    relative_metadata, relative_iterator, _, _ = implementation._open_partitioned_json_document(
        relative_path, relative_limits
    )
    assert relative_metadata == absolute_metadata
    assert list(relative_iterator) == absolute_parts

    alias = tmp_path.parent / "compact-root-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FreezeError):
        implementation._open_partitioned_json_document(
            alias / "wrapper.json", {**limits, "manifest_root": "compact-root-alias"}
        )

    traversed_root = Path(tmp_path.name) / ".." / tmp_path.name
    with pytest.raises(FreezeError):
        implementation._open_partitioned_json_document(
            traversed_root / "wrapper.json", {**limits, "manifest_root": str(traversed_root)}
        )


def test_fixture_loader_scans_all_parts_before_earliest_selection() -> None:
    from qtrad.application.r3_historical_exploratory import load_fixture_rows

    config = FreezeConfig.from_path(CONFIG)
    fixture = synthetic_fixture()
    latest_decision = max(row.decision_time for row in fixture)
    no_order_fixture = tuple(
        replace(row, period=f"{row.period}-fixture-late-earlier")
        if row.decision_time == latest_decision
        else row
        for row in fixture
    )
    rows, metadata = load_fixture_rows(no_order_fixture, config)
    assert len(rows) == 18
    assert {row.decision_time for row in rows} >= {"1969-01-01T00:00:00Z"}
    assert metadata["authority"]["authentication_performed"] is False
    assert metadata["outcome_decode_performed"] is False
    assert metadata["selection"]["stop_state"] == "SCANNED_ALL_PARTS_REQUIRED_NO_ORDER_PROOF"
    assert all(state == "EXHAUSTED" for state in metadata["unopened_parts"].values())
    assert metadata["stop_reason"] == "FULL_SCAN_REQUIRED_NO_ORDER_PROOF"
    assert metadata["consumed_parts_count"] == 13
    assert {len(hashes) for hashes in metadata["source_scan_part_hashes"].values()} == {3, 4}
    report = analyse_fixture(rows, config, retained_metadata=metadata).report
    graph_receipts = {
        control["id"]: control["execution_receipt"] for control in report["graph"]["controls"]
    }
    assert (
        graph_receipts["local_non_graph"]["role_binding"]
        == metadata["role_bindings"]["LOCAL_RIDGE"]
    )
    assert (
        graph_receipts["pooled_non_graph"]["role_binding"]
        == metadata["role_bindings"]["POOLED_LOCAL_RIDGE"]
    )
    assert "role_binding" not in graph_receipts["fixed_graph"]
    assert "role_binding" not in graph_receipts["shuffled_graph"]
    assert metadata["selected_rows"] == 18
    assert metadata["consumed_rows"] > metadata["selected_rows"]
    assert metadata["selected_bytes"] < metadata["consumed_bytes"]
    assert metadata["selected_bytes_kind"] == "logical_serialised_fixture_row_bytes"
    assert len(metadata["source_scan_wrapper_bytes"]) == 6


def test_native_fixture_receipts_are_cumulative_and_outcome_values_do_not_select() -> None:
    from qtrad.application.r3_historical_exploratory import load_fixture_rows

    config = FreezeConfig.from_path(CONFIG)
    fixture = synthetic_fixture()
    selected, metadata = load_fixture_rows(fixture, config)
    mutated = tuple(replace(row, realised_return=row.realised_return + 0.25) for row in fixture)
    mutated_selected, mutated_metadata = load_fixture_rows(mutated, config)
    assert tuple(row.decision_time for row in selected) == tuple(
        row.decision_time for row in mutated_selected
    )
    assert (
        metadata["selection"]["selected_decision_times"]
        == mutated_metadata["selection"]["selected_decision_times"]
    )
    assert metadata["source_scan_parts"] > metadata["selected_groups"]
    assert metadata["source_scan_read_operations"] >= metadata["source_scan_parts"]
    assert metadata["source_scan_bytes"] == metadata["consumed_bytes"]
    assert metadata["source_limits"]["max_part_bytes"] == 536_870_912
    assert metadata["selection_state"]["full_payload_materialisation"] is False
    assert (
        metadata["selection_state"]["bounded_id_state_peak"]
        <= metadata["source_limits"]["max_rows"]
    )
    assert metadata["native_target_source"]["pre_holdout_parts_unopened"] == 1


def test_retained_cli_requires_target_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "r3_historical_exploratory.py",
            "--retained",
            "--selection",
            "selection.json",
            "--consumed",
            "consumed.json",
            "--local-forecast",
            "local.json",
            "--pooled-forecast",
            "pooled.json",
            "--zero-forecast",
            "zero.json",
            "--outcome-evidence",
            "outcome.json",
        ],
    )
    with pytest.raises(SystemExit) as error:
        runpy.run_path("ops/research/r3_historical_exploratory.py", run_name="__main__")
    assert error.value.code == 2


def test_retained_source_inventory_requires_exact_declared_and_scanned_bounds() -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    streaming = FreezeConfig.from_path(CONFIG).document["retained_loader"]["streaming_policy"]
    declared = {
        "local_forecast": {"row_count": 202_709, "parts": [None] * 25},
        "pooled_forecast": {"row_count": 202_709, "parts": [None] * 25},
        "zero_forecast": {"row_count": 202_709, "parts": [None] * 25},
        "outcome_evidence": {"row_count": 608_127, "parts": [None] * 75},
    }
    implementation._validate_declared_source_inventory(streaming, declared)
    declared["outcome_evidence"]["parts"].append(None)
    with pytest.raises(FreezeError, match="declared source inventory"):
        implementation._validate_declared_source_inventory(streaming, declared)

    part_bytes = [4_198_824, 2_476_431, *([2_476_354] * 148)]
    receipts = {
        "local_forecast": [
            {"physical_rows": 202_709 // 25, "bytes": size} for size in part_bytes[:25]
        ],
        "pooled_forecast": [
            {"physical_rows": 202_709 // 25, "bytes": size} for size in part_bytes[25:50]
        ],
        "zero_forecast": [
            {"physical_rows": 202_709 // 25, "bytes": size} for size in part_bytes[50:75]
        ],
        "outcome_evidence": [
            {"physical_rows": 608_127 // 75, "bytes": size} for size in part_bytes[75:]
        ],
    }
    with pytest.raises(FreezeError, match="scanned source inventory"):
        implementation._validate_scanned_source_inventory(streaming, receipts)

    rows = [202_709] * 3 + [608_127]
    for source, expected_rows in zip(receipts, rows, strict=True):
        for receipt in receipts[source]:
            receipt["physical_rows"] = 0
        receipts[source][0]["physical_rows"] = expected_rows
    implementation._validate_scanned_source_inventory(streaming, receipts)


def test_swapped_retained_role_records_fail_closed() -> None:
    from qtrad.application.r3_historical_exploratory import load_fixture_rows

    config = FreezeConfig.from_path(CONFIG)
    rows, metadata = load_fixture_rows(synthetic_fixture(), config)
    swapped = deepcopy(metadata)
    pooled = swapped["role_bindings"]["POOLED_LOCAL_RIDGE"]
    zero = swapped["role_bindings"]["ZERO_RETURN"]
    swapped["role_bindings"]["POOLED_LOCAL_RIDGE"] = zero
    swapped["role_bindings"]["ZERO_RETURN"] = pooled
    with pytest.raises(FreezeError, match="swapped"):
        analyse_fixture(rows, config, retained_metadata=swapped)


def test_create_only_writer_is_atomic_on_collision_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    script_path = Path(__file__).parents[1] / "ops/research/r3_historical_exploratory.py"
    spec = importlib.util.spec_from_file_location("r3_h_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    destination = tmp_path / "report.json"
    module._write_create_only(destination, "{}")
    with pytest.raises(FileExistsError):
        module._write_create_only(destination, '{"changed":true}')
    assert destination.read_text(encoding="utf-8") == "{}"

    def fail_link(_temporary: Path, _destination: Path) -> None:
        raise OSError("injected link failure")

    monkeypatch.setattr(module.os, "link", fail_link)
    failed = tmp_path / "failed.json"
    with pytest.raises(OSError, match="injected link failure"):
        module._write_create_only(failed, "{}")
    assert not failed.exists()
    assert not list(tmp_path.glob(".failed.json.*.tmp"))


def test_retained_forecast_role_bindings_reject_swapped_datasets() -> None:
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = document["retained_loader"]["identity_bindings"]
    assert bindings["dataset_ids"]["POOLED_LOCAL_RIDGE"].startswith("d2d07d40")
    assert bindings["dataset_ids"]["ZERO_RETURN"].startswith("93eb9453")
    assert bindings["wrapper_sha256s"]["POOLED_LOCAL_RIDGE"].startswith("e973e855")
    assert bindings["wrapper_sha256s"]["ZERO_RETURN"].startswith("bfba06f1")

    swapped = json.loads(CONFIG.read_text(encoding="utf-8"))
    swapped_bindings = swapped["retained_loader"]["identity_bindings"]
    (
        swapped_bindings["dataset_ids"]["POOLED_LOCAL_RIDGE"],
        swapped_bindings["dataset_ids"]["ZERO_RETURN"],
    ) = (
        swapped_bindings["dataset_ids"]["ZERO_RETURN"],
        swapped_bindings["dataset_ids"]["POOLED_LOCAL_RIDGE"],
    )
    with pytest.raises(FreezeError, match="role bindings"):
        FreezeConfig.from_mapping(_rehashed(swapped))


def test_retained_forecast_roles_are_explicit_and_not_reversed() -> None:
    from qtrad.application.r3_historical_exploratory import retained_input_paths

    config = FreezeConfig.from_path(CONFIG)
    loader = config.document["retained_loader"]
    bindings = loader["identity_bindings"]
    assert bindings["dataset_ids"]["POOLED_LOCAL_RIDGE"] == (
        "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b"
    )
    assert bindings["dataset_ids"]["ZERO_RETURN"] == (
        "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223"
    )
    paths = retained_input_paths()
    assert paths["pooled_forecast"].endswith(
        "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json"
    )
    assert paths["zero_forecast"].endswith(
        "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json"
    )


def test_selector_rejects_early_incomplete_group_and_requires_complete_count() -> None:
    from qtrad.application.r3_historical_exploratory import select_synchronised_rows

    config = FreezeConfig.from_path(CONFIG)
    rows = list(synthetic_fixture())
    early = replace(
        rows[0],
        timestamp="2025-12-31T23:00:00Z",
        decision_time="2025-12-31T23:00:00Z",
        period="early-incomplete",
        available_at="2025-12-31T22:59:00Z",
        target_available_at="2025-12-31T23:05:00Z",
        dependency_start="2025-12-31T22:55:00Z",
        dependency_end="2025-12-31T22:59:00Z",
    )
    with pytest.raises(FreezeError, match="incomplete"):
        select_synchronised_rows((*rows, early), config)

    with pytest.raises(FreezeError, match=r"incomplete|fewer than"):
        select_synchronised_rows(rows[:-1], config)


def test_role_swapped_retained_locator_fails_closed() -> None:
    candidate = json.loads(CONFIG.read_text(encoding="utf-8"))
    locators = candidate["retained_loader"]["locators"]
    locators["pooled_forecast"], locators["zero_forecast"] = (
        locators["zero_forecast"],
        locators["pooled_forecast"],
    )
    with pytest.raises(FreezeError):
        FreezeConfig.from_mapping(_rehashed(candidate))


def test_markdown_renderer_is_deterministic_complete_and_fail_closed() -> None:
    config = FreezeConfig.from_path(CONFIG)
    result = analyse_fixture(synthetic_fixture(), config)
    rendered = render_markdown(result, config)

    assert rendered == render_markdown(result, config)
    second_result = analyse_fixture(synthetic_fixture(), config)
    assert canonical_report_semantic_identity(result.report) == (
        canonical_report_semantic_identity(second_result.report)
    )

    provenance_changed = json.loads(result.canonical_json())
    changed_parents = provenance_changed["retained_parents"]
    changed_parents["paths"]["terminal_report"] = "/relocated/report.md"
    changed_candidate = provenance_changed["statistical"]["candidates"][0]
    changed_candidate["execution_receipt"]["wrapper_sha256"] = "f" * 64
    changed_candidate["execution_receipt"]["path"] = "/relocated/wrapper.py"
    assert canonical_report_semantic_identity(result.report) == canonical_report_semantic_identity(
        provenance_changed
    )
    changed_rendered = render_markdown(MicroRun(provenance_changed, result.work_count), config)
    assert ("f" * 64) in changed_rendered
    assert "/relocated/wrapper.py" in changed_rendered

    semantic_changed = json.loads(result.canonical_json())
    semantic_changed["economic"]["trace_id"] = "changed"
    assert canonical_report_semantic_identity(result.report) != canonical_report_semantic_identity(
        semantic_changed
    )
    assert canonical_report_semantic_identity(result.report) in rendered
    assert '"schema_version": 1' in rendered
    assert (
        '"elapsed_seconds": ' + str(result.report["work"]["measurement"]["elapsed_seconds"])
        in rendered
    )
    for heading in (
        "Machine-readable report identity",
        "Terminal authority and consumed child identities",
        "Frozen configuration and code identity",
        "Loader, selection, resources, and work counts",
        "Economic break-even and turnover sensitivity",
        "Chronological statistical and bounded nonlinear comparison",
        "Tiny graph/GNN feasibility and controls",
        "Negative, failed, and inconclusive outcomes",
        "Claim boundary",
        "Physical closure, execution, and resource provenance",
    ):
        assert f"## {heading}" in rendered
    for label in (
        "HISTORICAL_EXPLORATORY",
        "IBKR_HISTORICAL_RESEARCH",
        "MIDPOINT_OHLC",
        "nonlinear_huber",
        "local_non_graph",
        "pooled_non_graph",
        "fixed_graph",
        "shuffled_graph",
        "NEGATIVE",
        "FAILED",
        "INCONCLUSIVE",
        "no_effectiveness_claim",
        "no_executable_alpha_claim",
        "no_profitability_claim",
        "no_native_validity_claim",
        "no_promotion_claim",
        "no_order_claim",
    ):
        assert label in rendered
    assert json.loads(result.canonical_json())["contract"] == result.report["contract"]

    malformed_reports = []
    for section, malformed_value in (
        ("economic", None),
        ("claims", "not-a-list"),
        ("work", []),
    ):
        malformed = json.loads(result.canonical_json())
        malformed[section] = malformed_value
        malformed_reports.append(malformed)

    malformed = json.loads(result.canonical_json())
    malformed["unexpected"] = True
    malformed_reports.append(malformed)

    malformed = json.loads(result.canonical_json())
    malformed["economic"]["asset"] = None
    malformed_reports.append(malformed)

    malformed = json.loads(result.canonical_json())
    malformed["economic"]["all_in_cost_sensitivity"][0] = None
    malformed_reports.append(malformed)

    malformed = json.loads(result.canonical_json())
    malformed["statistical"]["oof"] = None
    malformed_reports.append(malformed)

    malformed = json.loads(result.canonical_json())
    malformed["statistical"]["candidates"] = None
    malformed_reports.append(malformed)

    malformed = json.loads(result.canonical_json())
    malformed["graph"]["controls"] = None
    malformed_reports.append(malformed)

    malformed = json.loads(result.canonical_json())
    malformed["graph"]["tiny_learned_graph"] = None
    malformed_reports.append(malformed)

    for malformed in malformed_reports:
        with pytest.raises(FreezeError, match="renderer"):
            render_markdown(MicroRun(malformed, result.work_count), config)

    with pytest.raises(FreezeError, match="schema mismatch"):
        render_markdown(MicroRun({}, {}), config)


def test_renderer_rejects_nested_schema_mutations() -> None:
    config = FreezeConfig.from_path(CONFIG)
    result = analyse_fixture(synthetic_fixture(), config)
    mutations: list[Callable[[dict[str, Any]], None]] = [
        lambda report: report["loader_contract"].__setitem__("unexpected", True),
        lambda report: report["target_group_resolution"].__setitem__("unexpected", True),
        lambda report: report["scale_projection"]["decoder_limits"].__setitem__("unexpected", True),
        lambda report: report["observation_contract"].__setitem__("unexpected", True),
        lambda report: report["retained_parents"]["paths"].__setitem__("unexpected", "x"),
        lambda report: report["selection"].__setitem__("selected_rows", "18"),
        lambda report: report["work"]["measurement"].__setitem__("elapsed_seconds", "slow"),
        lambda report: report["statistical"]["candidates"][0]["execution_receipt"].__setitem__(
            "unexpected", True
        ),
        lambda report: report["graph"]["controls"][0].__setitem__("unexpected", True),
        lambda report: report["economic"]["configurations"]["linear_ridge"][
            "all_in_cost_sensitivity"
        ].pop(),
        lambda report: report["statistical"]["candidates"][0]["execution_receipt"].__setitem__(
            "role_binding", {"dataset_id": "bad"}
        ),
        lambda report: report["retained_parents"]["role_bindings"].__setitem__("UNKNOWN", {}),
        lambda report: report["graph"]["tiny_learned_graph"]["algorithm"].__setitem__("unknown", 1),
        lambda report: report["work"]["fit_executions"].__setitem__("unknown", 1),
        lambda report: report["statistical"]["oof"]["prediction_mask"].pop(),
        lambda report: report["result_classification"]["inconclusive"].append(
            report["result_classification"]["negative"][0]
        ),
        lambda report: report["economic"]["configurations"]["linear_ridge"].__setitem__(
            "turnover", -1.0
        ),
        lambda report: report["economic"]["configurations"]["linear_ridge"][
            "all_in_cost_sensitivity"
        ][0].__setitem__("cost", -1.0),
        lambda report: report["economic"]["configurations"]["linear_ridge"].__setitem__(
            "gross_total",
            report["economic"]["configurations"]["linear_ridge"]["gross_total"] + 1.0,
        ),
        lambda report: report["economic"]["configurations"]["linear_ridge"]["position_trace"][
            0
        ].__setitem__(
            "realised_gross",
            report["economic"]["configurations"]["linear_ridge"]["position_trace"][0][
                "realised_gross"
            ]
            + 1.0,
        ),
        lambda report: report["economic"]["configurations"]["linear_ridge"][
            "all_in_cost_sensitivity"
        ][0].__setitem__(
            "net_mean",
            report["economic"]["configurations"]["linear_ridge"]["all_in_cost_sensitivity"][0][
                "net_mean"
            ]
            + 1.0,
        ),
        lambda report: report["work"].__setitem__("rows", report["work"]["rows"] + 1),
        lambda report: report["work"]["fit_executions"].__setitem__(
            "linear_ridge", report["work"]["fit_executions"]["linear_ridge"] + 1
        ),
        lambda report: report["statistical"]["oof"]["folds"][0].__setitem__(
            "evaluation_rows", report["statistical"]["oof"]["folds"][0]["evaluation_rows"] + 1
        ),
        lambda report: report["statistical"]["candidates"][0]["execution_receipt"].__setitem__(
            "family", "corrupted-family"
        ),
        lambda report: report["graph"]["tiny_learned_graph"]["execution_receipt"].__setitem__(
            "layers", 99
        ),
        lambda report: report["retained_parents"]["identities"].__setitem__(
            "selection_manifest_id", "f" * 64
        ),
        lambda report: report["retained_parents"]["terminal_authentication"].__setitem__(
            "state", "CORRUPTED"
        ),
        lambda report: report["selection"].__setitem__("outcome_blind", False),
        lambda report: report["selection"]["selected_decision_times"].pop(),
        lambda report: report["economic"]["configurations"]["linear_ridge"][
            "all_in_cost_sensitivity"
        ][0].__setitem__("unit", "invalid"),
        lambda report: report["statistical"]["candidates"][0].__setitem__(
            "support", report["statistical"]["candidates"][0]["support"] + 1
        ),
        lambda report: report["statistical"]["candidates"][0].__setitem__("coverage", 0.0),
        lambda report: report["graph"]["tiny_learned_graph"].__setitem__(
            "walk_forward_fit_executions",
            report["graph"]["tiny_learned_graph"]["walk_forward_fit_executions"] + 1,
        ),
        lambda report: report["work"]["measurement"].__setitem__("elapsed_seconds", 61.0),
        lambda report: report["economic"]["configurations"]["linear_ridge"][
            "all_in_cost_sensitivity"
        ][0].__setitem__("net_mean", None),
        lambda report: report["economic"]["configurations"]["linear_ridge"]["position_trace"][
            0
        ].__setitem__(
            "target_position_change",
            report["economic"]["configurations"]["linear_ridge"]["position_trace"][0][
                "target_position_change"
            ]
            + 1.0,
        ),
        lambda report: report["statistical"]["candidates"][0]["prediction_trace"].__setitem__(
            next(
                index
                for index, selected in enumerate(
                    report["statistical"]["candidates"][0]["prediction_mask"]
                )
                if selected
            ),
            None,
        ),
    ]
    for mutate in mutations:
        malformed = json.loads(result.canonical_json())
        mutate(malformed)
        with pytest.raises(FreezeError, match="renderer"):
            render_markdown(MicroRun(malformed, result.work_count), config)


def test_renderer_rejects_subgroup_change_consistent_local_aggregate() -> None:
    config = FreezeConfig.from_path(CONFIG)
    result = analyse_fixture(synthetic_fixture(), config)
    report = json.loads(result.canonical_json())
    entry = report["economic"]["period"]["period-1"]["position_trace"][0]
    assert entry["target_position_change"] != 0
    entry["target_position_change"] = -entry["target_position_change"]
    with pytest.raises(FreezeError, match="renderer"):
        render_markdown(MicroRun(report, result.work_count), config)


def test_renderer_rejects_configuration_subgroup_change_consistent_local_aggregate() -> None:
    config = FreezeConfig.from_path(CONFIG)
    result = analyse_fixture(synthetic_fixture(), config)
    report = json.loads(result.canonical_json())
    entry = report["economic"]["configurations"]["linear_ridge"]["period"]["period-1"][
        "position_trace"
    ][0]
    assert entry["target_position_change"] != 0
    entry["target_position_change"] = -entry["target_position_change"]
    with pytest.raises(FreezeError, match="renderer"):
        render_markdown(MicroRun(report, result.work_count), config)


def test_renderer_rejects_configuration_subgroup_own_predecessor() -> None:
    config = FreezeConfig.from_path(CONFIG)
    result = analyse_fixture(synthetic_fixture(), config)
    report = json.loads(result.canonical_json())
    economic = report["economic"]
    configuration = economic["configurations"]["fixed_graph"]
    subgroup = configuration["period"]["period-1"]
    entry = next(
        item for item in subgroup["position_trace"] if item["target_id"] == "commodity:spot-gold"
    )
    target_id = entry["target_id"]
    decision_time = entry["decision_time"]
    configuration_trace = sorted(
        configuration["position_trace"], key=lambda item: (item["decision_time"], item["target_id"])
    )
    subgroup_trace = sorted(
        subgroup["position_trace"], key=lambda item: (item["decision_time"], item["target_id"])
    )

    def predecessor(trace: list[dict[str, Any]]) -> Decimal:
        prior = Decimal("0")
        for candidate in trace:
            if candidate["target_id"] == target_id:
                if candidate["decision_time"] == decision_time:
                    return prior
                prior = Decimal(str(candidate["target_position"]))
        raise AssertionError("selected configuration subgroup row has no matching parent trace")

    configuration_prior = predecessor(configuration_trace)
    subgroup_prior = predecessor(subgroup_trace)
    assert subgroup_prior != configuration_prior
    position = Decimal(str(entry["target_position"])) + Decimal("0.1")
    entry["target_position"] = float(position)
    entry["target_position_change"] = float(position - subgroup_prior)

    quantum = Decimal("0.000000000001")

    def quantize(value: Decimal) -> Decimal:
        return value.quantize(quantum, rounding=ROUND_HALF_EVEN)

    gross_total = sum(
        (Decimal(str(item["realised_gross"])) for item in subgroup["position_trace"]), Decimal("0")
    )
    turnover = sum(
        (abs(Decimal(str(item["target_position_change"]))) for item in subgroup["position_trace"]),
        Decimal("0"),
    )
    count = Decimal(len(subgroup["position_trace"]))
    break_even = quantize(gross_total / turnover) if turnover else None
    subgroup["gross_total"] = float(quantize(gross_total))
    subgroup["gross_mean"] = float(quantize(gross_total / count))
    subgroup["turnover"] = float(quantize(turnover))
    subgroup["break_even_cost"] = float(break_even) if break_even is not None else None
    for sensitivity in subgroup["all_in_cost_sensitivity"]:
        cost = Decimal(str(sensitivity["cost"]))
        sensitivity["net_mean"] = float(quantize(gross_total / count - cost * turnover / count))
        sensitivity["break_even_cost"] = float(break_even) if break_even is not None else None

    with pytest.raises(FreezeError, match="renderer"):
        render_markdown(MicroRun(report, result.work_count), config)


def test_renderer_rejects_frozen_role_wrapper_mutation() -> None:
    config = FreezeConfig.from_path(CONFIG)
    result = analyse_fixture(synthetic_fixture(), config)
    report = json.loads(result.canonical_json())
    bindings = config.document["retained_loader"]["identity_bindings"]
    report["retained_parents"]["role_bindings"] = {
        role: {
            "dataset_id": bindings["dataset_ids"][role],
            "config_id": bindings["config_ids"][role],
            "wrapper_sha256": bindings.get("wrapper_sha256s", {}).get(role, "f" * 64),
        }
        for role in ("LOCAL_RIDGE", "POOLED_LOCAL_RIDGE", "ZERO_RETURN")
    }
    report["retained_parents"]["role_bindings"]["POOLED_LOCAL_RIDGE"]["wrapper_sha256"] = "0" * 64
    with pytest.raises(FreezeError, match="renderer"):
        render_markdown(MicroRun(report, result.work_count), config)


def test_native_parts_root_rejects_orphans_and_ancestor_symlinks(tmp_path: Path) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    wrapper = tmp_path / "target-source.json"
    parts_root = wrapper.with_name("target-source.json.parts")
    (parts_root / "targets").mkdir(parents=True)
    (parts_root / "opportunities").mkdir()
    implementation._validate_native_authorised_root(wrapper)
    (parts_root / "orphan.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FreezeError, match="undeclared entry"):
        implementation._validate_native_authorised_root(wrapper)

    symlink_parent = tmp_path / "symlink-parent"
    symlink_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FreezeError, match="parts root"):
        implementation._validate_native_authorised_root(symlink_parent / "target-source.json")


def test_native_parts_root_allows_only_declared_forbidden_family_without_touching_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    wrapper = tmp_path / "target-source.json"
    parts_root = wrapper.with_name("target-source.json.parts")
    (parts_root / "targets").mkdir(parents=True)
    (parts_root / "opportunities").mkdir()
    forbidden = parts_root / "pre-holdout-target"
    forbidden.symlink_to(tmp_path / "outside", target_is_directory=True)
    lstat_calls: list[Path] = []

    def reject_forbidden_path(path: Path, *args: Any, **kwargs: Any) -> Any:
        path_value = Path(path)
        if "pre-holdout-target" in path_value.parts:
            raise AssertionError("pre-holdout family was inspected")
        lstat_calls.append(path_value)
        return original_lstat(path, *args, **kwargs)

    original_lstat = implementation.os.lstat
    monkeypatch.setattr(implementation.os, "lstat", reject_forbidden_path)
    implementation._validate_native_authorised_root(wrapper)

    (parts_root / "unknown").mkdir()
    with pytest.raises(FreezeError, match="undeclared entry"):
        implementation._validate_native_authorised_root(wrapper)
    assert all("unknown" not in path.parts for path in lstat_calls)


@pytest.mark.parametrize("family", ("targets", "opportunities"))
def test_native_parts_root_still_checks_authorised_families(tmp_path: Path, family: str) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    wrapper = tmp_path / "target-source.json"
    parts_root = wrapper.with_name("target-source.json.parts")
    (parts_root / "targets").mkdir(parents=True)
    (parts_root / "opportunities").mkdir()
    invalid_family = parts_root / family
    invalid_family.rmdir()
    invalid_family.write_text("not-a-directory", encoding="utf-8")

    with pytest.raises(FreezeError, match="family is unsafe"):
        implementation._validate_native_authorised_root(wrapper)


@pytest.mark.parametrize(
    "target_instruments",
    (
        tuple(),
        [
            "fx:aud-usd",
            "fx:eur-usd",
            "index:australia-200",
            "index:us-500",
            "commodity:spot-gold",
            "commodity:us-crude",
        ][:-1],
        [
            "fx:aud-usd",
            "fx:eur-usd",
            "index:australia-200",
            "index:us-500",
            "commodity:spot-gold",
            "commodity:us-crude",
            "extra:asset",
        ],
        [
            "fx:aud-usd",
            "fx:eur-usd",
            "index:australia-200",
            "index:us-500",
            "commodity:spot-gold",
            "fx:aud-usd",
        ],
        [
            "fx:aud-usd",
            "fx:eur-usd",
            "index:australia-200",
            "index:us-500",
            "commodity:spot-gold",
            42,
        ],
        "not-a-list",
    ),
    ids=("empty", "missing", "extra", "duplicate", "non_string", "non_list"),
)
def test_native_target_source_requires_exact_six_string_universe(
    target_instruments: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation
    from qtrad.application.r3_historical_exploratory import load_fixture_rows

    original_loader = implementation.load_retained_rows

    def mutated_loader(
        fixture_config: FreezeConfig,
        *,
        locators: Mapping[str, str] | None = None,
        _fixture: bool = False,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        assert locators is not None and _fixture
        source_path = Path(locators["target_source"])
        manifest = json.loads(source_path.read_bytes())
        manifest["target_instruments"] = target_instruments
        source_path.write_bytes(implementation._canonical_bytes(manifest))
        return original_loader(fixture_config, locators=locators, _fixture=True)

    monkeypatch.setattr(implementation, "load_retained_rows", mutated_loader)
    with pytest.raises(FreezeError, match="exact six-instrument universe"):
        load_fixture_rows(synthetic_fixture(), FreezeConfig.from_path(CONFIG))


def test_native_target_source_accepts_reordered_exact_six_string_universe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation
    from qtrad.application.r3_historical_exploratory import load_fixture_rows

    original_loader = implementation.load_retained_rows
    reordered = list(reversed(implementation._TARGET_IDS))

    def reordered_loader(
        fixture_config: FreezeConfig,
        *,
        locators: Mapping[str, str] | None = None,
        _fixture: bool = False,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        assert locators is not None and _fixture
        source_path = Path(locators["target_source"])
        manifest = json.loads(source_path.read_bytes())
        manifest["target_instruments"] = reordered
        source_path.write_bytes(implementation._canonical_bytes(manifest))
        return original_loader(fixture_config, locators=locators, _fixture=True)

    monkeypatch.setattr(implementation, "load_retained_rows", reordered_loader)
    rows, _metadata = load_fixture_rows(synthetic_fixture(), FreezeConfig.from_path(CONFIG))
    assert len(rows) == 18


def test_native_physical_parts_shape_and_pre_holdout_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    wrapper = tmp_path / "target-source.json"
    wrapper.write_bytes(b"{}")
    parts_root = wrapper.with_name("target-source.json.parts")
    targets = parts_root / "targets"
    targets.mkdir(parents=True)
    (parts_root / "opportunities").mkdir()
    rows = [{"target_id": "target-1", "decision_time": "t"}]
    envelope = {
        "contract": implementation._TARGET_SOURCE_PART_CONTRACT,
        "schema_version": 1,
        "source_id": "source-1",
        "kind": "targets",
        "part_index": 0,
        "rows": rows,
    }
    encoded = json.dumps(envelope, separators=(",", ":")).encode()
    part = targets / "part-000000.json"
    part.write_bytes(encoded)
    receipt: dict[str, Any] = {"max_parts": 1, "max_part_bytes": 10_000}
    result = list(
        implementation._iter_native_source_parts(
            wrapper,
            [
                (
                    "target-source.json.parts/targets/part-000000.json",
                    hashlib.sha256(encoded).hexdigest(),
                    1,
                )
            ],
            kind="targets",
            source_id="source-1",
            receipt=receipt,
        )
    )
    assert result == rows
    assert receipt["physical_parts"] == 1
    part.write_bytes(encoded.replace(b"source-1", b"source-2"))
    with pytest.raises(FreezeError, match="lineage"):
        list(
            implementation._iter_native_source_parts(
                wrapper,
                [
                    (
                        "target-source.json.parts/targets/part-000000.json",
                        hashlib.sha256(part.read_bytes()).hexdigest(),
                        1,
                    )
                ],
                kind="targets",
                source_id="source-1",
                receipt={"max_parts": 1, "max_part_bytes": 10_000},
            )
        )

    calls: list[str] = []

    def forbidden_stat(_path: Path) -> bool:
        calls.append("stat")
        raise AssertionError("pre-holdout stat")

    def forbidden_read(_path: Path) -> bytes:
        calls.append("read")
        raise AssertionError("pre-holdout read")

    monkeypatch.setattr(Path, "is_file", forbidden_stat)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    manifest = {
        "pre_holdout_target_parts": [
            {
                "path": "target-source.json.parts/pre-holdout-target/part-000000.json",
                "sha256": "0" * 64,
                "row_count": 1,
            }
        ]
    }
    assert implementation._native_source_references(
        wrapper, manifest, "pre_holdout_target_parts", inspect_files=False
    ) == [
        (
            "target-source.json.parts/pre-holdout-target/part-000000.json",
            "0" * 64,
            1,
        )
    ]
    assert calls == []


def test_native_declared_inventory_qualifies_child_and_path(tmp_path: Path) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    forecast_part = tmp_path / "local_forecast.json.parts" / "part-000000.json"
    outcome_part = tmp_path / "outcome-evidence.json.parts" / "part-000000.json"
    forecast_part.parent.mkdir(parents=True)
    outcome_part.parent.mkdir(parents=True)
    forecast_part.write_text("{}", encoding="utf-8")
    outcome_part.write_text("{}", encoding="utf-8")
    forecast_relative = forecast_part.relative_to(tmp_path).as_posix()
    outcome_relative = outcome_part.relative_to(tmp_path).as_posix()
    budget: dict[str, Any] = {"max_declared_parts": 1}

    implementation._register_declared_inventory(budget, "local_forecast", [forecast_relative])
    implementation._register_declared_inventory(budget, "local_forecast", [forecast_relative])
    assert budget["declared_paths"] == {f"local_forecast:{forecast_relative}"}
    with pytest.raises(FreezeError, match="declared source inventory"):
        implementation._register_declared_inventory(budget, "outcome_evidence", [outcome_relative])


def test_native_physical_budget_read_operations_fail_closed() -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    budget: dict[str, Any] = {"read_operations": 0, "max_read_operations": 1}
    implementation._charge_physical_budget(budget, read_operations=1)
    assert budget["read_operations"] == 1
    with pytest.raises(FreezeError, match="read_operations"):
        implementation._charge_physical_budget(budget, read_operations=1)


def test_native_marker_wrapper_accounting_is_exact_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    config = FreezeConfig.from_path(CONFIG)
    fixture_rows = synthetic_fixture()
    _, baseline = implementation.load_fixture_rows(fixture_rows, config)
    original_open = implementation._open_json_document

    def padded_marker(path: Path, limits: Mapping[str, Any]) -> Any:
        opened = original_open(path, limits)
        if path.name == "selection.json":
            return opened[0], opened[1], opened[2] + 7, opened[3]
        if path.name == "consumed.json":
            return opened[0], opened[1], opened[2] + 11, opened[3]
        return opened

    monkeypatch.setattr(implementation, "_open_json_document", padded_marker)
    _, mutated = implementation.load_fixture_rows(fixture_rows, config)
    assert (
        mutated["source_scan_wrapper_bytes"]["selection"]
        == baseline["source_scan_wrapper_bytes"]["selection"] + 7
    )
    assert (
        mutated["source_scan_wrapper_bytes"]["consumed"]
        == baseline["source_scan_wrapper_bytes"]["consumed"] + 11
    )
    assert mutated["source_scan_bytes"] == baseline["source_scan_bytes"] + 18
    assert mutated["consumed_bytes"] == mutated["source_scan_bytes"]
    assert mutated["source_scan_read_operations"] == baseline["source_scan_read_operations"]

    def reject_marker_read(path: Path, limits: Mapping[str, Any]) -> Any:
        if path.name == "selection.json":
            budget = limits["_physical_budget"]
            assert isinstance(budget, dict)
            budget["max_read_operations"] = int(budget["read_operations"])
        return original_open(path, limits)

    monkeypatch.setattr(implementation, "_open_json_document", reject_marker_read)
    with pytest.raises(FreezeError, match="read_operations"):
        implementation.load_fixture_rows(fixture_rows, config)


@pytest.mark.parametrize("field", ("instrument_id", "decision_time", "target_horizon_seconds"))
def test_native_opportunity_identity_matches_target(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    config = FreezeConfig.from_path(CONFIG)
    original_loader = implementation.load_retained_rows

    def mutated_loader(
        fixture_config: FreezeConfig,
        *,
        locators: Mapping[str, str] | None = None,
        _fixture: bool = False,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        assert _fixture
        assert locators is not None
        actual_locators = dict(locators)
        manifest_path = Path(actual_locators["target_source"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        reference = manifest["opportunity_parts"][0]
        part_path = manifest_path.parent / reference["path"]
        envelope = json.loads(part_path.read_text(encoding="utf-8"))
        row = envelope["rows"][0]
        if field == "instrument_id":
            row[field] = next(
                instrument for instrument in implementation._TARGET_IDS if instrument != row[field]
            )
        elif field == "decision_time":
            row[field] = "2099-01-01T00:00:00Z"
        else:
            row[field] = 901
        encoded = implementation._canonical_bytes(envelope)
        part_path.write_bytes(encoded)
        reference["sha256"] = hashlib.sha256(encoded).hexdigest()
        manifest["closure_id"] = implementation._native_source_closure(manifest)
        manifest_path.write_bytes(implementation._canonical_bytes(manifest))
        return original_loader(fixture_config, locators=actual_locators, _fixture=True)

    monkeypatch.setattr(implementation, "load_retained_rows", mutated_loader)
    with pytest.raises(FreezeError, match="opportunity identity differs from target"):
        implementation.load_fixture_rows(synthetic_fixture(), config)


@pytest.mark.parametrize(
    "mutation",
    (
        "orphan_file",
        "orphan_directory",
        "orphan_symlink",
        "symlinked_root",
        "symlinked_parent",
        "symlinked_grandparent",
        "declared_symlink",
    ),
)
def test_native_outcome_parts_tree_rejects_undeclared_and_symlink_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    real_parent = tmp_path / "real" / "anchor"
    real_parent.mkdir(parents=True)
    manifest_path = real_parent / "outcome-evidence.json"
    root = real_parent / "outcome-evidence.json.parts"
    root.mkdir()
    part = root / "part-000000.json"
    part.write_bytes(b"part")
    references: list[Mapping[str, Any]] = [
        {
            "path": "outcome-evidence.json.parts/part-000000.json",
            "sha256": hashlib.sha256(b"part").hexdigest(),
            "row_count": 1,
            "part_index": 0,
        }
    ]
    if mutation == "orphan_file":
        (root / "orphan.json").write_text("orphan", encoding="utf-8")

        def forbidden_is_file(_path: Path) -> bool:
            raise AssertionError("orphan stat")

        monkeypatch.setattr(Path, "is_file", forbidden_is_file)
    elif mutation == "orphan_directory":
        (root / "orphan").mkdir()
    elif mutation == "orphan_symlink":
        target = tmp_path / "symlink-target"
        target.write_text("target", encoding="utf-8")
        (root / "orphan").symlink_to(target)
    elif mutation == "symlinked_root":
        real_root = root.with_name("outcome-evidence.json.parts.real")
        root.rename(real_root)
        root.symlink_to(real_root, target_is_directory=True)
    elif mutation == "symlinked_parent":
        alias = tmp_path / "parent-alias"
        alias.symlink_to(real_parent, target_is_directory=True)
        manifest_path = alias / manifest_path.name
    elif mutation == "symlinked_grandparent":
        alias = tmp_path / "grandparent-alias"
        alias.symlink_to(tmp_path / "real", target_is_directory=True)
        manifest_path = alias / "anchor" / manifest_path.name
    else:
        real_part = root / "part-000000.real"
        part.rename(real_part)
        part.symlink_to(real_part.name)

    with pytest.raises(FreezeError):
        implementation._validate_native_outcome_parts_tree(
            manifest_path, references, manifest_relative_path="outcome-evidence.json"
        )


def test_native_outcome_parts_tree_accepts_normal_physical_tree(tmp_path: Path) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    parent = tmp_path / "normal"
    parent.mkdir()
    manifest_path = parent / "outcome-evidence.json"
    manifest_path.write_bytes(b"{}")
    root = parent / "outcome-evidence.json.parts"
    root.mkdir()
    part = root / "part-000000.json"
    part.write_bytes(b"part")
    references: list[Mapping[str, Any]] = [
        {
            "path": "outcome-evidence.json.parts/part-000000.json",
            "sha256": hashlib.sha256(b"part").hexdigest(),
            "row_count": 1,
            "part_index": 0,
        }
    ]
    implementation._validate_native_outcome_parts_tree(
        manifest_path, references, manifest_relative_path="outcome-evidence.json"
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "normal",
        "wrapper_sha",
        "wrapper_symlink",
        "parent_symlink",
        "grandparent_symlink",
        "declared_part_symlink",
    ),
)
def test_native_authenticated_open_surface_guard(tmp_path: Path, mutation: str) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    if mutation == "grandparent_symlink":
        real_grandparent = tmp_path / "real-grandparent"
        real_parent = real_grandparent / "parent"
        real_parent.mkdir(parents=True)
        alias_grandparent = tmp_path / "grandparent-alias"
        alias_grandparent.symlink_to(real_grandparent, target_is_directory=True)
        wrapper = alias_grandparent / "parent" / "wrapper.json"
    elif mutation == "parent_symlink":
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        alias_parent = tmp_path / "parent-alias"
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        wrapper = alias_parent / "wrapper.json"
    else:
        wrapper = tmp_path / "wrapper.json"
        wrapper.parent.mkdir(parents=True, exist_ok=True)

    encoded = b'{"ok":true}'
    wrapper.write_bytes(encoded)
    expected = hashlib.sha256(encoded).hexdigest()
    part_relative = PurePosixPath("wrapper.json.parts/part-000000.json")
    part = wrapper.parent / part_relative
    part.parent.mkdir()
    part.write_bytes(encoded)

    if mutation == "wrapper_sha":
        with pytest.raises(FreezeError, match="byte hash"):
            implementation._native_authenticated_read(
                wrapper, PurePosixPath(wrapper.name), expected_sha256="0" * 64
            )
        return
    if mutation == "wrapper_symlink":
        real_wrapper = wrapper.with_name("wrapper.real")
        wrapper.rename(real_wrapper)
        wrapper.symlink_to(real_wrapper.name)
    elif mutation == "declared_part_symlink":
        real_part = part.with_name("part.real")
        part.rename(real_part)
        part.symlink_to(real_part.name)

    if mutation == "normal":
        path, actual = implementation._native_authenticated_read(
            wrapper, PurePosixPath(wrapper.name), expected_sha256=expected
        )
        assert path == wrapper.absolute()
        assert actual == encoded
        return

    with pytest.raises(FreezeError):
        relative = (
            part_relative if mutation == "declared_part_symlink" else PurePosixPath(wrapper.name)
        )
        implementation._native_authenticated_read(wrapper, relative, expected_sha256=expected)


def test_native_json_open_surface_has_one_authenticated_read_boundary() -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    tree = ast.parse(inspect.getsource(implementation))
    loader_names = {
        "_declared_partition_paths",
        "_open_partitioned_json_document",
        "_open_json_document",
        "_iter_native_source_parts",
        "_load_native_target_source",
        "_load_native_outcome_values",
        "_load_native_retained_rows",
        "_validate_native_outcome_parts_tree",
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in loader_names:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            target = call.func
            if isinstance(target, ast.Name) and target.id == "open":
                violations.append(f"{node.name}:open")
            elif isinstance(target, ast.Attribute) and target.attr in {
                "open",
                "read_bytes",
                "read_text",
                "resolve",
                "stat",
                "is_file",
                "is_dir",
                "is_symlink",
            }:
                violations.append(f"{node.name}:{target.attr}")
    assert violations == []


def _mutate_fixture_native_source(
    monkeypatch: pytest.MonkeyPatch,
    config: FreezeConfig,
    mutation: Callable[[list[dict[str, Any]], list[dict[str, Any]]], None],
    *,
    preserve_target_order: bool = False,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    import qtrad.application.r3_historical_exploratory as implementation

    original_loader = implementation.load_retained_rows

    def mutated_loader(
        fixture_config: FreezeConfig,
        *,
        locators: Mapping[str, str] | None = None,
        _fixture: bool = False,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        assert _fixture
        assert locators is not None
        actual_locators = dict(locators)
        manifest_path = Path(actual_locators["target_source"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        target_references = cast(list[dict[str, Any]], manifest["target_parts"])
        opportunity_reference = cast(dict[str, Any], manifest["opportunity_parts"][0])

        def read_part_rows(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for reference in references:
                part_path = manifest_path.parent / str(reference["path"])
                document = json.loads(part_path.read_text(encoding="utf-8"))
                rows.extend(cast(list[dict[str, Any]], document["rows"]))
            return rows

        target_rows = read_part_rows(target_references)
        opportunity_path = manifest_path.parent / str(opportunity_reference["path"])
        opportunity_document = json.loads(opportunity_path.read_text(encoding="utf-8"))
        opportunity_rows = cast(list[dict[str, Any]], opportunity_document["rows"])
        mutation(target_rows, opportunity_rows)
        if not preserve_target_order:
            target_rows.sort(key=lambda row: str(row["target_id"]))

        target_template_path = manifest_path.parent / str(target_references[0]["path"])
        target_template = json.loads(target_template_path.read_text(encoding="utf-8"))
        target_boundary = max(1, len(target_rows) // 2)
        target_chunks = (target_rows[:target_boundary], target_rows[target_boundary:])
        target_references_rewritten: list[dict[str, Any]] = []
        for part_index, part_rows in enumerate(target_chunks):
            document = dict(target_template)
            document["part_index"] = part_index
            document["rows"] = part_rows
            relative = f"{manifest_path.name}.parts/targets/part-{part_index:06d}.json"
            part_path = manifest_path.parent / relative
            encoded = implementation._canonical_bytes(document)
            part_path.write_bytes(encoded)
            target_references_rewritten.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "row_count": len(part_rows),
                }
            )

        opportunity_document["rows"] = opportunity_rows
        encoded_opportunity = implementation._canonical_bytes(opportunity_document)
        opportunity_path.write_bytes(encoded_opportunity)
        opportunity_reference["sha256"] = hashlib.sha256(encoded_opportunity).hexdigest()
        opportunity_reference["row_count"] = len(opportunity_rows)

        manifest["target_parts"] = target_references_rewritten
        manifest["target_count"] = len(target_rows)
        manifest["opportunity_count"] = len(opportunity_rows)
        manifest["closure_id"] = implementation._native_source_closure(manifest)
        manifest_path.write_bytes(implementation._canonical_bytes(manifest))
        return original_loader(fixture_config, locators=actual_locators, _fixture=True)

    monkeypatch.setattr(implementation, "load_retained_rows", mutated_loader)
    return implementation.load_fixture_rows(synthetic_fixture(), config)


def _extra_fixture_target(
    target_rows: list[dict[str, Any]],
    *,
    decision_time: str,
    horizon: int,
) -> dict[str, Any]:
    extra = deepcopy(target_rows[0])
    extra["target_id"] = hashlib.sha256(
        f"extra-target-{decision_time}-{horizon}".encode()
    ).hexdigest()
    extra["fixture_target_id"] = f"extra-{horizon}"
    extra["decision_time"] = decision_time
    extra["target_horizon_seconds"] = horizon
    extra["target_start_time"] = decision_time
    extra["target_end_time"] = extra["target_available_at"]
    extra["target_freeze_at"] = extra["target_available_at"]
    return extra


def test_native_non_primary_target_is_validated_but_not_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FreezeConfig.from_path(CONFIG)

    def mutate(target_rows: list[dict[str, Any]], _opportunity_rows: list[dict[str, Any]]) -> None:
        target_rows.append(
            _extra_fixture_target(
                target_rows,
                decision_time="2026-01-01T00:00:00Z",
                horizon=901,
            )
        )

    rows, metadata = _mutate_fixture_native_source(monkeypatch, config, mutate)
    assert len(rows) == len(synthetic_fixture())
    assert metadata["native_target_source"]["target_unique_ids"] == len(rows)
    assert metadata["native_target_source"]["target_rows"] == len(rows) + 1
    assert metadata["native_target_source"]["target_parts"] == 2


def test_native_out_of_range_target_is_validated_but_not_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FreezeConfig.from_path(CONFIG)

    def mutate(target_rows: list[dict[str, Any]], _opportunity_rows: list[dict[str, Any]]) -> None:
        target_rows.append(
            _extra_fixture_target(
                target_rows,
                decision_time="2099-01-01T00:00:00Z",
                horizon=900,
            )
        )

    rows, metadata = _mutate_fixture_native_source(monkeypatch, config, mutate)
    assert len(rows) == len(synthetic_fixture())
    assert metadata["native_target_source"]["target_unique_ids"] == len(rows)
    assert metadata["native_target_source"]["target_rows"] == len(rows) + 1
    assert metadata["native_target_source"]["target_parts"] == 2


def test_native_missing_eligible_opportunity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FreezeConfig.from_path(CONFIG)

    def mutate(_target_rows: list[dict[str, Any]], opportunity_rows: list[dict[str, Any]]) -> None:
        opportunity_rows.pop()

    with pytest.raises(FreezeError, match="eligible target/opportunity universes differ"):
        _mutate_fixture_native_source(monkeypatch, config, mutate)


def test_native_ineligible_opportunity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FreezeConfig.from_path(CONFIG)

    def mutate(target_rows: list[dict[str, Any]], opportunity_rows: list[dict[str, Any]]) -> None:
        extra = _extra_fixture_target(
            target_rows,
            decision_time="2099-01-01T00:00:00Z",
            horizon=900,
        )
        target_rows.append(extra)
        opportunity = deepcopy(opportunity_rows[0])
        opportunity.update(
            {
                "target_id": extra["target_id"],
                "fixture_target_id": extra["fixture_target_id"],
                "decision_time": extra["decision_time"],
                "target_horizon_seconds": extra["target_horizon_seconds"],
            }
        )
        opportunity_rows.append(opportunity)

    with pytest.raises(FreezeError, match="opportunity identity differs from target"):
        _mutate_fixture_native_source(monkeypatch, config, mutate)


def test_native_second_pass_recovers_exact_selected_target_opportunity_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    config = FreezeConfig.from_path(CONFIG)
    observed: list[tuple[set[str], set[str]]] = []
    original_row = implementation._native_fixture_row

    def capture(
        target_id: str,
        targets: Mapping[str, Mapping[str, Any]],
        opportunities: Mapping[str, Mapping[str, Any]],
        forecasts: Mapping[str, Mapping[str, Mapping[str, Any]]],
        outcomes: Mapping[str, float],
        periods: Mapping[str, str],
    ) -> Any:
        observed.append((set(targets), set(opportunities)))
        return original_row(target_id, targets, opportunities, forecasts, outcomes, periods)

    monkeypatch.setattr(implementation, "_native_fixture_row", capture)
    rows, _metadata = implementation.load_fixture_rows(synthetic_fixture(), config)
    assert len(rows) == len(synthetic_fixture())
    assert observed
    assert all(target_ids == opportunity_ids for target_ids, opportunity_ids in observed)
    assert {row.target_id for row in rows} == {row.target_id for row in synthetic_fixture()}


def test_native_extra_target_domain_identity_is_still_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FreezeConfig.from_path(CONFIG)

    def mutate(target_rows: list[dict[str, Any]], _opportunity_rows: list[dict[str, Any]]) -> None:
        extra = _extra_fixture_target(
            target_rows,
            decision_time="2026-01-01T00:00:00Z",
            horizon=901,
        )
        extra["target_basis"] = "UNSUPPORTED"
        target_rows.append(extra)

    with pytest.raises(FreezeError, match="target row domain identity"):
        _mutate_fixture_native_source(monkeypatch, config, mutate)


@pytest.mark.parametrize(
    "mutation", ("unordered", "duplicate", "unordered_cross_part", "duplicate_cross_part")
)
def test_native_target_source_requires_strict_target_id_order(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    config = FreezeConfig.from_path(CONFIG)

    def mutate(target_rows: list[dict[str, Any]], _opportunity_rows: list[dict[str, Any]]) -> None:
        if mutation == "unordered":
            target_rows[0], target_rows[1] = target_rows[1], target_rows[0]
        elif mutation == "duplicate":
            target_rows.append(deepcopy(target_rows[-1]))
        elif mutation == "unordered_cross_part":
            target_rows[8], target_rows[9] = target_rows[9], target_rows[8]
        else:
            target_rows[9] = deepcopy(target_rows[8])

    with pytest.raises(FreezeError, match="strictly increasing"):
        _mutate_fixture_native_source(monkeypatch, config, mutate, preserve_target_order=True)


def test_native_target_source_rejects_repeated_eligible_instrument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FreezeConfig.from_path(CONFIG)

    def mutate(target_rows: list[dict[str, Any]], _opportunity_rows: list[dict[str, Any]]) -> None:
        duplicate = deepcopy(target_rows[0])
        duplicate["target_id"] = hashlib.sha256(b"duplicate-eligible-target").hexdigest()
        target_rows.append(duplicate)

    with pytest.raises(FreezeError, match="repeats eligible instrument"):
        _mutate_fixture_native_source(monkeypatch, config, mutate)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("max_elapsed_seconds", 1, "elapsed-time bound"),
        ("max_memory_mb", 1, "memory bound"),
    ),
)
def test_native_loader_uses_exact_frozen_compute_limits(
    monkeypatch: pytest.MonkeyPatch, field: str, value: int, match: str
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    base_config = FreezeConfig.from_path(CONFIG)
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    cast(dict[str, Any], document["compute_limits"])[field] = value
    config = FreezeConfig(document=document, semantic_identity=base_config.semantic_identity)
    if field == "max_elapsed_seconds":
        clock = [0]

        def fake_monotonic() -> float:
            clock[0] += 1
            return 0.0 if clock[0] < 4 else 2.0

        monkeypatch.setattr(implementation.time, "monotonic", fake_monotonic)
    else:
        monkeypatch.setattr(
            implementation.resource,
            "getrusage",
            lambda _resource: SimpleNamespace(ru_maxrss=2048),
        )

    with pytest.raises(FreezeError, match=match):
        implementation.load_fixture_rows(synthetic_fixture(), config)


@pytest.mark.parametrize(
    ("limit_field", "match"),
    (
        ("max_elapsed_seconds", "elapsed-time bound"),
        ("max_memory_mb", "memory bound"),
    ),
)
def test_native_scan_bound_is_checked_at_8192_rows_before_child_completion(
    monkeypatch: pytest.MonkeyPatch, limit_field: str, match: str
) -> None:
    import qtrad.application.r3_historical_exploratory as implementation

    base_config = FreezeConfig.from_path(CONFIG)
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    cast(dict[str, Any], document["compute_limits"])[limit_field] = 1
    config = FreezeConfig(document=document, semantic_identity=base_config.semantic_identity)
    scanned_kinds: list[str] = []
    original_iter = implementation._iter_native_source_parts

    def tracking_iter(*args: Any, **kwargs: Any) -> Any:
        kind = cast(str, kwargs["kind"])
        for row in original_iter(*args, **kwargs):
            scanned_kinds.append(kind)
            yield row

    monkeypatch.setattr(implementation, "_iter_native_source_parts", tracking_iter)
    if limit_field == "max_elapsed_seconds":
        clock = [0]

        def fake_monotonic() -> float:
            clock[0] += 1
            return 0.0 if clock[0] == 1 else 2.0

        monkeypatch.setattr(implementation.time, "monotonic", fake_monotonic)
    else:
        monkeypatch.setattr(
            implementation.resource,
            "getrusage",
            lambda _resource: SimpleNamespace(ru_maxrss=2048),
        )

    def mutate(target_rows: list[dict[str, Any]], _opportunity_rows: list[dict[str, Any]]) -> None:
        base = deepcopy(target_rows[0])
        for index in range(8200):
            extra = deepcopy(base)
            extra["target_id"] = f"{(1 << 256) - 10000 + index:064x}"
            extra["fixture_target_id"] = f"extra-{index}"
            extra["target_horizon_seconds"] = 1
            target_rows.append(extra)

    with pytest.raises(FreezeError, match=match):
        _mutate_fixture_native_source(monkeypatch, config, mutate)

    assert len(scanned_kinds) == 8192
    assert set(scanned_kinds) == {"targets"}
