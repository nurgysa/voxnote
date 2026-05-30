"""Tests for tasks.linear_client. HTTP is mocked via unittest.mock."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from tasks.linear_client import LinearClient, LinearError

# ── construction ──────────────────────────────────────────────────────


def test_client_rejects_empty_key():
    with pytest.raises(LinearError, match="ключ не задан"):
        LinearClient("")
    with pytest.raises(LinearError, match="ключ не задан"):
        LinearClient("   ")


def test_authorization_header_has_no_bearer_prefix():
    """Linear quirk — raw key, no 'Bearer'."""
    c = LinearClient("lin_api_test")
    assert c._session.headers["Authorization"] == "lin_api_test"


# ── validate_key ──────────────────────────────────────────────────────


def test_validate_key_returns_viewer_on_200():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {"viewer": {"id": "u-1", "name": "Айдар", "email": "a@x.com"}}
    }
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        v = c.validate_key()
    mock_post.assert_called_once()
    assert v == {"id": "u-1", "name": "Айдар", "email": "a@x.com"}


def test_validate_key_raises_on_graphql_error():
    """Linear returns 200 with 'errors' array on auth failure."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "errors": [{"message": "Authentication failed"}],
    }
    c = LinearClient("lin_api_bad")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(LinearError, match="Authentication"):
            c.validate_key()


def test_validate_key_raises_on_http_500():
    fake = MagicMock()
    fake.status_code = 500
    fake.text = "Internal server error"
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(LinearError, match="500"):
            c.validate_key()


# ── _graphql JSONDecodeError fix (review carry-over from Task 8) ──


def test_graphql_raises_LinearError_on_malformed_json_body():
    """200 with non-JSON body must surface as LinearError, not raw ValueError."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.side_effect = ValueError("Expecting value: line 1 column 1")
    fake.text = "<html>Service unavailable</html>"
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(LinearError, match="не-JSON"):
            c.validate_key()


# ── bootstrap ─────────────────────────────────────────────────────────


def test_bootstrap_returns_viewer_and_teams_in_one_query():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "viewer": {"id": "u-1", "name": "Айдар", "email": "a@x.com"},
            "teams": {
                "nodes": [
                    {"id": "t-1", "name": "Engineering", "key": "ENG"},
                    {"id": "t-2", "name": "Design", "key": "DES"},
                ]
            },
        }
    }
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        result = c.bootstrap()
    # One round-trip — verifies our optimization
    assert mock_post.call_count == 1
    assert result["viewer"]["name"] == "Айдар"
    assert len(result["teams"]) == 2
    assert result["teams"][0] == {"id": "t-1", "name": "Engineering", "key": "ENG"}


def test_bootstrap_returns_empty_team_list_when_user_has_no_teams():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "viewer": {"id": "u-1", "name": "Solo", "email": "s@x.com"},
            "teams": {"nodes": []},
        }
    }
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake):
        result = c.bootstrap()
    assert result["teams"] == []


# ── team_context ──────────────────────────────────────────────────────


def test_team_context_returns_members_and_labels():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "team": {
                "members": {
                    "nodes": [
                        {"id": "u-1", "name": "Айдар", "displayName": "айдар", "email": "a@x.com"},
                        {"id": "u-2", "name": "Нурғыса", "displayName": "ng", "email": "n@x.com"},
                    ]
                },
                "labels": {
                    "nodes": [
                        {"id": "l-1", "name": "bug", "color": "#ff0000"},
                        {"id": "l-2", "name": "mobile", "color": "#0000ff"},
                    ]
                },
            }
        }
    }
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        ctx = c.team_context("t-1")
    body = mock_post.call_args.kwargs["json"]
    assert body["variables"] == {"teamId": "t-1"}
    assert len(ctx["members"]) == 2
    assert ctx["members"][0]["name"] == "Айдар"
    assert len(ctx["labels"]) == 2
    assert ctx["labels"][0]["name"] == "bug"


def test_team_context_raises_when_team_id_unknown():
    """Linear returns data.team=null for invalid team IDs."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"team": None}}
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(LinearError, match="команда"):
            c.team_context("t-bogus")


# ── create_issue ──────────────────────────────────────────────────────


def test_create_issue_sends_full_input_and_returns_issue():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {
                    "id": "issue-uuid",
                    "identifier": "ENG-1234",
                    "url": "https://linear.app/x/issue/ENG-1234",
                },
            }
        }
    }
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        result = c.create_issue(
            team_id="t-1",
            title="Починить login",
            description="Длинное описание",
            priority=2,
            assignee_id="u-1",
            label_ids=["l-1", "l-2"],
            due_date="2026-05-15",
        )
    body = mock_post.call_args.kwargs["json"]
    vars_ = body["variables"]
    assert vars_["teamId"] == "t-1"
    assert vars_["title"] == "Починить login"
    assert vars_["description"] == "Длинное описание"
    assert vars_["priority"] == 2
    assert vars_["assigneeId"] == "u-1"
    assert vars_["labelIds"] == ["l-1", "l-2"]
    assert vars_["dueDate"] == "2026-05-15"
    assert result == {
        "id": "issue-uuid",
        "identifier": "ENG-1234",
        "url": "https://linear.app/x/issue/ENG-1234",
    }


def test_create_issue_omits_optional_fields_when_none():
    """Title is the only required field. None values mustn't be sent — Linear
    treats null differently from absent."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {"id": "x", "identifier": "DES-1", "url": "https://x"},
            }
        }
    }
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        c.create_issue(team_id="t-1", title="Title only")
    vars_ = mock_post.call_args.kwargs["json"]["variables"]
    assert vars_ == {"teamId": "t-1", "title": "Title only"}
    assert "priority" not in vars_
    assert "assigneeId" not in vars_
    assert "labelIds" not in vars_
    assert "dueDate" not in vars_


def test_create_issue_raises_when_success_false():
    """Linear returns success=False (with errors) when input is rejected."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "issueCreate": {
                "success": False,
                "issue": None,
            }
        }
    }
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(LinearError, match="отказ"):
            c.create_issue(team_id="t-1", title="X")


# ── add_comment ────────────────────────────────────────────────────────


def test_add_comment_success():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"commentCreate": {"success": True}}}
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        c.add_comment("issue-uuid-1", "снова обсуждалось")
    mock_post.assert_called_once()
    sent = mock_post.call_args.kwargs["json"]["variables"]
    assert sent == {"issueId": "issue-uuid-1", "body": "снова обсуждалось"}


def test_add_comment_raises_when_success_false():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"commentCreate": {"success": False}}}
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(LinearError, match="комментар"):
            c.add_comment("issue-uuid-1", "x")
