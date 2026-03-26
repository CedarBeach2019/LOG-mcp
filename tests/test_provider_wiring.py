"""Tests for provider registry wiring into call_model."""

import pytest
from unittest.mock import patch, AsyncMock
from gateway.shared import call_model
from vault.providers import ProviderConfig, ProviderRegistry, reset_registry


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


class TestProviderWiring:
    """Test that call_model uses provider registry when available."""

    def test_no_match_falls_back(self):
        """If no provider matches the model, should use the provided endpoint/key."""
        registry = ProviderRegistry()
        registry.register(ProviderConfig(
            name="test_provider",
            base_url="https://example.com/v1/chat/completions",
            api_key="test-key",
            models={"other-model": {"tier": "cheap"}},
        ))

        # Call with a model not in any provider — should still work (uses fallback)
        # We're just testing the resolution logic doesn't crash
        import asyncio
        with patch("gateway.shared.get_client") as mock_client:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": "hi"}}]}
            mock_post = AsyncMock(return_value=mock_resp)
            mock_client.return_value.post = mock_post

            result = asyncio.get_event_loop().run_until_complete(
                call_model("https://fallback.com/v1/chat/completions", "fallback-key", "unknown-model", [{"role": "user", "content": "hi"}])
            )
            # Should have used the fallback URL, not the provider URL
            call_args = mock_post.call_args
            assert "fallback.com" in call_args[0][0] or "fallback.com" in str(call_args)

    def test_provider_match_resolves(self):
        """If a provider has the model, call_model should use its endpoint/key."""
        registry = ProviderRegistry()
        registry.register(ProviderConfig(
            name="test_provider",
            base_url="https://provider.example.com/v1/chat/completions",
            api_key="provider-key",
            models={"my-model": {"tier": "cheap"}},
        ))

        import asyncio
        with patch("gateway.shared.get_client") as mock_client:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": "hi"}}]}
            mock_post = AsyncMock(return_value=mock_resp)
            mock_client.return_value.post = mock_post

            # Patch get_registry to return our custom registry
            with patch("vault.providers._registry", registry):
                result = asyncio.get_event_loop().run_until_complete(
                    call_model("https://fallback.com/v1/chat/completions", "fallback-key", "my-model", [{"role": "user", "content": "hi"}])
                )
                call_args = mock_post.call_args
                # Should have used the provider URL, not the fallback
                assert "provider.example.com" in str(call_args)
