"""Migration dialogs: prompt + progress.

Two modal CTkToplevel windows used by both first-launch and Settings-
trigger flows:

  MigrationPromptDialog — asks the user whether to move existing
    meetings from `src` to `dst`. Buttons differ by mode:
      first_launch: [Перенести] [Оставить в старой папке] [Спросить позже]
      settings:     [Перенести] [Просто переключить]

  MigrationProgressDialog — shows progress while migrate_meetings runs
    in a daemon thread. Cancel button signals the worker; closing via
    WM X is disabled (must use Cancel).

UI shell only — actual migration logic lives in meetings_migration.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable

import customtkinter as ctk

from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    FONT,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


def _folder_size_bytes(path: str) -> int:
    """Sum of file sizes under `path`. Tolerates locked files."""
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _fmt_size(n: int) -> str:
    """Bytes → human-readable (KB / MB / GB)."""
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.0f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.0f} MB"
    return f"{n / 1024**3:.1f} GB"


class MigrationPromptDialog(ctk.CTkToplevel):
    """Modal asking whether to migrate existing meetings.

    Modes:
      "first_launch" → 3 buttons (Перенести / Оставить в старой / Спросить позже)
      "settings"     → 2 buttons (Перенести / Просто переключить)

    On user choice, calls `on_choice(choice)` with one of:
      "migrate", "keep_old", "later", "switch_only"
    """

    def __init__(
        self,
        parent,
        *,
        src: str,
        dst: str,
        mode: str,
        on_choice: Callable[[str], None],
    ):
        super().__init__(parent)
        self.title("Перенос митингов")
        self.geometry("560x340")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        self._on_choice = on_choice
        self._mode = mode

        self.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        title_text = (
            "Перенос митингов" if mode == "first_launch"
            else "Перенести существующие митинги?"
        )
        ctk.CTkLabel(
            header, text=title_text,
            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        # Body — show src / dst paths + counts
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=20, pady=12, sticky="ew")
        body.grid_columnconfigure(0, weight=1)

        # Count + size for src
        from meetings_migration import count_meetings
        n_src = count_meetings(src)
        size_src = _fmt_size(_folder_size_bytes(src))

        if mode == "first_launch":
            label1 = f"Найдено {n_src} митингов в старой папке:"
        else:
            label1 = f"В текущей папке {n_src} митингов:"

        ctk.CTkLabel(
            body, text=label1,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_SECONDARY, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(
            body, text=src,
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_PRIMARY, anchor="w",
            wraplength=500,
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        dst_label = (
            "Новая папка по умолчанию:" if mode == "first_launch"
            else "Новая папка:"
        )
        ctk.CTkLabel(
            body, text=dst_label,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_SECONDARY, anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(
            body, text=dst,
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_PRIMARY, anchor="w",
            wraplength=500,
        ).grid(row=3, column=0, sticky="w", pady=(0, 8))

        # Footer with buttons
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=20, pady=(8, 16), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            footer,
            text=f"Перенести ({n_src} файлов, ~{size_src})",
            command=lambda: self._choose("migrate"),
            height=40, corner_radius=20, width=320,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
        ).grid(row=0, column=0, pady=4, sticky="ew")

        if mode == "first_launch":
            ctk.CTkButton(
                footer, text="Оставить в старой папке",
                command=lambda: self._choose("keep_old"),
                height=36, corner_radius=18, width=320,
                font=ctk.CTkFont(family=FONT, size=13),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
                text_color="#8AB4F8",
            ).grid(row=1, column=0, pady=4, sticky="ew")

            ctk.CTkButton(
                footer, text="Спросить позже",
                command=lambda: self._choose("later"),
                height=36, corner_radius=18, width=320,
                font=ctk.CTkFont(family=FONT, size=13),
                fg_color="transparent", hover_color=SURFACE_BRIGHT,
                text_color=TEXT_SECONDARY,
            ).grid(row=2, column=0, pady=4, sticky="ew")
        else:
            ctk.CTkButton(
                footer, text="Просто переключить",
                command=lambda: self._choose("switch_only"),
                height=36, corner_radius=18, width=320,
                font=ctk.CTkFont(family=FONT, size=13),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
                text_color="#8AB4F8",
            ).grid(row=1, column=0, pady=4, sticky="ew")

    def _choose(self, choice: str) -> None:
        self.grab_release()
        self.destroy()
        self._on_choice(choice)


class MigrationProgressDialog(ctk.CTkToplevel):
    """Modal showing migration progress. Cancel signals the worker.

    The actual move runs on a daemon thread. UI updates are marshalled
    via parent.after(0, ...). On completion, calls `on_done(summary)`
    where summary is the dict returned by migrate_meetings.
    """

    def __init__(
        self,
        parent,
        *,
        src: str,
        dst: str,
        on_done: Callable[[dict], None],
    ):
        super().__init__(parent)
        self.title("Перенос митингов")
        self.geometry("500x200")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        # Disable WM X — force user to use Cancel button
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        self._src = src
        self._dst = dst
        self._on_done = on_done
        self._cancel_event = threading.Event()

        self.grid_columnconfigure(0, weight=1)

        # Status label
        self._status = ctk.CTkLabel(
            self, text="Подготовка...",
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, anchor="w",
        )
        self._status.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="ew")

        # Current-folder label
        self._current = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(family=FONT, size=11),
            text_color=TEXT_SECONDARY, anchor="w",
        )
        self._current.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="ew")

        # Progress bar
        self._progress = ctk.CTkProgressBar(self, height=8, corner_radius=4)
        self._progress.grid(row=2, column=0, padx=20, pady=8, sticky="ew")
        self._progress.set(0.0)

        # Cancel button
        ctk.CTkButton(
            self, text="Отмена",
            command=self._on_cancel,
            height=36, corner_radius=18, width=120,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
            text_color="#8AB4F8",
        ).grid(row=3, column=0, padx=20, pady=(12, 16), sticky="e")

        # Spawn worker after grid is laid out
        self.after(50, self._start_worker)

    def _start_worker(self) -> None:
        def worker() -> None:
            from meetings_migration import migrate_meetings
            summary = migrate_meetings(
                self._src, self._dst,
                on_progress=self._on_progress,
                cancel_event=self._cancel_event,
            )
            self.after(0, lambda: self._on_complete(summary))

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        # Called from worker thread — marshal to main
        ratio = done / total if total else 0.0
        text_main = f"Переношу митинг {done} / {total}:"
        self.after(0, lambda: self._status.configure(text=text_main))
        self.after(0, lambda n=name: self._current.configure(text=n))
        self.after(0, lambda r=ratio: self._progress.set(r))

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        self._status.configure(text="Отменяется...")

    def _on_complete(self, summary: dict) -> None:
        # Always close + invoke callback. Caller handles partial-state UX.
        self.grab_release()
        self.destroy()
        self._on_done(summary)
