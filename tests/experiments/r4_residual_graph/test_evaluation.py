from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import polars as pl
import pytest

from experiments.r4_residual_graph import foundation as foundation_module
from experiments.r4_residual_graph.evaluation import (
    _correlation,
    _diagnostics,
    _family_metrics,
    _payload_rows,
    apply_historical_nomination_rule,
    comparison_delta,
    concentration_shares,
    evaluate_terminal_register,
    instrument_balanced_mse,
)
from experiments.r4_residual_graph.execution import (
    _summarize_fit_evidence,
    _validate_fit_evidence,
)
from experiments.r4_residual_graph.foundation import (
    ALL_INSTRUMENTS,
    _authenticated_training_rows,
    reconstruct_authenticated_linear_forecasts,
)


def _metric_rows() -> list[dict[str, object]]:
    return [
        {
            "period": "DEV_2",
            "instrument_id": instrument,
            "decision_time": "2024-01-01T00:00:00+00:00",
            "target_return": float(index + 1),
            "forecast": 0.0,
        }
        for index, instrument in enumerate(ALL_INSTRUMENTS)
    ]


def _terminal_payload(slot_id: str = "FAMILY:17:TERMINAL_FORMER_HOLDOUT") -> dict[str, object]:
    keys = tuple(f"{instrument}|2024-01-01T00:00:00+00:00" for instrument in ALL_INSTRUMENTS)
    values = [0.0] * len(keys)
    return {
        "slot_id": slot_id,
        "stage": "TERMINAL_FORMER_HOLDOUT",
        "target_keys": keys,
        "total_forecast": values,
        "residual_prediction": values,
        "local_ridge_forecast": values,
        "fully_pooled_local_ridge_forecast": values,
    }


def _terminal_outcomes() -> dict[str, dict[str, object]]:
    payload = _terminal_payload()
    keys = cast(tuple[str, ...], payload["target_keys"])
    return {
        key: {
            "target_return": 1.0,
            "period": "TERMINAL_FORMER_HOLDOUT",
            "instrument_id": key.split("|", 1)[0],
            "decision_time": key.split("|", 1)[1],
        }
        for key in keys
    }


def test_instrument_balanced_mse_uses_equal_frozen_instrument_weights() -> None:
    assert instrument_balanced_mse(_metric_rows()) == pytest.approx(143.5)


def test_instrument_balanced_mse_handles_unequal_per_instrument_support() -> None:
    rows = [
        {
            "instrument_id": instrument,
            "decision_time": f"2024-01-01T00:{offset:02d}:00+00:00",
            "target_return": (
                1.0
                if instrument == ALL_INSTRUMENTS[0]
                else 2.0
                if instrument == ALL_INSTRUMENTS[1]
                else 0.0
            ),
            "forecast": 0.0,
        }
        for instrument in ALL_INSTRUMENTS
        for offset in range(2 if instrument == ALL_INSTRUMENTS[0] else 1)
    ]
    assert instrument_balanced_mse(rows) == pytest.approx(5.0 / 20.0)


def test_comparison_delta_requires_exact_common_support() -> None:
    candidate = _metric_rows()
    comparator = [dict(row, forecast=-0.5) for row in candidate]
    delta = comparison_delta(candidate, comparator)
    assert set(delta) == set(ALL_INSTRUMENTS)
    assert all(value > 0.0 for value in delta.values())
    with pytest.raises(ValueError, match="exact common support"):
        comparison_delta(candidate[:-1], comparator)


def test_concentration_zero_positive_sum_is_failure_with_unit_share() -> None:
    result = concentration_shares({instrument: 0.0 for instrument in ALL_INSTRUMENTS})
    assert result["best_instrument_share"] == 1.0
    assert result["concentration_gate"] is False


def test_duplicate_keys_across_terminal_slots_are_allowed() -> None:
    payload = _terminal_payload()
    rows = _payload_rows(
        [payload, _terminal_payload("OTHER:29:TERMINAL_FORMER_HOLDOUT")],
        _terminal_outcomes(),
        periods=("TERMINAL_FORMER_HOLDOUT",),
    )
    assert len(rows) == 2 * len(ALL_INSTRUMENTS)


def test_duplicate_keys_within_slot_are_rejected() -> None:
    payload = _terminal_payload()
    keys = list(cast(tuple[str, ...], payload["target_keys"]))
    keys[1] = keys[0]
    payload["target_keys"] = tuple(keys)
    with pytest.raises(ValueError, match="duplicate target keys"):
        _payload_rows(
            [payload],
            _terminal_outcomes(),
            periods=("TERMINAL_FORMER_HOLDOUT",),
        )


def test_outcome_support_and_period_mismatches_are_rejected() -> None:
    payload = _terminal_payload()
    outcomes = _terminal_outcomes()
    extra = "commodity:extra|2024-01-01T00:00:00+00:00"
    outcomes[extra] = {
        "target_return": 1.0,
        "period": "TERMINAL_FORMER_HOLDOUT",
    }
    with pytest.raises(ValueError, match="out of forecast support"):
        _payload_rows([payload], outcomes, periods=("TERMINAL_FORMER_HOLDOUT",))
    mismatched = _terminal_outcomes()
    mismatched[next(iter(mismatched))]["period"] = "DEV_2"
    with pytest.raises(ValueError, match="does not match"):
        _payload_rows([payload], mismatched, periods=("TERMINAL_FORMER_HOLDOUT",))


def test_duplicate_outcome_keys_are_rejected_before_period_evaluation() -> None:
    payload = _terminal_payload()
    key = next(iter(cast(tuple[str, ...], payload["target_keys"])))
    duplicate_rows = [
        {"target_key": key, "period": "DEV_2", "target_return": 1.0},
        {"target_key": key, "period": "DEV_3", "target_return": 2.0},
    ]
    with pytest.raises(ValueError, match="duplicate evaluation outcome key"):
        _payload_rows([payload], duplicate_rows, periods=("TERMINAL_FORMER_HOLDOUT",))


def test_missing_pooled_control_is_rejected() -> None:
    payload = _terminal_payload()
    del payload["fully_pooled_local_ridge_forecast"]
    with pytest.raises(ValueError, match="canonical total, residual, local, and pooled"):
        _payload_rows(
            [payload],
            _terminal_outcomes(),
            periods=("TERMINAL_FORMER_HOLDOUT",),
        )


def test_total_forecast_must_equal_local_plus_residual() -> None:
    payload = _terminal_payload()
    payload["total_forecast"] = [1.0] * len(ALL_INSTRUMENTS)
    with pytest.raises(ValueError, match="total forecast"):
        _payload_rows(
            [payload],
            _terminal_outcomes(),
            periods=("TERMINAL_FORMER_HOLDOUT",),
        )


def test_diagnostics_include_finite_residual_correction_magnitudes() -> None:
    rows = [
        {
            "instrument_id": ALL_INSTRUMENTS[0],
            "target_return": 1.0,
            "forecast": 0.5,
            "residual_correction": -0.25,
        },
        {
            "instrument_id": ALL_INSTRUMENTS[1],
            "target_return": -1.0,
            "forecast": -0.5,
            "residual_correction": 0.75,
        },
    ]
    diagnostics = _diagnostics(rows, "forecast")
    assert diagnostics["residual_correction_magnitude_mean"] == pytest.approx(0.5)
    assert diagnostics["residual_correction_magnitude_max"] == pytest.approx(0.75)


def test_linear_control_reconstruction_requires_explicit_authenticated_support() -> None:

    with pytest.raises(ValueError, match="requires authenticated support"):
        reconstruct_authenticated_linear_forecasts(pl.DataFrame(), periods=("DEV_2",))


def test_linear_control_reconstruction_rejects_unsealed_support_identity() -> None:
    with pytest.raises(ValueError, match="requires sealed support identity"):
        reconstruct_authenticated_linear_forecasts(
            pl.DataFrame(),
            validation_by_period={"DEV_2": pl.DataFrame()},
            periods=("DEV_2",),
            support_identity="fabricated",
        )


def test_linear_control_reconstruction_rejects_fabricated_training_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticated_rows = pl.DataFrame({"row": [1]})
    fabricated_rows = pl.DataFrame({"row": [2]})
    capsule = SimpleNamespace(
        manifest={},
        manifest_sha256="m" * 64,
        child_closure_sha256="c" * 64,
    )
    support = SimpleNamespace(identity="a" * 64)
    monkeypatch.setattr(
        foundation_module,
        "load_parent_rows",
        lambda _capsule: authenticated_rows,
    )
    monkeypatch.setattr(
        foundation_module,
        "_lab_s_block_specs",
        lambda _manifest: {"DEV_2": {"start": "2024-01-01T00:00:00+00:00"}},
    )
    with pytest.raises(ValueError, match="not an authenticated parent projection"):
        reconstruct_authenticated_linear_forecasts(
            fabricated_rows,
            validation_by_period={"DEV_2": pl.DataFrame()},
            periods=("DEV_2",),
            capsule=cast(foundation_module.Lab0Capsule, capsule),
            support_identity="a" * 64,
            support=cast(object, support),
        )


def test_authenticated_training_projection_excludes_invalid_history_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.r4_residual_graph.foundation as foundation_module

    valid = pl.DataFrame({"target_return": [1.0], "target_valid": [True]})
    invalid = pl.DataFrame({"target_return": [None], "target_valid": [False]})

    def load_parent(
        _capsule: object,
        *,
        include_invalid: bool = False,
        include_return: bool = True,
    ) -> pl.DataFrame:
        assert include_return
        return pl.concat([valid, invalid]) if include_invalid else valid

    monkeypatch.setattr(foundation_module, "load_parent_rows", load_parent)
    training = _authenticated_training_rows(cast(foundation_module.Lab0Capsule, object()))
    assert training.height == 1
    assert training["target_return"].to_list() == [1.0]


def test_nomination_rejects_non_graph_candidate() -> None:
    with pytest.raises(ValueError, match="authorised graph family"):
        apply_historical_nomination_rule({}, candidate="LOCAL_RIDGE")


def test_nomination_preserves_both_graph_candidates_without_ranking() -> None:
    periods = ("DEV_2", "DEV_3", "TERMINAL_FORMER_HOLDOUT")
    instruments = list(ALL_INSTRUMENTS)

    def primary(value: float) -> dict[str, object]:
        period_metrics = {
            period: {"instrument_balanced_mse": value, "calibration_slope": 1.0}
            for period in periods
        }
        period_metrics["combined_development"] = {
            "instrument_balanced_mse": value,
            "calibration_slope": 1.0,
        }
        positive = {
            period: {instrument: 0.1 for instrument in instruments}
            for period in ("combined_development", "TERMINAL_FORMER_HOLDOUT")
        }
        return {
            "periods": period_metrics,
            "combined_development": period_metrics["combined_development"],
            "controls": {
                "ZERO_RETURN": {
                    "combined_development": {"instrument_balanced_mse": 2.0},
                    "periods": {"TERMINAL_FORMER_HOLDOUT": {"instrument_balanced_mse": 2.0}},
                }
            },
            "deltas": {
                "POOLED_NON_GRAPH_RESIDUAL": {
                    **{period: 1.0 for period in periods},
                    "combined_development": 1.0,
                }
            },
            "deltas_by_instrument": {"POOLED_NON_GRAPH_RESIDUAL": positive},
            "concentration": {},
        }

    def family(value: float, seed_values: tuple[float, ...] = (1.0, 1.0, 1.0)) -> dict[str, object]:
        return {
            "primary": primary(value),
            "seeds": {
                str(seed): {
                    "deltas": {
                        "POOLED_NON_GRAPH_RESIDUAL": {
                            "combined_development": seed_value,
                            "TERMINAL_FORMER_HOLDOUT": seed_value,
                        }
                    }
                }
                for seed, seed_value in zip((17, 29, 43), seed_values, strict=True)
            },
        }

    evaluation: dict[str, object] = {
        "families": {
            "POOLED_NON_GRAPH_RESIDUAL": family(2.0),
            "FIXED_ECONOMIC_GRAPH_RESIDUAL": family(1.0),
            "LEARNED_STATIC_GRAPH_RESIDUAL": family(0.5),
            "SHUFFLED_FIXED_GRAPH_RESIDUAL": family(2.0),
        },
        "coverage": {period: {instrument: 1 for instrument in instruments} for period in periods},
    }
    fixed = apply_historical_nomination_rule(evaluation, candidate="FIXED_ECONOMIC_GRAPH_RESIDUAL")
    learned = apply_historical_nomination_rule(
        evaluation, candidate="LEARNED_STATIC_GRAPH_RESIDUAL"
    )
    assert fixed["verdict"] == "HYPOTHESIS_NOMINATED"
    assert learned["verdict"] == "HYPOTHESIS_NOMINATED"
    assert {fixed["candidate"], learned["candidate"]} == {
        "FIXED_ECONOMIC_GRAPH_RESIDUAL",
        "LEARNED_STATIC_GRAPH_RESIDUAL",
    }


def test_development_metrics_compare_fitted_families_on_exact_common_support() -> None:
    from experiments.r4_residual_graph.evaluation import evaluate_development_register

    payloads: list[dict[str, object]] = []
    outcomes: dict[str, dict[str, object]] = {}
    for family, forecast in (
        ("FIXED_ECONOMIC_GRAPH_RESIDUAL", 1.0),
        ("POOLED_NON_GRAPH_RESIDUAL", 0.0),
    ):
        for stage, timestamp in (
            ("DEV_2", "2024-01-01T00:00:00+00:00"),
            ("DEV_3", "2024-01-01T01:00:00+00:00"),
        ):
            keys = tuple(f"{instrument}|{timestamp}" for instrument in ALL_INSTRUMENTS)
            for key in keys:
                outcomes[key] = {
                    "period": stage,
                    "instrument_id": key.split("|", 1)[0],
                    "decision_time": timestamp,
                    "target_return": 1.0,
                }
            payloads.append(
                {
                    "slot_id": f"{family}:17:{stage}",
                    "stage": stage,
                    "target_keys": keys,
                    "total_forecast": [forecast] * len(keys),
                    "residual_prediction": [forecast - 0.5] * len(keys),
                    "local_ridge_forecast": [0.5] * len(keys),
                    "fully_pooled_local_ridge_forecast": [0.25] * len(keys),
                    "linear_control_identities": {
                        name: f"{name}:{stage}"
                        for name in ("ZERO_RETURN", "LOCAL_RIDGE", "FULLY_POOLED_LOCAL_RIDGE")
                    },
                    "linear_control_support_identity": "common-support",
                }
            )
    register = evaluate_development_register(payloads, outcomes)
    for name, binding in register["control_bindings"].items():
        assert binding["period_identities"] == {
            stage: f"{name}:{stage}" for stage in ("DEV_2", "DEV_3")
        }
        assert binding["identity"] not in binding["period_identities"].values()
    fixed = register["families"]["FIXED_ECONOMIC_GRAPH_RESIDUAL"]["primary"]
    assert "POOLED_NON_GRAPH_RESIDUAL" in fixed["deltas"]
    assert all(
        value > 0.0
        for value in fixed["deltas_by_instrument"]["POOLED_NON_GRAPH_RESIDUAL"]["DEV_2"].values()
    )
    with pytest.raises(ValueError, match="out of forecast support"):
        bad_payloads = list(payloads)
        bad_payloads[-1] = dict(bad_payloads[-1])
        bad_payloads[-1]["target_keys"] = tuple(
            list(cast(tuple[str, ...], bad_payloads[-1]["target_keys"]))[:-1]
        )
        evaluate_development_register(bad_payloads, outcomes)


def test_payload_total_uses_float32_serialized_arithmetic() -> None:
    payload = _terminal_payload()
    keys = cast(tuple[str, ...], payload["target_keys"])
    payload["local_ridge_forecast"] = [0.1] * len(keys)
    payload["residual_prediction"] = [0.2] * len(keys)
    payload["total_forecast"] = [0.30000001192092896] * len(keys)
    rows = _payload_rows([payload], _terminal_outcomes(), periods=("TERMINAL_FORMER_HOLDOUT",))
    assert rows[0]["forecast"] == pytest.approx(0.30000001192092896)
    payload["total_forecast"] = [0.30000000000000004] * len(keys)
    with pytest.raises(ValueError, match="total forecast"):
        _payload_rows([payload], _terminal_outcomes(), periods=("TERMINAL_FORMER_HOLDOUT",))


def test_scalar_mapping_outcomes_are_rejected_explicitly() -> None:
    payload = _terminal_payload()
    key = next(iter(cast(tuple[str, ...], payload["target_keys"])))
    with pytest.raises(ValueError, match="canonical mapping records"):
        _payload_rows(
            [payload],
            cast(Any, {key: 1.0}),
            periods=("TERMINAL_FORMER_HOLDOUT",),
        )


def test_spearman_uses_average_tie_ranks() -> None:
    import numpy as np

    forecasts = np.asarray([1.0, 1.0, 2.0, 3.0])
    targets = np.asarray([4.0, 2.0, 3.0, 1.0])
    assert _correlation(forecasts, targets, "spearman") == pytest.approx(-0.6324555320)
    assert _correlation(forecasts, np.ones(4), "spearman") is None
    assert _correlation(np.ones(4), targets, "spearman") is None


def test_direct_zero_skill_is_dimensionless_and_handles_zero_denominator() -> None:
    rows = [
        {
            "family_id": "LOCAL_TEMPORAL_RESIDUAL",
            "seed": 17,
            "period": period,
            "instrument_id": instrument,
            "decision_time": "2024-01-01T00:00:00+00:00",
            "target_return": 10.0,
            "forecast": 8.0,
            "zero_forecast": 0.0,
            "local_forecast": 8.0,
            "pooled_local_forecast": 8.0,
            "residual_correction": 0.0,
        }
        for period in ("DEV_2", "DEV_3")
        for instrument in ALL_INSTRUMENTS
    ]
    metrics = _family_metrics(rows, "LOCAL_TEMPORAL_RESIDUAL", seed=17)
    assert metrics["direct_zero_skill"]["DEV_2"] == pytest.approx(0.96)
    assert metrics["direct_zero_skill"]["combined_development"] == pytest.approx(0.96)
    assert metrics["incremental_mse"]["ZERO_RETURN"]["DEV_2"] == pytest.approx(96.0)

    zero_rows = [dict(row, target_return=0.0, forecast=0.0, local_forecast=0.0) for row in rows]
    zero_metrics = _family_metrics(zero_rows, "LOCAL_TEMPORAL_RESIDUAL", seed=17)
    assert zero_metrics["direct_zero_skill"]["DEV_2"] is None


def test_fit_evidence_requires_canonical_fields_and_values() -> None:
    slot_id = "LOCAL_TEMPORAL_RESIDUAL:17:DEV_2"
    valid = {
        "elapsed_seconds": 1.5,
        "parameter_count": 10,
        "epochs": 2,
        "target_instruments": len(ALL_INSTRUMENTS),
        "equal_instrument_loss": True,
        "training_batch_identity": "a" * 64,
    }
    assert _validate_fit_evidence(valid, slot_id=slot_id)["parameter_count"] == 10
    for bad in (
        {**valid, "extra": 1},
        {key: value for key, value in valid.items() if key != "epochs"},
        {**valid, "parameter_count": True},
        {**valid, "elapsed_seconds": float("nan")},
        {**valid, "training_batch_identity": "b" * 64},
    ):
        with pytest.raises(ValueError):
            _validate_fit_evidence(
                bad,
                slot_id=slot_id,
                expected_training_batch_identity="a" * 64,
            )


def test_fit_evidence_summary_covers_exact_slots_and_capacity_differences() -> None:
    from experiments.r4_residual_graph.runtime import PRIMARY_SCHEDULE

    counts = {
        "LOCAL_TEMPORAL_RESIDUAL": 10,
        "POOLED_NON_GRAPH_RESIDUAL": 12,
        "FIXED_ECONOMIC_GRAPH_RESIDUAL": 20,
        "LEARNED_STATIC_GRAPH_RESIDUAL": 24,
        "SHUFFLED_FIXED_GRAPH_RESIDUAL": 20,
    }
    evidence = {
        f"{family}:{seed}:{stage}": {
            "elapsed_seconds": 1.0,
            "parameter_count": counts[family],
            "epochs": 2,
            "target_instruments": len(ALL_INSTRUMENTS),
            "equal_instrument_loss": True,
            "training_batch_identity": "a" * 64,
        }
        for family, seed, stage in PRIMARY_SCHEDULE
    }
    summary = _summarize_fit_evidence(evidence, expected_slots=tuple(evidence))
    assert summary["slot_count"] == 45
    sample = summary["slots"]["LOCAL_TEMPORAL_RESIDUAL:17:DEV_2"]
    assert sample["elapsed_seconds"] == 1.0
    assert sample["parameter_count"] == 10
    assert sample["training_batch_identity"] == "a" * 64
    assert summary["capacity"]["controls"] == "N/A"
    assert summary["capacity"]["fixed_vs_shuffled_equal"] is True
    assert summary["capacity"]["pooled_non_graph_difference"] == 2
    assert summary["capacity"]["learned_adjacency_extra"] == 4
    with pytest.raises(ValueError):
        _summarize_fit_evidence(
            {key: value for key, value in evidence.items() if key != next(iter(evidence))},
            expected_slots=tuple(evidence),
        )


def test_authenticated_terminal_evaluation_labels_post_outcome_exploratory() -> None:
    payloads = [
        _terminal_payload(f"{family}:17:TERMINAL_FORMER_HOLDOUT")
        for family in (
            "LOCAL_TEMPORAL_RESIDUAL",
            "POOLED_NON_GRAPH_RESIDUAL",
            "FIXED_ECONOMIC_GRAPH_RESIDUAL",
            "LEARNED_STATIC_GRAPH_RESIDUAL",
            "SHUFFLED_FIXED_GRAPH_RESIDUAL",
        )
    ]
    evaluation = evaluate_terminal_register(
        payloads,
        _terminal_outcomes(),
        development_register={
            "artifact_type": "R4.C_DEVELOPMENT_METRIC_GATE_REGISTER",
            "families": {},
            "coverage": {},
        },
    )
    assert evaluation["scientific_performance"] == "COMPUTED_POST_HOC_HISTORICAL_EXPLORATORY"


def test_control_binding_preserves_period_identities_and_rejects_local_drift() -> None:
    from experiments.r4_residual_graph.evaluation import _control_binding

    rows = [
        {
            "period": period,
            "instrument_id": "instrument",
            "decision_time": period,
            "zero_forecast": 0.0,
            "support_identity": "common-support",
            "linear_control_identities": {"ZERO_RETURN": identity},
        }
        for period, identity in (("DEV_2", "second"), ("DEV_3", "third"))
    ]
    binding = _control_binding(rows, "ZERO_RETURN")
    assert binding["period_identities"] == {"DEV_2": "second", "DEV_3": "third"}
    assert binding["identity"] == _control_binding(list(reversed(rows)), "ZERO_RETURN")["identity"]
    assert _control_binding(rows[:1], "ZERO_RETURN")["identity"] == "second"
    terminal = dict(rows[0], period="TERMINAL_FORMER_HOLDOUT")
    assert _control_binding([terminal], "ZERO_RETURN")["identity"] == "second"
    drifted = dict(
        rows[0], decision_time="another", linear_control_identities={"ZERO_RETURN": "other"}
    )
    with pytest.raises(ValueError, match="control identity drifted for ZERO_RETURN in DEV_2"):
        _control_binding([*rows, drifted], "ZERO_RETURN")
    changed_period = dict(rows[1], linear_control_identities={"ZERO_RETURN": "changed"})
    changed_binding = _control_binding([rows[0], changed_period], "ZERO_RETURN")
    assert changed_binding["identity"] != binding["identity"]


def test_comparison_delta_preserves_exact_reductions_without_per_instrument_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.r4_residual_graph import evaluation

    candidate = [dict(row, forecast=(index - 7) / 13.0) for index, row in enumerate(_metric_rows())]
    candidate.append(dict(candidate[0], decision_time="unequal-support", forecast=-1e-12))
    comparator = [dict(row, comparator=(index + 3) / 17.0) for index, row in enumerate(candidate)]
    original = evaluation._mse_values
    expected = {
        instrument: original(comparator, "comparator")[instrument]
        - original(candidate, "forecast")[instrument]
        for instrument in sorted(ALL_INSTRUMENTS)
    }
    calls: list[str] = []

    def measured(rows: Any, key: str) -> dict[str, float]:
        calls.append(key)
        return original(rows, key)

    monkeypatch.setattr(evaluation, "_mse_values", measured)
    assert comparison_delta(candidate, comparator, comparator_key="comparator") == expected
    assert calls == ["comparator", "forecast"]


def test_terminal_core_six_metrics_keep_primary_and_auxiliary_seeds_separate() -> None:
    primary = _terminal_payload("LOCAL_TEMPORAL_RESIDUAL:17:TERMINAL_FORMER_HOLDOUT")
    auxiliary = _terminal_payload("LOCAL_TEMPORAL_RESIDUAL:29:TERMINAL_FORMER_HOLDOUT")
    auxiliary["total_forecast"] = [3.0] * len(ALL_INSTRUMENTS)
    auxiliary["residual_prediction"] = [3.0] * len(ALL_INSTRUMENTS)
    result = evaluate_terminal_register(
        [primary, auxiliary],
        _terminal_outcomes(),
        development_register={
            "artifact_type": "R4.C_DEVELOPMENT_METRIC_GATE_REGISTER",
            "families": {},
            "coverage": {},
        },
    )
    family = result["families"]["LOCAL_TEMPORAL_RESIDUAL"]
    assert family["seeds"]["17"]["core_six"]["TERMINAL_FORMER_HOLDOUT"]["mse"] == 1.0
    assert family["seeds"]["29"]["core_six"]["TERMINAL_FORMER_HOLDOUT"]["mse"] == 4.0
    assert family["primary"] == family["seeds"]["17"]
