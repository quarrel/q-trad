"""Versioned capture-universe configuration at the runtime boundary."""

import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from qtrad.application.universe_promotion import UniversePromotion
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
    exact_review_epics: Mapping[InstrumentId, tuple[str, ...]]
    configuration_hash: str

    def __post_init__(self) -> None:
        if not self.name or not self.instruments:
            raise ValueError("capture candidate name and instruments are required")
        identifiers = [instrument.instrument_id for instrument in self.instruments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("capture candidate instrument IDs must be unique")
        if len(self.instruments) > 100:
            raise ValueError("capture candidate catalogue cannot exceed 100 instruments")
        if any(len(instrument.search_aliases) > 5 for instrument in self.instruments):
            raise ValueError("capture candidate instrument cannot exceed five search aliases")
        if set(self.exact_review_epics) - set(identifiers):
            raise ValueError("exact review hints must belong to candidate instruments")
        if any(len(epics) > 5 for epics in self.exact_review_epics.values()):
            raise ValueError("capture candidate cannot exceed five exact review epics")


def load_capture_universe(path: Path) -> CaptureUniverse:
    """Load a TOML universe without allowing provider selection to be inferred."""

    return _capture_universe(_document(path))


def _capture_universe(document: Mapping[str, object]) -> CaptureUniverse:
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


def render_capture_universe_promotion(
    promotion: UniversePromotion,
) -> tuple[str, CaptureUniverse]:
    """Render and re-parse a promoted universe so emitted TOML uses the normal safety gate."""

    instrument_entries: list[dict[str, object]] = []
    for instrument in promotion.instruments:
        entry: dict[str, object] = {
            "id": str(instrument.instrument_id),
            "display_name": instrument.display_name,
            "asset_class": instrument.asset_class.value,
            "quote_currency": instrument.quote_currency,
            "search_aliases": list(instrument.search_aliases),
            "preferred_epic": promotion.preferred_epics[instrument.instrument_id],
        }
        if instrument.base_currency is not None:
            entry["base_currency"] = instrument.base_currency
        instrument_entries.append(entry)
    promoted_at = promotion.promoted_at.isoformat().replace("+00:00", "Z")
    document: dict[str, object] = {
        "name": promotion.release_name,
        "source_catalogue_name": promotion.source_catalogue_name,
        "source_catalogue_hash": promotion.source_catalogue_hash,
        "source_review_hash": promotion.source_review_hash,
        "selection_hash": promotion.selection_hash,
        "promoted_at": promoted_at,
        "quarantined_instrument_ids": [
            str(instrument_id) for instrument_id in promotion.quarantined_instrument_ids
        ],
        "instrument": instrument_entries,
    }
    universe = _capture_universe(document)
    lines = [
        f"name = {_toml_string(promotion.release_name)}",
        f"source_catalogue_name = {_toml_string(promotion.source_catalogue_name)}",
        f"source_catalogue_hash = {_toml_string(promotion.source_catalogue_hash)}",
        f"source_review_hash = {_toml_string(promotion.source_review_hash)}",
        f"selection_hash = {_toml_string(promotion.selection_hash)}",
        f"promoted_at = {_toml_string(promoted_at)}",
        "quarantined_instrument_ids = "
        + json.dumps(
            [str(instrument_id) for instrument_id in promotion.quarantined_instrument_ids],
            ensure_ascii=True,
        ),
    ]
    for entry in instrument_entries:
        lines.extend(
            [
                "",
                "[[instrument]]",
                f"id = {_toml_string(_required_string(entry, 'id'))}",
                f"display_name = {_toml_string(_required_string(entry, 'display_name'))}",
                f"asset_class = {_toml_string(_required_string(entry, 'asset_class'))}",
            ]
        )
        base_currency = _optional_string(entry, "base_currency")
        if base_currency is not None:
            lines.append(f"base_currency = {_toml_string(base_currency)}")
        aliases = cast(list[str], entry["search_aliases"])
        lines.extend(
            [
                f"quote_currency = {_toml_string(_required_string(entry, 'quote_currency'))}",
                f"search_aliases = {json.dumps(aliases, ensure_ascii=True)}",
                f"preferred_epic = {_toml_string(_required_string(entry, 'preferred_epic'))}",
            ]
        )
    rendered = "\n".join(lines) + "\n"
    reparsed = _capture_universe(tomllib.loads(rendered))
    if reparsed.configuration_hash != universe.configuration_hash:
        raise RuntimeError("rendered capture universe changed its canonical configuration hash")
    return rendered, reparsed


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
        exact_review_epics={
            InstrumentId(_required_string(entry, "id")): tuple(
                _string_list(entry, "exact_review_epics")
            )
            for entry in entries
            if "exact_review_epics" in entry
        },
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


def _string_list(document: Mapping[str, object], field: str) -> list[str]:
    value = document[field]
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a non-empty string array")
    strings = cast(list[str], value)
    if len(set(strings)) != len(strings) or any(not item.strip() for item in strings):
        raise ValueError(f"{field} must contain unique non-empty strings")
    return strings


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


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
