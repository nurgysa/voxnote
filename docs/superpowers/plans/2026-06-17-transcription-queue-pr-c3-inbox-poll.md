# Transcription Queue PR-C3 — Inbox Poll — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-ingest phone-dropped audio: an App `after`-loop polls a Google Drive-synced `inbox/` folder via the existing `InboxWatcher` and enqueues newly-arrived files (no project); add an `inbox_dir` folder picker in Settings. This is the final slice of the transcription-queue chain (A→C).

**Architecture:** The App holds one `InboxWatcher` and a recurring `self.after(_INBOX_POLL_MS, self._inbox_tick)` loop (the queue's single `on_change` is already taken by the main bar). Each tick: rebuild the watcher if `config["inbox_dir"]` changed (Settings edit takes effect without a restart), `poll()` (size-stable debounce, already unit-tested), dedup the ready paths against the live queue snapshot by `audio_path`, then `enqueue` each with `source="inbox"` + `project_id=None` (no interactive key dialog — a missing key surfaces as a queue ERROR item). The Settings field mirrors the existing «Архив аудио» picker.

**Tech Stack:** Python 3.10+, CustomTkinter (`self.after`/`after_cancel`, `filedialog.askdirectory`), `processing.inbox_watcher.InboxWatcher` (PR-A/B1), `ProcessingQueue.enqueue/snapshot`. The poll wiring is **source-slice** tested (no `ui.app`/Tk import — PortAudio crashes Linux CI); the watcher's poll/debounce already has real unit tests in `tests/test_inbox_watcher.py`.

**Scope — IN:** App inbox poll-tick (rebuild-on-change + poll + dedup + inbox-enqueue + teardown-cancel); Settings «Приём с телефона» (`inbox_dir`) picker.
**Scope — OUT (deferred hygiene, unchanged):** prune DONE items from `queue.json`; dismiss a stuck ERROR item; cost hint at enqueue. After C3 the A→C chain is complete.

**Decisions locked in brainstorming:** poll interval **10 s** (≈2 polls = ~10–20 s ingest latency after Drive sync finishes); **rebuild the watcher live** when `inbox_dir` changes (no restart); **dedup guard** — skip ready paths already present in the queue snapshot by `audio_path` (covers a restart while an inbox file is queued-but-not-yet-moved).

**Invariants:** `encoding="utf-8"` on text I/O; Russian UI / English code+comments; narrow `except` only (the one new `except tk.TclError` teardown guard is narrow — no `except Exception`); UI/poll wiring is source-slice (no `ui.app` import); no `requirements.txt` change; no local CUDA/torch/pyannote imports. `inbox_dir` already exists in `config.example.json` (added in PR-B2) — no config or CLAUDE.md/AGENTS.md change.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `ui/app/queue_mixin.py` | Modify | `_INBOX_POLL_MS` class attr; `_inbox_tick` (rebuild-on-change → poll → dedup → inbox-enqueue → reschedule, `tk.TclError`-guarded); `_on_app_close` also cancels the inbox tick |
| `ui/app/__init__.py` | Modify | import `InboxWatcher`; after the queue is built, construct the watcher + schedule the first tick |
| `ui/dialogs/settings_builder.py` | Modify | new `build_inbox_section` (row 6); bump `build_dictionaries_section` row 6→7 |
| `ui/dialogs/settings.py` | Modify | wire `build_inbox_section` into tab 1; add `_on_pick_inbox_folder` + `_on_clear_inbox_folder` |
| `tests/test_ui_inbox_poll.py` | Create | source-slice: watcher built + first tick scheduled; tick rebuild/poll/dedup/enqueue/reschedule; close cancels |
| `tests/test_settings_inbox_section.py` | Create | source-slice: `build_inbox_section` + «Приём с телефона» + handlers write `inbox_dir` + `askdirectory` |

---

## Setup: feature branch

- [ ] **Step 1: Branch off up-to-date main**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/transcription-queue-pr-c3
```

Expected: on `feat/transcription-queue-pr-c3`, clean tree (main tip is the PR-C2 squash `0b8717f`).

---

## Task 1: App inbox poll

**Files:**
- Modify: `ui/app/queue_mixin.py`
- Modify: `ui/app/__init__.py`
- Test: `tests/test_ui_inbox_poll.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_inbox_poll.py`:

```python
"""Source-slice wiring tests for the PR-C3 inbox poll.

No ui.app/Tk import — customtkinter pulls PortAudio and crashes Linux CI.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INIT = (_ROOT / "ui" / "app" / "__init__.py").read_text(encoding="utf-8")
_QUEUE = (_ROOT / "ui" / "app" / "queue_mixin.py").read_text(encoding="utf-8")


def test_init_builds_inbox_watcher_and_schedules_tick():
    assert "from processing.inbox_watcher import InboxWatcher" in _INIT
    assert "InboxWatcher(" in _INIT
    assert "self.after(self._INBOX_POLL_MS, self._inbox_tick)" in _INIT


def test_queue_mixin_has_inbox_tick_and_interval():
    assert "def _inbox_tick" in _QUEUE
    assert "_INBOX_POLL_MS" in _QUEUE


def test_inbox_tick_polls_and_rebuilds_on_change():
    # the tick re-reads inbox_dir and rebuilds the watcher when it changed
    assert "self._inbox_watcher.poll()" in _QUEUE
    assert "InboxWatcher(" in _QUEUE
    assert 'self._config.get("inbox_dir")' in _QUEUE


def test_inbox_tick_dedups_against_snapshot():
    assert "self._queue.snapshot()" in _QUEUE
    assert "audio_path" in _QUEUE


def test_inbox_enqueue_is_no_project_inbox_source():
    assert '_build_options("inbox")' in _QUEUE
    assert 'options["project_id"] = None' in _QUEUE


def test_inbox_tick_reschedules_and_guards_teardown():
    assert "except tk.TclError" in _QUEUE
    assert "self.after(self._INBOX_POLL_MS, self._inbox_tick)" in _QUEUE


def test_on_app_close_cancels_inbox_tick():
    assert "after_cancel(self._inbox_after_id)" in _QUEUE
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_ui_inbox_poll.py -v`
Expected: FAIL — none of the inbox wiring exists yet.

- [ ] **Step 3: Add the interval constant + `_inbox_tick` + close-cancel in `queue_mixin.py`**

3a. Add the `InboxWatcher` import. The current import block (lines 22–30) is:

```python
import os
import tkinter as tk
from tkinter import messagebox

from processing.model import StageStatus
from theme import GREEN, RED, TEXT_SECONDARY
from utils import save_config

from .constants import LANGUAGES, NO_PROJECT_LABEL, SPEAKER_COUNTS
```

Add the watcher import after the `StageStatus` import:

```python
import os
import tkinter as tk
from tkinter import messagebox

from processing.inbox_watcher import InboxWatcher
from processing.model import StageStatus
from theme import GREEN, RED, TEXT_SECONDARY
from utils import save_config

from .constants import LANGUAGES, NO_PROJECT_LABEL, SPEAKER_COUNTS
```

3b. Add the poll-interval class attribute. Right after the class docstring line (`    """Enqueue + reactive indicator over the App's ProcessingQueue."""`), add:

```python
class QueueMixin:
    """Enqueue + reactive indicator over the App's ProcessingQueue."""

    # Inbox poll cadence. The watcher debounces on size-stability across two
    # polls, so effective enqueue latency after a Drive sync finishes is ≈2×.
    _INBOX_POLL_MS = 10_000
```

3c. Add `_inbox_tick` immediately AFTER `_refresh_queue_indicator` and BEFORE `_on_app_close` (insert between them):

```python
    def _inbox_tick(self) -> None:
        """Poll the Drive inbox folder and enqueue newly-arrived audio.

        Runs on the Tk thread via after(...). Rebuilds the watcher when the
        configured inbox_dir changed (a Settings edit takes effect without a
        restart). Dedups ready paths against the live queue snapshot by
        audio_path so a restart mid-queue (file still in inbox, not yet moved by
        the worker) can't enqueue it twice. Inbox items are no-project and skip
        the interactive API-key dialog — a missing key surfaces as a queue ERROR
        item (visible in «Встречи»), not a popup with no user to dismiss it."""
        try:
            current = (self._config.get("inbox_dir") or "").strip() or None
            if current != self._inbox_dir:
                self._inbox_dir = current
                self._inbox_watcher = InboxWatcher(current)
            ready = self._inbox_watcher.poll()
            if ready:
                queued = {it.audio_path for it in self._queue.snapshot()}
                added = 0
                for path in ready:
                    if path in queued:
                        continue
                    options = self._build_options("inbox")
                    options["project_id"] = None
                    self._queue.enqueue(path, options)
                    added += 1
                if added:
                    self._lbl_status.configure(
                        text=f"Из inbox добавлено: {added}", text_color=GREEN,
                    )
                    self._refresh_queue_indicator()
            self._inbox_after_id = self.after(self._INBOX_POLL_MS, self._inbox_tick)
        except tk.TclError:
            self._inbox_after_id = None  # window destroyed mid-tick — stop the loop
```

3d. Replace `_on_app_close` (currently lines 139–142) to also cancel the inbox tick:

```python
    def _on_app_close(self) -> None:
        """Stop the queue's daemon thread + the inbox poll, then close."""
        if self._inbox_after_id is not None:
            try:
                self.after_cancel(self._inbox_after_id)
            except tk.TclError:
                pass
            self._inbox_after_id = None
        self._queue.stop()
        self.destroy()
```

- [ ] **Step 4: Construct the watcher + schedule the first tick in `__init__.py`**

4a. Add the import. The current top imports include `from processing.worker import ProcessingQueue` (line 13). Add the watcher import right after it:

```python
from processing.inbox_watcher import InboxWatcher
from processing.worker import ProcessingQueue
```

4b. The construction block ends with `self._refresh_queue_indicator()` (line 236). Immediately after that line (before the first-launch migration comment), add:

```python
        self._refresh_queue_indicator()

        # Inbox poll (PR-C3): a Google Drive-synced inbox/ folder where the
        # phone drops audio. The watcher debounces on size-stability; the tick
        # enqueues ready files (no-project). The event loop only starts after
        # __init__ returns, so _inbox_after_id is set before _on_app_close can
        # ever fire. inbox_dir empty → poll() is a no-op (feature off).
        self._inbox_dir = (self._config.get("inbox_dir") or "").strip() or None
        self._inbox_watcher = InboxWatcher(self._inbox_dir)
        self._inbox_after_id = self.after(self._INBOX_POLL_MS, self._inbox_tick)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_ui_inbox_poll.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Lint + commit**

```bash
python -m ruff check ui/app/queue_mixin.py ui/app/__init__.py tests/test_ui_inbox_poll.py
git add ui/app/queue_mixin.py ui/app/__init__.py tests/test_ui_inbox_poll.py
git commit -F- <<'EOF'
feat(ui): transcription-queue PR-C3 — inbox poll

The App polls a Google Drive-synced inbox/ folder (InboxWatcher, PR-A/B1) on a
10s after-loop and auto-enqueues phone-dropped audio as no-project inbox items.
The tick rebuilds the watcher when config[inbox_dir] changes (Settings edit, no
restart), dedups ready paths against the live queue snapshot by audio_path
(guards a restart mid-queue), and skips the interactive key dialog (a missing
key surfaces as a queue ERROR item). _on_app_close cancels the tick.

Source-slice wiring tests; the watcher's poll/debounce is already unit-tested.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 2: Settings «Приём с телефона» (inbox_dir)

**Files:**
- Modify: `ui/dialogs/settings_builder.py` (new `build_inbox_section`; bump dictionaries row)
- Modify: `ui/dialogs/settings.py` (orchestrator line; two handlers)
- Test: `tests/test_settings_inbox_section.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings_inbox_section.py`:

```python
"""Source-text checks for the «Приём с телефона» (inbox_dir) section in Settings."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = _ROOT / "ui" / "dialogs" / "settings.py"
BUILDER_PATH = _ROOT / "ui" / "dialogs" / "settings_builder.py"


def test_inbox_section_card_exists():
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert "def build_inbox_section" in src
    assert '"Приём с телефона"' in src or "'Приём с телефона'" in src


def test_inbox_section_wired_into_tab():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "build_inbox_section" in src


def test_inbox_handlers_write_config_key():
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    for name in ("_on_pick_inbox_folder", "_on_clear_inbox_folder"):
        assert f"def {name}" in src
    assert '"inbox_dir"' in src
    assert "askdirectory" in src


def test_sources_section_still_present():
    # «Архив аудио» (PR-C1b) must survive untouched (regression guard).
    src = BUILDER_PATH.read_text(encoding="utf-8")
    assert '"Архив аудио"' in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_settings_inbox_section.py -v`
Expected: FAIL — `build_inbox_section` / handlers not present.

- [ ] **Step 3: Add `build_inbox_section` and bump the dictionaries row in `settings_builder.py`**

3a. Insert `build_inbox_section` immediately after `build_sources_section` ends (after its last line `).grid(row=2, column=3, padx=(4, 4), pady=(0, 6))`, before `def build_dictionaries_section`):

```python
def build_inbox_section(dialog, parent) -> None:
    """Inbox folder picker (inbox_dir).

    A Google Drive-synced inbox/ folder where the phone drops audio; the App
    polls it and auto-enqueues new files (no project). Empty = don't watch.
    Point this at a DIFFERENT folder than «Архив аудио»/«Встречи» — the worker
    moves processed inbox files into the archive, so a shared folder would
    re-scan them. No migration/stats: it's just a watched source.
    """
    section = section_card(dialog, parent, "Приём с телефона", row=6)

    label(
        section,
        "Папка-приёмник аудио с телефона (Google Drive → inbox). "
        "Пусто = не следить.",
        anchor="w",
    ).grid(row=0, column=0, columnspan=4, padx=4, pady=(4, 6), sticky="w")

    dialog._inbox_path_var = ctk.StringVar(
        value=(dialog._parent._config.get("inbox_dir") or ""),
    )
    dialog._inbox_entry = ctk.CTkEntry(
        section, textvariable=dialog._inbox_path_var,
        height=36, corner_radius=10,
        border_color=BORDER, border_width=1,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=12),
        state="readonly",
    )
    dialog._inbox_entry.grid(
        row=1, column=0, columnspan=3, padx=4, pady=6, sticky="ew",
    )

    tonal_button(
        section, text="\U0001f4c1 Выбрать",
        command=dialog._on_pick_inbox_folder, width=130,
    ).grid(row=1, column=3, padx=(4, 4), pady=6)

    tonal_button(
        section, text="Очистить",
        command=dialog._on_clear_inbox_folder, width=120,
    ).grid(row=2, column=3, padx=(4, 4), pady=(0, 6))
```

(All names — `section_card`, `label`, `ctk`, `BORDER`, `INPUT_BG`, `TEXT_PRIMARY`, `FONT`, `tonal_button` — are already imported at the top of settings_builder.py. No new imports.)

3b. Bump the dictionaries section row 6→7. In `build_dictionaries_section`:

```python
def build_dictionaries_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Словари", row=7)
```

- [ ] **Step 4: Wire `build_inbox_section` into tab 1 in `settings.py`**

The tab-1 orchestrator currently has (lines 156–157):

```python
        settings_builder.build_sources_section(self, scroll_transcription)
        settings_builder.build_dictionaries_section(self, scroll_transcription)
```

Insert the inbox call between them:

```python
        settings_builder.build_sources_section(self, scroll_transcription)
        settings_builder.build_inbox_section(self, scroll_transcription)
        settings_builder.build_dictionaries_section(self, scroll_transcription)
```

- [ ] **Step 5: Add the two handlers in `settings.py`**

Insert after `_on_clear_sources_folder` (after its last line `self._sources_path_var.set("")`, before `_refresh_summaries`):

```python
    def _on_pick_inbox_folder(self) -> None:
        """«Выбрать» for the phone inbox — native dir picker, then persist."""
        chosen = filedialog.askdirectory(
            title="Папка-приёмник аудио с телефона",
            initialdir=self._inbox_path_var.get() or None,
            parent=self,
        )
        if not chosen:
            return  # user cancelled
        normalized = os.path.abspath(chosen)
        self._parent._config["inbox_dir"] = normalized
        save_config(self._parent._config)
        self._inbox_path_var.set(normalized)

    def _on_clear_inbox_folder(self) -> None:
        """«Очистить» — empty inbox_dir stops the poll (the App tick rebuilds)."""
        self._parent._config["inbox_dir"] = ""
        save_config(self._parent._config)
        self._inbox_path_var.set("")
```

(`os`, `filedialog`, `save_config` are already imported in settings.py.)

- [ ] **Step 6: Run the inbox-section test to verify it passes**

Run: `python -m pytest tests/test_settings_inbox_section.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Confirm the «Архив аудио» / «Встречи» section tests still pass**

Run: `python -m pytest tests/test_settings_sources_section.py tests/test_settings_dialog_meetings_section.py -q`
Expected: all PASS (those sections are untouched; only a new section was inserted + dictionaries row bumped).

- [ ] **Step 8: Lint + commit**

```bash
python -m ruff check ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_settings_inbox_section.py
git add ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_settings_inbox_section.py
git commit -F- <<'EOF'
feat(ui): transcription-queue PR-C3 — Settings «Приём с телефона» (inbox_dir)

New tab-1 section with a folder picker for inbox_dir — the Google Drive-synced
inbox/ folder the App polls for phone-dropped audio. Empty = don't watch (the
poll no-ops). Mirrors the «Архив аудио» picker; the App tick picks up a change
without a restart. Словари bumped row 6→7.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 3: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Full test suite**

Run: `python -m pytest`
Expected: green. This PR adds 7 (inbox poll) + 4 (inbox section) = 11 tests on top of the post-C2 baseline (1051). Confirm **no failures/errors**, only the 2 pre-existing skips. (Windows PowerShell: don't pipe/redirect pytest output — read the dot-lines or use `--junitxml`.)

- [ ] **Step 2: Full ruff**

Run: `python -m ruff check .`
Expected: clean.

- [ ] **Step 3: Broad-except ratchet (no `except Exception` added)**

Run: `python -m pytest tests/test_broad_except_ratchet.py -v`
Expected: PASS. This PR adds only a narrow `except tk.TclError` (the inbox tick's teardown guard) — no broad except. Do not edit the baseline.

- [ ] **Step 4: Manual smoke checklist (real keys, Windows — record for the PR body)**

Not automatable (source-slice tests can't run the poll):
- [ ] Settings → «Приём с телефона»: pick a folder → it persists (reopen shows it); «Очистить» empties it.
- [ ] With `inbox_dir` set, drop an audio file into it → within ~10–20 s a meeting appears in the queue/«Встречи» (no project), status «в очереди» → «идёт» → «готово»; the source file is moved out of inbox into the archive on success.
- [ ] Change `inbox_dir` in Settings while the app runs → new folder is watched without a restart; old folder no longer polled.
- [ ] Empty `inbox_dir` → nothing is ingested (no errors logged).
- [ ] Закрытие окна не оставляет висящий таймер (no errors in the log on exit).

---

## Self-Review (completed during planning)

**Spec coverage (§11 + storage rows):**
- §11 "inbox files auto-enqueue via the App poll tick" → Task 1 (`_inbox_tick` → `enqueue`). ✓
- §11 "inbox files default no-project" → `options["project_id"] = None`. ✓
- Spec storage row `inbox_dir` → Drive `inbox` (user-set) → Task 2 picker (config key already present from PR-B2). ✓
- §Low-stakes defaults "inbox/ flat + no-project (triage later)" → `scan_inbox` is flat (direct children only, existing) + no-project enqueue. ✓
- Deferred per scope (DONE-pruning, ERROR dismiss, cost hint) — unchanged. ✓

**Placeholder scan:** every code step shows full code; no TBD/"handle edge cases"/"similar to". ✓

**Type/name consistency:** `_INBOX_POLL_MS` (QueueMixin class attr ↔ `__init__` first schedule ↔ `_inbox_tick` reschedule); `_inbox_dir` / `_inbox_watcher` / `_inbox_after_id` (set in `__init__` ↔ read/reassigned in `_inbox_tick` ↔ cancelled in `_on_app_close`); `InboxWatcher` (imported in both `__init__` and `queue_mixin`); `_build_options("inbox")` + `options["project_id"] = None`; `build_inbox_section` (settings_builder def ↔ settings call); `_inbox_path_var` / `_on_pick_inbox_folder` / `_on_clear_inbox_folder` (settings_builder ↔ settings). ✓
