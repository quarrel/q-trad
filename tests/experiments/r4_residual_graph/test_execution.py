from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

import experiments.r4_residual_graph.execution as execution_module
from experiments.r4_residual_graph import ALL_INSTRUMENTS
from experiments.r4_residual_graph.attempt_artifacts import (
    AttemptIdentity,
    CreateOnlyAttemptJournal,
    JournalStatus,
)
from experiments.r4_residual_graph.execution import (
    G0ExecutionIdentity,
    _authenticated_dev_control_training_rows,
    _development_stage_preprocessor_identities,
    _fixture_aggregate_metrics,
    _fixture_close_development_register,
    _fixture_materialise_terminal_support_stage,
    _fixture_predict_terminal_slot,
    _fixture_prepare_execution,
    _fixture_run_development_slots,
    _foundation_rows,
    _ordered_terminal_targets,
    _sha256,
    _summarize_fit_evidence,
    _validate_development_attempt_ledger,
    _validate_fit_evidence,
    main,
    prepare_execution,
    run_development_slots,
)
from experiments.r4_residual_graph.runtime import (
    FITTED_FAMILY_IDS,
    OUTPUT_POLICY,
    PRIMARY_SCHEDULE,
    PRIMARY_SEEDS,
    RUNTIME_VERSION,
    FrozenRuntimeConfig,
)


def test_fixture_execution_lifecycle_is_outcome_safe(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    assert identity.code_head
    _fixture_run_development_slots(tmp_path, all_development=True)
    register_identity = _fixture_close_development_register(tmp_path)
    support_identity = _fixture_materialise_terminal_support_stage(tmp_path)
    prediction_identity = _fixture_predict_terminal_slot(
        tmp_path, family_id=FITTED_FAMILY_IDS[0], seed=PRIMARY_SEEDS[0]
    )
    report_identity = _fixture_aggregate_metrics(tmp_path)
    assert all(
        len(value) == 64
        for value in (register_identity, support_identity, prediction_identity, report_identity)
    )
    report_input = json.loads((tmp_path / "metric" / "report-input.json").read_text())
    assert report_input["outcomes_loaded"] is False
    assert report_input["outcome_gate"] == "OPEN_AFTER_CREATE_ONLY_PREDICTIONS"
    support = json.loads((tmp_path / "support" / "terminal-support.json").read_text())
    assert support["outcomes_loaded"] is False
    assert "target_return" not in support
    prediction = next((tmp_path / "prediction").glob("*.json"))
    assert json.loads(prediction.read_text())["outcomes_loaded"] is False


def test_execution_requires_complete_development_register(tmp_path: Path) -> None:
    _fixture_prepare_execution(tmp_path)
    _fixture_run_development_slots(tmp_path, slots=(PRIMARY_SCHEDULE[0],))
    with pytest.raises(ValueError, match="incomplete"):
        _fixture_close_development_register(tmp_path)


def test_execution_rejects_terminal_stage_as_development(tmp_path: Path) -> None:
    _fixture_prepare_execution(tmp_path)
    with pytest.raises(ValueError, match="DEV_2 and DEV_3"):
        _fixture_run_development_slots(tmp_path, slots=(PRIMARY_SCHEDULE[-1],))


def test_execution_rejects_tampered_register_and_prediction_before_support(
    tmp_path: Path,
) -> None:
    _fixture_prepare_execution(tmp_path)
    _fixture_run_development_slots(tmp_path, all_development=True)
    _fixture_close_development_register(tmp_path)
    with pytest.raises(FileNotFoundError, match="terminal-support"):
        _fixture_predict_terminal_slot(
            tmp_path, family_id=FITTED_FAMILY_IDS[0], seed=PRIMARY_SEEDS[0]
        )
    register_path = tmp_path / "register" / "development-register.json"
    payload = json.loads(register_path.read_text())
    payload["register_identity"] = "0" * 64
    register_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="identity is invalid"):
        _fixture_materialise_terminal_support_stage(tmp_path)


def test_execution_preparation_is_create_only_and_root_bound(tmp_path: Path) -> None:
    _fixture_prepare_execution(tmp_path)
    with pytest.raises(FileExistsError, match="new empty output root"):
        _fixture_prepare_execution(tmp_path)
    with pytest.raises(FileNotFoundError, match="g0-identity"):
        run_development_slots(tmp_path / "other")


@pytest.mark.parametrize("rows_per_block", [1, 4])
def test_preparation_identity_lookup_construction_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows_per_block: int
) -> None:
    from collections.abc import Iterator
    from datetime import timedelta
    from types import SimpleNamespace

    import numpy as np
    import polars as pl

    from experiments.r4_residual_graph import (
        foundation as foundation_module,
    )
    from experiments.r4_residual_graph import (
        prepared_stage as prepared_stage_module,
    )
    from experiments.r4_residual_graph import (
        runtime as runtime_module,
    )
    from experiments.r4_residual_graph import (
        tensor as tensor_module,
    )
    from experiments.r4_residual_graph import (
        tensor_store as tensor_store_module,
    )

    times = tuple(
        datetime(2026, 5, 29, 14, 5, tzinfo=UTC) + timedelta(minutes=index)
        for index in range(3 * rows_per_block)
    )
    rows = pl.DataFrame(
        {
            "block": [
                block for block in ("DEV_1", "DEV_2", "DEV_3") for _ in range(rows_per_block)
            ],
            "decision_time": times,
        }
    )
    expected_identities = {time.isoformat(): f"tensor-{index}" for index, time in enumerate(times)}
    parent = SimpleNamespace(manifest_sha256="manifest", child_closure_sha256="parent")
    foundation = SimpleNamespace(rows=rows, content_identity="foundation")

    class CountingIdentities:
        def __init__(self, pairs: tuple[tuple[str, str], ...]) -> None:
            self.pairs = pairs
            self.iterations = 0

        def __iter__(self) -> Iterator[tuple[str, str]]:
            self.iterations += 1
            return iter(self.pairs)

    class CountingRawStore:
        def __init__(self) -> None:
            self.root = tmp_path / "raw"
            self.manifest = {"store_identity": "raw-store"}
            self.accesses = 0

        @property
        def tensor_identities(self) -> dict[str, str]:
            self.accesses += 1
            return dict(expected_identities)

    raw_store = CountingRawStore()
    supports: list[Any] = []
    bindings: list[tuple[tuple[str, str], ...]] = []
    predictions: list[dict[str, Any]] = []
    trainings: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    preprocessor = SimpleNamespace(
        mode="PRIMARY",
        feature_names=("feature",),
        means=np.array([0.0]),
        scales=np.array([1.0]),
        training_cutoff=times[0],
        contract_identity="contract",
        fit_partition="GLOBAL",
        binary_features=(),
        training_partition_identity="partition",
        reduction_algorithm="reduction",
    )

    def support_input(_corpus: Any, _parent: Any, selected_times: tuple[datetime, ...]) -> Any:
        return SimpleNamespace(
            support=SimpleNamespace(
                keys=tuple(time.isoformat() for time in selected_times), source_identity="source"
            ),
            identity="input",
        )

    def bind_support(capability: Any, identities: Mapping[str, str]) -> Any:
        pairs = tuple(identities.items())
        keys = capability.support.keys
        assert pairs == tuple((key, expected_identities[key]) for key in keys)
        bindings.append(pairs)
        support = SimpleNamespace(
            keys=keys,
            source_identity="source",
            identity=_sha256(pairs),
            target_keys=tuple(
                f"{instrument}|{key}"
                for key in reversed(keys)
                for instrument in ("EURUSD", "GBPUSD")
            ),
            tensor_identities=CountingIdentities(pairs),
        )
        supports.append(support)
        return support

    def build_partition(context: dict[str, Any]) -> tuple[Any, tuple[()]]:
        assert context["_preparation_capability"] is preparation_capability
        return SimpleNamespace(
            row_keys=tuple(reversed(context["support"].keys)), seal="partition"
        ), ()

    def prediction(**kwargs: Any) -> Any:
        predictions.append(kwargs)
        return SimpleNamespace(**kwargs)

    def training(**kwargs: Any) -> Any:
        trainings.append(kwargs)
        return SimpleNamespace(**kwargs)

    def persist(_root: Path, **kwargs: Any) -> Path:
        persisted.append(kwargs)
        return tmp_path / kwargs["stage"]

    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    monkeypatch.setattr(
        execution_module, "_file_digest", lambda _path: execution_module.MANIFEST_SHA256
    )
    identity = SimpleNamespace(identity="g0", to_dict=lambda: {"identity": "g0"})
    monkeypatch.setattr(execution_module, "capture_g0_identity", lambda *_args: identity)
    monkeypatch.setattr(
        execution_module,
        "_write_json_once",
        lambda path, payload: payloads.__setitem__(path.name, payload),
    )
    monkeypatch.setattr(execution_module, "_foundation_rows", lambda value: value)
    monkeypatch.setattr(
        execution_module, "_reconstruct_control_regression", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(execution_module, "_build_execution_support_input", support_input)
    monkeypatch.setattr(execution_module, "_build_tensors", lambda *_args: {})
    monkeypatch.setattr(execution_module, "_build_training_partition", build_partition)
    monkeypatch.setattr(
        execution_module, "training_preprocessor_identity", lambda _value: "preprocessor"
    )
    monkeypatch.setattr(
        execution_module,
        "_support_payload",
        lambda support, **_kwargs: {"tensor_identities": list(support.tensor_identities)},
    )
    monkeypatch.setattr(foundation_module, "authenticate_parent", lambda **_kwargs: parent)
    monkeypatch.setattr(foundation_module, "_preparation_parent", lambda value: value)
    monkeypatch.setattr(foundation_module, "load_parent_rows", lambda _parent: rows)
    monkeypatch.setattr(
        foundation_module, "build_oof_residual_foundation", lambda *_args, **_kwargs: rows
    )
    monkeypatch.setattr(
        foundation_module,
        "authenticate_oof_residual_foundation",
        lambda *_args, **_kwargs: foundation,
    )
    monkeypatch.setattr(
        foundation_module, "materialise_development_support", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        foundation_module, "project_authenticated_parent_support_rows", lambda _parent: rows
    )
    monkeypatch.setattr(
        foundation_module, "EXPECTED_COUNTS", {"lab_s_support": {"development": len(times)}}
    )
    monkeypatch.setattr(
        foundation_module,
        "reconstruct_authenticated_linear_forecasts",
        lambda *_args, **_kwargs: {
            block: {"keys": (), "LOCAL_RIDGE": (), "FULLY_POOLED_LOCAL_RIDGE": ()}
            for block in ("DEV_1", "DEV_2", "DEV_3")
        },
    )
    monkeypatch.setattr(tensor_module, "authenticate_support_corpus", lambda *_args: object())
    monkeypatch.setattr(tensor_module, "bind_support_tensor_identities", bind_support)
    monkeypatch.setattr(
        tensor_module,
        "fit_training_preprocessors",
        lambda partitions: dict.fromkeys(partitions, preprocessor),
    )
    monkeypatch.setattr(
        tensor_store_module, "build_raw_tensor_store", lambda *_args, **_kwargs: raw_store
    )
    preparation_capability = object()
    monkeypatch.setattr(
        tensor_store_module,
        "_preparation_partition_construction",
        lambda store, corpus, support_input: nullcontext(preparation_capability),
    )
    monkeypatch.setattr(
        runtime_module, "validate_residual_foundation", lambda _foundation: object()
    )
    monkeypatch.setattr(
        runtime_module, "project_authenticated_training_rows", lambda *_args, **_kwargs: ()
    )
    monkeypatch.setattr(
        runtime_module, "PredictionBatch", SimpleNamespace(from_authenticated_support=prediction)
    )
    monkeypatch.setattr(
        runtime_module, "ResidualTrainingBatch", SimpleNamespace(from_authenticated_oof=training)
    )
    monkeypatch.setattr(prepared_stage_module, "persist_prepared_stage", persist)

    assert (
        execution_module.prepare_authenticated_execution(
            tmp_path / "output", manifest_path=manifest
        )
        is identity
    )

    assert raw_store.accesses == 1
    assert len(bindings) == 5
    assert [entry["stage"] for entry in persisted] == ["DEV_2", "DEV_3"]
    assert len(predictions) == len(trainings) == 2
    for call, eval_support in zip(predictions, (supports[2], supports[4]), strict=True):
        assert eval_support.tensor_identities.iterations == 1
        assert call["input_identity"] == _sha256(
            {
                "support_identity": eval_support.identity,
                "row_keys": eval_support.target_keys,
                "tensor_identities": tuple(
                    expected_identities[key.rsplit("|", 1)[-1]] for key in eval_support.target_keys
                ),
                "preprocessor_identity": "preprocessor",
                "policy": runtime_module.TIMESTAMP_MATERIALISATION_POLICY,
            }
        )
    assert all(
        call["stream"] is True and call["tensors"] is raw_store for call in predictions + trainings
    )
    partition_keys = list(reversed(expected_identities))
    partition_identities = [expected_identities[key] for key in partition_keys]
    assert payloads["training-partition.json"] == {
        "seal": "partition",
        "support_identity": supports[0].identity,
        "source_identity": "source",
        "row_keys": partition_keys,
        "tensor_identities": partition_identities,
        "tensor_identity": _sha256({"tensor_identities": partition_identities}),
    }
    assert payloads["execution-support.json"]["tensor_identities"] == list(
        expected_identities.items()
    )


def test_preparation_stage_source_survives_prepared_and_cache_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.r4_residual_graph import tensor as tensor_module
    from experiments.r4_residual_graph import tensor_store as tensor_store_module
    from experiments.r4_residual_graph.prepared_stage import load_prepared_stage
    from experiments.r4_residual_graph.runtime import training_preprocessor_identity
    from experiments.r4_residual_graph.stage_cache import (
        CacheInput,
        build_stage_cache,
        load_stage_cache_batches,
    )
    from tests.experiments.r4_residual_graph.test_remediation import (
        _preparation_support,
        _preparation_support_corpus,
        _raw_publication_source,
    )
    from tests.experiments.r4_residual_graph.test_runtime_authenticated_masks import (
        _training_fixture,
    )

    foundation, _, _, tensor_rows = _training_fixture(False)
    # Reuse the bounded synthetic materialiser; all identity-bearing handoffs below are real.
    source = _raw_publication_source(monkeypatch, 3)
    source.clear()
    source.update((tensor.decision_time.isoformat(), tensor) for _, tensor in tensor_rows)
    corpus, parent = _preparation_support_corpus(monkeypatch, source)
    support_input, support = _preparation_support(corpus, parent, source)
    raw_store = tensor_store_module.build_raw_tensor_store(
        tmp_path / "input" / "raw-tensors",
        source,
        tuple(source),
        support_input_identity=support_input.identity,
        source_identity=support.source_identity,
    )
    identities = raw_store.tensor_identities
    context = {
        "parent": parent,
        "rows": foundation.rows,
        "foundation": foundation,
        "support": support,
        "support_corpus": corpus,
        "tensors": raw_store,
    }
    reads: list[str] = []
    getitem = tensor_store_module.RawTensorStore.__getitem__
    fit = tensor_module.fit_training_preprocessors
    stage_sources: dict[str, str] = {}
    stage_seals: dict[str, str] = {}

    def counted_getitem(store: Any, key: str) -> Any:
        reads.append(key)
        return getitem(store, key)

    def counted_fit(partitions: Any) -> Any:
        assert reads == []
        stage_sources.update(
            (name, partition.source_identity) for name, partition in partitions.items()
        )
        stage_seals.update((name, partition.seal) for name, partition in partitions.items())
        result = fit(partitions)
        assert reads == list(source)
        return result

    monkeypatch.setattr(tensor_store_module.RawTensorStore, "__getitem__", counted_getitem)
    monkeypatch.setattr(tensor_module, "fit_training_preprocessors", counted_fit)
    with tensor_store_module._preparation_partition_construction(
        raw_store, corpus, support_input
    ) as capability:
        context["_preparation_capability"] = capability
        global_partition, _ = execution_module._build_training_partition(context)
        bindings, global_preprocessor = execution_module._persist_prepared_development_stages(
            tmp_path, context, raw_store, global_partition, identities
        )
    assert len(set(stage_sources.values())) == 3
    assert len(set(stage_seals.values())) == 3
    for stage, binding in bindings.items():
        training, prediction, preprocessor, manifest = load_prepared_stage(
            Path(binding["path"]),
            expected_stage_identity=binding["stage_identity"],
            expected_raw_store_path=raw_store.root,
            expected_raw_store_identity=raw_store.manifest["store_identity"],
            trusted_root=tmp_path,
        )
        assert preprocessor.training_partition_identity == stage_seals[stage]
        assert training_preprocessor_identity(preprocessor) != training_preprocessor_identity(
            global_preprocessor
        )
        train_blocks = ("DEV_1",) if stage == "DEV_2" else ("DEV_1", "DEV_2")
        stage_input = execution_module._build_execution_support_input(
            corpus,
            parent,
            tuple(datetime.fromisoformat(key) for key in source)[: len(train_blocks)],
        )
        expected_support = tensor_module.bind_support_tensor_identities(
            stage_input, {key: identities[key] for key in stage_input.support.keys}
        )
        assert expected_support.source_identity == stage_sources[stage]
        assert training.support_identity == expected_support.identity
        cache_input = CacheInput(
            stage=stage,
            training_blocks=train_blocks,
            foundation_identity=foundation.content_identity,
            support_identity=training.support_identity,
            tensor_identity=tensor_module.TensorContract().identity,
            preprocessor_identity=training_preprocessor_identity(preprocessor),
            config_identity=FrozenRuntimeConfig().identity,
            numerical_runtime_identity="n" * 64,
            producer_head="h" * 40,
            universe=tuple(ALL_INSTRUMENTS),
            node_order=tuple(ALL_INSTRUMENTS),
            feature_order=tensor_module.TensorContract().feature_names,
        )
        verified = build_stage_cache(
            tmp_path,
            cache_input,
            training_batch=training,
            prediction_batch=prediction,
            build_id=f"source-binding-{stage}",
        )
        cached_training, cached_prediction, _ = load_stage_cache_batches(
            verified.root, expected=cache_input
        )
        assert cached_training.support_identity == manifest["training"]["support_identity"]
        assert cached_prediction.input_identity == prediction.input_identity
        assert tuple(cached_training.row_keys) == training.row_keys
        assert tuple(cached_prediction.row_keys) == prediction.row_keys


def test_authenticated_dispatch_uses_real_orchestration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import experiments.r4_residual_graph.execution as execution_module

    _fixture_prepare_execution(tmp_path)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(execution_module, "_is_real_execution", lambda _root: True)
    monkeypatch.setattr(
        execution_module,
        "run_authenticated_development_slots",
        lambda root, selected, config, identity: (
            calls.append((root, selected, config, identity)) or ("real-slot",)
        ),
    )
    result = run_development_slots(tmp_path, slots=(PRIMARY_SCHEDULE[0],))
    assert result == ("real-slot",)
    assert calls and calls[0][0] == tmp_path.resolve()


def test_public_lifecycle_requires_authenticated_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest_path"):
        prepare_execution(tmp_path)
    with pytest.raises(FileNotFoundError, match="g0-identity"):
        run_development_slots(tmp_path)


def test_development_stage_preprocessor_cache_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.r4_residual_graph.execution as execution_module

    calls: list[str] = []

    def fake_stage_identity(_context: dict[str, object], stage: str) -> str:
        calls.append(stage)
        return f"{stage}-identity"

    monkeypatch.setattr(
        execution_module,
        "_development_stage_preprocessor_identity",
        fake_stage_identity,
    )
    stages = tuple(slot[2] for slot in PRIMARY_SCHEDULE if slot[2] in {"DEV_2", "DEV_3"})
    identities = _development_stage_preprocessor_identities({}, stages)
    assert len(stages) == 30
    assert identities == {"DEV_2": "DEV_2-identity", "DEV_3": "DEV_3-identity"}
    assert calls.count("DEV_2") == 1
    assert calls.count("DEV_3") == 1


def test_production_cli_rejects_fixture_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="authenticated preparation artifacts"):
        main(
            [
                "--output-root",
                str(tmp_path),
                "development-slot",
                "--all",
            ]
        )


def test_foundation_rows_exclude_terminal_boundary_warmups() -> None:
    from datetime import datetime, timedelta

    import polars as pl

    boundary = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    base = datetime(2026, 6, 26, 13, 46, tzinfo=UTC)
    rows = pl.DataFrame(
        {
            "target_available_at": [
                datetime(2026, 6, 26, 14, 5, tzinfo=UTC),
                *([boundary] * 400),
            ],
            "decision_time": [
                datetime(2026, 6, 26, 14, 4, tzinfo=UTC),
                *(base + timedelta(minutes=index % 20) for index in range(400)),
            ],
        }
    )
    foundation_rows = _foundation_rows(rows)
    assert foundation_rows.height == 1
    assert foundation_rows["target_available_at"].item() < boundary


def test_terminal_history_requires_pre_boundary_rows_and_excludes_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timedelta

    import polars as pl

    import experiments.r4_residual_graph.execution as execution_module

    boundary = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    base = datetime(2026, 6, 26, 13, 6, tzinfo=UTC)
    history = pl.DataFrame(
        {
            "decision_time": [base + timedelta(minutes=index % 60) for index in range(1200)],
            "instrument_id": ["EURUSD"] * 1200,
            "target_valid": [False] * 1200,
        }
    )
    calls: list[dict[str, object]] = []

    def load_history(*_args: object, **kwargs: object) -> pl.DataFrame:
        calls.append(kwargs)
        return history

    monkeypatch.setattr(execution_module, "load_parent_rows", load_history)
    result = execution_module._terminal_history_rows(object(), boundary)
    assert result.height == 1200
    assert calls == [{"include_invalid": True, "include_return": False}]
    assert "target_return" not in result.columns
    with pytest.raises(ValueError, match="causal history is empty"):
        monkeypatch.setattr(execution_module, "load_parent_rows", lambda *_a, **_k: history.clear())
        execution_module._terminal_history_rows(object(), datetime(2026, 6, 26, 13, 6, tzinfo=UTC))


def test_dev_control_training_rows_are_return_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timedelta

    import polars as pl

    import experiments.r4_residual_graph.execution as execution_module
    from experiments.r4_residual_graph import foundation
    from experiments.r4_residual_graph.tensor import P0_FEATURE_NAMES

    boundary = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    parent = foundation.Lab0Capsule._create(
        foundation._LAB0_SEAL,
        manifest_path=Path("synthetic-manifest"),
        manifest_sha256="b" * 64,
        manifest={},
        instruments=("EUR_USD",),
        child_identities=(),
    )
    object.__setattr__(parent, "_authenticated_parent", foundation._LAB0_SEAL)
    calls: list[dict[str, object]] = []
    rows = pl.DataFrame(
        {
            "target_id": ["eligible", "boundary"],
            "instrument_id": ["EUR_USD", "EUR_USD"],
            "decision_time": [
                boundary - timedelta(minutes=21),
                boundary - timedelta(minutes=20),
            ],
            "block": ["DEV_3", "DEV_3"],
            "target_return": [0.25, 999.0],
            "target_valid": [True, True],
            "target_available_at": [boundary - timedelta(minutes=1), boundary],
            "horizon_minutes": [15, 15],
            "feature_available_at": [boundary - timedelta(minutes=21)] * 2,
            **{name: [1.0, 999.0] for name in P0_FEATURE_NAMES},
        }
    )

    def load_training(*_args: object, **kwargs: object) -> pl.DataFrame:
        calls.append(kwargs)
        return rows

    monkeypatch.setattr(execution_module, "load_parent_rows", load_training)
    monkeypatch.setattr(foundation, "load_parent_rows", load_training)
    result = _authenticated_dev_control_training_rows(parent, boundary)
    assert result.equals(rows.head(1).select([*foundation._DEV_CONTROL_COLUMNS, *P0_FEATURE_NAMES]))
    assert calls == [{"include_return": True}, {"include_return": True}]
    capability = foundation._seal_dev_control_training(parent, result, boundary)
    foundation._validate_dev_control_training(capability, parent, boundary)

    from dataclasses import fields

    from experiments.r4_residual_graph.terminal_support import (
        AuthenticatedTerminalPredictionInput,
    )

    assert "history_identity" in {
        field.name for field in fields(AuthenticatedTerminalPredictionInput)
    }


def test_terminal_outcomes_are_reordered_to_sealed_capsule_keys() -> None:
    keys = ("EURUSD|2026-06-26T14:06:00+00:00", "GBPUSD|2026-06-26T14:06:00+00:00")
    shuffled = {keys[1]: 2.0, "EXCLUDED|2026-06-26T14:06:00+00:00": 99.0, keys[0]: 1.0}
    assert tuple(_ordered_terminal_targets(shuffled, keys)) == keys
    with pytest.raises(ValueError, match="exactly match"):
        _ordered_terminal_targets({keys[0]: 1.0}, keys)
    with pytest.raises(ValueError, match="exactly match"):
        _ordered_terminal_targets(shuffled, (keys[0], keys[0]))


def test_terminal_prediction_input_rejects_history_identity_tamper() -> None:
    import hashlib
    from datetime import datetime

    from experiments.r4_residual_graph.tensor import P0_FEATURE_NAMES, _canonical_bytes
    from experiments.r4_residual_graph.terminal_support import (
        _TERMINAL_PREDICTION_INPUT_SEAL,
        AuthenticatedTerminalPredictionInput,
        _canonical_row_value,
    )

    decision_time = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    row = {"instrument_id": "fx:eur-usd", "decision_time": decision_time}
    row.update({name: 0.0 for name in P0_FEATURE_NAMES})
    key = f"{row['instrument_id']}|{decision_time.isoformat()}"
    identities = {
        "terminal_metadata_identity": "a" * 64,
        "terminal_support_identity": "b" * 64,
        "parent_identity": "c" * 64,
        "manifest_sha256": "d" * 64,
        "child_closure_sha256": "e" * 64,
        "graph_identity": "f" * 64,
        "config_identity": "1" * 64,
        "preprocessor_identity": "2" * 64,
        "history_identity": "3" * 64,
    }
    payload = {
        "rows": [{column: _canonical_row_value(value) for column, value in row.items()}],
        "forecasts": ((key, 0.0),),
        **identities,
    }
    content_identity = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    AuthenticatedTerminalPredictionInput._create(
        token=_TERMINAL_PREDICTION_INPUT_SEAL,
        rows=(row,),
        forecasts=((key, 0.0),),
        content_identity=content_identity,
        **identities,
    )
    with pytest.raises(ValueError, match="content identity mismatch"):
        AuthenticatedTerminalPredictionInput._create(
            token=_TERMINAL_PREDICTION_INPUT_SEAL,
            rows=(row,),
            forecasts=((key, 0.0),),
            content_identity=content_identity,
            **{**identities, "history_identity": "4" * 64},
        )


def test_authenticated_materialisation_is_metadata_only_and_create_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    import experiments.r4_residual_graph.execution as execution_module
    import experiments.r4_residual_graph.foundation as foundation_module
    import experiments.r4_residual_graph.graph as graph_module
    import experiments.r4_residual_graph.terminal_support as terminal_support_module

    identity = SimpleNamespace(identity="a" * 64, output_root_identity="b" * 64)
    metadata = SimpleNamespace(
        content_identity="c" * 64,
        to_rows=lambda: [
            {"decision_time": datetime(2026, 6, 26, 14, 6, tzinfo=UTC), "target_valid": True}
        ],
    )
    capsule = SimpleNamespace(
        key_count=20,
        per_instrument_counts=tuple((instrument, 1) for instrument in ALL_INSTRUMENTS),
        artifact_identity="d" * 64,
        to_dict=lambda: {"artifact_identity": "d" * 64},
    )
    calls: list[str] = []

    def fail_forbidden(*_args: object, **_kwargs: object) -> object:
        calls.append("forbidden")
        raise AssertionError("metadata-only materialisation opened outcome-bearing data")

    def fake_build(*_args: object, **kwargs: object) -> object:
        output_path = kwargs.get("output_path")
        if output_path is not None:
            path = Path(str(output_path))
            if path.exists():
                raise FileExistsError(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        return capsule

    monkeypatch.setattr(
        execution_module,
        "_load_authenticated_parent_context",
        lambda *_args, **_kwargs: {"current_identity": identity, "parent": object()},
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_metadata",
        lambda *_args, **_kwargs: {"register_identity": "e" * 64},
    )
    monkeypatch.setattr(execution_module, "_load_real_context", fail_forbidden)
    monkeypatch.setattr(execution_module, "load_parent_rows", fail_forbidden)
    monkeypatch.setattr(foundation_module, "load_parent_rows", fail_forbidden)
    monkeypatch.setattr(foundation_module, "_authenticated_training_rows", fail_forbidden)
    monkeypatch.setattr(execution_module, "_read_json", fail_forbidden)
    monkeypatch.setattr(execution_module, "_file_digest", lambda _path: "f" * 64)
    monkeypatch.setattr(
        execution_module, "_validate_terminal_capsule_payload", lambda *_a, **_k: None
    )
    monkeypatch.setattr(execution_module, "_terminal_rows_from_input", lambda *_a, **_k: object())
    monkeypatch.setattr(foundation_module, "authenticate_terminal_metadata", lambda **_k: metadata)
    monkeypatch.setattr(foundation_module, "terminal_history_bindings", lambda *_a, **_k: {})
    monkeypatch.setattr(graph_module, "build_fixed_economic_graph", lambda: object())
    monkeypatch.setattr(
        terminal_support_module.TerminalSupportConfig,
        "from_authenticated_parent",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(terminal_support_module, "build_terminal_support", fake_build)

    result = execution_module.materialise_authenticated_terminal_support(
        tmp_path, None, cast(G0ExecutionIdentity, identity)
    )
    assert result == "d" * 64
    assert calls == []
    with pytest.raises(FileExistsError):
        execution_module.materialise_authenticated_terminal_support(
            tmp_path, None, cast(G0ExecutionIdentity, identity)
        )
    assert calls == []


def test_terminal_prediction_validates_capsule_before_dev_control_or_fit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    import experiments.r4_residual_graph.execution as execution_module
    import experiments.r4_residual_graph.foundation as foundation_module
    import experiments.r4_residual_graph.graph as graph_module
    import experiments.r4_residual_graph.terminal_support as terminal_support_module

    identity = SimpleNamespace(identity="a" * 64, output_root_identity="b" * 64)
    metadata = SimpleNamespace(
        content_identity="c" * 64,
        to_rows=lambda: [
            {"decision_time": datetime(2026, 6, 26, 14, 6, tzinfo=UTC), "target_valid": True}
        ],
    )
    expected_capsule = SimpleNamespace(to_dict=lambda: {"artifact_identity": "d" * 64})
    events: list[str] = []

    def forbidden(name: str):
        def fail(*_args: object, **_kwargs: object) -> object:
            events.append(name)
            raise AssertionError(f"{name} opened before capsule validation")

        return fail

    def validate_capsule(*_args: object, **_kwargs: object) -> None:
        events.append("capsule")
        raise ValueError("capsule mismatch")

    monkeypatch.setattr(
        execution_module,
        "_load_authenticated_parent_context",
        lambda *_args, **_kwargs: {"current_identity": identity, "parent": object()},
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_metadata",
        lambda *_args, **_kwargs: {"register_identity": "e" * 64},
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_payload",
        forbidden("semantic-register"),
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_metadata",
        forbidden("metadata-register"),
    )

    def read_json(path: Path) -> dict[str, Any]:
        if path.name in {"terminal-support-canonical.json", "terminal-support-binding.json"}:
            return {}
        raise AssertionError(f"outcome-bearing JSON opened before capsule validation: {path}")

    monkeypatch.setattr(execution_module, "_read_json", read_json)
    monkeypatch.setattr(execution_module, "_load_real_context", forbidden("context"))
    monkeypatch.setattr(
        execution_module, "_authenticated_dev_control_training_rows", forbidden("dev-control")
    )
    monkeypatch.setattr(execution_module, "_terminal_rows_from_input", lambda *_a, **_k: object())
    monkeypatch.setattr(execution_module, "_validate_terminal_capsule_payload", validate_capsule)
    monkeypatch.setattr(foundation_module, "authenticate_terminal_metadata", lambda **_k: metadata)
    monkeypatch.setattr(foundation_module, "terminal_history_bindings", lambda *_a, **_k: {})
    monkeypatch.setattr(graph_module, "build_fixed_economic_graph", lambda: object())
    monkeypatch.setattr(
        terminal_support_module.TerminalSupportConfig,
        "from_authenticated_parent",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        terminal_support_module, "build_terminal_support", lambda *_a, **_k: expected_capsule
    )

    with pytest.raises(ValueError, match="capsule mismatch"):
        execution_module.predict_authenticated_terminal_slot(
            tmp_path,
            FITTED_FAMILY_IDS[0],
            PRIMARY_SEEDS[0],
            None,
            cast(G0ExecutionIdentity, identity),
        )
    assert events == ["capsule"]


@pytest.mark.parametrize(
    ("attempt_status", "attempt_mode", "error_match", "open_extra"),
    [
        ("FAILED", "PRIMARY", "every terminal slot", False),
        ("SUCCEEDED", "SMOKE", "PRIMARY terminal attempts", False),
        ("SUCCEEDED", "PRIMARY", "closed terminal attempt journal", True),
        ("SUCCEEDED", "PRIMARY", "streaming provider checked", False),
    ],
)
def test_aggregation_opens_terminal_outcomes_only_after_closed_predictions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    attempt_status: JournalStatus,
    attempt_mode: str,
    error_match: str,
    open_extra: bool,
) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    import polars as pl

    import experiments.r4_residual_graph.execution as execution_module
    import experiments.r4_residual_graph.foundation as foundation_module
    import experiments.r4_residual_graph.graph as graph_module
    import experiments.r4_residual_graph.runtime as runtime_module
    import experiments.r4_residual_graph.tensor as tensor_module
    import experiments.r4_residual_graph.terminal_support as terminal_support_module

    monkeypatch.setattr(execution_module, "FITTED_FAMILY_IDS", ("family",))
    monkeypatch.setattr(execution_module, "PRIMARY_SEEDS", (1,))
    identity = SimpleNamespace(identity="a" * 64, output_root_identity="b" * 64)
    parent = object()
    context = {
        "parent": parent,
        "foundation": SimpleNamespace(content_identity="c" * 64, ordered_row_keys=()),
        "support": SimpleNamespace(identity="d" * 64),
        "preprocessor": object(),
        "tensors": {},
        "rows": pl.DataFrame(),
    }
    decision_time = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    target_key = f"fx:eur-usd|{decision_time.isoformat()}"
    row = {
        "instrument_id": "fx:eur-usd",
        "decision_time": decision_time,
        "feature_data_asof": decision_time,
        "feature_available_at": decision_time,
        "source_active": True,
        "block": "TERMINAL_FORMER_HOLDOUT",
        "target_valid": True,
        "target_available_at": decision_time,
    }
    row.update({name: 0.0 for name in tensor_module.P0_FEATURE_NAMES})
    tensor = tensor_module.build_masked_sequence([row], decision_time)
    context["tensors"] = {decision_time.isoformat(): tensor}
    context["foundation"].ordered_row_keys = (target_key,)
    prediction_input = SimpleNamespace(content_identity="e" * 64, to_rows=lambda: [row])
    terminal_support = SimpleNamespace(identity="f" * 64, key_count=1)
    capsule_payload = {
        "artifact_identity": "1" * 64,
        "terminal_metadata_identity": "2" * 64,
        "keys": [target_key],
        "decision_time_count": 1,
    }
    binding = {
        "g0_identity": identity.identity,
        "terminal_metadata_identity": "2" * 64,
        "capsule_file_sha256": "8" * 64,
        "capsule_identity": capsule_payload["artifact_identity"],
        "register_file_sha256": "8" * 64,
        "terminal_input_file_sha256": "8" * 64,
        "register_identity": "6" * 64,
    }
    terminal_slot = "family:1:TERMINAL_FORMER_HOLDOUT"
    journal = CreateOnlyAttemptJournal(tmp_path)
    attempt = AttemptIdentity(
        release_identity=RUNTIME_VERSION,
        g0_identity=identity.identity,
        output_root=str(tmp_path.resolve()),
        slot_id=terminal_slot,
        family_id="family",
        seed=1,
        stage="TERMINAL_FORMER_HOLDOUT",
        attempt=0,
        mode=attempt_mode,
    )
    attempt_payload = {"prediction_input_identity": prediction_input.content_identity}
    journal.append(attempt, "STARTED", attempt_payload)
    journal.append(attempt, attempt_status, attempt_payload)
    if open_extra:
        open_attempt = AttemptIdentity(
            release_identity=RUNTIME_VERSION,
            g0_identity=identity.identity,
            output_root=str(tmp_path.resolve()),
            slot_id=terminal_slot,
            family_id="family",
            seed=1,
            stage="TERMINAL_FORMER_HOLDOUT",
            attempt=1,
            mode="SMOKE",
        )
        journal.append(open_attempt, "STARTED", attempt_payload)

    def read_json(path: Path) -> dict[str, Any]:
        if path.name == "terminal-support-binding.json":
            return binding
        if path.name == "terminal-support-canonical.json":
            return capsule_payload
        if path.name == "development-metrics.json":
            return {"metric_identity": "7" * 64}
        raise AssertionError(f"unexpected JSON read: {path}")

    monkeypatch.setattr(execution_module, "_load_real_context", lambda *_a, **_k: context)
    monkeypatch.setattr(
        execution_module,
        "_authenticated_dev_control_training_rows",
        lambda *_a, **_k: pl.DataFrame(),
    )
    monkeypatch.setattr(
        foundation_module,
        "_seal_dev_control_training",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(execution_module, "_read_json", read_json)
    monkeypatch.setattr(execution_module, "_file_digest", lambda _path: "8" * 64)
    monkeypatch.setattr(
        execution_module, "_validate_terminal_capsule_payload", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_payload",
        lambda *_a, **_k: {
            "attempts_file_sha256": "8" * 64,
            "outcomes_loaded": False,
            "outcome_blind": True,
        },
    )
    monkeypatch.setattr(execution_module, "_development_journal_identity", lambda _root: "8" * 64)
    monkeypatch.setattr(execution_module, "verify_attempt_bundle", lambda *_a, **_k: {"seal": {}})
    monkeypatch.setattr(execution_module, "training_preprocessor_identity", lambda _p: "9" * 64)
    monkeypatch.setattr(
        execution_module, "_terminal_rows_from_input", lambda *_a, **_k: pl.DataFrame([row])
    )
    monkeypatch.setattr(
        execution_module, "_terminal_history_rows", lambda *_a, **_k: pl.DataFrame([row])
    )
    monkeypatch.setattr(
        execution_module,
        "reconstruct_authenticated_linear_forecasts",
        lambda *_a, **_k: {
            "TERMINAL_FORMER_HOLDOUT": {
                "identity": "a" * 64,
                "keys": (target_key,),
                "controls": {"FULLY_POOLED_LOCAL_RIDGE": [0.0]},
                "support_identity": terminal_support.identity,
            }
        },
    )
    monkeypatch.setattr(graph_module, "build_fixed_economic_graph", lambda: object())
    monkeypatch.setattr(
        terminal_support_module.TerminalSupportConfig,
        "from_authenticated_parent",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        terminal_support_module,
        "build_terminal_support",
        lambda *_a, **_k: SimpleNamespace(to_dict=lambda: capsule_payload),
    )
    monkeypatch.setattr(
        foundation_module,
        "authenticate_terminal_metadata",
        lambda **_k: SimpleNamespace(
            content_identity="2" * 64,
            to_rows=lambda: [{"decision_time": decision_time, "target_valid": True}],
        ),
    )
    monkeypatch.setattr(foundation_module, "terminal_history_bindings", lambda *_a, **_k: {})
    monkeypatch.setattr(
        foundation_module, "authenticate_terminal_prediction_input", lambda **_k: prediction_input
    )
    monkeypatch.setattr(
        tensor_module,
        "candidate_independent_support",
        lambda *_a, **_k: terminal_support,
    )

    def streaming_training(_cls: object, **kwargs: Any) -> object:
        assert kwargs["stream"] is True
        assert kwargs["tensors"] is context["tensors"]
        assert (
            runtime_module._stream_provider_tensor(kwargs["tensors"], decision_time.isoformat(), {})
            is tensor
        )
        if error_match == "streaming provider checked":
            raise ValueError("streaming provider checked")
        return SimpleNamespace(content_identity="0" * 64)

    monkeypatch.setattr(
        runtime_module.ResidualTrainingBatch,
        "from_authenticated_oof",
        classmethod(streaming_training),
    )
    outcome_calls: list[str] = []
    monkeypatch.setattr(
        foundation_module,
        "_target_frame",
        lambda *_a, **_k: outcome_calls.append("outcomes") or pl.DataFrame(),
    )

    with pytest.raises(ValueError, match=error_match):
        execution_module.aggregate_authenticated_metrics(
            tmp_path, None, cast(G0ExecutionIdentity, identity)
        )
    assert outcome_calls == []


@pytest.mark.parametrize(
    ("binding", "error_match"),
    [
        ({"g0_identity": "0" * 64}, "execution binding G0 identity drifted"),
        (
            {
                "g0_identity": "a" * 64,
                "manifest_sha256": "0" * 64,
                "manifest_identity": "0" * 64,
                "manifest_path": "/tmp/manifest.json",
            },
            "frozen manifest",
        ),
    ],
)
def test_authenticated_parent_gate_rejects_manifest_and_g0_drift_before_parent_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    binding: dict[str, str],
    error_match: str,
) -> None:
    from types import SimpleNamespace

    import experiments.r4_residual_graph.execution as execution_module
    import experiments.r4_residual_graph.foundation as foundation_module

    identity = SimpleNamespace(identity="a" * 64)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "execution-input.json").write_text(json.dumps(binding), encoding="utf-8")

    def fail_parent(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("parent manifest read must be gated")

    monkeypatch.setattr(execution_module, "_require_identity", lambda *_a, **_k: identity)
    monkeypatch.setattr(foundation_module, "authenticate_parent", fail_parent)

    with pytest.raises(ValueError, match=error_match):
        execution_module._load_authenticated_parent_context(tmp_path)


def test_development_register_gate_rejects_missing_state_before_outcome_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    import experiments.r4_residual_graph.execution as execution_module

    def fail_context(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("missing register must fail before context loading")

    monkeypatch.setattr(execution_module, "_load_real_context", fail_context)
    with pytest.raises(FileNotFoundError, match=r"development-register\.json"):
        execution_module._validate_development_register_metadata(
            tmp_path, cast(G0ExecutionIdentity, SimpleNamespace(identity="a" * 64))
        )


@pytest.mark.parametrize("nested", [False, True])
def test_execution_evidence_reader_rejects_duplicate_fields(tmp_path: Path, nested: bool) -> None:
    path = tmp_path / "evidence.json"
    path.write_text('{"field":{"a":1,"a":2}}' if nested else '{"a":1,"a":2}')
    with pytest.raises(ValueError, match="duplicate"):
        execution_module._read_json(path)


def _write_attempt_ledger(
    root: Path,
    *,
    slot: str,
    config: FrozenRuntimeConfig,
    output_identity: str,
    statuses: tuple[tuple[int, JournalStatus], ...],
) -> Path:
    del output_identity
    family_id, seed_text, stage = slot.split(":", 2)
    journal = CreateOnlyAttemptJournal(root)
    for attempt_number, status in statuses:
        attempt = AttemptIdentity(
            release_identity=RUNTIME_VERSION,
            g0_identity="d" * 64,
            output_root=str(root.resolve()),
            slot_id=slot,
            family_id=family_id,
            seed=int(seed_text),
            stage=stage,
            attempt=attempt_number,
            mode="PRIMARY",
        )
        if status == "STARTED":
            attempt_payload = cast(
                dict[str, object],
                {
                    "config_identity": config.identity,
                    "foundation_content_identity": "e" * 64,
                },
            )
        elif status == "FAILED":
            attempt_payload = {"error": "failed"}
        else:
            attempt_payload = {"seal_identity": "f" * 64}
        journal.append(attempt, status, attempt_payload)
    return journal.path


def test_development_attempt_journal_replays_direct_success(tmp_path: Path) -> None:
    slot = f"{FITTED_FAMILY_IDS[0]}:{PRIMARY_SEEDS[0]}:DEV_2"
    config = FrozenRuntimeConfig()
    output_identity = _sha256({"root": str(tmp_path.resolve()), "policy": OUTPUT_POLICY})
    journal_path = _write_attempt_ledger(
        tmp_path,
        slot=slot,
        config=config,
        output_identity=output_identity,
        statuses=((0, "STARTED"), (0, "SUCCEEDED")),
    )
    result = _validate_development_attempt_ledger(
        journal_path,
        expected_slots=(slot,),
        runtime_config=config,
        foundation_content_identity="e" * 64,
        output_identity=output_identity,
        canonical_output_root=tmp_path,
    )
    assert result[slot].attempt == 0


def test_development_attempt_journal_replays_authorised_retry(tmp_path: Path) -> None:
    slot = f"{FITTED_FAMILY_IDS[0]}:{PRIMARY_SEEDS[0]}:DEV_2"
    config = FrozenRuntimeConfig(retry_budget=1)
    output_identity = _sha256({"root": str(tmp_path.resolve()), "policy": OUTPUT_POLICY})
    journal_path = _write_attempt_ledger(
        tmp_path,
        slot=slot,
        config=config,
        output_identity=output_identity,
        statuses=((0, "STARTED"), (0, "FAILED"), (1, "STARTED"), (1, "SUCCEEDED")),
    )
    result = _validate_development_attempt_ledger(
        journal_path,
        expected_slots=(slot,),
        runtime_config=config,
        foundation_content_identity="e" * 64,
        output_identity=output_identity,
        canonical_output_root=tmp_path,
    )
    assert result[slot].attempt == 1


@pytest.mark.parametrize("statuses", [((0, "STARTED"),), ((0, "STARTED"), (0, "FAILED"))])
def test_development_attempt_journal_rejects_unclosed_lifecycle(
    tmp_path: Path, statuses: tuple[tuple[int, JournalStatus], ...]
) -> None:
    slot = f"{FITTED_FAMILY_IDS[0]}:{PRIMARY_SEEDS[0]}:DEV_2"
    config = FrozenRuntimeConfig(retry_budget=1)
    output_identity = _sha256({"root": str(tmp_path.resolve()), "policy": OUTPUT_POLICY})
    journal_path = _write_attempt_ledger(
        tmp_path,
        slot=slot,
        config=config,
        output_identity=output_identity,
        statuses=statuses,
    )
    with pytest.raises(ValueError, match="lifecycle"):
        _validate_development_attempt_ledger(
            journal_path,
            expected_slots=(slot,),
            runtime_config=config,
            foundation_content_identity="e" * 64,
            output_identity=output_identity,
            canonical_output_root=tmp_path,
        )


def test_development_attempt_journal_rejects_extra_slot(tmp_path: Path) -> None:
    slot = f"{FITTED_FAMILY_IDS[0]}:{PRIMARY_SEEDS[0]}:DEV_2"
    extra = f"{FITTED_FAMILY_IDS[0]}:{PRIMARY_SEEDS[0]}:DEV_3"
    config = FrozenRuntimeConfig()
    output_identity = _sha256({"root": str(tmp_path.resolve()), "policy": OUTPUT_POLICY})
    journal_path = _write_attempt_ledger(
        tmp_path,
        slot=slot,
        config=config,
        output_identity=output_identity,
        statuses=((0, "STARTED"), (0, "SUCCEEDED")),
    )
    _write_attempt_ledger(
        tmp_path,
        slot=extra,
        config=config,
        output_identity=output_identity,
        statuses=((0, "STARTED"), (0, "SUCCEEDED")),
    )
    with pytest.raises(ValueError, match="unexpected"):
        _validate_development_attempt_ledger(
            journal_path,
            expected_slots=(slot,),
            runtime_config=config,
            foundation_content_identity="e" * 64,
            output_identity=output_identity,
            canonical_output_root=tmp_path,
        )


def test_development_attempt_journal_rejects_forged_record(tmp_path: Path) -> None:
    slot = f"{FITTED_FAMILY_IDS[0]}:{PRIMARY_SEEDS[0]}:DEV_2"
    config = FrozenRuntimeConfig()
    output_identity = _sha256({"root": str(tmp_path.resolve()), "policy": OUTPUT_POLICY})
    journal_path = _write_attempt_ledger(
        tmp_path,
        slot=slot,
        config=config,
        output_identity=output_identity,
        statuses=((0, "STARTED"), (0, "SUCCEEDED")),
    )
    started_path = next(journal_path.glob("*.STARTED.json"))
    forged = json.loads(started_path.read_text())
    forged["payload"]["config_identity"] = "0" * 64
    started_path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="schema or hash"):
        _validate_development_attempt_ledger(
            journal_path,
            expected_slots=(slot,),
            runtime_config=config,
            foundation_content_identity="e" * 64,
            output_identity=output_identity,
            canonical_output_root=tmp_path,
        )


def test_report_ledger_disposition_preserves_retry_and_failure_history() -> None:
    records = [
        {
            "slot_id": "FAMILY:17:DEV_2",
            "attempt": 0,
            "status": "STARTED",
            "mode": "PRIMARY",
            "stage": "DEV_2",
        },
        {
            "slot_id": "FAMILY:17:DEV_2",
            "attempt": 0,
            "status": "FAILED",
            "mode": "PRIMARY",
            "stage": "DEV_2",
            "error": "synthetic failure",
        },
        {
            "slot_id": "FAMILY:17:DEV_2",
            "attempt": 1,
            "status": "STARTED",
            "mode": "PRIMARY",
            "stage": "DEV_2",
        },
        {
            "slot_id": "FAMILY:17:DEV_2",
            "attempt": 1,
            "status": "SUCCEEDED",
            "mode": "PRIMARY",
            "stage": "DEV_2",
        },
    ]
    disposition = execution_module._ledger_dispositions(records)
    assert len(disposition) == 1
    assert disposition[0]["final_status"] == "SUCCEEDED"
    assert disposition[0]["disposition"] == "SUCCEEDED"
    assert [item["status"] for item in disposition[0]["attempts"]] == [
        "STARTED",
        "FAILED",
        "STARTED",
        "SUCCEEDED",
    ]
    assert disposition[0]["attempts"][1]["error"] == "synthetic failure"


def test_outcome_blind_failure_report_is_create_only(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    report_identity = execution_module.write_outcome_blind_closure_report(
        tmp_path,
        status="RUN_INVALIDATED_IMPLEMENTATION",
        reason="synthetic invariant defect",
        identity=identity,
    )
    assert len(report_identity) == 64
    payload = json.loads(next((tmp_path / "result").glob("closure-report-*.json")).read_text())
    assert payload["status"] == "RUN_INVALIDATED_IMPLEMENTATION"
    assert payload["outcomes_loaded"] is False
    assert payload["outcome_blind"] is True
    assert payload["diagnostics"]["scientific_performance"] == "NOT_COMPUTED"
    assert "target_return" not in json.dumps(payload)
    second_identity = execution_module.write_outcome_blind_closure_report(
        tmp_path,
        status="RUN_INVALIDATED_IMPLEMENTATION",
        reason="second closure",
        identity=identity,
    )
    assert second_identity != report_identity


def test_closure_reports_are_create_only_and_directly_sequenced(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    first = execution_module.write_outcome_blind_closure_report(
        tmp_path, status="RUN_FAILED", reason="first", identity=identity
    )
    second = execution_module.write_outcome_blind_closure_report(
        tmp_path, status="RUN_FAILED", reason="second", identity=identity
    )
    reports = sorted((tmp_path / "result").glob("closure-report-*.json"))
    assert first != second
    assert [json.loads(path.read_text())["closure_sequence"] for path in reports] == [1, 2]
    assert not (tmp_path / "result" / "closure-index.jsonl").exists()
    assert execution_module._closure_report_count(tmp_path) == 2


def test_closure_reports_reject_missing_sequence_and_tampering(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    execution_module.write_outcome_blind_closure_report(
        tmp_path, status="RUN_FAILED", reason="first", identity=identity
    )
    report_path = next((tmp_path / "result").glob("closure-report-*.json"))
    report = json.loads(report_path.read_text())
    report["reason"] = "tampered"
    report_path.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")))
    with pytest.raises(ValueError, match=r"contradictory|content identity"):
        execution_module.write_outcome_blind_closure_report(
            tmp_path, status="RUN_FAILED", reason="blocked", identity=identity
        )


def test_closure_reports_reject_noncanonical_extra_report(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    execution_module.write_outcome_blind_closure_report(
        tmp_path, status="RUN_FAILED", reason="first", identity=identity
    )
    (tmp_path / "result" / "closure-report-extra.json").write_text("{}")
    with pytest.raises(ValueError, match="sequence"):
        execution_module.write_outcome_blind_closure_report(
            tmp_path, status="RUN_FAILED", reason="blocked", identity=identity
        )


def test_closure_report_path_grammar_and_identity_binding(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    execution_module.write_outcome_blind_closure_report(
        tmp_path, status="RUN_FAILED", reason="path grammar", identity=identity
    )
    report_path = next((tmp_path / "result").glob("closure-report-*.json"))
    report = json.loads(report_path.read_text())
    assert report["closure_sequence"] == 1
    assert (
        execution_module._canonical_closure_report_path(tmp_path, report["report_path"], 1)
        == report_path.resolve()
    )
    invalid = report["report_path"].replace("000001", "000002")
    with pytest.raises(ValueError):
        execution_module._canonical_closure_report_path(tmp_path, invalid, 1)


def test_closure_reports_reject_symlinked_report(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    execution_module.write_outcome_blind_closure_report(
        tmp_path, status="RUN_FAILED", reason="seed", identity=identity
    )
    report_path = next((tmp_path / "result").glob("closure-report-*.json"))
    symlink_path = tmp_path / "result" / ("closure-report-000002-" + "a" * 64 + ".json")
    symlink_path.symlink_to(report_path.name)
    with pytest.raises(ValueError, match="regular file"):
        execution_module.write_outcome_blind_closure_report(
            tmp_path, status="RUN_FAILED", reason="blocked", identity=identity
        )


def test_ledger_dispositions_support_multiple_retries_and_open_tail() -> None:
    def record(attempt: int, status: str, error: str | None = None) -> dict[str, object]:
        return {
            "slot_id": "FAMILY:17:DEV_2",
            "attempt": attempt,
            "status": status,
            "mode": "PRIMARY",
            "stage": "DEV_2",
            "error": error,
        }

    records = [
        record(0, "STARTED"),
        record(0, "FAILED", "first failure"),
        record(1, "STARTED"),
        record(1, "FAILED", "second failure"),
        record(2, "STARTED"),
        record(2, "SUCCEEDED"),
    ]
    disposition = execution_module._ledger_dispositions(records)[0]
    assert disposition["final_status"] == "SUCCEEDED"
    assert disposition["disposition"] == "SUCCEEDED"
    assert [item["attempt"] for item in disposition["attempts"]] == [0, 0, 1, 1, 2, 2]

    open_disposition = execution_module._ledger_dispositions(records[:-1])[0]
    assert open_disposition["final_status"] == "STARTED"
    assert open_disposition["disposition"] == "OPEN"


@pytest.mark.parametrize(
    "status",
    (
        "HYPOTHESIS_NOMINATED",
        "NO_HYPOTHESIS_NOMINATED",
        "RUN_INVALIDATED_IMPLEMENTATION",
        "RUN_FAILED",
    ),
)
def test_closure_report_accepts_exact_final_status_vocabulary(tmp_path: Path, status: str) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    execution_module.write_outcome_blind_closure_report(
        tmp_path, status=status, reason="status vocabulary", identity=identity
    )
    report = json.loads(next((tmp_path / "result").glob("closure-report-*.json")).read_text())
    assert report["status"] == status
    assert report["evidence_class"] == "POST_HOC_HISTORICAL_EXPLORATORY"


def test_invalid_current_closure_report_does_not_create_bytes(tmp_path: Path) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    with pytest.raises(ValueError, match="unsupported outcome-blind closure status"):
        execution_module.write_outcome_blind_closure_report(
            tmp_path, status="EXPLORATORY_NON_DECISION_GRADE", reason="invalid", identity=identity
        )
    result_dir = tmp_path / "result"
    assert not list(result_dir.glob("closure-report-*.json"))
    assert not (result_dir / "closure-index.jsonl").exists()


def test_prediction_pre_attempt_failure_is_retained_and_reraised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    attempts_path = CreateOnlyAttemptJournal(tmp_path).path
    before_attempts = sorted(attempts_path.glob("*.json")) if attempts_path.exists() else []

    def fail(*_args: object, **_kwargs: object) -> str:
        raise ValueError("capsule artifact unavailable")

    monkeypatch.setattr(execution_module, "_predict_authenticated_terminal_slot_impl", fail)
    with pytest.raises(ValueError, match="capsule artifact unavailable"):
        execution_module.predict_authenticated_terminal_slot(
            tmp_path, FITTED_FAMILY_IDS[0], PRIMARY_SEEDS[0], None, identity
        )
    assert (
        sorted(attempts_path.glob("*.json")) if attempts_path.exists() else []
    ) == before_attempts
    report_path = next((tmp_path / "result").glob("closure-report-*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["phase"] == "TERMINAL_PRE_ATTEMPT_VALIDATION"
    assert report["outcomes_loaded"] is False
    assert report["status"] == "RUN_FAILED"
    assert len(list((tmp_path / "result").glob("closure-report-*.json"))) == 1


@pytest.mark.parametrize(
    ("operation", "exception_type"),
    (
        ("close", KeyError),
        ("close", TypeError),
        ("close", OSError),
        ("materialise", KeyError),
        ("materialise", TypeError),
        ("materialise", OSError),
    ),
)
def test_authenticated_boundaries_retain_and_reraise_original_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    exception_type: type[Exception],
) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    original = exception_type("boundary failure")

    def fail(*_args: object, **_kwargs: object) -> str:
        raise original

    target = (
        "_close_authenticated_register_impl"
        if operation == "close"
        else "_materialise_authenticated_terminal_support_impl"
    )
    monkeypatch.setattr(execution_module, target, fail)
    if operation == "close":

        def invoke() -> str:
            return execution_module.close_authenticated_register(tmp_path, identity)
    else:

        def invoke() -> str:
            return execution_module.materialise_authenticated_terminal_support(
                tmp_path, None, identity
            )

    with pytest.raises(exception_type, match="boundary failure") as caught:
        invoke()
    assert caught.value is original
    report_path = next((tmp_path / "result").glob("closure-report-*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "RUN_FAILED"
    assert report["exception"]["classification"] == "RUN_FAILED"
    assert len(list((tmp_path / "result").glob("closure-report-*.json"))) == 1


@pytest.mark.parametrize(
    ("exception", "expected"),
    (
        (ValueError("identity artifact unavailable"), "RUN_FAILED"),
        (AssertionError("identity artifact unavailable"), "RUN_FAILED"),
        (OSError("identity artifact unavailable"), "RUN_FAILED"),
        (FileNotFoundError("identity artifact unavailable"), "RUN_FAILED"),
        (
            execution_module._ImplementationInvariantError("proven implementation defect"),
            "RUN_INVALIDATED_IMPLEMENTATION",
        ),
    ),
)
def test_failure_status_requires_explicit_implementation_defect(
    exception: BaseException, expected: str
) -> None:
    assert execution_module._failure_status(exception) == expected


def test_aggregate_post_load_failure_retains_post_outcome_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    captured: list[dict[str, object]] = []

    def fail_after_load(*_args: object, **_kwargs: object) -> str:
        execution_module._aggregate_outcomes_loaded.set(True)
        raise RuntimeError("failure after target frame load")

    def retain(*_args: object, **kwargs: object) -> str:
        captured.append(kwargs)
        return "retained"

    monkeypatch.setattr(execution_module, "_aggregate_authenticated_metrics_impl", fail_after_load)
    monkeypatch.setattr(execution_module, "write_outcome_blind_closure_report", retain)
    with pytest.raises(RuntimeError, match="failure after target frame load"):
        execution_module.aggregate_authenticated_metrics(tmp_path, None, identity)
    assert captured[0]["phase"] == "TERMINAL_POST_OUTCOME_EVALUATION"
    assert captured[0]["outcomes_loaded"] is True


def test_closure_report_rejects_supplied_config_drift_before_create(tmp_path: Path) -> None:
    config = FrozenRuntimeConfig(retry_budget=5)
    identity = _fixture_prepare_execution(tmp_path, config=config)
    execution_module.write_outcome_blind_closure_report(
        tmp_path, status="RUN_FAILED", reason="config five", identity=identity, config=config
    )
    before = tuple((tmp_path / "result").glob("closure-report-*.json"))
    with pytest.raises(ValueError):
        execution_module.write_outcome_blind_closure_report(
            tmp_path,
            status="RUN_FAILED",
            reason="config default drift",
            identity=identity,
            config=FrozenRuntimeConfig(),
        )
    assert tuple((tmp_path / "result").glob("closure-report-*.json")) == before


def test_aggregate_retention_failure_adds_note_without_replacing_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _fixture_prepare_execution(tmp_path)
    original = RuntimeError("original aggregation failure")

    def fail(*_args: object, **_kwargs: object) -> str:
        raise original

    def fail_retention(*_args: object, **_kwargs: object) -> str:
        raise OSError("retention unavailable")

    monkeypatch.setattr(execution_module, "_aggregate_authenticated_metrics_impl", fail)
    monkeypatch.setattr(execution_module, "write_outcome_blind_closure_report", fail_retention)
    with pytest.raises(RuntimeError, match="original aggregation failure") as caught:
        execution_module.aggregate_authenticated_metrics(tmp_path, None, identity)
    assert caught.value is original
    assert any("retention unavailable" in note for note in caught.value.__notes__)


@pytest.mark.parametrize("identity_state", ["missing", "drifted"])
@pytest.mark.parametrize(
    ("wrapper", "phase"),
    [
        ("run", "DEVELOPMENT_PRE_ATTEMPT_VALIDATION"),
        ("close", "DEVELOPMENT_PRE_ATTEMPT_VALIDATION"),
        ("support", "TERMINAL_PRE_ATTEMPT_VALIDATION"),
        ("predict", "TERMINAL_PRE_ATTEMPT_VALIDATION"),
        ("aggregate", "TERMINAL_PRE_ATTEMPT_VALIDATION"),
    ],
)
def test_public_wrappers_retain_missing_or_drifted_g0_identity_failures(
    tmp_path: Path,
    identity_state: str,
    wrapper: str,
    phase: str,
) -> None:
    _fixture_prepare_execution(tmp_path)
    identity_path = tmp_path / "config" / "g0-identity.json"
    if identity_state == "missing":
        identity_path.unlink()
        expected_type: type[BaseException] = FileNotFoundError
        expected_message = f"required execution artifact is missing: {identity_path}"
    else:
        recorded = cast(dict[str, Any], json.loads(identity_path.read_text(encoding="utf-8")))
        recorded["identity"] = "0" * 64
        identity_path.write_text(json.dumps(recorded), encoding="utf-8")
        expected_type = ValueError
        expected_message = "G0 identity drift detected"

    def invoke() -> object:
        if wrapper == "run":
            return run_development_slots(tmp_path)
        if wrapper == "close":
            return execution_module.close_development_register(tmp_path)
        if wrapper == "support":
            return execution_module.materialise_terminal_support_stage(tmp_path)
        if wrapper == "predict":
            return execution_module.predict_terminal_slot(
                tmp_path, family_id=FITTED_FAMILY_IDS[0], seed=PRIMARY_SEEDS[0]
            )
        return execution_module.aggregate_metrics(tmp_path)

    with pytest.raises(expected_type) as raised:
        invoke()
    assert str(raised.value) == expected_message
    reports = sorted((tmp_path / "result").glob("closure-report-*.json"))
    assert len(reports) == 1
    report = cast(dict[str, Any], json.loads(reports[0].read_text(encoding="utf-8")))
    assert report["status"] == "RUN_FAILED"
    assert report["phase"] == phase
    assert report["outcomes_loaded"] is False
    assert report["provenance"]["g0_identity"] is None
    assert all(not ledger["dispositions"] for ledger in report["attempt_ledgers"].values())
    attempts_path = CreateOnlyAttemptJournal(tmp_path).path
    assert not attempts_path.exists() or not list(attempts_path.glob("*.json"))
    assert len(list((tmp_path / "result").glob("closure-report-*.json"))) == 1


@pytest.mark.parametrize("kind", ["symlink", "non_directory"])
def test_closure_writer_rejects_result_directory_boundary(tmp_path: Path, kind: str) -> None:
    result = tmp_path / "result"
    if kind == "symlink":
        target = tmp_path / "outside"
        target.mkdir()
        result.symlink_to(target, target_is_directory=True)
    else:
        result.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="result directory"):
        execution_module.write_outcome_blind_closure_report(
            tmp_path, status="RUN_FAILED", reason="path boundary", phase="BOUNDARY"
        )
    assert not (result / "closure-index.jsonl").exists()


def test_closure_writer_rejects_report_path_collision_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = execution_module._write_json_once
    raced: list[Path] = []

    def race(path: Path, payload: dict[str, Any]) -> None:
        if path.name.startswith("closure-report-"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"raced\n")
            raced.append(path)
        original_write(path, payload)

    monkeypatch.setattr(execution_module, "_write_json_once", race)
    with pytest.raises(FileExistsError):
        execution_module.write_outcome_blind_closure_report(
            tmp_path, status="RUN_FAILED", reason="report race", phase="BOUNDARY"
        )
    assert len(raced) == 1
    assert raced[0].read_bytes() == b"raced\n"
    assert not (tmp_path / "result" / "closure-index.jsonl").exists()


def test_terminal_fit_failure_preserves_original_when_failed_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    import polars as pl

    import experiments.r4_residual_graph.foundation as foundation_module
    import experiments.r4_residual_graph.graph as graph_module
    import experiments.r4_residual_graph.runtime as runtime_module
    import experiments.r4_residual_graph.tensor as tensor_module
    import experiments.r4_residual_graph.terminal_support as terminal_support_module

    identity = _fixture_prepare_execution(tmp_path)
    terminal_time = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    allowed_columns = tensor_module._SUPPORT_ALLOWED_COLUMNS
    bool_columns = {
        "gap_known_by_cutoff",
        "quality_healthy",
        "return_300s_available",
        "return_60s_available",
        "source_active",
        "target_valid",
    }

    def make_row() -> dict[str, object]:
        row: dict[str, object] = {}
        for column in allowed_columns:
            if column == "instrument_id":
                row[column] = "EURUSD"
            elif column == "block":
                row[column] = "TERMINAL_FORMER_HOLDOUT"
            elif column in {
                "decision_time",
                "feature_data_asof",
                "feature_available_at",
                "target_available_at",
            }:
                row[column] = terminal_time
            elif column in bool_columns:
                row[column] = True
            else:
                row[column] = 0.0
        return row

    row = make_row()
    metadata = SimpleNamespace(
        content_identity="m" * 64,
        to_rows=lambda: [{"decision_time": terminal_time, "target_valid": True}],
    )
    prediction_input = SimpleNamespace(content_identity="p" * 64, to_rows=lambda: [row])
    capsule_payload = {
        "artifact_identity": "c" * 64,
        "keys": [f"{terminal_time.isoformat()}|EURUSD"],
        "decision_time_count": 1,
    }
    terminal_binding = {
        "g0_identity": identity.identity,
        "terminal_metadata_identity": metadata.content_identity,
        "capsule_file_sha256": "d" * 64,
        "capsule_identity": capsule_payload["artifact_identity"],
        "register_file_sha256": "d" * 64,
        "terminal_input_file_sha256": "d" * 64,
        "register_identity": "r" * 64,
    }
    parent = object()
    foundation = SimpleNamespace(content_identity="f" * 64, ordered_row_keys=())
    context = {
        "current_identity": identity,
        "parent": parent,
        "foundation": foundation,
        "support": object(),
        "preprocessor": object(),
        "tensors": {},
    }
    support = SimpleNamespace(
        keys=(terminal_time.isoformat(),),
        key_count=1,
        target_keys=(),
        identity="s" * 64,
        row_content_identity="q" * 64,
    )

    monkeypatch.setattr(
        execution_module, "_load_authenticated_parent_context", lambda *_args: context
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_metadata",
        lambda *_args, **_kwargs: {
            "register_identity": "r" * 64,
            "attempts_file_sha256": "d" * 64,
            "outcomes_loaded": False,
            "outcome_blind": True,
        },
    )
    original_read_json = execution_module._read_json
    monkeypatch.setattr(
        execution_module,
        "_read_json",
        lambda path: (
            capsule_payload
            if path.name == "terminal-support-canonical.json"
            else (
                terminal_binding
                if path.name == "terminal-support-binding.json"
                else original_read_json(path)
            )
        ),
    )
    monkeypatch.setattr(execution_module, "_file_digest", lambda _path: "d" * 64)
    monkeypatch.setattr(execution_module, "_development_journal_identity", lambda _root: "d" * 64)
    monkeypatch.setattr(graph_module, "build_fixed_economic_graph", lambda: object())
    monkeypatch.setattr(
        terminal_support_module.TerminalSupportConfig,
        "from_authenticated_parent",
        classmethod(lambda cls, *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(
        terminal_support_module,
        "build_terminal_support",
        lambda *_args, **_kwargs: SimpleNamespace(to_dict=lambda: capsule_payload),
    )
    monkeypatch.setattr(
        execution_module, "_validate_terminal_capsule_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        foundation_module, "authenticate_terminal_metadata", lambda **_kwargs: metadata
    )
    monkeypatch.setattr(
        foundation_module, "terminal_history_bindings", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        execution_module, "_terminal_rows_from_input", lambda *_args: pl.DataFrame([row])
    )
    monkeypatch.setattr(execution_module, "_load_real_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_payload",
        lambda *_args, **_kwargs: {"register_identity": "r" * 64},
    )
    monkeypatch.setattr(
        execution_module, "_authenticated_dev_control_training_rows", lambda *_args: ()
    )
    monkeypatch.setattr(foundation_module, "_seal_dev_control_training", lambda *_args: object())
    monkeypatch.setattr(
        foundation_module,
        "authenticate_terminal_prediction_input",
        lambda **_kwargs: prediction_input,
    )
    monkeypatch.setattr(
        execution_module, "_terminal_history_rows", lambda *_args: pl.DataFrame([row])
    )
    monkeypatch.setattr(
        tensor_module, "candidate_independent_support", lambda *_args, **_kwargs: support
    )
    monkeypatch.setattr(execution_module, "_build_tensors", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        execution_module,
        "reconstruct_authenticated_linear_forecasts",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(execution_module, "training_preprocessor_identity", lambda _value: "t" * 64)
    prediction_batch_kwargs: list[dict[str, object]] = []
    training_batch_kwargs: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime_module,
        "PredictionBatch",
        SimpleNamespace(
            from_authenticated_support=lambda **kwargs: (
                prediction_batch_kwargs.append(kwargs) or object()
            )
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "ResidualTrainingBatch",
        SimpleNamespace(
            from_authenticated_oof=lambda **kwargs: training_batch_kwargs.append(kwargs) or object()
        ),
    )

    class FakeModel:
        def to(self, _device: object) -> FakeModel:
            return self

    monkeypatch.setattr(
        execution_module, "_build_real_family_model", lambda _family_id: FakeModel()
    )
    monkeypatch.setattr(execution_module, "require_cuda", lambda: "cpu")
    monkeypatch.setattr(
        runtime_module,
        "fit_one_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model fit failed")),
    )
    original_append = CreateOnlyAttemptJournal.append

    def fail_failed_persistence(
        self: CreateOnlyAttemptJournal,
        attempt: AttemptIdentity,
        status: JournalStatus,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if status == "FAILED":
            raise OSError("terminal ledger unavailable")
        return original_append(self, attempt, status, payload)

    monkeypatch.setattr(CreateOnlyAttemptJournal, "append", fail_failed_persistence)
    with pytest.raises(RuntimeError) as raised:
        execution_module.predict_authenticated_terminal_slot(
            tmp_path, FITTED_FAMILY_IDS[0], PRIMARY_SEEDS[0], None, identity
        )
    assert str(raised.value) == "model fit failed"
    assert any(
        "terminal failure persistence failed: terminal ledger unavailable" in note
        for note in raised.value.__notes__
    )
    assert prediction_batch_kwargs[0]["stream"] is True
    assert training_batch_kwargs[0]["stream"] is True


def _mock_development_runtime(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    identity: G0ExecutionIdentity,
) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    import polars as pl

    import experiments.r4_residual_graph.runtime as runtime_module
    import experiments.r4_residual_graph.stage_cache as stage_cache_module

    terminal_time = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    rows = pl.DataFrame(
        {
            "block": ["DEV_1", "DEV_2", "DEV_3"],
            "decision_time": [terminal_time] * 3,
        }
    )
    development_support = rows
    foundation_rows = pl.DataFrame(
        {
            "block": ["DEV_1"],
            "instrument_id": ["EURUSD"],
            "decision_time": [terminal_time],
            "local_forecast": [0.0],
        }
    )
    foundation = SimpleNamespace(
        content_identity="f" * 64,
        ordered_row_keys=(),
        rows=foundation_rows,
    )
    support = SimpleNamespace(identity="s" * 64, target_keys=())
    control_period = {
        "keys": (),
        "LOCAL_RIDGE": (),
        "FULLY_POOLED_LOCAL_RIDGE": (),
        "identity": "c" * 64,
        "controls": {},
        "control_identities": {},
        "support_identity": support.identity,
    }
    context = {
        "foundation": foundation,
        "support": support,
        "tensors": {},
        "rows": rows,
        "parent": object(),
        "binding": {"development_support_path": str(root / "development.parquet")},
        "current_identity": identity,
    }
    monkeypatch.setattr(execution_module, "_load_real_context", lambda *_a, **_k: context)
    monkeypatch.setattr(pl, "read_parquet", lambda _path: development_support)
    monkeypatch.setattr(
        execution_module,
        "reconstruct_authenticated_linear_forecasts",
        lambda *_a, **_k: {"DEV_2": control_period, "DEV_3": control_period},
    )
    monkeypatch.setattr(
        execution_module,
        "_build_execution_support",
        lambda *_a, **_k: support,
    )
    monkeypatch.setattr(execution_module, "_build_tensors", lambda *_a, **_k: {})
    monkeypatch.setattr(
        execution_module,
        "_build_training_objects",
        lambda *_a, **_k: (None, object(), {}),
    )
    monkeypatch.setattr(execution_module, "training_preprocessor_identity", lambda _value: "t" * 64)
    monkeypatch.setattr(
        execution_module,
        "_build_development_stage_cache",
        lambda _root, _context, _foundation, _support, _tensors, selected, _config, _identity: {
            stage: {
                "preprocessor_identity": "t" * 64,
                "training_batch": SimpleNamespace(content_identity="b" * 64),
                "prediction_batch": object(),
                "eval_support": support,
                "input_identity": "i" * 64,
            }
            for _, _, stage in selected
        },
    )
    cached_training = SimpleNamespace(content_identity="b" * 64)
    cached_prediction = SimpleNamespace(content_identity="q" * 64)
    capsule = {
        "foundation_content_identity": foundation.content_identity,
        "stages": {
            stage: {
                "cache_root": str(root / stage),
                "cache_identity": "v" * 64,
                "semantic_inputs": {},
                "training_batch_identity": cached_training.content_identity,
                "prediction_batch_identity": cached_prediction.content_identity,
                "preprocessor_identity": "t" * 64,
                "prediction_input_identity": "i" * 64,
                "target_keys": [],
                "control_period": control_period,
            }
            for stage in ("DEV_2", "DEV_3")
        },
    }
    monkeypatch.setattr(
        execution_module, "_load_development_execution_capsule", lambda *_a, **_k: capsule
    )
    monkeypatch.setattr(execution_module, "_capsule_cache_input", lambda *_a, **_k: object())
    monkeypatch.setattr(
        stage_cache_module,
        "load_stage_cache_batches",
        lambda *_a, **_k: (
            cached_training,
            cached_prediction,
            SimpleNamespace(cache_identity="v" * 64),
        ),
    )
    monkeypatch.setattr(execution_module, "_build_real_family_model", lambda _family_id: object())
    monkeypatch.setattr(execution_module, "configure_deterministic_cuda", lambda _seed: None)
    monkeypatch.setattr(execution_module, "_persist_real_model", lambda *_a, **_k: None)
    monkeypatch.setattr(
        execution_module,
        "verify_attempt_bundle",
        lambda *_a, **_k: {"seal": {"seal_identity": "s" * 64}},
    )
    monkeypatch.setattr(
        runtime_module,
        "PredictionBatch",
        SimpleNamespace(from_authenticated_support=lambda **_k: object()),
    )
    monkeypatch.setattr(
        runtime_module,
        "ResidualTrainingBatch",
        SimpleNamespace(
            from_authenticated_oof=lambda **_k: SimpleNamespace(content_identity="b" * 64)
        ),
    )
    monkeypatch.setattr(runtime_module, "fit_one_model", lambda *_a, **_k: object())
    monkeypatch.setattr(runtime_module, "predict_residual", lambda *_a, **_k: object())


def test_development_fit_failure_preserves_original_when_failed_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = FrozenRuntimeConfig()
    identity = _fixture_prepare_execution(tmp_path, config=config)
    _mock_development_runtime(monkeypatch, tmp_path, identity)
    import experiments.r4_residual_graph.runtime as runtime_module

    original = RuntimeError("development model fit failed")
    monkeypatch.setattr(
        runtime_module,
        "fit_one_model",
        lambda *_a, **_k: (_ for _ in ()).throw(original),
    )
    original_append = execution_module.CreateOnlyAttemptJournal.append

    def fail_failed_persistence(
        self: Any,
        attempt: Any,
        status: JournalStatus,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        if status == "FAILED":
            raise OSError("development terminal ledger unavailable")
        return original_append(self, attempt, status, payload)

    monkeypatch.setattr(
        execution_module.CreateOnlyAttemptJournal, "append", fail_failed_persistence
    )
    with pytest.raises(RuntimeError) as raised:
        run_development_slots(
            tmp_path,
            slots=(PRIMARY_SCHEDULE[0],),
            config=config,
        )
    assert raised.value is original
    assert str(raised.value) == "development model fit failed"
    assert any(
        "terminal failure persistence failed: development terminal ledger unavailable" in note
        for note in raised.value.__notes__
    )
    records = list((tmp_path / "register" / "attempts").glob("*.json"))
    assert sum(".STARTED.json" in path.name for path in records) == 1
    assert not any(".FAILED.json" in path.name for path in records)
    reports = sorted((tmp_path / "result").glob("closure-report-*.json"))
    assert len(reports) == 1
    report = cast(dict[str, Any], json.loads(reports[0].read_text(encoding="utf-8")))
    assert report["phase"] == "DEVELOPMENT_ATTEMPT"
    assert report["provenance"]["g0_identity"] == identity.to_dict()
    assert report["attempt_ledgers"]["development"]["lifecycle_status"] == "OPEN"


def test_development_attempt_context_resets_before_later_slot_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = FrozenRuntimeConfig()
    identity = _fixture_prepare_execution(tmp_path, config=config)
    _mock_development_runtime(monkeypatch, tmp_path, identity)
    first_slot = PRIMARY_SCHEDULE[0]
    second_slot = next(slot for slot in reversed(PRIMARY_SCHEDULE) if slot[2] == "DEV_3")
    original = RuntimeError("second slot start failed")
    original_append = execution_module.CreateOnlyAttemptJournal.append
    starts = 0

    def fail_second_start(
        self: Any,
        attempt: Any,
        status: JournalStatus,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        nonlocal starts
        if status == "STARTED":
            if starts == 1:
                raise original
            starts += 1
        return original_append(self, attempt, status, payload)

    monkeypatch.setattr(execution_module.CreateOnlyAttemptJournal, "append", fail_second_start)
    with pytest.raises(RuntimeError) as raised:
        run_development_slots(
            tmp_path,
            slots=(first_slot, second_slot),
            config=config,
        )
    assert raised.value is original
    assert str(raised.value) == "second slot start failed"
    ledger_text = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "register" / "attempts").glob("*.json")
    )
    assert f"{first_slot[0]}:{first_slot[1]}:{first_slot[2]}" in ledger_text
    assert f"{second_slot[0]}:{second_slot[1]}:{second_slot[2]}" not in ledger_text
    reports = sorted((tmp_path / "result").glob("closure-report-*.json"))
    assert len(reports) == 1
    report = cast(dict[str, Any], json.loads(reports[0].read_text(encoding="utf-8")))
    assert report["phase"] == "DEVELOPMENT_PRE_ATTEMPT_VALIDATION"
    assert report["provenance"]["g0_identity"] == identity.to_dict()
    assert report["attempt_ledgers"]["development"]["lifecycle_status"] != "OPEN"


def test_authenticated_fit_evidence_rejects_tamper_and_requires_exact_coverage() -> None:
    valid = {
        "elapsed_seconds": 0.5,
        "parameter_count": 10,
        "epochs": 2,
        "target_instruments": 20,
        "equal_instrument_loss": True,
        "training_batch_identity": "a" * 64,
    }
    slot_id = "LOCAL_TEMPORAL_RESIDUAL:17:DEV_2"
    assert _validate_fit_evidence(valid, slot_id=slot_id)["elapsed_seconds"] == 0.5
    for tampered in (
        {**valid, "missing": 1},
        {key: value for key, value in valid.items() if key != "epochs"},
        {**valid, "elapsed_seconds": "0.5"},
        {**valid, "parameter_count": True},
        {**valid, "target_instruments": 19},
        {**valid, "training_batch_identity": "b" * 64},
    ):
        with pytest.raises(ValueError):
            _validate_fit_evidence(
                tampered,
                slot_id=slot_id,
                expected_training_batch_identity="a" * 64,
            )

    evidence = {slot_id: valid}
    with pytest.raises(ValueError, match="canonical slot set"):
        _summarize_fit_evidence(
            evidence, expected_slots=(slot_id, "POOLED_NON_GRAPH_RESIDUAL:17:DEV_2")
        )


def test_authenticated_aggregate_requires_consistent_post_outcome_labels(tmp_path, monkeypatch):
    (tmp_path / "metrics").mkdir()
    (tmp_path / "result").mkdir()
    (tmp_path / "metric").mkdir()
    label = "COMPUTED_POST_HOC_HISTORICAL_EXPLORATORY"
    actual_paths = (
        "metrics/terminal-metrics.json",
        "result/nomination.json",
        "result/final-exploratory-report.json",
    )
    for relative in actual_paths:
        (tmp_path / relative).write_text(
            json.dumps({"scientific_performance": label}), encoding="utf-8"
        )

    def fake_impl(root, config, identity):
        return "metric-id"

    monkeypatch.setattr(execution_module, "_aggregate_authenticated_metrics_impl", fake_impl)
    identity = cast(Any, object())
    assert execution_module.aggregate_authenticated_metrics(tmp_path, None, identity) == "metric-id"

    for legacy in ("metric/terminal-metrics.json", "metric/nomination.json"):
        (tmp_path / legacy).write_text(
            json.dumps({"scientific_performance": label}), encoding="utf-8"
        )
    for relative in actual_paths:
        (tmp_path / relative).unlink()
    with pytest.raises(FileNotFoundError):
        execution_module.aggregate_authenticated_metrics(tmp_path, None, identity)
    for relative in actual_paths:
        (tmp_path / relative).write_text(
            json.dumps({"scientific_performance": label}), encoding="utf-8"
        )

    for relative in actual_paths:
        payload = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
        payload["scientific_performance"] = "NOT_COMPUTED"
        (tmp_path / relative).write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(
            ValueError, match="post-outcome scientific performance label is invalid"
        ):
            execution_module.aggregate_authenticated_metrics(tmp_path, None, identity)
        payload["scientific_performance"] = label
        (tmp_path / relative).write_text(json.dumps(payload), encoding="utf-8")


def test_build_tensors_index_matches_full_frame_reference() -> None:
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    import numpy as np
    import polars as pl

    import experiments.r4_residual_graph.tensor as tensor_module

    contract = tensor_module.TensorContract()
    start = datetime(2026, 5, 15, 14, 6, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for offset in range(130):
        timestamp = start + timedelta(minutes=offset)
        for instrument_index, instrument in enumerate(contract.node_order):
            row: dict[str, object] = {
                "instrument_id": instrument,
                "decision_time": timestamp,
                "feature_data_asof": timestamp,
                "feature_available_at": timestamp,
                "source_active": 1.0,
            }
            row.update(
                {feature: float(offset + instrument_index) for feature in contract.feature_names}
            )
            if offset == 25 and instrument_index == 0:
                row["return_60s"] = None
            if offset == 35 and instrument_index == 1:
                row["source_active"] = 0.0
            rows.append(row)
    frame = pl.DataFrame(rows)
    support = SimpleNamespace(
        keys=tuple(
            (start + timedelta(minutes=60 + offset)).isoformat() for offset in (0, 10, 40, 69)
        )
    )

    def reference() -> dict[str, Any]:
        expected: dict[str, Any] = {}
        for key in support.keys:
            timestamp = datetime.fromisoformat(key)
            history = frame.filter(pl.col("decision_time") <= timestamp)
            expected[key] = tensor_module.build_masked_sequence(history, timestamp)
        return expected

    expected = reference()
    actual = execution_module._build_tensors(frame, support)
    assert tuple(actual) == tuple(expected)
    for key in support.keys:
        expected_tensor = expected[key]
        actual_tensor = actual[key]
        np.testing.assert_equal(actual_tensor.values, expected_tensor.values)
        np.testing.assert_equal(actual_tensor.value_mask, expected_tensor.value_mask)
        np.testing.assert_equal(actual_tensor.availability_mask, expected_tensor.availability_mask)
        np.testing.assert_equal(actual_tensor.node_mask, expected_tensor.node_mask)
        assert actual_tensor.decision_time == expected_tensor.decision_time
        assert actual_tensor.contract_identity == expected_tensor.contract_identity

    duplicate = frame.vstack(frame.head(1))
    with pytest.raises(ValueError, match="duplicate tensor key"):
        execution_module._build_tensors(duplicate, support)


def test_build_tensors_materialises_on_demand_without_resident_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import polars as pl

    calls: list[str] = []

    def fake_build(rows: list[dict[str, object]], timestamp: datetime) -> tuple[int, datetime]:
        del rows
        calls.append(timestamp.isoformat())
        return len(calls), timestamp

    monkeypatch.setattr(
        "experiments.r4_residual_graph.tensor.build_masked_sequence",
        fake_build,
    )
    keys = tuple(
        datetime(2026, 5, 15, 14, 6 + offset, tzinfo=UTC).isoformat() for offset in range(3)
    )
    rows = pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd"],
            "decision_time": [datetime(2026, 5, 15, 14, 6, tzinfo=UTC)],
        }
    )
    tensors = execution_module._build_tensors(rows, SimpleNamespace(keys=keys))
    assert tuple(tensors) == keys
    assert calls == []
    assert tensors[keys[1]][0] == 1
    assert calls == [keys[1]]
    assert tensors[keys[1]][0] == 2
    assert calls == [keys[1], keys[1]]


def test_build_tensors_index_handles_large_key_set() -> None:
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    import polars as pl

    import experiments.r4_residual_graph.tensor as tensor_module

    contract = tensor_module.TensorContract()
    start = datetime(2026, 5, 15, 14, 6, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for offset in range(1_060):
        timestamp = start + timedelta(minutes=offset)
        for instrument in contract.node_order:
            row: dict[str, object] = {
                "instrument_id": instrument,
                "decision_time": timestamp,
                "feature_data_asof": timestamp,
                "feature_available_at": timestamp,
                "source_active": 1.0,
            }
            row.update({feature: float(offset) for feature in contract.feature_names})
            rows.append(row)
    frame = pl.DataFrame(rows)
    support = SimpleNamespace(
        keys=tuple((start + timedelta(minutes=60 + offset)).isoformat() for offset in range(500))
    )

    tensors = execution_module._build_tensors(frame, support)
    assert tuple(tensors) == support.keys


def test_authenticated_development_slot_reaches_control_key_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A retained DEV key reaches persistence without invoking scientific fitting."""
    from types import SimpleNamespace

    import polars as pl

    import experiments.r4_residual_graph.runtime as runtime_module
    import experiments.r4_residual_graph.stage_cache as stage_cache_module

    decision_times = {
        "DEV_1": datetime(2026, 5, 29, 14, 5, tzinfo=UTC),
        "DEV_2": datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
    }
    target_key = f"{ALL_INSTRUMENTS[0]}|{decision_times['DEV_2'].isoformat()}"
    rows = pl.DataFrame(
        {
            "instrument_id": [ALL_INSTRUMENTS[0], ALL_INSTRUMENTS[0]],
            "decision_time": [decision_times["DEV_1"], decision_times["DEV_2"]],
            "block": ["DEV_1", "DEV_2"],
            "local_forecast": [0.0, 0.1],
        }
    )
    development_support_path = tmp_path / "development-support.parquet"
    pl.DataFrame({"block": ["DEV_2"], "instrument_id": [ALL_INSTRUMENTS[0]]}).write_parquet(
        development_support_path
    )
    foundation = type(
        "Foundation",
        (),
        {"content_identity": "f" * 64, "ordered_row_keys": (), "rows": rows},
    )()
    support = type(
        "Support",
        (),
        {"target_keys": (target_key,), "identity": "s" * 64, "row_content_identity": "r" * 64},
    )()
    identity = G0ExecutionIdentity(
        code_head="0" * 40,
        lock_identity="1" * 64,
        application_identity="2" * 64,
        runtime_identity="3" * 64,
        config_identity="4" * 64,
        register_identity="5" * 64,
        output_root_identity="6" * 64,
    )
    context = {
        "binding": {"development_support_path": str(development_support_path)},
        "foundation": foundation,
        "support": support,
        "tensors": {},
        "rows": rows,
        "parent": object(),
    }
    monkeypatch.setattr(execution_module, "_load_real_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(
        execution_module,
        "reconstruct_authenticated_linear_forecasts",
        lambda *_args, **_kwargs: {
            "DEV_2": {
                "keys": (target_key,),
                "LOCAL_RIDGE": [0.1],
                "FULLY_POOLED_LOCAL_RIDGE": [0.2],
                "identity": "i" * 64,
                "controls": {
                    "ZERO_RETURN": [0.0],
                    "LOCAL_RIDGE": [0.1],
                    "FULLY_POOLED_LOCAL_RIDGE": [0.2],
                },
                "control_identities": {},
                "support_identity": "s" * 64,
            },
            "DEV_3": {"keys": (), "LOCAL_RIDGE": [], "FULLY_POOLED_LOCAL_RIDGE": []},
        },
    )
    monkeypatch.setattr(
        execution_module,
        "_build_execution_support",
        lambda *_args, **_kwargs: support,
    )
    monkeypatch.setattr(
        execution_module,
        "_build_tensors",
        lambda *_args, **_kwargs: {target_key.split("|", 1)[1]: object()},
    )
    monkeypatch.setattr(
        execution_module,
        "_build_training_objects",
        lambda *_args, **_kwargs: (object(), object(), object()),
    )
    monkeypatch.setattr(execution_module, "_masked_tensor_identity", lambda _tensor: "m" * 64)
    monkeypatch.setattr(execution_module, "training_preprocessor_identity", lambda _value: "p" * 64)
    monkeypatch.setattr(execution_module, "_build_real_family_model", lambda _family: object())
    monkeypatch.setattr(execution_module, "configure_deterministic_cuda", lambda _seed: None)
    monkeypatch.setattr(execution_module, "_persist_real_model", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(
        execution_module,
        "verify_attempt_bundle",
        lambda *_args, **_kwargs: {"seal": {"seal_identity": "s" * 64}},
    )

    class DummyBatch:
        def __init__(self) -> None:
            self.content_identity = "b" * 64

        @classmethod
        def from_authenticated_support(cls, **_kwargs: object) -> object:
            return cls()

        @classmethod
        def from_authenticated_oof(cls, **_kwargs: object) -> object:
            return cls()

    monkeypatch.setattr(runtime_module, "PredictionBatch", DummyBatch)
    monkeypatch.setattr(runtime_module, "ResidualTrainingBatch", DummyBatch)
    cached_training = DummyBatch()
    cached_prediction = DummyBatch()
    stage_payload = {
        "cache_root": str(tmp_path / "DEV_2"),
        "cache_identity": "v" * 64,
        "semantic_inputs": {},
        "training_batch_identity": cached_training.content_identity,
        "prediction_batch_identity": cached_prediction.content_identity,
        "preprocessor_identity": "p" * 64,
        "prediction_input_identity": "i" * 64,
        "target_keys": [target_key],
        "control_period": {
            "keys": [target_key],
            "LOCAL_RIDGE": [0.1],
            "FULLY_POOLED_LOCAL_RIDGE": [0.2],
            "identity": "i" * 64,
            "controls": {},
            "control_identities": {},
            "support_identity": "s" * 64,
        },
    }
    monkeypatch.setattr(
        execution_module,
        "_load_development_execution_capsule",
        lambda *_args, **_kwargs: {
            "foundation_content_identity": "f" * 64,
            "stages": {"DEV_2": stage_payload, "DEV_3": stage_payload},
        },
    )
    monkeypatch.setattr(execution_module, "_capsule_cache_input", lambda *_args: object())
    monkeypatch.setattr(
        stage_cache_module,
        "load_stage_cache_batches",
        lambda *_args, **_kwargs: (
            cached_training,
            cached_prediction,
            SimpleNamespace(cache_identity="v" * 64),
        ),
    )
    fit_calls: list[object] = []
    monkeypatch.setattr(
        runtime_module, "fit_one_model", lambda *args, **_kwargs: fit_calls.append(args) or object()
    )
    monkeypatch.setattr(runtime_module, "predict_residual", lambda *_args, **_kwargs: object())

    slot = next(slot for slot in PRIMARY_SCHEDULE if slot[2] == "DEV_2")
    result = execution_module.run_authenticated_development_slots(tmp_path, (slot,), None, identity)

    assert result == (f"{slot[0]}:{slot[1]}:{slot[2]}",)
    assert len(fit_calls) == 1


def test_persisted_support_representation_accepts_exact_keys_with_distinct_identity() -> None:
    from types import SimpleNamespace

    import polars as pl

    first = datetime(2026, 5, 29, 14, 6, tzinfo=UTC)
    second = datetime(2026, 5, 29, 14, 7, tzinfo=UTC)
    rows = pl.DataFrame(
        {
            "instrument_id": [ALL_INSTRUMENTS[0], ALL_INSTRUMENTS[1]],
            "decision_time": [first, second],
        }
    )
    support_keys = (first.isoformat(), second.isoformat())
    target_keys = (
        f"{ALL_INSTRUMENTS[0]}|{first.isoformat()}",
        f"{ALL_INSTRUMENTS[1]}|{second.isoformat()}",
    )
    full_support = SimpleNamespace(
        keys=support_keys,
        target_keys=target_keys,
        tensor_identities=(("full-history", "tensor-identity"),),
        identity="f" * 64,
        row_content_identity="r" * 64,
    )
    target_only_record = SimpleNamespace(
        keys=support_keys,
        target_keys=target_keys,
        tensor_identities=(("target-only", "different-tensor"),),
        identity="t" * 64,
        row_content_identity="c" * 64,
    )

    execution_module._validate_persisted_support_keys(rows, target_only_record, full_support)

    assert full_support.tensor_identities == (("full-history", "tensor-identity"),)


@pytest.mark.parametrize("mutation", ["missing", "altered", "reordered", "duplicate", "extra"])
def test_persisted_support_representation_rejects_key_drift(mutation: str) -> None:
    from types import SimpleNamespace

    import polars as pl

    first = datetime(2026, 5, 29, 14, 6, tzinfo=UTC)
    second = datetime(2026, 5, 29, 14, 7, tzinfo=UTC)
    third = datetime(2026, 5, 29, 14, 8, tzinfo=UTC)
    rows = pl.DataFrame(
        {
            "instrument_id": [ALL_INSTRUMENTS[0], ALL_INSTRUMENTS[1]],
            "decision_time": [first, second],
        }
    )
    support_keys = (first.isoformat(), second.isoformat())
    target_keys = (
        f"{ALL_INSTRUMENTS[0]}|{first.isoformat()}",
        f"{ALL_INSTRUMENTS[1]}|{second.isoformat()}",
    )
    full_support = SimpleNamespace(keys=support_keys, target_keys=target_keys)
    target_only_record = SimpleNamespace(keys=support_keys, target_keys=target_keys)

    if mutation == "missing":
        mutated_rows = rows.head(1)
    elif mutation == "altered":
        mutated_rows = pl.DataFrame(
            {
                "instrument_id": [ALL_INSTRUMENTS[2], ALL_INSTRUMENTS[1]],
                "decision_time": [first, second],
            }
        )
    elif mutation == "reordered":
        mutated_rows = rows.reverse()
    elif mutation == "duplicate":
        mutated_rows = pl.concat([rows, rows.tail(1)])
    else:
        mutated_rows = pl.concat(
            [
                rows,
                pl.DataFrame(
                    {
                        "instrument_id": [ALL_INSTRUMENTS[2]],
                        "decision_time": [third],
                    }
                ),
            ]
        )

    with pytest.raises(ValueError, match="canonical key drift"):
        execution_module._validate_persisted_support_keys(
            mutated_rows, target_only_record, full_support
        )


def test_control_regression_uses_preterminal_foundation_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    import polars as pl

    import experiments.r4_residual_graph.foundation as foundation_module

    terminal_start = execution_module.TERMINAL_START
    rows = pl.DataFrame(
        {
            "row_id": [1, 2, 3],
            "target_available_at": [
                terminal_start - timedelta(minutes=1),
                terminal_start,
                terminal_start + timedelta(minutes=1),
            ],
        }
    )
    calls: list[pl.DataFrame] = []
    marker = object()

    def fake_reconstruct(
        source: pl.DataFrame, *, capsule: object, require_expected: bool
    ) -> dict[str, list[int]]:
        assert capsule is marker
        assert require_expected is True
        calls.append(source)
        return {"row_ids": source["row_id"].to_list()}

    monkeypatch.setattr(foundation_module, "reconstruct_linear_controls", fake_reconstruct)
    unfiltered = fake_reconstruct(rows, capsule=marker, require_expected=True)
    corrected = execution_module._reconstruct_control_regression(rows, capsule=marker)

    assert unfiltered == {"row_ids": [1, 2, 3]}
    assert corrected == {"row_ids": [1]}
    assert calls[1].equals(rows.head(1))


@pytest.mark.parametrize(
    ("persisted", "reconstructed", "equivalent"),
    [
        (
            {"configurations": {"LOCAL_RIDGE": {"model_mse": 0.00028668}}},
            {"configurations": {"LOCAL_RIDGE": {"model_mse": 0.00028668 + 6.661338147750939e-16}}},
            True,
        ),
        (
            {"configurations": {"LOCAL_RIDGE": {"model_mse": 0.00028668}}},
            {"configurations": {"LOCAL_RIDGE": {"model_mse": 0.00028768}}},
            False,
        ),
        ({"a": 1, "b": ["same"]}, {"b": ["same"], "a": 1}, True),
        ({"a": 1}, {"b": 1}, False),
        ([1, 2], [1], False),
        ([1, 2], [2, 1], False),
        ({"value": 1}, {"value": 1.0}, False),
        ({"source_class": "native"}, {"source_class": "external"}, False),
        (
            {"folds": [{"rows": {"count": 2}}]},
            {"folds": [{"rows": {"count": 3}}]},
            False,
        ),
        (float("nan"), float("nan"), False),
        (float("inf"), float("inf"), False),
    ],
)
def test_replay_values_equal_preserves_structure_and_nonfloat_leaves(
    persisted: Any, reconstructed: Any, equivalent: bool
) -> None:
    assert execution_module._replay_values_equal(persisted, reconstructed) is equivalent


def test_development_stage_cache_builds_each_stage_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    import polars as pl

    import experiments.r4_residual_graph.runtime as runtime_module
    import experiments.r4_residual_graph.stage_cache as stage_cache_module

    decision_times = (
        datetime(2026, 5, 29, 14, 5, tzinfo=UTC),
        datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
        datetime(2026, 5, 29, 14, 7, tzinfo=UTC),
    )
    rows = pl.DataFrame(
        {
            "block": ["DEV_1", "DEV_2", "DEV_3"],
            "decision_time": list(decision_times),
        }
    )
    context: dict[str, Any] = {"rows": rows, "parent": object()}
    selected = tuple(slot for slot in PRIMARY_SCHEDULE if slot[2] in {"DEV_2", "DEV_3"})
    assert len(selected) == 30
    support_calls: list[tuple[datetime, ...]] = []
    tensor_calls: list[tuple[str, ...]] = []
    preprocessor_calls: list[object] = []
    prediction_calls: list[dict[str, object]] = []
    training_calls: list[dict[str, object]] = []

    def fake_support(
        _rows: object, _parent: object, times: tuple[datetime, ...], **_kwargs: object
    ) -> Any:
        support_calls.append(times)
        timestamp = times[0].isoformat()
        key = f"EURUSD|{timestamp}"
        return SimpleNamespace(
            target_keys=(key,),
            identity=f"support-{len(support_calls)}",
            tensor_identities=((timestamp, f"tensor-{len(support_calls)}"),),
        )

    def fake_tensors(_rows: object, fake_support_record: Any) -> dict[str, object]:
        tensor_calls.append(tuple(fake_support_record.target_keys))
        timestamp = fake_support_record.target_keys[0].rsplit("|", 1)[-1]
        return {timestamp: object()}

    def fake_training_objects(_objects: dict[str, object]) -> tuple[object, object, object]:
        preprocessor = object()
        preprocessor_calls.append(preprocessor)
        return object(), preprocessor, object()

    cached_batches: dict[Path, tuple[object, object, object]] = {}

    def fake_build_stage_cache(
        root: Path,
        _cache_input: object,
        *,
        training_batch: object,
        prediction_batch: object,
        build_id: str,
    ) -> Any:
        cache_root = root / build_id
        verified = SimpleNamespace(root=cache_root)
        cached_batches[cache_root] = (training_batch, prediction_batch, verified)
        return verified

    def fake_load_stage_cache_batches(
        cache_root: Path, **_kwargs: object
    ) -> tuple[object, object, object]:
        return cached_batches[cache_root]

    class DummyBatch:
        def __init__(self, identity: str) -> None:
            self.content_identity = identity
            import torch

            self.values = torch.zeros((1, 1, 1))
            self.value_mask = torch.ones((1, 1, 1), dtype=torch.bool)
            self.availability_mask = torch.ones((1, 1, 1), dtype=torch.bool)
            self.node_mask = torch.ones((1, 1), dtype=torch.bool)
            self.target_nodes = torch.zeros((1, 1), dtype=torch.long)
            self.target_mask = torch.ones((1, 1), dtype=torch.bool)
            self.residuals = torch.zeros((1, 1))
            self.row_keys = (f"EURUSD|{decision_times[0].isoformat()}",)
            self.input_identity = "i" * 64
            self.support_identity = "s" * 64
            self.chronology_identity = "c" * 64
            self.preprocessor_identity = "p" * 64
            self.mode = "PRIMARY"
            self.training_blocks = ("DEV_1",)

        @classmethod
        def from_authenticated_support(cls, **kwargs: object) -> DummyBatch:
            prediction_calls.append(kwargs)
            return cls(f"prediction-{len(prediction_calls)}")

        @classmethod
        def from_authenticated_oof(cls, **kwargs: object) -> DummyBatch:
            training_calls.append(kwargs)
            return cls(f"training-{len(training_calls)}")

    monkeypatch.setattr(execution_module, "_build_execution_support", fake_support)
    monkeypatch.setattr(execution_module, "_build_tensors", fake_tensors)
    monkeypatch.setattr(execution_module, "_build_training_objects", fake_training_objects)
    monkeypatch.setattr(execution_module, "training_preprocessor_identity", lambda _value: "p" * 64)
    monkeypatch.setattr(stage_cache_module, "build_stage_cache", fake_build_stage_cache)
    monkeypatch.setattr(
        stage_cache_module, "load_stage_cache_batches", fake_load_stage_cache_batches
    )
    monkeypatch.setattr(
        execution_module, "_persist_development_execution_capsule", lambda *_args: None
    )
    monkeypatch.setattr(runtime_module, "PredictionBatch", DummyBatch)
    monkeypatch.setattr(runtime_module, "ResidualTrainingBatch", DummyBatch)
    config = FrozenRuntimeConfig()
    identity = G0ExecutionIdentity(
        code_head="0" * 40,
        lock_identity="1" * 64,
        application_identity="2" * 64,
        runtime_identity="3" * 64,
        config_identity=config.identity,
        register_identity="5" * 64,
        output_root_identity="6" * 64,
    )
    cache = execution_module._build_development_stage_cache(
        tmp_path,
        context,
        SimpleNamespace(content_identity="f" * 64),
        SimpleNamespace(identity="s" * 64),
        {"base": object()},
        selected,
        config,
        identity,
    )

    assert tuple(cache) == ("DEV_2", "DEV_3")
    assert len(support_calls) == 4
    assert len(tensor_calls) == 4
    assert len(preprocessor_calls) == 2
    assert len(prediction_calls) == 2
    assert len(training_calls) == 2
    assert all(call["stream"] is True for call in prediction_calls + training_calls)


def test_authenticated_development_caller_uses_real_primary_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import torch

    import experiments.r4_residual_graph.runtime as runtime
    from experiments.r4_residual_graph.attempt_artifacts import verify_attempt_bundle

    identity = G0ExecutionIdentity(
        "a" * 40, "b" * 64, "c" * 64, "d" * 64, "e" * 64, "f" * 64, "0" * 64
    )
    writer = execution_module._persist_real_model
    builder = execution_module._build_real_family_model
    _mock_development_runtime(monkeypatch, tmp_path, identity)
    monkeypatch.setattr(execution_module, "_persist_real_model", writer)
    monkeypatch.setattr(execution_module, "_build_real_family_model", builder)
    monkeypatch.setattr(execution_module, "verify_attempt_bundle", verify_attempt_bundle)
    monkeypatch.setattr(execution_module, "configure_deterministic_cuda", torch.manual_seed)
    monkeypatch.setattr(
        runtime,
        "fit_one_model",
        lambda model, *_args, **_kwargs: {
            "parameter_count": execution_module.model_parameter_count(model)
        },
    )
    monkeypatch.setattr(runtime, "predict_residual", lambda *_args, **_kwargs: torch.empty(0))

    def place(model: Any, device: str) -> Any:
        assert device == FrozenRuntimeConfig().device
        return model

    monkeypatch.setattr(torch.nn.Module, "to", place)
    assert execution_module.run_authenticated_development_slots(
        tmp_path, (("LOCAL_TEMPORAL_RESIDUAL", 17, "DEV_2"),), FrozenRuntimeConfig(), identity
    ) == ("LOCAL_TEMPORAL_RESIDUAL:17:DEV_2",)
    bundle = tmp_path / "attempts" / "LOCAL_TEMPORAL_RESIDUAL:17:DEV_2" / "attempt-0"
    verify_attempt_bundle(bundle)
    assert json.loads((bundle / "manifest.json").read_text())["attempt"]["mode"] == "PRIMARY"
    assert (
        json.loads((bundle / "result.json").read_text())["artifact_type"] == "R4.C_PRIMARY_RESULT"
    )


def test_smoke_cli_dispatches_reference_and_distinct_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        execution_module,
        "run_bounded_smoke",
        lambda root, *, reference_root: calls.append((root, reference_root)) or {"mode": "SMOKE"},
    )
    monkeypatch.setattr(
        execution_module,
        "preparation_entry",
        Mock(side_effect=AssertionError("CLI entered output root before smoke reference entry")),
    )
    root, reference = tmp_path / "smoke", tmp_path / "reference"
    assert (
        execution_module.main(
            ["--output-root", str(root), "smoke", "--reference-root", str(reference)]
        )
        == 0
    )
    assert calls == [(root, reference)]


@pytest.mark.parametrize("metadata_only", [False, True])
@pytest.mark.parametrize("support_count", [516_590, 771_140, 516_591])
def test_development_validators_use_authenticated_tensor_support(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata_only: bool,
    support_count: int,
) -> None:
    from types import SimpleNamespace

    from experiments.r4_residual_graph.evaluation import historical_r3h_cost_grid
    from experiments.r4_residual_graph.foundation import EXPECTED_LINEAR

    assert int(EXPECTED_LINEAR["development"]["support"]) == 771_140
    config = FrozenRuntimeConfig()
    identity = cast(
        G0ExecutionIdentity, SimpleNamespace(identity="a" * 64, output_root_identity="b" * 64)
    )
    slots = sorted(
        f"{family}:{seed}:{stage}"
        for family, seed, stage in execution_module._EXPECTED_DEVELOPMENT_SLOTS
    )
    capsule = {
        "foundation_content_identity": "c" * 64,
        "preparation_preprocessor_identity": "d" * 64,
        "stages": {
            stage: {
                "preprocessor_identity": "e" * 64,
                "target_keys": {"shape": [count]} if metadata_only else range(count),
            }
            for stage, count in (("DEV_2", 260_490), ("DEV_3", 256_100))
        },
    }
    metric = {
        "artifact_type": "R4.C_DEVELOPMENT_METRIC_GATE_REGISTER",
        "periods": ["DEV_2", "DEV_3"],
        "weighting": "union_within_instrument_then_equal_twenty",
        "total_forecast_definition": "local_ridge_forecast_plus_residual_correction",
        "r3h_cost_grid": historical_r3h_cost_grid(),
        "support_row_count": support_count,
        "families": {
            family: {
                "seeds": {str(seed): {} for seed in PRIMARY_SEEDS},
                "primary": {"seed": min(PRIMARY_SEEDS)},
            }
            for family in FITTED_FAMILY_IDS
        },
        "controls": {
            name: {} for name in ("ZERO_RETURN", "LOCAL_RIDGE", "FULLY_POOLED_LOCAL_RIDGE")
        },
        "coverage": {stage: dict.fromkeys(ALL_INSTRUMENTS, 1) for stage in ("DEV_2", "DEV_3")},
    }
    metric["metric_identity"] = _sha256(metric)
    payload = {
        "artifact_type": "R4.C_COMPLETE_AUTHENTICATED_DEVELOPMENT_REGISTER",
        "g0_identity": identity.identity,
        "expected_slots": slots,
        "terminal_dispositions": {
            f"{family}:{seed}:TERMINAL_FORMER_HOLDOUT": "UNOPENED"
            for family in FITTED_FAMILY_IDS
            for seed in PRIMARY_SEEDS
        },
        "artifact_hashes": {slot: {} for slot in slots},
        "slot_provenance": {slot: {} for slot in slots},
        "attempts_file_sha256": "f" * 64,
        "foundation_content_identity": capsule["foundation_content_identity"],
        "preprocessor_identity": capsule["preparation_preprocessor_identity"],
        "config_identity": config.identity,
        "runtime_identity": config.identity,
        "input_identity": execution_module.RESIDUAL_FOUNDATION_IDENTITY,
        "outcome_blind": True,
        "outcomes_loaded": False,
        "metric_register_identity": metric["metric_identity"],
        "metric_register_file_sha256": "f" * 64,
    }
    payload["register_identity"] = _sha256(payload)
    (tmp_path / "register" / "attempts").mkdir(parents=True)
    (tmp_path / "register" / "development-metrics.json").write_text(json.dumps(metric))
    (tmp_path / "register" / "development-register.json").write_text(json.dumps(payload))

    def load_capsule(*_args: object, **kwargs: object) -> dict[str, Any]:
        assert kwargs.get("metadata_only", False) is metadata_only
        return capsule

    class ReachedLedger(Exception):
        pass

    def ledger(*_args: object, **_kwargs: object) -> object:
        raise ReachedLedger

    monkeypatch.setattr(execution_module, "_load_development_execution_capsule", load_capsule)
    monkeypatch.setattr(execution_module, "_file_digest", lambda _path: "f" * 64)
    monkeypatch.setattr(execution_module, "_development_journal_identity", lambda _root: "f" * 64)
    monkeypatch.setattr(execution_module, "_validate_development_attempt_ledger", ledger)
    validator = (
        execution_module._validate_development_register_metadata
        if metadata_only
        else execution_module._validate_development_register_payload
    )
    expected_error = ReachedLedger if support_count == 516_590 else ValueError
    error_match = None if support_count == 516_590 else "metric register"
    with pytest.raises(expected_error, match=error_match):
        validator(tmp_path, identity, config=config)


def test_terminal_graph_attribution_keeps_learned_seed_graphs_and_fixed_invariants() -> None:
    from experiments.r4_residual_graph.execution import _terminal_primary_graph_identities

    identities = {
        f"{family}:{seed}:TERMINAL_FORMER_HOLDOUT": (
            f"learned-{seed}" if family == "LEARNED_STATIC_GRAPH_RESIDUAL" else "fixed"
        )
        for family in ("LEARNED_STATIC_GRAPH_RESIDUAL", "FIXED_ECONOMIC_GRAPH_RESIDUAL")
        for seed in (43, 29, 17)
    }
    primary = _terminal_primary_graph_identities(identities)
    assert primary == {
        "LEARNED_STATIC_GRAPH_RESIDUAL": "learned-17",
        "FIXED_ECONOMIC_GRAPH_RESIDUAL": "fixed",
    }
    assert len(set(identities.values())) == 4
    identities["FIXED_ECONOMIC_GRAPH_RESIDUAL:29:TERMINAL_FORMER_HOLDOUT"] = "drifted"
    with pytest.raises(ValueError, match="frozen family"):
        _terminal_primary_graph_identities(identities)
