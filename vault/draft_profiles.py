"""Draft provider profiles — configs for parallel draft calls.

This module re-exports from vault.profiles for backwards compatibility.
All profile logic lives in vault/profiles.py.
"""

from vault.profiles import get_draft_profiles, ProfileManager, DEFAULT_PROFILES

# Legacy alias for code that imports DRAFT_PROFILES directly
DRAFT_PROFILES = DEFAULT_PROFILES
