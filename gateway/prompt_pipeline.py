"""Prompt pipeline — orchestrates template selection, context window, and few-shot injection."""

from __future__ import annotations

import logging
from typing import Any

from vault.prompt_intelligence import (
    DEFAULT_SYSTEM_PROMPTS,
    ContextWindow,
    FewShotInjector,
    render_template,
)

logger = logging.getLogger("gateway.prompt_pipeline")

# Map route actions to prompt template names
ACTION_TO_TEMPLATE: dict[str, str] = {
    "escalation": "code",      # complex tasks → code template (best general fit)
    "cheap": "default",        # simple queries → default
    "local": "default",        # local model → default
    "compare": "analyst",      # comparison → analyst
    "draft": "creative",       # drafts → creative
    "cache_hit": "default",
    "manual_override": "default",
}

# Override for specific route reasons
REASON_TO_TEMPLATE: dict[str, str] = {
    "debug": "debug",
    "review": "code",
    "audit": "analyst",
}


def select_template(route_action: str, route_reason: str = "", override: str | None = None) -> str:
    """Select the system prompt template for this request."""
    if override and override in DEFAULT_SYSTEM_PROMPTS:
        return override

    # Check route reason first (more specific)
    reason_lower = route_reason.lower() if route_reason else ""
    for keyword, template in REASON_TO_TEMPLATE.items():
        if keyword in reason_lower:
            return template

    # Fall back to action-based mapping
    return ACTION_TO_TEMPLATE.get(route_action, "default")


def build_system_prompt(template_name: str, preamble: str, prefs_text: str,
                        extra_vars: dict | None = None) -> str:
    """Build the full system prompt from template + existing preamble + prefs."""
    template_text = DEFAULT_SYSTEM_PROMPTS.get(template_name, DEFAULT_SYSTEM_PROMPTS["default"])
    rendered = render_template(template_text, extra_vars)
    parts = [rendered]
    if preamble:
        parts.append(preamble)
    if prefs_text:
        parts.append(f"User preferences: {prefs_text}")
    return "\n\n".join(parts)


async def apply_prompt_pipeline(
    messages: list[dict],
    route_action: str,
    route_reason: str = "",
    preamble: str = "",
    prefs_text: str = "",
    session_id: str = "",
    reallog=None,
    template_override: str | None = None,
    max_context_tokens: int = 4096,
    enable_few_shot: bool = True,
) -> tuple[list[dict], dict]:
    """Apply the full prompt pipeline: template selection + context window + few-shot.

    Returns (processed_messages, metadata) where metadata includes template_name,
    few_shot_count, tokens_estimated, etc.
    """
    metadata: dict[str, Any] = {}

    # 1. Select and build system prompt
    template_name = select_template(route_action, route_reason, template_override)
    system_prompt = build_system_prompt(template_name, preamble, prefs_text)
    metadata["template"] = template_name

    # 2. Context window management
    ctx = ContextWindow(max_tokens=max_context_tokens)
    non_system_messages = [m for m in messages if m.get("role") != "system"]
    truncated = ctx.truncate_messages(non_system_messages, system_prompt)
    metadata["tokens_estimated"] = ctx.estimate_tokens(truncated, system_prompt)
    metadata["original_messages"] = len(messages)
    metadata["truncated_messages"] = len(truncated)

    # 3. Few-shot injection
    few_shot_count = 0
    if enable_few_shot and reallog:
        try:
            injector = FewShotInjector(max_examples=2)
            # Get positively-rated interactions as few-shot candidates
            interactions = _get_few_shot_candidates(reallog, limit=50)
            if interactions:
                # Find the user's query from truncated messages
                query = ""
                for m in reversed(truncated):
                    if m.get("role") == "user":
                        query = m.get("content", "")
                        break
                if query:
                    examples = injector.find_relevant_examples(query, interactions)
                    if examples:
                        truncated = injector.inject_examples(
                            [{"role": "system", "content": system_prompt}] + truncated,
                            examples
                        )
                        few_shot_count = len(examples)
        except Exception as exc:
            logger.debug("Few-shot injection failed: %s", exc)

    metadata["few_shot_count"] = few_shot_count

    # 4. Build final message list (system prompt + processed messages)
    final = [{"role": "system", "content": system_prompt}] + truncated

    return final, metadata


def _get_few_shot_candidates(reallog, limit: int = 50) -> list[dict]:
    """Get positively-rated interactions for few-shot learning."""
    conn = reallog._get_connection()
    rows = conn.execute(
        """SELECT user_input, response, feedback
           FROM interactions
           WHERE feedback = 'up' AND response IS NOT NULL AND response != ''
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
