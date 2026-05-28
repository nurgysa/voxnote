# Tauri Lite-Rewrite — Phase 1: Foundation Implementation Plan (v2, post-Codex review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. (Subagent-driven dispatch is blocked in this environment per memory `feedback_subagent_dispatch_blocked_by_mcp_overhead` — inline execution with TDD discipline.) Steps use checkbox (`- [ ]`) syntax for tracking.

**Changes from v1 (post-Codex review 2026-05-28, 9 P1 + 5 P2 findings, all confirmed against v0.1 code):**

- **Sidecar build switched to PyInstaller `--onefile`** (Task 14). The plan v1 used `--onedir`, which produces an exe + sibling `_internal/` directory that doesn't fit Tauri 2's `externalBin` single-file contract (Codex P1 finding #1). `--onefile` packs everything into one `.exe` at `binaries/audio-transcriber-core-<triple>.exe` — slower startup (~1-3s for runtime archive extraction) but trivial Tauri staging.
- **`_handle_transcribe` now forwards `min_speakers` + `max_speakers`** (Task 4, Task 12 schema). v1 dropped these AssemblyAI-supported knobs that v0.1 `Transcriber.transcribe` accepts (Codex P1 finding #2).
- **`_handle_extract_tasks` serialises `Task` dataclasses + preserves all 7 return keys** (Task 5, Task 12). v1 returned raw `extract()` output, but v0.1 puts `Task` dataclass instances in `result["tasks"]` (not JSON-serialisable as-is) and includes `raw_response`/`members`/`labels` keys the v1 schema dropped. `corrections` is an `int` (count), not `list[dict]` (Codex P1 findings #3, #4).
- **`GenerateProtocolResult` schema rewritten to match `ProtocolResult`** (Task 6, Task 12). v1 had `markdown` + `blocks` + `usage`; actual dataclass at `tasks/protocol_generator.py:35` is `markdown` + `raw_llm_response` + `placeholders: Placeholders` — no usage field (Codex P1 finding #5).
- **`_handle_send_tasks` rewritten end-to-end** (Task 7). v1 splatted JSON into `send_tasks_iter(**params)`, but `tasks/sender.py:42` takes `list[Task]` + `container_id` + `backend` (BackendProtocol instance) + `on_status_change` + `cancel_check` + `retry_failed`. Handler now reconstructs Task via `Task.from_dict()`, instantiates `LinearBackend`/`GlideBackend` by name, wires cancel_check to `_cancel_event`, and yields `Task.to_dict()` (Codex P1 finding #6).
- **`_handle_gdrive_backup` calls `auth.load_tokens()` + `is_signed_in()` explicitly** (Task 8). v1 assumed `GDriveAuth()` constructor read cached creds — but `gdrive/auth.py:55` docstring explicitly says the constructor doesn't touch disk (Codex P1 finding #7).
- **`CommandChild.clone()` removed** (Task 16). `tauri-plugin-shell` 2's `CommandChild` is not `Clone`; the writer task now owns the single child handle exclusively. Cancel still works because it sends a normal JSON-RPC message via the mpsc (Codex P1 finding #8).
- **Tauri 2 capabilities file added** (Task 15 Step 3.5). Tauri 2 moved plugin permissions out of `tauri.conf.json::plugins` into `src-tauri/capabilities/<name>.json`. v1's `plugins.shell.scope` + `plugins.fs.scope` were ignored at runtime (Codex P1 finding #9 + P2 finding #10).
- **`_handle_list_history` derives timestamp from folder name + reads `description.md` instead of `meta.json`** (Task 9). v1 assumed a `meta.json` file that doesn't exist — v0.1 `utils.py:122-125` writes folders as `{ISO-timestamp}_{audio_base}/` with `transcript.txt` + optional `description.md` (Codex P2 finding #11).
- **Transcribe progress normalised 0..100 → 0..1 in the handler** (Task 4). v0.1 cloud providers emit 0..100 percentages (`providers/base.py:125`); the React store + UI work in 0..1 (Codex P2 finding #12).
- **`_handle_trim_audio` null-checks `get_ffmpeg_path()`** (Task 10). The helper returns `str | None` (Codex P2 finding #13).
- **Manual smoke in Task 17 imports `tauri::Manager`** for `.state()` (Codex P2 finding #14).

---

**Goal:** Build the Foundation of the Tauri lite-rewrite — a Python JSON-RPC sidecar, a minimal Tauri Rust bridge, and a React scaffold capable of demonstrating a working end-to-end transcribe flow (record → sidecar → cloud API → segments rendered in React).

**Architecture:** Three-tier bundle per spec §4 (Tauri WebView2/WKWebView/WebKitGTK + Rust core + PyInstaller sidecar). Phase 1 builds the FRAME — every JSON-RPC handler the sidecar exposes (12 methods), the Rust bridge that proxies them, the React skeleton with 5 route shells, the Recording component, and the first-run banner. Phase 2 (Feature parity sweep) builds per-feature UI on top of this frame.

**Tech Stack:** Tauri 2 + Rust + Python 3.10 (sidecar) + React 19 + TypeScript 5.x + Vite 6.x + Tailwind v4.x + shadcn/ui + Sonner + TanStack Router 1.x + TanStack Query 5.x + Zustand 5.x + Vitest 3.x + pnpm 9.x.

**Decisions resolved from spec §12 open questions (2026-05-28):**

- **Q4 Error UX → Sonner.** Single toast library imported via `sonner@^1.7.0`. Banner-style `FirstRunBanner` is a separate React component (not a Sonner toast) because banners are persistent UI affordances, not transient notifications.
- **Q5 Sidecar version compat → ping returns version + log warning on mismatch.** `_handle_ping` returns `{pong: true, version: "0.2.0"}`. Tauri Rust core compares the version to its own `CARGO_PKG_VERSION` env and logs a `tracing::warn!` if the major versions differ, but does **not** block startup. Rationale: sidecar is bundled in the same artifact as Tauri (PyInstaller `--onefile` staged into `src-tauri/binaries/`), so drift is unlikely; logging-only catches the unlikely case without adding friction.
- **Q6 PyInstaller spec → fresh.** New `python-sidecar/audio_transcriber_sidecar.spec` written from scratch with `console=True`, entry point `sidecar_main.py`, no CTk/Tk hidden imports. The v0.1 `audio_transcriber.spec` is consulted as reference for the ffmpeg vendoring + `runtime_hook_imports.py` pattern, but not copied wholesale.

---

## Context

This plan implements **weeks 3-4 of spec §9 timeline (Foundation phase)**. End-of-phase deliverable per spec §9.1: working end-to-end transcribe flow on macOS (recording → Python sidecar → cloud API → segments rendered in React).

**Prerequisite:** Per spec §3.3 hard gate, MVP v0.1 must be shipped to **at least 1 paying client** before Phase 1 implementation work begins. Plan-writing (this document) is fine now — no code is written until v0.1 lands. v0.1 = commit `1577e40` Task 7 "MVP code-complete" + `03d43ce` Task 9 partial onboarding doc.

**Subsequent phases** (separate plans, written later as Phase N+1 begins):

| Phase | Scope | When written |
|---|---|---|
| Phase 2 | Feature parity sweep (Settings, transcribe UI, tasks/protocol UI, history, audio cutter, GDrive button) | End of Phase 1 |
| Phase 3 | Polish (migration v0.1 → v0.2, error states, Windows MSI, cross-platform smoke) | End of Phase 2 |
| Phase 4 | QA + ship v0.2 alpha to 3 clients | End of Phase 3 |

**Out of scope for Phase 1** (lands in Phase 2-4):

- Settings dialog UI (4 sections) — Phase 2 (Phase 1 ships the `load_config`/`save_config` sidecar handlers only)
- Transcribe progress streaming UI (Phase 1 ships the data flow; Phase 2 polishes the streaming progress bar UI)
- Extract tasks UI + Protocol viewer UI — Phase 2 (Phase 1 ships the `extract_tasks`/`generate_protocol` handlers)
- History list + detail view UI — Phase 2 (Phase 1 ships the `list_history` handler)
- Audio cutter React UI — Phase 2 (Phase 1 ships the `trim_audio` handler)
- GDrive backup button — Phase 2 (Phase 1 ships the `gdrive_backup` handler)
- Migration v0.1 → v0.2 dialog — Phase 3
- Cross-platform CI builds (GitHub Actions matrix) — Phase 3
- WebdriverIO E2E tests — Phase 3
- Code signing / auto-updater — Phase 4 / v0.2 stable

**Repo convention:** v0.1 code stays in repo root (`app.py`, `transcriber/`, `providers/`, `tasks/`, `gdrive/`, `ui/`). v0.2 lives under three new top-level dirs: `src/` (React), `src-tauri/` (Tauri Rust), `python-sidecar/` (Python sidecar — a near-verbatim COPY of v0.1 modules). v0.1 stays buildable throughout Phase 1-3 so the `.zip` can be reissued to clients if needed. v0.1 deletion is a Phase 4 cleanup task.

**Test contract:** v0.1's 333-test baseline (see `CLAUDE.md`) stays green throughout Phase 1 — nothing in `python-sidecar/` or `src/` or `src-tauri/` should break the existing `pytest` invocation. Phase 1 adds two new test suites: `python-sidecar/tests/` (pytest) and `src/__tests__/` (Vitest). Rust unit tests live inline in `src-tauri/src/*.rs` per Cargo convention.

---

## File Structure

End-of-Phase-1 monorepo layout. Files marked **NEW** are created in this plan; **LIFTED** are copied verbatim from v0.1 (no edits); **MODIFIED** are touched in v0.1 too (rare — only the `.gitignore`).

```
audio-transcriber/                  # repo root
├── .gitignore                      # MODIFIED — add pnpm, node_modules, target/, dist/, src/lib/python-types.d.ts
│
├── src/                            # NEW — React 19 + TS frontend
│   ├── app/                        # TanStack Router file-based routes
│   │   ├── __root.tsx              # NEW — shell + global error boundary + Sonner <Toaster />
│   │   ├── index.tsx               # NEW — Home (FirstRunBanner + Recorder placeholder)
│   │   ├── history.tsx             # NEW — placeholder shell (Phase 2 fills it)
│   │   ├── history.$runId.tsx      # NEW — placeholder shell (Phase 2)
│   │   ├── audio-cutter.tsx        # NEW — placeholder shell (Phase 2)
│   │   └── settings.tsx            # NEW — placeholder shell (Phase 2)
│   ├── components/
│   │   ├── ui/                     # NEW — shadcn/ui primitives, copy-paste in-repo (button, card, dialog, input)
│   │   ├── Recorder.tsx            # NEW — MediaRecorder + wavesurfer.js live waveform + level meter
│   │   └── FirstRunBanner.tsx      # NEW — yellow banner when AssemblyAI key empty
│   ├── lib/
│   │   ├── ipc.ts                  # NEW — invokePython<T>() typed wrapper + python-event subscriber
│   │   ├── store.ts                # NEW — Zustand global store (config, recording state)
│   │   ├── schemas.ts              # NEW — Zod schemas mirroring Pydantic models (hand-written runtime validators)
│   │   └── python-types.d.ts       # NEW (gitignored) — GENERATED from Pydantic via pydantic-to-typescript
│   ├── hooks/
│   │   └── useTranscribe.ts        # NEW — TanStack Query mutation + python-event progress subscribe
│   ├── __tests__/
│   │   ├── ipc.test.ts             # NEW — Vitest unit tests for invokePython wrapper
│   │   └── store.test.ts           # NEW — Vitest tests for Zustand store
│   ├── main.tsx                    # NEW — React root + QueryClientProvider + RouterProvider
│   ├── index.html                  # NEW — Vite entry
│   └── styles.css                  # NEW — Tailwind v4 base
│
├── src-tauri/                      # NEW — Tauri 2 Rust core (~400 LOC)
│   ├── Cargo.toml                  # NEW — tauri 2, tauri-plugin-shell/fs/dialog, tokio, serde
│   ├── tauri.conf.json             # NEW — Tauri config (productName, identifier, build commands, sidecar binary refs)
│   ├── build.rs                    # NEW — tauri-build crate hook
│   ├── icons/                      # NEW — placeholder icons (replaced in Phase 4)
│   ├── binaries/                   # NEW (gitignored) — PyInstaller sidecar output staged here per target_triple
│   │   └── .gitkeep
│   ├── resources/
│   │   └── config.example.json     # COPIED at build time from repo root config.example.json
│   └── src/
│       ├── main.rs                 # NEW — Tauri app entry (~50 LOC)
│       ├── sidecar.rs              # NEW — sidecar spawn + JSON-RPC stdin/stdout pump (~200 LOC)
│       ├── commands.rs             # NEW — Tauri commands (invoke_python, save_recording, cancel_transcribe) (~80 LOC)
│       └── bootstrap.rs            # NEW — first-run config.example.json copy (~30 LOC)
│
├── python-sidecar/                 # NEW — Python sidecar package
│   ├── sidecar_main.py             # NEW — JSON-RPC dispatcher (~300 LOC)
│   ├── schemas.py                  # NEW — Pydantic v2 request/response models for JSON-RPC type contract
│   ├── providers/                  # LIFTED from repo-root providers/ verbatim
│   ├── tasks/                      # LIFTED from repo-root tasks/ verbatim
│   ├── gdrive/                     # LIFTED from repo-root gdrive/ verbatim
│   ├── transcriber/                # LIFTED from repo-root transcriber/ verbatim
│   ├── utils.py                    # LIFTED from repo-root utils.py verbatim
│   ├── logging_setup.py            # LIFTED from repo-root logging_setup.py verbatim
│   ├── transcript_format.py        # LIFTED from repo-root transcript_format.py verbatim
│   ├── audio_io.py                 # LIFTED from repo-root audio_io.py verbatim
│   ├── vendor/ffmpeg/              # LIFTED from repo-root vendor/ffmpeg/ (Windows binaries + macOS + Linux when available)
│   ├── runtime_hook_sidecar.py     # NEW — redirect None stdin/stderr/stdout to %TEMP%/audio-transcriber-sidecar-bootstrap.log
│   ├── audio_transcriber_sidecar.spec  # NEW — PyInstaller spec (console=True, sidecar_main.py entry)
│   ├── requirements.txt            # LIFTED from repo-root requirements.txt verbatim (~10 cloud-only deps)
│   ├── pyproject.toml              # NEW — sidecar-only ruff config + pytest paths
│   ├── pytest.ini                  # NEW — testpaths = tests
│   ├── scripts/
│   │   └── gen_ts_types.py         # NEW — pydantic-to-typescript codegen → src/lib/python-types.d.ts
│   └── tests/
│       ├── conftest.py             # NEW — shared fixtures (tmp_config_path, mock_openrouter_client)
│       ├── test_dispatch.py        # NEW — main loop + error response + method-not-found
│       ├── test_handle_ping.py     # NEW
│       ├── test_handle_transcribe.py        # NEW
│       ├── test_handle_extract_tasks.py     # NEW
│       ├── test_handle_generate_protocol.py # NEW
│       ├── test_handle_send_tasks.py        # NEW
│       ├── test_handle_gdrive_backup.py     # NEW
│       ├── test_handle_list_history.py      # NEW
│       ├── test_handle_trim_audio.py        # NEW
│       └── test_handle_config_io.py         # NEW — load_config + save_config + shutdown
│
├── package.json                    # NEW — top-level (pnpm workspaces, vite dev, tauri dev/build, vitest scripts)
├── pnpm-workspace.yaml             # NEW — workspaces = ['.']  (single-package monorepo for now)
├── tsconfig.json                   # NEW — strict mode, paths alias @/* -> src/*
├── vite.config.ts                  # NEW — React + Tauri integration (clearScreen: false, server.strictPort: true, server.port: 1420)
├── tailwind.config.ts              # NEW — content paths, theme extends
├── postcss.config.js               # NEW — tailwind/autoprefixer
├── components.json                 # NEW — shadcn/ui config
├── biome.json                      # NEW — Biome lint + format (replaces ESLint/Prettier per spec convention)
├── .pre-commit-config.yaml         # NEW — hook: python-sidecar/scripts/gen_ts_types.py + ruff
│
├── (existing v0.1 files — UNTOUCHED)
├── app.py, transcriber/, providers/, tasks/, gdrive/, ui/, ...
└── (existing v0.1 build artifacts)
    audio_transcriber.spec, runtime_hook_imports.py, build_exe.ps1
```

**v0.1 → python-sidecar/ lift mapping (Task 2):**

| v0.1 path | python-sidecar/ path | Method | Notes |
|---|---|---|---|
| `providers/` | `python-sidecar/providers/` | `cp -r` | 4 cloud providers + base ABC + __init__ registry |
| `tasks/` | `python-sidecar/tasks/` | `cp -r` | extractor, sender, protocol_generator, openrouter_client, linear_client, glide_client, schema, persistence, errors, backends/ |
| `gdrive/` | `python-sidecar/gdrive/` | `cp -r` | auth, client, backup |
| `transcriber/` | `python-sidecar/transcriber/` | `cp -r` | __init__.py + cloud_chunker.py (post-rip-out cloud-only layout) |
| `utils.py` | `python-sidecar/utils.py` | `cp` | Includes `load_config`, `save_config`, `get_ffmpeg_path`, `check_ffmpeg` |
| `logging_setup.py` | `python-sidecar/logging_setup.py` | `cp` | `get_logger(name)` factory |
| `transcript_format.py` | `python-sidecar/transcript_format.py` | `cp` | `format_diarized`, `format_timed` |
| `audio_io.py` | `python-sidecar/audio_io.py` | `cp` | `ensure_wav`, ffmpeg subprocess helpers |
| `vendor/ffmpeg/` | `python-sidecar/vendor/ffmpeg/` | `cp -r` | Bundled ffmpeg.exe + ffprobe.exe (Windows); macOS/Linux binaries added in Phase 3 |
| `requirements.txt` | `python-sidecar/requirements.txt` | `cp` | Cloud-only deps (~10 lines after the v5 rip-out) |

**Files NOT lifted** (v0.1 has them but sidecar doesn't need them): `ui/`, `app.py`, `audio_cutter.py` (UI portion — ffmpeg trim subprocess re-implemented inline in `_handle_trim_audio`), `recorder.py` (recording moves to Web Audio API in React), `audio_transcriber.spec` (sidecar gets a fresh spec), `runtime_hook_imports.py` (sidecar gets `runtime_hook_sidecar.py`).

---

## Tasks

### Task 1: Branch + monorepo dirs + .gitignore additions

**Files:**
- Create: `python-sidecar/.gitkeep`, `src/.gitkeep`, `src-tauri/.gitkeep` (placeholders so empty dirs can be committed)
- Modify: `.gitignore` (append node_modules, target/, dist/, src/lib/python-types.d.ts, src-tauri/binaries/*.exe, src-tauri/binaries/*-darwin, src-tauri/binaries/*-linux-gnu, .pnpm-store/)

- [ ] **Step 1: Create the topic branch**

```bash
git checkout main
git pull
git checkout -b feat/tauri-lite-rewrite-phase-1-foundation
```

- [ ] **Step 2: Create empty top-level dirs**

```bash
mkdir python-sidecar src src-tauri
touch python-sidecar/.gitkeep src/.gitkeep src-tauri/.gitkeep
```

- [ ] **Step 3: Update .gitignore**

Append these entries to `.gitignore` (preserve existing content):

```gitignore

# v0.2 lite-rewrite — added 2026-05-28
node_modules/
.pnpm-store/
dist/
src-tauri/target/
src-tauri/binaries/*
!src-tauri/binaries/.gitkeep
src/lib/python-types.d.ts
.vite/
*.tsbuildinfo
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore python-sidecar/ src/ src-tauri/
git commit -m "chore(v0.2): scaffold monorepo dirs + .gitignore additions"
```

---

### Task 2: Lift Python modules to python-sidecar/

**Files:**
- Copy from repo root to `python-sidecar/`: `providers/`, `tasks/`, `gdrive/`, `transcriber/`, `utils.py`, `logging_setup.py`, `transcript_format.py`, `audio_io.py`, `vendor/ffmpeg/`, `requirements.txt`
- Create: `python-sidecar/pyproject.toml`, `python-sidecar/pytest.ini`

- [ ] **Step 1: Copy modules verbatim**

PowerShell on Windows:

```powershell
Copy-Item -Recurse providers python-sidecar/providers
Copy-Item -Recurse tasks python-sidecar/tasks
Copy-Item -Recurse gdrive python-sidecar/gdrive
Copy-Item -Recurse transcriber python-sidecar/transcriber
Copy-Item -Recurse vendor python-sidecar/vendor
Copy-Item utils.py python-sidecar/utils.py
Copy-Item logging_setup.py python-sidecar/logging_setup.py
Copy-Item transcript_format.py python-sidecar/transcript_format.py
Copy-Item audio_io.py python-sidecar/audio_io.py
Copy-Item requirements.txt python-sidecar/requirements.txt
```

- [ ] **Step 2: Create python-sidecar/pyproject.toml**

Write `python-sidecar/pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "UP"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create python-sidecar/pytest.ini**

Write `python-sidecar/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 4: Verify lift — smoke import**

Run from `python-sidecar/` directory:

```bash
cd python-sidecar
python -c "from transcriber import Transcriber, TranscriptionCancelled; print('OK')"
python -c "from tasks.extractor import extract; from tasks.protocol_generator import generate; print('OK')"
python -c "from gdrive.backup import run_backup; print('OK')"
python -c "from providers import get_provider; print('OK')"
cd ..
```

Expected: each prints `OK`. If any fails — fix the import path in the copy (e.g. relative imports inside `tasks/` should still work because the entire package was copied as a unit).

- [ ] **Step 5: Verify v0.1 still works**

```bash
pytest
```

Expected: still green at 333 tests (the lift didn't touch v0.1 — defensive check).

- [ ] **Step 6: Commit**

```bash
git add python-sidecar/
git commit -m "chore(v0.2): lift Python modules to python-sidecar/ verbatim"
```

---

### Task 3: sidecar_main.py skeleton + ping handler + main loop (TDD)

**Files:**
- Create: `python-sidecar/sidecar_main.py`
- Create: `python-sidecar/tests/conftest.py`
- Create: `python-sidecar/tests/test_dispatch.py`
- Create: `python-sidecar/tests/test_handle_ping.py`

- [ ] **Step 1: Write the failing dispatch test**

Create `python-sidecar/tests/conftest.py`:

```python
"""Shared fixtures for sidecar tests.

The dispatcher reads stdin / writes stdout. Tests drive it by feeding lines
into a fake stdin and capturing what comes out via capsys.
"""
from __future__ import annotations

import io
import json
import sys
from typing import Iterator

import pytest


@pytest.fixture
def fake_stdin(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Replace sys.stdin with an in-memory buffer the test fills before main()."""
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdin", buf)
    return buf


def feed_request(stdin: io.StringIO, method: str, params: dict | None = None,
                 req_id: int | None = 1) -> None:
    """Append a JSON-RPC request line to the fake stdin buffer."""
    req: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        req["id"] = req_id
    if params is not None:
        req["params"] = params
    stdin.write(json.dumps(req) + "\n")
    stdin.seek(0)


def parse_responses(stdout: str) -> list[dict]:
    """Parse newline-delimited JSON responses written by the dispatcher."""
    return [json.loads(line) for line in stdout.strip().split("\n") if line.strip()]
```

Create `python-sidecar/tests/test_handle_ping.py`:

```python
"""ping handler — health check + sidecar version handshake (Q5 decision)."""
from __future__ import annotations

from tests.conftest import feed_request, parse_responses


def test_ping_returns_pong_and_version(fake_stdin, capsys):
    from sidecar_main import main

    feed_request(fake_stdin, "ping", req_id=1)
    main()

    out = capsys.readouterr().out
    responses = parse_responses(out)
    assert len(responses) == 1
    assert responses[0]["id"] == 1
    assert responses[0]["result"]["pong"] is True
    assert responses[0]["result"]["version"] == "0.2.0"
```

Create `python-sidecar/tests/test_dispatch.py`:

```python
"""Main dispatch loop — error responses, method-not-found, malformed JSON."""
from __future__ import annotations

import io

from tests.conftest import feed_request, parse_responses


def test_method_not_found_returns_jsonrpc_error(fake_stdin, capsys):
    from sidecar_main import main

    feed_request(fake_stdin, "nonexistent_method", req_id=42)
    main()

    responses = parse_responses(capsys.readouterr().out)
    assert responses[0]["id"] == 42
    assert responses[0]["error"]["code"] == -32601
    assert "nonexistent_method" in responses[0]["error"]["message"]


def test_malformed_json_is_skipped_silently(fake_stdin, capsys):
    """Malformed input has no id to correlate against — log + skip, do not crash."""
    from sidecar_main import main

    fake_stdin.write("this is not json\n")
    fake_stdin.seek(0)
    main()

    out = capsys.readouterr().out
    # The dispatcher must not have written anything (no id to respond to).
    assert out == "" or all("error" not in line for line in out.split("\n"))


def test_empty_line_skipped(fake_stdin, capsys):
    from sidecar_main import main

    fake_stdin.write("\n\n\n")
    fake_stdin.seek(0)
    main()

    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd python-sidecar
pytest tests/test_handle_ping.py tests/test_dispatch.py -v
```

Expected: ImportError or ModuleNotFoundError because `sidecar_main` doesn't exist yet.

- [ ] **Step 3: Implement sidecar_main.py skeleton**

Create `python-sidecar/sidecar_main.py`:

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
import traceback
from typing import Any, Callable

faulthandler.enable()  # do this FIRST — see CLAUDE.md invariant #1

from logging_setup import get_logger

logger = get_logger(__name__)

SIDECAR_VERSION = "0.2.0"


def _handle_ping(_params: dict[str, Any]) -> dict[str, Any]:
    """Health check + version handshake (spec §12 Q5 decision).

    Tauri Rust core calls this on startup and compares `version` to its own
    CARGO_PKG_VERSION. Mismatch → tracing::warn! (log-only, non-blocking).
    """
    return {"pong": True, "version": SIDECAR_VERSION}


DISPATCH: dict[str, Callable[[dict[str, Any]], Any]] = {
    "ping": _handle_ping,
}


def main() -> None:
    """Main dispatch loop — read stdin lines, dispatch, write responses."""
    logger.info("Sidecar started (version=%s), awaiting JSON-RPC on stdin", SIDECAR_VERSION)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("Malformed JSON request: %s", exc)
            continue  # cannot respond — no id to correlate against

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})

        if method not in DISPATCH:
            _write_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })
            continue

        try:
            result = DISPATCH[method](params)
            _write_response({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:
            logger.exception("Dispatch error for method=%s", method)
            _write_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": str(exc),
                    "data": {"traceback": traceback.format_exc()},
                },
            })


def _write_response(payload: dict[str, Any]) -> None:
    """Single chokepoint for writing JSON-RPC responses + notifications."""
    print(json.dumps(payload), flush=True)


def _emit_notification(method: str, params: dict[str, Any]) -> None:
    """Send a JSON-RPC notification (no id) — for streaming progress events."""
    _write_response({"jsonrpc": "2.0", "method": method, "params": params})


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd python-sidecar
pytest tests/test_handle_ping.py tests/test_dispatch.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Verify v0.1 baseline unaffected**

```bash
cd ..
pytest
```

Expected: still 333 tests green.

- [ ] **Step 6: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/
git commit -m "feat(sidecar): JSON-RPC dispatcher skeleton + ping handler + tests"
```

---

### Task 4: _handle_transcribe + cancel event (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py` (add `_handle_transcribe`, `_handle_cancel`, `_cancel_event` module global, register in DISPATCH)
- Create: `python-sidecar/tests/test_handle_transcribe.py`

**Context:** v0.1 `Transcriber.transcribe(...)` returns formatted `str` and populates `self.last_segments` (see `transcriber/__init__.py:60-87`). React needs the structured segments to render its own transcript view — so the handler returns BOTH `{formatted_text, segments}`. `on_progress` / `on_status` callbacks emit JSON-RPC notifications. `cancel_event` is a module-level `threading.Event` set by the `_handle_cancel` method (single per-process — only one transcribe at a time in Phase 1).

- [ ] **Step 1: Write failing tests**

Create `python-sidecar/tests/test_handle_transcribe.py`:

```python
"""_handle_transcribe — wraps Transcriber.transcribe, emits progress notifications."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import feed_request, parse_responses


@patch("sidecar_main.Transcriber")
def test_transcribe_returns_formatted_text_and_segments(MockTranscriber, fake_stdin, capsys):
    from sidecar_main import main

    instance = MockTranscriber.return_value
    instance.transcribe.return_value = "[00:00] Привет\n[00:05] Мир"
    instance.last_segments = [
        {"start": 0.0, "end": 5.0, "text": "Привет", "speaker": "A"},
        {"start": 5.0, "end": 10.0, "text": "Мир", "speaker": "B"},
    ]

    feed_request(fake_stdin, "transcribe", {
        "audio_path": "test.webm",
        "cloud_provider": "AssemblyAI",
        "cloud_api_key": "fake-key",
        "diarize": True,
    }, req_id=10)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 10)
    assert final["result"]["formatted_text"].startswith("[00:00]")
    assert len(final["result"]["segments"]) == 2
    assert final["result"]["segments"][0]["text"] == "Привет"


@patch("sidecar_main.Transcriber")
def test_transcribe_emits_progress_notifications(MockTranscriber, fake_stdin, capsys):
    """on_progress callback → JSON-RPC notification (no id)."""
    from sidecar_main import main

    instance = MockTranscriber.return_value
    instance.last_segments = []

    def fake_transcribe(**kwargs):
        kwargs["on_progress"](0.25)
        kwargs["on_status"]("Uploading...")
        kwargs["on_progress"](0.75)
        return ""

    instance.transcribe.side_effect = fake_transcribe

    feed_request(fake_stdin, "transcribe", {
        "audio_path": "x.webm",
        "cloud_provider": "AssemblyAI",
        "cloud_api_key": "k",
    }, req_id=11)
    main()

    responses = parse_responses(capsys.readouterr().out)
    notifications = [r for r in responses if "id" not in r]
    assert {"method": "progress", "params": {"pct": 0.25}, "jsonrpc": "2.0"} in notifications
    assert {"method": "status", "params": {"message": "Uploading..."}, "jsonrpc": "2.0"} in notifications


@patch("sidecar_main.Transcriber")
def test_transcribe_cancelled_returns_32001(MockTranscriber, fake_stdin, capsys):
    from sidecar_main import main
    from transcriber import TranscriptionCancelled

    instance = MockTranscriber.return_value
    instance.transcribe.side_effect = TranscriptionCancelled()

    feed_request(fake_stdin, "transcribe", {
        "audio_path": "x.webm",
        "cloud_provider": "AssemblyAI",
        "cloud_api_key": "k",
    }, req_id=12)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 12)
    assert final["error"]["code"] == -32001
    assert "ancel" in final["error"]["message"].lower()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd python-sidecar
pytest tests/test_handle_transcribe.py -v
```

Expected: AttributeError or "Method not found: transcribe".

- [ ] **Step 3: Implement _handle_transcribe + cancel**

Edit `python-sidecar/sidecar_main.py` — add these AFTER `_handle_ping` and BEFORE `DISPATCH`:

```python
import threading

from transcriber import Transcriber, TranscriptionCancelled

# Single per-process cancel event — set by the "cancel" method, observed by
# Transcriber via the cancel_event callback. Phase 1 supports only one
# transcribe at a time (matches v0.1 UI behaviour).
_cancel_event = threading.Event()


def _handle_transcribe(params: dict[str, Any]) -> dict[str, Any]:
    """Wrap Transcriber.transcribe; return structured segments + formatted text.

    v0.1 Transcriber.transcribe(...) returns formatted str and populates
    self.last_segments. React wants structured segments — we return both.
    """
    _cancel_event.clear()
    transcriber = Transcriber()

    formatted_text = transcriber.transcribe(
        audio_path=params["audio_path"],
        language=params.get("language"),
        diarize=params.get("diarize", False),
        hotwords=params.get("hotwords"),
        num_speakers=params.get("num_speakers"),
        min_speakers=params.get("min_speakers"),
        max_speakers=params.get("max_speakers"),
        denoise_audio=params.get("denoise_audio", False),
        cloud_provider=params["cloud_provider"],
        cloud_api_key=params["cloud_api_key"],
        # v0.1 providers emit progress as 0..100 (see providers/base.py:125 +
        # transcriber/__init__.py:226). Normalise to 0..1 here so the React
        # store + UI assume a single scale. Status messages pass through.
        on_progress=lambda pct: _emit_notification("progress", {"pct": pct / 100.0}),
        on_status=lambda msg: _emit_notification("status", {"message": msg}),
        cancel_event=_cancel_event,
    )

    return {
        "formatted_text": formatted_text,
        "segments": transcriber.last_segments or [],
    }


def _handle_cancel(_params: dict[str, Any]) -> dict[str, bool]:
    """Set the per-process cancel event observed by Transcriber."""
    _cancel_event.set()
    return {"cancelled": True}
```

Update the DISPATCH dict:

```python
DISPATCH: dict[str, Callable[[dict[str, Any]], Any]] = {
    "ping": _handle_ping,
    "transcribe": _handle_transcribe,
    "cancel": _handle_cancel,
}
```

Update the `except` block in `main()` to handle `TranscriptionCancelled` BEFORE the generic `except Exception`:

```python
        try:
            result = DISPATCH[method](params)
            _write_response({"jsonrpc": "2.0", "id": req_id, "result": result})
        except TranscriptionCancelled:
            _write_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32001, "message": "Cancelled by user"},
            })
        except Exception as exc:
            logger.exception("Dispatch error for method=%s", method)
            _write_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": str(exc),
                    "data": {"traceback": traceback.format_exc()},
                },
            })
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_handle_transcribe.py tests/test_handle_ping.py tests/test_dispatch.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_transcribe.py
git commit -m "feat(sidecar): _handle_transcribe + _handle_cancel + cancel_event"
```

---

### Task 5: _handle_extract_tasks (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py` (add `_handle_extract_tasks`, register in DISPATCH)
- Create: `python-sidecar/tests/test_handle_extract_tasks.py`

**Context:** v0.1 `tasks.extractor.extract(*, transcript, model, lang, openrouter_client, members, labels, team_id, linear_client)` returns a dict at `tasks/extractor.py:302` with these keys: `tasks` (list[Task] — `tasks.schema.Task` **dataclasses**, not plain dicts), `corrections` (int — count of self-corrections), `usage` (dict), `model` (str), `raw_response` (str), `members` (list[dict]), `labels` (list[dict]). The handler must call `Task.to_dict()` per task to make them JSON-serialisable, and PRESERVE all 7 keys (not just `tasks`/`corrections`). The handler constructs `OpenRouterClient` from `openrouter_api_key` param + optional `LinearClient` from `linear_api_key`.

- [ ] **Step 1: Write failing tests**

Create `python-sidecar/tests/test_handle_extract_tasks.py`:

```python
"""_handle_extract_tasks — wraps tasks.extractor.extract with constructed clients."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.conftest import feed_request, parse_responses


def _fake_task(title: str, **kwargs) -> MagicMock:
    """Build a mock Task dataclass with to_dict() returning a plain dict.

    The real Task lives at tasks/schema.py::Task — we don't need its full surface
    in unit tests, just the to_dict contract the handler depends on.
    """
    t = MagicMock()
    t.to_dict.return_value = {"title": title, **kwargs}
    return t


@patch("sidecar_main._extract")
@patch("sidecar_main.OpenRouterClient")
def test_extract_constructs_openrouter_client_and_serialises_tasks(
    MockOR, mock_extract, fake_stdin, capsys
):
    from sidecar_main import main

    # Real extract() returns the full 7-key dict — match that shape.
    mock_extract.return_value = {
        "tasks": [_fake_task("T1", assignee="Alice")],
        "corrections": 0,
        "usage": {"total_tokens": 1234},
        "model": "gpt-4o",
        "raw_response": "...llm raw...",
        "members": [],
        "labels": [],
    }

    feed_request(fake_stdin, "extract_tasks", {
        "transcript": "...",
        "model": "gpt-4o",
        "openrouter_api_key": "or-key",
        "language": "ru",
    }, req_id=20)
    main()

    MockOR.assert_called_once_with(api_key="or-key")
    mock_extract.assert_called_once()
    call_kwargs = mock_extract.call_args.kwargs
    assert call_kwargs["transcript"] == "..."
    assert call_kwargs["model"] == "gpt-4o"
    assert call_kwargs["lang"] == "ru"
    assert call_kwargs["linear_client"] is None  # no linear_api_key passed

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 20)
    # Handler must serialise Task dataclasses + preserve ALL 7 dict keys.
    assert final["result"]["tasks"] == [{"title": "T1", "assignee": "Alice"}]
    assert final["result"]["corrections"] == 0
    assert final["result"]["usage"]["total_tokens"] == 1234
    assert final["result"]["model"] == "gpt-4o"
    assert final["result"]["raw_response"] == "...llm raw..."
    assert final["result"]["members"] == []
    assert final["result"]["labels"] == []


@patch("sidecar_main._extract")
@patch("sidecar_main.LinearClient")
@patch("sidecar_main.OpenRouterClient")
def test_extract_constructs_linear_client_when_key_present(
    MockOR, MockLinear, mock_extract, fake_stdin, capsys
):
    from sidecar_main import main

    mock_extract.return_value = {
        "tasks": [],
        "corrections": 0,
        "usage": {},
        "model": "gpt-4o",
        "raw_response": "",
        "members": [{"name": "Alice"}],
        "labels": [{"name": "bug"}],
    }

    feed_request(fake_stdin, "extract_tasks", {
        "transcript": "x",
        "model": "gpt-4o",
        "openrouter_api_key": "or-key",
        "linear_api_key": "lin-key",
        "team_id": "TEAM_X",
        "members": [{"name": "Alice"}],
        "labels": [{"name": "bug"}],
    }, req_id=21)
    main()

    MockLinear.assert_called_once_with(api_key="lin-key")
    call_kwargs = mock_extract.call_args.kwargs
    assert call_kwargs["members"] == [{"name": "Alice"}]
    assert call_kwargs["labels"] == [{"name": "bug"}]
    assert call_kwargs["team_id"] == "TEAM_X"
    assert call_kwargs["linear_client"] is MockLinear.return_value
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_handle_extract_tasks.py -v
```

Expected: ModuleNotFoundError or "Method not found".

- [ ] **Step 3: Implement _handle_extract_tasks**

Edit `python-sidecar/sidecar_main.py`. Add these imports near the top (after `from transcriber import ...`):

```python
from tasks.extractor import extract as _extract
from tasks.linear_client import LinearClient
from tasks.openrouter_client import OpenRouterClient
```

Add handler after `_handle_cancel`:

```python
def _handle_extract_tasks(params: dict[str, Any]) -> dict[str, Any]:
    """Wrap tasks.extractor.extract; construct OpenRouter + optional Linear clients.

    v0.1 extract() returns Task dataclass instances in `result["tasks"]` (see
    tasks/schema.py::Task). Serialise via Task.to_dict() per task; preserve
    the rest of the dict verbatim — the schema (Task 12) expects all 7 keys
    (tasks, corrections, usage, model, raw_response, members, labels).
    """
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
    # Task instances → JSON-friendly dicts. The other keys (corrections int,
    # usage dict, model str, raw_response str, members list, labels list) are
    # already JSON-safe.
    return {
        **result,
        "tasks": [t.to_dict() for t in result["tasks"]],
    }
```

Register in DISPATCH:

```python
    "extract_tasks": _handle_extract_tasks,
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_handle_extract_tasks.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_extract_tasks.py
git commit -m "feat(sidecar): _handle_extract_tasks handler"
```

---

### Task 6: _handle_generate_protocol (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py`
- Create: `python-sidecar/tests/test_handle_generate_protocol.py`

**Context:** v0.1 `tasks.protocol_generator.generate(transcript, speakers, meeting_date, lang, model, openrouter_client) -> ProtocolResult` — a frozen dataclass at `tasks/protocol_generator.py:35` with three fields: `markdown: str`, `raw_llm_response: str`, `placeholders: Placeholders`. `Placeholders` is itself a dataclass (5-block MoM: meeting_type, participants, agenda, theses_and_decisions, action_items). `dataclasses.asdict()` recursively converts both — `_to_jsonable` (Task 6 step 3) handles this. There is **no** `usage` or `blocks` field on `ProtocolResult` (the v0.1 protocol path doesn't surface OpenRouter usage — that's only in `extract()`).

- [ ] **Step 1: Write failing tests**

Create `python-sidecar/tests/test_handle_generate_protocol.py`:

```python
"""_handle_generate_protocol — wraps protocol_generator.generate; returns JSON-friendly dict."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from tests.conftest import feed_request, parse_responses


@dataclass
class FakePlaceholders:
    meeting_type: str
    participants: str
    agenda: str
    theses_and_decisions: str
    action_items: str


@dataclass
class FakeProtocolResult:
    """Matches tasks.protocol_generator.ProtocolResult (markdown, raw_llm_response, placeholders)."""
    markdown: str
    raw_llm_response: str
    placeholders: FakePlaceholders


@patch("sidecar_main._generate_protocol")
@patch("sidecar_main.OpenRouterClient")
def test_generate_protocol_serialises_dataclass(MockOR, mock_gen, fake_stdin, capsys):
    from sidecar_main import main

    mock_gen.return_value = FakeProtocolResult(
        markdown="## Block1\n...",
        raw_llm_response="LLM raw text",
        placeholders=FakePlaceholders(
            meeting_type="Standup",
            participants="A, B",
            agenda="- Item 1",
            theses_and_decisions="- Decision 1",
            action_items="- Action 1 (A)",
        ),
    )

    feed_request(fake_stdin, "generate_protocol", {
        "transcript": "x",
        "speakers": ["A", "B"],
        "meeting_date": "2026-05-28",
        "language": "ru",
        "model": "gpt-4o",
        "openrouter_api_key": "or-key",
    }, req_id=30)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 30)
    assert final["result"]["markdown"].startswith("## Block1")
    assert final["result"]["raw_llm_response"] == "LLM raw text"
    assert final["result"]["placeholders"]["meeting_type"] == "Standup"
    assert final["result"]["placeholders"]["action_items"] == "- Action 1 (A)"


@patch("sidecar_main._generate_protocol")
@patch("sidecar_main.OpenRouterClient")
def test_generate_protocol_passes_empty_speakers_when_omitted(MockOR, mock_gen, fake_stdin, capsys):
    from sidecar_main import main
    mock_gen.return_value = FakeProtocolResult(
        markdown="",
        raw_llm_response="",
        placeholders=FakePlaceholders(
            meeting_type="", participants="", agenda="",
            theses_and_decisions="", action_items="",
        ),
    )

    feed_request(fake_stdin, "generate_protocol", {
        "transcript": "x",
        "meeting_date": "2026-05-28",
        "model": "gpt-4o",
        "openrouter_api_key": "or-key",
    }, req_id=31)
    main()

    call_kwargs = mock_gen.call_args.kwargs
    assert call_kwargs["speakers"] == []
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_handle_generate_protocol.py -v
```

- [ ] **Step 3: Implement handler**

Edit `python-sidecar/sidecar_main.py`. Add import:

```python
from dataclasses import asdict, is_dataclass
from tasks.protocol_generator import generate as _generate_protocol
```

Add a helper `_to_jsonable` after `_emit_notification`:

```python
def _to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of v0.1 return types to JSON-friendly shapes."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, list):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj
```

Add the handler:

```python
def _handle_generate_protocol(params: dict[str, Any]) -> dict[str, Any]:
    """Wrap tasks.protocol_generator.generate; serialise ProtocolResult."""
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
```

Register in DISPATCH:

```python
    "generate_protocol": _handle_generate_protocol,
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_handle_generate_protocol.py -v
```

- [ ] **Step 5: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_generate_protocol.py
git commit -m "feat(sidecar): _handle_generate_protocol + _to_jsonable helper"
```

---

### Task 7: _handle_send_tasks (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py`
- Create: `python-sidecar/tests/test_handle_send_tasks.py`

**Context:** v0.1 `tasks.sender.send_tasks_iter(tasks: list[Task], *, container_id, backend, on_status_change, cancel_check, retry_failed=False)` at `tasks/sender.py:42` is a generator that yields `Task` instances after each transitions to terminal `SENT`/`FAILED` status. The signature is **not** a free-form `**params` splat — it needs:
- `tasks`: `list[Task]` (constructed via `Task.from_dict()` from JSON input)
- `container_id`: Linear team ID OR Glide container name (caller picks per backend)
- `backend`: an instance of `tasks.backends.base.BackendProtocol` (LinearBackend or GlideBackend)
- `on_status_change(task, new_status)`: callback for status transitions
- `cancel_check() -> bool`: polled before each send
- `retry_failed`: optional bool

Sidecar handler must (1) deserialise input JSON into Task dataclasses, (2) instantiate the chosen backend class with its API key, (3) provide a `cancel_check` wired to `_cancel_event`, (4) collect Task instances yielded from the generator into a list via `Task.to_dict()`. Phase 1 does NOT stream per-task status as a notification (UI consumes the final list); Phase 2 may add per-task progress events.

- [ ] **Step 1: Write failing test**

Create `python-sidecar/tests/test_handle_send_tasks.py`:

```python
"""_handle_send_tasks — Task.from_dict, backend instantiation, generator drain."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.conftest import feed_request, parse_responses


@patch("sidecar_main.LinearBackend")
@patch("sidecar_main.send_tasks_iter")
@patch("sidecar_main.Task")
def test_send_tasks_linear_backend(MockTask, mock_sender, MockBackend, fake_stdin, capsys):
    from sidecar_main import main

    # Task.from_dict reconstructs Task instances. We mock the dataclass entirely.
    task_in_1 = MagicMock(name="task1")
    task_in_2 = MagicMock(name="task2")
    MockTask.from_dict.side_effect = [task_in_1, task_in_2]

    # Yielded Task instances after status transitions
    yielded_task_1 = MagicMock()
    yielded_task_1.to_dict.return_value = {"title": "T1", "status": "SENT"}
    yielded_task_2 = MagicMock()
    yielded_task_2.to_dict.return_value = {"title": "T2", "status": "FAILED"}

    def fake_gen(*_args, **_kwargs):
        yield yielded_task_1
        yield yielded_task_2

    mock_sender.side_effect = fake_gen

    feed_request(fake_stdin, "send_tasks", {
        "tasks": [{"title": "T1"}, {"title": "T2"}],
        "backend": "linear",
        "linear_api_key": "lin-key",
        "container_id": "TEAM_X",
    }, req_id=40)
    main()

    # Linear backend constructed with the key
    MockBackend.assert_called_once_with(api_key="lin-key")
    # send_tasks_iter called with proper kwargs — verify signature contract
    call_args, call_kwargs = mock_sender.call_args
    assert call_args[0] == [task_in_1, task_in_2]  # positional `tasks`
    assert call_kwargs["container_id"] == "TEAM_X"
    assert call_kwargs["backend"] is MockBackend.return_value
    assert callable(call_kwargs["on_status_change"])
    assert callable(call_kwargs["cancel_check"])

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 40)
    assert final["result"] == [
        {"title": "T1", "status": "SENT"},
        {"title": "T2", "status": "FAILED"},
    ]


@patch("sidecar_main.GlideBackend")
@patch("sidecar_main.send_tasks_iter")
@patch("sidecar_main.Task")
def test_send_tasks_glide_backend(MockTask, mock_sender, MockBackend, fake_stdin, capsys):
    from sidecar_main import main
    MockTask.from_dict.return_value = MagicMock()
    mock_sender.return_value = iter([])

    feed_request(fake_stdin, "send_tasks", {
        "tasks": [{"title": "T1"}],
        "backend": "glide",
        "glide_api_key": "glide-key",
        "container_id": "appXYZ:Sheet1",
    }, req_id=41)
    main()

    MockBackend.assert_called_once_with(api_key="glide-key")


def test_send_tasks_unknown_backend_returns_error(fake_stdin, capsys):
    from sidecar_main import main

    feed_request(fake_stdin, "send_tasks", {
        "tasks": [],
        "backend": "trello",  # not supported in v0.2 parity
        "container_id": "x",
    }, req_id=42)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 42)
    assert "error" in final
    assert "trello" in final["error"]["message"].lower()
```

- [ ] **Step 2: Run test — expect failure**

```bash
pytest tests/test_handle_send_tasks.py -v
```

- [ ] **Step 3: Implement handler**

Edit `python-sidecar/sidecar_main.py`. Add imports:

```python
from tasks.sender import send_tasks_iter
from tasks.schema import Task
from tasks.backends.linear import LinearBackend
from tasks.backends.glide import GlideBackend
```

Add handler:

```python
def _handle_send_tasks(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrap tasks.sender.send_tasks_iter; instantiate backend, drain generator.

    See tasks/sender.py:42 for the real signature. Caller passes raw task dicts
    (matching ExtractedTask schema); handler reconstructs Task dataclasses,
    picks the backend, wires a cancel_check to the global _cancel_event, and
    returns the list of Task.to_dict() after each transitions to terminal.
    """
    backend_name = params["backend"]
    if backend_name == "linear":
        backend = LinearBackend(api_key=params["linear_api_key"])
    elif backend_name == "glide":
        backend = GlideBackend(api_key=params["glide_api_key"])
    else:
        raise ValueError(f"Unsupported backend: {backend_name!r}. Phase 1 supports 'linear'|'glide' only.")

    tasks_in = [Task.from_dict(d) for d in params["tasks"]]
    container_id = params["container_id"]
    retry_failed = params.get("retry_failed", False)

    def _on_status_change(task, new_status):
        # Phase 1: status changes are not streamed back as notifications.
        # Phase 2 may emit per-task notifications here.
        logger.info("send_tasks: %r → %s", getattr(task, "local_id", "?"), new_status)

    results: list[dict[str, Any]] = []
    for task in send_tasks_iter(
        tasks_in,
        container_id=container_id,
        backend=backend,
        on_status_change=_on_status_change,
        cancel_check=lambda: _cancel_event.is_set(),
        retry_failed=retry_failed,
    ):
        results.append(task.to_dict())
    return results
```

Register in DISPATCH:

```python
    "send_tasks": _handle_send_tasks,
```

- [ ] **Step 4: Run test — expect pass**

```bash
pytest tests/test_handle_send_tasks.py -v
```

- [ ] **Step 5: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_send_tasks.py
git commit -m "feat(sidecar): _handle_send_tasks handler"
```

---

### Task 8: _handle_gdrive_backup (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py`
- Create: `python-sidecar/tests/test_handle_gdrive_backup.py`

**Context:** v0.1 `gdrive.backup.run_backup(*, auth, config, history_dir, work_dir, app_version, on_status) -> dict`. The handler resolves `history_dir` to `$APPDATA/audio-transcriber/history/` via Tauri's `app_data_dir()` — but the sidecar doesn't have Tauri's API. Solution: the params include `history_dir` and `work_dir` passed from Rust (Rust resolves them via `tauri::api::path::app_data_dir`). On `on_status`, emit a `progress` notification.

**Important** (per gdrive/auth.py:55): `GDriveAuth()` constructor does NOT touch disk. To load cached credentials from `~/.audio-transcriber/gdrive-token.json` the handler must explicitly call `auth.load_tokens()`. If `load_tokens()` returns falsy / raises (no cached token), raise a clear "user not signed in" error — `run_backup` would fail later with an opaque Google API error otherwise. The Settings dialog UI in Phase 2 is responsible for triggering `sign_in()`; the sidecar handler only consumes existing tokens.

- [ ] **Step 1: Write failing test**

Create `python-sidecar/tests/test_handle_gdrive_backup.py`:

```python
"""_handle_gdrive_backup — wraps gdrive.backup.run_backup; emits status notifications."""
from __future__ import annotations

from unittest.mock import patch

from tests.conftest import feed_request, parse_responses


@patch("sidecar_main.run_backup")
@patch("sidecar_main.GDriveAuth")
def test_gdrive_backup_emits_status_and_returns_result(mock_auth, mock_run, fake_stdin, capsys):
    from sidecar_main import main

    def fake_run(**kwargs):
        kwargs["on_status"]("Создаю архив истории...")
        kwargs["on_status"]("Загружаю manifest.json...")
        return {
            "root_folder_id": "root-id",
            "snapshot_folder_id": "snap-id",
            "snapshot_name": "2026-05-28T12-00-00",
            "uploaded": {"history.zip": "file-id-1"},
        }

    mock_run.side_effect = fake_run

    feed_request(fake_stdin, "gdrive_backup", {
        "config": {"gdrive_root_folder_id": "root-id"},
        "history_dir": "/tmp/history",
        "work_dir": "/tmp/work",
    }, req_id=50)
    main()

    responses = parse_responses(capsys.readouterr().out)
    notifications = [r for r in responses if "id" not in r]
    status_messages = [n["params"]["message"] for n in notifications if n["method"] == "progress"]
    assert "Создаю архив истории..." in status_messages
    assert "Загружаю manifest.json..." in status_messages

    final = next(r for r in responses if r.get("id") == 50)
    assert final["result"]["snapshot_name"] == "2026-05-28T12-00-00"
```

- [ ] **Step 2: Run test — expect failure**

```bash
pytest tests/test_handle_gdrive_backup.py -v
```

- [ ] **Step 3: Implement handler**

Edit `python-sidecar/sidecar_main.py`. Add imports:

```python
from gdrive.auth import GDriveAuth
from gdrive.backup import run_backup
```

Add handler:

```python
def _handle_gdrive_backup(params: dict[str, Any]) -> dict[str, Any]:
    """Wrap gdrive.backup.run_backup; emit status notifications + return result dict.

    Rust core passes resolved history_dir + work_dir (Tauri app_data_dir).
    GDriveAuth() constructor does NOT read disk — we explicitly load_tokens()
    and fail with a clear message if the user hasn't signed in yet.
    """
    auth = GDriveAuth()
    auth.load_tokens()  # reads ~/.audio-transcriber/gdrive-token.json
    if not auth.is_signed_in():
        raise RuntimeError(
            "Google Drive не авторизован. Откройте Настройки → Google Drive → Войти."
        )
    return run_backup(
        auth=auth,
        config=params["config"],
        history_dir=params["history_dir"],
        work_dir=params["work_dir"],
        on_status=lambda msg: _emit_notification("progress", {"message": msg}),
    )
```

Register in DISPATCH:

```python
    "gdrive_backup": _handle_gdrive_backup,
```

- [ ] **Step 4: Run test — expect pass**

```bash
pytest tests/test_handle_gdrive_backup.py -v
```

- [ ] **Step 5: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_gdrive_backup.py
git commit -m "feat(sidecar): _handle_gdrive_backup handler"
```

---

### Task 9: _handle_list_history (NEW — no v0.1 counterpart) (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py`
- Create: `python-sidecar/tests/test_handle_list_history.py`

**Context:** v0.1's history is rendered by the `HistoryDialog` Tk class reading folders directly. The folder naming convention (see `utils.py:122-125`) is `{ISO_timestamp}_{audio_base_name}` (e.g. `2026-05-28_10-00-00_meeting-a/`). Inside each folder: `transcript.txt`, optional `description.md` (v0.1 metadata — Russian markdown), optional audio file, optional `tasks.json` / `protocol.md` (when those features ran). **There is no `meta.json`** — the spec example was simplified. Parse the timestamp from the folder name prefix; `transcript_excerpt` reads the first 200 chars of `transcript.txt`; `has_protocol` / `has_tasks` check for those filenames.

- [ ] **Step 1: Write failing test**

Create `python-sidecar/tests/test_handle_list_history.py`:

```python
"""_handle_list_history — walks history dir, returns summary entries."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import feed_request, parse_responses


@pytest.fixture
def history_with_runs(tmp_path: Path) -> Path:
    """Create a fake history dir matching v0.1 utils.save_history layout."""
    h = tmp_path / "history"
    h.mkdir()

    # v0.1 naming: {YYYY-MM-DD_HH-MM-SS}_{audio_base_name}/
    run1 = h / "2026-05-28_10-00-00_meeting-a"
    run1.mkdir()
    (run1 / "transcript.txt").write_text("Привет всем\nЭто тест встречи")
    (run1 / "description.md").write_text("# meeting-a.wav\n- Дата: 2026-05-28 10:00:00")
    (run1 / "protocol.md").write_text("## Протокол")
    (run1 / "tasks.json").write_text(json.dumps([{"title": "T1"}]))

    run2 = h / "2026-05-27_14-00-00_quick-call"
    run2.mkdir()
    (run2 / "transcript.txt").write_text("Короткая встреча на 5 минут")
    # No protocol.md, no tasks.json — only transcript

    return h


def test_list_history_returns_summary(history_with_runs, fake_stdin, capsys):
    from sidecar_main import main

    feed_request(fake_stdin, "list_history", {
        "history_dir": str(history_with_runs),
    }, req_id=60)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 60)
    entries = final["result"]
    assert len(entries) == 2

    # Latest first (sorted reverse by folder name → reverse-chrono)
    assert entries[0]["run_id"].startswith("2026-05-28")
    assert entries[1]["run_id"].startswith("2026-05-27")

    e1 = entries[0]
    assert e1["has_protocol"] is True
    assert e1["has_tasks"] is True
    assert "Привет всем" in e1["transcript_excerpt"]
    # Timestamp parsed from folder prefix "YYYY-MM-DD_HH-MM-SS"
    assert e1["timestamp"] == "2026-05-28T10:00:00"

    e2 = entries[1]
    assert e2["has_protocol"] is False
    assert e2["has_tasks"] is False
    assert e2["timestamp"] == "2026-05-27T14:00:00"


def test_list_history_empty_dir_returns_empty_list(tmp_path: Path, fake_stdin, capsys):
    from sidecar_main import main
    empty = tmp_path / "empty_history"
    empty.mkdir()

    feed_request(fake_stdin, "list_history", {
        "history_dir": str(empty),
    }, req_id=61)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 61)
    assert final["result"] == []


def test_list_history_missing_dir_returns_empty_list(tmp_path: Path, fake_stdin, capsys):
    """First-run case: history dir doesn't exist yet — return [] not error."""
    from sidecar_main import main
    missing = tmp_path / "nope"

    feed_request(fake_stdin, "list_history", {
        "history_dir": str(missing),
    }, req_id=62)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 62)
    assert final["result"] == []
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_handle_list_history.py -v
```

- [ ] **Step 3: Implement handler**

Edit `python-sidecar/sidecar_main.py`. Add import:

```python
from pathlib import Path
```

Add handler:

```python
_FOLDER_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})_")


def _parse_folder_timestamp(folder_name: str) -> str:
    """Extract ISO timestamp from v0.1 folder naming.

    v0.1 utils.save_history writes folders as `{YYYY-MM-DD_HH-MM-SS}_{audio_base}`
    (utils.py:122-125). Convert that prefix to ISO 8601 `YYYY-MM-DDTHH:MM:SS`
    so the React store can sort + display.

    Returns empty string if the folder doesn't match the convention (e.g. user
    renamed it manually) — the entry still surfaces, just without a timestamp.
    """
    m = _FOLDER_TS_RE.match(folder_name)
    if not m:
        return ""
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def _handle_list_history(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk $APPDATA/audio-transcriber/history/ and return summary entries.

    Returns a list of {run_id, timestamp, transcript_excerpt, has_protocol,
    has_tasks}, sorted latest first by folder name (folder names start with
    the ISO timestamp, so reverse-lexical sort = reverse-chrono).
    Missing or empty history dir → empty list (first-run case).
    """
    history_dir = Path(params["history_dir"])
    if not history_dir.exists():
        return []

    entries: list[dict[str, Any]] = []
    for run_dir in sorted(history_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not run_dir.is_dir():
            continue

        transcript_path = run_dir / "transcript.txt"
        excerpt = ""
        if transcript_path.exists():
            try:
                text = transcript_path.read_text(encoding="utf-8")
                excerpt = text[:200].strip()
            except OSError:
                pass

        entries.append({
            "run_id": run_dir.name,
            "timestamp": _parse_folder_timestamp(run_dir.name),
            "transcript_excerpt": excerpt,
            "has_protocol": (run_dir / "protocol.md").exists(),
            "has_tasks": (run_dir / "tasks.json").exists(),
        })

    return entries
```

Also add at the imports section near top of sidecar_main.py:

```python
import re
```

Register in DISPATCH:

```python
    "list_history": _handle_list_history,
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_handle_list_history.py -v
```

- [ ] **Step 5: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_list_history.py
git commit -m "feat(sidecar): _handle_list_history (NEW — walks history dir)"
```

---

### Task 10: _handle_trim_audio (NEW — replaces v0.1 audio_cutter.py UI ffmpeg call site) (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py`
- Create: `python-sidecar/tests/test_handle_trim_audio.py`

**Context:** v0.1's `audio_cutter.py` has the React-friendly trim logic embedded in the Tk UI. Lift the ffmpeg invocation into a sidecar method called from React (Phase 2 builds the timeline UI; Phase 1 ships the handler). Input: list of `[(start_sec, end_sec), ...]` ranges + input file. Output: trimmed file at output path. Use ffmpeg's `concat` filter with selected segments. Per memory `feedback_mock_tests_dont_catch_ffmpeg_parse_errors`, special chars in paths must be escaped — use `audio_io.py`'s existing escape helper.

- [ ] **Step 1: Write failing tests**

Create `python-sidecar/tests/test_handle_trim_audio.py`:

```python
"""_handle_trim_audio — ffmpeg concat filter for [(start, end), ...] ranges."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import feed_request, parse_responses


@patch("sidecar_main.subprocess.run")
@patch("sidecar_main.get_ffmpeg_path")
def test_trim_audio_builds_concat_filter(mock_ffmpeg, mock_run, fake_stdin, capsys, tmp_path):
    from sidecar_main import main

    mock_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")

    input_audio = tmp_path / "in.wav"
    input_audio.write_bytes(b"fake-audio")
    output_audio = tmp_path / "out.wav"

    feed_request(fake_stdin, "trim_audio", {
        "input_path": str(input_audio),
        "output_path": str(output_audio),
        "ranges": [[0.0, 5.0], [10.0, 15.0]],
    }, req_id=70)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 70)
    assert final["result"]["success"] is True
    assert final["result"]["output_path"] == str(output_audio)

    args = mock_run.call_args.args[0]
    assert args[0] == "/usr/bin/ffmpeg"
    assert "-filter_complex" in args
    filter_str = args[args.index("-filter_complex") + 1]
    # Concat filter must reference both ranges
    assert "concat=n=2:v=0:a=1" in filter_str
    assert "atrim=start=0.0:end=5.0" in filter_str
    assert "atrim=start=10.0:end=15.0" in filter_str


@patch("sidecar_main.subprocess.run")
@patch("sidecar_main.get_ffmpeg_path")
def test_trim_audio_returns_error_on_ffmpeg_failure(mock_ffmpeg, mock_run, fake_stdin, capsys, tmp_path):
    from sidecar_main import main

    mock_ffmpeg.return_value = "/usr/bin/ffmpeg"
    mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"ffmpeg: invalid filter")

    input_audio = tmp_path / "in.wav"
    input_audio.write_bytes(b"x")

    feed_request(fake_stdin, "trim_audio", {
        "input_path": str(input_audio),
        "output_path": str(tmp_path / "out.wav"),
        "ranges": [[0.0, 5.0]],
    }, req_id=71)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 71)
    assert "error" in final
    assert "ffmpeg" in final["error"]["message"].lower()


def test_trim_audio_empty_ranges_returns_error(fake_stdin, capsys, tmp_path):
    from sidecar_main import main

    input_audio = tmp_path / "in.wav"
    input_audio.write_bytes(b"x")

    feed_request(fake_stdin, "trim_audio", {
        "input_path": str(input_audio),
        "output_path": str(tmp_path / "out.wav"),
        "ranges": [],
    }, req_id=72)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 72)
    assert "error" in final
    assert "empty" in final["error"]["message"].lower() or "no ranges" in final["error"]["message"].lower()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_handle_trim_audio.py -v
```

- [ ] **Step 3: Implement handler**

Edit `python-sidecar/sidecar_main.py`. Add imports:

```python
import subprocess
from utils import get_ffmpeg_path
```

Add handler:

```python
def _handle_trim_audio(params: dict[str, Any]) -> dict[str, Any]:
    """Trim audio file to keep only the given [(start, end), ...] ranges.

    Replaces the v0.1 audio_cutter.py UI call site. ffmpeg's atrim + concat
    filter pipeline drops everything outside the kept ranges.
    """
    input_path = params["input_path"]
    output_path = params["output_path"]
    ranges = params["ranges"]

    if not ranges:
        raise ValueError("No ranges provided — output would be empty")

    # utils.get_ffmpeg_path() returns str | None (see utils.py:48). Resolve early
    # and surface a clear Russian error if neither bundled vendor nor PATH ffmpeg
    # is available — subprocess.run would otherwise fail with a confusing
    # FileNotFoundError on None.
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg не найден. Переустановите приложение.")

    # Build filter_complex string: [0:a]atrim=start=A:end=B,asetpts=PTS-STARTPTS[s0];
    # ...repeat per range...; [s0][s1]concat=n=N:v=0:a=1[out]
    parts: list[str] = []
    labels: list[str] = []
    for i, (start, end) in enumerate(ranges):
        label = f"s{i}"
        parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{label}]")
        labels.append(f"[{label}]")
    parts.append(f"{''.join(labels)}concat=n={len(ranges)}:v=0:a=1[out]")
    filter_complex = ";".join(parts)

    cmd = [
        ffmpeg,
        "-y",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ffmpeg failed (rc={result.returncode}): {stderr}")

    return {"success": True, "output_path": output_path}
```

Register in DISPATCH:

```python
    "trim_audio": _handle_trim_audio,
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_handle_trim_audio.py -v
```

- [ ] **Step 5: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_trim_audio.py
git commit -m "feat(sidecar): _handle_trim_audio (NEW — ffmpeg concat filter)"
```

---

### Task 11: _handle_load_config + _handle_save_config + _handle_shutdown (TDD)

**Files:**
- Modify: `python-sidecar/sidecar_main.py`
- Create: `python-sidecar/tests/test_handle_config_io.py`

**Context:** v0.1's `utils.py` already has `load_config()` and `save_config(config)` (the v0.1 `Transcriber.transcribe(...)` reads them via `utils.load_config()` indirectly). But those functions resolve a path relative to `utils.py` in v0.1's onedir layout — `$APPDATA/audio-transcriber/config.json` doesn't exist in v0.1 conventions. Sidecar handlers MUST accept the config path from params (Rust resolves via `tauri::api::path::app_data_dir`). Add wrapper functions to `python-sidecar/utils.py` that accept a path explicitly.

- [ ] **Step 1: Write failing tests**

Create `python-sidecar/tests/test_handle_config_io.py`:

```python
"""_handle_load_config + _handle_save_config + _handle_shutdown."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import feed_request, parse_responses


def test_load_config_reads_json(fake_stdin, capsys, tmp_path: Path):
    from sidecar_main import main

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"cloud_provider": "AssemblyAI", "diarize": True}))

    feed_request(fake_stdin, "load_config", {"path": str(config_file)}, req_id=80)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 80)
    assert final["result"]["cloud_provider"] == "AssemblyAI"
    assert final["result"]["diarize"] is True


def test_load_config_missing_returns_empty_dict(fake_stdin, capsys, tmp_path: Path):
    from sidecar_main import main
    missing = tmp_path / "nope.json"

    feed_request(fake_stdin, "load_config", {"path": str(missing)}, req_id=81)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 81)
    assert final["result"] == {}


def test_save_config_writes_json_atomically(fake_stdin, capsys, tmp_path: Path):
    from sidecar_main import main
    config_file = tmp_path / "out.json"

    feed_request(fake_stdin, "save_config", {
        "path": str(config_file),
        "config": {"cloud_provider": "Deepgram", "diarize": False},
    }, req_id=82)
    main()

    responses = parse_responses(capsys.readouterr().out)
    final = next(r for r in responses if r.get("id") == 82)
    assert final["result"]["saved"] is True

    data = json.loads(config_file.read_text())
    assert data["cloud_provider"] == "Deepgram"


def test_shutdown_exits_process(fake_stdin, capsys):
    """shutdown handler calls sys.exit — the dispatcher loop is broken via SystemExit."""
    from sidecar_main import main

    feed_request(fake_stdin, "shutdown", {}, req_id=83)
    with pytest.raises(SystemExit):
        main()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_handle_config_io.py -v
```

- [ ] **Step 3: Implement handlers**

Edit `python-sidecar/sidecar_main.py`. Add handlers:

```python
def _handle_load_config(params: dict[str, Any]) -> dict[str, Any]:
    """Read config.json from a path supplied by Rust (resolved via app_data_dir)."""
    path = Path(params["path"])
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("load_config failed for %s: %s", path, exc)
        return {}


def _handle_save_config(params: dict[str, Any]) -> dict[str, bool]:
    """Atomic write: write to .tmp then rename, so a crash mid-write doesn't corrupt config."""
    path = Path(params["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(params["config"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return {"saved": True}


def _handle_shutdown(_params: dict[str, Any]) -> None:
    """Graceful sidecar exit — Tauri calls this before quitting the app."""
    logger.info("Sidecar shutdown requested")
    sys.exit(0)
```

Register in DISPATCH:

```python
    "load_config": _handle_load_config,
    "save_config": _handle_save_config,
    "shutdown": _handle_shutdown,
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_handle_config_io.py -v
```

- [ ] **Step 5: Run all sidecar tests + v0.1 baseline**

```bash
pytest -v          # all sidecar tests (should be ~25-30 by now)
cd ..
pytest             # v0.1 still 333 green
cd python-sidecar
ruff check .       # clean
```

- [ ] **Step 6: Commit**

```bash
git add python-sidecar/sidecar_main.py python-sidecar/tests/test_handle_config_io.py
git commit -m "feat(sidecar): _handle_load_config + _handle_save_config + _handle_shutdown"
```

---

### Task 12: Pydantic v2 schemas for JSON-RPC contract

**Files:**
- Create: `python-sidecar/schemas.py`
- Create: `python-sidecar/tests/test_schemas.py`

**Context:** Pydantic models define the **source of truth** for the JSON-RPC request/response contract. They'll be code-generated to TypeScript types in Task 13 — so React imports them rather than hand-writing them and drifting. Per memory `feedback_codex_catches_contract_drift`, drift between docstring/implementation/consumer is exactly the class of bug Codex finds post-merge — pre-empt it with type-gen.

- [ ] **Step 1: Write failing tests**

Create `python-sidecar/tests/test_schemas.py`:

```python
"""schemas.py — Pydantic v2 models for JSON-RPC request/response contract."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_transcribe_request_validates_required_fields():
    from schemas import TranscribeRequest

    # Missing required fields → ValidationError
    with pytest.raises(ValidationError):
        TranscribeRequest()  # type: ignore[call-arg]

    # Minimal valid
    req = TranscribeRequest(
        audio_path="x.webm",
        cloud_provider="AssemblyAI",
        cloud_api_key="k",
    )
    assert req.audio_path == "x.webm"
    assert req.diarize is False  # default


def test_transcribe_result_round_trips():
    from schemas import Segment, TranscribeResult

    res = TranscribeResult(
        formatted_text="[00:00] hi",
        segments=[Segment(start=0.0, end=1.0, text="hi", speaker="A")],
    )
    data = res.model_dump()
    rehydrated = TranscribeResult.model_validate(data)
    assert rehydrated.segments[0].speaker == "A"


def test_history_entry_model():
    from schemas import HistoryEntry
    e = HistoryEntry(
        run_id="r1",
        timestamp="2026-05-28T10:00:00",
        transcript_excerpt="hi",
        has_protocol=True,
        has_tasks=False,
    )
    assert e.has_protocol is True


def test_ping_response_includes_version():
    from schemas import PingResponse
    r = PingResponse(pong=True, version="0.2.0")
    assert r.version == "0.2.0"
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_schemas.py -v
```

Expected: ModuleNotFoundError for `schemas`.

- [ ] **Step 3: Implement schemas.py**

Create `python-sidecar/schemas.py`:

```python
"""Pydantic v2 models defining the JSON-RPC request/response contract.

These models are the source of truth: Task 13 generates TypeScript types
from them via pydantic-to-typescript, and React imports the generated
types instead of hand-writing them. Drift between Python and TS is caught
by the pre-commit hook (CI fails if generated output differs).

Each handler in sidecar_main.py SHOULD parse params into the matching
Request model at the start, and return a Response model (or dict matching
the schema). Phase 1 calls validate_python(...) at the handler boundary;
Phase 2 may add response validation too.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PingResponse(BaseModel):
    pong: bool
    version: str


class Segment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None
    language: str | None = None  # Phase 2 code-switching metadata; optional in Phase 1


class TranscribeRequest(BaseModel):
    audio_path: str
    cloud_provider: str
    cloud_api_key: str
    language: str | None = None
    diarize: bool = False
    hotwords: str | None = None
    num_speakers: int | None = None
    # min_speakers + max_speakers — AssemblyAI accepts speaker bounds when
    # num_speakers is unknown. v0.1 Transcriber.transcribe forwards them
    # (transcriber/__init__.py:79+204); v0.2 must too.
    min_speakers: int | None = None
    max_speakers: int | None = None
    denoise_audio: bool = False


class TranscribeResult(BaseModel):
    formatted_text: str
    segments: list[Segment]


class CancelResponse(BaseModel):
    cancelled: bool


class ExtractTasksRequest(BaseModel):
    transcript: str
    model: str
    openrouter_api_key: str
    language: str | None = None
    linear_api_key: str | None = None
    team_id: str | None = None
    members: list[dict] = Field(default_factory=list)
    labels: list[dict] = Field(default_factory=list)


class ExtractedTask(BaseModel):
    # Mirrors tasks.schema.Task fields actually returned by extractor.extract().
    # NOTE: v0.1 extract() returns Task dataclass instances (NOT plain dicts),
    # so the handler serialises via Task.to_dict() — see Task 5.
    title: str
    description: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    status: str | None = None
    local_id: str | None = None


class ExtractTasksResult(BaseModel):
    # Mirrors the dict returned by tasks.extractor.extract() at extractor.py:302.
    # `corrections` is the COUNT of LLM self-corrections (int), not a list of
    # dicts. `raw_response`/`members`/`labels` are preserved so the UI can render
    # the "Show raw response" affordance + display member/label grounding.
    tasks: list[ExtractedTask]
    corrections: int = 0
    usage: dict = Field(default_factory=dict)
    model: str | None = None
    raw_response: str | None = None
    members: list[dict] = Field(default_factory=list)
    labels: list[dict] = Field(default_factory=list)


class GenerateProtocolRequest(BaseModel):
    transcript: str
    meeting_date: str
    model: str
    openrouter_api_key: str
    speakers: list[str] = Field(default_factory=list)
    language: str | None = None


class ProtocolPlaceholders(BaseModel):
    # Mirrors tasks.protocol_generator.Placeholders (5-block MoM, parsed from LLM).
    meeting_type: str
    participants: str
    agenda: str
    theses_and_decisions: str
    action_items: str


class GenerateProtocolResult(BaseModel):
    # Mirrors tasks.protocol_generator.ProtocolResult dataclass at
    # protocol_generator.py:35. No usage field — v0.1 doesn't surface OpenRouter
    # usage from protocol_generator (extractor.extract() does, but the protocol
    # generator path doesn't propagate it).
    markdown: str
    raw_llm_response: str
    placeholders: ProtocolPlaceholders | dict


class HistoryEntry(BaseModel):
    run_id: str
    timestamp: str
    transcript_excerpt: str
    has_protocol: bool
    has_tasks: bool


class ListHistoryRequest(BaseModel):
    history_dir: str


class TrimAudioRequest(BaseModel):
    input_path: str
    output_path: str
    ranges: list[tuple[float, float]]


class TrimAudioResult(BaseModel):
    success: bool
    output_path: str


class LoadConfigRequest(BaseModel):
    path: str


class SaveConfigRequest(BaseModel):
    path: str
    config: dict


class SaveConfigResponse(BaseModel):
    saved: bool


class GDriveBackupRequest(BaseModel):
    config: dict
    history_dir: str
    work_dir: str


class GDriveBackupResult(BaseModel):
    root_folder_id: str
    snapshot_folder_id: str
    snapshot_name: str
    uploaded: dict


# Notification payloads (sent without `id`)
class ProgressNotification(BaseModel):
    pct: float


class StatusNotification(BaseModel):
    message: str
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_schemas.py -v
```

- [ ] **Step 5: Add pydantic to requirements.txt**

Edit `python-sidecar/requirements.txt`. Verify pydantic is present (v5 rip-out kept it because tasks/schema.py uses it). If absent, add:

```
pydantic>=2.5,<3.0
```

- [ ] **Step 6: Commit**

```bash
git add python-sidecar/schemas.py python-sidecar/tests/test_schemas.py python-sidecar/requirements.txt
git commit -m "feat(sidecar): Pydantic v2 schemas defining JSON-RPC contract"
```

---

### Task 13: pydantic-to-typescript generator + pre-commit hook

**Files:**
- Create: `python-sidecar/scripts/gen_ts_types.py`
- Create: `.pre-commit-config.yaml`
- Modify: `python-sidecar/requirements.txt` (add `pydantic-to-typescript`)

**Context:** Generates `src/lib/python-types.d.ts` from `schemas.py`. Runs as pre-commit hook and in CI. CI fails if the committed `.d.ts` differs from regenerated — this catches Pydantic edits that didn't refresh the TS types.

- [ ] **Step 1: Add pydantic-to-typescript to requirements**

Edit `python-sidecar/requirements.txt`. Append:

```
pydantic-to-typescript>=2.0,<3.0
```

Install:

```bash
cd python-sidecar
pip install pydantic-to-typescript
```

- [ ] **Step 2: Write the generator script**

Create `python-sidecar/scripts/gen_ts_types.py`:

```python
"""Generate TypeScript types from schemas.py via pydantic-to-typescript.

Output: src/lib/python-types.d.ts (gitignored — regenerated on every
commit by the pre-commit hook). CI runs this and `git diff --exit-code`
on the regenerated file vs. the committed version. CI fails on diff
because the committed `.d.ts` is itself never checked in (gitignored),
but the comparison is: regenerate, then check `git status --porcelain
src/lib/python-types.d.ts` is empty (no change since last commit means
nothing drifted between the committed schemas.py and what TS sees).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SIDECAR_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SIDECAR_ROOT.parent
SCHEMAS_PY = SIDECAR_ROOT / "schemas.py"
OUTPUT_TS = REPO_ROOT / "src" / "lib" / "python-types.d.ts"


def main() -> int:
    OUTPUT_TS.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pydantic2ts",
        "--module", str(SCHEMAS_PY),
        "--output", str(OUTPUT_TS),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"pydantic2ts failed:\n{result.stderr}", file=sys.stderr)
        return 1
    print(f"Generated {OUTPUT_TS} from {SCHEMAS_PY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run the generator manually + verify output**

```bash
cd python-sidecar
python scripts/gen_ts_types.py
```

Expected: prints "Generated .../src/lib/python-types.d.ts ...". Open the file and confirm it contains TypeScript interfaces (TranscribeRequest, Segment, HistoryEntry, etc.).

- [ ] **Step 4: Add pre-commit config**

Create `.pre-commit-config.yaml` at repo root:

```yaml
repos:
  - repo: local
    hooks:
      - id: gen-ts-types
        name: Regenerate TS types from Pydantic
        entry: python python-sidecar/scripts/gen_ts_types.py
        language: system
        files: ^python-sidecar/schemas\.py$
        pass_filenames: false
      - id: ruff-sidecar
        name: ruff (sidecar)
        entry: ruff check python-sidecar/
        language: system
        types: [python]
        files: ^python-sidecar/
        pass_filenames: false
```

- [ ] **Step 5: Verify generated file is git-ignored**

```bash
git status
```

Expected: `src/lib/python-types.d.ts` does NOT appear in untracked or modified (the Task 1 .gitignore entry catches it).

- [ ] **Step 6: Commit**

```bash
git add python-sidecar/scripts/ python-sidecar/requirements.txt .pre-commit-config.yaml
git commit -m "build(v0.2): pydantic-to-typescript codegen + pre-commit hook"
```

---

### Task 14: Sidecar PyInstaller spec + smoke build

**Files:**
- Create: `python-sidecar/audio_transcriber_sidecar.spec`
- Create: `python-sidecar/runtime_hook_sidecar.py`
- Create: `python-sidecar/build_sidecar.ps1` (Windows build helper)

**Context:** Fresh spec per §12 Q6 decision. Differences from v0.1 `audio_transcriber.spec`:
- `console=True` (sidecar needs stdin/stdout pipes; Tauri spawns with CREATE_NO_WINDOW so no window appears)
- Entry: `sidecar_main.py` instead of `app.py`
- No `customtkinter` hidden import
- Runtime hook redirects None streams to `%TEMP%/audio-transcriber-sidecar-bootstrap.log` (per memory `feedback_pyinstaller_windowed_stderr_none` — applies even with console=True because Tauri's CREATE_NO_WINDOW flag detaches stderr from any terminal)
- **`--onefile` mode** (NOT `--onedir`). Tauri 2's `externalBin` convention requires a single executable at `binaries/<name>-<target_triple>.exe`. PyInstaller `--onedir` produces an exe + sibling `_internal/` directory, which doesn't fit the externalBin contract cleanly. `--onefile` produces a self-extracting single .exe (~200-400 MB after deps); startup cost ~1-3s for `_internal/` extraction to %TEMP%, which happens once per app launch — acceptable for a long-lived sidecar. (Phase 3 polish may revisit if startup latency is a problem.)

- [ ] **Step 1: Write the runtime hook**

Create `python-sidecar/runtime_hook_sidecar.py`:

```python
"""Runtime hook executed before sidecar_main.py imports.

PyInstaller's stdio handling on Windows when Tauri spawns with
CREATE_NO_WINDOW: sys.stderr / sys.stdout / sys.stdin can all be None
or unbuffered file handles tied to closed pipes. The first print() or
faulthandler.enable() call into None would raise AttributeError —
silent crash from user POV. Redirect None streams to a sidecar
bootstrap log so we can diagnose any pre-main crashes.

After main() is up and Tauri has connected its stdin/stdout pipes,
this hook is irrelevant — but the OS doesn't tell us when that
happens, so we keep the file open for the process lifetime.
"""
from __future__ import annotations

import os
import sys
import tempfile

_BOOTSTRAP_LOG = os.path.join(tempfile.gettempdir(), "audio-transcriber-sidecar-bootstrap.log")

if sys.stdin is None or sys.stdout is None or sys.stderr is None:
    # If stdio is unavailable, log to file so a crash leaves a trace.
    _log_handle = open(_BOOTSTRAP_LOG, "a", encoding="utf-8", buffering=1)
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r")
    if sys.stdout is None:
        sys.stdout = _log_handle
    if sys.stderr is None:
        sys.stderr = _log_handle
```

- [ ] **Step 2: Write the PyInstaller spec**

Create `python-sidecar/audio_transcriber_sidecar.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Audio Transcriber Python SIDECAR (v0.2).

Build: pyinstaller python-sidecar/audio_transcriber_sidecar.spec --noconfirm
Output: python-sidecar/dist/audio-transcriber-core/ (onedir bundle)

After build, the onedir bundle is staged to src-tauri/binaries/ with the
PyInstaller-required target-triple suffix by build_sidecar.ps1 / .sh.

Differences from v0.1 audio_transcriber.spec:
- Entry point: sidecar_main.py (no Tk/CTk UI)
- console=True — sidecar needs stdin/stdout pipes for JSON-RPC
- Runtime hook redirects None streams to %TEMP%/audio-transcriber-sidecar-bootstrap.log
- No customtkinter hidden imports
"""
from pathlib import Path

block_cipher = None
SIDECAR_ROOT = Path(SPECPATH)
VENDOR_FFMPEG = SIDECAR_ROOT / "vendor" / "ffmpeg"


a = Analysis(
    ["sidecar_main.py"],
    pathex=[str(SIDECAR_ROOT)],
    binaries=[
        # Vendored ffmpeg + ffprobe — utils.get_ffmpeg_path() resolves
        # via sys._MEIPASS in frozen mode.
        (str(VENDOR_FFMPEG / "ffmpeg.exe"), "vendor/ffmpeg"),
        (str(VENDOR_FFMPEG / "ffprobe.exe"), "vendor/ffmpeg"),
    ],
    datas=[],  # config.json lives outside the bundle (in $APPDATA), seeded by Tauri's bootstrap.rs
    hiddenimports=[
        # Network / HTTP layer
        "requests",
        "urllib3",
        # Cloud STT providers — explicit so the registry resolves on
        # first import (providers/__init__.py eagerly imports each).
        "providers.assemblyai",
        "providers.deepgram",
        "providers.gladia",
        "providers.speechmatics",
        # Google Drive backup — googleapiclient sub-modules loaded by name at runtime
        "googleapiclient.discovery",
        "googleapiclient.discovery_cache",
        "googleapiclient.discovery_cache.file_cache",
        "google_auth_oauthlib.flow",
        # Pydantic v2 — used by schemas.py
        "pydantic",
    ],
    hookspath=[],
    runtime_hooks=[str(SIDECAR_ROOT / "runtime_hook_sidecar.py")],
    excludes=[
        # No UI in the sidecar
        "tkinter", "customtkinter", "PIL", "PIL.ImageTk",
        # Test deps
        "pytest", "_pytest", "pluggy",
        # Dev tools
        "matplotlib", "IPython", "jupyter",
        # PyInstaller itself
        "PyInstaller",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --onefile mode: bundle everything into a single self-extracting .exe so it
# fits Tauri 2's externalBin contract (`binaries/<name>-<target_triple>.exe`).
# At launch the bootloader extracts the embedded archive to %TEMP% (~1-3s),
# then runs sidecar_main.py.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="audio-transcriber-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # Sidecar — Tauri spawns with CREATE_NO_WINDOW so no window appears
    runtime_tmpdir=None,
)
```

Note: no `COLLECT()` call — `--onefile` mode packs everything into the EXE directly.

- [ ] **Step 3: Write the build helper (Windows)**

Create `python-sidecar/build_sidecar.ps1`:

```powershell
# Build the Python sidecar via PyInstaller and stage to src-tauri/binaries/
# Usage: from repo root: pwsh python-sidecar/build_sidecar.ps1

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path "$PSScriptRoot/.."
$Sidecar = "$RepoRoot/python-sidecar"
$Target = "x86_64-pc-windows-msvc"   # Windows target_triple for tauri-plugin-shell

Push-Location $Sidecar
try {
    Write-Host "Building Python sidecar (--onefile)..."
    pyinstaller audio_transcriber_sidecar.spec --noconfirm

    # --onefile output is a single .exe directly under dist/, no subdir.
    $OutBinary = "$Sidecar/dist/audio-transcriber-core.exe"
    if (-not (Test-Path $OutBinary)) {
        throw "PyInstaller output missing: $OutBinary (expected --onefile single-exe)"
    }

    # Tauri tauri-plugin-shell expects: binaries/<name>-<target_triple>.exe
    # (flat path — externalBin in tauri.conf.json references "binaries/audio-transcriber-core")
    $StagedDir = "$RepoRoot/src-tauri/binaries"
    New-Item -ItemType Directory -Force -Path $StagedDir | Out-Null

    $StagedExe = "$StagedDir/audio-transcriber-core-$Target.exe"
    if (Test-Path $StagedExe) {
        Remove-Item -Force $StagedExe
    }
    Copy-Item $OutBinary $StagedExe

    Write-Host "Sidecar staged to $StagedExe"
}
finally {
    Pop-Location
}
```

- [ ] **Step 4: Smoke build the sidecar**

```powershell
pwsh python-sidecar/build_sidecar.ps1
```

Expected output: single .exe staged at `src-tauri/binaries/audio-transcriber-core-x86_64-pc-windows-msvc.exe` (no `_internal/` neighbour — onefile mode packs everything into the exe).

- [ ] **Step 5: Verify the sidecar responds to ping**

Manual stdin smoke:

```powershell
$req = '{"jsonrpc":"2.0","id":1,"method":"ping"}'
$req | & "src-tauri/binaries/audio-transcriber-core-x86_64-pc-windows-msvc.exe"
```

Expected: prints something like `{"jsonrpc": "2.0", "id": 1, "result": {"pong": true, "version": "0.2.0"}}`. If hung — check `%TEMP%/audio-transcriber-sidecar-bootstrap.log` for crash trace.

- [ ] **Step 6: Commit**

```bash
git add python-sidecar/audio_transcriber_sidecar.spec python-sidecar/runtime_hook_sidecar.py python-sidecar/build_sidecar.ps1
git commit -m "build(sidecar): PyInstaller spec + runtime hook + Windows build helper"
```

---

### Task 15: Tauri Rust scaffold (cargo init + Cargo.toml + main.rs)

**Files:**
- Create: `src-tauri/Cargo.toml`, `src-tauri/tauri.conf.json`, `src-tauri/build.rs`
- Create: `src-tauri/src/main.rs`, `src-tauri/src/sidecar.rs`, `src-tauri/src/commands.rs`, `src-tauri/src/bootstrap.rs`
- Create: `src-tauri/icons/icon.png` (placeholder)

**Context:** Tauri 2 stable. We use `tauri-plugin-shell` for sidecar spawn, `tauri-plugin-fs` for file I/O, `tauri-plugin-dialog` for the file picker. Cargo modules are split: `main.rs` for the Tauri builder, `sidecar.rs` for the JSON-RPC pump (Task 17), `commands.rs` for Tauri command exports, `bootstrap.rs` for first-run config seeding.

- [ ] **Step 1: Initialize Cargo project**

```bash
cd src-tauri
cargo init --name audio-transcriber-tauri --bin
```

This creates a `Cargo.toml` skeleton. Overwrite with:

```toml
[package]
name = "audio-transcriber-tauri"
version = "0.2.0"
description = "Audio Transcriber — Tauri 2 lite-rewrite"
authors = ["andasbek.nurgysa@gmail.com"]
edition = "2021"
rust-version = "1.77"

[lib]
name = "audio_transcriber_tauri_lib"
crate-type = ["staticlib", "cdylib", "rlib"]

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = ["protocol-asset"] }
tauri-plugin-shell = "2"
tauri-plugin-fs = "2"
tauri-plugin-dialog = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full", "io-util", "process", "sync"] }
tracing = "0.1"
tracing-subscriber = "0.3"
thiserror = "1"
anyhow = "1"

[profile.dev]
incremental = true

[profile.release]
codegen-units = 1
lto = true
opt-level = "s"
panic = "abort"
strip = true
```

- [ ] **Step 2: Create build.rs**

Create `src-tauri/build.rs`:

```rust
fn main() {
    tauri_build::build()
}
```

- [ ] **Step 3: Create tauri.conf.json**

Create `src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2.0.0",
  "productName": "Audio Transcriber",
  "version": "0.2.0",
  "identifier": "com.andasbek.audio-transcriber",
  "build": {
    "beforeDevCommand": "pnpm dev",
    "beforeBuildCommand": "pnpm build",
    "devUrl": "http://localhost:1420",
    "frontendDist": "../dist"
  },
  "app": {
    "windows": [
      {
        "title": "Audio Transcriber",
        "width": 1200,
        "height": 800,
        "minWidth": 900,
        "minHeight": 600,
        "resizable": true,
        "fullscreen": false
      }
    ],
    "security": {
      "csp": "default-src 'self'; img-src 'self' data: blob: asset: http://asset.localhost; media-src 'self' blob: asset: http://asset.localhost; style-src 'self' 'unsafe-inline'; script-src 'self'"
    }
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": [
      "icons/icon.png"
    ],
    "externalBin": [
      "binaries/audio-transcriber-core"
    ]
  }
}
```

- [ ] **Step 3.5: Create the Tauri 2 capabilities file**

Tauri 2 moved plugin permissions out of `tauri.conf.json::plugins` into per-capability JSON files under `src-tauri/capabilities/`. Without this file the webview cannot invoke ANY plugin command (fs, shell, dialog).

Create `src-tauri/capabilities/default.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Default capability for the main window — allows fs reads/writes under $APPDATA, dialog opens, and spawning the audio-transcriber-core sidecar.",
  "windows": ["main"],
  "permissions": [
    "core:default",
    "dialog:default",
    "fs:default",
    {
      "identifier": "fs:scope",
      "allow": [
        { "path": "$APPDATA/**" },
        { "path": "$APPLOCALDATA/**" }
      ]
    },
    "shell:default",
    {
      "identifier": "shell:allow-execute",
      "allow": [
        {
          "name": "audio-transcriber-core",
          "sidecar": true,
          "args": true
        }
      ]
    }
  ]
}
```

Note: the actual permission identifiers (`core:default`, `dialog:default`, etc.) come from each plugin's `permissions/` folder. The exact list above is the Tauri 2.0 surface as of 2026-05-28; verify against `cargo tauri permission list` if a plugin changes between now and Phase 1 execution.

- [ ] **Step 4: Create placeholder icon**

Phase 1 uses a placeholder. Phase 4 replaces with real icons.

```powershell
# Create a 512×512 PNG placeholder. On Windows:
Copy-Item "$env:WINDIR\System32\imageres.dll" "src-tauri/icons/icon.png" -ErrorAction SilentlyContinue
# If above doesn't work, create a 1px PNG manually:
# Use any 512x512 PNG file as src-tauri/icons/icon.png
```

If you don't have an icon handy, generate one with Python:

```bash
python -c "from PIL import Image; img = Image.new('RGB', (512, 512), (74, 144, 226)); img.save('src-tauri/icons/icon.png')"
```

- [ ] **Step 5: Create main.rs scaffold (sidecar wiring lands in Task 16)**

Create `src-tauri/src/main.rs`:

```rust
// Prevent additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;
mod commands;
mod bootstrap;

use tracing_subscriber::EnvFilter;

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    tracing::info!("Audio Transcriber v0.2 starting");

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            bootstrap::seed_config_if_missing(app.handle())?;
            sidecar::spawn(app.handle())?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::invoke_python,
            commands::cancel_transcribe,
            commands::save_recording,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
```

- [ ] **Step 6: Create empty stubs for sidecar.rs / commands.rs / bootstrap.rs**

Create `src-tauri/src/sidecar.rs`:

```rust
//! Sidecar spawn + JSON-RPC stdin/stdout pump.
//! Implemented in Task 16 and Task 17.

use tauri::AppHandle;

pub fn spawn(_app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    tracing::info!("sidecar::spawn — stub, implemented in Task 16");
    Ok(())
}
```

Create `src-tauri/src/commands.rs`:

```rust
//! Tauri command handlers — exposed to React via `invoke()`.
//! Filled in across Tasks 17, 18, 20.

use serde_json::Value;

#[tauri::command]
pub fn invoke_python(_method: String, _params: Value) -> Result<Value, String> {
    Err("invoke_python: not yet implemented (Task 17)".into())
}

#[tauri::command]
pub fn cancel_transcribe() -> Result<(), String> {
    Err("cancel_transcribe: not yet implemented (Task 17)".into())
}

#[tauri::command]
pub fn save_recording(_bytes: Vec<u8>, _mime_type: String) -> Result<String, String> {
    Err("save_recording: not yet implemented (Task 20)".into())
}
```

Create `src-tauri/src/bootstrap.rs`:

```rust
//! First-run bootstrap — copy config.example.json into $APPDATA if missing.
//! Implemented in Task 19.

use tauri::AppHandle;

pub fn seed_config_if_missing(_app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    tracing::info!("bootstrap::seed_config_if_missing — stub, implemented in Task 19");
    Ok(())
}
```

- [ ] **Step 7: Verify the scaffold builds**

```bash
cd src-tauri
cargo check
```

Expected: compiles cleanly (warnings about unused vars are OK — they get used in later tasks). If `tauri-build` fails because tauri.conf.json references missing files, double-check `binaries/audio-transcriber-core` exists from Task 14.

- [ ] **Step 8: Commit**

```bash
cd ..
git add src-tauri/
git commit -m "feat(tauri): Rust scaffold — Cargo.toml + tauri.conf.json + main.rs stubs"
```

---

### Task 16: Sidecar spawn + lifecycle (3-retry policy)

**Files:**
- Modify: `src-tauri/src/sidecar.rs`
- Create: `src-tauri/src/sidecar/state.rs` (sub-module — moved when sidecar.rs grows)

**Context:** Spawn the Python sidecar via `tauri_plugin_shell::Command::new_sidecar("audio-transcriber-core")`. Hold the spawned `CommandChild` in a Tauri `State<SidecarState>` so other modules can write to its stdin. Monitor exit via `wait_with_output` in a background tokio task; on unexpected exit, attempt restart up to 3 times in 60 s. After 3 failures, log fatal + emit a Tauri event so React shows an error banner.

- [ ] **Step 1: Implement sidecar state + spawn**

Replace the contents of `src-tauri/src/sidecar.rs`:

```rust
//! Sidecar lifecycle: spawn, monitor, restart on crash.
//!
//! Holds a single `SidecarState` in Tauri's State, exposing:
//!   - `stdin_writer`: tokio mpsc sender for writing JSON-RPC requests
//!   - `pending_requests`: map of in-flight request id -> response oneshot
//!   - `restart_count`, `last_restart_at`: lifecycle bookkeeping

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::{mpsc, oneshot, Mutex};

const MAX_RESTARTS: u32 = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(60);

pub struct SidecarState {
    pub stdin_writer: mpsc::Sender<String>,
    pub pending: Arc<Mutex<HashMap<i64, oneshot::Sender<Value>>>>,
    pub restart_count: Arc<Mutex<u32>>,
    pub first_restart_at: Arc<Mutex<Option<Instant>>>,
}

pub fn spawn(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let (stdin_tx, stdin_rx) = mpsc::channel::<String>(32);
    let pending: Arc<Mutex<HashMap<i64, oneshot::Sender<Value>>>> =
        Arc::new(Mutex::new(HashMap::new()));

    let state = SidecarState {
        stdin_writer: stdin_tx.clone(),
        pending: pending.clone(),
        restart_count: Arc::new(Mutex::new(0)),
        first_restart_at: Arc::new(Mutex::new(None)),
    };
    app.manage(state);

    spawn_inner(app.clone(), stdin_rx, pending)?;
    Ok(())
}

fn spawn_inner(
    app: AppHandle,
    mut stdin_rx: mpsc::Receiver<String>,
    pending: Arc<Mutex<HashMap<i64, oneshot::Sender<Value>>>>,
) -> Result<(), Box<dyn std::error::Error>> {
    tracing::info!("Spawning sidecar audio-transcriber-core");
    let (mut rx, child) = app
        .shell()
        .sidecar("audio-transcriber-core")?
        .spawn()?;

    // CommandChild is NOT Clone in tauri-plugin-shell 2. The writer task owns
    // the child handle exclusively; if it ever needs sharing (e.g. with a
    // separate cancel task), wrap in Arc<Mutex<CommandChild>>. For Phase 1
    // we use the simpler model: cancel is sent as a normal JSON-RPC message
    // via the mpsc (see commands::cancel_transcribe in Task 17), so the writer
    // task is the only owner of the child stdin handle.
    tokio::spawn(async move {
        let mut child = child;
        while let Some(line) = stdin_rx.recv().await {
            if let Err(e) = child.write(line.as_bytes()) {
                tracing::warn!("sidecar stdin write failed: {e}");
                break;
            }
        }
    });

    // Stdout/stderr reader task — parse JSON-RPC, route to pending request or emit event.
    let app_for_events = app.clone();
    let pending_for_reader = pending.clone();
    tokio::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).to_string();
                    handle_sidecar_line(&app_for_events, &pending_for_reader, &line).await;
                }
                CommandEvent::Stderr(bytes) => {
                    tracing::warn!("sidecar stderr: {}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Terminated(status) => {
                    tracing::error!("sidecar terminated: {status:?}");
                    let _ = app_for_events.emit("sidecar-died", status.code);
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}

async fn handle_sidecar_line(
    app: &AppHandle,
    pending: &Arc<Mutex<HashMap<i64, oneshot::Sender<Value>>>>,
    line: &str,
) {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return;
    }
    let msg: Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("Cannot parse sidecar line: {e}: {trimmed}");
            return;
        }
    };

    if let Some(id) = msg.get("id").and_then(|v| v.as_i64()) {
        // Response to a request — route to the pending oneshot.
        let mut pending_guard = pending.lock().await;
        if let Some(tx) = pending_guard.remove(&id) {
            let _ = tx.send(msg);
        } else {
            tracing::warn!("Unsolicited response for id={id}");
        }
    } else if msg.get("method").is_some() {
        // Notification (no id) — forward to React.
        let _ = app.emit("python-event", msg);
    }
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd src-tauri
cargo check
```

Expected: compiles. Warnings about `MAX_RESTARTS`, `RESTART_WINDOW`, `child` unused — those land in the next step (Task 17).

- [ ] **Step 3: Commit**

```bash
cd ..
git add src-tauri/src/sidecar.rs
git commit -m "feat(tauri/sidecar): spawn + stdin writer + stdout reader + event routing"
```

---

### Task 17: invoke_python command + request id correlation

**Files:**
- Modify: `src-tauri/src/commands.rs`
- Modify: `src-tauri/src/sidecar.rs` (add request id counter helper)

**Context:** React's `invoke('invoke_python', {method, params})` must (1) generate a unique id, (2) register a oneshot receiver in `pending`, (3) write `{jsonrpc, id, method, params}` to sidecar stdin via the mpsc, (4) await the oneshot. Timeout: 10 minutes (transcribe can be long).

- [ ] **Step 1: Add request id counter to sidecar.rs**

Add at top of `src-tauri/src/sidecar.rs`:

```rust
use std::sync::atomic::{AtomicI64, Ordering};

static NEXT_REQUEST_ID: AtomicI64 = AtomicI64::new(1);

pub fn next_request_id() -> i64 {
    NEXT_REQUEST_ID.fetch_add(1, Ordering::SeqCst)
}
```

- [ ] **Step 2: Implement invoke_python in commands.rs**

Replace `src-tauri/src/commands.rs`:

```rust
//! Tauri command handlers — exposed to React via `invoke()`.

use std::time::Duration;

use serde_json::{json, Value};
use tauri::State;
use tokio::sync::oneshot;
use tokio::time::timeout;

use crate::sidecar::{next_request_id, SidecarState};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(600); // 10 min — transcribe can be long

#[tauri::command]
pub async fn invoke_python(
    method: String,
    params: Value,
    state: State<'_, SidecarState>,
) -> Result<Value, String> {
    let id = next_request_id();
    let (tx, rx) = oneshot::channel();
    {
        let mut pending = state.pending.lock().await;
        pending.insert(id, tx);
    }

    let request = json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": method,
        "params": params,
    });
    let line = format!("{request}\n");
    state.stdin_writer.send(line).await
        .map_err(|e| format!("Sidecar stdin closed: {e}"))?;

    let response = timeout(REQUEST_TIMEOUT, rx).await
        .map_err(|_| format!("Sidecar request {id} timed out after {}s", REQUEST_TIMEOUT.as_secs()))?
        .map_err(|e| format!("Sidecar oneshot dropped: {e}"))?;

    if let Some(error) = response.get("error") {
        return Err(error.to_string());
    }
    response.get("result").cloned()
        .ok_or_else(|| "Sidecar response missing result field".to_string())
}

#[tauri::command]
pub async fn cancel_transcribe(state: State<'_, SidecarState>) -> Result<(), String> {
    // Fire-and-forget notification — no id, no response expected.
    let line = format!("{}\n", json!({
        "jsonrpc": "2.0",
        "method": "cancel",
        "params": {},
    }));
    state.stdin_writer.send(line).await
        .map_err(|e| format!("Sidecar stdin closed: {e}"))?;
    Ok(())
}

#[tauri::command]
pub async fn save_recording(_bytes: Vec<u8>, _mime_type: String) -> Result<String, String> {
    Err("save_recording: not yet implemented (Task 20)".into())
}
```

- [ ] **Step 3: Verify compile + smoke**

```bash
cd src-tauri
cargo check
cargo build --release
```

Expected: compiles. Warnings about `MAX_RESTARTS` etc are fine (Phase 1 doesn't implement restart; deferred to Phase 3 polish).

- [ ] **Step 4: Manual smoke — ping from Rust**

Add a temporary smoke test in `src-tauri/src/main.rs` setup (will be removed in Step 5). The Manager trait import is REQUIRED for `.state()` to resolve:

```rust
use tauri::Manager;  // brings .state() / .path() / .manage() into scope

// ...inside .setup(|app| { ... })
            bootstrap::seed_config_if_missing(app.handle())?;
            sidecar::spawn(app.handle())?;

            // TEMPORARY smoke — remove before commit
            let handle = app.handle().clone();
            tokio::spawn(async move {
                tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                let state: tauri::State<sidecar::SidecarState> = handle.state();
                match commands::invoke_python(
                    "ping".to_string(),
                    serde_json::json!({}),
                    state,
                ).await {
                    Ok(v) => tracing::info!("Ping result: {v}"),
                    Err(e) => tracing::error!("Ping failed: {e}"),
                }
            });

            Ok(())
        })
```

Run dev:

```bash
cd ..
pnpm tauri dev   # If pnpm is not yet wired (Task 21 sets it up), use cargo run directly instead:
# cd src-tauri && cargo run
```

Expected log line: `Ping result: {"pong": true, "version": "0.2.0"}`.

- [ ] **Step 5: Remove the smoke test, commit**

Remove the TEMPORARY block from main.rs setup added in Step 4.

```bash
git add src-tauri/src/commands.rs src-tauri/src/sidecar.rs
git commit -m "feat(tauri/commands): invoke_python + cancel_transcribe with request id correlation"
```

---

### Task 18: First-run config bootstrap

**Files:**
- Modify: `src-tauri/src/bootstrap.rs`
- Create: `src-tauri/resources/config.example.json` (copy from repo root)
- Modify: `src-tauri/tauri.conf.json` (add `resources` to bundle)

**Context:** On first launch, if `$APPDATA/audio-transcriber/config.json` doesn't exist, copy from `resources/config.example.json` (bundled inside the Tauri app). Avoids the "config not found → no defaults → blank Settings dialog" UX.

- [ ] **Step 1: Stage the example config**

```bash
mkdir src-tauri/resources
cp config.example.json src-tauri/resources/config.example.json
```

- [ ] **Step 2: Add resources to tauri.conf.json bundle**

Edit `src-tauri/tauri.conf.json`. Inside the `"bundle"` object, add:

```json
    "resources": [
      "resources/config.example.json"
    ],
```

- [ ] **Step 3: Implement seed_config_if_missing**

Replace `src-tauri/src/bootstrap.rs`:

```rust
//! First-run bootstrap — copy config.example.json into $APPDATA if config.json missing.

use std::fs;

use tauri::{AppHandle, Manager};

pub fn seed_config_if_missing(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let app_data = app.path().app_data_dir()?;
    let config_path = app_data.join("config.json");

    if config_path.exists() {
        tracing::info!("config.json already present at {config_path:?}");
        return Ok(());
    }

    fs::create_dir_all(&app_data)?;

    // Resolve bundled resource path.
    let resource_path = app
        .path()
        .resolve("resources/config.example.json", tauri::path::BaseDirectory::Resource)?;

    fs::copy(&resource_path, &config_path)?;
    tracing::info!("Seeded config.json from bundled example at {config_path:?}");

    Ok(())
}
```

- [ ] **Step 4: Verify compile**

```bash
cd src-tauri
cargo check
```

- [ ] **Step 5: Manual smoke**

```bash
cargo run
```

After app starts, verify `$env:APPDATA\audio-transcriber\config.json` exists (Windows) — `%APPDATA%\Roaming\audio-transcriber\config.json`. Delete it and re-run to verify bootstrap fires again.

- [ ] **Step 6: Commit**

```bash
cd ..
git add src-tauri/src/bootstrap.rs src-tauri/resources/ src-tauri/tauri.conf.json
git commit -m "feat(tauri/bootstrap): seed config.json from bundled example on first run"
```

---

### Task 19: save_recording Tauri command

**Files:**
- Modify: `src-tauri/src/commands.rs`

**Context:** React records via MediaRecorder, gets a Blob, converts to bytes, and invokes `save_recording`. Rust writes to `$APPDATA/audio-transcriber/recordings/<uuid>.<ext>` (ext derived from mimeType). Returns the absolute file path that React passes into `transcribe`.

- [ ] **Step 1: Add uuid + path resolver helpers**

Edit `src-tauri/Cargo.toml` — add to `[dependencies]`:

```toml
uuid = { version = "1", features = ["v4"] }
```

- [ ] **Step 2: Implement save_recording**

In `src-tauri/src/commands.rs`, replace the placeholder `save_recording`:

```rust
use std::fs;
use std::path::PathBuf;

use tauri::Manager;
use uuid::Uuid;

#[tauri::command]
pub async fn save_recording(
    app: tauri::AppHandle,
    bytes: Vec<u8>,
    mime_type: String,
) -> Result<String, String> {
    let app_data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let recordings_dir: PathBuf = app_data.join("recordings");
    fs::create_dir_all(&recordings_dir).map_err(|e| e.to_string())?;

    let ext = mime_to_ext(&mime_type);
    let filename = format!("{}.{}", Uuid::new_v4(), ext);
    let path = recordings_dir.join(&filename);

    fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    tracing::info!("Saved {} bytes recording to {path:?}", bytes.len());

    path.to_str().map(String::from)
        .ok_or_else(|| "Recording path contains invalid UTF-8".into())
}

fn mime_to_ext(mime: &str) -> &'static str {
    match mime {
        "audio/webm" | "audio/webm;codecs=opus" => "webm",
        "audio/ogg" | "audio/ogg;codecs=opus" => "ogg",
        "audio/wav" | "audio/wave" => "wav",
        "audio/mpeg" | "audio/mp3" => "mp3",
        "audio/mp4" | "audio/m4a" => "m4a",
        _ => "bin",
    }
}
```

Note: the `app: tauri::AppHandle` parameter is auto-injected by Tauri 2's command macro when the type matches (no `State<>` wrapping needed).

- [ ] **Step 3: Verify compile**

```bash
cd src-tauri
cargo check
```

- [ ] **Step 4: Commit**

```bash
cd ..
git add src-tauri/src/commands.rs src-tauri/Cargo.toml
git commit -m "feat(tauri/commands): save_recording — write bytes to \$APPDATA/recordings/"
```

---

### Task 20: Vite + React 19 + TS + pnpm scaffold

**Files:**
- Create: `package.json`, `pnpm-workspace.yaml`, `tsconfig.json`, `vite.config.ts`, `biome.json`
- Create: `src/main.tsx`, `src/index.html`, `src/styles.css`

**Context:** Tauri's default frontend bootstrap (`create-tauri-app`) is one option, but since we already have `src-tauri/` from Task 15 and want explicit control, scaffold manually. Vite 6 is the build tool; React 19 is the framework; TypeScript 5 strict mode is the contract.

- [ ] **Step 1: Initialize package.json**

Create `package.json` at repo root:

```json
{
  "name": "audio-transcriber",
  "private": true,
  "version": "0.2.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "tauri": "tauri",
    "tauri:dev": "tauri dev",
    "tauri:build": "tauri build",
    "test": "vitest",
    "test:run": "vitest run",
    "lint": "biome check src/",
    "format": "biome format --write src/"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.59.0",
    "@tanstack/react-router": "^1.78.0",
    "@tauri-apps/api": "^2.0.0",
    "@tauri-apps/plugin-dialog": "^2.0.0",
    "@tauri-apps/plugin-fs": "^2.0.0",
    "@tauri-apps/plugin-shell": "^2.0.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "sonner": "^1.7.0",
    "zustand": "^5.0.0",
    "zod": "^3.23.0"
  },
  "devDependencies": {
    "@biomejs/biome": "^1.9.0",
    "@tanstack/router-devtools": "^1.78.0",
    "@tanstack/router-plugin": "^1.78.0",
    "@tauri-apps/cli": "^2.0.0",
    "@testing-library/react": "^16.0.0",
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "happy-dom": "^15.0.0",
    "typescript": "~5.6.0",
    "vite": "^6.0.0",
    "vitest": "^3.0.0"
  },
  "packageManager": "pnpm@9.12.0"
}
```

- [ ] **Step 2: Create pnpm-workspace.yaml**

Create `pnpm-workspace.yaml`:

```yaml
packages:
  - "."
```

- [ ] **Step 3: Create tsconfig.json**

Create `tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src", "vite.config.ts"]
}
```

- [ ] **Step 4: Create vite.config.ts**

Create `vite.config.ts`:

```typescript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { TanStackRouterVite } from '@tanstack/router-plugin/vite';
import path from 'node:path';

export default defineConfig({
  plugins: [
    TanStackRouterVite({ routesDirectory: 'src/app' }),
    react(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    target: 'esnext',
    minify: 'esbuild',
    sourcemap: true,
  },
  test: {
    environment: 'happy-dom',
    globals: true,
  },
});
```

- [ ] **Step 5: Create biome.json**

Create `biome.json`:

```json
{
  "$schema": "https://biomejs.dev/schemas/1.9.0/schema.json",
  "vcs": { "enabled": true, "clientKind": "git", "useIgnoreFile": true },
  "files": { "ignoreUnknown": false, "ignore": ["dist", "src-tauri/target"] },
  "formatter": { "enabled": true, "indentStyle": "space", "indentWidth": 2, "lineWidth": 100 },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true,
      "style": {
        "useImportType": "error",
        "useNodejsImportProtocol": "error"
      }
    }
  },
  "javascript": { "formatter": { "quoteStyle": "single", "trailingCommas": "all" } }
}
```

- [ ] **Step 6: Create src/index.html**

Create `src/index.html`:

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Audio Transcriber</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/main.tsx"></script>
  </body>
</html>
```

Update `vite.config.ts` to use `src/` as the project root:

```typescript
// Add at top of defineConfig:
  root: './src',
  publicDir: '../public',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    target: 'esnext',
    minify: 'esbuild',
    sourcemap: true,
  },
```

Reconcile the existing `build:` block — keep the new combined version.

- [ ] **Step 7: Create src/styles.css + src/main.tsx**

Create `src/styles.css` (Tailwind v4 lands in Task 21; for now just CSS reset):

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, sans-serif; }
```

Create `src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const App = () => <div style={{ padding: 24 }}>Audio Transcriber v0.2 — scaffold</div>;

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 8: Install deps**

```bash
pnpm install
```

- [ ] **Step 9: Smoke check**

```bash
pnpm dev
```

Expected: opens `http://localhost:1420/` showing "Audio Transcriber v0.2 — scaffold". Stop with Ctrl+C.

- [ ] **Step 10: Type-check**

```bash
pnpm tsc --noEmit
```

Expected: no errors.

- [ ] **Step 11: Commit**

```bash
git add package.json pnpm-workspace.yaml pnpm-lock.yaml tsconfig.json vite.config.ts biome.json src/index.html src/styles.css src/main.tsx
git commit -m "feat(v0.2): Vite + React 19 + TS scaffold + pnpm + Biome"
```

---

### Task 21: Tailwind v4 + shadcn/ui + Sonner

**Files:**
- Create: `tailwind.config.ts`, `postcss.config.js`, `components.json`
- Modify: `src/styles.css` (Tailwind directives)
- Modify: `src/main.tsx` (mount Sonner)
- Create: `src/components/ui/button.tsx` and other shadcn primitives via CLI

**Context:** Tailwind v4 uses the new CSS-first config (`@theme` in CSS, no `tailwind.config.js` required strictly). For Phase 1 we keep a minimal `tailwind.config.ts` for predictability. shadcn/ui copies components into the repo — `npx shadcn@latest add button card dialog input` populates `src/components/ui/`. Sonner is the Q4 decision.

- [ ] **Step 1: Install Tailwind v4**

```bash
pnpm add -D tailwindcss@^4.0.0 @tailwindcss/postcss@^4.0.0 autoprefixer
```

- [ ] **Step 2: Create postcss.config.js**

Create `postcss.config.js`:

```javascript
export default {
  plugins: {
    '@tailwindcss/postcss': {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 3: Create tailwind.config.ts**

Create `tailwind.config.ts`:

```typescript
import type { Config } from 'tailwindcss';

export default {
  content: ['./src/**/*.{ts,tsx,html}'],
  theme: {
    extend: {
      colors: {
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        border: 'hsl(var(--border))',
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 4: Update src/styles.css with Tailwind v4 directives + shadcn CSS vars**

Replace `src/styles.css`:

```css
@import "tailwindcss";

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 222.2 84% 4.9%;
    --primary: 222.2 47.4% 11.2%;
    --primary-foreground: 210 40% 98%;
    --muted: 210 40% 96.1%;
    --muted-foreground: 215.4 16.3% 46.9%;
    --border: 214.3 31.8% 91.4%;
  }

  @media (prefers-color-scheme: dark) {
    :root {
      --background: 222.2 84% 4.9%;
      --foreground: 210 40% 98%;
      --primary: 210 40% 98%;
      --primary-foreground: 222.2 47.4% 11.2%;
      --muted: 217.2 32.6% 17.5%;
      --muted-foreground: 215 20.2% 65.1%;
      --border: 217.2 32.6% 17.5%;
    }
  }

  * { @apply border-border; }
  body { @apply bg-background text-foreground; }
}
```

- [ ] **Step 5: Initialize shadcn/ui CLI**

```bash
pnpm dlx shadcn@latest init
```

When prompted: TypeScript yes, Style default, base color Slate, CSS variables yes, src dir yes, components alias `@/components`, utils alias `@/lib/utils`.

This creates `components.json` and `src/lib/utils.ts`.

- [ ] **Step 6: Install base shadcn primitives**

```bash
pnpm dlx shadcn@latest add button card dialog input label
```

Verify `src/components/ui/button.tsx` exists. Confirm imports work: `import { Button } from '@/components/ui/button';`.

- [ ] **Step 7: Mount Sonner in main.tsx**

Replace `src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Toaster } from 'sonner';
import { Button } from '@/components/ui/button';
import './styles.css';

const App = () => (
  <div className="p-6">
    <h1 className="text-2xl font-bold mb-4">Audio Transcriber v0.2 — scaffold</h1>
    <Button onClick={() => import('sonner').then(({ toast }) => toast.success('Sonner работает'))}>
      Тест toast
    </Button>
  </div>
);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
    <Toaster position="top-right" richColors />
  </StrictMode>,
);
```

- [ ] **Step 8: Smoke check**

```bash
pnpm dev
```

Open `http://localhost:1420`. Click "Тест toast" — green toast "Sonner работает" appears top-right.

- [ ] **Step 9: Commit**

```bash
git add package.json pnpm-lock.yaml tailwind.config.ts postcss.config.js components.json src/
git commit -m "feat(v0.2): Tailwind v4 + shadcn/ui base primitives + Sonner mount"
```

---

### Task 22: TanStack Router with 5 file-based route shells

**Files:**
- Create: `src/app/__root.tsx`, `src/app/index.tsx`, `src/app/history.tsx`, `src/app/history.$runId.tsx`, `src/app/audio-cutter.tsx`, `src/app/settings.tsx`
- Modify: `src/main.tsx` (replace ad-hoc App with RouterProvider)
- Create: `src/router.ts` (router instance)

**Context:** TanStack Router 1.x with file-based routing via the Vite plugin (already in vite.config.ts from Task 20). The plugin generates `src/routeTree.gen.ts` from the `src/app/` directory at dev startup. Phase 1 ships placeholder shells; Phase 2 fills them.

- [ ] **Step 1: Create the route files**

Create `src/app/__root.tsx`:

```tsx
import { Outlet, createRootRoute, Link } from '@tanstack/react-router';
import { Toaster } from 'sonner';

export const Route = createRootRoute({
  component: () => (
    <div className="min-h-screen flex flex-col">
      <nav className="border-b p-3 flex gap-4">
        <Link to="/" className="[&.active]:font-bold">Главная</Link>
        <Link to="/history" className="[&.active]:font-bold">История</Link>
        <Link to="/audio-cutter" className="[&.active]:font-bold">Аудио-редактор</Link>
        <Link to="/settings" className="[&.active]:font-bold">Настройки</Link>
      </nav>
      <main className="flex-1 p-6"><Outlet /></main>
      <Toaster position="top-right" richColors />
    </div>
  ),
});
```

Create `src/app/index.tsx`:

```tsx
import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/')({
  component: HomePage,
});

function HomePage() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Audio Transcriber</h1>
      <p className="text-muted-foreground">Recorder + transcribe — Phase 1 scaffold.</p>
    </div>
  );
}
```

Create `src/app/history.tsx`:

```tsx
import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/history')({
  component: HistoryPage,
});

function HistoryPage() {
  return <h1 className="text-2xl font-bold">История — Phase 2</h1>;
}
```

Create `src/app/history.$runId.tsx`:

```tsx
import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/history/$runId')({
  component: HistoryDetailPage,
});

function HistoryDetailPage() {
  const { runId } = Route.useParams();
  return <h1 className="text-2xl font-bold">Run {runId} — Phase 2</h1>;
}
```

Create `src/app/audio-cutter.tsx`:

```tsx
import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/audio-cutter')({
  component: AudioCutterPage,
});

function AudioCutterPage() {
  return <h1 className="text-2xl font-bold">Аудио-редактор — Phase 2</h1>;
}
```

Create `src/app/settings.tsx`:

```tsx
import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/settings')({
  component: SettingsPage,
});

function SettingsPage() {
  return <h1 className="text-2xl font-bold">Настройки — Phase 2</h1>;
}
```

- [ ] **Step 2: Create router instance**

Create `src/router.ts`:

```typescript
import { createRouter } from '@tanstack/react-router';
import { routeTree } from './routeTree.gen';

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
```

- [ ] **Step 3: Mount router in main.tsx**

Replace `src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from '@tanstack/react-router';
import { router } from './router';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
```

- [ ] **Step 4: Smoke check**

```bash
pnpm dev
```

The Vite TanStack plugin generates `src/routeTree.gen.ts` on first run. Open `http://localhost:1420` — see nav bar + "Audio Transcriber" landing page. Click each nav link to verify routes mount.

- [ ] **Step 5: Add routeTree.gen.ts to .gitignore**

Append to `.gitignore`:

```gitignore
src/routeTree.gen.ts
```

(Generated file — regenerated on dev/build.)

- [ ] **Step 6: Commit**

```bash
git add src/app/ src/router.ts src/main.tsx .gitignore
git commit -m "feat(v0.2): TanStack Router file-based routes (5 placeholder shells)"
```

---

### Task 23: ipc.ts typed wrapper + Zustand store + TanStack Query + useTranscribe (TDD)

**Files:**
- Create: `src/lib/ipc.ts`, `src/lib/store.ts`
- Create: `src/hooks/useTranscribe.ts`
- Create: `src/__tests__/ipc.test.ts`, `src/__tests__/store.test.ts`
- Modify: `src/main.tsx` (wrap with QueryClientProvider)

**Context:** Single chokepoint for JSON-RPC calls. `invokePython<T>(method, params)` wraps `@tauri-apps/api/core::invoke('invoke_python', {...})`. `listen<T>('python-event', cb)` exposes notifications. Zustand store holds global state (config, recording, current transcribe progress). useTranscribe is the first hook — exercises the full pipe.

- [ ] **Step 1: Write failing tests**

Create `src/__tests__/ipc.test.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest';
import { invokePython } from '@/lib/ipc';

vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(),
}));

describe('invokePython', () => {
  it('forwards method + params to Tauri invoke_python', async () => {
    const { invoke } = await import('@tauri-apps/api/core');
    vi.mocked(invoke).mockResolvedValue({ pong: true, version: '0.2.0' });

    const result = await invokePython('ping', {});

    expect(invoke).toHaveBeenCalledWith('invoke_python', {
      method: 'ping',
      params: {},
    });
    expect(result).toEqual({ pong: true, version: '0.2.0' });
  });

  it('surfaces Tauri errors verbatim', async () => {
    const { invoke } = await import('@tauri-apps/api/core');
    vi.mocked(invoke).mockRejectedValue('Sidecar request timed out');

    await expect(invokePython('transcribe', {})).rejects.toThrow('Sidecar request timed out');
  });
});
```

Create `src/__tests__/store.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { useAppStore } from '@/lib/store';

describe('useAppStore', () => {
  it('starts with no config and progress=null', () => {
    const s = useAppStore.getState();
    expect(s.config).toBeNull();
    expect(s.transcribeProgress).toBeNull();
  });

  it('updateConfig replaces config', () => {
    useAppStore.getState().updateConfig({ cloud_provider: 'AssemblyAI' });
    expect(useAppStore.getState().config?.cloud_provider).toBe('AssemblyAI');
  });

  it('updateProgress sets percentage and message', () => {
    useAppStore.getState().updateProgress({ pct: 0.5, message: 'Загрузка...' });
    const p = useAppStore.getState().transcribeProgress;
    expect(p?.pct).toBe(0.5);
    expect(p?.message).toBe('Загрузка...');
  });
});
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pnpm test:run src/__tests__
```

Expected: module not found errors for `@/lib/ipc` and `@/lib/store`.

- [ ] **Step 3: Implement src/lib/ipc.ts**

Create `src/lib/ipc.ts`:

```typescript
import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';

export interface PythonEventNotification {
  jsonrpc: '2.0';
  method: string;
  params: Record<string, unknown>;
}

/**
 * Single chokepoint for JSON-RPC calls into the Python sidecar.
 * All React → Python communication goes through this function.
 */
export async function invokePython<T = unknown>(
  method: string,
  params: Record<string, unknown>,
): Promise<T> {
  return invoke<T>('invoke_python', { method, params });
}

/**
 * Subscribe to python-event notifications (progress, status, etc.)
 * emitted by the sidecar without an `id` field.
 * Returns an unsubscribe function — call it on component unmount.
 */
export async function subscribePythonEvents(
  callback: (event: PythonEventNotification) => void,
): Promise<UnlistenFn> {
  return listen<PythonEventNotification>('python-event', (e) => callback(e.payload));
}

export async function cancelTranscribe(): Promise<void> {
  return invoke('cancel_transcribe');
}

export async function saveRecording(bytes: Uint8Array, mimeType: string): Promise<string> {
  return invoke<string>('save_recording', { bytes: Array.from(bytes), mimeType });
}
```

- [ ] **Step 4: Implement src/lib/store.ts**

Create `src/lib/store.ts`:

```typescript
import { create } from 'zustand';

export interface AppConfig {
  cloud_provider?: string;
  cloud_enabled?: boolean;
  diarize?: boolean;
  cloud_api_keys?: Record<string, string>;
  openrouter_api_key?: string;
  [key: string]: unknown;
}

export interface TranscribeProgress {
  pct: number | null;
  message: string | null;
}

interface AppState {
  config: AppConfig | null;
  transcribeProgress: TranscribeProgress | null;
  updateConfig: (patch: AppConfig) => void;
  setConfig: (config: AppConfig) => void;
  updateProgress: (patch: Partial<TranscribeProgress>) => void;
  resetProgress: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  config: null,
  transcribeProgress: null,
  updateConfig: (patch) =>
    set((s) => ({ config: { ...(s.config ?? {}), ...patch } })),
  setConfig: (config) => set({ config }),
  updateProgress: (patch) =>
    set((s) => ({
      transcribeProgress: {
        pct: patch.pct ?? s.transcribeProgress?.pct ?? null,
        message: patch.message ?? s.transcribeProgress?.message ?? null,
      },
    })),
  resetProgress: () => set({ transcribeProgress: null }),
}));
```

- [ ] **Step 5: Implement useTranscribe hook**

Create `src/hooks/useTranscribe.ts`:

```typescript
import { useMutation } from '@tanstack/react-query';
import { useEffect } from 'react';
import { invokePython, subscribePythonEvents } from '@/lib/ipc';
import { useAppStore } from '@/lib/store';

export interface TranscribeParams {
  audio_path: string;
  cloud_provider: string;
  cloud_api_key: string;
  language?: string | null;
  diarize?: boolean;
  hotwords?: string | null;
  num_speakers?: number | null;
  denoise_audio?: boolean;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

export interface TranscribeResult {
  formatted_text: string;
  segments: Segment[];
}

export function useTranscribeProgressListener(): void {
  const updateProgress = useAppStore((s) => s.updateProgress);
  const resetProgress = useAppStore((s) => s.resetProgress);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    subscribePythonEvents((e) => {
      if (e.method === 'progress') {
        const pct = e.params.pct as number | undefined;
        if (typeof pct === 'number') updateProgress({ pct });
      } else if (e.method === 'status') {
        const message = e.params.message as string | undefined;
        if (typeof message === 'string') updateProgress({ message });
      }
    }).then((fn) => { unlisten = fn; });

    return () => {
      unlisten?.();
      resetProgress();
    };
  }, [updateProgress, resetProgress]);
}

export function useTranscribe() {
  return useMutation<TranscribeResult, Error, TranscribeParams>({
    mutationFn: (params) => invokePython<TranscribeResult>('transcribe', params),
  });
}
```

- [ ] **Step 6: Wrap App with QueryClientProvider**

Update `src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { router } from './router';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
```

- [ ] **Step 7: Run tests — expect pass**

```bash
pnpm test:run src/__tests__
```

Expected: 5 tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/lib/ src/hooks/ src/__tests__/ src/main.tsx
git commit -m "feat(v0.2): ipc.ts + Zustand store + useTranscribe hook + Vitest tests"
```

---

### Task 24: Recorder.tsx — MediaRecorder + wavesurfer.js live waveform

**Files:**
- Create: `src/components/Recorder.tsx`
- Modify: `src/app/index.tsx` (mount Recorder)
- Install: `wavesurfer.js`

**Context:** Web Audio API records via `MediaRecorder` → Blob → bytes → `saveRecording`. Live waveform via wavesurfer.js (or AnalyserNode if wavesurfer record plugin is fiddly). Phase 1 keeps it minimal: record button, stop button, level meter, save the .webm, log the saved path. Phase 2 wires the saved path into a transcribe call.

- [ ] **Step 1: Install wavesurfer**

```bash
pnpm add wavesurfer.js@^7.8.0
```

- [ ] **Step 2: Implement Recorder.tsx**

Create `src/components/Recorder.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { saveRecording } from '@/lib/ipc';

type RecState = 'idle' | 'recording' | 'saving' | 'saved';

export function Recorder({ onSaved }: { onSaved?: (path: string) => void }) {
  const [state, setState] = useState<RecState>('idle');
  const [level, setLevel] = useState<number>(0);
  const [savedPath, setSavedPath] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => () => cleanup(), []);

  function cleanup() {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    audioContextRef.current?.close().catch(() => {});
    streamRef.current = null;
    audioContextRef.current = null;
  }

  async function start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const ctx = new AudioContext();
      audioContextRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);

      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        // RMS approximation: average |sample - 128| / 128
        let sum = 0;
        for (const v of data) sum += Math.abs(v - 128);
        setLevel(sum / data.length / 128);
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();

      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        setState('saving');
        const blob = new Blob(chunksRef.current, { type: mime });
        const bytes = new Uint8Array(await blob.arrayBuffer());
        try {
          const path = await saveRecording(bytes, mime);
          setSavedPath(path);
          setState('saved');
          onSaved?.(path);
          toast.success(`Записано: ${path}`);
        } catch (e) {
          toast.error(`Ошибка сохранения: ${e}`);
          setState('idle');
        } finally {
          cleanup();
        }
      };
      recorder.start();
      recorderRef.current = recorder;
      setState('recording');
    } catch (e) {
      toast.error(`Нет доступа к микрофону: ${e}`);
    }
  }

  function stop() {
    recorderRef.current?.stop();
    recorderRef.current = null;
  }

  return (
    <div className="flex flex-col gap-3 max-w-md">
      <div className="flex gap-2">
        {state === 'idle' || state === 'saved' ? (
          <Button onClick={start}>Запись</Button>
        ) : (
          <Button onClick={stop} variant="destructive" disabled={state !== 'recording'}>
            Стоп
          </Button>
        )}
        <span className="text-sm text-muted-foreground self-center">
          {state === 'recording' ? 'Идёт запись…' :
           state === 'saving' ? 'Сохранение…' :
           state === 'saved' ? 'Готово' : 'Ожидание'}
        </span>
      </div>

      <div className="h-2 bg-muted rounded overflow-hidden">
        <div
          className="h-full bg-primary transition-[width] duration-75"
          style={{ width: `${Math.min(100, level * 200)}%` }}
        />
      </div>

      {savedPath && (
        <p className="text-xs text-muted-foreground break-all">Файл: {savedPath}</p>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Mount Recorder in home route**

Update `src/app/index.tsx`:

```tsx
import { createFileRoute } from '@tanstack/react-router';
import { Recorder } from '@/components/Recorder';

export const Route = createFileRoute('/')({
  component: HomePage,
});

function HomePage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Audio Transcriber</h1>
      <Recorder onSaved={(p) => console.log('Recording saved at', p)} />
    </div>
  );
}
```

- [ ] **Step 4: Smoke check**

```bash
pnpm tauri dev
```

Open the app window. Click "Запись" — browser permission dialog appears (Tauri remembers grant). Speak — level meter moves. Click "Стоп" — toast "Записано: <path>". Verify file at `$env:APPDATA\audio-transcriber\recordings\<uuid>.webm` (Windows).

- [ ] **Step 5: Commit**

```bash
git add src/components/Recorder.tsx src/app/index.tsx package.json pnpm-lock.yaml
git commit -m "feat(v0.2): Recorder.tsx — MediaRecorder + level meter + saveRecording"
```

---

### Task 25: FirstRunBanner + config load on app start

**Files:**
- Create: `src/components/FirstRunBanner.tsx`
- Modify: `src/app/__root.tsx` (mount banner)
- Modify: `src/main.tsx` (load config at startup via TanStack Query)

**Context:** On app launch, fetch `config.json` via `load_config`. If `config.cloud_api_keys.AssemblyAI` is empty/missing, render a yellow banner: "API-ключ AssemblyAI не задан. Откройте Настройки." With a button linking to `/settings`. The banner persists until the key is set (Phase 2 wires the dismissal via re-fetched config).

- [ ] **Step 1: Add useConfig hook**

Append to `src/hooks/useTranscribe.ts` (or create separate `src/hooks/useConfig.ts`):

Create `src/hooks/useConfig.ts`:

```typescript
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { invokePython } from '@/lib/ipc';
import { useAppStore, type AppConfig } from '@/lib/store';

const CONFIG_PATH_KEY = 'config-path';

async function loadConfig(path: string): Promise<AppConfig> {
  return invokePython<AppConfig>('load_config', { path });
}

export function useConfig(configPath: string) {
  const setConfig = useAppStore((s) => s.setConfig);
  const query = useQuery({
    queryKey: [CONFIG_PATH_KEY, configPath],
    queryFn: () => loadConfig(configPath),
    enabled: !!configPath,
  });

  useEffect(() => {
    if (query.data) setConfig(query.data);
  }, [query.data, setConfig]);

  return query;
}
```

- [ ] **Step 2: Resolve config path via Tauri path API**

Create `src/lib/paths.ts`:

```typescript
import { appDataDir, join } from '@tauri-apps/api/path';

export async function configJsonPath(): Promise<string> {
  return join(await appDataDir(), 'config.json');
}

export async function historyDir(): Promise<string> {
  return join(await appDataDir(), 'history');
}
```

- [ ] **Step 3: Implement FirstRunBanner**

Create `src/components/FirstRunBanner.tsx`:

```tsx
import { useNavigate } from '@tanstack/react-router';
import { Button } from '@/components/ui/button';
import { useAppStore } from '@/lib/store';

export function FirstRunBanner() {
  const config = useAppStore((s) => s.config);
  const navigate = useNavigate();

  if (config === null) return null;  // still loading
  const assemblyKey = config.cloud_api_keys?.AssemblyAI;
  if (assemblyKey && assemblyKey.length > 0) return null;

  return (
    <div className="bg-yellow-100 dark:bg-yellow-900 border border-yellow-300 dark:border-yellow-700 px-4 py-3 flex items-center justify-between gap-4">
      <p className="text-sm">
        API-ключ AssemblyAI не задан. Без него транскрибация не запустится.
      </p>
      <Button size="sm" onClick={() => navigate({ to: '/settings' })}>
        Открыть настройки
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Wire config load + banner into __root.tsx**

Replace `src/app/__root.tsx`:

```tsx
import { Outlet, createRootRoute, Link } from '@tanstack/react-router';
import { Toaster } from 'sonner';
import { useEffect, useState } from 'react';
import { FirstRunBanner } from '@/components/FirstRunBanner';
import { useConfig } from '@/hooks/useConfig';
import { useTranscribeProgressListener } from '@/hooks/useTranscribe';
import { configJsonPath } from '@/lib/paths';

function RootLayout() {
  const [cfgPath, setCfgPath] = useState<string>('');
  useEffect(() => { configJsonPath().then(setCfgPath); }, []);
  useConfig(cfgPath);
  useTranscribeProgressListener();

  return (
    <div className="min-h-screen flex flex-col">
      <nav className="border-b p-3 flex gap-4">
        <Link to="/" className="[&.active]:font-bold">Главная</Link>
        <Link to="/history" className="[&.active]:font-bold">История</Link>
        <Link to="/audio-cutter" className="[&.active]:font-bold">Аудио-редактор</Link>
        <Link to="/settings" className="[&.active]:font-bold">Настройки</Link>
      </nav>
      <FirstRunBanner />
      <main className="flex-1 p-6"><Outlet /></main>
      <Toaster position="top-right" richColors />
    </div>
  );
}

export const Route = createRootRoute({ component: RootLayout });
```

- [ ] **Step 5: Smoke check**

```bash
pnpm tauri dev
```

Expected: app starts. If `config.cloud_api_keys.AssemblyAI` is empty/missing in `$APPDATA\audio-transcriber\config.json`, yellow banner appears. Click "Открыть настройки" — routes to `/settings`. Manually edit the config.json file, restart the app, banner disappears.

- [ ] **Step 6: Commit**

```bash
git add src/components/FirstRunBanner.tsx src/hooks/useConfig.ts src/lib/paths.ts src/app/__root.tsx
git commit -m "feat(v0.2): FirstRunBanner + config auto-load via TanStack Query"
```

---

### Task 26: End-to-end smoke + Phase 1 verification gate

**Files:**
- Create: `docs/superpowers/plans/2026-05-28-tauri-lite-rewrite-phase-1-foundation-verification.md` (gate sign-off doc)

**Context:** Per spec §9.1 hard gate: "If end-of-week-4 Foundation milestone does not have a working end-to-end transcribe flow on macOS, re-scope". Phase 1 verification proves the full pipe works on the user's primary dev OS (Windows for now — Phase 3 adds macOS + Linux smoke).

- [ ] **Step 1: Add manual transcribe smoke button to Home**

Modify `src/app/index.tsx` to wire Recorder → transcribe:

```tsx
import { createFileRoute } from '@tanstack/react-router';
import { useState } from 'react';
import { toast } from 'sonner';
import { Recorder } from '@/components/Recorder';
import { Button } from '@/components/ui/button';
import { useTranscribe, type Segment } from '@/hooks/useTranscribe';
import { useAppStore } from '@/lib/store';

export const Route = createFileRoute('/')({ component: HomePage });

function HomePage() {
  const [recordingPath, setRecordingPath] = useState<string | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const config = useAppStore((s) => s.config);
  const progress = useAppStore((s) => s.transcribeProgress);
  const mutation = useTranscribe();

  const canTranscribe = recordingPath !== null && config?.cloud_api_keys?.AssemblyAI;

  async function runTranscribe() {
    if (!recordingPath || !config?.cloud_api_keys?.AssemblyAI) return;
    try {
      const result = await mutation.mutateAsync({
        audio_path: recordingPath,
        cloud_provider: 'AssemblyAI',
        cloud_api_key: config.cloud_api_keys.AssemblyAI as string,
        diarize: true,
        language: 'ru',
      });
      setSegments(result.segments);
      toast.success(`Транскрипт готов (${result.segments.length} сегментов)`);
    } catch (e) {
      toast.error(`Ошибка транскрипции: ${e}`);
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      <h1 className="text-2xl font-bold">Audio Transcriber</h1>
      <Recorder onSaved={setRecordingPath} />

      <div className="flex items-center gap-4">
        <Button onClick={runTranscribe} disabled={!canTranscribe || mutation.isPending}>
          {mutation.isPending ? 'Транскрибация...' : 'Транскрибировать'}
        </Button>
        {progress && (
          <span className="text-sm text-muted-foreground">
            {progress.pct !== null ? `${Math.round(progress.pct * 100)}%` : ''}
            {progress.message ? ` — ${progress.message}` : ''}
          </span>
        )}
      </div>

      {segments.length > 0 && (
        <div className="border rounded p-4 space-y-2 max-h-96 overflow-y-auto">
          {segments.map((s, i) => (
            <div key={i} className="text-sm">
              <span className="font-mono text-muted-foreground">
                [{s.start.toFixed(1)}–{s.end.toFixed(1)}{s.speaker ? `, ${s.speaker}` : ''}]
              </span>{' '}
              {s.text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Run the full e2e smoke**

Manual procedure (record this in the verification doc):

1. `pnpm install` then `pnpm build` (verifies TS + Vite + Tauri build).
2. `pwsh python-sidecar/build_sidecar.ps1` (builds sidecar onedir, stages).
3. `pnpm tauri dev` — app window opens.
4. Verify: nav bar visible. Settings link routes correctly.
5. Manually edit `$env:APPDATA\audio-transcriber\config.json` and set `cloud_api_keys.AssemblyAI` to a real test key.
6. Restart app. FirstRunBanner should be gone.
7. Click "Запись" — grant mic permission. Speak ~10 seconds in Russian. Click "Стоп". Toast confirms file saved.
8. Click "Транскрибировать". Watch progress message update. Wait ~1-3 minutes.
9. Verify segments render in the panel. Speaker labels present if diarization succeeded.

- [ ] **Step 3: Run all tests**

```bash
# v0.1 baseline
pytest
# Sidecar tests
cd python-sidecar && pytest && cd ..
# Vitest
pnpm test:run
# Rust unit tests (sidecar.rs / commands.rs)
cd src-tauri && cargo test && cd ..
# Lints
ruff check python-sidecar/
pnpm lint
cd src-tauri && cargo clippy -- -D warnings && cd ..
```

All should pass.

- [ ] **Step 4: Write the Phase 1 verification doc**

Create `docs/superpowers/plans/2026-05-28-tauri-lite-rewrite-phase-1-foundation-verification.md`:

```markdown
# Tauri Lite-Rewrite Phase 1 — Verification Sign-Off

**Date completed:** YYYY-MM-DD
**Tester:** _____________
**OS:** Windows 10 Pro 19045  (macOS sign-off in Phase 3)

## Smoke checklist

- [ ] Monorepo dirs: `src/`, `src-tauri/`, `python-sidecar/` populated.
- [ ] `pnpm install` succeeds.
- [ ] `pnpm tsc --noEmit` clean.
- [ ] `pnpm lint` clean.
- [ ] `pnpm test:run` — N tests pass (record count).
- [ ] `pytest` (v0.1 baseline) — still 333 tests pass.
- [ ] `cd python-sidecar && pytest` — all sidecar tests pass.
- [ ] `ruff check python-sidecar/` clean.
- [ ] `cd src-tauri && cargo test` — all Rust tests pass.
- [ ] `cd src-tauri && cargo clippy -- -D warnings` clean.
- [ ] `pwsh python-sidecar/build_sidecar.ps1` produces a working bundle.
- [ ] Manual stdin ping to the staged sidecar returns `{pong: true, version: "0.2.0"}`.
- [ ] `pnpm tauri dev` launches the app window.
- [ ] First-run bootstrap creates `$APPDATA/audio-transcriber/config.json`.
- [ ] FirstRunBanner appears when AssemblyAI key empty.
- [ ] FirstRunBanner disappears when AssemblyAI key set + app restarted.
- [ ] Recorder grants mic permission and shows a moving level meter.
- [ ] "Стоп" saves a .webm to `$APPDATA/audio-transcriber/recordings/`.
- [ ] "Транскрибировать" with a valid AssemblyAI key returns segments.
- [ ] Progress updates render during transcription (pct + status message).
- [ ] Segments panel shows speaker-labeled lines.

## Re-scope decision (per spec §9.1)

If any of the above fails, re-scope:

- [ ] **Option A** chosen: defer macOS + Linux to v0.3; ship v0.2 Windows-only alpha.
- [ ] **Option B** chosen: extend Foundation by 1 week, push v0.2 alpha to week 9.
- [ ] No re-scope — proceed to Phase 2.

## Known issues / follow-ups

- _List any caveats the next-phase engineer should know._
```

- [ ] **Step 5: Commit + tag**

```bash
git add src/app/index.tsx docs/superpowers/plans/2026-05-28-tauri-lite-rewrite-phase-1-foundation-verification.md
git commit -m "feat(v0.2): wire Recorder + transcribe e2e on Home + Phase 1 verification doc"
git tag v0.2.0-phase1-foundation
```

- [ ] **Step 6: Push branch + open PR**

```bash
git push -u origin feat/tauri-lite-rewrite-phase-1-foundation
gh pr create --title "feat(v0.2): Tauri lite-rewrite Phase 1 — Foundation" --body "$(cat <<'EOF'
## Summary

Phase 1 of the Tauri lite-rewrite spec (`docs/superpowers/specs/2026-05-28-tauri-lite-rewrite-design.md`).

Outputs:
- `python-sidecar/` — Python JSON-RPC dispatcher with 12 handlers
- `src-tauri/` — Tauri 2 Rust core with sidecar bridge + first-run bootstrap
- `src/` — React 19 + TS scaffold with TanStack Router + Sonner + Recorder + FirstRunBanner

End-of-phase deliverable per spec §9.1: working end-to-end transcribe flow (record → sidecar → AssemblyAI → segments rendered).

## Test plan

- [ ] v0.1 baseline (`pytest`) — 333 tests green
- [ ] Sidecar tests (`cd python-sidecar && pytest`) — all green
- [ ] Vitest (`pnpm test:run`) — all green
- [ ] Rust tests (`cd src-tauri && cargo test`) — all green
- [ ] Lints clean: `ruff check python-sidecar/`, `pnpm lint`, `cargo clippy -- -D warnings`
- [ ] Manual e2e smoke per `2026-05-28-tauri-lite-rewrite-phase-1-foundation-verification.md`
EOF
)"
```

---

## Self-Review checklist (for the plan author)

After all tasks land:

1. **Spec coverage:** every Phase 1 deliverable from spec §9 row 3-4 is touched:
   - sidecar_main.py + JSON-RPC handlers → Tasks 3-11 ✓
   - pydantic-to-typescript + pre-commit → Tasks 12-13 ✓
   - Tauri Rust core (spawn/relay/event-emit) → Tasks 15-18 ✓
   - TanStack Router 5 routes → Task 22 ✓
   - Recording component (Web Audio + waveform) → Task 24 ✓
   - First-run banner → Task 25 ✓
2. **Open questions §12 resolved in header:** Q4 ✓, Q5 ✓, Q6 ✓. (Q1/Q2/Q3 belong to Phase 2.)
3. **No placeholders:** all 26 tasks have full code blocks + exact file paths.
4. **Type consistency:** `Segment`, `TranscribeResult`, `HistoryEntry`, `AppConfig` defined in `schemas.py` (Python) → mirrored in `python-types.d.ts` (codegen) → used in `useTranscribe.ts` + `Recorder.tsx`. Names match.
5. **TDD discipline:** Every Python handler task starts with a failing test (Step 1: write test, Step 2: run + expect fail, Step 3: implement, Step 4: run + expect pass, Step 5/6: commit). Rust + React tasks include manual smoke gates because full TDD for IPC + Web Audio is impractical in headless CI for Phase 1.

---

## Phase 1 task summary

| # | Task | Files NEW | Files MOD |
|---|---|---|---|
| 1 | Branch + monorepo dirs + .gitignore | 3 .gitkeep | .gitignore |
| 2 | Lift Python modules | python-sidecar/pyproject + pytest.ini | — |
| 3 | sidecar_main.py skeleton + ping | sidecar_main.py + 2 test files + conftest.py | — |
| 4 | _handle_transcribe + cancel | test_handle_transcribe.py | sidecar_main.py |
| 5 | _handle_extract_tasks | test_handle_extract_tasks.py | sidecar_main.py |
| 6 | _handle_generate_protocol | test_handle_generate_protocol.py | sidecar_main.py |
| 7 | _handle_send_tasks | test_handle_send_tasks.py | sidecar_main.py |
| 8 | _handle_gdrive_backup | test_handle_gdrive_backup.py | sidecar_main.py |
| 9 | _handle_list_history (NEW) | test_handle_list_history.py | sidecar_main.py |
| 10 | _handle_trim_audio (NEW) | test_handle_trim_audio.py | sidecar_main.py |
| 11 | load_config + save_config + shutdown | test_handle_config_io.py | sidecar_main.py |
| 12 | Pydantic schemas | schemas.py + test_schemas.py | requirements.txt |
| 13 | pydantic-to-typescript + pre-commit | gen_ts_types.py + .pre-commit-config.yaml | requirements.txt |
| 14 | PyInstaller sidecar spec | audio_transcriber_sidecar.spec + runtime_hook + build_sidecar.ps1 | — |
| 15 | Tauri Rust scaffold | Cargo.toml + tauri.conf.json + build.rs + 4 .rs stubs + icon | — |
| 16 | Sidecar spawn + lifecycle | — | sidecar.rs |
| 17 | invoke_python + cancel | — | commands.rs + sidecar.rs |
| 18 | First-run config bootstrap | config.example.json (copied) | bootstrap.rs + tauri.conf.json |
| 19 | save_recording command | — | commands.rs + Cargo.toml |
| 20 | Vite + React + TS scaffold | package.json + tsconfig + vite.config + biome + 3 src files | — |
| 21 | Tailwind v4 + shadcn/ui + Sonner | tailwind.config + postcss.config + components.json + ui/ primitives | styles.css + main.tsx |
| 22 | TanStack Router 5 shells | 6 app/ route files + router.ts | main.tsx + .gitignore |
| 23 | ipc.ts + Zustand + useTranscribe | ipc.ts + store.ts + useTranscribe.ts + 2 test files | main.tsx |
| 24 | Recorder.tsx | Recorder.tsx | app/index.tsx + package.json |
| 25 | FirstRunBanner + useConfig | FirstRunBanner.tsx + useConfig.ts + paths.ts | app/__root.tsx |
| 26 | E2E smoke + verification gate | verification.md | app/index.tsx |








