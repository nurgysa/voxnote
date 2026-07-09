# processing/sources.py
"""Archive audio originals into the Google Drive Sources tree.

A plain filesystem write — Google Drive Desktop syncs it; no gdrive API. The
meeting's transcript.md records where the archived audio lives. VoxNote audio is
kept under a dedicated, understandable hierarchy instead of dumping files into
``Sources`` root::

    Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/<base><ext>

``move=True`` for in-app recordings and inbox files (ours to relocate; this is
what drains the inbox); ``move=False`` (copy) for user-picked files outside the
Sources tree. If a picked file is already a loose file in ``Sources`` root, it is
rehomed instead of copied so the root stays clean.
"""
from __future__ import annotations

import os
import re
import shutil

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _meeting_archive_dir(sources_dir: str, base_name: str) -> str:
    """Return the organized VoxNote meeting-audio archive directory."""
    match = _DATE_PREFIX.match(base_name)
    date_dir = match.group(0) if match else "undated"
    return os.path.join(sources_dir, "Audio", "VoxNote", "Meetings", date_dir)


def _same_or_under(path: str, directory: str) -> bool:
    """True when ``path`` is ``directory`` or a descendant of it."""
    try:
        path_abs = os.path.normcase(os.path.abspath(path))
        dir_abs = os.path.normcase(os.path.abspath(directory))
        return os.path.commonpath([path_abs, dir_abs]) == dir_abs
    except (OSError, ValueError):
        return False


def _is_loose_sources_root_file(audio_path: str, sources_dir: str) -> bool:
    """True when ``audio_path`` is a direct file child of ``sources_dir`` root."""
    try:
        return os.path.dirname(os.path.abspath(audio_path)) == os.path.abspath(sources_dir)
    except OSError:
        return False


def _is_already_in_meeting_archive(audio_path: str, sources_dir: str) -> bool:
    """True when ``audio_path`` already lives in the organized VoxNote archive."""
    archive_root = os.path.join(sources_dir, "Audio", "VoxNote", "Meetings")
    return _same_or_under(audio_path, archive_root)


def archive_audio(
    audio_path: str, sources_dir: str, base_name: str, *, move: bool
) -> str:
    """Place ``audio_path`` under ``Sources/Audio/VoxNote/Meetings/<date>/``.

    The filename remains ``<base_name><ext>`` and collisions are safe
    (``-2``, ``-3`` … never overwrites). Returns the archived path. If the file
    already lives in the organized archive, return it unchanged rather than
    duplicating or renaming it.
    """
    if _is_already_in_meeting_archive(audio_path, sources_dir):
        return audio_path

    dest_dir = _meeting_archive_dir(sources_dir, base_name)
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(audio_path)[1]
    target = os.path.join(dest_dir, f"{base_name}{ext}")
    n = 2
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{base_name}-{n}{ext}")
        n += 1
    should_move = move or _is_loose_sources_root_file(audio_path, sources_dir)
    if should_move:
        shutil.move(audio_path, target)
    else:
        shutil.copy2(audio_path, target)
    return target
