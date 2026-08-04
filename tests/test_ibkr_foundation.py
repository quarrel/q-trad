from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from qtrad import __main__ as cli
from qtrad.__main__ import build_parser
from qtrad.application.ibkr_foundation import build_ibkr_foundation
from qtrad.domain.ibkr_foundation import (
    IBKR_CONFIRMATORY_CANDIDATES,
    IBKR_CONFIRMATORY_GROUPS,
    IBKR_CONFIRMATORY_INSTRUMENTS,
    IBKRFoundationReadinessState,
)
from qtrad.domain.market_data import BarProvenance
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.ibkr_foundation import (
    foundation_config_payload,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.provider_history import read_provider_history_observations
from tests.test_provider_history import _published_provider_history
from tests.test_r1_foundation import _config


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

    provider_dataset, provider_rows = read_provider_history_observations(provider_manifest)
    build = build_ibkr_foundation(provider_dataset, provider_rows, configuration)

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
    document["payload"]["readiness"]["state"] = "QUALIFYING_HISTORY_READY"
    bundle.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="payload identity"):
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
