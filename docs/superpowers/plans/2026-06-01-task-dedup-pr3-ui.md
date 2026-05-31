# Task-Dedup PR-3 — Dialog + UI Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the dedup engine LIVE. After extraction, match each new task against the registry of previously-SENT tasks; surface matches in the editor as an inline «🔁 возможный дубль → {identifier}» badge with a per-row **comment / create-new** toggle; and on send, **comment on the existing card** (new `TaskStatus.COMMENTED`) instead of creating a duplicate. This is the **first PR that changes runtime behaviour** — needs manual GUI smoke.

**Architecture:** The decision logic stays PURE and Tk-free: a new `dedup.select_match()` (find_candidates → HIGH/LOW split → disambiguate_via_llm) is unit-tested without CustomTkinter. The Extract dialog runs a thin best-effort driver (`_run_dedup`) on the **existing extraction worker thread** (where `backend`+`openrouter` already live), setting the transient `Task.dup_match`. The row renders badge+toggle from `dup_match`/`dup_action`; the sender branches on them. Per [[feedback-phasing-ui-before-backend]] the UI affordance (badge/toggle) and the capability (sender comment branch + `COMMENTED`) land together in THIS PR. Per [[feedback-ui-app-import-breaks-linux-ci]] all Tk-touching tests are **source-text only**.

**Tech Stack:** Python 3.10+, CustomTkinter (UI, untested on CI), `pytest` + `MagicMock` for the pure parts, ruff. No new deps.

**Design source:** `~/.claude/plans/foamy-wobbling-owl.md` (PR-3 section) + memory `project-task-dedup`. Verified against real code 2026-06-01 (all line numbers below confirmed by reading the files):
- Extract worker `_run_extraction` builds `backend`/`openrouter`, calls `extract()`, then `save_tasks_raw` (`ui/dialogs/extract_tasks/__init__.py:899`), protocol gen, then dispatches `self.after(0, self._on_extract_success, result, meta)` at **:971** — the dedup driver inserts just before that dispatch (backend/openrouter still open; `finally` at :986 closes them after).
- `_on_extract_success` (:1001) sets `self._tasks`, `self._meta`, then `_render_task_list()` builds rows via `_TaskRow(...)` (**:1405**); non-PENDING rows get `set_status_visual` (:1416).
- `_update_row_status` (:1896) already calls `row.set_status_visual(task.status, identifier=task.linear_issue_id, error_code=task.send_error)` generically → `COMMENTED` flows through once `set_status_visual` handles it.
- Send: `_start_send` (:1797) → `_run_send_worker` (:1837) → `send_tasks_iter(self._tasks, container_id=..., backend=..., on_status_change=..., cancel_check=..., retry_failed=...)` (:1852). `container_id` = `self._meta["team_id"]`; `backend_name` = `self._meta["backend"]` (default `"linear"`).
- `meta` carries `backend` + `team_id` (:894-895). Config read via `self._config.get(...)` (e.g. per-backend `*_enabled` at :214-218). `config.example.json` is a flat dict.

**Branch:** `feat/task-dedup-ui` (off updated `origin/main` = `6ae0d48`, which carries PR-1 #88 + PR-2 #89 via squash). NOT stacked on `feat/task-dedup-engine` ([[feedback-stacked-pr-squash-merge]]). Working tree clean.

**Design decisions locked here (decisive; user delegates):**
- **Transient match state lives on `Task`** as `dup_match: SentTask | None` + `dup_action: str = "comment"`, **excluded from `to_dict`/`from_dict`** — dedup is recomputed each extraction, never persisted. Circular import (`schema`↔`dedup`) avoided via `from __future__ import annotations` + `TYPE_CHECKING` import.
- **Driver runs on the extraction worker thread**, best-effort: any `OpenRouterError`/`OSError`/`PersistenceError` is logged and swallowed so a dedup hiccup never blocks showing the extracted tasks (badges just don't appear).
- **Fail-safe gates:** dedup is skipped entirely when `not backend.supports_comments` (Glide) OR `not config["dedup_enabled"]`. The sender ALSO guards (`getattr(backend, "supports_comments", False)`) and falls back to `create()` — belt-and-braces mirror of the `supports_mixed` gate.
- **Row toggle = `CTkSegmentedButton(["Закомментировать", "Создать новую"])** on a dedicated third row that only appears when `dup_match` is set. Default «Закомментировать»; picking «Создать новую» sets `dup_action="create"` (safety against a false match). Colours from `theme.py` only ([[feedback-no-hex-in-ctk-styles]]).
- **`COMMENTED` badge** = «🔁» in `BLUE_DIM` with the existing card's identifier appended (mirrors the SENT «✓» render).

**Tech reuse:**
- `dedup.build_sent_registry` / `find_candidates` / `disambiguate_via_llm` (PR-2, in main).
- `utils.list_history_entries` + `tasks.persistence.load_tasks` (injected into the registry builder).
- Lazy imports inside `_run_dedup` (mirror the existing lazy-import discipline in `_run_extraction`, :837-846) keep `tasks.*` off the dialog's module import chain.

---

## File Structure

| File | Change |
|------|--------|
| `docs/superpowers/plans/2026-06-01-task-dedup-pr3-ui.md` | **(this file)** committed first |
| `tasks/schema.py` | `TaskStatus.COMMENTED`; transient `dup_match`/`dup_action` (excluded from to_dict/from_dict) |
| `tasks/dedup.py` | `find_candidates(..., low=FUZZY_LOW)` param; new `select_match()` orchestrator |
| `tasks/sender.py` | comment branch (`add_comment` vs `create`) + `COMMENTED` + `meeting_label` param |
| `ui/dialogs/extract_tasks/task_row.py` | dedup badge + segmented toggle + `COMMENTED` badge |
| `ui/dialogs/extract_tasks/__init__.py` | `_run_dedup` driver (worker) + badge wiring + pass `meeting_label` to send |
| `ui/dialogs/settings.py` | `dedup_enabled` checkbox (mirror `linear_enabled`) |
| `config.example.json` | `dedup_enabled` / `dedup_fuzzy_high` / `dedup_fuzzy_low` |
| `tests/test_tasks_schema.py` | COMMENTED round-trip; dup_* transient (not persisted) |
| `tests/test_tasks_dedup.py` | `find_candidates(low=...)`; `select_match` confident / borderline / none |
| `tests/test_tasks_send.py` | comment branch: COMMENTED + add_comment called; create fallback; gate |
| `tests/test_dialog_dedup_ui.py` | **NEW** source-text checks (task_row badge/toggle, dialog driver wiring, settings checkbox) |

**Baseline:** `python -m pytest` green (origin/main = 563) + `ruff check .` clean before every commit.

**CI note:** GitHub Actions may show red on billing, not code — see [[reference-github-actions-billing-false-red]]. Compare the push-event run; merge-over-red is acceptable when local + push-run are green.

---

## Task 0: Commit this plan

- [ ] **Step 1:** `python -m pytest -q` → record green (563). `ruff check .` clean.
- [ ] **Step 2:** Commit (guard the branch so a mid-run switch can't misfire — [[feedback-user-switches-branches-mid-run]]):

```bash
test "$(git branch --show-current)" = "feat/task-dedup-ui" && git add docs/superpowers/plans/2026-06-01-task-dedup-pr3-ui.md && git commit -m "docs(dedup): PR-3 UI wiring bite-sized plan"  # + Co-Authored-By trailer
```

---

## Task 1: schema — `COMMENTED` + transient `dup_match`/`dup_action`

**Files:** Modify `tasks/schema.py`; Test `tests/test_tasks_schema.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tasks_schema.py` (it already imports `Task`, `TaskStatus`):

```python
def test_commented_status_round_trips():
    t = Task(local_id="c1", title="t", status=TaskStatus.COMMENTED)
    assert t.to_dict()["status"] == "commented"
    assert Task.from_dict(t.to_dict()).status is TaskStatus.COMMENTED


def test_dup_match_and_action_are_transient_not_persisted():
    t = Task(local_id="d1", title="t")
    assert t.dup_match is None
    assert t.dup_action == "comment"
    t.dup_match = object()        # stand-in for a SentTask
    t.dup_action = "create"
    d = t.to_dict()
    assert "dup_match" not in d   # never serialized
    assert "dup_action" not in d
    # from_dict ignores any stray keys and applies defaults
    revived = Task.from_dict({"local_id": "d2", "title": "t",
                              "dup_action": "create"})
    assert revived.dup_match is None
    assert revived.dup_action == "comment"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tasks_schema.py -k "commented or dup_" -v`
Expected: FAIL — `AttributeError: COMMENTED` / `Task` has no `dup_match`.

- [ ] **Step 3: Implement**

In `tasks/schema.py`, add to the `TaskStatus` enum (after `SKIPPED`):

```python
    SKIPPED = "skipped"   # user unchecked the task
    COMMENTED = "commented"  # dedup: commented on an existing card instead of creating
```

At the top of the file, under the existing `from __future__ import annotations`, add the TYPE_CHECKING import (a sibling to the other imports):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tasks.dedup import SentTask
```

In the `Task` dataclass, after `send_error: str | None = None`, add the transient fields:

```python
    send_error: str | None = None
    # Transient dedup state (PR-3) — set in-memory after extraction, NEVER
    # persisted (excluded from to_dict/from_dict). Recomputed each extract.
    dup_match: "SentTask | None" = None    # matched past task, or None
    dup_action: str = "comment"            # "comment" | "create"
```

`to_dict`/`from_dict` are left UNCHANGED — they already omit `dup_match`/`dup_action`, which is exactly the transient behaviour we want. (Verify: the test asserts the keys are absent.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tasks_schema.py -v` → PASS. `ruff check tasks/schema.py tests/test_tasks_schema.py` → clean.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/task-dedup-ui" && git add tasks/schema.py tests/test_tasks_schema.py && git commit -m "feat(schema): TaskStatus.COMMENTED + transient dup_match/dup_action"  # + trailer
```

---

## Task 2: dedup — `find_candidates(low=...)` + `select_match()`

**Files:** Modify `tasks/dedup.py`; Test `tests/test_tasks_dedup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tasks_dedup.py` (add `select_match` to the `tasks.dedup` import):

```python
from tasks.dedup import select_match  # noqa: E402  (added to the top import block)


def test_find_candidates_low_param_overrides_floor():
    registry = [
        _reg_entry("Подготовить отчёт по продажам", "r-hi"),
        _reg_entry("Купить кофе для офиса", "r-low"),
    ]
    new = Task(title="Подготовить отчёт по продажам за май")
    # A very high `low` keeps only the strong match.
    out = find_candidates(new, registry, backend="linear",
                          container_id="team-A", low=0.95)
    assert [s.ref for s, _ in out] == []  # even r-hi (~0.89) is below 0.95
    out2 = find_candidates(new, registry, backend="linear",
                           container_id="team-A", low=0.30)
    assert "r-hi" in [s.ref for s, _ in out2]


def test_select_match_confident_skips_llm():
    llm = MagicMock()
    registry = [_reg_entry("Починить логин", "r-1")]
    out = select_match(
        Task(title="починить логин"), registry,
        backend="linear", container_id="team-A",
        openrouter_client=llm, model="m",
    )
    assert out is not None and out.ref == "r-1"
    llm.complete.assert_not_called()  # >= HIGH -> no LLM spend


def test_select_match_borderline_delegates_to_llm():
    llm = MagicMock()
    llm.complete.return_value = {"content": '{"match_id": "r-1"}'}
    # Build a title that lands in the borderline band against the candidate.
    registry = [_reg_entry("Обновить документацию по API сервиса", "r-1")]
    out = select_match(
        Task(title="Освежить доки сервиса API немного"), registry,
        backend="linear", container_id="team-A",
        openrouter_client=llm, model="m",
    )
    assert out is not None and out.ref == "r-1"
    llm.complete.assert_called_once()


def test_select_match_no_candidates_returns_none_without_llm():
    llm = MagicMock()
    registry = [_reg_entry("Совсем другое", "r-1")]
    out = select_match(
        Task(title="Подготовить квартальный бюджет"), registry,
        backend="linear", container_id="team-A",
        openrouter_client=llm, model="m",
    )
    assert out is None
    llm.complete.assert_not_called()
```

> Note: `test_select_match_borderline_delegates_to_llm` depends on the chosen titles scoring within `FUZZY_LOW..FUZZY_HIGH`. If the assertion `llm.complete.assert_called_once()` fails because the pair scored ≥HIGH or <LOW, adjust the new-task title so the normalized SequenceMatcher ratio lands in the band (print `find_candidates(...)[0][1]` to tune). Do NOT change the thresholds.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tasks_dedup.py -k "low_param or select_match" -v`
Expected: FAIL — `ImportError: cannot import name 'select_match'` / `find_candidates() got an unexpected keyword argument 'low'`.

- [ ] **Step 3: Implement**

In `tasks/dedup.py`, change `find_candidates` to accept `low` (keyword-only, defaulting to the module constant) and use it instead of the hard-coded `FUZZY_LOW`:

```python
def find_candidates(
    new_task: Task,
    registry: list[SentTask],
    *,
    backend: str,
    container_id: str,
    low: float = FUZZY_LOW,
) -> list[tuple[SentTask, float]]:
```

…and in the body change the filter line:

```python
        if score >= low:
            scored.append((sent, score))
```

(Update the docstring's "with ``score >= FUZZY_LOW``" to "with ``score >= low`` (default ``FUZZY_LOW``)".)

Then append the orchestrator at the end of the module:

```python
def select_match(
    new_task: Task,
    registry: list[SentTask],
    *,
    backend: str,
    container_id: str,
    openrouter_client,
    model: str,
    high: float = FUZZY_HIGH,
    low: float = FUZZY_LOW,
) -> SentTask | None:
    """Full dedup decision for one new task: find -> threshold -> (LLM).

    Returns the matched ``SentTask`` or ``None``. Top score ``>= high`` is a
    confident duplicate (NO LLM call). A non-empty borderline band
    (``low..high``) is handed to ``disambiguate_via_llm``. Empty candidate
    set -> ``None`` without any LLM spend. ``high``/``low`` come from config
    (``dedup_fuzzy_high``/``dedup_fuzzy_low``) with the module constants as
    defaults. This is the single Tk-free entry point the dialog driver calls
    per task — it carries the whole matching policy so the UI layer stays a
    thin assigner.
    """
    candidates = find_candidates(
        new_task, registry, backend=backend, container_id=container_id, low=low,
    )
    if not candidates:
        return None
    if candidates[0][1] >= high:
        return candidates[0][0]
    return disambiguate_via_llm(
        new_task, [c for c, _ in candidates], openrouter_client, model,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tasks_dedup.py -v` → PASS (all, incl. PR-2's 18). `ruff check tasks/dedup.py tests/test_tasks_dedup.py` → clean.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/task-dedup-ui" && git add tasks/dedup.py tests/test_tasks_dedup.py && git commit -m "feat(dedup): select_match orchestrator + find_candidates low override"  # + trailer
```

---

## Task 3: sender — comment branch + `COMMENTED` + `meeting_label`

**Files:** Modify `tasks/sender.py`; Test `tests/test_tasks_send.py`

- [ ] **Step 1: Write the failing tests**

Read `tests/test_tasks_send.py` first to match its stub-backend pattern. Append (mirror the file's existing stub style; a stub backend exposes `create()`, and now `supports_comments` + `add_comment()`):

```python
class _CommentBackend:
    """Stub backend that supports comments (records add_comment calls)."""
    supports_comments = True

    def __init__(self):
        self.created = []
        self.comments = []

    def create(self, container_id, task):
        from tasks.backends.base import CreatedIssue
        self.created.append(task)
        return CreatedIssue(identifier="NEW-1", url="http://x/NEW-1", ref="new-ref")

    def add_comment(self, ref, body):
        self.comments.append((ref, body))


def _match(ref="old-ref", identifier="ENG-9", url="http://x/ENG-9"):
    from tasks.dedup import SentTask
    return SentTask(title="t", backend="linear", container_id="team-A",
                    ref=ref, identifier=identifier, url=url,
                    meeting_name="M1", meeting_date="2026-05-01")


def test_send_comments_on_match_instead_of_create():
    from tasks.schema import Task, TaskStatus
    be = _CommentBackend()
    task = Task(title="снова обсудили", dup_match=_match(), dup_action="comment")
    list(send_tasks_iter([task], container_id="team-A", backend=be,
                         on_status_change=lambda *a, **k: None,
                         cancel_check=lambda: False, meeting_label="Планёрка"))
    assert be.created == []                         # no duplicate created
    assert len(be.comments) == 1
    ref, body = be.comments[0]
    assert ref == "old-ref"                         # commented on the existing card
    assert "Планёрка" in body                       # current meeting named in body
    assert task.status is TaskStatus.COMMENTED
    assert task.linear_issue_id == "ENG-9"          # badge points at existing card
    assert task.linear_issue_url == "http://x/ENG-9"
    assert task.backend_ref == "old-ref"


def test_send_create_action_overrides_match():
    from tasks.schema import Task, TaskStatus
    be = _CommentBackend()
    task = Task(title="t", dup_match=_match(), dup_action="create")
    list(send_tasks_iter([task], container_id="team-A", backend=be,
                         on_status_change=lambda *a, **k: None,
                         cancel_check=lambda: False))
    assert len(be.created) == 1 and be.comments == []
    assert task.status is TaskStatus.SENT
    assert task.linear_issue_id == "NEW-1"


def test_send_falls_back_to_create_when_backend_lacks_comments():
    # Belt-and-braces: dup_match set but backend can't comment -> create.
    from tasks.schema import Task, TaskStatus

    class _NoComment:
        supports_comments = False
        def __init__(self): self.created = []
        def create(self, c, t):
            from tasks.backends.base import CreatedIssue
            self.created.append(t)
            return CreatedIssue(identifier="N", url="u", ref="r")
    be = _NoComment()
    task = Task(title="t", dup_match=_match(), dup_action="comment")
    list(send_tasks_iter([task], container_id="team-A", backend=be,
                         on_status_change=lambda *a, **k: None,
                         cancel_check=lambda: False))
    assert len(be.created) == 1
    assert task.status is TaskStatus.SENT


def test_send_comment_failure_marks_failed():
    from tasks.linear_client import LinearError
    from tasks.schema import Task, TaskStatus

    class _BoomComment:
        supports_comments = True
        def create(self, c, t): raise AssertionError("should not create")
        def add_comment(self, ref, body): raise LinearError("Linear 500: boom")
    be = _BoomComment()
    task = Task(title="t", dup_match=_match(), dup_action="comment")
    list(send_tasks_iter([task], container_id="team-A", backend=be,
                         on_status_change=lambda *a, **k: None,
                         cancel_check=lambda: False))
    assert task.status is TaskStatus.FAILED
    assert task.send_error == "500"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tasks_send.py -k "comment or create_action or fallback" -v`
Expected: FAIL — `send_tasks_iter() got an unexpected keyword argument 'meeting_label'` / comment path not implemented.

- [ ] **Step 3: Implement**

In `tasks/sender.py`:

(a) Add `meeting_label` to the signature (keyword-only, default ""):

```python
def send_tasks_iter(
    tasks: list[Task],
    *,
    container_id: str,
    backend,
    on_status_change: Callable,
    cancel_check: Callable[[], bool],
    retry_failed: bool = False,
    meeting_label: str = "",
) -> Iterator[Task]:
```

(b) Replace the create+success block (current :78-110) with a comment-aware version:

```python
        use_comment = (
            task.dup_match is not None
            and task.dup_action == "comment"
            and getattr(backend, "supports_comments", False)
        )

        try:
            if use_comment:
                backend.add_comment(
                    task.dup_match.ref, _dup_comment_body(task, meeting_label),
                )
            else:
                issue = backend.create(container_id, task)
        except (LinearError, GlideError, TrelloError) as e:
            task.status = TaskStatus.FAILED
            task.send_error = _short_error_code(str(e)) or "error"
            logger.warning(
                "send failed for task %r (%s): %s",
                task.local_id, task.title, e,
            )
            on_status_change(task, TaskStatus.FAILED)
            yield task
            continue
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.send_error = _short_error_code(str(e)) or "error"
            logger.exception(
                "unexpected error sending task %r (%s)",
                task.local_id, task.title,
            )
            on_status_change(task, TaskStatus.FAILED)
            yield task
            continue

        if use_comment:
            # Dedup: point the row at the EXISTING card and mark COMMENTED.
            task.status = TaskStatus.COMMENTED
            task.linear_issue_id = task.dup_match.identifier or None
            task.linear_issue_url = task.dup_match.url or None
            task.backend_ref = task.dup_match.ref
        else:
            task.status = TaskStatus.SENT
            task.linear_issue_id = issue.identifier
            task.linear_issue_url = issue.url
            task.backend_ref = issue.ref
        task.send_error = None
        on_status_change(task, task.status)
        yield task
```

(c) Add the body helper near the other helpers (after `_short_error_code`), plus the `date` import at the top:

At the top, add to the imports:
```python
from datetime import date
```
Helper:
```python
def _dup_comment_body(task: Task, meeting_label: str) -> str:
    """RU comment posted to the existing card when a task recurs."""
    where = f' "{meeting_label}"' if meeting_label else ""
    body = (
        f"🔁 Эта задача снова обсуждалась на встрече{where} "
        f"({date.today().isoformat()})."
    )
    if task.description:
        body += f"\n\n{task.description}"
    return body
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tasks_send.py -v` → PASS (all, incl. existing). `ruff check tasks/sender.py tests/test_tasks_send.py` → clean.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/task-dedup-ui" && git add tasks/sender.py tests/test_tasks_send.py && git commit -m "feat(sender): comment-on-match branch + COMMENTED status"  # + trailer
```

---

## Task 4: task_row — dedup badge + toggle + `COMMENTED` badge

**Files:** Modify `ui/dialogs/extract_tasks/task_row.py`; Test `tests/test_dialog_dedup_ui.py` (**create**, source-text only)

- [ ] **Step 1: Write the failing source-text tests**

Create `tests/test_dialog_dedup_ui.py` (NO Tk import — read the file text, like `test_ui_constants.py`):

```python
"""Source-text checks for the dedup UI wiring (PR-3).

CustomTkinter / ui.app must NOT be imported on Linux CI (sounddevice loads
PortAudio at import). We assert on the FILE TEXT instead — structural
guarantees that the badge/toggle/driver are present and wired.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW = (ROOT / "ui" / "dialogs" / "extract_tasks" / "task_row.py").read_text("utf-8")
DIALOG = (ROOT / "ui" / "dialogs" / "extract_tasks" / "__init__.py").read_text("utf-8")
SETTINGS = (ROOT / "ui" / "dialogs" / "settings.py").read_text("utf-8")
CONFIG = (ROOT / "config.example.json").read_text("utf-8")


def test_task_row_has_dedup_badge_and_toggle():
    assert "set_dup_visual" in ROW
    assert "возможный дубль" in ROW
    assert "CTkSegmentedButton" in ROW
    assert "Закомментировать" in ROW and "Создать новую" in ROW
    assert "dup_action" in ROW


def test_task_row_renders_commented_badge():
    assert "COMMENTED" in ROW  # set_status_visual handles the commented state
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_dialog_dedup_ui.py -k task_row -v`
Expected: FAIL — strings absent.

- [ ] **Step 3: Implement**

In `ui/dialogs/extract_tasks/task_row.py`:

(a) Add a `COMMENTED` branch to `set_status_visual` (after the `SENT` branch, before `FAILED`):

```python
        elif status is TaskStatus.COMMENTED:
            self._status_badge.configure(text="🔁", text_color=BLUE_DIM)
            base = self._summary_text()
            self._lbl_summary.configure(
                text=f"{base}  ·  {identifier}" if identifier else base,
            )
```

(b) Add a `set_dup_visual` method that builds the badge+toggle on a third row (only when a match exists). Place it after `set_status_visual`:

```python
    def set_dup_visual(self) -> None:
        """Show the «возможный дубль» badge + comment/create toggle.

        Only meaningful pre-send (status PENDING) when ``task.dup_match`` is
        set. Builds a third row lazily; idempotent (safe to call once per
        render). Picking «Создать новую» flips ``task.dup_action``.
        """
        match = self._task.dup_match
        if match is None:
            return
        if hasattr(self, "_dup_frame"):
            self._dup_frame.grid()
            return
        self._dup_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._dup_frame.grid(row=2, column=1, padx=4, pady=(0, 8), sticky="ew")

        ident = match.identifier or "?"
        self._dup_badge = ctk.CTkLabel(
            self._dup_frame, text=f"🔁 возможный дубль → {ident}",
            font=ctk.CTkFont(family=FONT, size=12, weight="bold"),
            text_color=BLUE_DIM, anchor="w", cursor="hand2",
        )
        self._dup_badge.grid(row=0, column=0, sticky="w")
        if match.url:
            self._dup_badge.bind(
                "<Button-1>", lambda _e, u=match.url: webbrowser.open(u),
            )

        self._dup_action_var = ctk.StringVar(
            value="Закомментировать" if self._task.dup_action == "comment"
            else "Создать новую",
        )
        self._dup_toggle = ctk.CTkSegmentedButton(
            self._dup_frame,
            values=["Закомментировать", "Создать новую"],
            variable=self._dup_action_var,
            command=self._handle_dup_action,
            font=ctk.CTkFont(family=FONT, size=11),
            selected_color=BLUE_DIM, selected_hover_color=BLUE_DIM,
        )
        self._dup_toggle.grid(row=1, column=0, pady=(4, 0), sticky="w")

    def _handle_dup_action(self, value: str) -> None:
        self._task.dup_action = (
            "comment" if value == "Закомментировать" else "create"
        )
```

(No new theme imports needed — `BLUE_DIM`, `FONT` are already imported.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_dialog_dedup_ui.py -k task_row -v` → PASS. `ruff check ui/dialogs/extract_tasks/task_row.py tests/test_dialog_dedup_ui.py` → clean.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/task-dedup-ui" && git add ui/dialogs/extract_tasks/task_row.py tests/test_dialog_dedup_ui.py && git commit -m "feat(extract): dedup badge + comment/create toggle + COMMENTED row"  # + trailer
```

---

## Task 5: dialog — `_run_dedup` driver + badge wiring + send label

**Files:** Modify `ui/dialogs/extract_tasks/__init__.py`; Test `tests/test_dialog_dedup_ui.py`

- [ ] **Step 1: Write the failing source-text tests**

Append to `tests/test_dialog_dedup_ui.py`:

```python
def test_dialog_runs_dedup_on_worker_before_success_dispatch():
    assert "_run_dedup" in DIALOG
    assert "build_sent_registry" in DIALOG
    assert "select_match" in DIALOG
    # gated on capability + config
    assert "supports_comments" in DIALOG
    assert 'dedup_enabled' in DIALOG
    # driver invoked before the success dispatch
    assert DIALOG.index("self._run_dedup(") < DIALOG.index(
        "self.after(0, self._on_extract_success")


def test_dialog_renders_dup_badge_after_row_build():
    assert "set_dup_visual" in DIALOG


def test_dialog_passes_meeting_label_to_send():
    assert "meeting_label=" in DIALOG
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_dialog_dedup_ui.py -k dialog -v`
Expected: FAIL — strings absent.

- [ ] **Step 3: Implement**

(a) Add the driver method (place it right after `_run_extraction`, before `_on_extract_success`). Lazy imports inside, best-effort:

```python
    def _run_dedup(self, tasks, *, backend, backend_name, container_id,
                   openrouter, model) -> None:
        """Worker-thread best-effort dedup pass: set ``task.dup_match`` on
        recurring tasks so the editor can offer "comment instead of dupe".

        Skipped when the backend can't comment (Glide) or the user disabled
        dedup. Any registry/LLM failure is logged and swallowed — a dedup
        hiccup must never block showing the freshly-extracted tasks.
        """
        if not getattr(backend, "supports_comments", False):
            return
        if not bool(self._config.get("dedup_enabled", True)):
            return
        import logging as _logging

        from tasks.dedup import FUZZY_HIGH, FUZZY_LOW, build_sent_registry, select_match
        from tasks.openrouter_client import OpenRouterError
        from tasks.persistence import PersistenceError, load_tasks
        from utils import list_history_entries

        high = float(self._config.get("dedup_fuzzy_high", FUZZY_HIGH))
        low = float(self._config.get("dedup_fuzzy_low", FUZZY_LOW))
        try:
            registry = build_sent_registry(
                list_history_entries(), load_tasks,
                exclude_folder=self._history_folder,
            )
        except (OSError, PersistenceError) as e:
            _logging.getLogger(__name__).warning("dedup registry build failed: %s", e)
            return
        for task in tasks:
            if self._cancel_event.is_set():
                return
            try:
                task.dup_match = select_match(
                    task, registry, backend=backend_name,
                    container_id=container_id, openrouter_client=openrouter,
                    model=model, high=high, low=low,
                )
            except OpenRouterError as e:
                _logging.getLogger(__name__).warning("dedup match failed: %s", e)
```

(b) Call it on the worker, just before the success dispatch. Change `_run_extraction`'s tail (currently :970-971):

```python
            self._remember_recent_model(model)

            if not self._cancel_event.is_set():
                self._run_dedup(
                    result["tasks"], backend=backend, backend_name=backend_name,
                    container_id=container.id, openrouter=openrouter, model=model,
                )

            if not self._cancel_event.is_set():
                self.after(0, self._on_extract_success, result, meta)
```

(c) In `_render_task_list`, after the row is created and the status badge re-applied (after the `if task.status is not TaskStatus.PENDING:` block, ~:1421), render the dedup badge:

```python
            self._task_rows.append(row)
            # PR-3: show the dedup badge+toggle for pre-send matches.
            if task.status is TaskStatus.PENDING and task.dup_match is not None:
                row.set_dup_visual()
```

(Place the `set_dup_visual` call so it runs once per row; keep `self._task_rows.append(row)` ordering as in the original — append, then the badge call, both inside the `for task in self._tasks:` loop.)

(d) Pass `meeting_label` into the send. In `_run_send_worker` (:1852), add the kwarg using the current meeting's folder name:

```python
            for _ in send_tasks_iter(
                self._tasks,
                container_id=container_id,
                backend=backend,
                on_status_change=self._on_send_status_change,
                cancel_check=self._cancel_event.is_set,
                retry_failed=retry_failed,
                meeting_label=os.path.basename(self._history_folder),
            ):
```

(`os` is already imported in this module — it's used at :1029. Confirm with a quick grep; if absent, add `import os` at top.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_dialog_dedup_ui.py -v` → PASS. `ruff check ui/dialogs/extract_tasks/__init__.py tests/test_dialog_dedup_ui.py` → clean.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/task-dedup-ui" && git add ui/dialogs/extract_tasks/__init__.py tests/test_dialog_dedup_ui.py && git commit -m "feat(extract): run dedup on extraction worker + wire badge + send label"  # + trailer
```

---

## Task 6: config keys + Settings `dedup_enabled` checkbox

**Files:** Modify `config.example.json`, `ui/dialogs/settings.py`; Test `tests/test_dialog_dedup_ui.py`

- [ ] **Step 1: Write the failing source-text tests**

Append to `tests/test_dialog_dedup_ui.py`:

```python
def test_config_example_has_dedup_keys():
    import json
    cfg = json.loads(CONFIG)
    assert cfg["dedup_enabled"] is True
    assert 0.0 < cfg["dedup_fuzzy_low"] < cfg["dedup_fuzzy_high"] < 1.0


def test_settings_has_dedup_enabled_checkbox():
    assert "dedup_enabled" in SETTINGS
    assert "дубл" in SETTINGS.lower()  # Russian label mentions duplicates
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_dialog_dedup_ui.py -k "config_example or settings_has_dedup" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

(a) `config.example.json` — add three keys (after `"trello_enabled": false,`):

```json
  "trello_enabled": false,
  "dedup_enabled": true,
  "dedup_fuzzy_high": 0.82,
  "dedup_fuzzy_low": 0.55,
```

(b) `ui/dialogs/settings.py` — read the per-backend `*_enabled` checkbox block first (grep `linear_enabled` to find the exact construction + how its `BooleanVar` is saved on apply), then add a sibling checkbox bound to `dedup_enabled`. Concretely:
- Add a `self._dedup_enabled_var = ctk.BooleanVar(value=bool(config.get("dedup_enabled", True)))` where the other `*_enabled` vars are created.
- Add a `CTkCheckBox(..., text="Искать дубликаты задач (комментировать вместо дубля)", variable=self._dedup_enabled_var)` in the backend/tasks section of the form.
- In the apply/save handler (where `linear_enabled` etc. are written back), add `config["dedup_enabled"] = bool(self._dedup_enabled_var.get())`.

Follow the EXACT pattern of the existing `*_enabled` checkboxes (label via `CTkLabel`/checkbox text per the file's convention — heed [[feedback-ctk-placeholder-hidden-by-textvariable]] if a textvariable+placeholder is involved; here it's a checkbox, so just `text=`).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_dialog_dedup_ui.py -v` → PASS. `ruff check ui/dialogs/settings.py tests/test_dialog_dedup_ui.py` → clean.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/task-dedup-ui" && git add config.example.json ui/dialogs/settings.py tests/test_dialog_dedup_ui.py && git commit -m "feat(settings): dedup_enabled toggle + config keys"  # + trailer
```

---

## Task 7: Full-suite gate + manual smoke + finish

**Files:** none (verification + handoff).

- [ ] **Step 1:** `python -m pytest` → all green (563 + new). Investigate any failure.
- [ ] **Step 2:** `python -m ruff check .` → clean.
- [ ] **Step 3:** `git status` → only intended commits; user's `cli/` WIP untouched.
- [ ] **Step 4 (MANDATORY — behaviour changes here):** Manual GUI smoke from the MAIN repo (not a worktree — [[feedback-run-app-from-main-not-worktree]]; gitignored `config.json`/`history/` live there). Per [[feedback-ctk-placeholder-hidden-by-textvariable]] structural tests can't catch render bugs — this MUST be done by a human:
  1. Send a task to Linear (or Trello) from one meeting.
  2. Open a NEW meeting, extract tasks that include a near-duplicate of that task → confirm the «🔁 возможный дубль → ENG-…» badge appears with the toggle.
  3. Leave «Закомментировать» → send → confirm a COMMENT appears on the ORIGINAL card (not a new card) and the row shows the 🔁 COMMENTED badge linking to the existing card.
  4. On another match, switch to «Создать новую» → send → confirm a NEW card is created (SENT).
  5. Confirm Glide: badge/toggle never appear (supports_comments=False), task creates as usual.
  6. Toggle `dedup_enabled` off in Settings → re-extract → confirm no badges.
- [ ] **Step 5:** Finish via `superpowers:finishing-a-development-branch` → push `feat/task-dedup-ui`, open PR vs `main`. PR body: Summary + Test plan; note this is **PR-3 of 3** (final — first behaviour change), list the manual-smoke checklist as unchecked boxes for the human, and that it completes the dedup feature. Do NOT merge (user's checkpoint).

---

## Self-Review

**Spec coverage (vs foamy-wobbling-owl PR-3):** dedup-after-extraction in the dialog ✓(T5) · `dup_match`/`dup_action` transient on Task ✓(T1) · skip when `not supports_comments` / `dedup_enabled` off ✓(T5 gate) · badge «🔁 возможный дубль → {identifier}» click→url ✓(T4) · per-row comment/create toggle ✓(T4) · sender `add_comment` branch + `COMMENTED` + linear_issue_* from match ✓(T3) · RU comment body with meeting name + date + optional description ✓(T3 `_dup_comment_body`) · Glide degradation (gate both dialog & sender) ✓(T3/T5) · config keys `dedup_enabled`/`dedup_fuzzy_high`/`dedup_fuzzy_low` ✓(T6) · source-text-only UI tests ✓(T4/T5/T6) · uses extraction model for LLM ✓(T5 passes `model`).

**Placeholder scan:** T3/T6 Step 3(b) are read-first against existing patterns (stub-backend fixture shape; the `*_enabled` checkbox construction) — acceptable per writing-plans (the exact shape lives in those files); every other step is code-complete.

**Type/name consistency:** `Task.dup_match: SentTask|None` / `dup_action: str` (T1) ← set by `select_match` return (T2) ← read by sender `use_comment` (T3) and `set_dup_visual` (T4) and `_run_dedup` (T5). `select_match(..., high, low)` (T2) called with config floats (T5). `send_tasks_iter(..., meeting_label="")` (T3) ← `os.path.basename(self._history_folder)` (T5). `TaskStatus.COMMENTED` (T1) → `set_status_visual` branch (T4) ← generic `_update_row_status` (existing :1900). `set_dup_visual()` (T4) ← called in `_render_task_list` (T5).

**Out of scope:** live tracker-API search (future phase); backfilling old sent tasks without `backend_ref` (Linear `ENG-123`→UUID resolve — deferred per MVP). No engine behaviour change beyond the additive `low` param + `select_match`.
