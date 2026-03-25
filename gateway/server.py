"""Starlette application factory for the LOG-mcp gateway."""

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from gateway.routes import chat_completions, login, serve_index, stats

app = Starlette(
    routes=[
        Route("/", serve_index, methods=["GET"]),
        Route("/auth/login", login, methods=["POST"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/stats", stats, methods=["GET"]),
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
