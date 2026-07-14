from pathlib import Path

import pytest

from qtrad.runtime.universe import load_capture_candidates, load_capture_universe


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


def test_capture_v2_candidates_are_hashable_but_cannot_authorise_ingestion() -> None:
    path = Path("config/capture-v2-candidates.toml")

    candidates = load_capture_candidates(path)

    identifiers = tuple(str(item.instrument_id) for item in candidates.instruments)
    assert candidates.name == "capture-v2-candidates"
    assert identifiers == (
        "fx:aud-usd",
        "fx:eur-usd",
        "fx:usd-jpy",
        "fx:gbp-usd",
        "fx:usd-chf",
        "fx:usd-cad",
        "fx:nzd-usd",
        "fx:eur-jpy",
        "fx:eur-gbp",
        "fx:usd-cnh",
        "index:australia-200",
        "index:us-500",
        "index:ftse-100",
        "index:us-tech-100",
        "index:wall-street",
        "index:germany-40",
        "index:france-40",
        "index:japan-225",
        "index:hong-kong-hs50",
        "index:eu-stocks-50",
    )
    assert len(candidates.configuration_hash) == 64
    with pytest.raises(ValueError, match="explicit preferred IG epic"):
        load_capture_universe(path)


def test_capture_candidates_reject_provider_authority(tmp_path: Path) -> None:
    path = tmp_path / "candidates.toml"
    path.write_text(
        """
name = "unsafe-candidates"
[[instrument]]
id = "fx:eur-usd"
display_name = "EUR/USD"
asset_class = "FX"
base_currency = "EUR"
quote_currency = "USD"
search_aliases = ["EUR/USD"]
preferred_epic = "must-not-be-accepted"
"""
    )

    with pytest.raises(ValueError, match="must not contain preferred IG epics"):
        load_capture_candidates(path)
