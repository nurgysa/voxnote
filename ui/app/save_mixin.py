"""Save / copy actions for the result textbox — TXT, SRT, VTT exports.

Extracted from ``ui/app/__init__.py`` (F4-PR-2c). Mixin contract: relies on
the App instance providing ``self._textbox``, ``self._audio_path``,
``self._lbl_status``, and the ``clipboard_clear`` / ``clipboard_append``
methods inherited from ``ctk.CTk``. Since the queue rework (PR-C1) there is
no in-session segment source — the worker writes per-segment timestamps to
the app-data sidecar (``utils.save_segments_sidecar``), not the UI — so
SRT/VTT export degrades to a warning; TXT/MD always works. (Wiring saved-
meeting segments from the sidecar into SRT/VTT is a future enhancement.)
"""
from __future__ import annotations

import os
from tkinter import filedialog, messagebox

from theme import TEXT_SECONDARY
from utils import get_output_path, save_transcript


class SaveMixin:
    """Save-and-copy actions for the transcription result textbox."""

    def _save_txt(self):
        text = self._textbox.get("1.0", "end").strip()
        if not text:
            return
        default_path = (
            get_output_path(self._audio_path) if self._audio_path else "transcript.md"
        )
        path = filedialog.asksaveasfilename(
            title="Сохранить транскрипцию",
            defaultextension=".md",
            initialfile=os.path.basename(default_path),
            filetypes=[
                ("Markdown", "*.md"),
                ("Text files", "*.txt"),
                ("SubRip subtitles", "*.srt"),
                ("WebVTT subtitles", "*.vtt"),
            ],
        )
        if not path:
            return

        # SRT/VTT need per-segment timestamps. The queue worker writes those to
        # the app-data sidecar, not the UI, so there is no in-session segment
        # source here yet — degrade to a warning (a .srt with one giant cue
        # would be useless). Wiring the sidecar in is a future enhancement.
        ext = os.path.splitext(path)[1].lower()
        segments = None
        if ext in (".srt", ".vtt"):
            if not segments:
                messagebox.showwarning(
                    "Нет таймкодов",
                    "Экспорт в SRT/VTT пока недоступен для сохранённых встреч —\n"
                    "сохраните как TXT или MD.",
                )
                return
            from transcript_format import format_srt, format_vtt
            payload = format_srt(segments) if ext == ".srt" else format_vtt(segments)
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)
        else:
            save_transcript(text, path)
        self._lbl_status.configure(
            text=f"Сохранено: {os.path.basename(path)}", text_color=TEXT_SECONDARY,
        )

    def _copy_text(self):
        text = self._textbox.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._lbl_status.configure(
                text="Скопировано в буфер обмена", text_color=TEXT_SECONDARY,
            )
