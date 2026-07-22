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
        "index:australia-200",
        "index:us-500",
        "index:ftse-100",
        "index:us-tech-100",
        "index:wall-street",
        "index:germany-40",
        "index:japan-225",
        "index:eu-stocks-50",
        "commodity:spot-gold",
        "commodity:spot-silver",
        "crypto:bitcoin-usd",
        "commodity:us-crude",
    )
    assert len(candidates.instruments) == 20
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


def test_capture_candidates_bound_provider_search_work(tmp_path: Path) -> None:
    too_many = tmp_path / "too-many.toml"
    entries = []
    for index in range(101):
        entries.append(
            f"""
[[instrument]]
id = "fx:fixture-{index}"
display_name = "Fixture {index}"
asset_class = "FX"
base_currency = "EUR"
quote_currency = "USD"
search_aliases = ["Fixture {index}"]
"""
        )
    too_many.write_text('name = "too-many"\n' + "".join(entries))

    with pytest.raises(ValueError, match="cannot exceed 100 instruments"):
        load_capture_candidates(too_many)

    too_many_aliases = tmp_path / "too-many-aliases.toml"
    too_many_aliases.write_text(
        """
name = "too-many-aliases"
[[instrument]]
id = "fx:eur-usd"
display_name = "EUR/USD"
asset_class = "FX"
base_currency = "EUR"
quote_currency = "USD"
search_aliases = ["one", "two", "three", "four", "five", "six"]
"""
    )

    with pytest.raises(ValueError, match="cannot exceed five search aliases"):
        load_capture_candidates(too_many_aliases)


def test_capture_v3_adds_reviewed_hang_seng_to_capture_v2() -> None:
    previous = load_capture_universe(Path("config/capture-v2.toml"))
    universe = load_capture_universe(Path("config/capture-v3.toml"))
    addition = load_capture_candidates(Path("config/capture-v3-hang-seng-candidate.toml"))

    assert universe.name == "capture-v3"
    assert len(universe.instruments) == 20
    assert tuple(universe.instruments[:-1]) == previous.instruments
    assert str(universe.instruments[-1].instrument_id) == "index:hong-kong-hs50"
    assert universe.preferred_epics[universe.instruments[-1].instrument_id] == (
        "IX.D.HANGSENG.IFM.IP"
    )
    assert universe.configuration_hash == (
        "50202ef7218f1d9816ebc88673259ecb5470f9360abe6b40f1f730c06d712836"
    )
    assert addition.configuration_hash == (
        "6bfcf421e650551bddfc3c39326933e7a1f6bc3c58c72b638409aa1d74f09613"
    )
