"""Unit tests for the extracted Settings banner FSM.

ui.dialogs.settings_helpers is a pure leaf (stdlib only) — the banner
decision tree tests on Linux CI without the dialog's Tk/sounddevice chain.
Behaviour is locked against the pre-extraction _update_banner decision
tree. languages/providers are injected as fakes so the FSM is exercised in
isolation from the real LANGUAGES / PROVIDERS tables.
"""
from __future__ import annotations

from ui.dialogs.settings_helpers import (
    compute_banner_state,
    format_glide_success,
    format_linear_success,
    format_openrouter_success,
    format_trello_success,
)


class _FakeProvider:
    def __init__(self, supports_mixed: bool):
        self.supports_mixed = supports_mixed


_LANGUAGES = {"Русский": "ru", "Смешанный (KZ+RU+EN)": "mixed"}
_PROVIDERS = {
    "AssemblyAI": _FakeProvider(supports_mixed=True),
    "Deepgram": _FakeProvider(supports_mixed=False),
}
_MIXED = "Смешанный (KZ+RU+EN)"


def test_empty_cloud_key_shows_stt_banner():
    action, text = compute_banner_state("", "Русский", "AssemblyAI", _LANGUAGES, _PROVIDERS)
    assert action == "stt"
    assert text == "⚠ Введите ключ провайдера STT (вкладка «Транскрипция») →"


def test_whitespace_cloud_key_shows_stt_banner():
    # Whitespace-only key is "empty" after strip — same as no key.
    action, _ = compute_banner_state("   ", "Русский", "AssemblyAI", _LANGUAGES, _PROVIDERS)
    assert action == "stt"


def test_stt_key_takes_priority_over_lang_warning():
    # Empty key wins (priority 1) even when mixed+unsupported would warn.
    action, _ = compute_banner_state("", _MIXED, "Deepgram", _LANGUAGES, _PROVIDERS)
    assert action == "stt"


def test_mixed_with_unsupported_provider_warns():
    action, text = compute_banner_state("sk-xxx", _MIXED, "Deepgram", _LANGUAGES, _PROVIDERS)
    assert action == "lang"
    assert "Deepgram" in text
    assert "Смешанный" in text


def test_mixed_with_supporting_provider_hides_banner():
    action, text = compute_banner_state("sk-xxx", _MIXED, "AssemblyAI", _LANGUAGES, _PROVIDERS)
    assert action is None
    assert text == ""


def test_non_mixed_language_hides_banner():
    action, _ = compute_banner_state("sk-xxx", "Русский", "Deepgram", _LANGUAGES, _PROVIDERS)
    assert action is None


def test_unknown_provider_hides_banner():
    # provider_cls is None → no warning (the `is not None` guard).
    action, _ = compute_banner_state("sk-xxx", _MIXED, "Ghost", _LANGUAGES, _PROVIDERS)
    assert action is None


# ── per-backend validation success formatters ──────────────────────

def test_format_openrouter_success_with_balance():
    assert format_openrouter_success({"balance_remaining": 12.5}) == "✓ Активен (баланс: $12.50)"


def test_format_openrouter_success_zero_balance_still_shows_balance():
    # `is not None` (not truthiness) — a $0.00 balance must still render.
    assert format_openrouter_success({"balance_remaining": 0}) == "✓ Активен (баланс: $0.00)"


def test_format_openrouter_success_without_balance_uses_label():
    assert format_openrouter_success({"label": "Tier-1"}) == "✓ Активен (Tier-1)"


def test_format_openrouter_success_without_balance_or_label_is_unlimited():
    assert format_openrouter_success({}) == "✓ Активен (unlimited)"


def test_format_linear_success_prefers_name():
    assert format_linear_success({"name": "Nur", "email": "n@x.io"}) == "✓ Подключено: Nur"


def test_format_linear_success_falls_back_to_email():
    assert format_linear_success({"email": "n@x.io"}) == "✓ Подключено: n@x.io"


def test_format_linear_success_unknown_when_empty():
    assert format_linear_success({}) == "✓ Подключено: (unknown)"


def test_format_glide_success_with_samples():
    info = {"board_count": 3, "sample_names": ["A", "B"]}
    assert format_glide_success(info) == "✓ Подключено: 3 досок (A, B)"


def test_format_glide_success_without_samples():
    info = {"board_count": 0, "sample_names": []}
    assert format_glide_success(info) == "✓ Подключено: 0 досок"


def test_format_trello_success_with_name():
    assert format_trello_success({"name": "My Board"}) == "✓ Подключено: My Board"


def test_format_trello_success_unknown_when_missing():
    assert format_trello_success({}) == "✓ Подключено: (unknown)"
