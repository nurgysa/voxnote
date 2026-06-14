# Transcription queue PR-A — storage core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four **additive, pure, headless** storage primitives the new
transcription queue needs — diarized-Markdown rendering, the meeting-folder
`transcript.md` writer, the Drive `sources` audio archiver, and the segments
sidecar — without touching the existing `model`/`store`/`worker` (so every commit
stays green).

**Architecture:** PR-A adds new functions/modules only. They are exercised by
unit tests and are deliberately not wired into the worker yet (that is PR-B, which
reworks the coupled `model`+`store`+`worker` trio together). This mirrors how
PR-2a shipped a tested-but-unwired worker. Spec:
`docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md`.

**Tech Stack:** Python 3.10+ stdlib only (`os`, `shutil`, `json`), `pytest` with
`tmp_path`. Reuses `transcript_format._build_speaker_map`,
`processing.layout.target_dir`, `directory.schema.Project`.

---

## Scope (PR-A only)

**In:** `transcript_format.format_diarized_markdown`, `processing/sources.py`,
`utils.save/load_segments_sidecar`, `processing/vault_note.py`, and their tests.

**Out (PR-B / PR-C):** any change to `processing/model.py`, `processing/store.py`,
`processing/worker.py`, `cli/core.py`, `integrations/hermes/*`, the inbox watcher,
pre-flight, the event schema, and all `ui/`. PR-A ships only additive primitives —
unused by the GUI/worker until PR-B (accepted: each is independently testable and
mergeable).

## Key grounding (verified — do not re-invent)

- Segment dict shape: `{"start": float, "end": float, "text": str, "speaker"?: str}`
  (`transcript_format.py` module docstring).
- `transcript_format._build_speaker_map(segments) -> dict` maps `SPEAKER_XX →
  "Спикер N"` (first-seen order); non-`SPEAKER_` labels kept verbatim. Reuse it.
- `processing.layout.target_dir(meetings_dir, project: Project | None) -> str` —
  `<meetings_dir>/<project_dirname>/` for a project, `<meetings_dir>` for `None`.
- `directory.schema.Project(name=..., id=...)` — `.name`, `.id`.
- `~/.voxnote/` is the app-data root (home via `USERPROFILE`/`HOME`), matching
  `processing/store._default_queue_path`.
- `utils.py` already imports `os` and `json`.
- Baseline: `pytest` green (~939+ tests); `ruff` clean. Run both before each commit.

---

### Task 1: `format_diarized_markdown` — Obsidian-friendly diarized body

**Files:**
- Modify: `transcript_format.py`
- Test: `tests/test_format_diarized_markdown.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_format_diarized_markdown.py
from transcript_format import format_diarized_markdown


def _seg(start, text, speaker=None):
    return {"start": start, "end": start + 1, "text": text, "speaker": speaker}


def test_groups_consecutive_same_speaker():
    segs = [
        _seg(0, "привет", "SPEAKER_00"),
        _seg(1, "как дела", "SPEAKER_00"),
        _seg(2, "норм", "SPEAKER_01"),
    ]
    assert format_diarized_markdown(segs) == (
        "**Спикер 1:** привет как дела\n\n**Спикер 2:** норм"
    )


def test_no_speakers_plain_paragraphs():
    segs = [_seg(0, "первый"), _seg(1, "второй")]
    assert format_diarized_markdown(segs) == "первый\n\nвторой"


def test_empty_returns_empty():
    assert format_diarized_markdown([]) == ""


def test_speaker_map_override():
    segs = [_seg(0, "да", "SPEAKER_00")]
    assert format_diarized_markdown(
        segs, speaker_map={"SPEAKER_00": "Айгерим"}
    ) == "**Айгерим:** да"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_format_diarized_markdown.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_diarized_markdown'`.

- [ ] **Step 3: Write minimal implementation**

Append to `transcript_format.py` (after `format_diarized`):

```python
def format_diarized_markdown(
    segments: list[dict], speaker_map: dict[str, str] | None = None
) -> str:
    """Obsidian-friendly diarized body: ``**Speaker:** text`` blocks, consecutive
    same-speaker segments merged, NO timecodes (those live in the SRT/VTT export).
    No speakers anywhere → plain paragraphs. Pure."""
    if not segments:
        return ""
    if speaker_map is None:
        speaker_map = _build_speaker_map(segments)

    blocks: list[str] = []
    prev: str | None = None
    texts: list[str] = []

    def _flush() -> None:
        body = " ".join(t.strip() for t in texts if t and t.strip())
        if body:
            blocks.append(f"**{prev}:** {body}" if prev else body)

    for seg in segments:
        raw = seg.get("speaker")
        speaker = speaker_map.get(raw, str(raw)) if raw else None
        if speaker == prev and prev is not None:
            texts.append(seg.get("text", ""))
            continue
        _flush()
        texts = [seg.get("text", "")]
        prev = speaker
    _flush()
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_format_diarized_markdown.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add transcript_format.py tests/test_format_diarized_markdown.py
git commit -m "feat(transcript): format_diarized_markdown for the meeting note body" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `processing/sources.py` — archive audio into Drive `sources`

**Files:**
- Create: `processing/sources.py`
- Test: `tests/test_processing_sources.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_processing_sources.py
import os

from processing import sources


def test_archive_copy_leaves_original(tmp_path):
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"abc")
    dest = tmp_path / "sources"
    out = sources.archive_audio(str(src), str(dest), "2026-06-14_1000_call", move=False)
    assert out == os.path.join(str(dest), "2026-06-14_1000_call.m4a")
    assert os.path.isfile(out)
    assert src.exists()  # copy leaves the original
    assert (dest / "2026-06-14_1000_call.m4a").read_bytes() == b"abc"


def test_archive_move_removes_original(tmp_path):
    src = tmp_path / "rec.mp3"
    src.write_bytes(b"x")
    dest = tmp_path / "sources"
    out = sources.archive_audio(str(src), str(dest), "m", move=True)
    assert out.endswith("m.mp3")
    assert os.path.isfile(out)
    assert not src.exists()  # moved


def test_archive_collision_safe(tmp_path):
    dest = tmp_path / "sources"
    dest.mkdir()
    (dest / "m.m4a").write_bytes(b"old")
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"new")
    out = sources.archive_audio(str(src), str(dest), "m", move=False)
    assert out.endswith("m-2.m4a")
    assert (dest / "m.m4a").read_bytes() == b"old"  # never overwritten
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'processing.sources'`.

- [ ] **Step 3: Write minimal implementation**

```python
# processing/sources.py
"""Archive audio originals into the Google Drive `sources` folder.

A plain filesystem write — Google Drive Desktop syncs it; no gdrive API. The
meeting's transcript.md records where the archived audio lives. `move=True` for
in-app recordings and inbox files (ours to relocate; this is what drains the
inbox); `move=False` (copy) for user-picked files (leave their original in place).
"""
from __future__ import annotations

import os
import shutil


def archive_audio(
    audio_path: str, sources_dir: str, base_name: str, *, move: bool
) -> str:
    """Place ``audio_path`` at ``<sources_dir>/<base_name><ext>``, collision-safe
    (``-2``, ``-3`` … never overwrites). Returns the archived path."""
    os.makedirs(sources_dir, exist_ok=True)
    ext = os.path.splitext(audio_path)[1]
    target = os.path.join(sources_dir, f"{base_name}{ext}")
    n = 2
    while os.path.exists(target):
        target = os.path.join(sources_dir, f"{base_name}-{n}{ext}")
        n += 1
    if move:
        shutil.move(audio_path, target)
    else:
        shutil.copy2(audio_path, target)
    return target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_sources.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add processing/sources.py tests/test_processing_sources.py
git commit -m "feat(processing): archive_audio — place audio in Drive sources (move/copy)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: segments sidecar — `utils.save/load_segments_sidecar`

**Files:**
- Modify: `utils.py`
- Test: `tests/test_segments_sidecar.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_segments_sidecar.py
import os

import utils


def test_sidecar_round_trip(tmp_path):
    segs = [{"start": 0.0, "end": 1.0, "text": "привет", "speaker": "SPEAKER_00"}]
    path = utils.save_segments_sidecar("abc123", segs, base_dir=str(tmp_path))
    assert path == os.path.join(str(tmp_path), "abc123.json")
    assert os.path.isfile(path)
    assert utils.load_segments_sidecar("abc123", base_dir=str(tmp_path)) == segs


def test_load_missing_returns_none(tmp_path):
    assert utils.load_segments_sidecar("nope", base_dir=str(tmp_path)) is None


def test_default_dir_is_voxnote_segments(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    utils.save_segments_sidecar("v1", [{"start": 0, "end": 1, "text": "x"}])
    assert os.path.isfile(tmp_path / ".voxnote" / "segments" / "v1.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_segments_sidecar.py -v`
Expected: FAIL with `AttributeError: module 'utils' has no attribute 'save_segments_sidecar'`.

- [ ] **Step 3: Write minimal implementation**

Append to `utils.py` (near the existing `save_segments` / `load_segments`):

```python
def _segments_sidecar_dir() -> str:
    """~/.voxnote/segments — SRT/VTT source data kept OUT of the vault. Home via
    USERPROFILE/HOME so tests can monkeypatch it (mirrors processing/store)."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "."
    return os.path.join(home, ".voxnote", "segments")


def save_segments_sidecar(
    voxnote_id: str, segments: list[dict], *, base_dir: str | None = None
) -> str:
    """Persist raw segments outside the vault for later SRT/VTT export, keyed by
    the meeting's voxnote_id. Atomic write. Returns the file path."""
    target_dir = base_dir or _segments_sidecar_dir()
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{voxnote_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_segments_sidecar(
    voxnote_id: str, *, base_dir: str | None = None
) -> list[dict] | None:
    """Read a sidecar by voxnote_id. None when absent or malformed."""
    target_dir = base_dir or _segments_sidecar_dir()
    path = os.path.join(target_dir, f"{voxnote_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_segments_sidecar.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add utils.py tests/test_segments_sidecar.py
git commit -m "feat(utils): segments sidecar (~/.voxnote/segments) for SRT/VTT export" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `processing/vault_note.py` — render + write `transcript.md`

**Files:**
- Create: `processing/vault_note.py`
- Test: `tests/test_processing_vault_note.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_processing_vault_note.py
import os

from directory.schema import Project
from processing import vault_note


def test_render_has_frontmatter_and_diarized_body():
    md = vault_note.render_transcript_note(
        segments=[{"start": 0, "end": 1, "text": "привет", "speaker": "SPEAKER_00"}],
        title="call", project_name="Kitng", date="2026-06-14", time="10:00",
        participants=[], provider="AssemblyAI", language="ru",
        voxnote_id="vid1", source_path="G:/My Drive/sources/call.m4a", nudged=True,
    )
    assert md.startswith("---\n")
    assert "type: meeting" in md
    assert "project: Kitng" in md
    assert 'source_path: "G:/My Drive/sources/call.m4a"' in md
    assert "nudged: true" in md
    assert "**Спикер 1:** привет" in md


def test_render_no_source_path_and_no_project():
    md = vault_note.render_transcript_note(
        segments=[], title="x", project_name=None, date="2026-06-14", time="09:00",
        participants=[], provider="Deepgram", language=None,
        voxnote_id="v", source_path=None, nudged=False,
    )
    assert 'source_path: ""' in md
    assert "project: \n" in md
    assert "nudged: false" in md


def test_write_creates_folder_and_transcript(tmp_path):
    p = vault_note.write_transcript_note(
        str(tmp_path), Project(name="Kitng", id="p1"),
        "2026-06-14_1000_call", "---\ntype: meeting\n---\nbody\n",
    )
    assert p == os.path.join(
        str(tmp_path), "Kitng", "2026-06-14_1000_call", "transcript.md"
    )
    assert os.path.isfile(p)


def test_write_no_project_uses_root(tmp_path):
    p = vault_note.write_transcript_note(str(tmp_path), None, "m", "x")
    assert p == os.path.join(str(tmp_path), "m", "transcript.md")


def test_write_collision_safe(tmp_path):
    vault_note.write_transcript_note(str(tmp_path), None, "m", "first")
    p2 = vault_note.write_transcript_note(str(tmp_path), None, "m", "second")
    assert p2 == os.path.join(str(tmp_path), "m-2", "transcript.md")
    assert open(p2, encoding="utf-8").read() == "second"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_processing_vault_note.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'processing.vault_note'`.

- [ ] **Step 3: Write minimal implementation**

```python
# processing/vault_note.py
"""Write the meeting folder's transcript.md into the Obsidian vault.

The ONLY VoxNote writer that touches the vault. One meeting = one folder under
<meetings_dir>/<project>/<meeting>/ holding transcript.md (VoxNote). Hermes later
adds protocol.md + the tasks file into the same folder. Audio never enters the
vault — transcript.md's frontmatter records its source_path in Drive.
"""
from __future__ import annotations

import os

from directory.schema import Project
from processing.layout import target_dir
from transcript_format import format_diarized_markdown


def _yaml_str(value: str) -> str:
    """Quote a value so ':' and Windows paths survive YAML; backslashes → '/'."""
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def render_transcript_note(
    *,
    segments: list[dict],
    title: str,
    project_name: str | None,
    date: str,
    time: str,
    participants: list[str],
    provider: str,
    language: str | None,
    voxnote_id: str,
    source_path: str | None,
    nudged: bool,
    speaker_map: dict[str, str] | None = None,
) -> str:
    """Render transcript.md = YAML frontmatter + diarized body. Pure, no I/O.
    ``title`` is accepted for symmetry/future use; the body is the diarized
    transcript and the meeting identity lives in the folder name."""
    sp_line = f"source_path: {_yaml_str(source_path)}" if source_path else 'source_path: ""'
    frontmatter = [
        "---",
        "type: meeting",
        f"date: {date}",
        f"time: {_yaml_str(time)}",
        f"project: {project_name or ''}",
        f"participants: [{', '.join(participants)}]",
        f"provider: {provider}",
        f"language: {language or ''}",
        f"voxnote_id: {voxnote_id}",
        sp_line,
        f"nudged: {'true' if nudged else 'false'}",
        "---",
        "",
    ]
    body = format_diarized_markdown(segments, speaker_map)
    return "\n".join(frontmatter) + body + "\n"


def write_transcript_note(
    meetings_dir: str, project: Project | None, meeting_name: str, content: str
) -> str:
    """Create <meetings_dir>/<project>/<meeting_name>/ (collision-safe folder) and
    write transcript.md inside (UTF-8, atomic). Returns the transcript.md path."""
    parent = target_dir(meetings_dir, project)
    os.makedirs(parent, exist_ok=True)
    folder = os.path.join(parent, meeting_name)
    n = 2
    while os.path.exists(folder):
        folder = os.path.join(parent, f"{meeting_name}-{n}")
        n += 1
    os.makedirs(folder)
    path = os.path.join(folder, "transcript.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_processing_vault_note.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite + ruff (no regressions; PR-A is additive)**

Run: `py -3 -m pytest -q` → expected exit 0.
Run: `py -3 -m ruff check .` → expected `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add processing/vault_note.py tests/test_processing_vault_note.py
git commit -m "feat(processing): vault_note — render + write the meeting transcript.md" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage (PR-A slice):**
- diarized Markdown body → Task 1. ✓
- audio → Drive `sources`, move/copy semantics, collision-safe → Task 2. ✓
- segments sidecar in `~/.voxnote/segments`, not vault → Task 3. ✓
- meeting folder + `transcript.md` with frontmatter (incl. `source_path`),
  no-project root, collision-safe → Task 4. ✓
- **Deferred to PR-B (by design):** model rework, store folder-scan, worker
  1-stage, inbox watcher, pre-flight, schema v1.1, speaker-count through
  `cli.core`. ✓

**2. Placeholder scan:** every code step carries complete code; no TODO/TBD. ✓

**3. Type consistency:** `format_diarized_markdown(segments, speaker_map=None)`
signature is identical in Task 1 and its use in Task 4's `render_transcript_note`;
`archive_audio(audio_path, sources_dir, base_name, *, move)`,
`save_segments_sidecar(voxnote_id, segments, *, base_dir=None)`,
`write_transcript_note(meetings_dir, project, meeting_name, content)` names are
consistent and match the spec's "Affected files"/component sections; segment dict
keys (`start/end/text/speaker`) match `transcript_format`. ✓

**Decision log:**
- **PR-A is additive-only** — narrower than the spec's PR-A (which also listed
  `model.py` + `store.build_view`). Rationale: `model`/`store`/`worker` all
  reference the 3-stage `StageStatus` fields, so reworking one breaks the others;
  they must move together to keep every commit green. That trio consolidates into
  **PR-B**. PR-A's modules are unused until PR-B (deliberate, independently tested
  — same pattern as PR-2a's unwired worker).
- **No YAML library** — frontmatter is rendered by hand (controlled fields) to
  avoid a new dependency (CLAUDE.md invariant #3). `_yaml_str` quotes values so
  `:` and Windows paths survive.
