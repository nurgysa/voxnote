"""Project -> folder mapping for meeting storage.

speakers.json.project_id is the source of truth for a meeting's project; the
folder location under meetings_dir/<project>/ is its reflection. These helpers
map a resolved Project to a folder name and move a meeting folder into place.
The caller resolves project_id -> Project, so this module stays decoupled from
the directory store and from Tk.
"""
from __future__ import annotations

import os
import re
import shutil

from directory.schema import Project

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def project_dirname(project: Project) -> str:
    """Filesystem-safe folder name for a project. Falls back to a short id slice
    when the name sanitizes to empty (e.g. all-illegal characters)."""
    cleaned = _ILLEGAL.sub("_", project.name).strip().strip("._")
    return cleaned or project.id[:8]


def target_dir(meetings_dir: str, project: Project | None) -> str:
    """Directory a meeting with this project belongs in: a project subfolder, or
    the meetings_dir root when project is None."""
    if project is None:
        return meetings_dir
    return os.path.join(meetings_dir, project_dirname(project))


def move_into(folder: str, dest_dir: str) -> str:
    """Move `folder` into `dest_dir`; return the new path. No-op (returns the
    normalized original) when already there. Collision-safe -- never overwrites;
    appends -2, -3, ... instead."""
    folder = os.path.normpath(folder)
    dest_dir = os.path.normpath(dest_dir)
    if os.path.normcase(os.path.dirname(folder)) == os.path.normcase(dest_dir):
        return folder
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(folder)
    target = os.path.join(dest_dir, base)
    n = 2
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{base}-{n}")
        n += 1
    shutil.move(folder, target)
    return target


def assign_project(meeting_folder: str, project: Project | None, meetings_dir: str) -> str:
    """Set the meeting's project (write speakers.json) and move its folder into
    the project dir (or the root when project is None). The single placement seam
    used by the worker's transcribe stage and (PR-3) by reassignment.

    Writes metadata FIRST, then moves: a failed move leaves a consistent (if
    mislocated) state recoverable on the next assign (spec failure-handling).
    Only project_id changes — participants/speakers are preserved (load-merge-save).
    Returns the folder's new path.
    """
    from utils import load_speakers, save_speakers

    existing = load_speakers(meeting_folder)
    project_id = project.id if project is not None else None
    save_speakers(
        meeting_folder,
        project_id,
        list(existing.get("participants") or []),
        existing.get("speakers") or {},
    )
    return move_into(meeting_folder, target_dir(meetings_dir, project))
