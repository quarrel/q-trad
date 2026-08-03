from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import cast
from uuid import UUID

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
    run_ibkr_historical_canary,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrHistoricalCallback,
    IbkrHistoricalCallbackKind,
    IbkrHistoricalConnection,
)
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrHistoricalPacingPolicy,
    IbkrHistoricalRequest,
    sha256_json,
    utc_text,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.ports.ibkr_historical import (
    IbkrContractReauthentication,
    IbkrHistoricalCallbackSink,
)
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


def _callbacks(
    case: IbkrHistoricalCanaryCase,
    kind: ibkr_canary.IbkrHistoricalRequestKind,
    provider_request_id: int,
) -> tuple[dict[str, JsonValue], ...]:
    session_id = "11111111-1111-4111-8111-111111111111"
    received_at = utc_text(_START)
    start = utc_text(case.interval_start)
    end = utc_text(case.interval_end)
    identity = {
        "connection_session_id": session_id,
        "provider_request_id": provider_request_id,
        "connection_generation": 1,
        "received_at": received_at,
    }
    if kind is ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS:
        return (
            {
                **identity,
                "kind": "MIDPOINT_BAR",
                "payload": {
                    "date": int(case.interval_end.timestamp()),
                    "open": "1.0",
                    "high": "1.1",
                    "low": "0.9",
                    "close": "1.05",
                },
            },
            {
                **identity,
                "kind": "COMPLETION",
                "payload": {"start": start, "end": end},
            },
        )
    return (
        {
            **identity,
            "kind": "SCHEDULE",
            "payload": {
                "start": start,
                "end": end,
                "time_zone": "UTC",
                "sessions": [
                    {"start": start, "end": end, "active": True},
                ],
            },
        },
        {
            **identity,
            "kind": "COMPLETION",
            "payload": {"start": start, "end": end, "time_zone": "UTC"},
        },
    )


def _evidence() -> IbkrHistoricalCanaryEvidence:
    cases = _cases()
    session_id = UUID("11111111-1111-4111-8111-111111111111")
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
    provider_request_id = 1_000_000
    for case in cases:
        requests: list[IbkrHistoricalCanaryRequestResult] = []
        for kind in ibkr_canary.IbkrHistoricalRequestKind:
            callbacks = _callbacks(case, kind, provider_request_id)
            requests.append(
                IbkrHistoricalCanaryRequestResult(
                    request=ibkr_canary._request_for_case(case, kind),
                    status="SUCCESS",
                    callback_count=len(callbacks),
                    bar_count=(
                        1 if kind is ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS else 0
                    ),
                    schedule_session_count=(
                        0 if kind is ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS else 1
                    ),
                    error_codes=(),
                    callbacks=callbacks,
                    expected_connection_session_id=session_id,
                    expected_provider_request_id=provider_request_id,
                    expected_connection_generation=1,
                )
            )
            provider_request_id += 1
        results.append(
            IbkrHistoricalCanaryCaseResult(
                case=case,
                requests=tuple(requests),
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


def _rehash_evidence(
    evidence: IbkrHistoricalCanaryEvidence,
    cases: tuple[IbkrHistoricalCanaryCaseResult, ...],
    stop_reason: str | None,
) -> IbkrHistoricalCanaryEvidence:
    identity: dict[str, JsonValue] = {
        "contract": ibkr_canary.IBKR_CANARY_CONTRACT,
        "schema_version": ibkr_canary.IBKR_CANARY_SCHEMA_VERSION,
        "runtime_sha256": evidence.runtime_sha256,
        "selection_sha256": evidence.selection_sha256,
        "started_at": utc_text(evidence.started_at),
        "completed_at": utc_text(evidence.completed_at),
        "reauthentication": [item.as_json_value() for item in evidence.reauthentication],
        "cases": [item.as_json_value() for item in cases],
        "stop_reason": stop_reason,
    }
    return replace(
        evidence,
        cases=cases,
        stop_reason=stop_reason,
        evidence_sha256=sha256_json(identity),
    )


class _BudgetAdapter:
    def __init__(self) -> None:
        self.request_count = 0
        self.disconnected = False
        self.session_id = UUID("11111111-1111-4111-8111-111111111111")

    async def connect(self) -> IbkrHistoricalConnection:
        return IbkrHistoricalConnection(
            connection_session_id=self.session_id,
            connection_generation=1,
        )

    async def disconnect(self) -> None:
        self.disconnected = True

    async def reauthenticate_contracts(
        self,
        fingerprints: Sequence[IbkrContractFingerprint],
    ) -> tuple[IbkrContractReauthentication, ...]:
        return tuple(
            IbkrContractReauthentication(
                request_id=index,
                connection_generation=1,
                expected=fingerprint,
                observed=(fingerprint,),
                status="MATCH",
            )
            for index, fingerprint in enumerate(fingerprints, start=1)
        )

    async def request_historical(
        self,
        request: IbkrHistoricalRequest,
        *,
        request_id: int,
        connection_session_id: UUID,
        connection_generation: int,
        callback: IbkrHistoricalCallbackSink,
    ) -> None:
        self.request_count += 1
        await callback(
            IbkrHistoricalCallback(
                connection_session_id=connection_session_id,
                provider_request_id=request_id,
                connection_generation=connection_generation,
                kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
                received_at=_START,
                payload={
                    "date": int(_START.timestamp()),
                    "open": "1",
                    "high": "1",
                    "low": "1",
                    "close": "1",
                    "padding": "x" * (ibkr_canary.IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES + 1),
                },
            )
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


@pytest.mark.asyncio
async def test_canary_closes_before_retained_callbacks_exceed_writer_limit(
    tmp_path: Path,
) -> None:
    adapter = _BudgetAdapter()
    evidence = await run_ibkr_historical_canary(
        adapter,
        _cases(),
        runtime_sha256=_RUNTIME_HASH,
        selection_sha256=_SELECTION_HASH,
        clock=lambda: _START,
    )
    stop_reason = ibkr_canary.IBKR_CANARY_EXCESSIVE_CLOSURE_REASON
    assert evidence.stop_reason == stop_reason
    assert evidence.cases[0].requests[0].status == "EXCESSIVE_CLOSURE"
    assert adapter.request_count == 1
    assert adapter.disconnected is True

    output = tmp_path / "stopped.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    assert output.stat().st_size < 8 * 1024 * 1024
    loaded = verify_ibkr_historical_canary_evidence(output)
    assert loaded.stop_reason == stop_reason


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


def _rewrite_rehashed_document(
    path: Path,
    document: dict[str, object],
) -> None:
    identity = {key: value for key, value in document.items() if key != "evidence_sha256"}
    document["evidence_sha256"] = sha256_json(identity)
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")


def test_file_verifier_replays_callback_counts_after_rehashed_mutation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "canary.json"
    write_ibkr_historical_canary_evidence(output, _evidence())
    document = json.loads(output.read_text())
    for case in document["cases"]:
        for request in case["requests"]:
            request["callbacks"] = []
            request["callback_count"] = 0
    _rewrite_rehashed_document(output, document)
    with pytest.raises(ValueError, match="callback counts"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_transport_identity_mutation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "canary.json"
    write_ibkr_historical_canary_evidence(output, _evidence())
    document = json.loads(output.read_text())
    first_request = document["cases"][0]["requests"][0]
    for callback in first_request["callbacks"]:
        callback["provider_request_id"] += 1
    _rewrite_rehashed_document(output, document)
    with pytest.raises(ValueError, match="expected provider"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_adjacency_mutation(tmp_path: Path) -> None:
    evidence = _evidence()
    mutated_cases = list(evidence.cases)
    for index, case_result in enumerate(mutated_cases):
        if case_result.case.duration != "1 D":
            continue
        shifted_case = replace(
            case_result.case,
            interval_start=case_result.case.interval_start + timedelta(minutes=1),
            interval_end=case_result.case.interval_end + timedelta(minutes=1),
        )
        shifted_requests = tuple(
            replace(
                request_result,
                request=ibkr_canary._request_for_case(shifted_case, request_result.request.kind),
            )
            for request_result in case_result.requests
        )
        mutated_cases[index] = replace(
            case_result,
            case=shifted_case,
            requests=shifted_requests,
        )
    output = tmp_path / "canary.json"
    write_ibkr_historical_canary_evidence(
        output, _rehash_evidence(evidence, tuple(mutated_cases), None)
    )
    with pytest.raises(ValueError, match="adjacent"):
        verify_ibkr_historical_canary_evidence(output)


def test_canary_writer_rejects_actual_eight_mib_boundary_crossing(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    first_case = evidence.cases[0]
    first_request = first_case.requests[0]
    huge_callback = dict(first_request.callbacks[0])
    huge_callback["payload"] = {
        **cast(dict[str, JsonValue], huge_callback["payload"]),
        "padding": "x" * (8 * 1024 * 1024),
    }
    huge_request = replace(
        first_request,
        callbacks=(huge_callback, *first_request.callbacks[1:]),
    )
    huge_cases = list(evidence.cases)
    huge_cases[0] = replace(first_case, requests=(huge_request, first_case.requests[1]))
    with pytest.raises(ValueError, match="bounded size"):
        write_ibkr_historical_canary_evidence(
            tmp_path / "too-large.json",
            _rehash_evidence(evidence, tuple(huge_cases), None),
        )


def test_canary_writer_accepts_bounded_excessive_closure_evidence(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    stop_reason = ibkr_canary.IBKR_CANARY_EXCESSIVE_CLOSURE_REASON
    first_case = evidence.cases[0]
    stopped_request = replace(
        first_case.requests[0],
        status="EXCESSIVE_CLOSURE",
        callbacks=(first_case.requests[0].callbacks[0],),
        callback_count=1,
        stop_reason=stop_reason,
        detail="callback evidence bound reached",
    )
    not_run = ibkr_canary._not_run_request(first_case.requests[1].request, stop_reason)
    stopped_case = replace(
        first_case,
        requests=(stopped_request, not_run),
        status="FAILED",
        stop_reason=stop_reason,
    )
    stopped_cases = (
        stopped_case,
        *(
            ibkr_canary._not_run_case(case_result.case, stop_reason)
            for case_result in evidence.cases[1:]
        ),
    )
    output = tmp_path / "stopped.json"
    write_ibkr_historical_canary_evidence(
        output, _rehash_evidence(evidence, stopped_cases, stop_reason)
    )
    loaded = verify_ibkr_historical_canary_evidence(output)
    assert loaded.stop_reason == stop_reason


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
