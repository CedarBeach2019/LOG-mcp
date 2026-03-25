"""Request tracing middleware for Starlette."""

from __future__ import annotations

import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("gateway.trace")


class TracingMiddleware(BaseHTTPMiddleware):
    """Adds request tracing to all endpoints."""

    async def dispatch(self, request: Request, call_next):
        t0 = time.monotonic()
        trace_id = f"{id(request):x}"[:8]

        # Add trace ID to request state for access in handlers
        request.state.trace_id = trace_id
        request.state.start_time = t0

        logger.info("[%s] → %s %s", trace_id, request.method, request.url.path)

        try:
            response = await call_next(request)
            elapsed = (time.monotonic() - t0) * 1000

            # Add timing headers
            response.headers["X-Request-Id"] = trace_id
            response.headers["X-Response-Time-Ms"] = f"{elapsed:.0f}"

            # Log completion
            level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(level, "[%s] ← %s %d (%.0fms)",
                       trace_id, request.url.path, response.status_code, elapsed)

            # Record in metrics
            try:
                from gateway.observability import MetricsCollector
                MetricsCollector.record_request(
                    path=request.url.path,
                    status_code=response.status_code,
                    latency_ms=elapsed,
                )
            except Exception:
                pass

            return response
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("[%s] ✗ %s %s (%.0fms)",
                         trace_id, request.url.path, type(exc).__name__, elapsed)
            raise
