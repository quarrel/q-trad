import json
import logging

import pytest
from pydantic import ValidationError

from qtrad.runtime.logging import JsonFormatter
from qtrad.runtime.settings import Settings


def test_production_ig_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"ig_environment": "live"})


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
