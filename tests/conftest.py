"""Shared test-environment defaults for deterministic identity-bound artefacts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from contextlib import suppress
from hashlib import sha256
from pathlib import Path

import pytest

import qtrad.runtime.r2_verification as _verification

_manifest_filename = re.compile(
    r"^qtrad-test-image-identity-[0-9a-f]{40}-(?P<pid>[1-9][0-9]*)\.json$"
)


def _unlink_manifest(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OverflowError, PermissionError):
        return True
    return True


def _cleanup_stale_manifests(directory: Path) -> None:
    for path in directory.glob("qtrad-test-image-identity-*.json"):
        match = _manifest_filename.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            continue
        if _process_is_alive(int(match.group("pid"))):
            continue
        _unlink_manifest(path)


_commit = subprocess.check_output(("git", "rev-parse", "HEAD"), text=True).strip()
_unsigned = {
    "contract": "qtrad-runtime-image-identity-v1",
    "schema_version": 1,
    "application_commit": _commit,
    "image_digest": "sha256:" + "0" * 64,
}
_manifest = {
    **_unsigned,
    "manifest_sha256": sha256(
        (json.dumps(_unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest(),
}
_manifest_path = (
    Path(tempfile.gettempdir()) / f"qtrad-test-image-identity-{_commit}-{os.getpid()}.json"
)
_unlink_manifest(_manifest_path)
_cleanup_stale_manifests(_manifest_path.parent)
_manifest_path.write_text(
    json.dumps(_manifest, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
_manifest_path.chmod(0o444)
_production_loader = _verification._image_identity_manifest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    unmarked = sorted(
        item.nodeid
        for item in items
        if isinstance(item, pytest.Function)
        and "postgres_database_url" in item.fixturenames
        and item.get_closest_marker("postgres") is None
    )
    if unmarked:
        raise pytest.UsageError(
            "tests using postgres_database_url must carry pytest.mark.postgres: "
            + ", ".join(unmarked)
        )


@pytest.fixture
def postgres_database_url() -> str:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    if database_url is None:
        skip_reason = (
            "QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration; "
            "run ops/dev/verify.sh for the complete local gate"
        )
        pytest.skip(skip_reason)  # ty: ignore[too-many-positional-arguments]
    return database_url


@pytest.fixture(autouse=True)
def _test_image_identity_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _verification,
        "_image_identity_manifest",
        lambda: _production_loader(_manifest_path),
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _unlink_manifest(_manifest_path)
