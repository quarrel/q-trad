import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from qtrad import __main__ as cli
from qtrad.application.capture_feed import (
    CaptureFeedCursor,
    advance_capture_feed_cursor,
    rebind_capture_feed_serving_identity,
)
from qtrad.domain.events import EventEnvelope
from qtrad.ports.capture_feed import CaptureFeedIdentity, CaptureFeedPage
from qtrad.runtime import capture_feed as capture_feed_runtime
from qtrad.runtime.capture_feed import decode_capture_feed_page

IDENTITY = CaptureFeedIdentity(
    feed_schema_version=1,
    source_id="oci-sydney-capture-1",
    universe_name="capture-v1",
    configuration_hash="a" * 64,
)
OBSERVED_AT = datetime(2026, 7, 14, 6, tzinfo=UTC)


def _event(position: int, *, event_id: UUID | None = None) -> EventEnvelope:
    identifier = event_id or UUID(int=position)
    return EventEnvelope(
        event_id=identifier,
        stream_id=f"feed-test:{position}",
        stream_version=1,
        event_type="FeedTestObserved",
        schema_version=1,
        event_time=OBSERVED_AT,
        received_time=OBSERVED_AT,
        persisted_time=OBSERVED_AT,
        correlation_id=UUID(int=10_000 + position),
        causation_id=None,
        producer="fixture",
        producer_version="1",
        payload={"position": position},
        global_position=position,
        raw_record_id=None,
    )


def _page(
    *,
    after: int,
    high_water: int,
    positions: tuple[int, ...],
    identity: CaptureFeedIdentity = IDENTITY,
) -> CaptureFeedPage:
    next_position = positions[-1] if positions else after
    return CaptureFeedPage(
        identity=identity,
        after_position=after,
        high_water_position=high_water,
        next_position=next_position,
        has_more=next_position < high_water,
        events=tuple(_event(position) for position in positions),
    )


def _page_json(page: CaptureFeedPage, *, raw_record_id: int | None = None) -> str:
    events = []
    for event in page.events:
        payload: dict[str, object] = {
            "global_position": event.global_position,
            "event_id": str(event.event_id),
            "stream_id": event.stream_id,
            "stream_version": event.stream_version,
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "event_time": event.event_time.isoformat().replace("+00:00", "Z"),
            "received_time": event.received_time.isoformat().replace("+00:00", "Z"),
            "persisted_time": event.persisted_time.isoformat().replace("+00:00", "Z")
            if event.persisted_time is not None
            else None,
            "correlation_id": str(event.correlation_id),
            "causation_id": None,
            "producer": event.producer,
            "producer_version": event.producer_version,
            "payload": event.payload,
        }
        if raw_record_id is not None:
            payload["raw_record_id"] = raw_record_id
        events.append(payload)
    return json.dumps(
        {
            "feed_schema_version": page.identity.feed_schema_version,
            "source_id": page.identity.source_id,
            "universe_name": page.identity.universe_name,
            "configuration_hash": page.identity.configuration_hash,
            "after_position": page.after_position,
            "high_water_position": page.high_water_position,
            "next_position": page.next_position,
            "has_more": page.has_more,
            "events": events,
        }
    )


def test_cursor_accepts_gapped_ordered_pages_and_an_empty_caught_up_page() -> None:
    cursor = CaptureFeedCursor.initial(IDENTITY)

    first = decode_capture_feed_page(_page_json(_page(after=0, high_water=7, positions=(2, 5))))
    cursor = advance_capture_feed_cursor(cursor, first)
    assert cursor.position == 5
    assert cursor.observed_high_water_position == 7

    second = decode_capture_feed_page(_page_json(_page(after=5, high_water=7, positions=(7,))))
    cursor = advance_capture_feed_cursor(cursor, second)
    assert cursor.position == 7

    empty = decode_capture_feed_page(_page_json(_page(after=7, high_water=7, positions=())))
    assert advance_capture_feed_cursor(cursor, empty) == cursor


def test_empty_page_can_report_a_concurrent_append_and_retry_the_same_cursor() -> None:
    cursor = CaptureFeedCursor.initial(IDENTITY, after_position=7)
    empty = _page(after=7, high_water=8, positions=())

    cursor = advance_capture_feed_cursor(cursor, empty)

    assert cursor.position == 7
    assert cursor.observed_high_water_position == 8
    final = _page(after=7, high_water=8, positions=(8,))
    assert advance_capture_feed_cursor(cursor, final).position == 8


@pytest.mark.parametrize(
    "identity",
    [
        replace(IDENTITY, source_id="another-source"),
        replace(IDENTITY, universe_name="capture-v2"),
        replace(IDENTITY, configuration_hash="b" * 64),
    ],
)
def test_cursor_rejects_feed_identity_drift(identity: CaptureFeedIdentity) -> None:
    cursor = CaptureFeedCursor.initial(IDENTITY)

    with pytest.raises(ValueError, match="identity changed"):
        advance_capture_feed_cursor(
            cursor, _page(after=0, high_water=1, positions=(1,), identity=identity)
        )


def test_cursor_rejects_replay_skip_and_high_water_regression() -> None:
    cursor = advance_capture_feed_cursor(
        CaptureFeedCursor.initial(IDENTITY),
        _page(after=0, high_water=10, positions=(5,)),
    )

    with pytest.raises(ValueError, match="does not continue"):
        advance_capture_feed_cursor(cursor, _page(after=0, high_water=10, positions=(5,)))
    with pytest.raises(ValueError, match="does not continue"):
        advance_capture_feed_cursor(cursor, _page(after=6, high_water=10, positions=(7,)))
    with pytest.raises(ValueError, match="regressed"):
        advance_capture_feed_cursor(cursor, _page(after=5, high_water=9, positions=(9,)))


def test_serving_identity_rebind_is_explicit_caught_up_and_same_source_only() -> None:
    caught_up = advance_capture_feed_cursor(
        CaptureFeedCursor.initial(IDENTITY),
        _page(after=0, high_water=5, positions=(5,)),
    )
    capture_v2 = replace(
        IDENTITY,
        universe_name="capture-v2",
        configuration_hash="b" * 64,
    )

    rebound = rebind_capture_feed_serving_identity(caught_up, capture_v2)

    assert rebound.position == 5
    assert rebound.identity == capture_v2
    with pytest.raises(ValueError, match="did not change"):
        rebind_capture_feed_serving_identity(caught_up, IDENTITY)
    with pytest.raises(ValueError, match="independent cursor"):
        rebind_capture_feed_serving_identity(
            caught_up,
            replace(capture_v2, source_id="independent-source"),
        )

    behind = advance_capture_feed_cursor(
        CaptureFeedCursor.initial(IDENTITY),
        _page(after=0, high_water=10, positions=(5,)),
    )
    with pytest.raises(ValueError, match="consumer is behind"):
        rebind_capture_feed_serving_identity(behind, capture_v2)


def test_page_rejects_ordering_bounds_next_position_and_continuation_contradictions() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        CaptureFeedPage(IDENTITY, 0, 5, 2, True, (_event(5), _event(2)))
    with pytest.raises(ValueError, match="outside the page bounds"):
        CaptureFeedPage(IDENTITY, 2, 5, 2, True, (_event(2),))
    with pytest.raises(ValueError, match="next position"):
        CaptureFeedPage(IDENTITY, 0, 5, 4, True, (_event(5),))
    with pytest.raises(ValueError, match="continuation flag"):
        CaptureFeedPage(IDENTITY, 0, 5, 5, True, (_event(5),))


def test_page_rejects_duplicate_event_identity_and_raw_record_identity() -> None:
    duplicate_id = UUID(int=999)
    with pytest.raises(ValueError, match="event IDs must be unique"):
        CaptureFeedPage(
            IDENTITY,
            0,
            2,
            2,
            False,
            (_event(1, event_id=duplicate_id), _event(2, event_id=duplicate_id)),
        )

    page = _page(after=0, high_water=1, positions=(1,))
    with pytest.raises(ValidationError, match="raw_record_id"):
        decode_capture_feed_page(_page_json(page, raw_record_id=42))


def test_page_decoder_rejects_unknown_fields_and_non_utc_events() -> None:
    page = _page(after=0, high_water=1, positions=(1,))
    payload = json.loads(_page_json(page))
    payload["unexpected"] = "field"
    with pytest.raises(ValidationError, match="unexpected"):
        decode_capture_feed_page(json.dumps(payload))

    payload.pop("unexpected")
    payload["events"][0]["event_time"] = "2026-07-14T06:00:00"
    with pytest.raises(ValueError, match="event_time must be timezone-aware"):
        decode_capture_feed_page(json.dumps(payload))


def test_page_decoder_bounds_serialised_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_feed_runtime, "_MAX_FEED_PAGE_BYTES", 10)

    with pytest.raises(ValueError, match="16 MiB"):
        decode_capture_feed_page("{" + " " * 10 + "}")


def test_offline_cli_verifier_reports_final_cursor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(_page_json(_page(after=0, high_water=3, positions=(1, 2))))
    second.write_text(_page_json(_page(after=2, high_water=3, positions=(3,))))

    cli._verify_capture_feed_pages(
        source_id=IDENTITY.source_id,
        universe_name=IDENTITY.universe_name,
        configuration_hash=IDENTITY.configuration_hash,
        after_position=0,
        page_paths=(first, second),
    )

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "caught_up": True,
        "configuration_hash": IDENTITY.configuration_hash,
        "event_count": 3,
        "observed_high_water_position": 3,
        "page_count": 2,
        "position": 3,
        "source_id": IDENTITY.source_id,
        "universe_name": IDENTITY.universe_name,
    }
