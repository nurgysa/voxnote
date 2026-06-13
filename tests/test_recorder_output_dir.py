"""recorder.start writes to the given output_dir (creating it).

recorder.py imports sounddevice, and audio_io imports soundfile — both load
native libs absent on Linux CI. Inject MagicMocks BEFORE importing recorder
so the test runs headless. We assert on the directory + path logic only (the
mocked SoundFile writes no real file).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sounddevice", MagicMock())
sys.modules.setdefault("soundfile", MagicMock())

from recorder import Recorder  # noqa: E402


def test_start_creates_and_uses_output_dir(tmp_path):
    target = tmp_path / "vault" / "recordings"   # parent dirs do NOT exist yet
    r = Recorder()
    path = r.start(output_dir=str(target))
    try:
        assert os.path.isdir(str(target))                  # makedirs created it
        assert path.startswith(str(target))
        assert os.path.basename(path).startswith("recording_")
        assert path.endswith(".wav")
    finally:
        r.stop()


def test_default_output_dir_is_not_documents_root():
    r = Recorder()
    expected = os.path.join(
        os.path.expanduser("~"), "Documents", "VoxNote", "recordings",
    )
    assert r._output_dir == expected
