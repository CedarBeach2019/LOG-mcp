"""Tests for vault/providers — provider registry, config, and failover chain."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from vault.providers import (
    ProviderConfig,
    ProviderRegistry,
    BUILTIN_PROVIDERS,
    get_registry,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_registry()
    yield
    reset_registry()


class TestProviderConfig:
    def test_from_dict(self):
        p = ProviderConfig.from_dict(BUILTIN_PROVIDERS["deepseek"])
        assert p.name == "deepseek"
        assert p.base_url == "https://api.deepseek.com/v1/chat/completions"
        assert p.auth_prefix == "Bearer"
        assert "deepseek-chat" in p.models

    def test_auth_headers(self):
        p = ProviderConfig(name="test", api_key="sk-123")
        assert p.get_auth_headers() == {"Authorization": "Bearer sk-123"}

    def test_auth_headers_no_key(self):
        p = ProviderConfig(name="test")
        assert p.get_auth_headers() == {}

    def test_auth_headers_custom_header(self):
        p = ProviderConfig(name="test", auth_header="X-API-Key", auth_prefix="", api_key="key123")
        assert p.get_auth_headers() == {"X-API-Key": " key123"}

    def test_supports(self):
        p = ProviderConfig(name="test", capabilities=["chat", "streaming"])
        assert p.supports("chat") is True
        assert p.supports("vision") is False

    def test_get_model(self):
        p = ProviderConfig.from_dict(BUILTIN_PROVIDERS["deepseek"])
        m = p.get_model("deepseek-chat")
        assert m is not None
        assert m["tier"] == "cheap"

    def test_get_model_missing(self):
        p = ProviderConfig.from_dict(BUILTIN_PROVIDERS["deepseek"])
        assert p.get_model("nonexistent") is None

    def test_get_tier_model(self):
        p = ProviderConfig.from_dict(BUILTIN_PROVIDERS["deepseek"])
        assert p.get_tier_model("cheap") == "deepseek-chat"
        assert p.get_tier_model("escalation") == "deepseek-reasoner"
        assert p.get_tier_model("nonexistent") is None

    def test_to_dict_masks_key(self):
        p = ProviderConfig(name="test", api_key="sk-long-secret-key-here")
        d = p.to_dict()
        assert d["api_key"] == "sk-long-..."
        assert "sk-long-secret" not in d["api_key"]

    def test_to_dict_no_key(self):
        p = ProviderConfig(name="test")
        d = p.to_dict()
        assert "api_key" not in d


class TestProviderRegistry:
    def test_register_builtin(self):
        reg = ProviderRegistry()
        reg.register_builtin("deepseek")
        assert reg.get("deepseek") is not None
        assert reg.get("deepseek").name == "deepseek"

    def test_register_unknown_builtin_raises(self):
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="Unknown"):
            reg.register_builtin("nonexistent")

    def test_register_all_builtins(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        assert len(reg.list_all()) >= 5
        assert reg.get("deepseek") is not None
        assert reg.get("groq") is not None
        assert reg.get("openai") is not None
        assert reg.get("openrouter") is not None
        assert reg.get("local") is not None

    def test_register_custom_provider(self):
        reg = ProviderRegistry()
        custom = ProviderConfig(name="custom", base_url="http://localhost:8080/v1/chat/completions",
                                 api_key="local-key", models={"my-model": {"tier": "cheap"}})
        reg.register(custom)
        assert reg.get("custom") is not None
        assert reg.get("custom").base_url == "http://localhost:8080/v1/chat/completions"

    def test_list_providers_enabled_only(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        # No API keys set, so enabled list should be empty
        assert reg.get_enabled_providers() == []

    def test_get_enabled_providers_with_keys(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        reg.update_api_key("deepseek", "sk-test")
        reg.update_api_key("groq", "gsk-test")
        enabled = reg.get_enabled_providers()
        names = [p.name for p in enabled]
        assert "deepseek" in names
        assert "groq" in names
        assert "openai" not in names  # no key

    def test_get_enabled_local_no_key_needed(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        # Local doesn't need an API key
        chain = reg.get_failover_chain(["local"])
        assert len(chain) == 1
        assert chain[0].name == "local"

    def test_failover_chain_filters(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        reg.update_api_key("deepseek", "sk-test")
        reg.update_api_key("openai", "sk-openai")
        chain = reg.get_failover_chain(["deepseek", "openai", "groq"])
        assert len(chain) == 2
        assert [p.name for p in chain] == ["deepseek", "openai"]

    def test_failover_chain_default_all_enabled(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        reg.update_api_key("deepseek", "sk-test")
        reg.update_api_key("groq", "gsk-test")
        chain = reg.get_failover_chain()
        assert len(chain) >= 2

    def test_get_provider_for_model(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        p = reg.get_provider_for_model("deepseek-chat")
        assert p is not None
        assert p.name == "deepseek"

    def test_get_provider_for_model_unknown(self):
        reg = ProviderRegistry()
        reg.register_all_builtins()
        assert reg.get_provider_for_model("nonexistent-model") is None

    def test_update_api_key(self):
        reg = ProviderRegistry()
        reg.register_builtin("deepseek")
        assert reg.update_api_key("deepseek", "sk-new") is True
        assert reg.get("deepseek").api_key == "sk-new"

    def test_update_api_key_unknown(self):
        reg = ProviderRegistry()
        assert reg.update_api_key("nonexistent", "sk-123") is False

    def test_set_enabled(self):
        reg = ProviderRegistry()
        reg.register_builtin("deepseek")
        assert reg.set_enabled("deepseek", False) is True
        assert reg.get("deepseek").enabled is False

    def test_set_enabled_unknown(self):
        reg = ProviderRegistry()
        assert reg.set_enabled("nonexistent", True) is False

    def test_update_api_key_enables_provider(self):
        reg = ProviderRegistry()
        reg.register_builtin("deepseek")
        reg.get("deepseek").enabled = False
        reg.update_api_key("deepseek", "sk-test")
        assert reg.get("deepseek").enabled is True


class TestBuiltinProviders:
    def test_all_builtins_have_required_fields(self):
        required = ["name", "base_url", "auth_header", "auth_prefix", "models", "capabilities"]
        for name, data in BUILTIN_PROVIDERS.items():
            for field in required:
                assert field in data, f"{name} missing {field}"

    def test_all_have_at_least_one_model(self):
        for name, data in BUILTIN_PROVIDERS.items():
            assert len(data["models"]) >= 1, f"{name} has no models"

    def test_all_models_have_tier(self):
        for name, data in BUILTIN_PROVIDERS.items():
            for model_name, model_data in data["models"].items():
                assert "tier" in model_data, f"{name}/{model_name} missing tier"

    def test_non_local_have_base_url(self):
        for name, data in BUILTIN_PROVIDERS.items():
            if name != "local":
                assert data["base_url"].startswith("http"), f"{name} has no base_url"


class TestGetRegistry:
    def test_returns_singleton(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_has_all_builtins(self):
        reg = get_registry()
        assert reg.get("deepseek") is not None
        assert reg.get("local") is not None

    def test_reset_creates_new(self):
        r1 = get_registry()
        reset_registry()
        r2 = get_registry()
        assert r1 is not r2
