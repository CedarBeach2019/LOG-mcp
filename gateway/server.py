"""Starlette application setup — Phase 2."""

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from gateway.routes import (
    dataset_deduplicate,
    dataset_export,
    dataset_score,
    dataset_stats,
    prompt_preview,
    prompt_template_update,
    prompt_templates_list,
    training_export,
    training_status,
    cache_clear,
    cache_stats,
    chat_completions,
    config_get,
    config_set,
    config_validate,
    drafts,
    elaborate,
    feedback,
    health,
    login,
    local_model_load,
    local_model_status,
    local_model_unload,
    local_models_list,
    metrics_dashboard,
    adaptive_dashboard,
    adaptive_health,
    adaptive_suggest,
    model_catalog,
    model_download,
    migrate,
    preferences_delete,
    preferences_list,
    preferences_set,
    profiles_create,
    profiles_delete as profiles_delete_route,
    profiles_list,
    routing_history,
    routing_optimize,
    routing_rules_list,
    routing_stats,
    routing_suggest,
    routing_update,
    serve_index,
    session_create,
    session_delete,
    session_get,
    sessions_list,
    stats,
)

from gateway.tracing import TracingMiddleware
from gateway.startup import validate_startup
from gateway.rate_limit import get_limiter
from starlette.middleware.base import BaseHTTPMiddleware

# Startup validation
from gateway.deps import get_settings
import os
import logging
_logger = logging.getLogger("gateway.server")

_settings = get_settings()
_startup_warnings = validate_startup(_settings)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce rate limits per client IP."""

    async def dispatch(self, request, call_next):
        if request.url.path in ("/", "/v1/health"):
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        limiter = get_limiter()
        allowed, info = limiter.check(client_ip)
        if not allowed:
            from starlette.responses import JSONResponse
            resp = JSONResponse(
                {"error": "rate limited", "reason": info.get("reason", ""),
                 "limit": info["limit"], "reset_at": info["reset_at"]},
                status_code=429,
            )
            resp.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp.headers["X-RateLimit-Remaining"] = "0"
            return resp
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        return response


class BodySizeMiddleware(BaseHTTPMiddleware):
    """Reject request bodies exceeding LOG_MAX_BODY_SIZE (default 1MB)."""

    async def dispatch(self, request, call_next):
        max_size = int(os.environ.get("LOG_MAX_BODY_SIZE", str(1024 * 1024)))
        if request.method in ("POST", "PUT", "PATCH"):
            cl = request.headers.get("content-length")
            if cl and int(cl) > max_size:
                from starlette.responses import JSONResponse
                return JSONResponse(
                    {"error": f"request body too large (max {max_size} bytes)"},
                    status_code=413,
                )
        return await call_next(request)

routes = [
    Route("/", serve_index, methods=["GET"]),
    Route("/auth/login", login, methods=["POST"]),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/v1/drafts", drafts, methods=["POST"]),
    Route("/v1/elaborate", elaborate, methods=["POST"]),
    Route("/v1/feedback", feedback, methods=["POST"]),
    Route("/v1/preferences", preferences_list, methods=["GET"]),
    Route("/v1/preferences", preferences_set, methods=["POST"]),
    Route("/v1/preferences/{key}", preferences_delete, methods=["DELETE"]),
    Route("/v1/health", health, methods=["GET"]),
    Route("/v1/profiles", profiles_list, methods=["GET"]),
    Route("/v1/profiles", profiles_create, methods=["POST"]),
    Route("/v1/profiles/{name}", profiles_delete_route, methods=["DELETE"]),
    Route("/v1/local/models", local_models_list, methods=["GET"]),
    Route("/v1/local/load", local_model_load, methods=["POST"]),
    Route("/v1/local/unload", local_model_unload, methods=["POST"]),
    Route("/v1/local/status", local_model_status, methods=["GET"]),
    Route("/v1/cache/stats", cache_stats, methods=["GET"]),
    Route("/v1/cache/clear", cache_clear, methods=["POST"]),
    Route("/stats", stats, methods=["GET"]),
    Route("/v1/stats/routing", routing_stats, methods=["GET"]),
    Route("/v1/routing/suggest", routing_suggest, methods=["POST"]),
    Route("/v1/routing/update", routing_update, methods=["POST"]),
    Route("/v1/routing/history", routing_history, methods=["GET"]),
    Route("/v1/routing/rules", routing_rules_list, methods=["GET"]),
    Route("/v1/routing/optimize", routing_optimize, methods=["POST"]),
    Route("/v1/sessions", sessions_list, methods=["GET"]),
    Route("/v1/sessions", session_create, methods=["POST"]),
    Route("/v1/sessions/{session_id}", session_get, methods=["GET"]),
    Route("/v1/sessions/{session_id}", session_delete, methods=["DELETE"]),
    Route("/v1/metrics", metrics_dashboard, methods=["GET"]),
    Route("/v1/dataset/stats", dataset_stats, methods=["GET"]),
    Route("/v1/dataset/score", dataset_score, methods=["POST"]),
    Route("/v1/dataset/export", dataset_export, methods=["GET"]),
    Route("/v1/dataset/deduplicate", dataset_deduplicate, methods=["POST"]),
    Route("/v1/training/export", training_export, methods=["POST"]),
    Route("/v1/training/status", training_status, methods=["GET"]),
    Route("/v1/config", config_get, methods=["GET"]),
    Route("/v1/config", config_set, methods=["PUT"]),
    Route("/v1/config/validate", config_validate, methods=["POST"]),
    Route("/v1/adaptive/dashboard", adaptive_dashboard, methods=["GET"]),
    Route("/v1/adaptive/health/{model_name}", adaptive_health, methods=["GET"]),
    Route("/v1/adaptive/suggest", adaptive_suggest, methods=["GET"]),
    Route("/v1/local/catalog", model_catalog, methods=["GET"]),
    Route("/v1/local/download", model_download, methods=["POST"]),
    Route("/v1/maintenance/migrate", migrate, methods=["POST"]),
    Route("/v1/prompt/templates", prompt_templates_list, methods=["GET"]),
    Route("/v1/prompt/template/{name}", prompt_template_update, methods=["PUT"]),
    Route("/v1/prompt/preview", prompt_preview, methods=["POST"]),
]


async def _on_shutdown():
    """Graceful shutdown: stop model subprocess, flush metrics."""
    import logging
    logger = logging.getLogger("gateway.shutdown")
    logger.info("Shutting down...")

    # Stop model subprocess (frees GPU memory)
    try:
        from gateway.shared import get_local_manager
        manager = get_local_manager()
        manager.unload()
        logger.info("Model unloaded")
    except Exception as exc:
        logger.warning("Error unloading model: %s", exc)

    # Flush metrics summary
    try:
        from gateway.observability import MetricsCollector
        summary = MetricsCollector.get_summary(minutes=5)
        total = summary.get("total_requests", 0)
        logger.info("Final metrics: %d requests in last 5min", total)
    except Exception:
        pass

    logger.info("Shutdown complete")


async def _not_found(request, exc):
    from starlette.responses import JSONResponse
    return JSONResponse({"error": "not found", "path": request.url.path}, status_code=404)


async def _method_not_allowed(request, exc):
    from starlette.responses import JSONResponse
    return JSONResponse({"error": "method not allowed", "method": request.method}, status_code=405)


app = Starlette(routes=routes, on_shutdown=[_on_shutdown],
                 exception_handlers={404: _not_found, 405: _method_not_allowed})

app.add_middleware(TracingMiddleware)

# CORS: default to localhost:8000, only wildcard if explicitly set
_cors_env = os.environ.get("LOG_CORS_ORIGINS", "")
if _cors_env == "*":
    _logger.warning("CORS set to wildcard (*) — this allows all origins")
    _cors_origins = ["*"]
elif _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",")]
else:
    _cors_origins = ["http://localhost:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BodySizeMiddleware)
app.add_middleware(RateLimitMiddleware)
