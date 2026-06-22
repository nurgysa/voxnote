# Cost-hint-at-enqueue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a rough cost estimate when the user adds a file to the queue (record / «Выбрать файл»), appended to the «Добавлено в очередь» status line.

**Architecture:** A new pure `preflight.cost_hint_suffix(provider, duration_s) -> str` wraps the existing `estimate_cost` into a display suffix; `QueueMixin._enqueue` probes the file and folds the suffix into its status line. Passive hint — never gates an enqueue; empty suffix when the cost is unknown.

**Tech Stack:** Python 3.12, customtkinter (Tk), pytest (headless — source-slice for the Tk-bound mixin), ruff. Interpreter: `C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe`.

Spec: `docs/superpowers/specs/2026-06-19-queue-cost-hint-design.md`.

## Global Constraints

- Russian UI strings; English code/comments/commits.
- `encoding="utf-8"` on every text read/write.
- UI tests must be **source-slice** (read the module text, assert substrings) — importing `ui.app.queue_mixin` pulls sounddevice → PortAudio and crashes Linux CI.
- Narrow `except` only.
- `cost_hint_suffix` is distinct from the extract dialog's token-based `estimate_cost_hint` — do not merge or rename to collide.
- Passive hint only — no confirmation/gate; inbox auto-enqueue (`_inbox_tick`) untouched.
- Commit messages with `«»`/`$`/`"`: use a single-quoted here-string `@'...'@` (literal `$`, safe for `«»`) or `git commit -F`; never plain `-m` with embedded `"`.

---

### Task 1: `cost_hint_suffix` pure helper

**Files:**
- Modify: `processing/preflight.py` (append after `estimate_cost`, end of file)
- Test: `tests/test_preflight.py` (append after `test_estimate_cost_none_for_unknown_provider`)

**Interfaces:**
- Consumes: `preflight.estimate_cost(provider, duration_s) -> float | None` (existing).
- Produces: `preflight.cost_hint_suffix(provider: str, duration_s: float | None) -> str` — `" · ~$X.XX"` or `""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preflight.py`:

```python
# ── cost_hint_suffix ──

def test_cost_hint_suffix_formats_two_decimals():
    assert preflight.cost_hint_suffix("AssemblyAI", 3600.0) == " · ~$0.17"


def test_cost_hint_suffix_empty_when_duration_unknown():
    assert preflight.cost_hint_suffix("AssemblyAI", None) == ""


def test_cost_hint_suffix_empty_for_unknown_provider():
    assert preflight.cost_hint_suffix("Nope", 3600.0) == ""
```

- [ ] **Step 2: Run them — verify they fail**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_preflight.py -k cost_hint_suffix -v`
Expected: FAIL — `AttributeError: module 'processing.preflight' has no attribute 'cost_hint_suffix'`.

- [ ] **Step 3: Write minimal implementation**

Append to `processing/preflight.py` (after `estimate_cost`):

```python


def cost_hint_suffix(provider: str, duration_s: float | None) -> str:
    """' · ~$X.XX' for an at-enqueue status-line hint, or '' when the cost is
    unknown (duration unmeasurable or provider not in the rate table)."""
    cost = estimate_cost(provider, duration_s)
    if cost is None:
        return ""
    return f" · ~${cost:.2f}"
```

- [ ] **Step 4: Run them — verify they pass**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_preflight.py -k cost_hint_suffix -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add processing/preflight.py tests/test_preflight.py
git commit -m "feat(queue): add preflight.cost_hint_suffix for at-enqueue cost"
```

(Plain `-m` — no special characters in this message.)

---

### Task 2: Wire the hint into `_enqueue`

**Files:**
- Modify: `ui/app/queue_mixin.py` — module imports + `_enqueue`
- Test: `tests/test_ui_cost_hint.py` (create — source-slice)

**Interfaces:**
- Consumes: `preflight.probe(audio_path) -> {"duration_s": float | None, "size_bytes": int}`, `preflight.cost_hint_suffix(provider, duration_s) -> str` (Task 1).
- Produces: nothing (UI wiring).

- [ ] **Step 1: Write the failing source-slice test**

Create `tests/test_ui_cost_hint.py`:

```python
"""Source-slice wiring test for the at-enqueue cost hint.

No ui.app import — sounddevice → PortAudio crashes Linux CI.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_QUEUE_MIXIN = (_ROOT / "ui" / "app" / "queue_mixin.py").read_text(encoding="utf-8")


def test_enqueue_shows_cost_hint():
    assert "from processing import preflight" in _QUEUE_MIXIN
    assert "preflight.probe(" in _QUEUE_MIXIN
    assert "cost_hint_suffix(" in _QUEUE_MIXIN
    # the suffix is interpolated into the «Добавлено в очередь» status line
    assert "{hint}" in _QUEUE_MIXIN
```

- [ ] **Step 2: Run it — verify it fails**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_ui_cost_hint.py -v`
Expected: FAIL — none of those substrings exist yet.

- [ ] **Step 3: Add the import**

In `ui/app/queue_mixin.py`, add to the `processing` imports (next to `from processing.model import StageStatus`):

```python
from processing import preflight
```

- [ ] **Step 4: Wire the hint into `_enqueue`**

In `ui/app/queue_mixin.py`, replace the tail of `_enqueue` (from `self._queue.enqueue(...)` onward):

```python
        self._queue.enqueue(audio_path, self._build_options(source))
        self._lbl_status.configure(
            text=f"Добавлено в очередь: {os.path.basename(audio_path)}",
            text_color=GREEN,
        )
        self._refresh_queue_indicator()
```

with (probe + suffix folded into the status line):

```python
        info = preflight.probe(audio_path)
        hint = preflight.cost_hint_suffix(provider, info.get("duration_s"))
        self._queue.enqueue(audio_path, self._build_options(source))
        self._lbl_status.configure(
            text=f"Добавлено в очередь: {os.path.basename(audio_path)}{hint}",
            text_color=GREEN,
        )
        self._refresh_queue_indicator()
```

(`provider` is already bound at the top of `_enqueue` for the key-check.)

- [ ] **Step 5: Run the source-slice test — verify it passes**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_ui_cost_hint.py -v`
Expected: PASS

- [ ] **Step 6: Lint the touched module**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m ruff check ui/app/queue_mixin.py processing/preflight.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add ui/app/queue_mixin.py tests/test_ui_cost_hint.py
git commit -F .cache/commit-msg.txt   # message contains «» — use a file or here-string
```

Message body (`.cache/commit-msg.txt`, then delete it):

```
feat(queue): show a cost hint when adding a file to the queue

_enqueue probes the file and appends preflight.cost_hint_suffix to the
«Добавлено в очередь» status line («… · ~$0.12»), using the selected provider's
rate. Passive — never gates the enqueue; empty when duration/cost is unknown.
Interactive enqueue only (record/pick); the inbox auto-enqueue is untouched.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

### Task 3: Full suite + lint gate

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q`
Expected: PASS (baseline ≈ 1067 + **4** new = ≈ 1071 passed, 2 skipped).

- [ ] **Step 2: Lint**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Broad-except ratchet (no new broad excepts)**

Run: `& C:/Users/nurgisa/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_broad_except_ratchet.py -q`
Expected: PASS.

---

## Self-Review

**Spec coverage:**
- Spec "New pure helper `cost_hint_suffix`" → Task 1. ✓
- Spec "Wiring in `_enqueue` (probe + suffix + import)" → Task 2. ✓
- Spec "Behavior — passive, degrades to empty, uses selected provider" → Task 1 (None→"") + Task 2 (`provider` reused, `{hint}` may be ""). ✓
- Spec "Testing — real unit tests for the helper; source-slice for the wiring" → Task 1 + Task 2. ✓
- Spec "Out of scope — gate, per-item «Встречи» cost, inbox hint" → not in plan. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `cost_hint_suffix(provider: str, duration_s: float | None) -> str`, `estimate_cost`, `preflight.probe(...)["duration_s"]`, `provider = self._cloud_provider_var.get()`, `GREEN`, `os.path.basename`, `self._lbl_status`, `_QUEUE_MIXIN` (test global) — all match existing code (`processing/preflight.py`, `ui/app/queue_mixin.py`).

**Cost format note:** `f"{cost:.2f}"` — a short clip rounds to e.g. `~$0.01`; a 2 h Speechmatics job → `~$2.08`. 2-decimal USD is intended.
