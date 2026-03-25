"""
Dataset quality scoring pipeline.

Rates each ranking on effort, diversity, and usefulness.
Deterministic and transparent — every score dimension is explainable.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("vault.dataset_quality")


# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS = {
    "code": [
        r"\b(function|class|def |import |return |if |for |while |try |except )\b",
        r"```", r"\b(bug|debug|error|stack|traceback|compile|syntax)\b",
        r"\b(python|javascript|typescript|rust|go|java|c\+\+)\b",
        r"\b(api|endpoint|database|sql|query|http|json|yaml|xml)\b",
    ],
    "math": [
        r"\b(calculate|compute|solve|equation|formula|theorem|prove|derive)\b",
        r"\b(sum|average|mean|median|standard deviation|probability)\b",
        r"\d+\s*[\+\-\*\/\^]\s*\d+", r"\b(integral|derivative|matrix|vector)\b",
    ],
    "creative": [
        r"\b(write|story|poem|creative|imagine|fiction|character|plot)\b",
        r"\b(song|lyrics|haiku|limerick|joke|riddle)\b",
        r"\b(describe|paint|visualize|scene|setting|narrative)\b",
    ],
    "writing": [
        r"\b(essay|article|blog|email|letter|report|proposal|summary)\b",
        r"\b(edit|revise|rewrite|proofread|grammar|spelling)\b",
        r"\b(tone|style|voice|audience|format|structure)\b",
    ],
    "factual": [
        r"\b(what is|who is|when did|where is|how does|why does)\b",
        r"\b(explain|describe|define|tell me about|compare|difference)\b",
        r"\b(history|science|geography|biology|chemistry|physics)\b",
    ],
}

# Everything that doesn't match specific domains
DEFAULT_DOMAIN = "general"


def classify_domain(text: str) -> str:
    """Classify a prompt into a domain using keyword heuristics."""
    scores: dict[str, int] = {}
    for domain, patterns in DOMAIN_KEYWORDS.items():
        count = 0
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                count += 1
        if count > 0:
            scores[domain] = count
    if not scores:
        return DEFAULT_DOMAIN
    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# QualityScore
# ---------------------------------------------------------------------------

@dataclass
class QualityScore:
    """Quality score for a single ranking interaction.

    Composite score from four dimensions, each 0.0-1.0:
    - reasoning_provided: user gave critique/reasoning (0-0.3)
    - response_length: winner response is substantial (0-0.2)
    - uniqueness: prompt not a near-duplicate (0-0.3)
    - feedback_consistency: feedback aligns with ranking (0-0.2)
    """
    interaction_id: int
    reasoning_provided: float = 0.0
    response_length: float = 0.0
    uniqueness: float = 0.0
    feedback_consistency: float = 0.0
    domain: str = DEFAULT_DOMAIN
    flags: list[str] = field(default_factory=list)

    @property
    def composite(self) -> float:
        return (self.reasoning_provided + self.response_length +
                self.uniqueness + self.feedback_consistency)

    @property
    def is_low_effort(self) -> bool:
        return self.composite < 0.3 or (
            self.reasoning_provided == 0.0 and self.response_length < 0.1
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "composite": round(self.composite, 3),
            "dimensions": {
                "reasoning_provided": round(self.reasoning_provided, 3),
                "response_length": round(self.response_length, 3),
                "uniqueness": round(self.uniqueness, 3),
                "feedback_consistency": round(self.feedback_consistency, 3),
            },
            "domain": self.domain,
            "is_low_effort": self.is_low_effort,
            "flags": self.flags,
        }


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _score_reasoning(critique: str | None, reasoning: str = "") -> float:
    """Score whether the user provided reasoning for their choice."""
    text = (reasoning or "") + " " + (critique or "")
    text = text.strip()

    # Check if critique is a ranking JSON blob with reasoning
    try:
        data = json.loads(text)
        reasoning_text = data.get("user_reasoning", "")
        if reasoning_text and len(reasoning_text) > 10:
            return 0.3
    except (json.JSONDecodeError, TypeError):
        pass

    # Plain text reasoning
    if len(text) > 50:
        return 0.3
    if len(text) > 20:
        return 0.2
    if len(text) > 5:
        return 0.1
    return 0.0


def _score_response_length(response: str | None) -> float:
    """Score based on winner response length."""
    if not response:
        return 0.0
    length = len(response.strip())
    if length >= 500:
        return 0.2
    if length >= 200:
        return 0.15
    if length >= 100:
        return 0.1
    if length >= 50:
        return 0.05
    return 0.0


def _extract_reasoning_from_critique(critique: str | None) -> str:
    """Extract user reasoning from a critique JSON blob if present."""
    if not critique:
        return ""
    try:
        data = json.loads(critique)
        return data.get("user_reasoning", "")
    except (json.JSONDecodeError, TypeError):
        return critique or ""


def score_ranking(interaction: dict[str, Any],
                  seen_prompts: dict[str, int] | None = None) -> QualityScore:
    """Score a single ranking interaction.

    Args:
        interaction: dict with keys like id, user_input, response, critique,
                     feedback, created_at, target_model
        seen_prompts: canonical prompt -> count, for uniqueness scoring.
                      If None, uniqueness defaults to 0.3.
    """
    iid = interaction.get("id", 0)
    critique = interaction.get("critique") or interaction.get("reasoning", "")
    response = interaction.get("response") or interaction.get("winner_response", "")
    feedback = interaction.get("feedback")
    user_input = interaction.get("user_input", "")

    reasoning = _extract_reasoning_from_critique(critique)
    rp = _score_reasoning(critique, reasoning)
    rl = _score_response_length(response)

    # Uniqueness
    if seen_prompts is not None:
        canonical = _canonicalize_prompt(user_input)
        count = seen_prompts.get(canonical, 0)
        if count == 0:
            uniq = 0.3
        elif count == 1:
            uniq = 0.2
        elif count == 2:
            uniq = 0.1
        else:
            uniq = 0.0
    else:
        uniq = 0.3

    # Feedback consistency: positive feedback = better signal
    if feedback == "up":
        fc = 0.2
    elif feedback == "down":
        fc = 0.05  # negative feedback still has signal, but weaker
    else:
        fc = 0.0

    domain = classify_domain(user_input)
    flags = []
    if rp == 0.0:
        flags.append("no_reasoning")
    if rl < 0.1:
        flags.append("short_response")
    if uniq < 0.1:
        flags.append("near_duplicate")

    return QualityScore(
        interaction_id=iid,
        reasoning_provided=rp,
        response_length=rl,
        uniqueness=uniq,
        feedback_consistency=fc,
        domain=domain,
        flags=flags,
    )


def score_rankings(interactions: list[dict[str, Any]]) -> list[QualityScore]:
    """Score a batch of rankings, computing uniqueness across the batch."""
    seen: dict[str, int] = {}
    canonical_order: list[str] = []
    for i in interactions:
        c = _canonicalize_prompt(i.get("user_input", ""))
        canonical_order.append(c)
        seen[c] = seen.get(c, 0) + 1

    # Build per-item seen counts (exclude self)
    results = []
    for idx, interaction in enumerate(interactions):
        own_canonical = canonical_order[idx]
        # seen minus self
        self_seen = {k: v - (1 if k == own_canonical else 0)
                     for k, v in seen.items()}
        results.append(score_ranking(interaction, seen_prompts=self_seen))
    return results


# ---------------------------------------------------------------------------
# Prompt canonicalization (for dedup/uniqueness)
# ---------------------------------------------------------------------------

_PII_PATTERN = re.compile(
    r'\b[A-Z]+_[A-Z]+\b'  # PERSON_A, EMAIL_B style tokens
)


def _canonicalize_prompt(text: str) -> str:
    """Normalize a prompt for deduplication."""
    if not text:
        return ""
    text = text.strip()
    text = _PII_PATTERN.sub("[PII]", text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.lower()
    return text


def canonicalize_prompt(text: str) -> str:
    """Public wrapper for canonicalization."""
    return _canonicalize_prompt(text)


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------

def filter_by_quality(interactions: list[dict[str, Any]],
                      min_composite: float = 0.2,
                      exclude_low_effort: bool = True) -> list[dict[str, Any]]:
    """Filter interactions by quality score, preserving order."""
    scores = score_rankings(interactions)
    result = []
    for interaction, score in zip(interactions, scores):
        if score.composite < min_composite:
            continue
        if exclude_low_effort and score.is_low_effort:
            continue
        result.append(interaction)
    return result
