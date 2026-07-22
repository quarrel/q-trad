"""Strict capture deployment-descriptor loading at the runtime boundary."""

import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from qtrad.runtime.universe import load_capture_universe

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = re.compile(r"^[0-9a-f]{4,32}$")


@dataclass(frozen=True, slots=True)
class CaptureDeploymentDescriptor:
    """Immutable identities required by one capture deployment."""

    name: str
    application_commit: str
    application_image: str
    universe_file: str
    universe_configuration_hash: str
    universe_instrument_count: int
    schema_head: str
    rollback_release_commit: str
    rollback_application_image: str
    rollback_universe_name: str

    def to_json(self) -> str:
        """Return a stable, non-secret representation for operator scripts."""

        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


def load_capture_deployment_descriptor(
    path: Path, *, repository_root: Path
) -> CaptureDeploymentDescriptor:
    """Load a descriptor and prove its referenced universe identity."""

    root = repository_root.resolve()
    descriptor_path = path.resolve()
    if descriptor_path.parent != (root / "config").resolve():
        raise ValueError("deployment descriptor must be directly inside repository config")
    document = tomllib.loads(descriptor_path.read_text())
    required = {
        "name",
        "application_commit",
        "application_image",
        "universe_file",
        "universe_configuration_hash",
        "schema_head",
        "rollback",
    }
    if set(document) != required:
        raise ValueError("deployment descriptor fields do not match the required contract")
    rollback = document["rollback"]
    if not isinstance(rollback, dict) or set(rollback) != {
        "release_commit",
        "application_image",
        "universe_name",
    }:
        raise ValueError("deployment rollback fields do not match the required contract")

    name = _required_string(document, "name")
    application_commit = _required_string(document, "application_commit")
    application_image = _required_string(document, "application_image")
    universe_file = _required_string(document, "universe_file")
    universe_hash = _required_string(document, "universe_configuration_hash")
    schema_head = _required_string(document, "schema_head")
    rollback_document = cast(dict[str, object], rollback)
    rollback_release_commit = _required_string(rollback_document, "release_commit")
    rollback_application_image = _required_string(rollback_document, "application_image")
    rollback_universe_name = _required_string(rollback_document, "universe_name")

    if not _NAME.fullmatch(name):
        raise ValueError("deployment name is invalid")
    if not _COMMIT.fullmatch(application_commit):
        raise ValueError("application commit must be a full lower-case Git commit")
    if not _IMAGE.fullmatch(application_image):
        raise ValueError("application image must use an immutable sha256 digest")
    universe_path = PurePosixPath(universe_file)
    if (
        universe_path.is_absolute()
        or universe_path.parent != PurePosixPath("config")
        or universe_path.suffix != ".toml"
    ):
        raise ValueError("universe file must be a TOML file directly inside config")
    if not _SHA256.fullmatch(universe_hash):
        raise ValueError("universe configuration hash must be lower-case SHA-256")
    if not _SCHEMA.fullmatch(schema_head):
        raise ValueError("schema head is invalid")
    if not _COMMIT.fullmatch(rollback_release_commit):
        raise ValueError("rollback release commit must be a full lower-case Git commit")
    if not _IMAGE.fullmatch(rollback_application_image):
        raise ValueError("rollback image must use an immutable sha256 digest")
    if not _NAME.fullmatch(rollback_universe_name):
        raise ValueError("rollback universe name is invalid")

    resolved_universe = (root / universe_path).resolve()
    if resolved_universe.parent != (root / "config").resolve():
        raise ValueError("universe file escaped repository config")
    universe = load_capture_universe(resolved_universe)
    if universe.name != name:
        raise ValueError("descriptor name does not match universe name")
    if universe.configuration_hash != universe_hash:
        raise ValueError("descriptor hash does not match universe configuration")

    return CaptureDeploymentDescriptor(
        name=name,
        application_commit=application_commit,
        application_image=application_image,
        universe_file=universe_file,
        universe_configuration_hash=universe_hash,
        universe_instrument_count=len(universe.instruments),
        schema_head=schema_head,
        rollback_release_commit=rollback_release_commit,
        rollback_application_image=rollback_application_image,
        rollback_universe_name=rollback_universe_name,
    )


def _required_string(document: dict[str, object], field: str) -> str:
    value = document[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value
