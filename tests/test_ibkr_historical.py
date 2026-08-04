import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qtrad.application import ibkr_historical as historical
from qtrad.application.ibkr_historical import (
    build_ibkr_contract_selection,
    build_ibkr_historical_plan,
    build_ibkr_historical_request_profile,
    build_ibkr_runtime_lock,
)
from qtrad.domain.ibkr_historical import (
    IbkrAcquisitionRuntime,
    IbkrArchiveIdentity,
    IbkrContractDecision,
    IbkrHistoricalPacingPolicy,
    IbkrHistoricalRequestKind,
    ibkr_end_date_time,
    sha256_json,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.ports.ibkr_capability import IbkrContractQuery
from qtrad.runtime.ibkr_historical import (
    build_ibkr_historical_plan_from_files,
    load_ibkr_contract_selection,
    load_ibkr_historical_plan,
    load_ibkr_historical_plan_bytes,
    load_ibkr_historical_request_profile,
    load_ibkr_runtime_lock,
    verify_ibkr_contract_selection,
    verify_ibkr_historical_plan,
    verify_ibkr_historical_request_profile,
    verify_ibkr_runtime_lock,
    write_ibkr_contract_selection,
    write_ibkr_historical_plan,
    write_ibkr_historical_request_profile,
    write_ibkr_runtime_lock,
)

_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
_CANARY_BYTES = b"verified-canary-evidence"
_CANARY_HASH = hashlib.sha256(_CANARY_BYTES).hexdigest()
_CANARY_FILE_HASH = hashlib.sha256(b"serialized-canary-file").hexdigest()
_CANARY_RUNTIME_HASH = "c" * 64
_CANARY_SELECTION_HASH = "d" * 64
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


def _closure_sources(tmp_path: Path) -> tuple[Path, Path]:
    catalogue = tmp_path / "catalogue.toml"
    catalogue.write_text(
        'name = "fixture-candidates"\n\n'
        "[[instrument]]\n"
        'id = "fx:eur-usd"\n'
        'display_name = "EUR/USD"\n'
        'asset_class = "FX"\n'
        'base_currency = "EUR"\n'
        'quote_currency = "USD"\n'
        'search_aliases = ["EUR/USD"]\n\n'
        "[[instrument]]\n"
        'id = "index:spx"\n'
        'display_name = "S&P 500"\n'
        'asset_class = "INDEX"\n'
        'base_currency = "USD"\n'
        'quote_currency = "USD"\n'
        'search_aliases = ["SPX"]\n\n'
        "[[instrument]]\n"
        'id = "commodity:gold"\n'
        'display_name = "Gold"\n'
        'asset_class = "COMMODITY"\n'
        'base_currency = "USD"\n'
        'quote_currency = "USD"\n'
        'search_aliases = ["GOLD"]\n'
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
        'local_symbol = "EUR.USD"\n\n'
        "[[query]]\n"
        'instrument_id = "index:spx"\n'
        'symbol = "SPX"\n'
        'security_type = "CFD"\n'
        'exchange = "IDEALPRO"\n'
        'currency = "USD"\n'
        'local_symbol = "SPX.USD"\n\n'
        "[[query]]\n"
        'instrument_id = "commodity:gold"\n'
        'symbol = "GOLD"\n'
        'security_type = "FUT"\n'
        'exchange = "IDEALPRO"\n'
        'currency = "USD"\n'
        'local_symbol = "GOLD.USD"\n'
    )
    return catalogue, probe


def _closure_review() -> dict[str, object]:
    review = _review()
    instruments = cast(list[dict[str, object]], review["instruments"])
    base_instrument = instruments[0]
    base_query = cast(list[dict[str, object]], base_instrument["queries"])[0]
    base_contract = cast(list[dict[str, object]], base_query["contracts"])[0]
    base_contract["con_id"] = 11
    base_contract["trading_class"] = "EUR.USD"
    representatives = (
        ("index:spx", "S&P 500", "SPX", "CFD", "IDEALPRO", "SPX.USD", 22),
        ("commodity:gold", "Gold", "GOLD", "FUT", "IDEALPRO", "GOLD.USD", 33),
    )
    for (
        instrument_id,
        display_name,
        symbol,
        security_type,
        exchange,
        local_symbol,
        con_id,
    ) in representatives:
        query = dict(cast(dict[str, object], base_query["query"]))
        query.update(
            instrument_id=instrument_id,
            symbol=symbol,
            security_type=security_type,
            exchange=exchange,
            local_symbol=local_symbol,
        )
        contract = dict(base_contract)
        contract.update(
            con_id=con_id,
            symbol=symbol,
            local_symbol=local_symbol,
            security_type=security_type,
            exchange=exchange,
            currency="USD",
            minimum_tick="0.01",
            market_rule_ids=[],
            valid_exchanges=[exchange],
            long_name=display_name,
            timezone="UTC",
            trading_class=f"{symbol}.USD",
            trading_hours="20260802:0000-0000",
            liquid_hours="20260802:0000-0000",
        )
        instruments.append(
            {
                "instrument_id": instrument_id,
                "display_name": display_name,
                "status": "OPERATOR_SELECTION_REQUIRED",
                "returned_contract_count": 1,
                "queries": [{"query": query, "contracts": [contract], "requests": []}],
            }
        )
    review["instruments"] = instruments
    return review


def _closure_operator(review: dict[str, object]) -> dict[str, object]:
    operator = _operator(review)
    decisions = cast(list[dict[str, object]], operator["decisions"])
    base_decision = decisions[0]
    base_fingerprint = dict(cast(dict[str, object], base_decision["fingerprint"]))
    base_fingerprint["con_id"] = 11
    base_fingerprint["trading_class"] = "EUR.USD"
    base_decision["fingerprint"] = base_fingerprint
    representatives = (
        ("index:spx", "SPX", "CFD", "IDEALPRO", "SPX.USD", 22),
        ("commodity:gold", "GOLD", "FUT", "IDEALPRO", "GOLD.USD", 33),
    )
    for instrument_id, symbol, security_type, exchange, local_symbol, con_id in representatives:
        decision = dict(base_decision)
        fingerprint = dict(base_decision["fingerprint"])
        fingerprint.update(
            con_id=con_id,
            symbol=symbol,
            local_symbol=local_symbol,
            security_type=security_type,
            exchange=exchange,
            trading_class=f"{symbol}.USD",
        )
        decision.update(instrument_id=instrument_id, fingerprint=fingerprint)
        decisions.append(decision)
    operator["decisions"] = decisions
    return operator


def _canonical_queries(review: dict[str, object]) -> frozenset[IbkrContractQuery]:
    instruments = cast(list[dict[str, object]], review["instruments"])
    return frozenset(
        IbkrContractQuery(
            InstrumentId(str(cast(dict[str, object], item["query"])["instrument_id"])),
            str(cast(dict[str, object], item["query"])["symbol"]),
            str(cast(dict[str, object], item["query"])["security_type"]),
            str(cast(dict[str, object], item["query"])["exchange"]),
            str(cast(dict[str, object], item["query"])["currency"]),
            str(cast(dict[str, object], item["query"])["local_symbol"]),
        )
        for instrument in instruments
        for item in cast(list[dict[str, object]], instrument["queries"])
    )


def _selection(review: dict[str, object], *, operator_selection: dict[str, object] | None = None):
    canonical_queries = _canonical_queries(review)
    return build_ibkr_contract_selection(
        capability_review=review,
        operator_selection=(
            operator_selection if operator_selection is not None else _operator(review)
        ),
        canonical_instrument_ids=frozenset(query.instrument_id for query in canonical_queries),
        canonical_queries=canonical_queries,
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
    operator_path = tmp_path / "operator.json"
    write_ibkr_contract_selection(path, selection)
    review_path.write_text(json.dumps(review))
    operator_path.write_text(json.dumps(_operator(review)))
    assert load_ibkr_contract_selection(path) == selection
    assert (
        verify_ibkr_contract_selection(
            path,
            capability_review_path=review_path,
            operator_selection_path=operator_path,
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
            expected_qtrad_commit="a" * 40,
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
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "c" * 40)
    with pytest.raises(ValueError, match="observed clean source commit"):
        verify_ibkr_runtime_lock(
            path,
            gateway_archive=archives[0],
            api_archive=archives[1],
            ibc_archive=archives[2],
            expected_gateway_sha256=hashes[0],
            expected_api_sha256=hashes[1],
            expected_ibc_sha256=hashes[2],
            expected_qtrad_commit="a" * 40,
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
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
            expected_qtrad_commit="a" * 40,
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
            expected_qtrad_commit="a" * 40,
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
            expected_qtrad_commit="a" * 40,
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
            expected_qtrad_commit="a" * 40,
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
    operator_path = tmp_path / "operator.json"
    write_ibkr_contract_selection(selection_path, selection)
    review_path.write_text(json.dumps(review))
    operator_path.write_text(json.dumps(_operator(review)))
    linked_catalogue = tmp_path / "linked-catalogue.toml"
    linked_catalogue.symlink_to(catalogue)
    with pytest.raises(ValueError, match="symlink"):
        verify_ibkr_contract_selection(
            selection_path,
            capability_review_path=review_path,
            operator_selection_path=operator_path,
            catalogue_path=linked_catalogue,
            probe_spec_path=probe,
        )
    with pytest.raises(ValueError, match="escapes"):
        verify_ibkr_contract_selection(
            selection_path,
            capability_review_path=review_path,
            operator_selection_path=operator_path,
            catalogue_path=tmp_path / "nested" / ".." / catalogue.name,
            probe_spec_path=probe,
        )
    oversized_probe = tmp_path / "oversized-probe.toml"
    oversized_probe.write_bytes(b"#" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="bounded size"):
        verify_ibkr_contract_selection(
            selection_path,
            capability_review_path=review_path,
            operator_selection_path=operator_path,
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
            expected_qtrad_commit="a" * 40,
            expected_image_digest=image,
            expected_gateway_version="10.49",
            expected_api_version="10.50",
            expected_ibc_version="3.24.1",
            expected_api_host="127.0.0.1",
            expected_api_port=4002,
            expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        )


def _profile(
    *, reverse_product_mapping: bool = False, fx_duration: str = "1 W", index_duration: str = "1 W"
):
    entries = [
        (AssetClass.FX, fx_duration),
        (AssetClass.INDEX, index_duration),
        (AssetClass.COMMODITY, "1 W"),
    ]
    if reverse_product_mapping:
        entries.reverse()
    return build_ibkr_historical_request_profile(
        canary_evidence_filename="canary.json",
        canary_evidence_sha256=_CANARY_HASH,
        canary_evidence_file_sha256=_CANARY_FILE_HASH,
        canary_runtime_sha256=_CANARY_RUNTIME_HASH,
        canary_selection_sha256=_CANARY_SELECTION_HASH,
        frozen_by="operator@example.invalid",
        frozen_at=_NOW,
        permitted_bar_durations=("1 D", "1 W", "2 W", "4 W"),
        permitted_schedule_durations=("1 D", "1 W", "2 W", "4 W"),
        bar_duration_by_asset_class=dict(entries),
        schedule_duration="1 W",
        maximum_in_flight_requests=2,
        request_timeout_seconds=60,
        retry_count=5,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds=15,
            per_contract_window_seconds=2,
            max_requests_per_contract_window=5,
            rolling_window_seconds=600,
            max_requests_per_rolling_window=55,
        ),
    )


def _planning_runtime() -> IbkrAcquisitionRuntime:
    gateway_archive = IbkrArchiveIdentity("gateway.zip", "a" * 64)
    api_archive = IbkrArchiveIdentity("api.zip", "b" * 64)
    ibc_archive = IbkrArchiveIdentity("ibc.zip", "c" * 64)
    identity = {
        "contract": IbkrAcquisitionRuntime.CONTRACT,
        "schema_version": IbkrAcquisitionRuntime.SCHEMA_VERSION,
        "gateway_version": "10.49",
        "gateway_archive": gateway_archive.as_json_value(),
        "api_version": "10.49",
        "api_archive": api_archive.as_json_value(),
        "ibc_version": "3.24.1",
        "ibc_archive": ibc_archive.as_json_value(),
        "qtrad_commit": "a" * 40,
        "qtrad_image_digest": "sha256:" + "d" * 64,
        "python_version": "3.13.0",
        "library_versions": {"pydantic": "2.11.0"},
        "gateway_configuration_identity": "e" * 64,
        "paper_account_environment": "paper",
        "api_host": "127.0.0.1",
        "api_port": 4002,
        "client_id_policy": "DEDICATED_NONZERO_CLIENT_ID",
        "frozen_at": "2026-08-02T12:00:00Z",
    }
    return IbkrAcquisitionRuntime(
        gateway_version="10.49",
        gateway_archive=gateway_archive,
        api_version="10.49",
        api_archive=api_archive,
        ibc_version="3.24.1",
        ibc_archive=ibc_archive,
        qtrad_commit="a" * 40,
        qtrad_image_digest="sha256:" + "d" * 64,
        python_version="3.13.0",
        library_versions={"pydantic": "2.11.0"},
        gateway_configuration_identity="e" * 64,
        paper_account_environment="paper",
        api_host="127.0.0.1",
        api_port=4002,
        client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        frozen_at=_NOW,
        runtime_sha256=sha256_json(identity),
    )


def _closure_canary_evidence(runtime_sha256: str, selection_sha256: str):
    from qtrad.application.ibkr_canary import IbkrHistoricalCanaryEvidence
    from qtrad.domain.ibkr_historical import IbkrHistoricalRequest
    from tests.test_ibkr_canary import _evidence as canary_fixture

    fixture = canary_fixture()

    def adapt_fingerprint(fingerprint):
        if fingerprint.symbol != "GOLD":
            return fingerprint
        return replace(fingerprint, security_type="FUT")

    def adapt_request(request):
        fingerprint = adapt_fingerprint(request.fingerprint)
        identity = dict(request.identity_payload())
        identity["fingerprint"] = fingerprint.as_json_value()
        return IbkrHistoricalRequest(
            instrument_id=request.instrument_id,
            fingerprint=fingerprint,
            kind=request.kind,
            interval_start=request.interval_start,
            interval_end=request.interval_end,
            end_date_time=request.end_date_time,
            duration=request.duration,
            bar_size=request.bar_size,
            what_to_show=request.what_to_show,
            use_rth=request.use_rth,
            format_date=request.format_date,
            keep_up_to_date=request.keep_up_to_date,
            request_sha256=sha256_json(identity),
        )

    cases = tuple(
        replace(
            result,
            case=replace(result.case, fingerprint=adapt_fingerprint(result.case.fingerprint)),
            requests=tuple(
                replace(request_result, request=adapt_request(request_result.request))
                for request_result in result.requests
            ),
        )
        for result in fixture.cases
    )
    reauthentication = tuple(
        replace(
            item,
            expected=adapt_fingerprint(item.expected),
            observed=tuple(adapt_fingerprint(value) for value in item.observed),
        )
        for item in fixture.reauthentication
    )
    identity = dict(fixture.identity_payload())
    identity["runtime_sha256"] = runtime_sha256
    identity["selection_sha256"] = selection_sha256
    identity["reauthentication"] = [item.as_json_value() for item in reauthentication]
    identity["cases"] = [item.as_json_value() for item in cases]
    return IbkrHistoricalCanaryEvidence(
        runtime_sha256=runtime_sha256,
        selection_sha256=selection_sha256,
        started_at=fixture.started_at,
        completed_at=fixture.completed_at,
        reauthentication=reauthentication,
        cases=cases,
        stop_reason=fixture.stop_reason,
        evidence_sha256=sha256_json(identity),
    )


def _write_plan_closure(tmp_path: Path) -> dict[str, Path]:
    from qtrad.application.ibkr_canary import (
        freeze_ibkr_request_profile_from_canary,
    )
    from qtrad.runtime.ibkr_canary import write_ibkr_historical_canary_evidence
    from qtrad.runtime.ibkr_capability import load_ibkr_capability_probe_spec
    from qtrad.runtime.universe import load_capture_candidates

    catalogue, probe = _closure_sources(tmp_path)
    review = _closure_review()
    candidates = load_capture_candidates(catalogue)
    probe_spec = load_ibkr_capability_probe_spec(probe)
    review["catalogue_hash"] = candidates.configuration_hash
    review["probe_spec_hash"] = probe_spec.configuration_hash
    unsigned = {key: value for key, value in review.items() if key != "review_hash"}
    review["review_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    paths = {
        "catalogue": catalogue,
        "probe": probe,
        "review": tmp_path / "review.json",
        "operator": tmp_path / "operator.json",
        "selection": tmp_path / "selection.json",
        "gateway_archive": tmp_path / "gateway.zip",
        "api_archive": tmp_path / "api.zip",
        "ibc_archive": tmp_path / "ibc.zip",
        "runtime": tmp_path / "runtime-lock.json",
        "canary": tmp_path / "canary.json",
        "profile": tmp_path / "profile.json",
    }
    paths["review"].write_text(json.dumps(review))
    operator = _closure_operator(review)
    paths["operator"].write_text(json.dumps(operator))
    selection = _selection(review, operator_selection=operator)
    write_ibkr_contract_selection(paths["selection"], selection)

    for archive_path in (
        paths["gateway_archive"],
        paths["api_archive"],
        paths["ibc_archive"],
    ):
        archive_path.write_bytes(archive_path.name.encode())
    runtime = historical._rebuild_ibkr_runtime_lock(
        gateway_archive=paths["gateway_archive"],
        api_archive=paths["api_archive"],
        ibc_archive=paths["ibc_archive"],
        gateway_version="10.49",
        api_version="10.49",
        ibc_version="3.24.1",
        qtrad_commit="a" * 40,
        qtrad_image_digest="sha256:" + "d" * 64,
        frozen_at=_NOW,
    )
    write_ibkr_runtime_lock(paths["runtime"], runtime)

    evidence = _closure_canary_evidence(runtime.runtime_sha256, selection.selection_sha256)
    write_ibkr_historical_canary_evidence(paths["canary"], evidence)
    canary_file_sha256 = hashlib.sha256(paths["canary"].read_bytes()).hexdigest()
    profile = freeze_ibkr_request_profile_from_canary(
        evidence,
        canary_evidence_filename=paths["canary"].name,
        canary_evidence_file_sha256=canary_file_sha256,
        frozen_by="operator@example.invalid",
        frozen_at=_NOW,
        maximum_in_flight_requests=2,
        request_timeout_seconds=60,
        retry_count=5,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds=15,
            per_contract_window_seconds=2,
            max_requests_per_contract_window=5,
            rolling_window_seconds=600,
            max_requests_per_rolling_window=55,
        ),
    )
    write_ibkr_historical_request_profile(paths["profile"], profile)
    return paths


def _build_plan_from_closure(paths: dict[str, Path], start: datetime, end: datetime):
    runtime = load_ibkr_runtime_lock(paths["runtime"])
    return build_ibkr_historical_plan_from_files(
        contract_selection_path=paths["selection"],
        operator_selection_path=paths["operator"],
        capability_review_path=paths["review"],
        catalogue_path=paths["catalogue"],
        probe_spec_path=paths["probe"],
        runtime_lock_path=paths["runtime"],
        gateway_archive=paths["gateway_archive"],
        api_archive=paths["api_archive"],
        ibc_archive=paths["ibc_archive"],
        expected_gateway_sha256=hashlib.sha256(paths["gateway_archive"].read_bytes()).hexdigest(),
        expected_api_sha256=hashlib.sha256(paths["api_archive"].read_bytes()).hexdigest(),
        expected_ibc_sha256=hashlib.sha256(paths["ibc_archive"].read_bytes()).hexdigest(),
        expected_runtime_qtrad_commit="a" * 40,
        expected_runtime_image_digest="sha256:" + "d" * 64,
        expected_gateway_version="10.49",
        expected_api_version="10.49",
        expected_ibc_version="3.24.1",
        expected_api_host="127.0.0.1",
        expected_api_port=4002,
        expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        request_profile_path=paths["profile"],
        canary_evidence_path=paths["canary"],
        expected_profile_frozen_by="operator@example.invalid",
        expected_profile_frozen_at=_NOW,
        maximum_in_flight_requests=2,
        request_timeout_seconds=60,
        retry_count=5,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds=15,
            per_contract_window_seconds=2,
            max_requests_per_contract_window=5,
            rolling_window_seconds=600,
            max_requests_per_rolling_window=55,
        ),
        start=start,
        end=end,
        planner_qtrad_commit=runtime.qtrad_commit,
        planner_qtrad_image_digest=runtime.qtrad_image_digest,
    )


def _verify_plan_from_closure(
    plan_path: Path, paths: dict[str, Path], start: datetime, end: datetime
):
    return verify_ibkr_historical_plan(
        plan_path,
        contract_selection_path=paths["selection"],
        operator_selection_path=paths["operator"],
        capability_review_path=paths["review"],
        catalogue_path=paths["catalogue"],
        probe_spec_path=paths["probe"],
        runtime_lock_path=paths["runtime"],
        gateway_archive=paths["gateway_archive"],
        api_archive=paths["api_archive"],
        ibc_archive=paths["ibc_archive"],
        expected_gateway_sha256=hashlib.sha256(paths["gateway_archive"].read_bytes()).hexdigest(),
        expected_api_sha256=hashlib.sha256(paths["api_archive"].read_bytes()).hexdigest(),
        expected_ibc_sha256=hashlib.sha256(paths["ibc_archive"].read_bytes()).hexdigest(),
        expected_runtime_qtrad_commit="a" * 40,
        expected_runtime_image_digest="sha256:" + "d" * 64,
        expected_gateway_version="10.49",
        expected_api_version="10.49",
        expected_ibc_version="3.24.1",
        expected_api_host="127.0.0.1",
        expected_api_port=4002,
        expected_client_id_policy="DEDICATED_NONZERO_CLIENT_ID",
        request_profile_path=paths["profile"],
        canary_evidence_path=paths["canary"],
        expected_profile_frozen_by="operator@example.invalid",
        expected_profile_frozen_at=_NOW,
        maximum_in_flight_requests=2,
        request_timeout_seconds=60,
        retry_count=5,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds=15,
            per_contract_window_seconds=2,
            max_requests_per_contract_window=5,
            rolling_window_seconds=600,
            max_requests_per_rolling_window=55,
        ),
        expected_start=start,
        expected_end=end,
        planner_qtrad_commit="a" * 40,
        planner_qtrad_image_digest="sha256:" + "d" * 64,
    )


def _verify_profile_from_closure(paths: dict[str, Path]):
    from qtrad.runtime.universe import load_capture_candidates

    candidates = load_capture_candidates(paths["catalogue"])
    selection = verify_ibkr_contract_selection(
        paths["selection"],
        capability_review_path=paths["review"],
        operator_selection_path=paths["operator"],
        catalogue_path=paths["catalogue"],
        probe_spec_path=paths["probe"],
    )
    runtime = load_ibkr_runtime_lock(paths["runtime"])
    return verify_ibkr_historical_request_profile(
        paths["profile"],
        canary_evidence_path=paths["canary"],
        expected_runtime_sha256=runtime.runtime_sha256,
        expected_selection_sha256=selection.selection_sha256,
        selection=selection,
        asset_class_by_instrument={
            instrument.instrument_id: instrument.asset_class
            for instrument in candidates.instruments
        },
        expected_frozen_by="operator@example.invalid",
        expected_frozen_at=_NOW,
        maximum_in_flight_requests=2,
        request_timeout_seconds=60,
        retry_count=5,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        pacing_policy=IbkrHistoricalPacingPolicy(
            identical_request_cooldown_seconds=15,
            per_contract_window_seconds=2,
            max_requests_per_contract_window=5,
            rolling_window_seconds=600,
            max_requests_per_rolling_window=55,
        ),
    )


def _rehash_document(path: Path, hash_field: str) -> None:
    payload = json.loads(path.read_text())
    unsigned = {key: value for key, value in payload.items() if key != hash_field}
    payload[hash_field] = sha256_json(unsigned)
    path.write_text(json.dumps(payload))


def _rebind_profile_to_canary(paths: dict[str, Path]) -> None:
    _rehash_document(paths["canary"], "evidence_sha256")
    canary_payload = json.loads(paths["canary"].read_text())
    profile_payload = json.loads(paths["profile"].read_text())
    profile_payload["canary_evidence_sha256"] = canary_payload["evidence_sha256"]
    profile_payload["canary_evidence_file_sha256"] = hashlib.sha256(
        paths["canary"].read_bytes()
    ).hexdigest()
    paths["profile"].write_text(json.dumps(profile_payload))
    _rehash_document(paths["profile"], "profile_sha256")


def _assert_exact_coverage(plan) -> None:
    for instrument_id in {request.instrument_id for request in plan.requests}:
        for kind in IbkrHistoricalRequestKind:
            requests = sorted(
                (
                    request
                    for request in plan.requests
                    if request.instrument_id == instrument_id and request.kind is kind
                ),
                key=lambda request: request.interval_start,
            )
            cursor = plan.start
            for request in requests:
                assert request.interval_start == cursor
                cursor = request.interval_end
            assert cursor == plan.end


def test_historical_plan_is_deterministic_file_replayable_and_uses_frozen_midpoint_parameters(
    tmp_path: Path,
) -> None:
    selection = _selection(_review())
    runtime = _planning_runtime()
    profile = _profile()
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 2, 15, tzinfo=UTC)
    plan = build_ibkr_historical_plan(
        selection=selection,
        runtime=runtime,
        request_profile=profile,
        asset_class_by_instrument={_QUERY.instrument_id: AssetClass.FX},
        start=start,
        end=end,
        planner_qtrad_commit=runtime.qtrad_commit,
        planner_qtrad_image_digest=runtime.qtrad_image_digest,
    )
    assert plan == build_ibkr_historical_plan(
        selection=selection,
        runtime=runtime,
        request_profile=_profile(reverse_product_mapping=True),
        asset_class_by_instrument={_QUERY.instrument_id: AssetClass.FX},
        start=start,
        end=end,
        planner_qtrad_commit=runtime.qtrad_commit,
        planner_qtrad_image_digest=runtime.qtrad_image_digest,
    )
    assert {request.kind for request in plan.requests} == set(IbkrHistoricalRequestKind)
    assert len(plan.requests) == 4
    _assert_exact_coverage(plan)
    for request in plan.requests:
        assert request.end_date_time.endswith(" UTC")
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
            assert request.bar_size == "1 min"
            assert request.what_to_show == "MIDPOINT"
            assert request.use_rth is False
            assert request.format_date == 2
            assert request.keep_up_to_date is False

    plan_path = tmp_path / "plan.json"
    write_ibkr_historical_plan(plan_path, plan)
    assert load_ibkr_historical_plan(plan_path) == plan
    assert load_ibkr_historical_plan_bytes(plan_path.read_bytes()) == plan
    with pytest.raises(ValueError, match="must contain an object"):
        load_ibkr_historical_plan_bytes(b"[]")


def test_historical_plan_rejects_additional_eligible_stk_index(
    tmp_path: Path,
) -> None:
    paths = _write_plan_closure(tmp_path)
    selection = load_ibkr_contract_selection(paths["selection"])
    index_decision = next(
        decision
        for decision in selection.decisions
        if decision.instrument_id == InstrumentId("index:spx")
    )
    assert index_decision.fingerprint is not None
    additional_instrument_id = InstrumentId("index:additional-stk")
    additional_decision = replace(
        index_decision,
        instrument_id=additional_instrument_id,
        fingerprint=replace(
            index_decision.fingerprint,
            con_id=90000001,
            symbol="STKX",
            security_type="STK",
            local_symbol="STKX",
            trading_class="STKX",
        ),
    )
    decisions = (*selection.decisions, additional_decision)
    mutated_selection = replace(
        selection,
        decisions=decisions,
        selection_sha256=sha256_json(
            {
                **selection.identity_payload(),
                "decisions": [item.as_json_value() for item in decisions],
            }
        ),
    )
    runtime = load_ibkr_runtime_lock(paths["runtime"])
    request_profile = load_ibkr_historical_request_profile(paths["profile"])

    with pytest.raises(ValueError, match="CFD"):
        build_ibkr_historical_plan(
            selection=mutated_selection,
            runtime=runtime,
            request_profile=request_profile,
            asset_class_by_instrument={
                InstrumentId("fx:eur-usd"): AssetClass.FX,
                InstrumentId("index:spx"): AssetClass.INDEX,
                additional_instrument_id: AssetClass.INDEX,
                InstrumentId("commodity:gold"): AssetClass.COMMODITY,
            },
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 2, 15, tzinfo=UTC),
            planner_qtrad_commit=runtime.qtrad_commit,
            planner_qtrad_image_digest=runtime.qtrad_image_digest,
        )


def test_historical_plan_builder_and_verifier_replay_authenticated_lower_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
    paths = _write_plan_closure(tmp_path)
    assert _verify_profile_from_closure(paths) == load_ibkr_historical_request_profile(
        paths["profile"]
    )
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 2, 15, tzinfo=UTC)
    plan = _build_plan_from_closure(paths, start, end)
    plan_path = tmp_path / "plan.json"
    write_ibkr_historical_plan(plan_path, plan)

    assert _verify_plan_from_closure(plan_path, paths, start, end) == plan


@pytest.mark.parametrize(
    "mutation",
    ["selection", "runtime", "duration", "pacing", "representative", "group", "canary"],
)
def test_historical_plan_verifier_rejects_mutated_lower_artifacts(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historical, "derive_qtrad_commit", lambda: "a" * 40)
    paths = _write_plan_closure(tmp_path)
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 2, 15, tzinfo=UTC)
    plan = _build_plan_from_closure(paths, start, end)
    plan_path = tmp_path / "plan.json"
    write_ibkr_historical_plan(plan_path, plan)

    if mutation == "selection":
        payload = json.loads(paths["selection"].read_text())
        payload["catalogue_hash"] = "f" * 64
        paths["selection"].write_text(json.dumps(payload))
        _rehash_document(paths["selection"], "selection_sha256")
    elif mutation == "runtime":
        payload = json.loads(paths["runtime"].read_text())
        payload["gateway_version"] = "99.99"
        paths["runtime"].write_text(json.dumps(payload))
        _rehash_document(paths["runtime"], "runtime_sha256")
    elif mutation == "duration":
        payload = json.loads(paths["profile"].read_text())
        payload["schedule_duration"] = "1 D"
        paths["profile"].write_text(json.dumps(payload))
        _rehash_document(paths["profile"], "profile_sha256")
    elif mutation == "pacing":
        payload = json.loads(paths["profile"].read_text())
        payload["pacing_policy"]["identical_request_cooldown_seconds"] = 16
        paths["profile"].write_text(json.dumps(payload))
        _rehash_document(paths["profile"], "profile_sha256")
    elif mutation == "representative":
        payload = json.loads(paths["canary"].read_text())
        cases = payload["cases"]
        replacement = next(item["case"] for item in cases if item["case"]["group"] == "INDEX")
        for item in cases:
            if item["case"]["group"] != "FX":
                continue
            item["case"]["instrument_id"] = replacement["instrument_id"]
            item["case"]["fingerprint"] = dict(replacement["fingerprint"])
            for request_result in item["requests"]:
                request = request_result["request"]
                request["instrument_id"] = replacement["instrument_id"]
                request["fingerprint"] = dict(replacement["fingerprint"])
                request["request_sha256"] = sha256_json(
                    {key: value for key, value in request.items() if key != "request_sha256"}
                )
        payload["reauthentication"] = [
            item for item in payload["reauthentication"] if item["expected"]["symbol"] != "EUR"
        ]
        paths["canary"].write_text(json.dumps(payload))
        _rebind_profile_to_canary(paths)
    elif mutation == "group":
        payload = json.loads(paths["canary"].read_text())
        for item in payload["cases"]:
            if item["case"]["group"] == "FX":
                item["case"]["group"] = "INDEX"
            elif item["case"]["group"] == "INDEX":
                item["case"]["group"] = "FX"
        paths["canary"].write_text(json.dumps(payload))
        _rebind_profile_to_canary(paths)
    else:
        paths["canary"].write_bytes(b"mutated-canary-evidence")

    with pytest.raises(ValueError):
        _verify_plan_from_closure(plan_path, paths, start, end)


@given(
    start_offset=st.integers(min_value=0, max_value=800),
    days=st.integers(min_value=1, max_value=80),
)
def test_historical_plan_property_preserves_exact_utc_half_open_coverage(
    start_offset: int, days: int
) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=start_offset)
    plan = build_ibkr_historical_plan(
        selection=_selection(_review()),
        runtime=_planning_runtime(),
        request_profile=_profile(),
        asset_class_by_instrument={_QUERY.instrument_id: AssetClass.FX},
        start=start,
        end=start + timedelta(days=days),
        planner_qtrad_commit="a" * 40,
        planner_qtrad_image_digest="sha256:" + "d" * 64,
    )
    _assert_exact_coverage(plan)


def test_historical_plan_uses_catalogue_asset_class_for_mismatched_namespace() -> None:
    base_selection = _selection(_review())
    instrument_id = InstrumentId("fx:index-named")
    base_fingerprint = base_selection.decisions[0].fingerprint
    assert base_fingerprint is not None
    decision = replace(
        base_selection.decisions[0],
        instrument_id=instrument_id,
        fingerprint=replace(base_fingerprint, security_type="CFD"),
    )
    selection_identity = {
        **base_selection.identity_payload(),
        "decisions": [decision.as_json_value()],
    }
    selection = replace(
        base_selection,
        decisions=(decision,),
        selection_sha256=sha256_json(selection_identity),
    )
    plan = build_ibkr_historical_plan(
        selection=selection,
        runtime=_planning_runtime(),
        request_profile=_profile(fx_duration="1 D", index_duration="1 W"),
        asset_class_by_instrument={instrument_id: AssetClass.INDEX},
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 15, tzinfo=UTC),
        planner_qtrad_commit="a" * 40,
        planner_qtrad_image_digest="sha256:" + "d" * 64,
    )
    midpoint = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    assert midpoint.duration == "1 W"


def test_historical_request_rejects_ownership_interval_longer_than_duration() -> None:
    plan = build_ibkr_historical_plan(
        selection=_selection(_review()),
        runtime=_planning_runtime(),
        request_profile=_profile(),
        asset_class_by_instrument={_QUERY.instrument_id: AssetClass.FX},
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 15, tzinfo=UTC),
        planner_qtrad_commit="a" * 40,
        planner_qtrad_image_digest="sha256:" + "d" * 64,
    )
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    oversized_end = request.interval_start + timedelta(days=8)
    with pytest.raises(ValueError, match="ownership interval exceeds"):
        replace(
            request,
            interval_end=oversized_end,
            end_date_time=ibkr_end_date_time(oversized_end),
        )


def test_historical_plan_rejects_unbounded_request_count_before_materialisation() -> None:
    with pytest.raises(ValueError, match="bounded planner limit"):
        build_ibkr_historical_plan(
            selection=_selection(_review()),
            runtime=_planning_runtime(),
            request_profile=_profile(fx_duration="1 D"),
            asset_class_by_instrument={_QUERY.instrument_id: AssetClass.FX},
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=20_001),
            planner_qtrad_commit="a" * 40,
            planner_qtrad_image_digest="sha256:" + "d" * 64,
        )


def test_historical_plan_uses_utc_ownership_across_dst_and_rejects_unsafe_profile() -> None:
    plan = build_ibkr_historical_plan(
        selection=_selection(_review()),
        runtime=_planning_runtime(),
        request_profile=_profile(),
        asset_class_by_instrument={_QUERY.instrument_id: AssetClass.FX},
        start=datetime(2026, 3, 8, 6, tzinfo=UTC),
        end=datetime(2026, 3, 10, 6, tzinfo=UTC),
        planner_qtrad_commit="a" * 40,
        planner_qtrad_image_digest="sha256:" + "d" * 64,
    )
    _assert_exact_coverage(plan)
    with pytest.raises(ValueError, match="duration"):
        build_ibkr_historical_request_profile(
            canary_evidence_filename="canary.json",
            canary_evidence_sha256=_CANARY_HASH,
            canary_evidence_file_sha256=_CANARY_FILE_HASH,
            canary_runtime_sha256=_CANARY_RUNTIME_HASH,
            canary_selection_sha256=_CANARY_SELECTION_HASH,
            frozen_by="operator@example.invalid",
            frozen_at=_NOW,
            permitted_bar_durations=("1 M",),
            permitted_schedule_durations=("1 D",),
            bar_duration_by_asset_class={
                AssetClass.FX: "1 M",
                AssetClass.INDEX: "1 M",
                AssetClass.COMMODITY: "1 M",
            },
            schedule_duration="1 D",
            maximum_in_flight_requests=1,
            request_timeout_seconds=60,
            retry_count=0,
            duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
            pacing_policy=IbkrHistoricalPacingPolicy(15, 2, 5, 600, 55),
        )


def test_historical_plan_verifier_rejects_rehashed_request_interval_mutation(
    tmp_path: Path,
) -> None:
    plan = build_ibkr_historical_plan(
        selection=_selection(_review()),
        runtime=_planning_runtime(),
        request_profile=_profile(),
        asset_class_by_instrument={_QUERY.instrument_id: AssetClass.FX},
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 15, tzinfo=UTC),
        planner_qtrad_commit="a" * 40,
        planner_qtrad_image_digest="sha256:" + "d" * 64,
    )
    path = tmp_path / "plan.json"
    write_ibkr_historical_plan(path, plan)
    payload = json.loads(path.read_text())
    requests = cast(list[dict[str, object]], payload["requests"])
    requests[0]["interval_end"] = "2026-02-07T00:00:00Z"
    requests[0]["end_date_time"] = "20260207-00:00:00 UTC"
    request_unsigned = {key: value for key, value in requests[0].items() if key != "request_sha256"}
    requests[0]["request_sha256"] = sha256_json(request_unsigned)
    plan_unsigned = {key: value for key, value in payload.items() if key != "plan_sha256"}
    payload["plan_sha256"] = sha256_json(plan_unsigned)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="contiguously"):
        load_ibkr_historical_plan(path)
