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

    def list_containers(self) -> list[dict]:
        """GET /members/me/boards with nested open lists.

        One round-trip. Returns a flat list of
        {board_name, list_id, list_name} — one row per list. Boards with no
        open lists contribute nothing (a card needs a list to land in).
        """
        boards = self._request(
            "GET", "/members/me/boards",
            params={
                "fields": "name",
                "filter": "open",
                "lists": "open",
                "list_fields": "name",
            },
        )
        if not isinstance(boards, list):
            raise TrelloError(
                f"Trello /members/me/boards вернул неожиданный формат: "
                f"{type(boards).__name__}",
            )
        rows: list[dict] = []
        for b in boards:
            board_name = b.get("name", "?")
            for lst in b.get("lists") or []:
                lid = lst.get("id")
                if not lid:
                    continue
                rows.append({
                    "board_name": board_name,
                    "list_id": lid,
                    "list_name": lst.get("name", "?"),
                })
        return rows

    def board_context(self, list_id: str) -> dict:
        """Resolve list→board and return grounding data for the LLM.

        Returns {"members": [...], "labels": [...]} in the shape
        tasks.extractor.build_prompt expects: members carry id + name +
        displayName; labels carry id + name. Labels with an empty name are
        dropped — Trello allows colour-only labels, but the LLM cannot
        address an unnamed label and an empty name pollutes the prompt.

        Single nested call: GET /lists/{id}/board with members=all & labels=all.
        If the API ever rejects that nesting, fall back to GET
        /lists/{id}?fields=idBoard then GET /boards/{idBoard}?members=all&...
        """
        if not list_id:
            raise TrelloError("list_id обязателен для board_context")
        board = self._request(
            "GET", f"/lists/{list_id}/board",
            params={
                "fields": "id",
                "members": "all",
                "member_fields": "fullName,username",
                "labels": "all",
                "label_fields": "name,color",
            },
        )
        if not isinstance(board, dict):
            raise TrelloError(
                f"Trello /lists/{list_id}/board вернул неожиданный формат: "
                f"{type(board).__name__}",
            )
        members = []
        for m in board.get("members") or []:
            mid = m.get("id")
            if not mid:
                continue
            name = m.get("fullName") or m.get("username") or "?"
            members.append({"id": mid, "name": name, "displayName": name})
        labels = []
        for lbl in board.get("labels") or []:
            lid = lbl.get("id")
            name = (lbl.get("name") or "").strip()
            if lid and name:
                labels.append({"id": lid, "name": name})
        return {"members": members, "labels": labels}
