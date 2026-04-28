"""Main App window — file selection, recording, transcription orchestration."""

from __future__ import annotations

import os
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk

from audio_cutter import AudioCutter
from logging_setup import crash_log_path, get_logger, init_logging
from recorder import Recorder
from theme import (
    BG, BLUE, BLUE_DIM, BLUE_SURFACE, BORDER, FONT, GREEN, INPUT_BG,
    PROGRESS_BG, RED, SURFACE, SURFACE_BRIGHT, TEXT_PRIMARY, TEXT_SECONDARY,
)
from transcriber import Transcriber, TranscriptionCancelled
from ui.dialogs.history import HistoryDialog
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.system_monitor import SystemMonitorDialog
from ui.dialogs.terms import TermsDialog
from ui.dialogs.voices import VoicesDialog
from ui.widgets import (
    card, label, option_menu, primary_button, tonal_button,
)
from utils import (
    check_ffmpeg, create_history_entry, get_output_path,
    load_config, save_config, save_transcript, validate_audio,
)

init_logging()
logger = get_logger(__name__)

LANGUAGES = {
    "Авто-определение": None,
    "Казахский": "kk",
    "Русский": "ru",
    "English": "en",
}

MODELS = {
    "small (быстрый)": "small",
    "medium (точный)": "medium",
    "large-v3 (максимум)": "large-v3",
}

# Speaker-count hint passed to pyannote diarization. Each value maps to one
# of three tuples: (num_speakers, min_speakers, max_speakers). A known exact
# count improves diarization error rate ~2× over pyannote's auto-detection.
# "5+" uses min_speakers so 6/7-way calls still work without a hard cap.
SPEAKER_COUNTS: dict[str, tuple[int | None, int | None, int | None]] = {
    "Авто": (None, None, None),
    "2": (2, None, None),
    "3": (3, None, None),
    "4": (4, None, None),
    "5+": (None, 5, None),
}

# Compute device choices.
#   "Авто"        — pick GPU when available, otherwise CPU. Silent fallback;
#                   right default for users who don't know what hardware
#                   they have.
#   "GPU (NVIDIA)" — explicit cuda. Hard-fails if no NVIDIA GPU found —
#                   we don't silently demote to CPU because the user picked
#                   GPU on purpose, and a 5-10× slower run with no warning
#                   would be confusing.
#   "CPU"         — explicit cpu. Always works. Slow on diarization
#                   (~10-20× slower than GPU); a warning label appears
#                   under the diarization device picker when this is chosen.
DEVICES: dict[str, str] = {
    "Авто": "auto",
    "GPU (NVIDIA)": "cuda",
    "CPU": "cpu",
}

# Visible label → CustomTkinter appearance_mode value.
# "system" follows the Windows light/dark setting; the other two are explicit.
APPEARANCE_MODES: dict[str, str] = {
    "Системная": "system",
    "Светлая": "light",
    "Тёмная": "dark",
}


class App(ctk.CTk):
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

        self._build_ui()

        # Token resolution order: config.json (set via "Вставить" button) →
        # HF_TOKEN env var. Env-sourced tokens are NOT written back to
        # config.json, so users who prefer env-only auth can keep their
        # secret out of disk state.
        saved_token = self._config.get("hf_token", "") or os.environ.get("HF_TOKEN", "")
        if saved_token:
            self._hf_token_var.set(saved_token)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)  # text result row

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=52)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="Audio Transcriber",
            font=ctk.CTkFont(family=FONT, size=17, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=24, pady=12)

        self._lbl_status = ctk.CTkLabel(
            header, text="", anchor="e",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_status.grid(row=0, column=1, padx=24, pady=12, sticky="e")

        # --- File card ---
        file_card = card(self)
        file_card.grid(row=1, column=0, padx=16, pady=(12, 6), sticky="ew")
        file_card.grid_columnconfigure(1, weight=1)

        self._btn_file = tonal_button(
            file_card, text="Выбрать файл", command=self._select_file, width=150,
        )
        self._btn_file.grid(row=0, column=0, padx=16, pady=14)

        self._lbl_file = label(file_card, text="Файл не выбран", anchor="w")
        self._lbl_file.grid(row=0, column=1, padx=(0, 12), pady=14, sticky="ew")

        self._btn_transcribe = primary_button(
            file_card, text="Транскрибировать",
            command=self._start_transcription, width=190, state="disabled",
        )
        self._btn_transcribe.grid(row=0, column=2, padx=16, pady=14)

        # --- Recorder card ---
        rec_card = card(self)
        rec_card.grid(row=2, column=0, padx=16, pady=6, sticky="ew")
        rec_card.grid_columnconfigure(2, weight=1)

        self._btn_rec = ctk.CTkButton(
            rec_card, text="⏺  Запись", width=130, height=40, corner_radius=20,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color="#D93025", hover_color="#B3261E", text_color="#FFFFFF",
            command=self._toggle_recording,
        )
        self._btn_rec.grid(row=0, column=0, padx=16, pady=14)

        self._btn_rec_pause = tonal_button(
            rec_card, text="Пауза", command=self._toggle_pause, width=100,
            state="disabled",
        )
        self._btn_rec_pause.grid(row=0, column=1, padx=(0, 8), pady=14)

        self._lbl_rec_time = label(rec_card, text="00:00", size=22, color=TEXT_PRIMARY)
        self._lbl_rec_time.grid(row=0, column=2, padx=8, pady=14, sticky="w")

        self._rec_level = ctk.CTkProgressBar(
            rec_card, height=8, corner_radius=4, width=180,
            fg_color=PROGRESS_BG, progress_color=GREEN,
        )
        self._rec_level.grid(row=0, column=3, padx=(8, 16), pady=14, sticky="e")
        self._rec_level.set(0)

        # --- Persistent state vars ---
        # All settings StringVar/BooleanVar live on App as the source of truth.
        # The Settings dialog binds widgets to these vars on demand; closing
        # the dialog destroys widgets but leaves vars (and config.json state)
        # intact, so _start_transcription always reads consistent values.
        saved_lang = self._config.get("language", "Авто-определение")
        self._lang_var = ctk.StringVar(
            value=saved_lang if saved_lang in LANGUAGES else "Авто-определение",
        )
        # Default to large-v3 (maximum quality). On this machine large-v3
        # int8_float16 is verified at ~1.5 GB VRAM and 5.7× realtime on GTX
        # 1650 Ti — fast enough to be the everyday default.
        saved_model = self._config.get("model", "large-v3 (максимум)")
        self._model_var = ctk.StringVar(
            value=saved_model if saved_model in MODELS else "large-v3 (максимум)",
        )
        self._diar_var = ctk.BooleanVar(value=False)
        self._hf_token_var = ctk.StringVar()
        saved_spk = self._config.get("speaker_count", "Авто")
        self._spk_count_var = ctk.StringVar(
            value=saved_spk if saved_spk in SPEAKER_COUNTS else "Авто",
        )
        self._normalize_var = ctk.BooleanVar(
            value=bool(self._config.get("normalize_audio", True)),
        )
        saved_tr_dev = self._config.get("transcribe_device", "Авто")
        self._tr_device_var = ctk.StringVar(
            value=saved_tr_dev if saved_tr_dev in DEVICES else "Авто",
        )
        saved_di_dev = self._config.get("diarize_device", "Авто")
        self._di_device_var = ctk.StringVar(
            value=saved_di_dev if saved_di_dev in DEVICES else "Авто",
        )

        # Cloud (managed-API) state. When _cloud_enabled_var is True, the
        # device pickers above are bypassed and transcription is routed to
        # the chosen provider. Default is False — opt-in only, since the
        # audio leaves the user's machine.
        self._cloud_enabled_var = ctk.BooleanVar(
            value=bool(self._config.get("cloud_enabled", False)),
        )
        self._cloud_provider_var = ctk.StringVar(
            value=self._config.get("cloud_provider", "AssemblyAI"),
        )
        self._cloud_api_key_var = ctk.StringVar(
            value=self._config.get("cloud_api_key", ""),
        )

        # Tasks pipeline (Phase 6.0+) — OpenRouter API key + default model slug.
        # The default model is persisted on every change via trace_add (the
        # CTk OptionMenu doesn't expose a `command=` for option selection that
        # reaches App's save_config, so a Var-level write trace is the cleanest
        # hook). The slug is stored as-is — Phase 6.4 may extend the curated
        # list with custom user-typed slugs prefixed `(custom) `.
        self._openrouter_key_var = ctk.StringVar(
            value=self._config.get("openrouter_api_key", ""),
        )
        self._openrouter_default_model_var = ctk.StringVar(
            value=self._config.get(
                "tasks_default_model", "anthropic/claude-sonnet-4.5",
            ),
        )
        self._openrouter_default_model_var.trace_add(
            "write", lambda *_: self._on_openrouter_default_model_changed(),
        )

        # Linear API key (Phase 6.0+). No team picker here — that's per-run
        # in the ExtractTasksDialog (Phase 6.1). Settings only persists the
        # key. The key is saved on Validate success, not on every keystroke
        # (same pattern as OpenRouter above and HF token below).
        self._linear_key_var = ctk.StringVar(
            value=self._config.get("linear_api_key", ""),
        )

        # Appearance mode (light/dark/system). The actual ctk.set_appearance_mode
        # call already happened above with the saved value; this StringVar
        # just drives the Settings dialog dropdown and the change callback.
        saved_appearance_label = self._config.get("appearance_mode", "Системная")
        self._appearance_var = ctk.StringVar(
            value=saved_appearance_label
            if saved_appearance_label in APPEARANCE_MODES else "Системная",
        )

        # --- Run controls card ---
        # Slim card with only per-run controls: diarization toggle, speaker
        # count hint, and the Settings button. Everything else (language,
        # model, HF token, normalize, devices, dictionaries) lives in the
        # Settings dialog, opened via the button on the right.
        run_card = card(self)
        run_card.grid(row=3, column=0, padx=16, pady=6, sticky="ew")
        run_card.grid_columnconfigure(2, weight=1)

        self._diar_check = ctk.CTkCheckBox(
            run_card, text="Диаризация",
            variable=self._diar_var, command=self._toggle_diarization,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        )
        self._diar_check.grid(row=0, column=0, padx=(16, 16), pady=14, sticky="w")

        # Speaker-count hint. Values map to pyannote hints:
        #   "Авто"  → no hint (pyannote auto-detects)
        #   "2".."4"→ num_speakers=K (exact hint; ~2× DER improvement when correct)
        #   "5+"    → min_speakers=5 (open upper bound)
        # Greyed out when diarize is off.
        label(run_card, "Число спикеров").grid(row=0, column=1, padx=(0, 8), pady=14)
        self._spk_count_menu = option_menu(
            run_card, self._spk_count_var, list(SPEAKER_COUNTS.keys()),
            command=self._on_speaker_count_changed, state="disabled",
        )
        self._spk_count_menu.grid(row=0, column=2, padx=(0, 12), pady=14, sticky="w")

        # Monitor button: opens the non-modal system stats window.
        # Sits to the LEFT of Settings — same row, two clickable buttons
        # at the right edge of the run card.
        self._btn_monitor = tonal_button(
            run_card, text="Монитор",
            command=self._open_monitor_dialog, width=110,
        )
        self._btn_monitor.grid(row=0, column=3, padx=(0, 8), pady=14, sticky="e")

        self._btn_settings = tonal_button(
            run_card, text="Настройки",
            command=self._open_settings_dialog, width=140,
        )
        self._btn_settings.grid(row=0, column=4, padx=(0, 16), pady=14, sticky="e")

        # --- Progress bar ---
        self._progress = ctk.CTkProgressBar(
            self, height=4, corner_radius=2,
            fg_color=PROGRESS_BG, progress_color=BLUE,
        )
        self._progress.grid(row=5, column=0, padx=16, pady=(10, 0), sticky="ew")
        self._progress.set(0)

        # --- Text result ---
        self._textbox = ctk.CTkTextbox(
            self, wrap="word", corner_radius=16,
            fg_color=SURFACE, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=14),
        )
        self._textbox.grid(row=6, column=0, padx=16, pady=(8, 8), sticky="nsew")

        # --- Action buttons ---
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=7, column=0, padx=16, pady=(0, 14), sticky="ew")

        self._btn_save = tonal_button(
            btn_frame, text="Сохранить (TXT/SRT/VTT)", command=self._save_txt,
            width=200, state="disabled",
        )
        self._btn_save.grid(row=0, column=0, padx=(0, 8), pady=4)

        self._btn_copy = tonal_button(
            btn_frame, text="Копировать", command=self._copy_text,
            width=150, state="disabled",
        )
        self._btn_copy.grid(row=0, column=1, padx=8, pady=4)

        self._btn_extract_tasks = tonal_button(
            btn_frame, text="Извлечь задачи",
            command=self._open_extract_tasks_dialog,
            width=160, state="disabled",
        )
        self._btn_extract_tasks.grid(row=0, column=2, padx=8, pady=4)

        self._btn_history = tonal_button(
            btn_frame, text="История", command=self._open_history_dialog,
            width=130,
        )
        self._btn_history.grid(row=0, column=3, padx=8, pady=4)

        self._btn_cutter = tonal_button(
            btn_frame, text="Audio Cutter", command=self._open_cutter,
            width=140,
        )
        self._btn_cutter.grid(row=0, column=4, padx=8, pady=4)

    # ── Dialog launchers ───────────────────────────────────────

    def _open_settings_dialog(self):
        # Track the open dialog so terms/voices saves can refresh its
        # summaries live. Cleared on close via Tk's <Destroy> event.
        if self._settings_dialog is not None:
            try:
                self._settings_dialog.lift()
                self._settings_dialog.focus_set()
                return
            except Exception:
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
            except Exception:
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
            except Exception:
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

    # ── Recorder controls ──────────────────────────────────────

    def _toggle_recording(self):
        """Start or stop recording."""
        if self._recorder.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        try:
            self._recorder.start()
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
            # Auto-load the recording for transcription
            self._audio_path = path
            self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
            self._btn_transcribe.configure(state="normal")
            elapsed = self._lbl_rec_time.cget("text")
            self._lbl_rec_time.configure(text=elapsed, text_color=GREEN)
            self._lbl_status.configure(
                text=f"Запись сохранена: {os.path.basename(path)}", text_color=GREEN,
            )

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

    # ── Settings handlers ─────────────────────────────────────

    def _paste_token_btn(self):
        """Handle paste via button click."""
        try:
            text = self.clipboard_get().strip()
            self._hf_token_var.set(text)
            if text:
                self._config["hf_token"] = text
                save_config(self._config)
        except Exception:
            pass

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

    def _on_cloud_provider_changed(self, value: str) -> None:
        self._config["cloud_provider"] = value
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
        Switch theme live and persist the choice.

        CustomTkinter's set_appearance_mode walks every CTk widget in the
        process and re-resolves its ``(light, dark)`` tuple colors — that
        covers the bulk of our UI. ``tk.Canvas`` instances (waveform,
        sparklines) don't speak tuples and need explicit redraw; we
        delegate that to each open child widget that knows about Canvas.
        """
        self._config["appearance_mode"] = value
        save_config(self._config)
        ctk.set_appearance_mode(APPEARANCE_MODES.get(value, "system"))
        # Notify Canvas-using children. None of these are required to be
        # open; the helper is a no-op when the dialog reference is None.
        if self._monitor_dialog is not None:
            try:
                self._monitor_dialog._apply_theme()
            except Exception:
                pass
        if self._cutter is not None:
            try:
                # Cutter window may have been closed already — winfo_exists
                # protects against AttributeError on a destroyed widget.
                if self._cutter.winfo_exists():
                    self._cutter._apply_theme()
            except Exception:
                pass

    def _paste_cloud_api_key(self) -> None:
        """Same paste-from-clipboard helper as the HF token, scoped to
        the cloud API key field."""
        try:
            text = self.clipboard_get().strip()
            self._cloud_api_key_var.set(text)
            if text:
                self._config["cloud_api_key"] = text
                save_config(self._config)
        except Exception:
            pass

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
        # to pick the local pipeline. When on, persist the API key the same
        # way HF Token is persisted (typed values aren't auto-saved).
        cloud_enabled = bool(self._cloud_enabled_var.get())
        cloud_api_key = self._cloud_api_key_var.get().strip()
        cloud_provider = self._cloud_provider_var.get() if cloud_enabled else None
        if cloud_enabled and cloud_api_key:
            self._config["cloud_api_key"] = cloud_api_key
            save_config(self._config)

        if cloud_enabled and not cloud_api_key:
            messagebox.showwarning(
                "Нужен API-ключ",
                "Облако включено, но API-ключ провайдера не задан.\n\n"
                "Открой Настройки → Облако и вставь ключ, либо выключи облако.",
            )
            self._set_running(False)
            return

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

    # ── Save / copy ──────────────────────────────────────────

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


def main():
    try:
        if not check_ffmpeg():
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "FFmpeg не найден",
                "Для работы приложения необходим FFmpeg.\n\n"
                "Установите его:\n"
                "1. Скачайте с https://ffmpeg.org/download.html\n"
                "2. Добавьте папку bin в переменную PATH\n"
                "3. Перезапустите приложение",
            )
            root.destroy()
            return

        app = App()
        app.mainloop()
    except Exception as e:
        logger.exception("fatal error in main()")
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Ошибка запуска", str(e))
            root.destroy()
        except Exception:
            print(f"Ошибка: {e}")
            input("Нажмите Enter для выхода...")
