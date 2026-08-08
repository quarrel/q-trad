"""B3 exact-two IBKR release promotion and offline deployment checks.

This module consumes an already reviewed B2 native-capture configuration.  It
does not discover contracts, contact IBKR, inspect a live database, or mutate a
host.  B3 promotion is a deterministic subset operation over immutable B2
evidence.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from qtrad.adapters.ibkr.market_hours import IbkrMarketActivity
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProviderListing
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.ibkr_historical import (
    load_ibkr_capability_review,
    verify_ibkr_contract_selection,
)
from qtrad.runtime.ibkr_native_capture import (
    IbkrNativeCaptureConfiguration,
    load_reviewed_configuration,
)

B3_RELEASE_CONTRACT = "qtrad-ibkr-native-release-v1"
B3_CAPTURE_SOURCE_ID = "ibkr-paper-v1"
B3_UNIVERSE_ID = "capture-ibkr-v1"
B3_DATABASE_NAME = "qtrad_ibkr"
B3_DATABASE_ENVIRONMENT = "IBKR_PAPER"
B3_SCHEMA_HEAD = "0014"

B3_TARGETS: tuple[tuple[str, str, int], ...] = (
    ("fx:aud-usd", "aud-usd", 14_433_401),
    ("index:australia-200", "australia-200", 111_987_484),
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_PRIVATE_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_FORBIDDEN_DESCRIPTOR_WORDS = (
    "password",
    "secret",
    "credential",
    "token",
    "2fa",
    "username",
)

_EXPECTED_SERVICE_IDENTITIES = (
    "qtrad-ibkr-ingest.service",
    "qtrad-ibkr-api.service",
    "qtrad-ibkr-health.timer",
    "qtrad-ibkr-backup.timer",
)


def _require_hash(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")


def _review_optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _review_contract_evidence(
    review: Mapping[str, object],
    instrument_id: InstrumentId,
    con_id: int,
) -> IbkrContractEvidence:
    instruments = cast(Sequence[object], review["instruments"])
    for raw_instrument_value in instruments:
        instrument = cast(Mapping[str, object], raw_instrument_value)
        if str(instrument["instrument_id"]) != str(instrument_id):
            continue
        query_results = cast(Sequence[object], instrument["queries"])
        for raw_result_value in query_results:
            result = cast(Mapping[str, object], raw_result_value)
            contracts = cast(Sequence[object], result["contracts"])
            for raw_contract_value in contracts:
                payload = cast(Mapping[str, object], raw_contract_value)
                if int(cast(str | int, payload["con_id"])) != con_id:
                    continue
                minimum_tick = payload["minimum_tick"]
                return IbkrContractEvidence(
                    con_id=con_id,
                    symbol=str(payload["symbol"]),
                    local_symbol=str(payload["local_symbol"]),
                    security_type=str(payload["security_type"]),
                    exchange=str(payload["exchange"]),
                    currency=str(payload["currency"]),
                    trading_class=_review_optional_string(payload["trading_class"]),
                    multiplier=_review_optional_string(payload["multiplier"]),
                    minimum_tick=Decimal(str(minimum_tick)) if minimum_tick is not None else None,
                    market_rule_ids=tuple(
                        str(item) for item in cast(Sequence[object], payload["market_rule_ids"])
                    ),
                    valid_exchanges=tuple(
                        str(item) for item in cast(Sequence[object], payload["valid_exchanges"])
                    ),
                    long_name=_review_optional_string(payload["long_name"]),
                    underlier_con_id=(
                        int(cast(str | int, payload["underlier_con_id"]))
                        if payload["underlier_con_id"] is not None
                        else None
                    ),
                    timezone=_review_optional_string(payload["timezone"]),
                    trading_hours=_review_optional_string(payload["trading_hours"]),
                    liquid_hours=_review_optional_string(payload["liquid_hours"]),
                    primary_exchange=_review_optional_string(payload.get("primary_exchange")),
                    contract_month=_review_optional_string(payload.get("contract_month")),
                )
    raise ValueError(
        f"B3 authority review has no exact contract for {instrument_id} conId {con_id}"
    )


def _verify_b3_provider_authority(
    source: IbkrNativeCaptureConfiguration,
    *,
    capability_review_path: Path,
    operator_selection_path: Path,
    contract_selection_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
) -> None:
    selection = verify_ibkr_contract_selection(
        contract_selection_path,
        capability_review_path=capability_review_path,
        operator_selection_path=operator_selection_path,
        catalogue_path=catalogue_path,
        probe_spec_path=probe_spec_path,
    )
    review = load_ibkr_capability_review(
        capability_review_path,
        catalogue_path=catalogue_path,
        probe_spec_path=probe_spec_path,
    )
    decisions = {decision.instrument_id: decision for decision in selection.decisions}
    for instrument_id_text, _, expected_con_id in B3_TARGETS:
        instrument_id = InstrumentId(instrument_id_text)
        decision = decisions.get(instrument_id)
        if decision is None or not decision.acquisition_eligible or decision.fingerprint is None:
            raise ValueError(f"B3 authority does not accept the exact contract for {instrument_id}")
        if decision.fingerprint.con_id != expected_con_id:
            raise ValueError(f"B3 authority conId mismatch for {instrument_id}")
        listing = next(
            (item for item in source.listings if item.instrument_id == instrument_id),
            None,
        )
        if listing is None:
            raise ValueError(f"B3 source is missing the authority instrument {instrument_id}")
        contract = source.contract_evidence.get(listing.listing_id)
        if contract is None:
            raise ValueError(f"B3 source is missing provider evidence for {instrument_id}")
        authenticated = _review_contract_evidence(review, instrument_id, contract.con_id)
        if authenticated != contract:
            raise ValueError(
                f"B3 source provider evidence does not match the authenticated "
                f"review for {instrument_id}"
            )
        if listing.currency != authenticated.currency:
            raise ValueError(
                f"B3 source listing currency does not match authenticated review "
                f"for {instrument_id}"
            )
        if listing.price_increment != authenticated.minimum_tick:
            raise ValueError(
                f"B3 source listing price increment does not match authenticated "
                f"minimum tick for {instrument_id}"
            )
        if listing.economics:
            raise ValueError(
                f"B3 source listing economics are not independently authenticated "
                f"for {instrument_id}"
            )


def promote_b3_configuration(
    source: IbkrNativeCaptureConfiguration,
    *,
    capability_review_path: Path,
    operator_selection_path: Path,
    contract_selection_path: Path,
    catalogue_path: Path,
    probe_spec_path: Path,
) -> IbkrNativeCaptureConfiguration:
    """Create the exact-two subset only from an authenticated provider closure."""

    _verify_b3_provider_authority(
        source,
        capability_review_path=capability_review_path,
        operator_selection_path=operator_selection_path,
        contract_selection_path=contract_selection_path,
        catalogue_path=catalogue_path,
        probe_spec_path=probe_spec_path,
    )
    evidence: dict[ProviderListingId, IbkrContractEvidence] = {}
    listings: list[ProviderListing] = []
    for instrument_id_text, expected_external_id, expected_con_id in B3_TARGETS:
        instrument_id = InstrumentId(instrument_id_text)
        matching = [item for item in source.listings if item.instrument_id == instrument_id]
        if len(matching) != 1:
            raise ValueError(f"B3 requires exactly one reviewed listing for {instrument_id}")
        listing = matching[0]
        if listing.listing_id != ProviderListingId("ibkr", "IBKR_PAPER", expected_external_id):
            raise ValueError(f"B3 listing identity is not reviewed for {instrument_id}")
        contract = source.contract_evidence.get(listing.listing_id)
        if contract is None:
            raise ValueError(f"B3 exact contract evidence is missing for {instrument_id}")
        if contract.con_id != expected_con_id:
            raise ValueError(
                f"B3 conId mismatch for {instrument_id}: "
                f"expected {expected_con_id}, got {contract.con_id}"
            )
        listings.append(listing)
        evidence[listing.listing_id] = contract

    promoted = IbkrNativeCaptureConfiguration.from_reviewed(listings, evidence)
    if len(promoted.listings) != 2:
        raise ValueError("B3 release must contain exactly two listings")
    return promoted


def _configuration_payload(
    configuration: IbkrNativeCaptureConfiguration,
) -> dict[str, JsonValue]:
    listings: list[JsonValue] = []
    for listing in sorted(configuration.listings, key=lambda item: str(item.listing_id)):
        evidence = configuration.contract_evidence[listing.listing_id]
        listings.append(
            cast(
                JsonValue,
                {
                    "provider": listing.listing_id.provider,
                    "environment": listing.listing_id.environment,
                    "external_id": listing.listing_id.external_id,
                    "instrument_id": str(listing.instrument_id),
                    "display_name": listing.display_name,
                    "product_type": listing.product_type.value,
                    "currency": listing.currency,
                    "minimum_deal_size": str(listing.minimum_deal_size),
                    "price_increment": (
                        str(listing.price_increment)
                        if listing.price_increment is not None
                        else None
                    ),
                    "valid_from": listing.valid_from.isoformat(),
                    "valid_to": listing.valid_to.isoformat()
                    if listing.valid_to is not None
                    else None,
                    "metadata_version": listing.metadata_version,
                    "economics": cast(dict[str, JsonValue], to_json_value(listing.economics)),
                    "evidence": cast(dict[str, JsonValue], to_json_value(evidence)),
                },
            )
        )
    return cast(
        dict[str, JsonValue],
        {
            "contract": B3_RELEASE_CONTRACT,
            "capture_source_id": configuration.capture_source_id,
            "universe_id": configuration.universe_id,
            "configuration_hash": configuration.configuration_hash,
            "listings": listings,
        },
    )


def write_reviewed_configuration(
    path: Path,
    configuration: IbkrNativeCaptureConfiguration,
) -> None:
    """Write a reviewed configuration create-only, preserving old releases."""

    payload = _configuration_payload(configuration)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(encoded)


def verify_b3_configuration(
    configuration: IbkrNativeCaptureConfiguration,
    *,
    observed_at: datetime,
) -> dict[str, JsonValue]:
    """Return deterministic, machine-readable offline release evidence."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("B3 verification timestamp must be timezone-aware")
    observed_at = observed_at.astimezone(UTC)
    errors: list[str] = []
    try:
        expected_ids = {InstrumentId(item[0]) for item in B3_TARGETS}
        actual_ids = {item.instrument_id for item in configuration.listings}
        if actual_ids != expected_ids or len(configuration.listings) != 2:
            errors.append("configuration does not contain exactly the B3 two-instrument set")
        for instrument_id_text, expected_external_id, expected_con_id in B3_TARGETS:
            instrument_id = InstrumentId(instrument_id_text)
            matches = [
                item for item in configuration.listings if item.instrument_id == instrument_id
            ]
            if len(matches) != 1:
                errors.append(f"missing or duplicate listing: {instrument_id}")
                continue
            listing = matches[0]
            if listing.listing_id != ProviderListingId("ibkr", "IBKR_PAPER", expected_external_id):
                errors.append(f"listing identity mismatch: {instrument_id}")
                continue
            contract = configuration.contract_evidence.get(listing.listing_id)
            if contract is None:
                errors.append(f"missing exact contract evidence: {instrument_id}")
            elif contract.con_id != expected_con_id:
                errors.append(
                    f"conId mismatch: {instrument_id} expected {expected_con_id}, "
                    f"got {contract.con_id}"
                )
    except (KeyError, TypeError, ValueError) as error:
        errors.append(str(error))

    instruments: list[JsonValue] = []
    requires_refresh = False
    for listing in sorted(configuration.listings, key=lambda item: str(item.instrument_id)):
        activity = configuration.activity(listing.listing_id, observed_at)
        requires_refresh |= activity is IbkrMarketActivity.UNKNOWN
        evidence = configuration.contract_evidence[listing.listing_id]
        instruments.append(
            cast(
                JsonValue,
                {
                    "instrument_id": str(listing.instrument_id),
                    "listing_id": str(listing.listing_id),
                    "con_id": evidence.con_id,
                    "activity": activity.value,
                },
            )
        )

    return cast(
        dict[str, JsonValue],
        {
            "contract": B3_RELEASE_CONTRACT,
            "valid": not errors,
            "operational_ready": not errors and not requires_refresh,
            "requires_evidence_refresh": requires_refresh,
            "source": B3_CAPTURE_SOURCE_ID,
            "universe": B3_UNIVERSE_ID,
            "configuration_hash": configuration.configuration_hash,
            "observed_at": observed_at.isoformat(),
            "instrument_count": len(configuration.listings),
            "instruments": instruments,
            "errors": errors,
        },
    )


@dataclass(frozen=True, slots=True)
class IbkrB3DeploymentDescriptor:
    """Non-secret identity required by the B3 host preflight."""

    application_commit: str
    image: str
    configuration_path: str
    configuration_hash: str
    api_package_fingerprint: str
    gateway_archive_sha256: str
    api_version: str
    gateway_version: str
    ibc_version: str
    client_id: int
    gateway_host: str
    gateway_port: int
    api_host: str
    api_port: int
    database_name: str
    database_url_environment: str
    checkpoint_root: str
    schema_head: str = B3_SCHEMA_HEAD
    ingest_service: str = "qtrad-ibkr-ingest.service"
    api_service: str = "qtrad-ibkr-api.service"
    health_timer: str = "qtrad-ibkr-health.timer"
    backup_timer: str = "qtrad-ibkr-backup.timer"

    def __post_init__(self) -> None:
        if not _COMMIT.fullmatch(self.application_commit):
            raise ValueError("B3 application_commit must be a full lower-case Git commit")
        if not _IMAGE.fullmatch(self.image):
            raise ValueError("B3 image must be an immutable @sha256 digest")
        _require_hash(self.configuration_hash, "B3 configuration_hash")
        if not Path(self.configuration_path).is_absolute():
            raise ValueError("B3 configuration_path must be absolute")
        _require_hash(self.api_package_fingerprint, "B3 API package fingerprint")
        _require_hash(self.gateway_archive_sha256, "B3 Gateway archive")
        if self.api_version != self.gateway_version:
            raise ValueError("B3 API and Gateway versions must match")
        if self.client_id <= 0:
            raise ValueError("B3 client_id must be positive")
        if self.gateway_host not in _PRIVATE_HOSTS or self.api_host not in _PRIVATE_HOSTS:
            raise ValueError("B3 Gateway and API hosts must be private loopback hosts")
        if self.gateway_port != 4002:
            raise ValueError("B3 Gateway API port must be 4002")
        if self.api_port <= 0 or self.api_port > 65535:
            raise ValueError("B3 API port must be valid")
        if self.database_name != B3_DATABASE_NAME:
            raise ValueError("B3 must use the dedicated IBKR database")
        if self.database_url_environment != "QTRAD_DATABASE_URL":
            raise ValueError("B3 database URL must use QTRAD_DATABASE_URL")
        if not self.checkpoint_root.startswith("/"):
            raise ValueError("B3 checkpoint_root must be absolute")
        if self.schema_head != B3_SCHEMA_HEAD:
            raise ValueError("B3 migration head is not the reviewed current head")
        if self.ibc_version != "3.24.1":
            raise ValueError("B3 IBC version is not the reviewed version")
        if (self.ingest_service, self.api_service, self.health_timer, self.backup_timer) != (
            _EXPECTED_SERVICE_IDENTITIES
        ):
            raise ValueError("B3 service identities must use the reviewed unit templates")

    @classmethod
    def from_toml(cls, path: Path) -> IbkrB3DeploymentDescriptor:
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"unable to read B3 deployment descriptor: {path}") from error
        _reject_secret_keys(document)
        release = _table(document, "release")
        ibkr = _table(document, "ibkr")
        network = _table(document, "network")
        database = _table(document, "database")
        services = _table(document, "services")
        return cls(
            application_commit=_string(release, "application_commit"),
            image=_string(release, "image"),
            configuration_path=_string(release, "configuration_path"),
            configuration_hash=_string(release, "configuration_hash"),
            api_package_fingerprint=_string(release, "api_package_fingerprint"),
            gateway_archive_sha256=_string(ibkr, "gateway_archive_sha256"),
            api_version=_string(ibkr, "api_version"),
            gateway_version=_string(ibkr, "gateway_version"),
            ibc_version=_string(ibkr, "ibc_version"),
            client_id=_integer(ibkr, "client_id"),
            gateway_host=_string(network, "gateway_host"),
            gateway_port=_integer(network, "gateway_port"),
            api_host=_string(network, "api_host"),
            api_port=_integer(network, "api_port"),
            database_name=_string(database, "name"),
            database_url_environment=_string(database, "url_environment"),
            checkpoint_root=_string(database, "checkpoint_root"),
            schema_head=str(release.get("schema_head", B3_SCHEMA_HEAD)),
            ingest_service=str(services.get("ingest", cls.ingest_service)),
            api_service=str(services.get("api", cls.api_service)),
            health_timer=str(services.get("health_timer", cls.health_timer)),
            backup_timer=str(services.get("backup_timer", cls.backup_timer)),
        )


def load_b3_deployment_descriptor(path: Path) -> IbkrB3DeploymentDescriptor:
    return IbkrB3DeploymentDescriptor.from_toml(path)


def b3_preflight(
    descriptor_path: Path,
    *,
    repository_root: Path,
    observed_at: datetime,
) -> dict[str, JsonValue]:
    """Verify release/config/unit identity without host or provider I/O."""

    errors: list[str] = []
    try:
        descriptor = load_b3_deployment_descriptor(descriptor_path)
    except ValueError as error:
        return {
            "contract": B3_RELEASE_CONTRACT,
            "valid": False,
            "operational_ready": False,
            "errors": [str(error)],
        }

    configuration_path = Path(descriptor.configuration_path)
    if not configuration_path.is_absolute():
        configuration_path = repository_root / configuration_path
    try:
        configuration = load_reviewed_configuration(configuration_path)
    except ValueError as error:
        configuration = None
        errors.append(str(error))

    if configuration is not None:
        if configuration.configuration_hash != descriptor.configuration_hash:
            errors.append("deployment descriptor/configuration hash mismatch")
        report = verify_b3_configuration(configuration, observed_at=observed_at)
        errors.extend(cast(list[str], report["errors"]))
    else:
        report = {
            "valid": False,
            "operational_ready": False,
            "requires_evidence_refresh": False,
            "instruments": [],
        }

    expected_units = {
        descriptor.ingest_service,
        descriptor.api_service,
        descriptor.health_timer,
        descriptor.backup_timer,
    }
    for unit in expected_units:
        unit_path = repository_root / "ops" / "ibkr" / f"{unit}.example"
        if not unit_path.is_file():
            errors.append(f"required B3 unit template is missing: {unit}")

    if descriptor.gateway_host not in _PRIVATE_HOSTS or descriptor.api_host not in _PRIVATE_HOSTS:
        errors.append("B3 endpoints are not private loopback endpoints")

    return cast(
        dict[str, JsonValue],
        {
            "contract": B3_RELEASE_CONTRACT,
            "valid": not errors,
            "operational_ready": not errors and not bool(report.get("requires_evidence_refresh")),
            "requires_evidence_refresh": bool(report.get("requires_evidence_refresh")),
            "application_commit": descriptor.application_commit,
            "image": descriptor.image,
            "source": B3_CAPTURE_SOURCE_ID,
            "universe": B3_UNIVERSE_ID,
            "configuration_hash": descriptor.configuration_hash,
            "configuration_path": descriptor.configuration_path,
            "api_package_fingerprint": descriptor.api_package_fingerprint,
            "gateway_archive_sha256": descriptor.gateway_archive_sha256,
            "ibc_version": descriptor.ibc_version,
            "database_url_environment": descriptor.database_url_environment,
            "api_version": descriptor.api_version,
            "gateway_version": descriptor.gateway_version,
            "client_id": descriptor.client_id,
            "gateway_host": descriptor.gateway_host,
            "gateway_port": descriptor.gateway_port,
            "api_host": descriptor.api_host,
            "api_port": descriptor.api_port,
            "database_name": descriptor.database_name,
            "schema_head": descriptor.schema_head,
            "configuration": report,
            "errors": errors,
        },
    )


def _table(document: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = document.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"B3 descriptor requires [{name}]")
    return cast(Mapping[str, object], value)


def _string(document: Mapping[str, object], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"B3 descriptor field {name} must be a non-empty string")
    return value


def _integer(document: Mapping[str, object], name: str) -> int:
    value = document.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"B3 descriptor field {name} must be an integer")
    return value


def _reject_secret_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(word in key_text for word in _FORBIDDEN_DESCRIPTOR_WORDS):
                raise ValueError(f"B3 descriptor contains forbidden secret-bearing field: {key}")
            _reject_secret_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_secret_keys(child)
