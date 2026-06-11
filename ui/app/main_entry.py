"""CLI entry point — ``main()`` invoked from the root ``app.py``.

Extracted from ``ui/app/__init__.py`` (F4-PR-2a) to keep the package root
focused on the ``App`` class. The root ``app.py`` continues to import this
through the re-export:

    from ui.app import main   # resolves to ui.app.main_entry.main

``App`` is imported lazily inside ``main()`` to avoid a circular import
during the package's own ``__init__`` evaluation: when ``__init__.py``
finishes executing ``from .main_entry import main`` at the bottom, the
class has already been defined, so the lazy ``from . import App`` resolves
cleanly without import-time partial-module pitfalls.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from logging_setup import get_logger
from utils import check_ffmpeg

logger = get_logger(__name__)


def main():
    try:
        if not check_ffmpeg():
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "FFmpeg не найден",
                "Для работы приложения необходим FFmpeg.\n\n"
                "Установите его:\n"
                "1. Скачайте с https://ffmpeg.org/download.html\n"
                "2. Добавьте папку bin в переменную PATH\n"
                "3. Перезапустите приложение",
            )
            root.destroy()
            return

        # Lazy import to keep the package __init__ free of cycles —
        # ``App`` is defined in ``__init__.py`` after ``main_entry`` is
        # loaded for its ``from .main_entry import main`` re-export.
        from . import App
        app = App()
        app.mainloop()
    # Last-resort crash handler: anything escaping App must be logged and
    # shown to the user before the process dies — frozen windowed builds
    # have no console where a traceback could be seen.
    except Exception as e:
        logger.exception("fatal error in main()")
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Ошибка запуска", str(e))
            root.destroy()
        # Tk itself may be unusable here (the crash above may BE a Tk init
        # failure) — fall back to the console so dev runs still see it.
        except Exception:
            print(f"Ошибка: {e}")
            input("Нажмите Enter для выхода...")
