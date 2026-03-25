"""Rule-based message routing script.

Classifies messages in ~5ms using regex patterns.
No ML, no external dependencies, no latency.

The ML optimizer (Phase 3) will update these rules over time.
"""

import re


# Each pattern is a regex. First match wins in order: MANUAL > ESCALATE > heuristics > default.
RULES = {
    "MANUAL_OVERRIDE": {
        "patterns": [
            r"^/(local|cloud|reason|compare|draft)\b",
        ],
        "action": "manual",
    },
    "ESCALATE": {
        "patterns": [
            r"(debug|traceback|error|exception|fix my)\b",
            r"(write|create|draft|compose)\b.*(code|essay|article|story|letter|email|poem|song)\b",
            r"(explain|analyze|compare|contrast|evaluate)\b.{20,}",
            r"(complex|advanced|expert|detailed)\b",
            r"(plan|design|architect)\b",
            r"(review|critique|improve|optimize)\b.*(my|this|the|code)\b",
            r"(how (to|should|would|could|do|can)\b).{30,}",
        ],
        "action": "ESCALATE",
    },
    "CHEAP_ONLY": {
        "patterns": [
            r"what (is|are|was|were|does|did)\b",
            r"how (many|much|old|far|long|big|tall|wide|deep|fast|hot|cold)\b",
            r"(convert|calculate|compute)\b",
            r"define\b",
            r"(translate|spell|synonym|antonym|rhyme)\b",
            r"(sum|list|count|show)\b.*\b(all|my|the|top|last|recent)\b",
            r"(who|when|where)\b.{5,40}(\?|$)",
            r"^\s*(yes|no|ok|thanks|great|sure|done)\s*[.!]?\s*$",
        ],
        "action": "CHEAP_ONLY",
    },
}

# Heuristic thresholds (not regex — simple length/content checks)
MAX_CHEAP_LENGTH = 500
MAX_CHEAP_CODE_BLOCKS = 0


def classify(user_input: str, message_length: int = None,
             has_code_blocks: bool = False) -> dict:
    """Classify a user message for routing.

    Returns:
        {
            "action": "CHEAP_ONLY" | "ESCALATE" | "local" | "cloud" | "reason" | "compare",
            "reason": str,
            "confidence": float
        }
    """
    text = user_input.strip()
    text_lower = text.lower()
    if message_length is None:
        message_length = len(text)

    # 1. Manual overrides (highest priority)
    for pattern in RULES["MANUAL_OVERRIDE"]["patterns"]:
        if re.search(pattern, text_lower):
            cmd = text_lower.split()[0].lstrip("/")
            return {"action": cmd, "reason": "manual override", "confidence": 1.0}

    # 2. Escalation patterns
    for pattern in RULES["ESCALATE"]["patterns"]:
        if re.search(pattern, text_lower):
            return {"action": "ESCALATE", "reason": "pattern matched", "confidence": 0.8}

    # 3. Heuristics: long messages → escalate
    if message_length > MAX_CHEAP_LENGTH:
        return {"action": "ESCALATE", "reason": f"long message ({message_length} chars)", "confidence": 0.7}

    # 4. Heuristics: code blocks → escalate
    if has_code_blocks:
        return {"action": "ESCALATE", "reason": "contains code blocks", "confidence": 0.75}

    # 5. Cheap-only patterns
    for pattern in RULES["CHEAP_ONLY"]["patterns"]:
        if re.search(pattern, text_lower):
            return {"action": "CHEAP_ONLY", "reason": "pattern matched", "confidence": 0.85}

    # 6. Default: escalate (safer — better to over-escalate than under-escalate)
    return {"action": "ESCALATE", "reason": "default (uncertain)", "confidence": 0.5}


def resolve_action(action: str, cheap_model_name: str,
                   escalation_model_name: str) -> tuple[str, str]:
    """Resolve routing action to (target_endpoint_type, model_name).

    Returns:
        (endpoint_type, model_name) where endpoint_type is 'cheap' or 'escalation'
    """
    if action == "CHEAP_ONLY":
        return ("cheap", cheap_model_name)
    elif action == "ESCALATE":
        return ("escalation", escalation_model_name)
    elif action == "local":
        return ("local", "local")
    elif action == "cloud":
        return ("cheap", cheap_model_name)
    elif action == "reason":
        return ("escalation", escalation_model_name)
    elif action == "compare":
        return ("compare", cheap_model_name)  # both fire
    elif action == "draft":
        return ("draft", cheap_model_name)
    else:
        return ("cheap", cheap_model_name)
