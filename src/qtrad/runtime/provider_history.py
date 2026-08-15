"""Current v3 provider-history facade.

The normal Stage 7 API is v3-only. Retained v1/v2 readers and migration
bridges were removed after the approved retained-evidence invalidation.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.domain.provider_history import ProviderHistoricalAvailabilityPolicy
from qtrad.runtime.provider_history_v3 import (
    DEFAULT_AVAILABILITY_POLICY,
    authenticate_provider_history_v3,
    provider_history_v3_verifier_sha256,
)
from qtrad.runtime.provider_history_v3 import (
    verify_provider_history as verify_provider_history_v3,
)


def provider_history_verifier_sha256() -> str:
    """Return the current v3 claim-scoped verifier identity."""

    return provider_history_v3_verifier_sha256()


def verify_provider_history(
    path: Path,
    *,
    stage6_manifest: Path,
    stage6_receipt: Path,
    receipt_output: Path,
    availability_policy: ProviderHistoricalAvailabilityPolicy = DEFAULT_AVAILABILITY_POLICY,
) -> ProviderHistorySourceEvidence:
    """Deeply verify one current v3 Stage 7 closure."""

    return verify_provider_history_v3(
        path,
        stage6_manifest=stage6_manifest,
        stage6_receipt=stage6_receipt,
        receipt_output=receipt_output,
        availability_policy=availability_policy,
    )


def authenticate_provider_history(
    path: Path,
    *,
    receipt: Path,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> ProviderHistorySourceEvidence:
    """Authenticate one current v3 Stage 7 receipt without parent replay."""

    return authenticate_provider_history_v3(
        path,
        receipt=receipt,
        instrument_ids=instrument_ids,
        interval_start=interval_start,
        interval_end=interval_end,
    )


__all__ = [
    "authenticate_provider_history",
    "provider_history_verifier_sha256",
    "verify_provider_history",
]
