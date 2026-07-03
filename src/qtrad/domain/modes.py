"""Independent runtime-mode dimensions."""

from enum import StrEnum


class ExecutionMode(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class CapitalMode(StrEnum):
    ALLOCATED = "ALLOCATED"
    SHADOW = "SHADOW"
    DISABLED = "DISABLED"


class DataMode(StrEnum):
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"


class BrokerEnvironment(StrEnum):
    NONE = "NONE"
    IG_DEMO = "IG_DEMO"
    IG_LIVE = "IG_LIVE"
    IBKR_PAPER = "IBKR_PAPER"
    IBKR_LIVE = "IBKR_LIVE"


class RunKind(StrEnum):
    INGESTION = "INGESTION"
    REPLAY = "REPLAY"
    BACKFILL = "BACKFILL"
    EXPORT = "EXPORT"
