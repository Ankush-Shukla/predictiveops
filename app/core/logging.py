"""Structured (JSON or key=value) logging built on the standard library."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in record.__dict__.items() if k not in _RESERVED)
        base = f"{datetime.fromtimestamp(record.created, UTC).isoformat()} " \
               f"{record.levelname} {record.name} {record.getMessage()}"
        text = f"{base} {extras}".rstrip()
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Idempotently configure the root logger."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_predictiveops", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler._predictiveops = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if json_logs else KeyValueFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
