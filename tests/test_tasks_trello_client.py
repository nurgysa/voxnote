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


# ── list_containers ────────────────────────────────────────────────────


def test_list_containers_flattens_boards_and_lists():
    boards = [
        {"id": "b-1", "name": "Маркетинг", "lists": [
            {"id": "l-1", "name": "To Do"},
            {"id": "l-2", "name": "Doing"},
        ]},
        {"id": "b-2", "name": "Продажи", "lists": [
            {"id": "l-3", "name": "Inbox"},
        ]},
    ]
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=boards)) as mock_req:
        rows = c.list_containers()
    assert rows == [
        {"board_name": "Маркетинг", "list_id": "l-1", "list_name": "To Do"},
        {"board_name": "Маркетинг", "list_id": "l-2", "list_name": "Doing"},
        {"board_name": "Продажи", "list_id": "l-3", "list_name": "Inbox"},
    ]
    # Nested-lists query params present.
    sent = mock_req.call_args.kwargs["params"]
    assert sent["lists"] == "open"
    assert sent["filter"] == "open"


def test_list_containers_skips_boards_without_lists():
    boards = [
        {"id": "b-1", "name": "Empty", "lists": []},
        {"id": "b-2", "name": "Has", "lists": [{"id": "l-9", "name": "Backlog"}]},
    ]
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=boards)):
        rows = c.list_containers()
    assert rows == [{"board_name": "Has", "list_id": "l-9", "list_name": "Backlog"}]


def test_list_containers_rejects_non_list_response():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body={"oops": 1})):
        with pytest.raises(TrelloError, match="неожиданный формат"):
            c.list_containers()
