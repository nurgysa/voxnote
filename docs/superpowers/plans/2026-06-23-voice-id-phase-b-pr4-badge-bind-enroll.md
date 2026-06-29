# Voice-ID Phase B — PR-4: «Встречи» badge + bind-and-enroll panel + playback + retroactive re-render — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Voice-ID loop — surface the new/unknown voices a finished Speechmatics meeting recorded into its `<id>.voiceid.json` sidecar as a «🆕 N новых голосов» badge in «Встречи», let the human listen and bind each voice to a directory person (or create one), enroll the Speechmatics identifier for future recognition, and retroactively re-render that meeting's `transcript.md` with the named speakers — no re-transcription.

**Architecture:** Three layers. (1) Pure, Tk-free helpers in `processing/voiceid.py` (rename segment labels → re-render via the canonical `vault_note` formatter; compute a preview audio window) + a `vault_note.overwrite_transcript_note` writer + a `utils.delete_voiceid_sidecar`. (2) `processing/store.py` gains `read_voxnote_id` (folder→sidecar bridge via transcript.md frontmatter) and `build_view` fills a new `QueueItem.pending_voices_count`. (3) UI: a new `ui/dialogs/voice_bind.py` Tk panel (snippet + «▶ Прослушать» + person dropdown per voice; «Применить» enrolls + re-renders + drains the sidecar) wired into `ui/dialogs/meetings.py` as a badge button. The whole feature stays dormant unless `voiceid_enabled` is on AND the provider is Speechmatics (those gates live in PR-3's worker).

**Tech Stack:** Python 3.12, stdlib + numpy/soundfile/sounddevice (already present), CustomTkinter for the panel. No new dependency.

## Global Constraints

(Copied verbatim from spec §8 — every task's requirements implicitly include this section.)

- **Invariant #2 unchanged** — pure cloud HTTPS / local file I/O; **no** torch / pyannote / faster-whisper / ctranslate2 / ONNX / local inference of any kind.
- **Invariant #3** — no `requirements.txt` pin changes; **no new dependency**.
- `encoding="utf-8"` on **every** text read/write (sidecars, note re-render, frontmatter parse).
- Narrow `except` only (`OSError`, `tk.TclError`, `DirectoryError`, `ValueError`, `sd.PortAudioError`). **No** broad `except Exception` anywhere in new code — the broad-except ratchet (`tests/test_broad_except_ratchet.py`) must stay flat.
- Russian user-facing strings; English code / comments / commit messages.
- **UI tests must be source-slice** — read the module's text and assert substrings. NEVER `import ui.app`, `ui.dialogs.meetings`, or `ui.dialogs.voice_bind` in a test (customtkinter → PortAudio → segfaults Linux CI). Pure logic goes in Tk-free modules (`processing/voiceid.py`, `processing/store.py`) and gets real unit tests.
- One concern per PR; **the user merges the PR** (push + open PR, do not merge to main).
- Commit messages lowercase-scoped (`feat(voiceid):` / `feat(queue):` / `test:`), ending with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Use `py -3` (Python 3.12) for `pytest` / `ruff`, never bare `python` (3.11).
- Test baseline (HEAD `16a653a`, after PR-3): **1085 passed / 2 skipped**. This PR adds ~25–35 tests. Verify the full count with `py -3 -m pytest --junitxml` (PowerShell+pytest pipes swallow the summary line).

---

## Context an implementer needs (read once)

**What PR-3 already produced** (do not rebuild it): when `voiceid_enabled` is on and the provider is Speechmatics, the queue worker writes a sidecar at `~/.voxnote/segments/<voxnote_id>.voiceid.json`:

```json
{
  "model": "<speechmatics model string>",
  "pending": [
    {"label": "SPEAKER_1", "identifier": "<opaque blob>", "sample_text": "первая реплика…", "first_start": 12.3}
  ],
  "note_meta": {
    "title": "...", "project_name": "Проект Альфа" | null, "date": "2026-06-29",
    "time": "10:15", "provider": "Speechmatics", "language": "ru",
    "voxnote_id": "<id>", "source_path": "<archived audio path>", "nudged": false
  }
}
```

- `pending[].label` is **already normalised** to `SPEAKER_N` form (`partition_speakers` did `S1`→`SPEAKER_1`). The entries are sorted by `first_start` and only include voices that carry an identifier.
- The segments for the meeting live in the sibling sidecar `~/.voxnote/segments/<voxnote_id>.json` (`utils.load_segments_sidecar`), each `{"start","end","text","speaker"}` where `speaker` is a real name (Speechmatics-identified) or `SPEAKER_N` (unknown).
- `note_meta` keys are **exactly** the non-`segments`/`participants`/`speaker_map` keyword params of `processing.vault_note.render_transcript_note`. That is the contract this PR relies on for the re-render.

**Existing helpers you will call (already merged):**
- `utils.load_voiceid_sidecar(voxnote_id, *, base_dir=None) -> dict | None`
- `utils.save_voiceid_sidecar(voxnote_id, payload, *, base_dir=None) -> str`
- `utils.load_segments_sidecar(voxnote_id, *, base_dir=None) -> list[dict] | None`
- `utils.plural_ru(n, one, few, many) -> str` (returns the WORD only)
- `directory.store.DirectoryStore.add_voiceprint(person_id, vp)` — appends, caps at 5
- `directory.store.DirectoryStore.upsert_person(person)` / `.people()` / `.get_person(id)`
- `directory.schema.Person`, `directory.schema.Voiceprint(identifier, model, provider="speechmatics", enrolled_at=…, source_meeting="")`
- `processing.vault_note.render_transcript_note(*, segments, title, project_name, date, time, participants, provider, language, voxnote_id, source_path, nudged, speaker_map=None) -> str`
- `audio_io.load_mono_float32(path) -> (np.ndarray, int)` — full-file decode; call OFF the Tk thread
- `processing.voiceid.partition_speakers(segments, speaker_identifiers, known_names)` — PR-3 helper; `_ANON_RE = re.compile(r"^SPEAKER_")`

**The `App` exposes `_dir_store`** (a loaded `DirectoryStore`) — the same store the worker reads via `resolve_known_speakers`. The bind panel must enroll into THIS store (not a fresh one) so future jobs see the new voiceprints. `MeetingsDialog` already reaches it as `self._app._dir_store` (see `_project_name`).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `processing/voiceid.py` | Pure Voice-ID queue helpers | **+** `participants_that_spoke`, `rename_segment_speakers`, `rerender_named_note`, `playback_window`; refactor `partition_speakers` to reuse `participants_that_spoke` |
| `processing/vault_note.py` | The vault transcript writer | **+** `overwrite_transcript_note(meeting_folder, content)` (in-place atomic overwrite; no new folder) |
| `utils.py` | App-data sidecars | **+** `delete_voiceid_sidecar(voxnote_id, *, base_dir=None)` |
| `processing/model.py` | `QueueItem` dataclass | **+** display field `pending_voices_count: int = 0` (to_dict / from_dict) |
| `processing/store.py` | Disk-derived «Встречи» view | **+** `read_voxnote_id(folder)`; `build_view` fills `pending_voices_count` for DONE rows |
| `ui/dialogs/voice_bind.py` | **NEW** bind-and-enroll Tk panel | one row per pending voice; «▶ Прослушать»; person dropdown + «+ создать»; «Применить» → enroll + re-render + drain sidecar |
| `ui/dialogs/meetings.py` | «Встречи» Tk renderer | **+** «🆕 N новых голосов» badge button on DONE rows with `pending_voices_count > 0` → opens `VoiceBindDialog`, refresh on apply |
| `tests/test_voiceid_rerender.py` | **NEW** | unit tests for the pure re-render + playback helpers + writer + delete |
| `tests/test_processing_store.py` | extend | `read_voxnote_id` + `pending_voices_count` |
| `tests/test_processing_model.py` *(create if absent)* | `pending_voices_count` round-trips | (or fold into an existing model test) |
| `tests/test_voice_bind_dialog.py` | **NEW** source-slice | panel wires the helpers/strings |
| `tests/test_meetings_dialog_queue.py` | extend source-slice | badge + panel wiring |

**Not touched** (already done in PR-3, do NOT duplicate): `config.example.json` `voiceid_enabled`, the Settings «Распознавание говорящих» checkbox, the worker, `ui/app` injection.

---

## Task 1: Pure re-render + playback helpers + writer + sidecar delete

**Files:**
- Modify: `processing/voiceid.py`
- Modify: `processing/vault_note.py`
- Modify: `utils.py`
- Test: `tests/test_voiceid_rerender.py` (create)

**Interfaces:**
- Consumes: `processing.vault_note.render_transcript_note` (existing); `processing.voiceid._ANON_RE` (existing).
- Produces (later tasks rely on these exact signatures):
  - `processing.voiceid.participants_that_spoke(segments: list[dict]) -> list[str]`
  - `processing.voiceid.rename_segment_speakers(segments: list[dict], names_by_label: dict[str, str]) -> list[dict]`
  - `processing.voiceid.rerender_named_note(segments: list[dict], names_by_label: dict[str, str], note_meta: dict) -> str`
  - `processing.voiceid.playback_window(n_samples: int, sample_rate: int, first_start: float, window_s: float = 6.0) -> tuple[int, int]`
  - `processing.vault_note.overwrite_transcript_note(meeting_folder: str, content: str) -> str`
  - `utils.delete_voiceid_sidecar(voxnote_id: str, *, base_dir: str | None = None) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voiceid_rerender.py`:

```python
"""Pure helpers for PR-4 retroactive re-render + preview playback window.

Tk-free / network-free — exercises processing.voiceid + vault_note + utils
directly so the bind panel's logic is proven without importing any UI.
"""
from __future__ import annotations

import os

from processing import vault_note
from processing.voiceid import (
    participants_that_spoke,
    playback_window,
    rename_segment_speakers,
    rerender_named_note,
)
from utils import delete_voiceid_sidecar, load_voiceid_sidecar, save_voiceid_sidecar

_SEGMENTS = [
    {"start": 0.0, "end": 1.0, "text": "привет", "speaker": "SPEAKER_1"},
    {"start": 1.0, "end": 2.0, "text": "здравствуйте", "speaker": "SPEAKER_2"},
    {"start": 2.0, "end": 3.0, "text": "как дела", "speaker": "SPEAKER_1"},
]

_NOTE_META = {
    "title": "Планёрка",
    "project_name": "Проект Альфа",
    "date": "2026-06-29",
    "time": "10:15",
    "provider": "Speechmatics",
    "language": "ru",
    "voxnote_id": "vid-1",
    "source_path": "C:/audio/planerka.m4a",
    "nudged": False,
}


def test_participants_that_spoke_excludes_anonymous_and_sorts():
    segs = [
        {"speaker": "SPEAKER_2", "text": "a"},
        {"speaker": "Борис Ким", "text": "b"},
        {"speaker": "Айбек Нурланов", "text": "c"},
        {"speaker": "Айбек Нурланов", "text": "d"},  # dup ignored
        {"speaker": "", "text": "e"},                # blank ignored
    ]
    assert participants_that_spoke(segs) == ["Айбек Нурланов", "Борис Ким"]


def test_rename_segment_speakers_is_nondestructive():
    out = rename_segment_speakers(_SEGMENTS, {"SPEAKER_1": "Айбек Нурланов"})
    assert out[0]["speaker"] == "Айбек Нурланов"
    assert out[1]["speaker"] == "SPEAKER_2"      # untouched
    assert _SEGMENTS[0]["speaker"] == "SPEAKER_1"  # original not mutated


def test_rerender_named_note_partial_naming():
    content = rerender_named_note(
        _SEGMENTS, {"SPEAKER_1": "Айбек Нурланов"}, _NOTE_META
    )
    # frontmatter participants = only the named person who spoke
    assert 'participants: ["Айбек Нурланов"]' in content
    # body: named speaker verbatim, remaining anonymous renumbered to «Спикер 1»
    assert "**Айбек Нурланов:** привет как дела" in content
    assert "**Спикер 1:** здравствуйте" in content
    # «Связи» links project + the named participant
    assert "## Связи" in content
    assert "[[Айбек Нурланов]]" in content
    assert "[[Проект Альфа]]" in content


def test_rerender_named_note_all_named():
    content = rerender_named_note(
        _SEGMENTS,
        {"SPEAKER_1": "Айбек Нурланов", "SPEAKER_2": "Борис Ким"},
        _NOTE_META,
    )
    assert 'participants: ["Айбек Нурланов", "Борис Ким"]' in content
    assert "SPEAKER_" not in content
    assert "Спикер" not in content  # no anonymous left


def test_playback_window_clamps():
    sr = 16000
    # window inside the audio
    assert playback_window(160000, sr, 1.0, window_s=2.0) == (16000, 48000)
    # start past the end → empty slice
    assert playback_window(16000, sr, 100.0, window_s=2.0) == (16000, 16000)
    # window tail clamps to n_samples
    assert playback_window(20000, sr, 1.0, window_s=10.0) == (16000, 20000)
    # degenerate inputs
    assert playback_window(0, sr, 1.0) == (0, 0)
    assert playback_window(16000, 0, 1.0) == (0, 0)


def test_overwrite_transcript_note_replaces_in_place(tmp_path):
    folder = tmp_path / "meeting"
    folder.mkdir()
    note = folder / "transcript.md"
    note.write_text("OLD", encoding="utf-8")
    path = vault_note.overwrite_transcript_note(str(folder), "НОВЫЙ текст")
    assert os.path.normpath(path) == os.path.normpath(str(note))
    assert note.read_text(encoding="utf-8") == "НОВЫЙ текст"
    # no stray temp file left behind
    assert not (folder / "transcript.md.tmp").exists()


def test_delete_voiceid_sidecar(tmp_path):
    save_voiceid_sidecar("vid-x", {"pending": [{"label": "SPEAKER_1"}]}, base_dir=str(tmp_path))
    assert load_voiceid_sidecar("vid-x", base_dir=str(tmp_path)) is not None
    delete_voiceid_sidecar("vid-x", base_dir=str(tmp_path))
    assert load_voiceid_sidecar("vid-x", base_dir=str(tmp_path)) is None
    # idempotent — deleting an absent sidecar is a no-op, not an error
    delete_voiceid_sidecar("vid-x", base_dir=str(tmp_path))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_voiceid_rerender.py -q`
Expected: FAIL — `ImportError` for `participants_that_spoke` / `rerender_named_note` / `playback_window` / `overwrite_transcript_note` / `delete_voiceid_sidecar`.

- [ ] **Step 3: Add the pure helpers to `processing/voiceid.py`**

At the top of `processing/voiceid.py`, add the `vault_note` import (it is Tk-free: `vault_note` only imports `directory.schema`, `processing.layout`, `transcript_format`):

```python
from processing import vault_note
```

Refactor `partition_speakers` to reuse a new shared scanner, and add the new helpers. Replace the existing participant-scan block inside `partition_speakers`:

```python
    spoke: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        sp = seg.get("speaker")
        if sp and not _ANON_RE.match(sp) and sp not in seen:
            seen.add(sp)
            spoke.append(sp)
    participants = sorted(spoke)
```

with a call to the extracted helper:

```python
    participants = participants_that_spoke(segments)
```

Then add these functions to the module (after `partition_speakers`):

```python
def participants_that_spoke(segments: list[dict]) -> list[str]:
    """Sorted unique non-anonymous speaker names that actually appear in
    ``segments`` (labels that are neither ``SPEAKER_*`` nor blank). Shared by
    partition_speakers (the identified set) and the retroactive re-render."""
    spoke: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        sp = seg.get("speaker")
        if sp and not _ANON_RE.match(sp) and sp not in seen:
            seen.add(sp)
            spoke.append(sp)
    return sorted(spoke)


def rename_segment_speakers(
    segments: list[dict], names_by_label: dict[str, str]
) -> list[dict]:
    """A shallow copy of ``segments`` with every ``speaker`` label present in
    ``names_by_label`` replaced by the chosen ФИО; other labels untouched.
    Non-destructive — the inputs are the persisted sidecar and must not mutate."""
    out: list[dict] = []
    for seg in segments:
        s = dict(seg)
        label = s.get("speaker")
        if label in names_by_label:
            s["speaker"] = names_by_label[label]
        out.append(s)
    return out


def rerender_named_note(
    segments: list[dict], names_by_label: dict[str, str], note_meta: dict
) -> str:
    """Re-render a meeting's transcript.md content after naming some voices.

    Applies ``names_by_label`` (``SPEAKER_n`` -> ФИО) to the segments, recomputes
    ``participants`` from the renamed segments (newly named + already-identified
    people who spoke; still-anonymous ``SPEAKER_*`` excluded), and renders via the
    canonical vault_note formatter. No ``speaker_map`` is passed, so any voices
    left unnamed renumber cleanly to «Спикер N». ``note_meta`` carries the
    non-segment render kwargs persisted in the sidecar; its keys MUST match
    render_transcript_note's remaining keyword params."""
    renamed = rename_segment_speakers(segments, names_by_label)
    participants = participants_that_spoke(renamed)
    return vault_note.render_transcript_note(
        segments=renamed,
        participants=participants,
        **note_meta,
    )


def playback_window(
    n_samples: int, sample_rate: int, first_start: float, window_s: float = 6.0
) -> tuple[int, int]:
    """[start_idx, end_idx) sample slice for a preview ``window_s`` seconds long
    starting at ``first_start`` (clamped to the audio). Returns an empty slice
    (start == end) for empty audio, a non-positive sample rate, or a start past
    the end."""
    if n_samples <= 0 or sample_rate <= 0:
        return 0, 0
    start = max(0, min(int(first_start * sample_rate), n_samples))
    end = min(n_samples, start + int(window_s * sample_rate))
    return start, end
```

- [ ] **Step 4: Add `overwrite_transcript_note` to `processing/vault_note.py`**

Append after `write_transcript_note`:

```python
def overwrite_transcript_note(meeting_folder: str, content: str) -> str:
    """Atomically overwrite ``<meeting_folder>/transcript.md`` with ``content``
    (UTF-8). The Voice-ID retroactive re-render reuses an existing meeting folder,
    so — unlike write_transcript_note — this never creates a new collision-safe
    folder. Returns the transcript.md path."""
    path = os.path.join(meeting_folder, "transcript.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    return path
```

- [ ] **Step 5: Add `delete_voiceid_sidecar` to `utils.py`**

Append after `load_voiceid_sidecar` (keeps the sidecar trio together):

```python
def delete_voiceid_sidecar(voxnote_id: str, *, base_dir: str | None = None) -> None:
    """Remove the Voice-ID sidecar for ``voxnote_id`` once every pending voice is
    resolved (the «🆕 новые голоса» badge then clears). Idempotent — a missing
    file is a no-op, not an error."""
    target_dir = base_dir or _segments_sidecar_dir()
    path = os.path.join(target_dir, f"{voxnote_id}.voiceid.json")
    try:
        os.remove(path)
    except OSError:
        pass
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_voiceid_rerender.py tests/test_voiceid_partition.py -q`
Expected: PASS (the partition refactor changes no behavior — `test_voiceid_partition.py` is the safety net).

- [ ] **Step 7: Lint**

Run: `py -3 -m ruff check processing/voiceid.py processing/vault_note.py utils.py tests/test_voiceid_rerender.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add processing/voiceid.py processing/vault_note.py utils.py tests/test_voiceid_rerender.py
git commit -F .cache/commit_pr4_task1.txt
```
Commit message (write to `.cache/commit_pr4_task1.txt` first):
```
feat(voiceid): pure re-render + preview-window helpers for PR-4

Add the Tk-free building blocks the bind panel needs:
- participants_that_spoke / rename_segment_speakers / rerender_named_note
  (retro re-render via the canonical vault_note formatter; partition_speakers
  refactored to share the participant scan)
- playback_window (clamped preview slice)
- vault_note.overwrite_transcript_note (in-place atomic overwrite)
- utils.delete_voiceid_sidecar (drain the badge when no voices remain)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 2: `read_voxnote_id` bridge + `build_view` pending-voices count + `QueueItem` field

**Files:**
- Modify: `processing/model.py`
- Modify: `processing/store.py`
- Test: `tests/test_processing_store.py` (extend); `tests/test_processing_model.py` (create)

**Interfaces:**
- Consumes: `utils.load_voiceid_sidecar` (existing); `processing.model.StageStatus` (existing).
- Produces:
  - `processing.model.QueueItem.pending_voices_count: int = 0`
  - `processing.store.read_voxnote_id(folder: str) -> str | None`
  - `build_view(...)` now sets `row.pending_voices_count` for every DONE row whose folder's transcript.md frontmatter `voxnote_id` resolves a sidecar.

- [ ] **Step 1: Write the failing model test**

Create `tests/test_processing_model.py`:

```python
"""QueueItem round-trip incl. the PR-4 disk-derived display field."""
from __future__ import annotations

from processing.model import QueueItem


def test_pending_voices_count_round_trips():
    item = QueueItem(id="x", audio_path="", title="t", created_at="", pending_voices_count=3)
    assert QueueItem.from_dict(item.to_dict()).pending_voices_count == 3


def test_pending_voices_count_defaults_zero_for_legacy_dict():
    # A queue.json written before PR-4 has no key → default 0.
    item = QueueItem.from_dict({"id": "x"})
    assert item.pending_voices_count == 0
```

- [ ] **Step 2: Write the failing store tests**

Add to `tests/test_processing_store.py` (imports `read_voxnote_id`, `build_view`; uses `monkeypatch` to point the sidecar dir at tmp via the `USERPROFILE`/`HOME` env the segments dir reads — match how the file's existing tests set up `meetings_dir`; if the existing tests already monkeypatch the home, reuse that fixture):

```python
from processing.store import build_view, read_voxnote_id


def _write_meeting(meetings_dir, folder_name, voxnote_id):
    folder = meetings_dir / folder_name
    folder.mkdir(parents=True)
    (folder / "transcript.md").write_text(
        "---\n"
        "type: meeting\n"
        f"voxnote_id: {voxnote_id}\n"
        "---\n\n"
        "**Спикер 1:** привет\n",
        encoding="utf-8",
    )
    return folder


def test_read_voxnote_id_from_frontmatter(tmp_path):
    folder = _write_meeting(tmp_path, "m1", "vid-42")
    assert read_voxnote_id(str(folder)) == "vid-42"


def test_read_voxnote_id_none_when_absent(tmp_path):
    folder = tmp_path / "m2"
    folder.mkdir()
    (folder / "transcript.md").write_text("no frontmatter here\n", encoding="utf-8")
    assert read_voxnote_id(str(folder)) is None
    # missing file
    assert read_voxnote_id(str(tmp_path / "nope")) is None


def test_build_view_fills_pending_voices_count(tmp_path, monkeypatch):
    meetings_dir = tmp_path / "meetings"
    folder = _write_meeting(meetings_dir, "m1", "vid-99")
    # Point the segments/voiceid sidecar dir at tmp via env (mirrors utils._segments_sidecar_dir)
    seg_home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(seg_home))
    monkeypatch.setenv("HOME", str(seg_home))
    from utils import save_voiceid_sidecar
    save_voiceid_sidecar("vid-99", {"pending": [{"label": "SPEAKER_1"}, {"label": "SPEAKER_2"}]})

    rows = build_view(str(meetings_dir), [])
    row = next(r for r in rows if r.meeting_folder and r.meeting_folder.endswith("m1"))
    assert row.pending_voices_count == 2


def test_build_view_zero_when_no_sidecar(tmp_path):
    meetings_dir = tmp_path / "meetings"
    _write_meeting(meetings_dir, "m1", "vid-empty")
    rows = build_view(str(meetings_dir), [])
    row = next(r for r in rows if r.meeting_folder and r.meeting_folder.endswith("m1"))
    assert row.pending_voices_count == 0
```

> Note for the implementer: if `tests/test_processing_store.py` already has a fixture that sets `USERPROFILE`/`HOME` or a sidecar base, reuse it rather than re-setting env. The key requirement is that `utils.save_voiceid_sidecar(voxnote_id, …)` and `build_view`'s `load_voiceid_sidecar` resolve the SAME directory.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_processing_model.py tests/test_processing_store.py -q`
Expected: FAIL — `pending_voices_count` attribute / `read_voxnote_id` import missing.

- [ ] **Step 4: Add the `QueueItem` field**

In `processing/model.py`, add after `has_tasks` in the dataclass body:

```python
    pending_voices_count: int = 0   # display: unnamed Voice-ID voices awaiting binding (build_view fills it)
```

Add to `to_dict` (after `"has_tasks"`):

```python
            "pending_voices_count": self.pending_voices_count,
```

Add to `from_dict` (after `has_tasks=...`):

```python
            pending_voices_count=int(d.get("pending_voices_count", 0) or 0),
```

- [ ] **Step 5: Add `read_voxnote_id` + wire `build_view` in `processing/store.py`**

Extend the utils import at the top of `processing/store.py`:

```python
from utils import load_speakers, load_voiceid_sidecar
```

Add the bridge helper (place it near `meeting_status_from_folder`):

```python
def read_voxnote_id(folder: str) -> str | None:
    """The meeting's voxnote_id from its transcript.md YAML frontmatter, or None
    when absent/unreadable. Reads only the frontmatter head (stops at the closing
    '---'). This bridges a disk meeting row back to its
    ~/.voxnote/segments/<id>.* sidecars (the original queue id is not on disk)."""
    path = os.path.join(folder, "transcript.md")
    try:
        with open(path, encoding="utf-8") as f:
            if f.readline().strip() != "---":
                return None  # no frontmatter block
            for line in f:
                if line.strip() == "---":
                    break
                if line.startswith("voxnote_id:"):
                    return line.split(":", 1)[1].strip() or None
    except OSError:
        return None
    return None
```

In `build_view`, replace the final `return rows` with a pass that fills the count for finished rows first:

```python
    for row in rows:
        if row.status == StageStatus.DONE and row.meeting_folder:
            vid = read_voxnote_id(row.meeting_folder)
            if vid:
                sidecar = load_voiceid_sidecar(vid)
                if sidecar:
                    row.pending_voices_count = len(sidecar.get("pending", []))
    return rows
```

> Why this runs on the final rows (after the active-item overlay): both disk rows and overlaid active items carry `meeting_folder`, and the frontmatter `voxnote_id` is the single source of truth that survives a restart (DONE active items are dropped on load). Reading only DONE rows and only the frontmatter head keeps the per-tick cost small.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_processing_model.py tests/test_processing_store.py -q`
Expected: PASS.

- [ ] **Step 7: Lint**

Run: `py -3 -m ruff check processing/model.py processing/store.py tests/test_processing_model.py tests/test_processing_store.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add processing/model.py processing/store.py tests/test_processing_model.py tests/test_processing_store.py
git commit -F .cache/commit_pr4_task2.txt
```
Message (`.cache/commit_pr4_task2.txt`):
```
feat(queue): surface pending-voice count per finished meeting

build_view now resolves each DONE meeting's voxnote_id from its transcript.md
frontmatter (read_voxnote_id) and counts the unnamed voices in its voiceid
sidecar into a new QueueItem.pending_voices_count display field. This drives the
«🆕 N новых голосов» badge in «Встречи» and is restart-safe (the original queue
id is recovered from disk, not memory).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 3: Bind-and-enroll panel (`ui/dialogs/voice_bind.py`)

**Files:**
- Create: `ui/dialogs/voice_bind.py`
- Test: `tests/test_voice_bind_dialog.py` (create, source-slice)

**Interfaces:**
- Consumes: `processing.voiceid.rerender_named_note` / `playback_window`; `processing.vault_note.overwrite_transcript_note`; `processing.store.read_voxnote_id`; `utils.{load_voiceid_sidecar,save_voiceid_sidecar,delete_voiceid_sidecar,load_segments_sidecar}`; `audio_io.load_mono_float32`; `directory.schema.{Person,Voiceprint}`; `directory.store.DirectoryError`; the app's `_dir_store`.
- Produces: `class VoiceBindDialog(ctk.CTkToplevel)` with `__init__(self, parent, app, item, on_applied)`.

- [ ] **Step 1: Write the failing source-slice test**

Create `tests/test_voice_bind_dialog.py`:

```python
"""Source-slice wiring tests for the Voice-ID bind-and-enroll panel.

No ui import — customtkinter pulls PortAudio and crashes Linux CI. We assert the
module text wires the pure helpers + store calls + the pinned Russian strings.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = (_ROOT / "ui" / "dialogs" / "voice_bind.py").read_text(encoding="utf-8")


def test_panel_class_and_signature():
    assert "class VoiceBindDialog(ctk.CTkToplevel)" in _SRC
    assert "def __init__(self, parent, app, item, on_applied)" in _SRC


def test_loads_sidecar_and_segments():
    assert "load_voiceid_sidecar(" in _SRC
    assert "load_segments_sidecar(" in _SRC
    assert "read_voxnote_id(" in _SRC


def test_enroll_and_rerender_wired():
    assert "add_voiceprint(" in _SRC
    assert "Voiceprint(" in _SRC
    assert "rerender_named_note(" in _SRC
    assert "overwrite_transcript_note(" in _SRC


def test_drains_sidecar_on_apply():
    # remaining → save, empty → delete (badge clears)
    assert "save_voiceid_sidecar(" in _SRC
    assert "delete_voiceid_sidecar(" in _SRC
    assert "self._on_applied(" in _SRC


def test_playback_uses_window_helper_off_thread():
    assert "playback_window(" in _SRC
    assert "load_mono_float32(" in _SRC
    assert "import sounddevice" in _SRC
    assert "threading.Thread(" in _SRC  # decode + playback never block Tk


def test_create_person_path():
    assert "+ создать нового" in _SRC
    assert "CTkInputDialog" in _SRC
    assert "upsert_person(" in _SRC


def test_pinned_russian_strings():
    assert "▶ Прослушать" in _SRC
    assert "Применить" in _SRC
    assert "Распознавание голосов" in _SRC


def test_no_broad_except():
    assert "except Exception" not in _SRC
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `py -3 -m pytest tests/test_voice_bind_dialog.py -q`
Expected: FAIL — `voice_bind.py` does not exist (FileNotFoundError on read).

- [ ] **Step 3: Create `ui/dialogs/voice_bind.py`**

```python
"""Voice-ID bind-and-enroll panel — name the new voices a finished Speechmatics
meeting recorded, enroll their identifiers for future recognition, and retro-
rewrite the meeting's transcript.md with the named speakers (no re-transcription).

Opened from «Встречи» when a row shows «🆕 N новых голосов». Pure logic lives in
processing.voiceid / vault_note (unit-tested); this is the thin Tk renderer.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import sounddevice as sd

from audio_io import load_mono_float32
from directory.schema import Person, Voiceprint
from directory.store import DirectoryError
from processing import vault_note
from processing.store import read_voxnote_id
from processing.voiceid import playback_window, rerender_named_note
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    FONT,
    INPUT_BG,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
)
from utils import (
    delete_voiceid_sidecar,
    load_segments_sidecar,
    load_voiceid_sidecar,
    save_voiceid_sidecar,
)

_UNSET_LABEL = "— выбрать —"
_CREATE_LABEL = "+ создать нового…"
_PREVIEW_WINDOW_S = 6.0


class VoiceBindDialog(ctk.CTkToplevel):
    """One row per unknown voice: sample snippet + «▶ Прослушать» + person
    dropdown. «Применить» enrolls each named voice, retro-renders this meeting's
    transcript.md, and drains the resolved entries from the sidecar."""

    def __init__(self, parent, app, item, on_applied):
        super().__init__(parent)
        self.title("Распознавание голосов")
        self.geometry("680x560")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._app = app
        self._item = item
        self._on_applied = on_applied
        self._store = getattr(app, "_dir_store", None)
        self._samples = None          # lazily decoded preview audio (np.ndarray)
        self._sample_rate = 0
        self._menus: list[ctk.CTkOptionMenu] = []
        self._rows: list[tuple[dict, ctk.StringVar]] = []  # (pending entry, choice var)
        self._person_by_name: dict[str, Person] = {}

        folder = item.meeting_folder or ""
        self._voxnote_id = read_voxnote_id(folder) or ""
        sidecar = load_voiceid_sidecar(self._voxnote_id) if self._voxnote_id else None
        if self._store is None or not sidecar or not sidecar.get("pending"):
            messagebox.showinfo(
                "Распознавание голосов",
                "Нет новых голосов для привязки.",
                parent=self,
            )
            self.after(0, self._close)
            return

        self._model = sidecar.get("model", "")
        self._note_meta = sidecar.get("note_meta", {})
        self._pending = list(sidecar["pending"])
        self._source_path = self._note_meta.get("source_path") or ""

        self._build_ui()

    # ── build ──
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text="Новые голоса — кто это говорит?",
            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        body = ctk.CTkScrollableFrame(self, fg_color=SURFACE, corner_radius=12)
        body.grid(row=1, column=0, padx=16, pady=8, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        self._refresh_people_index()
        for i, entry in enumerate(self._pending):
            self._build_voice_row(body, i, entry)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=16, pady=(4, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="Отмена", width=110, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._close,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Применить", width=150, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._apply,
        ).grid(row=0, column=1, sticky="e")

    def _build_voice_row(self, parent, idx: int, entry: dict) -> None:
        row = ctk.CTkFrame(parent, fg_color=SURFACE_BRIGHT, corner_radius=10)
        row.grid(row=idx, column=0, padx=4, pady=4, sticky="ew")
        row.grid_columnconfigure(0, weight=1)

        snippet = (entry.get("sample_text") or "").strip() or "(без текста)"
        if len(snippet) > 140:
            snippet = snippet[:140] + "…"
        ctk.CTkLabel(
            row, text=snippet, anchor="w", justify="left", wraplength=600,
            font=ctk.CTkFont(family=FONT, size=12), text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(8, 2), sticky="ew")

        ctk.CTkButton(
            row, text="▶ Прослушать", width=130, height=32, corner_radius=16,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=lambda fs=float(entry.get("first_start", 0.0)): self._play(fs),
        ).grid(row=1, column=0, padx=12, pady=(0, 10), sticky="w")

        var = ctk.StringVar(value=_UNSET_LABEL)
        menu = ctk.CTkOptionMenu(
            row, variable=var, values=self._menu_values(), width=260,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, button_color=BLUE, button_hover_color=BLUE_DIM,
            command=lambda choice, v=var: self._on_choice(choice, v),
        )
        menu.grid(row=1, column=1, padx=12, pady=(0, 10), sticky="e")
        self._menus.append(menu)
        self._rows.append((entry, var))

    # ── people dropdown ──
    def _refresh_people_index(self) -> None:
        self._person_by_name = {p.full_name: p for p in self._store.people()}

    def _menu_values(self) -> list[str]:
        return [_UNSET_LABEL, *sorted(self._person_by_name), _CREATE_LABEL]

    def _on_choice(self, choice: str, var: ctk.StringVar) -> None:
        if choice != _CREATE_LABEL:
            return
        name = ctk.CTkInputDialog(
            text="ФИО нового человека", title="Новый человек",
        ).get_input()
        name = (name or "").strip()
        if not name:
            var.set(_UNSET_LABEL)
            return
        try:
            self._store.upsert_person(Person(full_name=name))
        except DirectoryError as exc:
            messagebox.showerror(
                "Распознавание голосов",
                f"Не удалось создать человека:\n\n{exc}", parent=self,
            )
            var.set(_UNSET_LABEL)
            return
        self._refresh_people_index()
        for menu in self._menus:
            menu.configure(values=self._menu_values())
        var.set(name)

    # ── playback ──
    def _play(self, first_start: float) -> None:
        if self._samples is not None:
            self._play_slice(first_start)
            return
        if not (self._source_path and os.path.isfile(self._source_path)):
            messagebox.showinfo(
                "Распознавание голосов",
                "Аудиофайл недоступен — прослушать не получится, но привязать "
                "голос всё равно можно.",
                parent=self,
            )
            return
        threading.Thread(
            target=lambda: self._decode_then_play(first_start), daemon=True
        ).start()

    def _decode_then_play(self, first_start: float) -> None:
        try:
            samples, sr = load_mono_float32(self._source_path)
        except (OSError, RuntimeError, ValueError):
            self.after(0, lambda: self._toast_audio_error())
            return
        self._samples, self._sample_rate = samples, sr
        self.after(0, lambda: self._play_slice(first_start))

    def _play_slice(self, first_start: float) -> None:
        if self._samples is None or self._sample_rate <= 0:
            return
        start, end = playback_window(
            len(self._samples), self._sample_rate, first_start, _PREVIEW_WINDOW_S
        )
        if end <= start:
            return
        clip = self._samples[start:end]

        def _run():
            try:
                sd.play(clip, samplerate=self._sample_rate)
                sd.wait()
            except (sd.PortAudioError, ValueError):
                pass  # device gone / bad format — preview just stops

        threading.Thread(target=_run, daemon=True).start()

    def _toast_audio_error(self) -> None:
        messagebox.showinfo(
            "Распознавание голосов",
            "Не удалось прочитать аудио для прослушивания.", parent=self,
        )

    # ── apply ──
    def _apply(self) -> None:
        names_by_label: dict[str, str] = {}
        enroll: list[tuple[str, str]] = []  # (person_id, identifier)
        for entry, var in self._rows:
            name = var.get()
            if name in (_UNSET_LABEL, _CREATE_LABEL, ""):
                continue
            person = self._person_by_name.get(name)
            if person is None:
                continue
            names_by_label[entry["label"]] = person.full_name
            if entry.get("identifier"):
                enroll.append((person.id, entry["identifier"]))
        if not names_by_label:
            self._close()
            return

        # 1) enroll identifiers for future recognition
        for person_id, identifier in enroll:
            try:
                self._store.add_voiceprint(person_id, Voiceprint(
                    identifier=identifier, model=self._model,
                    provider="speechmatics", source_meeting=self._voxnote_id,
                ))
            except DirectoryError as exc:
                messagebox.showerror(
                    "Распознавание голосов",
                    f"Не удалось сохранить голос:\n\n{exc}", parent=self,
                )
                return

        # 2) retroactive re-render of THIS meeting's transcript.md
        segments = load_segments_sidecar(self._voxnote_id) or []
        if segments and self._item.meeting_folder:
            content = rerender_named_note(segments, names_by_label, self._note_meta)
            try:
                vault_note.overwrite_transcript_note(self._item.meeting_folder, content)
            except OSError as exc:
                messagebox.showerror(
                    "Распознавание голосов",
                    f"Не удалось перезаписать заметку:\n\n{exc}", parent=self,
                )
                return

        # 3) drain resolved entries; clear the sidecar (and badge) when empty
        remaining = [e for e in self._pending if e["label"] not in names_by_label]
        if remaining:
            save_voiceid_sidecar(self._voxnote_id, {
                "model": self._model,
                "pending": remaining,
                "note_meta": self._note_meta,
            })
        else:
            delete_voiceid_sidecar(self._voxnote_id)

        self._on_applied()
        self._close()

    def _close(self) -> None:
        try:
            sd.stop()
        except (sd.PortAudioError, ValueError):
            pass
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
```

- [ ] **Step 4: Run the source-slice test to verify it passes**

Run: `py -3 -m pytest tests/test_voice_bind_dialog.py -q`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `py -3 -m ruff check ui/dialogs/voice_bind.py tests/test_voice_bind_dialog.py`
Expected: clean. (If ruff flags `tk` as unused, confirm it IS used in `except tk.TclError`; keep the import.)

- [ ] **Step 6: Commit**

```bash
git add ui/dialogs/voice_bind.py tests/test_voice_bind_dialog.py
git commit -F .cache/commit_pr4_task3.txt
```
Message (`.cache/commit_pr4_task3.txt`):
```
feat(voiceid): bind-and-enroll panel for new voices

VoiceBindDialog lists each unknown voice from a meeting's voiceid sidecar with a
sample snippet, «▶ Прослушать» (windowed preview off the Tk thread), and a person
dropdown (existing or «+ создать нового…»). «Применить» enrolls each named voice's
Speechmatics identifier, retro-renders the meeting's transcript.md with the names,
and drains the resolved entries from the sidecar (deleting it when empty so the
badge clears). Tk renderer over the pure processing.voiceid helpers.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 4: «Встречи» badge wiring (`ui/dialogs/meetings.py`)

**Files:**
- Modify: `ui/dialogs/meetings.py`
- Test: `tests/test_meetings_dialog_queue.py` (extend, source-slice)

**Interfaces:**
- Consumes: `QueueItem.pending_voices_count` (Task 2); `ui.dialogs.voice_bind.VoiceBindDialog` (Task 3); `utils.plural_ru` (existing).
- Produces: a badge button on DONE rows + `MeetingsDialog._bind_voices(item)`.

- [ ] **Step 1: Write the failing source-slice tests**

Add to `tests/test_meetings_dialog_queue.py`:

```python
def test_meetings_renders_new_voices_badge():
    assert "pending_voices_count" in _MEET
    assert "🆕" in _MEET
    assert "новых голос" in _MEET  # plural_ru stem for the badge label


def test_meetings_badge_opens_bind_panel():
    assert "_bind_voices" in _MEET
    assert "VoiceBindDialog(" in _MEET
    assert "on_applied=self._render" in _MEET  # apply refreshes the list (badge clears)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_meetings_dialog_queue.py -q`
Expected: FAIL on the two new tests.

- [ ] **Step 3: Wire the badge into `ui/dialogs/meetings.py`**

Extend the utils import (currently `from utils import delete_history_entry, get_meetings_dir, open_in_explorer, save_transcript`):

```python
from utils import (
    delete_history_entry,
    get_meetings_dir,
    open_in_explorer,
    plural_ru,
    save_transcript,
)
```

In `_build_row`, inside the `if item.status == StageStatus.DONE and item.meeting_folder:` branch, insert the badge button as the FIRST action (before the `👁 Просмотр` button), so it leads the row:

```python
        if item.status == StageStatus.DONE and item.meeting_folder:
            if item.pending_voices_count > 0:
                n = item.pending_voices_count
                word = plural_ru(n, "новый голос", "новых голоса", "новых голосов")
                ctk.CTkButton(
                    row, text=f"🆕 {n} {word}", width=150, height=32, corner_radius=16,
                    font=ctk.CTkFont(family=FONT, size=12, weight="bold"),
                    fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
                    command=lambda it=item: self._bind_voices(it),
                ).grid(row=0, column=col, rowspan=2, padx=(8, 4), pady=6)
                col += 1
            ctk.CTkButton(
                row, text="👁 Просмотр", width=110, height=32, corner_radius=16,
                ...
```

(Leave the rest of the DONE branch — Просмотр / Obsidian / ✕ — unchanged; they continue from the now-incremented `col`.)

Add the handler in the «── actions ──» section (lazy import keeps `sounddevice` out of meetings.py import time):

```python
    def _bind_voices(self, item):
        from ui.dialogs.voice_bind import VoiceBindDialog
        VoiceBindDialog(self, self._app, item, on_applied=self._render)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_meetings_dialog_queue.py -q`
Expected: PASS (incl. the pre-existing `test_meetings_preserves_legacy_pinned_strings`).

- [ ] **Step 5: Full suite + lint**

Run: `py -3 -m pytest --junitxml=.cache/pr4_junit.xml` then read the `<testsuite>` element (PowerShell+pytest pipes swallow the summary).
Expected: 0 failures / 0 errors; total = 1085 + the new tests.
Run: `py -3 -m ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add ui/dialogs/meetings.py tests/test_meetings_dialog_queue.py
git commit -F .cache/commit_pr4_task4.txt
```
Message (`.cache/commit_pr4_task4.txt`):
```
feat(queue): «🆕 N новых голосов» badge opens the bind panel

A finished meeting with unnamed Voice-ID voices now leads its «Встречи» row with
a badge button (pluralised count) that opens VoiceBindDialog; applying refreshes
the list so the badge clears once every voice is named. Closes the Voice-ID
Phase B loop.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Done criteria (whole PR)

- `voiceid_enabled` off OR provider ≠ Speechmatics → **byte-identical to #163/#3 behaviour**: no sidecars are written (PR-3 gate), so `pending_voices_count` is always 0, no badge, no panel. The regression guard in `tests/test_processing_worker.py` (voiceid-off path) must still pass untouched.
- With the toggle on + Speechmatics: a finished meeting with unknown voices shows the badge; the panel plays each voice, binds it to a new/existing person, enrolls the identifier, re-renders `transcript.md` with the names + `## Связи` links, and clears the badge.
- Re-binding a known-but-mislabelled voice to an existing person appends a voiceprint (D-6; `add_voiceprint` caps at 5) — the next meeting passes that identifier and recognises them automatically.
- Full suite green on `py -3`; `ruff` clean; broad-except ratchet flat.

## Out of scope (deferred / not this PR)

- Manual smoke on real Speechmatics keys (separate, post-merge).
- Non-Speechmatics Voice-ID, enrollment from a dedicated clip, re-attributing pre-feature meetings (spec §10).
- If the panel proves oversized in review, playback can split into PR-4b (spec §6) — but it is included here.
