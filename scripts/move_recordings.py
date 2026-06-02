#!/usr/bin/env python3
"""One-time move of legacy recordings out of the ~/Documents root.

Old builds wrote recording_<ts>.wav straight into ~/Documents. This moves
those root files into the current recordings dir (<meetings_dir>/recordings/).
Dry-run by default; pass --apply to actually move. Non-recursive, exact glob —
it only touches recording_*.wav directly in ~/Documents, never subfolders or
other files. You-only; not bundled with the app.

Usage (from repo root):
    python scripts/move_recordings.py            # dry run
    python scripts/move_recordings.py --apply
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils import get_recordings_dir  # noqa: E402


def _select_root_recordings(documents_dir: str) -> list[str]:
    """recording_*.wav directly in documents_dir (non-recursive)."""
    pattern = os.path.join(documents_dir, "recording_*.wav")
    return sorted(p for p in glob.glob(pattern) if os.path.isfile(p))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Move legacy ~/Documents recordings into the recordings dir.",
    )
    ap.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    args = ap.parse_args()

    docs = os.path.join(os.path.expanduser("~"), "Documents")
    dest = get_recordings_dir()
    files = _select_root_recordings(docs)
    print(f"source: {docs}")
    print(f"dest:   {dest}")
    print(f"found:  {len(files)} recording_*.wav in the Documents root")

    if not args.apply:
        for f in files:
            print(f"  would move: {os.path.basename(f)}")
        print("DRY RUN — nothing moved. Re-run with --apply.")
        return 0

    os.makedirs(dest, exist_ok=True)
    moved = skipped = 0
    for f in files:
        target = os.path.join(dest, os.path.basename(f))
        if os.path.exists(target):
            print(f"  skip (exists): {os.path.basename(f)}")
            skipped += 1
            continue
        shutil.move(f, target)
        moved += 1
        print(f"  moved: {os.path.basename(f)}")
    print(f"done: moved={moved} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
