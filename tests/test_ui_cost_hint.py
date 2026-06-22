"""Source-slice wiring test for the at-enqueue cost hint.

No ui.app import — sounddevice → PortAudio crashes Linux CI.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_QUEUE_MIXIN = (_ROOT / "ui" / "app" / "queue_mixin.py").read_text(encoding="utf-8")


def test_enqueue_shows_cost_hint():
    assert "from processing import preflight" in _QUEUE_MIXIN
    assert "preflight.probe(" in _QUEUE_MIXIN
    assert "cost_hint_suffix(" in _QUEUE_MIXIN
    # the suffix is interpolated into the «Добавлено в очередь» status line
    assert "{hint}" in _QUEUE_MIXIN


def test_enqueue_cost_hint_probes_off_the_tk_thread():
    # Probing an .m4a shells out to ffmpeg (can stall on a Drive placeholder);
    # it must not block the Tk thread, so the probe runs on a daemon thread and
    # the label is updated via after(0, ...).
    assert "import threading" in _QUEUE_MIXIN
    assert "threading.Thread" in _QUEUE_MIXIN
    assert "daemon=True" in _QUEUE_MIXIN
