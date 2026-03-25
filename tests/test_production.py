"""Tests for production readiness: health, rate limit, startup, shutdown."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from unittest.mock import patch, MagicMock
from gateway.rate_limit import RateLimiter, get_limiter
from gateway.startup import validate_startup


class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter(max_requests=5, window_seconds=60, burst=10)
        for _ in range(5):
            allowed, _ = rl.check("test-key")
        assert allowed is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60, burst=10)
        for _ in range(3):
            rl.check("test-key")
        allowed, info = rl.check("test-key")
        assert allowed is False
        assert "remaining" in info

    def test_burst_limit(self):
        rl = RateLimiter(max_requests=100, window_seconds=60, burst=2)
        rl.check("burst-key")
        rl.check("burst-key")
        allowed, info = rl.check("burst-key")
        assert allowed is False
        assert info.get("reason") == "burst"

    def test_separate_keys(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        rl.check("key-a")
        allowed_a, _ = rl.check("key-a")
        allowed_b, _ = rl.check("key-b")
        assert allowed_a is False
        assert allowed_b is True

    def test_reset(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        rl.check("key")
        assert rl.check("key")[0] is False
        rl.reset("key")
        assert rl.check("key")[0] is True

    def test_info_has_remaining(self):
        rl = RateLimiter(max_requests=10, window_seconds=60)
        _, info = rl.check("key")
        assert info["remaining"] == 9
        assert info["limit"] == 10


class TestStartupValidation:
    def test_validates_db_path(self, tmp_path):
        from vault.config import VaultSettings
        s = VaultSettings(db_path=str(tmp_path / "test" / "vault.db"))
        warnings = validate_startup(s)
        assert isinstance(warnings, list)

    def test_warns_default_passphrase(self, tmp_path):
        from vault.config import VaultSettings
        s = VaultSettings(db_path=str(tmp_path / "test.db"), passphrase="changeme")
        warnings = validate_startup(s)
        assert any("passphrase" in w for w in warnings)

    def test_warns_bad_api_key_format(self, tmp_path):
        from vault.config import VaultSettings
        s = VaultSettings(db_path=str(tmp_path / "test.db"), api_key="not-an-sk-key")
        warnings = validate_startup(s)
        assert any("API key" in w for w in warnings)

    def test_raises_on_unwritable_db(self):
        from vault.config import VaultSettings
        s = VaultSettings(db_path="/nonexistent/path/to/db.db")
        with pytest.raises(ValueError, match="not writable"):
            validate_startup(s)

    def test_warns_invalid_cache_threshold(self, tmp_path):
        from vault.config import VaultSettings
        s = VaultSettings(db_path=str(tmp_path / "test.db"), cache_similarity_threshold=1.5)
        warnings = validate_startup(s)
        assert any("threshold" in w for w in warnings)


class TestHealthEndpoint:
    def test_health_returns_structured(self, client):
        resp = client.get("/v1/health")
        data = resp.json()
        assert "status" in data  # "ok" or "degraded"
        assert "checks" in data
        assert "database" in data["checks"]
        # May be 200 or 503 depending on environment
        assert resp.status_code in (200, 503)

    def test_health_has_disk_check(self, client):
        resp = client.get("/v1/health")
        data = resp.json()
        assert "disk" in data["checks"]
        assert "free_gb" in data["checks"]["disk"]

    def test_404_returns_json(self, client):
        resp = client.get("/v1/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data


class TestGracefulShutdown:
    def test_shutdown_handler_exists(self):
        """Shutdown handler should be registered in the app lifespan."""
        from gateway.server import _on_shutdown
        assert callable(_on_shutdown)
