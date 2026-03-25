"""Tests for error boundary — retry logic and fallback chain."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from unittest.mock import AsyncMock, patch
from gateway.error_boundary import (
    resilient_call,
    _is_retriable,
    _friendly_error_message,
    MAX_RETRIES,
)


class TestIsRetriable:
    def test_timeout_is_retriable(self):
        assert _is_retriable(0, "upstream timeout") is True

    def test_5xx_is_retriable(self):
        assert _is_retriable(500, "server error") is True
        assert _is_retriable(502, "bad gateway") is True
        assert _is_retriable(503, "unavailable") is True

    def test_429_is_retriable(self):
        assert _is_retriable(429, "rate limited") is True

    def test_4xx_not_retriable(self):
        assert _is_retriable(400, "bad request") is False
        assert _is_retriable(401, "unauthorized") is False
        assert _is_retriable(404, "not found") is False

    def test_200_not_retriable(self):
        assert _is_retriable(200, "") is False


class TestFriendlyErrorMessage:
    def test_timeout_message(self):
        msg = _friendly_error_message("upstream timeout", "timeout", "model-a", "model-b")
        assert "timed out" in msg
        assert "high load" in msg

    def test_rate_limit_message(self):
        msg = _friendly_error_message("429", "", "model-a", "model-b")
        assert "Rate limited" in msg

    def test_connection_message(self):
        msg = _friendly_error_message("connection failed", "connection error", "model-a", "model-b")
        assert "internet connection" in msg

    def test_auth_message(self):
        msg = _friendly_error_message("401 unauthorized", "", "model-a", "model-b")
        assert "authentication" in msg

    def test_generic_message(self):
        msg = _friendly_error_message("something weird", "also weird", "model-a", "model-b")
        assert "unavailable" in msg


@pytest.mark.anyio
class TestResilientCall:
    async def test_success_on_first_try(self):
        mock_response = {"choices": [{"message": {"content": "hi"}}]}
        with patch("gateway.error_boundary.call_model", new_callable=AsyncMock) as mock:
            mock.return_value = (200, mock_response, "")
            status, data, err = await resilient_call("http://test", "key", "model", [])
        assert status == 200
        assert mock.call_count == 1

    async def test_retries_on_timeout(self):
        mock_response = {"choices": [{"message": {"content": "hi"}}]}
        with patch("gateway.error_boundary.call_model", new_callable=AsyncMock) as mock:
            mock.side_effect = [
                (0, None, "timeout"),
                (0, None, "timeout"),
                (200, mock_response, ""),
            ]
            status, data, err = await resilient_call("http://test", "key", "model", [])
        assert status == 200
        assert mock.call_count == 3  # 2 failures + 1 success

    async def test_fallback_on_exhausted_retries(self):
        primary_resp = {"choices": [{"message": {"content": "primary"}}]}
        with patch("gateway.error_boundary.call_model", new_callable=AsyncMock) as mock:
            # Primary fails, fallback succeeds
            mock.return_value = (200, primary_resp, "")
            status, data, err = await resilient_call("http://test", "key", "model", [])
        assert status == 200

    async def test_no_retry_on_4xx(self):
        with patch("gateway.error_boundary.call_model", new_callable=AsyncMock) as mock:
            mock.return_value = (400, None, "bad request")
            status, data, err = await resilient_call("http://test", "key", "model", [])
        assert status == 400
        assert mock.call_count == 1  # no retry

    async def test_friendly_error_when_all_fail(self):
        with patch("gateway.error_boundary.call_model", new_callable=AsyncMock) as mock:
            mock.return_value = (0, None, "connection failed")
            status, data, err = await resilient_call("http://test", "key", "model", [])
        assert status == 0
        assert err  # should have a friendly message
        assert "502" not in err
