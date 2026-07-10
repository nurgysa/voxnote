# CLAUDE.md — context for AI coding assistants

This file primes Claude (and other AI agents) on conventions and invariants
specific to this codebase. Written as a compact briefing, not as user docs —
for the latter see `README.md`.

## What this project is

Windows desktop GUI for cloud-API audio transcription + speaker diarization.
Stack: CustomTkinter (UI) + cloud STT providers (AssemblyAI, Deepgram,
Gladia, Speechmatics with diarization; Groq ASR-only — see `providers/base.py`
ABC for the extension point) + OpenRouter for task extraction and protocol
generation.

Cloud-only since the 2026-05-28 rip-out. The local CUDA / Whisper / pyannote
code is gone — both from the codebase and from `requirements.txt`. No GPU
needed; transcription is HTTPS calls.

Open source (MIT) and public since 2026-06-10. End users get the
PyInstaller zip from GitHub Releases; support is GitHub Issues. The user
setup guide is `docs/CLIENT_SETUP.md` (English).

Earlier history (pre-2026-05-28): targeted ASUS ROG Strix G15, GTX 1650 Ti
(4 GB VRAM), faster-whisper + pyannote locally. Many architectural ghosts
from that era (`_DIARIZE_WORKER_PATH`, `cuda_utils`, the 25-min STFT chunker
threshold) are gone; only what cloud paths actively use remains.

## Hard invariants — DO NOT BREAK

1. **Faulthandler must initialize before any C-extension import.** See
   the guarded block at the top of `app.py` (dev/source mode) and its
   frozen twin `runtime_hook_imports.py` (which also redirects the None
   stdio streams under PyInstaller windowed mode). Native deps
   (soundfile, sounddevice) can SIGSEGV during shutdown; without the
   early `faulthandler.enable()`, the process vanishes silently. The old
   CUDA-teardown concern that motivated this in the GPU-era codebase is
   gone, but the invariant is cheap and still buys diagnostic value.
2. **No local CUDA / pyannote / faster-whisper / ctranslate2 / torch
   code may be reintroduced.** The codebase has been cloud-only since
   the 2026-05-28 rip-out. Adding any of those imports anywhere —
   `transcriber/`, `tasks/`, `ui/`, `providers/`, tests — is a regression.
   If a feature truly needs local inference, open a discussion before
   coding. The rationale is documented in
   `docs/superpowers/plans/2026-05-28-cloud-only-mvp-v5.md`.
3. **Do not "liberalize" version pins in `requirements.txt`.** Even
   after the rip-out trimmed the heavy stack, the remaining pins
   (CustomTkinter / soundfile / sounddevice versions)
   are load-bearing on Windows. Bumping them needs explicit smoke
   testing on a clean Win10 + Win11 VM.

(Old invariants #3 / #4 / #5 / #7 / #8 — unload_model, cuDNN, GO protocol,
16-kHz-mono-to-Whisper, 16-kHz-mono-to-Silero-VAD — are obsolete; their
code paths were deleted in the 2026-05-28 rip-out. The git history
preserves the rationale if anyone needs it.)

## Code conventions

- **Logging**: `from logging_setup import get_logger; logger =
  get_logger(__name__)` for main-process modules. `tasks/*` uses
  `logging.getLogger(__name__)` directly — both are fine.
- **Exceptions**: prefer narrow `except` classes over `except Exception`.
  The codebase uses `tk.TclError` for widget-cleanup paths,
  `OSError` for file I/O / socket cleanup,
  `requests.RequestException` for HTTP, custom `ProviderError` /
  `LinearError` / `PersistenceError` for module-level failures.
  When you must swallow, add a one-line comment explaining why
  (see `transcriber/__init__.py` for the gold-standard pattern).
- **Type hints**: used heavily in `tasks/`, `providers/`, and module-level
  helpers. Apply them to new code; don't bother retro-fitting unless you're
  already touching the file.
- **Russian UI strings, English code comments** — established convention.
  User-facing dialog text and error messages are in Russian; code,
  docstrings, commit messages, and PR descriptions are in English (this
  one's an exception — written for AI agent contributors).

## Test + lint contract

Before any commit:

```bash
pytest                       # must show green; baseline ≈ 1161 tests (regenerate: pytest --collect-only -q)
python -m ruff check .       # must be clean
```

CI (`.github/workflows/tests.yml`) runs both on every push and PR, on a
[ubuntu-latest, windows-latest] matrix. The `lint` job is fast (~30 s);
each `pytest` leg is slow on cold install (~5 min) but cached after first
run (~1-3 min). Don't push expecting CI to catch your local regressions —
run both locally first. The windows leg exists because stock Windows
defaults `open()`/`write_text()` to cp1252 — always pass
`encoding="utf-8"`; Linux CI and UTF-8-mode dev machines both mask that
bug class.

Windows shell gotchas (PowerShell 5.1): piping or `>`-redirecting pytest
output can swallow the final summary line — read the dot-lines or use
`--junitxml`; and args containing embedded `"` get mangled when passed to
native exes — pass long content via files (`git commit -F msg.txt`,
`gh pr create --body-file body.md`).

`pytest.ini` is configured (`testpaths = tests`). `pyproject.toml` holds the
ruff config (line-length=100, target=py310 lint-floor, rules E/W/F/I/B/UP).

## Where things live

| Concern | Module |
|---|---|
| Entry point + faulthandler bootstrap | `app.py` |
| Main window + queue-first intake | `ui/app/` package — `__init__.py` (App-class shell; builds and starts the `ProcessingQueue` + inbox poll) + 5 mixins (`recorder_mixin`, `save_mixin`, `settings_mixin`, `dialogs_mixin`, `queue_mixin`) + `builder.py` (widget tree as a `build_ui(app)` free function) + `constants.py` + `main_entry.py`. `queue_mixin` replaced the old synchronous `transcription_mixin` in queue PR-C1: record-stop and file-pick now enqueue onto the serial queue. |
| All dialogs | `ui/dialogs/` — `extract_tasks/` package (dialog-class shell + `builder.py` widget tree + `constants.py`/`pricing.py`/`task_row.py`/`cache_helpers.py`) + `settings.py` (class shell) + `settings_builder.py` (free-function sections) + `settings_helpers.py` + `meetings.py`/`meetings_view.py` (meetings history, Hermes protocol/tasks badges, pending-voices bind button) + `voice_bind.py` + `directory.py` + `migration.py` + `terms.py`. Both dialog god-objects were tree-split into builder modules (PRs #134–#136), mirroring `ui/app/builder.py`. |
| Cloud transcription dispatcher | `transcriber/` package — `__init__.py` (cloud-only `Transcriber` class + `TranscriptionCancelled` + `_check_cancelled`; ~240 LOC). Providers upload files whole — `cloud_chunker` was deleted as unreachable in #103, and the old `cuda_utils` / `prompt` / `progress` / `segmenter` / `speaker_aligner` submodules died in the 2026-05-28 rip-out. |
| Audio recording | `recorder.py` |
| Free-tier upload-cap preparation | `audio_upload_prep.py` — ffmpeg-only compress (single speech-optimized mono re-encode) or chunk-and-merge (sequential trims, recombined with cumulative time offsets) so an upload never exceeds a provider's `TranscriptionProvider.max_upload_bytes`. Provider-agnostic; only `providers/groq.py` sets the flag today. |
| Cloud provider ABC + registry | `providers/base.py` + `providers/__init__.py` |
| Cloud transcription providers | `providers/{assemblyai,deepgram,gladia,groq,speechmatics}.py` — Groq is ASR-only (no native speaker-label contract) and must stay out of diarized meeting mode; OpenAI Whisper remains deleted from the 2026-05-28 rip-out because it depended on the now-gone hybrid-with-local-pyannote path. Shared transport plumbing (HTTP error idiom, PollSpec poll loop, file streaming, validate/cancel helpers) lives in providers/_common.py — tests patch HTTP at providers._common.requests (one canonical target); tests/test_provider_transport_guard.py blocks regrowth. Groq additionally sets `max_upload_bytes` (25 MiB free-tier cap) — `transcriber/__init__.py`'s `_run_cloud_stt` compresses/chunks a temporary derivative via `audio_upload_prep.py` when the upload exceeds it; the original file and its archival/SHA-256 provenance are never touched. |
| Task extraction (LLM → Linear/Trello/Glide) | `tasks/` (`extractor`, `sender`, `schema`, `persistence`, `linear_client`, `trello_client`, `glide_client`, `openrouter_client`, `dedup`, `protocol_generator`, `errors`) + `tasks/backends/` (Protocol-based dispatch — `base.py`, `linear.py`, `trello.py`, `glide.py`) |
| People/projects directory (Phase A) | `directory/` (`schema`, `store` — atomic JSON at `~/.voxnote/directory.json`, `context` — prompt-context renderer). Grounds protocol + task prompts with real names/roles/project descriptions. Per-run speaker timestamps persisted via `utils.save_segments` → `<meeting>/segments.json`. |
| Reference-document grounding (markitdown) | `tasks/doc_context.py` (`convert_documents` + `combine_context`) — converts user-attached PDF/DOCX/PPTX/XLSX to Markdown via Microsoft markitdown (document extras ONLY; never `[audio-transcription]` — invariant #2) and folds them into the same `context=` slot the directory grounding feeds. Wired into the Extract dialog's `_run_extraction`; `MarkItDown` is sentinel-lazy-loaded for testability. |
| Audio editor | `audio_cutter.py` (silence-removal button removed in the 2026-05-28 rip-out; manual trim + preview + export retained) |
| Logging setup | `logging_setup.py` |
| Persistent settings | dev: repo-root `config.json`; frozen: `~/.voxnote/config.json` (survives app updates — PR #92). Template: `config.example.json`. Helpers: `utils.load_config` (corrupt-JSON quarantine) + `utils.save_config` (atomic write; owner-only ACL on the secret-store dir when frozen) |
| Shared audio I/O (ffmpeg) | `audio_io.py` (`ensure_wav`, `load_mono_float32`, `ffmpeg_trim`, `get_duration_s` — torch-free ffmpeg helpers shared by `transcriber`, `recorder`, `audio_cutter`) |
| Headless CLI + MCP server | `cli/` (`core` — pipeline glue reused by both surfaces; `app` — argparse CLI; `mcp_server` — MCP stdio server for agent CLIs, see `AGENTS.md`) |
| Meetings-by-project + processing queue | `processing/` (`model`, `store`, `layout`, `worker`, `preflight`, `sources`, `vault_note`, `inbox_watcher`, `voiceid`) — meetings organized by project on disk + the serial transcribe-only queue worker over `cli.core`. Fully wired into the UI via `ui/app/queue_mixin.py` (queue PR-C1/C2/C3: enqueue-first intake, Meetings view, 10 s Drive-inbox poll with stable-size debounce). Preflight guards: 2 GB size cap, Gladia 135-min gate, denoise auto-off >45 min, at-enqueue cost hint. |
| Long-meeting downstream processor | `tasks/long_meeting.py` (chunk → per-chunk fact extraction → synthesis → protocol/tasks drafts via OpenRouter; approval-safe, never mutates `transcript.md`) + CLI `python -m cli process-meeting` (dry-run by default, `--write` for local drafts). Headless Hermes/operator surface — deliberately NOT called by the desktop UI or the queue. |
| Hermes outbound integration | `integrations/hermes/` (`schema.py` — `audio.transcribed` v1.1 payload builder; `client.py` — HMAC-SHA256-signed best-effort POST, never raises; `synthetic_smoke.py`) + bundled skill `integrations/hermes/skills/voxnote/`. Fired by `processing/worker.py` after transcript write when enabled; Settings holds URL + secret. |
| Diagnostics log bundle | `support_bundle.py` (`build_log_bundle` — zips `logs/` + key-redacted config; wired to the Settings diagnostics log-bundle action) |
| Build + release packaging | `scripts/build_exe.ps1` (PyInstaller onedir + size guard) → `scripts/package_release.py` (zips via Python `zipfile` with forward-slash arcnames; guards abort on: secrets/state in bundle, missing markitdown, missing ffmpeg GPL license, scipy / pandas-tests bloat, backslash entries) |

## Branch + PR workflow

- Feature work goes on a topic branch (`feat/...`, `fix/...`,
  `refactor/...`, `docs/...`). Don't push directly to main.
- One concern per PR. The codebase-review work was split into PR #1
  (CI/ruff/diagnostics/tests) and PR #2 (Tk-cleanup narrowing) for
  reviewability, even though both touched many files.
- Commit messages: lowercase scoped (`feat(extract):`, `fix(sender):`,
  `refactor(transcriber):`, `chore(lint):`, `docs:`, `test:`, `ci:`).
  Russian commit body is fine when the change is Russian-domain (e.g. UI
  text); English otherwise.
- Pre-merge: PR description must include a Test plan checklist. See
  `.github/PULL_REQUEST_TEMPLATE.md` if it exists, otherwise the pattern
  is `## Summary` + `## Test plan` (markdown checkboxes).

## Current status & queued work

Snapshot as of 2026-07-10. This section is deliberately a snapshot, not a
chronicle — the phase-by-phase history lives in the dated specs/plans
under `docs/superpowers/` and in git history.

- **v0.2.0 is the latest public release** (2026-06-14 — the VoxNote
  rebrand commit, PR #150): repo is open source
  (MIT), the bundle ships as a GitHub Release asset, support is GitHub
  Issues. Release flow: `scripts/build_exe.ps1` →
  `scripts/package_release.py` (see the packaging row above for the
  guards it enforces). Main carries ~32 unreleased commits since v0.2.0
  (queue UI, Voice-ID Phase B, Hermes wiring, long-meeting processor,
  ASR-only/Groq) — a v0.3.0 is due once the Wave 5 evaluation verdict
  lands. Git-history note: the mid-June work (PRs ~#148–#166) landed
  bundled inside the #167 squash (2026-06-23), so `git log --since`
  shows a gap there; tags need `git fetch --tags`.
- **Audit remediation complete** (2026-06-04 → 06-09, PRs #100–#122):
  secret redaction in Drive backups, docs truth pass, dead-code removal
  (incl. `cloud_chunker`), CI safety net (py3.12 + ffmpeg + coverage +
  windows-latest leg), correctness fixes (config corrupt-JSON quarantine
  + atomic save, provider poll-loop JSON guards, Tk callback-exception
  logging, diagnostics log-bundle button), security hardening (CLI/MCP
  path confinement, markitdown size cap, owner-only ACL on
  `~/.voxnote`), PyInstaller de-bloat (568 → 355 MB) with
  packaging guards. Roadmap spec:
  `docs/superpowers/specs/2026-06-04-audit-remediation-design.md`.
- **Improvement audit complete** (2026-06-10 → 06-13, PRs #130–#147):
  post-open-source hardening pass — requests CVE bump + community files
  (#130/#131), extract close-data-loss guard + STT-key check button
  (#132/#133), the widget-tree split of both dialog god-objects into
  builder modules (#134–#136), provider transport dedup into
  `providers/_common.py` (#137/#138), scipy ghost-pin removal + bare-
  `except` ratchet guard (#139/#140), UX polish (terminology -> Meetings,
  async Settings stats, dedup checkbox, per-model cost forecast —
  #141–#144), and the outbound Hermes `audio.transcribed` webhook +
  Settings toggle (#146/#147).
- **Mixed-language is live behavior** (not history): Settings'
  mixed-language option maps to the `"mixed"` language sentinel and
  cloud providers branch on `options.language == "mixed"` — Gladia
  `code_switching: true`, AssemblyAI `speech_model: universal`,
  Speechmatics `language_identification_config`, and Groq omits the literal
  `mixed` sentinel while steering auto-detect with a KZ/RU/EN prompt. Deepgram
  opts out via the class attribute `supports_mixed = False` (nova-3 lacks
  Kazakh); `Transcriber.transcribe()` raises a provider error for any
  provider with that flag false. AssemblyAI sends the plural
  `speech_models: ["universal-2"]` field (singular `speech_model` is
  deprecated upstream).
- **Google Drive removed** (2026-06-23): the `gdrive/` package (auth,
  client, backup) was deleted; backup/restore now lives in Hermes Desktop.
- **Transcription queue shipped end-to-end** (queue PR-A/B/C series,
  landed via the #167 squash on 2026-06-23): queue-first intake replaced
  the synchronous run loop, Meetings view with Hermes badges, Google
  Drive phone-inbox watcher, preflight guards, source archiving to Drive
  `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/`, atomic `transcript.md`
  vault writer. Design: `docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md`.
- **Voice-ID Phase B merged** (PR-3/4/5, 2026-06-23 → 07-03, #167/#169/#170):
  Speechmatics speaker-ID sidecar → pending voices → bind/enroll UI
  (`ui/dialogs/voice_bind.py`) → transcript re-render. Remaining:
  real-audio quality validation + UX polish. Keep invariant #2: no
  torch / pyannote / local inference.
- **Mini-AGI V1 spec suite + long-meeting processor v0** (2026-07-02 →
  07-09): BRD/PRD/requirements/design/tasks under
  `docs/specs/voxnote-v1-mini-agi-integration/` (VoxNote = intake +
  transcription; Hermes owns protocol/tasks/approval downstream);
  headless `tasks/long_meeting.py` + `process-meeting` CLI;
  `docs/HERMES_MINI_AGI_INTEGRATION.md` activation guide. The decisive
  gate — the Wave 5 long-meeting evaluation on real 60–180 min audio —
  has NOT been run yet; those docs prescribe no new feature work until
  its verdict is recorded.
- **STT provider decision recorded** (2026-07-09,
  `docs/STT_PROVIDER_DECISION.md`): AssemblyAI default (Universal-2
  fallback for Kazakh), Gladia KZ-capable fallback (chunking >135 min),
  Deepgram/Speechmatics non-primary. Landed the same day:
  provider-specific API keys, ASR-only `transcription_mode` + Groq
  provider, Gladia duration guard, transcript provenance metadata.
- **Queued / deferred:**
  - **Wave 2–5 validation track**
    (`docs/specs/voxnote-v1-mini-agi-integration/tasks.md`): activate
    VoxNote in the live Hermes profile (skill + MCP + webhook +
    draft-only route), synthetic smoke, short real-audio smoke, then the
    60–180 min long-meeting evaluation with a recorded
    pass/partial/fail verdict. This gates new feature work.
  - STT track leftovers (`docs/STT_PROVIDER_DECISION.md` §Next
    implementation tasks): #2 AssemblyAI model routing
    (Universal-3.5-Pro routing / Settings opt-in not built), #6 A/B
    fixture plan (needs real sanitized recordings), #8 operator docs.
  - v0.3.0 release packaging (needs explicit approval).
  - UX/UI visual polish (user feedback 2026-06-14: the current
    CustomTkinter UI "looks rough / not pretty"). The Dev-OS spec's
    proposed pilot feature "Intake Cockpit — Main Screen First Slice"
    (`docs/superpowers/specs/2026-07-02-voxnote-mini-agi-development-os.md`)
    is the likely vehicle; schedule after the Wave 5 verdict.

## Don't

- Don't bump `requirements.txt` versions casually (see invariant 3).
- Don't add `print()` for diagnostics in main-process code — use the
  logger. (CLI surfaces in `cli/` and `scripts/` print by design —
  that's their stdout contract, not diagnostics.)
- Don't broaden `except` classes back to `except Exception` without a
  comment justifying it. The codebase deliberately narrowed these.
- Don't commit `config.json`, `logs/`, or anything in `.cache/` — see
  `.gitignore`.
- Don't introduce `mypy` config without checking with the user — F6 of
  the review chose to defer mypy to keep ruff alone for now.
