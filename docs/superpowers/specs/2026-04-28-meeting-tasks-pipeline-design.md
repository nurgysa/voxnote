# Meeting Tasks Pipeline — Design Spec

## Context

Audio Transcriber currently produces transcripts. A common downstream need: turn a
meeting transcript into a list of actionable tasks and push them to Linear. Doing
this by hand after every meeting is tedious; an LLM can do it well, given the
team's actual member list and labels as context.

This spec adds a post-transcription pipeline:
**transcript → LLM (via OpenRouter) → editable task list → Linear Backlog**.

User flow (after Phase 6.3 ships):

1. User finishes a transcription. Main window shows the transcript text.
2. User clicks **«Извлечь задачи»** in the bottom button row.
3. Extract dialog opens. User picks a model (`anthropic/claude-sonnet-4.5`
   default) and a Linear team. Clicks **«Извлечь»**.
4. Dialog populates a master-detail editor. User reviews, edits, checks/unchecks
   per-task selection. State auto-saves to disk.
5. User clicks **«Отправить выбранные в Linear»**. Tasks are POSTed one by one
   with live status (`pending → sending → sent/failed`). Failed tasks can be
   retried.

## Phasing

The feature ships in four sequential phases. Each phase is independently
shippable; a user can stop after any phase and have a working (if reduced)
feature.

| Phase | Adds | Visible result |
|-------|------|----------------|
| **6.0** Foundation | `tasks/` package skeleton + Linear client + OpenRouter client + two API key fields in Settings, with Validate buttons. No main-window changes. | Settings dialog gains two new sections. Validate confirms keys work. |
| **6.1** Extract | "Извлечь задачи" button on main window. Extract dialog with model/team dropdowns. Calls OpenRouter with team context. Saves `tasks_raw.json` to history folder. **Result is shown as raw JSON** in a textbox inside the dialog — no editor yet. | End-to-end flow works. JSON on disk. |
| **6.2** Edit | Same dialog gets the master-detail editor. JSON textbox is replaced with split layout: list left, form right. Auto-save on selection change. "+ Добавить задачу" button. Saves `tasks.json`. | Full editor. User can refine results before sending. |
| **6.3** Send | "Отправить выбранные в Linear" button activates. Per-task statuses with live updates. Retry for failed. Successful tasks open in browser on click. | Closed loop: transcript → tasks → Linear. |
| **6.4** *(future, out of scope)* | Re-open from History (already-extracted entries get "Open tasks" button). Global default-language override in Settings. Full OpenRouter model catalog browser. AssemblyAI Validate button (parity with new Settings sections). | Optional polish. |

## Architecture

### New package: `tasks/`

```
tasks/
├── __init__.py
├── schema.py              # Task dataclass, Priority/TaskStatus enums, JSON schema
├── openrouter_client.py   # REST: list_models, complete(model, messages, json_mode)
├── linear_client.py       # GraphQL: bootstrap, team_context, create_issue
├── extractor.py           # Orchestrator: context → prompt → LLM → parse → validate
└── persistence.py         # save/load tasks_raw.json and tasks.json in history/<entry>/
```

### Modified UI files

- `ui/dialogs/extract_tasks.py` — **new** dialog. Phase 6.1 has it minimal
  (dropdowns + JSON textbox); 6.2 swaps in master-detail; 6.3 adds send & status.
- `ui/dialogs/settings.py` — **modified**. Add `_build_openrouter_section`
  and `_build_linear_section` after the existing six sections.
- `ui/app.py` — **modified**. Add "Извлечь задачи" button in the bottom row
  (between "Копировать" and "История").

### Test files (pytest, mirrors existing pattern)

- `tests/test_tasks_schema.py`
- `tests/test_tasks_persistence.py`
- `tests/test_tasks_extractor.py`

### Data flow (Phase 6.3, full pipeline)

```
[Main window: transcript visible]
  ↓ click "Извлечь задачи"
[ExtractTasksDialog opens]
  ↓ on open: linear_client.bootstrap() if cache miss/expired
  ↓        → fills team dropdown
[User picks model + team]
  ↓ click "Извлечь"
[Worker thread]
  ├─ linear_client.team_context(team_id)         ← members + labels in one query
  ├─ build_prompt(transcript, members, labels, lang)
  ├─ openrouter_client.complete(model, messages, json_mode=True)
  ├─ parse_and_validate(response, members, labels)   ← filters hallucinated IDs
  └─ persistence.save_tasks_raw(history_folder, tasks)
  ↓ via self.after(0, ...)
[6.1: JSON textbox]
[6.2: master-detail editor populates]
  ↓ user edits, auto-save on selection change
[persistence.save_tasks(history_folder, tasks)]
  ↓ click "Отправить выбранные"
[Worker thread, per task]
  └─ linear_client.create_issue(team_id, task)
       ↓ status update via self.after(0, ...)
       └─ pending → sending → sent/failed
```

### Threading model

Reuses the existing pattern from `App._run_transcription`
([ui/app.py:836–916](../../../ui/app.py)):

- All network I/O (Linear, OpenRouter) runs in `threading.Thread(daemon=True)`.
- UI updates happen via `self.after(0, callback)` from the worker.
- Cancellation via `threading.Event()` per dialog. Worker checks the event at
  natural checkpoints (before each network call, between issues during send).

### Configuration additions (`config.json`)

```json
{
  "openrouter_api_key": "sk-or-...",
  "linear_api_key": "lin_api_...",
  "tasks_default_model": "anthropic/claude-sonnet-4.5",
  "tasks_recent_models": [
    "anthropic/claude-sonnet-4.5",
    "mistralai/mistral-large-2411"
  ],
  "linear_teams_cache": {
    "data": [{"id": "...", "name": "...", "key": "..."}],
    "fetched_at": "2026-04-28T15:30:00"
  }
}
```

`tasks_recent_models` preserves user-typed custom slugs across sessions, so they
appear in the dropdown labelled `(custom) <slug>`. A slug enters this list only
**after a successful extraction** (not on every typed character). Limit: last 5
distinct custom slugs (FIFO eviction).
`linear_teams_cache` has a 24-hour TTL; the `[↻]` button next to the team
dropdown forces a refresh.

## Data Model

### `tasks/schema.py`

```python
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Optional

class Priority(IntEnum):
    """Maps directly to Linear API priority (int 0-4).

    Counter-intuitive: 1 = Urgent, 4 = Low. Lower number = higher priority.
    """
    NONE   = 0
    URGENT = 1
    HIGH   = 2
    MEDIUM = 3
    LOW    = 4

class TaskStatus(Enum):
    """Send status to Linear. Only used in Phase 6.3+."""
    PENDING = "pending"   # not yet sent
    SENDING = "sending"   # in flight
    SENT    = "sent"      # successful
    FAILED  = "failed"    # failure (see send_error)
    SKIPPED = "skipped"   # user unchecked

@dataclass
class Task:
    # LLM-extracted fields
    title: str
    description: str = ""
    priority: Priority = Priority.NONE
    assignee_id: Optional[str] = None     # Linear member UUID
    assignee_name: Optional[str] = None   # cached display name for UI
    label_ids: list[str] = field(default_factory=list)
    label_names: list[str] = field(default_factory=list)
    due_date: Optional[str] = None        # ISO "YYYY-MM-DD"

    # Local-only fields (not from LLM)
    local_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    selected: bool = True                 # user's checkbox state
    status: TaskStatus = TaskStatus.PENDING
    linear_issue_id: Optional[str] = None      # set after successful send
    linear_issue_url: Optional[str] = None     # ditto
    send_error: Optional[str] = None      # ditto, if status == FAILED
```

### Priority mapping (LLM ↔ Python ↔ Linear)

| LLM string | Python `Priority` | Linear API int |
|------------|-------------------|----------------|
| `"none"` | `Priority.NONE` | `0` |
| `"urgent"` | `Priority.URGENT` | `1` |
| `"high"` | `Priority.HIGH` | `2` |
| `"medium"` | `Priority.MEDIUM` | `3` |
| `"low"` | `Priority.LOW` | `4` |

Conversion: `Priority[name.upper()]`. Unknown string falls back to `Priority.NONE`
with a warning logged.

### Persistence — files in `history/<entry>/`

Two files. `tasks_raw.json` is **immutable** — written once, right after a
successful extraction. `tasks.json` is **mutable** — overwritten on every user
edit and after every send-status update. The two-file scheme is an audit trail:
even if the user trashes a task during editing, the original LLM output is
preserved.

**`tasks_raw.json`**:

```json
{
  "extracted_at": "2026-04-28T15:30:00",
  "model": "anthropic/claude-sonnet-4.5",
  "team_id": "abc-123",
  "team_name": "Engineering",
  "transcript_lang": "ru",
  "tasks": [
    {
      "local_id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Починить login bug в iOS",
      "description": "Айдар сообщил жалобы пользователей...",
      "priority": "high",
      "assignee_id": "user-uuid-айдар",
      "assignee_name": "Айдар",
      "label_ids": ["label-uuid-bug"],
      "label_names": ["bug"],
      "due_date": "2026-05-15"
    }
  ]
}
```

**`tasks.json`** — superset of `tasks_raw.json` plus user-state:

```json
{
  "extracted_at": "2026-04-28T15:30:00",
  "model": "anthropic/claude-sonnet-4.5",
  "team_id": "abc-123",
  "team_name": "Engineering",
  "edited_at": "2026-04-28T15:45:00",
  "tasks": [
    {
      "local_id": "550e8400-...",
      "title": "Починить login bug в iOS (приоритет — pre-iOS-18 fix)",
      "description": "...",
      "priority": "urgent",
      "assignee_id": "user-uuid-айдар",
      "assignee_name": "Айдар",
      "label_ids": ["label-uuid-bug", "label-uuid-mobile"],
      "label_names": ["bug", "mobile"],
      "due_date": "2026-05-10",
      "selected": true,
      "status": "sent",
      "linear_issue_id": "ENG-1234",
      "linear_issue_url": "https://linear.app/yourorg/issue/ENG-1234",
      "send_error": null
    }
  ]
}
```

### Post-LLM validation (`extractor.py`)

After `openrouter_client.complete` returns and we `json.loads` the response, but
**before** `save_tasks_raw`:

| Field | Rule | On violation |
|-------|------|--------------|
| `title` | non-empty string | Drop the task entirely; log warning |
| `description` | string | Default to `""` |
| `priority` | one of `none/low/medium/high/urgent` (case-insensitive) | Fall back to `none`, log warning |
| `assignee_id` | must be in fetched `team_members` IDs | Clear (LLM hallucinated); log warning |
| `label_ids` | each ID must be in fetched `team_labels` IDs | Filter out invalid; keep valid |
| `due_date` | ISO `YYYY-MM-DD`, not more than 30 days in past | Clear; log warning |

All warnings flow through `logging_setup.get_logger(__name__)` to `logs/app.log`.
The user sees an aggregate badge in the dialog: *«Извлечено 12 задач (3 поля
скорректированы)»*. The badge is informational; it doesn't block the user.

**Edge case — all tasks invalid**: if every LLM-returned task fails the `title`
rule and gets dropped, we do **not** write `tasks_raw.json`. Instead, the
dialog shows an error: *«LLM не вернул валидных задач. Попробуйте другую
модель»* and offers a *«Показать сырой ответ»* button that reveals what the
LLM actually returned (helpful for prompt-tuning by the developer).

## UI Design

### Main window — new button

Add **«Извлечь задачи»** to the bottom button row in `App._build_ui`
([ui/app.py:325–350](../../../ui/app.py)), between *Копировать* and *История*:

```
[Сохранить (TXT/SRT/VTT)] [Копировать] [Извлечь задачи] [История] [Audio Cutter]
```

**State**:
- `disabled` whenever `_textbox` has no transcript text (same trigger as
  `_btn_save`/`_btn_copy`).
- On click: validate that both `openrouter_api_key` and `linear_api_key` are set
  in config. If not, show `messagebox.showwarning` *«Настройте OpenRouter и
  Linear в Settings»* and don't open the dialog. Existing pattern from
  [ui/app.py:754–761](../../../ui/app.py) for the AssemblyAI cloud check.

### Settings dialog — two new sections

Added at the bottom of the existing section chain (after Dictionaries):

```
╭─ OpenRouter ─────────────────────────────────╮
│ API ключ:                                       │
│ [sk-or-•••••••••••••••••••••••• ] [📋 Вставить] │
│ [Проверить ключ]    ✓ Активен (баланс: $12.40) │
│                                                  │
│ Модель по умолчанию:                            │
│ [anthropic/claude-sonnet-4.5            ▾]      │
╰──────────────────────────────────────────────────╯

╭─ Linear ─────────────────────────────────────────╮
│ API ключ:                                         │
│ [lin_api_••••••••••••••••••••••••] [📋 Вставить]  │
│ [Проверить ключ]    ✓ Подключено: Нурғыса А.     │
╰───────────────────────────────────────────────────╯
```

**Validate buttons** make a single cheap request:
- OpenRouter → `GET /api/v1/auth/key`. Response includes `label`, `usage`, and
  remaining balance. We display the balance, which is immediately useful.
- Linear → GraphQL `query { viewer { id name email } }`. We display the user's
  display name to confirm "right key, right account".

Validation result text appears next to the button (green ✓ or red ✗). State is
ephemeral — it does NOT save to config; just runtime UI feedback.

### ExtractTasksDialog — phase progression

**Phase 6.1 (minimal)** — ~640×520:

```
┌─ Извлечение задач ─────────────────────────────────────────┐
│ Модель:  [anthropic/claude-sonnet-4.5         ▾]            │
│ Команда: [Engineering                          ▾]   [↻]     │
│ [Извлечь]                                                   │
│ ───────────────────────────────────────────────────────     │
│ ✓ Извлечено 12 задач (3 поля скорректированы)               │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ {                                                       │ │
│ │   "extracted_at": "2026-04-28T15:30:00",                │ │
│ │   "tasks": [ { "title": "...", ... }, ... ]             │ │
│ │ }                                                       │ │
│ └────────────────────────────────────────────────────────┘ │
│ Сохранено: history/2026-04-28_15-30-00_…/tasks_raw.json     │
│                                                  [Закрыть]  │
└─────────────────────────────────────────────────────────────┘
```

**Phase 6.2 (master-detail)** — ~960×680:

```
┌─ Извлечение задач ──────────────────────────────────────────────────────────┐
│ Модель: [Sonnet 4.5 ▾] Команда: [Eng ▾] [↻] [Извлечь] [+ Добавить задачу]   │
│ ─────────────────────────────────────────────────────────────────────────── │
│ ┌──────────────────────────┐  ┌──────────────────────────────────────────┐ │
│ │ ☑ Починить login bug      │  │ Title:                                   │ │
│ │   👤 Айдар   🔴 High       │  │ [Починить login bug в iOS         ]      │ │
│ │ ☑ Документация API        │  │                                           │ │
│ │   👤 Нурғыса  🟡 Med       │  │ Priority: [High            ▾]            │ │
│ │ ☐ Refactor cache layer    │  │ Assignee: [Айдар           ▾]            │ │
│ │   👤 —      ⚪ None        │  │ Labels:   [bug ✕] [+]                    │ │
│ │ ☑ Релиз 1.4               │  │ Due:      [2026-05-15     ] [Очистить]   │ │
│ │ ☐ Investigate timeout     │  │                                           │ │
│ │                            │  │ Description:                              │ │
│ │ … (12 задач)               │  │ ┌──────────────────────────────────────┐ │ │
│ │                            │  │ │ Айдар сообщил жалобы пользователей   │ │ │
│ │ [✓ Все] [✗ Снять] [🗑 Уд]  │  │ │ на потерю сессии…                    │ │ │
│ └──────────────────────────┘  └──────────────────────────────────────────┘ │
│ Извлечено 12 задач (3 поля скорректированы)               [Закрыть]         │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Editor behavior**:

- Left list: `CTkScrollableFrame` with custom row widgets. Each row has a
  checkbox + title line + summary line (`👤 assignee | priority`).
- Selected row gets a different background color. Click anywhere on the row
  (except the checkbox) to select.
- Right form's widgets bind to `tk.StringVar`/`tk.BooleanVar` for the currently
  selected task. Switching selection swaps the variable bindings.
- **Auto-save** triggers on: (a) selection change; (b) Tab out of the last form
  field; (c) dialog close. Implementation: a single `_persist_current_task()`
  helper called from each trigger.
- **«+ Добавить задачу»**: appends an empty `Task` to the list, selects it. For
  cases where the LLM missed something the user wants to add by hand.
- **«✗ Снять»**: clears all checkboxes. If zero are checked, *«Отправить»* is
  disabled.
- **«🗑 Удалить выделенную»**: removes the current task. No confirmation, but
  with a 5-step undo stack (Ctrl+Z) — protects against the most common mishap.

**Phase 6.3 (send + statuses)** — same dimensions, status badges replace
checkboxes after send starts:

```
┌─ Извлечение задач ─────────────────────────────────────────────────────────┐
│ ... (header unchanged) ...                                                  │
│ ┌──────────────────────────┐  ┌──────────────────────────────────────────┐ │
│ │ ✓ Починить login… ENG-1234│  │ Title: ...                                │ │
│ │ ✓ Документация…  ENG-1235│  │ ...                                       │ │
│ │ ⚠ Refactor cache 401      │  │ Linear-тикет:                             │ │
│ │   (Network error)         │  │ [ENG-1234 — открыть в Linear ↗]           │ │
│ │ ✓ Релиз 1.4      ENG-1236│  │                                            │ │
│ │ ☐ Investigate…   pending  │  │                                            │ │
│ └──────────────────────────┘  └──────────────────────────────────────────┘ │
│ Отправлено: 3/4 (1 ошибка) │ [Отправить выбранные (3)] [Повторить упавшие] │
│                                                              [Закрыть]      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Status icons** (replace checkbox after send starts):
- `✓` (green) — sent; followed by Linear issue identifier
- `⚠` (red) — failed; followed by short error code (`401`, `429`, `500`)
- `⏳` (blue) — sending (in flight)
- `☑` / `☐` — pending (not yet attempted)
- `—` (gray) — skipped (user unchecked)

**Retry**: «Повторить упавшие» re-sends only tasks with `status == FAILED`.
Already-sent tasks (`status == SENT`) are not retouched, even if the user clicks
retry — protects against duplicate Linear issues.

## External APIs

### OpenRouter

**Base URL**: `https://openrouter.ai/api/v1`

**Headers (every request)**:

```
Authorization: Bearer ${openrouter_api_key}
HTTP-Referer:  https://github.com/<your-org>/audio-transcriber   # optional
X-Title:       Audio Transcriber
Content-Type:  application/json
```

**Endpoints used**:

| Endpoint | Where | Purpose |
|----------|-------|---------|
| `POST /chat/completions` | `extractor.py` | The main extraction call |
| `GET /auth/key` | Settings Validate | Confirm key works, show balance |
| `GET /models` | (Phase 6.4) | Full model catalog browser |

**JSON-mode strategy**:

```python
body = {
    "model": "anthropic/claude-sonnet-4.5",
    "messages": [
        {"role": "system", "content": <prompt>},
        {"role": "user",   "content": <transcript>}
    ],
    "response_format": {"type": "json_object"},  # not all models support
    "temperature": 0.2,                          # determinism
}
```

Not all models honor `response_format`. Strategy:

1. Try with `response_format=json_object`.
2. If 400 with "unsupported parameter" → retry without `response_format` and
   add an explicit `Return strictly valid JSON, no prose, no markdown fences`
   instruction in the system prompt.
3. Parse response with light pre-processing: strip leading/trailing
   ` ```json ... ``` ` fences if present, then `json.loads`.

**Cost estimation** (shown in the dialog before extraction):

- Transcript ~30 min ≈ 12 000 tokens input.
- Team context (10–20 members + 5–15 labels) ≈ 1 500 tokens.
- Prompt overhead ≈ 1 000 tokens.
- Output ≈ 3 000 tokens for 12 tasks.
- Sonnet 4.5: ~$3/1M input, ~$15/1M output → **~$0.09 per medium meeting**.

Estimation heuristic: `tokens ≈ len(transcript_chars) / 4`. Imprecise but good
enough for "Стоимость ≈ $0.09" UI hint. The `usage` field in the response gives
the exact post-hoc number.

### Linear

**Endpoint**: `https://api.linear.app/graphql` (single URL, GraphQL)

**Header**:

```
Authorization: ${linear_api_key}        # NB: no "Bearer" prefix
Content-Type:  application/json
```

This is a Linear quirk — most APIs use `Bearer`. Easy to copy-paste from
OpenRouter and break.

**Operations**:

```graphql
# 1. Bootstrap: validate + team list (one round-trip)
query Bootstrap {
  viewer { id name email }
  teams  { nodes { id name key } }
}

# 2. Team context: members + labels in one query (one round-trip)
query TeamContext($teamId: String!) {
  team(id: $teamId) {
    members { nodes { id name displayName email } }
    labels  { nodes { id name color } }
  }
}

# 3. Issue creation (one mutation per task)
mutation CreateIssue(
  $teamId: String!, $title: String!, $description: String,
  $priority: Int, $assigneeId: String, $labelIds: [String!],
  $dueDate: TimelessDate
) {
  issueCreate(input: {
    teamId: $teamId, title: $title, description: $description,
    priority: $priority, assigneeId: $assigneeId,
    labelIds: $labelIds, dueDate: $dueDate
  }) {
    success
    issue { id identifier url }
  }
}
```

**Caching**:
- `Bootstrap` response → `config["linear_teams_cache"]` with timestamp.
  TTL 24 hours. `[↻]` button forces refresh.
- `TeamContext` is **not** cached. It changes more frequently (members join,
  labels added) and the cost is small enough that fresh per-extract is fine.

**Rate limit**: 1500 requests per hour per personal API key. One extract uses
1 (TeamContext) + N (issueCreate) requests. Even with 30 tasks, well under the
limit.

### Error handling matrix

| Error class | Source | UI behavior | Auto-retry |
|-------------|--------|-------------|------------|
| Network unreachable (`ConnectionError`, DNS fail) | OR / Linear | Red text in dialog: *«Нет соединения с интернетом»* | 1 retry, 2 sec backoff |
| `401 Unauthorized` | OR / Linear | Messagebox *«Неверный API ключ»* + button *«Открыть Settings»* | No |
| `403 Forbidden` | Linear | *«Нет прав для команды X — выберите другую»* | No |
| `429 Too Many Requests` | OR / Linear | Read `Retry-After` header → wait → retry | Up to 2 auto-retries |
| `500/502/503/504` | OR / Linear | *«Сервер временно недоступен»* | 1 retry, 3 sec backoff |
| Timeout (>60 s on extract) | OR | *«LLM не ответил за 60 секунд»* + manual *«Повторить»* button | No |
| Malformed JSON from LLM | OR | *«LLM вернул некорректный JSON»* + raw response in textbox + suggest different model | No |
| Content moderation refusal | OR | *«Модель отказалась обрабатывать»* + suggest different model | No |
| Hallucinated assignee/label ID | post-LLM validation | Silently clear that field, log warning, badge: «N полей скорректировано» | N/A |
| Linear partial failure (some sent, then 401/429) | Linear | Stop send loop. Already-sent stay `SENT`, rest stay `PENDING`. *«Повторить упавшие»* button | Per-task, manual |

All exceptions flow through `logger.exception(...)` to `logs/app.log`. Critical
crashes during extract or send write a `transcribe_crash_*`-style artifact to
`logs/`, mirroring [ui/app.py:891–908](../../../ui/app.py).

### Cancellation

One `threading.Event()` per dialog instance. Worker thread checks it at each
natural checkpoint:

- Before TeamContext request.
- Before OpenRouter request.
- During send loop, before each `issueCreate`.

Mid-flight OpenRouter cancellation: use `httpx.Client(timeout=...)`; the client's
`close()` from a second thread interrupts the in-progress request. Already-sent
Linear issues stay sent — no undo, by design (see Insight in §"Send"
discussion: rollback would create a new bug class around external visibility).

## Testing

Unit tests follow the existing pattern in
[tests/test_transcriber_pure.py](../../../tests/test_transcriber_pure.py) —
pure functions, no real network.

| File | Coverage |
|------|----------|
| `tests/test_tasks_schema.py` | Task ↔ dict round-trip; Priority enum mapping (string ↔ enum ↔ Linear int); empty title raises; unknown priority falls back to `NONE` |
| `tests/test_tasks_persistence.py` | save/load `tasks_raw.json` and `tasks.json` via pytest's `tmp_path`; edited form preserves all fields; concurrent overwrite behavior |
| `tests/test_tasks_extractor.py` | Full extraction logic via mocked `openrouter_client` and `linear_client`. Cases: hallucinated `assignee_id` filtered out, hallucinated `label_id` filtered out, malformed JSON → `ExtractionError`, JSON-in-codefences parsed, partial-valid response keeps the good tasks and drops the bad |

**Out of unit test scope**:
- `openrouter_client.py` and `linear_client.py` — thin HTTP wrappers, tested
  through extractor mocks. Direct testing limited to a single smoke test with
  real key (manual run, not in CI).
- The dialog UI — Tk widgets aren't practical to unit-test. Manual smoke
  checklist below.

**Manual smoke checklist** (run before each phase merges):

1. Real OpenRouter call with each curated-list model to verify JSON mode works
   (or fallback path triggers cleanly).
2. Real Linear call against a test workspace (recommend creating an empty team
   first to isolate test data).
3. Network failure simulation: disconnect Wi-Fi mid-extract. Should surface a
   clean error, not crash.
4. Bad API key: deliberately mangle the key, verify error UX in Settings
   Validate and at extract time.
5. 30-minute mock transcript end-to-end: measure latency and confirm cost
   estimate matches actual `usage` data.

## Open questions / future work

- **Custom prompts**: power users may want to override the system prompt
  per-team (e.g., "always include Slack message permalinks in description if
  mentioned"). Defer to Phase 6.4+ if requested.
- **Multi-language meetings**: code-switched Russian ↔ Kazakh ↔ English
  meetings. Current plan: LLM auto-matches the dominant transcript language.
  Open question for real-world testing — does it produce mixed-language tasks?
- **Re-opening from History**: a History entry with `tasks.json` could get an
  *«Открыть задачи»* button that re-launches the editor on the saved state.
  Tracked as Phase 6.4.
- **Cycles, projects, parent issues** in Linear: not in initial scope. Linear
  supports rich relationships (parent issue, project, cycle). For meeting
  task extraction, top-level backlog issues are usually right. Add later if
  needed.
- **Bulk model A/B comparison**: nice-to-have UX where user picks two models
  and gets two task lists side by side. Out of scope for now.

## Implementation order summary

1. **Phase 6.0** — `tasks/` skeleton, two API clients, two Settings sections,
   Validate buttons, config plumbing. ~3–4 modules, ~150 lines tests.
2. **Phase 6.1** — extract dialog (minimal), main-window button, extractor with
   prompt + parse + validate, persistence (`tasks_raw.json`). ~2 new modules,
   ~250 lines tests.
3. **Phase 6.2** — master-detail editor in same dialog, `tasks.json` persistence
   on edit, Add/Delete/SelectAll buttons, undo stack. UI-heavy, ~500 lines code,
   no new tests (logic already covered).
4. **Phase 6.3** — Send button, per-task statuses, retry, Linear `create_issue`
   integration. ~150 lines code + ~100 lines tests.
