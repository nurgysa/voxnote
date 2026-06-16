# Transcription Queue PR-C1b — Project Selector + sources_dir Setting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a main-bar **project selector** that fills the `project_id` the worker uses to route a meeting into `<meetings_dir>/<project>/`, and a Settings **«Архив аудио»** field for `sources_dir` (the audio-archive folder the worker copies/moves source audio into).

**Architecture:** The selector is a `CTkOptionMenu` in the existing run-controls card (`ui/app/builder.py`), backed by a runtime `label→id` map rebuilt from `DirectoryStore.projects()` whenever projects change. The map + handlers live in `QueueMixin` (alongside `_build_options`, the one consumer). `_build_options` reads the selected `project_id` instead of the PR-C1 hardcoded `None`. The chosen project persists as `config["last_project_id"]` and is the default next launch. The Settings field mirrors the existing meetings-folder picker but simpler (no migration/stats): pick → write `config["sources_dir"]`; clear → empty (the worker already skips archiving when `sources_dir` is unset, `processing/worker.py:218`).

**Tech Stack:** Python 3.10+, CustomTkinter (`CTkOptionMenu`, `CTkEntry`, `filedialog.askdirectory`), `directory.store.DirectoryStore` / `directory.schema.Project`, `utils.save_config`. Tests are **source-slice** (read module text, assert on substrings) — importing `ui.app` pulls sounddevice/PortAudio and crashes Linux CI.

**Scope — IN:** main-bar project selector (+ `last_project_id` config key + `NO_PROJECT_LABEL` constant), `_build_options` reads it, refresh at startup + on Справочники-dialog close, Settings «Архив аудио» `sources_dir` picker.
**Scope — OUT (later PRs):** `inbox_dir` field + inbox poll → PR-C3; cost hint at enqueue → later; «Встречи» queue+history view (per-meeting status, Hermes badges, Открыть в Obsidian, Повторить) → PR-C2.

**Docs note:** `config.example.json` is the canonical config-key catalog (it is copied verbatim into `~/.voxnote/config.json` on frozen first-run by `utils._seed_default_config`). AGENTS.md/CLAUDE.md do **not** enumerate config keys (verified: zero matches for `sources_dir`/`inbox_dir`/`meetings_dir` in AGENTS.md), so this PR adds the key only to `config.example.json` — no CLAUDE.md/AGENTS.md churn.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `ui/app/constants.py` | Modify | Add `NO_PROJECT_LABEL = "Без проекта"` (shared by builder + queue_mixin). |
| `config.example.json` | Modify | Add `"last_project_id": ""` (default = no project). |
| `ui/app/builder.py` | Modify | Add `_project_var` + `_project_menu` as run-card row 1 («Проект» label + dropdown). |
| `ui/app/queue_mixin.py` | Modify | `_refresh_project_selector()` + `_on_project_changed()`; `_build_options` reads `project_id` from the selector map. |
| `ui/app/__init__.py` | Modify | Call `self._refresh_project_selector()` once after `self._dir_store.load()`. |
| `ui/app/dialogs_mixin.py` | Modify | On Справочники-dialog `<Destroy>`: reload `_dir_store` + refresh selector. |
| `ui/dialogs/settings_builder.py` | Modify | New `build_sources_section` (row 5); bump `build_dictionaries_section` row 5→6. |
| `ui/dialogs/settings.py` | Modify | Wire `build_sources_section` into tab 1; add `_on_pick_sources_folder` + `_on_clear_sources_folder`. |
| `tests/test_ui_project_selector.py` | Create | Source-slice asserts for the selector wiring + the config key. |
| `tests/test_settings_sources_section.py` | Create | Source-slice asserts for the «Архив аудио» section + handlers. |

---

## Setup: feature branch

- [ ] **Step 1: Create the branch off up-to-date main**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/transcription-queue-pr-c1b
```

Expected: on a new branch `feat/transcription-queue-pr-c1b`, working tree clean.

---

## Task 1: Main-bar project selector

**Files:**
- Modify: `ui/app/constants.py`
- Modify: `config.example.json`
- Modify: `ui/app/builder.py` (run-card block, ~lines 278–308)
- Modify: `ui/app/queue_mixin.py` (imports + `_build_options` + new methods)
- Modify: `ui/app/__init__.py` (after `self._dir_store.load()`, ~line 224)
- Modify: `ui/app/dialogs_mixin.py` (`_open_directory_dialog`, ~line 82)
- Test: `tests/test_ui_project_selector.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_project_selector.py`:

```python
"""Source-slice wiring tests for the PR-C1b main-bar project selector.

No ui.app import — sounddevice/PortAudio would break Linux CI. Mirrors
test_ui_queue_wiring.py: read the module text and assert on it.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONST = (_ROOT / "ui" / "app" / "constants.py").read_text(encoding="utf-8")
_BUILDER = (_ROOT / "ui" / "app" / "builder.py").read_text(encoding="utf-8")
_QUEUE = (_ROOT / "ui" / "app" / "queue_mixin.py").read_text(encoding="utf-8")
_INIT = (_ROOT / "ui" / "app" / "__init__.py").read_text(encoding="utf-8")
_DIALOGS = (_ROOT / "ui" / "app" / "dialogs_mixin.py").read_text(encoding="utf-8")


def test_no_project_label_constant_defined():
    assert "NO_PROJECT_LABEL" in _CONST


def test_config_example_has_last_project_id():
    example = json.loads((_ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert "last_project_id" in example, (
        "config.example.json must list last_project_id (seeded into user config)"
    )


def test_builder_creates_project_selector():
    assert "_project_var" in _BUILDER
    assert "_project_menu" in _BUILDER
    assert '"Проект"' in _BUILDER or "'Проект'" in _BUILDER
    assert "_on_project_changed" in _BUILDER  # menu command wired


def test_queue_mixin_has_project_selector_api():
    for name in ("_refresh_project_selector", "_on_project_changed"):
        assert f"def {name}" in _QUEUE


def test_build_options_reads_project_not_hardcoded_none():
    # project_id must come from the selector map, not a literal None.
    assert '"project_id": None' not in _QUEUE
    assert "_project_choices" in _QUEUE


def test_on_project_changed_persists_last_project_id():
    assert '"last_project_id"' in _QUEUE
    assert "save_config" in _QUEUE


def test_init_refreshes_selector_after_dir_store_load():
    assert "_refresh_project_selector" in _INIT


def test_directory_dialog_close_reloads_and_refreshes():
    # Editing projects in Справочники must refresh the selector on close.
    assert "<Destroy>" in _DIALOGS
    assert "_refresh_project_selector" in _DIALOGS
    assert "_dir_store.load()" in _DIALOGS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_ui_project_selector.py -v`
Expected: FAIL — `NO_PROJECT_LABEL`, `_project_var`, `_refresh_project_selector`, `last_project_id`, `<Destroy>` not yet present.

- [ ] **Step 3: Add the `NO_PROJECT_LABEL` constant**

In `ui/app/constants.py`, after the `SPEAKER_COUNTS` dict (after line 34, before `APPEARANCE_MODES`), add:

```python
# Main-bar project selector — the "no project" sentinel label. Its menu
# entry maps to project_id=None (meeting written to <meetings_dir>/ root,
# Hermes event project=null). Shared by builder.py + queue_mixin.py.
NO_PROJECT_LABEL = "Без проекта"
```

- [ ] **Step 4: Add the `last_project_id` config key**

In `config.example.json`, add the key after `"inbox_dir": "",` (line 28):

```json
  "inbox_dir": "",
  "last_project_id": "",
```

(Leave the rest of the file unchanged. `last_project_id=""` means «Без проекта» by default.)

- [ ] **Step 5: Build the selector widget in `builder.py`**

5a. Extend the constants import (lines 39–43) to include `NO_PROJECT_LABEL`:

```python
from .constants import (
    APPEARANCE_MODES,
    LANGUAGES,
    NO_PROJECT_LABEL,
    SPEAKER_COUNTS,
)
```

5b. Add the `_project_var` declaration. Put it in the persistent-state-vars block, right after the `_spk_count_var` block (after line 159, before the RNNoise comment at line 160):

```python
    # Main-bar project selector (PR-C1b). The visible value is a project
    # name (or «Без проекта»); QueueMixin._refresh_project_selector rebuilds
    # the values + the label→id map from the directory store after build_ui
    # (the store isn't constructed yet at build time) and again whenever the
    # Справочники dialog closes. Default selection comes from last_project_id.
    app._project_var = ctk.StringVar(value=NO_PROJECT_LABEL)
```

5c. Add the selector as run-card **row 1**. The run-card block ends with the settings button gridded at row 0, column 3 (lines 304–308). Immediately after that `app._btn_settings.grid(...)` call, append:

```python
    # Project selector — run-card row 1, aligned under the speaker label/menu
    # (col 1 = label, col 2 = dropdown). Populated by
    # QueueMixin._refresh_project_selector; feeds _build_options' project_id.
    label(run_card, "Проект").grid(
        row=1, column=1, padx=(0, 8), pady=(0, 14), sticky="w",
    )
    app._project_menu = option_menu(
        run_card, app._project_var, [NO_PROJECT_LABEL],
        command=app._on_project_changed,
    )
    app._project_menu.grid(
        row=1, column=2, padx=(0, 12), pady=(0, 14), sticky="ew",
    )
```

(`label` and `option_menu` are already imported in builder.py at lines 32–37.)

- [ ] **Step 6: Add the selector logic in `queue_mixin.py`**

6a. Extend imports. Replace the import block (lines 19–26):

```python
import os
import tkinter as tk
from tkinter import messagebox

from processing.model import StageStatus
from theme import GREEN, RED, TEXT_SECONDARY
from utils import save_config

from .constants import LANGUAGES, NO_PROJECT_LABEL, SPEAKER_COUNTS
```

6b. Rewrite `_build_options` (lines 32–51) — docstring + the `project_id` line:

```python
    def _build_options(self, source: str) -> dict:
        """Gather the current run options from the App's setting Vars into the
        dict the worker consumes. project_id comes from the main-bar project
        selector (Без проекта → None)."""
        saved_terms = self._config.get("hotwords", [])
        num_speakers, min_speakers, max_speakers = SPEAKER_COUNTS.get(
            self._spk_count_var.get(), (None, None, None),
        )
        return {
            "provider": self._cloud_provider_var.get(),
            "language": LANGUAGES.get(self._lang_var.get()),
            "diarize": bool(self._diar_var.get()),
            "hotwords": ", ".join(saved_terms) if saved_terms else None,
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
            "denoise": bool(self._denoise_var.get()),
            "project_id": getattr(self, "_project_choices", {}).get(
                self._project_var.get()
            ),
            "source": source,
        }
```

6c. Add the two new methods. Insert them right after `_build_options` (before `_enqueue`):

```python
    def _refresh_project_selector(self) -> None:
        """(Re)build the project dropdown from the directory store.

        Called once at startup (after _dir_store.load()) and again whenever the
        Справочники dialog closes (projects may be added/renamed/deleted).
        Builds a label→id map (Без проекта → None); duplicate project names get
        a short id suffix so the map stays 1:1. Restores the selection from
        config[last_project_id], falling back to Без проекта if that project is
        gone."""
        choices: dict[str, str | None] = {NO_PROJECT_LABEL: None}
        for project in self._dir_store.projects():
            label = project.name or "(без имени)"
            if label in choices:
                label = f"{label} · {project.id[:6]}"
            choices[label] = project.id
        self._project_choices = choices
        self._project_menu.configure(values=list(choices.keys()))

        last = (self._config.get("last_project_id") or "").strip()
        selected = NO_PROJECT_LABEL
        if last:
            for lbl, pid in choices.items():
                if pid == last:
                    selected = lbl
                    break
        self._project_var.set(selected)

    def _on_project_changed(self, _label: str | None = None) -> None:
        """Persist the chosen project as last_project_id so it's the default
        next launch. Без проекта (None) is stored as an empty string."""
        pid = getattr(self, "_project_choices", {}).get(self._project_var.get())
        self._config["last_project_id"] = pid or ""
        save_config(self._config)
```

- [ ] **Step 7: Populate the selector at startup in `__init__.py`**

In `ui/app/__init__.py`, the construction block has `self._dir_store.load()` (line 224). Immediately after that line, add:

```python
        self._dir_store.load()
        self._refresh_project_selector()  # populate the main-bar project dropdown
```

(The selector widget exists — `build_ui(self)` ran at line 218; `_dir_store` is now loaded; `_refresh_project_selector` is a QueueMixin method on `self`. The queue is constructed below and doesn't depend on this call.)

- [ ] **Step 8: Refresh the selector when Справочники closes (`dialogs_mixin.py`)**

Replace `_open_directory_dialog` (lines 82–83):

```python
    def _open_directory_dialog(self):
        dlg = DirectoryDialog(self)
        # Projects may be added/renamed/deleted in the editor. Refresh the
        # main-bar selector when this (modal, non-blocking — grab_set without
        # wait_window) dialog closes. DirectoryDialog persists via its own
        # store, so reload App's _dir_store first.
        dlg.bind(
            "<Destroy>",
            lambda e: self._on_directory_dialog_closed(e, dlg),
        )

    def _on_directory_dialog_closed(self, event, dlg) -> None:
        # CTk fires <Destroy> for inner widgets too; act only on the toplevel.
        if event.widget is dlg:
            self._dir_store.load()
            self._refresh_project_selector()
```

- [ ] **Step 9: Run the selector test to verify it passes**

Run: `python -m pytest tests/test_ui_project_selector.py -v`
Expected: PASS (all 8 tests).

- [ ] **Step 10: Lint**

Run: `python -m ruff check ui/app/ tests/test_ui_project_selector.py`
Expected: clean (no output / "All checks passed").

- [ ] **Step 11: Commit**

```bash
git add ui/app/constants.py config.example.json ui/app/builder.py ui/app/queue_mixin.py ui/app/__init__.py ui/app/dialogs_mixin.py tests/test_ui_project_selector.py
git commit -F- <<'EOF'
feat(ui): transcription-queue PR-C1b — main-bar project selector

Add a project dropdown to the run-controls card. It feeds _build_options'
project_id (was hardcoded None in PR-C1), so the worker routes each meeting
into <meetings_dir>/<project>/ and stamps the Hermes event's project field.

- ui/app/constants.py: NO_PROJECT_LABEL sentinel (Без проекта → project_id None)
- config.example.json: last_project_id key (default selection, persisted)
- builder.py: _project_var + _project_menu as run-card row 1
- queue_mixin.py: _refresh_project_selector (label→id map from DirectoryStore;
  dup-name suffix; restore from last_project_id) + _on_project_changed (persist)
- __init__.py: refresh once after _dir_store.load()
- dialogs_mixin.py: reload _dir_store + refresh on Справочники <Destroy>

Source-slice tests (no ui.app import — PortAudio crashes Linux CI).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 2: Settings «Архив аудио» (sources_dir)

**Files:**
- Modify: `ui/dialogs/settings_builder.py` (new `build_sources_section`; bump dictionaries row)
- Modify: `ui/dialogs/settings.py` (orchestrator line ~155; new handlers ~after line 409)
- Test: `tests/test_settings_sources_section.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings_sources_section.py`:

```python
"""Source-text checks for the «Архив аудио» (sources_dir) section in Settings."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = _ROOT / "ui" / "dialogs" / "settings.py"
BUILDER_PATH = _ROOT / "ui" / "dialogs" / "settings_builder.py"


def test_sources_section_card_exists():
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert "def build_sources_section" in src
    assert '"Архив аудио"' in src or "'Архив аудио'" in src


def test_sources_section_wired_into_tab():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "build_sources_section" in src


def test_sources_handlers_write_config_key():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    for name in ("_on_pick_sources_folder", "_on_clear_sources_folder"):
        assert f"def {name}" in src
    assert '"sources_dir"' in src
    assert "askdirectory" in src  # native folder picker


def test_meetings_section_still_present():
    # The existing Встречи section must survive untouched (regression guard).
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert '"Встречи"' in src or "'Встречи'" in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_settings_sources_section.py -v`
Expected: FAIL — `build_sources_section`, «Архив аудио», `_on_pick_sources_folder` not yet present.

- [ ] **Step 3: Add `build_sources_section` and bump the dictionaries row in `settings_builder.py`**

3a. Insert `build_sources_section` immediately after `build_meetings_section` ends (after line 170, before `def build_dictionaries_section`):

```python
def build_sources_section(dialog, parent) -> None:
    """Audio-archive folder picker (sources_dir).

    Where the worker copies/moves source audio after transcription (the
    Hermes 'sources/' convention). Empty = don't archive — the worker skips
    archiving when sources_dir is unset (processing/worker.py). No
    migration/stats like Встречи: this is just a destination, and any
    previously archived files stay where they are.
    """
    section = section_card(dialog, parent, "Архив аудио", row=5)

    label(
        section,
        "Папка-архив исходного аудио (напр. Google Drive → sources). "
        "Пусто = не архивировать.",
        anchor="w",
    ).grid(row=0, column=0, columnspan=4, padx=4, pady=(0, 6), sticky="w")

    dialog._sources_path_var = ctk.StringVar(
        value=(dialog._parent._config.get("sources_dir") or ""),
    )
    dialog._sources_entry = ctk.CTkEntry(
        section, textvariable=dialog._sources_path_var,
        height=36, corner_radius=10,
        border_color=BORDER, border_width=1,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=12),
        state="readonly",
    )
    dialog._sources_entry.grid(
        row=1, column=0, columnspan=3, padx=4, pady=6, sticky="ew",
    )

    tonal_button(
        section, text="\U0001f4c1 Выбрать",
        command=dialog._on_pick_sources_folder, width=130,
    ).grid(row=1, column=3, padx=(4, 4), pady=6)

    tonal_button(
        section, text="Очистить",
        command=dialog._on_clear_sources_folder, width=120,
    ).grid(row=2, column=3, padx=(4, 4), pady=(0, 6))
```

(All names used — `section_card`, `label`, `ctk`, `BORDER`, `INPUT_BG`, `TEXT_PRIMARY`, `FONT`, `tonal_button` — are already imported at the top of settings_builder.py. No new imports.)

3b. Bump the dictionaries section row 5→6. In `build_dictionaries_section` (line 174):

```python
def build_dictionaries_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Словари", row=6)
```

- [ ] **Step 4: Wire `build_sources_section` into tab 1 in `settings.py`**

In the tab-1 orchestrator (line 155), insert the sources call between meetings and dictionaries:

```python
        settings_builder.build_meetings_section(self, scroll_transcription)
        settings_builder.build_sources_section(self, scroll_transcription)
        settings_builder.build_dictionaries_section(self, scroll_transcription)
```

- [ ] **Step 5: Add the two handlers in `settings.py`**

Insert after `_on_reset_meetings_folder` (after line 409, before `_refresh_summaries`):

```python
    def _on_pick_sources_folder(self) -> None:
        """«Выбрать» for the audio archive — native dir picker, then persist."""
        chosen = filedialog.askdirectory(
            title="Папка-архив исходного аудио",
            initialdir=self._sources_path_var.get() or None,
            parent=self,
        )
        if not chosen:
            return  # user cancelled
        normalized = os.path.abspath(chosen)
        self._parent._config["sources_dir"] = normalized
        save_config(self._parent._config)
        self._sources_path_var.set(normalized)

    def _on_clear_sources_folder(self) -> None:
        """«Очистить» — empty sources_dir disables archiving (worker skips)."""
        self._parent._config["sources_dir"] = ""
        save_config(self._parent._config)
        self._sources_path_var.set("")
```

(`os`, `filedialog`, `save_config` are already imported in settings.py.)

- [ ] **Step 6: Run the sources test to verify it passes**

Run: `python -m pytest tests/test_settings_sources_section.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 7: Confirm the existing meetings-section test still passes**

Run: `python -m pytest tests/test_settings_dialog_meetings_section.py -v`
Expected: PASS (unchanged — the Встречи section was not modified).

- [ ] **Step 8: Lint**

Run: `python -m ruff check ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_settings_sources_section.py`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_settings_sources_section.py
git commit -F- <<'EOF'
feat(ui): transcription-queue PR-C1b — Settings «Архив аудио» (sources_dir)

New tab-1 section with a folder picker for sources_dir — the audio-archive
folder the worker copies/moves source audio into after transcription. Empty
= don't archive (the worker already skips when sources_dir is unset). No
migration/stats like Встречи: it's just a destination.

- settings_builder.py: build_sources_section (row 5); Словари bumped 5→6
- settings.py: wire into tab 1; _on_pick_sources_folder / _on_clear_sources_folder

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 3: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Full test suite**

Run: `python -m pytest`
Expected: green. Baseline before this PR ≈ 1012 passing (PR-C1) + project-foundation tests; this PR adds 12 source-slice tests (8 + 4). Confirm **no failures** and **no errors**, only the 2 pre-existing skips. (On Windows PowerShell, do not pipe/redirect pytest output — it can swallow the summary line; read the dot-lines or use `--junitxml`.)

- [ ] **Step 2: Full ruff**

Run: `python -m ruff check .`
Expected: clean.

- [ ] **Step 3: Broad-except ratchet sanity (no new broad excepts)**

Run: `python -m pytest tests/test_broad_except_ratchet.py -v`
Expected: PASS. This PR adds **zero** `except Exception` (the `<Destroy>` handler and the selector methods use no try/except), so the ratchet counts are unchanged — do not edit the baseline.

- [ ] **Step 4: Manual smoke checklist (real keys, Windows — record for the PR body)**

Not automated (source-slice tests can't instantiate the App). Verify by hand before opening the PR:
- [ ] Launch app → run-card shows a «Проект» dropdown; default is «Без проекта» (or the last-used project if `last_project_id` was set).
- [ ] Open Справочники → add a project → close → dropdown now lists it (no restart).
- [ ] Pick a project → record a short clip → meeting folder lands under `<meetings_dir>/<project name>/`; relaunch → that project is still selected.
- [ ] Settings → tab «Транскрипция» → «Архив аудио»: pick a folder → it persists (reopen Settings shows it); record/«Выбрать файл» → audio copy/move appears in that folder. «Очистить» → field empties → next run archives nothing (no error).
- [ ] «Без проекта» selected → meeting lands in `<meetings_dir>/` root (no project subfolder).

---

## Self-Review (completed during planning)

**Spec coverage (§11 + storage/config rows):**
- §11 "Project selector (default `last_project_id`); inbox files default no-project" → Task 1 (selector + `last_project_id`; «Без проекта» = the no-project path inbox will reuse in C3). ✓
- §11 config paths `meetings_dir`/`sources_dir`/`inbox_dir` → `meetings_dir` already shipped (Встречи section); `sources_dir` → Task 2; `inbox_dir` explicitly deferred to C3 (documented in Scope). ✓
- Spec table row "config.example.json: + last_project_id" → Task 1 Step 4. ✓
- Out-of-scope §11 items (indicator elapsed/position, cost hint, «Встречи» queue+history view) → not this PR (C1 shipped the basic indicator; rest is C2/later). ✓

**Placeholder scan:** every code step shows full code; no TBD/"handle edge cases"/"similar to". ✓

**Type/name consistency:** `NO_PROJECT_LABEL` (constants → builder import → queue_mixin import); `_project_var`/`_project_menu` (builder → queue_mixin); `_project_choices` (set in `_refresh_project_selector`, read in `_build_options` + `_on_project_changed` via `getattr(..., {})`); `_refresh_project_selector` (defined queue_mixin; called __init__ + dialogs_mixin); `_on_project_changed` (defined queue_mixin; referenced builder menu command); `_sources_path_var`/`_on_pick_sources_folder`/`_on_clear_sources_folder` (settings_builder ↔ settings); `build_sources_section` (settings_builder def ↔ settings call). ✓
