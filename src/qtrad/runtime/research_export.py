"""Canonical evidence attached to immutable research exports."""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import MarketBar

_MAX_GAP_EVIDENCE_ROWS = 5000


def research_export_metadata(
    *,
    universe_name: str,
    configuration_hash: str,
    instrument_ids: Sequence[InstrumentId],
    interval_start: datetime,
    interval_end: datetime,
    bars: Sequence[MarketBar],
    live_gaps: Sequence[Mapping[str, object]],
    historical_coverage: Sequence[Mapping[str, object]],
    application_version: str,
    application_image: str,
) -> dict[str, JsonValue]:
    """Build bounded, standalone coverage and gap evidence for one bars export."""

    expected = {str(instrument_id) for instrument_id in instrument_ids}
    if not expected:
        raise ValueError("research export requires an explicit instrument universe")
    if not universe_name or len(universe_name) > 64:
        raise ValueError("research export universe name is required")
    if len(configuration_hash) != 64 or any(
        character not in "0123456789abcdef" for character in configuration_hash
    ):
        raise ValueError("research export configuration hash must be lower-case SHA-256")
    interval_start = _datetime(interval_start)
    interval_end = _datetime(interval_end)
    if interval_end <= interval_start:
        raise ValueError("research export interval end must follow its start")
    unexpected_bars = sorted({str(bar.instrument_id) for bar in bars} - expected)
    if unexpected_bars:
        raise ValueError(f"research export contains bars outside its universe: {unexpected_bars}")
    if any(bar.interval_start < interval_start or bar.interval_end > interval_end for bar in bars):
        raise ValueError("research export contains bars outside its requested interval")
    if len(live_gaps) > _MAX_GAP_EVIDENCE_ROWS:
        raise ValueError("research export live-gap evidence exceeds its bounded limit")
    if len(historical_coverage) > _MAX_GAP_EVIDENCE_ROWS:
        raise ValueError("research export historical-coverage evidence exceeds its bounded limit")
    if not application_version or len(application_version) > 64:
        raise ValueError("research export application version is required")
    if not application_image or len(application_image) > 500:
        raise ValueError("research export application image identity is required")

    live_records = sorted(
        (_live_gap_row(row, expected, interval_start, interval_end) for row in live_gaps),
        key=lambda row: (str(row["instrument_id"]), str(row["interval_start"]), str(row["gap_id"])),
    )
    historical_records = sorted(
        (
            _historical_coverage_row(row, expected, interval_start, interval_end)
            for row in historical_coverage
        ),
        key=lambda row: (
            str(row["instrument_id"]),
            str(row["interval_start"]),
            str(row["basis"]),
            str(row["detected_by_plan_hash"]),
        ),
    )
    provenance_counts = Counter(bar.provenance.value for bar in bars)
    basis_counts = Counter(bar.basis.value for bar in bars)
    expected_values: list[JsonValue] = [instrument_id for instrument_id in sorted(expected)]
    live_values: list[JsonValue] = [record for record in live_records]
    historical_values: list[JsonValue] = [record for record in historical_records]
    universe: dict[str, JsonValue] = {
        "name": universe_name,
        "configuration_hash": configuration_hash,
        "instrument_ids": expected_values,
    }
    live_summary: dict[str, JsonValue] = {
        "count": len(live_records),
        "open_count": sum(record["repaired_at"] is None for record in live_records),
        "sha256": _sha256_json(live_records),
        "records": live_values,
    }
    historical_summary: dict[str, JsonValue] = {
        "count": len(historical_records),
        "open_count": sum(record["covered_at"] is None for record in historical_records),
        "sha256": _sha256_json(historical_records),
        "records": historical_values,
    }
    payload: dict[str, JsonValue] = {
        "manifest_contract": "qtrad-research-bars-v2",
        "application_version": application_version,
        "application_image": application_image,
        "requested_interval": {
            "start": _utc_text(interval_start),
            "end": _utc_text(interval_end),
        },
        "universe": universe,
        "bar_coverage": _bar_coverage(bars),
        "provenance_counts": dict(sorted(provenance_counts.items())),
        "basis_counts": dict(sorted(basis_counts.items())),
        "live_gaps": live_summary,
        "historical_coverage": historical_summary,
    }
    return payload


def _bar_coverage(bars: Sequence[MarketBar]) -> list[JsonValue]:
    groups: dict[tuple[str, str, str, str], list[MarketBar]] = {}
    for bar in bars:
        key = (
            str(bar.instrument_id),
            bar.basis.value,
            bar.provenance.value,
            str(bar.source_listing_id),
        )
        groups.setdefault(key, []).append(bar)
    return [
        {
            "instrument_id": key[0],
            "basis": key[1],
            "provenance": key[2],
            "source_listing_id": key[3],
            "interval_start": _utc_text(min(bar.interval_start for bar in group)),
            "interval_end": _utc_text(max(bar.interval_end for bar in group)),
            "row_count": len(group),
            "maximum_revision": max(bar.revision for bar in group),
        }
        for key, group in sorted(groups.items())
    ]


def _live_gap_row(
    row: Mapping[str, object],
    expected: set[str],
    requested_start: datetime,
    requested_end: datetime,
) -> dict[str, JsonValue]:
    instrument_id = str(row["instrument_id"])
    _require_expected_instrument(instrument_id, expected, "live gap")
    interval_start = _datetime(row["interval_start"])
    interval_end = _datetime(row["interval_end"])
    _require_overlap(interval_start, interval_end, requested_start, requested_end, "live gap")
    return {
        "gap_id": str(row["gap_id"]),
        "instrument_id": instrument_id,
        "interval_start": _utc_text(interval_start),
        "interval_end": _utc_text(interval_end),
        "reason": str(row["reason"]),
        "detected_at": _utc_text(_datetime(row["detected_at"])),
        "repaired_at": _optional_time(row["repaired_at"]),
    }


def _historical_coverage_row(
    row: Mapping[str, object],
    expected: set[str],
    requested_start: datetime,
    requested_end: datetime,
) -> dict[str, JsonValue]:
    instrument_id = str(row["instrument_id"])
    _require_expected_instrument(instrument_id, expected, "historical coverage")
    interval_start = _datetime(row["interval_start"])
    interval_end = _datetime(row["interval_end"])
    _require_overlap(
        interval_start,
        interval_end,
        requested_start,
        requested_end,
        "historical coverage",
    )
    returned_points = row["returned_points"]
    if returned_points is not None and not isinstance(returned_points, int):
        raise TypeError("historical request returned points must be an integer")
    observed_points = row["observed_points"]
    if observed_points is not None and not isinstance(observed_points, int):
        raise TypeError("historical coverage observed points must be an integer")
    return {
        "instrument_id": instrument_id,
        "source_listing_id": (
            f"{row['source_provider']}:{row['source_environment']}:{row['source_external_id']}"
        ),
        "source_listing_valid_from": _utc_text(_datetime(row["source_listing_valid_from"])),
        "source_listing_metadata_version": str(row["source_listing_metadata_version"]),
        "provenance": str(row["provenance"]),
        "basis": str(row["basis"]),
        "resolution": str(row["resolution"]),
        "interval_start": _utc_text(interval_start),
        "interval_end": _utc_text(interval_end),
        "detected_at": _utc_text(_datetime(row["detected_at"])),
        "detected_by_plan_hash": str(row["detected_by_plan_hash"]),
        "request_completed_at": _optional_time(row["request_completed_at"]),
        "returned_points": returned_points,
        "covered_at": _optional_time(row["covered_at"]),
        "covered_by_plan_hash": (
            str(row["covered_by_plan_hash"]) if row["covered_by_plan_hash"] is not None else None
        ),
        "observed_points": observed_points,
    }


def _require_expected_instrument(instrument_id: str, expected: set[str], evidence: str) -> None:
    if instrument_id not in expected:
        raise ValueError(f"research export {evidence} is outside its universe: {instrument_id}")


def _require_overlap(
    interval_start: datetime,
    interval_end: datetime,
    requested_start: datetime,
    requested_end: datetime,
    evidence: str,
) -> None:
    if interval_end <= interval_start:
        raise ValueError(f"research export {evidence} interval is invalid")
    if interval_start >= requested_end or interval_end <= requested_start:
        raise ValueError(f"research export {evidence} is outside its requested interval")


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("research export evidence timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research export evidence timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _optional_time(value: object) -> str | None:
    return _utc_text(_datetime(value)) if value is not None else None


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
