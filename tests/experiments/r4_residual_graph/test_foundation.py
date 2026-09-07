from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl
import pytest

from experiments.r2_historical_lab.lab_0.harness import configuration_id
from experiments.r4_residual_graph import (
    ALL_INSTRUMENTS,
    FULLY_POOLED_CONFIG_ID,
    LOCAL_CONFIG_ID,
    authenticate_oof_residual_foundation,
    chronological_memberships,
    materialise_development_support,
)
from experiments.r4_residual_graph import foundation as foundation_module
from experiments.r4_residual_graph.foundation import _configuration


def test_canonical_linear_configuration_ids() -> None:
    assert configuration_id(_configuration("ALL_20", "LOCAL_RIDGE")) == LOCAL_CONFIG_ID
    assert (
        configuration_id(_configuration("ALL_20", "FULLY_POOLED_RIDGE")) == FULLY_POOLED_CONFIG_ID
    )


def test_chronological_memberships_are_expanding() -> None:
    rows = pl.DataFrame(
        {
            "block": ["DEV_1", "DEV_2", "DEV_3"],
            "decision_time": [
                datetime(2026, 5, 15, 14, 6, tzinfo=UTC),
                datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
                datetime(2026, 6, 12, 14, 6, tzinfo=UTC),
            ],
            "local_configuration_id": [LOCAL_CONFIG_ID] * 3,
            "forecast_fold": ["DEV_1", "DEV_2", "DEV_3"],
            "instrument_id": [ALL_INSTRUMENTS[0]] * 3,
            "forecast_identity": [
                foundation_module._forecast_identity(
                    LOCAL_CONFIG_ID,
                    block,
                    ALL_INSTRUMENTS[0],
                    decision_time,
                )
                for block, decision_time in zip(
                    ["DEV_1", "DEV_2", "DEV_3"],
                    [
                        datetime(2026, 5, 15, 14, 6, tzinfo=UTC),
                        datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
                        datetime(2026, 6, 12, 14, 6, tzinfo=UTC),
                    ],
                    strict=True,
                )
            ],
        }
    )
    result = chronological_memberships(rows)
    assert result["residual_training_membership"].to_list() == [
        "WARMUP",
        "DEV_1",
        "DEV_1+DEV_2",
    ]


def test_chronological_memberships_rejects_pre_stage_timestamp() -> None:
    rows = (
        _oof_rows()
        .filter(pl.col("block") == "DEV_1")
        .with_columns(pl.lit(datetime(2026, 5, 15, 14, 5, tzinfo=UTC)).alias("decision_time"))
    )
    with pytest.raises(ValueError, match="outside its authoritative stage window"):
        chronological_memberships(rows)


def test_development_support_requires_positive_all_twenty() -> None:
    start = datetime(2026, 5, 29, 14, 6, tzinfo=UTC)
    times = [start - timedelta(minutes=offset) for offset in range(61)]
    rows = pl.DataFrame(
        {
            "target_valid": [True] * (len(ALL_INSTRUMENTS) * len(times)),
            "target_available_at": [start] * (len(ALL_INSTRUMENTS) * len(times)),
            "block": [
                block for _ in ALL_INSTRUMENTS for block in ["DEV_2", *(["TRAINING_ONLY"] * 60)]
            ],
            "instrument_id": [instrument for instrument in ALL_INSTRUMENTS for _ in times],
            "decision_time": [time for _ in ALL_INSTRUMENTS for time in times],
        }
    )
    with pytest.raises(ValueError, match="authenticated LAB-0 has no target children"):
        materialise_development_support(
            rows,
            capsule=foundation_module.Lab0Capsule._create(
                foundation_module._LAB0_SEAL,
                Path("/tmp/test-manifest"),
                "manifest-test",
                {},
                ALL_INSTRUMENTS,
                (),
            ),
        )


def test_chronology_rejects_unknown_block() -> None:
    rows = pl.DataFrame(
        {
            "block": ["UNKNOWN"],
            "decision_time": [datetime(2026, 5, 15, tzinfo=UTC)],
            "local_configuration_id": [LOCAL_CONFIG_ID],
            "forecast_fold": ["UNKNOWN"],
        }
    )
    with pytest.raises(ValueError, match="unrecognised block"):
        chronological_memberships(rows)


def _oof_rows() -> pl.DataFrame:
    rows = [
        ("TRAINING_ONLY", datetime(2026, 5, 1, tzinfo=UTC), 0.01),
        ("DEV_1", datetime(2026, 5, 15, 14, 6, tzinfo=UTC), 0.02),
        ("DEV_2", datetime(2026, 5, 29, 14, 6, tzinfo=UTC), 0.03),
        ("DEV_3", datetime(2026, 6, 12, 14, 6, tzinfo=UTC), 0.04),
    ]
    return pl.DataFrame(
        {
            "target_id": [f"target-{index}" for index in range(len(rows))],
            "instrument_id": [ALL_INSTRUMENTS[0]] * len(rows),
            "decision_time": [row[1] for row in rows],
            "target_available_at": [row[1] + timedelta(minutes=15) for row in rows],
            "block": [row[0] for row in rows],
            "horizon_minutes": [15] * len(rows),
            "target_return": [row[2] for row in rows],
            "target_valid": [True] * len(rows),
            "feature_data_asof": [row[1] for row in rows],
            "latest_feature_bar_end": [row[1] - timedelta(minutes=5) for row in rows],
            "return_60s_available": [0.0] * len(rows),
            "return_300s_available": [0.0] * len(rows),
            "cross_market_available_count": [0.0] * len(rows),
            "gap_known_by_cutoff": [0.0] * len(rows),
        }
    )


def test_oof_is_causal_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[datetime, int]] = []

    def fake_prediction(
        training: pl.DataFrame,
        validation: pl.DataFrame,
        config: dict[str, object],
        fit_time: datetime,
    ) -> tuple[np.ndarray, dict[str, object]]:
        calls.append((fit_time, training.height))
        assert training.filter(pl.col("decision_time") >= fit_time).is_empty()
        return np.zeros(validation.height), {}

    monkeypatch.setattr(foundation_module, "_raw_prediction", fake_prediction)
    result = foundation_module.build_oof_residual_foundation(
        _oof_rows(),
        capsule=foundation_module.Lab0Capsule._create(
            foundation_module._LAB0_SEAL,
            Path("/tmp/test-manifest"),
            "manifest-test",
            {},
            ALL_INSTRUMENTS,
            (),
        ),
    )
    assert result.height == 3
    assert result["local_residual"].to_list() == [0.02, 0.03, 0.04]
    assert result["forecast_fold"].to_list() == ["DEV_1", "DEV_2", "DEV_3"]
    assert result["manifest_sha256"].unique().to_list() == ["manifest-test"]
    assert result["local_configuration_id"].unique().to_list() == [LOCAL_CONFIG_ID]
    assert result["forecast_identity"].n_unique() == result.height
    assert [count for _, count in calls] == [1, 2, 3]


def test_oof_rows_reject_fabricated_or_incomplete_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_prediction(
        training: pl.DataFrame,
        validation: pl.DataFrame,
        config: dict[str, object],
        fit_time: datetime,
    ) -> tuple[np.ndarray, dict[str, object]]:
        del training, config, fit_time
        return np.zeros(validation.height), {}

    monkeypatch.setattr(foundation_module, "_raw_prediction", fake_prediction)
    rows = foundation_module.build_oof_residual_foundation(
        _oof_rows(),
        capsule=foundation_module.Lab0Capsule._create(
            foundation_module._LAB0_SEAL,
            Path("/tmp/test-manifest"),
            "manifest-test",
            {},
            ALL_INSTRUMENTS,
            (),
        ),
    )
    fabricated = foundation_module.Lab0Capsule._create(
        foundation_module._LAB0_SEAL,
        Path("/tmp/test-manifest"),
        foundation_module.MANIFEST_SHA256,
        {},
        ALL_INSTRUMENTS,
        (),
    )
    object.__setattr__(fabricated, "_authenticated_parent", foundation_module._LAB0_SEAL)
    with pytest.raises(ValueError, match="complete all-twenty"):
        authenticate_oof_residual_foundation(rows, capsule=fabricated)

    with pytest.raises(TypeError, match="authenticated LAB-0 parent"):
        authenticate_oof_residual_foundation(
            rows,
            capsule=foundation_module.Lab0Capsule._create(
                foundation_module._LAB0_SEAL,
                Path("/tmp/test-manifest"),
                foundation_module.MANIFEST_SHA256,
                {},
                ALL_INSTRUMENTS,
                (),
            ),
        )


def test_oof_fails_closed_on_nonfinite_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_prediction(
        training: pl.DataFrame,
        validation: pl.DataFrame,
        config: dict[str, object],
        fit_time: datetime,
    ) -> tuple[np.ndarray, dict[str, object]]:
        return np.full(validation.height, np.nan), {}

    monkeypatch.setattr(foundation_module, "_raw_prediction", failed_prediction)
    with pytest.raises(ValueError, match="failed closed"):
        foundation_module.build_oof_residual_foundation(
            _oof_rows(),
            capsule=foundation_module.Lab0Capsule._create(
                foundation_module._LAB0_SEAL,
                Path("/tmp/test-manifest"),
                "manifest-test",
                {},
                ALL_INSTRUMENTS,
                (),
            ),
        )


def test_oof_requires_complete_development_chronology() -> None:
    with pytest.raises(ValueError, match="all development blocks"):
        foundation_module.build_oof_residual_foundation(_oof_rows(), block_names=("DEV_1",))


def test_support_sidecar_is_create_only(tmp_path: Path) -> None:
    start = datetime(2026, 5, 29, 14, 6, tzinfo=UTC)
    times = [start - timedelta(minutes=offset) for offset in range(61)]
    rows = pl.DataFrame(
        {
            "target_valid": [True] * (len(ALL_INSTRUMENTS) * len(times)),
            "target_available_at": [start] * (len(ALL_INSTRUMENTS) * len(times)),
            "block": [
                block for _ in ALL_INSTRUMENTS for block in ["DEV_2", *(["TRAINING_ONLY"] * 60)]
            ],
            "instrument_id": [instrument for instrument in ALL_INSTRUMENTS for _ in times],
            "decision_time": [time for _ in ALL_INSTRUMENTS for time in times],
        }
    )
    output_path = tmp_path / "support.parquet"
    metadata_path = output_path.with_suffix(".json")
    metadata_path.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError, match="sidecar"):
        materialise_development_support(
            rows,
            output_path=output_path,
            capsule=foundation_module.Lab0Capsule._create(
                foundation_module._LAB0_SEAL,
                Path("/tmp/test-manifest"),
                "manifest-test",
                {},
                ALL_INSTRUMENTS,
                (),
            ),
        )
    assert not output_path.exists()


def test_support_metadata_binds_parent_identity(tmp_path: Path) -> None:
    start = datetime(2026, 5, 29, 14, 6, tzinfo=UTC)
    times = [start - timedelta(minutes=offset) for offset in range(61)]
    rows = pl.DataFrame(
        {
            "target_valid": [True] * (len(ALL_INSTRUMENTS) * len(times)),
            "target_available_at": [start] * (len(ALL_INSTRUMENTS) * len(times)),
            "block": [
                block for _ in ALL_INSTRUMENTS for block in ["DEV_2", *(["TRAINING_ONLY"] * 60)]
            ],
            "instrument_id": [instrument for instrument in ALL_INSTRUMENTS for _ in times],
            "decision_time": [time for _ in ALL_INSTRUMENTS for time in times],
        }
    )
    capsule = foundation_module.Lab0Capsule._create(
        foundation_module._LAB0_SEAL,
        Path("/tmp/test-manifest"),
        "manifest-test",
        {},
        ALL_INSTRUMENTS,
        (),
    )
    with pytest.raises(ValueError, match="authenticated LAB-0 has no target children"):
        materialise_development_support(
            rows, output_path=tmp_path / "support.parquet", capsule=capsule
        )


def test_authenticated_core_six_anchor_gate() -> None:
    capsule = foundation_module.Lab0Capsule._create(
        foundation_module._LAB0_SEAL,
        Path("/tmp/test-manifest"),
        "manifest-test",
        {
            "baseline_reconstruction": {
                "observed": {
                    "support": 239_535,
                    "ZERO_RETURN": 0.0000028404586671320294,
                    "POOLED_LOCAL_RIDGE": 0.000002841663414474555,
                    "LOCAL_RIDGE": 0.0000028481068080631273,
                }
            }
        },
        ALL_INSTRUMENTS,
        (),
    )
    result = foundation_module.authenticate_core_six_baseline(capsule)
    assert result["support"] == 239_535
    assert result["ordering"] == "ZERO_RETURN<POOLED_LOCAL_RIDGE<LOCAL_RIDGE"


def test_oof_fails_closed_on_invalid_target() -> None:
    rows = _oof_rows().with_columns(
        pl.when(pl.col("block") == "DEV_1").then(False).otherwise(True).alias("target_valid")
    )
    with pytest.raises(ValueError, match="invalid targets"):
        foundation_module.build_oof_residual_foundation(
            rows,
            capsule=foundation_module.Lab0Capsule._create(
                foundation_module._LAB0_SEAL,
                Path("/tmp/test-manifest"),
                "manifest-test",
                {},
                ALL_INSTRUMENTS,
                (),
            ),
        )


def test_oof_fails_closed_on_feature_cutoff() -> None:
    rows = _oof_rows().with_columns(
        pl.lit(datetime(2026, 5, 15, 14, 6, tzinfo=UTC)).alias("feature_data_asof")
    )
    with pytest.raises(ValueError, match="feature availability"):
        foundation_module.build_oof_residual_foundation(
            rows,
            capsule=foundation_module.Lab0Capsule._create(
                foundation_module._LAB0_SEAL,
                Path("/tmp/test-manifest"),
                "manifest-test",
                {},
                ALL_INSTRUMENTS,
                (),
            ),
        )


def test_reconstruction_rejects_terminal_rows() -> None:
    rows = pl.DataFrame({"block": [foundation_module.TERMINAL_BLOCK]})
    with pytest.raises(ValueError, match="unauthorised block"):
        foundation_module.reconstruct_linear_controls(rows, capsule=None)


def test_chronology_rejects_tampered_identity() -> None:
    times = [
        datetime(2026, 5, 15, 14, 6, tzinfo=UTC),
        datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
        datetime(2026, 6, 12, 14, 6, tzinfo=UTC),
    ]
    blocks = ["DEV_1", "DEV_2", "DEV_3"]
    rows = pl.DataFrame(
        {
            "block": blocks,
            "decision_time": times,
            "local_configuration_id": [LOCAL_CONFIG_ID] * 3,
            "forecast_fold": blocks,
            "instrument_id": [ALL_INSTRUMENTS[0]] * 3,
            "forecast_identity": [
                foundation_module._forecast_identity(
                    LOCAL_CONFIG_ID, block, ALL_INSTRUMENTS[0], time
                )
                for block, time in zip(blocks, times, strict=True)
            ],
        }
    )
    with pytest.raises(ValueError, match="forecast fold"):
        chronological_memberships(rows.with_columns(pl.lit("DEV_3").alias("forecast_fold")))
    with pytest.raises(ValueError, match="configuration identity"):
        chronological_memberships(
            rows.with_columns(pl.lit("wrong").alias("local_configuration_id"))
        )
    with pytest.raises(ValueError, match="forecast identity"):
        chronological_memberships(rows.with_columns(pl.lit("tampered").alias("forecast_identity")))


def test_post_seal_validation_rejects_missing_instrument_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_times = {
        "TRAINING_ONLY": datetime(2026, 5, 1, 14, 6, tzinfo=UTC),
        "DEV_1": datetime(2026, 5, 15, 14, 6, tzinfo=UTC),
        "DEV_2": datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
        "DEV_3": datetime(2026, 6, 12, 14, 6, tzinfo=UTC),
    }
    rows = pl.DataFrame(
        [
            {
                "target_id": f"{block}-{instrument}",
                "instrument_id": instrument,
                "decision_time": decision_time,
                "target_available_at": decision_time + timedelta(minutes=15),
                "block": block,
                "horizon_minutes": 15,
                "target_return": 0.01,
                "target_valid": True,
                "feature_data_asof": decision_time - timedelta(minutes=1),
                "latest_feature_bar_end": decision_time - timedelta(minutes=5),
                "return_60s_available": 0.0,
                "return_300s_available": 0.0,
                "cross_market_available_count": 0.0,
                "gap_known_by_cutoff": 0.0,
            }
            for instrument in ALL_INSTRUMENTS
            for block, decision_time in block_times.items()
        ]
    )
    monkeypatch.setattr(
        foundation_module,
        "_raw_prediction",
        lambda training, validation, config, fit_time: (np.zeros(validation.height), {}),
    )
    capsule = foundation_module.Lab0Capsule._create(
        foundation_module._LAB0_SEAL,
        Path("/tmp/test-manifest"),
        foundation_module.MANIFEST_SHA256,
        {},
        ALL_INSTRUMENTS,
        (),
    )
    object.__setattr__(capsule, "_authenticated_parent", foundation_module._LAB0_SEAL)
    residuals = foundation_module.build_oof_residual_foundation(rows, capsule=capsule)
    expected = residuals.select([*foundation_module._RESIDUAL_KEY_COLUMNS, "block"])
    foundation_module._validate_exact_residual_coverage(residuals, expected)
    tampered = residuals.filter(pl.col("instrument_id") != ALL_INSTRUMENTS[-1])
    with pytest.raises(ValueError, match="does not exactly cover authenticated structural keys"):
        foundation_module._validate_exact_residual_coverage(tampered, expected)


def test_exact_residual_coverage_rejects_substitution_and_count_mismatch() -> None:
    expected = pl.DataFrame(
        {
            "target_id": ["a", "b"],
            "instrument_id": [ALL_INSTRUMENTS[0], ALL_INSTRUMENTS[1]],
            "decision_time": [
                datetime(2026, 5, 15, 14, 6, tzinfo=UTC),
                datetime(2026, 5, 15, 14, 6, tzinfo=UTC),
            ],
            "block": ["DEV_1", "DEV_1"],
        }
    )
    substituted = expected.with_columns(
        pl.when(pl.col("target_id") == "b")
        .then(pl.lit("substituted"))
        .otherwise(pl.col("instrument_id"))
        .alias("instrument_id")
    )
    with pytest.raises(ValueError, match="does not exactly cover authenticated structural keys"):
        foundation_module._validate_exact_residual_coverage(substituted, expected)
    count_mismatch = expected.head(1)
    with pytest.raises(ValueError, match="does not exactly cover authenticated structural keys"):
        foundation_module._validate_exact_residual_coverage(count_mismatch, expected)


def test_exact_oof_values_reject_substitution_preserving_arithmetic() -> None:
    expected = pl.DataFrame(
        {
            "target_id": ["a"],
            "instrument_id": [ALL_INSTRUMENTS[0]],
            "decision_time": [datetime(2026, 5, 15, 14, 6, tzinfo=UTC)],
            "horizon_minutes": [15],
            "target_available_at": [datetime(2026, 5, 15, 14, 21, tzinfo=UTC)],
            "block": ["DEV_1"],
            "target_return": [0.02],
            "local_forecast": [0.01],
            "local_residual": [0.01],
            "local_configuration_id": [LOCAL_CONFIG_ID],
            "forecast_fold": ["DEV_1"],
            "manifest_sha256": ["m" * 64],
            "child_closure_sha256": ["c" * 64],
            "feature_semantic_sha256": ["f" * 64],
            "evidence_label": ["LAB"],
            "source_class": ["NATIVE"],
            "forecast_identity": ["canonical-forecast"],
            "residual_training_membership": ["WARMUP"],
        }
    )
    tampered = expected.with_columns(
        (pl.col("local_forecast") + 0.01).alias("local_forecast"),
        (pl.col("local_residual") - 0.01).alias("local_residual"),
    )
    with pytest.raises(ValueError, match="values differ from authenticated OOF computation"):
        foundation_module._validate_exact_oof_values(tampered, expected)
    maturity_tampered = expected.with_columns(
        (pl.col("target_available_at") + timedelta(minutes=1)).alias("target_available_at")
    )
    with pytest.raises(ValueError, match="values differ from authenticated OOF computation"):
        foundation_module._validate_exact_oof_values(maturity_tampered, expected)
    provenance_tampered = expected.with_columns(
        pl.lit("DEV_2").alias("block"),
        pl.lit("DEV_2").alias("forecast_fold"),
    )
    with pytest.raises(ValueError, match="values differ from authenticated OOF computation"):
        foundation_module._validate_exact_oof_values(provenance_tampered, expected)


def _dev_control_rows() -> pl.DataFrame:
    from experiments.r4_residual_graph.tensor import P0_FEATURE_NAMES

    times = [
        datetime(2026, 6, 25, 14, 0, tzinfo=UTC),
        datetime(2026, 6, 25, 14, 1, tzinfo=UTC),
    ]
    rows: list[dict[str, object]] = []
    for index, decision_time in enumerate(times):
        row: dict[str, object] = {
            "target_id": f"dev-target-{index}",
            "instrument_id": ALL_INSTRUMENTS[0],
            "decision_time": decision_time,
            "target_available_at": decision_time + timedelta(minutes=15),
            "target_valid": True,
            "block": "DEV_1",
            "horizon_minutes": 15,
            "target_return": 0.01 + index / 1000,
        }
        row.update({name: 0.0 for name in P0_FEATURE_NAMES})
        rows.append(row)
    return pl.DataFrame(rows)


@pytest.mark.parametrize("rows_per_block", [2, 4])
def test_preparation_parent_reuses_load_with_one_independent_oof_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows_per_block: int
) -> None:
    from types import SimpleNamespace

    from experiments.r2_historical_lab.lab_s.statistical import FEATURE_NAMES
    from experiments.r4_residual_graph.tensor import (
        P0_FEATURE_NAMES,
        authenticate_support_corpus,
    )

    parent = _dev_control_parent()
    starts = (
        ("TRAINING_ONLY", datetime(2026, 5, 1, tzinfo=UTC)),
        *[(block, window[0]) for block, window in foundation_module._STAGE_WINDOWS.items()],
    )
    manifest: dict[str, Any] = {
        "fold_blocks": [{"name": block, "start": time.isoformat()} for block, time in starts]
    }
    object.__setattr__(parent, "manifest", manifest)
    targets: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    for block, start in starts:
        for offset in range(rows_per_block):
            time = start + timedelta(minutes=offset)
            for instrument in ALL_INSTRUMENTS:
                targets.append(
                    {
                        "target_id": f"{instrument}|{time.isoformat()}",
                        "instrument_id": instrument,
                        "decision_time": time,
                        "target_available_at": time + timedelta(minutes=15),
                        "target_valid": True,
                        "block": block,
                        "horizon_minutes": 15,
                        "target_return": 0.01 + offset / 1000,
                    }
                )
                features.append(
                    {
                        **dict.fromkeys((*FEATURE_NAMES, *P0_FEATURE_NAMES), float(offset)),
                        "instrument_id": instrument,
                        "decision_time": time,
                        "feature_data_asof": time,
                        "feature_available_at": time,
                        "latest_feature_bar_end": time - timedelta(minutes=5),
                        "source_active": 1.0,
                        "cross_market_missing_count": 0.0,
                    }
                )
    target_frame = pl.DataFrame(targets)
    feature_frame = pl.DataFrame(features).drop("cross_market_available_count")
    context_frame = feature_frame.select("instrument_id", "decision_time").with_columns(
        pl.lit(1).alias("current_available")
    )
    loads: list[str] = []

    def target_loader(_capsule: Any, **kwargs: Any) -> pl.DataFrame:
        loads.append("target")
        return target_frame if kwargs["include_return"] else target_frame.drop("target_return")

    def feature_loader(_capsule: Any) -> pl.DataFrame:
        loads.append("feature")
        return feature_frame

    def context_loader(_capsule: Any) -> pl.DataFrame:
        loads.append("context")
        return context_frame

    monkeypatch.setattr(foundation_module, "_target_frame", target_loader)
    monkeypatch.setattr(foundation_module, "_feature_frame", feature_loader)
    monkeypatch.setattr(foundation_module, "_context_frame", context_loader)
    baseline_rows = foundation_module.load_parent_rows(parent)
    baseline_residuals = foundation_module.build_oof_residual_foundation(
        baseline_rows, capsule=parent
    )
    baseline = authenticate_oof_residual_foundation(baseline_residuals, capsule=parent)
    baseline_support, baseline_metadata = materialise_development_support(
        baseline_rows, capsule=parent
    )
    baseline_projection = foundation_module.project_authenticated_parent_support_rows(parent)
    baseline_corpus = authenticate_support_corpus(baseline_projection, parent)
    support = SimpleNamespace(
        identity="a" * 64,
        target_keys=tuple(
            f"{row['instrument_id']}|{row['decision_time'].isoformat()}"
            for row in baseline_projection.iter_rows(named=True)
        ),
    )
    validation = {
        block: baseline_projection.filter(pl.col("block") == block)
        for block in foundation_module.DEVELOPMENT_BLOCKS
    }
    baseline_controls = foundation_module.reconstruct_authenticated_linear_forecasts(
        baseline_rows,
        capsule=parent,
        validation_by_period=validation,
        support=support,
        support_identity=support.identity,
    )
    loads.clear()
    builds = 0
    build = foundation_module.build_oof_residual_foundation

    def counted_build(rows: pl.DataFrame, **kwargs: Any) -> pl.DataFrame:
        nonlocal builds
        builds += 1
        return build(rows, **kwargs)

    monkeypatch.setattr(foundation_module, "build_oof_residual_foundation", counted_build)
    prepared = foundation_module._preparation_parent(parent)
    rows = foundation_module.load_parent_rows(prepared)
    residuals = counted_build(rows, capsule=prepared)
    sealed = authenticate_oof_residual_foundation(
        residuals, capsule=prepared, output_path=tmp_path / "oof.parquet"
    )
    actual_support, actual_metadata = materialise_development_support(
        rows, capsule=prepared, output_path=tmp_path / "support.parquet"
    )
    projection = foundation_module.project_authenticated_parent_support_rows(prepared)
    corpus = authenticate_support_corpus(projection, prepared)
    controls = foundation_module.reconstruct_authenticated_linear_forecasts(
        rows,
        capsule=prepared,
        validation_by_period=validation,
        support=support,
        support_identity=support.identity,
    )
    sealed._validate()
    assert builds == 2  # Current transformation plus exactly one independent replay.
    assert loads == ["target", "feature", "context"]
    assert sealed.to_dict() == baseline.to_dict()
    assert sealed.rows.equals(baseline.rows)
    assert pl.read_parquet(tmp_path / "oof.parquet").equals(baseline.rows)
    assert actual_support.equals(baseline_support)
    assert pl.read_parquet(tmp_path / "support.parquet").equals(baseline_support)
    assert actual_metadata == baseline_metadata
    assert corpus.authentication == baseline_corpus.authentication
    assert controls == baseline_controls
    rows.replace_column(
        rows.columns.index("target_return"), pl.Series("target_return", [9.0] * rows.height)
    )
    assert foundation_module.load_parent_rows(prepared).equals(baseline_rows)
    with pytest.raises(ValueError, match="authenticated parent projection"):
        foundation_module.reconstruct_authenticated_linear_forecasts(
            rows,
            capsule=prepared,
            validation_by_period=validation,
            support=support,
            support_identity=support.identity,
        )
    forged = residuals.with_columns(
        (pl.col("local_forecast") + 0.01).alias("local_forecast"),
        (pl.col("local_residual") - 0.01).alias("local_residual"),
    )
    with pytest.raises(ValueError, match=r"independent|OOF"):
        authenticate_oof_residual_foundation(forged, capsule=prepared)
    with pytest.raises(ValueError, match="exactly cover"):
        authenticate_oof_residual_foundation(residuals.slice(1), capsule=prepared)
    with pytest.raises(ValueError, match="differs from authenticated parent"):
        foundation_module.project_authenticated_support_rows(
            projection.with_columns(pl.lit(9.0).alias("source_active")), prepared
        )
    manifest["changed"] = True
    with pytest.raises(ValueError, match="parent identity changed"):
        foundation_module.load_parent_rows(prepared)


def _dev_control_parent() -> foundation_module.Lab0Capsule:
    capsule = foundation_module.Lab0Capsule._create(
        foundation_module._LAB0_SEAL,
        Path("/tmp/test-manifest"),
        foundation_module.MANIFEST_SHA256,
        {},
        ALL_INSTRUMENTS,
        (),
    )
    object.__setattr__(capsule, "_authenticated_parent", foundation_module._LAB0_SEAL)
    return capsule


@pytest.mark.parametrize("mutation", ["fabricated", "tampered", "reordered", "extra", "missing"])
def test_dev_control_capability_rejects_frame_mutation(mutation: str) -> None:
    parent = _dev_control_parent()
    rows = _dev_control_rows()
    capability = foundation_module._seal_dev_control_training(
        parent, rows, foundation_module.TERMINAL_START
    )
    if mutation in {"fabricated", "tampered"}:
        changed = rows.with_columns(
            pl.when(pl.arange(0, rows.height) == 0)
            .then(pl.lit(0.99))
            .otherwise(pl.col("target_return"))
            .alias("target_return")
        )
    elif mutation == "reordered":
        changed = rows.reverse()
    elif mutation == "extra":
        changed = rows.with_columns(pl.lit("forged").alias("extra_column"))
    else:
        changed = rows.drop("target_return")
    object.__setattr__(capability, "rows", changed)
    with pytest.raises((TypeError, ValueError), match=r"drifted|canonical|identity"):
        foundation_module._validate_dev_control_training(
            capability, parent, foundation_module.TERMINAL_START
        )


def test_dev_control_capability_exact_seal_validates() -> None:
    parent = _dev_control_parent()
    rows = _dev_control_rows()
    capability = foundation_module._seal_dev_control_training(
        parent, rows, foundation_module.TERMINAL_START
    )
    validated = foundation_module._validate_dev_control_training(
        capability, parent, foundation_module.TERMINAL_START
    )
    assert validated.equals(rows)


def test_terminal_prediction_input_rejects_arbitrary_training_rows() -> None:
    with pytest.raises(TypeError, match="training_rows"):
        cast(Any, foundation_module.authenticate_terminal_prediction_input)(
            parent=cast(Any, None),
            runtime_config=None,
            graph=None,
            terminal_metadata=None,
            terminal_capsule=None,
            preprocessor_identity="0" * 64,
            training_rows=pl.DataFrame(),
        )


@pytest.mark.parametrize("mutation", ["fabricated", "tampered", "reordered", "extra", "missing"])
def test_public_dev_control_authentication_rejects_frame_mutation(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    parent = _dev_control_parent()
    rows = _dev_control_rows()
    monkeypatch.setattr(foundation_module, "_authenticated_training_rows", lambda _parent: rows)
    if mutation in {"fabricated", "tampered"}:
        changed = rows.with_columns(
            pl.when(pl.arange(0, rows.height) == 1)
            .then(pl.lit(0.99))
            .otherwise(pl.col("target_return"))
            .alias("target_return")
        )
    elif mutation == "reordered":
        changed = rows.reverse()
    elif mutation == "extra":
        changed = rows.with_columns(pl.lit("forged").alias("extra_column"))
    else:
        changed = rows.drop("target_return")
    with pytest.raises(ValueError, match="differ from authenticated parent"):
        foundation_module.authenticate_dev_control_training(
            parent, changed, foundation_module.TERMINAL_START
        )


def test_public_dev_control_authentication_seals_exact_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _dev_control_parent()
    rows = _dev_control_rows()
    monkeypatch.setattr(foundation_module, "_authenticated_training_rows", lambda _parent: rows)
    capability = foundation_module.authenticate_dev_control_training(
        parent, rows, foundation_module.TERMINAL_START
    )
    assert foundation_module._validate_dev_control_training(
        capability, parent, foundation_module.TERMINAL_START
    ).equals(rows)


def test_expected_structural_keys_use_mature_targets_without_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mature = datetime(2026, 6, 12, 14, 6, tzinfo=UTC)
    parent = pl.DataFrame(
        {
            "target_id": ["target-a", "target-b", "target-immature"],
            "instrument_id": [ALL_INSTRUMENTS[0], ALL_INSTRUMENTS[1], ALL_INSTRUMENTS[0]],
            "decision_time": [mature, mature, mature],
            "target_available_at": [
                mature + timedelta(minutes=15),
                mature + timedelta(minutes=15),
                datetime(2026, 6, 27, tzinfo=UTC),
            ],
            "block": ["DEV_3", "DEV_3", "DEV_3"],
        }
    )
    capsule = foundation_module.Lab0Capsule._create(
        foundation_module._LAB0_SEAL,
        Path("/tmp/test-manifest"),
        "manifest-test",
        {},
        ALL_INSTRUMENTS,
        (),
    )
    monkeypatch.setattr(foundation_module, "load_parent_rows", lambda _: parent)
    expected = foundation_module._expected_oof_structural_rows(capsule)
    assert expected["target_id"].to_list() == ["target-a", "target-b"]
    assert expected.height == 2


def test_authenticated_parent_support_projection_is_outcome_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _dev_control_parent()
    full = _dev_control_rows().with_columns(
        pl.col("decision_time").alias("feature_data_asof"),
        pl.col("decision_time").alias("feature_available_at"),
        pl.lit(1).alias("cross_market_missing_count"),
    )
    calls: list[bool] = []

    def load(
        _capsule: foundation_module.Lab0Capsule,
        *,
        include_invalid: bool = False,
        include_return: bool = True,
    ) -> pl.DataFrame:
        del include_invalid
        calls.append(include_return)
        return full if include_return else full.drop("target_return")

    monkeypatch.setattr(foundation_module, "load_parent_rows", load)
    from experiments.r4_residual_graph.tensor import _SUPPORT_ALLOWED_COLUMNS

    projected = foundation_module.project_authenticated_parent_support_rows(parent)
    assert projected.columns == sorted(_SUPPORT_ALLOWED_COLUMNS)
    assert "target_return" not in projected.columns
    assert calls == [False]

    with pytest.raises(ValueError, match="unknown columns"):
        foundation_module.project_authenticated_support_rows(
            projected.with_columns(pl.lit("forged").alias("forged")), parent
        )
    assert calls == [False]


def test_support_corpus_serialises_full_parent_once_and_preserves_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import json

    from experiments.r4_residual_graph import tensor as tensor_module
    from experiments.r4_residual_graph.runtime import PARENT_IDENTITY

    parent = _dev_control_parent()
    frame = (
        _dev_control_rows()
        .with_columns(
            pl.col("decision_time").alias("feature_data_asof"),
            pl.col("decision_time").alias("feature_available_at"),
            pl.lit(1).alias("cross_market_missing_count"),
        )
        .select(sorted(tensor_module._SUPPORT_ALLOWED_COLUMNS))
    )
    records = frame.to_dicts()
    loads: list[bool] = []

    def load(_parent: Any, *, include_return: bool) -> pl.DataFrame:
        assert _parent is parent
        loads.append(include_return)
        return frame.reverse()

    monkeypatch.setattr(foundation_module, "load_parent_rows", load)
    canonical = [
        {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items()
        }
        for row in records
    ]
    expected_bytes = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    serialisations: list[bytes] = []
    serialise = tensor_module._canonical_bytes

    def counted(value: object) -> bytes:
        result = serialise(value)
        serialisations.append(result)
        return result

    monkeypatch.setattr(tensor_module, "_canonical_bytes", counted)
    # Supply only one row: the returned digest must still cover the complete parent.
    corpus = tensor_module.authenticate_support_corpus(records[:1], parent, outcome_free=True)
    assert serialisations == [expected_bytes]
    assert loads == [False]
    assert corpus.authentication == (
        parent.manifest_sha256,
        parent.child_closure_sha256,
        hashlib.sha256(expected_bytes).hexdigest(),
        PARENT_IDENTITY,
    )
    for column in sorted(tensor_module._SUPPORT_ALLOWED_COLUMNS):
        altered = dict(records[0])
        if column == "decision_time":
            altered[column] += timedelta(minutes=2)
        else:
            altered[column] = "forged"
        with pytest.raises(ValueError, match=r"not present|content differs"):
            tensor_module.authenticate_support_corpus([altered], parent, outcome_free=True)
    with pytest.raises(ValueError, match="outcome-bearing"):
        tensor_module.authenticate_support_corpus(
            [{**records[0], "target_return": 1.0}], parent, outcome_free=True
        )
    monkeypatch.setattr(
        foundation_module, "load_parent_rows", lambda *_args, **_kwargs: frame.drop("source_active")
    )
    with pytest.raises(ValueError, match="lacks canonical support column"):
        tensor_module.authenticate_support_corpus(records[:1], parent, outcome_free=True)
    assert serialisations == [expected_bytes]


def test_terminal_support_corpus_retains_combined_canonical_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from experiments.r4_residual_graph import tensor as tensor_module
    from experiments.r4_residual_graph import terminal_support as terminal_module

    parent = _dev_control_parent()
    history = (
        _dev_control_rows()
        .with_columns(
            pl.col("decision_time").alias("feature_data_asof"),
            pl.col("decision_time").alias("feature_available_at"),
            pl.lit(1).alias("cross_market_missing_count"),
        )
        .select(sorted(tensor_module._SUPPORT_ALLOWED_COLUMNS))
    )
    terminal_row = dict(history.to_dicts()[0])
    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    terminal_row.update(
        decision_time=timestamp,
        feature_data_asof=timestamp,
        feature_available_at=timestamp,
        target_available_at=timestamp + timedelta(minutes=15),
        block="TERMINAL_FORMER_HOLDOUT",
    )
    bindings = dict.fromkeys(
        (
            "terminal_metadata_identity",
            "terminal_support_identity",
            "parent_identity",
            "manifest_sha256",
            "child_closure_sha256",
            "graph_identity",
            "config_identity",
            "preprocessor_identity",
            "history_identity",
        ),
        "terminal-test",
    )
    bindings["manifest_sha256"] = parent.manifest_sha256
    bindings["child_closure_sha256"] = parent.child_closure_sha256
    forecasts = ((f"{terminal_row['instrument_id']}|{timestamp.isoformat()}", 0.0),)
    payload = {
        **bindings,
        "rows": [
            {key: tensor_module._canonical_row_value(value) for key, value in terminal_row.items()}
        ],
        "forecasts": forecasts,
    }
    capability = terminal_module.AuthenticatedTerminalPredictionInput._create(
        token=terminal_module._TERMINAL_PREDICTION_INPUT_SEAL,
        rows=[terminal_row],
        forecasts=forecasts,
        **bindings,
        content_identity=hashlib.sha256(tensor_module._canonical_bytes(payload)).hexdigest(),
    )
    projected = pl.DataFrame([terminal_row]).select(sorted(tensor_module._SUPPORT_ALLOWED_COLUMNS))
    assert foundation_module._project_authenticated_terminal_control_rows(
        projected, parent, capability
    ).equals(projected)
    with pytest.raises(ValueError, match="differ from authenticated"):
        foundation_module._project_authenticated_terminal_control_rows(
            projected.with_columns(pl.lit(99.0).alias("source_active")), parent, capability
        )
    with pytest.raises(TypeError, match="authenticated prediction input"):
        foundation_module._project_authenticated_terminal_control_rows(projected, parent, None)
    loads: list[tuple[bool, bool]] = []

    def load(_parent: Any, *, include_invalid: bool, include_return: bool) -> pl.DataFrame:
        assert _parent is parent
        loads.append((include_invalid, include_return))
        return history.reverse()

    monkeypatch.setattr(foundation_module, "load_parent_rows", load)
    corpus = tensor_module.authenticate_support_corpus(
        [terminal_row], parent, allow_terminal=True, terminal_prediction_input=capability
    )
    combined = [
        {key: tensor_module._canonical_row_value(value) for key, value in row.items()}
        for row in [*history.to_dicts(), terminal_row]
    ]
    expected = hashlib.sha256(tensor_module._canonical_bytes(combined)).hexdigest()
    assert corpus.authentication == (
        parent.manifest_sha256,
        parent.child_closure_sha256,
        expected,
        capability.parent_identity,
    )
    assert expected != capability.content_identity
    assert loads == [(True, False)]
    with pytest.raises(ValueError, match="content differs"):
        tensor_module.authenticate_support_corpus(
            [{**terminal_row, "source_active": 99.0}],
            parent,
            allow_terminal=True,
            terminal_prediction_input=capability,
        )


def test_development_support_comparison_excludes_boundary_parent_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded support test; full closure uses explicit prepare authority."""
    from experiments.r4_residual_graph.tensor import _SUPPORT_ALLOWED_COLUMNS, P0_FEATURE_NAMES

    start = datetime(2026, 5, 29, 13, 6, tzinfo=UTC)
    records: list[dict[str, Any]] = []
    for instrument in ALL_INSTRUMENTS:
        for offset in range(61):
            decision_time = start + timedelta(minutes=offset)
            block = "DEV_2" if offset == 60 else "TRAINING_ONLY"
            row: dict[str, Any] = {
                "instrument_id": instrument,
                "decision_time": decision_time,
                "feature_data_asof": decision_time,
                "feature_available_at": decision_time,
                "target_valid": True,
                "target_available_at": decision_time + timedelta(minutes=15),
                "block": block,
            }
            for feature in P0_FEATURE_NAMES:
                row[feature] = 1.0 if feature.endswith("_available") else 0.0
            row["source_active"] = 1.0
            row["quality_healthy"] = 1.0
            row["gap_known_by_cutoff"] = 1.0
            row["cross_market_missing_count"] = 0.0
            records.append(row)
    mature_rows = pl.DataFrame(records)
    boundary = mature_rows.head(1).with_columns(
        pl.lit("DEV_3").alias("block"),
        pl.lit(foundation_module.TERMINAL_START).alias("decision_time"),
        pl.lit(foundation_module.TERMINAL_START + timedelta(minutes=15)).alias(
            "target_available_at"
        ),
    )
    authoritative = mature_rows.vstack(boundary)
    parent = _dev_control_parent()
    monkeypatch.setattr(
        foundation_module,
        "load_parent_rows",
        lambda _capsule, **_kwargs: authoritative,
    )

    support, metadata = materialise_development_support(mature_rows, capsule=parent)

    assert support.height == len(ALL_INSTRUMENTS)
    assert support.columns == sorted(_SUPPORT_ALLOWED_COLUMNS)
    assert set(support["instrument_id"].to_list()) == set(ALL_INSTRUMENTS)
    assert metadata["support"] == len(ALL_INSTRUMENTS)
    assert support["decision_time"].max() == start + timedelta(minutes=60)


def test_development_support_retains_mature_keys_with_masked_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mature authenticated keys survive missing or inactive causal inputs."""
    from experiments.r4_residual_graph.tensor import P0_FEATURE_NAMES

    decision_times = (
        datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
        datetime(2026, 6, 12, 14, 6, tzinfo=UTC),
    )
    records: list[dict[str, object]] = []
    for instrument in ALL_INSTRUMENTS:
        for index, decision_time in enumerate(decision_times):
            row: dict[str, object] = {
                "instrument_id": instrument,
                "decision_time": decision_time,
                "target_available_at": decision_time + timedelta(minutes=15),
                "target_valid": True,
                "block": "DEV_2" if index == 0 else "DEV_3",
                "feature_data_asof": decision_time,
                "feature_available_at": decision_time,
                "source_active": 1.0,
            }
            row.update({feature: 0.0 for feature in P0_FEATURE_NAMES})
            records.append(row)
    # This mature key has no usable history at all; tensor construction masks it.
    masked = records[0]
    masked["feature_data_asof"] = None
    masked["feature_available_at"] = None
    masked["source_active"] = 0.0
    masked.update({feature: None for feature in P0_FEATURE_NAMES if feature != "source_active"})
    rows = pl.DataFrame(records)
    parent = _dev_control_parent()
    monkeypatch.setattr(foundation_module, "load_parent_rows", lambda _capsule, **_kwargs: rows)

    support, metadata = materialise_development_support(rows, capsule=parent)

    assert support.height == len(ALL_INSTRUMENTS) * len(decision_times)
    assert metadata["support"] == support.height
    assert set(support["instrument_id"].unique().to_list()) == set(ALL_INSTRUMENTS)
    retained = support.filter(
        (pl.col("instrument_id") == ALL_INSTRUMENTS[0])
        & (pl.col("decision_time") == decision_times[0])
    )
    assert retained.height == 1
    assert retained["source_active"].item() == 0.0
    assert retained["return_60s"].item() is None


def _reconstruct_with_pooled_metrics(
    monkeypatch: pytest.MonkeyPatch, pooled_metrics: dict[str, float | int]
) -> dict[str, Any]:
    def evaluate(
        rows: pl.DataFrame,
        config: dict[str, object],
        specs: dict[str, dict[str, str]],
        block_names: tuple[str, ...],
    ) -> tuple[dict[str, float | int], dict[str, object]]:
        del rows, config, specs, block_names
        return pooled_metrics, {}

    monkeypatch.setattr(foundation_module, "_evaluate_configuration", evaluate)
    monkeypatch.setattr(foundation_module, "authenticate_core_six_baseline", lambda _capsule: {})
    return foundation_module.reconstruct_linear_controls(pl.DataFrame({"block": ["DEV_1"]}))


def test_expected_skill_accepts_reproduced_direct_delta_variation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pooled_metrics: dict[str, float | int] = {
        "support": 771_140,
        "zero_return_instrument_balanced_mse": 2.1863635979286415e-6,
        "model_instrument_balanced_mse": 2.186181309098118e-6,
        "direct_delta_mse_versus_zero": -1.8228883052356744e-10,
        "skill_versus_zero": 8.337535014590891e-5,
    }

    result = _reconstruct_with_pooled_metrics(monkeypatch, pooled_metrics)

    assert result["configurations"]["FULLY_POOLED_LOCAL_RIDGE"] == {
        "configuration_id": FULLY_POOLED_CONFIG_ID,
        **pooled_metrics,
    }


@pytest.mark.parametrize(
    "direct_delta, skill, expected_error",
    [
        (-1.8219030302837555e-10, 8.33302837105683e-5, "development delta differs"),
        (-1.8229040302837556e-10, 8.33762693789154e-5, "development skill differs"),
    ],
)
def test_expected_skill_rejects_substantive_divergence(
    monkeypatch: pytest.MonkeyPatch,
    direct_delta: float,
    skill: float,
    expected_error: str,
) -> None:
    zero_mse = 2.186363710428282e-6
    pooled_metrics: dict[str, float | int] = {
        "support": 771_140,
        "zero_return_instrument_balanced_mse": zero_mse,
        "model_instrument_balanced_mse": zero_mse + direct_delta,
        "direct_delta_mse_versus_zero": direct_delta,
        "skill_versus_zero": skill,
    }

    with pytest.raises(ValueError, match=expected_error):
        _reconstruct_with_pooled_metrics(monkeypatch, pooled_metrics)


def test_full_lab_terminal_regression_uses_full_population_and_preterminal_training(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    cutoff = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    targets = pl.DataFrame(
        {
            "instrument_id": list(ALL_INSTRUMENTS),
            "decision_time": [cutoff] * 20,
            "target_return": [1.0] * 20,
        }
    )
    features = targets.select("instrument_id", "decision_time").with_columns(
        pl.lit(999.0).alias("cross_market_available_count")
    )
    context = targets.select("instrument_id", "decision_time").with_columns(
        pl.lit(1.0).alias("current_available")
    )
    training = pl.DataFrame(
        {
            "decision_time": [cutoff - timedelta(hours=1), cutoff],
            "target_available_at": [cutoff - timedelta(minutes=30), cutoff + timedelta(minutes=15)],
        }
    )
    parent = cast(
        foundation_module.Lab0Capsule,
        SimpleNamespace(manifest={}, manifest_sha256="manifest", child_closure_sha256="closure"),
    )
    monkeypatch.setattr(foundation_module, "_feature_frame", lambda _: features)
    monkeypatch.setattr(foundation_module, "_context_frame", lambda _: context)
    monkeypatch.setattr(
        foundation_module,
        "_lab_s_block_specs",
        lambda _: {"TERMINAL_FORMER_HOLDOUT": {"start": cutoff.isoformat()}},
    )
    monkeypatch.setitem(
        foundation_module.EXPECTED_LINEAR,
        "terminal",
        {
            "support": 20,
            "fully_pooled_mse": 0.25,
            "direct_delta_mse": -0.75,
            "skill": 0.75,
        },
    )
    observed: list[int] = []

    def predict(fit_rows: Any, validation: Any, configuration: Any, fit_time: Any) -> Any:
        assert fit_rows.height == 1
        assert fit_time == cutoff
        assert configuration_id(configuration) == FULLY_POOLED_CONFIG_ID
        assert validation["cross_market_available_count"].to_list() == [19.0] * 20
        observed.append(validation.height)
        return np.full(validation.height, 0.5), {}

    monkeypatch.setattr(foundation_module, "_raw_prediction", predict)
    result = foundation_module._reconstruct_full_lab_terminal_regression(parent, training, targets)
    assert observed == [20]
    assert result["support"] == 20
    assert result["population"] == "FULL_LAB_VALID_TERMINAL_INSTRUMENT_ROWS"
    assert result["direct_delta_mse"] == -0.75
    with pytest.raises(ValueError, match="instrument-row population"):
        foundation_module._reconstruct_full_lab_terminal_regression(
            parent, training, targets.head(19)
        )
    monkeypatch.setitem(foundation_module.EXPECTED_LINEAR["terminal"], "skill", 0.5)
    with pytest.raises(ValueError, match="anchors differ"):
        foundation_module._reconstruct_full_lab_terminal_regression(parent, training, targets)
