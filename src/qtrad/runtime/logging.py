"""Small structured and secret-safe logging configuration."""

import json
import logging
from datetime import UTC, datetime

_REDACTED_FRAGMENTS = {"password", "api_key", "apikey", "token", "cst", "secret"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _STANDARD_LOG_FIELDS:
                continue
            normalised = key.lower().replace("-", "_")
            payload[key] = (
                "[REDACTED]"
                if any(fragment in normalised for fragment in _REDACTED_FRAGMENTS)
                else value
            )
        return json.dumps(payload, default=str, sort_keys=True)


_STANDARD_LOG_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
