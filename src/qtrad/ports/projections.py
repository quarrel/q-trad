"""Projection port."""

from typing import Protocol

from qtrad.domain.events import EventEnvelope


class ProjectionHandler(Protocol):
    @property
    def name(self) -> str: ...

    async def apply(self, event: EventEnvelope) -> None: ...

    async def reset(self) -> None: ...
