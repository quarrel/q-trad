from __future__ import annotations

import hashlib
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
    IbkrHistoricalDisconnected,
    IbkrHistoricalIncomplete,
    IbkrHistoricalRetryableError,
    IbkrHistoricalTerminalError,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrContractSelection,
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
from qtrad.runtime import ibkr_historical as ibkr_runtime
from qtrad.runtime.ibkr_canary import (
    load_ibkr_historical_canary_evidence,
    verify_ibkr_historical_canary_evidence,
    write_ibkr_historical_canary_evidence,
)

_START = datetime(2026, 2, 1, tzinfo=UTC)
_RUNTIME_HASH = "a" * 64
_SELECTION_HASH = "b" * 64


def _fingerprint(
    con_id: int,
    symbol: str,
    *,
    security_type: str = "CASH",
) -> IbkrContractFingerprint:
    return IbkrContractFingerprint(
        con_id=con_id,
        symbol=symbol,
        security_type=security_type,
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
        AssetClass.INDEX: (
            InstrumentId("index:spx"),
            _fingerprint(22, "SPX", security_type="CFD"),
        ),
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
                    "date": int(case.interval_start.timestamp()),
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
        "schema_version": ibkr_canary.IBKR_CANARY_SCHEMA_VERSION,
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
    reauthentication: tuple[IbkrContractReauthentication, ...] | None = None,
) -> IbkrHistoricalCanaryEvidence:
    retained_reauthentication = (
        evidence.reauthentication if reauthentication is None else reauthentication
    )
    identity: dict[str, JsonValue] = {
        "contract": ibkr_canary.IBKR_CANARY_CONTRACT,
        "schema_version": ibkr_canary.IBKR_CANARY_SCHEMA_VERSION,
        "runtime_sha256": evidence.runtime_sha256,
        "selection_sha256": evidence.selection_sha256,
        "started_at": utc_text(evidence.started_at),
        "completed_at": utc_text(evidence.completed_at),
        "reauthentication": [item.as_json_value() for item in retained_reauthentication],
        "cases": [item.as_json_value() for item in cases],
        "stop_reason": stop_reason,
    }
    return replace(
        evidence,
        reauthentication=retained_reauthentication,
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
                    "date": int(request.interval_start.timestamp()),
                    "open": "1",
                    "high": "1",
                    "low": "1",
                    "close": "1",
                    "padding": "x" * (ibkr_canary.IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES + 1),
                },
            )
        )


class _AggregateByteAdapter(_BudgetAdapter):
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
        case = next(
            case
            for case in _cases()
            if case.instrument_id == request.instrument_id
            and case.interval_start == request.interval_start
            and case.interval_end == request.interval_end
        )
        item = ibkr_canary._callback_from_evidence(_callbacks(case, request.kind, request_id)[0])
        for _ in range(4):
            await callback(item)


class _FailureAdapter(_BudgetAdapter):
    def __init__(
        self,
        fail_kind: ibkr_canary.IbkrHistoricalRequestKind,
        error: BaseException,
    ) -> None:
        super().__init__()
        self.fail_kind = fail_kind
        self.error = error

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
        if request.kind is self.fail_kind:
            raise self.error
        case = next(
            case
            for case in _cases()
            if case.instrument_id == request.instrument_id
            and case.interval_start == request.interval_start
            and case.interval_end == request.interval_end
        )
        for value in _callbacks(case, request.kind, request_id):
            await callback(ibkr_canary._callback_from_evidence(value))


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


@pytest.mark.parametrize("security_type", ["IND", "ETF"])
def test_index_canary_requires_midpoint_capable_representation(security_type: str) -> None:
    index_case = next(case for case in _cases() if case.group is AssetClass.INDEX)

    assert index_case.fingerprint.security_type == "CFD"
    assert (
        ibkr_canary._request_for_case(
            index_case, ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS
        ).what_to_show
        == "MIDPOINT"
    )
    with pytest.raises(ValueError, match="CFD in this stage"):
        replace(
            index_case,
            fingerprint=_fingerprint(22, "SPX", security_type=security_type),
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


@pytest.mark.asyncio
async def test_canary_byte_closure_replays_current_request_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = ibkr_canary._callback_from_evidence(
        _callbacks(
            _cases()[0],
            ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS,
            1_000_000,
        )[0]
    )
    callback_bytes = ibkr_canary._callback_encoded_upper_bound(sample)
    monkeypatch.setattr(
        ibkr_canary,
        "IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES",
        callback_bytes * 3,
    )

    evidence = await run_ibkr_historical_canary(
        _AggregateByteAdapter(),
        _cases(),
        runtime_sha256=_RUNTIME_HASH,
        selection_sha256=_SELECTION_HASH,
        clock=lambda: _START,
    )
    request_result = evidence.cases[0].requests[0]
    assert request_result.status == "EXCESSIVE_CLOSURE"
    assert request_result.retained_callback_count == 3
    assert request_result.retained_callback_bytes == callback_bytes * 3
    assert request_result.rejected_callback is not None

    output = tmp_path / "aggregate-byte-closure.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    loaded = verify_ibkr_historical_canary_evidence(output)
    assert loaded.as_json_value() == evidence.as_json_value()


@pytest.mark.asyncio
async def test_canary_count_closure_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ibkr_canary, "IBKR_CANARY_MAX_CALLBACKS_PER_REQUEST", 1)
    evidence = await run_ibkr_historical_canary(
        _FailureAdapter(
            ibkr_canary.IbkrHistoricalRequestKind.SCHEDULE,
            IbkrHistoricalIncomplete(),
        ),
        _cases(),
        runtime_sha256=_RUNTIME_HASH,
        selection_sha256=_SELECTION_HASH,
        clock=lambda: _START,
    )
    request_result = evidence.cases[0].requests[0]
    assert request_result.status == "EXCESSIVE_CLOSURE"
    assert request_result.closure_limit == ibkr_canary.IBKR_CANARY_CLOSURE_LIMIT_COUNT
    assert request_result.retained_callback_count == 1
    assert request_result.rejected_callback is not None

    output = tmp_path / "count-closure.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    loaded = verify_ibkr_historical_canary_evidence(output)
    assert loaded.cases[0].requests[0].closure_limit == (
        ibkr_canary.IBKR_CANARY_CLOSURE_LIMIT_COUNT
    )


@pytest.mark.asyncio
async def test_file_verifier_rejects_rehashed_rejected_callback_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = ibkr_canary._callback_from_evidence(
        _callbacks(
            _cases()[0],
            ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS,
            1_000_000,
        )[0]
    )
    callback_bytes = ibkr_canary._callback_encoded_upper_bound(sample)
    monkeypatch.setattr(
        ibkr_canary,
        "IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES",
        callback_bytes * 3,
    )
    evidence = await run_ibkr_historical_canary(
        _AggregateByteAdapter(),
        _cases(),
        runtime_sha256=_RUNTIME_HASH,
        selection_sha256=_SELECTION_HASH,
        clock=lambda: _START,
    )
    output = tmp_path / "rehashed-rejected-callback.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    document = cast(dict[str, object], json.loads(output.read_text()))
    cases = cast(list[dict[str, object]], document["cases"])
    requests = cast(list[dict[str, object]], cases[0]["requests"])
    request = requests[0]
    rejected_bytes = request["rejected_callback_bytes"]
    assert isinstance(rejected_bytes, int)
    request["rejected_callback_bytes"] = rejected_bytes + 1_000_000
    request["rejected_callback_sha256"] = "c" * 64
    _rewrite_rehashed_document(output, document)

    with pytest.raises(ValueError, match="rejected callback witness"):
        verify_ibkr_historical_canary_evidence(output)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_kind", "error"),
    [
        (
            ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS,
            IbkrHistoricalDisconnected(),
        ),
        (
            ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS,
            IbkrHistoricalRetryableError(),
        ),
        (
            ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS,
            IbkrHistoricalIncomplete(),
        ),
        (
            ibkr_canary.IbkrHistoricalRequestKind.SCHEDULE,
            IbkrHistoricalIncomplete(),
        ),
        (
            ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS,
            IbkrHistoricalTerminalError(
                IbkrTerminalDisposition.PROVIDER_REJECTED,
                "sanitized terminal failure",
            ),
        ),
    ],
    ids=(
        "global-disconnect",
        "callback-buffer-exhaustion",
        "malformed-bar",
        "malformed-schedule",
        "terminal-error",
    ),
)
async def test_callbackless_exception_evidence_round_trips(
    tmp_path: Path,
    fail_kind: ibkr_canary.IbkrHistoricalRequestKind,
    error: BaseException,
) -> None:
    evidence = await run_ibkr_historical_canary(
        _FailureAdapter(fail_kind, error),
        _cases(),
        runtime_sha256=_RUNTIME_HASH,
        selection_sha256=_SELECTION_HASH,
        clock=lambda: _START,
    )
    failed_index = 0 if fail_kind is ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS else 1
    failed_request = evidence.cases[0].requests[failed_index]
    assert failed_request.outcome_source == ibkr_canary.IBKR_CANARY_OUTCOME_EXCEPTION

    output = tmp_path / "exception.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    loaded = verify_ibkr_historical_canary_evidence(output)
    assert loaded.cases[0].requests[failed_index].status == failed_request.status


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
        canary_evidence_file_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
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


def test_file_verifier_accepts_sparse_market_completion_range(tmp_path: Path) -> None:
    evidence = _evidence()
    index_case_index = next(
        index
        for index, case_result in enumerate(evidence.cases)
        if case_result.case.group is AssetClass.INDEX
    )
    case_result = evidence.cases[index_case_index]
    request_result = case_result.requests[0]
    first_bar = dict(request_result.callbacks[0])
    last_bar = dict(request_result.callbacks[0])
    first_bar_at = case_result.case.interval_start + timedelta(hours=1)
    last_bar_at = case_result.case.interval_end - timedelta(minutes=2)
    first_bar["payload"] = {
        **cast(dict[str, JsonValue], first_bar["payload"]),
        "date": int(first_bar_at.timestamp()),
    }
    last_bar["payload"] = {
        **cast(dict[str, JsonValue], last_bar["payload"]),
        "date": int(last_bar_at.timestamp()),
    }
    completion = dict(request_result.callbacks[1])
    completion["payload"] = {
        "start": utc_text(first_bar_at),
        "end": utc_text(last_bar_at),
    }
    sparse_request = replace(
        request_result,
        callbacks=(first_bar, last_bar, completion),
        callback_count=3,
        bar_count=2,
    )
    sparse_cases = list(evidence.cases)
    sparse_cases[index_case_index] = replace(
        case_result,
        requests=(sparse_request, case_result.requests[1]),
    )
    output = tmp_path / "sparse-completion.json"
    write_ibkr_historical_canary_evidence(
        output,
        _rehash_evidence(evidence, tuple(sparse_cases), None),
    )

    loaded = verify_ibkr_historical_canary_evidence(output)
    assert loaded.cases[index_case_index].requests[0].status == "SUCCESS"


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


def test_canary_verifier_rejects_unwitnessed_excessive_closure_evidence(
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
        outcome_source=ibkr_canary.IBKR_CANARY_OUTCOME_EXCEPTION,
        closure_limit=ibkr_canary.IBKR_CANARY_CLOSURE_LIMIT_BYTES,
        retained_callback_count=1,
        retained_callback_bytes=0,
        rejected_callback_bytes=1,
        rejected_callback_sha256="c" * 64,
        rejected_callback=first_case.requests[0].callbacks[0],
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
    with pytest.raises(ValueError, match="closure witness"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_reauthentication_duplicate_ids(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    mutated = list(evidence.reauthentication)
    mutated[1] = replace(mutated[1], request_id=mutated[0].request_id)
    output = tmp_path / "duplicate-reauth.json"
    write_ibkr_historical_canary_evidence(
        output, _rehash_evidence(evidence, evidence.cases, None, tuple(mutated))
    )
    with pytest.raises(ValueError, match="transport identities"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_reauthentication_mixed_generation(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    mutated = list(evidence.reauthentication)
    mutated[1] = replace(mutated[1], connection_generation=2)
    output = tmp_path / "mixed-reauth.json"
    write_ibkr_historical_canary_evidence(
        output, _rehash_evidence(evidence, evidence.cases, None, tuple(mutated))
    )
    with pytest.raises(ValueError, match="generation"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_completion_reordering(tmp_path: Path) -> None:
    evidence = _evidence()
    output = tmp_path / "completion-order.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    document = json.loads(output.read_text())
    callbacks = document["cases"][0]["requests"][0]["callbacks"]
    document["cases"][0]["requests"][0]["callbacks"] = list(reversed(callbacks))
    _rewrite_rehashed_document(output, document)
    with pytest.raises(ValueError, match="status is not replayable"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_out_of_window_bar(tmp_path: Path) -> None:
    evidence = _evidence()
    output = tmp_path / "bar-window.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    document = json.loads(output.read_text())
    document["cases"][0]["requests"][0]["callbacks"][0]["payload"]["date"] = int(
        evidence.cases[0].case.interval_end.timestamp()
    )
    _rewrite_rehashed_document(output, document)
    with pytest.raises(ValueError, match="outside request interval"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_out_of_window_schedule(tmp_path: Path) -> None:
    evidence = _evidence()
    output = tmp_path / "schedule-window.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    document = json.loads(output.read_text())
    schedule = document["cases"][0]["requests"][1]["callbacks"][0]["payload"]
    outside_start = evidence.cases[0].case.interval_end + timedelta(minutes=1)
    outside_end = outside_start + timedelta(hours=1)
    schedule["start"] = utc_text(outside_start)
    schedule["end"] = utc_text(outside_end)
    schedule["sessions"][0]["start"] = utc_text(outside_start)
    schedule["sessions"][0]["end"] = utc_text(outside_end)
    completion = document["cases"][0]["requests"][1]["callbacks"][1]["payload"]
    completion["start"] = utc_text(outside_start)
    completion["end"] = utc_text(outside_end)
    _rewrite_rehashed_document(output, document)
    with pytest.raises(ValueError, match="does not overlap request interval"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_schedule_completion_mismatch(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    output = tmp_path / "schedule-completion.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    document = json.loads(output.read_text())
    completion = document["cases"][0]["requests"][1]["callbacks"][1]["payload"]
    completion["end"] = utc_text(evidence.cases[0].case.interval_end - timedelta(minutes=1))
    _rewrite_rehashed_document(output, document)
    with pytest.raises(ValueError, match="status is not replayable"):
        verify_ibkr_historical_canary_evidence(output)


def test_file_verifier_rejects_rehashed_callback_outside_execution_interval(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    output = tmp_path / "callback-time.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    document = json.loads(output.read_text())
    document["cases"][0]["requests"][0]["callbacks"][0]["received_at"] = utc_text(
        evidence.started_at - timedelta(minutes=1)
    )
    _rewrite_rehashed_document(output, document)
    with pytest.raises(ValueError, match="execution interval"):
        verify_ibkr_historical_canary_evidence(output)


def test_canary_evidence_rejects_tampered_hash(tmp_path: Path) -> None:
    evidence = _evidence()
    output = tmp_path / "canary.json"
    write_ibkr_historical_canary_evidence(output, evidence)

    document = json.loads(output.read_text())
    document["evidence_sha256"] = "c" * 64
    output.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError, match="evidence hash"):
        load_ibkr_historical_canary_evidence(output)


def test_canary_output_reservation_is_create_only_and_symlink_safe(tmp_path: Path) -> None:
    output = tmp_path / "canary.json"
    with ibkr_runtime.reserve_create_only_output(output, "canary evidence") as reservation:
        assert output.exists()
        reservation.publish({"status": "published"}, "canary evidence")

    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "published"}
    with pytest.raises(FileExistsError, match="already exists"):
        ibkr_runtime.reserve_create_only_output(output, "canary evidence")

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ibkr_runtime.reserve_create_only_output(linked_root / "canary.json", "canary evidence")


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


def _selection_for_representatives(
    *,
    index_security_type: str = "CFD",
    include_extra_fx: bool = False,
) -> IbkrContractSelection:
    from types import SimpleNamespace

    entries = [
        (InstrumentId("fx:eur-usd"), AssetClass.FX, _fingerprint(11, "EUR")),
        (
            InstrumentId("index:spx"),
            AssetClass.INDEX,
            _fingerprint(22, "SPX", security_type=index_security_type),
        ),
        (InstrumentId("commodity:gold"), AssetClass.COMMODITY, _fingerprint(33, "GOLD")),
    ]
    if include_extra_fx:
        entries.append((InstrumentId("fx:gbp-usd"), AssetClass.FX, _fingerprint(44, "GBP")))
    decisions = tuple(
        SimpleNamespace(
            instrument_id=instrument_id,
            decision=ibkr_canary.IbkrContractDecision.ACCEPTED_EXACT_CONTRACT,
            acquisition_eligible=True,
            fingerprint=fingerprint,
            descriptive_metadata={"asset_class": group.value},
        )
        for instrument_id, group, fingerprint in entries
    )
    return cast(IbkrContractSelection, SimpleNamespace(decisions=decisions))


def _representative_ids() -> dict[AssetClass, InstrumentId]:
    return {
        AssetClass.FX: InstrumentId("fx:eur-usd"),
        AssetClass.INDEX: InstrumentId("index:spx"),
        AssetClass.COMMODITY: InstrumentId("commodity:gold"),
    }


def test_canary_representatives_require_exact_groups_and_selected_contracts() -> None:
    selection = _selection_for_representatives()
    representatives = _representative_ids()

    with pytest.raises(ValueError, match="cover exactly"):
        ibkr_canary.validate_ibkr_historical_canary_representatives(
            selection, representatives={AssetClass.FX: representatives[AssetClass.FX]}
        )
    with pytest.raises(ValueError, match="distinct"):
        ibkr_canary.validate_ibkr_historical_canary_representatives(
            selection,
            representatives={
                AssetClass.FX: representatives[AssetClass.FX],
                AssetClass.INDEX: representatives[AssetClass.FX],
                AssetClass.COMMODITY: representatives[AssetClass.COMMODITY],
            },
        )
    with pytest.raises(ValueError, match="does not match"):
        ibkr_canary.validate_ibkr_historical_canary_representatives(
            selection,
            representatives={
                **representatives,
                AssetClass.COMMODITY: InstrumentId("commodity:silver"),
            },
        )


def test_canary_representatives_reject_wrong_group_and_non_cfd_index() -> None:
    wrong_group_selection = _selection_for_representatives(include_extra_fx=True)
    with pytest.raises(ValueError, match="does not match"):
        ibkr_canary.validate_ibkr_historical_canary_representatives(
            wrong_group_selection,
            representatives={
                AssetClass.FX: InstrumentId("fx:eur-usd"),
                AssetClass.INDEX: InstrumentId("fx:gbp-usd"),
                AssetClass.COMMODITY: InstrumentId("commodity:gold"),
            },
        )

    with pytest.raises(ValueError, match="CFD"):
        ibkr_canary.validate_ibkr_historical_canary_representatives(
            _selection_for_representatives(index_security_type="STK"),
            representatives=_representative_ids(),
        )


def _midpoint_bar_callback(timestamp: int) -> IbkrHistoricalCallback:
    return IbkrHistoricalCallback(
        connection_session_id=UUID("11111111-1111-4111-8111-111111111111"),
        provider_request_id=1_000_000,
        connection_generation=1,
        kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
        received_at=_START,
        payload={
            "date": timestamp,
            "open": "1",
            "high": "1",
            "low": "1",
            "close": "1",
        },
    )


def test_completion_range_allows_session_gap_before_first_bar() -> None:
    case = _cases()[0]
    request = ibkr_canary._request_for_case(
        case, ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    bar = _midpoint_bar_callback(int((case.interval_start + timedelta(hours=1)).timestamp()))

    ibkr_canary._verify_completion_payload(
        request,
        {"start": utc_text(case.interval_start), "end": utc_text(case.interval_end)},
        bars=(bar,),
    )


def test_completion_range_rejects_bar_before_start() -> None:
    case = _cases()[0]
    request = ibkr_canary._request_for_case(
        case, ibkr_canary.IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    bar = _midpoint_bar_callback(int((case.interval_start - timedelta(minutes=1)).timestamp()))

    with pytest.raises(ValueError, match="starts after first retained bar"):
        ibkr_canary._verify_completion_payload(
            request,
            {"start": utc_text(case.interval_start), "end": utc_text(case.interval_end)},
            bars=(bar,),
        )


def test_file_verifier_accepts_schedule_extending_past_request(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    output = tmp_path / "schedule-extension.json"
    write_ibkr_historical_canary_evidence(output, evidence)
    document = json.loads(output.read_text())
    request_end = evidence.cases[0].case.interval_end
    extended_end = request_end + timedelta(hours=1)
    schedule = document["cases"][0]["requests"][1]["callbacks"][0]["payload"]
    schedule["end"] = utc_text(extended_end)
    schedule["sessions"][0]["end"] = utc_text(extended_end)
    completion = document["cases"][0]["requests"][1]["callbacks"][1]["payload"]
    completion["end"] = utc_text(extended_end)
    _rewrite_rehashed_document(output, document)

    loaded = verify_ibkr_historical_canary_evidence(output)
    assert loaded.cases[0].requests[1].status == "SUCCESS"
