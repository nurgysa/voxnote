"""Tests for tasks.openrouter_client. HTTP is mocked via unittest.mock."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests   # used by the ConnectionError test below

from tasks.openrouter_client import OpenRouterClient, OpenRouterError


# ── construction ──────────────────────────────────────────────────────


def test_client_rejects_empty_key():
    with pytest.raises(OpenRouterError, match="ключ не задан"):
        OpenRouterClient("")
    with pytest.raises(OpenRouterError):
        OpenRouterClient("   ")


def test_client_strips_whitespace_from_key():
    c = OpenRouterClient("  sk-or-test  ")
    assert c._api_key == "sk-or-test"
    assert c._session.headers["Authorization"] == "Bearer sk-or-test"


# ── validate_key ──────────────────────────────────────────────────────


def test_validate_key_returns_label_and_balance_on_200():
    """OpenRouter /auth/key returns {label, usage, limit} on success."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "label": "personal",
            "usage": 5.40,
            "limit": 18.00,
            "is_free_tier": False,
        }
    }
    c = OpenRouterClient("sk-or-test")
    with patch.object(c._session, "get", return_value=fake) as mock_get:
        result = c.validate_key()

    mock_get.assert_called_once()
    assert "/auth/key" in mock_get.call_args[0][0]
    assert result["label"] == "personal"
    assert result["balance_remaining"] == pytest.approx(12.60)  # 18 - 5.40


def test_validate_key_returns_unlimited_when_no_limit():
    """Free tier or unlimited keys have limit=null."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"label": "free", "usage": 0.10, "limit": None}}
    c = OpenRouterClient("sk-or-test")
    with patch.object(c._session, "get", return_value=fake):
        result = c.validate_key()
    assert result["balance_remaining"] is None


def test_validate_key_raises_on_401():
    fake = MagicMock()
    fake.status_code = 401
    fake.text = '{"error": "Invalid key"}'
    c = OpenRouterClient("sk-or-bad")
    with patch.object(c._session, "get", return_value=fake):
        with pytest.raises(OpenRouterError, match="401"):
            c.validate_key()


def test_validate_key_raises_on_network_failure():
    """ConnectionError from requests bubbles up as OpenRouterError."""
    c = OpenRouterClient("sk-or-test")
    with patch.object(
        c._session, "get",
        side_effect=requests.exceptions.ConnectionError("DNS fail"),
    ):
        with pytest.raises(OpenRouterError, match="соединени"):
            c.validate_key()


def test_validate_key_raises_on_timeout():
    """Carry-over from Task 6 review: cover the Timeout branch."""
    c = OpenRouterClient("sk-or-test")
    with patch.object(
        c._session, "get",
        side_effect=requests.exceptions.Timeout("timed out"),
    ):
        with pytest.raises(OpenRouterError, match="Таймаут"):
            c.validate_key()


def test_validate_key_raises_on_other_request_exception():
    """RequestException subclasses (TooManyRedirects, ChunkedEncodingError, etc.)
    must surface as OpenRouterError, not propagate raw."""
    c = OpenRouterClient("sk-or-test")
    with patch.object(
        c._session, "get",
        side_effect=requests.exceptions.TooManyRedirects("loop"),
    ):
        with pytest.raises(OpenRouterError):
            c.validate_key()


# ── complete ──────────────────────────────────────────────────────────


def test_complete_sends_correct_body_with_json_mode():
    """response_format=json_object is set by default."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "id": "gen-1",
        "model": "anthropic/claude-sonnet-4.5",
        "choices": [{"message": {"content": '{"tasks": []}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    c = OpenRouterClient("sk-or-test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        result = c.complete(
            model="anthropic/claude-sonnet-4.5",
            messages=[{"role": "user", "content": "hi"}],
            json_mode=True,
        )
    args, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["model"] == "anthropic/claude-sonnet-4.5"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert result["content"] == '{"tasks": []}'
    assert result["usage"]["prompt_tokens"] == 100


def test_complete_omits_response_format_when_json_mode_false():
    """For models that don't support JSON mode, allow caller to skip."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "choices": [{"message": {"content": "free text"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    c = OpenRouterClient("sk-or-test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        c.complete(
            model="some/no-json-model",
            messages=[{"role": "user", "content": "hi"}],
            json_mode=False,
        )
    body = mock_post.call_args.kwargs["json"]
    assert "response_format" not in body


def test_complete_raises_on_400():
    fake = MagicMock()
    fake.status_code = 400
    fake.text = '{"error":{"message":"json mode unsupported"}}'
    c = OpenRouterClient("sk-or-test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(OpenRouterError, match="400"):
            c.complete(
                model="x/y",
                messages=[{"role": "user", "content": "hi"}],
                json_mode=True,
            )


def test_complete_raises_on_429_with_retry_after():
    """OpenRouterError message should expose Retry-After for caller-side retry."""
    fake = MagicMock()
    fake.status_code = 429
    fake.headers = {"Retry-After": "12"}
    fake.text = '{"error":"rate limited"}'
    c = OpenRouterClient("sk-or-test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(OpenRouterError, match="429.*12"):
            c.complete(
                model="x/y",
                messages=[{"role": "user", "content": "hi"}],
            )
