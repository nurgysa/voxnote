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
        add_comment(cid, txt) → POST /cards/{id}/actions/comments
        list_open_cards(lid)  → open cards in a list (dedup registry)
        list_card_comments(cid) → comment texts on a card (dedup idempotency)
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

        Two calls: GET /lists/{id}?fields=idBoard to resolve the board, then
        GET /boards/{idBoard} with members + labels. The nested-resource
        expansion (members=all / labels=all) is NOT honoured on
        GET /lists/{id}/board — that endpoint returns only board ``fields``,
        leaving members/labels empty — so we fetch the board directly. This
        is the reliable path (Codex P2 on PR #79).
        """
        if not list_id:
            raise TrelloError("list_id обязателен для board_context")
        lst = self._request("GET", f"/lists/{list_id}", params={"fields": "idBoard"})
        board_id = lst.get("idBoard") if isinstance(lst, dict) else None
        if not board_id:
            raise TrelloError(
                f"Trello: не удалось определить доску для списка {list_id}",
            )
        board = self._request(
            "GET", f"/boards/{board_id}",
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
                f"Trello /boards/{board_id} вернул неожиданный формат: "
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

    def list_open_cards(self, list_id: str) -> list[dict]:
        """Open cards on the BOARD that owns ``list_id`` (board-level so a
        duplicate moved to another list is still caught), for dedup.

        Resolves list→board (same as board_context), then GET
        /boards/{id}/cards?filter=open. Returns card dicts (id, name, desc,
        url, idShort, shortLink). Raises TrelloError on failure. A full
        board returns up to 1000 open cards; if exactly 1000 come back the
        board may be truncated — logged as a WARNING (path-to-scale: switch
        to server-side /search).
        """
        if not list_id:
            raise TrelloError("list_id обязателен для list_open_cards")
        lst = self._request("GET", f"/lists/{list_id}", params={"fields": "idBoard"})
        board_id = lst.get("idBoard") if isinstance(lst, dict) else None
        if not board_id:
            raise TrelloError(
                f"Trello: не удалось определить доску для списка {list_id}",
            )
        cards = self._request(
            "GET", f"/boards/{board_id}/cards",
            params={"filter": "open", "fields": "name,desc,url,idShort,shortLink"},
        )
        if not isinstance(cards, list):
            raise TrelloError(
                f"Trello /boards/{board_id}/cards вернул неожиданный формат: "
                f"{type(cards).__name__}",
            )
        if len(cards) >= 1000:
            logger.warning(
                "Trello board %s returned %d open cards; dedup may be "
                "truncated (consider server-side search)", board_id, len(cards),
            )
        return cards

    def create_card(
        self,
        *,
        id_list: str,
        name: str,
        desc: str | None = None,
        id_members: list[str] | None = None,
        id_labels: list[str] | None = None,
        due: str | None = None,
    ) -> dict:
        """POST /cards — create one card in a list. Returns the response dict.

        Trello accepts card params as query params (even on POST). Arrays
        (idMembers, idLabels) are comma-joined. None/empty optionals are
        omitted. No idempotency-key — Trello has no such header (retry may
        duplicate; documented in the spec).
        """
        if not id_list:
            raise TrelloError("id_list обязателен для create_card")
        if not name or not name.strip():
            raise TrelloError("name обязателен для create_card")

        params: dict = {"idList": id_list, "name": name}
        if desc:
            params["desc"] = desc
        if id_members:
            params["idMembers"] = ",".join(id_members)
        if id_labels:
            params["idLabels"] = ",".join(id_labels)
        if due:
            params["due"] = due
        return self._request("POST", "/cards", params=params)

    def add_comment(self, card_id: str, text: str) -> None:
        """Post a comment to an existing card (task-dedup).

        ``card_id`` is the full card id (``card["id"]``), NOT the #idShort
        badge value. Raises TrelloError on network/HTTP failure.
        """
        if not card_id:
            raise TrelloError("card_id обязателен для add_comment")
        self._request(
            "POST", f"/cards/{card_id}/actions/comments", params={"text": text},
        )

    def list_card_comments(self, card_id: str) -> list[str]:
        """Comment texts on a card (dedup idempotency check).

        GET /cards/{id}/actions?filter=commentCard → each action's
        data.text. Raises TrelloError on HTTP/network failure.
        """
        if not card_id:
            raise TrelloError("card_id обязателен для list_card_comments")
        actions = self._request(
            "GET", f"/cards/{card_id}/actions", params={"filter": "commentCard"},
        )
        if not isinstance(actions, list):
            return []
        return [(a.get("data") or {}).get("text") or "" for a in actions]
