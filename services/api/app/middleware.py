"""FastAPI/Starlette middleware assigning and propagating trace IDs."""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging import get_logger, trace_id_var

TRACE_HEADER = "X-Trace-Id"

logger = get_logger("app.request")


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Assigns a trace_id per request (honoring an incoming X-Trace-Id header),
    stores it in a contextvar so every log line in the request scope carries it,
    and echoes it back in the response headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(TRACE_HEADER)
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
