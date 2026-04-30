"""Tests for tasks.glide_client. HTTP is mocked at the requests.Session level."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from tasks.glide_client import GlideClient, GlideError, _extract_error_message


def _resp(status: int, *, json_body=None, text="", headers=None):
    """Build a MagicMock that quacks like requests.Response.

    `content` mirrors real-world behaviour: non-empty whenever there's ANY
    body (including JSON-empty containers like ``[]`` or ``{}``). Only a
    truly absent body yields b''.
    """
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.text = text
    if json_body is not None:
        r.content = b"x"   # placeholder non-empty; actual bytes irrelevant
        r.json.return_value = json_body
    elif text:
        r.content = text.encode("utf-8")
        r.json.side_effect = ValueError("no JSON")
    else:
        r.content = b""
        r.json.side_effect = ValueError("no JSON")
    return r


# ── Construction ──────────────────────────────────────────────────────


def test_client_rejects_empty_key():
    with pytest.raises(GlideError, match="ключ не задан"):
        GlideClient("")
    with pytest.raises(GlideError, match="ключ не задан"):
        GlideClient("   ")


def test_authorization_header_has_bearer_prefix():
    """Glide quirk vs Linear — Bearer-prefixed token."""
    c = GlideClient("glide_pk_test_abc")
    assert c._session.headers["Authorization"] == "Bearer glide_pk_test_abc"


# ── validate_key ──────────────────────────────────────────────────────


def test_validate_key_returns_count_and_sample():
    boards = [
        {"id": "b-1", "name": "Inbox"},
        {"id": "b-2", "name": "Sales"},
        {"id": "b-3", "name": "Support"},
        {"id": "b-4", "name": "Eng"},
    ]
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(200, json_body=boards),
    ):
        result = c.validate_key()
    assert result["board_count"] == 4
    assert result["sample_names"] == ["Inbox", "Sales", "Support"]   # first 3


def test_validate_key_handles_empty_workspace():
    c = GlideClient("glide_pk_test_abc")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=[])):
        result = c.validate_key()
    assert result == {"board_count": 0, "sample_names": []}


def test_validate_key_raises_on_401():
    body = {"error": {"code": "invalid_token", "message": "Token revoked"}}
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(401, json_body=body),
    ):
        with pytest.raises(GlideError, match="неверный или просроченный"):
            c.validate_key()


# ── list_boards ────────────────────────────────────────────────────────


def test_list_boards_returns_array():
    boards = [{"id": "b-1", "name": "Inbox"}]
    c = GlideClient("glide_pk_test_abc")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=boards)):
        assert c.list_boards() == boards


def test_list_boards_accepts_wrapped_response():
    """Defensive — if Glide ever wraps as {data: [...]}, we still parse."""
    wrapped = {"data": [{"id": "b-1", "name": "Inbox"}]}
    c = GlideClient("glide_pk_test_abc")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=wrapped)):
        assert c.list_boards() == [{"id": "b-1", "name": "Inbox"}]


def test_list_boards_rejects_unexpected_shape():
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(200, json_body={"unexpected": "garbage"}),
    ):
        with pytest.raises(GlideError, match="неожиданный формат"):
            c.list_boards()


# ── board_schema ──────────────────────────────────────────────────────


def test_board_schema_passes_id_in_path():
    schema = {
        "id": "b-1", "name": "Inbox",
        "groups": [{"id": "g-1", "title": "New"}],
        "columns": [{"id": "c-1", "title": "Status", "column_type": "status"}],
    }
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(200, json_body=schema),
    ) as mock_req:
        result = c.board_schema("b-1")
    assert result == schema
    # Verify URL was built with the board id.
    args, kwargs = mock_req.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/boards/b-1")


def test_board_schema_rejects_empty_id():
    c = GlideClient("glide_pk_test_abc")
    with pytest.raises(GlideError, match="board_id обязателен"):
        c.board_schema("")


# ── create_task ───────────────────────────────────────────────────────


def test_create_task_minimal_payload():
    """Only title — no priority/board_id/etc — produces minimal JSON body."""
    response_body = {
        "id": "t-1", "board_id": "b-default", "group_id": "g-default",
        "title": "Hello", "priority": None, "description": None,
        "created_at": "2026-04-30T08:00:00Z",
        "fields_applied": [], "fields_warnings": [],
    }
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(201, json_body=response_body),
    ) as mock_req:
        result = c.create_task(title="Hello")
    assert result["id"] == "t-1"
    # Verify the JSON payload was minimal.
    sent_json = mock_req.call_args.kwargs["json"]
    assert sent_json == {"title": "Hello"}


def test_create_task_full_payload():
    response_body = {
        "id": "t-2", "board_id": "b-1", "group_id": "g-1",
        "title": "T", "priority": "high", "description": "d",
        "created_at": "2026-04-30T08:00:00Z",
        "fields_applied": ["Status"], "fields_warnings": [],
    }
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(201, json_body=response_body),
    ) as mock_req:
        c.create_task(
            title="T", description="d", priority="high",
            board_id="b-1", group_id="g-1",
            fields={"Status": "В работе"},
            idempotency_key="k-1",
        )
    sent_json = mock_req.call_args.kwargs["json"]
    assert sent_json == {
        "title": "T", "description": "d", "priority": "high",
        "board_id": "b-1", "group_id": "g-1",
        "fields": {"Status": "В работе"},
    }
    # Idempotency-Key flows through to the headers param.
    sent_headers = mock_req.call_args.kwargs["headers"]
    assert sent_headers["Idempotency-Key"] == "k-1"


def test_create_task_omits_none_priority():
    """priority=None must NOT appear in payload (Glide leaves default)."""
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(201, json_body={"id": "t", "fields_warnings": []}),
    ) as mock_req:
        c.create_task(title="T", priority=None)
    sent = mock_req.call_args.kwargs["json"]
    assert "priority" not in sent


def test_create_task_rejects_invalid_priority():
    c = GlideClient("glide_pk_test_abc")
    with pytest.raises(GlideError, match="priority должен быть"):
        c.create_task(title="T", priority="urgent")   # Linear-ese, not Glide


def test_create_task_rejects_empty_title():
    c = GlideClient("glide_pk_test_abc")
    with pytest.raises(GlideError, match="title обязателен"):
        c.create_task(title="")
    with pytest.raises(GlideError, match="title обязателен"):
        c.create_task(title="   ")


def test_create_task_logs_field_warnings(caplog):
    response_body = {
        "id": "t-3", "title": "T", "fields_applied": [],
        "fields_warnings": [
            {"field": "Status", "error": "column_not_found"},
        ],
    }
    c = GlideClient("glide_pk_test_abc")
    with caplog.at_level("WARNING", logger="tasks.glide_client"):
        with patch.object(
            c._session, "request",
            return_value=_resp(201, json_body=response_body),
        ):
            c.create_task(title="T", fields={"Status": "X"})
    assert any("field-mapping warnings" in r.message for r in caplog.records)


# ── Error handling ─────────────────────────────────────────────────────


def test_request_raises_on_429_with_reset_header():
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(
            429,
            json_body={"error": {"code": "rate_limited"}},
            headers={"X-RateLimit-Reset": "1777493520"},
        ),
    ):
        with pytest.raises(GlideError, match="rate-limit.*1777493520"):
            c.list_boards()


def test_request_raises_on_403():
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(403, json_body={"error": {"code": "ip_not_allowed"}}),
    ):
        with pytest.raises(GlideError, match="403.*ip_not_allowed"):
            c.list_boards()


def test_request_raises_on_500_propagates_error_code():
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        return_value=_resp(500, json_body={"error": {"code": "internal_error", "message": "DB down"}}),
    ):
        with pytest.raises(GlideError, match="500.*internal_error.*DB down"):
            c.list_boards()


def test_network_error_wrapped_in_glide_error():
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        side_effect=requests.exceptions.ConnectionError("DNS fail"),
    ):
        with pytest.raises(GlideError, match="Нет соединения"):
            c.list_boards()


def test_timeout_wrapped_in_glide_error():
    c = GlideClient("glide_pk_test_abc")
    with patch.object(
        c._session, "request",
        side_effect=requests.exceptions.Timeout("read timeout"),
    ):
        with pytest.raises(GlideError, match="Таймаут"):
            c.list_boards()


# ── _extract_error_message ────────────────────────────────────────────


def test_extract_error_prefers_envelope_code():
    r = _resp(400, json_body={"error": {"code": "board_required", "message": "no default"}})
    assert _extract_error_message(r) == "board_required: no default"


def test_extract_error_falls_back_to_code_only_without_message():
    r = _resp(400, json_body={"error": {"code": "rate_limited"}})
    assert _extract_error_message(r) == "rate_limited"


def test_extract_error_handles_non_json():
    r = MagicMock(spec=requests.Response)
    r.status_code = 502
    r.json.side_effect = ValueError("not json")
    r.text = "<html>Bad gateway</html>"
    assert "Bad gateway" in _extract_error_message(r)


def test_extract_error_handles_empty_body():
    r = MagicMock(spec=requests.Response)
    r.status_code = 502
    r.json.side_effect = ValueError("not json")
    r.text = ""
    msg = _extract_error_message(r)
    assert "502" in msg
