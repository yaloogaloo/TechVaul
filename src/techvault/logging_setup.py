"""Process-wide logger. The log directory comes from PathLayout."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from techvault.redact import redact

_LOGGER_NAME = "techvault"


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "techvault.log").resolve()
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    already = False
    for handler in logger.handlers:
        base = getattr(handler, "baseFilename", None)
        if base is not None and Path(base).resolve() == log_file:
            already = True
            break
    if not already:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    return logger
