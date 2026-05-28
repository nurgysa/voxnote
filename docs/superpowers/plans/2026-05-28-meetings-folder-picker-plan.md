# Meetings folder picker + migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `<bundle>/_internal/history/` meetings path with a user-configurable folder, sane default at `%USERPROFILE%\Documents\AudioTranscriber\meetings\`, and one-time migration of legacy entries.

**Architecture:** Pure-Python migration logic lives in `meetings_migration.py` (fully unit-testable, no Tk). UI shell lives in `ui/dialogs/migration.py` and spawns daemon threads that marshal progress via `parent.after(0, ...)`. The `utils.get_meetings_dir()` resolver reads `config["meetings_dir"]` at-call-time with a 3-level fallback (config → default → legacy). The Settings dialog gets a new section in Tab 1 «Транскрипция»; the main-window button «История» renames to «Митинги»; and `ui/dialogs/history.py` becomes `ui/dialogs/meetings.py`.

**Tech Stack:** Python 3.12, `shutil.move` (handles same-volume rename + cross-volume copy), `threading.Thread(daemon=True)` + `parent.after(0, ...)` for non-blocking migration, `tkinter.filedialog.askdirectory` for the Win32-native folder picker, CustomTkinter for dialog widgets. Tests use `tempfile.TemporaryDirectory` for real I/O on isolated paths (per `feedback_ui_app_import_breaks_linux_ci.md`).

**Reference spec:** [docs/superpowers/specs/2026-05-28-meetings-folder-picker-design.md](../specs/2026-05-28-meetings-folder-picker-design.md)

---

## File structure

| Path | Action | Responsibility |
|---|---|---|
| `meetings_migration.py` | Create | `count_meetings(path) -> int`, `detect_old_locations(probe_paths=None) -> list[(str, int)]`, `migrate_meetings(src, dst, on_progress, cancel_event) -> dict`. Pure Python, no Tk. |
| `utils.py` | Modify | Replace `_HISTORY_DIR` constant + `_ensure_history_dir` with `get_meetings_dir()` resolver. Update `create_history_entry`/`list_history_entries`/`delete_history_entry` to call resolver. Add `_DEFAULT_MEETINGS_DIR`. |
| `config.example.json` | Modify | Add `"meetings_dir": ""` key. |
| `ui/dialogs/meetings.py` | **Rename from** `ui/dialogs/history.py` | Rename via `git mv`. Inside: classes `HistoryDialog`→`MeetingsDialog`, `HistoryViewerDialog`→`MeetingViewerDialog`. Title «История транскрипций»→«Митинги». Label «Записей: N»→«Митингов: N». Empty-state «Нет транскрипций»→«Нет митингов». |
| `ui/dialogs/migration.py` | Create | `MigrationPromptDialog` (Перенести/Оставить/Спросить позже) + `MigrationProgressDialog` (progress bar, cancel). |
| `ui/dialogs/settings.py` | Modify | New section card «Митинги» in Tab 1 «Транскрипция» at row=4. Path entry (readonly) + «📁 Выбрать» + «↻ Default» + stats. Shifts existing row=4 (Словари) to row=5. |
| `ui/app/dialogs_mixin.py` | Modify | `_open_history_dialog` → `_open_meetings_dialog`. Import update. |
| `ui/app/builder.py` | Modify | Button text «История» → «Митинги». Callback name update. |
| `ui/app/__init__.py` | Modify | After `load_config()`, schedule first-launch migration check via `self.after(500, ...)`. |
| `gdrive/backup.py` | Modify | Callers of `run_backup(history_dir=...)` pass `get_meetings_dir()` explicitly. |
| `tests/test_meetings_migration.py` | Create | 9 unit tests covering migrate / detect / count. |
| `tests/test_utils_meetings_resolver.py` | Create | AST + source-text checks for `get_meetings_dir`. |
| `tests/test_meetings_dialog_rename.py` | Create | Source-text checks: no «История транскрипций» in code, «Митинги» in builder.py, etc. |
| `tests/test_settings_dialog_meetings_section.py` | Create | Source-text: `_section_card(... "Митинги" ...)` + `askdirectory` reference. |

**Out of scope** (deferred): merged-view of multiple folders, auto-migration without consent, multi-folder support, per-meeting overrides, cloud-folder sync, rollback/undo, disk-space pre-check.

---

## Task 1: `meetings_migration.py` core (pure logic + 9 tests)

**Files:**
- Create: `meetings_migration.py`
- Create: `tests/test_meetings_migration.py`

This task delivers the entire pure-logic module. Tests come first (TDD), then implementation. Single commit at the end because the module is conceptually one unit.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meetings_migration.py`:

```python
"""Unit tests for meetings_migration — pure Python, real I/O on tempdirs.

No Tk imports, so this file runs cleanly on Linux CI (unlike anything
that touches ui.app — see feedback_ui_app_import_breaks_linux_ci).
"""
from __future__ import annotations

import os
import tempfile
import threading

from meetings_migration import (
    count_meetings,
    detect_old_locations,
    migrate_meetings,
)


def _make_meeting(parent: str, name: str, files: dict[str, bytes]) -> str:
    """Create a fake meeting folder with the given files inside."""
    folder = os.path.join(parent, name)
    os.makedirs(folder)
    for fname, content in files.items():
        with open(os.path.join(folder, fname), "wb") as f:
            f.write(content)
    return folder


# ── migrate_meetings ───────────────────────────────────────────────────


def test_migrate_empty_src():
    """Empty src directory → returns moved=[], no errors."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        result = migrate_meetings(src, dst, lambda *a: None, threading.Event())
        assert result["moved"] == []
        assert result["errors"] == []
        assert result["cancelled"] is False


def test_migrate_single_meeting():
    """One meeting folder moves with all files intact, src directory left empty."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        _make_meeting(src, "2026-01-01_meeting", {
            "transcript.txt": b"hello",
            "description.md": b"# meta",
            "audio.mp3": b"\x00" * 1000,
        })
        result = migrate_meetings(src, dst, lambda *a: None, threading.Event())
        assert "2026-01-01_meeting" in result["moved"]
        # Files moved to dst
        assert os.path.isfile(os.path.join(dst, "2026-01-01_meeting", "transcript.txt"))
        assert os.path.isfile(os.path.join(dst, "2026-01-01_meeting", "audio.mp3"))
        # src folder gone
        assert not os.path.exists(os.path.join(src, "2026-01-01_meeting"))


def test_migrate_multiple_meetings():
    """All subfolders moved; total count preserved."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        for i in range(3):
            _make_meeting(src, f"m{i}", {"transcript.txt": b""})
        result = migrate_meetings(src, dst, lambda *a: None, threading.Event())
        assert sorted(result["moved"]) == ["m0", "m1", "m2"]
        assert len(os.listdir(dst)) == 3
        assert len(os.listdir(src)) == 0


def test_migrate_collision_appends_timestamp():
    """If dst has a same-named folder, the new one gets `_imported_<HHMMSS>`."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        _make_meeting(src, "dup", {"a.txt": b"new"})
        _make_meeting(dst, "dup", {"a.txt": b"old"})
        result = migrate_meetings(src, dst, lambda *a: None, threading.Event())
        # Original "dup" in dst untouched
        with open(os.path.join(dst, "dup", "a.txt"), "rb") as f:
            assert f.read() == b"old"
        # New entry under _imported_<HHMMSS> suffix
        suffixed = [d for d in os.listdir(dst) if d.startswith("dup_imported_")]
        assert len(suffixed) == 1
        # The migrated content is in the suffixed copy
        with open(os.path.join(dst, suffixed[0], "a.txt"), "rb") as f:
            assert f.read() == b"new"


def test_migrate_progress_called():
    """on_progress fires twice per folder (start + done)."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        _make_meeting(src, "a", {"x": b""})
        _make_meeting(src, "b", {"x": b""})
        calls = []
        migrate_meetings(
            src, dst,
            lambda *args: calls.append(args),
            threading.Event(),
        )
        # 2 folders × 2 calls (start + done) = 4 progress events
        assert len(calls) == 4
        # First call signals (0, 2, name) — start of first folder
        assert calls[0][0] == 0 and calls[0][1] == 2


def test_migrate_cancel_mid_flight():
    """Cancel between folders → remaining stay in src, total count preserved."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        for i in range(5):
            _make_meeting(src, f"m{i}", {"x": b""})

        cancel = threading.Event()
        seen_done = [0]

        def progress(done, total, name):
            # Set cancel after 2 folders finished
            if done == 2:
                cancel.set()
            seen_done[0] = max(seen_done[0], done)

        result = migrate_meetings(src, dst, progress, cancel)
        assert result["cancelled"] is True
        assert len(result["moved"]) <= 5
        # Invariant: every meeting still accounted for somewhere
        assert len(os.listdir(src)) + len(os.listdir(dst)) == 5


# ── detect_old_locations ───────────────────────────────────────────────


def test_detect_old_locations_empty_returns_nothing():
    """No legacy paths exist → empty list."""
    result = detect_old_locations(probe_paths=["/nonexistent/probe/path"])
    assert result == []


def test_detect_old_locations_finds_populated():
    """Legacy path with entries → reported with count."""
    with tempfile.TemporaryDirectory() as old:
        _make_meeting(old, "m1", {"transcript.txt": b""})
        _make_meeting(old, "m2", {"transcript.txt": b""})
        result = detect_old_locations(probe_paths=[old])
        assert len(result) == 1
        assert result[0] == (old, 2)


# ── count_meetings ─────────────────────────────────────────────────────


def test_count_meetings_excludes_non_meeting_dirs():
    """Loose files at top level are ignored; only subdirectories count."""
    with tempfile.TemporaryDirectory() as d:
        _make_meeting(d, "real_meeting", {"transcript.txt": b""})
        # Loose file — not a folder
        with open(os.path.join(d, "stray.txt"), "w") as f:
            f.write("noise")
        assert count_meetings(d) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_meetings_migration.py -v`
Expected: 9 FAIL with `ModuleNotFoundError: No module named 'meetings_migration'`.

- [ ] **Step 3: Implement `meetings_migration.py`**

Create `meetings_migration.py` at repo root:

```python
"""Pure-Python meetings folder migration.

Three exported functions:
  - count_meetings(path) — how many subfolders look like meetings
  - detect_old_locations(probe_paths=None) — scan legacy paths for entries
  - migrate_meetings(src, dst, on_progress, cancel_event) — move all folders

No Tk imports. All UI integration lives in ui/dialogs/migration.py.

See docs/superpowers/specs/2026-05-28-meetings-folder-picker-design.md.
"""
from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from datetime import datetime


def count_meetings(path: str) -> int:
    """Count subdirectories under `path`. Loose files don't count.

    Returns 0 if `path` doesn't exist (no error — caller might be probing
    a path that's about to be created).
    """
    if not os.path.isdir(path):
        return 0
    return sum(
        1 for name in os.listdir(path)
        if os.path.isdir(os.path.join(path, name))
    )


def detect_old_locations(
    probe_paths: list[str] | None = None,
) -> list[tuple[str, int]]:
    """Find legacy meeting folders with content.

    Returns [(path, count), ...] sorted by count descending. Excludes
    paths that don't exist OR have zero meetings.

    `probe_paths` is injected for testability. Default in production
    code: the two legacy locations defined in utils._LEGACY_HISTORY_LOCATIONS
    (caller passes them explicitly so this module stays decoupled from
    utils.py's path constants).
    """
    if probe_paths is None:
        probe_paths = []
    seen: dict[str, int] = {}
    for raw_path in probe_paths:
        path = os.path.abspath(raw_path)
        if path in seen:
            continue
        n = count_meetings(path)
        if n > 0:
            seen[path] = n
    return sorted(seen.items(), key=lambda kv: kv[1], reverse=True)


def migrate_meetings(
    src: str,
    dst: str,
    on_progress: Callable[[int, int, str], None],
    cancel_event: threading.Event,
) -> dict:
    """Move all subfolders from `src` to `dst`.

    Each folder is moved atomically via shutil.move (os.rename for
    same-volume, copy2+remove for cross-volume — stdlib handles both).
    Per-folder progress; no per-byte tracking.

    On collision (dst already has a folder by the same name), the new
    one gets an `_imported_<HHMMSS>` suffix. Timestamp gives uniqueness
    even across multiple migration runs.

    On per-folder error (locked file, permission denied, disk full for
    that folder), the error is recorded and the next folder is tried —
    partial migration beats total failure.

    Cancellation: checked between folders only. The in-progress folder
    completes its move (we don't kill shutil mid-call).

    Returns:
      {
        "moved": [folder_name, ...],
        "skipped": [],   # reserved for future use (e.g. user pre-skip filter)
        "errors": [(folder_name, error_msg), ...],
        "cancelled": bool,
      }
    """
    os.makedirs(dst, exist_ok=True)

    src_entries = []
    if os.path.isdir(src):
        src_entries = [
            d for d in sorted(os.listdir(src))
            if os.path.isdir(os.path.join(src, d))
        ]

    total = len(src_entries)
    moved: list[str] = []
    errors: list[tuple[str, str]] = []

    for i, name in enumerate(src_entries):
        if cancel_event.is_set():
            break

        on_progress(i, total, name)

        src_path = os.path.join(src, name)
        dst_path = os.path.join(dst, name)

        if os.path.exists(dst_path):
            ts = datetime.now().strftime("%H%M%S")
            dst_path = os.path.join(dst, f"{name}_imported_{ts}")

        try:
            shutil.move(src_path, dst_path)
            moved.append(name)
        except (OSError, PermissionError) as e:
            errors.append((name, str(e)))
            # Continue with next folder
            continue

        on_progress(i + 1, total, name)

    return {
        "moved": moved,
        "skipped": [],
        "errors": errors,
        "cancelled": cancel_event.is_set(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_meetings_migration.py -v`
Expected: 9 PASS.

Also run full suite: `python -m pytest -q`
Expected: 388 + 9 = 397 passed.

Lint: `python -m ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add meetings_migration.py tests/test_meetings_migration.py
git commit -m "$(cat <<'EOF'
feat(meetings): pure-Python migration module + 9 unit tests

New module meetings_migration.py exports three functions:
  - count_meetings(path) — count subdirectories
  - detect_old_locations(probe_paths) — find populated legacy paths
  - migrate_meetings(src, dst, on_progress, cancel_event) — move all

Pure Python (no Tk), so the test file runs cleanly on Linux CI.
Real I/O via tempfile.TemporaryDirectory covers: empty src, single
move, multiple moves, collision suffix, progress callbacks (start +
done per folder), cancellation mid-flight, detect-finds-populated,
detect-finds-nothing, count-excludes-loose-files.

Spec: docs/superpowers/specs/2026-05-28-meetings-folder-picker-design.md
EOF
)"
```

---

## Task 2: `utils.get_meetings_dir()` resolver + config key

**Files:**
- Modify: `utils.py`
- Modify: `config.example.json`
- Create: `tests/test_utils_meetings_resolver.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_utils_meetings_resolver.py`:

```python
"""AST + source-text checks for utils.get_meetings_dir."""
from __future__ import annotations

import ast
from pathlib import Path

UTILS_PATH = Path(__file__).resolve().parent.parent / "utils.py"


def _get_function_def(source: str, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_get_meetings_dir_function_exists():
    source = UTILS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "get_meetings_dir")
    assert fn is not None, "get_meetings_dir must be defined in utils.py"


def test_get_meetings_dir_uses_config_key():
    source = UTILS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "get_meetings_dir")
    assert fn is not None
    body = ast.unparse(fn)
    assert "meetings_dir" in body, (
        "Resolver must read config['meetings_dir']"
    )


def test_get_meetings_dir_expands_env_and_user():
    """expanduser + expandvars must be called so ~/X and %USERPROFILE%\\X work."""
    source = UTILS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "get_meetings_dir")
    body = ast.unparse(fn)
    assert "expanduser" in body, "Resolver must expanduser() for ~ prefix"
    assert "expandvars" in body, "Resolver must expandvars() for %VAR% / $VAR"


def test_default_meetings_dir_constant_present():
    source = UTILS_PATH.read_text(encoding="utf-8")
    assert "_DEFAULT_MEETINGS_DIR" in source, (
        "Module-level _DEFAULT_MEETINGS_DIR constant required"
    )
    assert "Documents" in source, (
        "Default path should include 'Documents' (per spec)"
    )


def test_legacy_history_locations_constant_present():
    source = UTILS_PATH.read_text(encoding="utf-8")
    assert "_LEGACY_HISTORY_LOCATIONS" in source, (
        "Module-level _LEGACY_HISTORY_LOCATIONS constant required for "
        "first-launch detection (consumed by App.__init__)"
    )


def test_config_example_has_meetings_dir_key():
    """config.example.json must list meetings_dir with empty-string default."""
    import json
    config_path = UTILS_PATH.parent / "config.example.json"
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert "meetings_dir" in cfg, (
        "config.example.json must include 'meetings_dir' key"
    )
    assert cfg["meetings_dir"] == "", (
        "Default value must be '' (sentinel for 'use default')"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_utils_meetings_resolver.py -v`
Expected: 6 FAIL — function missing, constants missing, config key missing.

- [ ] **Step 3: Implement resolver in `utils.py`**

In `utils.py`, find the existing history section (around line 132):

```python
# ── History — each entry is a folder on disk ─────────────────

_HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")


def _ensure_history_dir() -> str:
    os.makedirs(_HISTORY_DIR, exist_ok=True)
    return _HISTORY_DIR
```

Replace those two definitions (the constant and `_ensure_history_dir`) with the new resolver block:

```python
# ── Meetings folder — user-configurable, with 3-level fallback ─────────

_DEFAULT_MEETINGS_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "AudioTranscriber", "meetings",
)

# Legacy paths probed on first launch — entries here trigger the
# migration prompt. Kept as a module-level constant so App.__init__
# can pass it to meetings_migration.detect_old_locations.
_LEGACY_HISTORY_LOCATIONS = [
    # Sibling of utils.py — in dev source mode this is <repo>/history/,
    # in PyInstaller bundle it's <bundle>/_internal/history/. Same
    # expression covers both because __file__ resolves differently.
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "history"),
    # PyInstaller bundle "root" (parent of _internal/) — edge case for
    # builds that drop history at bundle root instead of inside _internal.
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "history",
    ),
]


def _normalize_meetings_path(raw: str) -> str:
    """Expand %VARS% / ~, normalize separators, return absolute path."""
    return os.path.abspath(
        os.path.expandvars(os.path.expanduser(raw.strip()))
    )


def get_meetings_dir() -> str:
    """Return absolute path to the active meetings folder, creating it if missing.

    Resolution order (each level falls through on failure):
      1. config["meetings_dir"] if non-empty AND parent exists AND writable
      2. _DEFAULT_MEETINGS_DIR (%USERPROFILE%/Documents/AudioTranscriber/meetings/)
      3. <bundle>/_internal/history/ — legacy last-resort fallback for
         corporate Windows profiles where Documents itself is locked

    The chosen directory is created (mkdir -p) on call. Callers can
    rely on the returned path existing as a directory.
    """
    cfg = load_config()
    candidates: list[str] = []

    raw = (cfg.get("meetings_dir") or "").strip()
    if raw:
        candidates.append(_normalize_meetings_path(raw))
    candidates.append(_DEFAULT_MEETINGS_DIR)
    # Legacy first probe path is the same expression as _LEGACY_HISTORY_LOCATIONS[0]
    candidates.append(_LEGACY_HISTORY_LOCATIONS[0])

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            # Touch-test writability via a temp marker file
            test_marker = os.path.join(path, ".write-test")
            with open(test_marker, "w") as f:
                f.write("")
            os.remove(test_marker)
            return path
        except (OSError, PermissionError):
            continue

    # If everything fails, return the default and let the caller's
    # next os.* operation surface the real error. We've already tried
    # to create it above; if even that failed, something is deeply wrong.
    return _DEFAULT_MEETINGS_DIR


def _ensure_history_dir() -> str:
    """Backwards-compat shim — callers in this file pre-rename still use
    the old name. Equivalent to get_meetings_dir() now.
    """
    return get_meetings_dir()
```

Now update the existing callers inside `utils.py`. Find `create_history_entry` (around line 142):

```python
def create_history_entry(
    audio_file_path: str,
    transcript_text: str,
    language: str | None,
    model: str,
) -> str:
    """Create a history folder with audio copy, transcript.txt and description.md.

    Returns the path to the created folder.
    """
    _ensure_history_dir()
```

Replace `_ensure_history_dir()` calls and `_HISTORY_DIR` references with `get_meetings_dir()`. The full updated function body (replace lines 142-184 of the original):

```python
def create_history_entry(
    audio_file_path: str,
    transcript_text: str,
    language: str | None,
    model: str,
) -> str:
    """Create a meeting folder with audio copy, transcript.txt and description.md.

    Returns the path to the created folder.
    """
    meetings_dir = get_meetings_dir()

    audio_name = os.path.basename(audio_file_path)
    base_name = os.path.splitext(audio_name)[0]
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder_name = f"{timestamp}_{base_name}"
    folder_path = os.path.join(meetings_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    # Copy audio file
    if os.path.isfile(audio_file_path):
        shutil.copy2(audio_file_path, os.path.join(folder_path, audio_name))

    # Save transcript
    txt_path = os.path.join(folder_path, "transcript.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(transcript_text)

    # Save description.md
    lang_label = language or "auto"
    md_content = (
        f"# {audio_name}\n\n"
        f"- **Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- **Язык:** {lang_label}\n"
        f"- **Модель:** {model}\n"
        f"- **Аудио файл:** {audio_name}\n"
        f"- **Исходный путь:** {audio_file_path}\n"
    )
    md_path = os.path.join(folder_path, "description.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return folder_path
```

Find `list_history_entries` (around line 187). Replace its body's `_HISTORY_DIR` references:

```python
def list_history_entries() -> list[dict]:
    """Scan the meetings directory and return entries sorted by date (newest first).

    Each entry dict: folder_path, folder_name, audio_file, date_created.
    """
    meetings_dir = get_meetings_dir()
    entries = []
    for name in os.listdir(meetings_dir):
        folder_path = os.path.join(meetings_dir, name)
        if not os.path.isdir(folder_path):
            continue

        # Find audio file (not .txt, not .md)
        audio_file = None
        has_transcript = False
        for f in os.listdir(folder_path):
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                audio_file = f
            elif f == "transcript.txt":
                has_transcript = True

        # Parse date from folder name (YYYY-MM-DD_HH-MM-SS_...)
        date_str = name[:19] if len(name) >= 19 else name
        date_display = date_str.replace("_", " ", 1).replace("-", ":", 3)

        entries.append({
            "folder_path": folder_path,
            "folder_name": name,
            "audio_file": audio_file,
            "has_transcript": has_transcript,
            "date_created": date_str,
            "date_display": date_display,
        })

    entries.sort(key=lambda e: e["date_created"], reverse=True)
    return entries
```

`delete_history_entry` and `open_in_explorer` don't reference `_HISTORY_DIR` directly — leave them as-is.

Update `config.example.json` (add the new key). Read the current file first to know which JSON entry to insert near:

```bash
# In your editor, add this entry to config.example.json:
#   "meetings_dir": "",
# Place it near other path-ish keys (e.g. after the cloud_provider block).
```

Specifically, in `config.example.json`, add a new top-level key. The exact diff depends on current content, but the addition is:

```json
{
  "...existing keys...",
  "meetings_dir": ""
}
```

- [ ] **Step 4: Run tests + verify**

```powershell
python -m pytest tests/test_utils_meetings_resolver.py -v
python -m pytest -q
python -m ruff check .
```

Expected: 6 PASS on resolver tests, 397 + 6 = 403 total, ruff clean.

If existing utils.py-related tests fail because they assumed the old `_HISTORY_DIR` constant — update those assertions to use `get_meetings_dir()`. Run a grep first: `grep -rn "_HISTORY_DIR" tests/` — should return zero.

- [ ] **Step 5: Commit**

```bash
git add utils.py config.example.json tests/test_utils_meetings_resolver.py
git commit -m "$(cat <<'EOF'
feat(utils): get_meetings_dir() resolver + config[meetings_dir]

Replaces the hardcoded _HISTORY_DIR constant (which resolved to
<bundle>/_internal/history/ in frozen mode, wiped on every rebuild)
with a function that reads config["meetings_dir"] at-call-time with
a 3-level fallback:
  1. config (if non-empty AND parent exists AND writable)
  2. %USERPROFILE%/Documents/AudioTranscriber/meetings/ (default)
  3. <bundle>/_internal/history/ (legacy last-resort)

Each fallback level is touch-tested for writability before being
accepted. _LEGACY_HISTORY_LOCATIONS exposed as module-level constant
for App.__init__ to consume via detect_old_locations.

config.example.json gains "meetings_dir": "" (empty = use default).
Existing utils callers (create_history_entry, list_history_entries)
updated to call get_meetings_dir() instead of the old constant.

6 new AST + source-text tests covering function existence, config-key
read, expanduser/expandvars usage, constants presence, JSON schema.
EOF
)"
```

---

## Task 3: Rename `ui/dialogs/history.py` → `ui/dialogs/meetings.py`

**Files:**
- Rename: `ui/dialogs/history.py` → `ui/dialogs/meetings.py`
- Modify (inside renamed file): class names, window title, label texts
- Create: `tests/test_meetings_dialog_rename.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_meetings_dialog_rename.py`:

```python
"""Verifies the history → meetings rename across UI surface.

Source-text checks only (no UI imports — sounddevice on Linux CI).
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_history_file_renamed_to_meetings():
    """Old file must not exist; new file must exist."""
    assert not (REPO / "ui" / "dialogs" / "history.py").exists(), (
        "ui/dialogs/history.py must be removed (renamed to meetings.py)"
    )
    assert (REPO / "ui" / "dialogs" / "meetings.py").exists(), (
        "ui/dialogs/meetings.py must exist after rename"
    )


def test_meetings_module_defines_meetings_dialog_class():
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "class MeetingsDialog" in src, (
        "MeetingsDialog class must be defined in ui/dialogs/meetings.py"
    )
    assert "class MeetingViewerDialog" in src, (
        "MeetingViewerDialog class must be defined"
    )
    assert "class HistoryDialog" not in src, (
        "Old HistoryDialog name must be gone (clean rename)"
    )


def test_meetings_dialog_title_is_meetings():
    """Window title must be «Митинги», not «История транскрипций»."""
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "История транскрипций" not in src, (
        "Old window title must be gone"
    )
    assert '"Митинги"' in src or "'Митинги'" in src, (
        "Window title must be «Митинги»"
    )


def test_meetings_footer_label_renamed():
    """«Записей: N» → «Митингов: N»."""
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "Записей:" not in src, "Old «Записей:» label must be gone"
    assert "Митингов:" in src, "New «Митингов:» label required"


def test_meetings_empty_state_renamed():
    """«Нет транскрипций» → «Нет митингов»."""
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "Нет транскрипций" not in src
    assert "Нет митингов" in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_meetings_dialog_rename.py -v`
Expected: 5 FAIL — file doesn't exist at new path yet, old strings still present.

- [ ] **Step 3: Rename file + update identifiers**

First, git-aware rename:

```bash
git mv ui/dialogs/history.py ui/dialogs/meetings.py
```

Then edit `ui/dialogs/meetings.py` to apply identifier and string renames. Each replacement below is a single Edit; use Edit tool or sed equivalent:

| Find | Replace |
|---|---|
| `class HistoryDialog` | `class MeetingsDialog` |
| `class HistoryViewerDialog` | `class MeetingViewerDialog` |
| `"""History browser + read-only transcript viewer."""` | `"""Meetings browser + read-only transcript viewer."""` |
| `"""Browse transcription history — each entry is a folder on disk."""` | `"""Browse meeting history — each entry is a folder on disk."""` |
| `self.title("История транскрипций")` | `self.title("Митинги")` |
| `text="История транскрипций"` | `text="Митинги"` |
| `text=f"Записей: {len(entries)}{suffix}"` | `text=f"Митингов: {len(entries)}{suffix}"` |
| `msg = "Ничего не найдено" if query else "Нет транскрипций"` | `msg = "Ничего не найдено" if query else "Нет митингов"` |
| `HistoryViewerDialog(self, entry, self._on_load_to_main)` | `MeetingViewerDialog(self, entry, self._on_load_to_main)` |

The docstring at the top of the file becomes:

```python
"""Meetings browser + read-only transcript viewer.

Renamed from history.py on 2026-05-28 — UI consistency with the new
«Митинги» button + Settings folder picker. Files on disk are
unchanged; the underlying utils.list_history_entries / delete_history_entry
helpers keep their internal names (rename was UI-only).
"""
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/test_meetings_dialog_rename.py -v
python -m pytest -q
python -m ruff check .
```

Expected: 5 PASS, full suite still ≥ 403.

`pytest -q` may flag any test that imported `from ui.dialogs.history import ...` — find those:

```bash
grep -rn "from ui.dialogs.history" tests/ ui/ gdrive/ tasks/ providers/ transcriber/
```

If non-empty, update each to `from ui.dialogs.meetings import` (and class names accordingly).

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/meetings.py tests/test_meetings_dialog_rename.py
# git mv has already staged the rename; verify with git status
git commit -m "$(cat <<'EOF'
refactor(ui): rename history.py → meetings.py + class/string renames

git mv ui/dialogs/history.py ui/dialogs/meetings.py. Inside:
  - class HistoryDialog → MeetingsDialog
  - class HistoryViewerDialog → MeetingViewerDialog
  - Window title «История транскрипций» → «Митинги»
  - Footer label «Записей: N» → «Митингов: N»
  - Empty-state «Нет транскрипций» → «Нет митингов»

utils.* function names (list_history_entries etc.) intentionally
unchanged — they're internal helpers, renaming would ripple into
gdrive/backup.py and tests for zero user-visible benefit.

5 new source-text tests verify the rename held across all surfaces.
EOF
)"
```

---

## Task 4: Migration UI shell (`ui/dialogs/migration.py`)

**Files:**
- Create: `ui/dialogs/migration.py`

This task creates the two modal dialogs. They have no automated tests because they require a Tk root and are not unit-testable in the way `meetings_migration.py` is. Coverage comes from the manual smoke checklist in Task 10.

- [ ] **Step 1: Skip the failing-test step (UI-only module)**

Manual smoke handles this. Note in commit message that automated coverage is deferred to manual.

- [ ] **Step 2: Read existing dialog patterns**

Read `ui/dialogs/meetings.py` and `ui/dialogs/settings.py` to mirror their CTk styling conventions (theme.py colors, font, button shapes). Both already imported in this codebase.

- [ ] **Step 3: Implement `ui/dialogs/migration.py`**

Create the file:

```python
"""Migration dialogs: prompt + progress.

Two modal CTkToplevel windows used by both first-launch and Settings-
trigger flows:

  MigrationPromptDialog — asks the user whether to move existing
    meetings from `src` to `dst`. Buttons differ by mode:
      first_launch: [Перенести] [Оставить в старой папке] [Спросить позже]
      settings:     [Перенести] [Просто переключить]

  MigrationProgressDialog — shows progress while migrate_meetings runs
    in a daemon thread. Cancel button signals the worker; closing via
    WM X is disabled (must use Cancel).

UI shell only — actual migration logic lives in meetings_migration.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable

import customtkinter as ctk

from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    BORDER,
    FONT,
    GREEN,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


def _folder_size_bytes(path: str) -> int:
    """Sum of file sizes under `path`. Tolerates locked files."""
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _fmt_size(n: int) -> str:
    """Bytes → human-readable (KB / MB / GB)."""
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.0f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.0f} MB"
    return f"{n / 1024**3:.1f} GB"


class MigrationPromptDialog(ctk.CTkToplevel):
    """Modal asking whether to migrate existing meetings.

    Modes:
      "first_launch" → 3 buttons (Перенести / Оставить в старой / Спросить позже)
      "settings"     → 2 buttons (Перенести / Просто переключить)

    On user choice, calls `on_choice(choice)` with one of:
      "migrate", "keep_old", "later", "switch_only"
    """

    def __init__(
        self,
        parent,
        *,
        src: str,
        dst: str,
        mode: str,
        on_choice: Callable[[str], None],
    ):
        super().__init__(parent)
        self.title("Перенос митингов")
        self.geometry("560x340")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        self._on_choice = on_choice
        self._mode = mode

        self.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        title_text = (
            "Перенос митингов" if mode == "first_launch"
            else "Перенести существующие митинги?"
        )
        ctk.CTkLabel(
            header, text=title_text,
            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        # Body — show src / dst paths + counts
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=20, pady=12, sticky="ew")
        body.grid_columnconfigure(0, weight=1)

        # Count + size for src
        from meetings_migration import count_meetings
        n_src = count_meetings(src)
        size_src = _fmt_size(_folder_size_bytes(src))

        if mode == "first_launch":
            label1 = f"Найдено {n_src} митингов в старой папке:"
        else:
            label1 = f"В текущей папке {n_src} митингов:"

        ctk.CTkLabel(
            body, text=label1,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_SECONDARY, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(
            body, text=src,
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_PRIMARY, anchor="w",
            wraplength=500,
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        dst_label = (
            "Новая папка по умолчанию:" if mode == "first_launch"
            else "Новая папка:"
        )
        ctk.CTkLabel(
            body, text=dst_label,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_SECONDARY, anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(
            body, text=dst,
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_PRIMARY, anchor="w",
            wraplength=500,
        ).grid(row=3, column=0, sticky="w", pady=(0, 8))

        # Footer with buttons
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=20, pady=(8, 16), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        btn_migrate = ctk.CTkButton(
            footer,
            text=f"Перенести ({n_src} файлов, ~{size_src})",
            command=lambda: self._choose("migrate"),
            height=40, corner_radius=20, width=320,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
        )
        btn_migrate.grid(row=0, column=0, pady=4, sticky="ew")

        if mode == "first_launch":
            btn_keep = ctk.CTkButton(
                footer, text="Оставить в старой папке",
                command=lambda: self._choose("keep_old"),
                height=36, corner_radius=18, width=320,
                font=ctk.CTkFont(family=FONT, size=13),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
                text_color="#8AB4F8",
            )
            btn_keep.grid(row=1, column=0, pady=4, sticky="ew")

            btn_later = ctk.CTkButton(
                footer, text="Спросить позже",
                command=lambda: self._choose("later"),
                height=36, corner_radius=18, width=320,
                font=ctk.CTkFont(family=FONT, size=13),
                fg_color="transparent", hover_color=SURFACE_BRIGHT,
                text_color=TEXT_SECONDARY,
            )
            btn_later.grid(row=2, column=0, pady=4, sticky="ew")
        else:
            btn_switch = ctk.CTkButton(
                footer, text="Просто переключить",
                command=lambda: self._choose("switch_only"),
                height=36, corner_radius=18, width=320,
                font=ctk.CTkFont(family=FONT, size=13),
                fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
                text_color="#8AB4F8",
            )
            btn_switch.grid(row=1, column=0, pady=4, sticky="ew")

    def _choose(self, choice: str) -> None:
        self.grab_release()
        self.destroy()
        self._on_choice(choice)


class MigrationProgressDialog(ctk.CTkToplevel):
    """Modal showing migration progress. Cancel signals the worker.

    The actual move runs on a daemon thread. UI updates are marshalled
    via parent.after(0, ...). On completion, calls `on_done(summary)`
    where summary is the dict returned by migrate_meetings.
    """

    def __init__(
        self,
        parent,
        *,
        src: str,
        dst: str,
        on_done: Callable[[dict], None],
    ):
        super().__init__(parent)
        self.title("Перенос митингов")
        self.geometry("500x200")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        # Disable WM X — force user to use Cancel button
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        self._src = src
        self._dst = dst
        self._on_done = on_done
        self._cancel_event = threading.Event()

        self.grid_columnconfigure(0, weight=1)

        # Status label
        self._status = ctk.CTkLabel(
            self, text="Подготовка...",
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, anchor="w",
        )
        self._status.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="ew")

        # Current-folder label
        self._current = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(family=FONT, size=11),
            text_color=TEXT_SECONDARY, anchor="w",
        )
        self._current.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="ew")

        # Progress bar
        self._progress = ctk.CTkProgressBar(self, height=8, corner_radius=4)
        self._progress.grid(row=2, column=0, padx=20, pady=8, sticky="ew")
        self._progress.set(0.0)

        # Cancel button
        ctk.CTkButton(
            self, text="Отмена",
            command=self._on_cancel,
            height=36, corner_radius=18, width=120,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
            text_color="#8AB4F8",
        ).grid(row=3, column=0, padx=20, pady=(12, 16), sticky="e")

        # Spawn worker after grid is laid out
        self.after(50, self._start_worker)

    def _start_worker(self) -> None:
        def worker() -> None:
            from meetings_migration import migrate_meetings
            summary = migrate_meetings(
                self._src, self._dst,
                on_progress=self._on_progress,
                cancel_event=self._cancel_event,
            )
            self.after(0, lambda: self._on_complete(summary))

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        # Called from worker thread — marshal to main
        ratio = done / total if total else 0.0
        text_main = f"Переношу митинг {done} / {total}:"
        self.after(0, lambda: self._status.configure(text=text_main))
        self.after(0, lambda n=name: self._current.configure(text=n))
        self.after(0, lambda r=ratio: self._progress.set(r))

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        self._status.configure(text="Отменяется...")

    def _on_complete(self, summary: dict) -> None:
        # Always close + invoke callback. Caller handles partial-state UX.
        self.grab_release()
        self.destroy()
        self._on_done(summary)
```

- [ ] **Step 4: Run lint + suite**

```powershell
python -m pytest -q
python -m ruff check .
```

Expected: no regressions, ruff clean. New file is referenced only from yet-to-be-written code (Task 6 + Task 8) so no import errors yet.

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/migration.py
git commit -m "$(cat <<'EOF'
feat(ui/migration): MigrationPromptDialog + MigrationProgressDialog

UI shell for the meetings migration. No automated tests (UI imports
sounddevice via theme, fails on Linux CI). Coverage via manual smoke
checklist in Task 10 of the implementation plan.

MigrationPromptDialog: modal with src/dst paths + count + size,
buttons differ by mode (first_launch has 3 buttons, settings has 2).
on_choice callback receives "migrate" / "keep_old" / "later" / "switch_only".

MigrationProgressDialog: spawns daemon thread running
meetings_migration.migrate_meetings, marshals progress callbacks
via parent.after(0, ...). Cancel button signals the worker (in-
progress folder completes its move). WM X disabled — Cancel only.
EOF
)"
```

---

## Task 5: Settings dialog «Митинги» section

**Files:**
- Modify: `ui/dialogs/settings.py`
- Create: `tests/test_settings_dialog_meetings_section.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings_dialog_meetings_section.py`:

```python
"""Source-text checks for the new Митинги section in Settings."""
from __future__ import annotations

from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)


def test_settings_has_meetings_section_card():
    """A section card titled «Митинги» exists in settings.py."""
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert '"Митинги"' in src or "'Митинги'" in src, (
        "Settings must declare a section card with title «Митинги»"
    )
    # The section_card helper is called with title="Митинги"
    assert "_section_card" in src
    assert "Митинги" in src


def test_settings_uses_askdirectory_for_picker():
    """The folder picker uses tkinter.filedialog.askdirectory."""
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "askdirectory" in src, (
        "Folder picker must use filedialog.askdirectory (Win32-native)"
    )


def test_settings_imports_get_meetings_dir():
    """Settings must read the current meetings dir via the resolver."""
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "get_meetings_dir" in src, (
        "Settings must import + use utils.get_meetings_dir to show current path"
    )


def test_settings_has_default_reset_button():
    """A button to reset meetings_dir to default (empty string) is present."""
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    # Either the visible button text "↻ Default" or "Default" alone
    assert "Default" in src, (
        "Settings must include a reset-to-default button for meetings_dir"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_settings_dialog_meetings_section.py -v`
Expected: 4 FAIL.

- [ ] **Step 3: Add the section to `ui/dialogs/settings.py`**

First, add to the existing import block at the top of settings.py. Find:

```python
from utils import save_config
```

Replace with:

```python
from utils import get_meetings_dir, save_config
```

Also add filedialog import near other tkinter imports:

```python
import tkinter as tk
from tkinter import filedialog
```

(`tkinter` is already imported in the file — verify and update accordingly. If `filedialog` is not yet imported, add it. The `tkinter import tk` line is likely already there.)

Next, find `_build_dictionaries_section` (around line 325 of current settings.py). Insert a new section builder ABOVE it, and bump `_build_dictionaries_section`'s row arg from row=4 to row=5.

The new method (insert before `_build_dictionaries_section`):

```python
    def _build_meetings_section(self, parent) -> None:
        """Meetings folder picker — path entry + Выбрать + Default + stats.

        On path change: triggers MigrationPromptDialog if the current
        folder has entries (mode="settings"). Otherwise silent save.
        """
        section = self._section_card(parent, "Митинги", row=4)

        label(section, "Папка хранения").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )

        self._meetings_path_var = ctk.StringVar(value=get_meetings_dir())
        self._meetings_entry = ctk.CTkEntry(
            section, textvariable=self._meetings_path_var,
            height=36, corner_radius=10,
            border_color=BORDER, border_width=1,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            state="readonly",
        )
        self._meetings_entry.grid(
            row=0, column=1, columnspan=2, padx=4, pady=6, sticky="ew",
        )

        tonal_button(
            section, text="\U0001f4c1 Выбрать",
            command=self._on_pick_meetings_folder, width=130,
        ).grid(row=0, column=3, padx=(4, 4), pady=6)

        tonal_button(
            section, text="↻ Default",
            command=self._on_reset_meetings_folder, width=120,
        ).grid(row=1, column=3, padx=(4, 4), pady=(0, 6))

        # Stats label — refreshed on dialog open and after path change
        self._meetings_stats_label = label(section, "", anchor="w")
        self._meetings_stats_label.grid(
            row=1, column=0, columnspan=3, padx=4, pady=(0, 6), sticky="w",
        )
        self._refresh_meetings_stats()

    def _refresh_meetings_stats(self) -> None:
        """Compute «В этой папке: N митингов • X GB» and update the label."""
        from meetings_migration import count_meetings
        path = self._meetings_path_var.get()
        n = count_meetings(path)
        # Lazy size compute — count files lightly
        from ui.dialogs.migration import _folder_size_bytes, _fmt_size
        size = _folder_size_bytes(path)
        self._meetings_stats_label.configure(
            text=f"В этой папке: {n} митингов • {_fmt_size(size)}",
        )

    def _on_pick_meetings_folder(self) -> None:
        """User clicked «Выбрать» — open native dir picker, maybe migrate."""
        chosen = filedialog.askdirectory(
            title="Папка для хранения митингов",
            initialdir=self._meetings_path_var.get(),
            parent=self,
        )
        if not chosen:
            return  # user cancelled the picker

        current = self._meetings_path_var.get()
        normalized = os.path.abspath(chosen)
        if normalized == current:
            return  # no-op

        from meetings_migration import count_meetings
        if count_meetings(current) > 0:
            # Ask whether to migrate
            from ui.dialogs.migration import MigrationPromptDialog
            MigrationPromptDialog(
                self,
                src=current, dst=normalized, mode="settings",
                on_choice=lambda choice: self._on_migrate_choice(
                    choice, current, normalized,
                ),
            )
        else:
            # Empty current folder — silent switch
            self._save_meetings_path(normalized)

    def _on_migrate_choice(
        self, choice: str, src: str, dst: str,
    ) -> None:
        if choice == "migrate":
            from ui.dialogs.migration import MigrationProgressDialog
            MigrationProgressDialog(
                self, src=src, dst=dst,
                on_done=lambda summary: self._on_migration_done(summary, dst),
            )
        elif choice == "switch_only":
            self._save_meetings_path(dst)
        # No "later" branch — settings mode has 2 buttons only

    def _on_migration_done(self, summary: dict, new_path: str) -> None:
        """Worker finished. Persist new path + refresh stats."""
        self._save_meetings_path(new_path)
        # Could surface summary["errors"] in a follow-up, but for now
        # the user sees the new path + new stats which is sufficient.

    def _save_meetings_path(self, path: str) -> None:
        self._parent._config["meetings_dir"] = path
        save_config(self._parent._config)
        self._meetings_path_var.set(path)
        self._refresh_meetings_stats()

    def _on_reset_meetings_folder(self) -> None:
        """↻ Default — clear config[meetings_dir], resolver falls back."""
        self._parent._config["meetings_dir"] = ""
        save_config(self._parent._config)
        # After config save, get_meetings_dir() returns the default
        new_path = get_meetings_dir()
        self._meetings_path_var.set(new_path)
        self._refresh_meetings_stats()
```

Also add `os` import at top of settings.py if not present:

```python
import os
```

Finally, in `__init__` (the body construction with `_build_*_section` calls under Tab 1), find the line that calls `_build_dictionaries_section` and:
1. Add a call to `_build_meetings_section(scroll_transcription)` BEFORE it.
2. Change `_build_dictionaries_section(scroll_transcription)` to keep its existing row but bump the `row=4` arg inside the function body to `row=5`.

The Tab 1 section calls block should become:

```python
        # Tab 1 «Транскрипция» — core loop (minimal sufficient set)
        self._build_appearance_section(scroll_transcription)
        self._build_transcription_section(scroll_transcription)
        self._build_audio_section(scroll_transcription)
        self._build_cloud_section(scroll_transcription)
        self._build_meetings_section(scroll_transcription)    # ← new
        self._build_dictionaries_section(scroll_transcription)
```

Then inside `_build_dictionaries_section`, change:

```python
section = self._section_card(parent, "Словари", row=4)
```

to:

```python
section = self._section_card(parent, "Словари", row=5)
```

- [ ] **Step 4: Run tests + verify**

```powershell
python -m pytest tests/test_settings_dialog_meetings_section.py -v
python -m pytest -q
python -m ruff check .
```

Expected: 4 PASS on meetings-section tests, full suite ≥ 403 + 4 + 5 = 412 (with rename tests from Task 3).

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py tests/test_settings_dialog_meetings_section.py
git commit -m "$(cat <<'EOF'
feat(ui/settings): «Митинги» section with folder picker + migrate prompt

New section card at row=4 in Tab 1 «Транскрипция» (Словари bumped to
row=5). UI: readonly path entry showing get_meetings_dir() + «📁 Выбрать»
button (filedialog.askdirectory native picker) + «↻ Default» reset
button + stats label «В этой папке: N митингов • X GB».

On picker → if current folder has entries → MigrationPromptDialog
(settings mode = 2 buttons). On choice="migrate" → MigrationProgressDialog
runs the actual move. On choice="switch_only" → silent config update.

4 source-text tests verify: section title «Митинги», askdirectory
present, get_meetings_dir imported, Default button label visible.
EOF
)"
```

---

## Task 6: Main-window button rename + dialogs_mixin

**Files:**
- Modify: `ui/app/builder.py`
- Modify: `ui/app/dialogs_mixin.py`

- [ ] **Step 1: Write the failing test** (extends Task 3's rename test file)

Edit `tests/test_meetings_dialog_rename.py` and append:

```python
def test_builder_uses_meetings_button_text():
    """Main-window button text is «Митинги»."""
    builder = (REPO / "ui" / "app" / "builder.py").read_text(encoding="utf-8")
    assert '"Митинги"' in builder or "'Митинги'" in builder, (
        "Main window button must read «Митинги»"
    )
    assert '"История"' not in builder and "'История'" not in builder, (
        "Old «История» label must be gone from builder.py"
    )


def test_dialogs_mixin_has_open_meetings_dialog():
    """dialogs_mixin defines _open_meetings_dialog and imports MeetingsDialog."""
    mixin = (
        REPO / "ui" / "app" / "dialogs_mixin.py"
    ).read_text(encoding="utf-8")
    assert "_open_meetings_dialog" in mixin, (
        "DialogsMixin must define _open_meetings_dialog"
    )
    assert "_open_history_dialog" not in mixin, (
        "Old _open_history_dialog must be renamed"
    )
    assert "MeetingsDialog" in mixin, (
        "DialogsMixin must import MeetingsDialog from ui.dialogs.meetings"
    )
    assert "HistoryDialog" not in mixin, (
        "Old HistoryDialog import must be gone"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/test_meetings_dialog_rename.py::test_builder_uses_meetings_button_text tests/test_meetings_dialog_rename.py::test_dialogs_mixin_has_open_meetings_dialog -v
```

Expected: 2 FAIL.

- [ ] **Step 3: Apply renames**

In `ui/app/dialogs_mixin.py`, find the import:

```python
from ui.dialogs.history import HistoryDialog
```

Replace with:

```python
from ui.dialogs.meetings import MeetingsDialog
```

Find the method:

```python
    def _open_history_dialog(self):
        HistoryDialog(self, on_load_to_main=self._load_history_into_main)
```

Replace with:

```python
    def _open_meetings_dialog(self):
        MeetingsDialog(self, on_load_to_main=self._load_history_into_main)
```

(Note: `_load_history_into_main` keeps its name — it's an internal method that loads transcript text into the main textbox, unrelated to the meetings rename.)

In `ui/app/builder.py`, find the button definition for «История». The button text and command:

```python
text="История", command=app._open_history_dialog
```

(Find the exact line; format may vary slightly.) Replace with:

```python
text="Митинги", command=app._open_meetings_dialog
```

- [ ] **Step 4: Run tests + verify**

```powershell
python -m pytest tests/test_meetings_dialog_rename.py -v
python -m pytest -q
python -m ruff check .
```

Expected: 7 PASS on the rename test file (5 from Task 3 + 2 from Task 6), full suite passing.

- [ ] **Step 5: Commit**

```bash
git add ui/app/builder.py ui/app/dialogs_mixin.py tests/test_meetings_dialog_rename.py
git commit -m "$(cat <<'EOF'
refactor(ui): «История» button → «Митинги» + dialogs_mixin rename

Main window button text and callback name follow the dialog rename
from Task 3 of the implementation plan:
  - builder.py: text="История" → "Митинги", command renamed
  - dialogs_mixin.py: _open_history_dialog → _open_meetings_dialog
  - import HistoryDialog → MeetingsDialog

Internal _load_history_into_main keeps its name (unrelated; loads
transcript text into the main textbox).
EOF
)"
```

---

## Task 7: First-launch migration detection in `App.__init__`

**Files:**
- Modify: `ui/app/__init__.py`

- [ ] **Step 1: Write the failing test** (extends rename test)

Append to `tests/test_meetings_dialog_rename.py`:

```python
def test_app_init_schedules_migration_check():
    """App.__init__ must invoke detect_old_locations on startup."""
    src = (REPO / "ui" / "app" / "__init__.py").read_text(encoding="utf-8")
    assert "detect_old_locations" in src, (
        "App.__init__ must call detect_old_locations to find legacy meetings"
    )
    assert "MigrationPromptDialog" in src, (
        "App.__init__ must reference MigrationPromptDialog for first-launch flow"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
python -m pytest tests/test_meetings_dialog_rename.py::test_app_init_schedules_migration_check -v
```

Expected: FAIL.

- [ ] **Step 3: Add first-launch detection in `ui/app/__init__.py`**

Find the `App.__init__` method. After `self._config = load_config()` (or whichever existing call loads config), find a good insertion point — typically near other one-shot startup logic like `_first_run` detection.

Insert this code block (find a comment block about post-load_config setup, insert near it):

```python
        # First-launch meetings migration check. If meetings_dir isn't
        # explicitly configured AND a legacy history folder still has
        # entries, schedule a one-shot prompt (defer 500ms so the main
        # window finishes drawing before the modal appears).
        meetings_cfg = (self._config.get("meetings_dir") or "").strip()
        if not meetings_cfg:
            from meetings_migration import detect_old_locations
            from utils import _LEGACY_HISTORY_LOCATIONS, get_meetings_dir
            old_locations = detect_old_locations(
                probe_paths=_LEGACY_HISTORY_LOCATIONS,
            )
            if old_locations:
                # Use the most-populated legacy path as src
                src_path, _src_count = old_locations[0]
                dst_path = get_meetings_dir()
                if os.path.abspath(src_path) != os.path.abspath(dst_path):
                    self.after(500, lambda: self._show_migration_prompt(
                        src_path, dst_path,
                    ))
```

Add the supporting method to App (e.g., near other dialog-launcher methods):

```python
    def _show_migration_prompt(self, src: str, dst: str) -> None:
        """First-launch migration prompt. 3-button mode."""
        from ui.dialogs.migration import MigrationPromptDialog
        MigrationPromptDialog(
            self, src=src, dst=dst, mode="first_launch",
            on_choice=lambda c: self._on_first_launch_choice(c, src, dst),
        )

    def _on_first_launch_choice(
        self, choice: str, src: str, dst: str,
    ) -> None:
        if choice == "migrate":
            from ui.dialogs.migration import MigrationProgressDialog
            MigrationProgressDialog(
                self, src=src, dst=dst,
                on_done=lambda summary: self._on_first_launch_migrated(
                    summary, dst,
                ),
            )
        elif choice == "keep_old":
            # Point config at the old folder so the user keeps working
            # with the same entries; no files move.
            self._config["meetings_dir"] = src
            from utils import save_config
            save_config(self._config)
        # choice == "later" → do nothing; prompt re-appears next launch

    def _on_first_launch_migrated(
        self, summary: dict, new_path: str,
    ) -> None:
        from utils import save_config
        self._config["meetings_dir"] = new_path
        save_config(self._config)
```

Ensure `os` is imported at the top of `ui/app/__init__.py` (likely already is — verify).

- [ ] **Step 4: Run tests + verify**

```powershell
python -m pytest tests/test_meetings_dialog_rename.py -v
python -m pytest -q
python -m ruff check .
```

Expected: 8 PASS on rename file, full suite passing.

- [ ] **Step 5: Commit**

```bash
git add ui/app/__init__.py tests/test_meetings_dialog_rename.py
git commit -m "$(cat <<'EOF'
feat(ui/app): first-launch migration check + prompt scheduling

On App.__init__, after load_config, check whether meetings_dir is
unconfigured AND legacy history locations have populated entries.
If yes, schedule (after 500ms) MigrationPromptDialog in first_launch
mode (3 buttons: Перенести / Оставить в старой / Спросить позже).

3 new methods on App:
  - _show_migration_prompt(src, dst) — opens the dialog
  - _on_first_launch_choice(choice, src, dst) — dispatches by choice
  - _on_first_launch_migrated(summary, new_path) — persists new config

«Спросить позже» branch does nothing — prompt re-appears next launch.
«Оставить в старой» writes src into config, freezing user on legacy
path until they explicitly change it via Settings.
EOF
)"
```

---

## Task 8: gdrive/backup.py uses get_meetings_dir

**Files:**
- Modify: `gdrive/backup.py` (and any caller passing `history_dir=...`)

- [ ] **Step 1: Find callers**

```bash
grep -rn "history_dir=" gdrive/ ui/ tests/
grep -rn "run_backup" --include="*.py"
```

Expected output: at least one caller passing `history_dir="history"` (literal string). The exact site is the Settings dialog's Сделать backup сейчас button or a similar handler.

- [ ] **Step 2: Update each call site**

For each caller that passes `history_dir="history"` (or any other literal), replace with `history_dir=get_meetings_dir()`. The `run_backup(history_dir=...)` parameter name stays — only the value source changes.

Example diff for `ui/dialogs/settings.py` (if it's the caller — verify with grep):

```python
# Before:
result = run_backup(
    auth=self._parent._gdrive_auth,
    config=self._parent._config,
    history_dir="history",
    work_dir=work_dir,
    on_status=_status,
)

# After:
from utils import get_meetings_dir
result = run_backup(
    auth=self._parent._gdrive_auth,
    config=self._parent._config,
    history_dir=get_meetings_dir(),
    work_dir=work_dir,
    on_status=_status,
)
```

(`get_meetings_dir` may already be imported at top of settings.py from Task 5 — verify and skip the import line if so.)

- [ ] **Step 3: Verify existing gdrive tests still pass**

```powershell
python -m pytest tests/test_gdrive_backup.py -v
python -m pytest -q
python -m ruff check .
```

Expected: all green. The gdrive tests use mocked auth and tempfile paths — they call `run_backup` with explicit `history_dir=<tempdir>`, which still works because we didn't change the parameter name.

- [ ] **Step 4: Commit**

```bash
git add gdrive/ ui/
git commit -m "$(cat <<'EOF'
refactor(gdrive): run_backup callers pass get_meetings_dir() explicitly

Previously the literal "history" was passed as the history_dir kwarg.
After Task 2's resolver, the active path is computed dynamically,
so callers now invoke get_meetings_dir() at the call site.

The run_backup function signature is unchanged — only the value
flowing in. Existing tests (test_gdrive_backup.py) pass explicit
tempdir paths and continue to work.
EOF
)"
```

---

## Task 9: Manual smoke + PyInstaller verification

**Files:** none modified — verification only.

- [ ] **Step 1: Final automated checks**

```powershell
python -m pytest -q
python -m ruff check .
```

Expected: ~412 passed (388 baseline + 9 Task1 + 6 Task2 + 5 Task3 + 4 Task5 + 2 Task6 + 1 Task7 ≈ 415), ruff clean.

- [ ] **Step 2: Dev-mode smoke (`python app.py`)**

Pre-arrange: ensure a legacy `<bundle_or_repo>/history/` folder exists with 3+ fake meeting subfolders (`mkdir history/test_meeting_1` etc.). Each subfolder needs at least a `transcript.txt` for detection logic.

Walk through:
- [ ] App opens. First-launch banner (if `cloud_api_keys` empty) shown; otherwise no banner.
- [ ] If legacy history dir has ≥ 1 subfolder AND config["meetings_dir"] is "" → MigrationPromptDialog appears after ~500ms.
- [ ] Click «Перенести» → MigrationProgressDialog shows. Progress bar moves from 0% to 100%. Auto-closes on completion.
- [ ] After completion: legacy `history/` is empty; new default folder (`%USERPROFILE%\Documents\AudioTranscriber\meetings\`) contains the migrated meetings.
- [ ] Main-window button reads «Митинги» (not «История»).
- [ ] Click «Митинги» → MeetingsDialog opens. Title is «Митинги». Counter shows «Митингов: N». Migrated entries are visible.
- [ ] Settings → Tab 1 «Транскрипция» → section «Митинги» visible. Path entry shows the default path. Stats string «В этой папке: N митингов • X MB».
- [ ] Click «📁 Выбрать» → native Win32 folder picker opens. Choose a different folder. Settings-mode prompt appears.
- [ ] Click «Перенести» → progress dialog → completes → new path saved → Settings stats refresh.
- [ ] Click «↻ Default» → path resets to default. Stats refresh.
- [ ] Re-open the dialog, verify state persisted in config.json (`meetings_dir` value matches).
- [ ] Cancel mid-migration: click Cancel during progress → dialog closes with partial state; both folders have some entries; total count preserved.

- [ ] **Step 3: PyInstaller bundle build**

```powershell
$ErrorActionPreference = 'Continue'
if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
if (Test-Path 'dist') { Remove-Item -Recurse -Force 'dist' }
& '.\.venv-build\Scripts\python.exe' -m PyInstaller audio_transcriber.spec --noconfirm
Copy-Item 'config.example.json' 'dist\AudioTranscriber\_internal\config.json' -Force
```

Expected: build succeeds, `dist\AudioTranscriber\AudioTranscriber.exe` exists, bundle size ~351 MB.

- [ ] **Step 4: Bundled smoke**

```powershell
Start-Process '.\dist\AudioTranscriber\AudioTranscriber.exe'
```

Wait 3 seconds. Verify process alive. Repeat the dev-mode checklist briefly:
- App opens
- «Митинги» button visible
- Settings → «Митинги» section visible
- Folder picker opens native dialog

Check the sidecar log for boot-time errors:

```powershell
Get-Content (Join-Path $env:TEMP 'audio-transcriber-bootstrap.log') -Tail 5
```

Expected: only `=== bootstrap @ pid=N ===` markers.

- [ ] **Step 5: Push branch + open PR**

```bash
git push -u origin feat/v0.1-meetings-folder-picker
gh pr create --title "feat(meetings): user-configurable folder + migration + rename" --body "$(cat <<'EOF'
## Summary
- Replaces hardcoded \`<bundle>/_internal/history/\` with a user-configurable folder
- Default: \`%USERPROFILE%\\Documents\\AudioTranscriber\\meetings\\\`
- First-launch detection + migration prompt for legacy entries
- Settings dialog gains a «Митинги» section (folder picker + stats)
- UI rename «История» → «Митинги» across button, dialog, mixin

## Test plan
- [x] \`pytest\` — 415+ passed (9 unit + 11 source-text new)
- [x] \`ruff check\` — clean
- [x] Dev smoke — all checklist items in Task 9 of the plan
- [x] PyInstaller bundle smoke — opens, sidecar log clean

Spec: [docs/superpowers/specs/2026-05-28-meetings-folder-picker-design.md](docs/superpowers/specs/2026-05-28-meetings-folder-picker-design.md)
Plan: [docs/superpowers/plans/2026-05-28-meetings-folder-picker-plan.md](docs/superpowers/plans/2026-05-28-meetings-folder-picker-plan.md)
EOF
)"
```

(Branch name `feat/v0.1-meetings-folder-picker` — create at start of Task 1 via `git checkout -b feat/v0.1-meetings-folder-picker`.)

---

## Summary

9 tasks, each TDD-disciplined (test → fail → implement → pass → commit). Net deliverables:

- **`meetings_migration.py`** (new, ~140 LOC): pure logic, 9 unit tests
- **`utils.py`** (modified): `get_meetings_dir()` + 3-level fallback resolver
- **`ui/dialogs/meetings.py`** (renamed from history.py): class + string renames
- **`ui/dialogs/migration.py`** (new, ~200 LOC): two modal dialogs
- **`ui/dialogs/settings.py`** (modified): new «Митинги» section
- **`ui/app/__init__.py`** (modified): first-launch migration check
- **`ui/app/dialogs_mixin.py` + `ui/app/builder.py`** (modified): button + method rename
- **`gdrive/backup.py`** (modified): callers use resolver
- **`config.example.json`** (modified): new `meetings_dir` key
- **4 new test files**: ~25 tests total covering migration logic, resolver, dialog rename, settings section
- **PyInstaller bundle**: rebuilt, same ~351 MB size

`config.json` schema gains one optional string field — no migration tooling needed; absent or `""` key falls through to default. Existing installations get a one-time migration prompt on first launch with the new bundle.
