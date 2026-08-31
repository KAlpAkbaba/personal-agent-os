"""Structured JSON logging with trace_id / task_id context propagation.

Every log line emitted through structlog carries:
- trace_id: request correlation ID (honors incoming X-Trace-Id header)
- task_id: durable task correlation ID (set by workers/workflows when known)

M0 acceptance: "structured logging has trace/task IDs".
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.typing import FilteringBoundLogger

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)


def add_correlation_ids(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: inject trace_id/task_id contextvars into every event."""
    event_dict["trace_id"] = trace_id_var.get()
    event_dict["task_id"] = task_id_var.get()
    return event_dict


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    """Configure structlog for structured JSON output on stdout."""
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
            add_correlation_ids,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )

    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")


def get_logger(name: str) -> FilteringBoundLogger:
    """Named structured logger; the name appears as `logger_name` in every line.

    Uses lazy initial values (not .bind) so module-level loggers created before
    configure_logging() still pick up the JSON configuration at first use.
    ("logger" itself is a reserved wrap_logger argument in structlog.)
    """
    return structlog.get_logger(logger_name=name)
