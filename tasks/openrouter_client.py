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
        except requests.exceptions.RequestException as e:
            raise OpenRouterError(f"Ошибка сети OpenRouter: {e}") from e

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

    def complete(
        self,
        model: str,
        messages: list[dict],
        json_mode: bool = True,
        temperature: float = 0.2,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> dict:
        """POST /chat/completions and return the parsed response.

        Args:
            model: OpenRouter model slug (e.g. 'anthropic/claude-sonnet-4.5').
            messages: standard OpenAI-style chat messages.
            json_mode: if True, request response_format=json_object. Some models
                reject this with 400; in that case caller should retry with
                json_mode=False and rely on prompt-level instruction.
            temperature: low value (0.2) keeps extraction deterministic.
            timeout: seconds before requests raises Timeout.

        Returns dict:
            - content: str (the assistant message)
            - usage: dict with prompt_tokens / completion_tokens
            - model: str (echoed model slug, useful for logging)

        Raises OpenRouterError on any HTTP or network failure. 429 errors
        include the Retry-After value in the message string for caller-side
        parsing.
        """
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            resp = self._session.post(
                f"{_BASE_URL}/chat/completions",
                json=body,
                timeout=timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise OpenRouterError(f"Нет соединения с OpenRouter: {e}") from e
        except requests.exceptions.Timeout as e:
            raise OpenRouterError(f"Таймаут OpenRouter (>{timeout}s)") from e
        except requests.exceptions.RequestException as e:
            raise OpenRouterError(f"Ошибка сети OpenRouter: {e}") from e

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "?")
            raise OpenRouterError(f"OpenRouter 429 rate-limit (retry after {retry_after}s)")
        if resp.status_code != 200:
            raise OpenRouterError(
                f"OpenRouter вернул {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        choice = data["choices"][0]
        return {
            "content": choice["message"]["content"],
            "usage": data.get("usage", {}),
            "model": data.get("model", model),
        }
