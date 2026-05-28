"""Pure-Python meetings folder migration.

Three exported functions:
  - count_meetings(path) — how many subfolders look like meetings
  - detect_old_locations(probe_paths=None) — scan legacy paths for entries
  - migrate_meetings(src, dst, on_progress, cancel_event) — move all folders

No Tk imports. All UI integration lives in ui/dialogs/migration.py.

See docs/superpowers/specs/2026-05-28-meetings-folder-picker-design.md.
"""
from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from datetime import datetime


def count_meetings(path: str) -> int:
    """Count subdirectories under `path`. Loose files don't count.

    Returns 0 if `path` doesn't exist (no error — caller might be probing
    a path that's about to be created).
    """
    if not os.path.isdir(path):
        return 0
    return sum(
        1 for name in os.listdir(path)
        if os.path.isdir(os.path.join(path, name))
    )


def detect_old_locations(
    probe_paths: list[str] | None = None,
) -> list[tuple[str, int]]:
    """Find legacy meeting folders with content.

    Returns [(path, count), ...] sorted by count descending. Excludes
    paths that don't exist OR have zero meetings.

    `probe_paths` is injected for testability. In production code the
    caller (typically App.__init__) passes utils._LEGACY_HISTORY_LOCATIONS;
    this module stays decoupled from utils.py's path constants.
    """
    if probe_paths is None:
        probe_paths = []
    seen: dict[str, int] = {}
    for raw_path in probe_paths:
        path = os.path.abspath(raw_path)
        if path in seen:
            continue
        n = count_meetings(path)
        if n > 0:
            seen[path] = n
    return sorted(seen.items(), key=lambda kv: kv[1], reverse=True)


def migrate_meetings(
    src: str,
    dst: str,
    on_progress: Callable[[int, int, str], None],
    cancel_event: threading.Event,
) -> dict:
    """Move all subfolders from `src` to `dst`.

    Each folder is moved atomically via shutil.move (os.rename for
    same-volume, copy2+remove for cross-volume — stdlib handles both).
    Per-folder progress; no per-byte tracking.

    On collision (dst already has a folder by the same name), the new
    one gets an `_imported_<HHMMSS>` suffix. Timestamp gives uniqueness
    even across multiple migration runs.

    On per-folder error (locked file, permission denied, disk full for
    that folder), the error is recorded and the next folder is tried —
    partial migration beats total failure.

    Cancellation: checked between folders only. The in-progress folder
    completes its move (we don't kill shutil mid-call).

    Returns:
      {
        "moved": [folder_name, ...],
        "skipped": [],   # reserved for future use (e.g. user pre-skip filter)
        "errors": [(folder_name, error_msg), ...],
        "cancelled": bool,
      }
    """
    os.makedirs(dst, exist_ok=True)

    src_entries: list[str] = []
    if os.path.isdir(src):
        src_entries = [
            d for d in sorted(os.listdir(src))
            if os.path.isdir(os.path.join(src, d))
        ]

    total = len(src_entries)
    moved: list[str] = []
    errors: list[tuple[str, str]] = []

    for i, name in enumerate(src_entries):
        if cancel_event.is_set():
            break

        on_progress(i, total, name)

        src_path = os.path.join(src, name)
        dst_path = os.path.join(dst, name)

        if os.path.exists(dst_path):
            ts = datetime.now().strftime("%H%M%S")
            dst_path = os.path.join(dst, f"{name}_imported_{ts}")

        try:
            shutil.move(src_path, dst_path)
            moved.append(name)
        except (OSError, PermissionError) as e:
            errors.append((name, str(e)))
            # Continue with next folder
            continue

        on_progress(i + 1, total, name)

    return {
        "moved": moved,
        "skipped": [],
        "errors": errors,
        "cancelled": cancel_event.is_set(),
    }
