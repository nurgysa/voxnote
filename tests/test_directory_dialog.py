from pathlib import Path

SRC = Path("ui/dialogs/directory.py")


def test_dialog_file_exists():
    assert SRC.is_file(), "ui/dialogs/directory.py must exist"


def test_dialog_uses_tabview_with_both_tabs():
    src = SRC.read_text(encoding="utf-8")
    assert "CTkTabview" in src
    assert '"Люди"' in src
    assert '"Проекты"' in src


def test_dialog_is_backed_by_directory_store():
    src = SRC.read_text(encoding="utf-8")
    assert "DirectoryStore" in src
    assert "from directory.schema import" in src
    assert "Person" in src and "Project" in src


def test_dialog_persists_via_store_mutators():
    src = SRC.read_text(encoding="utf-8")
    # CRUD must go through the store, not ad-hoc file writes.
    assert "upsert_person" in src
    assert "upsert_project" in src
    assert "delete_person" in src
    assert "delete_project" in src


def test_dialog_releases_grab_on_close():
    src = SRC.read_text(encoding="utf-8")
    assert "grab_release" in src
