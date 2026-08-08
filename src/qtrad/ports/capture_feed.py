"""Provider-neutral contracts for consuming bounded canonical-event feed pages."""

from dataclasses import dataclass
from uuid import UUID

from qtrad.domain.events import EventEnvelope
from qtrad.domain.market_data import MarketDataSourceClass


@dataclass(frozen=True, slots=True)
class CaptureFeedIdentity:
    feed_schema_version: int
    source_id: str
    universe_name: str
    configuration_hash: str

    def __post_init__(self) -> None:
        if self.feed_schema_version != 1:
            raise ValueError("unsupported capture feed schema version")
        if not self.source_id or len(self.source_id) > 64:
            raise ValueError("capture feed source ID must contain between 1 and 64 characters")
        if any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in self.source_id
        ):
            raise ValueError("capture feed source ID contains unsupported characters")
        if not self.universe_name or len(self.universe_name) > 64:
            raise ValueError("capture feed universe name must contain between 1 and 64 characters")
        if len(self.configuration_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.configuration_hash
        ):
            raise ValueError("capture feed configuration hash must be lower-case SHA-256")


@dataclass(frozen=True, slots=True)
class CaptureIdentity:
    """Immutable provider/source identity attached to one capture session.

    ``CaptureFeedIdentity`` predates provider-selectable ingestion and remains the
    public identity of a canonical feed.  This companion value adds the source
    class and broker environment needed to keep native IBKR, historical IBKR and
    IG evidence separate at the raw persistence boundary.
    """

    provider: str
    environment: str
    source_class: MarketDataSourceClass
    capture_source_id: str
    universe_id: str
    configuration_hash: str

    def __post_init__(self) -> None:
        if not self.provider or len(self.provider) > 32:
            raise ValueError("capture provider must be bounded and non-empty")
        if not self.environment or len(self.environment) > 32:
            raise ValueError("capture environment must be bounded and non-empty")
        if self.source_class is MarketDataSourceClass.IBKR_NATIVE_CAPTURE and (
            self.provider != "ibkr" or self.environment != "IBKR_PAPER"
        ):
            raise ValueError("IBKR native capture identity must use ibkr/IBKR_PAPER")
        if self.source_class is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH and (
            self.provider != "ibkr"
        ):
            raise ValueError("IBKR historical identity must use provider ibkr")
        if self.source_class is MarketDataSourceClass.IG_NATIVE_CAPTURE and self.provider != "ig":
            raise ValueError("IG native capture identity must use provider ig")
        for name, value in (
            ("capture source ID", self.capture_source_id),
            ("capture universe ID", self.universe_id),
        ):
            if not value or len(value) > 64:
                raise ValueError(f"{name} must contain between 1 and 64 characters")
            if any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in value
            ):
                raise ValueError(f"{name} contains unsupported characters")
        if len(self.configuration_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.configuration_hash
        ):
            raise ValueError("capture configuration hash must be a lower-case SHA-256")

    @property
    def feed_identity(self) -> CaptureFeedIdentity:
        return CaptureFeedIdentity(
            feed_schema_version=1,
            source_id=self.capture_source_id,
            universe_name=self.universe_id,
            configuration_hash=self.configuration_hash,
        )


@dataclass(frozen=True, slots=True)
class CaptureFeedPage:
    identity: CaptureFeedIdentity
    after_position: int
    high_water_position: int
    next_position: int
    has_more: bool
    events: tuple[EventEnvelope, ...]

    def __post_init__(self) -> None:
        if self.after_position < 0:
            raise ValueError("capture feed page cursor cannot be negative")
        if self.high_water_position < self.after_position:
            raise ValueError("capture feed high-water position cannot precede its cursor")
        if len(self.events) > 1000:
            raise ValueError("capture feed page cannot exceed 1000 events")
        positions: list[int] = []
        event_ids: list[UUID] = []
        for event in self.events:
            if event.global_position is None or event.persisted_time is None:
                raise ValueError("capture feed pages require persisted events")
            if not self.after_position < event.global_position <= self.high_water_position:
                raise ValueError("capture feed event position is outside the page bounds")
            if event.raw_record_id is not None:
                raise ValueError("capture feed events must not expose raw-record identity")
            positions.append(event.global_position)
            event_ids.append(event.event_id)
        if positions != sorted(set(positions)):
            raise ValueError("capture feed event positions must be strictly increasing")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("capture feed event IDs must be unique within a page")
        expected_next = positions[-1] if positions else self.after_position
        if self.next_position != expected_next:
            raise ValueError("capture feed next position does not match its events")
        if self.has_more != (self.next_position < self.high_water_position):
            raise ValueError("capture feed continuation flag contradicts its positions")
