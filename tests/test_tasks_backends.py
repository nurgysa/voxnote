"""Tests for tasks.backends.{linear,glide} — translation between Phase
6.0 schema (Task / Priority enum) and each backend's wire format.

Sender's filtering / status-transition / cancellation logic is covered
by test_tasks_send.py with a mock backend; here we focus on what each
adapter does with the same input Task.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from tasks.backends.base import Container, CreatedIssue
from tasks.backends.glide import GlideBackend
from tasks.backends.linear import LinearBackend
from tasks.schema import Priority, Task

# ── LinearBackend ────────────────────────────────────────────────────


def test_linear_bootstrap_returns_containers_with_key():
    client = MagicMock()
    client.bootstrap.return_value = {
        "viewer": {"id": "u-1", "name": "Айдар"},
        "teams": [
            {"id": "t-1", "name": "Engineering", "key": "ENG"},
            {"id": "t-2", "name": "Mobile", "key": "MOB"},
        ],
    }
    b = LinearBackend(client)
    containers = b.bootstrap()
    assert containers == [
        Container(id="t-1", name="Engineering", key="ENG"),
        Container(id="t-2", name="Mobile", key="MOB"),
    ]


def test_linear_container_label_includes_key():
    b = LinearBackend(MagicMock())
    assert (
        b.container_label(Container(id="t", name="Engineering", key="ENG"))
        == "Engineering (ENG)"
    )
    # Defensive — if key missing, fall back to name.
    assert b.container_label(Container(id="t", name="Solo")) == "Solo"


def test_linear_context_passes_through_team_context():
    client = MagicMock()
    expected = {"members": [{"id": "u-1"}], "labels": [{"id": "l-1"}]}
    client.team_context.return_value = expected
    b = LinearBackend(client)
    assert b.context("t-1") == expected
    client.team_context.assert_called_once_with("t-1")


def test_linear_create_passes_priority_as_int():
    """Linear's priority is int 0-4; URGENT == 1."""
    client = MagicMock()
    client.create_issue.return_value = {
        "id": "uuid", "identifier": "ENG-101", "url": "https://linear.app/x/ENG-101",
    }
    b = LinearBackend(client)
    task = Task(title="A", priority=Priority.URGENT)
    b.create("team-id", task)
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["priority"] == 1


def test_linear_create_omits_priority_when_none():
    """Linear treats null as 'set null'; we omit so it stays default."""
    client = MagicMock()
    client.create_issue.return_value = {"identifier": "X-1", "url": ""}
    b = LinearBackend(client)
    b.create("t", Task(title="A", priority=Priority.NONE))
    kwargs = client.create_issue.call_args.kwargs
    assert "priority" not in kwargs


def test_linear_create_passes_assignee_label_due_date():
    client = MagicMock()
    client.create_issue.return_value = {"identifier": "X-1", "url": ""}
    b = LinearBackend(client)
    task = Task(
        title="A", assignee_id="u-1",
        label_ids=["l-1", "l-2"], due_date="2026-05-15",
    )
    b.create("t", task)
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["assignee_id"] == "u-1"
    assert kwargs["label_ids"] == ["l-1", "l-2"]
    assert kwargs["due_date"] == "2026-05-15"


def test_linear_create_returns_created_issue():
    client = MagicMock()
    client.create_issue.return_value = {
        "id": "uuid", "identifier": "ENG-101",
        "url": "https://linear.app/x/ENG-101",
    }
    b = LinearBackend(client)
    issue = b.create("t", Task(title="A"))
    assert isinstance(issue, CreatedIssue)
    assert issue.identifier == "ENG-101"
    assert issue.url == "https://linear.app/x/ENG-101"


# ── GlideBackend ─────────────────────────────────────────────────────


def test_glide_bootstrap_returns_containers_without_key():
    client = MagicMock()
    client.list_boards.return_value = [
        {"id": "b-1", "name": "Inbox"},
        {"id": "b-2", "name": "Sales"},
    ]
    b = GlideBackend(client)
    containers = b.bootstrap()
    assert containers == [
        Container(id="b-1", name="Inbox", key=None),
        Container(id="b-2", name="Sales", key=None),
    ]


def test_glide_container_label_just_name():
    b = GlideBackend(MagicMock())
    assert b.container_label(Container(id="b", name="Inbox")) == "Inbox"


def test_glide_context_returns_empty_lists():
    """Phase 6.4.1: no LLM grounding for Glide — heterogeneous schemas."""
    b = GlideBackend(MagicMock())
    assert b.context("b-1") == {"members": [], "labels": []}


def test_glide_create_translates_priority_enum_to_string():
    """URGENT → critical (Glide has no urgent / 4-level scale starting at critical)."""
    client = MagicMock()
    client.create_task.return_value = {
        "id": "467e1449-1737-4815-a8cc-12cff01b3a46",
        "board_id": "b-1",
        "fields_warnings": [],
    }
    b = GlideBackend(client)
    cases = [
        (Priority.URGENT, "critical"),
        (Priority.HIGH,   "high"),
        (Priority.MEDIUM, "medium"),
        (Priority.LOW,    "low"),
    ]
    for prio, expected in cases:
        b.create("b-1", Task(title="A", priority=prio))
        assert client.create_task.call_args.kwargs["priority"] == expected


def test_glide_create_omits_priority_for_none():
    """Priority.NONE → priority kwarg is None, GlideClient omits it from payload."""
    client = MagicMock()
    client.create_task.return_value = {"id": "uuid-x", "board_id": "b-1", "fields_warnings": []}
    b = GlideBackend(client)
    b.create("b-1", Task(title="A", priority=Priority.NONE))
    assert client.create_task.call_args.kwargs["priority"] is None


def test_glide_create_passes_idempotency_key_using_local_id():
    """Stable per-task key — retries don't duplicate; cached failures need
    new-key strategy in 6.4.2 (TODO)."""
    client = MagicMock()
    client.create_task.return_value = {"id": "uuid", "board_id": "b", "fields_warnings": []}
    b = GlideBackend(client)
    task = Task(title="A", local_id="task-uuid-abc")
    b.create("b", task)
    assert client.create_task.call_args.kwargs["idempotency_key"] == "task-task-uuid-abc"


def test_glide_create_passes_board_id_as_container():
    client = MagicMock()
    client.create_task.return_value = {"id": "uuid", "board_id": "b-1", "fields_warnings": []}
    b = GlideBackend(client)
    b.create("b-1", Task(title="A"))
    assert client.create_task.call_args.kwargs["board_id"] == "b-1"


def test_glide_create_returns_short_uuid_prefix_and_url():
    client = MagicMock()
    client.create_task.return_value = {
        "id": "467e1449-1737-4815-a8cc-12cff01b3a46",
        "board_id": "b-1",
        "fields_warnings": [],
    }
    b = GlideBackend(client)
    issue = b.create("b-1", Task(title="A"))
    assert issue.identifier == "467e14"   # first 6 chars of UUID
    assert "467e1449-1737-4815-a8cc-12cff01b3a46" in issue.url
    assert issue.url.startswith("https://os.tensor-ai.tech/")


def test_glide_create_ignores_assignee_and_labels():
    """Phase 6.4.1: Linear-shaped UUIDs in Task.assignee_id / label_ids
    aren't valid for Glide. Drop them silently — manual editor in 6.4.2."""
    client = MagicMock()
    client.create_task.return_value = {"id": "uuid", "board_id": "b", "fields_warnings": []}
    b = GlideBackend(client)
    task = Task(
        title="A",
        assignee_id="linear-uuid-aidar",
        label_ids=["linear-uuid-bug"],
        due_date="2026-05-15",
    )
    b.create("b", task)
    kwargs = client.create_task.call_args.kwargs
    # No assignee_id or label_ids in the Glide payload.
    assert "assignee_id" not in kwargs
    assert "label_ids" not in kwargs
    # due_date currently not mapped either — would need a column-name
    # mapping per board in 6.4.2.
    assert "fields" not in kwargs or "due_date" not in (kwargs.get("fields") or {})


# ── TrelloBackend ────────────────────────────────────────────────────


def _trello_card(id_short=7, url="https://trello.com/c/abc/7-x"):
    return {"id": "c-1", "idShort": id_short, "shortLink": "abc", "url": url}


def test_trello_bootstrap_folds_board_and_list_into_name():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.list_containers.return_value = [
        {"board_name": "Маркетинг", "list_id": "l-1", "list_name": "To Do"},
        {"board_name": "Продажи", "list_id": "l-3", "list_name": "Inbox"},
    ]
    b = TrelloBackend(client)
    assert b.bootstrap() == [
        Container(id="l-1", name="Маркетинг / To Do", key=None),
        Container(id="l-3", name="Продажи / Inbox", key=None),
    ]


def test_trello_container_label_is_name():
    from tasks.backends.trello import TrelloBackend
    b = TrelloBackend(MagicMock())
    assert b.container_label(Container(id="l", name="Маркетинг / To Do")) == "Маркетинг / To Do"


def test_trello_context_passes_through_board_context():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    expected = {"members": [{"id": "m-1"}], "labels": [{"id": "lbl-1"}]}
    client.board_context.return_value = expected
    b = TrelloBackend(client)
    assert b.context("l-1") == expected
    client.board_context.assert_called_once_with("l-1")


def test_trello_create_prepends_priority_line_to_desc():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A", description="тело", priority=Priority.URGENT))
    sent = client.create_card.call_args.kwargs
    assert sent["desc"] == "**Приоритет:** Срочный\n\nтело"


def test_trello_create_priority_line_without_body():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A", description="", priority=Priority.HIGH))
    assert client.create_card.call_args.kwargs["desc"] == "**Приоритет:** Высокий"


def test_trello_create_no_priority_line_when_none():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A", description="тело", priority=Priority.NONE))
    assert client.create_card.call_args.kwargs["desc"] == "тело"


def test_trello_create_passes_assignee_labels_due():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    task = Task(
        title="A", assignee_id="m-1",
        label_ids=["lbl-1", "lbl-2"], due_date="2026-06-01",
    )
    b.create("l-1", task)
    sent = client.create_card.call_args.kwargs
    assert sent["id_members"] == ["m-1"]
    assert sent["id_labels"] == ["lbl-1", "lbl-2"]
    assert sent["due"] == "2026-06-01"
    assert sent["id_list"] == "l-1"


def test_trello_create_omits_empty_assignee_labels_due():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A"))
    sent = client.create_card.call_args.kwargs
    assert sent["id_members"] is None
    assert sent["id_labels"] is None
    assert sent["due"] is None


def test_trello_create_returns_short_id_and_url():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card(id_short=42, url="https://trello.com/c/zz/42-fix")
    b = TrelloBackend(client)
    issue = b.create("l-1", Task(title="A"))
    assert isinstance(issue, CreatedIssue)
    assert issue.identifier == "#42"
    assert issue.url == "https://trello.com/c/zz/42-fix"
