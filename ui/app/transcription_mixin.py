"""Transcription orchestration — start/cancel/progress/complete callbacks.

Cloud-only since the 2026-05-28 rip-out. The old local-CUDA branch, voice-
library temp-file plumbing, hybrid cloud-STT+local-pyannote diarization
gate, HF-token resolution, and GPU-device resolution are all gone. What
remains: a cloud-provider dispatcher that constructs a no-arg
``Transcriber()``, reads the selected cloud provider + API key from the
parent App's StringVars, and forwards everything to
``self._transcriber.transcribe(...)`` in a daemon worker thread.

Mixin contract: relies on App providing ``self._is_running``,
``self._audio_path``, ``self._transcriber`` (Transcriber | None — set
lazily here), ``self._cancel_event`` (threading.Event), ``self._config``
(mutable dict), the persistence vars ``self._lang_var``,
``self._diar_var``, ``self._spk_count_var``,
``self._denoise_var``, ``self._cloud_provider_var``,
``self._cloud_api_key_var``, ``self._cloud_api_keys``,
``self._last_history_folder``, and the widget refs ``self._btn_*``,
``self._lbl_*``, ``self._progress``, ``self._textbox``,
``self._diar_check``, ``self._spk_count_menu``.

Threading model: ``_start_transcription`` spawns a daemon thread running
``_run_transcription``. The worker calls ``self._transcriber.transcribe``
(blocking) and posts results back via ``self.after(0, callback, …)`` so
they execute on the Tk main thread. Cancellation flows the other way:
the main thread sets ``self._cancel_event``; the worker polls it inside
the transcription loop and raises ``TranscriptionCancelled``, which the
worker's ``except`` arm routes to ``_on_cancelled`` via ``after``.
"""
from __future__ import annotations

import os
import threading
from tkinter import messagebox

from logging_setup import crash_log_path, get_logger
from theme import BLUE, BLUE_DIM, GREEN, RED, TEXT_SECONDARY
from transcriber import Transcriber, TranscriptionCancelled
from utils import (
    create_history_entry,
    save_config,
    save_segments,
    should_delete_after_transcription,
)

from .constants import LANGUAGES, SPEAKER_COUNTS

logger = get_logger(__name__)


class TranscriptionMixin:
    """Run-loop callbacks and the worker-thread orchestrator."""

    def _set_running(self, running: bool):
        self._is_running = running
        state = "disabled" if running else "normal"
        self._btn_file.configure(state=state)
        self._diar_check.configure(state=state)
        self._btn_settings.configure(state=state)
        # The transcribe button doubles as cancel: enabled in both states.
        # When running, swaps to a red "Отмена" with _request_cancel command;
        # when idle, returns to the standard blue primary look.
        if running:
            self._btn_transcribe.configure(
                state="normal", text="Отмена",
                command=self._request_cancel,
                fg_color="#D93025", hover_color="#B3261E",
            )
        else:
            self._btn_transcribe.configure(
                state="normal" if self._audio_path else "disabled",
                text="Транскрибировать",
                command=self._start_transcription,
                fg_color=BLUE, hover_color=BLUE_DIM,
            )
        # Speaker-count menu mirrors the diarization-on-and-not-running rule.
        # Other settings widgets live in the (modal) Settings dialog and the
        # dialog blocks input while open, so they need no separate gating.
        if not running and self._diar_var.get():
            self._spk_count_menu.configure(state="normal")
        else:
            self._spk_count_menu.configure(state="disabled")

    def _request_cancel(self):
        """Set the cancel event and disable the button until the worker exits.

        We don't ``join`` the worker here — that would freeze the GUI. The
        worker thread sees the event within ~250 ms (its polling tick on
        the diarization subprocess, or the next segment boundary during
        Whisper inference), raises TranscriptionCancelled, and reaches
        ``_on_cancelled`` via ``after(0, ...)``.
        """
        if not self._is_running:
            return
        self._cancel_event.set()
        self._btn_transcribe.configure(state="disabled", text="Отмена...")
        self._lbl_status.configure(text="Отмена...", text_color=RED)

    def _start_transcription(self):
        if self._is_running or not self._audio_path:
            return

        # Reset cancel signal before each run; otherwise a Cancel from the
        # previous run would short-circuit the new one immediately.
        self._cancel_event.clear()
        self._set_running(True)
        self._textbox.delete("1.0", "end")
        self._btn_save.configure(state="disabled")
        self._btn_copy.configure(state="disabled")
        self._btn_extract_tasks.configure(state="disabled")
        self._progress.configure(mode="indeterminate", progress_color=BLUE)
        self._progress.start()
        self._lbl_status.configure(text="Подготовка...", text_color=TEXT_SECONDARY)

        lang_code = LANGUAGES[self._lang_var.get()]
        diarize = self._diar_var.get()
        saved_terms = self._config.get("hotwords", [])
        hotwords = ", ".join(saved_terms) if saved_terms else None

        # Speaker-count hint from the dropdown. SPEAKER_COUNTS maps the
        # visible label to a (num, min, max) triple; "Авто" is all-None and
        # lets the cloud provider auto-detect.
        num_speakers, min_speakers, max_speakers = SPEAKER_COUNTS.get(
            self._spk_count_var.get(), (None, None, None),
        )
        denoise_audio = bool(self._denoise_var.get())

        # Resolve the active cloud provider + API key. cloud is the ONLY
        # transcription mode after the 2026-05-28 rip-out — there is no
        # local fallback. The Settings UI gates the provider list to the
        # 4 surviving providers (AssemblyAI / Deepgram / Gladia /
        # Speechmatics), all of which support native diarization.
        #
        # Also pick up any unsaved key the user typed directly into the
        # field (the paste button auto-saves but manual typing doesn't)
        # and persist it under the active provider before kicking off the
        # worker — so a subsequent run after they hit the Транскрибировать
        # button without saving still sees the typed key.
        cloud_provider_name = self._cloud_provider_var.get()
        cloud_api_key = self._cloud_api_key_var.get().strip()
        if cloud_api_key and self._cloud_api_keys.get(cloud_provider_name) != cloud_api_key:
            self._cloud_api_keys[cloud_provider_name] = cloud_api_key
            self._config["cloud_api_keys"] = self._cloud_api_keys
            save_config(self._config)

        if not cloud_api_key:
            messagebox.showwarning(
                "Нужен API-ключ",
                f"API-ключ для {cloud_provider_name} не задан.\n\n"
                f"Открой Настройки → Транскрибация (cloud API) и вставь ключ.",
            )
            self._set_running(False)
            return

        # Transcriber takes no constructor args in the cloud-only build;
        # construct lazily on first run and reuse the instance across runs.
        if self._transcriber is None:
            self._transcriber = Transcriber()

        thread = threading.Thread(
            target=self._run_transcription,
            args=(
                self._audio_path, lang_code, diarize, hotwords,
                num_speakers, min_speakers, max_speakers,
                denoise_audio,
                cloud_provider_name, cloud_api_key,
            ),
            daemon=True,
        )
        thread.start()

    def _on_progress(self, percent: float):
        self.after(0, self._update_progress, percent)

    def _update_progress(self, percent: float):
        self._progress.set(percent / 100.0)
        if percent <= 70 and self._diar_var.get():
            self._lbl_status.configure(text=f"Транскрипция... {percent:.0f}%")
        elif percent > 70 and self._diar_var.get():
            self._lbl_status.configure(text=f"Диаризация... {percent:.0f}%")
        else:
            self._lbl_status.configure(text=f"Транскрипция... {percent:.0f}%")

    def _switch_to_determinate(self):
        self._progress.stop()
        self._progress.configure(mode="determinate")
        self._progress.set(0)

    def _on_status(self, text: str):
        self.after(0, self._lbl_status.configure, {"text": text})

    def _run_transcription(self, audio_path: str, language: str | None,
                           diarize: bool, hotwords: str | None,
                           num_speakers: int | None,
                           min_speakers: int | None,
                           max_speakers: int | None,
                           denoise_audio: bool,
                           cloud_provider: str, cloud_api_key: str):
        try:
            self.after(0, self._switch_to_determinate)
            self.after(0, self._lbl_status.configure,
                       {"text": f"Транскрипция через {cloud_provider}..."})

            text = self._transcriber.transcribe(
                audio_path,
                language=language,
                diarize=diarize,
                hotwords=hotwords,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                denoise_audio=denoise_audio,
                cloud_provider=cloud_provider,
                cloud_api_key=cloud_api_key,
                on_progress=self._on_progress,
                on_status=self._on_status,
                cancel_event=self._cancel_event,
            )
            self.after(0, self._on_complete, text)
        except TranscriptionCancelled:
            logger.info("transcription cancelled by user")
            self.after(0, self._on_cancelled)
        except Exception as e:
            # logger.exception writes the full traceback to logs/app.log via
            # the rotating handler. We additionally drop a structured one-shot
            # dump under logs/transcribe_crash_*.log so the user has a
            # clearly identifiable artifact to share when reporting the issue.
            logger.exception(
                "transcription failed (audio=%s, language=%s, diarize=%s)",
                audio_path, language, diarize,
            )
            log_hint = ""
            try:
                import traceback as _tb
                path = crash_log_path("transcribe_crash")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"audio_path: {audio_path}\n")
                    f.write(f"language: {language}\n")
                    f.write(f"diarize: {diarize}\n")
                    f.write(f"cloud_provider: {cloud_provider}\n")
                    f.write(f"exception: {type(e).__name__}: {e}\n")
                    f.write("=" * 60 + "\n")
                    _tb.print_exc(file=f)
                log_hint = f"\n\nПолный лог: {path}"
            except Exception:
                logger.exception("failed to write transcribe crash dump")
            self.after(0, self._on_error, f"{e}{log_hint}")

    def _on_complete(self, text: str):
        self._textbox.delete("1.0", "end")
        self._textbox.insert("1.0", text)
        self._progress.set(1.0)
        self._progress.configure(progress_color=GREEN)
        self._lbl_status.configure(text="Готово!", text_color=GREEN)
        self._btn_save.configure(state="normal")
        self._btn_copy.configure(state="normal")
        self._set_running(False)

        if self._audio_path:
            # `model` in the history entry now records the cloud provider
            # (the cloud-only build has no Whisper model picker). Falls
            # back to "cloud" if the var was somehow empty.
            self._last_history_folder = create_history_entry(
                audio_file_path=self._audio_path,
                transcript_text=text,
                language=LANGUAGES.get(self._lang_var.get()),
                model=self._cloud_provider_var.get() or "cloud",
            )

            # Persist raw segments so post-transcription speaker attribution
            # (directory feature) can slice per-speaker audio later. The audio
            # is copied into the folder, but the speaker timestamps are not.
            if self._last_history_folder and self._transcriber is not None:
                save_segments(self._last_history_folder, self._transcriber.last_segments)

            # Opt-in: drop the source recording now that the transcript is
            # saved and the audio is copied into the history folder. Guarded by
            # path-containment so only files inside the recordings dir are
            # touched. Best-effort — a delete failure must not break success.
            if should_delete_after_transcription(self._config, self._audio_path):
                try:
                    os.unlink(self._audio_path)
                    logger.info("deleted recording after transcription: %s", self._audio_path)
                except OSError as e:
                    logger.warning("could not delete recording %s: %s", self._audio_path, e)

        # Enable extract button only when we actually have a target folder.
        # Mirrors the conditional enable in _load_history_into_main.
        self._btn_extract_tasks.configure(
            state="normal" if self._last_history_folder else "disabled",
        )

    def _on_error(self, error_msg: str):
        self._lbl_status.configure(text="Ошибка", text_color=RED)
        self._progress.stop()
        self._progress.configure(mode="determinate", progress_color=BLUE)
        self._progress.set(0)
        messagebox.showerror("Ошибка транскрипции", error_msg)
        self._set_running(False)

    def _on_cancelled(self):
        self._lbl_status.configure(text="Отменено", text_color=TEXT_SECONDARY)
        self._progress.stop()
        self._progress.configure(mode="determinate", progress_color=BLUE)
        self._progress.set(0)
        self._set_running(False)
