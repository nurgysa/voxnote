"""Long-meeting downstream processing for VoxNote transcript.md files.

VoxNote's queue stays transcribe-only. This module is a headless downstream
processor for Hermes/operator use after a ``transcript.md`` already exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class LongMeetingError(Exception):
    """Base error for long-meeting processing failures."""


@dataclass(frozen=True)
class MeetingNote:
    note_path: Path
    history_folder: Path
    meta: dict[str, str]
    body: str


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text.strip()

    meta: dict[str, str] = {}
    for raw in lines[1:end]:
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        if not key:
            continue
        meta[key] = _strip_quotes(value)

    body = "\n".join(lines[end + 1 :]).strip()
    return meta, body


def read_meeting_note(note_path: str | Path) -> MeetingNote:
    path = Path(note_path)
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    return MeetingNote(
        note_path=path,
        history_folder=path.parent,
        meta=meta,
        body=body,
    )
