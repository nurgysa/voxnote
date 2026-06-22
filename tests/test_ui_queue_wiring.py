"""Source-slice wiring tests for the PR-C1 processing-queue integration.

No ui.app import — sounddevice/PortAudio would break Linux CI. Pattern matches
the (removed) test_ui_hermes_emit.py: read the module text and assert on it.
"""
from __future__ import annotations

from pathlib import Path

_INIT = Path("ui/app/__init__.py").read_text(encoding="utf-8")
_QUEUE = Path("ui/app/queue_mixin.py").read_text(encoding="utf-8")
_BUILDER = Path("ui/app/builder.py").read_text(encoding="utf-8")
_RECORDER = Path("ui/app/recorder_mixin.py").read_text(encoding="utf-8")
_SETTINGS = Path("ui/app/settings_mixin.py").read_text(encoding="utf-8")
_DIALOGS = Path("ui/app/dialogs_mixin.py").read_text(encoding="utf-8")


def test_transcription_mixin_removed():
    assert not Path("ui/app/transcription_mixin.py").exists()


def test_app_uses_queue_mixin_not_transcription_mixin():
    assert "from .queue_mixin import QueueMixin" in _INIT
    assert "QueueMixin" in _INIT
    assert "TranscriptionMixin" not in _INIT


def test_app_constructs_and_starts_queue():
    assert "ProcessingQueue(" in _INIT
    assert "self._queue.start()" in _INIT
    assert "WM_DELETE_WINDOW" in _INIT


def test_queue_mixin_has_enqueue_api():
    for name in (
        "_build_options", "_enqueue", "_on_queue_changed",
        "_refresh_queue_indicator", "_on_app_close",
    ):
        assert f"def {name}" in _QUEUE
    assert "self._queue.enqueue(" in _QUEUE


def test_transcribe_button_removed_indicator_added():
    assert "Транскрибировать" not in _BUILDER
    assert "_btn_transcribe" not in _BUILDER
    assert "_lbl_queue" in _BUILDER


def test_record_and_pick_enqueue():
    assert '_enqueue(path, "record")' in _RECORDER
    assert "_btn_transcribe" not in _RECORDER
    assert '_enqueue(path, "pick")' in _SETTINGS
    assert "_btn_transcribe" not in _SETTINGS


def test_dialogs_mixin_has_no_transcribe_button():
    assert "_btn_transcribe" not in _DIALOGS


def test_on_app_close_stops_queue():
    assert "self._queue.stop()" in _QUEUE
    assert "self.destroy()" in _QUEUE


def test_on_change_marshals_via_after():
    # on_change fires on the worker thread → must marshal to Tk via after(0).
    assert "after(0, self._on_queue_changed)" in _QUEUE
    assert "_safe_after_refresh" in _INIT


def test_save_mixin_has_no_dead_transcriber_ref():
    # PR-C1 removed self._transcriber from App.__init__; save_mixin must not
    # read it (would AttributeError on «Сохранить»). Source-slice guard —
    # the App can't be imported on Linux CI (sounddevice/PortAudio).
    src = Path("ui/app/save_mixin.py").read_text(encoding="utf-8")
    assert "_transcriber" not in src


def test_app_wires_resolve_participants_from_directory():
    assert "resolve_participants=" in _INIT
    assert "people_for_project(" in _INIT
