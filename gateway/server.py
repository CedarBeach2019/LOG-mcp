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
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
