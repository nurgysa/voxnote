"""Selection logic for the one-time recordings move script.

Loads the script by path (it lives in scripts/, not a package). The script
imports utils (CI-safe — no native deps), so this import is safe on CI.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

_PATH = pathlib.Path("scripts/move_recordings.py")
_spec = importlib.util.spec_from_file_location("move_recordings", _PATH)
move_recordings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(move_recordings)


def test_selects_only_root_recording_wavs(tmp_path):
    docs = tmp_path / "Documents"
    (docs / "sub").mkdir(parents=True)
    (docs / "recording_2026-01-01_10-00-00.wav").write_bytes(b"x")
    (docs / "recording_2026-01-02_11-00-00.wav").write_bytes(b"x")
    (docs / "notes.wav").write_bytes(b"x")               # not a recording
    (docs / "report.docx").write_bytes(b"x")             # not a wav
    (docs / "sub" / "recording_nested.wav").write_bytes(b"x")  # nested -> skip

    found = move_recordings._select_root_recordings(str(docs))
    names = sorted(os.path.basename(p) for p in found)
    assert names == [
        "recording_2026-01-01_10-00-00.wav",
        "recording_2026-01-02_11-00-00.wav",
    ]
