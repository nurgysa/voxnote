"""Source-text checks for the «Архив аудио» (sources_dir) section in Settings."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = _ROOT / "ui" / "dialogs" / "settings.py"
BUILDER_PATH = _ROOT / "ui" / "dialogs" / "settings_builder.py"


def test_sources_section_card_exists():
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert "def build_sources_section" in src
    assert '"Архив аудио"' in src or "'Архив аудио'" in src


def test_sources_section_wired_into_tab():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "build_sources_section" in src


def test_sources_handlers_write_config_key():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    for name in ("_on_pick_sources_folder", "_on_clear_sources_folder"):
        assert f"def {name}" in src
    # Both handlers must reference the key — guards against a typo in one
    # (e.g. "source_dir") that a single bare `in` check would miss.
    assert src.count('"sources_dir"') >= 2
    assert "askdirectory" in src  # native folder picker


def test_meetings_section_still_present():
    # The existing Встречи section must survive untouched (regression guard).
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert '"Встречи"' in src or "'Встречи'" in src
