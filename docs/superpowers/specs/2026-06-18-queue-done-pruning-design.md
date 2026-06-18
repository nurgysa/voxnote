# Queue DONE-pruning — design

Date: 2026-06-18
Status: approved (Approach B)
Topic: deferred hygiene for the transcription queue (`processing/worker.py`)

## Context

The transcription queue (spec `2026-06-14-voxnote-transcription-queue-design.md`,
A→C shipped through #159) persists its active items to `~/.voxnote/queue.json`.
`ProcessingQueue._persist_locked` currently writes **every** `auto=True` item —
**including completed ones** (`StageStatus.DONE`). Completed items therefore
linger in the in-memory list and in `queue.json` forever; the only thing that
ever removes one is `forget(item_id)`, called when its meeting is deleted from
«Встречи» (the C2 ghost-row fix).

Consequences:

- **`queue.json` grows unbounded.** Every transcription a user ever runs stays
  in the active-queue file.
- **`snapshot()` carries dead rows.** The 10 s inbox poll and every indicator
  refresh deep-copy the whole list, DONE items included.
- **Cross-restart inbox dedup leans on a filter, not on the data.** A DONE
  item's `audio_path` still points at the original inbox file (the worker
  `move`s inbox/record audio to Drive `sources/`, but only `source_path` is
  rewritten — `audio_path` keeps the stale original). After a restart that stale
  path reloads from `queue.json`. The C3 inbox dedup already ignores DONE items
  (`it.status != StageStatus.DONE`), so correctness holds today — but the stale
  path should not exist at all.

## Goal

`queue.json` and the loaded in-memory list hold **active work only**
(PENDING / RUNNING / ERROR). A finished meeting lives on disk — its
`transcript.md` + `speakers.json` in the meeting folder — which is already the
source of truth for the «Встречи» history view (`store.build_view`).

## Non-goals

- **No change to the live in-session view.** DONE items produced during the
  current run stay in the in-memory list until the app closes, so «Встречи»
  shows a "just finished" overlay row immediately after completion. They are
  simply never persisted, and are dropped on the next load.
- **Not the stricter "active-only in memory too" variant** (drop DONE from
  `self._items` the instant the worker finishes). It buys a marginally cleaner
  invariant at the cost of breaking every test that asserts DONE appears in
  `snapshot()` after processing, for ~zero user-visible gain (DONE is not
  counted in the indicator, and «Встречи» reads finished meetings from disk).
  Rejected as YAGNI.
- **Not the other deferred-hygiene items** (dismiss a stuck ERROR; cost hint at
  enqueue) — separate, out of scope here.

## Why no data is lost when a DONE item is dropped

A finished meeting's display data comes from disk, not from the queue item:

- `store.build_view` derives each history row from the meeting folder
  (`transcript.md` ⇒ DONE; `protocol.md` / `tasks.md` presence ⇒ Hermes badges;
  `project_id` from `speakers.json`). It overlays an active item only when one
  still exists for that folder.
- The queue-item-only fields are **not displayed for a finished meeting**:
  - `nudge_delivered` — referenced nowhere in `ui/` (only worker/model/tests).
  - `started_at` — used only to render the running-row mm:ss timer
    (`ui/dialogs/meetings_view.py` `format_elapsed`), irrelevant once DONE.

So after a restart, a finished meeting renders identically whether or not its
old queue item survived.

## Design (Approach B)

Two point changes in `processing/worker.py`; no model or schema change.

### 1. Persist active-only

`_persist_locked` excludes DONE:

```python
def _persist_locked(self) -> None:
    # Caller holds self._lock. queue.json carries ACTIVE items only —
    # a finished meeting lives on disk (its transcript.md); persisting DONE
    # here would grow queue.json without bound and leak a stale audio_path
    # into the inbox dedup across restarts. build_view re-reads finished
    # meetings from their folders for «Встречи».
    store.save_active(
        [it for it in self._items if it.auto and it.status != StageStatus.DONE],
        self._queue_path,
    )
```

DONE items remain in `self._items` for the session (live overlay) but never
reach `queue.json`.

### 2. Drop DONE on load

In `__init__`, fold a DONE drop into the existing interrupted-RUNNING remap so
legacy `queue.json` files (written before this change) are cleaned on first
load and `snapshot()` is correct from the first tick:

```python
interrupted = [it for it in self._items if it.status == StageStatus.RUNNING]
for it in interrupted:
    it.status = StageStatus.ERROR
    it.error_message = (
        "Обработка прервана (приложение было перезапущено). "
        "Нажми «Повторить», чтобы запустить заново."
    )
# DONE items in a loaded queue are legacy (pre-pruning): a finished meeting
# belongs to disk, not the active queue. Drop them so the active list and
# queue.json hold active work only and no stale audio_path survives a restart.
had_done = any(it.status == StageStatus.DONE for it in self._items)
if had_done:
    self._items = [it for it in self._items if it.status != StageStatus.DONE]
if interrupted or had_done:
    store.save_active([it for it in self._items if it.auto], self._queue_path)
```

(After the drop no DONE remain, so the `if it.auto` save filter is sufficient;
it stays consistent with the existing interrupted-save call.)

## Edge cases

- **ERROR items still persist and survive a restart** — untouched by both
  changes, so `retry()` and crash-resume (RUNNING→ERROR) keep working.
- **`source_path` already recorded for a DONE item** — irrelevant once dropped;
  the archived audio in `sources/` is independent of `queue.json`.
- **Ghost-row bug (C2)** — strictly reinforced: after a restart there is no
  lingering active DONE item to mismatch a deleted folder, so the disk row is a
  plain `id=folder` row that `delete_history_entry` handles directly.
- **Inbox dedup (C3)** — the cross-restart stale-path case disappears entirely;
  the existing `status != DONE` filter becomes belt-and-suspenders.

## Test plan

Real unit tests against `ProcessingQueue` / `store` (headless module — no Tk):

1. **DONE not persisted.** Enqueue + drive an item to DONE; `store.load_active`
   on the queue path returns no DONE item, while a PENDING/ERROR item in the
   same queue *is* persisted.
2. **Load drops legacy DONE.** Write a `queue.json` containing a DONE item plus
   a PENDING item; construct `ProcessingQueue`; assert `snapshot()` omits the
   DONE item, and the on-disk file was re-saved without it.
3. **In-session overlay preserved.** After processing to DONE (no restart),
   `snapshot()` still contains the DONE item (in-memory overlay intact).
4. **Regressions.** ERROR persists + reloads; RUNNING→ERROR remap still fires
   and still re-saves; an ERROR + DONE mix on load keeps ERROR, drops DONE.

`pytest` green (baseline ≈ 1062) and `ruff` clean before commit.

## Out of scope

Dismiss-stuck-ERROR UI and enqueue cost-hint remain queued (see the
transcription-queue status memory).
