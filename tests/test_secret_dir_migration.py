"""migrate_legacy_secret_dir(): one-time move of the pre-VoxNote secret store.

Redirect HOME/USERPROFILE to a tmp dir so os.path.expanduser("~") resolves
there on both POSIX and Windows.
"""
import os

import utils


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_migrates_when_old_exists_and_new_absent(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    old = home / ".audio-transcriber"
    old.mkdir()
    (old / "config.json").write_text("{\"k\": 1}", encoding="utf-8")

    utils.migrate_legacy_secret_dir()

    new = home / ".voxnote"
    assert new.is_dir()
    assert (new / "config.json").read_text(encoding="utf-8") == "{\"k\": 1}"
    assert not old.exists()


def test_noop_when_new_already_exists(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    old = home / ".audio-transcriber"
    old.mkdir()
    (old / "x").write_text("old", encoding="utf-8")
    new = home / ".voxnote"
    new.mkdir()
    (new / "x").write_text("new", encoding="utf-8")

    utils.migrate_legacy_secret_dir()

    assert (new / "x").read_text(encoding="utf-8") == "new"  # untouched
    assert old.exists()  # left alone


def test_noop_when_neither_exists(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    utils.migrate_legacy_secret_dir()
    assert not (home / ".voxnote").exists()


def test_move_failure_is_swallowed(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    (home / ".audio-transcriber").mkdir()

    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(utils.shutil, "move", boom)
    utils.migrate_legacy_secret_dir()  # must NOT raise
    assert not (home / ".voxnote").exists()
