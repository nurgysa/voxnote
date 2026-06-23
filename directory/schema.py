"""Data model for the people/projects directory.

Pure stdlib — no third-party deps, no I/O. Mirrors tasks/schema.py style:
mutable dataclasses with explicit to_dict / tolerant from_dict.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Voiceprint:
    """One enrolled voiceprint = an opaque Speechmatics speaker identifier, tied
    to the model that issued it (cross-model identifiers are ignored server-side).
    A person accumulates several across meetings — different voice tonalities —
    and the worker passes them all on identify. (Voice-ID Phase B fills these.)"""

    identifier: str
    model: str
    provider: str = "speechmatics"
    enrolled_at: str = field(default_factory=_now_iso)
    source_meeting: str = ""

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "model": self.model,
            "provider": self.provider,
            "enrolled_at": self.enrolled_at,
            "source_meeting": self.source_meeting,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Voiceprint:
        return cls(
            identifier=d.get("identifier", ""),
            model=d.get("model", ""),
            provider=d.get("provider", "speechmatics"),
            enrolled_at=d.get("enrolled_at") or _now_iso(),
            source_meeting=d.get("source_meeting", ""),
        )


@dataclass
class Person:
    """A meeting participant. project_ids reference Project.id (relation owner)."""

    full_name: str
    role: str = ""
    project_ids: list[str] = field(default_factory=list)
    voiceprints: list[Voiceprint] = field(default_factory=list)
    tracker_member_id: str | None = None
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "full_name": self.full_name,
            "role": self.role,
            "project_ids": list(self.project_ids),
            "voiceprints": [vp.to_dict() for vp in self.voiceprints],
            "tracker_member_id": self.tracker_member_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Person:
        return cls(
            full_name=d["full_name"],
            role=d.get("role", ""),
            project_ids=list(d.get("project_ids", [])),
            voiceprints=[Voiceprint.from_dict(v) for v in d.get("voiceprints", [])],
            tracker_member_id=d.get("tracker_member_id"),
            id=d.get("id") or _new_id(),
            created_at=d.get("created_at") or _now_iso(),
            updated_at=d.get("updated_at") or _now_iso(),
        )


@dataclass
class Project:
    """A project with a description used to ground protocol/task prompts."""

    name: str
    description: str = ""
    tracker_ref: str | None = None
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tracker_ref": self.tracker_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Project:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            tracker_ref=d.get("tracker_ref"),
            id=d.get("id") or _new_id(),
            created_at=d.get("created_at") or _now_iso(),
            updated_at=d.get("updated_at") or _now_iso(),
        )
