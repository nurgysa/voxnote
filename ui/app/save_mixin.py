"""Save / copy actions for the result textbox — TXT, SRT, VTT exports.

Extracted from ``ui/app/__init__.py`` (F4-PR-2c). Mixin contract: relies on
the App instance providing ``self._textbox``, ``self._audio_path``,
``self._transcriber`` (or None), ``self._lbl_status``, and the
``clipboard_clear`` / ``clipboard_append`` methods inherited from
``ctk.CTk``. SRT/VTT export reads per-segment timestamps from
``self._transcriber.last_segments`` — silently degrades to TXT-only when
segments are missing (e.g. the user typed text into the box manually).
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
            get_output_path(self._audio_path) if self._audio_path else "transcript.txt"
        )
        path = filedialog.asksaveasfilename(
            title="Сохранить транскрипцию",
            defaultextension=".txt",
            initialfile=os.path.basename(default_path),
            filetypes=[
                ("Text files", "*.txt"),
                ("SubRip subtitles", "*.srt"),
                ("WebVTT subtitles", "*.vtt"),
            ],
        )
        if not path:
            return

        # SRT/VTT need per-segment timestamps from the last transcription.
        # If the user picks a subtitle format but we don't have segments
        # (e.g. they typed text into the box manually), warn — a silent .srt
        # with one giant cue would be useless.
        ext = os.path.splitext(path)[1].lower()
        segments = self._transcriber.last_segments if self._transcriber else None
        if ext in (".srt", ".vtt"):
            if not segments:
                messagebox.showwarning(
                    "Нет таймкодов",
                    "Для экспорта в SRT/VTT нужна свежая транскрипция —\n"
                    "запустите её заново.",
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
