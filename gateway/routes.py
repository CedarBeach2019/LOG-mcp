"""LOG-mcp route handlers — split into focused modules.

This file remains the entry point; server.py imports from here.
All handlers delegate to gateway/api_*.py modules.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse

from gateway.shared import authenticate, call_model, get_client, get_local_manager
from gateway.deps import get_reallog, get_settings
from vault.core import Dehydrator, Rehydrator
from vault.draft_profiles import get_draft_profiles
from vault.routing_script import classify, resolve_action

logger = logging.getLogger("gateway.routes")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def login(request: Request):
    """POST /auth/login — exchange passphrase for JWT."""
    from gateway.auth import create_token, get_jwt_secret
    try:
        body = await request.json()
    except Exception:
        from starlette.responses import JSONResponse
        return JSONResponse({"error": "invalid json"}, status_code=400)

    passphrase = body.get("passphrase", "")
    settings = get_settings()
    if passphrase != settings.passphrase:
        from starlette.responses import JSONResponse
        return JSONResponse({"error": "invalid passphrase"}, status_code=401)

    reallog = get_reallog()
    secret = get_jwt_secret(reallog)
    token = create_token(secret)
    from starlette.responses import JSONResponse
    return JSONResponse({"token": token})


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

async def serve_index(request: Request) -> HTMLResponse:
    """GET / — serve the chat UI."""
    return HTMLResponse(open(WEB_DIR / "index.html").read())


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

async def health(request: Request):
    """GET /v1/health — deep health check: DB, API keys, model, disk, memory."""
    from starlette.responses import JSONResponse
    import shutil
    import os

    settings = get_settings()
    results = {"status": "ok", "checks": {}}

    # DB check
    try:
        from vault.core import RealLog
        rl = RealLog(settings.db_path)
        rl.get_preference("_health_check")
        results["checks"]["database"] = {"ok": True, "path": str(settings.db_path)}
    except Exception as exc:
        results["checks"]["database"] = {"ok": False, "error": str(exc)}
        results["status"] = "degraded"

    # API key check (validate by making a lightweight models list call)
    try:
        client = get_client()
        base = settings.cheap_model_endpoint.rsplit("/chat/completions", 1)[0]
        base = base.rsplit("/v1", 1)[0] if "/v1" in base else base
        resp = await client.get(f"{base}/models", timeout=5.0,
                                headers={"Authorization": f"Bearer {settings.api_key}"})
        results["checks"]["api_key"] = {"ok": resp.status_code in (200, 401, 403)}  # reachable
    except Exception as exc:
        results["checks"]["api_key"] = {"ok": False, "error": str(exc)[:80]}
        results["status"] = "degraded"

    # Local model check
    try:
        manager = get_local_manager()
        info = manager.get_loaded_model_info()
        subprocess_alive = manager.get_subprocess_client() is not None
        results["checks"]["local_model"] = {
            "ok": info is not None or subprocess_alive,
            "loaded": info is not None,
            "subprocess": subprocess_alive,
            "name": info.get("model_name") if info else None,
        }
    except Exception as exc:
        results["checks"]["local_model"] = {"ok": False, "error": str(exc)[:80]}

    # Disk space
    try:
        db_dir = Path(settings.db_path).parent
        disk = shutil.disk_usage(str(db_dir))
        free_gb = round(disk.free / (1024**3), 1)
        results["checks"]["disk"] = {
            "ok": free_gb > 0.5,
            "free_gb": free_gb,
            "total_gb": round(disk.total / (1024**3), 1),
        }
        if free_gb <= 0.5:
            results["status"] = "degraded"
    except Exception:
        pass

    # Memory
    try:
        meminfo = Path("/proc/meminfo").read_text()
        for line in meminfo.split("\n"):
            if "MemAvailable" in line:
                avail_kb = int(line.split()[1])
                avail_mb = round(avail_kb / 1024, 0)
                results["checks"]["memory"] = {
                    "ok": avail_mb > 200,
                    "available_mb": avail_mb,
                }
                if avail_mb < 200:
                    results["status"] = "degraded"
                break
    except Exception:
        pass

    # Version / uptime
    results["checks"]["version"] = {"commit": "c976402"}

    status_code = 200 if results["status"] == "ok" else 503
    return JSONResponse(results, status_code=status_code)


# ---------------------------------------------------------------------------
# Chat completions
# ---------------------------------------------------------------------------

async def _call_draft_profile(settings, api_key: str, profile: dict, messages: list[dict]) -> dict:
    """Call a single draft profile. Returns result dict (never raises)."""
    t0 = time.time()
    name = profile["name"]
    if "endpoint" in profile and "model" in profile:
        endpoint, model = profile["endpoint"], profile["model"]
    else:
        endpoint = getattr(settings, profile.get("endpoint_key", ""), "")
        model = getattr(settings, profile.get("model_key", ""), "")
    system_msg = {"role": "system", "content": profile.get("system_prompt", profile.get("system", ""))}

    status, data, err = await call_model(endpoint, api_key, model, [system_msg] + messages,
                                          temperature=profile.get("temperature"))
    latency = int((time.time() - t0) * 1000)

    if status == 200 and data and "choices" in data:
        content = data["choices"][0]["message"]["content"]
        return {"profile": name, "response": content, "model": model, "latency_ms": latency, "error": None}
    return {"profile": name, "response": None, "model": model, "latency_ms": latency,
            "error": err or f"upstream {status}"}


async def chat_completions(request: Request):
    """POST /v1/chat/completions — dehydrate, route, call model(s), rehydrate."""
    from starlette.responses import JSONResponse

    auth_err = authenticate(request)
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

    # --- Session handling ---
    request_session_id = body.get("session_id", "")
    if not request_session_id:
        request_session_id = "s_" + str(int(time.time()))
    # Create session if it doesn't exist
    from datetime import datetime
    from vault.core import Session, Message
    existing = reallog.get_session(request_session_id)
    if not existing:
        reallog.add_session(Session(
            id=request_session_id,
            timestamp=datetime.now().isoformat(),
            summary="",
            metadata={},
        ))
    # Store user message
    try:
        reallog.add_message(Message(
            session_id=request_session_id, role="user",
            content=user_content, timestamp=datetime.now().isoformat(),
        ))
    except Exception:
        pass

    # --- PII dehydration ---
    t0 = time.time()
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

    if endpoint_type == "cheap" or endpoint_type == "escalation":
        endpoint = (settings.cheap_model_endpoint if endpoint_type == "cheap"
                    else settings.escalation_model_endpoint)
    else:
        endpoint = settings.cheap_model_endpoint

    # --- Semantic cache check ---
    if getattr(settings, 'cache_enabled', True) and endpoint_type not in ("compare", "draft"):
        from vault.semantic_cache import _get_cache
        embed_fn = None
        try:
            manager = get_local_manager()
            backend = manager.get_backend()
            subprocess_client = manager.get_subprocess_client()

            if subprocess_client and subprocess_client.is_loaded:
                # Subprocess mode: wrap async embed in sync call
                import asyncio
                client = subprocess_client
                def sync_embed(text):
                    try:
                        return asyncio.run(client.aembed(text))
                    except Exception:
                        return None
                embed_fn = sync_embed
            elif backend and backend.is_loaded:
                embed_fn = backend.embed
        except Exception:
            pass
        cache = _get_cache(settings, embed_fn=embed_fn)
        if cache:
            cached = cache.get(user_content, model_name)
            if cached:
                latency = int((time.time() - t0) * 1000)
                cache_iid = reallog.add_interaction(
                    session_id="cache_" + str(int(time.time())),
                    user_input=user_content, route_action="CACHE_HIT",
                    target_model=model_name, response=cached["response"],
                    response_latency_ms=latency,
                )
                return JSONResponse({
                    "interaction_id": cache_iid,
                    "choices": [{"message": {"role": "assistant", "content": cached["response"]}}],
                    "model": model_name,
                    "route": {"action": "cache_hit", "confidence": 1.0, "badge": "⚡ CACHED"},
                    "cached": True, "latency_ms": latency,
                })

    # Build system message with preferences
    prefs = reallog.get_preferences()
    prefs_text = ", ".join(f"{k}={v}" for k, v in prefs.items())
    preamble = Dehydrator.build_preamble()
    system_msg = {"role": "system", "content": f"{preamble}\n\nUser preferences: {prefs_text}"}
    upstream_messages = [system_msg] + dehydrated_messages

    api_key = settings.api_key
    if not api_key:
        return JSONResponse({"error": "no API key configured"}, status_code=500)

    # --- Streaming path ---
    if body.get("stream"):
        return await _stream_chat(settings, reallog, dehydrator, rehydrator,
                                  upstream_messages, request_session_id, user_content, route,
                                  endpoint, model_name, endpoint_type, api_key, t0)

    # --- Draft redirect ---
    if endpoint_type == "draft":
        profiles = body.get("profiles") or get_draft_profiles(settings)
        dehydrated_messages_2 = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str) and role in ("user", "system"):
                content, _ = dehydrator.dehydrate(content)
            dehydrated_messages_2.append({"role": role, "content": content})
        coros = [_call_draft_profile(settings, api_key, p, dehydrated_messages_2) for p in profiles]
        import asyncio
        results = list(await asyncio.gather(*coros))
        for r in results:
            if r["response"] is not None:
                r["response"] = rehydrator.rehydrate(r["response"])
        session_id_draft = "s_" + str(int(time.time()))
        for r in results:
            reallog.add_interaction(
                session_id=session_id_draft, user_input=user_content,
                route_action="DRAFT", route_reason=f"profile={r['profile']}",
                target_model=r["model"],
                response=r["response"] or f"[error: {r['error']}]",
                response_latency_ms=r["latency_ms"],
            )
        latency = int((time.time() - t0) * 1000)
        return JSONResponse({
            "drafts": results,
            "route": {"action": "draft", "target_model": "multiple"},
            "interaction_id": None,
            "latency_ms": latency,
        })

    # --- Call model(s) ---
    escalation_response = None
    escalation_latency = None

    if endpoint_type == "local":
        manager = get_local_manager()
        backend = manager.get_backend()
        subprocess_client = manager.get_subprocess_client()

        if subprocess_client and subprocess_client.is_loaded:
            local_content = await subprocess_client.agenerate(
                upstream_messages, temperature=0.7,
                max_tokens=getattr(settings, 'local_max_tokens', 512),
            )
        elif backend and backend.is_loaded:
            local_content = await backend.agenerate(
                upstream_messages, temperature=0.7,
                max_tokens=getattr(settings, 'local_max_tokens', 512),
            )
        else:
            logger.warning("Local model not loaded, falling back to cloud")
            endpoint_type = "cheap"
            endpoint = settings.cheap_model_endpoint
            model_name = settings.cheap_model_name
        # If local inference succeeded, process the result
        if endpoint_type == "local" and local_content:
            latency = int((time.time() - t0) * 1000)
            rehydrated = rehydrator.rehydrate(local_content)
            reallog.store_interaction(
                user_input=user_content, model_response=local_content,
                model_name="local", route_action="LOCAL", latency_ms=latency,
            )
            return JSONResponse({
                "choices": [{"message": {"role": "assistant", "content": rehydrated}}],
                "model": "local",
                "route": {"action": "local", "confidence": 1.0, "badge": "🔵 LOCAL"},
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "latency_ms": latency,
            })

    if endpoint_type == "compare":
        cheap_status, cheap_data, cheap_err = await call_model(
            settings.cheap_model_endpoint, api_key, settings.cheap_model_name, upstream_messages)
        esc_status, esc_data, esc_err = await call_model(
            settings.escalation_model_endpoint, api_key, settings.escalation_model_name, upstream_messages)
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
        status, data, err = await call_model(endpoint, api_key, model_name, upstream_messages)
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
    interaction_id = reallog.add_interaction(
        session_id=request_session_id, user_input=user_content,
        route_action=route["action"], route_reason=route.get("reason", ""),
        target_model=route.get("target_model", model_name),
        response=response_text, escalation_response=escalation_response,
        response_latency_ms=route.get("response_latency_ms", int((time.time() - t0) * 1000)),
        escalation_latency_ms=escalation_latency,
    )
    # Auto-summarize session from first user message
    try:
        existing = reallog.get_session(request_session_id)
        if existing and (not existing.summary or existing.summary.strip() == ""):
            summary = user_content[:80] + ("…" if len(user_content) > 80 else "")
            reallog.update_session_summary(request_session_id, summary)
    except Exception:
        pass  # non-critical, don't fail the request
    # Store assistant response in session
    try:
        reallog.add_message(Message(
            session_id=request_session_id, role="assistant",
            content=response_text, timestamp=datetime.now().isoformat(),
        ))
    except Exception:
        pass

    # --- Record for adaptive routing ---
    try:
        from vault.adaptive_routing import get_adaptive_router
        ar = get_adaptive_router()
        ar.record_request(
            model_name=model_name,
            latency_ms=latency if endpoint_type != "local" else 0,
            success=True,
            confidence=route.get("confidence", 0.5),
        )
    except Exception:
        pass

    # --- Store in semantic cache ---
    if getattr(settings, 'cache_enabled', True) and endpoint_type != "compare":
        from vault.semantic_cache import _get_cache
        cache = _get_cache(settings)
        if cache and response_text:
            cache.put(user_content, model_name, response_text)

    result = {
        "choices": [{"message": {"role": "assistant", "content": response_text}}],
        "route": {
            "action": route["action"], "reason": route.get("reason", ""),
            "target_model": route.get("target_model", model_name),
            "confidence": route.get("confidence", 0),
        },
        "interaction_id": interaction_id,
        "session_id": request_session_id,
    }
    if escalation_response:
        result["escalation_response"] = escalation_response
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Drafts & Elaborate
# ---------------------------------------------------------------------------

async def drafts(request: Request):
    """POST /v1/drafts — fire parallel draft calls across profiles."""
    from starlette.responses import JSONResponse
    import asyncio

    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    settings = get_settings()
    reallog = get_reallog()
    api_key = settings.api_key
    if not api_key:
        return JSONResponse({"error": "no API key configured"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)

    profiles = body.get("profiles") or get_draft_profiles(settings)

    dehydrator = Dehydrator(reallog=reallog)
    dehydrated_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str) and role in ("user", "system"):
            content, _ = dehydrator.dehydrate(content)
        dehydrated_messages.append({"role": role, "content": content})

    coros = [_call_draft_profile(settings, api_key, p, dehydrated_messages) for p in profiles]
    results = await asyncio.gather(*coros)

    rehydrator = Rehydrator(reallog=reallog)
    for r in results:
        if r["response"] is not None:
            r["response"] = rehydrator.rehydrate(r["response"])

    user_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break

    session_id = "session_" + str(int(time.time()))
    for r in results:
        reallog.add_interaction(
            session_id=session_id, user_input=user_content,
            route_action="DRAFT", route_reason=f"profile={r['profile']}",
            target_model=r["model"],
            response=r["response"] or f"[error: {r['error']}]",
            response_latency_ms=r["latency_ms"],
        )

    return JSONResponse({"drafts": list(results)})


async def elaborate(request: Request):
    """POST /v1/elaborate — expand a chosen draft into a full response."""
    from starlette.responses import JSONResponse
    import json

    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    settings = get_settings()
    reallog = get_reallog()
    api_key = settings.api_key
    if not api_key:
        return JSONResponse({"error": "no API key configured"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    winner_profile = body.get("winner_profile", "")
    all_drafts = body.get("all_drafts", [])
    user_reasoning = body.get("user_reasoning", "")
    original_messages = body.get("messages", [])
    interaction_id = body.get("interaction_id")

    if not winner_profile or not original_messages:
        return JSONResponse({"error": "winner_profile and messages required"}, status_code=400)

    all_profiles = get_draft_profiles()
    winner_cfg = next((p for p in all_profiles if p["name"] == winner_profile), None)
    if not winner_cfg:
        return JSONResponse({"error": f"unknown profile: {winner_profile}"}, status_code=400)

    if "endpoint" in winner_cfg and "model" in winner_cfg:
        endpoint, model = winner_cfg["endpoint"], winner_cfg["model"]
    else:
        endpoint = getattr(settings, winner_cfg.get("endpoint_key", ""), "")
        model = getattr(settings, winner_cfg.get("model_key", ""), "")

    prefs = reallog.get_preferences()
    prefs_text = ", ".join(f"{k}={v}" for k, v in prefs.items())
    preamble = Dehydrator.build_preamble()

    other_drafts = [d for d in all_drafts if d.get("profile") != winner_profile]
    other_lines = "\n".join(f"- [{d.get('profile', '?')}] {d.get('response', '')}" for d in other_drafts)
    winner_draft = next((d for d in all_drafts if d.get("profile") == winner_profile), {})
    winner_approach = winner_draft.get("response", "")

    system_parts = [
        f"{preamble}\n\nUser preferences: {prefs_text}",
        f"The user saw multiple brief approaches and chose the '{winner_profile}' approach: \"{winner_approach}\"",
    ]
    if other_lines:
        system_parts.append(f"Other approaches considered were:\n{other_lines}")
    if user_reasoning:
        system_parts.append(f"User chose you because: {user_reasoning}")
    system_parts.append("Now give a full, detailed response to the user's question.")
    system_content = "\n\n".join(system_parts)

    dehydrator = Dehydrator(reallog=reallog)
    rehydrator = Rehydrator(reallog=reallog)
    dehydrated_messages = []
    for msg in original_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str) and role in ("user", "system"):
            content, _ = dehydrator.dehydrate(content)
        dehydrated_messages.append({"role": role, "content": content})

    system_msg = {"role": "system", "content": system_content}
    upstream_messages = [system_msg] + dehydrated_messages

    t0 = time.time()
    status, data, err = await call_model(endpoint, api_key, model, upstream_messages)
    latency = int((time.time() - t0) * 1000)

    if status != 200 or data is None:
        return JSONResponse({"error": err or "elaboration failed"}, status_code=502)

    response_text = rehydrator.rehydrate(data["choices"][0]["message"]["content"])

    user_content = ""
    for msg in reversed(original_messages):
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break

    session_id = "session_" + str(int(time.time()))
    elab_id = reallog.add_interaction(
        session_id=session_id, user_input=user_content,
        route_action="ELABORATE", route_reason=f"winner={winner_profile}",
        target_model=model, response=response_text, response_latency_ms=latency,
    )

    if interaction_id:
        try:
            ranking = json.dumps({
                "winner_profile": winner_profile,
                "all_profiles": [d.get("profile") for d in all_drafts],
                "user_reasoning": user_reasoning,
                "draft_responses": {d.get("profile"): d.get("response") for d in all_drafts},
            })
            conn = reallog._get_connection()
            conn.execute("UPDATE interactions SET critique = ? WHERE id = ?", (ranking, interaction_id))
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to store ranking: %s", exc)

    return JSONResponse({
        "choices": [{"message": {"role": "assistant", "content": response_text}}],
        "interaction_id": elab_id, "winner_profile": winner_profile,
        "model": model, "latency_ms": latency,
    })


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

async def _stream_chat(settings, reallog, dehydrator, rehydrator,
                       upstream_messages, session_id, user_content, route,
                       endpoint, model_name, endpoint_type, api_key, t0):
    """Stream chat completion as Server-Sent Events."""
    from starlette.responses import StreamingResponse
    from datetime import datetime
    from vault.core import Message
    import json

    status, lines, err = await call_model(endpoint, api_key, model_name,
                                           upstream_messages, stream=True)
    if status != 200 or lines is None:
        return JSONResponse({"error": err or "stream failed"}, status_code=502)

    full_text = ""

    async def generate():
        nonlocal full_text
        try:
            async for line in lines:
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    yield "data: [DONE]\n\n"
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                    yield f"data: {data_str}\n\n"
                except json.JSONDecodeError:
                    continue
        finally:
            # Store after streaming completes
            if full_text:
                try:
                    rehydrated = rehydrator.rehydrate(full_text)
                    reallog.add_interaction(
                        session_id=session_id, user_input=user_content,
                        route_action=route["action"], route_reason=route.get("reason", ""),
                        target_model=model_name, response=rehydrated,
                        response_latency_ms=int((time.time() - t0) * 1000),
                    )
                    reallog.add_message(Message(
                        session_id=session_id, role="assistant",
                        content=rehydrated, timestamp=datetime.now().isoformat(),
                    ))
                except Exception:
                    pass

    route_meta = json.dumps({
        "action": route["action"], "reason": route.get("reason", ""),
        "target_model": model_name, "confidence": route.get("confidence", 0),
    })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Route": route_meta,
        },
    )


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

async def feedback(request: Request):
    """POST /v1/feedback — thumbs up/down for an interaction."""
    from starlette.responses import JSONResponse

    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    interaction_id = body.get("interaction_id")
    fb = body.get("feedback")
    critique = body.get("critique")

    if not interaction_id or fb not in ("up", "down"):
        return JSONResponse({"error": "interaction_id and feedback (up/down) required"}, status_code=400)

    reallog = get_reallog()
    interaction = reallog.get_interaction(interaction_id)
    if interaction is None:
        return JSONResponse({"error": "interaction not found"}, status_code=404)

    reallog.update_feedback(interaction_id, fb, critique)

    # Record for adaptive routing
    if interaction:
        try:
            from vault.adaptive_routing import get_adaptive_router
            ar = get_adaptive_router()
            ar.record_feedback(interaction.get("target_model", ""), fb)
        except Exception:
            pass

    # Invalidate cache on negative feedback
    if fb == "down" and interaction:
        from vault.semantic_cache import _get_cache
        cache = _get_cache(get_settings())
        if cache:
            query = interaction["user_input"] if interaction["user_input"] else ""
            model = interaction["target_model"] if interaction["target_model"] else ""
            if query and model:
                removed = cache.invalidate(query, model)
                if removed:
                    logger.info("Cache invalidated: %d entries for model=%s", removed, model)

    # Auto-optimize routing after every 10 feedbacks
    if fb in ("up", "down"):
        try:
            _maybe_auto_optimize(reallog)
        except Exception as exc:
            logger.warning("Auto-optimization failed: %s", exc)

    return JSONResponse({"ok": True, "interaction_id": interaction_id})


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

async def preferences_list(request: Request):
    """GET /v1/preferences — list all preferences."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    prefs = get_reallog().get_preferences()
    prefs.pop("jwt_secret", None)
    return JSONResponse(prefs)


async def preferences_set(request: Request):
    """POST /v1/preferences — upsert a preference."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    key, value = body.get("key"), body.get("value")
    if not key or value is None:
        return JSONResponse({"error": "key and value required"}, status_code=400)
    get_reallog().set_preference(key, value)
    return JSONResponse({"ok": True, "key": key})


async def preferences_delete(request: Request):
    """DELETE /v1/preferences/{key} — remove a preference."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    key = request.path_params.get("key", "")
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)
    deleted = get_reallog().delete_preference(key)
    return JSONResponse({"ok": deleted, "key": key})


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

async def profiles_list(request: Request):
    """GET /v1/profiles — list all profiles (defaults + custom)."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    from vault.profiles import ProfileManager
    profiles = get_draft_profiles(get_settings())
    return JSONResponse({"profiles": profiles})


async def profiles_create(request: Request):
    """POST /v1/profiles — create or update a custom profile."""
    from starlette.responses import JSONResponse
    from vault.profiles import ProfileManager
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    manager = ProfileManager()
    try:
        profile = manager.add_profile(body)
        return JSONResponse({"ok": True, "profile": profile})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def profiles_delete(request: Request):
    """DELETE /v1/profiles/{name} — remove a custom profile."""
    from starlette.responses import JSONResponse
    from vault.profiles import ProfileManager
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    name = request.path_params.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    manager = ProfileManager()
    try:
        removed = manager.remove_profile(name)
        if removed:
            return JSONResponse({"ok": True, "name": name})
        return JSONResponse({"ok": False, "name": name}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------------------
# Routing Intelligence
# ---------------------------------------------------------------------------

async def routing_stats(request: Request):
    """GET /v1/stats/routing — return current routing stats."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    days = int(request.query_params.get("days", 7))
    from vault.stats_collector import StatsCollector
    collector = StatsCollector(get_reallog().db)
    try:
        return JSONResponse(collector.collect(days).to_dict())
    finally:
        collector.close()


async def routing_suggest(request: Request):
    """POST /v1/routing/suggest — dry-run routing suggestions."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    days = 7
    try:
        body = await request.json()
        days = body.get("days", 7)
    except Exception:
        pass
    from vault.stats_collector import StatsCollector
    from vault.routing_updater import RoutingUpdater
    collector = StatsCollector(get_reallog().db)
    updater = RoutingUpdater(get_reallog().db)
    try:
        stats = collector.collect(days)
        return JSONResponse(updater.dry_run(stats))
    finally:
        collector.close()
        updater.close()


async def routing_update(request: Request):
    """POST /v1/routing/update — apply routing suggestions."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    dry = request.query_params.get("dry", "false").lower() == "true"
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    days = body.get("days", 7)
    from vault.stats_collector import StatsCollector
    from vault.routing_updater import RoutingUpdater, RoutingSuggestion
    collector = StatsCollector(get_reallog().db)
    updater = RoutingUpdater(get_reallog().db)
    try:
        stats = collector.collect(days)
        if dry:
            result = updater.dry_run(stats)
            result["status"] = "dry_run_preview"
            return JSONResponse(result)
        provided = body.get("suggestions")
        suggestions = [RoutingSuggestion(**s) for s in provided] if provided else updater.suggest_updates(stats)
        if not suggestions:
            return JSONResponse({"status": "no_changes", "suggestions": []})
        success = updater.apply_updates(suggestions)
        return JSONResponse({"status": "applied" if success else "failed",
                             "suggestions": [s.to_dict() for s in suggestions]})
    finally:
        collector.close()
        updater.close()


async def routing_history(request: Request):
    """GET /v1/routing/history — list of past routing updates."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    limit = int(request.query_params.get("limit", 20))
    from vault.routing_optimizer import RoutingOptimizer
    optimizer = RoutingOptimizer(get_settings().db_path)
    try:
        return JSONResponse({"history": optimizer.get_optimization_history(limit)})
    finally:
        conn = get_reallog()._get_connection()
        # also include legacy history if it exists
        pass


async def routing_rules_list(request: Request):
    """GET /v1/routing/rules — list current routing rules."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    from vault.routing_optimizer import RoutingOptimizer
    optimizer = RoutingOptimizer(get_settings().db_path)
    rules = optimizer.get_rules(enabled_only=False)
    return JSONResponse({"rules": [vars(r) for r in rules]})


async def routing_optimize(request: Request):
    """POST /v1/routing/optimize — manually trigger routing optimization."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    try:
        body = await request.json()
    except Exception:
        body = {}
    min_interactions = body.get("min_interactions", 5)
    days_back = body.get("days_back", 30)
    from vault.routing_optimizer import RoutingOptimizer
    optimizer = RoutingOptimizer(get_settings().db_path)
    result = optimizer.analyze_and_optimize(min_interactions=min_interactions, days_back=days_back)
    return JSONResponse({
        "timestamp": result.timestamp,
        "interactions_analyzed": result.interactions_analyzed,
        "rules_added": result.rules_added,
        "rules_modified": result.rules_modified,
        "changes": result.changes,
        "summary": result.summary,
    })


# ---------------------------------------------------------------------------
# Local Inference
# ---------------------------------------------------------------------------

async def local_models_list(request: Request):
    """GET /v1/local/models — list available .gguf models."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    manager = get_local_manager()
    return JSONResponse({"models": manager.list_models(), "loaded": manager.get_loaded_model_info()})


async def local_model_load(request: Request):
    """POST /v1/local/load — load a model by name."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    try:
        body = await request.json()
        model_name = body.get("model", "")
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    if not model_name:
        return JSONResponse({"error": "model name required"}, status_code=400)
    gpu_layers = body.get("gpu_layers")
    if gpu_layers is not None:
        get_local_manager().gpu_layers = int(gpu_layers)
    else:
        from vault.gpu_utils import calculate_optimal_gpu_layers
        model_info = next((m for m in get_local_manager().list_models() if m["name"] == model_name), None)
        if model_info:
            optimal = calculate_optimal_gpu_layers(model_info["size_mb"], settings.local_ctx_size)
            get_local_manager().gpu_layers = optimal
            logger.info("Auto-detected gpu_layers=%d for model %s (%dMB)", optimal, model_name, model_info["size_mb"])
    manager = get_local_manager()
    if manager.load_model(model_name):
        return JSONResponse({"ok": True, "model": manager.get_loaded_model_info()})
    return JSONResponse({"error": f"model '{model_name}' not found or failed to load"}, status_code=400)


async def local_model_unload(request: Request):
    """POST /v1/local/unload — unload current model."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    get_local_manager().unload()
    return JSONResponse({"ok": True})


async def local_model_status(request: Request):
    """GET /v1/local/status — loaded model info."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    info = get_local_manager().get_loaded_model_info()
    return JSONResponse({"loaded": info is not None, "model": info})


# ---------------------------------------------------------------------------
# Semantic Cache
# ---------------------------------------------------------------------------

async def cache_stats(request: Request):
    """GET /v1/cache/stats — cache hit rate, size, etc."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    from vault.semantic_cache import _get_cache
    cache = _get_cache(get_settings())
    if cache is None:
        return JSONResponse({"enabled": False})
    return JSONResponse({"enabled": True, **cache.stats()})


async def cache_clear(request: Request):
    """POST /v1/cache/clear — clear all cached entries."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    from vault.semantic_cache import _get_cache
    cache = _get_cache(get_settings())
    if cache is None:
        return JSONResponse({"enabled": False})
    try:
        body = await request.json()
        model = body.get("model")
    except Exception:
        model = None
    cache.clear(model)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Auto-optimization
# ---------------------------------------------------------------------------

_last_optimize_check = 0.0
_optimize_interval = 10  # run after every 10 feedbacks

def _maybe_auto_optimize(reallog):
    """Run routing optimization if enough feedback has accumulated."""
    global _last_optimize_check
    import time as _time

    conn = reallog._get_connection()
    row = conn.execute("SELECT COUNT(*) as n FROM interactions WHERE feedback IS NOT NULL").fetchone()
    total_feedback = row["n"]

    if total_feedback < _optimize_interval:
        return
    if total_feedback - _last_optimize_check < _optimize_interval:
        return

    _last_optimize_check = total_feedback
    logger.info("Running auto-optimization (total feedback: %d)", total_feedback)

    try:
        from vault.routing_optimizer import RoutingOptimizer
        settings = get_settings()
        optimizer = RoutingOptimizer(settings.db_path)
        result = optimizer.analyze_and_optimize(min_interactions=10)
        if result.changes:
            logger.info("Auto-optimization: %s", result.summary)
        else:
            logger.info("Auto-optimization: no changes needed")
    except Exception as exc:
        logger.warning("Auto-optimization error: %s", exc)


# ---------------------------------------------------------------------------
# Sessions (conversation history)
# ---------------------------------------------------------------------------

async def sessions_list(request: Request):
    """GET /v1/sessions — list recent sessions."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    limit = int(request.query_params.get("limit", 50))
    reallog = get_reallog()
    sessions = reallog.get_all_sessions(limit)
    result = []
    for s in sessions:
        result.append({
            "id": s.id,
            "timestamp": s.timestamp,
            "summary": s.summary,
            "metadata": s.metadata,
        })
    return JSONResponse({"sessions": result})


async def session_get(request: Request):
    """GET /v1/sessions/{session_id} — get a session with messages."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    session_id = request.path_params.get("session_id", "")
    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)
    reallog = get_reallog()
    session = reallog.get_session(session_id)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)
    messages = reallog.get_session_messages(session_id)
    return JSONResponse({
        "id": session.id,
        "timestamp": session.timestamp,
        "summary": session.summary,
        "messages": [{"role": m.role, "content": m.content, "timestamp": m.timestamp} for m in messages],
    })


async def session_create(request: Request):
    """POST /v1/sessions — create a new session."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    try:
        body = await request.json()
    except Exception:
        body = {}
    reallog = get_reallog()
    from datetime import datetime
    from vault.core import Session
    import uuid
    session_id = body.get("id", str(uuid.uuid4())[:8])
    session = Session(
        id=session_id,
        timestamp=datetime.now().isoformat(),
        summary=body.get("summary", ""),
        metadata=body.get("metadata", {}),
    )
    reallog.add_session(session)
    return JSONResponse({"id": session.id, "created": True})


async def session_delete(request: Request):
    """DELETE /v1/sessions/{session_id} — delete a session and its messages."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    session_id = request.path_params.get("session_id", "")
    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)
    reallog = get_reallog()
    conn = reallog._get_connection()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    return JSONResponse({"ok": True, "id": session_id})


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

async def stats(request: Request):
    """GET /stats — return system statistics."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    conn = get_reallog()._get_connection()
    return JSONResponse({
        "entities": conn.execute("SELECT COUNT(*) AS n FROM pii_map").fetchone()["n"],
        "sessions": conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"],
        "interactions": conn.execute("SELECT COUNT(*) AS n FROM interactions").fetchone()["n"],
        "thumbs_up": conn.execute("SELECT COUNT(*) AS n FROM interactions WHERE feedback='up'").fetchone()["n"],
        "thumbs_down": conn.execute("SELECT COUNT(*) AS n FROM interactions WHERE feedback='down'").fetchone()["n"],
    })


async def metrics_dashboard(request: Request):
    """GET /v1/metrics — observability dashboard data."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err
    minutes = int(request.query_params.get("minutes", 60))
    from gateway.observability import MetricsCollector
    return JSONResponse(MetricsCollector.get_summary(minutes))


async def training_export(request: Request):
    """POST /v1/training/export — run the training data export pipeline."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        body = {}

    days_back = int(body.get("days_back", 30))
    output_dir = body.get("output_dir", "")

    from gateway.deps import get_settings
    settings = get_settings()
    db_path = settings.db_path
    if not output_dir:
        output_dir = str(Path(db_path).parent / "training_data")

    from vault.training_pipeline import run_export_pipeline
    summary = run_export_pipeline(db_path, output_dir, days_back)

    return JSONResponse(summary)


async def training_status(request: Request):
    """GET /v1/training/status — check how much training data is available."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    from gateway.deps import get_settings
    settings = get_settings()
    from vault.training_pipeline import extract_ranking_data, extract_feedback_data

    rankings = extract_ranking_data(settings.db_path, days_back=90)
    feedback = extract_feedback_data(settings.db_path, days_back=90)

    return JSONResponse({
        "rankings_available": len(rankings),
        "feedback_available": len(feedback),
        "ready_for_lora": len(rankings) >= 5,
        "ready_for_dpo": any(r["loser_responses"] for r in rankings),
        "suggestion": (
            "Use /draft mode to generate comparative data for training"
            if len(rankings) < 10
            else "Good amount of data — run export pipeline to generate training files"
        ),
    })


async def config_get(request: Request):
    """GET /v1/config — view current runtime configuration (masked secrets)."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    s = get_settings()
    config = {
        "cheap_model_endpoint": s.cheap_model_endpoint,
        "cheap_model_name": s.cheap_model_name,
        "escalation_model_endpoint": s.escalation_model_endpoint,
        "escalation_model_name": s.escalation_model_name,
        "api_key": s.api_key[:8] + "..." if s.api_key else None,
        "privacy_mode": s.privacy_mode,
        "cache_enabled": s.cache_enabled,
        "cache_similarity_threshold": s.cache_similarity_threshold,
        "cache_max_entries": s.cache_max_entries,
        "cache_ttl_hours": s.cache_ttl_hours,
        "local_gpu_layers": s.local_gpu_layers,
        "local_max_tokens": s.local_max_tokens,
        "local_ctx_size": s.local_ctx_size,
        "local_use_subprocess": s.local_use_subprocess,
        "draft_mode": s.draft_mode,
        "instant_send": s.instant_send,
        "cors_origins": s.cors_origins,
    }
    return JSONResponse(config)


async def config_set(request: Request):
    """PUT /v1/config — update runtime configuration (no restart needed).

    Only allows safe changes: privacy_mode, cache settings, local model settings,
    draft_mode, instant_send. Cannot change API keys or endpoints (security).
    """
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    s = get_settings()
    # Allowed fields for runtime update
    allowed = {
        "privacy_mode": bool,
        "cache_enabled": bool,
        "cache_similarity_threshold": float,
        "cache_max_entries": int,
        "cache_ttl_hours": int,
        "local_gpu_layers": int,
        "local_max_tokens": int,
        "local_ctx_size": int,
        "draft_mode": bool,
        "instant_send": bool,
        "cors_origins": str,
    }

    updated = []
    errors = []
    for key, value in body.items():
        if key not in allowed:
            errors.append(f"{key} is not updatable at runtime")
            continue
        try:
            value = allowed[key](value)
            setattr(s, key, value)
            updated.append(key)
        except (ValueError, TypeError) as exc:
            errors.append(f"{key}: {exc}")

    # Clear cache if cache settings changed
    if any(k.startswith("cache_") for k in updated):
        try:
            from vault.semantic_cache import _cache_instance
            if _cache_instance is not None:
                _cache_instance.invalidate_all()
        except Exception:
            pass

    return JSONResponse({
        "updated": updated,
        "errors": errors,
    })


async def config_validate(request: Request):
    """POST /v1/config/validate — validate a config change without applying it."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    warnings = []
    if "cache_similarity_threshold" in body:
        v = body["cache_similarity_threshold"]
        if not 0.0 < v <= 1.0:
            warnings.append(f"cache_similarity_threshold {v} out of range (0, 1]")
    if "local_gpu_layers" in body:
        v = body["local_gpu_layers"]
        if v < -1:
            warnings.append("local_gpu_layers must be >= -1")
    if "local_max_tokens" in body:
        v = body["local_max_tokens"]
        if v < 1:
            warnings.append("local_max_tokens must be >= 1")

    return JSONResponse({"valid": len(warnings) == 0, "warnings": warnings})


async def adaptive_dashboard(request: Request):
    """GET /v1/adaptive/dashboard — model health, cost, calibration."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    from vault.adaptive_routing import get_adaptive_router
    return JSONResponse(get_adaptive_router().get_dashboard())


async def adaptive_health(request: Request):
    """GET /v1/adaptive/health/{model_name} — health for a specific model."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    from vault.adaptive_routing import get_adaptive_router
    model_name = request.path_params.get("model_name", "")
    health = get_adaptive_router().get_model_health(model_name)
    if health is None:
        return JSONResponse({"error": f"no data for model: {model_name}"}, status_code=404)
    return JSONResponse(health)


async def adaptive_suggest(request: Request):
    """GET /v1/adaptive/suggest — routing suggestion based on model health."""
    from starlette.responses import JSONResponse
    auth_err = authenticate(request)
    if auth_err is not None:
        return auth_err

    s = get_settings()
    from vault.adaptive_routing import get_adaptive_router
    return JSONResponse(get_adaptive_router().suggest_model(
        s.cheap_model_name, s.escalation_model_name
    ))
