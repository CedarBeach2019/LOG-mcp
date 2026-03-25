"""LOG-mcp configuration via environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    rate_limit: int = 30
    max_body_bytes: int = 10240

    model_config = SettingsConfigDict(env_prefix="LOG_")
