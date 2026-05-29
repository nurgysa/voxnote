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


# ── board_context ───────────────────────────────────────────────────────


def test_board_context_maps_members_and_labels():
    board = {
        "id": "b-1",
        "members": [
            {"id": "m-1", "fullName": "Айдар Нургиса", "username": "aidar"},
            {"id": "m-2", "fullName": "", "username": "guest"},
        ],
        "labels": [
            {"id": "lbl-1", "name": "Баг", "color": "red"},
            {"id": "lbl-2", "name": "", "color": "green"},
        ],
    }
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=board)) as mock_req:
        ctx = c.board_context("l-1")
    # Member with empty fullName falls back to username.
    assert ctx["members"] == [
        {"id": "m-1", "name": "Айдар Нургиса", "displayName": "Айдар Нургиса"},
        {"id": "m-2", "name": "guest", "displayName": "guest"},
    ]
    # Empty-name label is dropped (LLM can't address it).
    assert ctx["labels"] == [{"id": "lbl-1", "name": "Баг"}]
    # Resolves via /lists/{id}/board with nested members + labels.
    assert mock_req.call_args.args[1].endswith("/lists/l-1/board")
    sent = mock_req.call_args.kwargs["params"]
    assert sent["members"] == "all"
    assert sent["labels"] == "all"


def test_board_context_rejects_empty_list_id():
    c = TrelloClient("k", "t")
    with pytest.raises(TrelloError, match="list_id обязателен"):
        c.board_context("")


def test_board_context_tolerates_missing_members_labels():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body={"id": "b-1"})):
        ctx = c.board_context("l-1")
    assert ctx == {"members": [], "labels": []}


# ── create_card ─────────────────────────────────────────────────────────


def test_create_card_minimal_payload():
    response = {"id": "c-1", "idShort": 7, "url": "https://trello.com/c/abc/7-x"}
    c = TrelloClient("k", "t")
    mock_resp = _resp(200, json_body=response)
    with patch.object(c._session, "request", return_value=mock_resp) as mock_req:
        result = c.create_card(id_list="l-1", name="Починить баг")
    assert result["idShort"] == 7
    sent = mock_req.call_args.kwargs["params"]
    assert sent["idList"] == "l-1"
    assert sent["name"] == "Починить баг"
    # Optional fields absent when not provided.
    assert "idMembers" not in sent
    assert "idLabels" not in sent
    assert "due" not in sent
    assert "desc" not in sent


def test_create_card_full_payload_joins_arrays():
    response = {"id": "c-2", "idShort": 8, "url": "https://trello.com/c/def/8-y"}
    c = TrelloClient("k", "t")
    mock_resp = _resp(200, json_body=response)
    with patch.object(c._session, "request", return_value=mock_resp) as mock_req:
        c.create_card(
            id_list="l-1", name="T", desc="body",
            id_members=["m-1", "m-2"], id_labels=["lbl-1"], due="2026-06-01",
        )
    sent = mock_req.call_args.kwargs["params"]
    assert sent["desc"] == "body"
    assert sent["idMembers"] == "m-1,m-2"
    assert sent["idLabels"] == "lbl-1"
    assert sent["due"] == "2026-06-01"
    # POST verb.
    assert mock_req.call_args.args[0] == "POST"
    assert mock_req.call_args.args[1].endswith("/cards")


def test_create_card_rejects_empty_name():
    c = TrelloClient("k", "t")
    with pytest.raises(TrelloError, match="name обязателен"):
        c.create_card(id_list="l-1", name="")


def test_create_card_rejects_empty_list():
    c = TrelloClient("k", "t")
    with pytest.raises(TrelloError, match="id_list обязателен"):
        c.create_card(id_list="", name="T")
