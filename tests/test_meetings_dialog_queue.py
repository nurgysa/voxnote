"""Source-slice wiring tests for the PR-C2 «Встречи» queue+history rework.

No ui.app/Tk import — customtkinter pulls PortAudio and crashes Linux CI.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MEET = (_ROOT / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
_MIXIN = (_ROOT / "ui" / "app" / "dialogs_mixin.py").read_text(encoding="utf-8")


def test_meetings_uses_build_view_and_snapshot():
    assert "build_view(" in _MEET
    assert "_queue.snapshot()" in _MEET
    assert "list_history_entries" not in _MEET  # old data source replaced


def test_meetings_imports_pure_view_helpers():
    assert "from ui.dialogs.meetings_view import" in _MEET
    for name in ("format_status", "group_by_project", "queue_position"):
        assert name in _MEET


def test_meetings_retry_wired_to_queue():
    assert "_queue.retry(" in _MEET
    assert "Повторить" in _MEET


def test_meetings_open_obsidian_uses_default_md_app():
    assert "_open_obsidian" in _MEET
    assert "startfile" in _MEET


def test_meetings_live_poll_with_cancel():
    assert ".after(" in _MEET
    assert "after_cancel" in _MEET
    assert "except tk.TclError" in _MEET  # post-destroy poll guard


def test_meetings_dialog_takes_queue():
    assert "def __init__(self, parent, on_load_to_main, queue)" in _MEET


def test_mixin_passes_queue_to_meetings_dialog():
    assert "MeetingsDialog(" in _MIXIN
    assert "queue=self._queue" in _MIXIN


def test_meetings_preserves_legacy_pinned_strings():
    # Guards the strings that test_meetings_dialog_rename / _transcript_md_extension pin.
    assert '"Встречи"' in _MEET
    assert "Встреч:" in _MEET
    assert "Нет встреч" in _MEET
    assert "class MeetingsDialog" in _MEET
    assert "class MeetingViewerDialog" in _MEET
    assert "transcript.md" in _MEET and "transcript.txt" in _MEET
    assert 'initialfile="transcript.md"' in _MEET


def test_meetings_delete_forgets_queue_item():
    # Deleting a meeting must also evict its lingering active item (no ghost row).
    assert "_queue.forget(" in _MEET


def test_meetings_dismiss_error_wired_to_forget():
    # A stuck ERROR item can be cleared from the queue, distinct from retry.
    assert "def _dismiss" in _MEET
    assert "✕ Убрать" in _MEET
    assert "_dismiss(it)" in _MEET  # ERROR-row button wired to the dismiss handler
