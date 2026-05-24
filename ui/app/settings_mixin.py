"""Settings handlers — paste helpers, change callbacks, file picker.

Extracted from ``ui/app/__init__.py`` (F4-PR-2d). 16 methods that own the
"a settings dropdown / checkbox / button changed → persist to config and
maybe refresh widgets" surface. Includes ``_select_file`` (the audio-file
picker) for historical reasons — the original author grouped it under the
same section comment, and the method has identical persistence shape
(read a value, write to App state).

Mixin contract: relies on App providing ``self._config`` (mutable dict),
``self._hf_token_var``, ``self._diar_var``, ``self._spk_count_menu``,
``self._normalize_var``, ``self._cloud_*_var``, ``self._linear_*_var``,
``self._glide_*_var``, ``self._openrouter_*_var``, ``self._appearance_var``,
``self._transcriber`` (cleared on device change), ``self._cloud_api_keys``
(dict), ``self._audio_path``, ``self._lbl_file``, ``self._btn_transcribe``,
and the dialog refs ``self._settings_dialog`` / ``self._monitor_dialog`` /
``self._cutter`` (used by the live appearance-mode switch).
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from logging_setup import get_logger
from theme import TEXT_PRIMARY
from utils import save_config, validate_audio

from .constants import APPEARANCE_MODES

logger = get_logger(__name__)


class SettingsMixin:
    """Persistence-and-side-effect callbacks for the Settings dialog widgets."""

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

    # ── Google Drive (Phase 7.0) ────────────────────────────────────

    def _compute_gdrive_status_text(self) -> str:
        """Status badge text shown in the Settings dialog.

        Three states:
          - Signed in + email known   → "✓ Подключён к user@example.com"
          - Signed in + email unknown → "✓ Подключён"  (e.g. userinfo
            lookup failed during sign_in — token is still valid)
          - Not signed in             → "Не подключён"
        """
        if not self._gdrive_auth.is_signed_in():
            return "Не подключён"
        email = self._gdrive_auth.get_account_email()
        if email:
            return f"✓ Подключён к {email}"
        return "✓ Подключён"

    def _on_gdrive_signed_in(self) -> None:
        """Called from the Settings dialog after a successful sign-in.

        Updates the bound Vars + persists email + enabled flag to config.
        The dialog's worker thread routes here via self.after(0, ...) so
        this runs on the Tk main thread (safe to touch Vars + save).
        """
        email = self._gdrive_auth.get_account_email() or ""
        self._gdrive_account_email_var.set(email)
        self._gdrive_status_var.set(self._compute_gdrive_status_text())
        self._gdrive_enabled_var.set(True)
        self._config["gdrive_account_email"] = email
        self._config["gdrive_enabled"] = True
        save_config(self._config)

    def _on_gdrive_signed_out(self) -> None:
        """Called from the Settings dialog Выйти button handler.

        Mirrors _on_gdrive_signed_in in reverse: empty email, disable
        flag, persist. Also calls sign_out() on the auth instance so
        the token file is removed from disk.
        """
        self._gdrive_auth.sign_out()
        self._gdrive_account_email_var.set("")
        self._gdrive_status_var.set(self._compute_gdrive_status_text())
        self._gdrive_enabled_var.set(False)
        self._config["gdrive_account_email"] = ""
        self._config["gdrive_enabled"] = False
        save_config(self._config)

    def _on_gdrive_backup_succeeded(
        self,
        *,
        root_folder_id: str,
        snapshot_name: str,
    ) -> None:
        """Called from the Settings dialog after a successful backup.

        Persists two config keys:
          * gdrive_root_folder_id — cached so the NEXT backup skips
            the find_or_create_folder round-trip
          * gdrive_last_backup — ISO snapshot name, used by the
            Phase 7.3 scheduler's "is overdue?" check
        """
        self._config["gdrive_root_folder_id"] = root_folder_id
        self._config["gdrive_last_backup"] = snapshot_name
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
