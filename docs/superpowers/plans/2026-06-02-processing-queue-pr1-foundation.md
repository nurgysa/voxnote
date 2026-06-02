# Processing queue PR-1 — foundation (model + store + layout + migration)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure, fully-testable foundation of the processing queue — the `QueueItem` model, `queue.json` persistence + disk-derived view, project→folder layout helpers, and a one-time migration script — with **zero behavior change** (nothing imports these yet except the standalone script).

**Architecture:** A new `processing/` package (named to avoid shadowing stdlib `queue`). `model.py` is a str-enum + dataclass (mirrors `directory/schema.py`). `store.py` persists active items to `~/.audio-transcriber/queue.json` (atomic, mirrors `directory/store.py`) and derives the displayed meeting list fresh from disk (`build_view`, a two-level scan that skips `recordings/` and reads project from each meeting's `speakers.json`). `layout.py` maps a resolved `Project` to a folder and moves meeting folders collision-safely. The migration script relocates already-assigned meetings under their project folder.

**Tech Stack:** Python 3.10+, stdlib only (`dataclasses`, `enum`, `json`, `pathlib`, `os`, `re`, `shutil`), pytest. Reuses `utils.load_speakers` / `utils.get_meetings_dir` and `directory.store.DirectoryStore` / `directory.schema.Project`.

**Dependency note:** This PR is independent of the untracked `cli/` package (the worker in PR-2 depends on `cli.core`; PR-1 does not). Branch: `feat/processing-queue` (spec already committed there).

---

## File structure

| File | Responsibility |
|---|---|
| `processing/__init__.py` | package marker (empty) |
| `processing/model.py` | `StageStatus` enum + `QueueItem` dataclass (`to_dict`/`from_dict`) |
| `processing/store.py` | `queue.json` atomic I/O + `stage_status_from_folder` + `is_meeting_folder` + `build_view` |
| `processing/layout.py` | `project_dirname` + `target_dir` + `move_into` |
| `scripts/organize_by_project.py` | one-time dry-run migration (you-only) |
| `tests/test_processing_model.py` | model round-trip + tolerance |
| `tests/test_processing_store.py` | persistence + scan/overlay |
| `tests/test_processing_layout.py` | sanitization + target dir + move |
| `tests/test_organize_by_project.py` | migration planning + apply |

---

## Task 1: `processing/model.py` — StageStatus + QueueItem

**Files:**
- Create: `processing/__init__.py`
- Create: `processing/model.py`
- Test: `tests/test_processing_model.py`

- [ ] **Step 1: Create the empty package marker**

Create `processing/__init__.py` with a one-line docstring:

```python
"""Processing queue: model, persistence, project layout, and worker."""
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_processing_model.py`:

```python
from processing.model import QueueItem, StageStatus


def test_queue_item_round_trips():
    item = QueueItem(
        id="abc",
        audio_path="/a/x.wav",
        title="x",
        created_at="2026-06-02T10:00:00",
        meeting_folder="/m/x",
        options={"language": "ru", "project_id": "p1"},
        auto=True,
        project_id="p1",
        transcript=StageStatus.DONE,
        protocol=StageStatus.RUNNING,
        tasks=StageStatus.AWAITING_REVIEW,
        error_stage="protocol",
        error_message="boom",
    )
    restored = QueueItem.from_dict(item.to_dict())
    assert restored == item


def test_from_dict_tolerates_missing_and_bad_values():
    restored = QueueItem.from_dict({"id": "z", "transcript": "bogus"})
    assert restored.id == "z"
    assert restored.transcript is StageStatus.PENDING
    assert restored.auto is False
    assert restored.options == {}
    assert restored.project_id is None


def test_stage_status_serializes_to_plain_strings():
    d = QueueItem(id="i", audio_path="", title="", created_at="").to_dict()
    assert d["transcript"] == "pending"
    assert isinstance(d["transcript"], str)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_processing_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'processing.model'`

- [ ] **Step 4: Write the implementation**

Create `processing/model.py`:

```python
"""Queue item model for the processing pipeline.

Pure stdlib — no I/O, no Tk. Mirrors directory/schema.py: a str-enum plus a
mutable dataclass with explicit to_dict / tolerant from_dict so the on-disk
queue.json stays forward/backward compatible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    AWAITING_REVIEW = "awaiting_review"


@dataclass
class QueueItem:
    id: str
    audio_path: str
    title: str
    created_at: str
    meeting_folder: str | None = None
    options: dict = field(default_factory=dict)
    auto: bool = False
    project_id: str | None = None
    transcript: StageStatus = StageStatus.PENDING
    protocol: StageStatus = StageStatus.PENDING
    tasks: StageStatus = StageStatus.PENDING
    error_stage: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "audio_path": self.audio_path,
            "title": self.title,
            "created_at": self.created_at,
            "meeting_folder": self.meeting_folder,
            "options": dict(self.options),
            "auto": self.auto,
            "project_id": self.project_id,
            "transcript": self.transcript.value,
            "protocol": self.protocol.value,
            "tasks": self.tasks.value,
            "error_stage": self.error_stage,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QueueItem:
        def _stage(key: str) -> StageStatus:
            try:
                return StageStatus(d.get(key) or "pending")
            except ValueError:
                return StageStatus.PENDING

        return cls(
            id=d["id"],
            audio_path=d.get("audio_path", ""),
            title=d.get("title", ""),
            created_at=d.get("created_at", ""),
            meeting_folder=d.get("meeting_folder"),
            options=dict(d.get("options") or {}),
            auto=bool(d.get("auto", False)),
            project_id=d.get("project_id"),
            transcript=_stage("transcript"),
            protocol=_stage("protocol"),
            tasks=_stage("tasks"),
            error_stage=d.get("error_stage"),
            error_message=d.get("error_message"),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_processing_model.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Lint + commit**

```bash
python -m ruff check processing/ tests/test_processing_model.py
git add processing/__init__.py processing/model.py tests/test_processing_model.py
git commit -m "feat(processing): QueueItem model + StageStatus enum"
```

---

## Task 2: `processing/store.py` — queue.json atomic I/O

**Files:**
- Create: `processing/store.py`
- Test: `tests/test_processing_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_processing_store.py`:

```python
from processing.model import QueueItem, StageStatus
from processing.store import load_active, save_active


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "queue.json"
    items = [
        QueueItem(id="a", audio_path="/x.wav", title="x", created_at="t",
                  auto=True, transcript=StageStatus.DONE),
    ]
    save_active(items, path=p)
    loaded = load_active(path=p)
    assert loaded == items


def test_load_missing_file_returns_empty(tmp_path):
    assert load_active(path=tmp_path / "nope.json") == []


def test_load_malformed_returns_empty(tmp_path):
    p = tmp_path / "queue.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_active(path=p) == []


def test_save_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "queue.json"
    save_active([], path=p)
    assert p.is_file()
    assert not (tmp_path / ".queue.json.tmp").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_processing_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_active' from 'processing.store'` (module missing)

- [ ] **Step 3: Write the implementation**

Create `processing/store.py`:

```python
"""Persistence + disk-derived view for the processing queue.

queue.json (active items only) lives at ~/.audio-transcriber/queue.json, beside
config.json and directory.json. The displayed list is derived fresh from the
meetings dir each call (build_view) and overlaid with active items — so a
meeting folder's files stay the truth for stage status and speakers.json the
truth for project assignment. No Tk, no heavy deps; safe to import headlessly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from processing.model import QueueItem, StageStatus
from utils import load_speakers

FILENAME = "queue.json"
_SKIP_DIRS = {"recordings"}


def _default_queue_path() -> Path:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    return home / ".audio-transcriber" / FILENAME


def load_active(path: Path | str | None = None) -> list[QueueItem]:
    p = Path(path) if path is not None else _default_queue_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [QueueItem.from_dict(d) for d in data.get("items", [])]


def save_active(items: list[QueueItem], path: Path | str | None = None) -> None:
    p = Path(path) if path is not None else _default_queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": [it.to_dict() for it in items]}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = p.parent / f".{p.name}.tmp"
    tmp.write_text(encoded, encoding="utf-8")
    os.replace(tmp, p)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_processing_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check processing/store.py tests/test_processing_store.py
git add processing/store.py tests/test_processing_store.py
git commit -m "feat(processing): atomic queue.json load/save"
```

---

## Task 3: `processing/store.py` — stage status + meeting detection

**Files:**
- Modify: `processing/store.py`
- Test: `tests/test_processing_store.py`

- [ ] **Step 1: Add failing tests**

At the top of `tests/test_processing_store.py`, extend the `from processing.store import …` line to add `is_meeting_folder, stage_status_from_folder` (keep it alphabetical: `is_meeting_folder, load_active, save_active, stage_status_from_folder`). Then append these helpers and tests (no import lines mid-file — that would trip ruff E402):

```python
def _touch(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("x", encoding="utf-8")


def test_stage_status_all_pending_empty_folder(tmp_path):
    s = stage_status_from_folder(str(tmp_path))
    assert s == {
        "transcript": StageStatus.PENDING,
        "protocol": StageStatus.PENDING,
        "tasks": StageStatus.PENDING,
    }


def test_stage_status_full_meeting(tmp_path):
    for name in ("transcript.md", "protocol.md", "tasks.json"):
        _touch(tmp_path, name)
    s = stage_status_from_folder(str(tmp_path))
    assert s["transcript"] is StageStatus.DONE
    assert s["protocol"] is StageStatus.DONE
    assert s["tasks"] is StageStatus.DONE


def test_stage_status_draft_only_is_awaiting_review(tmp_path):
    _touch(tmp_path, "transcript.md")
    _touch(tmp_path, "tasks_raw.json")
    s = stage_status_from_folder(str(tmp_path))
    assert s["tasks"] is StageStatus.AWAITING_REVIEW


def test_is_meeting_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    meeting = tmp_path / "m"
    _touch(meeting, "transcript.md")
    assert is_meeting_folder(str(meeting)) is True
    assert is_meeting_folder(str(empty)) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_processing_store.py -v`
Expected: FAIL — collection `ImportError: cannot import name 'is_meeting_folder' from 'processing.store'`

- [ ] **Step 3: Implement — append to `processing/store.py`**

```python
def stage_status_from_folder(folder: str) -> dict:
    """Derive transcript/protocol/tasks StageStatus from which files exist."""

    def has(name: str) -> bool:
        return os.path.isfile(os.path.join(folder, name))

    if has("transcript.md") or has("transcript.txt"):
        transcript = StageStatus.DONE
    else:
        transcript = StageStatus.PENDING
    protocol = StageStatus.DONE if has("protocol.md") else StageStatus.PENDING
    if has("tasks.json"):
        tasks = StageStatus.DONE
    elif has("tasks_raw.json"):
        tasks = StageStatus.AWAITING_REVIEW
    else:
        tasks = StageStatus.PENDING
    return {"transcript": transcript, "protocol": protocol, "tasks": tasks}


def is_meeting_folder(folder: str) -> bool:
    """True if the folder holds meeting artifacts (so it is a meeting, not a
    project container). create_history_entry writes transcript.md +
    description.md together, so these markers are reliable for real meetings."""
    for marker in ("transcript.md", "transcript.txt", "description.md", "segments.json"):
        if os.path.isfile(os.path.join(folder, marker)):
            return True
    return False
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_processing_store.py -v`
Expected: PASS (8 tests total)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check processing/store.py tests/test_processing_store.py
git add processing/store.py tests/test_processing_store.py
git commit -m "feat(processing): derive stage status + meeting detection from disk"
```

---

## Task 4: `processing/store.py` — `build_view` two-level scan + overlay

**Files:**
- Modify: `processing/store.py`
- Test: `tests/test_processing_store.py`

- [ ] **Step 1: Add failing tests**

At the top of `tests/test_processing_store.py`, add `import json` to the stdlib import group and add `build_view` to the `from processing.store import …` line (final form, alphabetical):

```python
import json

from processing.model import QueueItem, StageStatus
from processing.store import (
    build_view,
    is_meeting_folder,
    load_active,
    save_active,
    stage_status_from_folder,
)
```

Then append these helpers and tests:

```python
def _meeting(folder, *, transcript=True, project_id=None):
    folder.mkdir(parents=True, exist_ok=True)
    if transcript:
        (folder / "transcript.md").write_text("hi", encoding="utf-8")
    if project_id is not None:
        (folder / "speakers.json").write_text(
            json.dumps({"project_id": project_id, "participants": [], "speakers": {}}),
            encoding="utf-8",
        )


def test_build_view_finds_root_and_project_meetings(tmp_path):
    _meeting(tmp_path / "2026-06-01_root_meeting")           # no project, root
    _meeting(tmp_path / "Kitng" / "2026-06-02_kitng",        # under a project dir
             project_id="p1")
    (tmp_path / "recordings").mkdir()                         # must be skipped
    (tmp_path / "recordings" / "rec.wav").write_text("x", encoding="utf-8")

    rows = build_view(str(tmp_path), active=[])
    titles = {r.title for r in rows}
    assert titles == {"2026-06-01_root_meeting", "2026-06-02_kitng"}
    by_title = {r.title: r for r in rows}
    assert by_title["2026-06-01_root_meeting"].project_id is None
    assert by_title["2026-06-02_kitng"].project_id == "p1"
    assert all(r.auto is False for r in rows)


def test_build_view_skips_recordings_dir(tmp_path):
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / "rec.wav").write_text("x", encoding="utf-8")
    assert build_view(str(tmp_path), active=[]) == []


def test_build_view_active_item_overrides_disk_row(tmp_path):
    folder = tmp_path / "2026-06-02_live"
    _meeting(folder)
    active = [QueueItem(id="live", audio_path="/a.wav", title="2026-06-02_live",
                        created_at="t", meeting_folder=str(folder), auto=True,
                        protocol=StageStatus.RUNNING)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].auto is True
    assert rows[0].protocol is StageStatus.RUNNING


def test_build_view_active_without_folder_is_appended(tmp_path):
    active = [QueueItem(id="new", audio_path="/a.wav", title="pending one",
                        created_at="t", auto=True)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].id == "new"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_processing_store.py -v`
Expected: FAIL — collection `ImportError: cannot import name 'build_view' from 'processing.store'`

- [ ] **Step 3: Implement — append to `processing/store.py`**

```python
def _row_from_folder(folder: str) -> QueueItem:
    stages = stage_status_from_folder(folder)
    speakers = load_speakers(folder)
    name = os.path.basename(os.path.normpath(folder))
    return QueueItem(
        id=folder,
        audio_path="",
        title=name,
        created_at="",
        meeting_folder=folder,
        auto=False,
        project_id=(speakers.get("project_id") or None),
        transcript=stages["transcript"],
        protocol=stages["protocol"],
        tasks=stages["tasks"],
    )


def build_view(meetings_dir: str, active: list[QueueItem]) -> list[QueueItem]:
    """Derive display rows from disk (two-level: root meetings + meetings inside
    project folders), then overlay active items (authoritative for their folder).
    `recordings/` and non-meeting/non-project entries are skipped. Project is read
    from each meeting's speakers.json, never inferred from the folder name."""
    rows: list[QueueItem] = []
    try:
        entries = sorted(os.listdir(meetings_dir))
    except OSError:
        entries = []
    for entry in entries:
        full = os.path.join(meetings_dir, entry)
        if not os.path.isdir(full) or entry in _SKIP_DIRS:
            continue
        if is_meeting_folder(full):
            rows.append(_row_from_folder(full))
            continue
        # treat as a project folder → one level of meetings inside
        try:
            subs = sorted(os.listdir(full))
        except OSError:
            subs = []
        for sub in subs:
            subfull = os.path.join(full, sub)
            if os.path.isdir(subfull) and is_meeting_folder(subfull):
                rows.append(_row_from_folder(subfull))

    index = {
        os.path.normcase(os.path.abspath(r.meeting_folder)): i
        for i, r in enumerate(rows)
        if r.meeting_folder
    }
    for item in active:
        key = (
            os.path.normcase(os.path.abspath(item.meeting_folder))
            if item.meeting_folder
            else None
        )
        if key is not None and key in index:
            rows[index[key]] = item
        else:
            rows.append(item)
    return rows
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_processing_store.py -v`
Expected: PASS (12 tests total)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check processing/store.py tests/test_processing_store.py
git add processing/store.py tests/test_processing_store.py
git commit -m "feat(processing): build_view two-level scan + active overlay"
```

---

## Task 5: `processing/layout.py` — project → folder mapping

**Files:**
- Create: `processing/layout.py`
- Test: `tests/test_processing_layout.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_processing_layout.py`:

```python
import os

from directory.schema import Project
from processing.layout import move_into, project_dirname, target_dir


def test_project_dirname_plain_name():
    assert project_dirname(Project(name="Kitng", id="p1")) == "Kitng"


def test_project_dirname_sanitizes_illegal_chars():
    assert project_dirname(Project(name='a/b:c*d', id="p1")) == "a_b_c_d"


def test_project_dirname_falls_back_to_id_when_empty():
    p = Project(name='///', id="abcdef1234567890")
    assert project_dirname(p) == "abcdef12"


def test_target_dir_none_is_root(tmp_path):
    assert target_dir(str(tmp_path), None) == str(tmp_path)


def test_target_dir_project_is_subfolder(tmp_path):
    p = Project(name="Kitng", id="p1")
    assert target_dir(str(tmp_path), p) == os.path.join(str(tmp_path), "Kitng")


def test_move_into_moves_folder(tmp_path):
    src = tmp_path / "meeting"
    src.mkdir()
    (src / "transcript.md").write_text("x", encoding="utf-8")
    dest = tmp_path / "Kitng"
    new = move_into(str(src), str(dest))
    assert new == os.path.join(str(dest), "meeting")
    assert os.path.isfile(os.path.join(new, "transcript.md"))
    assert not src.exists()


def test_move_into_noop_when_already_there(tmp_path):
    src = tmp_path / "meeting"
    src.mkdir()
    new = move_into(str(src), str(tmp_path))
    assert new == os.path.normpath(str(src))
    assert src.exists()


def test_move_into_collision_appends_suffix(tmp_path):
    src = tmp_path / "meeting"
    src.mkdir()
    dest = tmp_path / "Kitng"
    (dest / "meeting").mkdir(parents=True)  # occupied
    new = move_into(str(src), str(dest))
    assert new == os.path.join(str(dest), "meeting-2")
    assert os.path.isdir(new)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_processing_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'processing.layout'`

- [ ] **Step 3: Write the implementation**

Create `processing/layout.py`:

```python
"""Project → folder mapping for meeting storage.

speakers.json.project_id is the source of truth for a meeting's project; the
folder location under meetings_dir/<project>/ is its reflection. These helpers
map a resolved Project to a folder name and move a meeting folder into place.
The caller resolves project_id -> Project, so this module stays decoupled from
the directory store and from Tk.
"""
from __future__ import annotations

import os
import re
import shutil

from directory.schema import Project

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def project_dirname(project: Project) -> str:
    """Filesystem-safe folder name for a project. Falls back to a short id slice
    when the name sanitizes to empty (e.g. all-illegal characters)."""
    cleaned = _ILLEGAL.sub("_", project.name).strip().strip(".")
    return cleaned or project.id[:8]


def target_dir(meetings_dir: str, project: Project | None) -> str:
    """Directory a meeting with this project belongs in: a project subfolder, or
    the meetings_dir root when project is None."""
    if project is None:
        return meetings_dir
    return os.path.join(meetings_dir, project_dirname(project))


def move_into(folder: str, dest_dir: str) -> str:
    """Move `folder` into `dest_dir`; return the new path. No-op (returns the
    normalized original) when already there. Collision-safe — never overwrites;
    appends -2, -3, … instead."""
    folder = os.path.normpath(folder)
    dest_dir = os.path.normpath(dest_dir)
    if os.path.normcase(os.path.dirname(folder)) == os.path.normcase(dest_dir):
        return folder
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(folder)
    target = os.path.join(dest_dir, base)
    n = 2
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{base}-{n}")
        n += 1
    shutil.move(folder, target)
    return target
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_processing_layout.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check processing/layout.py tests/test_processing_layout.py
git add processing/layout.py tests/test_processing_layout.py
git commit -m "feat(processing): project_dirname + target_dir + move_into layout helpers"
```

---

## Task 6: `scripts/organize_by_project.py` — one-time migration

**Files:**
- Create: `scripts/organize_by_project.py`
- Test: `tests/test_organize_by_project.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_organize_by_project.py`. Load the script by path (scripts/ is not a package — mirror `test_move_recordings_script.py`) and drive the `_plan` seam (not `main`):

```python
import importlib.util
import json
import os
import pathlib

from directory.schema import Project
from directory.store import DirectoryStore

_PATH = pathlib.Path("scripts/organize_by_project.py")
_spec = importlib.util.spec_from_file_location("organize_by_project", _PATH)
organize_by_project = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(organize_by_project)


def _meeting(folder, *, project_id=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "transcript.md").write_text("hi", encoding="utf-8")
    if project_id is not None:
        (folder / "speakers.json").write_text(
            json.dumps({"project_id": project_id, "participants": [], "speakers": {}}),
            encoding="utf-8",
        )


def _store(tmp_path):
    store = DirectoryStore(path=tmp_path / "directory.json")
    store.load()
    store.upsert_project(Project(name="Kitng", id="p1"))
    return store


def test_plan_selects_only_resolvable_project_meetings(tmp_path):
    meetings = tmp_path / "meetings"
    _meeting(meetings / "2026-06-02_kitng", project_id="p1")
    _meeting(meetings / "2026-06-01_noproject")            # stays in root
    _meeting(meetings / "2026-05-30_ghost", project_id="gone")  # unknown project
    (meetings / "recordings").mkdir(parents=True)

    plan = organize_by_project._plan(str(meetings), _store(tmp_path))
    assert len(plan) == 1
    folder, dest, name = plan[0]
    assert os.path.basename(folder) == "2026-06-02_kitng"
    assert dest == os.path.join(str(meetings), "Kitng")
    assert name == "Kitng"


def test_plan_apply_moves_folder(tmp_path):
    meetings = tmp_path / "meetings"
    _meeting(meetings / "2026-06-02_kitng", project_id="p1")
    plan = organize_by_project._plan(str(meetings), _store(tmp_path))
    folder, dest, _name = plan[0]
    new = organize_by_project.move_into(folder, dest)
    assert os.path.isfile(os.path.join(new, "transcript.md"))
    assert new == os.path.join(str(meetings), "Kitng", "2026-06-02_kitng")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_organize_by_project.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.organize_by_project'`

- [ ] **Step 3: Write the implementation**

Create `scripts/organize_by_project.py`:

```python
#!/usr/bin/env python3
"""One-time: relocate meetings under their project folder.

Reads each root-level meeting's speakers.json; if it carries a project_id that
resolves to a directory Project, moves the folder into meetings_dir/<project>/.
Meetings without a (resolvable) project_id stay in the root. Dry-run by default;
--apply to move. Non-destructive: never overwrites (collision-safe). You-only;
not bundled with the app.

Usage (from repo root):
    python scripts/organize_by_project.py            # dry run
    python scripts/organize_by_project.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from directory.store import DirectoryStore  # noqa: E402
from processing.layout import move_into, target_dir  # noqa: E402
from processing.store import is_meeting_folder  # noqa: E402
from utils import get_meetings_dir, load_speakers  # noqa: E402

_SKIP = {"recordings"}


def _plan(meetings_dir: str, store) -> list[tuple[str, str, str]]:
    """Return (folder, dest_dir, project_name) for root meetings with a
    resolvable project. Only root-level meeting folders are considered."""
    out: list[tuple[str, str, str]] = []
    try:
        entries = sorted(os.listdir(meetings_dir))
    except OSError:
        return out
    for entry in entries:
        full = os.path.join(meetings_dir, entry)
        if not os.path.isdir(full) or entry in _SKIP:
            continue
        if not is_meeting_folder(full):
            continue  # likely already a project folder
        pid = (load_speakers(full).get("project_id") or "").strip()
        if not pid:
            continue
        project = store.get_project(pid)
        if project is None:
            continue
        out.append((full, target_dir(meetings_dir, project), project.name))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Relocate meetings under their project folder.",
    )
    ap.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    args = ap.parse_args()

    meetings_dir = get_meetings_dir()
    store = DirectoryStore()
    store.load()
    plan = _plan(meetings_dir, store)

    print(f"meetings dir: {meetings_dir}")
    print(f"found: {len(plan)} meeting(s) with a resolvable project")
    if not args.apply:
        for folder, _dest, name in plan:
            print(f"  would move: {os.path.basename(folder)} -> {name}/")
        print("DRY RUN — nothing moved. Re-run with --apply.")
        return 0

    moved = 0
    for folder, dest, name in plan:
        new = move_into(folder, dest)
        moved += 1
        print(f"  moved: {os.path.basename(folder)} -> {name}/  ({new})")
    print(f"done: moved={moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_organize_by_project.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Full suite + lint + commit**

```bash
pytest -q
python -m ruff check .
git add scripts/organize_by_project.py tests/test_organize_by_project.py
git commit -m "feat(processing): one-time organize-by-project migration script"
```

Expected: full suite green (baseline 333 + the new PR-1 tests, ~25 added), ruff clean.

---

## Done criteria

- `processing/` package with `model.py`, `store.py`, `layout.py` — all pure, headless-importable (no Tk / sounddevice).
- `scripts/organize_by_project.py` dry-run works against the real vault.
- Full `pytest` green; `ruff check .` clean.
- **No behavior change** — nothing in `ui/` or the running app imports `processing/` yet (verified: `grep -rn "import processing" ui/ app.py` returns nothing).
- Open PR `feat/processing-queue` → `main` titled "feat(processing): queue foundation (model + store + layout + migration)". PR-2 (worker + entry + UI) is planned separately after this lands.
