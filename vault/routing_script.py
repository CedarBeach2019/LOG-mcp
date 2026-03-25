"""
Routing script — classifies messages and picks the right model.

Two modes:
1. **Static** (fallback): regex-based classification, no DB needed
2. **Dynamic** (default): uses RoutingOptimizer rules from SQLite, learns from feedback

The classifier returns an action and confidence:
- CHEAP_ONLY: fast/cheap model (deepseek-chat)
- ESCALATE: reasoning model (deepseek-reasoner)
- COMPARE: run both, let user pick
- DRAFT: parallel draft profiles
- LOCAL: local llama.cpp model
- MANUAL_OVERRIDE: user specified a model
"""

from __future__ import annotations

import os
import re
import logging
from typing import Any

logger = logging.getLogger("vault.routing_script")


# ---------------------------------------------------------------------------
# Static classification (fallback, no DB)
# ---------------------------------------------------------------------------

_STATIC_RULES: list[dict] = [
    # Command prefixes (highest priority)
    {"name": "draft_mode", "pattern": r"^/draft\b", "action": "DRAFT", "confidence": 1.0},
    {"name": "local_mode", "pattern": r"^/local\b", "action": "LOCAL", "confidence": 1.0},
    {"name": "manual_override", "pattern": r"^/(deepseek|gpt|claude|local|cheap|escalate)\b", "action": "MANUAL_OVERRIDE", "confidence": 1.0},
    # Heuristic signals
    {"name": "code_block", "pattern": r"```", "action": "ESCALATE", "confidence": 0.8},
    {"name": "long_message", "pattern": r".{500,}", "action": "ESCALATE", "confidence": 0.6},
    # Escalation patterns (complex tasks need quality)
    {"name": "debug", "pattern": r"\b(debug|traceback|error|fix|broken|bug)\b", "action": "ESCALATE", "confidence": 0.7},
    {"name": "write_code", "pattern": r"\b(write|implement|create|build|code)\s+(a |the )?(function|class|module|script|program|app|service|api|endpoint)", "action": "ESCALATE", "confidence": 0.7},
    {"name": "explain_complex", "pattern": r"\b(explain|describe)\b.*\b(in detail|thoroughly|step.by.step|comprehensive)\b", "action": "ESCALATE", "confidence": 0.6},
    {"name": "plan", "pattern": r"\b(plan|strategy|architecture|roadmap|migration)\b", "action": "ESCALATE", "confidence": 0.7},
    {"name": "review", "pattern": r"\b(review|audit|critique|analyze|evaluate|improve|optimize)\b", "action": "ESCALATE", "confidence": 0.7},
    {"name": "design", "pattern": r"\b(design|architect|structure|schema)\b", "action": "ESCALATE", "confidence": 0.7},
    # Comparison (dual-model)
    {"name": "comparison", "pattern": r"\b(compare|vs|versus|difference)\b", "action": "COMPARE", "confidence": 0.5},
    # Cheap patterns (simple queries)
    {"name": "factual_question", "pattern": r"^(what|how|who|when|where|which|count|convert|define|calculate)\b", "action": "CHEAP_ONLY", "confidence": 0.7},
    {"name": "acknowledgment", "pattern": r"^(ok|thanks|thank you|got it|sure|great|nice|cool)\b", "action": "CHEAP_ONLY", "confidence": 0.9},
    {"name": "help", "pattern": r"^help$", "action": "CHEAP_ONLY", "confidence": 0.8},
]


def _normalize_action(action: str) -> str:
    """Normalize action to lowercase, canonical form."""
    return action.lower().replace("cheap_only", "cheap").replace("escalate", "escalation")


def classify_static(message: str, length: int = 0, has_code: bool = False) -> dict:
    """Static regex-based classification. Always works, no DB needed."""
    for rule in _STATIC_RULES:
        try:
            if re.search(rule["pattern"], message, re.IGNORECASE | re.DOTALL):
                return {
                    "action": _normalize_action(rule["action"]),
                    "confidence": rule["confidence"],
                    "reason": f"[static] {rule['name']}",
                }
        except re.error:
            continue

    return {
        "action": "cheap",
        "confidence": 0.3,
        "reason": "[static] no pattern matched",
    }


# ---------------------------------------------------------------------------
# Dynamic classification (uses optimizer rules from DB)
# ---------------------------------------------------------------------------

_optimizer = None


def _get_optimizer():
    """Lazy-load the routing optimizer."""
    global _optimizer
    if _optimizer is None:
        try:
            from vault.config import VaultSettings
            settings = VaultSettings()
            from vault.routing_optimizer import RoutingOptimizer
            _optimizer = RoutingOptimizer(settings.db_path)
            logger.info("Routing optimizer loaded from %s", settings.db_path)
        except Exception as exc:
            logger.warning("Could not load routing optimizer: %s — using static rules", exc)
    return _optimizer


def classify(message: str, length: int = 0, has_code: bool = False) -> dict:
    """Classify a message. Uses dynamic rules if available, falls back to static."""
    optimizer = _get_optimizer()

    if optimizer is None:
        return classify_static(message, length, has_code)

    # Check static command prefixes first (these always take priority)
    for rule in _STATIC_RULES:
        if rule["action"] in ("DRAFT", "LOCAL", "MANUAL_OVERRIDE"):
            try:
                if re.search(rule["pattern"], message, re.IGNORECASE):
                    action = rule["action"].lower()
                    return {
                        "action": action,
                        "confidence": rule["confidence"],
                        "reason": f"[static] {rule['name']}",
                    }
            except re.error:
                continue

    # Use optimizer rules for everything else
    try:
        result = optimizer.evaluate_message(message)
        result["reason"] = f"[dynamic] {result.get('reason', '')}"
        result["action"] = _normalize_action(result["action"])
        return result
    except Exception as exc:
        logger.warning("Optimizer failed, falling back to static: %s", exc)
        return classify_static(message, length, has_code)


def resolve_action(action: str, cheap_model: str, escalation_model: str) -> tuple[str, str]:
    """Map an action to (endpoint_type, model_name)."""
    action = action.upper()  # accept both cases
    mapping = {
        "CHEAP_ONLY": ("cheap", cheap_model),
        "CHEAP": ("cheap", cheap_model),
        "ESCALATE": ("escalation", escalation_model),
        "ESCALATION": ("escalation", escalation_model),
        "COMPARE": ("compare", cheap_model),
        "DRAFT": ("draft", cheap_model),
        "LOCAL": ("local", "local"),
        "MANUAL_OVERRIDE": ("cheap", cheap_model),
    }
    return mapping.get(action, ("cheap", cheap_model))


# Backwards compatibility
def classify_message(message: str, length: int = 0, has_code: bool = False) -> dict:
    """Alias for classify()."""
    return classify(message, length, has_code)
