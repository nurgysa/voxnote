from pathlib import Path

SRC = Path(__file__).parent.parent / "ui/dialogs/extract_tasks/__init__.py"


def test_dialog_loads_directory_store():
    src = SRC.read_text(encoding="utf-8")
    assert "from directory.store import" in src
    assert "DirectoryStore()" in src


def test_dialog_builds_context_section():
    src = SRC.read_text(encoding="utf-8")
    assert "Контекст встречи" in src
    assert "_context_project_var" in src
    assert "_context_person_vars" in src


def test_dialog_uses_default_participants():
    src = SRC.read_text(encoding="utf-8")
    assert "default_participants" in src


def test_dialog_restores_selection_from_speakers_json():
    src = SRC.read_text(encoding="utf-8")
    assert "load_speakers" in src


def test_run_extraction_passes_context_to_both_calls():
    src = SRC.read_text(encoding="utf-8")
    # render once, thread into extract() and generate()
    assert "render_meeting_context(" in src
    assert src.count("context=meeting_context") >= 2


def test_protocol_speakers_uses_real_names():
    src = SRC.read_text(encoding="utf-8")
    assert "speakers=[p.full_name for p in people]" in src
    assert "speakers=[],  # cloud-only build has no voice library" not in src


def test_run_extraction_persists_speakers_json():
    src = SRC.read_text(encoding="utf-8")
    assert "save_speakers(" in src
