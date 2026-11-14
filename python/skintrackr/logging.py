"""Logging setup: JSON lines on stdout, in the shape Go's `slog.NewJSONHandler` writes.

Pass structured context with `extra`, which becomes top-level keys of the record:

    logger.info("saved new token", extra={"athlete_id": 1234})
    # {"time": "...", "level": "INFO", "msg": "saved new token", "logger": "skintrackr.clients.store", "athlete_id": 1234}
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Attributes every LogRecord has; anything else on a record came from `extra`.
_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "color_message"}

# slog's level names (WARNING is "WARN" there)
_LEVEL_NAMES = {logging.WARNING: "WARN", logging.CRITICAL: "ERROR"}

# Third-party loggers that are chatty at INFO (e.g. httpx logs every request).
_NOISY_LOGGERS = ("httpx", "httpcore", "botocore", "boto3", "s3transfer", "urllib3")


class JsonFormatter(logging.Formatter):
    """Format records as one JSON object per line, with `extra` fields as top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": _LEVEL_NAMES.get(record.levelno, record.levelname),
            "msg": record.getMessage(),
            "logger": record.name,
        }
        entry.update((key, value) for key, value in vars(record).items() if key not in _RECORD_ATTRS)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def get_logger(name: str) -> logging.Logger:
    """Get a logger; pass `__name__` so loggers nest under `skintrackr`."""
    return logging.getLogger(name)


def configure_logging(verbose: bool = False) -> None:
    """Send log records to stdout as JSON lines.

    Args:
        verbose: Log skintrackr at DEBUG instead of INFO. Third-party libraries stay at WARNING either way.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    logging.getLogger("skintrackr").setLevel(logging.DEBUG if verbose else logging.INFO)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # server startup and access logs
    logging.getLogger("uvicorn").setLevel(logging.INFO)
