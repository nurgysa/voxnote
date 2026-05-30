"""Data model for the meeting-tasks pipeline.

Defines:
- Priority enum (maps to Linear API int 0-4)
- TaskStatus enum (send-status to Linear, used in Phase 6.3)
- Task dataclass
- Serialization helpers (to_dict / from_dict / priority_from_string)

Pure stdlib — no third-party deps, no I/O.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum


class Priority(IntEnum):
    """Linear-compatible task priority. Maps directly to Linear's int field.

    Counter-intuitive: 1 = Urgent, 4 = Low. Lower = higher priority.
    """
    NONE   = 0
    URGENT = 1
    HIGH   = 2
    MEDIUM = 3
    LOW    = 4


class TaskStatus(Enum):
    """Send-to-Linear status for a Task. Used in Phase 6.3+.

    Stored in tasks.json by .value (string) so JSON stays readable.
    """
    PENDING = "pending"   # not yet attempted
    SENDING = "sending"   # in flight
    SENT    = "sent"      # successfully created in Linear
    FAILED  = "failed"    # last attempt failed (see send_error)
    SKIPPED = "skipped"   # user unchecked the task


@dataclass
class Task:
    """A single meeting-extracted task. Edited in UI, sent to Linear.

    Fields divided into LLM-extracted (top half) and local-only (bottom half).
    Local fields support the editor and Linear send lifecycle, never go to
    the LLM, and never come back from Linear.
    """
    # ── LLM-extracted fields ──
    title: str
    description: str = ""
    priority: Priority = Priority.NONE
    assignee_id: str | None = None     # Linear member UUID
    assignee_name: str | None = None   # cached display name for UI
    label_ids: list[str] = field(default_factory=list)
    label_names: list[str] = field(default_factory=list)
    due_date: str | None = None        # ISO "YYYY-MM-DD"

    # ── Local-only fields ──
    local_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    selected: bool = True
    status: TaskStatus = TaskStatus.PENDING
    linear_issue_id: str | None = None
    linear_issue_url: str | None = None
    # Comment-addressable backend id (task-dedup): Linear node UUID /
    # Trello full card id. Distinct from linear_issue_id, which holds the
    # human identifier (ENG-1234) for the UI badge. Persisted so a future
    # meeting's dedup pass can comment on this object instead of duplicating.
    backend_ref: str | None = None
    send_error: str | None = None

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict. Enums become their .value (str)."""
        return {
            "local_id": self.local_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.name.lower(),    # 'high', not 2
            "assignee_id": self.assignee_id,
            "assignee_name": self.assignee_name,
            "label_ids": list(self.label_ids),
            "label_names": list(self.label_names),
            "due_date": self.due_date,
            "selected": self.selected,
            "status": self.status.value,
            "linear_issue_id": self.linear_issue_id,
            "linear_issue_url": self.linear_issue_url,
            "backend_ref": self.backend_ref,
            "send_error": self.send_error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        """Inverse of to_dict. Tolerant of missing optional fields.

        Older tasks.json files (pre-Phase-6.3) may lack status/linear_*;
        we apply defaults rather than raising.
        """
        return cls(
            title=d["title"],
            description=d.get("description", ""),
            priority=priority_from_string(d.get("priority")),
            assignee_id=d.get("assignee_id"),
            assignee_name=d.get("assignee_name"),
            label_ids=list(d.get("label_ids", [])),
            label_names=list(d.get("label_names", [])),
            due_date=d.get("due_date"),
            local_id=d.get("local_id") or str(uuid.uuid4()),  # generate fresh id if absent or empty
            selected=d.get("selected", True),
            status=TaskStatus(d.get("status", "pending")),
            linear_issue_id=d.get("linear_issue_id"),
            linear_issue_url=d.get("linear_issue_url"),
            backend_ref=d.get("backend_ref"),
            send_error=d.get("send_error"),
        )


def priority_from_string(name: str | None) -> Priority:
    """Map LLM-returned priority strings to Priority enum.

    Case-insensitive. Unknown strings (including None and empty) → NONE.
    Caller is responsible for logging warnings on fallback.
    """
    if not name:
        return Priority.NONE
    try:
        return Priority[name.strip().upper()]
    except KeyError:
        return Priority.NONE
