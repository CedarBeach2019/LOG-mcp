"""Tests for draft round endpoints."""

import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from starlette.testclient import TestClient

os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test-key")

from gateway.server import app


@pytest.fixture(autouse=True)
def reset_deps():
    from gateway import deps
    deps._settings = None
    deps._reallog = None
    yield
    deps._settings = None
    deps._reallog = None


@pytest.fixture
def client():
    return TestClient(app)


def _token(client):
    return client.post("/auth/login", json={"passphrase": "testpass"}).json()["token"]


def _headers(client):
    return {"Authorization": f"Bearer {_token(client)}"}


class TestDraftsEndpoint:
    def test_drafts_requires_auth(self, client):
        resp = client.post("/v1/drafts", json={})
        assert resp.status_code == 401

    @patch("gateway.routes.httpx.AsyncClient")
    def test_drafts_returns_responses(self, mock_client_cls, client):
        """Drafts should return profile results even with mocked upstream."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Use a hash map."}}]
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.post(
            "/v1/drafts",
            headers=_headers(client),
            json={"messages": [{"role": "user", "content": "How to do fast lookup?"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "drafts" in data
        assert len(data["drafts"]) == 3  # 3 default profiles
        # Each draft should have profile, response, model
        for d in data["drafts"]:
            assert "profile" in d
            assert "response" in d


class TestElaborateEndpoint:
    def test_elaborate_requires_auth(self, client):
        resp = client.post("/v1/elaborate", json={})
        assert resp.status_code == 401

    @patch("gateway.routes.httpx.AsyncClient")
    def test_elaborate_returns_full_response(self, mock_client_cls, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Full detailed response here."}}]
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.post(
            "/v1/elaborate",
            headers=_headers(client),
            json={
                "messages": [{"role": "user", "content": "test question"}],
                "winner_profile": "precise",
                "all_drafts": [
                    {"profile": "precise", "response": "Hash map approach"},
                    {"profile": "creative", "response": "Binary search tree"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data
        assert "Full detailed response" in data["choices"][0]["message"]["content"]

    @patch("gateway.routes.httpx.AsyncClient")
    def test_elaborate_includes_ranking_context(self, mock_client_cls, client):
        """Winner should receive context about other drafts."""
        captured_json = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OK"}}]
        }

        async def capture_post(*args, **kwargs):
            captured_json.update(kwargs.get("json", {}))
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client.post(
            "/v1/elaborate",
            headers=_headers(client),
            json={
                "messages": [{"role": "user", "content": "test"}],
                "winner_profile": "precise",
                "all_drafts": [
                    {"profile": "precise", "response": "A"},
                    {"profile": "creative", "response": "B"},
                ],
            },
        )
        # System message should mention other approaches
        system_msgs = [m for m in captured_json.get("messages", []) if m.get("role") == "system"]
        assert len(system_msgs) >= 1
        all_text = " ".join(m["content"] for m in system_msgs)
        assert "creative" in all_text or "B" in all_text
