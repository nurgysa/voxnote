"""Tests for utils.restrict_dir_to_owner — WS-5 P2 owner-only secret-store ACL.

``~/.audio-transcriber`` holds config.json (API keys) + gdrive-token.json. The
codebase's ``os.chmod(..., 0o600)`` is a *silent no-op on Windows*, so the dir
was left at default ACLs. This locks it owner-only: POSIX ``chmod 0o700``;
Windows ``icacls`` owner-only. Best-effort — never raises.

Linux-CI-safe: the POSIX branch is tested with a real chmod (skipped on
Windows, where mode bits are ~meaningless); the Windows branch is tested by
asserting the icacls *command* via a mocked ``subprocess.run`` (runs anywhere).
"""
from __future__ import annotations

import os
import stat
from unittest.mock import patch

import pytest

import utils

# ── POSIX branch: real chmod ────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod semantics; Windows uses icacls")
def test_posix_sets_0700(tmp_path):
    d = tmp_path / "secret"
    d.mkdir()
    assert utils.restrict_dir_to_owner(str(d)) is True
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only failure path")
def test_posix_missing_path_returns_false_not_raise(tmp_path):
    assert utils.restrict_dir_to_owner(str(tmp_path / "nope")) is False


# ── Windows branch: assert the icacls command (mocked subprocess) ────────

def test_windows_builds_owner_only_icacls_command():
    captured = {}

    class _Result:
        returncode = 0
        stderr = b""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    with patch.object(utils.os, "name", "nt"), \
         patch.dict(utils.os.environ, {"USERNAME": "alice"}), \
         patch.object(utils.subprocess, "run", _fake_run):
        ok = utils.restrict_dir_to_owner(r"C:\Users\alice\.audio-transcriber")

    assert ok is True
    cmd = captured["cmd"]
    assert cmd[0] == "icacls"
    assert r"C:\Users\alice\.audio-transcriber" in cmd
    assert "/inheritance:r" in cmd  # drop inherited ACEs (owner-only)
    # current user granted Full with object+container inheritance
    assert any("alice" in part and "(OI)(CI)F" in part for part in cmd), cmd
    # MUST NOT pass /T: applying (OI)(CI) to existing FILES corrupts their DACL
    # (empty -> owner locked out of config.json). Verified by a real-icacls smoke;
    # a dir-only grant re-propagates owner-only inheritance to existing children.
    assert "/T" not in cmd, "icacls /T corrupts existing file ACLs (WS-5 P2 smoke)"


def test_windows_icacls_nonzero_returns_false_not_raise():
    class _Result:
        returncode = 1
        stderr = b"icacls: access denied"

    with patch.object(utils.os, "name", "nt"), \
         patch.object(utils.subprocess, "run", lambda *a, **k: _Result()):
        assert utils.restrict_dir_to_owner(r"C:\x") is False


def test_windows_icacls_oserror_returns_false_not_raise():
    def _boom(*a, **k):
        raise OSError("icacls not found on PATH")

    with patch.object(utils.os, "name", "nt"), \
         patch.object(utils.subprocess, "run", _boom):
        assert utils.restrict_dir_to_owner(r"C:\x") is False


# ── Wiring: save_config hardens the secret dir only in frozen mode ───────

def test_save_config_hardens_secret_dir_when_frozen(tmp_path, monkeypatch):
    cfg = tmp_path / ".audio-transcriber" / "config.json"
    monkeypatch.setattr(utils, "_CONFIG_PATH", str(cfg))
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)
    seen = {}
    monkeypatch.setattr(
        utils, "restrict_dir_to_owner",
        lambda p: seen.setdefault("path", p) or True, raising=False,
    )
    utils.save_config({"k": "v"})
    assert seen.get("path") == str(cfg.parent)  # the secret dir was hardened
    assert cfg.exists()  # config still written


def test_save_config_skips_hardening_when_not_frozen(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"  # dev: repo-root-like, NOT the secret dir
    monkeypatch.setattr(utils, "_CONFIG_PATH", str(cfg))
    monkeypatch.setattr(utils.sys, "frozen", False, raising=False)
    seen = {}
    monkeypatch.setattr(
        utils, "restrict_dir_to_owner",
        lambda p: seen.setdefault("path", p), raising=False,
    )
    utils.save_config({"k": "v"})
    assert "path" not in seen  # dev mode must NOT lock the repo root
    assert cfg.exists()
