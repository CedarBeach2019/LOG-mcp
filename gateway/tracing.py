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

        request.state.trace_id = trace_id
        request.state.start_time = t0

        # Rate limit on chat endpoint
        if "/v1/chat/completions" in request.url.path and request.method == "POST":
            from gateway.rate_limit import get_limiter
            client_ip = request.client.host if request.client else "unknown"
            allowed, info = get_limiter().check(client_ip)
            if not allowed:
                from starlette.responses import JSONResponse
                return JSONResponse(
                    {"error": "Rate limit exceeded", "detail": info},
                    status_code=429,
                    headers={"Retry-After": str(int(info.get("reset_at", 0) - time.monotonic()) + 1)},
                )

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
