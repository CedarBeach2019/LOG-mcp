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

    # Local inference (llama.cpp)
    local_models_dir: Path = Path.home() / ".log" / "models"
    local_gpu_layers: int = -1  # -1 = all layers on GPU
    local_max_tokens: int = 512
    local_ctx_size: int = 4096

    # Profiles
    custom_profiles_path: Path = Path.home() / ".log" / "vault" / "profiles.json"

    # Routing behavior
    instant_send: bool = True
    parallel_mode: bool = False
    privacy_mode: bool = True
    draft_mode: bool = True  # enable draft round feature
    rate_limit: int = 30
    max_body_bytes: int = 10240

    model_config = SettingsConfigDict(env_prefix="LOG_")

    def get_draft_profiles(self):
        """Build provider profiles from settings."""
        from vault.profiles import ProfileManager
        return ProfileManager().list_profiles(self)
