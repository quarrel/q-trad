"""Shared test-environment defaults for deterministic identity-bound artefacts."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path

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
os.environ.setdefault("QTRAD_TEST_MODE", "1")
os.environ.setdefault("QTRAD_IMAGE_IDENTITY_PATH", str(_manifest_path))
