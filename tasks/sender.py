"""Send-to-backend orchestrator (backend-agnostic since Phase 6.4.1).

Pure logic — no Tk, no I/O outside the injected backend's `create()` call.
The dialog wraps this in a worker thread and marshals status updates back
to the UI via ``self.after(0, ...)``.

Public API:
    send_tasks_iter(tasks, *, container_id, backend, on_status_change,
                    cancel_check, retry_failed=False)
        → generator yielding Task objects after each status transition

The `backend` is a `tasks.backends.TaskBackend` (Protocol). Was named
`linear_client` in Phase 6.3 — renamed in 6.4.1 when we abstracted over
multiple backends. The orchestrator doesn't know or care whether the
backend talks GraphQL, REST, or telegrams.

Filtering rules:
- Initial send (retry_failed=False): send tasks where selected=True AND
  status=PENDING. Skip already-SENT, already-FAILED, and unselected tasks.
- Retry send (retry_failed=True): send tasks where status=FAILED. Already-
  SENT tasks are NEVER touched (avoids duplicate Linear/Glide issues).

Status transitions:
  PENDING → SENDING → SENT  (success)
  PENDING → SENDING → FAILED  (backend exception)
  FAILED → SENDING → SENT  (retry success)
  FAILED → SENDING → FAILED  (retry failure)
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from datetime import date

from tasks.dedup import dedup_marker
from tasks.glide_client import GlideError
from tasks.linear_client import LinearError
from tasks.schema import Task, TaskStatus
from tasks.trello_client import TrelloError

logger = logging.getLogger(__name__)


def send_tasks_iter(
    tasks: list[Task],
    *,
    container_id: str,
    backend,
    on_status_change: Callable,
    cancel_check: Callable[[], bool],
    retry_failed: bool = False,
    meeting_label: str = "",
) -> Iterator[Task]:
    """Iterate selected tasks and POST each via backend.create().

    Yields each task after its terminal status (SENT / FAILED) is set.
    Caller iterates the generator to drive the send (the generator does
    the work; the yielded values are mostly for testing).

    `cancel_check()` is called BEFORE each backend request. If it returns
    True, the iteration stops; tasks not yet sent retain their PENDING/
    FAILED status.

    `on_status_change(task, new_status)` is invoked on every transition.
    """
    for task in tasks:
        # Cancel check at the top of the loop — before any work.
        if cancel_check():
            logger.info("send cancelled before task %r", task.local_id)
            return

        if not _should_send(task, retry_failed=retry_failed):
            continue

        # PENDING (or FAILED if retry) → SENDING
        task.status = TaskStatus.SENDING
        task.send_error = None
        on_status_change(task, TaskStatus.SENDING)

        # Dedup (PR-3): a matched task whose row action stays "comment" is
        # commented onto the existing card instead of creating a duplicate.
        # The supports_comments guard mirrors the dialog gate (belt-and-
        # braces — a backend that can't comment falls back to create()).
        use_comment = (
            task.dup_match is not None
            and task.dup_action == "comment"
            and getattr(backend, "supports_comments", False)
        )

        try:
            if use_comment:
                _marker = dedup_marker(task.title)
                if backend.comment_exists(task.dup_match.ref, _marker):
                    logger.info(
                        "dedup idempotent: marker already on %s, skipping comment",
                        task.dup_match.ref,
                    )
                else:
                    backend.add_comment(
                        task.dup_match.ref,
                        _dup_comment_body(task, meeting_label, _marker),
                    )
            else:
                issue = backend.create(container_id, task)
        except (LinearError, GlideError, TrelloError) as e:
            task.status = TaskStatus.FAILED
            task.send_error = _short_error_code(str(e)) or "error"
            logger.warning(
                "send failed for task %r (%s): %s",
                task.local_id, task.title, e,
            )
            on_status_change(task, TaskStatus.FAILED)
            yield task
            continue
        except Exception as e:
            # Belt-and-braces: any unexpected exception → FAILED.
            task.status = TaskStatus.FAILED
            task.send_error = _short_error_code(str(e)) or "error"
            logger.exception(
                "unexpected error sending task %r (%s)",
                task.local_id, task.title,
            )
            on_status_change(task, TaskStatus.FAILED)
            yield task
            continue

        if use_comment:
            # Point the row at the EXISTING card and mark COMMENTED.
            task.status = TaskStatus.COMMENTED
            task.linear_issue_id = task.dup_match.identifier or None
            task.linear_issue_url = task.dup_match.url or None
            task.backend_ref = task.dup_match.ref
        else:
            task.status = TaskStatus.SENT
            # Field names are Linear-flavoured (Phase 6.0) but hold the
            # backend-agnostic identifier+url returned by backend.create().
            task.linear_issue_id = issue.identifier
            task.linear_issue_url = issue.url
            task.backend_ref = issue.ref
        task.send_error = None
        on_status_change(task, task.status)
        yield task


# ── Helpers ─────────────────────────────────────────────────────────


def _should_send(task: Task, *, retry_failed: bool) -> bool:
    if not task.selected:
        return False
    if retry_failed:
        return task.status is TaskStatus.FAILED
    return task.status is TaskStatus.PENDING


def _short_error_code(msg: str) -> str:
    """Extract a short tag from a backend error message.

    Examples:
        "Linear вернул 401: ..." → "401"
        "Glide 401: неверный..."  → "401"
        "Glide 429 rate-limit"    → "429"
        "Нет соединения с..."     → "network"
        "Таймаут Linear..."       → "timeout"
        anything else              → ""

    Backends localize messages differently; we match on numeric codes
    first, then language-agnostic keywords.
    """
    msg_lower = msg.lower()
    # HTTP status codes. \b ensures "1400" doesn't match "400" — between two
    # digits there's no word boundary, so "1400" is treated as one token.
    m = re.search(r"\b(4\d\d|5\d\d)\b", msg)
    if m:
        return m.group(1)
    if "соединен" in msg_lower or "connection" in msg_lower:
        return "network"
    if "таймаут" in msg_lower or "timeout" in msg_lower:
        return "timeout"
    return ""


def _dup_comment_body(task: Task, meeting_label: str, marker: str = "") -> str:
    """RU comment posted to the existing card when a task recurs (dedup)."""
    where = f' "{meeting_label}"' if meeting_label else ""
    body = (
        f"🔁 Эта задача снова обсуждалась на встрече{where} "
        f"({date.today().isoformat()})."
    )
    if task.description:
        body += f"\n\n{task.description}"
    if marker:
        body += f"\n\n{marker}"
    return body
