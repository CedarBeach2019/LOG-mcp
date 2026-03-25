"""Tests for vault.llm_scorer — Ollama LLM PII detection."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from vault.llm_scorer import score_pii, score_pii_sync, DEFAULT_MODEL, OLLAMA_URL


def _make_response(content: str, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"message": {"content": content}}
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.asyncio
async def test_returns_empty_when_ollama_down():
    """Ollama unreachable → empty dict, no exception."""
    with patch("vault.llm_scorer.httpx.AsyncClient") as MockClient:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        MockClient.return_value = mock_ctx

        result = await score_pii("hello world")
    assert result == {}


@pytest.mark.asyncio
async def test_parses_valid_json():
    """Well-formed Ollama response → correct entities."""
    payload = json.dumps({"entities": [{"type": "relationship", "text": "my wife"}]})
    resp = _make_response(payload)

    with patch("vault.llm_scorer.httpx.AsyncClient") as MockClient:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=resp)
        MockClient.return_value = mock_ctx

        result = await score_pii("I went with my wife to the store")
    assert len(result["entities"]) == 1
    assert result["entities"][0]["type"] == "relationship"
    assert result["entities"][0]["text"] == "my wife"


@pytest.mark.asyncio
async def test_handles_malformed_response():
    """Garbage or non-JSON from Ollama → empty dict."""
    resp = _make_response("Sure, let me think about that...")

    with patch("vault.llm_scorer.httpx.AsyncClient") as MockClient:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=resp)
        MockClient.return_value = mock_ctx

        result = await score_pii("some text")
    assert result == {}


@pytest.mark.asyncio
async def test_handles_think_wrapper():
    """qwen models may wrap JSON in <think...</think tags."""
    wrapped = '<think\nreasoning here\n</think\n' + json.dumps(
        {"entities": [{"type": "person", "text": "Alice"}]}
    )
    resp = _make_response(wrapped)

    with patch("vault.llm_scorer.httpx.AsyncClient") as MockClient:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=resp)
        MockClient.return_value = mock_ctx

        result = await score_pii("Alice came over")
    assert result["entities"][0]["text"] == "Alice"


@pytest.mark.asyncio
async def test_sync_wrapper_via_direct_call():
    """score_pii core function returns empty entities when none found."""
    payload = json.dumps({"entities": []})
    resp = _make_response(payload)

    with patch("vault.llm_scorer.httpx.AsyncClient") as MockClient:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=resp)
        MockClient.return_value = mock_ctx

        result = await score_pii("test")
    assert result == {"entities": []}


# Need httpx import for ConnectError
import httpx
