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
]

app = Starlette(routes=routes)

app.add_middleware(TracingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
