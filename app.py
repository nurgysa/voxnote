import faulthandler
import os
import sys
import threading
from tkinter import filedialog, messagebox, simpledialog

# Enable faulthandler BEFORE any C-extension imports. Catches SIGSEGV/SIGABRT/
# SIGFPE from native code (ctranslate2, torch, pyannote) and dumps the Python
# stack to logs/faulthandler.log before the process dies. Without this, a
# C-level crash during e.g. CUDA context teardown leaves no diagnostic trail —
# the app just silently vanishes.
_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)
_FAULT_LOG = open(os.path.join(_LOGS_DIR, "faulthandler.log"), "w", encoding="utf-8")
faulthandler.enable(file=_FAULT_LOG, all_threads=True)

import customtkinter as ctk

from audio_cutter import AudioCutter
from logging_setup import crash_log_path, get_logger, init_logging
from recorder import Recorder
from theme import (
    BG, BLUE, BLUE_DIM, BLUE_SURFACE, BORDER, FONT, GREEN, INPUT_BG,
    PROGRESS_BG, RED, SURFACE, SURFACE_BRIGHT, TEXT_PRIMARY, TEXT_SECONDARY,
)
from transcriber import Transcriber, TranscriptionCancelled
from utils import (
    check_ffmpeg, create_history_entry, delete_history_entry,
    get_output_path, list_history_entries, load_config,
    open_in_explorer, save_config, save_transcript, validate_audio,
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


class VoicesDialog(ctk.CTkToplevel):
    """Dialog for managing enrolled voices (CRUD)."""

    def __init__(self, parent, config: dict, hf_token: str | None, on_save):
        super().__init__(parent)
        self.title("Голоса")
        self.geometry("520x560")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._config = config
        self._hf_token = hf_token
        self._on_save = on_save
        self._enrolling = False  # guards against re-entry while worker runs
        # Recording state (separate Recorder instance — must not share with
        # the main window's recorder, which the user may have running).
        self._voice_recorder: Recorder | None = None
        self._rec_tmp_dir: str | None = None
        self._rec_tick_job: str | None = None  # after() handle

        # Recording state. The recorder owns its own output dir (a tempdir
        # created on start and torn down after enrollment succeeds/fails)
        # so samples never pollute the user's Documents folder.
        self._rec_recorder: Recorder | None = None
        self._rec_tempdir: str | None = None
        self._rec_pending_name: str | None = None
        self._rec_timer_id: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text="Голоса — библиотека спикеров",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12)

        # --- Add card ---
        add_card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12)
        add_card.grid(row=1, column=0, padx=16, pady=(12, 8), sticky="ew")
        add_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            add_card,
            text="Добавьте аудио-образец (10–30 с, один говорящий) и укажите имя.\n"
                 "Во время диаризации голос будет автоматически распознан по имени.",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY, justify="left", anchor="w",
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 6), sticky="ew")

        # Two buttons: record in-app OR import an existing file. Each
        # produces a WAV path that feeds the same enrollment pipeline.
        btn_row = ctk.CTkFrame(add_card, fg_color="transparent")
        btn_row.grid(row=1, column=0, columnspan=2, padx=12, pady=(4, 12), sticky="ew")
        btn_row.grid_columnconfigure(2, weight=1)

        self._record_btn = ctk.CTkButton(
            btn_row, text="⏺  Записать", height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color="#D93025", hover_color="#B3261E", text_color="#FFFFFF",
            command=self._toggle_record,
        )
        self._record_btn.grid(row=0, column=0, padx=(0, 8))

        self._enroll_btn = ctk.CTkButton(
            btn_row, text="Загрузить из файла", height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._start_enroll_from_file,
        )
        self._enroll_btn.grid(row=0, column=1, padx=(0, 8))

        self._status_lbl = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY, anchor="w",
        )
        self._status_lbl.grid(row=0, column=2, padx=(8, 0), sticky="ew")

        # --- List ---
        self._list_frame_outer = ctk.CTkScrollableFrame(
            self, fg_color=SURFACE, corner_radius=12,
        )
        self._list_frame_outer.grid(row=2, column=0, padx=16, pady=8, sticky="nsew")
        self._list_frame_outer.grid_columnconfigure(0, weight=1)

        self._render_list()

        # --- Footer ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(4, 14), sticky="ew")

        self._lbl_count = ctk.CTkLabel(
            footer, text="", font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_count.grid(row=0, column=0, sticky="w")
        self._update_count()

        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            footer, text="Готово", width=100, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._close,
        ).grid(row=0, column=1, sticky="e")

    def _render_list(self):
        for widget in self._list_frame_outer.winfo_children():
            widget.destroy()

        from voice_library import voices_from_config
        voices = voices_from_config(self._config)

        if not voices:
            ctk.CTkLabel(
                self._list_frame_outer,
                text="Нет сохранённых голосов. Добавьте образцы выше.",
                font=ctk.CTkFont(family=FONT, size=13),
                text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=20)
            return

        for i, voice in enumerate(voices):
            row = ctk.CTkFrame(
                self._list_frame_outer, fg_color=SURFACE_BRIGHT,
                corner_radius=10, height=44,
            )
            row.grid(row=i, column=0, padx=4, pady=3, sticky="ew")
            row.grid_columnconfigure(0, weight=1)

            created = voice.get("created_at") or ""
            subtitle = f"dim={voice['dim']}" + (f" · {created[:10]}" if created else "")

            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.grid(row=0, column=0, padx=12, pady=4, sticky="ew")
            inner.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                inner, text=voice["name"], anchor="w",
                font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
                text_color=TEXT_PRIMARY,
            ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                inner, text=subtitle, anchor="w",
                font=ctk.CTkFont(family=FONT, size=11),
                text_color=TEXT_SECONDARY,
            ).grid(row=1, column=0, sticky="w")

            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=14),
                fg_color="transparent", hover_color=BORDER,
                text_color=RED,
                command=lambda n=voice["name"]: self._delete_voice(n),
            ).grid(row=0, column=1, padx=(0, 8), pady=4)

    def _update_count(self):
        from voice_library import voice_names
        n = len(voice_names(self._config))
        self._lbl_count.configure(text=f"Голосов: {n}")

    def _delete_voice(self, name: str):
        from voice_library import remove_voice_from_config
        if remove_voice_from_config(self._config, name):
            save_config(self._config)
            self._on_save()
            self._render_list()
            self._update_count()

    def _start_enroll_from_file(self):
        if self._enrolling:
            return
        if not self._hf_token:
            messagebox.showwarning(
                "HF Token требуется",
                "Для извлечения эмбеддинга голоса нужен Hugging Face token.\n\n"
                "Введите токен в главном окне (галочка «Диаризация» → поле HF Token), "
                "сохраните настройки и откройте этот диалог заново.",
            )
            return

        path = filedialog.askopenfilename(
            title="Выберите аудио-образец голоса (10–30 с)",
            filetypes=[("Audio files", "*.mp3 *.wav *.m4a"), ("All files", "*.*")],
        )
        if not path:
            return

        name = simpledialog.askstring(
            "Имя спикера",
            "Как зовут этого спикера?",
            parent=self,
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return

        self._set_enrolling(True, f"Анализ голоса «{name}»...")
        thread = threading.Thread(
            target=self._run_enrollment,
            args=(path, name),
            daemon=True,
        )
        thread.start()

    def _set_enrolling(self, enrolling: bool, status: str = ""):
        self._enrolling = enrolling
        disabled = "disabled" if enrolling else "normal"
        self._enroll_btn.configure(state=disabled)
        # Recording and enrollment are mutually exclusive. The record button
        # is only re-enabled after enrollment finishes; during enrollment,
        # starting a recording would make the UI ambiguous.
        self._record_btn.configure(state=disabled)
        self._status_lbl.configure(
            text=status,
            text_color=BLUE if enrolling else TEXT_SECONDARY,
        )

    # ── In-app recording ────────────────────────────────────────

    def _toggle_record(self):
        """Start/stop recording. Same button acts as Record and Stop —
        visually matches the main window's record control pattern."""
        if self._enrolling:
            return
        if self._voice_recorder is None or not self._voice_recorder.is_recording:
            self._start_recording()
        else:
            self._stop_recording_and_enroll()

    def _start_recording(self):
        # Route recordings through a throwaway temp dir so they don't
        # pollute Documents. Recorder auto-names the file as
        # recording_<timestamp>.wav inside the given dir.
        import tempfile
        self._rec_tmp_dir = tempfile.mkdtemp(prefix="voice_enroll_")
        self._voice_recorder = Recorder(output_dir=self._rec_tmp_dir)
        try:
            self._voice_recorder.start()
        except Exception as e:
            messagebox.showerror(
                "Микрофон недоступен",
                f"Не удалось запустить запись:\n\n{e}",
            )
            self._cleanup_recording(delete_file=True)
            return

        self._record_btn.configure(text="⏹  Стоп", fg_color="#B3261E")
        self._enroll_btn.configure(state="disabled")
        self._status_lbl.configure(
            text="00:00 — говорите 10–30 секунд",
            text_color="#D93025",
        )
        self._tick_timer()

    def _tick_timer(self):
        """Update elapsed-time label every 500 ms while recording."""
        if self._voice_recorder is None or not self._voice_recorder.is_recording:
            return
        elapsed = int(self._voice_recorder.elapsed)
        m, s = divmod(elapsed, 60)
        self._status_lbl.configure(
            text=f"{m:02d}:{s:02d} — говорите 10–30 секунд",
        )
        self._rec_tick_job = self.after(500, self._tick_timer)

    def _stop_recording_and_enroll(self):
        if self._rec_tick_job is not None:
            try:
                self.after_cancel(self._rec_tick_job)
            except Exception:
                pass
            self._rec_tick_job = None

        recorder = self._voice_recorder
        if recorder is None:
            return
        try:
            wav_path = recorder.stop()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось остановить запись:\n\n{e}")
            self._cleanup_recording(delete_file=True)
            return

        self._record_btn.configure(text="⏺  Записать", fg_color="#D93025")
        self._enroll_btn.configure(state="normal")

        if not wav_path or not os.path.isfile(wav_path):
            self._status_lbl.configure(text="Запись не сохранена", text_color=RED)
            self._cleanup_recording(delete_file=True)
            return

        elapsed = recorder.elapsed
        # Require at least 5 seconds of speech. Under 5 s the embedding is
        # noisy enough that matching accuracy drops sharply — no point
        # saving it and discovering that later.
        if elapsed < 5.0:
            messagebox.showwarning(
                "Слишком коротко",
                f"Запись длится всего {elapsed:.1f} с. Нужно минимум 5 секунд "
                "непрерывной речи. Попробуйте ещё раз.",
            )
            self._status_lbl.configure(text="Запись слишком короткая", text_color=RED)
            self._cleanup_recording(delete_file=True)
            return

        name = simpledialog.askstring(
            "Имя спикера",
            f"Запись {elapsed:.1f} с сохранена. Как зовут этого спикера?",
            parent=self,
        )
        if not name or not name.strip():
            self._cleanup_recording(delete_file=True)
            self._status_lbl.configure(text="Отменено", text_color=TEXT_SECONDARY)
            return
        name = name.strip()

        # Hand off to the same enrollment worker the file-import path uses.
        # _run_enrollment deletes its own normalized temp WAV; we delete the
        # raw recording after the worker thread picks it up (via _cleanup).
        self._set_enrolling(True, f"Анализ голоса «{name}»...")
        thread = threading.Thread(
            target=self._run_enrollment_and_cleanup,
            args=(wav_path, name),
            daemon=True,
        )
        thread.start()

    def _run_enrollment_and_cleanup(self, audio_path: str, name: str):
        """Wrapper: run enrollment, then delete the source recording.
        Identical error handling to _run_enrollment."""
        try:
            self._run_enrollment(audio_path, name)
        finally:
            # Clean up the raw recording AND the temp dir. _run_enrollment
            # may have already succeeded by now — deletion is idempotent.
            self._cleanup_recording(delete_file=True)

    def _cleanup_recording(self, delete_file: bool):
        """Release recorder + remove temp dir. Safe to call repeatedly."""
        if self._voice_recorder is not None and self._voice_recorder.is_recording:
            try:
                self._voice_recorder.stop()
            except Exception:
                pass
        self._voice_recorder = None
        if delete_file and self._rec_tmp_dir and os.path.isdir(self._rec_tmp_dir):
            import shutil
            try:
                shutil.rmtree(self._rec_tmp_dir, ignore_errors=True)
            except Exception:
                pass
        self._rec_tmp_dir = None

    def _run_enrollment(self, audio_path: str, name: str):
        """Worker thread: ffmpeg normalize → enrollment subprocess → save."""
        import json as _json
        import subprocess as _subprocess
        import tempfile as _tempfile
        try:
            # Pre-process the sample through ensure_wav (normalization makes
            # low-input-volume samples comparable to higher-volume ones at
            # matching time — both detection and enrollment go through the
            # same filter chain, so the signal domain is consistent).
            from audio_io import ensure_wav
            wav_path, wav_is_temp = ensure_wav(audio_path, normalize=True)

            worker = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "enrollment_worker.py",
            )
            env = dict(os.environ)
            if self._hf_token:
                env["HF_TOKEN"] = self._hf_token

            try:
                proc = _subprocess.run(
                    [sys.executable, worker, wav_path],
                    env=env, capture_output=True, text=True,
                    encoding="utf-8", timeout=180,
                )
            finally:
                if wav_is_temp:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

            if proc.returncode != 0:
                raise RuntimeError(
                    f"enrollment_worker exit {proc.returncode}:\n"
                    f"{(proc.stderr or '')[-1500:]}"
                )

            data = _json.loads((proc.stdout or "").strip())
            import base64 as _b64
            import numpy as _np
            emb = _np.frombuffer(
                _b64.b64decode(data["embedding_b64"]), dtype=_np.float32,
            )

            from voice_library import save_voice_to_config
            save_voice_to_config(self._config, name, emb)
            save_config(self._config)

            self.after(0, self._on_enroll_success, name)
        except _subprocess.TimeoutExpired:
            self.after(0, self._on_enroll_fail,
                       "Таймаут: процесс извлечения эмбеддинга превысил 3 минуты.")
        except Exception as e:
            self.after(0, self._on_enroll_fail, str(e))

    def _on_enroll_success(self, name: str):
        self._set_enrolling(False, f"Голос «{name}» добавлен ✓")
        self._on_save()
        self._render_list()
        self._update_count()

    def _on_enroll_fail(self, message: str):
        self._set_enrolling(False, "")
        messagebox.showerror(
            "Не удалось извлечь эмбеддинг",
            f"Ошибка при анализе аудио-образца.\n\n{message[-2000:]}",
        )

    def _close(self):
        # Don't leak an active sounddevice stream if the user closes the
        # dialog mid-recording. Drop any in-progress recording and its temp
        # file — the user didn't confirm a name, so there's nothing to save.
        if self._rec_tick_job is not None:
            try:
                self.after_cancel(self._rec_tick_job)
            except Exception:
                pass
            self._rec_tick_job = None
        self._cleanup_recording(delete_file=True)
        self.grab_release()
        self.destroy()


class TermsDialog(ctk.CTkToplevel):
    """Dialog for managing saved hotwords/terms (CRUD)."""

    def __init__(self, parent, config: dict, on_save):
        super().__init__(parent)
        self.title("Словарь терминов")
        self.geometry("480x520")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._config = config
        self._on_save = on_save
        self._terms: list[str] = list(config.get("hotwords", []))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text="Словарь терминов",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12)

        # --- Add row ---
        add_frame = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12)
        add_frame.grid(row=1, column=0, padx=16, pady=(12, 8), sticky="ew")
        add_frame.grid_columnconfigure(0, weight=1)

        self._entry_var = ctk.StringVar()
        self._entry = ctk.CTkEntry(
            add_frame, textvariable=self._entry_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
            placeholder_text="Новый термин...",
        )
        self._entry.grid(row=0, column=0, padx=(12, 8), pady=12, sticky="ew")
        self._entry.bind("<Return>", lambda e: self._add_term())

        ctk.CTkButton(
            add_frame, text="Добавить", width=100, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._add_term,
        ).grid(row=0, column=1, padx=(0, 12), pady=12)

        # --- Terms list ---
        self._list_frame_outer = ctk.CTkScrollableFrame(
            self, fg_color=SURFACE, corner_radius=12,
        )
        self._list_frame_outer.grid(row=2, column=0, padx=16, pady=8, sticky="nsew")
        self._list_frame_outer.grid_columnconfigure(0, weight=1)

        self._render_list()

        # --- Footer ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(4, 14), sticky="ew")

        self._lbl_count = ctk.CTkLabel(
            footer, text="", font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_count.grid(row=0, column=0, sticky="w")
        self._update_count()

        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            footer, text="Готово", width=100, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._close,
        ).grid(row=0, column=1, sticky="e")

    def _render_list(self):
        for widget in self._list_frame_outer.winfo_children():
            widget.destroy()

        if not self._terms:
            ctk.CTkLabel(
                self._list_frame_outer, text="Нет сохранённых терминов",
                font=ctk.CTkFont(family=FONT, size=13),
                text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=20)
            return

        for i, term in enumerate(self._terms):
            row = ctk.CTkFrame(self._list_frame_outer, fg_color=SURFACE_BRIGHT, corner_radius=10, height=40)
            row.grid(row=i, column=0, padx=4, pady=3, sticky="ew")
            row.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                row, text=term, anchor="w",
                font=ctk.CTkFont(family=FONT, size=13),
                text_color=TEXT_PRIMARY,
            ).grid(row=0, column=0, padx=12, pady=8, sticky="ew")

            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=14),
                fg_color="transparent", hover_color=BORDER,
                text_color=RED, command=lambda idx=i: self._delete_term(idx),
            ).grid(row=0, column=1, padx=(0, 8), pady=4)

    def _add_term(self):
        term = self._entry_var.get().strip()
        if not term:
            return
        if term not in self._terms:
            self._terms.append(term)
            self._save()
            self._render_list()
            self._update_count()
        self._entry_var.set("")

    def _delete_term(self, index: int):
        self._terms.pop(index)
        self._save()
        self._render_list()
        self._update_count()

    def _update_count(self):
        n = len(self._terms)
        self._lbl_count.configure(text=f"Терминов: {n}")

    def _save(self):
        self._config["hotwords"] = self._terms
        save_config(self._config)
        self._on_save()

    def _close(self):
        self.grab_release()
        self.destroy()


def _read_transcript(folder_path: str) -> str:
    """Read transcript.txt from a history folder. Empty string on failure."""
    txt_path = os.path.join(folder_path, "transcript.txt")
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


class HistoryViewerDialog(ctk.CTkToplevel):
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
        textbox.insert("1.0", self._text or "(transcript.txt пуст или отсутствует)")
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
            defaultextension=".txt",
            initialfile="transcript.txt",
            filetypes=[("Text files", "*.txt")],
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


class HistoryDialog(ctk.CTkToplevel):
    """Browse transcription history — each entry is a folder on disk."""

    def __init__(self, parent, on_load_to_main):
        super().__init__(parent)
        self.title("История транскрипций")
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
            header, text="История транскрипций",
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
        self._lbl_count.configure(text=f"Записей: {len(entries)}{suffix}")

        if not entries:
            msg = "Ничего не найдено" if query else "Нет транскрипций"
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
                files.append("transcript.txt")
            files.append("description.md")
            meta = f"{date}   •   {', '.join(files)}"
            ctk.CTkLabel(
                row, text=meta, anchor="w",
                font=ctk.CTkFont(family=FONT, size=11),
                text_color=TEXT_SECONDARY,
            ).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")

            # View button (new) — only when transcript actually exists.
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
        HistoryViewerDialog(self, entry, self._on_load_to_main)

    def _delete_entry(self, folder_path: str):
        if messagebox.askyesno("Удалить", "Удалить эту запись из истории?"):
            delete_history_entry(folder_path)
            self._transcript_cache.pop(folder_path, None)
            self._all_entries = list_history_entries()
            self._render_entries()

    def _close(self):
        self.grab_release()
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Audio Transcriber")
        self.geometry("780x700")
        self.minsize(680, 600)
        ctk.set_appearance_mode("dark")

        self.configure(fg_color=BG)

        self._audio_path: str | None = None
        self._transcriber: Transcriber | None = None
        self._recorder = Recorder()
        self._is_running = False
        self._rec_timer_id: str | None = None
        self._config = load_config()
        # Cancel signal for the worker thread. Worker checks this between
        # segments and around the diarization subprocess; setting it
        # interrupts the run within ~250 ms.
        self._cancel_event = threading.Event()

        self._build_ui()

        # Token resolution order: config.json (set via "Вставить" button) →
        # HF_TOKEN env var. Env-sourced tokens are NOT written back to
        # config.json, so users who prefer env-only auth can keep their
        # secret out of disk state.
        saved_token = self._config.get("hf_token", "") or os.environ.get("HF_TOKEN", "")
        if saved_token:
            self._hf_token_var.set(saved_token)

    def _card(self, parent, **kwargs):
        return ctk.CTkFrame(
            parent, fg_color=SURFACE, corner_radius=16,
            border_width=0, **kwargs,
        )

    def _label(self, parent, text, size=13, color=TEXT_SECONDARY, **kwargs):
        return ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(family=FONT, size=size),
            text_color=color, **kwargs,
        )

    def _primary_btn(self, parent, text, command, width=160, **kwargs):
        return ctk.CTkButton(
            parent, text=text, command=command, width=width,
            height=40, corner_radius=20,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            **kwargs,
        )

    def _tonal_btn(self, parent, text, command, width=130, **kwargs):
        return ctk.CTkButton(
            parent, text=text, command=command, width=width,
            height=40, corner_radius=20,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
            text_color="#8AB4F8",
            **kwargs,
        )

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
        file_card = self._card(self)
        file_card.grid(row=1, column=0, padx=16, pady=(12, 6), sticky="ew")
        file_card.grid_columnconfigure(1, weight=1)

        self._btn_file = self._tonal_btn(
            file_card, text="Выбрать файл", command=self._select_file, width=150,
        )
        self._btn_file.grid(row=0, column=0, padx=16, pady=14)

        self._lbl_file = self._label(file_card, text="Файл не выбран", anchor="w")
        self._lbl_file.grid(row=0, column=1, padx=(0, 12), pady=14, sticky="ew")

        self._btn_transcribe = self._primary_btn(
            file_card, text="Транскрибировать",
            command=self._start_transcription, width=190, state="disabled",
        )
        self._btn_transcribe.grid(row=0, column=2, padx=16, pady=14)

        # --- Recorder card ---
        rec_card = self._card(self)
        rec_card.grid(row=2, column=0, padx=16, pady=6, sticky="ew")
        rec_card.grid_columnconfigure(2, weight=1)

        RED_BTN = "#D93025"
        RED_BTN_HOVER = "#B3261E"

        self._btn_rec = ctk.CTkButton(
            rec_card, text="⏺  Запись", width=130, height=40, corner_radius=20,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=RED_BTN, hover_color=RED_BTN_HOVER, text_color="#FFFFFF",
            command=self._toggle_recording,
        )
        self._btn_rec.grid(row=0, column=0, padx=16, pady=14)

        self._btn_rec_pause = self._tonal_btn(
            rec_card, text="Пауза", command=self._toggle_pause, width=100,
            state="disabled",
        )
        self._btn_rec_pause.grid(row=0, column=1, padx=(0, 8), pady=14)

        self._lbl_rec_time = self._label(rec_card, text="00:00", size=22, color=TEXT_PRIMARY)
        self._lbl_rec_time.grid(row=0, column=2, padx=8, pady=14, sticky="w")

        # Level meter
        self._rec_level = ctk.CTkProgressBar(
            rec_card, height=8, corner_radius=4, width=180,
            fg_color=PROGRESS_BG, progress_color=GREEN,
        )
        self._rec_level.grid(row=0, column=3, padx=(8, 16), pady=14, sticky="e")
        self._rec_level.set(0)

        # --- Settings card ---
        settings_card = self._card(self)
        settings_card.grid(row=3, column=0, padx=16, pady=6, sticky="ew")
        settings_card.grid_columnconfigure(1, weight=1)
        settings_card.grid_columnconfigure(4, weight=1)

        # Row 0: Language + Model
        self._label(settings_card, "Язык").grid(row=0, column=0, padx=(16, 8), pady=(14, 6))
        saved_lang = self._config.get("language", "Авто-определение")
        self._lang_var = ctk.StringVar(
            value=saved_lang if saved_lang in LANGUAGES else "Авто-определение",
        )
        self._lang_menu = ctk.CTkOptionMenu(
            settings_card, variable=self._lang_var, values=list(LANGUAGES.keys()),
            command=self._on_language_changed,
            width=175, height=36, corner_radius=10,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=INPUT_BG, button_color=BORDER, button_hover_color=BLUE_SURFACE,
            text_color=TEXT_PRIMARY, dropdown_fg_color=SURFACE_BRIGHT,
            dropdown_text_color=TEXT_PRIMARY, dropdown_hover_color=BLUE_SURFACE,
        )
        self._lang_menu.grid(row=0, column=1, padx=4, pady=(14, 6), sticky="w")

        self._label(settings_card, "Модель").grid(row=0, column=2, padx=(24, 8), pady=(14, 6))
        # Default to large-v3 (maximum quality). The earlier default was
        # "small" for fast first-time iteration, but on this machine large-v3
        # int8_float16 is verified at ~1.5 GB VRAM and 5.7× realtime on GTX
        # 1650 Ti — fast enough to be the everyday default. Persisted to
        # config.json so the user's choice survives restarts.
        saved_model = self._config.get("model", "large-v3 (максимум)")
        self._model_var = ctk.StringVar(
            value=saved_model if saved_model in MODELS else "large-v3 (максимум)",
        )
        self._model_menu = ctk.CTkOptionMenu(
            settings_card, variable=self._model_var, values=list(MODELS.keys()),
            command=self._on_model_changed,
            width=175, height=36, corner_radius=10,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=INPUT_BG, button_color=BORDER, button_hover_color=BLUE_SURFACE,
            text_color=TEXT_PRIMARY, dropdown_fg_color=SURFACE_BRIGHT,
            dropdown_text_color=TEXT_PRIMARY, dropdown_hover_color=BLUE_SURFACE,
        )
        self._model_menu.grid(row=0, column=3, columnspan=2, padx=4, pady=(14, 6), sticky="w")

        # Row 1: Diarization + HF Token
        self._diar_var = ctk.BooleanVar(value=False)
        self._diar_check = ctk.CTkCheckBox(
            settings_card, text="Диаризация",
            variable=self._diar_var, command=self._toggle_diarization,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4, checkbox_height=20, checkbox_width=20,
        )
        self._diar_check.grid(row=1, column=0, columnspan=2, padx=16, pady=(6, 14), sticky="w")

        self._label(settings_card, "HF Token").grid(row=1, column=2, padx=(24, 8), pady=(6, 14))
        self._hf_token_var = ctk.StringVar()
        self._hf_token_entry = ctk.CTkEntry(
            settings_card, textvariable=self._hf_token_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            placeholder_text="hf_...", state="disabled",
        )
        self._hf_token_entry.grid(row=1, column=3, padx=4, pady=(6, 14), sticky="ew")

        self._btn_paste = self._tonal_btn(
            settings_card, text="Вставить", command=self._paste_token_btn,
            width=100, state="disabled",
        )
        self._btn_paste.grid(row=1, column=4, padx=(4, 16), pady=(6, 14), sticky="w")

        # Row 2: Speaker count hint (diarization only).
        # Values map to pyannote hints:
        #   "Авто"  → no hint (pyannote auto-detects)
        #   "2".."4"→ num_speakers=K (exact hint; ~2× DER improvement when correct)
        #   "5+"    → min_speakers=5 (open upper bound)
        # Persisted to config.json on change. Grey when diarize is off.
        self._label(settings_card, "Число спикеров").grid(
            row=2, column=0, padx=(16, 8), pady=(0, 14),
        )
        saved_spk = self._config.get("speaker_count", "Авто")
        self._spk_count_var = ctk.StringVar(
            value=saved_spk if saved_spk in SPEAKER_COUNTS else "Авто",
        )
        self._spk_count_menu = ctk.CTkOptionMenu(
            settings_card, variable=self._spk_count_var,
            values=list(SPEAKER_COUNTS.keys()),
            command=self._on_speaker_count_changed,
            width=175, height=36, corner_radius=10,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=INPUT_BG, button_color=BORDER, button_hover_color=BLUE_SURFACE,
            text_color=TEXT_PRIMARY, dropdown_fg_color=SURFACE_BRIGHT,
            dropdown_text_color=TEXT_PRIMARY, dropdown_hover_color=BLUE_SURFACE,
            state="disabled",
        )
        self._spk_count_menu.grid(row=2, column=1, padx=4, pady=(0, 14), sticky="w")

        # Loudness-normalization toggle (EBU R128 + 80 Hz highpass, applied
        # to all source formats before transcription). Default ON — it's
        # the production-quality default. User can disable for pre-mastered
        # material where the filter would do more harm than good (clean
        # studio recording, already-normalized podcast rip). Persisted to
        # config.json. Independent of diarization, so no grey-out coupling.
        self._normalize_var = ctk.BooleanVar(
            value=bool(self._config.get("normalize_audio", True)),
        )
        self._normalize_check = ctk.CTkCheckBox(
            settings_card, text="Нормализовать громкость",
            variable=self._normalize_var, command=self._on_normalize_changed,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        )
        self._normalize_check.grid(
            row=2, column=2, columnspan=3,
            padx=(24, 16), pady=(0, 14), sticky="w",
        )

        # --- Hotwords card ---
        hw_card = self._card(self)
        hw_card.grid(row=4, column=0, padx=16, pady=6, sticky="ew")
        hw_card.grid_columnconfigure(1, weight=1)

        self._btn_terms = self._tonal_btn(
            hw_card, text="Словарь терминов",
            command=self._open_terms_dialog, width=180,
        )
        self._btn_terms.grid(row=0, column=0, padx=16, pady=(14, 6))

        self._lbl_terms = self._label(hw_card, text="", anchor="w")
        self._lbl_terms.grid(row=0, column=1, padx=(0, 16), pady=(14, 6), sticky="ew")
        self._update_terms_label()

        # Voices button: analogous to the terms button, in the same card.
        # Unlike hotwords (CTC-level biasing), enrolled voices rename
        # diarization clusters post-hoc — independent feature, but visually
        # they're both "dictionaries" the user maintains between sessions.
        self._btn_voices = self._tonal_btn(
            hw_card, text="Голоса", command=self._open_voices_dialog, width=180,
        )
        self._btn_voices.grid(row=1, column=0, padx=16, pady=(6, 14))

        self._lbl_voices = self._label(hw_card, text="", anchor="w")
        self._lbl_voices.grid(row=1, column=1, padx=(0, 16), pady=(6, 14), sticky="ew")
        self._update_voices_label()

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

        self._btn_save = self._tonal_btn(
            btn_frame, text="Сохранить (TXT/SRT/VTT)", command=self._save_txt,
            width=200, state="disabled",
        )
        self._btn_save.grid(row=0, column=0, padx=(0, 8), pady=4)

        self._btn_copy = self._tonal_btn(
            btn_frame, text="Копировать", command=self._copy_text,
            width=150, state="disabled",
        )
        self._btn_copy.grid(row=0, column=1, padx=8, pady=4)

        self._btn_history = self._tonal_btn(
            btn_frame, text="История", command=self._open_history_dialog,
            width=130,
        )
        self._btn_history.grid(row=0, column=2, padx=8, pady=4)

        self._btn_cutter = self._tonal_btn(
            btn_frame, text="Audio Cutter", command=self._open_cutter,
            width=140,
        )
        self._btn_cutter.grid(row=0, column=3, padx=8, pady=4)

    def _open_terms_dialog(self):
        TermsDialog(self, self._config, self._update_terms_label)

    def _update_terms_label(self):
        terms = self._config.get("hotwords", [])
        if terms:
            preview = ", ".join(terms[:5])
            if len(terms) > 5:
                preview += f"  ... (+{len(terms) - 5})"
            self._lbl_terms.configure(text=preview, text_color=TEXT_PRIMARY)
        else:
            self._lbl_terms.configure(text="Нет сохранённых терминов", text_color=TEXT_SECONDARY)

    def _open_voices_dialog(self):
        # Pass the CURRENT HF token value (from the field, may be unsaved).
        # Enrollment worker needs HF auth to download pyannote/embedding.
        hf_token = self._hf_token_var.get().strip() or None
        VoicesDialog(self, self._config, hf_token, self._update_voices_label)

    def _update_voices_label(self):
        from voice_library import voice_names
        names = voice_names(self._config)
        if names:
            preview = ", ".join(names[:5])
            if len(names) > 5:
                preview += f"  ... (+{len(names) - 5})"
            self._lbl_voices.configure(text=preview, text_color=TEXT_PRIMARY)
        else:
            self._lbl_voices.configure(
                text="Нет сохранённых голосов", text_color=TEXT_SECONDARY,
            )

    # ── Recorder controls ──────────────────────────────────────

    def _toggle_recording(self):
        """Start or stop recording."""
        if self._recorder.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        try:
            path = self._recorder.start()
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

    # ── Other controls ────────────────────────────────────────

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
        state = "normal" if self._diar_var.get() else "disabled"
        self._hf_token_entry.configure(state=state)
        self._btn_paste.configure(state=state)
        self._spk_count_menu.configure(state=state)

    def _on_speaker_count_changed(self, value: str) -> None:
        """Persist the dropdown choice immediately so it survives restarts."""
        self._config["speaker_count"] = value
        save_config(self._config)

    def _on_model_changed(self, value: str) -> None:
        """Persist the selected model so it survives restarts."""
        self._config["model"] = value
        save_config(self._config)

    def _on_language_changed(self, value: str) -> None:
        """Persist the selected language so it survives restarts."""
        self._config["language"] = value
        save_config(self._config)

    def _on_normalize_changed(self) -> None:
        """Persist the normalization toggle. BooleanVar supplies no arg."""
        self._config["normalize_audio"] = bool(self._normalize_var.get())
        save_config(self._config)

    def _select_file(self):
        path = filedialog.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[("Audio files", "*.mp3 *.wav *.m4a"), ("All files", "*.*")],
        )
        if not path:
            return
        if not validate_audio(path):
            messagebox.showerror("Ошибка", "Неподдерживаемый формат файла.\nПоддерживаются: MP3, WAV, M4A")
            return
        self._audio_path = path
        self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
        self._btn_transcribe.configure(state="normal")

    def _set_running(self, running: bool):
        self._is_running = running
        state = "disabled" if running else "normal"
        self._btn_file.configure(state=state)
        self._lang_menu.configure(state=state)
        self._model_menu.configure(state=state)
        self._diar_check.configure(state=state)
        self._normalize_check.configure(state=state)
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
        if not running and self._diar_var.get():
            self._hf_token_entry.configure(state="normal")
            self._btn_paste.configure(state="normal")
            self._spk_count_menu.configure(state="normal")
        else:
            self._hf_token_entry.configure(state="disabled")
            self._btn_paste.configure(state="disabled")
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
                import tempfile
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False,
                    encoding="utf-8", prefix="voicelib_",
                )
                try:
                    # Write only the minimal fields the worker needs.
                    import json
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

        if diarize and not hf_token:
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

        if self._transcriber is None or self._transcriber.model_size != model_size:
            self._transcriber = Transcriber(model_size=model_size)

        thread = threading.Thread(
            target=self._run_transcription,
            args=(
                self._audio_path, lang_code, diarize, hf_token, hotwords,
                num_speakers, min_speakers, max_speakers, normalize_audio,
                voice_lib_path,
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
                           voice_lib_path: str | None = None):
        try:
            self.after(0, self._lbl_status.configure, {"text": "Загрузка модели (первый раз может занять время)..."})
            self._transcriber.load_model()

            device_label = "GPU (CUDA)" if self._transcriber.device == "cuda" else "CPU"
            self.after(0, self._switch_to_determinate)
            self.after(0, self._lbl_status.configure, {"text": f"Транскрипция на {device_label}..."})
            text = self._transcriber.transcribe(
                audio_path,
                language=language,
                diarize=diarize,
                hf_token=hf_token,
                hotwords=hotwords,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                voice_lib_path=voice_lib_path,
                normalize_audio=normalize_audio,
                on_progress=self._on_progress,
                on_status=self._on_status,
                cancel_event=self._cancel_event,
            )
            self.after(0, self._on_complete, text)
        except TranscriptionCancelled:
            logger.info("transcription cancelled by user")
            self.after(0, self._on_cancelled)
        except Exception as e:
            # logger.exception writes the full traceback to logs/app.log
            # via the rotating handler. We additionally drop a structured
            # one-shot dump under logs/transcribe_crash_*.log so the user
            # has a clearly identifiable artifact to share when reporting
            # the issue — the rotating log can be many KB of unrelated noise.
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
            # Never raises — diagnostics/cleanup must not mask the real error.
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
            create_history_entry(
                audio_file_path=self._audio_path,
                transcript_text=text,
                language=LANGUAGES.get(self._lang_var.get()),
                model=MODELS.get(self._model_var.get(), ""),
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

    def _save_txt(self):
        text = self._textbox.get("1.0", "end").strip()
        if not text:
            return
        default_path = get_output_path(self._audio_path) if self._audio_path else "transcript.txt"
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
        # (e.g. they typed text into the box manually), fall back to plain
        # text and warn — a silent .srt with one giant cue would be useless.
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
            self._lbl_status.configure(text="Скопировано в буфер обмена", text_color=TEXT_SECONDARY)

    def _open_history_dialog(self):
        HistoryDialog(self, on_load_to_main=self._load_history_into_main)

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
        AudioCutter(self, audio_path=self._audio_path)


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
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Ошибка запуска", str(e))
            root.destroy()
        except Exception:
            print(f"Ошибка: {e}")
            input("Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
