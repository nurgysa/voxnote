"""Tests for transcript.md (new) + .txt (back-compat) handling.

User-requested change 2026-05-28: new transcripts are written with
.md extension so they render natively in Obsidian / markdown viewers.
Existing transcript.txt files in older meeting folders remain readable
via fallback logic — read prefers .md, falls back to .txt.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UTILS_PATH = REPO / "utils.py"
MEETINGS_PATH = REPO / "ui" / "dialogs" / "meetings.py"
SAVE_MIXIN_PATH = REPO / "ui" / "app" / "save_mixin.py"


def test_utils_writes_transcript_md():
    """utils.create_history_entry writes transcript.md (new convention)."""
    src = UTILS_PATH.read_text(encoding="utf-8")
    assert "transcript.md" in src, (
        "utils.create_history_entry must write transcript.md as the new "
        "primary transcript file"
    )


def test_utils_lists_both_md_and_txt():
    """list_history_entries detects has_transcript for either .md or .txt."""
    src = UTILS_PATH.read_text(encoding="utf-8")
    assert "transcript.md" in src
    assert "transcript.txt" in src, (
        "Back-compat .txt detection must remain so older meeting folders "
        "with transcript.txt still show as having a transcript"
    )


def test_meetings_reads_md_with_txt_fallback():
    """_read_transcript tries transcript.md first, then transcript.txt."""
    src = MEETINGS_PATH.read_text(encoding="utf-8")
    assert "transcript.md" in src, (
        "Meetings dialog read path must try transcript.md first"
    )
    assert "transcript.txt" in src, (
        "Meetings dialog must fall back to transcript.txt for back-compat"
    )


def test_meetings_save_as_initial_is_md():
    """Save-as dialog defaults to transcript.md."""
    src = MEETINGS_PATH.read_text(encoding="utf-8")
    # filedialog.asksaveasfilename(initialfile="transcript.md", ...)
    assert 'initialfile="transcript.md"' in src or \
           "initialfile='transcript.md'" in src, (
        "MeetingViewerDialog save-as must default initialfile to transcript.md"
    )


def test_save_mixin_default_uses_md():
    """save_mixin's fallback transcript name should be .md."""
    src = SAVE_MIXIN_PATH.read_text(encoding="utf-8")
    assert "transcript.md" in src, (
        "save_mixin.py fallback save-as name must be transcript.md"
    )
