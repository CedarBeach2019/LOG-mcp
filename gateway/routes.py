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
    """GET /v1/health — check service availability."""
    from starlette.responses import JSONResponse
    settings = get_settings()
    results = {}

    # Check cheap model
    try:
        client = get_client()
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
        client = get_client()
        resp = await client.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        results["ollama"] = resp.status_code == 200
    except Exception:
        results["ollama"] = False

    # Check local llama.cpp model
    try:
        info = get_local_manager().get_loaded_model_info()
        results["local_model"] = info is not None
        if info:
            results["local_model_name"] = info.get("model_name", "unknown")
    except Exception:
        results["local_model"] = False

    return JSONResponse(results)


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
    reallog.add_message(Message(
        session_id=request_session_id, role="user",
        content=user_content, timestamp=datetime.now().isoformat(),
    ))

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
            backend = get_local_manager().get_backend()
            if backend and backend.is_loaded:
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

    # --- Call model(s) ---
    escalation_response = None
    escalation_latency = None

    if endpoint_type == "local":
        backend = get_local_manager().get_backend()
        if backend is None or not backend.is_loaded:
            logger.warning("Local model not loaded, falling back to cloud")
            endpoint_type = "cheap"
            endpoint = settings.cheap_model_endpoint
            model_name = settings.cheap_model_name
        else:
            local_content = await backend.agenerate(
                upstream_messages, temperature=0.7,
                max_tokens=getattr(settings, 'local_max_tokens', 512),
            )
            latency = int((time.time() - t0) * 1000)
            if local_content:
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
            else:
                logger.warning("Local inference failed, falling back to cloud")
                endpoint_type = "cheap"

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
    # Store assistant response in session
    reallog.add_message(Message(
        session_id=request_session_id, role="assistant",
        content=response_text, timestamp=datetime.now().isoformat(),
    ))

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
    from vault.routing_updater import RoutingUpdater
    updater = RoutingUpdater(get_reallog().db)
    try:
        return JSONResponse({"history": updater.get_history(limit)})
    finally:
        updater.close()


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
