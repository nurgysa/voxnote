# Dismiss a stuck ERROR queue item — design

Date: 2026-06-19
Status: approved
Topic: deferred hygiene for the transcription queue («Встречи» dialog)

## Context

When a queue item fails, it settles in `StageStatus.ERROR` and shows in «Встречи»
with an error message and a single «↻ Повторить» (retry) button
(`ui/dialogs/meetings.py` `_build_row`, the `elif item.status == StageStatus.ERROR`
branch). There is **no way to clear it** — a permanently-failing item (bad file,
gone provider, unsupported language) sits forever and keeps the main-bar indicator
reading «… · N ошибок».

The backend already supports removal: `ProcessingQueue.forget(item_id)`
(`processing/worker.py`) drops any non-RUNNING item, repersists, and notifies; it is
unit-tested (`tests/test_processing_worker.py::test_forget_*`). The gap is purely the
missing UI affordance. The existing «✕» button and `_delete` are bound to DONE
meetings only and gate on `if folder` (`meetings.py` lines ~347-367, ~407-415), so an
ERROR item — whose `meeting_folder` is normally `None` — cannot be cleared today.

## Goal

Let the user clear a stuck ERROR item from the queue in one click, reusing the
existing `forget` backend.

## Design

In `ui/dialogs/meetings.py`:

1. In `_build_row`, the ERROR branch gains a second button «✕ Убрать» beside
   «↻ Повторить», wired to a new `_dismiss(item)`.
2. `_dismiss(item)` calls `self._queue.forget(item.id)` then `self._render()`.
   **Instant — no confirmation** (the user explicitly chose this): a stuck error is
   something to dismiss like a notification, and there is no data to lose.

No model/worker change — `forget` already does the work.

### What dismiss does NOT do

- **No folder deletion.** Unlike `_delete` (which `rmtree`s a transcript folder),
  `_dismiss` only evicts the queue item. This is correct in every case:
  - The common ERROR has `meeting_folder = None` (failed before a note was written):
    the row simply disappears.
  - A rare late-failure ERROR (e.g. the Hermes nudge raised *after* `transcript.md`
    was written) has a real folder on disk. `build_view` overlays the active ERROR
    item on that folder's disk row; dismissing forgets the active item, so the row
    reverts to a normal DONE history meeting — **the transcript is preserved**.
- **ERROR only.** PENDING/RUNNING items are not dismissable (RUNNING must never be
  evicted — `forget` already refuses it; PENDING is pending work). Out of scope (YAGNI).
- **Audio is untouched.** On failure the original audio stays in place (queue spec
  §Failure-handling), so a dismissed item can be re-added via «Выбрать файл» if needed.

### Why no confirmation is safe

Dismiss destroys nothing on disk (no folder, audio preserved, transcript — if any —
kept). A misclick costs only the error row + retry affordance, both re-creatable.
A confirm dialog on a zero-data-loss action is friction without payoff.

## Testing

`ui/dialogs/meetings.py` can't be imported under Linux CI (customtkinter → PortAudio),
so the dialog wiring is covered by **source-slice** assertions (read the module text,
assert substrings), matching the existing `tests/test_meetings_dialog_queue.py`:

- `_dismiss` method present; «Убрать» button label present; the ERROR branch wires a
  dismiss action distinct from retry.

Plus a real headless worker test pinning the backend contract this feature relies on:

- `tests/test_processing_worker.py::test_forget_drops_errored_item` — `forget` evicts
  an ERROR item and repersists (sibling to the existing DONE/RUNNING forget tests).

`pytest` green (baseline ≈ 1065) and `ruff` clean before commit.

## Out of scope

Cost-hint at enqueue (`preflight.estimate_cost`) — the remaining deferred-hygiene
item, its own next slice.
