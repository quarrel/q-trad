"""Frozen economic graph and deterministic shuffled-control contracts for R4.B."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np

from .foundation import ALL_INSTRUMENTS

SELF_LOOP_WEIGHT = 1.0
ASSET_CLASS_WEIGHTS = {"COMMODITY": 0.50, "FX": 0.35, "INDEX": 0.40}
SHUFFLE_SHIFT = 2
ECONOMIC_GRAPH_VERSION = "R4.B-FROZEN-ECONOMIC-V1"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _asset_class(instrument: str) -> str:
    prefix = instrument.split(":", 1)[0]
    return {"commodity": "COMMODITY", "fx": "FX", "index": "INDEX"}[prefix]


def _fx_currencies(instrument: str) -> tuple[str, str]:
    pair = instrument.split(":", 1)[1]
    base, quote = pair.split("-", 1)
    return base.upper(), quote.upper()


@dataclass(frozen=True)
class EconomicEdge:
    source: str
    target: str
    weight: float
    rationale: str


@dataclass(frozen=True)
class FrozenEconomicGraph:
    node_order: tuple[str, ...]
    adjacency: np.ndarray
    normalized_adjacency: np.ndarray
    edges: tuple[EconomicEdge, ...]
    identity: str
    normalization: str = "SYMMETRIC_D^-1/2 A D^-1/2"
    diagonal_policy: str = "EXPLICIT_SELF_LOOP_WEIGHT_1"
    semantics_version: str = ECONOMIC_GRAPH_VERSION

    def __post_init__(self) -> None:
        n = len(self.node_order)
        if self.adjacency.shape != (n, n) or self.normalized_adjacency.shape != (n, n):
            raise ValueError("graph matrices must be square and match node count")
        if not np.allclose(self.adjacency, self.adjacency.T):
            raise ValueError("fixed economic graph must be symmetric")
        if not np.all(np.diag(self.adjacency) > 0.0):
            raise ValueError("every node requires an explicit self-loop")
        self.adjacency.setflags(write=False)
        self.normalized_adjacency.setflags(write=False)


def build_fixed_economic_graph(
    node_order: tuple[str, ...] = tuple(ALL_INSTRUMENTS),
) -> FrozenEconomicGraph:
    """Build a graph from frozen asset-class, currency and family semantics only."""

    canonical = tuple(node_order)
    if canonical != tuple(ALL_INSTRUMENTS):
        raise ValueError("graph node order must be the canonical twenty instruments")
    n = len(canonical)
    matrix = np.zeros((n, n), dtype=float)
    edge_map = {}
    index = {node: i for i, node in enumerate(canonical)}

    def add_edge(source: str, target: str, weight: float, rationale: str) -> None:
        i, j = index[source], index[target]
        if i == j:
            matrix[i, j] = SELF_LOOP_WEIGHT
            edge_map[(i, j)] = EconomicEdge(source, target, SELF_LOOP_WEIGHT, rationale)
            return
        if matrix[i, j] == 0.0 or weight > matrix[i, j]:
            matrix[i, j] = weight
            edge_map[(i, j)] = EconomicEdge(source, target, weight, rationale)

    for node in canonical:
        add_edge(node, node, SELF_LOOP_WEIGHT, "self_loop: preserve node state")
    for source in canonical:
        source_class = _asset_class(source)
        for target in canonical:
            if source == target:
                continue
            target_class = _asset_class(target)
            if source_class == target_class:
                add_edge(
                    source,
                    target,
                    ASSET_CLASS_WEIGHTS[source_class],
                    f"asset_class:{source_class.lower()}",
                )
            if source_class == target_class == "FX" and set(_fx_currencies(source)) & set(
                _fx_currencies(target)
            ):
                add_edge(source, target, 0.75, "shared_currency")
    edges = tuple(edge_map[key] for key in sorted(edge_map))
    matrix = np.maximum(matrix, matrix.T)
    degrees = matrix.sum(axis=1)
    if np.any(degrees <= 0.0):
        raise ValueError("fixed graph contains a zero-degree node")
    normaliser = np.diag(1.0 / np.sqrt(degrees))
    normalized = normaliser @ matrix @ normaliser
    payload = {
        "node_order": canonical,
        "edges": [
            {"source": e.source, "target": e.target, "weight": e.weight, "rationale": e.rationale}
            for e in edges
        ],
        "normalization": "SYMMETRIC_D^-1/2 A D^-1/2",
        "diagonal_policy": "EXPLICIT_SELF_LOOP_WEIGHT_1",
        "semantics_version": ECONOMIC_GRAPH_VERSION,
    }
    identity = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return FrozenEconomicGraph(canonical, matrix, normalized, edges, identity)


@dataclass(frozen=True)
class ShuffledEconomicGraph:
    node_order: tuple[str, ...]
    permutation: np.ndarray
    adjacency: np.ndarray
    normalized_adjacency: np.ndarray
    source_identity: str
    identity: str
    off_diagonal_difference_fraction: float
    neighbourhood_difference_fraction: float

    def __post_init__(self) -> None:
        n = len(self.node_order)
        if self.permutation.shape != (n, n) or self.adjacency.shape != (n, n):
            raise ValueError("shuffled graph matrix shape mismatch")
        self.permutation.setflags(write=False)
        self.adjacency.setflags(write=False)
        self.normalized_adjacency.setflags(write=False)


def deterministic_fixed_point_free_permutation(
    node_count: int = len(ALL_INSTRUMENTS), *, shift: int = SHUFFLE_SHIFT
) -> np.ndarray:
    if node_count < 2:
        raise ValueError("at least two nodes are required")
    offset = shift % node_count
    if offset == 0:
        raise ValueError("permutation must be fixed-point-free")
    permutation = np.zeros((node_count, node_count), dtype=float)
    for index in range(node_count):
        permutation[index, (index + offset) % node_count] = 1.0
    if not np.allclose(permutation.sum(axis=0), 1.0) or not np.allclose(
        permutation.sum(axis=1), 1.0
    ):
        raise ValueError("invalid permutation matrix")
    if np.any(np.diag(permutation) != 0.0):
        raise ValueError("permutation is not fixed-point-free")
    return permutation


def shuffle_economic_graph(
    graph: FrozenEconomicGraph, *, shift: int = SHUFFLE_SHIFT
) -> ShuffledEconomicGraph:
    permutation = deterministic_fixed_point_free_permutation(len(graph.node_order), shift=shift)
    shuffled = permutation @ graph.adjacency @ permutation.T
    normalized = permutation @ graph.normalized_adjacency @ permutation.T
    off_diagonal = ~np.eye(len(graph.node_order), dtype=bool)
    union = (graph.adjacency != 0.0) | (shuffled != 0.0)
    denominator = int(np.count_nonzero(union & off_diagonal))
    if denominator == 0:
        raise ValueError("cannot assess shuffled graph with zero off-diagonal edges")
    changed = int(np.count_nonzero((graph.adjacency != shuffled) & off_diagonal))
    off_diagonal_fraction = changed / denominator
    old_neighbourhoods = graph.adjacency != 0.0
    new_neighbourhoods = shuffled != 0.0
    neighbourhood_fraction = float(
        np.mean(
            [
                not np.array_equal(old_neighbourhoods[index], new_neighbourhoods[index])
                for index in range(len(graph.node_order))
            ]
        )
    )
    if off_diagonal_fraction < 0.50 or neighbourhood_fraction < 0.50:
        raise ValueError("shuffled graph fails the required 50% structural difference")
    payload = {
        "source_identity": graph.identity,
        "shift": shift % len(graph.node_order),
        "permutation": permutation.astype(int).tolist(),
        "adjacency": shuffled.tolist(),
    }
    identity = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return ShuffledEconomicGraph(
        graph.node_order,
        permutation,
        shuffled,
        normalized,
        graph.identity,
        identity,
        off_diagonal_fraction,
        neighbourhood_fraction,
    )


build_fixed_graph = build_fixed_economic_graph
build_shuffled_graph = shuffle_economic_graph


__all__ = [
    "ASSET_CLASS_WEIGHTS",
    "SELF_LOOP_WEIGHT",
    "SHUFFLE_SHIFT",
    "EconomicEdge",
    "FrozenEconomicGraph",
    "ShuffledEconomicGraph",
    "build_fixed_economic_graph",
    "build_fixed_graph",
    "build_shuffled_graph",
    "deterministic_fixed_point_free_permutation",
    "shuffle_economic_graph",
]
