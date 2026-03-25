"""Shared utilities for LOG-mcp route handlers."""

from __future__ import annotations

import logging

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from gateway.auth import create_token, get_jwt_secret, verify_token
from gateway.deps import get_reallog, get_settings

logger = logging.getLogger("gateway.routes")

# ---------------------------------------------------------------------------
# Shared httpx client — reuses TCP connections (major latency win)
# ---------------------------------------------------------------------------
_shared_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Return a shared async HTTP client (connection pooling)."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_client


def authenticate(request: Request) -> JSONResponse | None:
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


async def call_model(endpoint: str, api_key: str, model: str,
                     messages: list[dict], timeout: float = 60.0,
                     temperature: float | None = None,
                     stream: bool = False):
    """Call an OpenAI-compatible API.

    Returns (status_code, json_data, error_str) for non-streaming.
    Returns (200, async_generator, "") for streaming.
    """
    try:
        body: dict = {"model": model, "messages": messages}
        if temperature is not None:
            body["temperature"] = temperature
        if stream:
            body["stream"] = True
            client = get_client()
            req = client.build_request(
                "POST", endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp = await client.send(req, stream=True, timeout=timeout)
            if resp.status_code != 200:
                text = await resp.aread()
                return resp.status_code, None, f"upstream returned {resp.status_code}: {text[:200]}"
            return 200, resp.aiter_lines(), ""

        client = get_client()
        resp = await client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return resp.status_code, None, f"upstream returned {resp.status_code}"
        return 200, resp.json(), ""
    except httpx.TimeoutException:
        return 0, None, "upstream timeout"
    except httpx.HTTPError as exc:
        return 0, None, f"upstream connection failed: {exc}"


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------
_local_manager = None


def get_local_manager():
    """Return the singleton ModelManager."""
    global _local_manager
    if _local_manager is None:
        from vault.model_manager import ModelManager
        s = get_settings()
        _local_manager = ModelManager(s.local_models_dir, s.local_gpu_layers, s.local_ctx_size)
    return _local_manager
