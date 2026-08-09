from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from qtrad.domain.ibkr_qualification import (
    IbkrQualificationStage,
    IbkrQualifiedContract,
    has_verified_ibkr_capture_qualification_provenance,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.ibkr_native_capture import IbkrNativeCaptureConfiguration
from qtrad.runtime.ibkr_qualification import (
    IbkrQualificationExpectation,
    write_qualification_artifact,
)
from qtrad.runtime.ibkr_qualification_evidence import (
    IbkrQualificationWindow,
    build_ibkr_qualification_snapshot,
    verify_ibkr_qualification_evidence,
)

_START = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
_END = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
_GENERATED = datetime(2026, 8, 10, 1, 4, tzinfo=UTC)
_SESSION = UUID("00000000-0000-0000-0000-000000000001")


def _configuration() -> IbkrNativeCaptureConfiguration:
    definitions = (
        ("fx:aud-usd", "aud-usd", "USD", "CASH", ProductType.SPOT_FX, 1),
        (
            "index:australia-200",
            "australia-200",
            "AUD",
            "CFD",
            ProductType.ROLLING_CFD,
            2,
        ),
    )
    listings: list[ProviderListing] = []
    evidence: dict[ProviderListingId, IbkrContractEvidence] = {}
    for instrument, external, currency, security_type, product_type, con_id in definitions:
        listing = ProviderListing(
            listing_id=ProviderListingId("ibkr", "IBKR_PAPER", external),
            instrument_id=InstrumentId(instrument),
            display_name=external,
            product_type=product_type,
            currency=currency,
            minimum_deal_size=Decimal("1"),
            price_increment=Decimal("0.1"),
            valid_from=_START - timedelta(days=1),
            valid_to=None,
            metadata_version="fixture-v1",
        )
        listings.append(listing)
        evidence[listing.listing_id] = IbkrContractEvidence(
            con_id=con_id,
            symbol=external,
            local_symbol=external,
            security_type=security_type,
            exchange="SMART",
            currency=currency,
            trading_class=external,
            multiplier=None,
            minimum_tick=Decimal("0.1"),
            market_rule_ids=("1",),
            valid_exchanges=("SMART",),
            long_name=external,
            underlier_con_id=None,
            timezone="UTC",
            trading_hours="20260810:0000-20260810:2400",
            liquid_hours="20260810:0000-20260810:2400",
        )
    return IbkrNativeCaptureConfiguration.from_reviewed(listings, evidence)


def _expectation(configuration: IbkrNativeCaptureConfiguration) -> IbkrQualificationExpectation:
    return IbkrQualificationExpectation(
        stage=IbkrQualificationStage.B3_EXACT_TWO,
        release_contract="qtrad-ibkr-native-release-v1",
        release_sha256="1" * 64,
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
        application_commit="3" * 40,
        image_digest="ghcr.io/quarrel/qtrad@sha256:" + "4" * 64,
        api_package_sha256="5" * 64,
        gateway_archive_sha256="6" * 64,
        gateway_version="10.49",
        ibc_version="3.24.1",
        database_name="qtrad_ibkr",
        schema_head="0014",
    )


def _retained_rows(configuration: IbkrNativeCaptureConfiguration) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sequence = 0
    for generation, base_seconds in ((1, 10), (2, 3570)):
        for request_id, listing in enumerate(configuration.listings, start=1):
            evidence = configuration.contract_evidence[listing.listing_id]
            for callback_type, tick_type, offset in (
                ("market_data_type", None, 0),
                ("tick_price", 1, 1),
                ("tick_price", 2, 2),
            ):
                sequence += 1
                received = _START + timedelta(seconds=base_seconds + request_id * 3 + offset)
                payload: dict[str, Any] = {
                    "callback_type": callback_type,
                    "request_id": generation * 100 + request_id,
                    "listing_id": str(listing.listing_id),
                    "con_id": evidence.con_id,
                    "callback_values": [1] if callback_type == "market_data_type" else [],
                }
                if tick_type is not None:
                    payload["tick_type"] = tick_type
                rows.append(
                    {
                        "raw_record_id": sequence,
                        "received_time": received,
                        "payload_sha256": f"{sequence:064x}",
                        "connection_generation": generation,
                        "arrival_sequence": sequence,
                        "payload": payload,
                        "global_position": sequence if tick_type is not None else None,
                        "event_type": "MarketQuoteObserved" if tick_type is not None else None,
                        "event_time": received if tick_type is not None else None,
                        "canonical_payload": (
                            {"quality": "HEALTHY"} if tick_type is not None else None
                        ),
                    }
                )
    return rows


class _Store:
    def __init__(
        self,
        database_name: str,
        configuration: IbkrNativeCaptureConfiguration,
        *,
        mutate_row: bool = False,
        market_data_type: int = 1,
        metrics_failed: int = 0,
        reconnect_completed: bool = True,
    ) -> None:
        self.database_name = database_name
        self.configuration = configuration
        self.rows = _retained_rows(configuration)
        if mutate_row:
            self.rows[-1]["payload_sha256"] = "f" * 64
        for row in self.rows:
            payload = cast(dict[str, Any], row["payload"])
            if payload["callback_type"] == "market_data_type":
                payload["callback_values"] = [market_data_type]
        self.metrics_failed = metrics_failed
        self.reconnect_completed = reconnect_completed

    async def query(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> list[dict[str, Any]]:
        del parameters
        if "current_database()" in statement:
            return [{"database_name": self.database_name, "schema_head": "0014"}]
        if "FROM raw.market_messages" in statement:
            return self.rows
        if "FROM ops.capture_session_metrics" in statement:
            return [
                {
                    "observed_at": _END,
                    "records_received": len(self.rows),
                    "persisted": len(self.rows),
                    "failed": self.metrics_failed,
                    "dropped": 0,
                }
            ]
        if "FROM ops.runs" in statement:
            return [
                {
                    "run_id": _SESSION,
                    "kind": "INGESTION",
                    "status": "STOPPED",
                    "environment": "IBKR_PAPER",
                    "started_at": _START,
                    "finished_at": _END + timedelta(seconds=1),
                    "configuration_hash": self.configuration.configuration_hash,
                    "detail": {
                        "qualification_health": {
                            "status": "HEALTHY",
                            "observed_at": _END,
                            "reason_codes": [],
                            "recovery_action": "NONE",
                            "attributes": {
                                "capture_session_id": str(_SESSION),
                                "capture_source_id": self.configuration.capture_source_id,
                                "universe_id": self.configuration.universe_id,
                                "configuration_hash": self.configuration.configuration_hash,
                                "source_class": "IBKR_NATIVE_CAPTURE",
                                "desired_subscriptions": "2",
                                "active_subscriptions": "2",
                                "forced_reconnect_requested": "true",
                                "forced_reconnect_completed": str(self.reconnect_completed).lower(),
                                "reconnect_from_generation": "1",
                                "reconnect_to_generation": "2",
                            },
                        }
                    },
                }
            ]
        raise AssertionError(statement)


def _window() -> IbkrQualificationWindow:
    return IbkrQualificationWindow(_SESSION, _START, _END, _GENERATED)


@pytest.mark.asyncio
async def test_independent_live_and_restore_replay_mints_opaque_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = _configuration()
    expectation = _expectation(configuration)
    live = _Store("qtrad_ibkr", configuration)
    restored = _Store("qtrad_ibkr_restore_verify_fixture", configuration)
    payload = await build_ibkr_qualification_snapshot(
        live,
        restored,
        expectation=expectation,
        configuration=configuration,
        window=_window(),
    )
    path = tmp_path / "qualification.json"
    write_qualification_artifact(path, payload)
    monkeypatch.setattr(
        "qtrad.runtime.ibkr_qualification_evidence._has_postgres_evidence_provenance",
        lambda _value: True,
    )

    capability = await verify_ibkr_qualification_evidence(
        path,
        live,
        restored,
        expectation=expectation,
        configuration=configuration,
    )

    assert has_verified_ibkr_capture_qualification_provenance(capability)
    assert capability.configuration_hash == configuration.configuration_hash
    assert capability.qualified_at == _GENERATED


@pytest.mark.asyncio
async def test_fixture_store_cannot_mint_production_authority(tmp_path: Path) -> None:
    configuration = _configuration()
    expectation = _expectation(configuration)
    live = _Store("qtrad_ibkr", configuration)
    restored = _Store("qtrad_ibkr_restore_verify_fixture", configuration)
    payload = await build_ibkr_qualification_snapshot(
        live,
        restored,
        expectation=expectation,
        configuration=configuration,
        window=_window(),
    )
    path = tmp_path / "qualification.json"
    write_qualification_artifact(path, payload)

    with pytest.raises(TypeError, match="exact PostgreSQL evidence stores"):
        await verify_ibkr_qualification_evidence(
            path,
            live,
            restored,
            expectation=expectation,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_restore_replay_rejects_changed_retained_callback() -> None:
    configuration = _configuration()
    with pytest.raises(ValueError, match="do not exactly replay"):
        await build_ibkr_qualification_snapshot(
            _Store("qtrad_ibkr", configuration),
            _Store("qtrad_ibkr_restore_verify_fixture", configuration, mutate_row=True),
            expectation=_expectation(configuration),
            configuration=configuration,
            window=_window(),
        )


@pytest.mark.asyncio
async def test_verifier_rejects_artifact_not_replayed_by_current_databases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = _configuration()
    expectation = _expectation(configuration)
    live = _Store("qtrad_ibkr", configuration)
    restored = _Store("qtrad_ibkr_restore_verify_fixture", configuration)
    payload = await build_ibkr_qualification_snapshot(
        live,
        restored,
        expectation=expectation,
        configuration=configuration,
        window=_window(),
    )
    path = tmp_path / "qualification.json"
    write_qualification_artifact(path, payload)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["evidence"]["retained_rows_sha256"] = "9" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        "qtrad.runtime.ibkr_qualification_evidence._has_postgres_evidence_provenance",
        lambda _value: True,
    )

    with pytest.raises(ValueError, match="artifact hash does not replay"):
        await verify_ibkr_qualification_evidence(
            path,
            live,
            restored,
            expectation=expectation,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_restore_replay_rejects_same_database_identity() -> None:
    configuration = _configuration()
    with pytest.raises(ValueError, match="restore-verify database"):
        await build_ibkr_qualification_snapshot(
            _Store("qtrad_ibkr", configuration),
            _Store("qtrad_ibkr", configuration),
            expectation=_expectation(configuration),
            configuration=configuration,
            window=_window(),
        )


@pytest.mark.asyncio
async def test_replayed_evidence_reports_delayed_market_data() -> None:
    configuration = _configuration()
    snapshot = await build_ibkr_qualification_snapshot(
        _Store("qtrad_ibkr", configuration, market_data_type=3),
        _Store("qtrad_ibkr_restore_verify_fixture", configuration, market_data_type=3),
        expectation=_expectation(configuration),
        configuration=configuration,
        window=_window(),
    )
    assert snapshot["result"] == "NOT_QUALIFIED"
    assert snapshot["reason_codes"] == ["LIVE_EVIDENCE_INCOMPLETE"]


@pytest.mark.asyncio
async def test_replayed_evidence_reports_persistence_failure() -> None:
    configuration = _configuration()
    snapshot = await build_ibkr_qualification_snapshot(
        _Store("qtrad_ibkr", configuration, metrics_failed=1),
        _Store("qtrad_ibkr_restore_verify_fixture", configuration, metrics_failed=1),
        expectation=_expectation(configuration),
        configuration=configuration,
        window=_window(),
    )
    assert snapshot["reason_codes"] == ["PERSISTENCE_RECONCILIATION_FAILED"]


@pytest.mark.asyncio
async def test_replayed_evidence_reports_uncontrolled_reconnect() -> None:
    configuration = _configuration()
    snapshot = await build_ibkr_qualification_snapshot(
        _Store("qtrad_ibkr", configuration, reconnect_completed=False),
        _Store(
            "qtrad_ibkr_restore_verify_fixture",
            configuration,
            reconnect_completed=False,
        ),
        expectation=_expectation(configuration),
        configuration=configuration,
        window=_window(),
    )
    assert snapshot["reason_codes"] == ["RECONNECT_EVIDENCE_INCOMPLETE"]
