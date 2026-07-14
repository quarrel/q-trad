from dataclasses import replace
from datetime import UTC, datetime

import pytest

from qtrad.api.app import FeedEventResponse
from qtrad.domain.events import EventEnvelope
from qtrad.ports.storage import EventPage


def _persisted_event(position: int) -> EventEnvelope:
    now = datetime(2026, 7, 14, 4, 0, tzinfo=UTC)
    return replace(
        EventEnvelope.create(
            stream_id=f"feed-test:{position}",
            stream_version=1,
            event_type="FeedTestObserved",
            event_time=now,
            received_time=now,
            producer="test",
            producer_version="1",
            payload={"position": position},
        ),
        global_position=position,
        persisted_time=now,
    )


def test_event_page_requires_ordered_persisted_events() -> None:
    first = _persisted_event(10)
    second = _persisted_event(12)

    page = EventPage(events=(first, second), high_water_position=12)

    assert page.events == (first, second)
    with pytest.raises(ValueError, match="strictly increasing"):
        EventPage(events=(second, first), high_water_position=12)
    with pytest.raises(ValueError, match="high-water"):
        EventPage(events=(second,), high_water_position=11)
    with pytest.raises(ValueError, match="positive"):
        EventPage(events=(_persisted_event(0),), high_water_position=0)


def test_feed_response_excludes_raw_record_identity() -> None:
    event = replace(_persisted_event(10), raw_record_id=99)

    response = FeedEventResponse.from_event(event).model_dump()

    assert response["global_position"] == 10
    assert "raw_record_id" not in response
