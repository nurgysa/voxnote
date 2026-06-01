"""Linear adapter — wraps tasks.linear_client.LinearClient.

Translates between Phase 6.0 schema (`Task` enum, UUIDs, ISO dates) and
Linear's GraphQL types (int priority, native UUIDs).
"""
from __future__ import annotations

from tasks.backends.base import Container, CreatedIssue, ExistingItem
from tasks.linear_client import LinearClient
from tasks.schema import Priority, Task


class LinearBackend:
    """Adapter: dialog/sender ←→ LinearClient."""

    name = "linear"
    display_name = "Linear"
    supports_comments = True

    def __init__(self, client: LinearClient):
        self._client = client

    def bootstrap(self) -> list[Container]:
        data = self._client.bootstrap()
        teams = data.get("teams", []) or []
        return [
            Container(id=t["id"], name=t.get("name", "?"), key=t.get("key"))
            for t in teams
        ]

    def container_label(self, c: Container) -> str:
        # Linear teams have a short key like "ENG", "NUR" — appending it
        # disambiguates between teams with similar names ("Mobile" vs
        # "Mobile QA"). Fall back to plain name if key missing.
        return f"{c.name} ({c.key})" if c.key else c.name

    def context(self, container_id: str) -> dict:
        # Returns {"members": [...], "labels": [...]} — extractor uses
        # these for prompt grounding. team_context isn't cached: members
        # join/leave teams often enough that 24h staleness costs more
        # than the cheap GraphQL call.
        return self._client.team_context(container_id)

    def create(self, container_id: str, task: Task) -> CreatedIssue:
        # Translate from generic Task → Linear's flat kwargs.
        # None values are deliberately omitted (not sent as null) — Linear
        # treats null as "set this field to null" rather than "leave default".
        kwargs: dict = {
            "team_id": container_id,
            "title": task.title,
        }
        if task.description:
            kwargs["description"] = task.description
        if task.priority is not Priority.NONE:
            kwargs["priority"] = int(task.priority.value)
        if task.assignee_id:
            kwargs["assignee_id"] = task.assignee_id
        if task.label_ids:
            kwargs["label_ids"] = list(task.label_ids)
        if task.due_date:
            kwargs["due_date"] = task.due_date

        issue = self._client.create_issue(**kwargs)
        return CreatedIssue(
            identifier=issue.get("identifier") or "?",
            url=issue.get("url") or "",
            ref=issue.get("id") or "",
        )

    def add_comment(self, ref: str, body: str) -> None:
        self._client.add_comment(ref, body)

    def list_existing(self, container_id: str) -> list[ExistingItem]:
        return [
            ExistingItem(
                title=i.get("title") or "",
                ref=i.get("id") or "",
                identifier=i.get("identifier") or "",
                url=i.get("url") or "",
                description=i.get("description") or "",
            )
            for i in self._client.list_issues(container_id)
        ]

    def comment_exists(self, ref: str, marker: str) -> bool:
        return any(marker in body for body in self._client.list_comments(ref))

    def close(self) -> None:
        self._client.close()
