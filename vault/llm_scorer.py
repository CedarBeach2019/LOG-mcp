"""
L.O.G. Vault — LLM-powered PII scorer.

Uses a local Ollama instance to detect PII that regex misses:
relationship references ("my wife"), implicit names, possessives, etc.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3.5:2b"
TIMEOUT_SECONDS = 3.0

SYSTEM_PROMPT = """\
You are a PII detection assistant. Given text, identify ALL personally identifiable information \
that a simple regex scanner would miss, including:
- Relationship references: "my wife", "our house", "his kids", "her mom"
- Implicit names in context: possessives, informal references
- Locations mentioned as places the subject frequents
- Any other contextual PII

Return ONLY a JSON object with this structure:
{"entities": [{"type": "<person|relationship|location|other>", "text": "<exact span from input>"}]}

If no PII is found, return: {"entities": []}
Do NOT include explanation, just the JSON."""

USER_TEMPLATE = "Identify PII in this text:\n\n{text}"


async def score_pii(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = TIMEOUT_SECONDS,
    ollama_url: str = OLLAMA_URL,
) -> Dict[str, Any]:
    """Send text to Ollama for LLM-based PII detection.

    Returns a dict with detected entities, or empty dict on any failure.
    Gracefully degrades — never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_TEMPLATE.format(text=text)},
                    ],
                    "format": "json",
                    "stream": False,
                },
            )
            resp.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
        logger.debug("Ollama unavailable: %s", exc)
        return {}

    try:
        body = resp.json()
        raw = body.get("message", {}).get("content", "{}")
        # Strip any <think...</think wrapper from qwen models
        if "<think" in raw:
            raw = raw[raw.index("</think") + len("</think") :] if "</think" in raw else raw
            raw = raw.strip()
        parsed = json.loads(raw)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.debug("Failed to parse Ollama response: %s", exc)
        return {}

    if not isinstance(parsed, dict) or "entities" not in parsed:
        return {}

    return parsed


def score_pii_sync(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = TIMEOUT_SECONDS,
    ollama_url: str = OLLAMA_URL,
) -> Dict[str, Any]:
    """Synchronous wrapper for score_pii."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an async context — create a new thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(
                asyncio.run, score_pii(text, model=model, timeout=timeout, ollama_url=ollama_url)
            ).result(timeout=timeout + 1)
    else:
        return asyncio.run(score_pii(text, model=model, timeout=timeout, ollama_url=ollama_url))
