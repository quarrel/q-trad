from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from qtrad.application import ibkr_canary
from qtrad.application.ibkr_canary import (
    IBKR_CANARY_DURATIONS,
    IBKR_CANARY_GROUPS,
    IbkrHistoricalCanaryCase,
    IbkrHistoricalCanaryCaseResult,
    IbkrHistoricalCanaryEvidence,
    IbkrHistoricalCanaryRequestResult,
    build_adjacent_ibkr_canary_cases,
    freeze_ibkr_request_profile_from_canary,
)
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrHistoricalPacingPolicy,
    sha256_json,
    utc_text,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.ports.ibkr_historical import IbkrContractReauthentication
from qtrad.runtime.ibkr_canary import (
    load_ibkr_historical_canary_evidence,
    verify_ibkr_historical_canary_evidence,
    write_ibkr_historical_canary_evidence,
)

_START = datetime(2026, 2, 1, tzinfo=UTC)
_RUNTIME_HASH = "a" * 64
_SELECTION_HASH = "b" * 64


def _fingerprint(con_id: int, symbol: str) -> IbkrContractFingerprint:

    return IbkrContractFingerprint(
        con_id=con_id,
        symbol=symbol,
        security_type="CASH",
        currency="USD",
        exchange="IDEALPRO",
        primary_exchange=None,
        local_symbol=f"{symbol}.USD",
        trading_class=f"{symbol}.USD",
        multiplier=None,
        underlying_con_id=None,
        contract_month=None,
    )


def _cases() -> tuple[IbkrHistoricalCanaryCase, ...]:
    representatives = {
        AssetClass.FX: (InstrumentId("fx:eur-usd"), _fingerprint(11, "EUR")),
        AssetClass.INDEX: (InstrumentId("index:spx"), _fingerprint(22, "SPX")),
        AssetClass.COMMODITY: (InstrumentId("commodity:gold"), _fingerprint(33, "GOLD")),
    }
    return build_adjacent_ibkr_canary_cases(representatives, anchor_end=_START)


def _evidence() -> IbkrHistoricalCanaryEvidence:
    cases = _cases()
    unique_fingerprints = tuple(dict.fromkeys(case.fingerprint for case in cases))
    reauthentication = tuple(
        IbkrContractReauthentication(
            request_id=index,
            connection_generation=1,
            expected=fingerprint,
            observed=(fingerprint,),
            status="MATCH",
        )
        for index, fingerprint in enumerate(unique_fingerprints, start=1)
    )
    results: list[IbkrHistoricalCanaryCaseResult] = []
    for case in cases:
        requests = tuple(
            IbkrHistoricalCanaryRequestResult(
                request=ibkr_canary._request_for_case(case, kind),
                status="SUCCESS",
                callback_count=0,
                bar_count=1 if kind.value == "MIDPOINT_BARS" else 0,
                schedule_session_count=0 if kind.value == "MIDPOINT_BARS" else 1,
                error_codes=(),
                callbacks=(),
            )
            for kind in ibkr_canary.IbkrHistoricalRequestKind
        )
        results.append(
            IbkrHistoricalCanaryCaseResult(
                case=case,
                requests=requests,
                status="SUCCESS",
            )
        )
    identity = {
        "contract": "qtrad-ibkr-historical-canary-v1",
        "schema_version": 1,
        "runtime_sha256": _RUNTIME_HASH,
        "selection_sha256": _SELECTION_HASH,
        "started_at": utc_text(_START),
        "completed_at": utc_text(_START),
        "reauthentication": [item.as_json_value() for item in reauthentication],
        "cases": [item.as_json_value() for item in results],
        "stop_reason": None,
    }
    return IbkrHistoricalCanaryEvidence(
        runtime_sha256=_RUNTIME_HASH,
        selection_sha256=_SELECTION_HASH,
        started_at=_START,
        completed_at=_START,
        reauthentication=reauthentication,
        cases=tuple(results),
        stop_reason=None,
        evidence_sha256=sha256_json(identity),
    )


def test_adjacent_canary_cases_cover_groups_and_durations() -> None:
    cases = _cases()

    assert len(cases) == len(IBKR_CANARY_GROUPS) * len(IBKR_CANARY_DURATIONS)
    assert {(case.group, case.duration) for case in cases} == {
        (group, duration) for group in IBKR_CANARY_GROUPS for duration in IBKR_CANARY_DURATIONS
    }
    by_duration = {
        duration: [case for case in cases if case.duration == duration]
        for duration in IBKR_CANARY_DURATIONS
    }
    assert all(
        len({(case.interval_start, case.interval_end) for case in values}) == 1
        for values in by_duration.values()
    )
    representative = {duration: values[0] for duration, values in by_duration.items()}
    assert all(
        representative[left].interval_start == representative[right].interval_end
        for left, right in pairwise(IBKR_CANARY_DURATIONS)
    )


def test_canary_evidence_round_trips_and_freezes_conservative_profile(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    output = tmp_path / "canary.json"

    write_ibkr_historical_canary_evidence(output, evidence)
    loaded = verify_ibkr_historical_canary_evidence(
        output,
        expected_runtime_sha256=_RUNTIME_HASH,
        expected_selection_sha256=_SELECTION_HASH,
    )

    assert loaded.as_json_value() == evidence.as_json_value()
    profile = freeze_ibkr_request_profile_from_canary(
        loaded,
        canary_evidence_filename=output.name,
        frozen_by="operator",
        frozen_at=_START,
        maximum_in_flight_requests=1,
        request_timeout_seconds=60,
        retry_count=1,
        duplicate_request_protection="PLAN_REQUEST_ID_UNIQUE_NO_RERUN",
        pacing_policy=IbkrHistoricalPacingPolicy(15, 2, 5, 600, 55),
    )
    assert profile.schedule_duration == "4 W"
    assert profile.permitted_bar_durations == ("4 W",)
    assert profile.permitted_schedule_durations == IBKR_CANARY_DURATIONS
    with pytest.raises(FileExistsError):
        write_ibkr_historical_canary_evidence(output, evidence)


def test_canary_evidence_rejects_tampered_hash(tmp_path: Path) -> None:
    evidence = _evidence()
    output = tmp_path / "canary.json"
    write_ibkr_historical_canary_evidence(output, evidence)

    document = json.loads(output.read_text())
    document["evidence_sha256"] = "c" * 64
    output.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError, match="evidence hash"):
        load_ibkr_historical_canary_evidence(output)


def test_canary_cli_exposes_file_only_verification_and_profile_freeze() -> None:
    from qtrad.__main__ import build_parser

    parser = build_parser()
    verify_args = parser.parse_args(
        [
            "historical",
            "ibkr",
            "canary-verify",
            "--evidence",
            "canary.json",
            "--expected-runtime-sha256",
            "a" * 64,
        ]
    )
    freeze_args = parser.parse_args(
        [
            "historical",
            "ibkr",
            "profile-freeze",
            "--canary-evidence",
            "canary.json",
            "--output",
            "profile.json",
            "--frozen-by",
            "operator",
            "--frozen-at",
            "2026-02-01T00:00:00Z",
        ]
    )

    assert verify_args.historical_ibkr_command == "canary-verify"
    assert verify_args.expected_runtime_sha256 == "a" * 64
    assert freeze_args.historical_ibkr_command == "profile-freeze"
    assert freeze_args.maximum_in_flight_requests == 1
