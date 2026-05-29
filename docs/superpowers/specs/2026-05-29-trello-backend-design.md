# Trello task backend — design

- **Date:** 2026-05-29
- **Status:** Approved (brainstorming) — pending implementation plan
- **Scope:** Add Trello as a third task backend alongside Linear and Glide,
  with **rich LLM grounding** (board members + labels) at full parity with
  the Linear backend.

## Summary

Extracted meeting tasks can currently be sent to **Linear** (rich grounding:
the LLM auto-assigns assignee + labels from team context) or **Glide**
(minimal: no grounding, manual fields). This adds **Trello** as a rich
backend: the LLM is grounded on the target board's members and labels so it
can auto-fill assignee and labels, exactly like Linear.

The work plugs into the existing `tasks/backends/` Protocol + Adapter layer.
The orchestration core (`tasks/sender.py`) and the dialog's run loop stay
backend-agnostic; we add a client, an adapter, registration, and UI wiring.

## Goals

- Trello cards created from extracted tasks, into a user-chosen Trello list.
- LLM grounding on board members + labels (assignee + label auto-assignment).
- Settings UI to enter + validate Trello credentials and toggle the backend.
- Trello appears in the Extract dialog's backend dropdown when enabled.
- Full parity with the Linear backend's behavior where Trello's data model
  allows it.

## Non-goals

- No editing/closing/syncing of existing Trello cards (create-only, like the
  other backends).
- No Trello checklists, attachments, custom fields, or Power-Ups.
- No native priority field (Trello has none — see decision D2).
- No board/list creation from the app (user picks from existing lists).

## Key design decisions

| # | Decision | Rationale |
|---|----------|-----------|
| **D1** | **Rich grounding** (members + labels), parity with Linear | User choice. The LLM grounding machinery already exists; Trello boards expose members + labels cleanly. |
| **D2** | **Priority → line in card `desc`** (not a label, not a title prefix) | User choice. Trello has no native priority field. A description line is faithful, requires no board mutation, and never collides with the LLM-grounded labels (which also live on labels). |
| **D3** | **Container = Trello list** (card destination); grounding resolves list→board | User choice. Trello requires `idList` to create a card, so a list is the unavoidable primitive; explicit list choice avoids hidden "which list?" assumptions and matches real multi-column board usage. |
| **D4** | Fold "Board / List" into `Container.name`, `key=None` | The dialog dropdown uses an inline label format (`f"{c.name} ({c.key})"` if key else `c.name`), **not** the adapter's `container_label()`. Putting the composite string in `name` renders correctly with zero change to that shared code path. |
| **D5** | `trello_enabled` defaults to **false** (opt-in) | Unlike Linear/Glide (default true). New backend; avoids cluttering the dropdown — and the resulting "(нет ключа)" failures — for users who never configure Trello. |

## Background: the backends layer

`tasks/backends/base.py` defines the `TaskBackend` Protocol that all backends
satisfy:

- `name: str` — stable config id (`"linear"`, `"glide"`, `"trello"`)
- `display_name: str` — dropdown label
- `bootstrap() -> list[Container]` — available destinations
- `container_label(c) -> str` — human label for one container
- `context(container_id) -> dict` — `{"members": [...], "labels": [...]}` for
  LLM grounding (empty lists = no grounding)
- `create(container_id, task) -> CreatedIssue`
- `close()`

Value types: `Container(id, name, key=None)` and
`CreatedIssue(identifier, url)`.

`tasks/backends/__init__.py::backend_from_name(name, config)` is the factory.
`tasks/sender.py::send_tasks_iter(...)` is the backend-agnostic orchestrator.

The grounding shape that `tasks/extractor.py::build_prompt` consumes:

- `members`: list of `{"id": ..., "name"/"displayName": ...}`
- `labels`: list of `{"id": ..., "name": ...}`

`parse_and_validate` drops any assignee/label id the LLM hallucinates that is
not present in the provided context.

## Architecture & components

### New files

#### `tasks/trello_client.py`

`TrelloClient` + `TrelloError(Exception)`. Mirrors `tasks/glide_client.py`
structure (a `requests.Session` + a private `_request(...)` wrapper that maps
transport/HTTP failures to `TrelloError` with Russian messages).

**Auth:** Trello uses an API **key** + a user **token**, both passed as
**query parameters** (`?key=<key>&token=<token>`) on every request — not a
header. `__init__(api_key, token)` validates both are non-empty; `_request`
merges them into `params`.

Public operations:

- `validate_key() -> dict` — `GET /1/members/me?fields=fullName,username`;
  returns `{"name": fullName or username}` for the success badge.
- `list_containers() -> list[dict]` — `GET /1/members/me/boards`
  `?fields=name&filter=open&lists=open&list_fields=name`. One call with
  nested lists; returns flattened `{board_name, list_id, list_name}` rows.
- `board_context(list_id) -> dict` — resolves list→board and returns members
  + labels (see Data flow §context).
- `create_card(*, id_list, name, desc, id_members, id_labels, due) -> dict` —
  `POST /1/cards`. No idempotency-key param — Trello has no such header
  (see Error handling / duplicate-cards risk).
- `close()`.

#### `tasks/backends/trello.py`

`TrelloBackend` adapter (mirrors `tasks/backends/linear.py`). `name="trello"`,
`display_name="Trello"`. Wraps a `TrelloClient`.

- `bootstrap()` → `[Container(id=list_id, name=f"{board_name} / {list_name}",
  key=None)]` (D4).
- `container_label(c)` → `c.name` (Protocol conformance; not used by the
  dialog dropdown but exercised by tests).
- `context(list_id)` → delegates to `client.board_context(list_id)`.
- `create(list_id, task)` → maps `Task` → `create_card` kwargs (see field
  mapping), returns `CreatedIssue(identifier=f"#{idShort}", url=card_url)`.
- `close()` → `client.close()`.

`_PRIORITY_LABELS_RU: dict[Priority, str]` for the description line
(URGENT→«Срочный», HIGH→«Высокий», MEDIUM→«Средний», LOW→«Низкий»; NONE
omitted). The description text is user-facing domain content (like the Russian
fields in `description.md`), so it is Russian.

### Touched files

| File | Change |
|------|--------|
| `tasks/backends/__init__.py` | `trello` branch in `backend_from_name` — reads **both** `trello_api_key` and `trello_token`; update `__all__` + module docstring. |
| `tasks/sender.py` | Import `TrelloError`; add to the `except (LinearError, GlideError, TrelloError)` tuple — otherwise Trello failures fall through to the noisy generic `except Exception` ("unexpected error"). |
| `tasks/errors.py` | `_detect_backend(msg)` += `"trello"` substring; `backend_name` map += `"trello": "Trello"`. Do **not** add `api.trello.com` to `_CORPORATE_HOST_PATTERNS` (it is a public host). Status-code humanization is generic and works as-is. |
| `ui/dialogs/settings.py` | New `_build_trello_section` (row=3) — two `api_key_row` calls (key + token); call it from the section assembler. |
| `ui/dialogs/extract_tasks/constants.py` | Add `_TRELLO_CACHE_KEY`; extend `_CONTAINER_LABEL_BY_BACKEND`; add `_NAME_TO_DISPLAY`/`_DISPLAY_TO_NAME`, `_CACHE_KEY_BY_BACKEND`, `_EMPTY_CONTAINER_LABEL_BY_BACKEND`, `_CONTAINER_ACCUSATIVE_BY_BACKEND`. |
| `ui/dialogs/extract_tasks/__init__.py` | Replace 9 hardcoded binary backend forks with dict lookups + a dual-key configured check (see UI §Extract dialog). |
| `config.example.json` | Add `trello_api_key: ""`, `trello_token: ""`, `trello_enabled: false`. |
| `tests/` | New `test_trello_client.py`, `test_trello_backend.py`; extend `test_errors.py`, `test_backends_factory.py`. |

## Trello REST integration

Base URL `https://api.trello.com/1`. All requests carry `?key=&token=`.

| Operation | Endpoint |
|-----------|----------|
| validate | `GET /members/me?fields=fullName,username` |
| bootstrap | `GET /members/me/boards?fields=name&filter=open&lists=open&list_fields=name` |
| context | `GET /lists/{list_id}/board?fields=id&members=all&member_fields=fullName,username&labels=all&label_fields=name,color` |
| create | `POST /cards` (body: `idList`, `name`, `desc`, `idMembers`, `idLabels`, `due`) |

## Data flow

### bootstrap()

One nested call returns each open board with its open lists. Flatten to one
`Container` per list, `name = "Board / List"`. Cached 24h under
`_TRELLO_CACHE_KEY` (separate key so Trello lists never collide with cached
Linear teams or Glide boards).

### context(list_id) — grounding

`context` receives only the list id (the Protocol passes a container id
string, not the `Container`). Trello members + labels are **board**-level, so
we resolve list→board. Preferred: a single `GET /lists/{id}/board` with nested
`members=all&labels=all`. Fallback (if the API rejects nesting on that path):
`GET /lists/{id}?fields=idBoard` then `GET /boards/{idBoard}?members=all&...`
(2 calls). Not cached — board membership changes often enough that staleness
costs more than the cheap call.

Mapping to the grounding shape:

- `members`: `[{"id": m["id"], "name": m["fullName"], "displayName": m["fullName"]}]`
  (fall back to `username` if `fullName` empty).
- `labels`: `[{"id": lbl["id"], "name": lbl["name"]}]` — **labels with an
  empty `name` are dropped** (Trello allows color-only labels; the LLM cannot
  address an unnamed label, and an empty name pollutes the prompt).

### create(list_id, task) — field mapping

| `Task` field | Trello card param | Notes |
|--------------|-------------------|-------|
| `title` | `name` | |
| `description` (+ priority line) | `desc` | prepend `**Приоритет:** <ru>\n\n` when `priority != NONE` (D2) |
| `priority` | — | only in `desc`; no native field |
| `assignee_id` | `idMembers=[id]` | board member id from grounding; omitted if empty |
| `label_ids` | `idLabels=[...]` | board label ids from grounding; omitted if empty |
| `due_date` | `due` | ISO `YYYY-MM-DD`; omitted if empty |

Response → `CreatedIssue(identifier=f"#{card['idShort']}", url=card['url'])`.
`idShort` is the per-board human card number (analogous to Linear's
"ENG-1234"); Trello returns the full `url` on create.

Idempotency: Trello has no idempotency-key header. The retry path may create
duplicates; acceptable for v1 (documented). A future guard could pre-search by
card name, deferred.

## Error handling

`TrelloError` messages mirror the Glide phrasing so
`sender._short_error_code` classifies them with no extra code (it matches
`\b(4\d\d|5\d\d)\b`, plus "соединен"→network and "таймаут"→timeout):

| Condition | Message |
|-----------|---------|
| `requests.ConnectionError` | «Нет соединения с Trello: …» |
| `requests.Timeout` | «Таймаут Trello (>30s)» |
| 401 / 403 | «Trello вернул 401: …» (invalid key or token) |
| 404 / 429 / 5xx | «Trello вернул {code}: …» |

`sender.py` adds `TrelloError` to its import + `except` tuple. `errors.humanize`
gains Trello detection + display name; its status-code humanization is generic.

## UI wiring

### Settings — `_build_trello_section` (row=3)

The shared `api_key_row` helper renders a single masked field (+ optional
enable-checkbox, validate button, status badge). Trello needs two secrets, so
we **compose two calls** (no change to the shared helper):

- **Row 0 — API key:** `key_var=_trello_key_var`, with the enable-checkbox
  («Использовать Trello», `_trello_enabled_var`, `_on_trello_enabled_changed`),
  persists `trello_api_key`. No validate button here.
- **Row 1 — Token:** `key_var=_trello_token_var`, with the validate button +
  status badge. The `on_validate` closure reads **both** vars and calls
  `TrelloClient(key, token).validate_key()`; persists `trello_token`.

Both fields masked with the eye-toggle (helper default). New App vars:
`_trello_key_var`, `_trello_token_var`, `_trello_enabled_var`; handler
`_on_trello_enabled_changed` (mirrors `_on_glide_enabled_changed`). A small
hint linking to `https://trello.com/app-key` (where users get key + token) is
desirable polish.

### Extract dialog — de-hardcoding

`_compute_enabled_backends` gains a `trello_enabled` branch (default false;
order linear → glide → trello). Nine hardcoded binary backend forks are
replaced with `constants.py` dict lookups + one helper:

| Line | Current (binary) | Fix |
|------|------------------|-----|
| 220 | `"Linear" if n=="linear" else "Glide"` | `_NAME_TO_DISPLAY[n]` |
| 399–402 | `if display=="Glide"/"Linear"` | `_DISPLAY_TO_NAME.get(display, fallback)` |
| 407 | `_BOARDS_CACHE_KEY if glide else _TEAMS_CACHE_KEY` | `_CACHE_KEY_BY_BACKEND[name]` |
| 454, 1521 | `api_key_field = "linear_api_key" if linear else "glide_api_key"` | `_backend_is_configured(name, config)` helper — Trello checks **both** `trello_api_key` and `trello_token` |
| 506–508 | `"(нет досок)" if glide else "(нет команд)"` | `_EMPTY_CONTAINER_LABEL_BY_BACKEND[name]` (Trello: «(нет списков)») |
| 527, 1017 | `"доску" if glide else "команду"` | `_CONTAINER_ACCUSATIVE_BY_BACKEND[name]` (Trello: «список») |
| 543 | `"Запрос к Linear (team_context)..." else "Запрос к Glide..."` | `f"Запрос к {_NAME_TO_DISPLAY[name]}..."` |

`backend_name.title()` already yields "Trello" for the messages at lines 457,
1525, 1539, so those work once `backend_name` can be `"trello"`. The dialog
dropdown's inline label format (513–517) needs no change because of D4
(`name` already holds "Board / List", `key=None`).

`_CONTAINER_LABEL_BY_BACKEND["trello"]` = «Список» (the short header label next
to the dropdown; dropdown items show the full "Board / List").

## Config keys

```jsonc
"trello_api_key": "",   // Trello API key (trello.com/app-key)
"trello_token": "",     // Trello user token (authorized for the app key)
"trello_enabled": false // opt-in (D5)
```

`trello_lists_cache` (the value of `_TRELLO_CACHE_KEY`) is written at runtime,
mirroring `linear_teams_cache` / `glide_boards_cache`.

## Testing strategy

Mocked unit tests (mirroring `test_glide_client` / `test_linear_backend`):

- **`test_trello_client.py`** — `requests` mocked: key+token present in query
  params; correct endpoints; error mapping (401/403/404/429/5xx +
  ConnectionError/Timeout → `TrelloError` with the right Russian text);
  `validate_key` parsing; `list_containers` nested-lists flattening;
  `board_context` member/label mapping **including the empty-name-label drop**;
  list→board resolution; `create_card` body (priority-line present/absent,
  `idMembers`/`idLabels`/`due` omitted when empty).
- **`test_trello_backend.py`** — `Container` shape (`name="Board / List"`,
  `key=None`); `context` returns `{"members","labels"}`; `create` mapping;
  `CreatedIssue` = `#idShort` + url.
- Extend **`test_errors.py`** (Trello detection + display name) and
  **`test_backends_factory.py`** (`backend_from_name("trello")` reads both
  keys; raises on missing credentials).

CI constraint: UI-touching tests must **not** import `ui.app` on Linux CI
(sounddevice loads PortAudio at import; the Ubuntu runner lacks it). Use
source-text assertions or `importlib.util.spec_from_file_location`, per
`test_ui_constants.py`. Local Windows pytest can pass while CI fails — verify
the import surface.

Baseline: `pytest` is 333 green today; this adds ~25 tests. `ruff check .`
must stay clean.

## Manual smoke (required before merge)

Mocked tests verify string composition, not live API contract. The exact field
names and whether `GET /lists/{id}/board` accepts nested `members`/`labels`
can only be confirmed against the real API (lesson: mocks miss contract drift).
With real credentials on a throwaway board:

1. Settings: enter key + token → Validate → «✓ Подключено: <name>».
2. Extract dialog: Trello appears; pick a "Board / List".
3. Run extraction → confirm assignee + labels were grounded (only real board
   members/labels offered, no hallucinations).
4. Send → card lands in the **chosen list** with title, description (with the
   priority line), assignee, labels, and due date.
5. Error paths: wrong token → friendly Russian message, not a raw traceback.

## Risks & open questions

- **Nested context call** — if `GET /lists/{id}/board` rejects nested
  `members`/`labels`, fall back to the 2-call path (already specified). Smoke
  step 3 confirms which.
- **Duplicate cards on retry** — no idempotency key in Trello; accepted for
  v1, documented.
- **Token longevity** — Trello tokens can be non-expiring or scoped; the user
  generates one manually. Out of scope to manage rotation.

## Out of scope

- Card updates/close/sync; checklists, attachments, custom fields, Power-Ups.
- Creating boards/lists from the app.
- A live model browser or per-board default-list config (the rejected
  approaches B/C from brainstorming).
