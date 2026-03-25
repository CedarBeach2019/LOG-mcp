"""Prompt intelligence — system templates, context management, few-shot injection."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("vault.prompt_intelligence")


# ---------------------------------------------------------------------------
# System prompt templates
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "default": "You are a helpful AI assistant. Provide clear, accurate, and well-structured responses. Be concise when possible, thorough when needed.",
    "code": "You are an expert software engineer. Write clean, efficient code with comments. Explain your reasoning. Consider edge cases. Use best practices.",
    "creative": "You are a creative writer and thinker. Be imaginative, vivid, and original. Don't be afraid to take risks with ideas.",
    "teacher": "You are a patient teacher. Explain concepts step by step. Use analogies. Check for understanding. Be encouraging.",
    "analyst": "You are a data analyst. Be precise, cite sources, show your work. Present findings clearly. Consider alternative interpretations.",
    "concise": "You are a concise assistant. Give brief, direct answers. No filler. If the answer is simple, keep it short. Use bullet points.",
    "debug": "You are a debugging expert. Analyze errors systematically. Suggest fixes with explanations. Consider common pitfalls.",
}

# Template variables: {name}, {date}, {time}, {model}, {route}
TEMPLATE_VARS = {
    "name": lambda: "User",  # would be set from user profile
    "date": lambda: datetime.now().strftime("%Y-%m-%d"),
    "time": lambda: datetime.now().strftime("%H:%M"),
}


def render_template(template: str, extra_vars: dict | None = None) -> str:
    """Render a system prompt template with variables."""
    rendered = template
    # Extra vars first (user-provided overrides defaults)
    if extra_vars:
        for key, value in extra_vars.items():
            rendered = rendered.replace(f"{{{key}}}", str(value))
    # Then fill remaining defaults
    for key, fn in TEMPLATE_VARS.items():
        if f"{{{key}}}" in rendered:  # only fill if not already replaced
            rendered = rendered.replace(f"{{{key}}}", fn())
    return rendered


# ---------------------------------------------------------------------------
# Context window manager
# ---------------------------------------------------------------------------

@dataclass
class ContextWindow:
    """Manage message history to fit within model context limits."""

    max_tokens: int = 4096
    reserve_for_response: int = 512
    avg_chars_per_token: float = 4.0  # conservative estimate

    def truncate_messages(self, messages: list[dict], system_prompt: str = "") -> list[dict]:
        """Truncate messages to fit context window, keeping system + most recent.

        Priority: system prompt > most recent messages > older messages.
        Uses simple character-based estimation (no tokenizer needed).
        """
        if not messages:
            return messages

        available_chars = (self.max_tokens - self.reserve_for_response) * self.avg_chars_per_token
        system_chars = len(system_prompt) if system_prompt else 0
        available_chars -= system_chars

        # Build result from newest to oldest
        result = []
        total_chars = 0

        for msg in reversed(messages):
            content = msg.get("content", "")
            msg_chars = len(content)
            if total_chars + msg_chars > available_chars:
                # Can we fit a truncated version?
                remaining = available_chars - total_chars
                if remaining > 50:  # at least 50 chars useful
                    truncated = content[-int(remaining):]
                    result.append({**msg, "content": f"...[truncated] {truncated}"})
                break
            result.append(msg)
            total_chars += msg_chars

        result.reverse()
        return result

    def estimate_tokens(self, messages: list[dict], system_prompt: str = "") -> int:
        """Estimate total token count."""
        total = len(system_prompt) if system_prompt else 0
        for msg in messages:
            total += len(msg.get("content", ""))
        return int(total / self.avg_chars_per_token)


# ---------------------------------------------------------------------------
# Few-shot injection
# ---------------------------------------------------------------------------

@dataclass
class FewShotInjector:
    """Inject relevant past interactions as few-shot examples."""

    max_examples: int = 3
    max_chars_per_example: int = 500

    def find_relevant_examples(self, query: str, interactions: list[dict]) -> list[dict]:
        """Find relevant past interactions for few-shot learning.

        Uses simple keyword matching (no embeddings required).
        Returns list of {input, output} dicts.
        """
        if not interactions or not query:
            return []

        # Extract keywords from query
        query_words = set(query.lower().split())
        query_words -= {"the", "a", "an", "is", "are", "was", "were", "i", "you", "it", "to", "of", "and", "in", "for", "on", "with", "that", "this", "what", "how", "why", "can"}

        # Score interactions by keyword overlap
        scored = []
        for interaction in interactions:
            input_text = interaction.get("user_input", "").lower()
            input_words = set(input_text.split())
            overlap = len(query_words & input_words)
            feedback = interaction.get("feedback", "")
            if feedback == "up":
                overlap += 2  # boost positive feedback
            if feedback == "down":
                overlap -= 1  # penalize negative
            scored.append((overlap, interaction))

        # Sort by relevance
        scored.sort(key=lambda x: x[0], reverse=True)

        # Build examples
        examples = []
        for score, interaction in scored[:self.max_examples]:
            if score <= 0:
                break
            user_input = interaction.get("user_input", "")[:self.max_chars_per_example]
            response = interaction.get("response", "")[:self.max_chars_per_example]
            if user_input and response:
                examples.append({"input": user_input, "output": response})

        return examples

    def inject_examples(self, messages: list[dict], examples: list[dict]) -> list[dict]:
        """Inject few-shot examples into the message list.

        Inserts examples after system prompt (if present) and before user messages.
        """
        if not examples:
            return messages

        result = []
        injected = False

        for msg in messages:
            result.append(msg)

            # Insert examples after system prompt
            if msg.get("role") == "system" and not injected:
                for ex in examples:
                    result.append({
                        "role": "user",
                        "content": f"Example question: {ex['input']}",
                    })
                    result.append({
                        "role": "assistant",
                        "content": f"Example answer: {ex['output']}",
                    })
                injected = True

        # If no system prompt, insert at beginning
        if not injected:
            for ex in examples:
                result.insert(0, {
                    "role": "assistant",
                    "content": f"Example answer: {ex['output']}",
                })
                result.insert(0, {
                    "role": "user",
                    "content": f"Example question: {ex['input']}",
                })

        return result
