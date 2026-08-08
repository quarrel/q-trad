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
from qtrad.domain.ibkr_historical import IbkrContractDecision
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.ibkr_b3 import (
    B3_DATABASE_NAME,
    B3_RELEASE_CONTRACT,
    IbkrB3DeploymentDescriptor,
    b3_preflight,
    promote_b3_configuration,
    verify_b3_configuration,
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
        'base_currency = "AUD"\n'
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


def _descriptor(
    path: Path,
    configuration: IbkrNativeCaptureConfiguration,
    *,
    api_host: str = "127.0.0.1",
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

[ibkr]
gateway_archive_sha256 = "{"d" * 64}"
api_version = "10.49"
gateway_version = "10.49"
ibc_version = "3.24.1"
client_id = 71

[network]
gateway_host = "127.0.0.1"
gateway_port = 4002
api_host = "{api_host}"
api_port = 8000

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


def test_b3_promotion_rejects_unauthenticated_listing_economics(tmp_path: Path) -> None:
    source = _source()
    listing = source.listings[0]
    tampered_listing = replace(listing, economics={"contract_size": "tampered"})
    tampered = IbkrNativeCaptureConfiguration.from_reviewed(
        (tampered_listing, *source.listings[1:]),
        source.contract_evidence,
    )

    with pytest.raises(ValueError, match="listing economics"):
        _promote(tampered, tmp_path, authority_source=source)


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

    write_reviewed_configuration(path, configuration)
    assert load_reviewed_configuration(path) == configuration
    assert path.read_bytes().endswith(b"\n")
    with pytest.raises(FileExistsError):
        write_reviewed_configuration(path, configuration)


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
    write_reviewed_configuration(path, configuration)
    assert load_reviewed_configuration(path).configuration_hash == configuration.configuration_hash


def test_b3_preflight_binds_config_identity_and_units(tmp_path: Path) -> None:
    configuration = _promote(_source(), tmp_path)
    write_reviewed_configuration(tmp_path / "capture.json", configuration)
    descriptor_path = tmp_path / "deployment.toml"
    _descriptor(descriptor_path, configuration)

    report = b3_preflight(descriptor_path, repository_root=_REPOSITORY_ROOT, observed_at=_NOW)

    assert report["contract"] == B3_RELEASE_CONTRACT
    assert report["valid"] is True
    assert report["operational_ready"] is True
    assert report["configuration_hash"] == configuration.configuration_hash
    assert report["database_name"] == B3_DATABASE_NAME


@pytest.mark.parametrize(
    ("api_host", "database_name", "message"),
    [
        ("0.0.0.0", B3_DATABASE_NAME, "loopback"),
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


def test_b3_descriptor_rejects_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "deployment.toml"
    path.write_text('[release]\npassword = "must-not-be-here"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="secret-bearing"):
        IbkrB3DeploymentDescriptor.from_toml(path)


def test_b3_wiring_is_private_unprivileged_and_order_free() -> None:
    ops = _REPOSITORY_ROOT / "ops" / "ibkr"
    ingest = (ops / "qtrad-ibkr-ingest-wrapper.example").read_text(encoding="utf-8")
    api = (ops / "qtrad-ibkr-api-wrapper.example").read_text(encoding="utf-8")
    deploy = (ops / "deploy.sh").read_text(encoding="utf-8")
    backup = (ops / "postgres-backup.sh").read_text(encoding="utf-8")
    restore = (ops / "postgres-restore-verify.sh").read_text(encoding="utf-8")

    assert "qtrad ingest --provider ibkr" in ingest
    assert "--entrypoint uv" in ingest
    assert "--user 10001:10001" in ingest
    assert "--read-only" in ingest
    assert "--cap-drop=ALL" in ingest
    assert "--volume /srv/qtrad/postgres:/srv/qtrad/postgres:rw" in ingest
    assert "placeOrder" not in ingest
    assert "QTRAD_IBKR_PASSWORD" not in ingest
    assert "QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH" in ingest
    assert "qtrad api --host 127.0.0.1 --port 8000" in api
    assert "--user 10001:10001" in api
    assert "--read-only" in api
    assert "--publish" not in api
    assert "QTRAD_IBKR_PASSWORD" not in api
    assert "QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH" in api
    assert "usage: deploy.sh --check|--apply" in deploy
    assert "verify_host_identity" in deploy
    assert "verify_database_head" in deploy
    assert "db verify-head" in deploy
    assert "qtrad db upgrade" not in deploy
    assert "systemctl enable --now" in deploy
    assert "qtrad_ibkr" in backup
    assert "qtrad_ibkr_restore_" in restore
    assert "trap cleanup EXIT" in restore
    assert "QTRAD_IBKR_PASSWORD" not in backup + restore


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
    environment.update(
        {
            "PATH": str(tmp_path) + os.pathsep + environment["PATH"],
            "QTRAD_IBKR_ENV_FILE": str(tmp_path / "missing.env"),
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
