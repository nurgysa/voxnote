# Transcription Queue PR-B2 — Trio Rework + Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the processing-queue trio (`processing/{model,store,worker}.py`) into a **single-stage, transcribe-only** queue that writes a diarized `transcript.md` into the vault, archives audio to Google Drive `sources/`, and fires a best-effort Hermes nudge — plus a new `processing/preflight.py` (duration/size probe, provider-cap guard, long-audio denoise auto-off, cost estimate).

**Architecture:** VoxNote's desktop queue is **transcribe-only**; Hermes owns protocol/tasks/approve/send downstream. The worker composes the PR-A/B1 primitives already on `main` — `vault_note.write_transcript_note`, `sources.archive_audio`, `utils.save_segments_sidecar`, `cli.core.run_transcribe` (speaker-count), and the Hermes v1.1 event — into one `_process_item` that ends in a single `status` (PENDING/RUNNING/DONE/ERROR). A meeting's status is derived from disk (transcript.md present ⇒ DONE); Hermes's downstream progress (protocol.md / tasks.md) shows as display **badges**, never as queue status.

**Tech Stack:** Python 3.10+ stdlib, `pytest`, `ruff`. No new third-party deps. No Tk (headless worker). UTF-8 everywhere (Windows cp1252 trap).

**Source of truth:** `docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md`. Resume context: `docs/superpowers/handoffs/2026-06-15-transcription-queue-pr-b2-handoff.md`.

**Conventions:** `py -3 -m pytest -q` (fallback `python -m pytest -q`) + `py -3 -m ruff check .` green before every commit. Commits lowercase-scoped, ending with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Russian user-facing strings, English code/comments.

---

## Blast radius (verified)

Only these files change. Nothing in `ui/` or `cli/` imports `ProcessingQueue` or the removed symbols (PR-C UI wiring is not done yet). `processing/layout.py` and `store.is_meeting_folder` are **kept** — `processing/vault_note.py` and `scripts/organize_by_project.py` depend on them.

- **New:** `processing/preflight.py`, `tests/test_preflight.py`
- **Rewrite:** `processing/model.py`, `processing/store.py`, `processing/worker.py`
- **Rewrite tests:** `tests/test_processing_model.py`, `tests/test_processing_store.py`, `tests/test_processing_worker.py`
- **Edit:** `tests/test_broad_except_ratchet.py` (baseline `processing/worker.py` 3 → 1), `config.example.json` (add `sources_dir` + `inbox_dir`), `AGENTS.md` (pipeline framing + webhook v1.1)

---

## File structure

| File | Responsibility after PR-B2 |
|---|---|
| `processing/preflight.py` (new) | Pure pre-upload checks: `probe` (duration+size), `provider_limit_ok` (size cap), `should_denoise` (off > 45 min), `estimate_cost`. No queue/Tk deps. |
| `processing/model.py` | `QueueItem` with a single `status: StageStatus` + `source`/`source_path`/`nudge_delivered` + disk-derived display badges `has_protocol`/`has_tasks`. Enum loses `AWAITING_REVIEW`. |
| `processing/store.py` | `load_active`/`save_active` (unchanged) + `build_view` (status from transcript.md presence; badges from protocol.md/tasks.md; project from speakers.json). `meeting_status_from_folder` + `hermes_badges_from_folder` replace `stage_status_from_folder`. `is_meeting_folder` kept. |
| `processing/worker.py` | Single-stage `_process_item`: transcribe → archive audio → write transcript.md → speakers.json + segments sidecar → best-effort nudge → DONE. One broad-except boundary. |

---

## Task 1: `processing/preflight.py` — pre-upload checks (new, additive, green on its own)

**Files:**
- Create: `processing/preflight.py`
- Test: `tests/test_preflight.py`

Pure functions, no I/O except `probe` (which reads file size + optionally shells `ffmpeg -i`). `probe` duration strategy: try `audio_io.get_duration_s` (soundfile — WAV/FLAC/OGG only); on failure parse `ffmpeg -i <path>` stderr `Duration: HH:MM:SS.ss` (ffmpeg via `utils.get_ffmpeg_path()`, which returns `str | None`; ffprobe is NOT bundled). Return `None` duration when both fail — callers size-gate only. Catch classes stay narrow (`OSError`, `RuntimeError`) so this file adds **zero** broad-except handlers.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preflight.py`:

```python
from processing import preflight


# ── _parse_ffmpeg_duration (pure) ──

def test_parse_ffmpeg_duration_extracts_seconds():
    stderr = (
        "Input #0, mov,mp4, from 'x.m4a':\n"
        "  Duration: 01:02:03.50, start: 0.000000, bitrate: 128 kb/s\n"
    )
    assert preflight._parse_ffmpeg_duration(stderr) == 3723.5


def test_parse_ffmpeg_duration_none_when_absent():
    assert preflight._parse_ffmpeg_duration("no duration here") is None


# ── probe ──

def test_probe_reads_size_from_real_file(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: None)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: None)
    f = tmp_path / "a.bin"
    f.write_bytes(b"0123456789")
    info = preflight.probe(str(f))
    assert info["size_bytes"] == 10
    assert info["duration_s"] is None


def test_probe_prefers_soundfile_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: 12.5)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: 99.0)
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert preflight.probe(str(f))["duration_s"] == 12.5


def test_probe_falls_back_to_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: None)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: 42.0)
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x")
    assert preflight.probe(str(f))["duration_s"] == 42.0


def test_probe_missing_file_size_zero(monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: None)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: None)
    info = preflight.probe("/no/such/file.m4a")
    assert info["size_bytes"] == 0
    assert info["duration_s"] is None


# ── provider_limit_ok ──

def test_provider_limit_ok_under_cap():
    ok, reason = preflight.provider_limit_ok("AssemblyAI", 3600.0, 50 * 1024**2)
    assert ok is True
    assert reason == ""


def test_provider_limit_ok_over_cap():
    ok, reason = preflight.provider_limit_ok("Gladia", None, 5 * 1024**3)
    assert ok is False
    assert "ГБ" in reason


def test_provider_limit_ok_unknown_size_passes():
    ok, reason = preflight.provider_limit_ok("AssemblyAI", None, 0)
    assert ok is True


# ── should_denoise ──

def test_should_denoise_true_for_short_requested():
    assert preflight.should_denoise(600.0, True) is True


def test_should_denoise_false_for_long_requested():
    assert preflight.should_denoise(46 * 60.0, True) is False


def test_should_denoise_false_when_not_requested():
    assert preflight.should_denoise(60.0, False) is False


def test_should_denoise_true_when_duration_unknown():
    assert preflight.should_denoise(None, True) is True


# ── estimate_cost ──

def test_estimate_cost_known_provider_one_hour():
    assert preflight.estimate_cost("AssemblyAI", 3600.0) == 0.17


def test_estimate_cost_none_when_duration_unknown():
    assert preflight.estimate_cost("AssemblyAI", None) is None


def test_estimate_cost_none_for_unknown_provider():
    assert preflight.estimate_cost("Nope", 3600.0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_preflight.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'processing.preflight'`.

- [ ] **Step 3: Write `processing/preflight.py`**

```python
"""Pre-upload checks for the transcription queue.

Pure, cheap guards run before spending a (possibly multi-hour, paid) cloud
upload: probe the file's duration + size, reject over-cap files with a Russian
message, auto-disable denoise on long files (the denoise path forces a
multi-hundred-MB temp WAV — spec §Long-audio), and estimate STT cost.

Duration probing: soundfile reads WAV/FLAC/OGG headers cheaply; phone audio is
usually .m4a/.mp3, which soundfile can't read, so we fall back to parsing
``ffmpeg -i`` stderr. ffprobe is NOT bundled (utils.get_ffmpeg_path may even
return None) — both paths degrade to ``None`` duration, and callers size-gate
only. Catch classes stay narrow so this module adds no broad-except handlers.
"""
from __future__ import annotations

import os
import re
import subprocess

# ~2 GB upload body cap — documented for AssemblyAI / Speechmatics / Deepgram;
# applied uniformly (Gladia's real cap is tighter but unpublished, so a 2 GB
# gate is a safe conservative ceiling whose job is to catch the obvious
# "this 5 GB file will 413" case before an upload is attempted).
_SIZE_CAP_BYTES = 2 * 1024**3

# Denoise forces ensure_wav → a huge temp WAV + hours of ffmpeg on long audio.
_DENOISE_MAX_S = 45 * 60

# Rough $/hour WITH speaker diarization, from each provider module's header
# comment (providers/{assemblyai,deepgram,gladia,speechmatics}.py). Estimate
# only — for an at-enqueue cost hint, not billing.
_COST_PER_HOUR = {
    "AssemblyAI": 0.17,
    "Deepgram": 0.43,
    "Gladia": 0.61,
    "Speechmatics": 1.04,
}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _parse_ffmpeg_duration(stderr: str) -> float | None:
    """Extract seconds from an ``ffmpeg -i`` stderr ``Duration: HH:MM:SS.ss``
    line. None when no duration line is present."""
    m = _DURATION_RE.search(stderr)
    if not m:
        return None
    hours, minutes, seconds = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _duration_via_soundfile(audio_path: str) -> float | None:
    """Duration via soundfile header (WAV/FLAC/OGG). None on any read failure
    (e.g. .m4a/.mp3, which soundfile can't decode)."""
    try:
        from audio_io import get_duration_s

        return get_duration_s(audio_path)
    except (RuntimeError, OSError):
        return None


def _duration_via_ffmpeg(audio_path: str) -> float | None:
    """Duration by parsing ``ffmpeg -i`` stderr. None when ffmpeg is absent or
    the output has no Duration line."""
    from utils import get_ffmpeg_path

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", audio_path],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    return _parse_ffmpeg_duration(stderr)


def probe(audio_path: str) -> dict:
    """Return ``{"duration_s": float | None, "size_bytes": int}``.

    Size from the filesystem (0 if unreadable). Duration tries soundfile first,
    then the ffmpeg-stderr fallback; ``None`` when both fail.
    """
    try:
        size_bytes = os.path.getsize(audio_path)
    except OSError:
        size_bytes = 0
    duration_s = _duration_via_soundfile(audio_path)
    if duration_s is None:
        duration_s = _duration_via_ffmpeg(audio_path)
    return {"duration_s": duration_s, "size_bytes": size_bytes}


def provider_limit_ok(
    provider: str, duration_s: float | None, size_bytes: int
) -> tuple[bool, str]:
    """``(ok, reason)``. False with a Russian message when the file exceeds the
    provider's upload cap. ``duration_s`` is reserved for future per-provider
    duration caps; the live gate is size."""
    if size_bytes and size_bytes > _SIZE_CAP_BYTES:
        gb = size_bytes / 1024**3
        return (
            False,
            f"Файл {gb:.1f} ГБ превышает лимит провайдера {provider} (~2 ГБ). "
            f"Сократи запись или сожми аудио и попробуй снова.",
        )
    return True, ""


def should_denoise(duration_s: float | None, requested: bool) -> bool:
    """Honor the user's denoise request, but force it off above the long-audio
    threshold (the denoise path is too heavy there). Unknown duration → honor
    the request."""
    if not requested:
        return False
    if duration_s is not None and duration_s > _DENOISE_MAX_S:
        return False
    return True


def estimate_cost(provider: str, duration_s: float | None) -> float | None:
    """Rough STT cost in USD for ``duration_s`` at ``provider``'s with-diarization
    rate. None when the duration is unknown or the provider isn't in the table."""
    if duration_s is None:
        return None
    rate = _COST_PER_HOUR.get(provider)
    if rate is None:
        return None
    return rate * (duration_s / 3600.0)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_preflight.py -q`
Expected: PASS (all preflight tests green).

- [ ] **Step 5: Lint**

Run: `py -3 -m ruff check processing/preflight.py tests/test_preflight.py`
Expected: clean (no output).

- [ ] **Step 6: Commit**

```bash
git add processing/preflight.py tests/test_preflight.py
git commit -F- <<'EOF'
feat(processing): preflight checks for the transcription queue (PR-B2)

probe (duration via soundfile→ffmpeg-stderr fallback, size), provider
size-cap guard with a Russian message, long-audio denoise auto-off
(>45 min), and a rough per-provider STT cost estimate. Pure/narrow-except;
consumed by the single-stage queue worker in the next commit.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 2: Trio rework — single-stage queue (atomic, green at the end)

**Files:**
- Modify (full rewrite): `processing/model.py`, `processing/store.py`, `processing/worker.py`
- Modify (full rewrite): `tests/test_processing_model.py`, `tests/test_processing_store.py`, `tests/test_processing_worker.py`
- Modify: `tests/test_broad_except_ratchet.py` (one line), `config.example.json` (two lines)

> **Why one task:** rewriting `model.py` (drops `AWAITING_REVIEW` + `transcript`/`protocol`/`tasks`/`error_stage`) breaks `store.py` and `worker.py` at import/attribute level. The three files + their tests must land together. **Do not run the full suite mid-task** — run the targeted module tests as you edit each file; the green gate (full suite + ruff) is Step 9, then a single commit (Step 10).

- [ ] **Step 1: Rewrite `processing/model.py`**

Replace the entire file with:

```python
"""Queue item model for the processing pipeline.

Pure stdlib — no I/O, no Tk. Mirrors directory/schema.py: a str-enum plus a
mutable dataclass with explicit to_dict / tolerant from_dict so the on-disk
queue.json stays forward/backward compatible.

PR-B2: VoxNote's queue is transcribe-only. One item = one transcription job
carried to a single ``status`` (Hermes owns protocol/tasks downstream).
``source`` records how the audio arrived (record/pick/inbox) and drives the
archive move-vs-copy decision; ``source_path`` is where the audio was archived
in Drive ``sources/``. ``has_protocol``/``has_tasks`` are disk-derived display
badges (store.build_view fills them) showing Hermes's downstream progress —
never queue status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


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
    source: str = "pick"             # record | pick | inbox
    source_path: str | None = None   # archived audio in Drive sources/
    status: StageStatus = StageStatus.PENDING
    nudge_delivered: bool = False
    error_message: str | None = None
    has_protocol: bool = False       # display badge: Hermes wrote protocol.md
    has_tasks: bool = False          # display badge: Hermes wrote tasks.md

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
            "source": self.source,
            "source_path": self.source_path,
            "status": self.status.value,
            "nudge_delivered": self.nudge_delivered,
            "error_message": self.error_message,
            "has_protocol": self.has_protocol,
            "has_tasks": self.has_tasks,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QueueItem:
        try:
            status = StageStatus(d.get("status") or "pending")
        except ValueError:
            status = StageStatus.PENDING
        return cls(
            id=d["id"],
            audio_path=d.get("audio_path", ""),
            title=d.get("title", ""),
            created_at=d.get("created_at", ""),
            meeting_folder=d.get("meeting_folder"),
            options=dict(d.get("options") or {}),
            auto=bool(d.get("auto", False)),
            project_id=d.get("project_id"),
            source=d.get("source") or "pick",
            source_path=d.get("source_path"),
            status=status,
            nudge_delivered=bool(d.get("nudge_delivered", False)),
            error_message=d.get("error_message"),
            has_protocol=bool(d.get("has_protocol", False)),
            has_tasks=bool(d.get("has_tasks", False)),
        )
```

- [ ] **Step 2: Rewrite `tests/test_processing_model.py`**

Replace the entire file with:

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
        source="record",
        source_path="G:/sources/x.wav",
        status=StageStatus.DONE,
        nudge_delivered=True,
        error_message=None,
        has_protocol=True,
        has_tasks=False,
    )
    restored = QueueItem.from_dict(item.to_dict())
    assert restored == item


def test_from_dict_tolerates_missing_and_bad_values():
    restored = QueueItem.from_dict({"id": "z", "status": "bogus"})
    assert restored.id == "z"
    assert restored.status is StageStatus.PENDING
    assert restored.auto is False
    assert restored.options == {}
    assert restored.project_id is None
    assert restored.source == "pick"
    assert restored.source_path is None
    assert restored.nudge_delivered is False
    assert restored.has_protocol is False
    assert restored.has_tasks is False


def test_status_serializes_to_plain_string():
    d = QueueItem(id="i", audio_path="", title="", created_at="").to_dict()
    assert d["status"] == "pending"
    assert isinstance(d["status"], str)
```

- [ ] **Step 3: Run the model tests**

Run: `py -3 -m pytest tests/test_processing_model.py -q`
Expected: PASS. (`store.py` / `worker.py` are now broken — that's expected until Steps 4–7.)

- [ ] **Step 4: Rewrite `processing/store.py`**

Replace the entire file with:

```python
"""Persistence + disk-derived view for the processing queue.

queue.json (active items only) lives at ~/.voxnote/queue.json, beside
config.json and directory.json. Atomic write (tmp + os.replace), mirroring
directory/store.py. build_view derives the displayed meeting list fresh from the
meetings dir (a two-level scan; project read from each meeting's speakers.json)
and overlays the active items. No Tk, no heavy deps; safe to import headlessly.

PR-B2: a meeting's status is binary on disk — transcript.md present ⇒ DONE,
else PENDING (VoxNote is transcribe-only). Hermes's downstream progress shows as
display badges (protocol.md / tasks.md presence), never as queue status.
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
    return home / ".voxnote" / FILENAME


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


def _has(folder: str, name: str) -> bool:
    return os.path.isfile(os.path.join(folder, name))


def meeting_status_from_folder(folder: str) -> StageStatus:
    """DONE when a transcript exists in the folder (VoxNote's only job), else
    PENDING. Hermes's protocol/tasks are surfaced as badges, not status."""
    if _has(folder, "transcript.md") or _has(folder, "transcript.txt"):
        return StageStatus.DONE
    return StageStatus.PENDING


def hermes_badges_from_folder(folder: str) -> dict:
    """Hermes downstream-progress display flags: has Hermes written protocol.md /
    tasks.md into this meeting folder yet? Pure file-presence, never status."""
    return {
        "has_protocol": _has(folder, "protocol.md"),
        "has_tasks": _has(folder, "tasks.md"),
    }


def is_meeting_folder(folder: str) -> bool:
    """True if the folder holds meeting artifacts (so it is a meeting, not a
    project container). VoxNote writes transcript.md; legacy meetings may also
    carry description.md / segments.json, kept as markers for back-compat."""
    for marker in ("transcript.md", "transcript.txt", "description.md", "segments.json"):
        if os.path.isfile(os.path.join(folder, marker)):
            return True
    return False


def _row_from_folder(folder: str) -> QueueItem:
    speakers = load_speakers(folder)
    badges = hermes_badges_from_folder(folder)
    name = os.path.basename(os.path.normpath(folder))
    return QueueItem(
        id=folder,
        audio_path="",
        title=name,
        created_at="",
        meeting_folder=folder,
        auto=False,
        project_id=(speakers.get("project_id") or None),
        status=meeting_status_from_folder(folder),
        has_protocol=badges["has_protocol"],
        has_tasks=badges["has_tasks"],
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
        try:
            subs = sorted(os.listdir(full))
        except OSError:
            subs = []
        for sub in subs:
            subfull = os.path.join(full, sub)
            if os.path.isdir(subfull) and sub not in _SKIP_DIRS and is_meeting_folder(subfull):
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

- [ ] **Step 5: Rewrite `tests/test_processing_store.py`**

Replace the entire file with:

```python
import json

from processing.model import QueueItem, StageStatus
from processing.store import (
    build_view,
    hermes_badges_from_folder,
    is_meeting_folder,
    load_active,
    meeting_status_from_folder,
    save_active,
)


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "queue.json"
    items = [
        QueueItem(id="a", audio_path="/x.wav", title="x", created_at="t",
                  auto=True, source="record", status=StageStatus.DONE),
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


def _touch(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("x", encoding="utf-8")


def test_meeting_status_pending_empty_folder(tmp_path):
    assert meeting_status_from_folder(str(tmp_path)) is StageStatus.PENDING


def test_meeting_status_done_with_transcript(tmp_path):
    _touch(tmp_path, "transcript.md")
    assert meeting_status_from_folder(str(tmp_path)) is StageStatus.DONE


def test_hermes_badges_reflect_files(tmp_path):
    _touch(tmp_path, "transcript.md")
    assert hermes_badges_from_folder(str(tmp_path)) == {
        "has_protocol": False, "has_tasks": False,
    }
    _touch(tmp_path, "protocol.md")
    _touch(tmp_path, "tasks.md")
    assert hermes_badges_from_folder(str(tmp_path)) == {
        "has_protocol": True, "has_tasks": True,
    }


def test_is_meeting_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    meeting = tmp_path / "m"
    _touch(meeting, "transcript.md")
    assert is_meeting_folder(str(meeting)) is True
    assert is_meeting_folder(str(empty)) is False


def _meeting(folder, *, transcript=True, project_id=None, protocol=False, tasks=False):
    folder.mkdir(parents=True, exist_ok=True)
    if transcript:
        (folder / "transcript.md").write_text("hi", encoding="utf-8")
    if protocol:
        (folder / "protocol.md").write_text("p", encoding="utf-8")
    if tasks:
        (folder / "tasks.md").write_text("t", encoding="utf-8")
    if project_id is not None:
        (folder / "speakers.json").write_text(
            json.dumps({"project_id": project_id, "participants": [], "speakers": {}}),
            encoding="utf-8",
        )


def test_build_view_finds_root_and_project_meetings(tmp_path):
    _meeting(tmp_path / "2026-06-01_root_meeting")
    _meeting(tmp_path / "Kitng" / "2026-06-02_kitng", project_id="p1", protocol=True)
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / "rec.wav").write_text("x", encoding="utf-8")

    rows = build_view(str(tmp_path), active=[])
    titles = {r.title for r in rows}
    assert titles == {"2026-06-01_root_meeting", "2026-06-02_kitng"}
    by_title = {r.title: r for r in rows}
    assert by_title["2026-06-01_root_meeting"].project_id is None
    assert by_title["2026-06-01_root_meeting"].status is StageStatus.DONE
    assert by_title["2026-06-02_kitng"].project_id == "p1"
    assert by_title["2026-06-02_kitng"].has_protocol is True
    assert by_title["2026-06-02_kitng"].has_tasks is False
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
                        status=StageStatus.RUNNING)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].auto is True
    assert rows[0].status is StageStatus.RUNNING


def test_build_view_active_without_folder_is_appended(tmp_path):
    active = [QueueItem(id="new", audio_path="/a.wav", title="pending one",
                        created_at="t", auto=True)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].id == "new"
```

- [ ] **Step 6: Run the model + store tests**

Run: `py -3 -m pytest tests/test_processing_model.py tests/test_processing_store.py -q`
Expected: PASS. (`worker.py` still broken until Step 7.)

- [ ] **Step 7: Rewrite `processing/worker.py`**

Replace the entire file with:

```python
"""Serial processing-queue worker — the third frontend over cli.core.

A single daemon thread carries each auto=True item through ONE stage:
transcribe → archive audio to Drive sources → write transcript.md into the
Obsidian vault → persist a segments sidecar → fire a best-effort Hermes nudge.
VoxNote is transcribe-only; Hermes owns protocol/tasks/approve/send downstream
(spec: docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md).

NO Tk: the thread mutates state under a lock and persists; the UI reads via
snapshot() and the injected on_change callback. Config and project resolution
are injected (config_loader / resolve_project) so this module stays headless
and decoupled from the directory store.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from datetime import datetime

from cli import core
from processing import preflight, sources, store, vault_note
from processing.model import QueueItem, StageStatus

logger = logging.getLogger(__name__)

_IDLE_WAIT_S = 1.0
_SLUG_ILLEGAL = re.compile(r"[^\w]+", re.UNICODE)


def _slug(text: str) -> str:
    """Filesystem-safe meeting slug from a title: Unicode letters/digits kept,
    runs of anything else → '-'. Falls back to 'meeting' when empty."""
    base = os.path.splitext(text)[0].strip().lower()
    base = _SLUG_ILLEGAL.sub("-", base).strip("-_")
    return base or "meeting"


def _parse_created(created_at: str) -> tuple[str, str, str]:
    """(date 'YYYY-MM-DD', time 'HH:MM', hhmm 'HHMM') from an ISO timestamp.
    Tolerant: returns ('', '', '') when unparseable."""
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return "", "", ""
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), dt.strftime("%H%M")


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
            source=options.get("source") or "pick",
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
                if it.id == item_id and it.status == StageStatus.ERROR:
                    it.status = StageStatus.PENDING
                    it.error_message = None
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

    def _set_status(
        self, item: QueueItem, status: StageStatus, *, error_message: str | None = None
    ) -> None:
        with self._lock:
            item.status = status
            item.error_message = error_message
            self._persist_locked()
        self._notify()

    def _process_item(self, item: QueueItem) -> None:
        """Transcribe → archive audio (sources) → write transcript.md (vault) →
        speakers.json + segments sidecar → best-effort Hermes nudge → DONE. Any
        failure halts THIS item (ERROR) but never kills the daemon."""
        self._set_status(item, StageStatus.RUNNING)
        try:
            import utils
            from integrations.hermes.client import (
                emit_audio_transcribed_event,
                get_hermes_webhook_config,
            )

            cfg = self._config_loader()
            opts = item.options
            provider = opts.get("provider") or cfg.get("cloud_provider") or "AssemblyAI"
            api_key = (cfg.get("cloud_api_keys") or {}).get(provider)
            if not api_key:
                raise ValueError(f"Нет API-ключа для провайдера {provider!r}.")
            language = opts.get("language") or None
            if language == "auto":
                language = None

            info = preflight.probe(item.audio_path)
            duration_s = info.get("duration_s")
            size_bytes = info.get("size_bytes", 0)
            ok, reason = preflight.provider_limit_ok(provider, duration_s, size_bytes)
            if not ok:
                raise ValueError(reason)
            denoise = preflight.should_denoise(duration_s, bool(opts.get("denoise")))

            out = core.run_transcribe(
                item.audio_path,
                provider=provider,
                api_key=api_key,
                language=language,
                diarize=bool(opts.get("diarize")),
                hotwords=opts.get("hotwords") or None,
                denoise=denoise,
                num_speakers=opts.get("num_speakers"),
                min_speakers=opts.get("min_speakers"),
                max_speakers=opts.get("max_speakers"),
            )

            date, time_str, hhmm = _parse_created(item.created_at)
            base = "_".join(p for p in (date, hhmm, _slug(item.title)) if p) or item.id
            project = self._resolve_project(opts.get("project_id"))

            # Archive only AFTER a successful transcribe, so a failure never
            # strands or loses audio (spec §Failure-handling, ordering). The
            # note then records the FINAL Drive path in a single write — no
            # second pass over transcript.md.
            sources_dir = (cfg.get("sources_dir") or "").strip()
            source_path: str | None = None
            if sources_dir:
                try:
                    source_path = sources.archive_audio(
                        item.audio_path, sources_dir, base,
                        move=item.source in ("record", "inbox"),
                    )
                except OSError as e:
                    # Archiving is non-fatal: the note records the original path
                    # instead (spec §Failure-handling). Audio stays put.
                    logger.warning("audio archive failed for %s: %s", item.id, e)

            hermes_cfg = get_hermes_webhook_config(cfg)
            content = vault_note.render_transcript_note(
                segments=out.segments,
                title=item.title,
                project_name=getattr(project, "name", None),
                date=date,
                time=time_str,
                participants=[],
                provider=provider,
                language=out.language,
                voxnote_id=item.id,
                source_path=source_path or item.audio_path,
                nudged=hermes_cfg.enabled,
            )
            note_path = vault_note.write_transcript_note(
                self._meetings_dir, project, base, content
            )
            folder = os.path.dirname(note_path)
            with self._lock:
                item.meeting_folder = folder
                item.source_path = source_path
            # Keep speakers.json for «Извлечь задачи» + directory compat, and so
            # store.build_view reads the project back from disk.
            utils.save_speakers(folder, opts.get("project_id"), [], {})
            utils.save_segments_sidecar(item.id, out.segments)

            if hermes_cfg.enabled:
                result = emit_audio_transcribed_event(
                    config=hermes_cfg,
                    transcript_text=out.text,
                    audio_path=item.audio_path,
                    history_folder=folder,
                    note_path=note_path,
                    source_path=source_path,
                    project=(
                        {"id": project.id, "name": project.name} if project else None
                    ),
                    provider=provider,
                    language=out.language,
                )
                with self._lock:
                    item.nudge_delivered = bool(result.sent)

            self._set_status(item, StageStatus.DONE)
        except Exception as e:  # worker-thread boundary: any failure halts THIS
            # item but must never kill the daemon. Humanize for the UI; the
            # ERROR status is the user signal. (CLAUDE.md broad-except: justified
            # boundary, tracked in test_broad_except_ratchet.)
            from tasks.errors import humanize

            logger.exception("processing failed for item %s", item.id)
            self._set_status(item, StageStatus.ERROR, error_message=humanize(e))

    def _next_auto_item(self) -> QueueItem | None:
        with self._lock:
            for it in self._items:
                if it.auto and it.status == StageStatus.PENDING:
                    return it
        return None

    def _run(self) -> None:
        while not self._stop:
            item = self._next_auto_item()
            if item is None:
                self._wake.wait(timeout=_IDLE_WAIT_S)
                self._wake.clear()
                continue
            self._process_item(item)
```

- [ ] **Step 8: Rewrite `tests/test_processing_worker.py`**

Replace the entire file with:

```python
import json
import os
import types

from directory.schema import Project
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


class _Out:
    def __init__(self, text="hello", language="ru", segments=None):
        self.text = text
        self.language = language
        self.segments = segments if segments is not None else [
            {"speaker": "A", "text": "hi"}
        ]


def _patch_happy(monkeypatch, *, duration_s=60.0, size_bytes=1000, capture=None):
    """Patch preflight.probe + cli.core.run_transcribe for a happy run. When
    ``capture`` is a dict, run_transcribe records its kwargs there."""
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": duration_s, "size_bytes": size_bytes},
    )

    def _fake_transcribe(*a, **k):
        if capture is not None:
            capture.update(k)
        return _Out()

    monkeypatch.setattr("cli.core.run_transcribe", _fake_transcribe)


def _sandbox_home(tmp_path, monkeypatch):
    """Keep the segments sidecar (~/.voxnote/segments) inside tmp_path."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def _audio(tmp_path, name="rec.m4a"):
    p = tmp_path / name
    p.write_bytes(b"\x00\x00")
    return str(p)


# ── enqueue / persistence (no processing) ──

def test_enqueue_appends_and_persists(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {"provider": "AssemblyAI", "project_id": "p1"})
    snap = q.snapshot()
    assert len(snap) == 1
    assert snap[0].id == item_id
    assert snap[0].audio_path == "/audio/a.m4a"
    assert snap[0].auto is True
    assert snap[0].project_id == "p1"
    assert snap[0].source == "pick"
    assert snap[0].status == StageStatus.PENDING
    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["items"][0]["id"] == item_id


def test_enqueue_captures_source(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {"source": "record"})
    assert q.snapshot()[0].source == "record"


def test_snapshot_is_a_deep_copy(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    snap = q.snapshot()
    snap[0].status = StageStatus.DONE
    assert q.snapshot()[0].status == StageStatus.PENDING


def test_on_change_fires_on_enqueue(tmp_path):
    calls = []
    q = _queue(tmp_path, on_change=lambda: calls.append(1))
    q.enqueue("/audio/a.m4a", {})
    assert calls == [1]


def test_loads_existing_active_items(tmp_path):
    q1 = _queue(tmp_path)
    q1.enqueue("/audio/a.m4a", {})
    q2 = _queue(tmp_path)
    assert len(q2.snapshot()) == 1


# ── _process_item: happy path + archive variants ──

def test_process_item_writes_note_and_copies_for_pick(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    from utils import load_segments_sidecar

    meetings = tmp_path / "meetings"
    sources_dir = tmp_path / "sources"
    audio = _audio(tmp_path)
    proj = Project(name="Kitng", id="p1")
    q = _queue(
        tmp_path,
        meetings_dir=str(meetings),
        resolve_project=lambda pid: proj if pid == "p1" else None,
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"},
            "sources_dir": str(sources_dir),
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "project_id": "p1", "source": "pick"})
    q._process_item(q._items[0])

    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert live.meeting_folder and os.path.isdir(live.meeting_folder)
    # transcript.md lives under the project subfolder
    assert os.path.basename(os.path.dirname(live.meeting_folder)) == "Kitng"
    note = os.path.join(live.meeting_folder, "transcript.md")
    assert os.path.isfile(note)
    with open(note, encoding="utf-8") as f:
        body = f.read()
    assert "hi" in body
    # speakers.json carries the project for build_view
    assert os.path.isfile(os.path.join(live.meeting_folder, "speakers.json"))
    # pick ⇒ COPY: original remains AND a copy landed in sources
    assert os.path.isfile(audio)
    assert live.source_path and os.path.isfile(live.source_path)
    assert os.path.dirname(live.source_path) == str(sources_dir)
    # segments → sidecar (not the folder, not the vault)
    assert load_segments_sidecar(live.id) == [{"speaker": "A", "text": "hi"}]
    assert not os.path.isfile(os.path.join(live.meeting_folder, "segments.json"))


def test_process_item_moves_audio_for_record(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    sources_dir = tmp_path / "sources"
    audio = _audio(tmp_path, "rec.wav")
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"}, "sources_dir": str(sources_dir),
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert not os.path.exists(audio)  # moved out (drains the source)
    assert live.source_path and os.path.isfile(live.source_path)


def test_process_item_without_sources_dir_keeps_audio(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},  # no sources_dir
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert os.path.isfile(audio)  # not archived → left in place
    assert live.source_path is None
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        assert "source_path:" in f.read()  # note records the original path


# ── _process_item: guards + errors ──

def test_process_item_missing_key_errors_and_halts(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(tmp_path, meetings_dir=str(tmp_path / "meetings"), config_loader=lambda: {})
    q.enqueue(audio, {"provider": "AssemblyAI"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.ERROR
    assert live.error_message
    assert live.meeting_folder is None


def test_process_item_provider_cap_blocks_before_upload(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": None, "size_bytes": 5 * 1024**3},
    )
    called = []
    monkeypatch.setattr("cli.core.run_transcribe", lambda *a, **k: called.append(1))
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI"})
    q._process_item(q._items[0])
    assert q.snapshot()[0].status == StageStatus.ERROR
    assert called == []  # never spent an upload


def test_process_item_denoise_auto_off_for_long_audio(tmp_path, monkeypatch):
    cap = {}
    _patch_happy(monkeypatch, duration_s=46 * 60, capture=cap)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "denoise": True})
    q._process_item(q._items[0])
    assert cap["denoise"] is False
    assert q.snapshot()[0].status == StageStatus.DONE


def test_process_item_denoise_kept_for_short_audio(tmp_path, monkeypatch):
    cap = {}
    _patch_happy(monkeypatch, duration_s=600, capture=cap)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "denoise": True})
    q._process_item(q._items[0])
    assert cap["denoise"] is True


def test_process_item_transcribe_error_halts_and_leaves_audio(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 1000},
    )

    def _boom(*a, **k):
        raise RuntimeError("AssemblyAI вернул 401")

    monkeypatch.setattr("cli.core.run_transcribe", _boom)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.ERROR
    assert live.error_message
    assert os.path.isfile(audio)  # untouched on failure (archive is after STT)


# ── _process_item: Hermes nudge ──

def test_process_item_nudge_enabled_marks_delivered(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    sent = {}

    def _emit(**k):
        sent.update(k)
        return types.SimpleNamespace(sent=True)

    monkeypatch.setattr(
        "integrations.hermes.client.emit_audio_transcribed_event", _emit
    )
    audio = _audio(tmp_path)
    proj = Project(name="P", id="p1")
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        resolve_project=lambda pid: proj,
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"},
            "hermes_webhook_enabled": True,
            "hermes_webhook_secret": "s",
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "project_id": "p1"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert live.nudge_delivered is True
    assert sent["note_path"].endswith("transcript.md")
    assert sent["project"] == {"id": "p1", "name": "P"}
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        assert "nudged: true" in f.read()


def test_process_item_nudge_failure_still_done(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "integrations.hermes.client.emit_audio_transcribed_event",
        lambda **k: types.SimpleNamespace(sent=False),
    )
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"},
            "hermes_webhook_enabled": True,
            "hermes_webhook_secret": "s",
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert live.nudge_delivered is False


# ── retry / scheduling ──

def test_retry_resets_errored_item_to_pending(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    it = q._items[0]
    it.status = StageStatus.ERROR
    it.error_message = "boom"
    it.auto = False
    q.retry(item_id)
    live = q.snapshot()[0]
    assert live.status == StageStatus.PENDING
    assert live.error_message is None
    assert live.auto is True


def test_retry_ignores_non_errored(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    q._items[0].status = StageStatus.DONE
    q.retry(item_id)
    assert q.snapshot()[0].status == StageStatus.DONE


def test_retry_unknown_id_is_noop(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q.retry("nope")
    assert len(q.snapshot()) == 1


def test_next_auto_item_skips_auto_false(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q._items[0].auto = False
    assert q._next_auto_item() is None


def test_next_auto_item_skips_settled(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q._items[0].status = StageStatus.DONE
    assert q._next_auto_item() is None


def test_started_thread_drains_to_done(tmp_path, monkeypatch):
    import time

    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.start()
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if q.snapshot()[0].status == StageStatus.DONE:
            break
        time.sleep(0.02)
    q.stop()
    assert q.snapshot()[0].status == StageStatus.DONE
```

- [ ] **Step 9a: Bump the broad-except ratchet baseline**

The 1-stage worker has ONE broad-except boundary (was 3). In `tests/test_broad_except_ratchet.py`, edit the `BASELINE` dict entry:

```python
    "processing/worker.py": 3,                     # worker-thread stage boundaries
```

to:

```python
    "processing/worker.py": 1,                     # single worker-thread boundary
```

- [ ] **Step 9b: Add config keys**

In `config.example.json`, replace:

```json
  "meetings_dir": "",
```

with:

```json
  "meetings_dir": "",
  "sources_dir": "",
  "inbox_dir": "",
```

(`sources_dir` → Google Drive `sources/` for archived audio, consumed by the worker now; `inbox_dir` → Drive `inbox/` for phone ingestion, consumed by the watcher in PR-C. Empty = feature off.)

- [ ] **Step 9c: Green gate — full suite + lint**

Run: `py -3 -m pytest -q`
Expected: PASS (baseline ~988 + the new preflight tests; processing trio green). If `processing/` tests fail, fix the trio before committing — do NOT commit red.

Run: `py -3 -m ruff check .`
Expected: clean. (Watch for unused imports — the worker no longer imports `json` or `layout`.)

- [ ] **Step 10: Commit**

```bash
git add processing/model.py processing/store.py processing/worker.py \
        tests/test_processing_model.py tests/test_processing_store.py \
        tests/test_processing_worker.py tests/test_broad_except_ratchet.py \
        config.example.json
git commit -F- <<'EOF'
feat(processing): single-stage transcribe-only queue (PR-B2)

Rework the queue trio for the Hermes-native architecture: VoxNote
transcribes + diarizes, writes transcript.md into the vault, archives
audio to Drive sources/, and fires a best-effort Hermes nudge. Hermes
owns protocol/tasks/approve/send downstream.

- model: single `status` (drop AWAITING_REVIEW + transcript/protocol/tasks
  stage fields + error_stage); add source/source_path/nudge_delivered and
  disk-derived display badges has_protocol/has_tasks.
- store: status from transcript.md presence; protocol.md/tasks.md surfaced
  as badges (meeting_status_from_folder + hermes_badges_from_folder replace
  stage_status_from_folder); project still read from speakers.json.
- worker: one `_process_item` composing preflight + run_transcribe +
  sources.archive_audio (move for record/inbox, copy for pick) +
  vault_note + segments sidecar + emit_audio_transcribed_event. Archive
  only after a successful transcribe; one broad-except boundary (ratchet
  3→1).
- config.example: add sources_dir + inbox_dir.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 3: `AGENTS.md` — pipeline framing + webhook v1.1 (docs)

**Files:**
- Modify: `AGENTS.md`

AGENTS.md still describes the old whole-chain pipeline as VoxNote's job and shows the webhook at v1.0. Update the framing (queue is transcribe-only; Hermes owns downstream; CLI/MCP commands remain for manual/agent use) and bring the documented event to v1.1 (matches the merged PR-B1 code: `audio.note_path`/`audio.source_path` + top-level `project`).

- [ ] **Step 1: Update the pipeline framing**

Replace:

```markdown
Pipeline: **transcribe → extract tasks → generate protocol → send to a task
backend** (Linear / Glide / Trello). Cloud STT (AssemblyAI / Deepgram / Gladia /
Speechmatics); KZ+RU+EN code-switching; OpenRouter for tasks + protocol.
```

with:

```markdown
Pipeline (full chain, available to CLI/MCP callers): **transcribe → extract
tasks → generate protocol → send to a task backend** (Linear / Glide / Trello).
Cloud STT (AssemblyAI / Deepgram / Gladia / Speechmatics); KZ+RU+EN
code-switching; OpenRouter for tasks + protocol.

> **Desktop queue (Mini-AGI / Hermes-native flow):** VoxNote's own processing
> queue runs **transcribe-only** — it writes a diarized `transcript.md` into the
> Obsidian vault, archives the audio to Google Drive `sources/`, and fires a
> best-effort `audio.transcribed` nudge (§4). **Hermes** then owns the
> downstream: protocol, task extraction, human approval, and sending to
> trackers. The `extract-tasks` / `protocol` / `send` commands below remain
> available for manual or agent-driven use — they are simply not what the
> desktop auto-pipeline runs.
```

- [ ] **Step 2: Update the §4.1 event payload to v1.1**

Replace the JSON block under "### 4.1 Event payload shape":

```json
{
  "event_type": "audio.transcribed",
  "version": "1.0",
  "source": "voxnote",
  "routing_hint": "obsidian_inbox",
  "audio": {
    "filename": "meeting.m4a",
    "path": "C:/Users/.../meeting.m4a",
    "history_folder": "C:/Users/.../<meeting-folder>"
  },
  "transcript": {
    "raw": "<full transcript text>",
    "segments": []
  },
  "analysis": {
    "summary": null,
    "tasks": [],
    "ideas": [],
    "decisions": [],
    "protocol": null
  },
  "meta": {
    "provider": "AssemblyAI",
    "language": "ru",
    "created_at": "2026-06-11T12:00:00Z"
  }
}
```

with:

```json
{
  "event_type": "audio.transcribed",
  "version": "1.1",
  "source": "voxnote",
  "routing_hint": "obsidian_inbox",
  "audio": {
    "filename": "meeting.m4a",
    "path": "C:/Users/.../meeting.m4a",
    "history_folder": "C:/Users/.../<meeting-folder>",
    "note_path": "C:/Users/.../30 Meetings/<project>/<meeting>/transcript.md",
    "source_path": "G:/My Drive/.../sources/2026-06-14_1000_meeting.m4a"
  },
  "project": { "id": "p1", "name": "Kitng" },
  "transcript": {
    "raw": "<full transcript text>",
    "segments": []
  },
  "analysis": {
    "summary": null,
    "tasks": [],
    "ideas": [],
    "decisions": [],
    "protocol": null
  },
  "meta": {
    "provider": "AssemblyAI",
    "language": "ru",
    "created_at": "2026-06-11T12:00:00Z"
  }
}
```

- [ ] **Step 3: Update the "Key fields" line**

Replace:

```markdown
Key fields for Hermes routing: `event_type`, `routing_hint`,
`transcript.raw`, `meta.provider`, `meta.language`, `audio.history_folder`.
```

with:

```markdown
Key fields for Hermes routing: `event_type`, `routing_hint`,
`transcript.raw`, `meta.provider`, `meta.language`, `audio.history_folder`,
`audio.note_path` (the vault `transcript.md`), `audio.source_path` (the archived
audio in Drive `sources/`), and `project` (`{id, name}`, or `null` outside a
queue run).
```

- [ ] **Step 4: Update the §4.5 curl smoke body to v1.1**

Replace the `BODY='...'` line under "### 4.5 Docs-only curl smoke example":

```bash
BODY='{"analysis":{"decisions":[],"ideas":[],"protocol":null,"summary":null,"tasks":[]},"audio":{"filename":"test.m4a","history_folder":null,"path":null},"event_type":"audio.transcribed","meta":{"created_at":"2026-06-11T12:00:00Z","language":"ru","provider":"test"},"routing_hint":"obsidian_inbox","source":"voxnote","transcript":{"raw":"test","segments":[]},"version":"1.0"}'
```

with (keys stay sorted — `sort_keys=True`; new `audio.note_path`/`audio.source_path` + top-level `project` added as `null`):

```bash
BODY='{"analysis":{"decisions":[],"ideas":[],"protocol":null,"summary":null,"tasks":[]},"audio":{"filename":"test.m4a","history_folder":null,"note_path":null,"path":null,"source_path":null},"event_type":"audio.transcribed","meta":{"created_at":"2026-06-11T12:00:00Z","language":"ru","provider":"test"},"project":null,"routing_hint":"obsidian_inbox","source":"voxnote","transcript":{"raw":"test","segments":[]},"version":"1.1"}'
```

- [ ] **Step 5: Verify nothing else regressed + lint**

Run: `py -3 -m pytest -q && py -3 -m ruff check .`
Expected: PASS + clean (AGENTS.md is docs; this is a safety re-run).

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md
git commit -F- <<'EOF'
docs(agents): transcribe-only queue framing + audio.transcribed v1.1

Clarify that VoxNote's desktop queue is transcribe-only (vault transcript +
Drive sources archive + Hermes nudge) and Hermes owns protocol/tasks/send;
the CLI/MCP full chain stays available for manual/agent use. Bring the
documented webhook event to v1.1 (audio.note_path / audio.source_path /
project), matching the merged PR-B1 schema.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Finish

After all three tasks: use **superpowers:finishing-a-development-branch** → verify tests → push `feat/transcription-queue-pr-b2` + open a PR for the user to review/merge (Option 2). PR body: `## Summary` (single-stage transcribe-only queue + preflight + docs) + `## Test plan` (checkboxes: preflight unit tests; trio model/store/worker tests incl. archive move/copy, provider-cap block, long-audio denoise-off, nudge delivered/failed, retry; full suite + ruff green; ratchet baseline updated).

Then **PR-C (UI wiring)** — out of scope here: enqueue from record/«Выбрать файл»/inbox poll-tick + main-bar project selector + indicator strip + remove «Транскрибировать» + «Встречи» = queue+history with Hermes-progress badges. Spec §UI / §Phasing.

---

## Self-review

**Spec coverage**

- Folder-per-meeting, transcript.md only (VoxNote) → worker writes via `vault_note.write_transcript_note`; drops description.md / in-folder audio / in-folder segments.json. ✓ (spec §1, §store; handoff #1)
- Audio → Drive `sources/`, move for record/inbox, copy for pick → `sources.archive_audio(move=item.source in {record,inbox})`. ✓ (spec §3 archive; handoff #2)
- `sources_dir` unset → skip archive, note records original path → `if sources_dir:` guard + `source_path or item.audio_path`. ✓ (spec §Failure-handling)
- Segments → app-data sidecar → `utils.save_segments_sidecar(item.id, out.segments)`. ✓ (handoff #4)
- `nudged:` frontmatter = Hermes enabled → `nudged=hermes_cfg.enabled`. ✓ (handoff #5)
- Single `status` (PENDING/RUNNING/DONE/ERROR) + new fields → model rewrite. ✓ (handoff #6)
- `build_view` status from transcript.md + protocol.md/tasks.md badges + project from speakers.json → store rewrite. ✓ (handoff #7, spec §store)
- Pre-flight: probe + provider-cap block + long-audio denoise-off + cost → `preflight.py`. ✓ (spec §Long-audio; handoff preflight)
- Ordering "archive only after successful transcribe" → archive placed after `run_transcribe`; reconciled with the note's `source_path` by archiving before the single note write (documented in worker comment). ✓ (spec §Failure-handling, ordering)
- Speaker-count forwarding (PR-B1) used → `num/min/max_speakers=opts.get(...)`. ✓
- Hermes v1.1 fields (PR-B1) used → `note_path`/`source_path`/`project`. ✓
- AGENTS.md stale-pipeline fix → Task 3. ✓ (user's standing request)

**Placeholder scan:** none — every code/test step shows complete content; commands have expected output. ✓

**Type/name consistency:**
- `StageStatus` keeps its name (only `AWAITING_REVIEW` removed); imported unchanged by store + tests. ✓
- `meeting_status_from_folder` / `hermes_badges_from_folder` used consistently across store + test imports. ✓
- `QueueItem` fields (`status`, `source`, `source_path`, `nudge_delivered`, `has_protocol`, `has_tasks`) match between model, store `_row_from_folder`, worker, and all tests. ✓
- Worker calls match real signatures: `run_transcribe(...num_speakers=...)`, `archive_audio(audio_path, sources_dir, base_name, *, move)`, `render_transcript_note(*, segments, title, project_name, date, time, participants, provider, language, voxnote_id, source_path, nudged)`, `write_transcript_note(meetings_dir, project, meeting_name, content)`, `save_speakers(folder, project_id, participant_ids, speaker_map)`, `save_segments_sidecar(voxnote_id, segments)`, `emit_audio_transcribed_event(*, config, transcript_text, audio_path, history_folder, note_path, source_path, project, provider, language)`, `get_hermes_webhook_config(cfg)`. ✓ (all verified against current source)
- Broad-except: worker has exactly one `except Exception` (the `except OSError` archive guard is narrow) → ratchet 3→1 correct. ✓
- `processing/layout.py` + `store.is_meeting_folder` kept (used by `vault_note` + `scripts/organize_by_project.py`); worker drops its unused `layout` + `json` imports (ruff gate catches leftovers). ✓
