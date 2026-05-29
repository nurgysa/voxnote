"""Tests for tasks.sender — pure orchestrator with mocked backend.

After Phase 6.4.1, sender is backend-agnostic: it calls `backend.create()`
and stores the returned `CreatedIssue` on the task. Backend-specific
kwargs construction lives in `tasks/backends/{linear,glide}.py` and is
covered by `test_tasks_backends.py`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from tasks.backends.base import CreatedIssue
from tasks.linear_client import LinearError
from tasks.schema import Priority, Task, TaskStatus
from tasks.sender import send_tasks_iter


def _pending_task(title="T", **kw) -> Task:
    kw.setdefault("selected", True)
    kw.setdefault("status", TaskStatus.PENDING)
    return Task(title=title, **kw)


def _make_backend(issues_iter=None, raise_on=None):
    """Construct a MagicMock backend that satisfies the TaskBackend Protocol.

    `issues_iter` — list of CreatedIssue to return on successive create() calls.
    `raise_on` — dict {call_index: exception} to raise instead of returning.
    """
    backend = MagicMock()
    issues_iter = list(issues_iter or [])
    raise_on = dict(raise_on or {})

    call_count = [0]

    def _create(container_id, task):
        idx = call_count[0]
        call_count[0] += 1
        if idx in raise_on:
            raise raise_on[idx]
        if idx < len(issues_iter):
            return issues_iter[idx]
        return CreatedIssue(
            identifier=f"ENG-{100 + idx}",
            url=f"https://linear.app/x/ENG-{100 + idx}",
        )

    backend.create.side_effect = _create
    return backend


# ── Filtering ────────────────────────────────────────────────────────


def test_send_iter_skips_unselected_tasks():
    tasks = [
        _pending_task("A", local_id="a"),
        _pending_task("B", selected=False, local_id="b"),  # unselected
        _pending_task("C", local_id="c"),
    ]
    backend = _make_backend()
    statuses = []
    list(send_tasks_iter(
        tasks, container_id="team-id", backend=backend,
        on_status_change=lambda t, s, **kw: statuses.append((t.local_id, s)),
        cancel_check=lambda: False,
    ))
    seen_ids = {local_id for local_id, _ in statuses}
    assert "a" in seen_ids and "c" in seen_ids and "b" not in seen_ids
    assert backend.create.call_count == 2


def test_send_iter_skips_already_sent_tasks():
    tasks = [
        _pending_task("A", local_id="a"),
        Task(title="B", selected=True, status=TaskStatus.SENT, local_id="b"),
    ]
    backend = _make_backend()
    list(send_tasks_iter(
        tasks, container_id="team-id", backend=backend,
        on_status_change=lambda t, s, **kw: None,
        cancel_check=lambda: False,
    ))
    assert backend.create.call_count == 1   # only A sent


def test_send_iter_skips_failed_tasks_unless_retry_mode():
    """Initial mode: skip FAILED. Retry mode: send only FAILED."""
    tasks = [
        _pending_task("A", local_id="a"),
        Task(title="B", selected=True, status=TaskStatus.FAILED,
             local_id="b", send_error="500"),
    ]
    backend = _make_backend()

    list(send_tasks_iter(
        tasks, container_id="t", backend=backend,
        on_status_change=lambda *a, **kw: None,
        cancel_check=lambda: False, retry_failed=False,
    ))
    assert backend.create.call_count == 1

    backend.create.reset_mock()
    list(send_tasks_iter(
        tasks, container_id="t", backend=backend,
        on_status_change=lambda *a, **kw: None,
        cancel_check=lambda: False, retry_failed=True,
    ))
    assert backend.create.call_count == 1


# ── Status transitions ──────────────────────────────────────────────


def test_send_iter_transitions_through_sending_then_sent_on_success():
    task = _pending_task("A", local_id="a", priority=Priority.HIGH)
    backend = _make_backend([
        CreatedIssue(identifier="ENG-101", url="https://linear.app/x/ENG-101"),
    ])
    seen = []
    list(send_tasks_iter(
        [task], container_id="t", backend=backend,
        on_status_change=lambda t, s, **kw: seen.append(s),
        cancel_check=lambda: False,
    ))
    assert seen == [TaskStatus.SENDING, TaskStatus.SENT]
    assert task.status is TaskStatus.SENT
    assert task.linear_issue_id == "ENG-101"
    assert task.linear_issue_url == "https://linear.app/x/ENG-101"
    assert task.send_error is None


def test_send_iter_transitions_to_failed_on_linear_error():
    task = _pending_task("A", local_id="a")
    backend = _make_backend(raise_on={0: LinearError("Linear вернул 401: unauth")})
    seen = []
    list(send_tasks_iter(
        [task], container_id="t", backend=backend,
        on_status_change=lambda t, s, **kw: seen.append(s),
        cancel_check=lambda: False,
    ))
    assert seen == [TaskStatus.SENDING, TaskStatus.FAILED]
    assert task.status is TaskStatus.FAILED
    assert task.send_error
    assert "401" in task.send_error


def test_send_iter_transitions_to_failed_on_glide_error():
    """Phase 6.4.1: GlideError is also caught and surfaced as FAILED."""
    from tasks.glide_client import GlideError
    task = _pending_task("A", local_id="a")
    backend = _make_backend(raise_on={0: GlideError("Glide 429 rate-limit (Reset=...)")})
    list(send_tasks_iter(
        [task], container_id="t", backend=backend,
        on_status_change=lambda *a, **kw: None,
        cancel_check=lambda: False,
    ))
    assert task.status is TaskStatus.FAILED
    assert "429" in (task.send_error or "")


def test_send_iter_extracts_short_error_code_from_message():
    """The full message is logged; status badge just needs a short code."""
    task = _pending_task("A", local_id="a")

    cases = [
        (LinearError("Linear вернул 429 rate-limit"), "429"),
        (LinearError("Linear вернул 500: ..."), "500"),
        (LinearError("Нет соединения с Linear: ..."), "network"),
        (LinearError("Таймаут Linear (>30s)"), "timeout"),
        (LinearError("Linear GraphQL: ошибка запроса"), "error"),
    ]
    for err, expected_code in cases:
        task.status = TaskStatus.PENDING
        task.send_error = None
        backend = _make_backend(raise_on={0: err})
        list(send_tasks_iter(
            [task], container_id="t", backend=backend,
            on_status_change=lambda *a, **kw: None,
            cancel_check=lambda: False,
        ))
        assert expected_code in (task.send_error or ""), \
            f"expected {expected_code} in {task.send_error!r} for {err}"


# ── Cancellation ────────────────────────────────────────────────────


def test_send_iter_stops_on_cancel_between_tasks():
    """Cancel checked BEFORE each backend.create. Already-sent stays sent."""
    tasks = [_pending_task(f"T{i}", local_id=str(i)) for i in range(5)]
    backend = _make_backend()
    cancel_count = [0]

    def cancel_check():
        cancel_count[0] += 1
        # Trigger cancel after the 2nd send completes (3rd cancel_check).
        return cancel_count[0] >= 3

    list(send_tasks_iter(
        tasks, container_id="t", backend=backend,
        on_status_change=lambda *a, **kw: None,
        cancel_check=cancel_check,
    ))
    assert backend.create.call_count == 2
    assert tasks[0].status is TaskStatus.SENT
    assert tasks[1].status is TaskStatus.SENT
    assert tasks[2].status is TaskStatus.PENDING
    assert tasks[3].status is TaskStatus.PENDING
    assert tasks[4].status is TaskStatus.PENDING


# ── Backend invocation contract ──────────────────────────────────────


def test_send_iter_calls_backend_with_container_id_and_task():
    """Sender invokes backend.create(container_id, task) — that's the
    full contract. Backend-specific translation (priority enum → int /
    string, kwargs assembly) is the backend's job, tested elsewhere."""
    task = _pending_task("A", local_id="a", priority=Priority.HIGH)
    backend = _make_backend()
    list(send_tasks_iter(
        [task], container_id="container-uuid", backend=backend,
        on_status_change=lambda *a, **kw: None,
        cancel_check=lambda: False,
    ))
    backend.create.assert_called_once()
    args, _kwargs = backend.create.call_args
    assert args == ("container-uuid", task)


def test_send_marks_failed_on_trello_error_not_unexpected(caplog):
    """A TrelloError must be caught by the narrow handler (logged WARNING),
    not the belt-and-braces Exception handler (logged as 'unexpected error')."""
    from tasks.schema import Task, TaskStatus
    from tasks.sender import send_tasks_iter
    from tasks.trello_client import TrelloError

    backend = MagicMock()
    backend.create.side_effect = TrelloError("Trello вернул 401: invalid token")
    task = Task(title="A")
    statuses = []
    with caplog.at_level("WARNING", logger="tasks.sender"):
        list(send_tasks_iter(
            [task],
            container_id="l-1",
            backend=backend,
            on_status_change=lambda t, s: statuses.append(s),
            cancel_check=lambda: False,
        ))
    assert task.status is TaskStatus.FAILED
    assert task.send_error == "401"   # _short_error_code extracts the code
    # Narrow handler path → "send failed", NOT "unexpected error".
    assert any("send failed" in r.message for r in caplog.records)
    assert not any("unexpected error" in r.message for r in caplog.records)
