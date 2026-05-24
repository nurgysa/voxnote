"""Phase 7.0 smoke tests for the Settings dialog GDrive section.

Pure source-text checks — no Python imports of `ui.app` or
`ui.dialogs.settings`. Loading those packages triggers
`from recorder import Recorder` → `import sounddevice` → requires
PortAudio at the OS level, which the Linux CI runner doesn't have
(see tests/test_ui_constants.py header for the same gotcha).

We verify the new method/var names exist in the source files. This
is weaker than `hasattr(class, name)` (a renamed-but-misplaced
definition would slip through) but it's the strongest check we can
run portably without spinning up an X display.
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)


def _read(rel_path: str) -> str:
    with open(os.path.join(_REPO, rel_path), encoding="utf-8") as f:
        return f.read()


def test_settings_dialog_has_gdrive_section_builder():
    """SettingsDialog source must define _build_gdrive_section + register
    it in the constructor's section-build dispatch."""
    src = _read(os.path.join("ui", "dialogs", "settings.py"))
    assert "def _build_gdrive_section(self, parent)" in src
    assert "self._build_gdrive_section(body)" in src, (
        "Section method exists but is never called from __init__ dispatch"
    )


def test_settings_dialog_has_gdrive_handlers():
    """All 5 button/state handlers referenced by _build_gdrive_section
    must be defined as methods on SettingsDialog."""
    src = _read(os.path.join("ui", "dialogs", "settings.py"))
    for method in (
        "_handle_gdrive_signin",
        "_handle_gdrive_signout",
        "_on_gdrive_signin_success",
        "_on_gdrive_signin_failure",
        "_refresh_gdrive_button_state",
    ):
        assert f"def {method}(self" in src, f"Missing method definition: {method}"


def test_settings_mixin_has_gdrive_callbacks():
    """SettingsMixin source must expose the 3 Phase 7.0 callbacks
    the dialog button handlers call back into."""
    src = _read(os.path.join("ui", "app", "settings_mixin.py"))
    for method in (
        "_compute_gdrive_status_text",
        "_on_gdrive_signed_in",
        "_on_gdrive_signed_out",
    ):
        assert f"def {method}(self" in src, f"Missing SettingsMixin method: {method}"


def test_builder_creates_gdrive_vars():
    """ui/app/builder.py must construct the GDriveAuth instance + the
    three Vars the Settings dialog binds to."""
    src = _read(os.path.join("ui", "app", "builder.py"))
    for marker in (
        "from gdrive.auth import GDriveAuth",
        "app._gdrive_auth",
        "app._gdrive_status_var",
        "app._gdrive_account_email_var",
        "app._gdrive_enabled_var",
        "app._gdrive_auth.load_tokens()",
    ):
        assert marker in src, f"builder.py source missing {marker!r}"


def test_settings_dialog_has_backup_now_button_and_handlers():
    """Phase 7.1: 'Сделать backup сейчас' button + 3 handlers must
    exist in SettingsDialog source."""
    src = _read(os.path.join("ui", "dialogs", "settings.py"))
    assert "Сделать backup сейчас" in src, (
        "Button label literal missing — Russian UX string check"
    )
    for method in (
        "_handle_gdrive_backup_now",
        "_on_gdrive_backup_success",
        "_on_gdrive_backup_failure",
    ):
        assert f"def {method}(self" in src, f"Missing handler: {method}"


def test_settings_mixin_has_backup_success_callback():
    """Phase 7.1: _on_gdrive_backup_succeeded must exist on
    SettingsMixin (called by the dialog's success worker)."""
    src = _read(os.path.join("ui", "app", "settings_mixin.py"))
    assert "def _on_gdrive_backup_succeeded(" in src
    # Sanity: it must persist both keys.
    assert '"gdrive_root_folder_id"' in src
    assert '"gdrive_last_backup"' in src
