"""Source-text checks for the Встречи section in Settings."""
from __future__ import annotations

from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)
BUILDER_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings_builder.py"
)


def test_settings_has_meetings_section_card():
    """A section card titled «Встречи» exists in settings_builder.py."""
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert '"Встречи"' in src or "'Встречи'" in src, (
        "Settings builder must declare a section card with title «Встречи»"
    )
    assert "section_card" in src


def test_settings_uses_askdirectory_for_picker():
    """The folder picker uses tkinter.filedialog.askdirectory (handler stays in settings.py)."""
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "askdirectory" in src, (
        "Folder picker must use filedialog.askdirectory (Win32-native)"
    )


def test_settings_imports_get_meetings_dir():
    """Settings builder must use utils.get_meetings_dir to show current path."""
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert "get_meetings_dir" in src, (
        "Settings builder must import + use utils.get_meetings_dir to show current path"
    )


def test_settings_has_default_reset_button():
    """A button to reset meetings_dir to default (empty string) is present."""
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert "Default" in src, (
        "Settings builder must include a reset-to-default button for meetings_dir"
    )
