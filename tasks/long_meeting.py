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


@dataclass(frozen=True)
class TranscriptChunk:
    index: int
    total: int
    text: str
    char_start: int
    char_end: int


def _split_turns(body: str) -> list[str]:
    turns = [part.strip() for part in body.split("\n\n") if part.strip()]
    return turns or [body.strip()]


def chunk_transcript(body: str, *, max_chars: int = 8000) -> list[TranscriptChunk]:
    clean = body.strip()
    if not clean:
        raise ValueError("empty transcript body")
    if max_chars < 1000:
        max_chars = 1000

    turns = _split_turns(clean)
    raw_chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for turn in turns:
        addition = len(turn) + (2 if current else 0)
        if current and current_len + addition > max_chars:
            raw_chunks.append("\n\n".join(current))
            current = [turn]
            current_len = len(turn)
        else:
            current.append(turn)
            current_len += addition
    if current:
        raw_chunks.append("\n\n".join(current))

    chunks: list[TranscriptChunk] = []
    cursor = 0
    total = len(raw_chunks)
    for idx, text in enumerate(raw_chunks, 1):
        start = clean.find(text, cursor)
        if start < 0:
            start = cursor
        end = start + len(text)
        chunks.append(TranscriptChunk(idx, total, text, start, end))
        cursor = end
    return chunks
