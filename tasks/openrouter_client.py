"""Thin REST wrapper around OpenRouter Chat Completions.

We deliberately keep this client dumb: no business logic, no validation
beyond HTTP status. The orchestrator (tasks/extractor.py, Phase 6.1)
builds prompts and parses responses.

Endpoints used:
- POST /chat/completions     — main extraction call
- GET  /auth/key              — Validate button in Settings (also returns balance)
- GET  /models                — Phase 6.4, full model catalog (not yet used)

Authentication: Bearer token in `Authorization` header.
Optional headers (HTTP-Referer, X-Title) help OpenRouter's leaderboard
and don't affect API behavior.
"""
from __future__ import annotations

import requests

_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_TIMEOUT_S = 60.0  # extract calls are slow; 60s covers Sonnet 4.5 on 30-min meetings


class OpenRouterError(Exception):
    """All OpenRouter HTTP/transport failures bubble up as this."""


class OpenRouterClient:
    """One client per session. Reuse it across multiple calls.

    Thread-safe enough for our use case: the underlying requests.Session
    handles concurrent calls via its connection pool.
    """

    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise OpenRouterError(
                "OpenRouter API ключ не задан. "
                "Откройте Настройки → OpenRouter и вставьте ключ."
            )
        self._api_key = api_key.strip()
        self._session = requests.Session()
        self._session.headers.update(self._build_headers())

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/audio-transcriber",
            "X-Title": "Audio Transcriber",
        }

    def close(self) -> None:
        """Close the underlying connection pool. Safe to call multiple times.

        Used by the dialog's cancel handler to interrupt an in-flight request
        from another thread (closes sockets immediately).
        """
        self._session.close()

    def validate_key(self) -> dict:
        """Cheap GET /auth/key — returns label, usage, balance_remaining.

        On success: returns dict with keys:
            - label: str (human-readable key label)
            - usage: float (USD spent so far)
            - limit: float | None (USD cap, or None for unlimited)
            - balance_remaining: float | None (limit - usage, or None)

        On any HTTP error or network failure, raises OpenRouterError.
        """
        try:
            resp = self._session.get(
                f"{_BASE_URL}/auth/key",
                timeout=10.0,
            )
        except requests.exceptions.ConnectionError as e:
            raise OpenRouterError(f"Нет соединения с OpenRouter: {e}") from e
        except requests.exceptions.Timeout as e:
            raise OpenRouterError("Таймаут подключения к OpenRouter") from e

        if resp.status_code != 200:
            raise OpenRouterError(
                f"OpenRouter вернул {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json().get("data", {})
        usage = float(data.get("usage", 0.0))
        limit = data.get("limit")  # may be None
        return {
            "label": data.get("label", ""),
            "usage": usage,
            "limit": float(limit) if limit is not None else None,
            "balance_remaining": (float(limit) - usage) if limit is not None else None,
        }
