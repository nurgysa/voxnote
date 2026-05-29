"""Tests for tasks.trello_client. HTTP mocked at the requests.Session level."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from tasks.trello_client import TrelloClient, TrelloError


def _resp(status: int, *, json_body=None, text="", headers=None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.text = text
    if json_body is not None:
        r.content = b"x"
        r.json.return_value = json_body
    elif text:
        r.content = text.encode("utf-8")
        r.json.side_effect = ValueError("no JSON")
    else:
        r.content = b""
        r.json.side_effect = ValueError("no JSON")
    return r


def test_client_rejects_empty_key():
    with pytest.raises(TrelloError, match="ключ"):
        TrelloClient("", "tok")
    with pytest.raises(TrelloError, match="ключ"):
        TrelloClient("   ", "tok")


def test_client_rejects_empty_token():
    with pytest.raises(TrelloError, match="[Тт]окен"):
        TrelloClient("key", "")
    with pytest.raises(TrelloError, match="[Тт]окен"):
        TrelloClient("key", "   ")


def test_request_injects_key_and_token_as_query_params():
    c = TrelloClient("my-key", "my-token")
    with patch.object(
        c._session, "request",
        return_value=_resp(200, json_body={"id": "u-1", "fullName": "Айдар"}),
    ) as mock_req:
        c.validate_key()
    sent = mock_req.call_args.kwargs["params"]
    assert sent["key"] == "my-key"
    assert sent["token"] == "my-token"


def test_network_error_wrapped_in_trello_error():
    c = TrelloClient("k", "t")
    with patch.object(
        c._session, "request",
        side_effect=requests.exceptions.ConnectionError("DNS fail"),
    ):
        with pytest.raises(TrelloError, match="Нет соединения с Trello"):
            c.validate_key()


def test_timeout_wrapped_in_trello_error():
    c = TrelloClient("k", "t")
    with patch.object(
        c._session, "request",
        side_effect=requests.exceptions.Timeout("read timeout"),
    ):
        with pytest.raises(TrelloError, match="Таймаут Trello"):
            c.validate_key()


def test_request_raises_on_401():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(401, text="invalid token")):
        with pytest.raises(TrelloError, match="Trello вернул 401"):
            c.validate_key()


def test_request_raises_on_429():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(429, text="rate limited")):
        with pytest.raises(TrelloError, match="Trello вернул 429"):
            c.validate_key()


def test_request_raises_on_500():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(500, text="server error")):
        with pytest.raises(TrelloError, match="Trello вернул 500"):
            c.validate_key()
