"""Public B/C entry points reject a missing freeze before using panel data."""

from pathlib import Path
from typing import cast

import pytest

from experiments.r4_p1_learnability.analysis import linear_probes, residual_diagnostics
from experiments.r4_p1_learnability.data import Panel
from experiments.r4_p1_learnability.run import neural_screen
from experiments.r4_p1_learnability.training import Policy


@pytest.mark.parametrize("entrypoint", ["diagnostics", "linear", "neural"])
def test_missing_freeze_precedes_panel_computation(tmp_path: Path, entrypoint: str) -> None:
    panel = cast(Panel, object())
    register = tmp_path / "absent.jsonl"
    with pytest.raises(ValueError, match="R4P1_POLICY_FREEZE"):
        if entrypoint == "diagnostics":
            residual_diagnostics(panel, register)
        elif entrypoint == "linear":
            linear_probes(panel, register)
        else:
            neural_screen(panel, register, Policy(device="cpu"), False, tmp_path)
