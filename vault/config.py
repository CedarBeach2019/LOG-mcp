"""LOG-mcp configuration via environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class VaultSettings(BaseSettings):
    """Application settings. Override with LOG_ prefix env vars."""

    db_path: Path = Path.home() / ".log" / "vault" / "reallog.db"
    provider_endpoint: str = "https://api.deepseek.com/v1/chat/completions"
    api_key: str | None = None
    local_port: int = 8000
    rate_limit: int = 30
    max_body_bytes: int = 10240
    passphrase: str = "changeme"  # LOG_MCP_PASSPHRASE or LOG_PASSPHRASE

    model_config = SettingsConfigDict(env_prefix="LOG_")
