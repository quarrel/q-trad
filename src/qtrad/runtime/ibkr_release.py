"""Provider-neutral mechanics for immutable IBKR native-capture releases.

Stage policy, provider-authority replay, and qualification decisions remain in
their stage modules. This module owns byte-stable envelope construction,
authority-file identity, canonical loading, and create-only publication.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import ProviderListing
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.ibkr_native_capture import (
    IbkrNativeCaptureConfiguration,
    load_reviewed_configuration,
)

AUTHORITY_HASH_FIELDS = (
    "capability_review_sha256",
    "operator_selection_sha256",
    "contract_selection_sha256",
    "catalogue_sha256",
    "probe_spec_sha256",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def require_sha256(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")


@dataclass(frozen=True, slots=True)
class IbkrPromotionAuthority:
    """Byte identities of the five files replayed for one release."""

    capability_review_sha256: str
    operator_selection_sha256: str
    contract_selection_sha256: str
    catalogue_sha256: str
    probe_spec_sha256: str

    def __post_init__(self) -> None:
        for field_name in AUTHORITY_HASH_FIELDS:
            require_sha256(getattr(self, field_name), f"IBKR {field_name}")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {field_name: getattr(self, field_name) for field_name in AUTHORITY_HASH_FIELDS}

    @classmethod
    def from_json_value(cls, value: object) -> IbkrPromotionAuthority:
        if not isinstance(value, Mapping) or set(value) != set(AUTHORITY_HASH_FIELDS):
            raise ValueError("IBKR release requires exact promotion authority identities")
        authority = cast(Mapping[str, object], value)
        return cls(**{field: str(authority[field]) for field in AUTHORITY_HASH_FIELDS})


@dataclass(frozen=True, slots=True)
class IbkrAuthorityPaths:
    capability_review_path: Path
    operator_selection_path: Path
    contract_selection_path: Path
    catalogue_path: Path
    probe_spec_path: Path

    def as_kwargs(self) -> dict[str, Path]:
        return {
            "capability_review_path": self.capability_review_path,
            "operator_selection_path": self.operator_selection_path,
            "contract_selection_path": self.contract_selection_path,
            "catalogue_path": self.catalogue_path,
            "probe_spec_path": self.probe_spec_path,
        }


def authority_identity(paths: IbkrAuthorityPaths, *, label: str) -> IbkrPromotionAuthority:
    return IbkrPromotionAuthority(
        capability_review_sha256=_sha256_file(
            paths.capability_review_path, label, "capability review"
        ),
        operator_selection_sha256=_sha256_file(
            paths.operator_selection_path, label, "operator selection"
        ),
        contract_selection_sha256=_sha256_file(
            paths.contract_selection_path, label, "contract selection"
        ),
        catalogue_sha256=_sha256_file(paths.catalogue_path, label, "catalogue"),
        probe_spec_sha256=_sha256_file(paths.probe_spec_path, label, "probe spec"),
    )


def configuration_payload(
    configuration: IbkrNativeCaptureConfiguration,
    authority: IbkrPromotionAuthority,
    *,
    contract: str,
    release_stage: str | None = None,
    parent_release: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Build a canonical envelope; omitted v2 fields preserve B3 v1 bytes."""

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
                    "valid_to": (
                        listing.valid_to.isoformat() if listing.valid_to is not None else None
                    ),
                    "metadata_version": listing.metadata_version,
                    "economics": cast(dict[str, JsonValue], to_json_value(listing.economics)),
                    "evidence": cast(dict[str, JsonValue], to_json_value(evidence)),
                },
            )
        )
    payload: dict[str, JsonValue] = {
        "contract": contract,
        "capture_source_id": configuration.capture_source_id,
        "universe_id": configuration.universe_id,
        "configuration_hash": configuration.configuration_hash,
        "promotion_authority": authority.as_json_value(),
        "listings": listings,
    }
    if release_stage is not None:
        payload["release_stage"] = release_stage
    if parent_release is not None:
        payload["parent_release"] = dict(parent_release)
    return payload


def write_release(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(encoded)


def load_release_document(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read {label} release configuration: {path}") from error
    if not isinstance(document, Mapping):
        raise ValueError(f"{label} release configuration must be an object")
    return cast(Mapping[str, object], document)


def load_canonical_release_configuration(
    path: Path,
    *,
    contract: str,
    authority: IbkrPromotionAuthority,
    label: str,
    release_stage: str | None = None,
    parent_release: Mapping[str, JsonValue] | None = None,
) -> IbkrNativeCaptureConfiguration:
    document = load_release_document(path, label=label)
    if document.get("contract") != contract:
        raise ValueError(f"{label} release configuration contract marker is unsupported")
    configuration = load_reviewed_configuration(path)
    expected = configuration_payload(
        configuration,
        authority,
        contract=contract,
        release_stage=release_stage,
        parent_release=parent_release,
    )
    if document != expected:
        raise ValueError(f"{label} release configuration contains non-canonical fields")
    return configuration


def sha256_path(path: Path, *, label: str) -> str:
    return _sha256_file(path, label, "artifact")


def _sha256_file(path: Path, label: str, kind: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError(f"unable to read {label} {kind}: {path}") from error


def review_contract_evidence(
    review: Mapping[str, object],
    instrument_id: InstrumentId,
    con_id: int,
    *,
    label: str,
) -> IbkrContractEvidence:
    """Reconstruct complete contract evidence from an authenticated capability review."""

    instruments = cast(Sequence[object], review["instruments"])
    for raw_instrument_value in instruments:
        instrument = cast(Mapping[str, object], raw_instrument_value)
        if str(instrument["instrument_id"]) != str(instrument_id):
            continue
        for raw_result_value in cast(Sequence[object], instrument["queries"]):
            result = cast(Mapping[str, object], raw_result_value)
            for raw_contract_value in cast(Sequence[object], result["contracts"]):
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
                    trading_class=_optional_string(payload["trading_class"]),
                    multiplier=_optional_string(payload["multiplier"]),
                    minimum_tick=Decimal(str(minimum_tick)) if minimum_tick is not None else None,
                    market_rule_ids=tuple(
                        str(item) for item in cast(Sequence[object], payload["market_rule_ids"])
                    ),
                    valid_exchanges=tuple(
                        str(item) for item in cast(Sequence[object], payload["valid_exchanges"])
                    ),
                    long_name=_optional_string(payload["long_name"]),
                    underlier_con_id=(
                        int(cast(str | int, payload["underlier_con_id"]))
                        if payload["underlier_con_id"] is not None
                        else None
                    ),
                    timezone=_optional_string(payload["timezone"]),
                    trading_hours=_optional_string(payload["trading_hours"]),
                    liquid_hours=_optional_string(payload["liquid_hours"]),
                    primary_exchange=_optional_string(payload.get("primary_exchange")),
                    contract_month=_optional_string(payload.get("contract_month")),
                )
    raise ValueError(
        f"{label} authority review has no exact contract for {instrument_id} conId {con_id}"
    )


def listing_mismatches(actual: ProviderListing, expected: ProviderListing) -> tuple[str, ...]:
    """Compare every persisted provider-listing field."""

    fields = (
        "listing_id",
        "instrument_id",
        "display_name",
        "product_type",
        "currency",
        "minimum_deal_size",
        "price_increment",
        "valid_from",
        "valid_to",
        "metadata_version",
        "economics",
    )
    return tuple(field for field in fields if getattr(actual, field) != getattr(expected, field))


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
