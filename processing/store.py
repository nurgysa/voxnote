"""Persistence + disk-derived view for the processing queue.

queue.json (active items only) lives at ~/.voxnote/queue.json, beside
config.json and directory.json. Atomic write (tmp + os.replace), mirroring
directory/store.py. build_view derives the displayed meeting list fresh from the
meetings dir (a two-level scan; project read from each meeting's speakers.json)
and overlays the active items. No Tk, no heavy deps; safe to import headlessly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from processing.model import QueueItem, StageStatus
from utils import load_speakers

FILENAME = "queue.json"
_SKIP_DIRS = {"recordings"}


def _default_queue_path() -> Path:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    return home / ".voxnote" / FILENAME


def load_active(path: Path | str | None = None) -> list[QueueItem]:
    p = Path(path) if path is not None else _default_queue_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [QueueItem.from_dict(d) for d in data.get("items", [])]


def save_active(items: list[QueueItem], path: Path | str | None = None) -> None:
    p = Path(path) if path is not None else _default_queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": [it.to_dict() for it in items]}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = p.parent / f".{p.name}.tmp"
    tmp.write_text(encoded, encoding="utf-8")
    os.replace(tmp, p)


def stage_status_from_folder(folder: str) -> dict:
    """Derive transcript/protocol/tasks StageStatus from which files exist."""

    def has(name: str) -> bool:
        return os.path.isfile(os.path.join(folder, name))

    if has("transcript.md") or has("transcript.txt"):
        transcript = StageStatus.DONE
    else:
        transcript = StageStatus.PENDING
    protocol = StageStatus.DONE if has("protocol.md") else StageStatus.PENDING
    if has("tasks.json"):
        tasks = StageStatus.DONE
    elif has("tasks_raw.json"):
        tasks = StageStatus.AWAITING_REVIEW
    else:
        tasks = StageStatus.PENDING
    return {"transcript": transcript, "protocol": protocol, "tasks": tasks}


def is_meeting_folder(folder: str) -> bool:
    """True if the folder holds meeting artifacts (so it is a meeting, not a
    project container). create_history_entry writes transcript.md +
    description.md together, so these markers are reliable for real meetings."""
    for marker in ("transcript.md", "transcript.txt", "description.md", "segments.json"):
        if os.path.isfile(os.path.join(folder, marker)):
            return True
    return False


def _row_from_folder(folder: str) -> QueueItem:
    stages = stage_status_from_folder(folder)
    speakers = load_speakers(folder)
    name = os.path.basename(os.path.normpath(folder))
    return QueueItem(
        id=folder,
        audio_path="",
        title=name,
        created_at="",
        meeting_folder=folder,
        auto=False,
        project_id=(speakers.get("project_id") or None),
        transcript=stages["transcript"],
        protocol=stages["protocol"],
        tasks=stages["tasks"],
    )


def build_view(meetings_dir: str, active: list[QueueItem]) -> list[QueueItem]:
    """Derive display rows from disk (two-level: root meetings + meetings inside
    project folders), then overlay active items (authoritative for their folder).
    `recordings/` and non-meeting/non-project entries are skipped. Project is read
    from each meeting's speakers.json, never inferred from the folder name."""
    rows: list[QueueItem] = []
    try:
        entries = sorted(os.listdir(meetings_dir))
    except OSError:
        entries = []
    for entry in entries:
        full = os.path.join(meetings_dir, entry)
        if not os.path.isdir(full) or entry in _SKIP_DIRS:
            continue
        if is_meeting_folder(full):
            rows.append(_row_from_folder(full))
            continue
        try:
            subs = sorted(os.listdir(full))
        except OSError:
            subs = []
        for sub in subs:
            subfull = os.path.join(full, sub)
            if os.path.isdir(subfull) and sub not in _SKIP_DIRS and is_meeting_folder(subfull):
                rows.append(_row_from_folder(subfull))

    index = {
        os.path.normcase(os.path.abspath(r.meeting_folder)): i
        for i, r in enumerate(rows)
        if r.meeting_folder
    }
    for item in active:
        key = (
            os.path.normcase(os.path.abspath(item.meeting_folder))
            if item.meeting_folder
            else None
        )
        if key is not None and key in index:
            rows[index[key]] = item
        else:
            rows.append(item)
    return rows
