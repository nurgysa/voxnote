# Meeting Tasks Pipeline — Phase 6.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the JSON textbox in `ExtractTasksDialog` with a master-detail editor: left list with checkboxes/rows, right form for editing the selected task. Auto-save changes to `tasks.json`. Add/Delete/SelectAll/SelectNone buttons. 5-step undo stack.

**Architecture:** Promote the in-memory task list (`result["tasks"]`) to a persistent dialog field (`self._tasks: list[Task]`). Selection state (`self._selected_index`) drives right-form variable bindings. Each user edit calls `_persist_current_task()` which writes back to the in-memory model AND saves `tasks.json`. Undo stack is a `collections.deque(maxlen=5)` of `copy.deepcopy(self._tasks)` snapshots taken before every destructive op (delete, add).

**Tech Stack:** Same as 6.1 — `customtkinter`, `tkinter`, `pytest`, stdlib `json`/`copy`/`collections`.

**Spec:** [docs/superpowers/specs/2026-04-28-meeting-tasks-pipeline-design.md](../specs/2026-04-28-meeting-tasks-pipeline-design.md) (Phasing → Phase 6.2; UI Design → Phase 6.2 master-detail; Persistence → tasks.json).

**Baseline (after Phase 6.1):**
- 121 tests passing (90 baseline + 30 new + 1 regression)
- `tasks/persistence.py` ships `save_tasks_raw`, `load_tasks_raw`
- `ui/dialogs/extract_tasks.py` ~449 lines: dialog with JSON textbox

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tasks/persistence.py` | Modify | Add `save_tasks(folder, tasks, meta)`, `load_tasks(folder)`, `MUTABLE_FILENAME = "tasks.json"`. tasks.json is the **mutable** superset (full Task fields + meta + edited_at). |
| `tests/test_tasks_persistence.py` | Modify | +5 tests for save_tasks/load_tasks (mirror existing test pattern) |
| `ui/dialogs/extract_tasks.py` | Modify | Replace `_json_box` with master-detail layout. Add ~400 lines of editor logic. |

**Why this split:** persistence extension is pure stdlib, fully testable. Dialog editor is UI-only (no unit tests in this project's pattern); rely on visual smoke + code review.

**Why all editor work in one file:** the spec ([Architecture, line 56](../specs/2026-04-28-meeting-tasks-pipeline-design.md)) explicitly keeps `extract_tasks.py` as one file across phases. Refactoring into multiple files at this phase would conflict with 6.3's send-button additions (also in same file). Defer file split to post-6.3 if needed.

---

## Decisions baked in

1. **Editor activates only after extract.** Opening the dialog with no `tasks.json` AND no fresh extract = empty right form + empty left list. After clicking Извлечь: tasks fill the list, first row auto-selected. After editing: tasks.json overwrites. (Re-opening from history with existing tasks.json is Phase 6.4 — but if `tasks.json` exists in `_history_folder` at dialog open, **we DO load it** since the user might have a half-edited file from a previous session crash.)

2. **In-memory model is canonical.** Right form binds to `self._tasks[self._selected_index]`. Edits update the in-memory `Task` immediately AND trigger `_save_tasks_to_disk()`. The on-disk `tasks.json` is the materialized projection.

3. **`save_tasks` writes the full `Task.to_dict()`** — no `_RAW_FIELDS` subset. The mutable file includes user-state fields (`selected`, `status`, `linear_*`, `send_error`). This is asymmetric from `tasks_raw.json` by design (audit trail vs editable state).

4. **Undo stack is `collections.deque(maxlen=5)`.** Pushes BEFORE every destructive op (delete, add). Ctrl+Z restores the last snapshot. Editing a single field is NOT undoable (would require diff per keystroke — overkill).

5. **Left list rows: custom CTkFrame widgets.** Each `_TaskRow(parent, task, on_select, on_toggle)` renders: checkbox + title + summary line (`👤 assignee · priority`). Selected row gets `BG_HOVER` background. The list is `CTkScrollableFrame` parent.

6. **Form variables: per-field StringVar/BooleanVar held on the dialog.** On selection change, `_bind_form_to(task)` reads task into vars. On any var change (`<<Modified>>`/`trace_add`), `_form_to_task()` writes vars back into the in-memory task and calls `_save_tasks_to_disk()`.

7. **"✗ Снять" disables nothing for 6.2** — that's a 6.3 thing for the Send button. For 6.2, the button just clears all checkboxes; downstream effect is "tasks marked as not-to-be-sent" which only matters in 6.3.

8. **Add/Delete granularity:** Add appends empty `Task(title="")` at end, selects it. Delete removes selected. Both push undo before mutating.

9. **Cancel-on-close still works.** All editor changes auto-save BEFORE close (so Закрыть = save what's selected, then teardown). The 6.1 cancel-event still gates worker `self.after` calls. No regression in cancel protocol.

---

## Task 1: Persistence extension

**Goal:** Add `save_tasks` (mutable, full-task) and `load_tasks` (Task-list reconstitution).

**Files:**
- Modify: `tasks/persistence.py` (+ ~50 lines)
- Modify: `tests/test_tasks_persistence.py` (+ ~80 lines)

- [ ] **Step 1.1: Write failing tests for `save_tasks` / `load_tasks`**

In `tests/test_tasks_persistence.py`, after the existing tests, add:

```python
# ── save_tasks / load_tasks ──────────────────────────────────────────


from tasks.persistence import (
    MUTABLE_FILENAME, load_tasks, save_tasks,
)
from tasks.schema import TaskStatus


def _full_state_tasks() -> list[Task]:
    return [
        Task(
            title="A", priority=Priority.HIGH, assignee_id="u1",
            assignee_name="Айдар", label_ids=["l1"], label_names=["bug"],
            selected=True, status=TaskStatus.SENT,
            linear_issue_id="ENG-101", linear_issue_url="https://linear.app/x/ENG-101",
        ),
        Task(
            title="B", description="multi\nline",
            selected=False, status=TaskStatus.SKIPPED,
        ),
    ]


def test_save_tasks_writes_full_state(tmp_path: Path):
    """tasks.json includes user-state fields (selected, status, linear_*) — unlike tasks_raw.json."""
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    data = json.loads((tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8"))
    sample = data["tasks"][0]
    # Full state present:
    assert sample["selected"] is True
    assert sample["status"] == "sent"
    assert sample["linear_issue_id"] == "ENG-101"
    assert sample["linear_issue_url"] == "https://linear.app/x/ENG-101"
    # Same meta keys as raw:
    assert data["model"] == "anthropic/claude-sonnet-4.5"
    assert data["team_id"] == "team-uuid"


def test_save_tasks_includes_edited_at_timestamp(tmp_path: Path):
    """tasks.json adds an `edited_at` field separate from `extracted_at`."""
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    data = json.loads((tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8"))
    assert "edited_at" in data
    assert isinstance(data["edited_at"], str)
    # Should be ISO-8601-ish:
    assert "T" in data["edited_at"]


def test_save_tasks_is_atomic(tmp_path: Path, monkeypatch):
    """Same atomic-write invariant as save_tasks_raw."""
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    original = (tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8")

    import tasks.persistence as P

    def boom(*args, **kwargs):
        raise RuntimeError("simulated mid-encode failure")

    monkeypatch.setattr(P.json, "dumps", boom)
    with pytest.raises(RuntimeError):
        save_tasks(str(tmp_path), [Task(title="X")], _sample_meta())

    # Original tasks.json untouched:
    assert (tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8") == original


def test_load_tasks_round_trips_full_state(tmp_path: Path):
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    loaded = load_tasks(str(tmp_path))
    assert loaded["model"] == "anthropic/claude-sonnet-4.5"
    out = loaded["tasks"]
    assert len(out) == 2
    assert out[0].selected is True
    assert out[0].status is TaskStatus.SENT
    assert out[0].linear_issue_id == "ENG-101"
    assert out[1].selected is False
    assert out[1].status is TaskStatus.SKIPPED


def test_load_tasks_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(PersistenceError, match="not found"):
        load_tasks(str(tmp_path))
```

- [ ] **Step 1.2: Run tests — confirm they fail with ImportError**

Run: `pytest tests/test_tasks_persistence.py -v`
Expected: All 5 new tests FAIL — `ImportError: cannot import name 'save_tasks'`.

- [ ] **Step 1.3: Implement `save_tasks` and `load_tasks` in `tasks/persistence.py`**

Add to `tasks/persistence.py` after the existing `load_tasks_raw` function:

```python
MUTABLE_FILENAME = "tasks.json"


def save_tasks(folder: str, tasks: list[Task], meta: dict) -> None:
    """Atomically write ``<folder>/tasks.json`` — the mutable user-state snapshot.

    Differs from ``save_tasks_raw`` in two ways:
    1. Persists the full ``Task.to_dict()`` (incl. selected/status/linear_*).
    2. Adds an ``edited_at`` timestamp separate from ``extracted_at``.

    ``meta`` keys: extracted_at, model, team_id, team_name, transcript_lang.
    """
    from datetime import datetime

    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        **meta,
        "edited_at": datetime.now().isoformat(timespec="seconds"),
        "tasks": [t.to_dict() for t in tasks],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)

    final = target_dir / MUTABLE_FILENAME
    tmp = target_dir / f".{MUTABLE_FILENAME}.tmp"
    try:
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, final)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PersistenceError(f"Не удалось записать {MUTABLE_FILENAME}: {e}") from e


def load_tasks(folder: str) -> dict:
    """Read ``<folder>/tasks.json`` and return ``{**meta, 'tasks': [Task, ...]}``.

    Raises PersistenceError if missing or malformed.
    """
    path = Path(folder) / MUTABLE_FILENAME
    if not path.is_file():
        raise PersistenceError(f"{MUTABLE_FILENAME} not found in {folder}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PersistenceError(f"{MUTABLE_FILENAME} malformed in {folder}: {e}") from e

    raw_tasks = data.pop("tasks", [])
    return {**data, "tasks": [Task.from_dict(t) for t in raw_tasks]}
```

- [ ] **Step 1.4: Run tests — verify all pass**

Run: `pytest tests/test_tasks_persistence.py -v`
Expected: 12 passed (7 existing + 5 new).

- [ ] **Step 1.5: Run full suite**

Run: `pytest tests/ -v`
Expected: 126 passed (121 + 5).

- [ ] **Step 1.6: Commit**

```bash
git add tasks/persistence.py tests/test_tasks_persistence.py
git commit -m "feat(persistence): save_tasks / load_tasks for mutable tasks.json + 5 tests"
```

---

## Task 2: Editor scaffold (replace JSON textbox with empty master-detail)

**Goal:** Replace `_json_box` with a split layout. No interactivity yet — just empty left list + empty right form. Verify static/visual that layout renders.

**Files:**
- Modify: `ui/dialogs/extract_tasks.py` (~80 line delta — remove ~10 lines of JSON box, add ~90 lines of editor scaffold)

- [ ] **Step 2.1: Promote in-memory model to dialog state**

In `ui/dialogs/extract_tasks.py`'s `__init__`, after `self._teams: list[dict] = []`, add:

```python
        # Editor state. _tasks is the canonical in-memory list; right form
        # binds to _tasks[_selected_index]. _meta carries extract context for
        # save_tasks (extracted_at, model, team_id, team_name, transcript_lang).
        self._tasks: list = []      # list[Task]; populated post-extract or post-load
        self._selected_index: Optional[int] = None
        self._meta: dict = {}       # populated post-extract or post-load
        # Undo stack (5 deep) of deepcopy(self._tasks) snapshots before destructive ops.
        from collections import deque
        self._undo_stack: deque = deque(maxlen=5)
```

- [ ] **Step 2.2: Replace `_json_box` with master-detail layout in `_build_ui`**

Find the existing `_json_box` block (~line 130 in current file):

```python
        # --- JSON textbox (read-only after extract) ---
        self._json_box = ctk.CTkTextbox(
            self, wrap="word", corner_radius=10,
            fg_color=SURFACE, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._json_box.grid(row=2, column=0, padx=16, pady=(2, 4), sticky="nsew")
        self._json_box.configure(state="disabled")  # nothing to show yet
```

Replace with:

```python
        # --- Editor: master-detail layout ---
        editor = ctk.CTkFrame(self, fg_color="transparent")
        editor.grid(row=2, column=0, padx=16, pady=(2, 4), sticky="nsew")
        editor.grid_columnconfigure(0, weight=1, minsize=300)
        editor.grid_columnconfigure(1, weight=2, minsize=400)
        editor.grid_rowconfigure(0, weight=1)

        # Left: scrollable list of task rows + bottom action bar.
        left_panel = ctk.CTkFrame(editor, fg_color=SURFACE, corner_radius=10)
        left_panel.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left_panel.grid_rowconfigure(0, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)

        self._task_list = ctk.CTkScrollableFrame(
            left_panel, fg_color="transparent", corner_radius=0,
        )
        self._task_list.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
        self._task_list.grid_columnconfigure(0, weight=1)

        # Action bar inside left panel: Add / SelectAll / SelectNone / Delete
        list_actions = ctk.CTkFrame(left_panel, fg_color="transparent")
        list_actions.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="ew")
        list_actions.grid_columnconfigure(0, weight=1)
        list_actions.grid_columnconfigure(1, weight=1)
        list_actions.grid_columnconfigure(2, weight=1)
        list_actions.grid_columnconfigure(3, weight=1)
        self._btn_add = tonal_button(
            list_actions, text="+ Добавить", command=self._on_add_task, width=110,
        )
        self._btn_add.grid(row=0, column=0, padx=2, sticky="ew")
        self._btn_select_all = tonal_button(
            list_actions, text="✓ Все", command=self._on_select_all, width=70,
        )
        self._btn_select_all.grid(row=0, column=1, padx=2, sticky="ew")
        self._btn_select_none = tonal_button(
            list_actions, text="✗ Снять", command=self._on_select_none, width=80,
        )
        self._btn_select_none.grid(row=0, column=2, padx=2, sticky="ew")
        self._btn_delete = tonal_button(
            list_actions, text="🗑 Удалить", command=self._on_delete_task, width=100,
        )
        self._btn_delete.grid(row=0, column=3, padx=2, sticky="ew")

        # Right: form for editing selected task.
        self._form_panel = ctk.CTkFrame(editor, fg_color=SURFACE, corner_radius=10)
        self._form_panel.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        self._form_panel.grid_columnconfigure(0, weight=1)
        self._build_form()

        # Disable buttons that need a selection until something is selected.
        self._set_editor_buttons_state(empty=True)
```

- [ ] **Step 2.3: Implement `_build_form` (right-form widgets, no bindings yet)**

Append to the class body (anywhere after `_build_ui`):

```python
    def _build_form(self) -> None:
        """Build the right-side form. Variables are owned by the form
        and bound to the selected task via _bind_form_to / _form_to_task."""
        f = self._form_panel

        # StringVar/BooleanVar instances (re-bound on selection change).
        self._var_title       = ctk.StringVar()
        self._var_description = ctk.StringVar()
        self._var_priority    = ctk.StringVar(value="none")
        self._var_assignee    = ctk.StringVar(value="(нет)")
        self._var_due_date    = ctk.StringVar()

        row = 0
        label(f, "Заголовок").grid(row=row, column=0, padx=12, pady=(12, 2), sticky="w")
        row += 1
        self._entry_title = ctk.CTkEntry(
            f, textvariable=self._var_title, height=36,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
        )
        self._entry_title.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        self._var_title.trace_add("write", lambda *_: self._on_form_changed())

        row += 1
        label(f, "Приоритет").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        self._dropdown_priority = ctk.CTkOptionMenu(
            f, variable=self._var_priority,
            values=["none", "low", "medium", "high", "urgent"],
            command=lambda _v: self._on_form_changed(),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, button_color=BORDER,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._dropdown_priority.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")

        row += 1
        label(f, "Исполнитель").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        self._dropdown_assignee = ctk.CTkOptionMenu(
            f, variable=self._var_assignee,
            values=["(нет)"],
            command=lambda _v: self._on_form_changed(),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, button_color=BORDER,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._dropdown_assignee.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")

        row += 1
        label(f, "Метки").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        # For Phase 6.2, labels are displayed as a comma-joined string in an
        # entry. Toggle UI (chips with X buttons) is post-6.4 polish.
        self._var_labels_csv = ctk.StringVar()
        self._entry_labels = ctk.CTkEntry(
            f, textvariable=self._var_labels_csv, height=36,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
            placeholder_text="метка1, метка2 (только из team-labels)",
        )
        self._entry_labels.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        self._var_labels_csv.trace_add("write", lambda *_: self._on_form_changed())

        row += 1
        label(f, "Дата (YYYY-MM-DD)").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        self._entry_due = ctk.CTkEntry(
            f, textvariable=self._var_due_date, height=36,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
            placeholder_text="напр. 2026-05-15",
        )
        self._entry_due.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        self._var_due_date.trace_add("write", lambda *_: self._on_form_changed())

        row += 1
        label(f, "Описание").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        f.grid_rowconfigure(row, weight=1)
        self._textbox_description = ctk.CTkTextbox(
            f, wrap="word", height=80,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        )
        self._textbox_description.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="nsew")
        # CTkTextbox doesn't take a textvariable — we read it on save.
        self._textbox_description.bind("<<Modified>>", self._on_description_modified)

    def _set_editor_buttons_state(self, *, empty: bool) -> None:
        """Toggle right-form widget enable + delete-button enable based on selection."""
        state = "disabled" if empty else "normal"
        for w in (
            self._entry_title, self._dropdown_priority,
            self._dropdown_assignee, self._entry_labels, self._entry_due,
            self._textbox_description, self._btn_delete,
        ):
            try:
                w.configure(state=state)
            except Exception:
                pass
```

- [ ] **Step 2.4: Add stubs for editor handlers (so `command=` references resolve)**

Append to the class body:

```python
    # ── Editor handlers (stubs filled in subsequent tasks) ────────

    def _on_add_task(self) -> None:
        pass

    def _on_select_all(self) -> None:
        pass

    def _on_select_none(self) -> None:
        pass

    def _on_delete_task(self) -> None:
        pass

    def _on_form_changed(self) -> None:
        pass

    def _on_description_modified(self, _event=None) -> None:
        pass
```

- [ ] **Step 2.5: Static checks**

Run:
1. `python -m py_compile ui/dialogs/extract_tasks.py`
2. `python -c "from ui.dialogs.extract_tasks import ExtractTasksDialog"`
3. `pytest tests/ -v 2>&1 | tail -3` — must report 126 passed.

All clean? Proceed.

- [ ] **Step 2.6: Manual visual smoke (deferred to Task 5)**

Skip — Task 5 does the visual smoke. Don't run the GUI here.

- [ ] **Step 2.7: Commit**

```bash
git add ui/dialogs/extract_tasks.py
git commit -m "feat(extract): swap JSON textbox for master-detail editor scaffold (no interactivity yet)"
```

---

## Task 3: Editor interactivity — list rows + selection + form bindings + auto-save

**Goal:** Make the editor functional. Render task rows in left list, handle selection, bind form vars on selection change, write back on form change, auto-save tasks.json.

**Files:**
- Modify: `ui/dialogs/extract_tasks.py` (~150 line delta)

- [ ] **Step 3.1: Add `_TaskRow` widget class** (file-private, before `ExtractTasksDialog`)

In `ui/dialogs/extract_tasks.py`, AFTER the module-level constants but BEFORE `class ExtractTasksDialog`, add:

```python
_PRIORITY_GLYPHS = {
    "none":   "⚪",
    "low":    "🔵",
    "medium": "🟡",
    "high":   "🟠",
    "urgent": "🔴",
}


class _TaskRow(ctk.CTkFrame):
    """One row in the left task list. Clicking the row body selects;
    clicking the checkbox toggles selected without selecting.
    """

    def __init__(
        self, parent, task, *, on_select, on_toggle,
    ):
        super().__init__(parent, fg_color="transparent", corner_radius=6)
        self._task = task
        self._on_select = on_select
        self._on_toggle = on_toggle
        self._selected_visual = False

        self.grid_columnconfigure(1, weight=1)

        self._var_checked = ctk.BooleanVar(value=task.selected)
        self._check = ctk.CTkCheckBox(
            self, text="", variable=self._var_checked,
            command=self._handle_toggle,
            checkbox_height=18, checkbox_width=18,
            fg_color=BLUE_DIM, hover_color=BLUE_DIM, border_color=BORDER,
        )
        self._check.grid(row=0, column=0, padx=(8, 6), pady=4, sticky="w")

        self._lbl_title = ctk.CTkLabel(
            self, text=task.title or "(без заголовка)",
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        )
        self._lbl_title.grid(row=0, column=1, padx=2, pady=(4, 0), sticky="ew")

        self._lbl_summary = ctk.CTkLabel(
            self, text=self._summary_text(),
            font=ctk.CTkFont(family=FONT, size=11),
            text_color=TEXT_SECONDARY, anchor="w",
        )
        self._lbl_summary.grid(row=1, column=1, padx=2, pady=(0, 4), sticky="ew")

        # Click anywhere on the body (except the checkbox) to select.
        for w in (self, self._lbl_title, self._lbl_summary):
            w.bind("<Button-1>", self._handle_click)

    def _handle_click(self, _event=None):
        self._on_select(self._task)

    def _handle_toggle(self):
        self._task.selected = bool(self._var_checked.get())
        self._on_toggle()

    def set_selected_visual(self, selected: bool) -> None:
        self._selected_visual = selected
        self.configure(fg_color=SURFACE if selected else "transparent")

    def refresh_from_task(self) -> None:
        """Re-render summary + title from the underlying task. Called after
        edits to keep the row in sync with the form."""
        self._lbl_title.configure(text=self._task.title or "(без заголовка)")
        self._lbl_summary.configure(text=self._summary_text())
        self._var_checked.set(self._task.selected)

    def _summary_text(self) -> str:
        glyph = _PRIORITY_GLYPHS.get(self._task.priority.name.lower(), "⚪")
        assignee = self._task.assignee_name or "—"
        return f"👤 {assignee}  ·  {glyph} {self._task.priority.name.lower()}"
```

(`BLUE_DIM` — already imported in the file? No — add to the imports. Open the existing top-of-file imports and ensure both `BG, BORDER, FONT, GREEN, INPUT_BG, RED, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY` AND `BLUE_DIM` come from `theme`. Add `BLUE_DIM` to the import line.)

- [ ] **Step 3.2: Implement `_render_task_list` and selection handlers**

Append to the class body (or replace the stubs from Task 2.4):

```python
    # ── List rendering and selection ─────────────────────────────

    def _render_task_list(self) -> None:
        """Re-create row widgets from `self._tasks`. Called after extract,
        load, add, or delete."""
        for child in self._task_list.winfo_children():
            child.destroy()
        self._task_rows = []
        for task in self._tasks:
            row = _TaskRow(
                self._task_list, task,
                on_select=self._select_task,
                on_toggle=self._on_row_toggle,
            )
            row.grid(sticky="ew", padx=2, pady=1)
            self._task_rows.append(row)
        # Re-apply visual selection if applicable.
        if self._selected_index is not None and 0 <= self._selected_index < len(self._task_rows):
            self._task_rows[self._selected_index].set_selected_visual(True)

    def _select_task(self, task) -> None:
        """User clicked a task row. Persist the previous selection's form
        edits, then bind the form to the new task."""
        # Persist current selection's pending edits before switching.
        self._persist_current_task()

        try:
            new_index = self._tasks.index(task)
        except ValueError:
            return  # task no longer in list

        self._set_selection(new_index)

    def _set_selection(self, new_index: Optional[int]) -> None:
        """Update visual selection + form binding."""
        # Clear previous visual.
        if self._selected_index is not None and self._selected_index < len(self._task_rows):
            try:
                self._task_rows[self._selected_index].set_selected_visual(False)
            except Exception:
                pass

        self._selected_index = new_index

        if new_index is None or not (0 <= new_index < len(self._tasks)):
            self._set_editor_buttons_state(empty=True)
            self._clear_form_vars()
            return

        self._task_rows[new_index].set_selected_visual(True)
        self._set_editor_buttons_state(empty=False)
        self._bind_form_to(self._tasks[new_index])

    def _on_row_toggle(self) -> None:
        """A row's checkbox was toggled. The underlying task is already
        updated; just save."""
        self._save_tasks_to_disk()
```

- [ ] **Step 3.3: Implement form binding (`_bind_form_to`, `_form_to_task`, `_clear_form_vars`, `_persist_current_task`)**

Append:

```python
    # ── Form binding ─────────────────────────────────────────────

    def _bind_form_to(self, task) -> None:
        """Read `task` into form vars. Called on selection change."""
        # Suspend trace handlers temporarily by setting a guard flag.
        self._form_binding_in_progress = True
        try:
            self._var_title.set(task.title or "")
            self._var_priority.set(task.priority.name.lower())
            # Assignee dropdown values come from team_context. Refresh choices.
            ctx_members = self._teams_context_members()
            assignee_values = ["(нет)"] + [
                m.get("displayName") or m.get("name", m["id"]) for m in ctx_members
            ]
            self._dropdown_assignee.configure(values=assignee_values)
            current_assignee_label = "(нет)"
            if task.assignee_id and task.assignee_name:
                current_assignee_label = task.assignee_name
            self._var_assignee.set(current_assignee_label)
            # Labels: comma-separated names.
            self._var_labels_csv.set(", ".join(task.label_names))
            self._var_due_date.set(task.due_date or "")

            # Description goes through textbox, not StringVar.
            self._textbox_description.delete("1.0", "end")
            self._textbox_description.insert("1.0", task.description or "")
            # Reset the modified flag so the trace doesn't fire spuriously.
            self._textbox_description.edit_modified(False)
        finally:
            self._form_binding_in_progress = False

    def _clear_form_vars(self) -> None:
        self._form_binding_in_progress = True
        try:
            self._var_title.set("")
            self._var_priority.set("none")
            self._var_assignee.set("(нет)")
            self._var_labels_csv.set("")
            self._var_due_date.set("")
            self._textbox_description.delete("1.0", "end")
            self._textbox_description.edit_modified(False)
        finally:
            self._form_binding_in_progress = False

    def _form_to_task(self, task) -> None:
        """Write form vars into `task`. Called from _persist_current_task."""
        from tasks.schema import priority_from_string

        task.title = self._var_title.get().strip()
        task.priority = priority_from_string(self._var_priority.get())

        # Assignee: resolve label back to id via team_context.
        assignee_label = self._var_assignee.get()
        if assignee_label == "(нет)" or not assignee_label.strip():
            task.assignee_id = None
            task.assignee_name = None
        else:
            for m in self._teams_context_members():
                name = m.get("displayName") or m.get("name", m["id"])
                if name == assignee_label:
                    task.assignee_id = m["id"]
                    task.assignee_name = name
                    break

        # Labels: comma-split, intersect with team-context label names.
        wanted_names = [
            n.strip() for n in self._var_labels_csv.get().split(",")
            if n.strip()
        ]
        ctx_labels = self._teams_context_labels()
        name_to_id = {l["name"]: l["id"] for l in ctx_labels}
        clean_ids = []
        clean_names = []
        for n in wanted_names:
            if n in name_to_id:
                clean_ids.append(name_to_id[n])
                clean_names.append(n)
        task.label_ids = clean_ids
        task.label_names = clean_names

        due = self._var_due_date.get().strip()
        task.due_date = due if due else None

        task.description = self._textbox_description.get("1.0", "end").rstrip("\n")

    def _persist_current_task(self) -> None:
        """If a task is selected, write form back to it and save tasks.json.

        Called on selection change, form change, and dialog close.
        Also refreshes the task row so left list stays consistent.
        """
        if self._selected_index is None:
            return
        if not (0 <= self._selected_index < len(self._tasks)):
            return
        task = self._tasks[self._selected_index]
        self._form_to_task(task)
        # Refresh the visual row so title/summary stays in sync.
        if self._selected_index < len(self._task_rows):
            try:
                self._task_rows[self._selected_index].refresh_from_task()
            except Exception:
                pass
        self._save_tasks_to_disk()

    def _on_form_changed(self) -> None:
        """Trace callback fired by var.trace_add. Suspends during programmatic
        bind to avoid a save-loop on selection switch."""
        if getattr(self, "_form_binding_in_progress", False):
            return
        self._persist_current_task()

    def _on_description_modified(self, _event=None) -> None:
        if getattr(self, "_form_binding_in_progress", False):
            self._textbox_description.edit_modified(False)
            return
        if not self._textbox_description.edit_modified():
            return
        self._textbox_description.edit_modified(False)
        self._persist_current_task()

    def _teams_context_members(self) -> list:
        """Return the members list from the most recent team_context fetch.

        Cached on _meta or fetched lazily — for 6.2 we can read from the
        last extract result. If team_context wasn't fetched, return [].
        """
        return self._cached_members if hasattr(self, "_cached_members") else []

    def _teams_context_labels(self) -> list:
        return self._cached_labels if hasattr(self, "_cached_labels") else []
```

- [ ] **Step 3.4: Hook up `_render_task_list` from extract success**

In `_on_extract_success`, find:
```python
        # Show what's actually on disk — guarantees "shown == saved".
        from pathlib import Path
        from tasks.persistence import RAW_FILENAME
        try:
            raw_path = Path(self._history_folder) / RAW_FILENAME
            content = raw_path.read_text(encoding="utf-8")
        except OSError:
            # Fallback: serialize the in-memory result if the file vanished.
            content = json.dumps(
                {**meta, "tasks": [t.to_dict() for t in result["tasks"]]},
                ensure_ascii=False, indent=2,
            )

        self._json_box.configure(state="normal")
        self._json_box.delete("1.0", "end")
        self._json_box.insert("1.0", content)
        self._json_box.configure(state="disabled")
```

Replace with:
```python
        # Promote in-memory tasks to dialog state.
        self._tasks = list(result["tasks"])
        self._meta = dict(meta)
        # Cache team context for assignee/label dropdowns + form-to-task resolution.
        # extract() already fetched it via linear_client.team_context — but
        # didn't return it. Fetch here lazily from cache.
        # (Alternative: have extract() return ctx in result. Keep simpler for now.)
        self._cached_members = []
        self._cached_labels = []
        for t in self._teams:
            if t.get("id") == meta.get("team_id"):
                # We don't have direct ctx here; could re-fetch but skip — the
                # form will use any cached members/labels populated by an
                # earlier extract round. Phase 6.4 polish: cache ctx in dialog.
                break
        self._render_task_list()
        if self._tasks:
            self._set_selection(0)
        # Persist tasks.json once with the fresh list.
        self._save_tasks_to_disk()
```

Also, **plumb the team_context into the dialog**: in `_run_extraction` find the line `ctx = linear_client.team_context(team_id)` (it's inside `extract()` — but extractor doesn't expose ctx). Easier path: cache it ON the worker side BEFORE calling extract. Modify `_run_extraction` to call `linear.team_context(team["id"])` itself, store on `self._cached_members` / `self._cached_labels`, then pass through to `extract()` if extractor accepted external ctx — but extractor doesn't. So pass the raw ctx in result via small refactor:

In `tasks/extractor.py` `extract()`, change the return to include `ctx`:
```python
    return {
        "tasks": tasks,
        "corrections": corrections,
        "usage": response.get("usage", {}),
        "model": response.get("model", model),
        "raw_response": raw_content,
        "members": members,    # NEW: pass-through for editor's assignee dropdown
        "labels": labels,      # NEW: pass-through for editor's label resolution
    }
```

Then in `_on_extract_success`, replace the `_cached_members = []` lines with:
```python
        self._cached_members = result.get("members", [])
        self._cached_labels = result.get("labels", [])
```

This is a tiny plan-ladder change but clean. Update the spec coverage map below.

- [ ] **Step 3.5: Implement `_save_tasks_to_disk`**

Append:
```python
    def _save_tasks_to_disk(self) -> None:
        """Write tasks.json. Errors logged but not raised — auto-save is best-effort."""
        if not self._tasks or not self._meta:
            return
        try:
            from tasks.persistence import save_tasks
            save_tasks(self._history_folder, self._tasks, self._meta)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("auto-save tasks.json failed")
```

- [ ] **Step 3.6: Static checks**

```
python -m py_compile ui/dialogs/extract_tasks.py
python -c "from ui.dialogs.extract_tasks import ExtractTasksDialog"
pytest tests/ -v 2>&1 | tail -3
```

Must report 126 passed (no regressions). The two-line extractor refactor (`members` / `labels` in result) is covered by the existing extractor tests — but those tests assert the result keys. **Run** `pytest tests/test_tasks_extractor.py -v` and check whether `test_extract_calls_clients_and_returns_validated_tasks` passes — if it does, the new keys are non-breaking. (It checks specific keys with `assert result["tasks"] == ...` style — adding new keys doesn't break.)

If extractor tests fail because of strict shape assertions, **add `members` and `labels` keys assertions** in 1-2 tests OR loosen the assertion. Don't break the test suite.

- [ ] **Step 3.7: Commit**

```bash
git add ui/dialogs/extract_tasks.py tasks/extractor.py
git commit -m "feat(extract): editor interactivity (rows + selection + form bindings + auto-save)"
```

---

## Task 4: Action buttons + undo + load-existing

**Goal:** Make Add/Delete/SelectAll/SelectNone buttons functional. Implement Ctrl+Z undo. Detect existing `tasks.json` on dialog open and load it.

**Files:**
- Modify: `ui/dialogs/extract_tasks.py` (~80 line delta)

- [ ] **Step 4.1: Implement action button handlers**

Replace the stubs from Task 2.4 with real implementations:

```python
    def _on_add_task(self) -> None:
        from tasks.schema import Task
        self._push_undo_snapshot()
        new_task = Task(title="")
        self._tasks.append(new_task)
        self._render_task_list()
        self._set_selection(len(self._tasks) - 1)
        self._save_tasks_to_disk()

    def _on_select_all(self) -> None:
        self._push_undo_snapshot()
        for t in self._tasks:
            t.selected = True
        for r in getattr(self, "_task_rows", []):
            r.refresh_from_task()
        self._save_tasks_to_disk()

    def _on_select_none(self) -> None:
        self._push_undo_snapshot()
        for t in self._tasks:
            t.selected = False
        for r in getattr(self, "_task_rows", []):
            r.refresh_from_task()
        self._save_tasks_to_disk()

    def _on_delete_task(self) -> None:
        if self._selected_index is None:
            return
        self._push_undo_snapshot()
        del self._tasks[self._selected_index]
        new_index = min(self._selected_index, len(self._tasks) - 1) if self._tasks else None
        self._render_task_list()
        self._set_selection(new_index)
        self._save_tasks_to_disk()
```

- [ ] **Step 4.2: Implement undo stack**

Append:
```python
    def _push_undo_snapshot(self) -> None:
        """Snapshot _tasks BEFORE a destructive op. Capped at 5 deep."""
        import copy
        self._undo_stack.append(copy.deepcopy(self._tasks))

    def _undo(self, _event=None) -> None:
        """Ctrl+Z handler. Restore the last snapshot if any."""
        if not self._undo_stack:
            return
        prior = self._undo_stack.pop()
        # Persist current selection first so it isn't lost mid-restore.
        self._persist_current_task()
        self._tasks = prior
        self._render_task_list()
        self._set_selection(0 if self._tasks else None)
        self._save_tasks_to_disk()
```

In `__init__` (after `_build_ui` line), add the keyboard binding:
```python
        self.bind("<Control-z>", self._undo)
        self.bind("<Control-Z>", self._undo)
```

- [ ] **Step 4.3: Implement load-existing-tasks-on-open**

In `__init__`, after `self._undo_stack = deque(...)` and before `self._build_ui()`, add a call to attempt load:

```python
        # If tasks.json exists in the history folder (e.g., user re-opened the
        # dialog after a half-finished edit), load it instead of waiting for a
        # fresh extract.
        self._try_load_existing_tasks()
```

Then append the method:
```python
    def _try_load_existing_tasks(self) -> None:
        from pathlib import Path
        from tasks.persistence import MUTABLE_FILENAME, load_tasks
        path = Path(self._history_folder) / MUTABLE_FILENAME
        if not path.is_file():
            return
        try:
            loaded = load_tasks(self._history_folder)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("could not load existing tasks.json")
            return
        self._tasks = list(loaded.get("tasks", []))
        self._meta = {k: v for k, v in loaded.items() if k != "tasks"}
        # We don't have team_context for an offline-loaded session; leave
        # _cached_members/_cached_labels empty. The form will still work for
        # title/priority/description/due_date — assignee/labels just show what
        # was saved without re-resolving.
```

Then, after `_build_ui()` is called, also render the loaded tasks if any:

```python
        # If we loaded existing tasks above, render them.
        if self._tasks:
            self._render_task_list()
            self._set_selection(0)
```

Tip: place this after `_build_ui` but before `self._load_teams_async()`. The render needs the widgets to exist.

- [ ] **Step 4.4: Hook persist on dialog close**

In `_on_close`, BEFORE `self._cancel_event.set()`, add:
```python
        # Persist any pending form edits before tearing down.
        try:
            self._persist_current_task()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("persist on close failed")
```

- [ ] **Step 4.5: Static checks**

```
python -m py_compile ui/dialogs/extract_tasks.py
python -c "from ui.dialogs.extract_tasks import ExtractTasksDialog"
pytest tests/ -v 2>&1 | tail -3
```

Must report 126 passed.

- [ ] **Step 4.6: Commit**

```bash
git add ui/dialogs/extract_tasks.py
git commit -m "feat(extract): action buttons + 5-step undo stack + load-existing tasks.json on open"
```

---

## Task 5: Smoke + handoff

**Goal:** Visual smoke check that the editor works end-to-end. Then commit handoff.

- [ ] **Step 5.1: Final pytest**

```
pytest tests/ -v 2>&1 | tail -3
```
Expected: 126 passed.

- [ ] **Step 5.2: Visual smoke checklist (HUMAN — deferred to user before tag)**

After all subagent commits land, the human runs:

1. `python app.py` → transcribe a short audio file → click Извлечь задачи → click Извлечь.
2. Wait for ✓ Извлечено N задач.
3. **Editor visible**: split layout, left list with row widgets (checkbox + title + summary), right form populated with the first task's fields.
4. **Click a different task** in the left list — right form switches to its values.
5. **Edit the title** — left list summary updates as you type (or on blur).
6. **Change priority dropdown** — saved on change.
7. **Click + Добавить** — empty new task appears, auto-selected.
8. **Click 🗑 Удалить** on the new empty task — gone, selection moves to neighboring.
9. **Press Ctrl+Z** — deleted task returns.
10. **Click ✗ Снять** — all checkboxes clear.
11. **Close the dialog** (X or [Закрыть]).
12. **Re-open** Извлечь задачи — editor loads existing tasks.json (no fresh extract needed).
13. Inspect `history/<entry>/tasks.json` — content matches the editor state.

Acceptance: edits round-trip; undo restores deletions; reload from disk preserves state.

- [ ] **Step 5.3: Write handoff to 6.3**

Create `docs/superpowers/handoffs/2026-04-29-phase-6.2-to-6.3-handoff.md` with a similar structure to the 6.1→6.2 handoff (commits list, files added, carry-forwards from reviews, suggested first prompt for next chat).

- [ ] **Step 5.4: Commit handoff**

```bash
git add docs/superpowers/handoffs/2026-04-29-phase-6.2-to-6.3-handoff.md
git commit -m "docs: Phase 6.2 → 6.3 handoff"
```

- [ ] **Step 5.5: Tag deferred until 6.3**

Phase 6.2 and 6.3 ship together at end of session. No interim `phase-6.2` tag. Branch `phase-6.2-edit` will be merged after 6.3 is also done OR after 6.2 is verified standalone — decision deferred to that point.

---

## Spec coverage map (Phase 6.2)

| Spec requirement | Implemented in | Verified by |
|---|---|---|
| Master-detail layout (list left, form right) | Task 2.2 | Smoke 5.2.3 |
| Custom row widgets (checkbox + title + summary) | Task 3.1 (`_TaskRow`) | Smoke 5.2.3 |
| Selected row gets background color | Task 3.1 (`set_selected_visual`) | Smoke 5.2.4 |
| Form variables bind to selected task | Task 3.3 (`_bind_form_to`) | Smoke 5.2.4 |
| Auto-save on selection change | Task 3.3 (`_persist_current_task`) | Smoke 5.2.5/6 |
| Auto-save on form change (Tab/blur via trace) | Task 3.3 (trace_add + `_on_form_changed`) | Smoke 5.2.5/6 |
| Auto-save on dialog close | Task 4.4 | Smoke 5.2.11/13 |
| `+ Добавить задачу` button | Task 4.1 (`_on_add_task`) | Smoke 5.2.7 |
| `✗ Снять` clears all checkboxes | Task 4.1 (`_on_select_none`) | Smoke 5.2.10 |
| `🗑 Удалить` removes selected | Task 4.1 (`_on_delete_task`) | Smoke 5.2.8 |
| 5-step undo stack via Ctrl+Z | Task 4.2 (`_push_undo_snapshot`, `_undo`) | Smoke 5.2.9 |
| `tasks.json` mutable persistence | Task 1 + Task 3.5 (`_save_tasks_to_disk`) | Tests 1.1-1.5 + Smoke 5.2.13 |
| Load existing `tasks.json` on dialog open | Task 4.3 (`_try_load_existing_tasks`) | Smoke 5.2.12 |

## Out-of-scope (defer)

- "Send to Linear" button + per-task statuses — Phase 6.3
- "Show raw response" inline button after parse error — already in 6.1 (textbox shows raw_response on error path; for 6.2 we route through left-list-empty + status badge)
- Re-open from History dialog (the History entry shows "Open tasks" button) — Phase 6.4
- Per-keystroke undo (currently only destructive ops snapshot)
- Drag-to-reorder rows
- Cycles/projects/parent issues — explicitly out per spec
