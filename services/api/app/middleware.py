"""FastAPI/Starlette middleware assigning and propagating trace IDs."""

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging import get_logger, trace_id_var

TRACE_HEADER = "X-Trace-Id"
# trace_id lands in String(128) DB columns and log pipelines: constrain the
# client-supplied value instead of surfacing a DB error on abuse.
_TRACE_ID_MAX_LEN = 128
_TRACE_ID_SANITIZE = re.compile(r"[^A-Za-z0-9._-]")

logger = get_logger("app.request")


def sanitize_trace_id(value: str | None) -> str | None:
    """Return a DB/log-safe trace id, or None if nothing usable remains."""
    if not value:
        return None
    cleaned = _TRACE_ID_SANITIZE.sub("", value)[:_TRACE_ID_MAX_LEN]
    return cleaned or None


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Assigns a trace_id per request (honoring an incoming X-Trace-Id header),
    stores it in a contextvar so every log line in the request scope carries it,
    and echoes it back in the response headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = sanitize_trace_id(request.headers.get(TRACE_HEADER))
        trace_id = incoming if incoming else uuid.uuid4().hex
        token = trace_id_var.set(trace_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=elapsed_ms,
            )
        except Exception:
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
            )
            raise
        finally:
            trace_id_var.reset(token)
        response.headers[TRACE_HEADER] = trace_id
        return response
