"""OpenRouter cost helpers for the Extract Tasks dialog.

Pure leaf module — stdlib only, no Tk / sounddevice — so the cost maths is
unit-testable on Linux CI independently of the dialog's UI. Holds the
per-model price table plus the two formatting helpers the dialog used to
carry inline (`_format_real_cost` and the pure part of `_update_cost_hint`).
"""
from __future__ import annotations

# Default input price per 1M tokens. Used for the upfront cost-estimate
# hint (before the user runs Извлечь — we don't know which model yet, so we
# approximate with the default).
_COST_PER_1M_INPUT_TOKENS_USD = 3.0

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


def estimate_cost_hint(char_count: int) -> str:
    """Upfront status line: cost-of-extract heuristic, or an adaptive welcome.

    Returns the welcome one-liner when there is effectively no transcript
    (< 50 chars → the dialog was opened for the manual / dictation flow),
    otherwise a "Стоимость ≈ $X.XX" estimate. Token count ≈ chars / 4; the
    *1.3 fudge pads the input-only rate toward the real prompt+completion
    cost so the upfront number doesn't undersell the post-call total.
    """
    if char_count < 50:
        # No transcript → manual-only flow; skip the cost line.
        return "Готов к работе. Извлеките из транскрипта или добавьте задачу вручную."
    approx_tokens = max(char_count // 4, 1)
    cost = approx_tokens / 1_000_000 * _COST_PER_1M_INPUT_TOKENS_USD * 1.3
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
