# VoxNote Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project from audio-transcriber / Audio Transcriber / AudioTranscriber to **VoxNote** across every live surface (code, the on-disk secret store, build artifacts, live docs, tests), without orphaning deployed clients' keys/tokens.

**Architecture:** One casing-aware mechanical pass over the live tree (5 distinct casings), plus one piece of new logic — a `move`-based `~/.audio-transcriber` → `~/.voxnote` migration shim that runs before any config/token read. The dated `docs/superpowers/` archive and third-party `vendor/` text are frozen. A durable guard test prevents the old name from creeping back.

**Tech Stack:** Python 3.10+, pytest, ruff, PyInstaller, git. Run Python as `py -3` (the bare `python` is shadowed by a Hermes venv without pytest/ruff).

**Casing map (the contract):**

| From | To |
|---|---|
| `AUDIO_TRANSCRIBER` (env prefix) | `VOXNOTE` |
| `AudioTranscriber` | `VoxNote` |
| `Audio Transcriber` | `VoxNote` |
| `audio_transcriber` | `voxnote` |
| `audio-transcriber` | `voxnote` |

**Do NOT touch (look-alikes that are a different contract):** the Hermes event
names `audio.transcribed`, `audio_transcribed`, and the webhook path
`webhooks/audio-transcribed` — all end in `-ed`/`_ed`, not `-er`/`_er`, so a
literal rename leaves them alone. They are the event API, not the brand.

**Out of scope (handled in Task 4 / follow-ups):** `gh repo rename`, renaming
the local checkout dir, rebuilding the `.exe`, and updating the user's external
`~/.hermes` env vars (the env-prefix rename below is a behavior change for any
agent that sets `AUDIO_TRANSCRIBER_*` in its environment — `config.json` users
are unaffected because the dir migrates).

---

### Task 1: Mechanical casing-aware rename

**Files:**
- Create (temporary, deleted at end of task): `scripts/_rebrand_voxnote.py`
- Rename via git: `audio_transcriber.spec` → `voxnote.spec`;
  `integrations/hermes/skills/audio-transcriber/` → `.../voxnote/`;
  `vendor/icons/audio_transcriber.ico` → `vendor/icons/voxnote.ico`
- Modify (by the script): all live `.py/.md/.ps1/.json/.txt/.spec/.toml/.cfg/.ini/.yml/.yaml/.bat`
  files outside `docs/superpowers/`, `vendor/`, and the build/cache dirs
- Modify (manual, special case): `tasks/openrouter_client.py:49`

- [ ] **Step 1: Do the three git renames first (so content rewrite lands on new paths)**

```bash
cd "C:/Users/nurgisa/Documents/audio-transcriber"
git mv audio_transcriber.spec voxnote.spec
git mv integrations/hermes/skills/audio-transcriber integrations/hermes/skills/voxnote
git mv vendor/icons/audio_transcriber.ico vendor/icons/voxnote.ico
```

Expected: three renames staged; `git status --short` shows `R  ` lines, no errors.

- [ ] **Step 2: Write the one-shot rename script**

Create `scripts/_rebrand_voxnote.py` with EXACTLY this content. It reads/writes
bytes (so CRLF/LF and BOM are preserved — only the matched spans change), skips
the historical archive, third-party vendor text, caches, and itself.

```python
"""One-shot, casing-aware rebrand pass: audio-transcriber -> VoxNote.

Throwaway. Run once via `py -3 scripts/_rebrand_voxnote.py`, verify, then delete.
Reads/writes bytes to preserve newlines + BOM; only matched spans change.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Order is not significant — the five forms are mutually non-overlapping
# (distinct separators/cases). Listed longest-context first for readability.
REPLACEMENTS = [
    ("AUDIO_TRANSCRIBER", "VOXNOTE"),   # SCREAMING_SNAKE env-var prefix
    ("AudioTranscriber", "VoxNote"),    # PascalCase: exe / dist / zip
    ("Audio Transcriber", "VoxNote"),   # Title Case: UI title, X-Title, docs
    ("audio_transcriber", "voxnote"),   # snake_case: .spec filename, icon
    ("audio-transcriber", "voxnote"),   # kebab: paths, repo refs, ~/.dir
]

SKIP_DIRS = {
    ".git", "vendor", "dist", "build", ".cache", "logs",
    "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
}
# docs/superpowers/* is a dated, frozen historical chronicle.
SKIP_REL_PREFIXES = (str(Path("docs") / "superpowers"),)
TEXT_EXT = {
    ".py", ".md", ".ps1", ".json", ".txt", ".toml",
    ".cfg", ".ini", ".yml", ".yaml", ".spec", ".bat",
}


def _skip(rel: Path) -> bool:
    s = str(rel)
    if s.startswith(SKIP_REL_PREFIXES):
        return True
    if rel.name.startswith("_rebrand_voxnote"):  # never rewrite this script
        return True
    return False


def main() -> None:
    changed = 0
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
            continue
        rel = path.relative_to(REPO)
        if any(part in SKIP_DIRS for part in rel.parts) or _skip(rel):
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new = text
        for old, repl in REPLACEMENTS:
            new = new.replace(old, repl)
        if new != text:
            path.write_bytes(new.encode("utf-8"))
            changed += 1
            print(f"rewrote {rel}")
    print(f"done: {changed} files changed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the script**

```bash
py -3 scripts/_rebrand_voxnote.py
```

Expected: a list of `rewrote <path>` lines (≈40 files) ending in `done: N files changed`. Sanity-check the list contains NO path under `docs/superpowers/` or `vendor/`.

- [ ] **Step 4: Fix the one special-case URL the blanket pass can't get right**

The blanket pass turned the malformed `https://github.com/audio-transcriber`
(no owner) into `https://github.com/voxnote` (still no owner). Set the real repo:

Edit `tasks/openrouter_client.py` — change `"https://github.com/voxnote"` to `"https://github.com/nurgysa/voxnote"`.

```python
            "HTTP-Referer": "https://github.com/nurgysa/voxnote",
            "X-Title": "VoxNote",
```

- [ ] **Step 5: Delete the throwaway script**

```bash
git rm -f --quiet scripts/_rebrand_voxnote.py 2>/dev/null || rm -f scripts/_rebrand_voxnote.py
```

Expected: the temp script is gone and not staged.

- [ ] **Step 6: Run the full suite — it is the regression test for the rename**

Tests reference the old paths/names and were rewritten in lockstep, so they must stay green.

```bash
py -3 -m pytest -q
```

Expected: all green (baseline ≈ 939; no new tests yet). If a test fails on a string mismatch, reconcile it against the casing map — do not weaken the assertion.

- [ ] **Step 7: Lint**

```bash
py -3 -m ruff check .
```

Expected: clean. (`voxnote.spec` and `gen_icon.py` now reference `vendor/icons/voxnote.ico`, which matches the renamed binary.)

- [ ] **Step 8: Stage only rebrand changes, then commit**

```bash
git status --short
```

Verify every line is a rebrand file (renames + content). If you see an unrelated
file the user edited in parallel, do NOT stage it.

```bash
git add -u
git add voxnote.spec integrations/hermes/skills/voxnote vendor/icons/voxnote.ico
git commit -F - <<'EOF'
refactor(rebrand): mechanical rename audio-transcriber -> VoxNote

Casing-aware pass over live code, build scripts, the Hermes skill, live
docs, and tests (5 forms incl. the AUDIO_TRANSCRIBER_ env prefix). Renames
the PyInstaller spec, Hermes skill dir, and app icon. Fixes the malformed
OpenRouter HTTP-Referer to https://github.com/nurgysa/voxnote. The dated
docs/superpowers archive and third-party vendor text are intentionally left.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: Secret-store migration shim (`~/.audio-transcriber` → `~/.voxnote`)

**Files:**
- Test: `tests/test_secret_dir_migration.py` (create)
- Modify: `utils.py` (add the shim — re-introduces the ONLY surviving legacy literal)
- Modify: `app.py`, `cli/app.py`, `cli/mcp_server.py` (call the shim before any config/token read)

- [ ] **Step 1: Write the failing test (four cases)**

Create `tests/test_secret_dir_migration.py`:

```python
"""migrate_legacy_secret_dir(): one-time move of the pre-VoxNote secret store.

Redirect HOME/USERPROFILE to a tmp dir so os.path.expanduser("~") resolves
there on both POSIX and Windows.
"""
import os

import utils


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_migrates_when_old_exists_and_new_absent(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    old = home / ".audio-transcriber"
    old.mkdir()
    (old / "config.json").write_text("{\"k\": 1}", encoding="utf-8")

    utils.migrate_legacy_secret_dir()

    new = home / ".voxnote"
    assert new.is_dir()
    assert (new / "config.json").read_text(encoding="utf-8") == "{\"k\": 1}"
    assert not old.exists()


def test_noop_when_new_already_exists(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    old = home / ".audio-transcriber"
    old.mkdir()
    (old / "x").write_text("old", encoding="utf-8")
    new = home / ".voxnote"
    new.mkdir()
    (new / "x").write_text("new", encoding="utf-8")

    utils.migrate_legacy_secret_dir()

    assert (new / "x").read_text(encoding="utf-8") == "new"  # untouched
    assert old.exists()  # left alone


def test_noop_when_neither_exists(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    utils.migrate_legacy_secret_dir()
    assert not (home / ".voxnote").exists()


def test_move_failure_is_swallowed(tmp_path, monkeypatch):
    home = _home(monkeypatch, tmp_path)
    (home / ".audio-transcriber").mkdir()

    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(utils.shutil, "move", boom)
    utils.migrate_legacy_secret_dir()  # must NOT raise
    assert not (home / ".voxnote").exists()
```

- [ ] **Step 2: Run it — verify it fails for the right reason**

```bash
py -3 -m pytest tests/test_secret_dir_migration.py -q
```

Expected: FAIL — `AttributeError: module 'utils' has no attribute 'migrate_legacy_secret_dir'`.

- [ ] **Step 3: Add the shim to `utils.py`**

Insert immediately after the `_CONFIG_PATH = _default_config_path()` line (≈ line 28). `os`, `shutil`, and `logger` are already imported at the top of the file.

```python
# Pre-VoxNote secret-store dir, kept ONLY as the one-time migration source.
# This is the single place the legacy brand literal may survive (guard-test
# allowlisted). See migrate_legacy_secret_dir().
_LEGACY_SECRET_DIR_NAME = ".audio-transcriber"
_SECRET_DIR_NAME = ".voxnote"


def migrate_legacy_secret_dir() -> None:
    """One-time move of ``~/.audio-transcriber`` → ``~/.voxnote``.

    Keeps existing installs' config.json (API keys), gdrive-token.json,
    directory.json, queue.json, and model cache after the rebrand. Idempotent
    (no-op once ``~/.voxnote`` exists). Best-effort: a failed move is logged
    and swallowed so it can never block startup.

    ORDERING INVARIANT: must run before any code reads config/tokens. Otherwise
    the renamed code reads an empty ``~/.voxnote`` and may overwrite it with
    defaults, orphaning live keys.
    """
    home = os.path.expanduser("~")
    new_dir = os.path.join(home, _SECRET_DIR_NAME)
    old_dir = os.path.join(home, _LEGACY_SECRET_DIR_NAME)
    if os.path.exists(new_dir) or not os.path.isdir(old_dir):
        return
    try:
        shutil.move(old_dir, new_dir)
        logger.info("migrated secret store %s -> %s", old_dir, new_dir)
    except OSError as exc:
        logger.warning("could not migrate %s -> %s: %s", old_dir, new_dir, exc)
```

- [ ] **Step 4: Run the migration test — verify it passes**

```bash
py -3 -m pytest tests/test_secret_dir_migration.py -q
```

Expected: 4 passed.

- [ ] **Step 5: Wire the call site in `app.py` (GUI)**

Between the faulthandler block and the `from ui.app import main` import (≈ line 34), insert:

```python
from utils import migrate_legacy_secret_dir  # noqa: E402  (after faulthandler)

migrate_legacy_secret_dir()

from ui.app import main  # noqa: E402  (must follow faulthandler setup)
```

(The existing `from ui.app import main` line is replaced by the three lines above.)

- [ ] **Step 6: Wire the call site in `cli/app.py` (CLI entry)**

At the very top of `def main(argv=...)` (≈ line 457), before `parser = build_parser()`. Keep the import local — `cli/app.py` documents a headless contract, and `utils` is otherwise only imported lazily for `--save`:

```python
def main(argv: list[str] | None = None) -> int:
    from utils import migrate_legacy_secret_dir

    migrate_legacy_secret_dir()
    parser = build_parser()
```

- [ ] **Step 7: Wire the call site in `cli/mcp_server.py` (MCP entry)**

In `def main()`, after the faulthandler setup and before `mcp.run()` (≈ line 197):

```python
    from utils import migrate_legacy_secret_dir

    migrate_legacy_secret_dir()
    mcp.run()
```

- [ ] **Step 8: Full suite + lint**

```bash
py -3 -m pytest -q
py -3 -m ruff check .
```

Expected: green (baseline + 4 new tests); ruff clean.

- [ ] **Step 9: Commit**

```bash
git add utils.py app.py cli/app.py cli/mcp_server.py tests/test_secret_dir_migration.py
git commit -F - <<'EOF'
feat(rebrand): migrate ~/.audio-transcriber secret store to ~/.voxnote

One-time, idempotent, best-effort move so existing installs keep their
config.json (keys), gdrive-token.json, directory.json, queue.json, and
model cache. Runs before any config/token read at all three entry points
(GUI app.py, CLI cli/app.py, MCP cli/mcp_server.py). 4-case unit test.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: Durable anti-regression guard + final sweep

**Files:**
- Test: `tests/test_no_legacy_brand_guard.py` (create)

- [ ] **Step 1: Write the guard test**

Mirrors the repo's existing ratchet-guard pattern (e.g. the provider-transport guard). Asserts no legacy brand token anywhere outside a tiny, explicit allowlist.

Create `tests/test_no_legacy_brand_guard.py`:

```python
"""Ratchet guard: the legacy 'audio-transcriber' brand must not reappear.

Allowed survivors: the one-time migration shim (utils.py), the migration
test, and this guard's own pattern list. The dated docs/superpowers archive
and third-party vendor/ text are out of scope by design.
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

LEGACY = (
    "audio-transcriber",
    "audio_transcriber",
    "AUDIO_TRANSCRIBER",
    "Audio Transcriber",
    "AudioTranscriber",
)
SKIP_DIRS = {
    ".git", "vendor", "dist", "build", ".cache", "logs",
    "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
}
SKIP_REL_PREFIXES = (str(Path("docs") / "superpowers"),)
ALLOWLIST = {
    "utils.py",
    str(Path("tests") / "test_secret_dir_migration.py"),
    str(Path("tests") / "test_no_legacy_brand_guard.py"),
}
TEXT_EXT = {
    ".py", ".md", ".ps1", ".json", ".txt", ".toml",
    ".cfg", ".ini", ".yml", ".yaml", ".spec", ".bat",
}


def test_no_legacy_brand_outside_allowlist():
    offenders = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            rel = path.relative_to(REPO)
            if str(rel).startswith(SKIP_REL_PREFIXES):
                continue
            if str(rel) in ALLOWLIST:
                continue
            if path.suffix.lower() not in TEXT_EXT:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for token in LEGACY:
                if token in text:
                    offenders.append(f"{rel}: {token}")
    assert not offenders, "legacy brand name found:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run the guard**

```bash
py -3 -m pytest tests/test_no_legacy_brand_guard.py -q
```

Expected: PASS. If it lists offenders, fix each (apply the casing map) until green — those are spots Task 1's pass missed.

- [ ] **Step 3: Eyeball the one allowed exception in a shipped artifact**

```bash
grep -niE 'audio.?transcriber' vendor/ffmpeg/LICENSE.txt
```

If matches are inside third-party license body text, leave them (out of scope).
If a match is a header WE added that names the app, fix that line by hand (vendor
text is otherwise frozen) and re-run the guard.

- [ ] **Step 4: Manual confirmation sweep (defense in depth)**

```bash
py -3 -m pytest -q && py -3 -m ruff check .
```

Expected: full suite green, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_no_legacy_brand_guard.py
git commit -F - <<'EOF'
test(rebrand): ratchet guard against the legacy brand name reappearing

Walks the live tree; allowlists only the migration shim + its test. Frozen
docs/superpowers archive and third-party vendor/ excluded by design.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 4: GitHub repo rename (separately confirmed — run last)

**Files:** none (remote + local git config only)

- [ ] **Step 1: Confirm with the user before any outward-facing action**

Renaming the repo is outward-facing. Confirm the user wants it now (vs. after the
branch merges). GitHub auto-redirects the old URL, so in-repo `nurgysa/voxnote`
links already resolve once the repo is renamed.

- [ ] **Step 2: Rename on GitHub**

```bash
gh repo rename voxnote --repo nurgysa/audio-transcriber --yes
```

Expected: confirmation that the repo is now `nurgysa/voxnote`.

- [ ] **Step 3: Point the local remote at the new name**

```bash
git remote set-url origin https://github.com/nurgysa/voxnote.git
git remote -v
```

Expected: `origin` shows `nurgysa/voxnote`.

- [ ] **Step 4: Note remaining manual follow-ups (do NOT do them here)**

- Update the user's external `~/.hermes` MCP env vars (`AUDIO_TRANSCRIBER_*` → `VOXNOTE_*`).
- Rebuild the `.exe` (`scripts/build_exe.ps1`) → `VoxNote.exe`, repackage the release zip, redeploy `C:\Apps`.
- Optionally rename the local checkout dir `Documents\audio-transcriber` (cosmetic).

---

## Self-review

- **Spec coverage:** casing map (Task 1, incl. the env prefix surfaced during
  grounding) ✓; secret-store migration with ordering invariant + 4 cases
  (Task 2) ✓; live-docs-only / archive frozen (Task 1 script skips) ✓; repo
  rename (Task 4) ✓; pytest+ruff+grep-sweep gate (Tasks 1/2/3) ✓; OpenRouter
  URL fix (Task 1 Step 4) ✓; icon `git mv` (Task 1 Step 1) ✓.
- **Placeholders:** none — every code/step shows full content and exact commands.
- **Naming consistency:** `migrate_legacy_secret_dir`, `_LEGACY_SECRET_DIR_NAME`,
  `_SECRET_DIR_NAME` used identically across utils.py, the test, and all three
  call sites; `tests/test_secret_dir_migration.py` and
  `tests/test_no_legacy_brand_guard.py` referenced consistently.
- **New finding vs. spec:** the `AUDIO_TRANSCRIBER_*` env prefix is a 5th casing
  not in the original spec map; folding it in is within "rename completely
  everywhere" but is a behavior change for env-var-based agent integrations —
  flagged in Task 4 Step 4 and in the handoff.
