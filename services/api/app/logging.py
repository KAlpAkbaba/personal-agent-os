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

from app.research.forbidden_keys import is_forbidden_key
from app.security.redaction import redact_value

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)


def add_correlation_ids(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: inject trace_id/task_id contextvars into every event."""
    event_dict["trace_id"] = trace_id_var.get()
    event_dict["task_id"] = task_id_var.get()
    return event_dict


#: Values under a key whose NAME means "credential" are replaced outright rather than
#: pattern-scanned: `{"password": "hunter2"}` carries a secret that no pattern in
#: SECRET_PATTERNS can recognise on its own, because the key is the only thing that says
#: what the value is. The key vocabulary is `app.research.forbidden_keys`' — the same token
#: list the browser worker and the Cloud Core already agree on — so this pipeline cannot
#: drift into a fourth opinion about what a credential-shaped name looks like.
REDACTED_BY_KEY = "[REDACTED:key_name]"

#: Only STRING values are replaced by key name. A count is not a credential: `tokens: 1430`
#: from an LLM call matches the "token" token and must survive as a number, or this
#: processor would quietly destroy the observability it is protecting.
_REDACT_BY_KEY_TYPES = (str, bytes)


#: Matches app.security.redaction.MAX_REDACT_DEPTH: a log event is a tree, and a walker
#: without a floor is a denial of service waiting for a cyclic-looking structure.
_MAX_DEPTH = 8


def _redact_node(value: Any, depth: int = 0) -> Any:
    """Both rules, applied at every level - not just the top one.

    `redact_value` alone scans strings by PATTERN, which cannot see that `hunter2` is a
    password: only the key says so. Walking here rather than at the top level only is the
    difference between `{"password": "x"}` being caught and `{"body": {"password": "x"}}`
    going out in clear text, and a log event is almost always the second shape.
    """
    if depth > _MAX_DEPTH:
        return "[REDACTED:depth_limit]"
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(item, _REDACT_BY_KEY_TYPES) and is_forbidden_key(key):
                out[key] = REDACTED_BY_KEY
            else:
                out[key] = _redact_node(item, depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [_redact_node(item, depth + 1) for item in value]
    return redact_value(value)


def redact_secrets(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: no secret reaches stdout (B04 req 7/683).

    The thirteen patterns existed and were applied on the security agent's own write paths,
    but nothing applied them to the log line - so an exception string carrying a DSN, a
    header dict, or a `password=` assignment in a captured config went out in clear text.
    Two rules, because a secret arrives in two different shapes:

    * a key whose NAME means credential -> the string value is replaced, whatever it is;
    * every other string -> scanned with the shared patterns and redacted span by span.

    Both are applied at every depth of the event.
    """
    return _redact_node(event_dict)


class SecretRedactingFilter(logging.Filter):
    """The same redaction for everything that goes out through the STANDARD library.

    ``redact_secrets`` only sees events written through structlog, and this process is not
    the only writer: uvicorn's access lines, SQLAlchemy's engine logging and every third-party
    library log through ``logging`` directly, straight past the processor chain. Requirement
    683 is "no secret is logged", not "no secret is logged by our own code", so the same
    patterns are attached to the root handler as well.

    The message is formatted here and the arguments dropped, because a secret in ``args``
    only becomes visible once ``msg % args`` has run - redacting the two separately would
    leave ``"connecting to %s"`` clean and the credential intact in the tuple beside it.
    A traceback is formatted into ``exc_text`` for the same reason: the formatter renders it
    later, by which time no filter runs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_value(record.getMessage())
            record.args = ()
            if record.exc_info and not record.exc_text:
                record.exc_text = redact_value(
                    logging.Formatter().formatException(record.exc_info)
                )
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = redact_value(record.exc_text)
        except Exception:  # noqa: BLE001 - a logging filter must never break logging
            record.msg = "[REDACTION_FAILED]"
            record.args = ()
        return True


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
            # AFTER format_exc_info: the traceback is a plain string by then, and a
            # traceback is one of the likeliest places a DSN appears. Last before the
            # renderer, so nothing added by an earlier processor escapes the scan either.
            redact_secrets,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )

    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    # On the HANDLERS, not the root logger: a filter on a logger is not consulted for
    # records that propagate up from a child (a documented asymmetry in `logging`), and
    # uvicorn's and SQLAlchemy's records are exactly those.
    redactor = SecretRedactingFilter()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
            handler.addFilter(redactor)


def get_logger(name: str) -> FilteringBoundLogger:
    """Named structured logger; the name appears as `logger_name` in every line.

    Uses lazy initial values (not .bind) so module-level loggers created before
    configure_logging() still pick up the JSON configuration at first use.
    ("logger" itself is a reserved wrap_logger argument in structlog.)
    """
    return structlog.get_logger(logger_name=name)
