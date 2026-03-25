"""Gateway integration tests — Task 7."""

import json
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from starlette.testclient import TestClient

# Must set env before importing app
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test-key")

from gateway.server import app


@pytest.fixture
def client(tmp_path):
    """Test client with fresh vault."""
    from gateway import deps
    # Reset singletons
    deps._settings = None
    deps._reallog = None
    return TestClient(app)


class TestAuth:
    def test_login_success(self, client):
        resp = client.post("/auth/login", json={"passphrase": "testpass"})
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_wrong_passphrase(self, client):
        resp = client.post("/auth/login", json={"passphrase": "wrong"})
        assert resp.status_code == 401

    def test_login_missing_body(self, client):
        resp = client.post("/auth/login", json={})
        assert resp.status_code == 401

    def test_protected_endpoint_no_token(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 401


class TestPIIProtection:
    """Verify PII never reaches upstream API."""

    def _get_token(self, client):
        resp = client.post("/auth/login", json={"passphrase": "testpass"})
        return resp.json()["token"]

    @patch("gateway.routes.httpx.AsyncClient")
    def test_no_pii_in_upstream_request(self, mock_client_cls, client):
        """PII in user message must be stripped before forwarding to upstream."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "I can help with that."}}]
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        token = self._get_token(client)
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "default",
                "messages": [
                    {"role": "user", "content": "My email is sarah@gmail.com and SSN is 123-45-6789"}
                ],
            },
        )
        assert resp.status_code == 200

        # Check what was sent to upstream
        call_args = mock_client.post.call_args
        sent_body = call_args.kwargs["json"]
        sent_messages = sent_body["messages"]
        all_text = " ".join(m["content"] for m in sent_messages)

        # PII must NOT be in the forwarded text
        assert "sarah@gmail.com" not in all_text
        assert "123-45-6789" not in all_text
        # Tokens should be present
        assert "[EMAIL_" in all_text or "[EMAIL_A]" in all_text
        assert "[SSN_" in all_text or "[SSN_A]" in all_text

    @patch("gateway.routes.httpx.AsyncClient")
    def test_rehydration_restores_pii(self, mock_client_cls, client):
        """Response from upstream should have PII restored."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "I'll email [EMAIL_A] right away."}}]
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        token = self._get_token(client)
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "default",
                "messages": [
                    {"role": "user", "content": "Contact sarah@gmail.com about the meeting"}
                ],
            },
        )
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        # Rehydrated — original email should be in the response
        assert "sarah@gmail.com" in content


class TestServeIndex:
    def test_serves_index(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "html" in resp.headers.get("content-type", "").lower()
