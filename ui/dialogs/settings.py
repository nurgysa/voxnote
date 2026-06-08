"""Settings dialog — collects all rarely-changed configuration in one window.

Per-run controls (Diarization toggle, Speaker count) stay on the main window.
This dialog owns the persistent cloud-only settings: language, audio
normalization, cloud provider + API key, plus the LLM-side OpenRouter /
Linear / Glide / Google Drive integrations. Whisper-model / GPU-device /
HF-token / voice-library entries were removed in the 2026-05-28 rip-out.

State model: the App owns the StringVar/BooleanVar instances; widgets here
bind to them directly. Closing the dialog destroys widgets but leaves the
vars untouched, so subsequent transcribe() calls read the right values.
The existing _on_*_changed callbacks on App fire on every change and persist
to config.json — no extra save logic needed here.
"""

from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from providers import PROVIDERS
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    GREEN,
    INPUT_BG,
    RED,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.app.constants import LANGUAGES
from ui.dialogs.settings_helpers import (
    compute_banner_state,
    format_glide_success,
    format_linear_success,
    format_openrouter_success,
    format_trello_success,
)
from ui.widgets import (
    api_key_row,
    card,
    label,
    option_menu,
    primary_button,
    tonal_button,
)
from utils import get_meetings_dir, save_config

_logger = logging.getLogger(__name__)

# Curated dropdown for OpenRouter default model. Slug → display label.
# Display label keeps the slug visible — power users recognize 'sonnet-4.5'
# faster than 'Anthropic Claude Sonnet 4.5 (latest)'.
_CURATED_MODELS = {
    "google/gemini-3.5-flash":        "google/gemini-3.5-flash",
}


class SettingsDialog(ctk.CTkToplevel):
    """Modal settings dialog. Mirrors the structure of TermsDialog."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Настройки")
        # 640 wide — fits the 4-widget API-key row (label + entry + 👁 +
        # Проверить + status) without status-label truncation at 520.
        self.geometry("640x680")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._parent = parent  # App instance — we read its StringVars

        self.grid_columnconfigure(0, weight=1)
        # row 0=header, 1=banner (hidden until needed), 2=tabview (expands),
        # 3=footer. Only the tabview row gets weight so the banner stays at
        # natural height and the footer pins to the bottom.
        self.grid_rowconfigure(2, weight=1)

        # --- Header (thin divider strip — title is in the OS title bar) ---
        # We intentionally do NOT duplicate "Настройки" as an in-body H1:
        # the OS title bar already shows it via self.title("Настройки"),
        # and the inline label was a leftover from pre-redesign layout.
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=8)
        header.grid(row=0, column=0, sticky="ew")

        # --- First-run status banner (between header and tabs) ---
        # Clickable: jumps to the relevant tab + focuses the relevant widget.
        # State machine in _update_banner; click dispatch in _handle_banner_click.
        # Default hidden — _update_banner shows it when a condition fires.
        self._banner_action: str | None = None
        self._banner = ctk.CTkButton(
            self,
            text="",
            command=self._handle_banner_click,
            fg_color="transparent",
            hover_color=SURFACE,
            text_color=RED,
            anchor="w",
            font=ctk.CTkFont(family=FONT, size=12),
            corner_radius=0,
            height=32,
        )
        self._banner.grid(row=1, column=0, padx=12, pady=(0, 4), sticky="ew")
        self._banner.grid_remove()

        # --- Tab view ---
        # CTkTabview inherits Light/Dark from the theme palette automatically
        # (unlike ttk.Notebook which needs manual ttk.Style for each mode).
        self._tabview = ctk.CTkTabview(
            self,
            fg_color="transparent",
            segmented_button_fg_color=SURFACE,
            segmented_button_selected_color=BLUE,
            segmented_button_selected_hover_color=BLUE_DIM,
            segmented_button_unselected_color=SURFACE,
            text_color=TEXT_PRIMARY,
        )
        self._tabview.grid(row=2, column=0, padx=12, pady=(4, 4), sticky="nsew")

        tab_transcription = self._tabview.add("Транскрипция")
        tab_integrations = self._tabview.add("Интеграции")
        tab_backup = self._tabview.add("Резервная копия")

        # Each tab wraps its content in a CTkScrollableFrame so taller
        # sections (Tab 1 has 5) don't clip when the dialog is shrunk.
        # The tab itself owns the scrollbar; sections grid into the
        # inner scroll frame at rows 0..N.
        for tab in (tab_transcription, tab_integrations, tab_backup):
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)

        scroll_transcription = ctk.CTkScrollableFrame(
            tab_transcription, fg_color="transparent", corner_radius=0,
        )
        scroll_transcription.grid(row=0, column=0, sticky="nsew")
        scroll_transcription.grid_columnconfigure(0, weight=1)

        scroll_integrations = ctk.CTkScrollableFrame(
            tab_integrations, fg_color="transparent", corner_radius=0,
        )
        scroll_integrations.grid(row=0, column=0, sticky="nsew")
        scroll_integrations.grid_columnconfigure(0, weight=1)

        scroll_backup = ctk.CTkScrollableFrame(
            tab_backup, fg_color="transparent", corner_radius=0,
        )
        scroll_backup.grid(row=0, column=0, sticky="nsew")
        scroll_backup.grid_columnconfigure(0, weight=1)

        # Default tab = where the STT key lives. First-run client lands here.
        self._tabview.set("Транскрипция")

        # Tab 1 «Транскрипция» — core loop (minimal sufficient set)
        self._build_appearance_section(scroll_transcription)
        self._build_transcription_section(scroll_transcription)
        self._build_audio_section(scroll_transcription)
        self._build_cloud_section(scroll_transcription)
        self._build_meetings_section(scroll_transcription)
        self._build_dictionaries_section(scroll_transcription)

        # Tab 2 «Интеграции» — LLM-side optional extras
        self._build_openrouter_section(scroll_integrations)
        self._build_linear_section(scroll_integrations)
        self._build_glide_section(scroll_integrations)
        self._build_trello_section(scroll_integrations)

        # Tab 3 «Резервная копия» — independent housekeeping
        self._build_gdrive_section(scroll_backup)
        self._build_diagnostics_section(scroll_backup)

        # Reactive banner: subscribe to the three vars whose values
        # determine the banner state. Tokens kept on self so destroy()
        # can unregister them (PR #25 pattern, extended).
        self._trace_lang = self._parent._lang_var.trace_add(
            "write", self._update_banner,
        )
        self._trace_provider = self._parent._cloud_provider_var.trace_add(
            "write", self._update_banner,
        )
        self._trace_api_key = self._parent._cloud_api_key_var.trace_add(
            "write", self._update_banner,
        )
        # Run once at end of __init__ so an already-loaded config (empty
        # STT key, mixed lang + Deepgram, etc.) surfaces the banner
        # immediately — not only after the first interaction.
        self._update_banner()

        # --- Footer ---
        # "Закрыть" is a cancel action, not the primary CTA — use tonal_button
        # so visual weight matches actual importance.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(4, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        tonal_button(
            footer, text="Закрыть", command=self.destroy, width=120,
        ).grid(row=0, column=0, sticky="e")

        # Esc closes — standard modal-dialog convention.
        self.bind("<Escape>", lambda _e: self.destroy())

        # CTkToplevel quirk: immediate iconbitmap() is silently dropped if
        # the WM hasn't finished its handshake. Defer 200 ms so the call
        # lands after Windows has assigned the WM_CLASS / icon slot.
        self.after(200, self._apply_dialog_icon)

    def destroy(self) -> None:
        """Remove app-level Var traces before the Toplevel is torn down.

        Var trace tokens live on the App and outlive the dialog. Without
        cleanup, reopening Settings registers a second trace pointing at
        the previous (destroyed) dialog's bound method — fires both, the
        stale one raises TclError on the destroyed widget, and the destroyed
        dialog instance is held alive by the trace (memory leak).

        Wrapped in try/except TclError because the underlying Var may have
        already been GC'd during parent teardown.

        Task 7 of the redesign plan adds new trace tokens for the global
        banner. Each token is opt-in via getattr — this loop accepts both
        pre-Task-7 (empty) and post-Task-7 (populated) states.
        """
        for var, token in (
            (self._parent._lang_var, getattr(self, "_trace_lang", None)),
            (self._parent._cloud_provider_var, getattr(self, "_trace_provider", None)),
            (self._parent._cloud_api_key_var, getattr(self, "_trace_api_key", None)),
        ):
            if token is not None:
                try:
                    var.trace_remove("write", token)
                except tk.TclError:
                    # Var already destroyed during parent teardown — safe to ignore.
                    pass
        super().destroy()

    def _apply_dialog_icon(self) -> None:
        """Apply the audio_transcriber.ico to this Toplevel.

        Called via `self.after(200, ...)` to work around the CTkToplevel
        WM-handshake race that silently drops immediate `iconbitmap()` calls
        on Windows. The 200 ms delay is empirically sufficient for all
        Windows 10/11 builds we test on.

        Swallows TclError + ImportError + FileNotFoundError — on
        Linux/macOS .ico is unsupported, but the dialog still opens fine.
        """
        try:
            from utils import get_app_icon_path
            icon_path = get_app_icon_path()
            if icon_path:
                self.iconbitmap(icon_path)
        except (tk.TclError, ImportError, FileNotFoundError):
            pass

    # ── First-run banner state machine + click handlers ────────────────

    def _update_banner(self, *_args) -> None:
        """Show the highest-priority actionable banner, or hide it.

        The decision tree lives in settings_helpers.compute_banner_state
        (priority: empty STT key → mixed-language-unsupported-provider →
        none); this wrapper applies the widget side (always-RED colour +
        grid()/grid_remove()).

        Subscribed to `_cloud_api_key_var`, `_lang_var`, `_cloud_provider_var`
        via `trace_add("write", ...)`. Also called once at the end of
        `__init__` so a pre-loaded config that already has the issue
        surfaces the banner immediately.
        """
        action, text = compute_banner_state(
            cloud_key=self._parent._cloud_api_key_var.get(),
            lang_label=self._parent._lang_var.get(),
            provider_name=self._parent._cloud_provider_var.get(),
            languages=LANGUAGES,
            providers=PROVIDERS,
        )
        if action is None:
            self._banner.grid_remove()
            self._banner_action = None
            return
        self._banner.configure(text=text, text_color=RED)
        self._banner_action = action
        self._banner.grid()

    def _handle_banner_click(self) -> None:
        """Banner is clickable — dispatch by current action."""
        if self._banner_action == "stt":
            self._jump_to_stt()
        elif self._banner_action == "lang":
            self._jump_to_lang()

    def _jump_to_stt(self) -> None:
        """Switch to Транскрипция tab + focus the STT API key entry."""
        self._tabview.set("Транскрипция")
        # _cloud_api_key_entry is captured in _build_cloud_section.
        entry = getattr(self, "_cloud_api_key_entry", None)
        if entry is not None:
            entry.focus_set()

    def _jump_to_lang(self) -> None:
        """Switch to Транскрипция tab + focus the Язык dropdown."""
        self._tabview.set("Транскрипция")
        menu = getattr(self, "_lang_menu", None)
        if menu is not None:
            menu.focus_set()

    def _section_card(self, parent, title: str, row: int) -> ctk.CTkFrame:
        """A titled card. Returns the inner content frame (already gridded)."""
        wrapper = card(parent)
        wrapper.grid(row=row, column=0, padx=4, pady=8, sticky="ew")
        wrapper.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            wrapper, text=title,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            text_color=TEXT_SECONDARY,
        ).grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")
        inner = ctk.CTkFrame(wrapper, fg_color="transparent")
        inner.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")
        inner.grid_columnconfigure(1, weight=1)
        return inner

    def _build_appearance_section(self, parent) -> None:
        # Lazy import — APPEARANCE_MODES lives in ui.app, importing at
        # module-load would create a circular dependency.
        from ui.app import APPEARANCE_MODES

        section = self._section_card(parent, "Внешний вид", row=0)

        label(section, "Тема").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._appearance_var, list(APPEARANCE_MODES.keys()),
            command=self._parent._on_appearance_changed,
        ).grid(row=0, column=1, padx=4, pady=6, sticky="w")
        label(
            section,
            "«Системная» следует за настройкой Windows (Light/Dark mode).",
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, padx=4, pady=(0, 4), sticky="w")

    def _build_transcription_section(self, parent) -> None:
        section = self._section_card(parent, "Транскрипция", row=1)

        label(section, "Язык").grid(row=0, column=0, padx=(4, 8), pady=6, sticky="w")
        # Capture ref so the banner's _jump_to_lang can focus_set() it.
        self._lang_menu = option_menu(
            section, self._parent._lang_var, list(LANGUAGES.keys()),
            command=self._parent._on_language_changed,
        )
        self._lang_menu.grid(row=0, column=1, padx=4, pady=6, sticky="w")

    def _build_audio_section(self, parent) -> None:
        section = self._section_card(parent, "Аудио", row=2)
        check = ctk.CTkCheckBox(
            section, text="Нормализовать громкость (EBU R128 + 80 Hz HPF)",
            variable=self._parent._normalize_var,
            command=self._parent._on_normalize_changed,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        )
        check.grid(row=0, column=0, columnspan=2, padx=4, pady=6, sticky="w")

        # RNNoise (arnndn) — opt-in noise suppression. Default off; the
        # neural denoiser can clip soft consonants on already-clean
        # recordings. ~85 KB model lazy-downloaded on first use.
        denoise_check = ctk.CTkCheckBox(
            section, text="Подавлять шум (RNNoise — для записей с фоном)",
            variable=self._parent._denoise_var,
            command=self._parent._on_denoise_changed,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        )
        denoise_check.grid(
            row=1, column=0, columnspan=2, padx=4, pady=6, sticky="w",
        )

    def _build_cloud_section(self, parent) -> None:
        """Cloud STT provider + API key + privacy + pricing disclosure.

        Key handling via api_key_row (no validate — Cloud STT validation
        is deferred to a follow-up PR per the spec). Provider dropdown
        stays as a separate row because its callback wires into the
        first-run banner's mixed-lang condition. Captures
        self._cloud_api_key_entry so the banner can focus_set() it.
        """
        section = self._section_card(parent, "Облачное распознавание", row=3)

        # Provider dropdown
        label(section, "Провайдер").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._cloud_provider_var, list(PROVIDERS.keys()),
            command=self._parent._on_cloud_provider_changed,
        ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

        # API key row — no validate (deferred). Capture entry ref so the
        # global first-run banner can focus_set() it on click.
        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._cloud_api_key_var,
            placeholder="API ключ провайдера",
            on_validate=None,
            row=1,
        )
        self._cloud_api_key_entry = refs["entry"]

        # Disclosure: audio leaves the user's machine and ends up on a
        # third-party server. Surfacing this in the cloud section is the
        # cheapest mitigation. Placed AFTER the key field so it reads
        # as context for the field it applies to.
        label(
            section,
            "⚠ Аудио загружается на сервер провайдера. "
            "Не используй для конфиденциальных записей.",
            anchor="w",
        ).grid(row=3, column=0, columnspan=4, padx=4, pady=(2, 6), sticky="w")
        # Static price summary. Cheapest with diarization first.
        label(
            section,
            "ℹ Цены с диаризацией: AssemblyAI ~$0.17/ч • "
            "Deepgram ~$0.43/ч • Gladia ~$0.61/ч • "
            "Speechmatics ~$1.04/ч.",
            anchor="w",
        ).grid(row=4, column=0, columnspan=4, padx=4, pady=(0, 4), sticky="w")

    def _build_meetings_section(self, parent) -> None:
        """Meetings folder picker — path entry + Выбрать + Default + stats.

        On path change: triggers MigrationPromptDialog if the current
        folder has entries (mode="settings"). Otherwise silent save.
        """
        section = self._section_card(parent, "Митинги", row=4)

        label(section, "Папка хранения").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )

        self._meetings_path_var = ctk.StringVar(value=get_meetings_dir())
        self._meetings_entry = ctk.CTkEntry(
            section, textvariable=self._meetings_path_var,
            height=36, corner_radius=10,
            border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            state="readonly",
        )
        self._meetings_entry.grid(
            row=0, column=1, columnspan=2, padx=4, pady=6, sticky="ew",
        )

        tonal_button(
            section, text="\U0001f4c1 Выбрать",
            command=self._on_pick_meetings_folder, width=130,
        ).grid(row=0, column=3, padx=(4, 4), pady=6)

        tonal_button(
            section, text="↻ Default",
            command=self._on_reset_meetings_folder, width=120,
        ).grid(row=1, column=3, padx=(4, 4), pady=(0, 6))

        # Stats label — refreshed on dialog open and after path change
        self._meetings_stats_label = label(section, "", anchor="w")
        self._meetings_stats_label.grid(
            row=1, column=0, columnspan=3, padx=4, pady=(0, 6), sticky="w",
        )
        self._refresh_meetings_stats()

    def _refresh_meetings_stats(self) -> None:
        """Compute «В этой папке: N митингов • X GB» and update the label."""
        from meetings_migration import count_meetings
        from ui.dialogs.migration import _fmt_size, _folder_size_bytes
        path = self._meetings_path_var.get()
        n = count_meetings(path)
        size = _folder_size_bytes(path)
        self._meetings_stats_label.configure(
            text=f"В этой папке: {n} митингов • {_fmt_size(size)}",
        )

    def _on_pick_meetings_folder(self) -> None:
        """User clicked «Выбрать» — open native dir picker, maybe migrate."""
        chosen = filedialog.askdirectory(
            title="Папка для хранения митингов",
            initialdir=self._meetings_path_var.get(),
            parent=self,
        )
        if not chosen:
            return  # user cancelled the picker

        current = self._meetings_path_var.get()
        normalized = os.path.abspath(chosen)
        if normalized == os.path.abspath(current):
            return  # no-op

        from meetings_migration import count_meetings
        if count_meetings(current) > 0:
            # Ask whether to migrate
            from ui.dialogs.migration import MigrationPromptDialog
            MigrationPromptDialog(
                self,
                src=current, dst=normalized, mode="settings",
                on_choice=lambda choice: self._on_migrate_choice(
                    choice, current, normalized,
                ),
            )
        else:
            # Empty current folder — silent switch
            self._save_meetings_path(normalized)

    def _on_migrate_choice(
        self, choice: str, src: str, dst: str,
    ) -> None:
        if choice == "migrate":
            from ui.dialogs.migration import MigrationProgressDialog
            MigrationProgressDialog(
                self, src=src, dst=dst,
                on_done=lambda summary: self._on_migration_done(summary, dst),
            )
        elif choice == "switch_only":
            self._save_meetings_path(dst)

    def _on_migration_done(self, summary: dict, new_path: str) -> None:
        """Worker finished. Persist new path + refresh stats."""
        self._save_meetings_path(new_path)

    def _save_meetings_path(self, path: str) -> None:
        self._parent._config["meetings_dir"] = path
        save_config(self._parent._config)
        self._meetings_path_var.set(path)
        self._refresh_meetings_stats()

    def _on_reset_meetings_folder(self) -> None:
        """↻ Default — clear config[meetings_dir], resolver falls back."""
        self._parent._config["meetings_dir"] = ""
        save_config(self._parent._config)
        new_path = get_meetings_dir()
        self._meetings_path_var.set(new_path)
        self._refresh_meetings_stats()

    def _build_dictionaries_section(self, parent) -> None:
        section = self._section_card(parent, "Словари", row=5)

        tonal_button(
            section, text="Словарь терминов",
            command=self._parent._open_terms_dialog, width=200,
        ).grid(row=0, column=0, padx=4, pady=6, sticky="w")
        # Compact summary of what's saved — same source as the main-window
        # label (kept in sync via _update_terms_label, which we reuse below).
        self._terms_summary = label(section, "", anchor="w")
        self._terms_summary.grid(row=0, column=1, padx=(8, 4), pady=6, sticky="ew")

        self._refresh_summaries()

    def _refresh_summaries(self) -> None:
        """Mirror App's existing summary-rendering for terms.

        Pulls the current string via parent helpers if they exist, otherwise
        falls back to a plain count. Keeps this dialog independent of internal
        helper signatures while still showing live data. Voices summary was
        removed in the 2026-05-28 rip-out (voice library deleted).
        """
        terms = self._parent._config.get("hotwords", []) or []
        if terms:
            preview = ", ".join(terms[:5])
            if len(terms) > 5:
                preview += f", … (+{len(terms) - 5})"
            self._terms_summary.configure(text=preview)
        else:
            self._terms_summary.configure(text="Нет сохранённых терминов")

    # ── OpenRouter section (Phase 6.0 Task 13) ────────────────────────

    def _build_openrouter_section(self, parent) -> None:
        """OpenRouter API key + default model.

        Key handling delegated to ui.widgets.api_key_row (entry + eye-toggle
        + Проверить + status). Default-model dropdown stays as a separate
        row below.
        """
        section = self._section_card(parent, "OpenRouter", row=0)

        def _persist(key: str, _info: dict) -> None:
            self._parent._config["openrouter_api_key"] = key
            save_config(self._parent._config)

        def _on_validate(key: str) -> dict:
            # Lazy import — keeps tasks/openrouter_client (and transitively
            # requests) off the dialog-construction path.
            from tasks.openrouter_client import OpenRouterClient
            client = OpenRouterClient(key)
            try:
                return client.validate_key()
            finally:
                client.close()

        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._openrouter_key_var,
            placeholder="sk-or-...",
            on_validate=_on_validate,
            on_key_persisted=_persist,
            format_success=format_openrouter_success,
            row=0,
        )
        self._openrouter_status = refs["status"]

        # Default model dropdown (unchanged — separate row below the key)
        label(section, "Модель по умолчанию").grid(
            row=2, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._openrouter_default_model_var,
            list(_CURATED_MODELS.keys()),
        ).grid(row=2, column=1, columnspan=2, padx=4, pady=6, sticky="ew")

    # ── Linear section (Phase 6.0 Task 15) ────────────────────────────

    def _build_linear_section(self, parent) -> None:
        """Linear API key + connection status.

        enable-checkbox + API key handling delegated to api_key_row.
        No team picker here — that's per-run in ExtractTasksDialog.
        """
        section = self._section_card(parent, "Linear", row=1)

        def _persist(key: str, _info: dict) -> None:
            self._parent._config["linear_api_key"] = key
            save_config(self._parent._config)

        def _on_validate(key: str) -> dict:
            from tasks.linear_client import LinearClient
            client = LinearClient(key)
            try:
                return client.validate_key()
            finally:
                client.close()

        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._linear_key_var,
            placeholder="lin_api_...",
            on_validate=_on_validate,
            on_key_persisted=_persist,
            enabled_var=self._parent._linear_enabled_var,
            enabled_label="Использовать Linear",
            on_enabled_changed=self._parent._on_linear_enabled_changed,
            format_success=format_linear_success,
            row=0,
        )
        self._linear_status = refs["status"]

    def _build_glide_section(self, parent) -> None:
        """Glide API key + connection status (Phase 6.4).

        Mirrors the Linear section pattern (enable-checkbox + validate
        through api_key_row).
        """
        section = self._section_card(parent, "Glide", row=2)

        def _persist(key: str, _info: dict) -> None:
            self._parent._config["glide_api_key"] = key
            save_config(self._parent._config)

        def _on_validate(key: str) -> dict:
            from tasks.glide_client import GlideClient
            client = GlideClient(key)
            try:
                return client.validate_key()
            finally:
                client.close()

        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._glide_key_var,
            placeholder="glide_pk_<workspace>_...",
            on_validate=_on_validate,
            on_key_persisted=_persist,
            enabled_var=self._parent._glide_enabled_var,
            enabled_label="Использовать Glide",
            on_enabled_changed=self._parent._on_glide_enabled_changed,
            format_success=format_glide_success,
            row=0,
        )
        self._glide_status = refs["status"]

    def _build_trello_section(self, parent) -> None:
        """Trello API key + token + connection status (spec 2026-05-29).

        Trello needs two secrets (key + token). The shared api_key_row
        helper renders one masked field, so we compose two calls:
        - key row: enable-checkbox + masked key field (no Validate)
        - token row: masked token field + Validate + status badge

        api_key_row only persists on Validate success, and only the token
        row has a Validate button — so the token row's _persist saves BOTH
        credentials, and its _on_validate reads BOTH vars.
        """
        section = self._section_card(parent, "Trello", row=3)

        key_frame = ctk.CTkFrame(section, fg_color="transparent")
        key_frame.grid(row=0, column=0, sticky="ew")
        key_frame.grid_columnconfigure(1, weight=1)

        token_frame = ctk.CTkFrame(section, fg_color="transparent")
        token_frame.grid(row=1, column=0, sticky="ew")
        token_frame.grid_columnconfigure(1, weight=1)

        def _persist(_token: str, _info: dict) -> None:
            self._parent._config["trello_api_key"] = self._parent._trello_key_var.get().strip()
            self._parent._config["trello_token"] = self._parent._trello_token_var.get().strip()
            save_config(self._parent._config)

        def _on_validate(token: str) -> dict:
            from tasks.trello_client import TrelloClient
            api_key = self._parent._trello_key_var.get().strip()
            client = TrelloClient(api_key, token)
            try:
                return client.validate_key()
            finally:
                client.close()

        # Key row — owns the enable-checkbox; no Validate button.
        api_key_row(
            key_frame,
            label_text="API ключ",
            key_var=self._parent._trello_key_var,
            placeholder="(ключ Trello — trello.com/app-key)",
            enabled_var=self._parent._trello_enabled_var,
            enabled_label="Использовать Trello",
            on_enabled_changed=self._parent._on_trello_enabled_changed,
            row=0,
        )

        # Token row — owns Validate + status; persists both credentials.
        refs = api_key_row(
            token_frame,
            label_text="Токен",
            key_var=self._parent._trello_token_var,
            placeholder="(токен Trello)",
            on_validate=_on_validate,
            on_key_persisted=_persist,
            format_success=format_trello_success,
            row=0,
        )
        self._trello_status = refs["status"]

    # ── Google Drive section (Phase 7.0) ──────────────────────────────

    def _build_gdrive_section(self, parent) -> None:
        """Google Drive backup: sign-in/out + status badge.

        Phase 7.0 surface only — no backup-now button (7.1), no
        frequency dropdown (7.3), no audio opt-in (7.4). Adding those
        widgets later just extends this method.

        Threading: sign_in() blocks while the browser is open; we run
        it in a daemon thread and route the result back to the Tk loop
        via `self.after(0, ...)` so widget updates happen on the main
        thread. Mirrors the _validate_openrouter pattern.
        """
        section = self._section_card(parent, "Google Drive", row=0)

        # Status row — badge bound to the App's _gdrive_status_var.
        label(section, "Статус").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        self._gdrive_status_label = ctk.CTkLabel(
            section,
            textvariable=self._parent._gdrive_status_var,
            anchor="w",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._gdrive_status_label.grid(
            row=0, column=1, columnspan=2, padx=4, pady=6, sticky="ew",
        )

        # Action row — Войти + Выйти (one of them disabled at any time).
        self._gdrive_signin_btn = primary_button(
            section, text="Войти через Google",
            command=self._handle_gdrive_signin, width=180,
        )
        self._gdrive_signin_btn.grid(
            row=1, column=0, columnspan=2, padx=4, pady=6, sticky="w",
        )

        self._gdrive_signout_btn = tonal_button(
            section, text="Выйти",
            command=self._handle_gdrive_signout, width=100,
        )
        self._gdrive_signout_btn.grid(row=1, column=2, padx=(4, 4), pady=6, sticky="e")

        # Backup-now row (Phase 7.1) — button + status label. Status is
        # local (not bound to a parent Var) because backup status is a
        # transient dialog-only concern; persistence of
        # gdrive_last_backup happens on success via the mixin callback.
        self._gdrive_backup_btn = tonal_button(
            section, text="Сделать backup сейчас",
            command=self._handle_gdrive_backup_now, width=200,
        )
        self._gdrive_backup_btn.grid(
            row=2, column=0, columnspan=2, padx=4, pady=6, sticky="w",
        )
        self._gdrive_backup_status = label(section, "", anchor="w")
        self._gdrive_backup_status.grid(
            row=2, column=2, padx=(8, 4), pady=6, sticky="ew",
        )

        # Initial button enabled-state reflects current sign-in state.
        self._refresh_gdrive_button_state()

    def _refresh_gdrive_button_state(self) -> None:
        """Войти is enabled iff not signed in; Выйти + Сделать backup
        iff signed in. Called after every state change so the UI
        matches the GDriveAuth state."""
        if self._parent._gdrive_auth.is_signed_in():
            self._gdrive_signin_btn.configure(state="disabled")
            self._gdrive_signout_btn.configure(state="normal")
            self._gdrive_backup_btn.configure(state="normal")
        else:
            self._gdrive_signin_btn.configure(state="normal")
            self._gdrive_signout_btn.configure(state="disabled")
            self._gdrive_backup_btn.configure(state="disabled")

    def _handle_gdrive_signin(self) -> None:
        """Войти clicked — spawn a worker that runs sign_in() (blocks on
        browser). Disable the button immediately so double-click can't
        spawn two flows."""
        self._gdrive_signin_btn.configure(state="disabled", text="Открываю браузер...")

        def worker():
            try:
                self._parent._gdrive_auth.sign_in()
                self.after(0, self._on_gdrive_signin_success)
            except Exception as e:   # any OAuth failure: network, user cancel, GCP misconfig
                _logger.exception("GDrive sign-in failed: %s", e)
                # Hoist str(e) into a plain local before the lambda — `e`
                # is del'd at except-block exit (Python scoping rule), so
                # `lambda: ...str(e)...` would NameError on the main thread.
                error_msg = str(e)
                self.after(0, lambda: self._on_gdrive_signin_failure(error_msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_gdrive_signin_success(self) -> None:
        """Worker → main thread: refresh state + restore button text."""
        self._parent._on_gdrive_signed_in()
        self._gdrive_signin_btn.configure(text="Войти через Google")
        self._refresh_gdrive_button_state()

    def _on_gdrive_signin_failure(self, error_msg: str) -> None:
        """Worker → main thread: restore button + show error in status."""
        self._gdrive_signin_btn.configure(text="Войти через Google")
        self._parent._gdrive_status_var.set(f"⚠ Ошибка входа: {error_msg[:80]}")
        self._refresh_gdrive_button_state()

    def _handle_gdrive_signout(self) -> None:
        """Выйти clicked — sync; sign_out() is fast (file delete)."""
        self._parent._on_gdrive_signed_out()
        self._refresh_gdrive_button_state()

    # ── Phase 7.1: Сделать backup сейчас ──────────────────────────────

    def _handle_gdrive_backup_now(self) -> None:
        """Сделать backup clicked — spawn a worker that runs
        gdrive.backup.run_backup. Disable button immediately so a
        double-click can't trigger two parallel backups (Drive's
        find_or_create_folder isn't atomic — concurrent runs could
        create duplicate top folders)."""
        self._gdrive_backup_btn.configure(
            state="disabled", text="Backup в процессе...",
        )
        self._gdrive_backup_status.configure(
            text="Запускаю...", text_color=TEXT_SECONDARY,
        )

        def worker():
            try:
                # Lazy imports — keep dialog construction independent
                # of gdrive.backup's googleapiclient import chain.
                import tempfile

                from gdrive.backup import run_backup

                # Status callback marshals each status string back to
                # the Tk main thread (CTk widgets are not thread-safe).
                def _status(msg: str) -> None:
                    self.after(0, self._gdrive_backup_status.configure, {
                        "text": msg, "text_color": TEXT_SECONDARY,
                    })

                work_dir = tempfile.mkdtemp(prefix="gdrive-backup-")
                result = run_backup(
                    auth=self._parent._gdrive_auth,
                    config=self._parent._config,
                    history_dir=get_meetings_dir(),
                    work_dir=work_dir,
                    on_status=_status,
                )
                self.after(0, lambda: self._on_gdrive_backup_success(result))
            except Exception as e:   # network, quota, RefreshError, disk full — all surface here
                _logger.exception("GDrive backup failed: %s", e)
                # Hoist str(e) before lambda — Python except-scope rule
                # (same gotcha as _handle_gdrive_signin in Phase 7.0).
                error_msg = str(e)
                self.after(0, lambda: self._on_gdrive_backup_failure(error_msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_gdrive_backup_success(self, result: dict) -> None:
        """Worker → main thread: persist new config keys + show ✓ message
        + re-enable button."""
        self._parent._on_gdrive_backup_succeeded(
            root_folder_id=result["root_folder_id"],
            snapshot_name=result["snapshot_name"],
        )
        n_files = len(result.get("uploaded", {}))
        self._gdrive_backup_status.configure(
            text=f"✓ Готово ({n_files} файла, snapshot {result['snapshot_name']})",
            text_color=GREEN,
        )
        self._gdrive_backup_btn.configure(
            state="normal", text="Сделать backup сейчас",
        )

    def _on_gdrive_backup_failure(self, error_msg: str) -> None:
        """Worker → main thread: surface error in status + re-enable
        button. Truncate to 100 chars so a long Drive error message
        doesn't break dialog layout."""
        self._gdrive_backup_status.configure(
            text=f"✗ {error_msg[:100]}", text_color=RED,
        )
        self._gdrive_backup_btn.configure(
            state="normal", text="Сделать backup сейчас",
        )
        # If ensure_valid_credentials() inside run_backup hit a
        # RefreshError, GDriveAuth.ensure_valid_credentials() already
        # called sign_out() internally — credentials gone, token file
        # deleted. But Phase 7.0's _on_gdrive_signed_out callback only
        # runs when the user clicks Выйти, so the top status badge +
        # config.json (gdrive_enabled, gdrive_account_email) remain
        # stale "signed in" until we sync them here. Codex P2 on PR #48
        # caught this UI/config drift.
        #
        # is_signed_in() is the canonical post-failure check: if False,
        # ensure_valid_credentials must have sign-out'd; call the mixin
        # callback so the badge flips to "Не подключён" and config
        # persists the revoked state. sign_out() inside is idempotent —
        # safe even though the auth layer already cleared its state.
        if not self._parent._gdrive_auth.is_signed_in():
            self._parent._on_gdrive_signed_out()
        # Refresh button states regardless — covers both the auth-
        # revoked path (just synced above) and any non-auth failure
        # (network, quota, disk full) where buttons should re-enable
        # to allow retry.
        self._refresh_gdrive_button_state()

    # ── Diagnostics: "Сохранить лог для отправки" (WS-3 / D4) ──────────

    def _build_diagnostics_section(self, parent) -> None:
        """Diagnostics export: bundle logs/ + a redacted config.json into a
        zip the user can send to support. No telemetry backend (D4) — the
        user picks where to save and ships it themselves."""
        section = self._section_card(parent, "Диагностика", row=1)
        label(section, "Логи").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        self._send_log_btn = tonal_button(
            section, text="Сохранить лог для отправки",
            command=self._handle_send_log, width=240,
        )
        self._send_log_btn.grid(row=0, column=1, padx=4, pady=6, sticky="w")
        self._send_log_status = label(section, "", anchor="w")
        self._send_log_status.grid(
            row=0, column=2, padx=(8, 4), pady=6, sticky="ew",
        )

    def _handle_send_log(self) -> None:
        """Pick a destination, then build the logs+config zip in a worker.

        filedialog must run on the Tk main thread; the zip is built off-thread
        so a large rotated-log set doesn't freeze the dialog."""
        from datetime import datetime

        default_name = f"audio-transcriber-log-{datetime.now():%Y-%m-%d_%H-%M-%S}.zip"
        dest = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить лог-архив",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("ZIP архив", "*.zip")],
        )
        if not dest:
            return   # user cancelled the save dialog

        self._send_log_btn.configure(state="disabled")
        self._send_log_status.configure(
            text="Собираю архив...", text_color=TEXT_SECONDARY,
        )

        def worker() -> None:
            try:
                from support_bundle import build_log_bundle
                summary = build_log_bundle(self._parent._config, dest)
                self.after(0, lambda: self._on_send_log_success(summary))
            except Exception as e:   # disk full, permission, bad path — all surface here
                _logger.exception("Log bundle failed: %s", e)
                error_msg = str(e)
                self.after(0, lambda: self._on_send_log_failure(error_msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_send_log_success(self, summary: dict) -> None:
        """Worker → main thread: show the saved path + re-enable the button."""
        self._send_log_status.configure(
            text=f"✓ Сохранено: {summary['dest']}", text_color=GREEN,
        )
        self._send_log_btn.configure(state="normal")

    def _on_send_log_failure(self, error_msg: str) -> None:
        """Worker → main thread: surface the error (truncated) + re-enable."""
        self._send_log_status.configure(
            text=f"✗ {error_msg[:100]}", text_color=RED,
        )
        self._send_log_btn.configure(state="normal")
