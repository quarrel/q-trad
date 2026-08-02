import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from qtrad.application import ibkr_historical as historical
from qtrad.application.ibkr_historical import build_ibkr_contract_selection, build_ibkr_runtime_lock
from qtrad.domain.ibkr_historical import IbkrContractDecision
from qtrad.domain.identifiers import InstrumentId
from qtrad.ports.ibkr_capability import IbkrContractQuery
from qtrad.runtime.ibkr_historical import (
    load_ibkr_contract_selection,
    load_ibkr_runtime_lock,
    verify_ibkr_contract_selection,
    verify_ibkr_runtime_lock,
    write_ibkr_contract_selection,
    write_ibkr_runtime_lock,
)

_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
_QUERY = IbkrContractQuery(InstrumentId("fx:eur-usd"), "EUR", "CASH", "IDEALPRO", "USD", "EUR.USD")


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
        "catalogue_name": "fixture-candidates",
        "catalogue_hash": "a" * 64,
        "probe_spec_name": "fixture-probe",
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


def _operator(review: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "capability_review_sha256": review["review_hash"],
        "decisions": [
            {
                "instrument_id": "fx:eur-usd",
                "decision": IbkrContractDecision.ACCEPTED_EXACT_CONTRACT.value,
                "acquisition_eligible": True,
                "fingerprint": {
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
                },
            }
        ],
    }


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    catalogue = tmp_path / "catalogue.toml"
    catalogue.write_text(
        'name = "fixture-candidates"\n\n'
        "[[instrument]]\n"
        'id = "fx:eur-usd"\n'
        'display_name = "EUR/USD"\n'
        'asset_class = "FX"\n'
        'base_currency = "EUR"\n'
        'quote_currency = "USD"\n'
        'search_aliases = ["EUR/USD"]\n'
    )
    probe = tmp_path / "probe.toml"
    probe.write_text(
        'schema_version = 1\nname = "fixture-probe"\n\n'
        "[[query]]\n"
        'instrument_id = "fx:eur-usd"\n'
        'symbol = "EUR"\n'
        'security_type = "CASH"\n'
        'exchange = "IDEALPRO"\n'
        'currency = "USD"\n'
        'local_symbol = "EUR.USD"\n'
    )
    return catalogue, probe


def _selection(review: dict[str, object]):
    return build_ibkr_contract_selection(
        capability_review=review,
        operator_selection=_operator(review),
        canonical_instrument_ids=frozenset({_QUERY.instrument_id}),
        canonical_queries=frozenset({_QUERY}),
        frozen_by="operator@example.invalid",
        frozen_at=_NOW,
    )


def test_contract_selection_reconstructs_canonical_closure_and_replays(tmp_path: Path) -> None:
    catalogue, probe = _sources(tmp_path)
    from qtrad.runtime.ibkr_capability import load_ibkr_capability_probe_spec
    from qtrad.runtime.universe import load_capture_candidates

    review = _review()
    candidates, spec = load_capture_candidates(catalogue), load_ibkr_capability_probe_spec(probe)
    review["catalogue_hash"] = candidates.configuration_hash
    review["probe_spec_hash"] = spec.configuration_hash
    unsigned = {key: value for key, value in review.items() if key != "review_hash"}
    review["review_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    selection = _selection(review)
    path, review_path = tmp_path / "selection.json", tmp_path / "review.json"
    write_ibkr_contract_selection(path, selection)
    review_path.write_text(json.dumps(review))
    assert load_ibkr_contract_selection(path) == selection
    assert (
        verify_ibkr_contract_selection(
            path,
            capability_review_path=review_path,
            catalogue_path=catalogue,
            probe_spec_path=probe,
        )
        == selection
    )
    with pytest.raises(ValueError, match="canonical instrument closure"):
        build_ibkr_contract_selection(
            capability_review=review,
            operator_selection=_operator(review),
            canonical_instrument_ids=frozenset({_QUERY.instrument_id, InstrumentId("fx:aud-usd")}),
            canonical_queries=frozenset({_QUERY}),
            frozen_by="operator",
            frozen_at=_NOW,
        )


def test_contract_selection_verifier_requires_replay_inputs() -> None:
    import inspect

    signature = inspect.signature(verify_ibkr_contract_selection)
    assert signature.parameters["capability_review_path"].default is inspect.Parameter.empty


def test_runtime_lock_replays_actual_environment_and_authoritative_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
    archives = []
    for name in ("gateway.zip", "api.zip", "ibc.zip"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        archives.append(path)
    image = "sha256:" + "b" * 64
    runtime = build_ibkr_runtime_lock(
        gateway_archive=archives[0],
        api_archive=archives[1],
        ibc_archive=archives[2],
        gateway_version="10.49",
        api_version="10.50",
        ibc_version="3.24.1",
        qtrad_image_digest=image,
        frozen_at=_NOW,
    )
    path = tmp_path / "runtime-lock.json"
    write_ibkr_runtime_lock(path, runtime)
    hashes = [hashlib.sha256(item.read_bytes()).hexdigest() for item in archives]
    assert load_ibkr_runtime_lock(path) == runtime
    assert (
        verify_ibkr_runtime_lock(
            path,
            gateway_archive=archives[0],
            api_archive=archives[1],
            ibc_archive=archives[2],
            expected_gateway_sha256=hashes[0],
            expected_api_sha256=hashes[1],
            expected_ibc_sha256=hashes[2],
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )
        == runtime
    )
    copied = tmp_path / "elsewhere"
    copied.mkdir()
    copies = []
    for item in archives:
        target = copied / item.name
        target.write_bytes(item.read_bytes())
        copies.append(target)
    assert (
        verify_ibkr_runtime_lock(
            path,
            gateway_archive=copies[0],
            api_archive=copies[1],
            ibc_archive=copies[2],
            expected_gateway_sha256=hashes[0],
            expected_api_sha256=hashes[1],
            expected_ibc_sha256=hashes[2],
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )
        == runtime
    )
    with pytest.raises(ValueError, match="authoritative role attestation"):
        verify_ibkr_runtime_lock(
            path,
            gateway_archive=archives[0],
            api_archive=archives[1],
            ibc_archive=archives[2],
            expected_gateway_sha256="0" * 64,
            expected_api_sha256=hashes[1],
            expected_ibc_sha256=hashes[2],
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )


def test_runtime_lock_rejects_duplicate_archive_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    with pytest.raises(ValueError, match="distinct filenames"):
        build_ibkr_runtime_lock(
            gateway_archive=archive,
            api_archive=archive,
            ibc_archive=archive,
            gateway_version="10.49",
            api_version="10.50",
            ibc_version="3.24.1",
            qtrad_image_digest="sha256:" + "b" * 64,
            frozen_at=_NOW,
        )


def _rehash_runtime_payload(payload: dict[str, object]) -> None:
    unsigned = {key: value for key, value in payload.items() if key != "runtime_sha256"}
    payload["runtime_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_runtime_lock_rejects_rehashed_untrusted_runtime_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
    archives = []
    for name in ("gateway.zip", "api.zip", "ibc.zip"):
        archive = tmp_path / name
        archive.write_bytes(name.encode())
        archives.append(archive)
    image = "sha256:" + "b" * 64
    runtime = build_ibkr_runtime_lock(
        gateway_archive=archives[0],
        api_archive=archives[1],
        ibc_archive=archives[2],
        gateway_version="10.49",
        api_version="10.50",
        ibc_version="3.24.1",
        qtrad_image_digest=image,
        frozen_at=_NOW,
    )
    path = tmp_path / "runtime-lock.json"
    write_ibkr_runtime_lock(path, runtime)
    hashes = [hashlib.sha256(item.read_bytes()).hexdigest() for item in archives]
    payload = json.loads(path.read_text())
    payload["gateway_version"] = "99.99"
    _rehash_runtime_payload(payload)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="does not replay"):
        verify_ibkr_runtime_lock(
            path,
            gateway_archive=archives[0],
            api_archive=archives[1],
            ibc_archive=archives[2],
            expected_gateway_sha256=hashes[0],
            expected_api_sha256=hashes[1],
            expected_ibc_sha256=hashes[2],
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )


def test_runtime_lock_rejects_rehashed_untrusted_gateway_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
    archives = []
    for name in ("gateway.zip", "api.zip", "ibc.zip"):
        archive = tmp_path / name
        archive.write_bytes(name.encode())
        archives.append(archive)
    image = "sha256:" + "b" * 64
    runtime = build_ibkr_runtime_lock(
        gateway_archive=archives[0],
        api_archive=archives[1],
        ibc_archive=archives[2],
        gateway_version="10.49",
        api_version="10.50",
        ibc_version="3.24.1",
        qtrad_image_digest=image,
        frozen_at=_NOW,
    )
    path = tmp_path / "runtime-lock.json"
    write_ibkr_runtime_lock(path, runtime)
    hashes = [hashlib.sha256(item.read_bytes()).hexdigest() for item in archives]
    payload = json.loads(path.read_text())
    payload["api_host"] = "untrusted.example.invalid"
    payload["gateway_configuration_identity"] = historical._gateway_configuration_identity(
        api_host="untrusted.example.invalid",
        api_port=4002,
        client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
    )
    _rehash_runtime_payload(payload)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="does not replay"):
        verify_ibkr_runtime_lock(
            path,
            gateway_archive=archives[0],
            api_archive=archives[1],
            ibc_archive=archives[2],
            expected_gateway_sha256=hashes[0],
            expected_api_sha256=hashes[1],
            expected_ibc_sha256=hashes[2],
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )


def test_contract_selection_restores_create_only_field_and_symlink_mutations(
    tmp_path: Path,
) -> None:
    review = _review()
    selection = _selection(review)
    path = tmp_path / "selection.json"
    write_ibkr_contract_selection(path, selection)
    with pytest.raises(FileExistsError):
        write_ibkr_contract_selection(path, selection)
    payload = json.loads(path.read_text())
    payload["unexpected"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unknown or missing fields"):
        load_ibkr_contract_selection(path)
    path.unlink()
    target = tmp_path / "selection-target.json"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        load_ibkr_contract_selection(path)


def test_contract_selection_restores_duplicate_and_substitution_rejection() -> None:
    review = _review()
    operator = _operator(review)
    decisions = operator["decisions"]
    assert isinstance(decisions, list)
    decision = cast(dict[str, object], decisions[0])
    duplicate = {**operator, "decisions": [decision, decision]}
    with pytest.raises(ValueError, match="repeats instrument"):
        build_ibkr_contract_selection(
            capability_review=review,
            operator_selection=duplicate,
            canonical_instrument_ids=frozenset({_QUERY.instrument_id}),
            canonical_queries=frozenset({_QUERY}),
            frozen_by="operator",
            frozen_at=_NOW,
        )
    fingerprint_value = decision["fingerprint"]
    assert isinstance(fingerprint_value, dict)
    fingerprint = dict(fingerprint_value)
    fingerprint["con_id"] = 999
    substituted = {**operator, "decisions": [{**decision, "fingerprint": fingerprint}]}
    with pytest.raises(ValueError, match="exact capability-review match"):
        build_ibkr_contract_selection(
            capability_review=review,
            operator_selection=substituted,
            canonical_instrument_ids=frozenset({_QUERY.instrument_id}),
            canonical_queries=frozenset({_QUERY}),
            frozen_by="operator",
            frozen_at=_NOW,
        )


def test_contract_selection_rejects_symlinked_and_oversized_canonical_inputs(
    tmp_path: Path,
) -> None:
    catalogue, probe = _sources(tmp_path)
    from qtrad.runtime.ibkr_capability import load_ibkr_capability_probe_spec
    from qtrad.runtime.universe import load_capture_candidates

    review = _review()
    review["catalogue_hash"] = load_capture_candidates(catalogue).configuration_hash
    review["probe_spec_hash"] = load_ibkr_capability_probe_spec(probe).configuration_hash
    unsigned = {key: value for key, value in review.items() if key != "review_hash"}
    review["review_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    selection = _selection(review)
    selection_path = tmp_path / "selection.json"
    review_path = tmp_path / "review.json"
    write_ibkr_contract_selection(selection_path, selection)
    review_path.write_text(json.dumps(review))
    linked_catalogue = tmp_path / "linked-catalogue.toml"
    linked_catalogue.symlink_to(catalogue)
    with pytest.raises(ValueError, match="symlink"):
        verify_ibkr_contract_selection(
            selection_path,
            capability_review_path=review_path,
            catalogue_path=linked_catalogue,
            probe_spec_path=probe,
        )
    with pytest.raises(ValueError, match="escapes"):
        verify_ibkr_contract_selection(
            selection_path,
            capability_review_path=review_path,
            catalogue_path=tmp_path / "nested" / ".." / catalogue.name,
            probe_spec_path=probe,
        )
    oversized_probe = tmp_path / "oversized-probe.toml"
    oversized_probe.write_bytes(b"#" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="bounded size"):
        verify_ibkr_contract_selection(
            selection_path,
            capability_review_path=review_path,
            catalogue_path=catalogue,
            probe_spec_path=oversized_probe,
        )


def test_runtime_lock_rejects_archive_mutation_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
    archives = []
    for name in ("gateway.zip", "api.zip", "ibc.zip"):
        archive = tmp_path / name
        archive.write_bytes(name.encode())
        archives.append(archive)
    image = "sha256:" + "b" * 64
    runtime = build_ibkr_runtime_lock(
        gateway_archive=archives[0],
        api_archive=archives[1],
        ibc_archive=archives[2],
        gateway_version="10.49",
        api_version="10.50",
        ibc_version="3.24.1",
        qtrad_image_digest=image,
        frozen_at=_NOW,
    )
    path = tmp_path / "runtime-lock.json"
    write_ibkr_runtime_lock(path, runtime)
    hashes = [hashlib.sha256(item.read_bytes()).hexdigest() for item in archives]
    archives[1].write_bytes(b"mutated")
    with pytest.raises(ValueError, match="authoritative role attestation"):
        verify_ibkr_runtime_lock(
            path,
            gateway_archive=archives[0],
            api_archive=archives[1],
            ibc_archive=archives[2],
            expected_gateway_sha256=hashes[0],
            expected_api_sha256=hashes[1],
            expected_ibc_sha256=hashes[2],
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )
