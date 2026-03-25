"""Singleton factories for VaultSettings and RealLog."""

from vault.config import VaultSettings
from vault.core import RealLog

_settings: VaultSettings | None = None
_reallog: RealLog | None = None


def get_settings() -> VaultSettings:
    """Return the global VaultSettings singleton."""
    global _settings
    if _settings is None:
        _settings = VaultSettings()
    return _settings


def get_reallog() -> RealLog:
    """Return the global RealLog singleton."""
    global _reallog
    if _reallog is None:
        _reallog = RealLog(settings=get_settings())
        _reallog._seed_default_preferences()
    return _reallog


def reset_all(db_path: str | None = None):
    """Reset singletons. Optionally override DB path for next init."""
    global _settings, _reallog
    if _reallog is not None:
        try:
            conn = _reallog._get_connection()
            conn.close()
        except Exception:
            pass
    _reallog = None
    _settings = None
    if db_path is not None:
        import os
        os.environ["LOG_DB_PATH"] = db_path
