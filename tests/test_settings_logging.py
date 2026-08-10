import json
import logging

import pytest
from pydantic import ValidationError

from qtrad.runtime.logging import JsonFormatter, configure_logging
from qtrad.runtime.settings import Settings


def test_production_ig_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"ig_environment": "live"})


@pytest.mark.parametrize("source_id", ["OCI-capture", "contains space", ""])
def test_capture_source_id_is_bounded_and_machine_safe(source_id: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"capture_source_id": source_id})


def test_ibkr_historical_client_id_is_required_positive_and_distinct() -> None:
    with pytest.raises(ValueError, match="require QTRAD_IBKR_HISTORICAL_CLIENT_ID"):
        Settings().require_ibkr_historical_client_id()
    with pytest.raises(ValidationError, match="must differ"):
        Settings(ibkr_client_id=71, ibkr_historical_client_id=71)
    with pytest.raises(ValidationError, match="must be positive"):
        Settings(ibkr_historical_client_id=0)
    assert Settings(ibkr_historical_client_id=72).require_ibkr_historical_client_id() == 72


def test_logging_redacts_secret_named_fields() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "credential_check",
        (),
        None,
    )
    record.api_key = "secret"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["api_key"] == "[REDACTED]"


def test_trading_ig_info_logs_are_suppressed() -> None:
    logger = logging.getLogger("trading_ig")
    original_level = logger.level
    try:
        configure_logging("INFO")
        assert logger.level == logging.WARNING
    finally:
        logger.setLevel(original_level)


def test_ibapi_info_logs_are_suppressed() -> None:
    logger = logging.getLogger("ibapi")
    original_level = logger.level
    try:
        configure_logging("INFO")
        assert logger.level == logging.WARNING
    finally:
        logger.setLevel(original_level)
