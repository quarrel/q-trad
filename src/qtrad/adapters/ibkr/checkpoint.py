"""Durable, identity-bound checkpoints for bounded IBKR capability probes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

from qtrad.domain.events import to_json_value
from qtrad.domain.identifiers import InstrumentId
from qtrad.ports.ibkr_capability import (
    IbkrCandidateCapability,
    IbkrContractEvidence,
    IbkrContractQuery,
    IbkrRequestEvidence,
)


@dataclass(frozen=True, slots=True)
class IbkrCapabilityCheckpointIdentity:
    """Inputs that must remain unchanged before evidence can be resumed."""

    catalogue_hash: str
    probe_spec_hash: str
    api_version: str
    gateway_version: str
    configuration_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("catalogue hash", self.catalogue_hash),
            ("probe spec hash", self.probe_spec_hash),
            ("configuration hash", self.configuration_hash),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"IBKR checkpoint {name} must be a lowercase SHA-256 digest")
        if not self.api_version or not self.gateway_version:
            raise ValueError("IBKR checkpoint versions are required")

    def as_json_value(self) -> dict[str, str]:
        return {
            "catalogue_hash": self.catalogue_hash,
            "probe_spec_hash": self.probe_spec_hash,
            "api_version": self.api_version,
            "gateway_version": self.gateway_version,
            "configuration_hash": self.configuration_hash,
        }


class IbkrCapabilityCheckpoint(Protocol):
    async def load(
        self, queries: Sequence[IbkrContractQuery]
    ) -> tuple[IbkrCandidateCapability, ...]: ...

    async def save(self, result: IbkrCandidateCapability) -> None: ...


def checkpoint_query_key(query: IbkrContractQuery) -> str:
    encoded = json.dumps(to_json_value(query), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class JsonIbkrCapabilityCheckpoint:
    """Atomic JSON checkpoint that is discarded when its run identity changes."""

    def __init__(self, path: Path, identity: IbkrCapabilityCheckpointIdentity) -> None:
        self._path = path
        self._identity = identity
        self._lock = asyncio.Lock()

    async def load(
        self, queries: Sequence[IbkrContractQuery]
    ) -> tuple[IbkrCandidateCapability, ...]:
        async with self._lock:
            document = await asyncio.to_thread(self._read_document)
        if document is None:
            return ()
        if document["identity"] != self._identity.as_json_value():
            return ()
        raw_results = cast(Mapping[str, object], document["results"])
        loaded: list[IbkrCandidateCapability] = []
        for query in queries:
            encoded = raw_results.get(checkpoint_query_key(query))
            if encoded is not None:
                loaded.append(_candidate_from_json(cast(Mapping[str, object], encoded)))
        return tuple(loaded)

    async def save(self, result: IbkrCandidateCapability) -> None:
        async with self._lock:
            document = await asyncio.to_thread(self._read_document)
            if document is None or document["identity"] != self._identity.as_json_value():
                document = {"identity": self._identity.as_json_value(), "results": {}}
            results = cast(dict[str, object], document["results"])
            results[checkpoint_query_key(result.query)] = to_json_value(result)
            await asyncio.to_thread(self._write_document, document)

    def _read_document(self) -> dict[str, object] | None:
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("IBKR capability checkpoint cannot be read") from error
        if not isinstance(raw, dict) or not isinstance(raw.get("identity"), dict):
            raise RuntimeError("IBKR capability checkpoint has an invalid envelope")
        if not isinstance(raw.get("results"), dict):
            raise RuntimeError("IBKR capability checkpoint has invalid results")
        return cast(dict[str, object], raw)

    def _write_document(self, document: Mapping[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(document, output, sort_keys=True, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self._path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)
            raise


def _candidate_from_json(payload: Mapping[str, object]) -> IbkrCandidateCapability:
    query_payload = cast(Mapping[str, object], payload["query"])
    query = IbkrContractQuery(
        instrument_id=InstrumentId(str(query_payload["instrument_id"])),
        symbol=str(query_payload["symbol"]),
        security_type=str(query_payload["security_type"]),
        exchange=str(query_payload["exchange"]),
        currency=str(query_payload["currency"]),
        local_symbol=_optional_string(query_payload["local_symbol"]),
        trading_class=_optional_string(query_payload["trading_class"]),
        multiplier=_optional_string(query_payload["multiplier"]),
    )
    contracts = tuple(
        _contract_from_json(cast(Mapping[str, object], item))
        for item in cast(Sequence[object], payload["contracts"])
    )
    requests = tuple(
        _request_from_json(cast(Mapping[str, object], item))
        for item in cast(Sequence[object], payload["requests"])
    )
    return IbkrCandidateCapability(query=query, contracts=contracts, requests=requests)


def _contract_from_json(payload: Mapping[str, object]) -> IbkrContractEvidence:
    return IbkrContractEvidence(
        con_id=_integer(payload["con_id"]),
        symbol=str(payload["symbol"]),
        local_symbol=str(payload["local_symbol"]),
        security_type=str(payload["security_type"]),
        exchange=str(payload["exchange"]),
        currency=str(payload["currency"]),
        trading_class=_optional_string(payload["trading_class"]),
        multiplier=_optional_string(payload["multiplier"]),
        minimum_tick=(
            Decimal(str(payload["minimum_tick"])) if payload["minimum_tick"] is not None else None
        ),
        market_rule_ids=_string_tuple(payload["market_rule_ids"]),
        valid_exchanges=_string_tuple(payload["valid_exchanges"]),
        long_name=_optional_string(payload["long_name"]),
        underlier_con_id=(
            _integer(payload["underlier_con_id"])
            if payload["underlier_con_id"] is not None
            else None
        ),
        timezone=_optional_string(payload["timezone"]),
        trading_hours=_optional_string(payload["trading_hours"]),
        liquid_hours=_optional_string(payload["liquid_hours"]),
        primary_exchange=_optional_string(payload.get("primary_exchange")),
        contract_month=_optional_string(payload.get("contract_month")),
    )


def _request_from_json(payload: Mapping[str, object]) -> IbkrRequestEvidence:
    earliest = payload["earliest_timestamp"]
    return IbkrRequestEvidence(
        kind=str(payload["kind"]),
        status=str(payload["status"]),
        latency_milliseconds=_integer(payload["latency_milliseconds"]),
        contract_con_id=(
            _integer(payload["contract_con_id"]) if payload["contract_con_id"] is not None else None
        ),
        market_data_type=_optional_string(payload["market_data_type"]),
        availability=_optional_string(payload["availability"]),
        bid_seen=bool(payload["bid_seen"]),
        ask_seen=bool(payload["ask_seen"]),
        bid_usable=bool(payload["bid_usable"]),
        ask_usable=bool(payload["ask_usable"]),
        bid_size_seen=bool(payload["bid_size_seen"]),
        ask_size_seen=bool(payload["ask_size_seen"]),
        row_count=_integer(payload["row_count"]) if payload["row_count"] is not None else None,
        earliest_timestamp=(
            datetime.fromisoformat(str(earliest).replace("Z", "+00:00")) if earliest else None
        ),
        timezone=_optional_string(payload["timezone"]),
        use_rth=bool(payload["use_rth"]) if payload["use_rth"] is not None else None,
        error_codes=_string_tuple(payload["error_codes"]),
        error_times=tuple(
            _integer(item) for item in cast(Sequence[object], payload["error_times"])
        ),
        returned_contract_count=(
            _integer(payload["returned_contract_count"])
            if payload["returned_contract_count"] is not None
            else None
        ),
    )


def _integer(value: object) -> int:
    return int(str(value))


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in cast(Sequence[object], value))
