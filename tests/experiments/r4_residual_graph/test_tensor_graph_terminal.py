from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import numpy as np
import polars as pl
import pytest

from experiments.r4_residual_graph import (
    ALL_INSTRUMENTS,
    P0_FEATURE_NAMES,
    FrozenRuntimeConfig,
    Lab0Capsule,
    TensorContract,
    TerminalSupportConfig,
    TrainingTensorPartition,
    authenticate_terminal_metadata,
    build_fixed_economic_graph,
    build_masked_sequence,
    build_terminal_support,
    candidate_independent_support,
    candidate_independent_support_smoke,
    deterministic_fixed_point_free_permutation,
    fit_training_preprocessor,
    fit_training_preprocessor_smoke,
    project_authenticated_support_rows,
    shuffle_economic_graph,
)


def _smoke_config() -> TerminalSupportConfig:
    return TerminalSupportConfig.for_smoke(
        FrozenRuntimeConfig(), build_fixed_economic_graph(), forecast_identity="forecast"
    )


def _rows(cutoff: datetime, *, lookback: int = 2) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for offset in range(lookback + 1):
        timestamp = cutoff - timedelta(minutes=lookback - offset)
        for instrument_index, instrument in enumerate(ALL_INSTRUMENTS):
            row: dict[str, object] = {
                "instrument_id": instrument,
                "decision_time": timestamp,
                "feature_data_asof": timestamp,
                "feature_available_at": timestamp,
                "source_active": 1.0,
                "block": "DEV_2",
                "target_valid": 1,
                "target_available_at": timestamp + timedelta(minutes=1),
            }
            row.update(
                {
                    feature: float(instrument_index + offset + feature_index)
                    for feature_index, feature in enumerate(P0_FEATURE_NAMES)
                }
            )
            rows.append(row)
    return rows


def test_b_tensor_canonical_grid_masks_and_candidate_independent_support() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=2)
    rows = _rows(cutoff)
    sequence = build_masked_sequence(list(reversed(rows)), cutoff, contract=contract)
    assert sequence.shape == (3, 20, 26)
    assert tuple(np.flatnonzero(sequence.node_mask[0])) == tuple(range(20))
    assert sequence.value_mask.all()
    support_a = candidate_independent_support_smoke(rows, [cutoff], contract=contract)
    support_b = candidate_independent_support_smoke(rows, [cutoff], contract=contract)
    assert support_a == support_b


def test_b_primary_support_requires_authenticated_parent() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    with pytest.raises(TypeError, match="authenticated parent"):
        candidate_independent_support(_rows(cutoff), [cutoff])


def test_b_terminal_capsule_binds_history_identities() -> None:
    from dataclasses import replace

    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    capsule = build_terminal_support(
        _terminal_rows(timestamp),
        config=_smoke_config(),
        development_register_closed=True,
    )
    for field in ("history_content_identity", "first_terminal_lookback_identity"):
        tampered = replace(capsule, **{field: "f" * 64})
        with pytest.raises(ValueError, match="artifact identity mismatch"):
            tampered._validate()


def test_b_smoke_support_cannot_be_relabelled_primary() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    support = candidate_independent_support_smoke(
        _rows(cutoff), [cutoff], contract=TensorContract(lookback_minutes=2)
    )
    object.__setattr__(support, "mode", "PRIMARY")
    with pytest.raises(TypeError, match="provenance capability"):
        support._validate()


@pytest.mark.parametrize("column", ["target_return", "local_residual", "unknown_column"])
def test_b_support_rejects_outcome_or_unknown_columns(column: str) -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    rows = _rows(cutoff)
    rows[0][column] = 0.0
    with pytest.raises(ValueError, match="non-canonical or outcome-bearing"):
        candidate_independent_support_smoke(
            rows, [cutoff], contract=TensorContract(lookback_minutes=2)
        )


def test_b_authenticated_primary_support_uses_40_hex_parent_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    parent_identity = "284a11cd63cca67445007ffa42321a393536cea7"
    monkeypatch.setattr(
        "experiments.r4_residual_graph.tensor._assert_authenticated_parent_rows",
        lambda rows, parent: (
            "a" * 64,
            "b" * 64,
            "c" * 64,
            parent_identity,
        ),
    )
    support = candidate_independent_support(
        _rows(cutoff),
        [cutoff],
        contract=TensorContract(lookback_minutes=2),
        authenticated_parent=object(),
    )
    assert support.mode == "PRIMARY"
    assert support.parent_identity == parent_identity
    support._validate()


@pytest.mark.parametrize("identity", ["a" * 64, "g" * 40, "0" * 40])
def test_b_support_rejects_wrong_parent_identity_kind(identity: str) -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    support = candidate_independent_support_smoke(
        _rows(cutoff), [cutoff], contract=TensorContract(lookback_minutes=2)
    )
    object.__setattr__(support, "parent_identity", identity)
    with pytest.raises(ValueError, match="parent identity"):
        support._validate()


def test_b_authenticated_projection_strips_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    rows = _rows(cutoff)
    authoritative = pl.DataFrame(rows).with_columns(
        pl.col("decision_time").alias("latest_feature_bar_end")
    )
    with_outcome = authoritative.with_columns(pl.lit(0.5).alias("target_return"))
    monkeypatch.setattr(
        "experiments.r4_residual_graph.foundation.load_parent_rows",
        lambda capsule: authoritative,
    )
    projected = project_authenticated_support_rows(
        with_outcome, capsule=cast(Lab0Capsule, object())
    )
    assert "target_return" not in projected.columns
    assert "latest_feature_bar_end" not in projected.columns
    assert set(projected.columns) == {
        "instrument_id",
        "decision_time",
        "feature_data_asof",
        "feature_available_at",
        "source_active",
        *P0_FEATURE_NAMES,
        "block",
        "target_valid",
        "target_available_at",
    }


def test_b_causal_future_and_missing_values_are_masked_without_fill() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=2)
    rows = _rows(cutoff)
    future = dict(rows[0])
    future["decision_time"] = cutoff + timedelta(minutes=1)
    future["return_60s"] = 9999.0
    rows.append(future)
    rows = [
        row
        for row in rows
        if row["instrument_id"] != ALL_INSTRUMENTS[0] or row["decision_time"] != cutoff
    ]
    masked = build_masked_sequence(rows, cutoff, contract=contract)
    assert not masked.node_mask[-1, 0]
    assert np.isnan(masked.values[-1, 0, 0])
    unavailable = dict(_rows(cutoff)[-1])
    unavailable["feature_data_asof"] = cutoff + timedelta(minutes=1)
    rows = [
        row
        for row in rows
        if row["instrument_id"] != ALL_INSTRUMENTS[-1] or row["decision_time"] != cutoff
    ]
    masked = build_masked_sequence([*rows, unavailable], cutoff, contract=contract)
    assert not masked.node_mask[-1, -1]


def test_b_training_preprocessor_uses_masks_and_training_contract() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=2)
    tensor = build_masked_sequence(_rows(cutoff), cutoff, contract=contract)
    fitted = fit_training_preprocessor_smoke(tensor, training_cutoff=cutoff, contract=contract)
    transformed = fitted.transform(tensor)
    assert transformed.contract_identity == contract.identity
    assert fitted.reduction_algorithm == "R4.D.CHUNKED_FLOAT64_V1"
    assert np.isclose(np.nanmean(transformed.values[..., 0]), 0.0)


def test_b_bulk_training_preprocessors_use_one_largest_partition_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.r4_residual_graph import tensor as tensor_module

    start = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    tensors = tuple(
        build_masked_sequence(
            _rows(start + timedelta(minutes=offset), lookback=60), start + timedelta(minutes=offset)
        )
        for offset in range(3)
    )
    keys = tuple(tensor.decision_time.isoformat() for tensor in tensors)
    identities = tuple(tensor_module._masked_tensor_identity(tensor) for tensor in tensors)

    class CountingSequence(list[Any]):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    largest_tensors = CountingSequence(tensors)

    def partition(name: str, size: int, sequence: Any) -> TrainingTensorPartition:
        selected_keys = keys[:size]
        selected_identities = identities[:size]
        cutoff = tensors[size - 1].decision_time
        return TrainingTensorPartition(
            sequence,
            selected_keys,
            "a" * 64,
            hashlib.sha256(name.encode()).hexdigest(),
            cutoff,
            selected_identities,
            _seal=tensor_module._TRAINING_PARTITION_SEAL,
        )

    partitions = {
        "GLOBAL": partition("GLOBAL", 3, largest_tensors),
        "DEV_2": partition("DEV_2", 1, (tensors[0],)),
        "DEV_3": partition("DEV_3", 2, tensors[:2]),
    }

    # Standalone construction still validates its payload stream independently.
    assert largest_tensors.iterations == 1

    def fail_if_called(_tensor: Any) -> bool:
        raise AssertionError("bulk fit must not remap tensor payloads for identity")

    monkeypatch.setattr(
        tensor_module,
        "_masked_tensor_identity",
        fail_if_called,
    )
    fitted = tensor_module.fit_training_preprocessors(partitions)

    assert largest_tensors.iterations == 2
    assert tuple(fitted) == ("GLOBAL", "DEV_2", "DEV_3")
    for name, size in (("GLOBAL", 3), ("DEV_2", 1), ("DEV_3", 2)):
        observed = np.concatenate(
            [tensor.values[..., 0][tensor.value_mask[..., 0]] for tensor in tensors[:size]]
        )
        assert fitted[name].training_partition_identity == partitions[name].seal
        assert fitted[name].training_cutoff == tensors[size - 1].decision_time
        assert fitted[name].means[0] == np.mean(observed)
        assert fitted[name].scales[0] == np.std(observed)


def test_b_training_partition_capability_is_retained_and_fail_closed() -> None:
    from experiments.r4_residual_graph import tensor as tensor_module

    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    tensor = build_masked_sequence(_rows(cutoff, lookback=60), cutoff)
    constructor = cast(Any, TrainingTensorPartition)
    arguments = (
        (tensor,),
        (cutoff.isoformat(),),
        "a" * 64,
        "b" * 64,
        cutoff,
    )

    with pytest.raises(TypeError):
        constructor(*arguments)
    for invalid_seal in (object(), tensor_module._TRAINING_PARTITION_BUILDER_CAPABILITY):
        with pytest.raises(TypeError, match="must be authenticated"):
            constructor(*arguments, _seal=invalid_seal)

    partition = constructor(*arguments, _seal=tensor_module._TRAINING_PARTITION_SEAL)
    assert partition._seal is tensor_module._TRAINING_PARTITION_SEAL
    tensor_module.fit_training_preprocessors({"GLOBAL": partition})

    object.__delattr__(partition, "_seal")
    with pytest.raises(TypeError, match="seal is private"):
        tensor_module.fit_training_preprocessors({"GLOBAL": partition})
    object.__setattr__(partition, "_seal", object())
    with pytest.raises(TypeError, match="seal is private"):
        tensor_module.fit_training_preprocessors({"GLOBAL": partition})


def test_b_preprocessor_reduction_algorithm_is_authenticated() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=2)
    tensor = build_masked_sequence(_rows(cutoff), cutoff, contract=contract)
    fitted = fit_training_preprocessor_smoke(tensor, training_cutoff=cutoff, contract=contract)
    object.__setattr__(fitted, "reduction_algorithm", "legacy")
    with pytest.raises(ValueError, match="reduction algorithm"):
        fitted._validate()


def test_b_smoke_preprocessor_cannot_be_relabelled_primary() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=2)
    tensor = build_masked_sequence(_rows(cutoff), cutoff, contract=contract)
    fitted = fit_training_preprocessor_smoke(tensor, training_cutoff=cutoff, contract=contract)
    object.__setattr__(fitted, "mode", "PRIMARY")
    with pytest.raises(TypeError, match="provenance capability"):
        fitted._validate()


def test_b_fixed_and_shuffled_graph_contracts() -> None:
    graph = build_fixed_economic_graph()
    assert graph.node_order == tuple(ALL_INSTRUMENTS)
    assert np.allclose(graph.adjacency, graph.adjacency.T)
    assert np.all(np.diag(graph.adjacency) == 1.0)
    assert np.allclose(graph.normalized_adjacency, graph.normalized_adjacency.T)
    permutation = deterministic_fixed_point_free_permutation()
    assert np.all(np.diag(permutation) == 0.0)
    shuffled = shuffle_economic_graph(graph)
    assert np.allclose(shuffled.adjacency, permutation @ graph.adjacency @ permutation.T)
    assert shuffled.off_diagonal_difference_fraction >= 0.50
    assert shuffled.neighbourhood_difference_fraction >= 0.50
    assert np.allclose(
        np.sort(graph.adjacency.sum(axis=1)),
        np.sort(shuffled.adjacency.sum(axis=1)),
    )


def _terminal_rows(
    timestamp: datetime, config: TerminalSupportConfig | None = None
) -> list[dict[str, object]]:
    config = config or _smoke_config()
    return [
        {
            "instrument_id": instrument,
            "decision_time": timestamp,
            "block": "TERMINAL_FORMER_HOLDOUT",
            "target_valid": 1,
            "target_available_at": timestamp + timedelta(minutes=15),
            "dependency_start": timestamp - timedelta(minutes=60),
            "dependency_end": timestamp,
            "feature_data_asof": timestamp,
            "feature_available_at": timestamp,
            "latest_feature_bar_end": timestamp,
            "feature_schema_identity": "features",
            "feature_identity": "feature-part",
            "feature_semantic_sha256": (
                "61b4955c8b1536e84c80a574a5a10924d6fd235a1e6b70da3b76de43c9b45282"
            ),
            "feature_mask": (True,) * len(P0_FEATURE_NAMES),
            "availability_mask": (True,) * len(P0_FEATURE_NAMES),
            "node_mask": True,
            "context_schema_identity": "context-schema",
            "context_identity": "context",
            "history_start": timestamp - timedelta(minutes=60),
            "history_end": timestamp,
            "history_identity": "history",
            "forecast_exists": True,
            "forecast_identity": "forecast",
            "graph_identity": config.graph_identity,
            "config_identity": config.config_identity,
            "manifest_sha256": config.manifest_sha256,
            "child_closure_sha256": config.child_closure_sha256,
            "parent_identity": config.parent_identity,
            "evidence_label": config.evidence_label,
            "source_class": config.source_class,
            "source_active": True,
        }
        for instrument in ALL_INSTRUMENTS
    ]


def test_b_terminal_config_requires_authenticated_factory() -> None:
    with pytest.raises(TypeError, match="authenticated factory"):
        TerminalSupportConfig("manifest", "closure", "graph", "config")


def test_b_terminal_boundary_is_allowlisted_and_create_only(tmp_path) -> None:
    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    rows = _terminal_rows(timestamp)
    config = _smoke_config()
    output = tmp_path / "terminal-support.json"
    capsule = build_terminal_support(
        rows,
        config=config,
        output_path=output,
        development_register_closed=True,
    )
    assert capsule.key_count == 20
    assert len(capsule.key_input_identities) == capsule.key_count
    assert output.exists()
    with pytest.raises(FileExistsError):
        build_terminal_support(
            rows,
            config=config,
            output_path=output,
            development_register_closed=True,
        )
    with pytest.raises(ValueError, match="outcome-bearing"):
        build_terminal_support(
            [{**rows[0], "target_return": 0.1}],
            config=config,
            development_register_closed=True,
        )
    with pytest.raises(RuntimeError, match="closed development register"):
        build_terminal_support(rows, config=config)
    with pytest.raises(RuntimeError, match="prohibited"):
        build_terminal_support(
            rows, config=config, development_register_closed=True, actual_terminal=True
        )


def test_b_terminal_capsule_binds_source_metadata(tmp_path) -> None:
    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    capsule = build_terminal_support(
        _terminal_rows(timestamp),
        config=_smoke_config(),
        output_path=tmp_path / "terminal-support.json",
        development_register_closed=True,
    )
    object.__setattr__(capsule, "source_class", "tampered")
    with pytest.raises(ValueError, match=r"source identity|artifact identity mismatch"):
        capsule._validate()


class _SpyRow(dict[str, object]):
    def __getitem__(self, key: str) -> object:
        if key == "target_return":
            raise AssertionError("forbidden outcome value was loaded")
        return super().__getitem__(key)


def test_b_terminal_rejects_forbidden_schema_before_loading_values(tmp_path) -> None:
    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    row = _SpyRow(_terminal_rows(timestamp)[0])
    row["target_return"] = 0.2
    with pytest.raises(ValueError, match="outcome-bearing"):
        build_terminal_support(
            [row],
            config=_smoke_config(),
            output_path=tmp_path / "forbidden.json",
            development_register_closed=True,
        )


def test_b_tensor_inclusive_lookback_and_malformed_value_rejected() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=2)
    sequence = build_masked_sequence(_rows(cutoff), cutoff, contract=contract)
    assert sequence.values.shape[0] == 3
    malformed = _rows(cutoff)
    malformed[0]["return_60s"] = "not-a-number"
    with pytest.raises(ValueError, match="not numeric"):
        build_masked_sequence(malformed, cutoff, contract=contract)


def test_b_support_sorts_and_rejects_duplicates_and_nonmature_rows() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=0)
    rows = _rows(cutoff, lookback=0) + _rows(cutoff - timedelta(minutes=1), lookback=0)
    support = candidate_independent_support_smoke(
        rows, [cutoff, cutoff - timedelta(minutes=1)], contract=contract
    )
    assert support.keys == (
        "2026-05-30T15:59:00+00:00",
        "2026-05-30T16:00:00+00:00",
    )
    with pytest.raises(ValueError, match="duplicate decision times"):
        candidate_independent_support_smoke(rows, [cutoff, cutoff], contract=contract)
    immature = [dict(row) for row in rows]
    for row in immature:
        if row["decision_time"] == cutoff:
            row["target_available_at"] = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    with pytest.raises(ValueError, match="support is empty"):
        candidate_independent_support_smoke(immature, [cutoff], contract=contract)
    predecision = [dict(row) for row in rows]
    for row in predecision:
        if row["decision_time"] == cutoff:
            row["target_available_at"] = cutoff - timedelta(minutes=1)
    with pytest.raises(ValueError, match="support is empty"):
        candidate_independent_support_smoke(predecision, [cutoff], contract=contract)


def test_b_preprocessor_imputation_and_binary_indicator_contract() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=2)
    tensor = build_masked_sequence(_rows(cutoff), cutoff, contract=contract)
    fitted = fit_training_preprocessor_smoke(tensor, training_cutoff=cutoff, contract=contract)
    assert fitted.scales[1] == 1.0
    assert fitted.means[1] == 0.0


def test_b_terminal_eligibility_rejects_block_time_and_identity_mismatch() -> None:
    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    rows = _terminal_rows(timestamp)
    config = _smoke_config()
    invalid_block = [dict(row) for row in rows]
    invalid_block[0]["block"] = "DEV_1"
    with pytest.raises(ValueError, match="outside"):
        build_terminal_support(invalid_block, config=config, development_register_closed=True)
    invalid_feature = [dict(row) for row in rows]
    invalid_feature[0]["feature_data_asof"] = timestamp + timedelta(minutes=1)
    with pytest.raises(ValueError, match="after decision"):
        build_terminal_support(invalid_feature, config=config, development_register_closed=True)
    invalid_dependency = [dict(row) for row in rows]
    invalid_dependency[0]["dependency_end"] = timestamp + timedelta(minutes=1)
    with pytest.raises(ValueError, match="after decision"):
        build_terminal_support(invalid_dependency, config=config, development_register_closed=True)
    invalid_maturity = [dict(row) for row in rows]
    invalid_maturity[0]["target_available_at"] = timestamp
    with pytest.raises(ValueError, match="follow decision"):
        build_terminal_support(invalid_maturity, config=config, development_register_closed=True)
    invalid_graph = [dict(row) for row in rows]
    invalid_graph[0]["graph_identity"] = "other"
    with pytest.raises(ValueError, match="graph identity"):
        build_terminal_support(invalid_graph, config=config, development_register_closed=True)

    invalid_mask = [dict(row) for row in rows]
    invalid_mask[0]["feature_mask"] = [True, False]
    with pytest.raises(ValueError, match="subset"):
        build_terminal_support(invalid_mask, config=config, development_register_closed=True)
    empty_mask = [dict(row) for row in rows]
    empty_mask[0]["feature_mask"] = np.array([], dtype=bool)
    with pytest.raises(ValueError, match="Boolean masks"):
        build_terminal_support(empty_mask, config=config, development_register_closed=True)


def test_b_tensor_contract_and_preprocessor_fail_closed() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    contract = TensorContract(lookback_minutes=0)
    rows = _rows(cutoff, lookback=0)
    rows[0].pop("feature_available_at")
    masked = build_masked_sequence(rows, cutoff, contract=contract)
    assert not masked.node_mask[-1, 0]
    with pytest.raises(ValueError, match="contract identity"):
        type(masked)(
            values=np.zeros((1, 20, 26)),
            value_mask=np.zeros((1, 20, 26), dtype=bool),
            availability_mask=np.zeros((1, 20, 26), dtype=bool),
            node_mask=np.zeros((1, 20), dtype=bool),
            decision_time=cutoff,
            contract_identity="a" * 64,
            contract=TensorContract(lookback_minutes=0),
        )
    with pytest.raises(ValueError, match="semantic"):
        TensorContract(feature_semantic_sha256="b" * 64)
    tensor = build_masked_sequence(
        _rows(cutoff), cutoff, contract=TensorContract(lookback_minutes=2)
    )
    with pytest.raises(TypeError, match="authenticated training partition"):
        fit_training_preprocessor(cast(TrainingTensorPartition, tensor))
    raw = np.zeros((1, 20, 26), dtype=float)
    raw[0, 0, 0] = np.nan
    with pytest.raises(TypeError, match="authenticated training partition"):
        fit_training_preprocessor(cast(TrainingTensorPartition, raw))


def test_b_terminal_schema_prepass_rejects_forbidden_column_in_later_row(tmp_path) -> None:
    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    rows = _terminal_rows(timestamp)
    forbidden = _SpyRow(rows[1])
    forbidden["target_return"] = 0.2
    rows[1] = forbidden
    with pytest.raises(ValueError, match="outcome-bearing"):
        build_terminal_support(
            rows,
            config=_smoke_config(),
            output_path=tmp_path / "forbidden-later.json",
            development_register_closed=True,
        )


def test_b_tensor_rejects_nonfinite_available_values() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    for invalid in (np.nan, np.inf, -np.inf):
        rows = _rows(cutoff, lookback=0)
        rows[0]["return_60s"] = invalid
        with pytest.raises(ValueError, match="must be finite"):
            build_masked_sequence(rows, cutoff, contract=TensorContract(lookback_minutes=0))


def test_b_terminal_allow_flag_without_sealed_metadata_is_rejected() -> None:
    from experiments.r4_residual_graph import foundation as foundation_module

    cutoff = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    rows = _rows(cutoff, lookback=0)
    for row in rows:
        row["block"] = "TERMINAL_FORMER_HOLDOUT"
        row["target_available_at"] = cutoff + timedelta(minutes=15)
    parent = object.__new__(Lab0Capsule)
    object.__setattr__(parent, "_authenticated_parent", foundation_module._LAB0_SEAL)
    object.__setattr__(parent, "manifest_sha256", "a" * 64)
    object.__setattr__(parent, "child_identities", ())
    with pytest.raises(TypeError, match="authenticated terminal metadata"):
        candidate_independent_support(
            rows,
            [cutoff],
            authenticated_parent=parent,
            _eligible_blocks=frozenset({"TERMINAL_FORMER_HOLDOUT"}),
            _allow_terminal=True,
        )


def test_b_authenticated_terminal_metadata_is_outcome_blind() -> None:
    from experiments.r4_residual_graph import terminal_support as terminal_module
    from experiments.r4_residual_graph.tensor import _canonical_bytes

    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    rows = _terminal_rows(timestamp)
    canonical_rows = [
        {column: terminal_module._canonical_row_value(value) for column, value in row.items()}
        for row in rows
    ]
    content_identity = hashlib.sha256(_canonical_bytes(canonical_rows)).hexdigest()
    metadata = terminal_module.AuthenticatedTerminalMetadata._create(
        token=terminal_module._TERMINAL_METADATA_SEAL,
        rows=rows,
        content_identity=content_identity,
        row_count=len(rows),
        parent_identity="p" * 64,
        manifest_sha256="m" * 64,
        child_closure_sha256="c" * 64,
        graph_identity="g" * 64,
        config_identity="i" * 64,
        forecast_configuration_id="LOCAL_RIDGE",
        forecast_fold="TERMINAL_FORMER_HOLDOUT",
    )
    assert "local_forecast" not in metadata.to_rows()[0]


def test_b_authenticated_terminal_metadata_rejects_altered_projection() -> None:
    from experiments.r4_residual_graph import terminal_support as terminal_module
    from experiments.r4_residual_graph.tensor import _canonical_bytes

    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    rows = _terminal_rows(timestamp)
    canonical_rows = [
        {column: terminal_module._canonical_row_value(value) for column, value in row.items()}
        for row in rows
    ]
    metadata = terminal_module.AuthenticatedTerminalMetadata._create(
        token=terminal_module._TERMINAL_METADATA_SEAL,
        rows=rows,
        content_identity=hashlib.sha256(_canonical_bytes(canonical_rows)).hexdigest(),
        row_count=len(rows),
        parent_identity="p" * 64,
        manifest_sha256="m" * 64,
        child_closure_sha256="c" * 64,
        graph_identity="g" * 64,
        config_identity="i" * 64,
        forecast_configuration_id="LOCAL_RIDGE",
        forecast_fold="TERMINAL_FORMER_HOLDOUT",
    )
    altered = [dict(row) for row in rows]
    altered[0]["forecast_identity"] = "tampered"
    with pytest.raises(ValueError, match="differ from authenticated terminal metadata"):
        build_terminal_support(
            altered,
            config=_smoke_config(),
            development_register_closed=True,
            authenticated_metadata=metadata,
        )


def test_b_terminal_metadata_rejects_development_row_injection() -> None:
    altered_development_rows = pl.DataFrame(
        {
            "instrument_id": ["asset:synthetic"],
            "decision_time": [datetime(2026, 5, 30, 16, 0, tzinfo=UTC)],
            "target_return": [999.0],
        }
    )
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        cast(Any, authenticate_terminal_metadata)(development_rows=altered_development_rows)


def _forecast_payload() -> tuple[dict[str, Any], Any]:
    keys = ["asset:a|2026-06-26T14:07:00+00:00", "asset:b|2026-06-26T14:07:00+00:00"]
    payload: dict[str, Any] = {
        "prediction_input_identity": "p" * 64,
        "target_keys": keys,
        "local_ridge_forecast": [1.0, 2.0],
        "fully_pooled_local_ridge_forecast": [0.8, 1.7],
        "linear_control_identity": "c" * 64,
        "residual_prediction": [0.5, -1.0],
        "total_forecast": [1.5, 1.0],
        "prediction": [0.5, -1.0],
    }

    class PredictionInput:
        forecasts = tuple(zip(keys, (1.0, 2.0), strict=True))

        def forecast_for(self, key: str) -> float:
            return dict(self.forecasts)[key]

    prediction_input = PredictionInput()
    return payload, prediction_input


def test_b_terminal_forecast_payload_validates_canonical_total() -> None:
    from experiments.r4_residual_graph import execution as execution_module

    payload, prediction_input = _forecast_payload()
    payload["forecast_identity"] = execution_module._terminal_prediction_identity(payload)
    assert execution_module._validate_terminal_forecast_payload(payload, prediction_input)
    assert execution_module._terminal_prediction_identity(payload)


def test_b_terminal_forecast_payload_float32_round_trip() -> None:
    import torch

    from experiments.r4_residual_graph import execution as execution_module

    payload, prediction_input = _forecast_payload()
    payload["local_ridge_forecast"] = [0.1, 0.2]
    prediction_input.forecasts = tuple(zip(payload["target_keys"], (0.1, 0.2), strict=True))
    payload["residual_prediction"] = [0.2, -0.1]
    payload["prediction"] = [0.2, -0.1]
    payload["total_forecast"] = (
        torch.tensor(payload["local_ridge_forecast"], dtype=torch.float32)
        + torch.tensor(payload["residual_prediction"], dtype=torch.float32)
    ).tolist()
    payload["forecast_identity"] = execution_module._terminal_prediction_identity(payload)
    assert execution_module._validate_terminal_forecast_payload(payload, prediction_input)


def test_b_terminal_forecast_payload_rejects_arithmetic_tamper() -> None:
    from experiments.r4_residual_graph import execution as execution_module

    payload, prediction_input = _forecast_payload()
    payload["total_forecast"] = [1.6, 1.0]
    with pytest.raises(ValueError, match="total forecast"):
        execution_module._validate_terminal_forecast_payload(payload, prediction_input)


def test_b_terminal_forecast_payload_rejects_order_tamper() -> None:
    from experiments.r4_residual_graph import execution as execution_module

    payload, prediction_input = _forecast_payload()
    payload["target_keys"] = list(reversed(payload["target_keys"]))
    with pytest.raises(ValueError, match="target-key order"):
        execution_module._validate_terminal_forecast_payload(payload, prediction_input)


def test_b_terminal_forecast_payload_rejects_authenticated_local_tamper() -> None:
    from experiments.r4_residual_graph import execution as execution_module

    payload, prediction_input = _forecast_payload()
    payload["local_ridge_forecast"] = [9.0, 2.0]
    payload["residual_prediction"] = [0.25, -1.0]
    payload["prediction"] = [0.25, -1.0]
    payload["total_forecast"] = [9.25, 1.0]
    payload["forecast_identity"] = execution_module._terminal_prediction_identity(payload)
    with pytest.raises(ValueError, match="LOCAL_RIDGE"):
        execution_module._validate_terminal_forecast_payload(payload, prediction_input)


def test_b_terminal_support_retains_incomplete_cross_section_and_counts_keys() -> None:
    first = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    rows = _terminal_rows(first) + _terminal_rows(second)
    rows = [
        row
        for row in rows
        if not (row["decision_time"] == second and row["instrument_id"] == ALL_INSTRUMENTS[-1])
    ]
    capsule = build_terminal_support(rows, config=_smoke_config(), development_register_closed=True)
    assert capsule.key_count == 39
    assert capsule.decision_time_count == 2
    assert dict(capsule.per_instrument_counts)[ALL_INSTRUMENTS[-1]] == 1
    assert dict(capsule.per_instrument_counts)[ALL_INSTRUMENTS[0]] == 2


def test_b_candidate_support_retains_individual_targets_with_masked_node() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    rows = [
        row
        for row in _rows(cutoff)
        if not (row["decision_time"] == cutoff and row["instrument_id"] == ALL_INSTRUMENTS[-1])
    ]
    support = candidate_independent_support_smoke(
        rows, [cutoff], contract=TensorContract(lookback_minutes=2)
    )
    assert support.key_count == 1
    assert len(support.target_keys) == 19
    assert ALL_INSTRUMENTS[-1] not in {key.split("|", 1)[0] for key in support.target_keys}


def test_b_linear_control_payload_rejects_per_control_tamper_and_swap() -> None:
    from experiments.r4_residual_graph import execution as execution_module

    expected = {
        "identity": "a" * 64,
        "controls": {
            "ZERO_RETURN": [0.0, 0.0],
            "LOCAL_RIDGE": [1.0, 2.0],
            "FULLY_POOLED_LOCAL_RIDGE": [1.0, 2.0],
        },
        "control_identities": {
            "ZERO_RETURN": "b" * 64,
            "LOCAL_RIDGE": "c" * 64,
            "FULLY_POOLED_LOCAL_RIDGE": "e" * 64,
        },
        "support_identity": "d" * 64,
    }
    payload = {
        "linear_control_identity": expected["identity"],
        "linear_controls": {
            "ZERO_RETURN": [0.0, 0.0],
            "LOCAL_RIDGE": [1.0, 2.0],
            "FULLY_POOLED_LOCAL_RIDGE": [1.0, 2.0],
        },
        "linear_control_identities": dict(expected["control_identities"]),
        "linear_control_support_identity": expected["support_identity"],
        "local_ridge_forecast": [1.0, 2.0],
        "fully_pooled_local_ridge_forecast": [1.0, 2.0],
    }
    execution_module._validate_linear_control_payload(payload, expected)
    payload["linear_controls"]["LOCAL_RIDGE"] = [2.0, 1.0]
    with pytest.raises(ValueError, match="values drifted"):
        execution_module._validate_linear_control_payload(payload, expected)
    payload["linear_controls"]["LOCAL_RIDGE"] = [1.0, 2.0]
    payload["linear_control_identities"]["ZERO_RETURN"] = expected["control_identities"][
        "LOCAL_RIDGE"
    ]
    with pytest.raises(ValueError, match="identities drifted"):
        execution_module._validate_linear_control_payload(payload, expected)


def test_b_linear_control_payload_rejects_sealed_period_identity_mismatch() -> None:
    from experiments.r4_residual_graph import execution as execution_module

    expected = {
        "identity": "a" * 64,
        "controls": {
            "ZERO_RETURN": [0.0],
            "LOCAL_RIDGE": [1.0],
            "FULLY_POOLED_LOCAL_RIDGE": [1.0],
        },
        "control_identities": {
            "ZERO_RETURN": "b" * 64,
            "LOCAL_RIDGE": "c" * 64,
            "FULLY_POOLED_LOCAL_RIDGE": "e" * 64,
        },
        "support_identity": "d" * 64,
    }
    payload = {
        "linear_control_identity": expected["identity"],
        "linear_controls": dict(expected["controls"]),
        "linear_control_identities": dict(expected["control_identities"]),
        "linear_control_support_identity": expected["support_identity"],
        "local_ridge_forecast": [1.0],
        "fully_pooled_local_ridge_forecast": [1.0],
    }
    execution_module._validate_linear_control_payload(payload, expected)
    expected["identity"] = "e" * 64
    with pytest.raises(ValueError, match="period identity drifted"):
        execution_module._validate_linear_control_payload(payload, expected)


def test_b_large_support_target_validation_preserves_canonical_membership() -> None:
    """Large target metadata validates canonically without quadratic tuple membership."""
    from experiments.r4_residual_graph import tensor as tensor_module
    from experiments.r4_residual_graph.runtime import PARENT_IDENTITY

    start = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    keys = tuple((start + timedelta(minutes=offset)).isoformat() for offset in range(5_000))
    target_keys = tuple(
        f"{instrument}|{timestamp}" for timestamp in keys for instrument in ALL_INSTRUMENTS
    )
    record = tensor_module.SupportRecord._create(
        token=tensor_module._SUPPORT_SEAL,
        keys=keys,
        ordered_key_sha256=hashlib.sha256(tensor_module._canonical_bytes(keys)).hexdigest(),
        key_count=len(keys),
        contract_identity=TensorContract().identity,
        lookback_minutes=60,
        node_order=tuple(ALL_INSTRUMENTS),
        feature_names=P0_FEATURE_NAMES,
        target_keys=target_keys,
        target_nodes=tuple(range(20)) * len(keys),
        source_identity="a" * 64,
        manifest_sha256="b" * 64,
        child_closure_sha256="c" * 64,
        row_content_identity="d" * 64,
        tensor_identities=(),
        mode="PRIMARY",
        parent_identity=PARENT_IDENTITY,
        _provenance=tensor_module._PRIMARY_SUPPORT_CAPABILITY,
    )

    assert record.keys == keys
    assert record.target_keys == target_keys


def test_b_support_retains_target_with_inactive_final_node_masked() -> None:
    cutoff = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)
    rows = _rows(cutoff)
    for row in rows:
        if row["instrument_id"] == ALL_INSTRUMENTS[0] and row["decision_time"] == cutoff:
            row["source_active"] = 0.0

    support = candidate_independent_support(
        rows,
        [cutoff],
        contract=TensorContract(lookback_minutes=2),
        _synthetic=True,
    )

    target_key = f"{ALL_INSTRUMENTS[0]}|{cutoff.isoformat()}"
    assert target_key in support.target_keys
    assert len(support.target_keys) == len(ALL_INSTRUMENTS)


def test_terminal_native_metadata_projection_preserves_masks_keys_and_part_bindings(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from experiments.r4_residual_graph import foundation as foundation_module
    from experiments.r4_residual_graph import runtime as runtime_module
    from experiments.r4_residual_graph import terminal_support as terminal_module

    cutoff = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    feature_rows = []
    target_rows = []
    context_rows = []
    for offset in range(61):
        timestamp = cutoff - timedelta(minutes=60 - offset)
        for instrument_index, instrument in enumerate(ALL_INSTRUMENTS):
            feature: dict[str, object] = {
                name: float(instrument_index + index + 1)
                for index, name in enumerate(P0_FEATURE_NAMES)
            }
            feature.update(
                instrument_id=instrument,
                decision_time=timestamp,
                latest_feature_bar_end=timestamp,
                feature_data_asof=timestamp,
                feature_available_at=timestamp,
                source_active=1.0,
                source_class=foundation_module.SOURCE_CLASS,
                evidence_label=foundation_module.LABEL,
                return_60s_available=1.0,
                return_300s_available=1.0,
                quality_healthy=1.0,
                gap_known_by_cutoff=0.0,
            )
            # Exercise finite, null, NaN and infinity with unchanged native mask semantics.
            feature["return_60s"] = (
                0.25
                if offset == 60
                else (None, np.nan, np.inf, -np.inf, 0.25)[instrument_index % 5]
            )
            if offset < 60 and instrument_index % 5 in (1, 2, 3):
                feature["return_60s_available"] = 0.0
            if offset == 60:
                if instrument_index == 0:
                    feature["feature_available_at"] = None
                elif instrument_index == 1:
                    feature["source_active"] = 0.0
                elif instrument_index == 2:
                    feature["feature_data_asof"] = None
                    feature["latest_feature_bar_end"] = None
                elif instrument_index == 3:
                    feature["return_60s"] = None
                elif instrument_index == 4:
                    feature["return_60s_available"] = 0.0
            feature_rows.append(feature)
            target_rows.append(
                {
                    "target_id": f"{instrument}:{offset}",
                    "instrument_id": instrument,
                    "decision_time": timestamp,
                    "target_available_at": timestamp + timedelta(minutes=15),
                    "target_valid": True,
                    "horizon_minutes": 15,
                    "block": foundation_module.TERMINAL_BLOCK if offset == 60 else "DEV_3",
                    "target_return": 999999.0,
                    "outcome_price": 999999.0,
                }
            )
            context_rows.append(
                {
                    "instrument_id": instrument,
                    "decision_time": timestamp,
                    "current_available": 1.0,
                    "realised_return": 999999.0,
                }
            )
    parts = []
    for kind, rows in (
        ("feature", feature_rows),
        ("target", target_rows),
        ("context", context_rows),
    ):
        path = tmp_path / f"{kind}.parquet"
        pl.DataFrame(rows).write_parquet(path)
        part: dict[str, str | int] = {
            "kind": kind,
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if kind == "target":
            part["horizon_minutes"] = 15
        parts.append(part)
    parent = foundation_module.Lab0Capsule._create(
        token=foundation_module._LAB0_SEAL,
        manifest_path=tmp_path / "manifest.json",
        manifest_sha256="a" * 64,
        manifest={},
        instruments=tuple(ALL_INSTRUMENTS),
        child_identities=tuple(parts),
    )
    object.__setattr__(parent, "_authenticated_parent", foundation_module._LAB0_SEAL)
    monkeypatch.setattr(runtime_module, "MANIFEST_IDENTITY", parent.manifest_sha256)
    monkeypatch.setattr(runtime_module, "CLOSURE_IDENTITY", parent.child_closure_sha256)
    config = FrozenRuntimeConfig(
        manifest_identity=parent.manifest_sha256,
        child_closure_identity=parent.child_closure_sha256,
    )
    graph = build_fixed_economic_graph()
    observed_columns = []
    original_collect = pl.LazyFrame.collect
    original_read = pl.read_parquet
    projection_path = tmp_path / "input/terminal-support-input.parquet"

    def collect(frame, *args, **kwargs):
        columns = frame.collect_schema().names()
        observed_columns.append(columns)
        forbidden = {"target_return", "outcome_price", "realised_return", "return_60s"}
        assert not forbidden & set(columns)
        return original_collect(frame, *args, **kwargs)

    def forbidden_full_read(path, *args, **kwargs):
        assert path == projection_path
        if "n_rows" not in kwargs or kwargs["n_rows"] != 0:
            assert set(kwargs["columns"]) <= terminal_module.ALLOWED_TERMINAL_COLUMNS
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(pl.LazyFrame, "collect", collect)
    monkeypatch.setattr(pl, "read_parquet", forbidden_full_read)
    projected = foundation_module._terminal_metadata_features(parent).to_dicts()
    expected_masks = {}
    for offset in range(61):
        native_rows = feature_rows[offset * 20 : (offset + 1) * 20]
        tensor = build_masked_sequence(
            native_rows,
            cast(datetime, native_rows[0]["decision_time"]),
            contract=TensorContract(lookback_minutes=0),
        )
        for node, (original, projection) in enumerate(
            zip(native_rows, projected[offset * 20 : (offset + 1) * 20], strict=True)
        ):
            assert tuple(projection["feature_mask"]) == tuple(tensor.value_mask[-1, node])
            assert tuple(projection["availability_mask"]) == tuple(
                tensor.availability_mask[-1, node]
            )
            assert projection["node_mask"] == bool(tensor.node_mask[-1, node])
            expected_masks[(original["instrument_id"], original["decision_time"])] = projection
    metadata = foundation_module.authenticate_terminal_metadata(parent, config, graph)
    rows = metadata.to_rows()
    assert len(rows) == 20
    for row in rows:
        expected = expected_masks[(row["instrument_id"], row["decision_time"])]
        assert tuple(row["feature_mask"]) == tuple(expected["feature_mask"])
        assert tuple(row["availability_mask"]) == tuple(expected["availability_mask"])
        assert row["node_mask"] == expected["node_mask"]
        assert set(row) <= terminal_module.ALLOWED_TERMINAL_COLUMNS
    from experiments.r4_residual_graph import execution as execution_module

    projection_path.parent.mkdir()
    with projection_path.open("xb") as stream:
        pl.DataFrame(rows).write_parquet(stream)
    rows = execution_module._terminal_rows_from_input(tmp_path, metadata)
    history = foundation_module.terminal_history_bindings(parent, cutoff)
    assert history["history_row_count"] == 1200
    assert history["first_terminal_lookback_row_count"] == 1200
    assert history["first_terminal_lookback_instrument_count"] == 20
    assert history["first_terminal_lookback_minute_count"] == 60
    support = build_terminal_support(
        rows,
        config=TerminalSupportConfig.from_authenticated_parent(parent, config, graph),
        development_register_closed=True,
        authenticated_metadata=metadata,
        **history,
    )
    assert support.keys == tuple(
        f"{cutoff.isoformat()}|{instrument}" for instrument in ALL_INSTRUMENTS
    )
    assert support.per_instrument_counts == tuple((instrument, 1) for instrument in ALL_INSTRUMENTS)
    assert observed_columns

    # Physical child identity remains authenticated even when permitted projection is unchanged.
    changed_parts = tuple(
        {**part, "sha256": "b" * 64} if part["kind"] == "feature" else part for part in parts
    )
    object.__setattr__(parent, "child_identities", changed_parts)
    monkeypatch.setattr(runtime_module, "CLOSURE_IDENTITY", parent.child_closure_sha256)
    changed_config = replace(config, child_closure_identity=parent.child_closure_sha256)
    changed = foundation_module.authenticate_terminal_metadata(parent, changed_config, graph)
    original = metadata.to_rows()[0]
    assert changed.to_rows()[0]["feature_mask"] == original["feature_mask"]
    assert changed.to_rows()[0]["feature_identity"] != original["feature_identity"]


def test_terminal_capsule_writer_reader_preserves_multivalued_identities(tmp_path) -> None:
    import copy
    import json

    from experiments.r4_residual_graph.execution import _validate_terminal_capsule_payload

    timestamp = datetime(2026, 6, 26, 14, 7, tzinfo=UTC)
    rows = _terminal_rows(timestamp) + _terminal_rows(timestamp + timedelta(minutes=1))
    for index, row in enumerate(rows):
        for name in ("feature_identity", "context_identity", "history_identity"):
            row[name] = f"{name}:{index}"
    path = tmp_path / "terminal-support-canonical.json"
    capsule = build_terminal_support(
        rows, config=_smoke_config(), output_path=path, development_register_closed=True
    )
    payload = json.loads(path.read_text())
    assert capsule.key_count == 40
    assert capsule.decision_time_count == 2
    assert len(payload["input_identities"]) == len(capsule.input_identities)
    assert sum(name == "feature_identity" for name, _ in payload["input_identities"]) == 40
    _validate_terminal_capsule_payload(payload, expected_payload=capsule.to_dict())
    assert payload["artifact_identity"] == capsule.artifact_identity
    missing = copy.deepcopy(payload)
    missing["input_identities"].pop()
    with pytest.raises(ValueError, match="not canonical"):
        _validate_terminal_capsule_payload(missing)
    changed = copy.deepcopy(payload)
    changed["input_identities"][0][1] = "tampered"
    with pytest.raises(ValueError, match="not canonical"):
        _validate_terminal_capsule_payload(changed)
    extra = dict(payload, unexpected="unbound")
    with pytest.raises(ValueError, match="representation is not canonical"):
        _validate_terminal_capsule_payload(extra)
