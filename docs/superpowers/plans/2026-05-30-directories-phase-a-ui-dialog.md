# «Справочники» directory dialog (Phase A UI, part 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A «Справочники» CRUD dialog that lets the user manage the people and projects directory (the `directory/` backend shipped in Phase A core), launched from the main window.

**Architecture:** A new `ui/dialogs/directory.py::DirectoryDialog` (a `CTkToplevel` with a `CTkTabview` — «Люди» / «Проекты» tabs), each a master-detail list+form backed by `directory.store.DirectoryStore`. Every mutation persists immediately (atomic JSON, already implemented in the store). A launcher in `ui/app/dialogs_mixin.py` and a toolbar button in `ui/app/builder.py` wire it to the main window. Mirrors the existing `ui/dialogs/terms.py` CRUD pattern and the `CTkTabview` already used in `ui/dialogs/settings.py`.

**Tech Stack:** Python 3.10+, CustomTkinter, the `directory/` package (on `main`). Tests: `pytest` (source-text/structural — CTk dialogs can't be instantiated on headless CI). Lint: `ruff`.

**Spec:** `docs/superpowers/specs/2026-05-30-directories-and-voice-id-design.md` (Part A — «Справочники» dialog, D-E).

**Scope of THIS plan:** the directory-management dialog + its entry point only. The **Extract-dialog speaker-attribution panel + context injection into `generate`/`extract`** (Phase A UI part 2) is a **separate follow-up plan** (`feat/directory-backend` already exposes the `context=` params it will use). Phase **B1** (voice-ID) is later still.

**Branch:** `feat/directory-ui-dialog` (off the current `main`, which contains Phase A core). One commit per task.

**Testing note:** CustomTkinter dialogs need a display and pull `ui` imports that load PortAudio on Linux CI, so they are **not** unit-tested by instantiation. The dialog's *data* layer (`DirectoryStore`) is already fully unit-tested in Phase A core. Here we use **source-text structural tests** (read the file as text, assert on content — the established repo pattern, e.g. `tests/test_ui_constants.py`) plus a **manual smoke** checklist. The global `tests/test_theme_invariants.py` automatically covers the new file for naked-hex violations.

---

## File structure

| File | Responsibility |
|------|----------------|
| `ui/dialogs/directory.py` | `DirectoryDialog` — two-tab CRUD over `DirectoryStore` |
| `ui/app/dialogs_mixin.py` | **+** `_open_directory_dialog` launcher + import |
| `ui/app/builder.py` | **+** «Справочники» toolbar button → `app._open_directory_dialog` |
| `tests/test_directory_dialog.py` | source-text checks on the dialog file |
| `tests/test_directory_dialog_entrypoint.py` | source-text checks on launcher + button |

---

## Task 1: the «Справочники» dialog

**Files:**
- Create: `ui/dialogs/directory.py`
- Test: `tests/test_directory_dialog.py`

- [ ] **Step 1: Write the failing structural test**

Create `tests/test_directory_dialog.py` (reads the file as text — does NOT import it, so it is safe on headless Linux CI):

```python
from pathlib import Path

SRC = Path("ui/dialogs/directory.py")


def test_dialog_file_exists():
    assert SRC.is_file(), "ui/dialogs/directory.py must exist"


def test_dialog_uses_tabview_with_both_tabs():
    src = SRC.read_text(encoding="utf-8")
    assert "CTkTabview" in src
    assert '"Люди"' in src
    assert '"Проекты"' in src


def test_dialog_is_backed_by_directory_store():
    src = SRC.read_text(encoding="utf-8")
    assert "DirectoryStore" in src
    assert "from directory.schema import" in src
    assert "Person" in src and "Project" in src


def test_dialog_persists_via_store_mutators():
    src = SRC.read_text(encoding="utf-8")
    # CRUD must go through the store, not ad-hoc file writes.
    assert "upsert_person" in src
    assert "upsert_project" in src
    assert "delete_person" in src
    assert "delete_project" in src


def test_dialog_releases_grab_on_close():
    src = SRC.read_text(encoding="utf-8")
    assert "grab_release" in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_directory_dialog.py -v`
Expected: FAIL — `assert SRC.is_file()` fails (file not created yet).

- [ ] **Step 3: Implement the dialog**

Create `ui/dialogs/directory.py` with this exact content:

```python
"""«Справочники» — people + projects directory editor (Phase A UI).

Two-tab CRUD over directory.store.DirectoryStore. «Люди»: ФИО, role, project
membership (checkboxes). «Проекты»: name + description. Mutations persist
immediately via DirectoryStore (atomic JSON at ~/.audio-transcriber/directory.json).
Mirrors ui/dialogs/terms.py for the list/row/button style.
"""
from __future__ import annotations

import customtkinter as ctk

from directory.schema import Person, Project
from directory.store import DirectoryError, DirectoryStore
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    INPUT_BG,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class DirectoryDialog(ctk.CTkToplevel):
    """CRUD editor for the people/projects directory («Справочники»)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Справочники")
        self.geometry("680x640")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._store = DirectoryStore()
        try:
            self._store.load()
        except DirectoryError:
            # Corrupt file: start empty rather than crash. The next successful
            # save overwrites the bad file atomically.
            pass

        self._editing_person_id: str | None = None
        self._editing_project_id: str | None = None
        self._project_check_vars: dict[str, ctk.BooleanVar] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._tabview = ctk.CTkTabview(
            self,
            fg_color=SURFACE,
            segmented_button_selected_color=BLUE,
            segmented_button_selected_hover_color=BLUE_DIM,
            text_color=TEXT_PRIMARY,
        )
        self._tabview.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")
        self._build_people_tab(self._tabview.add("Люди"))
        self._build_projects_tab(self._tabview.add("Проекты"))

        self._render_people()
        self._render_projects()
        self._clear_person_form()
        self._clear_project_form()

    # ───────────────────────── People tab ─────────────────────────
    def _build_people_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        self._people_list = ctk.CTkScrollableFrame(
            parent, fg_color=SURFACE, corner_radius=10,
        )
        self._people_list.grid(row=0, column=0, padx=4, pady=(4, 8), sticky="nsew")
        self._people_list.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(parent, fg_color=SURFACE_BRIGHT, corner_radius=10)
        form.grid(row=1, column=0, padx=4, pady=4, sticky="ew")
        form.grid_columnconfigure(0, weight=1)

        self._person_name_var = ctk.StringVar()
        self._person_role_var = ctk.StringVar()

        ctk.CTkEntry(
            form, textvariable=self._person_name_var, height=34,
            placeholder_text="ФИО", fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

        ctk.CTkEntry(
            form, textvariable=self._person_role_var, height=34,
            placeholder_text="Должностные обязанности", fg_color=INPUT_BG,
            border_color=BORDER, border_width=1, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        ).grid(row=1, column=0, padx=10, pady=4, sticky="ew")

        ctk.CTkLabel(
            form, text="Проекты:", anchor="w",
            font=ctk.CTkFont(family=FONT, size=12), text_color=TEXT_SECONDARY,
        ).grid(row=2, column=0, padx=10, pady=(6, 0), sticky="w")

        self._person_projects_frame = ctk.CTkFrame(form, fg_color="transparent")
        self._person_projects_frame.grid(row=3, column=0, padx=10, pady=4, sticky="ew")

        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.grid(row=4, column=0, padx=10, pady=(4, 10), sticky="w")

        ctk.CTkButton(
            btns, text="Новый", width=90, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(family=FONT, size=13), command=self._clear_person_form,
        ).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(
            btns, text="Сохранить", width=120, height=32, corner_radius=16,
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            command=self._save_person,
        ).grid(row=0, column=1, padx=6)
        self._person_delete_btn = ctk.CTkButton(
            btns, text="Удалить", width=100, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=RED,
            font=ctk.CTkFont(family=FONT, size=13), command=self._delete_person,
        )
        self._person_delete_btn.grid(row=0, column=2, padx=6)

    def _render_people(self) -> None:
        for w in self._people_list.winfo_children():
            w.destroy()
        people = self._store.people()
        if not people:
            ctk.CTkLabel(
                self._people_list, text="Нет людей",
                font=ctk.CTkFont(family=FONT, size=13), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=16)
            return
        for i, p in enumerate(people):
            row = ctk.CTkFrame(self._people_list, fg_color=SURFACE_BRIGHT, corner_radius=8)
            row.grid(row=i, column=0, padx=4, pady=3, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            label = p.full_name + (f" — {p.role}" if p.role else "")
            ctk.CTkButton(
                row, text=label, anchor="w", height=34, fg_color="transparent",
                hover_color=BORDER, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=13),
                command=lambda pid=p.id: self._load_person(pid),
            ).grid(row=0, column=0, padx=(8, 4), pady=4, sticky="ew")
            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                font=ctk.CTkFont(family=FONT, size=14),
                command=lambda pid=p.id: self._delete_person(pid),
            ).grid(row=0, column=1, padx=(0, 6))

    def _rebuild_person_projects(self, selected_ids: set[str]) -> None:
        for w in self._person_projects_frame.winfo_children():
            w.destroy()
        self._project_check_vars = {}
        projects = self._store.projects()
        if not projects:
            ctk.CTkLabel(
                self._person_projects_frame, text="(сначала добавьте проекты)",
                font=ctk.CTkFont(family=FONT, size=12), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, sticky="w")
            return
        for i, pr in enumerate(projects):
            var = ctk.BooleanVar(value=pr.id in selected_ids)
            self._project_check_vars[pr.id] = var
            ctk.CTkCheckBox(
                self._person_projects_frame, text=pr.name, variable=var,
                fg_color=BLUE, hover_color=BLUE_DIM, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=12),
            ).grid(row=i // 2, column=i % 2, padx=4, pady=2, sticky="w")

    def _clear_person_form(self) -> None:
        self._editing_person_id = None
        self._person_name_var.set("")
        self._person_role_var.set("")
        self._rebuild_person_projects(set())
        self._person_delete_btn.configure(state="disabled")

    def _load_person(self, person_id: str) -> None:
        p = self._store.get_person(person_id)
        if p is None:
            return
        self._editing_person_id = p.id
        self._person_name_var.set(p.full_name)
        self._person_role_var.set(p.role)
        self._rebuild_person_projects(set(p.project_ids))
        self._person_delete_btn.configure(state="normal")

    def _save_person(self) -> None:
        name = self._person_name_var.get().strip()
        if not name:
            return
        project_ids = [pid for pid, var in self._project_check_vars.items() if var.get()]
        role = self._person_role_var.get().strip()
        if self._editing_person_id:
            person = self._store.get_person(self._editing_person_id) or Person(full_name=name)
            person.full_name = name
            person.role = role
            person.project_ids = project_ids
        else:
            person = Person(full_name=name, role=role, project_ids=project_ids)
        self._store.upsert_person(person)
        self._render_people()
        self._clear_person_form()

    def _delete_person(self, person_id: str | None = None) -> None:
        pid = person_id or self._editing_person_id
        if not pid:
            return
        self._store.delete_person(pid)
        self._render_people()
        self._clear_person_form()

    # ───────────────────────── Projects tab ─────────────────────────
    def _build_projects_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        self._projects_list = ctk.CTkScrollableFrame(
            parent, fg_color=SURFACE, corner_radius=10,
        )
        self._projects_list.grid(row=0, column=0, padx=4, pady=(4, 8), sticky="nsew")
        self._projects_list.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(parent, fg_color=SURFACE_BRIGHT, corner_radius=10)
        form.grid(row=1, column=0, padx=4, pady=4, sticky="ew")
        form.grid_columnconfigure(0, weight=1)

        self._project_name_var = ctk.StringVar()
        ctk.CTkEntry(
            form, textvariable=self._project_name_var, height=34,
            placeholder_text="Название проекта", fg_color=INPUT_BG,
            border_color=BORDER, border_width=1, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

        self._project_desc_box = ctk.CTkTextbox(
            form, height=80, fg_color=INPUT_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(family=FONT, size=13),
        )
        self._project_desc_box.grid(row=1, column=0, padx=10, pady=4, sticky="ew")

        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.grid(row=2, column=0, padx=10, pady=(4, 10), sticky="w")
        ctk.CTkButton(
            btns, text="Новый", width=90, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(family=FONT, size=13), command=self._clear_project_form,
        ).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(
            btns, text="Сохранить", width=120, height=32, corner_radius=16,
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            command=self._save_project,
        ).grid(row=0, column=1, padx=6)
        self._project_delete_btn = ctk.CTkButton(
            btns, text="Удалить", width=100, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=RED,
            font=ctk.CTkFont(family=FONT, size=13), command=self._delete_project,
        )
        self._project_delete_btn.grid(row=0, column=2, padx=6)

    def _render_projects(self) -> None:
        for w in self._projects_list.winfo_children():
            w.destroy()
        projects = self._store.projects()
        if not projects:
            ctk.CTkLabel(
                self._projects_list, text="Нет проектов",
                font=ctk.CTkFont(family=FONT, size=13), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=16)
            return
        for i, pr in enumerate(projects):
            row = ctk.CTkFrame(self._projects_list, fg_color=SURFACE_BRIGHT, corner_radius=8)
            row.grid(row=i, column=0, padx=4, pady=3, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkButton(
                row, text=pr.name, anchor="w", height=34, fg_color="transparent",
                hover_color=BORDER, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=13),
                command=lambda pid=pr.id: self._load_project(pid),
            ).grid(row=0, column=0, padx=(8, 4), pady=4, sticky="ew")
            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                font=ctk.CTkFont(family=FONT, size=14),
                command=lambda pid=pr.id: self._delete_project(pid),
            ).grid(row=0, column=1, padx=(0, 6))

    def _clear_project_form(self) -> None:
        self._editing_project_id = None
        self._project_name_var.set("")
        self._project_desc_box.delete("1.0", "end")
        self._project_delete_btn.configure(state="disabled")

    def _load_project(self, project_id: str) -> None:
        pr = self._store.get_project(project_id)
        if pr is None:
            return
        self._editing_project_id = pr.id
        self._project_name_var.set(pr.name)
        self._project_desc_box.delete("1.0", "end")
        self._project_desc_box.insert("1.0", pr.description)
        self._project_delete_btn.configure(state="normal")

    def _save_project(self) -> None:
        name = self._project_name_var.get().strip()
        if not name:
            return
        description = self._project_desc_box.get("1.0", "end").strip()
        if self._editing_project_id:
            pr = self._store.get_project(self._editing_project_id) or Project(name=name)
            pr.name = name
            pr.description = description
        else:
            pr = Project(name=name, description=description)
        self._store.upsert_project(pr)
        self._render_projects()
        self._clear_project_form()

    def _delete_project(self, project_id: str | None = None) -> None:
        pid = project_id or self._editing_project_id
        if not pid:
            return
        self._store.delete_project(pid)
        self._render_projects()
        self._clear_project_form()

    def _close(self) -> None:
        self.grab_release()
        self.destroy()
```

- [ ] **Step 4: Run the structural test + lint**

Run: `pytest tests/test_directory_dialog.py -v`
Expected: PASS (5 tests).
Run: `python -m ruff check ui/dialogs/directory.py tests/test_directory_dialog.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/directory.py tests/test_directory_dialog.py
git commit -m "feat(ui): «Справочники» people/projects directory dialog"
```

---

## Task 2: launcher + toolbar button

**Files:**
- Modify: `ui/app/dialogs_mixin.py` (import + `_open_directory_dialog`)
- Modify: `ui/app/builder.py` (toolbar button)
- Test: `tests/test_directory_dialog_entrypoint.py`

- [ ] **Step 1: Write the failing structural test**

Create `tests/test_directory_dialog_entrypoint.py`:

```python
from pathlib import Path


def test_launcher_defined_in_mixin():
    src = Path("ui/app/dialogs_mixin.py").read_text(encoding="utf-8")
    assert "from ui.dialogs.directory import DirectoryDialog" in src
    assert "def _open_directory_dialog" in src
    assert "DirectoryDialog(self)" in src


def test_toolbar_button_wired_in_builder():
    src = Path("ui/app/builder.py").read_text(encoding="utf-8")
    assert "Справочники" in src
    assert "_open_directory_dialog" in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_directory_dialog_entrypoint.py -v`
Expected: FAIL (the strings aren't present yet).

- [ ] **Step 3: Add the launcher to `ui/app/dialogs_mixin.py`**

Add the import alongside the other dialog imports near the top (after
`from ui.dialogs.terms import TermsDialog`):

```python
from ui.dialogs.directory import DirectoryDialog
```

Add the launcher method (place it next to `_open_terms_dialog`):

```python
    def _open_directory_dialog(self):
        DirectoryDialog(self)
```

- [ ] **Step 4: Add the toolbar button in `ui/app/builder.py`**

`builder.py` builds the main window as a `build_ui(app)` free function. Find the
toolbar button that opens the Meetings dialog (it has `command=app._open_meetings_dialog`
and text «Митинги»). Immediately after that button, add a sibling button with the
SAME widget style/`grid`-or-`pack` placement the surrounding buttons use, changing
only:
- `text="Справочники"`
- `command=app._open_directory_dialog`
- the position index (next column/row in the toolbar's layout)

Match the existing buttons exactly for `fg_color`/`hover_color`/`text_color`/`font`/
`corner_radius`/`height` (use the same theme constants — do NOT introduce any naked
hex; `tests/test_theme_invariants.py` enforces this repo-wide). If the toolbar uses a
helper to build each button, call that helper instead of duplicating kwargs.

- [ ] **Step 5: Run the structural test + lint**

Run: `pytest tests/test_directory_dialog_entrypoint.py -v`
Expected: PASS (2 tests).
Run: `python -m ruff check ui/app/dialogs_mixin.py ui/app/builder.py tests/test_directory_dialog_entrypoint.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add ui/app/dialogs_mixin.py ui/app/builder.py tests/test_directory_dialog_entrypoint.py
git commit -m "feat(ui): launch «Справочники» from the main-window toolbar"
```

---

## Task 3: full gate + manual smoke

**Files:** none (verification only).

- [ ] **Step 1: Full test + lint gate**

Run: `pytest`
Expected: PASS — Phase-A-core baseline (~498) + 7 new structural tests ≈ 505 green.
Pay special attention to `tests/test_theme_invariants.py` (it scans the new
`ui/dialogs/directory.py` for naked-hex colour kwargs — must be clean).

Run: `python -m ruff check .`
Expected: clean.

- [ ] **Step 2: Manual smoke (required — UI is not unit-testable here)**

Run the app from the main repo (`python app.py`) and verify:
1. Main window shows a **«Справочники»** toolbar button; click opens the dialog with «Люди» / «Проекты» tabs.
2. **Проекты** tab → type name «Миграция биллинга» + description «Перенос на Stripe» → «Сохранить» → it appears in the list. Add a second project.
3. **Люди** tab → type ФИО «Айбек Нурланов», обязанности «тимлид» → tick «Миграция биллинга» checkbox → «Сохранить» → appears as «Айбек Нурланов — тимлид».
4. Click the person row → form repopulates (name, role, project checkbox ticked) → edit role → «Сохранить» → row updates.
5. Close + reopen the dialog → **all data persisted** (confirm `~/.audio-transcriber/directory.json` exists and contains the people + projects).
6. Delete a project → confirm it disappears AND the person no longer shows it ticked (the store's `delete_project` ref-cascade).
7. Delete the person → list shows «Нет людей».
8. No naked-hex/theme regressions: the dialog matches the app's dark theme.

- [ ] **Step 3: Commit (only if smoke surfaced fixes)**

If manual smoke required code changes, commit them:

```bash
git add -A
git commit -m "fix(ui): «Справочники» dialog smoke fixes"
```

(If smoke passed clean, there is nothing to commit in this task.)

---

## Self-review (completed during plan authoring)

**Spec coverage (Part A «Справочники» dialog, D-E):**
- Two-tab dialog (Люди / Проекты) → Task 1 ✓
- People CRUD with ФИО / role / project membership → Task 1 (`_save_person`, checkboxes) ✓
- Projects CRUD with name / description → Task 1 (`_save_project`) ✓
- Persists via `DirectoryStore` (atomic JSON, biometrics-safe location) → Task 1 (store mutators) ✓
- `delete_project` ref-cascade reflected in UI → covered by store (Phase A core) + smoke step 6 ✓
- Entry point from the main window → Task 2 ✓
- **Deferred (own plans):** Extract-dialog attribution panel + `context=` injection (Phase A UI part 2); `tracker_member_id`/`tracker_ref` editing (not needed until the tracker-bridge work) — intentionally omitted to keep v1 focused.

**Placeholder scan:** none — the dialog code is complete; the only freeform step is the Task 2 toolbar-button placement, which is bounded (mirror the existing «Митинги» button) and verified by a source-text test.

**Type/name consistency:** the dialog calls only `DirectoryStore` methods that exist on `main` (`load`, `people`, `projects`, `get_person`, `get_project`, `upsert_person`, `upsert_project`, `delete_person`, `delete_project`) and constructs `Person(full_name=, role=, project_ids=)` / `Project(name=, description=)` per the shipped schema. `_open_directory_dialog` / `DirectoryDialog` names match across Task 1, Task 2, and both tests.

**Testing honesty:** CTk dialogs can't be instantiated on headless CI, so Tasks 1–2 use source-text structural tests and Task 3 relies on manual smoke for actual behavior — explicitly called out, not hidden behind green unit tests.
