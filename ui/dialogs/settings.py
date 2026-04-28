"""Settings dialog — collects all rarely-changed configuration in one window.

Per-run controls (Diarization toggle, Speaker count) stay on the main window.
This dialog owns the persistent settings: language, model, HF token, audio
normalization, transcribe device, diarize device, plus shortcuts to the
hotword and voice library editors.

State model: the App owns the StringVar/BooleanVar instances; widgets here
bind to them directly. Closing the dialog destroys widgets but leaves the
vars untouched, so subsequent transcribe() calls read the right values.
The existing _on_*_changed callbacks on App fire on every change and persist
to config.json — no extra save logic needed here.
"""

from __future__ import annotations

import threading

import customtkinter as ctk

from theme import (
    BG, BLUE, BLUE_DIM, BORDER, FONT, GREEN, INPUT_BG, RED,
    SURFACE, TEXT_PRIMARY, TEXT_SECONDARY,
)
from ui.widgets import (
    card, label, option_menu, primary_button, tonal_button,
)
from utils import save_config


# Curated dropdown for OpenRouter default model. Slug → display label.
# Display label keeps the slug visible — power users recognize 'sonnet-4.5'
# faster than 'Anthropic Claude Sonnet 4.5 (latest)'.
_CURATED_MODELS = {
    "anthropic/claude-sonnet-4.5":   "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5":    "anthropic/claude-haiku-4.5",
    "openai/gpt-4o":                  "openai/gpt-4o",
    "google/gemini-2.5-pro":          "google/gemini-2.5-pro",
    "deepseek/deepseek-v3":           "deepseek/deepseek-v3",
}


class SettingsDialog(ctk.CTkToplevel):
    """Modal settings dialog. Mirrors the structure of TermsDialog/VoicesDialog."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Настройки")
        self.geometry("520x680")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._parent = parent  # App instance — we read its StringVars

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text="Настройки",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12)

        # --- Scrollable content ---
        # CTkScrollableFrame so the dialog gracefully handles future settings
        # additions without forcing geometry growth. Current contents already
        # fit at 680px height; the scrollbar is invisible until needed.
        body = ctk.CTkScrollableFrame(
            self, fg_color="transparent", corner_radius=0,
        )
        body.grid(row=1, column=0, padx=12, pady=(8, 4), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        self._build_appearance_section(body)
        self._build_transcription_section(body)
        self._build_diarization_section(body)
        self._build_audio_section(body)
        self._build_cloud_section(body)
        self._build_dictionaries_section(body)
        self._build_openrouter_section(body)
        self._build_linear_section(body)

        # --- Footer ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=16, pady=(4, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        primary_button(
            footer, text="Закрыть", command=self.destroy, width=120,
        ).grid(row=0, column=0, sticky="e")

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
        from ui.app import LANGUAGES, MODELS, DEVICES

        section = self._section_card(parent, "Транскрипция", row=1)

        label(section, "Язык").grid(row=0, column=0, padx=(4, 8), pady=6, sticky="w")
        option_menu(
            section, self._parent._lang_var, list(LANGUAGES.keys()),
            command=self._parent._on_language_changed,
        ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

        label(section, "Модель").grid(row=1, column=0, padx=(4, 8), pady=6, sticky="w")
        option_menu(
            section, self._parent._model_var, list(MODELS.keys()),
            command=self._parent._on_model_changed,
        ).grid(row=1, column=1, padx=4, pady=6, sticky="w")

        label(section, "Устройство").grid(row=2, column=0, padx=(4, 8), pady=6, sticky="w")
        option_menu(
            section, self._parent._tr_device_var, list(DEVICES.keys()),
            command=self._parent._on_transcribe_device_changed,
        ).grid(row=2, column=1, padx=4, pady=6, sticky="w")

    def _build_diarization_section(self, parent) -> None:
        from ui.app import DEVICES

        section = self._section_card(parent, "Диаризация", row=2)

        label(section, "HF Token").grid(row=0, column=0, padx=(4, 8), pady=6, sticky="w")
        token_entry = ctk.CTkEntry(
            section, textvariable=self._parent._hf_token_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            placeholder_text="hf_...",
        )
        token_entry.grid(row=0, column=1, padx=4, pady=6, sticky="ew")
        tonal_button(
            section, text="Вставить", command=self._parent._paste_token_btn,
            width=100,
        ).grid(row=0, column=2, padx=(4, 4), pady=6)

        label(section, "Устройство").grid(row=1, column=0, padx=(4, 8), pady=6, sticky="w")
        di_menu = option_menu(
            section, self._parent._di_device_var, list(DEVICES.keys()),
            command=self._on_diarize_device_changed,
        )
        di_menu.grid(row=1, column=1, padx=4, pady=6, sticky="w")

        # CPU-diarization warning. Only shown when the diarize device is CPU.
        # We place a label in a fixed slot and toggle visibility via grid()/
        # grid_remove() so the row layout is stable across selections.
        self._cpu_warning = label(
            section, "⚠ CPU-диаризация в 10-20× медленнее GPU", anchor="w",
        )
        self._cpu_warning.grid(
            row=2, column=0, columnspan=3, padx=4, pady=(0, 4), sticky="w",
        )
        self._update_cpu_warning()

    def _on_diarize_device_changed(self, value: str) -> None:
        """Forward to App's persistence callback, then refresh local warning."""
        self._parent._on_diarize_device_changed(value)
        self._update_cpu_warning()

    def _update_cpu_warning(self) -> None:
        if self._parent._di_device_var.get() == "CPU":
            self._cpu_warning.grid()
        else:
            self._cpu_warning.grid_remove()

    def _build_audio_section(self, parent) -> None:
        section = self._section_card(parent, "Аудио", row=3)
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

    def _build_cloud_section(self, parent) -> None:
        # Lazy import — registry pulls in `requests`, no need to load it
        # for users who only run local. Importing inside the method also
        # avoids a circular import with ui.app at module-load time.
        from providers import PROVIDERS

        section = self._section_card(parent, "Облако (опционально)", row=4)

        # Toggle. When ON, the local device pickers above are bypassed
        # and all transcription goes through the chosen provider. The
        # checkbox state is the single source of truth — provider
        # selection / API key only matter when this is checked.
        check = ctk.CTkCheckBox(
            section, text="Использовать облако вместо локального движка",
            variable=self._parent._cloud_enabled_var,
            command=self._parent._on_cloud_enabled_changed,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        )
        check.grid(row=0, column=0, columnspan=3, padx=4, pady=(2, 8), sticky="w")

        label(section, "Провайдер").grid(
            row=1, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._cloud_provider_var, list(PROVIDERS.keys()),
            command=self._parent._on_cloud_provider_changed,
        ).grid(row=1, column=1, padx=4, pady=6, sticky="w")

        label(section, "API key").grid(
            row=2, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        ctk.CTkEntry(
            section, textvariable=self._parent._cloud_api_key_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            placeholder_text="API ключ провайдера",
            show="•",  # Mask the key visually — same UX as a password field.
        ).grid(row=2, column=1, padx=4, pady=6, sticky="ew")
        tonal_button(
            section, text="Вставить",
            command=self._parent._paste_cloud_api_key, width=100,
        ).grid(row=2, column=2, padx=(4, 4), pady=6)

        # Disclosure. Cloud means audio leaves the user's machine and
        # ends up on a third-party server (US-based for AssemblyAI),
        # which has privacy/compliance implications. Surfacing this
        # right next to the toggle is the cheapest mitigation.
        label(
            section,
            "⚠ При включении аудио загружается на сервер провайдера. "
            "Не используй для конфиденциальных записей.",
            anchor="w",
        ).grid(row=3, column=0, columnspan=3, padx=4, pady=(2, 6), sticky="w")
        label(
            section,
            "ℹ Стоимость AssemblyAI: ~$0.37/час без диаризации, "
            "~$0.65/час с диаризацией.",
            anchor="w",
        ).grid(row=4, column=0, columnspan=3, padx=4, pady=(0, 4), sticky="w")

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

        tonal_button(
            section, text="Голоса",
            command=self._parent._open_voices_dialog, width=200,
        ).grid(row=1, column=0, padx=4, pady=6, sticky="w")
        self._voices_summary = label(section, "", anchor="w")
        self._voices_summary.grid(row=1, column=1, padx=(8, 4), pady=6, sticky="ew")

        self._refresh_summaries()

    def _refresh_summaries(self) -> None:
        """Mirror App's existing summary-rendering for terms and voices.

        Pulls the current strings via parent helpers if they exist, otherwise
        falls back to plain counts. Keeps this dialog independent of internal
        helper signatures while still showing live data.
        """
        terms = self._parent._config.get("hotwords", []) or []
        if terms:
            preview = ", ".join(terms[:5])
            if len(terms) > 5:
                preview += f", … (+{len(terms) - 5})"
            self._terms_summary.configure(text=preview)
        else:
            self._terms_summary.configure(text="Нет сохранённых терминов")

        voices = self._parent._config.get("voices", []) or []
        if voices:
            names = [v.get("name", "?") for v in voices[:5]]
            preview = ", ".join(names)
            if len(voices) > 5:
                preview += f", … (+{len(voices) - 5})"
            self._voices_summary.configure(text=preview)
        else:
            self._voices_summary.configure(text="Голоса не записаны")

    # ── OpenRouter section (Phase 6.0 Task 13) ────────────────────────

    def _build_openrouter_section(self, parent) -> None:
        """OpenRouter API key + default model.

        Layout: title, [api_key field][Вставить], [Проверить ключ][status],
        default model dropdown.
        """
        section = self._section_card(parent, "OpenRouter", row=6)

        # API key row — entry + paste button
        label(section, "API ключ").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        ctk.CTkEntry(
            section, textvariable=self._parent._openrouter_key_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            placeholder_text="sk-or-...",
            show="•",  # Mask the key visually — same UX as cloud API key field.
        ).grid(row=0, column=1, padx=4, pady=6, sticky="ew")
        tonal_button(
            section, text="Вставить",
            command=self._paste_openrouter_key, width=100,
        ).grid(row=0, column=2, padx=(4, 4), pady=6)

        # Validate row — button + status label
        tonal_button(
            section, text="Проверить ключ",
            command=self._validate_openrouter, width=140,
        ).grid(row=1, column=0, padx=4, pady=6, sticky="w")
        self._openrouter_status = label(section, "", anchor="w")
        self._openrouter_status.grid(
            row=1, column=1, columnspan=2, padx=(8, 4), pady=6, sticky="ew",
        )

        # Default model dropdown
        label(section, "Модель по умолчанию").grid(
            row=2, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._openrouter_default_model_var,
            list(_CURATED_MODELS.keys()),
        ).grid(row=2, column=1, columnspan=2, padx=4, pady=6, sticky="ew")

    def _paste_openrouter_key(self) -> None:
        """Paste-from-clipboard helper. Mirrors HF Token paste pattern but
        the handler lives on the dialog rather than App since the var is
        OpenRouter-specific (not yet used outside the Settings flow)."""
        try:
            text = self.clipboard_get().strip()
            self._parent._openrouter_key_var.set(text)
            if text:
                self._parent._config["openrouter_api_key"] = text
                save_config(self._parent._config)
        except Exception:
            pass

    # ── Linear section (Phase 6.0 Task 15) ────────────────────────────

    def _build_linear_section(self, parent) -> None:
        """Linear API key + connection status.

        No team picker here — that's per-run in the ExtractTasksDialog
        (Phase 6.1). Settings only persists the key.
        """
        section = self._section_card(parent, "Linear", row=7)

        label(section, "API ключ").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        ctk.CTkEntry(
            section, textvariable=self._parent._linear_key_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            placeholder_text="lin_api_...",
            show="•",
        ).grid(row=0, column=1, padx=4, pady=6, sticky="ew")
        tonal_button(
            section, text="Вставить",
            command=self._paste_linear_key, width=100,
        ).grid(row=0, column=2, padx=(4, 4), pady=6)

        tonal_button(
            section, text="Проверить ключ",
            command=self._validate_linear, width=140,
        ).grid(row=1, column=0, padx=4, pady=6, sticky="w")
        self._linear_status = label(section, "", anchor="w")
        self._linear_status.grid(
            row=1, column=1, columnspan=2, padx=(8, 4), pady=6, sticky="ew",
        )

    def _paste_linear_key(self) -> None:
        """Paste-from-clipboard. Mirrors _paste_openrouter_key."""
        try:
            text = self.clipboard_get().strip()
            self._parent._linear_key_var.set(text)
            if text:
                self._parent._config["linear_api_key"] = text
                save_config(self._parent._config)
        except Exception:
            pass

    def _validate_linear(self) -> None:
        """Make a single viewer GraphQL query. Show display name on success.

        Same threading pattern as _validate_openrouter. Saves the key to
        config.json only on successful validation.
        """
        key = self._parent._linear_key_var.get().strip()
        if not key:
            self._linear_status.configure(
                text="Введите API ключ", text_color=RED,
            )
            return

        self._linear_status.configure(
            text="Проверка...", text_color=TEXT_SECONDARY,
        )

        def worker():
            try:
                # Lazy import — same rationale as _validate_openrouter.
                from tasks.linear_client import LinearClient, LinearError
                client = LinearClient(key)
                try:
                    viewer = client.validate_key()
                finally:
                    client.close()
            except LinearError as e:
                self.after(0, self._linear_status.configure, {
                    "text": f"✗ {e}", "text_color": RED,
                })
                return
            except Exception as e:
                self.after(0, self._linear_status.configure, {
                    "text": f"✗ {e}", "text_color": RED,
                })
                return

            # Key works — persist it.
            self._parent._config["linear_api_key"] = key
            save_config(self._parent._config)

            name = viewer.get("name") or viewer.get("email") or "(unknown)"
            self.after(0, self._linear_status.configure, {
                "text": f"✓ Подключено: {name}", "text_color": GREEN,
            })

        threading.Thread(target=worker, daemon=True).start()

    def _validate_openrouter(self) -> None:
        """Make a single GET /auth/key. Show balance on success, error on fail.

        Runs in a worker thread to keep the dialog responsive on slow networks
        (the call is bounded by a 10s timeout inside the client). UI updates
        from the worker are marshalled back via ``self.after(0, ...)``.
        Saves the key to config.json only on success — typing intermediate
        garbage doesn't leak into persistent state.
        """
        key = self._parent._openrouter_key_var.get().strip()
        if not key:
            self._openrouter_status.configure(
                text="Введите API ключ", text_color=RED,
            )
            return

        self._openrouter_status.configure(
            text="Проверка...", text_color=TEXT_SECONDARY,
        )

        def worker():
            try:
                # Imported lazily to avoid pulling tasks/openrouter_client (and
                # thus `requests`) at Settings-dialog construction time.
                from tasks.openrouter_client import (
                    OpenRouterClient, OpenRouterError,
                )
                client = OpenRouterClient(key)
                try:
                    info = client.validate_key()
                finally:
                    client.close()
            except OpenRouterError as e:
                self.after(0, self._openrouter_status.configure, {
                    "text": f"✗ {e}", "text_color": RED,
                })
                return
            except Exception as e:  # belt-and-braces: anything else surfaces too
                self.after(0, self._openrouter_status.configure, {
                    "text": f"✗ {e}", "text_color": RED,
                })
                return

            # Key works — persist it.
            self._parent._config["openrouter_api_key"] = key
            save_config(self._parent._config)

            balance = info.get("balance_remaining")
            if balance is not None:
                msg = f"✓ Активен (баланс: ${balance:.2f})"
            else:
                msg = f"✓ Активен ({info.get('label') or 'unlimited'})"
            self.after(0, self._openrouter_status.configure, {
                "text": msg, "text_color": GREEN,
            })

        threading.Thread(target=worker, daemon=True).start()
