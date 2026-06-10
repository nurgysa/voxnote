"""Source-text checks for the Settings Trello section (two-field auth).

Cannot import ui.dialogs.settings (sounddevice on Linux CI). Scan source.
After Task 1.2 the section body lives in settings_builder.py; the call
site in settings.py delegates to settings_builder.build_trello_section.
"""
from __future__ import annotations

from pathlib import Path

_SETTINGS = Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
_BUILDER = Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings_builder.py"


def test_trello_section_defined_and_called():
    builder_src = _BUILDER.read_text(encoding="utf-8")
    settings_src = _SETTINGS.read_text(encoding="utf-8")
    assert "def build_trello_section(" in builder_src
    assert "settings_builder.build_trello_section(self, scroll_integrations)" in settings_src


def test_trello_section_binds_both_credential_vars():
    src = _BUILDER.read_text(encoding="utf-8")
    assert "_trello_key_var" in src
    assert "_trello_token_var" in src
    assert "_trello_enabled_var" in src


def test_trello_section_validates_via_trello_client():
    src = _BUILDER.read_text(encoding="utf-8")
    assert "from tasks.trello_client import TrelloClient" in src
