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
