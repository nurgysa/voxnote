"""Dialog launchers — opens Settings/History/Terms/Cutter/Extract.

Extracted from ``ui/app/__init__.py`` (F4-PR-2e). Methods covering the
five dialogs the App can launch, plus the history-load callback and the
singleton-state cleanup hooks. The Settings dialog is a singleton
(re-click lifts the existing window); History, Terms, ExtractTasks
are modal; Audio Cutter is loosely tracked (latest instance only) so
the live-theme switch can repaint its Canvas.

Voices dialog was removed in the 2026-05-28 cloud-only rip-out — voice
enrollment depended on local pyannote embeddings which are gone. The
System Monitor dialog was removed in the same rip-out — its GPU/CPU/RAM
panels were CUDA-era diagnostics with no purpose in cloud-only mode.

Mixin contract: relies on App providing ``self._config``, ``self._settings_dialog``,
``self._cutter``,
``self._textbox``, ``self._lbl_status``, ``self._lbl_file``,
``self._btn_save``, ``self._btn_copy``,
``self._btn_extract_tasks``, ``self._lang_var``, ``self._audio_path``,
``self._last_history_folder``. ``_open_extract_tasks_dialog`` lazy-imports
``ExtractTasksDialog`` to keep the ``tasks/extractor`` (and transitively
``requests``) module load off the App-startup path.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox

from audio_cutter import AudioCutter
from theme import TEXT_PRIMARY, TEXT_SECONDARY
from ui.dialogs.directory import DirectoryDialog
from ui.dialogs.meetings import MeetingsDialog
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.terms import TermsDialog

from .constants import LANGUAGES


class DialogsMixin:
    """Launchers + singleton-state callbacks for the App dialogs."""

    def _open_settings_dialog(self):
        # Track the open dialog so terms/voices saves can refresh its
        # summaries live. Cleared on close via Tk's <Destroy> event.
        if self._settings_dialog is not None:
            try:
                self._settings_dialog.lift()
                self._settings_dialog.focus_set()
                return
            except tk.TclError:
                # Dialog window was destroyed before <Destroy> fired (race
                # on Windows after alt-F4) — drop the stale ref and re-open.
                self._settings_dialog = None
        self._settings_dialog = SettingsDialog(self)
        self._settings_dialog.bind(
            "<Destroy>", lambda _e: self._on_settings_dialog_closed(_e),
        )

    def _on_settings_dialog_closed(self, event) -> None:
        # CTk fires <Destroy> for many child widgets; only the top-level
        # toplevel itself signals dialog close. Compare against widget to
        # avoid clearing the reference on inner widget destruction.
        if event.widget is self._settings_dialog:
            self._settings_dialog = None

    def _refresh_settings_summaries(self) -> None:
        """If the Settings dialog is open, re-render its term/voice summaries."""
        if self._settings_dialog is not None:
            try:
                self._settings_dialog._refresh_summaries()
            except tk.TclError:
                # Dialog widget was destroyed mid-refresh — nothing to update.
                pass

    def _open_terms_dialog(self):
        TermsDialog(self, self._config, self._refresh_settings_summaries)

    def _open_meetings_dialog(self):
        MeetingsDialog(self, on_load_to_main=self._load_history_into_main)

    def _open_directory_dialog(self):
        DirectoryDialog(self)

    def _open_extract_tasks_dialog(self):
        """Validate the OpenRouter key is set, then open the Extract dialog.

        Linear / Glide keys are OPT-IN: required only if the user actually
        picks that backend in the dialog. The dialog itself handles missing
        backend keys gracefully (see extract_tasks/__init__.py:432 —
        '(нет ключа Linear/Glide)' placeholder + empty members/labels lists
        passed to the LLM prompt). protocol.md generation also runs through
        OpenRouter alone — no Linear/Glide dependency at all.
        """
        openrouter_key = (self._config.get("openrouter_api_key") or "").strip()
        if not openrouter_key:
            messagebox.showwarning(
                "Нет OpenRouter ключа",
                "Извлечение задач и генерация протокола требуют "
                "OpenRouter API ключ (LLM-вызов).\n\n"
                "Откройте Настройки → OpenRouter и введите ключ.\n\n"
                "Linear / Glide ключи опциональны — они нужны только если "
                "хотите отправлять задачи в эти таск-трекеры.",
            )
            return

        transcript = self._textbox.get("1.0", "end").strip()
        if not transcript:
            messagebox.showwarning(
                "Нет транскрипции",
                "Сначала запустите транскрипцию или загрузите её из Истории.",
            )
            return

        if not self._last_history_folder:
            messagebox.showwarning(
                "Нет папки истории",
                "Извлечение пишет результат в папку из Истории. "
                "Запустите транскрипцию или откройте запись из Истории, "
                "затем повторите.",
            )
            return

        # Lazy import — pulls in tasks/extractor and (transitively) requests.
        # Same pattern as Settings dialog's lazy validate-button imports.
        # ExtractTasksDialog(parent, *, transcript, history_folder, transcript_lang, config)
        from ui.dialogs.extract_tasks import ExtractTasksDialog
        ExtractTasksDialog(
            self,
            transcript=transcript,
            history_folder=self._last_history_folder,
            transcript_lang=LANGUAGES.get(self._lang_var.get()),
            config=self._config,
        )

    def _load_history_into_main(self, transcript_text: str, audio_path: str | None):
        """Drop a history entry's transcript into the main textbox.

        If the audio file exists in the history folder, also wire it up as
        the current audio source so the user can re-transcribe (e.g. with
        diarization toggled differently) without re-picking the file.
        """
        self._textbox.delete("1.0", "end")
        self._textbox.insert("1.0", transcript_text)
        self._btn_save.configure(state="normal")
        self._btn_copy.configure(state="normal")
        # The history entry's folder IS the target for any future extract.
        self._last_history_folder = os.path.dirname(audio_path) if audio_path else None
        self._btn_extract_tasks.configure(
            state="normal" if self._last_history_folder else "disabled",
        )
        if audio_path and os.path.isfile(audio_path):
            self._audio_path = audio_path
            self._lbl_file.configure(
                text=os.path.basename(audio_path), text_color=TEXT_PRIMARY,
            )
        self._lbl_status.configure(
            text="Загружено из истории", text_color=TEXT_SECONDARY,
        )

    def _open_cutter(self):
        # Track the most recent cutter so theme changes can repaint it.
        # AudioCutter doesn't need true singleton semantics — multiple
        # opens are fine — we just keep the latest reference.
        self._cutter = AudioCutter(self, audio_path=self._audio_path)
