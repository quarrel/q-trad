"""Offline B3 exact-two release, preflight, and wiring tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

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
from qtrad.runtime.ibkr_native_capture import (
    IbkrNativeCaptureConfiguration,
    load_reviewed_configuration,
)

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
        multiplier="",
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


def test_b3_promotion_is_exact_two_and_reuses_authenticated_evidence() -> None:
    promoted = promote_b3_configuration(_source())

    assert len(promoted.listings) == 2
    assert {
        (str(listing.instrument_id), promoted.contract_evidence[listing.listing_id].con_id)
        for listing in promoted.listings
    } == {("fx:aud-usd", 14433401), ("index:australia-200", 111987484)}
    assert promoted.capture_source_id == "ibkr-paper-v1"
    assert promoted.universe_id == "capture-ibkr-v1"


@pytest.mark.parametrize(
    ("aud_con_id", "include_aud", "include_australia", "message"),
    [
        (999, True, True, "conId mismatch"),
        (14433401, False, True, "exactly one reviewed listing"),
        (14433401, True, False, "exactly one reviewed listing"),
    ],
)
def test_b3_promotion_rejects_substitution_or_missing_target(
    aud_con_id: int,
    include_aud: bool,
    include_australia: bool,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        promote_b3_configuration(
            _source(
                aud_con_id=aud_con_id,
                include_aud=include_aud,
                include_australia=include_australia,
            )
        )


def test_b3_configuration_round_trip_is_create_only(tmp_path: Path) -> None:
    configuration = promote_b3_configuration(_source())
    path = tmp_path / "capture.json"

    write_reviewed_configuration(path, configuration)
    assert load_reviewed_configuration(path) == configuration
    assert path.read_bytes().endswith(b"\n")
    with pytest.raises(FileExistsError):
        write_reviewed_configuration(path, configuration)


def test_unknown_schedule_requires_a_new_authenticated_release(tmp_path: Path) -> None:
    configuration = promote_b3_configuration(_source(australia_liquid_hours="20260807:CLOSED"))
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
    configuration = promote_b3_configuration(_source())
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
    configuration = promote_b3_configuration(_source())
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
    assert "docker pull" in deploy
    assert "systemctl enable --now" in deploy
    assert "qtrad_ibkr" in backup
    assert "qtrad_ibkr_restore_" in restore
    assert "qtrad_ibkr_restore_verify" in restore
    assert "QTRAD_IBKR_PASSWORD" not in backup + restore
