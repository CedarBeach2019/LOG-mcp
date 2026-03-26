"""Pluggable multi-provider abstraction for LOG-mcp.

Each provider is a dict-like config with name, base_url, auth, models, capabilities.
The ProviderRegistry manages discovery, lookup, and failover chains.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("vault.providers")

# Default provider definitions
BUILTIN_PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "name": "deepseek",
        "display_name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "models_url": "https://api.deepseek.com/v1/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
        "models": {
            "deepseek-chat": {"tier": "cheap", "streaming": True, "context": 64000},
            "deepseek-reasoner": {"tier": "escalation", "streaming": True, "context": 64000},
        },
        "capabilities": ["chat", "streaming", "function_calling"],
        "rate_limit_rpm": 60,
        "rate_limit_tpm": 120000,
        "pricing": {"input_per_1m": 0.14, "output_per_1m": 0.28},
    },
    "groq": {
        "name": "groq",
        "display_name": "Groq",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
        "models": {
            "llama-3.3-70b-versatile": {"tier": "cheap", "streaming": True, "context": 128000},
            "llama-3.1-8b-instant": {"tier": "cheap", "streaming": True, "context": 128000},
            "deepseek-r1-distill-llama-70b": {"tier": "escalation", "streaming": True, "context": 128000},
        },
        "capabilities": ["chat", "streaming", "function_calling"],
        "rate_limit_rpm": 30,
        "rate_limit_tpm": 18000,
        "pricing": {"input_per_1m": 0.59, "output_per_1m": 0.79},
    },
    "openai": {
        "name": "openai",
        "display_name": "OpenAI",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
        "models": {
            "gpt-4o-mini": {"tier": "cheap", "streaming": True, "context": 128000},
            "gpt-4o": {"tier": "escalation", "streaming": True, "context": 128000},
        },
        "capabilities": ["chat", "streaming", "function_calling", "vision"],
        "rate_limit_rpm": 500,
        "rate_limit_tpm": 200000,
        "pricing": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    },
    "openrouter": {
        "name": "openrouter",
        "display_name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
        "models": {
            "openai/gpt-4o-mini": {"tier": "cheap", "streaming": True, "context": 128000},
            "anthropic/claude-3.5-sonnet": {"tier": "escalation", "streaming": True, "context": 200000},
        },
        "capabilities": ["chat", "streaming", "function_calling", "vision"],
        "rate_limit_rpm": 20,
        "rate_limit_tpm": 200000,
        "pricing": {"input_per_1m": 0.0, "output_per_1m": 0.0},  # varies by model
    },
    "local": {
        "name": "local",
        "display_name": "Local (llama.cpp)",
        "base_url": "",  # Not HTTP-based
        "models_url": "",
        "auth_header": "",
        "auth_prefix": "",
        "models": {
            "local": {"tier": "cheap", "streaming": False, "context": 4096},
        },
        "capabilities": ["chat"],
        "rate_limit_rpm": 0,  # No rate limit
        "rate_limit_tpm": 0,
        "pricing": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    },
}


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    name: str
    display_name: str = ""
    base_url: str = ""
    models_url: str = ""
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    api_key: str | None = None
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    rate_limit_rpm: int = 0
    rate_limit_tpm: int = 0
    pricing: dict[str, float] = field(default_factory=dict)
    enabled: bool = True

    def get_auth_headers(self) -> dict[str, str]:
        """Build auth headers for this provider."""
        if not self.auth_header or not self.api_key:
            return {}
        return {self.auth_header: f"{self.auth_prefix} {self.api_key}"}

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def get_model(self, model_name: str) -> dict[str, Any] | None:
        return self.models.get(model_name)

    def get_tier_model(self, tier: str) -> str | None:
        """Get first model matching a tier (cheap/escalation)."""
        for name, info in self.models.items():
            if info.get("tier") == tier:
                return name
        return None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name, "display_name": self.display_name,
            "base_url": self.base_url, "enabled": self.enabled,
            "models": list(self.models.keys()),
            "capabilities": self.capabilities,
        }
        if self.api_key:
            d["api_key"] = "***MASKED***"
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ProviderRegistry:
    """Registry of LLM providers. Supports registration, lookup, and failover chains."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderConfig] = {}

    def register(self, provider: ProviderConfig) -> None:
        self._providers[provider.name] = provider
        logger.debug("Registered provider: %s", provider.name)

    def register_builtin(self, name: str) -> None:
        """Register a built-in provider by name (without API key)."""
        if name not in BUILTIN_PROVIDERS:
            raise ValueError(f"Unknown built-in provider: {name}")
        self.register(ProviderConfig.from_dict(BUILTIN_PROVIDERS[name]))

    def register_all_builtins(self) -> None:
        for name in BUILTIN_PROVIDERS:
            self.register_builtin(name)

    def get(self, name: str) -> ProviderConfig | None:
        return self._providers.get(name)

    def list_providers(self, enabled_only: bool = True) -> list[ProviderConfig]:
        providers = list(self._providers.values())
        if enabled_only:
            providers = [p for p in providers if p.enabled and p.api_key]
        return providers

    def list_all(self) -> list[ProviderConfig]:
        return list(self._providers.values())

    def get_enabled_providers(self) -> list[ProviderConfig]:
        """Providers that are enabled AND have an API key configured."""
        return [p for p in self._providers.values() if p.enabled and p.api_key]

    def get_failover_chain(self, chain: list[str] | None = None) -> list[ProviderConfig]:
        """Build an ordered failover chain. Filters to enabled providers with API keys.

        Args:
            chain: Provider names in order. None = all enabled providers.
        """
        if chain is None:
            chain = [p.name for p in self.get_enabled_providers()]
        result = []
        for name in chain:
            p = self._providers.get(name)
            if p and p.enabled and p.api_key:
                result.append(p)
            elif name == "local":
                p_local = self._providers.get("local")
                if p_local and p_local.enabled:
                    result.append(p_local)
        return result

    def get_provider_for_model(self, model_name: str) -> ProviderConfig | None:
        """Find which provider has a given model."""
        for p in self._providers.values():
            if model_name in p.models:
                return p
        return None

    def update_api_key(self, provider_name: str, api_key: str | None) -> bool:
        """Update API key for a provider. Returns True if provider exists."""
        p = self._providers.get(provider_name)
        if p is None:
            return False
        p.api_key = api_key
        if api_key:
            p.enabled = True
        return True

    def set_enabled(self, provider_name: str, enabled: bool) -> bool:
        p = self._providers.get(provider_name)
        if p is None:
            return False
        p.enabled = enabled
        return True


# Singleton
_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.register_all_builtins()
    return _registry


def reset_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    _registry = None
