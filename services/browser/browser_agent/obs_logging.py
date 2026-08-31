"""Structured JSON logging for the browser agent (structlog).

Mirrors the services/api logging conventions: JSON lines on stdout with ISO
timestamps and a `logger_name` field. Operation logs carry op name, target
spec, duration_ms and error_class.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import FilteringBoundLogger

_configured = False


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    """Configure structlog for structured JSON output on stdout."""
    global _configured
    renderer: structlog.typing.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str) -> FilteringBoundLogger:
    """Named structured logger; auto-configures JSON output on first use."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(logger_name=name)
