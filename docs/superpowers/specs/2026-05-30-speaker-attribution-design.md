# Speaker attribution (PR-2) — manual «Спикер N» → person binding

**Date:** 2026-05-30
**Status:** design approved, pending spec review
**Initiative:** directory + voice-ID (`project-directory-voice-id`)
**Umbrella spec:** `docs/superpowers/specs/2026-05-30-directories-and-voice-id-design.md` (Part A, "Speaker-attribution step")
**Predecessor:** PR-1 context injection — `docs/superpowers/specs/2026-05-30-directory-context-injection-design.md` (merged as PR #86)

## Problem

PR-1 added a «Контекст встречи» section to the Extract dialog: a project picker
plus participant checkboxes whose profiles are rendered into the protocol/task
prompts via `render_meeting_context`. It persists `<meeting>/speakers.json` as
`{project_id, participants, speakers: {}}` — but the `speakers` map is left
**empty**, a deliberate forward-compatible slot.

The gap: the transcript that reaches the LLM still carries anonymous `Спикер 1`,
`Спикер 2` labels (produced by `transcript_format._build_speaker_map`, which
renames raw provider labels `SPEAKER_0/1/…` → `Спикер N` in first-seen order).
The LLM is told the *list* of participants ("Заявленные участники: …") but has to
**guess** which voice is which person. Action items get misattributed; the
protocol's `## participants` block is ungrounded per-utterance.

PR-2 fills the empty `speakers: {}` slot with a manual binding and — crucially —
**uses that binding to substitute real names into the transcript** before the LLM
call, so the model sees who actually said what.

## Decisions (brainstorming 2026-05-30)

| # | Decision | Rationale |
|---|----------|-----------|
| D-1 | **Binding substitutes ФИО into the transcript** sent to the LLM (not just persisted, not just a prompt mapping-line). | Strongest grounding lever; the whole initiative is about protocol/task quality. |
| D-2 | **Keep both** the participant checkboxes and the new per-speaker rows, **auto-synced**: binding a speaker auto-ticks that person as a participant. | A bound speaker is obviously a participant → their profile must reach the context. Silent attendees still addable manually. Degrades when no diarization (checkboxes still work). |
| D-3 | **Defer** inline "create new person" from the binding dropdown. | The «Справочники» CRUD dialog (PR #84) already adds people. Keeps PR-2 narrow (one concern). |
| D-4 | Substitution mechanism = **targeted prefix replace** on the (possibly user-edited) transcript text, **not** a re-render from `segments.json`. | The dialog receives the *current textbox content* (`ui/app/dialogs_mixin.py:107`), which may carry manual edits. Re-rendering from segments would silently discard them. |
| D-5 | `speakers.json` key = the **raw** provider label (`SPEAKER_0`, Speechmatics `SPEAKER_1`), value = `person_id`. | Stable across re-formatting; no zero-padding assumption (the umbrella spec's `SPEAKER_00` was illustrative). |

## Existing code this builds on (verified)

- `transcript_format._build_speaker_map(segments) -> dict[str,str]` — raw label →
  `Спикер N`, first-seen order; non-`SPEAKER_` labels kept verbatim. Single source
  of truth for the friendly labels shown in the transcript, SRT, and VTT.
- `transcript_format.format_diarized` emits lines `"[MM:SS] [Спикер N]: text"`.
- `utils.save_segments(folder, segments)` writes `<meeting>/segments.json`
  (raw `[{start,end,text,speaker?}]`) — added in Phase A core.
- `utils.save_speakers(folder, project_id, participant_ids)` writes
  `{project_id, participants, speakers: {}}`; `utils.load_speakers(folder)` reads
  it, returning `{}` on missing/corrupt (never raises).
- Extract dialog `ui/dialogs/extract_tasks/__init__.py`:
  - ctor receives `transcript: str`, `history_folder: str` (no live segments).
  - `_context_person_vars: dict[person_id, BooleanVar]` — participant checkboxes.
  - `_rebuild_context_participants`, `_selected_context_project`,
    `_selected_context_people`, `_restore_context_selection`.
  - `_run_extraction(...)` builds `meeting_context` and calls
    `extract(transcript=self._transcript, …)` and
    `protocol_generator.generate(transcript=self._transcript,
    speakers=[p.full_name for p in people], …)`, then `save_speakers(...)`.

## Design

### A. New pure helper — `transcript_format.apply_speaker_names`

```python
def apply_speaker_names(text: str, name_by_label: dict[str, str]) -> str:
    """Replace bracketed friendly speaker labels with real names.

    `name_by_label` maps a friendly label ("Спикер 1") to a person's ФИО.
    Only bound labels are replaced; unbound ones stay "Спикер N". The
    bracketed token "[Спикер 1]" is replaced as a unit so "Спикер 1"
    never matches inside "Спикер 11". Identity when the map is empty.
    """
```

Implementation: for each `label, name` in the map, `text = text.replace(f"[{label}]", f"[{name}]")`. Pure, torch-free, trivially unit-testable — consistent with the rest of `transcript_format.py`.

### B. New persistence helper + extended writer — `utils.py`

```python
def load_segments(folder: str) -> list[dict]:
    """Read <folder>/segments.json. Returns [] if absent or malformed.
    Mirror of load_speakers — never raises."""

def save_speakers(folder, project_id, participant_ids,
                  speaker_map: dict[str, str] | None = None) -> None:
    # speaker_map: raw label -> person_id. None → writes {} (PR-1 back-compat).
```

`save_speakers` gains a trailing **optional** `speaker_map` (default `None` → `{}`),
so PR-1 callers and `tests/test_utils_save_speakers.py` keep passing unchanged.

### C. UI panel — «Кто говорит» sub-section

Rendered inside the existing `ctx_frame` (built ~`__init__.py:305`), below
«Участники». One row per friendly label from
`_build_speaker_map(load_segments(self._history_folder))`, in first-seen order:

```
Кто говорит
  Спикер 1   [ Айбек Нурланов        ▼ ]
  Спикер 2   [ — не выбрано —        ▼ ]
```

- Dropdown values: `["— не выбрано —"]` + every directory person's `full_name`.
  Rows default to «— не выбрано —» on first build (no prior `speakers.json`).
- State: `self._speaker_row_vars: dict[raw_label, StringVar]` +
  `self._speaker_friendly: dict[raw_label, friendly_label]`.
- One shared resolver `self._person_by_name(full_name) -> Person | None`
  (first match in `self._dir_store.people()`; «— не выбрано —» → `None`) — used
  by both auto-sync and the apply path. Same duplicate-name caveat already
  documented for `_selected_context_project`.
- **Auto-sync (D-2):** on dropdown change → `person = self._person_by_name(var.get())`;
  if `person is not None`, `self._context_person_vars[person.id].set(True)`.
- Rebuilt on directory load; the dropdowns list *all* people so they need no
  rebuild when the project changes.

**Hidden / empty states:** no `segments.json`, or segments with no `speaker`
key, or empty directory → no rows; show a muted hint
(«Диаризация недоступна — привязка не нужна» / «справочник пуст»). The dialog
behaves exactly as PR-1 in these cases.

### D. Restore on re-open

Extend `_restore_context_selection` to also read `data.get("speakers")` (raw
label → person_id) and set each row's `StringVar` to the bound person's
`full_name`, skipping ids no longer in the directory (→ «— не выбрано —»).

### E. Apply path — `_run_extraction`

Before the LLM calls:

```python
name_by_label = {}          # "Спикер N" -> ФИО, bound only
speaker_map   = {}          # raw label  -> person_id, bound only
for raw, var in self._speaker_row_vars.items():
    person = self._person_by_name(var.get())
    if person is not None:
        speaker_map[raw] = person.id
        name_by_label[self._speaker_friendly[raw]] = person.full_name

transcript_for_llm = apply_speaker_names(self._transcript, name_by_label)
```

- Pass `transcript_for_llm` to `extract(transcript=…)` and
  `generate(transcript=…)`. `speakers=[p.full_name for p in people]` stays —
  `people` already ⊇ bound persons via auto-sync.
- `save_speakers(self._history_folder, project.id if project else None,
  [p.id for p in people], speaker_map=speaker_map)`.
- Unchanged: the LLM prompt-builder contracts
  (`protocol_generator`/`extractor`) — PR-2 only changes the *data* passed in.
  Write failures stay non-fatal (logged warning, as PR-1).

## Edge cases

| Case | Behaviour |
|------|-----------|
| No `segments.json` (pre-Phase-A meeting) | Panel hidden; transcript sent as-is |
| Segments without `speaker` (no diarization) | Panel hidden |
| Directory empty | Muted hint, no rows |
| Bound `person_id` deleted from directory | Restore → «— не выбрано —» |
| User edited transcript in main textbox | Edits preserved (D-4 prefix replace) |
| `speakers.json` write fails | Logged warning, extraction not blocked |
| Friendly label `Спикер 1` vs `Спикер 11` | Bracketed-token replace avoids collision |

## Tests

Pure + source-text only — **no importing `ui.app`/customtkinter** on Linux CI
(`feedback_ui_app_import_breaks_linux_ci`):

- `tests/test_transcript_format.py` (extend): `apply_speaker_names` —
  bound subset replaced; unbound stay `Спикер N`; `Спикер 1` doesn't touch
  `Спикер 11`; empty map = identity; name containing brackets is inert.
- `tests/test_utils_*`: `load_segments` (missing → `[]`, corrupt → `[]`,
  valid → list); `save_speakers(..., speaker_map=…)` writes the map, default
  `None` → `{}`.
- `tests/test_dialog_*` (source-text): the Extract dialog file references
  `load_segments`, `apply_speaker_names`, `save_speakers(` with `speaker_map=`,
  and `_build_speaker_map`.

Baseline before PR-2 = 521 tests (per `project-directory-voice-id`); PR-2 adds
~10–13. `pytest` green + `ruff check .` clean before every commit.

## Touched files

| File | Change |
|------|--------|
| `transcript_format.py` | + `apply_speaker_names` |
| `utils.py` | + `load_segments`; extend `save_speakers` with optional `speaker_map` |
| `ui/dialogs/extract_tasks/__init__.py` | «Кто говорит» rows; auto-sync; restore; build `name_by_label`+`speaker_map`; rewrite transcript; `save_speakers(..., speaker_map=)` |
| `tests/` | new/extended suites above |

No changes to `tasks/protocol_generator.py`, `tasks/extractor.py`,
`directory/`, or `requirements.txt`.

## Out of scope

- Inline "create new person" from the dropdown (D-3) — use «Справочники».
- Voice-ID auto-fill (Part B / B1) — pre-fills the *same* panel later; PR-2's
  manual rows are the substrate it will populate.
- Rewriting the main-window transcript textbox display — substitution is
  applied only to the copy sent to the LLM.

## Workflow notes

- Branch `feat/speaker-attribution` off **updated `main`** (PR #86 already in).
- Untracked `cli/` + `tests/test_cli_import_guard.py` in the working tree are
  unrelated WIP — do not include them in this PR.
- Subagent-driven execution on the default model (haiku override overflows on
  big prompts — `feedback-subagent-dispatch-blocked-by-mcp-overhead`).
