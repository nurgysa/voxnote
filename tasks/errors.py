"""User-facing error humanization for the tasks pipeline.

Backend clients (LinearClient / OpenRouterClient / GlideClient) raise
exceptions with technical messages — they include host:port, errno,
and Python class names so logs stay debuggable. This module turns
those exceptions into short, plain-Russian messages with **action
items** for the user to fix the situation.

Two-layer split, intentional:

- ``tasks/sender.py:_short_error_code`` — extracts ``"401"``, ``"429"``,
  ``"network"``, ``"timeout"`` for status badges and retry decisions.
- ``tasks/errors.py:humanize`` (this module) — produces UI display text:
  *"Неверный API-ключ. Проверьте в Настройках."* etc.

Both consume the same exception; one drives logic, the other drives UX.

Usage:
    from tasks.errors import humanize
    try:
        ...
    except (LinearError, GlideError, OpenRouterError) as e:
        ui_text = humanize(e)        # always returns a non-empty string
        status_label.configure(text=f"✗ {ui_text}", text_color=RED)

The function is **defensive**: any unrecognized exception falls back to
a short generic message, never raises. That keeps the UI display path
robust even if a backend introduces a new error class tomorrow.
"""
from __future__ import annotations

import re

# Hosts that we *know* are corporate / VPN-gated. When a NameResolution
# error mentions one of these, the user almost certainly needs to enable
# VPN. Edit this list as new internal services are integrated.
_CORPORATE_HOST_PATTERNS = (
    "tensor-ai.tech",   # Glide (`os.tensor-ai.tech` and friends)
)


def humanize(
    exc: BaseException | str | None,
    *,
    fallback: str | None = None,
) -> str:
    """Convert a backend exception (or raw error message string) to a
    short user-friendly Russian message. Always returns a non-empty
    string.

    Accepts:
        - ``BaseException`` subclass instance (calls ``str(exc)``)
        - plain string (some old call-sites pass ``str(e)`` directly)
        - ``None`` (returns fallback or generic)

    The function inspects the message for known patterns produced by
    the project's *Error classes (LinearError, GlideError,
    OpenRouterError, requests.* exceptions). For unknown exceptions
    it returns ``fallback`` (or a short generic message if fallback
    is None).

    Examples:
        humanize(GlideError("Нет соединения с Glide: ... 'os.tensor-ai.tech' ..."))
            → "Не удаётся подключиться к Glide. Включите VPN — это корпоративный сервис."
        humanize(LinearError("Linear вернул 401: invalid token"))
            → "Неверный API-ключ Linear. Проверьте в Настройках."
        humanize("Таймаут подключения к OpenRouter (>10s)")
            → "OpenRouter не ответил вовремя. Проверьте интернет и попробуйте снова."
    """
    if exc is None:
        return fallback or "Произошла ошибка."
    if isinstance(exc, str):
        msg = exc
        exc_type_name = ""
    else:
        msg = str(exc)
        exc_type_name = type(exc).__name__
    if not msg:
        return fallback or "Произошла ошибка."

    # Identify the backend from the message prefix (the clients prefix
    # their errors with a recognizable name).
    backend = _detect_backend(msg)
    backend_name = {"linear": "Linear", "glide": "Glide",
                    "trello": "Trello", "openrouter": "OpenRouter"}.get(backend, "сервер")

    msg_lower = msg.lower()

    # ── Network: DNS failure ─────────────────────────────────────────
    # NameResolutionError / getaddrinfo failed → the host couldn't be
    # resolved at all. If it's a known corporate host, suggest VPN.
    if (
        "nameresolutionerror" in msg_lower
        or "getaddrinfo failed" in msg_lower
        or "name or service not known" in msg_lower
        or "11001" in msg                # WinSock WSAHOST_NOT_FOUND
    ):
        if any(host in msg_lower for host in _CORPORATE_HOST_PATTERNS):
            return (
                f"Не удаётся подключиться к {backend_name}. "
                f"Включите VPN — это корпоративный сервис."
            )
        return (
            f"Не удаётся подключиться к {backend_name}. "
            f"Проверьте интернет."
        )

    # ── Network: connection refused / unreachable ────────────────────
    if (
        "connection refused" in msg_lower
        or "10061" in msg                # WSAECONNREFUSED
        or "no route to host" in msg_lower
        or "network is unreachable" in msg_lower
    ):
        return (
            f"{backend_name} не отвечает (соединение отклонено). "
            f"Проверьте интернет или попробуйте позже."
        )

    # ── Network: generic «Нет соединения» from our wrappers ──────────
    if "нет соединения" in msg_lower or "connectionerror" in msg_lower:
        return f"Нет соединения с {backend_name}. Проверьте интернет."

    # ── Timeout ──────────────────────────────────────────────────────
    if "таймаут" in msg_lower or "timeout" in msg_lower or "timed out" in msg_lower:
        return (
            f"{backend_name} не ответил вовремя. "
            f"Проверьте интернет и попробуйте снова."
        )

    # ── HTTP status codes ────────────────────────────────────────────
    # Match \b4\d\d\b / \b5\d\d\b — same conservative pattern as
    # _short_error_code in sender.py to avoid false positives like
    # "1400 tokens" matching "400".
    status = _extract_http_status(msg)
    if status == 401:
        return f"Неверный API-ключ {backend_name}. Проверьте в Настройках."
    if status == 403:
        return (
            f"Нет прав доступа к {backend_name}. "
            f"Проверьте права API-ключа."
        )
    if status == 404:
        return f"{backend_name}: запрашиваемый ресурс не найден."
    if status == 429:
        return (
            f"{backend_name}: превышен лимит запросов. "
            f"Подождите минуту и попробуйте снова."
        )
    if status is not None and 500 <= status < 600:
        return (
            f"{backend_name} временно недоступен (сервер). "
            f"Попробуйте позже."
        )
    if status is not None and 400 <= status < 500:
        return f"{backend_name} отклонил запрос (HTTP {status})."

    # ── LLM-specific: malformed / no tasks ───────────────────────────
    if "extractionerror" in exc_type_name.lower() or "не вернул валидных задач" in msg_lower:
        return (
            "Модель не смогла извлечь задачи из текста. "
            "Попробуйте другую модель или перефразируйте."
        )
    if "не-json" in msg_lower or "malformed json" in msg_lower:
        return (
            f"{backend_name} вернул некорректный ответ. "
            f"Попробуйте другую модель или повторите."
        )

    # ── Fallback ─────────────────────────────────────────────────────
    if fallback is not None:
        return fallback
    # Last resort: trim the technical message to first sentence
    # (avoids dumping a multi-line traceback into the UI).
    short = msg.split("\n", 1)[0].strip()
    if len(short) > 120:
        short = short[:117] + "…"
    return short or "Произошла ошибка."


# ── Helpers ──────────────────────────────────────────────────────────


def _detect_backend(msg: str) -> str | None:
    """Identify which backend produced the error from its message prefix.

    Returns ``"linear"`` / ``"glide"`` / ``"openrouter"`` / None.
    """
    lower = msg.lower()
    if "linear" in lower:
        return "linear"
    if "glide" in lower:
        return "glide"
    if "trello" in lower:
        return "trello"
    if "openrouter" in lower or "open router" in lower:
        return "openrouter"
    return None


def _extract_http_status(msg: str) -> int | None:
    """Extract a 3-digit HTTP status code (4xx/5xx) from an error
    message. Mirrors the conservative regex in sender._short_error_code
    to avoid matching token counts or other 3-digit numbers.
    """
    m = re.search(r"\b(4\d\d|5\d\d)\b", msg)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None
