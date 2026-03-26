"""Tests for provider registry wiring into call_model."""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from gateway.shared import call_model
from vault.providers import ProviderConfig, ProviderRegistry, reset_registry


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


class TestProviderWiring:
    """Test that call_model uses provider registry when available."""

    @pytest.mark.asyncio
    async def test_no_match_falls_back(self):
        """If no provider matches the model, should use the provided endpoint/key."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "hi"}}]}
        mock_post = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock(post=mock_post)

        with patch("gateway.shared.get_client", return_value=mock_client):
            result = await call_model(
                "https://fallback.com/v1/chat/completions", "fallback-key",
                "unknown-model", [{"role": "user", "content": "hi"}]
            )
            assert result[0] == 200
            call_args = mock_post.call_args
            assert "fallback.com" in str(call_args)

    @pytest.mark.asyncio
    async def test_provider_match_resolves(self):
        """If a provider has the model, call_model should use its endpoint/key."""
        registry = ProviderRegistry()
        registry.register(ProviderConfig(
            name="test_provider",
            base_url="https://provider.example.com/v1/chat/completions",
            api_key="provider-key",
            models={"my-model": {"tier": "cheap"}},
        ))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "hi"}}]}
        mock_post = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock(post=mock_post)

        with patch("gateway.shared.get_client", return_value=mock_client):
            with patch("vault.providers._registry", registry):
                result = await call_model(
                    "https://fallback.com/v1/chat/completions", "fallback-key",
                    "my-model", [{"role": "user", "content": "hi"}]
                )
                assert result[0] == 200
                call_args = mock_post.call_args
                assert "provider.example.com" in str(call_args)

    @pytest.mark.asyncio
    async def test_registry_import_failure_falls_back(self):
        """If provider registry import fails, fall back gracefully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "hi"}}]}
        mock_post = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock(post=mock_post)

        with patch("gateway.shared.get_client", return_value=mock_client):
            with patch.dict("sys.modules", {"vault.providers": None}):
                result = await call_model(
                    "https://fallback.com/v1/chat/completions", "fallback-key",
                    "any-model", [{"role": "user", "content": "hi"}]
                )
                assert result[0] == 200
