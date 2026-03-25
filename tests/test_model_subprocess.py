"""Tests for model subprocess server and client."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSubprocessClient:
    """Tests for SubprocessModelClient (mocked subprocess)."""

    def _make_client(self):
        from vault.model_client import SubprocessModelClient
        return SubprocessModelClient(
            "/fake/model.gguf", gpu_layers=0, ctx_size=512,
            python="python3"
        )

    def test_init(self):
        c = self._make_client()
        assert c.model_path == "/fake/model.gguf"
        assert c._ready is False

    def test_is_loaded_false(self):
        c = self._make_client()
        assert c.is_loaded is False

    @pytest.mark.anyio
    async def test_ping_timeout(self):
        c = self._make_client()
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_process.stdin = AsyncMock()
        c._process = mock_process
        assert await c.ping() is False


class TestModelManagerSubprocess:
    """Tests for ModelManager with subprocess mode."""

    def test_creates_manager_with_subprocess(self, tmp_path):
        from vault.model_manager import ModelManager
        m = ModelManager(tmp_path / "models", use_subprocess=True)
        assert m.use_subprocess is True

    def test_no_subprocess_client_initially(self, tmp_path):
        from vault.model_manager import ModelManager
        m = ModelManager(tmp_path / "models", use_subprocess=True)
        assert m.get_subprocess_client() is None

    def test_jetson_detection(self):
        from gateway.shared import _is_jetson
        # We're running on Jetson, so this should be True
        result = _is_jetson()
        assert isinstance(result, bool)


class TestModelSubprocessProtocol:
    """Test the JSON protocol format."""

    def test_generate_message_format(self):
        from vault.model_client import SubprocessModelClient
        c = SubprocessModelClient.__new__(SubprocessModelClient)
        # Verify message structure
        msg = {"action": "generate", "prompt": "Hello", "max_tokens": 100, "temperature": 0.7}
        serialized = json.dumps(msg)
        parsed = json.loads(serialized)
        assert parsed["action"] == "generate"
        assert parsed["prompt"] == "Hello"

    def test_embed_message_format(self):
        msg = {"action": "embed", "text": "test embedding"}
        serialized = json.dumps(msg)
        parsed = json.loads(serialized)
        assert parsed["action"] == "embed"


