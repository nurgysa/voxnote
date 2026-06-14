# processing/sources.py
"""Archive audio originals into the Google Drive `sources` folder.

A plain filesystem write — Google Drive Desktop syncs it; no gdrive API. The
meeting's transcript.md records where the archived audio lives. `move=True` for
in-app recordings and inbox files (ours to relocate; this is what drains the
inbox); `move=False` (copy) for user-picked files (leave their original in place).
"""
from __future__ import annotations

import os
import shutil


def archive_audio(
    audio_path: str, sources_dir: str, base_name: str, *, move: bool
) -> str:
    """Place ``audio_path`` at ``<sources_dir>/<base_name><ext>``, collision-safe
    (``-2``, ``-3`` … never overwrites). Returns the archived path."""
    os.makedirs(sources_dir, exist_ok=True)
    ext = os.path.splitext(audio_path)[1]
    target = os.path.join(sources_dir, f"{base_name}{ext}")
    n = 2
    while os.path.exists(target):
        target = os.path.join(sources_dir, f"{base_name}-{n}{ext}")
        n += 1
    if move:
        shutil.move(audio_path, target)
    else:
        shutil.copy2(audio_path, target)
    return target
