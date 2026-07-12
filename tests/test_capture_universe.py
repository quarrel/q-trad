from pathlib import Path

import pytest

from qtrad.runtime.universe import load_capture_universe


def test_capture_v1_is_the_qualified_seven_and_hashes_its_content() -> None:
    universe = load_capture_universe(Path("config/capture-v1.toml"))

    assert universe.name == "capture-v1"
    assert tuple(str(item.instrument_id) for item in universe.instruments) == (
        "fx:aud-usd",
        "fx:eur-usd",
        "fx:usd-jpy",
        "fx:gbp-usd",
        "index:australia-200",
        "index:us-500",
        "index:ftse-100",
    )
    assert len(universe.configuration_hash) == 64
    assert len(universe.preferred_epics) == 7


def test_capture_universe_rejects_an_unpinned_provider_selection(tmp_path: Path) -> None:
    path = tmp_path / "universe.toml"
    path.write_text(
        """
name = "unsafe"
[[instrument]]
id = "fx:eur-usd"
display_name = "EUR/USD"
asset_class = "FX"
base_currency = "EUR"
quote_currency = "USD"
search_aliases = ["EUR/USD"]
"""
    )

    with pytest.raises(ValueError, match="explicit preferred IG epic"):
        load_capture_universe(path)
