from pathlib import Path


def test_run_loop_persists_segments_after_history_entry():
    src = Path("ui/app/transcription_mixin.py").read_text(encoding="utf-8")
    assert "save_segments(self._last_history_folder" in src
    assert "self._transcriber.last_segments" in src


def test_run_loop_imports_save_segments():
    src = Path("ui/app/transcription_mixin.py").read_text(encoding="utf-8")
    import_lines = [ln for ln in src.splitlines() if ln.startswith("from utils import")]
    assert import_lines, "expected a 'from utils import' line"
    assert any("save_segments" in ln for ln in import_lines)
