"""Tests for tasks.errors.humanize — pure exception → user-text mapping."""
from __future__ import annotations

import pytest

from tasks.errors import humanize

# ── DNS / corporate VPN ──────────────────────────────────────────────


def test_humanize_corporate_dns_failure_suggests_vpn():
    """Glide DNS error mentioning os.tensor-ai.tech → "включите VPN"."""
    msg = (
        "Нет соединения с Glide: HTTPSConnectionPool(host='os.tensor-ai.tech', "
        "port=443): Max retries exceeded with url: /api/v1/integrations/in/tasks "
        "(Caused by NameResolutionError(\"HTTPSConnection(host='os.tensor-ai.tech', "
        "port=443): Failed to resolve 'os.tensor-ai.tech' "
        "([Errno 11001] getaddrinfo failed)\"))"
    )
    out = humanize(Exception(msg))
    assert "VPN" in out
    assert "Glide" in out


def test_humanize_public_dns_failure_suggests_internet():
    """Linear DNS error (public host) → "проверьте интернет"."""
    msg = "Нет соединения с Linear: getaddrinfo failed for api.linear.app"
    out = humanize(Exception(msg))
    assert "интернет" in out.lower()
    assert "Linear" in out
    assert "VPN" not in out  # public host shouldn't suggest VPN


# ── Connection refused / network unreachable ─────────────────────────


def test_humanize_connection_refused():
    msg = "Нет соединения с Linear: Connection refused on port 443"
    out = humanize(Exception(msg))
    assert "не отвечает" in out.lower() or "соединение" in out.lower()


# ── Timeouts ─────────────────────────────────────────────────────────


def test_humanize_timeout_openrouter():
    msg = "Таймаут подключения к OpenRouter (>10s)"
    out = humanize(Exception(msg))
    assert "OpenRouter" in out
    assert "вовремя" in out.lower() or "врем" in out.lower()


def test_humanize_timeout_linear():
    msg = "Таймаут Linear (>30s)"
    out = humanize(Exception(msg))
    assert "Linear" in out


# ── HTTP status codes ────────────────────────────────────────────────


def test_humanize_401_says_invalid_key():
    msg = "Linear вернул 401: invalid token"
    out = humanize(Exception(msg))
    assert "API-ключ" in out
    assert "Linear" in out
    assert "Настрой" in out  # points user to Settings


def test_humanize_403_says_no_permission():
    msg = "Glide вернул 403: forbidden"
    out = humanize(Exception(msg))
    assert "прав" in out.lower()


def test_humanize_429_says_rate_limit():
    msg = "OpenRouter вернул 429: rate limit exceeded"
    out = humanize(Exception(msg))
    assert "лимит" in out.lower()
    assert "OpenRouter" in out


def test_humanize_500_says_server_unavailable():
    msg = "Linear вернул 500: internal server error"
    out = humanize(Exception(msg))
    assert "сервер" in out.lower()
    assert "Linear" in out


def test_humanize_503_classifies_as_5xx():
    msg = "Glide вернул 503"
    out = humanize(Exception(msg))
    assert "недоступ" in out.lower() or "сервер" in out.lower()


def test_humanize_400_falls_into_4xx_bucket():
    msg = "OpenRouter вернул 400: response_format unsupported"
    out = humanize(Exception(msg))
    assert "400" in out or "отклонил" in out.lower()


def test_humanize_does_not_match_token_count_as_status():
    """1400 tokens shouldn't match 400. \b boundary protects this."""
    msg = "OpenRouter usage: 1400 tokens"
    out = humanize(Exception(msg))
    # Should NOT match HTTP 400 path; falls through to fallback
    assert "отклонил" not in out.lower()


# ── LLM-specific ─────────────────────────────────────────────────────


def test_humanize_extraction_error():
    class ExtractionError(Exception):
        pass
    out = humanize(ExtractionError("LLM не вернул валидных задач"))
    assert "извлечь" in out.lower() or "модел" in out.lower()


def test_humanize_malformed_json():
    msg = "OpenRouter вернул не-JSON ответ: <html>..."
    out = humanize(Exception(msg))
    assert "некорректн" in out.lower() or "ответ" in out.lower()


# ── Fallbacks ────────────────────────────────────────────────────────


def test_humanize_unknown_message_uses_fallback_when_provided():
    out = humanize(Exception("some weird unknown error"),
                   fallback="Не удалось выполнить операцию.")
    assert out == "Не удалось выполнить операцию."


def test_humanize_unknown_message_truncates_long_msg():
    long = "X" * 200
    out = humanize(Exception(long))
    assert len(out) <= 120
    assert out.endswith("…")


def test_humanize_empty_message_returns_generic():
    out = humanize(Exception(""))
    assert out  # non-empty
    assert "ошибка" in out.lower() or "произошла" in out.lower()


def test_humanize_empty_message_uses_fallback():
    out = humanize(Exception(""), fallback="Custom fallback")
    assert out == "Custom fallback"


def test_humanize_none_safe():
    """Defensive: shouldn't crash on None."""
    out = humanize(None)  # type: ignore[arg-type]
    assert out  # returns something, doesn't raise


# ── Backend detection ────────────────────────────────────────────────


def test_humanize_uses_correct_backend_name_in_text():
    """The friendly text should mention the right backend."""
    cases = [
        ("Linear вернул 401", "Linear"),
        ("OpenRouter вернул 401", "OpenRouter"),
        ("Glide вернул 401", "Glide"),
        ("Trello вернул 401", "Trello"),
    ]
    for msg, expected_name in cases:
        out = humanize(Exception(msg))
        assert expected_name in out, f"expected {expected_name} in: {out!r}"


def test_humanize_trello_timeout_names_trello():
    out = humanize(Exception("Таймаут Trello (>30s)"))
    assert "Trello" in out
    assert "вовремя" in out.lower() or "врем" in out.lower()


def test_humanize_unknown_backend_uses_generic_сервер():
    msg = "вернул 401: ..."  # no backend name in message
    out = humanize(Exception(msg))
    assert "сервер" in out.lower()
