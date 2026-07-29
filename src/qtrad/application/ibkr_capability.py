"""Deterministic preflight for the account-gated IBKR capability probe."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import Instrument
from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_capability import IbkrCandidateCapability, IbkrContractQuery


@dataclass(frozen=True, slots=True)
class IbkrCapabilityPreflight:
    """Non-secret configuration evidence produced before Gateway authentication."""

    catalogue_name: str
    catalogue_hash: str
    candidate_count: int
    gateway_host: str
    gateway_port: int
    client_id: int
    preflight_hash: str

    def __post_init__(self) -> None:
        if not self.catalogue_name or len(self.catalogue_hash) != 64:
            raise ValueError("IBKR preflight requires a named, hashed candidate catalogue")
        if not 1 <= self.candidate_count <= 100:
            raise ValueError("IBKR preflight requires between one and 100 candidates")
        _validate_gateway(self.gateway_host, self.gateway_port, self.client_id)
        if len(self.preflight_hash) != 64:
            raise ValueError("IBKR preflight hash must be SHA-256")

    def as_json_value(self) -> dict[str, JsonValue]:
        payload = _preflight_payload(
            catalogue_name=self.catalogue_name,
            catalogue_hash=self.catalogue_hash,
            candidate_count=self.candidate_count,
            gateway_host=self.gateway_host,
            gateway_port=self.gateway_port,
            client_id=self.client_id,
        )
        return {**payload, "preflight_hash": self.preflight_hash}


def build_ibkr_capability_preflight(
    *,
    catalogue_name: str,
    catalogue_hash: str,
    candidate_count: int,
    gateway_host: str,
    gateway_port: int,
    client_id: int,
) -> IbkrCapabilityPreflight:
    """Validate all local inputs and stop explicitly before account-gated Gateway I/O."""

    _validate_gateway(gateway_host, gateway_port, client_id)
    payload = _preflight_payload(
        catalogue_name=catalogue_name,
        catalogue_hash=catalogue_hash,
        candidate_count=candidate_count,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        client_id=client_id,
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return IbkrCapabilityPreflight(
        catalogue_name=catalogue_name,
        catalogue_hash=catalogue_hash,
        candidate_count=candidate_count,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        client_id=client_id,
        preflight_hash=hashlib.sha256(encoded).hexdigest(),
    )


def _preflight_payload(
    *,
    catalogue_name: str,
    catalogue_hash: str,
    candidate_count: int,
    gateway_host: str,
    gateway_port: int,
    client_id: int,
) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "provider": "ibkr",
        "environment": "paper",
        "status": "OPERATOR_AUTHENTICATION_REQUIRED",
        "catalogue_name": catalogue_name,
        "catalogue_hash": catalogue_hash,
        "candidate_count": candidate_count,
        "gateway": {
            "host": gateway_host,
            "port": gateway_port,
            "client_id": client_id,
        },
        "selection_authority": False,
        "external_io_performed": False,
    }


def _validate_gateway(host: str, port: int, client_id: int) -> None:
    if not host or len(host) > 253 or any(character.isspace() for character in host):
        raise ValueError("IBKR Gateway host must be a bounded non-whitespace value")
    if not 1 <= port <= 65535:
        raise ValueError("IBKR Gateway port must be between 1 and 65535")
    if client_id <= 0:
        raise ValueError("IBKR client ID must be positive; client ID zero is not permitted")


@dataclass(frozen=True, slots=True)
class IbkrCapabilityReview:
    """Immutable, non-authoritative output from a bounded paper-Gateway probe."""

    review_hash: str
    payload: Mapping[str, JsonValue]

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.payload, "review_hash": self.review_hash}


def build_ibkr_capability_review(
    *,
    catalogue_name: str,
    catalogue_hash: str,
    instruments: Sequence[Instrument],
    probe_spec_name: str,
    probe_spec_hash: str,
    results: Sequence[IbkrCandidateCapability],
    observed_at: datetime,
) -> IbkrCapabilityReview:
    """Build complete review evidence without selecting a provider contract."""

    require_utc(observed_at, "IBKR capability review observed_at")
    if not catalogue_name or len(catalogue_hash) != 64:
        raise ValueError("IBKR capability review requires a named, hashed catalogue")
    if not probe_spec_name or len(probe_spec_hash) != 64:
        raise ValueError("IBKR capability review requires a named, hashed probe spec")
    expected = {instrument.instrument_id: instrument for instrument in instruments}
    if not expected or len(expected) != len(instruments):
        raise ValueError("IBKR capability review instruments must be non-empty and unique")
    by_instrument: dict[InstrumentId, list[IbkrCandidateCapability]] = {
        instrument_id: [] for instrument_id in expected
    }
    queries: set[IbkrContractQuery] = set()
    for result in results:
        if result.query.instrument_id not in expected:
            raise ValueError("IBKR capability review contains an extraneous instrument")
        if result.query in queries:
            raise ValueError("IBKR capability review contains a duplicate query result")
        queries.add(result.query)
        by_instrument[result.query.instrument_id].append(result)
    missing = set(expected) - set(by_instrument)
    if missing:
        raise ValueError("IBKR capability review is missing candidate instruments")

    instrument_payloads: list[JsonValue] = []
    for instrument in instruments:
        candidate_results = sorted(
            by_instrument[instrument.instrument_id],
            key=lambda result: (
                result.query.symbol,
                result.query.security_type,
                result.query.exchange,
                result.query.currency,
            ),
        )
        if not candidate_results:
            raise ValueError("IBKR capability review is missing a query result for a candidate")
        contract_count = sum(len(result.contracts) for result in candidate_results)
        instrument_payloads.append(
            {
                "instrument_id": str(instrument.instrument_id),
                "display_name": instrument.display_name,
                "status": (
                    "OPERATOR_SELECTION_REQUIRED" if contract_count else "NO_RETURNED_CONTRACT"
                ),
                "returned_contract_count": contract_count,
                "queries": [to_json_value(result) for result in candidate_results],
            }
        )
    payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "provider": "ibkr",
        "environment": "paper",
        "catalogue_name": catalogue_name,
        "catalogue_hash": catalogue_hash,
        "probe_spec_name": probe_spec_name,
        "probe_spec_hash": probe_spec_hash,
        "observed_at": to_json_value(observed_at),
        "selection_authority": False,
        "external_io_performed": True,
        "instruments": instrument_payloads,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return IbkrCapabilityReview(review_hash=hashlib.sha256(encoded).hexdigest(), payload=payload)
