"""SettingsDialog uses the unified api_key_row helper for each API key section.

Source-text check — we cannot import ui.dialogs.settings (sounddevice
PortAudio issue on Linux CI). See feedback_ui_app_import_breaks_linux_ci.

After Task 1.2 the api_key_row call sites live in settings_builder.py.
Final target: 6 calls (Cloud STT + OpenRouter + Linear + Glide +
Trello key + Trello token).
"""
from __future__ import annotations

from pathlib import Path

BUILDER_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings_builder.py"
)


def test_settings_imports_api_key_row():
    source = BUILDER_PATH.read_text(encoding="utf-8")
    assert "api_key_row" in source, (
        "ui/dialogs/settings_builder.py must import api_key_row from ui.widgets"
    )


def test_settings_calls_api_key_row_at_least_six_times():
    """Cloud STT + OpenRouter + Linear + Glide + Trello (key + token) = 6
    api_key_row(...) call sites."""
    source = BUILDER_PATH.read_text(encoding="utf-8")
    n_calls = source.count("api_key_row(")
    assert n_calls >= 6, (
        f"Expected ≥ 6 api_key_row(...) calls (incl. Trello key + token), "
        f"got {n_calls}"
    )
