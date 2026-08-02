import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from qtrad.application.ibkr_historical import build_ibkr_contract_selection, build_ibkr_runtime_lock
from qtrad.domain.ibkr_historical import IbkrContractDecision
from qtrad.runtime.ibkr_historical import (
    load_ibkr_contract_selection,
    load_ibkr_runtime_lock,
    verify_ibkr_contract_selection,
    verify_ibkr_runtime_lock,
    write_ibkr_contract_selection,
    write_ibkr_runtime_lock,
)

_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _review() -> dict[str, object]:
    contract = {
        "con_id": 12087792,
        "symbol": "EUR",
        "local_symbol": "EUR.USD",
        "security_type": "CASH",
        "exchange": "IDEALPRO",
        "currency": "USD",
        "trading_class": None,
        "multiplier": None,
        "minimum_tick": "0.00005",
        "market_rule_ids": ["26"],
        "valid_exchanges": ["IDEALPRO"],
        "long_name": "EUR.USD",
        "underlier_con_id": None,
        "timezone": "US/Eastern",
        "trading_hours": "20260802:1700-1700",
        "liquid_hours": "20260802:1700-1700",
        "primary_exchange": None,
        "contract_month": None,
    }
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "provider": "ibkr",
        "environment": "paper",
        "catalogue_name": "capture-ibkr-v1-candidates",
        "catalogue_hash": "a" * 64,
        "probe_spec_name": "operator-probe-v1",
        "probe_spec_hash": "b" * 64,
        "api": {"version": "10.49", "package_fingerprint": "c" * 64},
        "observed_at": "2026-08-02T11:00:00Z",
        "selection_authority": False,
        "external_io_performed": True,
        "instruments": [
            {
                "instrument_id": "fx:eur-usd",
                "display_name": "EUR/USD",
                "status": "OPERATOR_SELECTION_REQUIRED",
                "returned_contract_count": 1,
                "queries": [
                    {
                        "query": {
                            "instrument_id": "fx:eur-usd",
                            "symbol": "EUR",
                            "security_type": "CASH",
                            "exchange": "IDEALPRO",
                            "currency": "USD",
                            "local_symbol": "EUR.USD",
                            "trading_class": None,
                            "multiplier": None,
                        },
                        "contracts": [contract],
                        "requests": [],
                    }
                ],
            }
        ],
    }
    return {
        **unsigned,
        "review_hash": hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _fingerprint() -> dict[str, object]:
    return {
        "con_id": 12087792,
        "symbol": "EUR",
        "security_type": "CASH",
        "currency": "USD",
        "exchange": "IDEALPRO",
        "primary_exchange": None,
        "local_symbol": "EUR.USD",
        "trading_class": None,
        "multiplier": None,
        "underlying_con_id": None,
        "contract_month": None,
    }


def _operator_selection(review: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "capability_review_sha256": review["review_hash"],
        "decisions": [
            {
                "instrument_id": "fx:eur-usd",
                "decision": IbkrContractDecision.ACCEPTED_EXACT_CONTRACT.value,
                "acquisition_eligible": True,
                "fingerprint": _fingerprint(),
            }
        ],
    }


def test_contract_selection_reconstructs_review_and_replays_from_files(tmp_path: Path) -> None:
    review = _review()
    selection = build_ibkr_contract_selection(
        capability_review=review,
        operator_selection=_operator_selection(review),
        frozen_by="operator@example.invalid",
        frozen_at=_NOW,
    )
    path = tmp_path / "selection.json"
    write_ibkr_contract_selection(path, selection)

    assert load_ibkr_contract_selection(path) == selection
    review_path = _write_json(tmp_path / "review.json", review)
    assert verify_ibkr_contract_selection(path, capability_review_path=review_path) == selection
    with pytest.raises(FileExistsError):
        write_ibkr_contract_selection(path, selection)


def test_contract_selection_rejects_missing_duplicate_and_substituted_decisions() -> None:
    review = _review()
    operator = _operator_selection(review)
    missing = {**operator, "decisions": []}
    with pytest.raises(ValueError, match="requires decisions"):
        build_ibkr_contract_selection(
            capability_review=review,
            operator_selection=missing,
            frozen_by="operator",
            frozen_at=_NOW,
        )

    decision_values = cast(list[dict[str, object]], operator["decisions"])
    duplicate = {**operator, "decisions": decision_values * 2}
    with pytest.raises(ValueError, match="repeats instrument"):
        build_ibkr_contract_selection(
            capability_review=review,
            operator_selection=duplicate,
            frozen_by="operator",
            frozen_at=_NOW,
        )

    substituted = _operator_selection(review)
    fingerprint = dict(_fingerprint())
    fingerprint["con_id"] = 999
    substituted_decisions = cast(list[dict[str, object]], substituted["decisions"])
    substituted["decisions"] = [{**substituted_decisions[0], "fingerprint": fingerprint}]
    with pytest.raises(ValueError, match="exact capability-review match"):
        build_ibkr_contract_selection(
            capability_review=review,
            operator_selection=substituted,
            frozen_by="operator",
            frozen_at=_NOW,
        )


def test_contract_selection_loader_rejects_unknown_fields_and_symlink(tmp_path: Path) -> None:
    review = _review()
    selection = build_ibkr_contract_selection(
        capability_review=review,
        operator_selection=_operator_selection(review),
        frozen_by="operator",
        frozen_at=_NOW,
    )
    path = tmp_path / "selection.json"
    write_ibkr_contract_selection(path, selection)
    payload = json.loads(path.read_text())
    payload["unexpected"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unknown or missing fields"):
        load_ibkr_contract_selection(path)

    path.unlink()
    path.symlink_to(tmp_path / "target.json")
    (tmp_path / "target.json").write_text("{}")
    with pytest.raises(ValueError, match="symlink"):
        load_ibkr_contract_selection(path)


def test_runtime_lock_rehashes_archives_and_is_create_only(tmp_path: Path) -> None:
    archives = []
    for name in ("gateway.zip", "api.zip", "ibc.zip"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        archives.append(path)
    runtime = build_ibkr_runtime_lock(
        gateway_archive=archives[0],
        api_archive=archives[1],
        ibc_archive=archives[2],
        gateway_version="10.49",
        api_version="10.49",
        ibc_version="3.24.1",
        qtrad_commit="a" * 40,
        qtrad_image_digest="sha256:" + "b" * 64,
        frozen_at=_NOW,
        library_versions={"python": "3.13"},
    )
    path = tmp_path / "runtime-lock.json"
    write_ibkr_runtime_lock(path, runtime)
    assert load_ibkr_runtime_lock(path) == runtime
    assert verify_ibkr_runtime_lock(path) == runtime
    with pytest.raises(FileExistsError):
        write_ibkr_runtime_lock(path, runtime)

    archives[1].write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_ibkr_runtime_lock(path)


def test_runtime_lock_rejects_archive_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.zip"
    real.write_bytes(b"archive")
    link = tmp_path / "link.zip"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        build_ibkr_runtime_lock(
            gateway_archive=link,
            api_archive=real,
            ibc_archive=real,
            gateway_version="10.49",
            api_version="10.49",
            ibc_version="3.24.1",
            qtrad_commit="a" * 40,
            qtrad_image_digest="sha256:" + "b" * 64,
            frozen_at=_NOW,
            library_versions={"python": "3.13"},
        )


def _write_json(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return path
