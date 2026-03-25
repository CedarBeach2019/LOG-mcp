"""Phase 2 gateway integration tests."""

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test-key")

from gateway.server import app


@pytest.fixture(autouse=True)
def reset_deps(tmp_path):
    """Reset singletons between tests with fresh DB."""
    from gateway.deps import reset_all
    db = str(tmp_path / "test.db")
    reset_all(db)
    yield
    reset_all()


@pytest.fixture
def client():
    return TestClient(app)


def _get_token(client):
    resp = client.post("/auth/login", json={"passphrase": "testpass"})
    return resp.json()["token"]


def _auth_headers(client):
    return {"Authorization": f"Bearer {_get_token(client)}"}


class TestRouting:
    def test_simple_question_routes_cheap(self, client):
        """'What is' questions should route to cheap model."""
        with patch("gateway.routes.call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (200, {
                "choices": [{"message": {"content": "The capital is Paris."}}]
            }, "")
            resp = client.post(
                "/v1/chat/completions",
                headers=_auth_headers(client),
                json={"model": "default", "messages": [{"role": "user", "content": "What is the capital of France?"}]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["route"]["action"] == "CHEAP_ONLY"

    def test_complex_question_escapes(self, client):
        """Debug questions should escalate."""
        with patch("gateway.routes.call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (200, {
                "choices": [{"message": {"content": "The issue is..."}}]
            }, "")
            resp = client.post(
                "/v1/chat/completions",
                headers=_auth_headers(client),
                json={"model": "default", "messages": [{"role": "user", "content": "Debug this traceback: NameError: name 'x' is not defined"}]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["route"]["action"] == "ESCALATE"

    def test_response_has_route_metadata(self, client):
        """Every response must include route metadata and interaction_id."""
        with patch("gateway.routes.call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (200, {
                "choices": [{"message": {"content": "OK"}}]
            }, "")
            resp = client.post(
                "/v1/chat/completions",
                headers=_auth_headers(client),
                json={"model": "default", "messages": [{"role": "user", "content": "Hello"}]},
            )
            data = resp.json()
            assert "route" in data
            assert "action" in data["route"]
            assert "target_model" in data["route"]
            assert "confidence" in data["route"]
            assert "interaction_id" in data


class TestPIIProtectionPhase2:
    def test_no_pii_in_upstream(self, client):
        """PII must never reach upstream API."""
        with patch("gateway.routes.call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (200, {
                "choices": [{"message": {"content": "Done."}}]
            }, "")
            resp = client.post(
                "/v1/chat/completions",
                headers=_auth_headers(client),
                json={"model": "default", "messages": [{"role": "user", "content": "Email sarah@gmail.com SSN 123-45-6789"}]},
            )
            assert resp.status_code == 200
            # Check upstream call — messages is 4th positional arg
            call_args = mock_call.call_args
            sent_messages = call_args[0][3]
            all_text = " ".join(m["content"] for m in sent_messages)
            assert "sarah@gmail.com" not in all_text
            assert "123-45-6789" not in all_text


class TestFeedback:
    def test_thumbs_up(self, client):
        """Can submit thumbs up feedback."""
        with patch("gateway.routes.call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (200, {
                "choices": [{"message": {"content": "Done."}}]
            }, "")
            # First create an interaction
            resp = client.post(
                "/v1/chat/completions",
                headers=_auth_headers(client),
                json={"model": "default", "messages": [{"role": "user", "content": "test"}]},
            )
            interaction_id = resp.json()["interaction_id"]

        # Submit feedback
        resp = client.post(
            "/v1/feedback",
            headers=_auth_headers(client),
            json={"interaction_id": interaction_id, "feedback": "up"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_thumbs_down_with_critique(self, client):
        """Can submit thumbs down with critique."""
        with patch("gateway.routes.call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (200, {
                "choices": [{"message": {"content": "Done."}}]
            }, "")
            resp = client.post(
                "/v1/chat/completions",
                headers=_auth_headers(client),
                json={"model": "default", "messages": [{"role": "user", "content": "test"}]},
            )
            interaction_id = resp.json()["interaction_id"]

        resp = client.post(
            "/v1/feedback",
            headers=_auth_headers(client),
            json={"interaction_id": interaction_id, "feedback": "down", "critique": "Too verbose"},
        )
        assert resp.status_code == 200

    def test_invalid_interaction_404(self, client):
        """Non-existent interaction returns 404."""
        resp = client.post(
            "/v1/feedback",
            headers=_auth_headers(client),
            json={"interaction_id": 99999, "feedback": "up"},
        )
        assert resp.status_code == 404


class TestPreferences:
    def test_list_preferences(self, client):
        resp = client.get("/v1/preferences", headers=_auth_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "response_style" in data

    def test_set_preference(self, client):
        resp = client.post(
            "/v1/preferences",
            headers=_auth_headers(client),
            json={"key": "custom_pref", "value": "test_value"},
        )
        assert resp.status_code == 200

    def test_delete_preference(self, client):
        # Set first
        client.post("/v1/preferences", headers=_auth_headers(client),
                     json={"key": "temp_pref", "value": "temp"})
        # Delete
        resp = client.delete("/v1/preferences/temp_pref", headers=_auth_headers(client))
        assert resp.status_code == 200


class TestHealth:
    def test_health_returns_json(self, client):
        resp = client.get("/v1/health")
        # May fail to connect to real services, but should return JSON
        assert resp.status_code == 200
        data = resp.json()
        assert "ollama" in data
