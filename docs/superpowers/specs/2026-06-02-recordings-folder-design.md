# Recordings folder relocation + optional retention — design

**Date:** 2026-06-02
**Status:** approved (brainstorming)

## Problem

The recorder writes raw `recording_<timestamp>.wav` files straight into the
`~/Documents` **root**: `recorder.py:27` defaults `output_dir` to
`os.path.join(os.path.expanduser("~"), "Documents")`, and `ui/app/__init__.py:175`
constructs `Recorder()` with no `output_dir`. Nothing ever deletes the `.wav`
after transcription (`stop()` keeps it; only `discard()` — the cancel path —
deletes). The transcript goes to `meetings_dir` (the Obsidian vault); the source
`.wav` is orphaned in Documents and piles up (~103 files since April).

## Goal

Stop cluttering the Documents root. Recordings are a **keep-artifact** (source
audio the user may replay or re-transcribe), so:

1. New recordings go to a tidy subfolder co-located with transcripts:
   `<meetings_dir>/recordings/`.
2. An opt-in config toggle deletes the `.wav` after a *successful* transcription
   for users who treat it as disposable. Default **keep** (no deletion).
3. A one-time standalone script moves the existing ~103 root files into the new
   folder (you-only; clients install fresh and have none).

## Decisions (from brainstorming Q&A)

- Recordings are **kept**, with an **opt-in delete toggle** (not delete-by-default).
- Location: **inside `meetings_dir`**, **flat** `<meetings_dir>/recordings/`
  (not per-meeting — recording starts before the meeting folder exists, so a flat
  folder avoids a post-transcription move).
- Toggle is a **config key only** (no Settings UI — same call as the descoped
  dedup checkbox; the safe default ships regardless).
- Existing files handled by a **standalone one-time script**, not in-app auto-migration.

## Approach

### 1. Location resolver — `utils.get_recordings_dir() -> str`

Returns `os.path.join(get_meetings_dir(), "recordings")`. Building on the existing
`get_meetings_dir()` (no args; reads config itself) inherits its 3-level fallback
(config `meetings_dir` → `_DEFAULT_MEETINGS_DIR` = `~/Documents/AudioTranscriber/meetings`
→ legacy `_internal/history`), the `~`/`%VAR%` expansion (`_normalize_meetings_path`),
and the writability checks — so recordings always land as a `recordings/` subfolder
of whatever meetings dir is actually in use. Set → `<vault>/recordings/`; unset →
`~/Documents/AudioTranscriber/meetings/recordings/`. Unit-testable by patching
`get_meetings_dir` to a tmp path. (`get_meetings_dir` creates the meetings dir as a
side effect; the `recordings/` subfolder itself is created at the write sites —
recorder `start()` and the move script.)

### 2. Recorder writes to the resolved dir — `recorder.py`

- `start()` gains an optional `output_dir` parameter. When provided it overrides
  `self._output_dir` for that recording (so a mid-session `meetings_dir` change
  is honored — the resolver is consulted per-recording, not once at construction).
- `start()` calls `os.makedirs(<dir>, exist_ok=True)` before opening the WAV
  (the new subfolder may not exist yet; today it relied on `~/Documents` always
  existing).
- The `__init__` fallback default changes from `~/Documents` (root) to
  `~/Documents/AudioTranscriber/recordings/`, so even a bare `Recorder()` never
  writes to the Documents root again.

### 3. Caller passes the resolved dir — `ui/app/recorder_mixin.py`

`_start_recording` calls `self._recorder.start(output_dir=get_recordings_dir())`.
No other recorder-mixin behavior changes.

### 4. Optional delete-after-transcription

- New config key `delete_recording_after_transcription` (default `false`).
- Pure decision helper `utils.should_delete_after_transcription(config, audio_path) -> bool`:
  returns `True` only when the key is truthy **and** `audio_path` resolves to a
  location **inside** `get_recordings_dir()` (path-containment check via
  `os.path.commonpath` / normalized `startswith`). This guarantees a user-loaded
  file outside the recordings dir is never deleted — stateless, no "is this a
  recording?" flag to keep in sync across the record-stop vs file-picker paths.
- After a *successful* transcription, the transcription mixin calls the helper and
  `os.unlink`s the `.wav` when it returns `True` (best-effort: an `OSError` on
  delete is logged, never crashes the post-transcription flow).

### 5. Config template — `config.example.json`

Add `"delete_recording_after_transcription": false`.

### 6. One-time move script — `scripts/move_recordings.py`

- Dry-run by default; `--apply` to execute.
- Selects `recording_*.wav` in the `~/Documents` **root only** (non-recursive,
  exact glob — won't touch unrelated files or subfolders), moves them into the
  resolved recordings dir (reads `meetings_dir` from the config via the same
  resolver), skipping any name collision (don't overwrite).
- Prints what it will move / moved. You-only; not bundled, not shipped to clients.

## Tests

Monkeypatch / source-text only (no `ui.app` import — sounddevice/PortAudio is
absent on Linux CI):

- `get_recordings_dir`: patch `get_meetings_dir` to a tmp path → returns `<tmp>/recordings`.
- `recorder.start(output_dir=...)`: creates the dir if missing and writes the WAV
  there; honors the override. (Keep existing recorder tests green.)
- `should_delete_after_transcription`: toggle off → False; on + path inside
  recordings dir → True; on + path outside → False.
- `scripts/move_recordings.py`: selection picks only root `recording_*.wav`
  (not subfolders, not other files); dry-run moves nothing; collision is skipped.
- `recorder_mixin` wiring: source-text assertion that `_start_recording` calls
  `start(output_dir=get_recordings_dir(...))`.

## Out of scope (deliberate)

- Settings UI checkbox for the toggle (config key only — Q4).
- Per-meeting co-location / moving the `.wav` after transcription (flat folder — Q3).
- In-app auto-migration of old files (standalone script — Q5).
- Configurable recordings path (fixed resolver; `meetings_dir` already drives it).

## Affected files

| File | Change |
|---|---|
| `utils.py` | + `get_recordings_dir()` (builds on `get_meetings_dir`), + `should_delete_after_transcription(config, audio_path)` |
| `recorder.py` | `start(output_dir=None)` + makedirs; `__init__` fallback default → recordings subfolder |
| `ui/app/recorder_mixin.py` | `_start_recording` passes the resolved dir |
| `ui/app/transcription_mixin.py` | post-success: delete `.wav` when the helper says so |
| `config.example.json` | + `delete_recording_after_transcription: false` |
| `scripts/move_recordings.py` | new one-time move helper (dry-run default) |
| `tests/` | resolver + recorder + delete-decision + move-script tests |
