"""Validate retained capability results without replaying any model fit.

Set P1_CAPABILITY_RESULT for the mandatory execution-evidence check. Its absence
is expected for an ordinary source-only checkout, which skips only these cases.
"""

import json
import math
import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def capability_result() -> dict[str, Any]:
    location = os.environ.get("P1_CAPABILITY_RESULT")
    if location is None:
        pytest.skip("retained R4-P1 capability artifact not supplied")
    return json.loads(Path(location).read_text())["results"]


@pytest.mark.parametrize("task", ["local", "pooled", "fixed"])
def test_retained_planted_recovery_and_discrimination(
    capability_result: dict[str, Any], task: str
) -> None:
    case = capability_result[task]
    capable = case[task]
    assert capable["evaluation_mse"] <= 0.1 * capable["zero_mse"]
    if task == "local":
        controls = [case["contemporaneous_oracle_mse"]]
    elif task == "pooled":
        controls = [case["local"]["evaluation_mse"]]
    else:
        controls = [case["pooled"]["evaluation_mse"], case["shuffled"]["evaluation_mse"]]
        assert case["fixed"]["family"] != case["shuffled"]["family"]
    assert all(capable["evaluation_mse"] <= 0.5 * control for control in controls)
    for value in case.values():
        if isinstance(value, dict) and "updates" in value:
            assert value["updates"] > 0
            assert value["initial_max_correction"] == 0
            assert value["max_gradient_norm"] > 0
            assert value["parameter_change_l2"] > 0
            assert all(
                math.isfinite(value[key])
                for key in [
                    "initial_loss",
                    "final_loss",
                    "target_rms",
                    "correction_rms",
                    "max_gradient_norm",
                    "parameter_change_l2",
                    "evaluation_mse",
                ]
            )
    assert case["passed"]


def test_retained_tiny_all_instrument_capacity(capability_result: dict[str, Any]) -> None:
    tiny = capability_result["tiny_real"]
    assert tiny["final_loss"] <= 0.01 * tiny["initial_loss"]
    assert tiny["final_loss"] <= 0.01
    assert tiny["initial_max_correction"] == 0
    assert tiny["max_gradient_norm"] > 0
    assert tiny["parameter_change_l2"] > 0
    assert all(
        math.isfinite(tiny[key])
        for key in [
            "initial_loss",
            "final_loss",
            "target_rms",
            "correction_rms",
            "max_gradient_norm",
            "parameter_change_l2",
        ]
    )
    assert tiny["passed"] and capability_result["passed"]
