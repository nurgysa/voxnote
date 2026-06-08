"""report_callback_exception → logger (WS-3 crash visibility).

Tk's default ``report_callback_exception`` prints the traceback to stderr,
which is invisible in a windowed PyInstaller build (no console).
``logging_setup.log_callback_exception`` routes it through the logger so a
GUI-callback crash lands in ``logs/app.log`` (and the future "Отправить лог"
bundle) instead of vanishing. The App install is checked by source text —
``ui.app`` imports sounddevice and can't load on Linux CI (see
feedback_ui_app_import_breaks_linux_ci).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from logging_setup import log_callback_exception


def test_log_callback_exception_logs_at_error_with_traceback(caplog):
    try:
        raise ValueError("boom-in-callback")
    except ValueError:
        exc_info = sys.exc_info()

    with caplog.at_level(logging.ERROR, logger="tk.callback"):
        log_callback_exception(*exc_info)

    records = [r for r in caplog.records if r.name == "tk.callback"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None          # traceback captured
    assert "boom-in-callback" in caplog.text         # exception rendered


_APP_INIT = Path(__file__).resolve().parent.parent / "ui" / "app" / "__init__.py"


def test_app_installs_callback_exception_handler():
    # Source-text check — ui.app pulls sounddevice (unimportable on Linux CI).
    src = _APP_INIT.read_text(encoding="utf-8")
    assert "log_callback_exception" in src, "App must import the handler"
    assert "self.report_callback_exception = log_callback_exception" in src, (
        "App.__init__ must install the logging report_callback_exception"
    )
