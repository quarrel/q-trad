from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import qtrad.runtime.ibkr_foundation as foundation_runtime
from qtrad import __main__ as cli
from qtrad.__main__ import build_parser
from qtrad.application.ibkr_foundation import (
    _provider_evidence,
    build_ibkr_foundation,
    evaluate_ibkr_foundation_readiness,
)
from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.domain.events import JsonValue
from qtrad.domain.foundation import InstrumentRole, TargetDataset
from qtrad.domain.ibkr_foundation import (
    IBKR_CONFIRMATORY_CANDIDATES,
    IBKR_CONFIRMATORY_GROUPS,
    IBKR_CONFIRMATORY_INSTRUMENTS,
    IBKRFoundationReadiness,
    IBKRFoundationReadinessCause,
    IBKRFoundationReadinessState,
)
from qtrad.domain.ibkr_historical import IbkrHistoricalRequestKind
from qtrad.domain.ibkr_results import IbkrHistoricalEvidenceDisposition
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import BarProvenance
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.ibkr_foundation import (
    foundation_config_payload,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.provider_history import read_provider_history_source_evidence
from tests.test_provider_history import _FINGERPRINT, _published_provider_history, _request
from tests.test_r1_foundation import _config


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _rewrite_payload_reference(bundle: Path, field: str, value: object) -> None:
    document = cast(dict[str, object], json.loads(bundle.read_text(encoding="utf-8")))
    payload = cast(dict[str, object], document["payload"])
    children = cast(dict[str, object], payload["children"])
    references = cast(list[object], children["observations"])
    reference = cast(dict[str, object], references[0])
    reference[field] = value
    document["build_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    bundle.write_bytes(_canonical_json(document) + b"\n")


def _rewrite_child_manifest(bundle: Path, field: str, value: object) -> None:
    document = cast(dict[str, object], json.loads(bundle.read_text(encoding="utf-8")))
    payload = cast(dict[str, object], document["payload"])
    children = cast(dict[str, object], payload["children"])
    references = cast(list[object], children["observations"])
    reference = cast(dict[str, object], references[0])
    manifest_reference = cast(str, reference["manifest_path"])
    old_path = bundle.parent / Path(manifest_reference)
    manifest = cast(dict[str, object], json.loads(old_path.read_text(encoding="utf-8")))
    manifest[field] = value
    identity = dict(manifest)
    identity.pop("manifest_sha256")
    manifest_sha256 = hashlib.sha256(_canonical_json(identity)).hexdigest()
    manifest["manifest_sha256"] = manifest_sha256
    new_path = old_path.with_name(f"part-000000-{manifest_sha256[:24]}.json")
    new_path.write_bytes(_canonical_json(manifest) + b"\n")
    old_path.unlink()
    reference["manifest_id"] = manifest_sha256[:24]
    reference["manifest_path"] = new_path.relative_to(bundle.parent).as_posix()
    reference["manifest_sha256"] = manifest_sha256
    document["build_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    bundle.write_bytes(_canonical_json(document) + b"\n")


def _foundation_bundle_fixture(tmp_path: Path) -> Path:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 3, tzinfo=UTC),
    )
    bundle = tmp_path / "foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    return bundle


def test_stage8_writer_preserves_racing_output_on_child_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 3, tzinfo=UTC),
    )
    output = tmp_path / "racing-foundation.json"
    child_root = tmp_path / "racing-foundation.json.children"

    def fail_after_output_race(*_args: object, **_kwargs: object) -> dict[str, JsonValue]:
        output.write_text("competitor", encoding="utf-8")
        raise RuntimeError("simulated output race")

    monkeypatch.setattr(foundation_runtime, "_write_children", fail_after_output_race)

    with pytest.raises(RuntimeError, match="simulated output race"):
        write_ibkr_foundation(
            output,
            provider_manifest=provider_manifest,
            configuration=configuration,
        )

    assert output.read_text(encoding="utf-8") == "competitor"
    assert not child_root.exists()


def test_stage8_declarations_are_fixed_and_model_independent() -> None:
    assert tuple(str(instrument) for instrument, _ in IBKR_CONFIRMATORY_CANDIDATES) == (
        "fx:aud-usd",
        "fx:eur-usd",
        "index:australia-200",
        "index:us-500",
        "commodity:spot-gold",
        "commodity:us-crude",
    )
    assert (
        tuple(instrument for instrument, _ in IBKR_CONFIRMATORY_CANDIDATES)
        == IBKR_CONFIRMATORY_INSTRUMENTS
    )
    assert IBKR_CONFIRMATORY_GROUPS == ("FX", "indices", "commodities")


def test_provider_history_foundation_round_trips_and_replays_children(
    tmp_path: Path,
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 3, tzinfo=UTC),
    )

    source_evidence = read_provider_history_source_evidence(provider_manifest)
    build = build_ibkr_foundation(source_evidence, configuration)

    assert (
        build.readiness.state
        is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    )
    assert set(build.readiness.rows_by_candidate) == {
        str(instrument) for instrument in IBKR_CONFIRMATORY_INSTRUMENTS
    }
    assert build.observations.rows[0].provenance is BarProvenance.IBKR_HISTORICAL
    assert build.observations.rows[0].source_external_id
    assert len(build.panel.rows) > 0
    assert len(build.targets.rows) > 0
    assert tuple(
        instrument_id
        for instrument_id in build.configuration.ordered_instruments
        if build.configuration.instrument_roles[instrument_id] is InstrumentRole.TARGET
    ) == tuple(sorted(str(instrument) for instrument in IBKR_CONFIRMATORY_INSTRUMENTS))

    bundle = tmp_path / "foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    verified = verify_ibkr_foundation(bundle)

    assert verified.readiness.as_json() == build.readiness.as_json()
    assert verified.panel.dataset_id == build.panel.dataset_id
    with pytest.raises(FileExistsError):
        write_ibkr_foundation(
            bundle,
            provider_manifest=provider_manifest,
            configuration=configuration,
        )

    document = json.loads(bundle.read_text(encoding="utf-8"))
    assert not Path(document["provider_history_manifest"]).is_absolute()
    assert set(document["payload"]["children"]) == {
        "observations",
        "panel",
        "targets",
        "folds",
    }
    assert all(
        "rows" not in child
        for children in document["payload"]["children"].values()
        for child in children
    )
    assert document["payload"]["readiness"]["evidence"]["fold_count"] == 0
    assert "INSUFFICIENT_COMMON_SUPPORT" in document["payload"]["readiness"]["causes"]
    document["payload"]["readiness"]["state"] = "QUALIFYING_HISTORY_READY"
    bundle.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(
        ValueError,
        match=r"manifest bytes are not canonical|payload identity",
    ):
        verify_ibkr_foundation(bundle)


def test_stage8_forces_non_confirmatory_targets_to_context(tmp_path: Path) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 3, tzinfo=UTC),
    )
    extra_instrument = "fx:nzd-usd"
    extra_configuration = replace(
        configuration,
        ordered_instruments=(*configuration.ordered_instruments, extra_instrument),
        instrument_roles={
            **configuration.instrument_roles,
            extra_instrument: InstrumentRole.TARGET,
        },
    )

    source_evidence = read_provider_history_source_evidence(provider_manifest)
    build = build_ibkr_foundation(source_evidence, extra_configuration)

    assert build.configuration.instrument_roles[extra_instrument] is InstrumentRole.CONTEXT
    assert all(row.instrument_id != extra_instrument for row in build.targets.rows)
    assert (
        IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT in build.readiness.causes
        or build.readiness.evidence["fold_count"] == 0
    )


def test_stage8_zero_valid_folds_remains_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 3, tzinfo=UTC),
    )

    def no_valid_folds(*args: object, **kwargs: object) -> object:
        raise ValueError("no scientifically valid expanding folds are available")

    monkeypatch.setattr(
        "qtrad.application.ibkr_foundation.build_expanding_folds",
        no_valid_folds,
    )
    build = build_ibkr_foundation(
        read_provider_history_source_evidence(provider_manifest),
        configuration,
    )

    assert build.folds.folds == ()
    assert IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT in build.readiness.causes
    assert (
        build.readiness.state
        is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    )


def _session_aware_source_evidence(
    *,
    context_failure: bool = False,
    missing_active_bar: bool = False,
) -> tuple[ProviderHistorySourceEvidence, TargetDataset, datetime, datetime]:
    source_start = datetime(2026, 2, 1, tzinfo=UTC)
    source_end = datetime(2026, 3, 5, tzinfo=UTC)
    requests: list[object] = []
    results: list[object] = []
    observations: list[object] = []
    target_rows: list[object] = []
    contracts: list[object] = []

    def add_instrument(instrument: str, con_id: int, *, no_data: bool = False) -> None:
        instrument_id = InstrumentId(instrument)
        fingerprint = replace(_FINGERPRINT, con_id=con_id)
        contracts.append(SimpleNamespace(instrument_id=instrument_id, fingerprint=fingerprint))
        day = source_start
        while day < source_end:
            chunk_end = min(day + timedelta(days=1), source_end)
            bar_request = _request(
                day,
                chunk_end,
                IbkrHistoricalRequestKind.MIDPOINT_BARS,
                instrument=instrument_id,
                fingerprint=fingerprint,
            )
            schedule_request = _request(
                day,
                chunk_end,
                IbkrHistoricalRequestKind.SCHEDULE,
                instrument=instrument_id,
                fingerprint=fingerprint,
            )
            requests.extend((bar_request, schedule_request))

            sessions: list[dict[str, object]] = []
            accepted_rows: list[dict[str, object]] = []
            if day.weekday() < 5:
                session_start = day + timedelta(minutes=1)
                session_end = session_start + timedelta(minutes=65)
                sessions.append(
                    {
                        "interval_start": session_start.isoformat(),
                        "interval_end": session_end.isoformat(),
                        "active": True,
                    }
                )
                if not no_data:
                    for minute in range(65):
                        bar_start = session_start + timedelta(minutes=minute)
                        if (
                            missing_active_bar
                            and instrument == str(IBKR_CONFIRMATORY_INSTRUMENTS[0])
                            and day == source_start + timedelta(days=1)
                            and minute == 10
                        ):
                            continue
                        accepted_rows.append({"bar_start": bar_start.isoformat()})
                        observations.append(SimpleNamespace(instrument_id=instrument))
                        target_rows.append(
                            SimpleNamespace(
                                instrument_id=instrument,
                                decision_time=bar_start,
                                horizon=timedelta(minutes=15),
                                return_disposition=SimpleNamespace(value="VALID"),
                            )
                        )
            else:
                sessions.append(
                    {
                        "interval_start": day.isoformat(),
                        "interval_end": chunk_end.isoformat(),
                        "active": False,
                    }
                )

            result_index = len(results) + 1
            results.append(
                SimpleNamespace(
                    request_sha256=bar_request.request_sha256,
                    result_sha256=f"{result_index:064x}",
                    evidence_disposition=(
                        IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED
                        if no_data or not accepted_rows
                        else IbkrHistoricalEvidenceDisposition.SUCCEEDED
                    ),
                    accepted_rows=tuple(accepted_rows),
                    sessions=(),
                )
            )
            results.append(
                SimpleNamespace(
                    request_sha256=schedule_request.request_sha256,
                    result_sha256=f"{result_index + 1:064x}",
                    evidence_disposition=IbkrHistoricalEvidenceDisposition.SUCCEEDED,
                    accepted_rows=(),
                    sessions=tuple(sessions),
                )
            )
            day = chunk_end

    for index, instrument in enumerate(IBKR_CONFIRMATORY_INSTRUMENTS):
        add_instrument(str(instrument), 42 + index)
    if context_failure:
        add_instrument("fx:nzd-usd", 99, no_data=True)

    source_plan = SimpleNamespace(
        contract_selection_sha256="c" * 64,
        plan_sha256="p" * 64,
        runtime_sha256="r" * 64,
        requests=tuple(requests),
        eligible_contracts=tuple(contracts),
    )
    aggregate = SimpleNamespace(
        aggregate_sha256="a" * 64,
        coverage_summary={"planned_request_count": len(requests)},
        entitlement_summary={
            "provider_history_eligible_instruments": [
                str(instrument) for instrument in IBKR_CONFIRMATORY_INSTRUMENTS
            ]
        },
    )
    source_artifact = SimpleNamespace(
        plan=source_plan,
        aggregate=aggregate,
        request_results=tuple(results),
    )
    source_evidence = cast(
        ProviderHistorySourceEvidence,
        SimpleNamespace(
            observations=tuple(observations),
            source_artifact=source_artifact,
        ),
    )
    targets = cast(TargetDataset, SimpleNamespace(rows=tuple(target_rows)))
    return source_evidence, targets, source_start, source_end


def _evaluate_session_aware_readiness(
    *,
    context_failure: bool = False,
    missing_active_bar: bool = False,
) -> tuple[IBKRFoundationReadiness, tuple[Mapping[str, JsonValue], ...]]:
    source_evidence, targets, source_start, source_end = _session_aware_source_evidence(
        context_failure=context_failure,
        missing_active_bar=missing_active_bar,
    )
    active_intervals, provider_gaps = _provider_evidence(source_evidence)
    readiness = evaluate_ibkr_foundation_readiness(
        source_evidence,
        targets,
        source_start=source_start,
        source_end=source_end,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        primary_horizon=timedelta(minutes=15),
        fold_count=1,
    )
    return readiness, tuple(provider_gaps)


def test_stage8_session_aware_gaps_allow_qualifying_history() -> None:
    readiness, provider_gaps = _evaluate_session_aware_readiness()

    assert readiness.state is IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY
    assert provider_gaps == ()


def test_stage8_active_session_gap_is_insufficient_even_when_bar_result_precedes_schedule() -> None:
    readiness, provider_gaps = _evaluate_session_aware_readiness(missing_active_bar=True)

    assert readiness.state is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    assert IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT in readiness.causes
    assert any(gap["disposition"] == "MISSING_BAR" for gap in provider_gaps)


def test_stage8_context_instrument_gaps_do_not_block_confirmatory_readiness() -> None:
    readiness, provider_gaps = _evaluate_session_aware_readiness(context_failure=True)

    assert readiness.state is IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY
    assert readiness.evidence["provider_gap_count"] == 0
    assert cast(int, readiness.evidence["total_provider_gap_count"]) > 0
    assert any(gap["instrument_id"] == "fx:nzd-usd" for gap in provider_gaps)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("contract", "unsupported-child-contract", "child contract is unsupported"),
        ("schema_version", 2, "child schema is unsupported"),
        ("part_index", 1, "not contiguous"),
        (
            "lineage",
            {
                "provider_manifest_sha256": "1" * 64,
                "provider_dataset_sha256": "2" * 64,
                "plan_sha256": "3" * 64,
                "aggregate_sha256": "4" * 64,
            },
            "lineage differs from replay",
        ),
    ),
)
def test_stage8_rejects_child_manifest_lineage_and_part_drift(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    bundle = _foundation_bundle_fixture(tmp_path)
    _rewrite_child_manifest(bundle, field, value)

    with pytest.raises(ValueError, match=message):
        verify_ibkr_foundation(bundle)


def test_stage8_cli_requires_one_foundation_source() -> None:
    parser = build_parser()

    provider_args = parser.parse_args(
        [
            "research",
            "foundation",
            "build",
            "--provider-history-manifest",
            "provider.json",
            "--configuration",
            "configuration.json",
            "--output",
            "foundation.json",
        ]
    )
    assert provider_args.provider_history_manifest == Path("provider.json")
    assert provider_args.observations_manifest is None

    readiness_args = parser.parse_args(
        ["research", "foundation", "readiness", "--bundle", "foundation.json"]
    )
    assert readiness_args.bundle == Path("foundation.json")

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "research",
                "foundation",
                "build",
                "--observations-manifest",
                "observations.json",
                "--provider-history-manifest",
                "provider.json",
                "--configuration",
                "configuration.json",
                "--output",
                "foundation.json",
            ]
        )


def test_stage8_cli_build_and_verify_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 3, tzinfo=UTC),
    )
    configuration_path = tmp_path / "configuration.json"
    configuration_path.write_text(
        json.dumps(foundation_config_payload(configuration)),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "Settings", lambda: SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(cli, "configure_logging", lambda _: None)
    bundle = tmp_path / "cli-foundation.json"
    cli.main(
        [
            "research",
            "foundation",
            "build",
            "--provider-history-manifest",
            str(provider_manifest),
            "--configuration",
            str(configuration_path),
            "--output",
            str(bundle),
        ]
    )
    build_output = json.loads(capsys.readouterr().out)
    assert build_output["contract"] == "qtrad-ibkr-historical-foundation-v1"
    cli.main(["research", "foundation", "verify", "--bundle", str(bundle)])
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["source_class"] == "IBKR_HISTORICAL_RESEARCH"
    cli.main(["research", "foundation", "readiness", "--bundle", str(bundle)])
    readiness_output = json.loads(capsys.readouterr().out)
    assert readiness_output["state"] == "INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION"
