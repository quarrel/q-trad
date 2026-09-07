"""Authenticated LAB-0 input and causal individual-key linear foundation.

The implementation consumes only the authenticated LAB-0 15-minute target,
feature, context and fold children.  All forecasts are made fold by fold with
the existing LAB-S preprocessing and Ridge equations. Terminal former-holdout
outcomes are intentionally excluded from this foundation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.lab_0.harness import authenticate_manifest
from experiments.r2_historical_lab.lab_s.statistical import (
    DEVELOPMENT_BLOCK_NAMES,
    _evaluate_configuration,
    _raw_prediction,
)
from experiments.r2_historical_lab.lab_s.statistical import (
    _block_specs as _lab_s_block_specs,
)

MANIFEST_PATH = Path("/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json")
MANIFEST_SHA256 = "462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072"
TERMINAL_BLOCK = "TERMINAL_FORMER_HOLDOUT"
DEVELOPMENT_BLOCKS = tuple(DEVELOPMENT_BLOCK_NAMES)
TERMINAL_START = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
_STAGE_WINDOWS: dict[str, tuple[datetime, datetime]] = {
    "DEV_1": (
        datetime(2026, 5, 15, 14, 6, tzinfo=UTC),
        datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
    ),
    "DEV_2": (
        datetime(2026, 5, 29, 14, 6, tzinfo=UTC),
        datetime(2026, 6, 12, 14, 6, tzinfo=UTC),
    ),
    "DEV_3": (
        datetime(2026, 6, 12, 14, 6, tzinfo=UTC),
        TERMINAL_START,
    ),
}
CORE_SIX = (
    "commodity:spot-gold",
    "commodity:us-crude",
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
)

ALL_INSTRUMENTS = (
    "commodity:spot-gold",
    "commodity:spot-silver",
    "commodity:us-crude",
    "fx:aud-usd",
    "fx:eur-jpy",
    "fx:eur-usd",
    "fx:gbp-usd",
    "fx:nzd-usd",
    "fx:usd-cad",
    "fx:usd-chf",
    "fx:usd-jpy",
    "index:australia-200",
    "index:eu-stocks-50",
    "index:ftse-100",
    "index:germany-40",
    "index:hong-kong-hs50",
    "index:japan-225",
    "index:us-500",
    "index:us-tech-100",
    "index:wall-street",
)

FEATURE_SEMANTIC_SHA256 = "61b4955c8b1536e84c80a574a5a10924d6fd235a1e6b70da3b76de43c9b45282"
FULLY_POOLED_CONFIG_ID = "c969d8a78d428aea24e848f3328341ac31efe54db04f1baae0173661fd80bffe"
LOCAL_CONFIG_ID = "64124c5fdb3d66b01338688b3f8283ac663461fa87e3cb815b5a002b34bf6180"
EXPECTED_COUNTS: dict[str, dict[str, int]] = {
    "raw": {"development": 781_650, "terminal": 664_380},
    "valid": {"development": 771_540, "terminal": 655_424},
    "lab_s_support": {"development": 771_140, "terminal": 655_424},
}
EXPECTED_LINEAR: dict[str, dict[str, float | int]] = {
    "development": {
        "support": 771_140,
        "zero_mse": 2.186363710428282e-6,
        "fully_pooled_mse": 2.1861813075256115e-6,
        "direct_delta_mse": -1.8229040302837556e-10,
        "skill": 8.33760693789154e-5,
    },
    "terminal": {
        "support": 655_424,
        "fully_pooled_mse": 1.8223809664366566e-6,
        "direct_delta_mse": 1.5638341114994004e-9,
        "skill": -8.588639044175839e-4,
    },
}
CORE_SIX_EXPECTED: dict[str, float | int] = {
    "support": 239_535,
    "ZERO_RETURN": 0.0000028404586671320294,
    "POOLED_LOCAL_RIDGE": 0.000002841663414474555,
    "LOCAL_RIDGE": 0.0000028481068080631273,
}
CORE_SIX_METRIC_TOLERANCE = 1e-14


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _canonical_identity_json(value: object) -> bytes:
    """Canonical identity bytes shared with the runtime's content hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(UTC)


def _forecast_identity(
    configuration_id: str,
    forecast_fold: str,
    instrument_id: str,
    decision_time: datetime,
) -> str:
    """Return the canonical deterministic identity for one bound forecast."""

    payload = {
        "configuration_id": configuration_id,
        "decision_time": decision_time.isoformat(),
        "forecast_fold": forecast_fold,
        "instrument_id": instrument_id,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _configuration(universe: str, pooling: str) -> dict[str, Any]:
    return {
        "universe": universe,
        "horizon_minutes": 15,
        "feature_set": "P0",
        "feature_semantic_sha256": FEATURE_SEMANTIC_SHA256,
        "pooling": pooling,
        "hierarchical_instrument_penalty_ratio": None,
        "target_scale": "RAW_RETURN",
        "calibration": "RAW",
        "recency": "EXPANDING",
        "ridge_alpha": 1.0,
        "rolling_history_days": 84,
        "decay_half_life_days": 42,
        "calibration_inner_days": 14,
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
    }


_LAB0_SEAL = object()


@dataclass(frozen=True, init=False)
class Lab0Capsule:
    """The authenticated parent identity and exact consumed children."""

    manifest_path: Path
    manifest_sha256: str
    manifest: Mapping[str, Any]
    instruments: tuple[str, ...]
    child_identities: tuple[Mapping[str, Any], ...]
    _sealed: object

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("LAB-0 capsules must be created by authenticate_parent")

    @classmethod
    def _create(cls, token: object, *args: Any, **fields: Any) -> Lab0Capsule:
        if token is not _LAB0_SEAL:
            raise TypeError("LAB-0 capsule seal is private")
        if args:
            names = (
                "manifest_path",
                "manifest_sha256",
                "manifest",
                "instruments",
                "child_identities",
            )
            if fields or len(args) != len(names):
                raise TypeError("LAB-0 capsule fields must be complete and positional or named")
            fields = dict(zip(names, args, strict=True))
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_sealed", token)
        return instance

    @property
    def child_closure_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(list(self.child_identities))).hexdigest()

    @property
    def block_specs(self) -> dict[str, dict[str, Any]]:
        return _lab_s_block_specs(dict(self.manifest))


_DEV_CONTROL_SEAL = object()
_DEV_CONTROL_COLUMNS = (
    "target_id",
    "instrument_id",
    "decision_time",
    "target_available_at",
    "target_valid",
    "block",
    "horizon_minutes",
    "target_return",
)


def _canonical_dev_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (tuple, list)):
        return [_canonical_dev_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _canonical_dev_value(item) for key, item in value.items()}
    return value


def _dev_control_identity(rows: pl.DataFrame) -> tuple[str, str]:
    columns = tuple(rows.columns)
    payload = [
        {column: _canonical_dev_value(value) for column, value in row.items()}
        for row in rows.iter_rows(named=True)
    ]
    content = hashlib.sha256(
        _canonical_identity_json({"columns": columns, "rows": payload})
    ).hexdigest()
    keys = [
        f"{_as_utc(row['decision_time']).isoformat()}|{row['instrument_id']}"
        for row in rows.iter_rows(named=True)
    ]
    key_identity = hashlib.sha256(_canonical_identity_json(keys)).hexdigest()
    return content, key_identity


@dataclass(frozen=True, init=False)
class AuthenticatedDevControlTraining:
    """Sealed, parent-bound development/control rows for terminal fitting."""

    rows: pl.DataFrame
    terminal_start: datetime
    manifest_sha256: str
    child_closure_sha256: str
    columns: tuple[str, ...]
    row_count: int
    content_identity: str
    key_identity: str
    _sealed: object

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("DEV-control training capabilities must be created by authentication")

    @classmethod
    def _create(cls, token: object, **fields: Any) -> AuthenticatedDevControlTraining:
        if token is not _DEV_CONTROL_SEAL:
            raise TypeError("DEV-control training capability seal is private")
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_sealed", token)
        return instance


def _seal_dev_control_training(
    parent: Lab0Capsule,
    rows: pl.DataFrame,
    terminal_start: datetime,
) -> AuthenticatedDevControlTraining:
    """Seal an exact parent-bound DEV/control projection for terminal fitting."""
    if (
        not isinstance(parent, Lab0Capsule)
        or getattr(parent, "_authenticated_parent", None) is not _LAB0_SEAL
    ):
        raise TypeError("DEV-control training requires an authenticated LAB-0 parent")
    if not isinstance(rows, pl.DataFrame):
        raise TypeError("DEV-control training rows must be a Polars DataFrame")
    expected = set(_DEV_CONTROL_COLUMNS)
    from .tensor import P0_FEATURE_NAMES

    expected.update(P0_FEATURE_NAMES)
    if (
        set(rows.columns) != expected
        or tuple(rows.columns[: len(_DEV_CONTROL_COLUMNS)]) != _DEV_CONTROL_COLUMNS
    ):
        raise ValueError("DEV-control training schema is not canonical")
    if rows.is_empty():
        raise ValueError("DEV-control training projection is empty")
    if rows.select(["instrument_id", "decision_time"]).unique().height != rows.height:
        raise ValueError("DEV-control training keys are not unique")
    if rows.get_column("block").is_in(["TRAINING_ONLY", *DEVELOPMENT_BLOCKS]).not_().any():
        raise ValueError("DEV-control training includes an unauthorized block")
    if rows.get_column("target_return").is_null().any():
        raise ValueError("DEV-control training contains null target_return")
    if rows.filter(pl.col("target_available_at") >= terminal_start).height:
        raise ValueError("DEV-control training target is available at or after terminal start")
    ordered = rows.sort(["decision_time", "instrument_id"])
    if not rows.equals(ordered):
        raise ValueError("DEV-control training rows are not in canonical order")
    content_identity, key_identity = _dev_control_identity(rows)
    return AuthenticatedDevControlTraining._create(
        token=_DEV_CONTROL_SEAL,
        rows=rows,
        terminal_start=_as_utc(terminal_start),
        manifest_sha256=parent.manifest_sha256,
        child_closure_sha256=parent.child_closure_sha256,
        columns=tuple(rows.columns),
        row_count=rows.height,
        content_identity=content_identity,
        key_identity=key_identity,
    )


def authenticate_dev_control_training(
    parent: Lab0Capsule,
    rows: pl.DataFrame,
    terminal_start: datetime,
) -> AuthenticatedDevControlTraining:
    """Authenticate DEV/control rows against the exact parent projection, then seal."""
    from .tensor import P0_FEATURE_NAMES

    authoritative = (
        _authenticated_training_rows(parent)
        .filter(
            pl.col("block").is_in(["TRAINING_ONLY", *DEVELOPMENT_BLOCKS])
            & (pl.col("target_available_at") < terminal_start)
        )
        .select([*_DEV_CONTROL_COLUMNS, *P0_FEATURE_NAMES])
        .sort(["decision_time", "instrument_id"])
    )
    if not rows.equals(authoritative):
        raise ValueError("DEV-control training rows differ from authenticated parent")
    return _seal_dev_control_training(parent, rows, terminal_start)


def _validate_dev_control_training(
    capability: AuthenticatedDevControlTraining,
    parent: Lab0Capsule,
    terminal_start: datetime,
) -> pl.DataFrame:
    if (
        not isinstance(capability, AuthenticatedDevControlTraining)
        or capability._sealed is not _DEV_CONTROL_SEAL
    ):
        raise TypeError("terminal prediction requires sealed DEV-control training capability")
    if (
        capability.manifest_sha256 != parent.manifest_sha256
        or capability.child_closure_sha256 != parent.child_closure_sha256
        or capability.terminal_start != _as_utc(terminal_start)
    ):
        raise ValueError("DEV-control training capability parent or boundary drifted")
    rows = capability.rows
    if tuple(rows.columns) != capability.columns or rows.height != capability.row_count:
        raise ValueError("DEV-control training capability schema or height drifted")
    content_identity, key_identity = _dev_control_identity(rows)
    if content_identity != capability.content_identity or key_identity != capability.key_identity:
        raise ValueError("DEV-control training capability identity drifted")
    _seal_dev_control_training(parent, rows, terminal_start)
    return rows


def _part_path(root: Path, reference: Mapping[str, Any]) -> Path:
    path = Path(str(reference["path"])).resolve()
    if root != path.parent and root not in path.parents:
        raise ValueError(f"LAB-0 child escapes authenticated parent root: {path}")
    return path


def authenticate_parent(
    manifest_path: Path = MANIFEST_PATH,
    expected_manifest_sha256: str = MANIFEST_SHA256,
) -> Lab0Capsule:
    """Authenticate LAB-0 and every child consumed by the R4 linear foundation."""

    manifest = authenticate_manifest(manifest_path, expected_manifest_sha256)
    root = manifest_path.parent.resolve()
    instruments = tuple(str(value) for value in manifest["instruments"])
    if instruments != ALL_INSTRUMENTS:
        raise ValueError("LAB-0 universe differs from the fixed R4 twenty-instrument universe")
    if tuple(int(value) for value in manifest["horizons_minutes"]) != (5, 15, 30, 60):
        raise ValueError("LAB-0 horizon declaration differs from the authenticated parent")

    references: list[Mapping[str, Any]] = []
    for reference in cast(list[Mapping[str, Any]], manifest["parts"]):
        kind = str(reference["kind"])
        if kind in {"feature", "context", "fold"} or (
            kind == "target" and int(reference["horizon_minutes"]) == 15
        ):
            references.append(reference)

    if not references:
        raise ValueError("LAB-0 does not declare consumed R4 children")
    identities: list[Mapping[str, Any]] = []
    for reference in references:
        path = _part_path(root, reference)
        expected = str(reference["sha256"])
        observed = _sha256(path)
        if observed != expected:
            raise ValueError(f"LAB-0 child SHA-256 differs: {path}")
        identities.append(
            {
                "kind": str(reference["kind"]),
                "path": str(path),
                "sha256": observed,
                **(
                    {"instrument_id": str(reference["instrument_id"])}
                    if reference.get("instrument_id") is not None
                    else {}
                ),
                **(
                    {"horizon_minutes": int(reference["horizon_minutes"])}
                    if reference.get("horizon_minutes") is not None
                    else {}
                ),
            }
        )
    capsule = Lab0Capsule._create(
        token=_LAB0_SEAL,
        manifest_path=manifest_path.resolve(),
        manifest_sha256=expected_manifest_sha256,
        manifest=manifest,
        instruments=instruments,
        child_identities=tuple(identities),
    )
    object.__setattr__(capsule, "_authenticated_parent", _LAB0_SEAL)
    return capsule


def _references(capsule: Lab0Capsule, kind: str) -> list[Path]:
    """Return authenticated child paths in deterministic order."""
    items = [item for item in capsule.child_identities if item["kind"] == kind]
    if kind == "target":
        items = [item for item in items if int(item["horizon_minutes"]) == 15]
    if not items:
        raise ValueError(f"authenticated LAB-0 has no {kind} children")
    return sorted((Path(str(item["path"])) for item in items), key=lambda value: str(value))


def _target_frame(
    capsule: Lab0Capsule,
    *,
    blocks: Sequence[str] | None = None,
    include_return: bool = True,
) -> pl.DataFrame:
    columns = [
        "target_id",
        "instrument_id",
        "decision_time",
        "target_available_at",
        "target_valid",
        "block",
        "horizon_minutes",
    ]
    if include_return:
        columns.append("target_return")
    scan = pl.scan_parquet(_references(capsule, "target"))
    if blocks is not None:
        scan = scan.filter(pl.col("block").is_in(list(blocks)))
    return scan.select(columns).collect()


def _feature_frame(capsule: Lab0Capsule) -> pl.DataFrame:
    return pl.read_parquet(_references(capsule, "feature"), use_pyarrow=False)


def _context_frame(capsule: Lab0Capsule) -> pl.DataFrame:
    return pl.read_parquet(_references(capsule, "context"), use_pyarrow=False)


class _PreparationParent(Lab0Capsule):
    """Private operation-owned projection, never constructed from supplied rows."""

    _rows: pl.DataFrame
    _parent_binding: bytes


def _preparation_parent(capsule: Lab0Capsule) -> Lab0Capsule:
    if getattr(capsule, "_authenticated_parent", None) is not _LAB0_SEAL:
        raise TypeError("preparation requires an authenticated LAB-0 parent")
    rows = load_parent_rows(capsule)
    prepared = _PreparationParent._create(
        _LAB0_SEAL,
        manifest_path=capsule.manifest_path,
        manifest_sha256=capsule.manifest_sha256,
        manifest=capsule.manifest,
        instruments=capsule.instruments,
        child_identities=capsule.child_identities,
    )
    object.__setattr__(prepared, "_authenticated_parent", _LAB0_SEAL)
    object.__setattr__(prepared, "_rows", rows.clone())
    object.__setattr__(prepared, "_parent_binding", _preparation_parent_binding(capsule))
    return prepared


def _preparation_parent_binding(capsule: Lab0Capsule) -> bytes:
    return _canonical_json(
        [capsule.manifest_sha256, capsule.manifest, capsule.instruments, capsule.child_identities]
    )


def load_parent_rows(
    capsule: Lab0Capsule | None = None,
    *,
    include_terminal: bool = False,
    include_invalid: bool = False,
    include_return: bool = True,
) -> pl.DataFrame:
    """Load authenticated development/training rows from LAB-0.

    Terminal former-holdout outcomes are intentionally outside this foundation.
    """
    if include_terminal:
        raise ValueError("R4.A does not load terminal former-holdout outcomes")
    capsule = capsule or authenticate_parent()
    if isinstance(capsule, _PreparationParent):
        if include_invalid:
            raise ValueError("preparation projection does not contain invalid targets")
        if capsule._parent_binding != _preparation_parent_binding(capsule):
            raise ValueError("preparation parent identity changed")
        # Polars clones share immutable buffers, but frame mutations by a consumer
        # cannot change the private authenticated projection used by the replay.
        return capsule._rows.clone() if include_return else capsule._rows.drop("target_return")
    targets = _target_frame(
        capsule,
        blocks=("TRAINING_ONLY", *DEVELOPMENT_BLOCKS),
        include_return=include_return,
    )
    if not include_invalid:
        targets = targets.filter(pl.col("target_valid"))
    features = _feature_frame(capsule)
    context = _context_frame(capsule)
    key_columns = ["instrument_id", "decision_time"]
    feature_keys = features.select(key_columns)
    context_keys = context.select(key_columns)
    if feature_keys.unique().height != features.height:
        raise ValueError("authenticated LAB-0 feature keys are not unique")
    if context_keys.unique().height != context.height:
        raise ValueError("authenticated LAB-0 context keys are not unique")
    # Context is an authenticated sparse child; one-to-one validation prevents duplication,
    # while absent context remains an explicit null used by the P0 availability semantics.
    totals = context.group_by("decision_time").agg(
        pl.col("current_available").sum().alias("_total_available")
    )
    own = context.select(
        "instrument_id",
        "decision_time",
        pl.col("current_available").alias("_own_available"),
    )
    if own.select(["instrument_id", "decision_time"]).unique().height != own.height:
        raise ValueError("authenticated LAB-0 context keys are not unique")
    features = (
        features.join(totals, on="decision_time", how="left")
        .join(own, on=["instrument_id", "decision_time"], how="left", validate="1:1")
        .with_columns(
            (
                pl.col("_total_available").fill_null(0.0) - pl.col("_own_available").fill_null(0.0)
            ).alias("cross_market_available_count")
        )
        .drop("_total_available", "_own_available")
    )
    target_keys = targets.select(key_columns)
    if target_keys.unique().height != targets.height:
        raise ValueError("authenticated LAB-0 target keys are not unique")
    matched_features = feature_keys.join(target_keys, on=key_columns, how="inner", validate="1:1")
    if matched_features.height != target_keys.height:
        raise ValueError("target/feature key sets are not one-to-one")
    rows = targets.join(features, on=key_columns, how="inner", validate="1:1")
    if rows.height != targets.height:
        raise ValueError("target/feature key sets are not one-to-one")
    return rows.sort(["decision_time", "instrument_id"])


def _authenticated_training_rows(parent: Lab0Capsule) -> pl.DataFrame:
    """Return the valid, outcome-bearing parent projection used for model fitting."""
    training = load_parent_rows(parent, include_return=True)
    if training.filter(pl.col("target_return").is_null()).height:
        raise ValueError("authenticated training projection contains null target returns")
    return training


def _authenticated_history_rows(parent: Lab0Capsule) -> pl.DataFrame:
    """Return an authenticated parent projection that never materialises outcomes."""
    history = load_parent_rows(parent, include_invalid=True, include_return=False)
    if "target_return" in history.columns:
        raise ValueError("outcome-free terminal history unexpectedly contains target_return")
    return history


def terminal_history_bindings(
    parent: Lab0Capsule,
    first_terminal_time: datetime,
) -> dict[str, Any]:
    """Authenticate exact preterminal history and first-decision lookback coverage."""
    from .terminal_support import _canonical_row_value

    boundary = _as_utc(first_terminal_time)
    history = _terminal_history_metadata(parent).filter(pl.col("decision_time") < boundary)
    support_columns = sorted(history.columns)
    history = history.select(support_columns).sort(["decision_time", "instrument_id"])
    history_rows = [
        {column: _canonical_row_value(row[column]) for column in support_columns}
        for row in history.iter_rows(named=True)
    ]
    history_content_identity = hashlib.sha256(
        _canonical_identity_json({"rows": history_rows, "parts": list(parent.child_identities)})
    ).hexdigest()
    lookback_start = boundary - timedelta(minutes=60)
    lookback = history.filter(
        (pl.col("decision_time") >= lookback_start) & (pl.col("decision_time") < boundary)
    ).sort(["decision_time", "instrument_id"])
    lookback_rows = [
        {column: _canonical_row_value(row[column]) for column in support_columns}
        for row in lookback.iter_rows(named=True)
    ]
    expected_keys = tuple(
        f"{(lookback_start + timedelta(minutes=minute)).isoformat()}|{instrument}"
        for minute in range(60)
        for instrument in ALL_INSTRUMENTS
    )
    actual_keys = tuple(
        f"{_as_utc(row['decision_time']).isoformat()}|{row['instrument_id']}"
        for row in lookback.iter_rows(named=True)
    )
    if actual_keys != expected_keys:
        raise ValueError("first terminal lookback must cover exact 20x60 history rows")
    lookback_payload = {
        "boundary": boundary.isoformat(),
        "parts": list(parent.child_identities),
        "rows": lookback_rows,
        "keys": actual_keys,
    }
    first_terminal_lookback_identity = hashlib.sha256(
        _canonical_identity_json(lookback_payload)
    ).hexdigest()
    return {
        "history_content_identity": history_content_identity,
        "history_row_count": history.height,
        "first_terminal_lookback_identity": first_terminal_lookback_identity,
        "first_terminal_lookback_row_count": lookback.height,
        "first_terminal_lookback_instrument_count": len(
            {row["instrument_id"] for row in lookback.iter_rows(named=True)}
        ),
        "first_terminal_lookback_minute_count": lookback.select("decision_time").n_unique(),
    }


_DEVELOPMENT_OUTCOME_COLUMNS = frozenset(
    {
        "target_id",
        "horizon_minutes",
        "target_return",
        "local_forecast",
        "local_residual",
        "sequence_complete",
        "forecast_fold",
        "forecast_identity",
        "evidence_label",
        "source_class",
        "feature_semantic_sha256",
        "latest_feature_bar_end",
        "manifest_sha256",
        "child_closure_sha256",
    }
)


def project_authenticated_support_rows(
    rows: pl.DataFrame,
    capsule: Lab0Capsule | None = None,
    *,
    outcome_free: bool = False,
) -> pl.DataFrame:
    """Authenticate development rows, then expose only outcome-blind support columns."""
    capsule = capsule or authenticate_parent()
    from .tensor import _SUPPORT_ALLOWED_COLUMNS

    unknown = set(rows.columns) - _SUPPORT_ALLOWED_COLUMNS - _DEVELOPMENT_OUTCOME_COLUMNS
    if unknown:
        raise ValueError(
            f"development support projection rejects unknown columns: {sorted(unknown)}"
        )
    if outcome_free:
        authoritative = load_parent_rows(capsule, include_return=False)
    else:
        authoritative = load_parent_rows(capsule)
    required = sorted(_SUPPORT_ALLOWED_COLUMNS)
    if not set(required).issubset(rows.columns) or not set(required).issubset(
        authoritative.columns
    ):
        raise ValueError("development support projection lacks canonical support columns")
    expected = {
        (str(row["instrument_id"]), row["decision_time"]): row
        for row in authoritative.iter_rows(named=True)
    }
    for row in rows.iter_rows(named=True):
        key = (str(row["instrument_id"]), row["decision_time"])
        parent_row = expected.get(key)
        if parent_row is None:
            raise ValueError("development support projection row is not authenticated")
        for column in required:
            if row[column] != parent_row[column]:
                raise ValueError(
                    f"development support projection differs from authenticated parent: {column}"
                )
    return rows.select(required)


def project_authenticated_parent_support_rows(capsule: Lab0Capsule) -> pl.DataFrame:
    """Project authenticated parent rows into the outcome-free support schema."""
    from .tensor import _SUPPORT_ALLOWED_COLUMNS

    if (
        not isinstance(capsule, Lab0Capsule)
        or getattr(capsule, "_authenticated_parent", None) is not _LAB0_SEAL
    ):
        raise TypeError("parent support projection requires an authenticated LAB-0 parent")
    if capsule.manifest_sha256 != MANIFEST_SHA256 or capsule.instruments != ALL_INSTRUMENTS:
        raise ValueError("parent support projection requires the canonical LAB-0 parent")

    parent_rows = load_parent_rows(capsule, include_return=False)
    required = sorted(_SUPPORT_ALLOWED_COLUMNS)
    missing = [column for column in required if column not in parent_rows.columns]
    if missing:
        raise ValueError(f"authenticated parent lacks canonical support columns: {missing}")
    return parent_rows.select(required)


def _expected_population_maps(capsule: Lab0Capsule) -> dict[str, dict[str, dict[str, int]]]:
    """Derive exact authenticated population maps from LAB-0 dispositions."""

    raw_bi: dict[str, int] = {}
    valid_bi: dict[str, int] = {}
    for item in capsule.manifest["counts"]:
        if item["horizon_minutes"] != 15 or item["block"] not in (
            *DEVELOPMENT_BLOCKS,
            TERMINAL_BLOCK,
        ):
            continue
        key = f"{item['block']}::{item['instrument_id']}"
        count = int(item["row_count"])
        raw_bi[key] = raw_bi.get(key, 0) + count
        if item["target_disposition"] == "VALID":
            valid_bi[key] = valid_bi.get(key, 0) + count
    if set(raw_bi) != set(valid_bi):
        raise ValueError("LAB-0 disposition map does not cover valid targets")

    def by_block(values: dict[str, int]) -> dict[str, int]:
        return {
            block: sum(count for key, count in values.items() if key.startswith(f"{block}::"))
            for block in (*DEVELOPMENT_BLOCKS, TERMINAL_BLOCK)
        }

    def by_instrument(values: dict[str, int]) -> dict[str, int]:
        return {
            instrument: sum(
                count for key, count in values.items() if key.endswith(f"::{instrument}")
            )
            for instrument in ALL_INSTRUMENTS
        }

    # The authenticated 15-minute LAB-S contract trims the final 20 DEV_3
    # valid opportunities for each instrument at the terminal maturity boundary.
    maturity_trim = 20
    support_bi = dict(valid_bi)
    for instrument in ALL_INSTRUMENTS:
        key = f"DEV_3::{instrument}"
        if support_bi.get(key) is None or support_bi[key] < maturity_trim:
            raise ValueError("LAB-0 DEV_3 maturity support is not authenticated")
        support_bi[key] -= maturity_trim
    return {
        "raw": {
            "by_block": by_block(raw_bi),
            "by_instrument": by_instrument(raw_bi),
            "by_block_instrument": raw_bi,
        },
        "valid": {
            "by_block": by_block(valid_bi),
            "by_instrument": by_instrument(valid_bi),
            "by_block_instrument": valid_bi,
        },
        "lab_s_support": {
            "by_block": by_block(support_bi),
            "by_instrument": by_instrument(support_bi),
            "by_block_instrument": support_bi,
        },
    }


def count_populations(capsule: Lab0Capsule | None = None) -> dict[str, Any]:
    """Return distinct raw, valid and LAB-S support counts by block/instrument."""

    capsule = capsule or authenticate_parent()
    targets = _target_frame(
        capsule,
        blocks=(*DEVELOPMENT_BLOCKS, TERMINAL_BLOCK),
        include_return=False,
    )
    if targets.select(pl.col("target_id").n_unique()).item() != targets.height:
        raise ValueError("LAB-0 target population is not distinct by target_id")
    dev = pl.col("block").is_in(DEVELOPMENT_BLOCKS)
    mature = pl.col("target_available_at") < pl.lit(TERMINAL_START)
    support = pl.col("target_valid") & ((dev & mature) | (pl.col("block") == TERMINAL_BLOCK))
    result: dict[str, Any] = {}
    for name, expression in (
        ("raw", pl.lit(True)),
        ("valid", pl.col("target_valid")),
        ("lab_s_support", support),
    ):
        frame = targets.filter(expression)
        by_block = {
            str(row["block"]): int(row["len"])
            for row in frame.group_by("block").len().iter_rows(named=True)
        }
        by_instrument = {
            str(row["instrument_id"]): int(row["len"])
            for row in frame.group_by("instrument_id").len().iter_rows(named=True)
        }
        by_block_instrument = {
            f"{row['block']}::{row['instrument_id']}": int(row["len"])
            for row in frame.group_by(["block", "instrument_id"]).len().iter_rows(named=True)
        }
        result[name] = {
            "total": int(frame.height),
            "development": int(frame.filter(dev).height),
            "terminal": int(frame.filter(pl.col("block") == TERMINAL_BLOCK).height),
            "by_block": by_block,
            "by_instrument": by_instrument,
            "by_block_instrument": by_block_instrument,
        }
    expected_maps = _expected_population_maps(capsule)
    observed = {
        name: {
            "development": int(values["development"]),
            "terminal": int(values["terminal"]),
        }
        for name, values in result.items()
    }
    if observed != EXPECTED_COUNTS:
        raise ValueError(f"LAB-0 population counts differ: {observed}")
    for name, expected in expected_maps.items():
        for key in ("by_block", "by_instrument", "by_block_instrument"):
            if result[name][key] != expected[key]:
                raise ValueError(f"LAB-0 {name} {key} map differs from authenticated manifest")
    result["expected"] = EXPECTED_COUNTS
    result["expected_maps"] = expected_maps
    return result


def _linear_configurations() -> tuple[dict[str, Any], dict[str, Any]]:
    local = _configuration("ALL_20", "LOCAL_RIDGE")
    pooled = _configuration("ALL_20", "FULLY_POOLED_RIDGE")
    # Keep the IDs as an authentication gate, not as a lookup substitute.
    import experiments.r2_historical_lab.lab_0.harness as harness

    if harness.configuration_id(local) != LOCAL_CONFIG_ID:
        raise ValueError("canonical LOCAL_RIDGE configuration identity changed")
    if harness.configuration_id(pooled) != FULLY_POOLED_CONFIG_ID:
        raise ValueError("canonical FULLY_POOLED_LOCAL_RIDGE configuration identity changed")
    return local, pooled


def authenticate_core_six_baseline(
    capsule: Lab0Capsule | None = None,
) -> dict[str, Any]:
    """Validate authenticated LAB-0 core-six baseline anchors without outcomes."""
    capsule = capsule or authenticate_parent()
    observed = capsule.manifest["baseline_reconstruction"]["observed"]
    if int(observed["support"]) != int(CORE_SIX_EXPECTED["support"]):
        raise ValueError("authenticated core-six support differs")
    for name in ("ZERO_RETURN", "POOLED_LOCAL_RIDGE", "LOCAL_RIDGE"):
        error = abs(float(observed[name]) - float(CORE_SIX_EXPECTED[name]))
        if error > CORE_SIX_METRIC_TOLERANCE:
            raise ValueError(f"authenticated core-six {name} anchor differs")
    if not (
        float(observed["ZERO_RETURN"])
        < float(observed["POOLED_LOCAL_RIDGE"])
        < float(observed["LOCAL_RIDGE"])
    ):
        raise ValueError("authenticated core-six MSE ordering differs")
    return {
        "manifest_sha256": capsule.manifest_sha256,
        "child_closure_sha256": capsule.child_closure_sha256,
        "support": int(observed["support"]),
        "zero_return_mse": float(observed["ZERO_RETURN"]),
        "pooled_local_ridge_mse": float(observed["POOLED_LOCAL_RIDGE"]),
        "local_ridge_mse": float(observed["LOCAL_RIDGE"]),
        "ordering": "ZERO_RETURN<POOLED_LOCAL_RIDGE<LOCAL_RIDGE",
    }


def reconstruct_linear_controls(
    rows: pl.DataFrame,
    *,
    block_names: Sequence[str] = DEVELOPMENT_BLOCKS,
    require_expected: bool = True,
    capsule: Lab0Capsule | None = None,
) -> dict[str, Any]:
    """Reconstruct all-twenty local and fully pooled Ridge controls."""
    allowed_blocks = {"TRAINING_ONLY", *DEVELOPMENT_BLOCKS}
    if set(block_names) - set(DEVELOPMENT_BLOCKS) or tuple(block_names) != DEVELOPMENT_BLOCKS:
        raise ValueError("R4.A linear reconstruction is development-only")
    if "block" not in rows.columns:
        raise ValueError("R4.A linear reconstruction requires authenticated block labels")
    observed_blocks = {str(value) for value in rows["block"].unique().to_list()}
    if observed_blocks - allowed_blocks:
        raise ValueError("R4.A linear reconstruction contains an unauthorised block")
    core_six = authenticate_core_six_baseline(capsule)

    local_config, pooled_config = _linear_configurations()
    specs = {
        "DEV_1": {"start": "2026-05-15T14:06:00+00:00", "end": "2026-05-29T14:06:00+00:00"},
        "DEV_2": {"start": "2026-05-29T14:06:00+00:00", "end": "2026-06-12T14:06:00+00:00"},
        "DEV_3": {"start": "2026-06-12T14:06:00+00:00", "end": "2026-06-26T14:06:00+00:00"},
        TERMINAL_BLOCK: {"start": "2026-06-26T14:06:00+00:00", "end": "2026-08-01T23:36:00+00:00"},
    }
    local_result, local_folds = _evaluate_configuration(rows, local_config, specs, block_names)
    pooled_result, pooled_folds = _evaluate_configuration(rows, pooled_config, specs, block_names)
    result = {
        "source_class": SOURCE_CLASS,
        "evidence_label": LABEL,
        "core_six_baseline": core_six,
        "blocks": list(block_names),
        "configurations": {
            "LOCAL_RIDGE": {"configuration_id": LOCAL_CONFIG_ID, **local_result},
            "FULLY_POOLED_LOCAL_RIDGE": {
                "configuration_id": FULLY_POOLED_CONFIG_ID,
                **pooled_result,
            },
        },
        "folds": {"LOCAL_RIDGE": local_folds, "FULLY_POOLED_LOCAL_RIDGE": pooled_folds},
    }
    if require_expected and tuple(block_names) == DEVELOPMENT_BLOCKS:
        expected = EXPECTED_LINEAR["development"]
        observed = cast(Mapping[str, Any], result["configurations"]["FULLY_POOLED_LOCAL_RIDGE"])
        if int(observed["support"]) != int(expected["support"]):
            raise ValueError("all-twenty pooled development support differs")
        delta_error = float(observed["direct_delta_mse_versus_zero"]) - float(
            expected["direct_delta_mse"]
        )
        if abs(delta_error) > 1e-13:
            raise ValueError("all-twenty pooled development delta differs")
        derived_skill = -float(observed["direct_delta_mse_versus_zero"]) / float(
            observed["zero_return_instrument_balanced_mse"]
        )
        skill_error = float(observed["skill_versus_zero"]) - derived_skill
        if abs(skill_error) > 1e-10:
            raise ValueError("all-twenty pooled development skill differs")
    return result


def reconstruct_authenticated_linear_forecasts(
    rows: pl.DataFrame,
    *,
    validation_by_period: Mapping[str, pl.DataFrame] | None = None,
    periods: Sequence[str] = DEVELOPMENT_BLOCKS,
    capsule: Lab0Capsule | None = None,
    support_identity: str | None = None,
    support: Any | None = None,
    authenticated_source_rows: pl.DataFrame | None = None,
    terminal_input: Any | None = None,
) -> dict[str, Any]:
    """Reconstruct canonical LAB-S local and fully pooled forecasts per key."""
    if not periods:
        raise ValueError("linear-control reconstruction requires periods")
    if validation_by_period is None:
        raise ValueError("linear-control reconstruction requires authenticated support by period")
    if (
        not isinstance(support_identity, str)
        or len(support_identity) != 64
        or any(character not in "0123456789abcdef" for character in support_identity)
    ):
        raise ValueError("linear-control reconstruction requires sealed support identity")
    active_capsule = capsule or authenticate_parent()
    if support is None or not hasattr(support, "identity"):
        raise ValueError("linear-control reconstruction requires bound support")
    if support.identity != support_identity:
        raise ValueError("linear-control support identity is not bound to support")
    bound_support_identity = active_capsule.manifest.get("support_identity")
    if bound_support_identity is not None and support_identity != bound_support_identity:
        raise ValueError("linear-control support identity is not bound to authenticated parent")
    block_specs = _lab_s_block_specs(dict(active_capsule.manifest))
    local_config, pooled_config = _linear_configurations()
    if authenticated_source_rows is None:
        authenticated_source_rows = load_parent_rows(active_capsule)
    if (
        tuple(rows.columns) != tuple(authenticated_source_rows.columns)
        or rows.height != authenticated_source_rows.height
        or not rows.equals(authenticated_source_rows)
    ):
        raise ValueError("linear-control training rows are not an authenticated parent projection")
    source_rows = authenticated_source_rows
    outputs: dict[str, Any] = {}
    from .tensor import _SUPPORT_ALLOWED_COLUMNS

    for period in periods:
        if period not in block_specs:
            raise ValueError(f"linear-control period is not in authenticated fold blocks: {period}")
        supplied_validation = validation_by_period[period]
        required_columns = {
            "instrument_id",
            "decision_time",
            "target_valid",
            "target_available_at",
        }
        missing_columns = required_columns.difference(supplied_validation.columns)
        if missing_columns:
            raise ValueError(
                f"linear-control support is missing columns for {period}: {sorted(missing_columns)}"
            )
        support_columns = sorted(_SUPPORT_ALLOWED_COLUMNS)
        if not set(support_columns).issubset(supplied_validation.columns):
            raise ValueError(f"linear-control support is not canonical for {period}")
        if period == TERMINAL_BLOCK:
            validation = _project_authenticated_terminal_control_rows(
                supplied_validation.select(support_columns), active_capsule, terminal_input
            )
        else:
            validation = project_authenticated_support_rows(
                supplied_validation.select(support_columns), active_capsule
            )
        if not bool(validation["target_valid"].all()):
            raise ValueError(f"linear-control support contains invalid targets for {period}")
        if (validation["target_available_at"] <= validation["decision_time"]).any():
            raise ValueError(f"linear-control support is not mature for {period}")
        if "block" in validation.columns and validation["block"].n_unique() != 1:
            raise ValueError(f"linear-control support mixes periods for {period}")
        if "block" in validation.columns and str(validation["block"][0]) != period:
            raise ValueError(f"linear-control support period mismatch for {period}")
        counts = validation.group_by("decision_time").agg(
            pl.col("instrument_id").n_unique().alias("instrument_count")
        )
        if counts.is_empty():
            raise ValueError(f"linear-control validation is empty for {period}")
        instrument_counts = validation.group_by("instrument_id").len()
        if (
            instrument_counts.height != len(ALL_INSTRUMENTS)
            or (instrument_counts["len"] <= 0).any()
        ):
            raise ValueError("linear-control support lacks positive instrument coverage")
        if validation.is_empty():
            raise ValueError(f"linear-control validation is empty for {period}")
        fit_time = datetime.fromisoformat(str(block_specs[period]["start"]))
        training = source_rows.filter(
            (pl.col("decision_time") < fit_time) & (pl.col("target_available_at") < fit_time)
        )
        if training.is_empty():
            raise ValueError(f"linear-control training is empty for {period}")
        local_values, _ = _raw_prediction(training, validation, local_config, fit_time)
        pooled_values, _ = _raw_prediction(training, validation, pooled_config, fit_time)
        keys = tuple(
            f"{instrument}|{_as_utc(decision_time).isoformat()}"
            for instrument, decision_time in zip(
                validation["instrument_id"].to_list(),
                validation["decision_time"].to_list(),
                strict=True,
            )
        )
        if len(set(keys)) != len(keys):
            raise ValueError(f"linear-control validation keys are not unique for {period}")
        period_timestamps = {
            _as_utc(value).isoformat() for value in validation["decision_time"].to_list()
        }
        sealed_target_keys = tuple(
            key for key in support.target_keys if key.rsplit("|", 1)[-1] in period_timestamps
        )
        if sealed_target_keys != keys:
            raise ValueError(
                f"linear-control support target keys do not match sealed support for {period}"
            )
        if len(local_values) != len(keys) or len(pooled_values) != len(keys):
            raise ValueError(f"linear-control forecast cardinality differs for {period}")
        local_forecasts = tuple(float(value) for value in local_values)
        pooled_forecasts = tuple(float(value) for value in pooled_values)
        if not all(np.isfinite(value) for value in (*local_forecasts, *pooled_forecasts)):
            raise ValueError(f"linear-control forecasts are not finite for {period}")
        controls = {
            "ZERO_RETURN": [0.0] * len(keys),
            "LOCAL_RIDGE": list(local_forecasts),
            "FULLY_POOLED_LOCAL_RIDGE": list(pooled_forecasts),
        }
        control_identities = {
            name: hashlib.sha256(
                _canonical_identity_json(
                    {
                        "control": name,
                        "policy": "FIXED_LAB_S_LINEAR_CONTROLS",
                        "support_identity": support_identity,
                        "keys": list(keys),
                        "values": values,
                        "manifest_sha256": active_capsule.manifest_sha256,
                        "child_closure_sha256": active_capsule.child_closure_sha256,
                    }
                )
            ).hexdigest()
            for name, values in controls.items()
        }
        period_payload: dict[str, Any] = {
            "period": period,
            "support_identity": support_identity,
            "keys": list(keys),
            "controls": controls,
            "control_identities": control_identities,
            "policy": "FIXED_LAB_S_LINEAR_CONTROLS",
            "manifest_sha256": active_capsule.manifest_sha256,
            "child_closure_sha256": active_capsule.child_closure_sha256,
            "LOCAL_RIDGE": local_forecasts,
            "FULLY_POOLED_LOCAL_RIDGE": pooled_forecasts,
        }
        period_payload["identity"] = hashlib.sha256(
            _canonical_identity_json(period_payload)
        ).hexdigest()
        outputs[period] = period_payload
    identity_payload = {
        "parent_identity": active_capsule.child_closure_sha256,
        "manifest_sha256": active_capsule.manifest_sha256,
        "periods": tuple(periods),
        "outputs": outputs,
        "local_configuration_id": LOCAL_CONFIG_ID,
        "pooled_configuration_id": FULLY_POOLED_CONFIG_ID,
        "support_identity": support_identity,
    }
    outputs["identity"] = hashlib.sha256(_canonical_json(identity_payload)).hexdigest()
    outputs["parent_identity"] = active_capsule.child_closure_sha256
    outputs["manifest_sha256"] = active_capsule.manifest_sha256
    outputs["support_identity"] = support_identity
    return outputs


def _validate_stage_temporal_boundaries(rows: pl.DataFrame) -> None:
    """Require authoritative UTC development stage windows and mature targets."""
    if "decision_time" not in rows.columns or "block" not in rows.columns:
        raise ValueError("OOF rows lack stage boundary columns")
    columns = ["block", "decision_time"]
    has_maturity = "target_available_at" in rows.columns
    if has_maturity:
        columns.append("target_available_at")
    for row in rows.select(columns).iter_rows(named=True):
        block = str(row["block"])
        decision_time = row["decision_time"]
        if not isinstance(decision_time, datetime) or decision_time.tzinfo is None:
            raise ValueError("OOF decision timestamps must be timezone-aware UTC")
        if decision_time.utcoffset() != timedelta(0):
            raise ValueError("OOF decision timestamps must use UTC")
        decision_time = decision_time.astimezone(UTC)
        if block in _STAGE_WINDOWS:
            start, end = _STAGE_WINDOWS[block]
            if not start <= decision_time < end:
                raise ValueError("OOF decision time is outside its authoritative stage window")
            if has_maturity:
                available_at = row["target_available_at"]
                if not isinstance(available_at, datetime) or available_at.tzinfo is None:
                    raise ValueError(
                        "OOF target availability timestamps must be timezone-aware UTC"
                    )
                if available_at.utcoffset() != timedelta(0):
                    raise ValueError("OOF target availability timestamps must use UTC")
                available_at = available_at.astimezone(UTC)
                if not decision_time < available_at < TERMINAL_START:
                    raise ValueError("OOF target availability is outside the mature stage window")
        elif block == "TRAINING_ONLY":
            if decision_time >= _STAGE_WINDOWS["DEV_1"][0]:
                raise ValueError("OOF training row is outside the training stage window")
        else:
            raise ValueError("OOF row has an unauthorised stage")


def _validate_oof_input(frame: pl.DataFrame, fit_time: datetime, *, training: bool) -> None:
    """Fail closed when target or causal feature/dependency metadata is invalid."""

    if frame.is_empty():
        raise ValueError("OOF fold input is empty")
    if frame.filter(pl.col("target_valid").is_null() | ~pl.col("target_valid")).height:
        raise ValueError("OOF input contains invalid targets")
    if frame["target_return"].null_count():
        raise ValueError("OOF input contains non-finite targets")
    target_values = frame["target_return"].to_numpy()
    if not np.isfinite(target_values).all():
        raise ValueError("OOF input contains non-finite targets")
    required = (
        "feature_data_asof",
        "latest_feature_bar_end",
        "return_60s_available",
        "return_300s_available",
        "cross_market_available_count",
        "gap_known_by_cutoff",
    )
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"OOF input missing availability metadata: {missing}")
    bad_interval = frame.filter(
        pl.col("decision_time").is_null()
        | pl.col("feature_data_asof").is_null()
        | pl.col("latest_feature_bar_end").is_null()
        | (pl.col("feature_data_asof") > pl.col("decision_time"))
        | (pl.col("latest_feature_bar_end") > pl.col("decision_time"))
        | (pl.col("latest_feature_bar_end") > pl.col("feature_data_asof"))
    )
    if not bad_interval.is_empty():
        raise ValueError("OOF feature availability interval is invalid")
    for column in required[2:]:
        if frame[column].null_count():
            raise ValueError(f"OOF dependency availability is incomplete: {column}")
    if training:
        bad_cutoff = frame.filter(pl.col("feature_data_asof") >= pl.lit(fit_time))
        if not bad_cutoff.is_empty():
            raise ValueError("OOF training feature availability violates causal cutoff")


def build_oof_residual_foundation(
    rows: pl.DataFrame,
    *,
    block_names: Sequence[str] = DEVELOPMENT_BLOCKS,
    capsule: Lab0Capsule | None = None,
) -> pl.DataFrame:
    """Generate causal local Ridge OOF residuals for development blocks."""
    if tuple(block_names) != DEVELOPMENT_BLOCKS:
        raise ValueError("OOF residual foundation requires all development blocks")
    capsule = capsule or authenticate_parent()
    allowed_blocks = {"TRAINING_ONLY", *DEVELOPMENT_BLOCKS}
    observed_blocks = {str(value) for value in rows["block"].unique().to_list()}
    if observed_blocks - allowed_blocks:
        raise ValueError("OOF rows contain an unauthorised block")
    _validate_stage_temporal_boundaries(rows)
    local_config, _ = _linear_configurations()
    specs = {
        name: {
            "start": (
                "2026-05-15T14:06:00+00:00"
                if name == "DEV_1"
                else "2026-05-29T14:06:00+00:00"
                if name == "DEV_2"
                else "2026-06-12T14:06:00+00:00"
                if name == "DEV_3"
                else "2026-06-26T14:06:00+00:00"
            ),
            "end": "",
        }
        for name in block_names
    }
    pieces: list[pl.DataFrame] = []
    for block in block_names:
        if block not in DEVELOPMENT_BLOCKS:
            raise ValueError("OOF residuals cannot be generated from terminal outcomes")
        fit_time = datetime.fromisoformat(str(specs[block]["start"]))
        validation = rows.filter(
            (pl.col("block") == block) & (pl.col("target_available_at") < pl.lit(TERMINAL_START))
        )
        training = rows.filter(
            (pl.col("decision_time") < pl.lit(fit_time))
            & (pl.col("target_available_at") < pl.lit(fit_time))
            & (pl.col("block") != TERMINAL_BLOCK)
        )
        _validate_oof_input(training, fit_time, training=True)
        _validate_oof_input(validation, fit_time, training=False)
        if validation.is_empty() or training.is_empty():
            raise ValueError(f"OOF fold {block} lacks training or validation rows")
        prediction, _ = _raw_prediction(training, validation, local_config, fit_time)
        if len(prediction) != validation.height or not np.isfinite(prediction).all():
            raise ValueError(f"OOF local forecast failed closed for {block}")
        base = validation.select(
            "target_id",
            "instrument_id",
            "decision_time",
            "horizon_minutes",
            "target_available_at",
            "block",
            "target_return",
        ).with_columns(pl.Series("local_forecast", prediction))
        pieces.append(
            base.with_columns(
                (pl.col("target_return") - pl.col("local_forecast")).alias("local_residual"),
                pl.lit(LOCAL_CONFIG_ID).alias("local_configuration_id"),
                pl.lit(block).alias("forecast_fold"),
                pl.lit(capsule.manifest_sha256).alias("manifest_sha256"),
                pl.lit(capsule.child_closure_sha256).alias("child_closure_sha256"),
                pl.lit(FEATURE_SEMANTIC_SHA256).alias("feature_semantic_sha256"),
                pl.lit(LABEL).alias("evidence_label"),
                pl.lit(SOURCE_CLASS).alias("source_class"),
            ).with_columns(
                pl.struct(["instrument_id", "decision_time"])
                .map_elements(
                    lambda value, fold=block: _forecast_identity(
                        LOCAL_CONFIG_ID,
                        fold,
                        str(value["instrument_id"]),
                        value["decision_time"],
                    ),
                    return_dtype=pl.String,
                )
                .alias("forecast_identity")
            )
        )
    if not pieces:
        raise ValueError("OOF residual foundation is empty")
    result = pl.concat(pieces, how="vertical").sort(["decision_time", "instrument_id"])
    identity_columns = [
        "local_configuration_id",
        "manifest_sha256",
        "child_closure_sha256",
        "feature_semantic_sha256",
        "evidence_label",
        "source_class",
    ]
    if any(result[column].n_unique() != 1 for column in identity_columns):
        raise ValueError("OOF residual identity lineage is inconsistent")
    if result["local_configuration_id"][0] != LOCAL_CONFIG_ID:
        raise ValueError("OOF residual forecast identity differs")
    if result["forecast_identity"].n_unique() != result.height:
        raise ValueError("OOF residual forecast identities are not unique")
    if result.select(pl.col("target_id").n_unique()).item() != result.height:
        raise ValueError("OOF residual identity is not one-to-one")
    return result


_RESIDUAL_CAPSULE_TOKEN = object()
_OOF_CAPSULE_COLUMNS = (
    "target_id",
    "instrument_id",
    "decision_time",
    "horizon_minutes",
    "target_available_at",
    "block",
    "target_return",
    "local_forecast",
    "local_residual",
    "local_configuration_id",
    "forecast_fold",
    "manifest_sha256",
    "child_closure_sha256",
    "feature_semantic_sha256",
    "evidence_label",
    "source_class",
    "forecast_identity",
    "residual_training_membership",
)


def _residual_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _residual_content_digest(rows: pl.DataFrame) -> str:
    canonical_rows = [
        {column: _residual_json_value(row[column]) for column in _OOF_CAPSULE_COLUMNS}
        for row in rows.sort(["decision_time", "instrument_id", "target_id"]).iter_rows(named=True)
    ]
    return hashlib.sha256(_canonical_json(canonical_rows)).hexdigest()


_RESIDUAL_KEY_COLUMNS = ("target_id", "instrument_id", "decision_time")


def _ordered_residual_keys(rows: pl.DataFrame) -> tuple[str, ...]:
    return tuple(
        f"{row['target_id']}|{row['instrument_id']}|{row['decision_time'].isoformat()}"
        for row in rows.sort(["decision_time", "instrument_id", "target_id"]).iter_rows(named=True)
    )


def _coverage_counts(rows: pl.DataFrame) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (str(row["block"]), str(row["instrument_id"]), int(row["len"]))
        for row in (
            rows.group_by(["block", "instrument_id"])
            .len()
            .sort(["block", "instrument_id"])
            .iter_rows(named=True)
        )
    )


def _validate_exact_residual_coverage(actual: pl.DataFrame, expected: pl.DataFrame) -> pl.DataFrame:
    """Require actual residual keys and counts to equal authenticated structural support."""
    for frame, label in ((actual, "residual"), (expected, "authenticated structural")):
        missing = [
            column for column in (*_RESIDUAL_KEY_COLUMNS, "block") if column not in frame.columns
        ]
        if missing:
            raise ValueError(f"{label} coverage lacks required columns: {missing}")
    actual_ordered = actual.sort(["decision_time", "instrument_id", "target_id"])
    expected_ordered = expected.sort(["decision_time", "instrument_id", "target_id"])
    actual_keys = _ordered_residual_keys(actual_ordered)
    expected_keys = _ordered_residual_keys(expected_ordered)
    if len(actual_keys) != len(set(actual_keys)):
        raise ValueError("residual foundation row keys are not unique")
    if len(expected_keys) != len(set(expected_keys)):
        raise ValueError("authenticated structural keys are not unique")
    if actual_keys != expected_keys:
        raise ValueError("residual foundation does not exactly cover authenticated structural keys")
    if _coverage_counts(actual_ordered) != _coverage_counts(expected_ordered):
        raise ValueError("residual foundation structural coverage counts differ")
    return actual_ordered


def _expected_oof_structural_rows(capsule: Lab0Capsule) -> pl.DataFrame:
    """Derive exact causal OOF eligibility from authenticated parent inputs."""
    parent = load_parent_rows(capsule)
    candidates = parent.filter(
        pl.col("block").is_in(DEVELOPMENT_BLOCKS)
        & (pl.col("target_available_at") < pl.lit(TERMINAL_START))
    )
    if candidates.is_empty():
        raise ValueError("authenticated LAB-0 structural OOF support is empty")
    return candidates


def _validate_exact_oof_values(actual: pl.DataFrame, expected: pl.DataFrame) -> None:
    """Require exact equality with the independently recomputed OOF projection."""
    required = set(_OOF_CAPSULE_COLUMNS)
    if not required.issubset(actual.columns) or not required.issubset(expected.columns):
        raise ValueError("authenticated OOF comparison lacks complete provenance columns")
    columns = list(_OOF_CAPSULE_COLUMNS)
    actual_rows = (
        actual.select(columns).sort(["decision_time", "instrument_id", "target_id"]).rows()
    )
    expected_rows = (
        expected.select(columns).sort(["decision_time", "instrument_id", "target_id"]).rows()
    )
    if actual_rows != expected_rows:
        raise ValueError("residual foundation values differ from authenticated OOF computation")


@dataclass(frozen=True, init=False)
class AuthenticatedResidualFoundation:
    """Sealed, content-addressed development OOF residual foundation."""

    rows: pl.DataFrame
    manifest_sha256: str
    child_closure_sha256: str
    local_configuration_id: str
    feature_semantic_sha256: str
    ordered_row_keys: tuple[str, ...]
    stage_counts: tuple[tuple[str, int], ...]
    instrument_counts: tuple[tuple[str, int], ...]
    forecast_identities: tuple[str, ...]
    target_identities: tuple[str, ...]
    chronology_identity: str
    content_identity: str

    def _validate(self) -> None:
        """Recheck mutable frame contents against the sealed identities."""
        if getattr(self, "_sealed", None) is not _RESIDUAL_CAPSULE_TOKEN:
            raise TypeError("authenticated residual foundation is not sealed")
        if not isinstance(self.rows, pl.DataFrame):
            raise TypeError("authenticated residual foundation rows must be a DataFrame")
        missing = [column for column in _OOF_CAPSULE_COLUMNS if column not in self.rows.columns]
        if missing:
            raise ValueError(f"authenticated residual foundation columns changed: {missing}")
        ordered = self.rows.sort(["decision_time", "instrument_id", "target_id"])
        _validate_stage_temporal_boundaries(ordered)
        if set(ordered["instrument_id"].drop_nulls().to_list()) != set(ALL_INSTRUMENTS):
            raise ValueError(
                "authenticated residual foundation requires complete all-twenty coverage"
            )
        for stage in DEVELOPMENT_BLOCKS:
            stage_instruments = set(
                ordered.filter(pl.col("block") == stage)["instrument_id"].drop_nulls().to_list()
            )
            if stage_instruments != set(ALL_INSTRUMENTS):
                raise ValueError("authenticated residual foundation stage coverage changed")
        row_keys = tuple(
            f"{row['target_id']}|{row['instrument_id']}|{row['decision_time'].isoformat()}"
            for row in ordered.iter_rows(named=True)
        )
        if row_keys != self.ordered_row_keys:
            raise ValueError("authenticated residual foundation row keys changed")
        if _residual_content_digest(ordered) != self.content_identity:
            raise ValueError("authenticated residual foundation content digest mismatch")
        for column, expected in (
            ("local_configuration_id", self.local_configuration_id),
            ("manifest_sha256", self.manifest_sha256),
            ("child_closure_sha256", self.child_closure_sha256),
            ("feature_semantic_sha256", self.feature_semantic_sha256),
            ("evidence_label", LABEL),
            ("source_class", SOURCE_CLASS),
        ):
            if ordered[column].n_unique() != 1 or ordered[column][0] != expected:
                raise ValueError(f"authenticated residual foundation {column} identity changed")
        if ordered["target_available_at"].null_count():
            raise ValueError("authenticated residual foundation target availability changed")
        if ordered.filter(
            (pl.col("target_available_at") <= pl.col("decision_time"))
            | (pl.col("target_available_at") >= pl.lit(TERMINAL_START))
        ).height:
            raise ValueError("authenticated residual foundation target availability changed")
        for column in ("target_return", "local_forecast", "local_residual"):
            if not np.isfinite(ordered[column].to_numpy()).all():
                raise ValueError(f"authenticated residual foundation {column} values changed")
        if ordered.filter(
            (pl.col("target_return") - pl.col("local_forecast") - pl.col("local_residual")).abs()
            > 1e-12
        ).height:
            raise ValueError("authenticated residual foundation residual arithmetic changed")
        if tuple(ordered["forecast_identity"].to_list()) != self.forecast_identities:
            raise ValueError("authenticated residual foundation forecast identities changed")
        if tuple(ordered["target_id"].to_list()) != self.target_identities:
            raise ValueError("authenticated residual foundation target identities changed")
        stage_counts = tuple(
            (str(row["block"]), int(row["len"]))
            for row in ordered.group_by("block").len().sort("block").iter_rows(named=True)
        )
        instrument_counts = tuple(
            (str(row["instrument_id"]), int(row["len"]))
            for row in (
                ordered.group_by("instrument_id").len().sort("instrument_id").iter_rows(named=True)
            )
        )
        if stage_counts != self.stage_counts or instrument_counts != self.instrument_counts:
            raise ValueError("authenticated residual foundation cardinalities changed")
        chronology = chronological_memberships(ordered)
        chronology_identity = hashlib.sha256(
            _canonical_json(
                [
                    {
                        "block": row["block"],
                        "membership": row["residual_training_membership"],
                    }
                    for row in chronology.iter_rows(named=True)
                ]
            )
        ).hexdigest()
        if chronology_identity != self.chronology_identity:
            raise ValueError("authenticated residual foundation chronology changed")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("authenticated residual foundations must be created by the factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> AuthenticatedResidualFoundation:
        if token is not _RESIDUAL_CAPSULE_TOKEN:
            raise TypeError("authenticated residual foundation seal is private")
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_sealed", token)
        return instance

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": "R4A_AUTHENTICATED_OOF_RESIDUAL_FOUNDATION",
            "manifest_sha256": self.manifest_sha256,
            "child_closure_sha256": self.child_closure_sha256,
            "local_configuration_id": self.local_configuration_id,
            "feature_semantic_sha256": self.feature_semantic_sha256,
            "ordered_row_keys": list(self.ordered_row_keys),
            "stage_counts": dict(self.stage_counts),
            "instrument_counts": dict(self.instrument_counts),
            "forecast_identities": list(self.forecast_identities),
            "target_identities": list(self.target_identities),
            "chronology_identity": self.chronology_identity,
            "content_identity": self.content_identity,
            "row_count": self.rows.height,
        }


def authenticate_oof_residual_foundation(
    rows: pl.DataFrame,
    *,
    capsule: Lab0Capsule,
    output_path: Path | None = None,
) -> AuthenticatedResidualFoundation:
    """Validate and seal actual causal OOF rows against an authenticated LAB-0 parent."""

    if getattr(capsule, "_authenticated_parent", None) is not _LAB0_SEAL:
        raise TypeError("residual foundation requires an authenticated LAB-0 parent")
    if capsule.manifest_sha256 != MANIFEST_SHA256:
        raise ValueError("residual foundation parent manifest is not canonical")
    if capsule.instruments != ALL_INSTRUMENTS:
        raise ValueError("residual foundation parent universe is not canonical")
    missing = [column for column in _OOF_CAPSULE_COLUMNS[:-1] if column not in rows.columns]
    if "target_available_at" not in rows.columns:
        missing.append("target_available_at")
    if missing:
        raise ValueError(f"residual foundation is missing required columns: {missing}")
    _validate_stage_temporal_boundaries(rows)
    if rows["target_available_at"].null_count():
        raise ValueError("residual foundation target availability is null")
    if rows.is_empty():
        raise ValueError("residual foundation cannot be empty")
    observed_instruments = set(rows["instrument_id"].drop_nulls().to_list())
    if observed_instruments != set(ALL_INSTRUMENTS):
        raise ValueError("residual foundation requires complete all-twenty structural coverage")
    for stage in DEVELOPMENT_BLOCKS:
        stage_instruments = set(
            rows.filter(pl.col("block") == stage)["instrument_id"].drop_nulls().to_list()
        )
        if stage_instruments != set(ALL_INSTRUMENTS):
            raise ValueError("residual foundation requires complete per-stage instrument coverage")
    if rows.filter(~pl.col("block").is_in(DEVELOPMENT_BLOCKS)).height:
        raise ValueError("residual foundation cannot contain terminal or training rows")
    if rows.filter(pl.col("horizon_minutes") != 15).height:
        raise ValueError("residual foundation horizon is not canonical")
    if rows.select(pl.col("target_id").n_unique()).item() != rows.height:
        raise ValueError("residual foundation target identities are not unique")
    key_frame = rows.select(
        pl.concat_str(
            [
                pl.col("target_id"),
                pl.col("instrument_id"),
                pl.col("decision_time").cast(pl.String),
            ],
            separator="|",
        ).alias("_key")
    )
    if key_frame["_key"].n_unique() != rows.height:
        raise ValueError("residual foundation row keys are not unique")
    for column in ("target_return", "local_forecast", "local_residual"):
        values = rows[column].to_numpy()
        if not np.isfinite(values).all():
            raise ValueError(f"residual foundation {column} values must be finite")
    if rows.filter(
        (pl.col("target_available_at") <= pl.col("decision_time"))
        | (pl.col("target_available_at") >= pl.lit(TERMINAL_START))
    ).height:
        raise ValueError("residual foundation target availability is invalid")
    if rows.filter(
        (pl.col("target_return") - pl.col("local_forecast") - pl.col("local_residual")).abs()
        > 1e-12
    ).height:
        raise ValueError("residual foundation residuals do not equal target minus forecast")
    for column, expected in (
        ("local_configuration_id", LOCAL_CONFIG_ID),
        ("manifest_sha256", capsule.manifest_sha256),
        ("child_closure_sha256", capsule.child_closure_sha256),
        ("feature_semantic_sha256", FEATURE_SEMANTIC_SHA256),
        ("evidence_label", LABEL),
        ("source_class", SOURCE_CLASS),
    ):
        if rows[column].n_unique() != 1 or rows[column][0] != expected:
            raise ValueError(f"residual foundation {column} identity is not authenticated")
    if rows.filter(pl.col("forecast_fold") != pl.col("block")).height:
        raise ValueError("residual foundation forecast folds are inconsistent")
    checked = chronological_memberships(rows)
    expected_structural = _expected_oof_structural_rows(capsule)
    ordered = _validate_exact_residual_coverage(checked, expected_structural)
    trusted_input = load_parent_rows(capsule).filter(
        pl.col("target_available_at") < pl.lit(TERMINAL_START)
    )
    trusted = chronological_memberships(
        build_oof_residual_foundation(trusted_input, capsule=capsule)
    )
    trusted = trusted.join(
        expected_structural.select(list(_RESIDUAL_KEY_COLUMNS)),
        on=list(_RESIDUAL_KEY_COLUMNS),
        how="inner",
    )
    _validate_exact_oof_values(ordered, trusted)
    if checked["residual_training_membership"].null_count():
        raise ValueError("residual foundation chronology is incomplete")
    ordered = checked.sort(["decision_time", "instrument_id", "target_id"])
    expected_forecasts = [
        _forecast_identity(
            LOCAL_CONFIG_ID,
            str(row["block"]),
            str(row["instrument_id"]),
            row["decision_time"],
        )
        for row in ordered.iter_rows(named=True)
    ]
    if ordered["forecast_identity"].to_list() != expected_forecasts:
        raise ValueError("residual foundation forecast identities are not canonical")
    ordered_keys = tuple(
        f"{row['target_id']}|{row['instrument_id']}|{row['decision_time'].isoformat()}"
        for row in ordered.iter_rows(named=True)
    )
    stage_counts = tuple(
        (str(row["block"]), int(row["len"]))
        for row in ordered.group_by("block").len().sort("block").iter_rows(named=True)
    )
    instrument_counts = tuple(
        (str(row["instrument_id"]), int(row["len"]))
        for row in (
            ordered.group_by("instrument_id").len().sort("instrument_id").iter_rows(named=True)
        )
    )
    chronology_identity = hashlib.sha256(
        _canonical_json(
            [
                {
                    "block": row["block"],
                    "membership": row["residual_training_membership"],
                }
                for row in ordered.iter_rows(named=True)
            ]
        )
    ).hexdigest()
    content_identity = _residual_content_digest(ordered)
    result = AuthenticatedResidualFoundation._create(
        token=_RESIDUAL_CAPSULE_TOKEN,
        rows=ordered,
        manifest_sha256=capsule.manifest_sha256,
        child_closure_sha256=capsule.child_closure_sha256,
        local_configuration_id=LOCAL_CONFIG_ID,
        feature_semantic_sha256=FEATURE_SEMANTIC_SHA256,
        ordered_row_keys=ordered_keys,
        stage_counts=stage_counts,
        instrument_counts=instrument_counts,
        forecast_identities=tuple(ordered["forecast_identity"].to_list()),
        target_identities=tuple(ordered["target_id"].to_list()),
        chronology_identity=chronology_identity,
        content_identity=content_identity,
    )
    if output_path is not None:
        path = Path(output_path)
        metadata_path = path.with_suffix(path.suffix + ".json")
        if path.exists() or metadata_path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered.write_parquet(path)
        with metadata_path.open("x", encoding="utf-8") as handle:
            json.dump(result.to_dict(), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
    return result


def chronological_memberships(
    residuals: pl.DataFrame,
    *,
    include_terminal: bool = False,
) -> pl.DataFrame:
    """Attach expanding development residual-training memberships."""
    if include_terminal:
        raise ValueError("R4.A chronology excludes terminal former-holdout outcomes")
    if (
        "block" in residuals.columns
        and residuals.filter(~pl.col("block").is_in(DEVELOPMENT_BLOCKS)).height
    ):
        raise ValueError("chronological membership contains an unrecognised block")
    _validate_stage_temporal_boundaries(residuals)
    required = {
        "block",
        "decision_time",
        "local_configuration_id",
        "forecast_fold",
        "forecast_identity",
        "instrument_id",
    }
    if not required.issubset(residuals.columns):
        raise ValueError("residual foundation lacks chronology identity columns")
    if residuals.filter(
        pl.col("forecast_fold").is_null() | (pl.col("forecast_fold") != pl.col("block"))
    ).height:
        raise ValueError("chronology forecast fold does not match block")
    if residuals.filter(
        pl.col("local_configuration_id").is_null()
        | (pl.col("local_configuration_id") != pl.lit(LOCAL_CONFIG_ID))
    ).height:
        raise ValueError("chronology local configuration identity is not canonical")
    expected_identity = residuals.with_columns(
        pl.struct(["block", "instrument_id", "decision_time"])
        .map_elements(
            lambda value: _forecast_identity(
                LOCAL_CONFIG_ID,
                str(value["block"]),
                str(value["instrument_id"]),
                value["decision_time"],
            ),
            return_dtype=pl.String,
        )
        .alias("_expected_forecast_identity")
    )
    if expected_identity.filter(
        pl.col("forecast_identity").is_null()
        | (pl.col("forecast_identity") != pl.col("_expected_forecast_identity"))
    ).height:
        raise ValueError("chronology forecast identity is inconsistent")
    membership = (
        pl.when(pl.col("block") == "DEV_1")
        .then(pl.lit("WARMUP"))
        .when(pl.col("block") == "DEV_2")
        .then(pl.lit("DEV_1"))
        .when(pl.col("block") == "DEV_3")
        .then(pl.lit("DEV_1+DEV_2"))
        .otherwise(pl.lit("INELIGIBLE"))
        .alias("residual_training_membership")
    )
    result = residuals.with_columns(membership)
    if result.filter(pl.col("residual_training_membership") == "INELIGIBLE").height:
        raise ValueError("chronological membership contains an unrecognised block")
    return result


def materialise_development_support(
    rows: pl.DataFrame,
    *,
    lookback_minutes: int = 60,
    output_path: Path | None = None,
    capsule: Lab0Capsule | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Create candidate-independent positive DEV_2/DEV_3 sequence support."""
    capsule = capsule or authenticate_parent()

    if lookback_minutes != 60:
        raise ValueError("R4 support requires the canonical 60-minute lookback")
    if output_path is not None:
        metadata_path = output_path.with_suffix(".json")
        if output_path.exists() or metadata_path.exists():
            raise FileExistsError(
                f"development support is create-only: {output_path} and sidecar {metadata_path}"
            )
    required = {"target_valid", "target_available_at", "block", "instrument_id", "decision_time"}
    if not required.issubset(rows.columns):
        raise ValueError("support materialisation lacks required target or chronology fields")
    from .tensor import P0_FEATURE_NAMES

    authoritative = load_parent_rows(capsule).filter(
        pl.col("target_available_at") < pl.lit(TERMINAL_START)
    )
    comparison_columns = [
        *sorted(required),
        *P0_FEATURE_NAMES,
    ]
    for column in comparison_columns:
        if column not in authoritative.columns:
            raise ValueError("authenticated parent rows lack required support metadata")
    sort_columns = ["decision_time", "instrument_id"]
    if (
        rows.select(comparison_columns).sort(sort_columns).rows()
        != authoritative.select(comparison_columns).sort(sort_columns).rows()
    ):
        raise ValueError("development support rows do not match authenticated parent inputs")
    candidates = rows.filter(
        pl.col("target_valid")
        & pl.col("target_available_at").is_not_null()
        & (pl.col("target_available_at") < pl.lit(TERMINAL_START))
        & pl.col("block").is_in(["DEV_2", "DEV_3"])
    )
    if candidates.is_empty():
        raise ValueError("development support is empty")
    required_metadata = {"feature_data_asof", "feature_available_at", "source_active"}
    if not required_metadata.issubset(rows.columns):
        raise ValueError("development support requires authenticated feature metadata")
    # Authenticated mature target keys are eligible even when their causal history
    # contains unavailable or inactive feature rows.  Tensor construction preserves
    # those gaps as masks; support eligibility is determined by authenticated target
    # validity, maturity, and stage membership above.
    support = candidates
    # Expose only the authenticated, outcome-blind tensor/support schema.
    from .tensor import _SUPPORT_ALLOWED_COLUMNS

    support = project_authenticated_support_rows(
        support.select(sorted(_SUPPORT_ALLOWED_COLUMNS)), capsule
    )
    counts = {
        str(row["instrument_id"]): int(row["len"])
        for row in support.group_by("instrument_id").len().iter_rows(named=True)
    }
    if set(counts) != set(ALL_INSTRUMENTS) or any(value <= 0 for value in counts.values()):
        raise ValueError("development support must be positive for all twenty instruments")
    ordered_keys = support.select("instrument_id", "decision_time").sort(
        ["decision_time", "instrument_id"]
    )
    key_hash = hashlib.sha256(ordered_keys.write_csv().encode()).hexdigest()
    metadata: dict[str, Any] = {
        "contract": "qtrad-r4-p0-development-support-v1",
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "blocks": ["DEV_2", "DEV_3"],
        "lookback_minutes": lookback_minutes,
        "support": support.height,
        "per_instrument": counts,
        "ordered_key_sha256": key_hash,
        "candidate_independent": True,
        "manifest_sha256": capsule.manifest_sha256,
        "child_closure_sha256": capsule.child_closure_sha256,
    }
    if output_path is not None:
        metadata_path = output_path.with_suffix(".json")
        if output_path.exists() or metadata_path.exists():
            raise FileExistsError(
                f"development support is create-only: {output_path} and sidecar {metadata_path}"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        support.write_parquet(output_path)
        metadata_path.write_bytes(_canonical_json(metadata))
    return support, metadata


def _terminal_metadata_features(parent: Lab0Capsule, *, history: bool = False) -> pl.DataFrame:
    """Project causal values to masks natively, before materialising support metadata."""
    from .tensor import P0_FEATURE_NAMES

    features = pl.scan_parquet(_references(parent, "feature"))
    if history:
        # Match load_parent_rows' existing cross-market availability calculation.
        context = pl.scan_parquet(_references(parent, "context")).select(
            "instrument_id", "decision_time", "current_available"
        )
        totals = context.group_by("decision_time").agg(
            pl.col("current_available").sum().alias("_total_available")
        )
        own = context.select(
            "instrument_id", "decision_time", pl.col("current_available").alias("_own_available")
        )
        features = (
            features.join(totals, on="decision_time", how="left")
            .join(own, on=["instrument_id", "decision_time"], how="left", validate="1:1")
            .with_columns(
                (
                    pl.col("_total_available").fill_null(0.0)
                    - pl.col("_own_available").fill_null(0.0)
                ).alias("cross_market_available_count")
            )
        )
    columns = set(features.collect_schema().names())
    node_available = (
        pl.col("feature_data_asof").is_not_null()
        & pl.col("feature_available_at").is_not_null()
        & (pl.col("feature_data_asof") <= pl.col("decision_time"))
        & (pl.col("feature_available_at") <= pl.col("decision_time"))
        & (pl.col("source_active").cast(pl.Float64) > 0.0)
    ).fill_null(False)
    availability = [
        (
            node_available
            & (
                (pl.col(f"{name}_available").cast(pl.Float64) > 0.0)
                if f"{name}_available" in columns
                else pl.lit(True)
            )
        ).fill_null(False)
        for name in P0_FEATURE_NAMES
    ]
    value_masks = [
        available & pl.col(name).cast(pl.Float64).is_finite().fill_null(False)
        for name, available in zip(P0_FEATURE_NAMES, availability, strict=True)
    ]

    invalid_values = [
        available & pl.col(name).is_not_null() & ~pl.col(name).cast(pl.Float64).is_finite()
        for name, available in zip(P0_FEATURE_NAMES, availability, strict=True)
    ]
    invalid_indicators = [
        node_available & pl.col(f"{name}_available").is_null()
        for name in P0_FEATURE_NAMES
        if f"{name}_available" in columns
    ]
    projected = features.select(
        "instrument_id",
        "decision_time",
        "latest_feature_bar_end",
        "feature_data_asof",
        "feature_available_at",
        "source_class",
        "evidence_label",
        pl.col("source_active").cast(pl.Boolean),
        pl.concat_list(value_masks).alias("feature_mask"),
        pl.concat_list(availability).alias("availability_mask"),
        node_available.alias("node_mask"),
        pl.any_horizontal(*invalid_values, *invalid_indicators)
        .fill_null(False)
        .alias("_invalid_available_feature"),
    ).collect()
    if projected["_invalid_available_feature"].any():
        raise ValueError("available terminal feature value or indicator is not finite")
    return projected.drop("_invalid_available_feature")


def _terminal_history_metadata(parent: Lab0Capsule) -> pl.DataFrame:
    """Preserve development history keys and masks without materialising feature values."""
    targets = _target_frame(
        parent, blocks=("TRAINING_ONLY", *DEVELOPMENT_BLOCKS), include_return=False
    )
    features = _terminal_metadata_features(parent, history=True)
    keys = ["instrument_id", "decision_time"]
    if targets.select(keys).unique().height != targets.height:
        raise ValueError("authenticated LAB-0 target keys are not unique")
    if features.select(keys).unique().height != features.height:
        raise ValueError("authenticated LAB-0 feature keys are not unique")
    rows = targets.join(features, on=keys, how="inner", validate="1:1")
    if rows.height != targets.height:
        raise ValueError("target/feature key sets are not one-to-one")
    return rows.sort(["decision_time", "instrument_id"])


def authenticate_terminal_metadata(
    parent: Lab0Capsule | None = None,
    runtime_config: Any | None = None,
    graph: Any | None = None,
) -> Any:
    """Compose sealed, outcome-blind terminal metadata from authenticated LAB-0 children."""
    from .graph import FrozenEconomicGraph, build_fixed_economic_graph
    from .runtime import FrozenRuntimeConfig
    from .tensor import _canonical_bytes
    from .terminal_support import _TERMINAL_METADATA_SEAL, AuthenticatedTerminalMetadata

    authenticated_parent = parent or authenticate_parent()
    if (
        not isinstance(authenticated_parent, Lab0Capsule)
        or getattr(authenticated_parent, "_authenticated_parent", None) is not _LAB0_SEAL
    ):
        raise TypeError("terminal metadata requires an authenticated LAB-0 parent")
    frozen_config = runtime_config or FrozenRuntimeConfig()
    if not isinstance(frozen_config, FrozenRuntimeConfig):
        raise TypeError("terminal metadata requires the frozen runtime configuration")
    canonical_graph = build_fixed_economic_graph()
    frozen_graph = graph or canonical_graph
    if (
        not isinstance(frozen_graph, FrozenEconomicGraph)
        or frozen_graph.identity != canonical_graph.identity
    ):
        raise ValueError("terminal metadata requires the canonical economic graph")
    if frozen_config.fixed_graph_identity != frozen_graph.identity:
        raise ValueError("terminal metadata graph identity is not bound to configuration")
    if frozen_config.manifest_identity != authenticated_parent.manifest_sha256:
        raise ValueError("terminal metadata manifest is not bound to authenticated LAB-0")
    if frozen_config.child_closure_identity != authenticated_parent.child_closure_sha256:
        raise ValueError("terminal metadata closure is not bound to authenticated LAB-0")

    targets = _target_frame(
        authenticated_parent, blocks=(TERMINAL_BLOCK,), include_return=False
    ).filter(pl.col("target_valid"))
    features = _terminal_metadata_features(authenticated_parent)
    context = (
        pl.scan_parquet(_references(authenticated_parent, "context"))
        .select("instrument_id", "decision_time")
        .collect()
    )
    key_columns = ["instrument_id", "decision_time"]
    if targets.select(key_columns).unique().height != targets.height:
        raise ValueError("authenticated terminal target keys are not unique")
    if features.select(key_columns).unique().height != features.height:
        raise ValueError("authenticated LAB-0 feature keys are not unique")
    if context.select(key_columns).unique().height != context.height:
        raise ValueError("authenticated LAB-0 context keys are not unique")
    feature_by_key = {
        (str(row["instrument_id"]), _as_utc(row["decision_time"])): row
        for row in features.iter_rows(named=True)
    }
    context_by_key = {
        (str(row["instrument_id"]), _as_utc(row["decision_time"])): row
        for row in context.iter_rows(named=True)
    }
    terminal = targets.join(features, on=key_columns, how="inner", validate="1:1").sort(
        ["decision_time", "instrument_id"]
    )
    if terminal.height != targets.height:
        raise ValueError("authenticated terminal target/feature children are not one-to-one")
    _linear_configurations()
    feature_schema_identity = hashlib.sha256(
        _canonical_json(
            {
                "columns": features.columns,
                "schema": {key: str(value) for key, value in features.schema.items()},
                "parts": [
                    dict(part)
                    for part in authenticated_parent.child_identities
                    if part["kind"] == "feature"
                ],
            }
        )
    ).hexdigest()
    context_schema_identity = hashlib.sha256(
        _canonical_json(
            {
                "columns": context.columns,
                "schema": {key: str(value) for key, value in context.schema.items()},
                "parts": [
                    dict(part)
                    for part in authenticated_parent.child_identities
                    if part["kind"] == "context"
                ],
            }
        )
    ).hexdigest()

    def value_identity(value: Any) -> Any:
        if isinstance(value, datetime):
            return _as_utc(value).isoformat()
        if isinstance(value, Mapping):
            return {str(key): value_identity(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return tuple(value_identity(item) for item in value)
        if hasattr(value, "item"):
            return value_identity(value.item())
        return value

    rows: list[dict[str, Any]] = []
    for row in terminal.iter_rows(named=True):
        instrument = str(row["instrument_id"])
        timestamp = _as_utc(row["decision_time"])
        key = (instrument, timestamp)
        feature = feature_by_key[key]
        context_row = context_by_key.get(key)
        feature_identity = hashlib.sha256(
            _canonical_json(
                {
                    "schema_identity": feature_schema_identity,
                    "metadata": {
                        column: value_identity(feature[column]) for column in features.columns
                    },
                }
            )
        ).hexdigest()
        context_identity = hashlib.sha256(
            _canonical_json(
                {
                    "key": value_identity(key),
                    "schema_identity": context_schema_identity,
                    "row": (
                        None
                        if context_row is None
                        else {
                            column: value_identity(context_row[column])
                            for column in context.columns
                        }
                    ),
                }
            )
        ).hexdigest()
        latest_bar = (
            None
            if feature["latest_feature_bar_end"] is None
            else _as_utc(feature["latest_feature_bar_end"])
        )
        feature_asof = (
            None if feature["feature_data_asof"] is None else _as_utc(feature["feature_data_asof"])
        )
        feature_available = (
            None
            if feature["feature_available_at"] is None
            else _as_utc(feature["feature_available_at"])
        )
        history_start = timestamp - timedelta(minutes=60)
        forecast_identity = _forecast_identity(
            LOCAL_CONFIG_ID, TERMINAL_BLOCK, instrument, timestamp
        )
        feature_mask = tuple(feature["feature_mask"])
        availability_mask = tuple(feature["availability_mask"])
        row_metadata: dict[str, Any] = {
            "instrument_id": instrument,
            "decision_time": timestamp,
            "block": TERMINAL_BLOCK,
            "target_valid": True,
            "target_available_at": _as_utc(row["target_available_at"]),
            "dependency_start": history_start,
            "dependency_end": latest_bar,
            "feature_data_asof": feature_asof,
            "feature_available_at": feature_available,
            "latest_feature_bar_end": latest_bar,
            "feature_schema_identity": feature_schema_identity,
            "feature_identity": feature_identity,
            "feature_semantic_sha256": FEATURE_SEMANTIC_SHA256,
            "feature_mask": feature_mask,
            "availability_mask": availability_mask,
            "node_mask": feature["node_mask"],
            "context_schema_identity": context_schema_identity,
            "context_identity": context_identity,
            "history_start": history_start,
            "history_end": timestamp,
            "history_identity": hashlib.sha256(
                _canonical_json(
                    {
                        "key": value_identity(key),
                        "start": history_start.isoformat(),
                        "end": timestamp.isoformat(),
                        "feature_identity": feature_identity,
                    }
                )
            ).hexdigest(),
            "source_active": feature["source_active"],
            "source_class": str(feature["source_class"]),
            "evidence_label": str(feature["evidence_label"]),
            "forecast_exists": True,
            "forecast_identity": forecast_identity,
            "graph_identity": frozen_graph.identity,
            "config_identity": frozen_config.identity,
            "manifest_sha256": authenticated_parent.manifest_sha256,
            "child_closure_sha256": authenticated_parent.child_closure_sha256,
            "parent_identity": frozen_config.parent_identity,
        }
        rows.append(row_metadata)
    if not rows:
        raise ValueError("authenticated terminal metadata is empty")
    canonical_rows = [
        {column: value_identity(value) for column, value in row.items()} for row in rows
    ]
    content_identity = hashlib.sha256(_canonical_bytes(canonical_rows)).hexdigest()
    return AuthenticatedTerminalMetadata._create(
        token=_TERMINAL_METADATA_SEAL,
        rows=rows,
        content_identity=content_identity,
        row_count=len(rows),
        parent_identity=frozen_config.parent_identity,
        manifest_sha256=authenticated_parent.manifest_sha256,
        child_closure_sha256=authenticated_parent.child_closure_sha256,
        graph_identity=frozen_graph.identity,
        config_identity=frozen_config.identity,
        forecast_configuration_id=LOCAL_CONFIG_ID,
        forecast_fold=TERMINAL_BLOCK,
    )


def authenticate_terminal_prediction_input(
    *,
    parent: Lab0Capsule,
    runtime_config: Any,
    graph: Any,
    terminal_metadata: Any,
    terminal_capsule: Any,
    preprocessor_identity: str,
    training_capability: AuthenticatedDevControlTraining,
) -> Any:
    """Authenticate terminal feature values and development-only forecasts after support closes."""
    from .graph import FrozenEconomicGraph
    from .runtime import FrozenRuntimeConfig
    from .tensor import P0_FEATURE_NAMES, _canonical_bytes
    from .terminal_support import (
        _TERMINAL_PREDICTION_INPUT_SEAL,
        AuthenticatedTerminalMetadata,
        AuthenticatedTerminalPredictionInput,
        TerminalSupportCapsule,
        TerminalSupportConfig,
        _canonical_row_value,
        build_terminal_support,
    )

    if not isinstance(terminal_metadata, AuthenticatedTerminalMetadata):
        raise TypeError("terminal prediction input requires authenticated terminal metadata")
    if (
        not isinstance(terminal_capsule, TerminalSupportCapsule)
        or terminal_capsule.mode != "PRIMARY"
    ):
        raise TypeError("terminal prediction input requires a closed primary support capsule")
    if terminal_capsule.terminal_metadata_identity != terminal_metadata.content_identity:
        raise ValueError("terminal support capsule is not bound to terminal metadata")
    terminal_capsule._validate()
    if (
        not isinstance(parent, Lab0Capsule)
        or getattr(parent, "_authenticated_parent", None) is not _LAB0_SEAL
    ):
        raise TypeError("terminal prediction input requires an authenticated LAB-0 parent")
    if not isinstance(runtime_config, FrozenRuntimeConfig) or not isinstance(
        graph, FrozenEconomicGraph
    ):
        raise TypeError("terminal prediction input requires frozen runtime and graph capabilities")
    if (
        runtime_config.identity != terminal_capsule.config_identity
        or graph.identity != terminal_capsule.graph_identity
    ):
        raise ValueError("terminal prediction input configuration is not bound to support capsule")
    if (
        runtime_config.manifest_identity != parent.manifest_sha256
        or runtime_config.child_closure_identity != parent.child_closure_sha256
    ):
        raise ValueError("terminal prediction input parent identities are not bound")
    if not isinstance(preprocessor_identity, str) or not preprocessor_identity:
        raise ValueError("terminal prediction input requires preprocessor identity")
    eligible_metadata_times = [
        _as_utc(row["decision_time"])
        for row in terminal_metadata.to_rows()
        if row["target_valid"] is True or row["target_valid"] == 1
    ]
    if not eligible_metadata_times:
        raise ValueError("terminal prediction input has no eligible terminal decisions")
    history_bindings = terminal_history_bindings(parent, min(eligible_metadata_times))
    for name, expected in history_bindings.items():
        if getattr(terminal_capsule, name) != expected:
            raise ValueError(f"terminal support capsule {name} mismatch")
    expected_terminal_capsule = build_terminal_support(
        terminal_metadata.to_rows(),
        config=TerminalSupportConfig.from_authenticated_parent(parent, runtime_config, graph),
        output_path=None,
        development_register_closed=True,
        authenticated_metadata=terminal_metadata,
        **history_bindings,
    )
    if terminal_capsule.to_dict() != expected_terminal_capsule.to_dict():
        raise ValueError("terminal support capsule differs from authenticated canonical support")

    targets = _target_frame(parent, blocks=(TERMINAL_BLOCK,), include_return=False).filter(
        pl.col("target_valid")
    )
    features = _feature_frame(parent)
    terminal = targets.join(
        features, on=["instrument_id", "decision_time"], how="inner", validate="1:1"
    )
    metadata_by_key = {
        (str(row["instrument_id"]), _as_utc(row["decision_time"])): row
        for row in terminal_metadata.to_rows()
    }
    metadata_keys_ordered = tuple(
        f"{decision_time.isoformat()}|{instrument}"
        for instrument, decision_time in sorted(metadata_by_key, key=lambda key: (key[1], key[0]))
    )
    capsule_keys = tuple(str(key) for key in terminal_capsule.keys)
    metadata_key_set = set(metadata_keys_ordered)
    if any(key not in metadata_key_set for key in capsule_keys):
        raise ValueError("terminal support capsule contains unauthenticated metadata keys")
    metadata_positions = {key: index for index, key in enumerate(metadata_keys_ordered)}
    if tuple(sorted(capsule_keys, key=lambda key: metadata_positions[key])) != capsule_keys:
        raise ValueError(
            "terminal support capsule ordered keys do not match authenticated metadata"
        )
    capsule_key_set = set(capsule_keys)
    terminal = terminal.filter(
        pl.struct(["instrument_id", "decision_time"]).map_elements(
            lambda value: (
                f"{_as_utc(value['decision_time']).isoformat()}|{value['instrument_id']}"
                in capsule_key_set
            ),
            return_dtype=pl.Boolean,
        )
    ).sort(["decision_time", "instrument_id"])
    if terminal.height != len(capsule_keys):
        raise ValueError("terminal prediction input keys do not match support capsule")
    capsule_times = {}
    for key in capsule_keys:
        timestamp, instrument = key.rsplit("|", 1)
        capsule_times.setdefault(timestamp, set()).add(instrument)
    if not capsule_times or any(not instruments for instruments in capsule_times.values()):
        raise ValueError("terminal support capsule has no eligible target keys")
    terminal_keys = tuple(
        f"{_as_utc(row['decision_time']).isoformat()}|{row['instrument_id']}"
        for row in terminal.iter_rows(named=True)
    )
    if set(capsule_times) != {key.rsplit("|", 1)[0] for key in terminal_keys}:
        raise ValueError("terminal prediction input timestamps do not match support capsule")
    if terminal_keys != capsule_keys:
        raise ValueError("terminal prediction input ordered keys do not match support capsule")
    training = _validate_dev_control_training(training_capability, parent, TERMINAL_START)
    local_config, _ = _linear_configurations()
    forecasts, _ = _raw_prediction(training, terminal, local_config, TERMINAL_START)
    if len(forecasts) != terminal.height or not np.isfinite(forecasts).all():
        raise ValueError("terminal prediction forecasts are incomplete")

    rows: list[dict[str, Any]] = []
    forecast_items: list[tuple[str, float]] = []
    for source_row, forecast in zip(terminal.iter_rows(named=True), forecasts, strict=True):
        key = (str(source_row["instrument_id"]), _as_utc(source_row["decision_time"]))
        metadata_row = metadata_by_key.get(key)
        if metadata_row is None:
            raise ValueError("terminal prediction feature key is not authenticated")
        row = dict(metadata_row)
        row.update({name: source_row[name] for name in P0_FEATURE_NAMES})
        row["source_active"] = bool(source_row["source_active"])
        rows.append(row)
        forecast_items.append((f"{key[0]}|{key[1].isoformat()}", float(forecast)))
    payload = {
        "rows": [
            {column: _canonical_row_value(value) for column, value in row.items()} for row in rows
        ],
        "forecasts": tuple(forecast_items),
        "terminal_metadata_identity": terminal_metadata.content_identity,
        "terminal_support_identity": terminal_capsule.artifact_identity,
        "parent_identity": runtime_config.parent_identity,
        "manifest_sha256": parent.manifest_sha256,
        "child_closure_sha256": parent.child_closure_sha256,
        "graph_identity": graph.identity,
        "config_identity": runtime_config.identity,
        "preprocessor_identity": preprocessor_identity,
        "history_identity": history_bindings["history_content_identity"],
        **history_bindings,
    }
    content_identity = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return AuthenticatedTerminalPredictionInput._create(
        token=_TERMINAL_PREDICTION_INPUT_SEAL,
        rows=rows,
        forecasts=tuple(forecast_items),
        content_identity=content_identity,
        terminal_metadata_identity=terminal_metadata.content_identity,
        terminal_support_identity=terminal_capsule.artifact_identity,
        parent_identity=runtime_config.parent_identity,
        manifest_sha256=parent.manifest_sha256,
        child_closure_sha256=parent.child_closure_sha256,
        graph_identity=graph.identity,
        config_identity=runtime_config.identity,
        preprocessor_identity=preprocessor_identity,
        history_identity=history_bindings["history_content_identity"],
        history_content_identity=history_bindings["history_content_identity"],
        history_row_count=history_bindings["history_row_count"],
        first_terminal_lookback_identity=history_bindings["first_terminal_lookback_identity"],
        first_terminal_lookback_row_count=history_bindings["first_terminal_lookback_row_count"],
        first_terminal_lookback_instrument_count=history_bindings[
            "first_terminal_lookback_instrument_count"
        ],
        first_terminal_lookback_minute_count=history_bindings[
            "first_terminal_lookback_minute_count"
        ],
    )


def _project_authenticated_terminal_control_rows(
    rows: pl.DataFrame, parent: Lab0Capsule, terminal_input: Any
) -> pl.DataFrame:
    """Use the sealed prediction projection without opening terminal outcomes."""
    from .tensor import _SUPPORT_ALLOWED_COLUMNS
    from .terminal_support import AuthenticatedTerminalPredictionInput

    if not isinstance(terminal_input, AuthenticatedTerminalPredictionInput):
        raise TypeError("terminal controls require authenticated prediction input")
    terminal_input._validate()
    if (
        terminal_input.manifest_sha256 != parent.manifest_sha256
        or terminal_input.child_closure_sha256 != parent.child_closure_sha256
    ):
        raise ValueError("terminal controls are not bound to the authenticated parent")
    required = sorted(_SUPPORT_ALLOWED_COLUMNS)
    authoritative = pl.DataFrame(terminal_input.to_rows()).select(required)
    if not rows.equals(authoritative):
        raise ValueError("terminal control rows differ from authenticated prediction input")
    return authoritative


def _reconstruct_full_lab_terminal_regression(
    parent: Lab0Capsule, training: pl.DataFrame, targets: pl.DataFrame
) -> dict[str, Any]:
    """Reconstruct the separate full-LAB regression after the outcome gate.

    The aggregation caller authenticates all terminal predictions before supplying
    the complete authenticated valid terminal target frame.
    """
    features = _feature_frame(parent)
    context = _context_frame(parent)
    keys = ["instrument_id", "decision_time"]
    if features.select(keys).is_duplicated().any() or context.select(keys).is_duplicated().any():
        raise ValueError("terminal regression feature/context keys are duplicated")
    totals = context.group_by("decision_time").agg(
        pl.col("current_available").sum().alias("_total_available")
    )
    own = context.select(*keys, pl.col("current_available").alias("_own_available"))
    features = (
        features.join(totals, on="decision_time", how="left")
        .join(own, on=keys, how="left", validate="1:1")
        .with_columns(
            (
                pl.col("_total_available").fill_null(0.0) - pl.col("_own_available").fill_null(0.0)
            ).alias("cross_market_available_count")
        )
        .drop("_total_available", "_own_available")
    )
    validation = targets.join(features, on=keys, how="inner", validate="1:1").sort(
        ["decision_time", "instrument_id"]
    )
    expected = EXPECTED_LINEAR["terminal"]
    if validation.height != targets.height or validation.height != int(expected["support"]):
        raise ValueError("full-LAB terminal regression instrument-row population differs")
    if set(validation["instrument_id"].unique().to_list()) != set(ALL_INSTRUMENTS):
        raise ValueError("full-LAB terminal regression lacks all-twenty support")
    fit_time = datetime.fromisoformat(
        str(_lab_s_block_specs(dict(parent.manifest))[TERMINAL_BLOCK]["start"])
    )
    training = training.filter(
        (pl.col("decision_time") < fit_time) & (pl.col("target_available_at") < fit_time)
    )
    if training.is_empty():
        raise ValueError("full-LAB terminal regression training is empty")
    _, pooled_config = _linear_configurations()
    pooled_values, _ = _raw_prediction(training, validation, pooled_config, fit_time)
    from .evaluation import instrument_balanced_mse

    target_values = validation["target_return"].to_list()
    instruments = validation["instrument_id"].to_list()
    zero_mse = instrument_balanced_mse(
        [
            {"instrument_id": instrument, "target_return": target, "forecast": 0.0}
            for instrument, target in zip(instruments, target_values, strict=True)
        ]
    )
    pooled_mse = instrument_balanced_mse(
        [
            {"instrument_id": instrument, "target_return": target, "forecast": float(prediction)}
            for instrument, target, prediction in zip(
                instruments, target_values, pooled_values, strict=True
            )
        ]
    )
    delta = pooled_mse - zero_mse
    skill = -delta / zero_mse
    if (
        abs(pooled_mse - float(expected["fully_pooled_mse"])) > 1e-12
        or abs(delta - float(expected["direct_delta_mse"])) > 1e-12
        or abs(skill - float(expected["skill"])) > 1e-10
    ):
        raise ValueError("full-LAB terminal control regression anchors differ")
    result = {
        "population": "FULL_LAB_VALID_TERMINAL_INSTRUMENT_ROWS",
        "support": validation.height,
        "configuration_id": FULLY_POOLED_CONFIG_ID,
        "zero_return_instrument_balanced_mse": zero_mse,
        "fully_pooled_mse": pooled_mse,
        "direct_delta_mse": delta,
        "skill": skill,
        "manifest_sha256": parent.manifest_sha256,
        "child_closure_sha256": parent.child_closure_sha256,
    }
    result["identity"] = hashlib.sha256(_canonical_identity_json(result)).hexdigest()
    return result
