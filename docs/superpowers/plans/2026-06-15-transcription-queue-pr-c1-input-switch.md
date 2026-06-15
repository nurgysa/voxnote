# Transcription Queue PR-C1 — Input Switch (UI wiring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the merged `ProcessingQueue` the App's input path — record-stop and «Выбрать файл» enqueue instead of running a synchronous transcription, the worker writes `transcript.md` to the vault + archives audio + nudges Hermes, and the App shows an aggregate queue indicator.

**Architecture:** Replace `transcription_mixin.py` (the sync run-loop + UI-side Hermes emit + history write) with a small `queue_mixin.py`. `App.__init__` builds a `DirectoryStore` + `ProcessingQueue` (after `build_ui`), starts it, stops it on window close, and reacts to `on_change` (marshalled to the Tk thread via `after(0, …)`). The «Транскрибировать» button is removed; the main textbox becomes a viewer (loaded from «Встречи»). Per-meeting status + history view land in **PR-C2**; the project selector + Settings path fields land in **PR-C1b**.

**Tech Stack:** CustomTkinter, Python stdlib, `pytest`, `ruff`. UI tests are **source-slice** (read the module text and assert on it) — importing `ui.app` pulls `sounddevice`/PortAudio which crashes Linux CI; this is the established pattern (see the now-removed `test_ui_hermes_emit.py`).

**Source of truth:** `docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md` §11. Queue API: `processing/worker.py` `ProcessingQueue(*, meetings_dir, config_loader, resolve_project, queue_path=None, on_change=None)` with `.start()/.stop()/.enqueue(audio_path, options)/.retry(id)/.snapshot()`.

**Conventions:** `py -3 -m pytest -q` + `py -3 -m ruff check .` green before commit. Russian user-facing strings, English code/comments. `encoding="utf-8"` on text I/O. Narrow excepts (this PR adds ZERO broad-except; it REMOVES the 3 in `transcription_mixin.py`). Commit lowercase-scoped, ending with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## Scope

**In PR-C1:** queue lifecycle in App + `queue_mixin` + enqueue (record/pick) + remove «Транскрибировать»/sync loop + aggregate indicator strip. **Project is `None`** for now.

**NOT in PR-C1:** project selector + `last_project_id` (PR-C1b), `meetings_dir`/`sources_dir` Settings fields (PR-C1b — until then the queue uses `get_meetings_dir()`'s default and skips archiving when `sources_dir` is unset, both already handled by the worker), the «Встречи» queue+history view rework (PR-C2), inbox poll (PR-C3).

**Functional after PR-C1:** record/pick → enqueue → worker writes `transcript.md` into `get_meetings_dir()` (default `%USERPROFILE%/Documents/VoxNote/meetings`) → indicator shows progress. The (unchanged) «Встречи» dialog still lists the resulting folders.

---

## Coupling note (read before starting)

Removing `transcription_mixin.py` + the `_btn_transcribe` widget breaks `__init__.py` (bases/import), `builder.py`, `recorder_mixin.py`, `settings_mixin.py`, `dialogs_mixin.py`, and two test files **all at once**. This is therefore **ONE atomic task** with ordered steps and a **single commit** (mirrors PR-B2's trio). Do NOT run the full suite until every step is done; run targeted checks as noted, then the green gate (Step 13) before the commit (Step 14).

---

## File structure

| File | Change |
|---|---|
| `ui/app/queue_mixin.py` | **NEW** — `QueueMixin`: `_build_options`, `_enqueue`, `_on_queue_changed`, `_refresh_queue_indicator`, `_on_app_close`. No own thread. |
| `ui/app/__init__.py` | swap `TranscriptionMixin`→`QueueMixin`; build `DirectoryStore`+`ProcessingQueue` after `build_ui`; `WM_DELETE_WINDOW`→`_on_app_close`; drop now-dead `_transcriber`/`_is_running`/`_cancel_event`/`import threading`/TYPE_CHECKING `Transcriber`. |
| `ui/app/builder.py` | remove the `_btn_transcribe` widget; add the `_lbl_queue` indicator strip (row 5). |
| `ui/app/recorder_mixin.py` | `_stop_recording` → `self._enqueue(path, "record")` (drop the `_btn_transcribe` enable + docstring mention). |
| `ui/app/settings_mixin.py` | `_select_file` → `self._enqueue(path, "pick")` (drop the `_btn_transcribe` enable). |
| `ui/app/dialogs_mixin.py` | `_load_history_into_main` → drop the `_btn_transcribe` enable line + docstring mention (keep textbox/`_audio_path`/extract wiring). |
| `ui/app/transcription_mixin.py` | **DELETE**. |
| `tests/test_ui_hermes_emit.py` | **DELETE** (source-slices the deleted file; Hermes emit moved to the worker, covered by `test_processing_worker`). |
| `tests/test_transcription_mixin_delete_source.py` | **DELETE** (source-slices the deleted file; delete-after-transcription is superseded by the worker's archive-move-for-record, covered by `test_processing_worker`). |
| `tests/test_broad_except_ratchet.py` | remove the `"ui/app/transcription_mixin.py": 3` BASELINE entry. |
| `tests/test_ui_queue_wiring.py` | **NEW** — source-slice wiring assertions. |

---

## Task 1: Wire the processing queue as the input path (atomic)

**Files:** all of the above.

- [ ] **Step 1: Create `ui/app/queue_mixin.py`**

```python
"""Processing-queue integration for the main App window.

Replaces the old synchronous transcription run-loop (transcription_mixin,
removed in PR-C1). Record-stop and «Выбрать файл» now ENQUEUE onto the serial
ProcessingQueue (processing/worker.py): the worker transcribes + diarizes,
writes transcript.md into the Obsidian vault, archives audio to Drive sources,
and fires a best-effort Hermes nudge. The App reacts to queue changes via the
injected on_change callback (marshalled to the Tk thread with after(0, ...)) and
shows an aggregate indicator strip. Per-meeting status + history land in the
«Встречи» dialog (PR-C2); the project selector lands in PR-C1b.

Mixin contract: relies on App providing the option Vars (_cloud_provider_var,
_lang_var, _diar_var, _spk_count_var, _denoise_var), _cloud_api_keys, _config,
the widgets _lbl_queue / _lbl_status, and self._queue (ProcessingQueue, built in
App.__init__). NO worker thread of its own — ProcessingQueue owns that.
"""
from __future__ import annotations

import os
from tkinter import messagebox

from processing.model import StageStatus
from theme import GREEN, RED, TEXT_SECONDARY

from .constants import LANGUAGES, SPEAKER_COUNTS


class QueueMixin:
    """Enqueue + reactive indicator over the App's ProcessingQueue."""

    def _build_options(self, source: str) -> dict:
        """Gather the current run options from the App's setting Vars into the
        dict the worker consumes. project_id is None in PR-C1 (the project
        selector lands in PR-C1b)."""
        saved_terms = self._config.get("hotwords", [])
        num_speakers, min_speakers, max_speakers = SPEAKER_COUNTS.get(
            self._spk_count_var.get(), (None, None, None),
        )
        return {
            "provider": self._cloud_provider_var.get(),
            "language": LANGUAGES.get(self._lang_var.get()),
            "diarize": bool(self._diar_var.get()),
            "hotwords": ", ".join(saved_terms) if saved_terms else None,
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
            "denoise": bool(self._denoise_var.get()),
            "project_id": None,
            "source": source,
        }

    def _enqueue(self, audio_path: str, source: str) -> None:
        """Add an audio file to the processing queue. Pre-checks the cloud key
        so a missing key is caught here (clear dialog) rather than surfacing as
        a queue error item."""
        provider = self._cloud_provider_var.get()
        if not (self._cloud_api_keys.get(provider) or "").strip():
            messagebox.showwarning(
                "Нужен API-ключ",
                f"API-ключ для {provider} не задан.\n\n"
                f"Открой Настройки → Транскрибация (cloud API) и вставь ключ.",
            )
            return
        self._queue.enqueue(audio_path, self._build_options(source))
        self._lbl_status.configure(
            text=f"Добавлено в очередь: {os.path.basename(audio_path)}",
            text_color=GREEN,
        )
        self._refresh_queue_indicator()

    def _on_queue_changed(self) -> None:
        """ProcessingQueue on_change target. Already marshalled to the Tk thread
        by the App's after(0, ...) wrapper, so touching widgets here is safe."""
        self._refresh_queue_indicator()

    def _refresh_queue_indicator(self) -> None:
        """Repaint the aggregate queue strip from a fresh snapshot."""
        items = self._queue.snapshot()
        in_work = sum(
            1 for it in items
            if it.status in (StageStatus.PENDING, StageStatus.RUNNING)
        )
        errors = sum(1 for it in items if it.status == StageStatus.ERROR)
        self._lbl_queue.configure(
            text=f"● Очередь: {in_work} в работе · {errors} ошибок",
            text_color=RED if errors else TEXT_SECONDARY,
        )

    def _on_app_close(self) -> None:
        """Stop the queue's daemon thread, then close the window."""
        self._queue.stop()
        self.destroy()
```

- [ ] **Step 2: `ui/app/__init__.py` — swap the mixin import**

Replace:

```python
from .transcription_mixin import TranscriptionMixin
```

with:

```python
from .queue_mixin import QueueMixin
```

- [ ] **Step 3: `ui/app/__init__.py` — drop the now-dead `import threading`**

Replace:

```python
import os
import threading
import tkinter as tk
```

with:

```python
import os
import tkinter as tk
```

- [ ] **Step 4: `ui/app/__init__.py` — drop the TYPE_CHECKING `Transcriber` import**

Replace:

```python
if TYPE_CHECKING:
    from audio_cutter import AudioCutter
    from transcriber import Transcriber
    from ui.dialogs.settings import SettingsDialog
```

with:

```python
if TYPE_CHECKING:
    from audio_cutter import AudioCutter
    from ui.dialogs.settings import SettingsDialog
```

- [ ] **Step 5: `ui/app/__init__.py` — add queue imports + swap the class base**

Replace:

```python
from recorder import Recorder
from theme import BG
from utils import get_app_icon_path, load_config, save_config
```

with:

```python
from directory.store import DirectoryStore
from processing.worker import ProcessingQueue
from recorder import Recorder
from theme import BG
from utils import get_app_icon_path, get_meetings_dir, load_config, save_config
```

Then replace the class header:

```python
class App(
    DialogsMixin,
    RecorderMixin,
    SaveMixin,
    SettingsMixin,
    TranscriptionMixin,
    ctk.CTk,
):
```

with:

```python
class App(
    DialogsMixin,
    RecorderMixin,
    SaveMixin,
    SettingsMixin,
    QueueMixin,
    ctk.CTk,
):
```

- [ ] **Step 6: `ui/app/__init__.py` — drop dead state attrs (`_transcriber`, `_is_running`, `_cancel_event`)**

Replace:

```python
        self._audio_path: str | None = None
        self._transcriber: Transcriber | None = None
        self._recorder = Recorder()
        self._is_running = False
        self._rec_timer_id: str | None = None
        self._config = load_config()
```

with:

```python
        self._audio_path: str | None = None
        self._recorder = Recorder()
        self._rec_timer_id: str | None = None
        self._config = load_config()
```

Then replace:

```python
        # Cancel signal for the worker thread. Worker checks this between
        # segments and around the diarization subprocess; setting it
        # interrupts the run within ~250 ms.
        self._cancel_event = threading.Event()
        # Path to the most recent successful transcription's history folder.
        # Populated in _on_complete; consumed by _open_extract_tasks_dialog.
        self._last_history_folder: str | None = None
```

with:

```python
        # Path to the most recently loaded meeting folder (set when a meeting
        # is opened from «Встречи» via _load_history_into_main); consumed by
        # _open_extract_tasks_dialog.
        self._last_history_folder: str | None = None
```

- [ ] **Step 7: `ui/app/__init__.py` — construct + start the queue after `build_ui`**

Replace:

```python
        build_ui(self)

        # First-launch meetings migration check. If meetings_dir isn't
```

with:

```python
        build_ui(self)

        # Processing queue (PR-C1): record-stop / «Выбрать файл» enqueue here;
        # the serial worker transcribes → vault transcript.md → Drive sources →
        # Hermes nudge. on_change is marshalled to the Tk thread via after(0).
        self._dir_store = DirectoryStore()
        self._dir_store.load()
        self._queue = ProcessingQueue(
            meetings_dir=get_meetings_dir(),
            config_loader=load_config,
            resolve_project=lambda pid: (
                self._dir_store.get_project(pid) if pid else None
            ),
            on_change=lambda: self.after(0, self._on_queue_changed),
        )
        self._queue.start()
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self._refresh_queue_indicator()

        # First-launch meetings migration check. If meetings_dir isn't
```

- [ ] **Step 8: `ui/app/builder.py` — remove «Транскрибировать», add the indicator strip**

Replace:

```python
    app._btn_transcribe = primary_button(
        file_card, text="Транскрибировать",
        command=app._start_transcription, width=190, state="disabled",
    )
    app._btn_transcribe.grid(row=0, column=2, padx=16, pady=14)

    # --- Recorder card (row=3 — was row=2 before banner) ---
```

with:

```python
    # --- Recorder card (row=3 — was row=2 before banner) ---
```

Then replace:

```python
    app._btn_settings = tonal_button(
        run_card, text="Настройки",
        command=app._open_settings_dialog, width=140,
    )
    app._btn_settings.grid(row=0, column=3, padx=(0, 16), pady=14, sticky="e")

    # --- Progress bar ---
```

with:

```python
    app._btn_settings = tonal_button(
        run_card, text="Настройки",
        command=app._open_settings_dialog, width=140,
    )
    app._btn_settings.grid(row=0, column=3, padx=(0, 16), pady=14, sticky="e")

    # --- Queue indicator strip (row=5) ---
    # Aggregate status of the serial processing queue, repainted reactively by
    # QueueMixin._refresh_queue_indicator (record/pick enqueue replaced the old
    # «Транскрибировать» button). Per-meeting status lives in «Встречи» (PR-C2).
    app._lbl_queue = label(
        app, text="● Очередь: 0 в работе · 0 ошибок", anchor="w",
    )
    app._lbl_queue.grid(row=5, column=0, padx=24, pady=(2, 0), sticky="w")

    # --- Progress bar ---
```

Note: `primary_button` becomes unused in `builder.py` after removing `_btn_transcribe`. Remove it from the import to keep ruff clean — replace:

```python
from ui.widgets import (
    card,
    label,
    option_menu,
    primary_button,
    tonal_button,
)
```

with:

```python
from ui.widgets import (
    card,
    label,
    option_menu,
    tonal_button,
)
```

- [ ] **Step 9: `ui/app/recorder_mixin.py` — enqueue on stop**

Replace the docstring contract line:

```python
``self._lbl_file``, ``self._btn_transcribe``, ``self._rec_level``,
```

with:

```python
``self._lbl_file``, ``self._rec_level``,
```

Then replace the `_stop_recording` tail:

```python
        if path and os.path.exists(path):
            # Auto-load the recording for transcription
            self._audio_path = path
            self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
            self._btn_transcribe.configure(state="normal")
            elapsed = self._lbl_rec_time.cget("text")
            self._lbl_rec_time.configure(text=elapsed, text_color=GREEN)
            self._lbl_status.configure(
                text=f"Запись сохранена: {os.path.basename(path)}", text_color=GREEN,
            )
```

with:

```python
        if path and os.path.exists(path):
            # Keep _audio_path so the Audio Cutter can pre-load the recording;
            # the queue is the transcription path now (no «Транскрибировать»).
            self._audio_path = path
            self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
            elapsed = self._lbl_rec_time.cget("text")
            self._lbl_rec_time.configure(text=elapsed, text_color=GREEN)
            self._enqueue(path, "record")
```

- [ ] **Step 10: `ui/app/settings_mixin.py` — enqueue on file pick**

Replace:

```python
        self._audio_path = path
        self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
        self._btn_transcribe.configure(state="normal")
```

with:

```python
        # Keep _audio_path for the Audio Cutter; the queue is the transcription
        # path now (no «Транскрибировать»). «Выбрать файл» copies the original
        # to Drive sources (the worker leaves the user's file in place).
        self._audio_path = path
        self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
        self._enqueue(path, "pick")
```

- [ ] **Step 11: `ui/app/dialogs_mixin.py` — drop the transcribe-button enable**

Replace the docstring contract fragment:

```python
``self._btn_save``, ``self._btn_copy``, ``self._btn_transcribe``,
``self._btn_extract_tasks``, ``self._lang_var``, ``self._audio_path``,
```

with:

```python
``self._btn_save``, ``self._btn_copy``,
``self._btn_extract_tasks``, ``self._lang_var``, ``self._audio_path``,
```

Then replace the `_load_history_into_main` tail:

```python
        if audio_path and os.path.isfile(audio_path):
            self._audio_path = audio_path
            self._lbl_file.configure(
                text=os.path.basename(audio_path), text_color=TEXT_PRIMARY,
            )
            self._btn_transcribe.configure(state="normal")
        self._lbl_status.configure(
            text="Загружено из истории", text_color=TEXT_SECONDARY,
        )
```

with:

```python
        if audio_path and os.path.isfile(audio_path):
            self._audio_path = audio_path
            self._lbl_file.configure(
                text=os.path.basename(audio_path), text_color=TEXT_PRIMARY,
            )
        self._lbl_status.configure(
            text="Загружено из истории", text_color=TEXT_SECONDARY,
        )
```

- [ ] **Step 12: Delete the dead module + its source-slice tests; update the ratchet**

```bash
git rm ui/app/transcription_mixin.py tests/test_ui_hermes_emit.py tests/test_transcription_mixin_delete_source.py
```

Then in `tests/test_broad_except_ratchet.py`, remove this line from the `BASELINE` dict:

```python
    "ui/app/transcription_mixin.py": 3,            # worker boundary + crash dump + hermes daemon
```

(The Hermes emit + delete-after-transcription behaviors moved into `processing/worker.py` and are covered by `tests/test_processing_worker.py`'s nudge-delivered/failed and archive-move-for-record tests. `queue_mixin.py` has zero broad-except, so no new BASELINE entry.)

- [ ] **Step 13a: Create `tests/test_ui_queue_wiring.py`**

```python
"""Source-slice wiring tests for the PR-C1 processing-queue integration.

No ui.app import — sounddevice/PortAudio would break Linux CI. Pattern matches
the (removed) test_ui_hermes_emit.py: read the module text and assert on it.
"""
from __future__ import annotations

from pathlib import Path

_INIT = Path("ui/app/__init__.py").read_text(encoding="utf-8")
_QUEUE = Path("ui/app/queue_mixin.py").read_text(encoding="utf-8")
_BUILDER = Path("ui/app/builder.py").read_text(encoding="utf-8")
_RECORDER = Path("ui/app/recorder_mixin.py").read_text(encoding="utf-8")
_SETTINGS = Path("ui/app/settings_mixin.py").read_text(encoding="utf-8")
_DIALOGS = Path("ui/app/dialogs_mixin.py").read_text(encoding="utf-8")


def test_transcription_mixin_removed():
    assert not Path("ui/app/transcription_mixin.py").exists()


def test_app_uses_queue_mixin_not_transcription_mixin():
    assert "from .queue_mixin import QueueMixin" in _INIT
    assert "QueueMixin" in _INIT
    assert "TranscriptionMixin" not in _INIT


def test_app_constructs_and_starts_queue():
    assert "ProcessingQueue(" in _INIT
    assert "self._queue.start()" in _INIT
    assert "WM_DELETE_WINDOW" in _INIT


def test_queue_mixin_has_enqueue_api():
    for name in (
        "_build_options", "_enqueue", "_on_queue_changed",
        "_refresh_queue_indicator", "_on_app_close",
    ):
        assert f"def {name}" in _QUEUE
    assert "self._queue.enqueue(" in _QUEUE


def test_transcribe_button_removed_indicator_added():
    assert "Транскрибировать" not in _BUILDER
    assert "_btn_transcribe" not in _BUILDER
    assert "_lbl_queue" in _BUILDER


def test_record_and_pick_enqueue():
    assert '_enqueue(path, "record")' in _RECORDER
    assert "_btn_transcribe" not in _RECORDER
    assert '_enqueue(path, "pick")' in _SETTINGS
    assert "_btn_transcribe" not in _SETTINGS


def test_dialogs_mixin_has_no_transcribe_button():
    assert "_btn_transcribe" not in _DIALOGS
```

- [ ] **Step 13b: Green gate — run the new wiring tests, the ratchet, the full suite, and ruff**

Run: `py -3 -m pytest tests/test_ui_queue_wiring.py tests/test_broad_except_ratchet.py -q`
Expected: PASS (7 wiring + 1 ratchet).

Run: `py -3 -m pytest -q`
Expected: PASS, no failures. (The two deleted tests are gone; nothing else imports `transcription_mixin`/`_btn_transcribe`.) If the PowerShell pipe swallows the `-q` summary, re-run with `--junitxml=junit.xml`, read totals, then delete `junit.xml`.

Run: `py -3 -m ruff check .`
Expected: clean. Watch for unused imports — `primary_button` (builder), `threading` + TYPE_CHECKING `Transcriber` (`__init__.py`) were removed in Steps 8/3/4; if ruff still flags any leftover (e.g. an `os`/`GREEN` that became unused in a touched file), remove it.

- [ ] **Step 14: Commit**

```bash
git add ui/app/queue_mixin.py ui/app/__init__.py ui/app/builder.py \
        ui/app/recorder_mixin.py ui/app/settings_mixin.py ui/app/dialogs_mixin.py \
        tests/test_broad_except_ratchet.py tests/test_ui_queue_wiring.py
git commit -F- <<'EOF'
feat(ui): wire processing queue as the input path (PR-C1)

Replace the synchronous «Транскрибировать» run-loop with the serial
ProcessingQueue: record-stop and «Выбрать файл» enqueue; the worker
transcribes → writes transcript.md to the vault → archives audio to Drive
sources → nudges Hermes. App constructs/starts the queue, stops it on
window close, and shows an aggregate indicator strip; queue changes marshal
to the Tk thread via after(0, ...).

- new ui/app/queue_mixin.py (_build_options/_enqueue/_on_queue_changed/
  _refresh_queue_indicator/_on_app_close); App swaps TranscriptionMixin →
  QueueMixin and builds DirectoryStore + ProcessingQueue.
- builder: drop «Транскрибировать», add the queue indicator strip.
- recorder/_select_file enqueue (source record/pick); dialogs_mixin drops
  the transcribe-button enable; the main textbox stays a viewer.
- delete transcription_mixin.py + its 2 source-slice tests (Hermes emit &
  delete-after now live in the worker, covered by test_processing_worker);
  drop its broad-except ratchet entry; add test_ui_queue_wiring.py.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Self-review

**Spec coverage (§11):**
- Enqueue: record-stop auto-enqueue (Step 9) + «Выбрать файл» → queue (Step 10). ✓ (inbox poll → PR-C3)
- Remove «Транскрибировать» + the synchronous run-loop → Steps 8 (button) + 12 (delete `transcription_mixin.py`). ✓
- Indicator strip `● Очередь: N в работе · K ошибок`, reactive (`on_change → after(0,…)`) → Steps 1 (`_refresh_queue_indicator`/`_on_queue_changed`) + 7 (`on_change` lambda) + 8 (widget). ✓
- Project selector + cost hint → deferred (PR-C1b / later), stated in Scope. ✓
- «Встречи» queue+history view → deferred to PR-C2; the existing dialog keeps working over the same folders. ✓
- Worker option keys match `run_transcribe`/`_process_item`: `provider`, `language`, `diarize`, `hotwords`, `num_speakers`/`min_speakers`/`max_speakers`, `denoise`, `project_id`, `source` (Step 1 `_build_options`). ✓

**Placeholder scan:** none — every step shows full content or an exact replacement; commands have expected output. ✓

**Consistency / correctness:**
- Lifecycle: `App.__init__` builds `_dir_store` + `_queue` after `build_ui` (so `_lbl_queue` exists for `_refresh_queue_indicator`), starts it, binds `WM_DELETE_WINDOW` → `_on_app_close` (`queue.stop()` + `destroy()`). ✓
- Threading: `on_change` (fired from the worker daemon) wraps `self.after(0, self._on_queue_changed)` — the established cross-thread→Tk pattern in this codebase. `_on_queue_changed`/`_refresh_queue_indicator`/`_enqueue` only touch widgets on the Tk thread. ✓
- Dead-code removal is complete and ruff-driven: `_transcriber`/`_is_running`/`_cancel_event` (Step 6) → `import threading` (Step 3) + TYPE_CHECKING `Transcriber` (Step 4); `primary_button` (Step 8). `_last_history_folder`/`_audio_path` are KEPT (still used by `dialogs_mixin` + the cutter). ✓
- Removed-symbol fallout: the only non-doc consumers of `transcription_mixin`/`_btn_transcribe` are the files edited here + the two deleted tests; `cli/app.py`'s own `_emit_hermes_event`, the `voxnote.spec` comment, and `settings_builder.py`'s comment are incidental and unaffected. ✓
- Test strings match the code exactly: `_enqueue(path, "record")` (Step 9) / `_enqueue(path, "pick")` (Step 10) / `_lbl_queue` (Step 8) / `def _build_options` etc. (Step 1) / no `TranscriptionMixin` in `__init__` (Steps 2/5) / no `_btn_transcribe` in builder/recorder/settings/dialogs (Steps 8/9/10/11). ✓
- Ratchet: removing `transcription_mixin.py` (3 broad) + its BASELINE line; `queue_mixin.py` adds 0 broad — ratchet stays truthful. ✓

---

## Finish

After Task 1: **superpowers:finishing-a-development-branch** → push `feat/transcription-queue-pr-c1` + open a PR (user reviews/merges). PR body: `## Summary` (queue is the input path; «Транскрибировать» gone; indicator) + `## Test plan` (wiring source-slice tests; ratchet updated; full suite + ruff green; manual: record/pick → meeting folder appears + indicator updates).

Then **PR-C1b** (project selector + `last_project_id` + `meetings_dir`/`sources_dir` Settings fields), then **PR-C2** («Встречи» queue+history view), then **PR-C3** (inbox poll).
