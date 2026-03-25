"""Error boundaries: retry logic and fallback chain for model calls."""

from __future__ import annotations

import asyncio
import logging
import time

from gateway.shared import call_model
from gateway.deps import get_settings

logger = logging.getLogger("gateway.error_boundary")

# Maximum retries before giving up
MAX_RETRIES = 2
# Backoff multiplier (seconds)
BACKOFF_BASE = 1.5
# Timeout for retry attempts (seconds) — shorter than initial call
RETRY_TIMEOUT = 30.0


async def resilient_call(endpoint: str, api_key: str, model: str,
                         messages: list[dict], timeout: float = 60.0,
                         temperature: float | None = None,
                         stream: bool = False) -> tuple[int, dict | None | object, str]:
    """Call a model with retry logic and automatic fallback.

    Retry chain:
    1. Try primary endpoint (with retry on timeout/5xx)
    2. If all retries fail, try fallback endpoint
    3. If fallback fails, return graceful error (never 502 to user)

    Returns same format as call_model:
    - (200, data, "") for success
    - (0, None, error_message) for all failures (user-friendly message)
    """
    settings = get_settings()
    primary_endpoint = endpoint
    fallback_endpoint = settings.escalation_model_endpoint if endpoint == settings.cheap_model_endpoint else settings.cheap_model_endpoint
    fallback_model = settings.escalation_model_name if model == settings.cheap_model_name else settings.cheap_model_name

    # Phase 1: Retry primary
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        status, data, err = await call_model(
            primary_endpoint, api_key, model, messages,
            timeout=timeout, temperature=temperature, stream=stream,
        )

        if status == 200:
            return 200, data, ""

        # Only retry on retriable errors (timeout, 5xx, connection)
        if _is_retriable(status, err):
            last_error = err
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE ** (attempt + 1)
                logger.warning("Model call failed (attempt %d/%d): %s — retrying in %.1fs",
                             attempt + 1, MAX_RETRIES + 1, err, wait)
                await asyncio.sleep(wait)
                continue
        else:
            # Non-retriable (4xx) — return immediately
            return status, data, err

    # Phase 2: Try fallback (single attempt, no retry)
    logger.warning("Primary model failed after %d attempts: %s — trying fallback %s",
                 MAX_RETRIES + 1, last_error, fallback_model)

    fb_status, fb_data, fb_err = await call_model(
        fallback_endpoint, api_key, fallback_model, messages,
        timeout=RETRY_TIMEOUT, temperature=temperature, stream=stream,
    )

    if fb_status == 200:
        logger.info("Fallback to %s succeeded", fallback_model)
        return 200, fb_data, f"(fallback: {fallback_model})"

    # Phase 3: Graceful failure
    logger.error("Both primary and fallback failed: primary=%s, fallback=%s",
                last_error, fb_err)
    user_message = _friendly_error_message(last_error, fb_err, model, fallback_model)
    return 0, None, user_message


def _is_retriable(status: int, err: str) -> bool:
    """Determine if an error is worth retrying."""
    if status == 0:  # connection error / timeout
        return True
    if status >= 500:  # server error
        return True
    if status == 429:  # rate limited
        return True
    return False


def _friendly_error_message(primary_err: str, fallback_err: str,
                            primary_model: str, fallback_model: str) -> str:
    """Generate a user-friendly error message."""
    # Detect specific error types
    if "timeout" in primary_err.lower() or "timeout" in fallback_err.lower():
        return (f"Both {primary_model} and {fallback_model} timed out. "
                "The models may be experiencing high load. Please try again in a moment.")
    if "rate" in primary_err.lower() or "429" in primary_err:
        return (f"Rate limited by {primary_model}. "
                "Please wait a moment before trying again.")
    if "connection" in primary_err.lower() or "connection" in fallback_err.lower():
        return ("Could not reach the AI service. "
                "Please check your internet connection and try again.")
    if "401" in primary_err or "403" in primary_err:
        return ("API authentication failed. "
                "Please check your API key configuration.")

    return (f"Both {primary_model} and {fallback_model} are currently unavailable. "
            f"Primary error: {primary_err[:80]}. "
            "Please try again shortly.")
