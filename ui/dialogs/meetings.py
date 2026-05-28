"""Meetings browser + read-only transcript viewer.

Renamed from history.py on 2026-05-28 — UI consistency with the new
«Митинги» button + Settings folder picker. Files on disk are
unchanged; the underlying utils.list_history_entries / delete_history_entry
helpers keep their internal names (rename was UI-only).
"""

from __future__ import annotations

import os
from tkinter import filedialog, messagebox

import customtkinter as ctk

from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    BORDER,
    FONT,
    INPUT_BG,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from utils import (
    delete_history_entry,
    list_history_entries,
    open_in_explorer,
    save_transcript,
)


def _read_transcript(folder_path: str) -> str:
    """Read transcript from a meeting folder. Empty string on failure.

    Tries transcript.md first (new convention since 2026-05-28), falls
    back to transcript.txt for older meeting folders. Returning "" on
    both failures matches the caller's empty-state UI (textbox shows a
    "(empty)" placeholder).
    """
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
    """Read-only viewer for a single history entry's transcript.

    Three actions: copy to clipboard, save as a new file, or load the
    transcript text back into the main window's textbox.
    """

    def __init__(self, parent, entry: dict, on_load_to_main):
        super().__init__(parent)
        title = entry.get("audio_file") or entry.get("folder_name", "Транскрипт")
        self.title(title)
        self.geometry("760x600")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._on_load_to_main = on_load_to_main
        self._entry = entry
        self._text = _read_transcript(entry["folder_path"])

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
        # Keep editable so the user can copy a partial selection; we don't
        # write back to disk, the file remains the source of truth.

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
            filetypes=[
                ("Markdown", "*.md"),
                ("Text files", "*.txt"),
            ],
            parent=self,
        )
        if path:
            save_transcript(self._text, path)

    def _load_to_main(self):
        audio_name = self._entry.get("audio_file")
        audio_path = (
            os.path.join(self._entry["folder_path"], audio_name)
            if audio_name else None
        )
        self._on_load_to_main(self._text, audio_path)
        self._close()

    def _close(self):
        self.grab_release()
        self.destroy()


class MeetingsDialog(ctk.CTkToplevel):
    """Browse meeting history — each entry is a folder on disk."""

    def __init__(self, parent, on_load_to_main):
        super().__init__(parent)
        self.title("Митинги")
        self.geometry("760x600")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._on_load_to_main = on_load_to_main
        self._all_entries: list[dict] = []
        # Cache transcript text per entry so the search box doesn't reread
        # files on every keystroke. Built lazily on first match attempt.
        self._transcript_cache: dict[str, str] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Митинги",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        # Search bar — filters by filename OR transcript content.
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.grid(row=1, column=0, padx=16, pady=(8, 4), sticky="ew")
        search_frame.grid_columnconfigure(0, weight=1)
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._render_entries())
        ctk.CTkEntry(
            search_frame, textvariable=self._search_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
            placeholder_text="🔍 Поиск по имени файла или содержимому...",
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
            footer, text="",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_count.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            footer, text="Готово", width=100, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._close,
        ).grid(row=0, column=1, sticky="e")

        self._all_entries = list_history_entries()
        self._render_entries()

    def _matches_query(self, entry: dict, query: str) -> bool:
        """Case-insensitive match against filename then transcript body.

        Filename match is cheap (no I/O) — try it first and skip transcript
        read for entries that already match. Transcript content is cached
        across keystrokes so reading happens at most once per entry.
        """
        if not query:
            return True
        q = query.lower()
        if q in (entry.get("audio_file") or "").lower():
            return True
        if q in entry.get("folder_name", "").lower():
            return True
        path = entry["folder_path"]
        if path not in self._transcript_cache:
            self._transcript_cache[path] = _read_transcript(path).lower()
        return q in self._transcript_cache[path]

    def _render_entries(self):
        for w in self._entry_list.winfo_children():
            w.destroy()

        query = self._search_var.get().strip()
        entries = [e for e in self._all_entries if self._matches_query(e, query)]
        suffix = f" / {len(self._all_entries)}" if query else ""
        self._lbl_count.configure(text=f"Митингов: {len(entries)}{suffix}")

        if not entries:
            msg = "Ничего не найдено" if query else "Нет митингов"
            ctk.CTkLabel(
                self._entry_list, text=msg,
                font=ctk.CTkFont(family=FONT, size=13),
                text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=30)
            return

        for i, entry in enumerate(entries):
            row = ctk.CTkFrame(self._entry_list, fg_color=SURFACE_BRIGHT, corner_radius=10)
            row.grid(row=i, column=0, padx=4, pady=3, sticky="ew")
            row.grid_columnconfigure(0, weight=1)

            title = entry.get("audio_file") or entry["folder_name"]
            ctk.CTkLabel(
                row, text=title, anchor="w",
                font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
                text_color=TEXT_PRIMARY,
            ).grid(row=0, column=0, padx=12, pady=(8, 0), sticky="ew")

            date = entry.get("date_display", "")
            files = []
            if entry.get("audio_file"):
                files.append(entry["audio_file"])
            if entry.get("has_transcript"):
                files.append("transcript.md")
            files.append("description.md")
            meta = f"{date}   •   {', '.join(files)}"
            ctk.CTkLabel(
                row, text=meta, anchor="w",
                font=ctk.CTkFont(family=FONT, size=11),
                text_color=TEXT_SECONDARY,
            ).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")

            # View button — only when transcript actually exists.
            if entry.get("has_transcript"):
                ctk.CTkButton(
                    row, text="👁 Просмотр", width=110, height=32, corner_radius=16,
                    font=ctk.CTkFont(family=FONT, size=12),
                    fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                    command=lambda e=entry: self._view_entry(e),
                ).grid(row=0, column=1, rowspan=2, padx=(8, 4), pady=6)

            ctk.CTkButton(
                row, text="📂 Папка", width=100, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda p=entry["folder_path"]: open_in_explorer(p),
            ).grid(row=0, column=2, rowspan=2, padx=(0, 4), pady=6)

            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=14),
                fg_color="transparent", hover_color=BORDER,
                text_color=RED,
                command=lambda p=entry["folder_path"]: self._delete_entry(p),
            ).grid(row=0, column=3, rowspan=2, padx=(0, 8), pady=4)

    def _view_entry(self, entry: dict):
        MeetingViewerDialog(self, entry, self._on_load_to_main)

    def _delete_entry(self, folder_path: str):
        if messagebox.askyesno("Удалить", "Удалить эту запись из истории?"):
            delete_history_entry(folder_path)
            self._transcript_cache.pop(folder_path, None)
            self._all_entries = list_history_entries()
            self._render_entries()

    def _close(self):
        self.grab_release()
        self.destroy()
