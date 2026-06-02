"""Queue item model for the processing pipeline.

Pure stdlib — no I/O, no Tk. Mirrors directory/schema.py: a str-enum plus a
mutable dataclass with explicit to_dict / tolerant from_dict so the on-disk
queue.json stays forward/backward compatible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    AWAITING_REVIEW = "awaiting_review"


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
    transcript: StageStatus = StageStatus.PENDING
    protocol: StageStatus = StageStatus.PENDING
    tasks: StageStatus = StageStatus.PENDING
    error_stage: str | None = None
    error_message: str | None = None

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
            "transcript": self.transcript.value,
            "protocol": self.protocol.value,
            "tasks": self.tasks.value,
            "error_stage": self.error_stage,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QueueItem:
        def _stage(key: str) -> StageStatus:
            try:
                return StageStatus(d.get(key) or "pending")
            except ValueError:
                return StageStatus.PENDING

        return cls(
            id=d["id"],
            audio_path=d.get("audio_path", ""),
            title=d.get("title", ""),
            created_at=d.get("created_at", ""),
            meeting_folder=d.get("meeting_folder"),
            options=dict(d.get("options") or {}),
            auto=bool(d.get("auto", False)),
            project_id=d.get("project_id"),
            transcript=_stage("transcript"),
            protocol=_stage("protocol"),
            tasks=_stage("tasks"),
            error_stage=d.get("error_stage"),
            error_message=d.get("error_message"),
        )
