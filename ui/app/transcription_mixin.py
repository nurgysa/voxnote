"""Transcription orchestration — start/cancel/progress/complete callbacks.

Extracted from ``ui/app/__init__.py`` (F4-PR-2f, final commit of the
ui/app split series). 11 methods covering the full transcription run
loop: ``_start_transcription`` (the orchestrator that gates on cloud
mode, builds the temp voice-library file, spawns the worker thread),
``_run_transcription`` (the daemon-thread worker with crash-dump
side-channel), and seven UI-callback methods that the worker schedules
back onto the Tk main thread via ``self.after(0, …)``.

Mixin contract: relies on App providing ``self._is_running``,
``self._audio_path``, ``self._transcriber`` (Transcriber | None — set
lazily here, recreated on model/device change), ``self._cancel_event``
(threading.Event), ``self._config`` (mutable dict), the persistence vars
``self._lang_var``, ``self._model_var``, ``self._diar_var``,
``self._hf_token_var``, ``self._spk_count_var``, ``self._normalize_var``,
``self._tr_device_var``, ``self._di_device_var``, ``self._cloud_*_var``,
``self._cloud_api_keys``, ``self._last_history_folder``, and the widget
refs ``self._btn_*``, ``self._lbl_*``, ``self._progress``, ``self._textbox``,
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
from utils import create_history_entry, save_config

from .constants import DEVICES, LANGUAGES, MODELS, SPEAKER_COUNTS

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
        self._lbl_status.configure(text="Загрузка модели...", text_color=TEXT_SECONDARY)

        lang_code = LANGUAGES[self._lang_var.get()]
        model_size = MODELS[self._model_var.get()]
        diarize = self._diar_var.get()
        hf_token = self._hf_token_var.get().strip() or None
        saved_terms = self._config.get("hotwords", [])
        hotwords = ", ".join(saved_terms) if saved_terms else None

        # Speaker-count hint from the dropdown. SPEAKER_COUNTS maps the
        # visible label to a (num, min, max) triple; "Авто" is all-None and
        # leaves pyannote's auto-detection in place.
        num_speakers, min_speakers, max_speakers = SPEAKER_COUNTS.get(
            self._spk_count_var.get(), (None, None, None),
        )
        normalize_audio = bool(self._normalize_var.get())

        # Voice library → temp JSON file for the diarize subprocess to read.
        # Written only when diarize=True AND voices exist; otherwise no path
        # is passed and the worker skips the matching step entirely. Temp
        # path is threaded through to _run_transcription so it gets unlinked
        # in the finally block regardless of outcome.
        voice_lib_path: str | None = None
        if diarize:
            from voice_library import voices_from_config
            if voices_from_config(self._config):
                import json
                import tempfile
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False,
                    encoding="utf-8", prefix="voicelib_",
                )
                try:
                    json.dump(
                        self._config.get("voices", []),
                        tmp, ensure_ascii=False,
                    )
                    tmp.flush()
                    voice_lib_path = tmp.name
                finally:
                    tmp.close()

        if hf_token:
            self._config["hf_token"] = hf_token
            save_config(self._config)

        # Resolve cloud-mode settings up front. cloud_provider is None when
        # the toggle is off — that's the signal Transcriber.transcribe() uses
        # to pick the local pipeline. When on, also pick up any unsaved key
        # the user typed directly into the field (paste-button auto-saves,
        # manual typing doesn't) and store it under the active provider.
        cloud_enabled = bool(self._cloud_enabled_var.get())
        cloud_provider_name = self._cloud_provider_var.get()
        cloud_api_key = self._cloud_api_key_var.get().strip()
        cloud_provider = cloud_provider_name if cloud_enabled else None
        if cloud_enabled and cloud_api_key:
            if self._cloud_api_keys.get(cloud_provider_name) != cloud_api_key:
                self._cloud_api_keys[cloud_provider_name] = cloud_api_key
                self._config["cloud_api_keys"] = self._cloud_api_keys
                save_config(self._config)

        if cloud_enabled and not cloud_api_key:
            messagebox.showwarning(
                "Нужен API-ключ",
                f"Облако включено, но API-ключ для {cloud_provider_name} "
                f"не задан.\n\nОткрой Настройки → Облако и вставь ключ, "
                f"либо выключи облако.",
            )
            self._set_running(False)
            return

        # Diarization gate: some providers (e.g. OpenAI Whisper) don't
        # return speaker labels. Ask the user whether to fall back to
        # transcription-only rather than silently dropping the request.
        if cloud_enabled and diarize:
            from providers import PROVIDERS
            provider_cls = PROVIDERS.get(cloud_provider_name)
            if (provider_cls is not None
                    and not provider_cls.supports_diarization):
                if not messagebox.askokcancel(
                    "Диаризация недоступна",
                    f"Провайдер {cloud_provider_name} не поддерживает "
                    f"определение спикеров. Продолжить без меток?",
                ):
                    self._set_running(False)
                    return
                diarize = False

        # HF Token is only required for the LOCAL diarization path. Cloud
        # providers (AssemblyAI, …) carry their own auth via cloud_api_key.
        if diarize and not hf_token and not cloud_enabled:
            messagebox.showwarning(
                "Нужен токен",
                "Для диаризации необходим Hugging Face токен.\n\n"
                "1. Зарегистрируйтесь на huggingface.co\n"
                "2. Примите условия модели pyannote/speaker-diarization-3.1\n"
                "3. Создайте токен в Settings → Access Tokens\n"
                "4. Вставьте токен в поле HF Token",
            )
            self._set_running(False)
            return

        # Resolve UI-label → backend device strings ("auto"/"cuda"/"cpu").
        # Persistence already happened on dropdown change; we just read here.
        transcribe_device = DEVICES[self._tr_device_var.get()]
        diarize_device = DEVICES[self._di_device_var.get()]

        # In cloud mode we don't load Whisper at all — the Transcriber object
        # is still used as the orchestrator (it handles the cloud branch
        # internally), but no GPU model is loaded. Skip the recreate logic
        # to avoid unnecessary churn between cloud-mode runs.
        if cloud_enabled:
            if self._transcriber is None:
                self._transcriber = Transcriber(
                    model_size=model_size, device=transcribe_device,
                )
        else:
            # Recreate Transcriber when model OR device changed — both are baked
            # into WhisperModel at load_model() time.
            needs_new_transcriber = (
                self._transcriber is None
                or self._transcriber.model_size != model_size
                or self._transcriber._device != transcribe_device
            )
            if needs_new_transcriber:
                self._transcriber = Transcriber(
                    model_size=model_size, device=transcribe_device,
                )

        thread = threading.Thread(
            target=self._run_transcription,
            args=(
                self._audio_path, lang_code, diarize, hf_token, hotwords,
                num_speakers, min_speakers, max_speakers, normalize_audio,
                voice_lib_path, diarize_device,
                cloud_provider, cloud_api_key,
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
                           diarize: bool = False, hf_token: str | None = None,
                           hotwords: str | None = None,
                           num_speakers: int | None = None,
                           min_speakers: int | None = None,
                           max_speakers: int | None = None,
                           normalize_audio: bool = True,
                           voice_lib_path: str | None = None,
                           diarize_device: str = "auto",
                           cloud_provider: str | None = None,
                           cloud_api_key: str | None = None):
        try:
            # In cloud mode we skip the local model load entirely. The
            # status line clarifies which path is running so the user
            # isn't surprised by "Загрузка модели..." that takes 0 s.
            if cloud_provider:
                self.after(0, self._switch_to_determinate)
                self.after(0, self._lbl_status.configure,
                           {"text": f"Транскрипция через {cloud_provider}..."})
            else:
                self.after(0, self._lbl_status.configure,
                           {"text": "Загрузка модели (первый раз может занять время)..."})
                self._transcriber.load_model()
                device_label = "GPU (CUDA)" if self._transcriber.device == "cuda" else "CPU"
                self.after(0, self._switch_to_determinate)
                self.after(0, self._lbl_status.configure,
                           {"text": f"Транскрипция на {device_label}..."})

            text = self._transcriber.transcribe(
                audio_path,
                language=language,
                diarize=diarize,
                diarize_device=diarize_device,
                hf_token=hf_token,
                hotwords=hotwords,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                voice_lib_path=voice_lib_path,
                normalize_audio=normalize_audio,
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
                    f.write(f"exception: {type(e).__name__}: {e}\n")
                    f.write("=" * 60 + "\n")
                    _tb.print_exc(file=f)
                log_hint = f"\n\nПолный лог: {path}"
            except Exception:
                logger.exception("failed to write transcribe crash dump")
            self.after(0, self._on_error, f"{e}{log_hint}")
        finally:
            # Clean up the voice library temp file regardless of outcome.
            if voice_lib_path:
                try:
                    os.unlink(voice_lib_path)
                except OSError:
                    pass

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
            self._last_history_folder = create_history_entry(
                audio_file_path=self._audio_path,
                transcript_text=text,
                language=LANGUAGES.get(self._lang_var.get()),
                model=MODELS.get(self._model_var.get(), ""),
            )

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
