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
