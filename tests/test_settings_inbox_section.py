"""Source-text checks for the «Приём с телефона» (inbox_dir) section in Settings."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = _ROOT / "ui" / "dialogs" / "settings.py"
BUILDER_PATH = _ROOT / "ui" / "dialogs" / "settings_builder.py"


def test_inbox_section_card_exists():
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert "def build_inbox_section" in src
    assert '"Приём с телефона"' in src or "'Приём с телефона'" in src


def test_inbox_section_wired_into_tab():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "build_inbox_section" in src


def test_inbox_handlers_write_config_key():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    for name in ("_on_pick_inbox_folder", "_on_clear_inbox_folder"):
        assert f"def {name}" in src
    assert '"inbox_dir"' in src
    assert "askdirectory" in src


def test_sources_section_still_present():
    # «Архив аудио» (PR-C1b) must survive untouched (regression guard).
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert '"Архив аудио"' in src
