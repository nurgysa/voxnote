"""api_key_row helper exists in ui/widgets.py with the required signature.

Source-text + AST checks — we cannot import ui.widgets directly because
sounddevice (transitively imported through ui.app -> recorder) loads
PortAudio at import time, which is absent on Linux CI runners. See
~/.claude/memory/feedback_ui_app_import_breaks_linux_ci.md.
"""
from __future__ import annotations

import ast
from pathlib import Path

WIDGETS_PATH = Path(__file__).resolve().parent.parent / "ui" / "widgets.py"


def _get_function_def(source: str, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_api_key_row_function_exists():
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None, "api_key_row not defined in ui/widgets.py"


def test_api_key_row_has_required_kwargs():
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None

    kwonly_names = {a.arg for a in fn.args.kwonlyargs}
    expected = {
        "label_text", "key_var", "placeholder",
        "on_validate", "on_key_persisted",
        "enabled_var", "enabled_label", "on_enabled_changed",
        "format_success", "row",
    }
    missing = expected - kwonly_names
    assert not missing, f"api_key_row missing kwargs: {sorted(missing)}"


def test_api_key_row_uses_daemon_thread():
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None

    body = ast.unparse(fn)
    assert "threading.Thread" in body, (
        "api_key_row must spawn a worker thread for non-blocking validation"
    )
    assert "daemon=True" in body, (
        "worker thread must be daemon so it doesn't block process exit"
    )


def test_api_key_row_marshals_via_after():
    """UI updates from the worker thread MUST go through parent.after(0, ...).
    CTk widgets are not thread-safe — direct .configure() from worker
    causes random crashes on Windows + intermittent rendering bugs."""
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None

    body = ast.unparse(fn)
    assert "parent.after(0" in body, (
        "api_key_row must marshal worker-thread UI updates via parent.after(0, ...)"
    )
