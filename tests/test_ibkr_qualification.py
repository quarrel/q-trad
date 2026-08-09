from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_qualification import (
    IbkrQualificationStage,
    IbkrQualifiedContract,
    VerifiedIbkrCaptureQualification,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.runtime.ibkr_qualification import (
    IbkrQualificationExpectation,
    seal_qualification_artifact,
    verify_ibkr_capture_qualification,
    write_qualification_artifact,
)

_NOW = datetime(2026, 8, 10, 1, 5, tzinfo=UTC)
_IDS = frozenset((InstrumentId("fx:aud-usd"), InstrumentId("index:australia-200")))


def _contracts() -> tuple[IbkrQualifiedContract, ...]:
    return tuple(
        IbkrQualifiedContract(
            instrument_id=instrument_id,
            listing_id=ProviderListingId("ibkr", "IBKR_PAPER", str(instrument_id).split(":", 1)[1]),
            con_id=1 if str(instrument_id).startswith("fx:") else 2,
        )
        for instrument_id in sorted(_IDS, key=str)
    )


def _expectation() -> IbkrQualificationExpectation:
    return IbkrQualificationExpectation(
        stage=IbkrQualificationStage.B3_EXACT_TWO,
        release_contract="qtrad-ibkr-native-release-v1",
        release_sha256="1" * 64,
        configuration_hash="2" * 64,
        capture_source_id="ibkr-paper-v1",
        universe_id="capture-ibkr-v1",
        instruments=_IDS,
        contracts=_contracts(),
        application_commit="3" * 40,
        image_digest="ghcr.io/quarrel/qtrad@sha256:" + "4" * 64,
        api_package_sha256="5" * 64,
        gateway_archive_sha256="6" * 64,
        gateway_version="10.49",
        ibc_version="3.23.0",
        database_name="qtrad_ibkr",
        schema_head="0014",
    )


def _instrument(instrument_id: str) -> dict[str, JsonValue]:
    contract = next(item for item in _contracts() if str(item.instrument_id) == instrument_id)
    return {
        "instrument_id": instrument_id,
        "listing_id": str(contract.listing_id),
        "con_id": contract.con_id,
        "market_activity": "ACTIVE",
        "market_data_type": "LIVE",
        "active_started_at": "2026-08-10T00:00:00+00:00",
        "active_ended_at": "2026-08-10T01:00:00+00:00",
        "bid_count": 2,
        "ask_count": 2,
        "first_bid_at": "2026-08-10T00:00:01+00:00",
        "recent_bid_at": "2026-08-10T00:59:30+00:00",
        "first_ask_at": "2026-08-10T00:00:02+00:00",
        "recent_ask_at": "2026-08-10T00:59:31+00:00",
    }


def _payload() -> dict[str, JsonValue]:
    ids = sorted(str(item) for item in _IDS)
    return cast(
        dict[str, JsonValue],
        {
            "contract": "qtrad-ibkr-native-qualification-v1",
            "stage": "B3_EXACT_TWO",
            "generated_at": _NOW.isoformat(),
            "result": "QUALIFIED",
            "reason_codes": [],
            "release": {
                "contract": "qtrad-ibkr-native-release-v1",
                "artifact_sha256": "1" * 64,
                "configuration_hash": "2" * 64,
                "capture_source_id": "ibkr-paper-v1",
                "universe_id": "capture-ibkr-v1",
            },
            "runtime": {
                "application_commit": "3" * 40,
                "image_digest": "ghcr.io/quarrel/qtrad@sha256:" + "4" * 64,
                "api_package_sha256": "5" * 64,
                "gateway_archive_sha256": "6" * 64,
                "gateway_version": "10.49",
                "ibc_version": "3.23.0",
                "database_name": "qtrad_ibkr",
                "schema_head": "0014",
            },
            "window": {
                "started_at": "2026-08-10T00:00:00+00:00",
                "ended_at": "2026-08-10T01:00:00+00:00",
            },
            "capture_session_ids": ["00000000-0000-0000-0000-000000000001"],
            "connection_generations": [1, 2],
            "subscriptions": {"configured": ids, "desired": ids, "active": ids},
            "instruments": [_instrument(item) for item in ids],
            "persistence": {
                "records_received": 8,
                "persisted": 8,
                "failed": 0,
                "dropped": 0,
                "reconciliation_loss": 0,
            },
            "health": {"status": "HEALTHY", "observed_at": "2026-08-10T01:00:00+00:00"},
            "reconnect": {
                "from_generation": 1,
                "to_generation": 2,
                "expected_subscriptions": ids,
                "reconstructed_subscriptions": ids,
                "fresh_instruments": ids,
                "duplicate_subscriptions": 0,
                "stale_generation_callbacks": 0,
            },
            "backup_restore": {
                "verified": True,
                "configuration_hash": "2" * 64,
                "observed_at": "2026-08-10T01:04:00+00:00",
            },
        },
    )


def _write(path: Path, payload: dict[str, JsonValue]) -> None:
    write_qualification_artifact(path, payload)


def test_rehashed_self_attested_qualification_cannot_yield_runtime_capability(
    tmp_path: Path,
) -> None:
    path = tmp_path / "qualification.json"
    _write(path, _payload())

    with pytest.raises(ValueError, match="independently replayable live evidence"):
        verify_ibkr_capture_qualification(path, _expectation())


def test_verified_qualification_rejects_ordinary_construction() -> None:
    with pytest.raises(TypeError, match="evidence-replaying verifier"):
        VerifiedIbkrCaptureQualification()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__("contract", "wrong"), "contract marker"),
        (lambda value: value.__setitem__("result", "NOT_QUALIFIED"), "not QUALIFIED"),
        (
            lambda value: cast(dict[str, JsonValue], value["runtime"]).__setitem__(
                "image_digest", "ghcr.io/quarrel/qtrad@sha256:" + "9" * 64
            ),
            "image_digest identity mismatch",
        ),
        (
            lambda value: cast(list[dict[str, JsonValue]], value["instruments"])[0].__setitem__(
                "con_id", 999
            ),
            "contract identity is not exact",
        ),
        (
            lambda value: cast(list[dict[str, JsonValue]], value["instruments"])[0].__setitem__(
                "listing_id", "ibkr:IBKR_PAPER:tampered"
            ),
            "contract identity is not exact",
        ),
        (
            lambda value: cast(dict[str, JsonValue], value["subscriptions"]).__setitem__(
                "active", ["fx:aud-usd"]
            ),
            "active subscriptions are not exact",
        ),
        (
            lambda value: cast(list[dict[str, JsonValue]], value["instruments"])[0].__setitem__(
                "market_data_type", "DELAYED"
            ),
            "did not produce LIVE",
        ),
        (
            lambda value: cast(list[dict[str, JsonValue]], value["instruments"])[0].__setitem__(
                "ask_count", 0
            ),
            "lacks ask evidence",
        ),
        (
            lambda value: cast(list[dict[str, JsonValue]], value["instruments"])[0].__setitem__(
                "recent_bid_at", "2026-08-10T00:50:00+00:00"
            ),
            "bid evidence was stale",
        ),
        (
            lambda value: cast(dict[str, JsonValue], value["persistence"]).__setitem__(
                "dropped", 1
            ),
            "dropped is non-zero",
        ),
        (
            lambda value: cast(dict[str, JsonValue], value["persistence"]).__setitem__(
                "reconciliation_loss", 1
            ),
            "reconciliation_loss is non-zero",
        ),
        (
            lambda value: cast(dict[str, JsonValue], value["health"]).__setitem__(
                "status", "DEGRADED"
            ),
            "health is not HEALTHY",
        ),
        (
            lambda value: cast(dict[str, JsonValue], value["reconnect"]).__setitem__(
                "reconstructed_subscriptions", ["fx:aud-usd"]
            ),
            "reconstructed_subscriptions is not exact",
        ),
        (
            lambda value: cast(dict[str, JsonValue], value["reconnect"]).__setitem__(
                "stale_generation_callbacks", 1
            ),
            "stale-generation callbacks",
        ),
        (
            lambda value: cast(dict[str, JsonValue], value["backup_restore"]).__setitem__(
                "verified", False
            ),
            "backup/restore is not verified",
        ),
    ],
)
def test_qualification_verifier_rejects_adversarial_evidence(
    tmp_path: Path,
    mutation: Callable[[dict[str, JsonValue]], None],
    message: str,
) -> None:
    payload = deepcopy(_payload())
    mutation(payload)
    path = tmp_path / "qualification.json"
    _write(path, payload)

    with pytest.raises(ValueError, match=message):
        verify_ibkr_capture_qualification(path, _expectation())


def test_qualification_verifier_rejects_rehashed_release_identity_mismatch(tmp_path: Path) -> None:
    payload = _payload()
    release = cast(dict[str, JsonValue], payload["release"])
    release["configuration_hash"] = "8" * 64
    path = tmp_path / "qualification.json"
    _write(path, payload)

    with pytest.raises(ValueError, match="configuration_hash identity mismatch"):
        verify_ibkr_capture_qualification(path, _expectation())


def test_qualification_artifact_hash_detects_post_publication_tamper(tmp_path: Path) -> None:
    path = tmp_path / "qualification.json"
    _write(path, _payload())
    document = cast(dict[str, JsonValue], json.loads(path.read_text(encoding="utf-8")))
    cast(dict[str, JsonValue], document["health"])["status"] = "DEGRADED"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact hash does not replay"):
        verify_ibkr_capture_qualification(path, _expectation())


def test_qualification_publication_is_create_only(tmp_path: Path) -> None:
    path = tmp_path / "qualification.json"
    _write(path, _payload())

    with pytest.raises(FileExistsError):
        _write(path, _payload())


def test_sealing_rejects_an_existing_artifact_hash() -> None:
    payload = _payload()
    payload["artifact_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="must omit"):
        seal_qualification_artifact(payload)
