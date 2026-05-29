"""Source-text checks: App declares the Trello Vars + enabled handler.

Importing ui.app loads sounddevice (PortAudio) which crashes Linux CI, so
we scan the source text instead of instantiating the App.
See [[feedback_ui_app_import_breaks_linux_ci]].
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BUILDER = _ROOT / "ui" / "app" / "builder.py"
_SETTINGS_MIXIN = _ROOT / "ui" / "app" / "settings_mixin.py"


def test_builder_declares_trello_vars():
    src = _BUILDER.read_text(encoding="utf-8")
    assert "_trello_key_var" in src
    assert "_trello_token_var" in src
    assert "_trello_enabled_var" in src


def test_trello_enabled_var_defaults_false():
    """Opt-in (D5): the BooleanVar default reads trello_enabled with False fallback."""
    src = _BUILDER.read_text(encoding="utf-8")
    assert 'app._config.get("trello_enabled", False)' in src


def test_settings_mixin_has_trello_enabled_handler():
    src = _SETTINGS_MIXIN.read_text(encoding="utf-8")
    assert "_on_trello_enabled_changed" in src
    assert '"trello_enabled"' in src
