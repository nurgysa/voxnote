"""Settings "Сохранить лог для отправки" diagnostics button (WS-3 / D4).

Source/AST checks only — ui.dialogs.settings can't import on Linux CI
(sounddevice via the widget chain; see feedback_ui_app_import_breaks_linux_ci).
"""
from __future__ import annotations

import ast
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"


def _method_src(name: str) -> str | None:
    tree = ast.parse(SETTINGS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    return None


def test_diagnostics_section_is_built_in_backup_tab():
    from pathlib import Path
    builder_path = (
        Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings_builder.py"
    )
    builder_src = builder_path.read_text(encoding="utf-8")
    assert "def build_diagnostics_section(dialog, parent)" in builder_src
    dialog_src = SETTINGS.read_text(encoding="utf-8")
    assert "settings_builder.build_diagnostics_section(self, scroll_backup)" in dialog_src


def test_send_log_handler_builds_bundle_off_main_thread():
    src = _method_src("_handle_send_log")
    assert src is not None, "_handle_send_log must exist"
    assert "build_log_bundle" in src           # uses the tested helper
    assert "asksaveasfilename" in src          # user picks the destination
    assert "Thread" in src                     # zip off the Tk main thread


def test_send_log_handler_aborts_on_cancelled_dialog():
    # A cancelled save dialog (empty path) must short-circuit before any work.
    src = _method_src("_handle_send_log")
    assert src is not None
    assert "return" in src
