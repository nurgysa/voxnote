from pathlib import Path


def test_run_loop_persists_segments_after_history_entry():
    src = Path("ui/app/transcription_mixin.py").read_text(encoding="utf-8")
    assert "save_segments(self._last_history_folder" in src
    assert "self._transcriber.last_segments" in src


def test_run_loop_imports_save_segments():
    src = Path("ui/app/transcription_mixin.py").read_text(encoding="utf-8")
    # Tolerant of both single-line and parenthesized multiline `from utils import`.
    start = src.index("from utils import")
    if src[start:].startswith("from utils import ("):
        end = src.index(")", start)
    else:
        end = src.index("\n", start)
    import_block = src[start:end]
    assert "save_segments" in import_block
