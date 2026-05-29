"""SettingsDialog uses the unified api_key_row helper for each API key section.

Source-text check — we cannot import ui.dialogs.settings (sounddevice
PortAudio issue on Linux CI). See feedback_ui_app_import_breaks_linux_ci.

This assertion is BUMPED across Tasks 2-5 of the plan as each section is
migrated. Final target after Task 11: 6 calls (Cloud STT + OpenRouter +
Linear + Glide + Trello key + Trello token).
"""
from __future__ import annotations

from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)


def test_settings_imports_api_key_row():
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "api_key_row" in source, (
        "ui/dialogs/settings.py must import api_key_row from ui.widgets"
    )


def test_settings_calls_api_key_row_at_least_six_times():
    """Cloud STT + OpenRouter + Linear + Glide + Trello (key + token) = 6
    api_key_row(...) call sites."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    n_calls = source.count("api_key_row(")
    assert n_calls >= 6, (
        f"Expected ≥ 6 api_key_row(...) calls (incl. Trello key + token), "
        f"got {n_calls}"
    )
