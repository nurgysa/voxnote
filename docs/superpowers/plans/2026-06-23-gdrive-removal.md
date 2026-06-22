# gdrive/ API Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove VoxNote's API-based Google Drive backup subsystem (`gdrive/` package + its Google deps, UI, config keys, packaging hooks, docs, tests), keeping the transcription queue's `sources/`/`inbox/` Drive-Desktop filesystem convention and the `redact_config` helper.

**Architecture:** A four-task rip-out, ordered so nothing breaks mid-removal: (1) relocate `redact_config` into its sole surviving consumer `support_bundle.py`; (2) remove the Drive UI across the four UI files; (3) delete the `gdrive/` package, its tests, the 3 Google deps, and the PyInstaller hooks; (4) scrub remaining config/comment/doc references. Backup/restore now lives in Hermes Desktop.

**Tech Stack:** Python stdlib, `pytest`, `ruff`, PyInstaller (`voxnote.spec`). No new code beyond moving `redact_config`; the rest is deletion.

**Source of truth:** `docs/superpowers/specs/2026-06-23-gdrive-removal-design.md`.

## Global Constraints

- Cloud-only: no local CUDA / pyannote / torch (invariant #2) — only removes code.
- `encoding="utf-8"` on every text read/write (`support_bundle.py` + tests already comply).
- Narrow `except` only; the broad-except **count drops** — update the ratchet BASELINE in the SAME task that removes handlers (a drop fails the test otherwise).
- Russian user-facing strings; English code/comments/commits.
- `requirements.txt`: this **removes** three now-unused pins (`google-auth`, `google-auth-oauthlib`, `google-api-python-client`) — de-bloat, not a pin bump.
- Run tests/lint with `py -3` (Python 3.12; bare `python` is 3.11). Tests: `py -3 -m pytest -q`. Lint: `py -3 -m ruff check .`.
- Commits via the Bash tool (Git Bash), NOT PowerShell. Message ends with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Branch `chore/remove-gdrive` (spec already committed at `c1fe3cd`); the user merges.
- Do NOT touch `processing/sources.py`, `processing/inbox_watcher.py`, the `sources_dir`/`inbox_dir` config keys, or `build_sources_section`/`build_inbox_section` in `settings_builder.py` — those are the kept queue plumbing. Their picker labels mention "Google Drive → sources/inbox" and are **correct as-is** (they describe the Drive-Desktop folder to point at).

## File Structure

| File | Change |
|---|---|
| `support_bundle.py` | + own `redact_config` (+ constants/helper); drop the `from gdrive.backup import redact_config` |
| `tests/test_support_bundle.py` | + the 4 re-homed `redact_config` unit tests; update docstring |
| `ui/app/builder.py` | − the "Google Drive (Phase 7.0)" Vars block (lines 249-269) |
| `ui/app/settings_mixin.py` | − the "Google Drive (Phase 7.0)" methods (lines 85-149); update class docstring |
| `ui/dialogs/settings.py` | − the Google Drive section (lines 470-616) + the `build_gdrive_section` call (169); rename the «Резервная копия» tab; update docstring/comment |
| `ui/dialogs/settings_builder.py` | − `build_gdrive_section` (lines 555-618) |
| `tests/test_settings_gdrive.py` | DELETE |
| `tests/test_settings_worker_ui_guards.py` | lower the `_post_to_ui` threshold; update comment |
| `tests/test_broad_except_ratchet.py` | `ui/dialogs/settings.py` 3→1 (Task 2); remove `gdrive/backup.py` (Task 3) |
| `gdrive/` (4 files) | DELETE |
| `tests/test_gdrive_{auth,client,backup,backup_cleanup}.py` | DELETE |
| `requirements.txt` | − 3 Google deps |
| `voxnote.spec` | − Google hiddenimports + discovery-cache trim; update manifest comment |
| `config.example.json` | − 5 `gdrive_*` keys |
| `cli/_paths.py`, `utils.py`, `audio_io.py`, `tests/test_{secret_dir_acl,cli_paths}.py` | comment/sample cleanup |
| `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/CLIENT_SETUP.md`, `.github/SECURITY.md` | remove Drive sections |

Build order: Task 1 (relocate) → Task 2 (UI) → Task 3 (delete package/deps/packaging) → Task 4 (scrub config/comments/docs). Each ends with `py -3 -m pytest -q` green + `py -3 -m ruff check .` clean.

---

### Task 1: Relocate `redact_config` into `support_bundle.py`

**Files:**
- Modify: `support_bundle.py`
- Modify: `tests/test_support_bundle.py`

**Interfaces:**
- Produces: `support_bundle.redact_config(config: dict) -> dict`, plus module constants `REDACTION_PLACEHOLDER: str` and `REDACTED_KEYS: tuple[str, ...]`. Behavior identical to the old `gdrive.backup.redact_config` (deny-by-default secret-name redaction + nested `cloud_api_keys`).

- [ ] **Step 1: Re-home the redact_config tests (failing first)**

In `tests/test_support_bundle.py`, change the import line `from support_bundle import build_log_bundle` to also import the redactor, and append the four tests below (copied verbatim from `tests/test_gdrive_backup.py`, repointed to `support_bundle`):

```python
from support_bundle import (
    REDACTED_KEYS,
    REDACTION_PLACEHOLDER,
    build_log_bundle,
    redact_config,
)
```

```python
def test_redact_config_replaces_listed_keys_with_placeholder():
    config = {
        "language": "Авто-определение",
        "openrouter_api_key": "sk-or-real-key-12345",
        "linear_api_key": "lin_api_real",
        "glide_api_key": "real-glide-key",
        "assemblyai_api_key": "asm-real",
        "hf_token": "hf_real_token",
        "cloud_api_keys": {"AssemblyAI": "real", "Deepgram": "real2"},
        "gdrive_account_email": "user@example.com",
    }
    redacted = redact_config(config)
    assert redacted["openrouter_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["linear_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["glide_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["assemblyai_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["hf_token"] == REDACTION_PLACEHOLDER
    assert redacted["cloud_api_keys"] == {
        "AssemblyAI": REDACTION_PLACEHOLDER,
        "Deepgram": REDACTION_PLACEHOLDER,
    }
    assert redacted["language"] == "Авто-определение"
    assert redacted["gdrive_account_email"] == "user@example.com"
    assert config["openrouter_api_key"] == "sk-or-real-key-12345"


def test_redact_config_handles_missing_keys_silently():
    config = {"language": "Русский", "model": "large-v3"}
    redacted = redact_config(config)
    assert redacted == config
    assert redacted is not config


def test_redact_config_redacts_trello_credentials():
    config = {
        "trello_api_key": "trello-real-key",
        "trello_token": "trello-real-token",
        "trello_enabled": True,
    }
    redacted = redact_config(config)
    assert redacted["trello_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["trello_token"] == REDACTION_PLACEHOLDER
    assert redacted["trello_enabled"] is True


def test_redact_config_redacts_unknown_secret_named_keys():
    config = {
        "some_new_api_token": "future-secret",
        "WEBHOOK_SECRET": "another-secret",
        "user_password": "hunter2",
        "gdrive_account_email": "user@example.com",
        "meetings_dir": "C:/vault",
        "speaker_count": "Авто",
    }
    redacted = redact_config(config)
    assert redacted["some_new_api_token"] == REDACTION_PLACEHOLDER
    assert redacted["WEBHOOK_SECRET"] == REDACTION_PLACEHOLDER
    assert redacted["user_password"] == REDACTION_PLACEHOLDER
    assert redacted["gdrive_account_email"] == "user@example.com"
    assert redacted["meetings_dir"] == "C:/vault"
    assert redacted["speaker_count"] == "Авто"
```

Also update the test module docstring line `Pure (stdlib + gdrive.redact_config), so it tests on Linux CI.` → `Pure (stdlib + support_bundle.redact_config), so it tests on Linux CI.`

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `py -3 -m pytest tests/test_support_bundle.py -q`
Expected: FAIL — `ImportError: cannot import name 'redact_config' from 'support_bundle'`.

- [ ] **Step 3: Move `redact_config` into `support_bundle.py`**

In `support_bundle.py`: add `import copy` and `from typing import Any` to the imports, **remove** the line `from gdrive.backup import redact_config`, and add the redactor (verbatim from the old `gdrive/backup.py`) after the imports:

```python
REDACTION_PLACEHOLDER = "<REDACTED>"

REDACTED_KEYS = (
    "openrouter_api_key",
    "linear_api_key",
    "glide_api_key",
    "assemblyai_api_key",
    "trello_api_key",
    "trello_token",
    "hf_token",
)

_SECRET_NAME_HINTS = ("key", "token", "secret", "password")


def _looks_like_secret(key_name: str) -> bool:
    """True if ``key_name`` contains any _SECRET_NAME_HINTS substring."""
    lowered = key_name.lower()
    return any(hint in lowered for hint in _SECRET_NAME_HINTS)


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``config`` with all secret values replaced by
    REDACTION_PLACEHOLDER. Input is never mutated. Deny-by-default: any top-level
    string whose key is in REDACTED_KEYS or looks like a secret is replaced;
    cloud_api_keys values are replaced (provider names kept)."""
    out = copy.deepcopy(config)
    for key, value in out.items():
        if isinstance(value, str) and (key in REDACTED_KEYS or _looks_like_secret(key)):
            out[key] = REDACTION_PLACEHOLDER
    cloud_keys = out.get("cloud_api_keys")
    if isinstance(cloud_keys, dict):
        out["cloud_api_keys"] = {k: REDACTION_PLACEHOLDER for k in cloud_keys}
    return out
```

Update the module docstring's `Reuses ``gdrive.backup.redact_config`` …` paragraph to say the module now owns `redact_config` (no gdrive reference). `build_log_bundle` already calls `redact_config(config)` — now resolves locally.

- [ ] **Step 4: Run the suite to verify green**

Run: `py -3 -m pytest tests/test_support_bundle.py tests/test_gdrive_backup.py -q`
Expected: PASS — the 4 new tests pass against `support_bundle.redact_config`; `test_gdrive_backup.py` still passes (its copy is untouched, deleted in Task 3).

- [ ] **Step 5: Lint + commit**

```bash
py -3 -m ruff check support_bundle.py tests/test_support_bundle.py
git add support_bundle.py tests/test_support_bundle.py
git commit -m "refactor(support-bundle): own redact_config (drop gdrive dependency)

Move redact_config + its constants from gdrive.backup into support_bundle.py
(its sole surviving consumer) ahead of the gdrive rip-out, and re-home the
direct redaction tests. Behavior unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Remove the Drive UI

**Files:**
- Modify: `ui/app/builder.py`, `ui/app/settings_mixin.py`, `ui/dialogs/settings.py`, `ui/dialogs/settings_builder.py`
- Delete: `tests/test_settings_gdrive.py`
- Modify: `tests/test_settings_worker_ui_guards.py`, `tests/test_broad_except_ratchet.py`

**Interfaces:**
- Consumes: nothing from Task 1 (independent).
- Produces: no `App._gdrive_*` attributes or methods remain; the Settings dialog has no Google Drive section.

- [ ] **Step 1: Remove the builder Vars block**

In `ui/app/builder.py`, delete the entire "Google Drive (Phase 7.0)" block — the comment and the six statements at lines 249-269 (`from gdrive.auth import GDriveAuth` through `app._gdrive_status_var = ctk.StringVar(value=app._compute_gdrive_status_text())`). Leave the `trello` Vars above (244-247) and the "Appearance mode" block below (271+) intact.

- [ ] **Step 2: Remove the settings_mixin Drive methods**

In `ui/app/settings_mixin.py`, delete the "── Google Drive (Phase 7.0) ──" section, lines 85-149: `_compute_gdrive_status_text`, `_on_gdrive_signed_in`, `_on_gdrive_signed_out`, `_on_gdrive_backup_succeeded`. In the class docstring (line 19), remove `/ ``self._gdrive_*``` from the Vars-families list.

- [ ] **Step 3: Remove the settings.py Drive section + the builder call + rename the tab**

In `ui/dialogs/settings.py`:
- Delete the Google Drive section lines 470-616 (`_refresh_gdrive_button_state` through the end of `_on_gdrive_backup_failure`, i.e. up to but not including the `# ── Diagnostics:` comment at line 618).
- Delete line 169 `settings_builder.build_gdrive_section(self, scroll_backup)`. Keep line 170 (`build_diagnostics_section`).
- Line 119: rename the tab — `self._tabview.add("Резервная копия")` → `self._tabview.add("Диагностика")`. Update the comment at line 168 (`# Tab 3 «Резервная копия» — independent housekeeping`) → `# Tab 3 «Диагностика» — log bundle for support`. (Internal var names `tab_backup`/`scroll_backup` may stay.)
- Line 6 docstring: drop `/ Google Drive` from `OpenRouter / Linear / Glide / Google Drive integrations`.

- [ ] **Step 4: Remove `build_gdrive_section`**

In `ui/dialogs/settings_builder.py`, delete `def build_gdrive_section(dialog, parent) -> None:` and its body, lines 555-618 (up to but not including `def build_diagnostics_section` at 619). Do NOT touch `build_sources_section` (173) or `build_inbox_section` (217).

- [ ] **Step 5: Delete the gdrive Settings test + fix the two guard tests**

Delete `tests/test_settings_gdrive.py`:
```bash
git rm tests/test_settings_gdrive.py
```

In `tests/test_settings_worker_ui_guards.py`, the `_post_to_ui` count drops (the OAuth sign-in + backup workers are gone; only stats + log-bundle remain). Recount the surviving calls and update `test_workers_use_the_helper`:
- Run `py -3 -c "import pathlib,re; t=pathlib.Path('ui/dialogs/settings.py').read_text(encoding='utf-8'); print(t.count('self._post_to_ui('))"` to get the new exact count N (expected 3: stats 1 + log-bundle 2).
- Change `assert SETTINGS.count("self._post_to_ui(") >= 8` to `>= N`, and update the comment `# stats(1) + gdrive sign-in(2) + backup status/success/failure(3) + log bundle(2) = 8` → `# stats(1) + log bundle(2) = 3`.

In `tests/test_broad_except_ratchet.py`, change `"ui/dialogs/settings.py": 3,` to `"ui/dialogs/settings.py": 1,` (the OAuth + backup worker `except Exception` handlers are removed; only the log-bundle worker's remains). Leave `"gdrive/backup.py": 1` for now (removed in Task 3).

- [ ] **Step 6: Run the full suite + lint**

Run: `py -3 -m pytest -q` and `py -3 -m ruff check .`
Expected: PASS / clean. Watch for now-unused imports in the edited UI files (e.g. `threading`, `tempfile`, `GREEN`/`RED`/`TEXT_SECONDARY`, `get_meetings_dir` in `settings.py` if only the Drive code used them) — remove any ruff flags as `F401`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(ui): remove the Google Drive backup UI

Drop the GDriveAuth Vars (builder), the Drive sign-in/backup methods
(settings_mixin + settings.py), and build_gdrive_section (settings_builder).
The «Резервная копия» tab is now «Диагностика» (log bundle only). Delete the
gdrive Settings test; ratchet settings.py broad-except 3->1; recount the
_post_to_ui guard threshold.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Delete the `gdrive/` package, its tests, deps, and packaging hooks

**Files:**
- Delete: `gdrive/__init__.py`, `gdrive/auth.py`, `gdrive/client.py`, `gdrive/backup.py`
- Delete: `tests/test_gdrive_auth.py`, `tests/test_gdrive_client.py`, `tests/test_gdrive_backup.py`, `tests/test_gdrive_backup_cleanup.py`
- Modify: `requirements.txt`, `voxnote.spec`, `tests/test_broad_except_ratchet.py`

**Interfaces:**
- Consumes: Task 1 (`support_bundle` owns `redact_config`) + Task 2 (no UI imports `gdrive`). After this task nothing imports `gdrive` or the Google libraries.

- [ ] **Step 1: Delete the package + its tests**

```bash
git rm gdrive/__init__.py gdrive/auth.py gdrive/client.py gdrive/backup.py
git rm tests/test_gdrive_auth.py tests/test_gdrive_client.py tests/test_gdrive_backup.py tests/test_gdrive_backup_cleanup.py
```

- [ ] **Step 2: Remove the Google dependencies**

In `requirements.txt`, delete the three lines:
```
google-auth==2.46.0
google-auth-oauthlib==1.3.0
google-api-python-client==2.196.0
```

- [ ] **Step 3: Remove the PyInstaller Google hooks**

In `voxnote.spec`:
- Delete the hiddenimports block lines 107-113 (the `# Google Drive backup (Phase 7.0/7.1) …` comment plus `googleapiclient.discovery`, `googleapiclient.discovery_cache`, `googleapiclient.discovery_cache.file_cache`, `google_auth_oauthlib.flow`).
- Delete the discovery-cache trim block lines 149-163 (the `# Trim googleapiclient's bundled API-discovery cache …` comment, `def _keep_datum(entry):` and its body, and the `a.datas = [e for e in a.datas if _keep_datum(e)]` line).
- Line 13: update the bundle-manifest comment `app.py + … + tasks/* + gdrive/* + ui/* + …` → drop ` + gdrive/*`.

- [ ] **Step 4: Remove the ratchet entry**

In `tests/test_broad_except_ratchet.py`, delete the line `"gdrive/backup.py": 1,                         # UI callback isolation`.

- [ ] **Step 5: Verify the app imports + suite green + lint**

Run:
```bash
py -3 -c "import app"
py -3 -m pytest -q
py -3 -m ruff check .
```
Expected: `import app` succeeds with no `ModuleNotFoundError`; suite green (the deleted gdrive tests are gone, `redact_config` coverage lives in `test_support_bundle.py`); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(gdrive): delete the Google Drive API package + deps

Remove gdrive/ (auth+client+backup) and its tests; drop google-auth,
google-auth-oauthlib, google-api-python-client from requirements; strip the
googleapiclient hiddenimports + discovery-cache trim from voxnote.spec; remove
the gdrive/backup.py broad-except ratchet entry. Backup/restore now lives in
Hermes Desktop.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Scrub remaining config, comment, and doc references

**Files:**
- Modify: `config.example.json`, `cli/_paths.py`, `utils.py`, `audio_io.py`, `tests/test_secret_dir_acl.py`, `tests/test_cli_paths.py`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/CLIENT_SETUP.md`, `.github/SECURITY.md`

**Interfaces:** none — textual cleanup. `scripts/package_release.py` keeps `gdrive-token.json` in `FORBIDDEN_NAMES` (defense-in-depth) and is intentionally NOT changed.

- [ ] **Step 1: Remove the config keys**

In `config.example.json`, delete the five lines: `"gdrive_enabled": false,`, `"gdrive_account_email": "",`, `"gdrive_last_backup": "",`, `"gdrive_backup_frequency": "off",`, `"gdrive_root_folder_id": "",`. Ensure the resulting JSON is still valid (no trailing-comma error on the key now preceding `appearance_mode`).

- [ ] **Step 2: Comment + sample cleanup**

- `cli/_paths.py:6` — drop `gdrive-token.json,` from the secret-store example list `~/.voxnote/{config.json,gdrive-token.json,directory.json,queue.json}` → `~/.voxnote/{config.json,directory.json,queue.json}`.
- `utils.py` lines 20, 41, 112 — remove the `gdrive-token.json` mentions from the three `~/.voxnote` secret-store comments (e.g. `config.json (API keys) + gdrive-token.json` → `config.json (API keys)`); the ACL/migration logic covers the whole dir and is unchanged.
- `audio_io.py:104` — the comment cites `gdrive/auth.py` as the `~/.voxnote/<subsystem>/` cache-convention example; change the example to `directory.json` / the `~/.voxnote/` convention generally (the deleted `gdrive/auth.py` must not be referenced).
- `tests/test_secret_dir_acl.py:3` docstring — drop the `+ gdrive-token.json` from `config.json (API keys) + gdrive-token.json`.
- `tests/test_cli_paths.py:48` — change the sample path `home / ".voxnote" / ".." / ".voxnote" / "gdrive-token.json"` to `... / "config.json"` (any secret-store file works; `config.json` still exists). The assertion (raises `ValueError`) is unchanged.

- [ ] **Step 3: Docs**

- `CLAUDE.md` — delete the two "Google Drive" rows in the "Where things live" table (`gdrive/auth.py`, `gdrive/client.py`, `gdrive/backup.py`); in "Current status & queued work" replace the Google Drive bullet with a one-line note that the `gdrive/` API was removed (2026-06-23) — backup/restore now lives in Hermes Desktop; and in invariant #3 remove the `google-auth` mention (it is no longer a dependency).
- `docs/ARCHITECTURE.md` — remove the Google Drive backup section.
- `docs/CLIENT_SETUP.md` — remove the Russian Google Drive setup section.
- `.github/SECURITY.md` — remove the Drive-token / OAuth-scope mention.

- [ ] **Step 4: Verify no stray imports/deps remain + suite green + lint**

Run:
```bash
py -3 -m pytest -q
py -3 -m ruff check .
```
And confirm zero surviving code/dep references (filename strings in `package_release.py`/`test_cli_paths.py` are intentional and excluded):
```bash
grep -rEn "from gdrive|import gdrive|googleapiclient|google_auth|google-(auth|api-python-client)" --include=*.py --include=*.spec --include=requirements.txt . | grep -v "tool-results"
```
Expected: suite green; ruff clean; the grep prints nothing.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs(gdrive): scrub config keys, comments, and docs after rip-out

Drop the 5 gdrive_* keys from config.example.json; clean the gdrive-token.json
mentions from secret-store comments/samples; remove the Google Drive sections
from CLAUDE.md, ARCHITECTURE.md, CLIENT_SETUP.md, SECURITY.md.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Plan Self-Review

**1. Spec coverage:**
- §3.1 delete package/tests/deps → Task 3. ✓
- §3.2 relocate `redact_config` + re-home tests → Task 1. ✓
- §3.3 remove Drive UI → Task 2 (builder, settings_mixin, settings, settings_builder). ✓
- §3.4 packaging (voxnote.spec hooks; keep `gdrive-token.json` guard) → Task 3 (spec hooks); guard intentionally untouched (Task 4 note). ✓
- §3.5 config keys + leave-stale → Task 4 Step 1 (no migration code anywhere). ✓
- §3.6 comment cleanup + 2 test fixes → ratchet (Task 2 settings.py 3→1, Task 3 gdrive/backup.py removal), `_post_to_ui` recount (Task 2), comments (Task 4). ✓
- §3.7 docs → Task 4 Step 3. ✓

**2. Placeholder scan:** No TBD/TODO. The one computed value — the `_post_to_ui` threshold N — is given with the exact recount command and the expected value (3). Deletion ranges cite exact line numbers + the bounding symbol so they survive minor drift.

**3. Type consistency:** `redact_config(config: dict) -> dict` and the `REDACTION_PLACEHOLDER` / `REDACTED_KEYS` names are identical between Task 1's `support_bundle.py` definition and the re-homed tests' import. The ratchet edits are split correctly (settings.py in Task 2 when its handlers go; gdrive/backup.py in Task 3 when the file goes) so the test never sees a stale-vs-actual mismatch between tasks.
