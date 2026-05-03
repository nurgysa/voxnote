"""Module-level constants for the Extract Tasks dialog package.

Lifted out of the monolith so config-keys, cache-TTL, and the curated
model list are addressable independently of the dialog's UI code.
"""
from __future__ import annotations

from datetime import timedelta

# Same curated list as Settings → OpenRouter section, kept in sync manually.
# (Phase 6.4 may replace both with a live /models browser.)
_CURATED_MODELS = [
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5",
    "openai/gpt-4o",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-v3",
]

_TEAMS_CACHE_KEY = "linear_teams_cache"
_TEAMS_CACHE_TTL = timedelta(hours=24)
_RECENT_MODELS_KEY = "tasks_recent_models"
_RECENT_MODELS_LIMIT = 5

# Sonnet-4.5 input price per 1M tokens. Used for the cost-estimate hint.
# Imprecise (we don't know the actual model's price) but useful as a sanity-check.
_COST_PER_1M_INPUT_TOKENS_USD = 3.0

_PRIORITY_GLYPHS = {
    "none":   "⚪",
    "low":    "🔵",
    "medium": "🟡",
    "high":   "🟠",
    "urgent": "🔴",
}
