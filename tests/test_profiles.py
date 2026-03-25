"""Tests for virtual provider profiles — vault/profiles.py and API endpoints."""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from vault.profiles import ProfileManager, DEFAULT_PROFILES, get_draft_profiles
from vault.config import VaultSettings


@pytest.fixture
def tmp_profile_path(tmp_path):
    return tmp_path / "profiles.json"


@pytest.fixture
def manager(tmp_profile_path):
    return ProfileManager(tmp_profile_path)


@pytest.fixture
def settings():
    s = VaultSettings()
    s.cheap_model_endpoint = "https://cheap.example.com/v1/chat/completions"
    s.cheap_model_name = "cheap-model"
    s.escalation_model_endpoint = "https://escalation.example.com/v1/chat/completions"
    s.escalation_model_name = "escalation-model"
    return s


# ---------------------------------------------------------------------------
# Profile manager CRUD tests
# ---------------------------------------------------------------------------

class TestProfileManagerCRUD:

    def test_list_includes_defaults(self, manager, settings):
        profiles = manager.list_profiles(settings)
        names = [p["name"] for p in profiles]
        assert "precise" in names
        assert "creative" in names
        assert "deep" in names

    def test_default_profiles_have_endpoint_and_model(self, manager, settings):
        profiles = manager.list_profiles(settings)
        for p in profiles:
            if p["name"] in ("precise", "creative"):
                assert p["endpoint"] == settings.cheap_model_endpoint
                assert p["model"] == settings.cheap_model_name
            elif p["name"] == "deep":
                assert p["endpoint"] == settings.escalation_model_endpoint
                assert p["model"] == settings.escalation_model_name

    def test_add_custom_profile(self, manager, settings):
        manager.add_profile({
            "name": "my-custom",
            "endpoint": "https://my.api.com/v1/chat/completions",
            "model": "my-model",
            "temperature": 0.5,
            "system_prompt": "Be funny.",
        })
        profiles = manager.list_profiles(settings)
        custom = next(p for p in profiles if p["name"] == "my-custom")
        assert custom["endpoint"] == "https://my.api.com/v1/chat/completions"
        assert custom["model"] == "my-model"
        assert custom["temperature"] == 0.5

    def test_add_profile_lowercases_name(self, manager, settings):
        manager.add_profile({
            "name": "My-Custom",
            "endpoint": "https://x.com/v1/chat/completions",
            "model": "x",
        })
        profiles = manager.list_profiles(settings)
        assert any(p["name"] == "my-custom" for p in profiles)

    def test_add_profile_rejects_bad_name(self, manager):
        with pytest.raises(ValueError):
            manager.add_profile({
                "name": "",
                "endpoint": "https://x.com",
                "model": "x",
            })
        with pytest.raises(ValueError, match="invalid profile name"):
            manager.add_profile({
                "name": "has spaces",
                "endpoint": "https://x.com",
                "model": "x",
            })

    def test_add_profile_requires_endpoint_and_model(self, manager):
        with pytest.raises(ValueError, match="endpoint and model are required"):
            manager.add_profile({"name": "test"})

    def test_remove_custom_profile(self, manager, settings):
        manager.add_profile({
            "name": "removable",
            "endpoint": "https://x.com/v1/chat/completions",
            "model": "x",
        })
        assert manager.remove_profile("removable") is True
        profiles = manager.list_profiles(settings)
        assert not any(p["name"] == "removable" for p in profiles)

    def test_cannot_remove_default_profile(self, manager):
        with pytest.raises(ValueError, match="cannot delete default profile"):
            manager.remove_profile("precise")

    def test_override_default_profile(self, manager, settings):
        manager.add_profile({
            "name": "precise",
            "endpoint": "https://custom.example.com/v1/chat/completions",
            "model": "custom-model",
            "temperature": 0.0,
            "system_prompt": "New system prompt.",
        })
        profiles = manager.list_profiles(settings)
        precise = next(p for p in profiles if p["name"] == "precise")
        assert precise["endpoint"] == "https://custom.example.com/v1/chat/completions"
        assert precise["model"] == "custom-model"
        assert precise["temperature"] == 0.0
        # Only one "precise"
        assert sum(1 for p in profiles if p["name"] == "precise") == 1

    def test_reset_defaults(self, manager, settings):
        manager.add_profile({
            "name": "precise",
            "endpoint": "https://custom.example.com/v1/chat/completions",
            "model": "custom-model",
        })
        manager.add_profile({
            "name": "truly-custom",
            "endpoint": "https://x.com/v1/chat/completions",
            "model": "x",
        })
        manager.reset_defaults()
        profiles = manager.list_profiles(settings)
        # precise restored to default
        precise = next(p for p in profiles if p["name"] == "precise")
        assert precise["endpoint"] == settings.cheap_model_endpoint
        # truly-custom preserved
        assert any(p["name"] == "truly-custom" for p in profiles)

    def test_persistence(self, tmp_profile_path, settings):
        mgr1 = ProfileManager(tmp_profile_path)
        mgr1.add_profile({
            "name": "persisted",
            "endpoint": "https://x.com/v1/chat/completions",
            "model": "x",
        })
        mgr2 = ProfileManager(tmp_profile_path)
        profiles = mgr2.list_profiles(settings)
        assert any(p["name"] == "persisted" for p in profiles)

    def test_get_draft_profiles_function(self, tmp_profile_path, settings):
        with patch("vault.profiles.PROFILES_PATH", tmp_profile_path):
            profiles = get_draft_profiles(settings)
        names = [p["name"] for p in profiles]
        assert "precise" in names

    def test_legacy_endpoint_key_model_key_still_works(self, manager, settings):
        """Profiles with endpoint_key/model_key (old style) should still resolve."""
        manager._user_profiles.append({
            "name": "legacy",
            "endpoint_key": "escalation_model_endpoint",
            "model_key": "escalation_model_name",
            "temperature": 0.3,
            "system": "Legacy profile.",
        })
        profiles = manager.list_profiles(settings)
        legacy = next(p for p in profiles if p["name"] == "legacy")
        assert legacy["endpoint"] == settings.escalation_model_endpoint
        assert legacy["model"] == settings.escalation_model_name
        assert "endpoint_key" not in legacy


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestProfileAPIEndpoints:

    @pytest.fixture
    def app(self):
        from gateway.server import app
        return app

    @pytest.fixture
    def auth_headers(self):
        from gateway.auth import create_token
        from vault.core import RealLog
        import tempfile
        db = tempfile.mktemp(suffix=".db")
        rl = RealLog(db)
        secret = rl._get_connection().execute(
            "SELECT value FROM kv_store WHERE key='jwt_secret'"
        ).fetchone()
        if secret is None:
            from gateway.auth import _generate_secret
            _generate_secret(rl)
            secret = rl._get_connection().execute(
                "SELECT value FROM kv_store WHERE key='jwt_secret'"
            ).fetchone()
        token = create_token(secret[0])
        rl.close()
        return {"Authorization": f"Bearer {token}"}

    def test_list_profiles(self, app):
        pass  # integration test needs auth setup

    def test_create_and_delete_profile(self, manager, settings):
        manager.add_profile({
            "name": "api-test",
            "endpoint": "https://test.example.com/v1/chat/completions",
            "model": "test-model",
        })
        profiles = manager.list_profiles(settings)
        assert any(p["name"] == "api-test" for p in profiles)
        manager.remove_profile("api-test")
        profiles = manager.list_profiles(settings)
        assert not any(p["name"] == "api-test" for p in profiles)

    def test_system_prompt_alias(self, manager, settings):
        """Both system_prompt and system should work."""
        manager.add_profile({
            "name": "alias-test",
            "endpoint": "https://x.com/v1/chat/completions",
            "model": "x",
            "system": "Via system field.",
        })
        profiles = manager.list_profiles(settings)
        p = next(p for p in profiles if p["name"] == "alias-test")
        assert p["system_prompt"] == "Via system field."
        assert "system" not in p

    def test_is_default_flag(self, manager, settings):
        profiles = manager.list_profiles(settings)
        for p in profiles:
            if p["name"] in ("precise", "creative", "deep"):
                assert p["is_default"] is True

    def test_remove_nonexistent_returns_false(self, manager):
        assert manager.remove_profile("does-not-exist") is False
