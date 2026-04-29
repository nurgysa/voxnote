"""On-disk persistence for the tasks pipeline.

Phase 6.1 writes ``tasks_raw.json`` — the immutable LLM-extraction snapshot —
into the active history-entry folder. Phase 6.2 adds ``tasks.json`` for
the editable, user-state-bearing version.

Atomic write: dump JSON to ``<folder>/.tasks_raw.json.tmp`` then ``os.replace``
into place. Prevents a partial file on disk if the process dies mid-write.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from tasks.schema import Task

RAW_FILENAME = "tasks_raw.json"

# Subset of Task.to_dict() keys persisted to tasks_raw.json. We deliberately
# omit the local-only send-state fields — that's the audit-trail discipline
# from the spec ("tasks_raw.json is immutable").
_RAW_FIELDS = (
    "local_id", "title", "description", "priority",
    "assignee_id", "assignee_name", "label_ids", "label_names", "due_date",
)


class PersistenceError(Exception):
    """Disk read/write failures bubble up as this."""


def _task_to_raw_dict(task: Task) -> dict:
    full = task.to_dict()
    return {k: full[k] for k in _RAW_FIELDS}


def save_tasks_raw(folder: str, tasks: list[Task], meta: dict) -> None:
    """Atomically write ``<folder>/tasks_raw.json``.

    ``meta`` keys: extracted_at, model, team_id, team_name, transcript_lang.
    Folder is created if missing.

    Raises PersistenceError on OS-level failure. Re-raises whatever
    json.dumps raises (callers in tests poison json.dumps to verify atomicity).
    """
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        **meta,
        "tasks": [_task_to_raw_dict(t) for t in tasks],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)

    final = target_dir / RAW_FILENAME
    tmp = target_dir / f".{RAW_FILENAME}.tmp"
    try:
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, final)
    except OSError as e:
        # Best-effort cleanup of the temp file before re-raising.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PersistenceError(f"Не удалось записать {RAW_FILENAME}: {e}") from e


def load_tasks_raw(folder: str) -> dict:
    """Read ``<folder>/tasks_raw.json`` and return ``{**meta, 'tasks': [Task, ...]}``.

    Raises PersistenceError if the file is missing or malformed.
    """
    path = Path(folder) / RAW_FILENAME
    if not path.is_file():
        raise PersistenceError(f"{RAW_FILENAME} not found in {folder}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PersistenceError(f"{RAW_FILENAME} malformed in {folder}: {e}") from e

    raw_tasks = data.pop("tasks", [])
    return {**data, "tasks": [Task.from_dict(t) for t in raw_tasks]}


MUTABLE_FILENAME = "tasks.json"


def save_tasks(folder: str, tasks: list[Task], meta: dict) -> None:
    """Atomically write ``<folder>/tasks.json`` — the mutable user-state snapshot.

    Differs from ``save_tasks_raw`` in two ways:
    1. Persists the full ``Task.to_dict()`` (incl. selected/status/linear_*).
    2. Adds an ``edited_at`` timestamp separate from ``extracted_at``.

    ``meta`` keys: extracted_at, model, team_id, team_name, transcript_lang.
    """
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        **meta,
        "edited_at": datetime.now().isoformat(timespec="seconds"),
        "tasks": [t.to_dict() for t in tasks],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)

    final = target_dir / MUTABLE_FILENAME
    tmp = target_dir / f".{MUTABLE_FILENAME}.tmp"
    try:
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, final)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PersistenceError(f"Не удалось записать {MUTABLE_FILENAME}: {e}") from e


def load_tasks(folder: str) -> dict:
    """Read ``<folder>/tasks.json`` and return ``{**meta, 'tasks': [Task, ...]}``.

    Raises PersistenceError if missing or malformed.
    """
    path = Path(folder) / MUTABLE_FILENAME
    if not path.is_file():
        raise PersistenceError(f"{MUTABLE_FILENAME} not found in {folder}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PersistenceError(f"{MUTABLE_FILENAME} malformed in {folder}: {e}") from e

    raw_tasks = data.pop("tasks", [])
    return {**data, "tasks": [Task.from_dict(t) for t in raw_tasks]}
