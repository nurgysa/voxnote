# Tauri Lite-Rewrite — Design (v0.2)

**Date:** 2026-05-28
**Status:** Draft (post-brainstorm, pre-plan)
**Author:** nurgysa (with Claude)
**Brainstorm session:** in-conversation 2026-05-28
**Companion to:** `docs/superpowers/specs/2026-05-26-tauri-saas-migration-design.md` (big-bang Tauri SaaS variant — PAUSED 2026-05-27)

> Lite-rewrite of the `audio-transcriber` UI layer from Python + CustomTkinter
> to Tauri 2 + React 19 + TypeScript, keeping the entire shipped Python
> backend (`providers/`, `tasks/`, `gdrive/`, `transcriber/`) as an embedded
> sidecar. **Feature parity 1-to-1** with MVP v0.1 at cutover — no managed
> SaaS, no Stripe, no Supabase, no vault layout, no RAG, no MCP. Distinct
> from the 2614-line big-bang spec which describes a full managed-SaaS
> rewrite over ~6 months.

## 1. Context

The current `audio-transcriber` is a Windows desktop app: Python +
CustomTkinter UI driving 4 cloud STT providers (AssemblyAI, Deepgram,
Gladia, Speechmatics) + OpenRouter for task extraction and protocol
generation. Cloud-only since the 2026-05-28 v5 rip-out (the local
CUDA / Whisper / pyannote stack was deleted, `requirements.txt` dropped
from ~6-8 GB install to ~150 MB).

MVP v0.1 is **code-complete** as of 2026-05-28 (commits `1577e40` Task 7
"MVP code-complete" + `03d43ce` Task 9 partial), distributed as a
PyInstaller `--onedir` bundle (~351 MB, MSI-less `.zip` extraction to
`C:\Apps\`). Three first paying clients are the immediate ship target —
see `docs/CLIENT_SETUP.md`.

### 1.1 Why a lite-rewrite (not the big-bang Tauri spec)

The big-bang spec
(`docs/superpowers/specs/2026-05-26-tauri-saas-migration-design.md`, 2614
lines, committed as `b3ed95a` + frontend-stack pin `a23b82d` + WebdriverIO
fix `19867bc`) was **paused 2026-05-27** in favour of the MVP-to-3-clients
push. It describes:

- Tauri 2 + React + FastAPI + Supabase + Stripe managed SaaS
- Vault layout (Obsidian-style)
- Voice library + ECAPA-TDNN embeddings
- 8-pass LLM pipeline + 7 task backends + generic webhook
- RAG chat over the vault
- Bi-directional MCP (server + client)
- Email + Telegram protocol distribution
- ~6 months of solo work

That's the long-term direction. This spec is the **shorter path to a
visibly-better client experience**: replace only the UI layer, keep the
proven Python backend, ship in ~8 calendar weeks. v0.2 alpha lands in
client hands ~2 months after MVP, with feedback loops informing whether
to continue toward the big-bang scope or stay in lite-rewrite mode.

### 1.2 Motivation (from brainstorm 2026-05-28)

Four motives were endorsed by the user, all pointing in the same direction:

1. **CustomTkinter looks unprofessional** — basic Tk widgets, limited
   animation, dated dark-theme support. Tech-insider clients see it as
   "homemade".
2. **UI features painful in Tk** — waveform timeline player, full-window
   drag-drop, multi-window, markdown editor for protocol, in-line
   transcript edit with speaker reassignment.
3. **Agentic-friendly stack** — TypeScript + React have orders of
   magnitude more LLM training data than CustomTkinter. Code review,
   widget generation, refactoring are all noticeably faster + more
   accurate. Critical for solo development on an LLM-velocipede.
4. **Cross-platform** — Windows-only today; macOS and Linux requested
   by some prospective clients. Python+CTk theoretically portable but
   untested; Tauri/Electron natively cross-platform.

### 1.3 What is explicitly NOT in this scope

This is a **UI-layer rewrite only**. Anything beyond UI/UX polish that
was discussed in the big-bang spec is deferred — see §11 for the full
list. The principle is: **preserve all working backend code, replace
only what motivates the rewrite**.

## 2. Decisions log

Locked-in choices from the 2026-05-28 brainstorm:

| Question | Choice |
|---|---|
| Scope | Lite-rewrite of UI layer only (Python backend preserved) |
| Sequencing | MVP v0.1 ships to 3 clients first → then start v0.2 lite-rewrite |
| Desktop shell | Tauri 2 (not Electron) |
| Frontend framework | React 19.x (lifted from `2026-05-26-tauri-saas-migration-design.md` §2.1) |
| Language | TypeScript 5.x |
| Build tool | Vite 6.x |
| Routing | TanStack Router 1.x (typed search params, file-based routing) |
| Data fetching | TanStack Query 5.x |
| UI primitives | shadcn/ui (copy-paste model, in-repo) |
| CSS | Tailwind v4.x |
| State management | Zustand 5.x |
| Unit tests | Vitest 3.x |
| E2E / desktop tests | WebdriverIO 9.x via `tauri-driver` |
| Package manager | pnpm 9.x |
| IPC topology | stdio JSON-RPC 2.0 line protocol over `tauri-plugin-shell` sidecar |
| Python sidecar packaging | PyInstaller `--onedir`, bundled as Tauri sidecar binary |
| Recording flow | Web Audio API in Tauri WebView; Python sees only finished file |
| Feature parity scope | 1-to-1 with MVP v0.1 — no new features in v0.2 cutover |
| Persistence | `$APPDATA/audio-transcriber/{config.json, history/}` (lift from v0.1 layout) |
| Migration v0.1 → v0.2 | First-run dialog: detect v0.1 install, copy config + history; user keeps v0.1 install around manually |
| Repo structure | Monorepo extension of current repo (`src/`, `src-tauri/`, `python-sidecar/`) |
| Code signing pre-v1.0 | Skip for alpha; pursue Windows EV cert + macOS notarization for v0.2 stable |
| Auto-updater | Defer to v0.2 stable (`tauri-plugin-updater`) |
| UX/UI improvements beyond parity | Deferred to Phase 2 track — see §11.2 |

## 3. Goals & Scope

### 3.1 Included in v0.2 (feature parity with MVP v0.1)

- Microphone recording (Web Audio API + wavesurfer.js for live waveform)
- Audio file import (drag-drop + file picker)
- Transcription via 4 cloud STT providers (AssemblyAI / Deepgram / Gladia / Speechmatics)
- Diarization (provider-built-in, AssemblyAI Universal default)
- Code-switching KZ + RU + EN (provider-native)
- Task extraction via OpenRouter (Linear + Glide backends — Trello deferred)
- Protocol generation (5-block MoM, lifted as-is from `tasks/protocol_generator.py`)
- Audio cutter (manual trim, preview, export)
- History view (list + detail)
- Google Drive backup (manual trigger, button in Settings)
- Settings dialog (4 sections: STT, LLM/OpenRouter, Task backends, Google Drive)
- First-run banner (cloud API keys empty → prompt user to open Settings)
- Cross-platform builds (Windows MSI, macOS DMG, Linux AppImage)

### 3.2 Explicitly excluded from v0.2 — deferred to Phase 2+

See §11 for the full deferred list. Headline exclusions:

- Voice library / speaker enrollment
- Vault layout (Obsidian-style)
- RAG chat over history
- 8-pass LLM pipeline (v0.2 stays at 2 passes: tasks + protocol, matching v0.1)
- Additional task backends (Notion, Jira, Яндекс Трекер, Битрикс24, GitHub Projects)
- MCP server / client
- Email / Telegram protocol distribution
- Managed SaaS backend (FastAPI, Supabase, Stripe)
- React Native / mobile build
- Custom design system + motion language (deferred to UX Phase 2 — see §11.2)

### 3.3 Hard gate / prerequisite

MVP v0.1 must be shipped to **at least 1 paying client** before v0.2
lite-rewrite work starts. Rationale: real-user feedback informs scope
priorities. Ideal: 3 clients shipped + 1 week of feedback collection
before kicking off lite-rewrite plan-writing.

## 4. Architecture

### 4.1 Three-tier overview

```
┌────────────────────────────────────────────────────────────┐
│ AudioTranscriber.exe (Tauri 2)                              │
│ ┌──────────────────────────────────────────────────────┐   │
│ │ React 19 + TS Frontend (WebView2/WKWebView/WebKitGTK)│   │
│ │   shadcn/ui + Tailwind v4 + TanStack Query/Router    │   │
│ │   Zustand (global state) + Vitest (units)            │   │
│ │   wavesurfer.js (waveform), react-md-editor          │   │
│ └────────────────┬─────────────────────────────────────┘   │
│                  │ Tauri IPC (invoke + events)              │
│ ┌────────────────▼─────────────────────────────────────┐   │
│ │ Tauri Rust core (~300-500 LOC, minimal bridge)        │   │
│ │   • fs (read/write config.json, audio files)         │   │
│ │   • shell (spawn Python sidecar, JSON-RPC relay)     │   │
│ │   • keychain (opt-in API keys via OS keyring)        │   │
│ │   • dialog (file picker)                             │   │
│ └────────────────┬─────────────────────────────────────┘   │
│                  │ stdio JSON-RPC 2.0 line protocol         │
│ ┌────────────────▼─────────────────────────────────────┐   │
│ │ Python sidecar (PyInstaller --onedir, ~150-300 MB)    │   │
│ │   • providers/ — 4 cloud STT (lifted as-is)          │   │
│ │   • tasks/ — extractor + protocol_generator (lifted) │   │
│ │   • gdrive/ — auth + backup (lifted)                 │   │
│ │   • transcriber/ — cloud_chunker + dispatch (lifted) │   │
│ │   • sidecar_main.py — JSON-RPC dispatcher (NEW)      │   │
│ └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
                       │
                       ▼
                  Cloud APIs
   (AssemblyAI, Deepgram, Gladia, Speechmatics, OpenRouter,
    Linear, Glide, Google Drive)
```

**Key principle:** Tauri Rust core is a **minimal bridge only** — spawn
Python sidecar, relay JSON-RPC messages, surface OS APIs (file dialog,
keychain). All business logic stays in the proven Python backend. If a
Rust change needs business reasoning beyond "marshal this JSON", that's
a smell — move it to Python.

### 4.2 Tauri Rust core (`src-tauri/`)

Size: ~300-500 LOC Rust. Dependencies (Cargo.toml):

```toml
[dependencies]
tauri = { version = "2", features = ["protocol-asset"] }
tauri-plugin-shell = "2"
tauri-plugin-fs = "2"
tauri-plugin-dialog = "2"
tauri-plugin-keyring = "2"     # opt-in keychain (see §6)
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full"] }
```

Responsibilities:

- **Sidecar lifecycle:** spawn `audio-transcriber-core` via
  `tauri_plugin_shell::Command::new_sidecar(...)` at app startup;
  monitor exit status; restart on crash (max 3 retries within 60 s,
  then surface "sidecar unavailable" error to React).
- **JSON-RPC bridge:** Tauri command `invoke_python(request: JsonRpcRequest) -> Result<JsonRpcResponse>`.
  Internally a single-writer / multi-reader task pumps stdin / stdout
  of the sidecar process. Notifications (JSON-RPC messages without `id`)
  are forwarded as Tauri events (`app.emit_all("python-event", payload)`).
- **OS APIs:** file dialogs (`tauri-plugin-dialog`), OS notifications
  (Tauri built-in), system tray (optional — defer to v0.2 stable).
- **First-run bootstrap:** if `$APPDATA/audio-transcriber/config.json`
  is missing, copy from `resources/config.example.json` (bundled in
  the Tauri app).
- **Migration detection:** see §8.

What the Rust core does **not** do: parse audio, hit HTTP APIs, run
business logic. If a function would do those — it belongs in Python.

### 4.3 React frontend (`src/`)

File layout (feature-based):

```
src/
├── app/                       # Routes (TanStack Router file-based)
│   ├── __root.tsx             # Shell + global error boundary
│   ├── index.tsx              # Home (record + transcribe)
│   ├── history.tsx            # History list
│   ├── history.$runId.tsx     # History detail (transcript + protocol + tasks)
│   ├── audio-cutter.tsx       # Audio editor
│   └── settings.tsx           # Settings (4 sections)
├── components/
│   ├── ui/                    # shadcn/ui primitives in repo
│   ├── Recorder.tsx           # Mic button + live waveform + level meter
│   ├── TranscriptViewer.tsx   # Speaker-grouped segments (read-only in v0.2)
│   ├── ProtocolViewer.tsx     # Markdown render of 5-block MoM (read-only in v0.2)
│   ├── TaskList.tsx           # Extracted tasks + send to Linear/Glide
│   └── FirstRunBanner.tsx     # Yellow banner when AssemblyAI key empty
├── lib/
│   ├── ipc.ts                 # invoke_python<T>() typed wrapper + event subscribe
│   ├── store.ts               # Zustand global store (vault, prefs, current run)
│   ├── schemas.ts             # Zod schemas mirroring Pydantic models
│   └── python-types.d.ts      # GENERATED from Pydantic models, gitignored
├── hooks/
│   ├── useTranscribe.ts       # TanStack Query mutation + progress subscribe
│   ├── useExtractTasks.ts     # TanStack Query mutation
│   ├── useGenerateProtocol.ts # TanStack Query mutation
│   ├── useHistory.ts          # TanStack Query: list + invalidate on new run
│   └── useGDriveBackup.ts     # TanStack Query mutation
└── main.tsx
```

**Type contract Python ↔ TS:** generated from Pydantic v2 models in
`python-sidecar/` via `pydantic-to-typescript` (pre-commit hook). Output
goes to `src/lib/python-types.d.ts` (gitignored — regenerated on every
commit). CI fails if generated output differs from committed Pydantic
source (catches drift).

Conventions:

- All IPC calls go through `ipc.ts::invokePython<T>(method, params)` —
  no `invoke('transcribe', ...)` scattered through components. Single
  chokepoint for retry / error normalization / logging.
- Loading + error states use TanStack Query's `isPending` / `error` —
  no manual `useState` loading booleans.
- Russian UI strings inline in components (matches the v0.1 convention
  per `CLAUDE.md`). i18n deferred — no `react-i18next` in v0.2.

### 4.4 Python sidecar (`python-sidecar/`)

Layout: a near-verbatim copy of the existing `audio-transcriber/` Python
modules, plus one new entry point.

```
python-sidecar/
├── sidecar_main.py            # NEW — JSON-RPC dispatcher (~200 LOC)
├── providers/                 # LIFTED — 4 cloud STT (assemblyai/deepgram/gladia/speechmatics)
├── tasks/                     # LIFTED — extractor, sender, protocol_generator, linear/glide clients
├── gdrive/                    # LIFTED — auth, client, backup
├── transcriber/               # LIFTED — cloud_chunker, dispatch (post v5 rip-out)
├── utils.py                   # LIFTED — get_ffmpeg_path, save_config
├── logging_setup.py           # LIFTED
├── transcript_format.py       # LIFTED — format_diarized, format_timed
├── audio_io.py                # LIFTED — ffmpeg subprocess helpers
├── vendor/ffmpeg/             # LIFTED — bundled ffmpeg.exe + ffprobe.exe
├── audio_transcriber_sidecar.spec  # PyInstaller spec for sidecar mode
└── requirements.txt           # LIFTED (~10 deps after v5 rip-out)
```

Removed from the Python tree (compared to v0.1 main repo):

- `ui/` — entire CustomTkinter package, including `ui/app/`, `ui/dialogs/`, `ui/widgets/`
- `app.py` — entry point replaced by `sidecar_main.py`
- `recorder.py` — recording now in Tauri Web Audio API (Python no longer holds the mic handle)
- `audio_cutter.py` UI portion — preview + waveform UI in React; ffmpeg trim subprocess remains (callable via sidecar method)

**`sidecar_main.py` skeleton (~200-300 LOC):**

The handlers below are **wrappers**, not naive `lambda p: f(**p)` calls.
The v0.1 Python functions have idiomatic Python signatures
(`Transcriber.transcribe(...)` returns a formatted `str` and populates
`self.last_segments`; `tasks.extractor.extract(*, ..., openrouter_client,
linear_client)` takes constructed client objects; `tasks.protocol_generator.generate(...)`
returns a `ProtocolResult` dataclass). The sidecar handlers construct
those client objects from JSON-RPC params, invoke the v0.1 functions
as-is, and serialise their returns into JSON-friendly payloads.

```python
"""JSON-RPC 2.0 dispatcher for the Audio Transcriber Python sidecar.

Reads JSON-RPC requests line-by-line from stdin; writes JSON-RPC responses
and streaming notifications to stdout. Each line is one complete JSON
object — no embedded newlines (Python's `json.dumps` is single-line by
default).

Notifications (id absent) are progress events emitted during long-running
methods (transcribe, gdrive_backup). The Tauri Rust core forwards them
as Tauri events to React via `app.emit_all("python-event", payload)`.

This file is the only NEW Python module in the sidecar. Every other
module (providers/, tasks/, gdrive/, transcriber/, utils, logging_setup,
audio_io, transcript_format) is lifted verbatim from the v0.1 main repo.
"""
from __future__ import annotations

import faulthandler  # CLAUDE.md invariant #1 — initialise before C-extension imports
import json
import sys
import threading
import traceback
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

faulthandler.enable()  # do this FIRST — see CLAUDE.md

from gdrive.backup import run_backup
from tasks.extractor import extract as _extract
from tasks.linear_client import LinearClient
from tasks.openrouter_client import OpenRouterClient
from tasks.protocol_generator import generate as _generate_protocol
from tasks.sender import send_tasks_iter
from transcriber import Transcriber, TranscriptionCancelled
from utils import load_config, save_config
from logging_setup import get_logger

logger = get_logger(__name__)

# Single per-process cancel event — set by the "cancel" method,
# observed by Transcriber via on_progress / cancel_event callback.
_cancel_event = threading.Event()


def _emit_notification(method: str, params: dict[str, Any]) -> None:
    """Send a JSON-RPC notification (no id) to stdout — for progress events."""
    print(
        json.dumps({"jsonrpc": "2.0", "method": method, "params": params}),
        flush=True,
    )


def _to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of v0.1 return types to JSON-friendly shapes."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, list):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _handle_transcribe(params: dict[str, Any]) -> dict[str, Any]:
    """Wrap Transcriber.transcribe; return structured segments + formatted text.

    v0.1 Transcriber.transcribe(...) returns a formatted str and populates
    self.last_segments. The sidecar UI wants structured segments to render
    its own transcript view, so we read last_segments after the call and
    return both: the legacy formatted text (compat) plus segments[] (new).
    """
    _cancel_event.clear()
    transcriber = Transcriber()

    formatted_text = transcriber.transcribe(
        audio_path=params["audio_path"],
        language=params.get("language"),
        diarize=params.get("diarize", False),
        hotwords=params.get("hotwords"),
        num_speakers=params.get("num_speakers"),
        denoise_audio=params.get("denoise_audio", False),
        cloud_provider=params["cloud_provider"],
        cloud_api_key=params["cloud_api_key"],
        on_progress=lambda pct: _emit_notification("progress", {"pct": pct}),
        on_status=lambda msg: _emit_notification("status", {"message": msg}),
        cancel_event=_cancel_event,
    )

    return {
        "formatted_text": formatted_text,
        "segments": transcriber.last_segments or [],
    }


def _handle_extract_tasks(params: dict[str, Any]) -> dict[str, Any]:
    """Wrap tasks.extractor.extract; construct OpenRouter + Linear clients."""
    openrouter = OpenRouterClient(api_key=params["openrouter_api_key"])
    linear = None
    if params.get("linear_api_key"):
        linear = LinearClient(api_key=params["linear_api_key"])
    result = _extract(
        transcript=params["transcript"],
        model=params["model"],
        lang=params.get("language"),
        openrouter_client=openrouter,
        members=params.get("members") or [],
        labels=params.get("labels") or [],
        team_id=params.get("team_id"),
        linear_client=linear,
    )
    return _to_jsonable(result)


def _handle_generate_protocol(params: dict[str, Any]) -> dict[str, Any]:
    """Wrap tasks.protocol_generator.generate; return JSON-friendly ProtocolResult."""
    openrouter = OpenRouterClient(api_key=params["openrouter_api_key"])
    protocol = _generate_protocol(
        transcript=params["transcript"],
        speakers=params.get("speakers") or [],
        meeting_date=params["meeting_date"],
        lang=params.get("language"),
        model=params["model"],
        openrouter_client=openrouter,
    )
    return _to_jsonable(protocol)


def _handle_send_tasks(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrap tasks.sender.send_tasks_iter; collect generator yields to a list."""
    return list(send_tasks_iter(**params))


def _handle_cancel(_params: dict[str, Any]) -> dict[str, bool]:
    _cancel_event.set()
    return {"cancelled": True}


DISPATCH: dict[str, Callable[[dict[str, Any]], Any]] = {
    "ping": lambda _: {"pong": True},
    "transcribe": _handle_transcribe,
    "extract_tasks": _handle_extract_tasks,
    "generate_protocol": _handle_generate_protocol,
    "send_tasks": _handle_send_tasks,
    "gdrive_backup": lambda p: run_backup(**p),
    "list_history": _handle_list_history,    # NEW sidecar-only — reads $APPDATA/audio-transcriber/history/
    "trim_audio": _handle_trim_audio,        # NEW sidecar-only — ffmpeg subprocess for audio cutter
    "load_config": lambda _: load_config(),
    "save_config": lambda p: save_config(p["config"]),
    "cancel": _handle_cancel,
    "shutdown": lambda _: sys.exit(0),
}


def main() -> None:
    """Main dispatch loop — read stdin lines, dispatch, write responses."""
    logger.info("Sidecar started, awaiting JSON-RPC requests on stdin")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.exception("Malformed JSON request: %s", exc)
            continue  # cannot respond — no id to correlate against

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})

        if method not in DISPATCH:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }), flush=True)
            continue

        try:
            result = DISPATCH[method](params)
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result,
            }), flush=True)
        except TranscriptionCancelled:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32001, "message": "Cancelled by user"},
            }), flush=True)
        except Exception as exc:
            logger.exception("Dispatch error for method=%s", method)
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": str(exc),
                    "data": {"traceback": traceback.format_exc()},
                },
            }), flush=True)


if __name__ == "__main__":
    main()
```

Verified against v0.1 code at spec-writing time (commit `03d43ce`):

| Sidecar handler | Wraps v0.1 function | Verified location |
|---|---|---|
| `_handle_transcribe` | `Transcriber.transcribe(..., on_progress, on_status, cancel_event)` returns `str`, populates `self.last_segments` | `transcriber/__init__.py:60-87` + `:58` + `:224` |
| `_handle_extract_tasks` | `tasks.extractor.extract(*, transcript, model, lang, openrouter_client, members, labels, team_id, linear_client)` | `tasks/extractor.py:237` |
| `_handle_generate_protocol` | `tasks.protocol_generator.generate(transcript, speakers, meeting_date, lang, model, openrouter_client) -> ProtocolResult` | `tasks/protocol_generator.py:172` |
| `_handle_send_tasks` | `tasks.sender.send_tasks_iter(...)` (generator) | `tasks/sender.py:42` |
| `gdrive_backup` | `gdrive.backup.run_backup(...)` | `gdrive/backup.py:188` |

Sidecar-only handlers (no v0.1 counterpart — NEW code in `sidecar_main.py`):

| Sidecar handler | Purpose | Implementation hint |
|---|---|---|
| `_handle_list_history` | Walk `$APPDATA/audio-transcriber/history/<run_id>/` and return list of `{run_id, timestamp, transcript_excerpt, has_protocol, has_tasks}` for React's history list | `Path(app_data_dir / 'history').iterdir()` + read `meta.json` per entry; ~30 LOC |
| `_handle_trim_audio` | Run ffmpeg subprocess to trim an audio file `[(start, end), ...]` ranges into a new file. Replaces the v0.1 `audio_cutter.py` ffmpeg call site (the UI part moves to React). | `subprocess.run([ffmpeg_path, '-i', ..., '-filter_complex', concat_filter, '-y', output])` per `audio_io.py` patterns; ~50 LOC |

JSON-RPC method surface (v0.2):

| Method | Purpose | Streams progress? |
|---|---|---|
| `ping` | Health check (Rust core uses on startup) | No |
| `transcribe` | Run full transcribe + diarize via chosen provider; returns `{formatted_text, segments[]}` | Yes — `progress` (pct) + `status` (message) notifications |
| `cancel` | Set the per-process cancel event observed by `Transcriber` | No |
| `extract_tasks` | Run task extractor on a transcript (constructs OpenRouter + Linear clients from params) | No |
| `generate_protocol` | Run 5-block MoM protocol generator (returns serialised `ProtocolResult`) | No |
| `send_tasks` | Send extracted tasks to Linear/Glide; wraps `send_tasks_iter` generator | No |
| `gdrive_backup` | Run Google Drive backup of `history/` + redacted `config.json` | Yes — `progress` per file |
| `list_history` | Return history entries from `$APPDATA/audio-transcriber/history/` | No |
| `trim_audio` | ffmpeg subprocess to trim audio file with `[(start, end), ...]` ranges | No |
| `load_config` | Read + return `config.json` | No |
| `save_config` | Write `config.json` (overwrite) | No |
| `shutdown` | Graceful sidecar exit | No |

## 5. Data flows

### 5.1 Recording flow

```
User clicks "Запись" in Recorder.tsx
        │
        ▼
React: navigator.mediaDevices.getUserMedia({audio: true})
        │
        ▼  (MediaStream)
React Recorder.tsx:
  • MediaRecorder API → chunks → Blob (audio/webm Opus)
  • Parallel: AudioContext + AnalyserNode → wavesurfer.js live waveform
  • Level meter: avg RMS from AnalyserNode every ~50 ms
        │
        ▼  (User clicks "Стоп")
React: blob.arrayBuffer() → invoke('save_recording', {bytes, mimeType})
        │
        ▼
Tauri Rust: fs.writeBinaryFile($APPDATA/audio-transcriber/recordings/{uuid}.webm)
        │
        ▼
Returns file_path → React passes into transcribe flow (§5.2)
```

Recording format: WebM/Opus in the browser. Python decodes via ffmpeg
on transcribe (cloud providers accept WebM, or Python converts to WAV
before upload — provider-dependent). Advantage: WebM ~10× smaller than
WAV, faster upload.

Permission UX: browser permission dialog appears once and is remembered
by Tauri. State machine in Zustand: `recordingPermission: 'granted' |
'denied' | 'prompt'`. Denied path shows instructions to grant via OS
settings.

### 5.2 Transcribe flow (streaming JSON-RPC sequence)

```
[React]                      [Rust]                        [Python]
   │                             │                            │
   │  invoke('transcribe', {    │                            │
   │   file_path: "...webm",    │                            │
   │   provider: "AssemblyAI",  │                            │
   │   diarize: true,           │                            │
   │   language: "ru"           │                            │
   │  })                        │                            │
   ├────────────────────────────►│                            │
   │                             │  stdin: {"jsonrpc":"2.0", │
   │                             │   "id":42,                 │
   │                             │   "method":"transcribe",   │
   │                             │   "params":{...}}\n        │
   │                             ├───────────────────────────►│
   │                             │                            │
   │ emit('python-event',       │  stdout: {"jsonrpc":"2.0",│
   │  {method:"progress",       │   "method":"progress",     │
   │   params:{phase:           │   "params":{phase:         │
   │     "uploading"}})         │     "uploading"}}\n        │
   │◄────────────────────────────┤◄───────────────────────────┤
   │                             │   (no id → notification)   │
   │                             │                            │
   │ emit('python-event',       │  stdout: {"method":        │
   │  {phase:"transcribing",    │   "progress",              │
   │   pct:0.45})               │   "params":{pct:0.45}}\n  │
   │◄────────────────────────────┤◄───────────────────────────┤
   │                             │                            │
   │ resolve({                  │  stdout: {"jsonrpc":"2.0",│
   │   formatted_text: "...",   │   "id":42,                 │
   │   segments: [...]          │   "result":{              │
   │ })                         │     formatted_text:"...", │
   │                            │     segments:[...]}}\n    │
   │◄────────────────────────────┤◄───────────────────────────┤
```

TypeScript hook usage (`useTranscribe.ts`):

```typescript
export function useTranscribe() {
  const updateProgress = useTranscribeStore((s) => s.updateProgress);
  return useMutation({
    mutationFn: async (params: TranscribeRequest): Promise<TranscribeResult> => {
      const unsubscribe = await listen<PythonEvent>('python-event', (e) => {
        if (e.payload.method === 'progress') {
          updateProgress(e.payload.params);
        }
      });
      try {
        return await invokePython<TranscribeResult>('transcribe', params);
      } finally {
        unsubscribe();
      }
    },
  });
}
```

Cancellation: Tauri command `cancel_transcribe(req_id)` sends
`{"jsonrpc":"2.0","method":"cancel","params":{"id":42}}` to Python
stdin. Python sets a cancellation flag observed by the existing
`_check_cancelled()` helper (lifted from `transcriber/__init__.py`),
which raises `TranscriptionCancelled` → caught by the dispatcher →
JSON-RPC error code -32001 returned to React.

### 5.3 Extract tasks + protocol flow

Sequential (Phase A: extract_tasks → Phase B: generate_protocol).
Both methods return synchronous results; no progress streaming needed
(each pass is ~10-30 s, single OpenRouter call).

```
React: useExtractTasks().mutate({transcript, model, language})
  → tasks.extractor.extract_tasks(...)  via JSON-RPC
  → returns list of {title, assignee, due_date, priority}

If protocol checkbox ON (default true):
  React: useGenerateProtocol().mutate({transcript, language})
    → tasks.protocol_generator.generate_protocol(...)  via JSON-RPC
    → returns markdown string with 5 H2 blocks

Both results written to $APPDATA/audio-transcriber/history/<run_id>/:
  • tasks.json
  • protocol.md
```

### 5.4 GDrive backup flow

```
User clicks "Сделать backup сейчас" in Settings → GDriveBackup section
  → useGDriveBackup().mutate({})
  → invokePython('gdrive_backup', {})
  → Python: gdrive.backup.run_backup() — lifted from v0.1, unchanged

Backup steps inside Python:
  1. Ensure GDrive auth token valid (refresh if needed via gdrive.auth)
  2. Zip $APPDATA/audio-transcriber/history/ (text-only, exclude *.wav/*.mp3/*.m4a)
  3. Redact API keys from config.json
  4. Build SHA-256 + size manifest
  5. Upload zip + redacted config + manifest to
     audio-transcriber-backup/<ISO-ts>/ on Google Drive

Progress notification: "current_file" / "total_files" emitted per file.
```

## 6. Persistence model

### 6.1 Layout (v0.2)

| Artifact | Location | Format | Notes |
|---|---|---|---|
| Settings | `$APPDATA/audio-transcriber/config.json` | JSON | Mirrors v0.1 schema. See §8 for migration. |
| API keys (default) | Inline in `config.json` | string fields | Same as v0.1. |
| API keys (opt-in) | OS keychain via Tauri `keyring` plugin | binary | New in v0.2. Settings toggle: "Хранить ключи в OS keychain". Default OFF for v0.1 compat. |
| History runs | `$APPDATA/audio-transcriber/history/<run_id>/` | flat files | Mirrors v0.1: `transcript.txt`, `tasks.json`, `protocol.md`, `audio.webm` |
| Recordings (temp) | `$APPDATA/audio-transcriber/recordings/<uuid>.webm` | WebM/Opus | New in v0.2 — lives until user confirms save into history. |
| GDrive OAuth token | `~/.audio-transcriber/gdrive-token.json` | JSON | Unchanged from v0.1 (outside config.json — backup excludes itself). |
| Logs | `$APPDATA/audio-transcriber/logs/app.log` | text rotating | Lifted from v0.1 `logging_setup.py`. |

### 6.2 Why `$APPDATA` and not "next to .exe"

v0.1 PyInstaller bundles in `C:\Apps\AudioTranscriber\` are user-writable
beside the .exe. v0.2 Tauri ships as MSI installer to
`C:\Program Files\AudioTranscriber\` (system-wide, read-only for standard
user). User data MUST live in `$APPDATA\Roaming\audio-transcriber\` —
standard Windows convention.

On macOS: `~/Library/Application Support/audio-transcriber/`.
On Linux: `$XDG_DATA_HOME/audio-transcriber/` (default
`~/.local/share/audio-transcriber/`).

Tauri's `app_data_dir()` API resolves all three.

### 6.3 What's NOT in v0.2 persistence

- ❌ SQLite index — overkill for parity scope; flat files suffice.
- ❌ Vault layout (Obsidian-style nested project/meeting folders) —
  deferred to Phase 2 (matches big-bang spec §3.5).
- ❌ Embeddings database — no RAG in v0.2.
- ❌ `meetings.toml` per-meeting metadata — deferred.

## 7. Build & Distribution

### 7.1 Cross-platform build via GitHub Actions

```yaml
# .github/workflows/release.yml (sketch — full version in implementation plan)
name: Release
on:
  push:
    tags: ['v0.2.*']
jobs:
  build:
    strategy:
      matrix:
        os: [windows-latest, macos-latest, ubuntu-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.10' }
      - name: Build Python sidecar (PyInstaller --onedir)
        run: |
          pip install -r python-sidecar/requirements.txt pyinstaller==6.10.0
          pyinstaller python-sidecar/audio_transcriber_sidecar.spec
          # Output: python-sidecar/dist/audio-transcriber-core/
      - name: Stage sidecar into Tauri binaries
        run: |
          # Tauri convention: src-tauri/binaries/audio-transcriber-core-{target_triple}.exe
          # Where target_triple = x86_64-pc-windows-msvc | x86_64-apple-darwin | x86_64-unknown-linux-gnu
          ...
      - uses: tauri-apps/tauri-action@v0
        env:
          TAURI_PRIVATE_KEY: ${{ secrets.TAURI_PRIVATE_KEY }}     # for updater (v0.2 stable)
          TAURI_KEY_PASSWORD: ${{ secrets.TAURI_KEY_PASSWORD }}
        with:
          tagName: v0.2.${{ github.ref_name }}
          releaseName: 'Audio Transcriber v0.2'
          args: ${{ matrix.os == 'windows-latest' && '--target x86_64-pc-windows-msvc' || '' }}
```

Artifacts produced per release:

- Windows: `AudioTranscriber-0.2.0-x64-setup.msi` (~160-310 MB)
- macOS: `AudioTranscriber-0.2.0.dmg` (~160-310 MB)
- Linux: `AudioTranscriber-0.2.0.AppImage` (~160-310 MB)

### 7.2 Tauri sidecar mechanism

`tauri-plugin-shell` resolves sidecar binaries by the
`{name}-{target_triple}` filename convention:

```
src-tauri/binaries/
  ├── audio-transcriber-core-x86_64-pc-windows-msvc.exe
  ├── audio-transcriber-core-x86_64-apple-darwin
  ├── audio-transcriber-core-aarch64-apple-darwin
  └── audio-transcriber-core-x86_64-unknown-linux-gnu
```

`Command::new_sidecar("audio-transcriber-core")` picks the right binary
at runtime based on `target_triple`. Each binary is a self-contained
PyInstaller `--onedir` (with `_internal/` of Python deps + ffmpeg).

### 7.3 Code signing (pre-v1.0 stance)

- **Windows v0.2 alpha:** ship unsigned. Document SmartScreen warning
  in `CLIENT_SETUP-v0.2.md` ("More info → Run anyway"). Add `C:\Program
  Files\AudioTranscriber\` to Defender exclusions per v0.1 pattern.
- **Windows v0.2 stable:** procure EV certificate ($150-300/year). One-time
  founder-task ~1-2 weeks (provider verification).
- **macOS v0.2 alpha:** unsigned + Gatekeeper bypass instructions
  (`xattr -d com.apple.quarantine /Applications/AudioTranscriber.app`).
- **macOS v0.2 stable:** Apple Developer Program ($99/year) + Developer
  ID Application cert + notarization through `xcrun notarytool`.
- **Linux:** AppImage unsigned for alpha + stable; optional GPG sign.

### 7.4 Auto-updater

Deferred to v0.2 stable. `tauri-plugin-updater` reads a static JSON
manifest on GitHub Pages or S3, compares versions, downloads + signs
the new artifact. Requires the Tauri signing keypair from §7.1 GH
Actions secrets.

## 8. Migration v0.1 → v0.2

### 8.1 Detect-and-copy flow

On v0.2 first launch:

```
1. v0.2 checks $APPDATA/audio-transcriber/config.json
   ↓ if exists → proceed normally (already migrated or fresh install)
   ↓ if missing → enter migration probe
2. Probe for v0.1 layout:
   - Windows: C:\Apps\AudioTranscriber\{config.json, history/}
   - Windows fallback glob: C:\Apps\*\AudioTranscriber\{config.json, history/}
   - macOS: ~/Applications/AudioTranscriber/{config.json, history/}  (if user dragged the bundle)
   - Linux: ~/audio-transcriber/{config.json, history/}  (zip extraction)
   ↓ if not found → show first-run banner (empty config, prompt user to Settings)
   ↓ if found → show migration dialog
3. Migration dialog (React):
   "Найдена установка v0.1 в {path}. Импортировать настройки + историю?"
   [Импортировать] [Начать с нуля] [Указать другой путь]
4. If "Импортировать":
   - copy config.json → $APPDATA/audio-transcriber/config.json
   - copy history/ → $APPDATA/audio-transcriber/history/   (may be many MB)
   - leave v0.1 install intact (user removes manually if desired)
5. Toast: "Импорт завершён. v0.1 можно удалить в {path}."
```

### 8.2 What is NOT automated

- ❌ v0.1 uninstall — risky to auto-delete; user keeps the v0.1 install
  until comfortable with v0.2.
- ❌ Bidirectional sync v0.1 ↔ v0.2 — out of scope. This is a cutover,
  not a coexistence period.
- ❌ Audio file copy by default — only `history/<run_id>/audio.{webm,mp3,wav,m4a}`
  copies if present. Standalone audio in the v0.1 directory is left.

### 8.3 GDrive OAuth token

Lives at `~/.audio-transcriber/gdrive-token.json` in both v0.1 and v0.2 —
the path is OS-user-scoped, not app-scoped. No migration needed: v0.2
reads from the same location and continues to work.

## 9. Timeline / Sequencing

Assuming MVP ship completes by end of week 1:

| Week | Phase | Outputs |
|---|---|---|
| 0 (current, 2026-05-28) | MVP code-complete | Commits `1577e40` + `03d43ce` on `main` |
| 1 (2026-05-29 → 2026-06-04) | MVP ship (Tasks 8 + 9) | `.zip` to 3 clients, tag `v0.1.0-mvp-cloud-only`, smoke on clean VM |
| 2 (2026-06-05 → 2026-06-11) | Feedback + plan-writing | Feedback log from 3 clients; `superpowers:writing-plans` → `docs/superpowers/plans/2026-06-XX-tauri-lite-rewrite.md`; scaffold Tauri + React monorepo (`src/`, `src-tauri/`, `python-sidecar/`) |
| 3-4 (2026-06-12 → 2026-06-25) | Foundation | `sidecar_main.py` + 10 JSON-RPC methods; `pydantic-to-typescript` generator + pre-commit; Tauri Rust core (spawn/relay/event-emit); TanStack Router 5 routes (`/`, `/history`, `/history/$runId`, `/audio-cutter`, `/settings`); Recording component (Web Audio API + wavesurfer.js); first-run banner |
| 5-6 (2026-06-26 → 2026-07-09) | Feature parity sweep | Settings dialog (4 sections); Transcribe flow + streaming progress UI; Extract tasks + protocol UI; History list + detail view; Audio cutter (React UI + Python ffmpeg subprocess); GDrive backup integration |
| 7 (2026-07-10 → 2026-07-16) | Polish + migration | Migration dialog v0.1 → v0.2; error states + retry UX; Windows MSI build flow (signing skipped for alpha); first cross-platform smoke (Windows + macOS minimum) |
| 8 (2026-07-17 → 2026-07-23) | QA + ship v0.2 alpha | Cross-platform smoke pass (Win/Mac/Linux); ship v0.2.0-alpha to 3 clients; feedback collection → v0.2.0-stable patches |

Total: ~7 weeks of lite-rewrite work after MVP ships, ~8 weeks calendar
from today to v0.2 alpha in client hands.

### 9.1 Hard gate at week 4

If the end-of-week-4 Foundation milestone does NOT have a working
end-to-end transcribe flow on macOS (recording → Python sidecar → cloud
API → segments rendered in React), re-scope:

- Option A: defer macOS + Linux to v0.3; ship v0.2 alpha Windows-only.
- Option B: extend Foundation by 1 week, push v0.2 alpha to week 9.

The decision should be made at the week-4 check, not later — schedule
slip detection is cheaper early.

## 10. Risks

Ranked by `probability × impact`:

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | macOS WKWebView Web Audio quirks (chunk size, sample rate negotiation) | High | Medium | Hard gate at week 4 (§9.1); fallback to Windows-only v0.2 alpha |
| 2 | PyInstaller-bundled Python sidecar ~150-300 MB → MSI ~160-310 MB | Guaranteed | Low-medium | User-side comms: "160-310 MB installer" in `CLIENT_SETUP-v0.2.md`. Possible v0.3 fix: `python-embed` minimal Python |
| 3 | Pydantic ↔ TS contract drift | Medium | Medium | Pre-commit hook regenerates TS types; CI fails on diff |
| 4 | Windows EV cert procurement delay (~1-2 weeks) | Medium | Medium | v0.2 alpha ships unsigned + SmartScreen warning; v0.2 stable requires cert |
| 5 | Python sidecar hangs/crashes without stderr | Low | High | Faulthandler enabled in `sidecar_main.py` (CLAUDE.md invariant #1 carry-over); Tauri restart policy + crash log in `%TEMP%/audio-transcriber-sidecar-crash.log` |
| 6 | shadcn/ui v4 component churn during 7-week build | Low | Low | Lock-step `git tag` checkout of shadcn — components live in repo, not npm liability |
| 7 | OS keychain integration headaches (Linux secret-service daemon variance) | Medium | Low | Keychain is opt-in via Settings toggle; default stays in `config.json` |
| 8 | TanStack Router file-based routes confusion (early-adopter docs) | Low | Low | Fallback to declarative routes if blocking; one route file per page |
| 9 | Web Audio MediaRecorder produces unplayable WebM on some OSs | Low | Medium | Validate output via ffprobe before save; fallback to WAV via OfflineAudioContext if invalid |

## 11. Out of scope (deferred)

### 11.1 Backend features (parity to Tauri big-bang spec §14)

These are all in the big-bang spec but explicitly **not** in v0.2 lite-rewrite:

- Voice library + speaker enrollment (ECAPA-TDNN embeddings)
- Vault layout (Obsidian-style nested `<vault>/<Project>/<Meeting>/`)
- RAG chat over history (`sqlite-vec` + OpenAI text-embedding-3-small)
- 8-pass LLM pipeline (v0.2 stays at 2 passes: tasks + protocol)
- Additional task backends: Notion, Jira, Яндекс Трекер, Битрикс24, GitHub Projects v2, generic webhook
- MCP server (stdio + HTTPS surface for external agents)
- MCP client (consume external MCP servers)
- Email distribution (SES + opt-in Gmail/Outlook OAuth)
- Telegram distribution (`@AudioTranscriberBot`)
- Managed SaaS backend (FastAPI + Postgres + Supabase + Stripe)
- Cross-device sync (vault-in-cloud-folder works out-of-the-box; app-mediated sync deferred)
- Frontmatter convention for all `.md` artifacts
- Wiki-link convention (`[[Project/Meeting]]`)
- Audio editor advanced features (multi-region trim, noise reduction, normalization)

### 11.2 UX/UI continuous polish (Phase 2 track)

v0.2 lite-rewrite already gives a big UI bump (CustomTkinter → shadcn/ui)
but stays at **feature parity** — no new UX paradigms. The following are
explicitly deferred to a **Phase 2 UX/UI track** after v0.2 alpha:

- Full **design system** (typography scale, color palette, motion language, spacing system)
- **Custom theming** (multiple light/dark variants, accent colors, user-pickable themes)
- **Animated transitions** (page transitions, micro-interactions, loading skeletons, hover states)
- **Advanced accessibility** (keyboard navigation audit, screen reader support, ARIA-compliance review)
- UX-fичи deferred from v0.2 parity:
  - **Drag-drop on the whole window** (currently only via file dialog)
  - **Waveform-player** in transcript viewer (synced with segment timestamps, click to seek)
  - **Markdown-editor** for protocol (currently read-only)
  - **In-line transcript edit** with speaker reassignment (currently read-only)
  - **Multi-select operations** on history (bulk delete, bulk export)
- **Onboarding flow** (first-run wizard, in-app tutorials, progressive disclosure)
- **Multi-window** support (transcript detail in separate window for side-by-side review)
- **Customizable layout** (dock/undock panels, panel grouping, saved workspaces)
- **Locale-aware UI** (currently Russian hardcoded; future: pick at install or detect from OS)

Phase 2 cadence: starts after v0.2 alpha feedback collection (≥1 week)
and runs as continuous polish rather than a big-bang second phase —
release small UX improvements every 1-2 weeks as `v0.2.x` patch releases.

## 12. Open questions (for plan-writing time)

These were not resolved in the brainstorm and should be addressed when
`superpowers:writing-plans` produces the implementation plan:

1. **Audio cutter UI fidelity** — react-wavesurfer-based timeline editor
   is the obvious choice, but the ffmpeg subprocess for actual trim
   stays in Python. Need to spec the React ↔ Python contract for
   "preview at offset X" + "export with cuts at [(start, end), ...]".
2. **Settings dialog form library** — react-hook-form likely (per
   big-bang §2.1 deferred), but parity Settings has ~12 fields total —
   bare controlled inputs may suffice. Decide at first form-heavy
   component implementation.
3. **History list pagination / virtualization** — at how many entries
   does the list need windowing? `react-window` adds complexity. Skip
   until ~100 entries.
4. **Error toast vs banner UX** — Sonner vs shadcn `<Toast>` vs full
   banner. Pick one and use everywhere.
5. **Python sidecar version compatibility** — should v0.2 Tauri refuse
   to launch if it detects a newer Python sidecar version it can't
   speak to (forward compat)? In-process bundle so unlikely to drift,
   but the JSON-RPC schema has implicit versioning.
6. **Reuse of MVP v0.1 PyInstaller spec** — `audio_transcriber.spec`
   (v0.1) needs adapting for sidecar mode (no GUI, no Tk, different
   entry point). Branch from it vs rewrite from scratch — both viable.

## 13. Glossary

- **Lite-rewrite** — UI-layer rewrite preserving the proven Python
  backend; contrast with the big-bang spec's full managed-SaaS rewrite.
- **Sidecar** — Python subprocess launched and managed by Tauri via
  `tauri-plugin-shell` Command::new_sidecar.
- **JSON-RPC 2.0** — line-protocol IPC format per
  <https://www.jsonrpc.org/specification>. Notifications (no `id`) =
  one-way messages; requests (`id` present) expect responses.
- **`$APPDATA`** — Tauri's `app_data_dir()` resolved per OS: Windows
  `%APPDATA%\Roaming\audio-transcriber\`, macOS
  `~/Library/Application Support/audio-transcriber/`, Linux
  `$XDG_DATA_HOME/audio-transcriber/`.
- **Feature parity 1-to-1** — every user-visible feature shipped in
  v0.1 MVP also ships in v0.2 alpha; no new features in cutover.
- **Cutover** — point in time when v0.2 alpha lands and clients migrate;
  no extended v0.1/v0.2 coexistence period.
- **Phase 2 (in this spec)** — work that follows v0.2 alpha. Distinct
  from "Phase 2" in `2026-05-26-tauri-saas-migration-design.md` which
  refers to managed-SaaS deferred features.
- **Hard gate** — explicit checkpoint where scope can be reduced if
  velocity falls behind. v0.2 hard gate at end of week 4.
