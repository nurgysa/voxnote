"""Folder stats must be computed off the Tk thread (spec 2026-06-11, PR-2).

Window-sliced source checks — settings.py imports CTk, which Linux CI
cannot import (no PortAudio via the ui package chain).
"""
from pathlib import Path

SETTINGS = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")


def _stats_block() -> str:
    """Body of _refresh_meetings_stats (up to the next method def)."""
    start = SETTINGS.index("def _refresh_meetings_stats")
    end = SETTINGS.index("\n    def ", start + 1)
    return SETTINGS[start:end]


def test_stats_computed_in_worker_thread():
    block = _stats_block()
    assert "threading.Thread" in block, "size walk must run off the Tk thread"
    assert "daemon=True" in block


def test_stats_placeholder_shown_immediately():
    # The label must show feedback synchronously while the walk runs.
    assert "Подсчёт" in _stats_block()


def test_stats_guarded_by_generation_counter():
    # A stale walk result (user switched folders mid-scan) must be dropped.
    assert "_stats_gen" in _stats_block()


def test_stats_apply_guarded_against_dead_dialog():
    # self.after() from the worker raises TclError if the dialog is gone.
    # The guard lives in the shared _post_to_ui helper (its TclError catch
    # is locked by test_settings_worker_ui_guards.py).
    assert "_post_to_ui" in _stats_block()
