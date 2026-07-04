# Architecture

Briefing for engineers (human or AI) picking up this codebase. Pairs with
[`README.md`](../README.md) (user/dev-facing) and [`CLAUDE.md`](../CLAUDE.md)
(AI-agent quick reference). Read those first if you haven't.

> **Cloud-only since 2026-05-28.** The local CUDA / faster-whisper / pyannote
> / diarize-worker stack was deleted. Transcription and diarization are now
> HTTPS calls to managed providers. The pre-rip-out subprocess/GPU topology
> documented in earlier revisions of this file is gone; this describes the
> current system.

## Reading order for new contributors

1. **`README.md`** — what the app does, how to install, how to run.
2. **`CLAUDE.md`** — invariants and conventions in compact form.
3. **This file** — module map, runtime model, JSON contracts.
4. Module docstrings on demand.

## Layered architecture

```
                ┌─────────────────────────────────────┐
                │  app.py  ←  faulthandler bootstrap  │   process entry
                └───────────────────┬─────────────────┘
                                    │
                ┌───────────────────┴─────────────────┐
                │  ui/app/  App (CTk window) + mixins  │   presentation
                │  ui/dialogs/  Settings, History,     │   (single Tk loop;
                │   Meetings, ExtractTasks, Directory, │    worker threads
                │   Migration, Terms                   │    marshal via after)
                └───────────────────┬─────────────────┘
                                    │
        ┌───────────────────────────┼────────────────────────────┐
        ▼                           ▼                            ▼
 ┌────────────────┐        ┌──────────────────┐         ┌──────────────────┐
 │ transcriber/   │        │ recorder.py      │         │ tasks/           │
 │  Transcriber   │        │ audio_cutter.py  │         │  extractor       │
 │  (dispatch +   │        │ audio_io.py      │         │  protocol_gen    │
 │   cancellation)│        │ (ffmpeg + numpy) │         │  sender · dedup  │
 └───────┬────────┘        └──────────────────┘         │  doc_context     │
         │                                              │  *_client        │
         ▼                                              └────────┬─────────┘
 ┌────────────────────┐                                         ▼
 │ providers/ (ABC)   │                              ┌────────────────────┐
 │  assemblyai        │                              │ tasks/backends/    │
 │  deepgram          │                              │  Protocol dispatch │
 │  gladia            │                              │  linear · trello · │
 │  speechmatics      │                              │  glide             │
 └───────┬────────────┘                              └────────┬───────────┘
         ▼                                                    ▼
 ┌────────────────────┐                              ┌────────────────────┐
 │ STT provider HTTPS │                              │ OpenRouter (LLM) + │
 │ (upload → poll →   │                              │ Linear/Trello/Glide│
 │  segments)         │                              │ HTTP               │
 └────────────────────┘                              └────────────────────┘

 Supporting packages:
   directory/   people/projects grounding (schema · store · context)
   processing/  meetings-by-project layout + queue (model · store · layout)
   cli/         headless CLI + MCP stdio server (app · core · mcp_server)
   utils.py · logging_setup.py · transcript_format.py · theme.py
```

**Two runtime stacks** the app combines:

1. **Speech pipeline** (`transcriber` → `providers` → HTTPS): no local ML, no
   GPU. `Transcriber.transcribe()` validates options, uploads audio to the
   selected provider, polls for the result, and normalizes it to segments.
2. **Task pipeline** (`tasks/*` + `ui/dialogs/extract_tasks`): LLM-driven
   transcript → tasks + `protocol.md`, dispatched to Linear/Trello/Glide via
   `tasks/backends/`. HTTP only.

**UI** (`ui/*` + CustomTkinter): a single Tk main loop with `App` as the
coordinator. Long-running work (transcription, extraction, send, backup) runs
on **worker threads**; results marshal back to the Tk thread via
`self.after(0, ...)`. Cancellation flows through a `threading.Event`
(`cancel_event`) that providers poll, raising `TranscriptionCancelled`.

## Cloud transcription flow

```
Transcriber.transcribe(audio_path, options, on_status, on_progress, cancel_event)
   │  validate options (provider configured, key present, language supported)
   │  ensure_wav() — ffmpeg normalize/denoise if needed (audio_io)
   ▼
provider.transcribe(...)
   │  POST audio  →  provider upload endpoint
   │  poll job status every N seconds (cancel_event checked each tick)
   │  on terminal status: GET transcript JSON
   ▼
_to_segments(payload) → list of {start, end, text, speaker?}
   │
   ▼
transcript_format.format_timed / format_diarized  (same for every provider)
```

Provider errors raise `ProviderError`; the dispatcher re-wraps to `RuntimeError`
with the message preserved, so the UI shows a humanized Russian message
(`tasks/errors.humanize`) rather than a raw traceback.

## Task state machine — Send (Linear / Trello / Glide)

```
   ┌─────────┐  Send clicked + selected   ┌─────────┐  HTTP 200   ┌──────┐
   │ PENDING │ ─────────────────────────► │ SENDING │ ──────────► │ SENT │
   └─────────┘                            └────┬────┘             └──────┘
                                               │ backend error    (terminal —
                                               ▼                   never re-sent)
                                          ┌────────┐  Retry
                                          │ FAILED │ ──────────────┘
                                          └────────┘
```

**Filtering** (`tasks/sender.py`): initial send = `selected AND PENDING`;
retry = `FAILED` only. `SENT` is never re-sent — protects against duplicate
issues/cards. The dedup pass (`tasks/dedup.py`) can mark a task `COMMENTED`
(comment on an existing card instead of creating a duplicate).

## JSON / file inventory

| File | Owner | Mutable? | Schema |
|---|---|---|---|
| `~/.voxnote/config.json` | `utils.save_config` | yes | see `config.example.json` |
| `~/.voxnote/directory.json` | `directory.store` | yes | people/projects directory |
| `<meeting>/transcript.md` · `description.md` | `utils` / extract flow | one-shot | markdown |
| `<meeting>/tasks_raw.json` | `tasks.persistence` | no | LLM extractor output |
| `<meeting>/tasks.json` | `tasks.persistence` | yes | `tasks.schema.Task` |
| `<meeting>/protocol.md` | `tasks.protocol_generator` | one-shot | 5-block MoM |
| `<meeting>/segments.json` · `speakers.json` | `utils.save_segments/_speakers` | one-shot | per-run timing |
| `logs/app.log` | `logging_setup` | rotates 2MB×5 | text |

`<meeting>` is the per-recording folder under the meetings root
(`meetings_dir`, default `Documents\VoxNote\meetings`; recordings go
to `<meetings_dir>/recordings`).

## Cloud provider extension

1. Subclass `TranscriptionProvider` in [`providers/base.py`](../providers/base.py).
2. Implement `transcribe(audio_path, options, on_status, on_progress, cancel_event)`
   and the capability flags (`supports_diarization`, `supports_mixed`, ...).
3. Register in [`providers/__init__.py`](../providers/__init__.py)'s registry.

The Settings dropdown auto-populates from the registry. The result must match
`{start, end, text, speaker?}` segment shape so the shared `format_timed` /
`format_diarized` formatters work unchanged. Reference:
[`providers/assemblyai.py`](../providers/assemblyai.py) with mocked-HTTP tests
in `tests/test_providers_assemblyai.py`.

## Windows-specific gotchas

These shaped the architecture; any change touching startup, packaging, or
ffmpeg must respect them:

1. **`faulthandler.enable()` before any C-extension import** (`app.py` top).
   Native deps (soundfile, sounddevice) can SIGSEGV during shutdown; without
   the early enable the process vanishes with no trace.
2. **PyInstaller windowed mode sets `sys.stderr = None`** (`runw.exe`).
   `faulthandler.enable()` then raises silently → generic "Unhandled
   exception" dialog. [`runtime_hook_imports.py`](../runtime_hook_imports.py)
   redirects None streams to a `%TEMP%` sidecar before any print/faulthandler.
3. **ffmpeg filtergraph path escaping.** Windows paths (spaces, `:`, Cyrillic,
   backslashes) must be escaped per ffmpeg filtergraph rules — see
   `audio_io._escape_ffmpeg_filter_path`. Mocked subprocess tests verify
   string composition but NOT parseability; ffmpeg-touching code needs manual
   smoke. All ffmpeg calls use argv-list form (no `shell=True`).
4. **`requirements.txt` pins are load-bearing** (CustomTkinter / soundfile /
   sounddevice on Windows). Don't liberalize without a clean-VM smoke.
5. **Config lives in `~/.voxnote/`, never in the bundle.** Frozen
   builds resolve config there so client updates don't wipe keys; the bundle
   ships only `config.example.json` (enforced by `scripts/package_release.py`).
```
