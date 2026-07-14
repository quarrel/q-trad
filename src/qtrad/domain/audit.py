"""Canonical raw-audit representation identity."""

from enum import IntEnum


class RawPayloadRepresentation(IntEnum):
    """Stable compact codes describing what one stored raw payload represents."""

    LEGACY_UNCLASSIFIED = 0
    MERGED_STATE = 1
    CHANGED_FIELDS = 2
    FIXTURE = 3
