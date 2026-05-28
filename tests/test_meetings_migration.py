"""Unit tests for meetings_migration — pure Python, real I/O on tempdirs.

No Tk imports, so this file runs cleanly on Linux CI (unlike anything
that touches ui.app — see feedback_ui_app_import_breaks_linux_ci).
"""
from __future__ import annotations

import os
import tempfile
import threading

from meetings_migration import (
    count_meetings,
    detect_old_locations,
    migrate_meetings,
)


def _make_meeting(parent: str, name: str, files: dict[str, bytes]) -> str:
    """Create a fake meeting folder with the given files inside."""
    folder = os.path.join(parent, name)
    os.makedirs(folder)
    for fname, content in files.items():
        with open(os.path.join(folder, fname), "wb") as f:
            f.write(content)
    return folder


# ── migrate_meetings ───────────────────────────────────────────────────


def test_migrate_empty_src():
    """Empty src directory → returns moved=[], no errors."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        result = migrate_meetings(src, dst, lambda *a: None, threading.Event())
        assert result["moved"] == []
        assert result["errors"] == []
        assert result["cancelled"] is False


def test_migrate_single_meeting():
    """One meeting folder moves with all files intact, src directory left empty."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        _make_meeting(src, "2026-01-01_meeting", {
            "transcript.txt": b"hello",
            "description.md": b"# meta",
            "audio.mp3": b"\x00" * 1000,
        })
        result = migrate_meetings(src, dst, lambda *a: None, threading.Event())
        assert "2026-01-01_meeting" in result["moved"]
        # Files moved to dst
        assert os.path.isfile(os.path.join(dst, "2026-01-01_meeting", "transcript.txt"))
        assert os.path.isfile(os.path.join(dst, "2026-01-01_meeting", "audio.mp3"))
        # src folder gone
        assert not os.path.exists(os.path.join(src, "2026-01-01_meeting"))


def test_migrate_multiple_meetings():
    """All subfolders moved; total count preserved."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        for i in range(3):
            _make_meeting(src, f"m{i}", {"transcript.txt": b""})
        result = migrate_meetings(src, dst, lambda *a: None, threading.Event())
        assert sorted(result["moved"]) == ["m0", "m1", "m2"]
        assert len(os.listdir(dst)) == 3
        assert len(os.listdir(src)) == 0


def test_migrate_collision_appends_timestamp():
    """If dst has a same-named folder, the new one gets `_imported_<HHMMSS>`."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        _make_meeting(src, "dup", {"a.txt": b"new"})
        _make_meeting(dst, "dup", {"a.txt": b"old"})
        migrate_meetings(src, dst, lambda *a: None, threading.Event())
        # Original "dup" in dst untouched
        with open(os.path.join(dst, "dup", "a.txt"), "rb") as f:
            assert f.read() == b"old"
        # New entry under _imported_<HHMMSS> suffix
        suffixed = [d for d in os.listdir(dst) if d.startswith("dup_imported_")]
        assert len(suffixed) == 1
        # The migrated content is in the suffixed copy
        with open(os.path.join(dst, suffixed[0], "a.txt"), "rb") as f:
            assert f.read() == b"new"


def test_migrate_progress_called():
    """on_progress fires twice per folder (start + done)."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        _make_meeting(src, "a", {"x": b""})
        _make_meeting(src, "b", {"x": b""})
        calls = []
        migrate_meetings(
            src, dst,
            lambda *args: calls.append(args),
            threading.Event(),
        )
        # 2 folders × 2 calls (start + done) = 4 progress events
        assert len(calls) == 4
        # First call signals (0, 2, name) — start of first folder
        assert calls[0][0] == 0 and calls[0][1] == 2


def test_migrate_cancel_mid_flight():
    """Cancel between folders → remaining stay in src, total count preserved."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        for i in range(5):
            _make_meeting(src, f"m{i}", {"x": b""})

        cancel = threading.Event()

        def progress(done, total, name):
            # Set cancel after 2 folders finished
            if done == 2:
                cancel.set()

        result = migrate_meetings(src, dst, progress, cancel)
        assert result["cancelled"] is True
        assert len(result["moved"]) <= 5
        # Invariant: every meeting still accounted for somewhere
        assert len(os.listdir(src)) + len(os.listdir(dst)) == 5


# ── detect_old_locations ───────────────────────────────────────────────


def test_detect_old_locations_empty_returns_nothing():
    """No legacy paths exist → empty list."""
    result = detect_old_locations(probe_paths=["/nonexistent/probe/path"])
    assert result == []


def test_detect_old_locations_finds_populated():
    """Legacy path with entries → reported with count."""
    with tempfile.TemporaryDirectory() as old:
        _make_meeting(old, "m1", {"transcript.txt": b""})
        _make_meeting(old, "m2", {"transcript.txt": b""})
        result = detect_old_locations(probe_paths=[old])
        assert len(result) == 1
        assert result[0] == (old, 2)


# ── count_meetings ─────────────────────────────────────────────────────


def test_count_meetings_excludes_non_meeting_dirs():
    """Loose files at top level are ignored; only subdirectories count."""
    with tempfile.TemporaryDirectory() as d:
        _make_meeting(d, "real_meeting", {"transcript.txt": b""})
        # Loose file — not a folder
        with open(os.path.join(d, "stray.txt"), "w") as f:
            f.write("noise")
        assert count_meetings(d) == 1
