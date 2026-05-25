# CLAUDE.md — context for AI coding assistants

This file primes Claude (and other AI agents) on conventions and invariants
specific to this codebase. Written as a compact briefing, not as user docs —
for the latter see `README.md`.

## What this project is

Windows desktop GUI for offline audio transcription + speaker diarization.
Stack: CustomTkinter (UI) + faster-whisper/ctranslate2 (ASR) + pyannote.audio
(diarization) + a multi-provider cloud transcription path (AssemblyAI,
Deepgram, Gladia, OpenAI Whisper, Speechmatics — see `providers/base.py`
ABC for the extension point).

Target hardware: ASUS ROG Strix G15, GTX 1650 Ti (4 GB VRAM). VRAM is the
binding constraint — many architectural decisions exist solely because both
Whisper-large and pyannote can't be in VRAM at the same time on this card.

## Hard invariants — DO NOT BREAK

1. **Faulthandler must initialize before any C-extension import.** See
   `app.py:13-16`. ctranslate2/torch/pyannote can SIGSEGV during CUDA
   teardown; without the early `faulthandler.enable()`, the process
   vanishes silently.
2. **`ctranslate2` must be imported before `torch`** on Windows. See the
   comment at the top of `transcriber/__init__.py`. Wrong order ⇒
   `STATUS_DLL_INIT_FAILED` (Windows code 3221225794) on first run.
3. **Unload Whisper with `model.unload_model(to_cpu=True)`, never `del
   model`.** `del` triggers Fatal Python errors on Windows + GTX 1650 Ti
   during ctranslate2 teardown. See the long comment in
   `transcriber/__init__.py` around the unload site.
   *(After PR #4 the file is `transcriber/__init__.py` — F4 split
   moved the monolith into a package with `cuda_utils`, `progress`,
   `prompt`, `speaker_aligner` submodules. PR #9 added the
   `_DIARIZE_WORKER_PATH` constant to keep the subprocess path valid
   from inside the package.)*
4. **Disable cuDNN inside `diarize_worker.py`** before pyannote loads.
   On the 1650 Ti this prevents `HOST_ALLOCATION_FAILED` /
   `CUBLAS_STATUS_NOT_INITIALIZED`.
5. **The diarize subprocess uses a stdin GO protocol.** Parent writes
   `GO\n` to child stdin AFTER unloading Whisper. Child blocks reading
   stdin until then. This collapses the "70 % progress dead zone" where
   both processes were idle waiting for the other.
6. **Do not "liberalize" version pins in `requirements.txt`.** Every
   pin is load-bearing — speechbrain/lightning/pyannote/cuDNN
   workarounds depend on exact combinations. README explains why.
7. **Numpy audio → `model.transcribe()` MUST be 16 kHz mono.**
   faster-whisper assumes that rate unconditionally for ndarray input;
   passing a 44.1 / 48 kHz slice silently mangles text and timestamps
   (no error, just wrong output). The mixed-mode path in
   `transcriber/__init__.py` enforces this via `ensure_16khz_mono(wav_path)`
   called UPSTREAM of `load_model()` — running ffmpeg after `load_model`
   crashes Windows (consequence of invariant #2). When adding any new
   numpy-into-Whisper code path, gate it the same way.
8. **Numpy audio → `faster_whisper.vad.get_speech_timestamps` MUST be
   16 kHz mono.** Silero's neural model is 16-kHz-only and faster-whisper
   does NOT resample non-16k input — formants land at wrong frequencies
   and detection collapses. The `sampling_rate` kwarg only fixes
   ms→sample threshold arithmetic, not detection itself. Use
   `audio_io.resample_to_16khz_mono(samples, sample_rate)` (the numpy-in
   sibling of `ensure_16khz_mono`) — short-circuits when input is already
   16 kHz, ffmpeg pipe otherwise. `silence_remover.remove_silences`
   does this internally; future callers should follow the same pattern.

## Code conventions

- **Logging**: `from logging_setup import get_logger; logger =
  get_logger(__name__)` for main-process modules. `tasks/*` uses
  `logging.getLogger(__name__)` directly — both are fine.
  `diarize_worker.py` is a subprocess; it uses
  `print(..., file=sys.stderr, flush=True)` because the parent captures
  stderr — do not introduce logger.* calls there.
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
pytest                       # must show green; baseline = 461 tests
                             # (was 285 pre-code-switching; +30 from Phase 1
                             # cloud/UI tests, +4 segmenter, +15 mixed-mode,
                             # +8 from sampling-rate / VAD-resample fixes,
                             # +10 from GDrive auth (Phase 7.0 PR-A #40/#41),
                             # +4 from Settings-section smoke (Phase 7.0 PR-B #42),
                             # +11 from Drive client wrapper + root-parent fix
                             #   (Phase 7.1 PR-A #45 + #46),
                             # +10 from backup orchestrator (Phase 7.1 PR-B #47),
                             # +2 from backup-button smoke (Phase 7.1 PR-C #48),
                             # +1 from backup-failure state-sync regression
                             #   (Codex P2 fix #49),
                             # +27 from Groq STT provider with word-level
                             #   granularities (Phase 6.5 PR-A),
                             # +6 from transparent opus compression for
                             #   files > 25 MB (Phase 6.5 PR-A.1),
                             # +3 regression for word-interval check in
                             #   _to_segments (Codex P2 fix on PR #51),
                             # +4 max_upload_bytes ABC attribute +
                             #   18 cloud_chunker for 2-5h audio
                             #   (Phase 6.5 PR-C),
                             # +2 missing-file regression for
                             #   needs_chunking (Codex P2 fix on PR #54),
                             # +7 RNNoise denoise (lazy model download +
                             #   ensure_wav denoise param) — Phase 6.5 PR-E,
                             # +4 Windows ffmpeg filter-path escape
                             #   (Codex P1 fix on PR #56),
                             # +10 hybrid cloud-STT + local-pyannote
                             #   diarization (Phase 6.5 PR-B))
python -m ruff check .       # must be clean
```

CI (`.github/workflows/tests.yml`) runs both on every push. The `lint` job
is fast (~30 s); the `pytest` job is slow on cold install (~5 min) but
cached after first run (~1 min). Don't push expecting CI to catch your
local regressions — run both locally first.

`pytest.ini` is configured (`testpaths = tests`). `pyproject.toml` holds the
ruff config (line-length=100, target=py310, rules E/W/F/I/B/UP).

## Where things live

| Concern | Module |
|---|---|
| Entry point + faulthandler bootstrap | `app.py` |
| Main window + transcription run loop | `ui/app/` package — `__init__.py` (App-class shell, ~130 LOC) + 5 mixins (`recorder_mixin`, `save_mixin`, `settings_mixin`, `dialogs_mixin`, `transcription_mixin`) + `builder.py` (widget tree as a `build_ui(app)` free function) + `constants.py` + `main_entry.py` — split via F4-PR-2 series, PRs #12/#14–#18 |
| All dialogs | `ui/dialogs/` (`extract_tasks/` package + `settings.py`, `history.py`, `voices.py`, `terms.py`, `system_monitor.py`) |
| Whisper transcription | `transcriber/` package (`__init__.py` + `cuda_utils`, `progress`, `prompt`, `speaker_aligner`, `cloud_chunker` — last added Phase 6.5 PR-C) |
| Diarization subprocess | `diarize_worker.py` |
| Audio recording | `recorder.py` |
| Cloud provider ABC + registry | `providers/base.py` + `providers/__init__.py` |
| Cloud transcription providers | `providers/{assemblyai,deepgram,gladia,groq,openai_whisper,speechmatics}.py` |
| Task extraction (LLM → Linear/Glide) | `tasks/` (`extractor`, `sender`, `schema`, `persistence`, `linear_client`, `glide_client`, `openrouter_client`, `errors`) + `tasks/backends/` (Protocol-based dispatch — `base.py`, `linear.py`, `glide.py`) |
| Voice library (speaker enrollment) | `voice_library.py` + `enrollment_worker.py` |
| Audio editor | `audio_cutter.py` |
| Silence removal | `silence_remover.py` |
| Logging setup | `logging_setup.py` |
| Persistent settings | `config.json` (template: `config.example.json`); helper: `utils.save_config` |
| Google Drive auth (Phase 7.0) | `gdrive/auth.py` (`GDriveAuth` — OAuth desktop loopback via `InstalledAppFlow`; tokens at `~/.audio-transcriber/gdrive-token.json`) |
| Google Drive API wrapper (Phase 7.1) | `gdrive/client.py` (`DriveClient` — thin wrapper over `googleapiclient.discovery.build`; find/create folder + upload file) |
| Google Drive backup orchestrator (Phase 7.1) | `gdrive/backup.py` (`run_backup` — composes `redact_config` + `zip_history` + `build_manifest` + `DriveClient`) |

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

## Active work / context

- **Phase 6.5 — Groq STT + hybrid local diarize** (May 2026, in flight):
  user wants KZ+RU+EN code-switching transcription via cloud API while
  keeping diarization on the local GTX 1650 Ti (pyannote). PR-A (this PR)
  adds `providers/groq.py` with `whisper-large-v3` default — Groq is
  ~60× cheaper than OpenRouter for the same model ($0.111/h full,
  $0.04/h turbo) and exposes an OpenAI-compatible
  `/openai/v1/audio/transcriptions` endpoint. Provider requests BOTH
  `timestamp_granularities[]=segment` and `=word` and the new
  `_to_segments()` distributes top-level `words[]` to their owning
  segment by midpoint time-overlap — this preps the data shape for
  PR-B's hybrid orchestrator. PR-B (next) wires
  `_transcribe_via_cloud_with_local_diarize` in `transcriber/__init__.py`
  to spawn pyannote in parallel with the Groq upload, then merge via the
  existing `speaker_aligner._assign_speakers_word_level`. PR-C (deferred):
  Settings UI model picker. Spec/plan at
  `~/.claude/plans/glittery-foraging-scott.md` (user-local).
- **Codebase review** (May 2026): F1–F8 archived in
  `~/.claude/plans/codebase-review-keen-thompson.md` (user-local).
  Shipped: F1/F3/F5/F6 (PR #1), F3-B Tk-cleanup narrowing (PR #2),
  **F4-PR-1** transcriber split (PR #4), **F4-PR-3** extract_tasks split
  (PR #5), **F7** ARCHITECTURE.md (PR #6/#7), worker-path follow-up
  (PR #9), **F4-PR-2** ui/app split into a 5-mixin package
  (PRs #12/#14–#18, May 2026 — `__init__.py` 1278 → 133 LOC). The
  full codebase-review punchlist is now closed.
- **Phase 6.4** (May 2026, PR #10): added 4 cloud transcription
  providers (Deepgram, Gladia, Speechmatics, OpenAI Whisper) and the
  Glide LLM backend via the new `tasks/backends/` Protocol layer. UI
  got per-backend Settings checkboxes, Extract dialog backend selector,
  real-cost display via `_format_real_cost`, humanized errors via
  `tasks/errors.humanize()`, and Phase 6.5 keyboard shortcuts
  (Ctrl+N, Ctrl+Shift+E, Ctrl+Shift+S, F5, Esc) in the extract dialog.
- **Phase 7.1** (May 2026, shipped): Google Drive manual backup.
  Shipped via PR-A `gdrive/client.py` Drive API v3 wrapper (#45 +
  #46 Codex P2 fix for root-parent folder filter), PR-B
  `gdrive/backup.py` orchestrator (#47), PR-C Settings UI button +
  config key (this PR). New "Сделать backup сейчас" button under
  the Google Drive section of Settings; click triggers a worker
  thread that ensures auth is valid, zips `history/` (excluding
  `*.wav/*.mp3/*.m4a` per text-only scope), redacts API keys
  from `config.json`, builds a SHA-256 + size manifest, and
  uploads all three files to `audio-transcriber-backup/<ISO-ts>/`
  on Drive. New config keys: `gdrive_root_folder_id` (cached
  after first backup to skip find_or_create round-trip),
  `gdrive_last_backup` (ISO snapshot name; Phase 7.3 scheduler
  reads it). Spec at
  `docs/superpowers/specs/2026-04-30-gdrive-backup-design.md`,
  plan at `docs/superpowers/plans/2026-05-23-gdrive-phase-7.1-backup.md`.
  `DriveClient` imported lazily via a sentinel-pattern inside
  `gdrive/backup.py` so `from gdrive.backup import run_backup`
  stays cheap AND `patch("gdrive.backup.DriveClient", ...)` works
  cleanly in tests. Phase 7.2 (restore), 7.3 (auto-schedule),
  7.4 (audio opt-in) remain unstarted.
- **Phase 7.0** (May 2026, shipped): Google Drive auth + Settings UI.
  Shipped via PR #40 (PR-A `gdrive/auth.py` foundation) + #41 (Codex
  P2 fix — JSON decode in userinfo lookup) + #42 (PR-B Settings UI
  integration). New `gdrive/` package with `auth.py::GDriveAuth`
  wrapping `google_auth_oauthlib.flow.InstalledAppFlow` for the
  desktop OAuth loopback dance; tokens cached at
  `~/.audio-transcriber/gdrive-token.json` (outside `config.json`
  because the latter gets backed up to Drive — chicken/egg). Scope
  is `drive.file` (non-sensitive — no Google manual verification
  needed). Settings dialog gained a "Google Drive" section (row=9)
  with status badge + Войти/Выйти buttons; sign-in runs in a daemon
  thread to keep the UI responsive while the browser blocks. Spec at
  `docs/superpowers/specs/2026-04-30-gdrive-backup-design.md`, plan
  at `docs/superpowers/plans/2026-05-23-gdrive-phase-7.0-auth.md`.
  Note: `CLIENT_ID` / `CLIENT_SECRET` constants in `gdrive/auth.py`
  are placeholder strings — manual smoke + real-GCP wire-up deferred
  to a tiny follow-up commit once the GCP project Pre-flight is done
  (B.5 in the plan). Phase 7.1+ (backup, restore, scheduler, audio
  opt-in) remain unstarted.
- **Code-switching KZ+RU+EN Phase 1** (May 2026): shipped via 4 PRs
  (#21 PR-A foundation, #22 PR-B Gladia + Deepgram + capability gate,
  #23 PR-C AssemblyAI + Speechmatics + OpenAI Whisper, #24 PR-D Settings
  UI warning) plus a #25 hotfix for Settings-dialog Var trace lifecycle.
  Spec + plan at `docs/superpowers/specs/2026-05-21-code-switching-kz-ru-en-design.md`
  and `docs/superpowers/plans/2026-05-21-code-switching-kz-ru-en-phase-1.md`.
  Adds a `"Смешанный (KZ+RU+EN)"` → `"mixed"` sentinel: local Whisper
  gets a trilingual `initial_prompt` (frame in `transcriber/prompt.py`)
  and `language=None` (via `_effective_whisper_language()`); cloud
  providers branch on `options.language == "mixed"` to emit their native
  multilingual config — Gladia `code_switching: true`, AssemblyAI
  `speech_model: universal`, Speechmatics `language_identification_config`,
  OpenAI Whisper omits the language form field. Deepgram opts out
  (`supports_mixed = False`) because nova-3 lacks Kazakh — runtime guard
  in `Transcriber.transcribe()` raises a Russian `ProviderError` for
  any provider whose class attribute `supports_mixed = False`.
- **Code-switching KZ+RU+EN Phase 2** (May 2026, closed): local-Whisper
  per-segment language detection — true code-switching, not just the
  prompt-only Phase 1 band-aid. Shipped via PR #28 (PR-A segmenter
  foundation), PR #29 (PR-B integration: `_decode_chunk_single` +
  `_decode_chunk_mixed` + `language` field round-trip through
  `speaker_aligner`), then 3 sequential hotfixes (#30 sampling-rate
  bug, #31 ordering invariant, #32 cleanup-scope leak) — each Codex-
  caught post-merge, each fixing one dimension while breaking another.
  Two follow-up VAD fixes then hardened the invariant-#8 surface
  (#34 forwards `sampling_rate` to `get_speech_timestamps`, #35
  resamples non-16k input in `silence_remover`; #36 docs-only closed
  the latent-bug note and codified invariant #8). Lessons captured at
  [feedback_relocating_code_audit_all_invariants.md](memory).
  Spec + plan at `docs/superpowers/specs/2026-05-22-code-switching-kz-ru-en-phase-2-design.md`
  and `docs/superpowers/plans/2026-05-22-code-switching-kz-ru-en-phase-2.md`.
  Architecture: `transcriber/segmenter.py::vad_split()` wraps
  `faster_whisper.vad.get_speech_timestamps` with language-detection-
  tuned params (500 ms min speech / silence); `_decode_chunk_mixed`
  loads the chunk via `load_mono_float32`, VAD-splits it, and runs
  `model.transcribe(seg_audio, language=None, vad_filter=False, ...)`
  per region — Whisper's `detect_language` fires per slice. Output
  dicts carry a new `language` field (preserved through both
  no-diarize projection AND `_assign_speakers_word_level` /
  `_flush_word_group` paths). The PR-C formal A/B QA pass was
  deferred — quality signal came instead from the hotfix cycle on
  real workloads. If regression suspected later, Phase 1 baseline
  commit = `79071ff` (last commit before PR-A).

## Don't

- Don't bump `requirements.txt` versions casually (see invariant 6).
- Don't add `print()` for diagnostics in main-process code — use the
  logger. (`diarize_worker.py` is the documented exception.)
- Don't broaden `except` classes back to `except Exception` without a
  comment justifying it. The codebase deliberately narrowed these.
- Don't commit `config.json`, `logs/`, or anything in `.cache/` — see
  `.gitignore`.
- Don't introduce `mypy` config without checking with the user — F6 of
  the review chose to defer mypy to keep ruff alone for now.
