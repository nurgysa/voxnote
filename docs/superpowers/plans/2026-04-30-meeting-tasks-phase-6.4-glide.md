# Phase 6.4 — Glide Backend (Foundation)

## Context

Phases 6.0–6.3 ship a complete transcript→Linear pipeline. Now we add a
**second** task-manager backend: Glide (`os.tensor-ai.tech`), a custom internal
tool with REST API. Linear and Glide must coexist — at extract time the user
picks one via dropdown. Mirrors the `Phase 6.0 Foundation → 6.1 Wiring →
6.2 Edit → 6.3 Send` split that worked for Linear.

This plan is **6.4.0 Foundation only**: HTTP client + Settings section + config
plumbing + tests. UI dialog wiring is 6.4.1 (next chunk).

**Spec:** [`docs/superpowers/specs/2026-04-28-meeting-tasks-pipeline-design.md`](../specs/2026-04-28-meeting-tasks-pipeline-design.md)
(extending — Glide is parallel-backend, not in original spec).

**Glide API ref:** `C:\Users\nurgisa\Downloads\integrations-developer.md` (user-provided).

## Architectural decisions (user-confirmed)

1. **Backend choice in dialog** (not Settings) — dropdown next to Команда. Per-extract.
2. **No LLM grounding for Glide** — extractor only fills title/description/priority.
   Assignee/labels stay manual in editor (Glide's column-based schema is too
   heterogeneous for reliable LLM matching across boards).
3. **Identifier UX** — short prefix of UUID (first 6 chars, e.g. `467e14`).
   Click on SENT row opens task in Glide via constructed URL.
4. **Persistence** — `meta.backend` field discriminates per-extract; all tasks
   in one `tasks.json` go to one backend. `tasks_raw.json` carries it too.

## Mapping decisions (Linear ↔ Glide)

| Field | Linear | Glide |
|---|---|---|
| Priority | int 0-4 (1=Urgent counter-intuitive) | string `critical/high/medium/low` |
| Assignee | UUID via `assignee_id` | email/name via `fields: {Manager: ...}` (manual in 6.4.0) |
| Labels | native `label_ids[]` UUIDs | mostly N/A in 6.4.0 (could write to Status column) |
| Container | Team (UUID + key) | Board (UUID + name) |
| Server-warnings | hard fail | `fields_warnings[]` non-fatal (must surface in UI) |

**Priority mapping** (Linear `Priority` enum → Glide string):
- `NONE` → `None` (omit `priority` from payload — Glide leaves default)
- `URGENT` → `"critical"`
- `HIGH` → `"high"`
- `MEDIUM` → `"medium"`
- `LOW` → `"low"`

## File deltas (6.4.0)

| File | Action | Lines |
|---|---|---|
| `tasks/glide_client.py` | **Create** — HTTP client (validate, list_boards, board_schema, create_task) | ~200 |
| `tests/test_tasks_glide_client.py` | **Create** — 10–12 mocked-HTTP tests | ~250 |
| `ui/dialogs/settings.py` | Modify — `_build_glide_section` after Linear | ~80 |
| `tasks/schema.py` | (No change) — Priority enum already correct |
| `config.example.json` | Modify — add `"glide_api_key": ""` |
| `utils.py` | (No change) — `save_config` already handles new keys |

## File deltas (6.4.1 — next chunk, NOT this session)

- `tasks/backends/{__init__,base,linear,glide}.py` — Protocol + adapters
- `tasks/sender.py` — accept generic backend instead of `linear_client`
- `tasks/extractor.py` — skip member/label grounding when backend=glide
- `tasks/persistence.py` — read/write `backend` in meta
- `ui/dialogs/extract_tasks.py` — backend dropdown + container dropdown swap

## glide_client.py — public API

```python
class GlideError(Exception): ...

class GlideClient:
    def __init__(self, api_key: str): ...
    def close(self) -> None: ...
    def validate_key(self) -> dict:                  # GET /boards (proxy validation)
    def list_boards(self) -> list[dict]:             # GET /boards
    def board_schema(self, board_id: str) -> dict:   # GET /boards/{id}
    def create_task(
        self, *, title: str,
        description: str | None = None,
        priority: str | None = None,    # "critical|high|medium|low" or None
        board_id: str | None = None,
        group_id: str | None = None,
        fields: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:                                       # POST /tasks
```

### Internals

- `_BASE_URL = "https://os.tensor-ai.tech/api/v1/integrations/in"`
- Auth header: `Authorization: Bearer <api_key>`
- `_request(method, path, *, json=None, idempotency_key=None, timeout=30)` — single
  HTTP-method entry point. Returns parsed JSON on 2xx. Raises `GlideError` on:
  - Network failure (`ConnectionError`/`Timeout`/`RequestException`) — Russian message
  - 401 → "Неверный ключ Glide"
  - 403 → "Нет доступа к ресурсу Glide"
  - 429 → "Glide rate-limit (retry-after Xs)" using `X-RateLimit-Reset`
  - 4xx/5xx → propagates `error.code` from envelope
- Idempotency: per Glide docs, recommended for all `POST`. Caller passes
  `idempotency_key`; client adds the header.

### `fields_warnings` handling

`create_task()` returns the response dict as-is. Callers (Phase 6.4.1) can
inspect `result["fields_warnings"]` and surface to UI. For 6.4.0, just log
warnings via `logger.warning` so they appear in `logs/app.log`.

## Settings section: `_build_glide_section`

Mirrors `_build_linear_section`. Below Linear:

```
╭─ Glide ─────────────────────────────────────╮
│ API ключ:                                   │
│ [glide_pk_•••••••••••••••••] [📋 Вставить]  │
│ [Проверить ключ]   ✓ Подключено: 5 досок    │
╰─────────────────────────────────────────────╯
```

Validate behavior: `GlideClient(key).validate_key()` returns count of boards
visible to the integration. Display: `✓ Подключено: <N> досок` or red error.

## Tests (`test_tasks_glide_client.py`)

Mocks `requests.Session.request` (the lower-level seam) so client can be tested
without network. Cases:

1. `test_init_rejects_empty_key` — `GlideError` on empty string
2. `test_validate_key_returns_board_count`
3. `test_validate_key_401` → `GlideError` "неверный"
4. `test_list_boards_returns_array`
5. `test_board_schema_includes_columns_and_groups`
6. `test_create_task_minimal` — only title, default board
7. `test_create_task_full` — title + desc + priority + fields + idempotency
8. `test_create_task_omits_none_priority` — priority=None NOT in payload
9. `test_create_task_passes_idempotency_header`
10. `test_create_task_429_includes_retry_after`
11. `test_create_task_propagates_error_code` — body `{error: {code: ...}}`
12. `test_network_error_wrapped_in_glide_error`

## Verification

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_tasks_glide_client.py -v
# expect: 12 passed
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/ -v 2>&1 | tail -3
# expect: 98 passed (86 baseline + 12 new) — no UI tests yet
```

Manual smoke for Settings (after wiring):
1. Open app → Настройки → scroll to Glide section
2. Paste real key → click Проверить → see green ✓ with board count
3. Paste invalid key → see red ✗ "неверный"
4. Save → reopen Settings → key remains masked

## Critical files

- [tasks/glide_client.py](../../../tasks/glide_client.py) — new HTTP client
- [tasks/linear_client.py](../../../tasks/linear_client.py) — style reference
- [ui/dialogs/settings.py](../../../ui/dialogs/settings.py) — section host
- [tasks/schema.py](../../../tasks/schema.py) — Priority enum (no change)
