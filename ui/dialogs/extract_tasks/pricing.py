"""OpenRouter cost helpers for the Extract Tasks dialog.

Pure leaf module — stdlib only, no Tk / sounddevice — so the cost maths is
unit-testable on Linux CI independently of the dialog's UI. Holds the
per-model price table plus the two formatting helpers the dialog used to
carry inline (`_format_real_cost` and the pure part of `_update_cost_hint`).
"""
from __future__ import annotations

# Flat fallback rate per 1M tokens for models missing from the table —
# custom OpenRouter slugs still get a ballpark forecast.
_COST_PER_1M_INPUT_TOKENS_USD = 3.0

# Assumed completion-to-prompt ratio for the upfront forecast.
# Calibration: a typical 1-hour meeting ≈ 50k chars ≈ 12.5k input tokens
# yields ~1.5k tokens of task-JSON (10–20 tasks) → ≈ 0.12. Replaces the
# old flat ×1.3 input-rate fudge with an honest output term.
_EST_OUTPUT_RATIO = 0.12

# Below this transcript length the dialog is in the manual/dictation
# flow — no forecast is shown (and none is remembered for the
# forecast-vs-actual tail).
MIN_FORECAST_CHARS = 50

# Per-model pricing for the post-call **real** cost display in
# _on_extract_success — uses response.usage tokens × these rates.
# Tuple is (input_$_per_1M, output_$_per_1M). Updated 2026-04 from
# OpenRouter pricing pages. May drift; if the response itself includes
# `usage.cost`, that authoritative value is used and this table is
# bypassed (see format_real_cost). New models should be added here
# alongside constants._CURATED_MODELS.
_MODEL_PRICING_USD_PER_M = {
    "google/gemini-3.5-flash":      (1.50,  9.00),  # released 2026-05-19, 1M context
    # Legacy entries kept so existing history records (which may carry stale
    # model slugs from pre-2026-05-28 runs on the maintainer's dev machine)
    # still render a cost on the History view. The curated dropdown
    # (constants._CURATED_MODELS) restricts NEW runs to gemini-3.5-flash;
    # these lookups are read-only safety nets, not user-selectable options.
    "anthropic/claude-sonnet-4.5":  (3.00, 15.00),
    "anthropic/claude-haiku-4.5":   (1.00,  5.00),
    "openai/gpt-4o":                (2.50, 10.00),
    "google/gemini-2.5-pro":        (1.25, 10.00),
    "deepseek/deepseek-v3":         (0.27,  1.10),
}


def estimate_cost(char_count: int, model: str) -> float:
    """Forecast extraction cost in USD using the SELECTED model's rates.

    Input tokens ≈ chars/4; output ≈ _EST_OUTPUT_RATIO × input. Unknown
    models fall back to the flat default rate for both directions.
    """
    approx_in = max(char_count // 4, 1)
    approx_out = int(approx_in * _EST_OUTPUT_RATIO)
    rates = _MODEL_PRICING_USD_PER_M.get(model)
    if rates is None:
        in_rate = out_rate = _COST_PER_1M_INPUT_TOKENS_USD
    else:
        in_rate, out_rate = rates
    return (approx_in * in_rate + approx_out * out_rate) / 1_000_000.0


def estimate_cost_hint(char_count: int, model: str) -> str:
    """Upfront status line: per-model forecast, or an adaptive welcome.

    Returns the welcome one-liner when there is effectively no transcript
    (< MIN_FORECAST_CHARS → manual / dictation flow), otherwise a
    «Стоимость ≈ $X.XX» forecast from estimate_cost.
    """
    if char_count < MIN_FORECAST_CHARS:
        # No transcript → manual-only flow; skip the cost line.
        return "Готов к работе. Извлеките из транскрипта или добавьте задачу вручную."
    approx_tokens = max(char_count // 4, 1)
    cost = estimate_cost(char_count, model)
    return f"Стоимость ≈ ${cost:.2f} (≈ {approx_tokens:,} токенов)"


def format_real_cost(usage: dict, model: str) -> str:
    """Build a "X tokens · $0.0123" string from response.usage.

    Returns "" if usage is empty (defensive — all callers should
    already guard, but defensive helps composability).

    Cost source priority:
      1. usage["cost"] if OpenRouter included it (authoritative).
      2. computed: prompt × in_rate + completion × out_rate, where
         rates come from _MODEL_PRICING_USD_PER_M for the actual
         model that served the request.
      3. token count only — for unknown models we still show
         throughput so the user knows extraction did something.

    Format examples:
        "1,234↑ + 567↓ т.  ·  $0.0234"      (full, known model)
        "1,234↑ + 567↓ т."                    (unknown model)
    """
    if not usage:
        return ""
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    if prompt == 0 and completion == 0:
        return ""

    cost: float | None = None
    if "cost" in usage and isinstance(usage.get("cost"), (int, float)):
        cost = float(usage["cost"])
    else:
        rates = _MODEL_PRICING_USD_PER_M.get(model)
        if rates is not None:
            in_rate, out_rate = rates
            cost = (prompt * in_rate + completion * out_rate) / 1_000_000.0

    # Compose. Russian commas via .format spec; locale-agnostic comma
    # (1,234) is intentional — easier to read than 1234.
    toks_part = f"{prompt:,}↑ + {completion:,}↓ т."
    if cost is None:
        return toks_part
    return f"{toks_part}  ·  ${cost:.4f}"
