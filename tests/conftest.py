"""Shared test fixtures."""

import os

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_deps(tmp_path):
    """Reset singletons between every test with fresh DB."""
    db = str(tmp_path / "vault" / "test.db")
    os.environ["LOG_DB_PATH"] = db
    os.environ["LOG_PASSPHRASE"] = "testpass"
    os.environ["LOG_API_KEY"] = "sk-test"

    from gateway.deps import reset_all
    reset_all(db)

    # Reset rate limiter singleton so tests don't share rate limit state
    import gateway.rate_limit
    gateway.rate_limit._limiter = None

    # Reset provider registry singleton
    import vault.providers
    vault.providers._registry = None

    yield
    reset_all(db)
    gateway.rate_limit._limiter = None
    vault.providers._registry = None


@pytest.fixture
def client():
    from gateway.server import app
    return TestClient(app)
