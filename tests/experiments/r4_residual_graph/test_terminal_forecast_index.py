"""Finite reporting equivalence without model fitting or retained inputs."""

import ast
import inspect
import struct
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import torch

from experiments.r4_residual_graph import execution, terminal_support
from experiments.r4_residual_graph.attempt_artifacts import canonical_json


def _input() -> Any:
    rows: list[dict[str, Any]] = [
        {"instrument_id": name, "decision_time": datetime(2026, 7, 1, tzinfo=UTC)}
        for name in ("z", "a", "m", "b")
    ]
    forecasts = tuple(
        (f"{row['instrument_id']}|{row['decision_time'].isoformat()}", value)
        for row, value in zip(rows, (-0.0, 0.0, 0.125, -3.5), strict=True)
    )
    fields = dict.fromkeys(
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
        "synthetic",
    )
    payload = {
        "rows": [
            {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in row.items()
            }
            for row in rows
        ],
        "forecasts": forecasts,
        **fields,
    }
    return terminal_support.AuthenticatedTerminalPredictionInput._create(
        token=terminal_support._TERMINAL_PREDICTION_INPUT_SEAL,
        rows=rows,
        forecasts=forecasts,
        content_identity=execution._sha256(payload),
        **fields,
    )


def _assembly(prediction_input: Any, keys: tuple[str, ...], dtype: Any) -> Any:
    tree = ast.parse(inspect.getsource(execution._publish_prepared_terminal_prediction))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"forecasts_by_key", "local_forecast"}
    ]
    # Execute the actual reporting statements; exclude the later shape adjustment.
    assignments = [
        node
        for node in assignments
        if isinstance(node.value, ast.Call)
        and (
            isinstance(node.value.func, ast.Name)
            or (isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "as_tensor")
        )
    ]
    assert len(assignments) == 2
    namespace = {
        "torch": torch,
        "prediction_input": prediction_input,
        "support": SimpleNamespace(target_keys=keys),
        "prediction": torch.zeros(len(keys), dtype=dtype),
    }
    body: list[ast.stmt] = list(assignments)
    exec(compile(ast.Module(body=body, type_ignores=[]), "<reporting>", "exec"), namespace)
    return namespace["local_forecast"]


def _payload(values: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    residual = torch.zeros_like(values)
    payload = {
        "prediction_input_identity": "synthetic",
        "target_keys": keys,
        "local_ridge_forecast": values.tolist(),
        "fully_pooled_local_ridge_forecast": values.tolist(),
        "residual_prediction": residual.tolist(),
        "prediction": residual.tolist(),
        "total_forecast": (values + residual).tolist(),
        "linear_control_identity": "synthetic",
    }
    payload["forecast_identity"] = execution._terminal_prediction_identity(payload)
    return payload


@pytest.mark.parametrize("dtype", [torch.float32, torch.float64])
def test_indexed_reporting_preserves_bits_order_payload_and_identity(
    monkeypatch: pytest.MonkeyPatch,
    dtype: Any,
) -> None:
    prediction_input = _input()
    keys = tuple(key for key, _ in prediction_input.forecasts)
    expected = torch.as_tensor([prediction_input.forecast_for(key) for key in keys], dtype=dtype)

    def no_scan(*args: Any) -> Any:
        raise AssertionError("reporting must not scan forecast_for")

    monkeypatch.setattr(type(prediction_input), "forecast_for", no_scan)
    actual = _assembly(prediction_input, keys, dtype)
    assert actual.numpy().tobytes() == expected.numpy().tobytes()
    assert actual.dtype == expected.dtype and actual.device == expected.device
    assert canonical_json(_payload(actual, keys)) == canonical_json(_payload(expected, keys))
    assert (
        execution._validate_terminal_forecast_payload(_payload(actual, keys), prediction_input)
        == _payload(expected, keys)["forecast_identity"]
    )
    reversed_values = _assembly(prediction_input, keys[::-1], dtype)
    assert reversed_values.numpy().tobytes() == expected.flip(0).numpy().tobytes()
    assert struct.pack("!d", dict(prediction_input.forecasts)[keys[0]]) == struct.pack("!d", -0.0)


def test_indexed_reporting_missing_key_raises() -> None:
    prediction_input = _input()
    with pytest.raises(KeyError, match="absent"):
        prediction_input.forecast_for("absent")
    with pytest.raises(KeyError, match="absent"):
        _assembly(prediction_input, ("absent",), torch.float32)


def test_capability_rejects_duplicate_keys() -> None:
    prediction_input = _input()
    object.__setattr__(prediction_input, "rows", (prediction_input.rows[0],) * 4)
    with pytest.raises(ValueError, match="duplicate keys"):
        prediction_input._validate()


def test_reporting_iteration_work_is_linear() -> None:
    class CountedForecasts:
        def __init__(self) -> None:
            self.visits = 0

        def __iter__(self) -> Any:
            for item in _input().forecasts:
                self.visits += 1
                yield item

    forecasts = CountedForecasts()
    prediction_input = SimpleNamespace(forecasts=forecasts)
    keys = tuple(key for key, _ in _input().forecasts)
    values = _assembly(prediction_input, keys, torch.float32)
    assert forecasts.visits == len(keys)
    execution._validate_terminal_forecast_payload(_payload(values, keys), prediction_input)
    assert forecasts.visits == 3 * len(keys)
