"""Startup validation — fail fast if config is broken."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("gateway.startup")


def validate_startup(settings) -> list[str]:
    """Validate settings at startup. Returns list of warnings (empty = ok).

    Raises ValueError for fatal issues.
    """
    warnings = []

    # DB path must be writable
    db_path = Path(settings.db_path)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Test write access
        test_file = db_path.parent / ".startup_check"
        test_file.write_text("ok")
        test_file.unlink()
    except Exception as exc:
        raise ValueError(f"Database path not writable: {db_path} — {exc}")

    # Models directory
    try:
        Path(settings.local_models_dir).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        warnings.append(f"Models dir not writable: {settings.local_models_dir} — {exc}")

    # API key format (basic check)
    if settings.api_key and not settings.api_key.startswith("sk-"):
        warnings.append(f"API key doesn't start with 'sk-': {settings.api_key[:8]}...")

    # Endpoints must be valid URLs
    for name, url in [
        ("cheap_model_endpoint", settings.cheap_model_endpoint),
        ("escalation_model_endpoint", settings.escalation_model_endpoint),
    ]:
        if not url.startswith("http"):
            warnings.append(f"{name} doesn't look like a URL: {url}")

    # Passphrase
    if settings.passphrase in ("changeme", "testpass", "", "password"):
        warnings.append("Default passphrase detected — change for production")

    # Cache similarity threshold
    if not 0.0 < settings.cache_similarity_threshold <= 1.0:
        warnings.append(f"Invalid cache_similarity_threshold: {settings.cache_similarity_threshold}")

    # GPU layers sanity
    if settings.local_gpu_layers < -1 or settings.local_gpu_layers == 0:
        warnings.append("local_gpu_layers=0 means CPU-only inference")

    if warnings:
        for w in warnings:
            logger.warning("Startup warning: %s", w)

    return warnings
