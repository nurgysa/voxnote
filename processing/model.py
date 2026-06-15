"""Queue item model for the processing pipeline.

Pure stdlib — no I/O, no Tk. Mirrors directory/schema.py: a str-enum plus a
mutable dataclass with explicit to_dict / tolerant from_dict so the on-disk
queue.json stays forward/backward compatible.

PR-B2: VoxNote's queue is transcribe-only. One item = one transcription job
carried to a single ``status`` (Hermes owns protocol/tasks downstream).
``source`` records how the audio arrived (record/pick/inbox) and drives the
archive move-vs-copy decision; ``source_path`` is where the audio was archived
in Drive ``sources/``. ``has_protocol``/``has_tasks`` are disk-derived display
badges (store.build_view fills them) showing Hermes's downstream progress —
never queue status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class QueueItem:
    id: str
    audio_path: str
    title: str
    created_at: str
    meeting_folder: str | None = None
    options: dict = field(default_factory=dict)
    auto: bool = False
    project_id: str | None = None
    source: str = "pick"             # record | pick | inbox
    source_path: str | None = None   # archived audio in Drive sources/
    status: StageStatus = StageStatus.PENDING
    nudge_delivered: bool = False
    error_message: str | None = None
    has_protocol: bool = False       # display badge: Hermes wrote protocol.md
    has_tasks: bool = False          # display badge: Hermes wrote tasks.md

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "audio_path": self.audio_path,
            "title": self.title,
            "created_at": self.created_at,
            "meeting_folder": self.meeting_folder,
            "options": dict(self.options),
            "auto": self.auto,
            "project_id": self.project_id,
            "source": self.source,
            "source_path": self.source_path,
            "status": self.status.value,
            "nudge_delivered": self.nudge_delivered,
            "error_message": self.error_message,
            "has_protocol": self.has_protocol,
            "has_tasks": self.has_tasks,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QueueItem:
        try:
            status = StageStatus(d.get("status") or "pending")
        except ValueError:
            status = StageStatus.PENDING
        return cls(
            id=d["id"],
            audio_path=d.get("audio_path", ""),
            title=d.get("title", ""),
            created_at=d.get("created_at", ""),
            meeting_folder=d.get("meeting_folder"),
            options=dict(d.get("options") or {}),
            auto=bool(d.get("auto", False)),
            project_id=d.get("project_id"),
            source=d.get("source") or "pick",
            source_path=d.get("source_path"),
            status=status,
            nudge_delivered=bool(d.get("nudge_delivered", False)),
            error_message=d.get("error_message"),
            has_protocol=bool(d.get("has_protocol", False)),
            has_tasks=bool(d.get("has_tasks", False)),
        )
