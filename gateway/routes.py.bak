"""Starlette route handlers for the LOG-mcp gateway."""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from gateway.auth import create_token, get_jwt_secret, verify_token
from gateway.deps import get_reallog, get_settings
from vault.core import Dehydrator, Rehydrator

logger = logging.getLogger("gateway.routes")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _authenticate(request: Request) -> JSONResponse | None:
    """Return a 401 response if the bearer token is missing or invalid.

    Returns ``None`` when authentication succeeds.
    """
    auth_header: str | None = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "missing or invalid Authorization header"}, status_code=401)

    token = auth_header[7:]
    reallog = get_reallog()
    secret = get_jwt_secret(reallog)
    payload = verify_token(token, secret)
    if payload is None:
        return JSONResponse({"error": "invalid or expired token"}, status_code=401)
    return None


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def serve_index(request: Request) -> PlainTextResponse:
    """Serve the SPA landing page."""
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return PlainTextResponse(index_path.read_text(), media_type="text/html")
    return PlainTextResponse("LOG-mcp gateway — index.html not found", status_code=404)


async def login(request: Request) -> JSONResponse:
    """POST /auth/login — exchange a passphrase for a JWT."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    passphrase = body.get("passphrase", "")
    settings = get_settings()
    if not secrets.compare_digest(passphrase, settings.passphrase):
        return JSONResponse({"error": "invalid passphrase"}, status_code=401)

    reallog = get_reallog()
    secret = get_jwt_secret(reallog)
    token = create_token(secret)
    return JSONResponse({"token": token})


async def chat_completions(request: Request) -> JSONResponse:
    """POST /v1/chat/completions — proxy to upstream LLM with dehydration."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    messages: list[dict] = body.get("messages", [])
    model: str = body.get("model", "default")

    settings = get_settings()
    reallog = get_reallog()
    dehydrator = Dehydrator(reallog=reallog)

    # Dehydrate every user / assistant message content
    dehydrator = Dehydrator(reallog=reallog)
    rehydrator = Rehydrator(reallog=reallog)

    dehydrated_messages: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            dehydrated_text, _ = dehydrator.dehydrate(content)
            content = dehydrated_text
        dehydrated_messages.append({"role": role, "content": content})

    # Prepend the entity preamble as a system message
    preamble = dehydrator.build_preamble()
    upstream_messages = [{"role": "system", "content": preamble}] + dehydrated_messages

    # Forward to upstream
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                settings.provider_endpoint,
                headers={
                    "Authorization": f"Bearer {settings.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": upstream_messages},
            )
    except httpx.TimeoutException:
        return JSONResponse({"error": "upstream timeout"}, status_code=504)
    except httpx.HTTPError as exc:
        logger.error("upstream connection error: %s", exc)
        return JSONResponse({"error": "upstream connection failed"}, status_code=502)

    if resp.status_code == 429:
        return JSONResponse({"error": "rate limited by upstream"}, status_code=429)
    if resp.status_code >= 500:
        return JSONResponse({"error": "upstream server error"}, status_code=502)
    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"upstream returned {resp.status_code}"},
            status_code=502,
        )

    try:
        upstream_data = resp.json()
    except Exception:
        return JSONResponse({"error": "invalid upstream response"}, status_code=502)

    # Rehydrate assistant content in the response
    choices = upstream_data.get("choices", [])
    for choice in choices:
        msg = choice.get("message", {})
        if isinstance(msg.get("content"), str):
            msg["content"] = rehydrator.rehydrate(msg["content"])

    return JSONResponse(upstream_data)


async def stats(request: Request) -> JSONResponse:
    """GET /stats — return entity and session counts."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err

    reallog = get_reallog()
    entity_count = reallog.db.execute("SELECT COUNT(*) AS n FROM pii_map").fetchone()["n"]
    session_count = reallog.db.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    return JSONResponse({"entities": entity_count, "sessions": session_count})
