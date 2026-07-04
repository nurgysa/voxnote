"""Meetings browser — live queue + on-disk history + read-only viewer.

«Встречи» = queue + history (PR-C2): rows come from
processing.store.build_view (a disk scan overlaid with the live
ProcessingQueue snapshot), so an in-flight transcription shows its status
(в очереди / идёт mm:ss / готово / ошибка) next to finished meetings. Rows are
grouped by project; finished meetings carry Hermes-progress badges
(protocol/tasks) and open in Obsidian; errored items offer «Повторить».
Presentation logic lives in the headless ui.dialogs.meetings_view module
(unit-tested); this file is the Tk renderer. Renamed from history.py on
2026-05-28; terminology «Встречи» since 2026-06-11.
"""
from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from processing.model import StageStatus
from processing.store import build_view
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    BORDER,
    FONT,
    GREEN,
    INPUT_BG,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.dialogs.meetings_view import (
    NO_PROJECT_LABEL,
    format_status,
    group_by_project,
    queue_position,
)
from ui.dialogs.voice_bind import VoiceBindDialog
from utils import (
    delete_history_entry,
    get_meetings_dir,
    open_in_explorer,
    plural_ru,
    save_transcript,
)

# color_key from meetings_view.format_status → theme color.
_STATUS_COLORS = {
    "pending": TEXT_SECONDARY,
    "running": GREEN,
    "done": GREEN,
    "error": RED,
}


def _read_transcript(folder_path: str) -> str:
    """Read transcript from a meeting folder. Empty string on failure.

    Tries transcript.md first (convention since 2026-05-28), falls back to
    transcript.txt for older meeting folders."""
    for filename in ("transcript.md", "transcript.txt"):
        path = os.path.join(folder_path, filename)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                continue
    return ""


class MeetingViewerDialog(ctk.CTkToplevel):
    """Read-only viewer for a single meeting's transcript."""

    def __init__(self, parent, item, on_load_to_main):
        super().__init__(parent)
        title = item.title or os.path.basename(item.meeting_folder or "Транскрипт")
        self.title(title)
        self.geometry("760x600")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._on_load_to_main = on_load_to_main
        self._item = item
        self._text = _read_transcript(item.meeting_folder or "")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text=title,
            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        textbox = ctk.CTkTextbox(
            self, wrap="word", corner_radius=12,
            fg_color=SURFACE, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        )
        textbox.grid(row=1, column=0, padx=16, pady=8, sticky="nsew")
        textbox.insert("1.0", self._text or "(transcript отсутствует или пуст)")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=16, pady=(4, 14), sticky="ew")
        footer.grid_columnconfigure(3, weight=1)

        ctk.CTkButton(
            footer, text="Копировать", width=120, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._copy,
        ).grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            footer, text="Сохранить как…", width=160, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._save_as,
        ).grid(row=0, column=1, padx=8)

        ctk.CTkButton(
            footer, text="В основное окно", width=170, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._load_to_main,
        ).grid(row=0, column=2, padx=8)

        ctk.CTkButton(
            footer, text="Закрыть", width=110, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._close,
        ).grid(row=0, column=3, sticky="e")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._text)

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить транскрипцию",
            defaultextension=".md",
            initialfile="transcript.md",
            filetypes=[("Markdown", "*.md"), ("Text files", "*.txt")],
            parent=self,
        )
        if path:
            save_transcript(self._text, path)

    def _load_to_main(self):
        audio_path = self._item.audio_path or None
        if not (audio_path and os.path.isfile(audio_path)):
            audio_path = None
        self._on_load_to_main(self._text, audio_path)
        self._close()

    def _close(self):
        self.grab_release()
        self.destroy()


class MeetingsDialog(ctk.CTkToplevel):
    """«Встречи» — live queue + on-disk history, grouped by project."""

    _TICK_MS = 1000

    def __init__(self, parent, on_load_to_main, queue):
        super().__init__(parent)
        self.title("Встречи")
        self.geometry("820x640")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._app = parent
        self._queue = queue
        self._on_load_to_main = on_load_to_main
        self._transcript_cache: dict[str, str] = {}
        self._after_id = None
        self._last_sig: tuple | None = None
        self._running_rows: list = []  # (item, status_label) for the live mm:ss tick

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Встречи",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.grid(row=1, column=0, padx=16, pady=(8, 4), sticky="ew")
        search_frame.grid_columnconfigure(0, weight=1)
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._render())
        ctk.CTkEntry(
            search_frame, textvariable=self._search_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
            placeholder_text="🔍 Поиск по имени или содержимому...",
        ).grid(row=0, column=0, sticky="ew")

        self._entry_list = ctk.CTkScrollableFrame(
            self, fg_color=SURFACE, corner_radius=12,
        )
        self._entry_list.grid(row=2, column=0, padx=16, pady=4, sticky="nsew")
        self._entry_list.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(4, 12), sticky="ew")
        footer.grid_columnconfigure(1, weight=1)
        self._lbl_count = ctk.CTkLabel(
            footer, text="", font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_count.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Готово", width=100, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._close,
        ).grid(row=0, column=1, sticky="e")

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._render()
        self._after_id = self.after(self._TICK_MS, self._tick)

    # ── data ──
    def _rows(self) -> list:
        return build_view(get_meetings_dir(), self._queue.snapshot())

    def _project_name(self, project_id) -> str:
        store = getattr(self._app, "_dir_store", None)
        if project_id and store is not None:
            project = store.get_project(project_id)
            if project is not None:
                return project.name
        return NO_PROJECT_LABEL

    def _sig(self, rows) -> tuple:
        return tuple(
            (
                r.id,
                r.status.value,
                r.has_protocol,
                r.has_tasks,
                r.pending_voices_count,
            )
            for r in rows
        )

    def _matches(self, item, query: str) -> bool:
        if not query:
            return True
        q = query.lower()
        if q in (item.title or "").lower():
            return True
        folder = item.meeting_folder or ""
        if not folder:
            return False
        if folder not in self._transcript_cache:
            self._transcript_cache[folder] = _read_transcript(folder).lower()
        return q in self._transcript_cache[folder]

    # ── render ──
    def _render(self, rows=None):
        if rows is None:
            rows = self._rows()
        self._last_sig = self._sig(rows)
        self._running_rows = []
        for widget in self._entry_list.winfo_children():
            widget.destroy()

        query = self._search_var.get().strip()
        shown = [r for r in rows if self._matches(r, query)]
        suffix = f" / {len(rows)}" if query else ""
        self._lbl_count.configure(text=f"Встреч: {len(shown)}{suffix}")

        if not shown:
            message = "Ничего не найдено" if query else "Нет встреч"
            ctk.CTkLabel(
                self._entry_list, text=message,
                font=ctk.CTkFont(family=FONT, size=13), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=30)
            return

        now = datetime.now().isoformat(timespec="seconds")
        grid_row = 0
        for group_name, items in group_by_project(shown, self._project_name):
            ctk.CTkLabel(
                self._entry_list, text=group_name, anchor="w",
                font=ctk.CTkFont(family=FONT, size=12, weight="bold"),
                text_color=TEXT_SECONDARY,
            ).grid(row=grid_row, column=0, padx=8, pady=(10, 2), sticky="w")
            grid_row += 1
            for item in items:
                self._build_row(item, grid_row, rows, now)
                grid_row += 1

    def _build_row(self, item, grid_row, all_rows, now_iso):
        row = ctk.CTkFrame(self._entry_list, fg_color=SURFACE_BRIGHT, corner_radius=10)
        row.grid(row=grid_row, column=0, padx=4, pady=3, sticky="ew")
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row, text=item.title or os.path.basename(item.meeting_folder or "—"),
            anchor="w", font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=12, pady=(8, 0), sticky="ew")

        meta = ctk.CTkFrame(row, fg_color="transparent")
        meta.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")
        text, color_key = format_status(item, now_iso, queue_position(all_rows, item))
        status_lbl = ctk.CTkLabel(
            meta, text=text, anchor="w",
            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
            text_color=_STATUS_COLORS.get(color_key, TEXT_SECONDARY),
        )
        status_lbl.grid(row=0, column=0, sticky="w")
        if item.status == StageStatus.RUNNING:
            self._running_rows.append((item, status_lbl))
        badge_col = 1
        for present, badge_text in (
            (item.has_protocol, "• протокол"),
            (item.has_tasks, "• задачи"),
        ):
            if present:
                ctk.CTkLabel(
                    meta, text=badge_text, font=ctk.CTkFont(family=FONT, size=11),
                    text_color=TEXT_SECONDARY,
                ).grid(row=0, column=badge_col, padx=(8, 0), sticky="w")
                badge_col += 1
        if item.pending_voices_count:
            n = item.pending_voices_count
            word = plural_ru(n, "новый голос", "новых голоса", "новых голосов")
            ctk.CTkButton(
                meta,
                text=f"🆕 {n} {word}",
                width=0,
                height=24,
                corner_radius=12,
                font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
                fg_color=BLUE_SURFACE,
                hover_color=SURFACE_BRIGHT,
                text_color="#8AB4F8",
                command=lambda it=item: self._bind_voices(it),
            ).grid(row=0, column=badge_col, padx=(8, 0), sticky="w")
            badge_col += 1

        if item.status == StageStatus.ERROR and item.error_message:
            ctk.CTkLabel(
                row, text=item.error_message, anchor="w", justify="left",
                wraplength=560, font=ctk.CTkFont(family=FONT, size=11),
                text_color=RED,
            ).grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")

        col = 1
        if item.status == StageStatus.DONE and item.meeting_folder:
            ctk.CTkButton(
                row, text="👁 Просмотр", width=110, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda it=item: self._view(it),
            ).grid(row=0, column=col, rowspan=2, padx=(8, 4), pady=6)
            col += 1
            ctk.CTkButton(
                row, text="📝 Obsidian", width=120, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda it=item: self._open_obsidian(it),
            ).grid(row=0, column=col, rowspan=2, padx=(0, 4), pady=6)
            col += 1
            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=14),
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                command=lambda it=item: self._delete(it),
            ).grid(row=0, column=col, rowspan=2, padx=(0, 8), pady=4)
        elif item.status == StageStatus.ERROR:
            ctk.CTkButton(
                row, text="↻ Повторить", width=120, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda i=item.id: self._retry(i),
            ).grid(row=0, column=col, rowspan=2, padx=(8, 4), pady=6)
            col += 1
            ctk.CTkButton(
                row, text="✕ Убрать", width=100, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                command=lambda it=item: self._dismiss(it),
            ).grid(row=0, column=col, rowspan=2, padx=(0, 8), pady=6)

    # ── live poll ──
    def _tick(self):
        self._after_id = None
        try:
            rows = self._rows()
            if self._sig(rows) != self._last_sig:
                self._render(rows)
            else:
                now = datetime.now().isoformat(timespec="seconds")
                for item, label in self._running_rows:
                    text, _ = format_status(item, now, None)
                    label.configure(text=text)
        except tk.TclError:
            return  # window destroyed mid-tick — stop the loop
        self._after_id = self.after(self._TICK_MS, self._tick)

    # ── actions ──
    def _bind_voices(self, item):
        store = getattr(self._app, "_dir_store", None)
        if store is None:
            messagebox.showerror(
                "Новые голоса",
                "Справочник людей не загружен",
                parent=self,
            )
            return
        VoiceBindDialog(self, item, store, on_applied=lambda: self._render())

    def _view(self, item):
        MeetingViewerDialog(self, item, self._on_load_to_main)

    def _open_obsidian(self, item):
        path = os.path.join(item.meeting_folder or "", "transcript.md")
        if os.path.isfile(path):
            os.startfile(path)  # default .md handler (Obsidian if associated)
        else:
            open_in_explorer(item.meeting_folder or "")

    def _retry(self, item_id):
        self._queue.retry(item_id)
        self._render()

    def _dismiss(self, item):
        # Clear a stuck ERROR item from the queue. forget() drops any non-RUNNING
        # item; no folder is deleted — an ERROR normally has none, and a rare
        # late-failure's transcript.md on disk should survive as a DONE history
        # row (build_view reverts to it once the active item is forgotten).
        self._queue.forget(item.id)
        self._render()

    def _delete(self, item):
        folder = item.meeting_folder
        if folder and messagebox.askyesno("Удалить", "Удалить эту встречу?"):
            delete_history_entry(folder)
            # Also evict the lingering DONE active item, else build_view's
            # overlay re-appends it as a ghost row pointing at the deleted folder.
            self._queue.forget(item.id)
            self._transcript_cache.pop(folder, None)
            self._render()

    def _close(self):
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self.grab_release()
        self.destroy()
