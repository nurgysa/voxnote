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

# Per-backend dropdown labels — "Команда" for Linear (teams), "Доска" for
# Glide (boards). Keeps the header label honest about what's underneath.
_CONTAINER_LABEL_BY_BACKEND = {"linear": "Команда", "glide": "Доска"}

# Sonnet-4.5 input price per 1M tokens. Used for the upfront cost-estimate
# hint (before user runs Извлечь — we don't know which model yet, so we
# approximate with the default).
_COST_PER_1M_INPUT_TOKENS_USD = 3.0

# Per-model pricing for the post-call **real** cost display in
# _on_extract_success — uses response.usage tokens × these rates.
# Tuple is (input_$_per_1M, output_$_per_1M). Updated 2026-04 from
# OpenRouter pricing pages. May drift; if the response itself includes
# `usage.cost`, that authoritative value is used and this table is
# bypassed (see _compute_real_cost). New models should be added here
# alongside _CURATED_MODELS.
_MODEL_PRICING_USD_PER_M = {
    "google/gemini-3.5-flash":      (1.50,  9.00),  # released 2026-05-19, 1M context
    # Legacy entries kept so existing history records (which may carry stale
    # model slugs from pre-2026-05-28 runs on the maintainer's dev machine)
    # still render a cost on the History view. The curated dropdown above
    # restricts NEW runs to gemini-3.5-flash; these lookups are read-only
    # safety nets, not user-selectable options.
    "anthropic/claude-sonnet-4.5":  (3.00, 15.00),
    "anthropic/claude-haiku-4.5":   (1.00,  5.00),
    "openai/gpt-4o":                (2.50, 10.00),
    "google/gemini-2.5-pro":        (1.25, 10.00),
    "deepseek/deepseek-v3":         (0.27,  1.10),
}

_PRIORITY_GLYPHS = {
    "none":   "⚪",
    "low":    "🔵",
    "medium": "🟡",
    "high":   "🟠",
    "urgent": "🔴",
}
