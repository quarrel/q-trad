"""Loading of operator-authored, non-authoritative IBKR capability queries."""

import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from qtrad.domain.identifiers import InstrumentId
from qtrad.ports.ibkr_capability import IbkrContractQuery


@dataclass(frozen=True, slots=True)
class IbkrCapabilityProbeSpec:
    name: str
    queries: tuple[IbkrContractQuery, ...]
    configuration_hash: str

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 100:
            raise ValueError("IBKR capability probe spec requires a bounded name")
        if not self.queries or len(self.queries) > 100:
            raise ValueError("IBKR capability probe spec requires between one and 100 queries")
        if len(self.configuration_hash) != 64:
            raise ValueError("IBKR capability probe spec hash must be SHA-256")


def load_ibkr_capability_probe_spec(path: Path) -> IbkrCapabilityProbeSpec:
    """Load queries only; exact returned contracts remain the review evidence."""

    with path.open("rb") as source:
        document = cast(Mapping[str, object], tomllib.load(source))
    entries = document.get("query")
    if not isinstance(entries, list):
        raise ValueError("IBKR capability probe spec requires [[query]] entries")
    queries = tuple(_query(cast(Mapping[str, object], entry)) for entry in entries)
    if len(set(queries)) != len(queries):
        raise ValueError("IBKR capability probe spec queries must be unique")
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return IbkrCapabilityProbeSpec(
        name=_required_string(document, "name"),
        queries=queries,
        configuration_hash=hashlib.sha256(encoded).hexdigest(),
    )


def _query(entry: Mapping[str, object]) -> IbkrContractQuery:
    return IbkrContractQuery(
        instrument_id=InstrumentId(_required_string(entry, "instrument_id")),
        symbol=_required_string(entry, "symbol"),
        security_type=_required_string(entry, "security_type"),
        exchange=_required_string(entry, "exchange"),
        currency=_required_string(entry, "currency"),
        local_symbol=_optional_string(entry, "local_symbol"),
        trading_class=_optional_string(entry, "trading_class"),
        multiplier=_optional_string(entry, "multiplier"),
    )


def _required_string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"IBKR capability probe spec requires non-empty {field}")
    return value


def _optional_string(document: Mapping[str, object], field: str) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"IBKR capability probe spec {field} must be a non-empty string when present"
        )
    return value
