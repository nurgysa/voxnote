"""Main App window — file selection, recording, transcription orchestration."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from audio_cutter import AudioCutter
from logging_setup import crash_log_path, get_logger, init_logging
from recorder import Recorder
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    GREEN,
    RED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from transcriber import Transcriber, TranscriptionCancelled
from ui.dialogs.history import HistoryDialog
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.system_monitor import SystemMonitorDialog
from ui.dialogs.terms import TermsDialog
from ui.dialogs.voices import VoicesDialog
from utils import (
    create_history_entry,
    load_config,
    save_config,
    validate_audio,
)

# Submodule re-exports. ``main`` lives in ``.main_entry`` so the repo-root
# ``app.py`` (the faulthandler bootstrap) keeps working through its existing
# ``from ui.app import main``. ``main_entry`` imports ``App`` lazily inside
# ``main()``, so this top-level import is safe — no circular load.
from .builder import build_ui
from .constants import (
    APPEARANCE_MODES,
    DEVICES,
    LANGUAGES,
    MODELS,
    SPEAKER_COUNTS,
)
from .main_entry import main as main
from .recorder_mixin import RecorderMixin
from .save_mixin import SaveMixin

init_logging()
logger = get_logger(__name__)


class App(RecorderMixin, SaveMixin, ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Audio Transcriber")
        self.geometry("780x700")
        self.minsize(680, 600)
        # Apply the saved appearance mode BEFORE constructing widgets so
        # tuple colors in theme.py resolve to the right palette on first
        # paint. Default "system" follows the OS setting. Persisted via
        # _on_appearance_changed when the user switches in Settings.
        saved_appearance = load_config().get("appearance_mode", "Системная")
        ctk.set_appearance_mode(
            APPEARANCE_MODES.get(saved_appearance, "system"),
        )
        self.configure(fg_color=BG)

        self._audio_path: str | None = None
        self._transcriber: Transcriber | None = None
        self._recorder = Recorder()
        self._is_running = False
        self._rec_timer_id: str | None = None
        self._config = load_config()
        # One-time migration: collapse the old single ``cloud_api_key``
        # string into a per-provider dict. Lets the user keep separate
        # keys for AssemblyAI/Deepgram/Gladia/etc. without re-entering
        # one when switching the dropdown. Old field is dropped.
        if "cloud_api_keys" not in self._config:
            legacy = self._config.pop("cloud_api_key", "")
            current = self._config.get("cloud_provider", "AssemblyAI")
            self._config["cloud_api_keys"] = (
                {current: legacy} if legacy else {}
            )
            save_config(self._config)
        self._cloud_api_keys: dict[str, str] = (
            self._config["cloud_api_keys"]
        )
        # Open Settings dialog reference (singleton). Lets terms/voices saves
        # refresh its summaries live; None when dialog is closed.
        self._settings_dialog: SettingsDialog | None = None
        # Open System Monitor dialog reference (singleton). Non-modal —
        # designed to stay open during transcription. Re-clicking the
        # button just brings the existing window to the front.
        self._monitor_dialog: SystemMonitorDialog | None = None
        # Most recently opened AudioCutter instance. Tracked only so that
        # _on_appearance_changed can ping it to redraw its Canvas — the
        # cutter is otherwise free to be reopened/recreated freely.
        self._cutter: AudioCutter | None = None
        # Cancel signal for the worker thread. Worker checks this between
        # segments and around the diarization subprocess; setting it
        # interrupts the run within ~250 ms.
        self._cancel_event = threading.Event()
        # Path to the most recent successful transcription's history folder.
        # Populated in _on_complete; consumed by _open_extract_tasks_dialog.
        self._last_history_folder: str | None = None

        build_ui(self)

        # Token resolution order: config.json (set via "Вставить" button) →
        # HF_TOKEN env var. Env-sourced tokens are NOT written back to
        # config.json, so users who prefer env-only auth can keep their
        # secret out of disk state.
        saved_token = self._config.get("hf_token", "") or os.environ.get("HF_TOKEN", "")
        if saved_token:
            self._hf_token_var.set(saved_token)

    # ── Dialog launchers ───────────────────────────────────────

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

    def _open_monitor_dialog(self) -> None:
        # Singleton: re-clicking the button while the monitor is open
        # just lifts the existing window. Avoids duplicate timer chains
        # and duplicate NVML handles competing for the same device.
        if self._monitor_dialog is not None:
            try:
                self._monitor_dialog.lift()
                self._monitor_dialog.focus_set()
                return
            except tk.TclError:
                # Same race as in _open_settings_dialog — dialog gone, refresh.
                self._monitor_dialog = None
        self._monitor_dialog = SystemMonitorDialog(self)
        self._monitor_dialog.bind(
            "<Destroy>", lambda _e: self._on_monitor_dialog_closed(_e),
        )

    def _on_monitor_dialog_closed(self, event) -> None:
        if event.widget is self._monitor_dialog:
            self._monitor_dialog = None

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

    def _open_voices_dialog(self):
        # Pass the CURRENT HF token value (from the field, may be unsaved).
        # Enrollment worker needs HF auth to download pyannote/embedding.
        hf_token = self._hf_token_var.get().strip() or None
        VoicesDialog(
            self, self._config, hf_token, self._refresh_settings_summaries,
        )

    def _open_history_dialog(self):
        HistoryDialog(self, on_load_to_main=self._load_history_into_main)

    def _open_extract_tasks_dialog(self):
        """Validate API keys are set, then open the Extract dialog."""
        # Gate-check: both keys must be present in config. Mirrors the
        # cloud-mode key check at line 790-797.
        openrouter_key = (self._config.get("openrouter_api_key") or "").strip()
        linear_key     = (self._config.get("linear_api_key") or "").strip()
        if not openrouter_key or not linear_key:
            messagebox.showwarning(
                "Нет API-ключей",
                "Извлечение задач требует двух ключей:\n"
                "  • OpenRouter — чтобы вызвать LLM\n"
                "  • Linear — чтобы получить список команд и участников\n\n"
                "Откройте Настройки и введите ключи.",
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
            self._btn_transcribe.configure(state="normal")
        self._lbl_status.configure(
            text="Загружено из истории", text_color=TEXT_SECONDARY,
        )

    def _open_cutter(self):
        # Track the most recent cutter so theme changes can repaint it.
        # AudioCutter doesn't need true singleton semantics — multiple
        # opens are fine — we just keep the latest reference.
        self._cutter = AudioCutter(self, audio_path=self._audio_path)

    # ── Settings handlers ─────────────────────────────────────

    def _paste_token_btn(self):
        """Handle paste via button click.

        TclError = empty clipboard or non-text content (silent — user just
        clicked Paste without anything to paste). OSError = config save
        failed (real problem: token won't persist across launches).
        """
        try:
            text = self.clipboard_get().strip()
            self._hf_token_var.set(text)
            if text:
                self._config["hf_token"] = text
                save_config(self._config)
        except tk.TclError:
            return
        except OSError as e:
            logger.warning("Failed to persist HF token to config.json: %s", e)

    def _toggle_diarization(self):
        # Only the speaker-count menu lives on the main window; HF Token and
        # device pickers were moved to the Settings dialog (own enable state).
        state = "normal" if self._diar_var.get() else "disabled"
        self._spk_count_menu.configure(state=state)

    def _on_speaker_count_changed(self, value: str) -> None:
        """Persist the dropdown choice immediately so it survives restarts."""
        self._config["speaker_count"] = value
        save_config(self._config)

    def _on_model_changed(self, value: str) -> None:
        self._config["model"] = value
        save_config(self._config)

    def _on_language_changed(self, value: str) -> None:
        self._config["language"] = value
        save_config(self._config)

    def _on_normalize_changed(self) -> None:
        """Persist the normalization toggle. BooleanVar supplies no arg."""
        self._config["normalize_audio"] = bool(self._normalize_var.get())
        save_config(self._config)

    def _on_transcribe_device_changed(self, value: str) -> None:
        """
        Persist the choice and invalidate the cached Transcriber.

        Device is baked into the WhisperModel at load_model() time, so a
        device change requires a fresh Transcriber. Setting to None here
        causes _start_transcription's existing reuse-or-recreate check to
        rebuild it with the new device on the next run.
        """
        self._config["transcribe_device"] = value
        save_config(self._config)
        self._transcriber = None

    def _on_diarize_device_changed(self, value: str) -> None:
        """
        Persist the choice. The CPU-slow warning lives in the Settings dialog
        and refreshes itself there; nothing to update on the main window.
        """
        self._config["diarize_device"] = value
        save_config(self._config)

    def _on_cloud_enabled_changed(self) -> None:
        """Persist the cloud toggle. No widget reshuffling needed — the
        Settings dialog rebuilds itself on next open, and _start_transcription
        reads the var directly when starting a job."""
        self._config["cloud_enabled"] = bool(self._cloud_enabled_var.get())
        save_config(self._config)

    def _on_linear_enabled_changed(self) -> None:
        """Persist the Linear-backend enabled flag (Phase 6.4).

        Phase 6.4.1 will read this in ExtractTasksDialog to filter the
        backend dropdown. For now, the flag is just persisted — no
        immediate UI effect (the dialog only shows Linear in any case)."""
        self._config["linear_enabled"] = bool(self._linear_enabled_var.get())
        save_config(self._config)

    def _on_glide_enabled_changed(self) -> None:
        """Persist the Glide-backend enabled flag (Phase 6.4)."""
        self._config["glide_enabled"] = bool(self._glide_enabled_var.get())
        save_config(self._config)

    def _on_cloud_provider_changed(self, value: str) -> None:
        self._config["cloud_provider"] = value
        # Swap the visible key field to the one stored for this provider
        # (empty if the user has never pasted one). The dict in
        # self._cloud_api_keys is the source of truth — the StringVar
        # only reflects the current selection.
        self._cloud_api_key_var.set(self._cloud_api_keys.get(value, ""))
        save_config(self._config)

    def _on_openrouter_default_model_changed(self) -> None:
        """Persist the OpenRouter default model slug on dropdown change.

        Triggered via StringVar `trace_add` because the CTk OptionMenu used
        in the OpenRouter section doesn't take a `command=` callback that we
        wire here directly. No arguments — we read the var inside.
        """
        self._config["tasks_default_model"] = self._openrouter_default_model_var.get()
        save_config(self._config)

    def _on_appearance_changed(self, value: str) -> None:
        """
        Live theme switch — close Settings dialog before applying.

        Background: earlier iterations made the user report the window
        freezing after a light→dark switch. Profiling showed Python work
        finishes in ~250ms, so set_appearance_mode itself is fast. The
        perceived freeze comes from CustomTkinter dropdown + the open
        Settings dialog struggling to repaint themselves in-place after
        the palette swap.

        Workaround: destroy the Settings dialog before flipping the
        appearance mode. The dialog holds no unsaved state — all its
        controls bind to vars on App that already persist to config.json.
        The user can reopen it; rendering fresh in the new theme is fast.
        """
        # Persist immediately so the choice survives even if Tk hits an
        # exception during the rest of this method.
        self._config["appearance_mode"] = value
        save_config(self._config)

        # Force-close Settings dialog — its in-place repaint is the main
        # contributor to the perceived freeze. Destroying it dismisses
        # the dropdown the user just clicked too.
        if self._settings_dialog is not None:
            try:
                self._settings_dialog.destroy()
            except tk.TclError:
                pass
            self._settings_dialog = None

        # Apply the actual theme change. Main window CTk widgets handle
        # this through CTk's appearance tracker — no manual redraw needed.
        ctk.set_appearance_mode(APPEARANCE_MODES.get(value, "system"))

        # Notify Canvas-using children — plain tk.Canvas doesn't react
        # to set_appearance_mode automatically.
        if self._monitor_dialog is not None:
            try:
                self._monitor_dialog._apply_theme()
            except tk.TclError:
                pass
        if self._cutter is not None:
            try:
                if self._cutter.winfo_exists():
                    self._cutter._apply_theme()
            except tk.TclError:
                pass

    def _paste_cloud_api_key(self) -> None:
        """Same paste-from-clipboard helper as the HF token, scoped to
        the cloud API key field. Persists into the per-provider dict
        under the *currently selected* provider name.

        See ``_paste_token_btn`` for exception-handling rationale.
        """
        try:
            text = self.clipboard_get().strip()
            self._cloud_api_key_var.set(text)
            if text:
                provider = self._cloud_provider_var.get()
                self._cloud_api_keys[provider] = text
                self._config["cloud_api_keys"] = self._cloud_api_keys
                save_config(self._config)
        except tk.TclError:
            return
        except OSError as e:
            logger.warning("Failed to persist cloud API key to config.json: %s", e)

    def _select_file(self):
        path = filedialog.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[("Audio files", "*.mp3 *.wav *.m4a"), ("All files", "*.*")],
        )
        if not path:
            return
        if not validate_audio(path):
            messagebox.showerror(
                "Ошибка",
                "Неподдерживаемый формат файла.\nПоддерживаются: MP3, WAV, M4A",
            )
            return
        self._audio_path = path
        self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
        self._btn_transcribe.configure(state="normal")

    # ── Transcription orchestration ───────────────────────────

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


