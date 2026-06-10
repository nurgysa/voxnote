# Widget-Tree Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract widget-tree construction out of the two dialog god-objects (`ui/dialogs/settings.py` 1053 LOC, `ui/dialogs/extract_tasks/__init__.py` 2052 LOC) into free-function builder modules, with zero behavior change.

**Architecture:** Literal cut-paste refactor following the `ui/app/builder.py` precedent: each `_build_*` method becomes a free function `build_*(dialog, ...)` that mutates the dialog instance (`self` → `dialog` is the only token change inside bodies). Handlers, workers, traces, and the `__init__` skeleton stay on the dialog classes. Three serial PRs; each lands on main before the next branches.

**Tech Stack:** Python 3.10+, CustomTkinter, pytest (source-text structural tests — no GUI rig), ruff.

**Spec:** `docs/superpowers/specs/2026-06-10-widget-tree-split-design.md`
**Baseline:** main @ `687a9ba`. All line ranges below are valid at this commit — if the file drifted, re-locate by `def` name, not line number.

---

## Global rules (apply to every task)

**Rule M (the move):** for each method moved:
1. Cut the entire method body from the class.
2. Paste into the builder module as a free function: `def _build_X_section(self, parent)` → `def build_X_section(dialog, parent)` (drop the leading underscore, keep the rest of the name).
3. Inside the body, replace every `self` with `dialog`. **No other edits** — comments, docstrings, string literals, lazy imports all move verbatim.
4. Update every call site.

**Rule T (test edits):** structural tests are re-pointed, never weakened. Each edit keeps the original invariant; only the file being scanned (or the slice markers) change. Deleting an assertion is a plan violation.

**Gate (every commit):** `python -m pytest -q` green (baseline 766 passed / 2 skipped; grows as guard tests are added) AND `python -m ruff check .` clean. On PowerShell 5.1 do not pipe/redirect pytest output (it can swallow the summary).

**Git safety:** the user does parallel git work in this working tree. Before EVERY commit: `git branch --show-current` must print the task's branch, and stage specific files only (never `git add -A`).

---

# PR-1: `refactor/settings-builder` — ui/dialogs/settings_builder.py

What moves: module-level `_CURATED_MODELS` (settings.py:63–65), `_section_card` (316–329), and all 12 `_build_*_section` methods (~440 LOC). What stays in `settings.py`: `__init__` skeleton (header/banner/tabview/scrolls/footer/traces), `destroy`, `_apply_dialog_icon`, banner state machine (`_update_banner`, `_handle_banner_click`, `_jump_to_stt`, `_jump_to_lang`), meetings handlers (`_refresh_meetings_stats`, `_on_pick_meetings_folder`, `_on_migrate_choice`, `_on_migration_done`, `_save_meetings_path`, `_on_reset_meetings_folder`), `_refresh_summaries`, all gdrive handlers/workers, all send-log handlers/workers.

### Task 1.0: Branch + baseline

- [ ] **Step 1:** `git status --short` — expect only `?? scripts/smoke_dedup_live.py` (known untracked; leave it). `git branch --show-current` — expect `main`. If not on main or tree dirty beyond the known file: STOP, report.
- [ ] **Step 2:** `git pull origin main` then `git checkout -b refactor/settings-builder`
- [ ] **Step 3:** Baseline gate: `python -m pytest -q` → expect `766 passed, 2 skipped`; `python -m ruff check .` → expect no output (clean).

### Task 1.1: Builder module + section_card + simple sections (appearance, transcription, audio, meetings, dictionaries)

**Files:**
- Create: `ui/dialogs/settings_builder.py`
- Modify: `ui/dialogs/settings.py` (delete moved code; rewrite call sites)
- Modify: `tests/test_bundle_ui_only.py`, `tests/test_settings_dialog_no_inner_h1.py`, `tests/test_settings_dialog_meetings_section.py`, `tests/test_settings_dialog_banner.py` (only if red — see Step 5)

- [ ] **Step 1: Create `ui/dialogs/settings_builder.py`** with this exact header, then append the moved code per Rule M:

```python
"""Widget-tree constructor for the Settings dialog.

Extracted from ``ui/dialogs/settings.py`` (widget-tree split, 2026-06-10
spec). Mirrors the ``ui/app/builder.py`` contract: each ``build_*_section``
free function takes the live ``SettingsDialog`` instance, creates that
section's widgets inside ``parent`` (a per-tab scroll frame), and sets any
captured refs on ``dialog`` under their original names
(``dialog._lang_menu``, ``dialog._cloud_api_key_entry``, …) so the banner
jump / status handlers that remain on the class keep working. No business
logic lives here; handlers and workers stay on ``SettingsDialog``.

Import discipline (cycle guard): this module may import theme, ui.widgets,
ui.app.constants, providers, settings_helpers and utils — never
``ui.dialogs.settings`` and never module-level ``ui.app`` (the
``APPEARANCE_MODES`` import stays lazy inside ``build_appearance_section``).
"""

from __future__ import annotations

import customtkinter as ctk

from providers import PROVIDERS
from theme import (
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    INPUT_BG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.app.constants import LANGUAGES
from ui.dialogs.settings_helpers import (
    format_glide_success,
    format_linear_success,
    format_openrouter_success,
    format_trello_success,
)
from ui.widgets import (
    api_key_row,
    card,
    label,
    option_menu,
    primary_button,
    tonal_button,
)
from utils import get_meetings_dir, save_config
```

Then move, in this order (Rule M):
1. `_CURATED_MODELS` dict (settings.py:60–65 incl. its comment) — verbatim, stays module-level in the builder.
2. `_section_card(self, parent, title, row)` (316–329) → `section_card(dialog, parent, title, row)`. Note: its body never uses `self`/`dialog` — keep the parameter anyway (uniform contract, harmless).
3. `_build_appearance_section` (331–349) → `build_appearance_section(dialog, parent)`. The lazy `from ui.app import APPEARANCE_MODES` stays as the first statement inside the function (Rule 2 of the spec). `self._section_card(...)` becomes `section_card(dialog, ...)`; `self._parent.*` becomes `dialog._parent.*`.
4. `_build_transcription_section` (351–360) → `build_transcription_section`. Sets `dialog._lang_menu` — verify the attribute assignment survives verbatim.
5. `_build_audio_section` (362–382) → `build_audio_section`.
6. `_build_meetings_section` (452–492) → `build_meetings_section`. Its `command=` kwargs reference `dialog._on_pick_meetings_folder` / `dialog._on_reset_meetings_folder` and the trailing `dialog._refresh_meetings_stats()` call — those methods STAY on the class; the references resolve at runtime through `dialog`.
7. `_build_dictionaries_section` (565–577) → `build_dictionaries_section` (references `dialog._parent._open_terms_dialog`, sets `dialog._terms_summary`, calls `dialog._refresh_summaries()` — all stay valid).

Worked example of Rule M (section 3 above) so the transformation is unambiguous:

```python
def build_appearance_section(dialog, parent) -> None:
    # Lazy import — APPEARANCE_MODES lives in ui.app, importing at
    # module-load would create a circular dependency.
    from ui.app import APPEARANCE_MODES

    section = section_card(dialog, parent, "Внешний вид", row=0)

    label(section, "Тема").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    option_menu(
        section, dialog._parent._appearance_var, list(APPEARANCE_MODES.keys()),
        command=dialog._parent._on_appearance_changed,
    ).grid(row=0, column=1, padx=4, pady=6, sticky="w")
    label(
        section,
        "«Системная» следует за настройкой Windows (Light/Dark mode).",
        anchor="w",
    ).grid(row=1, column=0, columnspan=2, padx=4, pady=(0, 4), sticky="w")
```

- [ ] **Step 2: Rewrite the five call sites in `SettingsDialog.__init__`** (settings.py:164–169 region). Add at the top of settings.py imports: `from ui.dialogs import settings_builder`. Then:

```python
        # Tab 1 «Транскрипция» — core loop (minimal sufficient set)
        settings_builder.build_appearance_section(self, scroll_transcription)
        settings_builder.build_transcription_section(self, scroll_transcription)
        settings_builder.build_audio_section(self, scroll_transcription)
        self._build_cloud_section(scroll_transcription)
        settings_builder.build_meetings_section(self, scroll_transcription)
        settings_builder.build_dictionaries_section(self, scroll_transcription)
```

(`_build_cloud_section` still a method until Task 1.2.) Delete the five moved method bodies and `_section_card` — wait: `_build_cloud_section` and the other 6 not-yet-moved methods still call `self._section_card`. **Keep `_section_card` as a one-line delegating shim until Task 1.3 removes the last user:**

```python
    def _section_card(self, parent, title: str, row: int) -> ctk.CTkFrame:
        """Shim during the split — remaining sections move in Tasks 1.2/1.3."""
        return settings_builder.section_card(self, parent, title, row)
```

Also delete the `_CURATED_MODELS` block from settings.py ONLY in Task 1.2 (its consumer `_build_openrouter_section` is still here until then).

- [ ] **Step 3:** `python -m ruff check .` — fix unused-import fallout in settings.py mechanically (do NOT remove imports still used by remaining methods).
- [ ] **Step 4:** `python -m pytest -q` — triage failures. Expected red set for this task: `test_settings_dialog_meetings_section.py` (scans settings.py for the meetings section), possibly `test_settings_dialog_banner.py` (AST-walks settings.py methods; the `_lang_menu` capture moved), `test_settings_dialog_no_inner_h1.py` only if it errors on… it scans settings.py only — stays green here, extended in Step 5 anyway.
- [ ] **Step 5: Re-point red tests (Rule T) + extend absence-guards now that the new file exists:**
  - `tests/test_settings_dialog_meetings_section.py`: change the scanned path / add the builder path so the same assertions (`«Митинги»` card, picker button, Default button, stats label markers) now check `ui/dialogs/settings_builder.py`; assertions about handlers (`_on_pick_meetings_folder` etc.) keep pointing at settings.py.
  - `tests/test_settings_dialog_banner.py`: if red — banner methods stay in settings.py; only assertions about the `_lang_menu` / capture inside build methods re-point to the builder file.
  - `tests/test_settings_dialog_no_inner_h1.py`: keep the settings.py check AND add the same `text="Настройки"`-absence check over `ui/dialogs/settings_builder.py` (absence guard must cover the new file).
  - `tests/test_bundle_ui_only.py`: everywhere it reads `ui/dialogs/settings.py` into a string for forbidden-marker scans (including the normalize-guard block that asserts `_normalize_var` / `normalize_audio` / `_on_normalize_changed` are absent), add `ui/dialogs/settings_builder.py` to the scanned set.
- [ ] **Step 6:** Gate: full pytest + ruff green.
- [ ] **Step 7:** Commit (specific files only):

```bash
git add ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_bundle_ui_only.py tests/test_settings_dialog_no_inner_h1.py tests/test_settings_dialog_meetings_section.py tests/test_settings_dialog_banner.py
git commit -m "refactor(settings): extract section_card + 5 simple sections to settings_builder"
```

### Task 1.2: Move the five api_key_row sections (cloud, openrouter, linear, glide, trello)

**Files:**
- Modify: `ui/dialogs/settings_builder.py`, `ui/dialogs/settings.py`
- Modify: `tests/test_settings_dialog_naming.py`, `tests/test_settings_cloud_validate.py`, `tests/test_settings_dialog_uses_api_key_row.py`, `tests/test_settings_trello_section.py`

- [ ] **Step 1:** Move per Rule M:
  - `_build_cloud_section` (384–450) → `build_cloud_section(dialog, parent)`. The `_on_validate` / `_persist` closures move verbatim (their `self._parent` → `dialog._parent`; the lazy `from providers import get_provider` stays inside `_on_validate`). Capture `dialog._cloud_api_key_entry = refs["entry"]` must survive verbatim.
  - `_build_openrouter_section` (598–640) → `build_openrouter_section`. Now delete `_CURATED_MODELS` from settings.py (60–65) — the builder's copy (Task 1.1) is the only one. Lazy `from tasks.openrouter_client import OpenRouterClient` stays inside its closure.
  - `_build_linear_section` (644–677) → `build_linear_section` (lazy `LinearClient` import stays inside).
  - `_build_glide_section` (679–712) → `build_glide_section` (lazy `GlideClient`).
  - `_build_trello_section` (714–773) → `build_trello_section` (lazy `TrelloClient`; two `api_key_row` calls move together).
- [ ] **Step 2:** Rewrite call sites in `__init__` (the cloud line from Task 1.1 plus the Tab-2 block):

```python
        settings_builder.build_cloud_section(self, scroll_transcription)
        ...
        # Tab 2 «Интеграции» — LLM-side optional extras
        settings_builder.build_openrouter_section(self, scroll_integrations)
        settings_builder.build_linear_section(self, scroll_integrations)
        settings_builder.build_glide_section(self, scroll_integrations)
        settings_builder.build_trello_section(self, scroll_integrations)
```

- [ ] **Step 3:** `python -m pytest -q` — expected red: `test_settings_dialog_naming` («Облачное распознавание» string moved), `test_settings_cloud_validate` (slices the `_build_cloud_section` body out of settings.py), `test_settings_dialog_uses_api_key_row` (counts `api_key_row(` in settings.py — count drops 6→0), `test_settings_trello_section`.
- [ ] **Step 4: Re-point (Rule T):**
  - `test_settings_dialog_naming.py`: scan `settings_builder.py` for the section-title strings (or scan both files' concatenation — invariant: the Russian section titles exist exactly once in the dialog's source surface).
  - `test_settings_cloud_validate.py`: slice target file → `ui/dialogs/settings_builder.py`, slice markers → `def build_cloud_section(` to the next `def `; all inner assertions (validate_key dispatch, `get_provider`, persist into `cloud_api_keys`, `save_config`) unchanged.
  - `test_settings_dialog_uses_api_key_row.py`: count `api_key_row(` occurrences in `settings_builder.py` (same ≥6 threshold).
  - `test_settings_trello_section.py`: re-point the build-section assertions (two api_key_row calls, both-credentials persist, `TrelloClient` lazy import) to `settings_builder.py`; any assertions about `_trello_key_var`/config keys living on the parent stay as-is.
- [ ] **Step 5:** Gate: full pytest + ruff green.
- [ ] **Step 6:** Commit:

```bash
git add ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_settings_dialog_naming.py tests/test_settings_cloud_validate.py tests/test_settings_dialog_uses_api_key_row.py tests/test_settings_trello_section.py
git commit -m "refactor(settings): move the five api_key_row sections to settings_builder"
```

### Task 1.3: Move gdrive + diagnostics sections; drop the shim

**Files:**
- Modify: `ui/dialogs/settings_builder.py`, `ui/dialogs/settings.py`
- Modify: `tests/test_settings_gdrive.py`, `tests/test_settings_diagnostics.py`

- [ ] **Step 1:** Move per Rule M: `_build_gdrive_section` (777–838) → `build_gdrive_section` (5 captured `_gdrive_*` refs + trailing `dialog._refresh_gdrive_button_state()`; the handler methods it references via `command=` stay on the class), `_build_diagnostics_section` (988–1004) → `build_diagnostics_section` (captures `_send_log_btn`, `_send_log_status`).
- [ ] **Step 2:** Rewrite the Tab-3 call sites; **delete the `_section_card` shim** (no users left in settings.py). Run `python -m ruff check .` and prune now-unused settings.py imports (likely: `INPUT_BG`, `BORDER`, the `ui.widgets` names no longer used — keep what banner/handlers still use, e.g. `tonal_button` is still used by the footer? Check: the footer `tonal_button` call at 204 stays in `__init__` → keep that import).
- [ ] **Step 3:** `python -m pytest -q` — expected red: `test_settings_gdrive.py` (asserts `def _build_gdrive_section` in settings.py), `test_settings_diagnostics.py`.
- [ ] **Step 4 (Rule T):** in both tests, build-section structural assertions re-point to `settings_builder.py`; handler/worker assertions (`_handle_gdrive_signin`, threading, `after(0, ...)` marshalling, `build_log_bundle`) keep pointing at `settings.py` — that code did not move. `test_settings_gdrive.py` also reads `ui/app/builder.py` + `ui/app/settings_mixin.py` — untouched, leave those parts alone.
- [ ] **Step 5:** Gate: full pytest + ruff green.
- [ ] **Step 6:** Commit:

```bash
git add ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_settings_gdrive.py tests/test_settings_diagnostics.py
git commit -m "refactor(settings): move gdrive + diagnostics sections, drop section_card shim"
```

### Task 1.4: Regression-lock guard test

**Files:**
- Create: `tests/test_widget_tree_split.py`

- [ ] **Step 1: Write the guard** (source-text checks — must NOT import ui modules; Linux CI has no PortAudio):

```python
"""Regression locks for the widget-tree split (spec 2026-06-10).

Source-text checks only — importing ui.* would load sounddevice, which
Linux CI cannot (no PortAudio). Encoding pinned: stock Windows defaults
to cp1252.
"""

from pathlib import Path

SETTINGS = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")
BUILDER = Path("ui/dialogs/settings_builder.py").read_text(encoding="utf-8")


def test_settings_class_has_no_build_methods():
    # The split's whole point: widget-tree construction lives in the
    # builder module, not on the dialog class.
    assert "def _build_" not in SETTINGS


def test_settings_builder_defines_no_class():
    # Free functions only — a class here means the god-object is regrowing.
    assert "\nclass " not in BUILDER and not BUILDER.startswith("class ")


def test_settings_builder_import_discipline():
    # Cycle guard: the builder must never import its own dialog module,
    # and ui.app only lazily (inside build_appearance_section).
    assert "from ui.dialogs.settings import" not in BUILDER
    assert "import ui.dialogs.settings" not in BUILDER
    head = BUILDER.split("\ndef ", 1)[0]  # module level = before first def
    assert "from ui.app import" not in head
```

- [ ] **Step 2:** `python -m pytest tests/test_widget_tree_split.py -v` → expect 3 passed.
- [ ] **Step 3:** Full gate: pytest + ruff.
- [ ] **Step 4:** Commit:

```bash
git add tests/test_widget_tree_split.py
git commit -m "test: regression locks for the settings widget-tree split"
```

### Task 1.5: PR + checkpoint

- [ ] **Step 1:** Sanity: `wc -l ui/dialogs/settings.py ui/dialogs/settings_builder.py` → expect roughly 615 / 490 (±10%; report the numbers in the PR body). `git log --oneline main..HEAD` → 4 commits.
- [ ] **Step 2:** Push + `gh pr create` with `--body-file` (PS 5.1 quoting rule). Body: `## Summary` (what moved / what stayed / zero behavior change), `## Test plan` checkboxes: full suite green incl. new guards; ruff clean; **user GUI smoke (≤60 s): open Настройки from the main repo, walk all 3 tabs, click the first-run banner → focus lands in the key entry, «Проверить» on the STT key still round-trips.** End body with the Claude Code attribution line.
- [ ] **Step 3:** **STOP. Checkpoint:** user smokes + merges PR-1. Do not branch PR-2 until PR-1 is squash-merged into main (stacked PRs forbidden).

---

# PR-2: `refactor/extract-builder-form` — extract_tasks/builder.py (form + speaker rows + participants)

Branch from FRESH main (post PR-1 merge): `git checkout main && git pull && git checkout -b refactor/extract-builder-form`. Baseline gate first (Task 1.0 Step 3 pattern).

### Task 2.1: Builder module + the two runtime re-renderers

**Files:**
- Create: `ui/dialogs/extract_tasks/builder.py`
- Modify: `ui/dialogs/extract_tasks/__init__.py`

- [ ] **Step 1: Create `ui/dialogs/extract_tasks/builder.py`:**

```python
"""Widget-tree constructor for the Extract-Tasks dialog.

Extracted from ``ui/dialogs/extract_tasks/__init__.py`` (widget-tree
split, 2026-06-10 spec). Same contract as ``ui/app/builder.py`` /
``ui/dialogs/settings_builder.py``: free functions take the live dialog,
create widgets, and set captured refs on it under their original names.
Handlers, workers (extraction/dedup/containers), and state stay on
``ExtractTasksDialog``.

Import discipline (cycle guard): may import theme, ui.widgets and the
sibling ``.constants`` / ``.task_row`` — never the package ``__init__``.
"""

from __future__ import annotations

import customtkinter as ctk

from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    INPUT_BG,
    RED,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.widgets import label, primary_button, tonal_button
```

(Trim this import list with ruff after the moves land — start broad, end exact. Add `from .constants import ...` names only if the moved bodies use them.)

- [ ] **Step 2:** Move per Rule M: `_rebuild_context_participants` (`__init__.py`:480–502) → `rebuild_context_participants(dialog)`, `_build_speaker_rows` (560–609) → `build_speaker_rows(dialog)`. Both set state the queries read (`dialog._context_person_vars`, `dialog._speaker_row_vars`) — names verbatim.
- [ ] **Step 3:** Find ALL call sites: `grep -n "_rebuild_context_participants\|_build_speaker_rows" ui/dialogs/extract_tasks/__init__.py` — expected: inside `_build_ui` (229–479 region) and in `_on_context_project_changed` (503–510); there may be one more near `_restore_context_selection`. Rewrite each `self._rebuild_context_participants(...)` → `builder.rebuild_context_participants(self, ...)` (after adding `from . import builder` to the module imports — relative import of a sibling, no cycle).
- [ ] **Step 4:** Gate: full pytest + ruff (expected: all green — no structural test pins these two methods to `__init__.py`; if something red, apply Rule T).
- [ ] **Step 5:** Commit:

```bash
git add ui/dialogs/extract_tasks/builder.py ui/dialogs/extract_tasks/__init__.py
git commit -m "refactor(extract): extract speaker-rows + context-participants renderers to builder"
```

### Task 2.2: Move `_build_form`

**Files:**
- Modify: `ui/dialogs/extract_tasks/builder.py`, `ui/dialogs/extract_tasks/__init__.py`
- Modify: `tests/test_extract_dialog_close_persist.py`, `tests/test_bundle_ui_only.py`

- [ ] **Step 1:** Move `_build_form` (1245–1353) → `build_form(dialog)` per Rule M. Form vars/widgets (`_var_title`, `_entry_title`, `_dropdown_priority`, `_textbox_description`, …) are read by `_set_editor_buttons_state` (1354–1367) and the task-selection handlers — captured names verbatim.
- [ ] **Step 2:** `grep -n "_build_form" ui/dialogs/extract_tasks/__init__.py` — rewrite each call site to `builder.build_form(self)`.
- [ ] **Step 3:** `python -m pytest -q` — expected red: `tests/test_extract_dialog_close_persist.py` (window-slices `__init__.py` between `def _on_close(` and `def _build_form(` — the end marker is gone). Fix (Rule T): end marker → the def that now follows `_on_close` in the file (`def _set_editor_buttons_state(`); the sliced invariants (askyesno «Закрыть без сохранения?», abort-on-Нет) unchanged.
- [ ] **Step 4:** Extend the absence-guard: in `tests/test_bundle_ui_only.py`, add `ui/dialogs/extract_tasks/builder.py` to the same scanned-file sets that Task 1.1 extended (forbidden markers must not resurrect in any builder module).
- [ ] **Step 5:** Gate: full pytest + ruff green.
- [ ] **Step 6:** Commit:

```bash
git add ui/dialogs/extract_tasks/builder.py ui/dialogs/extract_tasks/__init__.py tests/test_extract_dialog_close_persist.py tests/test_bundle_ui_only.py
git commit -m "refactor(extract): move task-form construction to builder"
```

### Task 2.3: PR + checkpoint

- [ ] **Step 1:** `wc -l ui/dialogs/extract_tasks/__init__.py ui/dialogs/extract_tasks/builder.py` → expect roughly 1870 / 210. `gh pr create --body-file` (Summary + Test plan; user smoke ≤60 s: **open the Extract dialog, select a task → form fields populate and edit, switch context project → participants re-render, speaker rows render**). Attribution line.
- [ ] **Step 2:** **STOP. Checkpoint:** user smokes + merges PR-2.

---

# PR-3: `refactor/extract-builder-ui` — move `_build_ui` (the 251-LOC tree)

Branch from FRESH main (post PR-2): `refactor/extract-builder-ui`. Baseline gate first.

### Task 3.1: Move `_build_ui`

**Files:**
- Modify: `ui/dialogs/extract_tasks/builder.py`, `ui/dialogs/extract_tasks/__init__.py`

- [ ] **Step 1:** Move `_build_ui` (229–479 at baseline; re-locate by name — PR-2 shifted lines) → `build_ui(dialog)` per Rule M. The lazy `from directory.store import DirectoryError` (was line 324) stays inside the function. Internal calls to the already-moved renderers become plain module-local calls (`build_speaker_rows(dialog)` etc. — same module now). It sets ~20 captured refs (see spec Rule 4 list) — verbatim names.
- [ ] **Step 2:** Rewrite the single call site in `ExtractTasksDialog.__init__` (was line 178): `self._build_ui()` → `builder.build_ui(self)`. **Order check (spec Rule 3):** the keyboard bindings + `_load_containers_async()` that follow it in `__init__` must remain AFTER this call.
- [ ] **Step 3:** `python -m pytest -q` — triage. `tests/test_dialog_dedup_ui.py` reads `__init__.py` text but pins handler/worker markers (`_run_dedup`, `_on_extract_success`, ordering via `.index()`) — those did not move; expect green. If any structural test pinned `_build_ui` markers, apply Rule T (re-point to builder.py).
- [ ] **Step 4:** `python -m ruff check .` — prune `__init__.py` imports orphaned by the move (theme tokens, `ui.widgets` names now used only by the builder; keep what handlers still use — e.g. `messagebox`, `save_config`).
- [ ] **Step 5:** Commit:

```bash
git add ui/dialogs/extract_tasks/builder.py ui/dialogs/extract_tasks/__init__.py
git commit -m "refactor(extract): move the main widget tree to builder.build_ui"
```

### Task 3.2: Extend the regression lock to extract_tasks

**Files:**
- Modify: `tests/test_widget_tree_split.py`

- [ ] **Step 1:** Append:

```python
EXTRACT = Path("ui/dialogs/extract_tasks/__init__.py").read_text(encoding="utf-8")
EXTRACT_BUILDER = Path("ui/dialogs/extract_tasks/builder.py").read_text(encoding="utf-8")


def test_extract_dialog_has_no_build_methods():
    assert "def _build_" not in EXTRACT


def test_extract_builder_defines_no_class():
    assert "\nclass " not in EXTRACT_BUILDER and not EXTRACT_BUILDER.startswith("class ")


def test_extract_builder_import_discipline():
    # The builder must never import the package __init__ back (cycle).
    assert "from ui.dialogs.extract_tasks import" not in EXTRACT_BUILDER
```

(Note: `_rebuild_context_participants` was renamed in PR-2, so `def _build_` / `def _rebuild_` are both gone from `__init__.py` — if `_rebuild_` still matches anything, that's a missed move; investigate, don't loosen the assert.)

- [ ] **Step 2:** `python -m pytest tests/test_widget_tree_split.py -v` → 6 passed. Full gate: pytest + ruff.
- [ ] **Step 3:** Commit:

```bash
git add tests/test_widget_tree_split.py
git commit -m "test: extend widget-tree split locks to extract_tasks"
```

### Task 3.3: PR + checkpoint

- [ ] **Step 1:** `wc -l` both files → expect roughly 1620 / 470. `gh pr create --body-file` (user smoke ≤60 s: **open Extract dialog end-to-end — extraction run + task list render + send path untouched**). Attribution line.
- [ ] **Step 2:** **STOP. Final checkpoint:** user smokes + merges PR-3. Then update the memory note (Вариант 2 DONE) and surface the follow-up candidates (Вариант 3 provider dedup; optional micro-PR for the `__init__` skeleton if ever wanted).

---

## Self-review notes (writing-plans checklist)

- **Spec coverage:** layout (PR-1 Tasks 1.1–1.3, PR-2 2.1–2.2, PR-3 3.1), rules 1–4 (header docstrings + Rule M + order checks), absence-guard extension (1.1 Step 5, 2.2 Step 4), new guard test (1.4, 3.2), red-test list (each named in its task), serial PR gates (1.5, 2.3, 3.3), CUT list respected (handlers/workers never listed for moving). Constants migration from the parked design verified already-done → no task.
- **Known uncertainty, by design:** exact internal variable names inside the 11 structural test files are not reproduced here; Rule T pins the edit contract (re-point, never weaken) and each task names the exact file + invariant. Executors read the test file before editing.
- **Type consistency:** builder functions uniformly `(dialog, parent)` for settings sections, `(dialog)` for extract (parents are dialog attributes there); `section_card(dialog, parent, title, row)` matches all 12 internal uses.
