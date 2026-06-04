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

# Visible label → CustomTkinter appearance_mode value.
# "system" follows the Windows light/dark setting; the other two are explicit.
APPEARANCE_MODES: dict[str, str] = {
    "Системная": "system",
    "Светлая": "light",
    "Тёмная": "dark",
}
