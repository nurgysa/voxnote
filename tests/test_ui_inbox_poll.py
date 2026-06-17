"""Source-slice wiring tests for the PR-C3 inbox poll.

No ui.app/Tk import — customtkinter pulls PortAudio and crashes Linux CI.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INIT = (_ROOT / "ui" / "app" / "__init__.py").read_text(encoding="utf-8")
_QUEUE = (_ROOT / "ui" / "app" / "queue_mixin.py").read_text(encoding="utf-8")


def test_init_builds_inbox_watcher_and_schedules_tick():
    assert "from processing.inbox_watcher import InboxWatcher" in _INIT
    assert "InboxWatcher(" in _INIT
    assert "self.after(self._INBOX_POLL_MS, self._inbox_tick)" in _INIT


def test_queue_mixin_has_inbox_tick_and_interval():
    assert "def _inbox_tick" in _QUEUE
    assert "_INBOX_POLL_MS" in _QUEUE


def test_inbox_tick_polls_and_rebuilds_on_change():
    assert "self._inbox_watcher.poll()" in _QUEUE
    assert "InboxWatcher(" in _QUEUE
    assert 'self._config.get("inbox_dir")' in _QUEUE


def test_inbox_tick_dedups_against_snapshot():
    assert "self._queue.snapshot()" in _QUEUE
    assert "audio_path" in _QUEUE


def test_inbox_enqueue_is_no_project_inbox_source():
    assert '_build_options("inbox")' in _QUEUE
    assert 'options["project_id"] = None' in _QUEUE


def test_inbox_tick_reschedules_and_guards_teardown():
    assert "except tk.TclError" in _QUEUE
    assert "self.after(self._INBOX_POLL_MS, self._inbox_tick)" in _QUEUE


def test_on_app_close_cancels_inbox_tick():
    assert "after_cancel(self._inbox_after_id)" in _QUEUE
