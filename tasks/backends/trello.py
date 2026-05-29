"""Trello adapter — wraps tasks.trello_client.TrelloClient.

Translates between Phase 6.0 schema (Task / Priority enum) and Trello's
card model. Rich grounding parity with Linear: assignee + labels come from
the board (via context()). Trello has no native priority field, so priority
is rendered as a line in the card description (spec decision D2).

Container = a Trello list (id is the list id; create() uses it as idList).
The "Board / List" display string is folded into Container.name (key=None)
so the dialog's inline dropdown format needs no change (spec decision D4).
"""
from __future__ import annotations

from tasks.backends.base import Container, CreatedIssue
from tasks.schema import Priority, Task
from tasks.trello_client import TrelloClient

# Russian priority labels for the description line. NONE is omitted (no line).
_PRIORITY_LABELS_RU: dict[Priority, str] = {
    Priority.URGENT: "Срочный",
    Priority.HIGH:   "Высокий",
    Priority.MEDIUM: "Средний",
    Priority.LOW:    "Низкий",
}


class TrelloBackend:
    """Adapter: dialog/sender ←→ TrelloClient."""

    name = "trello"
    display_name = "Trello"

    def __init__(self, client: TrelloClient):
        self._client = client

    def bootstrap(self) -> list[Container]:
        rows = self._client.list_containers()
        return [
            Container(
                id=r["list_id"],
                name=f"{r['board_name']} / {r['list_name']}",
                key=None,
            )
            for r in rows
        ]

    def container_label(self, c: Container) -> str:
        # "Board / List" already lives in name (D4) — no key suffix.
        return c.name

    def context(self, container_id: str) -> dict:
        # container_id is the list id; board_context resolves list→board.
        return self._client.board_context(container_id)

    def create(self, container_id: str, task: Task) -> CreatedIssue:
        desc = task.description or ""
        if task.priority is not Priority.NONE:
            label = _PRIORITY_LABELS_RU.get(task.priority)
            if label:
                line = f"**Приоритет:** {label}"
                desc = f"{line}\n\n{desc}" if desc else line

        card = self._client.create_card(
            id_list=container_id,
            name=task.title,
            desc=desc or None,
            id_members=[task.assignee_id] if task.assignee_id else None,
            id_labels=list(task.label_ids) if task.label_ids else None,
            due=task.due_date or None,
        )

        id_short = card.get("idShort")
        if id_short is not None:
            identifier = f"#{id_short}"
        else:
            identifier = card.get("shortLink") or "?"
        return CreatedIssue(identifier=identifier, url=card.get("url") or "")

    def close(self) -> None:
        self._client.close()
