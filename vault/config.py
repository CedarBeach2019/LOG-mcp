"""LOG-mcp configuration via environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderProfile:
    """A model configuration profile for draft/compare mode."""
    def __init__(self, name: str, endpoint: str, model: str,
                 temperature: float = 0.7, system: str = "", max_chars: int = 0):
        self.name = name
        self.endpoint = endpoint
        self.model = model
        self.temperature = temperature
        self.system = system
        self.max_chars = max_chars

    def to_dict(self) -> dict:
        return {
            "name": self.name, "endpoint": self.endpoint, "model": self.model,
            "temperature": self.temperature, "system": self.system,
            "max_chars": self.max_chars,
        }


DEFAULT_DRAFT_PROFILES = [
    {"name": "precise", "temperature": 0.2,
     "system": "Be precise and concise. One sentence approach only. Under 280 characters.", "max_chars": 280},
    {"name": "creative", "temperature": 0.7,
     "system": "Think creatively. One sentence approach only. Under 280 characters.", "max_chars": 280},
    {"name": "deep", "temperature": 0.1,
     "system": "Reason step by step. One sentence. Under 280 characters.", "max_chars": 280, "_use_escalation": True},
]


class VaultSettings(BaseSettings):
    """Application settings. Override with LOG_ prefix env vars."""

    # Vault
    db_path: Path = Path.home() / ".log" / "vault" / "reallog.db"
    passphrase: str = "changeme"
    local_port: int = 8000

    # Cloud models
    api_key: str | None = None
    provider_endpoint: str = "https://api.deepseek.com/v1/chat/completions"
    cheap_model_endpoint: str = "https://api.deepseek.com/v1/chat/completions"
    cheap_model_name: str = "deepseek-chat"
    escalation_model_endpoint: str = "https://api.deepseek.com/v1/chat/completions"
    escalation_model_name: str = "deepseek-reasoner"

    # Local inference
    ollama_base_url: str = "http://localhost:11434"
    router_model: str = "qwen3.5:2b"

    # Routing behavior
    instant_send: bool = True
    parallel_mode: bool = False
    privacy_mode: bool = True
    draft_mode: bool = True  # enable draft round feature
    rate_limit: int = 30
    max_body_bytes: int = 10240

    model_config = SettingsConfigDict(env_prefix="LOG_")

    def get_draft_profiles(self) -> list[ProviderProfile]:
        """Build provider profiles from settings."""
        profiles = []
        for p in DEFAULT_DRAFT_PROFILES:
            use_esc = p.pop("_use_escalation", False)
            endpoint = self.escalation_model_endpoint if use_esc else self.cheap_model_endpoint
            model = self.escalation_model_name if use_esc else self.cheap_model_name
            profiles.append(ProviderProfile(
                name=p["name"], endpoint=endpoint, model=model,
                temperature=p["temperature"], system=p.get("system", ""),
                max_chars=p.get("max_chars", 0),
            ))
        return profiles
