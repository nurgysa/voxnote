# tests/test_sender_dedup_idempotent.py
from tasks.dedup import SentTask, dedup_marker
from tasks.schema import Priority, Task, TaskStatus
from tasks.sender import _dup_comment_body, send_tasks_iter


class _Backend:
    supports_comments = True

    def __init__(self, existing_marker=None):
        self._existing = existing_marker
        self.commented = []

    def comment_exists(self, ref, marker):
        return self._existing == marker

    def add_comment(self, ref, body):
        self.commented.append((ref, body))

    def create(self, container_id, task):
        raise AssertionError("must not create when commenting")

    def close(self):
        pass


def _dup_task(title="Изучить систему СУП"):
    t = Task(local_id="l1", title=title, description="", priority=Priority.MEDIUM,
             status=TaskStatus.PENDING, selected=True)
    t.dup_match = SentTask(title=title, backend="linear", container_id="c",
                           ref="ref-1", identifier="NUR-37", url="u",
                           meeting_name="", meeting_date="")
    t.dup_action = "comment"
    return t


def test_comment_body_carries_marker():
    body = _dup_comment_body(_dup_task(), "Встреча", dedup_marker("Изучить систему СУП"))
    assert dedup_marker("Изучить систему СУП") in body


def test_posts_when_no_existing_marker():
    b = _Backend(existing_marker=None)
    t = _dup_task()
    list(send_tasks_iter([t], container_id="c", backend=b,
                         on_status_change=lambda *a: None, cancel_check=lambda: False))
    assert len(b.commented) == 1
    assert t.status is TaskStatus.COMMENTED


def test_skips_post_when_marker_already_present():
    marker = dedup_marker("Изучить систему СУП")
    b = _Backend(existing_marker=marker)
    t = _dup_task()
    list(send_tasks_iter([t], container_id="c", backend=b,
                         on_status_change=lambda *a: None, cancel_check=lambda: False))
    assert b.commented == []                 # idempotent: no second comment
    assert t.status is TaskStatus.COMMENTED   # still resolves as commented
