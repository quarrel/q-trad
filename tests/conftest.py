"""Shared test-environment defaults for deterministic identity-bound artefacts."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path

import pytest

import qtrad.runtime.r2_verification as _verification

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
_manifest_path.write_text(
    json.dumps(_manifest, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
_manifest_path.chmod(0o444)
_production_loader = _verification._image_identity_manifest


@pytest.fixture(autouse=True)
def _test_image_identity_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _verification,
        "_image_identity_manifest",
        lambda: _production_loader(_manifest_path),
    )
