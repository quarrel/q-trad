"""B4 exact-six IBKR release promotion gated by verified B3 qualification.

This module is offline-only. It never contacts IBKR, reads a live database, or
constructs a qualification capability from operator input.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from qtrad.adapters.ibkr.market_hours import IbkrMarketActivity
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_qualification import (
    IbkrQualificationStage,
    IbkrQualifiedContract,
    VerifiedB3Qualification,
    has_verified_ibkr_capture_qualification_provenance,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import AssetClass, ProductType, ProviderListing
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.ibkr_b3 import (
    B3_CAPTURE_SOURCE_ID,
    B3_RELEASE_CONTRACT,
    B3_TARGETS,
    B3_UNIVERSE_ID,
    IbkrB3DeploymentDescriptor,
    load_authenticated_b3_configuration,
    load_b3_deployment_descriptor,
)
from qtrad.runtime.ibkr_historical import (
    load_ibkr_capability_review,
    verify_ibkr_contract_selection,
)
from qtrad.runtime.ibkr_native_capture import IbkrNativeCaptureConfiguration
from qtrad.runtime.ibkr_qualification import (
    IbkrQualificationExpectation,
    verify_ibkr_capture_qualification,
)
from qtrad.runtime.ibkr_release import (
    IbkrAuthorityPaths,
    IbkrPromotionAuthority,
    authority_identity,
    configuration_payload,
    listing_mismatches,
    load_canonical_release_configuration,
    load_release_document,
    review_contract_evidence,
    sha256_path,
    write_release,
)
from qtrad.runtime.universe import load_capture_candidates

B4_RELEASE_CONTRACT = "qtrad-ibkr-native-release-v2"
B4_RELEASE_STAGE = "B4_EXACT_SIX"
B4_CAPTURE_SOURCE_ID = B3_CAPTURE_SOURCE_ID
B4_UNIVERSE_ID = B3_UNIVERSE_ID


@dataclass(frozen=True, slots=True)
class _B4ListingPolicy:
    instrument_id: str
    external_id: str
    display_name: str
    asset_class: AssetClass
    base_currency: str | None
    currency: str
    valid_from: datetime
    metadata_version: str


_B3_VALID_FROM = datetime(2026, 8, 8, tzinfo=UTC)
_B4_VALID_FROM = datetime(2026, 8, 10, tzinfo=UTC)
_B3_METADATA_VERSION = "ibkr-b3-review-v1"
_B4_METADATA_VERSION = "ibkr-b4-review-v1"

_B4_LISTING_POLICIES = (
    _B4ListingPolicy(
        "fx:aud-usd",
        "aud-usd",
        "AUD/USD",
        AssetClass.FX,
        "AUD",
        "USD",
        _B3_VALID_FROM,
        _B3_METADATA_VERSION,
    ),
    _B4ListingPolicy(
        "fx:eur-usd",
        "eur-usd",
        "EUR/USD",
        AssetClass.FX,
        "EUR",
        "USD",
        _B4_VALID_FROM,
        _B4_METADATA_VERSION,
    ),
    _B4ListingPolicy(
        "index:australia-200",
        "australia-200",
        "Australia 200",
        AssetClass.INDEX,
        None,
        "AUD",
        _B3_VALID_FROM,
        _B3_METADATA_VERSION,
    ),
    _B4ListingPolicy(
        "index:us-500",
        "us-500",
        "US 500",
        AssetClass.INDEX,
        None,
        "USD",
        _B4_VALID_FROM,
        _B4_METADATA_VERSION,
    ),
    _B4ListingPolicy(
        "commodity:spot-gold",
        "spot-gold",
        "Gold",
        AssetClass.COMMODITY,
        "XAU",
        "USD",
        _B4_VALID_FROM,
        _B4_METADATA_VERSION,
    ),
    _B4ListingPolicy(
        "commodity:us-crude",
        "us-crude",
        "US Crude",
        AssetClass.COMMODITY,
        None,
        "USD",
        _B4_VALID_FROM,
        _B4_METADATA_VERSION,
    ),
)
B4_INSTRUMENTS = frozenset(InstrumentId(item.instrument_id) for item in _B4_LISTING_POLICIES)


@dataclass(frozen=True, slots=True)
class IbkrB4ParentRelease:
    contract: str
    artifact_sha256: str
    configuration_hash: str
    qualification_artifact_sha256: str

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "contract": self.contract,
            "artifact_sha256": self.artifact_sha256,
            "configuration_hash": self.configuration_hash,
            "qualification_artifact_sha256": self.qualification_artifact_sha256,
        }


@dataclass(frozen=True, slots=True)
class IbkrB4Promotion:
    configuration: IbkrNativeCaptureConfiguration
    authority: IbkrPromotionAuthority
    parent_release: IbkrB4ParentRelease


def _product_type(contract: IbkrContractEvidence, instrument_id: InstrumentId) -> ProductType:
    mapping = {"CASH": ProductType.SPOT_FX, "CFD": ProductType.ROLLING_CFD}
    try:
        return mapping[contract.security_type]
    except KeyError as error:
        raise ValueError(
            f"B4 authenticated security type has no reviewed listing mapping for {instrument_id}: "
            f"{contract.security_type}"
        ) from error


def _expected_listing(policy: _B4ListingPolicy, contract: IbkrContractEvidence) -> ProviderListing:
    instrument_id = InstrumentId(policy.instrument_id)
    if contract.currency != policy.currency:
        raise ValueError(f"B4 authenticated currency does not match policy for {instrument_id}")
    if contract.minimum_tick is None:
        raise ValueError(f"B4 authenticated minimum tick is missing for {instrument_id}")
    return ProviderListing(
        listing_id=ProviderListingId("ibkr", "IBKR_PAPER", policy.external_id),
        instrument_id=instrument_id,
        display_name=policy.display_name,
        product_type=_product_type(contract, instrument_id),
        currency=policy.currency,
        minimum_deal_size=Decimal("1"),
        price_increment=contract.minimum_tick,
        valid_from=policy.valid_from,
        valid_to=None,
        metadata_version=policy.metadata_version,
        economics={},
    )


def _verify_provider_authority(
    source: IbkrNativeCaptureConfiguration,
    paths: IbkrAuthorityPaths,
) -> dict[InstrumentId, ProviderListing]:
    if (
        len(source.listings) != 6
        or {item.instrument_id for item in source.listings} != B4_INSTRUMENTS
    ):
        raise ValueError("B4 source must contain exactly the fixed six instruments")
    selection = verify_ibkr_contract_selection(
        paths.contract_selection_path,
        capability_review_path=paths.capability_review_path,
        operator_selection_path=paths.operator_selection_path,
        catalogue_path=paths.catalogue_path,
        probe_spec_path=paths.probe_spec_path,
    )
    review = load_ibkr_capability_review(
        paths.capability_review_path,
        catalogue_path=paths.catalogue_path,
        probe_spec_path=paths.probe_spec_path,
    )
    candidates = load_capture_candidates(paths.catalogue_path)
    if candidates.configuration_hash != selection.catalogue_hash:
        raise ValueError("B4 catalogue changed during authenticated replay")
    canonical = {item.instrument_id: item for item in candidates.instruments}
    decisions = {item.instrument_id: item for item in selection.decisions}
    if set(decisions) != B4_INSTRUMENTS:
        raise ValueError("B4 contract selection must contain exactly the fixed six instruments")

    trusted: dict[InstrumentId, ProviderListing] = {}
    con_ids: set[int] = set()
    for policy in _B4_LISTING_POLICIES:
        instrument_id = InstrumentId(policy.instrument_id)
        decision = decisions[instrument_id]
        if not decision.acquisition_eligible or decision.fingerprint is None:
            raise ValueError(f"B4 authority does not accept an exact contract for {instrument_id}")
        if decision.fingerprint.con_id in con_ids:
            raise ValueError("B4 authority contains duplicate conIds")
        con_ids.add(decision.fingerprint.con_id)
        candidate = canonical.get(instrument_id)
        if candidate is None or (
            candidate.display_name != policy.display_name
            or candidate.asset_class is not policy.asset_class
            or candidate.base_currency != policy.base_currency
            or candidate.quote_currency != policy.currency
        ):
            raise ValueError(f"B4 canonical instrument does not match policy for {instrument_id}")

        matches = [item for item in source.listings if item.instrument_id == instrument_id]
        if len(matches) != 1:
            raise ValueError(f"B4 source has missing or duplicate listing for {instrument_id}")
        listing = matches[0]
        if listing.listing_id != ProviderListingId("ibkr", "IBKR_PAPER", policy.external_id):
            raise ValueError(f"B4 listing identity is not reviewed for {instrument_id}")
        contract = source.contract_evidence.get(listing.listing_id)
        if contract is None or contract.con_id != decision.fingerprint.con_id:
            raise ValueError(f"B4 source conId does not match authority for {instrument_id}")
        authenticated = review_contract_evidence(review, instrument_id, contract.con_id, label="B4")
        if authenticated != contract:
            raise ValueError(
                "B4 source provider evidence does not match authenticated review for "
                f"{instrument_id}"
            )
        expected = _expected_listing(policy, authenticated)
        mismatches = listing_mismatches(listing, expected)
        if mismatches:
            raise ValueError(
                f"B4 source listing does not match trusted policy for {instrument_id}: "
                + ", ".join(mismatches)
            )
        trusted[instrument_id] = expected
    return trusted


def _authenticate_provider_authority(
    source: IbkrNativeCaptureConfiguration,
    paths: IbkrAuthorityPaths,
) -> tuple[dict[InstrumentId, ProviderListing], IbkrPromotionAuthority]:
    before = authority_identity(paths, label="B4")
    trusted = _verify_provider_authority(source, paths)
    after = authority_identity(paths, label="B4")
    if after != before:
        raise ValueError("B4 promotion authority changed during authenticated replay")
    return trusted, before


def _require_b3_contract_inheritance(
    candidate: IbkrNativeCaptureConfiguration,
    parent: IbkrNativeCaptureConfiguration,
) -> None:
    """Require B4 to retain the exact listings and conIds qualified in B3."""

    candidate_by_instrument = {item.instrument_id: item for item in candidate.listings}
    parent_by_instrument = {item.instrument_id: item for item in parent.listings}
    for raw_instrument_id, _external_id, _con_id in B3_TARGETS:
        instrument_id = InstrumentId(raw_instrument_id)
        candidate_listing = candidate_by_instrument.get(instrument_id)
        parent_listing = parent_by_instrument.get(instrument_id)
        if candidate_listing is None or parent_listing is None:
            raise ValueError(f"B4 does not preserve B3-qualified listing for {instrument_id}")
        mismatches = listing_mismatches(candidate_listing, parent_listing)
        if mismatches:
            raise ValueError(
                f"B4 does not preserve B3-qualified listing for {instrument_id}: "
                + ", ".join(mismatches)
            )
        candidate_con_id = candidate.contract_evidence[candidate_listing.listing_id].con_id
        parent_con_id = parent.contract_evidence[parent_listing.listing_id].con_id
        if candidate_con_id != parent_con_id:
            raise ValueError(f"B4 does not preserve B3-qualified conId for {instrument_id}")


def promote_b4_configuration(
    source: IbkrNativeCaptureConfiguration,
    *,
    authority_paths: IbkrAuthorityPaths,
    parent_release_path: Path,
    parent_authority_paths: IbkrAuthorityPaths,
    qualification: VerifiedB3Qualification,
) -> IbkrB4Promotion:
    if not has_verified_ibkr_capture_qualification_provenance(qualification):
        raise ValueError("B4 transition requires verifier-minted B3 qualification authority")
    parent = load_authenticated_b3_configuration(
        parent_release_path, **parent_authority_paths.as_kwargs()
    )
    parent_sha = sha256_path(parent_release_path, label="B3 parent")
    b3_ids = frozenset(InstrumentId(item[0]) for item in B3_TARGETS)
    if (
        qualification.stage is not IbkrQualificationStage.B3_EXACT_TWO
        or qualification.release_contract != B3_RELEASE_CONTRACT
        or qualification.release_sha256 != parent_sha
        or qualification.configuration_hash != parent.configuration_hash
        or qualification.capture_source_id != B3_CAPTURE_SOURCE_ID
        or qualification.universe_id != B3_UNIVERSE_ID
        or qualification.instruments != b3_ids
        or {(item.instrument_id, item.listing_id, item.con_id) for item in qualification.contracts}
        != {
            (
                listing.instrument_id,
                listing.listing_id,
                parent.contract_evidence[listing.listing_id].con_id,
            )
            for listing in parent.listings
        }
    ):
        raise ValueError("B4 promotion requires a matching independently verified B3 qualification")

    trusted, authority = _authenticate_provider_authority(source, authority_paths)
    listings: list[ProviderListing] = []
    evidence: dict[ProviderListingId, IbkrContractEvidence] = {}
    for policy in _B4_LISTING_POLICIES:
        instrument_id = InstrumentId(policy.instrument_id)
        listing = trusted[instrument_id]
        source_listing = next(
            item for item in source.listings if item.instrument_id == instrument_id
        )
        listings.append(listing)
        evidence[listing.listing_id] = source.contract_evidence[source_listing.listing_id]
    configuration = IbkrNativeCaptureConfiguration.from_reviewed(
        sorted(listings, key=lambda item: str(item.listing_id)), evidence
    )
    if len(configuration.listings) != 6:
        raise ValueError("B4 release must contain exactly six listings")
    _require_b3_contract_inheritance(configuration, parent)
    return IbkrB4Promotion(
        configuration=configuration,
        authority=authority,
        parent_release=IbkrB4ParentRelease(
            contract=B3_RELEASE_CONTRACT,
            artifact_sha256=parent_sha,
            configuration_hash=parent.configuration_hash,
            qualification_artifact_sha256=qualification.artifact_sha256,
        ),
    )


def write_b4_release(path: Path, promotion: IbkrB4Promotion) -> None:
    write_release(
        path,
        configuration_payload(
            promotion.configuration,
            promotion.authority,
            contract=B4_RELEASE_CONTRACT,
            release_stage=B4_RELEASE_STAGE,
            parent_release=promotion.parent_release.as_json_value(),
        ),
    )


def load_authenticated_b4_configuration(
    path: Path,
    *,
    authority_paths: IbkrAuthorityPaths,
    parent_release_path: Path,
    parent_authority_paths: IbkrAuthorityPaths,
    qualification: VerifiedB3Qualification,
) -> IbkrNativeCaptureConfiguration:
    if not has_verified_ibkr_capture_qualification_provenance(qualification):
        raise ValueError("B4 transition requires verifier-minted B3 qualification authority")
    parent = load_authenticated_b3_configuration(
        parent_release_path, **parent_authority_paths.as_kwargs()
    )
    parent_identity = IbkrB4ParentRelease(
        contract=B3_RELEASE_CONTRACT,
        artifact_sha256=sha256_path(parent_release_path, label="B3 parent"),
        configuration_hash=parent.configuration_hash,
        qualification_artifact_sha256=qualification.artifact_sha256,
    )
    document = load_release_document(path, label="B4")
    embedded_authority = IbkrPromotionAuthority.from_json_value(document.get("promotion_authority"))
    before = authority_identity(authority_paths, label="B4")
    if before != embedded_authority:
        raise ValueError("B4 promotion authority identity mismatch")
    configuration = load_canonical_release_configuration(
        path,
        contract=B4_RELEASE_CONTRACT,
        authority=before,
        label="B4",
        release_stage=B4_RELEASE_STAGE,
        parent_release=parent_identity.as_json_value(),
    )
    trusted, after = _authenticate_provider_authority(configuration, authority_paths)
    if after != before or len(trusted) != 6:
        raise ValueError("B4 promotion authority changed during verification")
    if (
        qualification.release_sha256 != parent_identity.artifact_sha256
        or qualification.configuration_hash != parent.configuration_hash
        or qualification.instruments != frozenset(InstrumentId(item[0]) for item in B3_TARGETS)
        or {(item.instrument_id, item.listing_id, item.con_id) for item in qualification.contracts}
        != {
            (
                listing.instrument_id,
                listing.listing_id,
                parent.contract_evidence[listing.listing_id].con_id,
            )
            for listing in parent.listings
        }
    ):
        raise ValueError("B4 parent qualification does not match authenticated B3 release")
    _require_b3_contract_inheritance(configuration, parent)
    return configuration


def verify_b4_configuration(
    configuration: IbkrNativeCaptureConfiguration, *, observed_at: datetime
) -> dict[str, JsonValue]:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("B4 verification timestamp must be timezone-aware")
    observed_at = observed_at.astimezone(UTC)
    errors: list[str] = []
    if (
        len(configuration.listings) != 6
        or {item.instrument_id for item in configuration.listings} != B4_INSTRUMENTS
    ):
        errors.append("configuration does not contain exactly the B4 fixed-six set")
    instruments: list[JsonValue] = []
    requires_refresh = False
    for listing in sorted(configuration.listings, key=lambda item: str(item.instrument_id)):
        evidence = configuration.contract_evidence.get(listing.listing_id)
        policy = next(
            (
                item
                for item in _B4_LISTING_POLICIES
                if item.instrument_id == str(listing.instrument_id)
            ),
            None,
        )
        if evidence is None or policy is None:
            errors.append(f"missing exact policy/evidence: {listing.instrument_id}")
            continue
        try:
            expected = _expected_listing(policy, evidence)
            mismatches = listing_mismatches(listing, expected)
            if mismatches:
                errors.append(
                    f"listing policy mismatch: {listing.instrument_id}: " + ", ".join(mismatches)
                )
        except ValueError as error:
            errors.append(str(error))
        activity = configuration.activity(listing.listing_id, observed_at)
        requires_refresh |= activity is IbkrMarketActivity.UNKNOWN
        instruments.append(
            {
                "instrument_id": str(listing.instrument_id),
                "listing_id": str(listing.listing_id),
                "con_id": evidence.con_id,
                "activity": activity.value,
            }
        )
    return cast(
        dict[str, JsonValue],
        {
            "contract": B4_RELEASE_CONTRACT,
            "release_stage": B4_RELEASE_STAGE,
            "valid": not errors,
            "operational_ready": not errors and not requires_refresh,
            "requires_evidence_refresh": requires_refresh,
            "source": B4_CAPTURE_SOURCE_ID,
            "universe": B4_UNIVERSE_ID,
            "configuration_hash": configuration.configuration_hash,
            "observed_at": observed_at.isoformat(),
            "instrument_count": len(configuration.listings),
            "instruments": instruments,
            "errors": errors,
        },
    )


@dataclass(frozen=True, slots=True)
class IbkrB4DeploymentDescriptor:
    """B4 lineage plus the unchanged B3 deployment topology."""

    deployment: IbkrB3DeploymentDescriptor
    parent_release_path: Path
    parent_authority_paths: IbkrAuthorityPaths
    qualification_path: Path

    def __post_init__(self) -> None:
        paths = (
            self.parent_release_path,
            self.parent_authority_paths.capability_review_path,
            self.parent_authority_paths.operator_selection_path,
            self.parent_authority_paths.contract_selection_path,
            self.parent_authority_paths.catalogue_path,
            self.parent_authority_paths.probe_spec_path,
            self.qualification_path,
        )
        if any(not path.is_absolute() for path in paths):
            raise ValueError(
                "B4 parent release, authority, and qualification paths must be absolute"
            )

    @classmethod
    def from_toml(cls, path: Path) -> IbkrB4DeploymentDescriptor:
        deployment = load_b3_deployment_descriptor(path)
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"unable to read B4 deployment descriptor: {path}") from error
        parent = _required_table(document, "parent")
        qualification = _required_table(document, "qualification")
        return cls(
            deployment=deployment,
            parent_release_path=Path(_required_string(parent, "release_path")),
            parent_authority_paths=IbkrAuthorityPaths(
                capability_review_path=Path(_required_string(parent, "capability_review_path")),
                operator_selection_path=Path(_required_string(parent, "operator_selection_path")),
                contract_selection_path=Path(_required_string(parent, "contract_selection_path")),
                catalogue_path=Path(_required_string(parent, "catalogue_path")),
                probe_spec_path=Path(_required_string(parent, "probe_spec_path")),
            ),
            qualification_path=Path(_required_string(qualification, "artifact_path")),
        )


def verify_b3_qualification_for_release(
    qualification_path: Path,
    *,
    parent_release_path: Path,
    parent_authority_paths: IbkrAuthorityPaths,
    deployment: IbkrB3DeploymentDescriptor,
) -> VerifiedB3Qualification:
    """Authenticate one real B3 qualification; no operator-created capability is accepted."""

    parent = load_authenticated_b3_configuration(
        parent_release_path, **parent_authority_paths.as_kwargs()
    )
    return verify_ibkr_capture_qualification(
        qualification_path,
        IbkrQualificationExpectation(
            stage=IbkrQualificationStage.B3_EXACT_TWO,
            release_contract=B3_RELEASE_CONTRACT,
            release_sha256=sha256_path(parent_release_path, label="B3"),
            configuration_hash=parent.configuration_hash,
            capture_source_id=parent.capture_source_id,
            universe_id=parent.universe_id,
            instruments=frozenset(item.instrument_id for item in parent.listings),
            contracts=tuple(
                IbkrQualifiedContract(
                    instrument_id=listing.instrument_id,
                    listing_id=listing.listing_id,
                    con_id=parent.contract_evidence[listing.listing_id].con_id,
                )
                for listing in parent.listings
            ),
            application_commit=deployment.application_commit,
            image_digest=deployment.image,
            api_package_sha256=deployment.api_package_fingerprint,
            gateway_archive_sha256=deployment.gateway_archive_sha256,
            gateway_version=deployment.gateway_version,
            ibc_version=deployment.ibc_version,
            database_name=deployment.database_name,
            schema_head=deployment.schema_head,
            freshness_threshold=timedelta(seconds=60),
        ),
    )


def verify_b4_release(
    path: Path,
    *,
    authority_paths: IbkrAuthorityPaths,
    parent_release_path: Path,
    parent_authority_paths: IbkrAuthorityPaths,
    qualification: VerifiedB3Qualification,
    observed_at: datetime,
) -> dict[str, JsonValue]:
    try:
        configuration = load_authenticated_b4_configuration(
            path,
            authority_paths=authority_paths,
            parent_release_path=parent_release_path,
            parent_authority_paths=parent_authority_paths,
            qualification=qualification,
        )
    except ValueError as error:
        return {
            "contract": B4_RELEASE_CONTRACT,
            "release_stage": B4_RELEASE_STAGE,
            "valid": False,
            "operational_ready": False,
            "requires_evidence_refresh": False,
            "errors": [str(error)],
        }
    report = verify_b4_configuration(configuration, observed_at=observed_at)
    report["promotion_authority"] = authority_identity(authority_paths, label="B4").as_json_value()
    report["parent_release_sha256"] = qualification.release_sha256
    report["qualification_artifact_sha256"] = qualification.artifact_sha256
    return report


def b4_preflight(
    descriptor_path: Path,
    *,
    repository_root: Path,
    observed_at: datetime,
) -> dict[str, JsonValue]:
    """Verify B4 release, parent qualification, and unchanged topology offline."""

    try:
        descriptor = IbkrB4DeploymentDescriptor.from_toml(descriptor_path)
        deployment = descriptor.deployment
        qualification = verify_b3_qualification_for_release(
            descriptor.qualification_path,
            parent_release_path=descriptor.parent_release_path,
            parent_authority_paths=descriptor.parent_authority_paths,
            deployment=deployment,
        )
        report = verify_b4_release(
            Path(deployment.configuration_path),
            authority_paths=IbkrAuthorityPaths(
                capability_review_path=Path(deployment.capability_review_path),
                operator_selection_path=Path(deployment.operator_selection_path),
                contract_selection_path=Path(deployment.contract_selection_path),
                catalogue_path=Path(deployment.catalogue_path),
                probe_spec_path=Path(deployment.probe_spec_path),
            ),
            parent_release_path=descriptor.parent_release_path,
            parent_authority_paths=descriptor.parent_authority_paths,
            qualification=qualification,
            observed_at=observed_at,
        )
    except ValueError as error:
        return {
            "contract": B4_RELEASE_CONTRACT,
            "release_stage": B4_RELEASE_STAGE,
            "valid": False,
            "operational_ready": False,
            "requires_evidence_refresh": False,
            "errors": [str(error)],
        }

    errors = [error for error in cast(list[JsonValue], report["errors"]) if isinstance(error, str)]
    if report.get("configuration_hash") != deployment.configuration_hash:
        errors.append("deployment descriptor/configuration hash mismatch")
    for unit in {
        deployment.ingest_service,
        deployment.api_service,
        deployment.health_timer,
        deployment.backup_timer,
    }:
        if not (repository_root / "ops" / "ibkr" / f"{unit}.example").is_file():
            errors.append(f"required B4 unit template is missing: {unit}")

    return cast(
        dict[str, JsonValue],
        {
            "contract": B4_RELEASE_CONTRACT,
            "release_stage": B4_RELEASE_STAGE,
            "valid": not errors,
            "operational_ready": not errors and not bool(report.get("requires_evidence_refresh")),
            "requires_evidence_refresh": bool(report.get("requires_evidence_refresh")),
            "application_commit": deployment.application_commit,
            "image": deployment.image,
            "source": B4_CAPTURE_SOURCE_ID,
            "universe": B4_UNIVERSE_ID,
            "configuration_hash": deployment.configuration_hash,
            "configuration_path": deployment.configuration_path,
            "api_package_fingerprint": deployment.api_package_fingerprint,
            "gateway_archive_sha256": deployment.gateway_archive_sha256,
            "ibc_version": deployment.ibc_version,
            "database_url_environment": deployment.database_url_environment,
            "api_version": deployment.api_version,
            "gateway_version": deployment.gateway_version,
            "client_id": deployment.client_id,
            "gateway_host": deployment.gateway_host,
            "gateway_port": deployment.gateway_port,
            "api_host": deployment.api_host,
            "api_port": deployment.api_port,
            "database_name": deployment.database_name,
            "schema_head": deployment.schema_head,
            "configuration": report,
            "errors": errors,
        },
    )


def _required_table(document: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = document.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"B4 deployment descriptor requires [{field}]")
    return cast(Mapping[str, object], value)


def _required_string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"B4 deployment descriptor requires {field}")
    return value
