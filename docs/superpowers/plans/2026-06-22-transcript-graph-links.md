# transcript.md Graph-Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each VoxNote `transcript.md` a first-class Obsidian/GBrain graph node — an inline `## Связи` section linking the project + directory roster as `[[wikilinks]]`, plus `tags: [meeting]`.

**Architecture:** Pure-render change in `processing/vault_note.py` (a `_render_relations` helper + frontmatter tweaks), a read helper `DirectoryStore.people_for_project`, an injected `resolve_participants` callback on `ProcessingQueue` (mirroring the existing `resolve_project` injection so the worker stays decoupled from the store), and a one-line App wiring. Roster ≈ participants is the bridge until Voice-ID.

**Tech Stack:** Python stdlib, `pytest`, `ruff`. Touched logic is Tk-free + torch-free → real unit tests; the App-wiring test is **source-slice** (read the module text and assert on it — importing `ui.app` pulls `sounddevice`/PortAudio and crashes Linux CI).

**Source of truth:** `docs/superpowers/specs/2026-06-22-transcript-graph-links-design.md`.

## Global Constraints

- Cloud-only: NO local CUDA / pyannote / faster-whisper / ctranslate2 / torch imports anywhere (invariant #2). None needed here.
- `encoding="utf-8"` on every text read/write (vault note + tests).
- Narrow `except` only; add ZERO new `except Exception` (the broad-except ratchet guard enforces this).
- Russian user-facing strings (`## Связи`, `**Проект:**`, `**Участники:**`); English code / comments / commit messages.
- Do NOT bump `requirements.txt` pins (invariant #3); none needed.
- Before every commit: `py -3 -m pytest -q` green (baseline ≈ 1073 tests, 2 skipped) AND `py -3 -m ruff check .` clean.
- One concern per PR; branch `feat/transcript-graph-links` (already created, spec committed at `289dae9`); the user merges.
- Commit messages: lowercase-scoped, ending with the trailer
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
  On Windows PowerShell, embedded `"` mangles native-exe args — commit via
  `git commit -F` from a gitignored file (e.g. `.cache/msg.txt`) when the message
  has quotes; the Bash tool (Git Bash) can use a quoted heredoc instead.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `directory/store.py` | people/projects store | + `people_for_project(project_id) -> list[Person]` read helper |
| `processing/vault_note.py` | the ONLY vault writer (transcript.md render + write) | + `_wikilink_safe` / `_render_relations`; `render_transcript_note` adds `tags: [meeting]`, YAML-quotes `participants`, splices the `## Связи` section |
| `processing/worker.py` | serial queue worker | `__init__` gains injected `resolve_participants` (default `[]`); `_process_item` feeds it to the renderer |
| `ui/app/__init__.py` | App shell / `ProcessingQueue` construction | wire `resolve_participants` to `DirectoryStore.people_for_project` |

Build order: Task 1 (store) → Task 2 (render) → Task 3 (worker, asserts on Task 2's output) → Task 4 (App, needs Tasks 1 + 3).

---

### Task 1: `DirectoryStore.people_for_project`

**Files:**
- Modify: `directory/store.py` (add a read method after `get_project`, ~line 73)
- Test: `tests/test_directory_store.py`

**Interfaces:**
- Produces: `DirectoryStore.people_for_project(project_id: str | None) -> list[Person]` — members whose `project_ids` include `project_id`, sorted by `full_name`; `[]` for a falsy or unknown id.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_directory_store.py` (helpers `_fresh`, `Person`, `Project` are already imported at the top of that file):

```python
def test_people_for_project_returns_sorted_members(tmp_path):
    s = _fresh(tmp_path)
    pr = Project(name="Alpha")
    s.upsert_project(pr)
    s.upsert_person(Person(full_name="Данияр", project_ids=[pr.id]))
    s.upsert_person(Person(full_name="Алмас", project_ids=[pr.id]))
    s.upsert_person(Person(full_name="Чужой", project_ids=[]))
    names = [p.full_name for p in s.people_for_project(pr.id)]
    assert names == ["Алмас", "Данияр"]  # sorted by full_name; non-member excluded


def test_people_for_project_empty_for_falsy_or_unknown(tmp_path):
    s = _fresh(tmp_path)
    s.upsert_person(Person(full_name="A", project_ids=["p1"]))
    assert s.people_for_project(None) == []
    assert s.people_for_project("") == []
    assert s.people_for_project("nope") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_directory_store.py::test_people_for_project_returns_sorted_members tests/test_directory_store.py::test_people_for_project_empty_for_falsy_or_unknown -q`
Expected: FAIL — `AttributeError: 'DirectoryStore' object has no attribute 'people_for_project'`.

- [ ] **Step 3: Implement the read helper**

In `directory/store.py`, in the `# ── reads ──` block, immediately after `get_project` (line 73):

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_directory_store.py -q`
Expected: PASS (all directory-store tests green).

- [ ] **Step 5: Lint + commit**

```bash
py -3 -m ruff check directory/store.py tests/test_directory_store.py
git add directory/store.py tests/test_directory_store.py
git commit -m "feat(directory): people_for_project roster read helper

Members whose project_ids include the given project, sorted by full_name.
Feeds the transcript.md graph-wiring roster (spec 2026-06-22).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `vault_note` — tags + «Связи» section + YAML-safe participants

**Files:**
- Modify: `processing/vault_note.py` (add `_WIKILINK_ILLEGAL` / `_wikilink_safe` / `_render_relations`; edit `render_transcript_note`)
- Test: `tests/test_processing_vault_note.py`

**Interfaces:**
- Consumes: `format_diarized_markdown` (already imported), `_yaml_str` (already in the file).
- Produces: `render_transcript_note(...)` (signature unchanged) now emits `tags: [meeting]` after `type: meeting`, YAML-quotes each `participants` entry, and splices an inline `## Связи` section (project + participants as `[[wikilinks]]`) between the frontmatter and the diarized body. `_render_relations(project_name: str | None, participants: list[str]) -> str` returns `""` when there is neither a project nor any participant.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_processing_vault_note.py` (`from processing import vault_note` is already at the top):

```python
def test_render_adds_meeting_tag_and_relations_section():
    md = vault_note.render_transcript_note(
        segments=[{"start": 0, "end": 1, "text": "привет", "speaker": "SPEAKER_00"}],
        title="call", project_name="AI Auditor", date="2026-06-22", time="10:00",
        participants=["Алмас Нурлан", "Данияр Сатыбалды"],
        provider="AssemblyAI", language="ru",
        voxnote_id="vid1", source_path=None, nudged=True,
    )
    assert "tags: [meeting]" in md
    assert 'participants: ["Алмас Нурлан", "Данияр Сатыбалды"]' in md
    assert "## Связи" in md
    assert "- **Проект:** [[AI Auditor]]" in md
    assert "- **Участники:** [[Алмас Нурлан]], [[Данияр Сатыбалды]]" in md
    # the section sits between the frontmatter and the diarized body
    assert md.index("## Связи") < md.index("**Спикер 1:** привет")


def test_render_no_project_no_participants_omits_relations():
    md = vault_note.render_transcript_note(
        segments=[], title="x", project_name=None, date="2026-06-22", time="09:00",
        participants=[], provider="Deepgram", language=None,
        voxnote_id="v", source_path=None, nudged=False,
    )
    assert "## Связи" not in md
    assert "tags: [meeting]" in md   # the tag is unconditional
    assert "participants: []" in md


def test_render_project_only_when_roster_empty():
    md = vault_note.render_transcript_note(
        segments=[], title="x", project_name="Alpha", date="2026-06-22", time="09:00",
        participants=[], provider="Deepgram", language=None,
        voxnote_id="v", source_path=None, nudged=False,
    )
    assert "- **Проект:** [[Alpha]]" in md
    assert "**Участники:**" not in md


def test_render_strips_illegal_wikilink_chars():
    md = vault_note.render_transcript_note(
        segments=[], title="x", project_name="План#1", date="2026-06-22", time="09:00",
        participants=["Иван|Петров"], provider="Deepgram", language=None,
        voxnote_id="v", source_path=None, nudged=False,
    )
    assert "[[План 1]]" in md      # '#' -> space, collapsed
    assert "[[Иван Петров]]" in md  # '|' -> space, collapsed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_processing_vault_note.py -q`
Expected: FAIL — the four new tests fail (`## Связи` / `tags: [meeting]` not in output); the five existing tests still pass.

- [ ] **Step 3: Implement the helpers + render changes**

In `processing/vault_note.py`, add the module constant + two pure helpers above `render_transcript_note` (after the existing `_yaml_str` function):

```python
_WIKILINK_ILLEGAL = str.maketrans({c: " " for c in "[]|#^"})


def _wikilink_safe(name: str) -> str:
    """Strip characters that would break an Obsidian [[wikilink]] and collapse
    whitespace. Returns '' when nothing usable remains."""
    return " ".join(name.translate(_WIKILINK_ILLEGAL).split())


def _render_relations(project_name: str | None, participants: list[str]) -> str:
    """Inline '## Связи' section linking the project + roster people as
    [[wikilinks]] (the Obsidian graph + GBrain are fed by inline links, not
    frontmatter). Returns '' when there is neither a project nor any participant,
    so the caller omits the whole section."""
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

Then edit `render_transcript_note`. Add the `tags` line after `"type: meeting",`, switch the participants line to YAML-quote each name, and splice the relations section into the return:

```python
    frontmatter = [
        "---",
        "type: meeting",
        "tags: [meeting]",
        f"date: {date}",
        f"time: {_yaml_str(time)}",
        f"project: {project_name or ''}",
        f"participants: [{', '.join(_yaml_str(p) for p in participants)}]",
        f"provider: {provider}",
        f"language: {language or ''}",
        f"voxnote_id: {voxnote_id}",
        sp_line,
        f"nudged: {'true' if nudged else 'false'}",
        "---",
        "",
    ]
    body = format_diarized_markdown(segments, speaker_map)
    return (
        "\n".join(frontmatter)
        + _render_relations(project_name, participants)
        + body
        + "\n"
    )
```

(The frontmatter list ends with `"---", ""`, so it joins to `…---\n`. When the relations string is non-empty it begins with `\n`, yielding `---\n\n## Связи …`; when empty the body follows the frontmatter exactly as before.)

- [ ] **Step 4: Run the tests to verify they pass (incl. no regressions)**

Run: `py -3 -m pytest tests/test_processing_vault_note.py tests/test_processing_worker.py -q`
Expected: PASS — new vault-note tests green; the existing vault-note + worker tests stay green (their assertions are substring-based, so the now-present `## Связи` project line does not break them).

- [ ] **Step 5: Lint + commit**

```bash
py -3 -m ruff check processing/vault_note.py tests/test_processing_vault_note.py
git add processing/vault_note.py tests/test_processing_vault_note.py
git commit -m "feat(vault): inline graph links + tags in transcript.md

render_transcript_note now emits tags:[meeting], YAML-quotes participants,
and splices an inline '## Связи' section linking the project + roster as
[[wikilinks]] so meetings wire into the Obsidian/GBrain graph. Pure render
change; empty when no project and no participants (spec 2026-06-22).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `ProcessingQueue` — injected `resolve_participants`

**Files:**
- Modify: `processing/worker.py` (`__init__` signature + body; the `render_transcript_note(...)` call in `_process_item`)
- Test: `tests/test_processing_worker.py`

**Interfaces:**
- Consumes: `vault_note.render_transcript_note(..., participants=...)` (Task 2).
- Produces: `ProcessingQueue.__init__` gains keyword-only `resolve_participants: Callable[[str | None], list[str]] | None = None`, defaulting to a `[]`-returning lambda; `_process_item` passes `participants=self._resolve_participants(item.project_id)` to the renderer. The injected callback (wired in Task 4) is the only thing the worker knows about the directory — the store stays out of this module.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_processing_worker.py` (helpers `_queue`, `_patch_happy`, `_sandbox_home`, `_audio`, and `Project`, `StageStatus`, `os` are already in the file):

```python
def test_process_item_links_roster_participants(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    proj = Project(name="AI Auditor", id="p1")
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        resolve_project=lambda pid: proj if pid == "p1" else None,
        resolve_participants=lambda pid: (
            ["Алмас Нурлан", "Данияр Сатыбалды"] if pid == "p1" else []
        ),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(_audio(tmp_path), {"provider": "AssemblyAI", "project_id": "p1"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        note = f.read()
    assert "## Связи" in note
    assert "- **Проект:** [[AI Auditor]]" in note
    assert "[[Алмас Нурлан]], [[Данияр Сатыбалды]]" in note
    assert 'participants: ["Алмас Нурлан", "Данияр Сатыбалды"]' in note


def test_process_item_defaults_to_no_participants(tmp_path, monkeypatch):
    """Without an injected resolve_participants the worker renders an empty roster
    — backward compatibility for existing construction sites."""
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    q = _queue(  # _queue() does NOT pass resolve_participants → default applies
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(_audio(tmp_path), {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        note = f.read()
    assert "participants: []" in note
    assert "**Участники:**" not in note
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_processing_worker.py::test_process_item_links_roster_participants -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'resolve_participants'`.

- [ ] **Step 3: Implement the injection**

In `processing/worker.py`, add the parameter to `ProcessingQueue.__init__` (keyword-only block), immediately after `resolve_project`:

```python
        resolve_project: Callable[[str | None], object | None],
        resolve_participants: Callable[[str | None], list[str]] | None = None,
        queue_path: str | None = None,
```

Store it in the body, immediately after `self._resolve_project = resolve_project`:

```python
        self._resolve_participants = resolve_participants or (lambda _pid: [])
```

In `_process_item`, change the renderer call's `participants=[],` line to:

```python
                participants=self._resolve_participants(item.project_id),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_processing_worker.py -q`
Expected: PASS (the two new tests + all existing worker tests).

- [ ] **Step 5: Lint + commit**

```bash
py -3 -m ruff check processing/worker.py tests/test_processing_worker.py
git add processing/worker.py tests/test_processing_worker.py
git commit -m "feat(queue): feed roster participants into transcript.md

ProcessingQueue gains an injected resolve_participants callback (default
empty), mirroring resolve_project so the worker stays decoupled from the
directory store; _process_item threads the roster into the note renderer
(spec 2026-06-22).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: App wiring — roster from the directory store

**Files:**
- Modify: `ui/app/__init__.py:227-234` (the `ProcessingQueue(...)` construction)
- Test: `tests/test_ui_queue_wiring.py` (source-slice — `_INIT` is already defined at the top as the module text)

**Interfaces:**
- Consumes: `ProcessingQueue(resolve_participants=...)` (Task 3), `DirectoryStore.people_for_project` (Task 1), `self._dir_store` (already built in `__init__`).
- Produces: the App injects `resolve_participants=lambda pid: [p.full_name for p in self._dir_store.people_for_project(pid)]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_queue_wiring.py`:

```python
def test_app_wires_resolve_participants_from_directory():
    assert "resolve_participants=" in _INIT
    assert "people_for_project(" in _INIT
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `py -3 -m pytest tests/test_ui_queue_wiring.py::test_app_wires_resolve_participants_from_directory -q`
Expected: FAIL — `assert "resolve_participants=" in _INIT`.

- [ ] **Step 3: Wire the callback**

In `ui/app/__init__.py`, edit the `ProcessingQueue(...)` construction to add the `resolve_participants` argument after `resolve_project`:

```python
        self._queue = ProcessingQueue(
            meetings_dir=get_meetings_dir(),
            config_loader=load_config,
            resolve_project=lambda pid: (
                self._dir_store.get_project(pid) if pid else None
            ),
            resolve_participants=lambda pid: [
                p.full_name for p in self._dir_store.people_for_project(pid)
            ],
            on_change=self._safe_after_refresh,
        )
```

(`people_for_project` already returns `[]` for a falsy `pid`, so no `if pid` guard is needed.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `py -3 -m pytest tests/test_ui_queue_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + lint + commit**

```bash
py -3 -m pytest -q
py -3 -m ruff check .
git add ui/app/__init__.py tests/test_ui_queue_wiring.py
git commit -m "feat(ui): wire meeting roster from the directory store

App injects resolve_participants → DirectoryStore.people_for_project so the
queue renders [[person]] graph links in transcript.md (spec 2026-06-22).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Plan Self-Review

**1. Spec coverage:**
- §3 inline links + tags → Task 2 (`_render_relations`, `tags: [meeting]`). ✓
- §3 roster bridge → Task 1 (`people_for_project`) + Task 3 (injection) + Task 4 (wiring). ✓
- §4 output format (tag placement, YAML-quoted participants, section between frontmatter and body) → Task 2 tests assert all three. ✓
- §7 edge cases: no project → `test_render_no_project_no_participants_omits_relations`; empty roster → `test_render_project_only_when_roster_empty`; illegal wikilink chars → `test_render_strips_illegal_wikilink_chars`; YAML-safety → `'participants: ["…", "…"]'` assertion; dangling links are desired (no test needed — they are just `[[name]]` strings). ✓
- §8 testing surface → Tasks 1–4 map 1:1 to the spec's three test files + the source-slice wiring test. ✓
- §9 out of scope: the `audio.transcribed` event payload is untouched — no task modifies `integrations/hermes/`. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every run step has an exact command + expected result. ✓

**3. Type consistency:** `people_for_project(project_id: str | None) -> list[Person]` (Task 1) is consumed by the App lambda as `[p.full_name for p in …]` (Task 4). `resolve_participants: Callable[[str | None], list[str]] | None` (Task 3) is fed `item.project_id` and returns `list[str]`, matching `render_transcript_note`'s `participants: list[str]` param (Task 2). `_render_relations(project_name: str | None, participants: list[str]) -> str` matches its call site. ✓
