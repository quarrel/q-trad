"""Shared test-environment defaults for deterministic identity-bound artefacts."""

import os


os.environ.setdefault(
    "QTRAD_IMAGE_DIGEST",
    "sha256:0000000000000000000000000000000000000000000000000000000000000000",
)
