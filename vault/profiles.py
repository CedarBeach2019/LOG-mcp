"""Profile manager for custom and default draft profiles.

Loads/saves from ~/.log/vault/profiles.json. Merges user profiles
with built-in defaults. Provides CRUD operations.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from vault.config import VaultSettings

PROFILES_PATH = Path.home() / ".log" / "vault" / "profiles.json"

# Built-in default profiles. These can be overridden by user profiles
# with the same name, but cannot be deleted.
DEFAULT_PROFILES: list[dict[str, Any]] = [
    {
        "name": "precise",
        "temperature": 0.2,
        "system": "Be precise and concise. One sentence approach only. Under 280 characters.",
        "max_chars": 280,
        "is_default": True,
    },
    {
        "name": "creative",
        "temperature": 0.7,
        "system": "Think creatively. One sentence approach only. Under 280 characters.",
        "max_chars": 280,
        "is_default": True,
    },
    {
        "name": "deep",
        "temperature": 0.1,
        "system": "Reason step by step. One sentence. Under 280 characters.",
        "max_chars": 280,
        "is_default": True,
        "_use_escalation": True,
    },
]

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ProfileManager:
    """Manages draft profiles: built-in defaults + user custom profiles."""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else PROFILES_PATH
        self._user_profiles: list[dict[str, Any]] = []
        self._load()

    # -- persistence --

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._user_profiles = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._user_profiles = []
        else:
            self._user_profiles = []
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._user_profiles, indent=2))

    # -- CRUD --

    def list_profiles(self, settings: VaultSettings | None = None) -> list[dict[str, Any]]:
        """Return merged list of all profiles with resolved endpoint/model."""
        settings = settings or VaultSettings()
        default_names = {d["name"] for d in DEFAULT_PROFILES}
        user_names = {p["name"] for p in self._user_profiles}

        result: list[dict[str, Any]] = []

        # Add defaults not overridden by user
        for d in DEFAULT_PROFILES:
            if d["name"] not in user_names:
                resolved = self._resolve_profile(d, settings)
                result.append(resolved)

        # Add all user profiles (overrides or new)
        for p in self._user_profiles:
            resolved = self._resolve_profile(p, settings)
            resolved["is_default"] = resolved["name"] in default_names
            result.append(resolved)

        return result

    def add_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Add or update a custom profile. Returns the saved profile."""
        name = profile.get("name", "").strip()
        if not name:
            raise ValueError("profile name is required")
        name = name.lower()
        if not _NAME_RE.match(name):
            raise ValueError(f"invalid profile name: {name!r} (must be lowercase alphanumeric, starting with a-z or 0-9)")
        if not profile.get("endpoint") or not profile.get("model"):
            raise ValueError("endpoint and model are required")

        entry: dict[str, Any] = {
            "name": name,
            "endpoint": profile["endpoint"],
            "model": profile["model"],
        }
        if "temperature" in profile:
            entry["temperature"] = profile["temperature"]
        if "system_prompt" in profile:
            entry["system_prompt"] = profile["system_prompt"]
        elif "system" in profile:
            entry["system_prompt"] = profile["system"]
        if "max_chars" in profile:
            entry["max_chars"] = profile["max_chars"]

        # Update existing or append
        for i, p in enumerate(self._user_profiles):
            if p["name"] == name:
                self._user_profiles[i] = entry
                self._save()
                return entry
        self._user_profiles.append(entry)
        self._save()
        return entry

    def remove_profile(self, name: str) -> bool:
        """Remove a custom profile. Cannot remove defaults. Returns True if removed."""
        name = name.strip().lower()
        default_names = {d["name"] for d in DEFAULT_PROFILES}
        if name in default_names:
            raise ValueError(f"cannot delete default profile: {name}")
        before = len(self._user_profiles)
        self._user_profiles = [p for p in self._user_profiles if p["name"] != name]
        if len(self._user_profiles) < before:
            self._save()
            return True
        return False

    def reset_defaults(self) -> None:
        """Remove all user profiles that override defaults, keep truly custom ones."""
        default_names = {d["name"] for d in DEFAULT_PROFILES}
        self._user_profiles = [p for p in self._user_profiles if p["name"] not in default_names]
        self._save()

    # -- internal --

    @staticmethod
    def _resolve_profile(profile: dict[str, Any], settings: VaultSettings) -> dict[str, Any]:
        """Resolve a profile dict to have endpoint and model fields directly."""
        out = dict(profile)

        # If it already has endpoint/model, keep them
        if "endpoint" in profile and "model" in profile:
            pass
        else:
            # Legacy: resolve via settings keys
            use_esc = profile.get("_use_escalation", False)
            endpoint_key = profile.get("endpoint_key",
                                       "escalation_model_endpoint" if use_esc else "cheap_model_endpoint")
            model_key = profile.get("model_key",
                                    "escalation_model_name" if use_esc else "cheap_model_name")
            out["endpoint"] = getattr(settings, endpoint_key, "")
            out["model"] = getattr(settings, model_key, "")

        # Normalize system_prompt field
        if "system_prompt" not in out and "system" in out:
            out["system_prompt"] = out.pop("system")

        # Set defaults
        out.setdefault("temperature", 0.7)
        out.setdefault("max_chars", 0)
        out.setdefault("is_default", False)

        # Strip internal keys
        out.pop("_use_escalation", None)
        out.pop("endpoint_key", None)
        out.pop("model_key", None)

        return out


def get_draft_profiles(settings: VaultSettings | None = None) -> list[dict[str, Any]]:
    """Convenience function: return all profiles as resolved dicts."""
    manager = ProfileManager()
    return manager.list_profiles(settings)
