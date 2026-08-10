"""Canonical instruments and effective provider listings."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.time import require_utc


class AssetClass(StrEnum):
    FX = "FX"
    INDEX = "INDEX"
    COMMODITY = "COMMODITY"
    CRYPTO = "CRYPTO"


class ProductType(StrEnum):
    SPOT_FX = "SPOT_FX"
    ROLLING_CFD = "ROLLING_CFD"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: InstrumentId
    display_name: str
    asset_class: AssetClass
    base_currency: str | None
    quote_currency: str
    search_aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.display_name or not self.quote_currency:
            raise ValueError("display name and quote currency are required")
        if not self.search_aliases:
            raise ValueError("at least one provider search alias is required")


@dataclass(frozen=True, slots=True)
class ProviderListing:
    listing_id: ProviderListingId
    instrument_id: InstrumentId
    display_name: str
    product_type: ProductType
    currency: str
    minimum_deal_size: Decimal
    price_increment: Decimal | None
    valid_from: datetime
    valid_to: datetime | None
    metadata_version: str
    economics: Mapping[str, JsonValue] = field(
        default_factory=lambda: cast(Mapping[str, JsonValue], {})
    )

    def __post_init__(self) -> None:
        require_utc(self.valid_from, "valid_from")
        if self.valid_to is not None:
            require_utc(self.valid_to, "valid_to")
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be later than valid_from")
        if self.minimum_deal_size <= 0:
            raise ValueError("minimum deal size must be positive")


INITIAL_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument(
        InstrumentId("fx:aud-usd"), "AUD/USD", AssetClass.FX, "AUD", "USD", ("AUD/USD", "AUDUSD")
    ),
    Instrument(
        InstrumentId("fx:eur-usd"), "EUR/USD", AssetClass.FX, "EUR", "USD", ("EUR/USD", "EURUSD")
    ),
    Instrument(
        InstrumentId("fx:usd-jpy"), "USD/JPY", AssetClass.FX, "USD", "JPY", ("USD/JPY", "USDJPY")
    ),
    Instrument(
        InstrumentId("fx:gbp-usd"), "GBP/USD", AssetClass.FX, "GBP", "USD", ("GBP/USD", "GBPUSD")
    ),
    Instrument(
        InstrumentId("fx:usd-chf"), "USD/CHF", AssetClass.FX, "USD", "CHF", ("USD/CHF", "USDCHF")
    ),
    Instrument(
        InstrumentId("fx:usd-cad"), "USD/CAD", AssetClass.FX, "USD", "CAD", ("USD/CAD", "USDCAD")
    ),
    Instrument(
        InstrumentId("fx:nzd-usd"), "NZD/USD", AssetClass.FX, "NZD", "USD", ("NZD/USD", "NZDUSD")
    ),
    Instrument(
        InstrumentId("fx:eur-jpy"), "EUR/JPY", AssetClass.FX, "EUR", "JPY", ("EUR/JPY", "EURJPY")
    ),
    Instrument(
        InstrumentId("index:australia-200"),
        "Australia 200",
        AssetClass.INDEX,
        None,
        "AUD",
        ("Australia 200", "ASX 200"),
    ),
    Instrument(
        InstrumentId("index:us-500"), "US 500", AssetClass.INDEX, None, "USD", ("US 500", "S&P 500")
    ),
    Instrument(
        InstrumentId("index:wall-street"),
        "US 30",
        AssetClass.INDEX,
        None,
        "USD",
        ("US 30", "Dow Jones"),
    ),
    Instrument(
        InstrumentId("index:us-tech-100"),
        "US Tech 100",
        AssetClass.INDEX,
        None,
        "USD",
        ("US Tech 100", "Nasdaq 100"),
    ),
    Instrument(
        InstrumentId("index:ftse-100"),
        "FTSE 100",
        AssetClass.INDEX,
        None,
        "GBP",
        ("FTSE 100", "UK 100"),
    ),
    Instrument(
        InstrumentId("index:germany-40"),
        "Germany 40",
        AssetClass.INDEX,
        None,
        "EUR",
        ("Germany 40", "DAX"),
    ),
    Instrument(
        InstrumentId("index:japan-225"),
        "Japan 225",
        AssetClass.INDEX,
        None,
        "JPY",
        ("Japan 225", "Nikkei 225"),
    ),
    Instrument(
        InstrumentId("index:eu-stocks-50"),
        "EU Stocks 50",
        AssetClass.INDEX,
        None,
        "EUR",
        ("EU Stocks 50", "Euro Stoxx 50"),
    ),
    Instrument(
        InstrumentId("index:hong-kong-hs50"),
        "Hong Kong HS50",
        AssetClass.INDEX,
        None,
        "HKD",
        ("Hong Kong HS50", "Hang Seng 50"),
    ),
    Instrument(
        InstrumentId("commodity:spot-gold"),
        "Gold",
        AssetClass.COMMODITY,
        "XAU",
        "USD",
        ("Gold", "Spot Gold", "XAU/USD", "XAUUSD"),
    ),
    Instrument(
        InstrumentId("commodity:spot-silver"),
        "Silver",
        AssetClass.COMMODITY,
        "XAG",
        "USD",
        ("Silver", "Spot Silver", "XAG/USD", "XAGUSD"),
    ),
    Instrument(
        InstrumentId("commodity:us-crude"),
        "US Crude",
        AssetClass.COMMODITY,
        None,
        "USD",
        ("US Crude", "WTI", "Crude Oil"),
    ),
)

INSTRUMENTS_BY_ID = {instrument.instrument_id: instrument for instrument in INITIAL_INSTRUMENTS}
