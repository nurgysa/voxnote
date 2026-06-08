"""Centralized logging configuration.

One call to :func:`init_logging` at process start replaces the ad-hoc
``open(...).write()`` patterns scattered across ``transcriber.py`` and
``app.py``. Every module then uses a plain ``logging.getLogger(__name__)``
and inherits the rotating file handler + console handler configured here.

Why a custom file rotation: faulthandler.log is opened with mode "w" by
``app.py`` BEFORE this module loads (it has to — it's a C-level signal
handler). Standard logging's ``RotatingFileHandler`` is fine for our
plain log lines but must NOT touch faulthandler.log.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "app.log")
_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def init_logging(level: int = logging.INFO) -> None:
    """Configure the root logger. Safe to call multiple times (no-op after first).

    File handler rotates at 2 MB × 5 backups — enough to keep a couple of
    weeks of normal use without unbounded growth, even with diarization
    progress lines firing every few seconds.
    """
    global _initialized
    if _initialized:
        return

    os.makedirs(_LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=5,
        encoding="utf-8", delay=True,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Console handler is for the developer running ``python app.py`` from a
    # terminal — production users see the GUI status label, not stderr.
    # WARNING+ keeps it quiet during normal runs but surfaces real problems.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any pre-existing handlers (e.g. installed by a third-party
    # library at import time) so our format wins. Keeps log output uniform.
    root.handlers[:] = [file_handler, console_handler]

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Shorthand for ``logging.getLogger(name)`` — call sites read cleaner."""
    return logging.getLogger(name)


def log_callback_exception(exc_type, exc_value, exc_tb) -> None:
    """Replacement for ``tkinter.Tk.report_callback_exception``.

    Tk's default prints the traceback to ``sys.stderr`` — invisible in a
    windowed PyInstaller build (no console). Routing it through the logger
    lands the crash in ``logs/app.log`` (and the future "Отправить лог"
    bundle), so an uncaught exception in a GUI event callback is recoverable
    instead of silently swallowed. The console handler still surfaces it on
    stderr for a developer running ``python app.py`` from a terminal.

    Install via ``root.report_callback_exception = log_callback_exception``.
    """
    logging.getLogger("tk.callback").error(
        "Unhandled exception in a Tk callback",
        exc_info=(exc_type, exc_value, exc_tb),
    )


def crash_log_path(prefix: str) -> str:
    """Return a unique path under ``logs/`` for a structured crash dump.

    Used for traceback bundles that don't fit cleanly in a single log line
    (full subprocess stderr, environment snapshot, etc.). The plain rotating
    log carries the index entry; the dump carries the verbose payload.
    """
    from datetime import datetime
    os.makedirs(_LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(_LOG_DIR, f"{prefix}_{ts}.log")
