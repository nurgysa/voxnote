# Architecture

Briefing for engineers (human or AI) picking up this codebase. Pairs with
[`README.md`](../README.md) (user-facing) and [`CLAUDE.md`](../CLAUDE.md)
(AI-agent quick reference). Read those first if you haven't.

## Reading order for new contributors

1. **`README.md`** — what the app does, how to install, how to run.
2. **`CLAUDE.md`** — invariants and conventions in compact form.
3. **This file** — module map, runtime model, JSON contracts.
4. Module docstrings on demand — every meaningful file has a top docstring
   that explains its role.

## Layered architecture

```
                    ┌────────────────────────────────────┐
                    │  app.py  ←  faulthandler bootstrap │  process entry
                    └────────────────────────────────────┘
                                       │
                    ┌──────────────────┴────────────────────┐
                    │  ui.app.App (CTk window)              │  presentation
                    │  ui.dialogs.* (Settings, History,     │  (~3000 LOC)
                    │   ExtractTasks, SystemMonitor,        │
                    │   Voices, Terms)                      │
                    └──────────────────┬────────────────────┘
                                       │
        ┌──────────────────────────────┼─────────────────────────────┐
        ▼                              ▼                             ▼
 ┌──────────────────┐         ┌──────────────────┐          ┌───────────────┐
 │ transcriber/     │         │ recorder.py      │          │ tasks/        │
 │  __init__.py     │         │ audio_cutter.py  │          │  extractor    │
 │  cuda_utils.py   │         │ silence_remover  │          │  sender       │
 │  prompt.py       │         │ enrollment_*.py  │          │  schema       │
 │  progress.py     │         │ voice_library.py │          │  persistence  │
 │  speaker_aligner │         └──────────────────┘          │  linear_clt   │
 └────────┬─────────┘                  │                    │  openrouter_c │
          │                            │                    └───────┬───────┘
          ▼                            ▼                            ▼
   ┌─────────────────┐         ┌──────────────────┐         ┌──────────────┐
   │ providers/      │         │ audio_io.py      │         │ requests +   │
   │  base.py (ABC)  │         │ (ffmpeg + numpy) │         │ Linear/OpenR │
   │  assemblyai.py  │         └──────────────────┘         │ HTTP         │
   └────────┬────────┘                                      └──────────────┘
            ▼
 ┌──────────────────────────┐
 │ AssemblyAI HTTPS         │
 │ (cloud transcription)    │
 └──────────────────────────┘

Subprocess spawned by transcriber:
   diarize_worker.py — pyannote diarization in fresh Python interpreter
                       (CUDA-state isolation; see "Subprocess protocol" below)
```

**Three runtime stacks** the app combines:

1. **Speech pipeline** (transcriber + diarize_worker + audio_io): heavy ML.
   Runs on the CPU/GPU device chosen per-stage in Settings. Local (default)
   or cloud-delegated via the providers package.
2. **Task pipeline** (tasks/* + ui/dialogs/extract_tasks): LLM-driven
   meeting-notes → Linear-issues flow added in Phase 6. HTTP only — no GPU.
3. **UI** (ui/* + customtkinter): single Tk main loop, App class as the
   coordinator. Long-running work happens on worker threads with results
   marshalled back via `self.after(0, ...)`.

## Subprocess protocol — transcriber → diarize_worker

The diarization stage runs in a **fresh Python subprocess** because
ctranslate2's `WhisperModel` and pyannote's CUDA state conflict on
destruction (the OS cleans up cleanly when a subprocess exits, sidestepping
the in-process abort).

```
Parent (transcriber/__init__.py)              Child (diarize_worker.py)
─────────────────────────────────             ──────────────────────────
Whisper transcription running                 spawned with DIARIZE_WAIT=1
        │                                              │
        │                                     import pyannote, load
        │                                     weights to CPU, decode audio
        │                                              │
        │                                     block on stdin readline()
Whisper finishes                                       │
        │                                              │
self.offload_to_cpu()  ← VRAM freed                    │
        │                                              │
write "GO\n" to child stdin  ──────────────────►  reads "GO\n"
        │                                              │
poll proc.wait(timeout=0.25)                  preflight CUDA → pipeline.to(cuda)
in 0.25s ticks                                         │
        │                                     run inference, stream
        │                                     "PROGRESS\t..." to stderr
read stderr lines, parse                               │
"PROGRESS" / "STATUS" via                              │
_parse_progress_line                                   │
        │                                              │
        │                                     write speaker_turns JSON
        │                                     to stdout, exit(0)
proc.wait() returns                                    ✓
parse stdout JSON                              (CUDA context reclaimed by OS)
```

- **Why the GO protocol**: child can do all CPU/RAM-only setup (imports,
  weight loading to CPU, audio decode) **in parallel** with the parent's
  Whisper inference. Sending GO after `offload_to_cpu` collapses a 10-15s
  dead zone at 70 % progress where both processes were idle waiting.
- **Cancel**: parent polls `cancel_event` every 250 ms; on cancel calls
  `proc.kill()` and raises `TranscriptionCancelled`. The OS reclaims the
  child's CUDA context.
- **1-hour deadline**: hard cap on total diarization wall time. Even an
  hour-long file finishes in ~5 min on this hardware; the cap catches a
  genuinely stuck subprocess.

## Task state machine — Send to Linear (Phase 6.3)

```
        ┌─────────┐
        │ PENDING │  initial state from extractor; awaits user "Send"
        └────┬────┘
             │ Send clicked + selected=True
             ▼
        ┌─────────┐                           ┌──────┐
        │ SENDING │ ──── LinearError ────►   │ FAIL │ ──┐
        └────┬────┘                           └──────┘   │
             │                                           │
             │ HTTP 200                                  │ Retry clicked
             ▼                                           │
        ┌─────────┐                                      │
        │  SENT   │ ◄────────────────────────────────────┘
        └─────────┘
        (terminal — never re-sent)
```

**Filtering rules** (`tasks/sender.py:_should_send`):

- Initial send: `selected=True AND status=PENDING`
- Retry: `status=FAILED` only

`SENT` tasks are NEVER re-sent regardless of selection — protects against
duplicate Linear issues.

## JSON file inventory

| File | Owner | Mutable? | Schema |
|---|---|---|---|
| `config.json` | `utils.save_config` | yes (settings, voices, hotwords) | see `config.example.json` |
| `<history>/tasks_raw.json` | `tasks.persistence.save_tasks_raw` | no (one-shot snapshot) | LLM extractor output |
| `<history>/tasks.json` | `tasks.persistence.save_tasks` | yes (selected, status, linear_*) | `tasks.schema.Task` dataclass |
| `logs/app.log` | `logging_setup` | yes (rotates 2MB × 5) | text log lines |
| `logs/faulthandler.log` | `app.py:14` | yes (overwrite per launch) | C-level signal traceback |

`<history>` is the per-recording folder under the user's history root.

## Cloud provider extension

To add a new cloud transcription provider (e.g. Deepgram, Speechmatics):

1. Subclass `TranscriptionProvider` in `providers/base.py`
2. Implement `transcribe(audio_path, options, on_status, on_progress, cancel_event)`
3. Register in `providers/__init__.py`'s `PROVIDERS` dict

The Settings dropdown auto-populates from `PROVIDERS.keys()`. Reference
implementation: `providers/assemblyai.py` (361 LOC, mocked-HTTP test
coverage in `tests/test_providers_assemblyai.py`).

The result must match `TranscriptionResult.segments` shape — list of
`{start, end, text, speaker?}` dicts — so the same `format_timed` /
`format_diarized` formatters work unchanged for both local and cloud paths.

## Windows-specific gotchas

These are the failure modes that shaped the architecture. Any change that
touches imports, GPU, or process startup must respect them:

1. **`ctranslate2` MUST import before `torch`**. Wrong order
   ⇒ `STATUS_DLL_INIT_FAILED` (Windows code 3221225794) on first run.
   Enforced by `transcriber/cuda_utils.py` running first inside an
   `# isort: off / on` block in `transcriber/__init__.py` so ruff can't
   reorder it.
2. **`faulthandler.enable` MUST run before any C-extension import**.
   `app.py` opens `logs/faulthandler.log` and enables faulthandler in
   the first 16 lines, BEFORE importing `ui.app` (which transitively
   imports ctranslate2 + torch + pyannote). Without this, a CUDA-teardown
   SIGSEGV leaves no diagnostic trail — the process just vanishes.
3. **GTX 1650 Ti VRAM** (~4 GB) can't hold Whisper + pyannote at once.
   `Transcriber.offload_to_cpu()` uses ctranslate2's
   `unload_model(to_cpu=True)` instead of `del model`, because `del`
   triggers a Fatal Python error in the native destructor on Windows.
4. **cuDNN must be disabled inside `diarize_worker.py`** before pyannote
   loads. On the 1650 Ti, leaving cuDNN on triggers
   `HOST_ALLOCATION_FAILED` / `CUBLAS_STATUS_NOT_INITIALIZED`.
5. **ffmpeg before Whisper**, not after. Loading ctranslate2 first locks
   CUDA DLLs in a state that makes ffmpeg's GPU probe fail at startup
   with `STATUS_DLL_INIT_FAILED`. `Transcriber.transcribe` runs
   `ensure_wav` (which spawns ffmpeg) before `load_model`.
6. **`requirements.txt` versions are pinned hard.** Every pin is
   load-bearing — speechbrain/lightning/pyannote/cuDNN workarounds
   depend on exact combinations. README explains why.
