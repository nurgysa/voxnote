"""Module-level constants for the main App window.

Extracted from ``ui/app/__init__.py`` (F4-PR-2a) so the package root can
stay small and so that ``ui.dialogs.settings`` can keep importing these
visible-label → backend-value mappings through the existing
``from ui.app import ...`` re-export (see ``__init__.py``).
"""
from __future__ import annotations

LANGUAGES = {
    "Авто-определение": None,
    "Казахский": "kk",
    "Русский": "ru",
    "English": "en",
    "Смешанный (KZ+RU+EN)": "mixed",
}

MODELS = {
    "small (быстрый)": "small",
    "medium (точный)": "medium",
    "large-v3 (максимум)": "large-v3",
}

# Speaker-count hint passed to the provider's diarization. Each value maps to
# one of three tuples: (num_speakers, min_speakers, max_speakers). A known
# exact count improves speaker attribution vs the provider's auto-detection.
# "5+" uses min_speakers so 6/7-way calls still work without a hard cap.
SPEAKER_COUNTS: dict[str, tuple[int | None, int | None, int | None]] = {
    "Авто": (None, None, None),
    "2": (2, None, None),
    "3": (3, None, None),
    "4": (4, None, None),
    "5+": (None, 5, None),
}

# Main-bar project selector — the "no project" sentinel label. Its menu
# entry maps to project_id=None (meeting written to <meetings_dir>/ root,
# Hermes event project=null). Shared by builder.py + queue_mixin.py.
NO_PROJECT_LABEL = "Без проекта"

# Visible label → CustomTkinter appearance_mode value.
# "system" follows the Windows light/dark setting; the other two are explicit.
APPEARANCE_MODES: dict[str, str] = {
    "Системная": "system",
    "Светлая": "light",
    "Тёмная": "dark",
}


def compute_first_run(cloud_api_keys: dict, openrouter_key: str) -> bool:
    """Whether to show the first-run banner.

    True when EITHER mandatory key is missing — the cloud STT key (AssemblyAI,
    the default provider) OR the OpenRouter key (needed for task/protocol
    extraction). Checking only one leaves a client who set just one key at a
    silent dead-end later (e.g. AssemblyAI set, OpenRouter empty → the banner
    clears but «Извлечь задачи» then fails).
    """
    assemblyai = (cloud_api_keys or {}).get("AssemblyAI", "").strip()
    openrouter = (openrouter_key or "").strip()
    return not assemblyai or not openrouter
