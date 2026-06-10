"""Widget-tree constructor for the Settings dialog.

Extracted from ``ui/dialogs/settings.py`` (widget-tree split, 2026-06-10
spec). Mirrors the ``ui/app/builder.py`` contract: each ``build_*_section``
free function takes the live ``SettingsDialog`` instance, creates that
section's widgets inside ``parent`` (a per-tab scroll frame), and sets any
captured refs on ``dialog`` under their original names
(``dialog._lang_menu``, ``dialog._cloud_api_key_entry``, …) so the banner
jump / status handlers that remain on the class keep working. No business
logic lives here; handlers and workers stay on ``SettingsDialog``.

Import discipline (cycle guard): this module may import theme, ui.widgets,
ui.app.constants, providers, settings_helpers and utils — never
``ui.dialogs.settings`` and never module-level ``ui.app`` (the
``APPEARANCE_MODES`` import stays lazy inside ``build_appearance_section``).
"""

from __future__ import annotations

import customtkinter as ctk

from theme import (
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    INPUT_BG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.app.constants import LANGUAGES
from ui.widgets import (
    card,
    label,
    option_menu,
    tonal_button,
)
from utils import get_meetings_dir

# Curated dropdown for OpenRouter default model. Slug → display label.
# Display label keeps the slug visible — power users recognize 'sonnet-4.5'
# faster than 'Anthropic Claude Sonnet 4.5 (latest)'.
_CURATED_MODELS = {
    "google/gemini-3.5-flash":        "google/gemini-3.5-flash",
}


def section_card(dialog, parent, title: str, row: int) -> ctk.CTkFrame:
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


def build_appearance_section(dialog, parent) -> None:
    # Lazy import — APPEARANCE_MODES lives in ui.app, importing at
    # module-load would create a circular dependency.
    from ui.app import APPEARANCE_MODES

    section = section_card(dialog, parent, "Внешний вид", row=0)

    label(section, "Тема").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    option_menu(
        section, dialog._parent._appearance_var, list(APPEARANCE_MODES.keys()),
        command=dialog._parent._on_appearance_changed,
    ).grid(row=0, column=1, padx=4, pady=6, sticky="w")
    label(
        section,
        "«Системная» следует за настройкой Windows (Light/Dark mode).",
        anchor="w",
    ).grid(row=1, column=0, columnspan=2, padx=4, pady=(0, 4), sticky="w")


def build_transcription_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Транскрипция", row=1)

    label(section, "Язык").grid(row=0, column=0, padx=(4, 8), pady=6, sticky="w")
    # Capture ref so the banner's _jump_to_lang can focus_set() it.
    dialog._lang_menu = option_menu(
        section, dialog._parent._lang_var, list(LANGUAGES.keys()),
        command=dialog._parent._on_language_changed,
    )
    dialog._lang_menu.grid(row=0, column=1, padx=4, pady=6, sticky="w")


def build_audio_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Аудио", row=2)
    # No loudness-normalization toggle here on purpose: the cloud path
    # hardcodes ensure_wav(normalize=False) — provider gateways apply
    # their own gain normalization, so a checkbox would control nothing.

    # RNNoise (arnndn) — opt-in noise suppression. Default off; the
    # neural denoiser can clip soft consonants on already-clean
    # recordings. ~85 KB model lazy-downloaded on first use.
    denoise_check = ctk.CTkCheckBox(
        section, text="Подавлять шум (RNNoise — для записей с фоном)",
        variable=dialog._parent._denoise_var,
        command=dialog._parent._on_denoise_changed,
        font=ctk.CTkFont(family=FONT, size=13),
        text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
        border_color=BORDER, corner_radius=4,
        checkbox_height=20, checkbox_width=20,
    )
    denoise_check.grid(
        row=0, column=0, columnspan=2, padx=4, pady=6, sticky="w",
    )


def build_meetings_section(dialog, parent) -> None:
    """Meetings folder picker — path entry + Выбрать + Default + stats.

    On path change: triggers MigrationPromptDialog if the current
    folder has entries (mode="settings"). Otherwise silent save.
    """
    section = section_card(dialog, parent, "Митинги", row=4)

    label(section, "Папка хранения").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )

    dialog._meetings_path_var = ctk.StringVar(value=get_meetings_dir())
    dialog._meetings_entry = ctk.CTkEntry(
        section, textvariable=dialog._meetings_path_var,
        height=36, corner_radius=10,
        border_color=BORDER, border_width=1,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=12),
        state="readonly",
    )
    dialog._meetings_entry.grid(
        row=0, column=1, columnspan=2, padx=4, pady=6, sticky="ew",
    )

    tonal_button(
        section, text="\U0001f4c1 Выбрать",
        command=dialog._on_pick_meetings_folder, width=130,
    ).grid(row=0, column=3, padx=(4, 4), pady=6)

    tonal_button(
        section, text="↻ Default",
        command=dialog._on_reset_meetings_folder, width=120,
    ).grid(row=1, column=3, padx=(4, 4), pady=(0, 6))

    # Stats label — refreshed on dialog open and after path change
    dialog._meetings_stats_label = label(section, "", anchor="w")
    dialog._meetings_stats_label.grid(
        row=1, column=0, columnspan=3, padx=4, pady=(0, 6), sticky="w",
    )
    dialog._refresh_meetings_stats()


def build_dictionaries_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Словари", row=5)

    tonal_button(
        section, text="Словарь терминов",
        command=dialog._parent._open_terms_dialog, width=200,
    ).grid(row=0, column=0, padx=4, pady=6, sticky="w")
    # Compact summary of what's saved — same source as the main-window
    # label (kept in sync via _update_terms_label, which we reuse below).
    dialog._terms_summary = label(section, "", anchor="w")
    dialog._terms_summary.grid(row=0, column=1, padx=(8, 4), pady=6, sticky="ew")

    dialog._refresh_summaries()
