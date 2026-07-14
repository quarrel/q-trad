from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from qtrad.domain.identifiers import InstrumentId
from qtrad.runtime.research_export import research_export_metadata
from tests.test_quota_replay import sample_bar

NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _live_gap() -> dict[str, object]:
    return {
        "gap_id": UUID(int=1),
        "instrument_id": "index:us-500",
        "interval_start": NOW,
        "interval_end": NOW + timedelta(minutes=1),
        "reason": "fixture",
        "detected_at": NOW + timedelta(minutes=2),
        "repaired_at": None,
    }


def _historical_coverage() -> dict[str, object]:
    return {
        "instrument_id": "index:us-500",
        "source_provider": "ig",
        "source_environment": "demo",
        "source_external_id": "FIXTURE",
        "source_listing_valid_from": NOW,
        "source_listing_metadata_version": "fixture-v1",
        "provenance": "IG_HISTORICAL",
        "basis": "MID",
        "resolution": "MINUTE",
        "interval_start": NOW,
        "interval_end": NOW + timedelta(hours=1),
        "detected_at": NOW + timedelta(hours=2),
        "detected_by_plan_hash": "b" * 64,
        "covered_at": NOW + timedelta(hours=3),
        "covered_by_plan_hash": "b" * 64,
        "observed_points": 60,
    }


def _metadata(**overrides: object):
    arguments: dict[str, object] = {
        "universe_name": "research-fixture",
        "configuration_hash": "a" * 64,
        "instrument_ids": (InstrumentId("index:us-500"),),
        "bars": (sample_bar(),),
        "live_gaps": (_live_gap(),),
        "historical_coverage": (_historical_coverage(),),
        "application_version": "0.1.0",
        "application_image": "syd.ocir.io/example/qtrad@sha256:" + "c" * 64,
    }
    arguments.update(overrides)
    return research_export_metadata(**arguments)  # type: ignore[arg-type]


def test_export_metadata_contains_standalone_universe_coverage_and_gap_evidence() -> None:
    metadata = _metadata()

    assert metadata["manifest_contract"] == "qtrad-research-bars-v2"
    assert metadata["universe"] == {
        "name": "research-fixture",
        "configuration_hash": "a" * 64,
        "instrument_ids": ["index:us-500"],
    }
    assert metadata["provenance_counts"] == {"QUOTE_DERIVED": 1}
    assert metadata["basis_counts"] == {"MID": 1}
    assert metadata["bar_coverage"] == [
        {
            "instrument_id": "index:us-500",
            "basis": "MID",
            "provenance": "QUOTE_DERIVED",
            "source_listing_id": "fixture:test:US500",
            "interval_start": "2026-07-02T10:00:00Z",
            "interval_end": "2026-07-02T10:01:00Z",
            "row_count": 1,
            "maximum_revision": 1,
        }
    ]
    assert metadata["live_gaps"]["count"] == 1  # type: ignore[index]
    assert metadata["live_gaps"]["open_count"] == 1  # type: ignore[index]
    assert metadata["historical_coverage"]["count"] == 1  # type: ignore[index]
    assert metadata["historical_coverage"]["open_count"] == 0  # type: ignore[index]


def test_export_metadata_is_deterministic_and_fails_on_identity_drift() -> None:
    assert _metadata() == _metadata()
    with pytest.raises(ValueError, match="outside its universe"):
        _metadata(instrument_ids=(InstrumentId("fx:aud-usd"),))
    with pytest.raises(ValueError, match="configuration hash"):
        _metadata(configuration_hash="not-a-hash")
    wrong_gap = _live_gap()
    wrong_gap["instrument_id"] = "fx:aud-usd"
    with pytest.raises(ValueError, match="live gap is outside"):
        _metadata(live_gaps=(wrong_gap,))
