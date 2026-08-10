"""B5 full-universe IBKR release gated by verified B4 qualification."""

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
from qtrad.runtime.ibkr_b3 import IbkrB3DeploymentDescriptor, load_b3_deployment_descriptor
from qtrad.runtime.ibkr_b4 import (
    B4_CAPTURE_SOURCE_ID,
    B4_INSTRUMENTS,
    B4_RELEASE_CONTRACT,
    B4_UNIVERSE_ID,
    IbkrB4DeploymentDescriptor,
    load_authenticated_b4_configuration,
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
from qtrad.runtime.ibkr_qualification_evidence import (
    QualificationEvidenceStore,
    VerifiedIbkrRestoreEvidence,
    verify_ibkr_qualification_evidence,
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

B5_RELEASE_CONTRACT = "qtrad-ibkr-native-release-v3"
B5_RELEASE_STAGE = "B5_FULL_UNIVERSE"
B5_CAPTURE_SOURCE_ID = B4_CAPTURE_SOURCE_ID
B5_UNIVERSE_ID = B4_UNIVERSE_ID
B5_INSTRUMENT_COUNT = 20
_B5_METADATA_VERSION = "ibkr-b5-review-v1"


@dataclass(frozen=True, slots=True)
class IbkrB5ParentRelease:
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
class IbkrB5Promotion:
    configuration: IbkrNativeCaptureConfiguration
    authority: IbkrPromotionAuthority
    parent_release: IbkrB5ParentRelease


def _expected_listing(
    instrument_id: InstrumentId,
    *,
    display_name: str,
    currency: str,
    contract: IbkrContractEvidence,
    valid_from: datetime,
) -> ProviderListing:
    product_types = {"CASH": ProductType.SPOT_FX, "CFD": ProductType.ROLLING_CFD}
    try:
        product_type = product_types[contract.security_type]
    except KeyError as error:
        raise ValueError(
            f"B5 authenticated security type has no reviewed listing mapping for {instrument_id}: "
            f"{contract.security_type}"
        ) from error
    if contract.currency != currency:
        raise ValueError(f"B5 authenticated currency does not match catalogue for {instrument_id}")
    if contract.minimum_tick is None:
        raise ValueError(f"B5 authenticated minimum tick is missing for {instrument_id}")
    return ProviderListing(
        listing_id=ProviderListingId("ibkr", "IBKR_PAPER", str(instrument_id).split(":", 1)[1]),
        instrument_id=instrument_id,
        display_name=display_name,
        product_type=product_type,
        currency=currency,
        minimum_deal_size=Decimal("1"),
        price_increment=contract.minimum_tick,
        valid_from=valid_from,
        valid_to=None,
        metadata_version=_B5_METADATA_VERSION,
        economics={},
    )


def _authenticate_provider_authority(
    paths: IbkrAuthorityPaths,
    *,
    parent: IbkrNativeCaptureConfiguration,
    configuration: IbkrNativeCaptureConfiguration | None = None,
) -> tuple[IbkrNativeCaptureConfiguration, IbkrPromotionAuthority]:
    before = authority_identity(paths, label="B5")
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
    canonical = {item.instrument_id: item for item in candidates.instruments}
    decisions = {item.instrument_id: item for item in selection.decisions}
    if (
        candidates.configuration_hash != selection.catalogue_hash
        or len(canonical) != B5_INSTRUMENT_COUNT
        or set(decisions) != set(canonical)
    ):
        raise ValueError(
            "B5 authority must contain exactly the reviewed twenty-instrument universe"
        )

    parent_listings = {item.instrument_id: item for item in parent.listings}
    if set(parent_listings) != B4_INSTRUMENTS:
        raise ValueError("B5 parent must contain the exact B4 six-instrument universe")
    listings: list[ProviderListing] = []
    evidence: dict[ProviderListingId, IbkrContractEvidence] = {}
    con_ids: set[int] = set()
    for instrument_id in sorted(canonical, key=str):
        decision = decisions[instrument_id]
        if not decision.acquisition_eligible or decision.fingerprint is None:
            raise ValueError(f"B5 authority does not accept an exact contract for {instrument_id}")
        if decision.fingerprint.con_id in con_ids:
            raise ValueError("B5 authority contains duplicate conIds")
        con_ids.add(decision.fingerprint.con_id)
        contract = review_contract_evidence(
            review, instrument_id, decision.fingerprint.con_id, label="B5"
        )
        candidate = canonical[instrument_id]
        expected_security_type = "CASH" if candidate.asset_class is AssetClass.FX else "CFD"
        if contract.security_type != expected_security_type:
            raise ValueError(
                "B5 authenticated security type does not match catalogue asset class for "
                f"{instrument_id}"
            )
        listing = parent_listings.get(instrument_id) or _expected_listing(
            instrument_id,
            display_name=candidate.display_name,
            currency=candidate.quote_currency,
            contract=contract,
            valid_from=selection.frozen_at,
        )
        listings.append(listing)
        evidence[listing.listing_id] = contract

    trusted = IbkrNativeCaptureConfiguration.from_reviewed(
        sorted(listings, key=lambda item: str(item.listing_id)), evidence
    )
    if configuration is not None:
        supplied = {item.instrument_id: item for item in configuration.listings}
        if len(supplied) != B5_INSTRUMENT_COUNT or set(supplied) != set(canonical):
            raise ValueError(
                "B5 release must contain exactly the reviewed twenty-instrument universe"
            )
        for listing in trusted.listings:
            actual = supplied[listing.instrument_id]
            if (
                listing_mismatches(actual, listing)
                or configuration.contract_evidence.get(actual.listing_id)
                != evidence[listing.listing_id]
            ):
                raise ValueError(
                    f"B5 release does not match authenticated authority for {listing.instrument_id}"
                )
    after = authority_identity(paths, label="B5")
    if after != before:
        raise ValueError("B5 promotion authority changed during authenticated replay")
    return trusted, before


def _require_parent_qualification(
    qualification: VerifiedB3Qualification,
    *,
    parent: IbkrNativeCaptureConfiguration,
    parent_release_path: Path,
) -> None:
    if not has_verified_ibkr_capture_qualification_provenance(qualification):
        raise ValueError("B5 transition requires verifier-minted B4 qualification authority")
    expected_contracts = {
        (
            listing.instrument_id,
            listing.listing_id,
            parent.contract_evidence[listing.listing_id].con_id,
        )
        for listing in parent.listings
    }
    if (
        qualification.stage is not IbkrQualificationStage.B4_EXACT_SIX
        or qualification.release_contract != B4_RELEASE_CONTRACT
        or qualification.release_sha256 != sha256_path(parent_release_path, label="B4 parent")
        or qualification.configuration_hash != parent.configuration_hash
        or qualification.capture_source_id != B4_CAPTURE_SOURCE_ID
        or qualification.universe_id != B4_UNIVERSE_ID
        or qualification.instruments != B4_INSTRUMENTS
        or {(item.instrument_id, item.listing_id, item.con_id) for item in qualification.contracts}
        != expected_contracts
    ):
        raise ValueError(
            "B5 transition requires a matching independently verified B4 qualification"
        )


def _require_b4_inheritance(
    candidate: IbkrNativeCaptureConfiguration, parent: IbkrNativeCaptureConfiguration
) -> None:
    candidate_by_id = {item.instrument_id: item for item in candidate.listings}
    for parent_listing in parent.listings:
        listing = candidate_by_id.get(parent_listing.instrument_id)
        if listing is None or listing_mismatches(listing, parent_listing):
            raise ValueError(
                f"B5 does not preserve B4-qualified listing for {parent_listing.instrument_id}"
            )
        if (
            candidate.contract_evidence[listing.listing_id].con_id
            != parent.contract_evidence[parent_listing.listing_id].con_id
        ):
            raise ValueError(
                f"B5 does not preserve B4-qualified conId for {parent_listing.instrument_id}"
            )


def promote_b5_configuration(
    *,
    authority_paths: IbkrAuthorityPaths,
    parent_release_path: Path,
    parent_authority_paths: IbkrAuthorityPaths,
    parent_descriptor: IbkrB4DeploymentDescriptor,
    b3_qualification: VerifiedB3Qualification,
    b4_qualification: VerifiedB3Qualification,
) -> IbkrB5Promotion:
    parent = load_authenticated_b4_configuration(
        parent_release_path,
        authority_paths=parent_authority_paths,
        parent_release_path=parent_descriptor.parent_release_path,
        parent_authority_paths=parent_descriptor.parent_authority_paths,
        qualification=b3_qualification,
    )
    _require_parent_qualification(
        b4_qualification, parent=parent, parent_release_path=parent_release_path
    )
    configuration, authority = _authenticate_provider_authority(authority_paths, parent=parent)
    _require_b4_inheritance(configuration, parent)
    return IbkrB5Promotion(
        configuration=configuration,
        authority=authority,
        parent_release=IbkrB5ParentRelease(
            contract=B4_RELEASE_CONTRACT,
            artifact_sha256=sha256_path(parent_release_path, label="B4 parent"),
            configuration_hash=parent.configuration_hash,
            qualification_artifact_sha256=b4_qualification.artifact_sha256,
        ),
    )


def write_b5_release(path: Path, promotion: IbkrB5Promotion) -> None:
    write_release(
        path,
        configuration_payload(
            promotion.configuration,
            promotion.authority,
            contract=B5_RELEASE_CONTRACT,
            release_stage=B5_RELEASE_STAGE,
            parent_release=promotion.parent_release.as_json_value(),
        ),
    )


def load_authenticated_b5_configuration(
    path: Path,
    *,
    authority_paths: IbkrAuthorityPaths,
    parent_release_path: Path,
    parent_authority_paths: IbkrAuthorityPaths,
    parent_descriptor: IbkrB4DeploymentDescriptor,
    b3_qualification: VerifiedB3Qualification,
    b4_qualification: VerifiedB3Qualification,
) -> IbkrNativeCaptureConfiguration:
    parent = load_authenticated_b4_configuration(
        parent_release_path,
        authority_paths=parent_authority_paths,
        parent_release_path=parent_descriptor.parent_release_path,
        parent_authority_paths=parent_descriptor.parent_authority_paths,
        qualification=b3_qualification,
    )
    _require_parent_qualification(
        b4_qualification, parent=parent, parent_release_path=parent_release_path
    )
    parent_identity = IbkrB5ParentRelease(
        contract=B4_RELEASE_CONTRACT,
        artifact_sha256=sha256_path(parent_release_path, label="B4 parent"),
        configuration_hash=parent.configuration_hash,
        qualification_artifact_sha256=b4_qualification.artifact_sha256,
    )
    document = load_release_document(path, label="B5")
    embedded = IbkrPromotionAuthority.from_json_value(document.get("promotion_authority"))
    before = authority_identity(authority_paths, label="B5")
    if embedded != before:
        raise ValueError("B5 promotion authority identity mismatch")
    configuration = load_canonical_release_configuration(
        path,
        contract=B5_RELEASE_CONTRACT,
        authority=before,
        label="B5",
        release_stage=B5_RELEASE_STAGE,
        parent_release=parent_identity.as_json_value(),
    )
    _, after = _authenticate_provider_authority(
        paths=authority_paths, parent=parent, configuration=configuration
    )
    if after != before:
        raise ValueError("B5 promotion authority changed during verification")
    _require_b4_inheritance(configuration, parent)
    return configuration


@dataclass(frozen=True, slots=True)
class IbkrB5DeploymentDescriptor:
    deployment: IbkrB3DeploymentDescriptor
    parent_release_path: Path
    parent_authority_paths: IbkrAuthorityPaths
    parent_descriptor_path: Path
    parent_descriptor: IbkrB4DeploymentDescriptor
    qualification_path: Path

    def __post_init__(self) -> None:
        paths = (
            self.parent_release_path,
            self.parent_descriptor_path,
            self.qualification_path,
            *self.parent_authority_paths.as_kwargs().values(),
        )
        if any(not path.is_absolute() for path in paths):
            raise ValueError(
                "B5 parent release, descriptor, authority, and qualification paths must be absolute"
            )

    @classmethod
    def from_toml(cls, path: Path) -> IbkrB5DeploymentDescriptor:
        deployment = load_b3_deployment_descriptor(path)
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"unable to read B5 deployment descriptor: {path}") from error
        parent = _required_table(document, "parent")
        qualification = _required_table(document, "qualification")
        parent_descriptor_path = Path(_required_string(parent, "descriptor_path"))
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
            parent_descriptor_path=parent_descriptor_path,
            parent_descriptor=IbkrB4DeploymentDescriptor.from_toml(parent_descriptor_path),
            qualification_path=Path(_required_string(qualification, "artifact_path")),
        )


def b5_qualification_expectation(
    *,
    release_path: Path,
    authority_paths: IbkrAuthorityPaths,
    descriptor: IbkrB5DeploymentDescriptor,
    b3_qualification: VerifiedB3Qualification,
    b4_qualification: VerifiedB3Qualification,
) -> tuple[IbkrNativeCaptureConfiguration, IbkrQualificationExpectation]:
    configuration = load_authenticated_b5_configuration(
        release_path,
        authority_paths=authority_paths,
        parent_release_path=descriptor.parent_release_path,
        parent_authority_paths=descriptor.parent_authority_paths,
        parent_descriptor=descriptor.parent_descriptor,
        b3_qualification=b3_qualification,
        b4_qualification=b4_qualification,
    )
    deployment = descriptor.deployment
    return configuration, IbkrQualificationExpectation(
        stage=IbkrQualificationStage.B5_FULL_UNIVERSE,
        release_contract=B5_RELEASE_CONTRACT,
        release_sha256=sha256_path(release_path, label="B5"),
        configuration_hash=configuration.configuration_hash,
        capture_source_id=configuration.capture_source_id,
        universe_id=configuration.universe_id,
        instruments=frozenset(item.instrument_id for item in configuration.listings),
        contracts=tuple(
            IbkrQualifiedContract(
                instrument_id=listing.instrument_id,
                listing_id=listing.listing_id,
                con_id=configuration.contract_evidence[listing.listing_id].con_id,
            )
            for listing in configuration.listings
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
    )


async def verify_b5_qualification_evidence_for_release(
    qualification_path: Path,
    *,
    release_path: Path,
    authority_paths: IbkrAuthorityPaths,
    descriptor: IbkrB5DeploymentDescriptor,
    b3_qualification: VerifiedB3Qualification,
    b4_qualification: VerifiedB3Qualification,
    live_store: QualificationEvidenceStore,
    restored_store: QualificationEvidenceStore,
    restore_evidence: VerifiedIbkrRestoreEvidence,
) -> VerifiedB3Qualification:
    configuration, expectation = b5_qualification_expectation(
        release_path=release_path,
        authority_paths=authority_paths,
        descriptor=descriptor,
        b3_qualification=b3_qualification,
        b4_qualification=b4_qualification,
    )
    return await verify_ibkr_qualification_evidence(
        qualification_path,
        live_store,
        restored_store,
        restore_evidence=restore_evidence,
        expectation=expectation,
        configuration=configuration,
    )


def verify_b5_qualification_for_release(
    qualification_path: Path,
    *,
    release_path: Path,
    authority_paths: IbkrAuthorityPaths,
    descriptor: IbkrB5DeploymentDescriptor,
    b3_qualification: VerifiedB3Qualification,
    b4_qualification: VerifiedB3Qualification,
) -> VerifiedB3Qualification:
    _, expectation = b5_qualification_expectation(
        release_path=release_path,
        authority_paths=authority_paths,
        descriptor=descriptor,
        b3_qualification=b3_qualification,
        b4_qualification=b4_qualification,
    )
    return verify_ibkr_capture_qualification(qualification_path, expectation)


def verify_b5_release(
    path: Path,
    *,
    authority_paths: IbkrAuthorityPaths,
    parent_release_path: Path,
    parent_authority_paths: IbkrAuthorityPaths,
    parent_descriptor: IbkrB4DeploymentDescriptor,
    b3_qualification: VerifiedB3Qualification,
    b4_qualification: VerifiedB3Qualification,
    observed_at: datetime,
) -> dict[str, JsonValue]:
    configuration = load_authenticated_b5_configuration(
        path,
        authority_paths=authority_paths,
        parent_release_path=parent_release_path,
        parent_authority_paths=parent_authority_paths,
        parent_descriptor=parent_descriptor,
        b3_qualification=b3_qualification,
        b4_qualification=b4_qualification,
    )
    report = verify_b5_configuration(configuration, observed_at=observed_at)
    report["promotion_authority"] = authority_identity(authority_paths, label="B5").as_json_value()
    report["parent_qualification_artifact_sha256"] = b4_qualification.artifact_sha256
    return report


def verify_b5_configuration(
    configuration: IbkrNativeCaptureConfiguration, *, observed_at: datetime
) -> dict[str, JsonValue]:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("B5 verification timestamp must be timezone-aware")
    observed_at = observed_at.astimezone(UTC)
    errors: list[str] = []
    requires_refresh = False
    instruments: list[JsonValue] = []
    if len(configuration.listings) != B5_INSTRUMENT_COUNT:
        errors.append("configuration does not contain the reviewed twenty-instrument universe")
    for listing in sorted(configuration.listings, key=lambda item: str(item.instrument_id)):
        evidence = configuration.contract_evidence.get(listing.listing_id)
        if evidence is None:
            errors.append(f"missing exact evidence: {listing.instrument_id}")
            continue
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
            "contract": B5_RELEASE_CONTRACT,
            "release_stage": B5_RELEASE_STAGE,
            "valid": not errors,
            "operational_ready": not errors and not requires_refresh,
            "requires_evidence_refresh": requires_refresh,
            "source": B5_CAPTURE_SOURCE_ID,
            "universe": B5_UNIVERSE_ID,
            "configuration_hash": configuration.configuration_hash,
            "observed_at": observed_at.isoformat(),
            "instrument_count": len(configuration.listings),
            "instruments": instruments,
            "errors": errors,
        },
    )


def b5_preflight(
    descriptor_path: Path,
    *,
    repository_root: Path,
    observed_at: datetime,
    b3_qualification: VerifiedB3Qualification,
    b4_qualification: VerifiedB3Qualification,
) -> dict[str, JsonValue]:
    try:
        descriptor = IbkrB5DeploymentDescriptor.from_toml(descriptor_path)
        deployment = descriptor.deployment
        authority_paths = IbkrAuthorityPaths(
            capability_review_path=Path(deployment.capability_review_path),
            operator_selection_path=Path(deployment.operator_selection_path),
            contract_selection_path=Path(deployment.contract_selection_path),
            catalogue_path=Path(deployment.catalogue_path),
            probe_spec_path=Path(deployment.probe_spec_path),
        )
        configuration = load_authenticated_b5_configuration(
            Path(deployment.configuration_path),
            authority_paths=authority_paths,
            parent_release_path=descriptor.parent_release_path,
            parent_authority_paths=descriptor.parent_authority_paths,
            parent_descriptor=descriptor.parent_descriptor,
            b3_qualification=b3_qualification,
            b4_qualification=b4_qualification,
        )
        report = verify_b5_configuration(configuration, observed_at=observed_at)
    except ValueError as error:
        return {
            "contract": B5_RELEASE_CONTRACT,
            "release_stage": B5_RELEASE_STAGE,
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
            errors.append(f"required B5 unit template is missing: {unit}")
    return cast(
        dict[str, JsonValue],
        {
            "contract": B5_RELEASE_CONTRACT,
            "release_stage": B5_RELEASE_STAGE,
            "valid": not errors,
            "operational_ready": not errors and not bool(report.get("requires_evidence_refresh")),
            "requires_evidence_refresh": bool(report.get("requires_evidence_refresh")),
            "application_commit": deployment.application_commit,
            "image": deployment.image,
            "source": B5_CAPTURE_SOURCE_ID,
            "universe": B5_UNIVERSE_ID,
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
        raise ValueError(f"B5 deployment descriptor requires [{field}]")
    return cast(Mapping[str, object], value)


def _required_string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"B5 deployment descriptor requires {field}")
    return value
