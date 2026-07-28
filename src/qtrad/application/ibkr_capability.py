"""Deterministic preflight for the account-gated IBKR capability probe."""

import hashlib
import json
from dataclasses import dataclass

from qtrad.domain.events import JsonValue


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
