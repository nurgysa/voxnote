# Processing-queue PR-2a — headless worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the headless `ProcessingQueue` serial worker that auto-carries an enqueued audio file through transcribe → protocol → task-draft over the `cli.core` seam, plus the `layout.assign_project` placement entry point it needs — with zero Tk and zero UI wiring (that is PR-2b).

**Architecture:** A third frontend over `cli.core` (after GUI and CLI). A single daemon thread picks the next `auto=True` item with a `PENDING` stage, runs that stage via the existing `cli.core.run_*` functions, writes the artifact into the meeting folder, persists `queue.json`, and advances — halting the item on a stage error. The thread never touches widgets; the UI (PR-2b) reads via `snapshot()` and an injected `on_change` callback. Config/keys resolve through an injected `config_loader` (prod: `cli.config.merged_config`) so the worker reads persisted settings, never mutable UI vars. Project resolution (`project_id` → `Project`) is an injected `resolve_project` callable so `processing/` stays decoupled from the directory store and from Tk.

**Tech Stack:** Python 3.10+ stdlib (`threading`, `dataclasses`, `os`), `pytest` with `monkeypatch`/`tmp_path`. Reuses `processing/{model,store,layout}` (PR-1), `cli.core`, `utils`, `tasks.errors.humanize`, `directory` (only via the injected resolver).

---

## Scope (PR-2a only)

**In:** `processing/layout.assign_project`, `processing/worker.py` (`ProcessingQueue`), their tests, a broad-`except` ratchet baseline bump, and a one-line `CLAUDE.md` "Where things live" update.

**Out (PR-2b / PR-3):** any `ui/` change — record-stop/file-picker enqueue, main-bar project selector, removing «Транскrибировать», relocating the run-loop out of `transcription_mixin`, the «История» queue view, the indicator strip, the App-side poller, draft-review mode, reassignment-from-UI, project-rename → folder-rename, the migration script. PR-2a ships a fully unit-tested worker that nothing in the GUI calls yet (deliberate dead code until PR-2b wires it — accepted because the worker is independently testable and mergeable without a GUI smoke).

## File Structure

- **`processing/layout.py`** (modify) — add `assign_project(meeting_folder, project, meetings_dir) -> str`. Reuses the existing `target_dir` + `move_into`; reads/writes `speakers.json` via `utils.load_speakers`/`utils.save_speakers` (load-merge-save: only `project_id` changes, `participants`/`speakers` preserved). Stays decoupled — caller passes a resolved `Project | None`, not an id.
- **`processing/worker.py`** (create) — `ProcessingQueue`. Owns the active-item list, a `threading.Lock`, a `threading.Event` wake, and one daemon thread. Public: `start`, `stop`, `enqueue`, `retry`, `snapshot`. Stage runners call `cli.core.run_*`. Module-level broad-`except` only at the stage boundary (justified comment + ratchet bump).
- **`tests/test_processing_layout_assign.py`** (create) — `assign_project` unit tests.
- **`tests/test_processing_worker.py`** (create) — worker unit tests (all `cli.core.run_*` patched; `utils.get_meetings_dir` monkeypatched to `tmp_path`).
- **`tests/test_broad_except_ratchet.py`** (modify) — add `processing/worker.py` to `BASELINE`.
- **`CLAUDE.md`** (modify) — one-line "Where things live" update: `processing/` now includes `worker.py`.

## Key grounding (verified against current code — do not re-invent)

- `cli.core.run_transcribe(audio_path, *, provider, api_key, language=None, diarize=False, hotwords=None, denoise=False, on_status=None) -> TranscribeOutput` with `.text`, `.language`, `.segments`.
- `cli.core.run_protocol(*, transcript, lang, model, openrouter_key, speakers=(), meeting_date="") -> ProtocolResult` with `.markdown`.
- `cli.core.run_extract_tasks(*, transcript, lang, model, openrouter_key, backend_name=None, container_id=None, config=None) -> dict` with `tasks` (list of `Task`), `corrections`, `model`.
- `cli.core.DEFAULT_MODEL == "google/gemini-3.5-flash"`.
- `utils.create_history_entry(audio_file_path, transcript_text, language, model) -> folder` (creates `<meetings_dir>/<ts>_<base>/` with audio copy + `transcript.md` + `description.md`; uses `get_meetings_dir()` internally).
- `utils.save_segments(folder, segments)`, `utils.save_speakers(folder, project_id, participant_ids, speaker_map=None)`, `utils.load_speakers(folder) -> dict`, `utils.should_delete_after_transcription(config, audio_path) -> bool`.
- `processing.layout.target_dir(meetings_dir, project) -> str`, `processing.layout.move_into(folder, dest_dir) -> str` (collision-safe; no-op when already there).
- `directory.schema.Project` has `.id`, `.name`.
- `tasks.errors.humanize(exc, *, fallback=None) -> str` (never raises, always non-empty).
- `tasks.schema.Task.to_dict()` / `Task.from_dict(d)`.
- `processing.store.load_active(path) -> list[QueueItem]`, `save_active(items, path)`.
- `processing.model.QueueItem` fields: `id, audio_path, title, created_at, meeting_folder, options, auto, project_id, transcript, protocol, tasks, error_stage, error_message`; `StageStatus.{PENDING,RUNNING,DONE,ERROR,AWAITING_REVIEW}`.

**Invariant to preserve:** the worker's `meetings_dir` and `create_history_entry`'s internal `get_meetings_dir()` must agree. In prod both are `utils.get_meetings_dir()`; in tests, monkeypatch `utils.get_meetings_dir` to return `tmp_path` AND pass that same path as `meetings_dir`.

---

### Task 1: `layout.assign_project`

**Files:**
- Modify: `processing/layout.py`
- Test: `tests/test_processing_layout_assign.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_processing_layout_assign.py
import json
import os

from directory.schema import Project
from processing import layout


def _meeting(tmp_path, name="2026-06-13_10-00-00_call", project_id=None,
             participants=("p1",), speakers=None):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "transcript.md").write_text("hi", encoding="utf-8")
    payload = {
        "project_id": project_id,
        "participants": list(participants),
        "speakers": speakers or {"SPEAKER_00": "p1"},
    }
    (folder / "speakers.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return str(folder)


def test_assign_project_writes_id_and_moves(tmp_path):
    meetings = tmp_path
    folder = _meeting(meetings)
    project = Project(name="Kitng", id="proj-123")

    new_path = layout.assign_project(folder, project, str(meetings))

    # moved under the project dir
    assert os.path.basename(os.path.dirname(new_path)) == "Kitng"
    # speakers.json followed the folder, project_id updated, rest preserved
    with open(os.path.join(new_path, "speakers.json"), encoding="utf-8") as f:
        sp = json.load(f)
    assert sp["project_id"] == "proj-123"
    assert sp["participants"] == ["p1"]
    assert sp["speakers"] == {"SPEAKER_00": "p1"}
    assert not os.path.exists(folder)  # old location gone


def test_assign_project_none_keeps_root_and_clears_id(tmp_path):
    meetings = tmp_path
    folder = _meeting(meetings, project_id="old-proj")

    new_path = layout.assign_project(folder, None, str(meetings))

    assert os.path.normpath(os.path.dirname(new_path)) == os.path.normpath(str(meetings))
    with open(os.path.join(new_path, "speakers.json"), encoding="utf-8") as f:
        sp = json.load(f)
    assert sp["project_id"] is None
    assert sp["participants"] == ["p1"]  # preserved


def test_assign_project_no_speakers_file_creates_one(tmp_path):
    meetings = tmp_path
    folder = tmp_path / "2026-06-13_11-00-00_x"
    folder.mkdir()
    (folder / "transcript.md").write_text("hi", encoding="utf-8")
    project = Project(name="Beta", id="b-1")

    new_path = layout.assign_project(str(folder), project, str(meetings))

    with open(os.path.join(new_path, "speakers.json"), encoding="utf-8") as f:
        sp = json.load(f)
    assert sp["project_id"] == "b-1"
    assert sp["participants"] == []
    assert sp["speakers"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_layout_assign.py -v`
Expected: FAIL with `AttributeError: module 'processing.layout' has no attribute 'assign_project'`.

- [ ] **Step 3: Write minimal implementation**

Add to `processing/layout.py` (after `move_into`). Note the `utils` import is function-local to keep `import processing.layout` lightweight and avoid any import-order surprise:

```python
def assign_project(meeting_folder: str, project: "Project | None", meetings_dir: str) -> str:
    """Set the meeting's project (write speakers.json) and move its folder into
    the project dir (or the root when project is None). The single placement seam
    used by the worker's transcribe stage and (PR-3) by reassignment.

    Writes metadata FIRST, then moves: a failed move leaves a consistent (if
    mislocated) state recoverable on the next assign (spec failure-handling).
    Only project_id changes — participants/speakers are preserved (load-merge-save).
    Returns the folder's new path.
    """
    from utils import load_speakers, save_speakers

    existing = load_speakers(meeting_folder)
    project_id = project.id if project is not None else None
    save_speakers(
        meeting_folder,
        project_id,
        list(existing.get("participants") or []),
        existing.get("speakers") or {},
    )
    return move_into(meeting_folder, target_dir(meetings_dir, project))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_layout_assign.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add processing/layout.py tests/test_processing_layout_assign.py
git commit -m "feat(processing): layout.assign_project — write project_id + move folder"
```

---

### Task 2: `ProcessingQueue` core — enqueue / snapshot / persist / start / stop

**Files:**
- Create: `processing/worker.py`
- Test: `tests/test_processing_worker.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_processing_worker.py
import json

import pytest

from processing.model import StageStatus
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


def test_enqueue_appends_and_persists(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {"provider": "AssemblyAI", "project_id": "p1"})

    snap = q.snapshot()
    assert len(snap) == 1
    assert snap[0].id == item_id
    assert snap[0].audio_path == "/audio/a.m4a"
    assert snap[0].auto is True
    assert snap[0].project_id == "p1"
    assert snap[0].transcript == StageStatus.PENDING

    # queue.json on disk holds the active item
    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["items"][0]["id"] == item_id


def test_snapshot_is_a_deep_copy(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    snap = q.snapshot()
    snap[0].transcript = StageStatus.DONE  # mutate the copy
    assert q.snapshot()[0].transcript == StageStatus.PENDING  # original intact


def test_on_change_fires_on_enqueue(tmp_path):
    calls = []
    q = _queue(tmp_path, on_change=lambda: calls.append(1))
    q.enqueue("/audio/a.m4a", {})
    assert calls == [1]


def test_loads_existing_active_items(tmp_path):
    q1 = _queue(tmp_path)
    q1.enqueue("/audio/a.m4a", {})
    q2 = _queue(tmp_path)  # same queue_path
    assert len(q2.snapshot()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'processing.worker'`.

- [ ] **Step 3: Write minimal implementation**

```python
# processing/worker.py
"""Serial processing-queue worker — the third frontend over cli.core.

A single daemon thread carries each auto=True item through transcribe ->
protocol -> task-draft, calling the same cli.core.run_* functions the CLI and
GUI use, writing artifacts into the meeting folder, and persisting queue.json.
NO Tk: the thread mutates state under a lock and persists; the UI reads via
snapshot() and the injected on_change callback. Config and project resolution
are injected (config_loader / resolve_project) so this module stays headless
and decoupled from the directory store.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime

from cli import core
from processing import layout, store
from processing.model import QueueItem, StageStatus

logger = logging.getLogger(__name__)

_IDLE_WAIT_S = 1.0


class ProcessingQueue:
    def __init__(
        self,
        *,
        meetings_dir: str,
        config_loader: Callable[[], dict],
        resolve_project: Callable[[str | None], object | None],
        queue_path: str | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._meetings_dir = meetings_dir
        self._config_loader = config_loader
        self._resolve_project = resolve_project
        self._queue_path = queue_path
        self._on_change = on_change
        self._items: list[QueueItem] = store.load_active(queue_path)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = False

    # ── public API ──
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._run, name="processing-queue", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def enqueue(self, audio_path: str, options: dict) -> str:
        options = dict(options)
        item = QueueItem(
            id=f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}_{os.path.basename(audio_path)}",
            audio_path=audio_path,
            title=os.path.basename(audio_path),
            created_at=datetime.now().isoformat(timespec="seconds"),
            options=options,
            auto=True,
            project_id=options.get("project_id"),
        )
        with self._lock:
            self._items.append(item)
            self._persist_locked()
        self._wake.set()
        self._notify()
        return item.id

    def retry(self, item_id: str) -> None:
        with self._lock:
            for it in self._items:
                if it.id == item_id:
                    it.error_stage = None
                    it.error_message = None
                    for stage in ("transcript", "protocol", "tasks"):
                        if getattr(it, stage) == StageStatus.ERROR:
                            setattr(it, stage, StageStatus.PENDING)
                    it.auto = True
                    self._persist_locked()
                    break
        self._wake.set()
        self._notify()

    def snapshot(self) -> list[QueueItem]:
        with self._lock:
            return [QueueItem.from_dict(it.to_dict()) for it in self._items]

    # ── internals ──
    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _persist_locked(self) -> None:
        # Caller holds self._lock. queue.json carries active items only.
        store.save_active([it for it in self._items if it.auto], self._queue_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_worker.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add processing/worker.py tests/test_processing_worker.py
git commit -m "feat(processing): ProcessingQueue core — enqueue/snapshot/persist"
```

---

### Task 3: transcribe stage

**Files:**
- Modify: `processing/worker.py`
- Test: `tests/test_processing_worker.py`

- [ ] **Step 1: Write the failing test**

```python
def _fake_transcribe_output(text="hello", language="ru", segments=None):
    class _Out:
        pass
    o = _Out()
    o.text = text
    o.language = language
    o.segments = segments if segments is not None else [{"speaker": "A", "text": "hi"}]
    return o


def test_transcribe_stage_creates_folder_and_marks_done(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    monkeypatch.setattr("utils.get_meetings_dir", lambda: str(meetings))
    monkeypatch.setattr(
        "cli.core.run_transcribe",
        lambda *a, **k: _fake_transcribe_output(),
    )
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"\x00\x00")

    q = _queue(
        tmp_path,
        meetings_dir=str(meetings),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(str(audio), {"provider": "AssemblyAI", "language": "ru"})
    item = q.snapshot()[0]
    # drive ONE stage directly (no thread) for a deterministic unit test
    q._items[0] = item  # use the live item
    ok = q._stage_transcribe(q._items[0])

    assert ok is True
    live = q.snapshot()[0]
    assert live.transcript == StageStatus.DONE
    assert live.meeting_folder and os.path.isdir(live.meeting_folder)
    assert os.path.isfile(os.path.join(live.meeting_folder, "transcript.md"))
    assert os.path.isfile(os.path.join(live.meeting_folder, "segments.json"))


def test_transcribe_stage_missing_key_errors_and_halts(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    monkeypatch.setattr("utils.get_meetings_dir", lambda: str(meetings))
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"\x00")
    q = _queue(tmp_path, meetings_dir=str(meetings), config_loader=lambda: {})
    q.enqueue(str(audio), {"provider": "AssemblyAI"})
    ok = q._stage_transcribe(q._items[0])
    assert ok is False
    live = q.snapshot()[0]
    assert live.transcript == StageStatus.ERROR
    assert live.error_stage == "transcript"
    assert live.error_message  # humanized, non-empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_worker.py -k transcribe -v`
Expected: FAIL with `AttributeError: 'ProcessingQueue' object has no attribute '_stage_transcribe'`.

- [ ] **Step 3: Write minimal implementation**

Add the stage helper + a `_set_stage` helper to `processing/worker.py`:

```python
    def _set_stage(
        self,
        item: QueueItem,
        stage: str,
        status: StageStatus,
        *,
        error_stage: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._lock:
            setattr(item, stage, status)
            item.error_stage = error_stage
            item.error_message = error_message
            self._persist_locked()
        self._notify()

    def _stage_transcribe(self, item: QueueItem) -> bool:
        """Transcribe → create meeting folder → place under project. True to
        continue, False to halt the item (stage error)."""
        self._set_stage(item, "transcript", StageStatus.RUNNING)
        try:
            import utils

            cfg = self._config_loader()
            opts = item.options
            provider = opts.get("provider") or cfg.get("cloud_provider") or "AssemblyAI"
            api_key = (cfg.get("cloud_api_keys") or {}).get(provider)
            if not api_key:
                raise ValueError(f"Нет API-ключа для провайдера {provider!r}.")
            language = opts.get("language") or None
            if language == "auto":
                language = None
            out = core.run_transcribe(
                item.audio_path,
                provider=provider,
                api_key=api_key,
                language=language,
                diarize=bool(opts.get("diarize")),
                hotwords=opts.get("hotwords") or None,
                denoise=bool(opts.get("denoise")),
            )
            folder = utils.create_history_entry(
                item.audio_path, out.text, out.language, f"cloud:{provider}",
            )
            utils.save_segments(folder, out.segments)
            project = self._resolve_project(opts.get("project_id"))
            folder = layout.assign_project(folder, project, self._meetings_dir)
            with self._lock:
                item.meeting_folder = folder
            if utils.should_delete_after_transcription(cfg, item.audio_path):
                try:
                    os.remove(item.audio_path)
                except OSError as e:
                    logger.warning("could not delete recording %s: %s", item.audio_path, e)
            self._set_stage(item, "transcript", StageStatus.DONE)
            return True
        except Exception as e:  # worker-thread boundary: any failure halts the
            # item but must never kill the daemon. Humanize for the UI; the
            # stage's ✗! is the user signal. (CLAUDE.md broad-except: justified
            # boundary, tracked in test_broad_except_ratchet.)
            from tasks.errors import humanize

            logger.exception("transcribe stage failed for item %s", item.id)
            self._set_stage(
                item, "transcript", StageStatus.ERROR,
                error_stage="transcript", error_message=humanize(e),
            )
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_worker.py -k transcribe -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add processing/worker.py tests/test_processing_worker.py
git commit -m "feat(processing): worker transcribe stage (folder + project placement)"
```

---

### Task 4: protocol stage

**Files:**
- Modify: `processing/worker.py`
- Test: `tests/test_processing_worker.py`

- [ ] **Step 1: Write the failing test**

```python
def _done_meeting(q, tmp_path, folder_name="2026-06-13_12-00-00_m"):
    """Create a transcript-DONE item with a real folder + transcript.md."""
    folder = tmp_path / "meetings" / folder_name
    folder.mkdir(parents=True)
    (folder / "transcript.md").write_text("the transcript", encoding="utf-8")
    item_id = q.enqueue("/audio/x.m4a", {"language": "ru"})
    it = q._items[0]
    it.meeting_folder = str(folder)
    it.transcript = StageStatus.DONE
    return it


def test_protocol_stage_writes_protocol_md(tmp_path, monkeypatch):
    class _Proto:
        markdown = "# Протокол\n\n- пункт"
    monkeypatch.setattr("cli.core.run_protocol", lambda *a, **k: _Proto())
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_protocol(it)
    assert ok is True
    assert q.snapshot()[0].protocol == StageStatus.DONE
    assert (tmp_path / "meetings" / "2026-06-13_12-00-00_m" / "protocol.md").read_text(
        encoding="utf-8"
    ) == "# Протокол\n\n- пункт"


def test_protocol_stage_llm_error_halts(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("OpenRouter вернул 500")
    monkeypatch.setattr("cli.core.run_protocol", _boom)
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_protocol(it)
    assert ok is False
    live = q.snapshot()[0]
    assert live.protocol == StageStatus.ERROR
    assert live.error_stage == "protocol"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_worker.py -k protocol -v`
Expected: FAIL — `_stage_protocol` missing.

- [ ] **Step 3: Write minimal implementation**

Add a transcript reader + the protocol stage to `processing/worker.py`:

```python
    def _read_transcript(self, folder: str) -> str:
        for name in ("transcript.md", "transcript.txt"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    return f.read()
        raise FileNotFoundError(f"transcript not found in {folder}")

    def _stage_protocol(self, item: QueueItem) -> bool:
        self._set_stage(item, "protocol", StageStatus.RUNNING)
        try:
            cfg = self._config_loader()
            openrouter_key = cfg.get("openrouter_api_key")
            if not openrouter_key:
                raise ValueError("Нет ключа OpenRouter.")
            language = item.options.get("language") or None
            if language == "auto":
                language = None
            result = core.run_protocol(
                transcript=self._read_transcript(item.meeting_folder),
                lang=language,
                model=cfg.get("openrouter_model") or core.DEFAULT_MODEL,
                openrouter_key=openrouter_key,
            )
            with open(os.path.join(item.meeting_folder, "protocol.md"), "w", encoding="utf-8") as f:
                f.write(result.markdown)
            self._set_stage(item, "protocol", StageStatus.DONE)
            return True
        except Exception as e:  # worker-thread boundary — see _stage_transcribe.
            from tasks.errors import humanize

            logger.exception("protocol stage failed for item %s", item.id)
            self._set_stage(
                item, "protocol", StageStatus.ERROR,
                error_stage="protocol", error_message=humanize(e),
            )
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_worker.py -k protocol -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add processing/worker.py tests/test_processing_worker.py
git commit -m "feat(processing): worker protocol stage (writes protocol.md)"
```

---

### Task 5: task-draft stage

**Files:**
- Modify: `processing/worker.py`
- Test: `tests/test_processing_worker.py`

- [ ] **Step 1: Write the failing test**

```python
def test_tasks_stage_writes_raw_and_awaits_review(tmp_path, monkeypatch):
    from tasks.schema import Task

    fake = {"tasks": [Task(title="Do X")], "corrections": 1, "model": "m"}
    monkeypatch.setattr("cli.core.run_extract_tasks", lambda *a, **k: fake)
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_tasks(it)
    assert ok is True
    assert q.snapshot()[0].tasks == StageStatus.AWAITING_REVIEW
    raw_path = tmp_path / "meetings" / "2026-06-13_12-00-00_m" / "tasks_raw.json"
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    assert data["tasks"][0]["title"] == "Do X"
    assert data["corrections"] == 1
    assert data["model"] == "m"


def test_tasks_stage_error_halts(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("OpenRouter не вернул валидных задач")
    monkeypatch.setattr("cli.core.run_extract_tasks", _boom)
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_tasks(it)
    assert ok is False
    assert q.snapshot()[0].tasks == StageStatus.ERROR
    assert q.snapshot()[0].error_stage == "tasks"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_worker.py -k "tasks_stage" -v`
Expected: FAIL — `_stage_tasks` missing.

- [ ] **Step 3: Write minimal implementation**

Add to `processing/worker.py`:

```python
    def _stage_tasks(self, item: QueueItem) -> bool:
        """Extract a task DRAFT → tasks_raw.json → AWAITING_REVIEW. No send."""
        self._set_stage(item, "tasks", StageStatus.RUNNING)
        try:
            cfg = self._config_loader()
            openrouter_key = cfg.get("openrouter_api_key")
            if not openrouter_key:
                raise ValueError("Нет ключа OpenRouter.")
            language = item.options.get("language") or None
            if language == "auto":
                language = None
            model = cfg.get("openrouter_model") or core.DEFAULT_MODEL
            result = core.run_extract_tasks(
                transcript=self._read_transcript(item.meeting_folder),
                lang=language,
                model=model,
                openrouter_key=openrouter_key,
            )
            tasks = result.get("tasks", [])
            payload = {
                "tasks": [t.to_dict() for t in tasks],
                "corrections": result.get("corrections", 0),
                "model": result.get("model", model),
            }
            target = os.path.join(item.meeting_folder, "tasks_raw.json")
            with open(target, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._set_stage(item, "tasks", StageStatus.AWAITING_REVIEW)
            return True
        except Exception as e:  # worker-thread boundary — see _stage_transcribe.
            from tasks.errors import humanize

            logger.exception("task-draft stage failed for item %s", item.id)
            self._set_stage(
                item, "tasks", StageStatus.ERROR,
                error_stage="tasks", error_message=humanize(e),
            )
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_worker.py -k "tasks_stage" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add processing/worker.py tests/test_processing_worker.py
git commit -m "feat(processing): worker task-draft stage (tasks_raw.json + awaiting_review)"
```

---

### Task 6: `_process_item` + `_run` loop + broad-except ratchet bump

**Files:**
- Modify: `processing/worker.py`, `tests/test_broad_except_ratchet.py`
- Test: `tests/test_processing_worker.py`

- [ ] **Step 1: Write the failing test**

```python
def test_process_item_walks_all_stages(tmp_path, monkeypatch):
    from tasks.schema import Task

    meetings = tmp_path / "meetings"
    meetings.mkdir()
    monkeypatch.setattr("utils.get_meetings_dir", lambda: str(meetings))
    monkeypatch.setattr("cli.core.run_transcribe", lambda *a, **k: _fake_transcribe_output())

    class _Proto:
        markdown = "# P"
    monkeypatch.setattr("cli.core.run_protocol", lambda *a, **k: _Proto())
    monkeypatch.setattr(
        "cli.core.run_extract_tasks",
        lambda *a, **k: {"tasks": [Task(title="T")], "corrections": 0, "model": "m"},
    )
    audio = tmp_path / "r.m4a"
    audio.write_bytes(b"\x00")
    q = _queue(
        tmp_path, meetings_dir=str(meetings),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"}, "openrouter_api_key": "or",
        },
    )
    q.enqueue(str(audio), {"provider": "AssemblyAI", "language": "ru"})
    q._process_item(q._items[0])

    live = q.snapshot()[0]
    assert live.transcript == StageStatus.DONE
    assert live.protocol == StageStatus.DONE
    assert live.tasks == StageStatus.AWAITING_REVIEW


def test_process_item_halts_after_failed_stage(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    monkeypatch.setattr("utils.get_meetings_dir", lambda: str(meetings))

    def _boom(*a, **k):
        raise RuntimeError("AssemblyAI вернул 401")
    monkeypatch.setattr("cli.core.run_transcribe", _boom)
    called = []
    monkeypatch.setattr("cli.core.run_protocol", lambda *a, **k: called.append("p"))
    audio = tmp_path / "r.m4a"
    audio.write_bytes(b"\x00")
    q = _queue(
        tmp_path, meetings_dir=str(meetings),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(str(audio), {"provider": "AssemblyAI"})
    q._process_item(q._items[0])

    live = q.snapshot()[0]
    assert live.transcript == StageStatus.ERROR
    assert live.protocol == StageStatus.PENDING  # never ran
    assert called == []  # protocol stage skipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_worker.py -k process_item -v`
Expected: FAIL — `_process_item` missing.

- [ ] **Step 3: Write minimal implementation**

Add `_process_item`, `_next_auto_item`, and `_run` to `processing/worker.py`:

```python
    def _next_auto_item(self) -> QueueItem | None:
        with self._lock:
            for it in self._items:
                if it.auto and (
                    it.transcript == StageStatus.PENDING
                    or it.protocol == StageStatus.PENDING
                    or it.tasks == StageStatus.PENDING
                ):
                    return it
        return None

    def _process_item(self, item: QueueItem) -> None:
        if item.transcript == StageStatus.PENDING and not self._stage_transcribe(item):
            return
        if item.protocol == StageStatus.PENDING and not self._stage_protocol(item):
            return
        if item.tasks == StageStatus.PENDING:
            self._stage_tasks(item)

    def _run(self) -> None:
        while not self._stop:
            item = self._next_auto_item()
            if item is None:
                self._wake.wait(timeout=_IDLE_WAIT_S)
                self._wake.clear()
                continue
            self._process_item(item)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_worker.py -k process_item -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Bump the broad-except ratchet baseline**

The three stage handlers are justified worker-thread boundaries. Add the file to `BASELINE` in `tests/test_broad_except_ratchet.py` (keep the dict alphabetised by path; insert after the `gdrive/backup.py` line):

```python
    "processing/worker.py": 3,                     # worker-thread stage boundaries
```

- [ ] **Step 6: Run the ratchet + full worker suite**

Run: `py -3 -m pytest tests/test_broad_except_ratchet.py tests/test_processing_worker.py -v`
Expected: PASS (ratchet green with the new baseline; all worker tests pass).

- [ ] **Step 7: Commit**

```bash
git add processing/worker.py tests/test_processing_worker.py tests/test_broad_except_ratchet.py
git commit -m "feat(processing): worker _process_item walk + halt-on-error; ratchet bump"
```

---

### Task 7: `retry` resumes from the failed stage

**Files:**
- Test: `tests/test_processing_worker.py` (retry impl already added in Task 2)

- [ ] **Step 1: Write the failing test**

```python
def test_retry_resets_errored_stage_to_pending(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    it = q._items[0]
    it.transcript = StageStatus.DONE
    it.protocol = StageStatus.ERROR
    it.error_stage = "protocol"
    it.error_message = "boom"

    q.retry(item_id)

    live = q.snapshot()[0]
    assert live.transcript == StageStatus.DONE      # done stage untouched
    assert live.protocol == StageStatus.PENDING     # errored stage reset
    assert live.error_stage is None
    assert live.error_message is None
    assert live.auto is True


def test_retry_unknown_id_is_noop(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q.retry("nope")  # must not raise
    assert len(q.snapshot()) == 1
```

- [ ] **Step 2: Run test to verify it fails OR passes**

Run: `py -3 -m pytest tests/test_processing_worker.py -k retry -v`
Expected: PASS — `retry` was implemented in Task 2. (If it fails, fix `retry` to match these assertions; this task pins the behavior.)

- [ ] **Step 3: (only if Step 2 failed) adjust `retry`**

No change expected. If a test fails, reconcile `retry` with the assertions above (reset only `ERROR` stages, clear error fields, set `auto=True`).

- [ ] **Step 4: Commit**

```bash
git add tests/test_processing_worker.py
git commit -m "test(processing): pin retry resume-from-failed-stage behavior"
```

---

### Task 8: integration — serial ordering + `auto=False` guard + thread smoke + docs

**Files:**
- Modify: `tests/test_processing_worker.py`, `CLAUDE.md`
- Test: `tests/test_processing_worker.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_next_auto_item_skips_auto_false(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q._items[0].auto = False  # a reconciled disk row, display-only
    assert q._next_auto_item() is None


def test_next_auto_item_skips_fully_settled(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    it = q._items[0]
    it.transcript = StageStatus.DONE
    it.protocol = StageStatus.DONE
    it.tasks = StageStatus.AWAITING_REVIEW   # settled (awaiting user), not PENDING
    assert q._next_auto_item() is None


def test_started_thread_drains_to_awaiting_review(tmp_path, monkeypatch):
    import time

    from tasks.schema import Task

    meetings = tmp_path / "meetings"
    meetings.mkdir()
    monkeypatch.setattr("utils.get_meetings_dir", lambda: str(meetings))
    monkeypatch.setattr("cli.core.run_transcribe", lambda *a, **k: _fake_transcribe_output())

    class _Proto:
        markdown = "# P"
    monkeypatch.setattr("cli.core.run_protocol", lambda *a, **k: _Proto())
    monkeypatch.setattr(
        "cli.core.run_extract_tasks",
        lambda *a, **k: {"tasks": [Task(title="T")], "corrections": 0, "model": "m"},
    )
    audio = tmp_path / "r.m4a"
    audio.write_bytes(b"\x00")
    q = _queue(
        tmp_path, meetings_dir=str(meetings),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"}, "openrouter_api_key": "or",
        },
    )
    q.start()
    q.enqueue(str(audio), {"provider": "AssemblyAI", "language": "ru"})
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if q.snapshot()[0].tasks == StageStatus.AWAITING_REVIEW:
            break
        time.sleep(0.02)
    q.stop()
    assert q.snapshot()[0].tasks == StageStatus.AWAITING_REVIEW
```

- [ ] **Step 2: Run test to verify it fails (or passes for the guard tests)**

Run: `py -3 -m pytest tests/test_processing_worker.py -k "auto_false or settled or drains" -v`
Expected: guard tests PASS (logic exists); the threaded `drains` test PASS. If `drains` is flaky, it indicates a wake/loop bug — fix `_run`/`enqueue` wake handling, do not add sleeps to the worker.

- [ ] **Step 3: No new implementation expected**

These pin existing behavior. If `test_next_auto_item_skips_fully_settled` fails, confirm `_next_auto_item` only matches `PENDING` stages (not `AWAITING_REVIEW`/`DONE`).

- [ ] **Step 4: Update CLAUDE.md "Where things live"**

In the `processing/` row, add `worker.py`:

```
| Meetings-by-project + processing queue | `processing/` (`model`, `store`, `layout`, `worker` — meetings organized by project on disk + the serial auto-pipeline worker over `cli.core`; UI wiring lands in PR-2b) |
```

- [ ] **Step 5: Run the full suite + ruff**

Run: `py -3 -m pytest tests -q` → expected exit 0.
Run: `py -3 -m ruff check .` → expected `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add tests/test_processing_worker.py CLAUDE.md
git commit -m "test(processing): serial/auto-false guards + thread smoke; docs"
```

---

## Self-Review

**1. Spec coverage (PR-2a slice of the spec):**
- worker = third frontend over `cli.core`, serial daemon, never touches widgets → Tasks 2–6. ✓
- transcribe creates folder via `create_history_entry` + `save_segments`, then `layout.assign_project` → Task 3. ✓
- protocol → `protocol.md`; task-draft → `tasks_raw.json` + `AWAITING_REVIEW`, no auto-send → Tasks 4, 5. ✓
- stage error halts the item, humanized, no auto-retry; `retry` is manual and resumes → Tasks 6, 7. ✓
- `auto=False` items never processed (cost-flood guard); serial ordering → Task 8. ✓
- `layout.assign_project` writes metadata then moves, collision-safe, preserves participants/speakers → Task 1. ✓
- config via injected loader (persisted, not UI vars); project resolution injected (decoupled) → Tasks 2, 3. ✓
- **Out of PR-2a (no task, by design):** all `ui/` wiring, the migration script, draft-review (PR-2b/PR-3). ✓

**2. Placeholder scan:** every code step carries complete code; no TODO/TBD/"similar to". ✓

**3. Type consistency:** `_stage_*` all return `bool`; `_set_stage(item, stage, status, *, error_stage, error_message)` signature stable across Tasks 3–5; `StageStatus`/`QueueItem` field names match `processing/model.py`; `cli.core.run_*` keyword args match `cli/core.py`; `assign_project(folder, project, meetings_dir)` consistent between Task 1 and its call in Task 3. ✓

**Decision log (deviations from the spec text, with rationale):**
- `assign_project` takes a resolved `Project | None` + `meetings_dir`, NOT a `project_id` — honoring the existing `layout.py` decoupling docstring (and matching `target_dir`'s signature) over the spec's `assign_project(meeting_folder, project_id)`. The worker does the id→Project resolution via the injected `resolve_project`.
- `tasks_raw.json` shape mirrors the CLI `extract-tasks --json` payload (`{tasks, corrections, model}`) so PR-3's draft-review can load it with `Task.from_dict`.
- Broad `except Exception` at each stage boundary (not narrow typed tuples): a worker daemon must survive ANY stage failure, and `humanize` already classifies the message for the UI. Tracked via the ratchet baseline bump (Task 6).
