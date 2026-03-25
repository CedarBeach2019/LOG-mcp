"""Starlette application setup — Phase 2."""

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from gateway.routes import (
    chat_completions,
    feedback,
    health,
    login,
    preferences_delete,
    preferences_list,
    preferences_set,
    serve_index,
    stats,
)

app = Starlette(
    routes=[
        Route("/", serve_index, methods=["GET"]),
        Route("/auth/login", login, methods=["POST"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/feedback", feedback, methods=["POST"]),
        Route("/v1/preferences", preferences_list, methods=["GET"]),
        Route("/v1/preferences", preferences_set, methods=["POST"]),
        Route("/v1/preferences/{key}", preferences_delete, methods=["DELETE"]),
        Route("/v1/health", health, methods=["GET"]),
        Route("/stats", stats, methods=["GET"]),
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
