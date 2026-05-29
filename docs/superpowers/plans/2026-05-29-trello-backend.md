# Trello Task Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Trello as a third task backend alongside Linear and Glide, with rich LLM grounding (board members + labels) so the LLM auto-assigns assignee and labels at full parity with Linear.

**Architecture:** Trello plugs into the existing `tasks/backends/` Protocol + Adapter layer. A new `TrelloClient` (REST over `requests`, key+token query-param auth) is wrapped by a new `TrelloBackend` adapter. The orchestrator (`tasks/sender.py`) and the Extract dialog stay backend-agnostic; we add the client, the adapter, factory registration, error humanization, two-field Settings UI, and de-hardcode the dialog's binary backend forks into dictionaries.

**Tech Stack:** Python 3.10+, `requests`, CustomTkinter, pytest. Mirrors `tasks/glide_client.py` (client) and `tasks/backends/linear.py` (rich adapter).

**Spec:** `docs/superpowers/specs/2026-05-29-trello-backend-design.md` (commit 8de9770).

**Key decisions (from spec):** rich grounding parity with Linear (D1); priority rendered as a `**Приоритет:** …` line in the card description (D2); container = Trello **list**, grounding resolves list→board (D3); "Board / List" folded into `Container.name` with `key=None` so the dialog's inline dropdown format needs no change (D4); `trello_enabled` defaults to **false** / opt-in (D5).

**Conventions:** Russian for user-facing strings (UI text, card description content, error messages shown to users); English for code, comments, commit messages. Narrow `except` classes. Run `pytest` + `python -m ruff check .` before every commit; baseline is 333 green tests.

---

## File Structure

**New files:**
- `tasks/trello_client.py` — `TrelloClient` + `TrelloError`. REST wrapper; key+token query-param auth; `_request` maps transport/HTTP failures to `TrelloError` with Russian messages. Ops: `validate_key`, `list_containers`, `board_context`, `create_card`, `close`.
- `tasks/backends/trello.py` — `TrelloBackend` adapter (mirrors `linear.py`). Maps `Task` ↔ Trello wire format; priority→desc line; `#idShort` identifier.
- `tests/test_tasks_trello_client.py` — client unit tests (mirrors `test_tasks_glide_client.py`).
- `tests/test_extract_dialog_backend_dicts.py` — CI-safe tests for the de-hardcoded backend dictionaries (imports `constants.py`, which is sounddevice-free) + source-text checks on the dialog.

**Modified files:**
- `tasks/backends/__init__.py` — `trello` branch in `backend_from_name` (reads both `trello_api_key` + `trello_token`); `__all__`; docstring.
- `tasks/sender.py` — import `TrelloError`; add to the `except (LinearError, GlideError, TrelloError)` tuple.
- `tasks/errors.py` — `_detect_backend` += `"trello"`; `backend_name` map += `"trello": "Trello"`.
- `tasks/backends/trello.py` — (new, above).
- `tests/test_tasks_backends.py` — add `TrelloBackend` adapter tests + `backend_from_name` factory tests (no factory tests exist today).
- `tests/test_tasks_errors.py` — add Trello to the backend-name case list.
- `config.example.json` — add `trello_api_key`, `trello_token`, `trello_enabled`.
- `ui/app/builder.py` — declare `_trello_key_var`, `_trello_token_var`, `_trello_enabled_var`.
- `ui/app/settings_mixin.py` — add `_on_trello_enabled_changed`.
- `ui/dialogs/settings.py` — add `_build_trello_section`; call it from the integrations assembler.
- `ui/dialogs/extract_tasks/constants.py` — add backend metadata dicts + `_TRELLO_CACHE_KEY`.
- `ui/dialogs/extract_tasks/__init__.py` — de-hardcode 9 binary backend forks; add `trello` to `_compute_enabled_backends`; add `_backend_is_configured` helper.
- `tests/test_settings_dialog_uses_api_key_row.py` — bump the api_key_row count to ≥6.

---

## Task 1: TrelloClient — construction + auth + `_request` plumbing

**Files:**
- Create: `tasks/trello_client.py`
- Test: `tests/test_tasks_trello_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tasks_trello_client.py`:

```python
"""Tests for tasks.trello_client. HTTP mocked at the requests.Session level."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from tasks.trello_client import TrelloClient, TrelloError


def _resp(status: int, *, json_body=None, text="", headers=None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.text = text
    if json_body is not None:
        r.content = b"x"
        r.json.return_value = json_body
    elif text:
        r.content = text.encode("utf-8")
        r.json.side_effect = ValueError("no JSON")
    else:
        r.content = b""
        r.json.side_effect = ValueError("no JSON")
    return r


def test_client_rejects_empty_key():
    with pytest.raises(TrelloError, match="ключ"):
        TrelloClient("", "tok")
    with pytest.raises(TrelloError, match="ключ"):
        TrelloClient("   ", "tok")


def test_client_rejects_empty_token():
    with pytest.raises(TrelloError, match="[Тт]окен"):
        TrelloClient("key", "")
    with pytest.raises(TrelloError, match="[Тт]окен"):
        TrelloClient("key", "   ")


def test_request_injects_key_and_token_as_query_params():
    c = TrelloClient("my-key", "my-token")
    with patch.object(
        c._session, "request",
        return_value=_resp(200, json_body={"id": "u-1", "fullName": "Айдар"}),
    ) as mock_req:
        c.validate_key()
    sent = mock_req.call_args.kwargs["params"]
    assert sent["key"] == "my-key"
    assert sent["token"] == "my-token"


def test_network_error_wrapped_in_trello_error():
    c = TrelloClient("k", "t")
    with patch.object(
        c._session, "request",
        side_effect=requests.exceptions.ConnectionError("DNS fail"),
    ):
        with pytest.raises(TrelloError, match="Нет соединения с Trello"):
            c.validate_key()


def test_timeout_wrapped_in_trello_error():
    c = TrelloClient("k", "t")
    with patch.object(
        c._session, "request",
        side_effect=requests.exceptions.Timeout("read timeout"),
    ):
        with pytest.raises(TrelloError, match="Таймаут Trello"):
            c.validate_key()


def test_request_raises_on_401():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(401, text="invalid token")):
        with pytest.raises(TrelloError, match="Trello вернул 401"):
            c.validate_key()


def test_request_raises_on_429():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(429, text="rate limited")):
        with pytest.raises(TrelloError, match="Trello вернул 429"):
            c.validate_key()


def test_request_raises_on_500():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(500, text="server error")):
        with pytest.raises(TrelloError, match="Trello вернул 500"):
            c.validate_key()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_trello_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tasks.trello_client'`

- [ ] **Step 3: Write minimal implementation**

Create `tasks/trello_client.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_trello_client.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/trello_client.py tests/test_tasks_trello_client.py
git add tasks/trello_client.py tests/test_tasks_trello_client.py
git commit -m "feat(trello): add TrelloClient construction + query-param auth + _request"
```

---

## Task 2: TrelloClient.list_containers (boards with nested lists)

**Files:**
- Modify: `tasks/trello_client.py` (add `list_containers`)
- Test: `tests/test_tasks_trello_client.py` (add cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_trello_client.py`:

```python
# ── list_containers ────────────────────────────────────────────────────


def test_list_containers_flattens_boards_and_lists():
    boards = [
        {"id": "b-1", "name": "Маркетинг", "lists": [
            {"id": "l-1", "name": "To Do"},
            {"id": "l-2", "name": "Doing"},
        ]},
        {"id": "b-2", "name": "Продажи", "lists": [
            {"id": "l-3", "name": "Inbox"},
        ]},
    ]
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=boards)) as mock_req:
        rows = c.list_containers()
    assert rows == [
        {"board_name": "Маркетинг", "list_id": "l-1", "list_name": "To Do"},
        {"board_name": "Маркетинг", "list_id": "l-2", "list_name": "Doing"},
        {"board_name": "Продажи", "list_id": "l-3", "list_name": "Inbox"},
    ]
    # Nested-lists query params present.
    sent = mock_req.call_args.kwargs["params"]
    assert sent["lists"] == "open"
    assert sent["filter"] == "open"


def test_list_containers_skips_boards_without_lists():
    boards = [
        {"id": "b-1", "name": "Empty", "lists": []},
        {"id": "b-2", "name": "Has", "lists": [{"id": "l-9", "name": "Backlog"}]},
    ]
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=boards)):
        rows = c.list_containers()
    assert rows == [{"board_name": "Has", "list_id": "l-9", "list_name": "Backlog"}]


def test_list_containers_rejects_non_list_response():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body={"oops": 1})):
        with pytest.raises(TrelloError, match="неожиданный формат"):
            c.list_containers()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_trello_client.py -q -k list_containers`
Expected: FAIL — `AttributeError: 'TrelloClient' object has no attribute 'list_containers'`

- [ ] **Step 3: Write minimal implementation**

Add to `tasks/trello_client.py` after `validate_key`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_trello_client.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/trello_client.py tests/test_tasks_trello_client.py
git add tasks/trello_client.py tests/test_tasks_trello_client.py
git commit -m "feat(trello): list_containers — flatten boards + nested lists"
```

---

## Task 3: TrelloClient.board_context (list→board resolution + grounding shape)

**Files:**
- Modify: `tasks/trello_client.py` (add `board_context`)
- Test: `tests/test_tasks_trello_client.py` (add cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_trello_client.py`:

```python
# ── board_context ───────────────────────────────────────────────────────


def test_board_context_maps_members_and_labels():
    board = {
        "id": "b-1",
        "members": [
            {"id": "m-1", "fullName": "Айдар Нургиса", "username": "aidar"},
            {"id": "m-2", "fullName": "", "username": "guest"},
        ],
        "labels": [
            {"id": "lbl-1", "name": "Баг", "color": "red"},
            {"id": "lbl-2", "name": "", "color": "green"},
        ],
    }
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=board)) as mock_req:
        ctx = c.board_context("l-1")
    # Member with empty fullName falls back to username.
    assert ctx["members"] == [
        {"id": "m-1", "name": "Айдар Нургиса", "displayName": "Айдар Нургиса"},
        {"id": "m-2", "name": "guest", "displayName": "guest"},
    ]
    # Empty-name label is dropped (LLM can't address it).
    assert ctx["labels"] == [{"id": "lbl-1", "name": "Баг"}]
    # Resolves via /lists/{id}/board with nested members + labels.
    assert mock_req.call_args.args[1].endswith("/lists/l-1/board")
    sent = mock_req.call_args.kwargs["params"]
    assert sent["members"] == "all"
    assert sent["labels"] == "all"


def test_board_context_rejects_empty_list_id():
    c = TrelloClient("k", "t")
    with pytest.raises(TrelloError, match="list_id обязателен"):
        c.board_context("")


def test_board_context_tolerates_missing_members_labels():
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body={"id": "b-1"})):
        ctx = c.board_context("l-1")
    assert ctx == {"members": [], "labels": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_trello_client.py -q -k board_context`
Expected: FAIL — `AttributeError: ... has no attribute 'board_context'`

- [ ] **Step 3: Write minimal implementation**

Add to `tasks/trello_client.py` after `list_containers`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_trello_client.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/trello_client.py tests/test_tasks_trello_client.py
git add tasks/trello_client.py tests/test_tasks_trello_client.py
git commit -m "feat(trello): board_context — list->board grounding (members + labels)"
```

---

## Task 4: TrelloClient.create_card

**Files:**
- Modify: `tasks/trello_client.py` (add `create_card`)
- Test: `tests/test_tasks_trello_client.py` (add cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_trello_client.py`:

```python
# ── create_card ─────────────────────────────────────────────────────────


def test_create_card_minimal_payload():
    response = {"id": "c-1", "idShort": 7, "url": "https://trello.com/c/abc/7-x"}
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=response)) as mock_req:
        result = c.create_card(id_list="l-1", name="Починить баг")
    assert result["idShort"] == 7
    sent = mock_req.call_args.kwargs["params"]
    assert sent["idList"] == "l-1"
    assert sent["name"] == "Починить баг"
    # Optional fields absent when not provided.
    assert "idMembers" not in sent
    assert "idLabels" not in sent
    assert "due" not in sent
    assert "desc" not in sent


def test_create_card_full_payload_joins_arrays():
    response = {"id": "c-2", "idShort": 8, "url": "https://trello.com/c/def/8-y"}
    c = TrelloClient("k", "t")
    with patch.object(c._session, "request", return_value=_resp(200, json_body=response)) as mock_req:
        c.create_card(
            id_list="l-1", name="T", desc="body",
            id_members=["m-1", "m-2"], id_labels=["lbl-1"], due="2026-06-01",
        )
    sent = mock_req.call_args.kwargs["params"]
    assert sent["desc"] == "body"
    assert sent["idMembers"] == "m-1,m-2"
    assert sent["idLabels"] == "lbl-1"
    assert sent["due"] == "2026-06-01"
    # POST verb.
    assert mock_req.call_args.args[0] == "POST"
    assert mock_req.call_args.args[1].endswith("/cards")


def test_create_card_rejects_empty_name():
    c = TrelloClient("k", "t")
    with pytest.raises(TrelloError, match="name обязателен"):
        c.create_card(id_list="l-1", name="")


def test_create_card_rejects_empty_list():
    c = TrelloClient("k", "t")
    with pytest.raises(TrelloError, match="id_list обязателен"):
        c.create_card(id_list="", name="T")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_trello_client.py -q -k create_card`
Expected: FAIL — `AttributeError: ... has no attribute 'create_card'`

- [ ] **Step 3: Write minimal implementation**

Add to `tasks/trello_client.py` after `board_context`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_trello_client.py -q`
Expected: PASS (18 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/trello_client.py tests/test_tasks_trello_client.py
git add tasks/trello_client.py tests/test_tasks_trello_client.py
git commit -m "feat(trello): create_card — POST /cards with comma-joined arrays"
```

---

## Task 5: TrelloBackend adapter (priority→desc line, rich create)

**Files:**
- Create: `tasks/backends/trello.py`
- Test: `tests/test_tasks_backends.py` (add a TrelloBackend section)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_backends.py` (after the GlideBackend section). Each test imports `TrelloBackend` inline so it fails cleanly until Task 5's module exists; the top-level imports (`Container`, `CreatedIssue`, `Task`, `Priority`, `MagicMock`) are already present in the file:

```python
# ── TrelloBackend ────────────────────────────────────────────────────


def _trello_card(id_short=7, url="https://trello.com/c/abc/7-x"):
    return {"id": "c-1", "idShort": id_short, "shortLink": "abc", "url": url}


def test_trello_bootstrap_folds_board_and_list_into_name():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.list_containers.return_value = [
        {"board_name": "Маркетинг", "list_id": "l-1", "list_name": "To Do"},
        {"board_name": "Продажи", "list_id": "l-3", "list_name": "Inbox"},
    ]
    b = TrelloBackend(client)
    assert b.bootstrap() == [
        Container(id="l-1", name="Маркетинг / To Do", key=None),
        Container(id="l-3", name="Продажи / Inbox", key=None),
    ]


def test_trello_container_label_is_name():
    from tasks.backends.trello import TrelloBackend
    b = TrelloBackend(MagicMock())
    assert b.container_label(Container(id="l", name="Маркетинг / To Do")) == "Маркетинг / To Do"


def test_trello_context_passes_through_board_context():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    expected = {"members": [{"id": "m-1"}], "labels": [{"id": "lbl-1"}]}
    client.board_context.return_value = expected
    b = TrelloBackend(client)
    assert b.context("l-1") == expected
    client.board_context.assert_called_once_with("l-1")


def test_trello_create_prepends_priority_line_to_desc():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A", description="тело", priority=Priority.URGENT))
    sent = client.create_card.call_args.kwargs
    assert sent["desc"] == "**Приоритет:** Срочный\n\nтело"


def test_trello_create_priority_line_without_body():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A", description="", priority=Priority.HIGH))
    assert client.create_card.call_args.kwargs["desc"] == "**Приоритет:** Высокий"


def test_trello_create_no_priority_line_when_none():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A", description="тело", priority=Priority.NONE))
    assert client.create_card.call_args.kwargs["desc"] == "тело"


def test_trello_create_passes_assignee_labels_due():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    task = Task(
        title="A", assignee_id="m-1",
        label_ids=["lbl-1", "lbl-2"], due_date="2026-06-01",
    )
    b.create("l-1", task)
    sent = client.create_card.call_args.kwargs
    assert sent["id_members"] == ["m-1"]
    assert sent["id_labels"] == ["lbl-1", "lbl-2"]
    assert sent["due"] == "2026-06-01"
    assert sent["id_list"] == "l-1"


def test_trello_create_omits_empty_assignee_labels_due():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card()
    b = TrelloBackend(client)
    b.create("l-1", Task(title="A"))
    sent = client.create_card.call_args.kwargs
    assert sent["id_members"] is None
    assert sent["id_labels"] is None
    assert sent["due"] is None


def test_trello_create_returns_short_id_and_url():
    from tasks.backends.trello import TrelloBackend
    client = MagicMock()
    client.create_card.return_value = _trello_card(id_short=42, url="https://trello.com/c/zz/42-fix")
    b = TrelloBackend(client)
    issue = b.create("l-1", Task(title="A"))
    assert isinstance(issue, CreatedIssue)
    assert issue.identifier == "#42"
    assert issue.url == "https://trello.com/c/zz/42-fix"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_backends.py -q -k trello`
Expected: FAIL — `ModuleNotFoundError: No module named 'tasks.backends.trello'`

- [ ] **Step 3: Write minimal implementation**

Create `tasks/backends/trello.py`:

```python
"""Trello adapter — wraps tasks.trello_client.TrelloClient.

Translates between Phase 6.0 schema (Task / Priority enum) and Trello's
card model. Rich grounding parity with Linear: assignee + labels come from
the board (via context()). Trello has no native priority field, so priority
is rendered as a line in the card description (spec decision D2).

Container = a Trello list (id is the list id; create() uses it as idList).
The "Board / List" display string is folded into Container.name (key=None)
so the dialog's inline dropdown format needs no change (spec decision D4).
"""
from __future__ import annotations

from tasks.backends.base import Container, CreatedIssue
from tasks.schema import Priority, Task
from tasks.trello_client import TrelloClient

# Russian priority labels for the description line. NONE is omitted (no line).
_PRIORITY_LABELS_RU: dict[Priority, str] = {
    Priority.URGENT: "Срочный",
    Priority.HIGH:   "Высокий",
    Priority.MEDIUM: "Средний",
    Priority.LOW:    "Низкий",
}


class TrelloBackend:
    """Adapter: dialog/sender ←→ TrelloClient."""

    name = "trello"
    display_name = "Trello"

    def __init__(self, client: TrelloClient):
        self._client = client

    def bootstrap(self) -> list[Container]:
        rows = self._client.list_containers()
        return [
            Container(
                id=r["list_id"],
                name=f"{r['board_name']} / {r['list_name']}",
                key=None,
            )
            for r in rows
        ]

    def container_label(self, c: Container) -> str:
        # "Board / List" already lives in name (D4) — no key suffix.
        return c.name

    def context(self, container_id: str) -> dict:
        # container_id is the list id; board_context resolves list→board.
        return self._client.board_context(container_id)

    def create(self, container_id: str, task: Task) -> CreatedIssue:
        desc = task.description or ""
        if task.priority is not Priority.NONE:
            label = _PRIORITY_LABELS_RU.get(task.priority)
            if label:
                line = f"**Приоритет:** {label}"
                desc = f"{line}\n\n{desc}" if desc else line

        card = self._client.create_card(
            id_list=container_id,
            name=task.title,
            desc=desc or None,
            id_members=[task.assignee_id] if task.assignee_id else None,
            id_labels=list(task.label_ids) if task.label_ids else None,
            due=task.due_date or None,
        )

        id_short = card.get("idShort")
        if id_short is not None:
            identifier = f"#{id_short}"
        else:
            identifier = card.get("shortLink") or "?"
        return CreatedIssue(identifier=identifier, url=card.get("url") or "")

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_backends.py -q`
Expected: PASS (all existing Linear/Glide tests + 9 new Trello tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/backends/trello.py tests/test_tasks_backends.py
git add tasks/backends/trello.py tests/test_tasks_backends.py
git commit -m "feat(trello): TrelloBackend adapter — rich create + priority-in-desc"
```

---

## Task 6: Register Trello in the backend factory

**Files:**
- Modify: `tasks/backends/__init__.py`
- Test: `tests/test_tasks_backends.py` (add factory tests — none exist today)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_backends.py`:

```python
# ── backend_from_name factory ────────────────────────────────────────


def test_factory_builds_trello_with_both_credentials():
    from unittest.mock import patch
    from tasks.backends import backend_from_name
    from tasks.backends.trello import TrelloBackend
    config = {"trello_api_key": "key-abc", "trello_token": "tok-xyz"}
    with patch("tasks.trello_client.TrelloClient.__init__", return_value=None) as init:
        backend = backend_from_name("trello", config)
    assert isinstance(backend, TrelloBackend)
    # Both credentials passed positionally (api_key, token).
    assert init.call_args.args == ("key-abc", "tok-xyz")


def test_factory_trello_missing_credentials_raises_trello_error():
    from tasks.backends import backend_from_name
    from tasks.trello_client import TrelloError
    with pytest.raises(TrelloError):
        backend_from_name("trello", {})   # empty key + token


def test_factory_unknown_backend_raises_value_error():
    from tasks.backends import backend_from_name
    with pytest.raises(ValueError, match="Unknown backend"):
        backend_from_name("jira", {})
```

Add `import pytest` at the top of `tests/test_tasks_backends.py` if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_backends.py -q -k factory`
Expected: FAIL — `ValueError: Unknown backend: 'trello'`

- [ ] **Step 3: Write minimal implementation**

In `tasks/backends/__init__.py`, add the import (after the LinearBackend import):

```python
from tasks.backends.trello import TrelloBackend
```

Add the branch inside `backend_from_name`, before the `raise ValueError` line:

```python
    if name == "trello":
        from tasks.trello_client import TrelloClient
        client = TrelloClient(
            config.get("trello_api_key", ""),
            config.get("trello_token", ""),
        )
        return TrelloBackend(client)
```

Update `__all__` to include `"TrelloBackend"`:

```python
__all__ = [
    "Container", "CreatedIssue", "TaskBackend",
    "LinearBackend", "GlideBackend", "TrelloBackend",
    "backend_from_name",
]
```

Update the module docstring's "Public entry points" block to mention `TrelloBackend` (change the `LinearBackend, GlideBackend` line to `LinearBackend, GlideBackend, TrelloBackend`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_backends.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/backends/__init__.py tests/test_tasks_backends.py
git add tasks/backends/__init__.py tests/test_tasks_backends.py
git commit -m "feat(trello): register TrelloBackend in backend_from_name factory"
```

---

## Task 7: Teach sender.py to catch TrelloError

**Files:**
- Modify: `tasks/sender.py:35-37` (imports), `tasks/sender.py:79` (except tuple)
- Test: `tests/test_tasks_send.py` (add a Trello-failure case)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_send.py`:

```python
def test_send_marks_failed_on_trello_error_not_unexpected(caplog):
    """A TrelloError must be caught by the narrow handler (logged WARNING),
    not the belt-and-braces Exception handler (logged as 'unexpected error')."""
    from tasks.trello_client import TrelloError
    from tasks.sender import send_tasks_iter
    from tasks.schema import Task, TaskStatus

    backend = MagicMock()
    backend.create.side_effect = TrelloError("Trello вернул 401: invalid token")
    task = Task(title="A")
    statuses = []
    with caplog.at_level("WARNING", logger="tasks.sender"):
        list(send_tasks_iter(
            [task],
            container_id="l-1",
            backend=backend,
            on_status_change=lambda t, s: statuses.append(s),
            cancel_check=lambda: False,
        ))
    assert task.status is TaskStatus.FAILED
    assert task.send_error == "401"   # _short_error_code extracts the code
    # Narrow handler path → "send failed", NOT "unexpected error".
    assert any("send failed" in r.message for r in caplog.records)
    assert not any("unexpected error" in r.message for r in caplog.records)
```

Confirm `tests/test_tasks_send.py` already imports `MagicMock` (it uses mock backends); if not, add `from unittest.mock import MagicMock`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_send.py -q -k trello_error`
Expected: FAIL — the test asserts `"unexpected error"` is absent, but TrelloError currently falls into the generic `except Exception` and logs "unexpected error".

- [ ] **Step 3: Write minimal implementation**

In `tasks/sender.py`, add the import after the GlideError import (line ~35):

```python
from tasks.trello_client import TrelloError
```

Change the narrow `except` tuple (line ~79) from:

```python
        except (LinearError, GlideError) as e:
```

to:

```python
        except (LinearError, GlideError, TrelloError) as e:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_send.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/sender.py tests/test_tasks_send.py
git add tasks/sender.py tests/test_tasks_send.py
git commit -m "feat(trello): catch TrelloError in sender (narrow handler, not generic)"
```

---

## Task 8: Humanize Trello errors

**Files:**
- Modify: `tasks/errors.py:84` (backend_name map), `tasks/errors.py:189-196` (_detect_backend)
- Test: `tests/test_tasks_errors.py` (extend the backend-name case list)

- [ ] **Step 1: Write the failing test**

In `tests/test_tasks_errors.py`, extend the `cases` list in `test_humanize_uses_correct_backend_name_in_text` to add Trello:

```python
    cases = [
        ("Linear вернул 401", "Linear"),
        ("OpenRouter вернул 401", "OpenRouter"),
        ("Glide вернул 401", "Glide"),
        ("Trello вернул 401", "Trello"),
    ]
```

And add a dedicated test below it:

```python
def test_humanize_trello_timeout_names_trello():
    out = humanize(Exception("Таймаут Trello (>30s)"))
    assert "Trello" in out
    assert "вовремя" in out.lower() or "врем" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks_errors.py -q -k "backend_name or trello"`
Expected: FAIL — `"Trello" in out` is false; `_detect_backend` returns None for the Trello message, so `backend_name` falls back to "сервер".

- [ ] **Step 3: Write minimal implementation**

In `tasks/errors.py`, update the `backend_name` map (line ~84):

```python
    backend_name = {"linear": "Linear", "glide": "Glide",
                    "trello": "Trello", "openrouter": "OpenRouter"}.get(backend, "сервер")
```

Add the Trello branch in `_detect_backend` (after the `glide` branch, line ~193):

```python
    if "trello" in lower:
        return "trello"
```

(Do NOT touch `_CORPORATE_HOST_PATTERNS` — `api.trello.com` is a public host.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks_errors.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tasks/errors.py tests/test_tasks_errors.py
git add tasks/errors.py tests/test_tasks_errors.py
git commit -m "feat(trello): humanize Trello errors (detect + display name)"
```

---

## Task 9: Add Trello config keys to config.example.json

**Files:**
- Modify: `config.example.json`
- Test: `tests/test_config_example_trello_keys.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_example_trello_keys.py`:

```python
"""config.example.json must document the Trello backend keys."""
from __future__ import annotations

import json
from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"


def test_config_example_has_trello_keys():
    data = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    assert "trello_api_key" in data
    assert "trello_token" in data
    assert "trello_enabled" in data


def test_trello_enabled_defaults_false_opt_in():
    """Spec D5: Trello is opt-in (unlike linear/glide which default true)."""
    data = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    assert data["trello_enabled"] is False
    assert data["trello_api_key"] == ""
    assert data["trello_token"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_example_trello_keys.py -q`
Expected: FAIL — `KeyError`/`AssertionError`: keys absent.

- [ ] **Step 3: Write minimal implementation**

Open `config.example.json`, find the `"glide_enabled"` line, and add the three Trello keys immediately after it (keep valid JSON — add a trailing comma to the glide line if needed):

```json
  "trello_api_key": "",
  "trello_token": "",
  "trello_enabled": false,
```

(Place them next to the other backend keys for readability. Ensure the file still parses — no trailing comma on the final object key.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_example_trello_keys.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check tests/test_config_example_trello_keys.py
python -c "import json; json.load(open('config.example.json', encoding='utf-8'))"
git add config.example.json tests/test_config_example_trello_keys.py
git commit -m "feat(trello): document trello_api_key/token/enabled in config.example.json"
```

---

## Task 10: App Tk vars + enabled-flag handler

**Files:**
- Modify: `ui/app/builder.py:222-236` (Vars), `ui/app/settings_mixin.py:81-84` (handler)
- Test: `tests/test_app_trello_vars.py` (new, source-text — importing ui.app crashes Linux CI per [[feedback_ui_app_import_breaks_linux_ci]])

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_trello_vars.py`:

```python
"""Source-text checks: App declares the Trello Vars + enabled handler.

Importing ui.app loads sounddevice (PortAudio) which crashes Linux CI, so
we scan the source text instead of instantiating the App.
See [[feedback_ui_app_import_breaks_linux_ci]].
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BUILDER = _ROOT / "ui" / "app" / "builder.py"
_SETTINGS_MIXIN = _ROOT / "ui" / "app" / "settings_mixin.py"


def test_builder_declares_trello_vars():
    src = _BUILDER.read_text(encoding="utf-8")
    assert "_trello_key_var" in src
    assert "_trello_token_var" in src
    assert "_trello_enabled_var" in src


def test_trello_enabled_var_defaults_false():
    """Opt-in (D5): the BooleanVar default reads trello_enabled with False fallback."""
    src = _BUILDER.read_text(encoding="utf-8")
    assert 'app._config.get("trello_enabled", False)' in src


def test_settings_mixin_has_trello_enabled_handler():
    src = _SETTINGS_MIXIN.read_text(encoding="utf-8")
    assert "_on_trello_enabled_changed" in src
    assert '"trello_enabled"' in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_trello_vars.py -q`
Expected: FAIL — strings absent.

- [ ] **Step 3: Write minimal implementation**

In `ui/app/builder.py`, after the `app._glide_key_var = ...` block (line ~224), add:

```python
    # Trello API key + token (two-credential auth). Opt-in by default
    # (spec D5) — unlike Linear/Glide which default enabled. The card-
    # destination list picker lives in ExtractTasksDialog.
    app._trello_key_var = ctk.StringVar(
        value=app._config.get("trello_api_key", ""),
    )
    app._trello_token_var = ctk.StringVar(
        value=app._config.get("trello_token", ""),
    )
```

In the same file, after `app._glide_enabled_var = ...` (line ~236), add:

```python
    app._trello_enabled_var = ctk.BooleanVar(
        value=bool(app._config.get("trello_enabled", False)),
    )
```

In `ui/app/settings_mixin.py`, after `_on_glide_enabled_changed` (line ~84), add:

```python
    def _on_trello_enabled_changed(self) -> None:
        """Persist the Trello-backend enabled flag (opt-in, spec D5)."""
        self._config["trello_enabled"] = bool(self._trello_enabled_var.get())
        save_config(self._config)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app_trello_vars.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check ui/app/builder.py ui/app/settings_mixin.py tests/test_app_trello_vars.py
git add ui/app/builder.py ui/app/settings_mixin.py tests/test_app_trello_vars.py
git commit -m "feat(trello): App vars (key/token/enabled) + enabled-flag handler"
```

---

## Task 11: Settings — Trello section (two credential fields)

**Files:**
- Modify: `ui/dialogs/settings.py` (add `_build_trello_section`; call it at line ~168)
- Test: `tests/test_settings_dialog_uses_api_key_row.py` (bump count), `tests/test_settings_trello_section.py` (new, source-text)

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings_trello_section.py`:

```python
"""Source-text checks for the Settings Trello section (two-field auth).

Cannot import ui.dialogs.settings (sounddevice on Linux CI). Scan source.
"""
from __future__ import annotations

from pathlib import Path

_SETTINGS = Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"


def test_trello_section_defined_and_called():
    src = _SETTINGS.read_text(encoding="utf-8")
    assert "def _build_trello_section" in src
    assert "self._build_trello_section(scroll_integrations)" in src


def test_trello_section_binds_both_credential_vars():
    src = _SETTINGS.read_text(encoding="utf-8")
    assert "_trello_key_var" in src
    assert "_trello_token_var" in src
    assert "_trello_enabled_var" in src


def test_trello_section_validates_via_trello_client():
    src = _SETTINGS.read_text(encoding="utf-8")
    assert "from tasks.trello_client import TrelloClient" in src
```

Also update `tests/test_settings_dialog_uses_api_key_row.py`: change the assertion threshold from 4 to 6 and update the docstring/message (Cloud STT + OpenRouter + Linear + Glide + Trello-key + Trello-token = 6 call sites):

```python
def test_settings_calls_api_key_row_at_least_six_times():
    """Cloud STT + OpenRouter + Linear + Glide + Trello (key + token) = 6
    api_key_row(...) call sites."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    n_calls = source.count("api_key_row(")
    assert n_calls >= 6, (
        f"Expected ≥ 6 api_key_row(...) calls (incl. Trello key + token), "
        f"got {n_calls}"
    )
```

(Rename the old `test_settings_calls_api_key_row_at_least_four_times` to the above; keep `test_settings_imports_api_key_row` unchanged.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_trello_section.py tests/test_settings_dialog_uses_api_key_row.py -q`
Expected: FAIL — `_build_trello_section` absent; api_key_row count is 4 (< 6).

- [ ] **Step 3: Write minimal implementation**

In `ui/dialogs/settings.py`, add the call after `self._build_glide_section(scroll_integrations)` (line ~167):

```python
        self._build_trello_section(scroll_integrations)
```

Add the method after `_build_glide_section` (after line ~729). Two `api_key_row` calls in two transparent sub-frames so layout never collides (the helper grids into its parent starting at the given row; each sub-frame is an independent grid). The token row owns Validate + status; its `on_validate` reads BOTH credential vars; `on_key_persisted` saves both — because `api_key_row` only persists via the Validate path (the key row has no Validate button):

```python
    def _build_trello_section(self, parent) -> None:
        """Trello API key + token + connection status (spec 2026-05-29).

        Trello needs two secrets (key + token). The shared api_key_row
        helper renders one masked field, so we compose two calls:
        - key row: enable-checkbox + masked key field (no Validate)
        - token row: masked token field + Validate + status badge

        api_key_row only persists on Validate success, and only the token
        row has a Validate button — so the token row's _persist saves BOTH
        credentials, and its _on_validate reads BOTH vars.
        """
        section = self._section_card(parent, "Trello", row=3)

        key_frame = ctk.CTkFrame(section, fg_color="transparent")
        key_frame.grid(row=0, column=0, sticky="ew")
        key_frame.grid_columnconfigure(1, weight=1)

        token_frame = ctk.CTkFrame(section, fg_color="transparent")
        token_frame.grid(row=1, column=0, sticky="ew")
        token_frame.grid_columnconfigure(1, weight=1)

        def _persist(_token: str, _info: dict) -> None:
            self._parent._config["trello_api_key"] = self._parent._trello_key_var.get().strip()
            self._parent._config["trello_token"] = self._parent._trello_token_var.get().strip()
            save_config(self._parent._config)

        def _on_validate(token: str) -> dict:
            from tasks.trello_client import TrelloClient
            api_key = self._parent._trello_key_var.get().strip()
            client = TrelloClient(api_key, token)
            try:
                return client.validate_key()
            finally:
                client.close()

        def _format_success(info: dict) -> str:
            return f"✓ Подключено: {info.get('name', '(unknown)')}"

        # Key row — owns the enable-checkbox; no Validate button.
        api_key_row(
            key_frame,
            label_text="API ключ",
            key_var=self._parent._trello_key_var,
            placeholder="(ключ Trello — trello.com/app-key)",
            enabled_var=self._parent._trello_enabled_var,
            enabled_label="Использовать Trello",
            on_enabled_changed=self._parent._on_trello_enabled_changed,
            row=0,
        )

        # Token row — owns Validate + status; persists both credentials.
        refs = api_key_row(
            token_frame,
            label_text="Токен",
            key_var=self._parent._trello_token_var,
            placeholder="(токен Trello)",
            on_validate=_on_validate,
            on_key_persisted=_persist,
            format_success=_format_success,
            row=0,
        )
        self._trello_status = refs["status"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_settings_trello_section.py tests/test_settings_dialog_uses_api_key_row.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check ui/dialogs/settings.py tests/test_settings_trello_section.py tests/test_settings_dialog_uses_api_key_row.py
git add ui/dialogs/settings.py tests/test_settings_trello_section.py tests/test_settings_dialog_uses_api_key_row.py
git commit -m "feat(trello): Settings section with key + token fields + validate"
```

---

## Task 12: Backend-metadata dictionaries in the Extract dialog constants

**Files:**
- Modify: `ui/dialogs/extract_tasks/constants.py`
- Test: `tests/test_extract_dialog_backend_dicts.py` (new — `constants.py` is import-safe; it only imports `datetime`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract_dialog_backend_dicts.py`:

```python
"""Unit tests for the de-hardcoded backend-metadata dicts.

ui.dialogs.extract_tasks.constants imports only datetime (no sounddevice),
so it is safe to import directly on Linux CI — unlike the dialog package's
__init__.py which pulls in CTk widgets.
"""
from __future__ import annotations

from ui.dialogs.extract_tasks import constants as C


def test_name_to_display_covers_three_backends():
    assert C._NAME_TO_DISPLAY == {
        "linear": "Linear", "glide": "Glide", "trello": "Trello",
    }


def test_display_to_name_is_exact_inverse():
    assert C._DISPLAY_TO_NAME == {v: k for k, v in C._NAME_TO_DISPLAY.items()}


def test_cache_key_per_backend_distinct():
    keys = C._CACHE_KEY_BY_BACKEND
    assert keys["linear"] == C._TEAMS_CACHE_KEY
    assert keys["glide"] == C._BOARDS_CACHE_KEY
    assert keys["trello"] == C._TRELLO_CACHE_KEY
    # All three distinct so cached containers never collide.
    assert len(set(keys.values())) == 3


def test_container_label_header_includes_trello():
    assert C._CONTAINER_LABEL_BY_BACKEND["trello"] == "Список"


def test_empty_label_and_accusative_cover_trello():
    assert C._EMPTY_CONTAINER_LABEL_BY_BACKEND["trello"] == "(нет списков)"
    assert C._CONTAINER_ACCUSATIVE_BY_BACKEND["trello"] == "список"


def test_required_keys_trello_needs_both_credentials():
    assert C._REQUIRED_KEYS_BY_BACKEND["trello"] == ("trello_api_key", "trello_token")
    assert C._REQUIRED_KEYS_BY_BACKEND["linear"] == ("linear_api_key",)
    assert C._REQUIRED_KEYS_BY_BACKEND["glide"] == ("glide_api_key",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_dialog_backend_dicts.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_NAME_TO_DISPLAY'`

- [ ] **Step 3: Write minimal implementation**

In `ui/dialogs/extract_tasks/constants.py`, change the `_CONTAINER_LABEL_BY_BACKEND` line (line ~31) from:

```python
_CONTAINER_LABEL_BY_BACKEND = {"linear": "Команда", "glide": "Доска"}
```

to:

```python
_CONTAINER_LABEL_BY_BACKEND = {"linear": "Команда", "glide": "Доска", "trello": "Список"}
```

And add, right after the `_TEAMS_CACHE_TTL`/`_RECENT_MODELS_*` constants block (after line ~27), the Trello cache key + the de-hardcoding dictionaries:

```python
_TRELLO_CACHE_KEY = "trello_lists_cache"   # Phase: Trello lists (board/list pairs)

# Backend display ↔ internal name (replaces hardcoded "Linear"/"Glide"
# ternaries in the dialog). Add a backend here and the dropdown, the
# display→name reverse lookup, and the per-backend cache key all follow.
_NAME_TO_DISPLAY = {"linear": "Linear", "glide": "Glide", "trello": "Trello"}
_DISPLAY_TO_NAME = {v: k for k, v in _NAME_TO_DISPLAY.items()}

# Per-backend container cache key — distinct so Linear teams, Glide boards,
# and Trello lists never collide in config storage.
_CACHE_KEY_BY_BACKEND = {
    "linear": _TEAMS_CACHE_KEY,
    "glide": _BOARDS_CACHE_KEY,
    "trello": _TRELLO_CACHE_KEY,
}

# Dropdown "(empty)" placeholder + the accusative noun for "Выберите …".
_EMPTY_CONTAINER_LABEL_BY_BACKEND = {
    "linear": "(нет команд)", "glide": "(нет досок)", "trello": "(нет списков)",
}
_CONTAINER_ACCUSATIVE_BY_BACKEND = {
    "linear": "команду", "glide": "доску", "trello": "список",
}

# Credentials each backend needs to be considered "configured". Trello
# needs two (key + token); the others need one.
_REQUIRED_KEYS_BY_BACKEND = {
    "linear": ("linear_api_key",),
    "glide": ("glide_api_key",),
    "trello": ("trello_api_key", "trello_token"),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extract_dialog_backend_dicts.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check ui/dialogs/extract_tasks/constants.py tests/test_extract_dialog_backend_dicts.py
git add ui/dialogs/extract_tasks/constants.py tests/test_extract_dialog_backend_dicts.py
git commit -m "feat(trello): backend-metadata dicts in extract dialog constants"
```

---

## Task 13: De-hardcode the Extract dialog's binary backend forks

**Files:**
- Modify: `ui/dialogs/extract_tasks/__init__.py` (imports, `_compute_enabled_backends`, `_current_backend_name`, `_backend_cache_key`, `_fetch_containers_in_worker`, `_populate_container_dropdown`, `_on_extract`, status text, the send-path key check; add `_backend_is_configured`)
- Test: `tests/test_extract_dialog_backend_dicts.py` (append source-text checks)

This task makes the dialog support any registered backend. Apply all edits, then run the source-text tests. All replacements use the dicts from Task 12.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_extract_dialog_backend_dicts.py`:

```python
from pathlib import Path

_DIALOG = (
    Path(__file__).resolve().parent.parent
    / "ui" / "dialogs" / "extract_tasks" / "__init__.py"
)


def test_dialog_has_no_binary_backend_hardcodes():
    src = _DIALOG.read_text(encoding="utf-8")
    assert '"Linear" if n == "linear" else "Glide"' not in src
    assert 'if display == "Glide"' not in src
    assert '"linear_api_key" if backend_name == "linear" else "glide_api_key"' not in src
    assert '"доску" if backend_name == "glide" else "команду"' not in src


def test_dialog_uses_backend_dicts_and_helper():
    src = _DIALOG.read_text(encoding="utf-8")
    assert "_NAME_TO_DISPLAY" in src
    assert "_DISPLAY_TO_NAME" in src
    assert "_CACHE_KEY_BY_BACKEND" in src
    assert "_backend_is_configured" in src


def test_dialog_enables_trello_backend():
    src = _DIALOG.read_text(encoding="utf-8")
    assert 'self._config.get("trello_enabled"' in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_dialog_backend_dicts.py -q -k "binary or dicts or trello_backend"`
Expected: FAIL — the hardcodes are still present.

- [ ] **Step 3: Apply the edits**

**(a) Imports** — extend the `from .constants import (...)` block (line ~47) to add these names (alongside the existing `_CONTAINER_LABEL_BY_BACKEND`, `_TEAMS_CACHE_KEY`, `_BOARDS_CACHE_KEY`):

```python
    _CACHE_KEY_BY_BACKEND,
    _CONTAINER_ACCUSATIVE_BY_BACKEND,
    _DISPLAY_TO_NAME,
    _EMPTY_CONTAINER_LABEL_BY_BACKEND,
    _NAME_TO_DISPLAY,
    _REQUIRED_KEYS_BY_BACKEND,
    _TRELLO_CACHE_KEY,
```

**(b) Module helper** — add this module-level function (place near the top of the file after the imports, before the class definition):

```python
def _backend_is_configured(name: str, config: dict) -> bool:
    """True if every credential the backend needs is present + non-empty.

    Trello needs two (key + token); Linear/Glide need one. Replaces the old
    `"linear_api_key" if linear else "glide_api_key"` binary that silently
    picked the Glide key for any non-Linear backend.
    """
    keys = _REQUIRED_KEYS_BY_BACKEND.get(name, ())
    return bool(keys) and all((config.get(k) or "").strip() for k in keys)
```

**(c) `_compute_enabled_backends`** — add the Trello branch. Replace:

```python
        if bool(self._config.get("glide_enabled", True)):
            enabled.append("glide")
        return enabled or ["linear"]
```

with:

```python
        if bool(self._config.get("glide_enabled", True)):
            enabled.append("glide")
        if bool(self._config.get("trello_enabled", False)):
            enabled.append("trello")
        return enabled or ["linear"]
```

**(d) `backend_display`** (line ~219). Replace:

```python
        backend_display = [
            "Linear" if n == "linear" else "Glide"
            for n in self._enabled_backends
        ]
```

with:

```python
        backend_display = [_NAME_TO_DISPLAY[n] for n in self._enabled_backends]
```

**(e) `_current_backend_name`** (line ~392). Replace the body (keep/extend the docstring; change its "Returns "linear" or "glide"." line to "Returns "linear" / "glide" / "trello".") so the if/elif becomes a dict lookup:

```python
        var = getattr(self, "_backend_var", None)
        display = var.get() if var is not None else None
        name = _DISPLAY_TO_NAME.get(display)
        if name:
            return name
        # Pre-build / unknown — first enabled.
        return self._enabled_backends[0] if self._enabled_backends else "linear"
```

**(f) `_backend_cache_key`** (line ~406). Replace:

```python
        return _BOARDS_CACHE_KEY if self._current_backend_name() == "glide" else _TEAMS_CACHE_KEY
```

with:

```python
        return _CACHE_KEY_BY_BACKEND.get(self._current_backend_name(), _TEAMS_CACHE_KEY)
```

(If `_BOARDS_CACHE_KEY` / `_TEAMS_CACHE_KEY` become unused after this, leave their imports — they're still referenced by `_CACHE_KEY_BY_BACKEND` in constants, not here; ruff checks this file only for local unused names. If ruff flags them unused in this file, drop them from this file's import block.)

**(g) `_fetch_containers_in_worker`** (line ~453). Replace:

```python
        backend_name = self._current_backend_name()
        api_key_field = "linear_api_key" if backend_name == "linear" else "glide_api_key"
        api_key = (self._config.get(api_key_field) or "").strip()
        if not api_key:
            self._team_var.set(f"(нет ключа {backend_name.title()})")
            return
```

with:

```python
        backend_name = self._current_backend_name()
        if not _backend_is_configured(backend_name, self._config):
            self._team_var.set(
                f"(нет ключа {_NAME_TO_DISPLAY.get(backend_name, backend_name)})",
            )
            return
```

**(h) `_populate_container_dropdown`** (line ~505). Replace:

```python
            empty_label = (
                "(нет досок)"
                if self._current_backend_name() == "glide"
                else "(нет команд)"
            )
```

with:

```python
            empty_label = _EMPTY_CONTAINER_LABEL_BY_BACKEND.get(
                self._current_backend_name(), "(нет команд)",
            )
```

**(i) Warning word — both occurrences** (lines ~527 and ~1017 are textually identical). Use a replace-all on this exact line:

old (replace_all):

```python
            label_word = "доску" if backend_name == "glide" else "команду"
```

new:

```python
            label_word = _CONTAINER_ACCUSATIVE_BY_BACKEND.get(backend_name, "команду")
```

**(j) Status text** (line ~543) — collapse the `if backend_name == "linear": … else: …` block (which hardcodes "Glide" in the else, wrong for Trello) into one generic configure. Replace the whole if/else status block:

```python
        if backend_name == "linear":
            self._status_label.configure(
                text="Запрос к Linear (team_context)...", text_color=TEXT_SECONDARY,
            )
        else:
            self._status_label.configure(
                text="Запрос к Glide...", text_color=TEXT_SECONDARY,
            )
```

with:

```python
        self._status_label.configure(
            text=f"Запрос к {_NAME_TO_DISPLAY.get(backend_name, backend_name)}...",
            text_color=TEXT_SECONDARY,
        )
```

**(k) Send-path key check** (line ~1521). Replace:

```python
        api_key_field = "linear_api_key" if backend_name == "linear" else "glide_api_key"
        api_key = (self._config.get(api_key_field) or "").strip()
        if not api_key:
            messagebox.showwarning(
                f"Нет ключа {backend_name.title()}",
                f"Добавьте {backend_name.title()} API ключ в Settings и повторите.",
            )
            return
```

with:

```python
        if not _backend_is_configured(backend_name, self._config):
            display = _NAME_TO_DISPLAY.get(backend_name, backend_name)
            messagebox.showwarning(
                f"Нет ключа {display}",
                f"Добавьте ключ {display} в Settings и повторите.",
            )
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_extract_dialog_backend_dicts.py -q`
Expected: PASS (all dict tests + 3 source-text tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check ui/dialogs/extract_tasks/__init__.py tests/test_extract_dialog_backend_dicts.py
git add ui/dialogs/extract_tasks/__init__.py tests/test_extract_dialog_backend_dicts.py
git commit -m "feat(trello): de-hardcode dialog backend forks; wire Trello dropdown"
```

---

## Task 14: Full regression + manual smoke (required before merge)

**Files:** none changed — verification only.

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS — baseline 333 + the new Trello tests (≈ 45 added across Tasks 1-13). No failures, no errors.

- [ ] **Step 2: Run the linter over the whole repo**

Run: `python -m ruff check .`
Expected: clean (no diagnostics).

- [ ] **Step 3: Manual smoke against a real Trello board**

Mocked tests verify string composition, not the live API contract — in particular whether `GET /lists/{id}/board` accepts the nested `members`/`labels` params (Task 3). This step is **required** before merge (lesson: [[mock_tests_dont_catch_ffmpeg_parse_errors]] — mocks miss real-API drift).

Prep: generate a Trello API key + token at `https://trello.com/app-key`, on a throwaway board with ≥2 lists, ≥2 members, ≥2 named labels.

Run the app from the main repo (not a worktree — gitignored `config.json`/`history/` don't follow worktrees, per [[feedback_run_app_from_main_not_worktree]]):

Run: `python app.py`

Walk the flow:
1. Settings → Trello: paste key + token → **Проверить** → expect `✓ Подключено: <ваше имя>`. Tick **Использовать Trello**. Close Settings.
2. Open a meeting → **Извлечь задачи**. Backend dropdown shows **Trello**; pick it. Container dropdown shows **"Доска / Список"** entries; pick a list.
3. Run extraction → confirm the editor shows an assignee + labels that are **real board members/labels** (grounding worked; no hallucinated names).
4. Select tasks → **Отправить**. Open the board: cards landed in the **chosen list**, each with title, description (with the `**Приоритет:** …` line when priority ≠ none), assignee, labels, and due date.
5. Error path: in Settings, corrupt the token → **Проверить** → expect a friendly Russian message (`Trello вернул 401: …` humanized to «Неверный API-ключ Trello…»), not a raw traceback.

If step 3/4 fails because the nested-members call (Task 3) returned no members/labels, switch `board_context` to the 2-call fallback documented in its docstring (`GET /lists/{id}?fields=idBoard` then `GET /boards/{idBoard}?members=all&...`) and re-smoke.

- [ ] **Step 4: Open the PR**

Once smoke passes, push the branch and open a PR titled `feat(trello): rich Trello task backend` with a Summary + Test plan checklist (mirror `.github/PULL_REQUEST_TEMPLATE.md`). Note in the PR body that the manual smoke was completed against a real board (which board/date), since CI cannot exercise the live Trello API.

---

## Notes for the executor

- **Commit cadence:** one commit per task (15 commits total incl. the plan). Each task is independently green — never commit with a red suite.
- **Russian vs English:** card-description content and all UI/error strings are Russian; code, comments, commit messages stay English (project convention).
- **Don't touch `_CORPORATE_HOST_PATTERNS`** in `errors.py` — `api.trello.com` is public, not VPN-gated.
- **Branch:** the spec lives on `docs/trello-backend-spec`; this plan is committed there too. Implementation continues on this branch (rename to `feat/trello-backend` if preferred before opening the PR). Do not stack a second branch on top before this merges ([[feedback_stacked_pr_squash_merge]]).
- **If line numbers have drifted** (earlier tasks edit the same file), match on the quoted old-code snippet, not the line number.
