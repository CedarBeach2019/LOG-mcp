"""Starlette route handlers for the LOG-mcp gateway — Phase 2."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from gateway.auth import create_token, get_jwt_secret, verify_token
from gateway.deps import get_reallog, get_settings
from vault.core import Dehydrator, Rehydrator
from vault.routing_script import classify, resolve_action

logger = logging.getLogger("gateway.routes")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _authenticate(request: Request) -> JSONResponse | None:
    """Check JWT auth. Returns error response or None if valid."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "missing token"}, status_code=401)
    token = auth[7:]
    reallog = get_reallog()
    secret = get_jwt_secret(reallog)
    payload = verify_token(token, secret)
    if payload is None:
        return JSONResponse({"error": "invalid or expired token"}, status_code=401)
    return None


async def serve_index(request: Request) -> JSONResponse:
    """GET / — serve the chat UI."""
    return JSONResponse(
        open(WEB_DIR / "index.html").read(),
        media_type="text/html",
    )


async def login(request: Request) -> JSONResponse:
    """POST /auth/login — exchange passphrase for JWT."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    passphrase = body.get("passphrase", "")
    settings = get_settings()
    if passphrase != settings.passphrase:
        return JSONResponse({"error": "invalid passphrase"}, status_code=401)

    reallog = get_reallog()
    secret = get_jwt_secret(reallog)
    token = create_token(secret)
    return JSONResponse({"token": token})


async def _call_model(endpoint: str, api_key: str, model: str,
                      messages: list[dict], timeout: float = 60.0) -> tuple[int, dict | None, str]:
    """Call an OpenAI-compatible API. Returns (status_code, json_data, error_str)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": messages},
                timeout=timeout,
            )
        if resp.status_code != 200:
            return resp.status_code, None, f"upstream returned {resp.status_code}"
        return 200, resp.json(), ""
    except httpx.TimeoutException:
        return 0, None, "upstream timeout"
    except httpx.HTTPError as exc:
        return 0, None, f"upstream connection failed: {exc}"


async def chat_completions(request: Request) -> JSONResponse:
    """POST /v1/chat/completions — dehydrate, route, call model(s), rehydrate."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err

    settings = get_settings()
    reallog = get_reallog()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)

    # Get user input (last user message)
    user_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break

    # --- PII dehydration ---
    dehydrator = Dehydrator(reallog=reallog)
    rehydrator = Rehydrator(reallog=reallog)

    dehydrated_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str) and role in ("user", "system"):
            content, _ = dehydrator.dehydrate(content)
        dehydrated_messages.append({"role": role, "content": content})

    # --- Routing ---
    has_code_blocks = "```" in user_content
    route = classify(user_content, len(user_content), has_code_blocks)

    endpoint_type, model_name = resolve_action(
        route["action"], settings.cheap_model_name, settings.escalation_model_name
    )

    # Override endpoint based on type
    if endpoint_type == "cheap" or endpoint_type == "escalation":
        endpoint = (settings.cheap_model_endpoint if endpoint_type == "cheap"
                    else settings.escalation_model_endpoint)
    else:
        endpoint = settings.cheap_model_endpoint

    # Build system message with preferences
    prefs = reallog.get_preferences()
    prefs_text = ", ".join(f"{k}={v}" for k, v in prefs.items())
    preamble = Dehydrator.build_preamble()
    system_msg = {"role": "system", "content": f"{preamble}\n\nUser preferences: {prefs_text}"}
    upstream_messages = [system_msg] + dehydrated_messages

    api_key = settings.api_key
    if not api_key:
        return JSONResponse({"error": "no API key configured"}, status_code=500)

    # --- Call model(s) ---
    t0 = time.time()
    escalation_response = None
    escalation_latency = None

    if endpoint_type == "compare":
        # Fire both in parallel
        cheap_status, cheap_data, cheap_err = await _call_model(
            settings.cheap_model_endpoint, api_key, settings.cheap_model_name,
            upstream_messages
        )
        esc_status, esc_data, esc_err = await _call_model(
            settings.escalation_model_endpoint, api_key, settings.escalation_model_name,
            upstream_messages
        )
        cheap_latency = int((time.time() - t0) * 1000)

        if cheap_status == 200 and cheap_data:
            response_text = cheap_data["choices"][0]["message"]["content"]
            route["target_model"] = settings.cheap_model_name
        else:
            response_text = f"Error: {cheap_err or esc_err}"
            route["target_model"] = settings.cheap_model_name
        if esc_status == 200 and esc_data:
            escalation_response = esc_data["choices"][0]["message"]["content"]
            escalation_latency = int((time.time() - t0) * 1000)
    else:
        # Single model call
        status, data, err = await _call_model(endpoint, api_key, model_name, upstream_messages)
        latency = int((time.time() - t0) * 1000)

        if status != 200 or data is None:
            return JSONResponse({"error": err or "unknown error"}, status_code=502)

        response_text = data["choices"][0]["message"]["content"]
        route["target_model"] = model_name
        route["response_latency_ms"] = latency

    # --- Rehydrate ---
    response_text = rehydrator.rehydrate(response_text)
    if escalation_response:
        escalation_response = rehydrator.rehydrate(escalation_response)

    # --- Store interaction ---
    session_id = "session_" + str(int(time.time()))
    interaction_id = reallog.add_interaction(
        session_id=session_id,
        user_input=user_content,
        route_action=route["action"],
        route_reason=route.get("reason", ""),
        target_model=route.get("target_model", model_name),
        response=response_text,
        escalation_response=escalation_response,
        response_latency_ms=route.get("response_latency_ms", int((time.time() - t0) * 1000)),
        escalation_latency_ms=escalation_latency,
    )

    # --- Build response ---
    result = {
        "choices": [{"message": {"role": "assistant", "content": response_text}}],
        "route": {
            "action": route["action"],
            "reason": route.get("reason", ""),
            "target_model": route.get("target_model", model_name),
            "confidence": route.get("confidence", 0),
        },
        "interaction_id": interaction_id,
    }
    if escalation_response:
        result["escalation_response"] = escalation_response

    return JSONResponse(result)


async def feedback(request: Request) -> JSONResponse:
    """POST /v1/feedback — thumbs up/down for an interaction."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    interaction_id = body.get("interaction_id")
    fb = body.get("feedback")  # "up" or "down"
    critique = body.get("critique")

    if not interaction_id or fb not in ("up", "down"):
        return JSONResponse({"error": "interaction_id and feedback (up/down) required"}, status_code=400)

    reallog = get_reallog()
    interaction = reallog.get_interaction(interaction_id)
    if interaction is None:
        return JSONResponse({"error": "interaction not found"}, status_code=404)

    reallog.update_feedback(interaction_id, fb, critique)
    return JSONResponse({"ok": True, "interaction_id": interaction_id})


async def preferences_list(request: Request) -> JSONResponse:
    """GET /v1/preferences — list all preferences."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err
    reallog = get_reallog()
    return JSONResponse(reallog.get_preferences())


async def preferences_set(request: Request) -> JSONResponse:
    """POST /v1/preferences — upsert a preference."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    key = body.get("key")
    value = body.get("value")
    if not key or value is None:
        return JSONResponse({"error": "key and value required"}, status_code=400)

    reallog = get_reallog()
    reallog.set_preference(key, value)
    return JSONResponse({"ok": True, "key": key})


async def preferences_delete(request: Request) -> JSONResponse:
    """DELETE /v1/preferences/{key} — remove a preference."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err

    key = request.path_params.get("key", "")
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)

    reallog = get_reallog()
    deleted = reallog.delete_preference(key)
    return JSONResponse({"ok": deleted, "key": key})


async def stats(request: Request) -> JSONResponse:
    """GET /stats — return system statistics."""
    auth_err = _authenticate(request)
    if auth_err is not None:
        return auth_err

    reallog = get_reallog()
    entity_count = reallog.db.execute("SELECT COUNT(*) AS n FROM pii_map").fetchone()["n"]
    session_count = reallog.db.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    interaction_count = reallog.db.execute("SELECT COUNT(*) AS n FROM interactions").fetchone()["n"]
    thumbs_up = reallog.db.execute(
        "SELECT COUNT(*) AS n FROM interactions WHERE feedback='up'"
    ).fetchone()["n"]
    thumbs_down = reallog.db.execute(
        "SELECT COUNT(*) AS n FROM interactions WHERE feedback='down'"
    ).fetchone()["n"]
    return JSONResponse({
        "entities": entity_count,
        "sessions": session_count,
        "interactions": interaction_count,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
    })


async def health(request: Request) -> JSONResponse:
    """GET /v1/health — check service availability."""
    settings = get_settings()
    results = {}

    # Check cheap model
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                settings.cheap_model_endpoint.rsplit("/chat/completions", 1)[0].rsplit("/v1", 1)[0]
                    or settings.cheap_model_endpoint,
                timeout=3.0,
            )
        results["cheap"] = True
    except Exception:
        results["cheap"] = False

    # Check Ollama
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
            results["ollama"] = resp.status_code == 200
    except Exception:
        results["ollama"] = False

    return JSONResponse(results)
