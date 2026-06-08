"""Path-confinement guard for untrusted file inputs (WS-5, audit P1).

cli._paths.ensure_outside_secret_store rejects any path that resolves into
the secret store (~/.audio-transcriber/, holding config.json + tokens) so a
model-supplied MCP `audio_path` (or a CLI --transcript/--tasks path) can't
exfiltrate credentials by having them transcribed/uploaded. Deny-list, not
an allowlist — every other location stays readable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cli._paths import ensure_outside_secret_store


def _fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".audio-transcriber").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_allows_normal_path(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    rec = home / "Documents" / "meeting.wav"
    rec.parent.mkdir()
    rec.write_bytes(b"x")
    assert ensure_outside_secret_store(str(rec)) == str(rec)


def test_rejects_file_inside_secret_store(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    target = home / ".audio-transcriber" / "config.json"
    with pytest.raises(ValueError):
        ensure_outside_secret_store(str(target))


def test_rejects_secret_store_dir_itself(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        ensure_outside_secret_store(str(home / ".audio-transcriber"))


def test_rejects_dotdot_traversal_into_secret_store(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    sneaky = home / ".audio-transcriber" / ".." / ".audio-transcriber" / "gdrive-token.json"
    with pytest.raises(ValueError):
        ensure_outside_secret_store(str(sneaky))


def test_allows_sibling_directory_of_secret_store(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    ok = home / ".audio-transcriber-public" / "note.txt"
    ok.parent.mkdir()
    ok.write_text("hi", encoding="utf-8")
    # A sibling whose name merely starts with the secret dir's name is NOT
    # inside it — containment is by resolved parent, not string prefix.
    assert ensure_outside_secret_store(str(ok)) == str(ok)


def test_run_transcribe_rejects_secret_store_path(tmp_path, monkeypatch):
    # The guard fires at the shared core.run_transcribe chokepoint (covers
    # both the MCP transcribe_audio tool and the CLI), before any provider
    # call — so a secret-store path raises ValueError without transcribing.
    home = _fake_home(tmp_path, monkeypatch)
    from cli import core

    target = str(home / ".audio-transcriber" / "config.json")
    with pytest.raises(ValueError):
        core.run_transcribe(target, provider="AssemblyAI", api_key="k")
