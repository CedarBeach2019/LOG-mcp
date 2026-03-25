"""Draft provider profiles — configs for parallel draft calls.

This module re-exports from vault.profiles for backwards compatibility.
"""

from vault.profiles import get_draft_profiles, ProfileManager, DEFAULT_PROFILES

# Keep backwards compat alias
DRAFT_PROFILES: list[dict] = [
    {
        "name": "precise",
        "endpoint_key": "cheap_model_endpoint",
        "model_key": "cheap_model_name",
        "temperature": 0.2,
        "system": "Be precise and concise. One sentence approach only. Under 280 characters.",
        "max_chars": 280,
    },
    {
        "name": "creative",
        "endpoint_key": "cheap_model_endpoint",
        "model_key": "cheap_model_name",
        "temperature": 0.7,
        "system": "Think creatively. One sentence approach only. Under 280 characters.",
        "max_chars": 280,
    },
    {
        "name": "deep",
        "endpoint_key": "escalation_model_endpoint",
        "model_key": "escalation_model_name",
        "temperature": 0.1,
        "system": "Reason step by step. One sentence. Under 280 characters.",
        "max_chars": 280,
    },
]
