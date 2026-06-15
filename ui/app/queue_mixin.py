"""Processing-queue integration for the main App window.

Replaces the old synchronous transcription run-loop (transcription_mixin,
removed in PR-C1). Record-stop and «Выбрать файл» now ENQUEUE onto the serial
ProcessingQueue (processing/worker.py): the worker transcribes + diarizes,
writes transcript.md into the Obsidian vault, archives audio to Drive sources,
and fires a best-effort Hermes nudge. The App reacts to queue changes via the
injected on_change callback (marshalled to the Tk thread with after(0, ...)) and
shows an aggregate indicator strip. Per-meeting status + history land in the
«Встречи» dialog (PR-C2); the project selector lands in PR-C1b.

Mixin contract: relies on App providing the option Vars (_cloud_provider_var,
_lang_var, _diar_var, _spk_count_var, _denoise_var), _cloud_api_keys, _config,
the widgets _lbl_queue / _lbl_status, and self._queue (ProcessingQueue, built in
App.__init__). NO worker thread of its own — ProcessingQueue owns that.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox

from processing.model import StageStatus
from theme import GREEN, RED, TEXT_SECONDARY
from utils import save_config

from .constants import LANGUAGES, NO_PROJECT_LABEL, SPEAKER_COUNTS


class QueueMixin:
    """Enqueue + reactive indicator over the App's ProcessingQueue."""

    def _build_options(self, source: str) -> dict:
        """Gather the current run options from the App's setting Vars into the
        dict the worker consumes. project_id comes from the main-bar project
        selector (Без проекта → None)."""
        saved_terms = self._config.get("hotwords", [])
        num_speakers, min_speakers, max_speakers = SPEAKER_COUNTS.get(
            self._spk_count_var.get(), (None, None, None),
        )
        return {
            "provider": self._cloud_provider_var.get(),
            "language": LANGUAGES.get(self._lang_var.get()),
            "diarize": bool(self._diar_var.get()),
            "hotwords": ", ".join(saved_terms) if saved_terms else None,
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
            "denoise": bool(self._denoise_var.get()),
            "project_id": getattr(self, "_project_choices", {}).get(
                self._project_var.get()
            ),
            "source": source,
        }

    def _refresh_project_selector(self) -> None:
        """(Re)build the project dropdown from the directory store.

        Called once at startup (after _dir_store.load()) and again whenever the
        Справочники dialog closes (projects may be added/renamed/deleted).
        Builds a label→id map (Без проекта → None); duplicate project names get
        a short id suffix so the map stays 1:1. Restores the selection from
        config[last_project_id], falling back to Без проекта if that project is
        gone."""
        choices: dict[str, str | None] = {NO_PROJECT_LABEL: None}
        for project in self._dir_store.projects():
            label = project.name or "(без имени)"
            if label in choices:
                label = f"{label} · {project.id[:6]}"
            choices[label] = project.id
        self._project_choices = choices
        self._project_menu.configure(values=list(choices.keys()))

        last = (self._config.get("last_project_id") or "").strip()
        selected = NO_PROJECT_LABEL
        if last:
            for lbl, pid in choices.items():
                if pid == last:
                    selected = lbl
                    break
        self._project_var.set(selected)

    def _on_project_changed(self, _label: str | None = None) -> None:
        """Persist the chosen project as last_project_id so it's the default
        next launch. Без проекта (None) is stored as an empty string."""
        pid = getattr(self, "_project_choices", {}).get(self._project_var.get())
        self._config["last_project_id"] = pid or ""
        save_config(self._config)

    def _enqueue(self, audio_path: str, source: str) -> None:
        """Add an audio file to the processing queue. Pre-checks the cloud key
        so a missing key is caught here (clear dialog) rather than surfacing as
        a queue error item."""
        provider = self._cloud_provider_var.get()
        if not (self._cloud_api_keys.get(provider) or "").strip():
            messagebox.showwarning(
                "Нужен API-ключ",
                f"API-ключ для {provider} не задан.\n\n"
                f"Открой Настройки → Транскрибация (cloud API) и вставь ключ.",
            )
            return
        self._queue.enqueue(audio_path, self._build_options(source))
        self._lbl_status.configure(
            text=f"Добавлено в очередь: {os.path.basename(audio_path)}",
            text_color=GREEN,
        )
        self._refresh_queue_indicator()

    def _safe_after_refresh(self) -> None:
        """ProcessingQueue on_change fires on the worker daemon thread; during
        shutdown the Tk root may already be torn down. Catch the post-destroy
        TclError so it never kills the worker thread mid-status-write."""
        try:
            self.after(0, self._on_queue_changed)
        except tk.TclError:
            pass

    def _on_queue_changed(self) -> None:
        """ProcessingQueue on_change target. Already marshalled to the Tk thread
        by the App's after(0, ...) wrapper, so touching widgets here is safe."""
        self._refresh_queue_indicator()

    def _refresh_queue_indicator(self) -> None:
        """Repaint the aggregate queue strip from a fresh snapshot."""
        items = self._queue.snapshot()
        in_work = sum(
            1 for it in items
            if it.status in (StageStatus.PENDING, StageStatus.RUNNING)
        )
        errors = sum(1 for it in items if it.status == StageStatus.ERROR)
        self._lbl_queue.configure(
            text=f"● Очередь: {in_work} в работе · {errors} ошибок",
            text_color=RED if errors else TEXT_SECONDARY,
        )

    def _on_app_close(self) -> None:
        """Stop the queue's daemon thread, then close the window."""
        self._queue.stop()
        self.destroy()
