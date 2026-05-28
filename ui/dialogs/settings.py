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
import threading
import tkinter as tk

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
from ui.widgets import (
    card,
    label,
    option_menu,
    primary_button,
    tonal_button,
)
from utils import save_config

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
        self._build_audio_section(body)
        self._build_cloud_section(body)
        self._build_dictionaries_section(body)
        self._build_openrouter_section(body)
        self._build_linear_section(body)
        self._build_glide_section(body)
        self._build_gdrive_section(body)

        # Wire reactive warning: fires whenever language or cloud provider
        # changes so the label stays in sync without requiring a Save button.
        # The traced StringVars live on `self._parent` (App) and outlive the
        # dialog, so the trace tokens must be kept and unregistered in
        # destroy() — otherwise reopening the dialog stacks duplicate
        # callbacks that fire on already-destroyed dialogs and raise TclError.
        self._trace_lang = self._parent._lang_var.trace_add(
            "write", self._update_mixed_warning,
        )
        self._trace_provider = self._parent._cloud_provider_var.trace_add(
            "write", self._update_mixed_warning,
        )
        # Run once immediately so an already-loaded incompatible config
        # (e.g. lang=mixed + provider=Deepgram from config.json) shows the
        # warning as soon as the dialog opens — not only after the user
        # interacts with a dropdown.
        self._update_mixed_warning()

        # --- Footer ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=16, pady=(4, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        primary_button(
            footer, text="Закрыть", command=self.destroy, width=120,
        ).grid(row=0, column=0, sticky="e")

    def destroy(self) -> None:
        """Remove app-level Var traces before the Toplevel is torn down.

        ``_lang_var`` and ``_cloud_provider_var`` live on the App and outlive
        the dialog. Without cleanup, reopening Settings registers a second
        trace pointing at the previous (destroyed) dialog's bound method, and
        the next dropdown change fires both — the stale one raises TclError
        ("invalid command name") on the destroyed widget, and the destroyed
        dialog instance is held alive by the trace, leaking memory.

        Wrapped in try/except TclError because the underlying Var may have
        already been GC'd (parent App teardown ordering) — in that case there
        is nothing left to unregister.
        """
        for var, token in (
            (self._parent._lang_var, getattr(self, "_trace_lang", None)),
            (self._parent._cloud_provider_var, getattr(self, "_trace_provider", None)),
        ):
            if token is not None:
                try:
                    var.trace_remove("write", token)
                except tk.TclError:
                    # Var already destroyed during parent teardown — safe to ignore.
                    pass
        super().destroy()

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
        option_menu(
            section, self._parent._lang_var, list(LANGUAGES.keys()),
            command=self._parent._on_language_changed,
        ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

    def _update_mixed_warning(self, *_args) -> None:
        """Show or hide the inline incompatibility warning in the cloud section.

        Fires when either the language or cloud-provider StringVar changes, and
        once at __init__ end to reflect a pre-loaded config state.

        Shows a warning when:
          - the selected language label resolves to code ``"mixed"``
          - AND the active cloud provider has ``supports_mixed = False``
            (class attribute, no instantiation required)

        Currently the only provider with ``supports_mixed = False`` is Deepgram.
        """
        lang_label = self._parent._lang_var.get()
        lang_code = LANGUAGES.get(lang_label)
        if lang_code != "mixed":
            self._mixed_warning.grid_remove()
            return

        provider_name = self._parent._cloud_provider_var.get()
        provider_cls = PROVIDERS.get(provider_name)
        if provider_cls is None:
            self._mixed_warning.grid_remove()
            return

        if not provider_cls.supports_mixed:
            self._mixed_warning.configure(
                text=(
                    f"⚠ {provider_name} не поддерживает "
                    "«Смешанный (KZ+RU+EN)». "
                    "Выбери другой провайдер или язык."
                ),
            )
            self._mixed_warning.grid()
        else:
            self._mixed_warning.grid_remove()

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
        section = self._section_card(parent, "Транскрибация (cloud API)", row=3)

        label(section, "Провайдер").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._cloud_provider_var, list(PROVIDERS.keys()),
            command=self._parent._on_cloud_provider_changed,
        ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

        # Inline warning shown when language=mixed AND the chosen provider
        # has supports_mixed=False (currently only Deepgram). Initially
        # hidden; _update_mixed_warning() toggles visibility reactively.
        self._mixed_warning = ctk.CTkLabel(
            section, text="",
            font=ctk.CTkFont(family=FONT, size=11),
            text_color=RED,
            anchor="w",
            wraplength=340,
        )
        self._mixed_warning.grid(
            row=1, column=0, columnspan=3, padx=4, pady=(0, 2), sticky="w",
        )
        self._mixed_warning.grid_remove()  # hidden until needed

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

        # Disclosure. Audio leaves the user's machine and ends up on a
        # third-party server, which has privacy/compliance implications.
        # Surfacing this in the cloud section is the cheapest mitigation.
        label(
            section,
            "⚠ Аудио загружается на сервер провайдера. "
            "Не используй для конфиденциальных записей.",
            anchor="w",
        ).grid(row=3, column=0, columnspan=3, padx=4, pady=(2, 6), sticky="w")
        # Static price summary. Cheapest with diarization first.
        label(
            section,
            "ℹ Цены с диаризацией: AssemblyAI ~$0.17/ч • "
            "Deepgram ~$0.43/ч • Gladia ~$0.61/ч • "
            "Speechmatics ~$1.04/ч.",
            anchor="w",
        ).grid(row=4, column=0, columnspan=3, padx=4, pady=(0, 4), sticky="w")

    def _build_dictionaries_section(self, parent) -> None:
        section = self._section_card(parent, "Словари", row=4)

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

        Layout: title, [api_key field][Вставить], [Проверить ключ][status],
        default model dropdown.
        """
        section = self._section_card(parent, "OpenRouter", row=5)

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
        except tk.TclError:
            return  # empty clipboard / non-text — silent
        except OSError as e:
            _logger.warning("Failed to persist OpenRouter key: %s", e)

    # ── Linear section (Phase 6.0 Task 15) ────────────────────────────

    def _build_linear_section(self, parent) -> None:
        """Linear API key + connection status.

        No team picker here — that's per-run in the ExtractTasksDialog
        (Phase 6.1). Settings only persists the key. Phase 6.4 adds the
        enabled-checkbox above; when off, Linear is hidden from the
        backend dropdown in ExtractTasksDialog (effect wired in 6.4.1).
        """
        section = self._section_card(parent, "Linear", row=6)

        ctk.CTkCheckBox(
            section, text="Использовать Linear",
            variable=self._parent._linear_enabled_var,
            command=self._parent._on_linear_enabled_changed,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        ).grid(row=0, column=0, columnspan=3, padx=4, pady=(2, 8), sticky="w")

        label(section, "API ключ").grid(
            row=1, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        ctk.CTkEntry(
            section, textvariable=self._parent._linear_key_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            placeholder_text="lin_api_...",
            show="•",
        ).grid(row=1, column=1, padx=4, pady=6, sticky="ew")
        tonal_button(
            section, text="Вставить",
            command=self._paste_linear_key, width=100,
        ).grid(row=1, column=2, padx=(4, 4), pady=6)

        tonal_button(
            section, text="Проверить ключ",
            command=self._validate_linear, width=140,
        ).grid(row=2, column=0, padx=4, pady=6, sticky="w")
        self._linear_status = label(section, "", anchor="w")
        self._linear_status.grid(
            row=2, column=1, columnspan=2, padx=(8, 4), pady=6, sticky="ew",
        )

    def _paste_linear_key(self) -> None:
        """Paste-from-clipboard. Mirrors _paste_openrouter_key."""
        try:
            text = self.clipboard_get().strip()
            self._parent._linear_key_var.set(text)
            if text:
                self._parent._config["linear_api_key"] = text
                save_config(self._parent._config)
        except tk.TclError:
            return  # empty clipboard / non-text — silent
        except OSError as e:
            _logger.warning("Failed to persist Linear key: %s", e)

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

    def _build_glide_section(self, parent) -> None:
        """Glide API key + connection status (Phase 6.4).

        Glide is the parallel backend to Linear. Same pattern as the
        Linear section above: enabled-checkbox at top, paste, validate
        (saves on success), shows ✓/✗ status next to the button.
        """
        section = self._section_card(parent, "Glide", row=7)

        ctk.CTkCheckBox(
            section, text="Использовать Glide",
            variable=self._parent._glide_enabled_var,
            command=self._parent._on_glide_enabled_changed,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        ).grid(row=0, column=0, columnspan=3, padx=4, pady=(2, 8), sticky="w")

        label(section, "API ключ").grid(
            row=1, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        ctk.CTkEntry(
            section, textvariable=self._parent._glide_key_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            placeholder_text="glide_pk_<workspace>_...",
            show="•",
        ).grid(row=1, column=1, padx=4, pady=6, sticky="ew")
        tonal_button(
            section, text="Вставить",
            command=self._paste_glide_key, width=100,
        ).grid(row=1, column=2, padx=(4, 4), pady=6)

        tonal_button(
            section, text="Проверить ключ",
            command=self._validate_glide, width=140,
        ).grid(row=2, column=0, padx=4, pady=6, sticky="w")
        self._glide_status = label(section, "", anchor="w")
        self._glide_status.grid(
            row=2, column=1, columnspan=2, padx=(8, 4), pady=6, sticky="ew",
        )

    def _paste_glide_key(self) -> None:
        """Paste-from-clipboard. Mirrors _paste_linear_key."""
        try:
            text = self.clipboard_get().strip()
            self._parent._glide_key_var.set(text)
            if text:
                self._parent._config["glide_api_key"] = text
                save_config(self._parent._config)
        except Exception:
            pass

    def _validate_glide(self) -> None:
        """GET /boards via GlideClient.validate_key — proves the key works
        and reports how many boards are visible to the integration token.

        Saves the key to config.json only on success (mirrors Linear /
        OpenRouter discipline — typing intermediate garbage doesn't persist).
        """
        key = self._parent._glide_key_var.get().strip()
        if not key:
            self._glide_status.configure(
                text="Введите API ключ", text_color=RED,
            )
            return

        self._glide_status.configure(
            text="Проверка...", text_color=TEXT_SECONDARY,
        )

        def worker():
            try:
                # Lazy import — same rationale as _validate_linear.
                from tasks.glide_client import GlideClient, GlideError
                client = GlideClient(key)
                try:
                    info = client.validate_key()
                finally:
                    client.close()
            except GlideError as e:
                self.after(0, self._glide_status.configure, {
                    "text": f"✗ {e}", "text_color": RED,
                })
                return
            except Exception as e:
                self.after(0, self._glide_status.configure, {
                    "text": f"✗ {e}", "text_color": RED,
                })
                return

            # Key works — persist it.
            self._parent._config["glide_api_key"] = key
            save_config(self._parent._config)

            count = info["board_count"]
            sample = info["sample_names"]
            # "5 досок" / "1 доска" / "0 досок" — Russian noun-count is
            # awkward; use a simple form that's correct for all sizes.
            base = f"✓ Подключено: {count} досок"
            if sample:
                base += f" ({', '.join(sample)})"
            self.after(0, self._glide_status.configure, {
                "text": base, "text_color": GREEN,
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
                    OpenRouterClient,
                    OpenRouterError,
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
        section = self._section_card(parent, "Google Drive", row=8)

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
                    history_dir="history",
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
