"""Create-only authenticated mmap store for prepared causal tensors."""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import pickle
import tempfile
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from itertools import pairwise
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np

from .stage_cache import (
    _file_digest,
    _publication_file_state,
    _publish_cache_once,
    _semantic_array_digest,
)
from .tensor import (
    AuthenticatedSupportCorpus,
    LazyTensorSequence,
    MaskedTensor,
    SupportInputCapability,
    TensorContract,
    _as_utc,
    _masked_tensor_identity,
    _support_source_identity,
    build_masked_sequence,
)

_SCHEMA = "R4-P0-RAW-TENSOR-STORE-V1"
_MAX_WORKERS = 8
_WORKER_MMAP: mmap.mmap | None = None
_WORKER_INDEX: dict[str, tuple[int, int]] | None = None
_RAW_PUBLICATION_CAPABILITY = object()


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _identity(payload: object) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _private_memory_kib() -> tuple[int, int]:
    fields: dict[str, int] = {}
    for line in Path("/proc/self/smaps_rollup").read_text().splitlines():
        name, separator, value = line.partition(":")
        if separator and name in {"Pss", "Private_Clean", "Private_Dirty"}:
            fields[name] = int(value.split()[0])
    return fields["Pss"], fields["Private_Clean"] + fields["Private_Dirty"]


def _initialise_worker(source_path: str, index: dict[str, tuple[int, int]]) -> None:
    global _WORKER_INDEX, _WORKER_MMAP
    with Path(source_path).open("rb") as source_file:
        _WORKER_MMAP = mmap.mmap(source_file.fileno(), 0, access=mmap.ACCESS_READ)
    _WORKER_INDEX = index


def _worker_materialise(key: str) -> tuple[str, MaskedTensor, int, int, int]:
    source_mmap = _WORKER_MMAP
    index = _WORKER_INDEX
    if source_mmap is None or index is None:
        raise RuntimeError("raw tensor worker mmap source is unavailable")
    timestamp = datetime.fromisoformat(key)
    window_rows: list[dict[str, Any]] = []
    for offset in range(61):
        minute_key = (timestamp - timedelta(minutes=60 - offset)).isoformat()
        location = index.get(minute_key)
        if location is not None:
            start, size = location
            window_rows.extend(pickle.loads(source_mmap[start : start + size]))
    tensor = build_masked_sequence(window_rows, timestamp)
    pss_kib, uss_kib = _private_memory_kib()
    return key, tensor, os.getpid(), pss_kib, uss_kib


def _ordered_materialisation(
    source_path: Path, index: dict[str, tuple[int, int]], keys: Sequence[str]
) -> Iterator[tuple[str, MaskedTensor, int, int, int]]:
    """Construct tensors from shared read-only mmap input in canonical bounded order."""
    if not keys:
        return
    with ProcessPoolExecutor(
        max_workers=min(_MAX_WORKERS, len(keys)),
        mp_context=get_context("spawn"),
        initializer=_initialise_worker,
        initargs=(str(source_path), index),
    ) as executor:
        pending: deque[tuple[str, Future[tuple[str, MaskedTensor, int, int, int]]]] = deque()
        iterator = iter(keys)
        for key in iterator:
            pending.append((key, executor.submit(_worker_materialise, key)))
            if len(pending) == _MAX_WORKERS:
                break
        while pending:
            expected_key, future = pending.popleft()
            key, tensor, pid, pss_kib, uss_kib = future.result()
            if key != expected_key:
                raise ValueError("raw tensor worker order drifted")
            yield key, tensor, pid, pss_kib, uss_kib
            try:
                next_key = next(iterator)
            except StopIteration:
                continue
            pending.append((next_key, executor.submit(_worker_materialise, next_key)))


def _write_worker_source(
    source: Mapping[str, MaskedTensor], path: Path
) -> dict[str, tuple[int, int]]:
    worker_rows = getattr(source, "worker_rows", None)
    if worker_rows is None:
        raise TypeError("raw tensor construction requires explicit worker rows")
    index: dict[str, tuple[int, int]] = {}
    with path.open("xb") as stream:
        stream.write(b"\0")
        for timestamp, rows in worker_rows().items():
            encoded = pickle.dumps(tuple(rows), protocol=5)
            start = stream.tell()
            stream.write(encoded)
            index[str(timestamp)] = (start, len(encoded))
    return index


class RawTensorStore(Mapping[str, MaskedTensor]):
    """Lazy mmap-backed tensor mapping authenticated once at load."""

    def __init__(self, root: Path, manifest: dict[str, Any]) -> None:
        self.root = root
        self.manifest = manifest
        self._keys = tuple(str(value) for value in np.load(root / "timestamps.npy", mmap_mode="r"))
        self._index = {key: index for index, key in enumerate(self._keys)}
        self._values = np.load(root / "values.npy", mmap_mode="r")
        self._value_mask = np.load(root / "value-mask.npy", mmap_mode="r")
        self._availability_mask = np.load(root / "availability-mask.npy", mmap_mode="r")
        self._node_mask = np.load(root / "node-mask.npy", mmap_mode="r")
        self._identities = np.load(root / "tensor-identities.npy", mmap_mode="r")
        self._contract = TensorContract()
        self._preparation: _RawPreparationCapability | None = None

    def __len__(self) -> int:
        return len(self._keys)

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __getitem__(self, key: str) -> MaskedTensor:
        index = self._index[key]
        return MaskedTensor(
            values=self._values[index],
            value_mask=self._value_mask[index],
            availability_mask=self._availability_mask[index],
            node_mask=self._node_mask[index],
            decision_time=datetime.fromisoformat(key),
            contract_identity=self._contract.identity,
            contract=self._contract,
        )

    @property
    def tensor_identities(self) -> dict[str, str]:
        """Return the identity table sealed by full-store authentication."""
        return dict(zip(self._keys, (str(value) for value in self._identities), strict=True))

    def _support_tensor_identities(self, keys: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        """Read only the requested identities, in the caller's support order."""
        return tuple((key, str(self._identities[self._index[key]])) for key in keys)


class _RawPreparationCapability:
    """One immediate preparation handoff from authenticated raw-store publication."""

    def __init__(
        self,
        store: RawTensorStore,
        identities: tuple[str, ...],
        authenticate_publication: Callable[[Path], None],
        *,
        token: object,
    ) -> None:
        if token is not _RAW_PUBLICATION_CAPABILITY:
            raise TypeError("preparation requires authenticated raw-store publication")
        self.store = store
        self.keys = store._keys
        self.index = dict(store._index)
        self.identities = identities
        self.root = store.root
        self.manifest = _canonical(store.manifest)
        self.authenticate_publication = authenticate_publication
        self.arrays = (
            store._values,
            store._value_mask,
            store._availability_mask,
            store._node_mask,
            store._identities,
        )
        self.metadata = tuple((array.shape, array.dtype) for array in self.arrays)
        self.active = False
        self.source_authentication: tuple[str, str, str, str] | None = None
        contract = TensorContract()
        shape = (len(self.keys), contract.lookback_minutes + 1, 20, len(contract.feature_names))
        if self.metadata != (
            (shape, np.dtype("float64")),
            (shape, np.dtype(bool)),
            (shape, np.dtype(bool)),
            (shape[:-1], np.dtype(bool)),
            ((len(self.keys),), np.dtype("<U64")),
        ) or len(identities) != len(self.keys):
            raise ValueError("preparation raw-store metadata is not canonical")
        previous: datetime | None = None
        for key in self.keys:
            timestamp = _as_utc(datetime.fromisoformat(key))
            if key != timestamp.isoformat() or (previous is not None and timestamp <= previous):
                raise ValueError("preparation timestamps must be canonical and chronological")
            previous = timestamp

    def authenticate(
        self, store: RawTensorStore, keys: tuple[str, ...]
    ) -> tuple[tuple[str, ...], str]:
        if not self.active or store is not self.store or self.source_authentication is None:
            raise TypeError("preparation capability is inactive or belongs to another provider")
        arrays = (
            store._values,
            store._value_mask,
            store._availability_mask,
            store._node_mask,
            store._identities,
        )
        if (
            store.root != self.root
            or store._keys is not self.keys
            or _canonical(store.manifest) != self.manifest
            or store._contract != TensorContract()
            or any(
                actual is not sealed
                or (actual.shape, actual.dtype) != metadata
                or actual.flags.writeable
                for actual, sealed, metadata in zip(arrays, self.arrays, self.metadata, strict=True)
            )
        ):
            raise ValueError("preparation raw-store metadata drifted")
        self.authenticate_publication(self.root)
        positions = tuple(self.index[key] for key in keys)
        if not positions or any(left >= right for left, right in pairwise(positions)):
            raise ValueError("preparation keys must be non-empty, unique and canonically ordered")
        selected = tuple(self.identities[position] for position in positions)
        if any(
            store._index[key] != position or str(store._identities[position]) != identity
            for key, position, identity in zip(keys, positions, selected, strict=True)
        ):
            raise ValueError("preparation selected tensor identity or index drifted")
        return selected, _support_source_identity(self.source_authentication, keys)


@contextmanager
def _preparation_partition_construction(
    store: RawTensorStore,
    corpus: AuthenticatedSupportCorpus,
    support_input: SupportInputCapability,
) -> Iterator[_RawPreparationCapability]:
    capability = store._preparation
    if capability is None or capability.store is not store:
        raise TypeError("preparation requires an unused authenticated raw-store publication")
    store._preparation = None
    try:
        corpus.__post_init__()
        support_input.__post_init__()
        support = support_input.support
        manifest_sha256, child_closure_sha256, _, parent_identity = corpus.authentication
        if (
            support.keys != capability.keys
            or support.manifest_sha256 != manifest_sha256
            or support.child_closure_sha256 != child_closure_sha256
            or support.parent_identity != parent_identity
            or support.source_identity
            != _support_source_identity(corpus.authentication, support.keys)
            or store.manifest["source_identity"] != support.source_identity
            or store.manifest["support_input_identity"] != support_input.identity
        ):
            raise ValueError("preparation corpus or GLOBAL support-input binding drifted")
        capability.source_authentication = corpus.authentication
        capability.active = True
        capability.authenticate(store, support.keys)
        yield capability
    finally:
        capability.active = False
        capability.source_authentication = None


class _PreparationTensorSequence(LazyTensorSequence):
    def __init__(
        self, store: RawTensorStore, keys: tuple[str, ...], capability: _RawPreparationCapability
    ) -> None:
        if not isinstance(capability, _RawPreparationCapability):
            raise TypeError("preparation sequence requires a raw-store publication capability")
        self._store = store
        self._capability = capability
        self._provider = store.__getitem__
        super().__init__(keys, self._provider)

    def _authenticate(
        self, keys: tuple[str, ...], identities: tuple[str, ...], source_identity: str
    ) -> None:
        if not isinstance(self._capability, _RawPreparationCapability):
            raise TypeError("preparation sequence requires a raw-store publication capability")
        if self._factory is not self._provider or self._keys != keys:
            raise ValueError("preparation tensor provider or keys drifted")
        expected_identities, expected_source = self._capability.authenticate(self._store, keys)
        if expected_identities != identities:
            raise ValueError("preparation tensor identities do not match authenticated support")
        if source_identity != expected_source:
            raise ValueError("preparation source identity does not match authenticated support")


def build_raw_tensor_store(
    parent: Path,
    source: Mapping[str, MaskedTensor],
    keys: Sequence[str],
    *,
    support_input_identity: str,
    source_identity: str,
) -> RawTensorStore:
    """Materialise every authorised source timestamp once into bounded mmap arrays."""
    ordered_keys = tuple(keys)
    if not ordered_keys or len(set(ordered_keys)) != len(ordered_keys):
        raise ValueError("raw tensor store requires unique canonical timestamps")
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".raw-tensor-", dir=parent))
    try:
        worker_source_path = staging / "worker-source.bin"
        worker_index = _write_worker_source(source, worker_source_path)
        materialised = _ordered_materialisation(worker_source_path, worker_index, ordered_keys)
        first_key, first, first_pid, first_pss, first_uss = next(materialised)
        shape = (len(ordered_keys), *first.values.shape)
        values = np.lib.format.open_memmap(
            staging / "values.npy", mode="w+", dtype=first.values.dtype, shape=shape
        )
        value_mask = np.lib.format.open_memmap(
            staging / "value-mask.npy", mode="w+", dtype=np.bool_, shape=shape
        )
        availability_mask = np.lib.format.open_memmap(
            staging / "availability-mask.npy", mode="w+", dtype=np.bool_, shape=shape
        )
        node_mask = np.lib.format.open_memmap(
            staging / "node-mask.npy",
            mode="w+",
            dtype=np.bool_,
            shape=(len(ordered_keys), *first.node_mask.shape),
        )
        identities = np.empty(len(ordered_keys), dtype="<U64")
        worker_pids = {first_pid}
        worker_peak_pss_kib = {first_pid: first_pss}
        worker_peak_uss_kib = {first_pid: first_uss}

        def write(index: int, key: str, tensor: MaskedTensor) -> None:
            if key != ordered_keys[index]:
                raise ValueError("raw tensor materialisation order drifted")
            values[index] = tensor.values
            value_mask[index] = tensor.value_mask
            availability_mask[index] = tensor.availability_mask
            node_mask[index] = tensor.node_mask
            identities[index] = _masked_tensor_identity(tensor)

        write(0, first_key, first)
        for offset, (key, tensor, pid, pss_kib, uss_kib) in enumerate(materialised, start=1):
            worker_pids.add(pid)
            worker_peak_pss_kib[pid] = max(worker_peak_pss_kib.get(pid, 0), pss_kib)
            worker_peak_uss_kib[pid] = max(worker_peak_uss_kib.get(pid, 0), uss_kib)
            write(offset, key, tensor)
        worker_source_path.unlink()
        for array in (values, value_mask, availability_mask, node_mask):
            array.flush()
        np.save(
            staging / "timestamps.npy",
            np.asarray(ordered_keys, dtype=f"<U{max(map(len, ordered_keys))}"),
            allow_pickle=False,
        )
        np.save(staging / "tensor-identities.npy", identities, allow_pickle=False)
        files = []
        publication_files: dict[str, tuple[int, ...]] = {}
        for path in sorted(staging.glob("*.npy")):
            publication_files[path.name] = _publication_file_state(path)
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            files.append(
                {
                    "path": path.name,
                    "size": path.stat().st_size,
                    "sha256": _file_digest(path),
                    "semantic_sha256": _semantic_array_digest(array),
                }
            )
        manifest: dict[str, Any] = {
            "schema": _SCHEMA,
            "support_input_identity": support_input_identity,
            "source_identity": source_identity,
            "tensor_contract_identity": first.contract_identity,
            "timestamps": len(ordered_keys),
            "materialisation": {
                "executor": "spawn-process-shared-mmap",
                "maximum_workers": min(_MAX_WORKERS, len(ordered_keys)),
                "observed_worker_processes": len(worker_pids),
                "worker_peak_pss_kib": dict(sorted(worker_peak_pss_kib.items())),
                "worker_peak_uss_kib": dict(sorted(worker_peak_uss_kib.items())),
                "aggregate_worker_peak_pss_kib": sum(worker_peak_pss_kib.values()),
                "aggregate_worker_peak_uss_kib": sum(worker_peak_uss_kib.values()),
                "parent_pss_kib": _private_memory_kib()[0],
                "parent_uss_kib": _private_memory_kib()[1],
            },
            "files": files,
        }
        manifest["store_identity"] = _identity(manifest)
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(_canonical(manifest))
        publication_files[manifest_path.name] = _publication_file_state(manifest_path)
        manifest_bytes = manifest_path.read_bytes()
        if manifest_bytes != _canonical(manifest):
            raise ValueError("raw tensor store manifest publication handoff drifted")
        manifest = json.loads(manifest_bytes)
        # Rename changes directory ctime, but preserves these other identity/metadata fields.
        directory_state = _publication_file_state(staging)[:-1]

        def authenticate_publication(root: Path) -> None:
            if _publication_file_state(root)[:-1] != directory_state or any(
                _publication_file_state(root / name) != state
                for name, state in publication_files.items()
            ):
                raise ValueError("raw tensor store publication handoff drifted")

        for name in publication_files:
            with (staging / name).open("rb") as stream:
                os.fsync(stream.fileno())
        staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(staging_fd)
        finally:
            os.close(staging_fd)
        authenticate_publication(staging)
        destination = parent / manifest["store_identity"]
        _publish_cache_once(staging, destination)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        authenticate_publication(destination)
        store = RawTensorStore(destination, manifest)
        authenticate_publication(destination)
        store._preparation = _RawPreparationCapability(
            store,
            tuple(str(value) for value in identities),
            authenticate_publication,
            token=_RAW_PUBLICATION_CAPABILITY,
        )
        return store
    except BaseException as exc:
        if staging.exists():
            failure_payload = {
                "schema": "R4-P0-RAW-TENSOR-STORE-FAILURE-V1",
                "status": "FAILED",
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "files": [
                    {
                        "path": path.relative_to(staging).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": _file_digest(path),
                    }
                    for path in sorted(staging.rglob("*"))
                    if path.is_file() and not path.is_symlink()
                ],
            }
            failure_payload["failure_identity"] = _identity(failure_payload)
            (staging / "failure.json").write_bytes(_canonical(failure_payload))
        raise


def load_raw_tensor_store(root: Path, *, expected_identity: str | None = None) -> RawTensorStore:
    """Fully authenticate a prepared raw tensor store before exposing mmap views."""
    manifest = json.loads((root / "manifest.json").read_text())
    identity = manifest.pop("store_identity")
    if identity != _identity(manifest) or (
        expected_identity is not None and identity != expected_identity
    ):
        raise ValueError("raw tensor store identity drifted")
    manifest["store_identity"] = identity
    for item in manifest["files"]:
        path = root / item["path"]
        if path.stat().st_size != item["size"] or _file_digest(path) != item["sha256"]:
            raise ValueError("raw tensor store container drifted")
        if (
            _semantic_array_digest(np.load(path, mmap_mode="r", allow_pickle=False))
            != item["semantic_sha256"]
        ):
            raise ValueError("raw tensor store semantic content drifted")
    return RawTensorStore(root, manifest)
