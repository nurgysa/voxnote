#!/usr/bin/env python3
"""One-time: relocate meetings under their project folder.

Reads each root-level meeting's speakers.json; if it carries a project_id that
resolves to a directory Project, moves the folder into meetings_dir/<project>/.
Meetings without a (resolvable) project_id stay in the root. Dry-run by default;
--apply to move. Non-destructive: never overwrites (collision-safe). You-only;
not bundled with the app.

Usage (from repo root):
    python scripts/organize_by_project.py            # dry run
    python scripts/organize_by_project.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from directory.store import DirectoryStore  # noqa: E402
from processing.layout import move_into, target_dir  # noqa: E402
from processing.store import is_meeting_folder  # noqa: E402
from utils import get_meetings_dir, load_speakers  # noqa: E402

_SKIP = {"recordings"}


def _plan(meetings_dir: str, store) -> list[tuple[str, str, str]]:
    """Return (folder, dest_dir, project_name) for root meetings with a
    resolvable project. Only root-level meeting folders are considered."""
    out: list[tuple[str, str, str]] = []
    try:
        entries = sorted(os.listdir(meetings_dir))
    except OSError:
        return out
    for entry in entries:
        full = os.path.join(meetings_dir, entry)
        if not os.path.isdir(full) or entry in _SKIP:
            continue
        if not is_meeting_folder(full):
            continue  # likely already a project folder
        pid = (load_speakers(full).get("project_id") or "").strip()
        if not pid:
            continue
        project = store.get_project(pid)
        if project is None:
            continue
        out.append((full, target_dir(meetings_dir, project), project.name))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Relocate meetings under their project folder.",
    )
    ap.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    args = ap.parse_args()

    meetings_dir = get_meetings_dir()
    store = DirectoryStore()
    store.load()
    plan = _plan(meetings_dir, store)

    print(f"meetings dir: {meetings_dir}")
    print(f"found: {len(plan)} meeting(s) with a resolvable project")
    if not args.apply:
        for folder, _dest, name in plan:
            print(f"  would move: {os.path.basename(folder)} -> {name}/")
        print("DRY RUN — nothing moved. Re-run with --apply.")
        return 0

    moved = 0
    for folder, dest, name in plan:
        new = move_into(folder, dest)
        moved += 1
        print(f"  moved: {os.path.basename(folder)} -> {name}/  ({new})")
    print(f"done: moved={moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
