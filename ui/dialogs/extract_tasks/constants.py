"""Module-level constants for the Extract Tasks dialog package.

Lifted out of the monolith so config-keys, cache-TTL, and the curated
model list are addressable independently of the dialog's UI code.
"""
from __future__ import annotations

from datetime import timedelta

# Single-model lock for v0.1 client delivery: gemini-3.5-flash only.
# Rationale: consistency across 3 alpha clients (one provider, one rate,
# one quality baseline → easier to evaluate / triage issues without
# per-model variance). Custom slugs can still be entered (the dialog
# accepts arbitrary OpenRouter slugs via the recent-models path), so this
# is a curated-default restriction, not a hard provider lock. Same list
# must stay in sync with Settings → OpenRouter section (settings.py:53).
# (Phase 6.4 may replace both with a live /models browser.)
_CURATED_MODELS = [
    "google/gemini-3.5-flash",
]

_TEAMS_CACHE_KEY = "linear_teams_cache"      # Phase 6.1 — Linear teams
_BOARDS_CACHE_KEY = "glide_boards_cache"     # Phase 6.4.1 — Glide boards
_CONTAINER_CACHE_TTL = timedelta(hours=24)
_TEAMS_CACHE_TTL = _CONTAINER_CACHE_TTL      # back-compat alias for any callers
_RECENT_MODELS_KEY = "tasks_recent_models"
_RECENT_MODELS_LIMIT = 5

_TRELLO_CACHE_KEY = "trello_lists_cache"   # Phase: Trello lists (board/list pairs)

# Backend display ↔ internal name (replaces hardcoded "Linear"/"Glide"
# ternaries in the dialog). Add a backend here and the dropdown, the
# display→name reverse lookup, and the per-backend cache key all follow.
_NAME_TO_DISPLAY = {"linear": "Linear", "glide": "Glide", "trello": "Trello"}
_DISPLAY_TO_NAME = {v: k for k, v in _NAME_TO_DISPLAY.items()}

# Per-backend container cache key — distinct so Linear teams, Glide boards,
# and Trello lists never collide in config storage.
_CACHE_KEY_BY_BACKEND = {
    "linear": _TEAMS_CACHE_KEY,
    "glide": _BOARDS_CACHE_KEY,
    "trello": _TRELLO_CACHE_KEY,
}

# Dropdown "(empty)" placeholder + the accusative noun for "Выберите …".
_EMPTY_CONTAINER_LABEL_BY_BACKEND = {
    "linear": "(нет команд)", "glide": "(нет досок)", "trello": "(нет списков)",
}
_CONTAINER_ACCUSATIVE_BY_BACKEND = {
    "linear": "команду", "glide": "доску", "trello": "список",
}

# Credentials each backend needs to be considered "configured". Trello
# needs two (key + token); the others need one.
_REQUIRED_KEYS_BY_BACKEND = {
    "linear": ("linear_api_key",),
    "glide": ("glide_api_key",),
    "trello": ("trello_api_key", "trello_token"),
}

# Per-backend dropdown labels — "Команда" for Linear (teams), "Доска" for
# Glide (boards). Keeps the header label honest about what's underneath.
_CONTAINER_LABEL_BY_BACKEND = {"linear": "Команда", "glide": "Доска", "trello": "Список"}

_PRIORITY_GLYPHS = {
    "none":   "⚪",
    "low":    "🔵",
    "medium": "🟡",
    "high":   "🟠",
    "urgent": "🔴",
}
