from pathlib import Path

import pytest

from qtrad.runtime.ibkr_capability import load_ibkr_capability_probe_spec


def test_probe_spec_rejects_unknown_narrowing_field_before_adapter_construction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "probe.toml"
    path.write_text(
        """schema_version = 1
name = "test"

[[query]]
instrument_id = "fx:eur-usd"
symbol = "EUR"
security_type = "CASH"
exchange = "IDEALPRO"
currency = "USD"
trading_clas = "EUR.USD"
"""
    )

    with pytest.raises(ValueError, match="unknown or missing fields"):
        load_ibkr_capability_probe_spec(path)


@pytest.mark.parametrize(
    "query_fields",
    (
        'security_type = "FUT"\nexchange = "GLOBEX"\ncurrency = "USD"',
        'security_type = "OPT"\nexchange = "SMART"\ncurrency = "USD"',
    ),
)
def test_probe_spec_rejects_broad_derivative_queries_before_socket_io(
    tmp_path: Path, query_fields: str
) -> None:
    path = tmp_path / "broad.toml"
    path.write_text(
        "schema_version = 1\n"
        'name = "test"\n\n'
        "[[query]]\n"
        'instrument_id = "index:spx"\n'
        'symbol = "SPX"\n'
        f"{query_fields}\n"
    )

    with pytest.raises(ValueError, match=r"futures.*local symbol|broad derivative"):
        load_ibkr_capability_probe_spec(path)
