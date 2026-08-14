from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from qtrad.__main__ import build_parser
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
    ibkr_end_date_time,
    sha256_json,
    utc_text,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.provider_history import ProviderHistoricalDataset
from qtrad.runtime.provider_history import authenticate_provider_history
from qtrad.runtime.provider_history_v2 import _read_provider_history_v2_manifest

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "provider-history"
_INSTRUMENT = InstrumentId("fx:aud-usd")
_FINGERPRINT = IbkrContractFingerprint(
    con_id=42,
    symbol="AUD",
    security_type="CASH",
    currency="USD",
    exchange="IDEALPRO",
    primary_exchange=None,
    local_symbol="AUD.USD",
    trading_class="AUD.USD",
    multiplier=None,
    underlying_con_id=None,
    contract_month=None,
)


def _provider_history_receipt(manifest: Path) -> Path:
    return manifest.parent.parent / "provider-history-v2-receipt.json"


def _published_provider_history(
    tmp_path: Path,
    *,
    artifact: object | None = None,
) -> tuple[None, ProviderHistoricalDataset, Path]:
    if artifact is not None:
        raise ValueError("provider-history fixtures are forward-only v2")
    provider_root = tmp_path / "provider"
    shutil.copytree(_FIXTURE_ROOT / "v2", provider_root)
    shutil.copy2(
        _FIXTURE_ROOT / "v2-receipt.json",
        _provider_history_receipt(provider_root / "manifest.json"),
    )
    manifest = provider_root / "manifest.json"
    return None, _read_provider_history_v2_manifest(manifest).dataset, manifest


def _request(
    start: datetime,
    end: datetime,
    kind: IbkrHistoricalRequestKind,
    *,
    duration: str = "1 D",
    instrument: InstrumentId = _INSTRUMENT,
    fingerprint: IbkrContractFingerprint = _FINGERPRINT,
) -> IbkrHistoricalRequest:
    bar_size = "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day"
    what_to_show = "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
    identity: dict[str, JsonValue] = {
        "instrument_id": str(instrument),
        "fingerprint": fingerprint.as_json_value(),
        "kind": kind.value,
        "interval_start": utc_text(start),
        "interval_end": utc_text(end),
        "end_date_time": ibkr_end_date_time(end),
        "duration": duration,
        "bar_size": bar_size,
        "what_to_show": what_to_show,
        "use_rth": False,
        "format_date": 2,
        "keep_up_to_date": False,
    }
    return IbkrHistoricalRequest(
        instrument_id=instrument,
        fingerprint=fingerprint,
        kind=kind,
        interval_start=start,
        interval_end=end,
        end_date_time=ibkr_end_date_time(end),
        duration=duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=False,
        format_date=2,
        keep_up_to_date=False,
        request_sha256=sha256_json(identity),
    )


def test_retained_v1_receipt_authenticates_without_rows(tmp_path: Path) -> None:
    root = tmp_path / "provider-v1"
    shutil.copytree(_FIXTURE_ROOT / "v1", root)
    receipt = tmp_path / "provider-history-v1-receipt.json"
    shutil.copy2(_FIXTURE_ROOT / "v1-receipt.json", receipt)

    authenticated = authenticate_provider_history(root / "manifest.json", receipt=receipt)

    assert isinstance(authenticated, ProviderHistoricalDataset)
    assert not hasattr(authenticated, "observations")


@pytest.mark.parametrize("command", ["build-provider-history", "repack-provider-history"])
def test_retired_provider_history_commands_are_not_exposed(command: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["research", "observations", command])
