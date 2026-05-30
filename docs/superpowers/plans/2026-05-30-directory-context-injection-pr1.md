# Directory Context Injection (PR-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user pick a meeting's project + participants in the «Извлечь задачи» dialog and inject that directory context into the protocol-generation and task-extraction LLM prompts.

**Architecture:** The backend already accepts context (`protocol_generator.generate(..., context=)`, `extractor.extract(..., context=)`, `directory.context.render_meeting_context`). This PR adds a «Контекст встречи» section to the Extract dialog (project dropdown + project-driven participant checkboxes), threads the rendered context into both calls on the worker thread, fills `speakers=` with real ФИО, and persists the selection to `<meeting>/speakers.json` (forward-compatible `{speakers:{}}` slot for PR-2).

**Tech Stack:** Python 3.10+, CustomTkinter, pytest, ruff. Spec: `docs/superpowers/specs/2026-05-30-directory-context-injection-design.md`.

**Conventions (read before starting):**
- Russian UI strings, English code/comments.
- No naked hex in CTk kwargs — colours from `theme.py`; only `"#FFFFFF"`/`"transparent"` are sanctioned literals.
- UI tests use **source-text structural** checks (read the file as text, assert substrings) — CTk dialogs can't be instantiated on headless Linux CI (they import `ui`→`sounddevice`→PortAudio). Never `import ui.app...` in a test.
- Narrow `except` (e.g. `DirectoryError`, `OSError`), not bare `except Exception`.
- Before any commit: `python -m pytest -q` green + `python -m ruff check .` clean.
- When `git add`, stage files **by name** — never `git add -A`/`.` (untracked `cli/` and `tests/test_cli_import_guard.py` must NOT be staged).

---

### Task 1: Pure helper `default_participants`

The project→members default, kept beside `render_meeting_context` (both pure context-prep helpers). No CTk, fully unit-testable.

**Files:**
- Modify: `directory/context.py`
- Test: `tests/test_directory_context.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_directory_context.py`:

```python
from directory.context import default_participants
from directory.schema import Person


def test_default_participants_filters_by_project():
    a = Person(full_name="A", project_ids=["p1"])
    b = Person(full_name="B", project_ids=["p2"])
    c = Person(full_name="C", project_ids=["p1", "p2"])
    out = default_participants([a, b, c], "p1")
    assert [p.full_name for p in out] == ["A", "C"]


def test_default_participants_none_project_is_empty():
    a = Person(full_name="A", project_ids=["p1"])
    assert default_participants([a], None) == []


def test_default_participants_unknown_project_is_empty():
    a = Person(full_name="A", project_ids=["p1"])
    assert default_participants([a], "nope") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_directory_context.py -q`
Expected: FAIL — `ImportError: cannot import name 'default_participants'`.

- [ ] **Step 3: Write minimal implementation**

In `directory/context.py`, after `render_meeting_context`, add (the file already imports `Person, Project` from `directory.schema`):

```python
def default_participants(
    people: list[Person], project_id: str | None
) -> list[Person]:
    """People whose project_ids include project_id (preserving input order).

    Returns [] when project_id is None or matches no one — the dialog uses this
    to pre-check participant boxes when a project is chosen.
    """
    if not project_id:
        return []
    return [p for p in people if project_id in p.project_ids]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_directory_context.py -q`
Expected: PASS (all, including the pre-existing render tests).

- [ ] **Step 5: Commit**

```bash
git add directory/context.py tests/test_directory_context.py
git commit -m "feat(directory): default_participants — project→members helper"
```

---

### Task 2: `save_speakers` / `load_speakers` persistence

Atomic per-meeting persistence, mirroring `utils.save_segments`. Holds the forward-compatible `speakers.json` shape `{project_id, participants, speakers:{}}`.

**Files:**
- Modify: `utils.py` (add after `save_segments`, ~line 277)
- Test: `tests/test_utils_save_speakers.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_utils_save_speakers.py`:

```python
import json

from utils import load_speakers, save_speakers


def test_save_speakers_writes_forward_compatible_shape(tmp_path):
    save_speakers(str(tmp_path), "proj1", ["a", "b"])
    data = json.loads((tmp_path / "speakers.json").read_text(encoding="utf-8"))
    assert data == {"project_id": "proj1", "participants": ["a", "b"], "speakers": {}}


def test_save_speakers_null_project(tmp_path):
    save_speakers(str(tmp_path), None, ["a"])
    data = json.loads((tmp_path / "speakers.json").read_text(encoding="utf-8"))
    assert data["project_id"] is None


def test_load_speakers_roundtrip(tmp_path):
    save_speakers(str(tmp_path), "p", ["x"])
    assert load_speakers(str(tmp_path)) == {
        "project_id": "p", "participants": ["x"], "speakers": {},
    }


def test_load_speakers_missing_is_empty_dict(tmp_path):
    assert load_speakers(str(tmp_path)) == {}


def test_load_speakers_malformed_is_empty_dict(tmp_path):
    (tmp_path / "speakers.json").write_text("{not json", encoding="utf-8")
    assert load_speakers(str(tmp_path)) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_utils_save_speakers.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_speakers'`.

- [ ] **Step 3: Write minimal implementation**

In `utils.py`, immediately after `save_segments` (it already imports `json`, `os`), add:

```python
def save_speakers(
    folder: str, project_id: str | None, participant_ids: list[str]
) -> None:
    """Atomically write the meeting's context selection to <folder>/speakers.json.

    Shape is forward-compatible with PR-2's per-speaker attribution: the empty
    "speakers" map is the slot that {SPEAKER_00: person_id, ...} fills later.
    PR-1 only ever writes project_id + participants.
    """
    payload = {
        "project_id": project_id,
        "participants": list(participant_ids),
        "speakers": {},
    }
    target = os.path.join(folder, "speakers.json")
    tmp = os.path.join(folder, ".speakers.json.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(encoded)
    os.replace(tmp, target)


def load_speakers(folder: str) -> dict:
    """Read <folder>/speakers.json. Returns {} if absent or malformed.

    Never raises — the dialog restore path must degrade silently (a corrupt or
    missing file just means "no remembered selection").
    """
    target = os.path.join(folder, "speakers.json")
    try:
        with open(target, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_utils_save_speakers.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add utils.py tests/test_utils_save_speakers.py
git commit -m "feat(utils): save_speakers/load_speakers — meeting context persistence"
```

---

### Task 3: «Контекст встречи» UI section + directory load + restore

Add the project dropdown + participant checkboxes to the Extract dialog, load `DirectoryStore` in the constructor, and restore the selection from `speakers.json` on open. UI → source-text tests only.

**Files:**
- Modify: `ui/dialogs/extract_tasks/__init__.py` (constructor ~line 86-124; `_build_ui` ~line 287; new helper methods)
- Test: `tests/test_extract_dialog_context.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract_dialog_context.py`:

```python
from pathlib import Path

SRC = Path(__file__).parent.parent / "ui/dialogs/extract_tasks/__init__.py"


def test_dialog_loads_directory_store():
    src = SRC.read_text(encoding="utf-8")
    assert "from directory.store import" in src
    assert "DirectoryStore()" in src


def test_dialog_builds_context_section():
    src = SRC.read_text(encoding="utf-8")
    assert "Контекст встречи" in src
    assert "_context_project_var" in src
    assert "_context_person_vars" in src


def test_dialog_uses_default_participants():
    src = SRC.read_text(encoding="utf-8")
    assert "default_participants" in src


def test_dialog_restores_selection_from_speakers_json():
    src = SRC.read_text(encoding="utf-8")
    assert "load_speakers" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_dialog_context.py -q`
Expected: FAIL (assertions about `Контекст встречи` / `_context_project_var` etc. not found).

- [ ] **Step 3: Load the directory store in the constructor**

In `ui/dialogs/extract_tasks/__init__.py`, inside `__init__`, right after `self._config = config` (line ~91), add:

```python
        # Phase A UI part 2: directory for meeting-context grounding. Loaded
        # eagerly so the «Контекст встречи» section can populate; a corrupt
        # file degrades to an empty directory (warn once, never crash the
        # dialog).
        from directory.store import DirectoryError, DirectoryStore
        self._dir_store = DirectoryStore()
        try:
            self._dir_store.load()
        except DirectoryError as exc:
            messagebox.showwarning(
                "Справочник",
                f"Не удалось прочитать справочник — контекст недоступен.\n\n{exc}",
                parent=self,
            )
        self._context_project_var = ctk.StringVar(value="— нет —")
        self._context_person_vars: dict[str, ctk.BooleanVar] = {}
```

(`messagebox` and `ctk` are already imported at the top of the file.)

- [ ] **Step 4: Build the section in `_build_ui`**

In `_build_ui`, immediately AFTER the protocol-checkbox block that ends at line ~287 (`.grid(row=1, column=0, columnspan=8, ...)`) and BEFORE the `# --- Status / cost hint row ---` comment, insert:

```python
        # Phase A UI part 2: «Контекст встречи» — project + participants feed
        # render_meeting_context() → context= for both protocol and tasks.
        # Nested in `header` (row=2) so no self-level row renumbering is needed.
        ctx_frame = ctk.CTkFrame(header, fg_color="transparent")
        ctx_frame.grid(row=2, column=0, columnspan=8, padx=0, pady=(8, 0), sticky="ew")
        ctx_frame.grid_columnconfigure(1, weight=1)

        label(ctx_frame, "Проект").grid(row=0, column=0, padx=(0, 6), sticky="w")
        self._dir_projects = self._dir_store.projects()
        project_labels = ["— нет —"] + [p.name for p in self._dir_projects]
        self._context_project_menu = ctk.CTkComboBox(
            ctx_frame, variable=self._context_project_var, values=project_labels,
            width=240, height=30, state="readonly",
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            command=self._on_context_project_changed,
        )
        self._context_project_menu.grid(row=0, column=1, padx=(0, 12), sticky="ew")

        label(ctx_frame, "Участники").grid(
            row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="nw",
        )
        self._context_participants_frame = ctk.CTkScrollableFrame(
            ctx_frame, fg_color=INPUT_BG, height=90, corner_radius=8,
        )
        self._context_participants_frame.grid(
            row=1, column=1, padx=0, pady=(6, 0), sticky="ew",
        )
        self._rebuild_context_participants(set())
        self._restore_context_selection()
```

- [ ] **Step 5: Add the helper methods**

Add these methods to the dialog class (e.g. right after `_build_ui`):

```python
    def _rebuild_context_participants(self, checked_ids: set[str]) -> None:
        """Render a checkbox per directory person, ticking checked_ids."""
        for w in self._context_participants_frame.winfo_children():
            w.destroy()
        self._context_person_vars = {}
        people = self._dir_store.people()
        if not people:
            label(
                self._context_participants_frame,
                "(справочник пуст — добавьте людей в «Справочники»)",
            ).grid(row=0, column=0, padx=4, pady=2, sticky="w")
            return
        for i, p in enumerate(people):
            var = ctk.BooleanVar(value=p.id in checked_ids)
            self._context_person_vars[p.id] = var
            text = p.full_name + (f" — {p.role}" if p.role else "")
            ctk.CTkCheckBox(
                self._context_participants_frame, text=text, variable=var,
                fg_color=BLUE, hover_color=BLUE_DIM, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=12),
                checkbox_height=16, checkbox_width=16,
            ).grid(row=i, column=0, padx=4, pady=1, sticky="w")

    def _on_context_project_changed(self, _choice=None) -> None:
        """Project change → pre-check that project's members."""
        project = self._selected_context_project()
        pid = project.id if project else None
        from directory.context import default_participants
        defaults = {p.id for p in default_participants(self._dir_store.people(), pid)}
        self._rebuild_context_participants(defaults)

    def _selected_context_project(self):
        """Return the Project chosen in the dropdown, or None for «— нет —»."""
        chosen = self._context_project_var.get()
        for p in self._dir_projects:
            if p.name == chosen:
                return p
        return None

    def _selected_context_people(self) -> list:
        """Resolve the ticked participant ids to Person objects (skip stale)."""
        out = []
        for pid, var in self._context_person_vars.items():
            if var.get():
                person = self._dir_store.get_person(pid)
                if person is not None:
                    out.append(person)
        return out

    def _restore_context_selection(self) -> None:
        """Re-apply a previously saved project + participants from speakers.json."""
        from utils import load_speakers
        data = load_speakers(self._history_folder)
        if not data:
            return
        project = self._dir_store.get_project(data.get("project_id") or "")
        if project is not None:
            self._context_project_var.set(project.name)
        checked = set(data.get("participants") or [])
        if checked:
            self._rebuild_context_participants(checked)
```

**Import note:** `label` is already imported from `ui.widgets` (line 41). `BORDER`, `INPUT_BG`, `TEXT_PRIMARY`, `TEXT_SECONDARY`, `FONT` are already in the `from theme import (...)` block (lines 30-40). `BLUE` and `BLUE_DIM` are **NOT** imported — add `BLUE,` and `BLUE_DIM,` to that block (used by the participant checkboxes, matching the «Справочники» dialog). Do not introduce a naked hex.

- [ ] **Step 6: Run the structural tests + lint**

Run: `python -m pytest tests/test_extract_dialog_context.py -q`
Expected: PASS (4 passed).
Run: `python -m ruff check ui/dialogs/extract_tasks/__init__.py`
Expected: `All checks passed!` (fix any unused-import / line-length issues inline).

- [ ] **Step 7: Commit**

```bash
git add ui/dialogs/extract_tasks/__init__.py tests/test_extract_dialog_context.py
git commit -m "feat(ui): «Контекст встречи» section in the Extract dialog"
```

---

### Task 4: Thread context into both LLM calls + write speakers.json

Capture the selection on the main thread, pass `context=`/`speakers=` to `extract()` and `generate()`, and persist via `save_speakers`.

**Files:**
- Modify: `ui/dialogs/extract_tasks/__init__.py` (`_on_extract` ~line 566; `_run_extraction` ~line 581, 612, 666, 668)
- Test: `tests/test_extract_dialog_context.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_extract_dialog_context.py`:

```python
def test_run_extraction_passes_context_to_both_calls():
    src = SRC.read_text(encoding="utf-8")
    # render once, thread into extract() and generate()
    assert "render_meeting_context(" in src
    assert src.count("context=meeting_context") >= 2


def test_protocol_speakers_uses_real_names():
    src = SRC.read_text(encoding="utf-8")
    assert "speakers=[p.full_name for p in people]" in src
    assert "speakers=[],  # cloud-only build has no voice library" not in src


def test_run_extraction_persists_speakers_json():
    src = SRC.read_text(encoding="utf-8")
    assert "save_speakers(" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_dialog_context.py -q`
Expected: FAIL (new assertions fail; the 4 from Task 3 still pass).

- [ ] **Step 3: Capture the selection in `_on_extract` and pass it to the worker**

In `_on_extract`, change the thread spawn (currently `args=(container, model, backend_name)`, line ~568) to capture the selection on the main thread first:

```python
        project = self._selected_context_project()
        people = self._selected_context_people()
        threading.Thread(
            target=self._run_extraction,
            args=(container, model, backend_name, project, people),
            daemon=True,
        ).start()
```

- [ ] **Step 4: Update `_run_extraction` signature + thread the context**

Change the signature (line ~581) to accept the new args and render the context:

```python
    def _run_extraction(
        self, container, model: str, backend_name: str, project, people: list,
    ) -> None:
        from directory.context import render_meeting_context
        from utils import save_speakers
        meeting_context = render_meeting_context(people, project) or None
```

(Add these two imports alongside the existing function-level imports at the top of `_run_extraction`.)

Pass `context=meeting_context` to `extract()` (line ~612):

```python
            result = extract(
                transcript=self._transcript,
                model=model,
                lang=self._transcript_lang,
                openrouter_client=openrouter,
                members=members,
                labels=labels,
                context=meeting_context,
            )
```

Update the `generate()` call (line ~666) — real ФИО for `speakers`, plus `context`:

```python
                    proto_result = protocol_generator.generate(
                        transcript=self._transcript,
                        speakers=[p.full_name for p in people],
                        meeting_date="",  # not tracked at dialog level in v1.0
                        lang=self._transcript_lang,
                        model=model,
                        openrouter_client=openrouter,
                        context=meeting_context,
                    )
```

- [ ] **Step 5: Persist speakers.json after a successful extract**

Immediately after `save_tasks_raw(self._history_folder, result["tasks"], meta)` (line ~636), add:

```python
            # Phase A UI part 2: remember the meeting's context selection so a
            # re-open restores it (and PR-2 can extend speakers.json with the
            # per-speaker map). Only write when something was selected; a write
            # failure must not block the committed task extraction.
            if project is not None or people:
                try:
                    save_speakers(
                        self._history_folder,
                        project.id if project else None,
                        [p.id for p in people],
                    )
                except OSError as exc:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "speakers.json write failed: %s", exc,
                    )
```

- [ ] **Step 6: Run the full gate**

Run: `python -m pytest tests/test_extract_dialog_context.py -q`
Expected: PASS (7 passed).
Run: `python -m pytest -q`
Expected: all green (baseline + the new tests).
Run: `python -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add ui/dialogs/extract_tasks/__init__.py tests/test_extract_dialog_context.py
git commit -m "feat(ui): inject meeting context into protocol + task prompts"
```

---

### Task 5: Manual GUI smoke (required — UI not unit-testable headless)

- [ ] **Run the app from the main repo (`python app.py`) and verify:**

1. Ensure the directory has ≥1 project and ≥2 people (one person in that project) via «Справочники».
2. Transcribe (or open from history) a meeting so a transcript + `history_folder` exist; click «Извлечь задачи».
3. The dialog shows a **«Контекст встречи»** block: a **Проект** dropdown + a scrollable **Участники** checkbox list.
4. Pick the project → its members auto-tick; untick one, tick another — boxes respond.
5. Click «Извлечь» (with «генерировать протокол» on). After it finishes, open `<meeting>/protocol.md` → confirm the `=== КОНТЕКСТ ВСТРЕЧИ ===` block with the project + real ФИО appears, and the participants section uses real names (not «Спикер N»).
6. Confirm `<meeting>/speakers.json` exists: `{project_id, participants:[…], speakers:{}}`.
7. Close + re-open «Извлечь задачи» on the same meeting → the project + participant ticks are **restored**.
8. Empty-directory / no-selection path: with nothing ticked and «— нет —», extract still works and `protocol.md` has **no** context block (backward compatible).

- [ ] **Commit (only if smoke surfaced fixes)**

```bash
git add ui/dialogs/extract_tasks/__init__.py
git commit -m "fix(ui): «Контекст встречи» smoke fixes"
```

(If smoke passed clean, nothing to commit.)

---

## Self-Review

**Spec coverage:**
- UI section (project + project-driven participants) → Task 3 ✓
- Glue: `context=` into `extract()` + `generate()`, `speakers=` real ФИО → Task 4 ✓
- Persistence `speakers.json` `{project_id, participants, speakers:{}}` + restore → Task 2 (helpers) + Task 3 (restore) + Task 4 (write) ✓
- Pure helper `default_participants` → Task 1 ✓
- Backward compat (`context=None` when empty) → Task 4 `render_meeting_context(...) or None` ✓
- `DirectoryError` warn / `OSError` on write non-fatal → Task 3 / Task 4 ✓
- Source-text dialog tests + pure unit tests → Tasks 1-4 ✓

**Type/name consistency:** `meeting_context` (the rendered string) is defined once in Task 4 Step 4 and referenced in Steps 4-5; `_selected_context_project` / `_selected_context_people` / `_rebuild_context_participants` / `_context_project_var` / `_context_person_vars` / `_dir_store` / `_dir_projects` are defined in Task 3 and consumed in Task 4. `save_speakers`/`load_speakers` signatures match between Task 2 (def) and Task 3/4 (calls). `default_participants` signature matches between Task 1 (def) and Task 3 (call).

**Placeholder scan:** none — every code step shows complete code.

**Note for implementer:** the `from theme import (...)` block (lines 30-40) currently has `BG, BORDER, FONT, GREEN, INPUT_BG, RED, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY`. Task 3 adds `BLUE` and `BLUE_DIM` (used by the participant checkboxes) — those are the only theme names this plan introduces. `label` comes from `ui.widgets` (line 41), already imported. Do not add a naked hex.
