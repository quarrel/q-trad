"""Shared test-environment defaults for deterministic identity-bound artefacts."""

from __future__ import annotations

import os

os.environ.setdefault(
    "QTRAD_IMAGE_DIGEST",
    "sha256:0000000000000000000000000000000000000000000000000000000000000000",
)
