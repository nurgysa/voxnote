# Directory context injection — Phase A UI part 2, PR-1 design

**Date:** 2026-05-30
**Status:** Design — awaiting user review before planning
**Parent spec:** `docs/superpowers/specs/2026-05-30-directories-and-voice-id-design.md` (this is the implementable PR-1 slice of that spec's "Part A — directory + context injection")

## Motivation

The protocol generator and task extractor currently produce outputs with **no
knowledge of who the participants are or what the project is about**. The
protocol sees faceless `Спикер 1 / Спикер 2`; task extraction grounds assignees
only on tracker members. The whole point of the directory initiative is to feed
real participant names/roles + the project description into these prompts so the
LLM frames protocol and action items in the project's terms.

The backend for this **already shipped** (Phase A core):

- `tasks/protocol_generator.py::generate(..., context: str | None = None)` —
  accepts and injects context into the prompt (`build_prompt(..., context=)`),
  and a `speakers` list that fills the protocol's `## participants` block.
- `tasks/extractor.py::extract(..., context: str | None = None)` — same.
- `directory/context.py::render_meeting_context(people, project) -> str` —
  renders the `=== КОНТЕКСТ ВСТРЕЧИ ===` block; returns `""` when empty.
- `directory/store.py::DirectoryStore` — people/projects CRUD.
- `«Справочники»` dialog (Phase A UI part 1) — lets the user populate the
  directory.

**Nothing currently passes a real `context=` value.** This PR is purely the
**UI + glue** that lets the user pick the meeting's project + participants in the
Extract dialog and threads the rendered context into both LLM calls.

## Scope

**In scope (PR-1):**

- A "Контекст встречи" section in the Extract-Tasks dialog: a **project**
  dropdown + a **participants** checkbox list (project-driven defaults).
- Glue: render the context block and pass `context=` to both `extract()` and
  `protocol_generator.generate()`; pass participant ФИО as `speakers=`.
- Persist the selection to `<meeting>/speakers.json` (forward-compatible
  schema) and restore it when the dialog re-opens.

**Out of scope (explicitly deferred):**

- **Per-speaker attribution** (`Спикер N` → person mapping by reading
  `segments.json`) — that is **PR-2** of Phase A UI part 2.
- **Voice identification / biometrics** (the `voiceid/` ONNX package, auto-fill
  suggestions) — that is **Phase B1**, blocked on model selection.
- Re-attribution of meetings transcribed before this ships.

## Design

### 1. UI — "Контекст встречи" section in the Extract dialog

Location: `ui/dialogs/extract_tasks/__init__.py`, a section near the top of the
dialog — beside the existing «Генерировать протокол» checkbox, above the
«Извлечь задачи» button. ASCII sketch:

```
┌ Извлечение задач ──────────────────────────────────┐
│ Бэкенд: [Linear ▾]   Модель: [________]   [↻]      │
│ ▾ Контекст встречи (грунтинг протокола и задач)     │
│     Проект:    [Миграция биллинга ▾]                │
│     Участники: ☑ Айбек  ☑ Мария  ☐ Иван            │
│ ☑ Генерировать протокол                             │
│              [  Извлечь задачи  ]                   │
├─────────────────────────────────────────────────────┤
│ (редактор задач — как сейчас)                       │
└─────────────────────────────────────────────────────┘
```

- **Проект** — `CTkOptionMenu` listing directory projects by name, plus a
  «— нет —» sentinel (no project).
- **Участники** — a checkbox list of directory people. When the project
  selection changes, participants whose `Person.project_ids` contains the
  selected project id are **auto-checked**; the user can tick/untick freely.
  Selecting «— нет —» leaves the current checks untouched (manual control).
- The dialog loads a fresh `DirectoryStore()` in its constructor (mirrors the
  «Справочники» dialog). On `DirectoryError`, show a warning (messagebox) and
  treat the directory as empty.
- **Empty directory** → the section renders a hint
  («Справочник пуст — добавьте людей и проекты в «Справочники») and contributes
  nothing (no project, no participants).

### 2. Glue — thread the context through

Thread-safety: the UI selection is read on the **main thread** in `_on_extract`
(line ~535), exactly where `container`/`model`/`backend_name` are already
captured before spawning the worker. We resolve the checked person ids and the
selected project to `list[Person]` / `Project | None` from the in-memory store
and pass them as args into `_run_extraction`. CTk variables are **never** read
from the worker thread.

In `_run_extraction` (worker):

- `context = render_meeting_context(people, project)` — pure, no I/O; safe in
  the worker.
- Pass `context=context` to `extract(...)` (line ~612) and
  `protocol_generator.generate(...)` (line ~666). When `context == ""` we pass
  `context=None` so behaviour is byte-identical to today.
- Replace `speakers=[]` (line ~668) with `speakers=[p.full_name for p in people]`
  so the protocol's `## participants` block lists real ФИО.

### 3. Persistence — `<meeting>/speakers.json`

Written in `_run_extraction` (same place `save_tasks_raw` writes `tasks.json`),
only when a project or at least one participant is selected:

```json
{
  "project_id": "<project id or null>",
  "participants": ["<person id>", "..."],
  "speakers": {}
}
```

- The empty `"speakers"` object is the **forward-compatible slot** PR-2 fills
  with the `{ "SPEAKER_00": "<person_id>", ... }` map. PR-1 never writes into it.
- Atomic write via the existing tmp-rename helper pattern (mirrors
  `utils.save_segments`).
- On dialog open, after the directory loads and the section is built, read
  `speakers.json` (if present) and restore: select the project in the dropdown,
  check the listed participants. Missing ids (person/project deleted since) are
  skipped silently.

### 4. Isolation / testability

CustomTkinter dialogs can't be instantiated on headless Linux CI (they pull
`ui` imports that load PortAudio), so the dialog itself is covered by
**source-text structural tests** (the established repo pattern) asserting the
section, the `context=`/`speakers=` wiring, and the speakers.json read/write
strings are present.

The non-UI logic is extracted into **pure, unit-testable functions** (no CTk):

- `directory/context.py` gains
  `default_participants(people: list[Person], project_id: str | None) -> list[Person]`
  — the "members of this project" default (kept beside `render_meeting_context`
  since both are pure context-preparation helpers; no new module). Real pytest
  unit tests.
- `render_meeting_context` is already pure and unit-tested.
- A small pure helper to (de)serialize the speakers.json shape, unit-tested
  with `tmp_path`.

### 5. Backward compatibility & error handling

- No project and no participants → `render_meeting_context` returns `""` →
  `context=None`, `speakers=[]` → **today's exact behaviour**. Zero regression
  for users with an empty/unused directory.
- `DirectoryError` on load → warning + empty section; extraction still works.
- `OSError` writing `speakers.json` → log a warning and continue (the
  extraction + tasks.json commit must not be blocked by a context-persistence
  failure — same principle as the existing protocol-generation `OSError`
  handling at line ~682).
- Reading a malformed `speakers.json` → ignore it, start with an empty
  selection (never crash the dialog).

## Data flow

```
DirectoryStore.load()  ──▶  project dropdown + participant checkboxes
        (constructor)            │ (project change → default_participants)
                                 ▼
        _on_extract (main thread): resolve selected Project + list[Person]
                                 ▼  (passed as args)
        _run_extraction (worker):
            context      = render_meeting_context(people, project)
            speaker_names= [p.full_name for p in people]
            extract(..., context=context)
            generate(..., speakers=speaker_names, context=context)
            write <meeting>/speakers.json {project_id, participants, speakers:{}}
```

## Testing strategy

- **Pure unit tests:** `default_participants` (project filter, no project →
  empty, unknown id → empty), speakers.json (de)serialization round-trip,
  and the existing `render_meeting_context` coverage.
- **Structural source-text tests** for the dialog: section present, both call
  sites pass `context=`, `speakers=` no longer hard-coded `[]`, speakers.json
  read on open + write on extract.
- **Manual GUI smoke** (required — UI not unit-testable headless): pick a
  project → participants auto-check → extract → open the generated
  `protocol.md` and confirm the `=== КОНТЕКСТ ВСТРЕЧИ ===` block + real names;
  re-open the dialog → selection restored from `speakers.json`.

## Open questions

None — all design decisions resolved during brainstorming (phasing:
context-injection first; project-driven participant defaults; persist to
`speakers.json` with a forward-compatible schema).
