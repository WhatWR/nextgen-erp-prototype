"""Structured logging with cross-service correlation.

One correlation ID is generated at channel entry in Frappe and propagated to
dispatch, runtime logs, tool calls, proposals and result documents. Every log
line the runtime emits carries it, so a single operation can be followed across
both services.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ..redaction import redact

LOGGER_NAME = "nextgen_agent_runtime"

_context: ContextVar[dict[str, Any]] = ContextVar("nextgen_log_context", default={})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_context.get())
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Attach correlation fields to every log line emitted inside the block."""
    current = dict(_context.get())
    current.update({key: value for key, value in fields.items() if value is not None})
    token = _context.set(current)
    try:
        yield
    finally:
        _context.reset(token)


def log(level: int, message: str, **context: Any) -> None:
    get_logger().log(level, message, extra={"context": context})


def info(message: str, **context: Any) -> None:
    log(logging.INFO, message, **context)


def warning(message: str, **context: Any) -> None:
    log(logging.WARNING, message, **context)


def error(message: str, **context: Any) -> None:
    log(logging.ERROR, message, **context)
