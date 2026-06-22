# transcript.md as an Obsidian/GBrain Graph Citizen — Design

**Date:** 2026-06-22
**Status:** Approved (brainstorming) — ready for implementation plan
**Topic:** Wire VoxNote meeting notes into the Obsidian knowledge graph via inline
`[[wikilinks]]` to project + people, so backlinks and GBrain pick them up
automatically.

## 1. Why (strategic context)

VoxNote is the audio-input surface of the user's Hermes-native Mini-AGI (see the
`voxnote-in-mini-agi` memory). The transcription queue (A→C, PRs #153–#162) is
complete: record / pick / inbox → serial worker → diarized `transcript.md` into the
shared Obsidian vault → best-effort Hermes nudge. The next strategic deepening,
chosen over Voice-ID / Drive-restore / UX-polish, is **"Hermes integration deeper."**

Grounding the abstract "deeper" against the real code surfaced a concrete gap: the
event contract (`integrations/hermes/schema.py`, `audio.transcribed` v1.1) and the
note frontmatter are already mature, but the meeting notes **do not wire into the
Obsidian/GBrain graph** — `project` and `participants` are plain text, so there are
no backlinks `meeting ↔ project ↔ person` and no tags. The user's governing vault
rule (from `15 Dossiers/Dossier Index.md`) is *"Markdown канонический; GBrain
индексирует смысл из `.md`"* — the graph is fed by inline `[[links]]` in the note
body, exactly how dossiers link related entities.

This design makes each `transcript.md` a first-class graph citizen.

## 2. Current state (grounded)

- **`processing/vault_note.py`** `render_transcript_note(...)` writes YAML
  frontmatter (`type, date, time, project, participants, provider, language,
  voxnote_id, source_path, nudged`) + a diarized body from
  `transcript_format.format_diarized_markdown`. `participants` is a `list[str]`.
- **`processing/worker.py`** `_process_item` calls `render_transcript_note` with
  **`participants=[]` hardcoded** (worker.py:279) and `project_name=getattr(project,
  "name", None)`. The worker is deliberately decoupled from the directory store: it
  resolves the project through an **injected** `resolve_project` callback
  (worker.py:57), not a store reference.
- **`directory/`** — `Person` has `full_name`, `role`, `project_ids` (the relation
  owner: a person references the projects they belong to). `Project` has `name`.
  `DirectoryStore` (`directory/store.py`) is the in-memory people/projects store.
- **Vault conventions** (read from the live vault):
  - People dossiers: `15 Dossiers/People/<full_name>.md`, frontmatter `type:
    dossier/person` + `tags: [dossier, person]`; entity relations linked **inline in
    the body** (`## Связанные проекты` → `- [[ ]]`). Obsidian resolves `[[Имя]]` by
    **filename**.
  - Project notes: `10 Projects/<name>.md`, often no frontmatter; the filename is the
    link target → `[[AI Auditor]]`.
  - Meetings dir is config-driven (`meetings_dir`); the legacy April-era folder is
    `40 Meetings/Raw Transcripts/`. There is **no precedent** for `[[wikilinks]]`
    inside frontmatter anywhere in the vault.

## 3. Decision (approach A — inline links + tags)

Graph edges are carried by **inline `[[links]]` in the note body**, in a new
`## Связи` section — matching the dossier convention and what GBrain indexes.
Frontmatter gains `tags: [meeting]` (the vault's tag convention) and keeps
`project`/`participants` as plain (now YAML-quoted) metadata.

Rejected alternatives:
- **B — frontmatter wikilinks** (`project: "[[…]]"`): no precedent in the vault,
  GBrain/Dataview may read frontmatter as plain strings, YAML-quoting edge cases,
  version-dependent graph behavior.
- **C — both**: redundant render logic + tests for no compatibility the vault needs.

**People bridge:** speakers are unnamed today (`Speaker 0/1`, no `speaker_map`),
so the only pre-Voice-ID source of people is the **directory roster** — the people
whose `project_ids` include this meeting's project. Roster ≈ participants is an
accepted presumption (the roster is "who is associated with this project," not
verified attendance); Voice-ID later refines the source, and the `## Связи` section
is reused unchanged.

## 4. Output format

Rendered `transcript.md` for a project meeting with a two-person roster:

```markdown
---
type: meeting
tags: [meeting]
date: 2026-06-22
time: "14:30"
project: AI Auditor
participants: ["Алмас Нурлан", "Данияр Сатыбалды"]
provider: AssemblyAI
language: ru
voxnote_id: 20260622-143000-123456_meeting.m4a
source_path: "G:/Drive/.../sources/meeting.m4a"
nudged: true
---

## Связи

- **Проект:** [[AI Auditor]]
- **Участники:** [[Алмас Нурлан]], [[Данияр Сатыбалды]]

**Спикер 1:** ...
**Спикер 2:** ...
```

- `tags: [meeting]` is inserted immediately after `type: meeting`.
- `participants` frontmatter values are YAML-quoted (via the existing `_yaml_str`)
  so names containing `:` or `,` cannot break the list.
- The `## Связи` section is inserted between the frontmatter and the diarized body,
  preceded by one blank line.

## 5. Architecture / files

| File | Change |
|---|---|
| `processing/vault_note.py` | Add pure helper `_render_relations(project_name, participants) -> str`. `render_transcript_note` inserts `tags: [meeting]`, YAML-quotes `participants`, and splices the relations section before the body. |
| `directory/store.py` | Add read helper `people_for_project(project_id) -> list[Person]`. |
| `processing/worker.py` | `__init__` gains injected `resolve_participants: Callable[[str \| None], list[str]] \| None = None` (defaults to a `[]`-returning lambda). `_process_item` calls it instead of the hardcoded `participants=[]`. |
| `ui/app/__init__.py` (ProcessingQueue construction site) | Wire `resolve_participants=lambda pid: [p.full_name for p in self._dir_store.people_for_project(pid)]`. |

### 5.1 `_render_relations` (pure)

```python
_WIKILINK_ILLEGAL = str.maketrans({c: " " for c in "[]|#^"})

def _wikilink_safe(name: str) -> str:
    """Strip characters that would break an Obsidian [[wikilink]] and collapse
    whitespace. Returns '' when nothing usable remains."""
    return " ".join(name.translate(_WIKILINK_ILLEGAL).split())

def _render_relations(project_name: str | None, participants: list[str]) -> str:
    """Inline '## Связи' section linking the project and roster people. Returns ''
    when there is neither a project nor any participant (caller omits the section)."""
    lines: list[str] = []
    proj = _wikilink_safe(project_name or "")
    if proj:
        lines.append(f"- **Проект:** [[{proj}]]")
    people = [s for s in (_wikilink_safe(p) for p in participants) if s]
    if people:
        joined = ", ".join(f"[[{p}]]" for p in people)
        lines.append(f"- **Участники:** {joined}")
    if not lines:
        return ""
    return "\n## Связи\n\n" + "\n".join(lines) + "\n\n"
```

`render_transcript_note` assembly becomes
`"\n".join(frontmatter) + _render_relations(project_name, participants) + body + "\n"`,
where `frontmatter` ends with `["---", ""]` (joining to `...---\n`). When relations
is non-empty it begins with `\n`, yielding `---\n\n## Связи …`; when empty the body
follows the frontmatter exactly as today.

### 5.2 `people_for_project` (pure read)

```python
def people_for_project(self, project_id: str | None) -> list[Person]:
    """People whose project_ids include project_id, sorted by full_name for a
    stable note. Empty list for a falsy or unknown id."""
    if not project_id:
        return []
    return sorted(
        (p for p in self._people.values() if project_id in p.project_ids),
        key=lambda p: p.full_name,
    )
```

### 5.3 Worker injection

```python
# __init__
self._resolve_participants = resolve_participants or (lambda _pid: [])

# _process_item, replacing participants=[]
participants=self._resolve_participants(item.project_id),
```

The `utils.save_speakers(folder, project_id, [], {})` call is unrelated (the
diarized-speaker list for «Извлечь задачи» compat) and stays as-is.

## 6. Data flow

`item.project_id` → `resolve_participants(project_id)` (injected in the worker) →
`DirectoryStore.people_for_project` → `[full_name, …]` →
`render_transcript_note(participants=…, project_name=…)` → `_render_relations`
builds the section → `write_transcript_note` writes into the vault.

## 7. Edge cases

- **No project** (inbox / «Без проекта»): `project_name` falsy and roster empty →
  `_render_relations` returns `""` → no `## Связи` heading rendered; `tags: [meeting]`
  still present.
- **Project, empty roster**: only the `**Проект:**` line renders.
- **Names with YAML specials** (`:`, `,`): quoted in frontmatter via `_yaml_str`.
- **Names with illegal wikilink chars** (`[ ] | # ^`): stripped by `_wikilink_safe`;
  a name reduced to empty is skipped.
- **Un-dossiered people**: `[[Имя]]` resolves to nothing → Obsidian renders an
  unresolved-link stub that still appears in the graph. This is desired (a worklist
  of people to write up), not an error.

## 8. Testing

All touched logic is Tk-free and torch-free → real unit tests (no source-slice
needed).

- `tests/test_processing_vault_note.py`:
  - project + roster → body contains `## Связи`, `[[<project>]]`, and each
    `[[<person>]]`;
  - no project, empty roster → no `## Связи`, no `**Проект:**`;
  - project, empty roster → `**Проект:**` present, no `**Участники:**`;
  - `tags: [meeting]` present in frontmatter;
  - participant names YAML-quoted in frontmatter;
  - illegal-wikilink char in a name is stripped in the section.
- `tests/test_directory_store.py`:
  - `people_for_project` returns members of a project, sorted by `full_name`;
  - falsy / unknown id → `[]`.
- `tests/test_processing_worker.py`:
  - characterization: a fake `resolve_participants` is threaded into the rendered
    note (replaces the hardcoded `[]`);
  - default `resolve_participants=None` → `[]` (backward compatibility for existing
    construction sites/tests).

Baseline ≈ 1073 tests; this adds unit tests only.

## 9. Out of scope (YAGNI)

- **The `audio.transcribed` event payload is not changed.** The chosen gap is the
  vault graph, the durable handoff is the vault note, and the event already carries
  `project: {id, name}`. Mirroring `participants` into the event is a possible later
  follow-up, not this PR.
- **No vault-existence validation** of link targets (stubs are desired).
- **Voice-ID is out of scope.** `participants` is the project roster, not verified
  speakers; the section is reused when Voice-ID lands.

## 10. Global constraints (repo invariants)

- Cloud-only: no local CUDA / pyannote / faster-whisper / ctranslate2 / torch
  imports anywhere (invariant #2). This feature touches only pure-Python rendering
  and an in-memory dict read — none introduced.
- All file I/O passes `encoding="utf-8"` (the vault note already does; tests must
  too).
- Narrow `except` only; no new broad `except Exception` (the ratchet guard enforces
  this).
- Russian UI strings (`## Связи`, `**Проект:**`, `**Участники:**`), English code /
  comments / commits / spec.
- Do not bump `requirements.txt` pins (invariant #3); none needed.
- One concern per PR; feature branch + push + PR; the user merges.

## 11. Decisions locked

- Section: a single `## Связи` with two bullet lines (`Проект`, `Участники`).
- Participants: one comma-separated line of `[[links]]`.
- Event-parity: deferred (section 9).
- People bridge: directory roster via `people_for_project`, accepted as
  roster ≈ participants until Voice-ID.
