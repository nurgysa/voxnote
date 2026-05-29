"""Source-text checks for the Settings Trello section (two-field auth).

Cannot import ui.dialogs.settings (sounddevice on Linux CI). Scan source.
"""
from __future__ import annotations

from pathlib import Path

_SETTINGS = Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"


def test_trello_section_defined_and_called():
    src = _SETTINGS.read_text(encoding="utf-8")
    assert "def _build_trello_section" in src
    assert "self._build_trello_section(scroll_integrations)" in src


def test_trello_section_binds_both_credential_vars():
    src = _SETTINGS.read_text(encoding="utf-8")
    assert "_trello_key_var" in src
    assert "_trello_token_var" in src
    assert "_trello_enabled_var" in src


def test_trello_section_validates_via_trello_client():
    src = _SETTINGS.read_text(encoding="utf-8")
    assert "from tasks.trello_client import TrelloClient" in src
