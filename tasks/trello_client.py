"""Thin REST wrapper around the Trello API (api.trello.com/1).

Third task backend (parallel to linear_client.py / glide_client.py). Used by:
- Settings dialog (Validate button) for `validate_key`
- ExtractTasksDialog (container dropdown) for `list_containers`
- extractor grounding for `board_context` (members + labels)
- sender for `create_card`

Trello auth differs from the other backends: an API **key** + a user **token**,
both passed as **query parameters** on every request (not a header). Card is
created in a list (`idList` required); members + labels are board-level, so
`board_context` resolves list→board first.

Public API:
    class TrelloError(Exception)
    class TrelloClient(api_key, token)
        validate_key()        → GET /members/me, returns {"name": ...}
        list_containers()     → GET /members/me/boards (nested lists)
        board_context(lid)    → resolve list→board, returns {members, labels}
        create_card(...)      → POST /cards
        close()               → release HTTP session
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.trello.com/1"
_DEFAULT_TIMEOUT_S = 30.0


class TrelloError(Exception):
    """All Trello HTTP failures bubble up as this. Message is user-facing
    (Russian where the user is the audience; English for developer-facing
    network/code conditions)."""


class TrelloClient:
    """One client per send-session. Reuse across calls."""

    def __init__(self, api_key: str, token: str):
        if not api_key or not api_key.strip():
            raise TrelloError(
                "Trello API ключ не задан. "
                "Откройте Настройки → Trello и вставьте ключ."
            )
        if not token or not token.strip():
            raise TrelloError(
                "Trello токен не задан. "
                "Откройте Настройки → Trello и вставьте токен."
            )
        self._api_key = api_key.strip()
        self._token = token.strip()
        self._session = requests.Session()

    def close(self) -> None:
        """Close the underlying connection pool. Safe to call from a separate
        thread to interrupt an in-flight request."""
        self._session.close()

    # ── HTTP plumbing ────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ):
        """Single HTTP entry point. Returns parsed JSON on 2xx (list or dict).

        Auth (key + token) is merged into the query params on every call.
        Raises TrelloError with a user-facing message on network failure or
        non-2xx status.
        """
        url = f"{_BASE_URL}{path}"
        query = {"key": self._api_key, "token": self._token}
        if params:
            query.update(params)

        try:
            resp = self._session.request(method, url, params=query, timeout=timeout)
        except requests.exceptions.ConnectionError as e:
            raise TrelloError(f"Нет соединения с Trello: {e}") from e
        except requests.exceptions.Timeout as e:
            raise TrelloError(f"Таймаут Trello (>{timeout}s)") from e
        except requests.exceptions.RequestException as e:
            raise TrelloError(f"Ошибка сети Trello: {e}") from e

        if 200 <= resp.status_code < 300:
            try:
                return resp.json() if resp.content else {}
            except ValueError as e:
                raise TrelloError(
                    f"Trello вернул не-JSON ответ: {resp.text[:200]}",
                ) from e

        body = (resp.text or "").strip()[:200]
        raise TrelloError(f"Trello вернул {resp.status_code}: {body}")

    # ── Public operations ────────────────────────────────────────────

    def validate_key(self) -> dict:
        """Cheapest call that proves auth works: GET /members/me.

        Returns {"name": <fullName or username>} for the success badge.
        """
        me = self._request("GET", "/members/me", params={"fields": "fullName,username"})
        if not isinstance(me, dict):
            raise TrelloError(f"Trello /members/me вернул неожиданный формат: {type(me).__name__}")
        name = me.get("fullName") or me.get("username") or "(unknown)"
        return {"name": name}
