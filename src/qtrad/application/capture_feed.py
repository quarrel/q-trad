"""Deterministic consumer-side capture-feed cursor validation."""

from dataclasses import dataclass

from qtrad.ports.capture_feed import CaptureFeedIdentity, CaptureFeedPage


@dataclass(frozen=True, slots=True)
class CaptureFeedCursor:
    identity: CaptureFeedIdentity
    position: int
    observed_high_water_position: int

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ValueError("capture feed cursor position cannot be negative")
        if self.observed_high_water_position < self.position:
            raise ValueError("capture feed observed high-water position cannot precede its cursor")

    @classmethod
    def initial(
        cls, identity: CaptureFeedIdentity, *, after_position: int = 0
    ) -> "CaptureFeedCursor":
        return cls(
            identity=identity,
            position=after_position,
            observed_high_water_position=after_position,
        )


def advance_capture_feed_cursor(
    cursor: CaptureFeedCursor, page: CaptureFeedPage
) -> CaptureFeedCursor:
    """Accept exactly the requested page from the pinned append-only feed identity."""

    if page.identity != cursor.identity:
        raise ValueError("capture feed identity changed")
    if page.after_position != cursor.position:
        raise ValueError("capture feed page does not continue from the current cursor")
    if page.high_water_position < cursor.observed_high_water_position:
        raise ValueError("capture feed high-water position regressed")
    return CaptureFeedCursor(
        identity=cursor.identity,
        position=page.next_position,
        observed_high_water_position=page.high_water_position,
    )


def rebind_capture_feed_serving_identity(
    cursor: CaptureFeedCursor, new_identity: CaptureFeedIdentity
) -> CaptureFeedCursor:
    """Explicitly acknowledge a universe release change at a caught-up source cursor."""

    if cursor.position != cursor.observed_high_water_position:
        raise ValueError("capture feed identity cannot change while the consumer is behind")
    if new_identity.feed_schema_version != cursor.identity.feed_schema_version:
        raise ValueError("capture feed schema changes require a new consumer contract")
    if new_identity.source_id != cursor.identity.source_id:
        raise ValueError("capture feed source changes require an independent cursor")
    if new_identity == cursor.identity:
        raise ValueError("capture feed serving identity did not change")
    return CaptureFeedCursor(
        identity=new_identity,
        position=cursor.position,
        observed_high_water_position=cursor.position,
    )
