"""Starlette application setup — Phase 2."""

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from gateway.routes import (
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

# Startup validation
from gateway.deps import get_settings
_settings = get_settings()
_startup_warnings = validate_startup(_settings)

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
    Route("/v1/training/export", training_export, methods=["POST"]),
    Route("/v1/training/status", training_status, methods=["GET"]),
    Route("/v1/config", config_get, methods=["GET"]),
    Route("/v1/config", config_set, methods=["PUT"]),
    Route("/v1/config/validate", config_validate, methods=["POST"]),
    Route("/v1/adaptive/dashboard", adaptive_dashboard, methods=["GET"]),
    Route("/v1/adaptive/health/{model_name}", adaptive_health, methods=["GET"]),
    Route("/v1/adaptive/suggest", adaptive_suggest, methods=["GET"]),
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins.split(",") if _settings.cors_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
