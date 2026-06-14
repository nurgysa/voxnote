# Transcription queue PR-B1 — worker-support primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three **additive** primitives PR-B2's reworked worker will consume —
the speaker-count hint threaded through `cli.core`, the Hermes `audio.transcribed`
event v1.1 (`note_path` + `source_path` + `project`), and the Drive-`inbox/`
polling watcher — without touching `model`/`store`/`worker` (so every commit stays
green).

**Architecture:** PR-B1 adds/extends seams only. The trio rework (`model`+`store`+
`worker` → 1-stage) and `preflight.py` (duration probe needs an ffmpeg-based path)
land in **PR-B2**, which wires these primitives in. Spec:
`docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md`.

**Tech Stack:** Python 3.10+ stdlib, `pytest` with `monkeypatch`/`tmp_path`.
Builds on merged PR-A (`processing/{vault_note,sources}`, `utils` sidecar,
`transcript_format.format_diarized_markdown`).

---

## Scope (PR-B1 only)

**In:** `cli/core.py` (speaker-count kwargs), `integrations/hermes/{schema,client}.py`
(v1.1 fields) + their tests, `processing/inbox_watcher.py` (new) + tests.

**Out (PR-B2 / PR-C):** `processing/model.py`, `processing/store.py`,
`processing/worker.py`, `processing/preflight.py`, all `ui/`, `AGENTS.md` pipeline
rewrite. These primitives are unused until PR-B2 wires them (deliberate, tested).

## Key grounding (verified)

- `cli.core.run_transcribe(audio_path, *, provider, api_key, language=None,
  diarize=False, hotwords=None, denoise=False, on_status=None)` calls
  `transcriber.transcribe(...)`. `Transcriber.transcribe` ALREADY accepts
  `num_speakers` / `min_speakers` / `max_speakers` (the GUI passes them today) —
  `run_transcribe` just doesn't forward them yet.
- `integrations/hermes/schema.build_audio_transcribed_event(*, transcript_text,
  audio_path=None, history_folder=None, provider=None, language=None,
  segments=None, routing_hint="obsidian_inbox", summary=None, tasks=None,
  ideas=None, decisions=None, protocol=None, created_at=None) -> dict` builds a
  payload with `version: "1.0"`, an `audio` block (`filename`/`path`/
  `history_folder`), `transcript`, `analysis`, `meta`. `client.emit_audio_transcribed_event`
  wraps build → post.
- `tests/test_hermes_webhook_schema.py` asserts `payload["version"] == "1.0"` (two
  places: the version test and a "stability" test). The v1.1 bump must update both.
- `tests/test_hermes_webhook_client.py` may assert HMAC signature / `X-Request-ID`
  over a payload built by the function — adding fields/bumping the version changes
  the body bytes, so any such assertion must be reconciled (re-derive expected
  values or assert structurally). The implementer runs the suite and fixes what
  the change breaks.
- `tests/test_cli_import_guard.py` enforces `cli.core` headlessness — do not add
  top-level heavy imports.
- Baseline after PR-A: full suite green (977 passed, 2 skipped). Run `py -3 -m
  pytest -q` (fallback `python -m pytest -q`) and `py -3 -m ruff check .` before
  each commit.

---

### Task 1: thread the speaker-count hint through `cli.core.run_transcribe`

**Files:**
- Modify: `cli/core.py`
- Test: `tests/test_cli_core_speaker_count.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_core_speaker_count.py
from cli import core


def test_run_transcribe_forwards_speaker_count(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00")
    captured = {}

    class _FakeTranscriber:
        last_segments = []

        def transcribe(self, audio_path, **kw):
            captured.update(kw)
            return "hi"

    monkeypatch.setattr("transcriber.Transcriber", _FakeTranscriber)
    out = core.run_transcribe(
        str(audio), provider="AssemblyAI", api_key="k",
        num_speakers=3, min_speakers=None, max_speakers=None,
    )
    assert out.text == "hi"
    assert captured["num_speakers"] == 3
    assert captured["min_speakers"] is None
    assert captured["max_speakers"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_cli_core_speaker_count.py -v`
Expected: FAIL — `run_transcribe()` got an unexpected keyword argument `num_speakers`.

- [ ] **Step 3: Write minimal implementation**

In `cli/core.py`, extend `run_transcribe`'s signature (insert after `denoise: bool
= False,` and before `on_status=None,`):

```python
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
```

and add the three kwargs to the `transcriber.transcribe(...)` call (alongside the
existing `denoise_audio=denoise, cloud_provider=provider, cloud_api_key=api_key`):

```python
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
```

- [ ] **Step 4: Run test + import guard**

Run: `py -3 -m pytest tests/test_cli_core_speaker_count.py tests/test_cli_import_guard.py -v`
Expected: PASS (new test green; headless guard still green).

- [ ] **Step 5: Commit**

```bash
git add cli/core.py tests/test_cli_core_speaker_count.py
git commit -m "feat(cli): thread speaker-count hint through run_transcribe" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Hermes `audio.transcribed` v1.1 — `note_path` + `source_path` + `project`

**Files:**
- Modify: `integrations/hermes/schema.py`, `integrations/hermes/client.py`
- Test: `tests/test_hermes_webhook_schema.py` (update), `tests/test_hermes_v11_fields.py` (create)

- [ ] **Step 1: Write the new failing test**

```python
# tests/test_hermes_v11_fields.py
from integrations.hermes.schema import build_audio_transcribed_event


def test_version_is_1_1():
    assert build_audio_transcribed_event(transcript_text="x")["version"] == "1.1"


def test_note_path_in_audio_block():
    p = build_audio_transcribed_event(
        transcript_text="x", note_path="C:/Vault/30 Meetings/Kitng/m/transcript.md",
    )
    assert p["audio"]["note_path"] == "C:/Vault/30 Meetings/Kitng/m/transcript.md"


def test_source_path_in_audio_block():
    p = build_audio_transcribed_event(
        transcript_text="x", source_path="G:/My Drive/sources/m.m4a",
    )
    assert p["audio"]["source_path"] == "G:/My Drive/sources/m.m4a"


def test_project_top_level():
    p = build_audio_transcribed_event(
        transcript_text="x", project={"id": "p1", "name": "Kitng"},
    )
    assert p["project"] == {"id": "p1", "name": "Kitng"}


def test_new_fields_default_none():
    p = build_audio_transcribed_event(transcript_text="x")
    assert p["audio"]["note_path"] is None
    assert p["audio"]["source_path"] is None
    assert p["project"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3 -m pytest tests/test_hermes_v11_fields.py -v`
Expected: FAIL — version is "1.0"; `note_path`/`source_path`/`project` absent.

- [ ] **Step 3: Implement the schema change**

In `integrations/hermes/schema.py`, `build_audio_transcribed_event`:
- Add three keyword params (after `protocol: str | None = None,`):
  ```python
    note_path: str | None = None,
    source_path: str | None = None,
    project: dict | None = None,
  ```
- Change `"version": "1.0",` → `"version": "1.1",`.
- Add `"note_path": note_path,` and `"source_path": source_path,` inside the
  `"audio": {...}` dict.
- Add a top-level `"project": project,` key (e.g. right after the `"audio"` block).

In `integrations/hermes/client.py`, `emit_audio_transcribed_event`: add the same
three params (`note_path`, `source_path`, `project`, all `= None`) and forward
them into the `build_audio_transcribed_event(...)` call.

- [ ] **Step 4: Update the existing schema test for the version bump**

In `tests/test_hermes_webhook_schema.py`, change BOTH `assert payload["version"]
== "1.0"` assertions to `== "1.1"`. Run the FULL hermes test set:

Run: `py -3 -m pytest tests/test_hermes_v11_fields.py tests/test_hermes_webhook_schema.py tests/test_hermes_webhook_client.py -v`
Expected: PASS. If a client test asserts a hardcoded HMAC signature / `X-Request-ID`
built from `build_audio_transcribed_event`, the new keys changed the body bytes —
reconcile it: prefer asserting structurally (signature is a 64-char hex; request-id
starts `voxnote:`) or re-derive the expected value from the now-current payload.
Do NOT weaken what the test actually verifies (that signing is deterministic over
the exact body bytes).

- [ ] **Step 5: Full suite + ruff**

Run: `py -3 -m pytest -q` → exit 0. `py -3 -m ruff check .` → clean.

- [ ] **Step 6: Commit**

```bash
git add integrations/hermes/schema.py integrations/hermes/client.py tests/test_hermes_v11_fields.py tests/test_hermes_webhook_schema.py
# include tests/test_hermes_webhook_client.py in the add if you had to reconcile it
git commit -m "feat(hermes): audio.transcribed v1.1 — note_path + source_path + project" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `processing/inbox_watcher.py` — Drive-inbox polling with debounce

**Files:**
- Create: `processing/inbox_watcher.py`
- Test: `tests/test_inbox_watcher.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inbox_watcher.py
from processing.inbox_watcher import InboxWatcher, scan_inbox


def test_scan_filters_extensions_and_known(tmp_path):
    (tmp_path / "a.m4a").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    (tmp_path / "c.mp3").write_bytes(b"x")
    found = scan_inbox(str(tmp_path), known={str(tmp_path / "c.mp3")})
    assert found == [str(tmp_path / "a.m4a")]


def test_poll_requires_stable_size(tmp_path):
    f = tmp_path / "rec.m4a"
    f.write_bytes(b"12345")
    w = InboxWatcher(str(tmp_path))
    assert w.poll() == []            # first sighting: record size, not ready
    assert w.poll() == [str(f)]      # size stable across two polls → ready
    assert w.poll() == []            # already returned, not re-emitted


def test_poll_growing_file_not_ready(tmp_path):
    f = tmp_path / "rec.m4a"
    f.write_bytes(b"1")
    w = InboxWatcher(str(tmp_path))
    assert w.poll() == []            # record size 1
    f.write_bytes(b"123")            # still being written (grew)
    assert w.poll() == []            # size changed → not ready
    assert w.poll() == [str(f)]      # now stable → ready


def test_poll_no_dir():
    assert InboxWatcher(None).poll() == []


def test_poll_missing_dir(tmp_path):
    assert InboxWatcher(str(tmp_path / "missing")).poll() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3 -m pytest tests/test_inbox_watcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'processing.inbox_watcher'`.

- [ ] **Step 3: Write the implementation**

```python
# processing/inbox_watcher.py
"""Poll a Google Drive-synced `inbox/` folder for phone-uploaded audio.

Phone → Google Drive (mobile) → inbox/ → (Drive Desktop syncs) → this watcher.
Polling (not a filesystem-event lib) keeps it dependency-free and robust to Drive
sync quirks. A file is only handed off once its size is STABLE across two polls —
a large file (a 2-3 h recording is 100+ MB) is still syncing down and must not be
grabbed mid-write. No Tk: the App drives poll() on an after(...) tick and enqueues
the returned paths.
"""
from __future__ import annotations

import os

_AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac"}


def scan_inbox(inbox_dir: str, *, known: set[str]) -> list[str]:
    """Audio files directly in `inbox_dir` not already in `known`. Sorted, pure."""
    try:
        names = sorted(os.listdir(inbox_dir))
    except OSError:
        return []
    out: list[str] = []
    for name in names:
        full = os.path.join(inbox_dir, name)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(name)[1].lower() not in _AUDIO_EXTS:
            continue
        if full in known:
            continue
        out.append(full)
    return out


class InboxWatcher:
    """Stateful debounce over scan_inbox. poll() returns files whose size held
    steady since the previous poll (i.e. finished syncing), each returned once."""

    def __init__(self, inbox_dir: str | None) -> None:
        self._inbox_dir = inbox_dir
        self._sizes: dict[str, int] = {}   # path -> size seen last poll
        self._done: set[str] = set()       # already handed off

    def poll(self) -> list[str]:
        if not self._inbox_dir or not os.path.isdir(self._inbox_dir):
            return []
        candidates = scan_inbox(self._inbox_dir, known=self._done)
        live = set(candidates)
        ready: list[str] = []
        for path in candidates:
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if self._sizes.get(path) == size:   # unchanged since last poll → stable
                ready.append(path)
                self._done.add(path)
                self._sizes.pop(path, None)
            else:
                self._sizes[path] = size
        # Drop bookkeeping for files that vanished (moved out / deleted).
        self._sizes = {p: s for p, s in self._sizes.items() if p in live}
        return ready
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_inbox_watcher.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full suite + ruff**

Run: `py -3 -m pytest -q` → exit 0. `py -3 -m ruff check .` → clean.

- [ ] **Step 6: Commit**

```bash
git add processing/inbox_watcher.py tests/test_inbox_watcher.py
git commit -m "feat(processing): inbox_watcher — poll Drive inbox with size-stable debounce" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage (PR-B1 slice):**
- speaker-count through `cli.core` → Task 1. ✓
- event v1.1 (`note_path` + `source_path` + `project`) → Task 2. ✓
- Drive-inbox watcher with debounce → Task 3. ✓
- **Deferred to PR-B2:** model/store/worker rework, `preflight.py` (duration probe
  via ffmpeg for m4a), nudge wiring, AGENTS.md. ✓

**2. Placeholder scan:** complete code in every code step. Task 2 Step 4 carries an
adaptive instruction (reconcile any hardcoded-signature client test) rather than
guessing the exact hash — the implementer runs the suite and fixes precisely; this
is correct, not a placeholder.

**3. Type consistency:** `run_transcribe` new kwargs match `transcriber.transcribe`;
`build_audio_transcribed_event` / `emit_audio_transcribed_event` gain the same three
param names; `scan_inbox(inbox_dir, *, known)` and `InboxWatcher(inbox_dir).poll()`
names match between Task 3's test and impl, and match the spec's component section.

**Decision log:**
- **`preflight.py` deferred to PR-B2** — its duration probe must handle compressed
  audio (m4a/mp3), which `audio_io.get_duration_s` (soundfile) cannot read;
  it needs an ffmpeg-based probe (ffprobe is not bundled — only ffmpeg, via
  `utils.get_ffmpeg_path()`). Grounding that belongs with the worker that consumes
  it, so it moves to B2.
- **v1.1 fields follow the existing always-present-with-None pattern** (matching the
  `audio` block's `filename`/`path`), so consumers see a stable shape; the version
  bump signals the addition.
