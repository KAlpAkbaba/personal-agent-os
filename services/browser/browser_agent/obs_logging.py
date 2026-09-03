"""Structured JSON logging for the browser agent (structlog).

Mirrors the services/api logging conventions: JSON lines on stdout with ISO
timestamps and a `logger_name` field. Operation logs carry op name, target
spec, duration_ms and error_class.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog
from structlog.typing import FilteringBoundLogger


def configure_logging(
    *, json_output: bool = True, level: int = logging.INFO, stream: TextIO = sys.stdout
) -> None:
    """Configure structlog for structured JSON output on ``stream``.

    ``stream`` defaults to stdout (existing M2 behavior). The worker CLI
    (``browser_agent.worker``, M13) passes ``sys.stderr`` explicitly: stdout
    is protocol-only (newline-delimited JSON envelopes to the companion), so
    every log line must go to stderr instead (contract §7).
    """
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
        logger_factory=structlog.PrintLoggerFactory(stream),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    """Named structured logger.

    Deliberately does NOT auto-configure structlog. Every ``browser_agent``
    submodule creates its logger at *import* time
    (``logger = get_logger(__name__)`` at module scope) — if this function
    forced a default (stdout) configuration on first call, importing
    ``browser_agent`` at all would silently pin logging to stdout before the
    worker CLI (``browser_agent.worker``, M13) gets a chance to redirect it
    to stderr per contract §7 (stdout is protocol-only: newline-delimited
    JSON envelopes only). structlog's logger proxy resolves configuration
    lazily at the first actual log *call*, and is safe to use unconfigured
    (library default processors/stdout) for ad-hoc/test usage that never
    calls :func:`configure_logging` — callers that care which stream/format
    is used (the worker CLI) call :func:`configure_logging` explicitly, once,
    at process start, before any operation that might log.
    """
    return structlog.get_logger(logger_name=name)
