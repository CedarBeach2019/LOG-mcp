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
    return _reallog
