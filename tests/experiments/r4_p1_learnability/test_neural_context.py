"""Comparator and pooled permutation invariants of the actual shared encoder."""

import numpy as np
import pytest
import torch

from experiments.r4_p1_learnability.training import Policy, TemporalModel
from experiments.r4_residual_graph.graph import build_fixed_economic_graph, shuffle_economic_graph


@pytest.mark.parametrize("family", ["local", "pooled", "fixed", "shuffled"])
def test_zero_head_and_exact_graph(family: str) -> None:
    model = TemporalModel(family, 3, Policy(device="cpu", hidden=8))
    values = torch.randn(2, 5, 20, 3)
    present = torch.ones((2, 20), dtype=torch.bool)
    assert torch.count_nonzero(model(values, present)).item() == 0
    graph = build_fixed_economic_graph()
    expected = shuffle_economic_graph(graph) if family == "shuffled" else graph
    np.testing.assert_allclose(model.adjacency.numpy(), expected.normalized_adjacency)


def test_pooled_excludes_own_and_is_permutation_invariant() -> None:
    model = TemporalModel("pooled", 3, Policy(device="cpu", hidden=8))
    torch.nn.init.normal_(model.head.weight)
    values = torch.randn(2, 5, 20, 3)
    present = torch.ones((2, 20), dtype=torch.bool)
    permutation = torch.tensor([0, *range(19, 0, -1)])
    first = model(values, present)[:, 0]
    second = model(values[:, :, permutation], present[:, permutation])[:, 0]
    torch.testing.assert_close(first, second)
