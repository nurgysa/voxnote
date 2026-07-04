"""Persistence + disk-derived view for the processing queue.

queue.json (active items only) lives at ~/.voxnote/queue.json, beside
config.json and directory.json. Atomic write (tmp + os.replace), mirroring
directory/store.py. build_view derives the displayed meeting list fresh from the
meetings dir (a two-level scan; project read from each meeting's speakers.json)
and overlays the active items. No Tk, no heavy deps; safe to import headlessly.

PR-B2: a meeting's status is binary on disk — transcript.md present ⇒ DONE,
else PENDING (VoxNote is transcribe-only). Hermes's downstream progress shows as
display badges (protocol.md / tasks.md presence), never as queue status.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from processing.model import QueueItem, StageStatus
from utils import load_speakers, load_voiceid_sidecar

FILENAME = "queue.json"
_SKIP_DIRS = {"recordings"}
_SAFE_VOXNOTE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


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


def _has(folder: str, name: str) -> bool:
    return os.path.isfile(os.path.join(folder, name))


def meeting_status_from_folder(folder: str) -> StageStatus:
    """DONE when a transcript exists in the folder (VoxNote's only job), else
    PENDING. Hermes's protocol/tasks are surfaced as badges, not status."""
    if _has(folder, "transcript.md") or _has(folder, "transcript.txt"):
        return StageStatus.DONE
    return StageStatus.PENDING


def hermes_badges_from_folder(folder: str) -> dict:
    """Hermes downstream-progress display flags: has Hermes written protocol.md /
    tasks.md into this meeting folder yet? Pure file-presence, never status."""
    return {
        "has_protocol": _has(folder, "protocol.md"),
        "has_tasks": _has(folder, "tasks.md"),
    }


def read_voxnote_id(folder: str) -> str | None:
    """Read ``voxnote_id`` from transcript.md frontmatter.

    Voice-ID sidecars are keyed by this stable id, while «Встречи» rows are
    keyed by folder path. Missing/malformed frontmatter simply means no badge.
    """
    path = os.path.join(folder, "transcript.md")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            in_frontmatter = False
            for raw in f:
                line = raw.strip()
                if line == "---":
                    if not in_frontmatter:
                        in_frontmatter = True
                        continue
                    break
                if in_frontmatter and line.startswith("voxnote_id:"):
                    value = line.split(":", 1)[1].strip().strip('"\'')
                    if not value or ".." in value or not _SAFE_VOXNOTE_ID_RE.match(value):
                        return None
                    return value

        return None
    except OSError:
        return None


def pending_voices_count_from_folder(folder: str) -> int:
    """Count unresolved Voice-ID sidecar entries for a meeting folder."""
    voxnote_id = read_voxnote_id(folder)
    if not voxnote_id:
        return 0
    payload = load_voiceid_sidecar(voxnote_id)
    pending = payload.get("pending") if isinstance(payload, dict) else None
    return len(pending) if isinstance(pending, list) else 0


def is_meeting_folder(folder: str) -> bool:
    """True if the folder holds meeting artifacts (so it is a meeting, not a
    project container). VoxNote writes transcript.md; legacy meetings may also
    carry description.md / segments.json, kept as markers for back-compat."""
    for marker in ("transcript.md", "transcript.txt", "description.md", "segments.json"):
        if os.path.isfile(os.path.join(folder, marker)):
            return True
    return False


def _row_from_folder(folder: str) -> QueueItem:
    speakers = load_speakers(folder)
    badges = hermes_badges_from_folder(folder)
    name = os.path.basename(os.path.normpath(folder))
    return QueueItem(
        id=folder,
        audio_path="",
        title=name,
        created_at="",
        meeting_folder=folder,
        auto=False,
        project_id=(speakers.get("project_id") or None),
        status=meeting_status_from_folder(folder),
        has_protocol=badges["has_protocol"],
        has_tasks=badges["has_tasks"],
        pending_voices_count=pending_voices_count_from_folder(folder),
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
            disk_row = rows[index[key]]
            item.has_protocol = disk_row.has_protocol
            item.has_tasks = disk_row.has_tasks
            item.pending_voices_count = disk_row.pending_voices_count
            rows[index[key]] = item
        else:
            rows.append(item)
    return rows
