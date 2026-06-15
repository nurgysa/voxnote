"""Recorder controls — start/stop/pause + the 100 ms timer-and-level tick.

Extracted from ``ui/app/__init__.py`` (F4-PR-2c). Mixin contract: relies on
the App instance providing ``self._recorder`` (a ``recorder.Recorder``),
``self._btn_rec``, ``self._btn_rec_pause``, ``self._lbl_rec_time``,
``self._lbl_file``, ``self._rec_level``,
``self._lbl_status``, ``self._audio_path``, ``self._rec_timer_id``, and
the ``after`` / ``after_cancel`` methods inherited from ``ctk.CTk``.
"""
from __future__ import annotations

import os
from tkinter import messagebox

from theme import GREEN, RED, TEXT_PRIMARY, TEXT_SECONDARY
from utils import get_recordings_dir


class RecorderMixin:
    """Microphone capture controls bound to App's recorder card widgets."""

    def _toggle_recording(self):
        """Start or stop recording."""
        if self._recorder.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        try:
            self._recorder.start(output_dir=get_recordings_dir())
        # Mic-open failures are heterogeneous (PortAudio device errors,
        # libsndfile open, OSError from makedirs) — any of them must reach
        # the error dialog rather than crash the Tk callback.
        except Exception as e:
            messagebox.showerror("Ошибка записи", str(e))
            return
        self._btn_rec.configure(text="⏹  Стоп", fg_color="#B3261E")
        self._btn_rec_pause.configure(state="normal")
        self._lbl_rec_time.configure(text="00:00", text_color=RED)
        self._update_rec_timer()

    def _stop_recording(self):
        path = self._recorder.stop()
        if self._rec_timer_id:
            self.after_cancel(self._rec_timer_id)
            self._rec_timer_id = None
        self._btn_rec.configure(text="⏺  Запись", fg_color="#D93025")
        self._btn_rec_pause.configure(state="disabled", text="Пауза")
        self._rec_level.set(0)
        if path and os.path.exists(path):
            # Keep _audio_path so the Audio Cutter can pre-load the recording;
            # the queue is the transcription path now (no «Транскрибировать»).
            self._audio_path = path
            self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
            elapsed = self._lbl_rec_time.cget("text")
            self._lbl_rec_time.configure(text=elapsed, text_color=GREEN)
            self._enqueue(path, "record")

    def _toggle_pause(self):
        if self._recorder.is_paused:
            self._recorder.resume()
            self._btn_rec_pause.configure(text="Пауза")
            self._lbl_rec_time.configure(text_color=RED)
        else:
            self._recorder.pause()
            self._btn_rec_pause.configure(text="Продолжить")
            self._lbl_rec_time.configure(text_color=TEXT_SECONDARY)

    def _update_rec_timer(self):
        """Update recording timer and level meter every 100ms."""
        if not self._recorder.is_recording:
            return
        elapsed = self._recorder.elapsed
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        if h > 0:
            self._lbl_rec_time.configure(text=f"{h}:{m:02d}:{s:02d}")
        else:
            self._lbl_rec_time.configure(text=f"{m:02d}:{s:02d}")
        # Update level meter (smoothed)
        level = min(self._recorder.peak_level * 3.0, 1.0)  # amplify for visibility
        self._rec_level.set(level)
        self._rec_timer_id = self.after(100, self._update_rec_timer)
