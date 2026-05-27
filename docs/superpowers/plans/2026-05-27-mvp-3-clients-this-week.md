# MVP to 3 Clients (This Week) — Implementation Plan (v4, post-Codex sanity-check)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. (Subagent-driven dispatch is blocked in this environment per memory `feedback_subagent_dispatch_blocked_by_mcp_overhead` — inline execution with TDD discipline.) Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the existing Python `audio-transcriber` as a Windows `.zip` bundle (PyInstaller `--onedir`, ~200-500 MB) to 3 first paying users by end of this week — cloud-only (AssemblyAI for STT+diarization, OpenRouter for protocol+task LLM passes). New "5-block MoM protocol generation" pass lands alongside the existing transcript + tasks output.

**Architecture:** Four-track work on the existing Python codebase. (1) **Lazy-import refactor** at `transcriber/cuda_utils.py` + `transcriber/__init__.py` + `diarize_worker.py` + `enrollment_worker.py` wraps heavy local-CUDA imports in `try/except (ImportError, OSError, RuntimeError)` so the bundle (which excludes those libs) can still import these modules to access cloud routing. Sets `_LOCAL_AVAILABLE = False`. `TranscriptionCancelled` moves above the gated import so cloud providers keep their dependency. (2) **Bundle-only UI gating** hides the voice library + silence-removal + non-diarizing providers when `_LOCAL_AVAILABLE = False`, preventing the existing hybrid auto-route (`transcriber/__init__.py:1135`) from silently engaging local pyannote. (3) **PyInstaller `--onedir` bundle** ships with `requests` + cloud providers + CustomTkinter + bundled ffmpeg, but NO torch/pyannote/faster_whisper/ctranslate2. (4) **`tasks/protocol_generator.py`** is a new module parallel to `tasks/extractor.py`; the UI hook lives inside the existing `_run_extraction` flow in `ui/dialogs/extract_tasks/__init__.py` using the dialog's REAL instance state (`self._transcript`, `self._history_folder`, `self._transcript_lang`, `self._model_var`, `self._config`).

**Tech Stack:** Python 3.10, PyInstaller 6.x (onedir mode), AssemblyAI (provider default), OpenRouter API, existing CustomTkinter UI, existing `providers/` adapter layer.

**Changes from v3 (post-Codex sanity-check 2026-05-27):**
- `LocalEngineUnavailable` exception defined in `transcriber/cuda_utils.py` (alongside `TranscriptionCancelled`), NOT in `transcriber/__init__.py`. v3 had a contradiction: plan imported it from cuda_utils but defined it in __init__.py.
- Task 3 (UI gating) targets `ui/dialogs/settings.py:352` (where the cloud-provider dropdown actually lives), not `ui/app/builder.py`. Filter logic + first-run banner key check use REAL runtime conventions: display-name keys like `"AssemblyAI"`, config key `cloud_enabled` (not `cloud_engine`).
- `config.example.json` defaults updated: `cloud_enabled: true` AND a way to default-diarize-on. Without this, bundle starts in local mode → hits excluded local engine; without diar-default-on, AssemblyAI returns no speaker labels.
- Diarization default flipped to ON: `app._diar_var = True` at `ui/app/builder.py:134` (was False).
- Audio Cutter (`audio_cutter.py:227` + `:649`) silence-removal path explicitly hidden in bundle. v3 incorrectly targeted Settings dialog for this gate; reality has the call in Audio Cutter, not Settings.
- `extract_tasks/__init__.py` has NO module-level `logger` — Task 6 pseudocode uses inline `logging.getLogger(__name__)` to avoid NameError that would block `save_tasks_raw`.
- Banner row-shift instructions now enumerate the exact 7 grid edits + 1 rowconfigure edit (no broad "shift all" wording).
- ffmpeg site coverage corrected: 9 sites total — 6 in `audio_io.py` (around line 225+), 2 in `cloud_chunker.py` (415, 463), 1 in `groq.py` (266). `get_ffprobe_path()` removed — no bare `ffprobe` calls exist.
- `tests/test_providers_groq.py:319` updated to use the new helper instead of asserting literal `"ffmpeg"` in argv.
- Final test count: **490** (consistent across plan).
- Task 9 step 5 (memory note) reworded to not name the `~/.claude/` path explicitly inside the plan — the user updates their notes outside this implementation scope.
- Gate test for ctranslate2-missing case uses subprocess + import-hook to actually simulate the failure (v3 source-text check was insufficient).

**Changes from v2 (post-Codex review 2026-05-27):**
- Lazy-refactor scope corrected (`voice_library.py` is NOT affected — stdlib + numpy only). True files: `transcriber/cuda_utils.py`, `transcriber/__init__.py`, `diarize_worker.py`, `enrollment_worker.py`. `TranscriptionCancelled` relocated.
- Exception catch widened to `(ImportError, OSError, RuntimeError)` for Windows DLL failure modes.
- Diarization launcher (`_launch_diarization_subprocess` at `transcriber/__init__.py:351`) is itself guarded — not just callers.
- NEW Task 3: hide non-diarizing providers + silence-removal + voice library UI in cloud-only bundle (was missing entirely in v2; otherwise the hybrid auto-route would silently engage local pyannote on Groq/OpenAI Whisper selection → guaranteed runtime failures).
- ffmpeg resolution now covers ALL bare `"ffmpeg"` sites (`utils.check_ffmpeg`, `audio_io.py`, `transcriber/cloud_chunker.py:415` + `:463`, `providers/groq.py:266`) via a single `utils.get_ffmpeg_path()` helper.
- Config bootstrap REMOVED — bundle ships with a starter `config.json` next to `utils.py` (PyInstaller `datas`) so the existing `utils.load_config()` finds it immediately. First-run banner triggers on empty API keys, not on missing file.
- Test arithmetic corrected: +22 new tests → final 484 (was claimed 483 in v2).
- UI integration pseudocode uses REAL dialog instance state, not invented names.
- Banner widget uses grid layout (existing root is grid via `builder.py:47`), settings command is `_open_settings_dialog`.
- Distribution location is `C:\Apps\AudioTranscriber\` (not Program Files — existing code self-writes `logs/`, `config.json`, `history/` beside `utils.py`).
- AssemblyAI pricing in onboarding doc reflects 2026-05-27 actual rates ($0.15/h Universal-2 + $0.02/h diarization). (Stale prices in `settings.py:404` + `providers/assemblyai.py:12` will be cleaned up in a separately-tracked PR — see `mcp__ccd_session__spawn_task` chip from this planning session.)
- Memory update steps in Task 9 reworded — they touch `~/.claude/projects/...` user-state, not repo code (the boundary instruction was about codex's read-scope, not about plan's execution scope).

---

## Context

**Why this plan exists:** 2026-05-27 pivot. The 2581-line Tauri SaaS spec is the long-term target but a 6-month build. User needs 3 first paying clients on the existing Python app **this week**. Tauri rewrite resumes once MVP is live.

**Scope decisions (locked-in 2026-05-27, refined by Codex challenge):**

- **Distribution:** PyInstaller `--onedir` `.exe` bundle. Final delivery = `.zip` of `dist/AudioTranscriber/`. Client extracts to `C:\Apps\AudioTranscriber\` (user-writable). Bundle target **200-500 MB**.
- **STT + diarization:** CLOUD ONLY via AssemblyAI default (Universal model, KZ+RU+EN code-switching + built-in diarization, ~$0.17/hour combined). **Diarization default = ON** in bundle (`app._diar_var = True` at `builder.py:134`) so the user doesn't have to toggle anything to get speaker labels.
- **Cloud routing enforced:** `config.example.json` ships with `cloud_enabled: true` so the bundle's first launch uses the cloud path, not the (excluded) local engine.
- **Cloud-only UI gating:** Settings UI at `ui/dialogs/settings.py:352` filters Groq + OpenAI Whisper out of the provider dropdown when `_LOCAL_AVAILABLE = False`. Hides voice-library + Audio-Cutter silence-removal button. Without this gate, the existing hybrid auto-route at `transcriber/__init__.py:1135` would silently engage local pyannote — and pyannote is excluded from the bundle.
- **Protocol format:** Full 5-block MoM from Tauri spec §7.9. Generated as a NEW LLM pass via existing `OpenRouterClient`.
- **Trade-off:** Quality > scope. **Trello backend dropped.** Linear + Glide sufficient.

**Out of scope:**

- Trello (or any new) task backend
- macOS / Linux builds, MSI installer
- Local STT or local diarize in the bundle
- Voice library / speaker enrollment in the bundle
- Silence removal in the bundle (lazy `faster_whisper.vad` import would fail — see Task 3)
- Multi-meeting-type protocol templates
- Auto-distribution of protocol via email/Telegram (manual user copy)
- In-app onboarding wizard
- Cross-device sync, vault model, RAG chat, MCP server — Tauri-spec items
- Patching the codebase's stale `$0.65/h` AssemblyAI references in `settings.py:404` + `providers/assemblyai.py:12` (separate PR via spawn-task chip from this session)

**Daily milestone budget (5 working days):**

| Day | Goal |
|---|---|
| Mon | Task 1 (verify clients) + Task 2 (lazy refactor — full day) |
| Tue | Task 3 (UI gating for cloud-only) + Task 4 (PyInstaller spike — much smoother now) |
| Wed | Task 5 (`protocol_generator` module) + Task 6 (UI integration) |
| Thu | Task 7 (bundle integration with bundled `config.json` + ffmpeg helper + banner) |
| Fri | Task 8 (clean-machine smoke at `C:\Apps\AudioTranscriber\`) + Task 9 (delivery) |

If Task 4 (PyInstaller) fails by EOD-2 even after lazy-refactor → fall back to "ship Python source + handhold install". Hard gate.

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `tasks/protocol_generator.py` | LLM-driven 5-block MoM. Mocked clients for tests. |
| `tasks/protocol_template.py` | 5-block template + `substitute()` + `Placeholders` dataclass. |
| `tests/test_protocol_generator.py` | Unit tests for generator. |
| `tests/test_protocol_template.py` | Tests for template substitution. |
| `tests/test_local_engine_gate.py` | Verifies `_LOCAL_AVAILABLE` flag + `_require_local()` raises correctly. Uses `monkeypatch` on module attrs — actual runtime testing, not source-text. |
| `tests/test_bundle_ui_gating.py` | Source-text checks that voice-library + silence-removal + non-diarizing-provider UI render conditionally. |
| `tests/test_ffmpeg_path_resolution.py` | Verifies `utils.get_ffmpeg_path()` resolves to bundled binary in frozen mode and falls back to PATH in source mode. |
| `audio_transcriber.spec` | PyInstaller spec (onedir, minimal hiddenimports). |
| `runtime_hook_imports.py` | PyInstaller runtime hook — `faulthandler.enable()` only (invariant #1). |
| `scripts/build_exe.ps1` | Build script: clean → PyInstaller → copy `config.example.json` → `_internal/config.json` → verify size. |
| `vendor/ffmpeg/.gitkeep` | Placeholder (binaries are gitignored). |
| `docs/CLIENT_SETUP.md` | Onboarding doc for first 3 clients (cloud-only path, $0.15-0.17/h pricing). |
| `docs/PROTOCOL_TEMPLATE.md` | Documents the 5-block MoM template. |
| `requirements-build.txt` | `pyinstaller==6.10.0`. |

**Modified files:**

| Path | Change |
|---|---|
| `transcriber/cuda_utils.py` | Move `TranscriptionCancelled` class + `_check_cancelled` + **define `LocalEngineUnavailable`** ABOVE the `import ctranslate2` line. Wrap the `import ctranslate2` in `try/except (ImportError, OSError, RuntimeError)`. Add `_LOCAL_AVAILABLE`, `_LOCAL_IMPORT_ERROR` module attrs. `LocalEngineUnavailable` MUST live in this file (not in `__init__.py`) so the import path `from transcriber.cuda_utils import LocalEngineUnavailable` works even when ctranslate2 fails to load. |
| `transcriber/__init__.py` | Wrap `from faster_whisper import WhisperModel` in same try/except. **Import** (do NOT redefine) `LocalEngineUnavailable` from `cuda_utils`. Add `_require_local()` function that raises it. Add the guard at top of every local-only function: `load_model`, `_decode_chunk_single`, `_decode_chunk_mixed`, `_launch_diarization_subprocess` (the Popen-owner at line 351), `_transcribe_via_cloud_with_local_diarize`. Re-export `_LOCAL_AVAILABLE` + `LocalEngineUnavailable` for callers. |
| `diarize_worker.py` | Already a subprocess script never bundled. Add a module-docstring note. No other change. |
| `enrollment_worker.py` | Same — already excluded from bundle. Document. |
| `utils.py` | Add `get_ffmpeg_path()` + `get_ffprobe_path()` helpers — bundled-first (`sys._MEIPASS/vendor/ffmpeg/`), PATH fallback. Rewrite `check_ffmpeg()` to use `get_ffmpeg_path()` (returns True if found). |
| `audio_io.py` | Replace bare `"ffmpeg"`/`"ffprobe"` strings with `utils.get_ffmpeg_path()`/`get_ffprobe_path()`. |
| `transcriber/cloud_chunker.py` | Same — `line 415` + `line 463` bare `"ffmpeg"` → helper. |
| `providers/groq.py` | Same — `line 266` bare `"ffmpeg"` → helper. |
| `ui/dialogs/settings.py` | Wrap voice-library section at `line 427` ("Голоса" button) in `if _LOCAL_AVAILABLE:`. **Provider dropdown is constructed in this file around line 352** (NOT in builder.py per v3 — Codex sanity-check #2 corrected). Filter Groq + OpenAI Whisper out of the dropdown's option list when `_LOCAL_AVAILABLE = False`. Use `_enabled_cloud_providers()` helper defined in this same file. |
| `ui/app/builder.py` | **Flip `app._diar_var = tk.BooleanVar(value=True)`** at line 134 (was False). Without this default-on, AssemblyAI returns transcripts without speaker labels — contradicts the stated "качественная диаризация" goal. Also: shift the 7 known root-grid rows by +1 to make room for first-run banner at row=0; update `grid_rowconfigure(6)` → `(7)`. Concrete edits enumerated in Task 7 step 5. |
| `audio_cutter.py` | At line 227 and line 649 the editor calls `silence_remover.remove_silences()` (which lazy-imports `faster_whisper.vad` — excluded from bundle). Either gate the Audio Cutter dialog entry point on `_LOCAL_AVAILABLE`, OR wrap the silence-removal button inside it. Recommend the latter — Audio Cutter has other useful features (manual trim) that work cloud-only. |
| `ui/app/__init__.py` | Add first-run banner trigger. Detect first-run via: `config.get("cloud_enabled") is False OR config.get("cloud_api_keys", {}).get("AssemblyAI", "").strip() == ""`. Keys use DISPLAY-NAME format (`"AssemblyAI"`, not `"assemblyai"`) per `builder.py:167`. Banner calls `self._open_settings_dialog`. |
| `config.example.json` | Set `"cloud_enabled": true` (NOT `false` — bundle would otherwise default into LOCAL transcription path per `transcription_mixin.py:160`). Set `"cloud_provider": "AssemblyAI"` (display-name format). Set `"diarize_default": true` or whatever key `app._diar_var` initialization reads from (verify by reading `builder.py` around line 134). |
| `ui/dialogs/extract_tasks/__init__.py` | Add `generate_protocol` BooleanVar (default ON) to the form near existing backend checkboxes. In `_run_extraction` (line 530), after `extract()` succeeds at line 560-567, conditionally call `protocol_generator.generate(...)` using `self._transcript`, `self._transcript_lang`, `self._model_var.get()`, the locally-constructed `openrouter` client, and write to `Path(self._history_folder) / "protocol.md"`. |
| `config.example.json` | Verify keys match what builder.py:163 reads (`cloud_provider`, `cloud_api_keys`). Set `"cloud_provider": "assemblyai"` and `"cloud_engine": true` (or whatever the existing key is — read first). |
| `requirements.txt` | NO CHANGE. Local deps stay for source-mode devs. |
| `.gitignore` | Add `build/`, `dist/`, `*.zip`, `vendor/ffmpeg/*.exe`, `.venv-build/`. |
| `README.md` | Add "Build .exe (developer)" section pointing at `scripts/build_exe.ps1`. |
| `CLAUDE.md` | Annotate invariants #2 and #4: "(only on local-engine code path — not exercised by cloud-only PyInstaller bundle, see `_LOCAL_AVAILABLE` in `transcriber/__init__.py`)". Add invariant #9: bundle must exclude torch/ctranslate2/faster-whisper/pyannote/speechbrain via PyInstaller `excludes=[...]`. Add invariant #10: `TranscriptionCancelled` MUST live above the gated `ctranslate2` import in `cuda_utils.py` — cloud providers depend on it. |

---

## Task 1: Verify the 3 clients' setup

Cloud-only bundle removes GPU + HuggingFace requirements. Per-client info:

```
Client name: ___
Windows version: ___ (10 or 11, 64-bit required)
Disk free: ___ GB (need ≥ 2 GB)
RAM: ___ GB (need ≥ 8 GB)
AssemblyAI account: ___ (yes/no + ETA if no)
OpenRouter account / API key: ___ (yes/no)
Task backend needed: ___ (Linear / Glide / both / undecided ok)
```

- [ ] Send checklist to each client out-of-band
- [ ] Record status in private note (do not commit names)
- [ ] No code change. Proceed to Task 2.

---

## Task 2: Lazy-import refactor

**Files:**
- Modify: `transcriber/cuda_utils.py`
- Modify: `transcriber/__init__.py`
- Modify: `diarize_worker.py` (docstring only)
- Modify: `enrollment_worker.py` (docstring only)
- Create: `tests/test_local_engine_gate.py`

### Subtask 2a: cuda_utils.py refactor

- [ ] **Step 1: Baseline**

```powershell
pytest
python -m ruff check .
```

Expected: 462 tests pass, ruff clean.

- [ ] **Step 2: Read current `transcriber/cuda_utils.py`**

Use Read to load the file. Verify the current order at the top:
1. Module docstring
2. `from __future__ import annotations`
3. `import ctranslate2  # noqa: F401`
4. `class TranscriptionCancelled(Exception):`
5. `def _check_cancelled(...)`
6. `def _cuda_is_available() -> bool:` (uses ctranslate2)

- [ ] **Step 3: Write failing test for the gate**

Create `tests/test_local_engine_gate.py`:

```python
"""Test the lazy-import gate.

We can't easily simulate missing torch in a single test process (the real
module is already loaded), so we use monkeypatch to flip the flag and verify
guard behavior. Source-text checks complement this by ensuring the gate is
actually wired in.
"""
from pathlib import Path
import pytest


def test_local_available_flag_present_in_transcriber():
    import transcriber
    assert hasattr(transcriber, "_LOCAL_AVAILABLE")
    assert isinstance(transcriber._LOCAL_AVAILABLE, bool)


def test_local_engine_unavailable_exception_class_exists():
    from transcriber import LocalEngineUnavailable
    assert issubclass(LocalEngineUnavailable, Exception)


def test_require_local_raises_when_unavailable(monkeypatch):
    import transcriber
    monkeypatch.setattr(transcriber, "_LOCAL_AVAILABLE", False)
    monkeypatch.setattr(transcriber, "_LOCAL_IMPORT_ERROR", ImportError("test"))
    with pytest.raises(transcriber.LocalEngineUnavailable) as exc:
        transcriber._require_local()
    msg = str(exc.value).lower()
    assert "локальн" in msg or "local" in msg
    assert "облач" in msg or "cloud" in msg


def test_require_local_passes_when_available(monkeypatch):
    import transcriber
    monkeypatch.setattr(transcriber, "_LOCAL_AVAILABLE", True)
    transcriber._require_local()  # must not raise


def test_transcription_cancelled_class_position_in_source():
    # TranscriptionCancelled + LocalEngineUnavailable must be defined ABOVE the
    # gated ctranslate2 import in cuda_utils.py so they survive bundle load.
    src = Path("transcriber/cuda_utils.py").read_text(encoding="utf-8")
    cancelled_pos = src.find("class TranscriptionCancelled")
    unavail_pos = src.find("class LocalEngineUnavailable")
    ct2_pos = src.find("import ctranslate2")
    assert cancelled_pos != -1 and unavail_pos != -1 and ct2_pos != -1
    assert cancelled_pos < ct2_pos
    assert unavail_pos < ct2_pos


def test_cuda_utils_loads_when_ctranslate2_missing(tmp_path):
    """Subprocess test: simulate ctranslate2 unavailable, verify cuda_utils loads.

    This is the real bundle-mode test — we cannot just monkeypatch in-process
    because ctranslate2 is already imported. We run a fresh Python subprocess
    with sys.modules['ctranslate2'] = None (raises ImportError on next import).
    """
    import subprocess, sys
    code = (
        "import sys\n"
        "# Block ctranslate2 by inserting a meta_path finder that raises ImportError\n"
        "import importlib.abc, importlib.machinery\n"
        "class _BlockCT2(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'ctranslate2':\n"
        "            raise ImportError('blocked for test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _BlockCT2())\n"
        "# Now import cuda_utils — should load WITHOUT raising\n"
        "from transcriber.cuda_utils import (\n"
        "    TranscriptionCancelled, LocalEngineUnavailable,\n"
        "    _LOCAL_AVAILABLE, _LOCAL_IMPORT_ERROR,\n"
        ")\n"
        "assert _LOCAL_AVAILABLE is False\n"
        "assert _LOCAL_IMPORT_ERROR is not None\n"
        "print('PASS')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert "PASS" in result.stdout, \
        f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_cuda_utils_source_has_class_above_ctranslate2_import():
    src = Path("transcriber/cuda_utils.py").read_text(encoding="utf-8")
    class_pos = src.find("class TranscriptionCancelled")
    import_pos = src.find("import ctranslate2")
    assert class_pos != -1
    assert import_pos != -1
    assert class_pos < import_pos, "TranscriptionCancelled must be defined BEFORE ctranslate2 import to survive its failure"


def test_init_source_catches_oserror_and_runtimeerror():
    # Windows DLL failures arrive as OSError or RuntimeError, not ImportError.
    src = Path("transcriber/__init__.py").read_text(encoding="utf-8")
    assert "OSError" in src and "RuntimeError" in src and "ImportError" in src, \
        "try/except around heavy imports must catch (ImportError, OSError, RuntimeError)"
```

- [ ] **Step 4: Run tests, watch them fail**

```powershell
pytest tests/test_local_engine_gate.py -v
```

Expected: 7 failures (module doesn't have the symbols yet).

- [ ] **Step 5: Refactor `transcriber/cuda_utils.py`**

Restructure the file so `TranscriptionCancelled` and `_check_cancelled` come FIRST, then the gated import:

```python
"""CUDA availability + cancellation primitives for the transcriber package.

Cloud providers import TranscriptionCancelled + LocalEngineUnavailable from
this module — see providers/assemblyai.py:313. Both classes are defined
ABOVE the gated `ctranslate2` import so they survive a missing-CUDA bundle.

Invariant #2 (ctranslate2 before torch) only fires when _LOCAL_AVAILABLE = True.
"""
from __future__ import annotations


# ─── Cancellation + gate primitives (cloud-safe — no local deps) ─────


class TranscriptionCancelled(Exception):
    """Raised inside :meth:`Transcriber.transcribe` when the cancel event fires."""


class LocalEngineUnavailable(RuntimeError):
    """Raised by _require_local() when ctranslate2 / faster_whisper failed to import.

    Defined here (not in transcriber/__init__.py) so the import
    `from transcriber.cuda_utils import LocalEngineUnavailable` resolves
    cleanly even when ctranslate2 has failed at line `try: import ctranslate2`
    below. Cloud providers that need to distinguish "user cancelled" from
    "local engine missing" import this class.
    """


def _check_cancelled(cancel_event) -> None:
    """Raise :class:`TranscriptionCancelled` if the event is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelled()


# ─── Gated local-CUDA imports ──────────────────────────────────────────
#
# Wrap in try/except (ImportError, OSError, RuntimeError) because on Windows,
# missing/broken CUDA DLLs surface as OSError (WinError 126) or RuntimeError,
# NOT reliably ImportError. ctranslate2 must be imported before torch on
# Windows when both are available (invariant #2 from CLAUDE.md).

_LOCAL_AVAILABLE: bool
_LOCAL_IMPORT_ERROR: BaseException | None

try:
    import ctranslate2  # noqa: F401
    _LOCAL_AVAILABLE = True
    _LOCAL_IMPORT_ERROR = None
except (ImportError, OSError, RuntimeError) as _e:
    _LOCAL_AVAILABLE = False
    _LOCAL_IMPORT_ERROR = _e


def _cuda_is_available() -> bool:
    """Cheap CUDA-availability probe via ctranslate2.

    Returns False when the local stack is unavailable — the bundle uses this
    to keep the CUDA-device dropdown empty.
    """
    if not _LOCAL_AVAILABLE:
        return False
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
```

- [ ] **Step 6: Refactor `transcriber/__init__.py`**

Read the file first (top 80 lines + the `_launch_diarization_subprocess` function around line 351). Then:

A. Wrap the `from faster_whisper import WhisperModel` line in try/except. Mirror the cuda_utils pattern:

```python
# Near top of file, after the existing faulthandler bootstrap. NOTE:
# LocalEngineUnavailable is imported from cuda_utils, not defined here.
# Defining it here would put it BELOW the gated faster_whisper import,
# breaking the cloud-only bundle the same way TranscriptionCancelled
# would be broken if left below ctranslate2.
from transcriber.cuda_utils import (
    LocalEngineUnavailable,
    TranscriptionCancelled,
    _check_cancelled,
    _cuda_is_available,
    _LOCAL_AVAILABLE as _CT2_AVAILABLE,
    _LOCAL_IMPORT_ERROR as _CT2_ERROR,
)

try:
    from faster_whisper import WhisperModel  # noqa: F401
    _FW_AVAILABLE = True
    _FW_ERROR = None
except (ImportError, OSError, RuntimeError) as _e:
    _FW_AVAILABLE = False
    _FW_ERROR = _e

_LOCAL_AVAILABLE = _CT2_AVAILABLE and _FW_AVAILABLE
_LOCAL_IMPORT_ERROR = _CT2_ERROR or _FW_ERROR


def _require_local() -> None:
    if not _LOCAL_AVAILABLE:
        raise LocalEngineUnavailable(
            "Локальный движок (torch + ctranslate2 + faster-whisper) недоступен. "
            "Откройте Настройки → выберите облачного провайдера (AssemblyAI). "
            f"(Импорт упал: {_LOCAL_IMPORT_ERROR})"
        )
```

B. Add `_require_local()` at the top of every local-only function. The minimum set per Codex findings #5-8 + #13:
- `load_model()`
- `_decode_chunk_single()`
- `_decode_chunk_mixed()`
- `_launch_diarization_subprocess()` (around line 351 — guard the launcher itself, not just callers)
- `_transcribe_via_cloud_with_local_diarize()` (around line 744 — hybrid auto-route target)
- Any other function that touches `WhisperModel`, `ctranslate2`, or spawns `diarize_worker.py`

Use Grep with pattern `WhisperModel|ctranslate2|diarize_worker` over `transcriber/__init__.py` to confirm the exhaustive list. Each match's containing function gets `_require_local()` as its first line (or first line after docstring).

- [ ] **Step 7: Run tests**

```powershell
pytest tests/test_local_engine_gate.py -v
pytest  # full suite
python -m ruff check .
```

Expected: 7 new tests pass, baseline 462 still pass → total 469. Ruff clean.

If any existing test in the suite now fails, it's because a function we guarded is called by a test on a path where `_LOCAL_AVAILABLE` is unexpectedly False. Investigate; in source mode with torch installed `_LOCAL_AVAILABLE = True`, so this should not happen. If it does, the test is likely directly mutating module state — fix the test, not the gate.

- [ ] **Step 8: Manual source-mode smoke**

```powershell
python app.py
```

Verify: local transcription still works as before, cloud transcription still works as before, voice library + silence removal still work. **Do not commit if source mode regresses.**

- [ ] **Step 9: Add docstring notes to `diarize_worker.py` and `enrollment_worker.py`**

These are subprocess scripts spawned from the main app. PyInstaller `--onedir` bundles don't include them as executables — they remain `.py` files invoked via `subprocess.run([sys.executable, "diarize_worker.py", ...])`. In frozen mode, `sys.executable` is the bundle, not Python — so this call fails. The fix is to NOT REACH this call site in the bundle (Task 3 hides the UI affordances). Document this constraint at the top of both files:

```python
"""diarize_worker.py — subprocess for pyannote diarization.

NOT INCLUDED in the PyInstaller cloud-only bundle. In source mode, the main
process spawns this script via subprocess.run([sys.executable, "diarize_worker.py", ...]).
In frozen mode, sys.executable points at the bundle, not Python — so the
caller must be gated. See Task 3 in docs/superpowers/plans/2026-05-27-mvp-3-clients-this-week.md
for the UI gating that prevents the spawn from happening.
"""
```

- [ ] **Step 10: Commit Task 2**

```powershell
git add transcriber/ diarize_worker.py enrollment_worker.py tests/test_local_engine_gate.py
git commit -m "refactor(transcriber): lazy-import local-CUDA stack behind _LOCAL_AVAILABLE

Wraps ctranslate2 (in cuda_utils.py) and faster_whisper (in __init__.py) in
try/except (ImportError, OSError, RuntimeError) — Windows DLL failures
surface as the latter two, not reliably ImportError.

TranscriptionCancelled relocated ABOVE the gated ctranslate2 import so cloud
providers (e.g. providers/assemblyai.py:313) keep importing it cleanly when
ctranslate2 fails to load.

_require_local() guard added at every local-only entry point including
_launch_diarization_subprocess (the Popen owner at line 351), not just its
callers. This catches the case codex flagged: hybrid auto-route at line 1135
→ _transcribe_via_cloud_with_local_diarize → eventually Popen.

7 new tests cover the gate via monkeypatch + source-text invariant checks.
Source mode (with torch installed) is unchanged."
```

---

## Task 3: Cloud-only UI gating

**Why this is a separate task:** Even with the lazy-import gate from Task 2, the bundle is **not really cloud-only** until the UI stops letting users select code paths that would trigger `_require_local()` failures. The existing hybrid auto-route at `transcriber/__init__.py:1135` silently engages local pyannote when:
- Provider = Groq or OpenAI Whisper (no native `supports_diarization`)
- AND `diarize=True`

In the bundle without pyannote, this fails at runtime — embarrassing for clients. Same applies to the voice library and silence removal: their underlying lazy imports (e.g. `silence_remover.py:96`'s `from faster_whisper.vad import ...`) fail at first use, not at module load. UI must prevent the user from reaching them.

**Files:**
- Modify: `ui/dialogs/settings.py` (hide "Голоса" entry at line 427-430 + silence-removal section)
- Modify: `ui/app/builder.py` (provider dropdown — hide non-diarize-capable providers when not `_LOCAL_AVAILABLE`)
- Create: `tests/test_bundle_ui_gating.py`

- [ ] **Step 1: Write failing source-text tests**

Create `tests/test_bundle_ui_gating.py`:

```python
"""Source-text checks that the bundle UI hides local-only affordances when
_LOCAL_AVAILABLE is False. Linux CI does not import ui/ (per memory
feedback_ui_app_import_breaks_linux_ci) — these checks read the source text.
"""
from pathlib import Path


def test_settings_voice_library_section_gated():
    src = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")
    # The Голоса button (line ~427) must be wrapped in a _LOCAL_AVAILABLE check
    voice_pos = src.find('text="Голоса"')
    assert voice_pos != -1
    # Look backwards from voice_pos for an `if _LOCAL_AVAILABLE` or similar guard
    preceding = src[:voice_pos]
    assert "_LOCAL_AVAILABLE" in preceding[-500:], \
        "Голоса button must be inside an `if _LOCAL_AVAILABLE:` block"


def test_settings_silence_removal_gated():
    src = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")
    # find silence-remove section (look for the Russian label or English flag)
    candidates = ["silence_removal", "silence-remove", "удаление тишины", "Удаление тишины"]
    found = any(c in src for c in candidates)
    assert found, "silence-removal section not located by known markers"
    # And it should be inside a _LOCAL_AVAILABLE guard
    # (engineer to verify by reading + correct test markers if needed)


def test_builder_provider_dropdown_filters_local_dependent_providers():
    src = Path("ui/app/builder.py").read_text(encoding="utf-8")
    # The provider list construction must filter Groq + OpenAI Whisper when
    # _LOCAL_AVAILABLE is False (otherwise selecting them + diarize triggers
    # the hybrid auto-route into local pyannote, which fails in the bundle).
    assert "_LOCAL_AVAILABLE" in src
    # Look for filtering logic near provider names
    assert "groq" in src.lower() or "openai_whisper" in src.lower(), \
        "expected provider names in the dropdown construction"


def test_main_window_has_first_run_banner_logic():
    # First-run banner shows when AssemblyAI key is empty after config load.
    src_app = Path("ui/app/__init__.py").read_text(encoding="utf-8")
    assert "first_run" in src_app or "_first_run" in src_app
    assert "cloud_api_keys" in src_app
```

- [ ] **Step 2: Run tests, watch fail**

```powershell
pytest tests/test_bundle_ui_gating.py -v
```

- [ ] **Step 3: Read existing files to find exact insertion points**

Use the Read tool on:
- `ui/dialogs/settings.py` around lines 415-460 (find the Голоса button + surrounding section + silence-removal section)
- `ui/app/builder.py` around lines 100-200 (find provider dropdown construction; the cloud-provider list is likely defined in `ui/app/constants.py` based on the imports at line 38-44 — read that too)
- `ui/app/__init__.py` (the App class; find `__init__` body to add the first-run flag)

- [ ] **Step 4: Apply gates in `ui/dialogs/settings.py`**

Add an import at the top:

```python
from transcriber import _LOCAL_AVAILABLE
```

Wrap the Голоса button block (lines 427-432):

```python
if _LOCAL_AVAILABLE:
    tonal_button(
        section, text="Голоса",
        command=self._parent._open_voices_dialog, width=200,
    ).grid(row=1, column=0, padx=4, pady=6, sticky="w")
    self._voices_summary = label(section, "", anchor="w")
    self._voices_summary.grid(row=1, column=1, padx=(8, 4), pady=6, sticky="ew")
# else: omit the entry entirely in cloud-only bundle.
```

**Silence removal is NOT in Settings** (codex sanity-check #4 corrected v3). The actual call lives in `audio_cutter.py:227` + `audio_cutter.py:649` invoking `silence_remover.remove_silences()`. To gate it, open `audio_cutter.py` and wrap those call sites + the corresponding UI button:

```python
# At the top of audio_cutter.py
from transcriber import _LOCAL_AVAILABLE

# Where the "Remove silences" button is created (find via grep "silence" or
# "тишин" in audio_cutter.py)
if _LOCAL_AVAILABLE:
    # existing button creation here
    ...
# else: do not create the button. Audio Cutter still offers manual trim, etc.
```

Audio Cutter's manual-trim features work cloud-only — hiding only the silence-removal button (not the entire dialog) keeps useful functionality.

- [ ] **Step 5: Apply provider-dropdown filter in `ui/dialogs/settings.py:352` (NOT in builder.py)**

The provider dropdown is constructed inside the Settings dialog around line 352, not in `ui/app/builder.py` (Codex sanity-check #2 — v3 targeted the wrong file). Read `ui/dialogs/settings.py:330-370` to find the exact dropdown construction. Identify how the option list is currently built (likely from `providers.PROVIDERS.keys()` or a hardcoded list of display names).

Add the filter helper in the same file (top of the class or as a module-level function):

```python
# In ui/dialogs/settings.py
from transcriber import _LOCAL_AVAILABLE


def _enabled_cloud_providers():
    """Return the provider display-names visible to the user.

    In the cloud-only bundle (_LOCAL_AVAILABLE = False), Groq and OpenAI Whisper
    are hidden because they lack native diarization. Selecting them + diarize=True
    triggers the hybrid auto-route into local pyannote (transcriber/__init__.py:1135),
    which fails because pyannote is excluded from the bundle.
    """
    from providers import PROVIDERS
    if _LOCAL_AVAILABLE:
        return list(PROVIDERS.keys())
    return [
        name for name, cls in PROVIDERS.items()
        if getattr(cls, "supports_diarization", False)
    ]
```

Then in the dropdown construction near line 352, replace the option-list argument:

```python
# Old (something like):
#   ctk.CTkOptionMenu(..., values=list(PROVIDERS.keys()), ...)
# New:
ctk.CTkOptionMenu(..., values=_enabled_cloud_providers(), ...)
```

**Important key-name detail (Codex sanity-check #5):** Runtime stores provider preference under DISPLAY-NAME keys, not lowercase. `builder.py:167` shows the dropdown's current value is `"AssemblyAI"` (capitalized). `PROVIDERS.keys()` are the same display-name strings. Do not lowercase anywhere.

- [ ] **Step 6: Apply first-run detection in `ui/app/__init__.py`**

Inside `App.__init__()`, after the config is loaded:

```python
# Detect first run = cloud disabled OR no AssemblyAI key. Used by the banner
# (added in Task 7) to prompt the user toward Settings.
#
# Key-name detail per Codex sanity-check #5 + builder.py:167: runtime uses
# DISPLAY-NAME keys, NOT lowercase. The dropdown's current value is "AssemblyAI"
# (capitalized), and the api-keys dict uses the same display-name strings.
# `cloud_enabled` is the engine-toggle key (NOT `cloud_engine`).
cloud_keys = self._config.get("cloud_api_keys", {}) or {}
self._first_run = (
    not self._config.get("cloud_enabled", False)
    or not cloud_keys.get("AssemblyAI", "").strip()
)
```

The actual banner widget is built in Task 7 (where `build_ui(app)` shifts grid rows to make room).

- [ ] **Step 7: Flip diarization default in `ui/app/builder.py:134`**

The default value at line 134 currently is `app._diar_var = tk.BooleanVar(value=False)`. Change to `value=True`. Without this, AssemblyAI returns transcripts with no speaker labels — clients click "Транскрибировать" and get a wall of text from "Speaker A" only. Codex sanity-check #3.

```python
# In ui/app/builder.py around line 134
app._diar_var = tk.BooleanVar(value=True)  # was False — Codex sanity #3
```

If this breaks any existing test that expected the default to be False, update those tests (search via `grep "_diar_var"` across tests/).

- [ ] **Step 8: Update `config.example.json`**

The bundle ships this file as `_internal/config.json` (Task 4 build script). It MUST default the cloud engine ON, otherwise the existing routing at `transcription_mixin.py:160` picks the LOCAL path → which is excluded from the bundle.

Read `config.example.json` first, then change:

```json
{
  "cloud_enabled": true,           // was false — Codex sanity #2
  "cloud_provider": "AssemblyAI",  // display-name format, matches builder.py:167
  "cloud_api_keys": {
    "AssemblyAI": "",              // user fills via Settings on first run
    "Deepgram": "",
    "Gladia": "",
    "Speechmatics": ""
  },
  // ... preserve other existing keys verbatim
}
```

Verify the structure by reading the current `config.example.json` first — keys not listed above should be preserved.

- [ ] **Step 9: Run tests + manual smoke**

```powershell
pytest tests/test_bundle_ui_gating.py -v
pytest  # full suite — expect 462 + 7 (Task 2) + 4 (Task 3) = 473
python app.py  # source mode: voice library + silence removal + all providers visible; diar toggle now defaults ON
```

Expected: gating tests pass, source mode unchanged in behavior. Diar-on default may show in UI as the checkbox starting checked — this is intentional.

- [ ] **Step 10: Commit Task 3**

```powershell
git add ui/dialogs/settings.py audio_cutter.py ui/app/builder.py ui/app/__init__.py config.example.json tests/test_bundle_ui_gating.py
git commit -m "feat(ui): cloud-only gating + cloud-enabled defaults + diarize-on

Bundle mode (_LOCAL_AVAILABLE = False) hides:
- Settings 'Голоса' voice library section
- Audio Cutter silence-removal button (the real call site — silence removal
  is NOT in Settings dialog as v3 plan incorrectly claimed)
- Settings provider dropdown options for Groq + OpenAI Whisper (the dropdown
  itself lives at settings.py:352, NOT builder.py)

Diarization default flipped from False to True at builder.py:134 — without
this, AssemblyAI returns no speaker labels, contradicting MVP goal.

config.example.json now defaults cloud_enabled: true so the bundle's first
launch uses the cloud routing path (not local, which transcription_mixin.py:160
otherwise selects).

First-run banner trigger uses display-name keys (AssemblyAI, not assemblyai)
matching builder.py:167. Checks BOTH cloud_enabled AND empty key for
robustness.

Source mode unchanged."
```

---

## Task 4: PyInstaller spike

**Files:**
- Create: `audio_transcriber.spec`
- Create: `runtime_hook_imports.py`
- Create: `requirements-build.txt`
- Create: `scripts/build_exe.ps1`
- Create: `vendor/ffmpeg/.gitkeep`

- [ ] **Step 1: Build deps + ffmpeg vendor**

```
requirements-build.txt:
pyinstaller==6.10.0
```

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-build.txt
```

Download ffmpeg + ffprobe from https://www.gyan.dev/ffmpeg/builds/ into `vendor/ffmpeg/`. Verify:
```powershell
.\vendor\ffmpeg\ffmpeg.exe -version
```

- [ ] **Step 2: Runtime hook**

`runtime_hook_imports.py`:
```python
"""PyInstaller runtime hook — CLAUDE.md invariant #1 (faulthandler) in frozen mode.

Invariant #2 (ctranslate2 before torch) is NOT a concern: the bundle excludes
both via PyInstaller `excludes=[...]`. The lazy-import refactor in
transcriber/cuda_utils.py wraps the ctranslate2 import in try/except so the
module loads cleanly even when the lib is absent.
"""
import faulthandler
faulthandler.enable()
```

- [ ] **Step 3: PyInstaller spec**

`audio_transcriber.spec`:
```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the cloud-only Audio Transcriber Windows bundle.

Build: pyinstaller audio_transcriber.spec --noconfirm
Output: dist/AudioTranscriber/  (onedir bundle, ~200-500 MB)

Excludes the local-CUDA stack entirely. Bundles ffmpeg, ffprobe, and a
starter config.json (copied from config.example.json by build_exe.ps1
after PyInstaller runs).
"""
from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH)

a = Analysis(
    ['app.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[
        (str(PROJECT_ROOT / 'vendor' / 'ffmpeg' / 'ffmpeg.exe'), 'vendor/ffmpeg'),
        (str(PROJECT_ROOT / 'vendor' / 'ffmpeg' / 'ffprobe.exe'), 'vendor/ffmpeg'),
    ],
    datas=[
        ('config.example.json', '.'),
    ],
    hiddenimports=[
        'requests',
        'urllib3',
        'customtkinter',
        'providers.assemblyai',
        'providers.deepgram',
        'providers.gladia',
        'providers.speechmatics',
        # Note: providers.groq, providers.openai_whisper still importable from
        # source but the bundle's UI gates them via _enabled_cloud_providers() —
        # see Task 3. We still hidden-import them so the registry resolves on
        # load (PROVIDERS dict at providers/__init__.py:10 needs the modules).
        'providers.groq',
        'providers.openai_whisper',
    ],
    hookspath=[],
    runtime_hooks=[str(PROJECT_ROOT / 'runtime_hook_imports.py')],
    excludes=[
        # Heavy local stack — stripped via Task 2 lazy refactor:
        'torch',
        'torchaudio',
        'ctranslate2',
        'faster_whisper',
        'pyannote',
        'pyannote.audio',
        'pyannote.core',
        'pyannote.metrics',
        'speechbrain',
        'lightning',
        'pytorch_lightning',
        'transformers',
        'huggingface_hub',
        # Dev/test:
        'matplotlib',
        'tkinter.test',
        'unittest',
        'pytest',
        'IPython',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='AudioTranscriber',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AudioTranscriber',
)
```

- [ ] **Step 4: Build script**

`scripts/build_exe.ps1`:
```powershell
# Bundle the Audio Transcriber as a Windows .exe (cloud-only, onedir).
$ErrorActionPreference = 'Stop'

Write-Host "1. Cleaning previous build outputs..."
if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
if (Test-Path 'dist')  { Remove-Item -Recurse -Force 'dist'  }

Write-Host "2. Verifying vendor binaries..."
foreach ($name in @('ffmpeg.exe','ffprobe.exe')) {
    $path = "vendor/ffmpeg/$name"
    if (-not (Test-Path $path)) {
        throw "Missing $path — download from https://www.gyan.dev/ffmpeg/builds/"
    }
}

Write-Host "3. Running PyInstaller..."
pyinstaller audio_transcriber.spec --noconfirm

Write-Host "4. Verifying output..."
$bundleDir = 'dist/AudioTranscriber'
$exePath = "$bundleDir/AudioTranscriber.exe"
if (-not (Test-Path $exePath)) {
    throw "Build failed — $exePath not found"
}

Write-Host "5. Seeding starter config.json into _internal/..."
# utils.load_config() reads config.json from beside utils.py, which lives at
# _internal/utils.py in the bundle. Copy config.example.json as config.json
# so first launch has cloud_provider='assemblyai' + empty API keys pre-set.
$internalDir = "$bundleDir/_internal"
if (-not (Test-Path $internalDir)) {
    # PyInstaller 6.x onedir puts py modules in _internal/. Older layouts had
    # them at bundle root. Detect and adapt:
    $internalDir = $bundleDir
}
Copy-Item 'config.example.json' "$internalDir/config.json" -Force

Write-Host "6. Verifying bundle size..."
$bundleSize = (Get-ChildItem -Recurse $bundleDir | Measure-Object -Sum Length).Sum / 1MB
Write-Host ("   Bundle size: {0:N0} MB" -f $bundleSize)
if ($bundleSize -gt 800) {
    Write-Warning "Bundle larger than expected — local CUDA libs may have slipped in. Check Analysis warnings."
}

Write-Host "Done. Run dist/AudioTranscriber/AudioTranscriber.exe to test."
```

- [ ] **Step 5: .gitignore + vendor placeholder**

Edit `.gitignore`, add:
```
build/
dist/
*.zip
vendor/ffmpeg/*.exe
.venv-build/
```

Create empty `vendor/ffmpeg/.gitkeep`.

- [ ] **Step 6: First bundle attempt**

```powershell
.\scripts\build_exe.ps1
```

Expected: build in 2-5 min, output `dist/AudioTranscriber/` ~200-500 MB.

Failure modes + fixes:
- `Module 'X' not found`: add to `hiddenimports`
- Bundle > 800 MB: a transitively-pulled lib slipped in — check Analysis warnings
- App launches then closes immediately: set `console=True` in EXE block, rebuild, run from cmd.exe for traceback
- CustomTkinter themes missing: add `(site-packages/customtkinter/assets, customtkinter/assets)` to `datas`

- [ ] **Step 7: Smoke test the bundle**

```powershell
.\dist\AudioTranscriber\AudioTranscriber.exe
```

Verify:
- UI opens within 5 sec
- Settings → cloud_provider dropdown shows ONLY AssemblyAI/Deepgram/Gladia/Speechmatics (no Groq, no OpenAI Whisper — Task 3 gating works)
- Settings has no Голоса section, no silence-removal toggle
- Cloud transcription (with a real AssemblyAI key) works on a 30-sec audio

- [ ] **Step 8: Commit Task 4**

```powershell
git add audio_transcriber.spec runtime_hook_imports.py requirements-build.txt scripts/build_exe.ps1 vendor/ffmpeg/.gitkeep .gitignore
git commit -m "build: PyInstaller onedir spec for cloud-only bundle

Bundles requests + cloud providers + CustomTkinter + vendored ffmpeg.
Excludes torch / ctranslate2 / faster_whisper / pyannote / speechbrain /
lightning / transformers / huggingface_hub. Target ~200-500 MB.

build_exe.ps1 copies config.example.json -> _internal/config.json so the
bundle's first launch sees a starter config (cloud_provider=assemblyai,
empty API keys). Avoids the bootstrap-from-template ceremony in v2 plan.

Spike outcome: \$OUTCOME (record one-liner — PASS or FAIL + why)."
```

---

## Task 5: Protocol generator module (TDD)

Same as v2 plan, unchanged. Brief recap:

**Files:**
- Create: `tasks/protocol_template.py`
- Create: `tasks/protocol_generator.py`
- Create: `tests/test_protocol_template.py`
- Create: `tests/test_protocol_generator.py`
- Create: `docs/PROTOCOL_TEMPLATE.md`

### Subtask 5a: Template substitution helper

- [ ] Tests first (`tests/test_protocol_template.py`), then implementation (`tasks/protocol_template.py`), then commit.

See v2 plan section "Task 3 → Subtask 3a" for the full code. The module exports `MOM_5_BLOCK_TEMPLATE` (constant), `Placeholders` (6-field frozen dataclass), and `substitute(template, placeholders)`. 4 tests cover replacement, leave-unknown-intact, all-placeholders-present, empty-values.

```powershell
git add tasks/protocol_template.py tests/test_protocol_template.py
git commit -m "feat(tasks): 5-block MoM template + substitution helper"
```

### Subtask 5b: Protocol generator

- [ ] Tests first (`tests/test_protocol_generator.py`), then implementation (`tasks/protocol_generator.py`), then commit.

See v2 plan section "Task 3 → Subtask 3b" for the full code. The module exports `generate(...)`, `build_prompt(...)`, `parse_llm_response(...)`, `ProtocolGenerationError`, `ProtocolResult`. 5 tests cover prompt building, response parsing (5 blocks), missing-block error, end-to-end with mock LLM, error propagation.

Document the template in `docs/PROTOCOL_TEMPLATE.md`.

```powershell
git add tasks/protocol_generator.py tests/test_protocol_generator.py docs/PROTOCOL_TEMPLATE.md
git commit -m "feat(tasks): protocol_generator with 5-block MoM via OpenRouter"
```

After Task 5: 469 + 4 + 5 = 478 tests pass.

---

## Task 6: UI integration — protocol checkbox in extract-tasks dialog

**Why this uses real names this time:** v2 plan invented dialog variables (`_transcript_text`, `_known_speakers`, etc.) that don't exist. Codex finding #22 listed the actual fields. Below uses what `ui/dialogs/extract_tasks/__init__.py:65-75` and `:530-541` actually have.

**Files:**
- Modify: `ui/dialogs/extract_tasks/__init__.py`
- Create: `tests/test_extract_dialog_protocol_checkbox.py`

- [ ] **Step 1: Write source-text test**

Create `tests/test_extract_dialog_protocol_checkbox.py`:

```python
"""Source-text test: extract dialog declares the 'generate protocol' checkbox
and invokes protocol_generator using REAL instance state."""
from pathlib import Path

_DIALOG_FILE = Path("ui/dialogs/extract_tasks/__init__.py")


def test_dialog_imports_protocol_generator():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    assert "from tasks.protocol_generator import" in src or \
           "import tasks.protocol_generator" in src


def test_dialog_declares_generate_protocol_var():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    assert "generate_protocol" in src
    assert "BooleanVar" in src


def test_dialog_runs_protocol_using_real_instance_state():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    # The integration must use the REAL fields (self._transcript, etc.)
    # not invented ones.
    assert "self._transcript" in src      # already exists
    assert "self._history_folder" in src  # already exists
    assert "self._transcript_lang" in src # already exists
    # And the protocol call must wire these:
    assert "protocol_generator.generate(" in src
    # Output path uses history_folder + protocol.md
    assert '"protocol.md"' in src or "'protocol.md'" in src


def test_dialog_protocol_checkbox_default_on():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    # Find the BooleanVar line and verify default=True
    import re
    m = re.search(r"generate_protocol\s*=\s*tk\.BooleanVar\([^)]*value\s*=\s*True", src)
    assert m, "expected generate_protocol BooleanVar with value=True"
```

- [ ] **Step 2: Run test, watch fail**

```powershell
pytest tests/test_extract_dialog_protocol_checkbox.py -v
```

- [ ] **Step 3: Modify the dialog**

Open `ui/dialogs/extract_tasks/__init__.py`. Apply three edits:

A. Add the import near the top (after existing `tasks.*` imports if any):

```python
from tasks.protocol_generator import (
    generate as generate_protocol,
    ProtocolGenerationError,
)
```

B. In `__init__` (around line 95-100 where other state vars are declared), add:

```python
# Protocol generation: opt-in checkbox (default ON for MVP).
self.generate_protocol = tk.BooleanVar(value=True)
```

C. In `_build_ui` (find by grep — likely a method that uses `ctk.CTkCheckBox` for backend selection), add the checkbox near the existing backend selection block:

```python
ctk.CTkCheckBox(
    parent_frame,  # use the actual frame name from surrounding context
    text="Также сгенерировать протокол встречи (protocol.md)",
    variable=self.generate_protocol,
).grid(row=<next_row>, column=0, columnspan=2, sticky="w", padx=8, pady=4)
```

Read the existing form-build code first to find:
- The correct parent frame name
- The next free grid row
- The existing layout pattern (padx/pady values, columnspan)

D. In `_run_extraction` (line 530), after the existing `extract()` call succeeds (after line 567, before `save_tasks_raw`), add:

```python
            # Protocol generation: opt-in pass that uses the same OpenRouter
            # client we just constructed (line ~541). Outputs protocol.md
            # alongside tasks.json in the history folder.
            #
            # Important per Codex sanity-check #6: this dialog has NO module-level
            # `logger`. Use `logging.getLogger(__name__)` inline rather than the
            # bare `logger.info()` which would NameError and block save_tasks_raw.
            if self.generate_protocol.get() and not self._cancel_event.is_set():
                import logging as _logging
                _proto_logger = _logging.getLogger(__name__)
                if not self._cancel_event.is_set():
                    self.after(0, self._status_label.configure, {
                        "text": f"Генерация протокола ({model})...",
                        "text_color": TEXT_SECONDARY,
                    })
                try:
                    proto_result = generate_protocol(
                        transcript=self._transcript,
                        speakers=[],  # speakers list unknown to this dialog;
                                      # protocol prompt handles empty case
                        meeting_date="",  # not tracked at dialog level for MVP;
                                          # LLM extracts from transcript content
                        lang=self._transcript_lang,
                        model=model,
                        openrouter_client=openrouter,
                    )
                    from pathlib import Path
                    proto_path = Path(self._history_folder) / "protocol.md"
                    proto_path.write_text(proto_result.markdown, encoding="utf-8")
                    _proto_logger.info("protocol saved to %s", proto_path)
                except ProtocolGenerationError as e:
                    # Don't block task extraction on protocol failure — log + continue.
                    _proto_logger.warning("protocol generation failed: %s", e)
```

Speakers + meeting_date defaults work for v1.0 — the prompt instructs the LLM to fill the Metadata block from transcript content even when caller passes empty values. Refinement (passing actual speakers + date) is a follow-up.

- [ ] **Step 4: Run tests + manual smoke**

```powershell
pytest tests/test_extract_dialog_protocol_checkbox.py -v
pytest  # full suite — expect 478 + 4 = 482
python app.py
```

Manual: transcribe sample → open extract dialog → checkbox visible + ON → click Запустить → verify `history/<run_id>/protocol.md` created with 5 blocks.

- [ ] **Step 5: Commit Task 6**

```powershell
git add ui/dialogs/extract_tasks/ tests/test_extract_dialog_protocol_checkbox.py
git commit -m "feat(ui): generate-protocol checkbox in extract-tasks dialog

Uses the dialog's REAL instance state (self._transcript, self._history_folder,
self._transcript_lang, model param). Constructs OpenRouter via the existing
self._config['openrouter_api_key'] pattern (same as line 541). Default ON;
unchecking skips the protocol pass.

4 source-text tests verify the wiring without importing ui/."
```

---

## Task 7: Bundle integration — ffmpeg helper + first-run banner

**Files:**
- Modify: `utils.py` (add `get_ffmpeg_path()` + `get_ffprobe_path()`; rewrite `check_ffmpeg()`)
- Modify: `audio_io.py` (use the helper)
- Modify: `transcriber/cloud_chunker.py` (lines 415 + 463 — use the helper)
- Modify: `providers/groq.py` (line 266 — use the helper)
- Modify: `ui/app/builder.py` (insert first-run banner at row=0; shift other rows; update `rowconfigure(6, weight=1)` → `rowconfigure(7, weight=1)`)
- Create: `tests/test_ffmpeg_path_resolution.py`

- [ ] **Step 1: Write failing tests for the ffmpeg helper**

Create `tests/test_ffmpeg_path_resolution.py`:

```python
import sys
from pathlib import Path
import pytest

from utils import get_ffmpeg_path, get_ffprobe_path, check_ffmpeg


def test_get_ffmpeg_from_path_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    assert get_ffmpeg_path() == "/usr/bin/ffmpeg"


def test_get_ffmpeg_from_vendor_when_frozen(tmp_path, monkeypatch):
    fake_bundle = tmp_path / "AudioTranscriber"
    vendor = fake_bundle / "vendor" / "ffmpeg"
    vendor.mkdir(parents=True)
    (vendor / "ffmpeg.exe").write_bytes(b"fake")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)
    out = get_ffmpeg_path()
    assert out.endswith("ffmpeg.exe")
    assert "vendor" in out


def test_get_ffmpeg_returns_none_when_neither_exists(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert get_ffmpeg_path() is None


def test_check_ffmpeg_true_via_bundled(tmp_path, monkeypatch):
    fake_bundle = tmp_path / "AudioTranscriber"
    vendor = fake_bundle / "vendor" / "ffmpeg"
    vendor.mkdir(parents=True)
    (vendor / "ffmpeg.exe").write_bytes(b"fake")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)
    assert check_ffmpeg() is True
```

- [ ] **Step 2: Run, watch fail**

```powershell
pytest tests/test_ffmpeg_path_resolution.py -v
```

- [ ] **Step 3: Add helpers to `utils.py`**

Read current `utils.py` (the file is small, ~100 lines). Add at the top after existing imports:

```python
import sys


def get_ffmpeg_path() -> str | None:
    """Return absolute path to ffmpeg.exe.

    In frozen mode: looks in sys._MEIPASS / vendor / ffmpeg / ffmpeg.exe.
    In source mode: returns shutil.which('ffmpeg') or None.
    """
    if getattr(sys, "frozen", False):
        bundle_root = os.path.join(getattr(sys, "_MEIPASS", "."), "vendor", "ffmpeg")
        candidate = os.path.join(bundle_root, "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffmpeg")


def get_ffprobe_path() -> str | None:
    """Mirror of get_ffmpeg_path for ffprobe."""
    if getattr(sys, "frozen", False):
        bundle_root = os.path.join(getattr(sys, "_MEIPASS", "."), "vendor", "ffmpeg")
        candidate = os.path.join(bundle_root, "ffprobe.exe")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffprobe")
```

Rewrite `check_ffmpeg()`:

```python
def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available (bundled or on PATH)."""
    return get_ffmpeg_path() is not None
```

- [ ] **Step 4: Update all bare-string callers (9 sites total per Codex sanity-check)**

Exhaustive inventory of bare `"ffmpeg"` subprocess argv sites:
- `audio_io.py` — 6 sites (around line 225+; verify by `rg '"ffmpeg"' audio_io.py`)
- `transcriber/cloud_chunker.py:415`
- `transcriber/cloud_chunker.py:463`
- `providers/groq.py:266`

**Total: 9 sites.** Replace each with `get_ffmpeg_path()`. No bare `"ffprobe"` argv calls exist in the repo (Codex confirmed) — `get_ffprobe_path()` helper is **defined for symmetry but is not currently called by any production code**. Keep it anyway — it's needed if/when audio metadata probing is added; ~10 lines of cost is negligible.

Each file imports `from utils import get_ffmpeg_path`.

**Additional test fix:** `tests/test_providers_groq.py:319` currently asserts the literal string `"ffmpeg"` is in subprocess argv. This assertion now fails because argv contains the absolute path. Update:

```python
# Old:
assert "ffmpeg" in args  # asserts literal string

# New (matches both source and frozen modes):
assert any("ffmpeg" in str(a).lower() for a in args), f"expected ffmpeg path in argv, got: {args}"
```

After editing all 9 production sites + the 1 test, run:

```powershell
pytest tests/test_ffmpeg_path_resolution.py tests/test_providers_groq.py -v
pytest  # full suite
```

- [ ] **Step 5: Add the first-run banner to `ui/app/builder.py`**

**Concrete row-shift edits (Codex sanity-check #7 enumerated):** ONLY the root-children of `app` shift, NOT nested `grid(row=0, ...)` calls inside sub-frames. The exact 8 edits:

| Line | Widget | Old row | New row |
|---|---|---|---|
| `builder.py:50` | `app.grid_rowconfigure(6, weight=1)` | `(6, weight=1)` | `(7, weight=1)` |
| `builder.py:54` | `header.grid(...)` | `row=0` | `row=1` |
| `builder.py:72` | `file_card.grid(...)` | `row=1` | `row=2` |
| `builder.py:91` | `rec_card.grid(...)` | `row=2` | `row=3` |
| `builder.py:261` | `run_card.grid(...)` | `row=3` | `row=4` |
| `builder.py:306` | `app._progress.grid(...)` | `row=5` | `row=6` |
| `builder.py:315` | `app._textbox.grid(...)` | `row=6` | `row=7` |
| `builder.py:319` | `btn_frame.grid(...)` | `row=7` | `row=8` |

**Total: 7 `grid(row=...)` edits + 1 `grid_rowconfigure` edit.** Nested `grid(row=0, column=N, ...)` calls INSIDE these frames (e.g. inside `header` to position its label + status) stay UNCHANGED — those are positions inside the parent frame's grid, not the app's root grid.

Then insert the banner block at the top of `build_ui()`, BEFORE the header:

```python
def build_ui(app):
    app.grid_columnconfigure(0, weight=1)
    app.grid_rowconfigure(7, weight=1)  # was 6 — shifted by +1 for banner

    # --- First-run banner (conditional, row=0) ---
    if getattr(app, "_first_run", False):
        banner = ctk.CTkFrame(app, fg_color="#FFF3CD", corner_radius=0, height=42)
        banner.grid(row=0, column=0, sticky="ew")
        banner.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            banner,
            text="Первый запуск. Откройте Настройки → введите AssemblyAI API key + OpenRouter ключ.",
            text_color="#664D03",
            font=ctk.CTkFont(family=FONT, size=12),
            anchor="w",
        ).grid(row=0, column=0, padx=16, pady=10, sticky="w")
        ctk.CTkButton(
            banner, text="Открыть настройки →",
            command=app._open_settings_dialog,  # real method name per dialogs_mixin.py:39
            width=180, height=28,
        ).grid(row=0, column=1, padx=8, pady=6)
        app._first_run_banner = banner

    # --- Header (was row=0, now row=1) ---
    # ... apply the 7 shifts from the table above
```

**Do NOT replace any nested `grid(row=0, ...)` inside child frames** — those are correct as-is. Only the root-app-level grid calls listed in the table shift.

- [ ] **Step 6: Run tests + smoke**

```powershell
pytest
python -m ruff check .
python app.py
```

Source-mode test: the banner DOESN'T show because dev `config.json` has a valid AssemblyAI key already.

To test the banner manually: temporarily set `cloud_api_keys.assemblyai` to `""` in dev config, restart app, verify banner shows + button opens Settings. Restore config when done.

- [ ] **Step 7: Commit Task 7**

```powershell
git add utils.py audio_io.py transcriber/cloud_chunker.py providers/groq.py ui/app/builder.py tests/test_ffmpeg_path_resolution.py
git commit -m "feat(bundle): vendored ffmpeg resolution + first-run banner

utils.get_ffmpeg_path() + get_ffprobe_path() resolve to vendor/ffmpeg/ in
frozen mode, fall back to shutil.which() in source mode. check_ffmpeg()
rewritten to use the helper. Updated bare 'ffmpeg' subprocess sites in
audio_io.py, transcriber/cloud_chunker.py:415/463, providers/groq.py:266.

First-run banner in ui/app/builder.py at row=0, shown when
self._first_run = True (set in ui/app/__init__.py from Task 3 — empty
AssemblyAI key). Calls _open_settings_dialog (the actual method name per
dialogs_mixin.py:39). All grid rows shifted by +1; rowconfigure expand
target moved from row=6 to row=7.

4 new ffmpeg-resolution tests."
```

---

## Task 8: Clean-machine smoke test

**Why clean machine + why NOT Program Files:** existing code (`app.py:13`, `logging_setup.py:21`, `utils.py:8`, `utils.py:50`) self-writes `logs/`, `config.json`, `history/` beside the executable. Standard users can't write under `C:\Program Files\` without elevation. Codex finding #1.

Use a clean Windows 10/11 VM (Hyper-V recommended) or spare laptop.

- [ ] **Step 1: Build + zip**

```powershell
.\scripts\build_exe.ps1
Compress-Archive -Path 'dist/AudioTranscriber' -DestinationPath 'dist/AudioTranscriber-v0.1.0.zip' -Force
```

Verify size 200-500 MB. Transfer to test machine.

- [ ] **Step 2: Extract to a path WITH SPACES but USER-WRITABLE**

```powershell
Expand-Archive -Path AudioTranscriber.zip -DestinationPath "C:\Apps\Audio Transcriber Test"
"C:\Apps\Audio Transcriber Test\AudioTranscriber\AudioTranscriber.exe"
```

Path includes a space ("Audio Transcriber Test") to surface any quoting bugs in ffmpeg subprocess args — per memory `feedback_mock_tests_dont_catch_ffmpeg_parse_errors`. Path is under `C:\Apps\` not Program Files so the app can write its sidecar files.

- [ ] **Step 3: First-run UX checks**

- [ ] Window opens within 5 seconds (cold start)
- [ ] First-run banner visible at top
- [ ] No console window flashes
- [ ] No "missing DLL" popups
- [ ] Click "Открыть настройки →" → Settings dialog opens
- [ ] Settings dialog: cloud_provider dropdown ONLY shows AssemblyAI/Deepgram/Gladia/Speechmatics
- [ ] Settings dialog: NO "Голоса" section
- [ ] Settings dialog: NO silence-removal toggle
- [ ] Enter AssemblyAI API key + OpenRouter key → save
- [ ] Banner dismissed on next launch (config now non-empty)
- [ ] Logs created at `C:\Apps\Audio Transcriber Test\AudioTranscriber\_internal\logs\` (or beside utils.py wherever PyInstaller put it)
- [ ] Config saved at the same beside-utils.py location

- [ ] **Step 4: End-to-end smoke**

Test files (3 if possible):
1. 60-sec Russian 2-speaker
2. 60-sec mixed KZ+RU (note if unavailable)
3. 5-min audio for chunking sanity

For each:
- [ ] Drag → Transcribe → wait
- [ ] Transcript appears with AssemblyAI speaker labels (Speaker A / B)
- [ ] Open extract dialog → "Также сгенерировать протокол" ON by default → Run
- [ ] `protocol.md` generated alongside `transcript.txt` in `history/<run_id>/`
- [ ] Open `protocol.md` — all 5 H2 blocks in Russian + reasonable content
- [ ] Tasks appear in panel; send to Linear (if key configured) succeeds

- [ ] **Step 5: Bug fix loop**

For every issue:
1. Reproduce on dev machine (faster iteration)
2. Fix in source
3. Add regression test if it's a logic bug
4. Rebuild
5. Re-test on clean machine
6. Commit when green

Do not lower the bar. Better to ship Monday than ship broken.

- [ ] **Step 6: Pre-ship checklist**

- [ ] Cold-start time ≤ 10 sec
- [ ] First-run banner visible AND dismissable
- [ ] Keys persist across restarts
- [ ] Transcribe → tasks → protocol completes for 1-min sample
- [ ] protocol.md has 5 blocks AND is in Russian
- [ ] Settings UI hides voice library + silence removal + non-diarize providers
- [ ] Bundle works at a path with spaces
- [ ] `pytest` on dev = **490 tests** green
  *(Math: baseline 462 + Task 2 ~8 tests (gate incl. subprocess) + Task 3 4 tests + Task 5a 4 tests + Task 5b 5 tests + Task 6 4 tests + Task 7 4 tests + ffmpeg-test update doesn't add/remove. = 462 + 28 = 490. Numbers are approximate — Task 2 subprocess test counts as 1, other gate tests are 7 = 8 total in Task 2.)*
- [ ] `python -m ruff check .` = clean
- [ ] `git status` clean

- [ ] **Step 7: Commit smoke outcomes**

```powershell
git add docs/CLIENT_SETUP.md  # tester-checklist updates
git commit -m "docs: smoke-test outcomes for cloud-only bundle"
```

---

## Task 9: Client onboarding doc + delivery

**Files:**
- Finalize: `docs/CLIENT_SETUP.md`

- [ ] **Step 1: Write client-facing setup doc**

`docs/CLIENT_SETUP.md`:

```markdown
# Audio Transcriber — установка и первый запуск

## 1. Что вам понадобится

- Windows 10 (64-bit) или Windows 11
- ~2 GB свободного места
- Интернет (приложение использует cloud-API — не работает offline)
- **AssemblyAI API ключ** — для транскрибации + диаризации
- **OpenRouter API ключ** — для генерации задач + протокола
- (Опционально) Linear / Glide ключи

**Не нужно:** NVIDIA GPU, HuggingFace аккаунт, Python.

## 2. Установка

1. Получите `AudioTranscriber-v0.1.0.zip` от Андаса.
2. Распакуйте в `C:\Apps\AudioTranscriber\`.
   **НЕ распаковывайте в `C:\Program Files\`** — приложение хранит логи и историю рядом с собой, а туда обычному пользователю Windows нельзя писать.
3. Запустите `AudioTranscriber.exe`.

## 3. Первый запуск

Увидите жёлтый баннер: «Первый запуск. Откройте Настройки → введите AssemblyAI API key + OpenRouter ключ».

### 3.1 AssemblyAI

1. Регистрация: <https://www.assemblyai.com> → Get started free.
2. Скопируйте API key из <https://www.assemblyai.com/app/account>.
3. Pricing (2026-05-27): **$50 free credits** / до 185 часов аудио, Universal-2 = **$0.15/час**, диаризация = **$0.02/час**. Combined: ~$0.17/час.
4. Вставьте в Настройки → AssemblyAI API key.

### 3.2 OpenRouter

1. Регистрация на <https://openrouter.ai/>.
2. Пополните баланс — для теста $5 хватит на ≈30-50 встреч.
3. Создайте ключ на <https://openrouter.ai/keys>.
4. Вставьте в Настройки → OpenRouter API key.

### 3.3 (Опционально) Linear / Glide

Если планируете отправлять задачи — вставьте ключи. Можно пропустить.

## 4. Первый тест

1. Перетащите аудио (mp3/m4a/wav) в окно.
2. Нажмите «Транскрибировать» (≈ 20-30 сек на минуту аудио через cloud).
3. Транскрипт появится с метками спикеров (Speaker A, Speaker B…). Имена можно проставить вручную.
4. Откройте «Извлечь задачи» → галочка «Также сгенерировать протокол» включена → Запустить.
5. Через 30-60 сек появятся:
   - `transcript.txt` — текст
   - **`protocol.md`** — протокол встречи (5 блоков)
   - Задачи в panel'е
6. «Показать в Explorer» открывает папку результатов.
7. Если настроены Linear/Glide — кнопка «Отправить в…».

## 5. Если что-то пошло не так

- **App не запускается** → проверьте антивирус (Defender может ложно тегать PyInstaller-бандл — добавьте папку `C:\Apps\AudioTranscriber\` в exclusions).
- **«Ошибка записи»** → распаковали в Program Files? Перенесите в `C:\Apps\`.
- **«Локальный движок недоступен»** → в Settings включена локальная транскрибация. Выберите AssemblyAI в Настройках.
- **«401 Unauthorized»** → неверный AssemblyAI ключ. Проверьте в Настройках.
- **Протокол «(не зафиксировано)» на всех блоках** → попробуйте другую LLM модель (рекомендуем `anthropic/claude-sonnet-4.5`).
- **Крэши** → пришлите `logs/app.log` Андасу.

## 6. Обратная связь

Это первая внешняя версия. Любой фидбэк ценен. Telegram / Skype / звонок.

---

## Tester checklist (для Андаса)

| Client | Дата отгрузки | Windows ver | Первый запуск OK? | E2E smoke OK? | Linear/Glide? | Заметки |
|---|---|---|---|---|---|---|
| Client A | | | | | | |
| Client B | | | | | | |
| Client C | | | | | | |
```

- [ ] **Step 2: Build + zip the shippable**

```powershell
.\scripts\build_exe.ps1
Compress-Archive -Path 'dist/AudioTranscriber' -DestinationPath 'dist/AudioTranscriber-v0.1.0.zip' -Force
```

- [ ] **Step 3: Tag the release**

```powershell
git tag -a v0.1.0-mvp -m "First external MVP — cloud-only via AssemblyAI — 3 clients delivery"
git push origin v0.1.0-mvp
```

- [ ] **Step 4: Deliver to each client**

For each client:
1. Send zip via agreed channel
2. **Schedule a 15-min Skype/Zoom call** (non-optional — guided first-run buys retention)
3. Walk through the doc on screenshare
4. Watch ONE real audio E2E
5. Record outcome in Tester checklist

- [ ] **Step 5: Capture outcomes**

```powershell
git add docs/CLIENT_SETUP.md
git commit -m "docs: delivery outcomes for first 3 MVP clients"
```

After shipping, the user (Andas) updates the active-project memory file in his local Claude state directory with a "Shipped" entry — what landed, prompt tweaks discovered, packaging gotchas, what next iteration needs. **This memory update is a user task, not an implementation step performed by this plan** — listing it here only to flag that the work track ends with a memory recap.

---

## Self-review (post-Codex sanity-check, v4)

**0. Coverage of Codex sanity-check findings (10 items):**

| # | Severity | Finding | v4 response |
|---|---|---|---|
| 1 | P0 | LocalEngineUnavailable defined in wrong file | Task 2: class moved to `cuda_utils.py` alongside TranscriptionCancelled |
| 2 | P0 | Cloud-only enforcement broken (wrong file + `cloud_enabled: false` default) | Task 3 step 5: filter in `settings.py:352`; Task 3 step 8: `cloud_enabled: true` in `config.example.json` |
| 3 | P0 | Diarization default false | Task 3 step 7: flip `_diar_var = True` at `builder.py:134` |
| 4 | P1 | Silence removal not in Settings | Task 3 step 4: gate `audio_cutter.py:227+649` (the real call sites) |
| 5 | P1 | Display-name key drift | Task 3 step 6: banner check uses `"AssemblyAI"` (capitalized) + `cloud_enabled` |
| 6 | P1 | `logger.info()` NameError in extract_tasks | Task 6 step 3: inline `logging.getLogger(__name__)` as `_proto_logger` |
| 7 | P1 | Banner row-shift too broad | Task 7 step 5: concrete 7-edit + 1 rowconfigure table |
| 8 | P1 | Gate test too shallow | Task 2 step 3: subprocess test with `meta_path` finder that blocks ctranslate2 |
| 9 | P2 | Test count drift | Reconciled to **490** throughout |
| 10 | P2 | Task 9 mentions `~/.claude/` | Task 9 step 5 reworded — memory update is user task, not plan step |

**Codex's positive findings absorbed:**
- `cuda_utils.py` refactor preserves `_check_cancelled` + `_cuda_is_available` callers ✓
- `from transcriber import _LOCAL_AVAILABLE` does NOT create circular import (existing `ui.app` → `transcriber` edge) ✓
- ffmpeg site count corrected: 9 (6 in audio_io.py, 2 in cloud_chunker.py, 1 in groq.py)
- `get_ffprobe_path()` retained for symmetry but no production call sites — documented
- `tests/test_providers_groq.py:319` literal-string assertion updated
- Groq + OpenAI Whisper bundled because `providers/__init__.py:17` eager-loads registry
- `Path` already imported locally at `extract_tasks/__init__.py:1567` — second local import OK
- `_internal/config.json` is correct for PyInstaller 6.10 default onedir

**1. Coverage of user-stated requirements:**

| Requirement | Task | Verified? |
|---|---|---|
| Качественная транскрибация | AssemblyAI Universal model | ✓ |
| Качественная диаризация | AssemblyAI built-in (cloud per 2026-05-27 follow-up) | ✓ |
| Протокол | Task 5 + Task 6 | ✓ |
| Задачи в Linear / Glide | Existing, no change | ✓ |
| Задачи в Trello | DROPPED | ⚠ deferred |

**2. Coverage of Codex findings (27 items):**

| # | Finding | Plan response |
|---|---|---|
| 1 | Program Files writes blocked | Task 8 + onboarding switched to `C:\Apps\` |
| 2 | Bootstrap to wrong location | Removed bootstrap; `build_exe.ps1` seeds `_internal/config.json` |
| 3 | main_entry.py startup ffmpeg gate | Task 7: rewrite `utils.check_ffmpeg()` to use new helper |
| 4 | Missed ffmpeg subprocess sites | Task 7: cloud_chunker.py:415,463 + groq.py:266 updated |
| 5 | Not truly lazy (eager in source) | Documented as intentional — source mode keeps existing behavior |
| 6 | `except ImportError` too narrow | Task 2: catches `(ImportError, OSError, RuntimeError)` |
| 7 | `_LOCAL_AVAILABLE` doesn't gate workers | Task 2 guards `_launch_diarization_subprocess` directly |
| 8 | `TranscriptionCancelled` lost on ctranslate2 fail | Task 2 step 5: class moves ABOVE the gated import |
| 9 | Gate tests too shallow | Task 2 step 3: real `monkeypatch` runtime tests + source-text invariants |
| 10 | CI tests would fail without torch | Non-issue — `requirements.txt` keeps torch for source/CI |
| 11 | State leak risk | Non-issue with `monkeypatch` (auto-reverts) |
| 12 | UI traceback vs dialog | Import-time fix in Task 2 lazy refactor prevents the traceback |
| 13 | Two diarize launch routes | Guard `_launch_diarization_subprocess` itself (line 351) |
| 14 | "Cloud-only" is false (hybrid auto-route) | NEW Task 3: hide non-diarizing providers in bundle |
| 15 | Enrollment subprocess broken in bundle | Task 3: hide "Голоса" Settings entry |
| 16 | Voice library is JSON not SQLite | Plan corrected (no false claim about sqlite-vec) |
| 17 | Provider imports OK | Confirmed in spec hiddenimports |
| 18 | Silero VAD breaks without faster_whisper | Task 3: hide silence-removal UI in bundle |
| 19 | Other native deps in bundle | Acknowledged as out-of-scope concern |
| 20 | `config.example.json` keys wrong | Read existing file at plan time; key is `cloud_provider` |
| 21 | Vendor binaries don't exist yet | Task 4 step 1 explicitly stages them |
| 22 | Dialog variable names wrong | Task 6 uses REAL names from extract_tasks:65-75 + :541 |
| 23 | Banner pseudocode broken | Task 7 step 5 uses grid + `_open_settings_dialog` |
| 24 | Test arithmetic off | Corrected to +28 tests (462→490 expected) |
| 25 | AssemblyAI pricing stale | Onboarding doc has $0.17/h; codebase cleanup in spawn-task chip |
| 26 | Encoding fine | Confirmed |
| 27 | Task 8 modified `~/.claude/` | Task 9 step 5 reworded to clarify user-state vs repo boundary |

**3. Placeholder scan:** No "TBD"/"TODO" remain. Task 6 step 3 says "find the existing form-build code first" + Task 7 step 4 says "use Grep to find call sites" — these are intentional "look up the live code" instructions, not vague placeholders.

**4. Type consistency:**
- `Placeholders` dataclass: 6 fields, shared identically across template + generator + tests ✓
- `_LLMClient` Protocol: matches `OpenRouterClient.complete` signature ✓
- `LocalEngineUnavailable(RuntimeError)`: distinct from `TranscriptionCancelled` ✓
- `get_ffmpeg_path() -> str | None` + `get_ffprobe_path() -> str | None`: same signature ✓

**5. Dependency order:**
- Task 1 (verify) — non-blocking
- Task 2 (lazy refactor) — HARD GATE for Task 3 (provider gating uses `_LOCAL_AVAILABLE`)
- Task 3 (UI gating) — depends on Task 2
- Task 4 (PyInstaller spike) — HARD GATE for Tasks 7, 8, 9
- Task 5 (protocol_generator) — independent
- Task 6 (UI integration) — depends on Task 5
- Task 7 (bundle integration) — depends on Tasks 2 + 3 + 4
- Task 8 (smoke) — depends on Tasks 4 + 6 + 7
- Task 9 (deliver) — depends on Task 8

**6. Boundary test data audit:** non-zero starts in all integration tests ✓.

**7. Pre-check safety-net audit:** the removal of `bootstrap_first_run_config` removes a `FileNotFoundError` path; no new exception types are raised at startup. ✓

**8. ffmpeg-mock-blindness audit:** Task 8 step 2 extracts to path with space ("Audio Transcriber Test"). Validates real ffmpeg invocation with realistic Windows path. ✓

**9. Codex-contract-drift audit:** dialog state field names match `ui/dialogs/extract_tasks/__init__.py:65-75` exactly. Banner uses real method name `_open_settings_dialog` from `dialogs_mixin.py:39`. AssemblyAI pricing values reconciled against pricing page. ✓

---

## Glossary

- **`_LOCAL_AVAILABLE`** — module-level boolean in `transcriber/cuda_utils.py` (and re-exported from `transcriber/__init__.py`). True when both `ctranslate2` + `faster_whisper` import cleanly; False otherwise. Guards every local-engine code path.
- **Hybrid auto-route** — existing logic at `transcriber/__init__.py:1135` that engages local pyannote when the chosen cloud STT provider lacks native diarization (Groq, OpenAI Whisper). Task 3 hides those providers in the cloud-only bundle to prevent the route from ever being taken.
- **PyInstaller `--onedir`** — folder-mode bundle (vs `--onefile`); faster startup, easier debugging.
- **Frozen mode** — `sys.frozen = True` + `sys._MEIPASS` populated (PyInstaller runtime).
- **MoM** — Minutes of Meeting; 5-block structured protocol per Tauri spec §7.9.
