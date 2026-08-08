"""Reviewed configuration and composition for the IBKR native capture path.

The candidate-universe TOML is intentionally not read here.  Native capture is
authorised only by a reviewed file containing both the exact provider listing
and its immutable conId contract evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from qtrad.adapters.ibkr.capability import IbkrApiIdentity, IbkrGatewayEndpoint
from qtrad.adapters.ibkr.market_data import IbkrNativeMarketDataAdapter
from qtrad.adapters.ibkr.market_hours import ibkr_contract_is_expected_active
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.modes import BrokerEnvironment
from qtrad.ports.capture_feed import CaptureIdentity
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.settings import Settings


@dataclass(frozen=True, slots=True)
class IbkrNativeCaptureConfiguration:
    """The complete reviewed input needed before an IBKR socket is opened."""

    listings: tuple[ProviderListing, ...]
    contract_evidence: Mapping[ProviderListingId, IbkrContractEvidence]
    configuration_hash: str
    capture_source_id: str = "ibkr-paper-v1"
    universe_id: str = "capture-ibkr-v1"

    def __post_init__(self) -> None:
        if not self.listings:
            raise ValueError("IBKR native capture configuration must contain listings")
        if self.capture_source_id != "ibkr-paper-v1":
            raise ValueError("IBKR native capture source identity is fixed")
        if self.universe_id != "capture-ibkr-v1":
            raise ValueError("IBKR native capture universe identity is fixed")
        if len(self.configuration_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.configuration_hash
        ):
            raise ValueError("IBKR native configuration hash must be a lower-case SHA-256")
        listing_ids = tuple(listing.listing_id for listing in self.listings)
        if len(set(listing_ids)) != len(listing_ids):
            raise ValueError("IBKR native configuration listings must be unique")
        if set(self.contract_evidence) != set(listing_ids):
            raise ValueError("IBKR native configuration must provide exact contract evidence")
        con_ids = tuple(evidence.con_id for evidence in self.contract_evidence.values())
        if len(set(con_ids)) != len(con_ids):
            raise ValueError("IBKR native configuration conIds must be unique")
        if any(
            listing_id.provider != "ibkr" or listing_id.environment != "IBKR_PAPER"
            for listing_id in listing_ids
        ):
            raise ValueError("IBKR native configuration listings must use ibkr/IBKR_PAPER")

    @property
    def identity(self) -> CaptureIdentity:
        return CaptureIdentity(
            provider="ibkr",
            environment=BrokerEnvironment.IBKR_PAPER.value,
            source_class=MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
            capture_source_id=self.capture_source_id,
            universe_id=self.universe_id,
            configuration_hash=self.configuration_hash,
        )

    def is_expected_active(self, listing_id: ProviderListingId, observed_at: datetime) -> bool:
        return ibkr_contract_is_expected_active(self.contract_evidence[listing_id], observed_at)

    def expected_active_instrument_ids(self, observed_at: datetime) -> tuple[str, ...]:
        return tuple(
            str(listing.instrument_id)
            for listing in self.listings
            if self.is_expected_active(listing.listing_id, observed_at)
        )

    @classmethod
    def from_reviewed(
        cls,
        listings: Sequence[ProviderListing],
        contract_evidence: Mapping[ProviderListingId, IbkrContractEvidence],
    ) -> IbkrNativeCaptureConfiguration:
        payload = to_json_value(
            {
                "capture_source_id": "ibkr-paper-v1",
                "universe_id": "capture-ibkr-v1",
                "provider": "ibkr",
                "environment": BrokerEnvironment.IBKR_PAPER.value,
                "listings": tuple(listings),
                "contract_evidence": tuple(
                    {"listing_id": str(listing_id), "evidence": evidence}
                    for listing_id, evidence in sorted(
                        contract_evidence.items(), key=lambda item: str(item[0])
                    )
                ),
            }
        )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return cls(
            listings=tuple(listings),
            contract_evidence=dict(contract_evidence),
            configuration_hash=hashlib.sha256(encoded).hexdigest(),
        )


def load_reviewed_configuration(path: Path) -> IbkrNativeCaptureConfiguration:
    """Load a checked-in/offline JSON review artifact, never a candidate TOML."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read reviewed IBKR capture configuration: {path}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("reviewed IBKR capture configuration must be an object")
    if (
        payload.get("capture_source_id") != "ibkr-paper-v1"
        or payload.get("universe_id") != "capture-ibkr-v1"
    ):
        raise ValueError("reviewed IBKR capture configuration has an invalid fixed identity")
    listings_payload = payload.get("listings")
    if not isinstance(listings_payload, Sequence) or isinstance(listings_payload, (str, bytes)):
        raise ValueError("reviewed IBKR capture configuration requires listings")
    listings = tuple(_listing(cast(Mapping[str, object], item)) for item in listings_payload)
    evidence: dict[ProviderListingId, IbkrContractEvidence] = {}
    for item in listings_payload:
        listing_payload = cast(Mapping[str, object], item)
        listing_id = ProviderListingId(
            str(listing_payload["provider"]),
            str(listing_payload["environment"]),
            str(listing_payload["external_id"]),
        )
        evidence[listing_id] = _evidence(cast(Mapping[str, object], listing_payload["evidence"]))
    configuration = IbkrNativeCaptureConfiguration.from_reviewed(listings, evidence)
    supplied_hash = payload.get("configuration_hash")
    if supplied_hash is not None and supplied_hash != configuration.configuration_hash:
        raise ValueError("reviewed IBKR capture configuration hash does not match its contents")
    return configuration


def build_ibkr_native_adapter(
    settings: Settings,
    configuration: IbkrNativeCaptureConfiguration,
    *,
    clock,
    client_factory=None,
) -> IbkrNativeMarketDataAdapter:
    if settings.provider != "ibkr":
        raise ValueError("IBKR native adapter requires QTRAD_PROVIDER=ibkr")
    if settings.ibkr_capture_configuration_hash is not None and (
        settings.ibkr_capture_configuration_hash != configuration.configuration_hash
    ):
        raise ValueError("configured IBKR capture hash does not match the reviewed configuration")
    return IbkrNativeMarketDataAdapter(
        IbkrGatewayEndpoint(
            host=settings.ibkr_gateway_host,
            port=settings.ibkr_gateway_port,
            client_id=settings.ibkr_client_id,
        ),
        pre_reviewed_listings=configuration.listings,
        contract_evidence=configuration.contract_evidence,
        environment=BrokerEnvironment.IBKR_PAPER,
        request_timeout_seconds=settings.ibkr_historical_timeout_seconds,
        upstream_recovery_timeout_seconds=settings.ibkr_upstream_recovery_timeout_seconds,
        connect_timeout_seconds=settings.ibkr_connect_timeout_seconds,
        handshake_timeout_seconds=settings.ibkr_handshake_timeout_seconds,
        server_time_timeout_seconds=settings.ibkr_server_time_timeout_seconds,
        client_factory=client_factory,
        api_identity=(
            IbkrApiIdentity(
                package_fingerprint=settings.ibkr_api_package_fingerprint,
                version=settings.ibkr_api_version,
            )
            if settings.ibkr_api_package_fingerprint is not None
            else None
        ),
        clock=clock.now,
        freshness_max_age_seconds=settings.ibkr_capture_freshness_seconds,
        expected_active_policy=lambda listing, observed_at: configuration.is_expected_active(
            listing.listing_id, observed_at
        ),
    )


def _listing(payload: Mapping[str, object]) -> ProviderListing:
    listing_id = ProviderListingId(
        str(payload["provider"]), str(payload["environment"]), str(payload["external_id"])
    )
    valid_from = _datetime(payload["valid_from"])
    valid_to = _datetime(payload["valid_to"]) if payload.get("valid_to") else None
    economics = cast(Mapping[str, JsonValue], payload.get("economics", {}))
    return ProviderListing(
        listing_id=listing_id,
        instrument_id=InstrumentId(str(payload["instrument_id"])),
        display_name=str(payload["display_name"]),
        product_type=ProductType(str(payload["product_type"])),
        currency=str(payload["currency"]),
        minimum_deal_size=Decimal(str(payload["minimum_deal_size"])),
        price_increment=(
            Decimal(str(payload["price_increment"]))
            if payload.get("price_increment") is not None
            else None
        ),
        valid_from=valid_from,
        valid_to=valid_to,
        metadata_version=str(payload["metadata_version"]),
        economics=economics,
    )


def _evidence(payload: Mapping[str, object]) -> IbkrContractEvidence:
    return IbkrContractEvidence(
        con_id=int(cast(str | int, payload["con_id"])),
        symbol=str(payload["symbol"]),
        local_symbol=str(payload["local_symbol"]),
        security_type=str(payload["security_type"]),
        exchange=str(payload["exchange"]),
        currency=str(payload["currency"]),
        trading_class=_optional(payload.get("trading_class")),
        multiplier=_optional(payload.get("multiplier")),
        minimum_tick=(
            Decimal(str(payload["minimum_tick"])) if payload.get("minimum_tick") else None
        ),
        market_rule_ids=tuple(
            str(item) for item in cast(Sequence[object], payload.get("market_rule_ids", ()))
        ),
        valid_exchanges=tuple(
            str(item) for item in cast(Sequence[object], payload.get("valid_exchanges", ()))
        ),
        long_name=_optional(payload.get("long_name")),
        underlier_con_id=(
            int(cast(str | int, payload["underlier_con_id"]))
            if payload.get("underlier_con_id")
            else None
        ),
        timezone=_optional(payload.get("timezone")),
        trading_hours=_optional(payload.get("trading_hours")),
        liquid_hours=_optional(payload.get("liquid_hours")),
        primary_exchange=_optional(payload.get("primary_exchange")),
        contract_month=_optional(payload.get("contract_month")),
    )


def _optional(value: object) -> str | None:
    return str(value) if value is not None else None


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(UTC)
