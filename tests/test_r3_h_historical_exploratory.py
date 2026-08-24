"""Focused R3.H Stage 1 freeze and micro-run tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

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

CONFIG = Path("docs/archive/r3/r3-historical-exploratory-freeze.json")


def _rehashed(document: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in document.items() if key != "semantic_identity"}
    document["semantic_identity"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return document


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
    assert report["loader_contract"]["decode_policy"].startswith("decode only selected")
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
    metadata: dict[str, Any] = {key: None for key in declaration["required_keys"]}
    metadata.update(
        {
            "contract": declaration["contract"],
            "dataset_id": declaration["identity"],
            "parts": [
                {
                    "locator": "part.json",
                    "contract": declaration["contract"],
                    "identity": "part",
                    "sha256": "a" * 64,
                    "byte_size": 1,
                    "row_count": 1,
                }
            ],
        }
    )
    _validate_child_metadata("local_forecast", metadata, loader)
    metadata["dataset_id"] = "wrong-dataset"
    with pytest.raises(FreezeError, match="identity mismatch"):
        _validate_child_metadata("local_forecast", metadata, loader)


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


def test_fixture_loader_injects_terminal_authority_and_selection() -> None:
    from qtrad.application.r3_historical_exploratory import load_fixture_rows

    config = FreezeConfig.from_path(CONFIG)
    rows, metadata = load_fixture_rows(synthetic_fixture(), config)
    assert len(rows) == 18
    assert metadata["authority"]["authentication_performed"] is False
    assert metadata["outcome_decode_performed"] is False
    assert metadata["selection"]["stop_state"] == "STOPPED_AFTER_EARLIEST_COMPLETE_GROUP_BOUND"
    assert all(state == "NOT_SCANNED_AFTER_BOUND" for state in metadata["unopened_parts"].values())
    assert metadata["stop_reason"] == "EARLIEST_COMPLETE_GROUP_BOUND"
    assert metadata["consumed_parts_count"] == 8
    assert all(len(hashes) == 2 for hashes in metadata["source_scan_part_hashes"].values())
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
    ]
    for mutate in mutations:
        malformed = json.loads(result.canonical_json())
        mutate(malformed)
        with pytest.raises(FreezeError, match="renderer"):
            render_markdown(MicroRun(malformed, result.work_count), config)


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
