"""Tests for session management endpoints."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from httpx import AsyncClient, ASGITransport

from gateway.server import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def auth_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/auth/login", json={"passphrase": "testpass"})
        return resp.json()["token"]


@pytest.fixture
async def headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.mark.anyio
async def test_create_session(headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/v1/sessions", json={"summary": "test session"}, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is True
        assert "id" in data


@pytest.mark.anyio
async def test_list_sessions(headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/v1/sessions", headers=headers)
        resp = await client.get("/v1/sessions", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        # At least the one we just created plus any from other tests
        assert len(data["sessions"]) >= 1


@pytest.mark.anyio
async def test_get_session(headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Create
        created = await client.post("/v1/sessions", json={"id": "test-get-unique"}, headers=headers)
        session_id = created.json()["id"]

        # Get
        resp = await client.get(f"/v1/sessions/{session_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session_id
        assert "messages" in data


@pytest.mark.anyio
async def test_get_nonexistent_session(headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/v1/sessions/nonexistent", headers=headers)
        assert resp.status_code == 404


@pytest.mark.anyio
async def test_delete_session(headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post("/v1/sessions", json={"id": "delete-me"}, headers=headers)
        session_id = created.json()["id"]

        resp = await client.delete(f"/v1/sessions/{session_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify deleted
        resp = await client.get(f"/v1/sessions/{session_id}", headers=headers)
        assert resp.status_code == 404


@pytest.mark.anyio
async def test_chat_returns_session_id(headers):
    """Chat completions should return a session_id in the response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        from unittest.mock import AsyncMock, patch
        mock_response = {
            "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
        }
        with patch("gateway.routes.call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (200, mock_response, "")
            resp = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "Hello"}]},
                headers=headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data

        # Verify session exists
        sess_resp = await client.get(f"/v1/sessions/{data['session_id']}", headers=headers)
        assert sess_resp.status_code == 200
        msgs = sess_resp.json()["messages"]
        assert len(msgs) == 2  # user + assistant
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
