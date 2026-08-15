"""Central logging setup for the desktop process."""

import logging
from typing import TextIO

LOGGER_NAME = "skywriter"
DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(
    level: int = logging.INFO,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure and return the application logger without duplicating handlers."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
        logger.addHandler(handler)

    return logger
