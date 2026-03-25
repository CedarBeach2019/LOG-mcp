"""Starlette application setup — Phase 2."""

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from gateway.routes import (
    chat_completions,
    drafts,
    elaborate,
    feedback,
    health,
    login,
    preferences_delete,
    preferences_list,
    preferences_set,
    profiles_create,
    profiles_delete as profiles_delete_route,
    profiles_list,
    routing_history,
    routing_stats,
    routing_suggest,
    routing_update,
    serve_index,
    stats,
)

app = Starlette(
    routes=[
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
        Route("/stats", stats, methods=["GET"]),
        Route("/v1/stats/routing", routing_stats, methods=["GET"]),
        Route("/v1/routing/suggest", routing_suggest, methods=["POST"]),
        Route("/v1/routing/update", routing_update, methods=["POST"]),
        Route("/v1/routing/history", routing_history, methods=["GET"]),
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
