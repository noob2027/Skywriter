"""Logging setup tests."""

import io
import logging

from skywriter.logging_config import configure_logging


def test_logging_configuration_is_idempotent() -> None:
    logger = logging.getLogger("skywriter")
    logger.handlers.clear()

    configured = configure_logging(stream=io.StringIO())
    configured_again = configure_logging(stream=io.StringIO())

    assert configured is configured_again
    assert len(configured.handlers) == 1
    assert not configured.propagate
