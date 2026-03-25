"""Shared test fixtures."""

import os
import tempfile
import pytest

from vault.config import VaultSettings
from vault.core import RealLog, Dehydrator, Rehydrator


@pytest.fixture
def db_path():
    """Provide a temporary database path."""
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def settings(db_path):
    """Provide VaultSettings with temp DB."""
    return VaultSettings(db_path=db_path, passphrase="testpass", api_key="test-key")


@pytest.fixture
def reallog(settings):
    """Provide a RealLog instance with temp DB."""
    return RealLog(settings=settings)


@pytest.fixture
def dehydrator(reallog):
    return Dehydrator(reallog=reallog)


@pytest.fixture
def rehydrator(reallog):
    return Rehydrator(reallog=reallog)
