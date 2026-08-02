import hashlib
import json
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
    sha256_json,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.ports.ibkr_capability import IbkrContractQuery
from qtrad.runtime.ibkr_historical import (
    build_ibkr_historical_plan_from_files,
    load_ibkr_contract_selection,
    load_ibkr_historical_plan,
    load_ibkr_historical_request_profile,
    load_ibkr_runtime_lock,
    verify_ibkr_contract_selection,
    verify_ibkr_historical_plan,
    verify_ibkr_runtime_lock,
    write_ibkr_contract_selection,
    write_ibkr_historical_plan,
    write_ibkr_historical_request_profile,
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


def _profile(*, reverse_product_mapping: bool = False):
    entries = [
        (AssetClass.FX, "1 W"),
        (AssetClass.INDEX, "1 W"),
        (AssetClass.COMMODITY, "1 W"),
    ]
    if reverse_product_mapping:
        entries.reverse()
    return build_ibkr_historical_request_profile(
        canary_evidence_sha256="d" * 64,
        permitted_bar_durations=("1 D", "1 W", "2 W", "4 W"),
        permitted_schedule_durations=("1 D", "1 W", "2 W", "4 W"),
        bar_duration_by_asset_class=dict(entries),
        schedule_duration="1 W",
        maximum_in_flight_requests=2,
        request_timeout_seconds=60,
        retry_count=5,
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


def _assert_exact_coverage(plan) -> None:
    for kind in IbkrHistoricalRequestKind:
        requests = sorted(
            (request for request in plan.requests if request.kind is kind),
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
        start=start,
        end=end,
    )
    assert plan == build_ibkr_historical_plan(
        selection=selection,
        runtime=runtime,
        request_profile=_profile(reverse_product_mapping=True),
        start=start,
        end=end,
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

    selection_path = tmp_path / "selection.json"
    runtime_path = tmp_path / "runtime.json"
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    write_ibkr_contract_selection(selection_path, selection)
    write_ibkr_runtime_lock(runtime_path, runtime)
    write_ibkr_historical_request_profile(profile_path, profile)
    assert load_ibkr_historical_request_profile(profile_path) == profile
    rebuilt = build_ibkr_historical_plan_from_files(
        contract_selection_path=selection_path,
        runtime_lock_path=runtime_path,
        request_profile_path=profile_path,
        start=start,
        end=end,
    )
    assert rebuilt == plan
    write_ibkr_historical_plan(plan_path, plan)
    assert load_ibkr_historical_plan(plan_path) == plan
    assert verify_ibkr_historical_plan(plan_path) == plan


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
        start=start,
        end=start + timedelta(days=days),
    )
    _assert_exact_coverage(plan)


def test_historical_plan_uses_utc_ownership_across_dst_and_rejects_unsafe_profile() -> None:
    plan = build_ibkr_historical_plan(
        selection=_selection(_review()),
        runtime=_planning_runtime(),
        request_profile=_profile(),
        start=datetime(2026, 3, 8, 6, tzinfo=UTC),
        end=datetime(2026, 3, 10, 6, tzinfo=UTC),
    )
    _assert_exact_coverage(plan)
    with pytest.raises(ValueError, match="duration"):
        build_ibkr_historical_request_profile(
            canary_evidence_sha256="d" * 64,
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
            pacing_policy=IbkrHistoricalPacingPolicy(15, 2, 5, 600, 55),
        )


def test_historical_plan_verifier_rejects_rehashed_request_interval_mutation(
    tmp_path: Path,
) -> None:
    plan = build_ibkr_historical_plan(
        selection=_selection(_review()),
        runtime=_planning_runtime(),
        request_profile=_profile(),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 15, tzinfo=UTC),
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
        verify_ibkr_historical_plan(path)
