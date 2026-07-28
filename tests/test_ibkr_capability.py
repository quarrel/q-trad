import json
from pathlib import Path

import pytest

from qtrad.application.ibkr_capability import build_ibkr_capability_preflight
from qtrad.runtime.universe import load_capture_candidates


def test_ibkr_candidate_catalogue_is_canonical_and_non_authoritative() -> None:
    candidates = load_capture_candidates(Path("config/capture-ibkr-v1-candidates.toml"))

    assert candidates.name == "capture-ibkr-v1-candidates"
    assert len(candidates.instruments) == 20
    assert candidates.exact_review_epics == {}
    assert all("bitcoin" not in str(item.instrument_id) for item in candidates.instruments)
    assert all("korea" not in str(item.instrument_id) for item in candidates.instruments)


def test_ibkr_preflight_is_deterministic_non_secret_and_stops_before_io() -> None:
    kwargs = {
        "catalogue_name": "capture-ibkr-v1-candidates",
        "catalogue_hash": "a" * 64,
        "candidate_count": 20,
        "gateway_host": "127.0.0.1",
        "gateway_port": 4002,
        "client_id": 71,
    }

    first = build_ibkr_capability_preflight(**kwargs)
    second = build_ibkr_capability_preflight(**kwargs)
    payload = first.as_json_value()

    assert first.preflight_hash == second.preflight_hash
    assert payload["status"] == "OPERATOR_AUTHENTICATION_REQUIRED"
    assert payload["selection_authority"] is False
    assert payload["external_io_performed"] is False
    encoded = json.dumps(payload)
    assert "password" not in encoded.lower()
    assert "username" not in encoded.lower()
    assert "account" not in encoded.lower()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("gateway_host", "bad host", "host"),
        ("gateway_port", 0, "port"),
        ("client_id", 0, "client ID"),
    ],
)
def test_ibkr_preflight_rejects_unsafe_gateway_configuration(
    field: str, value: object, message: str
) -> None:
    kwargs: dict[str, object] = {
        "catalogue_name": "catalogue",
        "catalogue_hash": "a" * 64,
        "candidate_count": 1,
        "gateway_host": "127.0.0.1",
        "gateway_port": 4002,
        "client_id": 71,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        build_ibkr_capability_preflight(**kwargs)  # type: ignore[arg-type]
