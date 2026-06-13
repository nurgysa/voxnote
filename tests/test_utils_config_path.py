"""utils config-path resolution + first-run seed + save-dir creation.

Frozen (.exe) stores config at ~/.voxnote/config.json (outside the
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
        str(tmp_path), ".voxnote", "config.json",
    )


def test_save_config_creates_missing_parent_dir(monkeypatch, tmp_path):
    target = tmp_path / "made" / "up" / "config.json"   # parent dirs absent
    monkeypatch.setattr(utils, "_CONFIG_PATH", str(target))
    utils.save_config({"cloud_provider": "AssemblyAI"})
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == {"cloud_provider": "AssemblyAI"}


def test_load_config_seeds_template_when_frozen_and_missing(monkeypatch, tmp_path):
    # Simulate a frozen bundle whose _MEIPASS holds the config.example.json template.
    meipass = tmp_path / "bundle"
    meipass.mkdir()
    (meipass / "config.example.json").write_text(
        json.dumps({"cloud_provider": "AssemblyAI", "cloud_api_keys": {}}),
        encoding="utf-8",
    )
    target = tmp_path / "home" / ".voxnote" / "config.json"   # absent
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)
    monkeypatch.setattr(utils.sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(utils, "_CONFIG_PATH", str(target))

    result = utils.load_config()

    assert target.is_file()                       # seeded the template to ~
    assert result["cloud_provider"] == "AssemblyAI"
    assert result["cloud_api_keys"] == {}         # empty keys → first-run banner fires


def test_load_config_does_not_seed_in_source_mode(monkeypatch, tmp_path):
    target = tmp_path / "config.json"             # absent; not frozen
    monkeypatch.setattr(utils, "_CONFIG_PATH", str(target))
    assert utils.load_config() == {}              # unchanged dev behavior
    assert not target.exists()                    # no seeding when unfrozen
