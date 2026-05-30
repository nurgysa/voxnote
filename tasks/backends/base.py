"""Backend Protocol + value types — the contract every adapter satisfies.

A `TaskBackend` exposes four operations:
    bootstrap()        → list[Container]      (containers visible to the token)
    container_label(c) → str                  (UI label for the dropdown)
    context(cid)       → dict                 (members + labels, or empty)
    create(cid, task)  → CreatedIssue         (POST/mutation per task)
plus a `close()` method to release HTTP sessions.

Container/CreatedIssue are intentionally minimal — backends differ in what
metadata they track, but the dialog and sender only need the listed fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tasks.schema import Task


@dataclass(frozen=True)
class Container:
    """A backend "container" where tasks live.

    For Linear: a Team (id is a UUID, key is the short prefix like "NUR").
    For Glide: a Board (id is a UUID, key is None — Glide has no short codes).
    """
    id: str
    name: str
    key: str | None = None


@dataclass(frozen=True)
class CreatedIssue:
    """Result of a successful create() call.

    `identifier` is what the UI shows in the row badge after send:
    - Linear: human-readable ENG-1234 from the API response
    - Glide: first 6 chars of the task UUID (Glide has no human ID)

    `url` opens the task in the backend's web UI when the user clicks
    the SENT row.

    `ref` is the *comment-addressable* backend id — what add_comment()
    needs to target this object later (task-dedup feature):
    - Linear: the GraphQL node UUID (issue.id), NOT the ENG-1234 identifier
      — commentCreate's issueId rejects the human identifier.
    - Trello: the full card id (or shortLink) — the #idShort badge value is
      not a valid {id} path param for the comment endpoint.
    - Glide: the task UUID, but unused (Glide has no comment API; the
      backend declares supports_comments = False).
    Defaults to "" for backends/tests that don't populate it.
    """
    identifier: str
    url: str
    ref: str = ""


class TaskBackend(Protocol):
    """Every backend (Linear, Glide, future) implements this."""

    name: str             # stable id for config / persistence ("linear", "glide")
    display_name: str     # human-facing dropdown label ("Linear", "Glide")

    # Capability flag for the task-dedup feature. True if the backend can
    # POST a comment to an existing object via add_comment(). Backends whose
    # API has no comment concept (Glide) set this False; the dedup gate then
    # skips commenting and creates the task as usual. Mirrors the
    # ``supports_mixed`` capability pattern on providers/base.py.
    supports_comments: bool = False

    def bootstrap(self) -> list[Container]:
        """Validate the API key + return all containers visible to it.

        Single round-trip where possible. Used to populate the dropdown
        on dialog open and for the [↻] refresh button.
        """
        ...

    def container_label(self, c: Container) -> str:
        """How to render a container in the dropdown.

        Linear: "Engineering (ENG)". Glide: just "Inbox" (no key).
        """
        ...

    def context(self, container_id: str) -> dict:
        """Return member + label lists for LLM grounding.

        Linear: {"members": [...], "labels": [...]} from team_context.
        Glide: {"members": [], "labels": []} (no grounding — schema is
        too heterogeneous across boards for reliable LLM matching;
        assignee/labels stay manual in the editor).
        """
        ...

    def create(self, container_id: str, task: Task) -> CreatedIssue:
        """Send a single task to the backend. Returns identifier + URL + ref.

        Raises whatever the underlying client raises (LinearError /
        GlideError) — sender catches those and marks the task FAILED.
        """
        ...

    def add_comment(self, ref: str, body: str) -> None:
        """Post a comment to an existing backend object (task-dedup feature).

        `ref` is the value carried by CreatedIssue.ref / Task.backend_ref.
        Only called for backends with supports_comments = True; backends
        that opt out may raise NotImplementedError. Raises the backend's
        own error class (LinearError / TrelloError) on HTTP/network failure.
        """
        ...

    def close(self) -> None:
        """Release HTTP session. Safe to call from another thread to
        cancel an in-flight request (raises ConnectionError in the
        worker, which sender catches as a generic Exception)."""
        ...
