# Transcription Queue PR-C2 — «Встречи» = Queue + History — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the «Встречи» dialog into a live queue + history view: rows derived from `processing.store.build_view(meetings_dir, queue.snapshot())`, grouped by project, each showing status (в очереди / идёт `mm:ss` / готово / ошибка) + Hermes-progress badges (protocol/tasks), with «Открыть в Obsidian», «Повторить» (on error), and the existing view/search/delete.

**Architecture:** Presentation logic (group-by-project, status text, elapsed, queue position) lives in a new **headless** module `ui/dialogs/meetings_view.py` so it gets real unit tests (the dialog itself can't be imported under Linux CI — customtkinter → PortAudio). `ui/dialogs/meetings.py` becomes the thin Tk renderer over those helpers + `build_view`. The dialog refreshes via its own `self.after(1000, …)` poll (the queue's single `on_change` is already taken by the main bar) which both re-renders on a status-signature change and ticks the live `mm:ss` of the one RUNNING row. A new `QueueItem.started_at` (stamped by the worker on the RUNNING transition) backs the accurate `mm:ss`.

**Tech Stack:** Python 3.10+, CustomTkinter (CTkToplevel/CTkScrollableFrame, `self.after`/`after_cancel`), `processing.store.build_view` / `processing.model.QueueItem` / `ProcessingQueue.snapshot()`/`.retry()`, `os.startfile`. Pure-helper + model/worker changes get **real** unit tests; the dialog rework is **source-slice** (read module text, assert substrings — no `ui.app`/Tk import).

**Scope — IN:** `started_at` (model + worker stamp); pure `meetings_view.py` helpers; the `meetings.py` rework (build_view data source, project grouping, status pill + Hermes badges, «Просмотр»/«Открыть в Obsidian»/✕ on DONE, «Повторить» on ERROR, live poll, search retained); pass the queue into the dialog.
**Scope — OUT (later):** dismiss/remove a stuck ERROR item from the queue (no `queue.remove` API — don't grow the worker here); inbox poll + `inbox_dir` field → PR-C3; cost hint at enqueue → later. The old «📂 Папка» button is folded into «Открыть в Obsidian» (which falls back to opening the folder when `transcript.md` is absent).

**Decisions locked in brainstorming:** elapsed timer = **accurate** (`started_at` + worker stamp); «Открыть в Obsidian» = `os.startfile(transcript.md)` (opens whatever is the default `.md` handler — Obsidian if associated, else the default editor; folder-open fallback when the file is missing); **search retained**.

**Invariants (must hold):** `encoding="utf-8"` on all text I/O; Russian UI / English code+comments; narrow `except` only (the one new `except tk.TclError` poll-shutdown guard is narrow — no `except Exception`); UI/dialog tests are source-slice (no `ui.app`/Tk import); no `requirements.txt` change; no local CUDA/torch/pyannote imports. The rework MUST keep these strings that existing tests pin in `meetings.py`: window title `"Встречи"`, count label `Встреч:`, empty state `Нет встреч`, `class MeetingsDialog`, `class MeetingViewerDialog`, the `_read_transcript` md→txt fallback (`transcript.md` + `transcript.txt`), `initialfile="transcript.md"`; and **no** `«митинг»`/`«Митинг»` anywhere under `ui/`.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `processing/model.py` | Modify | + `started_at: str \| None` field (+ to_dict/from_dict) |
| `processing/worker.py` | Modify | stamp `started_at` in `_set_status` on the RUNNING transition |
| `ui/dialogs/meetings_view.py` | Create | **headless** presentation helpers: `format_elapsed`, `queue_position`, `format_status`, `group_by_project`, `NO_PROJECT_LABEL` |
| `ui/dialogs/meetings.py` | Rewrite | Tk renderer over `build_view` + the helpers: grouped rows, status pill + badges, actions, live poll; viewer adapted to `QueueItem` |
| `ui/app/dialogs_mixin.py` | Modify | `_open_meetings_dialog` passes `queue=self._queue` |
| `tests/test_processing_started_at.py` | Create | real unit: model round-trip + worker stamps on RUNNING |
| `tests/test_meetings_view.py` | Create | real unit: the 4 pure helpers |
| `tests/test_meetings_dialog_queue.py` | Create | source-slice: rework wiring + preserved-string guards |

---

## Setup: feature branch

- [ ] **Step 1: Branch off up-to-date main**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/transcription-queue-pr-c2
```

Expected: on `feat/transcription-queue-pr-c2`, clean tree (main tip is the PR-C1b squash `c616648`).

---

## Task 1: `started_at` on the model + worker stamp

**Files:**
- Modify: `processing/model.py` (field + to_dict + from_dict)
- Modify: `processing/worker.py` (`_set_status`)
- Test: `tests/test_processing_started_at.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_processing_started_at.py`:

```python
"""started_at: carried by the model + stamped by the worker on RUNNING."""
from __future__ import annotations

from processing.model import QueueItem, StageStatus
from processing.worker import ProcessingQueue


def _queue(tmp_path, **over):
    kwargs = dict(
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {},
        resolve_project=lambda pid: None,
        queue_path=str(tmp_path / "queue.json"),
        on_change=None,
    )
    kwargs.update(over)
    return ProcessingQueue(**kwargs)


def test_started_at_roundtrips():
    item = QueueItem(
        id="x", audio_path="", title="t", created_at="",
        started_at="2026-06-16T20:00:00",
    )
    assert item.to_dict()["started_at"] == "2026-06-16T20:00:00"
    assert QueueItem.from_dict(item.to_dict()).started_at == "2026-06-16T20:00:00"


def test_started_at_defaults_none():
    assert QueueItem.from_dict({"id": "x"}).started_at is None


def test_set_status_running_stamps_started_at(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    item = q._items[0]
    assert item.started_at is None
    q._set_status(item, StageStatus.RUNNING)
    assert item.started_at  # stamped (non-empty ISO string)
    assert item.status == StageStatus.RUNNING


def test_set_status_non_running_does_not_stamp(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    item = q._items[0]
    q._set_status(item, StageStatus.DONE)
    assert item.started_at is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_processing_started_at.py -v`
Expected: FAIL — `started_at` not a field yet / not stamped.

- [ ] **Step 3: Add the `started_at` field to the model**

In `processing/model.py`, add the field right after the `status` field (line 40 `status: StageStatus = StageStatus.PENDING`):

```python
    status: StageStatus = StageStatus.PENDING
    started_at: str | None = None    # ISO; stamped when status → RUNNING (mm:ss)
```

In `to_dict` (after the `"status": self.status.value,` line), add:

```python
            "status": self.status.value,
            "started_at": self.started_at,
```

In `from_dict` (after the `status=status,` line), add:

```python
            status=status,
            started_at=d.get("started_at"),
```

- [ ] **Step 4: Stamp `started_at` in the worker's `_set_status`**

In `processing/worker.py`, `_set_status` (lines 144-151), add the stamp inside the lock when transitioning to RUNNING:

```python
    def _set_status(
        self, item: QueueItem, status: StageStatus, *, error_message: str | None = None
    ) -> None:
        with self._lock:
            item.status = status
            item.error_message = error_message
            if status == StageStatus.RUNNING:
                item.started_at = datetime.now().isoformat(timespec="seconds")
            self._persist_locked()
        self._notify()
```

(`datetime` is already imported in worker.py.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_processing_started_at.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the existing processing tests (no regression)**

Run: `python -m pytest tests/test_processing_model.py tests/test_processing_worker.py tests/test_processing_store.py -q`
Expected: all PASS (the new field defaults to None; round-trip + key-presence tests are unaffected).

- [ ] **Step 7: Lint + commit**

```bash
python -m ruff check processing/ tests/test_processing_started_at.py
git add processing/model.py processing/worker.py tests/test_processing_started_at.py
git commit -F- <<'EOF'
feat(processing): QueueItem.started_at — stamped on RUNNING for the mm:ss timer

The «Встречи» view (PR-C2) shows accurate processing-elapsed for the in-flight
item. Add started_at to the model (round-trips; defaults None, back-compat with
existing queue.json) and stamp it in _set_status on the RUNNING transition.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 2: headless presentation helpers `ui/dialogs/meetings_view.py`

**Files:**
- Create: `ui/dialogs/meetings_view.py`
- Test: `tests/test_meetings_view.py` (create)

`ui/__init__.py` is docstring-only and `ui/dialogs/__init__.py` is empty, so `import ui.dialogs.meetings_view` pulls no Tk — these are real unit tests.

- [ ] **Step 1: Write the failing test**

Create `tests/test_meetings_view.py`:

```python
"""Unit tests for the headless «Встречи» presentation helpers."""
from __future__ import annotations

from processing.model import QueueItem, StageStatus
from ui.dialogs.meetings_view import (
    NO_PROJECT_LABEL,
    format_elapsed,
    format_status,
    group_by_project,
    queue_position,
)


def _item(id="i", status=StageStatus.PENDING, auto=True, project_id=None, started_at=None):
    return QueueItem(
        id=id, audio_path="", title=id, created_at="",
        status=status, auto=auto, project_id=project_id, started_at=started_at,
    )


def test_format_elapsed_minutes():
    assert format_elapsed("2026-06-16T20:00:00", "2026-06-16T20:01:05") == "01:05"


def test_format_elapsed_hours():
    assert format_elapsed("2026-06-16T20:00:00", "2026-06-16T21:02:03") == "1:02:03"


def test_format_elapsed_unparseable_or_missing():
    assert format_elapsed(None, "2026-06-16T20:00:00") == ""
    assert format_elapsed("bad", "also-bad") == ""


def test_format_elapsed_negative_clamps():
    assert format_elapsed("2026-06-16T20:00:10", "2026-06-16T20:00:00") == "00:00"


def test_queue_position_counts_active_pending_only():
    a = _item("a", StageStatus.DONE, auto=False)
    b = _item("b", StageStatus.PENDING)
    c = _item("c", StageStatus.RUNNING)
    d = _item("d", StageStatus.PENDING)
    rows = [a, b, c, d]
    assert queue_position(rows, b) == 1
    assert queue_position(rows, d) == 2
    assert queue_position(rows, c) is None
    assert queue_position(rows, a) is None


def test_format_status_running_with_elapsed():
    it = _item(status=StageStatus.RUNNING, started_at="2026-06-16T20:00:00")
    assert format_status(it, "2026-06-16T20:02:30", None) == ("идёт 02:30", "running")


def test_format_status_running_without_started_at():
    it = _item(status=StageStatus.RUNNING)
    assert format_status(it, "2026-06-16T20:00:00", None) == ("идёт…", "running")


def test_format_status_pending_positions():
    it = _item(status=StageStatus.PENDING)
    assert format_status(it, "x", 1) == ("в очереди", "pending")
    assert format_status(it, "x", 3) == ("в очереди (3-й)", "pending")


def test_format_status_done_and_error():
    assert format_status(_item(status=StageStatus.DONE), "x", None) == ("готово", "done")
    assert format_status(_item(status=StageStatus.ERROR), "x", None) == ("ошибка", "error")


def test_group_by_project_orders_no_project_last():
    name_of = lambda pid: {"p1": "Alpha", "p2": "Beta"}.get(pid, NO_PROJECT_LABEL)
    rows = [
        _item("a", project_id="p1"),
        _item("b", project_id=None),
        _item("c", project_id="p2"),
        _item("d", project_id="p1"),
    ]
    groups = group_by_project(rows, name_of)
    assert [g[0] for g in groups] == ["Alpha", "Beta", NO_PROJECT_LABEL]
    assert [r.id for r in groups[0][1]] == ["a", "d"]
    assert [r.id for r in groups[2][1]] == ["b"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_meetings_view.py -v`
Expected: FAIL — `ui.dialogs.meetings_view` does not exist.

- [ ] **Step 3: Create the helper module**

Create `ui/dialogs/meetings_view.py`:

```python
"""Pure presentation helpers for the «Встречи» dialog (no Tk).

Split out so the grouping/status/elapsed logic gets real unit tests — the
dialog itself (ui/dialogs/meetings.py) can't be imported under Linux CI
(customtkinter → PortAudio). The dialog is the thin Tk renderer over these.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from processing.model import QueueItem, StageStatus

NO_PROJECT_LABEL = "Без проекта"


def format_elapsed(started_at: str | None, now_iso: str) -> str:
    """'mm:ss' (or 'h:mm:ss' past an hour) between started_at and now_iso.
    Empty string when either timestamp is missing/unparseable; negative clamps."""
    try:
        start = datetime.fromisoformat(started_at)
        now = datetime.fromisoformat(now_iso)
    except (ValueError, TypeError):
        return ""
    total = max(0, int((now - start).total_seconds()))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def queue_position(rows: list[QueueItem], item: QueueItem) -> int | None:
    """1-based position of `item` among the active (auto) PENDING rows, in
    order; None if `item` is not an active PENDING row."""
    pending = [r for r in rows if r.auto and r.status == StageStatus.PENDING]
    for i, row in enumerate(pending, start=1):
        if row.id == item.id:
            return i
    return None


def format_status(
    item: QueueItem, now_iso: str, position: int | None
) -> tuple[str, str]:
    """(display text, color_key) for a row. color_key is one of
    'pending'/'running'/'done'/'error' — the dialog maps it to a theme color."""
    if item.status == StageStatus.RUNNING:
        elapsed = format_elapsed(item.started_at, now_iso)
        return (f"идёт {elapsed}" if elapsed else "идёт…", "running")
    if item.status == StageStatus.ERROR:
        return ("ошибка", "error")
    if item.status == StageStatus.DONE:
        return ("готово", "done")
    if position and position > 1:
        return (f"в очереди ({position}-й)", "pending")
    return ("в очереди", "pending")


def group_by_project(
    rows: list[QueueItem], name_of: Callable[[str | None], str]
) -> list[tuple[str, list[QueueItem]]]:
    """Group rows by project display name (name_of(project_id)), preserving each
    group's first-appearance order, with the «Без проекта» group forced last."""
    groups: dict[str, list[QueueItem]] = {}
    order: list[str] = []
    for row in rows:
        name = name_of(row.project_id)
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(row)
    ordered = [n for n in order if n != NO_PROJECT_LABEL]
    if NO_PROJECT_LABEL in groups:
        ordered.append(NO_PROJECT_LABEL)
    return [(n, groups[n]) for n in ordered]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_meetings_view.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check ui/dialogs/meetings_view.py tests/test_meetings_view.py
git add ui/dialogs/meetings_view.py tests/test_meetings_view.py
git commit -F- <<'EOF'
feat(ui): headless «Встречи» presentation helpers (meetings_view)

format_elapsed / queue_position / format_status / group_by_project — pure, no
Tk, so the grouping/status/elapsed logic gets real unit tests (the dialog can't
be imported under Linux CI). meetings.py (PR-C2) becomes the Tk renderer over
these.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 3: rework `ui/dialogs/meetings.py` + wire the queue

**Files:**
- Rewrite: `ui/dialogs/meetings.py`
- Modify: `ui/app/dialogs_mixin.py` (`_open_meetings_dialog`)
- Test: `tests/test_meetings_dialog_queue.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_meetings_dialog_queue.py`:

```python
"""Source-slice wiring tests for the PR-C2 «Встречи» queue+history rework.

No ui.app/Tk import — customtkinter pulls PortAudio and crashes Linux CI.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MEET = (_ROOT / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
_MIXIN = (_ROOT / "ui" / "app" / "dialogs_mixin.py").read_text(encoding="utf-8")


def test_meetings_uses_build_view_and_snapshot():
    assert "build_view(" in _MEET
    assert "_queue.snapshot()" in _MEET
    assert "list_history_entries" not in _MEET  # old data source replaced


def test_meetings_imports_pure_view_helpers():
    assert "from ui.dialogs.meetings_view import" in _MEET
    for name in ("format_status", "group_by_project", "queue_position"):
        assert name in _MEET


def test_meetings_retry_wired_to_queue():
    assert "_queue.retry(" in _MEET
    assert "Повторить" in _MEET


def test_meetings_open_obsidian_uses_default_md_app():
    assert "_open_obsidian" in _MEET
    assert "startfile" in _MEET


def test_meetings_live_poll_with_cancel():
    assert ".after(" in _MEET
    assert "after_cancel" in _MEET
    assert "except tk.TclError" in _MEET  # post-destroy poll guard


def test_meetings_dialog_takes_queue():
    assert "def __init__(self, parent, on_load_to_main, queue)" in _MEET


def test_mixin_passes_queue_to_meetings_dialog():
    assert "MeetingsDialog(" in _MIXIN
    assert "queue=self._queue" in _MIXIN


def test_meetings_preserves_legacy_pinned_strings():
    # Guards the strings that test_meetings_dialog_rename / _transcript_md_extension pin.
    assert '"Встречи"' in _MEET
    assert "Встреч:" in _MEET
    assert "Нет встреч" in _MEET
    assert "class MeetingsDialog" in _MEET
    assert "class MeetingViewerDialog" in _MEET
    assert "transcript.md" in _MEET and "transcript.txt" in _MEET
    assert 'initialfile="transcript.md"' in _MEET
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_meetings_dialog_queue.py -v`
Expected: FAIL — none of the new wiring is present yet.

- [ ] **Step 3: Rewrite `ui/dialogs/meetings.py`**

Replace the entire contents of `ui/dialogs/meetings.py` with:

```python
"""Meetings browser — live queue + on-disk history + read-only viewer.

«Встречи» = queue + history (PR-C2): rows come from
processing.store.build_view (a disk scan overlaid with the live
ProcessingQueue snapshot), so an in-flight transcription shows its status
(в очереди / идёт mm:ss / готово / ошибка) next to finished meetings. Rows are
grouped by project; finished meetings carry Hermes-progress badges
(protocol/tasks) and open in Obsidian; errored items offer «Повторить».
Presentation logic lives in the headless ui.dialogs.meetings_view module
(unit-tested); this file is the Tk renderer. Renamed from history.py on
2026-05-28; terminology «Встречи» since 2026-06-11.
"""
from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from processing.model import StageStatus
from processing.store import build_view
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    BORDER,
    FONT,
    GREEN,
    INPUT_BG,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.dialogs.meetings_view import (
    NO_PROJECT_LABEL,
    format_status,
    group_by_project,
    queue_position,
)
from utils import (
    delete_history_entry,
    get_meetings_dir,
    open_in_explorer,
    save_transcript,
)

# color_key from meetings_view.format_status → theme color.
_STATUS_COLORS = {
    "pending": TEXT_SECONDARY,
    "running": GREEN,
    "done": GREEN,
    "error": RED,
}


def _read_transcript(folder_path: str) -> str:
    """Read transcript from a meeting folder. Empty string on failure.

    Tries transcript.md first (convention since 2026-05-28), falls back to
    transcript.txt for older meeting folders."""
    for filename in ("transcript.md", "transcript.txt"):
        path = os.path.join(folder_path, filename)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                continue
    return ""


class MeetingViewerDialog(ctk.CTkToplevel):
    """Read-only viewer for a single meeting's transcript."""

    def __init__(self, parent, item, on_load_to_main):
        super().__init__(parent)
        title = item.title or os.path.basename(item.meeting_folder or "Транскрипт")
        self.title(title)
        self.geometry("760x600")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._on_load_to_main = on_load_to_main
        self._item = item
        self._text = _read_transcript(item.meeting_folder or "")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text=title,
            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        textbox = ctk.CTkTextbox(
            self, wrap="word", corner_radius=12,
            fg_color=SURFACE, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        )
        textbox.grid(row=1, column=0, padx=16, pady=8, sticky="nsew")
        textbox.insert("1.0", self._text or "(transcript отсутствует или пуст)")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=16, pady=(4, 14), sticky="ew")
        footer.grid_columnconfigure(3, weight=1)

        ctk.CTkButton(
            footer, text="Копировать", width=120, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._copy,
        ).grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            footer, text="Сохранить как…", width=160, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._save_as,
        ).grid(row=0, column=1, padx=8)

        ctk.CTkButton(
            footer, text="В основное окно", width=170, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._load_to_main,
        ).grid(row=0, column=2, padx=8)

        ctk.CTkButton(
            footer, text="Закрыть", width=110, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._close,
        ).grid(row=0, column=3, sticky="e")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._text)

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить транскрипцию",
            defaultextension=".md",
            initialfile="transcript.md",
            filetypes=[("Markdown", "*.md"), ("Text files", "*.txt")],
            parent=self,
        )
        if path:
            save_transcript(self._text, path)

    def _load_to_main(self):
        audio_path = self._item.audio_path or None
        if not (audio_path and os.path.isfile(audio_path)):
            audio_path = None
        self._on_load_to_main(self._text, audio_path)
        self._close()

    def _close(self):
        self.grab_release()
        self.destroy()


class MeetingsDialog(ctk.CTkToplevel):
    """«Встречи» — live queue + on-disk history, grouped by project."""

    _TICK_MS = 1000

    def __init__(self, parent, on_load_to_main, queue):
        super().__init__(parent)
        self.title("Встречи")
        self.geometry("820x640")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()

        self._app = parent
        self._queue = queue
        self._on_load_to_main = on_load_to_main
        self._transcript_cache: dict[str, str] = {}
        self._after_id = None
        self._last_sig: tuple | None = None
        self._running_rows: list = []  # (item, status_label) for the live mm:ss tick

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Встречи",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.grid(row=1, column=0, padx=16, pady=(8, 4), sticky="ew")
        search_frame.grid_columnconfigure(0, weight=1)
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._render())
        ctk.CTkEntry(
            search_frame, textvariable=self._search_var, height=36,
            corner_radius=10, border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
            placeholder_text="🔍 Поиск по имени или содержимому...",
        ).grid(row=0, column=0, sticky="ew")

        self._entry_list = ctk.CTkScrollableFrame(
            self, fg_color=SURFACE, corner_radius=12,
        )
        self._entry_list.grid(row=2, column=0, padx=16, pady=4, sticky="nsew")
        self._entry_list.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(4, 12), sticky="ew")
        footer.grid_columnconfigure(1, weight=1)
        self._lbl_count = ctk.CTkLabel(
            footer, text="", font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_count.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Готово", width=100, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._close,
        ).grid(row=0, column=1, sticky="e")

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._render()
        self._after_id = self.after(self._TICK_MS, self._tick)

    # ── data ──
    def _rows(self) -> list:
        return build_view(get_meetings_dir(), self._queue.snapshot())

    def _project_name(self, project_id) -> str:
        store = getattr(self._app, "_dir_store", None)
        if project_id and store is not None:
            project = store.get_project(project_id)
            if project is not None:
                return project.name
        return NO_PROJECT_LABEL

    def _sig(self, rows) -> tuple:
        return tuple((r.id, r.status.value) for r in rows)

    def _matches(self, item, query: str) -> bool:
        if not query:
            return True
        q = query.lower()
        if q in (item.title or "").lower():
            return True
        folder = item.meeting_folder or ""
        if not folder:
            return False
        if folder not in self._transcript_cache:
            self._transcript_cache[folder] = _read_transcript(folder).lower()
        return q in self._transcript_cache[folder]

    # ── render ──
    def _render(self, rows=None):
        if rows is None:
            rows = self._rows()
        self._last_sig = self._sig(rows)
        self._running_rows = []
        for widget in self._entry_list.winfo_children():
            widget.destroy()

        query = self._search_var.get().strip()
        shown = [r for r in rows if self._matches(r, query)]
        suffix = f" / {len(rows)}" if query else ""
        self._lbl_count.configure(text=f"Встреч: {len(shown)}{suffix}")

        if not shown:
            message = "Ничего не найдено" if query else "Нет встреч"
            ctk.CTkLabel(
                self._entry_list, text=message,
                font=ctk.CTkFont(family=FONT, size=13), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=30)
            return

        now = datetime.now().isoformat(timespec="seconds")
        grid_row = 0
        for group_name, items in group_by_project(shown, self._project_name):
            ctk.CTkLabel(
                self._entry_list, text=group_name, anchor="w",
                font=ctk.CTkFont(family=FONT, size=12, weight="bold"),
                text_color=TEXT_SECONDARY,
            ).grid(row=grid_row, column=0, padx=8, pady=(10, 2), sticky="w")
            grid_row += 1
            for item in items:
                self._build_row(item, grid_row, rows, now)
                grid_row += 1

    def _build_row(self, item, grid_row, all_rows, now_iso):
        row = ctk.CTkFrame(self._entry_list, fg_color=SURFACE_BRIGHT, corner_radius=10)
        row.grid(row=grid_row, column=0, padx=4, pady=3, sticky="ew")
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row, text=item.title or os.path.basename(item.meeting_folder or "—"),
            anchor="w", font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=12, pady=(8, 0), sticky="ew")

        meta = ctk.CTkFrame(row, fg_color="transparent")
        meta.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")
        text, color_key = format_status(item, now_iso, queue_position(all_rows, item))
        status_lbl = ctk.CTkLabel(
            meta, text=text, anchor="w",
            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
            text_color=_STATUS_COLORS.get(color_key, TEXT_SECONDARY),
        )
        status_lbl.grid(row=0, column=0, sticky="w")
        if item.status == StageStatus.RUNNING:
            self._running_rows.append((item, status_lbl))
        badge_col = 1
        for present, badge_text in (
            (item.has_protocol, "• протокол"),
            (item.has_tasks, "• задачи"),
        ):
            if present:
                ctk.CTkLabel(
                    meta, text=badge_text, font=ctk.CTkFont(family=FONT, size=11),
                    text_color=TEXT_SECONDARY,
                ).grid(row=0, column=badge_col, padx=(8, 0), sticky="w")
                badge_col += 1

        if item.status == StageStatus.ERROR and item.error_message:
            ctk.CTkLabel(
                row, text=item.error_message, anchor="w", justify="left",
                wraplength=560, font=ctk.CTkFont(family=FONT, size=11),
                text_color=RED,
            ).grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")

        col = 1
        if item.status == StageStatus.DONE and item.meeting_folder:
            ctk.CTkButton(
                row, text="👁 Просмотр", width=110, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda it=item: self._view(it),
            ).grid(row=0, column=col, rowspan=2, padx=(8, 4), pady=6)
            col += 1
            ctk.CTkButton(
                row, text="📝 Obsidian", width=120, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda it=item: self._open_obsidian(it),
            ).grid(row=0, column=col, rowspan=2, padx=(0, 4), pady=6)
            col += 1
            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=14),
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                command=lambda f=item.meeting_folder: self._delete(f),
            ).grid(row=0, column=col, rowspan=2, padx=(0, 8), pady=4)
        elif item.status == StageStatus.ERROR:
            ctk.CTkButton(
                row, text="↻ Повторить", width=120, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda i=item.id: self._retry(i),
            ).grid(row=0, column=col, rowspan=2, padx=(8, 8), pady=6)

    # ── live poll ──
    def _tick(self):
        self._after_id = None
        try:
            rows = self._rows()
            if self._sig(rows) != self._last_sig:
                self._render(rows)
            else:
                now = datetime.now().isoformat(timespec="seconds")
                for item, label in self._running_rows:
                    text, _ = format_status(item, now, None)
                    label.configure(text=text)
        except tk.TclError:
            return  # window destroyed mid-tick — stop the loop
        self._after_id = self.after(self._TICK_MS, self._tick)

    # ── actions ──
    def _view(self, item):
        MeetingViewerDialog(self, item, self._on_load_to_main)

    def _open_obsidian(self, item):
        path = os.path.join(item.meeting_folder or "", "transcript.md")
        if os.path.isfile(path):
            os.startfile(path)  # default .md handler (Obsidian if associated)
        else:
            open_in_explorer(item.meeting_folder or "")

    def _retry(self, item_id):
        self._queue.retry(item_id)
        self._render()

    def _delete(self, folder_path):
        if folder_path and messagebox.askyesno("Удалить", "Удалить эту встречу?"):
            delete_history_entry(folder_path)
            self._transcript_cache.pop(folder_path, None)
            self._render()

    def _close(self):
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self.grab_release()
        self.destroy()
```

- [ ] **Step 4: Pass the queue into the dialog (`dialogs_mixin.py`)**

In `ui/app/dialogs_mixin.py`, update `_open_meetings_dialog` (line ~79):

```python
    def _open_meetings_dialog(self):
        MeetingsDialog(
            self, on_load_to_main=self._load_history_into_main, queue=self._queue,
        )
```

- [ ] **Step 5: Run the new wiring test**

Run: `python -m pytest tests/test_meetings_dialog_queue.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Run the legacy guards that pin meetings.py (must stay green)**

Run: `python -m pytest tests/test_meetings_dialog_rename.py tests/test_transcript_md_extension.py -q`
Expected: all PASS — the rewrite kept «Встречи»/«Встреч:»/«Нет встреч», both classes, `_read_transcript` md→txt, `initialfile="transcript.md"`, and added no «митинг».

- [ ] **Step 7: Lint + commit**

```bash
python -m ruff check ui/dialogs/meetings.py ui/app/dialogs_mixin.py tests/test_meetings_dialog_queue.py
git add ui/dialogs/meetings.py ui/app/dialogs_mixin.py tests/test_meetings_dialog_queue.py
git commit -F- <<'EOF'
feat(ui): transcription-queue PR-C2 — «Встречи» = live queue + history

Rework the meetings dialog over processing.store.build_view(meetings_dir,
queue.snapshot()): rows grouped by project, each with a status pill (в очереди /
идёт mm:ss / готово / ошибка) + Hermes badges (protocol/tasks). DONE rows get
«Просмотр» / «Открыть в Obsidian» (os.startfile transcript.md) / delete; ERROR
rows get «Повторить» (queue.retry). Live self.after(1000) poll re-renders on a
status-signature change and ticks the running row's mm:ss, cancelled on close
(except tk.TclError guard). Search retained; viewer adapted to QueueItem.
dialogs_mixin passes the queue in.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 4: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Full test suite**

Run: `python -m pytest`
Expected: green. This PR adds 4 (started_at) + 11 (meetings_view) + 8 (meetings wiring) = 23 tests on top of the post-C1b baseline (1022). Confirm **no failures/errors**, only the 2 pre-existing skips. (Windows PowerShell: don't pipe/redirect pytest output — it can swallow the summary; read dot-lines or use `--junitxml`.)

- [ ] **Step 2: Full ruff**

Run: `python -m ruff check .`
Expected: clean.

- [ ] **Step 3: Broad-except ratchet (no `except Exception` added)**

Run: `python -m pytest tests/test_broad_except_ratchet.py -v`
Expected: PASS. This PR adds only narrow `except tk.TclError` (poll-shutdown guard in meetings.py) and `except (ValueError, TypeError)` (format_elapsed) — no broad except. Do not edit the baseline.

- [ ] **Step 4: Manual smoke checklist (real keys, Windows — record for the PR body)**

Not automatable (source-slice tests can't instantiate the dialog):
- [ ] Open «Встречи» → finished meetings appear grouped by project; «Без проекта» group last.
- [ ] While a transcription runs: its row shows «идёт mm:ss» ticking; on completion it flips to «готово» without reopening the dialog.
- [ ] A meeting whose Hermes folder has protocol.md/tasks.md shows the «• протокол» / «• задачи» badges.
- [ ] DONE row: «👁 Просмотр» opens the transcript; «📝 Obsidian» opens transcript.md (in Obsidian if it's the default `.md` app, else the default editor); ✕ deletes after confirm.
- [ ] Force an error (e.g. wrong key) → row shows «ошибка» + message + «↻ Повторить»; clicking it re-queues (status → в очереди).
- [ ] Search filters across groups; closing the window leaves no stray timer (no errors in the log).

---

## Self-Review (completed during planning)

**Spec coverage (§11 «Встречи» bullet):**
- "project-grouped rows" → `group_by_project` + grouped render (Task 2/3). ✓
- "status (в очереди / идёт mm:ss / готово / ошибка)" → `format_status` + `started_at` timer (Task 1/2/3). ✓
- "Hermes-progress badges (протокол/задачи)" → `item.has_protocol`/`has_tasks` chips (Task 3); filled by `build_view` (existing). ✓
- "Открыть в Obsidian" → `_open_obsidian` = `os.startfile(transcript.md)` + folder fallback (Task 3). ✓
- "Повторить (on error)" → `_retry` → `queue.retry` (Task 3). ✓
- "standalone «Извлечь задачи» stays available, not part of the queue" → untouched (lives in the main window). ✓
- Deferred per scope: dismiss stuck ERROR item (no queue.remove), inbox poll/inbox_dir (PR-C3), cost hint. ✓

**Placeholder scan:** every code step shows full code; the dialog is given as a complete file. No TBD/"handle edge cases"/"similar to". ✓

**Type/name consistency:** `started_at` (model field ↔ to_dict/from_dict ↔ worker stamp ↔ meetings_view.format_status ↔ tests); `format_elapsed`/`queue_position`/`format_status`/`group_by_project`/`NO_PROJECT_LABEL` (meetings_view def ↔ meetings.py import ↔ tests); `color_key` values `pending`/`running`/`done`/`error` (format_status ↔ `_STATUS_COLORS`); `build_view(meetings_dir, active)` (store signature ↔ `_rows`); `queue.snapshot()`/`queue.retry(id)` (worker API ↔ dialog); `MeetingsDialog(parent, on_load_to_main, queue)` (def ↔ dialogs_mixin call `queue=self._queue`). ✓
