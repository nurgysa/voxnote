# Dismiss-stuck-ERROR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user clear a stuck `ERROR` queue item from «Встречи» in one click, reusing the existing `ProcessingQueue.forget`.

**Architecture:** Pure UI wiring in `ui/dialogs/meetings.py` — the ERROR row gains an «✕ Убрать» button bound to a new `_dismiss(item)` that calls `self._queue.forget(item.id)` + `self._render()`. No model/worker change (`forget` already drops any non-RUNNING item). A characterization test pins the backend contract the UI relies on.

**Tech Stack:** Python 3.12, customtkinter (Tk), pytest (headless — source-slice for the Tk dialog), ruff. Interpreter: `C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe`.

Spec: `docs/superpowers/specs/2026-06-19-queue-dismiss-error-design.md`.

## Global Constraints

- Russian UI strings; English code/comments/commits.
- `encoding="utf-8"` on every text read/write.
- UI tests must be **source-slice** (read the module text, assert substrings) — importing `ui.app`/`ui.dialogs.meetings` pulls customtkinter → PortAudio and crashes Linux CI.
- Narrow `except` only; no `except Exception` without a justifying comment.
- No folder deletion in dismiss; ERROR only (PENDING/RUNNING not dismissable).
- Commit messages via `git commit -F` from a file in gitignored `.cache/` when they contain `«» ✕` or `"` (PowerShell native-arg mangling); delete the temp file after.

---

### Task 1: Characterize `forget` drops an ERROR item

**Files:**
- Test: `tests/test_processing_worker.py` (add in the `# ── forget` section, after `test_forget_drops_item_and_persists`)

**Interfaces:**
- Consumes: `ProcessingQueue.enqueue`, `._set_status(item, StageStatus.ERROR, error_message=...)`, `.forget(item_id)`, `.snapshot()` — all existing.
- Produces: nothing new (regression pin only).

- [ ] **Step 1: Write the characterization test**

Add to `tests/test_processing_worker.py`:

```python
def test_forget_drops_errored_item(tmp_path):
    """«✕ Убрать» in «Встречи» relies on forget evicting an ERROR item (not
    only DONE). Pins the backend contract the UI dismiss depends on."""
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    q._set_status(q._items[0], StageStatus.ERROR, error_message="boom")
    q.forget(item_id)
    assert q.snapshot() == []
    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        assert json.load(f)["items"] == []
```

- [ ] **Step 2: Run it — expect PASS immediately**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_processing_worker.py::test_forget_drops_errored_item -v`
Expected: PASS. This is a **characterization** test, not red-green: `forget` already drops any non-RUNNING item, so it documents (and locks) the existing behavior the UI dismiss is built on. If it ever FAILS in future, the dismiss button is silently broken.

- [ ] **Step 3: Commit**

```bash
git add tests/test_processing_worker.py
git commit -m "test(queue): pin forget() drops an ERROR item"
```

(No `«»`/quotes in this message — plain `git commit -m` is safe.)

---

### Task 2: «✕ Убрать» button on ERROR rows

**Files:**
- Modify: `ui/dialogs/meetings.py` — the `elif item.status == StageStatus.ERROR:` branch in `_build_row` (~lines 368-374) and a new `_dismiss` method in the `# ── actions ──` section (after `_retry`, ~line 405)
- Test: `tests/test_meetings_dialog_queue.py` (add after `test_meetings_delete_forgets_queue_item`)

**Interfaces:**
- Consumes: `self._queue.forget(item_id)`, `self._render()` — both existing on `MeetingsDialog`.
- Produces: `MeetingsDialog._dismiss(self, item)`.

- [ ] **Step 1: Write the failing source-slice test**

Add to `tests/test_meetings_dialog_queue.py`:

```python
def test_meetings_dismiss_error_wired_to_forget():
    # A stuck ERROR item can be cleared from the queue, distinct from retry.
    assert "def _dismiss" in _MEET
    assert "✕ Убрать" in _MEET
    assert "_dismiss(it)" in _MEET  # ERROR-row button wired to the dismiss handler
```

- [ ] **Step 2: Run it — verify it fails**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_meetings_dialog_queue.py::test_meetings_dismiss_error_wired_to_forget -v`
Expected: FAIL — `_dismiss` / «✕ Убрать» don't exist yet.

- [ ] **Step 3: Add the dismiss button to the ERROR branch**

In `ui/dialogs/meetings.py` `_build_row`, replace the ERROR branch:

```python
        elif item.status == StageStatus.ERROR:
            ctk.CTkButton(
                row, text="↻ Повторить", width=120, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda i=item.id: self._retry(i),
            ).grid(row=0, column=col, rowspan=2, padx=(8, 8), pady=6)
```

with (retry button's right pad tightened to `(8, 4)`; dismiss button added):

```python
        elif item.status == StageStatus.ERROR:
            ctk.CTkButton(
                row, text="↻ Повторить", width=120, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
                command=lambda i=item.id: self._retry(i),
            ).grid(row=0, column=col, rowspan=2, padx=(8, 4), pady=6)
            col += 1
            ctk.CTkButton(
                row, text="✕ Убрать", width=100, height=32, corner_radius=16,
                font=ctk.CTkFont(family=FONT, size=12),
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                command=lambda it=item: self._dismiss(it),
            ).grid(row=0, column=col, rowspan=2, padx=(0, 8), pady=6)
```

- [ ] **Step 4: Add the `_dismiss` method**

In `ui/dialogs/meetings.py`, in the `# ── actions ──` section, after `_retry`:

```python
    def _dismiss(self, item):
        # Clear a stuck ERROR item from the queue. forget() drops any non-RUNNING
        # item; no folder is deleted — an ERROR normally has none, and a rare
        # late-failure's transcript.md on disk should survive as a DONE history
        # row (build_view reverts to it once the active item is forgotten).
        self._queue.forget(item.id)
        self._render()
```

- [ ] **Step 5: Run the source-slice test — verify it passes**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_meetings_dialog_queue.py -q`
Expected: all PASS (the new test + the existing `test_meetings_*` source-slice tests, incl. the pinned-strings guard — «✕ Убрать» adds strings, removes none).

- [ ] **Step 6: Commit**

```bash
git add ui/dialogs/meetings.py tests/test_meetings_dialog_queue.py
git commit -F .cache/commit-msg.txt   # message contains «✕ Убрать» — use a file
```

Message body (`.cache/commit-msg.txt`, then delete it):

```
feat(queue): dismiss a stuck ERROR item from «Встречи»

ERROR rows get an «✕ Убрать» button next to «↻ Повторить», wired to a new
_dismiss(item) that calls ProcessingQueue.forget(item.id) + _render(). Instant,
no confirm, no folder deletion (a rare late-failure's transcript survives as a
DONE history row). Closes the deferred-hygiene gap: a permanently-failing item
could only be retried, never cleared.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

### Task 3: Full suite + lint gate

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q`
Expected: PASS (baseline ≈ 1065 + **2** new = ≈ 1067 passed, 2 skipped).

- [ ] **Step 2: Lint**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Broad-except ratchet (no new broad excepts)**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_broad_except_ratchet.py -q`
Expected: PASS.

---

## Self-Review

**Spec coverage:**
- Spec "Design 1/2 — «✕ Убрать» button → `_dismiss` → forget + render, instant, no confirm" → Task 2. ✓
- Spec "What dismiss does NOT do — no folder deletion; ERROR only" → Task 2 `_dismiss` (forget only, no `delete_history_entry`); button only in the ERROR branch. ✓
- Spec "Testing — source-slice for the dialog wiring" → Task 2 test. ✓
- Spec "Testing — `test_forget_drops_errored_item` pins the backend contract" → Task 1. ✓
- Spec "Out of scope — cost-hint" → not in plan. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `_dismiss(self, item)`, `self._queue.forget(item.id)`, `self._render()`, `col`, `BORDER`/`RED`/`BLUE_SURFACE`/`SURFACE_BRIGHT` (all imported in meetings.py), `StageStatus.ERROR`, `_set_status(item, status, *, error_message=...)`, `_MEET` (test module global) — all match existing code.

**Note on Task 1 being non-red-green:** intentional and called out — it characterizes existing `forget` behavior. The only true red-green is Task 2's source-slice test.
