"""Shared test fixtures."""

import os

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """Test client with isolated database."""
    os.environ["LOG_DB_PATH"] = str(tmp_path / "vault" / "test.db")
    os.environ["LOG_PASSPHRASE"] = "testpass"
    os.environ["LOG_API_KEY"] = "sk-test"

    from gateway.deps import reset_all
    reset_all(str(tmp_path / "vault" / "test.db"))

    from gateway.server import app
    return TestClient(app)
