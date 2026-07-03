"""Stable canonical identifiers."""

from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    value: str

    def __post_init__(self) -> None:
        parts = self.value.split(":", maxsplit=1)
        if len(parts) != 2 or not all(parts) or self.value != self.value.lower():
            raise ValueError("instrument ID must be a lower-case '<namespace>:<name>' value")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class ProviderListingId:
    provider: str
    environment: str
    external_id: str

    def __post_init__(self) -> None:
        if not self.provider or not self.environment or not self.external_id:
            raise ValueError("provider listing fields must be non-empty")

    def __str__(self) -> str:
        return f"{self.provider}:{self.environment}:{self.external_id}"


@dataclass(frozen=True, slots=True)
class RunId:
    value: UUID

    @classmethod
    def new(cls) -> "RunId":
        return cls(uuid4())

    def __str__(self) -> str:
        return str(self.value)
