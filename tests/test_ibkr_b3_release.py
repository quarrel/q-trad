"""Offline B3 exact-two release, preflight, and wiring tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from qtrad.application.ibkr_historical import build_ibkr_contract_selection
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import IbkrContractDecision
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.ibkr_b3 import (
    B3_API_HOST,
    B3_API_PORT,
    B3_DATABASE_NAME,
    B3_RELEASE_CONTRACT,
    IbkrB3DeploymentDescriptor,
    b3_preflight,
    promote_b3_configuration,
    verify_b3_configuration,
    verify_b3_release,
    write_reviewed_configuration,
)
from qtrad.runtime.ibkr_capability import load_ibkr_capability_probe_spec
from qtrad.runtime.ibkr_historical import write_ibkr_contract_selection
from qtrad.runtime.ibkr_native_capture import (
    IbkrNativeCaptureConfiguration,
    load_reviewed_configuration,
)
from qtrad.runtime.universe import load_capture_candidates

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
_REPOSITORY_ROOT = Path(__file__).parents[1]


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _listing(instrument_id: str, external_id: str) -> ProviderListing:
    is_fx = instrument_id == "fx:aud-usd"
    return ProviderListing(
        listing_id=ProviderListingId("ibkr", "IBKR_PAPER", external_id),
        instrument_id=InstrumentId(instrument_id),
        display_name="AUD/USD" if is_fx else "Australia 200",
        product_type=ProductType.SPOT_FX if is_fx else ProductType.ROLLING_CFD,
        currency="USD" if is_fx else "AUD",
        minimum_deal_size=Decimal("1"),
        price_increment=Decimal("0.00005" if is_fx else "0.1"),
        valid_from=datetime(2026, 8, 8, 0, 0, tzinfo=UTC),
        valid_to=None,
        metadata_version="ibkr-b3-review-v1",
    )


def _evidence(
    *,
    con_id: int,
    instrument_id: str,
    liquid_hours: str = "20260808:0000-20260808:2400",
) -> IbkrContractEvidence:
    is_fx = instrument_id == "fx:aud-usd"
    return IbkrContractEvidence(
        con_id=con_id,
        symbol="AUD" if is_fx else "IBAU200",
        local_symbol="AUD.USD" if is_fx else "IBAU200",
        security_type="CASH" if is_fx else "CFD",
        exchange="IDEALPRO" if is_fx else "SMART",
        currency="USD" if is_fx else "AUD",
        trading_class="AUD.USD" if is_fx else "IBAU200",
        multiplier=None,
        minimum_tick=Decimal("0.00005" if is_fx else "0.1"),
        market_rule_ids=("26",),
        valid_exchanges=("IDEALPRO",) if is_fx else ("SMART",),
        long_name="AUD.USD" if is_fx else "Australia 200",
        underlier_con_id=None if is_fx else 111987392,
        timezone="UTC",
        trading_hours=liquid_hours,
        liquid_hours=liquid_hours,
    )


def _source(
    *,
    aud_con_id: int = 14433401,
    australia_con_id: int = 111987484,
    aud_liquid_hours: str = "20260808:0000-20260808:2400",
    australia_liquid_hours: str = "20260808:0000-20260808:2400",
    include_aud: bool = True,
    include_australia: bool = True,
) -> IbkrNativeCaptureConfiguration:
    listings: list[ProviderListing] = []
    evidence: dict[ProviderListingId, IbkrContractEvidence] = {}
    if include_aud:
        listing = _listing("fx:aud-usd", "aud-usd")
        listings.append(listing)
        evidence[listing.listing_id] = _evidence(
            con_id=aud_con_id,
            instrument_id="fx:aud-usd",
            liquid_hours=aud_liquid_hours,
        )
    if include_australia:
        listing = _listing("index:australia-200", "australia-200")
        listings.append(listing)
        evidence[listing.listing_id] = _evidence(
            con_id=australia_con_id,
            instrument_id="index:australia-200",
            liquid_hours=australia_liquid_hours,
        )
    return IbkrNativeCaptureConfiguration.from_reviewed(listings, evidence)


def _authority_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    return (
        tmp_path / "capability-review.json",
        tmp_path / "operator-selection.json",
        tmp_path / "contract-selection.json",
        tmp_path / "authority-catalogue.toml",
        tmp_path / "authority-probe.toml",
    )


def _authority_files(
    tmp_path: Path, source: IbkrNativeCaptureConfiguration
) -> tuple[Path, Path, Path, Path, Path]:
    catalogue = tmp_path / "authority-catalogue.toml"
    catalogue.write_text(
        'name = "fixture-candidates"\n\n'
        "[[instrument]]\n"
        'id = "fx:aud-usd"\n'
        'display_name = "AUD/USD"\n'
        'asset_class = "FX"\n'
        'base_currency = "AUD"\n'
        'quote_currency = "USD"\n'
        'search_aliases = ["AUD/USD"]\n\n'
        "[[instrument]]\n"
        'id = "index:australia-200"\n'
        'display_name = "Australia 200"\n'
        'asset_class = "INDEX"\n'
        'quote_currency = "AUD"\n'
        'search_aliases = ["Australia 200"]\n',
        encoding="utf-8",
    )
    probe = tmp_path / "authority-probe.toml"
    probe.write_text(
        'schema_version = 1\nname = "fixture-probe"\n\n'
        "[[query]]\n"
        'instrument_id = "fx:aud-usd"\n'
        'symbol = "AUD"\n'
        'security_type = "CASH"\n'
        'exchange = "IDEALPRO"\n'
        'currency = "USD"\n'
        'local_symbol = "AUD.USD"\n\n'
        "[[query]]\n"
        'instrument_id = "index:australia-200"\n'
        'symbol = "IBAU200"\n'
        'security_type = "CFD"\n'
        'exchange = "SMART"\n'
        'currency = "AUD"\n'
        'local_symbol = "IBAU200"\n',
        encoding="utf-8",
    )
    candidates = load_capture_candidates(catalogue)
    probe_spec = load_ibkr_capability_probe_spec(probe)
    fallback = _source()
    fallback_by_instrument = {
        listing.instrument_id: fallback.contract_evidence[listing.listing_id]
        for listing in fallback.listings
    }
    by_instrument = {
        listing.instrument_id: source.contract_evidence[listing.listing_id]
        for listing in source.listings
    }
    authority_by_instrument = {**fallback_by_instrument, **by_instrument}

    def contract_payload(contract: IbkrContractEvidence) -> dict[str, object]:
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "local_symbol": contract.local_symbol,
            "security_type": contract.security_type,
            "exchange": contract.exchange,
            "currency": contract.currency,
            "trading_class": contract.trading_class,
            "multiplier": contract.multiplier,
            "minimum_tick": (
                str(contract.minimum_tick) if contract.minimum_tick is not None else None
            ),
            "market_rule_ids": list(contract.market_rule_ids),
            "valid_exchanges": list(contract.valid_exchanges),
            "long_name": contract.long_name,
            "underlier_con_id": contract.underlier_con_id,
            "timezone": contract.timezone,
            "trading_hours": contract.trading_hours,
            "liquid_hours": contract.liquid_hours,
            "primary_exchange": contract.primary_exchange,
            "contract_month": contract.contract_month,
        }

    instruments: list[dict[str, object]] = []
    for query in probe_spec.queries:
        contract = by_instrument.get(
            query.instrument_id, fallback_by_instrument[query.instrument_id]
        )
        instruments.append(
            {
                "instrument_id": str(query.instrument_id),
                "display_name": next(
                    str(listing.display_name)
                    for listing in (*source.listings, *fallback.listings)
                    if listing.instrument_id == query.instrument_id
                ),
                "status": "OPERATOR_SELECTION_REQUIRED",
                "returned_contract_count": 1,
                "queries": [
                    {
                        "query": {
                            "instrument_id": str(query.instrument_id),
                            "symbol": query.symbol,
                            "security_type": query.security_type,
                            "exchange": query.exchange,
                            "currency": query.currency,
                            "local_symbol": query.local_symbol,
                            "trading_class": query.trading_class,
                            "multiplier": query.multiplier,
                        },
                        "contracts": [contract_payload(contract)],
                        "requests": [],
                    }
                ],
            }
        )
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "provider": "ibkr",
        "environment": "paper",
        "catalogue_name": candidates.name,
        "catalogue_hash": candidates.configuration_hash,
        "probe_spec_name": probe_spec.name,
        "probe_spec_hash": probe_spec.configuration_hash,
        "api": {"version": "10.49", "package_fingerprint": "c" * 64},
        "observed_at": _NOW.isoformat().replace("+00:00", "Z"),
        "selection_authority": False,
        "external_io_performed": True,
        "instruments": instruments,
    }
    review = {
        **unsigned,
        "review_hash": hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    review_path = tmp_path / "capability-review.json"
    review_path.write_text(json.dumps(review, sort_keys=True), encoding="utf-8")
    operator = {
        "schema_version": 1,
        "capability_review_sha256": review["review_hash"],
        "decisions": [
            {
                "instrument_id": str(instrument_id),
                "decision": IbkrContractDecision.ACCEPTED_EXACT_CONTRACT.value,
                "acquisition_eligible": True,
                "fingerprint": {
                    "con_id": contract.con_id,
                    "symbol": contract.symbol,
                    "security_type": contract.security_type,
                    "currency": contract.currency,
                    "exchange": contract.exchange,
                    "primary_exchange": contract.primary_exchange,
                    "local_symbol": contract.local_symbol,
                    "trading_class": contract.trading_class,
                    "multiplier": contract.multiplier,
                    "underlying_con_id": contract.underlier_con_id,
                    "contract_month": contract.contract_month,
                },
            }
            for instrument_id, contract in authority_by_instrument.items()
        ],
    }
    operator_path = tmp_path / "operator-selection.json"
    operator_path.write_text(json.dumps(operator, sort_keys=True), encoding="utf-8")
    selection = build_ibkr_contract_selection(
        capability_review=review,
        operator_selection=operator,
        canonical_instrument_ids=frozenset(
            instrument.instrument_id for instrument in candidates.instruments
        ),
        canonical_queries=frozenset(probe_spec.queries),
        frozen_by="b3-fixture",
        frozen_at=_NOW,
    )
    selection_path = tmp_path / "contract-selection.json"
    write_ibkr_contract_selection(selection_path, selection)
    return review_path, operator_path, selection_path, catalogue, probe


def _promote(
    source: IbkrNativeCaptureConfiguration,
    tmp_path: Path,
    *,
    authority_source: IbkrNativeCaptureConfiguration | None = None,
) -> IbkrNativeCaptureConfiguration:
    authority = _authority_files(tmp_path, authority_source or source)
    return promote_b3_configuration(
        source,
        capability_review_path=authority[0],
        operator_selection_path=authority[1],
        contract_selection_path=authority[2],
        catalogue_path=authority[3],
        probe_spec_path=authority[4],
    )


def _write_release(path: Path, configuration: IbkrNativeCaptureConfiguration) -> None:
    authority = _authority_paths(path.parent)
    write_reviewed_configuration(
        path,
        configuration,
        capability_review_path=authority[0],
        operator_selection_path=authority[1],
        contract_selection_path=authority[2],
        catalogue_path=authority[3],
        probe_spec_path=authority[4],
    )


def _verify_release(path: Path) -> dict[str, JsonValue]:
    authority = _authority_paths(path.parent)
    return verify_b3_release(
        path,
        capability_review_path=authority[0],
        operator_selection_path=authority[1],
        contract_selection_path=authority[2],
        catalogue_path=authority[3],
        probe_spec_path=authority[4],
        observed_at=_NOW,
    )


def _descriptor(
    path: Path,
    configuration: IbkrNativeCaptureConfiguration,
    *,
    api_host: str = B3_API_HOST,
    api_port: int = B3_API_PORT,
    api_version: str = "10.49",
    gateway_version: str = "10.49",
    gateway_host: str = "127.0.0.1",
    database_name: str = B3_DATABASE_NAME,
) -> None:
    path.write_text(
        f"""[release]
application_commit = "{"a" * 40}"
image = "registry.example.invalid/qtrad@sha256:{"b" * 64}"
configuration_path = "{path.parent / "capture.json"}"
configuration_hash = "{configuration.configuration_hash}"
api_package_fingerprint = "{"c" * 64}"
schema_head = "0014"

[authority]
capability_review_path = "{path.parent / "capability-review.json"}"
operator_selection_path = "{path.parent / "operator-selection.json"}"
contract_selection_path = "{path.parent / "contract-selection.json"}"
catalogue_path = "{path.parent / "authority-catalogue.toml"}"
probe_spec_path = "{path.parent / "authority-probe.toml"}"

[ibkr]
gateway_archive_sha256 = "{"d" * 64}"
api_version = "{api_version}"
gateway_version = "{gateway_version}"
ibc_version = "3.24.1"
client_id = 71

[network]
gateway_host = "{gateway_host}"
gateway_port = 4002
api_host = "{api_host}"
api_port = {api_port}

[database]
name = "{database_name}"
url_environment = "QTRAD_DATABASE_URL"
checkpoint_root = "/srv/qtrad/postgres/qtrad-ibkr-checkpoints"

[services]
ingest = "qtrad-ibkr-ingest.service"
api = "qtrad-ibkr-api.service"
health_timer = "qtrad-ibkr-health.timer"
backup_timer = "qtrad-ibkr-backup.timer"
""",
        encoding="utf-8",
    )


def test_b3_promotion_is_exact_two_and_reuses_authenticated_evidence(tmp_path: Path) -> None:
    promoted = _promote(_source(), tmp_path)

    assert len(promoted.listings) == 2
    assert {
        (str(listing.instrument_id), promoted.contract_evidence[listing.listing_id].con_id)
        for listing in promoted.listings
    } == {("fx:aud-usd", 14433401), ("index:australia-200", 111987484)}
    assert promoted.capture_source_id == "ibkr-paper-v1"
    assert promoted.universe_id == "capture-ibkr-v1"


def test_b3_promotion_rejects_non_con_id_provider_tamper(tmp_path: Path) -> None:
    source = _source()
    listing_id = ProviderListingId("ibkr", "IBKR_PAPER", "aud-usd")
    tampered_evidence = replace(
        source.contract_evidence[listing_id],
        exchange="TAMPERED_EXCHANGE",
    )
    tampered = IbkrNativeCaptureConfiguration.from_reviewed(
        source.listings,
        {**source.contract_evidence, listing_id: tampered_evidence},
    )

    with pytest.raises(ValueError, match="provider evidence"):
        _promote(tampered, tmp_path, authority_source=source)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("display_name", "Tampered AUD/USD"),
        ("product_type", ProductType.ROLLING_CFD),
        ("currency", "EUR"),
        ("minimum_deal_size", Decimal("2")),
        ("price_increment", Decimal("0.0001")),
        ("valid_from", datetime(2026, 8, 7, tzinfo=UTC)),
        ("valid_to", datetime(2026, 8, 9, tzinfo=UTC)),
        ("metadata_version", "tampered-metadata"),
        ("economics", {"contract_size": "tampered"}),
    ],
)
def test_b3_promotion_rejects_mutable_listing_semantics(
    tmp_path: Path,
    field_name: str,
    replacement: object,
) -> None:
    source = _source()
    listing = source.listings[0]
    tampered_listing = replace(listing, **{field_name: replacement})
    tampered = IbkrNativeCaptureConfiguration.from_reviewed(
        (tampered_listing, *source.listings[1:]),
        source.contract_evidence,
    )

    with pytest.raises(ValueError, match=rf"trusted policy.*{field_name}"):
        _promote(tampered, tmp_path, authority_source=source)


def test_b3_verification_rejects_rehashed_product_type_tamper() -> None:
    source = _source()
    listing = source.listings[0]
    tampered_listing = replace(listing, product_type=ProductType.ROLLING_CFD)
    tampered = IbkrNativeCaptureConfiguration.from_reviewed(
        (tampered_listing, *source.listings[1:]),
        source.contract_evidence,
    )

    report = verify_b3_configuration(tampered, observed_at=_NOW)

    assert report["valid"] is False
    errors = report["errors"]
    assert isinstance(errors, list)
    assert any(
        isinstance(error, str) and "listing policy mismatch" in error and "product_type" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("aud_con_id", "include_aud", "include_australia", "message"),
    [
        (999, True, True, "conId mismatch"),
        (14433401, False, True, "source is missing the authority instrument"),
        (14433401, True, False, "source is missing the authority instrument"),
    ],
)
def test_b3_promotion_rejects_substitution_or_missing_target(
    tmp_path: Path,
    aud_con_id: int,
    include_aud: bool,
    include_australia: bool,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _promote(
            _source(
                aud_con_id=aud_con_id,
                include_aud=include_aud,
                include_australia=include_australia,
            ),
            tmp_path,
        )


def test_b3_configuration_round_trip_is_create_only(tmp_path: Path) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "capture.json"

    _write_release(path, configuration)
    assert load_reviewed_configuration(path) == configuration
    assert path.read_bytes().endswith(b"\n")
    with pytest.raises(FileExistsError):
        _write_release(path, configuration)


def test_unknown_schedule_requires_a_new_authenticated_release(tmp_path: Path) -> None:
    configuration = _promote(_source(australia_liquid_hours="20260807:CLOSED"), tmp_path)
    report = verify_b3_configuration(configuration, observed_at=_NOW)

    assert report["contract"] == B3_RELEASE_CONTRACT
    assert report["valid"] is True
    assert report["operational_ready"] is False
    assert report["requires_evidence_refresh"] is True
    instruments = report["instruments"]
    assert isinstance(instruments, list)
    assert any(item["activity"] == "UNKNOWN" for item in instruments if isinstance(item, dict))

    path = tmp_path / "capture.json"
    _write_release(path, configuration)
    assert load_reviewed_configuration(path).configuration_hash == configuration.configuration_hash


def test_b3_preflight_binds_config_identity_and_units(tmp_path: Path) -> None:
    configuration = _promote(_source(), tmp_path)
    _write_release(tmp_path / "capture.json", configuration)
    descriptor_path = tmp_path / "deployment.toml"
    _descriptor(descriptor_path, configuration)

    report = b3_preflight(descriptor_path, repository_root=_REPOSITORY_ROOT, observed_at=_NOW)

    assert report["contract"] == B3_RELEASE_CONTRACT
    assert report["valid"] is True
    assert report["operational_ready"] is True
    assert report["configuration_hash"] == configuration.configuration_hash
    assert report["database_name"] == B3_DATABASE_NAME


def test_b3_release_requires_contract_marker(tmp_path: Path) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "capture.json"
    _write_release(path, configuration)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["contract"] = "self-consistent-but-unauthenticated"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = _verify_release(path)

    assert report["valid"] is False
    assert "contract marker is unsupported" in str(report["errors"])


def test_b3_release_rejects_changed_authority_file(tmp_path: Path) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "capture.json"
    _write_release(path, configuration)
    capability_review_path = _authority_paths(tmp_path)[0]
    capability_review_path.write_text(
        capability_review_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    report = _verify_release(path)

    assert report["valid"] is False
    assert "promotion authority identity mismatch" in str(report["errors"])


def test_b3_preflight_rejects_rehashed_contract_evidence_tamper(tmp_path: Path) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "capture.json"
    _write_release(path, configuration)
    listing = configuration.listings[0]
    tampered_evidence = dict(configuration.contract_evidence)
    tampered_evidence[listing.listing_id] = replace(
        tampered_evidence[listing.listing_id],
        exchange="SMART",
    )
    tampered = IbkrNativeCaptureConfiguration.from_reviewed(
        configuration.listings,
        tampered_evidence,
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    listings = document["listings"]
    assert isinstance(listings, list)
    persisted_listing = next(
        item
        for item in listings
        if isinstance(item, dict) and item.get("instrument_id") == str(listing.instrument_id)
    )
    evidence = persisted_listing["evidence"]
    assert isinstance(evidence, dict)
    evidence["exchange"] = "SMART"
    document["configuration_hash"] = tampered.configuration_hash
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    descriptor_path = tmp_path / "deployment.toml"
    _descriptor(descriptor_path, tampered)

    report = b3_preflight(descriptor_path, repository_root=_REPOSITORY_ROOT, observed_at=_NOW)

    assert report["valid"] is False
    assert "provider evidence does not match the authenticated review" in str(report["errors"])


@pytest.mark.parametrize(
    ("api_host", "database_name", "message"),
    [
        ("0.0.0.0", B3_DATABASE_NAME, "reviewed runtime endpoint"),
        ("127.0.0.1", "qtrad", "dedicated IBKR database"),
    ],
)
def test_b3_descriptor_rejects_public_or_shared_identity(
    tmp_path: Path, api_host: str, database_name: str, message: str
) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "deployment.toml"
    _descriptor(path, configuration, api_host=api_host, database_name=database_name)

    with pytest.raises(ValueError, match=message):
        IbkrB3DeploymentDescriptor.from_toml(path)


@pytest.mark.parametrize(
    ("api_host", "api_port"),
    [("::1", B3_API_PORT), (B3_API_HOST, 9000)],
)
def test_b3_descriptor_rejects_api_runtime_endpoint_drift(
    tmp_path: Path, api_host: str, api_port: int
) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "deployment.toml"
    _descriptor(path, configuration, api_host=api_host, api_port=api_port)

    with pytest.raises(ValueError, match="reviewed runtime endpoint"):
        IbkrB3DeploymentDescriptor.from_toml(path)


def test_b3_descriptor_rejects_gateway_host_spelling_not_used_by_deploy(
    tmp_path: Path,
) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "deployment.toml"
    _descriptor(path, configuration, gateway_host="localhost")

    with pytest.raises(ValueError, match="Gateway host"):
        IbkrB3DeploymentDescriptor.from_toml(path)


@pytest.mark.parametrize(
    ("api_version", "gateway_version"),
    [("10.50", "10.50"), ("10.49", "10.45")],
)
def test_b3_descriptor_rejects_unreviewed_or_mismatched_ibkr_versions(
    tmp_path: Path, api_version: str, gateway_version: str
) -> None:
    configuration = _promote(_source(), tmp_path)
    path = tmp_path / "deployment.toml"
    _descriptor(
        path,
        configuration,
        api_version=api_version,
        gateway_version=gateway_version,
    )

    with pytest.raises(ValueError, match="reviewed version"):
        IbkrB3DeploymentDescriptor.from_toml(path)


def test_b3_descriptor_rejects_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "deployment.toml"
    path.write_text('[release]\npassword = "must-not-be-here"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="secret-bearing"):
        IbkrB3DeploymentDescriptor.from_toml(path)


def test_b3_wiring_is_private_unprivileged_and_order_free() -> None:
    ops = _REPOSITORY_ROOT / "ops" / "ibkr"
    ingest = (ops / "qtrad-ibkr-ingest-wrapper.example").read_text(encoding="utf-8")
    ingest_unit = (ops / "qtrad-ibkr-ingest.service.example").read_text(encoding="utf-8")
    api = (ops / "qtrad-ibkr-api-wrapper.example").read_text(encoding="utf-8")
    deploy = (ops / "deploy.sh").read_text(encoding="utf-8")
    container_cli = (ops / "qtrad-container-cli.sh").read_text(encoding="utf-8")
    backup = (ops / "postgres-backup.sh").read_text(encoding="utf-8")
    restore = (ops / "postgres-restore-verify.sh").read_text(encoding="utf-8")
    qualification = (ops / "qtrad-ibkr-qualification-wrapper.example").read_text(encoding="utf-8")

    assert "qtrad ingest --provider ibkr" in ingest
    assert '--entrypoint /app/.venv/bin/python "$image"' in ingest
    assert "run --frozen" not in ingest
    assert "--user 10001:10001" in ingest
    assert "--read-only" in ingest
    assert "--cap-drop=ALL" in ingest
    assert "--volume /srv/qtrad/postgres:/srv/qtrad/postgres:rw" in ingest
    assert "placeOrder" not in ingest
    assert "QTRAD_IBKR_PASSWORD" not in ingest
    assert "QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH" in ingest
    assert "qtrad api --host 127.0.0.1 --port 8000" in api
    assert '--entrypoint /app/.venv/bin/python "$image"' in api
    assert "run --frozen" not in api
    assert "--user 10001:10001" in api
    assert "--read-only" in api
    assert "--publish" not in api
    assert "QTRAD_IBKR_PASSWORD" not in api
    assert "QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH" in api
    assert "usage: deploy.sh --check|--apply" in deploy
    assert '[[ "$historical_client_id" != "$client_id" ]]' in deploy
    assert '--entrypoint /app/.venv/bin/python "$image"' in deploy
    assert "run --frozen --no-dev --no-sync python -m qtrad db verify-head" not in deploy
    script_dir = 'script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"'
    assert script_dir in deploy
    assert deploy.index(script_dir) < deploy.index("canonical_env_file=")
    assert 'env_file="/etc/qtrad/ibkr-ingest.env"' in deploy
    assert "EnvironmentFile=/etc/qtrad/ibkr-ingest.env" in ingest_unit
    assert "--env-file /etc/qtrad/ibkr-ingest.env" in ingest
    assert "verify_host_identity" in deploy
    assert 'preflight_bin="${QTRAD_B3_PREFLIGHT_BIN:-}"' in deploy
    assert 'bash "$script_dir/qtrad-container-cli.sh"' in deploy
    assert "--network none" in container_cli
    assert "--user 10001:10001" in container_cli
    assert "--read-only" in container_cli
    assert "--cap-drop=ALL" in container_cli
    assert "--volume /etc/qtrad:/etc/qtrad:ro" in container_cli
    assert "--volume /srv/qtrad/ibkr:/srv/qtrad/ibkr:ro" in container_cli
    assert '--entrypoint /app/.venv/bin/python "$image"' in container_cli
    assert '-m qtrad "$@"' in container_cli
    assert "verify_database_head" in deploy
    assert "db verify-head" in deploy
    assert "qtrad db upgrade" not in deploy
    assert "docker system prune" not in deploy
    assert "docker image prune" not in deploy
    assert "docker container ls -aq" in deploy
    assert "docker image rm" in deploy
    assert 'backup_env_file="/etc/qtrad/ibkr-backup.env"' in deploy
    assert "verify_backup_identity" in deploy
    assert "systemctl enable --now" not in deploy
    assert "systemctl restart qtrad-ibkr-api.service qtrad-ibkr-ingest.service" in deploy
    assert "systemctl restart qtrad-ibkr-health.timer qtrad-ibkr-backup.timer" in deploy
    assert "qtrad_ibkr" in backup
    assert "qtrad_ibkr_restore_verify_" in restore
    assert "sha256sum --check" in restore
    assert 'docker exec -i "$container" pg_restore --exit-on-error' in restore
    assert 'docker exec -i "$container" pg_restore --list' in backup
    assert "COMMENT ON DATABASE" in restore
    assert "restore-evidence" in restore
    assert "QTRAD_IBKR_RESTORE_ARCHIVE" in restore
    assert '"$@"' in restore
    assert "qtrad-ibkr-qualification-wrapper.example" in deploy
    assert "reviewed qualification wrapper is unavailable" in deploy
    assert "QTRAD_IBKR_QUALIFICATION_RESTORE_DATABASE_URL" in qualification
    assert "--user 10001:10001" in qualification
    assert "--read-only" in qualification
    assert "--cap-drop=ALL" in qualification
    assert "placeOrder" not in qualification
    assert "trap cleanup EXIT" in restore
    assert "QTRAD_IBKR_PASSWORD" not in backup + restore


@pytest.mark.parametrize(
    ("arguments", "expected_returncode"),
    [
        (("--max-seconds", "180", "--force-reconnect-after-seconds", "60"), 0),
        (("--force-reconnect-after-seconds", "60", "--max-seconds", "180"), 64),
        (("--max-seconds", "60", "--force-reconnect-after-seconds", "60"), 64),
    ],
)
def test_ingest_wrapper_bounds_qualification_arguments(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected_returncode: int,
) -> None:
    wrapper = _REPOSITORY_ROOT / "ops" / "ibkr" / "qtrad-ibkr-ingest-wrapper.example"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    _write_executable(fake_bin / "ss", "#!/bin/sh\nprintf 'LISTEN\\n'\n")
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{docker_calls}'\n",
    )
    checkpoint_root = tmp_path / "checkpoints"
    checkpoint_root.mkdir()
    configuration = tmp_path / "capture.json"
    configuration.write_text("{}\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
            "QTRAD_IBKR_IMAGE": "registry.example.invalid/qtrad@sha256:" + "a" * 64,
            "QTRAD_IBKR_CHECKPOINT_ROOT": str(checkpoint_root),
            "QTRAD_IBKR_CAPTURE_CONFIGURATION_PATH": str(configuration),
            "QTRAD_DATABASE_URL": "postgresql+asyncpg://qtrad_ibkr@127.0.0.1:5432/qtrad_ibkr",
            "QTRAD_IBKR_API_PACKAGE_FINGERPRINT": "b" * 64,
            "QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH": "c" * 64,
        }
    )

    result = subprocess.run(
        ["bash", str(wrapper), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode, result.stderr
    if expected_returncode == 0:
        call = docker_calls.read_text(encoding="utf-8")
        assert "--max-seconds 180 --force-reconnect-after-seconds 60" in call
        assert call.count("--provider ibkr") == 1
    else:
        assert not docker_calls.exists()


@pytest.mark.parametrize("mode", ["--check", "--apply"])
def test_deploy_rejects_stale_schedule_before_mutation(tmp_path: Path, mode: str) -> None:
    image = "registry.example.invalid/qtrad@sha256:" + "b" * 64
    configuration_hash = "a" * 64
    api_fingerprint = "c" * 64
    gateway_archive_sha = "d" * 64
    configuration_path = "/srv/qtrad/ibkr/capture.json"
    descriptor = tmp_path / "deployment.toml"
    descriptor.write_text(
        '[release]\napplication_commit = "' + "e" * 40 + '"\n',
        encoding="utf-8",
    )
    gateway_manifest = tmp_path / "gateway-manifest.json"
    gateway_manifest.write_text("{}\n", encoding="utf-8")
    docker_marker = tmp_path / "docker-called"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/bin/sh\nprintf 'docker was reached\\n' > " + str(docker_marker) + "\nexit 99\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    preflight = tmp_path / "qtrad-preflight"
    preflight.write_text(
        "#!/bin/sh\n"
        "cat <<'JSON'\n"
        + json.dumps(
            {
                "application_commit": "e" * 40,
                "valid": True,
                "operational_ready": False,
                "requires_evidence_refresh": True,
                "image": image,
                "configuration_hash": configuration_hash,
                "configuration_path": configuration_path,
                "api_package_fingerprint": api_fingerprint,
                "gateway_archive_sha256": gateway_archive_sha,
                "api_version": "10.49",
                "gateway_version": "10.49",
                "ibc_version": "3.24.1",
                "database_name": "qtrad_ibkr",
                "database_url_environment": "QTRAD_DATABASE_URL",
                "gateway_host": "127.0.0.1",
                "api_host": "127.0.0.1",
                "gateway_port": 4002,
                "api_port": 8000,
                "client_id": 71,
                "source": "ibkr-paper-v1",
                "universe": "capture-ibkr-v1",
            },
            sort_keys=True,
        )
        + "\nJSON\n",
        encoding="utf-8",
    )
    preflight.chmod(0o755)

    environment = os.environ.copy()
    environment.pop("QTRAD_IBKR_ENV_FILE", None)
    environment.update(
        {
            "PATH": str(tmp_path) + os.pathsep + environment["PATH"],
            "QTRAD_IBKR_IMAGE": image,
            "QTRAD_IMAGE": image,
            "QTRAD_IBKR_RELEASE_DESCRIPTOR": str(descriptor),
            "QTRAD_IBKR_REPOSITORY_ROOT": str(tmp_path),
            "QTRAD_B3_PREFLIGHT_BIN": str(preflight),
            "QTRAD_IBKR_CHECKPOINT_ROOT": "/srv/qtrad/ibkr/checkpoints",
            "QTRAD_IBKR_API_PACKAGE_FINGERPRINT": api_fingerprint,
            "QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256": gateway_archive_sha,
            "QTRAD_IBKR_GATEWAY_MANIFEST": str(gateway_manifest),
            "QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH": configuration_hash,
            "QTRAD_IBKR_CAPTURE_CONFIGURATION_PATH": configuration_path,
            "QTRAD_DATABASE_URL": "postgresql+asyncpg://qtrad_ibkr@127.0.0.1:5432/qtrad_ibkr",
            "QTRAD_IBKR_PREFLIGHT_OBSERVED_AT": "2026-08-08T12:00:00Z",
        }
    )
    result = subprocess.run(
        ["bash", str(_REPOSITORY_ROOT / "ops" / "ibkr" / "deploy.sh"), mode],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "release identity does not match" in result.stderr
    assert not docker_marker.exists()


def test_deploy_rejects_noncanonical_env_override(tmp_path: Path) -> None:
    image_a = "registry.example.invalid/qtrad@sha256:" + "a" * 64
    image_b = "registry.example.invalid/qtrad@sha256:" + "b" * 64
    env_a = tmp_path / "release-a.env"
    env_b = tmp_path / "release-b.env"
    env_a.write_text(f"QTRAD_IBKR_IMAGE={image_a}\n", encoding="utf-8")
    env_b.write_text(f"QTRAD_IBKR_IMAGE={image_b}\n", encoding="utf-8")
    preflight_marker = tmp_path / "preflight-called"
    preflight = tmp_path / "qtrad-preflight"
    preflight.write_text(
        "#!/bin/sh\nprintf 'preflight was reached\\n' > " + str(preflight_marker) + "\n",
        encoding="utf-8",
    )
    preflight.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "QTRAD_IBKR_ENV_FILE": str(env_a),
            "QTRAD_B3_PREFLIGHT_BIN": str(preflight),
        }
    )
    result = subprocess.run(
        ["bash", str(_REPOSITORY_ROOT / "ops" / "ibkr" / "deploy.sh"), "--apply"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert env_a.read_text(encoding="utf-8") != env_b.read_text(encoding="utf-8")
    assert result.returncode != 0
    assert "canonical" in result.stderr
    assert not preflight_marker.exists()


@pytest.mark.parametrize("mode", ["--check", "--apply"])
def test_deploy_successful_mocked_release_path(tmp_path: Path, mode: str) -> None:
    repository = tmp_path / "repository"
    script_dir = repository / "ops" / "ibkr"
    script_dir.mkdir(parents=True)
    canonical_env = tmp_path / "ibkr-ingest.env"
    backup_env = tmp_path / "ibkr-backup.env"
    calls = tmp_path / "calls"

    deploy_source = (_REPOSITORY_ROOT / "ops" / "ibkr" / "deploy.sh").read_text(encoding="utf-8")
    deploy_source = deploy_source.replace(
        'canonical_env_file="/etc/qtrad/ibkr-ingest.env"',
        f'canonical_env_file="{canonical_env}"',
    )
    deploy_source = deploy_source.replace(
        'backup_env_file="/etc/qtrad/ibkr-backup.env"',
        f'backup_env_file="{backup_env}"',
    )
    deploy_path = script_dir / "deploy.sh"
    _write_executable(deploy_path, deploy_source)
    _write_executable(
        script_dir / "verify-host.sh",
        f"#!/bin/sh\nprintf '%s\\n' verify-host >> '{calls}'\n",
    )
    _write_executable(
        script_dir / "postgres-provision.sh",
        f"#!/bin/sh\nprintf '%s\\n' postgres-provision >> '{calls}'\n",
    )
    for wrapper in (
        "qtrad-ibkr-qualification-wrapper.example",
        "qtrad-ibkr-dual-restore-qualification.example",
    ):
        _write_executable(script_dir / wrapper, "#!/bin/sh\n")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "ops/ibkr"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=q-trad test",
            "-c",
            "user.email=qtrad-test@example.invalid",
            "commit",
            "-qm",
            "deployment fixture",
        ],
        check=True,
    )
    application_commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    image_repository = "registry.example.invalid/qtrad"
    image_digest = "b" * 64
    image = f"{image_repository}@sha256:{image_digest}"
    deployed_image_id = "sha256:" + "b" * 64
    referenced_image_id = "sha256:" + "c" * 64
    rollback_image_id = "sha256:" + "d" * 64
    removable_image_id = "sha256:" + "e" * 64
    unrelated_image_id = "sha256:" + "f" * 64
    configuration_hash = "a" * 64
    api_fingerprint = "c" * 64
    gateway_archive_sha = "d" * 64

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_script = f"""#!/bin/sh
printf '%s %s\\n' docker "$*" >> '{calls}'
case "$1 $2" in
    "image inspect")
        printf '%s\\n' '{deployed_image_id}'
        ;;
    "container ls")
        printf '%s\\n' current-container retained-container
        ;;
    "container inspect")
        case "$5" in
            current-container) printf '%s\\n' '{deployed_image_id}' ;;
            retained-container) printf '%s\\n' '{referenced_image_id}' ;;
            *) exit 88 ;;
        esac
        ;;
    "image ls")
        printf '%s\\n' \
            '{image_repository}|sha256:{image_digest}|{deployed_image_id}' \
            '{image_repository}|sha256:{"c" * 64}|{referenced_image_id}' \
            '{image_repository}|sha256:{"d" * 64}|{rollback_image_id}' \
            '{image_repository}|sha256:{"e" * 64}|{removable_image_id}' \
            'registry.example.invalid/unrelated|sha256:{"f" * 64}|{unrelated_image_id}'
        ;;
esac
"""
    _write_executable(fake_bin / "docker", docker_script)
    for command in ("install", "systemctl"):
        _write_executable(
            fake_bin / command,
            f"#!/bin/sh\nprintf '%s %s\\n' {command} \"$*\" >> '{calls}'\n",
        )
    _write_executable(
        fake_bin / "stat",
        "#!/bin/sh\ncase \"$*\" in *%U:%G*) printf 'root:root\\n' ;; *) printf '600\\n' ;; esac\n",
    )
    configuration_path = "/srv/qtrad/ibkr/capture.json"
    descriptor = tmp_path / "deployment.toml"
    descriptor.write_text("[release]\n", encoding="utf-8")
    gateway_manifest = tmp_path / "gateway-manifest.json"
    gateway_manifest.write_text("{}\n", encoding="utf-8")
    canonical_env.write_text(
        f"QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH={configuration_hash}\n",
        encoding="utf-8",
    )
    backup_env.write_text(
        "QTRAD_IBKR_BACKUP_DIR=/srv/qtrad/postgres/backups\n"
        "QTRAD_IBKR_STATUS_DIR=/var/lib/qtrad/ibkr\n"
        "QTRAD_IBKR_POSTGRES_CONTAINER=qtrad-ibkr-native-postgres\n"
        "QTRAD_IBKR_POSTGRES_DATABASE=qtrad_ibkr\n"
        "QTRAD_IBKR_POSTGRES_USER=qtrad_ibkr\n"
        "QTRAD_IBKR_BACKUP_RETENTION_DAYS=14\n"
        "QTRAD_IBKR_RUNTIME_GID=10001\n",
        encoding="utf-8",
    )
    backup_env.chmod(0o600)

    preflight = tmp_path / "qtrad-preflight"
    preflight_report = {
        "application_commit": application_commit,
        "valid": True,
        "operational_ready": True,
        "requires_evidence_refresh": False,
        "image": image,
        "configuration_hash": configuration_hash,
        "configuration_path": configuration_path,
        "api_package_fingerprint": api_fingerprint,
        "gateway_archive_sha256": gateway_archive_sha,
        "api_version": "10.49",
        "gateway_version": "10.49",
        "ibc_version": "3.24.1",
        "database_name": "qtrad_ibkr",
        "database_url_environment": "QTRAD_DATABASE_URL",
        "gateway_host": "127.0.0.1",
        "api_host": B3_API_HOST,
        "gateway_port": 4002,
        "api_port": B3_API_PORT,
        "client_id": 71,
        "source": "ibkr-paper-v1",
        "universe": "capture-ibkr-v1",
    }
    _write_executable(
        preflight,
        "#!/bin/sh\nprintf '%s\\n' '" + json.dumps(preflight_report) + "'\n",
    )

    environment = os.environ.copy()
    environment.pop("QTRAD_IBKR_ENV_FILE", None)
    environment.update(
        {
            "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
            "QTRAD_IBKR_IMAGE": image,
            "QTRAD_IMAGE": image,
            "QTRAD_IBKR_RELEASE_DESCRIPTOR": str(descriptor),
            "QTRAD_B3_PREFLIGHT_BIN": str(preflight),
            "QTRAD_IBKR_CHECKPOINT_ROOT": "/srv/qtrad/ibkr/checkpoints",
            "QTRAD_IBKR_API_PACKAGE_FINGERPRINT": api_fingerprint,
            "QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256": gateway_archive_sha,
            "QTRAD_IBKR_GATEWAY_MANIFEST": str(gateway_manifest),
            "QTRAD_IBKR_CAPTURE_CONFIGURATION_PATH": configuration_path,
            "QTRAD_IBKR_HISTORICAL_CLIENT_ID": "72",
            "QTRAD_DATABASE_URL": ("postgresql+asyncpg://qtrad_ibkr@127.0.0.1:5432/qtrad_ibkr"),
            "QTRAD_IBKR_PREFLIGHT_OBSERVED_AT": "2026-08-09T02:30:00Z",
        }
    )
    result = subprocess.run(
        ["bash", str(deploy_path), mode],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    recorded_calls = calls.read_text(encoding="utf-8").splitlines()
    assert "verify-host" in recorded_calls
    assert "postgres-provision" in recorded_calls
    assert any(
        call.startswith("docker run ") and "db verify-head" in call for call in recorded_calls
    )
    assert f"keep {deployed_image_id} reason=deployed" in result.stdout
    assert f"keep {referenced_image_id} reason=container-referenced" in result.stdout
    assert f"keep {rollback_image_id} reason=most-recent-unreferenced-rollback" in result.stdout
    removable_reference = f"{image_repository}@sha256:{'e' * 64}"
    assert f"remove {removable_image_id} reference={removable_reference}" in result.stdout
    assert unrelated_image_id not in result.stdout
    image_remove_call = f"docker image rm {removable_reference}"
    if mode == "--check":
        assert "no host mutation performed" in result.stdout
        assert not any(call.startswith(("install ", "systemctl ")) for call in recorded_calls)
        assert image_remove_call not in recorded_calls
    else:
        assert (
            "systemctl enable qtrad-ibkr-postgres.service qtrad-ibkr-api.service "
            "qtrad-ibkr-ingest.service "
            "qtrad-ibkr-health.timer qtrad-ibkr-backup.timer"
        ) in recorded_calls
        ingest_restart = "systemctl restart qtrad-ibkr-api.service qtrad-ibkr-ingest.service"
        assert ingest_restart in recorded_calls
        assert (
            "systemctl restart qtrad-ibkr-health.timer qtrad-ibkr-backup.timer"
        ) in recorded_calls
        assert image_remove_call in recorded_calls
        assert recorded_calls.index(ingest_restart) < recorded_calls.index(image_remove_call)
        assert not any("enable --now" in call for call in recorded_calls)
