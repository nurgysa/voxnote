# Queue DONE-pruning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `queue.json` and the loaded in-memory queue holding active work only (PENDING/RUNNING/ERROR); a finished meeting lives on disk.

**Architecture:** Two point changes in `processing/worker.py` — `_persist_locked` stops writing DONE items, and `ProcessingQueue.__init__` drops legacy DONE from a loaded queue (folded into the existing interrupted-RUNNING→ERROR reconciliation). No model/schema change. DONE items stay in memory for the session (live «Встречи» overlay) but are never persisted and never survive a restart.

**Tech Stack:** Python stdlib, pytest (headless — no Tk import), ruff. Interpreter: `C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe`.

Spec: `docs/superpowers/specs/2026-06-18-queue-done-pruning-design.md`.

---

### Task 1: `_persist_locked` excludes DONE

**Files:**
- Modify: `processing/worker.py:155-157` (`_persist_locked`)
- Test: `tests/test_processing_worker.py` (add to the persistence section, after `test_loads_existing_active_items`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_processing_worker.py`:

```python
def test_done_item_not_persisted_but_kept_in_memory(tmp_path):
    """A completed item is dropped from queue.json (queue.json = active work
    only) but stays in the in-memory snapshot for the session's live view."""
    q = _queue(tmp_path)
    done_id = q.enqueue("/audio/done.m4a", {})
    pend_id = q.enqueue("/audio/pend.m4a", {})
    q._set_status(q._items[0], StageStatus.DONE)

    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        persisted_ids = [it["id"] for it in json.load(f)["items"]]
    assert done_id not in persisted_ids   # DONE not written
    assert pend_id in persisted_ids       # active item still written

    # in-memory overlay preserved (live «Встречи» shows "just finished")
    statuses = {it.id: it.status for it in q.snapshot()}
    assert statuses[done_id] == StageStatus.DONE
    assert statuses[pend_id] == StageStatus.PENDING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_processing_worker.py::test_done_item_not_persisted_but_kept_in_memory -v`
Expected: FAIL — `done_id` is currently persisted, so `assert done_id not in persisted_ids` fails.

- [ ] **Step 3: Write minimal implementation**

In `processing/worker.py`, replace `_persist_locked`:

```python
    def _persist_locked(self) -> None:
        # Caller holds self._lock. queue.json carries ACTIVE items only — a
        # finished meeting lives on disk (its transcript.md); persisting DONE
        # here would grow queue.json without bound and leak a stale audio_path
        # into the inbox dedup across restarts. build_view re-reads finished
        # meetings from their folders for «Встречи».
        store.save_active(
            [it for it in self._items if it.auto and it.status != StageStatus.DONE],
            self._queue_path,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_processing_worker.py::test_done_item_not_persisted_but_kept_in_memory -v`
Expected: PASS

- [ ] **Step 5: Run the full worker test file (no regression from existing DONE-in-snapshot tests)**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_processing_worker.py -q`
Expected: all PASS (the `test_process_item_*` and `test_forget_*` tests rely on DONE staying in memory / queue.json ending empty, both still true).

- [ ] **Step 6: Commit**

```bash
git add processing/worker.py tests/test_processing_worker.py
git commit -F commit-msg-task1.txt
```

Commit message (`commit-msg-task1.txt`, then delete it):

```
fix(queue): stop persisting DONE items to queue.json

_persist_locked now writes active items only (auto and status != DONE).
A finished meeting lives on disk (its transcript.md); persisting DONE grew
queue.json without bound and leaked a stale audio_path into the inbox dedup
across restarts. DONE items stay in memory for the session's live «Встречи»
overlay.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

### Task 2: Drop legacy DONE on load

**Files:**
- Modify: `processing/worker.py:72-80` (the interrupted-RUNNING reconciliation block in `__init__`)
- Test: `tests/test_processing_worker.py` (add after `test_loads_reconciles_interrupted_running_to_error`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_processing_worker.py`:

```python
def test_load_drops_legacy_done_and_rewrites(tmp_path):
    """A queue.json written before pruning may carry DONE items; loading drops
    them (active list = active work only) and rewrites the file without them."""
    from processing.model import QueueItem
    from processing.store import save_active

    qp = tmp_path / "queue.json"
    save_active(
        [
            QueueItem(id="d", audio_path="/a.m4a", title="a", created_at="t",
                      auto=True, status=StageStatus.DONE),
            QueueItem(id="p", audio_path="/b.m4a", title="b", created_at="t",
                      auto=True, status=StageStatus.PENDING),
        ],
        path=qp,
    )
    q = _queue(tmp_path, queue_path=str(qp))

    assert [it.id for it in q.snapshot()] == ["p"]          # DONE dropped in memory
    with open(qp, encoding="utf-8") as f:                   # file rewritten without it
        assert [it["id"] for it in json.load(f)["items"]] == ["p"]


def test_load_keeps_error_drops_done(tmp_path):
    """ERROR items survive a reload (retry/crash-resume intact); DONE does not."""
    from processing.model import QueueItem
    from processing.store import save_active

    qp = tmp_path / "queue.json"
    save_active(
        [
            QueueItem(id="e", audio_path="/a.m4a", title="a", created_at="t",
                      auto=True, status=StageStatus.ERROR, error_message="boom"),
            QueueItem(id="d", audio_path="/b.m4a", title="b", created_at="t",
                      auto=True, status=StageStatus.DONE),
        ],
        path=qp,
    )
    q = _queue(tmp_path, queue_path=str(qp))

    live = q.snapshot()
    assert [it.id for it in live] == ["e"]
    assert live[0].status == StageStatus.ERROR
    assert live[0].error_message == "boom"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_processing_worker.py::test_load_drops_legacy_done_and_rewrites tests/test_processing_worker.py::test_load_keeps_error_drops_done -v`
Expected: FAIL — load currently keeps DONE, so the snapshot id lists include `"d"`.

- [ ] **Step 3: Write minimal implementation**

In `processing/worker.py.__init__`, replace the interrupted block (currently lines ~72-80):

```python
        interrupted = [it for it in self._items if it.status == StageStatus.RUNNING]
        for it in interrupted:
            it.status = StageStatus.ERROR
            it.error_message = (
                "Обработка прервана (приложение было перезапущено). "
                "Нажми «Повторить», чтобы запустить заново."
            )
        # DONE items in a loaded queue are legacy (pre-pruning): a finished
        # meeting belongs to disk, not the active queue. Drop them so the active
        # list and queue.json hold active work only and no stale audio_path
        # survives a restart.
        had_done = any(it.status == StageStatus.DONE for it in self._items)
        if had_done:
            self._items = [it for it in self._items if it.status != StageStatus.DONE]
        if interrupted or had_done:
            store.save_active([it for it in self._items if it.auto], queue_path)
```

(The original `if interrupted: store.save_active(...)` two-liner is replaced by the `had_done` drop plus the combined `if interrupted or had_done:` save. After the drop no DONE remain, so the `if it.auto` save filter matches `_persist_locked`'s result.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_processing_worker.py::test_load_drops_legacy_done_and_rewrites tests/test_processing_worker.py::test_load_keeps_error_drops_done -v`
Expected: PASS

- [ ] **Step 5: Run the full worker file + the existing interrupted-reconcile test (no regression)**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_processing_worker.py -q`
Expected: all PASS (incl. `test_loads_reconciles_interrupted_running_to_error`, untouched by the DONE drop).

- [ ] **Step 6: Commit**

```bash
git add processing/worker.py tests/test_processing_worker.py
git commit -F commit-msg-task2.txt
```

Commit message (`commit-msg-task2.txt`, then delete it):

```
fix(queue): drop legacy DONE items when loading queue.json

__init__ now drops DONE items from a loaded queue (folded into the existing
interrupted-RUNNING→ERROR reconciliation) and rewrites the file. Cleans up
queue.json files written before DONE-pruning and keeps snapshot() correct from
the first tick. ERROR items still survive a reload (retry/crash-resume intact).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

### Task 3: Full suite + lint gate

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q`
Expected: PASS (baseline ≈ 1062 passed, 2 skipped, **+3** new = ≈ 1065 passed).

- [ ] **Step 2: Lint**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Broad-except ratchet (no new broad excepts introduced here)**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_broad_except_ratchet.py -q`
Expected: PASS (this change adds none).

---

## Self-Review

**Spec coverage:**
- Spec "Design §1 Persist active-only" → Task 1. ✓
- Spec "Design §2 Drop DONE on load" → Task 2. ✓
- Spec test plan #1 (DONE not persisted) + #3 (in-session overlay) → `test_done_item_not_persisted_but_kept_in_memory`. ✓
- Spec test plan #2 (load drops legacy DONE, file rewritten) → `test_load_drops_legacy_done_and_rewrites`. ✓
- Spec test plan #4 (ERROR persists/reloads; ERROR+DONE mix keeps ERROR, drops DONE) → `test_load_keeps_error_drops_done` + existing `test_loads_reconciles_interrupted_running_to_error`. ✓
- Spec non-goal "in-memory overlay preserved" → asserted in Task 1 test. ✓

**Placeholder scan:** none — every step has concrete test/impl code and exact commands.

**Type consistency:** `StageStatus.{PENDING,RUNNING,ERROR,DONE}`, `_persist_locked`, `store.save_active`, `QueueItem`, `_queue(tmp_path, queue_path=...)` helper — all match the existing module and test file.

**Windows note:** commit messages contain Cyrillic + «» + `<...>`; pass via `git commit -F <file>` (never `-m` inline) per CLAUDE.md PowerShell gotchas, then delete the temp file. The temp files are untracked and outside any committed path.
