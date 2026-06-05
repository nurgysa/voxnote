"""Unit tests for the extracted OpenRouter pricing helpers.

ui.dialogs.extract_tasks.pricing is a pure leaf module (stdlib only, no
Tk / sounddevice), so it imports cleanly on Linux CI — unlike the dialog
package's __init__.py. Behaviour is locked against the pre-extraction
dialog bodies (`_format_real_cost` / `_update_cost_hint`) so the WS-4
move stays behaviour-preserving.
"""
from __future__ import annotations

from ui.dialogs.extract_tasks.pricing import estimate_cost_hint, format_real_cost

# ── format_real_cost (post-call real cost from response.usage) ──────

def test_format_real_cost_empty_usage_returns_empty():
    assert format_real_cost({}, "google/gemini-3.5-flash") == ""


def test_format_real_cost_zero_tokens_returns_empty():
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    assert format_real_cost(usage, "google/gemini-3.5-flash") == ""


def test_format_real_cost_uses_authoritative_usage_cost_when_present():
    # OpenRouter sometimes includes usage["cost"] — that wins over the table.
    usage = {"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.025}
    assert format_real_cost(usage, "google/gemini-3.5-flash") == (
        "1,000↑ + 500↓ т.  ·  $0.0250"
    )


def test_format_real_cost_computes_from_known_model_rates():
    # gemini-3.5-flash rates = (1.50, 9.00) per 1M.
    # (1000*1.50 + 500*9.00) / 1e6 = 6000 / 1e6 = 0.0060
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    assert format_real_cost(usage, "google/gemini-3.5-flash") == (
        "1,000↑ + 500↓ т.  ·  $0.0060"
    )


def test_format_real_cost_unknown_model_shows_tokens_only():
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    assert format_real_cost(usage, "vendor/never-priced") == "1,000↑ + 500↓ т."


def test_format_real_cost_ignores_non_numeric_cost_and_falls_back_to_rates():
    # The isinstance guard rejects a non-numeric "cost" and computes instead.
    usage = {"prompt_tokens": 1000, "completion_tokens": 500, "cost": "oops"}
    assert format_real_cost(usage, "google/gemini-3.5-flash") == (
        "1,000↑ + 500↓ т.  ·  $0.0060"
    )


# ── estimate_cost_hint (upfront heuristic before extraction) ────────

def test_estimate_cost_hint_below_50_chars_is_welcome():
    welcome = "Готов к работе. Извлеките из транскрипта или добавьте задачу вручную."
    assert estimate_cost_hint(0) == welcome
    assert estimate_cost_hint(49) == welcome


def test_estimate_cost_hint_applies_1_3_fudge_factor():
    # 4_000_000 chars → approx_tokens = 1_000_000 → cost = 1.0 * 3.0 * 1.3 = 3.90.
    # Without the *1.3 fudge this would read "$3.00" — the assert locks the fudge.
    assert estimate_cost_hint(4_000_000) == "Стоимость ≈ $3.90 (≈ 1,000,000 токенов)"


def test_estimate_cost_hint_at_50_chars_shows_cost_not_welcome():
    # 50 is NOT < 50 → cost branch. approx_tokens = max(50 // 4, 1) = 12.
    assert estimate_cost_hint(50) == "Стоимость ≈ $0.00 (≈ 12 токенов)"
