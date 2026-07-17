import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrad.application.backfill_planning import build_backfill_plan
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.runtime.backfill_plan import write_backfill_plan
from qtrad.runtime.qualification_gap_history import (
    QualificationEvidence,
    qualification_gap_backfill_scopes,
)
from qtrad.runtime.qualification_gap_plan_set import (
    QualificationGapPlanEntry,
    build_qualification_gap_plan_set,
    load_qualification_gap_plan_set,
    write_qualification_gap_plan_set,
)

_NOW = datetime(2026, 7, 17, 7, tzinfo=UTC)
_UNIVERSE_HASH = "a" * 64


def _evidence() -> QualificationEvidence:
    gaps = [
        ("gap-1", "fx:aud-usd", "2026-07-14T20:38:23Z", "2026-07-14T20:40:10Z"),
        ("gap-2", "fx:aud-usd", "2026-07-14T20:40:30Z", "2026-07-14T20:42:00Z"),
        ("gap-3", "index:us-500", "2026-07-15T21:00:01Z", "2026-07-15T21:02:01Z"),
    ]
    return QualificationEvidence.model_validate_json(
        json.dumps(
            {
                "schema": "qtrad-capture-qualification-v1",
                "generated_at": "2026-07-17T04:43:57Z",
                "candidate_start": "2026-07-14T03:05:33Z",
                "not_before_end": "2026-07-17T03:05:33Z",
                "release": {
                    "expected_image": "example.invalid/qtrad@sha256:" + "1" * 64,
                    "actual_image": "example.invalid/qtrad@sha256:" + "1" * 64,
                    "postgres_image": "postgres@sha256:" + "2" * 64,
                    "descriptor_commit": "3" * 40,
                    "descriptor_sha256": "4" * 64,
                    "evidence_tool_sha256": "5" * 64,
                    "capture_source_id": "local-development",
                    "configuration_hash": _UNIVERSE_HASH,
                    "migration_version": "0009",
                },
                "candidate_gaps": [
                    {
                        "gap_id": gap_id,
                        "instrument_id": instrument_id,
                        "interval_start": start,
                        "interval_end": end,
                        "reason": "NO_HEALTHY_QUOTE_DURING_EXPECTED_STREAM",
                        "detected_at": end,
                        "repaired_at": None,
                    }
                    for gap_id, instrument_id, start, end in gaps
                ],
                "evidence_sha256": "b" * 64,
            }
        )
    )


def _plan():
    instrument_id = InstrumentId("fx:aud-usd")
    listing = ProviderListing(
        listing_id=ProviderListingId("ig", "demo", "CS.D.AUDUSD.CFD.IP"),
        instrument_id=instrument_id,
        display_name="AUD/USD",
        product_type=ProductType.SPOT_FX,
        currency="USD",
        minimum_deal_size=Decimal("0.5"),
        price_increment=Decimal("0.0001"),
        valid_from=datetime(2026, 7, 14, tzinfo=UTC),
        valid_to=None,
        metadata_version="listing-v1",
    )
    return build_backfill_plan(
        universe_name="capture-v1",
        universe_hash=_UNIVERSE_HASH,
        instrument_ids=(instrument_id,),
        listings=(listing,),
        preferred_epics={instrument_id: "CS.D.AUDUSD.CFD.IP"},
        start=datetime(2026, 7, 14, 20, 38, tzinfo=UTC),
        end=datetime(2026, 7, 14, 20, 42, tzinfo=UTC),
        remaining_allowance=10_000,
        quota_observed_at=_NOW,
        created_at=_NOW,
    )


def test_sparse_scopes_merge_only_touching_ranges_per_instrument() -> None:
    scopes = qualification_gap_backfill_scopes(_evidence())

    assert len(scopes) == 2
    assert scopes[0].instrument_ids == (InstrumentId("fx:aud-usd"),)
    assert scopes[0].start == datetime(2026, 7, 14, 20, 38, tzinfo=UTC)
    assert scopes[0].end == datetime(2026, 7, 14, 20, 42, tzinfo=UTC)
    assert scopes[0].gap_ids == ("gap-1", "gap-2")
    assert scopes[1].instrument_ids == (InstrumentId("index:us-500"),)
    assert scopes[1].end - scopes[1].start == timedelta(minutes=3)


def test_plan_set_binds_sibling_plans_aggregate_quota_and_gap_identity(tmp_path) -> None:
    plan = _plan()
    plan_path = tmp_path / "gap-plan-001.json"
    write_backfill_plan(plan_path, plan)
    entry = QualificationGapPlanEntry(
        file=plan_path.name,
        plan_hash=plan.plan_hash,
        gap_ids=("gap-1", "gap-2"),
        requested_points=plan.requested_points,
    )
    plan_set = build_qualification_gap_plan_set(
        qualification_evidence_sha256="b" * 64,
        snapshot_import_sha256="c" * 64,
        capture_source_id="local-development",
        universe_name="capture-v1",
        universe_hash=_UNIVERSE_HASH,
        created_at=_NOW,
        remaining_allowance=10_000,
        reserve_points=2_000,
        entries=(entry,),
    )
    set_path = tmp_path / "gap-plan-set.json"
    write_qualification_gap_plan_set(set_path, plan_set)

    loaded, plans = load_qualification_gap_plan_set(set_path)

    assert loaded == plan_set
    assert plans == (plan,)
    tampered = json.loads(set_path.read_text())
    tampered["requested_points"] += 1
    set_path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="hash"):
        load_qualification_gap_plan_set(set_path)


def test_plan_set_rejects_duplicate_gaps_and_aggregate_quota() -> None:
    plan = _plan()
    entry = QualificationGapPlanEntry(
        file="gap-plan-001.json",
        plan_hash=plan.plan_hash,
        gap_ids=("gap-1",),
        requested_points=plan.requested_points,
    )
    with pytest.raises(ValueError, match="duplicate gap"):
        build_qualification_gap_plan_set(
            qualification_evidence_sha256="b" * 64,
            snapshot_import_sha256="c" * 64,
            capture_source_id="local-development",
            universe_name="capture-v1",
            universe_hash=_UNIVERSE_HASH,
            created_at=_NOW,
            remaining_allowance=10_000,
            reserve_points=2_000,
            entries=(entry, entry.model_copy(update={"file": "gap-plan-002.json"})),
        )
    with pytest.raises(ValueError, match="exceeds quota"):
        build_qualification_gap_plan_set(
            qualification_evidence_sha256="b" * 64,
            snapshot_import_sha256="c" * 64,
            capture_source_id="local-development",
            universe_name="capture-v1",
            universe_hash=_UNIVERSE_HASH,
            created_at=_NOW,
            remaining_allowance=10,
            reserve_points=2,
            entries=(entry.model_copy(update={"requested_points": 9}),),
        )
