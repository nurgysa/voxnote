"""utils config-path resolution + first-run seed + save-dir creation.

Frozen (.exe) stores config at ~/.audio-transcriber/config.json (outside the
bundle, survives updates); dev/source uses repo-root config.json. Monkeypatch
only — never imports ui.app (sounddevice/PortAudio is absent on Linux CI).
"""
from __future__ import annotations

import json
import os

import utils


def test_default_config_path_source_mode():
    # Tests run unfrozen → repo-root config.json beside utils.py.
    assert getattr(__import__("sys"), "frozen", False) is False
    expected = os.path.join(os.path.dirname(os.path.abspath(utils.__file__)), "config.json")
    assert utils._default_config_path() == expected


def test_default_config_path_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)
    monkeypatch.setattr(utils.os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    assert utils._default_config_path() == os.path.join(
        str(tmp_path), ".audio-transcriber", "config.json",
    )


def test_save_config_creates_missing_parent_dir(monkeypatch, tmp_path):
    target = tmp_path / "made" / "up" / "config.json"   # parent dirs absent
    monkeypatch.setattr(utils, "_CONFIG_PATH", str(target))
    utils.save_config({"cloud_provider": "AssemblyAI"})
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == {"cloud_provider": "AssemblyAI"}
