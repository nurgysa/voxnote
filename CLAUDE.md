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
pytest                       # must show green; baseline = 285 tests
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
| Whisper transcription | `transcriber/` package (`__init__.py` + `cuda_utils`, `progress`, `prompt`, `speaker_aligner` — split via PR #4) |
| Diarization subprocess | `diarize_worker.py` |
| Audio recording | `recorder.py` |
| Cloud provider ABC + registry | `providers/base.py` + `providers/__init__.py` |
| Cloud transcription providers | `providers/{assemblyai,deepgram,gladia,openai_whisper,speechmatics}.py` |
| Task extraction (LLM → Linear/Glide) | `tasks/` (`extractor`, `sender`, `schema`, `persistence`, `linear_client`, `glide_client`, `openrouter_client`, `errors`) + `tasks/backends/` (Protocol-based dispatch — `base.py`, `linear.py`, `glide.py`) |
| Voice library (speaker enrollment) | `voice_library.py` + `enrollment_worker.py` |
| Audio editor | `audio_cutter.py` |
| Silence removal | `silence_remover.py` |
| Logging setup | `logging_setup.py` |
| Persistent settings | `config.json` (template: `config.example.json`); helper: `utils.save_config` |

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
- **Phase 7** (design spec only, commit `bbfa10f`): Google Drive
  backup + sync, brief at `docs/superpowers/specs/2026-04-30-gdrive-backup-design.md`.
  Implementation not started — backup-first (one-way upload + manual
  restore), text-only scope (~100 KB per snapshot, no audio).

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
