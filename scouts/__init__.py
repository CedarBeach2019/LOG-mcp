"""
Scout connectors for sending dehydrated prompts to external agents.
"""
from scouts.base import ScoutBase
from scouts.claude import ClaudeScout
from scouts.deepseek_scout import DeepSeekScout

__all__ = [
    "ScoutBase",
    "ClaudeScout",
    "DeepSeekScout",
]
