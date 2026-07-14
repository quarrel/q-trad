"""Versioned capture-universe configuration at the runtime boundary."""

import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass, Instrument


@dataclass(frozen=True, slots=True)
class CaptureUniverse:
    """A reviewed, provider-selection-safe set of instruments for one collector."""

    name: str
    instruments: tuple[Instrument, ...]
    preferred_epics: Mapping[InstrumentId, str]
    configuration_hash: str

    def __post_init__(self) -> None:
        if not self.name or not self.instruments:
            raise ValueError("capture universe name and instruments are required")
        identifiers = [instrument.instrument_id for instrument in self.instruments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("capture universe instrument IDs must be unique")
        missing = set(identifiers) - set(self.preferred_epics)
        if missing:
            raise ValueError(
                "capture universe requires an explicit preferred IG epic for every instrument: "
                + ", ".join(sorted(map(str, missing)))
            )

    @property
    def instruments_by_id(self) -> dict[InstrumentId, Instrument]:
        return {instrument.instrument_id: instrument for instrument in self.instruments}


@dataclass(frozen=True, slots=True)
class CaptureCandidates:
    """An offline instrument catalogue with no authority to select provider listings."""

    name: str
    instruments: tuple[Instrument, ...]
    configuration_hash: str

    def __post_init__(self) -> None:
        if not self.name or not self.instruments:
            raise ValueError("capture candidate name and instruments are required")
        identifiers = [instrument.instrument_id for instrument in self.instruments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("capture candidate instrument IDs must be unique")


def load_capture_universe(path: Path) -> CaptureUniverse:
    """Load a TOML universe without allowing provider selection to be inferred."""

    document = _document(path)
    name = _required_string(document, "name")
    entries = _entries(document)
    instruments = _instruments(entries)
    preferred_epics: dict[InstrumentId, str] = {}
    for entry in entries:
        instrument_id = InstrumentId(_required_string(entry, "id"))
        if "preferred_epic" not in entry:
            raise ValueError("capture universe requires an explicit preferred IG epic")
        preferred_epics[instrument_id] = _required_string(entry, "preferred_epic")
    return CaptureUniverse(
        name=name,
        instruments=tuple(instruments),
        preferred_epics=preferred_epics,
        configuration_hash=_configuration_hash(document),
    )


def load_capture_candidates(path: Path) -> CaptureCandidates:
    """Load an offline candidate catalogue that cannot authorise ingestion."""

    document = _document(path)
    entries = _entries(document)
    for entry in entries:
        if "preferred_epic" in entry:
            raise ValueError("capture candidates must not contain preferred IG epics")
    return CaptureCandidates(
        name=_required_string(document, "name"),
        instruments=tuple(_instruments(entries)),
        configuration_hash=_configuration_hash(document),
    )


def _document(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text())


def _entries(document: Mapping[str, object]) -> list[dict[str, object]]:
    entries = document.get("instrument")
    if not isinstance(entries, list):
        raise ValueError("capture universe requires [[instrument]] entries")
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("capture universe instrument entry must be a table")
    return cast(list[dict[str, object]], entries)


def _instruments(entries: list[dict[str, object]]) -> list[Instrument]:
    return [
        Instrument(
            instrument_id=InstrumentId(_required_string(entry, "id")),
            display_name=_required_string(entry, "display_name"),
            asset_class=AssetClass(_required_string(entry, "asset_class")),
            base_currency=_optional_string(entry, "base_currency"),
            quote_currency=_required_string(entry, "quote_currency"),
            search_aliases=_aliases(entry),
        )
        for entry in entries
    ]


def _configuration_hash(document: Mapping[str, object]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise ValueError(f"capture universe {key} must be a non-empty string")
    return item


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"capture universe {key} must be a non-empty string when set")
    return item


def _aliases(value: Mapping[str, object]) -> tuple[str, ...]:
    aliases = value["search_aliases"]
    if not isinstance(aliases, list) or not aliases:
        raise ValueError("capture universe search_aliases must be a non-empty list of strings")
    if not all(isinstance(item, str) and item for item in aliases):
        raise ValueError("capture universe search_aliases must be a non-empty list of strings")
    return tuple(cast(list[str], aliases))
